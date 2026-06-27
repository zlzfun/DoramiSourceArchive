"""读者文章收藏 + 账户头像编辑的端到端测试。

复用 test_subscriptions.py 的播种/登录约定：账户数据库托管，runtime=reader
即可命中 reader 面（/api/reader/* 与 /api/auth/avatar）。
"""

import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    from services import accounts as accounts_service
    from models.db import UserRecord

    with Session(engine) as session:
        for username, password, role in accounts:
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(password),
                role=role,
                is_active=True,
                created_at=__import__("datetime").datetime.now().isoformat(),
                updated_at=__import__("datetime").datetime.now().isoformat(),
            ))
        session.commit()


def _set_runtime_role(monkeypatch, app_module, role: str):
    from config import RuntimeConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role=role)),
    )


def _seed_article(engine, article_id: str, source_id: str, title: str):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(
            ArticleRecord(
                id=article_id,
                title=title,
                content_type="rss_article",
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date="2026-05-20T00:00:00",
                fetched_date="2026-05-21T00:00:00",
                has_content=True,
                content=f"{title} body",
                extensions_json="{}",
                is_vectorized=False,
            )
        )
        session.commit()


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _prepare(monkeypatch, tmp_path, name: str):
    import api.app as app_module

    sink = _make_sink(tmp_path, name)
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    _set_runtime_role(monkeypatch, app_module, "reader")
    return app_module, sink


# ==================== 头像 ====================
_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_avatar_update_and_reflected_in_session(monkeypatch, tmp_path):
    app_module, _ = _prepare(monkeypatch, tmp_path, "avatar.db")

    with TestClient(app_module.app) as client:
        _login(client)

        # 初始无头像
        session = client.get("/api/auth/session").json()
        assert session["user"]["avatar"] is None

        # 设置头像
        resp = client.post("/api/auth/avatar", json={"avatar": _PNG_DATA_URL})
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["avatar"] == _PNG_DATA_URL

        # 会话/登录响应都带回头像
        assert client.get("/api/auth/session").json()["user"]["avatar"] == _PNG_DATA_URL

        # 清除头像
        cleared = client.post("/api/auth/avatar", json={"avatar": ""})
        assert cleared.status_code == 200
        assert cleared.json()["user"]["avatar"] is None
        assert client.get("/api/auth/session").json()["user"]["avatar"] is None


def test_avatar_rejects_non_image(monkeypatch, tmp_path):
    app_module, _ = _prepare(monkeypatch, tmp_path, "avatar_bad.db")

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/auth/avatar", json={"avatar": "data:text/plain;base64,Zm9v"})
        assert resp.status_code == 400


# ==================== 收藏 ====================
def test_favorite_add_list_remove_flow(monkeypatch, tmp_path):
    app_module, sink = _prepare(monkeypatch, tmp_path, "fav.db")
    _seed_article(sink.engine, "a1", "rss_openai", "Alpha")
    _seed_article(sink.engine, "a2", "rss_hf", "Beta")

    with TestClient(app_module.app) as client:
        _login(client)

        # 空收藏
        assert client.get("/api/reader/favorites").json()["total"] == 0

        # 收藏两篇
        r1 = client.post("/api/reader/favorites/a1")
        assert r1.status_code == 200
        assert r1.json()["favorited"] is True
        assert set(r1.json()["favorite_ids"]) == {"a1"}
        r2 = client.post("/api/reader/favorites/a2")
        assert set(r2.json()["favorite_ids"]) == {"a1", "a2"}

        # 幂等：重复收藏不报错、不重复
        again = client.post("/api/reader/favorites/a1")
        assert set(again.json()["favorite_ids"]) == {"a1", "a2"}

        # 列表按收藏时间倒序（a2 后收藏 → 排前）
        listing = client.get("/api/reader/favorites").json()
        assert listing["total"] == 2
        assert [item["id"] for item in listing["items"]] == ["a2", "a1"]

        # 搜索过滤
        searched = client.get("/api/reader/favorites", params={"search": "Alpha"}).json()
        assert [item["id"] for item in searched["items"]] == ["a1"]

        # 取消收藏
        removed = client.delete("/api/reader/favorites/a1")
        assert removed.json()["favorited"] is False
        assert set(removed.json()["favorite_ids"]) == {"a2"}
        assert client.get("/api/reader/favorites").json()["total"] == 1


def test_favorite_nonexistent_article_404(monkeypatch, tmp_path):
    app_module, _ = _prepare(monkeypatch, tmp_path, "fav_404.db")

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/favorites/ghost").status_code == 404


def test_favorites_are_scoped_per_user(monkeypatch, tmp_path):
    app_module, sink = _prepare(monkeypatch, tmp_path, "fav_scope.db")
    _seed_article(sink.engine, "a1", "rss_openai", "Alpha")

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        client.post("/api/reader/favorites/a1")
        assert client.get("/api/reader/favorites").json()["total"] == 1

    # 另一账户（admin 也是 reader 超集）看不到 user 的收藏
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert client.get("/api/reader/favorites").json()["total"] == 0
