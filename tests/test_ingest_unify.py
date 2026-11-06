"""收录入口统一与同源内容判重(issue #262 叠分支波)。"""

import asyncio
import os
import random
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_NOW = "2026-11-06T00:00:00"
def _text(seed: int, chars: int = 900) -> str:
    """接近真实文章的正文:字符足够多样(常用汉字区随机取),每 40 字一个句号。"""
    rng = random.Random(seed)
    return "".join(
        chr(rng.randint(0x4E00, 0x6FFF)) + ("。" if i % 40 == 39 else "") for i in range(chars)
    )


def _setup(monkeypatch, tmp_path, name="unify.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, (("admin", "admin", "admin"), ("reader", "reader", "user")))
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda ids: None)
    return app_module, sink


def _login(client, username="admin"):
    assert client.post("/api/auth/login", json={"username": username, "password": username}).status_code == 200


def _issue_token(client, actor="research-agent"):
    response = client.post("/api/admin/ingest/tokens", json={"actor": actor})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _push(client, items, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/public/ingest/v1/items", json={"items": items}, headers=headers)


def _fetched(sink, sid, item_id, url, text, content_type="rss_article"):
    """抓取器入库的条目(没有收录来路)。"""
    from api.articles_view import GenericContent

    item = GenericContent(id=item_id, title=item_id, source_url=url, publish_date=_NOW, content=text, has_content=True)
    item.source_id = sid
    item.content_type = content_type
    assert asyncio.run(sink.save(item)) is True


def test_same_source_content_dedupe_only_on_push_and_only_within_one_source(monkeypatch, tmp_path):
    from models.db import ArticleFingerprintRecord, ArticlePickRecord, ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    body = _text(1)
    _fetched(sink, "rss_anil_dash", "rss_anil_dash_a", "https://anildash.com/2026/a", body)
    _fetched(sink, "rss_anil_dash", "rss_anil_dash_short", "https://anildash.com/2026/s", "短文")
    _fetched(sink, "docs_claude_code_changelog", "docs_claude_code_changelog_a",
             "https://code.claude.com/docs/en/changelog#1", body, content_type="web_article")
    # 抓取路径不动:同源再抓到一篇同正文、不同链接的条目照样入库。
    _fetched(sink, "rss_anil_dash", "rss_anil_dash_repost", "https://anildash.com/2026/repost", body)
    with Session(sink.engine) as session:
        assert session.exec(select(ArticleFingerprintRecord)).all() == []  # 入库不算指纹

    near = body[:200] + "补一句" + body[200:]  # 小改动:SimHash 距离 ≤ 3、Jaccard ≥ 0.8
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, [
            # 同源换链接、正文相同 → duplicate_content,只记收录。
            {"kind": "article", "url": "https://anildash.com/mirror/a", "title": "换链接",
             "content": body, "source": {"source_id": "rss_anil_dash"}, "note": "同一篇"},
            # 同源近似正文 → duplicate_content。
            {"kind": "article", "url": "https://anildash.com/mirror/b", "title": "近似",
             "content": near, "source": {"source_id": "rss_anil_dash"}},
            # 跨源同正文:各自保留。
            {"kind": "article", "url": "https://simonwillison.net/2026/x", "title": "跨源",
             "content": body, "source": {"source_id": "rss_simonwillison"}},
            # 新建的临时源不比。
            {"kind": "article", "url": "https://blog.newsite.dev/p/1", "title": "新源", "content": body},
            # 共用页面链接的源(单页文档类)不比。
            {"kind": "article", "url": "https://code.claude.com/docs/en/changelog#2", "title": "排除源",
             "content": body, "source": {"source_id": "docs_claude_code_changelog"}},
            # 短文本不比。
            {"kind": "article", "url": "https://anildash.com/mirror/s", "title": "短",
             "content": "短文", "source": {"source_id": "rss_anil_dash"}},
            # 不同正文照常新建。
            {"kind": "article", "url": "https://anildash.com/2026/other", "title": "另一篇",
             "content": _text(2), "source": {"source_id": "rss_anil_dash"}},
        ], token).json()["results"]
    statuses = [(r["status"], r.get("source_id")) for r in results]
    assert statuses[0] == ("duplicate_content", "rss_anil_dash")
    assert results[0]["id"] in {"rss_anil_dash_a", "rss_anil_dash_repost"}
    assert statuses[1] == ("duplicate_content", "rss_anil_dash")
    assert statuses[2] == ("created", "rss_simonwillison")
    assert statuses[3][0] == "created" and results[3].get("source_created") is True
    assert statuses[4] == ("created", "docs_claude_code_changelog")
    assert statuses[5] == ("created", "rss_anil_dash")
    assert statuses[6] == ("created", "rss_anil_dash")
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, results[0]["id"]) is not None
        assert session.exec(select(ArticleRecord).where(
            ArticleRecord.source_url == "https://anildash.com/mirror/a"
        )).first() is None
        pick = session.exec(select(ArticlePickRecord).where(ArticlePickRecord.note == "同一篇")).one()
        assert pick.article_id == results[0]["id"]
        # 现场补指纹:只补被推送过的源,短正文也落一行。
        fingerprints = session.exec(select(ArticleFingerprintRecord)).all()
        assert {fp.source_id for fp in fingerprints} == {"rss_anil_dash"}
        short = session.get(ArticleFingerprintRecord, "rss_anil_dash_short")
        assert short is not None and short.content_hash == "" and short.length < 300


def test_social_and_podcast_items_are_never_compared_by_content(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    body = _text(3)
    _fetched(sink, "x_openai", "x_openai_1990000000000000001", "https://x.com/OpenAI/status/1990000000000000001",
             body, content_type="social_post")
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, [
            {"kind": "social_post", "post_id": "1990000000000000002", "content": body,
             "author": {"handle": "OpenAI"}},
        ], token).json()["results"]
    assert (results[0]["status"], results[0]["source_id"]) == ("created", "x_openai")


def test_simhash_candidates_are_rechecked_with_jaccard(monkeypatch, tmp_path):
    """近似候选必须过 5-gram Jaccard 复核:SimHash 全部撞上(距离 0)时,不同正文照常新建。"""
    from services import content_fingerprint

    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(content_fingerprint, "simhash", lambda normalized: 0)
    _fetched(sink, "rss_anil_dash", "rss_anil_dash_a", "https://anildash.com/2026/a", _text(4))
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, [
            {"kind": "article", "url": "https://anildash.com/2026/b", "title": "不同",
             "content": _text(5), "source": {"source_id": "rss_anil_dash"}},
        ], token).json()["results"]
    assert results[0]["status"] == "created"


def test_fingerprints_follow_content_changes(tmp_path):
    """条目补正文后版本号变了,下次推送前重算指纹。"""
    from sqlmodel import SQLModel, create_engine

    from models.db import ArticleFingerprintRecord, ArticleRecord
    from services import content_fingerprint

    engine = create_engine(f"sqlite:///{tmp_path / 'fp.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ArticleRecord(
            id="rss_x_1", title="t", content_type="rss_article", source_id="rss_x", source_url="https://x.test/1",
            publish_date=_NOW, fetched_date=_NOW, archive_updated_at="r1", has_content=True, content="摘要",
        ))
        session.commit()
        assert content_fingerprint.sync_source(session, "rss_x") == 1
        assert content_fingerprint.sync_source(session, "rss_x") == 0
        row = session.get(ArticleRecord, "rss_x_1")
        row.content, row.archive_updated_at = _text(6), "r2"
        session.add(row)
        session.commit()
        assert content_fingerprint.sync_source(session, "rss_x") == 1
        fingerprint = session.get(ArticleFingerprintRecord, "rss_x_1")
        assert fingerprint.revision == "r2" and fingerprint.content_hash and fingerprint.length >= 300


def test_admin_session_uses_the_same_entry_as_manual_channel(monkeypatch, tmp_path):
    """单条收录:管理员会话直接调通用端点,通道 manual、收录者为本人;读者 / 未登录拒绝;令牌优先。"""
    from models.db import AdminAuditRecord, ArticlePickRecord, ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    item = {"kind": "article", "url": "https://blog.example.com/p/manual", "title": "手工一篇", "content": "正文"}
    with TestClient(app_module.app) as client:
        assert _push(client, [item]).status_code == 401
        _login(client, "reader")
        assert _push(client, [item]).status_code == 403
        client.post("/api/auth/logout")
        _login(client)
        assert _push(client, [item], token="ding_wrong").status_code == 401  # 带了令牌就只认令牌
        response = _push(client, [item])
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert (result["status"], result["source_id"], result["source_created"]) == (
        "created", "web_blog_example_com", True,
    )
    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, result["id"])
        assert (row.ingest_channel, row.ingest_actor, row.content_type) == ("manual", "admin", "web_article")
        pick = session.exec(select(ArticlePickRecord).where(ArticlePickRecord.article_id == row.id)).one()
        assert (pick.actor, pick.channel) == ("admin", "manual")
        audit = session.exec(select(AdminAuditRecord).where(
            AdminAuditRecord.path == "/api/public/ingest/v1/items"
        )).one()
        assert audit.username == "admin" and audit.summary.startswith("单条收录(admin)")
