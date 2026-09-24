"""Issue #154: deterministic reader ranking snapshots and visibility fences."""

from __future__ import annotations

import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    RankingContentItemRecord,
    RankingSnapshotRecord,
    RankingTagItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    TaxonomyVersionRecord,
)
from services import rankings  # noqa: E402
from api import deps  # noqa: E402
from api.routers import admin as admin_router  # noqa: E402
from api.routers import reader as reader_router  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


AT = dt.datetime(2026, 9, 24, 8, tzinfo=rankings.SHANGHAI)
STAMP = "2026-09-24T00:00:00+00:00"


def _tag(session, code, kind, *, parent_id=None, entity_type=""):
    row = CmsTagRecord(
        code=code,
        kind=kind,
        name_zh=code,
        name_en=code,
        normalized_name=code,
        status="active",
        parent_id=parent_id,
        entity_type=entity_type,
        created_at=STAMP,
        updated_at=STAMP,
        taxonomy_version=7,
    )
    session.add(row)
    session.flush()
    return row


def _content(session, article_id, source_id, *, podcast=False, score=8.0, basis="article_body"):
    content_type = "podcast_episode" if podcast else "web_article"
    article = ArticleRecord(
        id=article_id,
        title=f"Title {article_id}",
        content_type=content_type,
        source_id=source_id,
        source_url=f"https://example.test/{article_id}",
        publish_date="2026-09-23T08:00:00+08:00",
        fetched_date="2026-09-23T09:00:00+08:00",
        content="body",
    )
    session.add(article)
    session.flush()
    analysis = ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        tagging_status="succeeded",
        quality_score=score,
        podcast_initial_score=score - 1 if podcast else None,
        podcast_final_score=score if podcast and basis != "podcast_show_notes" else None,
        analysis_basis=basis,
        taxonomy_version=7,
        created_at=STAMP,
        updated_at=STAMP,
    )
    session.add(analysis)
    session.flush()
    return article


def _assign(session, article, tag, relevance=0.9, primary=False):
    session.add(ArticleTagAssignmentRecord(
        article_id=article.id,
        tag_id=tag.id,
        tag_kind=tag.kind,
        is_primary=primary,
        relevance=relevance,
        assignment_source="llm",
        prompt_version="test",
        taxonomy_version=7,
        created_at=STAMP,
        updated_at=STAMP,
    ))


def _seed(engine):
    with Session(engine) as session:
        session.add(TaxonomyVersionRecord(
            version=7, status="active", created_at=STAMP, change_summary="test"
        ))
        parent = _tag(session, "topic.generative", "topic")
        child = _tag(session, "topic.image", "topic", parent_id=parent.id)
        industry = _tag(session, "industry.media", "industry")
        organization = _tag(session, "entity.openai", "entity", entity_type="organization")
        model = _tag(session, "entity.model", "entity", entity_type="model")

        for index, source in enumerate(("source-a", "source-b"), start=1):
            article = _content(session, f"article-{index}", source, score=9 - index / 10)
            _assign(session, article, parent, primary=True)
            _assign(session, article, child)
            _assign(session, article, industry, primary=True)
            _assign(session, article, organization)
            _assign(session, article, model)  # non-organization entities never enter the board

        for index, source in enumerate(("podcast-a", "podcast-b"), start=1):
            basis = "asr_transcript" if index == 1 else "podcast_show_notes"
            episode = _content(
                session, f"podcast-{index}", source, podcast=True, score=8.5, basis=basis
            )
            _assign(session, episode, child, primary=True)
            _assign(session, episode, industry, primary=True)

        weak = _content(session, "weak", "source-c")
        _assign(session, weak, child, relevance=0.79)
        private = _content(session, "private", "user_rss_alice_private")
        _assign(session, private, child, primary=True)
        hidden = _content(session, "hidden", "hidden-source")
        _assign(session, hidden, child, primary=True)
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden-source"]'))
        session.commit()


def test_snapshot_counts_shapes_filters_and_parent_chain_must_read(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'rankings.db'}")
    _seed(sink.engine)

    first = rankings.build_snapshot(sink.engine, at=AT)
    second = rankings.build_snapshot(sink.engine, at=AT)
    assert first.snapshot_date == second.snapshot_date == "2026-09-24"
    with Session(sink.engine) as session:
        assert len(session.exec(select(RankingSnapshotRecord)).all()) == 1
        assert len(session.exec(select(RankingTagItemRecord)).all()) == 6
        # Two article sources + two podcast sources; weak/private/hidden rows are absent.
        article = rankings.read_rankings(session, shape="article")
        podcast = rankings.read_rankings(session, shape="podcast")

    assert [item["code"] for item in article["axes"]["topic"]] == [
        "topic.generative", "topic.image"
    ]
    assert article["axes"]["entity"][0]["code"] == "entity.openai"
    assert article["axes"]["topic"][0]["occurrence_count"] == 2
    # Parent + child collapse to one appearance; Industry is the second independent line.
    assert {item["appearance_count"] for item in article["must_read"]} == {3}
    # entity is an independent axis, therefore parent-chain topic + industry + organization = 3.
    assert len(article["must_read"]) == 2
    assert podcast["axes"]["entity"] == []
    assert {item["score_basis"] for item in podcast["must_read"]} == {
        "full_transcript", "show_notes"
    }


def test_sitewide_board_ignores_personal_subscriptions_and_reader_defaults(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'sitewide.db'}")
    _seed(sink.engine)
    with Session(sink.engine) as session:
        # Alice sees only source-a in her personal reader and new accounts have
        # no seeded defaults.  Neither preference may narrow a site-wide board.
        session.add(AppSettingRecord(key="reader_default_source_ids", value="[]"))
        session.add(ReaderSubscriptionRecord(
            owner_username="alice",
            name="Alice only",
            filters_json='{"source_ids":"source-a"}',
            delivery_policy_json="{}",
            token_hash="alice-token-hash",
            token_preview="ali…ice",
            is_active=True,
            created_at=STAMP,
            updated_at=STAMP,
        ))

        # Prefixes are a defense-in-depth convention, not the privacy boundary:
        # an owner-backed legacy config remains private even with a public-looking ID.
        session.add(SourceConfigRecord(
            source_id="legacy-private-feed",
            name="Legacy private feed",
            source_type="rss",
            url="https://private.example.test/feed",
            owner_username="alice",
            created_at=STAMP,
            updated_at=STAMP,
        ))
        private_article = _content(session, "legacy-private", "legacy-private-feed")
        topic = session.exec(
            select(CmsTagRecord).where(CmsTagRecord.code == "topic.image")
        ).one()
        _assign(session, private_article, topic, primary=True)
        session.commit()

    rankings.build_snapshot(sink.engine, at=AT)
    with Session(sink.engine) as session:
        board = rankings.read_rankings(session, shape="article")
        detail = rankings.read_tag_contents(
            session, date="latest", shape="article", tag_code="topic.image"
        )

    assert board["scope"] == rankings.PUBLIC_SCOPE
    image_tag = next(item for item in board["axes"]["topic"] if item["code"] == "topic.image")
    assert image_tag["occurrence_count"] == 2
    assert {item["source_id"] for item in detail["contents"]} == {"source-a", "source-b"}


def test_snapshot_boundary_and_failed_rerun_preserve_last_good_snapshot(monkeypatch, tmp_path):
    before = dt.datetime(2026, 9, 24, 6, 59, tzinfo=rankings.SHANGHAI)
    after = dt.datetime(2026, 9, 24, 7, 0, tzinfo=rankings.SHANGHAI)
    assert rankings.snapshot_boundary(before)[0] == "2026-09-23"
    assert rankings.snapshot_boundary(after)[0] == "2026-09-24"

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'rollback.db'}")
    _seed(sink.engine)
    rankings.build_snapshot(sink.engine, at=AT)
    with Session(sink.engine) as session:
        original_tags = len(session.exec(select(RankingTagItemRecord)).all())
        original_contents = len(session.exec(select(RankingContentItemRecord)).all())

    def _fail_commit(_session):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(Session, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        rankings.build_snapshot(sink.engine, at=AT)

    with Session(sink.engine) as session:
        assert len(session.exec(select(RankingSnapshotRecord)).all()) == 1
        assert len(session.exec(select(RankingTagItemRecord)).all()) == original_tags
        assert len(session.exec(select(RankingContentItemRecord)).all()) == original_contents


def test_current_hidden_source_is_removed_from_counts_titles_and_history(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'visibility.db'}")
    _seed(sink.engine)
    rankings.build_snapshot(sink.engine, at=AT)
    with Session(sink.engine) as session:
        setting = session.get(AppSettingRecord, "reader_hidden_source_ids")
        setting.value = '["hidden-source", "source-b"]'
        session.add(setting)
        session.commit()
        response = rankings.read_rankings(session, shape="article")
        detail = rankings.read_tag_contents(
            session, date="latest", shape="article", tag_code="topic.image"
        )
        history = rankings.read_history(
            session, tag_code="topic.image", shape="article", days=30
        )
    # One visible source no longer satisfies the public two-source support floor.
    assert all(not response["axes"][axis] for axis in rankings.AXES)
    assert response["must_read"] == []
    assert detail is None
    assert history["points"] == []


def test_article_deleted_after_snapshot_is_not_exposed(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'deleted.db'}")
    _seed(sink.engine)
    rankings.build_snapshot(sink.engine, at=AT)
    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-2")
        assert article is not None
        session.delete(article)
        session.commit()

    with Session(sink.engine) as session:
        response = rankings.read_rankings(session, shape="article")
        detail = rankings.read_tag_contents(
            session, date="latest", shape="article", tag_code="topic.image"
        )
    assert all(not response["axes"][axis] for axis in rankings.AXES)
    assert response["must_read"] == []
    assert detail is None


def test_reader_ranking_endpoints_expose_snapshot_detail_and_history(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'reader-api.db'}")
    _seed(sink.engine)
    rankings.build_snapshot(sink.engine, at=AT)

    api = FastAPI()
    api.include_router(reader_router.router)

    def _session_override():
        with Session(sink.engine) as session:
            yield session

    api.dependency_overrides[deps.get_session] = _session_override
    client = TestClient(api)

    board = client.get("/api/reader/rankings", params={"shape": "article"})
    assert board.status_code == 200
    assert board.json()["scope"] == rankings.PUBLIC_SCOPE
    assert set(board.json()["axes"]) == set(rankings.AXES)
    assert board.json()["axes"]["topic"][0]["occurrence_count"] == 2

    detail = client.get(
        "/api/reader/rankings/latest/tags/topic.image",
        params={"shape": "article"},
    )
    assert detail.status_code == 200
    assert detail.json()["scope"] == rankings.PUBLIC_SCOPE
    assert len(detail.json()["contents"]) == 2

    trend = client.get(
        "/api/reader/rankings/history",
        params={"shape": "article", "tag_code": "topic.image", "days": 30},
    )
    assert trend.status_code == 200
    assert trend.json()["scope"] == rankings.PUBLIC_SCOPE
    assert trend.json()["points"][0]["occurrence_count"] == 2
    assert client.get(
        "/api/reader/rankings/history",
        params={"shape": "article", "tag_code": "topic.image", "days": 91},
    ).status_code == 400


def test_first_reader_request_builds_once_when_database_has_no_snapshot(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'reader-first-build.db'}")
    _seed(sink.engine)

    api = FastAPI()
    api.include_router(reader_router.router)

    def _session_override():
        with Session(sink.engine) as session:
            yield session

    api.dependency_overrides[deps.get_session] = _session_override
    client = TestClient(api)

    first = client.get("/api/reader/rankings", params={"shape": "article"})
    second = client.get("/api/reader/rankings", params={"shape": "article"})

    assert first.status_code == second.status_code == 200
    assert first.json()["generated_at"] == second.json()["generated_at"]
    with Session(sink.engine) as session:
        assert len(session.exec(select(RankingSnapshotRecord)).all()) == 1


def test_concurrent_empty_snapshot_guards_perform_one_full_build(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'concurrent-first-build.db'}")
    _seed(sink.engine)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _index: rankings.ensure_snapshot_if_empty(sink.engine, at=AT),
            range(8),
        ))

    assert sum(result is not None for result in results) == 1
    with Session(sink.engine) as session:
        assert len(session.exec(select(RankingSnapshotRecord)).all()) == 1


def test_admin_ranking_status_and_manual_refresh(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'admin-refresh.db'}")
    _seed(sink.engine)

    api = FastAPI()
    api.include_router(admin_router.router)

    def _session_override():
        with Session(sink.engine) as session:
            yield session

    api.dependency_overrides[deps.get_session] = _session_override
    monkeypatch.setattr(deps, "get_db_sink", lambda: sink)
    client = TestClient(api)

    empty = client.get("/api/admin/rankings/status")
    assert empty.status_code == 200
    assert empty.json()["snapshot"] is None
    assert empty.json()["schedule"] == "0 7 * * *"
    assert empty.json()["timezone"] == "Asia/Shanghai"

    refreshed = client.post("/api/admin/rankings/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["snapshot"]["status"] == "complete"
    assert body["snapshot"]["coverage"]["article"] == {
        "eligible": 3,
        "analyzed": 3,
        "tagged": 2,
    }
    assert body["refresh_running"] is False

    def _busy(_engine):
        raise rankings.RankingSnapshotBusy("榜单正在刷新，请稍后再试")

    monkeypatch.setattr(rankings, "build_snapshot_if_idle", _busy)
    conflict = client.post("/api/admin/rankings/refresh")
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "榜单正在刷新，请稍后再试"


def test_ranking_schedule_is_0700_coalesced_and_misfire_tolerant(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'schedule.db'}")
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.start(paused=True)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    try:
        app_module.reload_ranking_schedule()
        app_module.reload_ranking_schedule()
        jobs = [job for job in scheduler.get_jobs() if job.id == app_module.RANKING_JOB_ID]
        assert len(jobs) == 1
        assert "hour='7'" in str(jobs[0].trigger)
        assert "minute='0'" in str(jobs[0].trigger)
        assert jobs[0].coalesce is True
        assert jobs[0].misfire_grace_time == app_module.CRON_MISFIRE_GRACE_SECONDS
    finally:
        scheduler.shutdown(wait=False)
