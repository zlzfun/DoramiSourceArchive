"""HTTP E2E for external enclosure cache, processing binding, and TTL GC."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import threading
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from config import PodcastArtifactStorageConfig, PodcastConfig, RuntimeConfig
from models.db import (
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    SourceConfigRecord,
)
from services.podcast_artifacts import (
    PodcastArtifactStorageFull,
    PodcastArtifactStore,
)
from services.podcast_processing_admin import PodcastProcessingProviderRegistry
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


def _wav(samples: int = 16) -> bytes:
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


WAV = _wav()
STAMP = "2026-09-06T00:00:00.000000+00:00"
SIGNED_URL = "https://audio.publisher.test/episode.wav?token=do-not-expose"


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


def _external_config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id="podcast-external-e2e",
        allowed_stages=(
            "fetch",
            "asr",
            "translate",
            "analyze",
            "digest",
            "script",
        ),
        processing_enabled=True,
        provider_ready_targets=("transcript",),
        monthly_budget_cny_minor=10_000,
        per_run_budget_cny_minor=1_000,
        budget_timezone="Asia/Shanghai",
    )


def _registry() -> PodcastProcessingProviderRegistry:
    registry = PodcastProcessingProviderRegistry()
    registry.register_target(
        "transcript",
        stage_executors={"asr": lambda _context: None},
        estimator=lambda _metadata: 25,
    )
    return registry


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'source-audio-e2e.db'}")
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-source",
                name="Podcast",
                source_type="podcast",
                url="https://publisher.test/feed.xml",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            ArticleRecord(
                id="episode-source",
                title="Source cache episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://publisher.test/episodes/1",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
                extensions_json=json.dumps(
                    {
                        "audio_url": SIGNED_URL,
                        "audio_mime": "audio/wav",
                        "audio_bytes": len(WAV),
                    }
                ),
            )
        )
        session.commit()

    storage_config = PodcastArtifactStorageConfig(
        root_dir=str(tmp_path / "source-audio-cas"),
        max_audio_mb=1,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        allowed_mime_types=("audio/wav",),
        download_timeout_seconds=5,
        download_max_redirects=2,
        source_audio_ttl_seconds=3600,
        source_audio_quota_bytes=1024 * 1024,
        orphan_grace_seconds=0,
        staging_ttl_seconds=0,
    )
    store = PodcastArtifactStore(
        sink.engine,
        storage_config.root_dir,
        max_bytes=storage_config.max_audio_mb * 1024 * 1024,
        total_quota_bytes=storage_config.total_quota_bytes,
        source_audio_quota_bytes=storage_config.source_audio_quota_bytes,
        source_audio_ttl_seconds=storage_config.source_audio_ttl_seconds,
        minimum_free_bytes=storage_config.minimum_free_bytes,
        staging_ttl_seconds=storage_config.staging_ttl_seconds,
        allowed_mime_types=storage_config.allowed_mime_types,
        orphan_grace_seconds=storage_config.orphan_grace_seconds,
        probe_runner=_probe,
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "podcast_artifact_store", store)
    monkeypatch.setattr(app_module, "podcast_processing_providers", _registry())
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=_external_config(),
            podcast_artifacts=storage_config,
        ),
    )
    return app_module, sink, store


def test_external_cache_to_processing_and_ttl_gc_http_e2e(monkeypatch, tmp_path):
    from api.routers import podcasts as podcasts_router
    from services import podcast_artifacts as artifact_service

    app_module, sink, store = _setup(monkeypatch, tmp_path)
    real_async_client = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav", "Content-Length": str(len(WAV))},
            stream=httpx.ByteStream(WAV),
        )

    def client_factory(*_args, **_kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )

    monkeypatch.setattr(podcasts_router.httpx, "AsyncClient", client_factory)
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        cached = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert cached.status_code == 200, cached.text
        artifact = cached.json()
        assert artifact["kind"] == "source_audio"
        assert artifact["status"] == "ready"
        assert artifact["retention_state"] == "temporary"
        assert (
            artifact["source_locator_hash"]
            == hashlib.sha256(SIGNED_URL.encode("utf-8")).hexdigest()
        )
        assert artifact["expires_at"]
        assert "do-not-expose" not in cached.text
        assert str(requests[0].url) == SIGNED_URL
        assert requests[0].headers["Host"] == "audio.publisher.test"
        assert (
            store.file_path_for_hash(artifact["content_hash"], "audio/wav").read_bytes()
            == WAV
        )

        # Same live locator is idempotent and never performs a second network call.
        replay = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == artifact["id"]
        assert len(requests) == 1

        queued = client.post(
            "/api/admin/podcast-episodes/episode-source/process",
            json={
                "target": "transcript",
                "selection_override": True,
                "reason": "editor selected this episode",
                "idempotency_key": "source-cache-process-0001",
            },
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["input_artifact_id"] == artifact["id"]

        # An active task pins an expired cache; once terminal, the same HTTP GC
        # call marks it expired and removes the now-unreferenced local blob.
        monkeypatch.setattr(
            artifact_service,
            "_now",
            lambda: "2099-01-01T00:00:00.000000+00:00",
        )
        protected = client.post("/api/admin/podcast-artifacts/reconcile")
        assert protected.status_code == 200
        assert protected.json()["expired_protected"] == 1
        with Session(sink.engine) as session:
            assert session.get(PodcastArtifactRecord, artifact["id"]).status == "ready"
            process = session.get(PodcastProcessingRecord, queued.json()["id"])
            process.processing_status = "failed"
            process.finished_at = "2026-09-06T01:00:00.000000+00:00"
            process.updated_at = process.finished_at
            session.add(process)
            session.commit()
        reclaimed = client.post("/api/admin/podcast-artifacts/reconcile")
        assert reclaimed.status_code == 200
        assert reclaimed.json()["expired_source_records"] == 1
        assert reclaimed.json()["deleted_orphan_blobs"] == 1
        listed = client.get("/api/admin/podcast-artifacts").json()["items"]
        assert listed[0]["status"] == "expired"
        assert listed[0]["retention_state"] == "expired"
        assert not store.file_path_for_hash(
            artifact["content_hash"], "audio/wav"
        ).exists()

    sink.engine.dispose()


def test_internal_installation_never_downloads_source_audio(monkeypatch, tmp_path):
    app_module, sink, _store = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            podcast=replace(
                app_module.settings.podcast,
                installation="internal",
                allowed_stages=(),
                processing_enabled=False,
                provider_ready_targets=(),
            ),
        ),
    )
    with TestClient(app_module.app) as client:
        unauthenticated = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {
            "code": "podcast_auth_required",
            "message": "未登录或登录已过期",
        }
        assert (
            client.post(
                "/api/auth/login", json={"username": "user", "password": "user"}
            ).status_code
            == 200
        )
        forbidden = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert forbidden.status_code == 403
        assert forbidden.json() == {
            "code": "podcast_admin_required",
            "message": "该操作需要管理员账号",
        }
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        denied = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert denied.status_code == 409
        assert denied.json() == {
            "code": "podcast_processing_conflict",
            "message": "source_audio 只能由 external Podcast installation 抓取",
        }
    sink.engine.dispose()


def test_source_cache_reports_stable_storage_unavailable_error(monkeypatch, tmp_path):
    app_module, sink, _store = _setup(monkeypatch, tmp_path)

    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        monkeypatch.setattr(app_module, "podcast_artifact_store", None)
        response = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert response.status_code == 503
        assert response.json() == {
            "code": "podcast_storage_unavailable",
            "message": "Podcast artifact storage 未配置",
        }

    sink.engine.dispose()


def test_enclosure_rotation_during_download_fails_closed_without_cache(
    monkeypatch, tmp_path
):
    from api.routers import podcasts as podcasts_router
    app_module, sink, store = _setup(monkeypatch, tmp_path)
    real_async_client = httpx.AsyncClient

    def handler(_request: httpx.Request) -> httpx.Response:
        with Session(sink.engine) as session:
            episode = session.get(ArticleRecord, "episode-source")
            metadata = json.loads(episode.extensions_json)
            metadata["audio_url"] = "https://audio.publisher.test/rotated.wav"
            episode.extensions_json = json.dumps(metadata)
            session.add(episode)
            session.commit()
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(WAV),
        )

    monkeypatch.setattr(
        podcasts_router.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: real_async_client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert response.status_code == 409
        assert "do-not-expose" not in response.text

    with Session(sink.engine) as session:
        assert session.exec(select(PodcastArtifactRecord)).all() == []
    assert list(store.root.glob("*/*.wav")) == []
    assert list((store.root / ".incoming").glob("*.part")) == []
    sink.engine.dispose()


def test_source_cache_maps_network_timeout_to_gateway_timeout(monkeypatch, tmp_path):
    from api.routers import podcasts as podcasts_router
    app_module, sink, _store = _setup(monkeypatch, tmp_path)
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream stalled", request=request)

    monkeypatch.setattr(
        podcasts_router.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert response.status_code == 504
        assert response.json()["code"] == "podcast_source_audio_timeout"
        assert set(response.json()) == {"code", "message"}
        assert "do-not-expose" not in response.text
    sink.engine.dispose()


def test_source_cache_redownload_repairs_corrupt_locator_blob(monkeypatch, tmp_path):
    from api.routers import podcasts as podcasts_router
    app_module, sink, store = _setup(monkeypatch, tmp_path)
    real_async_client = httpx.AsyncClient
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/wav"},
            stream=httpx.ByteStream(WAV),
        )

    monkeypatch.setattr(
        podcasts_router.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        first = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert first.status_code == 200
        artifact = first.json()
        path = store.file_path_for_hash(artifact["content_hash"], artifact["mime"])
        path.write_bytes(b"X" * len(WAV))

        repaired = client.post(
            "/api/admin/podcast-episodes/episode-source/cache-source-audio"
        )
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["id"] == artifact["id"]
        assert requests == 2
        assert path.read_bytes() == WAV
        with Session(sink.engine) as session:
            assert len(session.exec(select(PodcastArtifactRecord)).all()) == 1
    sink.engine.dispose()


def test_source_quota_counts_unique_live_blobs(monkeypatch, tmp_path):
    _app_module, sink, original_store = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        for suffix in ("two", "three"):
            session.add(
                ArticleRecord(
                    id=f"episode-{suffix}",
                    title=suffix,
                    content_type="podcast_episode",
                    source_id="podcast-source",
                    source_url=f"https://publisher.test/{suffix}",
                    publish_date=STAMP,
                    fetched_date=STAMP,
                    content="show notes",
                )
            )
        session.commit()
    store = PodcastArtifactStore(
        sink.engine,
        original_store.root,
        max_bytes=1024 * 1024,
        total_quota_bytes=2 * 1024 * 1024,
        source_audio_quota_bytes=len(WAV),
        source_audio_ttl_seconds=3600,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
        orphan_grace_seconds=0,
        probe_runner=_probe,
    )
    first = store.import_bytes(
        episode_id="episode-source",
        kind="source_audio",
        data=WAV,
        declared_mime="audio/wav",
        authority_id="podcast-external-e2e",
    )
    second = store.import_bytes(
        episode_id="episode-two",
        kind="source_audio",
        data=WAV,
        declared_mime="audio/wav",
        authority_id="podcast-external-e2e",
    )
    assert first.id != second.id
    assert first.content_hash == second.content_hash
    assert store.stats()["source_audio_bytes"] == len(WAV)
    with pytest.raises(PodcastArtifactStorageFull, match="临时缓存配额不足"):
        store.import_bytes(
            episode_id="episode-three",
            kind="source_audio",
            data=_wav(17),
            declared_mime="audio/wav",
            authority_id="podcast-external-e2e",
        )
    sink.engine.dispose()


def test_source_download_reservation_blocks_concurrent_quota_overcommit(
    monkeypatch, tmp_path
):
    _app_module, sink, original_store = _setup(monkeypatch, tmp_path)
    store = PodcastArtifactStore(
        sink.engine,
        original_store.root,
        max_bytes=1024 * 1024,
        total_quota_bytes=2 * 1024 * 1024,
        source_audio_quota_bytes=len(WAV),
        source_audio_ttl_seconds=3600,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
        orphan_grace_seconds=0,
        probe_runner=_probe,
    )
    entered = threading.Event()
    release = threading.Event()

    def hold_reservation() -> None:
        with store.reserve_source_download(len(WAV)):
            entered.set()
            assert release.wait(timeout=5)

    worker = threading.Thread(target=hold_reservation)
    worker.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(PodcastArtifactStorageFull, match="临时缓存配额不足"):
            with store.reserve_source_download(len(WAV)):
                pass
        stats = store.stats()
        assert stats["download_reservations"] == 1
        assert stats["download_reserved_bytes"] == len(WAV)
        reconciled = store.reconcile_storage()
        assert reconciled["deleted_staging_files"] == 0
        assert store.stats()["download_reservations"] == 1
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert store.stats()["download_reservations"] == 0
    sink.engine.dispose()


def test_source_download_reservation_checks_minimum_free_space_before_staging(
    monkeypatch, tmp_path
):
    _app_module, sink, original_store = _setup(monkeypatch, tmp_path)
    store = PodcastArtifactStore(
        sink.engine,
        original_store.root,
        max_bytes=1024 * 1024,
        total_quota_bytes=2 * 1024 * 1024,
        source_audio_quota_bytes=1024 * 1024,
        source_audio_ttl_seconds=3600,
        minimum_free_bytes=4096,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
        orphan_grace_seconds=0,
        probe_runner=_probe,
        disk_usage_provider=lambda _path: SimpleNamespace(
            total=1024 * 1024,
            used=1024 * 1024 - len(WAV) - 4095,
            free=len(WAV) + 4095,
        ),
    )
    with pytest.raises(PodcastArtifactStorageFull, match="磁盘可用空间不足"):
        with store.reserve_source_download(len(WAV)):
            pass
    assert list((store.root / ".incoming").glob("download-*.reserve")) == []
    sink.engine.dispose()


def test_digest_download_reservation_does_not_consume_source_subquota(
    monkeypatch, tmp_path
):
    _app_module, sink, original_store = _setup(monkeypatch, tmp_path)
    store = PodcastArtifactStore(
        sink.engine,
        original_store.root,
        max_bytes=1024 * 1024,
        total_quota_bytes=3 * len(WAV),
        source_audio_quota_bytes=len(WAV),
        source_audio_ttl_seconds=3600,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
        orphan_grace_seconds=0,
        probe_runner=_probe,
    )

    with store.reserve_download("digest_audio_zh", len(WAV)):
        with store.reserve_source_download(len(WAV)):
            stats = store.stats()
            assert stats["download_reservations"] == 2
            assert stats["download_reserved_bytes"] == 2 * len(WAV)

    assert store.stats()["download_reservations"] == 0
    sink.engine.dispose()
