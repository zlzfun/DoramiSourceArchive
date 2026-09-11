"""Focused state-machine tests for durable Podcast processing."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event

import pytest
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastBudgetReservationRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingCommandRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
import config as config_module  # noqa: E402
from config import AliyunIsiConfig, PodcastConfig  # noqa: E402
from services import podcast_processing as podcast_processing_service  # noqa: E402
from services.podcast_processing import (  # noqa: E402
    PodcastBudgetExceeded,
    PodcastEligibilityDenied,
    PodcastLeaseLost,
    PodcastProcessingConflict,
    PodcastProviderQuotaExceeded,
    PodcastProviderReconciliationRequired,
    _evaluate_external_asr_export,
    authorize_provider_call,
    begin_stage_attempt,
    claim_next_processing,
    commit_stage_attempt,
    deterministic_input_fingerprint,
    enqueue_processing,
    fail_stage_attempt,
    heartbeat_processing,
    mark_provider_submission,
    park_for_reconciliation,
    reconcile_parked_provider_request,
    reconcile_provider_request,
    schedule_stage_poll,
    settle_attempt_cost,
)
from services.podcast_processing_admin import (  # noqa: E402
    PodcastAdminError,
    PodcastProcessingProviderRegistry,
    retry_processing,
)
from services.podcast_artifacts import PodcastArtifactStore  # noqa: E402
from services.aliyun_isi_usage import asr_usage_plan, tts_usage_plan  # noqa: E402
from services.podcast_stage_policy import PodcastStagePolicy  # noqa: E402
from services.podcast_worker_contracts import (  # noqa: E402
    NormalizedUsage,
    ProviderUsagePlan,
    ProviderUsageUnit,
)
from services.podcast_normalized_transcripts import (  # noqa: E402
    NormalizedTranscriptConflict,
    NormalizedTranscriptError,
    canonical_normalized_transcript,
    materialize_normalized_transcript,
)
from services.podcast_text_reader import read_episode_texts  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 5, 8, 0, tzinfo=dt.timezone.utc)


class RecordingPolicy:
    def __init__(self, allowed: set[str] | None = None):
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []
        self.config = PodcastConfig(
            installation="external",
            authority_id="test-external",
            allowed_stages=(
                "fetch",
                "asr",
                "translate",
                "analyze",
                "digest",
                "script",
            ),
            processing_enabled=True,
            monthly_budget_cny_minor=100,
            per_run_budget_cny_minor=100,
            provider_ready_targets=("transcript", "digest_blog"),
        )

    def require_stage(self, stage: str, *, boundary: str) -> None:
        self.calls.append((stage, boundary))
        if self.allowed is not None and stage not in self.allowed:
            raise PermissionError(f"denied {stage} at {boundary}")


@pytest.fixture()
def engine(tmp_path):
    storage = DatabaseStorage(f"sqlite:///{tmp_path / 'podcast-processing.db'}")
    stamp = NOW.isoformat(timespec="microseconds")
    with Session(storage.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-source",
                name="Podcast",
                source_type="podcast",
                url="https://example.test/feed.xml",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.add(
            ArticleRecord(
                id="episode-1",
                title="Episode",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://example.test/episodes/1",
                publish_date=stamp,
                fetched_date=stamp,
                content="show notes",
            )
        )
        session.add(
            ArticleRecord(
                id="episode-2",
                title="Episode 2",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://example.test/episodes/2",
                publish_date=stamp,
                fetched_date=stamp,
                content="show notes 2",
            )
        )
        session.add(
            PodcastSourceMediaSnapshotRecord(
                id="source-media-1",
                episode_id="episode-1",
                content_hash="a" * 64,
                mime="audio/mpeg",
                size_bytes=1024,
                duration_seconds=60,
                locator_hash="1" * 64,
                created_at=stamp,
            )
        )
        session.add(
            PodcastSourceMediaSnapshotRecord(
                id="source-media-2",
                episode_id="episode-2",
                content_hash="b" * 64,
                mime="audio/mpeg",
                size_bytes=1024,
                duration_seconds=60,
                locator_hash="2" * 64,
                created_at=stamp,
            )
        )
        session.commit()
    yield storage.engine
    storage.engine.dispose()


def _enqueue(session: Session, policy: RecordingPolicy, **overrides):
    values = {
        "episode_id": "episode-1",
        "stage": "asr",
        "input_fingerprint": deterministic_input_fingerprint(
            {"audio_sha256": "abc", "normalization": {"mono": True}}
        ),
        "pipeline_version": "podcast-pipeline-v1",
        "policy_version": "eligibility-v1",
        "requested_target": "digest_blog",
        "idempotency_key": "process:episode-1:v1",
        "estimated_cost_minor": 80,
        "input_artifact_id": "source-media-1",
        "input_artifact_kind": "source_media_snapshot",
        "input_content_hash": "a" * 64,
        "input_language": "und",
        "budget_scope": "podcast-paid-processing",
        "budget_period": "2026-09",
        "budget_limit_minor": 100,
        "per_run_budget_minor": 100,
        "policy": policy,
        "now": NOW,
    }
    values.update(overrides)
    if values["episode_id"] == "episode-2" and "input_artifact_id" not in overrides:
        values["input_artifact_id"] = "source-media-2"
        values["input_content_hash"] = "b" * 64
    return enqueue_processing(session, **values)


def _attempt(
    session: Session,
    claim,
    policy: RecordingPolicy,
    *,
    suffix: str = "1",
    estimate: int = 80,
    cap: int = 100,
    input_hash: str | None = None,
    execution_kind: str = "provider",
    settings_fingerprint: str = "f" * 64,
    provider_usage_plan: ProviderUsagePlan | None = None,
):
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    assert process is not None
    previous_success = session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == claim.processing_id,
            PodcastStageAttemptRecord.submission_state == "succeeded",
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()
    expected_input_hash = (
        previous_success.output_hash
        if previous_success is not None
        else process.input_content_hash
    )
    # begin_stage_attempt deliberately owns the budget transaction and requires
    # a clean Session; release this read-only helper transaction first.
    session.rollback()
    return begin_stage_attempt(
        session,
        claim,
        input_hash=input_hash or expected_input_hash,
        settings_fingerprint=settings_fingerprint,
        provider_name="provider-spy",
        model_name="asr-model",
        provider_revision="2026-09",
        provider_request_key=f"provider-request:{suffix}",
        execution_kind=execution_kind,
        estimated_cost_minor=estimate,
        budget_scope="podcast-paid-processing",
        budget_period="2026-09",
        budget_limit_minor=cap,
        reservation_idempotency_key=f"reservation:{suffix}",
        provider_usage_plan=provider_usage_plan,
        policy=policy,
        now=NOW + dt.timedelta(seconds=int(suffix) if suffix.isdigit() else 1),
    )


def _usage_plan(
    *,
    reserved_units: int,
    limit_units: int = 100,
    unit_price_cny_minor: int = 0,
) -> ProviderUsagePlan:
    return ProviderUsagePlan(
        quota_scope="aliyun-isi-asr-trial",
        quota_period="2026-09-05",
        unit=ProviderUsageUnit.AUDIO_SECONDS,
        window_start_at=NOW - dt.timedelta(hours=8),
        window_end_at=NOW + dt.timedelta(hours=16),
        limit_units=limit_units,
        reserved_units=reserved_units,
        unit_price_cny_minor=unit_price_cny_minor,
        price_unit_count=3_600,
        pricing_revision="trial-2026-09",
        deadline_seconds=3_600,
    )


def _add_local_narration(
    session: Session,
    *,
    episode_id: str,
    characters: int,
) -> tuple[str, str]:
    text_value = "文" * characters
    artifact_id = f"remote-script-{episode_id}"
    content_hash = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
    stamp = NOW.isoformat(timespec="microseconds")
    session.add(
        PodcastTextArtifactRecord(
            id=artifact_id,
            episode_id=episode_id,
            kind="narration_script_zh",
            version=1,
            content_hash=content_hash,
            inline_text=text_value,
            language="zh-CN",
            authority_id="",
            provenance_json='{"pipeline":"test"}',
            created_at=stamp,
        )
    )
    session.add(
        PodcastTextPublicationRecord(
            identity=f"{episode_id}:narration_script_zh",
            episode_id=episode_id,
            kind="narration_script_zh",
            artifact_id=artifact_id,
            status="published",
            authority_id="",
            published_at=stamp,
            updated_at=stamp,
        )
    )
    session.commit()
    return artifact_id, content_hash


def _mark_source_credentialed(
    session: Session, *, secret: str = "private-feed-token"
) -> None:
    source = session.get(SourceConfigRecord, "podcast-source")
    assert source is not None
    source.url = f"https://feeds.example.test/private.xml?token={secret}"
    source.params_json = json.dumps({"credentialed_private": True})
    session.add(source)
    session.commit()


NORMALIZED_TRANSCRIPT = {
    "audio_duration_ms": 60_000,
    "text": "你好，世界。",
    "language": "zh-CN",
    "segments": [
        {
            "text": "你好，世界。",
            "start_ms": 100,
            "end_ms": 900,
            "channel": 0,
            "words": [
                {
                    "text": "你好",
                    "start_ms": 100,
                    "end_ms": 400,
                    "channel": 0,
                    "confidence": 0.99,
                },
                {
                    "text": "世界",
                    "start_ms": 500,
                    "end_ms": 900,
                    "channel": 0,
                    "confidence": 1,
                },
            ],
        }
    ],
}


def _submitted_asr_attempt(session: Session, policy: RecordingPolicy):
    _enqueue(session, policy, requested_target="transcript")
    claim = claim_next_processing(
        session,
        worker_id="asr-materializer",
        lease_seconds=60,
        policy=policy,
        now=NOW,
    )
    assert claim is not None
    attempt = _attempt(session, claim, policy)
    attempt = mark_provider_submission(
        session,
        claim,
        attempt_id=attempt.id,
        provider_task_id="provider-task-normalized",
        policy=policy,
        now=NOW + dt.timedelta(seconds=2),
    )
    return claim, attempt


def _materialize_asr_attempt(
    session: Session,
    claim,
    attempt,
    policy: RecordingPolicy,
    *,
    now: dt.datetime,
):
    return materialize_normalized_transcript(
        session,
        claim,
        attempt_id=attempt.id,
        document=NORMALIZED_TRANSCRIPT,
        policy=policy,
        now=now,
    )


def test_normalized_transcript_canonical_json_preserves_timing_channels_and_words():
    canonical = canonical_normalized_transcript(NORMALIZED_TRANSCRIPT)
    parsed = json.loads(canonical)
    assert parsed == {
        "audio_duration_ms": 60_000,
        "format_version": "dorami-normalized-transcript-v1",
        "language": "zh-cn",
        "segments": [
            {
                "channel": 0,
                "end_ms": 900,
                "start_ms": 100,
                "text": "你好，世界。",
                "words": [
                    {
                        "channel": 0,
                        "confidence": 0.99,
                        "end_ms": 400,
                        "start_ms": 100,
                        "text": "你好",
                    },
                    {
                        "channel": 0,
                        "confidence": 1.0,
                        "end_ms": 900,
                        "start_ms": 500,
                        "text": "世界",
                    },
                ],
            }
        ],
        "text": "你好，世界。",
    }
    reordered = {
        "segments": NORMALIZED_TRANSCRIPT["segments"],
        "language": "zh-CN",
        "text": "你好，世界。",
        "audio_duration_ms": 60_000,
    }
    assert canonical_normalized_transcript(reordered) == canonical
    malformed = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
    malformed["segments"][0]["words"][0]["end_ms"] = 1000
    with pytest.raises(NormalizedTranscriptError, match="word timecodes"):
        canonical_normalized_transcript(malformed)
    empty = {
        "audio_duration_ms": 1_000,
        "text": "有文本但没有证据",
        "language": "zh-CN",
        "segments": [],
    }
    with pytest.raises(NormalizedTranscriptError, match="ASR evidence"):
        canonical_normalized_transcript(empty)
    zero_length = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
    zero_length["segments"][0]["end_ms"] = 100
    with pytest.raises(NormalizedTranscriptError, match="segment timecodes"):
        canonical_normalized_transcript(zero_length)
    beyond_audio = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
    beyond_audio["audio_duration_ms"] = 899
    with pytest.raises(NormalizedTranscriptError, match="segment timecodes"):
        canonical_normalized_transcript(beyond_audio)
    overlapping_channels = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
    second_channel = json.loads(json.dumps(overlapping_channels["segments"][0]))
    second_channel["channel"] = 1
    for word in second_channel["words"]:
        word["channel"] = 1
    overlapping_channels["segments"].append(second_channel)
    assert (
        len(
            json.loads(canonical_normalized_transcript(overlapping_channels))[
                "segments"
            ]
        )
        == 2
    )
    same_channel_overlap = json.loads(json.dumps(overlapping_channels))
    same_channel_overlap["segments"][1]["channel"] = 0
    for word in same_channel_overlap["segments"][1]["words"]:
        word["channel"] = 0
    with pytest.raises(NormalizedTranscriptError, match="segment timecodes"):
        canonical_normalized_transcript(same_channel_overlap)


def test_begin_stage_attempt_requires_real_settings_fingerprint(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="settings-fingerprint",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        with pytest.raises(ValueError, match="settings_fingerprint must be a SHA-256"):
            _attempt(
                session,
                claim,
                policy,
                settings_fingerprint="not-a-fingerprint",
            )


def test_materialize_normalized_transcript_binds_attempt_and_exact_replay(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        artifact = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert session.in_transaction() is False
        replay = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert replay.id == artifact.id
        assert artifact.id == (
            "podcast-normalized-"
            + hashlib.sha256(attempt.id.encode("utf-8")).hexdigest()[:32]
        )
        assert session.in_transaction() is False

        persisted_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        persisted_artifact = session.get(PodcastTextArtifactRecord, artifact.id)
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:normalized_transcript"
        )
        assert persisted_attempt is not None and persisted_artifact is not None
        assert publication is not None
        assert publication.artifact_id == artifact.id
        assert publication.status == "published"
        assert publication.authority_id == ""
        assert persisted_attempt.settings_fingerprint == "f" * 64
        assert persisted_attempt.output_hash == artifact.content_hash
        assert persisted_attempt.output_artifact_id == artifact.id
        assert persisted_attempt.output_artifact_kind == "normalized_transcript"
        assert persisted_artifact.processing_id == claim.processing_id
        assert persisted_artifact.producing_attempt_id == attempt.id
        assert persisted_artifact.source_artifact_id == "source-media-1"
        assert persisted_artifact.source_content_hash == "a" * 64
        assert "provider-task-normalized" not in persisted_artifact.provenance_json
        assert (
            json.loads(persisted_artifact.provenance_json)["provider_task_id_ref"]
            == hashlib.sha256(b"provider-task-normalized").hexdigest()[:12]
        )
        session.rollback()
        reader_result = read_episode_texts(
            session,
            episode_id="episode-1",
            username="admin",
            config=policy.config,
            cursor_secret="test-reader-cursor-secret",
        )
        normalized_item = next(
            item
            for item in reader_result["items"]
            if item["kind"] == "normalized_transcript"
        )
        assert normalized_item["text"] == NORMALIZED_TRANSCRIPT["text"]
        session.rollback()
        with pytest.raises(IntegrityError):
            session.exec(
                text(
                    "UPDATE podcast_stage_attempts SET output_artifact_id='other-output' "
                    "WHERE id=:attempt_id"
                ).bindparams(attempt_id=attempt.id)
            )
            session.commit()
        session.rollback()


def test_materialize_normalized_transcript_replay_repairs_missing_publication(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        artifact = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:normalized_transcript"
        )
        assert publication is not None
        session.delete(publication)
        session.commit()

        replay = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )

        repaired = session.get(
            PodcastTextPublicationRecord, "episode-1:normalized_transcript"
        )
        assert replay.id == artifact.id
        assert repaired is not None
        assert repaired.artifact_id == artifact.id
        assert repaired.status == "published"


def test_materialize_normalized_transcript_refuses_remote_authority_slot(engine):
    policy = RecordingPolicy({"asr"})
    remote_text = canonical_normalized_transcript(NORMALIZED_TRANSCRIPT)
    remote_hash = hashlib.sha256(remote_text.encode("utf-8")).hexdigest()
    stamp = NOW.isoformat(timespec="microseconds")
    with Session(engine) as session:
        session.add(
            PodcastTextArtifactRecord(
                id="remote-normalized-transcript",
                episode_id="episode-1",
                kind="normalized_transcript",
                version=1,
                content_hash=remote_hash,
                inline_text=remote_text,
                language="zh-cn",
                authority_id="remote-podcast-authority",
                provenance_json='{"source":"archive-sync"}',
                created_at=stamp,
            )
        )
        session.add(
            PodcastTextPublicationRecord(
                identity="episode-1:normalized_transcript",
                episode_id="episode-1",
                kind="normalized_transcript",
                artifact_id="remote-normalized-transcript",
                status="published",
                authority_id="remote-podcast-authority",
                published_at=stamp,
                updated_at=stamp,
            )
        )
        session.commit()
        claim, attempt = _submitted_asr_attempt(session, policy)

        with pytest.raises(NormalizedTranscriptConflict, match="remote authority"):
            materialize_normalized_transcript(
                session,
                claim,
                attempt_id=attempt.id,
                document=NORMALIZED_TRANSCRIPT,
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )

        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:normalized_transcript"
        )
        assert publication is not None
        assert publication.artifact_id == "remote-normalized-transcript"
        assert session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.producing_attempt_id == attempt.id
            )
        ).first() is None


def test_materialized_transcript_exact_replay_survives_snapshot_reuse(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        session.add(
            PodcastSourceMediaSnapshotRecord(
                id="source-media-replay",
                episode_id="episode-1",
                content_hash="e" * 64,
                mime="audio/mpeg",
                size_bytes=1024,
                duration_seconds=60,
                locator_hash="e" * 64,
                created_at=NOW.isoformat(timespec="microseconds"),
            )
        )
        session.commit()
        _enqueue(
            session,
            policy,
            requested_target="transcript",
            input_fingerprint=deterministic_input_fingerprint("short-ttl"),
            idempotency_key="process:short-ttl",
            input_artifact_id="source-media-replay",
            input_content_hash="e" * 64,
        )
        claim = claim_next_processing(
            session,
            worker_id="ttl-replay",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-short-ttl",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        artifact = _materialize_asr_attempt(
            session,
            claim,
            attempt,
            policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        replay = _materialize_asr_attempt(
            session,
            claim,
            attempt,
            policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert replay.id == artifact.id


def test_materialize_normalized_transcript_rejects_changed_replay_and_revocation(
    engine,
):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        changed = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
        changed["text"] = "不同结果"
        with pytest.raises(NormalizedTranscriptConflict, match="replay conflicts"):
            materialize_normalized_transcript(
                session,
                claim,
                attempt_id=attempt.id,
                document=changed,
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )


def test_materialize_normalized_transcript_rejects_source_duration_mismatch(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        truncated = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
        truncated["audio_duration_ms"] = 1_000
        with pytest.raises(
            NormalizedTranscriptConflict, match="duration does not match"
        ):
            materialize_normalized_transcript(
                session,
                claim,
                attempt_id=attempt.id,
                document=truncated,
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )
        assert (
            session.exec(
                select(PodcastTextArtifactRecord).where(
                    PodcastTextArtifactRecord.producing_attempt_id == attempt.id
                )
            ).first()
            is None
        )


def test_materialize_normalized_transcript_accepts_configured_duration_boundary(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        boundary = json.loads(json.dumps(NORMALIZED_TRANSCRIPT))
        boundary["audio_duration_ms"] = 55_000
        artifact = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt.id,
            document=boundary,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert artifact.producing_attempt_id == attempt.id


def test_asr_commit_requires_materialized_normalized_transcript(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        claim, attempt = _submitted_asr_attempt(session, policy)
        settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="charge:missing-transcript",
            actual_cost_minor=0,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        with pytest.raises(
            PodcastProcessingConflict, match="bound normalized transcript"
        ):
            commit_stage_attempt(
                session,
                claim,
                attempt_id=attempt.id,
                output_hash="c" * 64,
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )
        persisted = session.get(PodcastProcessingRecord, claim.processing_id)
        assert persisted is not None and persisted.processing_status == "running"


def test_materialize_old_submitted_attempt_under_new_poll_fence(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        old_claim, attempt = _submitted_asr_attempt(session, policy)
        schedule_stage_poll(
            session,
            old_claim,
            attempt_id=attempt.id,
            retry_at=NOW + dt.timedelta(seconds=5),
            poll_performed=True,
            provider_deadline_at=NOW + dt.timedelta(seconds=50),
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        current_claim = claim_next_processing(
            session,
            worker_id="asr-poller-2",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=6),
        )
        assert current_claim is not None
        assert current_claim.fencing_token == old_claim.fencing_token + 1
        assert current_claim.lease_token != old_claim.lease_token

        with pytest.raises(PodcastLeaseLost):
            materialize_normalized_transcript(
                session,
                old_claim,
                attempt_id=attempt.id,
                document=NORMALIZED_TRANSCRIPT,
                policy=policy,
                now=NOW + dt.timedelta(seconds=7),
            )
        artifact = materialize_normalized_transcript(
            session,
            current_claim,
            attempt_id=attempt.id,
            document=NORMALIZED_TRANSCRIPT,
            policy=policy,
            now=NOW + dt.timedelta(seconds=7),
        )
        assert artifact.producing_attempt_id == attempt.id


def test_fingerprint_and_enqueue_are_deterministic_and_idempotent(engine):
    policy = RecordingPolicy({"asr", "translate"})
    assert deterministic_input_fingerprint(
        {"b": 2, "a": 1}
    ) == deterministic_input_fingerprint({"a": 1, "b": 2})
    with Session(engine) as session:
        first = _enqueue(session, policy)
        repeated = _enqueue(session, policy)
        effective_reuse = _enqueue(session, policy, idempotency_key="another-request")
        assert first.id == repeated.id == effective_reuse.id
        with pytest.raises(PodcastProcessingConflict, match="different processing run"):
            _enqueue(
                session,
                policy,
                input_fingerprint=deterministic_input_fingerprint(
                    {"audio_sha256": "changed"}
                ),
            )
    assert policy.calls == [
        ("asr", "enqueue"),
        ("asr", "enqueue"),
        ("asr", "enqueue"),
        ("asr", "enqueue"),
    ]


def test_external_asr_export_gate_has_fail_closed_source_semantics(engine):
    with Session(engine) as session:
        platform_episode = ArticleRecord(
            id="platform-episode",
            title="Platform",
            content_type="podcast_episode",
            source_id="platform-registry-podcast",
            source_url="https://public.example.test/episode",
            publish_date=NOW.isoformat(timespec="microseconds"),
            fetched_date=NOW.isoformat(timespec="microseconds"),
        )
        orphan_episode = ArticleRecord(
            id="orphan-episode",
            title="Orphan",
            content_type="podcast_episode",
            source_id="user_rss_orphan",
            source_url="https://private.example.test/episode",
            publish_date=NOW.isoformat(timespec="microseconds"),
            fetched_date=NOW.isoformat(timespec="microseconds"),
        )
        assert _evaluate_external_asr_export(
            session,
            platform_episode,
            stage="asr",
            input_artifact_kind="source_media_snapshot",
        ) == ("eligible", [])
        eligibility, reasons = _evaluate_external_asr_export(
            session,
            orphan_episode,
            stage="asr",
            input_artifact_kind="source_media_snapshot",
        )
        assert eligibility == "blocked_rights"
        assert reasons == [
            "Podcast publisher media may not leave this deployment for external ASR"
        ]


def test_credentialed_source_is_persisted_blocked_before_asr_enqueue(engine):
    policy = RecordingPolicy({"asr"})
    secret = "do-not-persist-this-feed-secret"
    with Session(engine) as session:
        _mark_source_credentialed(session, secret=secret)
        process = _enqueue(session, policy)
        assert process.eligibility_status == "blocked_rights"
        assert process.processing_status == "not_required"
        persisted_reason = process.eligibility_reasons_json
        assert "may not leave this deployment" in persisted_reason
        assert secret not in persisted_reason
        assert "feeds.example.test" not in persisted_reason
        assert secret not in process.error_message


def test_external_asr_gate_does_not_block_local_validation_or_non_asr_paths(engine):
    policy = RecordingPolicy({"translate", "tts"})
    with Session(engine) as session:
        _mark_source_credentialed(session)
        episode = session.get(ArticleRecord, "episode-1")
        assert episode is not None
        assert _evaluate_external_asr_export(
            session,
            episode,
            stage="fetch",
            input_artifact_kind="source_media_snapshot",
        ) == ("eligible", [])
        assert _evaluate_external_asr_export(
            session,
            episode,
            stage="asr",
            input_artifact_kind="publisher_transcript",
        ) == ("eligible", [])
        assert _evaluate_external_asr_export(
            session,
            episode,
            stage="tts",
            input_artifact_kind="source_media_snapshot",
        ) == ("eligible", [])
        session.rollback()

        publisher_path = _enqueue(
            session,
            policy,
            stage="translate",
            input_fingerprint=deterministic_input_fingerprint("publisher-path"),
            idempotency_key="process:publisher-path",
            input_artifact_id="publisher-transcript-1",
            input_artifact_kind="publisher_transcript",
            input_content_hash="c" * 64,
            input_language="en",
        )
        assert publisher_path.eligibility_status == "eligible"
        assert publisher_path.processing_status == "queued"


def test_claim_reclassifies_queued_asr_when_source_becomes_credentialed(engine):
    policy = RecordingPolicy({"asr"})
    secret = "claim-time-secret"
    with Session(engine) as session:
        process = _enqueue(session, policy)
        _mark_source_credentialed(session, secret=secret)
        assert (
            claim_next_processing(
                session,
                worker_id="worker",
                lease_seconds=30,
                policy=policy,
                now=NOW,
            )
            is None
        )
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.eligibility_status == "blocked_rights"
        assert persisted.processing_status == "not_required"
        assert secret not in persisted.eligibility_reasons_json
        assert "feeds.example.test" not in persisted.eligibility_reasons_json


def test_begin_reclassifies_running_asr_when_source_becomes_credentialed(engine):
    policy = RecordingPolicy({"asr"})
    secret = "begin-time-secret"
    with Session(engine) as session:
        process = _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        _mark_source_credentialed(session, secret=secret)
        with pytest.raises(PodcastEligibilityDenied, match="may not leave"):
            _attempt(session, claim, policy)
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.eligibility_status == "blocked_rights"
        assert persisted.processing_status == "not_required"
        assert secret not in persisted.eligibility_reasons_json
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 0


def test_queued_source_media_snapshot_remains_claimable(engine, monkeypatch):
    from services import podcast_processing as processing_service

    policy = RecordingPolicy({"asr"})
    real_as_utc = processing_service._as_utc
    with Session(engine) as session:
        process = _enqueue(session, policy)
        monkeypatch.setattr(
            processing_service,
            "_as_utc",
            lambda value: (
                dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)
                if value is None
                else real_as_utc(value)
            ),
        )
        claim = claim_next_processing(
            session,
            worker_id="ttl-pinned-worker",
            lease_seconds=30,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        assert claim.processing_id == process.id


def test_sqlite_claim_is_concurrency_safe(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        process = _enqueue(session, policy)
    barrier = Barrier(2)

    def claim(worker: str):
        with Session(engine) as session:
            barrier.wait()
            return claim_next_processing(
                session,
                worker_id=worker,
                lease_seconds=30,
                policy=policy,
                now=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))
    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    assert winners[0].processing_id == process.id
    assert len(winners[0].lease_token) == 32
    assert winners[0].fencing_token == 1


def test_claim_stage_filter_does_not_starve_allowed_work_after_many_denials(engine):
    policy = RecordingPolicy({"translate"})
    stamp = NOW.isoformat(timespec="microseconds")
    with Session(engine) as session:
        for index in range(65):
            session.add(
                PodcastProcessingRecord(
                    id=f"denied-{index}",
                    episode_id="episode-1",
                    input_fingerprint=deterministic_input_fingerprint(
                        {"candidate": index}
                    ),
                    pipeline_version="v1",
                    requested_target="digest_blog",
                    idempotency_key=f"denied:{index}",
                    input_artifact_id="source-media-1",
                    input_artifact_kind="source_media_snapshot",
                    input_content_hash="a" * 64,
                    input_language="und",
                    budget_scope="podcast-paid-processing",
                    budget_period="2026-09",
                    budget_limit_minor=100,
                    per_run_budget_minor=100,
                    eligibility_status="eligible",
                    processing_status="queued",
                    stage="asr",
                    queued_at=stamp,
                    updated_at=stamp,
                    created_at=stamp,
                )
            )
        session.add(
            PodcastProcessingRecord(
                id="allowed-after-64",
                episode_id="episode-1",
                input_fingerprint=deterministic_input_fingerprint(
                    {"candidate": "allowed"}
                ),
                pipeline_version="v1",
                requested_target="digest_blog",
                idempotency_key="allowed-after-64",
                input_artifact_id="source-media-1",
                input_artifact_kind="source_media_snapshot",
                input_content_hash="a" * 64,
                input_language="und",
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                per_run_budget_minor=100,
                eligibility_status="eligible",
                processing_status="queued",
                stage="translate",
                queued_at=stamp,
                updated_at=stamp,
                created_at=stamp,
            )
        )
        session.commit()
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=30,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        assert claim.processing_id == "allowed-after-64"


def test_mutator_rejects_but_does_not_rollback_caller_transaction(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        article = session.get(ArticleRecord, "episode-1")
        assert article is not None
        article.title = "uncommitted caller edit"
        session.add(article)
        with pytest.raises(PodcastProcessingConflict, match="clean/new Session"):
            _enqueue(session, policy)
        assert session.in_transaction()
        assert (
            session.get(ArticleRecord, "episode-1").title == "uncommitted caller edit"
        )
        session.rollback()
    with Session(engine) as session:
        article = session.get(ArticleRecord, "episode-1")
        assert article is not None and article.title == "Episode"


def test_concurrent_budget_reservations_cannot_exceed_hard_cap(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "episode-2"}
            ),
            idempotency_key="process:episode-2:v1",
        )
    with Session(engine) as session:
        first = claim_next_processing(
            session,
            worker_id="worker-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    with Session(engine) as session:
        second = claim_next_processing(
            session,
            worker_id="worker-2",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    assert first is not None and second is not None
    barrier = Barrier(2)

    def reserve(item):
        suffix, claim = item
        with Session(engine) as session:
            barrier.wait()
            try:
                return _attempt(
                    session, claim, policy, suffix=suffix, estimate=80, cap=100
                ).id
            except PodcastBudgetExceeded:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (("1", first), ("2", second))))
    assert len([item for item in results if item is not None]) == 1
    with Session(engine) as session:
        reservations = list(session.exec(select(PodcastBudgetReservationRecord)).all())
        assert len(reservations) == 1
        assert reservations[0].reserved_minor == 80


def test_lease_heartbeat_reclaim_and_aba_fencing(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        old = claim_next_processing(
            session,
            worker_id="old-worker",
            lease_seconds=10,
            policy=policy,
            now=NOW,
        )
        assert old is not None
        renewed = heartbeat_processing(
            session,
            old,
            lease_seconds=20,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert renewed.lease_token == old.lease_token
        assert renewed.fencing_token == old.fencing_token
        assert renewed.lease_expires_at > old.lease_expires_at

    with Session(engine) as session:
        assert (
            claim_next_processing(
                session,
                worker_id="too-early",
                lease_seconds=20,
                policy=policy,
                now=NOW + dt.timedelta(seconds=24),
            )
            is None
        )
        replacement = claim_next_processing(
            session,
            worker_id="new-worker",
            lease_seconds=20,
            policy=policy,
            now=NOW + dt.timedelta(seconds=26),
        )
        assert replacement is not None
        assert replacement.lease_token != old.lease_token
        assert replacement.fencing_token == old.fencing_token + 1
        with pytest.raises(PodcastLeaseLost):
            heartbeat_processing(
                session,
                old,
                lease_seconds=20,
                policy=policy,
                now=NOW + dt.timedelta(seconds=26),
            )


def test_timeout_before_provider_task_id_requires_request_key_reconciliation(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        unknown = mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="",
            request_unknown=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        assert unknown.provider_task_id == ""
        assert unknown.provider_request_key == "provider-request:1"
        assert unknown.retry_state == "reconcile_required"
        with pytest.raises(PodcastProviderReconciliationRequired):
            begin_stage_attempt(
                session,
                claim,
                input_hash="a" * 64,
                settings_fingerprint="f" * 64,
                provider_name="provider-spy",
                model_name="asr-model",
                provider_revision="2026-09",
                provider_request_key="provider-request:2",
                execution_kind="provider",
                estimated_cost_minor=80,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="reservation:2",
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )
        with pytest.raises(ValueError, match="provider_task_id"):
            reconcile_provider_request(
                session,
                claim,
                attempt_id=attempt.id,
                outcome="submitted",
                provider_task_id="",
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )
        reconciled = reconcile_provider_request(
            session,
            claim,
            attempt_id=attempt.id,
            outcome="submitted",
            provider_task_id="provider-task-recovered",
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert reconciled.submission_state == "submitted"
        assert reconciled.request_unknown is False
        assert reconciled.provider_task_id == "provider-task-recovered"
        with pytest.raises(PodcastProcessingConflict, match="reconciliation"):
            reconcile_provider_request(
                session,
                claim,
                attempt_id=attempt.id,
                outcome="submitted",
                provider_task_id="provider-task-overwrite",
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )


def test_provider_submission_replay_is_exact_and_identity_cannot_change(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        submitted = mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-1",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        replay = mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-1",
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert replay.id == submitted.id
        with pytest.raises(PodcastProcessingConflict, match="replay differs"):
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                provider_task_id="provider-task-2",
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )
        persisted = session.get(PodcastStageAttemptRecord, attempt.id)
        assert persisted is not None
        assert persisted.provider_task_id == "provider-task-1"


def test_execution_kind_separates_provider_submission_and_cost_boundaries(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(
            session,
            claim,
            policy,
            estimate=0,
            execution_kind="local",
        )
        with pytest.raises(PodcastProcessingConflict, match="local execution"):
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                provider_task_id="must-not-exist",
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )
        with pytest.raises(PodcastProcessingConflict, match="actual cost must be zero"):
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="charge:invalid-local",
                actual_cost_minor=1,
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )


def test_provider_attempt_requires_confirmed_submission_identity_before_settlement(
    engine,
):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        with pytest.raises(PodcastProcessingConflict, match="must be submitted"):
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="charge:before-submit",
                actual_cost_minor=0,
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )
        with pytest.raises(ValueError, match="provider_task_id"):
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                provider_task_id="",
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )


def test_exact_submission_replay_survives_lease_reclaim(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        old_claim = claim_next_processing(
            session,
            worker_id="old-worker",
            lease_seconds=5,
            policy=policy,
            now=NOW,
        )
        assert old_claim is not None
        attempt = _attempt(session, old_claim, policy)
        mark_provider_submission(
            session,
            old_claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-replay",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        replacement = claim_next_processing(
            session,
            worker_id="replacement",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=6),
        )
        assert replacement is not None
        replay = mark_provider_submission(
            session,
            old_claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-replay",
            policy=policy,
            now=NOW + dt.timedelta(seconds=7),
        )
        assert replay.submission_state == "submitted"


def test_restarted_worker_reconciles_and_commits_prior_submitted_attempt(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        old_claim = claim_next_processing(
            session,
            worker_id="old-worker",
            lease_seconds=5,
            policy=policy,
            now=NOW,
        )
        assert old_claim is not None
        attempt = _attempt(session, old_claim, policy)
        mark_provider_submission(
            session,
            old_claim,
            attempt_id=attempt.id,
            request_unknown=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
    with Session(engine) as session:
        new_claim = claim_next_processing(
            session,
            worker_id="restart-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=6),
        )
        assert new_claim is not None
        assert new_claim.fencing_token == 2
        with pytest.raises(PodcastProviderReconciliationRequired):
            _attempt(session, new_claim, policy, suffix="2")
        reconcile_provider_request(
            session,
            new_claim,
            attempt_id=attempt.id,
            outcome="submitted",
            provider_task_id="provider-task-restarted",
            policy=policy,
            now=NOW + dt.timedelta(seconds=7),
        )
        artifact = _materialize_asr_attempt(
            session,
            new_claim,
            attempt,
            policy,
            now=NOW + dt.timedelta(seconds=7, milliseconds=500),
        )
        settle_attempt_cost(
            session,
            new_claim,
            attempt_id=attempt.id,
            settlement_key="charge:recovered",
            actual_cost_minor=75,
            policy=policy,
            now=NOW + dt.timedelta(seconds=8),
        )
        process = commit_stage_attempt(
            session,
            new_claim,
            attempt_id=attempt.id,
            output_hash=artifact.content_hash,
            policy=policy,
            now=NOW + dt.timedelta(seconds=9),
        )
        assert process.processing_status == "ready"
        assert process.actual_cost_minor == 75


def test_submitted_poll_schedule_reclaims_same_attempt_after_restart(engine):
    policy = RecordingPolicy({"asr"})
    deadline = NOW + dt.timedelta(minutes=10)
    with Session(engine) as session:
        process = _enqueue(session, policy, requested_target="transcript")
        first_claim = claim_next_processing(
            session,
            worker_id="poll-worker-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        attempt = _attempt(session, first_claim, policy)
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-pending",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        waiting = schedule_stage_poll(
            session,
            first_claim,
            attempt_id=attempt.id,
            retry_at=NOW + dt.timedelta(seconds=20),
            poll_performed=False,
            provider_deadline_at=deadline,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert waiting.processing_status == "retry_wait"
        assert waiting.lease_token is None
        assert waiting.next_retry_at == (NOW + dt.timedelta(seconds=20)).isoformat(
            timespec="microseconds"
        )

        persisted_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert persisted_attempt is not None
        assert persisted_attempt.submission_state == "submitted"
        assert persisted_attempt.provider_task_id == "provider-task-pending"
        assert persisted_attempt.poll_count == 0
        assert persisted_attempt.last_polled_at is None
        assert persisted_attempt.provider_deadline_at == deadline.isoformat(
            timespec="microseconds"
        )
        assert reservation.status == "reserved"
        session.rollback()

        assert (
            claim_next_processing(
                session,
                worker_id="too-early",
                lease_seconds=60,
                policy=policy,
                now=NOW + dt.timedelta(seconds=19),
            )
            is None
        )
        restarted_claim = claim_next_processing(
            session,
            worker_id="poll-worker-2",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=20),
        )
        assert restarted_claim is not None
        assert restarted_claim.fencing_token == first_claim.fencing_token + 1
        with pytest.raises(PodcastProviderReconciliationRequired):
            _attempt(session, restarted_claim, policy, suffix="2")
        waiting_again = schedule_stage_poll(
            session,
            restarted_claim,
            attempt_id=attempt.id,
            retry_at=NOW + dt.timedelta(seconds=40),
            poll_performed=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=21),
        )
        assert waiting_again.processing_status == "retry_wait"
        same_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        assert same_attempt is not None and same_attempt.poll_count == 1
        assert same_attempt.last_polled_at == (
            NOW + dt.timedelta(seconds=21)
        ).isoformat(timespec="microseconds")
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 1
        assert session.get(PodcastProcessingRecord, process.id).attempt_count == 1
        session.rollback()
        with pytest.raises(IntegrityError, match="identity is immutable"):
            session.execute(
                text(
                    "UPDATE podcast_stage_attempts SET poll_count = 0 "
                    "WHERE id = :attempt_id"
                ),
                {"attempt_id": attempt.id},
            )
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError, match="identity is immutable"):
            session.execute(
                text(
                    "UPDATE podcast_stage_attempts SET provider_deadline_at = :deadline "
                    "WHERE id = :attempt_id"
                ),
                {
                    "attempt_id": attempt.id,
                    "deadline": (NOW + dt.timedelta(minutes=20)).isoformat(),
                },
            )
            session.commit()


def test_poll_schedule_rejects_missing_identity_deadline_and_deadline_overwrite(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="poll-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            request_unknown=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        with pytest.raises(PodcastProviderReconciliationRequired, match="identity"):
            schedule_stage_poll(
                session,
                claim,
                attempt_id=attempt.id,
                retry_at=NOW + dt.timedelta(seconds=10),
                poll_performed=True,
                provider_deadline_at=NOW + dt.timedelta(minutes=5),
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )

        reconcile_provider_request(
            session,
            claim,
            attempt_id=attempt.id,
            outcome="submitted",
            provider_task_id="provider-task-recovered",
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        with pytest.raises(ValueError, match="first poll"):
            schedule_stage_poll(
                session,
                claim,
                attempt_id=attempt.id,
                retry_at=NOW + dt.timedelta(seconds=10),
                poll_performed=False,
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )
        schedule_stage_poll(
            session,
            claim,
            attempt_id=attempt.id,
            retry_at=NOW + dt.timedelta(seconds=10),
            poll_performed=False,
            provider_deadline_at=NOW + dt.timedelta(minutes=5),
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        reclaimed = claim_next_processing(
            session,
            worker_id="poll-worker-reclaimed",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=10),
        )
        assert reclaimed is not None
        with pytest.raises(PodcastProcessingConflict, match="initial provider poll"):
            schedule_stage_poll(
                session,
                reclaimed,
                attempt_id=attempt.id,
                retry_at=NOW + dt.timedelta(seconds=20),
                poll_performed=False,
                policy=policy,
                now=NOW + dt.timedelta(seconds=11),
            )
        with pytest.raises(PodcastProcessingConflict, match="cannot be overwritten"):
            schedule_stage_poll(
                session,
                reclaimed,
                attempt_id=attempt.id,
                retry_at=NOW + dt.timedelta(seconds=20),
                poll_performed=True,
                provider_deadline_at=NOW + dt.timedelta(minutes=6),
                policy=policy,
                now=NOW + dt.timedelta(seconds=11),
            )


def test_reconciliation_park_is_not_claimable_and_preserves_provider_hold(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="deadline-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-deadline",
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        parked = park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt.id,
            reason_code="provider_deadline_elapsed",
            redacted_error_message="provider did not reach a terminal state",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        assert parked.processing_status == "reconciliation_required"
        assert parked.lease_owner is None
        assert parked.next_retry_at is None
        persisted_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert persisted_attempt is not None
        assert persisted_attempt.submission_state == "reconciling"
        assert persisted_attempt.request_unknown is True
        assert persisted_attempt.retry_state == "reconcile_required"
        assert persisted_attempt.provider_task_id == "provider-task-deadline"
        assert reservation.status == "reserved"
        assert PodcastArtifactStore._active_processing_reference_counts(
            session, ["source-media-1"]
        ) == {"source-media-1": 1}
        session.rollback()
        assert (
            claim_next_processing(
                session,
                worker_id="must-not-auto-reclaim",
                lease_seconds=60,
                policy=policy,
                now=NOW + dt.timedelta(days=1),
            )
            is None
        )
    registry = PodcastProcessingProviderRegistry()
    registry.register_target(
        "transcript",
        stage_executors={"asr": lambda _context: None},
        estimator=lambda _context: 0,
    )
    with pytest.raises(PodcastAdminError) as rejected:
        retry_processing(
            engine,
            registry,
            policy.config,
            processing_id=parked.id,
            idempotency_key="manual-retry-parked-provider",
            expected_attempt_count=1,
            reason="operator requested retry before reconciliation",
            actor="admin",
        )
    assert rejected.value.code == "podcast_provider_reconciliation_required"
    with Session(engine) as session:
        resumed = reconcile_parked_provider_request(
            session,
            processing_id=parked.id,
            attempt_id=attempt.id,
            expected_attempt_count=1,
            expected_fencing_token=claim.fencing_token,
            expected_provider_request_key="provider-request:1",
            outcome="submitted",
            provider_task_id="provider-task-deadline",
            retry_at=NOW + dt.timedelta(seconds=30),
            idempotency_key="reconcile-provider-deadline",
            actor="admin",
            reason="provider console confirms the original task exists",
            now=NOW + dt.timedelta(seconds=3),
        )
        assert resumed.processing_status == "retry_wait"
        replay = reconcile_parked_provider_request(
            session,
            processing_id=parked.id,
            attempt_id=attempt.id,
            expected_attempt_count=1,
            expected_fencing_token=claim.fencing_token,
            expected_provider_request_key="provider-request:1",
            outcome="submitted",
            provider_task_id="provider-task-deadline",
            retry_at=NOW + dt.timedelta(seconds=30),
            idempotency_key="reconcile-provider-deadline",
            actor="admin",
            reason="provider console confirms the original task exists",
            now=NOW + dt.timedelta(seconds=31),
        )
        assert replay.processing_status == "retry_wait"
        with pytest.raises(PodcastProcessingConflict, match="audit truth"):
            reconcile_parked_provider_request(
                session,
                processing_id=parked.id,
                attempt_id=attempt.id,
                expected_attempt_count=1,
                expected_fencing_token=claim.fencing_token,
                expected_provider_request_key="provider-request:1",
                outcome="submitted",
                provider_task_id="provider-task-deadline",
                retry_at=NOW + dt.timedelta(seconds=30),
                idempotency_key="reconcile-provider-deadline",
                actor="admin",
                reason="different operator assertion",
                now=NOW + dt.timedelta(seconds=4),
            )
        audit = session.exec(
            select(PodcastProcessingCommandRecord).where(
                PodcastProcessingCommandRecord.command_type == "provider_reconcile"
            )
        ).one()
        assert audit.outcome == "accepted"
        assert json.loads(audit.reason) == {
            "attempt_id": attempt.id,
            "expected_fencing_token": claim.fencing_token,
            "expected_provider_request_key": "provider-request:1",
            "operator_reason": "provider console confirms the original task exists",
            "provider_task_id": "provider-task-deadline",
            "requested_outcome": "submitted",
            "retry_at": (NOW + dt.timedelta(seconds=30)).isoformat(
                timespec="microseconds"
            ),
        }
        resumed_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        resumed_hold = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert resumed_attempt is not None
        assert resumed_attempt.submission_state == "submitted"
        assert resumed_attempt.request_unknown is False
        assert resumed_hold.status == "reserved"


def test_operator_not_submitted_reconciliation_is_audited_before_new_attempt(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="unknown-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            request_unknown=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        parked = park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt.id,
            reason_code="submission_unknown",
            redacted_error_message="provider lookup requires an operator",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        with pytest.raises(PodcastProcessingConflict, match="fence changed"):
            reconcile_parked_provider_request(
                session,
                processing_id=parked.id,
                attempt_id=attempt.id,
                expected_attempt_count=1,
                expected_fencing_token=claim.fencing_token + 1,
                expected_provider_request_key="provider-request:1",
                outcome="not_submitted",
                retry_at=NOW + dt.timedelta(seconds=20),
                idempotency_key="reconcile-wrong-fence",
                actor="admin",
                reason="provider confirms no matching task",
                now=NOW + dt.timedelta(seconds=3),
            )
        rejected = session.exec(
            select(PodcastProcessingCommandRecord).where(
                PodcastProcessingCommandRecord.idempotency_key
                == "reconcile-wrong-fence"
            )
        ).one()
        assert rejected.outcome == "rejected"
        assert rejected.error_code == "podcast_processing_conflict"
        session.rollback()

        retryable = reconcile_parked_provider_request(
            session,
            processing_id=parked.id,
            attempt_id=attempt.id,
            expected_attempt_count=1,
            expected_fencing_token=claim.fencing_token,
            expected_provider_request_key="provider-request:1",
            outcome="not_submitted",
            retry_at=NOW + dt.timedelta(seconds=20),
            idempotency_key="reconcile-not-submitted",
            actor="admin",
            reason="provider confirms no matching task",
            now=NOW + dt.timedelta(seconds=4),
        )
        assert retryable.processing_status == "retry_wait"
        old_attempt = session.get(PodcastStageAttemptRecord, attempt.id)
        old_hold = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert old_attempt is not None
        assert old_attempt.submission_state == "failed_retryable"
        assert old_hold.status == "released"
        session.rollback()

        next_claim = claim_next_processing(
            session,
            worker_id="safe-new-attempt-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=20),
        )
        assert next_claim is not None
        next_attempt = _attempt(session, next_claim, policy, suffix="2")
        assert next_attempt.attempt_no == 2


def test_poll_columns_reject_inconsistent_or_local_provider_state(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="constraint-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy, estimate=0, execution_kind="local")
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE podcast_stage_attempts SET poll_count = 1, "
                    "last_polled_at = :stamp, provider_deadline_at = :stamp "
                    "WHERE id = :attempt_id"
                ),
                {"stamp": NOW.isoformat(), "attempt_id": attempt.id},
            )
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE podcast_stage_attempts SET poll_count = -1 "
                    "WHERE id = :attempt_id"
                ),
                {"attempt_id": attempt.id},
            )
            session.commit()


def test_cost_settlement_is_idempotent_and_stage_commit_is_fenced(engine):
    policy = RecordingPolicy({"asr", "translate"})
    with Session(engine) as session:
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-1",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        artifact = _materialize_asr_attempt(
            session,
            claim,
            attempt,
            policy,
            now=NOW + dt.timedelta(seconds=2, milliseconds=500),
        )
        first = settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="charge:provider-task-1",
            actual_cost_minor=73,
            usage={"audio_seconds": 600},
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        repeated = settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="charge:provider-task-1",
            actual_cost_minor=73,
            usage={"audio_seconds": 600},
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert first.id == repeated.id
        with pytest.raises(PodcastProcessingConflict, match="settled differently"):
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="charge:different",
                actual_cost_minor=73,
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )
        process = commit_stage_attempt(
            session,
            claim,
            attempt_id=attempt.id,
            output_hash=artifact.content_hash,
            next_stage="translate",
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert process.processing_status == "queued"
        assert process.stage == "translate"
        assert process.actual_cost_minor == 73
        assert process.lease_token is None
        assert session.exec(select(func.count(PodcastCostLedgerRecord.id))).one() == 1
        reservation = session.exec(select(PodcastBudgetReservationRecord)).one()
        assert reservation.status == "settled"
        assert reservation.actual_cost_minor == 73

    assert ("asr", "enqueue") in policy.calls
    assert ("asr", "claim") in policy.calls
    assert ("asr", "provider_submit") in policy.calls
    assert ("asr", "commit") in policy.calls


def test_provider_boundary_rejects_cumulative_multi_stage_overrun(engine):
    policy = RecordingPolicy({"asr", "translate"})
    policy.config = replace(policy.config, monthly_budget_cny_minor=1_000)
    with Session(engine) as session:
        process = _enqueue(
            session,
            policy,
            estimated_cost_minor=100,
            budget_limit_minor=1_000,
            per_run_budget_minor=100,
        )
        first_claim = claim_next_processing(
            session,
            worker_id="worker-asr",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session, first_claim, policy, suffix="1", estimate=60, cap=1_000
        )
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="provider-stage-asr",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        artifact = _materialize_asr_attempt(
            session,
            first_claim,
            first_attempt,
            policy,
            now=NOW + dt.timedelta(seconds=2, milliseconds=500),
        )
        settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="charge:stage-asr",
            actual_cost_minor=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        commit_stage_attempt(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            output_hash=artifact.content_hash,
            next_stage="translate",
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        second_claim = claim_next_processing(
            session,
            worker_id="worker-translate",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert second_claim is not None
        with pytest.raises(PodcastBudgetExceeded, match="trusted configuration"):
            _attempt(
                session,
                second_claim,
                policy,
                suffix="6",
                estimate=41,
                cap=1_000,
            )
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 1
        assert (
            session.exec(select(func.count(PodcastBudgetReservationRecord.id))).one()
            == 1
        )
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None and persisted.actual_cost_minor == 60


def test_provider_boundary_rejects_forged_initial_input_hash(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        with pytest.raises(
            PodcastProcessingConflict, match="immutable processing chain"
        ):
            _attempt(session, claim, policy, input_hash="b" * 64)
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 0
        assert (
            session.exec(select(func.count(PodcastBudgetReservationRecord.id))).one()
            == 0
        )


def test_stage_hash_chain_rejects_invalid_output_and_forged_next_input(engine):
    policy = RecordingPolicy({"asr", "translate"})
    policy.config = replace(policy.config, monthly_budget_cny_minor=1_000)
    with Session(engine) as session:
        _enqueue(
            session,
            policy,
            estimated_cost_minor=100,
            budget_limit_minor=1_000,
        )
        first_claim = claim_next_processing(
            session,
            worker_id="worker-asr",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session, first_claim, policy, suffix="1", estimate=10, cap=1_000
        )
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="provider-stage-asr",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        artifact = _materialize_asr_attempt(
            session,
            first_claim,
            first_attempt,
            policy,
            now=NOW + dt.timedelta(seconds=2, milliseconds=500),
        )
        settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="charge:hash-chain-asr",
            actual_cost_minor=10,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        with pytest.raises(ValueError, match="output_hash must be a SHA-256"):
            commit_stage_attempt(
                session,
                first_claim,
                attempt_id=first_attempt.id,
                output_hash="not-a-content-hash",
                next_stage="translate",
                policy=policy,
                now=NOW + dt.timedelta(seconds=4),
            )
        commit_stage_attempt(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            output_hash=artifact.content_hash,
            next_stage="translate",
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        second_claim = claim_next_processing(
            session,
            worker_id="worker-translate",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert second_claim is not None
        with pytest.raises(
            PodcastProcessingConflict, match="immutable processing chain"
        ):
            _attempt(
                session,
                second_claim,
                policy,
                suffix="6",
                estimate=10,
                cap=1_000,
                input_hash="d" * 64,
            )
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 1
        assert (
            session.exec(select(func.count(PodcastBudgetReservationRecord.id))).one()
            == 1
        )


def test_provider_boundary_requires_typed_budget_configuration(engine):
    enqueue_policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, enqueue_policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=enqueue_policy,
            now=NOW,
        )
        assert claim is not None
        untrusted_policy = RecordingPolicy({"asr"})
        untrusted_policy.config = object()
        with pytest.raises(PodcastBudgetExceeded, match="requires trusted"):
            _attempt(session, claim, untrusted_policy)
        assert session.exec(select(func.count(PodcastStageAttemptRecord.id))).one() == 0
        assert (
            session.exec(select(func.count(PodcastBudgetReservationRecord.id))).one()
            == 0
        )


def test_effective_run_identity_includes_policy_and_budget_snapshot(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        first = _enqueue(session, policy)
        policy_change = _enqueue(
            session,
            policy,
            idempotency_key="process:episode-1:policy-v2",
            policy_version="eligibility-v2",
        )
        next_period = _enqueue(
            session,
            policy,
            idempotency_key="process:episode-1:period-2026-10",
            policy_version="eligibility-v2",
            budget_period="2026-10",
        )
        assert len({first.id, policy_change.id, next_period.id}) == 3


def test_target_stage_graph_and_destination_authority_are_enforced(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        with pytest.raises(ValueError, match="invalid for target"):
            _enqueue(
                session,
                policy,
                requested_target="digest_audio",
                stage="asr",
            )
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-stage-authority",
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="charge:stage-authority",
            actual_cost_minor=0,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        with pytest.raises(PermissionError, match="denied translate"):
            commit_stage_attempt(
                session,
                claim,
                attempt_id=attempt.id,
                output_hash="c" * 64,
                next_stage="translate",
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )
        persisted = session.get(PodcastProcessingRecord, claim.processing_id)
        assert persisted is not None and persisted.processing_status == "running"


def test_actual_cost_above_reservation_is_recorded_and_blocks_future_work(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        process = _enqueue(session, policy, requested_target="transcript")
        first = claim_next_processing(
            session,
            worker_id="worker-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first is not None
        first_attempt = _attempt(session, first, policy, estimate=80, cap=100)
        mark_provider_submission(
            session,
            first,
            attempt_id=first_attempt.id,
            provider_task_id="provider-overage",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        artifact = _materialize_asr_attempt(
            session,
            first,
            first_attempt,
            policy,
            now=NOW + dt.timedelta(seconds=2, milliseconds=500),
        )
        ledger = settle_attempt_cost(
            session,
            first,
            attempt_id=first_attempt.id,
            settlement_key="charge:provider-overage",
            actual_cost_minor=120,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert ledger.actual_cost_minor == 120
        assert ledger.budget_breached is True
        commit_stage_attempt(
            session,
            first,
            attempt_id=first_attempt.id,
            output_hash=artifact.content_hash,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "episode-2"}
            ),
            idempotency_key="process:episode-2:v1",
            estimated_cost_minor=1,
        )
        second = claim_next_processing(
            session,
            worker_id="worker-2",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert second is not None
        with pytest.raises(PodcastBudgetExceeded):
            _attempt(session, second, policy, suffix="2", estimate=1, cap=100)
        assert session.exec(select(func.count(PodcastCostLedgerRecord.id))).one() == 1
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == first_attempt.id
            )
        ).one()
        persisted_process = session.get(PodcastProcessingRecord, process.id)
        assert reservation.budget_breached is True
        assert (
            persisted_process is not None and persisted_process.budget_breached is True
        )


def test_budget_reservation_releases_on_retry_and_expired_pre_submit_recovers(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        first_claim = claim_next_processing(
            session,
            worker_id="crashed-worker",
            lease_seconds=5,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        with pytest.raises(PodcastBudgetExceeded):
            begin_stage_attempt(
                session,
                first_claim,
                input_hash="a" * 64,
                settings_fingerprint="f" * 64,
                provider_name="provider-spy",
                model_name="asr-model",
                provider_revision="2026-09",
                provider_request_key="provider-request:over-cap",
                execution_kind="provider",
                estimated_cost_minor=101,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="reservation:over-cap",
                policy=policy,
                now=NOW + dt.timedelta(seconds=2),
            )
        first_attempt = _attempt(session, first_claim, policy, estimate=80, cap=100)

    with Session(engine) as session:
        reclaimed = claim_next_processing(
            session,
            worker_id="restarted-worker",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=6),
        )
        assert reclaimed is not None
        old = session.get(PodcastStageAttemptRecord, first_attempt.id)
        assert old is not None and old.submission_state == "request_unknown"
        assert old.retry_state == "reconcile_required"
        reservations = list(
            session.exec(
                select(PodcastBudgetReservationRecord).order_by(
                    PodcastBudgetReservationRecord.created_at
                )
            ).all()
        )
        assert [item.status for item in reservations] == ["reserved"]
        session.rollback()
        with pytest.raises(PodcastProviderReconciliationRequired):
            _attempt(session, reclaimed, policy, suffix="2", estimate=80, cap=100)
        reconcile_provider_request(
            session,
            reclaimed,
            attempt_id=first_attempt.id,
            outcome="not_submitted",
            retry_at=NOW + dt.timedelta(seconds=20),
            policy=policy,
            now=NOW + dt.timedelta(seconds=8),
        )
        retry_claim = claim_next_processing(
            session,
            worker_id="retry-worker",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=20),
        )
        assert retry_claim is not None
        second_attempt = _attempt(
            session, retry_claim, policy, suffix="2", estimate=80, cap=100
        )
        assert second_attempt.attempt_no == 2

        failed = fail_stage_attempt(
            session,
            retry_claim,
            attempt_id=second_attempt.id,
            error_code="provider_unavailable",
            redacted_error_message="temporary provider failure",
            retryable=True,
            retry_at=NOW + dt.timedelta(seconds=20),
            policy=policy,
            now=NOW + dt.timedelta(seconds=8),
        )
        assert failed.processing_status == "retry_wait"
        assert failed.next_retry_at == (NOW + dt.timedelta(seconds=20)).isoformat(
            timespec="microseconds"
        )
        assert (
            session.exec(
                select(PodcastBudgetReservationRecord).where(
                    PodcastBudgetReservationRecord.attempt_id == second_attempt.id
                )
            )
            .one()
            .status
            == "released"
        )
        session.rollback()
        assert (
            claim_next_processing(
                session,
                worker_id="too-soon",
                lease_seconds=30,
                policy=policy,
                now=NOW + dt.timedelta(seconds=19),
            )
            is None
        )
        final_retry_claim = claim_next_processing(
            session,
            worker_id="retry-worker",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=20),
        )
        assert final_retry_claim is not None
        assert final_retry_claim.fencing_token == 4


def test_expired_local_prepared_attempt_releases_and_retries_without_reconciliation(
    engine,
):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        first_claim = claim_next_processing(
            session,
            worker_id="local-crashed-worker",
            lease_seconds=5,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session,
            first_claim,
            policy,
            estimate=0,
            execution_kind="local",
        )

    with Session(engine) as session:
        reclaimed = claim_next_processing(
            session,
            worker_id="local-restarted-worker",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=6),
        )
        assert reclaimed is not None
        old = session.get(PodcastStageAttemptRecord, first_attempt.id)
        assert old is not None
        assert old.execution_kind == "local"
        assert old.submission_state == "failed_retryable"
        assert old.request_unknown is False
        assert old.retry_state == "none"
        assert old.error_code == "lease_expired_local_attempt"
        assert old.completed_at == (NOW + dt.timedelta(seconds=6)).isoformat(
            timespec="microseconds"
        )
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == first_attempt.id
            )
        ).one()
        assert reservation.status == "released"
        session.rollback()

        retry = _attempt(
            session,
            reclaimed,
            policy,
            suffix="2",
            estimate=0,
            execution_kind="local",
        )
        assert retry.attempt_no == 2
        assert retry.execution_kind == "local"


@pytest.mark.parametrize("execution_kind", ["", "remote", "LOCAL"])
def test_begin_attempt_rejects_unknown_execution_kind(engine, execution_kind):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=30,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        with pytest.raises(ValueError, match="execution_kind"):
            _attempt(
                session,
                claim,
                policy,
                estimate=0,
                execution_kind=execution_kind,
            )


def test_local_attempt_requires_zero_cost_estimate(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=30,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        with pytest.raises(ValueError, match="must be zero"):
            _attempt(
                session,
                claim,
                policy,
                estimate=1,
                execution_kind="local",
            )


def test_failure_cannot_target_an_old_attempt_while_new_attempt_is_active(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        first_claim = claim_next_processing(
            session,
            worker_id="worker-1",
            lease_seconds=30,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(session, first_claim, policy)
        fail_stage_attempt(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            error_code="retry",
            redacted_error_message="retry",
            retryable=True,
            retry_at=NOW + dt.timedelta(seconds=5),
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        second_claim = claim_next_processing(
            session,
            worker_id="worker-2",
            lease_seconds=30,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert second_claim is not None
        second_attempt = _attempt(session, second_claim, policy, suffix="2")
        with pytest.raises(PodcastProcessingConflict, match="current active"):
            fail_stage_attempt(
                session,
                second_claim,
                attempt_id=first_attempt.id,
                error_code="wrong-attempt",
                redacted_error_message="wrong-attempt",
                retryable=False,
                policy=policy,
                now=NOW + dt.timedelta(seconds=6),
            )
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == second_attempt.id
            )
        ).one()
        assert reservation.status == "reserved"


def test_database_guards_cost_audits_and_cascade_cleanup(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        process = _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(session, claim, policy)
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-task-immutable",
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        ledger = settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="charge:immutable",
            actual_cost_minor=0,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        reservation = session.exec(select(PodcastBudgetReservationRecord)).one()
        ledger_id = ledger.id
        reservation_id = reservation.id
        for statement in (
            f"UPDATE podcast_processings SET actual_cost_minor=1.5 WHERE id='{process.id}'",
            f"UPDATE podcast_stage_attempts SET execution_kind='local' WHERE id='{attempt.id}'",
            f"UPDATE podcast_cost_ledger SET actual_cost_minor=1 WHERE id='{ledger.id}'",
            f"DELETE FROM podcast_cost_ledger WHERE id='{ledger.id}'",
            f"UPDATE podcast_budget_reservations SET status='released' WHERE id='{reservation.id}'",
            f"DELETE FROM podcast_budget_reservations WHERE id='{reservation.id}'",
        ):
            with pytest.raises(IntegrityError):
                session.execute(text(statement))
                session.commit()
            session.rollback()

        with pytest.raises(IntegrityError):
            session.delete(session.get(PodcastProcessingRecord, process.id))
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.delete(session.get(ArticleRecord, "episode-1"))
            session.commit()
        session.rollback()
        assert session.get(PodcastCostLedgerRecord, ledger_id) is not None
        assert session.get(PodcastBudgetReservationRecord, reservation_id) is not None


def test_database_rejects_provider_state_on_local_attempt(engine):
    policy = RecordingPolicy({"asr"})
    stamp = NOW.isoformat(timespec="microseconds")
    with Session(engine) as session:
        process = _enqueue(session, policy)
        invalid = PodcastStageAttemptRecord(
            id="invalid-local-provider-state",
            processing_id=process.id,
            stage="asr",
            attempt_no=1,
            fencing_token=1,
            lease_token="lease",
            provider_request_key="invalid-local-provider-state",
            provider_task_id="remote-task",
            execution_kind="local",
            submission_state="submitted",
            started_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()


def test_aliyun_speech_attempt_requires_usage_plan_before_submission(engine):
    recording_policy = RecordingPolicy({"asr"})
    aliyun = AliyunIsiConfig(
        asr_quota_scope="aliyun-isi-asr-trial",
        asr_quota_timezone="Asia/Shanghai",
        asr_daily_audio_seconds_limit=7_200,
        asr_entitlement_ends_at="2026-12-06T23:59:59+08:00",
        asr_provider_deadline_seconds=3_600,
        asr_price_cny_minor_per_hour=0,
        asr_pricing_revision="trial-2026-09",
    )
    policy = PodcastStagePolicy(recording_policy.config, aliyun)
    with Session(engine) as session:
        _enqueue(session, policy)
        claim = claim_next_processing(
            session,
            worker_id="aliyun-quota-required",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        with pytest.raises(PodcastProviderQuotaExceeded, match="usage plan"):
            begin_stage_attempt(
                session,
                claim,
                input_hash="a" * 64,
                settings_fingerprint="f" * 64,
                provider_name="aliyun-isi",
                model_name="filetrans",
                provider_revision="4.0",
                provider_request_key="aliyun-without-quota",
                execution_kind="provider",
                estimated_cost_minor=0,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="aliyun-without-quota",
                policy=policy,
                now=NOW + dt.timedelta(seconds=1),
            )
        trusted_plan = asr_usage_plan(
            aliyun,
            audio_duration_ms=60_000,
            now=NOW + dt.timedelta(seconds=1),
        )
        with pytest.raises(PodcastProviderQuotaExceeded, match="trusted configuration"):
            begin_stage_attempt(
                session,
                claim,
                input_hash="a" * 64,
                settings_fingerprint="f" * 64,
                provider_name="aliyun-isi",
                model_name="filetrans",
                provider_revision="4.0",
                provider_request_key="aliyun-tampered-quota",
                execution_kind="provider",
                estimated_cost_minor=0,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="aliyun-tampered-quota",
                provider_usage_plan=replace(trusted_plan, limit_units=999_999),
                policy=policy,
                now=NOW + dt.timedelta(seconds=1),
            )
        with pytest.raises(ValueError, match="unit does not match"):
            begin_stage_attempt(
                session,
                claim,
                input_hash="a" * 64,
                settings_fingerprint="f" * 64,
                provider_name="aliyun-isi",
                model_name="filetrans",
                provider_revision="4.0",
                provider_request_key="aliyun-wrong-unit",
                execution_kind="provider",
                estimated_cost_minor=0,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="aliyun-wrong-unit",
                provider_usage_plan=replace(
                    trusted_plan,
                    unit=ProviderUsageUnit.TTS_CHARACTERS,
                ),
                policy=policy,
                now=NOW + dt.timedelta(seconds=1),
            )


def test_provider_call_authorization_releases_near_window_end_before_network(
    engine, monkeypatch
):
    aliyun = AliyunIsiConfig(
        request_timeout_seconds=30,
        asr_quota_scope="aliyun-isi-asr-trial",
        asr_quota_timezone="Asia/Shanghai",
        asr_daily_audio_seconds_limit=7_200,
        asr_entitlement_ends_at="2026-12-06T23:59:59+08:00",
        asr_provider_deadline_seconds=3_600,
        asr_price_cny_minor_per_hour=0,
        asr_pricing_revision="trial-2026-09",
    )
    recording = RecordingPolicy({"asr"})
    policy = PodcastStagePolicy(recording.config, aliyun)
    before_midnight = dt.datetime(2026, 9, 5, 15, 58, 59, tzinfo=dt.timezone.utc)
    unsafe_call_time = dt.datetime(2026, 9, 5, 15, 59, 31, tzinfo=dt.timezone.utc)
    next_window = dt.datetime(2026, 9, 5, 16, 0, 1, tzinfo=dt.timezone.utc)
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript", estimated_cost_minor=0)
        claim = claim_next_processing(
            session,
            worker_id="window-boundary-worker",
            lease_seconds=120,
            policy=policy,
            now=before_midnight,
        )
        assert claim is not None
        plan = asr_usage_plan(
            aliyun,
            audio_duration_ms=60_000,
            now=before_midnight,
        )
        attempt = begin_stage_attempt(
            session,
            claim,
            input_hash="a" * 64,
            settings_fingerprint="f" * 64,
            provider_name="aliyun-isi",
            model_name="filetrans",
            provider_revision="4.0",
            provider_request_key="aliyun-before-midnight",
            execution_kind="provider",
            estimated_cost_minor=0,
            budget_scope="podcast-paid-processing",
            budget_period="2026-09",
            budget_limit_minor=100,
            reservation_idempotency_key="aliyun-before-midnight",
            provider_usage_plan=plan,
            policy=policy,
            now=before_midnight,
        )
        authorized = authorize_provider_call(
            session,
            claim,
            attempt_id=attempt.id,
            policy=policy,
            now=before_midnight,
        )
        assert authorized.id == attempt.id
        monkeypatch.setattr(
            config_module,
            "settings",
            replace(
                config_module.settings,
                podcast=recording.config,
                aliyun_isi=aliyun,
            ),
        )
        default_authorized = authorize_provider_call(
            session,
            claim,
            attempt_id=attempt.id,
            policy=None,
            now=before_midnight,
        )
        assert default_authorized.id == attempt.id
        with pytest.raises(PodcastProviderQuotaExceeded, match="request timeout"):
            authorize_provider_call(
                session,
                claim,
                attempt_id=attempt.id,
                policy=policy,
                now=unsafe_call_time,
            )
        failed = fail_stage_attempt(
            session,
            claim,
            attempt_id=attempt.id,
            error_code="provider_window_rollover",
            redacted_error_message="provider usage window is closing",
            retryable=True,
            retry_at=next_window,
            policy=policy,
            now=unsafe_call_time,
        )
        assert failed.processing_status == "retry_wait"
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert reservation.status == "released"
        session.rollback()
        next_claim = claim_next_processing(
            session,
            worker_id="next-window-worker",
            lease_seconds=60,
            policy=policy,
            now=next_window,
        )
        assert next_claim is not None
        next_plan = asr_usage_plan(
            aliyun,
            audio_duration_ms=60_000,
            now=next_window,
        )
        assert plan.quota_period == "2026-09-05"
        assert next_plan.quota_period == "2026-09-06"


def test_aliyun_tts_campaign_settles_integer_price_replays_and_enforces_cap(engine):
    podcast = PodcastConfig(
        installation="external",
        authority_id="test-external",
        allowed_stages=("tts", "audio_qa", "local_publish"),
        processing_enabled=True,
        monthly_budget_cny_minor=100,
        per_run_budget_cny_minor=100,
        provider_ready_targets=("digest_audio",),
        voice_profiles=("narrator-zh",),
        default_voice_profile="narrator-zh",
    )
    aliyun = AliyunIsiConfig(
        tts_quota_scope="aliyun-isi-tts-campaign",
        tts_campaign_id="initial-10k",
        tts_campaign_starts_at="2026-09-01T00:00:00+08:00",
        tts_campaign_ends_at="2026-10-01T00:00:00+08:00",
        tts_campaign_character_limit=5_000,
        tts_provider_deadline_seconds=3_600,
        tts_price_cny_minor_per_10000_chars=100,
        tts_pricing_revision="campaign-2026-09",
    )
    policy = PodcastStagePolicy(podcast, aliyun)
    with Session(engine) as session:
        first_artifact_id, first_hash = _add_local_narration(
            session,
            episode_id="episode-1",
            characters=4_001,
        )
        second_artifact_id, second_hash = _add_local_narration(
            session,
            episode_id="episode-2",
            characters=1_000,
        )
        first_plan = tts_usage_plan(
            aliyun,
            billable_characters=4_001,
            now=NOW,
        )
        _enqueue(
            session,
            policy,
            stage="tts",
            requested_target="digest_audio",
            input_fingerprint=deterministic_input_fingerprint("tts-episode-1"),
            idempotency_key="process:tts:episode-1",
            estimated_cost_minor=first_plan.estimated_cost_minor,
            input_artifact_id=first_artifact_id,
            input_artifact_kind="narration_script_zh",
            input_content_hash=first_hash,
            input_language="zh-CN",
            narration_artifact_id=first_artifact_id,
            narration_content_hash=first_hash,
            voice_profile_id="narrator-zh",
        )
        first_claim = claim_next_processing(
            session,
            worker_id="tts-campaign-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = begin_stage_attempt(
            session,
            first_claim,
            input_hash=first_hash,
            settings_fingerprint="f" * 64,
            provider_name="aliyun-isi",
            model_name="long-text-tts",
            provider_revision="2026-09",
            provider_request_key="aliyun-tts-episode-1",
            execution_kind="provider",
            estimated_cost_minor=first_plan.estimated_cost_minor,
            budget_scope="podcast-paid-processing",
            budget_period="2026-09",
            budget_limit_minor=100,
            reservation_idempotency_key="aliyun-tts-reservation-1",
            usage_settlement_mode="submitted_characters",
            provider_usage_plan=first_plan,
            policy=policy,
            now=NOW + dt.timedelta(seconds=1),
        )
        assert first_attempt.usage_settlement_mode == "submitted_characters"
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="aliyun-tts-task-1",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        normalized_usage = NormalizedUsage(
            cost_minor=first_plan.estimated_cost_minor,
            tts_characters=4_001,
        )
        ledger = settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="aliyun-tts-settlement-1",
            actual_cost_minor=first_plan.estimated_cost_minor,
            usage=normalized_usage,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert first_plan.estimated_cost_minor == 41
        assert ledger.actual_usage_units == 4_001
        assert ledger.provider_quota_breached is False
        replay = settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="aliyun-tts-settlement-1",
            actual_cost_minor=41,
            usage=normalized_usage,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert replay.id == ledger.id
        assert session.exec(select(func.count(PodcastCostLedgerRecord.id))).one() == 1
        session.rollback()

        second_plan = tts_usage_plan(
            aliyun,
            billable_characters=1_000,
            now=NOW,
        )
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            stage="tts",
            requested_target="digest_audio",
            input_fingerprint=deterministic_input_fingerprint("tts-episode-2"),
            idempotency_key="process:tts:episode-2",
            estimated_cost_minor=second_plan.estimated_cost_minor,
            input_artifact_id=second_artifact_id,
            input_artifact_kind="narration_script_zh",
            input_content_hash=second_hash,
            input_language="zh-CN",
            narration_artifact_id=second_artifact_id,
            narration_content_hash=second_hash,
            voice_profile_id="narrator-zh",
        )
        second_claim = claim_next_processing(
            session,
            worker_id="tts-cap-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert second_claim is not None
        with pytest.raises(PodcastProviderQuotaExceeded, match="configured cap"):
            begin_stage_attempt(
                session,
                second_claim,
                input_hash=second_hash,
                settings_fingerprint="f" * 64,
                provider_name="aliyun-isi",
                model_name="long-text-tts",
                provider_revision="2026-09",
                provider_request_key="aliyun-tts-episode-2",
                execution_kind="provider",
                estimated_cost_minor=second_plan.estimated_cost_minor,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key="aliyun-tts-reservation-2",
                provider_usage_plan=second_plan,
                policy=policy,
                now=NOW + dt.timedelta(seconds=6),
            )


def test_concurrent_provider_usage_reservations_are_atomic(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy)
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "provider-quota-episode-2"}
            ),
            idempotency_key="provider-quota:episode-2",
        )
    with Session(engine) as session:
        first = claim_next_processing(
            session,
            worker_id="quota-worker-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    with Session(engine) as session:
        second = claim_next_processing(
            session,
            worker_id="quota-worker-2",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    assert first is not None and second is not None
    barrier = Barrier(2)

    def reserve(item):
        suffix, claim = item
        with Session(engine) as session:
            barrier.wait()
            try:
                return _attempt(
                    session,
                    claim,
                    policy,
                    suffix=suffix,
                    estimate=0,
                    provider_usage_plan=_usage_plan(reserved_units=60),
                ).id
            except PodcastProviderQuotaExceeded:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, (("1", first), ("2", second))))

    assert len([result for result in results if result is not None]) == 1
    with Session(engine) as session:
        reservations = list(session.exec(select(PodcastBudgetReservationRecord)).all())
        assert len(reservations) == 1
        assert reservations[0].reserved_usage_units == 60
        assert reservations[0].provider_quota_limit_units == 100


def test_provider_daily_limit_can_change_without_rotating_quota_scope(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, estimated_cost_minor=0)
        first = claim_next_processing(
            session,
            worker_id="quota-limit-worker-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first is not None
        _attempt(
            session,
            first,
            policy,
            suffix="limit-1",
            estimate=0,
            provider_usage_plan=_usage_plan(reserved_units=60, limit_units=100),
        )

        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "changed-provider-limit"}
            ),
            idempotency_key="provider-quota:changed-limit",
            estimated_cost_minor=0,
        )
        second = claim_next_processing(
            session,
            worker_id="quota-limit-worker-2",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        assert second is not None
        attempt = _attempt(
            session,
            second,
            policy,
            suffix="limit-2",
            estimate=0,
            provider_usage_plan=_usage_plan(reserved_units=60, limit_units=200),
        )
        assert attempt.id


def test_request_unknown_holds_provider_quota_until_not_submitted_release(engine):
    policy = RecordingPolicy({"asr"})
    full_hold = _usage_plan(reserved_units=60, limit_units=60)
    one_more = _usage_plan(reserved_units=1, limit_units=60)
    with Session(engine) as session:
        _enqueue(session, policy)
        first_claim = claim_next_processing(
            session,
            worker_id="unknown-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session,
            first_claim,
            policy,
            estimate=0,
            provider_usage_plan=full_hold,
        )
        assert first_attempt.provider_deadline_at == (
            NOW + dt.timedelta(seconds=3_601)
        ).isoformat(timespec="microseconds")
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            request_unknown=True,
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "held-provider-quota"}
            ),
            idempotency_key="provider-quota:held:episode-2",
            estimated_cost_minor=0,
        )
        second_claim = claim_next_processing(
            session,
            worker_id="blocked-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert second_claim is not None
        with pytest.raises(PodcastProviderQuotaExceeded, match="configured cap"):
            _attempt(
                session,
                second_claim,
                policy,
                suffix="2",
                estimate=0,
                provider_usage_plan=one_more,
            )

    with Session(engine) as session:
        held = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == first_attempt.id
            )
        ).one()
        assert held.status == "reserved"
        assert held.reserved_usage_units == 60
        session.rollback()
        reconcile_provider_request(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            outcome="not_submitted",
            retry_at=NOW + dt.timedelta(seconds=30),
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        released = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == first_attempt.id
            )
        ).one()
        assert released.status == "released"
        assert released.reserved_usage_units == 60
        session.rollback()
        permitted = _attempt(
            session,
            second_claim,
            policy,
            suffix="2",
            estimate=0,
            provider_usage_plan=one_more,
        )
        assert permitted.attempt_no == 1


def test_provider_actual_over_reservation_records_breach_and_freezes_scope(engine):
    policy = RecordingPolicy({"asr"})
    reserved = _usage_plan(
        reserved_units=60,
        limit_units=100,
        unit_price_cny_minor=3_600,
    )
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        first_claim = claim_next_processing(
            session,
            worker_id="overage-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session,
            first_claim,
            policy,
            estimate=60,
            provider_usage_plan=reserved,
        )
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="provider-usage-overage",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        ledger = settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="provider-usage-overage",
            actual_cost_minor=61,
            usage=NormalizedUsage(cost_minor=61, audio_duration_ms=60_001),
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert ledger.actual_usage_units == 61
        assert ledger.provider_quota_breached is True
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == first_attempt.id
            )
        ).one()
        assert reservation.status == "settled"
        assert reservation.actual_usage_units == 61
        assert reservation.provider_quota_breached is True
        session.rollback()

        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "frozen-provider-quota"}
            ),
            idempotency_key="provider-quota:frozen:episode-2",
            estimated_cost_minor=1,
        )
        second_claim = claim_next_processing(
            session,
            worker_id="frozen-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert second_claim is not None
        with pytest.raises(PodcastProviderQuotaExceeded, match="frozen"):
            _attempt(
                session,
                second_claim,
                policy,
                suffix="2",
                estimate=1,
                provider_usage_plan=_usage_plan(
                    reserved_units=1,
                    limit_units=100,
                    unit_price_cny_minor=3_600,
                ),
            )


def test_provider_overage_settlement_serializes_against_new_reservation(
    engine, monkeypatch
):
    policy = RecordingPolicy({"asr"})
    priced_plan = _usage_plan(
        reserved_units=60,
        limit_units=100,
        unit_price_cny_minor=3_600,
    )
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint(
                {"audio_sha256": "settlement-race-episode-2"}
            ),
            idempotency_key="provider-quota:settlement-race:episode-2",
            estimated_cost_minor=1,
            now=NOW + dt.timedelta(microseconds=1),
        )
    with Session(engine) as session:
        first_claim = claim_next_processing(
            session,
            worker_id="settlement-race-1",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    with Session(engine) as session:
        second_claim = claim_next_processing(
            session,
            worker_id="settlement-race-2",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
    assert first_claim is not None and second_claim is not None
    with Session(engine) as session:
        first_attempt = _attempt(
            session,
            first_claim,
            policy,
            estimate=60,
            provider_usage_plan=priced_plan,
        )
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="settlement-race-task",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )

    settlement_holds_lock = Event()
    release_settlement = Event()
    original_actual_units = podcast_processing_service._actual_provider_usage_units

    def pause_after_lock(reservation, usage):
        settlement_holds_lock.set()
        assert release_settlement.wait(timeout=5)
        return original_actual_units(reservation, usage)

    monkeypatch.setattr(
        podcast_processing_service,
        "_actual_provider_usage_units",
        pause_after_lock,
    )

    def settle_overage():
        with Session(engine) as session:
            return settle_attempt_cost(
                session,
                first_claim,
                attempt_id=first_attempt.id,
                settlement_key="settlement-race",
                actual_cost_minor=61,
                usage=NormalizedUsage(cost_minor=61, audio_duration_ms=60_001),
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )

    def reserve_after_overage():
        assert settlement_holds_lock.wait(timeout=5)
        with Session(engine) as session:
            with pytest.raises(PodcastProviderQuotaExceeded, match="frozen"):
                _attempt(
                    session,
                    second_claim,
                    policy,
                    suffix="2",
                    estimate=1,
                    provider_usage_plan=_usage_plan(
                        reserved_units=1,
                        limit_units=100,
                        unit_price_cny_minor=3_600,
                    ),
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        settlement_future = pool.submit(settle_overage)
        reservation_future = pool.submit(reserve_after_overage)
        assert settlement_holds_lock.wait(timeout=5)
        release_settlement.set()
        ledger = settlement_future.result(timeout=5)
        reservation_future.result(timeout=5)
    assert ledger.provider_quota_breached is True


def test_provider_usage_settlement_replay_requires_identical_normalized_usage(engine):
    policy = RecordingPolicy({"asr"})
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript")
        claim = claim_next_processing(
            session,
            worker_id="settlement-replay-worker",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert claim is not None
        attempt = _attempt(
            session,
            claim,
            policy,
            estimate=0,
            provider_usage_plan=_usage_plan(reserved_units=60),
        )
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id="provider-usage-replay",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        with pytest.raises(
            PodcastProviderReconciliationRequired,
            match="cannot be lower",
        ):
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="provider-usage-too-low",
                actual_cost_minor=0,
                usage=NormalizedUsage(cost_minor=0, audio_duration_ms=59_000),
                policy=policy,
                now=NOW + dt.timedelta(seconds=3),
            )
        reserved = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert reserved.status == "reserved"
        assert reserved.actual_usage_units == 0
        session.rollback()
        usage = NormalizedUsage(cost_minor=0, audio_duration_ms=60_000)
        first = settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="provider-usage-replay",
            actual_cost_minor=0,
            usage=usage,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        replay = settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt.id,
            settlement_key="provider-usage-replay",
            actual_cost_minor=0,
            usage=usage,
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert replay.id == first.id
        assert replay.actual_usage_units == 60
        assert session.exec(select(func.count(PodcastCostLedgerRecord.id))).one() == 1
        session.rollback()

        with pytest.raises(PodcastProcessingConflict, match="settled differently"):
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="provider-usage-replay",
                actual_cost_minor=0,
                usage=NormalizedUsage(
                    cost_minor=0,
                    audio_duration_ms=60_000,
                    input_bytes=1,
                ),
                policy=policy,
                now=NOW + dt.timedelta(seconds=6),
            )


def test_settlement_integrity_race_never_reuses_another_attempt_ledger(
    engine, monkeypatch
):
    policy = RecordingPolicy({"asr"})
    plan = _usage_plan(reserved_units=60, limit_units=200)
    usage = NormalizedUsage(cost_minor=0, audio_duration_ms=60_000)
    with Session(engine) as session:
        _enqueue(session, policy, requested_target="transcript", estimated_cost_minor=0)
        first_claim = claim_next_processing(
            session,
            worker_id="settlement-winner",
            lease_seconds=60,
            policy=policy,
            now=NOW,
        )
        assert first_claim is not None
        first_attempt = _attempt(
            session,
            first_claim,
            policy,
            estimate=0,
            provider_usage_plan=plan,
        )
        mark_provider_submission(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            provider_task_id="settlement-winner-task",
            policy=policy,
            now=NOW + dt.timedelta(seconds=2),
        )
        winner = settle_attempt_cost(
            session,
            first_claim,
            attempt_id=first_attempt.id,
            settlement_key="globally-shared-settlement-key",
            actual_cost_minor=0,
            usage=usage,
            policy=policy,
            now=NOW + dt.timedelta(seconds=3),
        )
        _enqueue(
            session,
            policy,
            episode_id="episode-2",
            input_fingerprint=deterministic_input_fingerprint("settlement-loser"),
            idempotency_key="process:settlement-loser",
            requested_target="transcript",
            estimated_cost_minor=0,
        )
        second_claim = claim_next_processing(
            session,
            worker_id="settlement-loser",
            lease_seconds=60,
            policy=policy,
            now=NOW + dt.timedelta(seconds=4),
        )
        assert second_claim is not None
        second_attempt = _attempt(
            session,
            second_claim,
            policy,
            suffix="2",
            estimate=0,
            provider_usage_plan=plan,
        )
        mark_provider_submission(
            session,
            second_claim,
            attempt_id=second_attempt.id,
            provider_task_id="settlement-loser-task",
            policy=policy,
            now=NOW + dt.timedelta(seconds=5),
        )

        lookup_count = 0

        def raced_lookup(_session, *, attempt_id, settlement_key):
            nonlocal lookup_count
            lookup_count += 1
            if lookup_count == 1:
                return None
            return _session.get(PodcastCostLedgerRecord, winner.id)

        def lose_unique_insert():
            raise IntegrityError(
                "INSERT INTO podcast_cost_ledger",
                {},
                RuntimeError("simulated unique settlement-key race"),
            )

        monkeypatch.setattr(
            podcast_processing_service,
            "_existing_ledger",
            raced_lookup,
        )
        monkeypatch.setattr(session, "commit", lose_unique_insert)
        with pytest.raises(PodcastProcessingConflict, match="different debit"):
            settle_attempt_cost(
                session,
                second_claim,
                attempt_id=second_attempt.id,
                settlement_key="globally-shared-settlement-key",
                actual_cost_minor=0,
                usage=usage,
                policy=policy,
                now=NOW + dt.timedelta(seconds=6),
            )

    with Session(engine) as session:
        loser_reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == second_attempt.id
            )
        ).one()
        assert loser_reservation.status == "reserved"
        assert session.exec(select(func.count(PodcastCostLedgerRecord.id))).one() == 1
