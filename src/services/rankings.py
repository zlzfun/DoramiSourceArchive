"""Daily reader rankings derived from governed taxonomy assignments.

The snapshot is deliberately local and deterministic: it consumes only archived
content, current authoritative analyses and active canonical tags.  No LLM call
or taxonomy mutation occurs here.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import delete, or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from api.sources import (
    VALID_CONTENT_SHAPES,
    configured_source_shape,
    registry_source_ids_for_shape,
)
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    RankingContentItemRecord,
    RankingSnapshotRecord,
    RankingTagItemRecord,
    SourceConfigRecord,
    TaxonomyVersionRecord,
)
from services.article_time import SHANGHAI, parse_article_time
from services import source_visibility, user_sources


AXES = ("topic", "industry", "entity")
SHAPES = ("article", "podcast")
WINDOW_DAYS = 7
TOP_TAGS = 10
TOP_CONTENT = 10
MIN_TAG_CONTENT_SUPPORT = 1
MIN_MUST_READ_TAG_APPEARANCES = 2
MIN_MUST_READ_SOURCE_SUPPORT = 2
MIN_RELEVANCE = 0.8
MIN_TAGGED_COVERAGE = 0.5
FULL_PODCAST_BASES = frozenset({"publisher_transcript", "asr_transcript"})
PUBLIC_SCOPE = "all_visible_public_content"


class RankingSnapshotBusy(RuntimeError):
    """Raised when an operator refresh overlaps an existing snapshot build."""


# Every build entry (07:00 cron, startup catch-up, first reader request and the
# admin button) shares one process-wide mutex. The snapshot write is
# transactionally idempotent by date, while this lock prevents two concurrent
# requests from doing the same full-table scan and contending on SQLite.
_SNAPSHOT_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class ContentFact:
    article: ArticleRecord
    analysis: ArticleAnalysisRecord
    shape: str
    published_at: dt.datetime
    score: float
    score_basis: str


@dataclass(frozen=True)
class TagBucket:
    tag: CmsTagRecord
    facts: tuple[ContentFact, ...]

    @property
    def occurrence_count(self) -> int:
        return len({fact.article.id for fact in self.facts})

    @property
    def source_count(self) -> int:
        return len({fact.article.source_id for fact in self.facts})

    @property
    def max_score(self) -> float:
        return max((fact.score for fact in self.facts), default=0.0)

    @property
    def latest(self) -> dt.datetime:
        return max(
            (fact.published_at for fact in self.facts),
            default=dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        )


def snapshot_boundary(at: dt.datetime | None = None) -> tuple[str, dt.datetime, dt.datetime]:
    """Return Shanghai snapshot date and the rolling UTC ``[start, end)`` window."""

    current = at or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    local = current.astimezone(SHANGHAI)
    end_local = dt.datetime.combine(local.date(), dt.time(hour=7), tzinfo=SHANGHAI)
    if local < end_local:
        end_local -= dt.timedelta(days=1)
    end = end_local.astimezone(dt.timezone.utc)
    return end_local.date().isoformat(), end - dt.timedelta(days=WINDOW_DAYS), end


def _snapshot_window(
    at: dt.datetime | None,
    *,
    current_cutoff: bool,
) -> tuple[str, dt.datetime, dt.datetime]:
    """Choose the frozen daily boundary or an operator-triggered current cutoff."""

    if not current_cutoff:
        return snapshot_boundary(at)
    current = at or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    local = current.astimezone(SHANGHAI)
    end = current.astimezone(dt.timezone.utc)
    return local.date().isoformat(), end - dt.timedelta(days=WINDOW_DAYS), end


def _source_shapes(session: Session) -> dict[str, str]:
    result: dict[str, str] = {}
    for shape in VALID_CONTENT_SHAPES:
        for source_id in registry_source_ids_for_shape(shape):
            result[source_id] = shape
    for source in session.exec(select(SourceConfigRecord)).all():
        result.setdefault(
            source.source_id,
            configured_source_shape(source.source_type, source.fetcher_id),
        )
    return result


def _private_source_ids(session: Session) -> set[str]:
    """Return every configured private source, independent of subscriptions.

    The ``user_rss_`` prefix is still checked at article-read time so orphaned
    rows remain private after their source config is physically removed.  The
    owner-backed set closes the inverse gap: a private config with a legacy or
    imported non-standard ID must never enter a site-wide board.
    """

    return set(user_sources.user_source_ids(session))


def _content_shape(article: ArticleRecord, source_shapes: dict[str, str]) -> str:
    known = source_shapes.get(article.source_id)
    if known:
        return known
    if article.content_type == "podcast_episode":
        return "podcast"
    if article.content_type in {
        "github_release", "github_repository", "hf_model", "huggingface_model",
        "github_trending", "social_post", "daily_brief",
    }:
        return "excluded"
    return "article"


def _published_at(article: ArticleRecord) -> dt.datetime | None:
    return parse_article_time(article.publish_date) or parse_article_time(article.fetched_date)


def _score(analysis: ArticleAnalysisRecord, shape: str) -> tuple[float, str]:
    if shape == "podcast":
        if analysis.analysis_basis in FULL_PODCAST_BASES:
            value = analysis.podcast_final_score
            if value is None:
                value = analysis.quality_score
            return float(value or 0.0), "full_transcript"
        value = analysis.podcast_initial_score
        if value is None:
            value = analysis.quality_score
        return float(value or 0.0), "show_notes"
    return float(analysis.quality_score or 0.0), "article_body"


def _taxonomy_version(session: Session) -> int:
    active = session.exec(
        select(TaxonomyVersionRecord).where(TaxonomyVersionRecord.status == "active")
    ).first()
    if active is not None:
        return int(active.version)
    values = session.exec(select(CmsTagRecord.taxonomy_version)).all()
    return max((int(value or 0) for value in values), default=0)


def _eligible_facts(
    session: Session,
    *,
    start: dt.datetime,
    end: dt.datetime,
) -> tuple[dict[str, ContentFact], dict[str, dict[str, int]]]:
    hidden = source_visibility.reader_unavailable_source_ids(session)
    private = _private_source_ids(session)
    source_shapes = _source_shapes(session)
    facts: dict[str, ContentFact] = {}
    coverage = {shape: {"eligible": 0, "analyzed": 0, "tagged": 0} for shape in SHAPES}
    rows = session.exec(
        select(ArticleRecord, ArticleAnalysisRecord).outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
    ).all()
    tagged_ids = set(
        session.exec(
            select(ArticleTagAssignmentRecord.article_id)
            .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
            .where(
                CmsTagRecord.status == "active",
                or_(
                    ArticleTagAssignmentRecord.is_primary.is_(True),
                    ArticleTagAssignmentRecord.relevance >= MIN_RELEVANCE,
                ),
            )
            .distinct()
        ).all()
    )
    for article, analysis in rows:
        if (
            article.source_id in hidden
            or article.source_id in private
            or user_sources.is_user_source(article.source_id)
        ):
            continue
        shape = _content_shape(article, source_shapes)
        if shape not in SHAPES:
            continue
        published = _published_at(article)
        if published is None or not (start <= published < end):
            continue
        coverage[shape]["eligible"] += 1
        if analysis is None or analysis.status != "succeeded":
            continue
        coverage[shape]["analyzed"] += 1
        if (
            analysis.tagging_status not in {"succeeded", "partial"}
            or article.id not in tagged_ids
        ):
            continue
        coverage[shape]["tagged"] += 1
        score, basis = _score(analysis, shape)
        facts[article.id] = ContentFact(article, analysis, shape, published, score, basis)
    return facts, coverage


def _tag_buckets(
    session: Session,
    facts: dict[str, ContentFact],
) -> dict[tuple[str, str, str], TagBucket]:
    grouped: dict[tuple[str, str, str], list[ContentFact]] = defaultdict(list)
    tags: dict[str, CmsTagRecord] = {}
    if not facts:
        return {}
    rows = session.exec(
        select(ArticleTagAssignmentRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(
            ArticleTagAssignmentRecord.article_id.in_(list(facts)),
            CmsTagRecord.status == "active",
            or_(
                ArticleTagAssignmentRecord.is_primary.is_(True),
                ArticleTagAssignmentRecord.relevance >= MIN_RELEVANCE,
            ),
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    for assignment, tag in rows:
        fact = facts.get(assignment.article_id)
        if fact is None or tag.kind not in AXES:
            continue
        if tag.kind == "entity" and tag.entity_type != "organization":
            continue
        dedupe = (assignment.article_id, tag.code)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        key = (fact.shape, tag.kind, tag.code)
        grouped[key].append(fact)
        tags[tag.code] = tag
    return {
        key: TagBucket(tags[key[2]], tuple(items))
        for key, items in grouped.items()
    }


def _supported(bucket: TagBucket) -> bool:
    # A leaderboard describes what appeared, so a single public item from a
    # single public source is enough to rank.  Cross-source corroboration is a
    # stronger signal reserved for the must-read/must-listen section below.
    return bucket.occurrence_count >= MIN_TAG_CONTENT_SUPPORT


def _bucket_order(bucket: TagBucket) -> tuple[Any, ...]:
    return (
        -bucket.occurrence_count,
        -bucket.source_count,
        -bucket.max_score,
        -bucket.latest.timestamp(),
        bucket.tag.code,
    )


def _content_order(fact: ContentFact) -> tuple[Any, ...]:
    return (-fact.score, -fact.published_at.timestamp(), fact.article.id)


def _root_tag_id(tag_id: int, parent_by_id: dict[int, int | None]) -> int:
    current = tag_id
    visited: set[int] = set()
    while current not in visited and parent_by_id.get(current):
        visited.add(current)
        current = int(parent_by_id[current] or current)
    return current


def _build_snapshot_unlocked(
    engine: Engine,
    *,
    at: dt.datetime | None = None,
    current_cutoff: bool = False,
) -> RankingSnapshotRecord:
    """Build or atomically replace one Shanghai-day snapshot."""

    snapshot_date, start, end = _snapshot_window(at, current_cutoff=current_cutoff)
    previous_start = start - dt.timedelta(days=WINDOW_DAYS)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with Session(engine) as session:
        current_facts, coverage = _eligible_facts(session, start=start, end=end)
        previous_facts, _ = _eligible_facts(session, start=previous_start, end=start)
        current_buckets = _tag_buckets(session, current_facts)
        previous_window_buckets = _tag_buckets(session, previous_facts)

        previous_snapshot = session.exec(
            select(RankingSnapshotRecord)
            .where(RankingSnapshotRecord.snapshot_date < snapshot_date)
            .order_by(RankingSnapshotRecord.snapshot_date.desc())
        ).first()
        previous_ranks: dict[tuple[str, str, str], int] = {}
        if previous_snapshot and previous_snapshot.id is not None:
            previous_ranks = {
                (row.shape, row.axis, row.tag_code): row.rank
                for row in session.exec(
                    select(RankingTagItemRecord).where(
                        RankingTagItemRecord.snapshot_id == previous_snapshot.id
                    )
                ).all()
            }

        snapshot = session.exec(
            select(RankingSnapshotRecord).where(
                RankingSnapshotRecord.snapshot_date == snapshot_date
            )
        ).first()
        if snapshot is None:
            snapshot = RankingSnapshotRecord(
                snapshot_date=snapshot_date,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                generated_at=generated_at,
            )
            session.add(snapshot)
            session.flush()
        else:
            session.exec(delete(RankingContentItemRecord).where(
                RankingContentItemRecord.snapshot_id == snapshot.id
            ))
            session.exec(delete(RankingTagItemRecord).where(
                RankingTagItemRecord.snapshot_id == snapshot.id
            ))

        assert snapshot.id is not None
        snapshot.window_start = start.isoformat()
        snapshot.window_end = end.isoformat()
        snapshot.taxonomy_version = _taxonomy_version(session)
        snapshot.generated_at = generated_at
        for shape in SHAPES:
            setattr(snapshot, f"{shape}_eligible_count", coverage[shape]["eligible"])
            setattr(snapshot, f"{shape}_analyzed_count", coverage[shape]["analyzed"])
            setattr(snapshot, f"{shape}_tagged_count", coverage[shape]["tagged"])
        degraded = any(
            values["eligible"] > 0
            and values["tagged"] / values["eligible"] < MIN_TAGGED_COVERAGE
            for values in coverage.values()
        )
        snapshot.status = "degraded" if degraded else "complete"
        session.add(snapshot)

        parent_by_id = {
            int(tag.id): (int(tag.parent_id) if tag.parent_id is not None else None)
            for tag in session.exec(select(CmsTagRecord)).all()
            if tag.id is not None
        }
        content_rows: list[RankingContentItemRecord] = []
        top_memberships: dict[
            tuple[str, str], dict[tuple[str, int], set[str]]
        ] = defaultdict(lambda: defaultdict(set))
        fact_by_shape_id = {(fact.shape, article_id): fact for article_id, fact in current_facts.items()}

        for shape in SHAPES:
            for axis in AXES:
                buckets = sorted(
                    (
                        bucket for (bucket_shape, bucket_axis, _), bucket in current_buckets.items()
                        if bucket_shape == shape and bucket_axis == axis and _supported(bucket)
                    ),
                    key=_bucket_order,
                )[:TOP_TAGS]
                for rank, bucket in enumerate(buckets, start=1):
                    key = (shape, axis, bucket.tag.code)
                    previous_count = previous_window_buckets.get(key)
                    session.add(RankingTagItemRecord(
                        snapshot_id=snapshot.id,
                        shape=shape,
                        axis=axis,
                        tag_id=bucket.tag.id,
                        tag_code=bucket.tag.code,
                        tag_name_zh=bucket.tag.name_zh,
                        tag_name_en=bucket.tag.name_en,
                        rank=rank,
                        occurrence_count=bucket.occurrence_count,
                        distinct_source_count=bucket.source_count,
                        previous_rank=previous_ranks.get(key),
                        count_delta=bucket.occurrence_count - (
                            previous_count.occurrence_count if previous_count else 0
                        ),
                    ))
                    ordered_facts = sorted(bucket.facts, key=_content_order)
                    root_id = _root_tag_id(int(bucket.tag.id or 0), parent_by_id)
                    bucket_sources = {fact.article.source_id for fact in bucket.facts}
                    for content_rank, fact in enumerate(ordered_facts, start=1):
                        row = RankingContentItemRecord(
                            snapshot_id=snapshot.id,
                            shape=shape,
                            axis=axis,
                            tag_code=bucket.tag.code,
                            article_id=fact.article.id,
                            source_id=fact.article.source_id,
                            content_rank=content_rank,
                            score=fact.score,
                            score_basis=fact.score_basis,
                        )
                        content_rows.append(row)
                        if content_rank <= TOP_CONTENT:
                            top_memberships[(shape, fact.article.id)][
                                (axis, root_id)
                            ].update(bucket_sources)

        must_rank_by_content: dict[tuple[str, str], tuple[int, int]] = {}
        for shape in SHAPES:
            candidates: list[tuple[int, ContentFact]] = []
            for (member_shape, article_id), memberships in top_memberships.items():
                if member_shape != shape:
                    continue
                appearance = len(memberships)
                supporting_sources = set().union(*memberships.values()) if memberships else set()
                if (
                    appearance >= MIN_MUST_READ_TAG_APPEARANCES
                    and len(supporting_sources) >= MIN_MUST_READ_SOURCE_SUPPORT
                ):
                    candidates.append((appearance, fact_by_shape_id[(shape, article_id)]))
            candidates.sort(
                key=lambda item: (
                    -item[0],
                    -item[1].score,
                    -item[1].published_at.timestamp(),
                    item[1].article.id,
                )
            )
            for must_rank, (appearance, fact) in enumerate(candidates[:TOP_CONTENT], start=1):
                must_rank_by_content[(shape, fact.article.id)] = (appearance, must_rank)

        for row in content_rows:
            result = must_rank_by_content.get((row.shape, row.article_id))
            if result:
                row.appearance_count, row.must_rank = result
                row.is_must_read = True
            else:
                row.appearance_count = len(
                    top_memberships.get((row.shape, row.article_id), {})
                )
            session.add(row)

        session.commit()
        session.refresh(snapshot)
        return snapshot


def build_snapshot(
    engine: Engine,
    *,
    at: dt.datetime | None = None,
    current_cutoff: bool = False,
) -> RankingSnapshotRecord:
    """Build or atomically replace one Shanghai-day snapshot.

    Scheduled builds wait for the current build instead of racing it. Manual
    refreshes use :func:`build_snapshot_if_idle` so the UI can report a
    conflict immediately.
    """

    with _SNAPSHOT_BUILD_LOCK:
        return _build_snapshot_unlocked(
            engine,
            at=at,
            current_cutoff=current_cutoff,
        )


def build_snapshot_if_idle(
    engine: Engine,
    *,
    at: dt.datetime | None = None,
    current_cutoff: bool = False,
) -> RankingSnapshotRecord:
    """Build a snapshot, or fail fast when another build already owns the lock."""

    if not _SNAPSHOT_BUILD_LOCK.acquire(blocking=False):
        raise RankingSnapshotBusy("榜单正在刷新，请稍后再试")
    try:
        return _build_snapshot_unlocked(
            engine,
            at=at,
            current_cutoff=current_cutoff,
        )
    finally:
        _SNAPSHOT_BUILD_LOCK.release()


def ensure_snapshot_if_empty(
    engine: Engine,
    *,
    at: dt.datetime | None = None,
) -> RankingSnapshotRecord | None:
    """Build exactly once when the database has no usable ranking snapshot.

    The fast path avoids taking the process lock after the first snapshot.
    The second check under the lock makes simultaneous first-reader requests
    idempotent; only the winner performs the full build.
    """

    with Session(engine) as session:
        if _snapshot_or_none(session, "latest") is not None:
            return None
    with _SNAPSHOT_BUILD_LOCK:
        with Session(engine) as session:
            if _snapshot_or_none(session, "latest") is not None:
                return None
        return _build_snapshot_unlocked(engine, at=at, current_cutoff=True)


def _snapshot_or_none(session: Session, date: str) -> RankingSnapshotRecord | None:
    query = select(RankingSnapshotRecord)
    if date and date != "latest":
        query = query.where(RankingSnapshotRecord.snapshot_date == date)
    else:
        query = query.order_by(RankingSnapshotRecord.snapshot_date.desc())
    return session.exec(query).first()


def latest_snapshot_needed(session: Session, *, at: dt.datetime | None = None) -> bool:
    date, _, _ = snapshot_boundary(at)
    return session.exec(
        select(RankingSnapshotRecord.id).where(RankingSnapshotRecord.snapshot_date == date)
    ).first() is None


def snapshot_status(
    session: Session,
    *,
    at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return the operator-facing status of the most recent ranking snapshot."""

    now = at or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    local = now.astimezone(SHANGHAI)
    next_local = dt.datetime.combine(local.date(), dt.time(hour=7), tzinfo=SHANGHAI)
    if local >= next_local:
        next_local += dt.timedelta(days=1)
    snapshot = _snapshot_or_none(session, "latest")
    payload = None
    if snapshot is not None:
        payload = {
            "snapshot_date": snapshot.snapshot_date,
            "generated_at": snapshot.generated_at,
            "window_start": snapshot.window_start,
            "window_end": snapshot.window_end,
            "status": snapshot.status,
            "taxonomy_version": snapshot.taxonomy_version,
            "coverage": {
                shape: {
                    "eligible": getattr(snapshot, f"{shape}_eligible_count"),
                    "analyzed": getattr(snapshot, f"{shape}_analyzed_count"),
                    "tagged": getattr(snapshot, f"{shape}_tagged_count"),
                }
                for shape in SHAPES
            },
        }
    return {
        "refresh_running": _SNAPSHOT_BUILD_LOCK.locked(),
        "schedule": "0 7 * * *",
        "timezone": "Asia/Shanghai",
        "next_refresh_at": next_local.isoformat(),
        "snapshot": payload,
    }


def _visible_rows(
    session: Session,
    snapshot_id: int,
    shape: str,
) -> list[tuple[RankingContentItemRecord, ArticleRecord]]:
    hidden = source_visibility.reader_unavailable_source_ids(session)
    private = _private_source_ids(session)
    rows = session.exec(
        select(RankingContentItemRecord, ArticleRecord)
        .join(ArticleRecord, ArticleRecord.id == RankingContentItemRecord.article_id)
        .where(
            RankingContentItemRecord.snapshot_id == snapshot_id,
            RankingContentItemRecord.shape == shape,
        )
    ).all()
    return [
        (item, article) for item, article in rows
        if item.source_id not in hidden
        and item.source_id not in private
        and not user_sources.is_user_source(item.source_id)
    ]


def _content_payload(
    item: RankingContentItemRecord,
    article: ArticleRecord,
    *,
    appearance_count: int | None = None,
) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "source_id": article.source_id,
        "content_type": article.content_type,
        "publish_date": article.publish_date,
        "score": round(float(item.score), 1),
        "score_basis": item.score_basis,
        "appearance_count": int(
            item.appearance_count if appearance_count is None else appearance_count
        ),
    }


def _all_time_high_scores(
    session: Session,
    *,
    shape: str,
) -> list[dict[str, Any]]:
    """Return the current public all-time Top 10 for one content shape.

    Unlike the tag boards, this list is not bounded by the seven-day snapshot,
    taxonomy coverage or source support. Visibility is evaluated at read time so
    hiding a source removes its historical entries immediately.
    """

    hidden = source_visibility.reader_unavailable_source_ids(session)
    private = _private_source_ids(session)
    source_shapes = _source_shapes(session)
    candidates: list[ContentFact] = []
    rows = session.exec(
        select(ArticleRecord, ArticleAnalysisRecord)
        .join(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .where(ArticleAnalysisRecord.status == "succeeded")
    ).all()
    for article, analysis in rows:
        if (
            article.source_id in hidden
            or article.source_id in private
            or user_sources.is_user_source(article.source_id)
            or _content_shape(article, source_shapes) != shape
        ):
            continue
        score, basis = _score(analysis, shape)
        if score <= 0:
            continue
        published = _published_at(article) or dt.datetime.min.replace(
            tzinfo=dt.timezone.utc
        )
        candidates.append(ContentFact(article, analysis, shape, published, score, basis))

    return [
        {
            "id": fact.article.id,
            "title": fact.article.title,
            "source_id": fact.article.source_id,
            "content_type": fact.article.content_type,
            "publish_date": fact.article.publish_date,
            "score": round(fact.score, 1),
            "score_basis": fact.score_basis,
            "appearance_count": 0,
        }
        for fact in sorted(candidates, key=_content_order)[:TOP_CONTENT]
    ]


def _visible_tag_model(
    tag_items: Iterable[RankingTagItemRecord],
    rows: list[tuple[RankingContentItemRecord, ArticleRecord]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[tuple[RankingContentItemRecord, ArticleRecord]]]]:
    rows_by_tag: dict[str, list[tuple[RankingContentItemRecord, ArticleRecord]]] = defaultdict(list)
    for item, article in rows:
        rows_by_tag[item.tag_code].append((item, article))
    axes: dict[str, list[dict[str, Any]]] = {axis: [] for axis in AXES}
    selected_rows: dict[str, list[tuple[RankingContentItemRecord, ArticleRecord]]] = {}
    for axis in AXES:
        candidates: list[tuple[tuple[Any, ...], RankingTagItemRecord, list]] = []
        for tag in tag_items:
            if tag.axis != axis:
                continue
            visible = rows_by_tag.get(tag.tag_code, [])
            articles = {article.id for _, article in visible}
            sources = {article.source_id for _, article in visible}
            if len(articles) < MIN_TAG_CONTENT_SUPPORT:
                continue
            max_score = max((item.score for item, _ in visible), default=0.0)
            latest = max(
                (_published_at(article) for _, article in visible),
                default=dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            ) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
            order = (-len(articles), -len(sources), -max_score, -latest.timestamp(), tag.tag_code)
            candidates.append((order, tag, visible))
        candidates.sort(key=lambda item: item[0])
        for rank, (_, tag, visible) in enumerate(candidates[:TOP_TAGS], start=1):
            axes[axis].append({
                "code": tag.tag_code,
                "name": tag.tag_name_zh or tag.tag_name_en or tag.tag_code,
                "name_en": tag.tag_name_en,
                "rank": rank,
                "occurrence_count": len({article.id for _, article in visible}),
                "distinct_source_count": len({article.source_id for _, article in visible}),
                "previous_rank": tag.previous_rank,
                "rank_change": (tag.previous_rank - rank) if tag.previous_rank else None,
                "count_delta": tag.count_delta,
            })
            selected_rows[tag.tag_code] = visible
    return axes, selected_rows


def read_rankings(
    session: Session,
    *,
    date: str = "latest",
    shape: str,
) -> dict[str, Any] | None:
    snapshot = _snapshot_or_none(session, date)
    if snapshot is None or snapshot.id is None:
        return None
    tag_items = session.exec(
        select(RankingTagItemRecord).where(
            RankingTagItemRecord.snapshot_id == snapshot.id,
            RankingTagItemRecord.shape == shape,
        )
    ).all()
    rows = _visible_rows(session, snapshot.id, shape)
    axes, selected_rows = _visible_tag_model(tag_items, rows)

    # Rebuild Top-10 memberships after today's visibility filter.  This keeps
    # must-read honest when a source is hidden after snapshot generation.
    tag_by_code = {tag.tag_code: tag for tag in tag_items}
    parent_by_id = {
        int(tag.id): (int(tag.parent_id) if tag.parent_id is not None else None)
        for tag in session.exec(select(CmsTagRecord)).all()
        if tag.id is not None
    }
    memberships: dict[str, dict[tuple[str, int], set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    row_by_article: dict[str, tuple[RankingContentItemRecord, ArticleRecord]] = {}
    for axis in AXES:
        for tag_view in axes[axis]:
            tag = tag_by_code[tag_view["code"]]
            root = _root_tag_id(int(tag.tag_id or 0), parent_by_id)
            visible = sorted(
                selected_rows.get(tag.tag_code, []),
                key=lambda pair: (pair[0].content_rank, pair[1].id),
            )[:TOP_CONTENT]
            supporting_sources = {
                article.source_id
                for _, article in selected_rows.get(tag.tag_code, [])
            }
            for item, article in visible:
                memberships[article.id][(axis, root)].update(supporting_sources)
                row_by_article.setdefault(article.id, (item, article))
    must = [
        (len(member_sources), row_by_article[article_id])
        for article_id, member_sources in memberships.items()
        if (
            len(member_sources) >= MIN_MUST_READ_TAG_APPEARANCES
            and len(set().union(*member_sources.values()))
            >= MIN_MUST_READ_SOURCE_SUPPORT
        )
    ]
    must.sort(key=lambda pair: (
        -pair[0], -pair[1][0].score,
        -((_published_at(pair[1][1]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)).timestamp()),
        pair[1][1].id,
    ))
    coverage = {
        "eligible": getattr(snapshot, f"{shape}_eligible_count"),
        "analyzed": getattr(snapshot, f"{shape}_analyzed_count"),
        "tagged": getattr(snapshot, f"{shape}_tagged_count"),
    }
    coverage["analyzed_ratio"] = round(
        coverage["analyzed"] / coverage["eligible"], 4
    ) if coverage["eligible"] else 1.0
    coverage["tagged_ratio"] = round(
        coverage["tagged"] / coverage["eligible"], 4
    ) if coverage["eligible"] else 1.0
    return {
        "scope": PUBLIC_SCOPE,
        "snapshot_date": snapshot.snapshot_date,
        "window_start": snapshot.window_start,
        "window_end": snapshot.window_end,
        "generated_at": snapshot.generated_at,
        "taxonomy_version": snapshot.taxonomy_version,
        "status": snapshot.status,
        "shape": shape,
        "coverage": coverage,
        "axes": axes,
        "all_time_high_score": _all_time_high_scores(session, shape=shape),
        "must_read": [
            _content_payload(item, article, appearance_count=appearance)
            for appearance, (item, article) in must[:TOP_CONTENT]
        ],
    }


def read_tag_contents(
    session: Session,
    *,
    date: str,
    shape: str,
    tag_code: str,
) -> dict[str, Any] | None:
    snapshot = _snapshot_or_none(session, date)
    if snapshot is None or snapshot.id is None:
        return None
    tag = session.exec(
        select(RankingTagItemRecord).where(
            RankingTagItemRecord.snapshot_id == snapshot.id,
            RankingTagItemRecord.shape == shape,
            RankingTagItemRecord.tag_code == tag_code,
        )
    ).first()
    if tag is None:
        return None
    visible = [
        (item, article) for item, article in _visible_rows(session, snapshot.id, shape)
        if item.tag_code == tag_code
    ]
    visible.sort(key=lambda pair: (pair[0].content_rank, pair[1].id))
    if len({article.id for _, article in visible}) < MIN_TAG_CONTENT_SUPPORT:
        return None
    return {
        "scope": PUBLIC_SCOPE,
        "snapshot_date": snapshot.snapshot_date,
        "shape": shape,
        "axis": tag.axis,
        "tag": {
            "code": tag.tag_code,
            "name": tag.tag_name_zh or tag.tag_name_en or tag.tag_code,
        },
        "contents": [_content_payload(item, article) for item, article in visible[:TOP_CONTENT]],
    }


def read_history(
    session: Session,
    *,
    tag_code: str,
    shape: str,
    days: int,
) -> dict[str, Any]:
    snapshots = session.exec(
        select(RankingSnapshotRecord)
        .order_by(RankingSnapshotRecord.snapshot_date.desc())
        .limit(days)
    ).all()
    points: list[dict[str, Any]] = []
    for snapshot in reversed(snapshots):
        if snapshot.id is None:
            continue
        tag = session.exec(
            select(RankingTagItemRecord).where(
                RankingTagItemRecord.snapshot_id == snapshot.id,
                RankingTagItemRecord.shape == shape,
                RankingTagItemRecord.tag_code == tag_code,
            )
        ).first()
        if tag is None:
            continue
        visible = [
            (item, article) for item, article in _visible_rows(session, snapshot.id, shape)
            if item.tag_code == tag_code
        ]
        content_count = len({article.id for _, article in visible})
        source_count = len({article.source_id for _, article in visible})
        if content_count < MIN_TAG_CONTENT_SUPPORT:
            continue
        points.append({
            "date": snapshot.snapshot_date,
            "occurrence_count": content_count,
            "distinct_source_count": source_count,
            "rank": tag.rank,
        })
    return {
        "scope": PUBLIC_SCOPE,
        "tag_code": tag_code,
        "shape": shape,
        "points": points,
    }


__all__ = [
    "AXES", "PUBLIC_SCOPE", "SHAPES", "RankingSnapshotBusy", "build_snapshot",
    "build_snapshot_if_idle", "ensure_snapshot_if_empty", "latest_snapshot_needed",
    "read_history", "read_rankings", "read_tag_contents", "snapshot_boundary",
    "snapshot_status",
]
