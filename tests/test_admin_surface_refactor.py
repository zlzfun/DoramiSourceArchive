"""Issue #76 管理面重构的接口卫生:播客任务三轴筛选 / 搜索 / 排序 / 单集详情,
音频资产分页 + 节目标题,标签统一总账,回填分页。"""

from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    PodcastProcessingRecord,
    SourceConfigRecord,
)
from services import podcast_premium  # noqa: E402
from services.podcast_premium import dashboard, episode_detail  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


STAMP = "2026-09-10T00:00:00+00:00"
LATER = "2026-09-12T00:00:00+00:00"


def _episode(episode_id: str, title: str = "", publish: str = STAMP) -> ArticleRecord:
    return ArticleRecord(
        id=episode_id,
        title=title or f"Episode {episode_id}",
        content_type="podcast_episode",
        source_id="podcast-refactor-test",
        source_url=f"https://example.test/{episode_id}",
        publish_date=publish,
        fetched_date=publish,
        content="show notes",
    )


def _analysis(episode_id: str, *, initial, final=None, updated=STAMP) -> ArticleAnalysisRecord:
    return ArticleAnalysisRecord(
        article_id=episode_id,
        status="succeeded",
        quality_score=final if final is not None else initial,
        podcast_initial_score=initial,
        podcast_final_score=final,
        analysis_basis=("asr_transcript" if final is not None else "podcast_show_notes"),
        analyzed_at=updated,
        created_at=STAMP,
        updated_at=updated,
    )


def _processing(episode_id: str, *, status: str, stage: str = "asr", error: str = "") -> PodcastProcessingRecord:
    running = status == "running"
    return PodcastProcessingRecord(
        id=f"processing-{episode_id}",
        episode_id=episode_id,
        input_fingerprint="a" * 64,
        pipeline_version="test-v1",
        policy_version="test-v1",
        requested_target="full_analysis",
        selection_source="policy",
        requested_by="system",
        request_reason="简介初评达到全文处理线",
        idempotency_key=f"key-{episode_id}",
        input_artifact_id=f"audio-{episode_id}",
        input_artifact_kind="source_media_snapshot",
        input_content_hash="b" * 64,
        input_language="und",
        budget_scope="podcast-test",
        budget_period="2026-09",
        budget_limit_minor=1000,
        per_run_budget_minor=100,
        eligibility_status="eligible",
        processing_status=status,
        stage=stage,
        attempt_count=1,
        lease_owner="worker" if running else None,
        lease_token="lease" if running else None,
        lease_expires_at=LATER if running else None,
        next_retry_at=LATER if status == "retry_wait" else None,
        error_code="provider_failed" if error else "",
        error_message=error,
        queued_at=STAMP,
        updated_at=LATER,
        finished_at=None if running else STAMP,
        created_at=STAMP,
    )


@pytest.fixture()
def refactor_engine(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'podcast-refactor.db'}")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-refactor-test",
            name="Refactor Show",
            source_type="podcast",
            url="https://example.test/feed.xml",
            fetcher_id="generic_podcast_rss",
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(_episode("raw", "No analysis yet"))
        session.add(_episode("rejected", "Consumer AI"))
        session.add(_episode("waiting", "Vibe coding"))
        session.add(_episode("running", "Sholto and Trenton"))
        session.add(_episode("broken", "Cursor team"))
        session.add(_episode("reconcile", "Building agents"))
        session.add(_episode("premium", "Mike Krieger on Claude Code", publish=LATER))
        session.add(_episode("below", "Pricing war"))
        session.commit()
        session.add(_analysis("rejected", initial=4.5))
        session.add(_analysis("waiting", initial=5.5))
        session.add(_analysis("running", initial=7.5))
        session.add(_analysis("broken", initial=6.0))
        session.add(_analysis("reconcile", initial=6.5))
        session.add(_analysis("premium", initial=7.0, final=8.7, updated=LATER))
        session.add(_analysis("below", initial=8.0, final=7.6))
        session.add(_processing("running", status="running"))
        session.add(_processing("broken", status="failed", error="音频地址 403"))
        session.add(_processing("reconcile", status="reconciliation_required", error="任务结果待核对"))
        session.commit()
        below = session.get(ArticleRecord, "below")
        below.extensions_json = json.dumps({"premium_guide": {"status": "failed", "failed_stage": "synthesizing", "error": "合成超时", "updated_at": LATER}})
        session.add(below)
        session.commit()
    yield sink.engine
    sink.engine.dispose()


def test_dashboard_axes_filters_sort_and_breakdown(refactor_engine):
    result = dashboard(refactor_engine)
    by_id = {item["episode_id"]: item for item in result["items"]}
    assert by_id["raw"]["stage_code"] == "not_processed"
    assert by_id["rejected"]["stage_code"] == "not_selected"
    assert by_id["waiting"]["stage_code"] == "awaiting_transcript"
    assert by_id["running"]["stage_code"] == "processing"
    assert by_id["broken"]["stage_code"] == "failed"
    # 待对账是独立阶段,不再折进 failed(归并稿 P1 #2)
    assert by_id["reconcile"]["stage_code"] == "reconciliation"
    assert by_id["reconcile"]["stage"] == "failed"  # 旧字段保持兼容
    assert by_id["premium"]["stage_code"] == "full_analyzed"
    assert by_id["premium"]["verdict"] == "premium"
    assert by_id["below"]["verdict"] == "below_threshold"
    assert by_id["waiting"]["verdict"] == "unscored"
    assert by_id["premium"]["updated_at"] == LATER
    assert by_id["broken"]["processing_error"] == "音频地址 403"
    assert result["breakdown"]["stage"] == {
        "not_processed": 1, "not_selected": 1, "awaiting_transcript": 1, "processing": 1,
        "full_analyzed": 2, "reconciliation": 1, "failed": 1,
    }
    assert result["breakdown"]["verdict"] == {"premium": 1, "below_threshold": 1, "unscored": 6}
    assert result["breakdown"]["tts"] == {"not_started": 7, "active": 0, "ready": 0, "failed": 1}
    assert result["breakdown"]["shows"] == 1

    assert [i["episode_id"] for i in dashboard(refactor_engine, stage="reconciliation")["items"]] == ["reconcile"]
    assert {i["episode_id"] for i in dashboard(refactor_engine, verdict="unscored")["items"]} == {
        "raw", "rejected", "waiting", "running", "broken", "reconcile",
    }
    assert [i["episode_id"] for i in dashboard(refactor_engine, tts="failed")["items"]] == ["below"]
    assert [i["episode_id"] for i in dashboard(refactor_engine, q="claude")["items"]] == ["premium"]
    assert [i["episode_id"] for i in dashboard(refactor_engine, q="Refactor Show", stage="not_processed")["items"]] == ["raw"]
    scored = [i["episode_id"] for i in dashboard(refactor_engine, sort="score", order="desc")["items"]]
    assert scored[:3] == ["premium", "below", "running"] and scored[-1] == "raw"
    assert dashboard(refactor_engine, sort="updated", order="desc")["items"][0]["episode_id"] in {"premium", "running", "broken", "reconcile", "below"}
    # 旧 status 档位与新轴可叠加
    assert [i["episode_id"] for i in dashboard(refactor_engine, status_filter="failed", stage="failed")["items"]] == ["broken"]
    with pytest.raises(ValueError):
        dashboard(refactor_engine, stage="nope")
    with pytest.raises(ValueError):
        dashboard(refactor_engine, sort="nope")


def test_episode_detail_timeline_and_texts(refactor_engine):
    assert episode_detail(refactor_engine, "missing") is None
    detail = episode_detail(refactor_engine, "premium")
    assert detail["item"]["episode_id"] == "premium"
    assert detail["episode"]["source_name"] == "Refactor Show"
    steps = {row["step"]: row for row in detail["timeline"]}
    assert [row["step"] for row in detail["timeline"]] == ["initial", "fetch", "asr", "analyze", "guide", "tts"]
    assert steps["initial"]["state"] == "done" and "过处理线" in steps["initial"]["note"]
    assert steps["analyze"]["state"] == "done" and "8.7" in steps["analyze"]["note"]
    assert steps["guide"]["state"] == "pending"
    assert steps["tts"]["state"] == "pending"

    broken = {row["step"]: row for row in episode_detail(refactor_engine, "broken")["timeline"]}
    assert broken["fetch"]["state"] == "done"
    assert broken["asr"]["state"] == "fail" and "403" in broken["asr"]["note"]
    assert broken["analyze"]["state"] == "pending"
    assert broken["guide"]["state"] == "pending"

    rejected = {row["step"]: row for row in episode_detail(refactor_engine, "rejected")["timeline"]}
    assert "未过处理线" in rejected["initial"]["note"]
    assert rejected["fetch"]["state"] == "pending"

    below = episode_detail(refactor_engine, "below")
    below_steps = {row["step"]: row for row in below["timeline"]}
    assert below_steps["guide"]["state"] == "skipped"
    assert below_steps["tts"]["state"] == "fail" and "合成超时" in below_steps["tts"]["note"]
    assert below["texts"] == {}
    assert below["artifacts"] == []


def test_premium_tasks_endpoint_accepts_axes(monkeypatch, refactor_engine):
    import api.app as app_module

    class _Sink:
        engine = refactor_engine

    seed_default_accounts(refactor_engine)
    monkeypatch.setattr(app_module, "db_sink", _Sink())
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        listed = client.get("/api/admin/podcast-premium-tasks", params={"stage": "failed", "sort": "score", "order": "asc"})
        assert listed.status_code == 200
        assert [i["episode_id"] for i in listed.json()["items"]] == ["broken"]
        assert client.get("/api/admin/podcast-premium-tasks", params={"stage": "bogus"}).status_code == 422
        detail = client.get("/api/admin/podcast-premium-tasks/premium")
        assert detail.status_code == 200
        assert detail.json()["item"]["verdict"] == "premium"
        assert client.get("/api/admin/podcast-premium-tasks/nope").status_code == 404


def test_artifact_list_pagination_search_and_titles(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import _import, _login, _setup_app

    app_module, sink, _store = _setup_app(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        for episode_id, title in (("episode-1", "Latent Space · State of AI"), ("episode-2", "Hard Fork · Pricing")):
            article = session.get(ArticleRecord, episode_id)
            article.title = title
            session.add(article)
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        first = _import(client, "episode-1").json()
        second = _import(client, "episode-2").json()
        listed = client.get("/api/admin/podcast-artifacts").json()
        assert listed["total"] == 2 and listed["offset"] == 0 and listed["limit"] == 100
        assert {item["id"] for item in listed["items"]} == {first["id"], second["id"]}
        titles = {item["episode_id"]: item["episode_title"] for item in listed["items"]}
        assert titles == {"episode-1": "Latent Space · State of AI", "episode-2": "Hard Fork · Pricing"}
        assert all(item["source_name"] == "podcast_test" for item in listed["items"])

        page = client.get("/api/admin/podcast-artifacts", params={"limit": 1, "offset": 1, "sort": "created", "order": "asc"}).json()
        assert page["total"] == 2 and len(page["items"]) == 1
        assert page["items"][0]["id"] == second["id"]

        found = client.get("/api/admin/podcast-artifacts", params={"q": "hard fork"}).json()
        assert found["total"] == 1 and found["items"][0]["episode_id"] == "episode-2"
        by_id = client.get("/api/admin/podcast-artifacts", params={"q": "episode-1"}).json()
        assert by_id["total"] == 1 and by_id["items"][0]["id"] == first["id"]
        assert client.get("/api/admin/podcast-artifacts", params={"sort": "bogus"}).status_code == 422


def _seed_ledger(session):
    now = "2026-09-14T08:00:00+00:00"
    old = "2026-08-01T08:00:00+00:00"
    tags = []
    for code, kind, zh, en, status, selectable in (
        ("topic.agent-orchestration", "topic", "Agent 编排", "Agent orchestration", "active", True),
        ("topic.reasoning-models", "topic", "推理模型", "Reasoning models", "active", True),
        ("entity.anthropic", "entity", "Anthropic", "Anthropic", "active", False),
        ("topic.vector-databases", "topic", "向量数据库", "Vector databases", "deprecated", False),
    ):
        tag = CmsTagRecord(
            code=code, kind=kind, name_zh=zh, name_en=en, normalized_name=en.lower(), status=status,
            user_selectable=selectable, entity_type="organization" if kind == "entity" else "",
            created_at=old, updated_at=old,
        )
        session.add(tag)
        tags.append(tag)
    session.flush()
    session.add(CmsTagAliasRecord(tag_id=tags[0].id, kind="topic", alias="agentic workflow", normalized_alias="agentic workflow", alias_type="synonym", created_at=old, updated_at=old))
    session.add(CmsTagAliasRecord(tag_id=tags[0].id, kind="topic", alias="多智能体编排", normalized_alias="多智能体编排", alias_type="translation", created_at=old, updated_at=old))
    for index in range(3):
        article = ArticleRecord(
            id=f"ledger-{index}", title=f"Article {index}", content_type="web_article",
            source_id=f"source-{index % 2}", source_url=f"https://example.test/{index}",
            publish_date=now, fetched_date=now, content="body",
        )
        session.add(article)
    session.add(ArticleRecord(
        id="ledger-old", title="Old article", content_type="web_article", source_id="source-0",
        source_url="https://example.test/old", publish_date=old, fetched_date=old, content="body",
    ))
    session.flush()
    for index in range(3):
        session.add(ArticleTagAssignmentRecord(article_id=f"ledger-{index}", tag_id=tags[0].id, tag_kind="topic", is_primary=True, relevance=0.9, created_at=now, updated_at=now))
    session.add(ArticleTagAssignmentRecord(article_id="ledger-0", tag_id=tags[1].id, tag_kind="topic", is_primary=False, relevance=0.8, created_at=now, updated_at=now))
    session.add(ArticleTagAssignmentRecord(article_id="ledger-old", tag_id=tags[1].id, tag_kind="topic", is_primary=False, relevance=0.8, created_at=old, updated_at=old))
    candidate = CmsTagCandidateRecord(
        label="MCP 协议", normalized_label="mcp 协议", proposed_kind="topic", status="reviewing",
        support_article_count_7d=37, support_article_count_30d=60, distinct_source_count_7d=9, distinct_source_count_30d=12,
        distinct_day_count_7d=6, distinct_day_count_30d=20, mean_confidence=0.88, nearest_tag_id=tags[0].id,
        nearest_similarity=0.62, risk_flags_json=json.dumps(["similar_to_existing"]),
        first_seen_at=old, last_seen_at=now, created_at=old, updated_at=now,
    )
    rejected = CmsTagCandidateRecord(
        label="Vibe Coding", normalized_label="vibe coding", proposed_kind="topic", status="rejected",
        support_article_count_7d=1, distinct_source_count_7d=1, mean_confidence=0.5,
        first_seen_at=old, last_seen_at=old, created_at=old, updated_at=old,
    )
    session.add(candidate)
    session.add(rejected)
    session.flush()
    for index in range(2):
        session.add(CmsTagCandidateEvidenceRecord(
            candidate_id=candidate.id, article_id=f"ledger-{index}", source_id=f"source-{index}",
            source_owner_or_domain="example.test", published_date="2026-09-13", confidence=0.9,
            raw_label="MCP", context_excerpt="…", created_at=now,
        ))
    session.commit()
    return [tag.id for tag in tags], candidate.id


def test_taxonomy_ledger_unifies_tags_and_candidates(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'ledger.db'}")
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        tag_ids, candidate_id = _seed_ledger(session)
    monkeypatch.setattr(app_module, "db_sink", sink)
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        ledger = client.get("/api/admin/taxonomy/ledger").json()
        assert ledger["total"] == 6 and ledger["counts"] == {"tags": 4, "candidates": 2}
        # 默认按近 7 天命中降序:候选 37 > Agent 编排 3 > 推理模型 1(旧文章不计)
        assert [(row["type"], row.get("label") or row.get("name_zh")) for row in ledger["items"][:3]] == [
            ("candidate", "MCP 协议"), ("tag", "Agent 编排"), ("tag", "推理模型"),
        ]
        agent = next(row for row in ledger["items"] if row.get("code") == "topic.agent-orchestration")
        assert agent["hits_7d"] == 3 and agent["sources_7d"] == 2 and agent["alias_count"] == 2
        reasoning = next(row for row in ledger["items"] if row.get("code") == "topic.reasoning-models")
        assert reasoning["hits_7d"] == 1
        mcp = next(row for row in ledger["items"] if row["type"] == "candidate" and row["label"] == "MCP 协议")
        assert mcp["evidence_count"] == 2 and mcp["nearest_tag_name"] == "Agent 编排" and mcp["risk_flags"] == ["similar_to_existing"]

        only_tags = client.get("/api/admin/taxonomy/ledger", params={"type": "tag"}).json()
        assert only_tags["total"] == 4 and all(row["type"] == "tag" for row in only_tags["items"])
        by_status = client.get("/api/admin/taxonomy/ledger", params={"status": "reviewing"}).json()
        assert [row["label"] for row in by_status["items"]] == ["MCP 协议"]
        by_alias = client.get("/api/admin/taxonomy/ledger", params={"q": "agentic"}).json()
        assert [row["code"] for row in by_alias["items"]] == ["topic.agent-orchestration"]
        by_kind = client.get("/api/admin/taxonomy/ledger", params={"kind": "entity"}).json()
        assert [row["code"] for row in by_kind["items"]] == ["entity.anthropic"]
        page = client.get("/api/admin/taxonomy/ledger", params={"limit": 2, "offset": 2}).json()
        assert page["total"] == 6 and len(page["items"]) == 2
        assert client.get("/api/admin/taxonomy/ledger", params={"type": "bogus"}).status_code == 422

        tag_detail = client.get(f"/api/admin/cms-tags/{tag_ids[0]}").json()
        assert tag_detail["code"] == "topic.agent-orchestration" and len(tag_detail["aliases"]) == 2
        candidate_detail = client.get(f"/api/admin/cms-tag-candidates/{candidate_id}").json()
        assert candidate_detail["label"] == "MCP 协议" and len(candidate_detail["evidence"]) == 2
        assert client.get("/api/admin/cms-tags/999999").status_code == 404
        assert client.get("/api/admin/cms-tag-candidates/999999").status_code == 404


def test_backfill_list_reports_total_and_offset(tmp_path):
    from models.db import TagRetagJobRecord, TaxonomyVersionRecord
    from services import analysis_backfill

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'backfill-page.db'}")
    with Session(sink.engine) as session:
        assert analysis_backfill.count_full_analysis_backfills(session) == 0
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=STAMP))
        session.commit()
        for index in range(3):
            session.add(TagRetagJobRecord(
                operation="full_analysis", status="succeeded", taxonomy_version=1, affected_count=0,
                scope_json=json.dumps({"days": 7, "selection": "all"}),
                created_at=f"2026-09-1{index}T00:00:00+00:00", updated_at=f"2026-09-1{index}T00:00:00+00:00",
            ))
        session.commit()
        assert analysis_backfill.count_full_analysis_backfills(session) == 3
        listed = analysis_backfill.list_full_analysis_backfills(session, limit=2, offset=1)
        assert [job["job_id"] for job in listed] == [2, 1]


def test_module_exports_new_filter_vocabulary():
    assert podcast_premium.STAGE_CODES[-2:] == ("reconciliation", "failed")
    assert set(podcast_premium.VERDICT_CODES) == {"premium", "below_threshold", "unscored"}
