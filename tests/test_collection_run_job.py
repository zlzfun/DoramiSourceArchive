"""采集任务运行迁上持久化 jobs 后的端点契约（阶段3 长任务化）。

POST /api/collection-jobs/{id}/run 现返回 {status:accepted, job_id}，实际
run_collection_items 收进后台任务；轮询 /api/jobs/{id} 得 succeeded + 聚合结果。
校验（任务不存在/无可执行节点）仍在提交前同步抛 4xx。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import CollectionJobRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _setup(monkeypatch, tmp_path):
    import api.app as app_module
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'crun.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for u, p, r in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(username=u, password_hash=accounts_service.hash_password(p),
                                   role=r, is_active=True, created_at=now, updated_at=now))
        session.add(CollectionJobRecord(
            id=1, name="J1", fetcher_ids_json='["fx"]', is_active=True,
            created_at=now, updated_at=now,
        ))
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


def test_run_collection_job_submits_background_job(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    import api.routers.collection as collection_router

    monkeypatch.setattr(collection_router, "build_collection_job_items",
                        lambda job: [{"fetcher_id": "fx", "params": {}}])

    captured = {}

    async def fake_run(items, **kwargs):
        captured["items"] = items
        captured.update(kwargs)
        return {"status": "success", "saved_count": 5, "failed_count": 0, "results": []}

    monkeypatch.setattr(app_module, "run_collection_items", fake_run)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/collection-jobs/1/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        job_id = resp.json()["job_id"]

        done = _poll(client, job_id)
        assert done["status"] == "succeeded", done
        assert done["result"]["saved_count"] == 5
        assert captured["job_id"] == 1
        assert captured["run_scope"] == "saved_job"


def test_run_missing_job_is_synchronous_404(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/collection-jobs/999/run").status_code == 404


def test_run_job_denied_for_reader(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post("/api/collection-jobs/1/run").status_code == 403
