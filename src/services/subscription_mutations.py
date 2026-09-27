"""同用户订阅写操作的进程内互斥状态。

当前两条部署路径均为 uvicorn 单 worker；与 user_sources._WRITE_LOCK 一样，这里
用进程内状态阻止批量、单源、合集、自定源及订阅管理写事务互相重叠。不同用户
保持独立，避免把全站订阅写入串成一条队列。
"""

import threading
from typing import Dict, Optional


_STATE_LOCK = threading.Lock()
_ACTIVE: Dict[str, Dict[str, Optional[str]]] = {}


def begin(username: str, operation: str, *, shape: Optional[str] = None) -> bool:
    with _STATE_LOCK:
        if username in _ACTIVE:
            return False
        _ACTIVE[username] = {"operation": operation, "shape": shape}
        return True


def finish(username: str, operation: str) -> None:
    with _STATE_LOCK:
        active = _ACTIVE.get(username)
        if active and active.get("operation") == operation:
            _ACTIVE.pop(username, None)


def status(username: str) -> Optional[Dict[str, Optional[str]]]:
    with _STATE_LOCK:
        active = _ACTIVE.get(username)
        return dict(active) if active else None
