"""小程序端后端三切口回归测试(Issue #17,方案 docs/wechat-miniprogram-wave-plan.md §5)。

① Bearer 会话载体:与 Cookie 承载同一 token,登录显式索取才回 token(浏览器响应形状不变),
   改密(会话世代)/停用即时吊销对 Bearer 同样生效;非会话形态的 Bearer(dfeed_ 等)不当会话。
② 渲染端点:markdown → 净化 HTML(原始 HTML 转义、img 改签名链、重复首行标题剥离、
   译文缓存附带),可见性与单条详情同口径(隐藏源读者 404、admin 可见)。
③ 签名公开图链:免登录放行、篡改/过期/缺参 404、媒体库停用 302 回源。
"""

import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"))
_IMG = "https://img.example.com/pic.png"


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from models.db import ArticleRecord

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_mp.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    monkeypatch.setattr(app_module, "media_store", None)
    seed_default_accounts(sink.engine, _ACCOUNTS)
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="a1", title="🤖 哆啦美 AI 资讯日报 · 2026-07-01",
            content=(
                "# 🤖 哆啦美 AI 资讯日报 · 2026-07-01\n\n"
                "正文段落 <script>alert(1)</script> 含 **强调**。\n\n"
                f"![配图]({_IMG})\n\n"
                "![坏图](data:image/png;base64,AAAA)\n\n"
                "| 列 | 值 |\n|:---|---:|\n| a | 1 |\n\n"
                "```python\nprint(1)\n```\n"
            ),
            content_type="daily_brief", source_id="src_a", source_url="http://example.com/a1",
            publish_date="2026-07-01", fetched_date="2026-07-01",
        ))
        session.add(ArticleRecord(
            id="a2", title="Hidden one", content="body", content_type="web_article",
            source_id="src_hidden", source_url="http://example.com/a2",
            publish_date="2026-07-01", fetched_date="2026-07-01",
        ))
        session.add(ArticleRecord(
            id="a3", title="English title", content="Hello **world**",
            content_type="web_article", source_id="src_a", source_url="http://example.com/a3",
            publish_date="2026-07-01", fetched_date="2026-07-01",
            extensions_json=json.dumps({
                "translation_zh": "你好 **世界**",
                "translation_zh_title": "英文标题",
            }),
        ))
        session.commit()
    return app_module


def _login(client, username, password, **extra):
    return client.post("/api/auth/login", json={"username": username, "password": password, **extra})


# ---------- ① Bearer 会话载体 ----------

def test_login_returns_token_only_when_requested(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        browser = _login(client, "alice", "alice")
        assert browser.status_code == 200
        assert "session_token" not in browser.json()  # 浏览器路径响应形状不变
        client.cookies.clear()

        by_header = client.post(
            "/api/auth/login", json={"username": "alice", "password": "alice"},
            headers={"X-Dorami-Client": "miniprogram"},
        )
        assert by_header.status_code == 200
        body = by_header.json()
        assert body["session_token"] and "." in body["session_token"]
        assert body["session_expires_in"] == app_module.AUTH_SESSION_SECONDS
        client.cookies.clear()

        by_body = _login(client, "alice", "alice", return_token=True)
        assert by_body.json().get("session_token")


def test_bearer_token_is_equivalent_to_cookie(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        token = _login(client, "alice", "alice", return_token=True).json()["session_token"]
        client.cookies.clear()
        assert client.get("/api/reader/sources").status_code == 401
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/reader/sources", headers=auth).status_code == 200
        me = client.get("/api/auth/session", headers=auth).json()
        assert me["authenticated"] is True and me["user"]["username"] == "alice"
        # 非会话形态的 Bearer(聚合令牌样式,无点号)与坏签名均不当会话
        assert client.get("/api/reader/sources", headers={"Authorization": "Bearer dfeed_abcdef"}).status_code == 401
        payload, _sig = token.rsplit(".", 1)
        assert client.get("/api/reader/sources", headers={"Authorization": f"Bearer {payload}.deadbeef"}).status_code == 401


def test_bearer_revoked_by_password_reset_and_deactivation(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    with TestClient(app_module.app) as client:
        token = _login(client, "alice", "alice", return_token=True).json()["session_token"]
        client.cookies.clear()
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/reader/sources", headers=auth).status_code == 200
        with Session(app_module.db_sink.engine) as session:
            accounts_service.set_password(session, "alice", "newpass")  # 会话世代翻转
            session.commit()
        assert client.get("/api/reader/sources", headers=auth).status_code == 401

        token2 = _login(client, "alice", "newpass", return_token=True).json()["session_token"]
        client.cookies.clear()
        auth2 = {"Authorization": f"Bearer {token2}"}
        assert client.get("/api/reader/sources", headers=auth2).status_code == 200
        with Session(app_module.db_sink.engine) as session:
            accounts_service.set_active(session, "alice", False)
            session.commit()
        assert client.get("/api/reader/sources", headers=auth2).status_code == 401


# ---------- ② 渲染端点 ----------

def test_render_requires_login_and_sanitizes(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/reader/articles/a1/render").status_code == 401
        token = _login(client, "alice", "alice", return_token=True).json()["session_token"]
        client.cookies.clear()
        resp = client.get("/api/reader/articles/a1/render", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        html = body["html"]
        # 原始 HTML 被转义为文本,不成为标签
        assert "<script" not in html and "&lt;script&gt;" in html
        # 与标题重复的首行标题被剥离(页面自己画标题)
        assert "<h1" not in html
        # 图链改写为签名公开链,data: 图被丢弃
        assert "/api/public/media?u=https%3A%2F%2Fimg.example.com%2Fpic.png&amp;exp=" in html
        assert "data:image" not in html and _IMG not in html.replace("https%3A%2F%2Fimg.example.com%2Fpic.png", "")
        assert body["image_count"] == 1
        # 表格对齐 style 放行,代码块语言 class 放行
        assert 'style="text-align:right"' in html
        assert '<code class="language-python">' in html
        assert "<strong>强调</strong>" in html
        assert body["has_translation"] is False and body["translated_html"] is None
        assert body["is_chinese"] is True


def test_render_attaches_valid_translation_cache(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        body = client.get("/api/reader/articles/a3/render").json()
        assert body["has_translation"] is True
        assert "<strong>世界</strong>" in body["translated_html"]
        assert body["translated_title"] == "英文标题"
        assert body["is_chinese"] is False


def test_render_visibility_matches_detail(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import source_visibility as source_visibility_service

    with Session(app_module.db_sink.engine) as session:
        source_visibility_service.set_source_hidden(session, "src_hidden", True)
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/reader/articles/a2/render").status_code == 404
        assert client.get("/api/reader/articles/nope/render").status_code == 404
        client.cookies.clear()
        _login(client, "admin", "admin")
        assert client.get("/api/reader/articles/a2/render").status_code == 200


# ---------- ③ 签名公开图链 ----------

def test_public_signed_media(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from api.media_signing import sign_media_url

    signed = sign_media_url(_IMG)
    with TestClient(app_module.app) as client:
        # 免登录;媒体库停用 → 302 回源
        resp = client.get(signed, follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == _IMG
        # 篡改签名 / 篡改 URL / 缺参 → 404
        assert client.get(signed[:-1] + ("0" if signed[-1] != "0" else "1"), follow_redirects=False).status_code == 404
        assert client.get(signed.replace("pic.png", "other.png"), follow_redirects=False).status_code == 404
        assert client.get("/api/public/media?u=" + _IMG, follow_redirects=False).status_code == 404
        # 过期 → 404
        expired = sign_media_url(_IMG, ttl_seconds=-10)
        assert client.get(expired, follow_redirects=False).status_code == 404
        # 登录门控的 proxy 对访客仍 401(签名链没有放宽它)
        assert client.get("/api/media/proxy?url=" + _IMG, follow_redirects=False).status_code == 401


def test_media_signing_unit():
    from api.media_signing import sign_media_url, verify_media_signature
    from urllib.parse import parse_qs, urlparse

    assert sign_media_url("data:image/png;base64,AAAA") == ""
    assert sign_media_url("/relative.png") == ""
    signed = sign_media_url(_IMG, ttl_seconds=60, now=1_000_000)
    qs = parse_qs(urlparse(signed).query)
    u, exp, sig = qs["u"][0], qs["exp"][0], qs["sig"][0]
    assert u == _IMG and exp == "1000060"
    assert verify_media_signature(u, exp, sig, now=1_000_059)
    assert not verify_media_signature(u, exp, sig, now=1_000_061)
    assert not verify_media_signature(u, exp, sig.upper()[:-1] + "x")
    assert not verify_media_signature(u, "notint", sig)
    assert not verify_media_signature(u, exp, None)


# ---------- 渲染服务单元 ----------

def test_render_markdown_unit():
    from services.article_render import render_markdown, strip_duplicate_leading_heading

    mapper = lambda url: f"/m?u={url}" if url.startswith("https://") else ""  # noqa: E731

    html, images = render_markdown("line one\nline two\n\n3. third\n4. fourth", mapper)
    assert "line one<br />" in html or "line one<br>" in html  # breaks=True 镜像 remark-breaks
    assert '<ol start="3">' in html
    assert images == []

    html, images = render_markdown("<iframe src='x'></iframe> text ![a](https://h/i.png) ![b](http://h/j.png)", mapper)
    assert "<iframe" not in html and "&lt;iframe" in html
    assert '<img src="/m?u=https://h/i.png" alt="a" />' in html
    assert "j.png" not in html  # mapper 返回空 → 整图丢弃
    assert images == ["https://h/i.png"]

    html, _ = render_markdown("[x](javascript:alert(1)) [y](https://ok)", mapper)
    # 危险协议链接不成为 <a>(markdown-it 自身按文本输出),https 链接保留 href
    assert 'href="javascript' not in html and 'href="https://ok"' in html

    assert strip_duplicate_leading_heading("# Title\n\nbody", "Title") == "body"
    assert strip_duplicate_leading_heading("# Other\n\nbody", "Title") == "# Other\n\nbody"
    assert strip_duplicate_leading_heading("intro\n# Title", "Title") == "intro\n# Title"
    # 公式按原文保留(不解析)
    html, _ = render_markdown("Energy $E=mc^2$ here", mapper)
    assert "$E=mc^2$" in html
