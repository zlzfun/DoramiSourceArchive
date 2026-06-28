"""轻量后台任务注册表（阶段0 过渡版）。

把会跑很久的管理操作（全量向量化、全量重索引）从「同步占满整个 HTTP 请求」
改为「提交后立即返回 job_id，前端轮询状态」。本实现为**进程内、内存态**的过渡方案：
任务以 ``asyncio.create_task`` 跑在同一事件循环上（其内部的 CPU 重操作已由
向量层经 ``asyncio.to_thread`` 卸载，故不阻塞循环），进度/状态存在内存 dict。

局限（留待阶段3 的持久化 jobs 状态机解决）：
- 进程重启会丢失任务与进度；
- 无法跨多实例可见；
- 无持久化结果、无断点续跑。
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

_logger = logging.getLogger("dorami.jobs")

# 终态任务保留时长（秒）：供前端轮询取最终结果，过期后清理避免内存泄漏。
_FINISHED_TTL_SECONDS = 3600


class Job:
    """单个后台任务的状态与进度。"""

    def __init__(self, job_type: str) -> None:
        self.id: str = uuid.uuid4().hex
        self.type: str = job_type
        # queued -> running -> succeeded | failed
        self.status: str = "queued"
        self.total: Optional[int] = None
        self.processed: int = 0
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.created_at: float = time.time()
        self.started_at: Optional[float] = None
        self.ended_at: Optional[float] = None

    def set_total(self, total: int) -> None:
        self.total = total

    def advance(self, step: int = 1) -> None:
        self.processed += step

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.id,
            "type": self.type,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


_JOBS: Dict[str, Job] = {}
# 持有运行中的 asyncio.Task 强引用：asyncio 只对任务保留弱引用，不持有会被 GC
# 提前回收/取消。done 回调里再移除。
_TASKS: set = set()


def _cleanup() -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, job in _JOBS.items()
        if job.is_terminal and job.ended_at and now - job.ended_at > _FINISHED_TTL_SECONDS
    ]
    for job_id in stale:
        _JOBS.pop(job_id, None)


def get_job(job_id: str) -> Optional[Job]:
    _cleanup()
    return _JOBS.get(job_id)


def all_jobs() -> Dict[str, Job]:
    _cleanup()
    return dict(_JOBS)


def launch(job_type: str, work: Callable[[Job], Awaitable[Optional[Dict[str, Any]]]]) -> Job:
    """提交一个后台任务并立即返回其 ``Job``。

    ``work`` 是一个接收 ``Job`` 的协程函数，可在执行中调用 ``job.set_total`` /
    ``job.advance`` 上报进度，返回值（dict）作为最终结果存入 ``job.result``。
    """
    _cleanup()
    job = Job(job_type)
    _JOBS[job.id] = job

    async def _runner() -> None:
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = await work(job) or {}
            job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001 后台任务异常收敛进 job.error
            job.error = str(exc)
            job.status = "failed"
            _logger.error("后台任务失败 [%s/%s]: %s", job_type, job.id, exc, exc_info=True)
        finally:
            job.ended_at = time.time()

    task = asyncio.create_task(_runner())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    _logger.info("已提交后台任务 [%s/%s]", job_type, job.id)
    return job
