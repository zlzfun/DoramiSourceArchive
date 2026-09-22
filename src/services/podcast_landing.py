"""Podcast 全文处理自动入队(landing)的增量扫描、失败记忆与退避(issue #68)。

背景:v3.52.3 起 `execute_article_analysis_job` 每分钟把**全部**已成功的播客分析
交给自动入队,只靠 durable queue 的幂等键去重。幂等键在入队成功落行之后才生效,
入队失败什么都不落,下一分钟条件相同再来一遍——生产在「未配 ASR + 产物库到配额」
两个条件下变成每分钟 233 集全量重放,同步 DB 与下载校验跑在事件循环里,拖垮
日报/采集 cron。本模块只提供纯逻辑(KV 形状、扫描、分类、退避),调度接线在
`api/app.py::execute_podcast_landing_job`。

三层防线:
1. **游标**:复合 keyset 游标 `(updated_at, article_id)` 扫新更新的 succeeded
   播客分析行或带 publisher locator 的单集;另有一根**轮转 sweep 游标**每轮再看一小页全部行,兜住那些不改分析行
   但改变全文处理输入 revision 的变更(RSS transcript locator、publisher transcript
   发布、source-media snapshot、archive sync 收养)。
2. **失败记忆**:按 episode 记录最近一次尝试(revision/次数/类别/下次可试时刻)。
   瞬时失败指数退避(5 min 起,×2,封顶 6 h,超过 8 次进入长停);确定性失败
   (媒体不合规/过长/不存在等)不进重试阶梯,只在 24 h 后重新核对一次,或该集的
   revision 变化时重新放行;守门类失败(供应商/ASR 未就绪、产物库无余量)是配置或
   容量问题而非该集自身缺陷,10 min 后再看。入队成功记 `enqueued` + 最终 revision,
   同 revision 不再触碰入队,直到对账为 settled 被清除。
3. **前置守门**(app 层):运行时未就绪整轮跳过不动状态;需要下载源音频的集在 ASR
   新提交 admission 未就绪或产物库余量不足本集下载上限时记 `gated`。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from models.db import AppSettingRecord, ArticleAnalysisRecord, ArticleRecord
from services import podcast_artifacts, podcast_processing_admin, podcast_source_media

CURSOR_KEY = "podcast_landing:cursor"
SWEEP_CURSOR_KEY = "podcast_landing:sweep_cursor"
STATE_KEY = "podcast_landing:attempts"

LANDING_BASES: tuple[str, ...] = (
    "podcast_show_notes",
    "publisher_transcript",
    "asr_transcript",
)

SCAN_LIMIT = 200
SWEEP_PAGE = 40
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
KIND_ENQUEUED = "enqueued"

# 该集自身的确定性缺陷:同一 revision 下重试不会改变结果。
DETERMINISTIC_ADMIN_CODES = frozenset(
    {
        "podcast_stage_denied",
        "podcast_not_found",
        "podcast_source_media_too_long",
        "podcast_artifact_not_ready",
        "podcast_selection_required",
    }
)
# 配置 / 容量类:随运维动作改变,按守门节奏回访而非长停。
GATE_ADMIN_CODES = frozenset({"podcast_provider_unavailable", "podcast_landing_gated"})

DETERMINISTIC_EXCEPTIONS: tuple[type[BaseException], ...] = (
    podcast_artifacts.PodcastArtifactTooLarge,
    podcast_artifacts.PodcastArtifactUnsupportedMedia,
    podcast_artifacts.PodcastArtifactProbeUnavailable,
    podcast_source_media.SourceMediaTooLarge,
    podcast_source_media.SourceMediaTooLong,
    podcast_source_media.SourceMediaNotFound,
)
GATE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    podcast_artifacts.PodcastArtifactStorageFull,
)


class PodcastLandingGated(podcast_processing_admin.PodcastAdminError):
    """Raised before any source-audio download when ASR admission or capacity is missing.

    The manual admin API maps it like any other 503; the landing round records
    it as ``gated`` so the episode is revisited on the gate cadence.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            "podcast_landing_gated",
            status_code=503,
            message=f"Podcast 全文处理暂不可入队:{reason}",
        )
        self.reason = reason


@dataclass(frozen=True)
class FailureClass:
    kind: str
    code: str


def classify_failure(exc: BaseException) -> FailureClass:
    """Map an enqueue exception to a retry class plus a short diagnostic code."""

    if isinstance(exc, PodcastLandingGated):
        return FailureClass(KIND_GATED, exc.reason)
    if isinstance(exc, podcast_processing_admin.PodcastAdminError):
        code = str(exc.code or "podcast_admin_error")
        if code in GATE_ADMIN_CODES:
            return FailureClass(KIND_GATED, code)
        kind = KIND_DETERMINISTIC if code in DETERMINISTIC_ADMIN_CODES else KIND_TRANSIENT
        return FailureClass(kind, code)
    if isinstance(exc, GATE_EXCEPTIONS):
        return FailureClass(KIND_GATED, type(exc).__name__)
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


def _read_setting(session: Session, key: str) -> str:
    record = session.get(AppSettingRecord, key)
    return (record.value if record else "").strip()


def _write_setting(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)


Cursor = tuple[str, str]
EMPTY_CURSOR: Cursor = ("", "")


def load_cursor(session: Session) -> Cursor:
    """Composite keyset cursor ``(updated_at, article_id)``; legacy plain strings adopt ``(ts, "")``."""

    raw = _read_setting(session, CURSOR_KEY)
    if not raw:
        return EMPTY_CURSOR
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return EMPTY_CURSOR
        if isinstance(payload, dict):
            return (
                str(payload.get("updated_at") or ""),
                str(payload.get("article_id") or ""),
            )
        return EMPTY_CURSOR
    return (raw, "")


def save_cursor(session: Session, cursor: Cursor) -> None:
    _write_setting(
        session,
        CURSOR_KEY,
        json.dumps({"updated_at": cursor[0], "article_id": cursor[1]}),
    )


def load_sweep_cursor(session: Session) -> str:
    return _read_setting(session, SWEEP_CURSOR_KEY)


def save_sweep_cursor(session: Session, after_id: str) -> None:
    _write_setting(session, SWEEP_CURSOR_KEY, after_id)


def load_state(session: Session) -> dict[str, dict]:
    raw = _read_setting(session, STATE_KEY)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
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
    _write_setting(session, STATE_KEY, json.dumps(state, ensure_ascii=False, sort_keys=True))


# ── 增量扫描与轮转 sweep ──────────────────────────────────────────────────────


def _landing_rows_statement():
    # Publisher locators are eligible even when show-note analysis is absent or
    # failed.  Keep those episode rows in both incremental scan and sweep; the
    # resolver performs the authoritative locator validation before enqueue.
    activity_at = func.max(
        func.coalesce(
            func.nullif(ArticleRecord.archive_updated_at, ""),
            ArticleRecord.fetched_date,
            "",
        ),
        func.coalesce(ArticleAnalysisRecord.updated_at, ""),
    )
    return (
        select(ArticleRecord.id, activity_at)
        .select_from(ArticleRecord)
        .outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .where(
            ArticleRecord.content_type == "podcast_episode",
            or_(
                and_(
                    ArticleAnalysisRecord.status == "succeeded",
                    ArticleAnalysisRecord.analysis_basis.in_(LANDING_BASES),
                ),
                ArticleRecord.extensions_json.like('%"transcripts"%'),
            ),
        )
    )


def scan_new_candidates(
    session: Session, cursor: Cursor, *, limit: int = SCAN_LIMIT
) -> tuple[list[str], Cursor]:
    """Return eligible analysis/publisher-locator rows after the composite cursor.

    ``updated_at`` is an ISO-8601 UTC string so lexical order is chronological;
    ties are broken by ``article_id`` so a page boundary inside one timestamp
    never skips rows. The returned cursor is the last row's key (or the input
    when nothing was found); callers persist it only after the round ran.
    """

    ts, article_id = cursor
    activity_at = func.max(
        func.coalesce(
            func.nullif(ArticleRecord.archive_updated_at, ""),
            ArticleRecord.fetched_date,
            "",
        ),
        func.coalesce(ArticleAnalysisRecord.updated_at, ""),
    )
    statement = _landing_rows_statement().order_by(
        activity_at.asc(), ArticleRecord.id.asc()
    ).limit(max(1, limit))
    if ts:
        statement = statement.where(
            or_(
                activity_at > ts,
                and_(
                    activity_at == ts,
                    ArticleRecord.id > article_id,
                ),
            )
        )
    rows = session.exec(statement).all()
    ids = [str(row_id) for row_id, _updated in rows]
    next_cursor = (str(rows[-1][1]), str(rows[-1][0])) if rows else cursor
    return ids, next_cursor


def scan_sweep_page(
    session: Session, after_id: str, *, page: int = SWEEP_PAGE
) -> tuple[list[str], str]:
    """One bounded page of *all* landing rows ordered by ``article_id``, wrapping at the end.

    The sweep is the closed-form backstop for revision changes that never touch
    the analysis row; it costs DB reads only (candidate resolution never goes
    to the network). Returns ``""`` as the next cursor when the page reached
    the end so the following round restarts from the top.
    """

    statement = _landing_rows_statement().order_by(ArticleRecord.id.asc()).limit(
        max(1, page)
    )
    if after_id:
        statement = statement.where(ArticleRecord.id > after_id)
    rows = session.exec(statement).all()
    ids = [str(row_id) for row_id, _updated in rows]
    next_after = ids[-1] if len(ids) >= max(1, page) else ""
    return ids, next_after


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

    Unknown episodes and revision changes are always allowed. For the recorded
    revision the ``retry_at`` decides; ``None`` (an ``enqueued`` entry) means
    never until the revision moves.
    """

    entry = state.get(episode_id)
    if entry is None:
        return True
    if str(entry.get("revision") or "") != revision:
        return True
    retry_at = _parse_iso(entry.get("retry_at"))
    return retry_at is not None and retry_at <= now


def transient_backoff_seconds(attempts: int) -> int:
    exponent = max(0, attempts - 1)
    return min(TRANSIENT_BASE_SECONDS * (2**exponent), TRANSIENT_MAX_SECONDS)


def _prior_attempts(prior: dict, revision: str) -> int:
    if str(prior.get("revision") or "") != revision:
        return 0
    return int(prior.get("attempts") or 0)


def record_failure(
    state: dict[str, dict],
    episode_id: str,
    revision: str,
    failure: FailureClass,
    now: dt.datetime,
) -> dict:
    prior = state.get(episode_id) or {}
    if failure.kind == KIND_GATED:
        return record_gated(state, episode_id, revision, failure.code, now)
    attempts = _prior_attempts(prior, revision) + 1
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
    """Remember an episode held back by a gate; attempts carry over only within one revision."""

    prior = state.get(episode_id) or {}
    entry = {
        "revision": revision,
        "attempts": _prior_attempts(prior, revision),
        "kind": KIND_GATED,
        "code": reason,
        "last_at": _now_iso(now),
        "retry_at": _now_iso(now + dt.timedelta(seconds=GATE_RECHECK_SECONDS)),
    }
    state[episode_id] = entry
    return entry


def record_enqueued(
    state: dict[str, dict], episode_id: str, revision: str, now: dt.datetime
) -> dict:
    """Durable memory of a successful enqueue: same revision is never re-enqueued."""

    entry = {
        "revision": revision,
        "attempts": 0,
        "kind": KIND_ENQUEUED,
        "code": "",
        "last_at": _now_iso(now),
        "retry_at": None,
    }
    state[episode_id] = entry
    return entry


def record_settled(state: dict[str, dict], episode_id: str) -> None:
    """The episode no longer needs landing (ineligible, or its input is already analysed)."""

    state.pop(episode_id, None)


def prune_state(state: dict[str, dict], now: dt.datetime) -> None:
    """Drop retry-class entries untouched for longer than the retention window.

    ``enqueued`` entries are exempt: they end only by reconciliation (settled),
    otherwise a pruned entry would let the sweep enqueue the same revision again.
    """

    stale = [
        episode_id
        for episode_id, entry in state.items()
        if entry.get("kind") != KIND_ENQUEUED
        and (
            (last := _parse_iso(entry.get("last_at"))) is None
            or (now - last).total_seconds() > STATE_RETENTION_SECONDS
        )
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
    "SWEEP_CURSOR_KEY",
    "STATE_KEY",
    "FailureClass",
    "PodcastLandingGated",
    "classify_failure",
    "load_cursor",
    "save_cursor",
    "load_sweep_cursor",
    "save_sweep_cursor",
    "load_state",
    "save_state",
    "scan_new_candidates",
    "scan_sweep_page",
    "due_retries",
    "is_allowed",
    "record_failure",
    "record_gated",
    "record_enqueued",
    "record_settled",
    "prune_state",
    "dedupe_preserving_order",
    "summarize",
)
