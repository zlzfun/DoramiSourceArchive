"""Provider-neutral ASR/TTS port contracts."""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.podcast_provider_ports import (  # noqa: E402
    RemoteAudioDeliveryPolicy,
    TtsProviderAdapter,
    TtsProviderPlan,
)
from services.podcast_worker_contracts import (  # noqa: E402
    ExecutionIdentity,
    ExecutionKind,
    ProviderUsagePlan,
    ProviderUsageUnit,
    StagePlan,
)


def _identity() -> ExecutionIdentity:
    return ExecutionIdentity.from_settings(
        execution_kind=ExecutionKind.PROVIDER,
        provider="fake-tts",
        model="model-v1",
        revision="api-v1",
        settings={"voice": "voice-v1"},
    )


def test_tts_plan_accepts_a_non_aliyun_provider_identity():
    usage = ProviderUsagePlan(
        quota_scope="fake-account",
        quota_period="campaign-1",
        unit=ProviderUsageUnit.TTS_CHARACTERS,
        window_start_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
        window_end_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        limit_units=10_000,
        reserved_units=12,
        unit_price_cny_minor=300,
        price_unit_count=10_000,
        pricing_revision="fake-price-v1",
        deadline_seconds=600,
    )

    plan = TtsProviderPlan(
        identity=_identity(),
        stage=StagePlan(1, 10, 600),
        usage=usage,
        admission_fingerprint="a" * 64,
        voice_profile_id="narrator-zh",
    )

    assert plan.identity.provider == "fake-tts"


def test_remote_audio_policy_is_provider_neutral_and_fail_closed():
    policy = RemoteAudioDeliveryPolicy(("cdn.example.com", "objects.test.net"))

    assert policy.allowed_host_suffixes == (
        "cdn.example.com",
        "objects.test.net",
    )
    with pytest.raises(ValueError, match="unique"):
        RemoteAudioDeliveryPolicy(("cdn.example.com", "cdn.example.com"))
    with pytest.raises(ValueError, match="invalid"):
        RemoteAudioDeliveryPolicy(("localhost",))


def test_tts_adapter_port_is_structural_not_vendor_inherited():
    class FakeProvider:
        def plan(self, *args, **kwargs):
            raise NotImplementedError

        def remote_audio_policy(self):
            return RemoteAudioDeliveryPolicy(("cdn.example.com",))

        def submit(self, *args, **kwargs):
            raise NotImplementedError

        def supports(self, identity):
            return identity.provider == "fake-tts"

        def poll(self, *args, **kwargs):
            raise NotImplementedError

    assert isinstance(FakeProvider(), TtsProviderAdapter)
