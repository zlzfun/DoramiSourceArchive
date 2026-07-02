"""持久化后台任务状态机（阶段3）。

覆盖 services.jobs：launch 落库 + 执行到终态、成功结果/失败错误持久化、进度落库、
get_job/list_jobs 从库读回（含「另开引擎读」= 模拟重启后仍可见），以及 to_dict 形状
与旧内存版一致（前端轮询无感）。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from services import jobs  # noqa: E402


def _sink(tmp_path, name="jobs.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


async def _drain(engine, job_id, tries=200):
    for _ in range(tries):
        job = jobs.get_job(engine, job_id)
        if job and job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("任务未在预期时间内到达终态")


def test_launch_runs_work_and_persists_success(tmp_path):
    engine = _sink(tmp_path).engine

    async def scenario():
        async def work(job):
            job.set_total(3)
            for _ in range(3):
                job.advance()
            return {"count": 3}

        handle = jobs.launch(engine, "unit_success", work, payload={"n": 3})
        # 提交即落库为非终态
        assert jobs.get_job(engine, handle.id)["status"] in ("queued", "running")
        done = await _drain(engine, handle.id)
        assert done["status"] == "succeeded"
        assert done["result"] == {"count": 3}
        assert done["processed"] == 3
        assert done["total"] == 3
        assert done["ended_at"] is not None

    asyncio.run(scenario())


def test_launch_captures_failure(tmp_path):
    engine = _sink(tmp_path).engine

    async def scenario():
        async def work(job):
            raise RuntimeError("boom")

        handle = jobs.launch(engine, "unit_failure", work)
        done = await _drain(engine, handle.id)
        assert done["status"] == "failed"
        assert "boom" in (done["error"] or "")

    asyncio.run(scenario())


def test_state_survives_new_engine(tmp_path):
    """状态落库：用另一个连到同库的引擎也能读回（模拟进程重启/跨进程）。"""
    sink = _sink(tmp_path, "persist.db")

    async def scenario():
        async def work(job):
            job.set_total(1)
            job.advance()
            return {"ok": True}

        handle = jobs.launch(sink.engine, "persist_check", work)
        await _drain(sink.engine, handle.id)
        return handle.id

    job_id = asyncio.run(scenario())

    # 另开一个引擎连同一个库文件读回
    reopened = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'persist.db'}")
    seen = jobs.get_job(reopened.engine, job_id)
    assert seen is not None
    assert seen["status"] == "succeeded"
    assert seen["result"] == {"ok": True}


def test_get_missing_job_returns_none(tmp_path):
    engine = _sink(tmp_path).engine
    assert jobs.get_job(engine, "does-not-exist") is None


def test_list_jobs_orders_and_filters(tmp_path):
    engine = _sink(tmp_path).engine

    async def scenario():
        async def work(job):
            return {}

        ids = []
        for i in range(3):
            ids.append(jobs.launch(engine, "typeA" if i < 2 else "typeB", work).id)
            await asyncio.sleep(0.005)
        for jid in ids:
            await _drain(engine, jid)

        all_jobs = jobs.list_jobs(engine)
        assert len(all_jobs) == 3
        # 按 created_at 降序：最后提交的在最前
        assert all_jobs[0]["job_id"] == ids[-1]
        only_a = jobs.list_jobs(engine, job_type="typeA")
        assert {j["type"] for j in only_a} == {"typeA"}
        assert len(only_a) == 2

    asyncio.run(scenario())


def _seed_users(engine):
    import datetime
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
    with __import__("sqlmodel").Session(engine) as session:
        for username, password, role in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(
                username=username, password_hash=accounts_service.hash_password(password),
                role=role, is_active=True, created_at=now, updated_at=now,
            ))
        session.commit()


def test_vectorize_all_pending_end_to_end_via_endpoint(monkeypatch, tmp_path):
    """端到端：POST /api/vectorize/all-pending → job_id → 轮询 /api/jobs/{id} 到 succeeded。"""
    from sqlmodel import Session
    from fastapi.testclient import TestClient
    from models.db import ArticleRecord
    import api.app as app_module

    sink = _sink(tmp_path, "e2e.db")
    _seed_users(sink.engine)
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="x1", title="t", content_type="web_article", source_id="s",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content="body", is_vectorized=False,
        ))
        session.commit()
    monkeypatch.setattr(app_module, "db_sink", sink)

    class FakeVectorSink:
        async def save(self, content):
            return True

    monkeypatch.setattr(app_module, "vector_sink", FakeVectorSink())

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        resp = client.post("/api/vectorize/all-pending")
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # TestClient 同步轮询：每次 GET 驱动事件循环推进后台任务。
        final = None
        for _ in range(200):
            final = client.get(f"/api/jobs/{job_id}").json()
            if final["status"] in ("succeeded", "failed"):
                break
        assert final["status"] == "succeeded", final
        assert final["result"] == {"count": 1, "total_pending": 1}
        # 落库确认已向量化
        with Session(sink.engine) as session:
            assert session.get(ArticleRecord, "x1").is_vectorized is True


def test_to_dict_shape(tmp_path):
    engine = _sink(tmp_path).engine

    async def scenario():
        async def work(job):
            return {}

        handle = jobs.launch(engine, "shape_check", work)
        await _drain(engine, handle.id)
        payload = jobs.get_job(engine, handle.id)
        assert payload["job_id"] == handle.id
        assert payload["type"] == "shape_check"
        assert set(payload) >= {
            "job_id", "type", "status", "total", "processed",
            "result", "error", "created_at", "started_at", "ended_at",
        }

    asyncio.run(scenario())
