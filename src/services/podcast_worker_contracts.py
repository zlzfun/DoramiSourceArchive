"""Provider-neutral value contracts for Podcast stage workers.

The durable state machine owns attempt transitions; provider adapters own wire
formats.  This module is the intentionally small seam between them.  It must
not import a provider implementation or carry credentials in settings.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, TypeAlias
from urllib.parse import urlsplit


_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
_KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MIME_RE = re.compile(
    r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", re.IGNORECASE
)
_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
_FORBIDDEN_SETTING_KEY_PARTS = frozenset(
    {
        "address",
        "authorization",
        "credential",
        "credentials",
        "endpoint",
        "key",
        "password",
        "secret",
        "signature",
        "token",
        "uri",
        "url",
    }
)


def _required_text(value: str, field_name: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum} characters")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _kind(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name, maximum=64).lower()
    if not _KIND_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name, maximum=64).lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return normalized


def _mime_type(value: str) -> str:
    normalized = _required_text(value, "mime_type", maximum=127).lower()
    if not _MIME_RE.fullmatch(normalized):
        raise ValueError("mime_type must be a media type without parameters")
    return normalized


def _nonnegative_integer(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return value


def _positive_integer(value: int, field_name: str) -> int:
    normalized = _nonnegative_integer(value, field_name)
    if normalized == 0:
        raise ValueError(f"{field_name} must be positive")
    return normalized


def _setting_key_parts(key: str) -> set[str]:
    # Split camelCase as well as snake/kebab/dotted keys.  A field such as
    # ``tokenizer`` remains valid while ``accessToken`` and ``result_url`` do not.
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return {
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", with_boundaries)
        if part
    }


def _normalize_json(value: Any, *, path: str = "settings") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be nonempty strings")
            if _setting_key_parts(key) & _FORBIDDEN_SETTING_KEY_PARTS:
                raise ValueError(f"{path} contains forbidden sensitive field '{key}'")
            normalized[key] = _normalize_json(item, path=f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} contains a non-JSON value")


class ExecutionKind(str, Enum):
    PROVIDER = "provider"
    LOCAL = "local"


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    TERMINAL = "terminal"
    REQUEST_UNKNOWN = "request_unknown"
    PROTOCOL = "protocol"
    TIMEOUT = "timeout"


class ProviderUsageUnit(str, Enum):
    AUDIO_SECONDS = "audio_seconds"
    TTS_CHARACTERS = "tts_characters"


def canonical_settings_fingerprint(
    *,
    execution_kind: ExecutionKind,
    provider: str,
    model: str,
    revision: str,
    settings: Mapping[str, Any],
) -> str:
    """Hash effective execution settings without accepting secret material.

    Provider/model/revision are part of the envelope.  A display alias can stay
    stable while a model or an effective setting changes and the resulting
    identity will still change.
    """

    if not isinstance(execution_kind, ExecutionKind):
        raise ValueError("execution_kind must be an ExecutionKind")
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be a JSON object")
    envelope = {
        "execution_kind": execution_kind.value,
        "model": _identifier(model, "model"),
        "provider": _identifier(provider, "provider"),
        "revision": _identifier(revision, "revision"),
        "settings": _normalize_json(settings),
    }
    canonical = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    episode_id: str
    kind: str
    content_hash: str
    size_bytes: int
    mime_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _identifier(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "episode_id", _identifier(self.episode_id, "episode_id"))
        object.__setattr__(self, "kind", _kind(self.kind, "kind"))
        object.__setattr__(
            self, "content_hash", _sha256(self.content_hash, "content_hash")
        )
        object.__setattr__(self, "size_bytes", _nonnegative_integer(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "mime_type", _mime_type(self.mime_type))


@dataclass(frozen=True)
class ExecutionIdentity:
    execution_kind: ExecutionKind
    provider: str
    model: str
    revision: str
    settings_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.execution_kind, ExecutionKind):
            raise ValueError("execution_kind must be an ExecutionKind")
        object.__setattr__(self, "provider", _identifier(self.provider, "provider"))
        object.__setattr__(self, "model", _identifier(self.model, "model"))
        object.__setattr__(self, "revision", _identifier(self.revision, "revision"))
        object.__setattr__(
            self,
            "settings_fingerprint",
            _sha256(self.settings_fingerprint, "settings_fingerprint"),
        )

    @classmethod
    def from_settings(
        cls,
        *,
        execution_kind: ExecutionKind,
        provider: str,
        model: str,
        revision: str,
        settings: Mapping[str, Any],
    ) -> "ExecutionIdentity":
        return cls(
            execution_kind=execution_kind,
            provider=provider,
            model=model,
            revision=revision,
            settings_fingerprint=canonical_settings_fingerprint(
                execution_kind=execution_kind,
                provider=provider,
                model=model,
                revision=revision,
                settings=settings,
            ),
        )


@dataclass(frozen=True)
class StagePlan:
    estimated_cost_minor: int
    poll_interval_seconds: int
    deadline_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "estimated_cost_minor",
            _nonnegative_integer(self.estimated_cost_minor, "estimated_cost_minor"),
        )
        object.__setattr__(
            self,
            "poll_interval_seconds",
            _positive_integer(self.poll_interval_seconds, "poll_interval_seconds"),
        )
        object.__setattr__(
            self,
            "deadline_seconds",
            _positive_integer(self.deadline_seconds, "deadline_seconds"),
        )
        if self.deadline_seconds < self.poll_interval_seconds:
            raise ValueError("deadline_seconds must cover at least one poll interval")


@dataclass(frozen=True)
class ProviderUsagePlan:
    """Immutable provider entitlement and integer pricing snapshot for one call."""

    quota_scope: str
    quota_period: str
    unit: ProviderUsageUnit
    window_start_at: dt.datetime
    window_end_at: dt.datetime
    limit_units: int
    reserved_units: int
    unit_price_cny_minor: int
    price_unit_count: int
    pricing_revision: str
    deadline_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "quota_scope", _identifier(self.quota_scope, "quota_scope"))
        object.__setattr__(self, "quota_period", _identifier(self.quota_period, "quota_period"))
        if not isinstance(self.unit, ProviderUsageUnit):
            raise ValueError("unit must be a ProviderUsageUnit")
        for field_name in ("window_start_at", "window_end_at"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, dt.datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must be timezone-aware")
            object.__setattr__(self, field_name, value.astimezone(dt.timezone.utc))
        if self.window_start_at >= self.window_end_at:
            raise ValueError("provider usage window must be nonempty")
        object.__setattr__(self, "limit_units", _positive_integer(self.limit_units, "limit_units"))
        object.__setattr__(
            self, "reserved_units", _positive_integer(self.reserved_units, "reserved_units")
        )
        if self.reserved_units > self.limit_units:
            raise ValueError("reserved_units cannot exceed limit_units")
        object.__setattr__(
            self,
            "unit_price_cny_minor",
            _nonnegative_integer(self.unit_price_cny_minor, "unit_price_cny_minor"),
        )
        object.__setattr__(
            self, "price_unit_count", _positive_integer(self.price_unit_count, "price_unit_count")
        )
        object.__setattr__(
            self, "pricing_revision", _identifier(self.pricing_revision, "pricing_revision")
        )
        object.__setattr__(
            self, "deadline_seconds", _positive_integer(self.deadline_seconds, "deadline_seconds")
        )

    @property
    def estimated_cost_minor(self) -> int:
        numerator = self.reserved_units * self.unit_price_cny_minor
        return (numerator + self.price_unit_count - 1) // self.price_unit_count

    def actual_cost_minor(self, actual_units: int) -> int:
        actual = _nonnegative_integer(actual_units, "actual_units")
        numerator = actual * self.unit_price_cny_minor
        return (numerator + self.price_unit_count - 1) // self.price_unit_count


@dataclass(frozen=True)
class StageContext:
    processing_id: str
    episode_id: str
    target: str
    stage: str
    attempt_id: str
    attempt_no: int
    fencing_token: int
    input_artifact: ArtifactRef
    identity: ExecutionIdentity
    plan: StagePlan

    def __post_init__(self) -> None:
        object.__setattr__(self, "processing_id", _identifier(self.processing_id, "processing_id"))
        object.__setattr__(self, "episode_id", _identifier(self.episode_id, "episode_id"))
        object.__setattr__(self, "target", _kind(self.target, "target"))
        object.__setattr__(self, "stage", _kind(self.stage, "stage"))
        object.__setattr__(self, "attempt_id", _identifier(self.attempt_id, "attempt_id"))
        object.__setattr__(self, "attempt_no", _positive_integer(self.attempt_no, "attempt_no"))
        object.__setattr__(
            self, "fencing_token", _positive_integer(self.fencing_token, "fencing_token")
        )
        if not isinstance(self.input_artifact, ArtifactRef):
            raise ValueError("input_artifact must be an ArtifactRef")
        if self.input_artifact.episode_id != self.episode_id:
            raise ValueError("input_artifact episode_id must match the stage context")
        if not isinstance(self.identity, ExecutionIdentity):
            raise ValueError("identity must be an ExecutionIdentity")
        if not isinstance(self.plan, StagePlan):
            raise ValueError("plan must be a StagePlan")
        if (
            self.identity.execution_kind is ExecutionKind.LOCAL
            and self.plan.estimated_cost_minor != 0
        ):
            raise ValueError("local execution estimated_cost_minor must be zero")


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    code: str
    message: str = field(repr=False)
    retryable: bool
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FailureKind):
            raise ValueError("kind must be a FailureKind")
        object.__setattr__(self, "code", _identifier(self.code, "code"))
        object.__setattr__(
            self, "message", _required_text(self.message, "message", maximum=1000)
        )
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be boolean")
        if self.retry_after_seconds is not None:
            object.__setattr__(
                self,
                "retry_after_seconds",
                _positive_integer(self.retry_after_seconds, "retry_after_seconds"),
            )
            if not self.retryable:
                raise ValueError("retry_after_seconds requires a retryable failure")


@dataclass(frozen=True)
class NormalizedUsage:
    cost_minor: int = 0
    currency: str = "CNY"
    audio_duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    audio_tokens: int = 0
    tts_characters: int = 0
    input_bytes: int = 0
    output_bytes: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "cost_minor",
            "audio_duration_ms",
            "input_tokens",
            "output_tokens",
            "audio_tokens",
            "tts_characters",
            "input_bytes",
            "output_bytes",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_integer(getattr(self, field_name), field_name),
            )
        if not isinstance(self.currency, str) or not re.fullmatch(
            r"[A-Z]{3}", self.currency
        ):
            raise ValueError("currency must be an uppercase ISO 4217 code")


@dataclass(frozen=True)
class RemoteAudioOutput:
    download_url: str = field(repr=False)
    mime_type: str
    expected_size_bytes: int | None = None
    expected_content_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.download_url, str):
            raise ValueError("download_url must be a string")
        url = self.download_url.strip()
        if any(character.isspace() or ord(character) < 32 for character in url):
            raise ValueError("download_url must be an HTTPS URL")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ValueError("download_url must be an HTTPS URL") from None
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port == 0
        ):
            raise ValueError("download_url must be an HTTPS URL")
        object.__setattr__(self, "download_url", url)
        object.__setattr__(self, "mime_type", _mime_type(self.mime_type))
        if self.expected_size_bytes is not None:
            object.__setattr__(
                self,
                "expected_size_bytes",
                _nonnegative_integer(self.expected_size_bytes, "expected_size_bytes"),
            )
        if self.expected_content_hash is not None:
            object.__setattr__(
                self,
                "expected_content_hash",
                _sha256(self.expected_content_hash, "expected_content_hash"),
            )


@dataclass(frozen=True)
class TextOutput:
    text: str = field(repr=False)
    mime_type: str = "text/plain"
    language: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        object.__setattr__(self, "mime_type", _mime_type(self.mime_type))
        if not self.mime_type.startswith("text/") and self.mime_type != "application/json":
            raise ValueError("TextOutput mime_type must be text/* or application/json")
        if self.mime_type == "application/json":
            try:
                json.loads(self.text)
            except (TypeError, ValueError):
                raise ValueError("application/json TextOutput must contain valid JSON") from None
        if self.language is not None:
            language = _required_text(self.language, "language", maximum=35)
            if not _LANGUAGE_RE.fullmatch(language):
                raise ValueError("language must be a BCP 47 language tag")
            object.__setattr__(self, "language", language.lower())


ProviderOutput: TypeAlias = RemoteAudioOutput | TextOutput


@dataclass(frozen=True)
class MaterializedOutput:
    artifact: ArtifactRef
    usage: NormalizedUsage

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            raise ValueError("artifact must be an ArtifactRef")
        if not isinstance(self.usage, NormalizedUsage):
            raise ValueError("usage must be NormalizedUsage")
        if self.usage.output_bytes and self.usage.output_bytes != self.artifact.size_bytes:
            raise ValueError("usage output_bytes must match the materialized artifact size")


@dataclass(frozen=True)
class Accepted:
    task_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))


@dataclass(frozen=True)
class Unknown:
    failure: Failure
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.failure, Failure):
            raise ValueError("failure must be a Failure")
        if self.failure.kind is not FailureKind.REQUEST_UNKNOWN:
            raise ValueError("Unknown requires a request_unknown failure")
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))


@dataclass(frozen=True)
class Rejected:
    failure: Failure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, Failure):
            raise ValueError("failure must be a Failure")
        if self.failure.kind is FailureKind.REQUEST_UNKNOWN:
            raise ValueError("Rejected cannot carry a request_unknown failure")


SubmitOutcome: TypeAlias = Accepted | Unknown | Rejected


@dataclass(frozen=True)
class Pending:
    task_id: str
    retry_after_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "retry_after_seconds",
            _positive_integer(self.retry_after_seconds, "retry_after_seconds"),
        )


@dataclass(frozen=True)
class Succeeded:
    task_id: str
    output: ProviderOutput
    usage: NormalizedUsage

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        if not isinstance(self.output, (RemoteAudioOutput, TextOutput)):
            raise ValueError("output must be a provider output")
        if not isinstance(self.usage, NormalizedUsage):
            raise ValueError("usage must be NormalizedUsage")


@dataclass(frozen=True)
class TaskFailed:
    task_id: str
    failure: Failure
    usage: NormalizedUsage = field(default_factory=NormalizedUsage)
    release_unused_reservation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        if not isinstance(self.failure, Failure):
            raise ValueError("failure must be a Failure")
        if self.failure.kind is FailureKind.REQUEST_UNKNOWN:
            raise ValueError("TaskFailed cannot carry a request_unknown failure")
        if not isinstance(self.usage, NormalizedUsage):
            raise ValueError("usage must be NormalizedUsage")
        if not isinstance(self.release_unused_reservation, bool):
            raise ValueError("release_unused_reservation must be boolean")
        if self.release_unused_reservation:
            if self.failure.retryable or self.failure.kind is not FailureKind.TERMINAL:
                raise ValueError(
                    "unused reservation release requires a terminal failure"
                )
            if self.usage != NormalizedUsage():
                raise ValueError(
                    "unused reservation release requires zero normalized usage"
                )


@dataclass(frozen=True)
class Indeterminate:
    task_id: str
    failure: Failure
    retry_after_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        if not isinstance(self.failure, Failure):
            raise ValueError("failure must be a Failure")
        if not self.failure.retryable:
            raise ValueError("Indeterminate requires a retryable failure")
        object.__setattr__(
            self,
            "retry_after_seconds",
            _positive_integer(self.retry_after_seconds, "retry_after_seconds"),
        )


PollOutcome: TypeAlias = Pending | Succeeded | TaskFailed | Indeterminate


__all__ = [
    "Accepted",
    "ArtifactRef",
    "ExecutionIdentity",
    "ExecutionKind",
    "Failure",
    "FailureKind",
    "Indeterminate",
    "MaterializedOutput",
    "NormalizedUsage",
    "Pending",
    "PollOutcome",
    "ProviderOutput",
    "Rejected",
    "RemoteAudioOutput",
    "StageContext",
    "StagePlan",
    "SubmitOutcome",
    "Succeeded",
    "TaskFailed",
    "TextOutput",
    "Unknown",
    "canonical_settings_fingerprint",
]
