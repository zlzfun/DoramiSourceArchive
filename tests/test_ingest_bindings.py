"""推送绑定(issue #344):令牌只能往它绑定的源推,未登记的出处拒收;登记不带抓取的源。"""

import os
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_ingest_push import (  # noqa: E402
    _bind, _collect, _issue_token, _login, _push, _register, _row, _setup, _token_id,
)


def _post(handle, post_id, text="hello", **extra):
    return {"kind": "social_post", "post_id": str(post_id), "content": text,
            "author": {"handle": handle, "name": handle.title()}, **extra}


def _source_ids(engine):
    from models.db import SourceConfigRecord

    with Session(engine) as session:
        return set(session.exec(select(SourceConfigRecord.source_id)).all())


def test_a_token_without_bindings_stores_nothing_and_creates_no_source(monkeypatch, tmp_path):
    from models.db import ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        before = _source_ids(sink.engine)
        results = _push(client, token, [
            _post("new_lab", 1),
            _post("OpenAI", 2),  # 认得出的已有源也不行:没有绑定
            {"kind": "article", "url": "https://blog.example.com/p/1", "title": "t", "content": "x"},
            {"kind": "podcast_episode", "guid": "g", "audio_url": "https://cdn.pod.test/1.mp3",
             "source": {"feed_url": "https://pod.test/feed.xml"}},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["rejected"] * 4
    assert all("还没有绑定来源" in r["reason"] for r in results)
    assert _source_ids(sink.engine) == before
    with Session(sink.engine) as session:
        assert session.exec(select(ArticleRecord)).all() == []


def test_explicit_source_id_must_be_one_of_the_tokens_bindings(monkeypatch, tmp_path):
    from models.db import ArticleRecord, IngestBindingRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _bind(client, token, "x_openai")
        results = _push(client, token, [
            _post("OpenAI", 11, source={"source_id": "x_openai"}),
            _post("sama", 12, source={"source_id": "x_sama"}),          # 存在,但没绑给这个令牌
            _post("OpenAI", 13, source={"source_id": "nope_missing"}),
        ]).json()["results"]
        listed = client.get("/api/admin/ingest/tokens").json()["items"][0]
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("created", "x_openai"), ("rejected", None), ("rejected", None),
    ]
    assert "没有绑定来源 x_sama" in results[1]["reason"] and "nope_missing" in results[2]["reason"]
    assert results[0]["source_created"] is False
    row = _row(sink.engine, ArticleRecord, "x_openai_11")
    assert (row.ingest_channel, row.ingest_actor) == ("push", "research-agent")
    # 「最近一批」记在绑定行上,令牌列表带出绑定。
    with Session(sink.engine) as session:
        binding = session.exec(select(IngestBindingRecord)).one()
    assert (binding.token_id, binding.source_id) == (token_id, "x_openai") and binding.last_pushed_at
    assert [(b["source_id"], b["open"], bool(b["last_pushed_at"])) for b in listed["bindings"]] == [
        ("x_openai", False, True)
    ]


def test_items_without_source_id_are_matched_only_among_the_bindings(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        # 单条收录先留下两个临时源:一个之后绑给令牌,一个不绑。
        made = _collect(client, [
            _post("new_lab", 1, author={"handle": "new_lab", "user_id": "9001", "name": "New Lab"}),
            _post("other_lab", 2),
            {"kind": "article", "url": "https://blog.example.com/p/0", "title": "t", "content": "x"},
        ]).json()["results"]
        assert [r["source_id"] for r in made] == ["x_new_lab", "x_other_lab", "web_blog_example_com"]
        token = _issue_token(client)
        _bind(client, token, "x_new_lab", "x_openai", "web_blog_example_com", "web_anthropic_news")
        before = _source_ids(sink.engine)
        results = _push(client, token, [
            _post("new_lab", 21),                                   # 按账号名认回绑定的临时源
            {"kind": "social_post", "post_id": "22", "content": "by id", "author": {"user_id": "9001"}},
            _post("OpenAI", 23),                                    # 预设源
            _post("other_lab", 24),                                 # 认得出,但没绑
            _post("brand_new", 25),                                 # 认不出:不建源
            {"kind": "article", "url": "https://blog.example.com/p/1", "title": "a", "content": "x"},
            {"kind": "article", "url": "https://www.anthropic.com/news/claude-z", "title": "b", "content": "x"},
            {"kind": "article", "url": "https://elsewhere.example.org/p/1", "title": "c", "content": "x"},
        ]).json()["results"]
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("created", "x_new_lab"), ("created", "x_new_lab"), ("created", "x_openai"),
        ("rejected", None), ("rejected", None),
        ("created", "web_blog_example_com"), ("created", "web_anthropic_news"), ("rejected", None),
    ]
    assert all("认不出这一条的出处" in results[i]["reason"] for i in (3, 4, 7))
    # 多出来的只有两个预设源的镜像行(第一次有条目入库时补建);认不出的出处没有建源。
    assert _source_ids(sink.engine) - before == {"x_openai", "web_anthropic_news"}


def test_an_open_source_takes_posts_from_any_author(monkeypatch, tmp_path):
    from models.db import ArticleRecord, SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _token_id(client, token)
        picks = _register(client, kind="social_post", name="Grok 推荐", source_id_suffix="grok_picks",
                          token_id=token_id)
        assert picks == "push_grok_picks"
        _bind(client, token, "x_openai")
        results = _push(client, token, [
            _post("someone", 31, "from someone"),
            _post("another", 32, "from another"),
            _post("OpenAI", 33, "the account source wins when it is bound"),
            _post("someone", 34, source={"source_id": "push_grok_picks"}),
        ]).json()["results"]
        # 两个合集都绑着时,没写 source_id 的认不出该进哪个。
        _register(client, kind="social_post", name="第二个合集", source_id_suffix="second", token_id=token_id)
        ambiguous = _push(client, token, [_post("someone", 35)]).json()["results"][0]
        fetchers = {f["id"]: f for f in client.get("/api/fetchers").json()}
    assert [(r["status"], r["source_id"]) for r in results] == [
        ("created", "push_grok_picks"), ("created", "push_grok_picks"),
        ("created", "x_openai"), ("created", "push_grok_picks"),
    ]
    assert ambiguous["status"] == "rejected" and "source.source_id" in ambiguous["reason"]
    source = _row(sink.engine, SourceConfigRecord, "push_grok_picks")
    assert (source.origin, source.shape, source.platform, source.admission_status, source.name) == (
        "ingest", "social", "x", "incubating", "Grok 推荐",
    )
    assert source.fetcher_id == "" and source.legacy_export is False
    # 卡片的作者取自帖子自己:合集里各条保留各自的作者。
    import json

    authors = {
        json.loads(_row(sink.engine, ArticleRecord, f"push_grok_picks_{n}").extensions_json)["author_handle"]
        for n in (31, 32)
    }
    assert authors == {"someone", "another"}
    # 源管理:合集没有抓取绑定,但带着推送绑定;预设源两种都有。
    grok, openai = fetchers["push_grok_picks"], fetchers["x_openai"]
    assert grok["bound"] is False and [b["actor"] for b in grok["push_bindings"]] == ["research-agent"]
    assert openai["bound"] is True and openai["push_bindings"][0]["last_pushed_at"]
    assert fetchers["x_sama"]["push_bindings"] == []


def test_an_account_source_only_takes_its_own_account(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    raw = {
        "data": {"id": "44", "author_id": "777", "text": "raw post", "lang": "en",
                 "created_at": "2026-11-20T08:00:00.000Z"},
        "includes": {"users": [{"id": "777", "username": "intruder", "name": "Intruder"}]},
    }
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        _bind(client, token, "x_openai")
        results = _push(client, token, [
            _post("intruder", 41, source={"source_id": "x_openai"}),
            # 账号写在帖子链接里、作者对象没给账号名:同样核对。
            {"kind": "social_post", "url": "https://x.com/intruder/status/42", "content": "x",
             "source": {"source_id": "x_openai"}},
            {"kind": "social_post", "raw": raw, "source": {"source_id": "x_openai"}},
            # 转推:顶层作者是这个账号(转推者),原作者在 reposted 里。
            {"kind": "social_post", "url": "https://x.com/OpenAI/status/45", "author": {"name": "OpenAI"},
             "reposted": {"url": "https://x.com/intruder/status/40", "content": "original",
                          "author": {"name": "Intruder"}}},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["rejected", "rejected", "rejected", "created"]
    assert all("作者不是来源 x_openai 的账号" in r["reason"] for r in results[:3])
    assert results[3]["source_id"] == "x_openai"
    # 令牌推送与单条收录走同一套载荷整理:原帖对象照样落进扩展字段。
    import json

    from models.db import ArticleRecord

    stored = json.loads(_row(sink.engine, ArticleRecord, "x_openai_45").extensions_json)
    assert (stored["author_handle"], stored["reposted"]["author_handle"], stored["reposted"]["text"]) == (
        "OpenAI", "intruder", "original",
    )


def test_kind_must_match_the_bound_source_and_duplicates_still_resolve(monkeypatch, tmp_path):
    from models.db import ArticlePickRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        existing = _collect(client, [
            {"kind": "article", "url": "https://www.anthropic.com/news/already", "title": "t", "content": "x"},
        ]).json()["results"][0]
        token = _issue_token(client)
        token_id = _token_id(client, token)
        reads = _register(client, kind="article", name="Grok 读到的文章", source_id_suffix="grok_reads",
                          token_id=token_id)
        _register(client, kind="social_post", name="Grok 推荐", source_id_suffix="grok_picks", token_id=token_id)
        results = _push(client, token, [
            # 文章进文章合集、推文进推文合集:按形态各认各的。
            {"kind": "article", "url": "https://elsewhere.example.org/p/1", "title": "c", "content": "x"},
            _post("someone", 51),
            {"kind": "article", "url": "https://elsewhere.example.org/p/2", "title": "d",
             "source": {"source_id": "push_grok_picks"}},
            # 同一链接已在别的源里:不新建,只记收录。
            {"kind": "article", "url": "https://www.anthropic.com/news/already", "title": "again"},
            {"kind": "article", "url": "https://x.com/someone/status/52", "title": "x"},
        ]).json()["results"]
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("created", reads), ("created", "push_grok_picks"), ("rejected", None),
        ("duplicate_url", existing["source_id"]), ("rejected", None),
    ]
    assert "社交源" in results[2]["reason"] and "只收社交帖" in results[4]["reason"]
    with Session(sink.engine) as session:
        actors = {p.actor for p in session.exec(
            select(ArticlePickRecord).where(ArticlePickRecord.article_id == existing["id"])
        ).all()}
    assert actors == {"admin", "research-agent"}


def test_register_derives_ids_like_the_ingest_channel_and_reports_existing_sources(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord, SourceIdentityKeyRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        account = _register(client, kind="social_post", name="New Lab", handle="@New_Lab", user_id="9001",
                            avatar_url="https://img.test/a.jpg", listed=False)
        site = _register(client, kind="article", name="Example Blog", url="https://blog.example.com/")
        show = _register(client, kind="podcast_episode", name="Pod Show", feed_url="https://pod.test/feed.xml")

        def conflict(**body):
            response = client.post("/api/admin/ingest/sources", json=body)
            return response.status_code, response.json().get("detail")

        again = conflict(kind="social_post", name="dup", handle="new_lab")
        preset = conflict(kind="social_post", name="dup", handle="OpenAI")
        curated = conflict(kind="article", name="dup", url="https://www.anthropic.com/news/x")
        taken = conflict(kind="social_post", name="dup", source_id_suffix="grok")  # 第一次:成功
        bad = [
            conflict(kind="social_post", name="x", source_id_suffix="No Caps"),
            conflict(kind="social_post", name="", source_id_suffix="okay_id"),
            conflict(kind="podcast_episode", name="x"),
            conflict(kind="social_post", name="x", user_id="123"),
            conflict(kind="video", name="x", source_id_suffix="okay_id"),
        ]
        taken_again = conflict(kind="article", name="dup", source_id_suffix="grok")
        # 登记过的账号源:推送按账号认回,读者目录按准入状态列出。
        token = _issue_token(client)
        _bind(client, token, account)
        pushed = _push(client, token, [_post("new_lab", 61)]).json()["results"][0]
    assert (account, site) == ("x_new_lab", "web_blog_example_com") and show.startswith("podcast_pod_test_")
    row = _row(sink.engine, SourceConfigRecord, account)
    assert (row.origin, row.admission_status, row.name, row.avatar_url, row.homepage_url) == (
        "ingest", "provisional", "New Lab", "https://img.test/a.jpg", "https://x.com/New_Lab",
    )
    assert row.fetcher_id == "" and _row(sink.engine, SourceIdentityKeyRecord, "x:user_id:9001").source_id == account
    assert _row(sink.engine, SourceConfigRecord, site).admission_status == "incubating"
    podcast = _row(sink.engine, SourceConfigRecord, show)
    assert (podcast.shape, podcast.url, podcast.fetcher_id) == ("podcast", "https://pod.test/feed.xml", "")
    assert again == (409, {"message": "这个出处已经有来源 x_new_lab,直接绑定它即可", "source_id": "x_new_lab"})
    assert (preset[0], preset[1]["source_id"]) == (409, "x_openai")
    assert (curated[0], curated[1]["source_id"]) == (409, "web_anthropic_news")
    assert taken[0] == 200 and (taken_again[0], taken_again[1]["source_id"]) == (409, "push_grok")
    assert [status for status, _detail in bad] == [400] * 5
    assert (pushed["status"], pushed["source_id"]) == ("created", "x_new_lab")


def test_binding_rules_unbind_and_revoked_tokens(monkeypatch, tmp_path):
    from models.db import AdminAuditRecord, IngestBindingRecord, SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        now = "2026-11-06T00:00:00"
        session.add(SourceConfigRecord(source_id="user_rss_private", name="p", owner_username="reader",
                                       created_at=now, updated_at=now))
        # 读者自定源也可能不带 user_rss_ 前缀:认的是创建者,不只是前缀。
        session.add(SourceConfigRecord(source_id="reader_owned_feed", name="o", owner_username="reader",
                                       created_at=now, updated_at=now))
        session.add(SourceConfigRecord(source_id="synced_row", name="s", collection_authority_id="producer-a",
                                       created_at=now, updated_at=now))
        session.add(SourceConfigRecord(source_id="gone_row", name="g", retired_at=now, is_active=False,
                                       created_at=now, updated_at=now))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _token_id(client, token)

        def bind(source_id, tid=token_id):
            return client.post(f"/api/admin/ingest/tokens/{tid}/bindings", json={"source_id": source_id})

        refused = {sid: bind(sid).status_code for sid in (
            "user_rss_private", "reader_owned_feed", "gone_row", "dorami_daily_brief", "generic_rss",
            "nope_missing", "",
        )}
        # 带同步印记的源照常可以绑(issue #263:印记只是「上游也有这个源」的簿记)。
        assert bind("synced_row").status_code == 200
        assert client.delete(f"/api/admin/ingest/tokens/{token_id}/bindings/synced_row").status_code == 200
        assert bind("x_openai").status_code == 200
        twice = bind("x_openai").json()
        assert bind("x_openai", tid=99999).status_code == 404
        # 签发时带来源:有一个绑不了就不签发。
        failed = client.post("/api/admin/ingest/tokens", json={"actor": "half", "source_ids": ["x_sama", "gone_row"]})
        tokens_after_failure = len(client.get("/api/admin/ingest/tokens").json()["items"])
        ok = _push(client, token, [_post("OpenAI", 71)]).json()["results"][0]
        assert client.delete(f"/api/admin/ingest/tokens/{token_id}/bindings/x_openai").status_code == 200
        assert client.delete(f"/api/admin/ingest/tokens/{token_id}/bindings/x_openai").status_code == 404
        after_unbind = _push(client, token, [_post("OpenAI", 72)]).json()["results"][0]
        assert bind("x_sama").status_code == 200
        assert client.post(f"/api/admin/ingest/tokens/{token_id}/revoke").status_code == 200
        assert bind("x_openai").status_code == 400  # 吊销的令牌不能再绑
        assert _push(client, token, [_post("sama", 73)]).status_code == 401
    assert refused == dict.fromkeys(refused, 400)
    assert [b["source_id"] for b in twice["bindings"]] == ["x_openai"]
    assert failed.status_code == 400 and tokens_after_failure == 1
    assert ok["status"] == "created" and after_unbind["status"] == "rejected"
    with Session(sink.engine) as session:
        assert [b.source_id for b in session.exec(select(IngestBindingRecord)).all()] == ["x_sama"]
        summaries = [a.summary for a in session.exec(select(AdminAuditRecord)).all()]
    assert f"绑定来源 x_openai(收录令牌 {token_id})" in summaries
    assert f"解绑来源 x_openai(收录令牌 {token_id})" in summaries


def test_the_single_item_form_still_resolves_and_creates_sources(monkeypatch, tmp_path):
    """管理员「单条收录」不受绑定限制:照旧认出处,认不出建临时源。"""
    from models.db import SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [_post("brand_new", 81), _post("OpenAI", 82)]).json()["results"]
    assert [(r["status"], r["source_id"], r["source_created"]) for r in results] == [
        ("created", "x_brand_new", True), ("created", "x_openai", False),
    ]
    assert _row(sink.engine, SourceConfigRecord, "x_brand_new").admission_status == "provisional"


def test_only_registered_push_ids_count_as_open_sources(monkeypatch, tmp_path):
    """合集的认法是结构性的:``push_`` 开头、没有身份键;存量里没有身份键的旧导入行仍是账号源。"""
    from models.db import SourceConfigRecord
    from services import ingest_push

    app_module, sink = _setup(monkeypatch, tmp_path)
    now = "2026-11-06T00:00:00"
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(source_id="x_legacy_account", name="旧导入", origin="legacy",
                                       shape="social", platform="x", source_type="x",
                                       created_at=now, updated_at=now))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        _register(client, kind="social_post", name="合集", source_id_suffix="grok_picks")
        token = _issue_token(client)
        _bind(client, token, "x_legacy_account")
        results = _push(client, token, [_post("stranger", 91), _post("legacy_account", 92)]).json()["results"]
    with Session(sink.engine) as session:
        ctx = ingest_push.new_context(session, "")
        assert ingest_push.source_is_open(ctx, "x_legacy_account") is False
        assert ingest_push.source_is_open(ctx, "push_grok_picks") is True
    # 旧导入行没有身份键:不当成合集,陌生作者不进它;账号名对得上 id 的照收。
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("rejected", None), ("created", "x_legacy_account"),
    ]


# ── 检视 R1 的修复 ──────────────────────────────────────────────────────────


def test_deleting_a_source_drops_its_bindings_and_orphans_cannot_recreate_it(monkeypatch, tmp_path):
    from models.db import IngestBindingRecord, SourceConfigRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _token_id(client, token)
        picks = _register(client, kind="social_post", name="合集", source_id_suffix="gone_soon", token_id=token_id)
        # 还没有条目:删除是物理删除,绑定跟着走。
        assert client.delete(f"/api/source-configs/{picks}").json()["disposition"] == "deleted"
        with Session(sink.engine) as session:
            assert session.exec(select(IngestBindingRecord)).all() == []
            # 再造一条指向已删除源的绑定(别的删除路径漏清时的样子):推送被拒收,源不会被补建回来。
            session.add(IngestBindingRecord(source_id=picks, token_id=token_id, created_at="2026-11-06T00:00:00"))
            session.commit()
        results = _push(client, token, [
            {"kind": "article", "url": "https://elsewhere.example.org/p/1", "title": "t", "content": "x"},
            _post("someone", 101),
            _post("someone", 102, source={"source_id": picks}),
        ]).json()["results"]
        # 有条目的源删除时是退役:行还在,绑定保留,推送被「已下线」拒收。
        kept = _register(client, kind="social_post", name="留着", source_id_suffix="kept_one", token_id=token_id)
        assert _push(client, token, [_post("someone", 103, source={"source_id": kept})]).json()["results"][0]["status"] == "created"
        assert client.delete(f"/api/source-configs/{kept}").json()["disposition"] == "retired"
        retired = _push(client, token, [_post("someone", 104, source={"source_id": kept})]).json()["results"][0]
    assert [r["status"] for r in results] == ["rejected"] * 3
    assert all("不存在" in r["reason"] or "认不出" in r["reason"] for r in results), results
    assert _row(sink.engine, SourceConfigRecord, picks) is None
    assert retired["status"] == "rejected" and "下线" in retired["reason"]
    with Session(sink.engine) as session:
        assert {b.source_id for b in session.exec(select(IngestBindingRecord)).all()} == {picks, kept}


def test_author_check_uses_the_posts_platform(monkeypatch, tmp_path):
    """作者对象没有平台字段:核对用帖子自己的平台,别的平台上的同名账号进不了 X 账号源。"""
    import json

    from models.db import ArticleRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        # 单条收录留下一个非 X 平台的账号源。
        made = _collect(client, [
            {"kind": "social_post", "platform": "mastodon", "post_id": "1", "content": "toot",
             "author": {"handle": "alice", "name": "Alice"}},
        ]).json()["results"][0]
        assert made["source_id"] == "mastodon_alice"
        token = _issue_token(client)
        _bind(client, token, "x_openai", "mastodon_alice")
        results = _push(client, token, [
            # 别的平台上的同名账号,指名进 X 账号源:拒收(不论平台写在条目上还是出处描述里)。
            {"kind": "social_post", "platform": "mastodon", "post_id": "11", "content": "x",
             "author": {"handle": "OpenAI"}, "source": {"source_id": "x_openai"}},
            {"kind": "social_post", "platform": "mastodon", "post_id": "12", "content": "x",
             "author": {"handle": "OpenAI"}, "source": {"source_id": "x_openai", "platform": "x"}},
            # X 上的同名账号进不了 mastodon 账号源。
            {"kind": "social_post", "post_id": "13", "content": "x",
             "author": {"handle": "alice"}, "source": {"source_id": "mastodon_alice"}},
            # 非 X 的账号源照收自己平台上的作者:指名与按作者认两种写法。
            {"kind": "social_post", "platform": "mastodon", "post_id": "14", "content": "x",
             "author": {"handle": "alice"}, "source": {"source_id": "mastodon_alice"}},
            {"kind": "social_post", "platform": "mastodon", "post_id": "15", "content": "x",
             "author": {"handle": "alice"}},
        ]).json()["results"]
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("rejected", None), ("rejected", None), ("rejected", None),
        ("created", "mastodon_alice"), ("created", "mastodon_alice"),
    ]
    assert all("作者不是来源" in r["reason"] for r in results[:3])
    stored = json.loads(_row(sink.engine, ArticleRecord, "mastodon_alice_14").extensions_json)
    assert stored["platform"] == "mastodon"


def test_a_source_with_an_active_push_binding_counts_as_in_local_use(monkeypatch, tmp_path):
    """上游删掉或退役一个本机有令牌在推的源,不能把本机的推送掐断(绑定只在本机、不随同步)。"""
    from models.db import SourceConfigRecord
    from services import archive_sync_v2

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        now = "2026-11-06T00:00:00"
        session.add(SourceConfigRecord(source_id="synced_site", name="s", source_type="rss", shape="article",
                                       collection_authority_id="producer-a", created_at=now, updated_at=now))
        session.commit()

    def in_use():
        with Session(sink.engine) as session:
            row = session.get(SourceConfigRecord, "synced_site")
            return archive_sync_v2.source_in_local_use(session, row, archive_sync_v2._PageOutcome())  # noqa: SLF001

    with TestClient(app_module.app) as client:
        _login(client)
        assert in_use() is False
        token = _issue_token(client)
        token_id = _bind(client, token, "synced_site")
        bound = in_use()
        assert client.post(f"/api/admin/ingest/tokens/{token_id}/revoke").status_code == 200
        revoked = in_use()  # 吊销的令牌不算在推
        other = _issue_token(client, actor="second")
        other_id = _bind(client, other, "synced_site")
        rebound = in_use()
        assert client.delete(f"/api/admin/ingest/tokens/{other_id}/bindings/synced_site").status_code == 200
    assert (bound, revoked, rebound, in_use()) == (True, False, True, False)


def test_an_article_source_registered_by_feed_is_recognised_by_that_feed(monkeypatch, tmp_path):
    from models.db import SourceIdentityKeyRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    feed = "https://feeds.example.com/lab.xml"
    with TestClient(app_module.app) as client:
        _login(client)
        lab = _register(client, kind="article", name="Lab Blog", feed_url=feed)
        again = client.post("/api/admin/ingest/sources", json={"kind": "article", "name": "dup", "feed_url": feed})
        token = _issue_token(client)
        _bind(client, token, lab)
        results = _push(client, token, [
            # feed 与文章不在同一个主机上:靠 feed 认回登记的源。
            {"kind": "article", "url": "https://lab.example.com/blog/test", "title": "t", "content": "x",
             "source": {"feed_url": feed}},
            {"kind": "article", "url": "https://lab.example.com/blog/other", "title": "t", "content": "x"},
        ]).json()["results"]
        # 单条收录按同一个 feed 也认回它,不另建临时源。
        manual = _collect(client, [
            {"kind": "article", "url": "https://lab.example.com/blog/manual", "title": "t", "content": "x",
             "source": {"feed_url": feed}},
        ]).json()["results"][0]
    assert _row(sink.engine, SourceIdentityKeyRecord, f"web:feed:{feed}").source_id == lab
    assert (again.status_code, again.json()["detail"]["source_id"]) == (409, lab)
    assert [(r["status"], r.get("source_id")) for r in results] == [("created", lab), ("rejected", None)]
    assert (manual["status"], manual["source_id"], manual["source_created"]) == ("created", lab, False)


def test_a_reassigned_handle_falls_through_to_the_open_source(monkeypatch, tmp_path):
    """账号名对得上、稳定 id 对不上(改名后旧账号名被他人接手):不落进原账号的源,进绑定的合集。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _token_id(client, token)
        alice = _register(client, kind="social_post", name="Alice", handle="alice", user_id="1", token_id=token_id)
        picks = _register(client, kind="social_post", name="合集", source_id_suffix="picks", token_id=token_id)
        author = {"handle": "alice", "user_id": "2", "name": "Another Alice"}
        results = _push(client, token, [
            {"kind": "social_post", "post_id": "201", "content": "x", "author": author},
            {"kind": "social_post", "post_id": "202", "content": "x", "author": author, "source": {"source_id": alice}},
            {"kind": "social_post", "post_id": "203", "content": "x", "author": {"handle": "alice", "user_id": "1"}},
            {"kind": "social_post", "post_id": "204", "content": "x", "author": {"handle": "alice"}},
        ]).json()["results"]
    assert alice == "x_alice"
    assert [(r["status"], r.get("source_id")) for r in results] == [
        ("created", picks), ("rejected", None), ("created", alice), ("created", alice),
    ]
    assert "作者不是来源 x_alice 的账号" in results[1]["reason"]


def test_issuing_with_sources_is_all_or_nothing(monkeypatch, tmp_path):
    """签发与绑定同一事务:绑定中途失败不留下「拿不到明文、又没有绑定」的令牌。"""
    from models.db import IngestBindingRecord, IngestTokenRecord
    from services import ingest_bindings

    app_module, sink = _setup(monkeypatch, tmp_path)
    real_bind = ingest_bindings.bind

    def flaky(session, token_id, source_id, **kwargs):
        if source_id == "x_sama":
            raise ingest_bindings.BindingError("该来源已下线")  # 预校验之后来源被退役
        return real_bind(session, token_id, source_id, **kwargs)

    with TestClient(app_module.app) as client:
        _login(client)
        monkeypatch.setattr(ingest_bindings, "bind", flaky)
        failed = client.post("/api/admin/ingest/tokens", json={"actor": "half", "source_ids": ["x_openai", "x_sama"]})
        monkeypatch.setattr(ingest_bindings, "bind", real_bind)
        with Session(sink.engine) as session:
            leftovers = (session.exec(select(IngestTokenRecord)).all(), session.exec(select(IngestBindingRecord)).all())
        issued = client.post("/api/admin/ingest/tokens", json={"actor": "whole", "source_ids": ["x_openai", "x_sama"]})
    assert failed.status_code == 400 and leftovers == ([], [])
    assert issued.status_code == 200 and issued.json()["token"].startswith("ding_")
    assert [b["source_id"] for b in issued.json()["bindings"]] == ["x_openai", "x_sama"]
