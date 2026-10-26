"""R2 reader title projection and independent account preference contract."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import seed_default_accounts
from models.db import ArticleRecord
from services import accounts, guest_access
from storage.impl.db_storage import DatabaseStorage


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    import api.app as app
    from config import RuntimeConfig

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'title-preferences.db'}")
    monkeypatch.setattr(app, "db_sink", sink)
    monkeypatch.setattr(app, "settings", replace(app.settings, runtime=RuntimeConfig(role="all")))
    seed_default_accounts(sink.engine)
    return app


def _login(client, username="admin"):
    response = client.post("/api/auth/login", json={"username": username, "password": username})
    assert response.status_code == 200
    return response.json()["user"]


def test_chinese_title_preference_patch_survives_session_and_does_not_change_landing(app_module):
    with TestClient(app_module.app) as client:
        assert _login(client)["prefer_chinese_titles"] is True
        first = client.post("/api/auth/preferences", json={"prefer_chinese_titles": False})
        assert first.status_code == 200
        assert first.json() == {"ok": True, "default_surface": "console", "prefer_chinese_titles": False}
        second = client.post("/api/auth/preferences", json={"default_surface": "reader"})
        assert second.json() == {"ok": True, "default_surface": "reader", "prefer_chinese_titles": False}
        assert client.post("/api/auth/preferences", json={}).json() == second.json()
        assert client.get("/api/auth/session").json()["user"]["prefer_chinese_titles"] is False
        runtime = client.get("/api/runtime").json()
        assert runtime["prefer_chinese_titles"] is False
        assert runtime["default_surface"] == "reader"
        assert _login(client, "user")["prefer_chinese_titles"] is True
        assert client.get("/api/runtime").json()["prefer_chinese_titles"] is True
        assert _login(client)["prefer_chinese_titles"] is False


@pytest.mark.parametrize("bad", ["false", 0, 1, [], {}, None])
def test_title_preference_requires_boolean_and_patch_is_atomic(app_module, bad):
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/auth/preferences", json={"default_surface": "reader", "prefer_chinese_titles": bad})
        assert response.status_code in (400, 422)
        runtime = client.get("/api/runtime").json()
        assert runtime["default_surface"] == "console"
        assert runtime["prefer_chinese_titles"] is True
        invalid_surface = client.post("/api/auth/preferences", json={"default_surface": "bogus", "prefer_chinese_titles": False})
        assert invalid_surface.status_code == 400
        assert client.get("/api/auth/session").json()["user"]["prefer_chinese_titles"] is True


def test_guest_preference_is_never_written_to_shared_account(app_module):
    with Session(app_module.db_sink.engine) as session:
        guest_access.set_guest_access_enabled(session, True)
        guest = accounts.ensure_guest_account(session, enabled=True)
        guest_name = guest.username
        with pytest.raises(accounts.AccountError):
            accounts.set_preferences(session, guest_name, prefer_chinese_titles=False)
    with TestClient(app_module.app) as client:
        response = client.post("/api/auth/guest", json={"entry": "login_page"})
        assert response.status_code == 200
        assert response.json()["user"]["prefer_chinese_titles"] is True
        assert client.post("/api/auth/preferences", json={"prefer_chinese_titles": False}).status_code == 403
        assert client.get("/api/runtime").json()["prefer_chinese_titles"] is True
    with Session(app_module.db_sink.engine) as session:
        assert accounts.get_user(session, guest_name).prefer_chinese_titles is True


def test_title_projection_is_lightweight_and_needs_current_original_fingerprint():
    from api.articles_view import serialize_article_list_item

    now = datetime.now(timezone.utc).isoformat()
    title = "A new approach to language models"
    fingerprint = hashlib.sha256(title.encode()).hexdigest()[:16]
    article = ArticleRecord(id="title-projection", title=title, content_type="rss_article", source_id="rss_platform", source_url="https://example.test/article", publish_date=now, fetched_date=now, content="Original body", has_content=True, extensions_json=json.dumps({"translation_zh_title": "语言模型的新方法", "translation_zh_title_fp": fingerprint, "translation_zh": "Secret full body"}))
    view = serialize_article_list_item(article, include_content=False, include_extensions=False)
    assert view["title"] == title
    assert view["title_zh"] == "语言模型的新方法"
    assert "content" not in view and "extensions" not in view
    assert "Secret full body" not in json.dumps(view)
    article.title = "A changed original title"
    assert serialize_article_list_item(article)["title_zh"] is None
    article.title = title
    article.extensions_json = json.dumps({"translation_zh_title": "无指纹旧标题"})
    assert serialize_article_list_item(article)["title_zh"] is None
    article.extensions_json = "[]"
    assert serialize_article_list_item(article)["title_zh"] is None


def test_existing_account_migration_defaults_on_and_keeps_later_opt_out(tmp_path):
    from alembic import command
    from sqlalchemy import MetaData, Table, create_engine, text
    from models.db import UserRecord
    from storage.migrations import make_alembic_config

    db_url = f"sqlite:///{tmp_path / 'title-preference-upgrade.db'}"
    config = make_alembic_config(db_url)
    command.upgrade(config, "d5b8e2a7c913")
    engine = create_engine(db_url)
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        users = Table("users", MetaData(), autoload_with=conn)
        old_user = UserRecord(username="existing-reader", password_hash="test", created_at=now, updated_at=now)
        conn.execute(users.insert().values(**{col.name: getattr(old_user, col.name) for col in users.columns}))
    engine.dispose()
    command.upgrade(config, "heads")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT prefer_chinese_titles FROM users WHERE username='existing-reader'")).scalar_one() == 1
        conn.execute(text("UPDATE users SET prefer_chinese_titles=0 WHERE username='existing-reader'"))
    engine.dispose()
    command.upgrade(config, "heads")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT prefer_chinese_titles FROM users WHERE username='existing-reader'")).scalar_one() == 0
    engine.dispose()
