"""Aliyun ISI asynchronous long-text TTS wire adapter.

The adapter validates logical voice profiles, submits one non-idempotent TTS
request, and polls an existing provider task.  It never retries a submit and
never downloads ``audio_address``; the durable worker must persist the task ID
before fetching the result into the local content-addressed store.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from config import AliyunIsiConfig
from services.aliyun_isi_auth import AliyunIsiError, NlsTokenManager, NlsTokenStore
from services.http_safety import install_http_query_redaction_filters


SUCCESS_CODE = 20_000_000
RUNNING_CODE = 21_050_001
QUEUEING_CODE = 21_050_002
AUTH_FAILED_CODE = 40_000_001
THROTTLED_CODE = 40_000_005
IDLE_TIMEOUT_CODE = 40_000_004
MAX_VOICE_PROFILES = 32
SUPPORTED_FORMATS = frozenset({"wav", "mp3"})
SUPPORTED_SAMPLE_RATES = frozenset({8_000, 16_000})

install_http_query_redaction_filters()


class TtsState(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"


class AliyunTtsError(AliyunIsiError):
    def __init__(self, operation: str, code: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(f"Aliyun ISI TTS failed (operation={operation}, code={code})")


class AliyunTtsSubmissionUnknown(AliyunTtsError):
    """The caller cannot prove whether a billed synthesis task was created."""

    def __init__(self, code: str, *, task_id: str = "") -> None:
        self.task_id = task_id
        super().__init__("submit", code)


class AliyunTtsRejected(AliyunTtsError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("submit", code)


class AliyunTtsPollError(AliyunTtsError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("poll", code)


def _plain_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or (isinstance(value, str) and value.isdigit())
    ):
        raise ValueError(f"Aliyun ISI TTS {field_name} must be an integer")
    return int(value)


@dataclass(frozen=True)
class AliyunTtsVoiceProfile:
    alias: str
    voice: str
    format: str
    sample_rate: int
    volume: int
    speech_rate: int
    pitch_rate: int
    enable_subtitle: bool

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", self.alias):
            raise ValueError("Aliyun ISI TTS voice profile alias is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", self.voice):
            raise ValueError("Aliyun ISI TTS provider voice is invalid")
        if self.format not in SUPPORTED_FORMATS:
            raise ValueError("Aliyun ISI TTS format is unsupported")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (
                self.sample_rate,
                self.volume,
                self.speech_rate,
                self.pitch_rate,
            )
        ):
            raise ValueError("Aliyun ISI TTS numeric voice settings must be integers")
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("Aliyun ISI TTS sample_rate is unsupported")
        if not 0 <= self.volume <= 100:
            raise ValueError("Aliyun ISI TTS volume must be between 0 and 100")
        if not -500 <= self.speech_rate <= 500:
            raise ValueError("Aliyun ISI TTS speech_rate must be between -500 and 500")
        if not -500 <= self.pitch_rate <= 500:
            raise ValueError("Aliyun ISI TTS pitch_rate must be between -500 and 500")
        if not isinstance(self.enable_subtitle, bool):
            raise ValueError("Aliyun ISI TTS enable_subtitle must be boolean")

    @property
    def revision(self) -> str:
        canonical = json.dumps(
            self.request_settings(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def request_settings(self) -> dict[str, Any]:
        return {
            "enable_subtitle": self.enable_subtitle,
            "format": self.format,
            "pitch_rate": self.pitch_rate,
            "sample_rate": self.sample_rate,
            "speech_rate": self.speech_rate,
            "voice": self.voice,
            "volume": self.volume,
        }


def parse_voice_profiles(raw_json: str) -> dict[str, AliyunTtsVoiceProfile]:
    raw = str(raw_json or "").strip()
    if not raw:
        return {}

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate key")
            parsed[key] = value
        return parsed

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (TypeError, ValueError):
        raise ValueError("Aliyun ISI TTS voice profiles must be valid JSON") from None
    if not isinstance(payload, dict) or len(payload) > MAX_VOICE_PROFILES:
        raise ValueError("Aliyun ISI TTS voice profiles must be a bounded object")
    required = {
        "voice",
        "format",
        "sample_rate",
        "volume",
        "speech_rate",
        "pitch_rate",
        "enable_subtitle",
    }
    profiles: dict[str, AliyunTtsVoiceProfile] = {}
    for raw_alias, settings in payload.items():
        alias = raw_alias.strip() if isinstance(raw_alias, str) else ""
        if not isinstance(settings, dict) or set(settings) != required:
            raise ValueError(
                "Aliyun ISI TTS voice profile fields are incomplete or unknown"
            )
        enable_subtitle = settings["enable_subtitle"]
        if not isinstance(enable_subtitle, bool):
            raise ValueError("Aliyun ISI TTS enable_subtitle must be boolean")
        if not isinstance(settings["voice"], str) or not isinstance(
            settings["format"], str
        ):
            raise ValueError("Aliyun ISI TTS voice and format must be strings")
        profile = AliyunTtsVoiceProfile(
            alias=alias,
            voice=settings["voice"].strip(),
            format=settings["format"].strip().lower(),
            sample_rate=_plain_integer(settings["sample_rate"], "sample_rate"),
            volume=_plain_integer(settings["volume"], "volume"),
            speech_rate=_plain_integer(settings["speech_rate"], "speech_rate"),
            pitch_rate=_plain_integer(settings["pitch_rate"], "pitch_rate"),
            enable_subtitle=enable_subtitle,
        )
        if alias in profiles:
            raise ValueError("Aliyun ISI TTS voice profile aliases must be unique")
        profiles[alias] = profile
    return profiles


@dataclass(frozen=True)
class TtsSubmission:
    task_id: str
    error_code: int
    voice_profile: str
    settings_revision: str


@dataclass(frozen=True)
class TtsSubtitle:
    begin_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class TtsPollResult:
    task_id: str
    state: TtsState
    error_code: int
    audio_url: str = field(default="", repr=False)
    subtitles: tuple[TtsSubtitle, ...] = ()


def _response_dict(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise AliyunTtsError("parse", "invalid_json") from None
    if not isinstance(payload, dict):
        raise AliyunTtsError("parse", "invalid_object")
    return payload


def _business_code(payload: Mapping[str, Any]) -> int:
    try:
        code = _plain_integer(payload.get("error_code"), "error_code")
    except ValueError:
        raise AliyunTtsError("parse", "invalid_error_code") from None
    if code < 0:
        raise AliyunTtsError("parse", "invalid_error_code")
    return code


def _safe_task_id(value: Any) -> str:
    if not isinstance(value, str):
        raise AliyunTtsError("parse", "invalid_task_id")
    task_id = value.strip()
    if not task_id or len(task_id) > 200:
        raise AliyunTtsError("parse", "invalid_task_id")
    return task_id


def _optional_task_id(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    try:
        return _safe_task_id(data.get("task_id"))
    except AliyunTtsError:
        return ""


def _is_provider_server_error(code: int) -> bool:
    return 50_000_000 <= code < 60_000_000


def _is_retryable(code: int) -> bool:
    return code in {IDLE_TIMEOUT_CODE, THROTTLED_CODE} or _is_provider_server_error(
        code
    )


def _response_status_is_success(payload: Mapping[str, Any]) -> bool:
    try:
        return _plain_integer(payload.get("status"), "status") == 200
    except ValueError:
        return False


def validate_tts_text(value: str, *, max_chars: int) -> str:
    """Validate the exact plain-text value sent to the provider."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Aliyun ISI TTS text cannot be empty")
    if len(value) > max_chars:
        raise ValueError("Aliyun ISI TTS text exceeds the configured character limit")
    if "\x00" in value:
        raise ValueError("Aliyun ISI TTS text contains a NUL character")
    if "<" in value or ">" in value:
        raise ValueError("Aliyun ISI TTS text must be plain text, not SSML")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Aliyun ISI TTS text must be valid Unicode") from None
    return value


def _validate_audio_url(value: Any, *, allowed_host_suffixes: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise AliyunTtsError("parse", "invalid_audio_url")
    url = value.strip()
    if not url or len(url) > 8192 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        raise AliyunTtsError("parse", "invalid_audio_url")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise AliyunTtsError("parse", "invalid_audio_url") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.netloc.rsplit("@", 1)[-1].endswith(":")
        or port == 0
        or "." not in ascii_hostname
        or len(ascii_hostname) > 253
        or not any(
            ascii_hostname == suffix or ascii_hostname.endswith(f".{suffix}")
            for suffix in allowed_host_suffixes
        )
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in ascii_hostname.split(".")
        )
    ):
        raise AliyunTtsError("parse", "invalid_audio_url")
    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        # The legacy ISI endpoint returns an ``http://`` OSS address even
        # though the same signed object is available over TLS.  Never follow
        # that clear-text address: normalize an otherwise fully validated,
        # allowlisted result to HTTPS before it leaves the adapter.
        if parsed.scheme == "http":
            return urlunsplit(
                ("https", parsed.netloc, parsed.path, parsed.query, "")
            )
        return url
    raise AliyunTtsError("parse", "invalid_audio_url")


def _subtitles(value: Any) -> tuple[TtsSubtitle, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AliyunTtsError("parse", "invalid_subtitles")
    parsed: list[TtsSubtitle] = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise AliyunTtsError("parse", "invalid_subtitle")
        try:
            begin_ms = _plain_integer(row.get("begin_time"), "begin_time")
            end_ms = _plain_integer(row.get("end_time"), "end_time")
        except ValueError:
            raise AliyunTtsError("parse", "invalid_subtitle_timing") from None
        text = row["text"].strip()
        if begin_ms < 0 or end_ms <= begin_ms or not text:
            raise AliyunTtsError("parse", "invalid_subtitle_timing_or_text")
        parsed.append(TtsSubtitle(begin_ms, end_ms, text))
    ordered = sorted(parsed, key=lambda item: (item.begin_ms, item.end_ms))
    if any(
        current.begin_ms < previous.end_ms
        for previous, current in zip(ordered, ordered[1:])
    ):
        raise AliyunTtsError("parse", "overlapping_subtitles")
    return tuple(ordered)


class AliyunIsiTtsClient:
    """Submit and poll long-text synthesis without implicit request retries."""

    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        token_manager: NlsTokenManager | None = None,
        token_store: NlsTokenStore | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not config.tts_configured:
            raise ValueError("Aliyun ISI TTS credentials are incomplete")
        self._config = config
        self._profiles = parse_voice_profiles(config.tts_voice_profiles_json)
        if token_manager is not None and token_store is not None:
            raise ValueError("pass either token_manager or token_store, not both")
        self._token_manager = token_manager or NlsTokenManager(
            config,
            token_store=token_store,
        )
        self._owns_token_manager = token_manager is None
        self._http_client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_http_client = http_client is None

    @property
    def voice_profiles(self) -> Mapping[str, AliyunTtsVoiceProfile]:
        return dict(self._profiles)

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()
        if self._owns_token_manager:
            self._token_manager.close()

    def __enter__(self) -> "AliyunIsiTtsClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def submit(self, text: str, *, voice_profile: str) -> TtsSubmission:
        profile = self._profiles.get(str(voice_profile or "").strip())
        if profile is None:
            raise ValueError("Aliyun ISI TTS voice profile is not configured")
        if not self._config.tts_result_allowed_host_suffixes:
            raise ValueError(
                "Aliyun ISI TTS result host allowlist must be configured before submission"
            )
        narration = validate_tts_text(text, max_chars=self._config.tts_max_chars)
        token = self._token_manager.get()
        body = {
            "header": {
                "appkey": self._config.app_key,
                "token": token.value,
            },
            "context": {"device_id": self._config.tts_device_id},
            "payload": {
                "enable_notify": False,
                "tts_request": {
                    "text": narration,
                    **profile.request_settings(),
                },
            },
        }
        try:
            response = self._http_client.post(
                self._config.tts_url,
                json=body,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise AliyunTtsSubmissionUnknown("transport_unknown") from None
        try:
            payload = _response_dict(response)
        except AliyunTtsError:
            if (
                response.status_code in {408, 425}
                or response.status_code >= 500
                or 200 <= response.status_code < 300
            ):
                raise AliyunTtsSubmissionUnknown("invalid_http_response") from None
            raise AliyunTtsRejected(
                f"http_{response.status_code}",
                retryable=response.status_code == 429,
            ) from None
        try:
            code = _business_code(payload)
        except AliyunTtsError:
            if (
                response.status_code in {408, 425}
                or response.status_code >= 500
                or 200 <= response.status_code < 300
            ):
                raise AliyunTtsSubmissionUnknown(
                    "invalid_submit_response",
                    task_id=_optional_task_id(payload),
                ) from None
            raise AliyunTtsRejected(
                f"http_{response.status_code}",
                retryable=response.status_code == 429,
            ) from None
        if (
            response.status_code in {408, 425}
            or response.status_code >= 500
            or _is_provider_server_error(code)
        ):
            raise AliyunTtsSubmissionUnknown(
                "provider_server_unknown",
                task_id=_optional_task_id(payload),
            )
        task_id = _optional_task_id(payload)
        if task_id and (
            response.status_code < 200
            or response.status_code >= 300
            or code != SUCCESS_CODE
        ):
            raise AliyunTtsSubmissionUnknown(
                "inconsistent_response_with_task_identity",
                task_id=task_id,
            )
        if response.status_code < 200 or response.status_code >= 300 or code != SUCCESS_CODE:
            if code == AUTH_FAILED_CODE:
                self._token_manager.invalidate(token.value)
            raise AliyunTtsRejected(
                f"provider_{code}",
                retryable=(
                    response.status_code == 429
                    or code in {AUTH_FAILED_CODE, THROTTLED_CODE}
                ),
            )
        if not task_id:
            raise AliyunTtsSubmissionUnknown("accepted_without_task_id")
        message = payload.get("error_message")
        if (
            not _response_status_is_success(payload)
            or not isinstance(message, str)
            or message.strip().upper() != "SUCCESS"
        ):
            raise AliyunTtsSubmissionUnknown(
                "accepted_with_inconsistent_status",
                task_id=task_id,
            )
        return TtsSubmission(
            task_id=task_id,
            error_code=code,
            voice_profile=profile.alias,
            settings_revision=profile.revision,
        )

    def poll(self, task_id: str) -> TtsPollResult:
        normalized_task_id = _safe_task_id(task_id)
        token = self._token_manager.get()
        try:
            response = self._http_client.get(
                self._config.tts_url,
                params={
                    "appkey": self._config.app_key,
                    "token": token.value,
                    "task_id": normalized_task_id,
                },
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise AliyunTtsPollError("transport", retryable=True) from None
        try:
            payload = _response_dict(response)
            code = _business_code(payload)
        except AliyunTtsError:
            retryable = (
                response.status_code in {408, 425, 429}
                or response.status_code >= 500
                or 200 <= response.status_code < 300
            )
            raise AliyunTtsPollError(
                f"http_{response.status_code}_invalid_response",
                retryable=retryable,
            ) from None
        if response.status_code < 200 or response.status_code >= 300:
            if code == AUTH_FAILED_CODE:
                self._token_manager.invalidate(token.value)
            raise AliyunTtsPollError(
                f"http_{response.status_code}",
                retryable=(
                    response.status_code == 429
                    or code == SUCCESS_CODE
                    or code == AUTH_FAILED_CODE
                    or _is_retryable(code)
                ),
            )
        pending_code = code in {RUNNING_CODE, QUEUEING_CODE}
        if code != SUCCESS_CODE and not pending_code:
            if code == AUTH_FAILED_CODE:
                self._token_manager.invalidate(token.value)
            raise AliyunTtsPollError(
                f"provider_{code}",
                retryable=(code == AUTH_FAILED_CODE or _is_retryable(code)),
            )
        if not _response_status_is_success(payload):
            raise AliyunTtsPollError("inconsistent_status", retryable=True)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AliyunTtsPollError("missing_data", retryable=True)
        response_task_id = _safe_task_id(data.get("task_id"))
        if response_task_id != normalized_task_id:
            raise AliyunTtsPollError("task_id_mismatch", retryable=False)
        message = payload.get("error_message")
        if not isinstance(message, str):
            raise AliyunTtsPollError("invalid_status_message", retryable=True)
        normalized_message = message.strip().upper()
        audio_address = data.get("audio_address")
        if (
            normalized_message in {"RUNNING", "QUEUEING", "WAITING"}
            and audio_address is None
            and code in {SUCCESS_CODE, RUNNING_CODE, QUEUEING_CODE}
        ):
            return TtsPollResult(response_task_id, TtsState.RUNNING, code)
        if normalized_message == "SUCCESS" and code == SUCCESS_CODE:
            return TtsPollResult(
                response_task_id,
                TtsState.SUCCEEDED,
                code,
                audio_url=_validate_audio_url(
                    audio_address,
                    allowed_host_suffixes=(
                        self._config.tts_result_allowed_host_suffixes
                    ),
                ),
                subtitles=_subtitles(data.get("sentences")),
            )
        raise AliyunTtsPollError("unknown_status", retryable=True)
