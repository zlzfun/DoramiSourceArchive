from __future__ import annotations

import hashlib
import asyncio
import json
import struct
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from config import PodcastArtifactStorageConfig, PodcastConfig
from models.db import (
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastSourceMediaSnapshotRecord,
)
from services.podcast_artifacts import PodcastArtifactStore
from services.podcast_source_media import (
    EnclosureSnapshot,
    SourceMediaConflict,
    SourceMediaTooLarge,
    SourceMediaTooLong,
    download_snapshot_media,
    validate_source_media,
)
from storage.impl.db_storage import DatabaseStorage


def _wav() -> bytes:
    pcm = b"\x00\x00" * 16
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8_000, 16_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def _probe(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "streams": [{"codec_type": "audio", "duration": "0.002"}],
                "format": {"duration": "0.002"},
            }
        ),
        stderr="",
    )


def test_validation_is_idempotent_and_never_persists_source_bytes(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'source-media.db'}")
    raw_url = "http://audio.publisher.test/episode.wav?token=do-not-persist"
    payload = _wav()
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="episode-source-media",
                title="Episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://publisher.test/episode",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes",
                extensions_json=json.dumps(
                    {
                        "audio_url": raw_url,
                        "audio_mime": "audio/wav",
                        "audio_bytes": len(payload),
                    }
                ),
            )
        )
        session.commit()

    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / "podcast-cas"),
        max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",),
        download_timeout_seconds=5,
        download_max_redirects=2,
        orphan_grace_seconds=0,
        staging_ttl_seconds=0,
    )
    store = PodcastArtifactStore(
        sink.engine,
        storage.root_dir,
        max_bytes=storage.max_audio_mb * 1024 * 1024,
        total_quota_bytes=storage.total_quota_bytes,
        minimum_free_bytes=storage.minimum_free_bytes,
        staging_ttl_seconds=storage.staging_ttl_seconds,
        allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=storage.orphan_grace_seconds,
        probe_runner=_probe,
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(payload),
        )

    def client_factory(*_args, **_kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )

    config = PodcastConfig(
        installation="external",
        authority_id="podcast-external-test",
        allowed_stages=("fetch", "asr"),
    )
    def validate():
        return asyncio.run(
            validate_source_media(
                sink.engine,
                store,
                episode_id="episode-source-media",
                podcast_config=config,
                storage_config=storage,
                max_audio_seconds_per_file=43_200,
                client_factory=client_factory,
            )
        )

    first = validate()
    second = validate()

    assert second == first
    assert requests == [raw_url, raw_url]
    assert first["locator_hash"] == hashlib.sha256(raw_url.encode()).hexdigest()
    assert first["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert list((Path(storage.root_dir) / ".incoming").iterdir()) == []
    assert list(Path(storage.root_dir).glob("*/*")) == []
    with Session(sink.engine) as session:
        snapshots = session.exec(select(PodcastSourceMediaSnapshotRecord)).all()
        assert len(snapshots) == 1
        assert session.exec(select(PodcastArtifactRecord)).all() == []
        assert raw_url not in repr(snapshots[0])


def test_validation_rejects_overlong_audio_after_probe_without_persisting_snapshot(
    tmp_path,
):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'source-media-too-long.db'}")
    raw_url = "https://audio.publisher.test/episode.wav"
    payload = _wav()
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="episode-too-long",
                title="Long episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://publisher.test/episode-too-long",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes",
                extensions_json=json.dumps({"audio_url": raw_url}),
            )
        )
        session.commit()

    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / "podcast-cas-too-long"),
        max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",),
    )

    def long_probe(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "streams": [{"codec_type": "audio", "duration": "2.0"}],
                    "format": {"duration": "2.0"},
                }
            ),
            stderr="",
        )

    store = PodcastArtifactStore(
        sink.engine,
        storage.root_dir,
        max_bytes=storage.max_audio_mb * 1024 * 1024,
        total_quota_bytes=storage.total_quota_bytes,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=0,
        probe_runner=long_probe,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(payload),
        )

    def client_factory(*_args, **_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(SourceMediaTooLong, match="时长上限"):
        asyncio.run(
            validate_source_media(
                sink.engine,
                store,
                episode_id="episode-too-long",
                podcast_config=PodcastConfig(
                    installation="external",
                    authority_id="podcast-external-test",
                    allowed_stages=("fetch", "asr"),
                ),
                storage_config=storage,
                max_audio_seconds_per_file=1,
                client_factory=client_factory,
            )
        )

    assert list((Path(storage.root_dir) / ".incoming").iterdir()) == []
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastSourceMediaSnapshotRecord)).all() == []


# ── issue #323:订阅源声明的大小只是预留参考,不是下载上限 ─────────────────────


def _media_env(tmp_path, name: str, *, declared: int | None, max_bytes: int = 1024 * 1024):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / (name + '.db')}")
    extensions = {"audio_url": f"https://audio.publisher.test/{name}.wav", "audio_mime": "audio/wav"}
    if declared is not None:
        extensions["audio_bytes"] = declared
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id=name, title="Episode", content_type="podcast_episode",
                source_id="podcast-source", source_url=f"https://publisher.test/{name}",
                publish_date="2026-09-10T00:00:00+00:00",
                fetched_date="2026-09-10T00:00:00+00:00",
                content="notes", extensions_json=json.dumps(extensions),
            )
        )
        session.commit()
    storage = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / f"cas-{name}"), max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024, minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",), download_timeout_seconds=5,
        download_max_redirects=2, orphan_grace_seconds=0, staging_ttl_seconds=0,
    )
    store = PodcastArtifactStore(
        sink.engine, storage.root_dir, max_bytes=max_bytes,
        total_quota_bytes=storage.total_quota_bytes, minimum_free_bytes=0,
        staging_ttl_seconds=0, allowed_mime_types=storage.allowed_mime_types,
        orphan_grace_seconds=0, probe_runner=_probe,
    )
    return sink, store, storage


def _serving(payload: bytes, *, content_length: bool):
    """一个只会回 ``payload`` 的发布方;记录每次请求以及当时产物库里的预留额。"""

    seen: list[int] = []

    def factory(store):
        def handler(_request: httpx.Request) -> httpx.Response:
            seen.append(sum(size for _path, size in store._download_reservations()))
            headers = {"Content-Type": "audio/wav"}
            if content_length:
                headers["Content-Length"] = str(len(payload))
            return httpx.Response(200, headers=headers, stream=httpx.ByteStream(payload))

        def client_factory(*_args, **_kwargs):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=False
            )

        return client_factory

    return seen, factory


def _validate(sink, store, storage, name, client_factory, *, max_seconds=43_200):
    config = PodcastConfig(
        installation="external", authority_id="podcast-external-test",
        allowed_stages=("fetch", "asr"),
    )
    return asyncio.run(
        validate_source_media(
            sink.engine, store, episode_id=name, podcast_config=config,
            storage_config=storage, max_audio_seconds_per_file=max_seconds,
            client_factory=client_factory,
        )
    )


def _nothing_left_behind(storage) -> bool:
    return list((Path(storage.root_dir) / ".incoming").iterdir()) == []


@pytest.mark.parametrize(
    ("content_length", "reservations"),
    [
        # 响应自报了长度:第一次在读响应头时就知道声明值不够,第二次按真实大小预留。
        (True, lambda declared, actual, cap: [declared, actual]),
        # 响应没有长度:流超过声明值才发现,第二次只能按配置上限预留。
        (False, lambda declared, actual, cap: [declared, cap]),
    ],
)
def test_a_file_larger_than_the_feed_declared_is_still_validated(
    tmp_path, content_length, reservations
):
    payload = _wav()
    declared = len(payload) - 20  # 订阅源写小了
    sink, store, storage = _media_env(tmp_path, "ep-understated", declared=declared)
    seen, factory = _serving(payload, content_length=content_length)

    snapshot = _validate(sink, store, storage, "ep-understated", factory(store))

    assert snapshot["size_bytes"] == len(payload)
    assert snapshot["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert seen == reservations(declared, len(payload), store.max_bytes)
    assert _nothing_left_behind(storage)


def test_an_honest_feed_is_downloaded_once_with_the_declared_reservation(tmp_path):
    payload = _wav()
    sink, store, storage = _media_env(tmp_path, "ep-honest", declared=len(payload))
    seen, factory = _serving(payload, content_length=True)

    _validate(sink, store, storage, "ep-honest", factory(store))

    assert seen == [len(payload)]


@pytest.mark.parametrize(("content_length", "requests"), [(True, 1), (False, 2)])
def test_only_the_configured_cap_makes_a_file_too_large(tmp_path, content_length, requests):
    payload = _wav()
    cap = len(payload) - 1  # 真实文件确实超过配置上限
    sink, store, storage = _media_env(
        tmp_path, "ep-over-cap", declared=len(payload) - 30, max_bytes=cap
    )
    seen, factory = _serving(payload, content_length=content_length)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-over-cap", factory(store))

    # 响应自报的长度已经超过上限时,不必再下载一遍来确认。
    assert len(seen) == requests
    assert _nothing_left_behind(storage)
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastSourceMediaSnapshotRecord)).all() == []


def test_a_declared_size_over_the_cap_is_rejected_without_a_request(tmp_path):
    payload = _wav()
    sink, store, storage = _media_env(
        tmp_path, "ep-declared-huge", declared=10_000, max_bytes=len(payload) + 10
    )
    seen, factory = _serving(payload, content_length=True)

    with pytest.raises(SourceMediaTooLarge):
        _validate(sink, store, storage, "ep-declared-huge", factory(store))

    assert seen == []


def test_snapshot_redownload_reserves_the_verified_size_not_the_declared_one(tmp_path):
    """OSS 中转前的重新下载:手里有校验过的快照,订阅源写小了也不该再被拦。"""

    payload = _wav()
    declared = len(payload) - 20
    sink, store, storage = _media_env(tmp_path, "ep-relay", declared=declared)
    _seen, factory = _serving(payload, content_length=False)
    _validate(sink, store, storage, "ep-relay", factory(store))
    with Session(sink.engine) as session:
        expected = session.exec(select(PodcastSourceMediaSnapshotRecord)).one()
    enclosure = EnclosureSnapshot(
        url="https://audio.publisher.test/ep-relay.wav", mime="audio/wav",
        declared_bytes=declared,
    )

    seen, factory = _serving(payload, content_length=False)
    with download_snapshot_media(
        store, enclosure=enclosure, expected=expected, storage_config=storage,
        client_factory=factory(store),
    ) as media:
        assert media.size_bytes == len(payload)
    assert seen == [len(payload)]

    # 同一地址换成了更大的文件:这是内容变了,不是「超过配置上限」。
    grown = payload + b"\x00" * 8
    _seen, factory = _serving(grown, content_length=False)
    with pytest.raises(SourceMediaConflict):
        with download_snapshot_media(
            store, enclosure=enclosure, expected=expected, storage_config=storage,
            client_factory=factory(store),
        ):
            pass
    assert _nothing_left_behind(storage)
