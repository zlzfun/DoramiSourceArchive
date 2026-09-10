import asyncio
import datetime as dt
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.podcast_asr_worker as asr_worker  # noqa: E402
import services.podcast_processing as podcast_processing  # noqa: E402
from config import (  # noqa: E402
    AliyunIsiConfig,
    PodcastAsrFetchConfig,
    PodcastConfig,
    PodcastWorkerConfig,
)
from models.db import (  # noqa: E402
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastBudgetReservationRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    SourceConfigRecord,
)
from services.podcast_asr_worker import (  # noqa: E402
    AsrProviderPlan,
    AsrWorkerConfig,
    run_asr_worker_step,
)
from services.aliyun_isi_asr import (  # noqa: E402
    AliyunAsrPollError,
    AliyunAsrSubmissionUnknown,
    AliyunIsiAsrClient,
    AsrPollResult,
    AsrState,
    AsrSubmission,
    AsrTranscript,
    TranscriptSegment,
    TranscriptWord,
)
from services.aliyun_isi_asr_worker import (  # noqa: E402
    AliyunIsiAsrAdapter,
    AliyunIsiAsrWorkerBundle,
    _normalized_transcript,
    aliyun_asr_admission_fingerprint,
    aliyun_asr_identity,
    aliyun_asr_worker_ready,
    register_aliyun_isi_asr_worker,
)
from services.aliyun_isi_auth import AliyunPopClient  # noqa: E402
from services.podcast_asr_fetch_signing import (  # noqa: E402
    PodcastAsrFetchUrlSigner,
)
from services.podcast_processing import (  # noqa: E402
    PodcastProviderQuotaExceeded,
    deterministic_input_fingerprint,
    enqueue_processing,
)
from services.podcast_processing_inputs import (  # noqa: E402
    processing_input_fingerprint,
)
from services.podcast_stage_policy import PodcastStagePolicy  # noqa: E402
from services.podcast_processing_admin import (  # noqa: E402
    PodcastProcessingProviderRegistry,
)
from services.podcast_worker_contracts import (  # noqa: E402
    Accepted,
    ExecutionIdentity,
    ExecutionKind,
    Failure,
    FailureKind,
    NormalizedUsage,
    Pending,
    ProviderUsagePlan,
    ProviderUsageUnit,
    Rejected,
    RemoteAudioOutput,
    StagePlan,
    Succeeded,
    TaskFailed,
    TextOutput,
    Unknown,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


START = dt.datetime(2026, 9, 5, 15, 59, 50, tzinfo=dt.timezone.utc)
TRANSCRIPT = {
    "audio_duration_ms": 60_000,
    "language": "zh-CN",
    "text": "你好，世界。",
    "segments": [
        {
            "start_ms": 0,
            "end_ms": 60_000,
            "text": "你好，世界。",
            "channel": 0,
            "words": [
                {
                    "start_ms": 0,
                    "end_ms": 30_000,
                    "text": "你好",
                    "channel": 0,
                    "confidence": 0.99,
                },
                {
                    "start_ms": 30_000,
                    "end_ms": 60_000,
                    "text": "世界",
                    "channel": 0,
                    "confidence": 0.98,
                },
            ],
        }
    ],
}


class NoProviderUsage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dt.datetime]] = []

    def plan_usage(self, *, identity, input_artifact, audio_duration_ms, now):
        self.calls.append((identity.provider, audio_duration_ms, now))
        return None


class FrozenProviderUsage(NoProviderUsage):
    def plan_usage(self, *, identity, input_artifact, audio_duration_ms, now):
        self.calls.append((identity.provider, audio_duration_ms, now))
        return ProviderUsagePlan(
            quota_scope="fake-asr-daily",
            quota_period="2026-09-05",
            unit=ProviderUsageUnit.AUDIO_SECONDS,
            window_start_at=dt.datetime(2026, 9, 4, 16, 0, tzinfo=dt.timezone.utc),
            window_end_at=dt.datetime(2026, 9, 5, 16, 0, tzinfo=dt.timezone.utc),
            limit_units=7_200,
            reserved_units=(audio_duration_ms + 999) // 1000,
            unit_price_cny_minor=0,
            price_unit_count=1,
            pricing_revision="test-zero-cost-v1",
            deadline_seconds=300,
        )


class FixedProviderUsage(NoProviderUsage):
    def __init__(
        self,
        *,
        quota_period: str,
        window_start_at: dt.datetime,
        window_end_at: dt.datetime,
        limit_units: int,
        unit_price_cny_minor: int = 0,
        price_unit_count: int = 1,
    ) -> None:
        super().__init__()
        self.quota_period = quota_period
        self.window_start_at = window_start_at
        self.window_end_at = window_end_at
        self.limit_units = limit_units
        self.unit_price_cny_minor = unit_price_cny_minor
        self.price_unit_count = price_unit_count

    def plan_usage(self, *, identity, input_artifact, audio_duration_ms, now):
        self.calls.append((identity.provider, audio_duration_ms, now))
        return ProviderUsagePlan(
            quota_scope="fake-asr-daily",
            quota_period=self.quota_period,
            unit=ProviderUsageUnit.AUDIO_SECONDS,
            window_start_at=self.window_start_at,
            window_end_at=self.window_end_at,
            limit_units=self.limit_units,
            reserved_units=(audio_duration_ms + 999) // 1000,
            unit_price_cny_minor=self.unit_price_cny_minor,
            price_unit_count=self.price_unit_count,
            pricing_revision="test-fixed-price-v1",
            deadline_seconds=300,
        )


class FakeAsrProvider:
    def __init__(
        self,
        *,
        submit_outcome,
        poll_outcomes=(),
        provider="fake-asr",
        model="fake-zh-v1",
        revision="test-1",
        estimated_cost_minor=0,
    ) -> None:
        self.submit_outcome = submit_outcome
        self.poll_outcomes = list(poll_outcomes)
        self.events: list[str] = []
        self.request_keys: list[str] = []
        self.poll_identities: list[ExecutionIdentity] = []
        self.estimated_cost_minor = estimated_cost_minor
        self.identity = ExecutionIdentity.from_settings(
            execution_kind=ExecutionKind.PROVIDER,
            provider=provider,
            model=model,
            revision=revision,
            settings={"language": "zh-CN", "punctuation": True},
        )

    def plan(self, input_artifact, *, audio_duration_ms, now):
        self.events.append("plan")
        assert input_artifact.kind == "source_audio"
        assert audio_duration_ms == 60_000
        return AsrProviderPlan(
            identity=self.identity,
            stage=StagePlan(
                estimated_cost_minor=self.estimated_cost_minor,
                poll_interval_seconds=10,
                deadline_seconds=300,
            ),
        )

    def submit(self, context, *, provider_request_key):
        self.events.append("submit")
        self.request_keys.append(provider_request_key)
        assert context.stage == "asr"
        if isinstance(self.submit_outcome, BaseException):
            raise self.submit_outcome
        return self.submit_outcome

    def supports(self, identity):
        return identity.provider == self.identity.provider

    def poll(self, *, task_id, identity, audio_duration_ms, reserved_cost_minor):
        self.events.append("poll")
        self.poll_identities.append(identity)
        assert task_id == "fake-task-1"
        assert audio_duration_ms == 60_000
        assert reserved_cost_minor == 0
        return self.poll_outcomes.pop(0)


@pytest.fixture()
def worker_env(tmp_path):
    storage = DatabaseStorage(f"sqlite:///{tmp_path / 'asr-worker.db'}")
    stamp = START.isoformat(timespec="microseconds")
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
        for number, content_hash in ((1, "a" * 64), (2, "b" * 64)):
            episode_id = f"episode-{number}"
            session.add(
                ArticleRecord(
                    id=episode_id,
                    title=f"Episode {number}",
                    content_type="podcast_episode",
                    source_id="podcast-source",
                    source_url=f"https://example.test/episodes/{number}",
                    publish_date=stamp,
                    fetched_date=stamp,
                    content="show notes",
                )
            )
            session.add(
                PodcastArtifactRecord(
                    id=f"source-audio-{number}",
                    episode_id=episode_id,
                    kind="source_audio",
                    content_hash=content_hash,
                    mime="audio/mpeg",
                    ext="mp3",
                    size_bytes=1024,
                    duration_seconds=60,
                    status="ready",
                    expires_at="2099-01-01T00:00:00.000000+00:00",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        session.commit()
    policy = PodcastStagePolicy(
        PodcastConfig(
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
    )
    config = AsrWorkerConfig(
        worker_id="fake-asr-worker",
        lease_seconds=30,
        fallback_retry_seconds=15,
        next_stage_by_target={"transcript": None, "digest_blog": "translate"},
    )
    yield storage.engine, policy, config
    storage.engine.dispose()


def _enqueue(
    session: Session,
    policy: PodcastStagePolicy,
    *,
    episode_number: int = 1,
    stage: str = "asr",
    target: str = "transcript",
    queued_at: dt.datetime = START,
    artifact_id: str | None = None,
    estimated_cost_minor: int = 0,
):
    content_hash = ("a" if episode_number == 1 else "b") * 64
    bound_artifact_id = artifact_id or f"source-audio-{episode_number}"
    aliyun = getattr(policy, "aliyun_isi", None)
    input_fingerprint = (
        processing_input_fingerprint(
            episode_id=f"episode-{episode_number}",
            entry_stage=stage,
            artifact_id=bound_artifact_id,
            content_hash=content_hash,
            kind="source_audio",
            language="und",
            audio_duration_ms=60_000,
            admission_fingerprint=aliyun_asr_admission_fingerprint(aliyun),
        )
        if aliyun is not None and stage == "asr"
        else deterministic_input_fingerprint(
            {
                "artifact_id": bound_artifact_id,
                "episode": episode_number,
                "stage": stage,
            }
        )
    )
    return enqueue_processing(
        session,
        episode_id=f"episode-{episode_number}",
        stage=stage,
        input_fingerprint=input_fingerprint,
        pipeline_version="podcast-worker-test-v1",
        policy_version="worker-policy-v1",
        requested_target=target,
        idempotency_key=f"worker:{episode_number}:{stage}:{bound_artifact_id}",
        estimated_cost_minor=estimated_cost_minor,
        input_artifact_id=bound_artifact_id,
        input_artifact_kind="source_audio",
        input_content_hash=content_hash,
        input_language="und",
        budget_scope="podcast-paid-processing",
        budget_period="2026-09",
        budget_limit_minor=100,
        per_run_budget_minor=100,
        policy=policy,
        now=queued_at,
    )


def _success(task_id="fake-task-1"):
    return Succeeded(
        task_id,
        TextOutput(
            json.dumps(TRANSCRIPT, ensure_ascii=False),
            mime_type="application/json",
            language="zh-CN",
        ),
        NormalizedUsage(
            cost_minor=0,
            audio_duration_ms=60_000,
            input_bytes=1024,
            output_bytes=len(json.dumps(TRANSCRIPT, ensure_ascii=False).encode()),
        ),
    )


def test_fake_asr_e2e_restarts_across_midnight_and_commits_one_attempt(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[Pending("fake-task-1", 10), _success()],
    )
    usage = FrozenProviderUsage()
    events = adapter.events
    real_authorize = asr_worker.authorize_provider_call

    def recording_authorize(*args, **kwargs):
        events.append("authorize")
        return real_authorize(*args, **kwargs)

    monkeypatch.setattr(asr_worker, "authorize_provider_call", recording_authorize)
    with Session(engine) as session:
        process = _enqueue(session, policy)
        first = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START,
        )
        assert first.action == "poll_scheduled"
        assert events[-2:] == ["authorize", "submit"]
    with Session(engine) as session:
        pending = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert pending.action == "poll_scheduled"
    with Session(engine) as session:
        completed = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=20),
        )
        assert completed.action == "completed"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempts = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).all()
        artifacts = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == process.id
            )
        ).all()
        ledgers = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.processing_id == process.id
            )
        ).all()
        assert persisted is not None and persisted.processing_status == "ready"
        assert len(attempts) == len(artifacts) == len(ledgers) == 1
        assert attempts[0].submission_state == "succeeded"
        assert ledgers[0].provider_quota_scope == "fake-asr-daily"
        assert ledgers[0].provider_quota_period == "2026-09-05"
        assert ledgers[0].provider_quota_unit == "audio_seconds"
        assert ledgers[0].actual_usage_units == 60
        assert json.loads(artifacts[0].inline_text)["text"] == "你好，世界。"
    # The second step occurs after midnight in the configured provider timezone.
    # A submitted task is polled from frozen state; no current-window reservation
    # is regenerated during either restart.
    assert len(usage.calls) == 1
    assert usage.calls[0][1] == 60_000
    assert events.count("submit") == 1
    assert events.count("poll") == 2


def test_request_unknown_with_task_id_is_polled_then_reconciled(worker_env):
    engine, policy, config = worker_env
    failure = Failure(
        FailureKind.REQUEST_UNKNOWN,
        "ambiguous_submit",
        "provider accepted but response status was ambiguous",
        retryable=False,
    )
    adapter = FakeAsrProvider(
        submit_outcome=Unknown(failure, "fake-task-1"),
        poll_outcomes=[_success()],
    )
    usage = NoProviderUsage()
    with Session(engine) as session:
        process = _enqueue(session, policy)
        submitted = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START,
        )
        assert submitted.action == "poll_scheduled"
    with Session(engine) as session:
        completed = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert completed.action == "completed"
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        assert attempt.submission_state == "succeeded"
        assert attempt.request_unknown is False
    assert adapter.events.count("submit") == 1


def test_submitted_attempt_drains_after_input_is_revoked(worker_env):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[_success()],
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        assert (
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=NoProviderUsage(),
                config=config,
                policy=policy,
                now=START,
            ).action
            == "poll_scheduled"
        )
        source_audio = session.get(PodcastArtifactRecord, "source-audio-1")
        assert source_audio is not None
        source_audio.status = "withdrawn"
        session.add(source_audio)
        session.commit()
    with Session(engine) as session:
        drained = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert drained.action == "not_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        reservation = session.exec(select(PodcastBudgetReservationRecord)).one()
        assert persisted is not None
        assert persisted.eligibility_status == "invalid_input"
        assert persisted.processing_status == "not_required"
        assert reservation.status == "settled"
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []
    assert adapter.events.count("submit") == 1
    assert adapter.events.count("poll") == 1


def test_restart_replays_materialized_success_after_crash_before_settlement(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[_success(), _success()],
    )
    usage = NoProviderUsage()
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START,
        )

    real_settle = asr_worker.settle_attempt_cost
    crash_once = True

    def crash_before_settlement(*args, **kwargs):
        nonlocal crash_once
        if crash_once:
            crash_once = False
            raise RuntimeError("simulated process crash")
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(asr_worker, "settle_attempt_cost", crash_before_settlement)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="simulated process crash"):
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=usage,
                config=config,
                policy=policy,
                now=START + dt.timedelta(seconds=10),
            )
    with Session(engine) as session:
        materialized = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == process.id
            )
        ).all()
        assert len(materialized) == 1

    # The first worker's lease expires. A fresh worker polls the same remote
    # task, replays the identical transcript materialization, then settles and
    # commits without another provider submission or duplicate audit rows.
    with Session(engine) as session:
        completed = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=41),
        )
        assert completed.action == "completed"
        attempts = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).all()
        artifacts = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == process.id
            )
        ).all()
        ledgers = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.processing_id == process.id
            )
        ).all()
        assert len(attempts) == len(artifacts) == len(ledgers) == 1
        assert attempts[0].submission_state == "succeeded"
    assert adapter.events.count("submit") == 1
    assert adapter.events.count("poll") == 2
    assert len(usage.calls) == 1


@pytest.mark.parametrize(
    "submit_outcome",
    [
        Unknown(
            Failure(
                FailureKind.REQUEST_UNKNOWN,
                "transport_unknown",
                "provider outcome cannot be confirmed",
                retryable=False,
            )
        ),
        RuntimeError("must never be persisted"),
    ],
)
def test_submit_without_durable_task_identity_parks_and_holds_budget(
    worker_env, submit_outcome
):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(submit_outcome=submit_outcome)
    with Session(engine) as session:
        process = _enqueue(session, policy)
        parked = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
        assert parked.action == "reconciliation_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert persisted is not None
        assert persisted.processing_status == "reconciliation_required"
        assert attempt.submission_state == "reconciling"
        assert attempt.request_unknown is True
        assert reservation.status == "reserved"
        assert "must never be persisted" not in persisted.error_message


def test_retryable_submit_rejection_releases_hold_for_configured_retry(worker_env):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(
        submit_outcome=Rejected(
            Failure(
                FailureKind.TRANSIENT,
                "provider_busy",
                "Authorization: Bearer SUPER-SECRET Signature=LEAK",
                retryable=True,
                retry_after_seconds=7,
            )
        )
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
        assert result.action == "retry_wait"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert persisted is not None
        assert persisted.next_retry_at == (START + dt.timedelta(seconds=7)).isoformat(
            timespec="microseconds"
        )
        assert attempt.submission_state == "failed_retryable"
        assert persisted.error_message == "ASR provider rejected the submission"
        assert "SUPER-SECRET" not in attempt.error_message
        assert reservation.status == "released"


def test_restart_polls_persisted_identity_after_provider_config_rotation(worker_env):
    engine, policy, config = worker_env
    original = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        model="fake-zh-v1",
        revision="test-1",
    )
    rotated = FakeAsrProvider(
        submit_outcome=Accepted("unused"),
        poll_outcomes=[_success()],
        model="fake-zh-v2",
        revision="test-2",
    )
    with Session(engine) as session:
        _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=original,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
    with Session(engine) as session:
        completed = run_asr_worker_step(
            session,
            adapter=rotated,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert completed.action == "completed"
    assert rotated.events == ["poll"]
    assert rotated.poll_identities == [original.identity]


def test_restart_parks_mismatched_provider_adapter_without_polling(worker_env):
    engine, policy, config = worker_env
    original = FakeAsrProvider(submit_outcome=Accepted("fake-task-1"))
    mismatched = FakeAsrProvider(
        submit_outcome=Accepted("unused"),
        poll_outcomes=[_success()],
        provider="other-asr",
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=original,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
    with Session(engine) as session:
        parked = run_asr_worker_step(
            session,
            adapter=mismatched,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert parked.action == "reconciliation_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.error_code == "provider_adapter_unavailable"
    assert mismatched.events == []
    assert mismatched.poll_identities == []


def test_expired_provider_deadline_parks_without_plan_or_poll(worker_env):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(submit_outcome=Accepted("fake-task-1"))
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
    with Session(engine) as session:
        parked = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=301),
        )
        assert parked.action == "reconciliation_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.error_code == "provider_deadline_elapsed"
    assert adapter.events == ["plan", "submit"]


def test_expiring_source_audio_fails_before_authorization_or_provider_io(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(submit_outcome=Accepted("fake-task-1"))
    authorization_calls = 0
    real_authorize = asr_worker.authorize_provider_call

    def recording_authorize(*args, **kwargs):
        nonlocal authorization_calls
        authorization_calls += 1
        return real_authorize(*args, **kwargs)

    monkeypatch.setattr(asr_worker, "authorize_provider_call", recording_authorize)
    with Session(engine) as session:
        stamp = START.isoformat(timespec="microseconds")
        session.add(
            PodcastArtifactRecord(
                id="source-audio-expiring",
                episode_id="episode-1",
                kind="source_audio",
                content_hash="a" * 64,
                mime="audio/mpeg",
                ext="mp3",
                size_bytes=1024,
                duration_seconds=60,
                status="ready",
                expires_at=(START + dt.timedelta(seconds=299)).isoformat(
                    timespec="microseconds"
                ),
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.commit()
        process = _enqueue(session, policy, artifact_id="source-audio-expiring")
        result = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
        assert result.action == "failed"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempts = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).all()
        reservations = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.processing_id == process.id
            )
        ).all()
        assert persisted is not None
        assert persisted.error_code == "source_audio_expiry_insufficient"
        assert persisted.attempt_count == 0
        assert attempts == []
        assert reservations == []
    assert adapter.events == ["plan"]
    assert authorization_calls == 0


def test_task_failure_settlement_replays_after_crash_before_failure_commit(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    failure = Failure(
        FailureKind.TERMINAL,
        "provider_task_failed",
        "Authorization: Bearer SUPER-SECRET Signature=LEAK",
        retryable=False,
    )
    failed_outcome = TaskFailed(
        "fake-task-1",
        failure,
        NormalizedUsage(cost_minor=0, audio_duration_ms=60_000),
    )
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[failed_outcome, failed_outcome],
    )
    usage = FrozenProviderUsage()
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START,
        )

    real_fail = asr_worker.fail_stage_attempt
    crash_once = True

    def crash_after_settlement(*args, **kwargs):
        nonlocal crash_once
        if crash_once:
            crash_once = False
            raise RuntimeError("simulated failure-commit crash")
        return real_fail(*args, **kwargs)

    monkeypatch.setattr(asr_worker, "fail_stage_attempt", crash_after_settlement)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="failure-commit crash"):
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=usage,
                config=config,
                policy=policy,
                now=START + dt.timedelta(seconds=10),
            )
    with Session(engine) as session:
        result = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=41),
        )
        assert result.action == "failed"
        persisted = session.get(PodcastProcessingRecord, process.id)
        ledgers = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.processing_id == process.id
            )
        ).all()
        assert persisted is not None
        assert persisted.error_message == "ASR provider task failed"
        assert "SUPER-SECRET" not in persisted.error_message
        assert len(ledgers) == 1
        assert ledgers[0].actual_usage_units == 60
    assert adapter.events.count("submit") == 1
    assert adapter.events.count("poll") == 2
    assert len(usage.calls) == 1


def test_invalid_success_output_is_settled_then_parked_without_repoll(worker_env):
    engine, policy, config = worker_env
    invalid_success = Succeeded(
        "fake-task-1",
        RemoteAudioOutput(
            "https://provider.example.test/unexpected.wav",
            mime_type="audio/wav",
        ),
        NormalizedUsage(cost_minor=0, audio_duration_ms=60_000),
    )
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[invalid_success],
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=FrozenProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
    with Session(engine) as session:
        parked = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=FrozenProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert parked.action == "reconciliation_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        ledger = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.processing_id == process.id
            )
        ).one()
        assert persisted is not None
        assert persisted.error_code == "normalized_transcript_invalid"
        assert "provider.example.test" not in persisted.error_message
        assert ledger.actual_usage_units == 60
    assert adapter.events.count("poll") == 1


def test_terminal_usage_mismatch_parks_without_repolling(worker_env):
    engine, policy, config = worker_env
    inconsistent = TaskFailed(
        "fake-task-1",
        Failure(
            FailureKind.TERMINAL,
            "provider_task_failed",
            "provider reported a terminal failure",
            retryable=False,
        ),
        NormalizedUsage(cost_minor=0, audio_duration_ms=0),
    )
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[inconsistent],
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=FrozenProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
    with Session(engine) as session:
        parked = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=FrozenProviderUsage(),
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=10),
        )
        assert parked.action == "reconciliation_required"
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.error_code == "provider_usage_reconciliation_required"
    with Session(engine) as session:
        assert (
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=FrozenProviderUsage(),
                config=config,
                policy=policy,
                now=START + dt.timedelta(seconds=41),
            ).action
            == "idle"
        )
    assert adapter.events.count("poll") == 1


def test_pre_call_authorization_rejection_releases_without_provider_io(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(submit_outcome=Accepted("fake-task-1"))

    def reject_before_network(*_args, **_kwargs):
        raise PodcastProviderQuotaExceeded("window is closing")

    monkeypatch.setattr(asr_worker, "authorize_provider_call", reject_before_network)
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
        assert result.action == "retry_wait"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert persisted is not None
        assert persisted.next_retry_at == (
            START + dt.timedelta(seconds=config.fallback_retry_seconds)
        ).isoformat(timespec="microseconds")
        assert attempt.submission_state == "failed_retryable"
        assert reservation.status == "released"
    assert "submit" not in adapter.events


def test_asr_runner_does_not_claim_an_earlier_non_asr_row(worker_env):
    engine, policy, config = worker_env
    adapter = FakeAsrProvider(submit_outcome=Accepted("fake-task-1"))
    with Session(engine) as session:
        translate = _enqueue(
            session,
            policy,
            episode_number=2,
            stage="translate",
            target="digest_blog",
            queued_at=START - dt.timedelta(seconds=1),
        )
        asr = _enqueue(session, policy)
        result = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=NoProviderUsage(),
            config=config,
            policy=policy,
            now=START,
        )
        assert result.processing_id == asr.id
        untouched = session.get(PodcastProcessingRecord, translate.id)
        assert untouched is not None
        assert untouched.processing_status == "queued"
        assert untouched.lease_owner is None


def test_quota_exhaustion_before_attempt_releases_claim_until_frozen_window_end(
    worker_env,
):
    engine, policy, config = worker_env
    window_end = START + dt.timedelta(hours=1)
    exhausted_usage = FixedProviderUsage(
        quota_period="2026-09-05",
        window_start_at=START - dt.timedelta(minutes=1),
        window_end_at=window_end,
        limit_units=60,
    )
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        poll_outcomes=[_success()],
    )

    # Consume the complete provider allowance with one settled task.
    with Session(engine) as session:
        _enqueue(session, policy, episode_number=1)
        assert (
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=exhausted_usage,
                config=config,
                policy=policy,
                now=START,
            ).action
            == "poll_scheduled"
        )
    with Session(engine) as session:
        assert (
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=exhausted_usage,
                config=config,
                policy=policy,
                now=START + dt.timedelta(seconds=10),
            ).action
            == "completed"
        )

    with Session(engine) as session:
        process = _enqueue(
            session,
            policy,
            episode_number=2,
            queued_at=START + dt.timedelta(seconds=11),
        )
        deferred = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=exhausted_usage,
            config=config,
            policy=policy,
            now=START + dt.timedelta(seconds=11),
        )
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert deferred.action == "retry_wait"
        assert persisted is not None
        assert persisted.processing_status == "retry_wait"
        assert persisted.next_retry_at == window_end.isoformat(timespec="microseconds")
        assert persisted.lease_owner is None
        assert persisted.lease_token is None
        assert persisted.lease_expires_at is None
        assert persisted.attempt_count == 0
        assert persisted.error_code == "provider_usage_window_unavailable"
        assert "configured cap" not in persisted.error_message
        assert (
            session.exec(
                select(PodcastStageAttemptRecord).where(
                    PodcastStageAttemptRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastBudgetReservationRecord).where(
                    PodcastBudgetReservationRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastCostLedgerRecord).where(
                    PodcastCostLedgerRecord.processing_id == process.id
                )
            ).all()
            == []
        )
    assert adapter.events.count("submit") == 1

    # The same processing row is eligible again in the next trusted window.
    recovered_usage = FixedProviderUsage(
        quota_period="2026-09-06",
        window_start_at=window_end,
        window_end_at=window_end + dt.timedelta(days=1),
        limit_units=60,
    )
    with Session(engine) as session:
        resumed = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=recovered_usage,
            config=config,
            policy=policy,
            now=window_end,
        )
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempts = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).all()
        reservations = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.processing_id == process.id
            )
        ).all()
        assert resumed.action == "poll_scheduled"
        assert persisted is not None and persisted.attempt_count == 1
        assert len(attempts) == len(reservations) == 1
    assert adapter.events.count("submit") == 2


def test_price_increase_beyond_enqueued_estimate_fails_without_attempt_or_lease(
    worker_env,
):
    engine, policy, config = worker_env
    priced_usage = FixedProviderUsage(
        quota_period="2026-09-05",
        window_start_at=START - dt.timedelta(minutes=1),
        window_end_at=START + dt.timedelta(hours=1),
        limit_units=7_200,
        unit_price_cny_minor=1,
        price_unit_count=60,
    )
    adapter = FakeAsrProvider(
        submit_outcome=Accepted("fake-task-1"),
        estimated_cost_minor=1,
    )
    with Session(engine) as session:
        process = _enqueue(
            session,
            policy,
            estimated_cost_minor=0,
        )
        failed = run_asr_worker_step(
            session,
            adapter=adapter,
            usage_planner=priced_usage,
            config=config,
            policy=policy,
            now=START,
        )
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert failed.action == "failed"
        assert persisted is not None
        assert persisted.processing_status == "failed"
        assert persisted.lease_owner is None
        assert persisted.lease_token is None
        assert persisted.lease_expires_at is None
        assert persisted.next_retry_at is None
        assert persisted.finished_at == START.isoformat(timespec="microseconds")
        assert persisted.attempt_count == 0
        assert persisted.error_code == "provider_budget_unavailable"
        assert "trusted configuration" not in persisted.error_message
        assert (
            session.exec(
                select(PodcastStageAttemptRecord).where(
                    PodcastStageAttemptRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastBudgetReservationRecord).where(
                    PodcastBudgetReservationRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastCostLedgerRecord).where(
                    PodcastCostLedgerRecord.processing_id == process.id
                )
            ).all()
            == []
        )
    with Session(engine) as session:
        assert (
            run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=priced_usage,
                config=config,
                policy=policy,
                now=START + dt.timedelta(seconds=config.lease_seconds + 1),
            ).action
            == "idle"
        )
    assert "submit" not in adapter.events


def test_pre_attempt_resolution_cas_preserves_concurrent_attempt(
    worker_env, monkeypatch
):
    engine, policy, config = worker_env
    with Session(engine) as resolver_session:
        process = _enqueue(resolver_session, policy)
        claim = podcast_processing.claim_next_processing(
            resolver_session,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
            policy=policy,
            now=START,
        )
        assert claim is not None

        real_latest = podcast_processing._latest_active_attempt
        interleaved_attempt_id: str | None = None

        def begin_after_resolver_read(session, processing_id):
            nonlocal interleaved_attempt_id
            if session is resolver_session and interleaved_attempt_id is None:
                # End SQLite's read snapshot while retaining the resolver's
                # already-captured attempt_count, then let the cloned claim win.
                session.rollback()
                with Session(engine) as competing_session:
                    attempt = podcast_processing.begin_stage_attempt(
                        competing_session,
                        claim,
                        input_hash="a" * 64,
                        settings_fingerprint="c" * 64,
                        provider_name="fake-asr",
                        model_name="fake-zh-v1",
                        provider_revision="test-1",
                        provider_request_key=f"request:{process.id}",
                        execution_kind="provider",
                        estimated_cost_minor=0,
                        budget_scope="podcast-paid-processing",
                        budget_period="2026-09",
                        budget_limit_minor=100,
                        reservation_idempotency_key=(f"reservation:{process.id}"),
                        policy=policy,
                        now=START,
                    )
                    interleaved_attempt_id = attempt.id
                # Simulate the original no-active-attempt read completing just
                # before the concurrent transaction committed.
                return None
            return real_latest(session, processing_id)

        monkeypatch.setattr(
            podcast_processing,
            "_latest_active_attempt",
            begin_after_resolver_read,
        )
        with pytest.raises(podcast_processing.PodcastLeaseLost):
            podcast_processing.resolve_claim_before_attempt(
                resolver_session,
                claim,
                error_code="provider_usage_window_unavailable",
                redacted_error_message="provider usage capacity unavailable",
                retryable=True,
                retry_at=START + dt.timedelta(minutes=1),
                policy=policy,
                now=START,
            )

    assert interleaved_attempt_id is not None
    with Session(engine) as session:
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.get(PodcastStageAttemptRecord, interleaved_attempt_id)
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == interleaved_attempt_id
            )
        ).one()
        assert persisted is not None
        assert persisted.processing_status == "running"
        assert persisted.lease_token == claim.lease_token
        assert persisted.attempt_count == 1
        assert attempt is not None and attempt.submission_state == "prepared"
        assert reservation.status == "reserved"


def test_begin_after_pre_attempt_resolution_is_rejected_by_fence(worker_env):
    engine, policy, config = worker_env
    with Session(engine) as session:
        process = _enqueue(session, policy, episode_number=2)
        claim = podcast_processing.claim_next_processing(
            session,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
            policy=policy,
            now=START,
        )
        assert claim is not None
        resolved = podcast_processing.resolve_claim_before_attempt(
            session,
            claim,
            error_code="provider_usage_window_unavailable",
            redacted_error_message="provider usage capacity unavailable",
            retryable=True,
            retry_at=START + dt.timedelta(minutes=1),
            policy=policy,
            now=START,
        )
        assert resolved.processing_status == "retry_wait"
        with pytest.raises(podcast_processing.PodcastLeaseLost):
            podcast_processing.begin_stage_attempt(
                session,
                claim,
                input_hash="b" * 64,
                settings_fingerprint="c" * 64,
                provider_name="fake-asr",
                model_name="fake-zh-v1",
                provider_revision="test-1",
                provider_request_key=f"request:{process.id}",
                execution_kind="provider",
                estimated_cost_minor=0,
                budget_scope="podcast-paid-processing",
                budget_period="2026-09",
                budget_limit_minor=100,
                reservation_idempotency_key=f"reservation:{process.id}",
                policy=policy,
                now=START,
            )
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert persisted is not None
        assert persisted.processing_status == "retry_wait"
        assert persisted.lease_token is None
        assert persisted.attempt_count == 0
        assert (
            session.exec(
                select(PodcastStageAttemptRecord).where(
                    PodcastStageAttemptRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastBudgetReservationRecord).where(
                    PodcastBudgetReservationRecord.processing_id == process.id
                )
            ).all()
            == []
        )


def _aliyun_config(**updates):
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
        "asr_entitlement_ends_at": "2026-12-01T00:00:00+08:00",
        "asr_provider_deadline_seconds": 300,
        "asr_price_cny_minor_per_hour": 0,
        "asr_pricing_revision": "test-zero-cost-v1",
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def _asr_signer(policy: PodcastStagePolicy):
    return PodcastAsrFetchUrlSigner(
        PodcastAsrFetchConfig(
            public_base_url=(
                "https://archive.example.test/api/public/podcast-asr/source-audio"
            ),
            signing_secret="test-only-signing-secret-at-least-32-bytes",
            # Successful submit grants must cover the frozen 300-second
            # provider deadline, not merely the 5-second HTTP timeout.
            url_ttl_seconds=600,
            min_remaining_seconds=30,
        ),
        authority_id=policy.config.authority_id,
    )


def test_aliyun_bundle_scheduler_mock_transport_runs_signed_asr_e2e(
    worker_env, monkeypatch
):
    import api.app as app_module
    import services.aliyun_isi_asr_worker as aliyun_worker

    engine, base_policy, _config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    signer = _asr_signer(policy)
    observed_urls: list[str] = []
    observed_configs: list[AliyunIsiConfig] = []
    usage_configs: list[AliyunIsiConfig] = []
    http_clients: list[httpx.Client] = []
    current = [START]

    def handler(request: httpx.Request):
        if request.method == "POST":
            form = parse_qs(request.content.decode("utf-8"))
            task = json.loads(form["Task"][0])
            observed_urls.append(task["file_link"])
            return httpx.Response(
                200,
                json={
                    "TaskId": "aliyun-task-1",
                    "StatusCode": 21050000,
                    "StatusText": "SUCCESS",
                },
            )
        assert request.method == "GET"
        assert request.url.params["Action"] == "GetTaskResult"
        return httpx.Response(
            200,
            json={
                "TaskId": "aliyun-task-1",
                "StatusCode": 21050000,
                "StatusText": "SUCCESS",
                "BizDuration": 60_000,
                "Result": {
                    "Sentences": [
                        {
                            "BeginTime": 0,
                            "EndTime": 60_000,
                            "Text": "你好，世界。",
                            "ChannelId": 0,
                        }
                    ],
                    "Words": [
                        {
                            "BeginTime": 0,
                            "EndTime": 30_000,
                            "Word": "你好",
                            "ChannelId": 0,
                        },
                        {
                            "BeginTime": 30_000,
                            "EndTime": 60_000,
                            "Word": "世界",
                            "ChannelId": 0,
                        },
                    ],
                },
            },
        )

    def client_factory(snapshot):
        observed_configs.append(snapshot)
        http = httpx.Client(transport=httpx.MockTransport(handler))
        http_clients.append(http)
        return AliyunIsiAsrClient(
            snapshot,
            pop_client=AliyunPopClient(
                snapshot,
                http_client=http,
                nonce_factory=lambda: "test-nonce",
                clock=lambda: current[0],
            ),
        )

    signer_calls = 0

    def signer_resolver(session, *, podcast_config):
        nonlocal signer_calls
        signer_calls += 1
        assert session.in_transaction() is False
        assert podcast_config is policy.config
        return signer

    real_usage_plan = aliyun_worker.asr_usage_plan

    def recording_usage_plan(snapshot, **kwargs):
        usage_configs.append(snapshot)
        return real_usage_plan(snapshot, **kwargs)

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=client_factory,
        signer_resolver=signer_resolver,
        clock=lambda: current[0],
        transcript_language="zh-CN",
    )
    registry = PodcastProcessingProviderRegistry()
    register_aliyun_isi_asr_worker(registry, bundle=bundle)
    configured = replace(
        app_module.settings,
        podcast=policy.config,
        podcast_worker=PodcastWorkerConfig(
            tick_seconds=10,
            lease_seconds=30,
            heartbeat_seconds=10,
            fallback_retry_seconds=15,
            max_steps_per_tick=1,
        ),
        aliyun_isi=aliyun,
    )
    monkeypatch.setattr(app_module, "settings", configured)
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)
    monkeypatch.setattr(
        app_module.aliyun_isi_config_service,
        "resolve_config",
        lambda _session: aliyun,
    )
    monkeypatch.setattr(aliyun_worker, "asr_usage_plan", recording_usage_plan)

    with Session(engine) as session:
        process = _enqueue(session, policy)
    try:
        assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == (
            "poll_scheduled",
        )
        current[0] += dt.timedelta(seconds=10)
        assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == (
            "completed",
        )
    finally:
        for client in http_clients:
            client.close()

    assert observed_configs == [aliyun, aliyun]
    assert usage_configs == [aliyun, aliyun]
    assert signer_calls == 2
    assert len(observed_urls) == 1
    assert observed_urls[0].startswith(
        "https://archive.example.test/api/public/podcast-asr/source-audio?"
    )
    assert "payload=" in observed_urls[0] and "signature=" in observed_urls[0]
    with Session(engine) as session:
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        artifact = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == process.id
            )
        ).one()
        ledger = session.exec(
            select(PodcastCostLedgerRecord).where(
                PodcastCostLedgerRecord.processing_id == process.id
            )
        ).one()
        assert persisted is not None and persisted.processing_status == "ready"
        assert attempt.provider_name == "aliyun-isi"
        assert attempt.provider_task_id == "aliyun-task-1"
        assert ledger.actual_usage_units == 60
        assert json.loads(artifact.inline_text)["text"] == "你好，世界。"
        persisted_text = "\n".join(
            (
                attempt.provider_task_id,
                attempt.usage_json,
                artifact.inline_text,
                artifact.provenance_json,
            )
        )
        assert observed_urls[0] not in persisted_text


def test_aliyun_bundle_missing_signer_retries_without_provider_submit(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    submit_calls = 0

    class NoSubmitClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            pytest.fail("missing signer must reject before provider submit")

        def poll(self, _task_id):
            pytest.fail("new attempt must not poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: NoSubmitClient(),
        signer_resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("signing is not configured")
        ),
        clock=lambda: START,
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = bundle(session, config=config, policy=policy)
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert result.action == "retry_wait"
        assert persisted is not None
        assert persisted.processing_status == "retry_wait"
        assert persisted.lease_owner is None
        assert dt.datetime.fromisoformat(persisted.next_retry_at) == (
            START + dt.timedelta(seconds=aliyun.asr_poll_interval_seconds)
        )
        assert attempt.submission_state == "failed_retryable"
        assert attempt.error_code == "aliyun_fetch_signer_unavailable"
        assert reservation.status == "released"
        assert submit_calls == 0


def test_aliyun_bundle_signing_error_releases_without_unknown_submit(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    submit_calls = 0
    sensitive_error = "invalid local signing input secret=must-not-persist"

    class BrokenSigner:
        def issue(self, **_kwargs):
            raise ValueError(sensitive_error)

    class NoSubmitClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            pytest.fail("signing failure must reject before provider submit")

        def poll(self, _task_id):
            pytest.fail("new attempt must not poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: NoSubmitClient(),
        signer_resolver=lambda _session, **_kwargs: BrokenSigner(),
        clock=lambda: START,
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = bundle(session, config=config, policy=policy)
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert result.action == "retry_wait"
        assert persisted is not None
        assert persisted.processing_status == "retry_wait"
        assert attempt.submission_state == "failed_retryable"
        assert attempt.request_unknown is False
        assert attempt.error_code == "aliyun_fetch_signing_failed"
        assert reservation.status == "released"
        assert sensitive_error not in "\n".join(
            filter(
                None,
                (
                    attempt.error_code,
                    attempt.error_message,
                    persisted.error_code,
                    persisted.error_message,
                ),
            )
        )
        assert submit_calls == 0


def test_aliyun_bundle_missing_app_key_rejects_before_network(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config(app_key="")
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    signer = _asr_signer(policy)
    submit_calls = 0

    class NoSubmitClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            pytest.fail("missing AppKey must reject before provider submit")

        def poll(self, _task_id):
            pytest.fail("new attempt must not poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: NoSubmitClient(),
        signer_resolver=lambda _session, **_kwargs: signer,
        clock=lambda: START,
    )
    assert aliyun_asr_worker_ready(aliyun) is True
    assert aliyun.asr_poll_configured is True
    assert aliyun.asr_configured is False
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = bundle(session, config=config, policy=policy)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert result.action == "retry_wait"
        assert attempt.submission_state == "failed_retryable"
        assert attempt.request_unknown is False
        assert attempt.error_code == "aliyun_submit_app_key_unavailable"
        assert reservation.status == "released"
        assert submit_calls == 0


def test_aliyun_submit_caps_fetch_url_to_shorter_source_expiry(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config(asr_provider_deadline_seconds=300)
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    signer = PodcastAsrFetchUrlSigner(
        PodcastAsrFetchConfig(
            public_base_url=(
                "https://archive.example.test/api/public/podcast-asr/source-audio"
            ),
            signing_secret="bounded-expiry-test-signing-secret-000000",
            url_ttl_seconds=900,
            min_remaining_seconds=30,
        ),
        authority_id=policy.config.authority_id,
    )
    submitted_url = ""

    class CaptureClient:
        def submit(self, file_url):
            nonlocal submitted_url
            submitted_url = file_url
            return AsrSubmission("aliyun-task-bounded-source", 21050000)

        def poll(self, _task_id):
            pytest.fail("first step only submits")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: CaptureClient(),
        signer_resolver=lambda _session, **_kwargs: signer,
        clock=lambda: START,
    )
    source_expiry = START + dt.timedelta(seconds=400)
    with Session(engine) as session:
        stamp = START.isoformat(timespec="microseconds")
        session.add(
            PodcastArtifactRecord(
                id="source-audio-bounded",
                episode_id="episode-1",
                kind="source_audio",
                content_hash="a" * 64,
                mime="audio/mpeg",
                ext="mp3",
                size_bytes=1024,
                duration_seconds=60,
                status="ready",
                expires_at=source_expiry.isoformat(timespec="microseconds"),
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.commit()
        process = _enqueue(session, policy, artifact_id="source-audio-bounded")
        result = bundle(session, config=config, policy=policy)
        assert result.action == "poll_scheduled"
        assert result.processing_id == process.id

    split = urlsplit(submitted_url)
    claims = signer.verify(
        method="GET",
        canonical_path=split.path,
        raw_query=split.query,
        now=int(START.timestamp()),
    )
    assert claims.expires_at == int(source_expiry.timestamp())
    assert claims.expires_at < int(START.timestamp()) + 900


def test_aliyun_bundle_short_fetch_ttl_rejects_before_network(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config(asr_provider_deadline_seconds=300)
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    short_signer = PodcastAsrFetchUrlSigner(
        PodcastAsrFetchConfig(
            public_base_url=(
                "https://archive.example.test/api/public/podcast-asr/source-audio"
            ),
            signing_secret="short-ttl-test-signing-secret-000000",
            url_ttl_seconds=299,
            min_remaining_seconds=30,
        ),
        authority_id=policy.config.authority_id,
    )
    submit_calls = 0

    class NoSubmitClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            pytest.fail("short capability lifetime must reject before submit")

        def poll(self, _task_id):
            pytest.fail("new attempt must not poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: NoSubmitClient(),
        signer_resolver=lambda _session, **_kwargs: short_signer,
        clock=lambda: START,
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = bundle(session, config=config, policy=policy)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert result.action == "retry_wait"
        assert attempt.submission_state == "failed_retryable"
        assert attempt.request_unknown is False
        assert attempt.error_code == "aliyun_fetch_url_lifetime_insufficient"
        assert reservation.status == "released"
        assert submit_calls == 0


def test_aliyun_bundle_polls_after_signer_and_price_rotation(worker_env):
    engine, base_policy, config = worker_env
    original = _aliyun_config(
        asr_price_cny_minor_per_hour=60,
        asr_pricing_revision="test-price-v1",
    )
    rotated = replace(
        original,
        asr_price_cny_minor_per_hour=120,
        asr_pricing_revision="test-price-v2",
        asr_enable_words=False,
        app_key="",
    )
    original_policy = PodcastStagePolicy(base_policy.config, original)
    rotated_policy = PodcastStagePolicy(base_policy.config, rotated)
    signer = _asr_signer(original_policy)
    sensitive_signer_error = "database parse failed: secret=test-signing-secret"
    submit_calls = 0
    poll_calls = 0
    transcript = AsrTranscript(
        text="价格轮换后仍完成。",
        segments=(TranscriptSegment(0, 60_000, "价格轮换后仍完成。", 0),),
        words=(),
        audio_duration_ms=60_000,
    )

    class SubmitClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            return AsrSubmission("aliyun-task-rotated", 21050000)

        def poll(self, _task_id):
            pytest.fail("first tick only submits")

        def close(self):
            pass

    class PollClient:
        def submit(self, _url):
            pytest.fail("resumed task must never be resubmitted")

        def poll(self, task_id):
            nonlocal poll_calls
            poll_calls += 1
            return AsrPollResult(
                task_id,
                AsrState.SUCCEEDED,
                21050000,
                transcript,
            )

        def close(self):
            pass

    submit_bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda snapshot: (
            SubmitClient()
            if snapshot is original
            else pytest.fail("submit must use original config snapshot")
        ),
        signer_resolver=lambda _session, **_kwargs: signer,
        clock=lambda: START,
    )
    poll_at = START + dt.timedelta(seconds=original.asr_poll_interval_seconds)
    poll_bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda snapshot: (
            PollClient()
            if snapshot is rotated
            else pytest.fail("poll must use rotated config snapshot")
        ),
        signer_resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(sensitive_signer_error)
        ),
        clock=lambda: poll_at,
    )
    assert rotated.asr_poll_configured is True
    assert rotated.asr_configured is False

    with Session(engine) as session:
        process = _enqueue(
            session,
            original_policy,
            estimated_cost_minor=1,
        )
        submitted = submit_bundle(session, config=config, policy=original_policy)
        assert submitted.action == "poll_scheduled"

    with Session(engine) as session:
        completed = poll_bundle(session, config=config, policy=rotated_policy)
        assert completed.action == "completed"
        persisted = session.get(PodcastProcessingRecord, process.id)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
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
        assert persisted is not None and persisted.processing_status == "ready"
        assert (
            attempt.settings_fingerprint
            == aliyun_asr_identity(original).settings_fingerprint
        )
        assert reservation.status == "settled"
        assert reservation.unit_price_cny_minor == 60
        assert reservation.pricing_revision == "test-price-v1"
        assert reservation.actual_cost_minor == 1
        assert ledger.actual_cost_minor == 1
        assert ledger.actual_usage_units == 60
        persisted_text = "\n".join(
            filter(
                None,
                (
                    attempt.error_code,
                    attempt.error_message,
                    attempt.usage_json,
                    persisted.error_code,
                    persisted.error_message,
                ),
            )
        )
        assert sensitive_signer_error not in persisted_text
    assert submit_calls == 1
    assert poll_calls == 1


def test_aliyun_expired_accounting_plan_fails_before_attempt_or_network(
    worker_env,
):
    engine, base_policy, config = worker_env
    original = _aliyun_config()
    expired = replace(
        original,
        asr_entitlement_ends_at=(START - dt.timedelta(seconds=1)).isoformat(),
    )
    original_policy = PodcastStagePolicy(base_policy.config, original)
    expired_policy = PodcastStagePolicy(base_policy.config, expired)
    submit_calls = 0

    class NoNetworkClient:
        def submit(self, _url):
            nonlocal submit_calls
            submit_calls += 1
            raise AssertionError("expired planning must not submit")

        def poll(self, _task_id):
            raise AssertionError("expired planning has no task to poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _snapshot: NoNetworkClient(),
        signer_resolver=lambda _session, **_kwargs: _asr_signer(original_policy),
        clock=lambda: START,
    )
    with Session(engine) as session:
        process = _enqueue(session, original_policy)
        result = bundle(session, config=config, policy=expired_policy)
        persisted = session.get(PodcastProcessingRecord, process.id)
        assert result.action == "failed"
        assert persisted is not None
        assert persisted.processing_status == "failed"
        assert persisted.error_code == "provider_planning_unavailable"
        assert persisted.lease_token is None
        assert persisted.attempt_count == 0
        assert (
            session.exec(
                select(PodcastStageAttemptRecord).where(
                    PodcastStageAttemptRecord.processing_id == process.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(PodcastBudgetReservationRecord).where(
                    PodcastBudgetReservationRecord.processing_id == process.id
                )
            ).all()
            == []
        )
    assert submit_calls == 0


def test_aliyun_bundle_maps_submit_timeout_to_request_unknown(worker_env):
    engine, base_policy, config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    signer = _asr_signer(policy)
    submitted_url = ""

    class UnknownClient:
        def submit(self, file_url):
            nonlocal submitted_url
            submitted_url = file_url
            raise AliyunAsrSubmissionUnknown("submit", "read_timeout")

        def poll(self, _task_id):
            pytest.fail("unknown submit without TaskId must not poll")

        def close(self):
            pass

    bundle = AliyunIsiAsrWorkerBundle(
        client_factory=lambda _config: UnknownClient(),
        signer_resolver=lambda _session, **_kwargs: signer,
        clock=lambda: START,
    )
    with Session(engine) as session:
        process = _enqueue(session, policy)
        result = bundle(session, config=config, policy=policy)
        attempt = session.exec(
            select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == process.id
            )
        ).one()
        reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt.id
            )
        ).one()
        assert result.action == "reconciliation_required"
        assert attempt.submission_state == "reconciling"
        assert attempt.request_unknown is True
        assert attempt.error_code == "aliyun_submit_read_timeout"
        assert reservation.status == "reserved"
        assert submitted_url.startswith("https://archive.example.test/")
        persisted_text = "\n".join(
            filter(
                None,
                (
                    attempt.provider_task_id,
                    attempt.error_code,
                    attempt.error_message,
                    attempt.usage_json,
                ),
            )
        )
        assert submitted_url not in persisted_text


def test_aliyun_readiness_and_identity_are_poll_safe_not_entitlement_gated():
    expired = _aliyun_config(
        asr_quota_scope="",
        asr_daily_audio_seconds_limit=0,
        asr_entitlement_ends_at="",
        asr_provider_deadline_seconds=0,
        asr_pricing_revision="",
    )
    assert expired.asr_accounting_ready is False
    assert aliyun_asr_worker_ready(expired) is True
    identity = aliyun_asr_identity(expired)
    assert identity.provider == "aliyun-isi"
    assert (
        aliyun_asr_identity(replace(expired, access_key_secret="rotated")) == identity
    )
    assert aliyun_asr_identity(replace(expired, asr_enable_words=False)) != identity


def test_aliyun_adapter_maps_poll_errors_without_resubmitting(worker_env):
    _engine, base_policy, _config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)

    class PollFailureClient:
        def submit(self, _url):
            pytest.fail("poll mapping must not submit")

        def poll(self, _task_id):
            raise AliyunAsrPollError("http_403", retryable=False)

        def close(self):
            pass

    adapter = AliyunIsiAsrAdapter(
        aliyun,
        signer=_asr_signer(policy),
        client=PollFailureClient(),
        clock=lambda: START,
        transcript_language="zh-CN",
    )
    outcome = adapter.poll(
        task_id="aliyun-task-1",
        identity=aliyun_asr_identity(aliyun),
        audio_duration_ms=60_000,
        reserved_cost_minor=0,
    )
    assert outcome.task_id == "aliyun-task-1"
    assert outcome.failure.kind is FailureKind.PROTOCOL
    assert outcome.failure.retryable is True
    assert outcome.retry_after_seconds == aliyun.asr_poll_interval_seconds


@pytest.mark.parametrize(
    ("state", "expected_type", "retryable"),
    [
        (AsrState.QUEUED, Pending, None),
        (AsrState.RUNNING, Pending, None),
        (AsrState.EMPTY, TaskFailed, False),
        (AsrState.FAILED_RETRYABLE, TaskFailed, True),
        (AsrState.FAILED_TERMINAL, TaskFailed, False),
    ],
)
def test_aliyun_adapter_maps_provider_poll_states(
    worker_env, state, expected_type, retryable
):
    _engine, base_policy, _config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)

    class StateClient:
        def submit(self, _url):
            pytest.fail("poll mapping must not submit")

        def poll(self, task_id):
            return AsrPollResult(task_id, state, 21050003)

        def close(self):
            pass

    adapter = AliyunIsiAsrAdapter(
        aliyun,
        signer=_asr_signer(policy),
        client=StateClient(),
        clock=lambda: START,
        transcript_language="zh-CN",
    )
    outcome = adapter.poll(
        task_id="aliyun-task-1",
        identity=aliyun_asr_identity(aliyun),
        audio_duration_ms=60_000,
        reserved_cost_minor=0,
    )
    assert isinstance(outcome, expected_type)
    if isinstance(outcome, TaskFailed):
        assert outcome.failure.retryable is retryable
        assert outcome.usage.audio_duration_ms == 60_000


def test_aliyun_adapter_normalizes_success_with_frozen_usage_duration(worker_env):
    _engine, base_policy, _config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    transcript = AsrTranscript(
        text="你好，世界。",
        segments=(TranscriptSegment(0, 59_900, "你好，世界。", 0),),
        words=(
            TranscriptWord(0, 20_000, "你好", 0),
            TranscriptWord(20_000, 59_900, "世界", 0),
        ),
        audio_duration_ms=59_900,
    )

    class SuccessClient:
        def submit(self, _url):
            pytest.fail("poll mapping must not submit")

        def poll(self, task_id):
            return AsrPollResult(task_id, AsrState.SUCCEEDED, 21050000, transcript)

        def close(self):
            pass

    adapter = AliyunIsiAsrAdapter(
        aliyun,
        signer=_asr_signer(policy),
        client=SuccessClient(),
        clock=lambda: START,
        transcript_language="zh-CN",
    )
    outcome = adapter.poll(
        task_id="aliyun-task-1",
        identity=aliyun_asr_identity(aliyun),
        audio_duration_ms=60_000,
        reserved_cost_minor=0,
    )
    assert isinstance(outcome, Succeeded)
    assert outcome.usage.audio_duration_ms == 60_000
    document = json.loads(outcome.output.text)
    assert document["audio_duration_ms"] == 59_900
    assert document["segments"][0]["words"][1]["text"] == "世界"


def test_aliyun_adapter_folds_identical_stereo_and_uses_wall_clock_duration(
    worker_env,
):
    _engine, base_policy, _config = worker_env
    aliyun = _aliyun_config()
    policy = PodcastStagePolicy(base_policy.config, aliyun)
    transcript = AsrTranscript(
        text="hello\nhello",
        segments=(
            TranscriptSegment(0, 59_000, "hello", 0),
            TranscriptSegment(0, 59_000, "hello", 1),
        ),
        words=(
            TranscriptWord(0, 59_000, "hello", 0),
            TranscriptWord(0, 59_000, "hello", 1),
        ),
        audio_duration_ms=120_000,
    )

    class SuccessClient:
        def submit(self, _url):
            pytest.fail("poll mapping must not submit")

        def poll(self, task_id):
            return AsrPollResult(task_id, AsrState.SUCCEEDED, 21050000, transcript)

        def close(self):
            pass

    adapter = AliyunIsiAsrAdapter(
        aliyun,
        signer=_asr_signer(policy),
        client=SuccessClient(),
        clock=lambda: START,
        transcript_language="en",
    )
    outcome = adapter.poll(
        task_id="aliyun-stereo-task",
        identity=aliyun_asr_identity(aliyun),
        audio_duration_ms=60_000,
        reserved_cost_minor=0,
    )

    assert isinstance(outcome, Succeeded)
    document = json.loads(outcome.output.text)
    assert document["audio_duration_ms"] == 60_000
    assert document["text"] == "hello"
    assert len(document["segments"]) == 1
    assert document["segments"][0]["words"] == [
        {"channel": 0, "end_ms": 59_000, "start_ms": 0, "text": "hello"}
    ]


def test_aliyun_normalizer_folds_offset_rechunked_mirror_but_keeps_distinct_channel():
    transcript = AsrTranscript(
        text="unused provider aggregate",
        segments=(
            TranscriptSegment(0, 4_000, "AI is becoming more powerful", 0),
            TranscriptSegment(0, 8_000, "the guest gives a distinct answer", 1),
            TranscriptSegment(
                3,
                8_003,
                "AI is becoming more powerful and resources are concentrated",
                2,
            ),
            TranscriptSegment(4_000, 8_000, "resources are concentrated", 0),
        ),
        words=(),
        audio_duration_ms=24_000,
    )

    document = json.loads(
        _normalized_transcript(
            transcript,
            language="en",
            source_audio_duration_ms=8_000,
        )
    )

    assert document["audio_duration_ms"] == 8_000
    retained_channels = {segment["channel"] for segment in document["segments"]}
    assert 1 in retained_channels
    assert len(retained_channels & {0, 2}) == 1
    assert document["text"].count("AI is becoming more powerful") == 1
    assert "the guest gives a distinct answer" in document["text"]
