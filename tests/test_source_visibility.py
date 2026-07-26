"""读者面源可见性（管理面隐藏节点）回归测试。

覆盖：服务层往返与坏数据韧性；/api/admin/source-visibility 门控（401/403）与读写；
隐藏后读者目录/一键订阅/文章列表/单条详情/聚合 feed 的排除口径；admin 会话不受
影响（知识台账仍见全档）；恢复可见即回归；管理操作审计落行。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

HIDDEN_SOURCE = "rss_hidden_src"
VISIBLE_SOURCE = "rss_visible_src"
HIDDEN_ARTICLE = "art-hidden-1"
VISIBLE_ARTICLE = "art-visible-1"


def _mark_defaults_seeded(engine, usernames=("user", "admin")):
    """预标记「默认订阅已播种」，避免登录点播种的精选订阅混入断言。"""
    import datetime
    from services import daily_brief as daily_brief_service

    with Session(engine) as session:
        for username in usernames:
            daily_brief_service.set_setting(
                session, f"reader_defaults_seeded:{username}",
                datetime.datetime.now().isoformat(),
            )


def _seed_article(engine, article_id: str, source_id: str):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=f"title of {article_id}",
            content_type="rss_article",
            source_id=source_id,
            source_url=f"https://example.test/{article_id}",
            publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00",
            has_content=True,
            content=f"{article_id} body",
            extensions_json="{}",
            is_vectorized=False,
        ))
        session.commit()


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_source_visibility.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    _mark_defaults_seeded(sink.engine)
    _seed_article(sink.engine, HIDDEN_ARTICLE, HIDDEN_SOURCE)
    _seed_article(sink.engine, VISIBLE_ARTICLE, VISIBLE_SOURCE)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _article_ids(payload):
    items = payload if isinstance(payload, list) else payload.get("items", [])
    return {item["id"] for item in items}


# ==================== 服务层 ====================

def test_service_roundtrip_and_bad_data(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import AppSettingRecord
    from services import source_visibility

    with Session(app_module.db_sink.engine) as session:
        assert source_visibility.hidden_source_ids(session) == set()
        assert source_visibility.set_source_hidden(session, HIDDEN_SOURCE, True) == [HIDDEN_SOURCE]
        # 幂等重复隐藏、再隐藏第二个源。
        assert source_visibility.set_source_hidden(session, HIDDEN_SOURCE, True) == [HIDDEN_SOURCE]
        assert source_visibility.set_source_hidden(session, "another", True) == [
            "another", HIDDEN_SOURCE,
        ]
        assert source_visibility.hidden_source_ids(session) == {HIDDEN_SOURCE, "another"}
        # 恢复可见（含对不存在项的幂等恢复）。
        assert source_visibility.set_source_hidden(session, "another", False) == [HIDDEN_SOURCE]
        assert source_visibility.set_source_hidden(session, "ghost", False) == [HIDDEN_SOURCE]
        # 坏数据韧性：非 JSON / 非数组一律按空集处理。
        record = session.get(AppSettingRecord, source_visibility.HIDDEN_SOURCES_KEY)
        record.value = "not-json"
        session.add(record)
        session.commit()
        assert source_visibility.hidden_source_ids(session) == set()


# ==================== 管理端点门控 ====================

def test_admin_endpoints_gating(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/admin/source-visibility").status_code == 401
        _login(client, "user", "user")
        assert client.get("/api/admin/source-visibility").status_code == 403
        assert client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True}
        ).status_code == 403
        _login(client, "admin", "admin")
        assert client.get("/api/admin/source-visibility").json() == {"hidden_source_ids": []}
        res = client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True}
        )
        assert res.status_code == 200
        assert res.json()["hidden_source_ids"] == [HIDDEN_SOURCE]
        assert client.get("/api/admin/source-visibility").json() == {
            "hidden_source_ids": [HIDDEN_SOURCE]
        }


# ==================== 读者面排除口径 ====================

def test_hidden_source_excluded_from_reader_plane(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        # 先以读者身份订阅两个源（隐藏前均可订），并收藏隐藏源的文章。
        _login(client, "user", "user")
        assert client.post(f"/api/reader/sources/{HIDDEN_SOURCE}/subscribe").status_code == 200
        assert client.post(f"/api/reader/sources/{VISIBLE_SOURCE}/subscribe").status_code == 200
        assert client.post(f"/api/reader/favorites/{HIDDEN_ARTICLE}").status_code == 200

        # 管理员隐藏其一。
        _login(client, "admin", "admin")
        assert client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True}
        ).status_code == 200

        # 读者目录：已订阅的隐藏源保留为「暂不可用」条目（hidden=True，subscribed 保持），
        # 订阅并集（目录口径）也保留它——源栏据此渲染退订/等待入口。
        _login(client, "user", "user")
        catalog = client.get("/api/reader/sources").json()
        by_id = {s["source_id"]: s for s in catalog["sources"]}
        assert by_id[HIDDEN_SOURCE]["hidden"] is True
        assert by_id[HIDDEN_SOURCE]["subscribed"] is True
        assert by_id[VISIBLE_SOURCE]["hidden"] is False
        assert HIDDEN_SOURCE in catalog["subscribed_source_ids"]
        assert VISIBLE_SOURCE in catalog["subscribed_source_ids"]

        # 未订阅者完全不可见：admin 未订阅该源，其目录不出现（发现页同源）。
        _login(client, "admin", "admin")
        admin_catalog_ids = {s["source_id"] for s in client.get("/api/reader/sources").json()["sources"]}
        assert HIDDEN_SOURCE not in admin_catalog_ids
        _login(client, "user", "user")

        # 新订阅被拒（目录里也点不到，防直连 API 绕过）。
        assert client.post(f"/api/reader/sources/{HIDDEN_SOURCE}/subscribe").status_code == 404

        # 文章列表：订阅范围与跨源全量列表都排除隐藏源。
        only = client.get("/api/articles?subscribed_scope=only").json()
        assert _article_ids(only) == {VISIBLE_ARTICLE}
        all_for_user = client.get("/api/articles").json()
        assert HIDDEN_ARTICLE not in _article_ids(all_for_user)
        assert VISIBLE_ARTICLE in _article_ids(all_for_user)

        # 单条详情：读者按不存在处理。
        assert client.get(f"/api/articles/{HIDDEN_ARTICLE}").status_code == 404
        assert client.get(f"/api/articles/{VISIBLE_ARTICLE}").status_code == 200

        # 收藏列表同口径排除（收藏行保留，恢复可见后回归）。
        favorites = client.get("/api/reader/favorites").json()
        assert _article_ids(favorites) == set()

        # admin 会话不受影响：知识台账仍见全档。
        _login(client, "admin", "admin")
        all_for_admin = client.get("/api/articles").json()
        assert {HIDDEN_ARTICLE, VISIBLE_ARTICLE} <= _article_ids(all_for_admin)
        assert client.get(f"/api/articles/{HIDDEN_ARTICLE}").status_code == 200

        # 恢复可见：订阅关系保留，读者面原样回归（hidden 标志清除）。
        _login(client, "admin", "admin")
        assert client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": False}
        ).status_code == 200
        _login(client, "user", "user")
        catalog = client.get("/api/reader/sources").json()
        by_id = {s["source_id"]: s for s in catalog["sources"]}
        assert by_id[HIDDEN_SOURCE]["hidden"] is False
        assert HIDDEN_SOURCE in catalog["subscribed_source_ids"]
        assert _article_ids(client.get("/api/articles?subscribed_scope=only").json()) == {
            HIDDEN_ARTICLE, VISIBLE_ARTICLE,
        }
        assert _article_ids(client.get("/api/reader/favorites").json()) == {HIDDEN_ARTICLE}


def test_unsubscribe_hidden_source(monkeypatch, tmp_path):
    """隐藏期间读者仍可退订（「暂不可用」条目的主动出口）；退订后条目不再保留。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post(f"/api/reader/sources/{HIDDEN_SOURCE}/subscribe").status_code == 200
        _login(client, "admin", "admin")
        assert client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True}
        ).status_code == 200
        _login(client, "user", "user")
        res = client.delete(f"/api/reader/sources/{HIDDEN_SOURCE}/subscribe")
        assert res.status_code == 200
        assert res.json()["subscribed"] is False
        assert HIDDEN_SOURCE not in res.json()["subscribed_source_ids"]
        catalog = client.get("/api/reader/sources").json()
        assert HIDDEN_SOURCE not in {s["source_id"] for s in catalog["sources"]}


# ==================== 聚合 feed 交付 ====================

def test_feed_delivery_excludes_hidden(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post(f"/api/reader/sources/{HIDDEN_SOURCE}/subscribe").status_code == 200
        assert client.post(f"/api/reader/sources/{VISIBLE_SOURCE}/subscribe").status_code == 200
        token = client.post("/api/reader/feed-token/rotate").json()["token"]

        _login(client, "admin", "admin")
        assert client.post(
            f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True}
        ).status_code == 200

        # 令牌拉取（无会话）：隐藏源不再交付。
        client.cookies.clear()
        feed = client.get(
            "/api/public/feed/articles", headers={"Authorization": f"Bearer {token}"}
        ).json()
        ids = {item["id"] for item in feed["items"]}
        assert HIDDEN_ARTICLE not in ids
        assert VISIBLE_ARTICLE in ids


# ==================== 操作审计 ====================

def test_visibility_toggle_is_audited(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        client.post(f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": True})
        client.post(f"/api/admin/source-visibility/{HIDDEN_SOURCE}", json={"hidden": False})
        items = client.get("/api/admin/audit-log").json()["items"]
        summaries = [item["summary"] for item in items]
        assert any("在读者面隐藏源" in s and HIDDEN_SOURCE in s for s in summaries)
        assert any("恢复源读者面可见" in s and HIDDEN_SOURCE in s for s in summaries)
