"""内容形态分流测试(迭代 3):article | bulletin | social 三容器。

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

# 内置节点形态快照:bulletin / social 全集(其余一律 article)。
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

EXPECTED_SOCIAL_SOURCE_IDS = {
    # X 社交卡片流（通用模板 + 首批 8 个 preset）
    "generic_x_timeline",
    "x_ai_at_meta",
    "x_deepseek_ai",
    "x_alibaba_qwen",
    "x_moonshot_ai",
    "x_openrouter",
    "x_karpathy",
    "x_sama",
    "x_openai",
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
    actual_social = set()
    for meta in fetcher_registry.get_all_metadata():
        shape = meta.get("shape")
        assert shape in ("article", "bulletin", "social"), f"{meta['id']} 非法形态: {shape!r}"
        if shape == "bulletin":
            actual_bulletin.add(meta["id"])
        elif shape == "social":
            actual_social.add(meta["id"])
    assert actual_bulletin == EXPECTED_BULLETIN_SOURCE_IDS
    assert actual_social == EXPECTED_SOCIAL_SOURCE_IDS


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
    assert source_shape("legacy_social", "social_post", meta) == "social"
    assert source_shape("x_openai", "social_post", meta) == "social"
    assert source_shape("wechat_jiqizhixin", "wechat_article", meta) == "article"


# ==================== 目录透出 ====================

def test_reader_sources_payload_carries_shape(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord
    from services.x_api_config import write_user_cache

    app_module, sink = _make_app(monkeypatch, tmp_path, "catalog.db")
    # 未注册的历史归档源(靠 content_type 兜底)
    _seed_article(sink.engine, "r1", "legacy_releases", content_type="github_release")
    _seed_article(sink.engine, "w1", "wechat_jiqizhixin", content_type="wechat_article")
    _seed_article(sink.engine, "s1", "legacy_social", content_type="social_post")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="x_configured_zero",
            name="Configured X",
            source_type="x",
            params_json='{"handle":"configured_ai"}',
            created_at="2026-07-20T00:00:00",
            updated_at="2026-07-20T00:00:00",
        ))
        session.commit()
        write_user_cache(
            session,
            "x_configured_zero",
            handle="configured_ai",
            user_id="123456",
            user={
                "id": "123456",
                "name": "Configured AI",
                "username": "configured_ai",
                "profile_image_url": "https://pbs.twimg.com/profile_images/123/avatar_normal.jpg",
            },
        )

    with TestClient(app_module.app) as client:
        _login(client)
        data = client.get("/api/reader/sources").json()
        shapes = {s["source_id"]: s["shape"] for s in data["sources"]}
        platforms = {s["source_id"]: s["platform"] for s in data["sources"]}
        sources = {s["source_id"]: s for s in data["sources"]}
        assert shapes["github_opencode_releases"] == "bulletin"
        assert shapes["docs_claude_code_changelog"] == "bulletin"
        assert shapes["web_anthropic_news"] == "article"
        assert shapes["dorami_daily_brief"] == "article"  # 日报是要读的
        assert shapes["legacy_releases"] == "bulletin"    # content_type 兜底
        assert shapes["x_openai"] == "social"             # 注册源标记
        assert shapes["legacy_social"] == "social"        # social_post 兜底
        assert shapes["x_configured_zero"] == "social"    # 零产出 SourceConfig
        assert shapes["wechat_jiqizhixin"] == "article"
        assert platforms["x_openai"] == "x"              # fetcher 类属性
        assert platforms["x_configured_zero"] == "x"     # SourceConfig source_type 兜底
        assert platforms["web_anthropic_news"] == ""      # 非社交源留空
        assert sources["x_configured_zero"]["avatar_url"] == (
            "https://pbs.twimg.com/profile_images/123/avatar_400x400.jpg"
        )
        assert sources["x_configured_zero"]["avatar_url_original"] == (
            "https://pbs.twimg.com/profile_images/123/avatar_normal.jpg"
        )
        assert sources["web_anthropic_news"]["avatar_url"] == ""


# ==================== shape= 过滤 ====================

def test_articles_shape_filter(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord

    app_module, sink = _make_app(monkeypatch, tmp_path, "filter.db")
    # 五类代表:注册/兜底 bulletin，兜底/配置 social，普通 article。
    _seed_article(sink.engine, "c1", "docs_claude_code_changelog", content_type="web_article")
    _seed_article(sink.engine, "r1", "legacy_releases", content_type="github_release")
    _seed_article(sink.engine, "a1", "web_anthropic_news", content_type="web_article")
    _seed_article(sink.engine, "s1", "legacy_social", content_type="social_post")
    # 即使存量 content_type 异常，配置源的 source_type=x 仍是第一事实源。
    _seed_article(sink.engine, "s2", "x_configured_filter", content_type="rss_article")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="x_configured_filter",
            name="Configured Filter X",
            source_type="x_timeline",
            params_json='{"handle":"configured_filter"}',
            created_at="2026-07-20T00:00:00",
            updated_at="2026-07-20T00:00:00",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

        def ids(shape=None):
            params = {"include_total": "true", "include_content": "false"}
            if shape:
                params["shape"] = shape
            return {item["id"] for item in client.get("/api/articles", params=params).json()["items"]}

        assert ids() == {"c1", "r1", "a1", "s1", "s2"}  # 不传 shape:不过滤
        assert ids("bulletin") == {"c1", "r1"}          # 源级标记 ∪ content_type 兜底
        assert ids("social") == {"s1", "s2"}             # social_post 兜底 ∪ 配置源标记
        assert ids("article") == {"a1"}                  # 不误收 bulletin / social
        assert ids("bogus") == {"c1", "r1", "a1", "s1", "s2"} # 非法值忽略


def test_articles_shape_composes_with_subscribed_scope(monkeypatch, tmp_path):
    """阅读器聚合流的真实组合:subscribed_scope=only + shape 同时生效。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "compose.db")
    _seed_article(sink.engine, "c1", "docs_claude_code_changelog", content_type="web_article")
    _seed_article(sink.engine, "a1", "web_anthropic_news", content_type="web_article")
    _seed_article(sink.engine, "s1", "x_openai", content_type="social_post")
    _seed_article(sink.engine, "x1", "web_qbitai", content_type="web_article")  # 未订阅

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/docs_claude_code_changelog/subscribe")
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        client.post("/api/reader/sources/x_openai/subscribe")
        params = {"subscribed_scope": "only", "include_total": "true", "include_content": "false"}
        article_ids = {
            item["id"]
            for item in client.get("/api/articles", params={**params, "shape": "article"}).json()["items"]
        }
        bulletin_ids = {
            item["id"]
            for item in client.get("/api/articles", params={**params, "shape": "bulletin"}).json()["items"]
        }
        social_ids = {
            item["id"]
            for item in client.get("/api/articles", params={**params, "shape": "social"}).json()["items"]
        }
        assert article_ids == {"a1"}
        assert bulletin_ids == {"c1"}
        assert social_ids == {"s1"}
