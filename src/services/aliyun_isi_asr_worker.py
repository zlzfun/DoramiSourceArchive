"""Aliyun ISI bridge for the durable provider-neutral Podcast ASR runner.

The scheduler supplies one :class:`PodcastStagePolicy` containing the resolved
Aliyun snapshot. New submissions revalidate the current RSS enclosure against
the immutable source-media snapshot immediately before provider I/O. The wire
client safely resolves publisher redirects before handing the final CDN URL to
Aliyun. Polling an existing task remains URL-independent.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Protocol

from sqlmodel import Session, select

from config import AliyunIsiConfig, PodcastArtifactStorageConfig, PodcastConfig
from models.db import (
    ArticleRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
)
from services import aliyun_isi_config as aliyun_isi_config_service
from services import user_sources
from services.aliyun_isi_asr import (
    AliyunAsrError,
    AliyunAsrPollError,
    AliyunAsrRejected,
    AliyunAsrSubmissionUnknown,
    AliyunIsiAsrClient,
    AsrPollResult,
    AsrState,
    AsrSubmission,
    AsrTranscript,
    validate_provider_fetch_url,
)
from services.aliyun_isi_usage import (
    AliyunIsiUsageConfigurationError,
    asr_usage_plan,
)
from services.podcast_asr_worker import (
    AsrProviderPlan,
    AsrPlanningUnavailable,
    AsrWorkerConfig,
    AsrWorkerStep,
    PROVIDER_FALLBACK_MARKER,
    run_asr_worker_step,
)
from services.podcast_artifacts import PodcastArtifactStore
from services.aliyun_oss_asr_relay import AliyunOssAsrRelay, OssRelayError
from services.podcast_processing import deterministic_input_fingerprint
from services.podcast_processing_admin import AdmissionEstimate, PodcastAdminError
from services.podcast_source_media import (
    SourceMediaError,
    enclosure_snapshot,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services.podcast_transcript_dedup import deduplicate_transcript_evidence
from services.podcast_worker_contracts import (
    Accepted,
    ArtifactRef,
    ExecutionIdentity,
    ExecutionKind,
    Failure,
    FailureKind,
    Indeterminate,
    NormalizedUsage,
    Pending,
    PollOutcome,
    ProviderUsagePlan,
    Rejected,
    StageContext,
    StagePlan,
    SubmitOutcome,
    Succeeded,
    TaskFailed,
    TextOutput,
    Unknown,
)


PROVIDER_NAME = "aliyun-isi"

# These provider task failures happen before recording-file recognition starts:
# Aliyun could not download, inspect, or decode the supplied media and therefore
# produced no recognition result. They consume none of Dorami's local
# audio-seconds allowance. Unknown provider failures remain conservatively
# charged against the frozen source duration.
UNPROCESSED_AUDIO_STATUS_CODES = frozenset(
    {
        40_270_003,
        41_050_002,
        41_050_003,
        41_050_004,
        41_050_005,
        41_050_006,
        41_050_007,
        41_050_008,
        41_050_011,
        41_050_023,
        41_050_024,
        41_050_025,
        41_050_026,
    }
)

# Only failures that prove Aliyun could not retrieve the publisher URL qualify
# for the one-shot OSS relay. Decoder/format/content-length failures are not
# download transport failures and deliberately stay terminal.
DOWNLOAD_FALLBACK_STATUS_CODES = frozenset(
    {41_050_002, 41_050_024, 41_050_025, 41_050_026}
)


class _AsrClient(Protocol):
    def submit(self, file_url: str) -> AsrSubmission: ...

    def submit_internal(self, file_url: str) -> AsrSubmission: ...

    def poll(self, task_id: str) -> AsrPollResult: ...

    def close(self) -> None: ...


class _WorkerRegistry(Protocol):
    def register_stage_worker(
        self, stage: str, worker, *, readiness
    ) -> None: ...

    def register_worker_backed_target(
        self, target: str, *, estimator
    ) -> None: ...


ClientFactory = Callable[[AliyunIsiConfig], _AsrClient]
Clock = Callable[[], dt.datetime]
ConfigResolver = Callable[[Session], AliyunIsiConfig]
DirectAudioUrlResolver = Callable[[StageContext], str]
FallbackAudioUrlResolver = Callable[[StageContext], str]
FallbackCleanup = Callable[[StageContext], None]
FallbackSubmissionSelector = Callable[[StageContext], bool]


class _OssRelay(Protocol):
    def prepare(
        self,
        context: StageContext,
        *,
        enclosure,
        expected: PodcastSourceMediaSnapshotRecord,
    ) -> str: ...

    def delete(self, context: StageContext) -> None: ...


RelayFactory = Callable[
    [AliyunIsiConfig, PodcastArtifactStore, PodcastArtifactStorageConfig],
    _OssRelay,
]


def _relay_factory(
    config: AliyunIsiConfig,
    store: PodcastArtifactStore,
    storage_config: PodcastArtifactStorageConfig,
) -> _OssRelay:
    return AliyunOssAsrRelay(config, store, storage_config)


class DirectAudioUrlError(ValueError):
    """Safe provider-submit refusal that never includes the enclosure URL."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _direct_audio_url(session: Session, context: StageContext) -> str:
    """Revalidate the RSS enclosure and immutable snapshot before submit."""

    process = session.get(PodcastProcessingRecord, context.processing_id)
    episode = session.get(ArticleRecord, context.episode_id)
    snapshot = session.get(
        PodcastSourceMediaSnapshotRecord, context.input_artifact.artifact_id
    )
    if episode is None:
        raise DirectAudioUrlError("aliyun_source_media_url_unavailable")
    if not user_sources.source_content_may_leave_deployment(
        session, episode.source_id
    ):
        raise DirectAudioUrlError("aliyun_source_media_url_unavailable")
    try:
        enclosure = enclosure_snapshot(episode)
    except SourceMediaError as exc:
        raise DirectAudioUrlError(
            "aliyun_source_media_url_unavailable"
        ) from exc
    expected_locator_hash = hashlib.sha256(
        enclosure.url.encode("utf-8")
    ).hexdigest()
    if (
        process is None
        or process.episode_id != context.episode_id
        or process.stage != "asr"
        or process.input_artifact_id != context.input_artifact.artifact_id
        or process.input_artifact_kind != "source_media_snapshot"
        or process.input_content_hash != context.input_artifact.content_hash
        or snapshot is None
        or snapshot.episode_id != context.episode_id
        or snapshot.content_hash != context.input_artifact.content_hash
        or snapshot.locator_hash != expected_locator_hash
    ):
        raise DirectAudioUrlError("aliyun_source_media_binding_changed")
    try:
        validate_provider_fetch_url(enclosure.url)
        return enclosure.url
    except ValueError as exc:
        raise DirectAudioUrlError("aliyun_source_media_url_invalid") from exc


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Aliyun ASR worker clock must be timezone-aware")
    return value.astimezone(dt.timezone.utc)


def aliyun_asr_identity(config: AliyunIsiConfig) -> ExecutionIdentity:
    """Build the non-secret persisted identity used to resume provider tasks."""

    return ExecutionIdentity.from_settings(
        execution_kind=ExecutionKind.PROVIDER,
        provider=PROVIDER_NAME,
        model=config.asr_product,
        revision=config.asr_api_version,
        settings={
            "asr_domain": config.asr_domain,
            "asr_task_version": config.asr_task_version,
            "auto_split": config.asr_auto_split,
            "enable_sample_rate_adaptive": (
                config.asr_enable_sample_rate_adaptive
            ),
            "enable_words": config.asr_enable_words,
            "price_cny_minor_per_hour": config.asr_price_cny_minor_per_hour,
            "pricing_revision": config.asr_pricing_revision,
            "region_id": config.region_id,
        },
    )


def aliyun_asr_admission_fingerprint(config: AliyunIsiConfig) -> str:
    """Hash non-secret execution/accounting semantics plus the submit AppKey."""

    app_key_digest = hashlib.sha256(config.app_key.encode("utf-8")).hexdigest()
    return deterministic_input_fingerprint(
        {
            "schema": "aliyun-isi-asr-admission-v1",
            "identity_fingerprint": aliyun_asr_identity(
                config
            ).settings_fingerprint,
            "app_key_sha256": app_key_digest,
            "quota_scope": config.asr_quota_scope,
            "quota_timezone": config.asr_quota_timezone,
            "daily_audio_seconds_limit": (
                config.asr_daily_audio_seconds_limit
            ),
            "max_audio_seconds_per_file": config.asr_max_audio_seconds_per_file,
            "entitlement_ends_at": config.asr_entitlement_ends_at,
            "provider_deadline_seconds": (
                config.asr_provider_deadline_seconds
            ),
            "request_timeout_seconds": config.request_timeout_seconds,
            "price_cny_minor_per_hour": (
                config.asr_price_cny_minor_per_hour
            ),
            "pricing_revision": config.asr_pricing_revision,
        }
    )


def _resolve_aliyun_config(session: Session) -> AliyunIsiConfig:
    return aliyun_isi_config_service.resolve_config(session)


@dataclass(frozen=True)
class AliyunIsiAsrAdmissionEstimator:
    """Resolve and freeze one submit-capable Aliyun admission snapshot."""

    config_resolver: ConfigResolver = _resolve_aliyun_config
    clock: Clock = lambda: dt.datetime.now(dt.timezone.utc)

    def __call__(
        self,
        session: Session,
        input_metadata: dict[str, object],
        podcast_config: PodcastConfig,
    ) -> AdmissionEstimate:
        if not isinstance(podcast_config, PodcastConfig):
            raise TypeError("Podcast config is required for ASR admission")
        duration = input_metadata.get("audio_duration_ms")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration <= 0
        ):
            raise ValueError("positive source media duration is required")
        snapshot = self.config_resolver(session)
        if not isinstance(snapshot, AliyunIsiConfig):
            raise TypeError("Aliyun ASR config resolver returned invalid data")
        if not snapshot.asr_configured or not snapshot.asr_accounting_ready:
            raise ValueError("Aliyun ASR submit/accounting is unavailable")
        if duration > snapshot.asr_max_audio_seconds_per_file * 1000:
            raise PodcastAdminError(
                "podcast_source_media_too_long", status_code=422
            )
        plan = asr_usage_plan(
            snapshot,
            audio_duration_ms=duration,
            now=_utc(self.clock()),
        )
        return AdmissionEstimate(
            cost_minor=plan.estimated_cost_minor,
            admission_fingerprint=aliyun_asr_admission_fingerprint(snapshot),
        )


def aliyun_asr_worker_ready(provider_config: object) -> bool:
    """Check only configuration required to safely poll an existing paid task."""

    return bool(
        isinstance(provider_config, AliyunIsiConfig)
        and provider_config.asr_poll_configured
        and provider_config.request_timeout_seconds > 0
        and provider_config.asr_poll_interval_seconds > 0
    )


def _usage(audio_duration_ms: int, *, reserved_cost_minor: int) -> NormalizedUsage:
    if (
        isinstance(audio_duration_ms, bool)
        or not isinstance(audio_duration_ms, int)
        or audio_duration_ms <= 0
    ):
        raise ValueError("persisted ASR audio duration must be positive")
    if (
        isinstance(reserved_cost_minor, bool)
        or not isinstance(reserved_cost_minor, int)
        or reserved_cost_minor < 0
    ):
        raise ValueError("persisted ASR reserved cost must be nonnegative")
    return NormalizedUsage(
        cost_minor=reserved_cost_minor,
        audio_duration_ms=audio_duration_ms,
        currency="CNY",
    )


def _failure(
    *,
    kind: FailureKind,
    code: str,
    retryable: bool,
    retry_after_seconds: int | None = None,
) -> Failure:
    return Failure(
        kind=kind,
        code=code,
        message="Aliyun ISI ASR operation did not complete",
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )


def _error_code(prefix: str, value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    safe = "".join(
        character if character.isalnum() or character in {"_", ":", ".", "-"}
        else "_"
        for character in normalized
    )[:96]
    return f"aliyun_{prefix}_{safe or 'unknown'}"


def _wall_clock_duration_ms(
    transcript: AsrTranscript,
    *,
    source_media_duration_ms: int | None,
) -> int:
    """Convert provider channel-duration accounting to playback wall time."""

    if source_media_duration_ms is None:
        return transcript.audio_duration_ms
    expected = int(source_media_duration_ms)
    channels = {segment.channel_id for segment in transcript.segments}
    channel_count = len(channels)
    if channel_count <= 1:
        return transcript.audio_duration_ms
    tolerance_ms = max(5_000, channel_count * 1_000)
    if abs(transcript.audio_duration_ms - expected * channel_count) > tolerance_ms:
        return transcript.audio_duration_ms
    timeline_end = max(
        (item.end_ms for item in (*transcript.segments, *transcript.words)),
        default=0,
    )
    if timeline_end > expected + tolerance_ms:
        return transcript.audio_duration_ms
    return expected


def _normalized_transcript(
    transcript: AsrTranscript,
    *,
    language: str,
    source_media_duration_ms: int | None = None,
) -> str:
    """Map Aliyun sentence/word evidence to the canonical provider-neutral shape."""

    deduplicated = deduplicate_transcript_evidence(
        transcript.segments,
        transcript.words,
    )
    segments = deduplicated.segments
    words = deduplicated.words
    segment_words: list[list[dict[str, object]]] = [
        [] for _ in segments
    ]
    for word in words:
        matches = [
            index
            for index, segment in enumerate(segments)
            if word.channel_id == segment.channel_id
            and segment.begin_ms <= word.begin_ms
            and word.end_ms <= segment.end_ms
        ]
        if len(matches) != 1:
            raise ValueError("Aliyun ASR word evidence is not bound to one sentence")
        segment_words[matches[0]].append(
            {
                "channel": word.channel_id,
                "end_ms": word.end_ms,
                "start_ms": word.begin_ms,
                "text": word.text,
            }
        )
    document = {
        "audio_duration_ms": _wall_clock_duration_ms(
            transcript,
            source_media_duration_ms=source_media_duration_ms,
        ),
        "language": language,
        "segments": [
            {
                "channel": segment.channel_id,
                "end_ms": segment.end_ms,
                "start_ms": segment.begin_ms,
                "text": segment.text,
                "words": segment_words[index],
            }
            for index, segment in enumerate(segments)
        ],
        "text": "\n".join(segment.text for segment in segments),
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class AliyunIsiAsrUsagePlanner:
    config: AliyunIsiConfig

    def plan_usage(
        self,
        *,
        identity: ExecutionIdentity,
        input_artifact: ArtifactRef,
        audio_duration_ms: int,
        now: dt.datetime,
    ) -> ProviderUsagePlan:
        if identity != aliyun_asr_identity(self.config):
            raise ValueError("Aliyun ASR usage identity does not match config snapshot")
        if input_artifact.kind != "source_media_snapshot":
            raise ValueError("Aliyun ASR usage requires a source media snapshot")
        if audio_duration_ms > self.config.asr_max_audio_seconds_per_file * 1000:
            raise AsrPlanningUnavailable(
                "Aliyun ASR source media exceeds the per-task duration limit"
            )
        try:
            return asr_usage_plan(
                self.config,
                audio_duration_ms=audio_duration_ms,
                now=now,
            )
        except (AliyunIsiUsageConfigurationError, ValueError) as exc:
            raise AsrPlanningUnavailable(
                "Aliyun ASR accounting plan is unavailable"
            ) from exc


class AliyunIsiAsrAdapter:
    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        client: _AsrClient,
        clock: Clock,
        transcript_language: str,
        direct_audio_url_resolver: DirectAudioUrlResolver | None = None,
        fallback_audio_url_resolver: FallbackAudioUrlResolver | None = None,
        fallback_submission_selector: FallbackSubmissionSelector | None = None,
        fallback_cleanup: FallbackCleanup | None = None,
    ) -> None:
        self._config = config
        self._direct_audio_url_resolver = (
            direct_audio_url_resolver
            if direct_audio_url_resolver is not None
            else lambda _context: (_ for _ in ()).throw(
                DirectAudioUrlError("aliyun_source_media_url_unavailable")
            )
        )
        self._fallback_audio_url_resolver = fallback_audio_url_resolver
        self._fallback_submission_selector = fallback_submission_selector
        self._fallback_cleanup = fallback_cleanup
        self._client = client
        self._clock = clock
        self._language = str(transcript_language or "").strip()
        if not self._language:
            raise ValueError("transcript_language is required")
        self._identity = aliyun_asr_identity(config)

    def plan(
        self,
        input_artifact: ArtifactRef,
        *,
        audio_duration_ms: int,
        now: dt.datetime,
    ) -> AsrProviderPlan:
        try:
            usage = asr_usage_plan(
                self._config,
                audio_duration_ms=audio_duration_ms,
                now=now,
            )
        except (AliyunIsiUsageConfigurationError, ValueError) as exc:
            raise AsrPlanningUnavailable(
                "Aliyun ASR execution plan is unavailable"
            ) from exc
        return AsrProviderPlan(
            identity=self._identity,
            stage=StagePlan(
                estimated_cost_minor=usage.estimated_cost_minor,
                poll_interval_seconds=self._config.asr_poll_interval_seconds,
                deadline_seconds=usage.deadline_seconds,
            ),
            admission_fingerprint=aliyun_asr_admission_fingerprint(
                self._config
            ),
        )

    def supports(self, identity: ExecutionIdentity) -> bool:
        # Provider protocol/config can rotate while an already-paid task is in
        # flight. Its immutable TaskId may still be disclosed only to another
        # Aliyun adapter; provider errors then flow through reconciliation.
        return (
            isinstance(identity, ExecutionIdentity)
            and identity.execution_kind is ExecutionKind.PROVIDER
            and identity.provider == PROVIDER_NAME
        )

    def submit(
        self, context: StageContext, *, provider_request_key: str
    ) -> SubmitOutcome:
        del provider_request_key
        if context.identity != self._identity:
            return Rejected(
                _failure(
                    kind=FailureKind.PROTOCOL,
                    code="aliyun_identity_mismatch",
                    retryable=False,
                )
            )
        if not self._config.app_key:
            return Rejected(
                _failure(
                    kind=FailureKind.TRANSIENT,
                    code="aliyun_submit_app_key_unavailable",
                    retryable=True,
                    retry_after_seconds=self._config.asr_poll_interval_seconds,
                )
            )
        if (
            self._fallback_audio_url_resolver is not None
            and self._fallback_submission_selector is not None
            and self._fallback_submission_selector(context)
        ):
            try:
                fallback_url = self._fallback_audio_url_resolver(context)
            except DirectAudioUrlError as exc:
                return Rejected(
                    _failure(
                        kind=FailureKind.TERMINAL,
                        code=exc.code,
                        retryable=False,
                    )
                )
            except Exception:
                return Rejected(
                    _failure(
                        kind=FailureKind.TERMINAL,
                        code="aliyun_oss_fallback_prepare_failed",
                        retryable=False,
                    )
                )
            return self._submit_url(fallback_url, internal=True)
        try:
            direct_audio_url = self._direct_audio_url_resolver(context)
        except DirectAudioUrlError as exc:
            return Rejected(
                _failure(
                    kind=FailureKind.TERMINAL,
                    code=exc.code,
                    retryable=False,
                )
            )
        except Exception:
            return Rejected(
                _failure(
                    kind=FailureKind.TERMINAL,
                    code="aliyun_source_media_url_invalid",
                    retryable=False,
                )
            )
        return self._submit_url(direct_audio_url, internal=False)

    def can_fallback(self, outcome: TaskFailed) -> bool:
        if self._fallback_audio_url_resolver is None:
            return False
        if not outcome.release_unused_reservation:
            return False
        prefix = "aliyun_task_"
        code = outcome.failure.code
        if not code.startswith(prefix):
            return False
        try:
            status_code = int(code[len(prefix) :])
        except ValueError:
            return False
        return status_code in DOWNLOAD_FALLBACK_STATUS_CODES

    def cleanup_fallback(self, context: StageContext) -> None:
        cleanup = self._fallback_cleanup
        if cleanup is None:
            return
        try:
            cleanup(context)
        except Exception:
            return

    def _submit_url(self, audio_url: str, *, internal: bool) -> SubmitOutcome:
        try:
            result = (
                self._client.submit_internal(audio_url)
                if internal
                else self._client.submit(audio_url)
            )
        except AliyunAsrSubmissionUnknown as exc:
            return Unknown(
                _failure(
                    kind=FailureKind.REQUEST_UNKNOWN,
                    code=_error_code("submit", exc.code),
                    retryable=False,
                ),
                exc.task_id or None,
            )
        except AliyunAsrRejected as exc:
            return Rejected(
                _failure(
                    kind=(
                        FailureKind.TRANSIENT
                        if exc.retryable
                        else FailureKind.TERMINAL
                    ),
                    code=_error_code("submit", exc.code),
                    retryable=exc.retryable,
                    retry_after_seconds=(
                        self._config.asr_poll_interval_seconds
                        if exc.retryable
                        else None
                    ),
                )
            )
        except AliyunAsrError as exc:
            return Unknown(
                _failure(
                    kind=FailureKind.REQUEST_UNKNOWN,
                    code=_error_code("submit", exc.code),
                    retryable=False,
                )
            )
        return Accepted(result.task_id)

    def poll(
        self,
        *,
        task_id: str,
        identity: ExecutionIdentity,
        audio_duration_ms: int,
        reserved_cost_minor: int,
    ) -> PollOutcome:
        if not self.supports(identity):
            raise ValueError("persisted Aliyun ASR identity is unsupported")
        try:
            result = self._client.poll(task_id)
        except AliyunAsrPollError as exc:
            return Indeterminate(
                task_id,
                _failure(
                    kind=(
                        FailureKind.TRANSIENT
                        if exc.retryable
                        else FailureKind.PROTOCOL
                    ),
                    code=_error_code("poll", exc.code),
                    # A failed poll does not prove a paid task failed. Keep
                    # polling the same task until the frozen deadline.
                    retryable=True,
                    retry_after_seconds=self._config.asr_poll_interval_seconds,
                ),
                self._config.asr_poll_interval_seconds,
            )
        except AliyunAsrError as exc:
            return Indeterminate(
                task_id,
                _failure(
                    kind=FailureKind.PROTOCOL,
                    code=_error_code("poll", exc.code),
                    retryable=True,
                    retry_after_seconds=self._config.asr_poll_interval_seconds,
                ),
                self._config.asr_poll_interval_seconds,
            )
        return self._poll_result(
            result,
            audio_duration_ms=audio_duration_ms,
            reserved_cost_minor=reserved_cost_minor,
        )

    def _poll_result(
        self,
        result: AsrPollResult,
        *,
        audio_duration_ms: int,
        reserved_cost_minor: int,
    ) -> PollOutcome:
        usage = _usage(
            audio_duration_ms, reserved_cost_minor=reserved_cost_minor
        )
        if result.state in {AsrState.QUEUED, AsrState.RUNNING}:
            return Pending(
                result.task_id, self._config.asr_poll_interval_seconds
            )
        if result.state is AsrState.SUCCEEDED:
            if result.transcript is None:
                return TaskFailed(
                    result.task_id,
                    _failure(
                        kind=FailureKind.PROTOCOL,
                        code="aliyun_poll_missing_transcript",
                        retryable=False,
                    ),
                    usage,
                )
            try:
                document = _normalized_transcript(
                    result.transcript,
                    language=self._language,
                    source_media_duration_ms=audio_duration_ms,
                )
            except ValueError:
                return TaskFailed(
                    result.task_id,
                    _failure(
                        kind=FailureKind.PROTOCOL,
                        code="aliyun_poll_invalid_transcript",
                        retryable=False,
                    ),
                    usage,
                )
            return Succeeded(
                result.task_id,
                TextOutput(
                    document,
                    mime_type="application/json",
                    language=self._language,
                ),
                usage,
            )
        if result.state is AsrState.FAILED_RETRYABLE:
            return TaskFailed(
                result.task_id,
                _failure(
                    kind=FailureKind.TRANSIENT,
                    code=_error_code("task", result.status_code),
                    retryable=True,
                    retry_after_seconds=self._config.asr_poll_interval_seconds,
                ),
                usage,
            )
        if result.state in {AsrState.EMPTY, AsrState.FAILED_TERMINAL}:
            unprocessed = (
                result.state is AsrState.FAILED_TERMINAL
                and result.status_code in UNPROCESSED_AUDIO_STATUS_CODES
            )
            return TaskFailed(
                result.task_id,
                _failure(
                    kind=FailureKind.TERMINAL,
                    code=_error_code("task", result.status_code),
                    retryable=False,
                ),
                NormalizedUsage() if unprocessed else usage,
                release_unused_reservation=unprocessed,
            )
        return Indeterminate(
            result.task_id,
            _failure(
                kind=FailureKind.PROTOCOL,
                code="aliyun_poll_unknown_state",
                retryable=True,
                retry_after_seconds=self._config.asr_poll_interval_seconds,
            ),
            self._config.asr_poll_interval_seconds,
        )


@dataclass(frozen=True)
class AliyunIsiAsrWorkerBundle:
    client_factory: ClientFactory = AliyunIsiAsrClient
    clock: Clock = lambda: dt.datetime.now(dt.timezone.utc)
    artifact_store: PodcastArtifactStore | None = None
    storage_config: PodcastArtifactStorageConfig | None = None
    relay_factory: RelayFactory = _relay_factory
    # ISI file transcription does not return a detected language.  Persisting
    # every result as Chinese made English Podcast source transcripts look
    # translated when they were not.  Keep the provider-neutral unknown marker
    # until a dedicated language/translation stage resolves it.
    transcript_language: str = "und"

    def readiness(self, provider_config: object) -> bool:
        return aliyun_asr_worker_ready(provider_config)

    def __call__(
        self,
        session: Session,
        /,
        *,
        config: object,
        policy: object,
    ) -> AsrWorkerStep:
        if not isinstance(config, AsrWorkerConfig):
            raise TypeError("Aliyun ASR worker requires AsrWorkerConfig")
        if not isinstance(policy, PodcastStagePolicy):
            raise TypeError("Aliyun ASR worker requires PodcastStagePolicy")
        aliyun = policy.aliyun_isi
        if not aliyun_asr_worker_ready(aliyun):
            raise ValueError("Aliyun ASR worker cannot safely poll provider tasks")

        client = self.client_factory(aliyun)
        current = _utc(self.clock())
        relay = (
            self.relay_factory(aliyun, self.artifact_store, self.storage_config)
            if (
                aliyun.asr_oss_configured
                and self.artifact_store is not None
                and self.storage_config is not None
            )
            else None
        )

        def resolve_direct_audio_url(context: StageContext) -> str:
            try:
                return _direct_audio_url(session, context)
            finally:
                # Do not hold a database read transaction across provider I/O.
                session.rollback()

        def resolve_fallback_audio_url(context: StageContext) -> str:
            if relay is None:
                raise DirectAudioUrlError("aliyun_oss_fallback_unavailable")
            try:
                _direct_audio_url(session, context)
                episode = session.get(ArticleRecord, context.episode_id)
                snapshot = session.get(
                    PodcastSourceMediaSnapshotRecord,
                    context.input_artifact.artifact_id,
                )
                if episode is None or snapshot is None:
                    raise DirectAudioUrlError(
                        "aliyun_source_media_url_unavailable"
                    )
                enclosure = enclosure_snapshot(episode)
                session.expunge(snapshot)
                session.rollback()
                return relay.prepare(
                    context,
                    enclosure=enclosure,
                    expected=snapshot,
                )
            except DirectAudioUrlError:
                raise
            except (OssRelayError, SourceMediaError) as exc:
                code = getattr(exc, "code", "aliyun_oss_fallback_source_download_failed")
                raise DirectAudioUrlError(str(code)) from None
            finally:
                session.rollback()

        def should_submit_fallback(context: StageContext) -> bool:
            previous = session.exec(
                select(PodcastStageAttemptRecord).where(
                    PodcastStageAttemptRecord.processing_id
                    == context.processing_id,
                    PodcastStageAttemptRecord.attempt_no
                    == context.attempt_no - 1,
                )
            ).first()
            selected = bool(
                previous is not None
                and previous.submission_state == "failed_retryable"
                and previous.error_code == PROVIDER_FALLBACK_MARKER
            )
            session.rollback()
            return selected

        def cleanup_fallback(context: StageContext) -> None:
            if relay is not None:
                relay.delete(context)

        adapter = AliyunIsiAsrAdapter(
            aliyun,
            direct_audio_url_resolver=resolve_direct_audio_url,
            fallback_audio_url_resolver=(
                resolve_fallback_audio_url if relay is not None else None
            ),
            fallback_submission_selector=(
                should_submit_fallback if relay is not None else None
            ),
            fallback_cleanup=cleanup_fallback if relay is not None else None,
            client=client,
            clock=lambda: current,
            transcript_language=self.transcript_language,
        )
        planner = AliyunIsiAsrUsagePlanner(aliyun)
        try:
            return run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=planner,
                config=config,
                policy=policy,
                now=current,
            )
        finally:
            client.close()


def register_aliyun_isi_asr_worker(
    registry: _WorkerRegistry,
    *,
    bundle: AliyunIsiAsrWorkerBundle | None = None,
    admission_estimator: AliyunIsiAsrAdmissionEstimator | None = None,
) -> AliyunIsiAsrWorkerBundle:
    resolved = bundle or AliyunIsiAsrWorkerBundle()
    registry.register_worker_backed_target(
        "transcript",
        estimator=(
            admission_estimator
            or AliyunIsiAsrAdmissionEstimator(
                clock=resolved.clock,
            )
        ),
    )
    registry.register_stage_worker(
        "asr", resolved, readiness=resolved.readiness
    )
    return resolved


__all__ = [
    "AliyunIsiAsrAdapter",
    "AliyunIsiAsrAdmissionEstimator",
    "AliyunIsiAsrUsagePlanner",
    "AliyunIsiAsrWorkerBundle",
    "PROVIDER_NAME",
    "aliyun_asr_identity",
    "aliyun_asr_admission_fingerprint",
    "aliyun_asr_worker_ready",
    "register_aliyun_isi_asr_worker",
]
