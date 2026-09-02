"""Personal-digest domain service.

The service owns the privacy scope, deterministic orchestration and immutable
edition snapshots.  API side effects, scheduling and readiness polling remain in
WP-4; this module exposes small functions those integration points can call.
"""

from __future__ import annotations

import datetime as dt
import json
import zlib
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.analysis_contracts import (
    AnalysisStatus,
    ContentGenre,
    DigestArticleCandidateDTO,
    DigestGenerationReason,
    InterestStance,
    PERSONAL_DIGEST_FALLBACK_WINDOW_HOURS,
    PERSONAL_DIGEST_LATEST_FALLBACK_LIMIT,
    PERSONAL_DIGEST_WINDOW_HOURS,
    PersonalDigestStatus,
    SelectionLane,
    TagStatus,
    TaggingStatus,
    UserInterestDTO,
)
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    DuplicateGroupMemberRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    TaxonomyVersionRecord,
    UserInterestTagRecord,
)
from services import source_visibility
from services.article_display_tags import load_display_tags
from services.digest_selection import (
    DigestSelectionPolicy,
    section_for_genre,
    select_digest_articles,
)
from services.source_naming import friendly_source_name


SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_VERSION = "personal-digest-v1"
PRIVATE_SOURCE_PREFIX = "user_rss_"
DEFAULT_PRIVATE_FRESHNESS_MINUTES = 60


@dataclass(frozen=True)
class FrozenDigestScope:
    expected_source_ids: tuple[str, ...]
    due_source_ids: tuple[str, ...]
    source_state_snapshot: Mapping[str, object]


@dataclass(frozen=True)
class PersonalDigestGenerationResult:
    status: Literal["ready", "degraded", "empty_subscriptions"]
    edition: PersonalDigestEditionRecord | None
    items: tuple[PersonalDigestItemRecord, ...] = ()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_list(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(sorted({str(item).strip() for item in parsed if str(item).strip()}))


def _json_mapping(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_shanghai(value: dt.datetime | None = None) -> dt.datetime:
    value = value or dt.datetime.now(SHANGHAI)
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _parse_datetime(value: str | None) -> dt.datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _explicit_subscription_source_ids(subscription: ReaderSubscriptionRecord) -> set[str]:
    """Parse only the two explicit source-filter fields; all other filters are inert."""

    try:
        filters = json.loads(subscription.filters_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(filters, dict):
        return set()
    result: set[str] = set()
    for key in ("source_ids", "source_id"):
        value = filters.get(key)
        values = value if isinstance(value, list) else str(value or "").split(",")
        for item in values:
            source_id = str(item).strip()
            if source_id and source_id != "*":
                result.add(source_id)
    return result


def resolve_personal_digest_source_ids(session: Session, username: str) -> list[str]:
    """Resolve the personal-digest permission boundary without any broadening.

    Only explicit ``source_id``/``source_ids`` filters from this user's active
    subscription rows count.  Empty/global filters contribute nothing, hidden
    sources are removed, admins receive no special all-library scope, and private
    sources must have a live config plus this very subscription membership.
    """

    username = (username or "").strip()
    if not username:
        return []
    subscriptions = session.exec(
        select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == username,
            ReaderSubscriptionRecord.is_active == True,  # noqa: E712
        )
    ).all()
    explicit: set[str] = set()
    membership: dict[str, set[str]] = {}
    for subscription in subscriptions:
        ids = _explicit_subscription_source_ids(subscription)
        explicit.update(ids)
        for source_id in ids:
            membership.setdefault(source_id, set()).add(subscription.owner_username)

    explicit -= source_visibility.hidden_source_ids(session)
    if not explicit:
        return []

    configs = {
        record.source_id: record
        for record in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(sorted(explicit)))
        ).all()
    }
    allowed: list[str] = []
    for source_id in sorted(explicit):
        config = configs.get(source_id)
        is_private = source_id.startswith(PRIVATE_SOURCE_PREFIX) or bool(
            config and config.owner_username
        )
        if not is_private:
            allowed.append(source_id)
            continue
        # Orphaned private articles/config-less IDs must not become reachable via a
        # crafted subscription row.  A configured private source is reachable only
        # through an explicit active row owned by this user.
        if config is not None and config.owner_username and username in membership.get(source_id, set()):
            allowed.append(source_id)
    return allowed


def calculate_due_source_ids(
    session: Session,
    expected_source_ids: Iterable[str],
    *,
    as_of: dt.datetime | None = None,
    scheduled_source_ids: Iterable[str] | None = None,
) -> list[str]:
    """Calculate the readiness-wait set separately from the permission set.

    Built-in/public sources are due by default (or when explicitly named by the
    scheduler).  Hourly private sources are due only when their last successful run
    is older than their configured freshness interval, preventing a missing global
    collection-run ID from blocking the edition forever.
    """

    expected = tuple(sorted({str(value).strip() for value in expected_source_ids if str(value).strip()}))
    if not expected:
        return []
    now = _as_shanghai(as_of)
    scheduled = None if scheduled_source_ids is None else {
        str(value).strip() for value in scheduled_source_ids if str(value).strip()
    }
    configs = {
        row.source_id: row for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(expected))
        ).all()
    }
    states = {
        row.source_id: row for row in session.exec(
            select(SourceStateRecord).where(SourceStateRecord.source_id.in_(expected))
        ).all()
    }
    due: list[str] = []
    for source_id in expected:
        config = configs.get(source_id)
        is_private = source_id.startswith(PRIVATE_SOURCE_PREFIX) or bool(
            config and config.owner_username
        )
        if is_private:
            interval = (
                config.fetch_interval_minutes
                if config and config.fetch_interval_minutes
                else DEFAULT_PRIVATE_FRESHNESS_MINUTES
            )
            interval = max(int(interval), 1)
            last_success = _parse_datetime(states.get(source_id).last_success_at) if states.get(source_id) else None
            if last_success is None or last_success < now - dt.timedelta(minutes=interval):
                due.append(source_id)
            continue
        if scheduled is None or source_id in scheduled:
            due.append(source_id)
    return due


def freeze_personal_digest_scope(
    session: Session,
    username: str,
    *,
    as_of: dt.datetime | None = None,
    scheduled_source_ids: Iterable[str] | None = None,
) -> FrozenDigestScope:
    """Freeze immutable permission/wait sets plus source-state evidence."""

    expected = resolve_personal_digest_source_ids(session, username)
    due = calculate_due_source_ids(
        session,
        expected,
        as_of=as_of,
        scheduled_source_ids=scheduled_source_ids,
    )
    states = {
        row.source_id: row for row in session.exec(
            select(SourceStateRecord).where(SourceStateRecord.source_id.in_(expected))
        ).all()
    } if expected else {}
    configs = {
        row.source_id: row for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(expected))
        ).all()
    } if expected else {}
    due_set = set(due)
    snapshot: dict[str, object] = {}
    for source_id in expected:
        state = states.get(source_id)
        config = configs.get(source_id)
        snapshot[source_id] = {
            "due": source_id in due_set,
            "status": state.status if state else "never_run",
            "last_run_id": state.last_run_id if state else None,
            "last_completed_at": state.last_completed_at if state else None,
            "last_success_at": state.last_success_at if state else None,
            "latest_saved_count": state.latest_saved_count if state else 0,
            "fetch_interval_minutes": config.fetch_interval_minutes if config else None,
        }
    return FrozenDigestScope(tuple(expected), tuple(due), snapshot)


def _load_interests(session: Session, username: str) -> list[UserInterestDTO]:
    rows = session.exec(
        select(UserInterestTagRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == UserInterestTagRecord.tag_id)
        .where(
            UserInterestTagRecord.owner_username == username,
            CmsTagRecord.status == TagStatus.ACTIVE.value,
        )
    ).all()
    return [
        UserInterestDTO(
            tag_code=tag.code,
            stance=InterestStance(interest.stance),
        )
        for interest, tag in rows
    ]


def _interest_version(session: Session, username: str) -> int:
    rows = session.exec(
        select(UserInterestTagRecord).where(UserInterestTagRecord.owner_username == username)
    ).all()
    payload = "|".join(sorted(
        f"{row.tag_id}:{row.stance}:{row.priority}:{row.updated_at}" for row in rows
    ))
    return zlib.crc32(payload.encode("utf-8")) if payload else 0


def _interest_display_names(
    session: Session, interests: Sequence[UserInterestDTO]
) -> dict[str, str]:
    codes = sorted({item.tag_code for item in interests})
    if not codes:
        return {}
    rows = session.exec(
        select(CmsTagRecord).where(
            CmsTagRecord.code.in_(codes),
            CmsTagRecord.status == TagStatus.ACTIVE.value,
        )
    ).all()
    return {
        tag.code: (tag.name_zh or tag.name_en or tag.code)
        for tag in rows
    }


def _source_display_names(
    session: Session, source_ids: Sequence[str]
) -> dict[str, str]:
    configs = {
        row.source_id: row.name for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(source_ids))
        ).all()
        if row.name
    } if source_ids else {}
    return {
        source_id: configs.get(source_id) or friendly_source_name(source_id)
        for source_id in source_ids
    }


def _taxonomy_version(session: Session) -> int:
    value = session.exec(
        select(TaxonomyVersionRecord.version).where(
            TaxonomyVersionRecord.status == "active"
        )
    ).first()
    return int(value or 0)


def _tag_maps(
    session: Session, article_ids: Sequence[str]
) -> tuple[dict[str, tuple[str, ...]], dict[str, list[dict[str, object]]]]:
    if not article_ids:
        return {}, {}
    rows = session.exec(
        select(ArticleTagAssignmentRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(
            ArticleTagAssignmentRecord.article_id.in_(article_ids),
            CmsTagRecord.status == TagStatus.ACTIVE.value,
        )
    ).all()
    codes: dict[str, list[str]] = {}
    snapshots: dict[str, list[dict[str, object]]] = {}
    for assignment, tag in rows:
        codes.setdefault(assignment.article_id, []).append(tag.code)
        snapshots.setdefault(assignment.article_id, []).append({
            "code": tag.code,
            "kind": tag.kind,
            "name_zh": tag.name_zh,
            "name_en": tag.name_en,
            "is_primary": assignment.is_primary,
            "relevance": assignment.relevance,
        })
    return (
        {article_id: tuple(sorted(set(values))) for article_id, values in codes.items()},
        {
            article_id: sorted(
                values,
                key=lambda item: (
                    not bool(item["is_primary"]),
                    str(item["kind"]),
                    str(item["code"]),
                ),
            )
            for article_id, values in snapshots.items()
        },
    )


def _topic_codes_by_article(
    session: Session, article_ids: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    if not article_ids:
        return {}
    rows = session.exec(
        select(ArticleTagAssignmentRecord.article_id, CmsTagRecord.code)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(
            ArticleTagAssignmentRecord.article_id.in_(article_ids),
            CmsTagRecord.kind == "topic",
            CmsTagRecord.status == TagStatus.ACTIVE.value,
        )
    ).all()
    result: dict[str, list[str]] = {}
    for article_id, code in rows:
        result.setdefault(str(article_id), []).append(str(code))
    return {
        article_id: tuple(sorted(set(codes)))
        for article_id, codes in result.items()
    }


def load_digest_candidates(
    session: Session,
    source_ids: Sequence[str],
    *,
    cutoff_at: dt.datetime,
    window_hours: int,
    require_tagging_complete: bool = False,
) -> list[DigestArticleCandidateDTO]:
    """Bulk-load succeeded analyses inside the requested window."""

    if not source_ids:
        return []
    cutoff = _as_shanghai(cutoff_at)
    since = (cutoff - dt.timedelta(hours=window_hours)).isoformat()
    query = (
        select(ArticleRecord, ArticleAnalysisRecord)
        .join(ArticleAnalysisRecord, ArticleAnalysisRecord.article_id == ArticleRecord.id)
        .where(
            ArticleRecord.source_id.in_(source_ids),
            ArticleRecord.publish_date >= since,
            ArticleRecord.publish_date <= cutoff.isoformat(),
            ArticleAnalysisRecord.status == AnalysisStatus.SUCCEEDED.value,
            ArticleAnalysisRecord.quality_score.is_not(None),
        )
    )
    if require_tagging_complete:
        query = query.where(
            ArticleAnalysisRecord.tagging_status.in_((
                TaggingStatus.SUCCEEDED.value,
                TaggingStatus.PARTIAL.value,
            ))
        )
    rows = session.exec(query).all()
    article_ids = [article.id for article, _analysis in rows]
    tag_codes, _tag_snapshots = _tag_maps(session, article_ids)
    primary_ids = {
        analysis.primary_tag_id for _article, analysis in rows
        if analysis.primary_tag_id is not None
    }
    primary_codes = {
        tag.id: tag.code for tag in session.exec(
            select(CmsTagRecord).where(
                CmsTagRecord.id.in_(primary_ids),
                CmsTagRecord.status == TagStatus.ACTIVE.value,
            )
        ).all()
    } if primary_ids else {}
    duplicate_groups = {
        row.article_id: row.group_id for row in session.exec(
            select(DuplicateGroupMemberRecord).where(
                DuplicateGroupMemberRecord.article_id.in_(article_ids)
            )
        ).all()
    } if article_ids else {}
    candidates: list[DigestArticleCandidateDTO] = []
    for article, analysis in rows:
        try:
            genre = ContentGenre(analysis.content_genre or ContentGenre.OTHER.value)
        except ValueError:
            genre = ContentGenre.OTHER
        primary_code = primary_codes.get(analysis.primary_tag_id)
        candidates.append(DigestArticleCandidateDTO(
            article_id=article.id,
            source_id=article.source_id,
            title=article.title,
            source_url=article.source_url,
            publish_date=article.publish_date,
            fetched_date=article.fetched_date,
            quality_score=float(analysis.quality_score),
            score_reason=analysis.score_reason,
            one_sentence_summary=analysis.one_sentence_summary,
            content_genre=genre,
            tag_codes=tag_codes.get(article.id, ()),
            primary_tag_code=primary_code,
            duplicate_group_id=duplicate_groups.get(article.id),
        ))
    return candidates


def _latest_revision(session: Session, username: str, report_date: str) -> int:
    value = session.exec(
        select(func.max(PersonalDigestEditionRecord.revision)).where(
            PersonalDigestEditionRecord.owner_username == username,
            PersonalDigestEditionRecord.report_date == report_date,
        )
    ).one()
    return int(value or 0)


def _latest_completed_edition(
    session: Session, username: str, report_date: str
) -> PersonalDigestEditionRecord | None:
    return session.exec(
        select(PersonalDigestEditionRecord).where(
            PersonalDigestEditionRecord.owner_username == username,
            PersonalDigestEditionRecord.report_date == report_date,
            PersonalDigestEditionRecord.status.in_((
                PersonalDigestStatus.READY.value,
                PersonalDigestStatus.DEGRADED.value,
            )),
        ).order_by(PersonalDigestEditionRecord.revision.desc())
    ).first()


def _latest_edition(
    session: Session, username: str, report_date: str
) -> PersonalDigestEditionRecord | None:
    return session.exec(
        select(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.owner_username == username,
            PersonalDigestEditionRecord.report_date == report_date,
        )
        .order_by(PersonalDigestEditionRecord.revision.desc())
    ).first()


def _previous_edition_article_ids(
    session: Session, username: str, report_date: str
) -> set[str]:
    previous_date = (dt.date.fromisoformat(report_date) - dt.timedelta(days=1)).isoformat()
    previous = _latest_completed_edition(session, username, previous_date)
    if previous is None or previous.id is None:
        return set()
    rows = session.exec(
        select(PersonalDigestItemRecord.article_id).where(
            PersonalDigestItemRecord.edition_id == previous.id,
            PersonalDigestItemRecord.article_id.is_not(None),
        )
    ).all()
    return {str(article_id) for article_id in rows if article_id}


def _items_for_edition(
    session: Session, edition_id: int | None
) -> tuple[PersonalDigestItemRecord, ...]:
    if edition_id is None:
        return ()
    return tuple(session.exec(
        select(PersonalDigestItemRecord).where(
            PersonalDigestItemRecord.edition_id == edition_id
        ).order_by(PersonalDigestItemRecord.position)
    ).all())


def _snapshot(
    article: ArticleRecord,
    analysis: ArticleAnalysisRecord | None,
    tags: Sequence[dict[str, object]],
    *,
    display_tags: Sequence[dict[str, object]] | None = None,
    selection_reason: str,
    degraded: bool,
) -> dict[str, object]:
    return {
        "article_id": article.id,
        "title": article.title,
        "content_type": article.content_type,
        "source_id": article.source_id,
        "source_url": article.source_url,
        "publish_date": article.publish_date,
        "fetched_date": article.fetched_date,
        # Keeping the archived body is what makes a deleted private-RSS article
        # genuinely readable rather than merely listable from history.
        "content": article.content,
        "one_sentence_summary": analysis.one_sentence_summary if analysis else "",
        "summary": analysis.summary if analysis else "",
        "quality_score": analysis.quality_score if analysis else None,
        "score_reason": analysis.score_reason if analysis else "",
        "content_genre": analysis.content_genre if analysis else None,
        "tags": list(tags),
        "display_tags": list(display_tags if display_tags is not None else tags),
        "selection_reason": selection_reason,
        "is_latest_update": degraded,
    }


def _load_latest_fallback(
    session: Session,
    source_ids: Sequence[str],
    interests: Sequence[UserInterestDTO],
    *,
    limit: int,
    excluded_article_ids: Iterable[str] = (),
) -> list[tuple[ArticleRecord, ArticleAnalysisRecord | None, tuple[str, ...], list[dict[str, object]]]]:
    if not source_ids or limit < 1:
        return []
    mute_codes = {
        item.tag_code for item in interests
        if getattr(item.stance, "value", item.stance) == InterestStance.MUTE.value
    }
    query = (
        select(ArticleRecord, ArticleAnalysisRecord)
        .join(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
            isouter=not bool(mute_codes),
        )
        .where(ArticleRecord.source_id.in_(source_ids))
    )
    excluded = sorted({str(value) for value in excluded_article_ids if str(value)})
    if excluded:
        query = query.where(ArticleRecord.id.notin_(excluded))
    if mute_codes:
        muted_assignment = (
            select(ArticleTagAssignmentRecord.id)
            .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
            .where(
                ArticleTagAssignmentRecord.article_id == ArticleRecord.id,
                CmsTagRecord.status == TagStatus.ACTIVE.value,
                CmsTagRecord.code.in_(sorted(mute_codes)),
            )
        )
        query = query.where(
            ArticleAnalysisRecord.tagging_status.in_((
                TaggingStatus.SUCCEEDED.value,
                TaggingStatus.PARTIAL.value,
            )),
            ~exists(muted_assignment),
        )
    rows = session.exec(
        query.order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.asc()).limit(limit)
    ).all()
    article_ids = [article.id for article, _analysis in rows]
    tag_codes, tag_snapshots = _tag_maps(session, article_ids)
    analyses = {article.id: analysis for article, analysis in rows if analysis is not None}
    display_tags = load_display_tags(
        session,
        article_ids,
        analyses=analyses,
        canonical_tags=tag_snapshots,
    )
    result: list[tuple[ArticleRecord, ArticleAnalysisRecord | None, tuple[str, ...], list[dict[str, object]], list[dict[str, object]]]] = []
    for article, analysis in rows:
        codes = tag_codes.get(article.id, ())
        result.append((
            article,
            analysis,
            codes,
            tag_snapshots.get(article.id, []),
            display_tags.get(article.id, []),
        ))
        if len(result) >= limit:
            break
    return result


def start_personal_digest_edition(
    session: Session,
    username: str,
    *,
    report_date: str | None = None,
    now: dt.datetime | None = None,
    generation_reason: DigestGenerationReason | str = DigestGenerationReason.SCHEDULED,
    first_open_at: dt.datetime | None = None,
    scheduled_source_ids: Iterable[str] | None = None,
) -> PersonalDigestGenerationResult:
    """Persist a pending edition with its frozen permission/readiness scope.

    WP-4 can call this from schedule/ensure/rebuild handlers, poll readiness using
    the returned row, then pass its ``id`` to :func:`generate_personal_digest`.
    Ordinary ensure calls reuse the current lifecycle row; explicit interest,
    subscription or manual rebuild reasons allocate a new same-day revision.
    """

    current = _as_shanghai(now)
    report_date = report_date or current.date().isoformat()
    reason = DigestGenerationReason(
        str(getattr(generation_reason, "value", generation_reason))
    ).value
    if report_date != current.date().isoformat():
        existing = _latest_completed_edition(session, username, report_date)
        if existing is not None:
            return PersonalDigestGenerationResult(
                status=existing.status,  # type: ignore[arg-type]
                edition=existing,
                items=_items_for_edition(session, existing.id),
            )
        raise ValueError("个人早报只生成当天版本，历史日期保持不变")

    rebuild_reasons = {
        DigestGenerationReason.INTEREST_CHANGED.value,
        DigestGenerationReason.SUBSCRIPTION_CHANGED.value,
        DigestGenerationReason.MANUAL_REBUILD.value,
    }
    # Resolve the current permission boundary before reusing today's lifecycle
    # row.  In particular, removing the last subscription must not leave an
    # older pending/ready revision looking current simply because it was frozen
    # before the unsubscribe completed.
    scope = freeze_personal_digest_scope(
        session,
        username,
        as_of=current,
        scheduled_source_ids=scheduled_source_ids,
    )
    if not scope.expected_source_ids:
        stale_pending = list(
            session.exec(
                select(PersonalDigestEditionRecord).where(
                    PersonalDigestEditionRecord.owner_username == username,
                    PersonalDigestEditionRecord.report_date == report_date,
                    PersonalDigestEditionRecord.status == PersonalDigestStatus.PENDING.value,
                )
            ).all()
        )
        for previous in stale_pending:
            previous.status = PersonalDigestStatus.SUPERSEDED.value
            previous.updated_at = current.isoformat()
            session.add(previous)
        if stale_pending:
            session.commit()
        return PersonalDigestGenerationResult(status="empty_subscriptions", edition=None)

    if reason not in rebuild_reasons:
        existing = _latest_edition(session, username, report_date)
        if existing is not None:
            first_open = _as_shanghai(first_open_at) if first_open_at else None
            if first_open is not None and existing.first_open_at is None:
                existing.first_open_at = first_open.isoformat()
                existing.deadline_at = (first_open + dt.timedelta(minutes=15)).isoformat()
                existing.updated_at = current.isoformat()
                session.add(existing)
                session.commit()
            return PersonalDigestGenerationResult(
                status=existing.status,  # type: ignore[arg-type]
                edition=existing,
                items=_items_for_edition(session, existing.id),
            )

    first_open = _as_shanghai(first_open_at) if first_open_at else None
    report_day = dt.date.fromisoformat(report_date)
    edition = PersonalDigestEditionRecord(
        owner_username=username,
        report_date=report_date,
        revision=_latest_revision(session, username, report_date) + 1,
        status=PersonalDigestStatus.PENDING.value,
        first_open_at=first_open.isoformat() if first_open else None,
        check_after=dt.datetime.combine(report_day, dt.time(8, 30), SHANGHAI).isoformat(),
        cutoff_at=current.isoformat(),
        deadline_at=(first_open + dt.timedelta(minutes=15)).isoformat() if first_open else None,
        expected_source_ids_json=_json(list(scope.expected_source_ids)),
        due_source_ids_json=_json(list(scope.due_source_ids)),
        source_state_snapshot_json=_json(dict(scope.source_state_snapshot)),
        policy_version=POLICY_VERSION,
        taxonomy_version=_taxonomy_version(session),
        interest_version=_interest_version(session, username),
        generation_reason=reason,
        created_at=current.isoformat(),
        updated_at=current.isoformat(),
    )
    session.add(edition)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = _latest_edition(session, username, report_date)
        if winner is None:
            raise
        return PersonalDigestGenerationResult(
            status=winner.status,  # type: ignore[arg-type]
            edition=winner,
            items=_items_for_edition(session, winner.id),
        )
    session.refresh(edition)
    # A rebuild freezes a new subscription/interest boundary.  Older pending
    # rows have no immutable content yet and must never be generated later
    # using the newly changed interests.  Keep the lifecycle row for audit,
    # but make it terminal and visibly superseded by this revision.
    older_pending = list(
        session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == username,
                PersonalDigestEditionRecord.report_date == report_date,
                PersonalDigestEditionRecord.revision < edition.revision,
                PersonalDigestEditionRecord.status == PersonalDigestStatus.PENDING.value,
            )
        ).all()
    )
    for previous in older_pending:
        previous.status = PersonalDigestStatus.SUPERSEDED.value
        previous.updated_at = current.isoformat()
        session.add(previous)
    if older_pending:
        session.commit()
    return PersonalDigestGenerationResult(status="pending", edition=edition)


def mark_personal_digest_failed(
    session: Session, edition_id: int, error: str
) -> PersonalDigestEditionRecord:
    """Move a non-terminal lifecycle row to failed with a bounded error summary."""

    edition = session.get(PersonalDigestEditionRecord, edition_id)
    if edition is None:
        raise ValueError("个人早报版本不存在")
    if edition.status in {PersonalDigestStatus.READY.value, PersonalDigestStatus.DEGRADED.value}:
        return edition
    edition.status = PersonalDigestStatus.FAILED.value
    edition.error = (error or "generation failed").strip()[:1000]
    edition.updated_at = dt.datetime.now(SHANGHAI).isoformat()
    session.add(edition)
    session.commit()
    session.refresh(edition)
    return edition


def generate_personal_digest(
    session: Session,
    username: str,
    *,
    report_date: str | None = None,
    now: dt.datetime | None = None,
    generation_reason: DigestGenerationReason | str = DigestGenerationReason.SCHEDULED,
    first_open_at: dt.datetime | None = None,
    scheduled_source_ids: Iterable[str] | None = None,
    source_state_snapshot: Mapping[str, object] | None = None,
    frozen_scope: FrozenDigestScope | None = None,
    force_new_revision: bool = False,
    pending_edition_id: int | None = None,
    policy: DigestSelectionPolicy | None = None,
) -> PersonalDigestGenerationResult:
    """Generate and persist one immutable personal-digest edition.

    Normal ensure calls are idempotent.  Interest/subscription/manual rebuilds create
    a new same-day revision; an ``interest_changed`` request for a historical date is
    rejected so historical editions never move with today's preferences.
    """

    policy = policy or DigestSelectionPolicy()
    current = _as_shanghai(now)
    report_date = report_date or current.date().isoformat()
    reason = str(getattr(generation_reason, "value", generation_reason))
    reason = DigestGenerationReason(reason).value
    rebuild_reasons = {
        DigestGenerationReason.INTEREST_CHANGED.value,
        DigestGenerationReason.SUBSCRIPTION_CHANGED.value,
        DigestGenerationReason.MANUAL_REBUILD.value,
    }
    if reason == DigestGenerationReason.INTEREST_CHANGED.value and report_date != current.date().isoformat():
        raise ValueError("兴趣变化只允许重编排当天早报，历史日期保持不变")
    if report_date != current.date().isoformat():
        existing = _latest_completed_edition(session, username, report_date)
        if existing is not None:
            return PersonalDigestGenerationResult(
                status=existing.status,  # type: ignore[arg-type]
                edition=existing,
                items=_items_for_edition(session, existing.id),
            )
        raise ValueError("个人早报只生成当天版本，历史日期保持不变")
    pending_edition = None
    if pending_edition_id is not None:
        pending_edition = session.get(PersonalDigestEditionRecord, pending_edition_id)
        if pending_edition is None or pending_edition.owner_username != username:
            raise ValueError("个人早报待生成版本不存在")
        if pending_edition.report_date != report_date:
            raise ValueError("个人早报待生成版本日期不匹配")
        if pending_edition.status not in {
            PersonalDigestStatus.PENDING.value,
            PersonalDigestStatus.GENERATING.value,
        }:
            return PersonalDigestGenerationResult(
                status=pending_edition.status,  # type: ignore[arg-type]
                edition=pending_edition,
                items=_items_for_edition(session, pending_edition.id),
            )
        scope = FrozenDigestScope(
            expected_source_ids=_json_list(pending_edition.expected_source_ids_json),
            due_source_ids=_json_list(pending_edition.due_source_ids_json),
            source_state_snapshot=_json_mapping(pending_edition.source_state_snapshot_json),
        )
        reason = pending_edition.generation_reason
    else:
        scope = frozen_scope or freeze_personal_digest_scope(
            session, username, as_of=current, scheduled_source_ids=scheduled_source_ids
        )
    if not scope.expected_source_ids:
        return PersonalDigestGenerationResult(status="empty_subscriptions", edition=None)
    if pending_edition is None and not force_new_revision and reason not in rebuild_reasons:
        existing = _latest_completed_edition(session, username, report_date)
        if existing is not None:
            return PersonalDigestGenerationResult(
                status=existing.status,  # type: ignore[arg-type]
                edition=existing,
                items=_items_for_edition(session, existing.id),
            )

    interests = _load_interests(session, username)
    tag_display_names = _interest_display_names(session, interests)
    source_display_names = _source_display_names(session, scope.expected_source_ids)
    previous_article_ids = _previous_edition_article_ids(session, username, report_date)
    has_mutes = any(
        getattr(item.stance, "value", item.stance) == InterestStance.MUTE.value
        for item in interests
    )
    candidates = load_digest_candidates(
        session,
        scope.expected_source_ids,
        cutoff_at=current,
        window_hours=PERSONAL_DIGEST_WINDOW_HOURS,
        require_tagging_complete=has_mutes,
    )
    candidates = [
        candidate for candidate in candidates
        if candidate.article_id not in previous_article_ids
    ]
    selections = select_digest_articles(
        candidates,
        interests,
        policy=policy,
        topic_codes_by_article=_topic_codes_by_article(
            session, [candidate.article_id for candidate in candidates]
        ),
        tag_display_names=tag_display_names,
        source_display_names=source_display_names,
    )
    if len(selections) < policy.target_items:
        candidates = load_digest_candidates(
            session,
            scope.expected_source_ids,
            cutoff_at=current,
            window_hours=PERSONAL_DIGEST_FALLBACK_WINDOW_HOURS,
            require_tagging_complete=has_mutes,
        )
        candidates = [
            candidate for candidate in candidates
            if candidate.article_id not in previous_article_ids
        ]
        selections = select_digest_articles(
            candidates,
            interests,
            policy=policy,
            topic_codes_by_article=_topic_codes_by_article(
                session, [candidate.article_id for candidate in candidates]
            ),
            tag_display_names=tag_display_names,
            source_display_names=source_display_names,
        )

    generated_at = current.isoformat()
    first_open = _as_shanghai(first_open_at) if first_open_at else None
    report_day = dt.date.fromisoformat(report_date)
    check_after = dt.datetime.combine(report_day, dt.time(8, 30), SHANGHAI)
    raw_source_snapshot = dict(source_state_snapshot or scope.source_state_snapshot)
    source_snapshot = {
        source_id: raw_source_snapshot.get(source_id, {})
        for source_id in scope.expected_source_ids
    }
    degraded = not selections
    revision = (
        pending_edition.revision
        if pending_edition is not None
        else _latest_revision(session, username, report_date) + 1
    )
    edition = pending_edition or PersonalDigestEditionRecord(
        owner_username=username,
        report_date=report_date,
        revision=revision,
        check_after=check_after.isoformat(),
        cutoff_at=current.isoformat(),
        created_at=generated_at,
        updated_at=generated_at,
    )
    edition.status = (
        PersonalDigestStatus.DEGRADED.value if degraded else PersonalDigestStatus.READY.value
    )
    if first_open is not None:
        edition.first_open_at = edition.first_open_at or first_open.isoformat()
        edition.deadline_at = edition.deadline_at or (
            first_open + dt.timedelta(minutes=15)
        ).isoformat()
    edition.generated_at = generated_at
    edition.expected_source_ids_json = _json(list(scope.expected_source_ids))
    edition.due_source_ids_json = _json(list(scope.due_source_ids))
    edition.source_state_snapshot_json = _json(source_snapshot)
    edition.policy_version = POLICY_VERSION
    edition.taxonomy_version = _taxonomy_version(session)
    edition.interest_version = _interest_version(session, username)
    edition.generation_reason = reason
    edition.degraded_reason = "no_qualified_content" if degraded else None
    edition.error = None
    edition.updated_at = generated_at
    session.add(edition)
    session.flush()
    assert edition.id is not None

    # A generating edition is deliberately recoverable after a process restart.
    # The prior attempt may already have flushed or committed some snapshot rows
    # before it was interrupted.  Rebuild that non-terminal revision in place so
    # the unique (edition_id, position) key cannot strand it in `generating`.
    # This delete and the replacement inserts share the final transaction, so a
    # later failure restores the previous rows instead of losing the snapshot.
    if pending_edition is not None:
        existing_snapshot_ids = session.exec(
            select(PersonalDigestItemRecord.id).where(
                PersonalDigestItemRecord.edition_id == edition.id,
            )
        ).all()
        if existing_snapshot_ids:
            session.exec(
                delete(PersonalDigestItemRecord).where(
                    PersonalDigestItemRecord.edition_id == edition.id
                )
            )
            session.flush()

    item_records: list[PersonalDigestItemRecord] = []
    if selections:
        selection_by_id = {selection.article_id: selection for selection in selections}
        article_ids = list(selection_by_id)
        articles = {
            row.id: row for row in session.exec(
                select(ArticleRecord).where(ArticleRecord.id.in_(article_ids))
            ).all()
        }
        analyses = {
            row.article_id: row for row in session.exec(
                select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(article_ids))
            ).all()
        }
        _codes, tag_snapshots = _tag_maps(session, article_ids)
        display_tags = load_display_tags(
            session,
            article_ids,
            analyses=analyses,
            canonical_tags=tag_snapshots,
        )
        for position, selection in enumerate(selections):
            article = articles.get(selection.article_id)
            analysis = analyses.get(selection.article_id)
            if article is None or analysis is None:
                continue
            record = PersonalDigestItemRecord(
                edition_id=edition.id,
                article_id=article.id,
                position=position,
                section=section_for_genre(analysis.content_genre),
                selection_lane=str(getattr(selection.lane, "value", selection.lane)),
                quality_score_snapshot=analysis.quality_score,
                matched_interest_codes_json=_json(list(selection.matched_interest_codes)),
                ranking_features_json=_json({"policy_version": POLICY_VERSION}),
                coverage_adjustments_json=_json(list(selection.coverage_adjustments)),
                selection_reason=selection.selection_reason,
                snapshot_json=_json(_snapshot(
                    article,
                    analysis,
                    tag_snapshots.get(article.id, []),
                    display_tags=display_tags.get(article.id, []),
                    selection_reason=selection.selection_reason,
                    degraded=False,
                )),
                created_at=generated_at,
            )
            session.add(record)
            item_records.append(record)
    else:
        latest = _load_latest_fallback(
            session,
            scope.expected_source_ids,
            interests,
            limit=PERSONAL_DIGEST_LATEST_FALLBACK_LIMIT,
            excluded_article_ids=previous_article_ids,
        )
        for position, (article, analysis, _codes, tags, display_tags) in enumerate(latest):
            record = PersonalDigestItemRecord(
                edition_id=edition.id,
                article_id=article.id,
                position=position,
                section="订阅源最新更新",
                selection_lane=SelectionLane.QUALITY.value,
                quality_score_snapshot=analysis.quality_score if analysis else None,
                matched_interest_codes_json="[]",
                ranking_features_json=_json({"degraded": True}),
                coverage_adjustments_json="[]",
                selection_reason="",
                snapshot_json=_json(_snapshot(
                    article,
                    analysis,
                    tags,
                    display_tags=display_tags,
                    selection_reason="",
                    degraded=True,
                )),
                created_at=generated_at,
            )
            session.add(record)
            item_records.append(record)
    try:
        session.commit()
    except IntegrityError:
        # Concurrent ensure/rebuild calls can race on the immutable
        # (owner, date, revision) key.  The database chooses one winner; the loser
        # returns that same edition instead of surfacing a transient 500.
        session.rollback()
        winner = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == username,
                PersonalDigestEditionRecord.report_date == report_date,
                PersonalDigestEditionRecord.revision == revision,
            )
        ).first()
        if winner is None:
            raise
        return PersonalDigestGenerationResult(
            status=winner.status,  # type: ignore[arg-type]
            edition=winner,
            items=_items_for_edition(session, winner.id),
        )
    session.refresh(edition)
    for item in item_records:
        session.refresh(item)
    return PersonalDigestGenerationResult(
        status="degraded" if degraded else "ready",
        edition=edition,
        items=tuple(item_records),
    )


__all__ = [
    "FrozenDigestScope",
    "PersonalDigestGenerationResult",
    "calculate_due_source_ids",
    "freeze_personal_digest_scope",
    "generate_personal_digest",
    "start_personal_digest_edition",
    "load_digest_candidates",
    "resolve_personal_digest_source_ids",
]
