"""End-to-end security contract for the public provider ASR audio route."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import replace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

import config
from config import PodcastAsrFetchConfig, PodcastConfig
from models.db import (
    AdminAuditRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
)
from services.aliyun_isi_asr import AsrSubmission
from services.aliyun_isi_asr_worker import AliyunIsiAsrAdapter
from services.podcast_asr_fetch_signing import PodcastAsrFetchUrlSigner
from services import credentials, podcast_asr_fetch_signing
from services.aliyun_isi_usage import asr_usage_plan
from services.podcast_processing import (
    begin_stage_attempt,
    claim_next_processing,
    enqueue_processing,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services.podcast_worker_contracts import (
    Accepted,
    ArtifactRef,
    StageContext,
)
from tests.test_podcast_artifacts import WAV, _import, _login, _setup_app


SECRET = "asr-public-route-test-secret-000000000000"
AUTHORITY_ID = "external-test-authority"
PUBLIC_BASE_URL = "https://audio.example.test/api/public/podcast-asr/source-audio"


def _podcast_config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id=AUTHORITY_ID,
        allowed_stages=("fetch", "asr"),
        processing_enabled=True,
        monthly_budget_cny_minor=10_000,
        per_run_budget_cny_minor=1_000,
        provider_ready_targets=("transcript",),
    )


def _asr_config() -> PodcastAsrFetchConfig:
    return PodcastAsrFetchConfig(
        public_base_url=PUBLIC_BASE_URL,
        signing_secret=SECRET,
        url_ttl_seconds=900,
        clock_skew_seconds=30,
        min_remaining_seconds=300,
    )


def _request_target(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.path}?{parsed.query}"


def _setup_authorized_route(
    monkeypatch,
    tmp_path,
    *,
    source_ttl_seconds: int = 0,
    provider_deadline_seconds: int = 900,
):
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    if source_ttl_seconds:
        store.source_audio_ttl_seconds = source_ttl_seconds
    podcast = _podcast_config()
    asr = _asr_config()
    aliyun_isi = replace(
        app_module.settings.aliyun_isi,
        access_key_id="test-ak-id",
        access_key_secret="test-ak-secret",
        app_key="test-app-key",
        request_timeout_seconds=5,
        asr_quota_scope="aliyun-isi-asr-public-route-test",
        asr_quota_timezone="Asia/Shanghai",
        asr_daily_audio_seconds_limit=7_200,
        asr_entitlement_ends_at="2099-01-01T00:00:00+08:00",
        asr_provider_deadline_seconds=provider_deadline_seconds,
        asr_price_cny_minor_per_hour=3_600,
        asr_pricing_revision="test-v1",
    )
    runtime_settings = replace(
        app_module.settings,
        podcast=podcast,
        podcast_asr_fetch=asr,
        aliyun_isi=aliyun_isi,
    )
    monkeypatch.setattr(app_module, "settings", runtime_settings)
    monkeypatch.setattr(config, "settings", runtime_settings)

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        imported = _import(client, kind="source_audio")
        assert imported.status_code == 201, imported.text
        artifact = imported.json()

    current = dt.datetime.now(dt.timezone.utc)
    period = current.astimezone(ZoneInfo(podcast.budget_timezone)).strftime("%Y-%m")
    policy = PodcastStagePolicy(podcast, aliyun_isi)
    with Session(sink.engine) as session:
        process = enqueue_processing(
            session,
            episode_id="episode-1",
            stage="asr",
            input_fingerprint=artifact["content_hash"],
            pipeline_version="asr-route-v1",
            policy_version="rights-v1",
            requested_target="transcript",
            idempotency_key="asr-public-route-run",
            estimated_cost_minor=100,
            input_artifact_id=artifact["id"],
            input_artifact_kind="source_audio",
            input_content_hash=artifact["content_hash"],
            input_language="zh-CN",
            budget_scope=podcast.budget_scope,
            budget_period=period,
            budget_limit_minor=podcast.monthly_budget_cny_minor,
            per_run_budget_minor=podcast.per_run_budget_cny_minor,
            policy=policy,
            now=current,
        )
    with Session(sink.engine) as session:
        claim = claim_next_processing(
            session,
            worker_id="asr-route-worker",
            lease_seconds=600,
            policy=policy,
            now=current,
        )
        assert claim is not None
    with Session(sink.engine) as session:
        attempt = begin_stage_attempt(
            session,
            claim,
            input_hash=artifact["content_hash"],
            settings_fingerprint="f" * 64,
            provider_name="aliyun-isi",
            model_name="filetrans",
            provider_revision="4.0",
            provider_request_key="asr-public-route-request",
            execution_kind="provider",
            estimated_cost_minor=100,
            budget_scope=podcast.budget_scope,
            budget_period=period,
            budget_limit_minor=podcast.monthly_budget_cny_minor,
            reservation_idempotency_key="asr-public-route-reservation",
            provider_usage_plan=asr_usage_plan(
                aliyun_isi,
                audio_duration_ms=100_000,
                now=current,
            ),
            policy=policy,
            now=current,
        )

    signed = PodcastAsrFetchUrlSigner(asr, authority_id=AUTHORITY_ID).issue(
        processing_id=process.id,
        artifact_id=artifact["id"],
        content_sha256=artifact["content_hash"],
    )
    return {
        "app": app_module,
        "sink": sink,
        "store": store,
        "podcast": podcast,
        "asr": asr,
        "aliyun_isi": aliyun_isi,
        "artifact": artifact,
        "processing_id": process.id,
        "attempt_id": attempt.id,
        "url": _request_target(signed.url),
    }


def _assert_public_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers


def test_public_asr_get_head_and_range_end_to_end(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    digest = case["artifact"]["content_hash"]
    with TestClient(case["app"].app) as client:
        full = client.get(case["url"])
        assert full.status_code == 200
        assert full.content == WAV
        assert full.headers["content-length"] == str(len(WAV))
        assert full.headers["accept-ranges"] == "bytes"
        assert full.headers["etag"] == f'"{digest}"'
        _assert_public_headers(full)

        head = client.head(case["url"], headers={"Range": "bytes=4-11"})
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(len(WAV))
        _assert_public_headers(head)

        partial = client.get(case["url"], headers={"Range": "bytes=4-11"})
        assert partial.status_code == 206
        assert partial.content == WAV[4:12]
        assert partial.headers["content-range"] == f"bytes 4-11/{len(WAV)}"
        assert partial.headers["content-length"] == "8"
        _assert_public_headers(partial)

        unsatisfied = client.get(case["url"], headers={"Range": f"bytes={len(WAV)}-"})
        assert unsatisfied.status_code == 416
        assert unsatisfied.headers["content-range"] == f"bytes */{len(WAV)}"
        _assert_public_headers(unsatisfied)


def test_provider_fetch_accepts_capability_bounded_by_shorter_artifact_lifetime(
    monkeypatch, tmp_path
):
    case = _setup_authorized_route(
        monkeypatch,
        tmp_path,
        source_ttl_seconds=400,
        provider_deadline_seconds=300,
    )
    with Session(case["sink"].engine) as session:
        artifact = session.get(PodcastArtifactRecord, case["artifact"]["id"])
        attempt = session.get(PodcastStageAttemptRecord, case["attempt_id"])
        assert artifact is not None and artifact.expires_at
        assert attempt is not None
        artifact_expiry = dt.datetime.fromisoformat(
            artifact.expires_at.replace("Z", "+00:00")
        )
    signer = PodcastAsrFetchUrlSigner(case["asr"], authority_id=AUTHORITY_ID)
    submitted_url = ""
    fetched_content = b""

    class ProviderFetchClient:
        def submit(self, file_url):
            nonlocal submitted_url, fetched_content
            submitted_url = file_url
            with TestClient(case["app"].app) as client:
                response = client.get(_request_target(file_url))
            assert response.status_code == 200
            _assert_public_headers(response)
            fetched_content = response.content
            return AsrSubmission("provider-fetch-task", 21050000)

        def poll(self, _task_id):
            raise AssertionError("provider fetch test does not poll")

        def close(self):
            pass

    now = dt.datetime.now(dt.timezone.utc)
    adapter = AliyunIsiAsrAdapter(
        case["aliyun_isi"],
        signer=signer,
        client=ProviderFetchClient(),
        clock=lambda: now,
        transcript_language="zh-CN",
    )
    artifact_ref = ArtifactRef(
        artifact_id=artifact.id,
        episode_id=artifact.episode_id,
        kind=artifact.kind,
        content_hash=artifact.content_hash,
        size_bytes=artifact.size_bytes,
        mime_type=artifact.mime,
    )
    plan = adapter.plan(
        artifact_ref,
        audio_duration_ms=100_000,
        now=now,
    )
    outcome = adapter.submit(
        StageContext(
            processing_id=case["processing_id"],
            episode_id=artifact.episode_id,
            target="transcript",
            stage="asr",
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token,
            input_artifact=artifact_ref,
            identity=plan.identity,
            plan=plan.stage,
            input_expires_at=artifact_expiry,
        ),
        provider_request_key="provider-fetch-capability-test",
    )
    assert isinstance(outcome, Accepted)
    assert fetched_content == WAV

    split = urlsplit(submitted_url)
    claims = signer.verify(
        method="GET",
        canonical_path=split.path,
        raw_query=split.query,
        now=int(now.timestamp()),
    )
    assert int(now.timestamp()) + 300 <= claims.expires_at
    assert claims.expires_at <= int(artifact_expiry.timestamp())
    assert claims.expires_at < int(now.timestamp()) + case["asr"].url_ttl_seconds


def test_public_asr_get_survives_one_secret_rotation_grace(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    new_secret = "rotated-asr-public-route-secret-000000000"
    rotated_settings = replace(
        case["app"].settings,
        podcast_asr_fetch=replace(case["asr"], previous_signing_secret=""),
    )
    monkeypatch.setattr(case["app"], "settings", rotated_settings)
    monkeypatch.setattr(config, "settings", rotated_settings)
    with Session(case["sink"].engine) as session:
        credentials.save_updates(
            session,
            credentials.PODCAST_ASR_FETCH_NAMESPACE,
            {
                "signing_secret": new_secret,
                "previous_signing_secret": SECRET,
            },
        )
        rotated_fetch = podcast_asr_fetch_signing.resolve_config(session)
        assert (
            podcast_asr_fetch_signing.field_sources(session)["previous_signing_secret"]
            == "runtime_kv"
        )
        assert rotated_fetch.signing_secret == new_secret
        assert rotated_fetch.previous_signing_secret == SECRET
    new_signed = PodcastAsrFetchUrlSigner(
        rotated_fetch,
        authority_id=AUTHORITY_ID,
    ).issue(
        processing_id=case["processing_id"],
        artifact_id=case["artifact"]["id"],
        content_sha256=case["artifact"]["content_hash"],
    )
    new_url = _request_target(new_signed.url)

    with TestClient(case["app"].app) as client:
        assert client.get(case["url"]).content == WAV
        assert client.get(new_url).content == WAV
        clear_path = "/api/admin/podcast-asr-fetch/previous-signing-secret"
        assert client.delete(clear_path).status_code in {401, 403}
        assert client.get(case["url"]).content == WAV
        _login(client, "admin", "admin")
        cleared = client.delete(clear_path)
        assert cleared.status_code == 200
        assert cleared.json() == {
            "previous_signing_secret_set": False,
            "previous_signing_secret_source": "default",
        }
        assert client.get(case["url"]).status_code == 404
        assert client.get(new_url).content == WAV

    with Session(case["sink"].engine) as session:
        resolved = podcast_asr_fetch_signing.resolve_config(session)
        sources = podcast_asr_fetch_signing.field_sources(session)
        sanitized = credentials.sanitize_values(
            credentials.PODCAST_ASR_FETCH_NAMESPACE,
            resolved.__dict__,
        )
        assert resolved.previous_signing_secret == ""
        assert resolved.signing_secret == new_secret
        assert sources["previous_signing_secret"] == "default"
        assert sources["signing_secret"] == "runtime_kv"
        assert sanitized["previous_signing_secret_set"] is False
        audit = session.exec(
            select(AdminAuditRecord).where(
                AdminAuditRecord.path
                == "/api/admin/podcast-asr-fetch/previous-signing-secret"
            )
        ).one()
        assert audit.username == "admin"
        assert audit.summary == "结束 Podcast ASR 签名密钥轮换宽限期"


def test_public_asr_rejects_tampering_before_range_can_leak_size(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    replacement = "A" if case["url"][-1] != "A" else "B"
    tampered = case["url"][:-1] + replacement
    with TestClient(case["app"].app) as client:
        response = client.get(tampered, headers={"Range": "bytes=999999-"})
        assert response.status_code == 404
        assert "content-range" not in response.headers
        assert "accept-ranges" not in response.headers
        assert response.headers.get("content-length") != str(len(WAV))
        _assert_public_headers(response)

        missing = client.get(
            "/api/public/podcast-asr/source-audio",
            headers={"Range": "bytes=0-0"},
        )
        assert missing.status_code == 404
        assert "content-range" not in missing.headers
        _assert_public_headers(missing)


def test_public_asr_invalid_signature_never_acquires_sqlite_writer_lock(
    monkeypatch, tmp_path
):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    replacement = "A" if case["url"][-1] != "A" else "B"
    tampered = case["url"][:-1] + replacement
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(
        case["sink"].engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        with TestClient(case["app"].app) as client:
            # Startup reconciliation legitimately takes a writer lock; this
            # assertion is scoped to the unauthenticated route request only.
            statements.clear()
            response = client.get(tampered)
    finally:
        event.remove(
            case["sink"].engine,
            "before_cursor_execute",
            record_statement,
        )

    assert response.status_code == 404
    assert not any(statement.startswith("BEGIN IMMEDIATE") for statement in statements)


def test_public_asr_capability_must_not_outlive_source_artifact(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path, source_ttl_seconds=10 * 60)

    # The configured grant lasts 15 minutes, so it must be rejected even while
    # the source artifact itself still has ten minutes of valid retention.
    with TestClient(case["app"].app) as client:
        response = client.get(case["url"])

    assert response.status_code == 404
    _assert_public_headers(response)


def test_public_asr_rejects_internal_installation_and_digest_mismatch(
    monkeypatch, tmp_path
):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    wrong_digest = PodcastAsrFetchUrlSigner(
        case["asr"], authority_id=AUTHORITY_ID
    ).issue(
        processing_id=case["processing_id"],
        artifact_id=case["artifact"]["id"],
        content_sha256="0" * 64,
    )
    with TestClient(case["app"].app) as client:
        mismatch = client.get(_request_target(wrong_digest.url))
        assert mismatch.status_code == 404
        _assert_public_headers(mismatch)

        internal = PodcastConfig(
            installation="internal",
            authority_id=AUTHORITY_ID,
            allowed_stages=(),
            processing_enabled=False,
        )
        monkeypatch.setattr(
            case["app"],
            "settings",
            replace(case["app"].settings, podcast=internal),
        )
        denied = client.get(case["url"])
        assert denied.status_code == 404
        _assert_public_headers(denied)


def test_public_asr_rejects_terminal_or_unbound_processing(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    with Session(case["sink"].engine) as session:
        process = session.get(PodcastProcessingRecord, case["processing_id"])
        assert process is not None
        process.processing_status = "failed"
        process.lease_owner = None
        process.lease_token = None
        process.lease_expires_at = None
        process.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        session.add(process)
        session.commit()
    with TestClient(case["app"].app) as client:
        terminal = client.get(case["url"])
        assert terminal.status_code == 404
        _assert_public_headers(terminal)

    with Session(case["sink"].engine) as session:
        process = session.get(PodcastProcessingRecord, case["processing_id"])
        attempt = session.get(PodcastStageAttemptRecord, case["attempt_id"])
        assert process is not None and attempt is not None
        process.processing_status = "retry_wait"
        process.next_retry_at = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
        ).isoformat()
        process.finished_at = None
        attempt.submission_state = "failed_retryable"
        attempt.completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        session.add(process)
        session.add(attempt)
        session.commit()
    with TestClient(case["app"].app) as client:
        no_active_attempt = client.get(case["url"])
        assert no_active_attempt.status_code == 404
        _assert_public_headers(no_active_attempt)


def test_public_asr_rejects_expired_capability_artifact_and_corrupt_cas(
    monkeypatch, tmp_path
):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    old = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 2_000
    expired_url = PodcastAsrFetchUrlSigner(
        case["asr"], authority_id=AUTHORITY_ID
    ).issue(
        processing_id=case["processing_id"],
        artifact_id=case["artifact"]["id"],
        content_sha256=case["artifact"]["content_hash"],
        now=old,
    )
    with TestClient(case["app"].app) as client:
        expired_claim = client.get(_request_target(expired_url.url))
        assert expired_claim.status_code == 404
        _assert_public_headers(expired_claim)

        with Session(case["sink"].engine) as session:
            artifact = session.get(PodcastArtifactRecord, case["artifact"]["id"])
            assert artifact is not None
            path = case["store"].file_path_for(artifact)
        path.write_bytes(WAV[:-1] + b"X")
        corrupt = client.get(case["url"])
        assert corrupt.status_code == 404
        _assert_public_headers(corrupt)


def test_public_asr_rejects_expired_source_artifact(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path, source_ttl_seconds=1)
    time.sleep(1.05)
    with TestClient(case["app"].app) as client:
        response = client.get(case["url"])
        assert response.status_code == 404
        _assert_public_headers(response)


def test_public_asr_rejects_processing_identity_rebinding(monkeypatch, tmp_path):
    case = _setup_authorized_route(monkeypatch, tmp_path)
    rebound = PodcastAsrFetchUrlSigner(case["asr"], authority_id=AUTHORITY_ID).issue(
        processing_id="another-processing",
        artifact_id=case["artifact"]["id"],
        content_sha256=case["artifact"]["content_hash"],
    )
    with TestClient(case["app"].app) as client:
        response = client.get(_request_target(rebound.url))
        assert response.status_code == 404
        _assert_public_headers(response)
