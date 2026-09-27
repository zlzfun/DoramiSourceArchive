"""Admin visibility and periodic storage maintenance remain opt-in and node-local."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import datetime as dt
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient
import pytest

from config import RuntimeConfig
from config_backup import BackupConfig
from config_oss import OssConfig
from services.media_store import MediaStore
from services.object_storage import ObjectStorage
from services.storage_backup import BackupService
from services.storage_runtime import maintain_storage
from tests.test_object_storage import oss_config
from tests.test_podcast_artifacts import _login, _setup_app


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    app, sink, podcast = _setup_app(monkeypatch, tmp_path)
    config = OssConfig(access_key_id="unused-id", access_key_secret="unused-secret",
                       security_token="unused-security-token")

    def forbidden_bucket(_):
        pytest.fail("status or scheduler inspection must not call OSS")

    media = MediaStore(sink.engine, tmp_path / "media", object_storage=ObjectStorage(
        sink.engine, tmp_path / "media", "media", config, bucket_factory=forbidden_bucket,
    ))
    podcast.object_storage = ObjectStorage(sink.engine, podcast.root, "podcast", config,
                                           bucket_factory=forbidden_bucket)
    backup_config = BackupConfig(local_dir=str(tmp_path / "backups"),
                                 access_key_secret="unused-backup-secret")
    backup = BackupService(backup_config, str(sink.engine.url), tmp_path / "receipts",
                           media_root=media.root, podcast_root=podcast.root)
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(app, "media_store", media)
    monkeypatch.setattr(app, "storage_backup_service", backup)
    monkeypatch.setattr(app, "scheduler", scheduler)
    monkeypatch.setattr(app, "settings", replace(app.settings, oss=config, backup=backup_config))
    return app, media.object_storage, podcast.object_storage, backup, scheduler


def test_storage_status_authentication_and_local_defaults(runtime):
    app, _, _, _, _ = runtime
    with TestClient(app.app) as client:
        endpoint = "/api/admin/storage/status"
        assert client.get(endpoint).status_code == 401
        _login(client, "user", "user")
        assert client.get(endpoint).status_code == 403
        _login(client, "admin", "admin")
        response = client.get(endpoint)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"media", "podcast", "backup"}
    for namespace in ("media", "podcast"):
        value = data[namespace]
        assert set(value) == {"storage_backend", "remote_objects", "remote_bytes", "cache", "storage_health"}
        assert value["storage_backend"] == "local"
        assert value["remote_objects"] == value["remote_bytes"] == 0
        assert value["cache"] == {"enabled": False, "max_bytes": (2048 if namespace == "media" else 4096) * 1024 * 1024, "local_bytes": 0}
        assert value["storage_health"] == {}
    assert set(data["backup"]) == {"enabled", "destination", "interval_hours", "status",
                                   "last_success_at", "last_attempt_at", "snapshot_at", "last_error", "running", "last_size_bytes"}
    assert data["backup"]["enabled"] is False
    assert data["backup"]["status"] == "disabled"
    assert "unused-" not in response.text


def test_configured_status_exposes_observations_but_no_credentials(runtime, monkeypatch):
    app, media, _, backup, _ = runtime
    media.config = oss_config()
    media.enabled = True
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    media._state_update("cache", last_run_at=now, last_error=None, evicted_files=2, evicted_bytes=128, skipped=1)
    media._state_update("storage_health", last_success_at=now, last_error=None, last_error_at=None)
    backup.config = replace(backup.config, enabled=True, access_key_secret="private-backup-key")
    backup.directory.mkdir()
    (backup.directory / "status.json").write_text(json.dumps({
        "status": "succeeded", "last_success_at": 1_790_000_000, "last_attempt_at": 1_790_000_000,
        "files": 4, "size_bytes": 256, "external_objects": 2,
        "access_key_secret": "private-backup-key", "security_token": "private-backup-token",
    }))
    monkeypatch.setattr(app, "settings", replace(app.settings, backup=backup.config))
    # API status inspection does not need to start maintenance or make a cloud probe.
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        response = client.get("/api/admin/storage/status")
    assert response.status_code == 200
    data = response.json()
    assert set(data["media"]["cache"]) == {"enabled", "max_bytes", "local_bytes", "last_run_at",
                                           "last_error", "evicted_files", "evicted_bytes", "skipped"}
    assert set(data["media"]["storage_health"]) == {"last_success_at", "last_error", "last_error_at"}
    assert data["backup"]["last_size_bytes"] == 256
    assert data["backup"]["last_success_at"].endswith("+00:00")
    assert set(data["backup"]) <= {"enabled", "destination", "interval_hours", "status", "running",
                                  "last_success_at", "last_attempt_at", "last_error", "last_size_bytes",
                                  "files", "size_bytes", "external_objects", "error", "archive", "sha256", "object_key",
                                  "snapshot_at", "pending_upload"}
    for secret in ("test-id", "test-secret", "private-backup-key", "private-backup-token", "access_key", "security_token"):
        assert secret not in response.text


@pytest.mark.parametrize("cache_enabled,backup_enabled", [(False, False), (True, False), (False, True)])
def test_maintenance_registration_is_opt_in_and_can_be_disabled(runtime, monkeypatch, cache_enabled, backup_enabled):
    app, media, _, backup, scheduler = runtime
    media.enabled = cache_enabled
    backup.config = replace(backup.config, enabled=backup_enabled)
    monkeypatch.setattr(app, "settings", replace(app.settings, backup=backup.config))
    app.reload_storage_schedule()
    job = scheduler.get_job("storage_maintenance")
    if not cache_enabled and not backup_enabled:
        assert job is None
    else:
        assert job.func is app.execute_storage_maintenance_job
        assert job.max_instances == 1 and job.coalesce
        assert job.trigger.interval.total_seconds() == 60
    media.enabled = False
    monkeypatch.setattr(app, "settings", replace(app.settings, backup=BackupConfig()))
    app.reload_storage_schedule()
    assert scheduler.get_job("storage_maintenance") is None


def test_reader_startup_registers_maintenance_without_collector_jobs(runtime, monkeypatch):
    app, media, _, _, scheduler = runtime
    media.enabled = True
    monkeypatch.setattr(app, "settings", replace(app.settings, runtime=RuntimeConfig(role="reader")))
    with TestClient(app.app):
        assert scheduler.get_job("storage_maintenance") is not None
        assert scheduler.get_job("article_analysis") is None
        assert scheduler.get_job("retention_cleanup") is not None


def test_collection_schedule_reload_preserves_storage_maintenance(runtime):
    app, media, _, _, scheduler = runtime
    media.enabled = True
    app.reload_storage_schedule()
    for _ in range(2):
        app.load_tasks_to_scheduler()
        assert scheduler.get_job("storage_maintenance") is not None
        assert [job.id for job in scheduler.get_jobs()].count("storage_maintenance") == 1


class ObservedStore:
    """Two independent workers share only the filesystem, as in uvicorn."""

    def __init__(self, root, evict):
        self.root = Path(root)
        self.enabled = True
        self.config = SimpleNamespace(cache_enabled=True, cache_interval_seconds=300)
        self._evict = evict

    @staticmethod
    def _now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def _state(self):
        path = self.root / "status.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def _state_update(self, section, **values):
        state = self._state()
        state.setdefault(section, {}).update(values)
        (self.root / "status.json").write_text(json.dumps(state))

    def evict_cache(self):
        self._evict()
        self._state_update("cache", last_run_at=self._now(), last_error=None)


def test_multiple_workers_share_one_maintenance_lease_and_due_timestamp(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def slow_evict():
        calls.append("first")
        entered.set()
        assert release.wait(5)

    first = ObservedStore(tmp_path, slow_evict)
    second = ObservedStore(tmp_path, lambda: calls.append("second"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(maintain_storage, [first])
        try:
            assert entered.wait(5)
            executor.submit(maintain_storage, [second]).result(timeout=2)
            assert calls == ["first"]
        finally:
            release.set()
        running.result(timeout=5)
    # Once the lock is released, the persisted timestamp prevents another
    # worker from repeating a full remote verification on the next minute tick.
    maintain_storage([second])
    assert calls == ["first"]
    second._state_update("cache", last_run_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=301)).isoformat())
    maintain_storage([second])
    assert calls == ["first", "second"]


def test_failed_maintenance_is_sanitized_and_retries_after_interval(tmp_path):
    calls = []

    def failing_evict():
        calls.append("attempt")
        raise RuntimeError("private-signed-request")

    store = ObservedStore(tmp_path, failing_evict)
    maintain_storage([store])
    maintain_storage([store])
    assert calls == ["attempt"]
    assert store._state()["cache"]["last_error"] == "object_storage_cache_failed"
    assert "private-signed-request" not in json.dumps(store._state())


def test_backup_is_independent_of_disabled_cache_and_tick_uses_current_database(runtime, monkeypatch, tmp_path):
    app, media, podcast, backup, _ = runtime
    calls = []
    due_backup = SimpleNamespace(config=SimpleNamespace(enabled=True), run_if_due=lambda: calls.append("backup"))
    maintain_storage([None, media, podcast], due_backup)
    assert calls == ["backup"]

    def observed(stores, selected_backup):
        calls.append((stores, selected_backup))

    monkeypatch.setattr(app, "maintain_storage", observed)
    asyncio.run(app.execute_storage_maintenance_job())
    assert calls[-1] == ([media, podcast], backup)
    backup.database_url = f"sqlite:///{tmp_path / 'other.db'}"
    asyncio.run(app.execute_storage_maintenance_job())
    assert calls[-1] == ([media, podcast], None)
