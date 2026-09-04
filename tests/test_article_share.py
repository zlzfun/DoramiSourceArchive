"""文章分享（公开只读链接）回归测试。

覆盖签发/撤销/归属隔离/限额，以及公开端点的四条失效路径（过期、撤销、总闸关闭、
源被隐藏）与访问计量。站内深链是纯前端 URL，不落库、无端点，故不在此覆盖。
"""

import datetime
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"), ("bob", "bob", "user"))


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from models.db import ArticleRecord

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_share.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="a1", title="测试文章",
            content="# 测试文章\n\n正文段落。\n\n![配图](https://img.example.com/pic.png)",
            content_type="web_article", source_id="src_a", source_url="http://example.com/a1",
            publish_date="2026-07-01", fetched_date="2026-07-01",
        ))
        session.commit()
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _create(client, article_id="a1", days=7):
    return client.post(f"/api/reader/articles/{article_id}/share", json={"expires_in_days": days})


def test_share_endpoints_require_authentication(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _create(client).status_code == 401
        assert client.get("/api/reader/shares").status_code == 401
        assert client.delete("/api/reader/shares/1").status_code == 401
        # 总闸是管理端点，读者/游客均不可读写
        assert client.get("/api/admin/public-share").status_code == 401


def test_create_share_and_open_publicly(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        created = _create(client)
        assert created.status_code == 200
        body = created.json()
        assert body["token"].startswith("dshr_")
        assert body["live"] is True and body["view_count"] == 0
        assert body["expires_at"]  # 7 天档位应写入过期时间
        token = body["token"]

    # 公开端点无会话即可访问（另起 client，不带 cookie）
    with TestClient(app_module.app) as guest:
        res = guest.get(f"/api/public/share/{token}")
        assert res.status_code == 200
        payload = res.json()
        assert payload["title"] == "测试文章"
        assert "正文段落" in payload["content"]
        assert payload["shared_by"] == "alice"
        # 贫瘠响应：不得夹带任何可用于扩大访问面的字段
        assert "article_id" not in payload and "token" not in payload

        # 每次打开累加计量
        guest.get(f"/api/public/share/{token}")

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        item = client.get("/api/reader/shares").json()["items"][0]
        assert item["view_count"] == 2


def test_permanent_share_has_no_expiry(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        body = _create(client, days=None).json()
        assert body["expires_at"] is None and body["live"] is True


def test_invalid_expiry_and_missing_article_rejected(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _create(client, days=999).status_code == 400
        assert _create(client, article_id="nope").status_code == 404


def test_revoked_share_stops_resolving(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        created = _create(client).json()
        token, share_id = created["token"], created["id"]
        revoked = client.delete(f"/api/reader/shares/{share_id}")
        assert revoked.status_code == 200
        assert revoked.json()["live"] is False
        # 幂等：再撤一次仍 200
        assert client.delete(f"/api/reader/shares/{share_id}").status_code == 200

    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 404


def test_expired_share_stops_resolving(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import ArticleShareRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        token = _create(client).json()["token"]

    # 把过期时间挪到过去，等价于「7 天后再来打开」
    with Session(app_module.db_sink.engine) as session:
        record = session.exec(select(ArticleShareRecord)).first()
        record.expires_at = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat()
        session.add(record)
        session.commit()

    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 404


def test_unknown_and_malformed_tokens_are_404(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as guest:
        assert guest.get("/api/public/share/dshr_nonexistent").status_code == 404
        # 非 dshr_ 前缀（例如拿聚合令牌来试）同样 404，不泄露令牌种类信息
        assert guest.get("/api/public/share/dfeed_something").status_code == 404


def test_share_is_owner_scoped(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        share_id = _create(client).json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        # bob 看不到 alice 的分享，也撤不掉
        assert client.get("/api/reader/shares").json()["items"] == []
        assert client.delete(f"/api/reader/shares/{share_id}").status_code == 404

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/reader/shares").json()["items"][0]["live"] is True


def test_hidden_source_cannot_be_shared_and_existing_links_die(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import source_visibility

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        token = _create(client).json()["token"]

    with Session(app_module.db_sink.engine) as session:
        source_visibility.set_source_hidden(session, "src_a", True)

    # 既有链接立即失效，与「读者面隐藏 = 内容交付全量排除」同口径
    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 404

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _create(client).status_code == 403


def test_existing_share_dies_when_source_becomes_credentialed(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import SourceConfigRecord

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        token = _create(client).json()["token"]

    with Session(app_module.db_sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="src_a",
            name="Reclassified RSS",
            source_type="rss",
            url="https://feeds.example.test/rss?subscriber=Abc123Def456Ghi789Jkl012",
            params_json='{"credentialed_private": true}',
            created_at="2026-07-02T00:00:00",
            updated_at="2026-07-02T00:00:00",
        ))
        session.commit()

    # Resolve-time policy applies to the article and its media proxy alike.
    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 404
        assert guest.get(
            f"/api/public/share/{token}/media",
            params={"url": "https://img.example.com/pic.png"},
        ).status_code == 404


def test_global_switch_kills_public_links_without_deleting_them(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        token = _create(client).json()["token"]

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        status = client.get("/api/admin/public-share").json()
        assert status["enabled"] is True and status["live_count"] == 1
        assert client.post("/api/admin/public-share", json={"enabled": False}).json()["enabled"] is False

    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 404

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _create(client).status_code == 403

    # 重新开启即回归——总闸不销毁签发记录
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert client.post("/api/admin/public-share", json={"enabled": True}).json()["enabled"] is True

    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{token}").status_code == 200


def test_public_share_switch_is_admin_only(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/admin/public-share").status_code == 403
        assert client.post("/api/admin/public-share", json={"enabled": False}).status_code == 403


def test_regenerate_rotates_old_link(monkeypatch, tmp_path):
    """一篇一链:同一文章再次生成 → 旧链接被撤销,存活链接恒 ≤1(rotate 语义)。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        first = _create(client, days=7).json()
        second = _create(client, days=30).json()
        assert first["token"] != second["token"]
        items = client.get("/api/reader/shares?article_id=a1").json()["items"]
        live = [i for i in items if i["live"]]
        assert len(live) == 1 and live[0]["token"] == second["token"]

    with TestClient(app_module.app) as guest:
        assert guest.get(f"/api/public/share/{first['token']}").status_code == 404
        assert guest.get(f"/api/public/share/{second['token']}").status_code == 200


def test_shared_media_serves_only_article_images(monkeypatch, tmp_path):
    """分享页取图端点：护栏与文章端点同套，且只放行该文章自身的图链。

    media_store 置 None 走 302 回源降级路径——既隔离网络，又验证降级契约本身。
    """
    app_module = _setup_app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "media_store", None)
    img = "https://img.example.com/pic.png"

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        token = _create(client).json()["token"]

    with TestClient(app_module.app, follow_redirects=False) as guest:
        # 正文中的图链 → 免登录放行（库关闭时 302 回源）
        res = guest.get(f"/api/public/share/{token}/media?url={img}")
        assert res.status_code == 302 and res.headers["location"] == img
        # 不属于这一篇的 URL → 404:令牌不是开放图片代理的通行证
        assert guest.get(
            f"/api/public/share/{token}/media?url=https://evil.example.com/x.png"
        ).status_code == 404
        # 坏令牌 → 404
        assert guest.get(f"/api/public/share/dshr_bad/media?url={img}").status_code == 404

    # 撤销后媒体端点同步失效
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        share_id = client.get("/api/reader/shares").json()["items"][0]["id"]
        client.delete(f"/api/reader/shares/{share_id}")
    with TestClient(app_module.app, follow_redirects=False) as guest:
        assert guest.get(f"/api/public/share/{token}/media?url={img}").status_code == 404


def test_daily_share_limit(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import article_share

    monkeypatch.setattr(article_share, "DAILY_SHARE_LIMIT", 2)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _create(client).status_code == 200
        assert _create(client).status_code == 200
        assert _create(client).status_code == 429
