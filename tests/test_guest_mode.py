"""游客模式(issue #213)回归测试。

覆盖:游客会话签发 / 续用访客身份 / 总闸吊销;白名单门控(读放行、账户写一律 403 login_required);
保留账号不进账户管理面与统计;保留账号订阅镜像默认名单;已读与 AI 额度按访客分开;
媒体代理对游客只取缓存;登录即记转化;运维游客区取数与设置。
"""

import datetime
import os
import sys
from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"))
_SOURCE = "rss_openai_news"  # 代码缺省默认名单里的一个来源


def _setup_app(monkeypatch, tmp_path, *, articles=3):
    import api.app as app_module
    from config import RuntimeConfig
    from models.db import ArticleRecord

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_guest.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)
    now = datetime.datetime.now()
    with Session(sink.engine) as session:
        for i in range(articles):
            stamp = (now - datetime.timedelta(hours=i + 1)).isoformat()
            session.add(ArticleRecord(
                id=f"g{i}", title=f"游客测试文章 {i}", content="正文",
                content_type="rss_article", source_id=_SOURCE, source_url=f"http://example.com/g{i}",
                publish_date=stamp, fetched_date=stamp,
            ))
        session.commit()
    return app_module


def _guest(client, entry="deep_link"):
    return client.post("/api/auth/guest", json={"entry": entry})


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_guest_session_lifecycle(monkeypatch, tmp_path):
    from models.db import GuestVisitRecord
    from services import accounts as accounts_service

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/auth/session").json()["guest_access_enabled"] is True
        resp = _guest(client)
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["role"] == "guest" and user["display_name"] == "游客"
        assert user["username"].startswith(accounts_service.GUEST_VISITOR_PREFIX)
        assert user["interest_onboarding_completed"] is True

        session_payload = client.get("/api/auth/session").json()
        assert session_payload["authenticated"] is True and session_payload["guest"] is True
        runtime = client.get("/api/runtime").json()
        assert runtime["account_role"] == "guest"
        assert runtime["reader_enabled"] is True and runtime["collector_enabled"] is False
        assert runtime["root_admin"] is False

        # 同一浏览器再进一次沿用同一访客身份
        again = _guest(client, entry="login_page").json()["user"]["username"]
        assert again == user["username"]

    vid = user["username"][len(accounts_service.GUEST_VISITOR_PREFIX):]
    with Session(app_module.db_sink.engine) as session:
        rows = session.exec(select(GuestVisitRecord).where(GuestVisitRecord.visitor_id == vid)).all()
    assert len(rows) == 1
    assert rows[0].entry == "deep_link"  # 当天首个入口不被后来的入口覆盖
    assert rows[0].visits == 1           # 打开一次应用(拉 runtime)= 一次访问


def test_guest_whitelist_allows_reading_and_blocks_account_writes(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _guest(client).status_code == 200
        for path in (
            "/api/articles",
            "/api/reader/sources",
            "/api/reader/favorites",
            "/api/reader/unread-counts",
            "/api/reader/announcements",
        ):
            assert client.get(path).status_code == 200, path
        assert client.get("/api/articles/g0").status_code == 200

        blocked = [
            ("POST", f"/api/reader/sources/{_SOURCE}/subscribe"),
            ("DELETE", f"/api/reader/sources/{_SOURCE}/subscribe"),
            ("POST", "/api/reader/favorites/g0"),
            ("POST", "/api/reader/articles/g0/share"),
            ("GET", "/api/reader/feed-token"),
            ("GET", "/api/reader/shares"),
            ("POST", "/api/reader/feedback"),
            ("PUT", "/api/reader/interests"),
            ("POST", "/api/reader/briefs/today/rebuild"),
            ("POST", "/api/auth/change-password"),
            ("GET", "/api/subscriptions"),
            ("GET", "/api/admin/overview"),
            ("GET", "/api/accounts"),
            ("GET", "/api/mcp/status"),
            ("GET", "/api/source-configs"),
            ("POST", "/api/fetch/batch"),
        ]
        for method, path in blocked:
            resp = client.request(method, path, json={})
            assert resp.status_code == 403, (method, path, resp.status_code)
            assert resp.json()["code"] == "login_required", (method, path)
        # MCP 端点不在 /api 下,同样拒绝
        assert client.post("/mcp", json={}).status_code == 403


def test_guest_switch_off_revokes_live_sessions(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as guest, TestClient(app_module.app) as admin:
        assert _guest(guest).status_code == 200
        assert guest.get("/api/runtime").status_code == 200
        assert _login(admin, "admin", "admin").status_code == 200
        assert admin.put("/api/admin/guest/settings", json={"enabled": False}).status_code == 200

        assert guest.get("/api/runtime").status_code == 401
        refused = _guest(guest)
        assert refused.status_code == 403 and refused.json()["code"] == "guest_disabled"
        anon = TestClient(app_module.app)
        assert anon.get("/api/auth/session").json()["guest_access_enabled"] is False
        # 重新打开后可再进,旧令牌因世代轮换不复活
        assert admin.put("/api/admin/guest/settings", json={"enabled": True}).status_code == 200
        assert guest.get("/api/runtime").status_code == 401
        assert _guest(guest).status_code == 200
        assert guest.get("/api/runtime").status_code == 200


def test_logged_in_account_cannot_downgrade_to_guest(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _login(client, "alice", "alice").status_code == 200
        resp = _guest(client)
        assert resp.status_code == 409
        assert client.get("/api/runtime").json()["account_role"] == "user"


def test_guest_account_excluded_from_account_management(monkeypatch, tmp_path):
    from services import accounts as accounts_service

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _login(client, "admin", "admin").status_code == 200
        names = {row["username"] for row in client.get("/api/admin/accounts").json()["items"]}
        assert accounts_service.GUEST_USERNAME not in names and names == {"admin", "alice"}
        assert {row["username"] for row in client.get("/api/accounts").json()} == {"admin", "alice"}
        growth = client.get("/api/admin/account-growth").json()
        assert growth["totals"]["accounts"] == 2
        assert client.get(f"/api/admin/accounts/{accounts_service.GUEST_USERNAME}/activity").status_code == 404
        # 保留账号不能被当成账户来改 / 删 / 登录
        assert client.put(f"/api/accounts/{accounts_service.GUEST_USERNAME}", json={"is_active": True}).status_code in (400, 404)
        assert client.delete(f"/api/accounts/{accounts_service.GUEST_USERNAME}").status_code in (400, 404)
    with TestClient(app_module.app) as client:
        assert _login(client, accounts_service.GUEST_USERNAME, "!guest").status_code == 401


def test_guest_subscriptions_mirror_default_list(monkeypatch, tmp_path):
    from api.sources import subscription_source_ids
    from models.db import ReaderSubscriptionRecord
    from services import accounts as accounts_service
    from services import reader_defaults

    app_module = _setup_app(monkeypatch, tmp_path)

    def guest_scope():
        with Session(app_module.db_sink.engine) as session:
            rows = session.exec(select(ReaderSubscriptionRecord).where(
                ReaderSubscriptionRecord.owner_username == accounts_service.GUEST_USERNAME)).all()
            return sorted(sid for row in rows for sid in subscription_source_ids(row))

    with TestClient(app_module.app) as client:
        assert guest_scope() == sorted(reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS)
        assert _login(client, "admin", "admin").status_code == 200
        assert client.post("/api/admin/reader-defaults", json={"source_ids": [_SOURCE]}).status_code == 200
        assert guest_scope() == [_SOURCE]
        # 游客的「我的订阅」就是这份名单;发现页订阅人数不计游客
        with TestClient(app_module.app) as guest:
            assert _guest(guest).status_code == 200
            sources = guest.get("/api/reader/sources").json()
            assert sources["subscribed_source_ids"] == [_SOURCE]
            counts = {row["source_id"]: row.get("subscriber_count", 0) for row in sources.get("sources", [])}
            with Session(app_module.db_sink.engine) as session:
                real_owners = {
                    row.owner_username for row in session.exec(select(ReaderSubscriptionRecord)).all()
                    if _SOURCE in subscription_source_ids(row)
                    and not accounts_service.is_guest_username(row.owner_username)
                }
            assert counts.get(_SOURCE, 0) == len(real_owners)  # 只算真实账户(admin 登录时播种了默认名单)


def test_guest_read_state_is_per_visitor(monkeypatch, tmp_path):
    from services import accounts as accounts_service
    from services import reader_defaults

    app_module = _setup_app(monkeypatch, tmp_path, articles=3)
    with Session(app_module.db_sink.engine) as session:
        reader_defaults.set_default_source_ids(session, [_SOURCE])
    with TestClient(app_module.app) as a, TestClient(app_module.app) as b:
        assert _guest(a).status_code == 200 and _guest(b).status_code == 200
        assert a.get("/api/auth/session").json()["user"]["username"] != b.get("/api/auth/session").json()["user"]["username"]
        assert a.get("/api/reader/unread-counts").json()["total"] == 3
        assert b.get("/api/reader/unread-counts").json()["total"] == 3
        assert a.post("/api/reader/articles/g0/mark-read").status_code == 200
        assert a.get("/api/reader/unread-counts").json()["total"] == 2
        assert b.get("/api/reader/unread-counts").json()["total"] == 3
        # 打开文章:逐篇已读记访客名下,阅读计量也按访客记
        assert b.post("/api/reader/articles/g1/read").json()["status"] == "ok"
        assert b.get("/api/reader/unread-counts").json()["total"] == 2
    from models.db import ReaderReadRecord
    with Session(app_module.db_sink.engine) as session:
        owners = {row.username for row in session.exec(select(ReaderReadRecord)).all()}
    assert owners and all(o.startswith(accounts_service.GUEST_VISITOR_PREFIX) for o in owners)


def test_guest_ai_quota_counts_per_visitor(monkeypatch, tmp_path):
    from api.routers import reader as reader_router
    from models.db import AiUsageRecord
    from services import accounts as accounts_service
    from services import guest_access

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app):
        pass  # lifespan 建好游客保留账号
    engine = app_module.db_sink.engine
    a = accounts_service.guest_visitor_username("visitorAAAAAAAAAAAAAAA")
    b = accounts_service.guest_visitor_username("visitorBBBBBBBBBBBBBBB")
    limit = reader_router._AI_DAILY_CALL_LIMITS["ask"]
    today = datetime.date.today().isoformat()
    with Session(engine) as session:
        session.add(AiUsageRecord(day=today, username=a, purpose="ask", model="m", calls=limit,
                                  prompt_tokens=0, completion_tokens=0, total_tokens=0,
                                  updated_at=datetime.datetime.now().isoformat()))
        session.commit()
    with pytest.raises(guest_access.GuestQuotaExhausted):
        reader_router._enforce_ai_daily_quota(a, "ask")
    reader_router._enforce_ai_daily_quota(b, "ask")  # 另一位访客不受影响
    # 管理员调高后同一访客恢复;0 = 不限
    with Session(engine) as session:
        guest_access.set_guest_ai_limit_overrides(session, {"ask": limit + 1})
    reader_router._enforce_ai_daily_quota(a, "ask")
    with Session(engine) as session:
        guest_access.set_guest_ai_limit_overrides(session, {"ask": 0})
    reader_router._enforce_ai_daily_quota(a, "ask")
    # 正式读者的额度不受游客设置影响,超限仍是「明日再试」
    with Session(engine) as session:
        session.add(AiUsageRecord(day=today, username="alice", purpose="ask", model="m", calls=limit,
                                  prompt_tokens=0, completion_tokens=0, total_tokens=0,
                                  updated_at=datetime.datetime.now().isoformat()))
        session.commit()
    with pytest.raises(HTTPException) as exc:
        reader_router._enforce_ai_daily_quota("alice", "ask")
    assert exc.value.status_code == 429


def test_guest_quota_exhausted_response_carries_code(monkeypatch, tmp_path):
    from config import LLMConfig
    from api.routers import reader as reader_router
    from services import daily_brief as daily_brief_service

    app_module = _setup_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        daily_brief_service, "resolve_llm_config",
        lambda session: LLMConfig(base_url="http://llm.invalid", api_key="k", model="m"),
    )

    def exhausted(username, purpose):
        from services import guest_access
        raise guest_access.GuestQuotaExhausted(retry_after=60)

    monkeypatch.setattr(reader_router, "_enforce_ai_daily_quota", exhausted)
    with TestClient(app_module.app) as client:
        assert _guest(client).status_code == 200
        resp = client.post("/api/reader/ai/ask", json={"question": "hi", "scope": "article", "article_id": "g0"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "guest_quota_exhausted"
        assert resp.headers.get("Retry-After") == "60"


def test_guest_ai_follows_guest_ai_switch(monkeypatch, tmp_path):
    from config import LLMConfig
    from services import daily_brief as daily_brief_service

    app_module = _setup_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        daily_brief_service, "resolve_llm_config",
        lambda session: LLMConfig(base_url="http://llm.invalid", api_key="k", model="m"),
    )
    with TestClient(app_module.app) as guest, TestClient(app_module.app) as admin:
        assert _guest(guest).status_code == 200
        assert guest.get("/api/runtime").json()["ai_beta_enabled"] is True
        assert _login(admin, "admin", "admin").status_code == 200
        assert admin.put("/api/admin/guest/settings", json={"ai_enabled": False}).status_code == 200
        assert guest.get("/api/runtime").json()["ai_beta_enabled"] is False
        resp = guest.post("/api/reader/ai/ask", json={"question": "hi", "scope": "article", "article_id": "g0"})
        assert resp.status_code == 403


def test_guest_media_proxy_only_serves_cache(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    calls = {"fetch": 0, "cached": 0}

    class FakeStore:
        object_storage = None
        engine = None
        namespace = "media"

        async def get_cached(self, url):
            calls["cached"] += 1
            return None

        async def get_or_fetch(self, url, force=False):
            calls["fetch"] += 1
            return None

    monkeypatch.setattr(app_module, "media_store", FakeStore())
    with TestClient(app_module.app) as client:
        assert _guest(client).status_code == 200
        resp = client.get("/api/media/proxy", params={"url": "https://img.example.com/a.png"}, follow_redirects=False)
        assert resp.status_code == 302
        assert calls == {"fetch": 0, "cached": 1}


def test_guest_login_records_conversion(monkeypatch, tmp_path):
    from models.db import GuestVisitRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _guest(client).status_code == 200
        assert _login(client, "alice", "alice").status_code == 200
        assert client.get("/api/runtime").json()["account_role"] == "user"
    with Session(app_module.db_sink.engine) as session:
        row = session.exec(select(GuestVisitRecord)).one()
    assert row.converted_username == "alice" and row.converted_at


def test_admin_guest_endpoint_reports_settings_and_stats(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as guest:
        assert _guest(guest).status_code == 200
        guest.get("/api/runtime")
        assert guest.post("/api/reader/articles/g0/read").status_code == 200
    with TestClient(app_module.app) as admin:
        assert _login(admin, "admin", "admin").status_code == 200
        payload = admin.get("/api/admin/guest").json()
        assert payload["settings"]["enabled"] is True and payload["settings"]["ai_enabled"] is True
        assert payload["settings"]["ai_limits"] == payload["settings"]["reader_ai_limits"]
        kpi = payload["stats"]["kpi"]
        assert kpi["visitors"] == 1 and kpi["visits"] == 1 and kpi["reads"] == 1
        assert payload["stats"]["entries"]["deep_link"] == 1
        updated = admin.put("/api/admin/guest/settings", json={"ai_limits": {"ask": 7, "translate": None}}).json()
        assert updated["settings"]["ai_limits"]["ask"] == 7
        assert updated["settings"]["overridden"] == ["ask"]
        assert admin.put("/api/admin/guest/settings", json={"ai_limits": {"bogus": 1}}).status_code == 400
        # 账户 KPI 不含游客的阅读
        summary = admin.get("/api/admin/accounts").json()["summary"]
        assert summary["reads"] == 0


def test_shared_guest_brief_owner_mapping():
    from api.routers import personal_briefs
    from services import accounts as accounts_service

    visitor = accounts_service.guest_visitor_username("visitorCCCCCCCCCCCCCCC")
    assert personal_briefs._username({"sub": visitor}) == accounts_service.GUEST_USERNAME
    assert personal_briefs._username({"sub": "alice"}) == "alice"


def _guest_brief_env(monkeypatch, tmp_path):
    """游客保留账号 + 镜像默认订阅(经 lifespan)+ 个人早报开关打开。"""
    from models.db import AppSettingRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app):
        pass
    engine = app_module.db_sink.engine
    with Session(engine) as session:
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true"))
        session.commit()
    return engine


def _add_guest_edition(engine, *, revision, status="failed", minutes_ago=11):
    from models.db import PersonalDigestEditionRecord
    from services import accounts as accounts_service
    from services import personal_digest as digest_service

    now = datetime.datetime.now(digest_service.SHANGHAI)
    stamp = (now - datetime.timedelta(minutes=minutes_ago)).isoformat()
    with Session(engine) as session:
        session.add(PersonalDigestEditionRecord(
            owner_username=accounts_service.GUEST_USERNAME, report_date=now.date().isoformat(),
            revision=revision, status=status, check_after=stamp, cutoff_at=stamp,
            generation_reason="scheduled", created_at=stamp, updated_at=stamp,
        ))
        session.commit()


def _guest_revisions(engine):
    from models.db import PersonalDigestEditionRecord
    from services import accounts as accounts_service

    with Session(engine) as session:
        rows = session.exec(
            select(PersonalDigestEditionRecord)
            .where(PersonalDigestEditionRecord.owner_username == accounts_service.GUEST_USERNAME)
            .order_by(PersonalDigestEditionRecord.revision)
        ).all()
        return [(r.revision, r.status, r.generation_reason) for r in rows]


def test_guest_brief_retry_claims_once_under_write_lock(monkeypatch, tmp_path):
    """检视 F6:失败版过冷却后只认领一次——新 pending 版本即认领,重复 / 晚到的首开在写锁后重读即跳过。"""
    from api.routers import personal_briefs

    engine = _guest_brief_env(monkeypatch, tmp_path)
    _add_guest_edition(engine, revision=1)
    personal_briefs._retry_failed_guest_edition(engine)
    personal_briefs._retry_failed_guest_edition(engine)  # 第二个首开:最新版已是 pending,不再重编
    assert _guest_revisions(engine) == [(1, "failed", "scheduled"), (2, "pending", "manual_rebuild")]


def test_guest_brief_retry_skips_stale_request_after_newer_failure(monkeypatch, tmp_path):
    """E1 认领后又出了新的失败版 E2(冷却期内):针对 E1 的晚到请求在锁内重读到 E2,不回写、不重编。"""
    from api.routers import personal_briefs
    from models.db import PersonalDigestEditionRecord
    from services import personal_digest as digest_service

    engine = _guest_brief_env(monkeypatch, tmp_path)
    _add_guest_edition(engine, revision=1)
    personal_briefs._retry_failed_guest_edition(engine)
    with Session(engine) as session:
        second = session.exec(select(PersonalDigestEditionRecord).where(PersonalDigestEditionRecord.revision == 2)).one()
        second.status = "failed"
        second.updated_at = datetime.datetime.now(digest_service.SHANGHAI).isoformat()
        session.add(second)
        session.commit()
    personal_briefs._retry_failed_guest_edition(engine)
    assert [r[:2] for r in _guest_revisions(engine)] == [(1, "failed"), (2, "failed")]


def test_guest_brief_retry_rolls_back_when_creation_fails(monkeypatch, tmp_path):
    """认领与建版同一事务:建版前抛错 → 什么都不留,下一次首开仍能认领。"""
    from api.routers import personal_briefs
    from services import personal_digest as digest_service

    engine = _guest_brief_env(monkeypatch, tmp_path)
    _add_guest_edition(engine, revision=1)
    original = digest_service.start_personal_digest_edition

    def boom(*args, **kwargs):
        raise RuntimeError("建版前失败")

    monkeypatch.setattr(digest_service, "start_personal_digest_edition", boom)
    personal_briefs._retry_failed_guest_edition(engine)
    assert _guest_revisions(engine) == [(1, "failed", "scheduled")]
    monkeypatch.setattr(digest_service, "start_personal_digest_edition", original)
    personal_briefs._retry_failed_guest_edition(engine)
    assert [r[:2] for r in _guest_revisions(engine)] == [(1, "failed"), (2, "pending")]


def test_guest_brief_retry_reads_only_after_taking_write_lock(monkeypatch, tmp_path):
    """确定性交错:另一请求持有写锁时,重试必须卡在拿锁处、还没读;对方把失败版推进冷却期并提交后,
    重试在锁内重读到新状态即跳过——证明「复核」与「建版」在同一写事务里(无锁先读的旧写法会重编)。"""
    import threading
    import time

    from api.routers import personal_briefs
    from services import personal_digest as digest_service

    engine = _guest_brief_env(monkeypatch, tmp_path)
    _add_guest_edition(engine, revision=1)
    holder = engine.raw_connection()
    try:
        holder.isolation_level = None  # 显式 BEGIN / COMMIT
        cursor = holder.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(target=personal_briefs._retry_failed_guest_edition, args=(engine,))
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive()  # 卡在 BEGIN IMMEDIATE
        cursor.execute(
            "UPDATE personal_digest_editions SET updated_at = ? WHERE revision = 1",
            (datetime.datetime.now(digest_service.SHANGHAI).isoformat(),),
        )
        cursor.execute("COMMIT")
    finally:
        holder.close()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert _guest_revisions(engine) == [(1, "failed", "scheduled")]


def test_guest_stats_count_conversions_by_event_time(monkeypatch, tmp_path):
    """检视 F7:八天前逛过、今天直接登录——转化记在八天前那一行,但按 converted_at 算进近 7 天;
    该访客同时进入窗口访客集合,转化率不超过 100%;窗口外的访问不进访问数与入口。"""
    from services import guest_access

    app_module = _setup_app(monkeypatch, tmp_path)
    engine = app_module.db_sink.engine
    eight_days_ago = (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat()
    with Session(engine) as session:
        guest_access.record_entry(session, "visitorEEEEEEEEEEEEEEE", "deep_link", now=eight_days_ago)
        assert guest_access.record_conversion(session, "visitorEEEEEEEEEEEEEEE", "alice")
        week = guest_access.guest_stats(session, days=7)
        month = guest_access.guest_stats(session, days=30)
    today = datetime.date.today().isoformat()
    assert week["kpi"]["conversions"] == 1 and week["kpi"]["visitors"] == 1
    assert week["kpi"]["conversion_rate"] == 1.0
    assert week["kpi"]["new_visitors"] == 0  # 早于窗口就来过
    assert week["kpi"]["visits"] == 0 and sum(week["entries"].values()) == 0
    assert [d for d in week["by_day"] if d["conversions"]] == [{"day": today, "visitors": 0, "visits": 0, "conversions": 1}]
    assert month["kpi"]["conversions"] == 1 and month["kpi"]["visitors"] == 1
    assert month["entries"]["deep_link"] == 1


def test_guest_conversion_requires_visit_within_30_days(monkeypatch, tmp_path):
    from services import guest_access

    app_module = _setup_app(monkeypatch, tmp_path)
    long_ago = (datetime.datetime.now() - datetime.timedelta(days=31)).isoformat()
    with Session(app_module.db_sink.engine) as session:
        guest_access.record_entry(session, "visitorFFFFFFFFFFFFFFF", "login_page", now=long_ago)
        assert not guest_access.record_conversion(session, "visitorFFFFFFFFFFFFFFF", "alice")
        stats = guest_access.guest_stats(session, days=7)
    assert stats["kpi"]["visitors"] == 0 and stats["kpi"]["conversions"] == 0
    assert stats["kpi"]["conversion_rate"] == 0.0
