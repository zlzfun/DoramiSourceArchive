import dataclasses
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.podcast_worker_contracts import (  # noqa: E402
    Accepted,
    ArtifactRef,
    ExecutionIdentity,
    ExecutionKind,
    Failure,
    FailureKind,
    Indeterminate,
    MaterializedOutput,
    NormalizedUsage,
    Pending,
    Rejected,
    RemoteAudioOutput,
    StageContext,
    StagePlan,
    Succeeded,
    TaskFailed,
    TextOutput,
    Unknown,
    canonical_settings_fingerprint,
)


SHA = "a" * 64


def _identity(kind=ExecutionKind.PROVIDER, **updates):
    values = {
        "execution_kind": kind,
        "provider": "provider_one",
        "model": "model-v1",
        "revision": "revision-1",
        "settings": {"sample_rate": 16_000, "voice": "narrator"},
    }
    values.update(updates)
    return ExecutionIdentity.from_settings(**values)


def _artifact(**updates):
    values = {
        "artifact_id": "artifact-1",
        "episode_id": "episode-1",
        "kind": "source_audio",
        "content_hash": SHA,
        "size_bytes": 123,
        "mime_type": "audio/mpeg",
    }
    values.update(updates)
    return ArtifactRef(**values)


def _failure(kind=FailureKind.TRANSIENT, **updates):
    values = {
        "kind": kind,
        "code": "provider_busy",
        "message": "try later",
        "retryable": True,
        "retry_after_seconds": 5,
    }
    values.update(updates)
    return Failure(**values)


def test_settings_fingerprint_is_canonical_and_binds_effective_identity():
    common = {
        "execution_kind": ExecutionKind.PROVIDER,
        "provider": "provider_one",
        "model": "model-v1",
        "revision": "revision-1",
    }
    first = canonical_settings_fingerprint(
        **common,
        settings={"voice_alias": "daily", "params": {"rate": 0, "volume": 50}},
    )
    reordered = canonical_settings_fingerprint(
        **common,
        settings={"params": {"volume": 50, "rate": 0}, "voice_alias": "daily"},
    )

    assert first == reordered
    assert len(first) == 64
    assert first != canonical_settings_fingerprint(
        **{**common, "model": "model-v2"},
        settings={"voice_alias": "daily", "params": {"rate": 0, "volume": 50}},
    )
    assert first != canonical_settings_fingerprint(
        **common,
        settings={"voice_alias": "daily", "params": {"rate": 1, "volume": 50}},
    )
    assert first != canonical_settings_fingerprint(
        **{**common, "revision": "revision-2"},
        settings={"voice_alias": "daily", "params": {"rate": 0, "volume": 50}},
    )


def test_settings_fingerprint_normalizes_tuple_like_json_array():
    common = {
        "execution_kind": ExecutionKind.LOCAL,
        "provider": "dorami",
        "model": "audio-qa",
        "revision": "1",
    }
    assert canonical_settings_fingerprint(**common, settings={"checks": ("a", "b")}) == (
        canonical_settings_fingerprint(**common, settings={"checks": ["a", "b"]})
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"api_token": "do-not-hash"},
        {"resultUrl": "https://signed.example/audio"},
        {"nested": {"client_secret": "do-not-hash"}},
        {"endpoint-uri": "https://provider.example"},
        {"password": "do-not-hash"},
        {"api_key": "do-not-hash"},
        {"audio_address": "https://signed.example/audio"},
    ],
)
def test_settings_fingerprint_rejects_sensitive_field_names(settings):
    with pytest.raises(ValueError, match="forbidden sensitive field"):
        canonical_settings_fingerprint(
            execution_kind=ExecutionKind.PROVIDER,
            provider="provider_one",
            model="model-v1",
            revision="1",
            settings=settings,
        )


def test_settings_fingerprint_allows_non_secret_tokenizer_parameter():
    assert canonical_settings_fingerprint(
        execution_kind=ExecutionKind.LOCAL,
        provider="dorami",
        model="normalizer",
        revision="1",
        settings={"tokenizer": "jieba"},
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"bad": math.nan},
        {"bad": math.inf},
        {"bad": object()},
        {1: "non-string key"},
    ],
)
def test_settings_fingerprint_rejects_non_canonical_json(settings):
    with pytest.raises(ValueError):
        canonical_settings_fingerprint(
            execution_kind=ExecutionKind.LOCAL,
            provider="dorami",
            model="normalizer",
            revision="1",
            settings=settings,
        )


def test_execution_identity_factory_and_direct_constructor_are_strict():
    identity = _identity()

    assert identity.execution_kind is ExecutionKind.PROVIDER
    assert identity.settings_fingerprint == canonical_settings_fingerprint(
        execution_kind=ExecutionKind.PROVIDER,
        provider="provider_one",
        model="model-v1",
        revision="revision-1",
        settings={"sample_rate": 16_000, "voice": "narrator"},
    )
    with pytest.raises(ValueError, match="ExecutionKind"):
        ExecutionIdentity("provider", "provider_one", "model-v1", "1", SHA)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="SHA-256"):
        ExecutionIdentity(ExecutionKind.PROVIDER, "provider_one", "model-v1", "1", "bad")


def test_artifact_ref_normalizes_safe_fields_and_is_frozen():
    artifact = _artifact(kind="SOURCE_AUDIO", content_hash=SHA.upper(), mime_type="Audio/MPEG")

    assert artifact.kind == "source_audio"
    assert artifact.content_hash == SHA
    assert artifact.mime_type == "audio/mpeg"
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.kind = "digest_audio_zh"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"artifact_id": "bad id"}, "artifact_id"),
        ({"kind": "bad-kind"}, "kind"),
        ({"content_hash": "abc"}, "SHA-256"),
        ({"size_bytes": -1}, "nonnegative"),
        ({"size_bytes": True}, "nonnegative"),
        ({"mime_type": "audio/mpeg; charset=utf-8"}, "media type"),
    ],
)
def test_artifact_ref_rejects_invalid_persisted_identity(updates, message):
    with pytest.raises(ValueError, match=message):
        _artifact(**updates)


def test_stage_plan_and_context_validate_deadlines_fences_and_local_cost():
    local = _identity(ExecutionKind.LOCAL, provider="dorami", model="audio-qa")
    plan = StagePlan(estimated_cost_minor=0, poll_interval_seconds=2, deadline_seconds=30)
    context = StageContext(
        processing_id="process-1",
        episode_id="episode-1",
        target="digest_audio",
        stage="audio_qa",
        attempt_id="attempt-1",
        attempt_no=1,
        fencing_token=2,
        input_artifact=_artifact(),
        identity=local,
        plan=plan,
    )

    assert context.plan.deadline_seconds == 30
    with pytest.raises(ValueError, match="zero"):
        StageContext(
            **{
                **context.__dict__,
                "plan": StagePlan(1, 2, 30),
            }
        )
    with pytest.raises(ValueError, match="episode_id"):
        StageContext(**{**context.__dict__, "episode_id": "another-episode"})
    with pytest.raises(ValueError, match="positive"):
        StagePlan(0, 0, 30)
    with pytest.raises(ValueError, match="at least one poll"):
        StagePlan(0, 31, 30)
    with pytest.raises(ValueError, match="positive"):
        StageContext(**{**context.__dict__, "fencing_token": 0})


def test_failure_validates_retry_contract():
    failure = _failure(message="  provider busy  ")

    assert failure.message == "provider busy"
    assert "provider busy" not in repr(failure)
    assert failure.retry_after_seconds == 5
    with pytest.raises(ValueError, match="retryable"):
        _failure(retryable=False)
    with pytest.raises(ValueError, match="positive"):
        _failure(retry_after_seconds=0)
    with pytest.raises(ValueError, match="boolean"):
        _failure(retryable=1, retry_after_seconds=None)


def test_normalized_usage_requires_integer_counters_and_iso_currency():
    usage = NormalizedUsage(
        cost_minor=3,
        audio_duration_ms=1_000,
        input_tokens=11,
        output_tokens=12,
        audio_tokens=13,
        tts_characters=20,
        input_bytes=100,
        output_bytes=200,
    )

    assert usage.currency == "CNY"
    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.audio_tokens,
        usage.tts_characters,
    ) == (11, 12, 13, 20)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(cost_minor=-1)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(output_bytes=True)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(input_tokens=-1)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(output_tokens=True)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(audio_tokens=-1)
    with pytest.raises(ValueError, match="nonnegative"):
        NormalizedUsage(tts_characters=True)
    with pytest.raises(ValueError, match="ISO 4217"):
        NormalizedUsage(currency="cny")


def test_remote_audio_output_hides_signed_url_and_validates_metadata():
    output = RemoteAudioOutput(
        download_url="https://bucket.example.test/result.wav?signature=sensitive",
        mime_type="Audio/WAV",
        expected_size_bytes=123,
        expected_content_hash=SHA.upper(),
    )

    assert output.mime_type == "audio/wav"
    assert output.expected_content_hash == SHA
    assert "signature" not in repr(output)
    with pytest.raises(ValueError, match="HTTPS"):
        RemoteAudioOutput("http://bucket.example/result.wav", "audio/wav")
    with pytest.raises(ValueError, match="HTTPS"):
        RemoteAudioOutput("https://bucket.example/result.wav#fragment", "audio/wav")
    with pytest.raises(ValueError, match="nonnegative"):
        RemoteAudioOutput("https://bucket.example/result.wav", "audio/wav", -1)


def test_text_and_materialized_outputs_are_validated():
    text = TextOutput("你好", mime_type="text/plain", language="ZH-CN")
    usage = NormalizedUsage(output_bytes=123)
    materialized = MaterializedOutput(_artifact(), usage)

    assert text.language == "zh-cn"
    assert "你好" not in repr(text)
    assert materialized.artifact.size_bytes == usage.output_bytes
    with pytest.raises(ValueError, match="TextOutput"):
        TextOutput("{}", mime_type="audio/mpeg")
    with pytest.raises(ValueError, match="valid JSON"):
        TextOutput("not-json", mime_type="application/json")
    with pytest.raises(ValueError, match="language"):
        TextOutput("hello", language="not_a_language")
    with pytest.raises(ValueError, match="match"):
        MaterializedOutput(_artifact(), NormalizedUsage(output_bytes=122))


def test_submit_outcomes_are_mutually_validated_and_unknown_may_lack_task_id():
    accepted = Accepted("provider-task-1")
    unknown_failure = _failure(
        FailureKind.REQUEST_UNKNOWN,
        code="transport_closed",
        retryable=False,
        retry_after_seconds=None,
    )

    assert accepted.task_id == "provider-task-1"
    assert Unknown(unknown_failure).task_id is None
    assert Unknown(unknown_failure, "provider-task-2").task_id == "provider-task-2"
    with pytest.raises(TypeError):
        Accepted()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="task_id"):
        Accepted("")
    with pytest.raises(ValueError, match="request_unknown"):
        Unknown(_failure())
    with pytest.raises(ValueError, match="cannot carry"):
        Rejected(unknown_failure)


def test_poll_outcomes_capture_pending_success_failure_and_indeterminate():
    usage = NormalizedUsage(cost_minor=2, output_bytes=123)
    retryable = _failure()

    assert Pending("provider-task-1", 5).retry_after_seconds == 5
    assert Succeeded(
        "provider-task-1", TextOutput("transcript", language="zh"), usage
    ).task_id == "provider-task-1"
    assert TaskFailed("provider-task-1", retryable).usage == NormalizedUsage()
    assert Indeterminate("provider-task-1", retryable, 5).failure.retryable is True
    with pytest.raises(ValueError, match="positive"):
        Pending("provider-task-1", 0)
    with pytest.raises(ValueError, match="provider output"):
        Succeeded("provider-task-1", object(), usage)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="request_unknown"):
        TaskFailed(
            "provider-task-1",
            _failure(
                FailureKind.REQUEST_UNKNOWN,
                retryable=False,
                retry_after_seconds=None,
            ),
        )
    with pytest.raises(ValueError, match="retryable"):
        Indeterminate(
            "provider-task-1",
            _failure(
                FailureKind.TERMINAL,
                retryable=False,
                retry_after_seconds=None,
            ),
            5,
        )
