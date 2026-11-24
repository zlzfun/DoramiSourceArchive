"""Internal fixed-publication grants for a trusted operator, never an HTTP API.

The private harness must pin/approve ``manifest_sha256`` before constructing a
scope, bind the engine to its approved DB, and independently gate each real paid
POST. These immutable values are not unforgeable Python capabilities or a cost
sandbox. No reader setting or public API may construct one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from models.custom_interests import UserInterestConditionRecord as Condition
from models.db import AppSettingRecord, ArticleRecord, UserRecord
from services.article_time import in_time_window

UTC = dt.timezone.utc
MAX_SCOPE_SECONDS = 21600
MAX_BATCH_IDS = 200


class MatchScopeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _utc(value: dt.datetime) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MatchScopeError("scope_timestamp_invalid")
    return value.astimezone(UTC)


def database_binding(engine) -> str:
    """Non-secret binding to the approved SQLite file, not a replacement-file guard."""
    database = engine.url.database
    if engine.dialect.name != "sqlite" or not database or database == ":memory:":
        raise MatchScopeError("scope_database_invalid")
    return _hash(["sqlite", str(Path(database).resolve())])


@dataclass(frozen=True)
class MatchScopeGrant:
    condition_id: int
    article_id: str
    owner_username: str
    condition_revision: int
    source_id: str
    publish_date: str
    paid_content_identity: str


@dataclass(frozen=True)
class OperatorMatchEligibilityScope:
    # SHA256 of canonical authorization fields (manifest_payload), pinned by the
    # private approver. Recomputing this in untrusted code is not authorization.
    manifest_sha256: str
    database_binding: str
    issued_at: dt.datetime
    expires_at: dt.datetime
    publish_after: dt.datetime
    publish_before: dt.datetime
    grants: tuple[MatchScopeGrant, ...]

    def manifest_payload(self):
        return {
            "schema": 1, "database_binding": self.database_binding,
            "issued_at": _utc(self.issued_at).isoformat(), "expires_at": _utc(self.expires_at).isoformat(),
            "publish_after": _utc(self.publish_after).isoformat(), "publish_before": _utc(self.publish_before).isoformat(),
            "grants": [vars(grant) for grant in self.grants],
        }

    def manifest_digest(self) -> str:
        return _hash(self.manifest_payload())

    def validate(self, engine, current: dt.datetime):
        if type(self.grants) is not tuple or not self.grants:
            raise MatchScopeError("scope_grants_invalid")
        keys = []
        for grant in self.grants:
            if (type(grant) is not MatchScopeGrant or type(grant.condition_id) is not int
                    or grant.condition_id < 1 or type(grant.condition_revision) is not int
                    or grant.condition_revision < 1
                    or any(not isinstance(value, str) or not value.strip() for value in (
                        grant.article_id, grant.owner_username, grant.source_id, grant.publish_date))
                    or not isinstance(grant.paid_content_identity, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", grant.paid_content_identity)):
                raise MatchScopeError("scope_grants_invalid")
            keys.append((grant.condition_id, grant.article_id))
        if keys != sorted(set(keys)):
            raise MatchScopeError("scope_grants_invalid")
        if not isinstance(self.manifest_sha256, str) or self.manifest_sha256 != self.manifest_digest():
            raise MatchScopeError("scope_manifest_mismatch")
        if self.database_binding != database_binding(engine):
            raise MatchScopeError("scope_database_mismatch")
        self.check_clock(current)

    def check_clock(self, current):
        """Cheap repeat check for an already validated immutable scope."""
        after, before = _utc(self.publish_after), _utc(self.publish_before)
        issued, expires, current = _utc(self.issued_at), _utc(self.expires_at), _utc(current)
        if (before - after != dt.timedelta(days=7) or before > issued
                or not dt.timedelta(0) < expires - issued <= dt.timedelta(seconds=MAX_SCOPE_SECONDS)
                or issued > current):
            raise MatchScopeError("scope_window_invalid")
        if current >= expires:
            raise MatchScopeError("scope_expired")


@dataclass(frozen=True)
class ScopedMatchBatch:
    scope: OperatorMatchEligibilityScope
    condition_id: int
    article_ids: tuple[str, ...]

    @property
    def batch_id(self):
        return _hash([self.scope.manifest_sha256, self.condition_id, self.article_ids])

    def grant(self, article_id: str):
        return next((g for g in self.scope.grants
                     if g.condition_id == self.condition_id and g.article_id == article_id), None)

    def check_article(self, session, condition, article, text, source, current):
        from services import custom_interests as service
        self.scope.check_clock(current)
        grant = self.grant(article.id)
        user = session.get(UserRecord, condition.owner_username)
        if (grant is None or article.id not in self.article_ids
                or condition.id != self.condition_id or condition.status != "active"
                or condition.owner_username != grant.owner_username
                or condition.revision != grant.condition_revision or not user or not user.is_active):
            raise MatchScopeError("scope_condition_changed")
        if (article.source_id != grant.source_id or article.publish_date != grant.publish_date
                or not in_time_window(article.publish_date, start=self.scope.publish_after, end=self.scope.publish_before)):
            raise MatchScopeError("scope_publication_changed")
        if article.source_id not in service.readable_source_ids(session, condition.owner_username, [article.source_id]):
            raise MatchScopeError("scope_acl_revoked")
        if service._paid_identity(condition, article, text, source) != grant.paid_content_identity:
            raise MatchScopeError("scope_input_changed")

    def check_one(self, session, article_id, current, *, paid=False):
        from services import custom_interests as service
        self.scope.validate(session.get_bind(), current)
        condition = session.get(Condition, self.condition_id)
        article = session.get(ArticleRecord, article_id)
        if condition is None or article is None:
            raise MatchScopeError("scope_article_missing")
        _, text, _ = service.evidence_snapshot(session, article)
        self.check_article(session, condition, article, text, service._source_metadata(session, article), current)
        if paid:
            enabled = session.get(AppSettingRecord, service.MATCHING_ENABLED_KEY)
            if enabled and enabled.value.strip().casefold() in {'0', 'false', 'off', 'no'}:
                raise MatchScopeError("scope_matching_disabled")
            if not service._semantic_allowed(session, condition.owner_username, article.source_id):
                raise MatchScopeError("scope_ai_consent_revoked")
        return article

    def check_all(self, session, current):
        from services import custom_interests as service
        self.scope.validate(session.get_bind(), current)
        condition = session.get(Condition, self.condition_id)
        if condition is None:
            raise MatchScopeError("scope_condition_changed")
        rows = session.exec(select(ArticleRecord).where(
            ArticleRecord.id.in_(self.article_ids)).order_by(ArticleRecord.id)).all()
        if len(rows) != len(self.article_ids):
            raise MatchScopeError("scope_article_missing")
        for article in rows:
            _, text, _ = service.evidence_snapshot(session, article)
            self.check_article(session, condition, article, text, service._source_metadata(session, article), current)
        return rows


def prepare_batch(engine, scope, candidate_pairs, limit, now, current):
    if scope is None:
        return None
    if now is not None:
        raise MatchScopeError("scope_now_override_forbidden")
    if (type(scope) is not OperatorMatchEligibilityScope or not isinstance(candidate_pairs, dict)
            or len(candidate_pairs) != 1 or type(limit) is not int or limit < 1):
        raise MatchScopeError("scope_batch_invalid")
    cid, ids = next(iter(candidate_pairs.items()))
    if (type(cid) is not int or cid < 1 or not isinstance(ids, (list, tuple, set, frozenset))
            or not 1 <= len(ids) <= min(limit, MAX_BATCH_IDS)
            or any(not isinstance(aid, str) or not aid.strip() for aid in ids)
            or len(set(ids)) != len(ids)):
        raise MatchScopeError("scope_batch_invalid")
    scope.validate(engine, current)
    batch = ScopedMatchBatch(scope, cid, tuple(sorted(ids)))
    if any(batch.grant(aid) is None for aid in ids):
        raise MatchScopeError("scope_batch_not_granted")
    # Complete preflight before any lease or scan mutation.
    with Session(engine) as session:
        batch.check_all(session, current)
    return batch


def release_lease(engine, condition_id, token):
    from sqlalchemy import update
    from models.custom_interests import InterestMatchJobRecord as Job
    with Session(engine) as session:
        session.exec(update(Job).where(Job.condition_id == condition_id, Job.lease_token == token)
                     .values(lease_token="", lease_expires_at=""))
        session.commit()


def batch_report(engine, batch, *, processed, calls, invocations=0, paid_articles=(), status="processed", error_code=""):
    """Live read-only accounting, not an immutable receipt; unknown is not success."""
    from models.custom_interests import InterestArticleMatchRecord as Match, InterestSemanticScanRecord as Scan
    from services import custom_interests as service
    items = {}
    with Session(engine) as session:
        condition = session.get(Condition, batch.condition_id)
        for aid in batch.article_ids:
            article = session.get(ArticleRecord, aid)
            match = session.exec(select(Match).where(Match.condition_id == batch.condition_id,
                                                     Match.article_id == aid)).first()
            row = {"status": "blocked", "reason": error_code or "no_current_result"}
            user = session.get(UserRecord, condition.owner_username) if condition else None
            if (article and condition and condition.status == 'active' and user and user.is_active
                    and match and match.condition_revision == condition.revision
                    and article.source_id in service.readable_source_ids(session, condition.owner_username, [article.source_id])):
                fingerprint, text, _ = service.bound_evidence_snapshot(
                    session, article, service._decode(match.evidence_json))
                identity = service._paid_identity(condition, article, text, service._source_metadata(session, article))
                if match.fingerprint == fingerprint and identity == batch.grant(aid).paid_content_identity:
                    evidence = service._decode(match.evidence_json)
                    scan = session.get(Scan, evidence.get("scan_id")) if evidence.get("scan_id") else None
                    if evidence.get('method') == 'semantic' and not service._semantic_allowed(session, condition.owner_username, article.source_id):
                        items[aid] = {"status": "blocked", "reason": "scope_ai_consent_revoked"}
                        continue
                    if scan and scan.status in ("deferred", "partial"):
                        row = {"status": "pending" if scan.status == "deferred" else "partial", "reason": scan.error_code}
                    elif match.outcome == "unknown":
                        row = {"status": "unknown", "reason": evidence.get("error_code", "unresolved_or_ambiguous")}
                    else:
                        new_result = aid in (paid_articles if evidence.get('method') == 'semantic' else processed)
                        row = {"status": "succeeded_new" if new_result else "reused_verified", "reason": ""}
                    row["outcome"] = match.outcome
            if status == 'expired' and row['status'] in {'blocked', 'pending', 'partial'}:
                row = {**row, 'prior_status': row['status'], 'status': 'expired_unprocessed',
                       'reason': error_code or 'scope_expired'}
            items[aid] = row
    pending = any(row["status"] in ("pending", "partial") for row in items.values())
    return {"status": status, "processed": len(processed), "condition_id": batch.condition_id,
            "semantic_calls": calls, "verifier_invocations": invocations, "scope_hash": batch.scope.manifest_sha256,
            "batch_id": batch.batch_id, "discovery_complete": status == "processed",
            "batch_terminal": status == "processed" and not pending, "items": items,
            **({"error_code": error_code} if error_code else {})}
