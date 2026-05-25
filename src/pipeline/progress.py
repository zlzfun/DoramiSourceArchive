"""轻量内存进度跟踪 (pipeline/progress.py)

在 Pipeline 流式处理过程中按 fetcher_id 暴露 {current, total}。
仅供前端轮询查看运行中节点的实时计数，不持久化。
"""
from typing import Any, Dict, Optional

_PROGRESS: Dict[str, Dict[str, Any]] = {}


def set_progress(fetcher_id: str, current: int, total: Optional[int]) -> None:
    if not fetcher_id:
        return
    _PROGRESS[fetcher_id] = {"current": current, "total": total}


def clear_progress(fetcher_id: str) -> None:
    if not fetcher_id:
        return
    _PROGRESS.pop(fetcher_id, None)


def get_all_progress() -> Dict[str, Dict[str, Any]]:
    return dict(_PROGRESS)
