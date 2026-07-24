"""管理员公告 Router 回归测试(v3.18 互通波)。

覆盖:门控(未登录 401 / user 访问管理面 403 / admin CRUD);校验(空 content、
非法 level 400);读者可见性与升序;逐用户一次性 dismiss(幂等、跨账号独立、
下线不清 dismissal);删除级联清 dismissal 行。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

# 两个读者账号,以验证 dismiss 各自独立。
_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"), ("user2", "user2", "user"))


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_announcements.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ==================== 门控 ====================
def test_unauthenticated_is_401(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/admin/announcements").status_code == 401
        assert client.get("/api/reader/announcements").status_code == 401


def test_user_cannot_access_admin_surface(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/announcements").status_code == 403
        assert (
            client.post("/api/admin/announcements", json={"content": "x"}).status_code == 403
        )


# ==================== 管理面 CRUD + 校验 ====================
def test_admin_crud_and_validation(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

        # 空 content → 400。
        assert (
            client.post("/api/admin/announcements", json={"content": "   "}).status_code == 400
        )
        # 非法 level → 400。
        assert (
            client.post(
                "/api/admin/announcements", json={"content": "hi", "level": "bogus"}
            ).status_code
            == 400
        )

        # 正常创建:默认 level=info、created_by=admin。
        res = client.post(
            "/api/admin/announcements", json={"title": "标题", "content": "  正文  "}
        )
        assert res.status_code == 200
        created = res.json()
        assert created["content"] == "正文"  # strip 生效
        assert created["level"] == "info"
        assert created["title"] == "标题"
        assert created["created_by"] == "admin"
        assert created["is_active"] is True
        assert created["dismiss_count"] == 0
        ann_id = created["id"]

        # 更新:改 content/level;updated_at 变化。
        res = client.put(
            f"/api/admin/announcements/{ann_id}",
            json={"content": "新正文", "level": "warning"},
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["content"] == "新正文"
        assert updated["level"] == "warning"
        assert updated["title"] == "标题"  # 未传保持不变

        # 更新校验同样生效。
        assert (
            client.put(
                f"/api/admin/announcements/{ann_id}", json={"level": "bogus"}
            ).status_code
            == 400
        )
        assert (
            client.put(
                f"/api/admin/announcements/{ann_id}", json={"content": "  "}
            ).status_code
            == 400
        )

        # 404 于不存在。
        assert (
            client.put("/api/admin/announcements/99999", json={"title": "x"}).status_code
            == 404
        )
        assert (
            client.post("/api/admin/announcements/99999/toggle").status_code == 404
        )
        assert client.delete("/api/admin/announcements/99999").status_code == 404


# ==================== 读者可见性 + dismiss ====================
def test_reader_visibility_and_dismiss(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    # admin 发两条。
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        first = client.post(
            "/api/admin/announcements", json={"content": "第一条"}
        ).json()
        second = client.post(
            "/api/admin/announcements", json={"content": "第二条"}
        ).json()

    # user 看到两条(升序:第一条在前)。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        items = client.get("/api/reader/announcements").json()["items"]
        assert [i["content"] for i in items] == ["第一条", "第二条"]
        # 读者面不含 dismiss_count。
        assert all("dismiss_count" not in i for i in items)

        # dismiss 第一条。
        assert (
            client.post(
                f"/api/reader/announcements/{first['id']}/dismiss"
            ).status_code
            == 200
        )
        # 幂等:重复调用不报错。
        assert (
            client.post(
                f"/api/reader/announcements/{first['id']}/dismiss"
            ).status_code
            == 200
        )
        # 再取只剩第二条。
        items = client.get("/api/reader/announcements").json()["items"]
        assert [i["content"] for i in items] == ["第二条"]

        # dismiss 不存在的公告 → 404。
        assert (
            client.post("/api/reader/announcements/99999/dismiss").status_code == 404
        )

    # 管理面 dismiss_count 计到 1(仅第一条)。
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        items = {i["id"]: i for i in client.get("/api/admin/announcements").json()["items"]}
        assert items[first["id"]]["dismiss_count"] == 1
        assert items[second["id"]]["dismiss_count"] == 0
        # 管理面倒序:第二条在前。
        listed = client.get("/api/admin/announcements").json()["items"]
        assert [i["id"] for i in listed] == [second["id"], first["id"]]


def test_other_reader_unaffected_by_dismiss(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        ann = client.post("/api/admin/announcements", json={"content": "公共"}).json()

    # user dismiss。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        client.post(f"/api/reader/announcements/{ann['id']}/dismiss")
        assert client.get("/api/reader/announcements").json()["items"] == []

    # user2 不受影响,仍能看到。
    with TestClient(app_module.app) as client:
        _login(client, "user2", "user2")
        items = client.get("/api/reader/announcements").json()["items"]
        assert [i["content"] for i in items] == ["公共"]


def test_toggle_offline_hides_but_keeps_dismissal(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        ann = client.post("/api/admin/announcements", json={"content": "开关"}).json()
        ann_id = ann["id"]

    # user dismiss 后不可见。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        client.post(f"/api/reader/announcements/{ann_id}/dismiss")
        assert client.get("/api/reader/announcements").json()["items"] == []

    # admin 下线。
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.post(f"/api/admin/announcements/{ann_id}/toggle")
        assert res.json()["is_active"] is False

    # 下线后其他读者也不可见。
    with TestClient(app_module.app) as client:
        _login(client, "user2", "user2")
        assert client.get("/api/reader/announcements").json()["items"] == []

    # admin 重新上线。
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.post(f"/api/admin/announcements/{ann_id}/toggle")
        assert res.json()["is_active"] is True

    # 重新上线后:曾 dismiss 的 user 依然不可见;未 dismiss 的 user2 可见。
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/reader/announcements").json()["items"] == []
    with TestClient(app_module.app) as client:
        _login(client, "user2", "user2")
        items = client.get("/api/reader/announcements").json()["items"]
        assert [i["content"] for i in items] == ["开关"]


def test_delete_cascades_dismissals(monkeypatch, tmp_path):
    from models.db import AnnouncementDismissRecord

    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        ann = client.post("/api/admin/announcements", json={"content": "待删"}).json()
        ann_id = ann["id"]

    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        client.post(f"/api/reader/announcements/{ann_id}/dismiss")

    # 删除前:dismissal 行存在。
    with Session(app_module.db_sink.engine) as session:
        rows = session.exec(
            select(AnnouncementDismissRecord).where(
                AnnouncementDismissRecord.announcement_id == ann_id
            )
        ).all()
        assert len(rows) == 1

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert client.delete(f"/api/admin/announcements/{ann_id}").status_code == 200

    # 删除后:公告与 dismissal 行皆无。
    with Session(app_module.db_sink.engine) as session:
        rows = session.exec(
            select(AnnouncementDismissRecord).where(
                AnnouncementDismissRecord.announcement_id == ann_id
            )
        ).all()
        assert rows == []
