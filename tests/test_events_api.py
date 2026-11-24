"""事件热点榜端点(issue #121 wave 1;#221 默认 7 天)TestClient 测试:读者 / 游客 / 管理端点、403 边界、审计落表、
合并 / 摘出语义与返回字段逐字段对齐方案 §5.0(#221 加 latest / first_report_at / is_recent / 主体 source_id)。
令牌化交付口 ``/api/public/feed/events``(issue #310)在文件末尾:与读者面同源、令牌门、运行面门、隐藏源不外流。
显式建 tmp sink;造数据相对时间。"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

from models.db import (  # noqa: E402
    AdminAuditRecord,
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    EventAssignmentLedgerRecord,
    EventMemberRecord,
    EventRecord,
    EventRedirectRecord,
)


NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

BOARD_KEYS = {"generated_at", "window_hours", "snapshot_date", "empty_reason", "items"}
ITEM_KEYS = {
    "event_id", "rank", "previous_rank", "title", "title_lang", "summary", "publisher_count",
    "member_count", "first_seen_at", "first_report_at", "latest_at", "latest", "is_new", "is_recent",
    "representative", "publishers", "story",
}
LATEST_KEYS = {"article_id", "title", "title_zh", "source_id", "source_name", "published_at"}
REPRESENTATIVE_KEYS = {"article_id", "title", "source_id", "source_name", "source_role", "url", "publish_date"}
PUBLISHER_KEYS = {"key", "name", "count", "source_id"}
STORY_KEYS = {"follows_event_id", "follows_title"}
MEMBER_KEYS = {
    "article_id", "title", "title_zh", "source_id", "source_name", "source_role", "content_shape",
    "url", "publish_date", "report_at", "published_at", "publisher_key", "is_representative", "summary",
}
FOLLOWUP_KEYS = {"event_id", "title", "first_seen_at"}
DETAIL_KEYS = ITEM_KEYS | {"members", "followups", "merged_into"}
# 令牌化交付口(issue #310):读者面字段原样下发,外面包一层 feed 家族的 status / count。
FEED_BOARD_KEYS = BOARD_KEYS | {"status", "count"}
FEED_DETAIL_KEYS = {"status", "event"}


def _iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'events_api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    return app_module


def _article(session, article_id, source_id, *, hours_ago=3.0, score=7.0):
    stamp = _iso(hours_ago)
    session.add(ArticleRecord(
        id=article_id, title=f"Title {article_id}", content_type="rss_article", source_id=source_id,
        source_url=f"https://example.test/{article_id}", publish_date=stamp, fetched_date=stamp,
        content="body", extensions_json="{}",
    ))
    session.flush()  # 先落文章行,再挂分析行(FK)
    session.add(ArticleAnalysisRecord(
        article_id=article_id, status="succeeded", quality_score=score, summary=f"Summary {article_id}",
        created_at=stamp, updated_at=stamp,
    ))
    session.flush()


def _event(session, *, hours_ago=3.0, status="open", title="", follows=None):
    stamp = _iso(hours_ago)
    event = EventRecord(
        status=status, title=title, first_seen_at=stamp, last_member_at=stamp,
        follows_event_id=follows, created_at=stamp, updated_at=stamp,
    )
    session.add(event)
    session.flush()
    return event


def _member(session, event, article_id, *, hours_ago=3.0):
    stamp = _iso(hours_ago)
    session.add(EventMemberRecord(
        event_id=event.id, article_id=article_id, report_at=stamp, first_seen_at=stamp,
        assigned_by="llm", created_at=stamp,
    ))
    session.flush()


def _seed(engine) -> dict[str, int]:
    """两件可上榜事件(hot / warm)、一件被并入 hot 的旧事件、一件单成员事件、一件后续事件。"""

    with Session(engine) as session:
        hot = _event(session, hours_ago=2, title="hot stored")
        for article_id, source in (("h1", "media-a"), ("h2", "media-b"), ("h3", "media-c")):
            _article(session, article_id, source, hours_ago=2)
            _member(session, hot, article_id, hours_ago=2)
        warm = _event(session, hours_ago=5, title="warm stored")
        for article_id, source in (("w1", "media-a"), ("w2", "media-d")):
            _article(session, article_id, source, hours_ago=5)
            _member(session, warm, article_id, hours_ago=5)
        # 4 天前的事件:默认 7 天榜上有,近 2 天榜上没有(issue #221)。
        week = _event(session, hours_ago=96, status="closed", title="week stored")
        for article_id, source in (("k1", "media-a"), ("k2", "media-b")):
            _article(session, article_id, source, hours_ago=96)
            _member(session, week, article_id, hours_ago=96)
        old = _event(session, hours_ago=6, status="merged")
        session.add(EventRedirectRecord(old_event_id=old.id, new_event_id=hot.id, reason="manual", created_at=_iso(1)))
        single = _event(session, hours_ago=1, title="single stored")
        _article(session, "s1", "media-e", hours_ago=1)
        _member(session, single, "s1", hours_ago=1)
        followup = _event(session, hours_ago=1, title="followup stored", follows=hot.id)
        _article(session, "f1", "media-f", hours_ago=1)
        _member(session, followup, "f1", hours_ago=1)
        session.commit()
        return {
            "hot": hot.id, "warm": warm.id, "week": week.id, "old": old.id, "single": single.id,
            "followup": followup.id,
        }


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response


def _audit_rows(engine):
    with Session(engine) as session:
        return session.exec(select(AdminAuditRecord).order_by(AdminAuditRecord.id)).all()


def test_reader_board_and_detail_match_contract(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        board = client.get("/api/reader/events")
        assert board.status_code == 200
        payload = board.json()
        assert set(payload) == BOARD_KEYS
        assert payload["window_hours"] == 168 and payload["empty_reason"] is None
        assert [item["event_id"] for item in payload["items"]] == [ids["hot"], ids["warm"], ids["week"]]
        for item in payload["items"]:
            assert set(item) == ITEM_KEYS
            assert set(item["representative"]) == REPRESENTATIVE_KEYS
            assert set(item["latest"]) == LATEST_KEYS
            assert set(item["story"]) == STORY_KEYS
            assert item["publishers"] and all(set(publisher) == PUBLISHER_KEYS for publisher in item["publishers"])
        hot = payload["items"][0]
        assert hot["rank"] == 1 and hot["is_new"] is True and hot["publisher_count"] == 3
        assert hot["previous_rank"] is None and payload["snapshot_date"] is None
        assert hot["is_recent"] is True and payload["items"][2]["is_recent"] is False
        assert {publisher["source_id"] for publisher in hot["publishers"]} == {"media-a", "media-b", "media-c"}
        assert hot["latest"]["source_id"] in {"media-a", "media-b", "media-c"} and hot["first_report_at"] == _iso(2)

        near = client.get("/api/reader/events?window_hours=48").json()
        assert near["window_hours"] == 48
        assert [item["event_id"] for item in near["items"]] == [ids["hot"], ids["warm"]]

        assert client.get("/api/reader/events?limit=11").status_code == 400
        assert client.get("/api/reader/events?window_hours=0").status_code == 400
        assert client.get("/api/reader/events?window_hours=24&limit=1").json()["items"][0]["event_id"] == ids["hot"]

        detail = client.get(f"/api/reader/events/{ids['hot']}")
        assert detail.status_code == 200
        body = detail.json()
        assert set(body) == DETAIL_KEYS
        assert body["merged_into"] is None and body["rank"] == 1
        assert all(set(member) == MEMBER_KEYS for member in body["members"])
        assert {member["publisher_key"] for member in body["members"]} == {"media-a", "media-b", "media-c"}
        # 名次按读者所在视图的窗口算:4 天前的事件在 7 天榜第 3,近 2 天榜上没有。
        assert client.get(f"/api/reader/events/{ids['week']}").json()["rank"] == 3
        assert client.get(f"/api/reader/events/{ids['week']}?window_hours=48").json()["rank"] is None
        assert client.get(f"/api/reader/events/{ids['week']}?window_hours=0").status_code == 400
        assert len(body["members"]) == 3 and sum(member["is_representative"] for member in body["members"]) == 1
        followup_title = client.get(f"/api/reader/events/{ids['followup']}").json()["title"]
        assert body["followups"] == [
            {"event_id": ids["followup"], "title": followup_title, "first_seen_at": _iso(1)}
        ]
        assert all(set(item) == FOLLOWUP_KEYS for item in body["followups"])

        via_old = client.get(f"/api/reader/events/{ids['old']}")
        assert via_old.status_code == 200
        assert via_old.json()["event_id"] == ids["hot"] and via_old.json()["merged_into"] == ids["hot"]

        follow = client.get(f"/api/reader/events/{ids['followup']}").json()
        assert follow["story"] == {"follows_event_id": ids["hot"], "follows_title": body["title"]}
        assert follow["rank"] is None  # 单主体不上榜但可按 ID 查

        assert client.get("/api/reader/events/999999").status_code == 404
        assert client.get("/api/reader/events/not-a-number").status_code == 422
    assert _audit_rows(app_module.db_sink.engine) == []  # 读者面只读,不入审计


def test_guest_reads_events_but_admin_surface_stays_login_required(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/guest", json={"entry": "deep_link"}).status_code == 200
        assert client.get("/api/reader/events").status_code == 200
        assert client.get(f"/api/reader/events/{ids['hot']}").status_code == 200
        for method, path in (
            ("GET", "/api/admin/events"),
            ("POST", f"/api/admin/events/{ids['warm']}/merge-into/{ids['hot']}"),
            ("POST", f"/api/admin/events/{ids['hot']}/members/h1/detach"),
        ):
            response = client.request(method, path)
            assert response.status_code == 403, (method, path)
            assert response.json()["code"] == "login_required", (method, path)
    with Session(app_module.db_sink.engine) as session:
        assert session.get(EventRecord, ids["warm"]).status == "open"  # 没有被动到


def test_reader_account_cannot_touch_admin_events(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/events").status_code == 403
        assert client.post(f"/api/admin/events/{ids['warm']}/merge-into/{ids['hot']}").status_code == 403
        assert client.post(f"/api/admin/events/{ids['hot']}/members/h1/detach").status_code == 403
        assert client.get("/api/reader/events").status_code == 200
    assert _audit_rows(app_module.db_sink.engine) == []  # 非 admin 会话的被拒请求不入审计


def test_admin_list_merge_detach_are_applied_and_audited(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        listing = client.get("/api/admin/events")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert {item["id"] for item in items} == set(ids.values())
        assert client.get("/api/admin/events?status=merged").json()["items"][0]["id"] == ids["old"]
        assert [item["id"] for item in client.get("/api/admin/events?q=warm").json()["items"]] == [ids["warm"]]
        assert client.get("/api/admin/events?status=bogus").status_code == 400
        assert client.get("/api/admin/events?limit=0").status_code == 400

        merged = client.post(f"/api/admin/events/{ids['warm']}/merge-into/{ids['hot']}")
        assert merged.status_code == 200
        assert merged.json()["merged_from"] == ids["warm"]
        assert merged.json()["event"]["id"] == ids["hot"]
        assert merged.json()["event"]["member_count"] == 5
        assert merged.json()["event"]["publisher_count"] == 4  # media-a 两篇只算一个主体

        assert client.post(f"/api/admin/events/{ids['warm']}/merge-into/{ids['hot']}").status_code == 409
        assert client.post(f"/api/admin/events/{ids['hot']}/merge-into/{ids['hot']}").status_code == 409
        assert client.post(f"/api/admin/events/999999/merge-into/{ids['hot']}").status_code == 404

        detached = client.post(f"/api/admin/events/{ids['hot']}/members/w2/detach")
        assert detached.status_code == 200
        new_id = detached.json()["event"]["id"]
        assert detached.json()["detached_from"] == ids["hot"]
        assert detached.json()["event"]["representative_article_id"] == "w2"
        assert client.post(f"/api/admin/events/{ids['hot']}/members/missing/detach").status_code == 404
        assert client.post(f"/api/admin/events/{new_id}/members/w2/detach").status_code == 409

        # 读者面立刻反映:旧 ID 指向 hot,hot 现有 4 篇,摘出的 w2 独立成事且不上榜。
        _login(client, "user", "user")
        via_warm = client.get(f"/api/reader/events/{ids['warm']}").json()
        assert via_warm["event_id"] == ids["hot"] and via_warm["merged_into"] == ids["hot"]
        assert via_warm["member_count"] == 4
        board_ids = [item["event_id"] for item in client.get("/api/reader/events").json()["items"]]
        assert board_ids == [ids["hot"], ids["week"]]

    with Session(app_module.db_sink.engine) as session:
        assert session.get(EventRedirectRecord, ids["warm"]).new_event_id == ids["hot"]
        assert json.loads(session.get(EventAssignmentLedgerRecord, "w2").blocked_event_ids_json) == [ids["hot"]]

    rows = _audit_rows(app_module.db_sink.engine)
    by_path = {(row.method, row.path, row.status_code): row for row in rows}
    merge_row = by_path[("POST", f"/api/admin/events/{ids['warm']}/merge-into/{ids['hot']}", 200)]
    assert merge_row.username == "admin"
    assert merge_row.summary == f"合并事件 {ids['warm']} → {ids['hot']}"
    assert merge_row.target == str(ids["warm"])
    detach_row = by_path[("POST", f"/api/admin/events/{ids['hot']}/members/w2/detach", 200)]
    assert detach_row.summary == f"从事件 {ids['hot']} 摘出文章 w2"
    assert detach_row.target == str(ids["hot"])
    # 被拒的管理写请求同样留痕(状态码如实记录);只读列表不入审计。
    assert ("POST", f"/api/admin/events/{ids['hot']}/merge-into/{ids['hot']}", 409) in by_path
    assert ("POST", f"/api/admin/events/999999/merge-into/{ids['hot']}", 404) in by_path
    assert all(row.method == "POST" for row in rows)


# ── 令牌化交付口(issue #310) ──


def _feed_token(client, username="user", password="user") -> str:
    _login(client, username, password)
    token = client.post("/api/reader/feed-token/rotate").json()["token"]
    assert token.startswith("dfeed_")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_public_feed_events_deliver_the_reader_board(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        token = _feed_token(client)
        reader_board = client.get("/api/reader/events").json()
        reader_detail = client.get(f"/api/reader/events/{ids['hot']}").json()
        client.cookies.clear()  # 之后只凭令牌,不带会话

        board = client.get("/api/public/feed/events", headers=_bearer(token))
        assert board.status_code == 200
        payload = board.json()
        assert set(payload) == FEED_BOARD_KEYS
        assert payload["status"] == "success" and payload["count"] == 3
        assert payload["window_hours"] == 168 and payload["empty_reason"] is None
        # 同一份榜:条目与读者面逐字段相同,默认不带成员。
        assert payload["items"] == reader_board["items"]
        assert all(set(item) == ITEM_KEYS for item in payload["items"])

        # ?token= 兜底(不能设请求头的工具)。
        via_query = client.get(f"/api/public/feed/events?token={token}")
        assert via_query.status_code == 200 and via_query.json()["items"] == payload["items"]

        near = client.get("/api/public/feed/events?window_hours=48&limit=1", headers=_bearer(token)).json()
        assert near["window_hours"] == 48 and near["count"] == 1
        assert [item["event_id"] for item in near["items"]] == [ids["hot"]]

        # include_members:每件事多带成员,形状与次序同详情;其余字段不变。
        full = client.get("/api/public/feed/events?include_members=true", headers=_bearer(token)).json()
        assert [item["event_id"] for item in full["items"]] == [ids["hot"], ids["warm"], ids["week"]]
        for item, plain in zip(full["items"], payload["items"]):
            assert set(item) == ITEM_KEYS | {"members"}
            assert {key: value for key, value in item.items() if key != "members"} == plain
            assert len(item["members"]) == item["member_count"]
            assert all(set(member) == MEMBER_KEYS for member in item["members"])
        assert full["items"][0]["members"] == reader_detail["members"]

        detail = client.get(f"/api/public/feed/events/{ids['hot']}", headers=_bearer(token))
        assert detail.status_code == 200
        body = detail.json()
        assert set(body) == FEED_DETAIL_KEYS and body["status"] == "success"
        assert set(body["event"]) == DETAIL_KEYS
        assert body["event"] == reader_detail
        assert all(set(item) == FOLLOWUP_KEYS for item in body["event"]["followups"])
        # 名次跟窗口走;合并掉的旧 ID 指到新事件;单主体事件不上榜但可按 ID 取。
        week = client.get(f"/api/public/feed/events/{ids['week']}?window_hours=48", headers=_bearer(token)).json()
        assert week["event"]["rank"] is None
        via_old = client.get(f"/api/public/feed/events/{ids['old']}", headers=_bearer(token)).json()["event"]
        assert via_old["event_id"] == ids["hot"] and via_old["merged_into"] == ids["hot"]
        single = client.get(f"/api/public/feed/events/{ids['single']}", headers=_bearer(token)).json()["event"]
        assert single["rank"] is None and single["member_count"] == 1

        assert client.get("/api/public/feed/events/999999", headers=_bearer(token)).status_code == 404
        assert client.get("/api/public/feed/events/not-a-number", headers=_bearer(token)).status_code == 422
        assert client.get("/api/public/feed/events?limit=11", headers=_bearer(token)).status_code == 400
        assert client.get("/api/public/feed/events?window_hours=0", headers=_bearer(token)).status_code == 400
        assert client.get(
            f"/api/public/feed/events/{ids['hot']}?window_hours=337", headers=_bearer(token)
        ).status_code == 400

        # 榜单是全站口径:管理员的令牌拿到同一份。
        admin_token = _feed_token(client, "admin", "admin")
        client.cookies.clear()
        assert client.get("/api/public/feed/events", headers=_bearer(admin_token)).json()["items"] == payload["items"]
    assert _audit_rows(app_module.db_sink.engine) == []  # 只读交付,不入审计


def test_public_feed_events_require_a_valid_feed_token(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    paths = ("/api/public/feed/events", f"/api/public/feed/events/{ids['hot']}")
    with TestClient(app_module.app) as client:
        for path in paths:
            assert client.get(path).status_code == 401, path
            assert client.get(f"{path}?token=dfeed_wrong").status_code == 401, path
            assert client.get(path, headers=_bearer("dfeed_wrong")).status_code == 401, path

        old_token = _feed_token(client)
        # 登录会话不顶替令牌:这条口子只认令牌。
        for path in paths:
            assert client.get(path).status_code == 401, path
        # 单订阅令牌(dsub_)不是这里的凭据。
        subscription = client.post(
            "/api/subscriptions", json={"name": "media-a", "filters": {"source_id": "media-a"}}
        ).json()
        assert subscription["token"].startswith("dsub_")
        new_token = client.post("/api/reader/feed-token/rotate").json()["token"]
        client.cookies.clear()
        for path in paths:
            assert client.get(path, headers=_bearer(subscription["token"])).status_code == 401, path
            assert client.get(path, headers=_bearer(old_token)).status_code == 401, path  # 轮换后旧令牌作废
            assert client.get(path, headers=_bearer(new_token)).status_code == 200, path

        # 令牌先于参数取值校验:没有有效令牌时不回范围类 400。
        assert client.get("/api/public/feed/events?limit=11").status_code == 401
        assert client.get(f"/api/public/feed/events/{ids['hot']}?window_hours=0").status_code == 401
        assert client.get("/api/public/feed/events/999999", headers=_bearer("dfeed_wrong")).status_code == 401


def test_public_feed_events_disabled_in_collector_role(monkeypatch, tmp_path):
    from config import RuntimeConfig

    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with TestClient(app_module.app) as client:
        token = _feed_token(client)
        client.cookies.clear()
        monkeypatch.setattr(
            app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="collector"))
        )
        for path in ("/api/public/feed/events", f"/api/public/feed/events/{ids['hot']}"):
            assert client.get(path, headers=_bearer(token)).status_code == 403, path


def test_public_feed_events_never_deliver_hidden_sources(monkeypatch, tmp_path):
    """隐藏源全量排除于交付:成员、主体数、名次都只从读者可见成员算,与读者面一致。"""

    app_module = _setup_app(monkeypatch, tmp_path)
    ids = _seed(app_module.db_sink.engine)
    with Session(app_module.db_sink.engine) as session:
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["media-b"]'))
        session.commit()
    with TestClient(app_module.app) as client:
        token = _feed_token(client)
        client.cookies.clear()
        board = client.get("/api/public/feed/events?include_members=true", headers=_bearer(token)).json()
        # hot 剩 media-a / media-c 两家;week 只剩 media-a 一家,掉出榜单。
        assert [item["event_id"] for item in board["items"]] == [ids["hot"], ids["warm"]]
        hot = board["items"][0]
        assert hot["publisher_count"] == 2 and hot["member_count"] == 2
        assert {member["source_id"] for member in hot["members"]} == {"media-a", "media-c"}
        assert "media-b" not in json.dumps(board)

        detail = client.get(f"/api/public/feed/events/{ids['hot']}", headers=_bearer(token)).json()["event"]
        assert {member["article_id"] for member in detail["members"]} == {"h1", "h3"}
        week = client.get(f"/api/public/feed/events/{ids['week']}", headers=_bearer(token)).json()["event"]
        assert week["rank"] is None and {member["source_id"] for member in week["members"]} == {"media-a"}
