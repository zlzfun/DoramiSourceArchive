"""Fail-closed Aliyun ISI entitlement and integer-price planning."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from config import AliyunIsiConfig
from services.podcast_worker_contracts import ProviderUsagePlan, ProviderUsageUnit


class AliyunIsiUsageConfigurationError(ValueError):
    """The selected provider stage has no complete accounting policy."""


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(dt.timezone.utc)


def _timestamp(value: str, field_name: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AliyunIsiUsageConfigurationError(
            f"{field_name} must be an RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AliyunIsiUsageConfigurationError(
            f"{field_name} must include a timezone offset"
        )
    return parsed.astimezone(dt.timezone.utc)


def asr_usage_plan(
    config: AliyunIsiConfig,
    *,
    audio_duration_ms: int,
    now: dt.datetime,
) -> ProviderUsagePlan:
    """Reserve the source duration against the Shanghai submission day."""

    if not config.asr_accounting_ready:
        raise AliyunIsiUsageConfigurationError(
            "Aliyun ISI ASR provider accounting is not configured"
        )
    if isinstance(audio_duration_ms, bool) or not isinstance(audio_duration_ms, int):
        raise ValueError("audio_duration_ms must be a positive integer")
    seconds = (audio_duration_ms + 999) // 1000
    if seconds <= 0:
        raise ValueError("audio_duration_ms must be a positive integer")
    current = _utc(now)
    entitlement_end = _timestamp(
        config.asr_entitlement_ends_at, "asr_entitlement_ends_at"
    )
    if current >= entitlement_end:
        raise AliyunIsiUsageConfigurationError(
            "Aliyun ISI ASR entitlement has expired"
        )
    timezone = ZoneInfo(config.asr_quota_timezone)
    local = current.astimezone(timezone)
    local_start = dt.datetime.combine(
        local.date(), dt.time.min, tzinfo=timezone
    )
    local_end = local_start + dt.timedelta(days=1)
    window_start = local_start.astimezone(dt.timezone.utc)
    window_end = min(local_end.astimezone(dt.timezone.utc), entitlement_end)
    return ProviderUsagePlan(
        quota_scope=config.asr_quota_scope,
        quota_period=local.date().isoformat(),
        unit=ProviderUsageUnit.AUDIO_SECONDS,
        window_start_at=window_start,
        window_end_at=window_end,
        limit_units=config.asr_daily_audio_seconds_limit,
        reserved_units=seconds,
        unit_price_cny_minor=config.asr_price_cny_minor_per_hour,
        price_unit_count=3600,
        pricing_revision=config.asr_pricing_revision,
        deadline_seconds=config.asr_provider_deadline_seconds,
    )


def tts_usage_plan(
    config: AliyunIsiConfig,
    *,
    billable_characters: int,
    now: dt.datetime,
) -> ProviderUsagePlan:
    """Reserve a caller-supplied character estimate in one explicit campaign.

    The current provider contract has no actual billed-character response
    field, so callers must not present this local submission count as final
    provider usage.
    """

    if not config.tts_accounting_ready:
        raise AliyunIsiUsageConfigurationError(
            "Aliyun ISI TTS provider accounting is not configured"
        )
    if (
        isinstance(billable_characters, bool)
        or not isinstance(billable_characters, int)
        or billable_characters <= 0
    ):
        raise ValueError("billable_characters must be a positive integer")
    current = _utc(now)
    starts = _timestamp(config.tts_campaign_starts_at, "tts_campaign_starts_at")
    ends = _timestamp(config.tts_campaign_ends_at, "tts_campaign_ends_at")
    if not starts <= current < ends:
        raise AliyunIsiUsageConfigurationError(
            "Aliyun ISI TTS campaign is not active"
        )
    return ProviderUsagePlan(
        quota_scope=config.tts_quota_scope,
        quota_period=config.tts_campaign_id,
        unit=ProviderUsageUnit.TTS_CHARACTERS,
        window_start_at=starts,
        window_end_at=ends,
        limit_units=config.tts_campaign_character_limit,
        reserved_units=billable_characters,
        unit_price_cny_minor=config.tts_price_cny_minor_per_10000_chars,
        price_unit_count=10_000,
        pricing_revision=config.tts_pricing_revision,
        deadline_seconds=config.tts_provider_deadline_seconds,
    )
