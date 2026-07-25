"""读者反馈 API 回归测试。

覆盖读者提交/自查/撤回、每日限额与归属隔离，以及管理员门控、全量统计、
状态流转和回复回显。
"""

import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"), ("bob", "bob", "user"))


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from services import accounts as accounts_service

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / 'app_feedback.db'}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _submit(client, content, category="suggestion"):
    return client.post(
        "/api/reader/feedback",
        json={"category": category, "content": content},
    )


def test_feedback_requires_authentication(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/reader/feedback").status_code == 401
        assert _submit(client, "未登录提交").status_code == 401
        assert client.delete("/api/reader/feedback/1").status_code == 401
        assert client.get("/api/admin/feedback").status_code == 401
        assert client.post(
            "/api/admin/feedback/1/status",
            json={"status": "resolved"},
        ).status_code == 401


def test_feedback_admin_role_gating(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        assert _login(client, "alice", "alice").status_code == 200
        assert client.get("/api/admin/feedback").status_code == 403
        assert client.post(
            "/api/admin/feedback/1/status",
            json={"status": "resolved"},
        ).status_code == 403

    with TestClient(app_module.app) as client:
        assert _login(client, "admin", "admin").status_code == 200
        response = client.get("/api/admin/feedback")
        assert response.status_code == 200
        assert response.json()["counts"]["total"] == 0


def test_reader_submits_and_lists_own_feedback(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    expected_keys = {
        "id",
        "owner_username",
        "category",
        "content",
        "status",
        "admin_note",
        "created_at",
        "updated_at",
    }

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        response = _submit(client, "  希望增加一个新功能  ")
        assert response.status_code == 200
        created = response.json()
        assert set(created) == expected_keys
        assert created["owner_username"] == "alice"
        assert created["category"] == "suggestion"
        assert created["content"] == "希望增加一个新功能"
        assert created["status"] == "open"
        assert created["admin_note"] == ""

        response = client.get("/api/reader/feedback")
        assert response.status_code == 200
        assert response.json()["items"] == [created]


def test_feedback_submission_validation(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert _submit(client, "正文", category="unknown").status_code == 400
        assert _submit(client, " \n\t ").status_code == 400
        assert _submit(client, "x" * 2001).status_code == 400


def test_feedback_daily_submission_limit(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        for index in range(10):
            response = _submit(client, f"第 {index + 1} 条")
            assert response.status_code == 200
        limited = _submit(client, "第 11 条")
        assert limited.status_code == 429


def test_feedback_withdraw_flow(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        withdraw_id = _submit(client, "我会撤回").json()["id"]
        alice_only_id = _submit(client, "不能被 Bob 撤回").json()["id"]
        moved_id = _submit(client, "管理员会开始处理").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        hidden = client.delete(f"/api/reader/feedback/{alice_only_id}")
        assert hidden.status_code == 404

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        moved = client.post(
            f"/api/admin/feedback/{moved_id}/status",
            json={"status": "in_progress"},
        )
        assert moved.status_code == 200

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        withdrawn = client.delete(f"/api/reader/feedback/{withdraw_id}")
        assert withdrawn.status_code == 200
        assert withdrawn.json() == {"status": "success", "id": withdraw_id}
        assert client.delete(f"/api/reader/feedback/{withdraw_id}").status_code == 404
        assert client.delete(f"/api/reader/feedback/{moved_id}").status_code == 409


def test_admin_list_full_dataset_counts_and_filter(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        resolved_id = _submit(client, "Alice 已解决").json()["id"]
        in_progress_id = _submit(client, "Alice 处理中").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        open_id = _submit(client, "Bob 待处理", category="bug").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert client.post(
            f"/api/admin/feedback/{resolved_id}/status",
            json={"status": "resolved"},
        ).status_code == 200
        assert client.post(
            f"/api/admin/feedback/{in_progress_id}/status",
            json={"status": "in_progress"},
        ).status_code == 200

        response = client.get("/api/admin/feedback")
        assert response.status_code == 200
        body = response.json()
        assert {item["owner_username"] for item in body["items"]} == {"alice", "bob"}
        assert {item["id"] for item in body["items"]} == {
            resolved_id,
            in_progress_id,
            open_id,
        }
        expected_counts = {
            "open": 1,
            "in_progress": 1,
            "resolved": 1,
            "dismissed": 0,
            "total": 3,
        }
        assert body["counts"] == expected_counts

        filtered = client.get("/api/admin/feedback", params={"status": "open"})
        assert filtered.status_code == 200
        assert [item["id"] for item in filtered.json()["items"]] == [open_id]
        assert filtered.json()["counts"] == expected_counts
        assert client.get(
            "/api/admin/feedback", params={"status": "invalid"}
        ).status_code == 400


def test_admin_status_and_note_are_visible_to_owner(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    note = "已定位问题，将在下一次发布中修复。"

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        feedback_id = _submit(client, "阅读页有显示问题", category="bug").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = client.post(
            f"/api/admin/feedback/{feedback_id}/status",
            json={"status": "resolved", "admin_note": note},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert response.json()["admin_note"] == note
        assert client.post(
            f"/api/admin/feedback/{feedback_id}/status",
            json={"status": "not-a-status"},
        ).status_code == 400
        assert client.post(
            f"/api/admin/feedback/{feedback_id}/status",
            json={"status": "resolved", "admin_note": "x" * 2001},
        ).status_code == 400
        assert client.post(
            "/api/admin/feedback/999999/status",
            json={"status": "resolved"},
        ).status_code == 404

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        item = client.get("/api/reader/feedback").json()["items"][0]
        assert item["id"] == feedback_id
        assert item["status"] == "resolved"
        assert item["admin_note"] == note


def test_reader_cannot_see_other_readers_feedback(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        alice_id = _submit(client, "Alice 的反馈").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        bob_id = _submit(client, "Bob 的反馈").json()["id"]
        bob_items = client.get("/api/reader/feedback").json()["items"]
        assert [item["id"] for item in bob_items] == [bob_id]
        assert all(item["owner_username"] == "bob" for item in bob_items)
        assert alice_id not in {item["id"] for item in bob_items}

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        alice_items = client.get("/api/reader/feedback").json()["items"]
        assert [item["id"] for item in alice_items] == [alice_id]
        assert bob_id not in {item["id"] for item in alice_items}


def _reply(client, feedback_id, note, status="in_progress"):
    return client.post(
        f"/api/admin/feedback/{feedback_id}/status",
        json={"status": status, "admin_note": note},
    )


def test_feedback_unread_reply_count_flow(monkeypatch, tmp_path):
    """未读回复计数:无回复=0 → 管理员回复后计 1 → mark-seen 归零 → 新回复再计 1。"""
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        feedback_id = _submit(client, "希望增加一个新源").json()["id"]
        # 尚无管理员回复 → 未读为 0
        assert client.get("/api/reader/feedback/unread-count").json() == {"unread_count": 0}

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert _reply(client, feedback_id, "已收到,正在评估。").status_code == 200

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        # 有新回复且尚未查看 → 计 1
        assert client.get("/api/reader/feedback/unread-count").json()["unread_count"] == 1
        # 标记已看 → 归零
        seen = client.post("/api/reader/feedback/mark-seen")
        assert seen.status_code == 200
        assert seen.json()["unread_count"] == 0
        assert client.get("/api/reader/feedback/unread-count").json()["unread_count"] == 0

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 管理员再次回复(updated_at 晚于上次查看)
        assert _reply(client, feedback_id, "已排期,下个版本上线。", status="resolved").status_code == 200

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/reader/feedback/unread-count").json()["unread_count"] == 1


def test_feedback_unread_count_isolated_per_reader(monkeypatch, tmp_path):
    """未读计数只统计本人的反馈回复,不串号。"""
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        alice_id = _submit(client, "Alice 的诉求").json()["id"]

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert _reply(client, alice_id, "给 Alice 的回复").status_code == 200

    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        # Bob 没有任何被回复的反馈
        assert client.get("/api/reader/feedback/unread-count").json()["unread_count"] == 0

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert client.get("/api/reader/feedback/unread-count").json()["unread_count"] == 1


def test_feedback_unread_count_gating(monkeypatch, tmp_path):
    """门控:未登录 401;管理员是读者语义,可调用(自身无被回复反馈即 0)。"""
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        assert client.get("/api/reader/feedback/unread-count").status_code == 401
        assert client.post("/api/reader/feedback/mark-seen").status_code == 401

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        resp = client.get("/api/reader/feedback/unread-count")
        assert resp.status_code == 200
        assert resp.json() == {"unread_count": 0}
        assert client.post("/api/reader/feedback/mark-seen").status_code == 200


def test_admin_list_pagination_and_total(monkeypatch, tmp_path):
    """管理面分页:建 3 条反馈,limit=2&skip=2 返回 1 条,total==3;
    带 status 过滤时 total 为过滤后数,counts 仍全量。"""
    app_module = _setup_app(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        first_id = _submit(client, "反馈一").json()["id"]
        _submit(client, "反馈二")
        _submit(client, "反馈三")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # 全量:total 反映过滤前(此处无过滤)总条数。
        full = client.get("/api/admin/feedback").json()
        assert full["total"] == 3
        assert len(full["items"]) == 3

        # 分页切片:limit=2 & skip=2 → 只剩最后 1 条,total 仍为 3。
        page = client.get("/api/admin/feedback", params={"limit": 2, "skip": 2}).json()
        assert page["total"] == 3
        assert len(page["items"]) == 1

        # 首页 2 条与尾页 1 条并集覆盖全部 3 条,互不重叠。
        head = client.get("/api/admin/feedback", params={"limit": 2, "skip": 0}).json()
        head_ids = {item["id"] for item in head["items"]}
        tail_ids = {item["id"] for item in page["items"]}
        assert len(head_ids) == 2 and head_ids.isdisjoint(tail_ids)

        # status 过滤:把一条置为 resolved 后,total 为过滤后条数,counts 仍全量。
        assert client.post(
            f"/api/admin/feedback/{first_id}/status", json={"status": "resolved"}
        ).status_code == 200
        resolved = client.get("/api/admin/feedback", params={"status": "resolved"}).json()
        assert resolved["total"] == 1
        assert [item["id"] for item in resolved["items"]] == [first_id]
        assert resolved["counts"]["total"] == 3
        assert resolved["counts"]["open"] == 2
