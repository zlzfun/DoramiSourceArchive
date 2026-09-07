"""WP-4 API wiring: strict reader scope, batch analysis views and edition lifecycle."""

import datetime as dt
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagEventRecord,
    CollectionJobRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserRecord,
)
from services import accounts as accounts_service
from storage.impl.db_storage import DatabaseStorage


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'wp4.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    local_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    with Session(sink.engine) as session:
        for username, role in (("alice", "user"), ("bob", "user"), ("admin", "admin")):
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password("pw"),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                AppSettingRecord(
                    key=f"reader_defaults_seeded:{username}", value=now
                )
            )
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true"))
        session.add(AppSettingRecord(key="article_analysis_enabled", value="true"))
        session.add(
            ReaderSubscriptionRecord(
                owner_username="alice",
                name="Only A",
                filters_json='{"source_ids":"source-a"}',
                delivery_policy_json="{}",
                token_hash="hash-a",
                token_preview="hash-a",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SourceStateRecord(
                source_id="source-a",
                fetcher_id="source-a",
                status="healthy",
                last_completed_at=local_now.isoformat(),
                last_success_at=local_now.isoformat(),
                updated_at=local_now.isoformat(),
            )
        )
        tag = CmsTagRecord(
            code="topic-agents",
            kind="topic",
            name_zh="智能体",
            name_en="Agents",
            normalized_name="agents",
            status="active",
            user_selectable=True,
            created_at=now,
            updated_at=now,
        )
        session.add(tag)
        session.flush()
        article_time = (local_now - dt.timedelta(hours=1)).isoformat()
        article = ArticleRecord(
            id="article-a",
            title="Agent release",
            content_type="web_article",
            source_id="source-a",
            source_url="https://example.com/a",
            publish_date=article_time,
            fetched_date=article_time,
            has_content=True,
            content="body",
        )
        hidden = ArticleRecord(
            id="article-b",
            title="Outside subscription",
            content_type="web_article",
            source_id="source-b",
            source_url="https://example.com/b",
            publish_date=article_time,
            fetched_date=article_time,
            has_content=True,
            content="body",
        )
        session.add(article)
        session.add(hidden)
        session.flush()
        for record, score in ((article, 8.8), (hidden, 9.9)):
            session.add(
                ArticleAnalysisRecord(
                    article_id=record.id,
                    status="succeeded",
                    tagging_status="succeeded",
                    quality_score=score,
                    score_reason=f"score {score}",
                    summary="unified summary",
                    content_genre="model_release",
                    primary_tag_id=tag.id,
                    content_hash=f"hash-{record.id}",
                    prompt_version="article-analysis-v1",
                    scoring_version="quality-v1",
                    analyzed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                ArticleTagAssignmentRecord(
                    article_id=record.id,
                    tag_id=tag.id,
                    tag_kind="topic",
                    is_primary=True,
                    relevance=0.9,
                    assignment_source="llm",
                    prompt_version="article-analysis-v1",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        tag_id = tag.id
    return app_module, sink, tag_id


def _login(client: TestClient, username: str):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "pw"}
    )
    assert response.status_code == 200
    return response.json()


def test_interest_and_personal_brief_api_are_subscription_strict(monkeypatch, tmp_path):
    app_module, _sink, tag_id = _setup(monkeypatch, tmp_path)
    with Session(_sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "article-a")
        analysis.display_tags_json = json.dumps([{
            "candidate_id": None,
            "label": "Agent Runtime",
            "kind": "topic",
            "confidence": 0.86,
        }])
        session.add(analysis)
        session.commit()
    with TestClient(app_module.app) as client:
        login = _login(client, "alice")
        assert login["user"]["interest_onboarding_completed"] is False
        assert client.get("/api/runtime").json()["personal_digest_enabled"] is True
        catalog = client.get("/api/reader/interests/catalog")
        assert catalog.status_code == 200
        assert [row["id"] for row in catalog.json()["items"]] == [tag_id]

        saved = client.put(
            "/api/reader/interests",
            json={
                "items": [{"tag_id": tag_id, "stance": "follow"}],
                "complete_onboarding": True,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["items"][0]["stance"] == "follow"
        assert "priority" not in saved.json()["items"][0]
        assert saved.json()["onboarding_completed"] is True
        assert client.get("/api/auth/session").json()["user"]["interest_onboarding_completed"] is True

        ensured = client.post("/api/reader/briefs/today/ensure")
        assert ensured.status_code == 200, ensured.text
        edition = ensured.json()["edition"]
        # With only one followed-interest article and no quality-lane peer, the
        # hard 50% actual-output ceiling correctly uses latest-update fallback.
        assert edition["status"] == "degraded", edition.get("error")
        assert edition["expected_source_ids"] == ["source-a"]
        assert [item["article_id"] for item in edition["items"]] == ["article-a"]
        assert edition["items"][0]["snapshot"]["summary"] == "unified summary"
        assert [row["type"] for row in edition["items"][0]["snapshot"]["display_tags"]] == [
            "canonical", "extracted",
        ]

        # Same-day ensure is idempotent and returns the current immutable revision.
        repeated = client.post("/api/reader/briefs/today/ensure").json()["edition"]
        assert repeated["id"] == edition["id"]
        assert repeated["revision"] == edition["revision"]
        assert repeated["status"] == "degraded"

        rebuilt = client.post("/api/reader/briefs/today/rebuild").json()["edition"]
        assert rebuilt["revision"] == edition["revision"] + 1
        by_revision = client.get(
            f"/api/reader/briefs/{edition['report_date']}?revision={edition['revision']}"
        )
        assert by_revision.status_code == 200
        assert by_revision.json()["id"] == edition["id"]
        latest = client.get(f"/api/reader/briefs/{edition['report_date']}")
        assert latest.status_code == 200
        assert latest.json()["id"] == rebuilt["id"]
        assert client.get(
            f"/api/reader/briefs/{edition['report_date']}?revision=999"
        ).status_code == 404


def test_personal_brief_accepts_persisted_public_brief_without_source_state(monkeypatch, tmp_path):
    app_module, sink, tag_id = _setup(monkeypatch, tmp_path)
    local_now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    now = local_now.isoformat()
    report_date = local_now.date().isoformat()
    with Session(sink.engine) as session:
        session.add(
            ReaderSubscriptionRecord(
                owner_username="alice",
                name="Public daily brief",
                filters_json='{"source_ids":"dorami_daily_brief"}',
                delivery_policy_json="{}",
                token_hash="hash-public-brief",
                token_preview="hash-public-brief",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        article = ArticleRecord(
            id=f"daily_brief_{report_date}",
            title=f"Daily brief {report_date}",
            content_type="daily_brief",
            source_id="dorami_daily_brief",
            source_url=f"dorami://daily-brief/{report_date}",
            publish_date=report_date,
            fetched_date=now,
            has_content=True,
            content="public brief body",
        )
        session.add(article)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=8.0,
                score_reason="public brief score",
                summary="public brief summary",
                content_genre="model_release",
                primary_tag_id=tag_id,
                content_hash="hash-public-brief",
                prompt_version="article-analysis-v1",
                scoring_version="quality-v1",
                analyzed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "alice")
        edition = client.post("/api/reader/briefs/today/ensure").json()["edition"]
        assert edition["status"] == "ready", edition.get("error")
        assert "dorami_daily_brief" in edition["expected_source_ids"]
        assert {item["article_id"] for item in edition["items"]} == {
            "article-a",
            f"daily_brief_{report_date}",
        }


def test_admin_can_configure_interest_top_n_and_must_classify_entities(monkeypatch, tmp_path):
    app_module, _sink, _tag_id = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        initial = client.get("/api/admin/taxonomy/interest-catalog-policy")
        assert initial.status_code == 200
        assert initial.json()["policy"]["limits"] == {
            "topic": 30,
            "industry": 15,
            "entity": 20,
        }
        updated = client.patch(
            "/api/admin/taxonomy/interest-catalog-policy",
            json={"topic": 1, "industry": 0, "entity": 2, "reason": "product decision"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["policy"]["limits"] == {
            "topic": 1,
            "industry": 0,
            "entity": 2,
        }
        invalid = client.post(
            "/api/admin/cms-tags",
            json={"code": "entity.mcp", "kind": "entity", "name_en": "MCP", "status": "active"},
        )
        assert invalid.status_code == 400
        created = client.post(
            "/api/admin/cms-tags",
            json={
                "code": "entity.mcp",
                "kind": "entity",
                "name_en": "MCP",
                "status": "active",
                "entity_type": "protocol",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["entity_type"] == "protocol"
        updated_tag = client.patch(
            f"/api/admin/cms-tags/{created.json()['id']}",
            json={
                "name_zh": "模型上下文协议",
                "name_en": "Model Context Protocol",
                "description": "连接模型与外部工具的协议。",
                "prompt_description": "仅在文章核心讨论 MCP 协议时使用。",
                "reason": "补齐双语规范名和模型边界",
            },
        )
        assert updated_tag.status_code == 200, updated_tag.text
        payload = updated_tag.json()
        assert payload["name_zh"] == "模型上下文协议"
        assert payload["name_en"] == "Model Context Protocol"
        assert payload["description"] == "连接模型与外部工具的协议。"
        assert payload["prompt_description"] == "仅在文章核心讨论 MCP 协议时使用。"
        assert any(alias["alias"] == "MCP" for alias in payload["aliases"])


def test_synced_taxonomy_is_read_only_on_internal_node(monkeypatch, tmp_path):
    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="taxonomy:authority_id", value="external-node"))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        assert client.get("/api/admin/cms-tags").status_code == 200
        response = client.patch(
            "/api/admin/taxonomy/interest-catalog-policy",
            json={"topic": 1, "industry": 1, "entity": 1, "reason": "should fail"},
        )
        assert response.status_code == 409
        assert "远端权威节点" in response.json()["detail"]


def test_v2_consumer_fence_makes_taxonomy_read_only_before_first_sync(
    monkeypatch,
    tmp_path,
):
    from services import sync_consumer_policy

    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="manual_v2")
        assert session.get(AppSettingRecord, "taxonomy:authority_id") is None

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        assert client.get("/api/admin/cms-tags").status_code == 200
        legacy_import = client.post(
            "/api/archive/import/articles.jsonl",
            content=b"",
            headers={"content-type": "application/x-ndjson"},
        )
        assert legacy_import.status_code == 409
        assert "禁止 legacy v1" in legacy_import.json()["detail"]
        response = client.post(
            "/api/admin/cms-tags",
            json={
                "code": "blocked-before-first-sync",
                "kind": "topic",
                "name_en": "Blocked",
            },
        )
        assert response.status_code == 409
        assert "远端权威节点" in response.json()["detail"]


def test_replica_deployment_makes_taxonomy_read_only_before_first_sync(
    monkeypatch,
    tmp_path,
):
    from api.routers import taxonomy as taxonomy_router

    app_module, _sink, _tag_id = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        taxonomy_router,
        "settings",
        SimpleNamespace(taxonomy=SimpleNamespace(mode="replica")),
    )

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        assert client.get("/api/admin/cms-tags").status_code == 200
        response = client.post(
            "/api/admin/cms-tags",
            json={
                "code": "blocked-replica-before-first-sync",
                "kind": "topic",
                "name_en": "Blocked Replica",
            },
        )
        assert response.status_code == 409
        assert "远端权威节点" in response.json()["detail"]


def test_v2_consumer_fence_blocks_public_source_and_article_crud_before_first_sync(
    monkeypatch,
    tmp_path,
):
    from services import sync_consumer_policy

    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with Session(sink.engine) as session:
        session.add_all([
            SourceConfigRecord(
                source_id="platform",
                name="Platform",
                source_type="rss",
                url="https://example.test/feed",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceConfigRecord(
                source_id="legacy-custom",
                name="Legacy custom",
                source_type="rss",
                url="https://example.test/custom",
                owner_username="alice",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            ArticleRecord(
                id="custom-article",
                title="Custom",
                content_type="rss_article",
                source_id="legacy-custom",
                source_url="https://example.test/custom/1",
                publish_date=now,
                fetched_date=now,
                has_content=True,
                content="body",
            ),
        ])
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="manual_v2")

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        assert client.post("/api/source-configs", json={
            "source_id": "new-platform",
            "name": "New platform",
        }).status_code == 409
        assert client.put(
            "/api/source-configs/platform", json={"name": "Changed"}
        ).status_code == 409
        assert client.post(
            "/api/source-configs/platform/toggle", json={"is_active": False}
        ).status_code == 409
        assert client.post("/api/source-configs/platform/fetch").status_code == 409
        assert client.delete("/api/source-configs/platform").status_code == 409

        custom_update = client.put(
            "/api/source-configs/legacy-custom", json={"name": "Still local"}
        )
        assert custom_update.status_code == 200

        assert client.post("/api/articles", json={
            "id": "manual-public",
            "title": "Manual public",
            "source_id": "manual",
            "content": "body",
        }).status_code == 409
        assert client.put(
            "/api/articles/article-a", json={"title": "Changed"}
        ).status_code == 409
        assert client.delete("/api/articles/article-a").status_code == 409
        assert client.post(
            "/api/articles/batch-delete", json={"ids": ["article-a"]}
        ).status_code == 409

        assert client.put(
            "/api/articles/custom-article", json={"title": "Changed locally"}
        ).status_code == 200
        assert client.delete("/api/articles/custom-article").status_code == 200


def test_empty_subscription_never_broadens_and_article_analysis_filters_work(monkeypatch, tmp_path):
    app_module, _sink, tag_id = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "bob")
        empty = client.post("/api/reader/briefs/today/ensure")
        assert empty.status_code == 200
        assert empty.json() == {"status": "empty_subscriptions", "edition": None}

        rows = client.get(
            f"/api/articles?min_score=9&tag_ids={tag_id}&sort=score&include_content=false"
        )
        assert rows.status_code == 200
        assert [row["id"] for row in rows.json()] == ["article-b"]
        assert rows.json()[0]["quality_score"] == 9.9
        assert rows.json()[0]["primary_tag"]["code"] == "topic-agents"

        detail = client.get("/api/articles/article-b/analysis")
        assert detail.status_code == 200, detail.text
        assert detail.json()["summary"] == "unified summary"
        assert detail.json()["tags"][0]["is_primary"] is True


def test_article_api_exposes_flexible_display_tags_and_admin_can_delete_candidate(monkeypatch, tmp_path):
    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with Session(sink.engine) as session:
        candidate = CmsTagCandidateRecord(
            label="Flexible Runtime Label",
            normalized_label="flexible runtime label",
            proposed_kind="topic",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(candidate)
        session.flush()
        session.add(CmsTagCandidateEvidenceRecord(
            candidate_id=int(candidate.id),
            article_id="article-b",
            source_id="source-b",
            confidence=0.88,
            raw_label=candidate.label,
            created_at=now,
        ))
        analysis = session.get(ArticleAnalysisRecord, "article-b")
        analysis.display_tags_json = json.dumps([{
            "candidate_id": int(candidate.id),
            "label": candidate.label,
            "kind": "topic",
            "confidence": 0.88,
        }])
        session.add(analysis)
        session.commit()
        candidate_id = int(candidate.id)

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        detail = client.get("/api/articles/article-b")
        assert detail.status_code == 200, detail.text
        assert "type" not in detail.json()["tags"][0]
        assert [row["type"] for row in detail.json()["display_tags"]] == [
            "canonical", "extracted",
        ]
        assert detail.json()["display_tags"][1]["label"] == "Flexible Runtime Label"
        analysis = client.get("/api/articles/article-b/analysis").json()
        assert len(analysis["display_tags"]) == 2
        temporary = client.get(
            "/api/articles",
            params={"display_tag": " flexible runtime label ", "include_content": "false"},
        )
        assert temporary.status_code == 200, temporary.text
        assert [row["id"] for row in temporary.json()] == ["article-b"]
        assert client.get(
            "/api/articles", params={"display_tag": "智能体"}
        ).json() == []  # canonical tags use tag_ids, not the flexible-label path

        deleted = client.delete(
            f"/api/admin/cms-tag-candidates/{candidate_id}",
            params={"reason": "API 验收：低质量标签"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["evidence_count"] == 1
        after = client.get("/api/articles/article-b").json()
        assert [row["type"] for row in after["display_tags"]] == ["canonical"]
        assert client.get(
            "/api/articles", params={"display_tag": "Flexible Runtime Label"}
        ).json() == []

    with Session(sink.engine) as session:
        assert session.get(CmsTagCandidateRecord, candidate_id) is None
        event = session.exec(
            select(CmsTagEventRecord).where(CmsTagEventRecord.action == "delete_candidate")
        ).one()
        assert json.loads(event.payload_json)["candidate_id"] == candidate_id


def test_first_open_waits_then_degrades_in_place_after_deadline(monkeypatch, tmp_path):
    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.db import PersonalDigestEditionRecord

    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "article-a")
        analysis.status = "pending"
        analysis.quality_score = None
        session.add(analysis)
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "alice")
        pending = client.post("/api/reader/briefs/today/ensure").json()["edition"]
        assert pending["status"] == "pending"
        assert pending["first_open_at"] is not None
        assert pending["readiness"]["sources"] == {
            "total": 0,
            "completed": 0,
            "pending": 0,
            "pending_sources": [],
        }
        assert pending["readiness"]["analysis"] == {
            "total": 1,
            "completed": 0,
            "pending": 1,
        }
        first_rebuild = client.post("/api/reader/briefs/today/rebuild").json()["edition"]
        repeated_rebuild = client.post("/api/reader/briefs/today/rebuild").json()["edition"]
        assert first_rebuild["id"] == pending["id"]
        assert repeated_rebuild["id"] == pending["id"]
        assert repeated_rebuild["revision"] == pending["revision"]
        assert repeated_rebuild["generation_reason"] == "manual_rebuild"

        with Session(sink.engine) as session:
            edition = session.get(PersonalDigestEditionRecord, pending["id"])
            edition.deadline_at = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
            ).isoformat()
            session.add(edition)
            session.commit()
        assert personal_briefs.process_pending_editions(sink.engine) == 1

        completed = client.get("/api/reader/briefs/today").json()["edition"]
        assert completed["id"] == pending["id"]
        assert completed["revision"] == pending["revision"]
        assert completed["status"] == "degraded"
        assert completed["degraded_reason"] == "no_qualified_content"
        assert completed["sync_stale"] is False
        assert completed["analysis_incomplete"] is True
        assert completed["readiness"] is None
        assert all(item["article_id"] != "article-b" for item in completed["items"])


def test_first_open_before_check_after_waits_even_if_deadline_looks_expired(monkeypatch, tmp_path):
    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.analysis_contracts import DigestGenerationReason
    from services import personal_digest

    report_day = dt.datetime.now(personal_digest.SHANGHAI).date()
    early = dt.datetime.combine(report_day, dt.time(7, 0), personal_digest.SHANGHAI)
    with Session(sink.engine) as session:
        pending = personal_digest.start_personal_digest_edition(
            session,
            "alice",
            now=early,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=early,
        ).edition
        assert pending is not None
        assert dt.datetime.fromisoformat(pending.deadline_at).time() == dt.time(8, 45)
        pending.deadline_at = (early - dt.timedelta(minutes=1)).isoformat()
        session.add(pending)
        session.commit()

        unchanged = personal_briefs.process_pending_edition(
            session,
            pending,
            now=early + dt.timedelta(minutes=30),
        )

        assert unchanged.status == "pending"


def test_deadline_source_staleness_is_separate_from_content_fallback(monkeypatch, tmp_path):
    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.analysis_contracts import DigestGenerationReason
    from services import personal_digest

    current = dt.datetime.now(personal_digest.SHANGHAI)
    with Session(sink.engine) as session:
        state = session.get(SourceStateRecord, "source-a")
        state.last_completed_at = (current - dt.timedelta(days=1)).isoformat()
        state.last_success_at = state.last_completed_at
        session.add(state)
        session.add(CollectionJobRecord(
            name="source-a schedule",
            fetcher_ids_json='["source-a"]',
            is_active=True,
            created_at=current.isoformat(),
            updated_at=current.isoformat(),
        ))
        session.commit()

        pending = personal_digest.start_personal_digest_edition(
            session,
            "alice",
            now=current,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=current,
        ).edition
        assert pending is not None
        assert personal_briefs.readiness_progress(session, pending, now=current)["sources"]["pending"] == 1
        pending.deadline_at = (current - dt.timedelta(seconds=1)).isoformat()
        session.add(pending)
        session.commit()

        completed = personal_briefs.process_pending_edition(session, pending, now=current)
        assert completed.status == "degraded"
        assert completed.sync_stale is True
        assert completed.analysis_incomplete is False
        assert completed.degraded_reason is None
        assert session.exec(
            select(ArticleRecord.id)
            .join(PersonalDigestItemRecord, PersonalDigestItemRecord.article_id == ArticleRecord.id)
            .where(PersonalDigestItemRecord.edition_id == completed.id)
        ).all() == ["article-a"]


def test_public_daily_brief_due_waits_for_todays_article_not_yesterdays(monkeypatch, tmp_path):
    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.analysis_contracts import DigestGenerationReason
    from services import personal_digest

    current = dt.datetime.combine(
        dt.datetime.now(personal_digest.SHANGHAI).date(),
        dt.time(8, 31),
        personal_digest.SHANGHAI,
    )
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="daily_brief_enabled", value="true"))
        session.add(ReaderSubscriptionRecord(
            owner_username="alice",
            name="Public brief",
            filters_json='{"source_ids":"dorami_daily_brief"}',
            delivery_policy_json="{}",
            token_hash="hash-daily",
            token_preview="hash-daily",
            is_active=True,
            created_at=current.isoformat(),
            updated_at=current.isoformat(),
        ))
        old = ArticleRecord(
            id="daily_brief_yesterday",
            title="Yesterday",
            content_type="daily_brief",
            source_id="dorami_daily_brief",
            source_url="",
            publish_date=(current.date() - dt.timedelta(days=1)).isoformat(),
            fetched_date=(current - dt.timedelta(hours=12)).isoformat(),
            has_content=True,
            content="old brief",
        )
        session.add(old)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=old.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=8.0,
            created_at=current.isoformat(),
            updated_at=current.isoformat(),
        ))
        session.commit()
        pending = personal_digest.start_personal_digest_edition(
            session,
            "alice",
            now=current,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=current,
        ).edition
        assert pending is not None
        assert "dorami_daily_brief" in json.loads(pending.due_source_ids_json)

        unchanged = personal_briefs.process_pending_edition(session, pending, now=current)

        assert unchanged.status == "pending"


def test_generation_exception_after_terminal_cas_rolls_back_before_marking_failed(monkeypatch, tmp_path):
    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.analysis_contracts import DigestGenerationReason
    from models.db import PersonalDigestEditionRecord, PersonalDigestItemRecord
    from services import personal_digest

    current = dt.datetime.combine(
        dt.datetime.now(personal_digest.SHANGHAI).date(),
        dt.time(9, 0),
        personal_digest.SHANGHAI,
    )
    with Session(sink.engine) as session:
        pending = personal_digest.start_personal_digest_edition(
            session,
            "alice",
            now=current,
            generation_reason=DigestGenerationReason.FIRST_OPEN,
            first_open_at=current,
        ).edition
        assert pending is not None

        def fail_snapshot(*_args, **_kwargs):
            raise RuntimeError("snapshot failed after terminal CAS")

        monkeypatch.setattr(personal_digest, "_snapshot", fail_snapshot)
        failed = personal_briefs.process_pending_edition(session, pending, now=current)
        edition_id = int(failed.id)

    with Session(sink.engine) as session:
        persisted = session.get(PersonalDigestEditionRecord, edition_id)
        items = list(session.exec(
            select(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id == edition_id)
        ).all())
        assert persisted.status == "failed"
        assert "snapshot failed" in persisted.error
        assert items == []


def test_custom_rss_participates_in_digest_analysis_readiness(monkeypatch, tmp_path):
    """An enabled custom RSS is analyzed locally and must be ready before selection."""

    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs
    from models.db import PersonalDigestEditionRecord

    now = dt.datetime.now(dt.timezone.utc)
    source_id = "user_rss_private"
    with Session(sink.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id=source_id,
                name="Private RSS",
                source_type="rss",
                owner_username="alice",
                ai_analysis_enabled=True,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        session.add(
            ArticleRecord(
                id="private-rss-article",
                title="Private article",
                content_type="web_article",
                source_id=source_id,
                source_url="https://example.com/private",
                publish_date=now.isoformat(),
                fetched_date=now.isoformat(),
                has_content=True,
                content="private body",
            )
        )
        session.commit()

        edition = PersonalDigestEditionRecord(
            owner_username="alice",
            report_date=now.date().isoformat(),
            timezone="UTC",
            revision=1,
            generation_reason="scheduled",
            expected_source_ids_json=json.dumps([source_id]),
            due_source_ids_json=json.dumps([source_id]),
            interest_snapshot_json="[]",
            status="pending",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        assert personal_briefs._analysis_ready(session, edition, now) is False
        session.add(
            ArticleAnalysisRecord(
                article_id="private-rss-article",
                status="succeeded",
                tagging_status="succeeded",
                quality_score=8.0,
                analyzed_at=now.isoformat(),
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        session.commit()
        assert personal_briefs._analysis_ready(session, edition, now) is True


def test_runtime_taxonomy_retag_worker_consumes_published_job(monkeypatch, tmp_path):
    app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from models.db import TagRetagJobRecord
    from services import taxonomy

    with Session(sink.engine) as session:
        version = taxonomy.create_taxonomy_version(
            session, change_summary="taxonomy v1"
        )
        taxonomy.activate_taxonomy_version(
            session, version.version, actor_id="admin"
        )
        job = taxonomy.queue_retag_job(
            session,
            taxonomy_version=version.version,
            scope={"article_ids": ["article-a"]},
        )
        version_number = version.version
        job_id = job.id

    app_module.execute_taxonomy_retag_job()

    with Session(sink.engine) as session:
        finished = session.get(TagRetagJobRecord, job_id)
        analysis = session.get(ArticleAnalysisRecord, "article-a")
        assert finished.status == "succeeded"
        assert finished.affected_count == 1
        assert analysis.taxonomy_version == version_number


def test_archive_write_stays_successful_when_analysis_enqueue_fails(monkeypatch, tmp_path):
    _app_module, sink, _tag_id = _setup(monkeypatch, tmp_path)
    from api.routers.archive_sync import import_archive_sync_jsonl
    from services import article_analysis

    def fail_queue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(article_analysis, "queue_article_analysis", fail_queue)
    line = {
        "kind": "article",
        "schema_version": "articles-jsonl-v1",
        "article": {
            "id": "archive-new",
            "title": "Archive",
            "content_type": "web_article",
            "source_id": "source-a",
            "source_url": "https://example.com/archive",
            "publish_date": dt.datetime.now(dt.timezone.utc).isoformat(),
            "fetched_date": dt.datetime.now(dt.timezone.utc).isoformat(),
            "has_content": True,
            "content": "body",
            "extensions": {},
        },
    }
    result = import_archive_sync_jsonl(json.dumps(line))
    assert result["status"] == "success"
    assert result["imported_count"] == 1
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "archive-new") is not None
