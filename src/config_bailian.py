"""Singapore Model Studio speech configuration (disabled until explicitly selected)."""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BailianSpeechConfig:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    account_scope: str = ""
    asr_model: str = "fun-asr-2025-11-07"
    tts_model: str = "qwen3-tts-flash-2025-11-27"
    tts_voice: str = "Cherry"
    voice_profile: str = "narrator_zh"
    request_timeout_seconds: int = 120
    asr_poll_interval_seconds: int = 10
    asr_provider_deadline_seconds: int = 7200
    asr_max_audio_seconds_per_file: int = 43200
    asr_daily_audio_seconds_limit: int = 0
    asr_quota_timezone: str = "Asia/Singapore"
    asr_entitlement_ends_at: str = ""
    # Rational CNY minor-unit prices: no floating-point currency conversion.
    asr_price_minor: int = 26
    asr_price_units: int = 1000
    tts_price_minor: int = 733924
    tts_price_units: int = 100000000
    pricing_revision: str = "aliyun-sg-2026-09-15"
    tts_max_chars: int = 4500
    tts_chunk_chars: int = 500
    tts_monthly_budget_minor: int = 0
    tts_per_run_budget_minor: int = 0
    tts_receipt_root: str = "data/bailian-speech"
    tts_cache_max_bytes: int = 536870912
    result_host_suffixes: tuple[str, ...] = ("oss-ap-southeast-1.aliyuncs.com",)

    def __post_init__(self):
        p = urlsplit(self.base_url)
        host = p.hostname or ""
        if (
            p.scheme != "https"
            or p.username
            or p.password
            or p.port not in (None, 443)
            or p.query
            or p.fragment
            or p.path != "/api/v1"
            or not (
                host == "dashscope-intl.aliyuncs.com"
                or re.fullmatch(
                    r"[a-z0-9-]+\.ap-southeast-1\.maas\.aliyuncs\.com", host
                )
            )
        ):
            raise ValueError("Bailian speech requires a Singapore HTTPS API base URL")
        if self.asr_model not in {"fun-asr", "fun-asr-2025-11-07"}:
            raise ValueError("Unsupported Bailian ASR model")
        if self.tts_model not in {"qwen3-tts-flash", "qwen3-tts-flash-2025-11-27"}:
            raise ValueError("Unsupported Bailian TTS model or billing unit")
        if (
            not 1 <= self.tts_chunk_chars <= 600
            or not 1 <= self.tts_max_chars <= 100000
        ):
            raise ValueError("Invalid Bailian TTS text limits")
        if not 1 <= self.asr_max_audio_seconds_per_file <= 43200:
            raise ValueError("Invalid Bailian ASR file duration limit")
        for name in (
            "request_timeout_seconds",
            "asr_poll_interval_seconds",
            "asr_provider_deadline_seconds",
            "asr_price_minor",
            "asr_price_units",
            "tts_price_minor",
            "tts_price_units",
            "tts_cache_max_bytes",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"Bailian {name} must be positive")
        for name in (
            "asr_daily_audio_seconds_limit",
            "tts_monthly_budget_minor",
            "tts_per_run_budget_minor",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"Bailian {name} must be nonnegative")
        if self.asr_quota_timezone != "Asia/Singapore":
            raise ValueError("Bailian speech quota timezone must be Asia/Singapore")
        if not self.result_host_suffixes or any(
            not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", x)
            for x in self.result_host_suffixes
        ):
            raise ValueError("Bailian result host allowlist is required")

    @property
    def asr_quota_scope(self):
        return f"bailian-asr:{self.account_scope}"

    @property
    def asr_poll_configured(self):
        return bool(self.api_key.strip() and self.account_scope.strip())

    @property
    def asr_configured(self):
        return self.asr_poll_configured

    @property
    def asr_accounting_ready(self):
        return bool(
            self.asr_daily_audio_seconds_limit
            and self.asr_entitlement_ends_at
            and self.pricing_revision
        )

    @property
    def tts_configured(self):
        return bool(
            self.asr_poll_configured
            and self.tts_voice
            and self.voice_profile
            and self.tts_monthly_budget_minor
            and self.tts_per_run_budget_minor
        )


def load_bailian_config(parser: configparser.ConfigParser) -> BailianSpeechConfig:
    defaults = BailianSpeechConfig()
    values = {}
    for f in fields(defaults):
        default = getattr(defaults, f.name)
        raw = os.getenv(f"DORAMI_BAILIAN_{f.name.upper()}")
        if raw is None or not raw.strip():
            raw = parser.get("bailian_speech", f.name, fallback=None)
        if raw is None:
            continue
        if isinstance(default, bool):
            if raw.lower() not in configparser.ConfigParser.BOOLEAN_STATES:
                raise ValueError(f"Invalid boolean: {f.name}")
            values[f.name] = configparser.ConfigParser.BOOLEAN_STATES[raw.lower()]
        elif isinstance(default, int):
            values[f.name] = int(raw)
        elif isinstance(default, tuple):
            values[f.name] = tuple(s.strip() for s in raw.split(",") if s.strip())
        else:
            values[f.name] = raw.strip()
    return BailianSpeechConfig(**values)
