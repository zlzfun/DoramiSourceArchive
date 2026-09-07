"""Logging guards for request targets that carry short-lived credentials.

The public Podcast ASR fetch URL authenticates with its query string.  Keep the
route observable by status/metrics, but never let that query reach Uvicorn's
access or exception logs.
"""

from __future__ import annotations

import logging
import re
import traceback


PODCAST_ASR_SOURCE_AUDIO_PATH = "/api/public/podcast-asr/source-audio"

_SIGNED_ASR_TARGET_RE = re.compile(
    rf"{re.escape(PODCAST_ASR_SOURCE_AUDIO_PATH)}\?\S+"
)


def redact_sensitive_request_queries(value: str) -> str:
    """Remove the complete query from the signed ASR download route only."""

    return _SIGNED_ASR_TARGET_RE.sub(PODCAST_ASR_SOURCE_AUDIO_PATH, value)


class SensitiveRequestQueryFilter(logging.Filter):
    """Sanitize formatted records, including exception text, in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Preserve Uvicorn AccessFormatter's five-item args contract.  Flattening
        # the already rendered message would redact the query but break every
        # signed-route access record during formatting.
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_request_queries(record.msg)
        record.args = _redact_log_value(record.args)

        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_info = None
            record.exc_text = redact_sensitive_request_queries(exception_text)
        elif record.exc_text:
            record.exc_text = redact_sensitive_request_queries(record.exc_text)

        if record.stack_info:
            record.stack_info = redact_sensitive_request_queries(record.stack_info)
        return True


def _redact_log_value(value):
    if isinstance(value, str):
        return redact_sensitive_request_queries(value)
    if isinstance(value, tuple):
        return tuple(_redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_log_value(item) for key, item in value.items()}
    return value


_FILTER = SensitiveRequestQueryFilter()
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


def _attach_once(target: logging.Filterer) -> None:
    if not any(
        isinstance(item, SensitiveRequestQueryFilter)
        for item in target.filters
    ):
        target.addFilter(_FILTER)


def install_uvicorn_sensitive_request_filters() -> None:
    """Install the route-specific redactor without disabling access logging.

    This is called again while ``api.app`` is imported.  Uvicorn imports the
    application only after applying its ``dictConfig``, so that second call is
    the startup-order guarantee rather than relying on pre-existing filters to
    survive logger reconfiguration.
    """

    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        _attach_once(logger)
        for handler in logger.handlers:
            _attach_once(handler)

    # dorami.* records propagate to this deliberately application-owned logger
    # and bypass ancestor logger filters, so guard its handlers as well.
    dorami_logger = logging.getLogger("dorami")
    _attach_once(dorami_logger)
    for handler in dorami_logger.handlers:
        _attach_once(handler)
