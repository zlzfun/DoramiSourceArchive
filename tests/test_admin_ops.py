"""管理员运维面板回归测试。

覆盖：登录埋点 last_login_at；AI 用量计数自增；AI Beta 全局总开关读写 +
关闭后熔断（_require_reader_ai 403、runtime ai_beta_enabled 归 false）；
/api/admin/overview 聚合；/api/admin/* 仅 admin 可访问；
AI token 计量（record_usage 累加不重复、summarize 聚合、recorder 门控 +
ping 不计、/api/admin/ai-usage 端点与鉴权）。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_admin_ops.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ==================== 纯函数：埋点与全局开关 ====================
def test_touch_login_and_record_ai_usage(tmp_path):
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'helpers.db'}").engine
    seed_default_accounts(engine)
    with Session(engine) as session:
        assert accounts_service.get_user(session, "user").last_login_at is None
        accounts_service.touch_login(session, "user")
        accounts_service.record_ai_usage(session, "user", "translate")
        accounts_service.record_ai_usage(session, "user", "ask")
        accounts_service.record_ai_usage(session, "user", "ask")
        record = accounts_service.get_user(session, "user")
        assert record.last_login_at is not None
        assert record.ai_translate_count == 1
        assert record.ai_ask_count == 2
        assert record.ai_last_used_at is not None


def test_ai_beta_global_switch_roundtrip(tmp_path):
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'global.db'}").engine
    with Session(engine) as session:
        # 默认开启。
        assert accounts_service.ai_beta_global_enabled(session) is True
        accounts_service.set_ai_beta_global_enabled(session, False)
        assert accounts_service.ai_beta_global_enabled(session) is False
        accounts_service.set_ai_beta_global_enabled(session, True)
        assert accounts_service.ai_beta_global_enabled(session) is True


# ==================== 端到端：埋点 / 全局开关 / overview / 门控 ====================
def test_login_writes_last_login_at(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    with TestClient(app_module.app) as client:
        assert _login(client, "admin", "admin").status_code == 200
    with Session(app_module.db_sink.engine) as session:
        assert accounts_service.get_user(session, "admin").last_login_at is not None


def test_admin_overview_and_gating(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.get("/api/admin/overview")
        assert res.status_code == 200
        body = res.json()
        assert body["accounts"]["total"] >= 2
        assert body["accounts"]["admin"] >= 1
        assert "articles" in body["archive"]
        assert "calls_total" in body["ai"]

        # 账户列表带订阅数字段;响应为 {items,total,summary}。
        body = client.get("/api/admin/accounts").json()
        assert set(body) == {"items", "total", "summary"}
        assert all("subscription_count" in a for a in body["items"])
        # summary/total 聚合全部账户(seed admin+user)。
        assert body["total"] == 2
        assert body["summary"]["accounts"] == 2
        assert body["summary"]["admins"] == 1
        assert body["summary"]["disabled"] == 0
        assert isinstance(body["summary"]["top_reads"], list)
        assert isinstance(body["summary"]["top_logins"], list)

    # 受限读者无权访问运维面。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/overview").status_code == 403
        assert client.get("/api/admin/accounts").status_code == 403
        assert client.get("/api/admin/ai-beta/global").status_code == 403


def test_admin_accounts_pagination_and_q_filter(monkeypatch, tmp_path):
    """账户列表规模化:q 子串过滤(命中/未命中)+ skip/limit 切片;
    summary/total 聚合全部账户不受 q/分页影响。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    with Session(app_module.db_sink.engine) as session:
        # seed = admin + user;再造若干读者。
        for name in ("alpha", "alberta", "beta", "gamma"):
            accounts_service.create_user(session, name, f"{name}pw1", "user")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

        # 全量:total=6(admin+user+4),summary 聚合全部账户。
        full = client.get("/api/admin/accounts").json()
        assert full["total"] == 6
        assert full["summary"]["accounts"] == 6
        assert full["summary"]["admins"] == 1
        assert full["summary"]["disabled"] == 0

        # q 命中(大小写不敏感):"AL" → alpha/alberta 两条,total/summary 不变。
        hit = client.get("/api/admin/accounts", params={"q": "AL"}).json()
        assert {a["username"] for a in hit["items"]} == {"alpha", "alberta"}
        assert hit["total"] == 2
        assert hit["summary"]["accounts"] == 6  # summary 不受 q 影响

        # q 未命中:空 items、total=0,summary 仍全量。
        miss = client.get("/api/admin/accounts", params={"q": "zzz"}).json()
        assert miss["items"] == [] and miss["total"] == 0
        assert miss["summary"]["accounts"] == 6

        # skip/limit 切片:每页 2 条,三页并集覆盖全部 6 且互不重叠。
        seen = []
        for skip in (0, 2, 4):
            page = client.get(
                "/api/admin/accounts", params={"skip": skip, "limit": 2}
            ).json()
            assert page["total"] == 6
            assert len(page["items"]) == 2
            seen.extend(a["username"] for a in page["items"])
        assert len(seen) == 6 and len(set(seen)) == 6

        # limit clamp:超界 limit 收敛到 200,不报错。
        assert client.get(
            "/api/admin/accounts", params={"limit": 9999}
        ).status_code == 200


def test_global_switch_disables_ai(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    # 给 user 开启逐账户 AI Beta。
    with Session(app_module.db_sink.engine) as session:
        accounts_service.set_ai_beta_enabled(session, "user", True)

    with TestClient(app_module.app) as admin_client:
        _login(admin_client, "admin", "admin")
        # 关闭全局总开关。
        res = admin_client.post("/api/admin/ai-beta/global", json={"enabled": False})
        assert res.status_code == 200 and res.json()["enabled"] is False

    with TestClient(app_module.app) as user_client:
        _login(user_client, "user", "user")
        # runtime 能力随总开关归 false（即使逐账户已开）。
        assert user_client.get("/api/runtime").json()["ai_beta_enabled"] is False
        # 调用 AI 直接 403 熔断。
        blocked = user_client.post("/api/reader/ai/translate", json={"article_id": "any"})
        assert blocked.status_code == 403
        assert "临时关闭" in blocked.json()["detail"]


# ==================== AI 用量计量 ====================
def test_record_usage_accumulates_not_duplicates(tmp_path):
    from services import ai_usage
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'usage.db'}").engine
    with Session(engine) as session:
        meta_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        ai_usage.record_usage(session, username="alice", purpose="translate", model="m1", usage=meta_usage, day="2026-06-27")
        ai_usage.record_usage(session, username="alice", purpose="translate", model="m1", usage=meta_usage, day="2026-06-27")
        # 系统级日报 + 另一用途。
        ai_usage.record_usage(session, username="system", purpose="daily_brief_map", model="m1",
                              usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}, day="2026-06-27")
        # 非法用途被忽略。
        ai_usage.record_usage(session, username="alice", purpose="not_a_purpose", model="m1", usage=meta_usage, day="2026-06-27")

        from models.db import AiUsageRecord
        rows = list(session.exec(__import__("sqlmodel").select(AiUsageRecord)).all())
        # alice/translate 累加进一行（calls=2），system/daily_brief_map 一行；非法用途不建行。
        assert len(rows) == 2
        alice = next(r for r in rows if r.username == "alice")
        assert alice.calls == 2 and alice.total_tokens == 30 and alice.prompt_tokens == 20


def test_summarize_aggregates(tmp_path):
    from services import ai_usage
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'usage2.db'}").engine
    today = ai_usage._today()
    with Session(engine) as session:
        ai_usage.record_usage(session, username="alice", purpose="ask", model="m1",
                              usage={"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}, day=today)
        ai_usage.record_usage(session, username="system", purpose="daily_brief_reduce", model="m1",
                              usage={"prompt_tokens": 80, "completion_tokens": 120, "total_tokens": 200}, day=today)
        summary = ai_usage.summarize(session, days=7)
    assert summary["totals"]["total_tokens"] == 250
    assert summary["totals"]["calls"] == 2
    purposes = {r["purpose"] for r in summary["by_purpose"]}
    assert {"ask", "daily_brief_reduce"} <= purposes
    users = {r["username"] for r in summary["by_user"]}
    assert {"alice", "system"} <= users
    # by_day 携带输入/输出分量。
    day_row = next(r for r in summary["by_day"] if r["day"] == today)
    assert day_row["prompt_tokens"] == 100
    assert day_row["completion_tokens"] == 150
    assert day_row["total_tokens"] == 250
    # 日×维度明细：供前端按用途/用户拆多系列。
    dp = {(r["day"], r["purpose"]): r for r in summary["by_day_purpose"]}
    assert dp[(today, "ask")]["total_tokens"] == 50
    assert dp[(today, "daily_brief_reduce")]["calls"] == 1
    du = {(r["day"], r["username"]): r for r in summary["by_day_user"]}
    assert du[(today, "alice")]["total_tokens"] == 50
    assert du[(today, "system")]["total_tokens"] == 200


def test_usage_by_user_and_summarize_user(tmp_path):
    from services import ai_usage
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'usage3.db'}").engine
    today = ai_usage._today()
    with Session(engine) as session:
        ai_usage.record_usage(session, username="alice", purpose="translate", model="m1",
                              usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, day=today)
        ai_usage.record_usage(session, username="alice", purpose="ask", model="m1",
                              usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, day=today)
        # 系统任务不计入按用户榜。
        ai_usage.record_usage(session, username="system", purpose="daily_brief_map", model="m1",
                              usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}, day=today)

        by_user = ai_usage.usage_by_user(session, days=30)
        assert "system" not in by_user
        assert by_user["alice"]["calls"] == 2
        assert by_user["alice"]["total_tokens"] == 45

        user = ai_usage.summarize_user(session, "alice", days=30)
        assert user["totals"]["calls"] == 2
        assert user["totals"]["total_tokens"] == 45
        purposes = {r["purpose"] for r in user["by_purpose"]}
        assert {"translate", "ask"} == purposes
        # by_purpose 按调用数降序；by_day 含当天聚合。
        day_row = next(r for r in user["by_day"] if r["day"] == today)
        assert day_row["calls"] == 2 and day_row["total_tokens"] == 45
        dp = {(r["day"], r["purpose"]) for r in user["by_day_purpose"]}
        assert (today, "translate") in dp and (today, "ask") in dp


def test_reader_activity_service(tmp_path):
    from services import reader_activity
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'reads.db'}").engine
    today = reader_activity._today()
    with Session(engine) as session:
        reader_activity.record_read(session, username="alice", source_id="src_a", day=today)
        reader_activity.record_read(session, username="alice", source_id="src_a", day=today)
        reader_activity.record_read(session, username="alice", source_id="src_b", day=today)
        # 空用户/来源静默跳过。
        reader_activity.record_read(session, username="", source_id="src_a", day=today)
        reader_activity.record_read(session, username="alice", source_id="", day=today)

        reader_activity.record_read(session, username="bob", source_id="src_a", day=today)

        by_user = reader_activity.reads_by_user(session, days=30)
        assert by_user["alice"] == 3

        # 各源聚合（全量，跨用户）：src_a = alice 2 + bob 1 = 3，src_b = 1。
        by_source = reader_activity.reads_by_source(session)
        assert by_source["src_a"] == 3
        assert by_source["src_b"] == 1

        summary = reader_activity.summarize_user_reads(session, "alice", days=30)
        assert summary["total"] == 3
        # by_source 按次数降序：src_a(2) 在前。
        assert summary["by_source"][0] == {"source_id": "src_a", "reads": 2}
        day_row = next(r for r in summary["by_day"] if r["day"] == today)
        assert day_row["reads"] == 3


def test_login_events_aggregation(tmp_path):
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'logins.db'}").engine
    seed_default_accounts(engine)
    with Session(engine) as session:
        accounts_service.touch_login(session, "user")
        accounts_service.touch_login(session, "user")
        accounts_service.touch_login(session, "admin")
        # 不存在账户静默跳过，不建事件。
        accounts_service.touch_login(session, "ghost")

        by_user = accounts_service.logins_by_user(session, days=30)
        assert by_user["user"] == 2
        assert by_user["admin"] == 1
        assert "ghost" not in by_user

        summary = accounts_service.summarize_user_logins(session, "user", days=30)
        assert summary["count"] == 2
        assert len(summary["recent"]) == 2
        assert all(summary["recent"])  # 均为非空时间戳
        assert sum(d["logins"] for d in summary["by_day"]) == 2


def test_admin_accounts_windowed_and_activity(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service, ai_usage, reader_activity
    from models.db import ReaderSubscriptionRecord, ArticleRecord, ReaderFavoriteRecord

    today = ai_usage._today()
    with Session(app_module.db_sink.engine) as session:
        accounts_service.touch_login(session, "user")
        accounts_service.touch_login(session, "user")
        ai_usage.record_usage(session, username="user", purpose="translate", model="m1",
                              usage={"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}, day=today)
        reader_activity.record_read(session, username="user", source_id="src_a", day=today)
        reader_activity.record_read(session, username="user", source_id="src_a", day=today)
        session.add(ArticleRecord(
            id="a1", title="t", content_type="web_article", source_id="src_a",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
        ))
        session.add(ReaderFavoriteRecord(owner_username="user", article_id="a1", created_at=today))
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="src_a", filters_json='{"source_ids": ["src_a"]}',
            token_hash="h1", is_active=True, created_at=today, updated_at=today,
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        body = client.get("/api/admin/accounts?days=30").json()
        accounts = body["items"]
        row = next(a for a in accounts if a["username"] == "user")
        assert row["window_days"] == 30
        assert row["ai_calls"] == 1 and row["ai_tokens"] == 12
        assert row["reads"] == 2
        # 列表登录次数 = 测试里 2 次 + 本次 admin 视图前的 user 未再登录（仅 setup 的 2 次）。
        assert row["logins"] == 2
        assert row["logged_in_window"] is True
        assert row["subscription_count"] == 1
        # summary 全表聚合:user 窗口阅读 2 次进 top_reads,登录 2 次进 top_logins。
        summary = body["summary"]
        assert summary["reads"] >= 2 and summary["ai_calls"] >= 1
        top_read = next(t for t in summary["top_reads"] if t["username"] == "user")
        assert top_read["value"] == 2
        top_login = next(t for t in summary["top_logins"] if t["username"] == "user")
        assert top_login["value"] == 2

        detail = client.get("/api/admin/accounts/user/activity?days=30").json()
        assert detail["usage"]["totals"]["calls"] == 1
        assert detail["reads"]["total"] == 2
        assert detail["logins"]["count"] == 2
        assert len(detail["logins"]["recent"]) == 2
        # 各源互动并集：src_a 含 reads=2 + favorites=1。
        eng = next(e for e in detail["source_engagement"] if e["source_id"] == "src_a")
        assert eng["reads"] == 2 and eng["favorites"] == 1 and "name" in eng
        assert detail["favorites_total"] == 1
        assert detail["account"]["subscription_count"] == 1
        # 多管理员平权后 admin 活动详情同样可查（200）；不存在账户仍 404。
        assert client.get("/api/admin/accounts/admin/activity").status_code == 200
        assert client.get("/api/admin/accounts/ghost/activity").status_code == 404

    # 受限读者无权访问。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/accounts/user/activity").status_code == 403


def test_admin_accounts_last_login_falls_back_to_event_stream(monkeypatch, tmp_path):
    """快照 last_login_at 缺失(历史数据疤痕)时,列表/详情以登录事件流兜底。

    肇因:实库出现「窗口登录 241 次但最近登录显示 –」的矛盾行。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    with Session(app_module.db_sink.engine) as session:
        accounts_service.touch_login(session, "user")
        # 模拟疤痕:抹掉快照,事件流仍在
        record = accounts_service.get_user(session, "user")
        record.last_login_at = None
        session.add(record)
        session.commit()
        expected = accounts_service.last_login_by_user(session)["user"]

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        row = next(a for a in client.get("/api/admin/accounts?days=30").json()["items"] if a["username"] == "user")
        assert row["last_login_at"] == expected
        assert row["logged_in_window"] is True
        detail = client.get("/api/admin/accounts/user/activity?days=30").json()
        assert detail["account"]["last_login_at"] == expected


def test_reader_read_endpoint_records(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import reader_activity
    from models.db import ArticleRecord

    with Session(app_module.db_sink.engine) as session:
        session.add(ArticleRecord(
            id="a1", title="t", content_type="web_article", source_id="src_x",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        res = client.post("/api/reader/articles/a1/read")
        assert res.status_code == 200 and res.json()["status"] == "ok"
        # 不存在的文章安静忽略（不报错、不计量）。
        assert client.post("/api/reader/articles/ghost/read").json()["status"] == "ignored"

    with Session(app_module.db_sink.engine) as session:
        assert reader_activity.reads_by_user(session, days=30).get("user") == 1


def test_usage_recorder_gating_and_ping_excluded():
    """usage_meta 为 None（如 ping）不触发 recorder；提供时才触发。"""
    from llm import client as llm_client

    captured = []
    llm_client.set_usage_recorder(lambda meta, usage, model: captured.append((meta.purpose, model)))
    try:
        # 无 meta（ping 路径）→ 不记。
        llm_client._maybe_record_usage(None, {"total_tokens": 9}, "m1")
        assert captured == []
        # 有 meta → 记一次。
        llm_client._maybe_record_usage(llm_client.UsageMeta(purpose="translate", username="a"), {"total_tokens": 9}, "m1")
        assert captured == [("translate", "m1")]
    finally:
        llm_client.set_usage_recorder(None)


def test_admin_ai_usage_endpoint(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import ai_usage

    with Session(app_module.db_sink.engine) as session:
        ai_usage.record_usage(session, username="alice", purpose="translate", model="m1",
                              usage={"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10})

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.get("/api/admin/ai-usage?days=7")
        assert res.status_code == 200
        body = res.json()
        assert body["totals"]["total_tokens"] == 10
        assert any(r["purpose"] == "translate" for r in body["by_purpose"])

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/ai-usage").status_code == 403
