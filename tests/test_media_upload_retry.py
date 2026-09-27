"""Cloud outages retain downloaded media and retry without source negative caching."""
import asyncio
import datetime as dt
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import Session

from config_oss import OssConfig
from models.db import ArticleRecord, MediaAssetRecord
from services import archive_sync_v2, media_store as media_module
from services.media_store import MediaStore, url_hash_of
from services.object_storage import ObjectStorage
from services.object_storage_maintenance import execute, inventory
from tests.test_media_store import PNG_BYTES, _counting_transport, _public_ok, _sink
from tests.test_object_storage import FakeBucket, oss_config


URL = "https://images.example.test/upload-retry.png"


@pytest.fixture
def upload(monkeypatch, tmp_path):
    monkeypatch.setattr(media_module, "_resolve_is_public", _public_ok)
    sink = _sink(tmp_path)
    bucket, calls, attempts = FakeBucket(), [], []
    bucket.unavailable = True
    remote = ObjectStorage(sink.engine, tmp_path / "media", "media", oss_config(), bucket_factory=lambda _: bucket)
    persist = remote.persist

    def observed_persist(*args):
        attempts.append(args)
        return persist(*args)

    monkeypatch.setattr(remote, "persist", observed_persist)

    def make_store():
        return MediaStore(sink.engine, remote.root, object_storage=remote, transport=_counting_transport(calls))

    return SimpleNamespace(sink=sink, bucket=bucket, calls=calls, attempts=attempts,
                           remote=remote, make_store=make_store, store=make_store())


def row(upload):
    with Session(upload.sink.engine) as session:
        return session.get(MediaAssetRecord, url_hash_of(URL))


def expire(upload):
    with Session(upload.sink.engine) as session:
        record = session.get(MediaAssetRecord, url_hash_of(URL))
        record.updated_at = (dt.datetime.now() - dt.timedelta(seconds=31)).isoformat()
        session.add(record)
        session.commit()


def test_pending_upload_survives_restart_and_short_cooldown_without_refetch(upload, monkeypatch):
    async def scenario():
        assert await upload.store.get_or_fetch(URL) is None
        record = row(upload)
        assert record.status == "pending_upload" and record.fail_count == 0
        assert record.content_hash == hashlib.sha256(PNG_BYTES).hexdigest()
        assert upload.store.file_path_for(record).read_bytes() == PNG_BYTES
        assert record.last_error == "object_storage_head_failed"
        await upload.store.aclose()
        restarted = upload.make_store()
        upload.bucket.unavailable = False
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "to_thread", lambda *_args, **_kwargs: pytest.fail("cooldown must not occupy thread pool"))
            assert await restarted.get_or_fetch(URL) is None
            assert await restarted.get_or_fetch(URL, force=True) is None
        assert len(upload.attempts) == 1 and upload.calls == [URL]
        expire(upload)
        restored = await restarted.get_or_fetch(URL)
        assert restored.status == "cached" and restored.fail_count == 0 and restored.last_error is None
        assert len(upload.attempts) == 2 and upload.bucket.uploads == 1
        assert upload.calls == [URL]
        await restarted.aclose()

    asyncio.run(scenario())


def test_separate_workers_claim_one_due_upload_attempt(upload):
    async def scenario():
        assert await upload.store.get_or_fetch(URL) is None
        expire(upload)
        stale = row(upload)
        upload.bucket.unavailable = False
        second = upload.make_store()
        results = await asyncio.gather(upload.store._retry_pending_upload(stale), second._retry_pending_upload(stale))
        assert sum(record is not None for record in results) == 1
        assert len(upload.attempts) == 2 and upload.bucket.uploads == 1
        assert upload.calls == [URL]
        await upload.store.aclose()
        await second.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["same_size_corruption", "missing"])
def test_pending_bytes_are_fully_verified_before_retry_without_origin_fetch(upload, damage):
    async def scenario():
        assert await upload.store.get_or_fetch(URL) is None
        path = upload.store.file_path_for(row(upload))
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"x" * len(PNG_BYTES))
        expire(upload)
        upload.bucket.unavailable = False
        assert await upload.make_store().get_or_fetch(URL) is None
        assert len(upload.attempts) == 1 and upload.calls == [URL]
        record = row(upload)
        assert record.status == "pending_upload" and record.fail_count == 0
        assert record.last_error == ("object_storage_local_file_unavailable" if damage == "missing"
                                     else "object_storage_local_checksum_mismatch")
        await upload.store.aclose()

    asyncio.run(scenario())


def test_authority_takeover_during_upload_is_not_overwritten(upload, monkeypatch):
    persist = upload.remote.persist

    def takeover(*args):
        persist(*args)
        with Session(upload.sink.engine) as session:
            record = session.get(MediaAssetRecord, url_hash_of(URL))
            record.sync_authority_id = "new-owner"
            record.sync_authority_revision = "new-revision"
            record.status = "pending_sync"
            record.content_hash = "a" * 64
            session.add(record)
            session.commit()

    async def scenario():
        assert await upload.store.get_or_fetch(URL) is None
        expire(upload)
        upload.bucket.unavailable = False
        monkeypatch.setattr(upload.remote, "persist", takeover)
        assert await upload.store.get_or_fetch(URL) is None
        assert row(upload).content_hash == "a" * 64
        assert row(upload).status == "pending_sync" and row(upload).sync_authority_id == "new-owner"
        assert await upload.store.get_or_fetch(URL, force=True) is None
        assert upload.calls == [URL]
        await upload.store.aclose()

    asyncio.run(scenario())


def test_claim_returns_its_snapshot_when_another_worker_takes_over_after_commit(upload):
    asyncio.run(upload.store.get_or_fetch(URL))
    previous = row(upload)
    armed = True

    def after_commit(session):
        nonlocal armed
        if not armed:
            return
        armed = False
        with Session(upload.sink.engine) as other:
            record = other.get(MediaAssetRecord, previous.url_hash)
            record.sync_authority_id = "new-owner"
            record.status = "pending_sync"
            record.content_hash = "b" * 64
            other.add(record)
            other.commit()

    event.listen(Session, "after_commit", after_commit)
    try:
        claimed = upload.store._replace_record(previous, URL, previous.url_hash, updated_at=media_module._now())
    finally:
        event.remove(Session, "after_commit", after_commit)
    assert claimed.status == "pending_upload" and claimed.sync_authority_id == ""
    assert claimed.content_hash == hashlib.sha256(PNG_BYTES).hexdigest()
    assert row(upload).sync_authority_id == "new-owner" and row(upload).content_hash == "b" * 64
    assert upload.store._replace_record(claimed, URL, claimed.url_hash, status="cached") is None


def test_pending_upload_consumers_and_gc_protect_the_download(upload, monkeypatch):
    from api.routers import media as media_router
    asyncio.run(upload.store.get_or_fetch(URL))
    record = row(upload)
    with Session(upload.sink.engine) as session:
        session.add(ArticleRecord(id="pending-image", title="Pending", content_type="web_article", source_id="public",
                                 source_url="https://example.test/article", content=f"![image]({URL})",
                                 fetched_date="2026-09-16", publish_date="2026-09-16"))
        # An old, unrelated sync URL shares these bytes. Its cleanup must honor
        # the local pending upload as a reference, even though it is not cached.
        session.add(MediaAssetRecord(url_hash="c" * 64, url="https://old.example.test/orphan.png",
                    status="cached", content_hash=record.content_hash, mime=record.mime, ext=record.ext,
                    size_bytes=record.size_bytes, sync_authority_id="external", created_at="2020-01-01",
                    updated_at="2020-01-01"))
        session.commit()
    monkeypatch.setattr(media_router, "_app", lambda: SimpleNamespace(media_store=upload.store, db_sink=upload.sink))
    assert upload.store.url_status_map([URL])[URL] == {"status": "pending", "error": None}
    heatmap = asyncio.run(media_router.media_heatmap(year=2026))
    assert heatmap["days"][0]["pending"] == 1 and heatmap["days"][0]["failed"] == 0
    detail = asyncio.run(media_router.media_day_detail("2026-09-16"))
    assert detail["articles"][0]["pending"] == 1
    _, exported = archive_sync_v2.parse_page(archive_sync_v2.export_page(upload.sink.engine, "media"), expected_stream="media")
    assert exported == []
    cleanup = upload.store.gc_remote_unreferenced(now=dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc))
    assert cleanup["records_deleted"] == 1 and cleanup["files_deleted"] == 0
    assert upload.store.file_path_for(record).read_bytes() == PNG_BYTES
    assert upload.store.stats()["failed_count"] == upload.store.stats()["cached_count"] == 0
    assert inventory(upload.sink.engine)[0].references == 1
    upload.bucket.unavailable = False
    upload.remote.persist(upload.store.file_path_for(record), record.content_hash, record.ext, record.size_bytes, record.mime)
    reports = []
    execute(upload.sink.engine, {"media": upload.remote}, action="gc", emit=reports.append)
    assert reports[0]["references"] == 1 and reports[0]["result"] == "skipped"
    expire(upload)
    assert asyncio.run(upload.store.get_or_fetch(URL)).status == "cached"
    _, exported = archive_sync_v2.parse_page(archive_sync_v2.export_page(upload.sink.engine, "media"), expected_stream="media")
    assert len(exported) == 1


def test_pending_upload_cannot_override_sync_ownership_even_when_forced(upload):
    asyncio.run(upload.store.get_or_fetch(URL))
    with Session(upload.sink.engine) as session:
        record = session.get(MediaAssetRecord, url_hash_of(URL))
        record.sync_authority_id = "remote-owner"
        record.updated_at = "2020-01-01"
        session.add(record)
        session.commit()
    assert asyncio.run(upload.store.get_or_fetch(URL, force=True)) is None
    assert len(upload.attempts) == 1 and upload.calls == [URL]


def test_local_download_does_not_enter_upload_state_or_call_adapter(upload, monkeypatch):
    upload.remote.enabled = False
    upload.remote.config = OssConfig()
    monkeypatch.setattr(upload.remote, "persist", lambda *_args: pytest.fail("local download must not persist to OSS"))
    record = asyncio.run(upload.store.get_or_fetch(URL))
    assert record.status == "cached" and upload.calls == [URL]
