"""Singapore speech wire contracts, durable replay and bounded accounting."""

import asyncio
import datetime as dt
import io
import json
import wave
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from config_bailian import BailianSpeechConfig, load_bailian_config
from models.db import BailianTtsCallRecord
from services.bailian_asr import (
    BailianAsrAdapter,
    asr_identity,
    usage_plan,
    admission_fingerprint,
    normalize_transcript,
)
from services.bailian_speech_client import (
    BailianSpeechClient,
    BailianSpeechError,
    result_url,
)
from services.bailian_tts import (
    BailianPremiumGuideTtsProvider,
    join_wav,
    split_narration,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services.podcast_worker_contracts import (
    Accepted,
    Unknown,
    Rejected,
    Succeeded,
    Indeterminate,
    ArtifactRef,
)

NOW = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)


def configuration(**kw):
    defaults = dict(
        api_key="secret-test",
        account_scope="test-account",
        asr_daily_audio_seconds_limit=7200,
        asr_entitlement_ends_at="2027-01-01T00:00:00Z",
        tts_monthly_budget_minor=100,
        tts_per_run_budget_minor=50,
    )
    defaults.update(kw)
    return BailianSpeechConfig(**defaults)


def wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 2400)
    return out.getvalue()


def transcript():
    return {
        "properties": {"original_duration_in_milliseconds": 60000},
        "transcripts": [
            {
                "channel_id": 0,
                "sentences": [
                    {
                        "text": "测试。",
                        "begin_time": 0,
                        "end_time": 1000,
                        "words": [
                            {
                                "text": "测试",
                                "punctuation": "。",
                                "begin_time": 0,
                                "end_time": 1000,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def success():
    return {
        "output": {
            "task_id": "task-1",
            "task_status": "SUCCEEDED",
            "results": [
                {
                    "subtask_status": "SUCCEEDED",
                    "transcription_url": "https://result.oss-ap-southeast-1.aliyuncs.com/test.json",
                }
            ],
        },
        "usage": {"duration": 10},
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://dashscope-intl.aliyuncs.com/api/v1",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://evil.example/api/v1",
        "https://dashscope-intl.aliyuncs.com/api/v1?secret=x",
    ],
)
def test_region_rejects_wrong_or_unsafe_endpoint(url):
    with pytest.raises(ValueError):
        configuration(base_url=url)


def test_env_ini_configuration_and_secret_redaction(monkeypatch):
    import configparser

    parser = configparser.ConfigParser()
    parser.read_string(
        "[bailian_speech]\nenabled=true\nasr_daily_audio_seconds_limit=180\n"
    )
    monkeypatch.setenv("DORAMI_BAILIAN_API_KEY", "secret-test")
    cfg = load_bailian_config(parser)
    assert cfg.enabled and cfg.asr_daily_audio_seconds_limit == 180
    assert "secret-test" not in repr(cfg)
    assert not cfg.tts_configured


def test_exact_fractional_price_and_region_boundary():
    cfg = configuration()
    plan = usage_plan(cfg, audio_duration_ms=3600000, now=NOW)
    assert plan.estimated_cost_minor == 94
    assert plan.actual_cost_minor(1000) == 26
    assert plan.window_start_at.hour == 16
    assert usage_plan(cfg, audio_duration_ms=1001, now=NOW).reserved_units == 2
    with pytest.raises(ValueError):
        usage_plan(
            replace(cfg, asr_entitlement_ends_at="2026-01-01T00:00:00Z"),
            audio_duration_ms=1,
            now=NOW,
        )


def test_usage_authority_does_not_accept_forged_prices():
    from config import PodcastConfig

    cfg = configuration()
    plan = usage_plan(cfg, audio_duration_ms=1000, now=NOW)
    policy = PodcastStagePolicy(PodcastConfig(), bailian_speech=cfg)
    assert policy.provider_usage_plan_matches("aliyun-bailian", "asr", plan, now=NOW)
    assert not policy.provider_usage_plan_matches(
        "aliyun-bailian", "asr", replace(plan, unit_price_cny_minor=1), now=NOW
    )
    assert not PodcastStagePolicy(PodcastConfig()).provider_usage_plan_matches(
        "aliyun-bailian", "asr", plan, now=NOW
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, Accepted),
        (400, Rejected),
        (401, Rejected),
        (429, Rejected),
        (500, Unknown),
        (408, Unknown),
    ],
)
def test_submit_wire_semantics_no_idempotency_or_auto_retry(status, expected):
    requests = []

    def handle(req):
        requests.append(req)
        assert req.headers["Authorization"] == "Bearer secret-test"
        assert req.headers["X-DashScope-Async"] == "enable"
        body = json.loads(req.content)
        assert body["parameters"] == {"channel_id": [0]}
        assert "provider_request_key" not in req.content.decode()
        return httpx.Response(
            status, json={"output": {"task_id": "task-1", "task_status": "PENDING"}}
        )

    cfg = configuration()
    client = BailianSpeechClient(cfg, transport=httpx.MockTransport(handle))
    adapter = BailianAsrAdapter(
        cfg, url_resolver=lambda _: "https://example.com/audio.mp3", client=client
    )
    result = adapter.submit(
        SimpleNamespace(identity=asr_identity(cfg)),
        provider_request_key="must-not-send",
    )
    assert isinstance(result, expected) and len(requests) == 1
    client.close()


def test_timeout_and_malformed_success_are_unknown():
    cfg = configuration()
    for handle in (
        lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("secret-test")),
        lambda req: httpx.Response(200, json={}),
    ):
        client = BailianSpeechClient(cfg, transport=httpx.MockTransport(handle))
        adapter = BailianAsrAdapter(
            cfg, url_resolver=lambda _: "https://example.com/audio.mp3", client=client
        )
        result = adapter.submit(
            SimpleNamespace(identity=asr_identity(cfg)), provider_request_key="key"
        )
        assert isinstance(result, Unknown) and "secret-test" not in str(result)
        client.close()


def test_poll_normalization_actual_usage_key_rotation_and_scope_change():
    cfg = configuration()
    client = BailianSpeechClient(
        cfg,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=success())),
    )

    async def download(*args, **kwargs):
        return json.dumps(transcript()).encode()

    adapter = BailianAsrAdapter(
        cfg, url_resolver=lambda _: None, client=client, downloader=download
    )
    result = adapter.poll(
        task_id="task-1",
        identity=asr_identity(cfg),
        audio_duration_ms=60000,
        reserved_cost_minor=2,
    )
    assert isinstance(result, Succeeded)
    assert result.usage.audio_duration_ms == 60000 and result.usage.cost_minor == 1
    assert result.usage.billed_audio_duration_ms == 10000
    assert json.loads(result.output.text)["segments"][0]["words"][0]["text"] == "测试。"
    assert adapter.supports(asr_identity(replace(cfg, api_key="rotated")))
    assert not adapter.supports(asr_identity(replace(cfg, account_scope="other")))
    client.close()


@pytest.mark.parametrize("blank_text", ["", " ", "\t\n"])
def test_completed_asr_with_blank_word_tokens_is_materialized_without_resubmit(blank_text):
    cfg = configuration()
    payload = transcript()
    sentence = payload["transcripts"][0]["sentences"][0]
    sentence["text"] = "Hello world."
    sentence["words"] = [
        {"text": "Hello", "begin_time": 0, "end_time": 400},
        {"text": blank_text, "punctuation": "", "begin_time": 400, "end_time": 450},
        {"text": "world", "begin_time": 450, "end_time": 900},
        {"text": "", "punctuation": ".", "begin_time": 900, "end_time": 1000},
    ]
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=success())

    async def download(*args, **kwargs):
        return json.dumps(payload).encode()

    client = BailianSpeechClient(cfg, transport=httpx.MockTransport(handle))
    adapter = BailianAsrAdapter(
        cfg, url_resolver=lambda _: None, client=client, downloader=download
    )
    result = adapter.poll(
        task_id="task-1", identity=asr_identity(cfg),
        audio_duration_ms=60000, reserved_cost_minor=2,
    )
    assert isinstance(result, Succeeded)
    normalized = json.loads(result.output.text)
    assert normalized["text"] == sentence["text"]
    assert [w["text"] for w in normalized["segments"][0]["words"]] == [
        "Hello", "world", ".",
    ]
    assert result.usage.billed_audio_duration_ms == 10000
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/api/v1/tasks/task-1")
    ]
    client.close()


def test_blank_word_filter_keeps_strict_timecodes_for_real_words():
    payload = transcript()
    words = payload["transcripts"][0]["sentences"][0]["words"]
    words.insert(0, {"text": " ", "begin_time": 0, "end_time": 1})
    words[1]["end_time"] = 1001
    with pytest.raises(ValueError, match="word timecodes"):
        normalize_transcript(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["output"].update(task_id="other"),
        lambda p: p["output"]["results"][0].update(subtask_status="RUNNING"),
        lambda p: p["usage"].update(duration=999999),
    ],
)
def test_poll_mismatch_cannot_be_success(mutation):
    cfg = configuration()
    payload = success()
    mutation(payload)
    client = BailianSpeechClient(
        cfg, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    adapter = BailianAsrAdapter(cfg, url_resolver=lambda _: None, client=client)
    result = adapter.poll(
        task_id="task-1",
        identity=asr_identity(cfg),
        audio_duration_ms=60000,
        reserved_cost_minor=2,
    )
    assert not isinstance(result, Succeeded)
    client.close()


def test_result_host_policy_upgrades_tls_only_on_allowed_host():
    cfg = configuration()
    assert result_url(
        "http://result.oss-ap-southeast-1.aliyuncs.com/a?Signature=keep", cfg
    ).startswith("https://")
    for url in [
        "https://localhost/a",
        "https://oss-ap-southeast-1.aliyuncs.com.evil.test/a",
        "http://user:pw@result.oss-ap-southeast-1.aliyuncs.com/a",
    ]:
        with pytest.raises(BailianSpeechError):
            result_url(url, cfg)


def test_split_and_join_preserve_order_and_frames():
    text = "Hello world. 中文句子。\n" * 90
    chunks = split_narration(text, 500)
    assert "".join(chunks) == text and max(map(len, chunks)) <= 500
    data = join_wav([wav(), wav()], 20000)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnframes() == 4800
    with pytest.raises(ValueError):
        join_wav([wav()], 100)
    with pytest.raises(ValueError):
        join_wav([wav()[:-10]], 20000)


@pytest.fixture
def tts_env(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/receipts.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    cfg = configuration(tts_receipt_root=str(tmp_path / "private"), tts_chunk_chars=8)
    yield engine, cfg
    engine.dispose()


class FakeTtsClient:
    calls = []

    def __init__(self, config):
        pass

    def close(self):
        pass

    def synthesize(self, text):
        self.calls.append(text)
        return {
            "request_id": "req-" + str(len(self.calls)),
            "usage": {"characters": len(text)},
            "output": {
                "audio": {"url": "https://result.oss-ap-southeast-1.aliyuncs.com/a.wav"}
            },
        }


async def fake_audio(*args, **kwargs):
    return wav()


def provider(engine, cfg, **kw):
    return BailianPremiumGuideTtsProvider(
        cfg,
        engine=engine,
        episode_id="episode-test",
        voice_profile="narrator_zh",
        max_audio_bytes=200000,
        client_factory=kw.pop("client_factory", FakeTtsClient),
        downloader=kw.pop("downloader", fake_audio),
        clock=lambda: NOW,
        **kw,
    )


def test_tts_restart_uses_cached_chunks_and_one_receipt_per_call(tts_env):
    engine, cfg = tts_env
    FakeTtsClient.calls = []
    first = asyncio.run(
        provider(engine, cfg).synthesize("你好，这是播客测试。Hello world.")
    )
    calls = len(FakeTtsClient.calls)
    second = asyncio.run(
        provider(engine, cfg).synthesize("你好，这是播客测试。Hello world.")
    )
    assert first.data == second.data and len(FakeTtsClient.calls) == calls > 1
    with Session(engine) as session:
        rows = session.exec(select(BailianTtsCallRecord)).all()
        assert len(rows) == calls and all(r.status == "succeeded" for r in rows)
        assert sum(r.actual_characters for r in rows) == len(
            "你好，这是播客测试。Hello world."
        )


def test_tts_unknown_keeps_reservation_blocks_changed_script(tts_env):
    engine, cfg = tts_env

    class TimeoutClient(FakeTtsClient):
        def synthesize(self, text):
            raise BailianSpeechError("transport_error", unknown=True)

    with pytest.raises(BailianSpeechError):
        asyncio.run(
            provider(engine, cfg, client_factory=TimeoutClient).synthesize("hello")
        )
    FakeTtsClient.calls = []
    with pytest.raises(BailianSpeechError, match="reconciliation"):
        asyncio.run(provider(engine, cfg).synthesize("changed script"))
    assert FakeTtsClient.calls == []
    with Session(engine) as session:
        row = session.exec(select(BailianTtsCallRecord)).one()
        assert row.status == "authorized" and row.cost_minor > 0


def test_tts_download_retry_does_not_repeat_paid_call(tts_env):
    engine, cfg = tts_env
    FakeTtsClient.calls = []

    async def broken(*a, **kw):
        raise BailianSpeechError("download_failed")

    with pytest.raises(BailianSpeechError):
        asyncio.run(provider(engine, cfg, downloader=broken).synthesize("hello"))
    result = asyncio.run(provider(engine, cfg).synthesize("hello"))
    assert len(FakeTtsClient.calls) == 1 and result.data == wav()


def test_tts_budget_gates_before_network(tts_env):
    engine, cfg = tts_env
    FakeTtsClient.calls = []
    with pytest.raises(BailianSpeechError, match="budget"):
        asyncio.run(
            provider(engine, replace(cfg, tts_per_run_budget_minor=1)).synthesize(
                "一二三四五六七八九十" * 5
            )
        )
    assert FakeTtsClient.calls == []


def test_observed_qwen_streaming_wav_length_is_normalized():
    import struct

    data = bytearray(wav())
    struct.pack_into("<I", data, 4, 0x7FFFFFBF)
    struct.pack_into("<I", data, 40, 0x7FFFFF9B)
    normalized = join_wav([bytes(data)], 20000)
    assert normalized == wav()
    with pytest.raises(ValueError):
        join_wav([bytes(data[:-1])], 20000)


def test_durable_asr_worker_mock_transport_end_to_end(tmp_path):
    from models.db import (
        ArticleRecord,
        SourceConfigRecord,
        PodcastSourceMediaSnapshotRecord,
        PodcastCostLedgerRecord,
        PodcastTextArtifactRecord,
    )
    from config import PodcastConfig
    from services.bailian_asr import BailianAsrWorkerBundle
    from services.podcast_asr_worker import AsrWorkerConfig
    from services.podcast_processing import enqueue_processing
    from services.podcast_processing_inputs import processing_input_fingerprint
    import hashlib

    engine = create_engine(f"sqlite:///{tmp_path}/asr.db")
    SQLModel.metadata.create_all(engine)
    cfg = configuration()
    url = "https://example.com/audio.mp3"
    content_hash = "a" * 64
    podcast = PodcastConfig(
        installation="external",
        authority_id="test",
        allowed_stages=("fetch", "asr"),
        processing_enabled=True,
        provider_ready_targets=("transcript",),
        budget_scope="test",
        monthly_budget_cny_minor=100,
        per_run_budget_cny_minor=100,
    )
    policy = PodcastStagePolicy(podcast, bailian_speech=cfg)
    with Session(engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="sample",
                name="Sample",
                source_type="podcast",
                url="https://example.com/feed",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                created_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
            )
        )
        session.add(
            ArticleRecord(
                id="sample",
                title="Sample",
                content_type="podcast_episode",
                source_id="sample",
                source_url="https://example.com",
                publish_date=NOW.isoformat(),
                fetched_date=NOW.isoformat(),
                extensions_json=json.dumps({"audio_url": url}),
            )
        )
        session.add(
            PodcastSourceMediaSnapshotRecord(
                id="media",
                episode_id="sample",
                content_hash=content_hash,
                mime="audio/mpeg",
                size_bytes=100,
                duration_seconds=60,
                locator_hash=hashlib.sha256(url.encode()).hexdigest(),
                created_at=NOW.isoformat(),
            )
        )
        session.commit()
        fingerprint = processing_input_fingerprint(
            episode_id="sample",
            entry_stage="asr",
            artifact_id="media",
            content_hash=content_hash,
            kind="source_media_snapshot",
            language="und",
            audio_duration_ms=60000,
            admission_fingerprint=admission_fingerprint(cfg),
        )
        enqueue_processing(
            session,
            episode_id="sample",
            stage="asr",
            input_fingerprint=fingerprint,
            pipeline_version="test",
            policy_version="test",
            requested_target="transcript",
            idempotency_key="test",
            estimated_cost_minor=2,
            input_artifact_id="media",
            input_artifact_kind="source_media_snapshot",
            input_content_hash=content_hash,
            input_language="und",
            budget_scope="test",
            budget_period="2026-09",
            budget_limit_minor=100,
            per_run_budget_minor=100,
            policy=policy,
            now=NOW,
        )
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                200, json={"output": {"task_id": "task-1", "task_status": "PENDING"}}
            )
        payload = success()
        payload["usage"]["duration"] = 28
        return httpx.Response(200, json=payload)

    async def download(*a, **kw):
        return json.dumps(transcript()).encode()

    factory = lambda cfg: BailianSpeechClient(
        cfg, transport=httpx.MockTransport(handle)
    )
    worker_config = AsrWorkerConfig(
        worker_id="test",
        lease_seconds=300,
        fallback_retry_seconds=10,
        next_stage_by_target={"transcript": None},
    )
    with Session(engine) as session:
        first = BailianAsrWorkerBundle(
            client_factory=factory, downloader=download, clock=lambda: NOW
        )(session, config=worker_config, policy=policy)
        assert first.action == "poll_scheduled"
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with engine.begin() as connection:
        with pytest.raises(
            IntegrityError, match="invalid podcast budget reservation transition"
        ):
            connection.execute(
                text("UPDATE podcast_budget_reservations SET minimum_usage_units=1")
            )
    # A restart with rotated credentials and expired new-call authorization
    # still drains the already submitted task under its frozen identity.
    cfg = replace(
        cfg,
        api_key="rotated",
        asr_entitlement_ends_at=(NOW - dt.timedelta(days=1)).isoformat(),
    )
    policy = PodcastStagePolicy(podcast, bailian_speech=cfg)
    with Session(engine) as session:
        second = BailianAsrWorkerBundle(
            client_factory=factory,
            downloader=download,
            clock=lambda: NOW + dt.timedelta(seconds=11),
        )(session, config=worker_config, policy=policy)
        assert second.action == "completed"
        ledger = session.exec(select(PodcastCostLedgerRecord)).one()
        assert ledger.actual_usage_units == 28 and ledger.actual_cost_minor == 1
        artifact = session.exec(select(PodcastTextArtifactRecord)).one()
        assert json.loads(artifact.inline_text)["text"] == "测试。"
    assert calls == ["POST", "GET"]
    engine.dispose()


def test_input_and_billable_duration_have_independent_validated_units():
    from services.podcast_worker_contracts import NormalizedUsage

    usage = NormalizedUsage(audio_duration_ms=30240, billed_audio_duration_ms=28000)
    assert usage.audio_duration_ms == 30240
    with pytest.raises(ValueError):
        NormalizedUsage(audio_duration_ms=30240, billed_audio_duration_ms=32000)
    with pytest.raises(ValueError):
        NormalizedUsage(audio_duration_ms=30240, billed_audio_duration_ms=True)
    plan = usage_plan(configuration(), audio_duration_ms=30240, now=NOW)
    assert plan.minimum_units == 0 and plan.reserved_units == 31
    with pytest.raises(ValueError):
        replace(plan, minimum_units=32)
    from config import PodcastConfig

    policy = PodcastStagePolicy(PodcastConfig(), bailian_speech=configuration())
    assert not policy.provider_usage_plan_matches(
        "aliyun-bailian", "asr", replace(plan, minimum_units=None), now=NOW
    )


def test_tts_replay_needs_no_additional_disk_reservation(tts_env):
    engine, cfg = tts_env
    FakeTtsClient.calls = []
    first = asyncio.run(provider(engine, cfg).synthesize("hello"))
    # Existing checked audio remains usable after reducing the cache allowance.
    second = asyncio.run(
        provider(engine, replace(cfg, tts_cache_max_bytes=1)).synthesize("hello")
    )
    assert first.data == second.data and len(FakeTtsClient.calls) == 1


def test_tts_concurrent_duplicate_calls_share_one_receipt(tts_env):
    engine, cfg = tts_env
    FakeTtsClient.calls = []

    async def run():
        return await asyncio.gather(
            provider(engine, cfg).synthesize("hello"),
            provider(engine, cfg).synthesize("hello"),
        )

    a, b = asyncio.run(run())
    assert a.data == b.data and len(FakeTtsClient.calls) == 1


def test_asr_admission_is_independent_of_tts_and_secret():
    cfg = configuration()
    assert admission_fingerprint(cfg) == admission_fingerprint(
        replace(
            cfg,
            api_key="rotated",
            tts_voice="Ethan",
            tts_chunk_chars=200,
            tts_monthly_budget_minor=500,
            tts_receipt_root="elsewhere",
        )
    )
    assert admission_fingerprint(cfg) != admission_fingerprint(
        replace(cfg, account_scope="other")
    )
