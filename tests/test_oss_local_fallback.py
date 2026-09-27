"""Leaving OSS requires verified local bytes before discarding remote locations."""
import hashlib
import pytest
from sqlmodel import Session, select

from config_oss import OssConfig
from models.db import MediaAssetRecord, ObjectBlobRecord
from services.object_storage import ObjectStorage, ObjectStorageError
from services.object_storage_maintenance import execute
from storage.impl.db_storage import DatabaseStorage
from tests.test_object_storage import FakeBucket, remote_store


def media_object(sink, store, body, suffix):
    digest = hashlib.sha256(body).hexdigest()
    path = store.root / digest[:2] / f"{digest}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    store.persist(path, digest, ".png", len(body), "image/png")
    with Session(sink.engine) as session:
        session.add(MediaAssetRecord(url_hash=suffix, url="https://example.test/" + suffix,
                    content_hash=digest, ext=".png", size_bytes=len(body), mime="image/png",
                    status="cached", created_at="2026-09-16", updated_at="2026-09-16"))
        session.commit()
    return path


def registered_count(sink):
    with Session(sink.engine) as session:
        return len(session.exec(select(ObjectBlobRecord)).all())


def test_fallback_requires_restore_and_retains_entire_index_on_error(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'fallback.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    good = media_object(sink, store, b"local-good", "good")
    missing = media_object(sink, store, b"restore-me", "missing")
    missing.unlink()
    stores = {"media": store}
    with pytest.raises(ValueError, match="offline"):
        execute(sink.engine, stores, action="finalize-local", apply=True)
    reports = []
    plan = execute(sink.engine, stores, action="check-local", emit=reports.append)
    assert plan["errors"] == 1 and not plan["local_ready"]
    assert bucket.reads == 0 and registered_count(sink) == 2
    assert any(row.get("error") == "object_storage_local_file_missing" for row in reports)
    result = execute(sink.engine, stores, action="finalize-local", apply=True, offline=True)
    assert result["errors"] == 1 and result["removed_registry_records"] == 0
    assert registered_count(sink) == 2 and good.read_bytes() == b"local-good"

    assert execute(sink.engine, stores, action="restore", apply=True, offline=True)["errors"] == 0
    reads_after_restore = bucket.reads
    plan = execute(sink.engine, stores, action="finalize-local")
    assert plan["local_ready"] and registered_count(sink) == 2
    finished = execute(sink.engine, stores, action="finalize-local", apply=True, offline=True)
    assert finished["local_ready"] and finished["removed_registry_records"] == 2
    assert registered_count(sink) == 0 and len(bucket.objects) == 2
    assert bucket.reads == reads_after_restore
    assert missing.read_bytes() == b"restore-me"
    local = ObjectStorage(sink.engine, store.root, "media", OssConfig())
    assert local.materialize(missing, hashlib.sha256(b"restore-me").hexdigest(), ".png", 10) == missing


def test_fallback_checks_full_hash_without_cloud_credentials(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'corruption.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    path = media_object(sink, store, b"original", "image")
    path.write_bytes(b"tampered")
    local = ObjectStorage(sink.engine, store.root, "media", OssConfig())
    result = execute(sink.engine, {"media": local}, action="finalize-local", apply=True, offline=True)
    assert result["errors"] == 1 and registered_count(sink) == 1
    assert bucket.reads == 0


def test_local_backend_with_historical_cold_registry_refuses_cloud(tmp_path, monkeypatch):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'historical.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    body = b"archived-media"
    path = media_object(sink, store, body, "image")
    path.unlink()
    local = ObjectStorage(sink.engine, store.root, "media", OssConfig())

    def forbidden(*args, **kwargs):
        raise AssertionError("local backend must not access cloud")

    monkeypatch.setattr(local, "_bucket", forbidden)
    with pytest.raises(ObjectStorageError, match="local_copy_missing"):
        local.materialize(path, hashlib.sha256(body).hexdigest(), ".png", len(body))
    assert bucket.reads == 0 and registered_count(sink) == 1


def test_finalize_selected_namespace_keeps_other_registry_and_cloud_orphans(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'namespaces.db'}")
    bucket = FakeBucket()
    media = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    audio = remote_store(sink.engine, tmp_path / "audio", "podcast", bucket)
    media_object(sink, media, b"image", "image")
    orphan = tmp_path / "orphan.wav"
    orphan.write_bytes(b"audio")
    audio.persist(orphan, hashlib.sha256(b"audio").hexdigest(), ".wav", 5, "audio/wav")
    result = execute(sink.engine, {"media": media}, action="finalize-local", apply=True, offline=True)
    assert result["removed_registry_records"] == 1 and registered_count(sink) == 1
    reports = []
    result = execute(sink.engine, {"podcast": audio}, action="finalize-local", apply=True, offline=True,
                     emit=reports.append)
    assert result["local_ready"] and registered_count(sink) == 0 and len(bucket.objects) == 2
    assert reports[0]["result"] == "unreferenced_remote_retained"
    assert reports[0]["remote_location"]["object_key"].startswith("test/writer/podcast/")


def test_cli_local_precheck_is_read_only_and_reports_missing_bytes(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    database = tmp_path / "cli.db"
    sink = DatabaseStorage(f"sqlite:///{database}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    path = media_object(sink, store, b"verified", "image")
    sink.engine.dispose()
    config = tmp_path / "local.ini"
    config.write_text(f"[storage]\ndatabase_url=sqlite:///{database}\n"
                      f"[media]\nmedia_dir={store.root}\n"
                      f"[podcast_artifacts]\nroot_dir={tmp_path / 'audio'}\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("DORAMI_")}
    env["DORAMI_CONFIG_FILE"] = str(config)
    before = database.read_bytes()
    command = [sys.executable, str(Path(__file__).parents[1] / "scripts/migrate_media_oss.py"), "check-local"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["summary"]["local_ready"]
    assert database.read_bytes() == before
    path.unlink()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert not json.loads(result.stdout.splitlines()[-1])["summary"]["local_ready"]
    assert database.read_bytes() == before
