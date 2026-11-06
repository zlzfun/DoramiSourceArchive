"""收录推送通道(issue #262 波 1)。"""

import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_NOW = "2026-11-06T00:00:00"


def _setup(monkeypatch, tmp_path, name="agent.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, (("admin", "admin", "admin"),))
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda ids: None)
    return app_module, sink


def _login(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200


def _issue_token(client, actor="research-agent", **extra):
    response = client.post("/api/admin/ingest/tokens", json={"actor": actor, **extra})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"].startswith("ding_")
    return body["token"]


def _push(client, token, items):
    return client.post(
        "/api/public/ingest/v1/items",
        json={"items": items},
        headers={"Authorization": f"Bearer {token}"},
    )


def _row(engine, model, key):
    with Session(engine) as session:
        return session.get(model, key)


def test_tokens_are_hashed_shown_once_and_revocable(monkeypatch, tmp_path):
    from models.db import IngestTokenRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        listed = client.get("/api/admin/ingest/tokens").json()["items"]
        assert listed and "token" not in listed[0] and listed[0]["token_preview"] == f"...{token[-6:]}"
        with Session(sink.engine) as session:
            stored = session.exec(select(IngestTokenRecord)).one()
            assert token not in stored.token_hash
        client.post("/api/auth/logout")
        assert client.post("/api/public/ingest/v1/items", json={"items": []}).status_code == 401
        assert _push(client, "ding_wrong", []).status_code == 401
        assert _push(client, token, []).status_code == 200
        _login(client)
        assert client.post(f"/api/admin/ingest/tokens/{listed[0]['id']}/revoke").status_code == 200
        assert _push(client, token, []).status_code == 401


def test_social_posts_resolve_presets_and_create_provisional_sources(monkeypatch, tmp_path):
    from models.db import ArticlePickRecord, ArticleRecord, SourceConfigRecord, SourceIdentityKeyRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        response = _push(client, token, [
            {"kind": "social_post", "post_id": "1", "content": "Kimi 新模型",
             "author": {"handle": "Kimi_Moonshot", "name": "Kimi"}},
            {"kind": "social_post", "post_id": "2", "content": "a new lab post",
             "author": {"handle": "new_lab", "user_id": "9001", "name": "New Lab",
                        "avatar_url": "https://img.test/a.jpg"}},
            # 同一账号只给 user_id:按身份键认回本批次刚建的临时源。
            {"kind": "social_post", "post_id": "3", "content": "by id",
             "author": {"user_id": "9001"}},
            {"kind": "social_post", "post_id": "4", "content": "no author"},
        ])
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        again = _push(client, token, [
            {"kind": "social_post", "post_id": "2", "author": {"handle": "new_lab"}},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["created", "created", "created", "rejected"]
    assert results[0]["source_id"] == "x_moonshot_ai" and not results[0]["source_created"]
    assert results[1]["source_id"] == "x_new_lab" and results[1]["source_created"]
    assert results[2]["source_id"] == "x_new_lab"
    assert again[0]["status"] == "duplicate"
    engine = sink.engine
    source = _row(engine, SourceConfigRecord, "x_new_lab")
    assert (source.origin, source.admission_status, source.name) == ("ingest", "provisional", "X · New Lab")
    assert (source.shape, source.platform, source.avatar_url) == ("social", "x", "https://img.test/a.jpg")
    assert source.fetcher_id == "" and source.legacy_export is False
    assert _row(engine, SourceIdentityKeyRecord, "x:user_id:9001").source_id == "x_new_lab"
    article = _row(engine, ArticleRecord, "x_new_lab_2")
    assert (article.ingest_channel, article.ingest_actor, article.content_type) == (
        "push", "research-agent", "social_post"
    )
    with Session(engine) as session:
        picks = session.exec(select(ArticlePickRecord)).all()
    assert {pick.article_id for pick in picks} == {"x_moonshot_ai_1", "x_new_lab_2", "x_new_lab_3"}


def test_articles_resolve_sites_dedupe_urls_and_strip_system_keys(monkeypatch, tmp_path):
    from api.articles_view import GenericContent
    from models.db import ArticlePickRecord, ArticleRecord, SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    existing = GenericContent(
        id="rss_simonwillison_x", title="Simon", source_url="https://simonwillison.net/2026/Nov/1/post/",
        publish_date=_NOW, content="body", has_content=True,
    )
    existing.source_id = "rss_simonwillison"
    existing.content_type = "rss_article"
    import asyncio

    assert asyncio.run(sink.save(existing)) is True
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            {"kind": "article", "url": "https://www.anthropic.com/news/claude-x", "title": "Claude X",
             "content": "正文", "note": "值得一读"},
            {"kind": "article", "url": "https://blog.example.com/p/1", "title": "Example",
             "source": {"name": "Example Blog"},
             "extensions": {"author": "A", "listing_source": "hn", "translation_zh_title": "伪造"}},
            {"kind": "article", "url": "https://blog.example.com/p/2", "title": "Example 2"},
            {"kind": "article", "url": "https://github.com/openai/codex/releases/tag/v1", "title": "rel"},
            # 同一链接已在别的源里:不新建,只记收录。
            {"kind": "article", "url": "https://simonwillison.net/2026/Nov/1/post", "title": "dup",
             "source": {"url": "https://other.example.org"}},
            {"kind": "article", "title": "no url"},
        ]).json()["results"]
    statuses = [(r["status"], r.get("source_id")) for r in results]
    assert statuses[0] == ("created", "web_anthropic_news")
    assert statuses[1] == ("created", "web_blog_example_com")
    assert statuses[2] == ("created", "web_blog_example_com")
    assert statuses[3] == ("created", "web_github_com_openai")
    assert statuses[4] == ("duplicate_url", "rss_simonwillison")
    assert results[4]["id"] == "rss_simonwillison_x"
    assert statuses[5][0] == "rejected"
    engine = sink.engine
    blog = _row(engine, SourceConfigRecord, "web_blog_example_com")
    assert (blog.name, blog.admission_status) == ("Example Blog", "provisional")
    pushed = _row(engine, ArticleRecord, results[1]["id"])
    extensions = json.loads(pushed.extensions_json)
    assert "listing_source" not in extensions and "translation_zh_title" not in extensions
    assert pushed.content_type == "web_article"
    with Session(engine) as session:
        assert session.exec(select(ArticlePickRecord).where(
            ArticlePickRecord.article_id == "rss_simonwillison_x"
        )).one().actor == "research-agent"


def test_podcast_episode_creates_a_provisional_podcast_source(monkeypatch, tmp_path):
    from models.db import ArticleRecord, SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [{
            "kind": "podcast_episode", "title": "EP1", "guid": "ep-1",
            "audio_url": "https://cdn.pod.test/ep1.mp3", "summary": "简介",
            "source": {"feed_url": "https://pod.test/feed.xml", "name": "Pod Show"},
        }]).json()["results"]
    assert results[0]["status"] == "created", results
    source = _row(sink.engine, SourceConfigRecord, results[0]["source_id"])
    assert (source.source_type, source.shape, source.admission_status) == ("podcast", "podcast", "provisional")
    assert source.url == "https://pod.test/feed.xml" and source.fetcher_id == ""
    episode = _row(sink.engine, ArticleRecord, results[0]["id"])
    assert episode.content_type == "podcast_episode"


def test_push_rejects_reserved_hidden_and_quota_and_receivers(monkeypatch, tmp_path):
    from services.source_visibility import set_source_hidden
    from services.sync_consumer_policy import activate_v2_consumer_mode

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, daily_limit=2)
        with Session(sink.engine) as session:
            set_source_hidden(session, "x_sama", True)
        results = _push(client, token, [
            {"kind": "article", "url": "https://a.test/1", "source": {"source_id": "dorami_daily_brief"}},
            {"kind": "social_post", "post_id": "1", "author": {"handle": "sama"}},
            {"kind": "article", "url": "https://a.test/2", "source": {"source_id": "nope_missing"}},
        ]).json()["results"]
        assert [r["status"] for r in results] == ["rejected"] * 3
        assert "下架" in results[1]["reason"]
        # 日额度按收录条数计(含去重命中);超出的逐条拒收。
        ok = _push(client, token, [
            {"kind": "article", "url": "https://q.test/1"},
            {"kind": "article", "url": "https://q.test/2"},
            {"kind": "article", "url": "https://q.test/3"},
        ]).json()["results"]
        assert [r["status"] for r in ok] == ["created", "created", "rejected"]
        with Session(sink.engine) as session:
            activate_v2_consumer_mode(session, reason="test")
        assert _push(client, token, []).status_code == 409


def test_admin_ingest_sources_list_admit_and_hide(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        _push(client, token, [{"kind": "article", "url": "https://news.example.net/a", "title": "A"}])
        items = client.get("/api/admin/ingest/sources").json()["items"]
        assert [(i["source_id"], i["count"], i["actors"]) for i in items] == [
            ("web_news_example_net", 1, ["research-agent"])
        ]
        assert client.post("/api/admin/ingest/sources/web_news_example_net/admit").json()[
            "admission_status"] == "incubating"
        assert client.post("/api/admin/ingest/sources/web_news_example_net/hide").status_code == 200
        assert client.patch("/api/admin/ingest/sources/web_news_example_net",
                            json={"name": "Example News"}).json()["name"] == "Example News"
        rejected = _push(client, token, [{"kind": "article", "url": "https://news.example.net/b"}])
        assert rejected.json()["results"][0]["status"] == "rejected"
        assert client.post("/api/admin/ingest/sources/rss_simonwillison/admit").status_code == 404
    row = _row(sink.engine, SourceConfigRecord, "web_news_example_net")
    assert (row.admission_status, row.name) == ("incubating", "Example News")
    from models.db import AdminAuditRecord

    with Session(sink.engine) as session:
        audits = session.exec(select(AdminAuditRecord).order_by(AdminAuditRecord.id)).all()
    pushes = [a for a in audits if a.path == "/api/public/ingest/v1/items"]
    assert pushes and pushes[0].username == "ingest:research-agent"
    assert "收录推送(research-agent)" in pushes[0].summary and "新建临时源 1 个" in pushes[0].summary
    summaries = {a.summary for a in audits}
    assert "签发收录令牌 research-agent" in summaries
    assert "列入收录源 web_news_example_net" in summaries
    assert "下架收录源 web_news_example_net" in summaries


# ── 临时源的下游策略(方案 §5.2) ─────────────────────────────────────────────


def test_provisional_sources_do_not_corroborate_breaking_events():
    from models.analysis_contracts import ContentGenre, DigestArticleCandidateDTO
    from services.digest_selection import select_breaking_events

    def candidate(article_id, source, score):
        return DigestArticleCandidateDTO(
            article_id=article_id, source_id=source, title=article_id,
            source_url=f"https://e.test/{article_id}", publish_date="2026-11-06T08:00:00+08:00",
            fetched_date="2026-11-06T08:30:00+08:00", quality_score=score, score_reason="r",
            content_genre=ContentGenre.INDUSTRY_NEWS, tag_codes=("entity.anthropic",),
            event_id=None, source_role="media", content_shape="article",
        )

    pair = [candidate("a1", "web_aiera", 9.2), candidate("a2", "web_pushed_blog", 8.7)]
    assert [s.article_id for s in select_breaking_events(pair)] == ["a1"]
    # 同一件事,另一家只是收录推送建的临时源:不构成「两家来源同时报道」。
    assert select_breaking_events(pair, uncorroborating_source_ids={"web_pushed_blog"}) == []


def test_provisional_sources_stay_out_of_the_daily_brief_pool_until_admitted(tmp_path):
    import asyncio

    from api.articles_view import GenericContent
    from models.db import SourceConfigRecord
    from services import daily_brief
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'brief.db'}")
    for article_id, source_id, hint in (
        ("vetted_1", "web_vetted", None),
        ("pushed_1", "web_pushed", {"origin": "ingest", "admission_status": "provisional"}),
    ):
        item = GenericContent(id=article_id, title=article_id, source_url=f"https://e.test/{article_id}",
                              publish_date=_NOW, content="body", has_content=True)
        item.source_id = source_id
        item.content_type = "web_article"
        if hint:
            item._source_hint = hint
        assert asyncio.run(sink.save(item)) is True
    with Session(sink.engine) as session:
        ids = {c.id for c in daily_brief.collect_candidates(session, cursor="")[0]}
        assert ids == {"vetted_1"}
        row = session.get(SourceConfigRecord, "web_pushed")
        row.admission_status = "incubating"
        session.add(row)
        session.commit()
        ids = {c.id for c in daily_brief.collect_candidates(session, cursor="")[0]}
    assert ids == {"vetted_1", "pushed_1"}


def test_provisional_podcast_episodes_never_enter_paid_full_processing(tmp_path):
    from models.db import ArticleAnalysisRecord, ArticleRecord, SourceConfigRecord
    from services import podcast_landing as landing
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'landing.db'}")
    with Session(sink.engine) as session:
        for source_id, admission in (("podcast_vetted", "incubating"), ("podcast_pushed", "provisional")):
            session.add(SourceConfigRecord(
                source_id=source_id, name=source_id, source_type="podcast", url=f"https://{source_id}.test/f",
                origin="ingest" if admission == "provisional" else "catalog",
                admission_status=admission, created_at=_NOW, updated_at=_NOW,
            ))
            episode_id = f"{source_id}_ep"
            session.add(ArticleRecord(
                id=episode_id, title=episode_id, content_type="podcast_episode", source_id=source_id,
                source_url=f"https://{source_id}.test/ep", publish_date=_NOW, fetched_date=_NOW,
                archive_updated_at=_NOW, has_content=True, content="notes",
                extensions_json=json.dumps({"audio_url": f"https://{source_id}.test/ep.mp3"}),
            ))
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=episode_id, status="succeeded", quality_score=8.5, score_reason="r",
                summary="s", content_hash="a" * 64, analysis_basis="podcast_show_notes",
                analysis_input_hash="b" * 64, prompt_version="p", scoring_version="s",
                analyzed_at=_NOW, created_at=_NOW, updated_at=_NOW,
            ))
        session.commit()
        ids, _cursor = landing.scan_new_candidates(session, landing.EMPTY_CURSOR)
    assert ids == ["podcast_vetted_ep"]


# ── 实现检视 R1 返修(#3–#6、#11、#12) ───────────────────────────────────────


def _seed_source(engine, source_id, **fields):
    from models.db import SourceConfigRecord

    with Session(engine) as session:
        session.add(SourceConfigRecord(source_id=source_id, name=fields.pop("name", source_id),
                                       created_at=_NOW, updated_at=_NOW, **fields))
        session.commit()


def _seed_row(engine, article_id, source_id, url, content_type="rss_article"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=article_id, content_type=content_type, source_id=source_id,
            source_url=url, publish_date=_NOW, fetched_date=_NOW, archive_updated_at=_NOW,
            has_content=True, content="x", extensions_json="{}",
        ))
        session.commit()


def test_review_fixes_identity_dedupe_and_payload(monkeypatch, tmp_path):
    from models.db import ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    engine = sink.engine
    # #3 私有条目不参与跨源去重
    _seed_source(engine, "user_rss_secret00000", source_type="rss", owner_username="alice", url="https://p.test/f")
    _seed_row(engine, "user_rss_secret00000_1", "user_rss_secret00000", "https://shared.example.com/post")
    # #4 feed 以 query 区分
    _seed_source(engine, "rss_alice_feed", source_type="rss", url="https://feeds.example.com/feed?source=alice",
                 fetcher_id="generic_rss", origin="admin")
    # #5 handle 被他人接手
    _seed_source(engine, "x_oldhandle", source_type="x_timeline", fetcher_id="generic_x_timeline", origin="admin",
                 params_json=json.dumps({"handle": "oldhandle", "user_id": "111"}))
    # #6 收养过的播客保留 entry_id_namespace;同源同链接
    _seed_source(engine, "podcast_adopted", source_type="podcast", url="https://pod.test/feed.xml",
                 fetcher_id="generic_podcast_rss", origin="catalog",
                 params_json=json.dumps({"entry_id_namespace": "user_rss_old000000"}))
    import hashlib

    adopted_id = "user_rss_old000000_" + hashlib.sha1(b"ep-1").hexdigest()[:16]
    _seed_row(engine, adopted_id, "podcast_adopted", "https://pod.test/ep1", content_type="podcast_episode")
    _seed_source(engine, "web_samesite", source_type="web", url="https://same.example.org/",
                 fetcher_id="generic_web", origin="admin", base_url="https://same.example.org")
    _seed_row(engine, "web_samesite_byguid", "web_samesite", "https://same.example.org/a")
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            {"kind": "article", "url": "https://shared.example.com/post", "title": "public copy"},
            {"kind": "article", "url": "https://x.test/b1", "source": {"feed_url": "https://feeds.example.com/feed?source=bob"}},
            {"kind": "social_post", "post_id": "9", "author": {"handle": "oldhandle", "user_id": "222"}},
            {"kind": "podcast_episode", "guid": "ep-1", "audio_url": "https://pod.test/ep1.mp3", "title": "EP1",
             "source": {"source_id": "podcast_adopted"}},
            {"kind": "article", "url": "https://same.example.org/a", "title": "same"},
            {"kind": "article", "url": "https://ext.example.net/e", "title": "ext",
             "extensions": {"author": "Ann", "language": "en", "image_url": "https://img/x.png"}},
            {"kind": "article", "url": "https://ext.example.net/bad", "extensions": {"tags": 7}},
            {"kind": "podcast_episode", "guid": "ep-9", "audio_url": "https://pod2.test/9.mp3", "title": "EP9",
             "source": {"feed_url": "https://pod2.test/feed.xml"}, "extensions": {"episode": 9, "season": 2}},
        ]).json()["results"]
    status = [(r["status"], r.get("source_id")) for r in results]
    assert status[0][0] == "created" and status[0][1] != "user_rss_secret00000"
    assert "user_rss_secret00000" not in json.dumps(results)
    assert status[1][0] == "created" and status[1][1] != "rss_alice_feed"
    assert status[2][0] == "created" and status[2][1] not in {"x_oldhandle"}
    # 同一集(id 前缀沿用 entry_id_namespace):走播客元数据刷新,不新建。
    assert status[3] in {("duplicate", "podcast_adopted"), ("refreshed", "podcast_adopted")}
    assert results[3]["id"] == adopted_id
    assert status[4] == ("duplicate", "web_samesite") and results[4]["id"] == "web_samesite_byguid"
    assert status[5][0] == "created"
    assert status[6][0] == "rejected" and "tags" in results[6]["reason"]
    assert status[7][0] == "created"
    with Session(engine) as session:
        ext = json.loads(session.get(ArticleRecord, results[5]["id"]).extensions_json)
        assert (ext["author"], ext["language"], ext["image_url"]) == ("Ann", "en", "https://img/x.png")
        pod = json.loads(session.get(ArticleRecord, results[7]["id"]).extensions_json)
        assert (pod["episode"], pod["season"]) == (9, 2)
        assert session.exec(select(ArticleRecord).where(ArticleRecord.source_id == "podcast_adopted")).all()[0].id == adopted_id


def test_rereview_fixes_batch_identity_podcast_ids_and_numbers(monkeypatch, tmp_path):
    """检视复检:批内同 handle 不同 user_id、播客与抓取器同公式、同节目页不并单集、溢出数值。"""
    import hashlib

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            {"kind": "social_post", "post_id": "1", "author": {"handle": "sharedname", "user_id": "222"}},
            {"kind": "social_post", "post_id": "2", "author": {"handle": "sharedname", "user_id": "333"}},
            {"kind": "podcast_episode", "guid": "g-1", "url": "https://show.example.com/",
             "audio_url": "https://cdn.show.example.com/1.mp3", "source": {"feed_url": "https://show.example.com/feed"}},
            {"kind": "podcast_episode", "guid": "g-2", "url": "https://show.example.com/",
             "audio_url": "https://cdn.show.example.com/2.mp3", "source": {"feed_url": "https://show.example.com/feed"}},
            {"kind": "podcast_episode", "audio_url": "https://cdn.show.example.com/3.mp3",
             "source": {"feed_url": "https://show.example.com/feed"}},
        ]).json()["results"]
        # 合法 JSON 数值 1e309 解析成 inf:单条拒收,整批照常回执(TestClient 的编码器发不出 inf,直接发原文)。
        overflow = client.post(
            "/api/public/ingest/v1/items",
            content='{"items":[{"kind":"podcast_episode","guid":"g-9","audio_url":"https://c/9.mp3",'
                    '"source":{"feed_url":"https://show.example.com/feed"},"extensions":{"episode":1e309}},'
                    '{"kind":"article","url":"https://n.example.com/ok"}]}',
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert overflow.status_code == 200
        assert [r["status"] for r in overflow.json()["results"]] == ["rejected", "created"]
    s = [(r["status"], r.get("source_id")) for r in results]
    assert s[0][0] == s[1][0] == "created" and s[0][1] != s[1][1]
    assert s[2][0] == s[3][0] == "created" and results[2]["id"] != results[3]["id"]
    # 与 podcast_rss_fetcher._entry_id 同公式:guid 优先。
    assert results[2]["id"] == f"{s[2][1]}_" + hashlib.sha1(b"g-1").hexdigest()[:16]
    assert s[4][0] == "rejected" and "guid" in results[4]["reason"]


def test_ready_is_revoked_when_a_later_sources_run_is_legacy(tmp_path):
    from services import archive_sync_v2, remote_sync
    from storage.impl.db_storage import DatabaseStorage

    consumer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'revoke.db'}")
    base_url = "https://authority.test"
    caps = [*archive_sync_v2.REQUIRED_CAPABILITIES, archive_sync_v2.SOURCE_CATALOG_CAPABILITY]
    with Session(consumer.engine) as session:
        remote_sync.prepare_transaction_revision_consumer(
            session, base_url=base_url, username="sync", authority_id="authority-1",
            schema_version=archive_sync_v2.SCHEMA_VERSION, prepared_at=_NOW, capabilities=caps,
        )
        session.commit()
    for projection, expected in (("catalog-v1", True), ("legacy", False)):
        remote_sync.record_v2_stream_success(
            consumer.engine, base_url=base_url, username="sync", stream="sources",
            checkpoint={"snapshot": "9", "authority_id": "authority-1", "source_projection": projection},
            synced_at=_NOW,
        )
        assert remote_sync.source_catalog_ready(consumer.engine) is expected


def _fetched(sid, item_id, url, *, content="抓取全文", content_type="web_article", title="抓取标题"):
    """模拟抓取器入库的条目(条目 id 用抓取器自己的公式,与推送的不同)。"""
    from api.articles_view import GenericContent

    item = GenericContent(
        id=item_id, title=title, source_url=url, publish_date=_NOW,
        content=content, has_content=bool(content),
    )
    item.source_id = sid
    item.content_type = content_type
    return item


def test_push_then_fetch_merges_into_one_row_only_for_url_identity_sources(monkeypatch, tmp_path):
    """先推后抓合流(补检 A):抓取器条目 id 公式不同(RSS guid ≠ link、列表页另算哈希)时,
    同源同链接的收录条目就是同一条;单页文档 / 共用链接的源不合流,普通抓取条目之间行为不变。"""
    import asyncio

    from models.db import ArticleRecord
    from storage.impl import db_storage

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            # 列表页源,推送只带摘要:抓取到全文后补进同一行。
            {"kind": "article", "url": "https://www.anthropic.com/news/claude-y", "title": "Claude Y",
             "summary": "推送摘要"},
            # RSS 源,feed 的 guid 是 ?p=123 一类、推送方没带 guid。
            {"kind": "article", "url": "https://anildash.com/2026/11/06/post", "title": "Anil",
             "content": "推送正文", "source": {"source_id": "rss_anil_dash"}},
            # 单页文档(changelog):多个条目共用页面链接,不能按链接合流。
            {"kind": "article", "url": "https://code.claude.com/docs/en/changelog", "title": "整页",
             "content": "推送正文", "source": {"source_id": "docs_claude_code_changelog"}},
            {"kind": "article", "url": "https://seed.bytedance.com/en/research", "title": "整页",
             "content": "推送正文", "source": {"source_id": "web_bytedance_seed_research"}},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["created"] * 4, results
    pushed_news, pushed_rss = results[0]["id"], results[1]["id"]

    def save(item):
        return asyncio.run(sink.save_with_outcome(item))

    news = _fetched("web_anthropic_news", "web_anthropic_news_fetcherhash", "https://www.anthropic.com/news/claude-y/")
    assert save(news) == db_storage.SAVE_CONTENT_FILLED
    assert news.id == pushed_news  # 管线后续分析 / 媒体预取挂到已有行
    filled = _row(sink.engine, ArticleRecord, pushed_news)
    assert filled.has_content and filled.content == "抓取全文" and filled.ingest_channel == "push"
    rss = _fetched("rss_anil_dash", "rss_anil_dash_guidhash", "https://anildash.com/2026/11/06/post",
                   content_type="rss_article")
    assert save(rss) == db_storage.SAVE_DUPLICATE
    assert _row(sink.engine, ArticleRecord, "rss_anil_dash_guidhash") is None
    # 共用页面链接的源:带锚点与不带锚点的抓取条目都照常新建。
    for item in (
        _fetched("docs_claude_code_changelog", "docs_claude_code_changelog_a", "https://code.claude.com/docs/en/changelog#2-1-0"),
        _fetched("docs_claude_code_changelog", "docs_claude_code_changelog_b", "https://code.claude.com/docs/en/changelog"),
        _fetched("web_bytedance_seed_research", "web_bytedance_seed_research_a", "https://seed.bytedance.com/en/research"),
    ):
        assert save(item) == db_storage.SAVE_CREATED, item.id
    # 普通抓取条目之间不按链接合流(行为不变)。
    for item_id in ("rss_anil_dash_one", "rss_anil_dash_two"):
        assert save(_fetched("rss_anil_dash", item_id, "https://anildash.com/shared", content_type="rss_article")) \
            == db_storage.SAVE_CREATED
    with Session(sink.engine) as session:
        rows = session.exec(select(ArticleRecord).where(
            ArticleRecord.source_url.in_([
                "https://www.anthropic.com/news/claude-y", "https://www.anthropic.com/news/claude-y/",
                "https://anildash.com/2026/11/06/post",
            ])
        )).all()
    assert sorted(row.id for row in rows) == sorted([pushed_news, pushed_rss])


def test_url_identity_guards_shared_links_fragments_and_podcasts(tmp_path):
    from sqlmodel import SQLModel, create_engine

    from models.db import ArticleRecord
    from services import item_url_identity

    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for item_id, url, channel in (
            ("rss_x_pushed", "https://example.com/a", "push"),
            ("rss_x_fetched", "https://example.com/a", ""),  # 同源已有同链接的抓取条目
            ("rss_y_pushed", "https://example.com/b#part", "manual"),
        ):
            session.add(ArticleRecord(
                id=item_id, title="t", content_type="rss_article", source_id=item_id.rsplit("_", 1)[0],
                source_url=url, publish_date=_NOW, fetched_date=_NOW, ingest_channel=channel,
            ))
        session.commit()
        # 同源里已有同链接的抓取条目 → 这个源共用链接,不合流。
        assert item_url_identity.ingested_row_for(session, "rss_x", "https://example.com/a", "rss_article") is None
        # 带锚点只认原样。
        assert item_url_identity.ingested_row_for(session, "rss_y", "https://example.com/b", "rss_article") is None
        assert item_url_identity.ingested_row_for(
            session, "rss_y", "https://example.com/b#part", "rss_article"
        ).id == "rss_y_pushed"
        assert not item_url_identity.url_is_item_identity(session, "podcast_any", "podcast_episode")
        assert not item_url_identity.url_is_item_identity(session, "github_openclaw_releases", "github_release")


def test_push_kind_must_match_target_source_shape(monkeypatch, tmp_path):
    """推送类型与目标源形态(补检 B):显式 id 形态不符拒收;前缀 / feed 匹配跳过形态不同的源。"""
    from models.db import SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            {"kind": "article", "url": "https://openai.com/index/x", "title": "文章",
             "source": {"source_id": "x_openai"}},
            {"kind": "social_post", "post_id": "1990000000000000101", "content": "帖子",
             "author": {"handle": "someone"}, "source": {"source_id": "web_anthropic_news"}},
            {"kind": "podcast_episode", "title": "EP", "guid": "ep-x", "audio_url": "https://cdn.t/ep.mp3",
             "source": {"source_id": "rss_anil_dash"}},
            # x.com 账号下的链接以文章推送:不落进该账号的社交源。
            {"kind": "article", "url": "https://x.com/OpenAI/status/1990000000000000102", "title": "转成文章"},
            # 播客单集给了文章 RSS 的 feed:不落进 RSS 源,另建临时播客源。
            {"kind": "podcast_episode", "title": "EP2", "guid": "ep-2", "audio_url": "https://cdn.t/ep2.mp3",
             "source": {"feed_url": "https://anildash.com/feed.xml", "name": "误给的 feed"}},
            # 形态一致照常命中。
            {"kind": "social_post", "post_id": "1990000000000000103", "content": "官方帖",
             "author": {"handle": "OpenAI"}},
        ]).json()["results"]
    assert [r["status"] for r in results[:3]] == ["rejected"] * 3, results
    assert results[0]["reason"] == "该来源是社交源,不收文章"
    assert results[1]["reason"] == "该来源是文章源,不收社交帖"
    assert results[2]["reason"] == "该来源是文章源,不收播客单集"
    assert results[3]["status"] == "created" and results[3]["source_id"] != "x_openai"
    assert _row(sink.engine, SourceConfigRecord, results[3]["source_id"]).shape == "article"
    assert results[4]["status"] == "created" and results[4]["source_id"].startswith("podcast_")
    assert (results[5]["status"], results[5]["source_id"]) == ("created", "x_openai")


def test_article_links_fall_back_to_the_only_curated_source_on_the_host(monkeypatch, tmp_path):
    """主机兜底(沙箱端到端发现):策展源登记的是分类页 / feed 地址时,文章链接落不进前缀;
    该主机上恰好一个同形态源就认它,不另建临时源。有歧义、单页文档类、多租户主机不兜底。"""
    from models.db import SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        for sid, home in (("rss_twin_a", "https://twin.example/a"), ("rss_twin_b", "https://twin.example/b")):
            session.add(SourceConfigRecord(
                source_id=sid, name=sid, source_type="rss", url=f"{home}/feed.xml",
                homepage_url=home, origin="admin", admission_status="admitted",
                created_at=_NOW, updated_at=_NOW,
            ))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        results = _push(client, token, [
            # rss_openai_news 登记的是 openai.com/news/rss.xml,文章在 /index/…
            {"kind": "article", "url": "https://openai.com/index/some-new-post/", "title": "OpenAI 新文"},
            # 主机上只有 Codex changelog(单页文档类):不兜底,另建临时源。
            {"kind": "article", "url": "https://developers.openai.com/api/docs/guides/x", "title": "文档页"},
            # 同主机两个源、前缀都不中:有歧义,另建临时源;前缀命中的照常。
            {"kind": "article", "url": "https://twin.example/c/post", "title": "歧义"},
            {"kind": "article", "url": "https://twin.example/a/post", "title": "前缀"},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["created"] * 4, results
    assert [r["source_id"] for r in results] == [
        "rss_openai_news", "web_developers_openai_com", "web_twin_example", "rss_twin_a",
    ]
    assert [r["source_created"] for r in results] == [False, True, True, False]
    assert _row(sink.engine, SourceConfigRecord, "web_developers_openai_com").admission_status == "provisional"
