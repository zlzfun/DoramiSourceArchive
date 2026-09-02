"""Governed, resumable historical ``full_analysis`` backfills.

The existing article-analysis row remains the single execution queue.  This
module adds an auditable job header plus per-article snapshots so a deployment
restart can resume dispatch without duplicating work.  Backfill articles are
only dispatched into spare analysis capacity; ``claim_analysis_tasks`` keeps
its newest-first ordering, so live arrivals always win.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlmodel import Session, select

from llm.article_analysis_prompt import (
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
    MAX_ANALYSIS_BODY_CHARS,
)
from models.analysis_contracts import AnalysisOperation, AnalysisStatus, TaggingStatus
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    SourceConfigRecord,
    TagRetagJobItemRecord,
    TagRetagJobRecord,
)
from services.article_analysis import (
    DEFAULT_MAX_ATTEMPTS,
    compute_content_hash,
    queue_article_analysis,
    sanitize_error,
)
from services.taxonomy import current_taxonomy_version, now_iso


FULL_ANALYSIS_CONFIRMATION = "RUN FULL ANALYSIS"
FULL_ANALYSIS_ACTIVE_STATUSES = ("queued", "running", "paused")
FULL_ANALYSIS_SELECTIONS = ("all", "missing_or_outdated")
DEFAULT_DISPATCH_LIMIT = 8
LEASE_SECONDS = 300
SHANGHAI = ZoneInfo("Asia/Shanghai")


class AnalysisBackfillError(ValueError):
    """A full-analysis backfill request violates a governance invariant."""


@dataclass(frozen=True)
class BackfillTarget:
    article: ArticleRecord
    content_hash: str


def _utc(now: Optional[dt.datetime] = None) -> dt.datetime:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _scope_bounds(
    *,
    days: Optional[int],
    now: Optional[dt.datetime],
) -> tuple[Optional[str], str]:
    current = _utc(now)
    since = now_iso(current - dt.timedelta(days=days)) if days is not None else None
    return since, now_iso(current)


def _article_time(value: str) -> Optional[dt.datetime]:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    # Article ingestion historically stores local wall-clock timestamps without
    # an offset, while newer paths may preserve an explicit offset. Normalize
    # both before applying a backfill window; lexical SQL comparison would drop
    # same-day local rows when the coordinator uses UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(dt.timezone.utc)


def _eligible_rows(
    session: Session,
    *,
    days: Optional[int],
    selection: str,
    source_ids: Sequence[str],
    taxonomy_version: int,
    now: Optional[dt.datetime],
) -> tuple[list[BackfillTarget], Optional[str], str]:
    if days is not None and not 1 <= days <= 3650:
        raise AnalysisBackfillError("days must be between 1 and 3650 or null for all history")
    if selection not in FULL_ANALYSIS_SELECTIONS:
        raise AnalysisBackfillError("unsupported full-analysis selection")

    current = _utc(now)
    since, until = _scope_bounds(days=days, now=current)
    since_time = current - dt.timedelta(days=days) if days is not None else None
    statement = (
        select(ArticleRecord, ArticleAnalysisRecord)
        .outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .outerjoin(
            SourceConfigRecord,
            SourceConfigRecord.source_id == ArticleRecord.source_id,
        )
        .where(
            ArticleRecord.has_content.is_(True),
            ArticleRecord.content.is_not(None),
            ArticleRecord.content != "",
            or_(
                SourceConfigRecord.source_id.is_(None),
                SourceConfigRecord.ai_analysis_enabled.is_(True),
            ),
        )
        .order_by(ArticleRecord.fetched_date.desc(), ArticleRecord.id.desc())
    )
    normalized_sources = tuple(dict.fromkeys(item.strip() for item in source_ids if item.strip()))
    if normalized_sources:
        statement = statement.where(ArticleRecord.source_id.in_(normalized_sources))

    targets: list[BackfillTarget] = []
    for article, analysis in session.exec(statement).all():
        fetched_at = _article_time(article.fetched_date)
        if fetched_at is None or fetched_at > current or (since_time and fetched_at < since_time):
            continue
        content_hash = compute_content_hash(article)
        is_current = bool(
            analysis is not None
            and analysis.status == AnalysisStatus.SUCCEEDED.value
            and analysis.tagging_status != TaggingStatus.FAILED.value
            and analysis.content_hash == content_hash
            and analysis.prompt_version == ARTICLE_ANALYSIS_PROMPT_VERSION
            and analysis.scoring_version == ARTICLE_ANALYSIS_SCORING_VERSION
            and int(analysis.taxonomy_version) == int(taxonomy_version)
        )
        if selection == "missing_or_outdated" and is_current:
            continue
        targets.append(BackfillTarget(article=article, content_hash=content_hash))
    return targets, since, until


def estimate_full_analysis_backfill(
    session: Session,
    *,
    days: Optional[int],
    selection: str,
    source_ids: Sequence[str] = (),
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    taxonomy_version = current_taxonomy_version(session)
    targets, since, until = _eligible_rows(
        session,
        days=days,
        selection=selection,
        source_ids=source_ids,
        taxonomy_version=taxonomy_version,
        now=now,
    )
    bounded_chars = sum(
        min(MAX_ANALYSIS_BODY_CHARS, len(item.article.content or ""))
        + len(item.article.title or "")
        for item in targets
    )
    # This is intentionally a capacity estimate rather than a money promise:
    # configured OpenAI-compatible providers do not expose a stable price table.
    estimated_input_tokens = (bounded_chars + 3) // 4
    return {
        "selection": selection,
        "days": days,
        "since": since,
        "until": until,
        "source_count": len({item.article.source_id for item in targets}),
        "article_count": len(targets),
        "estimated_initial_llm_calls": len(targets),
        "estimated_max_llm_calls": len(targets) * DEFAULT_MAX_ATTEMPTS,
        "estimated_article_input_tokens": estimated_input_tokens,
        "taxonomy_version": taxonomy_version,
        "prompt_version": ARTICLE_ANALYSIS_PROMPT_VERSION,
        "scoring_version": ARTICLE_ANALYSIS_SCORING_VERSION,
        "ready": taxonomy_version > 0 and bool(targets),
        "blockers": [
            message
            for condition, message in (
                (taxonomy_version <= 0, "必须先发布一个 active taxonomy 版本"),
                (not targets, "当前范围没有需要执行 full_analysis 的文章"),
            )
            if condition
        ],
    }


def _active_full_job(session: Session) -> Optional[TagRetagJobRecord]:
    return session.exec(
        select(TagRetagJobRecord)
        .where(
            TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value,
            TagRetagJobRecord.status.in_(FULL_ANALYSIS_ACTIVE_STATUSES),
        )
        .order_by(TagRetagJobRecord.id)
    ).first()


def create_full_analysis_backfill(
    session: Session,
    *,
    days: Optional[int],
    selection: str,
    source_ids: Sequence[str] = (),
    actor_id: str,
    confirmation: str,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if confirmation != FULL_ANALYSIS_CONFIRMATION:
        raise AnalysisBackfillError("full-analysis confirmation text does not match")
    if _active_full_job(session) is not None:
        raise AnalysisBackfillError("another full-analysis backfill is unfinished")

    taxonomy_version = current_taxonomy_version(session)
    if taxonomy_version <= 0:
        raise AnalysisBackfillError("an active taxonomy version is required")
    targets, since, until = _eligible_rows(
        session,
        days=days,
        selection=selection,
        source_ids=source_ids,
        taxonomy_version=taxonomy_version,
        now=now,
    )
    if not targets:
        raise AnalysisBackfillError("no eligible articles in the selected scope")

    stamp = now_iso(_utc(now))
    scope = {
        "selection": selection,
        "days": days,
        "since": since,
        "until": until,
        "source_ids": list(dict.fromkeys(item.strip() for item in source_ids if item.strip())),
        "target_source_count": len({item.article.source_id for item in targets}),
        "created_by": actor_id.strip() or "admin",
        "target_prompt_version": ARTICLE_ANALYSIS_PROMPT_VERSION,
        "target_scoring_version": ARTICLE_ANALYSIS_SCORING_VERSION,
        "target_taxonomy_version": taxonomy_version,
    }
    job = TagRetagJobRecord(
        taxonomy_version=taxonomy_version,
        operation=AnalysisOperation.FULL_ANALYSIS.value,
        scope_json=json.dumps(scope, ensure_ascii=False, sort_keys=True),
        status="queued",
        affected_count=len(targets),
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(job)
    session.flush()
    session.add_all(
        [
            TagRetagJobItemRecord(
                job_id=int(job.id),
                article_id=item.article.id,
                article_id_snapshot=item.article.id,
                target_content_hash=item.content_hash,
                status="pending",
                created_at=stamp,
                updated_at=stamp,
            )
            for item in targets
        ]
    )
    session.commit()
    session.refresh(job)
    return job


def _scope(job: TagRetagJobRecord) -> dict[str, Any]:
    try:
        value = json.loads(job.scope_json or "{}")
    except ValueError as exc:
        raise AnalysisBackfillError("full-analysis scope is invalid JSON") from exc
    if not isinstance(value, dict):
        raise AnalysisBackfillError("full-analysis scope must be an object")
    return value


def claim_full_analysis_backfill(
    session: Session,
    *,
    lease_owner: str,
    lease_seconds: int = LEASE_SECONDS,
    now: Optional[dt.datetime] = None,
) -> Optional[TagRetagJobRecord]:
    owner = lease_owner.strip()
    if not owner:
        raise AnalysisBackfillError("lease_owner is required")
    current = _utc(now)
    stamp = now_iso(current)
    job = session.exec(
        select(TagRetagJobRecord)
        .where(
            TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value,
            or_(
                TagRetagJobRecord.status == "queued",
                (TagRetagJobRecord.status == "running")
                & (TagRetagJobRecord.lease_owner == owner),
                (TagRetagJobRecord.status == "running")
                & (TagRetagJobRecord.lease_expires_at < stamp),
            ),
        )
        .order_by(TagRetagJobRecord.id)
    ).first()
    if job is None:
        return None
    job.status = "running"
    job.lease_owner = owner
    job.lease_expires_at = now_iso(current + dt.timedelta(seconds=max(1, lease_seconds)))
    job.updated_at = stamp
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _item_counts(session: Session, job_id: int) -> Counter[str]:
    return Counter(
        {
            status: int(count)
            for status, count in session.exec(
                select(TagRetagJobItemRecord.status, func.count(TagRetagJobItemRecord.id))
                .where(TagRetagJobItemRecord.job_id == job_id)
                .group_by(TagRetagJobItemRecord.status)
            ).all()
        }
    )


def _target_versions_match(job: TagRetagJobRecord, scope: dict[str, Any]) -> bool:
    return bool(
        int(scope.get("target_taxonomy_version") or 0) == int(job.taxonomy_version)
        and scope.get("target_prompt_version") == ARTICLE_ANALYSIS_PROMPT_VERSION
        and scope.get("target_scoring_version") == ARTICLE_ANALYSIS_SCORING_VERSION
    )


def reconcile_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    lease_owner: str,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if job.operation != AnalysisOperation.FULL_ANALYSIS.value:
        raise AnalysisBackfillError("job is not a full-analysis backfill")
    if job.status != "running" or job.lease_owner != lease_owner:
        raise AnalysisBackfillError("full-analysis job is not leased by this worker")

    current = _utc(now)
    stamp = now_iso(current)
    scope = _scope(job)
    queued = list(
        session.exec(
            select(TagRetagJobItemRecord)
            .where(
                TagRetagJobItemRecord.job_id == job.id,
                TagRetagJobItemRecord.status == "queued",
            )
            .order_by(TagRetagJobItemRecord.id)
        ).all()
    )
    for item in queued:
        if item.article_id is None:
            item.status = "skipped"
            item.last_error = "article_deleted"
            item.completed_at = stamp
            item.updated_at = stamp
            session.add(item)
            continue
        article = session.get(ArticleRecord, item.article_id)
        analysis = session.get(ArticleAnalysisRecord, item.article_id)
        if article is None:
            item.status = "skipped"
            item.last_error = "article_deleted"
        elif analysis is None:
            # A crash between item dispatch and queue commit is safe to replay.
            item.status = "pending"
            item.last_error = "analysis_queue_missing"
        else:
            current_hash = compute_content_hash(article)
            current_result = bool(
                analysis.status == AnalysisStatus.SUCCEEDED.value
                and analysis.tagging_status != TaggingStatus.FAILED.value
                and analysis.content_hash == current_hash
                and analysis.prompt_version == scope.get("target_prompt_version")
                and analysis.scoring_version == scope.get("target_scoring_version")
                and int(analysis.taxonomy_version) == int(job.taxonomy_version)
            )
            terminal_failure = bool(
                (
                    analysis.status in {AnalysisStatus.FAILED.value, AnalysisStatus.TIMEOUT.value}
                    and analysis.next_attempt_at is None
                    and analysis.attempt_count >= DEFAULT_MAX_ATTEMPTS
                )
                or (
                    analysis.status == AnalysisStatus.SUCCEEDED.value
                    and analysis.tagging_status == TaggingStatus.FAILED.value
                )
            )
            if current_result:
                item.status = "succeeded"
                item.target_content_hash = current_hash
                item.last_error = None
            elif analysis.status == AnalysisStatus.SKIPPED.value:
                item.status = "skipped"
                item.last_error = analysis.last_error or "analysis_skipped"
            elif terminal_failure:
                item.status = "failed"
                item.last_error = sanitize_error(analysis.last_error or "analysis_failed")
            else:
                continue
        item.completed_at = stamp if item.status in {"succeeded", "failed", "skipped"} else None
        item.updated_at = stamp
        session.add(item)

    session.flush()
    counts = _item_counts(session, int(job.id))
    job.succeeded_count = counts["succeeded"]
    job.failed_count = counts["failed"]
    terminal = counts["succeeded"] + counts["failed"] + counts["skipped"]
    if terminal >= job.affected_count:
        job.status = "partial_failed" if counts["failed"] else "succeeded"
        job.lease_owner = None
        job.lease_expires_at = None
    elif not _target_versions_match(job, scope) or current_taxonomy_version(session) != job.taxonomy_version:
        job.status = "failed"
        job.last_error = "runtime taxonomy/prompt/scoring version changed; create a new backfill"
        job.lease_owner = None
        job.lease_expires_at = None
    else:
        job.lease_expires_at = now_iso(current + dt.timedelta(seconds=LEASE_SECONDS))
    job.updated_at = stamp
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def dispatch_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    lease_owner: str,
    limit: int = DEFAULT_DISPATCH_LIMIT,
    now: Optional[dt.datetime] = None,
) -> int:
    if job.status != "running" or job.lease_owner != lease_owner:
        return 0
    current = _utc(now)
    stamp = now_iso(current)
    rows = list(
        session.exec(
            select(TagRetagJobItemRecord)
            .outerjoin(ArticleRecord, ArticleRecord.id == TagRetagJobItemRecord.article_id)
            .where(
                TagRetagJobItemRecord.job_id == job.id,
                TagRetagJobItemRecord.status == "pending",
            )
            .order_by(ArticleRecord.fetched_date.desc(), TagRetagJobItemRecord.id)
            .limit(max(1, limit * 3))
        ).all()
    )
    dispatched = 0
    for item in rows:
        if dispatched >= max(1, limit):
            break
        if item.article_id is None:
            item.status = "skipped"
            item.last_error = "article_deleted"
            item.completed_at = stamp
            item.updated_at = stamp
            session.add(item)
            continue
        article = session.get(ArticleRecord, item.article_id)
        if article is None:
            item.status = "skipped"
            item.last_error = "article_deleted"
            item.completed_at = stamp
            item.updated_at = stamp
            session.add(item)
            continue
        outcome = queue_article_analysis(
            session,
            article.id,
            enabled=True,
            force=True,
            now=current,
        )
        if outcome == "busy":
            continue
        if outcome in {"ineligible", "skipped", "unchanged"}:
            item.status = "skipped"
            item.last_error = f"queue_{outcome}"
            item.completed_at = stamp
        else:
            item.status = "queued"
            item.target_content_hash = compute_content_hash(article)
            item.last_error = None
            item.queued_at = stamp
            dispatched += 1
        item.updated_at = stamp
        session.add(item)
    job.updated_at = stamp
    session.add(job)
    session.commit()
    return dispatched


def _set_nonterminal_items_skipped(
    session: Session,
    job: TagRetagJobRecord,
    *,
    reason: str,
    now: Optional[dt.datetime],
) -> None:
    stamp = now_iso(_utc(now))
    for item in session.exec(
        select(TagRetagJobItemRecord).where(
            TagRetagJobItemRecord.job_id == job.id,
            TagRetagJobItemRecord.status.in_(("pending", "queued")),
        )
    ).all():
        item.status = "skipped"
        item.last_error = reason
        item.completed_at = stamp
        item.updated_at = stamp
        session.add(item)


def pause_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if job.operation != AnalysisOperation.FULL_ANALYSIS.value or job.status not in {"queued", "running"}:
        raise AnalysisBackfillError("only an active full-analysis backfill can be paused")
    job.status = "paused"
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = now_iso(_utc(now))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def resume_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if job.operation != AnalysisOperation.FULL_ANALYSIS.value or job.status != "paused":
        raise AnalysisBackfillError("only a paused full-analysis backfill can be resumed")
    job.status = "queued"
    job.updated_at = now_iso(_utc(now))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def cancel_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if job.operation != AnalysisOperation.FULL_ANALYSIS.value or job.status not in FULL_ANALYSIS_ACTIVE_STATUSES:
        raise AnalysisBackfillError("only an unfinished full-analysis backfill can be cancelled")
    _set_nonterminal_items_skipped(session, job, reason="job_cancelled", now=now)
    job.status = "cancelled"
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = now_iso(_utc(now))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def retry_failed_full_analysis_items(
    session: Session,
    job: TagRetagJobRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if job.operation != AnalysisOperation.FULL_ANALYSIS.value or job.status != "partial_failed":
        raise AnalysisBackfillError("only a partial-failed full-analysis backfill can retry failed items")
    stamp = now_iso(_utc(now))
    failed = list(
        session.exec(
            select(TagRetagJobItemRecord).where(
                TagRetagJobItemRecord.job_id == job.id,
                TagRetagJobItemRecord.status == "failed",
            )
        ).all()
    )
    if not failed:
        raise AnalysisBackfillError("full-analysis backfill has no failed items")
    for item in failed:
        item.status = "pending"
        item.last_error = None
        item.queued_at = None
        item.completed_at = None
        item.updated_at = stamp
        session.add(item)
    job.status = "queued"
    job.failed_count = 0
    job.last_error = None
    job.updated_at = stamp
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def serialize_full_analysis_backfill(
    session: Session,
    job: TagRetagJobRecord,
    *,
    include_failures: bool = False,
) -> dict[str, Any]:
    scope = _scope(job)
    counts = _item_counts(session, int(job.id))
    total = int(job.affected_count)
    finished = counts["succeeded"] + counts["failed"] + counts["skipped"]
    result = {
        "job_id": int(job.id),
        "operation": job.operation,
        "status": job.status,
        "selection": scope.get("selection"),
        "days": scope.get("days"),
        "since": scope.get("since"),
        "until": scope.get("until"),
        "source_count": int(scope.get("target_source_count") or 0),
        "created_by": scope.get("created_by") or "",
        "target_prompt_version": scope.get("target_prompt_version") or "",
        "target_scoring_version": scope.get("target_scoring_version") or "",
        "target_taxonomy_version": int(job.taxonomy_version),
        "counts": {
            "total": total,
            "pending": counts["pending"],
            "queued": counts["queued"],
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "finished": finished,
        },
        "progress": finished / total if total else 1.0,
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if include_failures:
        result["failed_items"] = [
            {
                "article_id": item.article_id_snapshot,
                "error": item.last_error or "analysis_failed",
            }
            for item in session.exec(
                select(TagRetagJobItemRecord)
                .where(
                    TagRetagJobItemRecord.job_id == job.id,
                    TagRetagJobItemRecord.status == "failed",
                )
                .order_by(TagRetagJobItemRecord.id)
                .limit(50)
            ).all()
        ]
    return result


def list_full_analysis_backfills(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    jobs = session.exec(
        select(TagRetagJobRecord)
        .where(TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value)
        .order_by(TagRetagJobRecord.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [serialize_full_analysis_backfill(session, job) for job in jobs]


def get_full_analysis_backfill(session: Session, job_id: int) -> Optional[TagRetagJobRecord]:
    job = session.get(TagRetagJobRecord, job_id)
    if job is None or job.operation != AnalysisOperation.FULL_ANALYSIS.value:
        return None
    return job
