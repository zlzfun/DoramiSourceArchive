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


def _auth_config(admin="admin:admin", user="user:user"):
    from config import _auth_credentials

    return replace(
        __import__("api.app", fromlist=["settings"]).settings.auth,
        admin_users=_auth_credentials(admin) if admin else [],
        user_users=_auth_credentials(user) if user else [],
    )


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_admin_ops.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    accounts_service.seed_users_if_empty(sink.engine, _auth_config())
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ==================== 纯函数：埋点与全局开关 ====================
def test_touch_login_and_record_ai_usage(tmp_path):
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'helpers.db'}").engine
    accounts_service.seed_users_if_empty(engine, _auth_config())
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

        # 账户列表带订阅数字段。
        accounts = client.get("/api/admin/accounts").json()
        assert all("subscription_count" in a for a in accounts)

    # 受限读者无权访问运维面。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/overview").status_code == 403
        assert client.get("/api/admin/accounts").status_code == 403
        assert client.get("/api/admin/ai-beta/global").status_code == 403


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
                              usage={"total_tokens": 50}, day=today)
        ai_usage.record_usage(session, username="system", purpose="daily_brief_reduce", model="m1",
                              usage={"total_tokens": 200}, day=today)
        summary = ai_usage.summarize(session, days=7)
    assert summary["totals"]["total_tokens"] == 250
    assert summary["totals"]["calls"] == 2
    purposes = {r["purpose"] for r in summary["by_purpose"]}
    assert {"ask", "daily_brief_reduce"} <= purposes
    users = {r["username"] for r in summary["by_user"]}
    assert {"alice", "system"} <= users


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
