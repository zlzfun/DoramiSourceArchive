"""Podcast audio artifact storage and API end-to-end tests."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import struct
import subprocess
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from config import RuntimeConfig, load_config
from models.db import (
    AppSettingRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
)
from services.podcast_artifacts import (
    PodcastArtifactConflict,
    PodcastArtifactError,
    PodcastArtifactRecoveryConflict,
    PodcastArtifactStore,
)
from services.podcast_processing import enqueue_processing
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


def _wav_bytes(*, samples: int = 16) -> bytes:
    pcm = b"\x00\x00" * samples
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8_000, 16_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


WAV = _wav_bytes()


def _probe_result(*_args, **_kwargs):
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


def _article(article_id: str, content_type: str = "podcast_episode") -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type=content_type,
        source_id="podcast_test",
        source_url=f"https://podcasts.example/{article_id}",
        publish_date="2026-09-05T00:00:00+00:00",
        fetched_date="2026-09-05T00:00:00+00:00",
        content="show notes",
    )


def _narration_script(
    episode_id: str,
) -> tuple[PodcastTextArtifactRecord, PodcastTextPublicationRecord]:
    text = f"{episode_id} 的中文口播稿。"
    artifact_id = f"script-{episode_id}"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    artifact = PodcastTextArtifactRecord(
        id=artifact_id,
        episode_id=episode_id,
        kind="narration_script_zh",
        version=1,
        content_hash=content_hash,
        inline_text=text,
        language="zh-CN",
        authority_id="",
        provenance_json='{"pipeline":"test"}',
        created_at="2026-09-05T00:00:00+00:00",
    )
    publication = PodcastTextPublicationRecord(
        identity=f"{episode_id}:narration_script_zh",
        episode_id=episode_id,
        kind="narration_script_zh",
        artifact_id=artifact_id,
        status="published",
        authority_id="",
        published_at="2026-09-05T00:00:00+00:00",
        updated_at="2026-09-05T00:00:00+00:00",
    )
    return artifact, publication


def _setup_app(
    monkeypatch,
    tmp_path,
    *,
    max_bytes: int = 1024,
    total_quota_bytes: int = 10 * 1024 * 1024,
    minimum_free_bytes: int = 0,
    staging_ttl_seconds: int = 3600,
    disk_usage_provider=None,
):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'podcast-artifacts.db'}")
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        stamp = "2026-09-05T00:00:00+00:00"
        for source_id in ("podcast_test", "podcast_hidden", "user_rss_private"):
            session.add(
                SourceConfigRecord(
                    source_id=source_id,
                    name=source_id,
                    source_type="podcast",
                    url=f"https://podcasts.example/{source_id}.xml",
                    category="podcast",
                    fetcher_id="generic_podcast_rss",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        session.flush()
        session.add(_article("episode-1"))
        session.add(_article("episode-2"))
        session.add(_article("ordinary-1", "web_article"))
        session.commit()
        for episode_id in ("episode-1", "episode-2"):
            artifact, publication = _narration_script(episode_id)
            session.add(artifact)
            session.add(publication)
        session.commit()
    store = PodcastArtifactStore(
        sink.engine,
        tmp_path / "podcast-artifacts",
        max_bytes=max_bytes,
        total_quota_bytes=total_quota_bytes,
        minimum_free_bytes=minimum_free_bytes,
        staging_ttl_seconds=staging_ttl_seconds,
        allowed_mime_types=(
            "audio/wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
            "audio/webm",
        ),
        orphan_grace_seconds=0,
        probe_runner=_probe_result,
        disk_usage_provider=disk_usage_provider,
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "podcast_artifact_store", store)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    return app_module, sink, store


def _login(client: TestClient, username: str, password: str) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "password": password}
        ).status_code
        == 200
    )


def _import(
    client: TestClient,
    episode_id: str = "episode-1",
    kind: str = "digest_audio_zh",
    *,
    body: bytes = WAV,
    mime: str = "audio/wav",
    processing_id: str | None = None,
):
    params = {"provenance": "test_fixture"}
    if kind == "digest_audio_zh":
        text = f"{episode_id} 的中文口播稿。"
        params.update(
            {
                "narration_artifact_id": f"script-{episode_id}",
                "narration_content_hash": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
            }
        )
    if processing_id is not None:
        params["processing_id"] = processing_id
    return client.post(
        f"/api/admin/podcast-artifacts/import/{episode_id}/{kind}",
        params=params,
        content=body,
        headers={"Content-Type": mime},
    )


def _publish(client: TestClient, artifact: dict):
    return client.post(
        f"/api/admin/podcast-artifacts/{artifact['id']}/publish",
        params={
            "expected_updated_at": artifact["updated_at"],
        },
    )


def _enqueue_digest_audio(sink: DatabaseStorage, *, idempotency_key: str):
    narration_hash = hashlib.sha256(
        "episode-1 的中文口播稿。".encode("utf-8")
    ).hexdigest()
    with Session(sink.engine) as session:
        return enqueue_processing(
            session,
            episode_id="episode-1",
            stage="tts",
            input_fingerprint="1" * 64,
            pipeline_version="audio-v1",
            policy_version="rights-v1",
            requested_target="digest_audio",
            idempotency_key=idempotency_key,
            input_artifact_id="script-episode-1",
            input_artifact_kind="narration_script_zh",
            input_content_hash=narration_hash,
            input_language="zh-CN",
            budget_scope="podcast-paid-processing",
            budget_period="2026-09",
            budget_limit_minor=100,
            per_run_budget_minor=100,
            narration_artifact_id="script-episode-1",
            narration_content_hash=narration_hash,
            voice_profile_id="test-voice",
            policy=SimpleNamespace(require_stage=lambda *_args, **_kwargs: None),
        )


def test_config_supports_ini_and_environment_overrides(monkeypatch, tmp_path):
    ini = tmp_path / "backend.ini"
    ini.write_text(
        "[podcast]\nfeed_max_bytes = 12345\nfeed_timeout_seconds = 17\n"
        "[podcast_artifacts]\nroot_dir = relative-audio\nmax_audio_mb = 12\n"
        "total_quota_mb = 321\nminimum_free_mb = 64\n"
        "allowed_mime_types = audio/wav,audio/mpeg\n"
        "upload_timeout_seconds = 31\ndownload_timeout_seconds = 41\n"
        "ffprobe_binary = /opt/media/ffprobe\nprobe_timeout_seconds = 9\n"
        "orphan_grace_seconds = 77\nstaging_ttl_seconds = 78\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_PODCAST_FEED_MAX_BYTES", "54321")
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_MAX_AUDIO_MB", "22")
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_ROOT_DIR", str(tmp_path / "override"))
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_BYTES", "123456")
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_MB", "7")

    configured = load_config()

    assert configured.podcast.feed_max_bytes == 54321
    assert configured.podcast.feed_timeout_seconds == 17
    assert configured.podcast_artifacts.root_dir == str(tmp_path / "override")
    assert configured.podcast_artifacts.max_audio_mb == 22
    assert configured.podcast_artifacts.total_quota_bytes == 123456
    assert configured.podcast_artifacts.minimum_free_bytes == 7 * 1024 * 1024
    assert configured.podcast_artifacts.allowed_mime_types == (
        "audio/wav",
        "audio/mpeg",
    )
    assert configured.podcast_artifacts.upload_timeout_seconds == 31
    assert configured.podcast_artifacts.download_timeout_seconds == 41
    assert configured.podcast_artifacts.ffprobe_binary == "/opt/media/ffprobe"
    assert configured.podcast_artifacts.probe_timeout_seconds == 9
    assert configured.podcast_artifacts.orphan_grace_seconds == 77
    assert configured.podcast_artifacts.staging_ttl_seconds == 78


def test_magic_detection_accepts_supported_audio_and_rejects_spoof(tmp_path):
    store = PodcastArtifactStore(
        DatabaseStorage(f"sqlite:///{tmp_path / 'magic.db'}").engine,
        tmp_path / "cas",
        max_bytes=1024,
        total_quota_bytes=10 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=3600,
        allowed_mime_types=(
            "audio/wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
            "audio/webm",
        ),
        probe_runner=_probe_result,
    )
    fixtures = {
        "audio/wav": WAV,
        "audio/mpeg": b"ID3\x04\x00\x00\x00\x00\x00\x00payload",
        "audio/mp4": b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A ",
        "audio/ogg": b"OggS\x00payload",
        "audio/webm": b"\x1aE\xdf\xa3payloadwebm",
    }
    for mime, payload in fixtures.items():
        assert store.validate_audio(payload, mime) == mime
    with pytest.raises(PodcastArtifactError, match="音频格式"):
        store.validate_audio(b"not really wave", "audio/wav")
    with pytest.raises(PodcastArtifactError, match="Content-Type"):
        store.validate_audio(WAV, "text/plain")


def test_quota_counts_unique_blobs_and_allows_deduplicated_import(
    monkeypatch, tmp_path
):
    app_module, _, store = _setup_app(
        monkeypatch,
        tmp_path,
        total_quota_bytes=len(WAV),
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        first = _import(client, "episode-1")
        assert first.status_code == 201, first.text

        # A second registry row reuses the same verified CAS blob and consumes
        # no additional quota.
        duplicate = _import(client, "episode-2")
        assert duplicate.status_code == 201, duplicate.text
        assert duplicate.json()["content_hash"] == first.json()["content_hash"]

        unique = _import(client, "episode-2", body=_wav_bytes(samples=17))
        assert unique.status_code == 507
        assert "配额" in unique.json()["detail"]

        stats = client.get("/api/admin/podcast-artifacts/stats").json()
        assert stats["artifacts"] == 2
        assert stats["logical_bytes"] == len(WAV) * 2
        assert stats["disk_bytes"] == len(WAV)
        assert stats["quota_bytes"] == len(WAV)
        assert stats["quota_remaining_bytes"] == 0
        assert stats["quota_pressure"] is True
        assert len(store._blob_files()) == 1


def test_minimum_free_space_is_injected_and_dedup_does_not_consume_again(
    monkeypatch, tmp_path
):
    available = {"free": 10_000}

    def disk_usage(_path):
        return SimpleNamespace(total=20_000, used=10_000, free=available["free"])

    app_module, _, _ = _setup_app(
        monkeypatch,
        tmp_path,
        total_quota_bytes=10_000,
        minimum_free_bytes=100,
        disk_usage_provider=disk_usage,
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        assert _import(client, "episode-1").status_code == 201

        available["free"] = 99
        denied = _import(client, "episode-2", body=_wav_bytes(samples=17))
        assert denied.status_code == 507
        assert "磁盘可用空间" in denied.json()["detail"]

        # The staged file is already included in disk_usage().free. Finalizing
        # it with an atomic same-filesystem rename is allowed exactly at the
        # configured reserve boundary.
        available["free"] = 100
        boundary = _import(client, "episode-2", body=_wav_bytes(samples=17))
        assert boundary.status_code == 201, boundary.text
        assert (
            client.get("/api/admin/podcast-artifacts/stats").json()["disk_pressure"]
            is False
        )

        # Even below the reserve, a byte-identical import is metadata-only.
        available["free"] = 0
        duplicate = _import(client, "episode-2")
        assert duplicate.status_code == 201, duplicate.text
        stats = client.get("/api/admin/podcast-artifacts/stats").json()
        assert stats["disk_capacity_bytes"] == 20_000
        assert stats["disk_free_bytes"] == 0
        assert stats["minimum_free_bytes"] == 100
        assert stats["disk_pressure"] is True
        assert stats["storage_pressure"] is True


def test_startup_reconcile_cleans_only_stale_staging_and_reports_it(
    monkeypatch, tmp_path
):
    app_module, _, store = _setup_app(
        monkeypatch,
        tmp_path,
        staging_ttl_seconds=10,
    )
    stale_fd, stale_path = store.create_upload_temp()
    fresh_fd, fresh_path = store.create_upload_temp()
    with os.fdopen(stale_fd, "wb") as handle:
        handle.write(b"stale")
    with os.fdopen(fresh_fd, "wb") as handle:
        handle.write(b"fresh")
    old = time.time() - 20
    os.utime(stale_path, (old, old))

    before = store.stats()
    assert before["staging_files"] == 2
    assert before["staging_bytes"] == 10
    assert before["stale_staging_files"] == 1
    assert before["stale_staging_bytes"] == 5

    with TestClient(app_module.app) as client:
        assert not stale_path.exists()
        assert fresh_path.exists()
        _login(client, "admin", "admin")
        stats = client.get("/api/admin/podcast-artifacts/stats").json()
        assert stats["staging_files"] == 1
        assert stats["stale_staging_files"] == 0

        os.utime(fresh_path, (old, old))
        cleaned = client.post("/api/admin/podcast-artifacts/reconcile").json()
        assert cleaned == {
            "expired_source_records": 0,
            "expired_protected": 0,
            "deleted_orphan_blobs": 0,
            "deleted_bytes": 0,
            "deleted_staging_files": 1,
            "deleted_staging_bytes": 5,
        }
        assert not fresh_path.exists()


def test_reconcile_never_unlinks_a_live_locked_staging_file(monkeypatch, tmp_path):
    _app_module, sink, store = _setup_app(monkeypatch, tmp_path, staging_ttl_seconds=0)
    fd, path = store.create_upload_temp()
    os.write(fd, b"in-flight")
    try:
        first = store.reconcile_storage()
        assert first["deleted_staging_files"] == 0
        assert path.exists()
    finally:
        os.close(fd)
    second = store.reconcile_storage()
    assert second["deleted_staging_files"] == 1
    assert not path.exists()
    sink.engine.dispose()


def test_download_reservation_setup_failure_removes_marker(monkeypatch, tmp_path):
    from services import podcast_artifacts as artifact_service

    _app_module, sink, store = _setup_app(monkeypatch, tmp_path)

    def fail_fsync(_fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(artifact_service.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated"):
        with store.reserve_source_download(1024):
            pass
    assert list((store.root / ".incoming").glob("download-*.reserve")) == []
    sink.engine.dispose()


def test_upload_wall_clock_timeout_is_python_310_compatible(monkeypatch, tmp_path):
    import api.app as app_module
    from api.routers import podcasts as podcasts_router

    class SlowRequest:
        headers: dict[str, str] = {}

        async def stream(self):
            await asyncio.sleep(0.05)
            yield WAV

    store = PodcastArtifactStore(
        DatabaseStorage(f"sqlite:///{tmp_path / 'timeout.db'}").engine,
        tmp_path / "cas",
        max_bytes=1024,
        total_quota_bytes=10 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=3600,
        allowed_mime_types=("audio/wav",),
        probe_runner=_probe_result,
    )
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            podcast_artifacts=replace(
                app_module.settings.podcast_artifacts,
                upload_timeout_seconds=0.001,
            ),
        ),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(podcasts_router._stream_bounded_audio(SlowRequest(), store))

    assert getattr(raised.value, "status_code", None) == 408
    assert list((tmp_path / "cas" / ".tmp").glob("*")) == []


def test_audio_artifact_api_end_to_end_and_restart(monkeypatch, tmp_path):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = _import(client)
        assert response.status_code == 201, response.text
        artifact = response.json()
        artifact_id = artifact["id"]
        assert artifact["episode_id"] == "episode-1"
        assert artifact["kind"] == "digest_audio_zh"
        assert artifact["status"] == "ready"
        assert artifact["authority_id"] == "dev-local"
        assert artifact["content_hash"] == hashlib.sha256(WAV).hexdigest()
        assert artifact["narration_artifact_id"] == "script-episode-1"
        assert (
            artifact["narration_content_hash"]
            == hashlib.sha256("episode-1 的中文口播稿。".encode("utf-8")).hexdigest()
        )
        assert artifact["size_bytes"] == len(WAV)

        with Session(sink.engine) as session:
            row = session.get(PodcastArtifactRecord, artifact_id)
            assert row is not None
            assert row.mime == "audio/wav"
            assert store.file_path_for(row).read_bytes() == WAV

        listed = client.get("/api/admin/podcast-artifacts").json()
        assert [item["id"] for item in listed["items"]] == [artifact_id]
        stats = client.get("/api/admin/podcast-artifacts/stats").json()
        assert stats["artifacts"] == 1
        assert stats["ready"] == 1
        assert stats["disk_bytes"] == len(WAV)

        assert (
            client.get(f"/api/reader/podcast-artifacts/{artifact_id}/audio").status_code
            == 404
        )
        published = _publish(client, artifact)
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"

        head = client.head(f"/api/reader/podcast-artifacts/{artifact_id}/audio")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(len(WAV))
        assert head.headers["accept-ranges"] == "bytes"
        assert head.headers["etag"] == f'"{hashlib.sha256(WAV).hexdigest()}"'
        assert head.headers["last-modified"]

        head_with_range = client.head(
            f"/api/reader/podcast-artifacts/{artifact_id}/audio",
            headers={"Range": "bytes=4-11"},
        )
        assert head_with_range.status_code == 200
        assert head_with_range.headers["content-length"] == str(len(WAV))

        partial = client.get(
            f"/api/reader/podcast-artifacts/{artifact_id}/audio",
            headers={"Range": "bytes=4-11"},
        )
        assert partial.status_code == 206
        assert partial.content == WAV[4:12]
        assert partial.headers["content-range"] == f"bytes 4-11/{len(WAV)}"
        assert partial.headers["content-length"] == "8"
        assert partial.headers["etag"] == head.headers["etag"]
        assert partial.headers["last-modified"] == head.headers["last-modified"]
        unsatisfied = client.get(
            f"/api/reader/podcast-artifacts/{artifact_id}/audio",
            headers={"Range": f"bytes={len(WAV)}-"},
        )
        assert unsatisfied.status_code == 416
        assert unsatisfied.headers["content-range"] == f"bytes */{len(WAV)}"
        assert unsatisfied.headers["etag"] == head.headers["etag"]
        assert unsatisfied.headers["last-modified"] == head.headers["last-modified"]

        # A newly constructed store can resolve the same DB row and CAS file.
        monkeypatch.setattr(
            app_module,
            "podcast_artifact_store",
            PodcastArtifactStore(
                sink.engine,
                store.root,
                max_bytes=1024,
                total_quota_bytes=10 * 1024 * 1024,
                minimum_free_bytes=0,
                staging_ttl_seconds=3600,
                allowed_mime_types=store.allowed_mime_types,
                orphan_grace_seconds=0,
                probe_runner=_probe_result,
            ),
        )
        assert (
            client.get(f"/api/reader/podcast-artifacts/{artifact_id}/audio").content
            == WAV
        )

        withdrawn = client.post(f"/api/admin/podcast-artifacts/{artifact_id}/withdraw")
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "withdrawn"
        assert (
            client.get(f"/api/reader/podcast-artifacts/{artifact_id}/audio").status_code
            == 404
        )
        deleted = client.delete(f"/api/admin/podcast-artifacts/{artifact_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "blob_deleted": False}
        assert any(store.root.rglob("*.wav"))
        reconciled = client.post("/api/admin/podcast-artifacts/reconcile")
        assert reconciled.json() == {
            "expired_source_records": 0,
            "expired_protected": 0,
            "deleted_orphan_blobs": 1,
            "deleted_bytes": len(WAV),
            "deleted_staging_files": 0,
            "deleted_staging_bytes": 0,
        }
        assert not any(store.root.rglob("*.wav"))


def test_ready_audio_is_admin_only_and_shared_blob_delete_is_reference_safe(
    monkeypatch, tmp_path
):
    app_module, _, store = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        first = _import(client, "episode-1", "source_audio").json()
        second = _import(client, "episode-2", "digest_audio_zh").json()
        path = store.file_path_for_hash(first["content_hash"], first["mime"])
        assert path.is_file()
        assert (
            client.get(f"/api/reader/podcast-artifacts/{first['id']}/audio").status_code
            == 404
        )
        assert (
            client.get(f"/api/admin/podcast-artifacts/{first['id']}/audio").content
            == WAV
        )
        assert _publish(client, first).status_code == 409

        # The registry itself refuses the legacy published source-audio state.
        with Session(store.engine) as session:
            row = session.get(PodcastArtifactRecord, first["id"])
            row.status = "published"
            session.add(row)
            with pytest.raises(IntegrityError, match="source_never_published"):
                session.commit()
            session.rollback()

        for artifact_id in (first["id"], second["id"]):
            assert (
                client.post(
                    f"/api/admin/podcast-artifacts/{artifact_id}/withdraw"
                ).status_code
                == 200
            )
        result = client.delete(f"/api/admin/podcast-artifacts/{first['id']}").json()
        assert result == {"deleted": True, "blob_deleted": False}
        assert path.is_file()
        result = client.delete(f"/api/admin/podcast-artifacts/{second['id']}").json()
        assert result == {"deleted": True, "blob_deleted": False}
        assert path.exists()
        assert (
            client.post("/api/admin/podcast-artifacts/reconcile").json()[
                "deleted_orphan_blobs"
            ]
            == 1
        )
        assert not path.exists()


def test_digest_audio_import_and_publish_fail_closed_on_script_mismatch(
    monkeypatch, tmp_path
):
    app_module, sink, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        wrong = client.post(
            "/api/admin/podcast-artifacts/import/episode-1/digest_audio_zh",
            params={
                "narration_artifact_id": "script-episode-1",
                "narration_content_hash": "0" * 64,
            },
            content=WAV,
            headers={"Content-Type": "audio/wav"},
        )
        assert wrong.status_code == 409

        artifact = _import(client).json()
        with Session(sink.engine) as session:
            text = "替换后的口播稿。"
            replacement = PodcastTextArtifactRecord(
                id="script-episode-1-v2",
                episode_id="episode-1",
                kind="narration_script_zh",
                version=2,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                inline_text=text,
                language="zh-CN",
                authority_id="",
                provenance_json='{"pipeline":"test"}',
                created_at="2026-09-05T01:00:00+00:00",
            )
            session.add(replacement)
            publication = session.get(
                PodcastTextPublicationRecord,
                "episode-1:narration_script_zh",
            )
            publication.artifact_id = replacement.id
            publication.updated_at = "2026-09-05T01:00:00+00:00"
            session.add(publication)
            session.commit()
            refreshed = session.get(PodcastArtifactRecord, artifact["id"])
            assert refreshed.status == "withdrawn"

        publish = _publish(client, artifact)
        assert publish.status_code == 409
        assert (
            client.get(
                f"/api/admin/podcast-artifacts/{artifact['id']}/audio"
            ).status_code
            == 404
        )


def test_digest_audio_processing_binding_is_validated_immutable_and_unique(
    monkeypatch, tmp_path
):
    app_module, sink, _ = _setup_app(monkeypatch, tmp_path)
    internal_policy = SimpleNamespace(require_stage=lambda *_args, **_kwargs: None)
    with Session(sink.engine) as session:
        first_run = enqueue_processing(
            session,
            episode_id="episode-1",
            stage="tts",
            input_fingerprint="1" * 64,
            pipeline_version="audio-v1",
            policy_version="rights-v1",
            requested_target="digest_audio",
            idempotency_key="audio-run-1",
            input_artifact_id="script-episode-1",
            input_artifact_kind="narration_script_zh",
            input_content_hash=hashlib.sha256(
                "episode-1 的中文口播稿。".encode("utf-8")
            ).hexdigest(),
            input_language="zh-CN",
            budget_scope="podcast-paid-processing",
            budget_period="2026-09",
            budget_limit_minor=100,
            per_run_budget_minor=100,
            narration_artifact_id="script-episode-1",
            narration_content_hash=hashlib.sha256(
                "episode-1 的中文口播稿。".encode("utf-8")
            ).hexdigest(),
            voice_profile_id="test-voice",
            policy=internal_policy,
        )
    with Session(sink.engine) as session:
        alternative_text = "episode-1 的另一版中文口播稿。"
        alternative_hash = hashlib.sha256(alternative_text.encode("utf-8")).hexdigest()
        alternative_script = PodcastTextArtifactRecord(
            id="script-episode-1-alternative",
            episode_id="episode-1",
            kind="narration_script_zh",
            version=2,
            content_hash=alternative_hash,
            inline_text=alternative_text,
            language="zh-CN",
            authority_id="",
            provenance_json='{"pipeline":"test"}',
            created_at="2026-09-05T01:00:00+00:00",
        )
        session.add(alternative_script)
        session.commit()

    with Session(sink.engine) as session:
        second_run = enqueue_processing(
            session,
            episode_id="episode-1",
            stage="tts",
            input_fingerprint="2" * 64,
            pipeline_version="audio-v1",
            policy_version="rights-v1",
            requested_target="digest_audio",
            idempotency_key="audio-run-2",
            input_artifact_id="script-episode-1-alternative",
            input_artifact_kind="narration_script_zh",
            input_content_hash=alternative_hash,
            input_language="zh-CN",
            budget_scope="podcast-paid-processing",
            budget_period="2026-09",
            budget_limit_minor=100,
            per_run_budget_minor=100,
            narration_artifact_id="script-episode-1-alternative",
            narration_content_hash=alternative_hash,
            voice_profile_id="test-voice",
            policy=internal_policy,
        )

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client, processing_id=first_run.id).json()
        duplicate = _import(client, processing_id=first_run.id)
        assert duplicate.status_code == 409
        mismatched_processing_input = _import(client, processing_id=second_run.id)
        assert mismatched_processing_input.status_code == 409
        wrong_episode = _import(
            client,
            episode_id="episode-2",
            processing_id=first_run.id,
        )
        assert wrong_episode.status_code == 409
        source_audio = _import(
            client,
            kind="source_audio",
            processing_id=first_run.id,
        )
        assert source_audio.status_code == 409

    with Session(sink.engine) as session:
        current_script = session.get(PodcastTextArtifactRecord, "script-episode-1")
        session.add(
            PodcastArtifactRecord(
                id="mismatched-processing-input",
                episode_id="episode-1",
                kind="digest_audio_zh",
                content_hash="d" * 64,
                mime="audio/mpeg",
                ext=".mp3",
                size_bytes=10,
                status="ready",
                provenance="test",
                authority_id="",
                narration_artifact_id=current_script.id,
                narration_content_hash=current_script.content_hash,
                processing_id=second_run.id,
                created_at="2026-09-05T01:00:00+00:00",
                updated_at="2026-09-05T01:00:00+00:00",
            )
        )
        with pytest.raises(
            IntegrityError,
            match="podcast audio processing dependency is invalid",
        ):
            session.commit()

    with Session(sink.engine) as session:
        script_two = session.get(PodcastTextArtifactRecord, "script-episode-2")
        session.add(
            PodcastArtifactRecord(
                id="wrong-processing-owner",
                episode_id="episode-2",
                kind="digest_audio_zh",
                content_hash="e" * 64,
                mime="audio/mpeg",
                ext=".mp3",
                size_bytes=10,
                status="ready",
                provenance="test",
                authority_id="",
                narration_artifact_id=script_two.id,
                narration_content_hash=script_two.content_hash,
                processing_id=second_run.id,
                created_at="2026-09-05T01:00:00+00:00",
                updated_at="2026-09-05T01:00:00+00:00",
            )
        )
        with pytest.raises(
            IntegrityError,
            match="podcast audio processing dependency is invalid",
        ):
            session.commit()

    with Session(sink.engine) as session:
        row = session.get(PodcastArtifactRecord, artifact["id"])
        row.processing_id = second_run.id
        session.add(row)
        with pytest.raises(IntegrityError, match="podcast audio binding is immutable"):
            session.commit()


def test_digest_audio_processing_recovery_returns_only_an_exact_intact_row(
    monkeypatch, tmp_path
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    processing = _enqueue_digest_audio(sink, idempotency_key="recovery-exact")
    narration_hash = hashlib.sha256(
        "episode-1 的中文口播稿。".encode("utf-8")
    ).hexdigest()
    content_hash = hashlib.sha256(WAV).hexdigest()
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client, processing_id=processing.id).json()

    recovered = store.find_digest_audio_by_processing_id(
        processing_id=processing.id,
        episode_id="episode-1",
        narration_artifact_id="script-episode-1",
        narration_content_hash=narration_hash.upper(),
        content_hash=content_hash.upper(),
        size_bytes=len(WAV),
        mime="audio/x-wav",
    )

    assert recovered is not None
    assert recovered.id == artifact["id"]
    assert recovered.processing_id == processing.id
    assert (
        store.find_digest_audio_by_processing_id(
            processing_id="missing-processing",
            episode_id="episode-1",
            narration_artifact_id="script-episode-1",
            narration_content_hash=narration_hash,
        )
        is None
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"episode_id": "episode-2"},
        {"narration_artifact_id": "script-episode-2"},
        {"narration_content_hash": "2" * 64},
        {"content_hash": "3" * 64},
        {"size_bytes": len(WAV) + 1},
        {"mime": "audio/mpeg"},
    ],
)
def test_digest_audio_processing_recovery_fails_closed_on_identity_mismatch(
    monkeypatch, tmp_path, overrides
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    processing = _enqueue_digest_audio(sink, idempotency_key="recovery-mismatch")
    narration_hash = hashlib.sha256(
        "episode-1 的中文口播稿。".encode("utf-8")
    ).hexdigest()
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client, processing_id=processing.id).json()

    expected = {
        "processing_id": processing.id,
        "episode_id": "episode-1",
        "narration_artifact_id": "script-episode-1",
        "narration_content_hash": narration_hash,
        "content_hash": artifact["content_hash"],
        "size_bytes": artifact["size_bytes"],
        "mime": artifact["mime"],
    }
    expected.update(overrides)
    with pytest.raises(
        PodcastArtifactRecoveryConflict,
        match="不同的精简音频产物",
    ):
        store.find_digest_audio_by_processing_id(**expected)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_digest_audio_processing_recovery_fails_closed_on_cas_damage(
    monkeypatch, tmp_path, damage
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    processing = _enqueue_digest_audio(sink, idempotency_key=f"recovery-{damage}")
    narration_hash = hashlib.sha256(
        "episode-1 的中文口播稿。".encode("utf-8")
    ).hexdigest()
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client, processing_id=processing.id).json()

    row = store.get(artifact["id"])
    assert row is not None
    path = store.file_path_for(row)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"X" * row.size_bytes)

    with pytest.raises(
        PodcastArtifactRecoveryConflict,
        match="CAS 缺失或损坏",
    ):
        store.find_digest_audio_by_processing_id(
            processing_id=processing.id,
            episode_id="episode-1",
            narration_artifact_id="script-episode-1",
            narration_content_hash=narration_hash,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"processing_id": ""}, "身份字段不能为空"),
        ({"narration_content_hash": "not-a-hash"}, "口播稿哈希无效"),
        ({"content_hash": "not-a-hash"}, "内容哈希无效"),
        ({"size_bytes": 0}, "大小必须是正整数"),
        ({"mime": "text/plain"}, "MIME 无效"),
    ],
)
def test_digest_audio_processing_recovery_rejects_invalid_expectations(
    monkeypatch, tmp_path, overrides, message
):
    _, _, store = _setup_app(monkeypatch, tmp_path)
    expected = {
        "processing_id": "processing-id",
        "episode_id": "episode-1",
        "narration_artifact_id": "script-episode-1",
        "narration_content_hash": "1" * 64,
    }
    expected.update(overrides)

    with pytest.raises(PodcastArtifactError, match=message):
        store.find_digest_audio_by_processing_id(**expected)


def test_import_rejects_bad_mime_oversize_non_podcast_and_non_admin(
    monkeypatch, tmp_path
):
    app_module, _, _ = _setup_app(monkeypatch, tmp_path, max_bytes=len(WAV) - 1)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert _import(client).status_code == 403

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        oversized = _import(client)
        assert oversized.status_code == 413
        assert "大小上限" in oversized.json()["detail"]
        bad_mime = _import(client, body=b"plain text", mime="text/plain")
        assert bad_mime.status_code == 415
        non_podcast = _import(client, episode_id="ordinary-1", body=WAV[:44])
        assert non_podcast.status_code == 400


def test_delete_requires_withdrawal(monkeypatch, tmp_path):
    app_module, _, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact_id = _import(client).json()["id"]
        response = client.delete(f"/api/admin/podcast-artifacts/{artifact_id}")
        assert response.status_code == 409
        assert "撤下" in response.json()["detail"]


def test_import_is_streamed_ready_local_and_probe_is_required(monkeypatch, tmp_path):
    app_module, _, store = _setup_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        store,
        "import_bytes",
        lambda **_kwargs: pytest.fail(
            "HTTP import must not buffer through import_bytes"
        ),
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = client.post(
            "/api/admin/podcast-artifacts/import/episode-1/digest_audio_zh",
            params={
                "status": "published",
                "authority_id": "spoofed",
                "narration_artifact_id": "script-episode-1",
                "narration_content_hash": hashlib.sha256(
                    "episode-1 的中文口播稿。".encode("utf-8")
                ).hexdigest(),
            },
            content=WAV,
            headers={"Content-Type": "audio/wav"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "ready"
        assert response.json()["authority_id"] == "dev-local"
        assert not list((store.root / ".incoming").glob("*.part"))

    missing = PodcastArtifactStore(
        store.engine,
        tmp_path / "missing-probe",
        max_bytes=1024,
        total_quota_bytes=10 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=3600,
        allowed_mime_types=("audio/wav",),
        ffprobe_binary="definitely-no-such-ffprobe",
    )
    monkeypatch.setattr(app_module, "podcast_artifact_store", missing)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = _import(client)
        assert response.status_code == 503
        assert "探测器不可用" in response.json()["detail"]


def test_probe_rejects_container_without_audio_stream(monkeypatch, tmp_path):
    app_module, _, store = _setup_app(monkeypatch, tmp_path)
    store._probe_runner = lambda *_args, **_kwargs: subprocess.CompletedProcess(
        [], 0, stdout='{"streams":[{"codec_type":"video"}]}', stderr=""
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = _import(client)
        assert response.status_code == 415
        assert "音频流" in response.json()["detail"]


def test_publish_uses_optimistic_lock_and_cannot_be_repeated(monkeypatch, tmp_path):
    app_module, _, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client).json()
        stale = client.post(
            f"/api/admin/podcast-artifacts/{artifact['id']}/publish",
            params={"expected_updated_at": "stale"},
        )
        assert stale.status_code == 409
        published = _publish(client, artifact)
        assert published.status_code == 200
        repeated = _publish(client, artifact)
        assert repeated.status_code == 409


def test_concurrent_publish_has_exactly_one_winner(monkeypatch, tmp_path):
    app_module, _, store = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client).json()

    barrier = threading.Barrier(2)

    def publish_once():
        barrier.wait()
        try:
            return store.publish(
                artifact["id"], expected_updated_at=artifact["updated_at"]
            ).status
        except PodcastArtifactConflict:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: publish_once(), range(2)))

    assert sorted(outcomes) == ["conflict", "published"]


@pytest.mark.parametrize(
    "method,range_header",
    [
        ("get", None),
        ("head", None),
        ("get", "bytes=0-5"),
    ],
)
def test_reader_audio_rechecks_script_after_authorization_before_serving(
    monkeypatch, tmp_path, method, range_header
):
    from api.routers import podcasts as podcasts_router

    app_module, sink, _ = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client).json()
        assert _publish(client, artifact).status_code == 200

        with Session(sink.engine) as session:
            text = "授权完成后刚刚替换的口播稿。"
            session.add(
                PodcastTextArtifactRecord(
                    id="script-raced-v2",
                    episode_id="episode-1",
                    kind="narration_script_zh",
                    version=2,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    inline_text=text,
                    language="zh-CN",
                    authority_id="",
                    provenance_json='{"pipeline":"test"}',
                    created_at="2026-09-05T03:00:00+00:00",
                )
            )
            session.commit()

        original_authorize = podcasts_router._ensure_reader_episode_visible
        calls = 0

        def replace_script_after_first_authorization(session, record, auth_session):
            nonlocal calls
            original_authorize(session, record, auth_session)
            calls += 1
            if calls == 1:
                publication = session.get(
                    PodcastTextPublicationRecord,
                    "episode-1:narration_script_zh",
                )
                publication.artifact_id = "script-raced-v2"
                publication.updated_at = "2026-09-05T03:00:00+00:00"
                session.add(publication)

        monkeypatch.setattr(
            podcasts_router,
            "_ensure_reader_episode_visible",
            replace_script_after_first_authorization,
        )
        _login(client, "user", "user")
        headers = {"Range": range_header} if range_header else {}
        response = getattr(client, method)(
            f"/api/reader/podcast-artifacts/{artifact['id']}/audio",
            headers=headers,
        )
        assert response.status_code == 404
        assert calls == 1


@pytest.mark.parametrize(
    "method,range_header",
    [
        ("get", None),
        ("head", None),
        ("get", "bytes=0-5"),
    ],
)
def test_reader_audio_enforces_hidden_and_private_source_visibility(
    monkeypatch, tmp_path, method, range_header
):
    app_module, sink, _ = _setup_app(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-1")
        episode.source_id = "podcast_hidden"
        session.add(episode)
        session.add(
            AppSettingRecord(key="reader_hidden_source_ids", value='["podcast_hidden"]')
        )
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client).json()
        assert _publish(client, artifact).status_code == 200
        _login(client, "user", "user")
        headers = {"Range": range_header} if range_header else {}
        response = getattr(client, method)(
            f"/api/reader/podcast-artifacts/{artifact['id']}/audio", headers=headers
        )
        assert response.status_code == 404

    # Private user feeds are visible only to a subscriber, exactly like article detail.
    with Session(sink.engine) as session:
        setting = session.get(AppSettingRecord, "reader_hidden_source_ids")
        session.delete(setting)
        episode = session.get(ArticleRecord, "episode-1")
        episode.source_id = "user_rss_private"
        session.add(episode)
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert (
            client.get(
                f"/api/reader/podcast-artifacts/{artifact['id']}/audio"
            ).status_code
            == 404
        )
    with Session(sink.engine) as session:
        session.add(
            ReaderSubscriptionRecord(
                owner_username="user",
                name="private",
                description="",
                filters_json='{"source_ids":"user_rss_private"}',
                delivery_policy_json="{}",
                token_hash="hash",
                token_preview="hash",
                is_active=True,
                created_at="2026-09-05T00:00:00+00:00",
                updated_at="2026-09-05T00:00:00+00:00",
            )
        )
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert (
            client.get(
                f"/api/reader/podcast-artifacts/{artifact['id']}/audio"
            ).status_code
            == 200
        )


def test_article_delete_cascades_registry_and_reconcile_reclaims_blob(
    monkeypatch, tmp_path
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        artifact = _import(client).json()
        path = store.file_path_for_hash(artifact["content_hash"], artifact["mime"])
        assert client.delete("/api/articles/episode-1").status_code == 200
        with Session(sink.engine) as session:
            assert session.get(PodcastArtifactRecord, artifact["id"]) is None
        assert path.is_file()
        stats = client.get("/api/admin/podcast-artifacts/stats").json()
        assert stats["orphan_blobs"] == 1
        assert stats["orphan_bytes"] == len(WAV)
        assert client.post("/api/admin/podcast-artifacts/reconcile").json() == {
            "expired_source_records": 0,
            "expired_protected": 0,
            "deleted_orphan_blobs": 1,
            "deleted_bytes": len(WAV),
            "deleted_staging_files": 0,
            "deleted_staging_bytes": 0,
        }
        assert not path.exists()


def test_reconcile_preserves_shared_blob_while_any_row_references_it(
    monkeypatch, tmp_path
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        first = _import(client, "episode-1").json()
        second = _import(client, "episode-2").json()
        path = store.file_path_for_hash(first["content_hash"], first["mime"])
        assert client.delete("/api/articles/episode-1").status_code == 200
        assert (
            client.post("/api/admin/podcast-artifacts/reconcile").json()[
                "deleted_orphan_blobs"
            ]
            == 0
        )
        assert path.exists()
        with Session(sink.engine) as session:
            session.delete(session.get(ArticleRecord, "episode-2"))
            session.commit()
            assert session.get(PodcastArtifactRecord, second["id"]) is None
        assert (
            client.post("/api/admin/podcast-artifacts/reconcile").json()[
                "deleted_orphan_blobs"
            ]
            == 1
        )
        assert not path.exists()
