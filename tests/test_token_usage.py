"""令牌交付计量(issue #312):服务层口径 + 各交付路径的接线 + 管理面 / 读者面的读接口。

要钉住的是那条关系:账户 → 它的令牌 → 接口调用与调用量。显式建 tmp sink;造数据用相对日期。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

from models.db import (  # noqa: E402
    ArticleRecord,
    ReaderFeedTokenRecord,
    ReaderSubscriptionRecord,
    TokenDeliveryUsageRecord,
)
from services import token_usage  # noqa: E402
from services.token_usage import TokenIdentity  # noqa: E402


def _day(offset_days: int = 0) -> str:
    return (dt.date.today() - dt.timedelta(days=offset_days)).isoformat()


def _engine(tmp_path, name="token_usage.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}").engine


def _feed(username: str, token_hash: str, preview: str = "...aaaaaa") -> TokenIdentity:
    return TokenIdentity(username=username, kind=token_usage.KIND_FEED, token_hash=token_hash, preview=preview)


def _rows(engine) -> list[TokenDeliveryUsageRecord]:
    with Session(engine) as session:
        return list(session.exec(select(TokenDeliveryUsageRecord).order_by(TokenDeliveryUsageRecord.id)).all())


def _feed_token_row(session, username: str, token_hash: str, preview: str) -> None:
    now = dt.datetime.now().isoformat()
    record = session.get(ReaderFeedTokenRecord, username)
    if record is None:
        record = ReaderFeedTokenRecord(owner_username=username, token_hash=token_hash, token_preview=preview,
                                       created_at=now, updated_at=now)
    record.token_hash, record.token_preview, record.updated_at = token_hash, preview, now
    session.add(record)
    session.commit()


# ── 服务层 ───────────────────────────────────────────────────────────────────

def test_record_call_accumulates_per_day_token_and_channel(tmp_path):
    engine = _engine(tmp_path)
    alice = _feed("alice", "a" * 64)
    with Session(engine) as session:
        token_usage.record_call(session, alice, token_usage.FEED_ARTICLES, items=20)
        token_usage.record_call(session, alice, token_usage.FEED_ARTICLES, items=5)
        token_usage.record_call(session, alice, token_usage.FEED_EVENTS, items=10)
        token_usage.record_call(session, alice, token_usage.FEED_ARTICLES, items=7, day=_day(1))
        token_usage.record_call(session, _feed("alice", "b" * 64, "...bbbbbb"), token_usage.FEED_ARTICLES, items=1)

    rows = {(r.day, r.token_fp, r.channel): r for r in _rows(engine)}
    assert len(rows) == 4
    today = rows[(_day(), "a" * 16, token_usage.FEED_ARTICLES)]
    assert (today.calls, today.items, today.rejected) == (2, 25, 0)
    assert today.username == "alice" and today.token_kind == "feed" and today.token_preview == "...aaaaaa"
    assert today.last_at and today.subscription_id is None
    assert rows[(_day(), "a" * 16, token_usage.FEED_EVENTS)].calls == 1
    assert rows[(_day(1), "a" * 16, token_usage.FEED_ARTICLES)].items == 7
    assert rows[(_day(), "b" * 16, token_usage.FEED_ARTICLES)].token_preview == "...bbbbbb"


def test_tokens_without_an_owner_are_not_recorded(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        token_usage.record_call(session, _feed("", "a" * 64), token_usage.FEED_ARTICLES, items=3)
        token_usage.record_call(session, _feed("alice", ""), token_usage.FEED_ARTICLES, items=3)
    assert _rows(engine) == []


def test_rejected_is_recorded_only_for_tokens_seen_before(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        # 认不出的令牌:随手试出来的字符串不留行。
        assert token_usage.record_rejected(session, "f" * 64, token_usage.FEED_ARTICLES) is False
    assert _rows(engine) == []

    with Session(engine) as session:
        token_usage.record_call(session, _feed("alice", "a" * 64), token_usage.FEED_ARTICLES, items=4, day=_day(3))
        assert token_usage.record_rejected(session, "a" * 64, token_usage.FEED_EVENTS) is True
        assert token_usage.record_rejected(session, "a" * 64, token_usage.FEED_EVENTS) is True

    rows = {(r.day, r.channel): r for r in _rows(engine)}
    old = rows[(_day(3), token_usage.FEED_ARTICLES)]
    assert (old.calls, old.rejected) == (1, 0)          # 以前的成功记录不动
    rejected = rows[(_day(), token_usage.FEED_EVENTS)]
    assert (rejected.calls, rejected.items, rejected.rejected) == (0, 0, 2)
    assert rejected.username == "alice" and rejected.token_preview == "...aaaaaa"
    assert rejected.last_at is None                      # last_at 只记成功调用


def test_summarize_user_links_account_tokens_and_channels(tmp_path):
    """重新生成过令牌的账号:现用的一枚排前;旧的一枚留着自己的记录、标已作废、带作废后被拒的次数。"""
    engine = _engine(tmp_path)
    old_hash, new_hash = "1" * 64, "2" * 64
    with Session(engine) as session:
        old = _feed("alice", old_hash, "...old111")
        token_usage.record_call(session, old, token_usage.FEED_ARTICLES, items=100, day=_day(9))
        token_usage.record_call(session, old, token_usage.FEED_ARTICLES_MARKDOWN, items=60, day=_day(9))
        token_usage.record_call(session, old, token_usage.FEED_ARTICLES, items=90, day=_day(8))
        _feed_token_row(session, "alice", new_hash, "...new222")           # 重新生成
        token_usage.record_rejected(session, old_hash, token_usage.FEED_ARTICLES, day=_day(7))
        new = _feed("alice", new_hash, "...new222")
        token_usage.record_call(session, new, token_usage.FEED_EVENTS, items=10, day=_day(1))
        token_usage.record_call(session, new, token_usage.mcp_channel("search_articles"), items=8)
        token_usage.record_call(session, _feed("bob", "9" * 64), token_usage.FEED_ARTICLES, items=1)

        summary = token_usage.summarize_user(session, "alice", days=30)

    assert summary["window_days"] == 30
    assert summary["totals"] == {"calls": 5, "items": 268}
    current, retired = summary["tokens"]
    assert (current["preview"], current["status"], current["kind"]) == ("...new222", "current", "feed")
    assert (current["calls"], current["items"], current["rejected"]) == (2, 18, 0)
    assert {c["channel"]: (c["group"], c["calls"], c["items"]) for c in current["channels"]} == {
        "feed/events": ("events", 1, 10),
        "mcp:search_articles": ("mcp", 1, 8),
    }
    assert (retired["preview"], retired["status"]) == ("...old111", "retired")
    assert (retired["calls"], retired["items"], retired["rejected"]) == (3, 250, 1)
    assert [c["channel"] for c in retired["channels"]] == ["feed/articles", "feed/articles.md"]  # 调得多的在前
    assert retired["last_at"] and current["last_at"] and retired["subscription"] is None
    assert {(d["day"], d["group"]): (d["calls"], d["items"]) for d in summary["by_day"]} == {
        (_day(9), "articles"): (2, 160),
        (_day(8), "articles"): (1, 90),
        (_day(1), "events"): (1, 10),
        (_day(), "mcp"): (1, 8),
    }


def test_summarize_user_lists_an_unused_feed_token_but_not_unused_subscription_tokens(tmp_path):
    engine = _engine(tmp_path)
    now = dt.datetime.now().isoformat()
    with Session(engine) as session:
        _feed_token_row(session, "alice", "3" * 64, "...feed33")
        used = ReaderSubscriptionRecord(owner_username="alice", name="OpenAI 博客", token_hash="4" * 64,
                                        token_preview="...sub444", created_at=now, updated_at=now)
        idle = ReaderSubscriptionRecord(owner_username="alice", name="没人调的订阅", token_hash="5" * 64,
                                        token_preview="...sub555", created_at=now, updated_at=now)
        session.add(used)
        session.add(idle)
        session.commit()
        token_usage.record_call(
            session,
            TokenIdentity(username="alice", kind=token_usage.KIND_SUBSCRIPTION, token_hash="4" * 64,
                          preview="...sub444", subscription_id=used.id),
            token_usage.SUBSCRIPTION_ARTICLES, items=20,
        )
        summary = token_usage.summarize_user(session, "alice", days=30)
        tokens = {t["preview"]: t for t in summary["tokens"]}
        assert set(tokens) == {"...feed33", "...sub444"}                  # 没调过的订阅令牌不列
        assert (tokens["...feed33"]["status"], tokens["...feed33"]["calls"], tokens["...feed33"]["channels"]) == ("current", 0, [])
        assert tokens["...sub444"]["subscription"] == {"id": used.id, "name": "OpenAI 博客"}
        assert tokens["...sub444"]["status"] == "current"

        # 订阅停用 / 换了令牌 / 删掉之后,它名下的记录还在,状态变成已作废。
        used.is_active = False
        session.add(used)
        session.commit()
        assert {t["preview"]: t["status"] for t in token_usage.summarize_user(session, "alice")["tokens"]}["...sub444"] == "retired"
        session.delete(used)
        session.commit()
        gone = {t["preview"]: t for t in token_usage.summarize_user(session, "alice")["tokens"]}["...sub444"]
        assert gone["status"] == "retired" and gone["subscription"] == {"id": gone["subscription"]["id"], "name": ""}


def test_window_reads_skip_old_rows_tombstones_and_guests(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        token_usage.record_call(session, _feed("alice", "a" * 64), token_usage.FEED_ARTICLES, items=5)
        token_usage.record_call(session, _feed("alice", "a" * 64), token_usage.FEED_ARTICLES, items=5, day=_day(45))
        token_usage.record_call(session, _feed("bob", "b" * 64), token_usage.FEED_EVENTS, items=2, day=_day(2))
        token_usage.record_call(session, _feed("deleted:carol", "c" * 64), token_usage.FEED_ARTICLES, items=9)
        token_usage.record_call(session, _feed("guest:shared", "d" * 64), token_usage.FEED_ARTICLES, items=9)
        token_usage.record_rejected(session, "b" * 64, token_usage.FEED_EVENTS)      # 被拒不算调用

        assert token_usage.calls_by_user(session, days=30) == {"alice": 1, "bob": 1}
        assert token_usage.window_summary(session, days=30) == {"calls": 2, "items": 7, "tokens_active": 2}
        assert token_usage.calls_by_user(session, days=90) == {"alice": 2, "bob": 1}
        assert token_usage.window_summary(session, days=1) == {"calls": 1, "items": 5, "tokens_active": 1}

        usage = token_usage.feed_token_usage(session, "alice", "a" * 64, days=30)
        assert usage["calls"] == 1 and usage["window_days"] == 30 and usage["last_used_at"]
        assert token_usage.feed_token_usage(session, "alice", None) == {"window_days": 30, "calls": 0, "last_used_at": None}


def test_delete_recreate_delete_merges_usage_into_the_tombstone(tmp_path):
    from services.accounts import create_user, delete_user

    engine = _engine(tmp_path)
    with Session(engine) as session:
        create_user(session, "repeat", "pw123456", "user")
        token_usage.record_call(session, _feed("repeat", "a" * 64), token_usage.FEED_ARTICLES, items=10)
        delete_user(session, "repeat")
        create_user(session, "repeat", "pw123456", "user")
        token_usage.record_call(session, _feed("repeat", "a" * 64), token_usage.FEED_ARTICLES, items=25)
        token_usage.record_rejected(session, "a" * 64, token_usage.FEED_ARTICLES)
        delete_user(session, "repeat")       # 同名二次删号不得撞聚合键唯一索引

    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0].username == "deleted:repeat"
    assert (rows[0].calls, rows[0].items, rows[0].rejected) == (2, 35, 1)


def test_retention_keeps_a_year_of_usage(tmp_path):
    from services import retention

    engine = _engine(tmp_path)
    with Session(engine) as session:
        token_usage.record_call(session, _feed("alice", "a" * 64), token_usage.FEED_ARTICLES, items=1, day=_day(400))
        token_usage.record_call(session, _feed("alice", "a" * 64), token_usage.FEED_ARTICLES, items=1, day=_day(300))
    result = retention.run_retention_cleanup(engine)
    assert result["token_delivery_usage"] == 1
    assert [row.day for row in _rows(engine)] == [_day(300)]


# ── 接线:各交付路径 ───────────────────────────────────────────────────────────

def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'token_usage_api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all")))
    seed_default_accounts(sink.engine)
    stamp = dt.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for index in range(3):
            session.add(ArticleRecord(
                id=f"openai_{index}", title=f"OpenAI Update {index}", content_type="rss_article",
                source_id="rss_openai", source_url=f"https://example.test/openai/{index}",
                publish_date=stamp, fetched_date=stamp, content="OpenAI ships a thing", extensions_json="{}",
            ))
        session.commit()
    return app_module


def _login(client, username="admin", password=None):
    response = client.post("/api/auth/login", json={"username": username, "password": password or username})
    assert response.status_code == 200, response.text


def _rotate(client) -> str:
    token = client.post("/api/reader/feed-token/rotate").json()["token"]
    assert token.startswith("dfeed_")
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _usage(app_module) -> dict[tuple[str, str, str], TokenDeliveryUsageRecord]:
    return {(r.username, r.token_preview, r.channel): r for r in _rows(app_module.db_sink.engine)}


def test_feed_token_calls_are_metered_per_endpoint(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user")
        token = _rotate(client)
        preview = f"...{token[-6:]}"
        client.cookies.clear()                      # 往后只凭令牌

        articles = client.get("/api/public/feed/articles", headers=_bearer(token))
        assert articles.status_code == 200
        limited = client.get("/api/public/feed/articles?limit=1", headers=_bearer(token))
        assert limited.status_code == 200
        assert client.get("/api/public/feed/articles.md", headers=_bearer(token)).status_code == 200
        assert client.get(f"/api/public/feed/articles.xml?token={token}").status_code == 200
        board = client.get("/api/public/feed/events", headers=_bearer(token))
        assert board.status_code == 200
        # 参数不合法、事件不存在:没有成功返回,不算调用。
        assert client.get("/api/public/feed/events?window_hours=9999", headers=_bearer(token)).status_code == 400
        assert client.get("/api/public/feed/events/424242", headers=_bearer(token)).status_code == 404

    usage = _usage(app_module)
    assert {key[2]: (row.calls, row.items) for key, row in usage.items()} == {
        "feed/articles": (2, articles.json()["count"] + limited.json()["count"]),
        "feed/articles.md": (1, articles.json()["count"]),
        "feed/articles.xml": (1, articles.json()["count"]),
        "feed/events": (1, board.json()["count"]),
    }
    assert {key[:2] for key in usage} == {("user", preview)}
    assert all(row.token_kind == "feed" and row.rejected == 0 and row.last_at for row in usage.values())


def test_unknown_tokens_leave_no_rows_and_a_rotated_token_counts_as_rejected(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user")
        old = _rotate(client)
        client.cookies.clear()
        for headers in ({}, _bearer("dfeed_not_a_real_token"), _bearer("")):
            assert client.get("/api/public/feed/articles", headers=headers).status_code == 401
            assert client.get("/api/public/feed/events", headers=headers).status_code == 401
        assert _rows(app_module.db_sink.engine) == []

        assert client.get("/api/public/feed/articles", headers=_bearer(old)).status_code == 200
        _login(client, "user")
        new = _rotate(client)
        client.cookies.clear()
        assert client.get("/api/public/feed/articles", headers=_bearer(old)).status_code == 401
        assert client.get("/api/public/feed/events", headers=_bearer(old)).status_code == 401
        assert client.get("/api/public/feed/events/1", headers=_bearer(old)).status_code == 401
        assert client.get("/api/public/feed/articles", headers=_bearer(new)).status_code == 200

    usage = _usage(app_module)
    old_preview, new_preview = f"...{old[-6:]}", f"...{new[-6:]}"
    assert (usage[("user", old_preview, "feed/articles")].calls, usage[("user", old_preview, "feed/articles")].rejected) == (1, 1)
    assert (usage[("user", old_preview, "feed/events")].calls, usage[("user", old_preview, "feed/events")].rejected) == (0, 1)
    assert usage[("user", old_preview, "feed/events/{id}")].rejected == 1
    assert (usage[("user", new_preview, "feed/articles")].calls, usage[("user", new_preview, "feed/articles")].rejected) == (1, 0)


def test_subscription_token_calls_are_metered_under_the_subscription(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        sub = client.post("/api/subscriptions", json={"name": "OpenAI feed", "filters": {"source_id": "rss_openai"}}).json()
        client.cookies.clear()
        url = f"/api/public/subscriptions/{sub['id']}/articles"
        first = client.get(url, headers=_bearer(sub["token"]))
        assert first.status_code == 200 and first.json()["count"] == 3
        assert client.get(f"{url}?limit=2", headers=_bearer(sub["token"])).status_code == 200
        assert client.get(url, headers=_bearer("dsub_wrong")).status_code == 401
        search = client.post(
            f"/api/public/subscriptions/{sub['id']}/vector/search", json={"query": "OpenAI"}, headers=_bearer(sub["token"]),
        )
        assert search.status_code == 200

        _login(client, "admin")
        rotated = client.post(f"/api/subscriptions/{sub['id']}/rotate-token").json()
        client.cookies.clear()
        assert client.get(url, headers=_bearer(sub["token"])).status_code == 401       # 旧令牌:记一次被拒
        assert client.post(
            f"/api/public/subscriptions/{sub['id']}/vector/search", json={"query": "OpenAI"}, headers=_bearer(sub["token"]),
        ).status_code == 401
        assert client.get(url, headers=_bearer(rotated["token"])).status_code == 200

    usage = _usage(app_module)
    old = usage[("admin", sub["token_preview"], "subscriptions/{id}/articles")]
    assert (old.token_kind, old.subscription_id, old.calls, old.items, old.rejected) == ("subscription", sub["id"], 2, 5, 1)
    searched = usage[("admin", sub["token_preview"], "subscriptions/{id}/vector/search")]
    assert (searched.calls, searched.items, searched.rejected) == (1, search.json()["count"], 1)
    assert usage[("admin", rotated["token_preview"], "subscriptions/{id}/articles")].calls == 1
    assert len(usage) == 3                                                             # 错令牌没有留行


def test_mcp_tool_calls_are_metered(monkeypatch, tmp_path):
    from api.delivery_metering import record_mcp_usage
    from api.feed_service import resolve_subscription_sources_by_token
    from mcp_server import build_mcp_app

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        token = _rotate(client)
    mcp = build_mcp_app(
        app_module.db_sink, subscription_resolver=resolve_subscription_sources_by_token, usage_recorder=record_mcp_usage,
    )

    async def _drive():
        await mcp.call_tool("browse_articles", {"subscription_token": token, "limit": 2})
        await mcp.call_tool("search_articles", {"query": "OpenAI", "subscription_token": token})
        await mcp.call_tool("get_article", {"article_id": "openai_0", "subscription_token": token})
        await mcp.call_tool("get_article", {"article_id": "no_such_article", "subscription_token": token})
        await mcp.call_tool("get_rag_context", {"query": "OpenAI", "subscription_token": token})
        await mcp.call_tool("list_sources", {})                                           # 不需要令牌,不计
        await mcp.call_tool("browse_articles", {"subscription_token": "dfeed_unknown"})   # 认不出,不留行
        await mcp.call_tool("browse_articles", {})                                         # 没带令牌

    asyncio.run(_drive())

    usage = {row.channel: row for row in _rows(app_module.db_sink.engine)}
    assert set(usage) == {"mcp:browse_articles", "mcp:search_articles", "mcp:get_article", "mcp:get_rag_context"}
    assert all(row.username == "admin" and row.token_kind == "feed" and row.rejected == 0 for row in usage.values())
    assert (usage["mcp:browse_articles"].calls, usage["mcp:browse_articles"].items) == (1, 2)
    assert (usage["mcp:get_article"].calls, usage["mcp:get_article"].items) == (2, 1)      # 没找到的那次:算调用,0 条
    assert usage["mcp:search_articles"].calls == 1 and usage["mcp:get_rag_context"].calls == 1


def test_metering_failure_never_breaks_delivery(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("usage table is on fire")

    with TestClient(app_module.app) as client:
        _login(client, "user")
        token = _rotate(client)
        client.cookies.clear()
        monkeypatch.setattr(token_usage, "record_call", _boom)
        monkeypatch.setattr(token_usage, "record_rejected", _boom)
        assert client.get("/api/public/feed/articles", headers=_bearer(token)).status_code == 200
        assert client.get("/api/public/feed/events", headers=_bearer(token)).status_code == 200
        assert client.get("/api/public/feed/articles", headers=_bearer("dfeed_nope")).status_code == 401
    assert _rows(app_module.db_sink.engine) == []


# ── 读接口:管理面账户列表 / 抽屉、读者自己的令牌卡 ────────────────────────────

def test_admin_sees_account_token_channel_chain(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user")
        token = _rotate(client)
        status = client.get("/api/reader/feed-token").json()
        assert (status["last_used_at"], status["usage_calls"], status["usage_window_days"]) == (None, 0, 30)
        client.cookies.clear()
        for _ in range(3):
            assert client.get("/api/public/feed/articles", headers=_bearer(token)).status_code == 200
        assert client.get("/api/public/feed/events", headers=_bearer(token)).status_code == 200

        _login(client, "user")
        status = client.get("/api/reader/feed-token").json()
        assert status["usage_calls"] == 4 and status["last_used_at"]

        _login(client, "admin")
        listing = client.get("/api/admin/accounts?sort=api").json()
        assert [(item["username"], item["api_calls"]) for item in listing["items"]] == [("user", 4), ("admin", 0)]
        assert listing["summary"]["api_calls"] == 4
        assert listing["summary"]["api_tokens_active"] == 1
        assert listing["summary"]["top_api"] == [{"username": "user", "value": 4}]
        ascending = client.get("/api/admin/accounts?sort=api&order=asc").json()
        assert [item["username"] for item in ascending["items"]] == ["admin", "user"]

        delivery = client.get("/api/admin/accounts/user/activity").json()["delivery"]
        assert delivery["totals"]["calls"] == 4 and delivery["window_days"] == 30
        (only,) = delivery["tokens"]
        assert (only["kind"], only["preview"], only["status"], only["calls"]) == ("feed", f"...{token[-6:]}", "current", 4)
        assert [(c["channel"], c["group"], c["calls"]) for c in only["channels"]] == [
            ("feed/articles", "articles", 3), ("feed/events", "events", 1),
        ]
        assert {(d["group"], d["calls"]) for d in delivery["by_day"]} == {("articles", 3), ("events", 1)}
        assert "token_hash" not in only                                    # 哈希不出接口,只给指纹与尾号

        # 没有令牌的账号:空清单,界面据此不出这一区。
        assert client.get("/api/admin/accounts/admin/activity").json()["delivery"]["tokens"] == []

        # 重新生成之后,读者卡从零算起;抽屉里旧的一枚还在、标已作废。
        _login(client, "user")
        _rotate(client)
        status = client.get("/api/reader/feed-token").json()
        assert (status["usage_calls"], status["last_used_at"]) == (0, None)
        _login(client, "admin")
        tokens = client.get("/api/admin/accounts/user/activity").json()["delivery"]["tokens"]
        assert [(t["status"], t["calls"]) for t in tokens] == [("current", 0), ("retired", 4)]
