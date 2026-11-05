"""注入入口(手工录入 / 社交导入)回归(issue #262 波 0:B1–B3、B5、B6、B8–B11)。"""

import asyncio
import datetime as dt
import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402


def _setup_app(monkeypatch, tmp_path, name="ingest.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    seed_default_accounts(sink.engine, (("admin", "admin", "admin"),))
    return app_module


def _login(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def _article(engine, article_id):
    from models.db import ArticleRecord

    with Session(engine) as session:
        return session.get(ArticleRecord, article_id)


def _audit_rows(engine):
    from models.db import AdminAuditRecord

    with Session(engine) as session:
        return session.exec(select(AdminAuditRecord).order_by(AdminAuditRecord.id)).all()


# ── 手工录入 /api/articles ──────────────────────────────────────────────────


def test_manual_entry_keeps_caller_id_and_reports_precise_errors(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        created = client.post("/api/articles", json={
            "id": "manual_1",
            "source_id": "web_agent_example",
            "title": "Agent 收录的一篇",
            "source_url": "https://example.com/a",
            "content": "正文",
            "extensions_json": {"note": "对象形式也接受"},
        })
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["id"] == "manual_1"
        assert body["outcome"] == "created"

        duplicate = client.post("/api/articles", json={
            "id": "manual_1", "source_id": "web_agent_example", "title": "again", "content": "x",
        })
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "该条目已存在"

        # 同一 id 被别的源占着:不改写对方的行(B3)。
        foreign = client.post("/api/articles", json={
            "id": "manual_1", "source_id": "web_other", "title": "抢占", "content": "x",
        })
        assert foreign.status_code == 409
        assert "其它来源" in foreign.json()["detail"]

        # 不给 id 时按链接生成本源命名空间下的稳定 id。
        generated = client.post("/api/articles", json={
            "source_id": "web_agent_example", "title": "无 id", "source_url": "https://example.com/b",
            "content": "x",
        })
        assert generated.status_code == 200
        assert generated.json()["id"].startswith("web_agent_example_")

        # 坏扩展:整体忽略、录入照常(既有契约),回执如实报告(B10)。
        bad_json = client.post("/api/articles", json={
            "id": "manual_2", "title": "t", "content": "x", "extensions_json": "{not json",
        })
        assert bad_json.status_code == 200
        assert bad_json.json()["warnings"]

    record = _article(app_module.db_sink.engine, "manual_1")
    assert record.source_id == "web_agent_example"
    assert json.loads(record.extensions_json)["note"] == "对象形式也接受"
    rows = [row for row in _audit_rows(app_module.db_sink.engine) if row.path == "/api/articles"]
    assert rows and "web_agent_example" in rows[0].summary
    assert rows[0].target == "web_agent_example"


def test_manual_entry_rejects_reserved_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        for reserved in ("user_rss_abcdef123456", "generic_rss", "x_openrouter"):
            response = client.post("/api/articles", json={
                "id": "a", "source_id": reserved, "title": "t", "content": "x",
            })
            assert response.status_code == 400, reserved


def test_manual_podcast_episode_without_podcast_source_is_not_reported_as_duplicate(
    monkeypatch, tmp_path
):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/articles", json={
            "id": "ep1", "source_id": "podcast_unknown_show", "title": "单集",
            "content_type": "podcast_episode", "content": "x",
        })
    assert response.status_code == 400
    assert "播客源" in response.json()["detail"]


def test_storage_never_rewrites_a_row_owned_by_another_source(tmp_path):
    from api.articles_view import GenericContent
    from storage.impl.db_storage import SAVE_ID_CONFLICT, DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'conflict.db'}")
    placeholder = GenericContent(id="shared_1", title="占位", source_url="https://a", publish_date="2026-11-01", content="", has_content=False)
    placeholder.source_id = "web_owner"
    assert asyncio.run(sink.save(placeholder)) is True

    intruder = GenericContent(id="shared_1", title="改写", source_url="https://b", publish_date="2026-11-01", content="正文", has_content=True)
    intruder.source_id = "web_intruder"
    assert asyncio.run(sink.save_with_outcome(intruder)) == SAVE_ID_CONFLICT
    record = _article(sink.engine, "shared_1")
    assert (record.source_id, record.title, record.has_content) == ("web_owner", "占位", False)


# ── 社交导入 /api/import/social-posts ────────────────────────────────────────


def _post(post_id, handle="", **extra):
    return {"platform": "x", "post_id": post_id, "author_handle": handle, "text": f"post {post_id}", **extra}


def test_social_import_keeps_one_source_per_account_and_recognizes_presets(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/import/social-posts", json={"posts": [
            _post("1", "Kimi_Moonshot"),      # 预设 x_moonshot_ai 的 handle,大小写不敏感
            _post("2", "@new_lab"),           # 未登记账号 → 派生 x_new_lab
            _post("3", "new_lab"),
            _post("4"),                       # 没有作者信息、也没有批级 source_id → 报错
        ]})
    body = response.json()
    assert response.status_code == 200
    assert body["saved_count"] == 3
    assert body["error_count"] == 1 and body["errors"][0]["index"] == 3
    engine = app_module.db_sink.engine
    assert _article(engine, "x_moonshot_ai_1").source_id == "x_moonshot_ai"
    assert _article(engine, "x_new_lab_2").source_id == "x_new_lab"
    assert _article(engine, "x_new_lab_3").source_id == "x_new_lab"
    rows = [row for row in _audit_rows(engine) if row.path == "/api/import/social-posts"]
    assert rows and "导入社交动态 4 条" in rows[0].summary


def test_social_import_batch_source_is_only_a_fallback(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/import/social-posts", json={
            "source_id": "x_batch_bucket",
            "posts": [_post("1", "someone"), _post("2"), _post("3", source_id="x_explicit")],
        })
    assert response.json()["saved_count"] == 3
    engine = app_module.db_sink.engine
    assert _article(engine, "x_someone_1").source_id == "x_someone"
    assert _article(engine, "x_batch_bucket_2").source_id == "x_batch_bucket"
    assert _article(engine, "x_explicit_3").source_id == "x_explicit"


def test_social_import_is_refused_on_v2_consumer(monkeypatch, tmp_path):
    from services.sync_consumer_policy import activate_v2_consumer_mode

    app_module = _setup_app(monkeypatch, tmp_path)
    with Session(app_module.db_sink.engine) as session:
        activate_v2_consumer_mode(session, reason="test")
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/import/social-posts", json={"posts": [_post("1", "someone")]})
    assert response.status_code == 409


# ── B6:隐藏源排除于交付 ───────────────────────────────────────────────────


def _seed_article(engine, article_id, source_id, *, content_type="article", hours_ago=1):
    from models.db import ArticleRecord

    fetched = (dt.datetime.now() - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=article_id, content_type=content_type, source_id=source_id,
            source_url=f"https://example.com/{article_id}", publish_date=fetched,
            fetched_date=fetched, archive_updated_at=fetched, has_content=True, content="body",
            extensions_json="{}",
        ))
        session.commit()


def test_hidden_sources_are_excluded_from_feed_and_daily_brief_pool(monkeypatch, tmp_path):
    from services import daily_brief
    from services.source_visibility import set_source_hidden

    app_module = _setup_app(monkeypatch, tmp_path)
    engine = app_module.db_sink.engine
    _seed_article(engine, "web_visible_1", "web_visible")
    _seed_article(engine, "web_hidden_1", "web_hidden")
    with Session(engine) as session:
        set_source_hidden(session, "web_hidden", True)

    with TestClient(app_module.app) as client:
        _login(client)
        items = client.get("/api/feed/articles").json()["items"]
        markdown = client.get("/api/feed/articles.md").text
    assert [item["id"] for item in items] == ["web_visible_1"]
    assert "web_hidden_1" not in markdown

    with Session(engine) as session:
        candidates, _cursor, _scanned = daily_brief.collect_candidates(session, cursor="")
    assert {candidate.id for candidate in candidates} == {"web_visible_1"}


# ── B8:事件归并的形态兜底 ─────────────────────────────────────────────────


def test_event_source_meta_infers_shape_for_unregistered_sources(tmp_path):
    from services.event_assignment import load_source_meta
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'meta.db'}")
    _seed_article(sink.engine, "x_unlisted_1", "x_unlisted", content_type="social_post")
    _seed_article(sink.engine, "web_unlisted_1", "web_unlisted")
    with Session(sink.engine) as session:
        meta = load_source_meta(session, ["x_unlisted", "web_unlisted"])
    assert meta["x_unlisted"].shape == "social"
    assert meta["web_unlisted"].shape == "article"
    assert meta["x_unlisted"].role == "media"
