"""Durable, provider-neutral Podcast processing state machine.

Every mutating function is a database transaction boundary.  Provider calls
must happen *after* ``authorize_provider_call`` and before
``mark_provider_submission``; this module never calls a provider and stores no
provider-specific secrets.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.db import (
    ArticleRecord,
    PODCAST_PROCESSING_STAGES,
    PodcastArtifactRecord,
    PodcastBudgetReservationRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingCommandRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services.podcast_worker_contracts import (
    NormalizedUsage,
    ProviderUsagePlan,
    ProviderUsageUnit,
)


ACTIVE_ATTEMPT_STATES = frozenset(
    {"prepared", "submitted", "request_unknown", "reconciling"}
)


class PodcastProcessingError(RuntimeError):
    """Base class for durable processing transition failures."""


class PodcastProcessingConflict(PodcastProcessingError):
    """An idempotency key or state transition conflicts with persisted truth."""


class PodcastLeaseLost(PodcastProcessingError):
    """The supplied lease token/fence is expired or has been superseded."""


class PodcastBudgetExceeded(PodcastProcessingError):
    """The configured budget cap cannot cover a provider submission."""


class PodcastProviderQuotaExceeded(PodcastBudgetExceeded):
    """The durable provider entitlement cannot cover a submission."""


class PodcastProviderReconciliationRequired(PodcastProcessingError):
    """A prior provider call must be reconciled before another submission."""


class PodcastEligibilityDenied(PodcastProcessingError):
    """The current processing policy denies this Podcast boundary."""


class StagePolicy(Protocol):
    def require_stage(self, stage: str, *, boundary: str) -> None: ...


StagePolicyCheck = StagePolicy | Callable[..., None]

STAGE_GRAPH: dict[str, dict[str, frozenset[Optional[str]]]] = {
    "transcript": {"asr": frozenset({None})},
    "full_analysis": {
        "asr": frozenset({"analyze"}),
        "analyze": frozenset({None}),
    },
    "digest_blog": {
        "asr": frozenset({"translate", "analyze"}),
        "translate": frozenset({"analyze"}),
        "analyze": frozenset({"digest"}),
        "digest": frozenset({"script"}),
        "script": frozenset({None}),
    },
    "digest_audio": {
        "tts": frozenset({"audio_qa"}),
        "audio_qa": frozenset({"local_publish"}),
        "local_publish": frozenset({None}),
    },
}


@dataclass(frozen=True)
class PodcastProcessingClaim:
    processing_id: str
    episode_id: str
    stage: str
    input_fingerprint: str
    pipeline_version: str
    lease_owner: str
    lease_token: str
    fencing_token: int
    lease_expires_at: str
    drain_only: bool = False


def _as_utc(value: Optional[dt.datetime]) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _require_stage(
    policy: Optional[StagePolicyCheck], stage: str, *, boundary: str
) -> None:
    if policy is None:
        from services.podcast_stage_policy import require_stage

        require_stage(stage, boundary=boundary)
        return
    method = getattr(policy, "require_stage", None)
    if callable(method):
        method(stage, boundary=boundary)
        return
    policy(stage, boundary=boundary)  # type: ignore[misc,operator]


def _require_clean_session(session: Session, operation: str) -> None:
    if session.in_transaction():
        raise PodcastProcessingConflict(
            f"{operation} requires a clean/new Session transaction boundary"
        )


def _effective_provider_policy(
    policy: Optional[StagePolicyCheck],
) -> StagePolicyCheck:
    if policy is None:
        from config import settings
        from services.podcast_stage_policy import PodcastStagePolicy

        return PodcastStagePolicy(settings.podcast, settings.aliyun_isi)
    return policy


def _provider_usage_plan_is_trusted(
    policy: Optional[StagePolicyCheck],
    *,
    provider_name: str,
    stage: str,
    plan: ProviderUsagePlan,
    now: dt.datetime,
) -> bool:
    effective_policy = _effective_provider_policy(policy)
    validator = getattr(effective_policy, "provider_usage_plan_matches", None)
    return bool(
        callable(validator)
        and validator(provider_name, stage, plan, now=now)
    )


def _transaction_boundary(function):
    """Rollback only work opened by this mutator when it raises.

    Every public mutator rejects a caller-owned transaction before touching the
    database.  Recording the entry state here means error cleanup can never
    accidentally rollback unrelated caller work, while still ensuring an
    idempotency conflict does not poison the Session for the next operation.
    """

    @wraps(function)
    def guarded(session: Session, *args, **kwargs):
        caller_owned_transaction = session.in_transaction()
        try:
            return function(session, *args, **kwargs)
        except BaseException:
            if not caller_owned_transaction and session.in_transaction():
                session.rollback()
            raise

    return guarded


def _detach_after_read(session: Session, record: Any) -> Any:
    """Return a fully loaded record without leaving a caller read txn open."""

    session.refresh(record)
    session.expunge(record)
    session.rollback()
    return record


def _validate_stage_graph(target: str, stage: str) -> None:
    if stage not in STAGE_GRAPH.get(target, {}):
        raise ValueError(f"Podcast stage '{stage}' is invalid for target '{target}'")


def _evaluate_external_asr_export(
    session: Session,
    article: ArticleRecord,
    *,
    stage: str,
    input_artifact_kind: str,
) -> tuple[str, list[str]]:
    """Fail closed before source audio may cross the deployment boundary.

    This privacy gate is deliberately limited to the third-party ASR boundary:
    caching source audio locally, using a publisher transcript, and generating
    digest audio do not export the source recording.

    Keep the persisted reason generic.  The source configuration may contain a
    signed feed URL or another credential and must never be copied into an audit
    or operator-facing error field.
    """

    if stage != "asr" or input_artifact_kind != "source_audio":
        return "eligible", []

    from services.user_sources import source_content_may_leave_deployment

    if source_content_may_leave_deployment(session, article.source_id):
        return "eligible", []
    return "blocked_rights", [
        "Podcast source audio may not leave this deployment for external ASR"
    ]


def _evaluate_input_binding(
    session: Session,
    process: PodcastProcessingRecord,
    policy: Optional[StagePolicyCheck],
) -> tuple[str, list[str]]:
    """Revalidate the immutable source input at every execution boundary."""

    artifact_id = str(process.input_artifact_id or "").strip()
    artifact_kind = str(process.input_artifact_kind or "").strip()
    content_hash = str(process.input_content_hash or "").strip().lower()
    if not artifact_id or not artifact_kind or not content_hash:
        return "invalid_input", ["Podcast processing input binding is missing"]
    if (
        artifact_kind == "source_audio"
        and process.requested_target == "full_analysis"
        and process.stage == "analyze"
    ):
        # Once ASR has committed, the durable normalized transcript is the
        # analyze-stage input.  Source audio may expire after the paid call and
        # must not strand a restart-safe local analysis.
        predecessor = session.exec(
            select(PodcastStageAttemptRecord)
            .where(
                PodcastStageAttemptRecord.processing_id == process.id,
                PodcastStageAttemptRecord.stage == "asr",
                PodcastStageAttemptRecord.submission_state == "succeeded",
                PodcastStageAttemptRecord.output_artifact_kind
                == "normalized_transcript",
            )
            .order_by(PodcastStageAttemptRecord.attempt_no.desc())
        ).first()
        transcript = (
            session.get(PodcastTextArtifactRecord, predecessor.output_artifact_id)
            if predecessor is not None and predecessor.output_artifact_id
            else None
        )
        if (
            predecessor is None
            or transcript is None
            or transcript.processing_id != process.id
            or transcript.episode_id != process.episode_id
            or transcript.kind != "normalized_transcript"
            or transcript.content_hash != predecessor.output_hash
        ):
            return "invalid_input", [
                "Podcast normalized transcript output is no longer usable"
            ]
    elif artifact_kind == "source_audio":
        source_audio = session.get(PodcastArtifactRecord, artifact_id)
        if (
            source_audio is None
            or source_audio.episode_id != process.episode_id
            or source_audio.kind != "source_audio"
            or source_audio.status != "ready"
            or source_audio.content_hash != content_hash
            or not source_audio.expires_at
        ):
            return "invalid_input", ["Podcast source audio input is no longer current"]
    else:
        text_artifact = session.get(PodcastTextArtifactRecord, artifact_id)
        publication = session.get(
            PodcastTextPublicationRecord, f"{process.episode_id}:{artifact_kind}"
        )
        producing_attempt = (
            session.get(
                PodcastStageAttemptRecord, text_artifact.producing_attempt_id
            )
            if text_artifact is not None
            and text_artifact.producing_attempt_id
            else None
        )
        producing_process = (
            session.get(PodcastProcessingRecord, text_artifact.processing_id)
            if text_artifact is not None and text_artifact.processing_id
            else None
        )
        normalized_bound = bool(
            artifact_kind == "normalized_transcript"
            and text_artifact is not None
            and producing_attempt is not None
            and producing_process is not None
            and producing_attempt.processing_id == text_artifact.processing_id
            and producing_process.id == text_artifact.processing_id
            and producing_process.episode_id == process.episode_id
            and (
                text_artifact.processing_id == process.id
                or process.requested_target == "full_analysis"
            )
            and producing_attempt.stage == "asr"
            and producing_attempt.submission_state == "succeeded"
            and producing_attempt.output_artifact_kind == "normalized_transcript"
            and producing_attempt.output_artifact_id == text_artifact.id
            and producing_attempt.output_hash == text_artifact.content_hash
            and hashlib.sha256(text_artifact.inline_text.encode("utf-8")).hexdigest()
            == text_artifact.content_hash
        )
        if (
            text_artifact is None
            or text_artifact.episode_id != process.episode_id
            or text_artifact.kind != artifact_kind
            or text_artifact.content_hash != content_hash
            or (
                not normalized_bound
                and (
                    publication is None
                    or publication.status != "published"
                    or publication.artifact_id != artifact_id
                    or publication.authority_id != text_artifact.authority_id
                )
            )
        ):
            return "invalid_input", ["Podcast text input is no longer current"]

    if process.requested_target != "digest_audio":
        return "eligible", []
    from services.podcast_artifacts import (
        PodcastArtifactConflict,
        require_current_narration_dependency,
    )

    try:
        narration = require_current_narration_dependency(
            session,
            episode_id=process.episode_id,
            narration_artifact_id=process.narration_artifact_id,
            narration_content_hash=process.narration_content_hash,
        )
    except PodcastArtifactConflict as exc:
        return "invalid_input", [str(exc)]
    config = getattr(policy, "config", None)
    if config is None:
        return "eligible", []
    voice = str(process.voice_profile_id or "")
    if voice not in set(config.voice_profiles):
        return "invalid_input", ["Podcast voice profile is no longer registered"]
    authority = str(narration.authority_id or "").strip()
    if authority:
        return "invalid_input", ["Podcast narration is not locally authored"]
    return "eligible", []


def _evaluate_processing_eligibility(
    session: Session,
    process: PodcastProcessingRecord,
    policy: Optional[StagePolicyCheck],
) -> tuple[str, list[str]]:
    article = session.get(ArticleRecord, process.episode_id)
    if article is None or article.content_type != "podcast_episode":
        return "invalid_input", ["Podcast episode no longer exists"]
    eligibility, reasons = _evaluate_external_asr_export(
        session,
        article,
        stage=str(process.stage or "").strip().lower(),
        input_artifact_kind=str(process.input_artifact_kind or "").strip().lower(),
    )
    if eligibility != "eligible":
        return eligibility, reasons
    return _evaluate_input_binding(session, process, policy)


def _apply_denial(
    process: PodcastProcessingRecord,
    eligibility: str,
    reasons: list[str],
    *,
    stamp: str,
) -> None:
    process.eligibility_status = eligibility
    process.eligibility_reasons_json = json.dumps(
        reasons, ensure_ascii=False, separators=(",", ":")
    )
    process.processing_status = "not_required"
    process.lease_owner = None
    process.lease_token = None
    process.lease_expires_at = None
    process.next_retry_at = None
    process.error_code = eligibility
    process.error_message = reasons[0][:500] if reasons else eligibility
    process.updated_at = stamp


def deterministic_input_fingerprint(value: Any) -> str:
    """Hash canonical JSON so equivalent inputs produce the same run identity."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_nonempty(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _require_minor_units(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer CNY minor-unit value")
    return value


def _validate_sha256(value: str, field: str) -> str:
    normalized = _require_nonempty(value, field).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _validate_fingerprint(value: str) -> str:
    return _validate_sha256(value, "input_fingerprint")


def _same_run(record: PodcastProcessingRecord, expected: dict[str, Any]) -> bool:
    return all(getattr(record, key) == value for key, value in expected.items())


@_transaction_boundary
def enqueue_processing(
    session: Session,
    *,
    episode_id: str,
    stage: str,
    input_fingerprint: str,
    pipeline_version: str,
    policy_version: str,
    requested_target: str,
    idempotency_key: str,
    selection_source: str = "policy",
    requested_by: Optional[str] = None,
    request_reason: str = "",
    eligibility_reasons: Optional[list[str]] = None,
    input_safety_evaluator: Optional[
        Callable[[Session, ArticleRecord, str, str], tuple[bool, list[str]]]
    ] = None,
    estimated_cost_minor: int = 0,
    input_artifact_id: Optional[str] = None,
    input_artifact_kind: Optional[str] = None,
    input_content_hash: Optional[str] = None,
    input_language: Optional[str] = None,
    budget_scope: Optional[str] = None,
    budget_period: Optional[str] = None,
    budget_limit_minor: Optional[int] = None,
    per_run_budget_minor: Optional[int] = None,
    narration_artifact_id: Optional[str] = None,
    narration_content_hash: Optional[str] = None,
    voice_profile_id: Optional[str] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
    _transaction_open: bool = False,
) -> PodcastProcessingRecord:
    """Create or reuse one effective eligible run.

    Idempotency keys are immutable: reusing a key with different run identity
    fails instead of silently retargeting an already-audited request.
    """

    stage = _require_nonempty(stage, "stage").lower()
    if stage not in PODCAST_PROCESSING_STAGES:
        raise ValueError(f"unknown Podcast stage: {stage}")
    _require_stage(policy, stage, boundary="enqueue")
    episode_id = _require_nonempty(episode_id, "episode_id")
    fingerprint = _validate_fingerprint(input_fingerprint)
    pipeline_version = _require_nonempty(pipeline_version, "pipeline_version")
    requested_target = _require_nonempty(requested_target, "requested_target")
    if requested_target not in {
        "transcript",
        "full_analysis",
        "digest_blog",
        "digest_audio",
    }:
        raise ValueError(
            "requested_target must be transcript, full_analysis, digest_blog or digest_audio"
        )
    _validate_stage_graph(requested_target, stage)
    bound_artifact_id = _require_nonempty(input_artifact_id, "input_artifact_id")
    bound_artifact_kind = _require_nonempty(
        input_artifact_kind, "input_artifact_kind"
    ).lower()
    if bound_artifact_kind not in {
        "source_audio",
        "publisher_transcript",
        "normalized_transcript",
        "transcript_zh",
        "digest_blog_zh",
        "narration_script_zh",
    }:
        raise ValueError("unknown Podcast processing input artifact kind")
    bound_content_hash = _validate_fingerprint(str(input_content_hash or ""))
    bound_language = _require_nonempty(input_language, "input_language").lower()
    bound_budget_scope = _require_nonempty(budget_scope, "budget_scope")
    bound_budget_period = _require_nonempty(budget_period, "budget_period")
    bound_budget_limit = _require_minor_units(
        budget_limit_minor, "budget_limit_minor"
    )
    bound_per_run_budget = _require_minor_units(
        per_run_budget_minor, "per_run_budget_minor"
    )
    if (
        bound_budget_limit <= 0
        or bound_per_run_budget <= 0
        or bound_per_run_budget > bound_budget_limit
    ):
        raise ValueError("Podcast processing budget snapshot is invalid")
    narration_id = str(narration_artifact_id or "").strip() or None
    narration_hash = str(narration_content_hash or "").strip().lower() or None
    voice_id = str(voice_profile_id or "").strip() or None
    if requested_target == "digest_audio":
        if not narration_id or not voice_id:
            raise ValueError("digest_audio requires narration and voice bindings")
        narration_hash = _validate_fingerprint(str(narration_hash or ""))
        if (
            bound_artifact_kind != "narration_script_zh"
            or bound_artifact_id != narration_id
            or bound_content_hash != narration_hash
        ):
            raise ValueError("digest_audio input must match its narration binding")
    elif narration_id is not None or narration_hash is not None or voice_id is not None:
        raise ValueError("narration and voice bindings are only valid for digest_audio")
    idempotency_key = _require_nonempty(idempotency_key, "idempotency_key")
    estimated = _require_minor_units(estimated_cost_minor, "estimated_cost_minor")
    selection_source = _require_nonempty(selection_source, "selection_source").lower()
    if selection_source not in {"policy", "editor"}:
        raise ValueError("selection_source must be policy or editor")
    reason = str(request_reason or "").strip()
    if selection_source == "editor" and not reason:
        raise ValueError("request_reason is required for an editor request")
    if not _transaction_open:
        _require_clean_session(session, "processing enqueue")
        if session.connection().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    article = session.get(ArticleRecord, episode_id)
    if article is None or article.content_type != "podcast_episode":
        raise ValueError("episode_id must reference a podcast_episode article")
    source_id = article.source_id
    if session.connection().dialect.name == "postgresql":
        session.expire(article)
        article = session.get(ArticleRecord, episode_id)
        if (
            article is None
            or article.content_type != "podcast_episode"
            or article.source_id != source_id
        ):
            raise PodcastProcessingConflict(
                "Podcast episode source changed during enqueue"
            )

    expected = {
        "episode_id": episode_id,
        "input_fingerprint": fingerprint,
        "pipeline_version": pipeline_version,
        "policy_version": str(policy_version or ""),
        "requested_target": requested_target,
        "input_artifact_id": bound_artifact_id,
        "input_artifact_kind": bound_artifact_kind,
        "input_content_hash": bound_content_hash,
        "input_language": bound_language,
        "budget_scope": bound_budget_scope,
        "budget_period": bound_budget_period,
        "budget_limit_minor": bound_budget_limit,
        "per_run_budget_minor": bound_per_run_budget,
        "narration_artifact_id": narration_id,
        "narration_content_hash": narration_hash,
        "voice_profile_id": voice_id,
    }
    eligibility, evaluated_reasons = _evaluate_external_asr_export(
        session,
        article,
        stage=stage,
        input_artifact_kind=bound_artifact_kind,
    )
    evaluated_reasons = [*evaluated_reasons, *(eligibility_reasons or [])]
    if eligibility == "eligible" and input_safety_evaluator is not None:
        safe, safety_reasons = input_safety_evaluator(
            session, article, requested_target, stage
        )
        if not safe:
            eligibility = "invalid_input"
        evaluated_reasons.extend(str(item) for item in safety_reasons)

    def finish(record: PodcastProcessingRecord) -> PodcastProcessingRecord:
        if _transaction_open:
            session.flush()
            return record
        session.commit()
        return _detach_after_read(session, record)

    def reuse(record: PodcastProcessingRecord) -> PodcastProcessingRecord:
        active = {"not_required", "queued", "retry_wait", "running"}
        if record.processing_status in active:
            if eligibility == "eligible" and record.processing_status == "not_required":
                record.eligibility_status = "eligible"
                record.eligibility_reasons_json = "[]"
                record.processing_status = "queued"
                record.error_code = ""
                record.error_message = ""
                record.queued_at = _iso(_as_utc(now))
                record.updated_at = record.queued_at
                session.add(record)
            elif eligibility != "eligible":
                _apply_denial(
                    record,
                    eligibility,
                    evaluated_reasons,
                    stamp=_iso(_as_utc(now)),
                )
                session.add(record)
        return finish(record)

    existing = session.exec(
        select(PodcastProcessingRecord).where(
            PodcastProcessingRecord.idempotency_key == idempotency_key
        )
    ).first()
    if existing is not None:
        if not _same_run(existing, expected):
            raise PodcastProcessingConflict(
                "idempotency key already belongs to a different processing run"
            )
        return reuse(existing)

    effective = session.exec(
        select(PodcastProcessingRecord).where(
            PodcastProcessingRecord.episode_id == episode_id,
            PodcastProcessingRecord.input_fingerprint == fingerprint,
            PodcastProcessingRecord.pipeline_version == pipeline_version,
            PodcastProcessingRecord.policy_version == str(policy_version or ""),
            PodcastProcessingRecord.requested_target == requested_target,
            PodcastProcessingRecord.budget_scope == bound_budget_scope,
            PodcastProcessingRecord.budget_period == bound_budget_period,
            PodcastProcessingRecord.budget_limit_minor == bound_budget_limit,
            PodcastProcessingRecord.per_run_budget_minor == bound_per_run_budget,
        )
    ).first()
    if effective is not None:
        return reuse(effective)

    stamp = _iso(_as_utc(now))
    record = PodcastProcessingRecord(
        id=uuid.uuid4().hex,
        episode_id=episode_id,
        input_fingerprint=fingerprint,
        pipeline_version=pipeline_version,
        policy_version=str(policy_version or ""),
        requested_target=requested_target,
        selection_source=selection_source,
        requested_by=(str(requested_by).strip() if requested_by else None),
        request_reason=reason,
        idempotency_key=idempotency_key,
        input_artifact_id=bound_artifact_id,
        input_artifact_kind=bound_artifact_kind,
        input_content_hash=bound_content_hash,
        input_language=bound_language,
        budget_scope=bound_budget_scope,
        budget_period=bound_budget_period,
        budget_limit_minor=bound_budget_limit,
        per_run_budget_minor=bound_per_run_budget,
        narration_artifact_id=narration_id,
        narration_content_hash=narration_hash,
        voice_profile_id=voice_id,
        eligibility_status=eligibility,
        eligibility_reasons_json=json.dumps(
            evaluated_reasons, ensure_ascii=False, separators=(",", ":")
        ),
        processing_status="queued" if eligibility == "eligible" else "not_required",
        stage=stage,
        cost_currency="CNY",
        estimated_cost_minor=estimated,
        queued_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(record)
    if _transaction_open:
        session.flush()
        return record
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.exec(
            select(PodcastProcessingRecord).where(
                or_(
                    PodcastProcessingRecord.idempotency_key == idempotency_key,
                    (
                        (PodcastProcessingRecord.episode_id == episode_id)
                        & (PodcastProcessingRecord.input_fingerprint == fingerprint)
                        & (PodcastProcessingRecord.pipeline_version == pipeline_version)
                        & (
                            PodcastProcessingRecord.policy_version
                            == str(policy_version or "")
                        )
                        & (PodcastProcessingRecord.requested_target == requested_target)
                        & (PodcastProcessingRecord.budget_scope == bound_budget_scope)
                        & (PodcastProcessingRecord.budget_period == bound_budget_period)
                        & (
                            PodcastProcessingRecord.budget_limit_minor
                            == bound_budget_limit
                        )
                        & (
                            PodcastProcessingRecord.per_run_budget_minor
                            == bound_per_run_budget
                        )
                    ),
                )
            )
        ).first()
        if winner is None or not _same_run(winner, expected):
            raise PodcastProcessingConflict("processing enqueue raced with a conflicting run")
        return _detach_after_read(session, winner)
    return _detach_after_read(session, record)


def _claimable(now_stamp: str):
    return or_(
        PodcastProcessingRecord.processing_status == "queued",
        (
            (PodcastProcessingRecord.processing_status == "retry_wait")
            & (PodcastProcessingRecord.next_retry_at <= now_stamp)
        ),
        (
            (PodcastProcessingRecord.processing_status == "running")
            & (PodcastProcessingRecord.lease_expires_at <= now_stamp)
        ),
    )


def _drain_candidate():
    """Denied terminal rows that still own a provider-side hold.

    ``reconciliation_required`` is deliberately absent: it is the durable
    manual-intervention state. An operator can confirm a TaskId as submitted,
    which moves the process to ``retry_wait`` and makes it auto-drainable again.
    """

    active_provider_attempt = (
        select(PodcastStageAttemptRecord.id)
        .where(
            PodcastStageAttemptRecord.processing_id
            == PodcastProcessingRecord.id,
            PodcastStageAttemptRecord.execution_kind == "provider",
            PodcastStageAttemptRecord.submission_state.in_(
                ACTIVE_ATTEMPT_STATES
            ),
        )
        .exists()
    )
    return (
        PodcastProcessingRecord.processing_status == "not_required"
    ) & active_provider_attempt


def _drainable_provider_attempt(
    session: Session,
    processing_id: str,
    *,
    include_settled: bool = True,
) -> Optional[PodcastStageAttemptRecord]:
    attempt = _latest_active_attempt(session, processing_id)
    if attempt is None or attempt.execution_kind != "provider":
        return None
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt.id
        )
    ).first()
    if (
        reservation is None
        or reservation.status == "released"
        or (not include_settled and reservation.status != "reserved")
    ):
        return None
    return attempt


@_transaction_boundary
def claim_next_processing(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    policy: Optional[StagePolicyCheck] = None,
    requested_target: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> Optional[PodcastProcessingClaim]:
    """Atomically claim one due row, including work abandoned after restart."""

    worker = _require_nonempty(worker_id, "worker_id")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    current = _as_utc(now)
    stamp = _iso(current)
    expires = _iso(current + dt.timedelta(seconds=lease_seconds))
    _require_clean_session(session, "processing claim")
    allowed_stages: Optional[set[str]] = None
    if policy is not None:
        configured = getattr(getattr(policy, "config", None), "allowed_stages", None)
        if configured is None:
            configured = getattr(policy, "allowed", None)
        if configured is not None:
            allowed_stages = {str(item) for item in configured}
    conditions = [or_(_claimable(stamp), _drain_candidate())]
    if requested_target is not None:
        conditions.append(
            PodcastProcessingRecord.requested_target
            == _require_nonempty(requested_target, "requested_target")
        )
    if allowed_stages is not None:
        if not allowed_stages:
            return None
        conditions.append(PodcastProcessingRecord.stage.in_(allowed_stages))
    candidates = list(
        session.exec(
            select(PodcastProcessingRecord)
            .where(*conditions)
            .order_by(PodcastProcessingRecord.queued_at, PodcastProcessingRecord.id)
        ).all()
    )
    for candidate in candidates:
        eligibility, reasons = _evaluate_processing_eligibility(
            session, candidate, policy
        )
        active_provider_attempt = _drainable_provider_attempt(
            session,
            candidate.id,
            # A terminal row with an already-settled output has no paid hold
            # left to drain and may still be resumed explicitly by an admin.
            # A settled attempt on an expired running row is instead the
            # recoverable crash window between settlement and terminal commit.
            include_settled=candidate.processing_status != "not_required",
        )
        drain_only = active_provider_attempt is not None and (
            eligibility != "eligible"
            or candidate.eligibility_status != "eligible"
            or candidate.processing_status == "not_required"
        )
        if eligibility != "eligible" and not drain_only:
            _apply_denial(candidate, eligibility, reasons, stamp=stamp)
            session.add(candidate)
            session.commit()
            continue
        if (
            not drain_only
            and eligibility == "eligible"
            and candidate.processing_status in {
                "not_required",
                "reconciliation_required",
            }
        ):
            # Recovered input eligibility does not implicitly reactivate
            # terminal or operator-parked work. Those transitions remain
            # admin-owned.
            continue
        try:
            _require_stage(policy, candidate.stage, boundary="claim")
        except PermissionError:
            continue
        lease_token = uuid.uuid4().hex
        result = session.exec(
            update(PodcastProcessingRecord)
            .where(
                PodcastProcessingRecord.id == candidate.id,
                PodcastProcessingRecord.fencing_token == candidate.fencing_token,
                (
                    PodcastProcessingRecord.processing_status
                    == candidate.processing_status
                    if drain_only
                    and candidate.processing_status
                    == "not_required"
                    else _claimable(stamp)
                ),
            )
            .values(
                processing_status="running",
                lease_owner=worker,
                lease_token=lease_token,
                lease_expires_at=expires,
                heartbeat_at=stamp,
                fencing_token=PodcastProcessingRecord.fencing_token + 1,
                next_retry_at=None,
                started_at=func.coalesce(PodcastProcessingRecord.started_at, stamp),
                updated_at=stamp,
                **(
                    {
                        "eligibility_status": eligibility,
                        "eligibility_reasons_json": json.dumps(
                            reasons,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "error_code": eligibility,
                        "error_message": (
                            reasons[0][:500] if reasons else eligibility
                        ),
                    }
                    if drain_only and eligibility != "eligible"
                    else {}
                ),
            )
        )
        if result.rowcount != 1:
            session.rollback()
            continue
        previous = _latest_active_attempt(session, candidate.id)
        if (
            previous is not None
            and previous.submission_state == "prepared"
            and previous.lease_token != lease_token
        ):
            if previous.execution_kind == "local":
                # Local work has no remote side effect to reconcile. Its stale
                # zero-cost reservation can be released and a fresh fenced
                # attempt may start under the newly acquired lease.
                previous.submission_state = "failed_retryable"
                previous.request_unknown = False
                previous.retry_state = "none"
                previous.error_code = "lease_expired_local_attempt"
                previous.error_message = "local attempt lease expired before completion"
                previous.completed_at = stamp
                _release_reservation(session, previous.id, stamp=stamp)
            else:
                # A process crash can happen after the HTTP request left the host
                # but before mark_provider_submission. Preserve the request key and
                # reservation until the provider confirms it was not submitted.
                previous.submission_state = "request_unknown"
                previous.request_unknown = True
                previous.retry_state = "reconcile_required"
                previous.error_code = "lease_expired_submission_unknown"
                previous.error_message = "lease expired before provider outcome was persisted"
            previous.updated_at = stamp
            session.add(previous)
        session.commit()
        claimed = session.get(PodcastProcessingRecord, candidate.id)
        if claimed is None or claimed.lease_token != lease_token:
            raise PodcastProcessingConflict("claimed processing disappeared")
        claim = PodcastProcessingClaim(
            processing_id=claimed.id,
            episode_id=claimed.episode_id,
            stage=claimed.stage,
            input_fingerprint=claimed.input_fingerprint,
            pipeline_version=claimed.pipeline_version,
            lease_owner=worker,
            lease_token=lease_token,
            fencing_token=claimed.fencing_token,
            lease_expires_at=expires,
            drain_only=drain_only,
        )
        # ``get`` opens a read transaction after the claim commit. Close it so
        # begin_stage_attempt can acquire SQLite's budget write lock before its
        # first capacity read.
        session.rollback()
        return claim
    session.rollback()
    return None


def _fenced_where(claim: PodcastProcessingClaim, now_stamp: str):
    return (
        (PodcastProcessingRecord.id == claim.processing_id)
        & (PodcastProcessingRecord.processing_status == "running")
        & (PodcastProcessingRecord.stage == claim.stage)
        & (PodcastProcessingRecord.lease_owner == claim.lease_owner)
        & (PodcastProcessingRecord.lease_token == claim.lease_token)
        & (PodcastProcessingRecord.fencing_token == claim.fencing_token)
        & (PodcastProcessingRecord.lease_expires_at > now_stamp)
    )


@_transaction_boundary
def resolve_claim_before_attempt(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    error_code: str,
    redacted_error_message: str,
    retryable: bool,
    retry_at: Optional[dt.datetime] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Release a fenced claim when reservation failed before attempt creation.

    This transition never creates an attempt, reservation, or ledger. Historical
    completed attempts may exist, but an active attempt makes this primitive
    invalid because its cost/submission state must use the attempt-aware paths.
    """

    if not isinstance(retryable, bool):
        raise ValueError("retryable must be a boolean")
    current = _as_utc(now)
    stamp = _iso(current)
    retry = _as_utc(retry_at) if retry_at is not None else None
    if retryable and (retry is None or retry <= current):
        raise ValueError("retry_at must be later than now for a retryable claim")
    if not retryable and retry is not None:
        raise ValueError("retry_at requires a retryable claim")
    _require_clean_session(session, "pre-attempt claim resolution")
    _require_stage(policy, claim.stage, boundary="commit")
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        session.rollback()
        raise PodcastProcessingConflict("processing does not exist")
    expected_attempt_count = process.attempt_count
    if _latest_active_attempt(session, claim.processing_id) is not None:
        session.rollback()
        raise PodcastProcessingConflict(
            "pre-attempt claim resolution cannot replace an active attempt"
        )
    eligibility, reasons = _evaluate_processing_eligibility(
        session, process, policy
    )
    eligible = eligibility == "eligible"
    effective_retryable = retryable and eligible
    values: dict[str, Any] = {
        "processing_status": (
            "retry_wait"
            if effective_retryable
            else ("failed" if eligible else "not_required")
        ),
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
        "next_retry_at": _iso(retry) if effective_retryable else None,
        "error_code": (
            str(error_code or "")[:120] if eligible else eligibility
        ),
        "error_message": (
            str(redacted_error_message or "")[:500]
            if eligible
            else (reasons[0][:500] if reasons else eligibility)
        ),
        "finished_at": None if effective_retryable else stamp,
        "updated_at": stamp,
    }
    if not eligible:
        values.update(
            eligibility_status=eligibility,
            eligibility_reasons_json=json.dumps(
                reasons,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(
            _fenced_where(claim, stamp),
            PodcastProcessingRecord.attempt_count == expected_attempt_count,
        )
        .values(**values)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    session.commit()
    resolved = session.get(PodcastProcessingRecord, claim.processing_id)
    if resolved is None:
        raise PodcastProcessingConflict(
            "processing disappeared after pre-attempt resolution"
        )
    return _detach_after_read(session, resolved)


def _cost_recovery_where(claim: PodcastProcessingClaim, now_stamp: str):
    """Fence cost/reconciliation writes after eligibility has blocked output."""

    return (
        (PodcastProcessingRecord.id == claim.processing_id)
        & (PodcastProcessingRecord.stage == claim.stage)
        & (PodcastProcessingRecord.fencing_token == claim.fencing_token)
        & or_(
            _fenced_where(claim, now_stamp),
            (
                (PodcastProcessingRecord.processing_status == "not_required")
                & (PodcastProcessingRecord.eligibility_status != "eligible")
            ),
        )
    )


@_transaction_boundary
def heartbeat_processing(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    lease_seconds: int,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingClaim:
    """Renew an unexpired lease without changing its ABA-resistant token."""

    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    _require_clean_session(session, "processing heartbeat")
    _require_stage(policy, claim.stage, boundary="claim")
    current = _as_utc(now)
    stamp = _iso(current)
    expires = _iso(current + dt.timedelta(seconds=lease_seconds))
    result = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(heartbeat_at=stamp, lease_expires_at=expires, updated_at=stamp)
    )
    if result.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    session.commit()
    return PodcastProcessingClaim(
        **{**claim.__dict__, "lease_expires_at": expires}
    )


def _latest_active_attempt(
    session: Session, processing_id: str
) -> Optional[PodcastStageAttemptRecord]:
    return session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == processing_id,
            PodcastStageAttemptRecord.submission_state.in_(ACTIVE_ATTEMPT_STATES),
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()


def _release_reservation(
    session: Session, attempt_id: str, *, stamp: str
) -> None:
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    if reservation is not None and reservation.status == "reserved":
        result = session.exec(
            update(PodcastBudgetReservationRecord)
            .where(
                PodcastBudgetReservationRecord.id == reservation.id,
                PodcastBudgetReservationRecord.status == "reserved",
            )
            .values(status="released", released_at=stamp, updated_at=stamp)
        )
        if result.rowcount != 1:
            raise PodcastProcessingConflict("budget reservation transition raced")


def _begin_budget_transaction(
    session: Session,
    *,
    budget_scope: str,
    budget_period: str,
    provider_usage_plan: Optional[ProviderUsagePlan] = None,
    provider_quota_key: Optional[tuple[str, str, str]] = None,
) -> None:
    """Serialize read-capacity/write-reservation for one database transaction.

    SQLite needs ``BEGIN IMMEDIATE`` before the first capacity read; otherwise
    two deferred transactions can both observe the same balance. PostgreSQL
    uses a transaction-scoped advisory lock keyed by the configured budget
    scope/period. Other dialects fail closed until an equivalent lock is added.
    """

    _require_clean_session(session, "budget reservation")
    connection = session.connection()
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        return
    if dialect == "postgresql":
        lock_keys = {f"cny\x1f{budget_scope}\x1f{budget_period}"}
        if provider_usage_plan is not None:
            provider_quota_key = (
                provider_usage_plan.quota_scope,
                provider_usage_plan.quota_period,
                provider_usage_plan.unit.value,
            )
        if provider_quota_key is not None:
            scope, period, unit = provider_quota_key
            lock_keys.add(f"provider\x1f{scope}\x1f{period}\x1f{unit}")
        for budget_key in sorted(lock_keys):
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:budget_key, 0))"),
                {"budget_key": budget_key},
            )
        return
    session.rollback()
    raise PodcastProcessingError(
        f"atomic Podcast budget reservation is unsupported for dialect: {dialect}"
    )


def _reservation_matches_usage_plan(
    reservation: PodcastBudgetReservationRecord,
    plan: Optional[ProviderUsagePlan],
) -> bool:
    if plan is None:
        return reservation.provider_quota_scope is None
    return (
        reservation.provider_quota_scope == plan.quota_scope
        and reservation.provider_quota_period == plan.quota_period
        and reservation.provider_quota_unit == plan.unit.value
        and reservation.provider_quota_window_start_at == _iso(plan.window_start_at)
        and reservation.provider_quota_window_end_at == _iso(plan.window_end_at)
        and reservation.provider_quota_limit_units == plan.limit_units
        and reservation.reserved_usage_units == plan.reserved_units
        and reservation.unit_price_cny_minor == plan.unit_price_cny_minor
        and reservation.price_unit_count == plan.price_unit_count
        and reservation.pricing_revision == plan.pricing_revision
    )


def _require_provider_usage_capacity(
    session: Session, plan: ProviderUsagePlan
) -> None:
    reservations = list(session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.provider_quota_scope == plan.quota_scope,
            PodcastBudgetReservationRecord.provider_quota_period == plan.quota_period,
            PodcastBudgetReservationRecord.provider_quota_unit == plan.unit.value,
        )
    ).all())
    expected_definition = (
        _iso(plan.window_start_at),
        _iso(plan.window_end_at),
        plan.unit_price_cny_minor,
        plan.price_unit_count,
        plan.pricing_revision,
    )
    for reservation in reservations:
        definition = (
            reservation.provider_quota_window_start_at,
            reservation.provider_quota_window_end_at,
            reservation.unit_price_cny_minor,
            reservation.price_unit_count,
            reservation.pricing_revision,
        )
        if definition != expected_definition:
            raise PodcastProcessingConflict(
                "provider quota scope/period was reused with a different definition"
            )
        if reservation.provider_quota_breached:
            raise PodcastProviderQuotaExceeded(
                "provider quota is frozen after an observed overage"
            )
    spent = int(session.exec(
        select(func.coalesce(func.sum(PodcastCostLedgerRecord.actual_usage_units), 0)).where(
            PodcastCostLedgerRecord.provider_quota_scope == plan.quota_scope,
            PodcastCostLedgerRecord.provider_quota_period == plan.quota_period,
            PodcastCostLedgerRecord.provider_quota_unit == plan.unit.value,
        )
    ).one())
    reserved = sum(
        int(item.reserved_usage_units or 0)
        for item in reservations
        if item.status == "reserved"
    )
    if spent + reserved + plan.reserved_units > plan.limit_units:
        raise PodcastProviderQuotaExceeded(
            "provider usage reservation exceeds the configured cap"
        )


@_transaction_boundary
def begin_stage_attempt(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    input_hash: str,
    settings_fingerprint: str,
    provider_name: str,
    model_name: str,
    provider_revision: str,
    provider_request_key: str,
    execution_kind: str,
    estimated_cost_minor: int,
    budget_scope: str,
    budget_period: str,
    budget_limit_minor: int,
    reservation_idempotency_key: str,
    usage_settlement_mode: str = "",
    reservation_expires_at: Optional[str] = None,
    provider_usage_plan: Optional[ProviderUsagePlan] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastStageAttemptRecord:
    """Fence one explicit execution kind and reserve its CNY budget atomically."""

    _require_stage(policy, claim.stage, boundary="provider_submit")
    if claim.drain_only:
        raise PodcastEligibilityDenied(
            "drain-only processing cannot create a provider attempt"
        )
    execution = _require_nonempty(execution_kind, "execution_kind")
    if execution not in {"provider", "local"}:
        raise ValueError("execution_kind must be provider or local")
    settlement_mode = str(usage_settlement_mode or "").strip().lower()
    if claim.stage == "tts" and execution == "provider":
        settlement_mode = settlement_mode or "manual"
        if settlement_mode not in {"manual", "submitted_characters"}:
            raise ValueError("unsupported TTS usage settlement mode")
    elif settlement_mode:
        raise ValueError("usage settlement mode is only valid for provider TTS")
    estimate = _require_minor_units(estimated_cost_minor, "estimated_cost_minor")
    if execution == "local" and estimate != 0:
        raise ValueError("local execution estimated_cost_minor must be zero")
    if provider_usage_plan is not None and not isinstance(
        provider_usage_plan, ProviderUsagePlan
    ):
        raise ValueError("provider_usage_plan must be a ProviderUsagePlan")
    if execution == "local" and provider_usage_plan is not None:
        raise ValueError("local execution cannot reserve provider usage")
    if (
        execution == "provider"
        and str(provider_name or "").strip().lower() == "aliyun-isi"
        and claim.stage in {"asr", "tts"}
        and provider_usage_plan is None
    ):
        raise PodcastProviderQuotaExceeded(
            "Aliyun ISI ASR/TTS submission requires a provider usage plan"
        )
    if (
        provider_usage_plan is not None
        and estimate != provider_usage_plan.estimated_cost_minor
    ):
        raise ValueError(
            "estimated_cost_minor must match the provider usage pricing snapshot"
        )
    expected_usage_unit = {
        "asr": ProviderUsageUnit.AUDIO_SECONDS,
        "tts": ProviderUsageUnit.TTS_CHARACTERS,
    }.get(claim.stage)
    if provider_usage_plan is not None and (
        expected_usage_unit is None
        or provider_usage_plan.unit is not expected_usage_unit
    ):
        raise ValueError("provider usage unit does not match the processing stage")
    limit = _require_minor_units(budget_limit_minor, "budget_limit_minor")
    budget_scope = _require_nonempty(budget_scope, "budget_scope")
    budget_period = _require_nonempty(budget_period, "budget_period")
    reservation_key = _require_nonempty(
        reservation_idempotency_key, "reservation_idempotency_key"
    )
    provider_request_key = _require_nonempty(
        provider_request_key, "provider_request_key"
    )
    current = _as_utc(now)
    stamp = _iso(current)
    if (
        execution == "provider"
        and str(provider_name or "").strip().lower() == "aliyun-isi"
        and provider_usage_plan is not None
        and not _provider_usage_plan_is_trusted(
            policy,
            provider_name="aliyun-isi",
            stage=claim.stage,
            plan=provider_usage_plan,
            now=current,
        )
    ):
        raise PodcastProviderQuotaExceeded(
            "Aliyun ISI provider usage plan does not match trusted configuration"
        )

    _begin_budget_transaction(
        session,
        budget_scope=budget_scope,
        budget_period=budget_period,
        provider_usage_plan=provider_usage_plan,
    )

    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if (
        process is None
        or process.processing_status != "running"
        or process.stage != claim.stage
        or process.lease_owner != claim.lease_owner
        or process.lease_token != claim.lease_token
        or process.fencing_token != claim.fencing_token
        or not process.lease_expires_at
        or process.lease_expires_at <= stamp
    ):
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    article = session.get(ArticleRecord, process.episode_id)
    if article is None or article.content_type != "podcast_episode":
        session.rollback()
        raise PodcastEligibilityDenied("Podcast episode no longer exists")
    source_id = article.source_id
    if session.connection().dialect.name == "postgresql":
        session.expire_all()
        process = session.get(PodcastProcessingRecord, claim.processing_id)
        article = session.get(ArticleRecord, claim.episode_id)
        if (
            process is None
            or process.processing_status != "running"
            or process.stage != claim.stage
            or process.lease_owner != claim.lease_owner
            or process.lease_token != claim.lease_token
            or process.fencing_token != claim.fencing_token
            or not process.lease_expires_at
            or process.lease_expires_at <= stamp
        ):
            session.rollback()
            raise PodcastLeaseLost("processing lease is expired or fenced")
        if (
            article is None
            or article.content_type != "podcast_episode"
            or article.source_id != source_id
        ):
            session.rollback()
            raise PodcastEligibilityDenied(
                "Podcast episode source changed"
            )
    eligibility, reasons = _evaluate_processing_eligibility(
        session, process, policy
    )
    if eligibility != "eligible":
        _apply_denial(process, eligibility, reasons, stamp=stamp)
        session.add(process)
        session.commit()
        raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)

    supplied_input_hash = _validate_sha256(input_hash, "input_hash")
    if provider_usage_plan is not None and not (
        provider_usage_plan.window_start_at
        <= current
        < provider_usage_plan.window_end_at
    ):
        session.rollback()
        raise PodcastProviderQuotaExceeded("provider usage window is not active")
    supplied_settings_fingerprint = _validate_sha256(
        settings_fingerprint, "settings_fingerprint"
    )
    previous_success = session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == process.id,
            PodcastStageAttemptRecord.submission_state == "succeeded",
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()
    expected_input_hash = (
        previous_success.output_hash
        if previous_success is not None
        else str(process.input_content_hash or "")
    )
    if supplied_input_hash != expected_input_hash:
        session.rollback()
        raise PodcastProcessingConflict(
            "stage input hash does not match the immutable processing chain"
        )

    from config import PodcastConfig, settings

    config = settings.podcast if policy is None else getattr(policy, "config", None)
    if not isinstance(config, PodcastConfig):
        session.rollback()
        raise PodcastBudgetExceeded(
            "provider boundary requires trusted Podcast budget configuration"
        )
    configured_period = current.astimezone(
        ZoneInfo(config.budget_timezone)
    ).strftime("%Y-%m")
    cumulative_estimate = process.actual_cost_minor + estimate
    if (
        not config.processing_enabled
        or budget_scope != config.budget_scope
        or budget_period != configured_period
        or limit != config.monthly_budget_cny_minor
        or cumulative_estimate > config.per_run_budget_cny_minor
        or cumulative_estimate > process.estimated_cost_minor
        or budget_scope != process.budget_scope
        or budget_period != process.budget_period
        or limit != process.budget_limit_minor
        or cumulative_estimate > int(process.per_run_budget_minor or 0)
    ):
        session.rollback()
        raise PodcastBudgetExceeded(
            "provider boundary budget arguments do not match trusted configuration"
        )

    existing_reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.idempotency_key == reservation_key
        )
    ).first()
    if existing_reservation is not None:
        attempt = session.get(PodcastStageAttemptRecord, existing_reservation.attempt_id)
        if (
            attempt is None
            or attempt.processing_id != claim.processing_id
            or attempt.stage != claim.stage
            or attempt.lease_token != claim.lease_token
            or attempt.fencing_token != claim.fencing_token
            or existing_reservation.reserved_minor != estimate
            or existing_reservation.budget_scope != budget_scope
            or existing_reservation.budget_period != budget_period
            or attempt.provider_request_key != provider_request_key
            or attempt.execution_kind != execution
            or attempt.settings_fingerprint != supplied_settings_fingerprint
            or not _reservation_matches_usage_plan(
                existing_reservation, provider_usage_plan
            )
        ):
            session.rollback()
            raise PodcastProcessingConflict(
                "reservation idempotency key belongs to a different attempt"
            )
        session.expunge(attempt)
        session.commit()
        return attempt

    previous = _latest_active_attempt(session, claim.processing_id)
    if previous is not None:
        session.rollback()
        raise PodcastProviderReconciliationRequired(
            "prior provider submission must be reconciled before retry"
        )

    spent = int(
        session.exec(
            select(func.coalesce(func.sum(PodcastCostLedgerRecord.actual_cost_minor), 0)).where(
                PodcastCostLedgerRecord.budget_scope == budget_scope,
                PodcastCostLedgerRecord.budget_period == budget_period,
            )
        ).one()
    )
    reserved = int(
        session.exec(
            select(
                func.coalesce(func.sum(PodcastBudgetReservationRecord.reserved_minor), 0)
            ).where(
                PodcastBudgetReservationRecord.budget_scope == budget_scope,
                PodcastBudgetReservationRecord.budget_period == budget_period,
                PodcastBudgetReservationRecord.status == "reserved",
            )
        ).one()
    )
    if spent + reserved + estimate > limit:
        session.rollback()
        raise PodcastBudgetExceeded(
            f"budget reservation exceeds configured cap ({spent + reserved + estimate}>{limit})"
        )
    if provider_usage_plan is not None:
        _require_provider_usage_capacity(session, provider_usage_plan)

    attempt_no = process.attempt_count + 1
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(
            _fenced_where(claim, stamp),
            PodcastProcessingRecord.attempt_count == process.attempt_count,
        )
        .values(attempt_count=attempt_no, updated_at=stamp)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease changed before provider submission")
    attempt = PodcastStageAttemptRecord(
        id=uuid.uuid4().hex,
        processing_id=claim.processing_id,
        stage=claim.stage,
        attempt_no=attempt_no,
        fencing_token=claim.fencing_token,
        lease_token=claim.lease_token,
        input_hash=supplied_input_hash,
        settings_fingerprint=supplied_settings_fingerprint,
        output_authority_id=(
            config.authority_id
            if claim.stage == "tts" and execution == "provider"
            else None
        ),
        provider_name=str(provider_name or ""),
        model_name=str(model_name or ""),
        provider_revision=str(provider_revision or ""),
        usage_settlement_mode=settlement_mode,
        provider_request_key=provider_request_key,
        execution_kind=execution,
        submission_state="prepared",
        retry_state="none",
        cost_currency="CNY",
        estimated_cost_minor=estimate,
        provider_deadline_at=(
            _iso(current + dt.timedelta(seconds=provider_usage_plan.deadline_seconds))
            if provider_usage_plan is not None
            else None
        ),
        started_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    reservation = PodcastBudgetReservationRecord(
        id=uuid.uuid4().hex,
        processing_id=claim.processing_id,
        attempt_id=attempt.id,
        budget_scope=budget_scope,
        budget_period=budget_period,
        currency="CNY",
        reserved_minor=estimate,
        status="reserved",
        idempotency_key=reservation_key,
        expires_at=reservation_expires_at,
        provider_quota_scope=(
            provider_usage_plan.quota_scope if provider_usage_plan else None
        ),
        provider_quota_period=(
            provider_usage_plan.quota_period if provider_usage_plan else None
        ),
        provider_quota_unit=(
            provider_usage_plan.unit.value if provider_usage_plan else None
        ),
        provider_quota_window_start_at=(
            _iso(provider_usage_plan.window_start_at) if provider_usage_plan else None
        ),
        provider_quota_window_end_at=(
            _iso(provider_usage_plan.window_end_at) if provider_usage_plan else None
        ),
        provider_quota_limit_units=(
            provider_usage_plan.limit_units if provider_usage_plan else None
        ),
        reserved_usage_units=(
            provider_usage_plan.reserved_units if provider_usage_plan else None
        ),
        unit_price_cny_minor=(
            provider_usage_plan.unit_price_cny_minor if provider_usage_plan else None
        ),
        price_unit_count=(
            provider_usage_plan.price_unit_count if provider_usage_plan else None
        ),
        pricing_revision=(
            provider_usage_plan.pricing_revision if provider_usage_plan else None
        ),
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(attempt)
    try:
        # There are intentionally no ORM relationships on these audit rows;
        # flush the attempt so SQLite can validate the reservation FK.
        session.flush()
        session.add(reservation)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        winner = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.idempotency_key == reservation_key
            )
        ).first()
        if winner is None:
            raise PodcastProcessingConflict("attempt creation raced") from exc
        winner_attempt = session.get(PodcastStageAttemptRecord, winner.attempt_id)
        if winner_attempt is None or winner_attempt.processing_id != claim.processing_id:
            raise PodcastProcessingConflict("reservation key conflict") from exc
        return _detach_after_read(session, winner_attempt)
    return _detach_after_read(session, attempt)


@_transaction_boundary
def authorize_provider_call(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastStageAttemptRecord:
    """Fail closed immediately before network I/O if a frozen window may roll over.

    Aliyun accounting is assigned by provider submission time. Requiring one
    full configured HTTP timeout before the persisted window end prevents a
    reservation made near a daily/campaign boundary from being charged to the
    following window. The caller must fail the prepared attempt (releasing its
    hold) before rebuilding a plan.
    """

    _require_clean_session(session, "provider call authorization")
    _require_stage(policy, claim.stage, boundary="provider_submit")
    if claim.drain_only:
        raise PodcastEligibilityDenied(
            "drain-only processing cannot authorize a provider submission"
        )
    current = _as_utc(now)
    stamp = _iso(current)
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if (
        attempt is None
        or reservation is None
        or process is None
        or attempt.processing_id != claim.processing_id
        or attempt.stage != claim.stage
        or attempt.lease_token != claim.lease_token
        or attempt.fencing_token != claim.fencing_token
        or attempt.execution_kind != "provider"
        or attempt.submission_state != "prepared"
        or attempt.request_unknown
        or reservation.processing_id != claim.processing_id
        or reservation.status != "reserved"
        or process.processing_status != "running"
        or process.stage != claim.stage
        or process.lease_owner != claim.lease_owner
        or process.lease_token != claim.lease_token
        or process.fencing_token != claim.fencing_token
        or not process.lease_expires_at
        or process.lease_expires_at <= stamp
    ):
        raise PodcastLeaseLost("provider call is not owned by this active lease")
    eligibility, reasons = _evaluate_processing_eligibility(session, process, policy)
    if eligibility != "eligible":
        raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)
    if reservation.provider_quota_scope is not None:
        try:
            window_start = _as_utc(
                dt.datetime.fromisoformat(
                    str(reservation.provider_quota_window_start_at or "")
                )
            )
            window_end = _as_utc(
                dt.datetime.fromisoformat(
                    str(reservation.provider_quota_window_end_at or "")
                )
            )
        except ValueError as exc:
            raise PodcastProcessingConflict(
                "persisted provider usage window is invalid"
            ) from exc
        effective_policy = _effective_provider_policy(policy)
        guard_method = getattr(
            effective_policy, "provider_call_guard_seconds", None
        )
        guard_seconds = (
            guard_method(attempt.provider_name, attempt.stage)
            if callable(guard_method)
            else None
        )
        if (
            isinstance(guard_seconds, bool)
            or not isinstance(guard_seconds, int)
            or guard_seconds < 0
        ):
            raise PodcastProviderQuotaExceeded(
                "provider call window guard is not configured"
            )
        safe_until = current + dt.timedelta(seconds=guard_seconds)
        if current < window_start or safe_until >= window_end:
            raise PodcastProviderQuotaExceeded(
                "provider usage window cannot cover the configured request timeout"
            )
    return _detach_after_read(session, attempt)


@_transaction_boundary
def mark_provider_submission(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    provider_task_id: str = "",
    request_unknown: bool = False,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastStageAttemptRecord:
    """Persist the post-call provider identity or ambiguous request outcome."""

    _require_clean_session(session, "provider submission")
    task_id = str(provider_task_id or "").strip()
    stamp = _iso(_as_utc(now))
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    desired_state = "request_unknown" if request_unknown else "submitted"
    if (
        attempt is None
        or attempt.processing_id != claim.processing_id
        or attempt.stage != claim.stage
        or attempt.lease_token != claim.lease_token
        or attempt.fencing_token != claim.fencing_token
    ):
        raise PodcastLeaseLost("stage attempt is not owned by this lease")
    if attempt.execution_kind != "provider":
        raise PodcastProcessingConflict(
            "local execution cannot record a provider submission"
        )
    if not request_unknown and not task_id:
        raise ValueError("provider_task_id is required for a confirmed submission")
    if attempt.submission_state != "prepared":
        if (
            attempt.submission_state == desired_state
            and attempt.request_unknown == bool(request_unknown)
            and attempt.provider_task_id == task_id
        ):
            return _detach_after_read(session, attempt)
        raise PodcastProcessingConflict("provider submission replay differs from persisted outcome")
    _require_stage(policy, claim.stage, boundary="provider_submit")
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(updated_at=stamp)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    eligibility, reasons = _evaluate_processing_eligibility(session, process, policy)
    if eligibility != "eligible":
        attempt.provider_task_id = task_id
        attempt.request_unknown = bool(request_unknown)
        attempt.submission_state = desired_state
        attempt.retry_state = "reconcile_required" if request_unknown else "none"
        attempt.submitted_at = stamp
        attempt.updated_at = stamp
        _apply_denial(process, eligibility, reasons, stamp=stamp)
        session.add(attempt)
        session.add(process)
        session.commit()
        raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)
    attempt.provider_task_id = task_id
    attempt.request_unknown = bool(request_unknown)
    attempt.submission_state = desired_state
    attempt.retry_state = "reconcile_required" if request_unknown else "none"
    attempt.submitted_at = stamp
    attempt.updated_at = stamp
    session.add(attempt)
    session.commit()
    return _detach_after_read(session, attempt)


def _active_provider_attempt(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
) -> tuple[PodcastStageAttemptRecord, PodcastBudgetReservationRecord]:
    """Load the one active provider attempt without binding it to a stale lease.

    A submitted task deliberately survives process-lease rotation.  The current
    process claim fences the transition; the attempt's original fence remains
    immutable audit evidence for the provider submission that must be polled.
    """

    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    latest = _latest_active_attempt(session, claim.processing_id)
    if (
        attempt is None
        or reservation is None
        or attempt.processing_id != claim.processing_id
        or attempt.stage != claim.stage
        or attempt.execution_kind != "provider"
        or reservation.processing_id != claim.processing_id
        or reservation.status == "released"
        or latest is None
        or latest.id != attempt.id
    ):
        raise PodcastProcessingConflict(
            "stage attempt is not the current active provider attempt"
        )
    return attempt, reservation


@_transaction_boundary
def schedule_stage_poll(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    retry_at: dt.datetime,
    poll_performed: bool,
    provider_deadline_at: Optional[dt.datetime] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Persist a poll result or initial deferral and release the process lease.

    The active attempt, provider task identity and budget reservation remain in
    place so a restarted worker can only poll the same paid provider task.
    ``poll_performed=False`` schedules the first poll after submission without
    claiming that an external poll already happened.
    """

    _require_clean_session(session, "provider poll schedule")
    _require_stage(policy, claim.stage, boundary="commit")
    current = _as_utc(now)
    stamp = _iso(current)
    retry = _as_utc(retry_at)
    if retry <= current:
        raise ValueError("retry_at must be later than now")
    if not isinstance(poll_performed, bool):
        raise ValueError("poll_performed must be a boolean")
    attempt, _reservation = _active_provider_attempt(
        session, claim, attempt_id=attempt_id
    )
    if attempt.submission_state not in {"submitted", "request_unknown"}:
        raise PodcastProcessingConflict("provider attempt is not pollable")
    if not str(attempt.provider_task_id or "").strip():
        raise PodcastProviderReconciliationRequired(
            "provider task identity is required before polling"
        )

    supplied_deadline = (
        _iso(_as_utc(provider_deadline_at))
        if provider_deadline_at is not None
        else None
    )
    persisted_deadline = str(attempt.provider_deadline_at or "").strip() or None
    if (
        persisted_deadline is not None
        and supplied_deadline is not None
        and supplied_deadline != persisted_deadline
    ):
        raise PodcastProcessingConflict("provider deadline cannot be overwritten")
    deadline = persisted_deadline or supplied_deadline
    if deadline is None:
        raise ValueError("provider_deadline_at is required for the first poll")
    try:
        deadline_at = _as_utc(dt.datetime.fromisoformat(deadline))
    except ValueError as exc:
        raise PodcastProcessingConflict("persisted provider deadline is invalid") from exc
    if deadline_at <= current:
        raise PodcastProviderReconciliationRequired(
            "provider deadline has elapsed; manual reconciliation is required"
        )
    if retry > deadline_at:
        raise ValueError("retry_at cannot be later than provider_deadline_at")
    if not poll_performed and (
        attempt.poll_count != 0
        or attempt.retry_state == "scheduled"
    ):
        raise PodcastProcessingConflict(
            "initial provider poll deferral cannot follow a completed poll"
        )

    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(
            processing_status="retry_wait",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_retry_at=_iso(retry),
            updated_at=stamp,
        )
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    if poll_performed:
        attempt.poll_count += 1
        attempt.last_polled_at = stamp
    attempt.provider_deadline_at = deadline
    if attempt.submission_state == "submitted":
        attempt.retry_state = "scheduled"
    attempt.updated_at = stamp
    session.add(attempt)
    session.commit()
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing disappeared after poll schedule")
    return _detach_after_read(session, process)


@_transaction_boundary
def park_for_reconciliation(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    reason_code: str,
    redacted_error_message: str,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Park an unsettled provider task for explicit operator reconciliation."""

    _require_clean_session(session, "provider reconciliation park")
    _require_stage(policy, claim.stage, boundary="commit")
    code = _require_nonempty(reason_code, "reason_code")[:120]
    message = str(redacted_error_message or "")[:500]
    stamp = _iso(_as_utc(now))
    attempt, _reservation = _active_provider_attempt(
        session, claim, attempt_id=attempt_id
    )
    if attempt.submission_state not in {
        "submitted",
        "request_unknown",
        "reconciling",
    }:
        raise PodcastProcessingConflict(
            "provider attempt does not require reconciliation"
        )

    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(
            processing_status="reconciliation_required",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_retry_at=None,
            error_code=code,
            error_message=message,
            updated_at=stamp,
        )
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    attempt.submission_state = "reconciling"
    attempt.request_unknown = True
    attempt.retry_state = "reconcile_required"
    attempt.error_code = code
    attempt.error_message = message
    attempt.updated_at = stamp
    session.add(attempt)
    session.commit()
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing disappeared after reconciliation park")
    return _detach_after_read(session, process)


@_transaction_boundary
def reconcile_parked_provider_request(
    session: Session,
    *,
    processing_id: str,
    attempt_id: str,
    expected_attempt_count: int,
    expected_fencing_token: int,
    expected_provider_request_key: str,
    outcome: str,
    retry_at: dt.datetime,
    idempotency_key: str,
    actor: str,
    reason: str,
    provider_task_id: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Resolve a parked provider request through an audited operator CAS.

    Confirming submission preserves the task and hold and schedules another
    poll. Only an explicit ``not_submitted`` confirmation releases the hold and
    permits a later attempt. Every accepted or rejected command is immutable
    and idempotent through ``PodcastProcessingCommandRecord``.
    """

    _require_clean_session(session, "parked provider reconciliation")
    processing_id = _require_nonempty(processing_id, "processing_id")
    attempt_id = _require_nonempty(attempt_id, "attempt_id")
    request_key = _require_nonempty(
        expected_provider_request_key, "expected_provider_request_key"
    )
    command_key = _require_nonempty(idempotency_key, "idempotency_key")
    operator = _require_nonempty(actor, "actor")
    operator_reason = _require_nonempty(reason, "reason")
    if (
        isinstance(expected_attempt_count, bool)
        or not isinstance(expected_attempt_count, int)
        or expected_attempt_count < 0
    ):
        raise ValueError("expected_attempt_count must be a nonnegative integer")
    if (
        isinstance(expected_fencing_token, bool)
        or not isinstance(expected_fencing_token, int)
        or expected_fencing_token < 1
    ):
        raise ValueError("expected_fencing_token must be a positive integer")
    normalized_outcome = str(outcome or "").strip().lower()
    if normalized_outcome not in {"submitted", "not_submitted"}:
        raise ValueError("outcome must be submitted or not_submitted")
    supplied_task_id = str(provider_task_id or "").strip()
    current = _as_utc(now)
    stamp = _iso(current)
    retry = _as_utc(retry_at)
    audited_reason = json.dumps(
        {
            "attempt_id": attempt_id,
            "expected_fencing_token": expected_fencing_token,
            "expected_provider_request_key": request_key,
            "operator_reason": operator_reason,
            "provider_task_id": supplied_task_id,
            "requested_outcome": normalized_outcome,
            "retry_at": _iso(retry),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    statement = select(PodcastProcessingRecord).where(
        PodcastProcessingRecord.id == processing_id
    )
    if connection.dialect.name == "postgresql":
        statement = statement.with_for_update()
    process = session.exec(statement).first()
    if process is None:
        raise PodcastProcessingConflict("processing does not exist")

    existing = session.exec(
        select(PodcastProcessingCommandRecord).where(
            PodcastProcessingCommandRecord.processing_id == processing_id,
            PodcastProcessingCommandRecord.command_type == "provider_reconcile",
            PodcastProcessingCommandRecord.idempotency_key == command_key,
        )
    ).first()
    if existing is not None:
        if (
            existing.expected_attempt_count != expected_attempt_count
            or existing.requested_by != operator
            or existing.reason != audited_reason
        ):
            raise PodcastProcessingConflict(
                "provider reconciliation idempotency key conflicts with audit truth"
            )
        if existing.outcome == "rejected":
            raise PodcastProcessingConflict(existing.error_message)
        session.commit()
        current_process = session.get(PodcastProcessingRecord, processing_id)
        if current_process is None:
            raise PodcastProcessingConflict(
                "processing disappeared after reconciliation replay"
            )
        return _detach_after_read(session, current_process)
    if retry <= current:
        raise ValueError("retry_at must be later than now")

    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    latest = _latest_active_attempt(session, processing_id)
    code = ""
    message = ""
    if process.attempt_count != expected_attempt_count:
        code = "podcast_processing_conflict"
        message = "processing attempt count changed before reconciliation"
    elif process.fencing_token != expected_fencing_token:
        code = "podcast_processing_conflict"
        message = "processing fence changed before reconciliation"
    elif process.processing_status != "reconciliation_required":
        code = "podcast_processing_conflict"
        message = "processing is not parked for reconciliation"
    elif (
        attempt is None
        or reservation is None
        or attempt.processing_id != processing_id
        or attempt.execution_kind != "provider"
        or attempt.submission_state != "reconciling"
        or not attempt.request_unknown
        or attempt.retry_state != "reconcile_required"
        or latest is None
        or latest.id != attempt.id
    ):
        code = "podcast_processing_conflict"
        message = "parked provider attempt no longer matches persisted truth"
    elif attempt.provider_request_key != request_key:
        code = "podcast_processing_conflict"
        message = "provider request key changed before reconciliation"
    elif reservation.status == "released":
        code = "podcast_processing_conflict"
        message = "provider budget hold was already released"
    elif normalized_outcome == "submitted":
        resolved_task_id = supplied_task_id or attempt.provider_task_id
        if not resolved_task_id:
            code = "podcast_processing_conflict"
            message = "provider_task_id is required to confirm submission"
        elif attempt.provider_task_id and attempt.provider_task_id != resolved_task_id:
            code = "podcast_processing_conflict"
            message = "provider_task_id cannot be overwritten"
    else:
        resolved_task_id = ""
        if supplied_task_id or attempt.provider_task_id:
            code = "podcast_processing_conflict"
            message = "a provider task identity cannot be confirmed not submitted"
        elif reservation.status != "reserved":
            code = "podcast_processing_conflict"
            message = "settled provider cost cannot be confirmed not submitted"

    command = PodcastProcessingCommandRecord(
        id=uuid.uuid4().hex,
        processing_id=processing_id,
        command_type="provider_reconcile",
        idempotency_key=command_key,
        expected_attempt_count=expected_attempt_count,
        requested_by=operator,
        reason=audited_reason,
        outcome="rejected" if code else "accepted",
        error_code=code,
        error_message=message,
        created_at=stamp,
    )
    session.add(command)
    if code:
        session.commit()
        raise PodcastProcessingConflict(message)

    assert attempt is not None
    if normalized_outcome == "submitted":
        attempt.provider_task_id = resolved_task_id
        attempt.submission_state = "submitted"
        attempt.request_unknown = False
        attempt.retry_state = "scheduled"
        attempt.completed_at = None
    else:
        denied = process.eligibility_status != "eligible"
        attempt.submission_state = (
            "failed_terminal" if denied else "failed_retryable"
        )
        attempt.request_unknown = False
        attempt.retry_state = "exhausted" if denied else "scheduled"
        attempt.completed_at = stamp
        _release_reservation(session, attempt.id, stamp=stamp)
    attempt.error_code = ""
    attempt.error_message = ""
    attempt.updated_at = stamp
    denied_not_submitted = (
        normalized_outcome == "not_submitted"
        and process.eligibility_status != "eligible"
    )
    process.processing_status = (
        "not_required" if denied_not_submitted else "retry_wait"
    )
    process.lease_owner = None
    process.lease_token = None
    process.lease_expires_at = None
    process.next_retry_at = None if denied_not_submitted else _iso(retry)
    process.error_code = (
        process.eligibility_status if denied_not_submitted else ""
    )
    if not denied_not_submitted:
        process.error_message = ""
    process.finished_at = stamp if denied_not_submitted else None
    process.updated_at = stamp
    session.add(attempt)
    session.add(process)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise PodcastProcessingConflict(
            "provider reconciliation command raced with another operator"
        ) from exc
    return _detach_after_read(session, process)


@_transaction_boundary
def reconcile_provider_request(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    outcome: str,
    provider_task_id: Optional[str] = None,
    retry_at: Optional[dt.datetime] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastStageAttemptRecord:
    """Resolve an ambiguous submission without ever issuing a second call."""

    _require_stage(policy, claim.stage, boundary="provider_submit")
    _require_clean_session(session, "provider reconciliation")
    normalized = str(outcome or "").strip().lower()
    if normalized not in {"submitted", "not_submitted"}:
        raise ValueError("outcome must be submitted or not_submitted")
    stamp = _iso(_as_utc(now))
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    if (
        attempt is None
        or attempt.processing_id != claim.processing_id
        or attempt.execution_kind != "provider"
        or not attempt.request_unknown
        or attempt.submission_state not in {"request_unknown", "reconciling"}
        or not attempt.provider_request_key
    ):
        raise PodcastProcessingConflict("attempt does not require provider reconciliation")
    reconciled_task_id = (
        str(provider_task_id).strip() if provider_task_id is not None else attempt.provider_task_id
    )
    if attempt.provider_task_id and reconciled_task_id != attempt.provider_task_id:
        session.rollback()
        raise PodcastProcessingConflict("provider_task_id cannot be overwritten")
    if normalized == "submitted" and not reconciled_task_id:
        raise ValueError(
            "provider_task_id is required when reconciliation confirms submission"
        )
    attempt.provider_task_id = reconciled_task_id
    values: dict[str, Any] = {"updated_at": stamp}
    if normalized == "submitted":
        attempt.request_unknown = False
        attempt.submission_state = "submitted"
        attempt.retry_state = "none"
    else:
        if retry_at is None:
            raise ValueError("retry_at is required when provider confirms no submission")
        attempt.request_unknown = False
        attempt.submission_state = "failed_retryable"
        attempt.retry_state = "scheduled"
        attempt.completed_at = stamp
        _release_reservation(session, attempt.id, stamp=stamp)
        eligibility_blocked = (
            process is not None and process.eligibility_status != "eligible"
        )
        values.update(
            processing_status="not_required" if eligibility_blocked else "retry_wait",
            next_retry_at=None if eligibility_blocked else _iso(_as_utc(retry_at)),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_cost_recovery_where(claim, stamp))
        .values(**values)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    attempt.updated_at = stamp
    session.add(attempt)
    session.commit()
    return _detach_after_read(session, attempt)


def _existing_ledger(
    session: Session, *, attempt_id: str, settlement_key: str
) -> Optional[PodcastCostLedgerRecord]:
    return session.exec(
        select(PodcastCostLedgerRecord).where(
            or_(
                PodcastCostLedgerRecord.attempt_id == attempt_id,
                PodcastCostLedgerRecord.settlement_key == settlement_key,
            )
        )
    ).first()


def _ledger_matches_settlement(
    ledger: PodcastCostLedgerRecord,
    reservation: PodcastBudgetReservationRecord,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    settlement_key: str,
    actual_cost_minor: int,
    usage_json: str,
    actual_usage_units: int,
) -> bool:
    """Match every duplicated ownership/accounting field for exact replay."""

    return (
        ledger.attempt_id == attempt_id
        and ledger.processing_id == claim.processing_id
        and ledger.reservation_id == reservation.id
        and ledger.stage == claim.stage
        and ledger.settlement_key == settlement_key
        and ledger.budget_scope == reservation.budget_scope
        and ledger.budget_period == reservation.budget_period
        and ledger.currency == reservation.currency
        and ledger.actual_cost_minor == actual_cost_minor
        and ledger.budget_breached == reservation.budget_breached
        and ledger.usage_json == usage_json
        and ledger.provider_quota_scope == reservation.provider_quota_scope
        and ledger.provider_quota_period == reservation.provider_quota_period
        and ledger.provider_quota_unit == reservation.provider_quota_unit
        and ledger.actual_usage_units == actual_usage_units
        and ledger.provider_quota_breached == reservation.provider_quota_breached
    )


def _normalized_usage_dict(
    usage: Optional[dict[str, Any] | NormalizedUsage],
) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, NormalizedUsage):
        return {
            "cost_minor": usage.cost_minor,
            "currency": usage.currency,
            "audio_duration_ms": usage.audio_duration_ms,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "audio_tokens": usage.audio_tokens,
            "tts_characters": usage.tts_characters,
            "input_bytes": usage.input_bytes,
            "output_bytes": usage.output_bytes,
        }
    if not isinstance(usage, dict):
        raise ValueError("usage must be a NormalizedUsage or dictionary")
    return dict(usage)


def _normalized_usage_for_reservation(
    reservation: PodcastBudgetReservationRecord,
    usage: Optional[dict[str, Any] | NormalizedUsage],
    *,
    actual_cost_minor: int,
) -> dict[str, Any]:
    if reservation.provider_quota_unit is not None:
        if not isinstance(usage, NormalizedUsage):
            raise ValueError(
                "provider-quota settlement requires trusted NormalizedUsage"
            )
        if usage.currency != "CNY" or usage.cost_minor != actual_cost_minor:
            raise PodcastProcessingConflict(
                "provider usage cost must match the CNY settlement"
            )
    return _normalized_usage_dict(usage)


def _actual_provider_usage_units(
    reservation: PodcastBudgetReservationRecord,
    usage: dict[str, Any],
) -> int:
    unit = reservation.provider_quota_unit
    if unit is None:
        return 0
    if unit == ProviderUsageUnit.AUDIO_SECONDS.value:
        if "audio_duration_ms" in usage:
            milliseconds = usage["audio_duration_ms"]
            if (
                isinstance(milliseconds, bool)
                or not isinstance(milliseconds, int)
                or milliseconds < 0
            ):
                raise ValueError("audio_duration_ms must be a nonnegative integer")
            return (milliseconds + 999) // 1000
        seconds = usage.get("audio_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise ValueError("ASR settlement requires nonnegative audio usage")
        return seconds
    if unit == ProviderUsageUnit.TTS_CHARACTERS.value:
        characters = usage.get("tts_characters")
        if (
            isinstance(characters, bool)
            or not isinstance(characters, int)
            or characters < 0
        ):
            raise ValueError("TTS settlement requires nonnegative character usage")
        return characters
    raise PodcastProcessingConflict("persisted provider usage unit is invalid")


def _priced_cost_minor(
    reservation: PodcastBudgetReservationRecord, actual_units: int
) -> int:
    if reservation.provider_quota_unit is None:
        return reservation.actual_cost_minor
    numerator = actual_units * int(reservation.unit_price_cny_minor or 0)
    denominator = int(reservation.price_unit_count or 0)
    if denominator <= 0:
        raise PodcastProcessingConflict("persisted provider price is invalid")
    return (numerator + denominator - 1) // denominator


@_transaction_boundary
def settle_attempt_cost(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    settlement_key: str,
    actual_cost_minor: int,
    usage: Optional[dict[str, Any] | NormalizedUsage] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastCostLedgerRecord:
    """Idempotently turn one reservation into one immutable actual-cost debit."""

    _require_clean_session(session, "cost settlement")
    actual = _require_minor_units(actual_cost_minor, "actual_cost_minor")
    settlement_key = _require_nonempty(settlement_key, "settlement_key")
    reservation_snapshot = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    if reservation_snapshot is None:
        session.rollback()
        raise PodcastProcessingConflict("attempt reservation is incomplete")
    provider_quota_key = None
    if reservation_snapshot.provider_quota_scope is not None:
        provider_quota_key = (
            reservation_snapshot.provider_quota_scope,
            str(reservation_snapshot.provider_quota_period or ""),
            str(reservation_snapshot.provider_quota_unit or ""),
        )
    budget_scope = reservation_snapshot.budget_scope
    budget_period = reservation_snapshot.budget_period
    # The snapshot only identifies the advisory-lock domains.  Roll back its
    # read transaction, acquire the same CNY/provider locks as begin, then read
    # every mutable row again inside the serialized settlement transaction.
    session.rollback()
    _begin_budget_transaction(
        session,
        budget_scope=budget_scope,
        budget_period=budget_period,
        provider_quota_key=provider_quota_key,
    )
    existing = _existing_ledger(
        session, attempt_id=attempt_id, settlement_key=settlement_key
    )
    if existing is not None:
        existing_reservation = session.get(
            PodcastBudgetReservationRecord, existing.reservation_id
        )
        if existing_reservation is None:
            raise PodcastProcessingConflict("cost settlement reservation is missing")
        normalized_usage = _normalized_usage_for_reservation(
            existing_reservation,
            usage,
            actual_cost_minor=actual,
        )
        replay_usage_json = json.dumps(
            normalized_usage,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        replay_usage_units = _actual_provider_usage_units(
            existing_reservation, normalized_usage
        )
        if not _ledger_matches_settlement(
            existing,
            existing_reservation,
            claim,
            attempt_id=attempt_id,
            settlement_key=settlement_key,
            actual_cost_minor=actual,
            usage_json=replay_usage_json,
            actual_usage_units=replay_usage_units,
        ):
            raise PodcastProcessingConflict("cost settlement key or attempt already settled differently")
        return _detach_after_read(session, existing)

    _require_stage(policy, claim.stage, boundary="commit")
    stamp = _iso(_as_utc(now))
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    if attempt is None or reservation is None or process is None:
        raise PodcastProcessingConflict("attempt reservation is incomplete")
    if attempt.processing_id != claim.processing_id or attempt.stage != claim.stage:
        raise PodcastProcessingConflict("attempt does not belong to this processing stage")
    if attempt.request_unknown:
        raise PodcastProviderReconciliationRequired(
            "ambiguous provider request must be reconciled before cost settlement"
        )
    if attempt.execution_kind == "provider":
        if attempt.submission_state != "submitted":
            raise PodcastProcessingConflict(
                "provider attempt must be submitted before cost settlement"
            )
    elif attempt.execution_kind == "local":
        if attempt.submission_state != "prepared":
            raise PodcastProcessingConflict(
                "local attempt must remain prepared before cost settlement"
            )
        if actual != 0:
            raise PodcastProcessingConflict("local attempt actual cost must be zero")
    else:
        raise PodcastProcessingConflict("stage attempt execution kind is invalid")
    if reservation.status != "reserved":
        raise PodcastProcessingConflict("budget reservation is not available for settlement")
    normalized_usage = _normalized_usage_for_reservation(
        reservation,
        usage,
        actual_cost_minor=actual,
    )
    actual_usage_units = _actual_provider_usage_units(reservation, normalized_usage)
    if (
        reservation.reserved_usage_units is not None
        and actual_usage_units < reservation.reserved_usage_units
    ):
        raise PodcastProviderReconciliationRequired(
            "provider usage cannot be lower than the immutable submitted input"
        )
    if reservation.provider_quota_unit is not None:
        priced_cost = _priced_cost_minor(reservation, actual_usage_units)
        if actual != priced_cost:
            raise PodcastProcessingConflict(
                "actual cost does not match the persisted integer pricing snapshot"
            )
    usage_json = json.dumps(
        normalized_usage, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    budget_breached = actual > reservation.reserved_minor
    provider_quota_breached = bool(
        reservation.reserved_usage_units is not None
        and actual_usage_units > reservation.reserved_usage_units
    )
    ledger = PodcastCostLedgerRecord(
        id=uuid.uuid4().hex,
        processing_id=claim.processing_id,
        attempt_id=attempt.id,
        reservation_id=reservation.id,
        stage=claim.stage,
        budget_scope=reservation.budget_scope,
        budget_period=reservation.budget_period,
        currency="CNY",
        actual_cost_minor=actual,
        budget_breached=budget_breached,
        usage_json=usage_json,
        provider_name=attempt.provider_name,
        model_name=attempt.model_name,
        provider_revision=attempt.provider_revision,
        provider_task_id=attempt.provider_task_id,
        settlement_key=settlement_key,
        created_at=stamp,
        provider_quota_scope=reservation.provider_quota_scope,
        provider_quota_period=reservation.provider_quota_period,
        provider_quota_unit=reservation.provider_quota_unit,
        actual_usage_units=actual_usage_units,
        provider_quota_breached=provider_quota_breached,
    )
    reserved_update = session.exec(
        update(PodcastBudgetReservationRecord)
        .where(
            PodcastBudgetReservationRecord.id == reservation.id,
            PodcastBudgetReservationRecord.status == "reserved",
        )
        .values(
            status="settled",
            actual_cost_minor=actual,
            budget_breached=budget_breached,
            actual_usage_units=actual_usage_units,
            provider_quota_breached=provider_quota_breached,
            settled_at=stamp,
            updated_at=stamp,
        )
    )
    if reserved_update.rowcount != 1:
        session.rollback()
        raise PodcastProcessingConflict("budget reservation transition raced")
    attempt.actual_cost_minor = actual
    attempt.usage_json = usage_json
    attempt.updated_at = stamp
    stage_cost = json.loads(process.stage_cost_json or "{}")
    stage_cost[claim.stage] = int(stage_cost.get(claim.stage, 0)) + actual
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_cost_recovery_where(claim, stamp))
        .values(
            actual_cost_minor=PodcastProcessingRecord.actual_cost_minor + actual,
            budget_breached=(
                True if budget_breached else PodcastProcessingRecord.budget_breached
            ),
            stage_cost_json=json.dumps(
                stage_cost, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            updated_at=stamp,
        )
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    session.add(attempt)
    session.add(ledger)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        winner = _existing_ledger(
            session, attempt_id=attempt_id, settlement_key=settlement_key
        )
        caller_reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == attempt_id
            )
        ).first()
        if (
            winner is None
            or caller_reservation is None
            or not _ledger_matches_settlement(
                winner,
                caller_reservation,
                claim,
                attempt_id=attempt_id,
                settlement_key=settlement_key,
                actual_cost_minor=actual,
                usage_json=usage_json,
                actual_usage_units=actual_usage_units,
            )
        ):
            raise PodcastProcessingConflict("cost settlement raced with a different debit") from exc
        return _detach_after_read(session, winner)
    return _detach_after_read(session, ledger)


@_transaction_boundary
def commit_stage_attempt(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    output_hash: str,
    next_stage: Optional[str] = None,
    awaiting_review: bool = False,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Fence the stage result and release the lease for the next DB claim."""

    _require_stage(policy, claim.stage, boundary="commit")
    normalized_output_hash = _validate_sha256(output_hash, "output_hash")
    _require_clean_session(session, "stage commit")
    if next_stage is not None:
        next_stage = _require_nonempty(next_stage, "next_stage").lower()
        if next_stage not in PODCAST_PROCESSING_STAGES:
            raise ValueError(f"unknown Podcast stage: {next_stage}")
    if next_stage is not None and awaiting_review:
        raise ValueError("next_stage and awaiting_review are mutually exclusive")
    stamp = _iso(_as_utc(now))
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing does not exist")
    eligibility, reasons = _evaluate_processing_eligibility(
        session, process, policy
    )
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    latest = _latest_active_attempt(session, claim.processing_id)
    if (
        attempt is None
        or reservation is None
        or attempt.processing_id != claim.processing_id
        or attempt.stage != claim.stage
        or reservation.processing_id != claim.processing_id
        or latest is None
        or latest.id != attempt.id
    ):
        raise PodcastProcessingConflict("stage attempt is not committable")
    bound_asr_artifact = None
    if claim.stage == "asr" and attempt.output_artifact_id:
        candidate = session.get(
            PodcastTextArtifactRecord, attempt.output_artifact_id
        )
        if (
            candidate is not None
            and attempt.output_artifact_kind == "normalized_transcript"
            and candidate.kind == "normalized_transcript"
            and candidate.processing_id == process.id
            and candidate.producing_attempt_id == attempt.id
            and candidate.content_hash == attempt.output_hash
        ):
            bound_asr_artifact = candidate
    bound_tts_artifact = None
    if claim.stage == "tts" and attempt.output_artifact_id:
        candidate = session.get(PodcastArtifactRecord, attempt.output_artifact_id)
        expected_authority = str(
            getattr(getattr(policy, "config", None), "authority_id", "") or ""
        ).strip()
        if (
            candidate is not None
            and attempt.output_artifact_kind == "digest_audio_zh"
            and candidate.kind == "digest_audio_zh"
            and candidate.processing_id == process.id
            and candidate.producing_attempt_id == attempt.id
            and candidate.episode_id == process.episode_id
            and candidate.content_hash == attempt.output_hash
            and candidate.narration_artifact_id == process.narration_artifact_id
            and candidate.narration_content_hash == process.narration_content_hash
            and attempt.input_hash == process.narration_content_hash
            and expected_authority
            and attempt.output_authority_id == expected_authority
            and candidate.authority_id == expected_authority
        ):
            bound_tts_artifact = candidate
    if attempt.request_unknown or attempt.submission_state in {
        "request_unknown",
        "reconciling",
    }:
        if eligibility != "eligible":
            _apply_denial(process, eligibility, reasons, stamp=stamp)
            session.add(process)
            session.commit()
            raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)
        raise PodcastProviderReconciliationRequired(
            "ambiguous provider request must be reconciled before commit"
        )
    if attempt.execution_kind == "provider" and attempt.submission_state != "submitted":
        raise PodcastProcessingConflict(
            "provider attempt must be submitted before stage commit"
        )
    if attempt.execution_kind == "local" and attempt.submission_state != "prepared":
        raise PodcastProcessingConflict(
            "local attempt must remain prepared before stage commit"
        )
    if attempt.execution_kind not in {"provider", "local"}:
        raise PodcastProcessingConflict("stage attempt is not committable")
    if eligibility != "eligible":
        # Local work has no remote side effect, so its hold is safe to release.
        # A submitted provider hold remains until its actual charge is settled.
        if attempt.execution_kind == "local":
            attempt.submission_state = "failed_terminal"
            attempt.retry_state = "exhausted"
            attempt.completed_at = stamp
            _release_reservation(session, attempt.id, stamp=stamp)
        elif (
            reservation.status == "settled"
            and bound_asr_artifact is None
            and bound_tts_artifact is None
        ):
            attempt.submission_state = "failed_terminal"
            attempt.retry_state = "exhausted"
            attempt.completed_at = stamp
        attempt.error_code = eligibility
        attempt.error_message = reasons[0][:500] if reasons else eligibility
        attempt.updated_at = stamp
        _apply_denial(process, eligibility, reasons, stamp=stamp)
        session.add(attempt)
        session.add(process)
        session.commit()
        raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)
    allowed_destinations = STAGE_GRAPH.get(process.requested_target, {}).get(claim.stage)
    if allowed_destinations is None or next_stage not in allowed_destinations:
        raise PodcastProcessingConflict(
            f"invalid stage transition for {process.requested_target}: "
            f"{claim.stage}->{next_stage or 'terminal'}"
        )
    if next_stage is not None:
        _require_stage(policy, next_stage, boundary="enqueue")
    if reservation.status != "settled":
        raise PodcastProcessingConflict("actual cost must be settled before stage commit")
    if attempt.output_hash and attempt.output_hash != normalized_output_hash:
        raise PodcastProcessingConflict(
            "stage output hash conflicts with the materialized attempt output"
        )
    if claim.stage == "asr":
        if (
            bound_asr_artifact is None
            or bound_asr_artifact.content_hash != normalized_output_hash
        ):
            raise PodcastProcessingConflict(
                "ASR success requires its bound normalized transcript artifact"
            )
    if claim.stage == "tts":
        if (
            bound_tts_artifact is None
            or bound_tts_artifact.content_hash != normalized_output_hash
        ):
            raise PodcastProcessingConflict(
                "TTS success requires its bound digest audio artifact"
            )
    attempt.submission_state = "succeeded"
    attempt.retry_state = "none"
    attempt.output_hash = normalized_output_hash
    attempt.error_code = ""
    attempt.error_message = ""
    attempt.completed_at = stamp
    attempt.updated_at = stamp
    target_status = "queued" if next_stage else ("awaiting_review" if awaiting_review else "ready")
    values: dict[str, Any] = {
        "processing_status": target_status,
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
        "next_retry_at": None,
        "error_code": "",
        "error_message": "",
        "updated_at": stamp,
    }
    if next_stage:
        values.update(stage=next_stage, queued_at=stamp)
    else:
        values["finished_at"] = stamp
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(**values)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    session.add(attempt)
    session.commit()
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing disappeared after commit")
    return _detach_after_read(session, process)


@_transaction_boundary
def fail_stage_attempt(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    error_code: str,
    redacted_error_message: str,
    retryable: bool,
    retry_at: Optional[dt.datetime] = None,
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastProcessingRecord:
    """Persist a fenced failure and release only an unsettled reservation."""

    _require_stage(policy, claim.stage, boundary="commit")
    _require_clean_session(session, "stage failure")
    stamp = _iso(_as_utc(now))
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing does not exist")
    eligibility, eligibility_reasons = _evaluate_processing_eligibility(
        session, process, policy
    )
    terminal_not_required = eligibility != "eligible" or claim.drain_only
    if terminal_not_required:
        retryable = False
    if retryable and retry_at is None:
        raise ValueError("retry_at is required for a retryable failure")
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    latest = _latest_active_attempt(session, claim.processing_id)
    if (
        attempt is None
        or reservation is None
        or attempt.processing_id != claim.processing_id
        or reservation.processing_id != claim.processing_id
        or attempt.stage != claim.stage
        or latest is None
        or latest.id != attempt.id
        or attempt.submission_state
        not in {"prepared", "submitted", "request_unknown", "reconciling"}
        or (
            attempt.submission_state == "prepared"
            and (
                attempt.lease_token != claim.lease_token
                or attempt.fencing_token != claim.fencing_token
            )
        )
    ):
        raise PodcastProcessingConflict("stage attempt is not the current active attempt")
    if attempt.request_unknown or attempt.submission_state in {"request_unknown", "reconciling"}:
        if eligibility != "eligible":
            _apply_denial(process, eligibility, eligibility_reasons, stamp=stamp)
            session.add(process)
            session.commit()
            raise PodcastEligibilityDenied(
                eligibility_reasons[0] if eligibility_reasons else eligibility
            )
        raise PodcastProviderReconciliationRequired(
            "ambiguous provider request must be reconciled before retry"
        )
    if attempt.execution_kind == "local" and attempt.submission_state != "prepared":
        raise PodcastProcessingConflict(
            "local attempt must remain prepared before failure"
        )
    if attempt.submission_state == "submitted" and reservation.status != "settled":
        if eligibility != "eligible":
            _apply_denial(process, eligibility, eligibility_reasons, stamp=stamp)
            session.add(process)
            session.commit()
            raise PodcastEligibilityDenied(
                eligibility_reasons[0] if eligibility_reasons else eligibility
            )
        raise PodcastProcessingConflict(
            "actual cost must be settled before failing a submitted attempt"
        )
    attempt.submission_state = "failed_retryable" if retryable else "failed_terminal"
    attempt.retry_state = "scheduled" if retryable else "exhausted"
    attempt.error_code = (
        eligibility
        if eligibility != "eligible"
        else (
            str(process.error_code or "podcast_processing_not_required")[:120]
            if claim.drain_only
            else str(error_code or "")[:120]
        )
    )
    attempt.error_message = (
        eligibility_reasons[0][:500]
        if eligibility != "eligible" and eligibility_reasons
        else (
            str(process.error_message or redacted_error_message or "")[:500]
            if claim.drain_only
            else str(redacted_error_message or "")[:500]
        )
    )
    attempt.completed_at = stamp
    attempt.updated_at = stamp
    _release_reservation(session, attempt.id, stamp=stamp)
    values: dict[str, Any] = {
        "processing_status": (
            "not_required"
            if terminal_not_required
            else ("retry_wait" if retryable else "failed")
        ),
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
        "next_retry_at": _iso(_as_utc(retry_at)) if retryable and retry_at else None,
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "updated_at": stamp,
    }
    if eligibility != "eligible":
        values.update(
            eligibility_status=eligibility,
            eligibility_reasons_json=json.dumps(
                eligibility_reasons,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    if not retryable:
        values["finished_at"] = stamp
    guarded = session.exec(
        update(PodcastProcessingRecord)
        .where(_fenced_where(claim, stamp))
        .values(**values)
    )
    if guarded.rowcount != 1:
        session.rollback()
        raise PodcastLeaseLost("processing lease is expired or fenced")
    session.add(attempt)
    session.commit()
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None:
        raise PodcastProcessingConflict("processing disappeared after failure")
    return _detach_after_read(session, process)
