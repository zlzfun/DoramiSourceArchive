"""采集任务 CRUD 端点的热重载契约(issue #82 PR-0,经 HTTP 走真实 lifespan 调度器)。

- 无关任务的创建 / 删除不改变其它命名空间任务与未修改采集任务的 trigger / args / next_run_time;
- 五段但字段越界的 cron 在 commit 前被 400 拒绝,库里不落坏行、旧值不变。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session, select  # noqa: E402

from models.db import CollectionJobRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'jobs-reload.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        session.add(UserRecord(username="admin", password_hash=accounts_service.hash_password("admin"),
                               role="admin", is_active=True, created_at=now, updated_at=now))
        session.add(CollectionJobRecord(
            id=1, name="J1", fetcher_ids_json='["rss_the_decoder"]', cron_expr="0 8 * * *",
            is_active=True, created_at=now, updated_at=now,
        ))
        session.commit()
    return app_module


def _snapshot(scheduler, job_id):
    job = scheduler.get_job(job_id)
    assert job is not None, job_id
    return (str(job.trigger), tuple(job.args or ()), job.next_run_time)


def test_unrelated_crud_keeps_other_jobs_and_rejects_bad_cron(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        scheduler = app_module.scheduler
        watched = ("article_analysis", "personal_digest_pending", "personal_digest_schedule",
                   "retention_cleanup", "collection_job_1")
        before = {job_id: _snapshot(scheduler, job_id) for job_id in watched}

        created = client.post("/api/collection-jobs", json={
            "name": "J2", "fetcher_ids": ["rss_the_decoder"], "cron_expr": "0 9 * * *",
        })
        assert created.status_code == 200, created.text
        new_id = created.json()["id"]
        assert scheduler.get_job(f"collection_job_{new_id}") is not None
        assert {job_id: _snapshot(scheduler, job_id) for job_id in watched} == before

        bad_update = client.put(f"/api/collection-jobs/{new_id}", json={"cron_expr": "61 8 * * *"})
        assert bad_update.status_code == 400
        with Session(app_module.db_sink.engine) as session:
            assert session.get(CollectionJobRecord, new_id).cron_expr == "0 9 * * *"
        assert "hour='9'" in str(scheduler.get_job(f"collection_job_{new_id}").trigger)

        bad_create = client.post("/api/collection-jobs", json={
            "name": "bad", "fetcher_ids": ["rss_the_decoder"], "cron_expr": "0 25 * * *",
        })
        assert bad_create.status_code == 400
        with Session(app_module.db_sink.engine) as session:
            assert session.exec(select(CollectionJobRecord).where(CollectionJobRecord.name == "bad")).first() is None

        assert client.delete(f"/api/collection-jobs/{new_id}").status_code == 200
        assert scheduler.get_job(f"collection_job_{new_id}") is None
        assert {job_id: _snapshot(scheduler, job_id) for job_id in watched} == before
