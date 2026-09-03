"""WP-3 personal-digest scope, orchestration and snapshot tests."""

import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import DigestGenerationReason  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserInterestTagRecord,
    UserRecord,
)
from services.digest_selection import DigestSelectionPolicy  # noqa: E402
from services.personal_digest import (  # noqa: E402
    calculate_due_source_ids,
    claim_personal_digest_generation,
    freeze_personal_digest_scope,
    generate_personal_digest,
    resolve_personal_digest_source_ids,
    start_personal_digest_edition,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 1, 8, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
NOW_ISO = NOW.isoformat()


@pytest.fixture
def storage(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'personal-digest.db'}")
    yield value
    value.engine.dispose()


def _user(username: str = "alice", *, role: str = "user") -> UserRecord:
    return UserRecord(
        username=username,
        password_hash="hash",
        role=role,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _subscribe(
    username: str,
    source_ids: str | None,
    *,
    active: bool = True,
    token: str = "token",
) -> ReaderSubscriptionRecord:
    filters = {} if source_ids is None else {"source_ids": source_ids}
    return ReaderSubscriptionRecord(
        owner_username=username,
        name=f"sub-{token}",
        filters_json=json.dumps(filters),
        token_hash=token,
        is_active=active,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _article(
    number: int,
    *,
    source: str = "rss_a",
    published_at: dt.datetime | None = None,
    score: float | None = 8.0,
    tagging_status: str = "succeeded",
) -> tuple[ArticleRecord, ArticleAnalysisRecord | None]:
    article_id = f"article-{number:02d}"
    published_at = published_at or NOW - dt.timedelta(hours=number)
    article = ArticleRecord(
        id=article_id,
        title=f"Article {number}",
        content_type="rss_article",
        source_id=source,
        source_url=f"https://example.com/{article_id}",
        publish_date=published_at.isoformat(),
        fetched_date=published_at.isoformat(),
        content=f"full body {number}",
    )
    if score is None:
        return article, None
    analysis = ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        tagging_status=tagging_status,
        quality_score=score,
        score_reason=f"reason {number}",
        one_sentence_summary=f"one sentence {number}",
        summary=f"summary {number}",
        content_genre="industry_news",
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )
    return article, analysis


def _seed_article(session: Session, *args, **kwargs) -> ArticleRecord:
    article, analysis = _article(*args, **kwargs)
    session.add(article)
    session.flush()
    if analysis is not None:
        session.add(analysis)
    return article


def _tag(code: str) -> CmsTagRecord:
    return CmsTagRecord(
        code=code,
        kind="topic",
        name_zh=code,
        normalized_name=code,
        status="active",
        user_selectable=True,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def test_scope_uses_only_explicit_active_visible_memberships_even_for_admin(storage):
    private_id = "user_rss_private01"
    with Session(storage.engine) as session:
        session.add(_user("admin", role="admin"))
        session.add_all([
            _subscribe("admin", None, token="global-empty"),
            _subscribe("admin", "rss_visible,rss_hidden", token="public"),
            _subscribe("admin", private_id, token="private"),
            _subscribe("admin", "user_rss_orphan", token="orphan"),
            _subscribe("admin", "rss_inactive", active=False, token="inactive"),
            SourceConfigRecord(
                source_id=private_id,
                name="private",
                owner_username="someone-else",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            AppSettingRecord(
                key="reader_hidden_source_ids",
                value=json.dumps(["rss_hidden"]),
            ),
        ])
        session.commit()

        resolved = resolve_personal_digest_source_ids(session, "admin")

    assert resolved == ["rss_visible", private_id]
    assert "rss_inactive" not in resolved
    assert "user_rss_orphan" not in resolved


def test_expected_and_due_are_frozen_separately_with_private_freshness(storage):
    fresh_private = "user_rss_fresh"
    stale_private = "user_rss_stale"
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", f"rss_public,{fresh_private},{stale_private}"))
        for source_id in (fresh_private, stale_private):
            session.add(SourceConfigRecord(
                source_id=source_id,
                name=source_id,
                owner_username="alice",
                fetch_interval_minutes=60,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ))
        session.add_all([
            SourceStateRecord(
                source_id=fresh_private,
                fetcher_id="generic_rss",
                status="healthy",
                last_success_at=(NOW - dt.timedelta(minutes=30)).isoformat(),
                updated_at=NOW_ISO,
            ),
            SourceStateRecord(
                source_id=stale_private,
                fetcher_id="generic_rss",
                status="healthy",
                last_success_at=(NOW - dt.timedelta(hours=2)).isoformat(),
                updated_at=NOW_ISO,
            ),
        ])
        session.commit()

        due = calculate_due_source_ids(
            session,
            ["rss_public", fresh_private, stale_private],
            as_of=NOW,
            scheduled_source_ids=["rss_public"],
        )
        frozen = freeze_personal_digest_scope(
            session, "alice", as_of=NOW, scheduled_source_ids=["rss_public"]
        )

    assert due == ["rss_public", stale_private]
    assert frozen.expected_source_ids == ("rss_public", fresh_private, stale_private)
    assert frozen.due_source_ids == ("rss_public", stale_private)
    assert frozen.source_state_snapshot[fresh_private]["due"] is False


def test_no_subscriptions_returns_recognizable_state_without_creating_edition(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "empty_subscriptions"
        assert result.edition is None
        assert session.exec(select(PersonalDigestEditionRecord)).all() == []


def test_last_unsubscribe_supersedes_pending_and_first_open_stays_empty(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        subscription = _subscribe("alice", "rss_a")
        session.add(subscription)
        session.commit()

        pending = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.SUBSCRIPTION_CHANGED,
        )
        assert pending.edition is not None
        pending_id = pending.edition.id

        session.delete(subscription)
        session.commit()
        changed = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            generation_reason=DigestGenerationReason.SUBSCRIPTION_CHANGED,
        )
        reopened = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=2),
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=NOW + dt.timedelta(minutes=2),
        )

        assert changed.status == "empty_subscriptions"
        assert changed.edition is None
        assert reopened.status == "empty_subscriptions"
        assert reopened.edition is None
        assert session.get(PersonalDigestEditionRecord, pending_id).status == "superseded"


def test_last_unsubscribe_also_supersedes_a_ready_today_edition(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        subscription = _subscribe("alice", "rss_a")
        session.add(subscription)
        _seed_article(session, 1, score=8.5)
        session.commit()
        ready = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=1),
        )
        ready_id = int(ready.edition.id)

        session.delete(subscription)
        session.commit()
        changed = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            generation_reason=DigestGenerationReason.SUBSCRIPTION_CHANGED,
        )

        assert changed.status == "empty_subscriptions"
        assert session.get(PersonalDigestEditionRecord, ready_id).status == "superseded"


def test_quality_only_generation_expands_36_to_72_hours_and_is_idempotent(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a,rss_b"))
        _seed_article(session, 1, source="rss_a", score=9.0)
        _seed_article(session, 2, source="rss_b", score=8.5)
        older = _seed_article(
            session,
            3,
            source="rss_a",
            score=8.0,
            published_at=NOW - dt.timedelta(hours=50),
        )
        session.commit()

        policy = DigestSelectionPolicy(target_items=3)
        first = generate_personal_digest(session, "alice", now=NOW, policy=policy)
        second = generate_personal_digest(session, "alice", now=NOW, policy=policy)

        assert first.status == "ready"
        assert len(first.items) == 3
        assert older.id in {item.article_id for item in first.items}
        assert {item.selection_lane for item in first.items} == {"quality"}
        assert first.edition.id == second.edition.id
        assert json.loads(first.edition.expected_source_ids_json) == ["rss_a", "rss_b"]
        assert session.exec(select(PersonalDigestEditionRecord)).all() == [first.edition]


def test_candidate_window_normalizes_utc_naive_and_excludes_future_times(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        utc_within = _seed_article(
            session,
            20,
            score=9.0,
            published_at=NOW.astimezone(dt.timezone.utc) - dt.timedelta(hours=30),
        )
        naive_within = _seed_article(
            session,
            21,
            score=8.5,
            published_at=(NOW - dt.timedelta(hours=4)).replace(tzinfo=None),
        )
        future = _seed_article(
            session,
            22,
            score=10.0,
            published_at=NOW.astimezone(dt.timezone.utc) + dt.timedelta(hours=4),
        )
        session.commit()

        result = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=3),
        )

        assert {item.article_id for item in result.items} == {
            utc_within.id,
            naive_within.id,
        }
        assert future.id not in {item.article_id for item in result.items}


def test_no_qualified_content_is_honest_degraded_latest_five(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        for number in range(7):
            _seed_article(session, number, score=6.5 if number % 2 else None)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert result.edition.degraded_reason == "no_qualified_content"
        assert len(result.items) == 5
        assert all(item.section == "订阅源最新更新" for item in result.items)
        assert all(item.selection_reason == "" for item in result.items)
        assert all(json.loads(item.snapshot_json)["is_latest_update"] for item in result.items)


def test_mute_excludes_matches_and_unfinished_tagging_from_degraded_area(storage):
    with Session(storage.engine) as session:
        user = _user()
        tag = _tag("muted")
        session.add_all([user, tag, _subscribe("alice", "rss_a")])
        session.flush()
        muted = _seed_article(session, 1, score=6.5, tagging_status="succeeded")
        unfinished = _seed_article(session, 2, score=6.5, tagging_status="pending")
        safe = _seed_article(session, 3, score=6.5, tagging_status="succeeded")
        session.flush()
        session.add_all([
            ArticleTagAssignmentRecord(
                article_id=muted.id,
                tag_id=tag.id,
                tag_kind="topic",
                relevance=0.9,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            UserInterestTagRecord(
                owner_username="alice",
                tag_id=tag.id,
                stance="mute",
                priority="normal",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
        ])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert [item.article_id for item in result.items] == [safe.id]
        assert muted.id not in {item.article_id for item in result.items}
        assert unfinished.id not in {item.article_id for item in result.items}


def test_interest_changed_creates_today_revision_and_history_is_immutable(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a,rss_b"))
        for number in range(4):
            _seed_article(session, number, source=f"rss_{number % 2}", score=8.0 + number / 10)
        session.commit()

        first = generate_personal_digest(
            session, "alice", now=NOW, policy=DigestSelectionPolicy(target_items=2)
        )
        second = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.INTEREST_CHANGED,
            policy=DigestSelectionPolicy(target_items=2),
        )

        assert first.edition.revision == 1
        assert second.edition.revision == 2
        assert second.edition.generation_reason == "interest_changed"
        with pytest.raises(ValueError, match="历史日期"):
            generate_personal_digest(
                session,
                "alice",
                now=NOW,
                report_date="2026-08-31",
                generation_reason=DigestGenerationReason.INTEREST_CHANGED,
            )


def test_new_revision_supersedes_older_pending_lifecycle(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        session.commit()

        first = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=NOW,
        )
        second = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            generation_reason=DigestGenerationReason.INTEREST_CHANGED,
        )

        session.refresh(first.edition)
        assert first.edition.status == "superseded"
        assert second.edition.status == "pending"
        assert second.edition.revision == first.edition.revision + 1


def test_pending_edition_freezes_interests_before_later_preference_changes(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a"), _tag("agents")])
        session.flush()
        tag = session.exec(select(CmsTagRecord)).one()
        interested = _seed_article(session, 1, score=9.0)
        quality = _seed_article(session, 2, score=8.5)
        session.flush()
        interest = UserInterestTagRecord(
            owner_username="alice",
            tag_id=tag.id,
            stance="follow",
            priority="normal",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
        session.add_all([
            interest,
            ArticleTagAssignmentRecord(
                article_id=interested.id,
                tag_id=tag.id,
                tag_kind="topic",
                relevance=0.95,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
        ])
        session.commit()

        pending = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=NOW,
        )
        frozen = json.loads(pending.edition.interest_snapshot_json)
        assert frozen == [{"tag_code": "agents", "stance": "follow", "priority": "normal"}]

        interest.stance = "mute"
        interest.updated_at = (NOW + dt.timedelta(minutes=1)).isoformat()
        session.add(interest)
        session.commit()
        result = generate_personal_digest(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            pending_edition_id=pending.edition.id,
            policy=DigestSelectionPolicy(target_items=2),
        )

        assert result.status == "ready"
        by_id = {item.article_id: item for item in result.items}
        assert by_id[interested.id].selection_lane == "interest"
        assert by_id[quality.id].selection_lane == "quality"


def test_expired_digest_lease_reclaim_rejects_stale_generation_token(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        _seed_article(session, 1, score=8.5)
        session.commit()
        pending = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=NOW,
        )
        edition_id = int(pending.edition.id)
        _edition, stale_token = claim_personal_digest_generation(
            session, edition_id, now=NOW
        )
        assert stale_token
        session.get(PersonalDigestEditionRecord, edition_id).generation_lease_expires_at = (
            NOW - dt.timedelta(seconds=1)
        ).isoformat()
        session.commit()
        _edition, fresh_token = claim_personal_digest_generation(
            session, edition_id, now=NOW + dt.timedelta(minutes=1)
        )
        assert fresh_token and fresh_token != stale_token

        stale = generate_personal_digest(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            pending_edition_id=edition_id,
            generation_token=stale_token,
        )
        assert stale.status == "generating"
        assert stale.items == ()

        fresh = generate_personal_digest(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            pending_edition_id=edition_id,
            generation_token=fresh_token,
        )
        assert fresh.status == "ready"


def test_generating_edition_recovery_replaces_partial_snapshot_rows(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        session.add(
            SourceStateRecord(
                source_id="rss_a",
                fetcher_id="rss_a",
                status="healthy",
                last_completed_at=NOW_ISO,
                last_success_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        for number in range(1, 4):
            _seed_article(session, number, score=8.0 + number / 10)
        session.commit()

        pending = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=NOW,
        )
        assert pending.edition is not None
        edition_id = pending.edition.id
        first = generate_personal_digest(
            session,
            "alice",
            report_date=NOW.date().isoformat(),
            now=NOW,
            pending_edition_id=edition_id,
        )
        assert first.status == "ready"
        first_article_ids = [item.article_id for item in first.items]

        # Recovery happens in a fresh worker session after a restart.
        session.expunge_all()
        edition = session.get(PersonalDigestEditionRecord, edition_id)
        edition.status = "generating"
        session.add(edition)
        session.commit()

        recovered = generate_personal_digest(
            session,
            "alice",
            report_date=NOW.date().isoformat(),
            now=NOW + dt.timedelta(minutes=1),
            pending_edition_id=edition_id,
        )
        assert recovered.status == "ready"
        assert [item.article_id for item in recovered.items] == first_article_ids
        persisted = session.exec(
            select(PersonalDigestItemRecord)
            .where(PersonalDigestItemRecord.edition_id == edition_id)
            .order_by(PersonalDigestItemRecord.position)
        ).all()
        assert [item.article_id for item in persisted] == first_article_ids


def test_yesterdays_exact_article_is_not_reused_in_normal_or_fallback_selection(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        repeated = _seed_article(session, 1, score=9.5)
        fresh = _seed_article(session, 2, score=8.0)
        yesterday = PersonalDigestEditionRecord(
            owner_username="alice",
            report_date="2026-08-31",
            revision=1,
            status="ready",
            check_after="2026-08-31T08:30:00+08:00",
            cutoff_at="2026-08-31T08:30:00+08:00",
            generated_at="2026-08-31T08:30:00+08:00",
            created_at="2026-08-31T08:30:00+08:00",
            updated_at="2026-08-31T08:30:00+08:00",
        )
        session.add(yesterday)
        session.flush()
        session.add(PersonalDigestItemRecord(
            edition_id=yesterday.id,
            article_id=repeated.id,
            position=0,
            selection_lane="quality",
            quality_score_snapshot=9.5,
            snapshot_json="{}",
            created_at="2026-08-31T08:30:00+08:00",
        ))
        session.commit()

        result = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=2),
        )

        assert result.status == "ready"
        assert [item.article_id for item in result.items] == [fresh.id]


def test_item_keeps_full_snapshot_after_article_is_physically_deleted(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        article = _seed_article(session, 1, score=9.0)
        session.commit()

        result = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=1),
        )
        item_id = result.items[0].id
        snapshot_before = json.loads(result.items[0].snapshot_json)
        session.delete(session.get(ArticleRecord, article.id))
        session.commit()
        session.expire_all()

        item = session.get(PersonalDigestItemRecord, item_id)
        assert item.article_id is None
        assert json.loads(item.snapshot_json) == snapshot_before
        assert "content" not in snapshot_before
        assert snapshot_before["title"] == "Article 1"
