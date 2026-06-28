import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_launch_runs_work_and_records_success():
    from services import background_jobs

    async def scenario():
        async def work(job):
            job.set_total(3)
            for _ in range(3):
                job.advance()
            return {"count": 3}

        job = background_jobs.launch("unit_success", work)
        assert job.status in ("queued", "running")
        # 让事件循环把后台任务跑到终态
        for _ in range(50):
            if background_jobs.get_job(job.id).is_terminal:
                break
            await asyncio.sleep(0.01)
        done = background_jobs.get_job(job.id)
        assert done.status == "succeeded"
        assert done.result == {"count": 3}
        assert done.processed == 3
        assert done.total == 3
        assert done.ended_at is not None

    asyncio.run(scenario())


def test_launch_captures_failure():
    from services import background_jobs

    async def scenario():
        async def work(job):
            raise RuntimeError("boom")

        job = background_jobs.launch("unit_failure", work)
        for _ in range(50):
            if background_jobs.get_job(job.id).is_terminal:
                break
            await asyncio.sleep(0.01)
        done = background_jobs.get_job(job.id)
        assert done.status == "failed"
        assert "boom" in (done.error or "")

    asyncio.run(scenario())


def test_get_missing_job_returns_none():
    from services import background_jobs

    assert background_jobs.get_job("does-not-exist") is None


def test_job_to_dict_shape():
    from services import background_jobs

    job = background_jobs.Job("shape_check")
    payload = job.to_dict()
    assert payload["job_id"] == job.id
    assert payload["type"] == "shape_check"
    assert payload["status"] == "queued"
    assert set(payload) >= {
        "job_id", "type", "status", "total", "processed",
        "result", "error", "created_at", "started_at", "ended_at",
    }
