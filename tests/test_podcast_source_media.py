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
from services.podcast_source_media import SourceMediaTooLong, validate_source_media
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
