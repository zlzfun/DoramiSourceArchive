"""Receiver-side reuse, repair, retry and progress across both binary streams."""
import asyncio
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session

from models.db import MediaAssetRecord, PodcastArtifactRecord
from services import archive_sync_v2 as sync, remote_sync
from services.podcast_artifacts import PodcastArtifactStore
from tests.test_archive_sync_v2 import _sink, _source, _article, _V2Remote
from tests.test_podcast_text_sync import (
    _source as _podcast_source, _episode, _artifact, _publication,
    _local_digest_audio, _wav,
)


@pytest.fixture(params=["media", "podcast_audio"])
def env(request, tmp_path, monkeypatch):
    stream = request.param
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    root = tmp_path / stream
    bodies = {}
    with Session(producer.engine) as session:
        if stream == "media":
            session.add(_source())
            urls = [f"https://img.test/{i}.jpg" for i in range(2)]
            session.add(_article(content="\n".join(f"![image]({u})" for u in urls)))
            for i, url in enumerate(urls):
                body = b"\xff\xd8\xffimage" + bytes([i])
                key = hashlib.sha256(url.encode()).hexdigest()
                bodies[key] = body
                session.add(MediaAssetRecord(
                    url_hash=key, url=url, status="cached",
                    content_hash=hashlib.sha256(body).hexdigest(),
                    mime="image/jpeg", ext=".jpg", size_bytes=len(body),
                    created_at="2026-09-01", updated_at="2026-09-01",
                ))
        else:
            session.add(_podcast_source())
            session.commit()
            for i in range(2):
                episode = _episode(f"episode-{i}")
                script = _artifact(f"script-{i}", episode_id=episode.id,
                                   kind="narration_script_zh")
                session.add(episode)
                session.commit()
                session.add(script)
                session.add(_publication(script.id, episode_id=episode.id,
                                         kind="narration_script_zh"))
                session.commit()
                record = _local_digest_audio(episode_id=episode.id, script=script)
                body = _wav()[:-1] + bytes([i])
                bodies[record.id] = body
                record.content_hash = hashlib.sha256(body).hexdigest()
                record.size_bytes = len(body)
                record.mime, record.ext = "audio/wav", ".wav"
                session.add(record)
        session.commit()
    e = SimpleNamespace(
        stream=stream, producer=producer, consumer=consumer, root=root,
        bodies=bodies, downloads=[], progress=[], checkpoints={}, response=None,
        store=PodcastArtifactStore(consumer.engine, root, max_bytes=1024 * 1024,
            total_quota_bytes=0, minimum_free_bytes=0, staging_ttl_seconds=0,
            allowed_mime_types=("audio/wav",)),
        object_storage=None,
    )
    e.ext = ".jpg" if stream == "media" else ".wav"
    e.path = lambda body: root / hashlib.sha256(body).hexdigest()[:2] / (
        hashlib.sha256(body).hexdigest() + e.ext)
    remote = _V2Remote(producer)
    prefix = "/api/archive/v2/" + ("media/" if stream == "media" else "podcast-audio/")
    def handler(req):
        if req.url.path.startswith(prefix):
            key = req.url.path.rsplit("/", 1)[-1]
            e.downloads.append(key)
            if e.response:
                return e.response(key)
            return httpx.Response(200, content=bodies[key])
        return remote.handler(req)
    monkeypatch.setattr(remote_sync, "_MAX_RETRIES", 1)
    def pull(**kwargs):
        return asyncio.run(remote_sync.run_pull_v2(
            engine=consumer.engine, base_url="https://remote.test", username="admin",
            password="test", media_root=root, podcast_artifact_store=e.store,
            media_object_storage=e.object_storage, transport=httpx.MockTransport(handler),
            push_candidate_evidence=False, checkpoints=e.checkpoints,
            on_stream_complete=lambda stream, cp: e.checkpoints.update({stream: cp}),
            **kwargs,
        ))
    e.pull = pull
    return e


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _replace_fixture_record(session, row, **changes):
    # Audio bindings are immutable: seed a different fixture record, never
    # weaken the production trigger merely to construct a test scenario.
    replacement = type(row).model_validate({**row.model_dump(), **changes})
    session.delete(row)
    session.flush()
    session.add(replacement)


def test_complete_local_files_skip_binary_get_and_finish_metadata(env):
    for body in env.bodies.values():
        _write(env.path(body), body)
    result = env.pull()
    assert env.downloads == []
    assert result["streams"][env.stream][f"{env.stream}_reused"] == 2
    with Session(env.consumer.engine) as session:
        for key in env.bodies:
            model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
            row = session.get(model, key)
            assert row.status == ("cached" if env.stream == "media" else "published")
            assert (row.sync_authority_id if env.stream == "media" else row.authority_id)


@pytest.mark.parametrize("env", ["media"], indirect=True)
@pytest.mark.parametrize("status", ["cached", "failed", "pending_sync"])
def test_historical_media_rows_are_adopted_and_reuse_verified_content(env, status):
    with Session(env.producer.engine) as source, Session(env.consumer.engine) as sink:
        for key, body in env.bodies.items():
            original = source.get(MediaAssetRecord, key)
            sink.add(MediaAssetRecord.model_validate({
                **original.model_dump(), "status": status,
                "sync_authority_id": "", "sync_authority_revision": "",
            }))
            _write(env.path(body), body)
        sink.commit()
    env.pull()
    assert not env.downloads
    with Session(env.consumer.engine) as session:
        for key in env.bodies:
            row = session.get(MediaAssetRecord, key)
            assert row.status == "cached" and row.fetched_at
            assert row.sync_authority_id == sync.producer_authority_id(env.producer.engine)
            assert row.sync_authority_revision


@pytest.mark.parametrize("env", ["podcast_audio"], indirect=True)
def test_audio_tombstones_count_as_records_without_binary_transfers(env):
    env.pull()
    env.downloads.clear()
    with Session(env.producer.engine) as session:
        for key in env.bodies:
            row = session.get(PodcastArtifactRecord, key)
            row.status = "withdrawn"
            row.updated_at = row.withdrawn_at = "2026-09-19T10:00:00+00:00"
            session.add(row)
        session.commit()
    result = env.pull(on_progress=env.progress.append)
    final = [p for p in env.progress if p["stream"] == "podcast_audio"][-1]
    assert final["stream_processed"] == 2
    assert final["reused"] == final["downloaded"] == 0
    assert final["reused_bytes"] == final["downloaded_bytes"] == 0
    assert not env.downloads
    assert result["streams"]["podcast_audio"]["deleted"] == 2


@pytest.mark.parametrize("damage", ["missing", "truncated", "same_size"])
def test_missing_or_corrupt_file_is_downloaded_and_repaired(env, damage):
    for body in env.bodies.values():
        if damage != "missing":
            _write(env.path(body), body[:3] if damage == "truncated" else b"X" * len(body))
    result = env.pull()
    assert len(env.downloads) == 2
    assert result["streams"][env.stream][f"{env.stream}_downloaded"] == 2
    for body in env.bodies.values():
        assert env.path(body).read_bytes() == body


def test_retry_reuses_finished_files_and_does_not_publish_partial_checkpoint(env):
    env.response = lambda key: httpx.Response(
        500 if len(env.downloads) == 2 else 200, content=env.bodies[key])
    with pytest.raises(remote_sync.RemoteSyncError):
        env.pull()
    assert env.stream not in env.checkpoints
    assert "source_states" not in env.checkpoints
    first = env.downloads[0]
    env.downloads.clear()
    env.response = None
    result = env.pull()
    assert first not in env.downloads
    assert len(env.downloads) == 1
    stats = result["streams"][env.stream]
    assert stats[f"{env.stream}_reused"] == stats[f"{env.stream}_downloaded"] == 1
    assert "source_states" in result["streams"]


def test_first_file_progress_is_visible_before_second_download(env):
    def response(key):
        if len(env.downloads) == 2:
            assert env.progress[-1]["stream"] == env.stream
            assert env.progress[-1]["stream_processed"] == 1
            assert env.progress[-1]["downloaded"] == 1
            assert env.stream not in env.checkpoints
        return httpx.Response(200, content=env.bodies[key])
    env.response = response
    advances = []
    result = env.pull(on_progress=lambda p: env.progress.append(p.copy()),
                      on_advance=advances.append)
    final = [p for p in env.progress if p["stream"] == env.stream][-1]
    assert final["stream_processed"] == final["downloaded"] == 2
    assert final["downloaded_bytes"] == sum(map(len, env.bodies.values()))
    assert sum(advances) == sum(s["count"] for s in result["streams"].values())
    assert env.progress[-1]["processed"] == sum(advances)


def test_shared_content_is_downloaded_once_across_distinct_records(env):
    shared = next(iter(env.bodies.values()))
    model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
    with Session(env.producer.engine) as session:
        for key in env.bodies:
            row = session.get(model, key)
            _replace_fixture_record(session, row,
                content_hash=hashlib.sha256(shared).hexdigest(), size_bytes=len(shared))
            env.bodies[key] = shared
        session.commit()
    result = env.pull()
    assert len(env.downloads) == 1
    stats = result["streams"][env.stream]
    assert stats[f"{env.stream}_downloaded"] == stats[f"{env.stream}_reused"] == 1
    assert stats["reused_bytes"] == stats["downloaded_bytes"] == len(shared)


@pytest.mark.parametrize("response", ["error", "truncated", "wrong_hash"])
def test_bad_response_cannot_publish_or_count_a_corrupt_local_file(env, response):
    env.pull()
    env.checkpoints.clear()
    env.downloads.clear()
    for body in env.bodies.values():
        _write(env.path(body), b"X" * len(body))
    env.response = lambda key: httpx.Response(500 if response == "error" else 200,
        content=env.bodies[key][:3] if response == "truncated" else b"X" * len(env.bodies[key]))
    with pytest.raises((remote_sync.RemoteSyncError, sync.SyncV2Error)):
        env.pull(on_progress=lambda p: env.progress.append(p.copy()))
    assert env.stream not in env.checkpoints
    assert "source_states" not in env.checkpoints
    current = env.progress[-1]
    assert current["stream"] == env.stream
    assert current["stream_processed"] == current["reused"] == current["downloaded"] == 0
    model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
    with Session(env.consumer.engine) as session:
        row = session.get(model, env.downloads[-1])
        assert row.status == ("pending_sync" if env.stream == "media" else "ready")


def test_matching_hash_still_requires_real_image_or_audio_format(env):
    model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
    with Session(env.producer.engine) as session:
        for key, body in env.bodies.items():
            bad = b"X" * len(body)
            row = session.get(model, key)
            _replace_fixture_record(session, row, content_hash=hashlib.sha256(bad).hexdigest())
            env.bodies[key] = bad
            _write(env.path(bad), bad)
        session.commit()
    with pytest.raises(sync.SyncV2Error):
        env.pull()
    assert env.stream not in env.checkpoints


def test_existing_files_do_not_override_another_authority(env):
    env.pull()
    env.checkpoints.clear()
    env.downloads.clear()
    model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
    with Session(env.consumer.engine) as session:
        for key in env.bodies:
            row = session.get(model, key)
            field = "sync_authority_id" if env.stream == "media" else "authority_id"
            _replace_fixture_record(session, row, **{field: "other"})
        session.commit()
    with pytest.raises(sync.SyncV2Error, match="authority"):
        env.pull()
    assert not env.downloads
    assert env.stream not in env.checkpoints


def test_oss_reuse_is_persisted_and_protected_by_cache_lease(env, monkeypatch):
    from contextlib import contextmanager
    from tests.test_object_storage import FakeBucket, remote_store
    bucket = FakeBucket()
    storage = remote_store(env.consumer.engine, env.root,
                           "media" if env.stream == "media" else "podcast", bucket)
    env.object_storage = env.store.object_storage = storage
    for body in env.bodies.values():
        _write(env.path(body), body)
    pin, persist = storage.pin, storage.persist
    held = []
    @contextmanager
    def tracked_pin(digest, **kwargs):
        with pin(digest, **kwargs) as result:
            held.append(digest)
            try:
                yield result
            finally:
                held.pop()
    def checked_persist(path, digest, *args):
        assert digest in held
        return persist(path, digest, *args)
    monkeypatch.setattr(storage, "pin", tracked_pin)
    monkeypatch.setattr(storage, "persist", checked_persist)
    env.pull()
    assert not env.downloads
    assert bucket.uploads == 2
    assert not held


def test_oss_failure_leaves_stream_unfinished_and_retry_reuses_file(env):
    from tests.test_object_storage import FakeBucket, remote_store
    bucket = FakeBucket()
    bucket.unavailable = True
    storage = remote_store(env.consumer.engine, env.root,
                           "media" if env.stream == "media" else "podcast", bucket)
    env.object_storage = env.store.object_storage = storage
    with pytest.raises(OSError):
        env.pull()
    first = env.downloads[-1]
    assert env.stream not in env.checkpoints
    bucket.unavailable = False
    env.downloads.clear()
    env.pull()
    assert first not in env.downloads
    assert bucket.uploads == 2


def test_concurrent_handoff_or_withdrawal_during_reuse_cannot_publish(env, monkeypatch):
    from config_oss import OssConfig
    from services.object_storage import ObjectStorage
    storage = ObjectStorage(env.consumer.engine, env.root,
                            "media" if env.stream == "media" else "podcast", OssConfig())
    env.object_storage = env.store.object_storage = storage
    for body in env.bodies.values():
        _write(env.path(body), body)
    model = MediaAssetRecord if env.stream == "media" else PodcastArtifactRecord
    def mutate(_path, digest, *_args):
        with Session(env.consumer.engine) as session:
            for key, body in env.bodies.items():
                if hashlib.sha256(body).hexdigest() != digest:
                    continue
                row = session.get(model, key)
                if env.stream == "media":
                    row.sync_authority_revision = "999999"
                else:
                    row.status = "withdrawn"
                session.add(row)
            session.commit()
    monkeypatch.setattr(storage, "persist", mutate)
    with pytest.raises(sync.SyncV2Error, match="manifest changed"):
        env.pull()
    assert not env.downloads
    assert env.stream not in env.checkpoints
