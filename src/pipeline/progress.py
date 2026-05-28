"""轻量内存进度跟踪 (pipeline/progress.py)

在 Pipeline 流式处理过程中按 fetcher_id 暴露 {current, total}。
仅供前端轮询查看运行中节点的实时计数，不持久化。
"""
from typing import Any, Dict, Optional
import time

_PROGRESS: Dict[str, Dict[str, Any]] = {}
_COMPLETED_PROGRESS_TTL_SECONDS = 10


def set_progress(fetcher_id: str, current: int, total: Optional[int]) -> None:
    if not fetcher_id:
        return
    _PROGRESS[fetcher_id] = {
        "current": current,
        "total": total,
        "status": "running",
        "updated_at": time.time(),
    }


def complete_progress(fetcher_id: str, current: int, total: Optional[int]) -> None:
    if not fetcher_id:
        return
    _PROGRESS[fetcher_id] = {
        "current": current,
        "total": total,
        "status": "completed",
        "updated_at": time.time(),
    }


def clear_progress(fetcher_id: str) -> None:
    if not fetcher_id:
        return
    _PROGRESS.pop(fetcher_id, None)


def get_all_progress() -> Dict[str, Dict[str, Any]]:
    now = time.time()
    stale_ids = [
        fetcher_id
        for fetcher_id, progress in _PROGRESS.items()
        if progress.get("status") != "running"
        and now - float(progress.get("updated_at", 0)) > _COMPLETED_PROGRESS_TTL_SECONDS
    ]
    for fetcher_id in stale_ids:
        _PROGRESS.pop(fetcher_id, None)
    return {
        fetcher_id: {key: value for key, value in progress.items() if key != "updated_at"}
        for fetcher_id, progress in _PROGRESS.items()
    }
