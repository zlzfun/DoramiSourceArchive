"""Persistent full-analysis history backfill and scheduler integration."""

from __future__ import annotations

import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from config import LLMConfig
from llm.article_analysis_prompt import (
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
)
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    AppSettingRecord,
    CmsTagRecord,
    SourceConfigRecord,
    TagRetagJobItemRecord,
    TagRetagJobRecord,
    TaxonomyVersionRecord,
    UserRecord,
)
from services.analysis_backfill import (
    AnalysisBackfillError,
    FULL_ANALYSIS_CONFIRMATION,
    cancel_full_analysis_backfill,
    claim_full_analysis_backfill,
    create_full_analysis_backfill,
    dispatch_full_analysis_backfill,
    estimate_full_analysis_backfill,
    pause_full_analysis_backfill,
    resume_full_analysis_backfill,
    retry_failed_full_analysis_items,
    serialize_full_analysis_backfill,
)
from services.article_analysis import compute_content_hash, run_analysis_cycle
from services import accounts as accounts_service
from storage.impl.db_storage import DatabaseStorage


NOW = dt.datetime(2026, 9, 2, 8, 0, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat()
LLM_CONFIG = LLMConfig(base_url="https://llm.invalid/v1", api_key="test", model="fake")


@pytest.fixture
def storage(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'full-analysis.db'}")
    try:
        yield value
    finally:
        value.engine.dispose()


def _article(article_id: str, *, age_days: int, source_id: str = "public") -> ArticleRecord:
    fetched = NOW - dt.timedelta(days=age_days)
    return ArticleRecord(
        id=article_id,
        title=f"Agent update {article_id}",
        content_type="article",
        source_id=source_id,
        source_url=f"https://example.invalid/{article_id}",
        publish_date=fetched.isoformat(),
        fetched_date=fetched.isoformat(),
        has_content=True,
        content="A detailed article about agent planning and tool use.",
    )


def _seed_taxonomy(session: Session) -> None:
    session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW_ISO))
    session.add(
        CmsTagRecord(
            code="topic.ai-agents",
            kind="topic",
            name_zh="AI 智能体",
            name_en="AI Agents",
            normalized_name="ai agents",
            status="active",
            taxonomy_version=1,
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
    )
    session.commit()


def _seed_current_analysis(session: Session, article: ArticleRecord) -> None:
    session.add(
        ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.5,
            summary="old summary",
            content_hash=compute_content_hash(article),
            prompt_version=ARTICLE_ANALYSIS_PROMPT_VERSION,
            scoring_version=ARTICLE_ANALYSIS_SCORING_VERSION,
            taxonomy_version=1,
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
    )


def _payload() -> dict:
    return {
        "quality_score": 8.6,
        "score_reason": "信息完整且有明确实践价值。",
        "one_sentence_summary": "文章介绍了新的智能体能力。",
        "summary": "文章解释了智能体规划、工具调用和落地边界。",
        "content_genre": "product_update",
        "primary_tag_code": "topic.ai-agents",
        "tag_assignments": [
            {"code": "topic.ai-agents", "kind": "topic", "relevance": 0.96}
        ],
        "tag_candidates": [],
        "content_features": ["official_release"],
        "entities": [],
    }


def test_estimate_filters_current_and_disabled_sources(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        current = _article("current", age_days=30)
        missing = _article("missing", age_days=200)
        disabled = _article("disabled", age_days=20, source_id="disabled-source")
        session.add_all([current, missing, disabled])
        session.add(
            SourceConfigRecord(
                source_id="disabled-source",
                name="Disabled",
                ai_analysis_enabled=False,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.flush()
        _seed_current_analysis(session, current)
        session.commit()

        all_estimate = estimate_full_analysis_backfill(
            session,
            days=None,
            selection="all",
            now=NOW,
        )
        outdated = estimate_full_analysis_backfill(
            session,
            days=None,
            selection="missing_or_outdated",
            now=NOW,
        )

    assert all_estimate["article_count"] == 2
    assert all_estimate["estimated_initial_llm_calls"] == 2
    assert all_estimate["estimated_max_llm_calls"] == 8
    assert all_estimate["taxonomy_version"] == 1
    assert outdated["article_count"] == 1


def test_estimate_treats_naive_article_timestamps_as_shanghai_wall_clock(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        article = _article("local-naive", age_days=0)
        article.fetched_date = NOW.astimezone(ZoneInfo("Asia/Shanghai")).replace(
            tzinfo=None
        ).isoformat()
        session.add(article)
        session.commit()
        estimate = estimate_full_analysis_backfill(
            session,
            days=1,
            selection="all",
            now=NOW + dt.timedelta(minutes=1),
        )
    assert estimate["article_count"] == 1


def test_create_snapshots_scope_and_rejects_parallel_job(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        session.add_all([_article("a", age_days=10), _article("b", age_days=400)])
        session.commit()
        with pytest.raises(AnalysisBackfillError, match="confirmation"):
            create_full_analysis_backfill(
                session,
                days=None,
                selection="all",
                actor_id="admin",
                confirmation="wrong",
                now=NOW,
            )
        job = create_full_analysis_backfill(
            session,
            days=None,
            selection="all",
            actor_id="admin",
            confirmation=FULL_ANALYSIS_CONFIRMATION,
            now=NOW,
        )
        state = serialize_full_analysis_backfill(session, job)
        items = session.exec(
            select(TagRetagJobItemRecord).where(TagRetagJobItemRecord.job_id == job.id)
        ).all()
        with pytest.raises(AnalysisBackfillError, match="unfinished"):
            create_full_analysis_backfill(
                session,
                days=30,
                selection="all",
                actor_id="admin",
                confirmation=FULL_ANALYSIS_CONFIRMATION,
                now=NOW,
            )

    assert job.operation == "full_analysis"
    assert state["counts"] == {
        "total": 2,
        "pending": 2,
        "queued": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "finished": 0,
    }
    assert state["created_by"] == "admin"
    assert {item.article_id_snapshot for item in items} == {"a", "b"}


def test_scheduler_prioritizes_live_article_then_completes_forced_history(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        older = _article("older", age_days=300)
        newer = _article("newer", age_days=100)
        session.add_all([older, newer])
        session.flush()
        _seed_current_analysis(session, older)
        _seed_current_analysis(session, newer)
        session.commit()
        job = create_full_analysis_backfill(
            session,
            days=None,
            selection="all",
            actor_id="admin",
            confirmation=FULL_ANALYSIS_CONFIRMATION,
            now=NOW,
        )
        session.add(_article("live", age_days=0))
        session.commit()
        job_id = int(job.id)

    calls: list[str] = []

    async def analyzer(article_input, _tags, _config):
        calls.append(article_input.article_id)
        return _payload()

    for _ in range(3):
        result = asyncio.run(
            run_analysis_cycle(
                storage.engine,
                worker_id="runtime-all",
                llm_config=LLM_CONFIG,
                analyzer=analyzer,
                enabled=True,
                candidate_enabled=False,
                batch_size=1,
                now_fn=lambda: NOW,
            )
        )
        assert len(result) == 1

    with Session(storage.engine) as session:
        job = session.get(TagRetagJobRecord, job_id)
        state = serialize_full_analysis_backfill(session, job)
        old_rows = [session.get(ArticleAnalysisRecord, article_id) for article_id in ("newer", "older")]

    assert calls == ["live", "newer", "older"]
    assert state["status"] == "succeeded"
    assert state["progress"] == 1.0
    assert state["counts"]["succeeded"] == 2
    assert all(row.quality_score == 8.6 for row in old_rows)


def test_expired_job_lease_and_queued_analysis_resume_after_restart(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        session.add(_article("restart", age_days=500))
        session.commit()
        job = create_full_analysis_backfill(
            session,
            days=None,
            selection="all",
            actor_id="admin",
            confirmation=FULL_ANALYSIS_CONFIRMATION,
            now=NOW,
        )
        claimed = claim_full_analysis_backfill(
            session,
            lease_owner="dead-worker",
            lease_seconds=1,
            now=NOW,
        )
        assert claimed.id == job.id
        assert dispatch_full_analysis_backfill(
            session,
            claimed,
            lease_owner="dead-worker",
            limit=1,
            now=NOW,
        ) == 1
        job_id = int(job.id)

    async def analyzer(_article_input, _tags, _config):
        return _payload()

    result = asyncio.run(
        run_analysis_cycle(
            storage.engine,
            worker_id="runtime-all",
            llm_config=LLM_CONFIG,
            analyzer=analyzer,
            enabled=True,
            candidate_enabled=False,
            batch_size=1,
            now_fn=lambda: NOW + dt.timedelta(seconds=2),
        )
    )
    assert result[0].article_id == "restart"
    with Session(storage.engine) as session:
        state = serialize_full_analysis_backfill(
            session,
            session.get(TagRetagJobRecord, job_id),
        )
    assert state["status"] == "succeeded"
    assert state["counts"]["succeeded"] == 1


def test_pause_resume_cancel_and_failed_item_retry(storage):
    with Session(storage.engine) as session:
        _seed_taxonomy(session)
        session.add_all([_article("one", age_days=1), _article("two", age_days=2)])
        session.commit()
        job = create_full_analysis_backfill(
            session,
            days=None,
            selection="all",
            actor_id="admin",
            confirmation=FULL_ANALYSIS_CONFIRMATION,
            now=NOW,
        )
        pause_full_analysis_backfill(session, job, now=NOW)
        assert claim_full_analysis_backfill(session, lease_owner="worker", now=NOW) is None
        resume_full_analysis_backfill(session, job, now=NOW)
        claimed = claim_full_analysis_backfill(session, lease_owner="worker", now=NOW)
        assert claimed.id == job.id
        cancel_full_analysis_backfill(session, claimed, now=NOW)
        cancelled = serialize_full_analysis_backfill(session, claimed)

        # A separate terminal job proves failed-item retry without making a
        # real model call fail four times in this state-transition test.
        failed_job = TagRetagJobRecord(
            taxonomy_version=1,
            operation="full_analysis",
            scope_json=job.scope_json,
            status="partial_failed",
            affected_count=1,
            failed_count=1,
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
        session.add(failed_job)
        session.flush()
        session.add(
            TagRetagJobItemRecord(
                job_id=int(failed_job.id),
                article_id="one",
                article_id_snapshot="one",
                status="failed",
                last_error="model failure",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()
        retry_failed_full_analysis_items(session, failed_job, now=NOW)
        retried = serialize_full_analysis_backfill(session, failed_job)

    assert cancelled["status"] == "cancelled"
    assert cancelled["counts"]["skipped"] == 2
    assert retried["status"] == "queued"
    assert retried["counts"]["pending"] == 1
    assert retried["counts"]["failed"] == 0


def test_admin_backfill_api_estimate_create_and_lifecycle(monkeypatch, tmp_path):
    import api.app as app_module
    from api.routers import analysis_ops

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'full-analysis-api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        analysis_ops.daily_brief_service,
        "resolve_llm_config",
        lambda _session: LLM_CONFIG,
    )
    with Session(sink.engine) as session:
        for username, role in (("admin", "admin"), ("reader", "user")):
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password("pw"),
                    role=role,
                    is_active=True,
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
        session.add(AppSettingRecord(key="article_analysis_enabled", value="true"))
        _seed_taxonomy(session)
        session.add(_article("api-history", age_days=200))
        session.commit()

    with TestClient(app_module.app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "reader", "password": "pw"}
        ).status_code == 200
        assert client.post(
            "/api/admin/analysis/backfills/estimate",
            json={"days": None, "selection": "all", "source_ids": []},
        ).status_code == 403

    with TestClient(app_module.app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "pw"}
        ).status_code == 200
        estimate = client.post(
            "/api/admin/analysis/backfills/estimate",
            json={"days": None, "selection": "all", "source_ids": []},
        )
        assert estimate.status_code == 200
        assert estimate.json()["ready"] is True
        assert estimate.json()["article_count"] == 1

        created = client.post(
            "/api/admin/analysis/backfills",
            json={
                "days": None,
                "selection": "all",
                "source_ids": [],
                "confirmation": FULL_ANALYSIS_CONFIRMATION,
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_id"]
        assert created.json()["counts"]["total"] == 1
        assert created.json()["created_by"] == "admin"

        assert client.get("/api/admin/analysis/backfills").json()["items"][0]["job_id"] == job_id
        assert client.post(f"/api/admin/analysis/backfills/{job_id}/pause").json()["status"] == "paused"
        assert client.post(f"/api/admin/analysis/backfills/{job_id}/resume").json()["status"] == "queued"
        cancelled = client.post(f"/api/admin/analysis/backfills/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["counts"]["skipped"] == 1

    sink.engine.dispose()
