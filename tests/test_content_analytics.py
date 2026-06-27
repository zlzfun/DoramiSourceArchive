"""内容看板（content_analytics）回归测试。

覆盖：summarize 的每源文章/向量化/最新抓取/主类型聚合、收藏的按源 + 文章级
top 榜聚合、totals；以及 /api/admin/content 端点的源名/订阅数富化与 admin 鉴权。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _auth_config(admin="admin:admin", user="user:user"):
    from config import _auth_credentials

    return replace(
        __import__("api.app", fromlist=["settings"]).settings.auth,
        admin_users=_auth_credentials(admin) if admin else [],
        user_users=_auth_credentials(user) if user else [],
    )


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_content.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    accounts_service.seed_users_if_empty(sink.engine, _auth_config())
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _seed_content(engine):
    """两个源、四篇文章、若干收藏 + 订阅。"""
    from models.db import ArticleRecord, ReaderFavoriteRecord, ReaderSubscriptionRecord
    import json

    def art(aid, source, ctype, vec, fetched, title):
        return ArticleRecord(
            id=aid, title=title, content_type=ctype, source_id=source,
            source_url=f"http://x/{aid}", publish_date="2026-06-01", fetched_date=fetched,
            has_content=True, content="body", is_vectorized=vec,
        )

    with Session(engine) as session:
        session.add(art("a1", "src_alpha", "arxiv", True, "2026-06-10", "Alpha One"))
        session.add(art("a2", "src_alpha", "arxiv", True, "2026-06-20", "Alpha Two"))
        session.add(art("a3", "src_alpha", "wechat_article", False, "2026-06-15", "Alpha Three"))
        session.add(art("a4", "src_beta", "github_repository", False, "2026-06-05", "Beta One"))
        # 收藏：a1 被 2 人收藏、a4 被 1 人收藏。
        session.add(ReaderFavoriteRecord(owner_username="u1", article_id="a1", created_at="2026-06-21"))
        session.add(ReaderFavoriteRecord(owner_username="u2", article_id="a1", created_at="2026-06-22"))
        session.add(ReaderFavoriteRecord(owner_username="u1", article_id="a4", created_at="2026-06-23"))
        # 订阅：单源订阅每源一行（与一键订阅的真实落库格式一致，filters.source_ids 为字符串）。
        # u1 订阅 src_alpha；u2 订阅 src_alpha + src_beta。
        def sub(owner, source, h):
            return ReaderSubscriptionRecord(
                owner_username=owner, name=source, filters_json=json.dumps({"source_ids": source}),
                token_hash=h, is_active=True, created_at="2026-06-01", updated_at="2026-06-01")
        session.add(sub("u1", "src_alpha", "h1"))
        session.add(sub("u2", "src_alpha", "h2"))
        session.add(sub("u2", "src_beta", "h3"))
        session.commit()


def test_summarize_aggregates(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import content_analytics

    _seed_content(app_module.db_sink.engine)
    with Session(app_module.db_sink.engine) as session:
        agg = content_analytics.summarize(session, top_n=10)

    alpha = agg["by_source"]["src_alpha"]
    assert alpha["article_count"] == 3
    assert alpha["vectorized_count"] == 2
    assert alpha["last_fetched"] == "2026-06-20"
    assert alpha["primary_content_type"] == "arxiv"  # 计数最高者（2 > 1）
    assert alpha["content_types"] == {"arxiv": 2, "wechat_article": 1}

    assert agg["favorites_by_source"]["src_alpha"] == 2
    assert agg["favorites_by_source"]["src_beta"] == 1

    # 文章级收藏榜：a1(2) 居首。
    assert agg["top_articles"][0]["article_id"] == "a1"
    assert agg["top_articles"][0]["favorite_count"] == 2

    assert agg["totals"] == {"articles": 4, "vectorized": 2, "favorites": 3}


def test_admin_content_endpoint(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    _seed_content(app_module.db_sink.engine)

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.get("/api/admin/content")
        assert res.status_code == 200
        body = res.json()

        assert body["totals"]["articles"] == 4
        assert body["totals"]["favorites"] == 3
        assert body["totals"]["sources"] >= 2

        by_id = {s["source_id"]: s for s in body["sources"]}
        # src_alpha：2 人订阅、2 收藏、3 文章、向量化率 2/3。
        assert by_id["src_alpha"]["subscription_count"] == 2
        assert by_id["src_alpha"]["favorite_count"] == 2
        assert by_id["src_alpha"]["article_count"] == 3
        assert round(by_id["src_alpha"]["vectorized_rate"], 2) == 0.67
        # src_beta：1 人订阅、1 收藏。
        assert by_id["src_beta"]["subscription_count"] == 1
        assert by_id["src_beta"]["favorite_count"] == 1

        # 收藏榜带富化的源名。
        assert body["top_articles"][0]["article_id"] == "a1"
        assert body["top_articles"][0]["source_name"]

    # 受限读者无权访问。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/content").status_code == 403
