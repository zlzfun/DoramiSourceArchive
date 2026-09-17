"""Cache eviction must never lose durable content or interrupt an active reader."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from api.storage_response import StorageFileResponse
from config_oss import OssConfig
from models.db import MediaAssetRecord
from services.media_store import MediaStore
from services.object_storage import ObjectStorage, ObjectStorageError
from storage.impl.db_storage import DatabaseStorage
from tests.test_object_storage import FakeBucket, oss_config


@pytest.fixture
def cached_media(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'cache.db'}")
    bucket = FakeBucket()
    config = replace(oss_config(), media_cache_max_mb=0, podcast_cache_max_mb=0,
                     cache_min_age_seconds=0)
    remote = ObjectStorage(sink.engine, tmp_path / "media", "media", config,
                           bucket_factory=lambda _: bucket)
    store = MediaStore(sink.engine, remote.root, object_storage=remote)
    # More than one FileResponse chunk so the lease must cover the whole body.
    body = b"immutable cached media\n" * 8192
    digest = hashlib.sha256(body).hexdigest()
    record = MediaAssetRecord(
        url_hash="image", url="https://images.example.test/cache.png", status="cached",
        content_hash=digest, ext=".png", mime="image/png", size_bytes=len(body),
        created_at="2026-09-16", updated_at="2026-09-16",
    )
    path = store.file_path_for(record)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    remote.persist(path, digest, record.ext, len(body), record.mime)
    return remote, store, record, path, body, bucket


def test_active_reader_survives_concurrent_eviction_and_release_allows_reclaim(cached_media):
    remote, _, record, path, body, bucket = cached_media
    with remote.pin(record.content_hash), ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(remote.evict_cache).result(timeout=5)
        assert result["evicted_files"] == 0
        assert path.read_bytes() == body
        assert bucket.reads == 0  # Busy readers are skipped before remote verification.
    result = remote.evict_cache()
    assert result["evicted_files"] == 1
    assert not path.exists()
    assert len(bucket.objects) == 1
    assert remote.materialize(path, record.content_hash, record.ext, len(body)).read_bytes() == body


def test_reader_in_another_process_protects_the_same_cached_file(cached_media):
    remote, _, record, path, body, _ = cached_media
    code = """
import json, sys
from pathlib import Path
from config_oss import OssConfig
from services.object_storage import ObjectStorage
store = ObjectStorage(None, Path(sys.argv[1]), 'media', OssConfig(**json.loads(sys.argv[3])))
with store.pin(sys.argv[2]):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(remote.root), record.content_hash, json.dumps(asdict(remote.config))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert select.select([child.stdout], [], [], 15)[0], "reader failed to acquire its lease"
        assert child.stdout.readline().strip() == "locked"
        assert remote.evict_cache()["evicted_files"] == 0
        assert path.read_bytes() == body
        child.communicate("release\n", timeout=10)
        assert child.returncode == 0
        assert remote.evict_cache()["evicted_files"] == 1
        assert not path.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


@pytest.mark.parametrize("remote_failure", ["missing", "corrupt", "unavailable"])
def test_remote_copy_must_be_complete_and_valid_before_local_eviction(cached_media, remote_failure):
    remote, _, record, path, body, bucket = cached_media
    location = remote.location(record.content_hash, record.ext)
    if remote_failure == "missing":
        bucket.objects.pop(location.object_key)
    elif remote_failure == "corrupt":
        _, headers = bucket.objects[location.object_key]
        # HEAD still reports the right length and SHA metadata: only a full GET
        # and digest comparison can detect this corruption.
        bucket.objects[location.object_key] = (b"x" * len(body), headers)
    else:
        bucket.unavailable = True
    assert remote.evict_cache()["evicted_files"] == 0
    assert path.read_bytes() == body
    assert remote.stats()["cache"]["last_error"]


def test_local_only_and_recent_files_are_not_eviction_candidates(cached_media):
    remote, _, record, path, body, bucket = cached_media
    unique_body = b"no remote copy exists for this file"
    unique_hash = hashlib.sha256(unique_body).hexdigest()
    unique_path = remote.root / unique_hash[:2] / f"{unique_hash}.png"
    unique_path.parent.mkdir(parents=True, exist_ok=True)
    unique_path.write_bytes(unique_body)
    remote.config = replace(remote.config, cache_min_age_seconds=300)
    assert remote.evict_cache()["evicted_files"] == 0
    assert path.read_bytes() == body
    assert bucket.reads == 0
    aged = time.time() - 600
    os.utime(path, (aged, aged))
    assert remote.evict_cache()["evicted_files"] == 1
    assert unique_path.read_bytes() == unique_body
    assert remote.location(unique_hash, record.ext) is None


@pytest.mark.parametrize("local_backend", [True, False])
def test_disabled_cache_performs_no_cloud_requests(cached_media, local_backend):
    remote, _, record, path, body, _ = cached_media
    config = OssConfig() if local_backend else replace(remote.config, cache_enabled=False)

    def forbidden_bucket(_):
        pytest.fail("disabled cache contacted object storage")

    disabled = ObjectStorage(remote.engine, remote.root, "media", config, bucket_factory=forbidden_bucket)
    assert disabled.evict_cache()["evicted_files"] == 0
    assert disabled.materialize(path, record.content_hash, record.ext, len(body)).read_bytes() == body
    if local_backend:
        disabled.persist(path, record.content_hash, record.ext, len(body), record.mime)
        path.unlink()
        with pytest.raises(ObjectStorageError, match="local_copy_missing"):
            disabled.materialize(path, record.content_hash, record.ext, len(body))


def _scope(method="GET", headers=()):
    return {
        "type": "http", "method": method, "path": "/media", "headers": list(headers),
        "extensions": {"http.response.pathsend": {}},
    }


async def _receive():
    return {"type": "http.disconnect"}


@pytest.mark.parametrize("method,headers,status", [
    ("GET", (), 200),
    ("GET", ((b"range", b"bytes=4-19"),), 206),
    ("HEAD", (), 200),
])
def test_deferred_response_restores_evicted_file_and_pins_through_send(cached_media, method, headers, status):
    remote, store, record, path, body, _ = cached_media
    response = StorageFileResponse(store, record, media_type=record.mime)
    assert remote.evict_cache()["evicted_files"] == 1
    assert not path.exists()
    messages = []

    async def send(message):
        assert path.exists()
        assert remote.evict_cache()["evicted_files"] == 0
        messages.append(message)

    asyncio.run(response(_scope(method, headers), _receive, send))
    assert messages[0]["status"] == status
    assert all(message["type"] != "http.response.pathsend" for message in messages)
    delivered = b"".join(message.get("body", b"") for message in messages)
    assert delivered == (b"" if method == "HEAD" else body[4:20] if status == 206 else body)
    response_headers = dict(messages[0]["headers"])
    assert response_headers[b"content-length"] == str(16 if status == 206 else len(body)).encode()
    assert remote.evict_cache()["evicted_files"] == 1


def test_disconnected_response_releases_lease(cached_media):
    remote, store, record, path, _, _ = cached_media
    response = StorageFileResponse(store, record, media_type=record.mime)

    async def send(message):
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError("client disconnected")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(response(_scope(), _receive, send))
    with remote.pin(record.content_hash, exclusive=True, blocking=False) as acquired:
        assert acquired
    assert remote.evict_cache()["evicted_files"] == 1
    assert not path.exists()


def test_open_audio_descriptor_remains_readable_after_cache_eviction(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import WAV, _setup_app

    _, sink, store = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    remote = ObjectStorage(sink.engine, store.root, "podcast", replace(
        oss_config(), podcast_cache_max_mb=0, cache_min_age_seconds=0,
    ), bucket_factory=lambda _: bucket)
    store.object_storage = remote
    artifact = store.import_bytes(
        episode_id="episode-1", kind="digest_audio_zh", data=WAV, declared_mime="audio/wav",
        provenance="premium_guide_tts", authority_id="", narration_artifact_id="script-episode-1",
        narration_content_hash=hashlib.sha256("episode-1 的中文口播稿。".encode()).hexdigest(),
    )
    store.publish(artifact.id, expected_updated_at=artifact.updated_at)
    _, handle = store.open_readable_audio(artifact.id, admin=False)
    with handle:
        assert remote.evict_cache()["evicted_files"] == 1
        assert not store.file_path_for(artifact).exists()
        assert handle.read() == WAV
    assert len(bucket.objects) == 1


def _additional_cache_file(remote, body=b"newer healthy cached file"):
    digest = hashlib.sha256(body).hexdigest()
    path = remote.root / digest[:2] / f"{digest}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    remote.persist(path, digest, ".png", len(body), "image/png")
    touched = time.time() - 300
    os.utime(path, (touched, touched))
    return path, body


def test_corrupt_oldest_file_is_isolated_without_blocking_newer_eviction(cached_media):
    remote, _, record, path, body, bucket = cached_media
    newer, newer_body = _additional_cache_file(remote)
    corrupt = b"x" * len(body)
    path.write_bytes(corrupt)
    touched = time.time() - 600
    os.utime(path, (touched, touched))
    quarantine = path.with_name(path.name + ".corrupt")

    result = remote.evict_cache()
    assert result == {"evicted_files": 1, "evicted_bytes": len(newer_body), "skipped": 0}
    assert not path.exists() and not newer.exists()
    assert quarantine.read_bytes() == corrupt
    assert remote.local_bytes() == len(body)  # Renaming reclaimed no disk space.
    assert remote.stats()["cache"]["last_error"] == "object_storage_local_checksum_mismatch"

    bucket.unavailable = True
    with pytest.raises(ObjectStorageError, match="download_failed"):
        remote.materialize(path, record.content_hash, record.ext, len(body))
    assert not path.exists() and quarantine.read_bytes() == corrupt
    bucket.unavailable = False
    assert remote.materialize(path, record.content_hash, record.ext, len(body)).read_bytes() == body

    # Recovery makes the canonical file hot and brings the cache under target;
    # neither condition may indefinitely retain the diagnostic duplicate.
    remote.config = replace(remote.config, media_cache_max_mb=8, cache_min_age_seconds=3600)
    assert remote.local_bytes() == 2 * len(body)
    assert remote.evict_cache()["evicted_bytes"] == len(body)
    assert not quarantine.exists() and path.read_bytes() == body
    assert remote.local_bytes() == len(body)
    assert remote.stats()["cache"]["last_error"] is None


def test_quarantine_only_cleanup_requires_full_remote_verification(cached_media):
    remote, _, record, path, body, bucket = cached_media
    quarantine = path.with_name(path.name + ".corrupt")
    path.write_bytes(b"x" * len(body))
    assert remote.evict_cache()["evicted_files"] == 0
    remote.config = replace(remote.config, media_cache_max_mb=8)
    location = remote.location(record.content_hash, record.ext)
    original = bucket.objects[location.object_key]
    # Matching HEAD metadata is insufficient to discard the diagnostic copy.
    bucket.objects[location.object_key] = (b"y" * len(body), original[1])
    assert remote.evict_cache()["evicted_files"] == 0
    assert quarantine.exists() and not path.exists()
    bucket.objects[location.object_key] = original
    assert remote.evict_cache()["evicted_bytes"] == len(body)
    assert not quarantine.exists() and remote.local_bytes() == 0
    assert remote.materialize(path, record.content_hash, record.ext, len(body)).read_bytes() == body


def test_quarantine_uses_one_non_media_name_and_respects_busy_readers(cached_media):
    remote, _, record, path, body, bucket = cached_media
    quarantine = path.with_name(path.name + ".corrupt")
    quarantine.write_bytes(b"older diagnostic copy")
    path.write_bytes(b"x" * len(body))
    with remote.pin(record.content_hash):
        assert remote.evict_cache()["evicted_files"] == 0
        assert path.exists() and quarantine.read_bytes() == b"older diagnostic copy"
        assert bucket.reads == 0
    assert remote.evict_cache()["evicted_files"] == 0
    assert not path.exists() and quarantine.read_bytes() == b"x" * len(body)
    assert list(path.parent.glob("*.corrupt")) == [quarantine]
    assert remote.local_bytes() == len(body)
    with remote.pin(record.content_hash):
        assert remote.evict_cache()["evicted_files"] == 0
        assert quarantine.exists() and bucket.reads == 0
    assert remote.evict_cache()["evicted_files"] == 1


def test_reader_arriving_during_remote_verification_defers_quarantine_cleanup(monkeypatch, cached_media):
    remote, _, record, path, body, _ = cached_media
    path.write_bytes(b"x" * len(body))
    assert remote.evict_cache()["evicted_files"] == 0
    quarantine = path.with_name(path.name + ".corrupt")
    original = remote.verify_remote
    lease = remote.pin(record.content_hash)

    def verify_with_reader(row):
        original(row)
        lease.__enter__()

    monkeypatch.setattr(remote, "verify_remote", verify_with_reader)
    try:
        assert remote.evict_cache() == {"evicted_files": 0, "evicted_bytes": 0, "skipped": 1}
        assert quarantine.exists()
    finally:
        lease.__exit__(None, None, None)
    monkeypatch.setattr(remote, "verify_remote", original)
    assert remote.evict_cache()["evicted_files"] == 1


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_quarantined_bytes_are_never_served_when_remote_is_unavailable(cached_media, method):
    remote, store, record, path, body, bucket = cached_media
    path.write_bytes(b"x" * len(body))
    assert remote.evict_cache()["evicted_files"] == 0
    bucket.unavailable = True
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(StorageFileResponse(store, record, media_type=record.mime)(_scope(method), _receive, send))
    assert messages[0]["status"] == 503
    assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
    assert not path.exists()
    assert path.with_name(path.name + ".corrupt").read_bytes() == b"x" * len(body)
    with remote.pin(record.content_hash, exclusive=True, blocking=False) as acquired:
        assert acquired


@pytest.mark.parametrize("failure_phase", ["stat", "hash", "unlink"])
def test_local_candidate_errors_do_not_stop_other_files(monkeypatch, cached_media, failure_phase):
    import services.object_storage as object_module

    remote, _, _, path, body, _ = cached_media
    newer, newer_body = _additional_cache_file(remote)
    touched = time.time() - 600
    os.utime(path, (touched, touched))
    if failure_phase == "hash":
        owner, name = object_module, "hash_file"
    else:
        owner, name = Path, "lstat" if failure_phase == "stat" else "unlink"
    original = getattr(owner, name)

    def unreadable(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("local diagnostic details must not escape")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(owner, name, unreadable)
    assert remote.evict_cache()["evicted_bytes"] == len(newer_body)
    assert path.read_bytes() == body and not newer.exists()
    assert remote.stats()["cache"]["last_error"] == "object_storage_cache_failed"


def test_remote_failure_still_stops_the_round_without_fanout(monkeypatch, cached_media):
    remote, _, _, path, _, bucket = cached_media
    newer, _ = _additional_cache_file(remote)
    calls = []
    original = remote.verify_remote

    def verify(record):
        calls.append(record.id)
        return original(record)

    monkeypatch.setattr(remote, "verify_remote", verify)
    bucket.unavailable = True
    assert remote.evict_cache()["evicted_files"] == 0
    assert len(calls) == 1
    assert path.exists() and newer.exists()


def test_podcast_quarantine_does_not_change_quota_or_get_orphan_collected(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import WAV, _setup_app

    _, sink, store = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    remote = ObjectStorage(sink.engine, store.root, "podcast", replace(
        oss_config(), podcast_cache_max_mb=0, cache_min_age_seconds=0,
    ), bucket_factory=lambda _: bucket)
    store.object_storage = remote
    artifact = store.import_bytes(
        episode_id="episode-1", kind="digest_audio_zh", data=WAV, declared_mime="audio/wav",
        provenance="premium_guide_tts", authority_id="", narration_artifact_id="script-episode-1",
        narration_content_hash=hashlib.sha256("episode-1 的中文口播稿。".encode()).hexdigest(),
    )
    path = store.file_path_for(artifact)
    before = store._durable_blob_bytes()
    path.write_bytes(b"x" * len(WAV))
    assert remote.evict_cache()["evicted_files"] == 0
    quarantine = path.with_name(path.name + ".corrupt")
    assert quarantine.exists() and not path.exists()
    assert store._blob_files() == []
    assert store._durable_blob_bytes() == before == len(WAV)
    assert remote.local_bytes() == len(WAV)
    assert store.reconcile_storage()["deleted_orphan_blobs"] == 0
    assert quarantine.exists()
