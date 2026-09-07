"""Restart-safe, provider-neutral orchestration for one Podcast ASR step.

The runner deliberately performs at most one provider I/O per invocation.  All
durable transitions stay in :mod:`services.podcast_processing`; an adapter only
plans pure execution values and translates its wire protocol into the typed
worker contracts.  In particular, ``authorize_provider_call`` is the final
operation before a first submission can leave the host.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from sqlmodel import Session, select

from models.db import (
    PodcastArtifactRecord,
    PodcastBudgetReservationRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
)
from services.podcast_normalized_transcripts import (
    NormalizedTranscriptConflict,
    NormalizedTranscriptError,
    materialize_normalized_transcript,
)
from services.podcast_processing import (
    ACTIVE_ATTEMPT_STATES,
    PodcastBudgetExceeded,
    PodcastEligibilityDenied,
    PodcastProcessingClaim,
    PodcastProcessingConflict,
    PodcastProviderQuotaExceeded,
    PodcastProviderReconciliationRequired,
    authorize_provider_call,
    begin_stage_attempt,
    claim_next_processing,
    commit_stage_attempt,
    fail_stage_attempt,
    mark_provider_submission,
    park_for_reconciliation,
    reconcile_provider_request,
    resolve_claim_before_attempt,
    schedule_stage_poll,
    settle_attempt_cost,
)
from services.podcast_processing_inputs import (
    SourceAudioDurationError,
    processing_input_fingerprint,
    source_audio_duration_ms,
)
from services.podcast_provider_ports import (
    AsrPlanningUnavailable,
    AsrProviderAdapter,
    AsrProviderPlan,
    AsrUsagePlanner,
)
from services.podcast_worker_contracts import (
    Accepted,
    ArtifactRef,
    ExecutionIdentity,
    ExecutionKind,
    Indeterminate,
    NormalizedUsage,
    Pending,
    ProviderUsagePlan,
    Rejected,
    StageContext,
    Succeeded,
    TaskFailed,
    TextOutput,
    Unknown,
)


@dataclass(frozen=True)
class AsrWorkerConfig:
    worker_id: str
    lease_seconds: int
    fallback_retry_seconds: int
    next_stage_by_target: Mapping[str, str | None]

    def __post_init__(self) -> None:
        worker_id = str(self.worker_id or "").strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        object.__setattr__(self, "worker_id", worker_id)
        for name in ("lease_seconds", "fallback_retry_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.next_stage_by_target, Mapping):
            raise ValueError("next_stage_by_target must be a mapping")
        normalized: dict[str, str | None] = {}
        for raw_target, raw_stage in self.next_stage_by_target.items():
            target = str(raw_target or "").strip().lower()
            if not target:
                raise ValueError("next-stage target names must be nonempty")
            stage = None if raw_stage is None else str(raw_stage or "").strip().lower()
            if stage == "":
                raise ValueError("next-stage values must be nonempty or None")
            normalized[target] = stage
        object.__setattr__(self, "next_stage_by_target", MappingProxyType(normalized))


@dataclass(frozen=True)
class AsrWorkerStep:
    action: str
    processing_id: str | None = None
    attempt_id: str | None = None
    provider_task_id: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {
            "completed",
            "failed",
            "idle",
            "not_required",
            "poll_scheduled",
            "reconciliation_required",
            "retry_wait",
        }:
            raise ValueError("unknown ASR worker action")


@dataclass(frozen=True)
class _RunSnapshot:
    requested_target: str
    input_hash: str
    input_language: str
    budget_scope: str
    budget_period: str
    budget_limit_minor: int
    estimated_cost_minor: int
    artifact: ArtifactRef
    audio_duration_ms: int
    source_expires_at: dt.datetime | None
    attempt_id: str | None
    attempt_no: int | None
    provider_name: str | None
    model_name: str | None
    provider_revision: str | None
    settings_fingerprint: str | None
    provider_task_id: str | None
    submission_state: str | None
    provider_deadline_at: dt.datetime | None
    reservation_status: str | None


class _AsrClaimPolicy:
    """Narrow a deployment policy to ASR without weakening its stage guard."""

    allowed = frozenset({"asr"})

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate

    def require_stage(self, stage: str, *, boundary: str) -> None:
        method = getattr(self._delegate, "require_stage", None)
        if not callable(method):
            raise TypeError("policy must provide require_stage")
        method(stage, boundary=boundary)


def _utc(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _parse_deadline(value: str | None) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PodcastProcessingConflict(
            "persisted provider deadline is invalid"
        ) from exc
    return _utc(parsed)


def _parse_source_expiry(value: str | None) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _load_snapshot(session: Session, claim: PodcastProcessingClaim) -> _RunSnapshot:
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None or process.stage != "asr":
        session.rollback()
        raise PodcastProcessingConflict("claimed ASR processing disappeared")
    active = session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == process.id,
            PodcastStageAttemptRecord.submission_state.in_(ACTIVE_ATTEMPT_STATES),
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()
    reservation = (
        session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == active.id
            )
        ).first()
        if active is not None
        else None
    )
    if active is not None and reservation is None:
        session.rollback()
        raise PodcastProcessingConflict("active ASR attempt reservation is missing")
    artifact = session.get(PodcastArtifactRecord, str(process.input_artifact_id or ""))
    artifact_is_current = (
        artifact is not None
        and artifact.episode_id == claim.episode_id
        and artifact.kind == "source_audio"
        and artifact.status == "ready"
        and artifact.content_hash == process.input_content_hash
    )
    if not artifact_is_current and not (
        claim.drain_only
        and active is not None
        and active.execution_kind == "provider"
    ):
        session.rollback()
        raise PodcastProcessingConflict("ASR source-audio binding is not usable")
    if artifact is not None:
        try:
            audio_duration_ms = source_audio_duration_ms(
                artifact.duration_seconds
            )
        except SourceAudioDurationError:
            audio_duration_ms = 0
    else:
        audio_duration_ms = 0
    if audio_duration_ms <= 0 and claim.drain_only and reservation is not None:
        if (
            reservation.provider_quota_unit == "audio_seconds"
            and isinstance(reservation.reserved_usage_units, int)
            and reservation.reserved_usage_units > 0
        ):
            audio_duration_ms = reservation.reserved_usage_units * 1000
    if audio_duration_ms <= 0:
        session.rollback()
        raise SourceAudioDurationError(
            "ASR source-audio duration is unavailable for settlement"
        )
    immutable_artifact = ArtifactRef(
        artifact_id=str(process.input_artifact_id or ""),
        episode_id=claim.episode_id,
        kind=str(process.input_artifact_kind or ""),
        content_hash=str(process.input_content_hash or ""),
        size_bytes=(artifact.size_bytes if artifact_is_current else 0),
        mime_type=(artifact.mime if artifact_is_current else "application/octet-stream"),
    )
    snapshot = _RunSnapshot(
        requested_target=process.requested_target,
        input_hash=str(process.input_content_hash or ""),
        input_language=str(process.input_language or ""),
        budget_scope=str(process.budget_scope or ""),
        budget_period=str(process.budget_period or ""),
        budget_limit_minor=int(process.budget_limit_minor or 0),
        estimated_cost_minor=int(active.estimated_cost_minor) if active else 0,
        artifact=immutable_artifact,
        audio_duration_ms=audio_duration_ms,
        source_expires_at=(
            _parse_source_expiry(artifact.expires_at)
            if artifact_is_current
            else None
        ),
        attempt_id=active.id if active else None,
        attempt_no=active.attempt_no if active else None,
        provider_name=active.provider_name if active else None,
        model_name=active.model_name if active else None,
        provider_revision=active.provider_revision if active else None,
        settings_fingerprint=active.settings_fingerprint if active else None,
        provider_task_id=active.provider_task_id if active else None,
        submission_state=active.submission_state if active else None,
        provider_deadline_at=(
            _parse_deadline(active.provider_deadline_at) if active else None
        ),
        reservation_status=reservation.status if reservation else None,
    )
    session.rollback()
    return snapshot


def _idempotency_key(namespace: str, claim: PodcastProcessingClaim) -> str:
    digest = hashlib.sha256(
        (
            f"{namespace}\x00{claim.processing_id}\x00{claim.stage}\x00"
            f"{claim.fencing_token}\x00{claim.input_fingerprint}"
        ).encode("utf-8")
    ).hexdigest()
    return f"podcast-asr-{namespace}:{digest}"


def _context(
    claim: PodcastProcessingClaim,
    snapshot: _RunSnapshot,
    plan: AsrProviderPlan,
    *,
    attempt_id: str,
    attempt_no: int,
) -> StageContext:
    return StageContext(
        processing_id=claim.processing_id,
        episode_id=claim.episode_id,
        target=snapshot.requested_target,
        stage="asr",
        attempt_id=attempt_id,
        attempt_no=attempt_no,
        fencing_token=claim.fencing_token,
        input_artifact=snapshot.artifact,
        identity=plan.identity,
        plan=plan.stage,
        input_expires_at=snapshot.source_expires_at,
    )


def _retry_at(
    failure_retry_after: int | None,
    *,
    config: AsrWorkerConfig,
    now: dt.datetime,
) -> dt.datetime:
    seconds = failure_retry_after or config.fallback_retry_seconds
    return now + dt.timedelta(seconds=seconds)


def _schedule(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    desired_retry_at: dt.datetime,
    deadline: dt.datetime,
    poll_performed: bool,
    policy: object,
    now: dt.datetime,
) -> None:
    if deadline <= now:
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_deadline_elapsed",
            redacted_error_message=(
                "provider deadline elapsed before the next safe poll"
            ),
            policy=policy,
            now=now,
        )
        return
    retry_at = min(desired_retry_at, deadline)
    schedule_stage_poll(
        session,
        claim,
        attempt_id=attempt_id,
        retry_at=retry_at,
        poll_performed=poll_performed,
        provider_deadline_at=deadline,
        policy=policy,
        now=now,
    )


def _settle_terminal_usage(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    task_id: str,
    usage: NormalizedUsage,
    policy: object,
    now: dt.datetime,
) -> AsrWorkerStep | None:
    """Settle one terminal provider response or durably stop re-polling it."""

    try:
        settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt_id,
            settlement_key=f"podcast-asr-settlement:{attempt_id}",
            actual_cost_minor=usage.cost_minor,
            usage=usage,
            policy=policy,
            now=now,
        )
    except (PodcastProviderReconciliationRequired, PodcastProcessingConflict):
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_usage_reconciliation_required",
            redacted_error_message=(
                "terminal ASR provider usage does not match the frozen reservation"
            ),
            policy=policy,
            now=now,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    return None


def _persisted_restart_identity(
    snapshot: _RunSnapshot,
) -> tuple[str, ExecutionIdentity]:
    if snapshot.attempt_id is None or snapshot.attempt_no is None:
        raise PodcastProcessingConflict("active ASR attempt identity is incomplete")
    try:
        identity = ExecutionIdentity(
            execution_kind=ExecutionKind.PROVIDER,
            provider=str(snapshot.provider_name or ""),
            model=str(snapshot.model_name or ""),
            revision=str(snapshot.provider_revision or ""),
            settings_fingerprint=str(snapshot.settings_fingerprint or ""),
        )
    except ValueError as exc:
        raise PodcastProcessingConflict(
            "persisted ASR provider identity is invalid"
        ) from exc
    return snapshot.attempt_id, identity


def _document(output: TextOutput) -> Mapping[str, object]:
    if output.mime_type != "application/json":
        raise NormalizedTranscriptError(
            "ASR success must contain a normalized transcript JSON document"
        )
    try:
        parsed = json.loads(output.text)
    except (TypeError, ValueError) as exc:
        raise NormalizedTranscriptError(
            "ASR success contained invalid transcript JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise NormalizedTranscriptError("normalized transcript must be a JSON object")
    return parsed


def run_asr_worker_step(
    session: Session,
    *,
    adapter: AsrProviderAdapter,
    usage_planner: AsrUsagePlanner,
    config: AsrWorkerConfig,
    policy: object,
    now: dt.datetime | None = None,
) -> AsrWorkerStep:
    """Claim and advance at most one ASR provider operation.

    Returning ``idle`` means no due ASR row was claimable.  Every other result
    describes a durable state already committed before this function returns.
    """

    current = _utc(now)
    claim = claim_next_processing(
        session,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
        policy=_AsrClaimPolicy(policy),
        now=current,
    )
    if claim is None:
        return AsrWorkerStep("idle")
    if claim.stage != "asr":
        raise PodcastProcessingConflict("ASR worker claimed a non-ASR stage")

    try:
        snapshot = _load_snapshot(session, claim)
    except SourceAudioDurationError:
        session.rollback()
        if claim.drain_only:
            active_attempt = session.exec(
                select(PodcastStageAttemptRecord)
                .where(
                    PodcastStageAttemptRecord.processing_id
                    == claim.processing_id,
                    PodcastStageAttemptRecord.submission_state.in_(
                        ACTIVE_ATTEMPT_STATES
                    ),
                )
                .order_by(PodcastStageAttemptRecord.attempt_no.desc())
            ).first()
            active_attempt_id = active_attempt.id if active_attempt else None
            active_task_id = (
                active_attempt.provider_task_id if active_attempt else ""
            )
            session.rollback()
            if active_attempt_id is None:
                raise PodcastProcessingConflict(
                    "drain-only ASR attempt disappeared"
                )
            park_for_reconciliation(
                session,
                claim,
                attempt_id=active_attempt_id,
                reason_code="source_audio_duration_unavailable",
                redacted_error_message=(
                    "ASR input duration is unavailable for safe settlement"
                ),
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(
                "reconciliation_required",
                claim.processing_id,
                active_attempt_id,
                active_task_id or None,
            )
        failed = resolve_claim_before_attempt(
            session,
            claim,
            error_code="source_audio_duration_invalid",
            redacted_error_message=(
                "persisted source audio duration is invalid for ASR"
            ),
            retryable=False,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(failed.processing_status, failed.id)
    if snapshot.requested_target not in config.next_stage_by_target:
        raise PodcastProcessingConflict(
            "ASR worker has no configured destination for the requested target"
        )
    if snapshot.attempt_id is None:
        try:
            plan = adapter.plan(
                snapshot.artifact,
                audio_duration_ms=snapshot.audio_duration_ms,
                now=current,
            )
            if not isinstance(plan, AsrProviderPlan):
                raise ValueError("adapter.plan must return an AsrProviderPlan")
            usage_plan = usage_planner.plan_usage(
                identity=plan.identity,
                input_artifact=snapshot.artifact,
                audio_duration_ms=snapshot.audio_duration_ms,
                now=current,
            )
        except AsrPlanningUnavailable:
            failed = resolve_claim_before_attempt(
                session,
                claim,
                error_code="provider_planning_unavailable",
                redacted_error_message=(
                    "ASR provider planning is unavailable before submission"
                ),
                retryable=False,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id)
        if usage_plan is not None:
            if not isinstance(usage_plan, ProviderUsagePlan):
                raise ValueError(
                    "usage planner must return a ProviderUsagePlan or None"
                )
            expected_seconds = (snapshot.audio_duration_ms + 999) // 1000
            if (
                snapshot.audio_duration_ms <= 0
                or usage_plan.reserved_units != expected_seconds
            ):
                raise ValueError(
                    "ASR usage reservation must match persisted source-audio duration"
                )
            if plan.stage.estimated_cost_minor != usage_plan.estimated_cost_minor:
                raise ValueError(
                    "stage estimate must match trusted provider usage pricing"
                )
            if plan.stage.deadline_seconds != usage_plan.deadline_seconds:
                raise ValueError(
                    "stage deadline must match trusted provider usage deadline"
                )
        if plan.admission_fingerprint:
            expected_input_fingerprint = processing_input_fingerprint(
                episode_id=claim.episode_id,
                entry_stage="asr",
                artifact_id=snapshot.artifact.artifact_id,
                content_hash=snapshot.artifact.content_hash,
                kind=snapshot.artifact.kind,
                language=snapshot.input_language,
                audio_duration_ms=snapshot.audio_duration_ms,
                admission_fingerprint=plan.admission_fingerprint,
            )
            if expected_input_fingerprint != claim.input_fingerprint:
                failed = resolve_claim_before_attempt(
                    session,
                    claim,
                    error_code="provider_admission_changed",
                    redacted_error_message=(
                        "ASR provider admission changed after enqueue"
                    ),
                    retryable=False,
                    policy=policy,
                    now=current,
                )
                return AsrWorkerStep(failed.processing_status, failed.id)
        source_valid_until = snapshot.source_expires_at
        required_valid_until = current + dt.timedelta(
            seconds=plan.required_input_lifetime_seconds
        )
        if source_valid_until is None or source_valid_until <= required_valid_until:
            failed = resolve_claim_before_attempt(
                session,
                claim,
                error_code="source_audio_expiry_insufficient",
                redacted_error_message=(
                    "source audio will expire before the required ASR input horizon"
                ),
                retryable=False,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id)
        provider_request_key = _idempotency_key("request", claim)
        try:
            attempt = begin_stage_attempt(
                session,
                claim,
                input_hash=snapshot.input_hash,
                settings_fingerprint=plan.identity.settings_fingerprint,
                provider_name=plan.identity.provider,
                model_name=plan.identity.model,
                provider_revision=plan.identity.revision,
                provider_request_key=provider_request_key,
                execution_kind=plan.identity.execution_kind.value,
                estimated_cost_minor=plan.stage.estimated_cost_minor,
                budget_scope=snapshot.budget_scope,
                budget_period=snapshot.budget_period,
                budget_limit_minor=snapshot.budget_limit_minor,
                reservation_idempotency_key=_idempotency_key(
                    "reservation", claim
                ),
                provider_usage_plan=usage_plan,
                policy=policy,
                now=current,
            )
        except PodcastProviderQuotaExceeded:
            retry_at = current + dt.timedelta(
                seconds=config.fallback_retry_seconds
            )
            if usage_plan is not None:
                retry_at = max(retry_at, usage_plan.window_end_at)
            deferred = resolve_claim_before_attempt(
                session,
                claim,
                error_code="provider_usage_window_unavailable",
                redacted_error_message=(
                    "provider usage capacity was unavailable before submission"
                ),
                retryable=True,
                retry_at=retry_at,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(deferred.processing_status, deferred.id)
        except PodcastBudgetExceeded:
            failed = resolve_claim_before_attempt(
                session,
                claim,
                error_code="provider_budget_unavailable",
                redacted_error_message=(
                    "the frozen processing budget cannot cover this provider plan"
                ),
                retryable=False,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id)
        context = _context(
            claim,
            snapshot,
            plan,
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
        )
        try:
            authorize_provider_call(
                session,
                claim,
                attempt_id=attempt.id,
                policy=policy,
                now=current,
            )
        except PodcastProviderQuotaExceeded:
            retry_at = current + dt.timedelta(
                seconds=config.fallback_retry_seconds
            )
            if usage_plan is not None:
                retry_at = max(retry_at, usage_plan.window_end_at)
            failed = fail_stage_attempt(
                session,
                claim,
                attempt_id=attempt.id,
                error_code="provider_call_window_unavailable",
                redacted_error_message=(
                    "provider call was not authorized before network I/O"
                ),
                retryable=True,
                retry_at=retry_at,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id, attempt.id)
        except PodcastEligibilityDenied:
            failed = fail_stage_attempt(
                session,
                claim,
                attempt_id=attempt.id,
                error_code="podcast_eligibility_denied",
                redacted_error_message=(
                    "Podcast eligibility changed before provider submission"
                ),
                retryable=False,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id, attempt.id)
        try:
            outcome = adapter.submit(
                context, provider_request_key=provider_request_key
            )
        except Exception:
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                request_unknown=True,
                policy=policy,
                now=current,
            )
            park_for_reconciliation(
                session,
                claim,
                attempt_id=attempt.id,
                reason_code="provider_submit_outcome_unknown",
                redacted_error_message=(
                    "provider submission raised after authorization; outcome is unknown"
                ),
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(
                "reconciliation_required", claim.processing_id, attempt.id
            )
        if isinstance(outcome, Rejected):
            retry_at = (
                _retry_at(
                    outcome.failure.retry_after_seconds,
                    config=config,
                    now=current,
                )
                if outcome.failure.retryable
                else None
            )
            failed = fail_stage_attempt(
                session,
                claim,
                attempt_id=attempt.id,
                error_code=outcome.failure.code,
                redacted_error_message="ASR provider rejected the submission",
                retryable=outcome.failure.retryable,
                retry_at=retry_at,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(failed.processing_status, failed.id, attempt.id)
        if isinstance(outcome, Unknown):
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                provider_task_id=outcome.task_id or "",
                request_unknown=True,
                policy=policy,
                now=current,
            )
            if outcome.task_id is None:
                park_for_reconciliation(
                    session,
                    claim,
                    attempt_id=attempt.id,
                    reason_code=outcome.failure.code,
                    redacted_error_message=(
                        "ASR provider submission outcome is unknown"
                    ),
                    policy=policy,
                    now=current,
                )
                return AsrWorkerStep(
                    "reconciliation_required", claim.processing_id, attempt.id
                )
            deadline = attempt.provider_deadline_at
            deadline_at = _parse_deadline(deadline) or (
                current + dt.timedelta(seconds=plan.stage.deadline_seconds)
            )
            _schedule(
                session,
                claim,
                attempt_id=attempt.id,
                desired_retry_at=(
                    current + dt.timedelta(seconds=plan.stage.poll_interval_seconds)
                ),
                deadline=deadline_at,
                poll_performed=False,
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(
                "poll_scheduled", claim.processing_id, attempt.id, outcome.task_id
            )
        if not isinstance(outcome, Accepted):
            # Once the authorization boundary has passed, an adapter protocol
            # violation is ambiguous just like a transport exception: never
            # issue a second submission until an operator reconciles it.
            mark_provider_submission(
                session,
                claim,
                attempt_id=attempt.id,
                request_unknown=True,
                policy=policy,
                now=current,
            )
            park_for_reconciliation(
                session,
                claim,
                attempt_id=attempt.id,
                reason_code="provider_submit_protocol_error",
                redacted_error_message=(
                    "provider adapter returned an invalid submission outcome"
                ),
                policy=policy,
                now=current,
            )
            return AsrWorkerStep(
                "reconciliation_required", claim.processing_id, attempt.id
            )
        mark_provider_submission(
            session,
            claim,
            attempt_id=attempt.id,
            provider_task_id=outcome.task_id,
            policy=policy,
            now=current,
        )
        deadline_at = _parse_deadline(attempt.provider_deadline_at) or (
            current + dt.timedelta(seconds=plan.stage.deadline_seconds)
        )
        _schedule(
            session,
            claim,
            attempt_id=attempt.id,
            desired_retry_at=(
                current + dt.timedelta(seconds=plan.stage.poll_interval_seconds)
            ),
            deadline=deadline_at,
            poll_performed=False,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "poll_scheduled", claim.processing_id, attempt.id, outcome.task_id
        )

    attempt_id, persisted_identity = _persisted_restart_identity(snapshot)
    if claim.drain_only and snapshot.reservation_status == "settled":
        denied = fail_stage_attempt(
            session,
            claim,
            attempt_id=attempt_id,
            error_code="podcast_eligibility_denied",
            redacted_error_message=(
                "settled ASR output was discarded after eligibility changed"
            ),
            retryable=False,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            denied.processing_status,
            denied.id,
            attempt_id,
            snapshot.provider_task_id,
        )
    if snapshot.submission_state in {"request_unknown", "reconciling"} and not (
        snapshot.provider_task_id or ""
    ).strip():
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_task_identity_missing",
            redacted_error_message=(
                "ambiguous provider submission has no task identity for polling"
            ),
            policy=policy,
            now=current,
        )
        return AsrWorkerStep("reconciliation_required", claim.processing_id, attempt_id)
    if snapshot.submission_state not in {"submitted", "request_unknown"}:
        raise PodcastProcessingConflict("active ASR attempt is not pollable")
    task_id = str(snapshot.provider_task_id or "").strip()
    deadline = snapshot.provider_deadline_at
    if deadline is None or deadline <= current:
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_deadline_elapsed",
            redacted_error_message="provider deadline elapsed before polling",
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    if adapter.supports(persisted_identity) is not True:
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_adapter_unavailable",
            redacted_error_message=(
                "no configured ASR adapter supports the persisted provider task"
            ),
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    try:
        outcome = adapter.poll(
            task_id=task_id,
            identity=persisted_identity,
            audio_duration_ms=snapshot.audio_duration_ms,
            reserved_cost_minor=snapshot.estimated_cost_minor,
        )
    except Exception:
        _schedule(
            session,
            claim,
            attempt_id=attempt_id,
            desired_retry_at=(
                current + dt.timedelta(seconds=config.fallback_retry_seconds)
            ),
            deadline=deadline,
            poll_performed=True,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "poll_scheduled", claim.processing_id, attempt_id, task_id
        )
    if not isinstance(outcome, (Pending, Succeeded, TaskFailed, Indeterminate)):
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_poll_protocol_error",
            redacted_error_message=(
                "provider adapter returned an invalid polling outcome"
            ),
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    if outcome.task_id != task_id:
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="provider_task_identity_mismatch",
            redacted_error_message="provider poll returned a different task identity",
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    if snapshot.submission_state == "request_unknown" and not isinstance(
        outcome, Indeterminate
    ):
        reconcile_provider_request(
            session,
            claim,
            attempt_id=attempt_id,
            outcome="submitted",
            provider_task_id=task_id,
            policy=policy,
            now=current,
        )
    if isinstance(outcome, (Pending, Indeterminate)):
        retry_after = (
            outcome.retry_after_seconds
            if isinstance(outcome, (Pending, Indeterminate))
            else config.fallback_retry_seconds
        )
        _schedule(
            session,
            claim,
            attempt_id=attempt_id,
            desired_retry_at=current + dt.timedelta(seconds=retry_after),
            deadline=deadline,
            poll_performed=True,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "poll_scheduled", claim.processing_id, attempt_id, task_id
        )
    if isinstance(outcome, TaskFailed):
        settlement_issue = _settle_terminal_usage(
            session,
            claim,
            attempt_id=attempt_id,
            task_id=task_id,
            usage=outcome.usage,
            policy=policy,
            now=current,
        )
        if settlement_issue is not None:
            return settlement_issue
        retry_at = (
            _retry_at(
                outcome.failure.retry_after_seconds,
                config=config,
                now=current,
            )
            if outcome.failure.retryable
            else None
        )
        failed = fail_stage_attempt(
            session,
            claim,
            attempt_id=attempt_id,
            error_code=outcome.failure.code,
            redacted_error_message="ASR provider task failed",
            retryable=outcome.failure.retryable,
            retry_at=retry_at,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(failed.processing_status, failed.id, attempt_id, task_id)

    assert isinstance(outcome, Succeeded)
    if claim.drain_only:
        settlement_issue = _settle_terminal_usage(
            session,
            claim,
            attempt_id=attempt_id,
            task_id=task_id,
            usage=outcome.usage,
            policy=policy,
            now=current,
        )
        if settlement_issue is not None:
            return settlement_issue
        denied = fail_stage_attempt(
            session,
            claim,
            attempt_id=attempt_id,
            error_code="podcast_eligibility_denied",
            redacted_error_message=(
                "ASR provider output was discarded after eligibility changed"
            ),
            retryable=False,
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            denied.processing_status, denied.id, attempt_id, task_id
        )
    try:
        if not isinstance(outcome.output, TextOutput):
            raise NormalizedTranscriptError("ASR success must contain text output")
        document = _document(outcome.output)
        artifact = materialize_normalized_transcript(
            session,
            claim,
            attempt_id=attempt_id,
            document=document,
            policy=policy,
            now=current,
        )
    except (NormalizedTranscriptError, NormalizedTranscriptConflict):
        settlement_issue = _settle_terminal_usage(
            session,
            claim,
            attempt_id=attempt_id,
            task_id=task_id,
            usage=outcome.usage,
            policy=policy,
            now=current,
        )
        if settlement_issue is not None:
            return settlement_issue
        park_for_reconciliation(
            session,
            claim,
            attempt_id=attempt_id,
            reason_code="normalized_transcript_invalid",
            redacted_error_message=(
                "provider output could not be materialized as a normalized transcript"
            ),
            policy=policy,
            now=current,
        )
        return AsrWorkerStep(
            "reconciliation_required", claim.processing_id, attempt_id, task_id
        )
    settlement_issue = _settle_terminal_usage(
        session,
        claim,
        attempt_id=attempt_id,
        task_id=task_id,
        usage=outcome.usage,
        policy=policy,
        now=current,
    )
    if settlement_issue is not None:
        return settlement_issue
    completed = commit_stage_attempt(
        session,
        claim,
        attempt_id=attempt_id,
        output_hash=artifact.content_hash,
        next_stage=config.next_stage_by_target[snapshot.requested_target],
        policy=policy,
        now=current,
    )
    return AsrWorkerStep("completed", completed.id, attempt_id, task_id)


__all__ = [
    "AsrProviderAdapter",
    "AsrProviderPlan",
    "AsrUsagePlanner",
    "AsrWorkerConfig",
    "AsrWorkerStep",
    "run_asr_worker_step",
]
