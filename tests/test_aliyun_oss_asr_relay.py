from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from config import AliyunIsiConfig, PodcastArtifactStorageConfig
from models.db import PodcastSourceMediaSnapshotRecord
from services.aliyun_oss_asr_relay import AliyunOssAsrRelay, OssRelayError
from services.podcast_source_media import EnclosureSnapshot, ValidatedSourceMediaFile
from services.podcast_worker_contracts import (
    ArtifactRef,
    ExecutionIdentity,
    ExecutionKind,
    StageContext,
    StagePlan,
)


def _config() -> AliyunIsiConfig:
    return AliyunIsiConfig(
        access_key_id="ak-id",
        access_key_secret="ak-secret",
        app_key="app-key",
        region_id="cn-shanghai",
        asr_oss_endpoint="https://oss-cn-shanghai.aliyuncs.com",
        asr_oss_internal_endpoint=(
            "https://oss-cn-shanghai-internal.aliyuncs.com"
        ),
        asr_oss_bucket="dorami-asr-relay-test",
        asr_oss_prefix="asr-relay",
        asr_oss_signed_url_ttl_seconds=86_400,
    )


def _context() -> StageContext:
    digest = "a" * 64
    return StageContext(
        processing_id="processing-1",
        episode_id="episode-1",
        target="transcript",
        stage="asr",
        attempt_id="attempt-2",
        attempt_no=2,
        fencing_token=1,
        input_artifact=ArtifactRef(
            artifact_id="snapshot-1",
            episode_id="episode-1",
            kind="source_media_snapshot",
            content_hash=digest,
            size_bytes=3,
            mime_type="audio/mpeg",
        ),
        identity=ExecutionIdentity.from_settings(
            execution_kind=ExecutionKind.PROVIDER,
            provider="aliyun-isi",
            model="nls-filetrans",
            revision="2018-08-17",
            settings={},
        ),
        plan=StagePlan(
            estimated_cost_minor=0,
            poll_interval_seconds=1,
            deadline_seconds=3_600,
        ),
    )


def _snapshot() -> PodcastSourceMediaSnapshotRecord:
    return PodcastSourceMediaSnapshotRecord(
        id="snapshot-1",
        episode_id="episode-1",
        locator_hash="b" * 64,
        content_hash="a" * 64,
        mime="audio/mpeg",
        size_bytes=3,
        duration_seconds=1.0,
        created_at="2026-09-11T00:00:00+00:00",
    )


class _Bucket:
    def __init__(self, *, sign_error: bool = False) -> None:
        self.sign_error = sign_error
        self.uploads: list[tuple[str, str, dict[str, str]]] = []
        self.deletes: list[str] = []

    def put_object_from_file(self, key, filename, headers=None):
        self.uploads.append((key, filename, dict(headers or {})))
        return SimpleNamespace(status=200)

    def sign_url(self, method, key, expires, *, slash_safe=False):
        if self.sign_error:
            raise RuntimeError("signed URL must never leak")
        assert method == "GET"
        assert expires == 86_400
        assert slash_safe is True
        return (
            "https://dorami-asr-relay-test."
            "oss-cn-shanghai-internal.aliyuncs.com/"
            f"{key}?signature=redacted"
        )

    def delete_object(self, key):
        self.deletes.append(key)


def _patch_download(monkeypatch, path):
    @contextmanager
    def download(*_args, **_kwargs):
        yield ValidatedSourceMediaFile(
            path=path,
            mime="audio/mpeg",
            size_bytes=3,
            content_hash="a" * 64,
        )

    monkeypatch.setattr(
        "services.aliyun_oss_asr_relay.download_snapshot_media", download
    )


def test_prepare_uploads_exact_bytes_and_returns_internal_signed_url(
    monkeypatch, tmp_path
):
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"mp3")
    _patch_download(monkeypatch, media)
    public = _Bucket()
    internal = _Bucket()

    relay = AliyunOssAsrRelay(
        _config(),
        artifact_store=SimpleNamespace(),
        storage_config=PodcastArtifactStorageConfig(root_dir=str(tmp_path)),
        bucket_factory=lambda endpoint: (
            internal if "-internal." in endpoint else public
        ),
    )
    context = _context()
    url = relay.prepare(
        context,
        enclosure=EnclosureSnapshot(
            "https://publisher.example/episode.mp3", "audio/mpeg", 3
        ),
        expected=_snapshot(),
    )

    key = f"asr-relay/attempt-2/{'a' * 64}.mp3"
    assert public.uploads == [(key, str(media), {"Content-Type": "audio/mpeg"})]
    assert url.startswith(
        "https://dorami-asr-relay-test.oss-cn-shanghai-internal.aliyuncs.com/"
    )
    relay.delete(context)
    assert public.deletes == [key]


def test_prepare_deletes_uploaded_object_when_signing_fails(monkeypatch, tmp_path):
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"mp3")
    _patch_download(monkeypatch, media)
    public = _Bucket()
    internal = _Bucket(sign_error=True)
    relay = AliyunOssAsrRelay(
        _config(),
        artifact_store=SimpleNamespace(),
        storage_config=PodcastArtifactStorageConfig(root_dir=str(tmp_path)),
        bucket_factory=lambda endpoint: (
            internal if "-internal." in endpoint else public
        ),
    )

    with pytest.raises(OssRelayError) as error:
        relay.prepare(
            _context(),
            enclosure=EnclosureSnapshot(
                "https://publisher.example/episode.mp3", "audio/mpeg", 3
            ),
            expected=_snapshot(),
        )

    assert error.value.code == "aliyun_oss_fallback_prepare_failed"
    assert public.deletes == [f"asr-relay/attempt-2/{'a' * 64}.mp3"]
