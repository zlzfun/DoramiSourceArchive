"""账户系统（数据库托管，多管理员平权 v3.19）回归测试。

覆盖：PBKDF2 哈希 round-trip / 非法编码；seed_root_admin_if_empty 空表建根管理员
/ 二次 False / 非空表不动库；末位活跃管理员保护矩阵（单 admin 全拒、双 admin 动
其一成、停用态不计入活跃、API 400 detail）；admin 账户 CRUD（可直建/提升 admin、
列表含 admin 行、activity 对 admin 200）；admin 自我降级后旧 cookie 失效；自助改密；
停用用户旧 cookie 立即失效；非 admin 访问 /api/accounts 被拒。
"""
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402


# ==================== 纯函数：哈希 ====================
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


# ==================== 空表自动种根管理员 ====================
def test_seed_root_admin_if_empty(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    # 空表：落一行 admin/admin(admin)，返回 True。
    assert accounts_service.seed_root_admin_if_empty(engine) is True
    with Session(engine) as session:
        users = {u.username: u.role for u in accounts_service.list_users(session)}
    assert users == {"admin": "admin"}
    # 二次：表非空，返回 False。
    assert accounts_service.seed_root_admin_if_empty(engine) is False


def test_seed_root_admin_leaves_non_empty_db_untouched(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    # 表里已有一个读者（无 admin）——种子函数仍应一动不动、不补 admin。
    with Session(engine) as session:
        accounts_service.create_user(session, "alice", "alice-pw", "user")
    assert accounts_service.seed_root_admin_if_empty(engine) is False
    with Session(engine) as session:
        users = {u.username: u.role for u in accounts_service.list_users(session)}
    assert users == {"alice": "user"}


# ==================== 末位活跃管理员保护（服务层矩阵） ====================
def test_last_admin_guard_single_admin_all_blocked(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    seed_default_accounts(engine, (("admin", "admin", "admin"),))
    with Session(engine) as session:
        # 仅一个活跃 admin：降级 / 停用 / 删除均被拒。
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_role(session, "admin", "user")
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_active(session, "admin", False)
        with pytest.raises(accounts_service.AccountError):
            accounts_service.delete_user(session, "admin")


def test_last_admin_guard_two_admins_act_on_one_then_last_blocked(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    seed_default_accounts(engine, (("a", "a-pw", "admin"), ("b", "b-pw", "admin")))
    with Session(engine) as session:
        assert accounts_service.count_active_admins(session) == 2
        # 两个活跃 admin：降级其一成功。
        accounts_service.set_role(session, "a", "user")
        assert accounts_service.count_active_admins(session) == 1
        # 现在只剩 b 一个活跃 admin：再动它被拒。
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_role(session, "b", "user")
        with pytest.raises(accounts_service.AccountError):
            accounts_service.delete_user(session, "b")


def test_last_admin_guard_disabled_admin_not_counted(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    seed_default_accounts(engine, (("a", "a-pw", "admin"), ("b", "b-pw", "admin")))
    with Session(engine) as session:
        # 停用 b（此时仍有两个活跃 → 允许）。
        accounts_service.set_active(session, "b", False)
        assert accounts_service.count_active_admins(session) == 1
        # a 是唯一活跃 admin：动它被拒。
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_active(session, "a", False)
        # 停用态的 b 不计入活跃数，可被删除。
        accounts_service.delete_user(session, "b")
        assert accounts_service.get_user(session, "b") is None


def test_set_role_idempotent_admin_to_admin(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    seed_default_accounts(engine, (("admin", "admin", "admin"),))
    with Session(engine) as session:
        # 把唯一 admin 设成 admin（幂等）——不触发末位保护。
        record = accounts_service.set_role(session, "admin", "admin")
        assert record.role == "admin"


def test_set_default_surface(tmp_path):
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path)
    seed_default_accounts(engine, (("admin", "admin", "admin"),))
    with Session(engine) as session:
        assert accounts_service.get_user(session, "admin").default_surface == "console"
        record = accounts_service.set_default_surface(session, "admin", "reader")
        assert record.default_surface == "reader"
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_default_surface(session, "admin", "bogus")


# ==================== 端到端：HTTP 鉴权与端点 ====================
def _setup_app(monkeypatch, tmp_path, accounts=(("admin", "admin", "admin"), ("user", "user", "user"))):
    import api.app as app_module
    from config import RuntimeConfig

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_accounts.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, accounts)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_db_login_success_failure_and_disabled(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        admin_login = _login(client, "admin", "admin")
        assert admin_login.status_code == 200
        assert admin_login.json()["user"]["interest_onboarding_completed"] is True
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "ghost", "whatever").status_code == 401

    # 停用 user 后拒绝登录。
    from services import accounts as accounts_service
    with Session(app_module.db_sink.engine) as session:
        accounts_service.set_active(session, "user", False)
    with TestClient(app_module.app) as client:
        assert _login(client, "user", "user").status_code == 401


def test_new_reader_interest_onboarding_is_persisted(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    from models.db import AppSettingRecord
    with Session(app_module.db_sink.engine) as session:
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true"))
        session.commit()
    with TestClient(app_module.app) as client:
        login = _login(client, "user", "user")
        assert login.json()["user"]["interest_onboarding_completed"] is False

        # 空选择也允许完成引导：不关注是中性质量日报，不等价于屏蔽。
        completed = client.put(
            "/api/reader/interests",
            json={"items": [], "complete_onboarding": True},
        )
        assert completed.status_code == 200
        assert completed.json()["onboarding_completed"] is True
        session = client.get("/api/auth/session").json()
        assert session["user"]["interest_onboarding_completed"] is True

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

        # 多管理员平权：可直建管理员账户（200）。
        created_admin = client.post("/api/accounts", json={"username": "root2", "password": "rootpw1", "role": "admin"})
        assert created_admin.status_code == 200
        assert created_admin.json()["role"] == "admin"

        # 列表包含新账户。
        usernames = {a["username"] for a in client.get("/api/accounts").json()}
        assert {"admin", "user", "carol", "root2"} <= usernames

        # 可把读者提升为管理员（200）。
        assert client.put("/api/accounts/carol", json={"role": "admin"}).status_code == 200
        assert next(a for a in client.get("/api/accounts").json() if a["username"] == "carol")["role"] == "admin"

        # 停用、重置密码仍可用（carol 已提升为 admin，但仍有 admin/root2 兜底活跃数）。
        assert client.put("/api/accounts/carol", json={"is_active": False}).json()["is_active"] is False
        assert client.post("/api/accounts/carol/reset-password", json={"new_password": "newpw123"}).status_code == 200

        # 删除。
        assert client.delete("/api/accounts/carol").status_code == 200
        assert "carol" not in {a["username"] for a in client.get("/api/accounts").json()}

    # 运维视图（/api/admin/accounts）现在也列出管理员行（带 role）。
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        admin_view = client.get("/api/admin/accounts").json()
        admin_row = next((a for a in admin_view["items"] if a["username"] == "admin"), None)
        assert admin_row is not None and admin_row["role"] == "admin"
        # admin 活动详情也可查（200）。
        assert client.get("/api/admin/accounts/admin/activity").status_code == 200


def test_last_admin_guard_via_api(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 仅一个 admin：降级 / 停用 / 删除均被拒，且 400 detail 透传保护文案。
        demote = client.put("/api/accounts/admin", json={"role": "user"})
        assert demote.status_code == 400
        assert "至少需保留一名活跃管理员" in demote.json()["detail"]
        assert client.put("/api/accounts/admin", json={"is_active": False}).status_code == 400
        assert client.delete("/api/accounts/admin").status_code == 400


def test_admin_self_demote_revokes_cookie(monkeypatch, tmp_path):
    """两个 admin 时，其一自我降级成功；降级后其旧 cookie 因角色不符在下一请求 401。"""
    app_module = _setup_app(
        monkeypatch, tmp_path,
        accounts=(("admin", "admin", "admin"), ("admin2", "admin2", "admin")),
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin2", "admin2")
        # 自我降级为读者（另有 admin 兜底活跃数，允许）。
        assert client.put("/api/accounts/admin2", json={"role": "user"}).status_code == 200
        # 旧 cookie 角色为 admin，与库中现角色 user 不符 → read_auth_token 回查吊销。
        assert client.get("/api/runtime").status_code == 401


def test_preferences_read_write_and_reject_invalid(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 默认 console。
        assert client.get("/api/runtime").json()["default_surface"] == "console"
        # 写 reader。
        resp = client.post("/api/auth/preferences", json={"default_surface": "reader"})
        assert resp.status_code == 200 and resp.json()["default_surface"] == "reader"
        assert client.get("/api/runtime").json()["default_surface"] == "reader"
        # 非法值 400。
        assert client.post("/api/auth/preferences", json={"default_surface": "bogus"}).status_code == 400


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

    with TestClient(app_module.app) as user_client, TestClient(app_module.app) as admin_client:
        _login(admin_client, "admin", "admin")
        _login(user_client, "user", "user")
        runtime = user_client.get("/api/runtime")
        assert runtime.status_code == 200
        # 版本号透出(单一事实来源 src/version.py,设置-关于展示)
        from version import __version__
        assert runtime.json()["version"] == __version__

        # 管理员停用 user → user 已签发的 cookie 在下一次请求即失效。
        assert admin_client.put("/api/accounts/user", json={"is_active": False}).status_code == 200
        assert user_client.get("/api/runtime").status_code == 401


def test_non_admin_cannot_manage_accounts(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/accounts").status_code == 403
        assert client.post("/api/accounts", json={"username": "x", "password": "ppppp1", "role": "user"}).status_code == 403


# ==================== v3.40.4 P0 安全收口（审计 M03/M04） ====================
def test_password_reset_revokes_existing_sessions(monkeypatch, tmp_path):
    """M04：管理员重置密码轮换会话世代，目标账户既有 Cookie 下一请求即失效。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as user_client, TestClient(app_module.app) as admin_client:
        _login(user_client, "user", "user")
        assert user_client.get("/api/runtime").status_code == 200
        _login(admin_client, "admin", "admin")
        assert admin_client.post(
            "/api/accounts/user/reset-password", json={"new_password": "npw12345"}
        ).status_code == 200
        # 旧 Cookie 世代不符 → 吊销；新密码重新登录后恢复。
        assert user_client.get("/api/runtime").status_code == 401
        assert _login(user_client, "user", "npw12345").status_code == 200
        assert user_client.get("/api/runtime").status_code == 200


def test_self_change_password_keeps_own_session_kills_others(monkeypatch, tmp_path):
    """M04：自助改密就地续签本人 Cookie 不断线；他处旧会话全部吊销。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as here, TestClient(app_module.app) as elsewhere:
        _login(here, "user", "user")
        _login(elsewhere, "user", "user")
        assert here.post(
            "/api/auth/change-password",
            json={"current_password": "user", "new_password": "npw12345"},
        ).status_code == 200
        assert here.get("/api/runtime").status_code == 200
        assert elsewhere.get("/api/runtime").status_code == 401


def test_recreated_same_name_account_does_not_revive_old_cookie(monkeypatch, tmp_path):
    """M04：删号后同名同角色重建，旧 Cookie 不复活（会话世代随机重生）。"""
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as user_client, TestClient(app_module.app) as admin_client:
        _login(user_client, "user", "user")
        _login(admin_client, "admin", "admin")
        assert admin_client.delete("/api/accounts/user").status_code == 200
        assert user_client.get("/api/runtime").status_code == 401
        assert admin_client.post(
            "/api/accounts", json={"username": "user", "password": "user", "role": "user"}
        ).status_code == 200
        # 同名同角色重建后，旧 Cookie 仍然无效。
        assert user_client.get("/api/runtime").status_code == 401


def test_delete_account_purges_assets_and_tombstones_metering(tmp_path):
    """M03：删号单事务收口——个人资产物理删除、计量墓碑化、自定源随退订清理，
    同名重建不继承任何历史。"""
    from models.db import (
        AiUsageRecord,
        AnnouncementDismissRecord,
        ArticleShareRecord,
        FeedbackRecord,
        LoginEventRecord,
        ReaderArticleReadStateRecord,
        ReaderFavoriteRecord,
        ReaderFeedTokenRecord,
        ReaderReadCursorRecord,
        ReaderReadRecord,
        ReaderSubscriptionRecord,
        SourceConfigRecord,
        SourceStateRecord,
    )
    from sqlmodel import select
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path, name="delete_cascade.db")
    seed_default_accounts(engine)
    t = "2026-09-01T08:00:00"
    custom_sid = "user_rss_0123456789ab"
    with Session(engine) as session:
        # 个人资产（全部应物理删除）
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="平台源订阅", token_hash="th1",
            filters_json='{"source_id": "rss_x"}', created_at=t, updated_at=t))
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="自定源订阅", token_hash="th2",
            filters_json=f'{{"source_id": "{custom_sid}"}}', created_at=t, updated_at=t))
        session.add(ReaderFeedTokenRecord(owner_username="user", token_hash="fh", created_at=t, updated_at=t))
        session.add(ReaderFavoriteRecord(owner_username="user", article_id="a1", created_at=t))
        session.add(ArticleShareRecord(token="dshr_test", article_id="a1", owner_username="user", created_at=t))
        session.add(ReaderArticleReadStateRecord(owner_username="user", article_id="a1", read_at=t))
        session.add(ReaderReadCursorRecord(owner_username="user", source_id="rss_x", updated_at=t))
        session.add(FeedbackRecord(owner_username="user", category="bug", content="x", created_at=t, updated_at=t))
        session.add(AnnouncementDismissRecord(owner_username="user", announcement_id=1, dismissed_at=t))
        # 仅该用户订阅的自定源（应随退订「无人订阅即物理删」）
        session.add(SourceConfigRecord(
            source_id=custom_sid, name="私有源", source_type="rss",
            owner_username="user", created_at=t, updated_at=t))
        session.add(SourceStateRecord(source_id=custom_sid, fetcher_id="generic_rss", updated_at=t))
        # 计量历史（应墓碑化保留）
        session.add(AiUsageRecord(day="2026-09-01", username="user", purpose="ask",
                                  model="m", calls=3, total_tokens=120, updated_at=t))
        session.add(ReaderReadRecord(day="2026-09-01", username="user", source_id="rss_x", reads=2, updated_at=t))
        session.add(LoginEventRecord(username="user", at=t))
        session.commit()

    with Session(engine) as session:
        accounts_service.delete_user(session, "user")

    with Session(engine) as session:
        assert accounts_service.get_user(session, "user") is None
        # 个人资产全部清空
        for model, col in (
            (ReaderSubscriptionRecord, ReaderSubscriptionRecord.owner_username),
            (ReaderFavoriteRecord, ReaderFavoriteRecord.owner_username),
            (ArticleShareRecord, ArticleShareRecord.owner_username),
            (ReaderArticleReadStateRecord, ReaderArticleReadStateRecord.owner_username),
            (ReaderReadCursorRecord, ReaderReadCursorRecord.owner_username),
            (FeedbackRecord, FeedbackRecord.owner_username),
            (AnnouncementDismissRecord, AnnouncementDismissRecord.owner_username),
        ):
            assert session.exec(select(model).where(col == "user")).all() == []
        assert session.get(ReaderFeedTokenRecord, "user") is None
        # 自定源无人订阅 → 配置与抓取状态物理删除
        assert session.get(SourceConfigRecord, custom_sid) is None
        assert session.get(SourceStateRecord, custom_sid) is None
        # 计量墓碑化：原名无行，deleted:user 保全值
        assert session.exec(select(AiUsageRecord).where(AiUsageRecord.username == "user")).all() == []
        tomb = session.exec(select(AiUsageRecord).where(AiUsageRecord.username == "deleted:user")).one()
        assert tomb.calls == 3 and tomb.total_tokens == 120
        assert session.exec(select(ReaderReadRecord).where(ReaderReadRecord.username == "deleted:user")).one().reads == 2
        assert session.exec(select(LoginEventRecord).where(LoginEventRecord.username == "deleted:user")).one().at == t

        # 同名重建：零资产、零计量继承
        accounts_service.create_user(session, "user", "pw123456", "user")
        assert session.exec(select(ReaderFavoriteRecord).where(ReaderFavoriteRecord.owner_username == "user")).all() == []
        assert session.exec(select(AiUsageRecord).where(AiUsageRecord.username == "user")).all() == []


def test_delete_account_keeps_shared_custom_source_with_tombstoned_owner(tmp_path):
    """M03：他人仍订阅的共享自定源存活，owner_username 溯源字段改写墓碑。"""
    from models.db import ReaderSubscriptionRecord, SourceConfigRecord
    from services import accounts as accounts_service

    engine = _make_engine(tmp_path, name="delete_shared.db")
    seed_default_accounts(engine, (("admin", "admin", "admin"), ("user", "user", "user"), ("bob", "bobpw123", "user")))
    t = "2026-09-01T08:00:00"
    shared_sid = "user_rss_ba9876543210"
    with Session(engine) as session:
        session.add(SourceConfigRecord(
            source_id=shared_sid, name="共享私有源", source_type="rss",
            owner_username="user", created_at=t, updated_at=t))
        for owner in ("user", "bob"):
            session.add(ReaderSubscriptionRecord(
                owner_username=owner, name="订阅", token_hash=f"th_{owner}",
                filters_json=f'{{"source_id": "{shared_sid}"}}', created_at=t, updated_at=t))
        session.commit()

    with Session(engine) as session:
        accounts_service.delete_user(session, "user")

    with Session(engine) as session:
        record = session.get(SourceConfigRecord, shared_sid)
        assert record is not None
        assert record.owner_username == "deleted:user"
