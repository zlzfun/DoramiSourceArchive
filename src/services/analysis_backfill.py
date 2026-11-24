"""Governed, resumable historical ``full_analysis`` backfills.

The existing article-analysis row remains the single execution queue.  This
module adds an auditable job header plus per-article snapshots so a deployment
restart can resume dispatch without duplicating work.  Backfill articles are
only dispatched into spare analysis capacity; ``claim_analysis_tasks`` keeps
its newest-first ordering, so live arrivals always win.

Selections (issue #178 wave 2 adds the last two):

- ``all`` — force every article in scope through a full re-analysis;
- ``missing_or_outdated`` — articles without a current result (content, prompt,
  scoring or taxonomy version changed);
- ``missing`` — only articles that never got a result; existing news values are
  never re-scored (the 2026-10-12 stock decision, plan §3.1);
- ``content_value`` — articles that already have a current news value but lack a
  current content value, limited to paper / tutorial / opinion / digest plus
  ``research_paper``-genre news; only the content-value fields are written.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from llm.article_analysis_prompt import (
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
    CONTENT_VALUE_SCORING_VERSION,
    MAX_ANALYSIS_BODY_CHARS,
    analysis_contract_versions,
    analysis_contract_is_reusable,
    content_value_contract_version,
)
from models.analysis_contracts import AnalysisOperation, AnalysisStatus, TaggingStatus
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    TagRetagJobItemRecord,
    TagRetagJobRecord,
)
from services.article_analysis import (
    PENDING_SCOPE_CONTENT_VALUE,
    DEFAULT_MAX_ATTEMPTS,
    compute_content_hash,
    content_value_fill_is_current,
    has_authoritative_analysis,
    in_version_refresh_window,
    queue_article_analysis,
    queue_content_value_fill,
    revoke_queued_analysis,
    sanitize_error,
    source_allows_analysis,
)
from services import sync_consumer_policy
from services.article_time import parse_article_time
from services.taxonomy import current_taxonomy_version, now_iso


FULL_ANALYSIS_CONFIRMATION = "RUN FULL ANALYSIS"
FULL_ANALYSIS_ACTIVE_STATUSES = ("queued", "running", "paused")
FULL_ANALYSIS_SELECTIONS = ("all", "missing_or_outdated", "missing", "content_value")
MISSING_SELECTION = "missing"
CONTENT_VALUE_SELECTION = "content_value"
# 「补内容价值」默认覆盖的内容类型(内容价值为主分的几类),外加体裁为「学术论文」的资讯——
# 这批最可能被模型改判为论文(issue #178 第二波 §3.7)。
CONTENT_VALUE_FILL_KINDS = frozenset({"paper", "tutorial", "opinion", "digest"})
CONTENT_VALUE_FILL_GENRES = frozenset({"research_paper"})
DEFAULT_DISPATCH_LIMIT = 8
LEASE_SECONDS = 300


class AnalysisBackfillError(ValueError):
    """A full-analysis backfill request violates a governance invariant."""


def _local_execution_block(session: Session, article: ArticleRecord) -> Optional[str]:
    """本机对这篇是否已无分析执行权:归档同步接管(远端权威)或接收端停写;有权时返回 None。"""

    if (article.analysis_authority_id or "").strip():
        return "remote_authority"
    if not sync_consumer_policy.local_source_operation_allowed(
        session, article.source_id, operation="analysis"
    ):
        return "v2_consumer_quiesced"
    return None


def _dispatch_target_done(
    selection: Optional[str],
    article: ArticleRecord,
    analysis: Optional[ArticleAnalysisRecord],
) -> bool:
    """派发前目标是否已经达成(不必再派):仅从未分析 = 已有结果;补内容价值 = 已有当前内容价值。

    派发与对账共用「目标已完成 → 本机执行权 → 等待 / 入队」的顺序(codex R1 / R2-1)。
    """

    if selection == MISSING_SELECTION:
        return has_authoritative_analysis(analysis)
    if selection == CONTENT_VALUE_SELECTION:
        return bool(
            analysis is not None
            and analysis.status == AnalysisStatus.SUCCEEDED.value
            and content_value_fill_is_current(article, analysis)
        )
    return False


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
    return parse_article_time(value)


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
        .where(
            ArticleRecord.has_content.is_(True),
            ArticleRecord.content.is_not(None),
            ArticleRecord.content != "",
            ArticleRecord.analysis_authority_id == "",
        )
        .order_by(ArticleRecord.fetched_date.desc(), ArticleRecord.id.desc())
    )
    normalized_sources = tuple(dict.fromkeys(item.strip() for item in source_ids if item.strip()))
    if normalized_sources:
        statement = statement.where(ArticleRecord.source_id.in_(normalized_sources))

    from services.content_classification import classify_item, source_default_kinds

    source_kinds = source_default_kinds()
    targets: list[BackfillTarget] = []
    for article, analysis in session.exec(statement).all():
        if not source_allows_analysis(session, article.source_id):
            continue
        fetched_at = _article_time(article.fetched_date)
        if fetched_at is None or fetched_at > current or (since_time and fetched_at < since_time):
            continue
        content_hash = compute_content_hash(article)
        expected_prompt, expected_scoring = analysis_contract_versions(article.content_type)
        is_current = bool(
            analysis is not None
            and analysis.status == AnalysisStatus.SUCCEEDED.value
            and analysis.tagging_status != TaggingStatus.FAILED.value
            and analysis.content_hash == content_hash
            and analysis_contract_is_reusable(article.content_type, analysis.prompt_version, analysis.scoring_version)
            and int(analysis.taxonomy_version) == int(taxonomy_version)
        )
        if selection == "missing_or_outdated" and is_current:
            continue
        if selection == MISSING_SELECTION and has_authoritative_analysis(analysis):
            continue
        if selection == CONTENT_VALUE_SELECTION:
            if (
                analysis is None
                or analysis.status != AnalysisStatus.SUCCEEDED.value
                or not has_authoritative_analysis(analysis)
                or analysis.content_hash != content_hash
                or analysis.scoring_version != expected_scoring
                or not content_value_contract_version(article.content_type)
                or content_value_fill_is_current(article, analysis)
                # 版本巡检即将整篇重跑的近期文章会顺带产出内容价值,不重复派发(codex R1-8)
                or in_version_refresh_window(article, analysis, now=current)
            ):
                continue
            kind = classify_item(
                source_id=article.source_id,
                source_url=article.source_url,
                content_type=article.content_type,
                content_genre=analysis.content_genre,
                source_kinds=source_kinds,
                content_kind=analysis.content_kind,
            ).kind
            if kind not in CONTENT_VALUE_FILL_KINDS and analysis.content_genre not in CONTENT_VALUE_FILL_GENRES:
                continue
        targets.append(BackfillTarget(article=article, content_hash=content_hash))
    return targets, since, until


PUBLICATION_COHORT_SCHEMA = 1
MAX_PUBLICATION_COHORT_IDS = 5000


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _publication_bound(value: Any) -> dt.datetime:
    try:
        if not isinstance(value, dt.datetime) and (
            not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
            )
        ):
            raise ValueError()
        parsed = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise AnalysisBackfillError("publication bounds require timezone-aware RFC3339 timestamps") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalysisBackfillError("publication bounds require timezone-aware RFC3339 timestamps")
    return parsed.astimezone(dt.timezone.utc)


def _input_fingerprint(article: ArticleRecord) -> str:
    return _digest({key: getattr(article, key) for key in (
        "id", "title", "content", "extensions_json", "source_id", "source_url",
        "content_type", "publish_date", "analysis_authority_id", "has_content",
    )})


def _cohort_result_current(analysis, frozen: dict[str, Any], taxonomy_version: int) -> bool:
    return bool(analysis is not None
                and analysis.status == AnalysisStatus.SUCCEEDED.value
                and analysis.tagging_status != TaggingStatus.FAILED.value
                and analysis.content_hash == frozen["content_hash"]
                and (analysis.prompt_version == frozen["prompt_version"]
                     or (frozen["prompt_version"] == analysis_contract_versions(frozen["content_type"])[0]
                         and analysis_contract_is_reusable(frozen["content_type"], analysis.prompt_version, analysis.scoring_version)))
                and analysis.scoring_version == frozen["scoring_version"]
                and analysis.taxonomy_version == taxonomy_version)


def _publication_cohort(session: Session, *, days, selection, source_ids, article_ids,
                        publish_after, publish_before, taxonomy_version, now):
    """Build both the estimate and creation commitment; never use fetched_date."""
    if all(value is None for value in (article_ids, publish_after, publish_before)):
        return None
    if (days is not None or article_ids is None or publish_after is None
            or publish_before is None):
        raise AnalysisBackfillError("publication cohort requires article_ids, both bounds and days=null")
    if selection != "missing_or_outdated":
        raise AnalysisBackfillError("publication cohort requires selection=missing_or_outdated")
    if (isinstance(article_ids, (str, bytes)) or not 1 <= len(article_ids) <= MAX_PUBLICATION_COHORT_IDS
            or any(not isinstance(aid, str) or not aid.strip() for aid in article_ids)):
        raise AnalysisBackfillError("publication cohort requires 1..5000 nonblank article IDs")
    ids = sorted(aid.strip() for aid in article_ids)
    if len(set(ids)) != len(ids):
        raise AnalysisBackfillError("publication cohort contains duplicate article IDs")
    after, before = _publication_bound(publish_after), _publication_bound(publish_before)
    if after > before or before > _utc(now):
        raise AnalysisBackfillError("publication bounds require after <= before <= current time")
    sources = sorted({sid.strip() for sid in source_ids if sid.strip()})
    rows = {}
    for offset in range(0, len(ids), 500):
        rows.update({row.id: row for row in session.exec(select(ArticleRecord).where(
            ArticleRecord.id.in_(ids[offset:offset + 500]))).all()})
    invalid = {}
    for aid in ids:
        article = rows.get(aid)
        if article is None:
            invalid[aid] = "article_not_found"
        elif sources and article.source_id not in sources:
            invalid[aid] = "source_outside_cohort"
        else:
            published = _article_time(article.publish_date)
            if published is None or not after <= _utc(published) <= before:
                invalid[aid] = "publication_outside_cohort"
    if invalid:
        raise AnalysisBackfillError("invalid publication cohort IDs: " + json.dumps(invalid, sort_keys=True))
    targets, items = [], {}
    for aid in ids:
        article = rows[aid]
        prompt, scoring = analysis_contract_versions(article.content_type)
        frozen = {"input_fingerprint": _input_fingerprint(article),
                  "content_hash": compute_content_hash(article), "source_id": article.source_id,
                  "publish_date": article.publish_date, "content_type": article.content_type,
                  "prompt_version": prompt, "scoring_version": scoring}
        if not article.has_content or not article.content:
            disposition = "no_content"
        elif (block := _local_execution_block(session, article)):
            disposition = block
        elif not source_allows_analysis(session, article.source_id):
            disposition = "source_ai_analysis_disabled"
        elif _cohort_result_current(session.get(ArticleAnalysisRecord, aid), frozen, taxonomy_version):
            disposition = "reused_current"
        else:
            disposition = "target"
            targets.append(BackfillTarget(article, frozen["content_hash"]))
        items[aid] = {**frozen, "disposition": disposition}
    snapshot = {"schema": PUBLICATION_COHORT_SCHEMA, "requested_ids": ids,
                "target_ids": [item.article.id for item in targets], "items": items,
                "publish_after": after.isoformat(), "publish_before": before.isoformat(),
                "source_ids": sources, "selection": selection, "taxonomy_version": taxonomy_version}
    snapshot["cohort_hash"] = _digest(snapshot)
    return targets, snapshot


def _frozen_cohort(scope: dict[str, Any]):
    if "publication_cohort" not in scope:
        return None
    snapshot = scope["publication_cohort"]
    try:
        if not isinstance(snapshot, dict) or snapshot["schema"] != PUBLICATION_COHORT_SCHEMA:
            raise ValueError()
        payload = {key: value for key, value in snapshot.items() if key != "cohort_hash"}
        ids, targets, items = snapshot["requested_ids"], snapshot["target_ids"], snapshot["items"]
        if (snapshot["cohort_hash"] != _digest(payload)
                or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_PUBLICATION_COHORT_IDS
                or any(not isinstance(aid, str) or not aid.strip() for aid in ids)
                or len(set(ids)) != len(ids) or not isinstance(targets, list)
                or len(set(targets)) != len(targets) or not set(targets) <= set(ids)
                or not isinstance(items, dict) or set(items) != set(ids)
                or snapshot["selection"] != "missing_or_outdated"
                or scope.get("selection") != "missing_or_outdated" or scope.get("days") is not None
                or not isinstance(snapshot["taxonomy_version"], int)
                or _publication_bound(snapshot["publish_after"]) > _publication_bound(snapshot["publish_before"])):
            raise ValueError()
        for aid in ids:
            row = items[aid]
            if not all(isinstance(row[key], str) for key in (
                    "input_fingerprint", "content_hash", "source_id", "publish_date", "content_type",
                    "prompt_version", "scoring_version", "disposition")):
                raise ValueError()
            if (row["disposition"] == "target") != (aid in targets):
                raise ValueError()
        return snapshot
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisBackfillError("publication cohort snapshot is invalid") from exc


def _cohort_item_error(session, job, scope, cohort, item, article):
    frozen = cohort["items"].get(item.article_id_snapshot)
    if (item.article_id_snapshot not in cohort["target_ids"] or not frozen
            or item.article_id != item.article_id_snapshot
            or item.target_content_hash != frozen["content_hash"]):
        return "cohort_scope_changed"
    if (job.taxonomy_version != cohort["taxonomy_version"]
            or current_taxonomy_version(session) != cohort["taxonomy_version"]
            or not _target_versions_match(job, scope)
            or analysis_contract_versions(article.content_type)
            != (frozen["prompt_version"], frozen["scoring_version"])):
        return "cohort_version_changed"
    if (article.source_id != frozen["source_id"] or article.publish_date != frozen["publish_date"]):
        return "cohort_scope_changed"
    if _input_fingerprint(article) != frozen["input_fingerprint"]:
        return "cohort_input_changed"
    return None


def _fail_cohort_item(session, item, error, stamp, current):
    # Revoke only the queued matching input, never an unrelated newer analysis.
    analysis = session.get(ArticleAnalysisRecord, item.article_id) if item.article_id else None
    if (item.status == "queued" and analysis is not None
            and analysis.status == AnalysisStatus.PENDING.value
            and analysis.content_hash == item.target_content_hash):
        revoke_queued_analysis(session, item.article_id, reason=error, now=current)
    item.status, item.last_error = "failed", error
    item.completed_at = item.updated_at = stamp
    session.add(item)


def _record_cohort_versions(scope, item, analysis):
    scope.setdefault("cohort_execution_observations", {})[item.article_id_snapshot] = {
        "prompt_version": analysis.prompt_version, "scoring_version": analysis.scoring_version,
        "taxonomy_version": analysis.taxonomy_version, "content_hash": analysis.content_hash,
    }


def estimate_full_analysis_backfill(
    session: Session,
    *,
    days: Optional[int],
    selection: str,
    source_ids: Sequence[str] = (),
    article_ids: Optional[Sequence[str]] = None,
    publish_after: Optional[str] = None,
    publish_before: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    taxonomy_version = current_taxonomy_version(session)
    publication = _publication_cohort(
        session, days=days, selection=selection, source_ids=source_ids, article_ids=article_ids,
        publish_after=publish_after, publish_before=publish_before,
        taxonomy_version=taxonomy_version, now=now,
    )
    if publication is not None:
        targets, cohort = publication
        since, until = cohort["publish_after"], cohort["publish_before"]
    else:
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
    result = {
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
        "contracts": sorted(
            {
                ":".join(analysis_contract_versions(item.article.content_type))
                for item in targets
            }
        ),
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

    if publication is not None:
        result.update({"cohort_hash": cohort["cohort_hash"], "article_ids": cohort["requested_ids"],
                       "target_ids": cohort["target_ids"], "publish_after": since, "publish_before": until,
                       "dispositions": {aid: row["disposition"] for aid, row in cohort["items"].items()}})
    return result


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
    article_ids: Optional[Sequence[str]] = None,
    publish_after: Optional[str] = None,
    publish_before: Optional[str] = None,
    expected_cohort_hash: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if confirmation != FULL_ANALYSIS_CONFIRMATION:
        raise AnalysisBackfillError("full-analysis confirmation text does not match")
    if any(value is not None for value in (article_ids, publish_after, publish_before)):
        # Serialize the rebuild/commit decision against archive writers on SQLite.
        connection = session.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    if _active_full_job(session) is not None:
        raise AnalysisBackfillError("another full-analysis backfill is unfinished")

    taxonomy_version = current_taxonomy_version(session)
    if taxonomy_version <= 0:
        raise AnalysisBackfillError("an active taxonomy version is required")
    try:
        publication = _publication_cohort(
            session, days=days, selection=selection, source_ids=source_ids, article_ids=article_ids,
            publish_after=publish_after, publish_before=publish_before,
            taxonomy_version=taxonomy_version, now=now,
        )
    except AnalysisBackfillError as exc:
        if expected_cohort_hash is not None and str(exc).startswith("invalid publication cohort IDs:"):
            raise AnalysisBackfillError("cohort_changed: " + str(exc)) from exc
        raise

    if publication is not None:
        targets, cohort = publication
        since, until = cohort["publish_after"], cohort["publish_before"]
        if expected_cohort_hash != cohort["cohort_hash"]:
            raise AnalysisBackfillError("cohort_changed: estimate and confirm the current publication cohort")
    else:
        if expected_cohort_hash is not None:
            raise AnalysisBackfillError("expected_cohort_hash requires publication cohort")
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
        "target_contracts": sorted(
            {
                ":".join(analysis_contract_versions(item.article.content_type))
                for item in targets
            }
        ),
        "target_taxonomy_version": taxonomy_version,
        "target_content_value_version": CONTENT_VALUE_SCORING_VERSION,
    }
    if publication is not None:
        scope["publication_cohort"] = cohort
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
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise AnalysisBackfillError("another full-analysis backfill is unfinished") from exc
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
    job_ids=None,
) -> Optional[TagRetagJobRecord]:
    from services.worker_scope import scoped_ids
    job_ids = scoped_ids(job_ids, integer=True)
    owner = lease_owner.strip()
    if not owner:
        raise AnalysisBackfillError("lease_owner is required")
    current = _utc(now)
    stamp = now_iso(current)
    job = session.exec(
        select(TagRetagJobRecord)
        .where(
            TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value,
            TagRetagJobRecord.id.in_(job_ids) if job_ids is not None else True,
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
    current_contracts = {
        ":".join(analysis_contract_versions("")),
        ":".join(analysis_contract_versions("podcast_episode")),
    }
    scoped_contracts = set(scope.get("target_contracts") or [])
    content_value_ok = (
        scope.get("selection") != CONTENT_VALUE_SELECTION
        or scope.get("target_content_value_version") == CONTENT_VALUE_SCORING_VERSION
    )
    return bool(
        int(scope.get("target_taxonomy_version") or 0) == int(job.taxonomy_version)
        and scope.get("target_prompt_version") == ARTICLE_ANALYSIS_PROMPT_VERSION
        and scope.get("target_scoring_version") == ARTICLE_ANALYSIS_SCORING_VERSION
        and (not scoped_contracts or scoped_contracts <= current_contracts)
        and content_value_ok
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
    cohort = _frozen_cohort(scope)
    selection = scope.get("selection")
    content_value_job = selection == CONTENT_VALUE_SELECTION
    queued = list(
        session.exec(
            select(TagRetagJobItemRecord)
            .where(
                TagRetagJobItemRecord.job_id == job.id,
                TagRetagJobItemRecord.status.in_(["pending", "queued"] if cohort else ["queued"]),
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
        if cohort is not None and article is not None:
            error = _cohort_item_error(session, job, scope, cohort, item, article)
            if error:
                _fail_cohort_item(session, item, error, stamp, current)
                continue
            if item.status == "pending":
                continue
        if article is None:
            item.status = "skipped"
            item.last_error = "article_deleted"
        elif analysis is None:
            block = _local_execution_block(session, article)
            if block:
                # 目标未完成而本机已无执行权:没有可等待或可重放的本地任务(codex R2-1)。
                item.status = "skipped"
                item.last_error = block
            else:
                # A crash between item dispatch and queue commit is safe to replay.
                item.status = "pending"
                item.last_error = "analysis_queue_missing"
        else:
            current_hash = compute_content_hash(article)
            expected_prompt, expected_scoring = analysis_contract_versions(
                article.content_type
            )
            if content_value_job:
                # 「补内容价值」只看内容价值是否已是当前版本;新闻分与标签沿用原结果。
                current_result = bool(
                    analysis.status == AnalysisStatus.SUCCEEDED.value
                    and content_value_fill_is_current(article, analysis)
                )
            elif selection == MISSING_SELECTION:
                # 「仅从未分析」的目标是「有结果」,不论它出自回填还是实时补偿扫描(codex R1-1)。
                current_result = bool(
                    analysis.status == AnalysisStatus.SUCCEEDED.value
                    and has_authoritative_analysis(analysis)
                    and analysis.content_hash == current_hash
                )
            else:
                current_result = bool(
                    analysis.status == AnalysisStatus.SUCCEEDED.value
                    and analysis.tagging_status != TaggingStatus.FAILED.value
                    and analysis.content_hash == current_hash
                    and analysis_contract_is_reusable(article.content_type, analysis.prompt_version, analysis.scoring_version)
                    and int(analysis.taxonomy_version) == int(job.taxonomy_version)
                )
            terminal_failure = bool(
                (
                    analysis.status in {AnalysisStatus.FAILED.value, AnalysisStatus.TIMEOUT.value}
                    and analysis.next_attempt_at is None
                    and analysis.attempt_count >= DEFAULT_MAX_ATTEMPTS
                )
                or (
                    not content_value_job
                    and analysis.status == AnalysisStatus.SUCCEEDED.value
                    and analysis.tagging_status == TaggingStatus.FAILED.value
                )
            )
            local_execution_allowed = bool(
                not (article.analysis_authority_id or "").strip()
                and sync_consumer_policy.local_source_operation_allowed(
                    session, article.source_id, operation="analysis"
                )
            )
            if current_result:
                item.status = "succeeded"
                if cohort is None:
                    item.target_content_hash = current_hash
                else:
                    _record_cohort_versions(scope, item, analysis)
                item.last_error = None
            elif not local_execution_allowed:
                # 目标未完成而本机已无执行权(归档同步接管 / 接收端停写):没有可等待的本地任务(codex R1)。
                item.status = "skipped"
                item.last_error = (
                    "remote_authority" if (article.analysis_authority_id or "").strip() else "v2_consumer_quiesced"
                )
            elif analysis.status == AnalysisStatus.SKIPPED.value:
                item.status = "skipped"
                item.last_error = analysis.last_error or "analysis_skipped"
            elif terminal_failure:
                item.status = "failed"
                item.last_error = sanitize_error(analysis.last_error or "analysis_failed")
            elif (
                content_value_job
                and analysis.status == AnalysisStatus.SUCCEEDED.value
                and analysis.pending_scope != PENDING_SCOPE_CONTENT_VALUE
            ):
                # 补评被撤销(源开关关闭等)或被别的路径收尾而没写内容价值,且已无在途工作(codex R1-3)。
                item.status = "skipped"
                item.last_error = analysis.last_error or "content_value_fill_revoked"
            elif not source_allows_analysis(session, article.source_id):
                # 目标未完成而源的本机分析已关(管理员关掉,或首次接入上游时程序关掉默认播客源):
                # worker 会撤销排队并恢复旧结果,不会再为它排队——没有可等待的本地任务。
                item.status = "skipped"
                item.last_error = "source_ai_analysis_disabled"
            else:
                continue
        item.completed_at = stamp if item.status in {"succeeded", "failed", "skipped"} else None
        item.updated_at = stamp
        session.add(item)

    if cohort is not None:
        job.scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
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
    article_ids=None,
) -> int:
    from services.worker_scope import scoped_ids
    article_ids = scoped_ids(article_ids)
    if job.status != "running" or job.lease_owner != lease_owner:
        return 0
    current = _utc(now)
    stamp = now_iso(current)
    scope = _scope(job)
    cohort = _frozen_cohort(scope)
    selection = scope.get("selection")
    content_value_job = selection == CONTENT_VALUE_SELECTION
    rows = list(
        session.exec(
            select(TagRetagJobItemRecord)
            .outerjoin(ArticleRecord, ArticleRecord.id == TagRetagJobItemRecord.article_id)
            .where(
                TagRetagJobItemRecord.job_id == job.id,
                TagRetagJobItemRecord.status == "pending",
                TagRetagJobItemRecord.article_id.in_(article_ids) if article_ids is not None else True,
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
        if cohort is not None:
            error = _cohort_item_error(session, job, scope, cohort, item, article)
            if error:
                _fail_cohort_item(session, item, error, stamp, current)
                continue
        # 共同外层顺序:目标已完成 → 本机执行权 → 等待 / 入队(codex R1 / R2-1)。
        analysis = session.get(ArticleAnalysisRecord, article.id)
        if (_cohort_result_current(analysis, cohort["items"][article.id], cohort["taxonomy_version"])
                if cohort is not None else _dispatch_target_done(selection, article, analysis)):
            # 建任务后到派发前,别的路径(实时补偿扫描、版本巡检、远端同步)已经达成目标:不再重评。
            item.status = "succeeded"
            if cohort is not None:
                _record_cohort_versions(scope, item, analysis)
            item.last_error = "already_analyzed" if selection == MISSING_SELECTION else None
            if cohort is None:
                item.target_content_hash = compute_content_hash(article)
            item.completed_at = stamp
            item.updated_at = stamp
            session.add(item)
            continue
        block = _local_execution_block(session, article)
        if block:
            item.status = "skipped"
            item.last_error = block
            item.completed_at = stamp
            item.updated_at = stamp
            session.add(item)
            continue
        if selection == MISSING_SELECTION:
            if (
                analysis is not None
                and analysis.status in {AnalysisStatus.PENDING.value, AnalysisStatus.RUNNING.value}
            ):
                # 已在途:关联它的结果,不重新入队、不重置尝试次数。
                item.status = "queued"
                if cohort is None:
                    item.target_content_hash = compute_content_hash(article)
                item.last_error = None
                item.queued_at = stamp
                item.updated_at = stamp
                session.add(item)
                dispatched += 1
                continue
        if content_value_job:
            outcome = queue_content_value_fill(session, article.id, now=current)
        else:
            outcome = queue_article_analysis(
                session,
                article.id,
                enabled=True,
                force=True,
                now=current,
            )
        if outcome == "busy":
            continue
        if content_value_job and outcome == "unchanged":
            # 已有当前内容价值(例如版本巡检的整篇重跑顺带产出了它):目标已完成。
            item.status = "succeeded"
            if cohort is None:
                item.target_content_hash = compute_content_hash(article)
            item.last_error = None
            item.completed_at = stamp
        elif outcome in {"ineligible", "remote_authority", "consumer_quiesced", "skipped", "unchanged"}:
            item.status = "skipped"
            item.last_error = f"queue_{outcome}"
            item.completed_at = stamp
        else:
            item.status = "queued"
            if cohort is None:
                item.target_content_hash = compute_content_hash(article)
            item.last_error = None
            item.queued_at = stamp
            dispatched += 1
        item.updated_at = stamp
        session.add(item)
    if cohort is not None:
        job.scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
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
        if item.status == "queued" and item.article_id:
            revoke_queued_analysis(
                session,
                item.article_id,
                reason=reason,
                now=_utc(now),
            )
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
    stamp = now_iso(_utc(now))
    for item in session.exec(
        select(TagRetagJobItemRecord).where(
            TagRetagJobItemRecord.job_id == job.id,
            TagRetagJobItemRecord.status == "queued",
        )
    ).all():
        if item.article_id:
            revoke_queued_analysis(
                session,
                item.article_id,
                reason="job_paused",
                now=_utc(now),
            )
        item.status = "pending"
        item.queued_at = None
        item.last_error = None
        item.updated_at = stamp
        session.add(item)
    job.status = "paused"
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = stamp
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
    _frozen_cohort(_scope(job))
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
    scope = _scope(job)
    cohort = _frozen_cohort(scope)
    if cohort is not None:
        for item in failed:
            article = session.get(ArticleRecord, item.article_id) if item.article_id else None
            if (article is None or item.last_error in {"cohort_input_changed", "cohort_scope_changed", "cohort_version_changed"}
                    or _cohort_item_error(session, job, scope, cohort, item, article)):
                raise AnalysisBackfillError("cohort_changed: frozen target cannot be retried; estimate a new cohort")
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
    cohort = _frozen_cohort(scope)
    if cohort is not None:
        result.update({"cohort_hash": cohort["cohort_hash"], "article_ids": cohort["requested_ids"],
                       "target_ids": cohort["target_ids"], "publish_after": cohort["publish_after"],
                       "publish_before": cohort["publish_before"],
                       "dispositions": {aid: row["disposition"] for aid, row in cohort["items"].items()},
                       "execution_observations": scope.get("cohort_execution_observations", {})})
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


def list_full_analysis_backfills(
    session: Session, *, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    jobs = session.exec(
        select(TagRetagJobRecord)
        .where(TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value)
        .order_by(TagRetagJobRecord.id.desc())
        .offset(max(0, int(offset)))
        .limit(max(1, min(limit, 100)))
    ).all()
    return [serialize_full_analysis_backfill(session, job) for job in jobs]


def count_full_analysis_backfills(session: Session) -> int:
    return int(
        session.exec(
            select(func.count(TagRetagJobRecord.id)).where(
                TagRetagJobRecord.operation == AnalysisOperation.FULL_ANALYSIS.value
            )
        ).one()
        or 0
    )


def get_full_analysis_backfill(session: Session, job_id: int) -> Optional[TagRetagJobRecord]:
    job = session.get(TagRetagJobRecord, job_id)
    if job is None or job.operation != AnalysisOperation.FULL_ANALYSIS.value:
        return None
    return job
