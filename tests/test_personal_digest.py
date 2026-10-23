"""WP-3 personal-digest scope, orchestration and snapshot tests."""

import datetime as dt
import json
import os
import sys

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import DigestGenerationReason  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    EventMemberRecord,
    EventRecord,
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
    BREAKING_MAX_ITEMS_KEY,
    calculate_due_source_ids,
    claim_personal_digest_generation,
    freeze_personal_digest_scope,
    generate_personal_digest,
    materialize_desired_personal_digest_revision,
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
    genre: str = "industry_news",
    content_type: str = "rss_article",
) -> tuple[ArticleRecord, ArticleAnalysisRecord | None]:
    article_id = f"article-{number:02d}"
    published_at = published_at or NOW - dt.timedelta(hours=number)
    article = ArticleRecord(
        id=article_id,
        title=f"Article {number}",
        content_type=content_type,
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
        summary=f"summary {number}",
        content_genre=genre,
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


def test_scope_only_exempts_public_podcast_from_legacy_source_active_gate(storage):
    source_ids = (
        "rss_public_inactive",
        "podcast_public_inactive",
        "podcast_private_inactive",
    )
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", ",".join(source_ids)))
        session.add_all([
            SourceConfigRecord(
                source_id="rss_public_inactive",
                name="Inactive public RSS",
                source_type="rss",
                is_active=False,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            SourceConfigRecord(
                source_id="podcast_public_inactive",
                name="Legacy inactive public podcast",
                source_type="podcast",
                is_active=False,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            SourceConfigRecord(
                source_id="podcast_private_inactive",
                name="Inactive private podcast",
                source_type="podcast",
                owner_username="alice",
                is_active=False,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
        ])
        session.commit()

        resolved = resolve_personal_digest_source_ids(session, "alice")
        due = calculate_due_source_ids(
            session,
            source_ids,
            as_of=NOW,
            scheduled_source_ids=source_ids,
        )

    assert resolved == ["podcast_public_inactive"]
    assert due == ["podcast_public_inactive"]


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


def test_public_daily_brief_is_neither_in_scope_nor_due(storage):
    """issue #74:公共日报不是早报素材——范围解析剔除它,就绪等待集也不再给它特例。"""

    with Session(storage.engine) as session:
        session.add_all([
            _user(),
            _subscribe("alice", "dorami_daily_brief,rss_a"),
            AppSettingRecord(key="daily_brief_enabled", value="true"),
        ])
        session.commit()

        assert resolve_personal_digest_source_ids(session, "alice") == ["rss_a"]
        assert resolve_personal_digest_source_ids(session, "nobody") == []
        due = calculate_due_source_ids(
            session,
            ["dorami_daily_brief"],
            as_of=NOW,
            scheduled_source_ids=[],
        )

    assert due == []


def test_remote_authority_public_source_is_due_without_local_collection_job(storage):
    with Session(storage.engine) as session:
        session.add_all([
            SourceConfigRecord(
                source_id="remote-by-config",
                name="Remote Config",
                collection_authority_id="producer-a",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            SourceConfigRecord(
                source_id="remote-by-state",
                name="Remote State",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            SourceStateRecord(
                source_id="remote-by-state",
                fetcher_id="generic_rss",
                authority_id="producer-a",
                updated_at=NOW_ISO,
            ),
        ])
        session.commit()

        due = calculate_due_source_ids(
            session,
            ["remote-by-config", "remote-by-state"],
            as_of=NOW,
            scheduled_source_ids=[],
        )

    assert due == ["remote-by-config", "remote-by-state"]


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
            _seed_article(session, number, score=4.5 if number % 2 else None)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert result.edition.degraded_reason == "no_qualified_content"
        assert len(result.items) == 5
        assert all(item.section == "订阅源最新更新" for item in result.items)
        assert all(item.selection_reason == "订阅源最新更新，不计入正式精选。" for item in result.items)
        assert all(json.loads(item.snapshot_json)["is_latest_update"] for item in result.items)


def test_interest_only_pool_reports_unfillable_ratio_and_keeps_fallback_lanes_honest(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), _subscribe("alice", "rss_a"), tag])
        session.flush()
        for number in range(1, 10):
            article = _seed_article(session, number, score=9.0 - number / 10)
            session.flush()
            session.add(ArticleTagAssignmentRecord(
                article_id=article.id,
                tag_id=tag.id,
                tag_kind="topic",
                relevance=0.9,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ))
        session.add(UserInterestTagRecord(
            owner_username="alice",
            tag_id=tag.id,
            stance="follow",
            priority="normal",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert result.edition.degraded_reason == "insufficient_non_interest_content"
        assert len(result.items) == 5
        assert {item.selection_lane for item in result.items} == {"interest"}
        assert all(json.loads(item.matched_interest_codes_json) == ["agents"] for item in result.items)
        assert all("不计入正式精选" in item.selection_reason for item in result.items)


def test_degraded_area_does_not_wait_for_tagging(storage):
    """v3.56(issue #27)屏蔽退役后,「有屏蔽时等打标完成」的就绪依赖随之消失:
    设了关注的读者,打标未完成的文章照常进入降级区。"""
    with Session(storage.engine) as session:
        user = _user()
        tag = _tag("agents")
        session.add_all([user, tag, _subscribe("alice", "rss_a")])
        session.flush()
        unfinished = _seed_article(session, 2, score=4.5, tagging_status="pending")
        finished = _seed_article(session, 3, score=4.5, tagging_status="succeeded")
        session.flush()
        session.add(
            UserInterestTagRecord(
                owner_username="alice",
                tag_id=tag.id,
                stance="follow",
                priority="normal",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
        )
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert {item.article_id for item in result.items} == {unfinished.id, finished.id}


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


def test_new_trigger_refreshes_existing_pending_lifecycle_in_place(storage):
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
        assert first.edition.status == "pending"
        assert second.edition.id == first.edition.id
        assert second.edition.status == "pending"
        assert second.edition.revision == first.edition.revision
        assert second.edition.generation_reason == "interest_changed"
        assert len(session.exec(select(PersonalDigestEditionRecord)).all()) == 1


def test_generating_triggers_persist_and_materialize_one_coalesced_successor(storage):
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
        _edition, generation_token = claim_personal_digest_generation(
            session, edition_id, now=NOW
        )
        assert generation_token

        first_trigger = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=1),
            generation_reason=DigestGenerationReason.MANUAL_REBUILD,
        )
        latest_trigger = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=2),
            generation_reason=DigestGenerationReason.INTEREST_CHANGED,
        )
        assert first_trigger.edition.id == edition_id
        assert latest_trigger.edition.id == edition_id
        assert latest_trigger.edition.desired_generation_reason == "interest_changed"
        assert latest_trigger.edition.desired_requested_at is not None

        completed = generate_personal_digest(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=3),
            pending_edition_id=edition_id,
            generation_token=generation_token,
            policy=DigestSelectionPolicy(target_items=1),
        )
        assert completed.status == "ready"
        session.expunge_all()  # the desired intent must survive worker restart
        carrier = session.get(PersonalDigestEditionRecord, edition_id)
        successor = materialize_desired_personal_digest_revision(
            session,
            carrier,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert successor is not None
        assert successor.edition.revision == completed.edition.revision + 1
        assert successor.edition.status == "pending"
        assert successor.edition.generation_reason == "interest_changed"
        assert materialize_desired_personal_digest_revision(
            session,
            session.get(PersonalDigestEditionRecord, edition_id),
            now=NOW + dt.timedelta(seconds=5),
        ) is None
        editions = session.exec(
            select(PersonalDigestEditionRecord).order_by(PersonalDigestEditionRecord.revision)
        ).all()
        assert len(editions) == 2
        assert editions[0].desired_requested_at is None


def test_revision_insert_race_reenters_and_merges_losing_request(storage, monkeypatch):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        _seed_article(session, 1, score=8.5)
        session.commit()
        generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=1),
        )

        original_commit = session.commit
        injected = False

        def commit_with_competing_winner():
            nonlocal injected
            pending = next(
                (
                    row for row in session.new
                    if isinstance(row, PersonalDigestEditionRecord) and row.revision == 2
                ),
                None,
            )
            if injected or pending is None:
                return original_commit()
            injected = True
            values = {
                column.name: getattr(pending, column.name)
                for column in PersonalDigestEditionRecord.__table__.columns
                if column.name != "id"
            }
            values["generation_reason"] = DigestGenerationReason.MANUAL_REBUILD.value
            session.rollback()
            session.add(PersonalDigestEditionRecord(**values))
            original_commit()
            raise IntegrityError("simulated revision race", {}, RuntimeError("duplicate"))

        monkeypatch.setattr(session, "commit", commit_with_competing_winner)
        result = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            generation_reason=DigestGenerationReason.INTEREST_CHANGED,
        )

        assert injected is True
        assert result.edition.revision == 2
        assert result.edition.status == "pending"
        assert result.edition.generation_reason == "interest_changed"
        assert len(session.exec(select(PersonalDigestEditionRecord)).all()) == 2


def test_stale_carrier_intent_does_not_overwrite_newer_revision(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        _seed_article(session, 1, score=8.5)
        session.commit()
        pending = start_personal_digest_edition(
            session,
            "alice",
            now=NOW,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
        )
        carrier_id = int(pending.edition.id)
        _edition, token = claim_personal_digest_generation(session, carrier_id, now=NOW)
        assert token is not None
        start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=1),
            generation_reason=DigestGenerationReason.MANUAL_REBUILD,
        )
        generate_personal_digest(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=2),
            pending_edition_id=carrier_id,
            generation_token=token,
            policy=DigestSelectionPolicy(target_items=1),
        )
        newer = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(seconds=3),
            generation_reason=DigestGenerationReason.INTEREST_CHANGED,
        ).edition
        newer_id = int(newer.id)
        assert newer.revision == 2
        assert newer.generation_reason == "interest_changed"

        session.expunge_all()
        carrier = session.get(PersonalDigestEditionRecord, carrier_id)
        assert materialize_desired_personal_digest_revision(
            session,
            carrier,
            now=NOW + dt.timedelta(seconds=4),
        ) is None
        refreshed_newer = session.get(PersonalDigestEditionRecord, newer_id)
        assert refreshed_newer.status == "pending"
        assert refreshed_newer.generation_reason == "interest_changed"
        assert session.get(PersonalDigestEditionRecord, carrier_id).desired_requested_at is None


def test_unrelated_visibility_fanout_reuses_same_frozen_subscription_scope(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        _seed_article(session, 1, score=8.5)
        session.commit()
        ready = generate_personal_digest(
            session,
            "alice",
            now=NOW,
            policy=DigestSelectionPolicy(target_items=1),
        )

        unchanged = start_personal_digest_edition(
            session,
            "alice",
            now=NOW + dt.timedelta(minutes=1),
            generation_reason=DigestGenerationReason.SUBSCRIPTION_CHANGED,
        )

        assert unchanged.edition.id == ready.edition.id
        assert unchanged.edition.revision == ready.edition.revision



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

        # 冻结之后取消关注:待生成的版本仍按冻结快照(agents 关注)编排
        session.delete(interest)
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


# ── 「重大事件」通道(v3.50,issue #33 §2)──

def _entity_tag(code: str) -> CmsTagRecord:
    return CmsTagRecord(
        code=code,
        kind="entity",
        name_zh=code,
        normalized_name=code,
        status="active",
        user_selectable=True,
        entity_type="organization",
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _assign(article: ArticleRecord, tag: CmsTagRecord) -> ArticleTagAssignmentRecord:
    return ArticleTagAssignmentRecord(
        article_id=article.id,
        tag_id=tag.id,
        tag_kind=tag.kind,
        relevance=0.9,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def test_breaking_official_headline_comes_from_outside_the_subscription(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        tag = _entity_tag("entity.openai")
        session.add(tag)
        session.flush()
        own = _seed_article(session, 1, score=8.0)
        # 注册表官方源(rss_openai_news 是 registry preset,role=official/shape=article),
        # alice 没订它;分数过线即以「重大事件」通道进入头条位。
        headline = _seed_article(session, 2, source="rss_openai_news", score=9.6)
        tweet = _seed_article(session, 3, source="x_openai", score=9.8)
        lone_media = _seed_article(session, 4, source="web_aiera", score=9.4)
        session.flush()
        session.add_all([_assign(headline, tag), _assign(tweet, tag)])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        assert [item.article_id for item in result.items] == [headline.id, own.id]
        first, second = result.items
        assert first.position == 0 and first.selection_lane == "breaking"
        assert first.section == "重大事件"
        assert "不在你的订阅内" in first.selection_reason
        features = json.loads(first.ranking_features_json)
        assert features["breaking"] == {"basis": "official", "source_count": 2, "subscribed": False}
        assert features["event_entity_codes"] == ["entity.openai"]
        assert json.loads(first.snapshot_json)["source_name"]
        assert second.position == 1 and second.selection_lane == "quality"
        assert lone_media.id not in {item.article_id for item in result.items}


def test_breaking_lane_skips_hidden_and_private_sources_and_can_be_switched_off(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value=json.dumps(["rss_openai_news"])))
        session.add(SourceConfigRecord(
            source_id="user_rss_private01",
            name="private",
            source_type="rss",
            owner_username="bob",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.flush()
        own = _seed_article(session, 1, score=8.0)
        _seed_article(session, 2, source="rss_openai_news", score=9.9)
        _seed_article(session, 3, source="user_rss_private01", score=9.9)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)
        assert [item.article_id for item in result.items] == [own.id]

        # 隐藏解除后头条出现;KV 条数 0 关闭通道后又消失(同日重编排取新版本)。
        session.delete(session.get(AppSettingRecord, "reader_hidden_source_ids"))
        session.commit()
        rebuilt = generate_personal_digest(
            session, "alice", now=NOW, generation_reason=DigestGenerationReason.MANUAL_REBUILD
        )
        assert [item.selection_lane for item in rebuilt.items] == ["breaking", "quality"]

        session.add(AppSettingRecord(key=BREAKING_MAX_ITEMS_KEY, value="0"))
        session.commit()
        disabled = generate_personal_digest(
            session, "alice", now=NOW, generation_reason=DigestGenerationReason.MANUAL_REBUILD
        )
        assert [item.selection_lane for item in disabled.items] == ["quality"]


def test_breaking_same_entity_is_suppressed_after_a_recent_headline_and_empty_scope_stays_empty(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _user("bob"), _subscribe("alice", "rss_a")])
        tag = _entity_tag("entity.openai")
        session.add(tag)
        session.flush()
        own = _seed_article(session, 1, score=8.0)
        followup = _seed_article(session, 2, source="rss_openai_news", score=9.7)
        session.flush()
        session.add(_assign(followup, tag))
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
            article_id=None,
            position=0,
            section="重大事件",
            selection_lane="breaking",
            quality_score_snapshot=9.8,
            ranking_features_json=json.dumps({"event_entity_codes": ["entity.openai", "entity.chatgpt"]}),
            snapshot_json="{}",
            created_at="2026-08-31T08:30:00+08:00",
        ))
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)
        assert [item.article_id for item in result.items] == [own.id]

        # 没有订阅的读者不会因为重大事件通道凭空得到早报。
        empty = generate_personal_digest(session, "bob", now=NOW)
        assert empty.status == "empty_subscriptions"
        assert empty.edition is None


def test_breaking_lane_excludes_owner_scoped_legacy_sources(storage):
    """非 user_rss_ 前缀但带 owner_username 的存量私有源同样不进全读者共享的头条池(codex P1)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.add(SourceConfigRecord(
            source_id="legacy_private_feed",
            name="legacy private",
            source_type="rss",
            source_scope="",
            provenance_tier="",
            owner_username="bob",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.flush()
        own = _seed_article(session, 1, score=8.0)
        _seed_article(session, 2, source="legacy_private_feed", score=9.9)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)
        assert [item.article_id for item in result.items] == [own.id]


def test_breaking_qualification_ignores_subscription_and_promotes_selected_article(storage):
    """头条资格按全池判定:读者已订阅的官方源过线稿从精选提级为头条,不重复、不算降级(codex P2)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_openai_news")])
        tag = _entity_tag("entity.openai")
        session.add(tag)
        session.flush()
        own = _seed_article(session, 1, score=8.0)
        headline = _seed_article(session, 2, source="rss_openai_news", score=9.6)
        session.flush()
        session.add(_assign(headline, tag))
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        assert [(item.article_id, item.selection_lane) for item in result.items] == [
            (headline.id, "breaking"), (own.id, "quality"),
        ]
        first = result.items[0]
        assert first.selection_reason.endswith("官方一手发布。")
        assert json.loads(first.ranking_features_json)["breaking"]["subscribed"] is True


def test_promoted_headline_does_not_consume_a_curated_slot(storage):
    """精选已满 target 时头条提级后按同一策略重选,精选仍满员——头条额外于 target 之上(codex P2)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_openai_news")])
        tag = _entity_tag("entity.openai")
        session.add(tag)
        session.flush()
        headline = _seed_article(session, 1, source="rss_openai_news", score=9.6)
        others = [_seed_article(session, n, score=8.0 - n * 0.1) for n in range(2, 6)]
        session.flush()
        session.add(_assign(headline, tag))
        session.commit()

        result = generate_personal_digest(
            session, "alice", now=NOW, policy=DigestSelectionPolicy(target_items=3, per_source_max=5)
        )

        lanes = [(item.article_id, item.selection_lane) for item in result.items]
        assert lanes[0] == (headline.id, "breaking")
        assert [lane for _id, lane in lanes[1:]] == ["quality", "quality", "quality"]
        assert {aid for aid, _lane in lanes[1:]} == {a.id for a in others[:3]}


# ── v3.54「订阅 ∪ 兴趣」(issue #33 §3 前置) ──


def _assign_topic(session: Session, article: ArticleRecord, tag: CmsTagRecord, *, relevance: float = 0.9, primary: bool = False) -> None:
    session.add(ArticleTagAssignmentRecord(
        article_id=article.id,
        tag_id=tag.id,
        tag_kind="topic",
        is_primary=primary,
        relevance=relevance,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    ))


def _follow(session: Session, username: str, tag: CmsTagRecord) -> None:
    session.add(UserInterestTagRecord(
        owner_username=username,
        tag_id=tag.id,
        stance="follow",
        priority="normal",
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    ))


def test_interest_lane_reaches_outside_the_subscription_with_the_higher_floor(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), _subscribe("alice", "rss_a"), tag])
        session.flush()
        # 订阅内:6 篇质量稿(不命中)
        for number in range(1, 7):
            _seed_article(session, number, source="rss_a", score=7.0)
        # 订阅外命中:一篇 6.5 进、一篇 5.5 不进(订阅外门槛 6.0)、一篇相关度 0.5 的弱指派不算命中
        outside_hit = _seed_article(session, 11, source="rss_outside", score=6.5)
        outside_low = _seed_article(session, 12, source="rss_outside", score=5.5)
        weak = _seed_article(session, 13, source="rss_outside2", score=8.0)
        session.flush()
        _assign_topic(session, outside_hit, tag)
        _assign_topic(session, outside_low, tag)
        _assign_topic(session, weak, tag, relevance=0.5)
        _follow(session, "alice", tag)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        by_id = {item.article_id: item for item in result.items}
        assert "article-11" in by_id and "article-12" not in by_id and "article-13" not in by_id
        item = by_id["article-11"]
        assert item.selection_lane == "interest"
        assert json.loads(item.matched_interest_codes_json) == ["agents"]
        snapshot = json.loads(item.snapshot_json)
        assert snapshot["subscribed"] is False
        assert "来自你未订阅的" in item.selection_reason
        assert json.loads(item.ranking_features_json)["subscribed"] is False
        assert all(json.loads(by_id[f"article-{n:02d}"].snapshot_json)["subscribed"] is True for n in range(1, 7))
        # 冻结的权限边界仍只有订阅源
        assert json.loads(result.edition.expected_source_ids_json) == ["rss_a"]
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["external_candidate_count"] == 1
        assert stats["subscribed_candidate_count"] == 6
        assert stats["interest_hits"] == 1 and stats["external_hits"] == 1
        assert stats["followed_count"] == 1 and stats["interest_only"] is False
        assert stats["external_min_score"] == 6.0


def test_outside_pool_skips_hidden_private_and_daily_brief_sources(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), _subscribe("alice", "rss_a"), tag])
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value=json.dumps(["rss_hidden"])))
        session.add(SourceConfigRecord(
            source_id="legacy_private",
            name="legacy",
            source_type="rss",
            fetcher_id="generic_rss",
            owner_username="bob",
            is_active=True,
            params_json="{}",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.flush()
        for number in range(1, 7):
            _seed_article(session, number, source="rss_a", score=7.0)
        rows = [
            _seed_article(session, 21, source="rss_hidden", score=9.0),
            _seed_article(session, 22, source="user_rss_abc", score=9.0),
            _seed_article(session, 23, source="legacy_private", score=9.0),
            _seed_article(session, 24, source="dorami_daily_brief", score=9.0),
            _seed_article(session, 25, source="rss_public", score=9.0),
        ]
        session.flush()
        for row in rows:
            _assign_topic(session, row, tag)
        _follow(session, "alice", tag)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        ids = {item.article_id for item in result.items}
        assert "article-25" in ids
        assert ids.isdisjoint({"article-21", "article-22", "article-23", "article-24"})


def test_no_subscriptions_but_interests_yields_an_interest_only_edition(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), tag])
        session.flush()
        for number in range(1, 9):
            article = _seed_article(session, number, source=f"rss_{number % 3}", score=9.0 - number / 10)
            session.flush()
            _assign_topic(session, article, tag)
        _follow(session, "alice", tag)
        session.commit()

        assert resolve_personal_digest_source_ids(session, "alice") == []
        started = start_personal_digest_edition(
            session, "alice", now=NOW, generation_reason=DigestGenerationReason.FIRST_OPEN, first_open_at=NOW
        )
        assert started.status == "pending" and started.edition is not None
        assert json.loads(started.edition.expected_source_ids_json) == []

        result = generate_personal_digest(
            session, "alice", now=NOW, pending_edition_id=started.edition.id
        )

        assert result.status == "ready"
        assert 1 <= len(result.items) <= 5
        assert {item.selection_lane for item in result.items} == {"interest"}
        assert all(json.loads(item.snapshot_json)["subscribed"] is False for item in result.items)
        # 订阅外每源硬上限 2:三个来源各最多 2 条
        per_source: dict[str, int] = {}
        for item in result.items:
            source_id = json.loads(item.snapshot_json)["source_id"]
            per_source[source_id] = per_source.get(source_id, 0) + 1
        assert max(per_source.values()) <= 2
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["interest_only"] is True and stats["followed_count"] == 1


def test_no_subscriptions_and_no_interests_still_reports_empty(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.commit()
        started = start_personal_digest_edition(session, "alice", now=NOW)
        assert started.status == "empty_subscriptions" and started.edition is None
        assert generate_personal_digest(session, "alice", now=NOW).status == "empty_subscriptions"


def test_interest_only_edition_with_nothing_qualifying_is_honest_and_empty(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), tag])
        session.flush()
        low = _seed_article(session, 1, source="rss_x", score=5.5)  # 低于订阅外门槛
        session.flush()
        _assign_topic(session, low, tag)
        _follow(session, "alice", tag)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert result.edition.degraded_reason == "no_qualified_content"
        assert result.items == ()
        assert json.loads(result.edition.selection_stats_json)["interest_only"] is True


def test_subscribed_relevance_threshold_matches_the_reader_lens(storage):
    """订阅内的兴趣命中也只认主标签或相关度 ≥ 0.8 的指派(与阅读器透镜同一尺)。"""
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), _subscribe("alice", "rss_a"), tag])
        session.flush()
        weak = _seed_article(session, 1, source="rss_a", score=9.5)
        primary = _seed_article(session, 2, source="rss_a", score=6.0)
        for number in range(3, 9):
            _seed_article(session, number, source="rss_a", score=7.0)
        session.flush()
        _assign_topic(session, weak, tag, relevance=0.4)
        _assign_topic(session, primary, tag, relevance=0.3, primary=True)
        _follow(session, "alice", tag)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        lanes = {item.article_id: item.selection_lane for item in result.items}
        assert lanes["article-01"] == "quality"   # 弱指派:仍入选,但不算命中
        assert lanes["article-02"] == "interest"  # 主标签:命中


def test_external_knobs_are_read_from_kv(storage):
    from services.personal_digest import EXTERNAL_MIN_SCORE_KEY, EXTERNAL_PER_SOURCE_MAX_KEY, selection_policy

    with Session(storage.engine) as session:
        assert selection_policy(session).external_min_quality_score == 6.0
        assert selection_policy(session).external_per_source_max == 2
        session.add(AppSettingRecord(key=EXTERNAL_MIN_SCORE_KEY, value="7.5"))
        session.add(AppSettingRecord(key=EXTERNAL_PER_SOURCE_MAX_KEY, value="9"))  # 越界钳到上限 5
        session.commit()
        policy = selection_policy(session)
        assert policy.external_min_quality_score == 7.5
        assert policy.external_per_source_max == 5


def test_public_daily_brief_never_enters_subscribed_pool_or_fallback(storage):
    """issue #74:订阅了日报的读者,早报里不能出现「哆啦美 · AI资讯日报」。

    范围层剔除之外,候选查询与降级兜底各有一道机械双保险——直接喂含日报的 source_ids 也拿不到它。
    """

    from services.personal_digest import _load_latest_fallback, load_digest_candidates

    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "dorami_daily_brief,rss_a")])
        _seed_article(session, 1, source="dorami_daily_brief", score=9.5)
        _seed_article(session, 2, source="rss_a", score=7.0)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        assert [item.article_id for item in result.items] == ["article-02"]
        assert json.loads(result.edition.expected_source_ids_json) == ["rss_a"]

        pool = load_digest_candidates(
            session, ["dorami_daily_brief", "rss_a"], cutoff_at=NOW, window_hours=48
        )
        assert [candidate.article_id for candidate in pool] == ["article-02"]
        latest = _load_latest_fallback(session, ["dorami_daily_brief", "rss_a"], [], limit=5)
        assert [article.id for article, *_rest in latest] == ["article-02"]

    with Session(storage.engine) as session:
        session.add_all([_user("bob"), _subscribe("bob", "dorami_daily_brief,rss_b")])
        _seed_article(session, 3, source="dorami_daily_brief", score=9.0)
        _seed_article(session, 4, source="rss_b", score=4.0)
        session.commit()

        result = generate_personal_digest(session, "bob", now=NOW)

        assert result.status == "degraded"
        assert [item.article_id for item in result.items] == ["article-04"]


def test_positions_follow_section_order_then_score(storage):
    """issue #74:板块按 SECTION_ORDER 固定,板块内按分数降序——不再随通道与首次出现漂移。"""

    # 三个来源各 ≤ 2 篇:不依赖选篇层的每源上限放宽(codex 检视 P3),排序断言只考排序
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_b,rss_c")])
        _seed_article(session, 1, source="rss_a", score=8.5, genre="research_paper")
        _seed_article(session, 2, source="rss_b", score=7.0, genre="model_release")
        _seed_article(session, 3, source="rss_c", score=7.8, genre="industry_news")
        _seed_article(session, 4, source="rss_a", score=6.5, genre="model_release")
        _seed_article(session, 5, source="rss_b", score=9.0, genre="tutorial")
        session.commit()

        result = generate_personal_digest(
            session, "alice", now=NOW, policy=DigestSelectionPolicy(target_items=5)
        )

        assert result.status == "ready"
        assert [
            (item.position, item.section, item.quality_score_snapshot) for item in result.items
        ] == [
            (0, "模型发布", 7.0),
            (1, "模型发布", 6.5),
            (2, "行业资讯", 7.8),
            (3, "学术论文", 8.5),
            (4, "工程实践", 9.0),
        ]
        stored = session.exec(
            select(PersonalDigestItemRecord)
            .where(PersonalDigestItemRecord.edition_id == result.edition.id)
            .order_by(PersonalDigestItemRecord.position)
        ).all()
        assert [item.article_id for item in stored] == [item.article_id for item in result.items]


def test_assign_positions_section_order_score_desc_missing_last_ties_stable():
    """issue #74:_assign_positions 的四条规则——板块按 SECTION_ORDER、板块内分数降序、缺分殿后、
    同分保序;未登记板块殿后且按名字稳定;重大事件恒前。纯函数,瞬态记录不落库。"""

    from services.personal_digest import _assign_positions

    def record(section: str, score: float | None, article_id: str, lane: str = "quality"):
        return PersonalDigestItemRecord(
            edition_id=1, article_id=article_id, position=0, section=section, selection_lane=lane,
            quality_score_snapshot=score, matched_interest_codes_json="[]", ranking_features_json="{}",
            coverage_adjustments_json="[]", selection_reason="", snapshot_json="{}", created_at=NOW_ISO,
        )

    records = [
        record("学术论文", 8.5, "paper"),
        record("模型发布", 7.0, "model-first"),
        record("Z 未登记", 9.0, "unknown-z"),
        record("重大事件", 8.0, "breaking", lane="breaking"),
        record("模型发布", 7.0, "model-second"),
        record("A 未登记", 5.0, "unknown-a"),
        record("模型发布", None, "model-unscored"),
        record("订阅源最新更新", 9.9, "fallback"),
    ]
    _assign_positions(records)

    assert [(r.position, r.article_id) for r in records] == [
        (0, "breaking"),
        (1, "model-first"),
        (2, "model-second"),
        (3, "model-unscored"),
        (4, "paper"),
        (5, "fallback"),
        (6, "unknown-a"),
        (7, "unknown-z"),
    ]


# ── 「深度阅读」「播客精选」(issue #178 第三波,方案 §5.1) ──

from llm.article_analysis_prompt import CONTENT_VALUE_SCORING_VERSION  # noqa: E402


def _seed_valued(
    session: Session,
    number: int,
    *,
    source: str,
    content_value: float | None = None,
    content_kind: str | None = None,
    news: float = 5.0,
    content_type: str = "rss_article",
    days_ago: float = 1.0,
    url: str | None = None,
    version: str = CONTENT_VALUE_SCORING_VERSION,
    genre: str = "industry_news",
) -> ArticleRecord:
    article_id = f"article-{number:02d}"
    published_at = NOW - dt.timedelta(days=days_ago)
    article = ArticleRecord(
        id=article_id,
        title=f"Article {number}",
        content_type=content_type,
        source_id=source,
        source_url=url or f"https://example.com/{article_id}",
        publish_date=published_at.isoformat(),
        fetched_date=published_at.isoformat(),
        content=f"full body {number}",
    )
    session.add(article)
    session.flush()
    session.add(ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        tagging_status="succeeded",
        quality_score=news,
        score_reason=f"reason {number}",
        summary=f"summary {number}",
        content_genre=genre,
        content_kind=content_kind,
        content_value=content_value,
        content_value_reason=f"value reason {number}" if content_value is not None else "",
        content_value_version=version if content_value is not None else "",
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    ))
    return article


def _past_edition(session: Session, report_date: str, article_ids: list[str], *, lane: str = "read") -> None:
    stamp = f"{report_date}T08:30:00+08:00"
    edition = PersonalDigestEditionRecord(
        owner_username="alice",
        report_date=report_date,
        revision=1,
        status="ready",
        check_after=stamp,
        cutoff_at=stamp,
        generated_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(edition)
    session.flush()
    for position, article_id in enumerate(article_ids):
        session.add(PersonalDigestItemRecord(
            edition_id=edition.id,
            article_id=article_id,
            position=position,
            selection_lane=lane,
            quality_score_snapshot=5.0,
            snapshot_json="{}",
            created_at=stamp,
        ))


def test_value_sections_are_extra_and_follow_the_news_sections(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_papers,rss_blog,rss_ops,pod_a,pod_b")])
        news = [_seed_valued(session, n, source="rss_a", news=7.0, days_ago=0.2) for n in range(1, 4)]
        # 论文:新闻价值 4.5 过不了新闻门槛,内容价值 8.2 进「深度阅读」;链接形状判为论文
        paper = _seed_valued(
            session, 11, source="rss_papers", news=4.5, content_value=8.2,
            url="https://arxiv.org/abs/2610.00011", days_ago=3,
        )
        # 同源第二篇论文:每源 1 篇,让位给 8.2
        _seed_valued(
            session, 12, source="rss_papers", news=4.5, content_value=8.0,
            url="https://arxiv.org/abs/2610.00012", days_ago=2,
        )
        tutorial = _seed_valued(session, 13, source="rss_blog", news=4.0, content_value=7.4, content_kind="tutorial")
        # 内容价值 6.5 < 7.0;资讯类不进「深度阅读」
        _seed_valued(session, 14, source="rss_blog2", news=4.0, content_value=6.5, content_kind="opinion")
        _seed_valued(session, 15, source="rss_ops", news=4.0, content_value=9.0, content_kind="news")
        # 播客:即使分数够新闻门槛也不进新闻通道,只进「播客精选」(播客分 = 内容价值)
        episode = _seed_valued(session, 21, source="pod_a", news=8.0, content_type="podcast_episode", days_ago=4)
        _seed_valued(session, 22, source="pod_b", news=7.0, content_type="podcast_episode", days_ago=1)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        lanes = [(item.article_id, item.selection_lane, item.section) for item in result.items]
        assert lanes == [
            *[(article.id, "quality", "行业资讯") for article in news],
            (paper.id, "read", "深度阅读"),
            (tutorial.id, "read", "深度阅读"),
            (episode.id, "listen", "播客精选"),
        ]
        by_id = {item.article_id: item for item in result.items}
        paper_item = by_id[paper.id]
        assert paper_item.selection_reason == "来自你订阅的「rss_papers」，按内容价值入选。"
        snapshot = json.loads(paper_item.snapshot_json)
        assert snapshot["score_dimension"] == "content"
        assert snapshot["content_value"] == 8.2 and snapshot["content_kind"] == "paper"
        assert snapshot["content_value_reason"] == "value reason 11"
        assert snapshot["quality_score"] == 4.5
        assert json.loads(paper_item.ranking_features_json)["content_value"] == 8.2
        listen_snapshot = json.loads(by_id[episode.id].snapshot_json)
        assert listen_snapshot["score_dimension"] == "content" and listen_snapshot["content_value"] == 8.0
        assert json.loads(by_id[news[0].id].snapshot_json)["score_dimension"] == "news"
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["selected_count"] == 3
        assert stats["read"] == {
            "candidate_count": 3, "selected_count": 2, "min_value": 7.0, "max_items": 3, "window_days": 7,
        }
        assert stats["listen"]["candidate_count"] == 1 and stats["listen"]["selected_count"] == 1


def test_value_sections_skip_news_picks_recent_editions_stale_rulers_and_old_items(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_b,rss_c,rss_d,rss_e")])
        # 已进新闻通道的论文不在「深度阅读」重复出现
        in_news = _seed_valued(session, 1, source="rss_a", news=7.5, content_value=8.5, content_kind="paper", days_ago=0.3)
        # 五天前的早报出现过
        seen = _seed_valued(session, 2, source="rss_b", news=4.0, content_value=8.4, content_kind="paper", days_ago=5)
        # 尺子版本不是当前版本:缺失即缺席
        _seed_valued(session, 3, source="rss_c", news=4.0, content_value=9.0, content_kind="paper", version="content-value-v1")
        # 窗口 7 天之外
        _seed_valued(session, 4, source="rss_d", news=4.0, content_value=9.0, content_kind="paper", days_ago=8)
        fresh = _seed_valued(session, 5, source="rss_e", news=4.0, content_value=7.2, content_kind="opinion", days_ago=6)
        _past_edition(session, "2026-08-27", [seen.id])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        lanes = [(item.article_id, item.selection_lane) for item in result.items]
        assert lanes == [(in_news.id, "quality"), (fresh.id, "read")]


def test_value_sections_reach_outside_subscriptions_only_through_interests(storage):
    with Session(storage.engine) as session:
        tag = _tag("agents")
        session.add_all([_user(), _subscribe("alice", "rss_a"), tag])
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value=json.dumps(["rss_hidden"])))
        session.flush()
        _seed_valued(session, 1, source="rss_a", news=7.0, days_ago=0.2)
        hit = _seed_valued(session, 2, source="rss_outside", news=4.0, content_value=7.6, content_kind="tutorial")
        miss = _seed_valued(session, 3, source="rss_outside2", news=4.0, content_value=9.1, content_kind="tutorial")
        hidden = _seed_valued(session, 4, source="rss_hidden", news=4.0, content_value=9.3, content_kind="paper")
        private = _seed_valued(session, 5, source="user_rss_private01", news=4.0, content_value=9.4, content_kind="paper")
        session.flush()
        for article in (hit, hidden, private):
            _assign_topic(session, article, tag)
        _follow(session, "alice", tag)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        read = [item for item in result.items if item.selection_lane == "read"]
        assert [item.article_id for item in read] == [hit.id]
        assert miss.id not in {item.article_id for item in result.items}
        assert json.loads(read[0].matched_interest_codes_json) == ["agents"]
        assert read[0].selection_reason == "命中你的兴趣「agents」，来自你未订阅的「rss_outside」，按内容价值入选。"
        assert json.loads(read[0].snapshot_json)["subscribed"] is False


def test_value_section_knobs_come_from_kv_and_zero_items_turns_a_section_off(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_p,pod_a")])
        session.add_all([
            AppSettingRecord(key="personal_digest_read_max_items", value="0"),
            AppSettingRecord(key="personal_digest_listen_min_value", value="8.5"),
            AppSettingRecord(key="personal_digest_listen_window_days", value="2"),
        ])
        _seed_valued(session, 1, source="rss_a", news=7.0, days_ago=0.2)
        _seed_valued(session, 2, source="rss_p", news=4.0, content_value=9.5, content_kind="paper")
        _seed_valued(session, 3, source="pod_a", news=8.0, content_type="podcast_episode")
        _seed_valued(session, 4, source="pod_a", news=9.0, content_type="podcast_episode", days_ago=3)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [item.selection_lane for item in result.items] == ["quality"]
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["read"]["max_items"] == 0 and stats["read"]["candidate_count"] == 0
        assert stats["listen"] == {
            "candidate_count": 0, "selected_count": 0, "min_value": 8.5, "max_items": 2, "window_days": 2,
        }


def test_podcasts_leave_every_news_lane_including_breaking(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,pod_a")])
        session.add(SourceConfigRecord(
            source_id="pod_a", name="Pod A", source_type="podcast", source_scope="official",
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        own = _seed_valued(session, 1, source="rss_a", news=6.0, days_ago=0.2)
        loud = _seed_valued(session, 2, source="pod_a", news=9.8, content_type="podcast_episode", days_ago=0.3)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        lanes = [(item.article_id, item.selection_lane) for item in result.items]
        assert lanes == [(own.id, "quality"), (loud.id, "listen")]
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["breaking_count"] == 0 and stats["candidate_count"] == 1


def test_degraded_fallback_avoids_value_picks_and_value_sections_stay_last(storage):
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        paper = _seed_valued(session, 1, source="rss_a", news=3.0, content_value=8.0, content_kind="paper", days_ago=0.5)
        other = _seed_valued(session, 2, source="rss_a", news=3.0, days_ago=0.6)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        lanes = [(item.article_id, item.section) for item in result.items]
        assert lanes == [(other.id, "订阅源最新更新"), (paper.id, "深度阅读")]


def test_assign_positions_orders_value_sections_by_content_value():
    from services.personal_digest import _assign_positions

    def record(section: str, lane: str, news: float, value: float | None, article_id: str):
        return PersonalDigestItemRecord(
            edition_id=1, article_id=article_id, position=0, section=section, selection_lane=lane,
            quality_score_snapshot=news, matched_interest_codes_json="[]",
            ranking_features_json=json.dumps({"content_value": value}),
            coverage_adjustments_json="[]", selection_reason="", snapshot_json="{}", created_at=NOW_ISO,
        )

    records = [
        record("播客精选", "listen", 7.6, 7.6, "pod"),
        record("深度阅读", "read", 6.9, 7.1, "read-low"),
        record("深度阅读", "read", 4.2, 8.3, "read-high"),
        record("订阅源最新更新", "quality", 5.0, None, "fallback"),
        record("模型发布", "quality", 7.0, None, "news"),
    ]
    _assign_positions(records)

    assert [r.article_id for r in records] == ["news", "fallback", "read-high", "read-low", "pod"]


# ── issue #103(事件层 wave 2):个人早报同事件去重消费事件层归属 ──


def _event_with(session: Session, *article_ids: str, hours_ago: float = 3.0) -> EventRecord:
    """把给定文章归成一个事件(直接落 events / event_members,等价于分配服务已判定)。"""

    stamp = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    event = EventRecord(
        status="open", title="", title_source="", first_seen_at=stamp, last_member_at=stamp,
        member_count=len(article_ids), publisher_count=len(article_ids), created_at=stamp, updated_at=stamp,
    )
    session.add(event)
    session.flush()
    for article_id in article_ids:
        session.add(EventMemberRecord(
            event_id=event.id, article_id=article_id, publisher_key="", report_at=stamp, first_seen_at=stamp,
            assigned_by="llm", is_representative=False, created_at=stamp,
        ))
    session.flush()
    return event


def test_same_event_candidates_collapse_to_one_item_with_related_sources(storage):
    """同一事件的两篇订阅内候选只出一条;被并掉的那篇挂在代表条目的「另见」里,编排说明行计 1。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_b")])
        session.flush()
        first = _seed_article(session, 1, source="rss_a", score=8.0)
        echo = _seed_article(session, 2, source="rss_b", score=8.5)   # 同事件、分更高 → 代表
        other = _seed_article(session, 3, source="rss_a", score=7.0)
        session.flush()
        event = _event_with(session, first.id, echo.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "ready"
        assert [item.article_id for item in result.items] == [echo.id, other.id]
        rep = result.items[0]
        snapshot = json.loads(rep.snapshot_json)
        assert snapshot["event_id"] == event.id
        assert [row["article_id"] for row in snapshot["related_sources"]] == [first.id]
        link = snapshot["related_sources"][0]
        assert link["url"] == first.source_url and link["source_name"] and link["title"] == first.title
        assert json.loads(rep.ranking_features_json)["same_event_merged"] == 1
        assert json.loads(rep.ranking_features_json)["event_id"] == event.id
        plain = json.loads(result.items[1].snapshot_json)
        assert plain["event_id"] is None and plain["related_sources"] == []
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["same_event_merged"] == 1 and stats["same_event_previous_day"] == 0


def test_headline_event_is_not_repeated_in_the_curated_lanes(storage):
    """头条已代表的事件,兴趣 / 分数通道不再出同事件的另一篇(事件键跨三条通道共享)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        own = _seed_article(session, 1, score=8.0)                                   # 订阅内媒体稿
        headline = _seed_article(session, 2, source="rss_openai_news", score=9.6)   # 官方,订阅外 → 头条
        filler = _seed_article(session, 3, score=7.5)
        session.flush()
        _event_with(session, own.id, headline.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [(item.article_id, item.selection_lane) for item in result.items] == [
            (headline.id, "breaking"), (filler.id, "quality"),
        ]
        head = json.loads(result.items[0].snapshot_json)
        assert [row["article_id"] for row in head["related_sources"]] == [own.id]
        assert json.loads(result.edition.selection_stats_json)["same_event_merged"] == 1


def test_yesterdays_event_is_suppressed_as_a_whole_today_except_an_escalation_headline(storage):
    """昨天版面上过的事件(任一成员)今天在精选里整组不入选;升级例外:昨天只是精选里一条稿,
    今天同事件出现够头条资格的官方稿时允许以头条出现,它的「另见」列出昨天那篇(用户目检拍板)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        yesterday_pick = _seed_article(session, 1, score=8.0, published_at=NOW - dt.timedelta(hours=20))
        today_echo = _seed_article(session, 2, score=8.8, published_at=NOW - dt.timedelta(hours=2))
        official_today = _seed_article(session, 3, source="rss_openai_news", score=9.6)
        fresh = _seed_article(session, 4, score=7.0)
        session.flush()
        _event_with(session, yesterday_pick.id, today_echo.id, official_today.id)
        _past_edition(session, (NOW.date() - dt.timedelta(days=1)).isoformat(), [yesterday_pick.id])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [(item.article_id, item.selection_lane) for item in result.items] == [
            (official_today.id, "breaking"), (fresh.id, "quality"),
        ]
        head = json.loads(result.items[0].snapshot_json)
        assert {row["article_id"] for row in head["related_sources"]} == {yesterday_pick.id, today_echo.id}
        stats = json.loads(result.edition.selection_stats_json)
        # 订阅 ∪ 兴趣池里 today_echo 被昨日事件剔除;头条池不剔(昨天不是头条),它在头条池里算并入头条;
        # 昨天那篇按文章 id 排除,不计
        assert stats["same_event_previous_day"] == 1 and stats["same_event_previous_day_all"] == 1
        assert stats["same_event_merged"] == 0 and stats["same_event_merged_all"] == 1


def test_yesterdays_headline_event_stays_suppressed_even_for_an_official_follow_up(storage):
    """昨天本身就是头条的事件,今天同事件的官方后续不再成头条(与既有前两期实体抑制叠加,无实体标签也生效)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        yesterday_head = _seed_article(
            session, 1, source="rss_openai_news", score=9.5, published_at=NOW - dt.timedelta(hours=20)
        )
        official_today = _seed_article(session, 2, source="rss_openai_news", score=9.6)
        fresh = _seed_article(session, 3, score=7.0)
        session.flush()
        _event_with(session, yesterday_head.id, official_today.id)
        _past_edition(session, (NOW.date() - dt.timedelta(days=1)).isoformat(), [yesterday_head.id], lane="breaking")
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [item.article_id for item in result.items] == [fresh.id]
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["same_event_previous_day"] == 0 and stats["same_event_previous_day_all"] == 1


def test_degraded_fallback_skips_the_event_already_headlined(storage):
    """无合格精选但有同事件头条时,「订阅源最新更新」不再放回同事件的媒体稿(codex R1 P2)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        media = _seed_article(session, 1, score=4.0)                                # 订阅内,低于门槛
        headline = _seed_article(session, 2, source="rss_openai_news", score=9.6)   # 官方,订阅外 → 头条
        plain = _seed_article(session, 3, score=4.0)
        session.flush()
        _event_with(session, media.id, headline.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert [(item.article_id, item.selection_lane) for item in result.items] == [
            (headline.id, "breaking"), (plain.id, "quality"),
        ]
        assert {json.loads(item.snapshot_json).get("event_id") for item in result.items} == {
            json.loads(result.items[0].snapshot_json)["event_id"], None,
        }


def test_related_sources_only_list_reader_visible_members(storage):
    """「另见」只列读者可见成员:隐藏源 / 私有源成员不出现;订阅外的可见成员照列。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value=json.dumps(["rss_hidden"])))
        session.add(SourceConfigRecord(
            source_id="user_rss_private01", name="private", source_type="rss",
            owner_username="bob", created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.flush()
        rep = _seed_article(session, 1, score=8.0)
        hidden = _seed_article(session, 2, source="rss_hidden", score=7.0)
        private = _seed_article(session, 3, source="user_rss_private01", score=7.0)
        visible = _seed_article(session, 4, source="rss_other", score=6.0)
        session.flush()
        _event_with(session, rep.id, hidden.id, private.id, visible.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [item.article_id for item in result.items] == [rep.id]
        links = json.loads(result.items[0].snapshot_json)["related_sources"]
        assert [row["article_id"] for row in links] == [visible.id]
        assert json.loads(result.edition.selection_stats_json)["same_event_merged"] == 0


def test_degraded_fallback_keeps_one_item_per_event(storage):
    """降级版面的「订阅源最新更新」同事件只留最新一条,昨天上过的事件不出。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        newest = _seed_article(session, 1, score=4.0)
        older_echo = _seed_article(session, 2, score=4.0)
        plain = _seed_article(session, 3, score=4.0)
        stale = _seed_article(session, 4, score=4.0)
        yesterday_pick = _seed_article(session, 5, score=4.0, published_at=NOW - dt.timedelta(hours=20))
        session.flush()
        _event_with(session, newest.id, older_echo.id)
        _event_with(session, stale.id, yesterday_pick.id)
        _past_edition(session, (NOW.date() - dt.timedelta(days=1)).isoformat(), [yesterday_pick.id])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert result.status == "degraded"
        assert [item.article_id for item in result.items] == [newest.id, plain.id]
        snapshot = json.loads(result.items[0].snapshot_json)
        assert [row["article_id"] for row in snapshot["related_sources"]] == [older_echo.id]


def test_event_that_never_made_yesterdays_edition_is_not_suppressed_today(storage):
    """跨期抑制只看昨天**版面上出现过**的事件:昨天因分低没上报的事件,今天后续报道分高照常入选,
    同事件只出一条、早先的低分稿挂在「另见」里(用户 2026-10-23 目检追问的场景)。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a")])
        session.flush()
        early_low = _seed_article(session, 1, score=4.5, published_at=NOW - dt.timedelta(hours=30))   # 昨天没过门槛
        shown_yesterday = _seed_article(session, 2, score=7.0, published_at=NOW - dt.timedelta(hours=28))
        followup_high = _seed_article(session, 3, score=9.0, published_at=NOW - dt.timedelta(hours=3))  # 今天的强化报道
        session.flush()
        _event_with(session, early_low.id, followup_high.id, hours_ago=30)
        _past_edition(session, (NOW.date() - dt.timedelta(days=1)).isoformat(), [shown_yesterday.id])
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [item.article_id for item in result.items] == [followup_high.id]
        snapshot = json.loads(result.items[0].snapshot_json)
        assert [row["article_id"] for row in snapshot["related_sources"]] == [early_low.id]
        stats = json.loads(result.edition.selection_stats_json)
        assert stats["same_event_previous_day"] == 0


def test_official_member_represents_the_event_in_the_curated_lanes(storage):
    """精选通道里同事件的代表按「官方 > 非社交 > 分数 > 更早」(issue #103 验收第一条):订阅内官方稿 8.0
    压过媒体转述 8.5 当代表,媒体稿进「另见」;官方稿不够头条线,仍走精选。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,rss_openai_news")])
        session.flush()
        official = _seed_article(session, 1, source="rss_openai_news", score=8.0)   # 注册表官方源
        echo = _seed_article(session, 2, source="rss_a", score=8.5)
        other = _seed_article(session, 3, source="rss_a", score=7.0)
        session.flush()
        _event_with(session, official.id, echo.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [(item.article_id, item.selection_lane) for item in result.items] == [
            (official.id, "quality"), (other.id, "quality"),
        ]
        snapshot = json.loads(result.items[0].snapshot_json)
        assert [row["article_id"] for row in snapshot["related_sources"]] == [echo.id]
        assert json.loads(result.edition.selection_stats_json)["same_event_merged"] == 1


def test_social_post_from_an_unregistered_source_never_outranks_the_article_as_representative(storage):
    """来源无配置行也无注册类时,形态按文章 content_type 推(codex 检视 P2):同事件里 8.5 分的推文
    让位给 8.0 分的文章当代表。"""
    with Session(storage.engine) as session:
        session.add_all([_user(), _subscribe("alice", "rss_a,x_unknown_account")])
        session.flush()
        article = _seed_article(session, 1, source="rss_a", score=8.0)
        tweet = _seed_article(session, 2, source="x_unknown_account", score=8.5, content_type="social_post")
        session.flush()
        _event_with(session, article.id, tweet.id)
        session.commit()

        result = generate_personal_digest(session, "alice", now=NOW)

        assert [item.article_id for item in result.items] == [article.id]
        snapshot = json.loads(result.items[0].snapshot_json)
        assert [row["article_id"] for row in snapshot["related_sources"]] == [tweet.id]

