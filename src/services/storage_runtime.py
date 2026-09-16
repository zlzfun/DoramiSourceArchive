"""Periodic node-local cache maintenance, independent of the collector role."""

import datetime as dt
import fcntl


def maintain_storage(stores, backup=None):
    for store in stores:
        if store is None or not store.enabled or not store.config.cache_enabled:
            continue
        try:
            store.root.mkdir(parents=True, exist_ok=True)
            # Multiple uvicorn workers may schedule a tick; one node does the work.
            with (store.root / ".oss-maintenance.lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                state = store._state().get("cache", {})
                last = state.get("last_run_at")
                if last:
                    elapsed = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).total_seconds()
                    if elapsed < store.config.cache_interval_seconds:
                        continue
                store.evict_cache()
        except Exception:
            store._state_update("cache", last_run_at=store._now(), last_error="object_storage_cache_failed")
    if backup is not None and backup.config.enabled:
        backup.run_if_due()
