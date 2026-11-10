"""管理员管理写操作审计回归测试。"""

import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402


def _setup_app(
    monkeypatch,
    tmp_path,
    accounts=(("admin", "admin", "admin"), ("user", "user", "user")),
):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'admin_audit.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    seed_default_accounts(sink.engine, accounts)
    return app_module


def _login(client: TestClient, username: str = "admin", password: str = "admin"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response


def _audit_rows(engine):
    from models.db import AdminAuditRecord

    with Session(engine) as session:
        return session.exec(
            select(AdminAuditRecord).order_by(AdminAuditRecord.id)
        ).all()


def test_admin_write_creates_one_semantic_audit_row(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/accounts",
            json={
                "username": "audit-reader",
                "password": "reader-password",
                "role": "user",
            },
        )

    assert response.status_code == 200
    rows = _audit_rows(app_module.db_sink.engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.username == "admin"
    assert row.method == "POST"
    assert row.path == "/api/accounts"
    assert row.status_code == 200
    assert "audit-reader" in row.summary
    assert row.target == "audit-reader"


def test_get_requests_are_never_audited(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.get("/api/accounts").status_code == 200
        assert client.get("/api/admin/overview").status_code == 200

    assert _audit_rows(app_module.db_sink.engine) == []


def test_new_analysis_and_taxonomy_writes_have_semantic_audit_summaries(tmp_path):
    from services.admin_audit import record_audit, should_audit
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'new-audit-rules.db'}")
    writes = [
        ("PUT", "/api/admin/analysis/config", {"article_analysis_enabled": False}),
        ("POST", "/api/admin/analysis/backfills", {"days": 30, "selection": "all"}),
        (
            "POST",
            "/api/admin/podcast-premium-guides/episode-42/force",
            {"reason": "manual TTS", "idempotency_key": "force-episode-42"},
        ),
        ("PATCH", "/api/admin/cms-tags/42", {"name_zh": "安全"}),
        ("POST", "/api/admin/cms-tag-candidates/7/reject", {"reason": "noise"}),
        ("POST", "/api/admin/taxonomy/v1/publish", {"confirmation": "publish"}),
    ]
    for method, path, body in writes:
        assert should_audit(path, method) is True
        record_audit(
            sink.engine,
            username="admin",
            method=method,
            path=path,
            status_code=200,
            body=body,
        )
    rows = _audit_rows(sink.engine)
    assert len(rows) == len(writes)
    assert all(row.summary for row in rows)
    force_row = next(row for row in rows if row.path.endswith("/force"))
    assert force_row.summary == "强制生成播客 TTS episode-42"
    assert force_row.target == "episode-42"
    assert should_audit("/api/admin/analysis/backfills/estimate", "POST") is False


def test_reader_surface_write_is_exempt(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/reader/sources/rss_openai/subscribe"
        )

    assert response.status_code == 200
    assert _audit_rows(app_module.db_sink.engine) == []


def test_rejected_last_admin_change_is_audited(monkeypatch, tmp_path):
    app_module = _setup_app(
        monkeypatch,
        tmp_path,
        accounts=(("admin", "admin", "admin"),),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.put(
            "/api/accounts/admin",
            json={"role": "user"},
        )

    assert response.status_code == 400
    rows = _audit_rows(app_module.db_sink.engine)
    assert len(rows) == 1
    assert rows[0].status_code == 400
    assert rows[0].summary == "将 admin 角色改为 读者"
    assert rows[0].target == "admin"


def test_audit_log_shape_gating_and_window_limit(monkeypatch, tmp_path):
    from models.db import AdminAuditRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        for username in ("recent-a", "recent-b"):
            response = client.post(
                "/api/accounts",
                json={
                    "username": username,
                    "password": "reader-password",
                    "role": "user",
                },
            )
            assert response.status_code == 200

        with Session(app_module.db_sink.engine) as session:
            session.add(
                AdminAuditRecord(
                    username="admin",
                    method="DELETE",
                    path="/api/accounts/ancient",
                    status_code=200,
                    summary="删除账户 ancient",
                    target="ancient",
                    at="2000-01-01T00:00:00",
                )
            )
            session.commit()

        response = client.get("/api/admin/audit-log?days=30&limit=1")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"items", "total"}
        assert payload["total"] == 2
        assert len(payload["items"]) == 1
        assert set(payload["items"][0]) == {
            "id",
            "username",
            "method",
            "path",
            "status_code",
            "summary",
            "target",
            "at",
        }

        # 分页 skip:窗口内 2 条,skip=1&limit=1 返回第 2 条,total 仍为 2。
        page = client.get("/api/admin/audit-log?days=30&limit=1&skip=1").json()
        assert page["total"] == 2
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] != payload["items"][0]["id"]

        _login(client, "user", "user")
        assert client.get("/api/admin/audit-log").status_code == 403


def test_audit_write_failure_never_breaks_business_request(monkeypatch, tmp_path):
    from services import accounts as accounts_service
    from services import admin_audit as admin_audit_service

    app_module = _setup_app(monkeypatch, tmp_path)

    class BrokenAuditSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            raise RuntimeError("audit database unavailable")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(admin_audit_service, "Session", BrokenAuditSession)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/accounts",
            json={
                "username": "survives-audit-failure",
                "password": "reader-password",
                "role": "user",
            },
        )

    assert response.status_code == 200
    with Session(app_module.db_sink.engine) as session:
        assert (
            accounts_service.get_user(session, "survives-audit-failure")
            is not None
        )


def test_middleware_body_preread_is_replayed_to_handler(monkeypatch, tmp_path):
    from services import accounts as accounts_service

    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/accounts",
            json={
                "username": "body-replay-reader",
                "password": "body-replay-password",
                "role": "user",
            },
        )

    assert response.status_code == 200
    assert response.json()["username"] == "body-replay-reader"
    assert response.json()["role"] == "user"
    with Session(app_module.db_sink.engine) as session:
        record = accounts_service.get_user(session, "body-replay-reader")
        assert record is not None
        assert record.role == "user"
        assert accounts_service.verify_password(
            "body-replay-password", record.password_hash
        )


def test_source_analysis_switch_is_admin_only_audited_and_shown_in_the_catalog(monkeypatch, tmp_path):
    """issue #263 的「本机分析」开关:同步建出来的源默认关,管理员打开后目录里看得到;写入审计;
    读者自定源不走这里;非管理员不可达。"""
    from models.db import SourceConfigRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    engine = app_module.db_sink.engine
    stamp = "2026-11-01T00:00:00+00:00"
    with Session(engine) as session:
        session.add_all([
            SourceConfigRecord(
                source_id="rss_synced", name="Synced", source_type="rss", url="https://example.test/feed",
                fetcher_id="generic_rss", ai_analysis_enabled=False, collection_authority_id="upstream-a",
                created_at=stamp, updated_at=stamp,
            ),
            SourceConfigRecord(
                source_id="user_rss_private", name="Private", source_type="rss",
                url="https://example.test/private", owner_username="user", ai_analysis_enabled=False,
                created_at=stamp, updated_at=stamp,
            ),
        ])
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post(
            "/api/admin/sources/rss_synced/analysis", json={"enabled": True}
        ).status_code == 403
        _login(client)
        catalog = {row["id"]: row for row in client.get("/api/fetchers").json()}
        assert catalog["rss_synced"]["ai_analysis_enabled"] is False

        opened = client.post("/api/admin/sources/rss_synced/analysis", json={"enabled": True})
        assert opened.status_code == 200
        assert opened.json() == {"source_id": "rss_synced", "ai_analysis_enabled": True}
        catalog = {row["id"]: row for row in client.get("/api/fetchers").json()}
        assert catalog["rss_synced"]["ai_analysis_enabled"] is True

        assert client.post(
            "/api/admin/sources/user_rss_private/analysis", json={"enabled": True}
        ).status_code == 400
        assert client.post(
            "/api/admin/sources/no-such-source/analysis", json={"enabled": True}
        ).status_code == 404
        closed = client.post("/api/admin/sources/rss_synced/analysis", json={"enabled": False})
        assert closed.json()["ai_analysis_enabled"] is False

    with Session(engine) as session:
        synced = session.get(SourceConfigRecord, "rss_synced")
        # 开关只动分析;「上游也有这个源」的簿记印记不受影响
        assert (synced.ai_analysis_enabled, synced.collection_authority_id) == (False, "upstream-a")
        assert session.get(SourceConfigRecord, "user_rss_private").ai_analysis_enabled is False
    summaries = [
        (row.status_code, row.summary, row.target)
        for row in _audit_rows(engine)
        if row.path.endswith("/analysis")
    ]
    assert (200, "开启源的本机分析 rss_synced", "rss_synced") in summaries
    assert (200, "关闭源的本机分析 rss_synced", "rss_synced") in summaries

