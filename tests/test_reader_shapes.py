"""内容形态分流测试(迭代 2 · A):源级 content_shape 标记、目录透出与 shape= 过滤。

形态是**源级标记**(fetcher.content_shape),content_type 只作注册表之外历史
归档源的兜底(github_release/github_repository/hf_model/huggingface_model
必然是动态形)。快照断言锁死每个内置节点的形态——新增节点必须显式归类,
改动既有节点形态也会在此显形。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.registry import fetcher_registry  # noqa: E402


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))

# 内置节点形态快照:动态形全集(其余一律文章形)。
EXPECTED_BULLETIN_SOURCE_IDS = {
    # changelog / 发布说明(content_type 是 web_article,必须靠源级标记)
    "docs_openai_codex_changelog",
    "docs_claude_code_changelog",
    "docs_gemma_release_notes",
    "docs_xai_release_notes",
    "docs_deepseek_api_changelog",
    "docs_zai_new_released",
    "web_cursor_changelog",
    # GitHub Releases(基类标记,预设继承)
    "generic_github_releases",
    "github_opencode_releases",
    "github_openclaw_releases",
    "github_hermes_agent_releases",
    # 仓库 / 模型监控(基类标记,预设继承)
    "generic_github_repositories",
    "github_deepseek_repositories",
    "generic_huggingface_models",
    "hf_deepseek_models",
    # 榜单类(短条目发现流)
    "github_trending_daily",
}


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    import datetime
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
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
                created_at=now,
                updated_at=now,
            ))
        session.commit()


def _seed_article(engine, article_id: str, source_id: str, content_type: str = "rss_article"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(
            ArticleRecord(
                id=article_id,
                title=f"Title {article_id}",
                content_type=content_type,
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date="2026-05-20T00:00:00",
                fetched_date="2026-05-21T00:00:00",
                has_content=True,
                content=f"{article_id} body",
                extensions_json="{}",
                is_vectorized=False,
            )
        )
        session.commit()


def _make_app(monkeypatch, tmp_path, name: str):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    return app_module, sink


# ==================== 源级标记快照 ====================

def test_builtin_fetcher_shape_snapshot():
    """每个内置节点的 content_shape 与快照一致——形态归类是显式决策,不许漂移。"""
    actual_bulletin = set()
    for meta in fetcher_registry.get_all_metadata():
        shape = meta.get("shape")
        assert shape in ("article", "bulletin"), f"{meta['id']} 非法形态: {shape!r}"
        if shape == "bulletin":
            actual_bulletin.add(meta["id"])
    assert actual_bulletin == EXPECTED_BULLETIN_SOURCE_IDS


def test_source_shape_fallback_by_content_type():
    """注册表之外的历史源按 content_type 兜底;注册源以源级标记优先。"""
    from api.sources import _registry_source_meta, source_shape

    meta = _registry_source_meta()
    # 注册源:HN AI / HF Daily Papers 虽是发现型/榜单型,拍板保持文章形
    assert source_shape("rss_hn_ai", "rss_article", meta) == "article"
    assert source_shape("web_huggingface_daily_papers", "web_article", meta) == "article"
    assert source_shape("docs_claude_code_changelog", "web_article", meta) == "bulletin"
    # 未注册历史源:content_type 兜底
    assert source_shape("legacy_gone", "github_release", meta) == "bulletin"
    assert source_shape("legacy_gone", "huggingface_model", meta) == "bulletin"
    assert source_shape("wechat_jiqizhixin", "wechat_article", meta) == "article"


# ==================== 目录透出 ====================

def test_reader_sources_payload_carries_shape(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "catalog.db")
    # 未注册的历史归档源(靠 content_type 兜底)
    _seed_article(sink.engine, "r1", "legacy_releases", content_type="github_release")
    _seed_article(sink.engine, "w1", "wechat_jiqizhixin", content_type="wechat_article")

    with TestClient(app_module.app) as client:
        _login(client)
        data = client.get("/api/reader/sources").json()
        shapes = {s["source_id"]: s["shape"] for s in data["sources"]}
        assert shapes["github_opencode_releases"] == "bulletin"
        assert shapes["docs_claude_code_changelog"] == "bulletin"
        assert shapes["web_anthropic_news"] == "article"
        assert shapes["dorami_daily_brief"] == "article"  # 日报是要读的
        assert shapes["legacy_releases"] == "bulletin"    # content_type 兜底
        assert shapes["wechat_jiqizhixin"] == "article"


# ==================== shape= 过滤 ====================

def test_articles_shape_filter(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "filter.db")
    # 三类代表:注册表动态源(content_type 却是 web_article)/兜底动态/普通文章
    _seed_article(sink.engine, "c1", "docs_claude_code_changelog", content_type="web_article")
    _seed_article(sink.engine, "r1", "legacy_releases", content_type="github_release")
    _seed_article(sink.engine, "a1", "web_anthropic_news", content_type="web_article")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

        def ids(shape=None):
            params = {"include_total": "true", "include_content": "false"}
            if shape:
                params["shape"] = shape
            return {item["id"] for item in client.get("/api/articles", params=params).json()["items"]}

        assert ids() == {"c1", "r1", "a1"}              # 不传 shape:不过滤
        assert ids("bulletin") == {"c1", "r1"}          # 源级标记 ∪ content_type 兜底
        assert ids("article") == {"a1"}                  # 取反
        assert ids("bogus") == {"c1", "r1", "a1"}       # 非法值忽略


def test_articles_shape_composes_with_subscribed_scope(monkeypatch, tmp_path):
    """阅读器聚合流的真实组合:subscribed_scope=only + shape 同时生效。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "compose.db")
    _seed_article(sink.engine, "c1", "docs_claude_code_changelog", content_type="web_article")
    _seed_article(sink.engine, "a1", "web_anthropic_news", content_type="web_article")
    _seed_article(sink.engine, "x1", "web_qbitai", content_type="web_article")  # 未订阅

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/docs_claude_code_changelog/subscribe")
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        params = {"subscribed_scope": "only", "include_total": "true", "include_content": "false"}
        article_ids = {
            item["id"]
            for item in client.get("/api/articles", params={**params, "shape": "article"}).json()["items"]
        }
        bulletin_ids = {
            item["id"]
            for item in client.get("/api/articles", params={**params, "shape": "bulletin"}).json()["items"]
        }
        assert article_ids == {"a1"}
        assert bulletin_ids == {"c1"}
