"""用户自定 RSS 源(v3.40)回归测试。

覆盖:URL 规范化与身份、preview 守门(非 feed 拒绝/SSRF)、添加端到端(建行+订阅+
最简正文参数)、去重共享、配额(源数/日增)、系统源撞库(可见转引导/隐藏 404)、
隔离面(目录可见性/all 检索域/归档导出/日报候选/文章直查轻门槛)、删除级联
(独占删/共享仅退订/admin 强删)、自动停用、总闸熔断与 runtime 能力位、admin 门控。

feed 网络一律 httpx.MockTransport 或 monkeypatch service 函数,不打真网。
"""

import asyncio
import datetime
import os
import sys
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"), ("bob", "bob", "user"))

_FEED_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Test Blog</title>
<item><title>Post One</title><description>hello world content</description>
<pubDate>Mon, 24 Aug 2026 08:00:00 GMT</pubDate><link>https://blog.example.com/p1</link></item>
<item><title>Post Two</title><description>second entry body text</description>
<pubDate>Tue, 25 Aug 2026 08:00:00 GMT</pubDate><link>https://blog.example.com/p2</link></item>
</channel></rss>"""


# ==================== 单元:规范化 / 身份 / 撞库 ====================

def test_canonical_feed_url_normalization():
    from services import user_sources

    assert user_sources.canonical_feed_url("HTTPS://Blog.Example.com:443/feed/") == \
        "https://blog.example.com/feed"
    assert user_sources.canonical_feed_url("http://a.com:80/rss.xml#frag") == "http://a.com/rss.xml"
    # query 原样保留(hnrss 语义)
    assert user_sources.canonical_feed_url("https://hnrss.org/newest?q=AI") == \
        "https://hnrss.org/newest?q=AI"
    for bad in ("", "ftp://a.com/feed", "not-a-url"):
        with pytest.raises(ValueError):
            user_sources.canonical_feed_url(bad)


def test_source_id_prefix_and_stability():
    from services import user_sources

    sid = user_sources.source_id_for_url("https://blog.example.com/feed")
    assert sid.startswith(user_sources.USER_SOURCE_PREFIX)
    assert sid == user_sources.source_id_for_url("https://blog.example.com/feed")
    full = user_sources.source_id_for_url("https://blog.example.com/feed", full=True)
    assert full != sid and full.startswith(user_sources.USER_SOURCE_PREFIX)


def test_registry_has_no_user_prefix_sources():
    """`user_rss_` 是用户源独占命名空间:registry 里任何源都不得使用该前缀。"""
    from fetchers.registry import fetcher_registry
    from services.user_sources import USER_SOURCE_PREFIX

    for source_id in fetcher_registry._fetchers:  # noqa: SLF001
        assert not source_id.startswith(USER_SOURCE_PREFIX)


# ==================== 单元:feed 拉取守门 ====================

def _feed_transport(body: bytes = _FEED_XML, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


async def _noop_public_host(host):
    return None


def test_fetch_feed_preview_parses_entries(monkeypatch):
    from services import media_store, user_sources

    monkeypatch.setattr(media_store, "ensure_public_host", _noop_public_host)
    preview = asyncio.run(user_sources.fetch_feed_preview(
        "https://blog.example.com/feed", transport=_feed_transport()
    ))
    assert preview["feed_title"] == "Test Blog"
    assert preview["entry_count"] == 2
    assert preview["entries"][0]["title"] == "Post One"
    assert preview["entries"][0]["content_chars"] > 0


def test_fetch_feed_preview_rejects_non_feed(monkeypatch):
    from services import media_store, user_sources

    monkeypatch.setattr(media_store, "ensure_public_host", _noop_public_host)
    with pytest.raises(ValueError):
        asyncio.run(user_sources.fetch_feed_preview(
            "https://blog.example.com/page", transport=_feed_transport(b"<html>not a feed</html>")
        ))


def test_fetch_feed_preview_ssrf_rejected():
    from services import user_sources
    from services.media_store import SSRFError

    with pytest.raises(SSRFError):
        asyncio.run(user_sources.fetch_feed_preview("http://127.0.0.1/feed"))


# ==================== 端点测试基建 ====================

def _setup_app(monkeypatch, tmp_path, db_name="app_user_sources.db"):
    import api.app as app_module
    from config import RuntimeConfig

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / db_name}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)

    # feed 守门 mock(端点侧不打真网;守门本身的真实测试在上方单元)
    async def _fake_preview(url, **kwargs):
        return {
            "canonical_url": url, "feed_title": "Test Blog", "entry_count": 2,
            "entries": [{"title": "Post One", "publish_date": "", "content_chars": 20}],
        }

    from services import user_sources as user_sources_service

    monkeypatch.setattr(user_sources_service, "fetch_feed_preview", _fake_preview)

    # 首抓 job 的实际抓取置换为空跑(fire-and-forget,不打真网)
    async def _fake_single_fetch(*args, **kwargs):
        return {"status": "success", "results": []}

    monkeypatch.setattr(app_module, "run_single_fetch_as_collection", _fake_single_fetch)
    return app_module


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


def _add(client, url="https://blog.example.com/feed", name=None):
    payload = {"url": url}
    if name is not None:
        payload["name"] = name
    return client.post("/api/reader/custom-sources", json=payload)


# ==================== 添加 / 预览 / 去重共享 ====================

def test_add_custom_source_end_to_end(monkeypatch, tmp_path):
    import json as jsonlib

    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = _add(client)
        assert res.status_code == 200
        body = res.json()
        assert body["created"] is True
        source_id = body["source_id"]
        assert source_id.startswith("user_rss_")
        assert body["name"] == "Test Blog"  # 默认名取 feed title

        with Session(app_module.db_sink.engine) as session:
            record = session.get(SourceConfigRecord, source_id)
            assert record is not None and record.owner_username == "alice"
            assert record.source_type == "rss" and record.category == "user"
            params = jsonlib.loads(record.params_json)
            # 最简正文拍板:feed 给什么存什么,永不触发详情补抓
            assert params["fetch_detail_if_missing"] is False

        # 添加即订阅;目录里可见且带 user_source 标记
        catalog = client.get("/api/reader/sources").json()
        entry = next(s for s in catalog["sources"] if s["source_id"] == source_id)
        assert entry["subscribed"] is True and entry["user_source"] is True

        # 我的自定源列表
        listing = client.get("/api/reader/custom-sources").json()
        assert [i["source_id"] for i in listing["items"]] == [source_id]
        assert listing["quota"]["used"] == 1


def test_preview_endpoint_returns_entries_and_quota(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = client.post("/api/reader/custom-sources/preview",
                          json={"url": "https://blog.example.com/feed"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok" and body["feed_title"] == "Test Blog"
        assert body["quota"] == {"used": 0, "max": 20}


def test_dedup_share_same_url_two_users(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        first = _add(client).json()
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        second = _add(client).json()
    # 同 canonical URL → 同一配置行(created=False),双方各自订阅
    assert second["source_id"] == first["source_id"]
    assert second["created"] is False
    with Session(app_module.db_sink.engine) as session:
        rows = session.exec(select(SourceConfigRecord).where(
            SourceConfigRecord.owner_username != "")).all()
        assert len(rows) == 1 and rows[0].owner_username == "alice"  # owner 记首建者
    from services import user_sources
    with Session(app_module.db_sink.engine) as session:
        assert user_sources.active_subscriber_usernames(
            session, first["source_id"]) == ["alice", "bob"]


def test_quota_max_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import user_sources
    monkeypatch.setattr(user_sources, "MAX_SOURCES_PER_USER", 2)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _add(client, "https://a.example.com/feed").status_code == 200
        assert _add(client, "https://b.example.com/feed").status_code == 200
        res = _add(client, "https://c.example.com/feed")
        assert res.status_code == 400 and "上限" in res.json()["detail"]


def test_quota_daily_add_limit(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import user_sources
    monkeypatch.setattr(user_sources, "DAILY_ADD_LIMIT", 1)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _add(client, "https://a.example.com/feed").status_code == 200
        assert _add(client, "https://b.example.com/feed").status_code == 429


# ==================== 系统源撞库 ====================

def test_conflict_with_preset_feed_redirects_to_subscribe(monkeypatch, tmp_path):
    """撞中可见系统源(preset feed_url):不建用户源,返回引导载荷。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    from fetchers.registry import fetcher_registry

    # 取一个真实带 feed_url 的 preset 作撞库对象
    preset_id, preset_url = next(
        (sid, getattr(cls, "feed_url"))
        for sid, cls in fetcher_registry._fetchers.items()  # noqa: SLF001
        if getattr(cls, "feed_url", "") and not getattr(cls, "is_template", False)
    )
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = _add(client, preset_url)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "exists"
        assert body["existing"]["source_id"] == preset_id
        # preview 同样引导
        pv = client.post("/api/reader/custom-sources/preview", json={"url": preset_url}).json()
        assert pv["status"] == "exists" and pv["existing"]["source_id"] == preset_id


def test_conflict_with_hidden_system_source_is_404(monkeypatch, tmp_path):
    """撞中被隐藏系统源:统一「暂不可用」,不泄露隐藏细节、不造影子源。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    from fetchers.registry import fetcher_registry
    from services import source_visibility

    preset_id, preset_url = next(
        (sid, getattr(cls, "feed_url"))
        for sid, cls in fetcher_registry._fetchers.items()  # noqa: SLF001
        if getattr(cls, "feed_url", "") and not getattr(cls, "is_template", False)
    )
    with Session(app_module.db_sink.engine) as session:
        source_visibility.set_source_hidden(session, preset_id, True)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = _add(client, preset_url)
        assert res.status_code == 404 and "暂不可用" in res.json()["detail"]


# ==================== 隔离面 ====================

def _seed_article(engine, article_id, source_id, fetched="2026-08-27T10:00:00"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=f"文章 {article_id}", content="正文内容足够长" * 5,
            content_type="rss_article", source_id=source_id,
            source_url=f"https://x.example.com/{article_id}",
            publish_date="2026-08-27", fetched_date=fetched,
        ))
        session.commit()


def test_catalog_hides_user_source_from_non_subscriber(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        catalog = client.get("/api/reader/sources").json()
        assert all(s["source_id"] != source_id for s in catalog["sources"])


def test_all_visible_scope_excludes_user_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from api.feed_service import resolve_all_visible_source_ids

    _seed_article(app_module.db_sink.engine, "u1", "user_rss_abc123def456")
    _seed_article(app_module.db_sink.engine, "n1", "rss_normal_source")
    with Session(app_module.db_sink.engine) as session:
        visible = resolve_all_visible_source_ids(session)
    assert "rss_normal_source" in visible
    assert "user_rss_abc123def456" not in visible


def test_archive_export_excludes_user_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    _seed_article(app_module.db_sink.engine, "u1", "user_rss_abc123def456")
    _seed_article(app_module.db_sink.engine, "n1", "rss_normal_source")
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        body = client.get("/api/archive/export/articles.jsonl").text
        assert "rss_normal_source" in body
        assert "user_rss_abc123def456" not in body


def test_daily_brief_candidates_exclude_user_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services.daily_brief import collect_candidates

    _seed_article(app_module.db_sink.engine, "u1", "user_rss_abc123def456")
    _seed_article(app_module.db_sink.engine, "n1", "rss_normal_source")
    with Session(app_module.db_sink.engine) as session:
        candidates, _cursor, scanned = collect_candidates(session, cursor="")
    ids = {c.id for c in candidates}
    assert "n1" in ids and "u1" not in ids


def test_article_list_gate_blocks_non_subscriber(monkeypatch, tmp_path):
    """轻门槛:非订阅者按 source_id 直查用户源得空;订阅者(添加人)可见。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    _seed_article(app_module.db_sink.engine, "u1", source_id)
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        res = client.get(f"/api/articles?source_id={source_id}")
        assert res.status_code == 200 and res.json() == []
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = client.get(f"/api/articles?source_id={source_id}")
        assert [a["id"] for a in res.json()] == ["u1"]
    # admin 会话不受门槛影响(知识台账全档)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.get(f"/api/articles?source_id={source_id}")
        assert [a["id"] for a in res.json()] == ["u1"]


# ==================== 删除级联 ====================

def test_remove_sole_subscriber_purges_source(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import ArticleRecord, SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
        _seed_article(app_module.db_sink.engine, "u1", source_id)
        res = client.delete(f"/api/reader/custom-sources/{source_id}")
        assert res.status_code == 200 and res.json()["purged"] is True
    with Session(app_module.db_sink.engine) as session:
        assert session.get(SourceConfigRecord, source_id) is None
        assert session.exec(select(ArticleRecord).where(
            ArticleRecord.source_id == source_id)).all() == []


def test_remove_with_other_subscriber_keeps_source(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        _add(client)  # 去重共享订阅
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = client.delete(f"/api/reader/custom-sources/{source_id}")
        assert res.status_code == 200 and res.json()["purged"] is False
    with Session(app_module.db_sink.engine) as session:
        assert session.get(SourceConfigRecord, source_id) is not None
    from services import user_sources
    with Session(app_module.db_sink.engine) as session:
        assert user_sources.active_subscriber_usernames(session, source_id) == ["bob"]


def test_admin_force_delete_cascades_subscriptions(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        _add(client)
    _seed_article(app_module.db_sink.engine, "u1", source_id)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.delete(f"/api/admin/user-sources/{source_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["affected_subscribers"] == ["alice", "bob"]
        assert body["articles_deleted"] == 1
    with Session(app_module.db_sink.engine) as session:
        assert session.get(SourceConfigRecord, source_id) is None
    from services import user_sources
    with Session(app_module.db_sink.engine) as session:
        assert user_sources.active_subscriber_usernames(session, source_id) == []


# ==================== 调度治理 ====================

def test_auto_disable_failing_sources(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord, SourceStateRecord
    from services import user_sources

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    with Session(app_module.db_sink.engine) as session:
        session.add(SourceStateRecord(
            source_id=source_id, fetcher_id="generic_rss", status="failing",
            consecutive_failures=user_sources.AUTO_DISABLE_FAILURES,
            updated_at=datetime.datetime.now().isoformat(),
        ))
        session.commit()
    with Session(app_module.db_sink.engine) as session:
        disabled = user_sources.auto_disable_failing(session)
        assert disabled == [source_id]
        assert session.get(SourceConfigRecord, source_id).is_active is False
    # 重新添加同 URL 即复活
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _add(client).json()["created"] is False
    with Session(app_module.db_sink.engine) as session:
        assert session.get(SourceConfigRecord, source_id).is_active is True


# ==================== 总闸 / admin 配置 / 门控 ====================

def test_master_switch_blocks_add_but_not_cleanup(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import user_sources

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
        with Session(app_module.db_sink.engine) as session:
            user_sources.set_feature_enabled(session, False)
        # 添加/preview 403
        assert _add(client, "https://b.example.com/feed").status_code == 403
        assert client.post("/api/reader/custom-sources/preview",
                           json={"url": "https://b.example.com/feed"}).status_code == 403
        # 列表/删除不挡(允许清理);既有源与文章数据不动
        assert client.get("/api/reader/custom-sources").status_code == 200
        assert client.delete(f"/api/reader/custom-sources/{source_id}").status_code == 200
        # runtime 能力位透出
        runtime = client.get("/api/runtime").json()
        assert runtime["user_sources_enabled"] is False


def test_admin_config_and_overview(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    reloaded = []
    monkeypatch.setattr(app_module, "reload_user_rss_schedule", lambda: reloaded.append(True))
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        _add(client)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        overview = client.get("/api/admin/user-sources").json()
        assert overview["kpi"]["source_count"] == 1
        assert overview["kpi"]["covered_users"] == 1
        assert overview["items"][0]["owner_username"] == "alice"
        assert overview["items"][0]["subscriber_count"] == 1
        # 配置写入 + 调度热生效
        res = client.post("/api/admin/user-sources/config",
                          json={"enabled": True, "refresh_minutes": 30})
        assert res.status_code == 200 and res.json()["refresh_minutes"] == 30
        assert reloaded  # reload_user_rss_schedule 被调用
        # 间隔下限保护
        res = client.post("/api/admin/user-sources/config", json={"refresh_minutes": 1})
        assert res.json()["refresh_minutes"] == user_sources_min_refresh()


def user_sources_min_refresh():
    from services.user_sources import MIN_REFRESH_MINUTES

    return MIN_REFRESH_MINUTES


def test_admin_endpoints_require_admin(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/admin/user-sources").status_code == 403
        assert client.post("/api/admin/user-sources/config", json={"enabled": False}).status_code == 403
        assert client.delete("/api/admin/user-sources/whatever").status_code == 403


def test_admin_toggle_user_source(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        source_id = _add(client).json()["source_id"]
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.post(f"/api/admin/user-sources/{source_id}/toggle",
                          json={"is_active": False})
        assert res.status_code == 200 and res.json()["is_active"] is False
        # 非用户源 404(平台配置源不归本端点管)
        assert client.post("/api/admin/user-sources/rss_openai_news/toggle",
                           json={"is_active": False}).status_code == 404
    with Session(app_module.db_sink.engine) as session:
        assert session.get(SourceConfigRecord, source_id).is_active is False
