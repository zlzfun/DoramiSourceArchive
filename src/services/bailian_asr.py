"""Fun-ASR adapter for the existing fenced, durable ASR worker."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from config_bailian import BailianSpeechConfig
from services.bailian_speech_client import (
    BailianSpeechClient,
    BailianSpeechError,
    download_result,
    task_id as valid_task_id,
)
from services.podcast_provider_ports import AsrProviderPlan, AsrPlanningUnavailable
from services.podcast_worker_contracts import (
    Accepted,
    Unknown,
    Rejected,
    Pending,
    Succeeded,
    TaskFailed,
    Indeterminate,
    ExecutionIdentity,
    ExecutionKind,
    StagePlan,
    ProviderUsagePlan,
    ProviderUsageUnit,
    NormalizedUsage,
    TextOutput,
    Failure,
    FailureKind,
)
from services.podcast_processing_admin import AdmissionEstimate
from services.podcast_normalized_transcripts import canonical_normalized_transcript

PROVIDER = "aliyun-bailian"


def usage_plan(config, *, audio_duration_ms, now):
    if (
        not config.asr_accounting_ready
        or type(audio_duration_ms) is not int
        or audio_duration_ms <= 0
    ):
        raise AsrPlanningUnavailable("Bailian ASR accounting is unavailable")
    if audio_duration_ms > config.asr_max_audio_seconds_per_file * 1000:
        raise AsrPlanningUnavailable(
            "Bailian ASR source exceeds the file duration limit"
        )
    ends = dt.datetime.fromisoformat(
        config.asr_entitlement_ends_at.replace("Z", "+00:00")
    )
    if now.tzinfo is None or ends.tzinfo is None or now >= ends:
        raise AsrPlanningUnavailable("Bailian ASR spending authorization expired")
    local = now.astimezone(ZoneInfo(config.asr_quota_timezone))
    starts = dt.datetime.combine(local.date(), dt.time.min, tzinfo=local.tzinfo)
    return ProviderUsagePlan(
        quota_scope=config.asr_quota_scope,
        quota_period=local.date().isoformat(),
        unit=ProviderUsageUnit.AUDIO_SECONDS,
        window_start_at=starts,
        window_end_at=min(starts + dt.timedelta(days=1), ends),
        limit_units=config.asr_daily_audio_seconds_limit,
        reserved_units=(audio_duration_ms + 999) // 1000,
        unit_price_cny_minor=config.asr_price_minor,
        price_unit_count=config.asr_price_units,
        pricing_revision=config.pricing_revision,
        deadline_seconds=config.asr_provider_deadline_seconds,
        minimum_units=0,
    )


def asr_identity(config):
    return ExecutionIdentity.from_settings(
        execution_kind=ExecutionKind.PROVIDER,
        provider=PROVIDER,
        model=config.asr_model,
        revision="dashscope-filetrans-v1",
        settings={
            "api_host": urlsplit(config.base_url).hostname,
            "account_scope": config.account_scope,
            "channel_id": [0],
            "price_minor": config.asr_price_minor,
            "price_units": config.asr_price_units,
            "pricing_revision": config.pricing_revision,
        },
    )


def admission_fingerprint(config):
    payload = {
        k: v
        for k, v in asdict(config).items()
        if k.startswith("asr_")
        or k
        in {"base_url", "account_scope", "request_timeout_seconds", "pricing_revision"}
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def failure(code, *, unknown=False, retryable=False):
    return Failure(
        kind=(
            FailureKind.REQUEST_UNKNOWN
            if unknown
            else FailureKind.TRANSIENT if retryable else FailureKind.TERMINAL
        ),
        code="bailian_" + code,
        message="Bailian speech operation did not complete",
        retryable=retryable,
    )


def normalize_transcript(payload):
    segments = []
    for transcript in payload["transcripts"]:
        if transcript["channel_id"] != 0:
            raise ValueError("Unexpected billed audio channel")
        for s in transcript["sentences"]:
            segments.append(
                {
                    "text": s["text"],
                    "start_ms": s["begin_time"],
                    "end_ms": s["end_time"],
                    "channel": 0,
                    "words": [
                        {
                            "text": w["text"] + w.get("punctuation", ""),
                            "start_ms": w["begin_time"],
                            "end_ms": w["end_time"],
                            "channel": 0,
                        }
                        for w in s.get("words", [])
                    ],
                }
            )
    return canonical_normalized_transcript(
        {
            "audio_duration_ms": payload["properties"][
                "original_duration_in_milliseconds"
            ],
            "language": "und",
            "text": "\n".join(s["text"] for s in segments),
            "segments": segments,
        }
    )


class BailianAsrAdapter:
    def __init__(
        self, config, *, url_resolver, client=None, downloader=download_result
    ):
        self.config, self.url_resolver = config, url_resolver
        self.client = client or BailianSpeechClient(config)
        self.downloader = downloader

    def plan(self, input_artifact, *, audio_duration_ms, now):
        plan = self.plan_usage(
            identity=asr_identity(self.config),
            input_artifact=input_artifact,
            audio_duration_ms=audio_duration_ms,
            now=now,
        )
        return AsrProviderPlan(
            asr_identity(self.config),
            StagePlan(
                estimated_cost_minor=plan.estimated_cost_minor,
                poll_interval_seconds=self.config.asr_poll_interval_seconds,
                deadline_seconds=plan.deadline_seconds,
            ),
            admission_fingerprint(self.config),
        )

    def plan_usage(
        self, *, identity: ExecutionIdentity, input_artifact, audio_duration_ms, now
    ):
        if (
            identity != asr_identity(self.config)
            or input_artifact.kind != "source_media_snapshot"
        ):
            raise AsrPlanningUnavailable("Bailian ASR input identity changed")
        return usage_plan(self.config, audio_duration_ms=audio_duration_ms, now=now)

    def supports(self, persisted):
        # Includes account/endpoint/price semantics, but deliberately excludes the
        # secret: rotating a key within the same account safely resumes polling.
        return persisted == asr_identity(self.config)

    def submit(self, context, *, provider_request_key):
        # DashScope does not promise idempotency for this key. Never send it.
        if context.identity != asr_identity(self.config):
            return Rejected(failure("identity_mismatch"))
        try:
            url = self.url_resolver(context)
        except Exception:
            return Rejected(failure("source_binding_invalid"))
        try:
            result = self.client.submit_asr(url)
            output = result["output"]
            task = valid_task_id(output["task_id"])
            if output["task_status"] not in {"PENDING", "RUNNING", "SUCCEEDED"}:
                return Unknown(failure("submit_state_unknown", unknown=True), task)
            return Accepted(task)
        except BailianSpeechError as exc:
            return (
                Unknown(failure(exc.code, unknown=True))
                if exc.unknown
                else Rejected(failure(exc.code))
            )
        except (KeyError, TypeError, ValueError):
            return Unknown(failure("invalid_submit_response", unknown=True))

    def poll(self, *, task_id, identity, audio_duration_ms, reserved_cost_minor):
        if not self.supports(identity):
            raise ValueError("Bailian ASR identity mismatch")
        try:
            payload = self.client.poll_asr(task_id)
            output = payload["output"]
            if output["task_id"] != task_id:
                raise BailianSpeechError("task_identity_mismatch")
            status = output["task_status"]
            if status in {"PENDING", "RUNNING"}:
                return Pending(task_id, self.config.asr_poll_interval_seconds)
            if status in {"FAILED", "CANCELED"}:
                return TaskFailed(
                    task_id,
                    failure("task_failed"),
                    NormalizedUsage(
                        cost_minor=reserved_cost_minor,
                        audio_duration_ms=audio_duration_ms,
                    ),
                )
            if status != "SUCCEEDED":
                raise BailianSpeechError("unknown_task_state")
            results = output["results"]
            if len(results) != 1:
                raise BailianSpeechError("unexpected_result_count")
            item = results[0]
            if item["subtask_status"] != "SUCCEEDED":
                return TaskFailed(
                    task_id,
                    failure("subtask_failed"),
                    NormalizedUsage(
                        cost_minor=reserved_cost_minor,
                        audio_duration_ms=audio_duration_ms,
                    ),
                )
            duration = Decimal(str(payload["usage"]["duration"]))
            if (
                not duration.is_finite()
                or duration <= 0
                or duration > Decimal((audio_duration_ms + 999) // 1000)
            ):
                raise BailianSpeechError("invalid_billed_duration")
            units = int(duration.to_integral_value(rounding=ROUND_CEILING))
            price = (
                units * self.config.asr_price_minor + self.config.asr_price_units - 1
            ) // self.config.asr_price_units
            document = asyncio.run(
                self.downloader(
                    item["transcription_url"], self.config, max_bytes=32 * 1024 * 1024
                )
            )
            normalized = normalize_transcript(json.loads(document))
            return Succeeded(
                task_id,
                TextOutput(normalized, mime_type="application/json", language="und"),
                NormalizedUsage(
                    cost_minor=price,
                    audio_duration_ms=audio_duration_ms,
                    billed_audio_duration_ms=int(
                        (duration * 1000).to_integral_value(rounding=ROUND_CEILING)
                    ),
                ),
            )
        except Exception:
            # Failed polls/downloads never justify another paid submission.
            return Indeterminate(
                task_id,
                failure("poll_incomplete", retryable=True),
                self.config.asr_poll_interval_seconds,
            )


def worker_ready(config):
    return isinstance(config, BailianSpeechConfig) and config.asr_poll_configured


def admission_ready(config):
    return worker_ready(config) and config.asr_accounting_ready


@dataclass(frozen=True)
class BailianAsrAdmissionEstimator:
    def __call__(self, session, input_metadata, podcast_config):
        from services.bailian_speech_config import resolve_bailian

        config = resolve_bailian(session)
        if not admission_ready(config):
            raise AsrPlanningUnavailable("Bailian ASR is not configured")
        plan = usage_plan(
            config,
            audio_duration_ms=input_metadata.get("audio_duration_ms"),
            now=dt.datetime.now(dt.timezone.utc),
        )
        return AdmissionEstimate(
            cost_minor=plan.estimated_cost_minor,
            admission_fingerprint=admission_fingerprint(config),
        )


@dataclass(frozen=True)
class BailianAsrWorkerBundle:
    client_factory: object = BailianSpeechClient
    downloader: object = download_result
    clock: object = lambda: dt.datetime.now(dt.timezone.utc)

    def __call__(self, session, /, *, config, policy):
        from services.podcast_asr_worker import run_asr_worker_step
        from services.aliyun_isi_asr_worker import _direct_audio_url

        # This existing helper validates immutable source/episode/permission
        # bindings. No ISI API or credentials are used here.
        def url_resolver(context):
            try:
                return _direct_audio_url(session, context)
            finally:
                session.rollback()

        client = self.client_factory(policy.bailian_speech)
        adapter = BailianAsrAdapter(
            policy.bailian_speech,
            url_resolver=url_resolver,
            client=client,
            downloader=self.downloader,
        )
        try:
            return run_asr_worker_step(
                session,
                adapter=adapter,
                usage_planner=adapter,
                config=config,
                policy=policy,
                now=self.clock(),
            )
        finally:
            client.close()


def register_bailian_asr_worker(registry, *, estimator=None):
    registry.register_worker_backed_target(
        "transcript", estimator=estimator or BailianAsrAdmissionEstimator()
    )
    registry.register_stage_worker(
        "asr",
        BailianAsrWorkerBundle(),
        readiness=worker_ready,
        admission_readiness=admission_ready,
    )
