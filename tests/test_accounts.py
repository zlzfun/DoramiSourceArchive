"""账户系统（数据库托管）回归测试。

覆盖：PBKDF2 哈希 round-trip / 非法编码；seed_users_if_empty 幂等；DB 登录
成功/失败/停用拒登；admin 账户 CRUD；末位管理员删除/停用/降级保护；自助改密；
停用用户旧 cookie 立即失效；非 admin 访问 /api/accounts 被拒。
"""
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ==================== 纯函数：哈希与种子 ====================
def test_password_hash_roundtrip():
    from services import accounts as accounts_service

    encoded = accounts_service.hash_password("s3cret-pw")
    assert encoded.startswith("pbkdf2_sha256$")
    assert accounts_service.verify_password("s3cret-pw", encoded) is True
    assert accounts_service.verify_password("wrong-pw", encoded) is False
    # 同一明文两次哈希盐不同，密文不同。
    assert encoded != accounts_service.hash_password("s3cret-pw")


def test_verify_password_rejects_malformed_encoding():
    from services import accounts as accounts_service

    assert accounts_service.verify_password("x", "") is False
    assert accounts_service.verify_password("x", "not-an-encoded-hash") is False
    assert accounts_service.verify_password("x", "md5$1$salt$hash") is False
    assert accounts_service.verify_password("", accounts_service.hash_password("x")) is False


def _make_engine(tmp_path, name="accounts.db"):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}").engine


def _auth_config(admin="admin:admin", user="user:user"):
    from config import _auth_credentials

    return replace(
        __import__("api.app", fromlist=["settings"]).settings.auth,
        admin_users=_auth_credentials(admin) if admin else [],
        user_users=_auth_credentials(user) if user else [],
    )


def test_seed_users_if_empty_is_idempotent(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    created = accounts_service.seed_users_if_empty(engine, _auth_config())
    assert created == 2
    # 再次播种应跳过（表非空）。
    assert accounts_service.seed_users_if_empty(engine, _auth_config()) == 0
    with Session(engine) as session:
        users = {u.username: u.role for u in accounts_service.list_users(session)}
    assert users == {"admin": "admin", "user": "user"}


def test_last_admin_guard_blocks_demote_and_delete(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    accounts_service.seed_users_if_empty(engine, _auth_config(user=""))
    with Session(engine) as session:
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_role(session, "admin", "user")
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_active(session, "admin", False)
        with pytest.raises(accounts_service.AccountError):
            accounts_service.delete_user(session, "admin")


# ==================== 端到端：HTTP 鉴权与端点 ====================
def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_accounts.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    accounts_service.seed_users_if_empty(sink.engine, _auth_config())
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_db_login_success_failure_and_disabled(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert _login(client, "admin", "admin").status_code == 200
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "ghost", "whatever").status_code == 401

    # 停用 user 后拒绝登录。
    from services import accounts as accounts_service
    with Session(app_module.db_sink.engine) as session:
        accounts_service.set_active(session, "user", False)
    with TestClient(app_module.app) as client:
        assert _login(client, "user", "user").status_code == 401


def test_admin_account_crud(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

        created = client.post("/api/accounts", json={"username": "carol", "password": "carol-pw", "role": "user"})
        assert created.status_code == 200
        assert created.json()["role"] == "user"
        assert "password_hash" not in created.json()

        # 重复用户名失败。
        assert client.post("/api/accounts", json={"username": "carol", "password": "x2", "role": "user"}).status_code == 400

        # 列表包含新账户。
        usernames = {a["username"] for a in client.get("/api/accounts").json()}
        assert {"admin", "user", "carol"} <= usernames

        # 升级角色、停用、重置密码。
        assert client.put("/api/accounts/carol", json={"role": "admin"}).json()["role"] == "admin"
        assert client.put("/api/accounts/carol", json={"is_active": False}).json()["is_active"] is False
        assert client.post("/api/accounts/carol/reset-password", json={"new_password": "newpw123"}).status_code == 200

        # 删除。
        assert client.delete("/api/accounts/carol").status_code == 200
        assert "carol" not in {a["username"] for a in client.get("/api/accounts").json()}


def test_last_admin_guard_via_api(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 仅一个 admin：降级 / 停用 / 删除均被拒。
        assert client.put("/api/accounts/admin", json={"role": "user"}).status_code == 400
        assert client.put("/api/accounts/admin", json={"is_active": False}).status_code == 400
        assert client.delete("/api/accounts/admin").status_code == 400


def test_self_change_password(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        # 旧密码错误被拒。
        assert client.post(
            "/api/auth/change-password",
            json={"current_password": "nope", "new_password": "brand-new-pw"},
        ).status_code == 400
        # 正确改密。
        assert client.post(
            "/api/auth/change-password",
            json={"current_password": "user", "new_password": "brand-new-pw"},
        ).status_code == 200

    with TestClient(app_module.app) as client:
        assert _login(client, "user", "user").status_code == 401
        assert _login(client, "user", "brand-new-pw").status_code == 200


def test_disabling_user_revokes_existing_cookie(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from services import accounts as accounts_service

    with TestClient(app_module.app) as user_client, TestClient(app_module.app) as admin_client:
        _login(admin_client, "admin", "admin")
        _login(user_client, "user", "user")
        assert user_client.get("/api/runtime").status_code == 200

        # 管理员停用 user → user 已签发的 cookie 在下一次请求即失效。
        assert admin_client.put("/api/accounts/user", json={"is_active": False}).status_code == 200
        assert user_client.get("/api/runtime").status_code == 401


def test_non_admin_cannot_manage_accounts(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/accounts").status_code == 403
        assert client.post("/api/accounts", json={"username": "x", "password": "ppppp1", "role": "user"}).status_code == 403
