"""Fair, node-local candidate maintenance and exact identity reconciliation.

No function here promotes a new concept or infers a new alias. Evidence remains
available for human review; independent-support counts are a separate projection.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import or_, update
from sqlmodel import Session, select

from config import settings
from models.db import (
    AppSettingRecord, ArticleAnalysisRecord, ArticleRecord, CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord, CmsTagEventRecord, CmsTagRecord, EventAssignmentLedgerRecord,
    EventMemberRecord, SourceConfigRecord, TagRetagJobRecord,
)
from models.taxonomy_governance import CandidateGovernanceActionRecord, CandidateGovernanceStateRecord
from services import sync_consumer_policy, taxonomy
from services.source_visibility import hidden_source_ids
from services.user_sources import is_user_source, source_is_credentialed
from services.article_time import SHANGHAI, parse_article_time

RULE_VERSION = "exact-governance-v1"


def _now(value=None):
    value = value or dt.datetime.now(dt.timezone.utc)
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _date(row):
    # A malformed explicit publication date is not fresh evidence. Missing dates
    # may use ingestion time; future dates never fall back to ingestion time.
    parsed = parse_article_time(row.published_date or row.created_at)
    return parsed.astimezone(SHANGHAI).date() if parsed else dt.date.min


def public_review_evidence(session: Session, candidate_id: int, *, now=None):
    """Public, current 30-day evidence; no mutation or transaction boundary."""
    today = _now(now).astimezone(SHANGHAI).date()
    since = today - dt.timedelta(days=29)
    hidden = hidden_source_ids(session)
    rows = session.exec(select(CmsTagCandidateEvidenceRecord).where(
        CmsTagCandidateEvidenceRecord.candidate_id == candidate_id
    )).all()
    result = []
    for row in rows:
        article = session.get(ArticleRecord, row.article_id)
        if article is None or article.source_id != row.source_id or row.source_id in hidden:
            continue
        source = session.get(SourceConfigRecord, row.source_id)
        if source_is_credentialed(source):
            continue
        if article.source_id == "dorami_daily_brief" or article.content_type in {"daily_brief", "personal_digest"}:
            continue
        published = parse_article_time(row.published_date or row.created_at)
        if published is None or published > _now(now) or not since <= _date(row) <= today:
            continue
        result.append(row)
    return sorted(result, key=lambda row: (row.created_at, row.article_id), reverse=True)


def article_version_fingerprint(session: Session, article: ArticleRecord) -> str:
    analysis = session.get(ArticleAnalysisRecord, article.id)
    publications = []
    if article.content_type == "podcast_episode":
        from models.db import PodcastTextPublicationRecord, PodcastTextArtifactRecord
        for pub in session.exec(select(PodcastTextPublicationRecord).where(PodcastTextPublicationRecord.episode_id == article.id)).all():
            artifact = session.get(PodcastTextArtifactRecord, pub.artifact_id)
            publications.append([pub.model_dump(), artifact.content_hash if artifact else None])
    return _hash({"publications": publications, "article": [article.id, article.title, article.content, article.publish_date,
                              article.source_id, article.analysis_authority_id, article.archive_updated_at],
                  "analysis": analysis.model_dump() if analysis else None})


def evidence_projection(session: Session, candidate_id: int, *, now=None):
    """Two explicit views: review support vs de-duplicated independent support."""
    current = _now(now)
    rows = public_review_evidence(session, candidate_id, now=current)
    today = current.astimezone(SHANGHAI).date()
    independent = []
    seen_content, seen_events = set(), set()
    unknown = []
    fingerprint_rows = []
    for row in rows:
        article = session.get(ArticleRecord, row.article_id)
        source = session.get(SourceConfigRecord, row.source_id)
        member = session.exec(select(EventMemberRecord).where(EventMemberRecord.article_id == row.article_id)).first()
        ledger = session.get(EventAssignmentLedgerRecord, row.article_id)
        origin = (row.source_owner_or_domain or urlsplit(article.source_url).hostname or row.source_id).casefold()
        # Per-row sync provenance is deliberately excluded from origin identity.
        publisher = member.publisher_key if member and member.publisher_key else origin
        body = " ".join((article.content or "").split())
        content_key = _hash(body) if len(body) >= 80 else "url:" + article.source_url
        fingerprint_rows.append([row.model_dump(), article_version_fingerprint(session, article),
                                 member.model_dump() if member else None,
                                 ledger.model_dump() if ledger else None,
                                 source.model_dump() if source else None])
        if is_user_source(row.source_id) or (source and (source.owner_username or "").strip()):
            continue  # User-submitted public feeds remain review-only.
        if member is None and (ledger is None or ledger.status != "skipped"):
            unknown.append(row.article_id)
            continue
        event_key = (member.event_id, publisher) if member else ("article", row.article_id)
        if content_key in seen_content or event_key in seen_events:
            continue
        seen_content.add(content_key)
        seen_events.add(event_key)
        independent.append((row, publisher, member.event_id if member else None))

    review, support = {}, {}
    for days in (7, 30):
        since = today - dt.timedelta(days=days - 1)
        scoped = [row for row in rows if since <= _date(row) <= today]
        review[f"{days}d"] = {
            "article_count": len({row.article_id for row in scoped}),
            "source_count": len({row.source_owner_or_domain or row.source_id for row in scoped}),
            "day_count": len({_date(row) for row in scoped}),
        }
        scoped_support = [item for item in independent if since <= _date(item[0]) <= today]
        support[f"{days}d"] = {
            "article_count": len(scoped_support),
            "source_count": len({item[1] for item in scoped_support}),
            "day_count": len({_date(item[0]) for item in scoped_support}),
            "event_count": len({item[2] for item in scoped_support if item[2] is not None}),
        }
    review["mean_confidence"] = sum(row.confidence for row in rows) / len(rows) if rows else 0.0
    review["sample_article_ids"] = [row.article_id for row in rows[:10]]
    support["event_assignment_complete"] = not unknown
    support["unknown_event_article_ids"] = unknown
    projection = {"schema_version": 1, "review": review, "independent": support,
                  "evidence_article_ids": [row.article_id for row in rows]}
    return projection, _hash({"projection": projection, "evidence": fingerprint_rows})


def wake_candidate(session: Session, candidate_id: int, *, now=None):
    """Wake after changed evidence, in the caller's transaction; never commit."""
    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        return
    current = _now(now)
    stamp = current.isoformat()
    state = session.get(CandidateGovernanceStateRecord, candidate_id)
    if state is None:
        state = CandidateGovernanceStateRecord(candidate_id=candidate_id,
            lane="entity" if candidate.proposed_kind == "entity" else "new", created_at=stamp, updated_at=stamp)
    state.next_evaluation_at = stamp
    state.updated_at = stamp
    session.add(state)


@dataclass(frozen=True)
class ExactMergePreview:
    candidate_id: int
    target_tag_id: int
    evidence_fingerprint: str
    taxonomy_revision: int
    rule_version: str = RULE_VERSION


def preview_exact_merge(session: Session, candidate_id: int, *, now=None):
    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None or candidate.status not in {"candidate", "reviewing"}:
        return None
    try:
        target = taxonomy.resolve_tag(session, candidate.label, kind=candidate.proposed_kind)
    except taxonomy.AmbiguousTagError:
        return None
    if target is None or target.kind != candidate.proposed_kind:
        return None
    _, fingerprint = evidence_projection(session, candidate_id, now=now)
    return ExactMergePreview(candidate_id, int(target.id), fingerprint, taxonomy.current_taxonomy_sync_revision(session))


def apply_exact_merge(session: Session, preview: ExactMergePreview, *, now=None):
    """Apply preview + audit + bounded retag queue atomically, without committing."""
    if settings.taxonomy.mode == "replica" or sync_consumer_policy.taxonomy_governed_remotely(session):
        raise taxonomy.TaxonomyError("taxonomy is governed remotely")
    if taxonomy.current_taxonomy_version(session) <= 0:
        raise taxonomy.TaxonomyError("a published taxonomy is required for automatic exact merge")
    current = _now(now)
    key = _hash([preview.candidate_id, preview.target_tag_id, preview.evidence_fingerprint,
                 preview.taxonomy_revision, preview.rule_version])
    prior = session.exec(select(CandidateGovernanceActionRecord).where(
        CandidateGovernanceActionRecord.action_key == key)).first()
    if prior:
        return prior
    if preview.rule_version != RULE_VERSION or preview_exact_merge(session, preview.candidate_id, now=current) != preview:
        raise taxonomy.TaxonomyError("exact merge preview is stale")
    candidate = session.get(CmsTagCandidateRecord, preview.candidate_id)
    candidate.status = "merged"
    candidate.resolution_tag_id = preview.target_tag_id
    candidate.updated_at = current.isoformat()
    session.add(candidate)
    event = taxonomy._event(session, "merge", target_tag_id=preview.target_tag_id,
        actor_type="system", actor_id="candidate-governance", reason="unique existing exact name or alias",
        payload={"operation": "candidate_exact_resolve", "candidate_id": candidate.id,
                 "action_key": key, "rule_version": RULE_VERSION}, now=current)
    rows = public_review_evidence(session, preview.candidate_id, now=current)[:100]
    version = taxonomy.current_taxonomy_version(session)
    job = None
    if rows and version > 0:
        article_ids = [row.article_id for row in rows]
        fingerprints = {aid: article_version_fingerprint(session, session.get(ArticleRecord, aid)) for aid in article_ids}
        job = TagRetagJobRecord(event_id=event.id, taxonomy_version=version, operation="retag_only",
            scope_json=json.dumps({"article_ids": article_ids, "version_fingerprints": fingerprints,
                "candidate_id": candidate.id, "target_tag_id": preview.target_tag_id,
                "relevance_verification_required": True}, sort_keys=True),
            status="queued", created_at=current.isoformat(), updated_at=current.isoformat())
        session.add(job)
        session.flush()
    action = CandidateGovernanceActionRecord(action_key=key, candidate_id=preview.candidate_id,
        target_tag_id=preview.target_tag_id, event_id=event.id, retag_job_id=job.id if job else None,
        evidence_fingerprint=preview.evidence_fingerprint, taxonomy_revision=preview.taxonomy_revision,
        rule_version=RULE_VERSION, created_at=current.isoformat())
    session.add(action)
    session.flush()
    return action


def _seed_states(session, current, limit):
    missing = session.exec(select(CmsTagCandidateRecord).outerjoin(CandidateGovernanceStateRecord,
        CmsTagCandidateRecord.id == CandidateGovernanceStateRecord.candidate_id).where(
        CandidateGovernanceStateRecord.candidate_id.is_(None),
        CmsTagCandidateRecord.status.in_(["candidate", "reviewing"])
    ).order_by(CmsTagCandidateRecord.id).limit(max(500, limit * 4))).all()
    for candidate in missing:
        wake_candidate(session, int(candidate.id), now=current)
        state = session.get(CandidateGovernanceStateRecord, int(candidate.id))
        if candidate.proposed_kind != "entity" and candidate.first_seen_at < (current - dt.timedelta(days=7)).isoformat():
            state.lane = "history"
            session.add(state)
    session.commit()


def _wake_changed_inputs(session, current, limit):
    """Rotate through a bounded input scan, including sleeping low-frequency rows.

    Source visibility, analysis and event changes do not necessarily append
    evidence. A durable scan cursor prevents those changes from waiting for a
    weekly timer, without doing an unbounded full-library scan on every tick.
    """
    key = "taxonomy_governance_input_scan_cursor"
    cursor = session.get(AppSettingRecord, key)
    try:
        after = int(cursor.value) if cursor else 0
    except (ValueError, TypeError):
        after = 0
    states = session.exec(select(CandidateGovernanceStateRecord)
        .join(CmsTagCandidateRecord, CmsTagCandidateRecord.id == CandidateGovernanceStateRecord.candidate_id)
        .where(CandidateGovernanceStateRecord.candidate_id > after,
               CmsTagCandidateRecord.status.in_(["candidate", "reviewing"]))
        .order_by(CandidateGovernanceStateRecord.candidate_id).limit(limit)).all()
    for state in states:
        if not state.stats_at or (state.failure_count and state.next_evaluation_at > current.isoformat()):
            continue
        try:
            _, fingerprint = evidence_projection(session, state.candidate_id, now=current)
        except Exception:
            # The normal leased evaluator records the specific failure code.
            state.next_evaluation_at = current.isoformat()
            session.add(state)
            continue
        if fingerprint != state.evidence_fingerprint:
            state.next_evaluation_at = current.isoformat()
            session.add(state)
    value = str(states[-1].candidate_id) if len(states) == limit else "0"
    if cursor is None:
        cursor = AppSettingRecord(key=key, value=value)
    else:
        cursor.value = value
    session.add(cursor)
    session.commit()


def run_governance_cycle(session: Session, *, limit=100, now=None, apply_exact=False):
    """Bounded fair leases; per-item failure isolation; no model dependency.

    New/history alternate. Entities have a separate bounded quota and cannot
    consume topic/industry capacity. Expired leases resume after a crash.
    """
    current = _now(now)
    stamp = current.isoformat()
    bound = max(1, min(int(limit), 500))
    _seed_states(session, current, bound)
    _wake_changed_inputs(session, current, max(100, bound * 2))
    lanes = {}
    for lane in ("new", "history", "entity"):
        lanes[lane] = list(session.exec(select(CandidateGovernanceStateRecord.candidate_id)
            .join(CmsTagCandidateRecord, CmsTagCandidateRecord.id == CandidateGovernanceStateRecord.candidate_id)
            .where(CandidateGovernanceStateRecord.lane == lane,
                CandidateGovernanceStateRecord.next_evaluation_at <= stamp,
                CandidateGovernanceStateRecord.lease_until <= stamp,
                CmsTagCandidateRecord.status.in_(["candidate", "reviewing"]))
            .order_by(CandidateGovernanceStateRecord.next_evaluation_at, CandidateGovernanceStateRecord.candidate_id)
            .limit(bound)).all())
    cursor_key = "taxonomy_governance_next_lane"
    lane_cursor = session.get(AppSettingRecord, cursor_key)
    first_lane = lane_cursor.value if lane_cursor and lane_cursor.value in {"new", "history"} else "new"
    order = [first_lane, "history" if first_lane == "new" else "new"]
    selected = []
    next_lane = first_lane
    while len(selected) < bound and (lanes["new"] or lanes["history"]):
        for lane in order:
            if lanes[lane] and len(selected) < bound:
                selected.append(lanes[lane].pop(0))
                next_lane = "history" if lane == "new" else "new"
    if lane_cursor is None:
        lane_cursor = AppSettingRecord(key=cursor_key, value=next_lane)
    else:
        lane_cursor.value = next_lane
    session.add(lane_cursor)
    session.commit()
    selected.extend(lanes["entity"][:max(1, bound // 5)])
    result = {"evaluated": 0, "merged": 0, "failed": 0, "entities": 0, "candidate_ids": []}
    for candidate_id in selected:
        token = uuid.uuid4().hex
        claimed = session.exec(update(CandidateGovernanceStateRecord).where(
            CandidateGovernanceStateRecord.candidate_id == candidate_id,
            CandidateGovernanceStateRecord.next_evaluation_at <= stamp,
            CandidateGovernanceStateRecord.lease_until <= stamp).values(
                lease_token=token, lease_until=(current + dt.timedelta(minutes=5)).isoformat()))
        session.commit()
        if claimed.rowcount != 1:
            continue
        try:
            candidate = taxonomy.aggregate_candidate(session, candidate_id, now=current, commit=False)
            projection, fingerprint = evidence_projection(session, candidate_id, now=current)
            state = session.get(CandidateGovernanceStateRecord, candidate_id)
            if state.lease_token != token:
                session.rollback()
                continue
            if state.evidence_fingerprint != fingerprint:
                state.evidence_revision += 1
            state.evidence_fingerprint = fingerprint
            state.independent_evidence_json = json.dumps(projection, sort_keys=True)
            state.stats_at = stamp
            state.last_evaluated_at = stamp
            state.rule_version = RULE_VERSION
            state.failure_count = 0
            state.last_error_code = ""
            state.lease_token = state.lease_until = ""
            days = 7 if not projection["review"]["30d"]["article_count"] else 1
            state.next_evaluation_at = (current + dt.timedelta(days=days)).isoformat()
            state.lane = "entity" if candidate.proposed_kind == "entity" else "history"
            state.updated_at = stamp
            session.add(state)
            did_merge = False
            if apply_exact:
                preview = preview_exact_merge(session, candidate_id, now=current)
                if preview:
                    apply_exact_merge(session, preview, now=current)
                    did_merge = True
            session.commit()
            result["merged"] += int(did_merge)
            result["evaluated"] += 1
            result["entities"] += int(candidate.proposed_kind == "entity")
            result["candidate_ids"].append(candidate_id)
        except Exception as exc:
            session.rollback()
            state = session.get(CandidateGovernanceStateRecord, candidate_id)
            if state and state.lease_token == token:
                state.failure_count += 1
                state.last_error_code = type(exc).__name__[:80]
                state.lease_token = state.lease_until = ""
                state.next_evaluation_at = (current + dt.timedelta(minutes=min(1440, 5 * 2 ** min(state.failure_count, 8)))).isoformat()
                state.updated_at = stamp
                session.add(state)
                session.commit()
            result["failed"] += 1
    return result
