import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _set_auth_accounts(monkeypatch, app_module):
    monkeypatch.setattr(
        app_module,
        "AUTH_ACCOUNTS",
        {
            "admin": {"password": "admin", "role": "admin"},
            "user": {"password": "user", "role": "user"},
        },
    )


def _set_runtime_role(monkeypatch, role: str):
    import api.app as app_module
    from config import RuntimeConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role=role)),
    )
    return app_module


def test_runtime_role_normalization_rejects_invalid_role():
    from config import _auth_credentials, _runtime_role

    assert _runtime_role("") == "all"
    assert _runtime_role(" Reader ") == "reader"
    assert [item.username for item in _auth_credentials("admin:a,user:b")] == ["admin", "user"]

    try:
        _runtime_role("invalid")
    except ValueError as exc:
        assert "Invalid runtime role" in str(exc)
    else:
        raise AssertionError("invalid runtime role should fail")


def test_reader_role_disables_collector_api(monkeypatch):
    app_module = _set_runtime_role(monkeypatch, "reader")
    _set_auth_accounts(monkeypatch, app_module)

    with TestClient(app_module.app) as client:
        unauthenticated_response = client.get("/api/fetchers")
        assert unauthenticated_response.status_code == 401

        _login(client, "user", "user")
        runtime_response = client.get("/api/runtime")
        assert runtime_response.status_code == 200
        assert runtime_response.json()["role"] == "reader"
        assert runtime_response.json()["account_role"] == "user"

        collector_response = client.get("/api/fetchers")
        assert collector_response.status_code == 403
        assert collector_response.json()["collector_enabled"] is False
        export_response = client.get("/api/archive/export/articles.jsonl")
        assert export_response.status_code == 403

        reader_response = client.get("/api/feed/articles")
        assert reader_response.status_code == 200


def test_collector_role_disables_reader_api(monkeypatch):
    app_module = _set_runtime_role(monkeypatch, "collector")
    _set_auth_accounts(monkeypatch, app_module)

    with TestClient(app_module.app) as client:
        _login(client)
        runtime_response = client.get("/api/runtime")
        assert runtime_response.status_code == 200
        assert runtime_response.json()["role"] == "collector"

        collector_response = client.get("/api/fetchers")
        assert collector_response.status_code == 200

        reader_response = client.get("/api/feed/articles")
        assert reader_response.status_code == 403
        assert reader_response.json()["reader_enabled"] is False
        import_response = client.post("/api/archive/import/articles.jsonl", content="")
        assert import_response.status_code == 403

        mcp_response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert mcp_response.status_code == 403

        assert app_module.disabled_runtime_surface("/mcpfoo") is None


def test_all_runtime_splits_surfaces_by_login_account(monkeypatch):
    app_module = _set_runtime_role(monkeypatch, "all")
    _set_auth_accounts(monkeypatch, app_module)

    with TestClient(app_module.app) as client:
        # admin 为超级用户：采集 + 读者通吃。
        _login(client, "admin", "admin")
        admin_runtime = client.get("/api/runtime").json()
        assert admin_runtime["role"] == "all"
        assert admin_runtime["account_role"] == "admin"
        assert admin_runtime["collector_enabled"] is True
        assert admin_runtime["reader_enabled"] is True
        assert client.get("/api/fetchers").status_code == 200
        assert client.get("/api/subscriptions").status_code == 200

        client.post("/api/auth/logout")
        # user 为受限读者：仅读者面，采集面被拒。
        _login(client, "user", "user")
        user_runtime = client.get("/api/runtime").json()
        assert user_runtime["role"] == "all"
        assert user_runtime["account_role"] == "user"
        assert user_runtime["collector_enabled"] is False
        assert user_runtime["reader_enabled"] is True
        assert client.get("/api/fetchers").status_code == 403
        assert client.get("/api/subscriptions").status_code == 200
        assert client.get("/api/articles").status_code == 200
        assert client.post("/api/articles", json={}).status_code == 403
