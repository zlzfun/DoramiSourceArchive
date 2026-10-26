"""Process-local wakeups, never the durable source of translation state.

Subscriptions always re-read fenced DB state. Periodic rechecks cover another
worker process, lost wakeups and permission changes.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import threading

_lock = threading.Lock()
_waiters: dict[tuple[str, str], set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}


def _key(engine, article_id):
    return str(engine.url), article_id


def notify(engine, article_id):
    with _lock:
        listeners = tuple(_waiters.get(_key(engine, article_id), ()))
    for loop, event in listeners:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # Listener loop is closing; next subscriber re-reads.
            pass


@asynccontextmanager
async def watch(engine, article_id):
    entry = asyncio.get_running_loop(), asyncio.Event()
    key = _key(engine, article_id)
    with _lock:
        _waiters.setdefault(key, set()).add(entry)
    try:
        yield entry[1]
    finally:
        with _lock:
            group = _waiters.get(key)
            if group:
                group.discard(entry)
                if not group:
                    _waiters.pop(key, None)
