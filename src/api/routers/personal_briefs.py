"""Personal-digest API and all-in-one runtime orchestration."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from api import deps
from models.analysis_contracts import DigestGenerationReason, PersonalDigestStatus
from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserRecord,
)
from services import personal_digest as digest_service
from services.article_analysis import source_allows_analysis
from services.article_time import in_time_window


router = APIRouter(
    prefix="/api/reader/briefs",
    tags=["personal-briefs"],
    dependencies=[Depends(deps.require_reader)],
)

PERSONAL_DIGEST_ENABLED_KEY = "personal_digest_enabled"
ARTICLE_ANALYSIS_ENABLED_KEY = "article_analysis_enabled"
TERMINAL_SOURCE_STATES = frozenset({"healthy", "failing", "unknown"})
PUBLIC_DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"


def _enabled(session: Session, key: str) -> bool:
    row = session.get(AppSettingRecord, key)
    return bool(row and str(row.value or "").strip().casefold() in {"1", "true", "yes", "on"})


def personal_digest_enabled(session: Session) -> bool:
    return _enabled(session, PERSONAL_DIGEST_ENABLED_KEY)


def _require_enabled(session: Session) -> None:
    if not personal_digest_enabled(session):
        raise HTTPException(status_code=404, detail="个人早报功能尚未启用")


def _username(auth: dict[str, Any]) -> str:
    username = str(auth.get("sub") or auth.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return username


def _parse_json(raw: str, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value


def _parse_time(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=digest_service.SHANGHAI)
    return value.astimezone(digest_service.SHANGHAI)


def _serialize_item(item: PersonalDigestItemRecord) -> dict[str, Any]:
    return {
        "id": item.id,
        "article_id": item.article_id,
        "position": item.position,
        "section": item.section,
        "selection_lane": item.selection_lane,
        "quality_score": item.quality_score_snapshot,
        "matched_interest_codes": _parse_json(item.matched_interest_codes_json, []),
        "coverage_adjustments": _parse_json(item.coverage_adjustments_json, []),
        "selection_reason": item.selection_reason,
        "snapshot": _parse_json(item.snapshot_json, {}),
    }


def serialize_edition(
    session: Session,
    edition: PersonalDigestEditionRecord,
    *,
    include_items: bool = True,
) -> dict[str, Any]:
    items = []
    if include_items:
        rows = session.exec(
            select(PersonalDigestItemRecord)
            .where(PersonalDigestItemRecord.edition_id == edition.id)
            .order_by(PersonalDigestItemRecord.position)
        ).all()
        items = [_serialize_item(row) for row in rows]
    return {
        "id": edition.id,
        "report_date": edition.report_date,
        "revision": edition.revision,
        "status": edition.status,
        "first_open_at": edition.first_open_at,
        "check_after": edition.check_after,
        "cutoff_at": edition.cutoff_at,
        "deadline_at": edition.deadline_at,
        "generated_at": edition.generated_at,
        "expected_source_ids": _parse_json(edition.expected_source_ids_json, []),
        "due_source_ids": _parse_json(edition.due_source_ids_json, []),
        "policy_version": edition.policy_version,
        "taxonomy_version": edition.taxonomy_version,
        "interest_version": edition.interest_version,
        "generation_reason": edition.generation_reason,
        "degraded_reason": edition.degraded_reason,
        "error": edition.error,
        "items": items,
    }


def _latest_edition(session: Session, username: str, report_date: str) -> PersonalDigestEditionRecord | None:
    return session.exec(
        select(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.owner_username == username,
            PersonalDigestEditionRecord.report_date == report_date,
            PersonalDigestEditionRecord.status != PersonalDigestStatus.SUPERSEDED.value,
        )
        .order_by(PersonalDigestEditionRecord.revision.desc())
    ).first()


def _sources_ready(
    session: Session,
    edition: PersonalDigestEditionRecord,
    now: dt.datetime,
) -> bool:
    due = tuple(str(value) for value in _parse_json(edition.due_source_ids_json, []) if value)
    if not due:
        return True
    configs = {
        row.source_id: row
        for row in session.exec(select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(due))).all()
    }
    states = {
        row.source_id: row
        for row in session.exec(select(SourceStateRecord).where(SourceStateRecord.source_id.in_(due))).all()
    }
    day_start = dt.datetime.combine(
        dt.date.fromisoformat(edition.report_date), dt.time.min, digest_service.SHANGHAI
    )
    for source_id in due:
        # The public daily brief is synthesized into the article ledger rather
        # than collected by a fetcher, so it intentionally has no source-state
        # row.  Treat today's persisted brief as its readiness signal; without
        # this exception a user subscribed to the public brief always waits
        # until the personal-digest deadline even when that brief already
        # exists and has finished analysis.
        if source_id == PUBLIC_DAILY_BRIEF_SOURCE_ID:
            public_brief = session.exec(
                select(ArticleRecord.id).where(
                    ArticleRecord.source_id == PUBLIC_DAILY_BRIEF_SOURCE_ID,
                    ArticleRecord.publish_date.like(f"{edition.report_date}%"),
                    ArticleRecord.has_content.is_(True),
                )
            ).first()
            if public_brief is None:
                return False
            continue
        config = configs.get(source_id)
        private = source_id.startswith(digest_service.PRIVATE_SOURCE_PREFIX) or bool(
            config and config.owner_username
        )
        if private:
            if source_id in digest_service.calculate_due_source_ids(
                session, [source_id], as_of=now
            ):
                return False
            continue
        state = states.get(source_id)
        completed = _parse_time(state.last_completed_at) if state else None
        if (
            state is None
            or state.status not in TERMINAL_SOURCE_STATES
            or completed is None
            or completed < day_start
        ):
            return False
    return True


def _analysis_ready(
    session: Session,
    edition: PersonalDigestEditionRecord,
    now: dt.datetime,
) -> bool:
    if not _enabled(session, ARTICLE_ANALYSIS_ENABLED_KEY):
        return True
    source_ids = [
        str(value)
        for value in _parse_json(edition.expected_source_ids_json, [])
        if value and source_allows_analysis(session, str(value))
    ]
    if not source_ids:
        return True
    since = now - dt.timedelta(hours=72)
    coarse_start = (since - dt.timedelta(days=1)).date().isoformat()
    coarse_end = (now + dt.timedelta(days=1)).date().isoformat()
    article_rows = list(
        session.exec(
            select(ArticleRecord.id, ArticleRecord.fetched_date).where(
                ArticleRecord.source_id.in_(source_ids),
                ArticleRecord.has_content.is_(True),
                func.substr(ArticleRecord.fetched_date, 1, 10) >= coarse_start,
                func.substr(ArticleRecord.fetched_date, 1, 10) <= coarse_end,
            )
        ).all()
    )
    article_ids = [
        article_id for article_id, fetched_date in article_rows
        if in_time_window(fetched_date, start=since, end=now)
    ]
    if not article_ids:
        return True
    finished = {
        row.article_id for row in session.exec(
            select(ArticleAnalysisRecord).where(
                ArticleAnalysisRecord.article_id.in_(article_ids),
                or_(
                    ArticleAnalysisRecord.status.in_(["succeeded", "skipped"]),
                    (
                        ArticleAnalysisRecord.status.in_(["failed", "timeout"])
                        & ArticleAnalysisRecord.next_attempt_at.is_(None)
                    ),
                    (
                        ArticleAnalysisRecord.analyzed_at.is_not(None)
                        & ArticleAnalysisRecord.quality_score.is_not(None)
                    ),
                ),
            )
        ).all()
    }
    return len(finished) == len(set(article_ids))


def process_pending_edition(
    session: Session,
    edition: PersonalDigestEditionRecord,
    *,
    now: dt.datetime | None = None,
) -> PersonalDigestEditionRecord:
    if edition.status not in {
        PersonalDigestStatus.PENDING.value,
        PersonalDigestStatus.GENERATING.value,
    }:
        return edition
    current = (now or dt.datetime.now(digest_service.SHANGHAI)).astimezone(
        digest_service.SHANGHAI
    )
    if edition.status == PersonalDigestStatus.GENERATING.value:
        lease_expires = _parse_time(edition.generation_lease_expires_at)
        if lease_expires is not None and lease_expires > current:
            return edition
    deadline = _parse_time(edition.deadline_at)
    forced = deadline is not None and current >= deadline
    if not forced and (not _sources_ready(session, edition, current) or not _analysis_ready(session, edition, current)):
        return edition
    edition, generation_token = digest_service.claim_personal_digest_generation(
        session, edition.id, now=current
    )
    if generation_token is None:
        return edition
    try:
        result = digest_service.generate_personal_digest(
            session,
            edition.owner_username,
            report_date=edition.report_date,
            now=current,
            pending_edition_id=edition.id,
            generation_token=generation_token,
        )
    except Exception as exc:  # noqa: BLE001 - persist lifecycle failure without content
        return digest_service.mark_personal_digest_failed(
            session,
            edition.id,
            str(exc),
            generation_token=generation_token,
        )
    assert result.edition is not None
    return result.edition


def trigger_today_revision(
    engine: Engine,
    username: str,
    reason: DigestGenerationReason | str,
) -> PersonalDigestEditionRecord | None:
    with Session(engine) as session:
        if not personal_digest_enabled(session):
            return None
        result = digest_service.start_personal_digest_edition(
            session, username, generation_reason=reason
        )
        return result.edition


def trigger_all_today_revisions(engine: Engine, reason: DigestGenerationReason | str) -> int:
    with Session(engine) as session:
        if not personal_digest_enabled(session):
            return 0
        usernames = list(
            session.exec(select(UserRecord.username).where(UserRecord.is_active.is_(True))).all()
        )
    return sum(1 for username in usernames if trigger_today_revision(engine, username, reason))


def start_scheduled_editions(engine: Engine) -> int:
    return trigger_all_today_revisions(engine, DigestGenerationReason.SCHEDULED)


def process_pending_editions(engine: Engine) -> int:
    with Session(engine) as session:
        if not personal_digest_enabled(session):
            return 0
        rows = list(
            session.exec(
                select(PersonalDigestEditionRecord)
                .where(
                    PersonalDigestEditionRecord.status.in_([
                        PersonalDigestStatus.PENDING.value,
                        PersonalDigestStatus.GENERATING.value,
                    ])
                )
                .order_by(PersonalDigestEditionRecord.report_date, PersonalDigestEditionRecord.revision)
            ).all()
        )
        completed = 0
        for edition in rows:
            before = edition.status
            after = process_pending_edition(session, edition)
            if before != after.status:
                completed += 1
        return completed


def _ensure(
    session: Session,
    username: str,
    *,
    reason: DigestGenerationReason,
    first_open: bool,
) -> dict[str, Any]:
    _require_enabled(session)
    now = dt.datetime.now(digest_service.SHANGHAI)
    result = digest_service.start_personal_digest_edition(
        session,
        username,
        now=now,
        generation_reason=reason,
        first_open_at=now if first_open else None,
    )
    if result.edition is None:
        return {"status": "empty_subscriptions", "edition": None}
    edition = process_pending_edition(session, result.edition, now=now)
    return {"status": edition.status, "edition": serialize_edition(session, edition)}


@router.post("/today/ensure")
def ensure_today(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    return _ensure(
        session,
        _username(auth),
        reason=DigestGenerationReason.FIRST_OPEN,
        first_open=True,
    )


@router.post("/today/rebuild")
def rebuild_today(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    return _ensure(
        session,
        _username(auth),
        reason=DigestGenerationReason.MANUAL_REBUILD,
        first_open=True,
    )


@router.get("/today")
def get_today(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    _require_enabled(session)
    today = dt.datetime.now(digest_service.SHANGHAI).date().isoformat()
    edition = _latest_edition(session, _username(auth), today)
    if edition is None:
        return {"status": "not_started", "edition": None}
    return {"status": edition.status, "edition": serialize_edition(session, edition)}


@router.get("/{report_date}")
def get_by_date(
    report_date: str,
    revision: int | None = Query(default=None, ge=1),
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    _require_enabled(session)
    try:
        dt.date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期必须为 YYYY-MM-DD") from exc
    if revision is None:
        edition = _latest_edition(session, _username(auth), report_date)
    else:
        edition = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == _username(auth),
                PersonalDigestEditionRecord.report_date == report_date,
                PersonalDigestEditionRecord.revision == revision,
            )
        ).first()
    if edition is None:
        raise HTTPException(status_code=404, detail="个人早报不存在")
    return serialize_edition(session, edition)


@router.get("")
def list_editions(
    limit: int = Query(default=30, ge=1, le=100),
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    _require_enabled(session)
    rows = session.exec(
        select(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.owner_username == _username(auth),
            PersonalDigestEditionRecord.status != PersonalDigestStatus.SUPERSEDED.value,
        )
        .order_by(
            PersonalDigestEditionRecord.report_date.desc(),
            PersonalDigestEditionRecord.revision.desc(),
        )
        .limit(limit)
    ).all()
    return {"items": [serialize_edition(session, row, include_items=False) for row in rows]}
