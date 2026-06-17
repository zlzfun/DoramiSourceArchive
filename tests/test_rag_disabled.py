"""RAG 总开关关闭路径的回归测试。

当 [rag] enabled = false 时：
- 模块级 vector_sink 不应被构造（保持 None）。
- 所有 /api/vector/* /api/vectorize/* /api/rag/* /api/vector/auto-vectorize 返回 503。
- /api/runtime 暴露 rag_enabled = false。
- auto_vectorize_after_fetch() 应为 no-op，不调用 db_sink。
"""
import asyncio
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _set_auth_accounts(monkeypatch, app_module):
    """账户已迁移到数据库托管：将测试账户播种进当前 db_sink 的 users 表。"""
    from sqlmodel import Session
    from models.db import UserRecord
    from services import accounts as accounts_service

    with Session(app_module.db_sink.engine) as session:
        for username, password, role in (("admin", "admin", "admin"), ("user", "user", "user")):
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            accounts_service.create_user(session, username, password, role)


def _disable_rag(monkeypatch, app_module):
    """模拟 [rag] enabled=false 启动：vector_sink=None + settings.rag.enabled=False。"""
    from config import RagConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, rag=RagConfig(enabled=False)),
    )
    monkeypatch.setattr(app_module, "vector_sink", None)


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def test_runtime_capabilities_reports_rag_disabled(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "rag_off.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _disable_rag(monkeypatch, app_module)

    with TestClient(app_module.app) as client:
        _login(client)
        runtime = client.get("/api/runtime").json()
        assert runtime["rag_enabled"] is False


def test_vector_endpoints_return_503_when_disabled(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "rag_off_endpoints.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _disable_rag(monkeypatch, app_module)

    with TestClient(app_module.app) as client:
        _login(client)

        # 不同形态都应 503。
        assert client.get("/api/vector/stats").status_code == 503
        assert client.get("/api/vector/auto-vectorize").status_code == 503
        assert client.post("/api/vector/auto-vectorize", json={"enabled": True}).status_code == 503
        assert client.post("/api/vectorize/batch", json={"ids": ["x"]}).status_code == 503
        assert client.post("/api/vectorize/all-pending").status_code == 503
        assert client.post("/api/vectorize/any-id").status_code == 503
        assert client.post(
            "/api/rag/context", json={"query": "anything"},
        ).status_code == 503
        assert client.delete("/api/vector/some-id").status_code == 503
        assert client.post("/api/vector/batch-delete", json={"ids": ["x"]}).status_code == 503
        assert client.post("/api/vector/reindex-all").status_code == 503


def test_vector_search_user_no_subscriptions_returns_empty_without_loading_model(monkeypatch, tmp_path):
    """user 账号无订阅时，硬性范围拦截在 require_vector_sink() 之前返回空集，不应 503。"""
    import api.app as app_module

    sink = _make_sink(tmp_path, "rag_off_empty.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _disable_rag(monkeypatch, app_module)
    # 强制 reader 角色，否则 admin 的检索不会硬性限定 → 触发 require_vector_sink → 503。
    from config import RuntimeConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="reader")),
    )

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        resp = client.post("/api/vector/search", json={"query": "anything"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == []
        assert body["scoped"] is True


def test_auto_vectorize_after_fetch_is_noop_when_disabled(monkeypatch, tmp_path):
    """RAG 关闭时，自动向量化旁路应直接 return，不应触达 db_sink 查询。"""
    import api.app as app_module

    sink = _make_sink(tmp_path, "rag_off_auto.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _disable_rag(monkeypatch, app_module)

    db_get_called = {"count": 0}
    original_get = sink.get

    async def counted_get(content_id):
        db_get_called["count"] += 1
        return await original_get(content_id)

    monkeypatch.setattr(sink, "get", counted_get)

    asyncio.run(app_module.auto_vectorize_after_fetch(["fake_id_a", "fake_id_b"]))
    # 即使传了 ids，未启用 RAG 应当短路，不查询 db_sink。
    assert db_get_called["count"] == 0


def test_article_delete_still_works_when_disabled(monkeypatch, tmp_path):
    """RAG 关闭时，文章 CRUD 应继续工作，跳过向量删除分支。"""
    import api.app as app_module
    from models.db import ArticleRecord
    from sqlmodel import Session

    sink = _make_sink(tmp_path, "rag_off_crud.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _disable_rag(monkeypatch, app_module)

    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="article_old",
            title="legacy",
            content_type="rss_article",
            source_id="rss_openai",
            source_url="https://example.test/legacy",
            publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00",
            has_content=True,
            content="legacy body",
            extensions_json="{}",
            is_vectorized=True,  # 历史标记为已向量化
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.delete("/api/articles/article_old")
        # 即便 is_vectorized=True，因为 vector_sink 为 None 而跳过向量删除分支，删除应成功。
        assert resp.status_code == 200
