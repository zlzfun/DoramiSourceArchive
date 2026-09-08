"""Provider-neutral ports for restart-safe Podcast ASR and TTS workers."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from services.podcast_worker_contracts import (
    ArtifactRef,
    ExecutionIdentity,
    ExecutionKind,
    PollOutcome,
    ProviderUsagePlan,
    StageContext,
    StagePlan,
    SubmitOutcome,
)


class ProviderPlanningUnavailable(ValueError):
    """Provider configuration/accounting cannot safely start an attempt."""


class AsrPlanningUnavailable(ProviderPlanningUnavailable):
    pass


class TtsPlanningUnavailable(ProviderPlanningUnavailable):
    pass


@dataclass(frozen=True)
class AsrProviderPlan:
    identity: ExecutionIdentity
    stage: StagePlan
    admission_fingerprint: str = ""
    required_input_lifetime_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionIdentity):
            raise ValueError("ASR identity must be an ExecutionIdentity")
        if self.identity.execution_kind is not ExecutionKind.PROVIDER:
            raise ValueError("ASR identity must use provider execution")
        if not isinstance(self.stage, StagePlan):
            raise ValueError("ASR stage must be a StagePlan")
        lifetime = self.required_input_lifetime_seconds
        if lifetime is None:
            lifetime = self.stage.deadline_seconds
            object.__setattr__(self, "required_input_lifetime_seconds", lifetime)
        if (
            isinstance(lifetime, bool)
            or not isinstance(lifetime, int)
            or lifetime < self.stage.deadline_seconds
        ):
            raise ValueError("ASR input lifetime must cover the provider deadline")
        _fingerprint(self.admission_fingerprint, required=False)


@dataclass(frozen=True)
class RemoteAudioDeliveryPolicy:
    """Non-secret network policy for fetching one provider audio result."""

    allowed_host_suffixes: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.allowed_host_suffixes, str):
            raise ValueError("remote audio host suffixes must be a tuple")
        suffixes = tuple(
            str(value or "").strip().lower().lstrip(".")
            for value in self.allowed_host_suffixes
        )
        if not suffixes or len(set(suffixes)) != len(suffixes):
            raise ValueError("remote audio host suffixes must be unique and nonempty")
        for suffix in suffixes:
            if len(suffix) > 253 or "." not in suffix or any(
                not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label
                )
                for label in suffix.split(".")
            ):
                raise ValueError("remote audio host suffix is invalid")
        object.__setattr__(self, "allowed_host_suffixes", suffixes)


@dataclass(frozen=True)
class TtsProviderPlan:
    identity: ExecutionIdentity
    stage: StagePlan
    usage: ProviderUsagePlan
    admission_fingerprint: str
    voice_profile_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionIdentity):
            raise ValueError("TTS identity must be an ExecutionIdentity")
        if self.identity.execution_kind is not ExecutionKind.PROVIDER:
            raise ValueError("TTS identity must use provider execution")
        if not isinstance(self.stage, StagePlan):
            raise ValueError("TTS stage must be a StagePlan")
        if not isinstance(self.usage, ProviderUsagePlan):
            raise ValueError("TTS usage must be a ProviderUsagePlan")
        if self.usage.estimated_cost_minor != self.stage.estimated_cost_minor:
            raise ValueError("TTS usage and stage estimates must match")
        if self.usage.deadline_seconds != self.stage.deadline_seconds:
            raise ValueError("TTS usage and stage deadlines must match")
        if not str(self.voice_profile_id or "").strip():
            raise ValueError("TTS voice profile is required")
        _fingerprint(self.admission_fingerprint, required=True)


def _fingerprint(value: str, *, required: bool) -> None:
    normalized = str(value or "")
    if not normalized and not required:
        return
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValueError("admission fingerprint must be lowercase SHA-256")


@runtime_checkable
class AsrProviderAdapter(Protocol):
    def plan(
        self,
        input_artifact: ArtifactRef,
        *,
        audio_duration_ms: int,
        now: dt.datetime,
    ) -> AsrProviderPlan: ...

    def submit(
        self, context: StageContext, *, provider_request_key: str
    ) -> SubmitOutcome: ...

    def supports(self, identity: ExecutionIdentity) -> bool: ...

    def poll(
        self,
        *,
        task_id: str,
        identity: ExecutionIdentity,
        audio_duration_ms: int,
        reserved_cost_minor: int,
    ) -> PollOutcome: ...


@runtime_checkable
class AsrUsagePlanner(Protocol):
    def plan_usage(
        self,
        *,
        identity: ExecutionIdentity,
        input_artifact: ArtifactRef,
        audio_duration_ms: int,
        now: dt.datetime,
    ) -> ProviderUsagePlan | None: ...


@runtime_checkable
class TtsProviderAdapter(Protocol):
    def plan(
        self,
        narration: str,
        *,
        voice_profile_id: str,
        now: dt.datetime,
    ) -> TtsProviderPlan: ...

    def remote_audio_policy(self) -> RemoteAudioDeliveryPolicy: ...

    def submit(
        self,
        context: StageContext,
        *,
        narration: str,
        voice_profile_id: str,
        provider_request_key: str,
    ) -> SubmitOutcome: ...

    def supports(self, identity: ExecutionIdentity) -> bool: ...

    def poll(
        self,
        *,
        task_id: str,
        identity: ExecutionIdentity,
        voice_profile_id: str,
        reserved_usage_units: int,
        reserved_cost_minor: int,
    ) -> PollOutcome: ...


__all__ = [
    "AsrPlanningUnavailable",
    "AsrProviderAdapter",
    "AsrProviderPlan",
    "AsrUsagePlanner",
    "ProviderPlanningUnavailable",
    "RemoteAudioDeliveryPolicy",
    "TtsPlanningUnavailable",
    "TtsProviderAdapter",
    "TtsProviderPlan",
]
