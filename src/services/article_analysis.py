"""Reliable, article-level AI analysis backed by SQLite state and leases.

The module intentionally owns no scheduler or article-write hook.  WP-4 can
connect the narrow functions below to every existing persistence path without
making analysis part of the article transaction:

``queue_article_analysis``
    Idempotently create/invalidate one task immediately after an article commit.
``scan_analysis_backfill``
    Compensate for every import/edit path and enqueue at most the latest seven
    days, newest first.
``claim_analysis_tasks`` / ``process_claimed_analysis``
    Short SQLite claim transaction followed by an out-of-transaction LLM call.
``run_analysis_cycle``
    A scheduler-friendly composition of recovery, scan, claim, and processing.

No function logs article titles, bodies, source URLs, prompt payloads, or model
output. User-added RSS follows its explicit source switch just like a platform
source; credential-bearing feeds remain ineligible for third-party analysis.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import inspect
import json
import logging
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import LLMConfig
from llm.article_analysis_prompt import (
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
    analysis_contract_versions,
    analysis_system_prompt,
    build_article_analysis_user_prompt,
)
from llm.client import ChatMessage, UsageMeta, chat_completion, parse_json_object
from models.analysis_contracts import (
    ANALYSIS_LEASE_SECONDS,
    AnalysisAttemptStatus,
    AnalysisOperation,
    AnalysisStatus,
    ArticleAnalysisResultDTO,
    ArticleTagAssignmentDTO,
    ArticleTagCandidateDTO,
    ContentGenre,
    TagAssignmentSource,
    TaggingStatus,
    TaxonomyTagDTO,
)
from models.db import (
    AppSettingRecord,
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    SourceConfigRecord,
    TagRetagJobRecord,
    TaxonomyVersionRecord,
)
from services.article_display_tags import extracted_tag_snapshot
from services.article_time import in_time_window, parse_article_time
from services import sync_consumer_policy


logger = logging.getLogger("dorami.article_analysis")

ARTICLE_ANALYSIS_ENABLED_KEY = "article_analysis_enabled"
TAXONOMY_CANDIDATE_ENABLED_KEY = "taxonomy_candidate_enabled"
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_MAX_ATTEMPTS = 4
# 版本键过期的 succeeded 行每个扫描 tick 最多失效多少篇(v3.48 收口:版本一变近 7 天存量
# 曾瞬时全部 invalidated 与新文章抢队列;现慢滴重跑,新到文章永远优先,7 天外等 full_analysis)。
VERSION_REFRESH_PER_CYCLE = 16
PODCAST_PEOPLE_SCAN_PAGE_SIZE = 64
PODCAST_PEOPLE_SCAN_CURSOR_KEY = "article_analysis:podcast_people_scan_cursor"
PODCAST_PEOPLE_DIRTY_REASON = "podcast publisher people changed"
DEFAULT_BATCH_SIZE = 8
DEFAULT_MAX_BATCHES_PER_CYCLE = 8
CLAIM_SCAN_PAGE_SIZE = 64
MAX_ANALYSIS_CONCURRENCY = 4
DEFAULT_SCAN_LIMIT = 500
# score_reason 解析上限：提示词要求 ≤40 汉字，此处留余量；超出即截断而非拒收。
_SCORE_REASON_MAX_CHARS = 120
MAX_ERROR_CHARS = 800
_PODCAST_FACTOR_KEYS = (
    "guest_authority",
    "topic_timeliness",
    "novelty",
    "evidence_depth",
    "viewpoint_diversity",
    "practical_value",
)

_TAG_LIMITS = {"topic": 5, "industry": 2, "entity": 3}
_GENERIC_RECALL_TOKENS = {"ai", "artificial", "intelligence", "topic", "industry", "entity"}
_DESCRIPTION_RECALL_STOPWORDS = {
    "a",
    "an",
    "and",
    "article",
    "for",
    "if",
    "in",
    "is",
    "only",
    "or",
    "the",
    "to",
    "use",
    "when",
    "一般",
    "主体",
    "事件",
    "产品",
    "企业",
    "体验",
    "使用",
    "功能",
    "发布",
    "工作流",
    "平台",
    "应用",
    "服务",
    "模型",
    "涉及",
    "版本",
    "组织",
}
_DESCRIPTION_RECALL_PREFIXES = (
    "仅当文章核心讨论",
    "仅当文章核心涉及",
    "文章核心讨论",
    "文章核心涉及",
    "仅当文章",
    "核心讨论",
    "核心涉及",
    "仅当",
    "普通",
)
_DESCRIPTION_RECALL_SUFFIXES = ("时使用", "不使用", "使用", "以及", "或者", "或")
_URL_RE = re.compile(r"(?i)\b(?:https?|feed)://[^\s<>\]\[)('\"]+")
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|authorization|password|secret)\s*[=:]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class AnalysisInput:
    """Minimal LLM input; notably excludes ``source_url``."""

    article_id: str
    title: str
    body: str
    content_type: str
    source_id: str
    publish_date: str
    fetched_date: str
    credentialed_source: bool
    source_owner_or_domain: str
    # 评分尺子的两个来源信号(issue #22):友好名 + 信息角色(官方/媒体/个人/榜单),
    # 厂商主次甄别与「一手发布 vs 转述」需要它;服务端派生,不喂 URL。
    source_name: str = ""
    source_role: str = ""
    analysis_basis: str = "article_body"
    people: tuple[dict[str, str], ...] = ()
    topic_heat: dict[str, Any] | None = None
    transcript_artifact_id: str | None = None


@dataclass(frozen=True)
class ClaimedAnalysisTask:
    article_id: str
    worker_id: str
    lease_token: str
    attempt_no: int
    content_hash: str


@dataclass(frozen=True)
class ReconcileStats:
    scanned: int = 0
    created: int = 0
    invalidated: int = 0
    skipped: int = 0
    unchanged: int = 0
    # 版本键过期但本 tick 预算用尽、留给下个 tick 的 succeeded 行数
    deferred: int = 0


@dataclass(frozen=True)
class ProcessResult:
    article_id: str
    status: str
    tagging_status: str


@dataclass(frozen=True)
class ValidatedAnalysis:
    result: ArticleAnalysisResultDTO
    warnings: tuple[str, ...]
    podcast_factors: dict[str, dict[str, str]]


Analyzer = Callable[
    [AnalysisInput, Sequence[TaxonomyTagDTO], LLMConfig],
    Awaitable[dict[str, Any] | ArticleAnalysisResultDTO] | dict[str, Any] | ArticleAnalysisResultDTO,
]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_utc(value: Optional[dt.datetime] = None) -> dt.datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _iso(value: Optional[dt.datetime] = None) -> str:
    # Analysis rows are an Archive Sync v2 keyset stream.  Its snapshot carries
    # microseconds, so truncating a mutation to whole seconds can place a write
    # that happened *after* the snapshot lexically before it and hide that state
    # forever from an incremental consumer.  Keep one canonical precision for
    # lifecycle, lease and revision timestamps.
    return _as_utc(value).isoformat(timespec="microseconds")


def _parse_datetime(value: str | None) -> Optional[dt.datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _flag_value(raw: str | None, default: bool) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


def read_feature_flag(session: Session, key: str, *, default: bool = False) -> bool:
    record = session.get(AppSettingRecord, key)
    return _flag_value(record.value if record else None, default)


def compute_content_hash(article: ArticleRecord) -> str:
    """Hash the complete title and body with an unambiguous boundary."""

    title = unicodedata.normalize("NFKC", article.title or "").strip()
    body = unicodedata.normalize("NFKC", article.content or "").strip()
    payload = json.dumps([title, body], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _analysis_messages(
    article: AnalysisInput, active_tags: Sequence[TaxonomyTagDTO]
) -> list[ChatMessage]:
    taxonomy_payload = [tag.model_dump() for tag in active_tags]
    return [
        ChatMessage(
            role="system",
            content=analysis_system_prompt(
                article.content_type, article.analysis_basis
            ),
        ),
        ChatMessage(
            role="user",
            content=build_article_analysis_user_prompt(
                title=article.title,
                body=article.body,
                content_type=article.content_type,
                source_id=article.source_id,
                source_name=article.source_name,
                source_role=article.source_role,
                taxonomy_tags=taxonomy_payload,
                people=article.people,
                topic_heat=article.topic_heat,
                analysis_basis=article.analysis_basis,
            ),
        ),
    ]


def compute_analysis_input_hash(
    article: AnalysisInput, active_tags: Sequence[TaxonomyTagDTO]
) -> str:
    """Hash the exact role/content messages sent to the configured model."""

    payload = json.dumps(
        [[str(message.role), message.content] for message in _analysis_messages(article, active_tags)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sanitize_error(error: BaseException | str) -> str:
    """Return a bounded diagnostic with URLs and common secret forms removed."""

    if isinstance(error, BaseException):
        raw = f"{type(error).__name__}: {error}"
    else:
        raw = str(error)
    raw = _URL_RE.sub("[redacted-url]", raw)
    raw = _SECRET_RE.sub("[redacted-secret]", raw)
    raw = " ".join(raw.split())
    return raw[:MAX_ERROR_CHARS]


def _source_params(source: SourceConfigRecord | None) -> dict[str, Any]:
    if source is None:
        return {}
    try:
        value = json.loads(source.params_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_credentialed_source(source: SourceConfigRecord | None) -> bool:
    """Whether sending this source's body to a third-party model is forbidden."""

    # Keep the URL-inference fallback in one place so signed feeds created by an
    # older release stay protected even before their params_json is backfilled.
    from services.user_sources import source_is_credentialed

    return source_is_credentialed(source)


def _source_allows_analysis(
    source: SourceConfigRecord | None,
    *,
    source_id: str = "",
) -> bool:
    # Registry-backed platform sources have no config row and retain default-on.
    # A user RSS without its policy row is an orphan, not a registry source: fail
    # closed because its credential classification can no longer be established.
    from services.user_sources import is_user_source

    if source is None:
        return not is_user_source(source_id)
    return bool(source.ai_analysis_enabled and not _is_credentialed_source(source))


def source_allows_analysis(session: Session, source_id: str) -> bool:
    """Honor the source switch while never exporting credentialed feed bodies."""

    source = session.get(SourceConfigRecord, source_id)
    return _source_allows_analysis(source, source_id=source_id)


def has_authoritative_analysis(record: ArticleAnalysisRecord | None) -> bool:
    """A successful prior result remains readable while a forced refresh runs."""

    return bool(
        record is not None
        and record.quality_score is not None
        and (
            record.status == AnalysisStatus.SUCCEEDED.value
            or record.analyzed_at
        )
    )


def _podcast_people_are_current(
    article: ArticleRecord, record: ArticleAnalysisRecord | None
) -> bool:
    if article.content_type != "podcast_episode" or record is None:
        return True
    return _podcast_people_payloads_are_current(
        article.extensions_json,
        record.analysis_diagnostics_json,
    )


def mark_podcast_people_dirty(
    session: Session,
    article_id: str,
    *,
    now: Optional[dt.datetime] = None,
) -> bool:
    """Persist a cheap reconciliation marker after publisher metadata refresh."""

    article = session.get(ArticleRecord, article_id)
    record = session.get(ArticleAnalysisRecord, article_id)
    if (
        article is None
        or article.content_type != "podcast_episode"
        or record is None
        or record.status != AnalysisStatus.SUCCEEDED.value
    ):
        return False
    is_current = _podcast_people_are_current(article, record)
    next_error = None if is_current else PODCAST_PEOPLE_DIRTY_REASON
    if is_current and record.last_error != PODCAST_PEOPLE_DIRTY_REASON:
        return False
    if next_error == record.last_error:
        return False
    record.last_error = next_error
    record.updated_at = _iso(now)
    session.add(record)
    return True


def _clear_authoritative_result(record: ArticleAnalysisRecord) -> None:
    record.quality_score = None
    record.podcast_initial_score = None
    record.podcast_final_score = None
    record.dimension_scores_json = "{}"
    record.analysis_basis = ""
    record.analysis_input_hash = ""
    record.transcript_artifact_id = None
    record.analysis_diagnostics_json = "{}"
    record.score_reason = ""
    record.summary = ""
    record.content_genre = None
    record.content_features_json = "[]"
    record.entities_json = "[]"
    record.display_tags_json = "[]"
    record.primary_tag_id = None
    record.analyzed_at = None
    record.tagged_at = None


def _next_attempt_number(session: Session, article_id: str) -> int:
    last = session.exec(
        select(func.max(ArticleAnalysisAttemptRecord.attempt_no)).where(
            ArticleAnalysisAttemptRecord.article_id == article_id
        )
    ).one()
    return int(last or 0) + 1


def _delete_stale_machine_tags(session: Session, article_id: str) -> None:
    session.exec(
        delete(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == article_id,
            ArticleTagAssignmentRecord.assignment_source == TagAssignmentSource.LLM.value,
        )
    )


def _close_running_attempts(
    session: Session,
    article_id: str,
    *,
    now: dt.datetime,
    reason: str,
) -> None:
    """Do not leave an orphan ``running`` attempt after task invalidation."""

    for attempt in session.exec(
        select(ArticleAnalysisAttemptRecord).where(
            ArticleAnalysisAttemptRecord.article_id == article_id,
            ArticleAnalysisAttemptRecord.status == AnalysisAttemptStatus.RUNNING.value,
        )
    ).all():
        attempt.status = AnalysisAttemptStatus.SKIPPED.value
        attempt.ended_at = _iso(now)
        started = _parse_datetime(attempt.started_at)
        attempt.duration_ms = (
            max(0, int((now - started).total_seconds() * 1000)) if started else None
        )
        attempt.error = reason
        session.add(attempt)


def revoke_queued_analysis(
    session: Session,
    article_id: str,
    *,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> bool:
    """Revoke pending/running execution while preserving any prior authority."""

    record = session.get(ArticleAnalysisRecord, article_id)
    if record is None or record.status not in {
        AnalysisStatus.PENDING.value,
        AnalysisStatus.RUNNING.value,
    }:
        return False
    current = _as_utc(now)
    _close_running_attempts(session, article_id, now=current, reason=reason)
    record.status = (
        AnalysisStatus.SUCCEEDED.value
        if has_authoritative_analysis(record)
        else AnalysisStatus.SKIPPED.value
    )
    record.started_at = None
    record.next_attempt_at = None
    record.lease_owner = None
    record.lease_expires_at = None
    record.last_error = reason
    record.updated_at = _iso(current)
    session.add(record)
    return True


def queue_article_analysis(
    session: Session,
    article_id: str,
    *,
    enabled: bool = True,
    force: bool = False,
    now: Optional[dt.datetime] = None,
) -> str:
    """Create or invalidate one analysis row after the article already exists.

    Returns ``created``, ``invalidated``, ``busy``, ``skipped``, ``unchanged``,
    ``remote_authority`` or ``ineligible``. ``force`` invalidates an otherwise current successful
    asset for a governed full-analysis backfill, but never interrupts a leased
    running attempt. The caller controls the transaction so an integration hook
    can batch this with other *analysis* work, but it must be called only after
    the article transaction has succeeded.
    """

    if not enabled:
        return "ineligible"
    article = session.get(ArticleRecord, article_id)
    if article is None or not article.has_content or not (article.content or "").strip():
        return "ineligible"
    # Archive Sync v2 把平台源的分析权威铆在 producer；即使两端
    # 都是 role=all，接收端的入队、补偿扫描和手动 force 也不得重算。
    if (article.analysis_authority_id or "").strip():
        return "remote_authority"

    now_iso = _iso(now)
    content_hash = compute_content_hash(article)
    prompt_version, scoring_version = analysis_contract_versions(article.content_type)
    source = session.get(SourceConfigRecord, article.source_id)
    record = session.get(ArticleAnalysisRecord, article.id)
    if (
        article.content_type == "podcast_episode"
        and record is not None
        and record.analysis_basis in {"publisher_transcript", "asr_transcript"}
    ):
        # Show notes are a lower-fidelity input.  Refresh/backfill/manual legacy
        # entry points must use the Podcast full-analysis selector instead of
        # replacing a completed transcript result.
        next_hash = compute_content_hash(article)
        if record.content_hash != next_hash:
            record.content_hash = next_hash
            record.updated_at = _iso(now)
            session.add(record)
        return "unchanged"
    if not sync_consumer_policy.local_source_operation_allowed(
        session, article.source_id, operation="analysis"
    ):
        if record is not None:
            revoke_queued_analysis(
                session,
                article.id,
                reason="v2_consumer_quiesced",
                now=now,
            )
        return "consumer_quiesced"
    allowed = _source_allows_analysis(source, source_id=article.source_id)

    if force and record is not None and record.status == AnalysisStatus.RUNNING.value:
        return "busy"

    if not allowed:
        if _is_credentialed_source(source):
            purge_source_candidate_evidence(
                session,
                article.source_id,
                now=_as_utc(now),
            )
        # Closing the source switch does not delete or mutate an already
        # successful result.  It only prevents a new/current task from running.
        if record is not None and record.status == AnalysisStatus.SUCCEEDED.value:
            return "unchanged"
        if (
            record is not None
            and record.status == AnalysisStatus.SKIPPED.value
            and record.content_hash == content_hash
            and record.prompt_version == prompt_version
            and record.scoring_version == scoring_version
        ):
            return "unchanged"
        if record is None:
            record = ArticleAnalysisRecord(
                article_id=article.id,
                status=AnalysisStatus.SKIPPED.value,
                tagging_status=TaggingStatus.PENDING.value,
                content_hash=content_hash,
                prompt_version=prompt_version,
                scoring_version=scoring_version,
                last_error="source_ai_analysis_disabled",
                created_at=now_iso,
                updated_at=now_iso,
            )
        else:
            _close_running_attempts(
                session,
                article.id,
                now=_as_utc(now),
                reason="source_ai_analysis_disabled",
            )
            record.status = AnalysisStatus.SKIPPED.value
            record.content_hash = content_hash
            record.prompt_version = prompt_version
            record.scoring_version = scoring_version
            record.next_attempt_at = None
            record.started_at = None
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error = "source_ai_analysis_disabled"
            record.updated_at = now_iso
        session.add(record)
        return "skipped"

    same_content = bool(record is not None and record.content_hash == content_hash)
    is_current = bool(
        same_content
        and record.prompt_version == prompt_version
        and record.scoring_version == scoring_version
        and _podcast_people_are_current(article, record)
    )
    if not force and is_current and record.status != AnalysisStatus.SKIPPED.value:
        return "unchanged"
    if (
        not force
        and same_content
        and record.status in {
            AnalysisStatus.PENDING.value,
            AnalysisStatus.RUNNING.value,
            AnalysisStatus.FAILED.value,
            AnalysisStatus.TIMEOUT.value,
        }
    ):
        # Do not let the periodic compensation scan invalidate an already queued
        # version refresh or reset its retry budget merely because V_old still
        # carries the previous prompt/scoring version.
        return "unchanged"

    if record is None:
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status=AnalysisStatus.PENDING.value,
                tagging_status=TaggingStatus.PENDING.value,
                content_hash=content_hash,
                prompt_version=prompt_version,
                scoring_version=scoring_version,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        return "created"

    # Forced refresh of the same content is a two-version handoff, including
    # the normal full-analysis case where only prompt/scoring versions are old:
    # readers keep the prior authority until replacement succeeds. Actual
    # article-content changes still invalidate immediately.
    preserve_authority = bool(same_content and has_authoritative_analysis(record))
    if not preserve_authority:
        _clear_authoritative_result(record)
        _delete_stale_machine_tags(session, article.id)
        _delete_candidate_evidence(session, article.id, now=_as_utc(now))
    elif not record.analyzed_at:
        # Legacy successful rows may predate the explicit authority timestamp.
        # Stamp it before status becomes pending so readers can identify V_old.
        record.analyzed_at = record.updated_at or now_iso
    _close_running_attempts(
        session,
        article.id,
        now=_as_utc(now),
        reason="analysis superseded by content or version change",
    )
    record.status = AnalysisStatus.PENDING.value
    if not preserve_authority:
        record.tagging_status = TaggingStatus.PENDING.value
    record.content_hash = content_hash
    if not preserve_authority:
        record.model_name = ""
    if not preserve_authority:
        record.prompt_version = prompt_version
        record.scoring_version = scoring_version
    record.attempt_count = 0
    record.started_at = None
    record.next_attempt_at = None
    record.lease_owner = None
    record.lease_expires_at = None
    record.last_error = None
    record.updated_at = now_iso
    session.add(record)
    return "invalidated"


def scan_analysis_backfill(
    session: Session,
    *,
    enabled: bool = True,
    now: Optional[dt.datetime] = None,
    lookback_days: int = DEFAULT_BACKFILL_DAYS,
    limit: int = DEFAULT_SCAN_LIMIT,
    version_refresh_limit: int = VERSION_REFRESH_PER_CYCLE,
) -> ReconcileStats:
    """Scan latest articles in descending order so new arrivals beat backfill.

    v3.48 收口:扫描只挑**需要动作**的行——轻列 LEFT JOIN ``article_analyses``,命中四类:
    ①无分析行;②``skipped``(源开关可能已重开);③``succeeded`` 但 prompt/scoring 版本键
    过期;④ Podcast ``succeeded`` 被入库刷新持久标记为 publisher persons 过期
    (后两类每 tick 合计最多 ``version_refresh_limit`` 篇,新到文章永远优先)。其余行
    (pending/running/failed/timeout/当前 succeeded)扫描不碰:重试与租约机制自管,内容变化
    由入库钩子与文章编辑端点显式入队覆盖。此前每分钟把 7 天全部正文载入内存逐篇比哈希,
    绝大多数结果是 unchanged。``scanned`` 自此语义为「需要动作的行数」。
    """

    if not enabled:
        return ReconcileStats()
    now_utc = _as_utc(now)
    since_time = now_utc - dt.timedelta(days=max(1, lookback_days))
    coarse_start = (since_time - dt.timedelta(days=1)).date().isoformat()
    coarse_end = (now_utc + dt.timedelta(days=1)).date().isoformat()
    from llm.article_analysis_prompt import (
        PODCAST_ANALYSIS_PROMPT_VERSION,
        PODCAST_ANALYSIS_SCORING_VERSION,
    )

    version_stale = and_(
        ArticleAnalysisRecord.status == AnalysisStatus.SUCCEEDED.value,
        or_(
            and_(
                ArticleRecord.content_type == "podcast_episode",
                or_(
                    ArticleAnalysisRecord.prompt_version != PODCAST_ANALYSIS_PROMPT_VERSION,
                    ArticleAnalysisRecord.scoring_version != PODCAST_ANALYSIS_SCORING_VERSION,
                ),
            ),
            and_(
                ArticleRecord.content_type != "podcast_episode",
                or_(
                    ArticleAnalysisRecord.prompt_version != ARTICLE_ANALYSIS_PROMPT_VERSION,
                    ArticleAnalysisRecord.scoring_version != ARTICLE_ANALYSIS_SCORING_VERSION,
                ),
            ),
        ),
    )
    light_rows = session.exec(
        select(
            ArticleRecord.id,
            ArticleRecord.fetched_date,
            ArticleRecord.source_id,
            ArticleAnalysisRecord.status,
        )
        .outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .where(
            ArticleRecord.has_content.is_(True),
            ArticleRecord.content.is_not(None),
            ArticleRecord.analysis_authority_id == "",
            func.substr(ArticleRecord.fetched_date, 1, 10) >= coarse_start,
            func.substr(ArticleRecord.fetched_date, 1, 10) <= coarse_end,
            or_(
                ArticleAnalysisRecord.article_id.is_(None),
                ArticleAnalysisRecord.status == AnalysisStatus.SKIPPED.value,
                version_stale,
            ),
        )
    ).all()
    # Metadata refreshes persist a dirty marker even while analysis is disabled.
    # A durable keyset cursor walks only a bounded dirty page per tick, so old
    # episodes are eventually covered without parsing all historical JSON.
    people_cursor_record = session.get(
        AppSettingRecord, PODCAST_PEOPLE_SCAN_CURSOR_KEY
    )
    people_cursor = (people_cursor_record.value if people_cursor_record else "").strip()
    people_page_size = max(1, PODCAST_PEOPLE_SCAN_PAGE_SIZE)

    def load_people_page(after_id: str):
        statement = (
            select(
                ArticleRecord.id,
                ArticleRecord.fetched_date,
                ArticleRecord.source_id,
                ArticleAnalysisRecord.status,
            )
            .join(
                ArticleAnalysisRecord,
                ArticleAnalysisRecord.article_id == ArticleRecord.id,
            )
            .where(
                ArticleRecord.content_type == "podcast_episode",
                ArticleRecord.has_content.is_(True),
                ArticleRecord.content.is_not(None),
                ArticleRecord.content != "",
                ArticleRecord.analysis_authority_id == "",
                ArticleAnalysisRecord.status == AnalysisStatus.SUCCEEDED.value,
                ArticleAnalysisRecord.last_error == PODCAST_PEOPLE_DIRTY_REASON,
            )
            .order_by(ArticleRecord.id)
            .limit(people_page_size)
        )
        if after_id:
            statement = statement.where(ArticleRecord.id > after_id)
        return list(session.exec(statement).all())

    people_page = load_people_page(people_cursor)
    if not people_page and people_cursor:
        people_cursor = ""
        people_page = load_people_page(people_cursor)
    next_people_cursor = (
        str(people_page[-1][0]) if len(people_page) >= people_page_size else ""
    )
    if people_cursor_record is None:
        people_cursor_record = AppSettingRecord(
            key=PODCAST_PEOPLE_SCAN_CURSOR_KEY,
            value=next_people_cursor,
        )
    else:
        people_cursor_record.value = next_people_cursor
    session.add(people_cursor_record)
    stale_people_rows = [
        (article_id, fetched, source_id, status)
        for article_id, fetched, source_id, status in people_page
    ]
    rows_by_id = {
        article_id: (article_id, fetched, source_id, status)
        for article_id, fetched, source_id, status in stale_people_rows
    }
    rows_by_id.update(
        {
            article_id: (article_id, fetched, source_id, status)
            for article_id, fetched, source_id, status in light_rows
            if in_time_window(fetched, start=since_time, end=now_utc)
        }
    )
    rows = sorted(
        rows_by_id.values(),
        key=lambda row: (
            parse_article_time(row[1]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            row[0],
        ),
        reverse=True,
    )
    counts: Counter[str] = Counter()
    scanned = 0
    actionable = 0
    deferred = 0
    refresh_budget = max(0, version_refresh_limit)
    for article_id, _fetched, source_id, status in rows:
        if status == AnalysisStatus.SUCCEEDED.value:
            source = session.get(SourceConfigRecord, source_id)
            if (
                not _source_allows_analysis(source, source_id=source_id)
                or not sync_consumer_policy.local_source_operation_allowed(
                    session, source_id, operation="analysis"
                )
            ):
                continue
            if refresh_budget <= 0:
                deferred += 1
                continue
        scanned += 1
        outcome = queue_article_analysis(session, article_id, enabled=True, now=now_utc)
        counts[outcome] += 1
        if status == AnalysisStatus.SUCCEEDED.value and outcome == "invalidated":
            refresh_budget -= 1
        if outcome in {"created", "invalidated", "skipped"}:
            actionable += 1
            if actionable >= max(1, limit):
                break
    session.commit()
    return ReconcileStats(
        scanned=scanned,
        created=counts["created"],
        invalidated=counts["invalidated"],
        skipped=counts["skipped"],
        unchanged=counts["unchanged"],
        deferred=deferred,
    )


def _retry_delay(attempt_count: int) -> dt.timedelta:
    seconds = (60, 5 * 60, 30 * 60, 2 * 60 * 60)
    return dt.timedelta(seconds=seconds[min(max(attempt_count - 1, 0), len(seconds) - 1)])


def _finish_or_retry(
    record: ArticleAnalysisRecord,
    *,
    terminal_status: str,
    error: str,
    now: dt.datetime,
    max_attempts: int,
) -> None:
    record.status = terminal_status
    record.started_at = None
    record.lease_owner = None
    record.lease_expires_at = None
    record.last_error = error
    record.updated_at = _iso(now)
    record.next_attempt_at = (
        _iso(now + _retry_delay(record.attempt_count))
        if record.attempt_count < max_attempts
        else None
    )


def recover_expired_leases(
    session: Session,
    *,
    now: Optional[dt.datetime] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Turn abandoned running attempts into timeout rows eligible for retry."""

    now_utc = _as_utc(now)
    expired = list(
        session.exec(
            select(ArticleAnalysisRecord).where(
                ArticleAnalysisRecord.status == AnalysisStatus.RUNNING.value,
                ArticleAnalysisRecord.lease_expires_at.is_not(None),
                ArticleAnalysisRecord.lease_expires_at <= _iso(now_utc),
            )
        ).all()
    )
    for record in expired:
        error = "analysis lease expired after 300 seconds"
        _finish_or_retry(
            record,
            terminal_status=AnalysisStatus.TIMEOUT.value,
            error=error,
            now=now_utc,
            max_attempts=max_attempts,
        )
        attempt = session.exec(
            select(ArticleAnalysisAttemptRecord)
            .where(
                ArticleAnalysisAttemptRecord.article_id == record.article_id,
                ArticleAnalysisAttemptRecord.status == AnalysisAttemptStatus.RUNNING.value,
            )
            .order_by(ArticleAnalysisAttemptRecord.attempt_no.desc())
        ).first()
        if attempt is not None:
            attempt.status = AnalysisAttemptStatus.TIMEOUT.value
            attempt.ended_at = _iso(now_utc)
            started = _parse_datetime(attempt.started_at)
            attempt.duration_ms = max(0, int((now_utc - started).total_seconds() * 1000)) if started else None
            attempt.error = error
            session.add(attempt)
        session.add(record)
    if expired:
        session.commit()
        logger.info("Recovered %d expired article-analysis lease(s)", len(expired))
    return len(expired)


def claim_analysis_tasks(
    session: Session,
    *,
    worker_id: str,
    limit: int = DEFAULT_BATCH_SIZE,
    now: Optional[dt.datetime] = None,
    lease_seconds: int = ANALYSIS_LEASE_SECONDS,
) -> list[ClaimedAnalysisTask]:
    """Atomically lease eligible work; newest article wins every claim cycle."""

    worker = (worker_id or "").strip()
    if not worker:
        raise ValueError("worker_id is required")
    now_utc = _as_utc(now)
    now_iso = _iso(now_utc)
    eligible = or_(
        ArticleAnalysisRecord.status == AnalysisStatus.PENDING.value,
        (
            ArticleAnalysisRecord.status.in_(
                [AnalysisStatus.FAILED.value, AnalysisStatus.TIMEOUT.value]
            )
            & ArticleAnalysisRecord.next_attempt_at.is_not(None)
            & (ArticleAnalysisRecord.next_attempt_at <= now_iso)
        ),
    )
    claimed: list[ClaimedAnalysisTask] = []
    revoked = False
    effective_limit = max(1, limit)
    scan_page_size = max(CLAIM_SCAN_PAGE_SIZE, effective_limit * 4)
    effective_lease_seconds = min(
        ANALYSIS_LEASE_SECONDS,
        max(1, lease_seconds),
    )
    lease_expires = _iso(now_utc + dt.timedelta(seconds=effective_lease_seconds))
    cursor_fetched_date = ""
    cursor_article_id = ""
    cursor_initialized = False
    while len(claimed) < effective_limit:
        query = (
            select(ArticleAnalysisRecord.article_id, ArticleRecord.fetched_date)
            .join(ArticleRecord, ArticleRecord.id == ArticleAnalysisRecord.article_id)
            .where(eligible, ArticleRecord.analysis_authority_id == "")
        )
        if cursor_initialized:
            query = query.where(
                or_(
                    ArticleRecord.fetched_date < cursor_fetched_date,
                    (ArticleRecord.fetched_date == cursor_fetched_date)
                    & (ArticleRecord.id < cursor_article_id),
                )
            )
        candidates = list(
            session.exec(
                query.order_by(
                    ArticleRecord.fetched_date.desc(), ArticleRecord.id.desc()
                ).limit(scan_page_size)
            ).all()
        )
        if not candidates:
            break
        # Advance from the page's immutable ordering key, irrespective of
        # whether policy revocation changes a row. Failed/timeout public rows
        # are eligible for retry but are not revocable by revoke_queued_analysis;
        # relying on mutation for progress would repeatedly scan the same page
        # and starve an older local custom-RSS task forever.
        cursor_article_id, cursor_fetched_date = candidates[-1]
        cursor_initialized = True
        for article_id, _fetched_date in candidates:
            if len(claimed) >= effective_limit:
                break
            article = session.get(ArticleRecord, article_id)
            if article is None or not sync_consumer_policy.local_source_operation_allowed(
                session, article.source_id, operation="analysis"
            ):
                item_revoked = revoke_queued_analysis(
                    session,
                    article_id,
                    reason="v2_consumer_quiesced",
                    now=now_utc,
                )
                revoked = item_revoked or revoked
                continue
            lease_token = f"{worker}:{uuid.uuid4().hex}"
            result = session.exec(
                update(ArticleAnalysisRecord)
                .where(ArticleAnalysisRecord.article_id == article_id, eligible)
                .values(
                    status=AnalysisStatus.RUNNING.value,
                    attempt_count=ArticleAnalysisRecord.attempt_count + 1,
                    started_at=now_iso,
                    next_attempt_at=None,
                    lease_owner=lease_token,
                    lease_expires_at=lease_expires,
                    last_error=None,
                    updated_at=now_iso,
                )
            )
            if result.rowcount != 1:
                continue
            record = session.get(ArticleAnalysisRecord, article_id)
            if record is None:
                continue
            prompt_version, scoring_version = analysis_contract_versions(article.content_type)
            attempt_no = _next_attempt_number(session, article_id)
            session.add(
                ArticleAnalysisAttemptRecord(
                    article_id=article_id,
                    attempt_no=attempt_no,
                    operation=AnalysisOperation.FULL_ANALYSIS.value,
                    status=AnalysisAttemptStatus.RUNNING.value,
                    content_hash=record.content_hash,
                    # A preserved V_old keeps its own version fields readable while
                    # refresh is pending; this attempt always runs the current contract.
                    prompt_version=prompt_version,
                    scoring_version=scoring_version,
                    taxonomy_version=record.taxonomy_version,
                    started_at=now_iso,
                    created_at=now_iso,
                )
            )
            session.flush()
            claimed.append(
                ClaimedAnalysisTask(
                    article_id=article_id,
                    worker_id=worker,
                    lease_token=lease_token,
                    attempt_no=attempt_no,
                    content_hash=record.content_hash,
                )
            )
        if len(candidates) < scan_page_size:
            break
    if claimed or revoked:
        session.commit()
    return claimed


def _normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().strip()
    value = re.sub(r"[\s\-_./·]+", " ", value)
    value = re.sub(r"[^\w\s\u3400-\u9fff]", "", value)
    return " ".join(value.split())


def _normalize_recall_text(value: str) -> str:
    """Normalize prose for permissive recall without changing tag identity rules."""

    value = unicodedata.normalize("NFKC", value or "").casefold().strip()
    value = re.sub(r"[\s\-_./·]+", " ", value)
    value = re.sub(r"[^\w\s\u3400-\u9fff]", " ", value)
    return " ".join(value.split())


def _prompt_description_recall_terms(value: str) -> tuple[str, ...]:
    """Extract bounded lexical anchors from governed model guidance.

    The description is only a recall aid.  Negative examples may therefore
    widen the closed set, but the LLM still receives the complete guidance and
    remains responsible for the final assignment boundary.
    """

    terms: list[str] = []
    for raw in _normalize_recall_text(value).split():
        term = raw
        for prefix in _DESCRIPTION_RECALL_PREFIXES:
            if term.startswith(prefix):
                term = term[len(prefix) :]
                break
        for suffix in _DESCRIPTION_RECALL_SUFFIXES:
            if term.endswith(suffix):
                term = term[: -len(suffix)]
                break
        if (
            len(term) >= 2
            and term not in _GENERIC_RECALL_TOKENS
            and term not in _DESCRIPTION_RECALL_STOPWORDS
        ):
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def _clean_text(value: Any, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def validate_analysis_payload(
    payload: dict[str, Any] | ArticleAnalysisResultDTO,
    *,
    active_tags: Sequence[TaxonomyTagDTO],
) -> ValidatedAnalysis:
    """Validate base fields strictly while isolating malformed tag fields."""

    if isinstance(payload, ArticleAnalysisResultDTO):
        # DTO construction proves shape/ranges, but active-code and per-facet
        # checks still belong to this service boundary.
        payload = payload.model_dump()
    if not isinstance(payload, dict):
        raise ValueError("analysis output must be a JSON object")

    try:
        score = float(payload["quality_score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("analysis base fields are invalid") from exc
    warnings: list[str] = []
    try:
        genre = ContentGenre(str(payload.get("content_genre") or ""))
    except ValueError:
        # 闭集外体裁(模型自造 model_evaluation 之类)降级为 other 并记 warning:
        # 体裁是展示字段,不该让整篇分析连同分数一起作废(issue #33 F6——生产样本里
        # 一篇 7.0 分的评测因此永远进不了分析表,重试两次同样输出)。
        genre = ContentGenre.OTHER
        warnings.append("unknown_genre_fallback")
    # score_reason 是分数注脚（提示词要求 ≤40 字）：上限留 3 倍余量容忍模型超写，
    # 但不再给它 1200 字——那正是它长成第二段摘要的空间（issue #13）。
    reason = _clean_text(payload.get("score_reason"), max_chars=_SCORE_REASON_MAX_CHARS)
    summary = _clean_text(payload.get("summary"), max_chars=6_000)
    if not reason or not summary:
        raise ValueError("analysis summary and score_reason must be non-empty")

    factors: dict[str, dict[str, str]] = {}
    raw_factors = payload.get("podcast_factors")
    if raw_factors is not None:
        if not isinstance(raw_factors, dict):
            warnings.append("podcast_factors_not_object")
        else:
            for key in _PODCAST_FACTOR_KEYS:
                item = raw_factors.get(key)
                if not isinstance(item, dict):
                    warnings.append(f"missing_podcast_factor:{key}")
                    continue
                level = str(item.get("level") or "").strip().casefold()
                evidence = _clean_text(item.get("evidence"), max_chars=120)
                if level not in {"low", "medium", "high"} or not evidence:
                    warnings.append(f"invalid_podcast_factor:{key}")
                    continue
                factors[key] = {"level": level, "evidence": evidence}

    tag_map = {tag.code: tag for tag in active_tags if tag.status == "active"}
    raw_assignments = payload.get("tag_assignments", [])
    if not isinstance(raw_assignments, list):
        raw_assignments = []
        warnings.append("tag_assignments_not_list")
    parsed_assignments: list[ArticleTagAssignmentDTO] = []
    for raw in raw_assignments[:20]:
        try:
            parsed_assignments.append(ArticleTagAssignmentDTO.model_validate(raw))
        except ValidationError:
            warnings.append("invalid_tag_assignment")
    facet_rank = {"topic": 0, "industry": 1, "entity": 2}
    parsed_assignments.sort(
        key=lambda item: (-item.relevance, facet_rank[str(item.kind)], item.code)
    )

    assignments: list[ArticleTagAssignmentDTO] = []
    seen_codes: set[str] = set()
    counts: Counter[str] = Counter()
    for item in parsed_assignments:
        active = tag_map.get(item.code)
        if active is None or active.kind != item.kind:
            warnings.append("unknown_or_mismatched_tag")
            continue
        if item.code in seen_codes or counts[str(item.kind)] >= _TAG_LIMITS[str(item.kind)]:
            warnings.append("duplicate_or_excess_tag")
            continue
        seen_codes.add(item.code)
        counts[str(item.kind)] += 1
        assignments.append(item.model_copy(update={"is_primary": False}))

    raw_candidates = payload.get("tag_candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []
        warnings.append("tag_candidates_not_list")
    candidates: list[ArticleTagCandidateDTO] = []
    seen_candidates: set[tuple[str, str]] = set()
    active_names = {
        (_normalize_label(name), str(tag.kind))
        for tag in active_tags
        for name in (tag.code, tag.name_zh, tag.name_en)
        if name
    }
    for raw in raw_candidates[:12]:
        try:
            item = ArticleTagCandidateDTO.model_validate(raw)
        except ValidationError:
            warnings.append("invalid_tag_candidate")
            continue
        label = _clean_text(item.label, max_chars=120)
        evidence = _clean_text(item.evidence, max_chars=400)
        normalized = _normalize_label(label)
        key = (normalized, str(item.proposed_kind))
        if not normalized or key in seen_candidates or key in active_names:
            warnings.append("duplicate_or_known_candidate")
            continue
        seen_candidates.add(key)
        candidates.append(item.model_copy(update={"label": label, "evidence": evidence}))
    candidates.sort(
        key=lambda item: (
            -item.confidence,
            facet_rank[str(item.proposed_kind)],
            _normalize_label(item.label),
        )
    )

    raw_primary = str(payload.get("primary_tag_code") or "").strip() or None
    if raw_primary and raw_primary not in {item.code for item in assignments}:
        warnings.append("invalid_primary_tag")
    # The service boundary, not model array order, owns the primary invariant.
    # Relevance wins globally; a tie prefers the semantic topic over an entity
    # mention and then industry, producing stable article-list labels.
    selected_primary = assignments[0].code if assignments else None
    assignments = [
        item.model_copy(update={"is_primary": item.code == selected_primary})
        for item in assignments
    ]

    raw_features = payload.get("content_features", [])
    if not isinstance(raw_features, list):
        raw_features = []
        warnings.append("content_features_not_list")
    features = tuple(
        dict.fromkeys(
            value
            for value in (_clean_text(item, max_chars=80) for item in raw_features[:12])
            if value
        )
    )
    raw_entities = payload.get("entities", [])
    if not isinstance(raw_entities, list):
        raw_entities = []
        warnings.append("entities_not_list")
    entities: list[dict[str, object]] = []
    for raw in raw_entities[:12]:
        if not isinstance(raw, dict):
            warnings.append("invalid_entity")
            continue
        name = _clean_text(raw.get("name"), max_chars=120)
        entity_type = _clean_text(raw.get("type"), max_chars=60)
        try:
            relevance = min(1.0, max(0.0, float(raw.get("relevance", 0.0))))
        except (TypeError, ValueError):
            warnings.append("invalid_entity")
            continue
        if name:
            entities.append({"name": name, "type": entity_type, "relevance": relevance})

    try:
        result = ArticleAnalysisResultDTO(
            quality_score=score,
            score_reason=reason,
            summary=summary,
            content_genre=genre,
            primary_tag_code=selected_primary,
            tag_assignments=tuple(assignments),
            tag_candidates=tuple(candidates),
            content_features=features,
            entities=tuple(entities),
        )
    except ValidationError as exc:
        raise ValueError("analysis base fields failed schema validation") from exc
    return ValidatedAnalysis(
        result=result,
        warnings=tuple(dict.fromkeys(warnings)),
        podcast_factors=factors,
    )


def _active_taxonomy_version(session: Session) -> int:
    row = session.exec(
        select(TaxonomyVersionRecord)
        .where(TaxonomyVersionRecord.status == "active")
        .order_by(TaxonomyVersionRecord.version.desc())
    ).first()
    return int(row.version) if row else 0


def load_relevant_active_tags(
    session: Session,
    article: ArticleRecord,
    *,
    limit: int = 120,
) -> list[TaxonomyTagDTO]:
    """Small lexical recall stage; never dumps the whole taxonomy into a prompt."""

    haystack = _normalize_recall_text(
        f"{article.title or ''} {(article.content or '')[:8000]}"
    )
    tags = list(
        session.exec(
            select(CmsTagRecord)
            .where(CmsTagRecord.status == "active")
            .order_by(CmsTagRecord.kind, CmsTagRecord.code)
        ).all()
    )
    aliases_by_tag: dict[int, list[str]] = {}
    tag_ids = [int(tag.id) for tag in tags if tag.id is not None]
    if tag_ids:
        for alias in session.exec(
            select(CmsTagAliasRecord).where(CmsTagAliasRecord.tag_id.in_(tag_ids))
        ).all():
            aliases_by_tag.setdefault(int(alias.tag_id), []).append(alias.alias)

    scored: list[tuple[int, CmsTagRecord]] = []
    tokens = set(haystack.split())
    for tag in tags:
        names = [
            tag.normalized_name,
            tag.name_zh,
            tag.name_en,
            *aliases_by_tag.get(int(tag.id or 0), []),
        ]
        normalized_names = [_normalize_recall_text(value) for value in names if value]
        exact_lengths = [len(value) for value in normalized_names if value and value in haystack]
        description_lengths = [
            len(value)
            for value in _prompt_description_recall_terms(tag.prompt_description)
            if value in haystack
        ]
        overlap = 0
        for value in normalized_names:
            meaningful = {
                token for token in value.split() if token not in _GENERIC_RECALL_TOKENS
            }
            matched = len(meaningful & tokens)
            if len(meaningful) >= 2 and matched >= 2:
                overlap = max(overlap, matched)
        if exact_lengths:
            scored.append((1_000 + max(exact_lengths), tag))
        elif description_lengths:
            scored.append((500 + max(description_lengths), tag))
        elif overlap:
            scored.append((overlap, tag))
    scored.sort(key=lambda item: (-item[0], item[1].kind, item[1].code))
    return [
        TaxonomyTagDTO(
            id=tag.id or 0,
            code=tag.code,
            kind=tag.kind,
            name_zh=tag.name_zh,
            name_en=tag.name_en,
            prompt_description=tag.prompt_description,
            status=tag.status,
            user_selectable=tag.user_selectable,
        )
        for _, tag in scored[: max(0, limit)]
    ]


async def analyze_article_with_llm(
    article: AnalysisInput,
    active_tags: Sequence[TaxonomyTagDTO],
    llm_config: LLMConfig,
    *,
    usage_meta: UsageMeta | None = None,
    http_client=None,
) -> dict[str, Any]:
    """The platform's single article-scoring call (issue #22).

    Used by the analysis worker and, for candidates the worker has not reached
    yet, by the public daily brief's in-place scoring — same prompt, same
    parser, so both produce the one news-value ruler.  Caller may inject a fake
    in tests.  ``usage_meta`` defaults to the worker's unattributed
    ``article_analysis`` purpose; the brief passes its trigger attribution.
    """

    raw = await chat_completion(
        messages=_analysis_messages(article, active_tags),
        config=llm_config.for_aux(),
        response_json=True,
        usage_meta=usage_meta or UsageMeta(purpose="article_analysis", username=None),
        http_client=http_client,
    )
    return parse_json_object(raw)


def _article_extensions(article: ArticleRecord) -> dict[str, Any]:
    try:
        value = json.loads(article.extensions_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalized_podcast_people(people: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(people, list):
        return ()
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in people[:40]:
        if not isinstance(raw, dict):
            continue
        item = {
            key: _clean_text(raw.get(key), max_chars=160)
            for key in ("name", "role", "group", "scope", "evidence")
            if raw.get(key)
        }
        if not item.get("name"):
            continue
        key = (
            unicodedata.normalize("NFKC", item["name"]).casefold(),
            item.get("role", ""),
            item.get("scope", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return tuple(normalized)


def _podcast_people(article: ArticleRecord) -> tuple[dict[str, str], ...]:
    return _normalized_podcast_people(_article_extensions(article).get("persons"))


def _podcast_people_payloads_are_current(
    extensions_json: str | None,
    diagnostics_json: str | None,
) -> bool:
    try:
        extensions = json.loads(extensions_json or "{}")
        diagnostics = json.loads(diagnostics_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(extensions, dict) or not isinstance(diagnostics, dict):
        return False
    recorded = diagnostics.get("people")
    if not isinstance(recorded, list):
        recorded = []
    return recorded == list(_normalized_podcast_people(extensions.get("persons")))


def build_topic_heat_context(
    session: Session,
    *,
    article_id: str,
    active_tags: Sequence[TaxonomyTagDTO],
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Snapshot seven-day canonical topic/entity heat without RSS volume bias."""

    snapshot = _as_utc(now)
    start = snapshot - dt.timedelta(days=7)
    relevant = {
        int(tag.id): tag
        for tag in active_tags
        if str(tag.kind) in {"topic", "entity"}
    }
    context: dict[str, Any] = {
        "window_days": 7,
        "snapshot_at": _iso(snapshot),
        "signals": [],
    }
    if not relevant:
        return context

    public_ids: set[str] = set()
    briefs = session.exec(
        select(ArticleRecord).where(ArticleRecord.source_id == "dorami_daily_brief")
    ).all()
    for brief in briefs:
        brief_time = parse_article_time(brief.publish_date) or parse_article_time(
            brief.fetched_date
        )
        if brief_time is None or not (start <= brief_time <= snapshot):
            continue
        included = _article_extensions(brief).get("included_article_ids")
        if isinstance(included, list):
            public_ids.update(str(value) for value in included if value)

    rows = session.exec(
        select(
            ArticleTagAssignmentRecord,
            ArticleRecord,
            ArticleAnalysisRecord,
        )
        .join(ArticleRecord, ArticleRecord.id == ArticleTagAssignmentRecord.article_id)
        .outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .where(
            ArticleTagAssignmentRecord.tag_id.in_(list(relevant)),
            ArticleRecord.id != article_id,
            ArticleRecord.source_id != "dorami_daily_brief",
        )
    ).all()
    aggregates: dict[int, dict[str, Any]] = {}
    for assignment, evidence_article, analysis in rows:
        seen_at = parse_article_time(evidence_article.publish_date) or parse_article_time(
            evidence_article.fetched_date
        )
        if seen_at is None or not (start <= seen_at <= snapshot):
            continue
        tag_id = int(assignment.tag_id)
        aggregate = aggregates.setdefault(
            tag_id,
            {"sources": set(), "max_quality_score": None, "latest_seen_at": seen_at,
             "in_public_daily_brief": False},
        )
        aggregate["sources"].add(evidence_article.source_id)
        aggregate["latest_seen_at"] = max(aggregate["latest_seen_at"], seen_at)
        if evidence_article.id in public_ids:
            aggregate["in_public_daily_brief"] = True
        score = analysis.quality_score if analysis is not None else None
        if score is not None:
            current = aggregate["max_quality_score"]
            aggregate["max_quality_score"] = max(float(score), current or float(score))

    for tag_id in sorted(aggregates, key=lambda value: relevant[value].code):
        tag = relevant[tag_id]
        aggregate = aggregates[tag_id]
        context["signals"].append(
            {
                "code": tag.code,
                "kind": str(tag.kind),
                "name": tag.name_zh or tag.name_en or tag.code,
                "distinct_source_count": len(aggregate["sources"]),
                "max_quality_score": aggregate["max_quality_score"],
                "latest_seen_at": _iso(aggregate["latest_seen_at"]),
                "in_public_daily_brief": aggregate["in_public_daily_brief"],
            }
        )
    return context


def analysis_input_from_article(
    article: ArticleRecord,
    source: SourceConfigRecord | None,
    *,
    active_tags: Sequence[TaxonomyTagDTO] = (),
    session: Session | None = None,
    now: Optional[dt.datetime] = None,
) -> AnalysisInput:
    """Build the LLM input for one article; shared by the worker and the brief."""

    from services.source_naming import friendly_source_name, source_role

    owner = ((source.source_owner if source else "") or article.source_id).strip()
    sid = article.source_id or ""
    is_podcast = (article.content_type or "").strip() == "podcast_episode"
    return AnalysisInput(
        article_id=article.id,
        title=article.title or "",
        body=article.content or "",
        content_type=article.content_type or "",
        source_id=sid,
        publish_date=article.publish_date or "",
        fetched_date=article.fetched_date or "",
        credentialed_source=_is_credentialed_source(source),
        source_owner_or_domain=owner,
        source_name=(source.name if source and source.name else friendly_source_name(sid)),
        source_role=source_role(
            sid,
            source_scope=source.source_scope if source else None,
            provenance_tier=source.provenance_tier if source else None,
        ),
        analysis_basis="podcast_show_notes" if is_podcast else "article_body",
        people=_podcast_people(article) if is_podcast else (),
        topic_heat=(
            build_topic_heat_context(
                session,
                article_id=article.id,
                active_tags=active_tags,
                now=now,
            )
            if is_podcast and session is not None
            else None
        ),
    )


def _input_for(session: Session, article_id: str) -> tuple[AnalysisInput, list[TaxonomyTagDTO]]:
    article = session.get(ArticleRecord, article_id)
    if article is None:
        raise LookupError("article missing")
    source = session.get(SourceConfigRecord, article.source_id)
    active_tags = load_relevant_active_tags(session, article)
    return (
        analysis_input_from_article(
            article,
            source,
            active_tags=active_tags,
            session=session,
        ),
        active_tags,
    )


def _attempt_for(session: Session, task: ClaimedAnalysisTask) -> ArticleAnalysisAttemptRecord | None:
    return session.exec(
        select(ArticleAnalysisAttemptRecord).where(
            ArticleAnalysisAttemptRecord.article_id == task.article_id,
            ArticleAnalysisAttemptRecord.attempt_no == task.attempt_no,
        )
    ).first()


def _candidate_cutoff(day: dt.date, days: int) -> dt.date:
    return day - dt.timedelta(days=days - 1)


def _refresh_candidate_stats(session: Session, candidate: CmsTagCandidateRecord, now: dt.datetime) -> None:
    evidence = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord).where(
                CmsTagCandidateEvidenceRecord.candidate_id == candidate.id
            )
        ).all()
    )
    today = now.date()
    for days in (7, 30):
        cutoff = _candidate_cutoff(today, days)
        scoped = []
        for item in evidence:
            parsed = _parse_datetime(item.published_date)
            if parsed is None:
                try:
                    published_day = dt.date.fromisoformat((item.published_date or "")[:10])
                except ValueError:
                    published_day = today
            else:
                published_day = parsed.date()
            if published_day >= cutoff:
                scoped.append((item, published_day))
        setattr(candidate, f"support_article_count_{days}d", len(scoped))
        setattr(
            candidate,
            f"distinct_source_count_{days}d",
            len({item.source_owner_or_domain or item.source_id for item, _ in scoped}),
        )
        setattr(candidate, f"distinct_day_count_{days}d", len({day for _, day in scoped}))
    candidate.mean_confidence = (
        sum(item.confidence for item in evidence) / len(evidence) if evidence else 0.0
    )
    candidate.sample_article_ids_json = json.dumps(
        [item.article_id for item in evidence[-10:]], ensure_ascii=False
    )
    candidate.updated_at = _iso(now)
    session.add(candidate)


def _delete_candidate_evidence(
    session: Session,
    article_id: str,
    *,
    now: dt.datetime,
) -> list[CmsTagCandidateRecord]:
    candidate_ids = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord.candidate_id).where(
                CmsTagCandidateEvidenceRecord.article_id == article_id
            )
        ).all()
    )
    if not candidate_ids:
        return []
    candidates = list(
        session.exec(
            select(CmsTagCandidateRecord).where(CmsTagCandidateRecord.id.in_(candidate_ids))
        ).all()
    )
    session.exec(
        delete(CmsTagCandidateEvidenceRecord).where(
            CmsTagCandidateEvidenceRecord.article_id == article_id
        )
    )
    session.flush()
    for candidate in candidates:
        _refresh_candidate_stats(session, candidate, now)
    return candidates


def purge_source_candidate_evidence(
    session: Session,
    source_id: str,
    *,
    now: Optional[dt.datetime] = None,
) -> int:
    """Retract global Candidate evidence when a source becomes credentialed."""

    current = _as_utc(now)
    evidence = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord).where(
                CmsTagCandidateEvidenceRecord.source_id == source_id
            )
        ).all()
    )
    if not evidence:
        return 0
    candidate_ids = {int(item.candidate_id) for item in evidence}
    session.exec(
        delete(CmsTagCandidateEvidenceRecord).where(
            CmsTagCandidateEvidenceRecord.source_id == source_id
        )
    )
    session.flush()
    for candidate_id in candidate_ids:
        candidate = session.get(CmsTagCandidateRecord, candidate_id)
        if candidate is not None:
            _refresh_candidate_stats(session, candidate, current)
    return len(evidence)


def _persist_candidates(
    session: Session,
    *,
    article: AnalysisInput,
    candidates: Iterable[ArticleTagCandidateDTO],
    prompt_version: str,
    now: dt.datetime,
) -> dict[tuple[str, str], int]:
    if article.credentialed_source:
        return {}
    touched: list[CmsTagCandidateRecord] = _delete_candidate_evidence(
        session, article.article_id, now=now
    )
    for item in candidates:
        normalized = _normalize_label(item.label)
        candidate = session.exec(
            select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.proposed_kind == str(item.proposed_kind),
                CmsTagCandidateRecord.normalized_label == normalized,
            )
        ).first()
        if candidate is None:
            candidate = CmsTagCandidateRecord(
                label=item.label,
                normalized_label=normalized,
                proposed_kind=str(item.proposed_kind),
                first_seen_at=_iso(now),
                last_seen_at=_iso(now),
                created_at=_iso(now),
                updated_at=_iso(now),
            )
            session.add(candidate)
            session.flush()
        elif candidate.status in {"rejected", "merged"}:
            # A governance decision is authoritative. Keep the id in the
            # article snapshot so the read projection can hide/resolve it.
            touched.append(candidate)
            continue
        existing = session.get(
            CmsTagCandidateEvidenceRecord,
            (candidate.id, article.article_id),
        )
        if existing is None:
            session.add(
                CmsTagCandidateEvidenceRecord(
                    candidate_id=candidate.id or 0,
                    article_id=article.article_id,
                    source_id=article.source_id,
                    source_owner_or_domain=article.source_owner_or_domain,
                    published_date=article.publish_date,
                    confidence=item.confidence,
                    raw_label=item.label,
                    # Public evidence is still minimized and URLs are stripped.
                    context_excerpt=_URL_RE.sub("[url]", item.evidence)[:240],
                    prompt_version=prompt_version,
                    created_at=_iso(now),
                )
            )
        candidate.last_seen_at = _iso(now)
        touched.append(candidate)
    session.flush()
    for candidate in {candidate.id: candidate for candidate in touched}.values():
        _refresh_candidate_stats(session, candidate, now)
    return {
        (candidate.proposed_kind, candidate.normalized_label): int(candidate.id)
        for candidate in touched
        if candidate.id is not None
    }


def _persist_tags(
    session: Session,
    *,
    article: AnalysisInput,
    result: ArticleAnalysisResultDTO,
    prompt_version: str,
    taxonomy_version: int,
    candidate_enabled: bool,
    now: dt.datetime,
) -> tuple[str, Optional[int], list[dict[str, Any]]]:
    _delete_stale_machine_tags(session, article.article_id)
    active_by_code = {
        row.code: row
        for row in session.exec(
            select(CmsTagRecord).where(CmsTagRecord.status == "active")
        ).all()
    }
    manual_primary = session.exec(
        select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == article.article_id,
            ArticleTagAssignmentRecord.assignment_source != TagAssignmentSource.LLM.value,
            ArticleTagAssignmentRecord.is_primary.is_(True),
        )
    ).first()
    primary_id = manual_primary.tag_id if manual_primary else None
    for item in result.tag_assignments:
        tag = active_by_code.get(item.code)
        if tag is None or tag.kind != str(item.kind):
            continue
        is_primary = bool(item.is_primary and manual_primary is None)
        session.add(
            ArticleTagAssignmentRecord(
                article_id=article.article_id,
                tag_id=tag.id or 0,
                tag_kind=tag.kind,
                is_primary=is_primary,
                relevance=item.relevance,
                assignment_source=TagAssignmentSource.LLM.value,
                prompt_version=prompt_version,
                taxonomy_version=taxonomy_version,
                created_at=_iso(now),
                updated_at=_iso(now),
            )
        )
        if is_primary:
            primary_id = tag.id
    candidate_ids: dict[tuple[str, str], int] = {}
    if candidate_enabled:
        candidate_ids = _persist_candidates(
            session,
            article=article,
            candidates=result.tag_candidates,
            prompt_version=prompt_version,
            now=now,
        )
    else:
        _delete_candidate_evidence(session, article.article_id, now=now)
    return (
        TaggingStatus.SUCCEEDED.value,
        primary_id,
        extracted_tag_snapshot(result.tag_candidates, candidate_ids),
    )


def _mark_failure(
    engine: Engine,
    task: ClaimedAnalysisTask,
    *,
    status: str,
    error: BaseException | str,
    now: dt.datetime,
    max_attempts: int,
) -> ProcessResult:
    with Session(engine) as session:
        record = session.get(ArticleAnalysisRecord, task.article_id)
        attempt = _attempt_for(session, task)
        if record is None or record.lease_owner != task.lease_token:
            return ProcessResult(task.article_id, "superseded", TaggingStatus.PENDING.value)
        article = session.get(ArticleRecord, task.article_id)
        source = session.get(SourceConfigRecord, article.source_id) if article else None
        if article is not None and not sync_consumer_policy.local_source_operation_allowed(
            session, article.source_id, operation="analysis"
        ):
            revoke_queued_analysis(
                session,
                task.article_id,
                reason="v2_consumer_quiesced",
                now=now,
            )
            session.commit()
            return ProcessResult(
                task.article_id,
                "superseded",
                record.tagging_status,
            )
        if article is not None and _is_credentialed_source(source):
            error_type = type(error).__name__ if isinstance(error, BaseException) else "AnalysisError"
            safe_error = f"{error_type}: private source analysis {status}"
        else:
            safe_error = sanitize_error(error)
        _finish_or_retry(
            record,
            terminal_status=status,
            error=safe_error,
            now=now,
            max_attempts=max_attempts,
        )
        if attempt is not None:
            attempt.status = (
                AnalysisAttemptStatus.TIMEOUT.value
                if status == AnalysisStatus.TIMEOUT.value
                else AnalysisAttemptStatus.FAILED.value
            )
            attempt.ended_at = _iso(now)
            started = _parse_datetime(attempt.started_at)
            attempt.duration_ms = max(0, int((now - started).total_seconds() * 1000)) if started else None
            attempt.error = safe_error
            session.add(attempt)
        session.add(record)
        session.commit()
    logger.warning("Article analysis %s (article_id=%s)", status, task.article_id)
    return ProcessResult(task.article_id, status, TaggingStatus.PENDING.value)


async def process_claimed_analysis(
    engine: Engine,
    task: ClaimedAnalysisTask,
    *,
    llm_config: LLMConfig,
    analyzer: Analyzer = analyze_article_with_llm,
    timeout_seconds: int = ANALYSIS_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    candidate_enabled: bool = False,
    now_fn: Callable[[], dt.datetime] = _utc_now,
) -> ProcessResult:
    """Run one lease without holding a DB connection during the LLM request."""

    with Session(engine) as session:
        record = session.get(ArticleAnalysisRecord, task.article_id)
        if (
            record is None
            or record.status != AnalysisStatus.RUNNING.value
            or record.lease_owner != task.lease_token
            or record.content_hash != task.content_hash
        ):
            return ProcessResult(task.article_id, "superseded", TaggingStatus.PENDING.value)
        article_record = session.get(ArticleRecord, task.article_id)
        if (
            article_record is None
            or (article_record.analysis_authority_id or "").strip()
            or not sync_consumer_policy.local_source_operation_allowed(
                session, article_record.source_id, operation="analysis"
            )
        ):
            if article_record is not None:
                revoke_queued_analysis(
                    session,
                    task.article_id,
                    reason="v2_consumer_quiesced",
                    now=_as_utc(now_fn()),
                )
                session.commit()
            return ProcessResult(task.article_id, "superseded", TaggingStatus.PENDING.value)
        try:
            article_input, active_tags = _input_for(session, task.article_id)
        except LookupError as exc:
            return _mark_failure(
                engine,
                task,
                status=AnalysisStatus.FAILED.value,
                error=exc,
                now=_as_utc(now_fn()),
                max_attempts=max_attempts,
            )
        effective_model = llm_config.for_aux().model
        analysis_input_hash = compute_analysis_input_hash(article_input, active_tags)
        taxonomy_version = _active_taxonomy_version(session)
        record.model_name = effective_model
        record.taxonomy_version = taxonomy_version
        attempt = _attempt_for(session, task)
        if attempt is not None:
            attempt.model_name = effective_model
            attempt.taxonomy_version = taxonomy_version
            session.add(attempt)
        session.add(record)
        session.commit()
        if not source_allows_analysis(session, article_input.source_id):
            source = session.get(SourceConfigRecord, article_input.source_id)
            if _is_credentialed_source(source):
                purge_source_candidate_evidence(
                    session,
                    article_input.source_id,
                    now=_as_utc(now_fn()),
                )
            revoke_queued_analysis(
                session,
                task.article_id,
                reason="source_ai_analysis_disabled",
                now=_as_utc(now_fn()),
            )
            session.commit()
            session.refresh(record)
            return ProcessResult(task.article_id, record.status, record.tagging_status)

    effective_timeout = min(
        float(ANALYSIS_LEASE_SECONDS),
        max(0.001, float(timeout_seconds)),
    )
    try:
        call = analyzer(article_input, active_tags, llm_config)
        raw = await asyncio.wait_for(
            call if inspect.isawaitable(call) else _immediate(call),
            timeout=effective_timeout,
        )
        validated = validate_analysis_payload(raw, active_tags=active_tags)
    except TimeoutError:
        return _mark_failure(
            engine,
            task,
            status=AnalysisStatus.TIMEOUT.value,
            error=f"analysis exceeded {effective_timeout:g} seconds",
            now=_as_utc(now_fn()),
            max_attempts=max_attempts,
        )
    except Exception as exc:  # noqa: BLE001 - persisted retry state is the boundary
        return _mark_failure(
            engine,
            task,
            status=AnalysisStatus.FAILED.value,
            error=exc,
            now=_as_utc(now_fn()),
            max_attempts=max_attempts,
        )

    ended = _as_utc(now_fn())
    with Session(engine) as session:
        record = session.get(ArticleAnalysisRecord, task.article_id)
        article = session.get(ArticleRecord, task.article_id)
        attempt = _attempt_for(session, task)
        local_analysis_allowed = bool(
            article is not None
            and sync_consumer_policy.local_source_operation_allowed(
                session, article.source_id, operation="analysis"
            )
        )
        if (
            record is None
            or article is None
            or record.status != AnalysisStatus.RUNNING.value
            or record.lease_owner != task.lease_token
            or record.content_hash != task.content_hash
            or compute_content_hash(article) != task.content_hash
            or (article.analysis_authority_id or "").strip()
            or not local_analysis_allowed
        ):
            if (
                article is not None
                and not (article.analysis_authority_id or "").strip()
                and not local_analysis_allowed
            ):
                revoke_queued_analysis(
                    session,
                    task.article_id,
                    reason="v2_consumer_quiesced",
                    now=ended,
                )
                session.commit()
            return ProcessResult(task.article_id, "superseded", TaggingStatus.PENDING.value)
        if (
            article_input.content_type == "podcast_episode"
            and record.analysis_basis
            in {"publisher_transcript", "asr_transcript"}
            and article_input.analysis_basis == "podcast_show_notes"
        ):
            revoke_queued_analysis(
                session,
                task.article_id,
                reason="transcript analysis superseded show notes",
                now=ended,
            )
            session.commit()
            return ProcessResult(
                task.article_id, "superseded", record.tagging_status
            )
        if (
            article_input.content_type == "podcast_episode"
            and tuple(article_input.people) != _podcast_people(article)
        ):
            # Podcast persons live in publisher metadata, outside content_hash.
            # Close this lease, then rebuild a pending task from the newest
            # metadata so the stale LLM response can never become authoritative.
            revoke_queued_analysis(
                session,
                task.article_id,
                reason="podcast people changed during analysis",
                now=ended,
            )
            queue_article_analysis(session, task.article_id, enabled=True, now=ended)
            session.commit()
            return ProcessResult(
                task.article_id, "superseded", TaggingStatus.PENDING.value
            )
        if not source_allows_analysis(session, article.source_id):
            source = session.get(SourceConfigRecord, article.source_id)
            if _is_credentialed_source(source):
                purge_source_candidate_evidence(
                    session,
                    article.source_id,
                    now=ended,
                )
            revoke_queued_analysis(
                session,
                task.article_id,
                reason="source_ai_analysis_disabled",
                now=ended,
            )
            session.commit()
            session.refresh(record)
            return ProcessResult(task.article_id, record.status, record.tagging_status)

        result = validated.result
        prompt_version, scoring_version = analysis_contract_versions(article.content_type)
        record.status = AnalysisStatus.SUCCEEDED.value
        record.quality_score = result.quality_score
        if article_input.content_type == "podcast_episode":
            record.podcast_initial_score = result.quality_score
        record.dimension_scores_json = json.dumps(
            {
                "schema_version": "podcast-factors-v1",
                "factors": validated.podcast_factors,
            }
            if article_input.content_type == "podcast_episode"
            else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        record.score_reason = result.score_reason
        record.summary = result.summary
        record.content_genre = str(result.content_genre)
        record.content_features_json = json.dumps(result.content_features, ensure_ascii=False)
        record.entities_json = json.dumps(result.entities, ensure_ascii=False)
        # Persist free labels for every source, including private RSS. They are
        # article-scoped display metadata and never enter the public Candidate pool.
        record.display_tags_json = json.dumps(
            extracted_tag_snapshot(result.tag_candidates), ensure_ascii=False
        )
        record.model_name = llm_config.for_aux().model
        record.analysis_basis = article_input.analysis_basis
        record.analysis_input_hash = analysis_input_hash
        record.transcript_artifact_id = article_input.transcript_artifact_id
        record.analysis_diagnostics_json = json.dumps(
            {
                "people": list(article_input.people),
                "topic_heat": article_input.topic_heat,
                "podcast_factors": validated.podcast_factors,
            }
            if article_input.content_type == "podcast_episode"
            else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        record.prompt_version = prompt_version
        record.scoring_version = scoring_version
        record.taxonomy_version = _active_taxonomy_version(session)
        record.analyzed_at = _iso(ended)
        record.started_at = None
        record.next_attempt_at = None
        record.lease_owner = None
        record.lease_expires_at = None
        record.last_error = None
        record.updated_at = _iso(ended)

        tag_status = TaggingStatus.PARTIAL.value if validated.warnings else TaggingStatus.SUCCEEDED.value
        try:
            with session.begin_nested():
                persisted_status, primary_id, display_tags = _persist_tags(
                    session,
                    article=article_input,
                    result=result,
                    prompt_version=prompt_version,
                    taxonomy_version=record.taxonomy_version,
                    candidate_enabled=candidate_enabled,
                    now=ended,
                )
                session.flush()
            record.primary_tag_id = primary_id
            record.display_tags_json = json.dumps(display_tags, ensure_ascii=False)
            if tag_status == TaggingStatus.SUCCEEDED.value:
                tag_status = persisted_status
        except Exception as exc:  # noqa: BLE001 - base analysis remains authoritative
            tag_status = TaggingStatus.FAILED.value
            logger.warning(
                "Article tag persistence failed (article_id=%s): %s",
                task.article_id,
                sanitize_error(exc),
            )
        record.tagging_status = tag_status
        record.tagged_at = _iso(ended) if tag_status in {
            TaggingStatus.SUCCEEDED.value,
            TaggingStatus.PARTIAL.value,
        } else None

        if attempt is not None:
            attempt.status = AnalysisAttemptStatus.SUCCEEDED.value
            attempt.model_name = record.model_name
            attempt.taxonomy_version = record.taxonomy_version
            attempt.ended_at = _iso(ended)
            started = _parse_datetime(attempt.started_at)
            attempt.duration_ms = max(0, int((ended - started).total_seconds() * 1000)) if started else None
            attempt.result_summary_json = json.dumps(
                {
                    "quality_score": result.quality_score,
                    "content_genre": str(result.content_genre),
                    "tagging_status": tag_status,
                    "tag_count": len(result.tag_assignments),
                    "candidate_count": 0 if article_input.credentialed_source else len(result.tag_candidates),
                    "validation_warnings": list(validated.warnings),
                },
                ensure_ascii=False,
            )
            session.add(attempt)
        session.add(record)
        session.commit()
    return ProcessResult(task.article_id, AnalysisStatus.SUCCEEDED.value, tag_status)


async def _immediate(value: Any) -> Any:
    return value


async def run_analysis_cycle(
    engine: Engine,
    *,
    worker_id: str,
    llm_config: LLMConfig,
    analyzer: Analyzer = analyze_article_with_llm,
    enabled: Optional[bool] = None,
    candidate_enabled: Optional[bool] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES_PER_CYCLE,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    now_fn: Callable[[], dt.datetime] = _utc_now,
) -> list[ProcessResult]:
    """Scheduler integration point for the all-in-one V1 runtime.

    Live/missing work is claimed first. Historical full-analysis items are only
    dispatched into remaining batch capacity, after which the same newest-first
    claim path and lease/retry machinery processes them.  A scheduler tick drains
    up to ``max_batches`` immediately instead of sleeping a minute after every
    map-sized claim; each batch remains bounded by ``map_concurrency`` so all
    leases start promptly.
    """

    now = _as_utc(now_fn())
    backfill_job_id: Optional[int] = None
    backfill_owner = f"{worker_id}:full-analysis"
    with Session(engine) as session:
        effective_enabled = (
            read_feature_flag(session, ARTICLE_ANALYSIS_ENABLED_KEY, default=False)
            if enabled is None
            else enabled
        )
        if not effective_enabled:
            return []
        if max_batches <= 0:
            return []
        effective_candidates = (
            read_feature_flag(session, TAXONOMY_CANDIDATE_ENABLED_KEY, default=False)
            if candidate_enabled is None
            else candidate_enabled
        )
        recover_expired_leases(session, now=now)
        scan_analysis_backfill(session, enabled=True, now=now, limit=scan_limit)
        # Local import avoids a module cycle: the backfill coordinator reuses
        # queue_article_analysis rather than owning a second execution queue.
        from services import analysis_backfill as backfill_service

        backfill_job = backfill_service.claim_full_analysis_backfill(
            session,
            lease_owner=backfill_owner,
            now=now,
        )
        if backfill_job is not None:
            backfill_job = backfill_service.reconcile_full_analysis_backfill(
                session,
                backfill_job,
                lease_owner=backfill_owner,
                now=now,
            )
            backfill_job_id = int(backfill_job.id)
    # Every leased task starts promptly; otherwise a sequential batch could let
    # later leases expire while the first LLM call consumes its five minutes.
    # Only the short claim/finalize sections touch SQLite.
    concurrency = max(
        1,
        min(batch_size, llm_config.map_concurrency, MAX_ANALYSIS_CONCURRENCY),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _guarded(task: ClaimedAnalysisTask) -> ProcessResult:
        async with semaphore:
            return await process_claimed_analysis(
                engine,
                task,
                llm_config=llm_config,
                analyzer=analyzer,
                candidate_enabled=effective_candidates,
                now_fn=now_fn,
            )

    results: list[ProcessResult] = []
    for _batch_number in range(max_batches):
        batch_now = _as_utc(now_fn())
        with Session(engine) as session:
            current_backfill = (
                session.get(TagRetagJobRecord, backfill_job_id)
                if backfill_job_id is not None
                else None
            )
            if (
                current_backfill is not None
                and current_backfill.status == "running"
                and current_backfill.lease_owner == backfill_owner
            ):
                current_backfill = backfill_service.reconcile_full_analysis_backfill(
                    session,
                    current_backfill,
                    lease_owner=backfill_owner,
                    now=batch_now,
                )

            tasks = claim_analysis_tasks(
                session,
                worker_id=worker_id,
                limit=concurrency,
                now=batch_now,
            )
            remaining = max(0, concurrency - len(tasks))
            if (
                current_backfill is not None
                and current_backfill.status == "running"
                and remaining
            ):
                dispatched = backfill_service.dispatch_full_analysis_backfill(
                    session,
                    current_backfill,
                    lease_owner=backfill_owner,
                    limit=remaining,
                    now=batch_now,
                )
                if dispatched:
                    tasks.extend(
                        claim_analysis_tasks(
                            session,
                            worker_id=worker_id,
                            limit=remaining,
                            now=batch_now,
                        )
                    )

        if not tasks:
            break
        gathered = await asyncio.gather(
            *(_guarded(task) for task in tasks),
            return_exceptions=True,
        )
        for task, outcome in zip(tasks, gathered):
            if isinstance(outcome, BaseException):
                # One unexpected worker failure must not cancel siblings or
                # strand its lease. Reuse the normal retry transition.
                results.append(_mark_failure(
                    engine,
                    task,
                    status=AnalysisStatus.FAILED.value,
                    error=outcome,
                    now=_as_utc(now_fn()),
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                ))
            else:
                results.append(outcome)

    if backfill_job_id is not None:
        try:
            with Session(engine) as session:
                current = session.get(TagRetagJobRecord, backfill_job_id)
                if (
                    current is not None
                    and current.status == "running"
                    and current.lease_owner == backfill_owner
                ):
                    backfill_service.reconcile_full_analysis_backfill(
                        session,
                        current,
                        lease_owner=backfill_owner,
                        now=_as_utc(now_fn()),
                    )
        except Exception as exc:  # noqa: BLE001 - analysis results remain authoritative
            logger.warning("Failed to reconcile full-analysis backfill job %s: %s", backfill_job_id, exc)
    return results


def get_article_analysis(session: Session, article_id: str) -> Optional[dict[str, Any]]:
    """Read the current successful asset in a router/reader-AI friendly shape."""

    record = session.get(ArticleAnalysisRecord, article_id)
    if not has_authoritative_analysis(record):
        return None
    return {
        "article_id": record.article_id,
        "quality_score": record.quality_score,
        "score_reason": record.score_reason,
        "summary": record.summary,
        "content_genre": record.content_genre,
        "primary_tag_id": record.primary_tag_id,
        "analysis_basis": record.analysis_basis,
        "analysis_input_hash": record.analysis_input_hash,
        "transcript_artifact_id": record.transcript_artifact_id,
        "prompt_version": record.prompt_version,
        "scoring_version": record.scoring_version,
        "taxonomy_version": record.taxonomy_version,
        "analyzed_at": record.analyzed_at,
    }


def resolve_summary_with_legacy_fallback(session: Session, article: ArticleRecord) -> str:
    """Prefer the unified summary; retain ``extensions_json.summary_zh`` fallback."""

    analysis = get_article_analysis(session, article.id)
    if analysis and analysis["summary"]:
        return str(analysis["summary"])
    try:
        extensions = json.loads(article.extensions_json or "{}")
    except (TypeError, json.JSONDecodeError):
        extensions = {}
    if isinstance(extensions, dict):
        return str(extensions.get("summary_zh") or extensions.get("summary") or "")
    return ""
