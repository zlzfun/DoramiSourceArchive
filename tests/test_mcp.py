import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sqlmodel import create_engine, SQLModel, Session

def make_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


# ── Task 2 ────────────────────────────────────────────────────────────────────

def test_app_setting_crud():
    from models.db import AppSettingRecord
    engine = make_engine()
    with Session(engine) as s:
        s.add(AppSettingRecord(key="mcp_enabled", value="true"))
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec is not None and rec.value == "true"
        rec.value = "false"
        s.add(rec)
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec.value == "false"


# ── Task 3 helpers ────────────────────────────────────────────────────────────

from unittest.mock import MagicMock, AsyncMock
from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage


def make_db_sink():
    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    return sink


def make_vector_sink():
    sink = MagicMock(spec=ChromaVectorStorage)
    sink.search = AsyncMock()
    return sink


def seed_article(db_sink, title="Test Article", source_id="test_src",
                 content_type="arxiv", content="hello world body"):
    from models.db import ArticleRecord
    import datetime
    rec = ArticleRecord(
        id=f"test_{title.replace(' ', '_')}",
        title=title,
        source_id=source_id,
        content_type=content_type,
        source_url="http://example.com",
        publish_date=str(datetime.date.today()),
        fetched_date=str(datetime.date.today()),
        has_content=bool(content),
        content=content,
        extensions_json='{"key": "val"}',
        is_vectorized=False,
    )
    record_id = rec.id
    with Session(db_sink.engine) as s:
        s.add(rec)
        s.commit()
    return record_id


# ── Task 3 tests ──────────────────────────────────────────────────────────────

def test_list_sources_returns_list():
    from mcp_server import _list_sources_impl
    db = make_db_sink()
    result = _list_sources_impl(db)
    assert isinstance(result, list)
    for item in result:
        assert "source_id" in item
        assert "name" in item


def test_browse_articles_empty():
    from mcp_server import _browse_articles_impl
    db = make_db_sink()
    result = _browse_articles_impl(db)
    assert result == []


def test_browse_articles_filter_by_source():
    from mcp_server import _browse_articles_impl
    db = make_db_sink()
    seed_article(db, title="Match", source_id="src_a")
    seed_article(db, title="No Match", source_id="src_b")
    result = _browse_articles_impl(db, source_id="src_a")
    assert len(result) == 1
    assert result[0]["title"] == "Match"
    assert result[0]["source_url"] == "http://example.com"


def test_get_article_found():
    from mcp_server import _get_article_impl
    db = make_db_sink()
    article_id = seed_article(db, title="Full Article", content="long body text")
    result = _get_article_impl(db, article_id)
    assert isinstance(result, dict)
    assert result["title"] == "Full Article"
    assert result["content"] == "long body text"
    assert result["extensions"] == {"key": "val"}


def test_get_article_respects_subscription_scope():
    from mcp_server import _get_article_impl
    db = make_db_sink()
    article_id = seed_article(db, title="Scoped Article", source_id="src_a")
    allowed = _get_article_impl(db, article_id, source_ids=["src_a"])
    denied = _get_article_impl(db, article_id, source_ids=["src_b"])
    assert allowed["title"] == "Scoped Article"
    assert denied == {"error": "article is outside the subscription scope"}


def test_get_article_not_found():
    from mcp_server import _get_article_impl
    db = make_db_sink()
    result = _get_article_impl(db, "nonexistent_id")
    assert isinstance(result, dict)
    assert "error" in result


def test_resolve_scope_requires_token_for_content_tools():
    from mcp_server import _resolve_scope
    resolver = lambda token: ["src_a"] if token == "good" else None
    # 无令牌：内容类工具一律拒绝（/mcp 无登录会话，令牌是唯一鉴权）。
    assert _resolve_scope(resolver, None) == (False, None)
    assert _resolve_scope(resolver, "") == (False, None)
    # 无效令牌（resolver 返回 None）同样拒绝。
    assert _resolve_scope(resolver, "bad") == (False, None)
    # 有效令牌：放行并限定到其覆盖来源。
    assert _resolve_scope(resolver, "good") == (True, ["src_a"])


def test_resolve_scope_allows_tokenless_when_not_required():
    from mcp_server import _resolve_scope
    resolver = lambda token: ["src_a"]
    # list_sources 等目录类工具：无令牌放行、不限定作用域。
    assert _resolve_scope(resolver, None, require_token=False) == (True, None)


def test_parse_bearer_extracts_token():
    from mcp_server import _parse_bearer
    # 标准 Bearer（大小写不敏感的 scheme），取出令牌本体。
    assert _parse_bearer("Bearer dfeed_abc") == "dfeed_abc"
    assert _parse_bearer("bearer dsub_xyz") == "dsub_xyz"
    # 非 Bearer / 空 / 仅 scheme 无令牌 → None。
    assert _parse_bearer(None) is None
    assert _parse_bearer("") is None
    assert _parse_bearer("Token abc") is None
    assert _parse_bearer("Bearer   ") is None


# ── Task 4 ────────────────────────────────────────────────────────────────────
import asyncio


def run(coro):
    return asyncio.run(coro)


def test_search_articles_empty_index():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    vec.search.return_value = []
    result = run(_search_articles_impl(vec, query="embodied intelligence"))
    assert result == []
    vec.search.assert_called_once()


def test_search_articles_deduplicates_chunks():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    vec.search.return_value = [
        {"id": "art1_chunk_0", "document": "chunk A", "distance": 0.3,
         "metadata": {"parent_id": "art1", "title": "Robot Survey",
                      "source_id": "arxiv_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
        {"id": "art1_chunk_1", "document": "chunk B", "distance": 0.8,
         "metadata": {"parent_id": "art1", "title": "Robot Survey",
                      "source_id": "arxiv_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
    ]
    result = run(_search_articles_impl(vec, query="robots"))
    assert len(result) == 1
    assert result[0]["id"] == "art1"
    assert result[0]["distance"] == 0.3


def test_search_articles_filters_by_threshold():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    vec.search.return_value = [
        {"id": "art2_chunk_0", "document": "irrelevant", "distance": 1.8,
         "metadata": {"parent_id": "art2", "title": "Off-topic",
                      "source_id": "other", "content_type": "misc",
                      "publish_date": "2025-01-01"}},
    ]
    result = run(_search_articles_impl(vec, query="something", distance_threshold=1.5))
    assert result == []


def test_get_rag_context_empty():
    from mcp_server import _get_rag_context_impl
    db = make_db_sink()
    vec = make_vector_sink()
    vec.search.return_value = []
    result = run(_get_rag_context_impl(db, vec, query="test"))
    assert result == ""


def test_get_rag_context_formats_block():
    from mcp_server import _get_rag_context_impl
    db = make_db_sink()
    article_id = seed_article(db, title="Embodied AI Survey", content="Survey content here.")
    vec = make_vector_sink()
    vec.search.return_value = [
        {"id": f"{article_id}_chunk_0",
         "document": "Header line\n\nSurvey content here.",
         "distance": 0.4,
         "metadata": {"parent_id": article_id, "title": "Embodied AI Survey",
                      "source_id": "test_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
    ]
    result = run(_get_rag_context_impl(db, vec, query="embodied AI"))
    assert "Embodied AI Survey" in result
    assert "2025-03-01" in result
    assert isinstance(result, str)


def test_get_rag_context_passes_subscription_scope_to_vector_search():
    from mcp_server import _get_rag_context_impl
    db = make_db_sink()
    vec = make_vector_sink()
    vec.search.return_value = []
    result = run(_get_rag_context_impl(db, vec, query="embodied AI", source_ids=["src_a"]))
    assert result == ""
    kwargs = vec.search.call_args.kwargs
    assert kwargs["source_ids"] == ["src_a"]


# ── Task 5 ────────────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient


def get_client():
    import api.app as app_module
    with TestClient(app_module.app) as client:
        return client


def login_test_admin(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "admin"


def login_test_user(client):
    resp = client.post("/api/auth/login", json={"username": "user", "password": "user"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "user"


def set_test_auth_accounts(monkeypatch, app_module):
    monkeypatch.setattr(
        app_module,
        "AUTH_ACCOUNTS",
        {
            "admin": {"password": "admin", "role": "admin"},
            "user": {"password": "user", "role": "user"},
        },
    )


def test_admin_auth_session_lifecycle(monkeypatch):
    app_module = __import__('api.app', fromlist=['app'])
    set_test_auth_accounts(monkeypatch, app_module)
    with TestClient(app_module.app) as client:
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert client.get("/api/mcp/status").status_code == 401
        login_test_admin(client)
        assert client.get("/api/auth/session").json()["authenticated"] is True
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/session").json()["authenticated"] is False


def test_mcp_transport_does_not_require_admin_cookie():
    with TestClient(__import__('api.app', fromlist=['app']).app) as client:
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp.status_code != 401


def test_mcp_status_returns_correct_structure(monkeypatch):
    app_module = __import__('api.app', fromlist=['app'])
    set_test_auth_accounts(monkeypatch, app_module)
    with TestClient(app_module.app) as client:
        login_test_user(client)
        resp = client.get("/api/mcp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "url" in data
        assert data["url"].endswith("/mcp")
        assert "tools" in data
        assert len(data["tools"]) == 5
        tool_names = {t["name"] for t in data["tools"]}
        assert tool_names == {"list_sources", "browse_articles", "get_article",
                              "search_articles", "get_rag_context"}


def test_daily_brief_skill_download_embeds_live_prompt(monkeypatch):
    import io
    import zipfile
    app_module = __import__('api.app', fromlist=['app'])
    set_test_auth_accounts(monkeypatch, app_module)
    with TestClient(app_module.app) as client:
        login_test_user(client)
        resp = client.get("/api/skill/daily-brief")
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            skill_text = zf.read("dorami-daily-brief/SKILL.md").decode("utf-8")
        assert "Shared daily brief generation style" in skill_text
        assert "/api/public/feed/articles" in skill_text
        assert "{DAILY_BRIEF_STYLE_GUIDE}" not in skill_text


def test_mcp_toggle_flips_state(monkeypatch):
    app_module = __import__('api.app', fromlist=['app'])
    set_test_auth_accounts(monkeypatch, app_module)
    with TestClient(app_module.app) as client:
        login_test_user(client)
        initial = client.get("/api/mcp/status").json()["enabled"]
        resp = client.post("/api/mcp/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] != initial
        # Restore
        client.post("/api/mcp/toggle")
        assert client.get("/api/mcp/status").json()["enabled"] == initial
