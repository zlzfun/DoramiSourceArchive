"""注入入口回归(issue #262 波 0:B3、B6、B8、B11;手工录入 / 社交导入已于推送入口统一波退役)。"""

import asyncio
import datetime as dt
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


# ── 退役入口(issue #262 推送入口统一波)──────────────────────────────────────
# 手工录入与社交导入并入收录 POST /api/public/ingest/v1/items;原先这两个入口的回归
# (一账号一源、认回预设、保留 id、接收端 409、播客源围栏)由 tests/test_ingest_push.py 与
# tests/test_ingest_unify.py 在新入口上覆盖。


def test_retired_entry_points_no_longer_write(monkeypatch, tmp_path):
    from models.db import ArticleRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        manual = client.post("/api/articles", json={"id": "m", "source_id": "manual", "title": "t", "content": "x"})
        assert manual.status_code == 410
        social = client.post("/api/import/social-posts", json={"posts": [
            {"platform": "x", "post_id": "1", "author_handle": "someone", "text": "hi"},
        ]})
        assert social.status_code == 404
    with Session(app_module.db_sink.engine) as session:
        assert session.exec(select(ArticleRecord)).all() == []


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
