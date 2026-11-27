"""Fixed publication cohorts through the existing admin API and backfill worker."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sqlalchemy import event

from api import deps
from api.routers import analysis_ops
from config import LLMConfig
from llm.article_analysis_prompt import analysis_contract_versions
from models.db import (
    AppSettingRecord, ArticleAnalysisRecord, ArticleRecord, CmsTagRecord,
    SourceConfigRecord, TagRetagJobItemRecord, TagRetagJobRecord, TaxonomyVersionRecord,
)
from services import analysis_backfill as service
from services.article_analysis import compute_content_hash, run_analysis_cycle
from storage.impl.db_storage import DatabaseStorage


@pytest.fixture
def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(minutes=1)


@pytest.fixture
def sink(tmp_path, now):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'cohort.db'}")
    with Session(value.engine) as session:
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now.isoformat()))
        session.add(CmsTagRecord(code="topic.ai-agents", kind="topic", name_zh="AI 智能体",
                                name_en="AI Agents", normalized_name="ai agents", status="active",
                                taxonomy_version=1, created_at=now.isoformat(), updated_at=now.isoformat()))
        session.add(AppSettingRecord(key="article_analysis_enabled", value="true"))
        session.commit()
    yield value
    value.engine.dispose()


def article(aid, now, *, published=None, fetched=None, source="public"):
    return ArticleRecord(id=aid, title=f"Original {aid}", content_type="article", source_id=source,
                         source_url=f"https://example.invalid/{aid}",
                         publish_date=(published or now - dt.timedelta(days=2)).isoformat(),
                         fetched_date=(fetched or now - dt.timedelta(days=60)).isoformat(),
                         has_content=True, content="Original detailed discussion of planning and tool use.")


def args(now, ids=("a",), **changes):
    return {"days": None, "selection": "missing_or_outdated", "article_ids": list(ids),
            "publish_after": (now - dt.timedelta(days=7)).isoformat(), "publish_before": now.isoformat(),
            "source_ids": [], "now": now, **changes}


def create(session, now, ids=("a",)):
    params = args(now, ids)
    estimated = service.estimate_full_analysis_backfill(session, **params)
    return service.create_full_analysis_backfill(session, **params, actor_id="admin",
        confirmation=service.FULL_ANALYSIS_CONFIRMATION, expected_cohort_hash=estimated["cohort_hash"])


def seed_analysis(session, row, now, *, status="succeeded"):
    prompt, scoring = analysis_contract_versions(row.content_type)
    analysis = ArticleAnalysisRecord(article_id=row.id, status=status, tagging_status="succeeded",
        quality_score=7, summary="Already processed", content_hash=compute_content_hash(row),
        prompt_version=prompt, scoring_version=scoring, taxonomy_version=1,
        created_at=now.isoformat(), updated_at=now.isoformat())
    session.add(analysis)
    session.commit()
    return analysis


def item_for(session, job):
    return session.exec(select(TagRetagJobItemRecord).where(TagRetagJobItemRecord.job_id == job.id)).one()


def test_publication_not_fetch_date_and_inclusive_bounds(sink, now):
    with Session(sink.engine) as session:
        session.add_all([article("lower", now, published=now - dt.timedelta(days=7)),
                         article("upper", now, published=now),
                         article("stale", now, published=now - dt.timedelta(days=8), fetched=now)])
        session.commit()
        legacy = service.estimate_full_analysis_backfill(session, days=7, selection="all", now=now)
        assert legacy["article_count"] == 1 and "cohort_hash" not in legacy
        result = service.estimate_full_analysis_backfill(session, **args(now, ["upper", "lower"]))
        assert result["target_ids"] == ["lower", "upper"]
        for aid in ["stale"]:
            with pytest.raises(service.AnalysisBackfillError, match="publication_outside_cohort"):
                service.estimate_full_analysis_backfill(session, **args(now, [aid]))


@pytest.mark.parametrize("delta", [-1, 7 * 86400 + 1])
def test_publication_outside_one_second_rejected(sink, now, delta):
    with Session(sink.engine) as session:
        session.add(article("a", now, published=now - dt.timedelta(seconds=delta)))
        session.commit()
        with pytest.raises(service.AnalysisBackfillError, match="publication_outside_cohort"):
            service.estimate_full_analysis_backfill(session, **args(now))


def test_timezone_equivalent_commitment(sink, now):
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit()
        first = service.estimate_full_analysis_backfill(session, **args(now))
        tz = dt.timezone(dt.timedelta(hours=8))
        second = service.estimate_full_analysis_backfill(session, **args(now,
            publish_after=(now - dt.timedelta(days=7)).astimezone(tz).isoformat(),
            publish_before=now.astimezone(tz).isoformat()))
        assert first["cohort_hash"] == second["cohort_hash"]


@pytest.mark.parametrize("change", [
    {"days": 7}, {"publish_after": None}, {"publish_before": None}, {"article_ids": None},
    {"article_ids": []}, {"article_ids": ["a", "a"]}, {"article_ids": [""]},
    {"article_ids": ["a"] * 5001}, {"selection": "all"}, {"selection": "missing"},
    {"selection": "content_value"}, {"publish_before": "yesterday"},
    {"publish_before": "2026-01-01 00:00:00+00:00"},
    {"publish_before": "2026-W01-1T00:00:00+00:00"},
    {"publish_before": "2026-01-01"}, {"publish_before": "2026-01-01T00:00:00"},
])
def test_invalid_cohort_is_rejected(sink, now, change):
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit()
        with pytest.raises(service.AnalysisBackfillError):
            service.estimate_full_analysis_backfill(session, **args(now, **change))
        assert not session.exec(select(TagRetagJobRecord)).all()


@pytest.mark.parametrize("kind", ["reversed", "future", "missing_id", "source"])
def test_invalid_scope_all_or_nothing(sink, now, kind):
    change = {"reversed": {"publish_after": (now + dt.timedelta(seconds=1)).isoformat()},
              "future": {"publish_before": (now + dt.timedelta(seconds=1)).isoformat()},
              "missing_id": {"article_ids": ["a", "absent"]}, "source": {"source_ids": ["elsewhere"]}}[kind]
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit()
        with pytest.raises(service.AnalysisBackfillError):
            service.estimate_full_analysis_backfill(session, **args(now, **change))


@pytest.mark.parametrize("change", ["body", "source", "published", "eligibility", "version"])
def test_estimate_create_rejects_drift_before_insert(sink, now, change, monkeypatch):
    with Session(sink.engine) as session:
        row = article("a", now); session.add(row); session.commit()
        estimate = service.estimate_full_analysis_backfill(session, **args(now))
        if change == "body": row.content += " Changed body."
        elif change == "source": row.source_id = "another"
        elif change == "published": row.publish_date = (now - dt.timedelta(days=3)).isoformat()
        elif change == "eligibility": row.has_content = False
        else: monkeypatch.setattr(service, "current_taxonomy_version", lambda _: 2)
        session.add(row); session.commit()
        with pytest.raises(service.AnalysisBackfillError, match="cohort_changed"):
            service.create_full_analysis_backfill(session, **args(now), actor_id="admin",
                confirmation=service.FULL_ANALYSIS_CONFIRMATION, expected_cohort_hash=estimate["cohort_hash"])
        assert not session.exec(select(TagRetagJobRecord)).all()
        assert not session.exec(select(TagRetagJobItemRecord)).all()


def test_exclusions_and_success_reuse_are_explicit(sink, now):
    with Session(sink.engine) as session:
        rows = [article(aid, now) for aid in ["a", "done", "empty", "remote", "disabled"]]
        rows[2].has_content = False; rows[3].analysis_authority_id = "upstream"
        rows[4].source_id = "disabled-source"
        session.add_all(rows)
        session.add(SourceConfigRecord(source_id="disabled-source", name="Disabled", ai_analysis_enabled=False,
                                       created_at=now.isoformat(), updated_at=now.isoformat()))
        session.commit(); seed_analysis(session, rows[1], now)
        result = service.estimate_full_analysis_backfill(session, **args(now, [r.id for r in rows]))
        assert result["target_ids"] == ["a"]
        assert result["dispositions"] == {"a": "target", "done": "reused_current", "empty": "no_content",
                                         "remote": "remote_authority", "disabled": "source_ai_analysis_disabled"}
        result = service.estimate_full_analysis_backfill(session, **args(now, ["done"]))
        assert not result["ready"] and result["article_count"] == 0


def test_restart_exact_membership_and_real_clock(sink, now):
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit()
        job_id = create(session, now).id
    later = now + dt.timedelta(days=2)
    with Session(sink.engine) as session:
        session.add(article("new-arrival", now)); session.commit()
        job = service.claim_full_analysis_backfill(session, lease_owner="restart", now=later)
        assert job.id == job_id and dt.datetime.fromisoformat(job.lease_expires_at) > later
        service.dispatch_full_analysis_backfill(session, job, lease_owner="restart", now=later)
        assert [r.article_id for r in session.exec(select(TagRetagJobItemRecord)).all()] == ["a"]
        assert session.get(ArticleAnalysisRecord, "new-arrival") is None
        state = service.serialize_full_analysis_backfill(session, job)
        assert state["publish_before"] == now.isoformat()


@pytest.mark.parametrize("stage", ["dispatch", "reconcile"])
@pytest.mark.parametrize("change", ["body", "title", "extensions", "source", "published", "version"])
def test_drift_never_refreshes_frozen_target(sink, now, stage, change, monkeypatch):
    with Session(sink.engine) as session:
        row = article("a", now); session.add(row); session.commit(); job = create(session, now)
        frozen = json.loads(job.scope_json)["publication_cohort"]
        job = service.claim_full_analysis_backfill(session, lease_owner="worker", now=now)
        if stage == "reconcile":
            service.dispatch_full_analysis_backfill(session, job, lease_owner="worker", now=now)
        if change == "body": row.content += " New version"
        elif change == "title": row.title += " changed"
        elif change == "extensions": row.extensions_json = '{"note":"changed"}'
        elif change == "source": row.source_id = "other"
        elif change == "published": row.publish_date = (now - dt.timedelta(days=3)).isoformat()
        else: monkeypatch.setattr(service, "current_taxonomy_version", lambda _: 2)
        session.add(row); session.commit()
        if stage == "dispatch":
            assert service.dispatch_full_analysis_backfill(session, job, lease_owner="worker", now=now) == 0
        service.reconcile_full_analysis_backfill(session, job, lease_owner="worker", now=now)
        item = item_for(session, job)
        assert item.status == "failed" and item.last_error.startswith("cohort_")
        assert item.target_content_hash == frozen["items"]["a"]["content_hash"]
        assert json.loads(job.scope_json)["publication_cohort"] == frozen
        assert job.status == "partial_failed"
        with pytest.raises(service.AnalysisBackfillError, match="cohort_changed"):
            service.retry_failed_full_analysis_items(session, job, now=now)


def test_concurrently_completed_same_input_is_reused_without_queue(sink, now, monkeypatch):
    with Session(sink.engine) as session:
        row = article("a", now); session.add(row); session.commit(); job = create(session, now)
        seed_analysis(session, row, now)
        job = service.claim_full_analysis_backfill(session, lease_owner="worker", now=now)
        monkeypatch.setattr(service, "queue_article_analysis", lambda *_a, **_k: pytest.fail("must reuse"))
        assert service.dispatch_full_analysis_backfill(session, job, lease_owner="worker", now=now) == 0
        service.reconcile_full_analysis_backfill(session, job, lease_owner="worker", now=now)
        assert job.status == "succeeded"
        assert service.serialize_full_analysis_backfill(session, job)["execution_observations"]["a"]["taxonomy_version"] == 1


@pytest.mark.parametrize("mutation", ["hash", "shape"])
def test_corrupt_snapshot_fails_closed(sink, now, mutation):
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit(); job = create(session, now)
        scope = json.loads(job.scope_json)
        scope["publication_cohort"] = {"schema": 1} if mutation == "shape" else {**scope["publication_cohort"], "cohort_hash": "0" * 64}
        job.scope_json = json.dumps(scope); session.add(job); session.commit()
        job = service.claim_full_analysis_backfill(session, lease_owner="worker", now=now)
        with pytest.raises(service.AnalysisBackfillError, match="snapshot is invalid"):
            service.dispatch_full_analysis_backfill(session, job, lease_owner="worker", now=now)
        assert session.get(ArticleAnalysisRecord, "a") is None


def test_normal_worker_consumes_frozen_scope_with_fake_analyzer(sink, now, monkeypatch):
    from services import article_analysis
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit(); job_id = create(session, now).id
    # Live scans are a separately governed workflow, not part of this fixture's cohort.
    monkeypatch.setattr(article_analysis, "scan_analysis_backfill", lambda *_a, **_k: None)
    calls = []
    async def fake(input, _tags, _config):
        calls.append(input.article_id)
        return {"quality_score": 8.6, "score_reason": "Complete original analysis.",
                "content_kind": "news", "content_value": 6.4, "content_value_reason": "Specific mechanism.",
                "summary": "Explains planning and tools.", "content_genre": "product_update",
                "primary_tag_code": "topic.ai-agents", "tag_assignments": [
                    {"code": "topic.ai-agents", "kind": "topic", "relevance": 0.96}],
                "tag_candidates": [], "content_features": ["official_release"], "entities": []}
    asyncio.run(run_analysis_cycle(sink.engine, worker_id="fixture", enabled=True,
        candidate_enabled=False, analyzer=fake, batch_size=1, now_fn=lambda: now + dt.timedelta(seconds=2),
        llm_config=LLMConfig(base_url="https://llm.invalid/v1", api_key="fixture", model="fixture")))
    with Session(sink.engine) as session:
        job = session.get(TagRetagJobRecord, job_id)
        assert job.status == "succeeded" and calls == ["a"]
        assert item_for(session, job).status == "succeeded"
        assert "a" in service.serialize_full_analysis_backfill(session, job)["execution_observations"]


def test_real_router_estimate_commitment_and_admin_dependency(sink, now, monkeypatch):
    # Actual router with explicit isolated session; no api.app startup or listening socket.
    app = FastAPI(); app.include_router(analysis_ops.router)
    def session_dep():
        with Session(sink.engine) as session:
            yield session
    app.dependency_overrides[deps.get_session] = session_dep
    monkeypatch.setattr(analysis_ops.daily_brief_service, "resolve_llm_config", lambda _: LLMConfig(
        base_url="https://llm.invalid/v1", api_key="fixture", model="fixture"))
    with Session(sink.engine) as session:
        session.add(article("a", now)); session.commit()
    payload = {k: v for k, v in args(now).items() if k != "now"}
    statements = []
    def observe_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(sink.engine, "before_cursor_execute", observe_sql)
    with TestClient(app) as client:
        assert client.post("/api/admin/analysis/backfills/estimate", json=payload).status_code in (401, 403)
        app.dependency_overrides[deps.require_admin] = lambda: {"sub": "admin", "role": "admin"}
        est = client.post("/api/admin/analysis/backfills/estimate", json=payload)
        assert est.status_code == 200, est.text
        create_payload = {**payload, "confirmation": service.FULL_ANALYSIS_CONFIRMATION}
        assert client.post("/api/admin/analysis/backfills", json=create_payload).status_code == 409
        with Session(sink.engine) as session:
            row = session.get(ArticleRecord, "a"); row.content += " changed"; session.add(row); session.commit()
        stale = {**create_payload, "expected_cohort_hash": est.json()["cohort_hash"]}
        assert client.post("/api/admin/analysis/backfills", json=stale).status_code == 409
        with Session(sink.engine) as session:
            assert not session.exec(select(TagRetagJobRecord)).all()
        fresh = client.post("/api/admin/analysis/backfills/estimate", json=payload).json()
        ok = client.post("/api/admin/analysis/backfills", json={**create_payload, "expected_cohort_hash": fresh["cohort_hash"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["target_ids"] == ["a"]
        state = client.get(f'/api/admin/analysis/backfills/{ok.json()["job_id"]}')
        assert state.json()["cohort_hash"] == fresh["cohort_hash"]
    event.remove(sink.engine, "before_cursor_execute", observe_sql)
    # Real router flag/config SELECTs do not start a legacy sqlite DBAPI transaction.
    assert "BEGIN IMMEDIATE" in statements


@pytest.mark.parametrize("mutation", ["delete", "outside", "source"])
def test_create_detects_membership_drift(sink, now, mutation):
    with Session(sink.engine) as session:
        row = article("a", now); session.add(row); session.commit()
        params = args(now, source_ids=["public"])
        estimated = service.estimate_full_analysis_backfill(session, **params)
        if mutation == "delete": session.delete(row)
        else:
            if mutation == "outside": row.publish_date = (now - dt.timedelta(days=8)).isoformat()
            else: row.source_id = "moved"
            session.add(row)
        session.commit()
        with pytest.raises(service.AnalysisBackfillError, match="cohort_changed"):
            service.create_full_analysis_backfill(session, **params, actor_id="admin",
                confirmation=service.FULL_ANALYSIS_CONFIRMATION, expected_cohort_hash=estimated["cohort_hash"])
        assert not session.exec(select(TagRetagJobRecord)).all()


def test_pause_restart_does_not_reselect_or_refresh_snapshot(sink, now):
    with Session(sink.engine) as session:
        row = article("a", now); session.add(row); session.commit(); job = create(session, now)
        frozen = json.loads(job.scope_json)["publication_cohort"]
        job_id = job.id
        job = service.claim_full_analysis_backfill(session, lease_owner="first", now=now)
        service.dispatch_full_analysis_backfill(session, job, lease_owner="first", now=now)
        service.pause_full_analysis_backfill(session, job, now=now)
    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, "a"); row.content += " replaced while paused"
        session.add(row); session.add(article("late", now)); session.commit()
        job = session.get(TagRetagJobRecord, job_id)
        service.resume_full_analysis_backfill(session, job, now=now + dt.timedelta(days=1))
        job = service.claim_full_analysis_backfill(session, lease_owner="second", now=now + dt.timedelta(days=1))
        service.reconcile_full_analysis_backfill(session, job, lease_owner="second", now=now + dt.timedelta(days=1))
        assert job.status == "partial_failed" and item_for(session, job).last_error == "cohort_input_changed"
        assert json.loads(job.scope_json)["publication_cohort"] == frozen
        assert session.get(ArticleAnalysisRecord, "late") is None
