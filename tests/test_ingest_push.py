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
