"""Provider-neutral administrative entry points for Podcast processing.

These functions only validate, estimate and enqueue durable work. They never
perform ASR/TTS calls and deliberately know no provider endpoint or secret.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastBudgetReservationRecord,
    PodcastCostLedgerRecord,
    PodcastProcessingCommandRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services import podcast_premium
from services.podcast_processing import (
    ACTIVE_ATTEMPT_STATES,
    PodcastProcessingConflict,
    _evaluate_full_analysis_authority,
    _evaluate_processing_eligibility,
    enqueue_processing,
)
from services.podcast_processing_inputs import (
    processing_input_fingerprint,
    source_media_duration_ms,
)
from services.podcast_source_media import SourceMediaError, enclosure_snapshot
from services.podcast_stage_policy import PodcastStageDenied, PodcastStagePolicy
from services.podcast_publisher_transcripts import (
    publisher_artifact_matches_current_locator,
)


ERROR_MESSAGES: Mapping[str, str] = {
    "podcast_not_found": "Podcast 单集或处理任务不存在",
    "podcast_selection_required": "当前没有已持久化候选结论，需要显式人工选择",
    "podcast_artifact_not_ready": "没有可用于处理的本地就绪音频或当前已发布文本",
    "podcast_provider_unavailable": "Podcast 处理能力尚未完成逻辑配置",
    "podcast_source_media_too_long": "Podcast 单集音频超过 ASR 单任务时长上限",
    "podcast_budget_exceeded": "Podcast CNY 预算不足",
    "podcast_processing_conflict": "Podcast 处理命令与当前状态冲突",
    "podcast_input_changed": "Podcast 处理输入已变化，请重新发起请求",
    "podcast_provider_reconciliation_required": "必须先核对上一次供应方请求状态",
    "podcast_stage_denied": "当前部署不拥有所需 Podcast 执行阶段",
}


class PodcastAdminError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int,
        processing_id: str | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.processing_id = processing_id
        self.message = message or ERROR_MESSAGES[code]
        super().__init__(self.message)


Estimator = Callable[[Mapping[str, Any]], int]
WorkerBackedEstimator = Callable[
    [Session, Mapping[str, Any], PodcastConfig], "AdmissionEstimate"
]
StageWorkerReadiness = Callable[[object], bool]


class StageExecutor(Protocol):
    """Minimum provider-neutral contract for one processing stage."""

    def __call__(self, context: Mapping[str, Any], /) -> Any:
        """Execute the stage from a redacted, immutable processing context."""

        ...


class StageWorker(Protocol):
    """Durable worker entry point registered by a concrete provider bundle.

    The scheduler passes one policy containing the effective provider config
    snapshot used for trusted accounting.  Provider bundles must construct
    their adapter and usage planner from that same snapshot; they must not
    independently re-resolve mutable runtime KV during the call.
    """

    def __call__(
        self, session: Session, /, *, config: object, policy: object
    ) -> Any:
        """Claim and advance a bounded unit of durable stage work."""

        ...


TARGET_STAGES: Mapping[str, frozenset[str]] = {
    "transcript": frozenset({"asr"}),
    "full_analysis": frozenset({"asr", "analyze"}),
    "digest_blog": frozenset({"asr", "translate", "analyze", "digest", "script"}),
    "digest_audio": frozenset({"tts", "audio_qa", "local_publish"}),
}


@dataclass(frozen=True)
class AdmissionEstimate:
    cost_minor: int
    admission_fingerprint: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.cost_minor, bool)
            or not isinstance(self.cost_minor, int)
            or self.cost_minor < 0
        ):
            raise ValueError("cost_minor must be a nonnegative integer")
        fingerprint = str(self.admission_fingerprint or "")
        if fingerprint and (
            len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError(
                "admission_fingerprint must be lowercase SHA-256 hex"
            )


class PodcastProcessingProviderRegistry:
    """Readiness registry populated only by a concrete provider integration."""

    def __init__(self) -> None:
        self._targets: dict[str, tuple[dict[str, StageExecutor], Estimator]] = {}
        self._stage_workers: dict[
            str, tuple[StageWorker, StageWorkerReadiness]
        ] = {}
        self._worker_targets: dict[str, WorkerBackedEstimator] = {}

    def register_target(
        self,
        target: str,
        *,
        stage_executors: Mapping[str, StageExecutor],
        estimator: Estimator,
    ) -> None:
        normalized = str(target or "").strip().lower()
        if normalized not in TARGET_STAGES:
            raise ValueError("unknown Podcast processing target")
        if normalized in self._worker_targets:
            raise ValueError(
                "Podcast target already has worker-backed registration"
            )
        executors = {
            str(stage or "").strip().lower(): executor
            for stage, executor in dict(stage_executors).items()
        }
        if set(executors) != set(TARGET_STAGES[normalized]):
            raise ValueError("Podcast target requires one executor per pipeline stage")
        if any(not callable(executor) for executor in executors.values()):
            raise TypeError("Podcast stage executors must be callable")
        if not callable(estimator):
            raise TypeError("Podcast cost estimator must be callable")
        self._targets[normalized] = (executors, estimator)

    def register_worker_backed_target(
        self,
        target: str,
        *,
        estimator: WorkerBackedEstimator,
    ) -> None:
        """Register target admission without pretending workers are executors."""

        normalized = str(target or "").strip().lower()
        if normalized not in TARGET_STAGES:
            raise ValueError("unknown Podcast processing target")
        if normalized in self._targets:
            raise ValueError("Podcast target already has legacy registration")
        if not callable(estimator):
            raise TypeError("Podcast worker-backed estimator must be callable")
        self._worker_targets[normalized] = estimator

    def register_stage_worker(
        self,
        stage: str,
        worker: StageWorker,
        *,
        readiness: StageWorkerReadiness,
    ) -> None:
        """Register execution plus a pure pre-claim provider readiness gate."""

        normalized = str(stage or "").strip().lower()
        known_stages = frozenset().union(*TARGET_STAGES.values())
        if normalized not in known_stages:
            raise ValueError("unknown Podcast processing stage")
        if not callable(worker):
            raise TypeError("Podcast stage worker must be callable")
        if not callable(readiness):
            raise TypeError("Podcast stage worker readiness must be callable")
        self._stage_workers[normalized] = (worker, readiness)

    def worker_for(self, stage: str) -> StageWorker | None:
        registered = self._stage_workers.get(str(stage or "").strip().lower())
        worker = registered[0] if registered is not None else None
        return worker if callable(worker) else None

    def stage_worker_ready(self, stage: str, provider_config: object) -> bool:
        """Fail closed before claim when the registered bundle cannot poll."""

        registered = self._stage_workers.get(str(stage or "").strip().lower())
        if registered is None:
            return False
        _worker, readiness = registered
        try:
            return readiness(provider_config) is True
        except Exception:
            return False

    def is_ready(self, target: str) -> bool:
        normalized = str(target or "").strip().lower()
        worker_estimator = self._worker_targets.get(normalized)
        if worker_estimator is not None:
            return callable(worker_estimator) and all(
                self.worker_for(stage) is not None
                for stage in TARGET_STAGES.get(normalized, ())
            )
        registered = self._targets.get(normalized)
        if registered is None:
            return False
        executors, estimator = registered
        return (
            set(executors) == set(TARGET_STAGES.get(normalized, ()))
            and all(callable(executor) for executor in executors.values())
            and callable(estimator)
        )

    def executor_for(self, target: str, stage: str) -> StageExecutor:
        registered = self._targets.get(str(target or "").strip().lower())
        executor = (
            registered[0].get(str(stage or "").strip().lower())
            if registered is not None
            else None
        )
        if not callable(executor):
            raise PodcastAdminError(
                "podcast_provider_unavailable", status_code=503
            )
        return executor

    def estimate(
        self,
        target: str,
        input_metadata: Mapping[str, Any],
        *,
        session: Session | None = None,
        podcast_config: PodcastConfig | None = None,
    ) -> AdmissionEstimate:
        worker_estimator = self._worker_targets.get(target)
        if worker_estimator is not None:
            if session is None or podcast_config is None:
                raise PodcastAdminError(
                    "podcast_provider_unavailable", status_code=503
                )
            try:
                value = worker_estimator(
                    session, dict(input_metadata), podcast_config
                )
            except PodcastAdminError:
                raise
            except Exception as exc:
                raise PodcastAdminError(
                    "podcast_provider_unavailable", status_code=503
                ) from exc
            if not isinstance(value, AdmissionEstimate):
                raise PodcastAdminError(
                    "podcast_provider_unavailable", status_code=503
                )
            return value
        registered = self._targets.get(target)
        if registered is None:
            raise PodcastAdminError(
                "podcast_provider_unavailable", status_code=503
            )
        _executors, estimator = registered
        try:
            value = estimator(dict(input_metadata))
        except Exception as exc:
            raise PodcastAdminError(
                "podcast_provider_unavailable", status_code=503
            ) from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PodcastAdminError(
                "podcast_provider_unavailable", status_code=503
            )
        return AdmissionEstimate(
            cost_minor=value,
            admission_fingerprint="",
        )


@dataclass(frozen=True)
class SelectedInput:
    stage: str
    artifact_id: str
    content_hash: str
    kind: str
    language: str
    audio_duration_ms: int | None = None

    def fingerprint(
        self,
        *,
        episode_id: str,
        admission_fingerprint: str,
        voice_profile_id: str = "",
    ) -> str:
        return processing_input_fingerprint(
            episode_id=episode_id,
            entry_stage=self.stage,
            artifact_id=self.artifact_id,
            content_hash=self.content_hash,
            kind=self.kind,
            language=self.language,
            audio_duration_ms=self.audio_duration_ms,
            admission_fingerprint=admission_fingerprint,
            voice_profile_id=voice_profile_id,
        )


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _begin_locked(session: Session, engine: Engine, lock_key: str) -> None:
    connection = session.connection()
    if engine.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif engine.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"dorami:podcast-processing:{lock_key}"},
        )
    else:
        session.rollback()
        raise PodcastAdminError("podcast_processing_conflict", status_code=503)


def _episode(session: Session, episode_id: str, *, for_update: bool = False) -> ArticleRecord:
    statement = select(ArticleRecord).where(ArticleRecord.id == episode_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    episode = session.exec(statement).first()
    if episode is None or episode.content_type != "podcast_episode":
        raise PodcastAdminError("podcast_not_found", status_code=404)
    return episode


def _locked_episode(
    session: Session, engine: Engine, episode_id: str
) -> ArticleRecord:
    """Load the episode and lock it when the database supports row locking."""

    preliminary = _episode(session, episode_id)
    source_id = preliminary.source_id
    episode = _episode(
        session,
        episode_id,
        for_update=engine.dialect.name == "postgresql",
    )
    if episode.source_id != source_id:
        raise PodcastAdminError("podcast_processing_conflict", status_code=409)
    return episode


def _require_runtime(
    config: PodcastConfig,
    registry: PodcastProcessingProviderRegistry,
    target: str,
) -> None:
    if (
        not config.processing_enabled
        or target not in config.provider_ready_targets
        or config.monthly_budget_cny_minor <= 0
        or config.per_run_budget_cny_minor <= 0
    ):
        raise PodcastAdminError("podcast_provider_unavailable", status_code=503)
    # Registry readiness proves the complete executor chain plus estimator;
    # estimation happens only after immutable input selection.
    if not registry.is_ready(target):
        raise PodcastAdminError("podcast_provider_unavailable", status_code=503)


def _current_text(
    session: Session, episode_id: str, kind: str
) -> PodcastTextArtifactRecord | None:
    publication = session.get(PodcastTextPublicationRecord, f"{episode_id}:{kind}")
    if publication is None or publication.status != "published":
        return None
    artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
    if (
        artifact is None
        or artifact.episode_id != episode_id
        or artifact.kind != kind
        or artifact.authority_id != publication.authority_id
    ):
        return None
    return artifact


def require_full_analysis_llm(session: Session, target: str) -> None:
    """Check the effective runtime LLM before preparing or enqueueing work."""

    if target != "full_analysis":
        return
    from services.daily_brief import resolve_llm_config

    if not resolve_llm_config(session).configured:
        raise PodcastAdminError("podcast_provider_unavailable", status_code=503)


def require_full_analysis_authority(
    session: Session,
    *,
    episode_id: str,
    target: str,
    episode: ArticleRecord | None = None,
) -> ArticleRecord:
    """Reject remote-owned analysis before any full-analysis provider work."""

    selected = episode or session.get(ArticleRecord, episode_id)
    if selected is None or selected.content_type != "podcast_episode":
        raise PodcastAdminError("podcast_not_found", status_code=404)
    eligibility, _reasons = _evaluate_full_analysis_authority(
        session,
        selected,
        requested_target=target,
    )
    if eligibility != "eligible":
        raise PodcastAdminError("podcast_stage_denied", status_code=403)
    return selected


def _select_external_input(
    session: Session,
    *,
    episode_id: str,
    target: str,
) -> SelectedInput:
    if target in {"full_analysis", "digest_blog"}:
        kinds = (
            ("publisher_transcript", "normalized_transcript")
            if target == "full_analysis"
            else ("transcript_zh", "publisher_transcript")
        )
        for kind in kinds:
            artifact = _current_text(session, episode_id, kind)
            if (
                target == "full_analysis"
                and kind == "publisher_transcript"
                and artifact is not None
                and not publisher_artifact_matches_current_locator(
                    session, episode_id=episode_id, artifact=artifact
                )
            ):
                artifact = None
            if artifact is None and kind == "normalized_transcript":
                artifact = session.exec(
                    select(PodcastTextArtifactRecord)
                    .join(
                        PodcastStageAttemptRecord,
                        PodcastStageAttemptRecord.id
                        == PodcastTextArtifactRecord.producing_attempt_id,
                    )
                    .where(
                        PodcastTextArtifactRecord.episode_id == episode_id,
                        PodcastTextArtifactRecord.kind == kind,
                        PodcastTextArtifactRecord.processing_id.is_not(None),
                        PodcastStageAttemptRecord.submission_state == "succeeded",
                        PodcastStageAttemptRecord.output_artifact_id
                        == PodcastTextArtifactRecord.id,
                        PodcastStageAttemptRecord.output_hash
                        == PodcastTextArtifactRecord.content_hash,
                    )
                    .order_by(
                        PodcastTextArtifactRecord.version.desc(),
                        PodcastTextArtifactRecord.created_at.desc(),
                    )
                ).first()
            if artifact is not None:
                language = str(artifact.language or "").lower()
                stage = (
                    "analyze"
                    if target == "full_analysis"
                    or kind == "transcript_zh"
                    or language.startswith("zh")
                    else "translate"
                )
                return SelectedInput(stage, artifact.id, artifact.content_hash, kind, artifact.language)
    episode = session.get(ArticleRecord, episode_id)
    try:
        locator_hash = enclosure_snapshot(episode).locator_hash if episode else ""
    except SourceMediaError:
        locator_hash = ""
    snapshot_statement = (
        select(PodcastSourceMediaSnapshotRecord)
        .where(
            PodcastSourceMediaSnapshotRecord.episode_id == episode_id,
            PodcastSourceMediaSnapshotRecord.locator_hash == locator_hash,
        )
        .order_by(
            PodcastSourceMediaSnapshotRecord.created_at.desc(),
            PodcastSourceMediaSnapshotRecord.id.desc(),
        )
    )
    if session.get_bind().dialect.name == "postgresql":
        snapshot_statement = snapshot_statement.with_for_update()
    snapshot = session.exec(snapshot_statement).first()
    if snapshot is not None:
        return SelectedInput(
            "asr",
            snapshot.id,
            snapshot.content_hash,
            "source_media_snapshot",
            "und",
            source_media_duration_ms(snapshot.duration_seconds),
        )
    raise PodcastAdminError("podcast_artifact_not_ready", status_code=409)


def select_full_analysis_input(
    engine: Engine,
    *,
    episode_id: str,
) -> SelectedInput:
    """Read the current authoritative full-analysis input for reconciliation."""

    with Session(engine) as session:
        return _select_external_input(
            session, episode_id=episode_id, target="full_analysis"
        )


def _budget_period(config: PodcastConfig) -> str:
    local = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(config.budget_timezone))
    return local.strftime("%Y-%m")


def _budget_available(
    session: Session, config: PodcastConfig, estimate: int
) -> bool:
    if estimate > config.per_run_budget_cny_minor:
        return False
    period = _budget_period(config)
    breach = session.exec(
        select(PodcastCostLedgerRecord.id).where(
            PodcastCostLedgerRecord.budget_scope == config.budget_scope,
            PodcastCostLedgerRecord.budget_period == period,
            PodcastCostLedgerRecord.budget_breached.is_(True),
        )
    ).first()
    if breach is not None:
        return False
    spent = int(session.exec(select(func.coalesce(func.sum(PodcastCostLedgerRecord.actual_cost_minor), 0)).where(
        PodcastCostLedgerRecord.budget_scope == config.budget_scope,
        PodcastCostLedgerRecord.budget_period == period,
    )).one())
    reserved = int(session.exec(select(func.coalesce(func.sum(PodcastBudgetReservationRecord.reserved_minor), 0)).where(
        PodcastBudgetReservationRecord.budget_scope == config.budget_scope,
        PodcastBudgetReservationRecord.budget_period == period,
        PodcastBudgetReservationRecord.status == "reserved",
    )).one())
    return spent + reserved + estimate <= config.monthly_budget_cny_minor


def _error_for_record(record: PodcastProcessingRecord) -> PodcastAdminError | None:
    if record.eligibility_status == "invalid_input":
        return PodcastAdminError("podcast_input_changed", status_code=409, processing_id=record.id)
    if record.eligibility_status == "over_budget":
        return PodcastAdminError("podcast_budget_exceeded", status_code=409, processing_id=record.id)
    return None


def _status_for_code(code: str) -> int:
    if code == "podcast_provider_unavailable":
        return 503
    if code == "podcast_stage_denied":
        return 403
    return 409


def _replay_request(
    session: Session,
    *,
    episode_id: str,
    target: str,
    idempotency_key: str,
    reason: str,
    actor: str,
    narration_artifact_id: str | None = None,
    narration_content_hash: str | None = None,
    voice_profile_id: str | None = None,
) -> PodcastProcessingRecord | None:
    record = session.exec(
        select(PodcastProcessingRecord).where(
            PodcastProcessingRecord.idempotency_key == idempotency_key
        )
    ).first()
    if record is None:
        return None
    if (
        record.episode_id != episode_id
        or record.requested_target != target
        or record.requested_by != actor
        or record.request_reason != reason
        or (
            target == "digest_audio"
            and (
                record.narration_artifact_id != narration_artifact_id
                or record.narration_content_hash != narration_content_hash
                or record.voice_profile_id != voice_profile_id
            )
        )
    ):
        raise PodcastAdminError("podcast_processing_conflict", status_code=409)
    failure = _error_for_record(record)
    if failure is not None:
        raise failure
    return record


def _enqueue_locked(
    session: Session,
    *,
    episode_id: str,
    selected: SelectedInput,
    target: str,
    idempotency_key: str,
    reason: str,
    actor: str,
    selection_source: str,
    estimate: AdmissionEstimate,
    config: PodcastConfig,
    policy: PodcastStagePolicy,
    narration_artifact_id: str | None = None,
    narration_content_hash: str | None = None,
    voice_profile_id: str | None = None,
) -> PodcastProcessingRecord:
    fingerprint = selected.fingerprint(
        episode_id=episode_id,
        admission_fingerprint=estimate.admission_fingerprint,
        voice_profile_id=voice_profile_id or "",
    )
    existing = session.exec(
        select(PodcastProcessingRecord).where(
            (PodcastProcessingRecord.idempotency_key == idempotency_key)
            | (
                (PodcastProcessingRecord.episode_id == episode_id)
                & (PodcastProcessingRecord.input_fingerprint == fingerprint)
                & (
                    PodcastProcessingRecord.pipeline_version
                    == (
                        config.audio_pipeline_version
                        if target == "digest_audio"
                        else config.text_pipeline_version
                    )
                )
                & (PodcastProcessingRecord.requested_target == target)
                & (
                    PodcastProcessingRecord.policy_version
                    == config.processing_policy_version
                )
                & (PodcastProcessingRecord.budget_scope == config.budget_scope)
                & (
                    PodcastProcessingRecord.budget_period
                    == _budget_period(config)
                )
                & (
                    PodcastProcessingRecord.budget_limit_minor
                    == config.monthly_budget_cny_minor
                )
                & (
                    PodcastProcessingRecord.per_run_budget_minor
                    == config.per_run_budget_cny_minor
                )
            )
        )
    ).first()
    if existing is not None and existing.idempotency_key != idempotency_key:
        raise PodcastAdminError(
            "podcast_processing_conflict",
            status_code=409,
            processing_id=existing.id,
        )
    prior_status = existing.processing_status if existing is not None else None
    prior_eligibility = existing.eligibility_status if existing is not None else None
    record = enqueue_processing(
        session,
        episode_id=episode_id,
        stage=selected.stage,
        input_fingerprint=fingerprint,
        pipeline_version=(
            config.audio_pipeline_version if target == "digest_audio" else config.text_pipeline_version
        ),
        policy_version=config.processing_policy_version,
        requested_target=target,
        idempotency_key=idempotency_key,
        selection_source=selection_source,
        requested_by=actor,
        request_reason=reason,
        estimated_cost_minor=estimate.cost_minor,
        input_artifact_id=selected.artifact_id,
        input_artifact_kind=selected.kind,
        input_content_hash=selected.content_hash,
        input_language=selected.language,
        budget_scope=config.budget_scope,
        budget_period=_budget_period(config),
        budget_limit_minor=config.monthly_budget_cny_minor,
        per_run_budget_minor=config.per_run_budget_cny_minor,
        narration_artifact_id=narration_artifact_id,
        narration_content_hash=narration_content_hash,
        voice_profile_id=voice_profile_id,
        policy=policy,
        _transaction_open=True,
    )
    failure = _error_for_record(record)
    if failure is not None:
        session.commit()
        raise failure
    needs_budget_check = existing is None or (
        prior_status == "not_required"
        and prior_eligibility != "eligible"
        and record.processing_status == "queued"
    )
    if not needs_budget_check:
        session.commit()
        session.refresh(record)
        session.expunge(record)
        return record
    if not _budget_available(session, config, estimate.cost_minor):
        processing_id = record.id
        record.eligibility_status = "over_budget"
        record.eligibility_reasons_json = '["configured CNY budget is unavailable"]'
        record.processing_status = "not_required"
        record.error_code = "over_budget"
        record.error_message = "configured CNY budget is unavailable"
        record.updated_at = _now()
        session.add(record)
        session.commit()
        raise PodcastAdminError(
            "podcast_budget_exceeded", status_code=409, processing_id=processing_id
        )
    session.commit()
    session.refresh(record)
    session.expunge(record)
    return record


def request_processing(
    engine: Engine,
    registry: PodcastProcessingProviderRegistry,
    config: PodcastConfig,
    *,
    episode_id: str,
    target: str,
    selection_override: bool,
    idempotency_key: str,
    reason: str,
    actor: str,
) -> PodcastProcessingRecord:
    if target not in {"transcript", "full_analysis", "digest_blog"}:
        raise PodcastAdminError("podcast_processing_conflict", status_code=422)
    if config.installation != "external":
        raise PodcastAdminError("podcast_stage_denied", status_code=403)
    policy = PodcastStagePolicy(config)
    with Session(engine) as session:
        try:
            _begin_locked(session, engine, f"{episode_id}:{target}")
            episode = _locked_episode(session, engine, episode_id)
            require_full_analysis_authority(
                session,
                episode_id=episode_id,
                target=target,
                episode=episode,
            )
            replay = _replay_request(
                session,
                episode_id=episode_id,
                target=target,
                idempotency_key=idempotency_key,
                reason=reason,
                actor=actor,
            )
            if replay is not None:
                session.expunge(replay)
                session.commit()
                return replay
            _require_runtime(config, registry, target)
            require_full_analysis_llm(session, target)
            selected = _select_external_input(session, episode_id=episode_id, target=target)
            selection_source = "editor" if selection_override else "policy"
            if not selection_override:
                analysis = session.get(ArticleAnalysisRecord, episode_id)
                initial_candidate = bool(
                    analysis is not None
                    and analysis.status == "succeeded"
                    and analysis.analysis_basis == "podcast_show_notes"
                    and analysis.quality_score is not None
                    and float(analysis.quality_score)
                    >= podcast_premium.INITIAL_PROCESSING_THRESHOLD
                )
                transcript_refresh = bool(
                    analysis is not None
                    and analysis.status == "succeeded"
                    and analysis.analysis_basis
                    in {"publisher_transcript", "asr_transcript"}
                    and selected.kind
                    in {"publisher_transcript", "normalized_transcript"}
                    and selected.artifact_id != analysis.transcript_artifact_id
                )
                if target != "full_analysis" or not (
                    initial_candidate or transcript_refresh
                ):
                    raise PodcastAdminError(
                        "podcast_selection_required", status_code=409
                    )
            estimate = registry.estimate(
                target,
                selected.__dict__,
                session=session,
                podcast_config=config,
            )
            return _enqueue_locked(
                session,
                episode_id=episode_id,
                selected=selected,
                target=target,
                idempotency_key=idempotency_key,
                reason=reason,
                actor=actor,
                selection_source=selection_source,
                estimate=estimate,
                config=config,
                policy=policy,
            )
        except PodcastAdminError:
            if session.in_transaction():
                session.rollback()
            raise
        except (
            PodcastProcessingConflict,
            PodcastStageDenied,
            IntegrityError,
            ValueError,
        ) as exc:
            session.rollback()
            code = "podcast_stage_denied" if isinstance(exc, PodcastStageDenied) else "podcast_processing_conflict"
            status = 403 if isinstance(exc, PodcastStageDenied) else 409
            raise PodcastAdminError(code, status_code=status) from exc


def get_processing(engine: Engine, processing_id: str) -> PodcastProcessingRecord:
    with Session(engine) as session:
        record = session.get(PodcastProcessingRecord, processing_id)
        if record is None:
            raise PodcastAdminError("podcast_not_found", status_code=404)
        session.expunge(record)
        session.rollback()
        return record


def retry_processing(
    engine: Engine,
    registry: PodcastProcessingProviderRegistry,
    config: PodcastConfig,
    *,
    processing_id: str,
    idempotency_key: str,
    expected_attempt_count: int,
    reason: str,
    actor: str,
) -> PodcastProcessingRecord:
    policy = PodcastStagePolicy(config)
    with Session(engine) as session:
        try:
            _begin_locked(session, engine, f"retry:{processing_id}")
            statement = select(PodcastProcessingRecord).where(PodcastProcessingRecord.id == processing_id)
            if engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            record = session.exec(statement).first()
            if record is None:
                raise PodcastAdminError("podcast_not_found", status_code=404)
            existing = session.exec(select(PodcastProcessingCommandRecord).where(
                PodcastProcessingCommandRecord.processing_id == processing_id,
                PodcastProcessingCommandRecord.command_type == "manual_retry",
                PodcastProcessingCommandRecord.idempotency_key == idempotency_key,
            )).first()
            if existing is not None:
                if (
                    existing.expected_attempt_count != expected_attempt_count
                    or existing.requested_by != actor
                    or existing.reason != reason
                ):
                    raise PodcastAdminError("podcast_processing_conflict", status_code=409)
                if existing.outcome == "rejected":
                    raise PodcastAdminError(
                        existing.error_code,
                        status_code=_status_for_code(existing.error_code),
                        processing_id=processing_id,
                        message=existing.error_message,
                    )
                session.expunge(record)
                session.commit()
                return record

            active_attempt = session.exec(select(PodcastStageAttemptRecord).where(
                PodcastStageAttemptRecord.processing_id == processing_id,
                PodcastStageAttemptRecord.submission_state.in_(ACTIVE_ATTEMPT_STATES),
            )).first()
            recoverable_output = False
            if active_attempt is not None and record.stage == "asr":
                reservation = session.exec(
                    select(PodcastBudgetReservationRecord).where(
                        PodcastBudgetReservationRecord.attempt_id == active_attempt.id
                    )
                ).first()
                artifact = (
                    session.get(
                        PodcastTextArtifactRecord,
                        active_attempt.output_artifact_id,
                    )
                    if active_attempt.output_artifact_id
                    else None
                )
                recoverable_output = bool(
                    active_attempt.submission_state == "submitted"
                    and not active_attempt.request_unknown
                    and active_attempt.output_artifact_kind
                    == "normalized_transcript"
                    and reservation is not None
                    and reservation.status == "settled"
                    and artifact is not None
                    and artifact.kind == "normalized_transcript"
                    and artifact.processing_id == record.id
                    and artifact.producing_attempt_id == active_attempt.id
                    and artifact.content_hash == active_attempt.output_hash
                )
            if recoverable_output:
                if config.processing_enabled:
                    code = ""
                    message = ""
                else:
                    code = "podcast_provider_unavailable"
                    message = ERROR_MESSAGES[code]
            else:
                try:
                    _require_runtime(config, registry, record.requested_target)
                except PodcastAdminError as exc:
                    code = exc.code
                    message = exc.message
                else:
                    code = ""
                    message = ""
            if not code:
                try:
                    require_full_analysis_llm(session, record.requested_target)
                except PodcastAdminError as exc:
                    code = exc.code
                    message = exc.message
            if not code and (
                record.processing_status == "reconciliation_required"
                or (active_attempt is not None and not recoverable_output)
            ):
                code = "podcast_provider_reconciliation_required"
                message = ERROR_MESSAGES[code]
            elif not code and record.attempt_count != expected_attempt_count:
                code = "podcast_processing_conflict"
                message = ERROR_MESSAGES[code]
            elif not code and record.budget_breached and not recoverable_output:
                code = "podcast_budget_exceeded"
                message = ERROR_MESSAGES[code]
            elif not code and record.processing_status not in {"failed", "retry_wait", "not_required"}:
                code = "podcast_processing_conflict"
                message = ERROR_MESSAGES[code]
            elif not code and not recoverable_output and (
                record.budget_scope != config.budget_scope
                or record.budget_period != _budget_period(config)
                or record.budget_limit_minor != config.monthly_budget_cny_minor
                or record.per_run_budget_minor != config.per_run_budget_cny_minor
            ):
                code = "podcast_processing_conflict"
                message = "Podcast 任务预算快照已过期，请创建新任务"
            elif not code:
                try:
                    policy.require_stage(record.stage, boundary="enqueue")
                except PodcastStageDenied:
                    code = "podcast_stage_denied"
                    message = ERROR_MESSAGES[code]
                eligibility, reasons = _evaluate_processing_eligibility(session, record, policy)
                if not code and eligibility != "eligible":
                    code = "podcast_input_changed"
                    message = ERROR_MESSAGES[code]
                if not code and not recoverable_output and not _budget_available(
                    session, config, record.estimated_cost_minor
                ):
                    code = "podcast_budget_exceeded"
                    message = ERROR_MESSAGES[code]
            command = PodcastProcessingCommandRecord(
                id=uuid.uuid4().hex,
                processing_id=processing_id,
                command_type="manual_retry",
                idempotency_key=idempotency_key,
                expected_attempt_count=expected_attempt_count,
                requested_by=actor,
                reason=reason,
                outcome="rejected" if code else "accepted",
                error_code=code,
                error_message=message,
                created_at=_now(),
            )
            session.add(command)
            if code:
                session.commit()
                raise PodcastAdminError(
                    code,
                    status_code=_status_for_code(code),
                    processing_id=processing_id,
                    message=message,
                )
            stamp = _now()
            record.eligibility_status = "eligible"
            record.eligibility_reasons_json = "[]"
            record.processing_status = "queued"
            record.lease_owner = None
            record.lease_token = None
            record.lease_expires_at = None
            record.next_retry_at = None
            record.error_code = ""
            record.error_message = ""
            record.finished_at = None
            record.queued_at = stamp
            record.updated_at = stamp
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except PodcastAdminError:
            if session.in_transaction():
                session.rollback()
            raise
        except IntegrityError as exc:
            session.rollback()
            raise PodcastAdminError("podcast_processing_conflict", status_code=409) from exc


def serialize_processing(record: PodcastProcessingRecord) -> dict[str, Any]:
    """Return the documented redacted view (no lease/provider request details)."""

    try:
        reasons = json.loads(record.eligibility_reasons_json or "[]")
    except (TypeError, ValueError):
        reasons = []
    return {
        "id": record.id,
        "episode_id": record.episode_id,
        "target": record.requested_target,
        "selection_source": record.selection_source,
        "requested_by": record.requested_by,
        "request_reason": record.request_reason,
        "input_fingerprint": record.input_fingerprint,
        "input_artifact_id": record.input_artifact_id,
        "input_artifact_kind": record.input_artifact_kind,
        "input_content_hash": record.input_content_hash,
        "input_language": record.input_language,
        "pipeline_version": record.pipeline_version,
        "policy_version": record.policy_version,
        "eligibility_status": record.eligibility_status,
        "eligibility_reasons": reasons,
        "status": record.processing_status,
        "stage": record.stage,
        "attempt_count": record.attempt_count,
        "cost_currency": "CNY",
        "estimated_cost_minor": record.estimated_cost_minor,
        "budget_scope": record.budget_scope,
        "budget_period": record.budget_period,
        "budget_limit_minor": record.budget_limit_minor,
        "per_run_budget_minor": record.per_run_budget_minor,
        "actual_cost_minor": record.actual_cost_minor,
        "budget_breached": record.budget_breached,
        "narration_artifact_id": record.narration_artifact_id,
        "narration_content_hash": record.narration_content_hash,
        "voice_profile_id": record.voice_profile_id,
        "error_code": record.error_code or None,
        "error_message": record.error_message or None,
        "queued_at": record.queued_at,
        "started_at": record.started_at,
        "updated_at": record.updated_at,
        "finished_at": record.finished_at,
    }
