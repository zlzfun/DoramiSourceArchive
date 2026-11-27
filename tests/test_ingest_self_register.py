"""令牌自己登记来源(issue #344 第二个 PR):是一项单独的权限,登记的源先是待列入并绑定给它,每天有上限。"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_ingest_push import (  # noqa: E402
    _bind, _issue_token, _login, _push, _register, _row, _setup, _token_id,
)

PATH = "/api/public/ingest/v1/sources"
NEW_LAB = {"kind": "social_post", "name": "X · New Lab", "handle": "new_lab", "user_id": "9001"}


def _post(handle, post_id, text="hello", **extra):
    return {"kind": "social_post", "post_id": str(post_id), "content": text,
            "author": {"handle": handle, "name": handle.title()}, **extra}


def _self_register(client, token, **body):
    return client.post(PATH, json=body, headers={"Authorization": f"Bearer {token}"})


def _token_row(client, token):
    return next(item for item in client.get("/api/admin/ingest/tokens").json()["items"]
                if item["token_preview"] == f"...{token[-6:]}")


def _source_ids(engine):
    from models.db import SourceConfigRecord

    with Session(engine) as session:
        return set(session.exec(select(SourceConfigRecord.source_id)).all())


def test_registering_needs_a_token_that_was_given_the_permission(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        plain = _issue_token(client)
        allowed = _issue_token(client, actor="scout", can_register_sources=True)
        before = _source_ids(sink.engine)
        refused = _self_register(client, plain, **NEW_LAB)
        # 管理员会话不算:这个接口只认收录令牌(管理员在管理面登记)。
        by_session = client.post(PATH, json=NEW_LAB)
        bad_token = _self_register(client, "ding_not-a-real-token", **NEW_LAB)
        assert (_token_row(client, plain)["can_register_sources"], _token_row(client, plain)["registered_today"]) == (
            False, 0,
        )
        # 采集面关闭的节点不收录,也不接受登记。
        monkeypatch.setattr(app_module, "runtime_collector_enabled", lambda: False)
        reader_node = _self_register(client, allowed, **NEW_LAB)
    assert (refused.status_code, by_session.status_code, bad_token.status_code, reader_node.status_code) == (
        403, 401, 401, 403,
    )
    assert "没有登记来源的权限" in refused.json()["detail"]
    assert _source_ids(sink.engine) == before


def test_a_registered_source_is_provisional_bound_to_the_token_and_takes_its_pushes(monkeypatch, tmp_path):
    from models.db import AdminAuditRecord, IngestBindingRecord, SourceConfigRecord, SourceIdentityKeyRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        # 令牌不能自己决定「列入」、也不能替别的令牌登记:这两个字段不认,回执里点出来。
        response = _self_register(client, token, **NEW_LAB, avatar_url="https://img.test/a.jpg",
                                  listed=True, token_id=99)
        body = response.json()
        results = _push(client, token, [_post("new_lab", 1), _post("someone_else", 2)]).json()["results"]
        listed = _token_row(client, token)
        catalog = {item["source_id"]: item for item in client.get("/api/admin/ingest/sources").json()["items"]}
        admitted = client.post("/api/admin/ingest/sources/x_new_lab/admit").json()
    assert response.status_code == 200, response.text
    assert {key: body[key] for key in (
        "status", "source_id", "name", "shape", "admission_status", "open", "bound", "registered_today", "daily_limit",
    )} == {
        "status": "success", "source_id": "x_new_lab", "name": "X · New Lab", "shape": "social",
        "admission_status": "provisional", "open": False, "bound": True, "registered_today": 1, "daily_limit": 20,
    }
    assert body["warnings"] == ["未识别的字段:listed、token_id(已忽略)"]
    row = _row(sink.engine, SourceConfigRecord, "x_new_lab")
    assert (row.origin, row.admission_status, row.fetcher_id, row.avatar_url, row.homepage_url) == (
        "ingest", "incubating", "", "https://img.test/a.jpg", "https://x.com/new_lab",
    )  # 上面管理员已经「列入」;登记时是 provisional(回执与收录源列表)
    assert catalog["x_new_lab"]["admission_status"] == "provisional" and admitted["admission_status"] == "incubating"
    assert _row(sink.engine, SourceIdentityKeyRecord, "x:user_id:9001").source_id == "x_new_lab"
    with Session(sink.engine) as session:
        binding = session.exec(select(IngestBindingRecord)).one()
        audits = session.exec(select(AdminAuditRecord).where(AdminAuditRecord.path == PATH)).all()
    assert (binding.source_id, binding.created_by) == ("x_new_lab", "ingest:research-agent")
    assert [(a.username, a.summary, a.status_code) for a in audits] == [
        ("ingest:research-agent", "登记来源 X · New Lab(x_new_lab)", 200),
    ]
    # 登记完就能推;账号源照旧只收这个账号的帖子。
    assert [(r["status"], r.get("source_id")) for r in results] == [("created", "x_new_lab"), ("rejected", None)]
    assert (listed["can_register_sources"], listed["registered_today"], listed["register_daily_limit"]) == (True, 1, 20)
    # 令牌的绑定清单带准入状态(抽屉里据此给待列入的来源标「临时」);取清单时管理员还没有列入。
    assert [(b["source_id"], b["admission_status"]) for b in listed["bindings"]] == [("x_new_lab", "provisional")]


def test_sites_feeds_and_open_collections_can_be_registered_too(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        site = _self_register(client, token, kind="article", name="Example Blog", url="https://blog.example.com/")
        show = _self_register(client, token, kind="podcast_episode", name="Pod Show",
                              feed_url="https://pod.test/feed.xml")
        picks = _self_register(client, token, kind="social_post", name="Agent 精选", source_id_suffix="agent_picks")
        results = _push(client, token, [
            {"kind": "article", "url": "https://blog.example.com/p/1", "title": "t", "content": "x"},
            _post("anyone", 7),  # 合集不限出处
        ]).json()["results"]
    assert [r.status_code for r in (site, show, picks)] == [200, 200, 200]
    assert (site.json()["source_id"], picks.json()["source_id"], picks.json()["open"]) == (
        "web_blog_example_com", "push_agent_picks", True,
    )
    assert show.json()["source_id"].startswith("podcast_pod_test_")
    assert all(r.json()["admission_status"] == "provisional" for r in (site, show, picks))
    assert [(r["status"], r["source_id"]) for r in results] == [
        ("created", "web_blog_example_com"), ("created", "push_agent_picks"),
    ]


def test_an_existing_origin_is_a_409_that_says_whether_this_token_is_bound(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        other = _issue_token(client, actor="other-agent", can_register_sources=True)
        first = _self_register(client, token, **NEW_LAB)
        again = _self_register(client, token, **NEW_LAB)                       # 自己登记过的
        theirs = _self_register(client, other, **NEW_LAB)                      # 别的令牌登记过的
        preset = _self_register(client, token, kind="social_post", name="dup", handle="OpenAI")  # 平台已有的源
        _bind(client, token, "x_openai")
        preset_bound = _self_register(client, token, kind="social_post", name="dup", handle="OpenAI")
        taken = _self_register(client, other, kind="social_post", name="dup", source_id_suffix="agent_picks")
        taken_again = _self_register(client, token, kind="article", name="dup", source_id_suffix="agent_picks")
        counts = (_token_row(client, token)["registered_today"], _token_row(client, other)["registered_today"])
    assert first.status_code == 200 and taken.status_code == 200
    assert (again.status_code, again.json()["detail"]) == (409, {
        "message": "这个出处已经有来源 x_new_lab,这个令牌已经绑定,直接推送即可", "source_id": "x_new_lab", "bound": True,
    })
    assert (theirs.status_code, theirs.json()["detail"]) == (409, {
        "message": "这个出处已经有来源 x_new_lab;请管理员把它绑定给这个令牌", "source_id": "x_new_lab", "bound": False,
    })
    assert (preset.status_code, preset.json()["detail"]["source_id"], preset.json()["detail"]["bound"]) == (
        409, "x_openai", False,
    )
    assert (preset_bound.status_code, preset_bound.json()["detail"]["bound"]) == (409, True)
    assert (taken_again.status_code, taken_again.json()["detail"]["source_id"], taken_again.json()["detail"]["bound"]) == (
        409, "push_agent_picks", False,
    )
    # 没登记成的不占名额。
    assert counts == (1, 1)


def test_the_daily_cap_counts_registrations_and_does_not_roll_back_when_a_source_goes(monkeypatch, tmp_path):
    from services import ingest_push

    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ingest_push, "REGISTER_DAILY_LIMIT", 2)
    day = {"value": "2026-11-27"}
    monkeypatch.setattr(ingest_push, "_utc_day", lambda: day["value"])

    def register(client, token, suffix):
        return _self_register(client, token, kind="social_post", name=suffix, source_id_suffix=suffix)

    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        other = _issue_token(client, actor="other-agent", can_register_sources=True)
        invalid = register(client, token, "No Caps")  # 登记不成:不占名额
        statuses = [register(client, token, f"box_{n}").status_code for n in range(3)]
        over = register(client, token, "box_9")
        # 管理员解绑、删源都不退还名额:上限卡的是「今天登记了几次」。
        token_id = _token_id(client, token)
        assert client.delete(f"/api/admin/ingest/tokens/{token_id}/bindings/push_box_0").status_code == 200
        assert client.delete("/api/source-configs/push_box_1").status_code == 200
        still_over = register(client, token, "box_8")
        theirs = register(client, other, "theirs")  # 名额各算各的
        used = _token_row(client, token)["registered_today"]
        day["value"] = "2026-11-28"
        tomorrow_used = _token_row(client, token)["registered_today"]
        tomorrow = register(client, token, "box_7")
    assert invalid.status_code == 400
    assert statuses == [200, 200, 429] and (over.status_code, still_over.status_code) == (429, 429)
    assert "已到上限(2 个)" in over.json()["detail"]
    assert theirs.status_code == 200 and used == 2
    assert (tomorrow_used, tomorrow.status_code, tomorrow.json()["registered_today"]) == (0, 200, 1)
    assert "push_box_2" not in _source_ids(sink.engine) and "push_box_9" not in _source_ids(sink.engine)


def test_reserving_a_slot_is_one_statement(monkeypatch, tmp_path):
    from models.db import IngestTokenRecord
    from services import ingest_push

    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ingest_push, "REGISTER_DAILY_LIMIT", 2)
    with Session(sink.engine) as session:
        record, _plain = ingest_push.create_token(session, actor="scout", created_by="admin", can_register_sources=True)
        token_id = record.id
        taken = [ingest_push.reserve_registration(session, token_id) for _ in range(3)]
        session.rollback()  # 没提交的名额退还
        session.expire_all()
        after_rollback = ingest_push.registered_today(session.get(IngestTokenRecord, token_id))
        assert ingest_push.reserve_registration(session, token_id)
        session.commit()
        session.expire_all()
        kept = ingest_push.registered_today(session.get(IngestTokenRecord, token_id))
        missing = ingest_push.reserve_registration(session, token_id + 100)
    assert taken == [True, True, False] and (after_rollback, kept) == (0, 1) and missing is False


def test_the_admin_switches_the_permission_and_it_is_audited(monkeypatch, tmp_path):
    from models.db import AdminAuditRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        token_id = _token_id(client, token)
        url = f"/api/admin/ingest/tokens/{token_id}"
        before = _self_register(client, token, **NEW_LAB).status_code
        on = client.patch(url, json={"can_register_sources": True})
        while_on = _self_register(client, token, **NEW_LAB).status_code
        off = client.patch(url, json={"can_register_sources": False})
        after = _self_register(client, token, kind="social_post", name="x", source_id_suffix="later").status_code
        # 关掉权限不动它已经登记并绑定的源:照常能推。
        pushed = _push(client, token, [_post("new_lab", 3)]).json()["results"][0]["status"]
        empty = client.patch(url, json={})
        missing = client.patch("/api/admin/ingest/tokens/9999", json={"can_register_sources": True})
        assert client.post(f"{url}/revoke").status_code == 200
        revoked = client.patch(url, json={"can_register_sources": True})
        dead = _self_register(client, token, kind="social_post", name="x", source_id_suffix="dead").status_code
    assert (before, while_on, after, dead) == (403, 200, 403, 401)
    assert (on.status_code, on.json()["can_register_sources"], off.json()["can_register_sources"]) == (200, True, False)
    assert [b["source_id"] for b in off.json()["bindings"]] == ["x_new_lab"] and pushed == "created"
    assert (empty.status_code, empty.json()["can_register_sources"]) == (200, False)
    assert (missing.status_code, revoked.status_code) == (404, 400)
    with Session(sink.engine) as session:
        summaries = [a.summary for a in session.exec(
            select(AdminAuditRecord).where(AdminAuditRecord.path == url, AdminAuditRecord.method == "PATCH")
        ).all()]
    assert summaries[:2] == [f"允许收录令牌 {token_id} 登记来源", f"不再允许收录令牌 {token_id} 登记来源"]


@pytest.mark.parametrize("field,value", [
    ("avatar_url", "javascript:alert(1)"),
    ("homepage_url", "data:text/html,<script>alert(1)</script>"),
    ("homepage_url", "https://"),
    ("url", "javascript://blog.example.com/%0aalert(1)"),
    ("feed_url", "file:///etc/passwd"),
    ("avatar_url", "https://img.test/" + "a" * 2000),
])
def test_addresses_given_at_registration_must_be_http(monkeypatch, tmp_path, field, value):
    """这些地址会成为源的主页链接与头像;令牌和管理员登记走同一条校验。主页地址令牌根本不能给(见下一组用例)。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    body = {"kind": "article", "name": "Example Blog", "url": "https://blog.example.com/", field: value}
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        before = _source_ids(sink.engine)
        by_admin = client.post("/api/admin/ingest/sources", json=body)
        by_token = _self_register(client, token, **body) if field != "homepage_url" else None
        used = _token_row(client, token)["registered_today"]
        ok = _register(client, kind="article", name="Example Blog", url="https://blog.example.com/",
                       avatar_url="https://img.test/a.png", homepage_url="http://blog.example.com/about")
    assert (by_admin.status_code, used) == (400, 0) and "地址" in by_admin.json()["detail"]
    if by_token is not None:
        assert by_token.status_code == 400 and "地址" in by_token.json()["detail"]
    assert _source_ids(sink.engine) - before == {ok}


def test_rejections_point_tokens_that_may_register_at_the_endpoint(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        plain = _issue_token(client)
        scout = _issue_token(client, actor="scout", can_register_sources=True)
        unbound_plain = _push(client, plain, [_post("new_lab", 1)]).json()["results"][0]["reason"]
        unbound_scout = _push(client, scout, [_post("new_lab", 1)]).json()["results"][0]["reason"]
        assert _self_register(client, scout, **NEW_LAB).status_code == 200
        unknown_scout = _push(client, scout, [_post("someone_new", 2)]).json()["results"][0]["reason"]
    hint = "也可以先登记来源(POST /api/public/ingest/v1/sources)"
    assert "还没有绑定来源" in unbound_plain and hint not in unbound_plain
    assert "还没有绑定来源" in unbound_scout and unbound_scout.endswith(hint)
    assert "认不出这一条的出处" in unknown_scout and unknown_scout.endswith(hint)


# ── 检视首轮(Codex):令牌登记是排他的——只能占用没有任何源认领的出处 ──


def test_a_token_cannot_take_over_an_account_name_another_source_holds(monkeypatch, tmp_path):
    """账号名对得上、稳定 id 对不上:单条收录会按「改名后被接手」另建源并把账号名转过去;令牌登记不行。"""
    from models.db import SourceIdentityKeyRecord

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        original = _register(client, kind="social_post", name="Original", handle="review_original", user_id="1001")
        owner = _issue_token(client, actor="owner-agent", source_ids=[original])
        scout = _issue_token(client, actor="scout", can_register_sources=True)
        before = _source_ids(sink.engine)
        taken = _self_register(client, scout, kind="social_post", name="Mine", handle="review_original", user_id="1002")
        preset = _self_register(client, scout, kind="social_post", name="Mine", handle="OpenAI", user_id="424242")
        # 原来的令牌只带账号名照常推得进原来的源。
        pushed = _push(client, owner, [_post("review_original", 5)]).json()["results"][0]
        used = _token_row(client, scout)["registered_today"]
    assert (taken.status_code, taken.json()["detail"]["source_id"], taken.json()["detail"]["bound"]) == (
        409, original, False,
    )
    assert (preset.status_code, preset.json()["detail"]["source_id"]) == (409, "x_openai")
    assert _source_ids(sink.engine) == before and used == 0
    assert _row(sink.engine, SourceIdentityKeyRecord, "x:handle:review_original").source_id == original
    assert _row(sink.engine, SourceIdentityKeyRecord, "x:user_id:1002") is None
    assert (pushed["status"], pushed["source_id"]) == ("created", original)


def test_a_token_cannot_claim_addresses_that_already_resolve_to_a_source(monkeypatch, tmp_path):
    """主页地址不由令牌给;给的每个地址(站点、feed)都按收录时认出处的办法探过,认得到已有源就是 409。"""
    from models.db import SourceConfigRecord
    from tests.test_ingest_push import _collect

    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        original = _register(client, kind="article", name="Original", url="https://original.test/")
        scout = _issue_token(client, actor="scout", can_register_sources=True)
        # 1. 令牌带的 homepage_url 不认(写进 warnings),主页由出处派生:别的站点的地址进不了前缀索引。
        mine = _self_register(client, scout, kind="article", name="Mine", url="https://new.test/",
                              homepage_url="https://original.test/news")
        story = _collect(client, [
            {"kind": "article", "url": "https://original.test/news/story", "title": "t", "content": "x"},
        ]).json()["results"][0]
        before = _source_ids(sink.engine)
        # 2. 只给 feed:feed 所在的主机上已经有源(这里是策展源 web_anthropic_news)。
        feed_only = _self_register(client, scout, kind="article", name="Mine",
                                   feed_url="https://www.anthropic.com/feed.xml")
        # 3. 站点是新的,feed 在已有源的主机上。
        split = _self_register(client, scout, kind="article", name="Mine", url="https://another.test/",
                               feed_url="https://original.test/feed.xml")
        # 4. 合集没有出处,也就没有主页。
        picks = _self_register(client, scout, kind="article", name="Picks", source_id_suffix="scout_reads",
                               homepage_url="https://original.test/")
        after = _source_ids(sink.engine)
        used = _token_row(client, scout)["registered_today"]
        # 策展源所在主机上的其它链接照旧认到策展源(它的镜像行在这时才补建,所以源表的前后对比取在这之前)。
        research = _collect(client, [
            {"kind": "article", "url": "https://www.anthropic.com/research/x", "title": "t", "content": "x"},
        ]).json()["results"][0]
    assert mine.status_code == 200 and mine.json()["warnings"] == ["未识别的字段:homepage_url(已忽略)"]
    # 服务层也守着这一条(不只靠路由的字段表):直接调用并带上主页,同样不采用。
    from models.db import IngestTokenRecord
    from services import ingest_bindings

    with Session(sink.engine) as session:
        record = session.exec(select(IngestTokenRecord).where(IngestTokenRecord.actor == "scout")).one()
        direct = ingest_bindings.register_by_token(
            session, record, kind="article", name="Direct", url="https://direct.test/",
            homepage_url="https://original.test/news",
        )
        assert (direct.homepage_url, direct.base_url) == ("https://direct.test/",) * 2
        session.rollback()
    row = _row(sink.engine, SourceConfigRecord, mine.json()["source_id"])
    assert (row.homepage_url, row.base_url, row.url) == ("https://new.test/",) * 3
    assert (story["status"], story["source_id"]) == ("created", original)
    assert (feed_only.status_code, feed_only.json()["detail"]["source_id"], feed_only.json()["detail"]["bound"]) == (
        409, "web_anthropic_news", False,
    )
    assert (split.status_code, split.json()["detail"]["source_id"]) == (409, original)
    assert picks.status_code == 200
    collection = _row(sink.engine, SourceConfigRecord, "push_scout_reads")
    assert (collection.homepage_url, collection.base_url, collection.url) == ("", "", "")
    assert after - before == {"push_scout_reads"} and used == 2
    assert (research["status"], research["source_id"]) == ("created", "web_anthropic_news")


def test_registering_a_known_source_again_is_a_409_even_when_todays_slots_are_used_up(monkeypatch, tmp_path):
    """契约:200 或 409 + bound=true 都可以接着推——名额用完之后也一样,只有确实要新建的才回 429。"""
    from services import ingest_push

    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ingest_push, "REGISTER_DAILY_LIMIT", 2)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client, can_register_sources=True)
        first = _self_register(client, token, **NEW_LAB)
        second = _self_register(client, token, kind="social_post", name="Box", source_id_suffix="box_1")
        before = _source_ids(sink.engine)
        again = _self_register(client, token, **NEW_LAB)                      # 自己登记过的
        preset = _self_register(client, token, kind="social_post", name="dup", handle="OpenAI")  # 平台已有、没绑
        invalid = _self_register(client, token, kind="social_post", name="x", source_id_suffix="No Caps")
        fresh = _self_register(client, token, kind="social_post", name="Box", source_id_suffix="box_2")
        used = _token_row(client, token)["registered_today"]
    assert (first.status_code, second.status_code) == (200, 200)
    assert (again.status_code, again.json()["detail"]["bound"]) == (409, True)
    assert (preset.status_code, preset.json()["detail"]["bound"]) == (409, False)
    assert invalid.status_code == 400
    assert fresh.status_code == 429 and "已到上限(2 个)" in fresh.json()["detail"]
    assert _source_ids(sink.engine) == before and used == 2


def test_admin_registration_probes_feed_and_homepage_too_and_a_feed_only_source_claims_just_the_feed(monkeypatch, tmp_path):
    """占用探测在共用的登记函数里:管理员登记同样不能让新源一建出来就认走别的源的链接。"""
    from models.db import SourceConfigRecord, SourceIdentityKeyRecord
    from tests.test_ingest_push import _collect

    app_module, sink = _setup(monkeypatch, tmp_path)

    def admin(**body):
        response = client.post("/api/admin/ingest/sources", json=body)
        return response.status_code, (response.json().get("detail") or {}) if response.status_code != 200 else response.json()

    with TestClient(app_module.app) as client:
        _login(client)
        original = _register(client, kind="article", name="Original", url="https://original.test/")
        on_curated_host = admin(kind="article", name="x", feed_url="https://www.anthropic.com/feed.xml")
        feed_on_taken_site = admin(kind="article", name="x", url="https://another.test/",
                                   feed_url="https://original.test/feed.xml")
        homepage_on_taken_site = admin(kind="article", name="x", url="https://another.test/",
                                       homepage_url="https://original.test/news")
        # 多个源共用的主机(没有主机兜底)上靠路径前缀认:主页落在策展源的前缀下面同样是 409。
        homepage_under_prefix = admin(kind="article", name="x", url="https://another.test/",
                                      homepage_url="https://huggingface.co/blog/some-post")
        # 同一个 feed 主机上可以登记多个 feed(各认领各的 feed),主机本身不归它们。
        first = _register(client, kind="article", name="Lab A", feed_url="https://feeds.example.com/a.xml")
        second = _register(client, kind="article", name="Lab B", feed_url="https://feeds.example.com/b.xml")
        # 账号改名被接手:管理员仍然可以(单条收录与登记都是管理员对「这个账号名现在归谁」负责)。
        account = _register(client, kind="social_post", name="Old", handle="renamed", user_id="1")
        taken_over = _register(client, kind="social_post", name="New", handle="renamed", user_id="2")
        elsewhere = _collect(client, [
            {"kind": "article", "url": "https://feeds.example.com/about", "title": "t", "content": "x"},
        ]).json()["results"][0]
    assert (on_curated_host[0], on_curated_host[1]["source_id"]) == (409, "web_anthropic_news")
    assert (feed_on_taken_site[0], feed_on_taken_site[1]["source_id"]) == (409, original)
    assert (homepage_on_taken_site[0], homepage_on_taken_site[1]["source_id"]) == (409, original)
    assert (homepage_under_prefix[0], homepage_under_prefix[1]["source_id"]) == (409, "rss_hf_blog")
    assert first != second
    for source_id, feed in ((first, "a"), (second, "b")):
        row = _row(sink.engine, SourceConfigRecord, source_id)
        assert (row.homepage_url, row.base_url, row.url) == ("", "", "")
        assert _row(sink.engine, SourceIdentityKeyRecord, f"web:feed:https://feeds.example.com/{feed}.xml").source_id == source_id
    assert account != taken_over
    assert _row(sink.engine, SourceIdentityKeyRecord, "x:handle:renamed").source_id == taken_over
    # feed 主机上的一篇普通文章不属于任何一个按 feed 登记的源:单条收录为它另建临时源,主机的站点身份归那个源。
    assert elsewhere["status"] == "created" and elsewhere["source_id"] not in (first, second)
    assert _row(sink.engine, SourceIdentityKeyRecord, "web:site:feeds.example.com").source_id == elsewhere["source_id"]

