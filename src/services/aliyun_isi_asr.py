"""Aliyun ISI recording-file ASR wire adapter.

This module deliberately has no database or Podcast state-machine dependency.
The worker persists its prepared attempt before calling ``submit`` and must map
``AliyunAsrSubmissionUnknown`` to request-unknown without retrying the POST.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from config import AliyunIsiConfig
from services.aliyun_isi_auth import (
    AliyunIsiError,
    AliyunIsiProtocolError,
    AliyunIsiTransportError,
    AliyunPopClient,
)


SUCCESS = 21050000
RUNNING = 21050001
QUEUEING = 21050002
EMPTY_RESULT = 21050003
THROTTLED = 40000005
NO_WORDS = "ASR_RESPONSE_HAVE_NO_WORDS"


def _is_provider_server_error(code: int) -> bool:
    return 50_000_000 <= code < 60_000_000


class AsrState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class AliyunAsrError(AliyunIsiError):
    def __init__(self, operation: str, code: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(f"Aliyun ISI ASR failed (operation={operation}, code={code})")


class AliyunAsrSubmissionUnknown(AliyunAsrError):
    """The caller cannot prove whether a provider task was created."""

    def __init__(self, operation: str, code: str, *, task_id: str = "") -> None:
        self.task_id = task_id
        super().__init__(operation, code)


class AliyunAsrRejected(AliyunAsrError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("submit", code)


class AliyunAsrPollError(AliyunAsrError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("poll", code)


@dataclass(frozen=True)
class AsrSubmission:
    task_id: str
    status_code: int


@dataclass(frozen=True)
class TranscriptSegment:
    begin_ms: int
    end_ms: int
    text: str
    channel_id: int


@dataclass(frozen=True)
class TranscriptWord:
    begin_ms: int
    end_ms: int
    text: str
    channel_id: int


@dataclass(frozen=True)
class AsrTranscript:
    text: str
    segments: tuple[TranscriptSegment, ...]
    words: tuple[TranscriptWord, ...]
    audio_duration_ms: int


@dataclass(frozen=True)
class AsrPollResult:
    task_id: str
    state: AsrState
    status_code: int
    transcript: AsrTranscript | None = None


def _provider_code(payload: Mapping[str, Any]) -> int:
    raw = payload.get("StatusCode")
    if raw == NO_WORDS:
        return EMPTY_RESULT
    if isinstance(raw, bool) or not (
        isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit())
    ):
        raise AliyunAsrError("parse", "invalid_status_code") from None
    code = int(raw)
    if code < 0:
        raise AliyunAsrError("parse", "invalid_status_code")
    return code


def _safe_task_id(value: Any) -> str:
    if not isinstance(value, str):
        raise AliyunAsrError("parse", "invalid_task_id")
    task_id = value.strip()
    if not task_id or len(task_id) > 200:
        raise AliyunAsrError("parse", "invalid_task_id")
    return task_id


def _optional_task_id(payload: Mapping[str, Any]) -> str:
    try:
        return _safe_task_id(payload.get("TaskId"))
    except AliyunAsrError:
        return ""


def validate_provider_fetch_url(value: str) -> str:
    """Require a provider-fetchable HTTPS domain URL, never a local path/IP."""

    url = str(value or "").strip()
    if len(url) > 8192 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        raise ValueError("ASR file URL must be a valid provider-fetchable HTTPS URL")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError(
            "ASR file URL must be a provider-fetchable HTTPS domain URL"
        ) from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.netloc.rsplit("@", 1)[-1].endswith(":")
        or port == 0
        or ascii_hostname == "localhost"
        or "." not in ascii_hostname
        or len(ascii_hostname) > 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in ascii_hostname.split(".")
        )
    ):
        raise ValueError("ASR file URL must be a provider-fetchable HTTPS domain URL")
    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        pass
    else:
        raise ValueError("ASR file URL cannot use an IP literal")
    return url


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or (isinstance(value, str) and value.isdigit())
    ):
        raise AliyunAsrError("parse", f"invalid_{field}")
    parsed = int(value)
    if parsed < minimum:
        raise AliyunAsrError("parse", f"invalid_{field}")
    return parsed


def _timed_items(
    rows: Any,
    *,
    text_key: str,
    item_type: type[TranscriptSegment] | type[TranscriptWord],
) -> Sequence[TranscriptSegment] | Sequence[TranscriptWord]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise AliyunAsrError("parse", "invalid_result_items")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise AliyunAsrError("parse", "invalid_result_item")
        begin_ms = _integer(row.get("BeginTime"), "begin_time")
        end_ms = _integer(row.get("EndTime"), "end_time")
        channel_id = _integer(row.get("ChannelId"), "channel_id")
        raw_text = row.get(text_key)
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if end_ms <= begin_ms or not text:
            raise AliyunAsrError("parse", "invalid_result_timing_or_text")
        items.append(item_type(begin_ms, end_ms, text, channel_id))
    return tuple(
        sorted(
            items,
            key=lambda item: (item.begin_ms, item.end_ms, item.channel_id),
        )
    )


def _transcript(payload: Mapping[str, Any]) -> AsrTranscript:
    result = payload.get("Result")
    if not isinstance(result, dict):
        raise AliyunAsrError("parse", "missing_result")
    segments = _timed_items(
        result.get("Sentences"),
        text_key="Text",
        item_type=TranscriptSegment,
    )
    words = _timed_items(
        result.get("Words"),
        text_key="Word",
        item_type=TranscriptWord,
    )
    if not segments:
        raise AliyunAsrError("parse", "missing_sentences")
    duration = _integer(payload.get("BizDuration"), "biz_duration")
    if any(item.end_ms > duration for item in (*segments, *words)):
        raise AliyunAsrError("parse", "timestamp_exceeds_duration")
    return AsrTranscript(
        text="\n".join(item.text for item in segments),
        segments=tuple(segments),
        words=tuple(words),
        audio_duration_ms=duration,
    )


class AliyunIsiAsrClient:
    """Submit and poll ISI recording-file recognition without implicit retries."""

    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        pop_client: AliyunPopClient | None = None,
        enable_words: bool | None = None,
        auto_split: bool | None = None,
    ) -> None:
        if not config.asr_poll_configured:
            raise ValueError("Aliyun ISI ASR polling credentials are incomplete")
        self._config = config
        self._pop_client = pop_client or AliyunPopClient(config)
        self._owns_pop_client = pop_client is None
        self._enable_words = (
            config.asr_enable_words if enable_words is None else bool(enable_words)
        )
        self._auto_split = (
            config.asr_auto_split if auto_split is None else bool(auto_split)
        )
        self._url = f"https://{config.asr_domain}/"

    def close(self) -> None:
        if self._owns_pop_client:
            self._pop_client.close()

    def __enter__(self) -> "AliyunIsiAsrClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def submit(self, file_url: str) -> AsrSubmission:
        if not self._config.app_key:
            raise AliyunAsrRejected("app_key_unavailable", retryable=True)
        task = {
            "appkey": self._config.app_key,
            "file_link": validate_provider_fetch_url(file_url),
            "version": self._config.asr_task_version,
            "enable_words": self._enable_words,
            "auto_split": self._auto_split,
            "enable_sample_rate_adaptive": (
                self._config.asr_enable_sample_rate_adaptive
            ),
            "enable_callback": False,
        }
        try:
            payload = self._pop_client.call(
                action="SubmitTask",
                version=self._config.asr_api_version,
                url=self._url,
                method="POST",
                parameters={
                    "Task": json.dumps(
                        task,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                },
            )
        except AliyunIsiTransportError:
            raise AliyunAsrSubmissionUnknown("submit", "transport_unknown") from None
        except AliyunIsiProtocolError as exc:
            if (
                exc.status_code is None
                or exc.status_code in {408, 425}
                or exc.status_code >= 500
                or 200 <= exc.status_code < 300
            ):
                raise AliyunAsrSubmissionUnknown("submit", "http_unknown") from None
            raise AliyunAsrRejected(
                f"http_{exc.status_code or 'error'}",
                retryable=(
                    exc.status_code == 429
                    or exc.provider_error_kind == "throttled"
                ),
            ) from None
        try:
            code = _provider_code(payload)
        except AliyunAsrError:
            raise AliyunAsrSubmissionUnknown(
                "submit",
                "invalid_submit_response",
                task_id=_optional_task_id(payload),
            ) from None
        if code != SUCCESS:
            if _is_provider_server_error(code):
                raise AliyunAsrSubmissionUnknown(
                    "submit",
                    "provider_server_unknown",
                    task_id=_optional_task_id(payload),
                )
            raise AliyunAsrRejected(
                f"provider_{code}", retryable=code == THROTTLED
            )
        try:
            task_id = _safe_task_id(payload.get("TaskId"))
        except AliyunAsrError:
            # Provider reported accepted but did not return the only durable
            # lookup key; another POST could create a second billed job.
            raise AliyunAsrSubmissionUnknown("submit", "accepted_without_task_id") from None
        if str(payload.get("StatusText") or "").strip().upper() != "SUCCESS":
            raise AliyunAsrSubmissionUnknown(
                "submit",
                "accepted_with_inconsistent_status",
                task_id=task_id,
            )
        return AsrSubmission(task_id=task_id, status_code=code)

    def poll(self, task_id: str) -> AsrPollResult:
        normalized_task_id = _safe_task_id(task_id)
        try:
            payload = self._pop_client.call(
                action="GetTaskResult",
                version=self._config.asr_api_version,
                url=self._url,
                method="GET",
                parameters={"TaskId": normalized_task_id},
            )
        except AliyunIsiTransportError:
            raise AliyunAsrPollError("transport", retryable=True) from None
        except AliyunIsiProtocolError as exc:
            retryable = (
                exc.status_code == 429
                or exc.provider_error_kind == "throttled"
                or bool(exc.status_code is not None and exc.status_code >= 500)
            )
            raise AliyunAsrPollError(
                f"http_{exc.status_code or 'error'}", retryable=retryable
            ) from None
        response_task_id = _safe_task_id(payload.get("TaskId"))
        if response_task_id != normalized_task_id:
            raise AliyunAsrError("poll", "task_id_mismatch")
        code = _provider_code(payload)
        expected_statuses = {
            QUEUEING: "QUEUEING",
            RUNNING: "RUNNING",
            SUCCESS: "SUCCESS",
        }
        status_text = str(payload.get("StatusText") or "").strip().upper()
        valid_statuses = (
            {"SUCCESS_WITH_NO_VALID_FRAGMENT", NO_WORDS}
            if code == EMPTY_RESULT
            else {expected_statuses[code]}
            if code in expected_statuses
            else None
        )
        if valid_statuses is not None and status_text not in valid_statuses:
            raise AliyunAsrError("poll", "inconsistent_status")
        if code == QUEUEING:
            return AsrPollResult(response_task_id, AsrState.QUEUED, code)
        if code == RUNNING:
            return AsrPollResult(response_task_id, AsrState.RUNNING, code)
        if code == EMPTY_RESULT:
            return AsrPollResult(response_task_id, AsrState.EMPTY, code)
        if code == SUCCESS:
            return AsrPollResult(
                response_task_id,
                AsrState.SUCCEEDED,
                code,
                transcript=_transcript(payload),
            )
        state = (
            AsrState.FAILED_RETRYABLE
            if code == THROTTLED or _is_provider_server_error(code)
            else AsrState.FAILED_TERMINAL
        )
        return AsrPollResult(response_task_id, state, code)
