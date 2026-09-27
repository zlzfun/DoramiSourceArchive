"""Normalization and manifest concurrency checks survive the upload transaction gap."""
import hashlib

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from config_oss import OssConfig
from models.db import MediaAssetRecord
from services.archive_sync_v2 import SyncV2Error, install_media_bytes
from services.object_storage import ObjectStorage
from tests.test_media_store import PNG_BYTES, _sink
from tests.test_object_storage import FakeBucket, oss_config


@pytest.fixture
def media_manifest(tmp_path):
    sink = _sink(tmp_path)
    root = tmp_path / "media"
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    key = hashlib.sha256(b"https://example.test/synced.png").hexdigest()

    def declare(ext=".PNG", mime="image/png; charset=utf-8"):
        with Session(sink.engine) as session:
            session.add(MediaAssetRecord(url_hash=key, url="https://example.test/synced.png",
                status="pending_sync", content_hash=digest, mime=mime, ext=ext, size_bytes=len(PNG_BYTES),
                sync_authority_id="producer", sync_authority_revision="42",
                created_at="2026-09-16", updated_at="2026-09-16"))
            session.commit()
    return sink, root, digest, key, declare


@pytest.mark.parametrize("backend", ["none", "local", "oss"])
@pytest.mark.parametrize("ext,mime", [
    (".PNG", "image/png"), (".png", "image/png; charset=utf-8"), (".PNG", "IMAGE/PNG; charset=utf-8"),
])
def test_sync_normalizes_original_declaration_after_persist(media_manifest, backend, ext, mime):
    sink, root, digest, key, declare = media_manifest
    declare(ext, mime)
    bucket = FakeBucket()
    adapter = None if backend == "none" else ObjectStorage(
        sink.engine, root, "media", oss_config() if backend == "oss" else OssConfig(), bucket_factory=lambda _: bucket)
    record = install_media_bytes(sink.engine, root, key, PNG_BYTES, object_storage=adapter)
    assert record.status == "cached" and record.mime == "image/png" and record.ext == ".png"
    assert record.updated_at == "2026-09-16" and record.sync_authority_revision == "42"
    assert (root / digest[:2] / f"{digest}.png").read_bytes() == PNG_BYTES
    with Session(sink.engine) as session:
        saved = session.get(MediaAssetRecord, key)
        assert saved.mime == "image/png" and saved.ext == ".png"
    assert bucket.uploads == (1 if backend == "oss" else 0)


@pytest.mark.parametrize("field,replacement", [
    ("content_hash", "a" * 64), ("size_bytes", 123), ("ext", ".png"),
    ("mime", "image/png"), ("sync_authority_id", "different-producer"),
    ("sync_authority_revision", "43"), ("updated_at", "2026-09-17"), ("status", "failed"),
])
@pytest.mark.parametrize("backend", ["local", "oss"])
def test_sync_refuses_changed_declaration_during_persist(media_manifest, monkeypatch, backend, field, replacement):
    sink, root, _, key, declare = media_manifest
    declare()
    bucket = FakeBucket()
    adapter = ObjectStorage(sink.engine, root, "media", oss_config() if backend == "oss" else OssConfig(),
                            bucket_factory=lambda _: bucket)
    persist = adapter.persist

    def concurrent_change(*args):
        persist(*args)
        with Session(sink.engine) as session:
            row = session.get(MediaAssetRecord, key)
            setattr(row, field, replacement)
            session.add(row)
            session.commit()

    monkeypatch.setattr(adapter, "persist", concurrent_change)
    with pytest.raises(SyncV2Error, match="manifest changed"):
        install_media_bytes(sink.engine, root, key, PNG_BYTES, object_storage=adapter)
    with Session(sink.engine) as session:
        row = session.get(MediaAssetRecord, key)
        assert getattr(row, field) == replacement
        assert row.status == (replacement if field == "status" else "pending_sync")


def test_sync_final_publication_is_atomic_with_identity_comparison(media_manifest):
    sink, root, _, key, declare = media_manifest
    declare()
    concurrent = create_engine(sink.engine.url)
    armed = True

    def before_write(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal armed
        if armed and statement.lstrip().startswith("UPDATE media_assets"):
            armed = False
            with concurrent.begin() as connection:
                connection.execute(text("UPDATE media_assets SET sync_authority_revision='43' WHERE url_hash=:key"), {"key": key})

    event.listen(sink.engine, "before_cursor_execute", before_write)
    try:
        with pytest.raises(SyncV2Error, match="manifest changed"):
            install_media_bytes(sink.engine, root, key, PNG_BYTES,
                                object_storage=ObjectStorage(sink.engine, root, "media", OssConfig()))
    finally:
        event.remove(sink.engine, "before_cursor_execute", before_write)
        concurrent.dispose()
    assert not armed
    with Session(sink.engine) as session:
        row = session.get(MediaAssetRecord, key)
        assert row.sync_authority_revision == "43" and row.status == "pending_sync"
        assert row.ext == ".PNG" and row.mime == "image/png; charset=utf-8"


def test_sync_does_not_recreate_manifest_deleted_during_persist(media_manifest, monkeypatch):
    sink, root, _, key, declare = media_manifest
    declare()
    adapter = ObjectStorage(sink.engine, root, "media", OssConfig())

    def delete(*_args):
        with Session(sink.engine) as session:
            session.delete(session.get(MediaAssetRecord, key))
            session.commit()

    monkeypatch.setattr(adapter, "persist", delete)
    with pytest.raises(SyncV2Error, match="manifest changed"):
        install_media_bytes(sink.engine, root, key, PNG_BYTES, object_storage=adapter)
    with Session(sink.engine) as session:
        assert session.get(MediaAssetRecord, key) is None
