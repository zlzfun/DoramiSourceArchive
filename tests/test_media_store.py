"""媒体库（图床）测试。

覆盖 services.media_store：图链提取（markdown/HTML/去重/非 http 剔除）、下载缓存
（内容去重共用落盘文件、二次命中不发网络请求）、非图片响应/HTTP 错误/大小超限的
失败负缓存与冷却窗口、SSRF 私网拦截、按文章批量预取；以及 API 面：
GET /api/media/proxy 的命中回文件/失败 302 回源/停用 302/参数校验/读者可访问，
/api/admin/media/stats|backfill 的 admin 鉴权与回填 job 端到端。
"""

import asyncio
import os
import sys
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import ArticleRecord, MediaAssetRecord  # noqa: E402
from services import media_store as ms  # noqa: E402
from services.media_store import MediaStore, extract_image_urls, url_hash_of  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fakepixels" * 8


def _sink(tmp_path, name="media.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _counting_transport(calls, body=PNG_BYTES, content_type="image/png", status=200):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


async def _public_ok(host):  # 单测里绕开真实 DNS；SSRF 判定另有专测
    return True


def _store(engine, tmp_path, transport, **kwargs):
    return MediaStore(engine, tmp_path / "media", transport=transport, **kwargs)


# ==================== 图链提取 ====================

def test_extract_image_urls_markdown_html_dedup():
    content = (
        "开头 ![图一](https://a.com/1.png) 中间\n"
        '![带标题](https://a.com/2.png "title")\n'
        "![尖括号](<https://a.com/3.png>)\n"
        '<img src="https://a.com/4.png?x=1&amp;y=2" alt="">\n'
        "![重复](https://a.com/1.png)\n"
        "![data](data:image/png;base64,AAAA)\n"
        "[普通链接](https://a.com/page.html)\n"
        "![相对](/local/5.png)\n"
    )
    urls = extract_image_urls(content)
    assert urls == [
        "https://a.com/1.png",
        "https://a.com/2.png",
        "https://a.com/3.png",
        "https://a.com/4.png?x=1&y=2",  # HTML 实体已还原
    ]
    assert extract_image_urls(None) == []
    assert extract_image_urls("无图正文") == []


# ==================== 下载缓存与去重 ====================

def test_get_or_fetch_caches_then_hits_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    calls = []
    store = _store(engine, tmp_path, _counting_transport(calls))

    async def scenario():
        record = await store.get_or_fetch("https://cdn.example.com/pic.png")
        assert record is not None and record.status == "cached"
        assert record.mime == "image/png" and record.ext == ".png"
        path = store.file_path_for(record)
        assert path.is_file() and path.read_bytes() == PNG_BYTES
        # 二次取图：命中缓存，不再发起网络请求
        again = await store.get_or_fetch("https://cdn.example.com/pic.png")
        assert again is not None and len(calls) == 1
        # 不同 URL、相同字节 → 各自一行记录，共用同一份落盘文件
        other = await store.get_or_fetch("https://mirror.example.com/same.png")
        assert other is not None and len(calls) == 2
        assert store.file_path_for(other) == path

    asyncio.run(scenario())
    stats = store.stats()
    assert stats["cached_count"] == 2
    assert stats["distinct_files"] == 1
    assert stats["disk_bytes"] == len(PNG_BYTES)
    assert stats["failed_count"] == 0


def test_non_image_response_fails_with_cooling(tmp_path, monkeypatch):
    """HTML 响应（如 CF 挑战页）不得缓存为图；失败负缓存冷却期内不重试。"""
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    calls = []
    store = _store(engine, tmp_path, _counting_transport(calls, body=b"<html>challenge</html>", content_type="text/html"))

    async def scenario():
        assert await store.get_or_fetch("https://cdn.example.com/fake.png") is None
        assert await store.get_or_fetch("https://cdn.example.com/fake.png") is None

    asyncio.run(scenario())
    assert len(calls) == 1  # 第二次落在冷却窗口内，未发请求
    with Session(engine) as session:
        record = session.get(MediaAssetRecord, url_hash_of("https://cdn.example.com/fake.png"))
        assert record.status == "failed" and record.fail_count == 1
        assert "不是图片" in record.last_error


def test_http_error_and_size_cap_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    store404 = _store(engine, tmp_path, _counting_transport([], status=404))
    capped = _store(engine, tmp_path, _counting_transport([]), max_bytes=8)

    async def scenario():
        assert await store404.get_or_fetch("https://cdn.example.com/gone.png") is None
        assert await capped.get_or_fetch("https://cdn.example.com/big.png") is None

    asyncio.run(scenario())
    with Session(engine) as session:
        gone = session.get(MediaAssetRecord, url_hash_of("https://cdn.example.com/gone.png"))
        big = session.get(MediaAssetRecord, url_hash_of("https://cdn.example.com/big.png"))
        assert gone.status == "failed" and "404" in gone.last_error
        assert big.status == "failed" and "大小上限" in big.last_error


def test_ssrf_private_hosts_blocked_without_request(tmp_path):
    engine = _sink(tmp_path).engine
    calls = []
    store = _store(engine, tmp_path, _counting_transport(calls))

    async def scenario():
        for url in ("http://127.0.0.1/x.png", "http://192.168.1.5/x.png", "http://10.0.0.8/x.png"):
            assert await store.get_or_fetch(url) is None
        assert await store.get_or_fetch("ftp://example.com/x.png") is None  # 非 http(s)

    asyncio.run(scenario())
    assert calls == []  # 全部在 SSRF 防护处拦截，未发任何请求
    with Session(engine) as session:
        record = session.get(MediaAssetRecord, url_hash_of("http://127.0.0.1/x.png"))
        assert record.status == "failed" and "SSRF" in record.last_error


def test_referer_derivation_and_sent(tmp_path, monkeypatch):
    """防盗链解：按图片域名推导站内 Referer 并随请求发出（qbitai 实测 403→200）。"""
    assert ms._referer_for("https://i.qbitai.com/wp-content/x.webp") == "https://qbitai.com/"
    assert ms._referer_for("https://mmbiz.qpic.cn/mmbiz_jpg/x") == "https://qpic.cn/"
    assert ms._referer_for("https://cdn.example.com/a.png") == "https://example.com/"
    # 非子域前缀不剥（避免把 news.site.com 误削成 site.com 反而不合法）
    assert ms._referer_for("https://news.example.com/a.png") == "https://news.example.com/"
    assert ms._referer_for("https://example.com/a.png") == "https://example.com/"

    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    seen = {}

    def handler(request):
        seen["referer"] = request.headers.get("referer")
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

    store = _store(engine, tmp_path, httpx.MockTransport(handler))
    asyncio.run(store.get_or_fetch("https://i.qbitai.com/wp-content/uploads/x.png"))
    assert seen["referer"] == "https://qbitai.com/"


def test_resolve_is_public_rules(monkeypatch):
    """SSRF 判定：字面私网 IP 拒绝、公网放行；域名解析出危险段拒绝、
    fake-ip 段（本机代理 DNS 接管，见 _FAKE_IP_NET）豁免。"""

    async def scenario():
        # 字面 IP：无需 DNS
        assert await ms._resolve_is_public("127.0.0.1") is False
        assert await ms._resolve_is_public("10.0.0.8") is False
        assert await ms._resolve_is_public("8.8.8.8") is True

        async def fake_resolver(ips):
            async def _resolve(host):
                return ips
            return _resolve

        # 域名解析到内网 → 拒绝（DNS rebinding 场景）
        monkeypatch.setattr(ms, "_resolve_host_ips", await fake_resolver(["192.168.0.10"]))
        assert await ms._resolve_is_public("evil.example.com") is False
        # 域名解析到 fake-ip 段 → 豁免（Clash/Surge 代理环境下一切域名如此解析）
        monkeypatch.setattr(ms, "_resolve_host_ips", await fake_resolver(["198.18.0.199"]))
        assert await ms._resolve_is_public("cdn.example.com") is True
        # 正常公网解析 → 放行
        monkeypatch.setattr(ms, "_resolve_host_ips", await fake_resolver(["93.184.216.34"]))
        assert await ms._resolve_is_public("cdn.example.com") is True

    asyncio.run(scenario())


def test_prefetch_articles_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    with Session(engine) as session:
        session.add(ArticleRecord(
            id="a1", title="有图", content_type="web_article", source_id="s",
            source_url="http://x", publish_date="2026-07-01", fetched_date="2026-07-01",
            content="![a](https://cdn.example.com/1.png) ![b](https://cdn.example.com/2.png)",
        ))
        session.add(ArticleRecord(
            id="a2", title="无图", content_type="web_article", source_id="s",
            source_url="http://y", publish_date="2026-07-01", fetched_date="2026-07-01",
            content="纯文本",
        ))
        session.commit()
    calls = []
    store = _store(engine, tmp_path, _counting_transport(calls))

    counts = asyncio.run(store.prefetch_articles(["a1", "a2", "missing"]))
    assert counts == {"articles": 2, "cached": 2, "failed": 0}
    assert len(calls) == 2


# ==================== API 面 ====================

def _auth_config():
    from config import _auth_credentials
    import api.app as app_module

    return replace(
        app_module.settings.auth,
        admin_users=_auth_credentials("admin:admin"),
        user_users=_auth_credentials("user:user"),
    )


def _setup_app(monkeypatch, tmp_path, *, transport=None, store_none=False):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = _sink(tmp_path, "app_media.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    accounts_service.seed_users_if_empty(sink.engine, _auth_config())
    if store_none:
        monkeypatch.setattr(app_module, "media_store", None)
    else:
        calls = []
        store = _store(sink.engine, tmp_path, transport or _counting_transport(calls))
        monkeypatch.setattr(app_module, "media_store", store)
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    return app_module, sink


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_proxy_serves_cached_file_for_reader(monkeypatch, tmp_path):
    app_module, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")  # 受限读者即可取图
        resp = client.get("/api/media/proxy", params={"url": "https://cdn.example.com/p.png"})
        assert resp.status_code == 200
        assert resp.content == PNG_BYTES
        assert resp.headers["content-type"].startswith("image/png")
        assert "immutable" in resp.headers["cache-control"]


def test_proxy_redirects_to_origin_on_failure_and_disabled(monkeypatch, tmp_path):
    # 下载失败 → 302 回源
    app_module, _ = _setup_app(
        monkeypatch, tmp_path, transport=_counting_transport([], status=404)
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        resp = client.get(
            "/api/media/proxy", params={"url": "https://cdn.example.com/gone.png"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://cdn.example.com/gone.png"
        assert resp.headers["cache-control"] == "no-store"


def test_proxy_disabled_store_redirects(monkeypatch, tmp_path):
    app_module, _ = _setup_app(monkeypatch, tmp_path, store_none=True)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        resp = client.get(
            "/api/media/proxy", params={"url": "https://cdn.example.com/p.png"},
            follow_redirects=False,
        )
        assert resp.status_code == 302


def test_proxy_rejects_non_http_and_requires_login(monkeypatch, tmp_path):
    app_module, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert client.get("/api/media/proxy", params={"url": "file:///etc/passwd"}).status_code == 400
    with TestClient(app_module.app) as client:
        resp = client.get("/api/media/proxy", params={"url": "https://cdn.example.com/p.png"})
        assert resp.status_code == 401


def test_url_status_map_three_states(tmp_path, monkeypatch):
    """状态盘点：已缓存 / 失败 / 从未尝试 三态（热点图的数据基础）。"""
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    ok_store = _store(engine, tmp_path, _counting_transport([]))
    bad_store = _store(engine, tmp_path, _counting_transport([], status=403))

    async def scenario():
        await ok_store.get_or_fetch("https://cdn.example.com/ok.png")
        await bad_store.get_or_fetch("https://cdn.example.com/bad.png")

    asyncio.run(scenario())
    statuses = ok_store.url_status_map([
        "https://cdn.example.com/ok.png",
        "https://cdn.example.com/bad.png",
        "https://cdn.example.com/never.png",
    ])
    assert statuses["https://cdn.example.com/ok.png"]["status"] == "cached"
    assert statuses["https://cdn.example.com/bad.png"]["status"] == "failed"
    assert "403" in statuses["https://cdn.example.com/bad.png"]["error"]
    assert statuses["https://cdn.example.com/never.png"] == {"status": "pending", "error": None}


def test_force_bypasses_failed_cooldown(tmp_path, monkeypatch):
    """定点重试：force=True 绕过负缓存冷却窗口（普通路径仍不重试）。"""
    monkeypatch.setattr(ms, "_resolve_is_public", _public_ok)
    engine = _sink(tmp_path).engine
    calls = []
    failing = _store(engine, tmp_path, _counting_transport(calls, status=500))

    async def scenario():
        assert await failing.get_or_fetch("https://cdn.example.com/x.png") is None
        assert await failing.get_or_fetch("https://cdn.example.com/x.png") is None  # 冷却中
        assert len(calls) == 1
        # force：无视冷却再打一次（此处仍失败，但确实发了请求）
        assert await failing.get_or_fetch("https://cdn.example.com/x.png", force=True) is None
        assert len(calls) == 2

    asyncio.run(scenario())


def test_heatmap_day_detail_and_article_prefetch(monkeypatch, tmp_path):
    """热点图三端点：逐日聚合 / 单日明细 / 单篇定点重试（含 admin 门控与 404）。"""
    app_module, sink = _setup_app(monkeypatch, tmp_path)
    today = __import__("datetime").date.today().isoformat()
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="h1", title="有图文章", content_type="web_article", source_id="s1",
            source_url="http://x", publish_date=today, fetched_date=f"{today}T09:00:00",
            content="![a](https://cdn.example.com/h1.png) ![b](https://cdn.example.com/h2.png)",
        ))
        session.add(ArticleRecord(
            id="h2", title="无图文章", content_type="web_article", source_id="s2",
            source_url="http://y", publish_date=today, fetched_date=f"{today}T10:00:00",
            content="纯文本",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")  # 读者禁入
        assert client.get("/api/admin/media/heatmap").status_code == 403
        assert client.get(f"/api/admin/media/days/{today}").status_code == 403
        assert client.post("/api/admin/media/articles/h1/prefetch").status_code == 403

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 尚未预取 → 两张图都是 pending
        day = next(d for d in client.get("/api/admin/media/heatmap").json()["days"] if d["date"] == today)
        assert day["articles"] == 2 and day["with_images"] == 1
        assert day["images_total"] == 2 and day["pending"] == 2 and day["cached"] == 0

        detail = client.get(f"/api/admin/media/days/{today}").json()
        assert [a["id"] for a in detail["articles"]] == ["h1", "h2"]
        assert detail["articles"][0]["pending"] == 2
        assert detail["articles"][1]["images_total"] == 0
        assert all(img["status"] == "pending" for img in detail["articles"][0]["images"])

        # 单篇定点重试 → 两张图转 cached，热点图随之更新
        resp = client.post("/api/admin/media/articles/h1/prefetch").json()
        assert resp["cached"] == 2 and resp["failed"] == 0
        assert all(img["status"] == "cached" for img in resp["images"])
        day = next(d for d in client.get("/api/admin/media/heatmap").json()["days"] if d["date"] == today)
        assert day["cached"] == 2 and day["pending"] == 0

        assert client.post("/api/admin/media/articles/nope/prefetch").status_code == 404
        assert client.get("/api/admin/media/days/2026-7-1").status_code == 400


def test_admin_media_endpoints_gated_and_backfill_e2e(monkeypatch, tmp_path):
    app_module, sink = _setup_app(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="m1", title="带图文章", content_type="web_article", source_id="s",
            source_url="http://x", publish_date="2026-07-01", fetched_date="2026-07-01",
            content="![a](https://cdn.example.com/one.png)",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        # 读者禁入管理端点
        _login(client, "user", "user")
        assert client.get("/api/admin/media/stats").status_code == 403
        assert client.post("/api/admin/media/backfill").status_code == 403

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        stats = client.get("/api/admin/media/stats").json()
        assert stats["enabled"] is True and stats["cached_count"] == 0

        resp = client.post("/api/admin/media/backfill")
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        final = None
        for _ in range(200):
            final = client.get(f"/api/jobs/{job_id}").json()
            if final["status"] in ("succeeded", "failed"):
                break
        assert final["status"] == "succeeded", final
        assert final["result"] == {
            "articles_scanned": 1,
            "articles_with_images": 1,
            "images_cached": 1,
            "images_failed": 0,
        }
        stats = client.get("/api/admin/media/stats").json()
        assert stats["cached_count"] == 1 and stats["disk_bytes"] == len(PNG_BYTES)
