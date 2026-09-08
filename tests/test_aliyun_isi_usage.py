"""Offline tests for Aliyun ISI quota windows and integer pricing."""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import AliyunIsiConfig  # noqa: E402
from services.aliyun_isi_usage import (  # noqa: E402
    AliyunIsiUsageConfigurationError,
    asr_usage_plan,
    tts_usage_plan,
)
from services.podcast_worker_contracts import ProviderUsageUnit  # noqa: E402


def _asr_config(**overrides) -> AliyunIsiConfig:
    values = {
        "asr_quota_scope": "aliyun-isi-asr-trial",
        "asr_quota_timezone": "Asia/Shanghai",
        "asr_daily_audio_seconds_limit": 7_200,
        "asr_entitlement_ends_at": "2026-12-06T00:00:00+08:00",
        "asr_provider_deadline_seconds": 7_200,
        "asr_price_cny_minor_per_hour": 100,
        "asr_pricing_revision": "trial-2026-09",
    }
    values.update(overrides)
    return AliyunIsiConfig(**values)


def _tts_config(**overrides) -> AliyunIsiConfig:
    values = {
        "tts_quota_scope": "aliyun-isi-tts-smoke",
        "tts_campaign_id": "initial-smoke-2026-09",
        "tts_campaign_starts_at": "2026-09-01T00:00:00+08:00",
        "tts_campaign_ends_at": "2026-10-01T00:00:00+08:00",
        "tts_campaign_character_limit": 10_000,
        "tts_provider_deadline_seconds": 3_600,
        "tts_price_cny_minor_per_10000_chars": 300,
        "tts_pricing_revision": "public-price-2026-09",
    }
    values.update(overrides)
    return AliyunIsiConfig(**values)


def test_asr_uses_shanghai_submission_day_and_rounds_milliseconds_up():
    before_midnight = asr_usage_plan(
        _asr_config(),
        audio_duration_ms=1_001,
        now=dt.datetime(2026, 9, 5, 15, 59, 59, tzinfo=dt.timezone.utc),
    )
    after_midnight = asr_usage_plan(
        _asr_config(),
        audio_duration_ms=1_000,
        now=dt.datetime(2026, 9, 5, 16, 0, tzinfo=dt.timezone.utc),
    )

    assert before_midnight.quota_period == "2026-09-05"
    assert before_midnight.unit is ProviderUsageUnit.AUDIO_SECONDS
    assert before_midnight.reserved_units == 2
    assert before_midnight.window_start_at == dt.datetime(
        2026, 9, 4, 16, 0, tzinfo=dt.timezone.utc
    )
    assert before_midnight.window_end_at == dt.datetime(
        2026, 9, 5, 16, 0, tzinfo=dt.timezone.utc
    )
    assert before_midnight.estimated_cost_minor == 1
    assert after_midnight.quota_period == "2026-09-06"
    assert after_midnight.reserved_units == 1


def test_asr_window_is_clipped_to_entitlement_and_expiry_is_exclusive():
    config = _asr_config(
        asr_entitlement_ends_at="2026-09-05T23:30:00+08:00"
    )
    active = asr_usage_plan(
        config,
        audio_duration_ms=60_000,
        now=dt.datetime(2026, 9, 5, 15, 0, tzinfo=dt.timezone.utc),
    )
    assert active.window_end_at == dt.datetime(
        2026, 9, 5, 15, 30, tzinfo=dt.timezone.utc
    )

    with pytest.raises(AliyunIsiUsageConfigurationError, match="expired"):
        asr_usage_plan(
            config,
            audio_duration_ms=1_000,
            now=dt.datetime(2026, 9, 5, 15, 30, tzinfo=dt.timezone.utc),
        )


def test_asr_integer_price_rounds_up_without_floating_point():
    plan = asr_usage_plan(
        _asr_config(asr_daily_audio_seconds_limit=7_201),
        audio_duration_ms=3_600_001,
        now=dt.datetime(2026, 9, 5, 8, 0, tzinfo=dt.timezone.utc),
    )

    assert plan.reserved_units == 3_601
    assert plan.estimated_cost_minor == 101
    assert plan.actual_cost_minor(3_600) == 100


def test_tts_campaign_window_characters_and_integer_price_are_frozen():
    config = _tts_config(tts_campaign_character_limit=20_000)
    plan = tts_usage_plan(
        config,
        billable_characters=10_001,
        now=dt.datetime(2026, 9, 5, 8, 0, tzinfo=dt.timezone.utc),
    )

    assert plan.quota_scope == "aliyun-isi-tts-smoke"
    assert plan.quota_period == "initial-smoke-2026-09"
    assert plan.unit is ProviderUsageUnit.TTS_CHARACTERS
    assert plan.window_start_at == dt.datetime(
        2026, 8, 31, 16, 0, tzinfo=dt.timezone.utc
    )
    assert plan.window_end_at == dt.datetime(
        2026, 9, 30, 16, 0, tzinfo=dt.timezone.utc
    )
    assert plan.reserved_units == 10_001
    assert plan.estimated_cost_minor == 301
    assert plan.actual_cost_minor(10_000) == 300
    assert plan.pricing_revision == "public-price-2026-09"


def test_tts_campaign_start_is_inclusive_and_end_is_exclusive():
    config = _tts_config()
    starts = dt.datetime(2026, 8, 31, 16, 0, tzinfo=dt.timezone.utc)
    ends = dt.datetime(2026, 9, 30, 16, 0, tzinfo=dt.timezone.utc)

    assert tts_usage_plan(
        config, billable_characters=1, now=starts
    ).reserved_units == 1
    with pytest.raises(AliyunIsiUsageConfigurationError, match="not active"):
        tts_usage_plan(config, billable_characters=1, now=ends)


def test_empty_accounting_configuration_fails_closed():
    empty = AliyunIsiConfig()

    assert empty.asr_accounting_ready is False
    assert empty.tts_accounting_ready is False
    with pytest.raises(AliyunIsiUsageConfigurationError, match="not configured"):
        asr_usage_plan(
            empty,
            audio_duration_ms=1_000,
            now=dt.datetime(2026, 9, 5, 8, 0, tzinfo=dt.timezone.utc),
        )
    with pytest.raises(AliyunIsiUsageConfigurationError, match="not configured"):
        tts_usage_plan(
            empty,
            billable_characters=1,
            now=dt.datetime(2026, 9, 5, 8, 0, tzinfo=dt.timezone.utc),
        )
