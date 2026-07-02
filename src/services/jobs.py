"""持久化后台任务状态机（阶段3）。

取代进程内内存态的 ``background_jobs``：长任务（全量向量化、全量重索引、日报、批量
抓取等）提交后立即返回 job_id，执行状态/进度/结果落 ``JobRecord`` 表，从而进程重启不丢、
可跨进程/多实例查询、为 scheduler↔worker 拆分铺路。

对外沿用旧接口形态，调用方近乎无感：
- ``launch(engine, job_type, work, created_by=..., payload=...)`` 提交并立即返回 ``Job`` 句柄；
- ``work`` 是接收 ``Job`` 的协程，可 ``job.set_total`` / ``job.advance`` 上报进度，返回 dict 作结果；
- ``get_job(engine, job_id)`` / ``list_jobs(engine)`` 从库读回，形状与旧 ``to_dict`` 一致（前端无感）。

进度写库经节流（每 ``_FLUSH_EVERY`` 步或 ``_FLUSH_INTERVAL`` 秒），避免 all-pending 逐条
advance 打爆数千次 UPDATE；状态迁移（running/succeeded/failed）与 set_total 立即落库。
时间戳沿用 epoch 浮点，与旧契约一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlmodel import Session, select

from models.db import JobRecord

_logger = logging.getLogger("dorami.jobs")

# 进度落库节流：满 N 步或距上次 flush 超过 T 秒才写一次。
_FLUSH_EVERY = 25
_FLUSH_INTERVAL = 1.0

# 持有运行中的 asyncio.Task 强引用（asyncio 只保弱引用，不持有会被 GC 提前回收）。
_TASKS: set = set()


def _record_to_dict(record: JobRecord) -> Dict[str, Any]:
    """转成与旧内存版 Job.to_dict 一致的形状（前端轮询无感）。"""
    return {
        "job_id": record.id,
        "type": record.type,
        "status": record.status,
        "total": record.total,
        "processed": record.processed,
        "result": json.loads(record.result_json) if record.result_json else None,
        "error": record.error,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
    }


class Job:
    """单个后台任务的句柄：进度在内存累计，节流写回 JobRecord。"""

    def __init__(self, engine, job_id: str) -> None:
        self.engine = engine
        self.id = job_id
        self._processed = 0
        self._total: Optional[int] = None
        self._last_flush = 0.0
        self._pending = 0

    def _update(self, **fields: Any) -> None:
        with Session(self.engine) as session:
            record = session.get(JobRecord, self.id)
            if record is None:
                return
            for key, value in fields.items():
                setattr(record, key, value)
            session.add(record)
            session.commit()

    def set_total(self, total: int) -> None:
        self._total = total
        self._update(total=total)

    def advance(self, step: int = 1) -> None:
        self._processed += step
        self._pending += step
        now = time.time()
        if self._pending >= _FLUSH_EVERY or now - self._last_flush >= _FLUSH_INTERVAL:
            self._flush_progress()

    def _flush_progress(self) -> None:
        self._update(processed=self._processed)
        self._last_flush = time.time()
        self._pending = 0


def launch(
    engine,
    job_type: str,
    work: Callable[[Job], Awaitable[Optional[Dict[str, Any]]]],
    *,
    created_by: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Job:
    """提交一个后台任务：落库为 queued，spawn 执行协程，立即返回 ``Job`` 句柄。"""
    job_id = uuid.uuid4().hex
    now = time.time()
    with Session(engine) as session:
        session.add(JobRecord(
            id=job_id,
            type=job_type,
            status="queued",
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            created_by=created_by,
            created_at=now,
        ))
        session.commit()

    job = Job(engine, job_id)

    async def _runner() -> None:
        job._update(status="running", started_at=time.time())
        try:
            result = await work(job) or {}
            job._flush_progress()
            job._update(status="succeeded", result_json=json.dumps(result, ensure_ascii=False),
                        ended_at=time.time())
        except Exception as exc:  # noqa: BLE001 后台任务异常收敛进 record.error
            job._flush_progress()
            job._update(status="failed", error=str(exc), ended_at=time.time())
            _logger.error("后台任务失败 [%s/%s]: %s", job_type, job_id, exc, exc_info=True)

    task = asyncio.create_task(_runner())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    _logger.info("已提交后台任务 [%s/%s]", job_type, job_id)
    return job


def get_job(engine, job_id: str) -> Optional[Dict[str, Any]]:
    with Session(engine) as session:
        record = session.get(JobRecord, job_id)
        return _record_to_dict(record) if record else None


def list_jobs(engine, *, job_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with Session(engine) as session:
        statement = select(JobRecord)
        if job_type:
            statement = statement.where(JobRecord.type == job_type)
        statement = statement.order_by(JobRecord.created_at.desc()).limit(limit)
        return [_record_to_dict(record) for record in session.exec(statement).all()]
