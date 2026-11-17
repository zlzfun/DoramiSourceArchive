"""Bounded, process-local QA diagnostics; no question, answer, quote or username.

Not an audit ledger or semantic verdict. A restart clears it; multiple workers
have independent windows. Stored snapshots expire after an hour, at most 100.
"""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time

MAX_RECORDS = 100
TTL_SECONDS = 3600
_records = deque(maxlen=MAX_RECORDS)
_lock = threading.Lock()


def _expire(now):
    while _records and now - _records[0][0] >= TTL_SECONDS:
        _records.popleft()


def record(state):
    snapshot = state.diagnostic()
    snapshot["created_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        now = time.monotonic()
        _expire(now)
        _records.append((now, snapshot))


def recent():
    with _lock:
        _expire(time.monotonic())
        return {"items": deepcopy([row for _, row in reversed(_records)]),
                "retention_seconds": TTL_SECONDS, "max_records": MAX_RECORDS,
                "storage": "process_memory", "semantic_status": "not_evaluated"}
