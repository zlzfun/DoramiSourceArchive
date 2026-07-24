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
