"""临时批量抓取迁上持久化 jobs 后的端点契约（阶段3 长任务化）。

POST /api/fetch/batch 现返回 {status:accepted, job_id}，实际 run_collection_items
（含写 CollectionJobRunRecord 聚合记录）收进后台任务；轮询 /api/jobs/{id} 得
succeeded + 聚合结果。items 为空仍在提交前同步抛 400。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _setup(monkeypatch, tmp_path):
    import api.app as app_module
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'fbatch.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for u, p, r in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(username=u, password_hash=accounts_service.hash_password(p),
                                   role=r, is_active=True, created_at=now, updated_at=now))
        session.commit()
    return app_module


def _login(client, u="admin", p="admin"):
    assert client.post("/api/auth/login", json={"username": u, "password": p}).status_code == 200


def _poll(client, job_id, tries=200):
    for _ in range(tries):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
    raise AssertionError("任务未到达终态")


def test_fetch_batch_submits_background_job(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)

    captured = {}

    async def fake_run(items, **kwargs):
        captured["items"] = items
        captured.update(kwargs)
        return {"status": "success", "saved_count": 7, "failed_count": 1, "results": []}

    monkeypatch.setattr(app_module, "run_collection_items", fake_run)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/fetch/batch", json={"items": [
            {"fetcher_id": "fa", "params": {"limit": 3}},
            {"fetcher_id": "fb", "params": {}},
        ]})
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        job_id = resp.json()["job_id"]

        done = _poll(client, job_id)
        assert done["status"] == "succeeded", done
        # 聚合结果（成功/失败计数）经 job.result 原样落库回传
        assert done["result"]["saved_count"] == 7
        assert done["result"]["failed_count"] == 1
        # run_collection_items 以 ad_hoc/manual 语义被调用，节点透传（含 params）
        assert captured["run_scope"] == "ad_hoc"
        assert captured["trigger_type"] == "manual"
        assert captured["name"] == "临时批量抓取"
        assert [item["fetcher_id"] for item in captured["items"]] == ["fa", "fb"]
        assert captured["items"][0]["params"]["limit"] == 3


def test_fetch_batch_test_limit_overrides_params(monkeypatch, tmp_path):
    """?test_limit= 仍合入每个节点的 params（提交给后台任务的 items 里）。"""
    app_module = _setup(monkeypatch, tmp_path)

    captured = {}

    async def fake_run(items, **kwargs):
        captured["items"] = items
        return {"status": "success", "saved_count": 0, "failed_count": 0, "results": []}

    monkeypatch.setattr(app_module, "run_collection_items", fake_run)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/fetch/batch?test_limit=2",
                           json={"items": [{"fetcher_id": "fa", "params": {}}]})
        assert resp.status_code == 200
        _poll(client, resp.json()["job_id"])
        # test_run_overrides(2) 已合入 params（limit=2）
        assert captured["items"][0]["params"].get("limit") == 2


def test_fetch_batch_empty_items_is_synchronous_400(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/fetch/batch", json={"items": []}).status_code == 400


def test_fetch_batch_denied_for_reader(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post("/api/fetch/batch", json={"items": [
            {"fetcher_id": "fa", "params": {}},
        ]}).status_code == 403
