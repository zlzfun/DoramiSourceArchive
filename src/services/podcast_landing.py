"""Podcast 全文处理自动入队(landing)的增量扫描、失败记忆与退避(issue #68)。

背景:v3.52.3 起 `execute_article_analysis_job` 每分钟把**全部**已成功的播客分析
交给自动入队,只靠 durable queue 的幂等键去重。幂等键在入队成功落行之后才生效,
入队失败什么都不落,下一分钟条件相同再来一遍——生产在「未配 ASR + 产物库到配额」
两个条件下变成每分钟 233 集全量重放,同步 DB 与下载校验跑在事件循环里,拖垮
日报/采集 cron。本模块只提供纯逻辑(KV 形状、扫描、分类、退避),调度接线在
`api/app.py::execute_podcast_landing_job`。

三层防线:
1. **游标**:只扫 `updated_at` 大于游标的 succeeded 播客分析行,不再全表重放。
2. **失败记忆**:按 episode 记录最近一次尝试(revision/次数/类别/下次可试时刻)。
   瞬时失败指数退避(5 min 起,×2,封顶 6 h,超过 8 次进入长停);确定性失败
   (供应商不可用/配额满/媒体不合规等)不进重试阶梯,只在 24 h 后重新核对一次,
   或该集的 revision 变化时重新放行。
3. **前置守门**(app 层):运行时未就绪或需要下载源音频而 ASR 未就绪 / 产物库
   无余量时整轮跳过,受影响的集记 `gated`,短间隔后再看。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlmodel import Session, select

from models.db import AppSettingRecord, ArticleAnalysisRecord
from services import podcast_artifacts, podcast_processing_admin, podcast_source_media

CURSOR_KEY = "podcast_landing:cursor"
STATE_KEY = "podcast_landing:attempts"

LANDING_BASES: tuple[str, ...] = (
    "podcast_show_notes",
    "publisher_transcript",
    "asr_transcript",
)

SCAN_LIMIT = 200
MAX_CONCURRENCY = 3

TRANSIENT_BASE_SECONDS = 5 * 60
TRANSIENT_MAX_SECONDS = 6 * 60 * 60
TRANSIENT_MAX_ATTEMPTS = 8
HALT_RECHECK_SECONDS = 24 * 60 * 60
GATE_RECHECK_SECONDS = 10 * 60
STATE_RETENTION_SECONDS = 30 * 24 * 60 * 60

KIND_TRANSIENT = "transient"
KIND_DETERMINISTIC = "deterministic"
KIND_EXHAUSTED = "exhausted"
KIND_GATED = "gated"

# 这些 admin 错误码在同一 revision 下重试不会改变结果。
DETERMINISTIC_ADMIN_CODES = frozenset(
    {
        "podcast_provider_unavailable",
        "podcast_stage_denied",
        "podcast_not_found",
        "podcast_source_media_too_long",
        "podcast_artifact_not_ready",
    }
)

DETERMINISTIC_EXCEPTIONS: tuple[type[BaseException], ...] = (
    podcast_artifacts.PodcastArtifactStorageFull,
    podcast_artifacts.PodcastArtifactTooLarge,
    podcast_artifacts.PodcastArtifactUnsupportedMedia,
    podcast_artifacts.PodcastArtifactProbeUnavailable,
    podcast_source_media.SourceMediaTooLarge,
    podcast_source_media.SourceMediaTooLong,
    podcast_source_media.SourceMediaNotFound,
)


@dataclass(frozen=True)
class FailureClass:
    kind: str
    code: str


def classify_failure(exc: BaseException) -> FailureClass:
    """Map an enqueue exception to a retry class plus a short diagnostic code."""

    if isinstance(exc, podcast_processing_admin.PodcastAdminError):
        code = str(exc.code or "podcast_admin_error")
        kind = KIND_DETERMINISTIC if code in DETERMINISTIC_ADMIN_CODES else KIND_TRANSIENT
        return FailureClass(kind, code)
    if isinstance(exc, DETERMINISTIC_EXCEPTIONS):
        return FailureClass(KIND_DETERMINISTIC, type(exc).__name__)
    return FailureClass(KIND_TRANSIENT, type(exc).__name__)


def _now_iso(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


# ── KV 存取 ──────────────────────────────────────────────────────────────────


def load_cursor(session: Session) -> str:
    record = session.get(AppSettingRecord, CURSOR_KEY)
    return (record.value if record else "").strip()


def save_cursor(session: Session, cursor: str) -> None:
    record = session.get(AppSettingRecord, CURSOR_KEY)
    if record is None:
        record = AppSettingRecord(key=CURSOR_KEY, value=cursor)
    else:
        record.value = cursor
    session.add(record)


def load_state(session: Session) -> dict[str, dict]:
    record = session.get(AppSettingRecord, STATE_KEY)
    if record is None or not record.value.strip():
        return {}
    try:
        payload = json.loads(record.value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def save_state(session: Session, state: dict[str, dict]) -> None:
    record = session.get(AppSettingRecord, STATE_KEY)
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if record is None:
        record = AppSettingRecord(key=STATE_KEY, value=encoded)
    else:
        record.value = encoded
    session.add(record)


# ── 增量扫描 ──────────────────────────────────────────────────────────────────


def scan_new_candidates(
    session: Session, cursor: str, *, limit: int = SCAN_LIMIT
) -> tuple[list[str], str]:
    """Return newly updated succeeded podcast analyses after ``cursor``.

    ``updated_at`` is stored as an ISO-8601 UTC string, so lexical comparison is
    chronological. The returned cursor is the last row's timestamp (or the input
    cursor when nothing was found); callers persist it only after the round ran.
    """

    statement = (
        select(ArticleAnalysisRecord.article_id, ArticleAnalysisRecord.updated_at)
        .where(
            ArticleAnalysisRecord.status == "succeeded",
            ArticleAnalysisRecord.analysis_basis.in_(LANDING_BASES),
        )
        .order_by(ArticleAnalysisRecord.updated_at.asc(), ArticleAnalysisRecord.article_id.asc())
        .limit(max(1, limit))
    )
    if cursor:
        statement = statement.where(ArticleAnalysisRecord.updated_at > cursor)
    rows = session.exec(statement).all()
    ids = [str(article_id) for article_id, _updated in rows]
    next_cursor = str(rows[-1][1]) if rows else cursor
    return ids, next_cursor


# ── 失败记忆与退避 ────────────────────────────────────────────────────────────


def due_retries(state: dict[str, dict], now: dt.datetime) -> list[str]:
    due: list[str] = []
    for episode_id, entry in state.items():
        retry_at = _parse_iso(entry.get("retry_at"))
        if retry_at is not None and retry_at <= now:
            due.append(episode_id)
    return due


def is_allowed(
    state: dict[str, dict], episode_id: str, revision: str, now: dt.datetime
) -> bool:
    """Whether an eligible episode may be attempted now.

    Unknown episodes and revision changes are always allowed; otherwise the
    recorded ``retry_at`` decides.
    """

    entry = state.get(episode_id)
    if entry is None:
        return True
    if str(entry.get("revision") or "") != revision:
        return True
    retry_at = _parse_iso(entry.get("retry_at"))
    return retry_at is None or retry_at <= now


def transient_backoff_seconds(attempts: int) -> int:
    exponent = max(0, attempts - 1)
    return min(TRANSIENT_BASE_SECONDS * (2**exponent), TRANSIENT_MAX_SECONDS)


def record_failure(
    state: dict[str, dict],
    episode_id: str,
    revision: str,
    failure: FailureClass,
    now: dt.datetime,
) -> dict:
    prior = state.get(episode_id) or {}
    same_revision = str(prior.get("revision") or "") == revision
    attempts = int(prior.get("attempts") or 0) + 1 if same_revision else 1
    if failure.kind == KIND_DETERMINISTIC:
        kind = KIND_DETERMINISTIC
        delay = HALT_RECHECK_SECONDS
    elif attempts >= TRANSIENT_MAX_ATTEMPTS:
        kind = KIND_EXHAUSTED
        delay = HALT_RECHECK_SECONDS
    else:
        kind = KIND_TRANSIENT
        delay = transient_backoff_seconds(attempts)
    entry = {
        "revision": revision,
        "attempts": attempts,
        "kind": kind,
        "code": failure.code,
        "last_at": _now_iso(now),
        "retry_at": _now_iso(now + dt.timedelta(seconds=delay)),
    }
    state[episode_id] = entry
    return entry


def record_gated(
    state: dict[str, dict], episode_id: str, revision: str, reason: str, now: dt.datetime
) -> dict:
    """Remember an episode skipped by the round-level gate so the cursor can move on."""

    prior = state.get(episode_id) or {}
    entry = {
        "revision": revision,
        "attempts": int(prior.get("attempts") or 0),
        "kind": KIND_GATED,
        "code": reason,
        "last_at": _now_iso(now),
        "retry_at": _now_iso(now + dt.timedelta(seconds=GATE_RECHECK_SECONDS)),
    }
    state[episode_id] = entry
    return entry


def record_success(state: dict[str, dict], episode_id: str) -> None:
    state.pop(episode_id, None)


def prune_state(state: dict[str, dict], now: dt.datetime) -> None:
    """Drop entries untouched for longer than the retention window."""

    stale = [
        episode_id
        for episode_id, entry in state.items()
        if (last := _parse_iso(entry.get("last_at"))) is None
        or (now - last).total_seconds() > STATE_RETENTION_SECONDS
    ]
    for episode_id in stale:
        state.pop(episode_id, None)


def dedupe_preserving_order(*groups: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
    return ordered


def summarize(state: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in state.values():
        kind = str(entry.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


__all__: Sequence[str] = (
    "CURSOR_KEY",
    "STATE_KEY",
    "FailureClass",
    "classify_failure",
    "load_cursor",
    "save_cursor",
    "load_state",
    "save_state",
    "scan_new_candidates",
    "due_retries",
    "is_allowed",
    "record_failure",
    "record_gated",
    "record_success",
    "prune_state",
    "dedupe_preserving_order",
    "summarize",
)
