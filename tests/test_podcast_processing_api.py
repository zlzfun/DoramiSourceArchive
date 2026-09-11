"""HTTP end-to-end contracts for provider-neutral Podcast processing commands."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import struct
import subprocess
import sys
from dataclasses import replace
from urllib.parse import parse_qs

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import (  # noqa: E402
    AliyunIsiConfig,
    PodcastConfig,
    PodcastWorkerConfig,
    RuntimeConfig,
)
from models.db import (  # noqa: E402
    ArticleRecord,
    PodcastBudgetReservationRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingCommandRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    SourceConfigRecord,
)
from services.aliyun_isi_asr import (  # noqa: E402
    AliyunIsiAsrClient,
)
from services.aliyun_isi_asr_worker import (  # noqa: E402
    AliyunIsiAsrAdmissionEstimator,
    AliyunIsiAsrWorkerBundle,
    aliyun_asr_admission_fingerprint,
    register_aliyun_isi_asr_worker,
)
from services.aliyun_isi_auth import AliyunPopClient  # noqa: E402
from services.podcast_artifacts import PodcastArtifactStore  # noqa: E402
from services.podcast_processing_admin import (  # noqa: E402
    PodcastAdminError,
    PodcastProcessingProviderRegistry,
)
from services.podcast_processing_inputs import (  # noqa: E402
    processing_input_fingerprint,
    source_media_duration_ms,
)
from services.podcast_processing import deterministic_input_fingerprint  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from tests.conftest import seed_default_accounts  # noqa: E402


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


def _external_config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id="podcast-external-test",
        allowed_stages=(
            "fetch",
            "asr",
            "translate",
            "analyze",
            "digest",
            "script",
        ),
        processing_enabled=True,
        provider_ready_targets=("transcript", "digest_blog"),
        monthly_budget_cny_minor=10_000,
        per_run_budget_cny_minor=1_000,
        budget_timezone="Asia/Shanghai",
    )


def _aliyun_config(now: dt.datetime, **updates) -> AliyunIsiConfig:
    values = {
        "access_key_id": "test-ak-id",
        "access_key_secret": "test-ak-secret",
        "app_key": "test-app-key",
        "asr_domain": "asr.example.test",
        "request_timeout_seconds": 5,
        "asr_poll_interval_seconds": 10,
        "asr_quota_scope": "test-asr-daily",
        "asr_quota_timezone": "Asia/Shanghai",
        "asr_daily_audio_seconds_limit": 7_200,
        "asr_entitlement_ends_at": (now + dt.timedelta(days=2)).isoformat(),
        "asr_provider_deadline_seconds": 300,
        "asr_price_cny_minor_per_hour": 3_600,
        "asr_pricing_revision": "test-one-minor-per-second-v1",
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def _registry(*targets: str) -> PodcastProcessingProviderRegistry:
    stages = {
        "transcript": {"asr"},
        "digest_blog": {"asr", "translate", "analyze", "digest", "script"},
        "digest_audio": {"tts", "audio_qa", "local_publish"},
    }
    registry = PodcastProcessingProviderRegistry()
    for target in targets:
        registry.register_target(
            target,
            stage_executors={
                stage: (lambda _context: None) for stage in stages[target]
            },
            estimator=lambda _metadata: 25,
        )
    return registry


@pytest.fixture()
def api_env(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'processing-api.db'}")
    seed_default_accounts(sink.engine)
    stamp = "2026-09-06T00:00:00+00:00"
    with Session(sink.engine) as session:
        for source_id, status in (
            ("podcast-ok", "approved"),
            ("podcast-blocked", "blocked"),
        ):
            session.add(
                SourceConfigRecord(
                    source_id=source_id,
                    name=source_id,
                    source_type="podcast",
                    url=f"https://example.test/{source_id}.xml",
                    category="podcast",
                    fetcher_id="generic_podcast_rss",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        session.add(
            ArticleRecord(
                id="episode-ok",
                title="Allowed episode",
                content_type="podcast_episode",
                source_id="podcast-ok",
                source_url="https://example.test/episode-ok",
                publish_date=stamp,
                fetched_date=stamp,
                content="show notes",
                extensions_json=json.dumps(
                    {"audio_url": "https://cdn.example.test/episode-ok.mp3?token=raw"}
                ),
            )
        )
        session.add(
            ArticleRecord(
                id="episode-blocked",
                title="Blocked episode",
                content_type="podcast_episode",
                source_id="podcast-blocked",
                source_url="https://example.test/episode-blocked",
                publish_date=stamp,
                fetched_date=stamp,
                content="show notes",
            )
        )
        session.commit()

    store = PodcastArtifactStore(
        sink.engine,
        tmp_path / "podcast-cas",
        max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=3600,
        allowed_mime_types=("audio/wav",),
        orphan_grace_seconds=0,
        probe_runner=_probe,
    )
    with Session(sink.engine) as session:
        source_media = PodcastSourceMediaSnapshotRecord(
            id="source-media-episode-ok",
            episode_id="episode-ok",
            locator_hash=hashlib.sha256(
                b"https://cdn.example.test/episode-ok.mp3?token=raw"
            ).hexdigest(),
            content_hash=hashlib.sha256(_wav()).hexdigest(),
            mime="audio/wav",
            size_bytes=len(_wav()),
            duration_seconds=0.002,
            created_at=stamp,
        )
        session.add(source_media)
        session.commit()
        session.refresh(source_media)
        session.expunge(source_media)
    config = _external_config()
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "podcast_artifact_store", store)
    monkeypatch.setattr(
        app_module,
        "podcast_processing_providers",
        _registry("transcript", "digest_blog"),
    )
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=config,
        ),
    )
    yield app_module, sink, store, source_media, config
    sink.engine.dispose()


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).status_code
        == 200
    )


def test_premium_threshold_api_persists_and_returns_effective_value(api_env):
    app_module, _sink, _store, _source_audio, _config = api_env
    with TestClient(app_module.app) as client:
        _login(client)
        initial = client.get("/api/admin/podcast-premium-tasks")
        assert initial.status_code == 200
        assert initial.json()["threshold"] == 8.0
        assert initial.json()["initial_processing_threshold"] == 5.0

        saved = client.put(
            "/api/admin/podcast-premium-threshold", json={"threshold": 7.5}
        )
        assert saved.status_code == 200
        assert saved.json()["threshold"] == 7.5
        refreshed = client.get("/api/admin/podcast-premium-tasks")
        assert refreshed.json()["threshold"] == 7.5

        assert client.put(
            "/api/admin/podcast-premium-threshold", json={"threshold": 8.55}
        ).status_code == 422


def test_force_tts_api_requires_auditable_idempotent_command(api_env, monkeypatch):
    app_module, _sink, _store, _source_audio, _config = api_env
    calls = []

    def schedule(episode_id, **kwargs):
        calls.append((episode_id, kwargs))
        return {
            "episode_id": episode_id,
            "status": "queued",
            "forced": True,
            "replayed": False,
            "should_schedule": True,
            "started": True,
        }

    monkeypatch.setattr(app_module, "schedule_forced_podcast_premium_guide", schedule)
    with TestClient(app_module.app) as client:
        _login(client)
        invalid = client.post(
            "/api/admin/podcast-premium-guides/episode-ok/force", json={}
        )
        assert invalid.status_code == 422
        response = client.post(
            "/api/admin/podcast-premium-guides/episode-ok/force",
            json={
                "idempotency_key": "force-episode-ok-0001",
                "reason": "  管理员手动强制生成 TTS  ",
            },
        )
    assert response.status_code == 202
    assert response.json() == {
        "episode_id": "episode-ok",
        "status": "queued",
        "forced": True,
        "replayed": False,
        "started": True,
    }
    assert calls == [(
        "episode-ok",
        {
            "idempotency_key": "force-episode-ok-0001",
            "reason": "管理员手动强制生成 TTS",
            "actor": "admin",
        },
    )]


@pytest.mark.parametrize("persisted_status", ["queued", "ready"])
def test_force_tts_replay_precedes_current_provider_readiness(
    api_env, persisted_status
):
    app_module, sink, _store, _source_audio, _config = api_env
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-ok")
        episode.extensions_json = json.dumps({
            "premium_guide": {
                "status": persisted_status,
                "force_request": {
                    "episode_id": "episode-ok",
                    "idempotency_key": "force-provider-change-0001",
                    "reason": "管理员手动强制生成 TTS",
                    "requested_by": "admin",
                    "selection_override": True,
                },
            }
        })
        session.add(episode)
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/admin/podcast-premium-guides/episode-ok/force",
            json={
                "idempotency_key": "force-provider-change-0001",
                "reason": "管理员手动强制生成 TTS",
            },
        )
    assert response.status_code == 202
    assert response.json() == {
        "episode_id": "episode-ok",
        "status": persisted_status,
        "forced": True,
        "replayed": True,
        "started": False,
    }


def test_provider_registry_requires_the_complete_target_stage_chain():
    registry = PodcastProcessingProviderRegistry()
    with pytest.raises(ValueError, match="one executor per pipeline stage"):
        registry.register_target(
            "digest_audio",
            stage_executors={"tts": object()},
            estimator=lambda _metadata: 1,
        )
    for invalid_executor in (object(), None):
        with pytest.raises(TypeError, match="stage executors must be callable"):
            registry.register_target(
                "transcript",
                stage_executors={"asr": invalid_executor},
                estimator=lambda _metadata: 1,
            )
        assert registry.is_ready("transcript") is False

    executor = lambda _context: None
    registry.register_target(
        "transcript",
        stage_executors={"asr": executor},
        estimator=lambda _metadata: 1,
    )
    assert registry.executor_for("transcript", "asr") is executor


def test_worker_backed_registry_is_distinct_and_duration_binding_is_strict():
    registry = PodcastProcessingProviderRegistry()
    worker = lambda _session, **_kwargs: None
    registry.register_stage_worker("asr", worker, readiness=lambda _config: True)
    registry.register_worker_backed_target(
        "transcript",
        estimator=lambda _session, _metadata, _config: None,
    )
    assert registry.is_ready("transcript") is True
    assert registry.is_ready("digest_blog") is False
    assert registry.is_ready("digest_audio") is False
    assert registry.worker_for("asr") is worker
    with pytest.raises(PodcastAdminError):
        registry.executor_for("transcript", "asr")
    with pytest.raises(ValueError, match="worker-backed"):
        registry.register_target(
            "transcript",
            stage_executors={"asr": lambda _context: None},
            estimator=lambda _metadata: 0,
        )
    legacy = _registry("transcript")
    with pytest.raises(ValueError, match="legacy"):
        legacy.register_worker_backed_target(
            "transcript",
            estimator=lambda _session, _metadata, _config: None,
        )

    assert source_media_duration_ms(0.002) == 2
    assert processing_input_fingerprint(
        episode_id="episode",
        entry_stage="asr",
        artifact_id="artifact",
        content_hash="a" * 64,
        kind="source_media_snapshot",
        language="und",
        audio_duration_ms=2,
        admission_fingerprint="",
    ) == deterministic_input_fingerprint(
        {
            "schema": "podcast-processing-input-v1",
            "episode_id": "episode",
            "entry_stage": "asr",
            "artifact_id": "artifact",
            "content_hash": "a" * 64,
            "kind": "source_media_snapshot",
            "language": "und",
            "voice_profile_id": "",
        }
    )
    for invalid in (True, 0, -1.0, float("nan"), float("inf"), "0.002"):
        with pytest.raises(ValueError):
            source_media_duration_ms(invalid)
    now = dt.datetime.now(dt.timezone.utc)
    aliyun = _aliyun_config(now)
    assert aliyun_asr_admission_fingerprint(aliyun) == (
        aliyun_asr_admission_fingerprint(
            replace(
                aliyun,
                access_key_id="rotated-ak",
                access_key_secret="rotated-sk",
            )
        )
    )
    assert aliyun_asr_admission_fingerprint(aliyun) != (
        aliyun_asr_admission_fingerprint(
            replace(aliyun, app_key="different-submit-app-key")
        )
    )
    assert aliyun_asr_admission_fingerprint(aliyun) != (
        aliyun_asr_admission_fingerprint(
            replace(aliyun, asr_max_audio_seconds_per_file=3_600)
        )
    )


def test_external_process_is_bound_redacted_and_idempotent(api_env, monkeypatch):
    app_module, _sink, _store, source_media, config = api_env
    body = {
        "target": "transcript",
        "selection_override": True,
        "reason": "editor selected this episode",
        "idempotency_key": "process-episode-ok-0001",
    }
    with TestClient(app_module.app) as client:
        _login(client)
        missing_selection = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={**body, "selection_override": False},
        )
        assert missing_selection.status_code == 409

        created = client.post(
            "/api/admin/podcast-episodes/episode-ok/process", json=body
        )
        assert created.status_code == 202
        payload = created.json()
        assert payload["status"] == "queued"
        assert payload["stage"] == "asr"
        assert payload["input_artifact_id"] == source_media.id
        assert payload["input_content_hash"] == source_media.content_hash
        assert payload["budget_limit_minor"] == 10_000
        assert payload["per_run_budget_minor"] == 1_000
        assert not (
            {"lease_token", "provider_request_key", "local_path"} & payload.keys()
        )
        assert created.headers["cache-control"] == "private, no-store"

        conflict = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={**body, "idempotency_key": "process-episode-ok-0002"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["processing_id"] == payload["id"]

        disabled = replace(
            config,
            processing_enabled=False,
            provider_ready_targets=(),
            monthly_budget_cny_minor=0,
            per_run_budget_cny_minor=0,
        )
        monkeypatch.setattr(
            app_module, "settings", replace(app_module.settings, podcast=disabled)
        )
        replay = client.post(
            "/api/admin/podcast-episodes/episode-ok/process", json=body
        )
        assert replay.status_code == 202
        assert replay.json()["id"] == payload["id"]


def test_full_analysis_http_rejects_remote_authority_before_provider_work(
    api_env, monkeypatch
):
    app_module, sink, _store, _source_media, _config = api_env
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-ok")
        episode.analysis_authority_id = "remote-analysis-producer"
        session.add(episode)
        session.commit()

    monkeypatch.setattr(
        app_module.podcast_processing_admin_service,
        "require_full_analysis_llm",
        lambda *_args, **_kwargs: pytest.fail(
            "remote authority must be rejected before LLM resolution"
        ),
    )
    monkeypatch.setattr(
        app_module.podcast_publisher_transcript_service,
        "publisher_transcript_refresh_revision",
        lambda *_args, **_kwargs: pytest.fail(
            "remote authority must be rejected before transcript refresh"
        ),
    )
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={
                "target": "full_analysis",
                "selection_override": True,
                "reason": "remote analysis authority must remain single writer",
                "idempotency_key": "remote-authority-http-e2e-01",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "podcast_stage_denied"
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []


def test_admin_transcript_enqueue_runs_worker_backed_aliyun_e2e(api_env, monkeypatch):
    app_module, sink, _store, source_media, podcast_config = api_env
    now = [dt.datetime.now(dt.timezone.utc).replace(microsecond=0)]
    aliyun = _aliyun_config(now[0])
    aliyun_resolutions = 0
    submit_calls = 0
    poll_calls = 0
    http_clients: list[httpx.Client] = []

    def resolve_aliyun(_session):
        nonlocal aliyun_resolutions
        aliyun_resolutions += 1
        return aliyun

    def handler(request: httpx.Request):
        nonlocal submit_calls, poll_calls
        if request.method == "POST":
            submit_calls += 1
            task = json.loads(parse_qs(request.content.decode("utf-8"))["Task"][0])
            assert task["file_link"] == (
                "https://cdn.example.test/episode-ok.mp3?token=raw"
            )
            return httpx.Response(
                200,
                json={
                    "TaskId": "aliyun-admin-e2e-task",
                    "StatusCode": 21050000,
                    "StatusText": "SUCCESS",
                },
            )
        poll_calls += 1
        assert request.url.params["Action"] == "GetTaskResult"
        return httpx.Response(
            200,
            json={
                "TaskId": "aliyun-admin-e2e-task",
                "StatusCode": 21050000,
                "StatusText": "SUCCESS",
                "BizDuration": 2,
                "Result": {
                    "Sentences": [
                        {
                            "BeginTime": 0,
                            "EndTime": 2,
                            "Text": "好。",
                            "ChannelId": 0,
                        }
                    ],
                    "Words": [],
                },
            },
        )

    def client_factory(snapshot):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        http_clients.append(http)
        return AliyunIsiAsrClient(
            snapshot,
            pop_client=AliyunPopClient(
                snapshot,
                http_client=http,
                nonce_factory=lambda: "test-nonce",
                clock=lambda: now[0],
            ),
            file_url_resolver=lambda value: value,
        )

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=client_factory,
        clock=lambda: now[0],
    )
    estimator = AliyunIsiAsrAdmissionEstimator(
        config_resolver=resolve_aliyun,
        clock=lambda: now[0],
    )
    registry = PodcastProcessingProviderRegistry()
    register_aliyun_isi_asr_worker(
        registry,
        bundle=bundle,
        admission_estimator=estimator,
    )
    assert registry.is_ready("transcript") is True
    assert registry.is_ready("digest_blog") is False
    assert registry.is_ready("digest_audio") is False

    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=podcast_config,
            podcast_worker=PodcastWorkerConfig(
                tick_seconds=10,
                lease_seconds=30,
                heartbeat_seconds=10,
                fallback_retry_seconds=15,
                max_steps_per_tick=1,
            ),
            aliyun_isi=aliyun,
        ),
    )
    monkeypatch.setattr(
        app_module.aliyun_isi_config_service,
        "resolve_config",
        lambda _session: aliyun,
    )
    body = {
        "target": "transcript",
        "selection_override": True,
        "reason": "worker backed transcript E2E",
        "idempotency_key": "worker-backed-transcript-e2e-01",
    }
    try:
        with TestClient(app_module.app) as client:
            _login(client)
            created = client.post(
                "/api/admin/podcast-episodes/episode-ok/process", json=body
            )
            assert created.status_code == 202
            payload = created.json()
            assert payload["estimated_cost_minor"] == 1
            assert payload["input_artifact_id"] == source_media.id
            assert aliyun_resolutions == 1
            assert submit_calls == poll_calls == 0

            assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == (
                "poll_scheduled",
            )
            now[0] += dt.timedelta(seconds=10)
            assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == (
                "completed",
            )
    finally:
        for http in http_clients:
            http.close()

    assert submit_calls == poll_calls == 1
    with Session(sink.engine) as session:
        process = session.get(PodcastProcessingRecord, payload["id"])
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == payload["id"]
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        ledger = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.attempt_id == attempt.id
            )
        ).one()
        transcript = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == payload["id"]
            )
        ).one()
        assert process is not None and process.processing_status == "ready"
        assert reservation.reserved_usage_units == 1
        assert ledger.actual_usage_units == 1
        assert ledger.actual_cost_minor == 1
        assert json.loads(transcript.inline_text)["audio_duration_ms"] == 2
        persisted = json.dumps(
            {
                "input_fingerprint": process.input_fingerprint,
                "settings_fingerprint": attempt.settings_fingerprint,
                "provenance": transcript.provenance_json,
            }
        )
        assert aliyun.app_key not in persisted
        assert aliyun.access_key_id not in persisted
        assert aliyun.access_key_secret not in persisted
        assert "signature=" not in persisted


@pytest.mark.parametrize(
    "failure",
    (
        "missing_ak",
        "missing_app_key",
        "missing_accounting",
    ),
)
def test_worker_backed_transcript_admission_failure_is_503_without_record(
    api_env, monkeypatch, failure
):
    app_module, sink, _store, _source_media, podcast_config = api_env
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    aliyun_updates = {
        "missing_ak": {"access_key_id": "", "access_key_secret": ""},
        "missing_app_key": {"app_key": ""},
        "missing_accounting": {"asr_quota_scope": ""},
    }.get(failure, {})
    aliyun = _aliyun_config(now, **aliyun_updates)
    client_factory_calls = 0

    def client_factory(_snapshot):
        nonlocal client_factory_calls
        client_factory_calls += 1
        raise AssertionError("admin admission must not construct a client")

    bundle = AliyunIsiAsrWorkerBundle(client_factory=client_factory)
    estimator = AliyunIsiAsrAdmissionEstimator(
        config_resolver=lambda _session: aliyun,
        clock=lambda: now,
    )
    registry = PodcastProcessingProviderRegistry()
    register_aliyun_isi_asr_worker(
        registry,
        bundle=bundle,
        admission_estimator=estimator,
    )
    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={
                "target": "transcript",
                "selection_override": True,
                "reason": f"admission failure {failure}",
                "idempotency_key": f"admission-failure-{failure}-01",
            },
        )
    assert response.status_code == 503
    assert response.json()["code"] == "podcast_provider_unavailable"
    assert client_factory_calls == 0
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []


def test_worker_backed_transcript_rejects_overlong_audio_before_enqueue(
    api_env, monkeypatch
):
    app_module, sink, _store, source_media, _podcast_config = api_env
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    with Session(sink.engine) as session:
        record = session.get(PodcastSourceMediaSnapshotRecord, source_media.id)
        record.duration_seconds = 2
        session.add(record)
        session.commit()

    aliyun = _aliyun_config(now, asr_max_audio_seconds_per_file=1)
    registry = PodcastProcessingProviderRegistry()
    register_aliyun_isi_asr_worker(
        registry,
        bundle=AliyunIsiAsrWorkerBundle(
            client_factory=lambda _snapshot: (_ for _ in ()).throw(
                AssertionError("overlong admission must not construct a client")
            )
        ),
        admission_estimator=AliyunIsiAsrAdmissionEstimator(
            config_resolver=lambda _session: aliyun,
            clock=lambda: now,
        ),
    )
    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={
                "target": "transcript",
                "selection_override": True,
                "reason": "reject overlong source media",
                "idempotency_key": "reject-overlong-source-media-01",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "podcast_source_media_too_long"
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []


def test_aliyun_pricing_rotation_before_first_attempt_fails_without_network(
    api_env, monkeypatch
):
    app_module, sink, _store, _source_media, podcast_config = api_env
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    original = _aliyun_config(now)
    rotated = replace(
        original,
        asr_price_cny_minor_per_hour=7_200,
        asr_pricing_revision="test-two-minor-per-second-v2",
    )
    admission_snapshot = [original]
    submit_calls = 0

    class NoNetworkClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            raise AssertionError("rotated admission must fail before submit")

        def poll(self, _task_id):
            raise AssertionError("no task exists to poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _snapshot: NoNetworkClient(),
        clock=lambda: now,
    )
    registry = PodcastProcessingProviderRegistry()
    register_aliyun_isi_asr_worker(
        registry,
        bundle=bundle,
        admission_estimator=AliyunIsiAsrAdmissionEstimator(
            config_resolver=lambda _session: admission_snapshot[0],
            clock=lambda: now,
        ),
    )
    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=podcast_config,
            podcast_worker=PodcastWorkerConfig(
                tick_seconds=10,
                lease_seconds=30,
                heartbeat_seconds=10,
                fallback_retry_seconds=15,
                max_steps_per_tick=1,
            ),
            aliyun_isi=rotated,
        ),
    )
    monkeypatch.setattr(
        app_module.aliyun_isi_config_service,
        "resolve_config",
        lambda _session: rotated,
    )
    with TestClient(app_module.app) as client:
        _login(client)
        created = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={
                "target": "transcript",
                "selection_override": True,
                "reason": "freeze original admission before price rotation",
                "idempotency_key": "pricing-rotation-before-submit-01",
            },
        )
        assert created.status_code == 202
        created_payload = created.json()
        processing_id = created_payload["id"]
        admission_snapshot[0] = rotated
        assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == ("failed",)
        replacement = client.post(
            "/api/admin/podcast-episodes/episode-ok/process",
            json={
                "target": "transcript",
                "selection_override": True,
                "reason": "enqueue under the rotated provider admission",
                "idempotency_key": "pricing-rotation-replacement-02",
            },
        )
        assert replacement.status_code == 202
        replacement_payload = replacement.json()
        assert replacement_payload["estimated_cost_minor"] == 2
        assert (
            replacement_payload["input_fingerprint"]
            != created_payload["input_fingerprint"]
        )

    assert submit_calls == 0
    with Session(sink.engine) as session:
        process = session.get(PodcastProcessingRecord, processing_id)
        assert process is not None
        assert process.processing_status == "failed"
        assert process.error_code == "provider_admission_changed"
        assert process.lease_token is None
        assert process.attempt_count == 0
        replacement = session.get(PodcastProcessingRecord, replacement_payload["id"])
        assert replacement is not None
        assert replacement.processing_status == "queued"
        assert session.exec(select(PodcastStageAttemptRecord)).all() == []
        assert session.exec(select(PodcastBudgetReservationRecord)).all() == []
        assert session.exec(select(PodcastCostLedgerRecord)).all() == []


def test_retry_rejection_replays_original_status(api_env, monkeypatch):
    app_module, sink, _store, _source_media, _config = api_env
    process_body = {
        "target": "transcript",
        "selection_override": True,
        "reason": "retry contract fixture",
        "idempotency_key": "retry-source-process-01",
    }
    with TestClient(app_module.app) as client:
        _login(client)
        created = client.post(
            "/api/admin/podcast-episodes/episode-ok/process", json=process_body
        ).json()
        with Session(sink.engine) as session:
            record = session.get(PodcastProcessingRecord, created["id"])
            assert record is not None
            record.processing_status = "failed"
            record.error_code = "provider_error"
            record.error_message = "test failure"
            record.finished_at = "2026-09-06T01:00:00+00:00"
            session.add(record)
            session.commit()
        monkeypatch.setattr(
            app_module,
            "podcast_processing_providers",
            PodcastProcessingProviderRegistry(),
        )
        retry_body = {
            "idempotency_key": "manual-retry-command-01",
            "expected_attempt_count": 0,
            "reason": "operator verified safe retry",
        }
        first = client.post(
            f"/api/admin/podcast-processings/{created['id']}/retry", json=retry_body
        )
        second = client.post(
            f"/api/admin/podcast-processings/{created['id']}/retry", json=retry_body
        )
        assert first.status_code == second.status_code == 503
        assert first.json() == second.json()
    with Session(sink.engine) as session:
        commands = list(session.exec(select(PodcastProcessingCommandRecord)).all())
        assert len(commands) == 1
        assert commands[0].outcome == "rejected"
        commands[0].outcome = "accepted"
        session.add(commands[0])
        with pytest.raises(IntegrityError, match="command is immutable"):
            session.commit()
        session.rollback()
