"""Health diagnostics are best effort and recover after successful verification."""
import hashlib
import json

import pytest

from services.object_storage_maintenance import execute
from storage.impl.db_storage import DatabaseStorage
from tests.test_object_storage import FakeBucket, remote_store


def prepared(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'health.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    body = b"health-check-image"
    digest = hashlib.sha256(body).hexdigest()
    path = store.root / digest[:2] / f"{digest}.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    return sink, bucket, store, path, digest, body


@pytest.mark.parametrize("corrupt", ["{", "null", "42", "[]", '{"storage_health":"broken","cache":[]}'])
def test_corrupt_health_state_cannot_break_stats_or_successful_upload(tmp_path, corrupt):
    _, bucket, store, path, digest, body = prepared(tmp_path)
    (store.root / ".oss-status.json").write_text(corrupt)
    assert store.stats()["storage_health"] == {}
    store.persist(path, digest, ".png", len(body), "image/png")
    assert bucket.uploads == 1
    health = store.stats()["storage_health"]
    assert health["last_error"] is None
    assert health["last_success_at"]


def test_diagnostic_write_failure_does_not_fail_storage(tmp_path, monkeypatch):
    _, bucket, store, path, digest, body = prepared(tmp_path)
    from services import object_storage

    def denied(*args, **kwargs):
        raise PermissionError("diagnostic directory unavailable")

    monkeypatch.setattr(object_storage.tempfile, "mkstemp", denied)
    store.persist(path, digest, ".png", len(body), "image/png")
    assert bucket.uploads == 1
    assert store.location(digest, ".png") is not None


def test_maintenance_verify_clears_previous_cloud_error(tmp_path):
    sink, bucket, store, path, digest, body = prepared(tmp_path)
    store.persist(path, digest, ".png", len(body), "image/png")
    bucket.unavailable = True
    failed = execute(sink.engine, {"media": store}, action="verify", apply=True, offline=True)
    assert failed["errors"] == 1
    failed_health = store.stats()["storage_health"]
    assert failed_health["last_error"] == "object_storage_head_failed"
    assert failed_health["last_error_at"]
    assert "credential-and-signature" not in json.dumps(failed_health)

    bucket.unavailable = False
    recovered = execute(sink.engine, {"media": store}, action="verify", apply=True, offline=True)
    assert recovered["errors"] == 0 and recovered["completed"] == 1
    health = store.stats()["storage_health"]
    assert health["last_error"] is None
    assert health["last_success_at"] >= failed_health["last_success_at"]
