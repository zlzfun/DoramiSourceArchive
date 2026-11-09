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


# ── 检视 R1 修复回归 ─────────────────────────────────────────────────────────


def test_concurrent_first_sync_of_one_source_does_not_collide(monkeypatch, tmp_path):
    """两个请求同时首次补齐同一个源:原子 UPSERT,不撞主键(R1 #1)。

    屏障把交错固定下来:两个线程都读完「已有指纹」、开始算第一条之后才继续写入。
    """
    import threading

    from sqlmodel import SQLModel, create_engine

    from models.db import ArticleFingerprintRecord, ArticleRecord
    from services import content_fingerprint

    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 30})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for i in range(200):
            session.add(ArticleRecord(
                id=f"rss_race_{i}", title="t", content_type="rss_article", source_id="rss_race",
                source_url=f"https://race.test/{i}", publish_date=_NOW, fetched_date=_NOW,
                archive_updated_at="r1", has_content=True, content=_text(100 + i, chars=400),
            ))
        session.commit()
    barrier, errors = threading.Barrier(2), []
    first_compute = threading.Barrier(2, timeout=10)
    seen = set()
    original = content_fingerprint._fingerprint_values

    def synchronized(text):
        me = threading.get_ident()
        if me not in seen:
            seen.add(me)
            try:
                first_compute.wait()
            except threading.BrokenBarrierError:
                pass
        return original(text)

    monkeypatch.setattr(content_fingerprint, "_fingerprint_values", synchronized)

    def worker():
        try:
            with Session(engine) as session:
                barrier.wait()
                content_fingerprint.sync_source(session, "rss_race")
        except Exception as exc:  # noqa: BLE001 - 收集给断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with Session(engine) as session:
        assert len(session.exec(select(ArticleFingerprintRecord)).all()) == 200


def test_custom_source_bound_to_a_github_fetcher_is_not_compared(monkeypatch, tmp_path):
    """按目标源的真实类型判资格:自定源绑 GitHub releases 抓取器,同正文不同链接不判重(R1 #2)。"""
    from models.db import SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="custom_release", name="custom", source_type="github_releases",
            fetcher_id="generic_github_releases", url="https://github.com/acme/tool",
            origin="admin", admission_status="admitted", created_at=_NOW, updated_at=_NOW,
        ))
        session.commit()
    body = _text(7)
    _fetched(sink, "custom_release", "custom_release_preview", "https://github.com/acme/tool/releases/tag/preview",
             body, content_type="github_release")
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, [
            {"kind": "article", "url": "https://github.com/acme/tool/releases/tag/nightly", "title": "nightly",
             "content": body, "source": {"source_id": "custom_release"}},
        ], token).json()["results"]
    assert results[0]["status"] == "created", results


def test_reader_runtime_nodes_refuse_ingest_for_sessions_and_tokens(monkeypatch, tmp_path):
    """采集面关闭的节点(runtime role = reader)不接受收录(R1 #3)。"""
    from config import RuntimeConfig

    app_module, _sink = _setup(monkeypatch, tmp_path)
    item = {"kind": "article", "url": "https://blog.example.com/p/r", "title": "t", "content": "x"}
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        monkeypatch.setattr(
            app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="reader"))
        )
        assert _push(client, [item]).status_code == 403
        assert _push(client, [item], token).status_code == 403


def test_orphan_fingerprints_do_not_hide_a_live_duplicate(monkeypatch, tmp_path):
    """删掉的条目留下的指纹不挤掉仍存在的同正文条目(R1 #4)。"""
    from models.db import ArticleFingerprintRecord, ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    body = _text(8)
    for i in range(11):
        _fetched(sink, "rss_anil_dash", f"rss_anil_dash_a{i:02d}", f"https://anildash.com/2026/a{i}", body)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        first = _push(client, [
            {"kind": "article", "url": "https://anildash.com/mirror/x", "title": "x", "content": body,
             "source": {"source_id": "rss_anil_dash"}},
        ], token).json()["results"][0]
        assert first["status"] == "duplicate_content"
        with Session(sink.engine) as session:
            for i in range(10):
                session.delete(session.get(ArticleRecord, f"rss_anil_dash_a{i:02d}"))
            session.commit()
        again = _push(client, [
            {"kind": "article", "url": "https://anildash.com/mirror/y", "title": "y", "content": body,
             "source": {"source_id": "rss_anil_dash"}},
        ], token).json()["results"][0]
    assert (again["status"], again["id"]) == ("duplicate_content", "rss_anil_dash_a10")
    with Session(sink.engine) as session:
        assert {fp.article_id for fp in session.exec(select(ArticleFingerprintRecord)).all()} == {"rss_anil_dash_a10"}


def test_manual_ingest_does_not_spend_a_same_named_token_quota(monkeypatch, tmp_path):
    """管理员手工收录不占同名令牌的额度(R1 #5)。"""
    app_module, _sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/admin/ingest/tokens", json={"actor": "admin", "daily_limit": 1})
        token = response.json()["token"]
        manual = _push(client, [{"kind": "article", "url": "https://blog.example.com/p/m", "title": "m", "content": "x"}])
        assert manual.json()["results"][0]["status"] == "created"
        pushed = _push(client, [{"kind": "article", "url": "https://blog.example.com/p/t", "title": "t", "content": "x"}], token)
    assert pushed.json()["results"][0]["status"] == "created", pushed.json()


def test_ledger_exposes_provenance_as_filter_facet_and_fields(monkeypatch, tmp_path):
    """台账来路(目检后补):列表 / 分面按 ingest_channel 区分抓取 / 推送 / 手工;收录者只对管理员透出。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    _fetched(sink, "rss_anil_dash", "rss_anil_dash_prov1", "https://anildash.com/p/1", _text(1))
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, actor="research-agent")
        pushed = _push(client, [
            {"kind": "article", "url": "https://blog.example.com/p/pushed", "title": "推", "content": "正文"},
        ], token).json()["results"][0]
        manual = _push(client, [
            {"kind": "article", "url": "https://blog.example.com/p/manual", "title": "手", "content": "正文"},
        ]).json()["results"][0]
        assert (pushed["status"], manual["status"]) == ("created", "created"), (pushed, manual)
        facets = client.get("/api/articles/facets").json()
        channels = {row["value"]: (row["count"], row["label"]) for row in facets["ingest_channels"]}
        assert channels == {"fetch": (1, "抓取"), "push": (1, "推送"), "manual": (1, "手工")}
        rows = client.get("/api/articles", params={"ingest_channel": "push", "include_content": "false"}).json()
        assert [(r["id"], r["ingest_channel"], r["ingest_channel_label"], r["ingest_actor"]) for r in rows] == [
            (pushed["id"], "push", "推送", "research-agent"),
        ]
        rows = client.get("/api/articles", params={"ingest_channel": "fetch", "include_content": "false"}).json()
        assert [(r["id"], r["ingest_channel"], r["ingest_channel_label"], r["ingest_actor"]) for r in rows] == [
            ("rss_anil_dash_prov1", "", "抓取", ""),
        ]
        client.post("/api/auth/logout")
        _login(client, "reader")
        rows = client.get("/api/articles", params={"ingest_channel": "manual", "include_content": "false"}).json()
        assert [r["id"] for r in rows] == [manual["id"]]
        # 读者看得到通道,看不到收录者是谁;单条详情同样不带收录者(检视 R2 建议补)。
        assert rows[0]["ingest_channel"] == "manual" and "ingest_actor" not in rows[0]
        detail = client.get(f"/api/articles/{manual['id']}").json()
        assert detail["ingest_channel"] == "manual" and "ingest_actor" not in detail
        # 未知来路值不报错、不漏放:空结果。
        assert client.get("/api/articles", params={"ingest_channel": "foo", "include_content": "false"}).json() == []
