from __future__ import annotations

import datetime as dt
import hashlib
import inspect
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import asyncio
import wave

import pytest
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.podcast_artifacts import PodcastArtifactStore
from services.podcast_premium import dashboard
from services.podcast_premium_guides import (
    CAPACITY_RETRY_MAX_ATTEMPTS,
    PremiumGuideDraft,
    PremiumGuideForceError,
    SynthesizedAudio,
    _set_episode_status,
    fail_premium_guide,
    list_premium_guide_tasks,
    pending_premium_guide_candidates,
    premium_guide_auto_schedule_allowed,
    prepare_forced_premium_guide,
    run_premium_guide,
)
from storage.impl.db_storage import DatabaseStorage
from api.routers.podcasts import run_podcast_premium_guide as run_premium_guide_endpoint


STAMP = "2026-09-07T00:00:00+00:00"
PREMIUM_STAGES = (
    "fetch",
    "asr",
    "translate",
    "analyze",
    "digest",
    "script",
    "tts",
    "audio_qa",
    "local_publish",
)


def _external_config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id="podcast-external-test",
        allowed_stages=PREMIUM_STAGES,
        premium_score_threshold=8.5,
    )


def test_premium_guide_run_endpoint_keeps_the_request_event_loop():
    assert inspect.iscoroutinefunction(run_premium_guide_endpoint)


def test_premium_guide_failure_retains_the_actionable_stage(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium-status.db'}")
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="episode-status",
                title="Status episode",
                content_type="podcast_episode",
                source_id="podcast-status",
                source_url="https://example.test/status",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
            )
        )
        session.commit()

    _set_episode_status(sink.engine, "episode-status", "synthesizing")
    _set_episode_status(
        sink.engine,
        "episode-status",
        "failed",
        error="TTS provider timeout",
    )

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-status")
        guide = json.loads(episode.extensions_json)["premium_guide"]
        assert guide["status"] == "failed"
        assert guide["failed_stage"] == "synthesizing"
        assert guide["error"] == "TTS provider timeout"


def test_tts_retry_reuses_valid_published_text_without_llm_regeneration(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'retry.db'}")
    _seed_force_candidate(sink)
    store = PodcastArtifactStore(
        sink.engine, tmp_path / "retry-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}', stderr="",
        ),
    )

    class CountingText(TextProvider):
        blogs = 0
        scripts = 0

        async def create_blog(self, **kwargs):
            self.blogs += 1
            return await super().create_blog(**kwargs)

        async def create_narration(self, **kwargs):
            self.scripts += 1
            return await super().create_narration(**kwargs)

    class FailingTts(TtsProvider):
        async def synthesize(self, _text):
            raise RuntimeError("capacity blocked")

    text = CountingText()
    kwargs = dict(engine=sink.engine, store=store, episode_id="episode-force",
                  config=_external_config(), text_provider=text, selection_override=True)
    with pytest.raises(RuntimeError, match="capacity blocked"):
        asyncio.run(run_premium_guide(**kwargs, tts_provider=FailingTts()))
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        assert guide["status"] == "failed"
        assert "next_retry_at" not in guide
    with Session(sink.engine) as session:
        script = session.get(PodcastTextPublicationRecord, "episode-force:narration_script_zh").artifact_id
    result = asyncio.run(run_premium_guide(**kwargs, tts_provider=TtsProvider()))
    assert result["audio_artifact_id"]
    assert (text.blogs, text.scripts) == (1, 1)
    with Session(sink.engine) as session:
        assert session.get(PodcastTextPublicationRecord, "episode-force:narration_script_zh").artifact_id == script


def test_ordinary_rerun_reuses_matching_published_audio_without_tts(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'published-audio-reuse.db'}")
    _seed_force_candidate(sink)
    store = PodcastArtifactStore(
        sink.engine, tmp_path / "published-audio-reuse", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0,
            stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}',
            stderr="",
        ),
    )

    class CountingTts(TtsProvider):
        calls = 0

        async def synthesize(self, text):
            self.calls += 1
            return await super().synthesize(text)

    tts = CountingTts()
    kwargs = dict(
        engine=sink.engine,
        store=store,
        episode_id="episode-force",
        config=_external_config(),
        text_provider=TextProvider(),
        tts_provider=tts,
        selection_override=True,
    )
    first = asyncio.run(run_premium_guide(**kwargs))
    second = asyncio.run(run_premium_guide(**kwargs))

    assert second["audio_artifact_id"] == first["audio_artifact_id"]
    assert tts.calls == 1
    with Session(sink.engine) as session:
        audio_rows = session.exec(
            select(PodcastArtifactRecord).where(
                PodcastArtifactRecord.episode_id == "episode-force",
                PodcastArtifactRecord.kind == "digest_audio_zh",
            )
        ).all()
        assert [(row.id, row.status) for row in audio_rows] == [
            (first["audio_artifact_id"], "published")
        ]


def test_tts_cache_preflight_blocks_before_text_generation(tmp_path):
    from services.bailian_speech_client import BailianSpeechError

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'preflight.db'}")
    _seed_force_candidate(sink)

    class NoText(TextProvider):
        async def create_blog(self, **_kwargs):
            pytest.fail("LLM must not run when the TTS cache is blocked")

    class NoTts(TtsProvider):
        async def check_capacity(self, _characters):
            raise BailianSpeechError("tts_receipt_cache_full")

        async def synthesize(self, _text):
            pytest.fail("TTS must not be submitted")

    with pytest.raises(BailianSpeechError, match="tts_receipt_cache_full"):
        asyncio.run(run_premium_guide(
            sink.engine, None, episode_id="episode-force", config=_external_config(),
            text_provider=NoText(), tts_provider=NoTts(), selection_override=True,
        ))
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        assert guide["status"] == "retry_wait"
        assert guide["error_code"] == "tts_receipt_cache_full"
        assert guide["next_retry_at"]
        assert guide["failed_stage"] == "synthesizing"
        assert "回执缓存不足" in guide["error"]
        assert session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh") is None


def test_tts_cache_is_checked_again_before_the_narration_is_written(tmp_path):
    """导读正文已有、口播稿还没写时,容量不足同样先于付费的文本生成被发现。"""
    from services.bailian_speech_client import BailianSpeechError

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'second-check.db'}")
    _seed_force_candidate(sink)
    checks = []

    class NoNarration(TextProvider):
        async def create_narration(self, **_kwargs):
            pytest.fail("the narration must not be written when the TTS cache is blocked")

    class FullOnSecondCheck(TtsProvider):
        async def check_capacity(self, characters):
            checks.append(characters)
            if len(checks) == 2:
                raise BailianSpeechError("tts_receipt_cache_full")

        async def synthesize(self, _text):
            pytest.fail("TTS must not be submitted")

    with pytest.raises(BailianSpeechError, match="tts_receipt_cache_full"):
        asyncio.run(run_premium_guide(
            sink.engine, None, episode_id="episode-force", config=_external_config(),
            text_provider=NoNarration(), tts_provider=FullOnSecondCheck(), selection_override=True,
        ))
    assert len(checks) == 2
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        assert guide["status"] == "retry_wait"
        assert guide["error_code"] == "tts_receipt_cache_full"
        assert guide["failed_stage"] == "synthesizing"
        assert session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh") is not None
        assert session.get(PodcastTextPublicationRecord, "episode-force:narration_script_zh") is None


def _pcm_wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 2400)
    return out.getvalue()


@pytest.mark.parametrize("code", [
    "tts_receipt_cache_full", "tts_receipt_disk_full", "tts_monthly_budget_exceeded",
    "http_403", "tts_per_run_budget_exceeded",
])
def test_only_capacity_and_monthly_budget_errors_enter_retry_wait(tmp_path, code):
    from services.bailian_speech_client import BailianSpeechError

    sink = DatabaseStorage(f"sqlite:///{tmp_path / f'status-{code}.db'}")
    _seed_force_candidate(sink, duration=21 * 60)
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-force")
        analysis.quality_score = 9.0
        analysis.podcast_final_score = 9.0
        session.add(analysis)
        session.commit()

    class Boom(TtsProvider):
        async def synthesize(self, _text):
            raise BailianSpeechError(code)

    with pytest.raises(BailianSpeechError, match=code):
        asyncio.run(run_premium_guide(
            sink.engine, None, episode_id="episode-force", config=_external_config(),
            text_provider=TextProvider(), tts_provider=Boom(),
            budget_wait=True,  # 巡检拉起的自动运行
        ))
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        blog = session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh")
        script = session.get(PodcastTextPublicationRecord, "episode-force:narration_script_zh")
    assert blog is not None and script is not None
    minimum = _external_config().premium_min_duration_seconds
    threshold = _external_config().premium_score_threshold
    if code in {"http_403", "tts_per_run_budget_exceeded"}:
        assert guide["status"] == "failed"
        assert "next_retry_at" not in guide
        assert "episode-force" not in pending_premium_guide_candidates(
            sink.engine, minimum_duration_seconds=minimum, score_threshold=threshold,
        )
    else:
        assert guide["status"] == "retry_wait"
        assert guide["error_code"] == code
        assert "episode-force" not in pending_premium_guide_candidates(
            sink.engine, minimum_duration_seconds=minimum, score_threshold=threshold,
        )


def test_capacity_wait_retries_tts_without_another_supplier_or_llm_call(tmp_path):
    from config_bailian import BailianSpeechConfig
    from models.db import BailianTtsCallRecord
    from services.bailian_speech_client import BailianSpeechError
    from services.bailian_tts import BailianPremiumGuideTtsProvider, split_narration

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'capacity-retry.db'}")
    _seed_force_candidate(sink, duration=21 * 60)
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-force")
        analysis.quality_score = 9.0
        analysis.podcast_final_score = 9.0
        session.add(analysis)
        session.commit()
    narration = "这是内部中文播客导读。"
    assert len(split_narration(narration, 8)) == 2
    root = tmp_path / "receipts"
    cfg = BailianSpeechConfig(
        enabled=True, api_key="secret-test", account_scope="test-account",
        tts_monthly_budget_minor=100, tts_per_run_budget_minor=50,
        tts_chunk_chars=8, tts_receipt_root=str(root), tts_cache_max_bytes=40_000_000,
        asr_entitlement_ends_at="2027-01-01T00:00:00+00:00",
    )
    poisoned = {"done": False}

    class Supplier:
        texts: list[str] = []

        def __init__(self, _config):
            pass

        def close(self):
            pass

        def synthesize(self, text):
            Supplier.texts.append(text)
            return {
                "request_id": "req-" + str(len(Supplier.texts)),
                "usage": {"characters": len(text)},
                "output": {"audio": {"url": "https://result.oss-ap-southeast-1.aliyuncs.com/a.wav"}},
            }

    async def download(*_args, **_kwargs):
        if len(Supplier.texts) >= 2 and not poisoned["done"]:
            poisoned["done"] = True
            (root / "blocker").write_bytes(b"x" * cfg.tts_cache_max_bytes)
        return _pcm_wav()

    class CountingText(TextProvider):
        blogs = 0
        scripts = 0

        async def create_blog(self, **kwargs):
            self.blogs += 1
            return await super().create_blog(**kwargs)

        async def create_narration(self, **_kwargs):
            self.scripts += 1
            return narration

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}', stderr="",
        ),
    )
    text = CountingText()
    tts = BailianPremiumGuideTtsProvider(
        cfg, engine=sink.engine, episode_id="episode-force", voice_profile="narrator_zh",
        max_audio_bytes=store.max_bytes, client_factory=Supplier, downloader=download,
    )
    config = _external_config()
    kwargs = dict(
        engine=sink.engine, store=store, episode_id="episode-force", config=config,
        text_provider=text, tts_provider=tts,
    )
    assert pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == ["episode-force"]
    with pytest.raises(BailianSpeechError, match="tts_receipt_cache_full"):
        asyncio.run(run_premium_guide(**kwargs))
    assert (text.blogs, text.scripts) == (1, 1)
    assert len(Supplier.texts) == 2
    assert (root / "blocker").is_file()
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        first_ids = {
            row.id: row.request_id
            for row in session.exec(select(BailianTtsCallRecord)).all()
        }
    assert guide["status"] == "retry_wait"
    assert guide["error_code"] == "tts_receipt_cache_full"
    assert guide["retry_attempts"] == 1
    retry_at = dt.datetime.fromisoformat(guide["next_retry_at"])
    remaining = retry_at - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(seconds=300 - 15) <= remaining <= dt.timedelta(seconds=300 + 5)
    assert "episode-force" not in pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        extensions = json.loads(episode.extensions_json)
        extensions["premium_guide"]["next_retry_at"] = "2020-01-01T00:00:00+00:00"
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    assert pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == ["episode-force"]
    (root / "blocker").unlink()
    result = asyncio.run(run_premium_guide(**kwargs))
    assert result["audio_artifact_id"]
    assert (text.blogs, text.scripts) == (1, 1)
    assert len(Supplier.texts) == 2
    with Session(sink.engine) as session:
        guide = json.loads(session.get(ArticleRecord, "episode-force").extensions_json)["premium_guide"]
        rows = session.exec(select(BailianTtsCallRecord)).all()
        assert guide["status"] == "ready"
        assert "retry_attempts" not in guide
        assert "next_retry_at" not in guide
        assert "error_code" not in guide
        assert {row.id: row.request_id for row in rows} == first_ids
        assert {row.status for row in rows} == {"succeeded"}


# ── issue #326:回执音频被逐出之后,同一份口播稿改用成品 ─────────────────────


def _evictable_guide(tmp_path, name):
    """一期跑得通完整导读流程的节目,配真实的语音回执与产物库。"""

    from config_bailian import BailianSpeechConfig
    from services.bailian_speech_client import BailianSpeechError
    from services.bailian_tts import BailianPremiumGuideTtsProvider

    sink = _eligible_force_sink(tmp_path, name)
    root = tmp_path / "receipts"
    cfg = BailianSpeechConfig(
        enabled=True, api_key="secret-test", account_scope="test-account",
        tts_monthly_budget_minor=100, tts_per_run_budget_minor=50,
        tts_chunk_chars=8, tts_receipt_root=str(root), tts_cache_max_bytes=40_000_000,
        asr_entitlement_ends_at="2027-01-01T00:00:00+00:00",
    )
    state = {"posts": 0, "url_expired": False, "frames": 2400}

    class Supplier:
        def __init__(self, _config):
            pass

        def close(self):
            pass

        def synthesize(self, text):
            state["posts"] += 1
            return {
                "request_id": f"req-{state['posts']}",
                "usage": {"characters": len(text)},
                "output": {"audio": {"url": "https://result.oss-ap-southeast-1.aliyuncs.com/a.wav"}},
            }

    async def download(*_args, **_kwargs):
        if state["url_expired"]:
            raise BailianSpeechError("result_download_failed")
        out = io.BytesIO()
        with wave.open(out, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x00\x00" * state["frames"])
        return out.getvalue()

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}', stderr="",
        ),
    )
    tts = BailianPremiumGuideTtsProvider(
        cfg, engine=sink.engine, episode_id="episode-force", voice_profile="narrator_zh",
        max_audio_bytes=store.max_bytes, client_factory=Supplier, downloader=download,
    )
    kwargs = dict(
        engine=sink.engine, store=store, episode_id="episode-force", config=_external_config(),
        text_provider=TextProvider(), tts_provider=tts,
    )
    return sink, store, root, state, kwargs


def _evict_receipt_audio(sink, root, state):
    """节目有了成品之后回执音频被逐出;供应商的临时地址随后过期。"""

    from models.db import BailianTtsCallRecord
    from services.bailian_tts import _drop_audio

    with Session(sink.engine) as session:
        rows = session.exec(select(BailianTtsCallRecord)).all()
        ledger = sorted((row.id, row.status, row.cost_minor, row.audio_hash) for row in rows)
    for key, *_rest in ledger:
        _drop_audio(root, key, root / f"{key}.wav")
    state["url_expired"] = True
    return ledger


def _ledger_rows(sink):
    from models.db import BailianTtsCallRecord

    with Session(sink.engine) as session:
        return sorted(
            (row.id, row.status, row.cost_minor, row.audio_hash)
            for row in session.exec(select(BailianTtsCallRecord)).all()
        )


def test_evicted_receipts_fall_back_to_the_withdrawn_audio_of_the_same_narration(tmp_path):
    sink, store, root, state, kwargs = _evictable_guide(tmp_path, "evicted-withdrawn.db")
    first = asyncio.run(run_premium_guide(**kwargs))
    posts = state["posts"]
    original = store.get(first["audio_artifact_id"])
    ledger = _evict_receipt_audio(sink, root, state)

    # 管理端撤回成品后重跑:成品文件还完好,回执却重放不了。
    store.withdraw(original.id)
    again = asyncio.run(run_premium_guide(**kwargs))

    restored = store.get(again["audio_artifact_id"])
    assert restored.id != original.id and restored.status == "published"
    assert restored.content_hash == original.content_hash
    assert restored.narration_artifact_id == original.narration_artifact_id
    assert _force_guide(sink)["status"] == "ready"
    # 没有再调供应商,账本一行没动。
    assert state["posts"] == posts
    assert _ledger_rows(sink) == ledger


def test_evicted_receipts_fall_back_when_the_same_text_returns_as_a_new_narration(tmp_path):
    """流程版本变了、重新生成的口播文本恰好一样:口播稿换了记录,旧成品被撤回。"""

    sink, store, root, state, kwargs = _evictable_guide(tmp_path, "evicted-renarrated.db")
    first = asyncio.run(run_premium_guide(**kwargs))
    posts = state["posts"]
    original = store.get(first["audio_artifact_id"])
    ledger = _evict_receipt_audio(sink, root, state)

    bumped = replace(kwargs["config"], text_pipeline_version="next-pipeline")
    again = asyncio.run(run_premium_guide(**{**kwargs, "config": bumped}))

    restored = store.get(again["audio_artifact_id"])
    assert store.get(original.id).status == "withdrawn"
    assert restored.status == "published" and restored.content_hash == original.content_hash
    assert restored.narration_artifact_id != original.narration_artifact_id
    assert restored.narration_content_hash == original.narration_content_hash
    assert state["posts"] == posts
    assert _ledger_rows(sink) == ledger


def test_evicted_receipts_without_an_intact_audio_of_that_text_surface_the_error(tmp_path):
    """只认同一份口播文本的完好成品:别的稿子的音频不能顶替,找不到就如实报错。"""

    from services.bailian_speech_client import BailianSpeechError

    sink, store, root, state, kwargs = _evictable_guide(tmp_path, "evicted-lost.db")
    earlier = store.get(asyncio.run(run_premium_guide(**kwargs))["audio_artifact_id"])

    class Rewritten(TextProvider):
        async def create_narration(self, **_kwargs):
            return "这是改写之后的另一份导读。"

    # 口播稿改写过一次:新稿合成并发布,旧稿的成品被撤回(文件还在)。
    state["frames"] = 4800
    current_kwargs = {
        **kwargs,
        "config": replace(kwargs["config"], text_pipeline_version="next-pipeline"),
        "text_provider": Rewritten(),
    }
    current = store.get(asyncio.run(run_premium_guide(**current_kwargs))["audio_artifact_id"])
    assert store.get(earlier.id).status == "withdrawn" and store.is_intact(earlier)
    assert current.content_hash != earlier.content_hash
    posts = state["posts"]

    _evict_receipt_audio(sink, root, state)
    store.withdraw(current.id)
    store.file_path_for(current).unlink()  # 当前这份稿子的成品文件也没了

    ledger = _ledger_rows(sink)
    with pytest.raises(BailianSpeechError, match="tts_cached_audio_evicted"):
        asyncio.run(run_premium_guide(**current_kwargs))
    guide = _force_guide(sink)
    assert guide["status"] == "failed" and "tts_cached_audio_evicted" in guide["error"]
    assert state["posts"] == posts and _ledger_rows(sink) == ledger  # 不重新付费合成
    with Session(sink.engine) as session:
        assert not session.exec(select(PodcastArtifactRecord).where(
            PodcastArtifactRecord.status.in_(("ready", "published")),
        )).all()


def test_evicted_receipts_fall_back_for_the_shorter_second_narration_too(tmp_path):
    """长稿超时长 → 改短稿发布 → 两份回执都被逐出 → 换版本后重新生成同样的两份稿。

    重跑把每一步都再走一遍:长稿用成品补上、仍然超时长、再到短稿——短稿的那次合成
    也得走恢复路径,否则完好的短稿成品在库里,运行却失败,旧发布又已被撤回。
    """

    sink, store, root, state, kwargs = _evictable_guide(tmp_path, "evicted-shortened.db")
    long_text, short_text = "长稿长稿长稿长稿长稿长稿", "短稿"

    class Shortening(TextProvider):
        async def create_narration(self, **options):
            return short_text if options.get("retry_shorter") else long_text

    # 探测时长跟着音频大小走:长稿两段(约 9.6KB)超过上限,短稿一段(约 4.8KB)不超。
    def probe(command, **_kwargs):
        seconds = 5000 if Path(command[-1]).stat().st_size > 6_000 else 1
        return subprocess.CompletedProcess(
            [], 0, stderr="",
            stdout=json.dumps({"streams": [{"codec_type": "audio", "duration": str(seconds)}],
                               "format": {"duration": str(seconds)}}),
        )

    store._probe_runner = probe
    kwargs = {**kwargs, "text_provider": Shortening()}
    first = asyncio.run(run_premium_guide(**kwargs))
    published = store.get(first["audio_artifact_id"])
    with Session(sink.engine) as session:
        narrations = {
            row.id: row.inline_text for row in session.exec(select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.kind == "narration_script_zh",
            )).all()
        }
    assert narrations[published.narration_artifact_id] == short_text
    assert sorted(narrations.values()) == sorted([long_text, short_text])
    posts = state["posts"]
    ledger = _evict_receipt_audio(sink, root, state)

    bumped = replace(kwargs["config"], text_pipeline_version="next-pipeline")
    again = asyncio.run(run_premium_guide(**{**kwargs, "config": bumped}))

    restored = store.get(again["audio_artifact_id"])
    assert restored.status == "published" and restored.content_hash == published.content_hash
    assert restored.narration_artifact_id != published.narration_artifact_id
    assert _force_guide(sink)["status"] == "ready"
    assert state["posts"] == posts and _ledger_rows(sink) == ledger


def test_evicted_receipts_use_the_audio_another_worker_just_published(tmp_path):
    """两个进程同时重试:一个从缓存发布完,缓存被逐出,另一个才轮到合成。"""

    from services.bailian_speech_client import BailianSpeechError

    sink, store, _root, _state, kwargs = _evictable_guide(tmp_path, "evicted-raced.db")
    published_by_other = {}

    class LateWorker:
        async def synthesize(self, _text):
            with Session(sink.engine) as session:
                pointer = session.get(
                    PodcastTextPublicationRecord, "episode-force:narration_script_zh"
                )
                narration = session.get(PodcastTextArtifactRecord, pointer.artifact_id)
                binding = dict(
                    narration_artifact_id=narration.id,
                    narration_content_hash=narration.content_hash,
                )
            other = store.import_bytes(
                episode_id="episode-force", kind="digest_audio_zh", data=_pcm_wav(),
                declared_mime="audio/wav", provenance="premium_guide_tts", authority_id="",
                **binding,
            )
            published_by_other["id"] = store.publish(
                other.id, expected_updated_at=other.updated_at
            ).id
            raise BailianSpeechError("tts_cached_audio_evicted")

    result = asyncio.run(run_premium_guide(**{**kwargs, "tts_provider": LateWorker()}))

    audio = store.get(result["audio_artifact_id"])
    assert audio.status == "published"
    assert audio.content_hash == store.get(published_by_other["id"]).content_hash
    assert _force_guide(sink)["status"] == "ready"


def _force_guide(sink: DatabaseStorage) -> dict:
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        return json.loads(episode.extensions_json)["premium_guide"]


def _eligible_force_sink(tmp_path, name: str) -> DatabaseStorage:
    sink = DatabaseStorage(f"sqlite:///{tmp_path / name}")
    _seed_force_candidate(sink, duration=21 * 60)
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-force")
        analysis.quality_score = 9.0
        analysis.podcast_final_score = 9.0
        session.add(analysis)
        session.commit()
    return sink


def test_capacity_retry_backs_off_and_stops_after_eight_attempts(tmp_path):
    from services.bailian_speech_client import BailianSpeechError

    sink = _eligible_force_sink(tmp_path, "backoff.db")
    config = _external_config()
    error = BailianSpeechError("tts_receipt_cache_full")
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )

    fail_premium_guide(sink.engine, "episode-force", error)
    fail_premium_guide(sink.engine, "episode-force", error)
    guide = _force_guide(sink)
    assert guide["status"] == "retry_wait"
    assert guide["retry_attempts"] == 1
    assert guide["error_code"] == "tts_receipt_cache_full"
    remaining = dt.datetime.fromisoformat(guide["next_retry_at"]) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(seconds=300 - 15) <= remaining <= dt.timedelta(seconds=300 + 5)
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)

    expected = {2: 30 * 60, 3: 2 * 60 * 60, 4: 6 * 60 * 60}
    for attempt, delay in expected.items():
        _set_episode_status(sink.engine, "episode-force", "synthesizing")
        fail_premium_guide(sink.engine, "episode-force", error)
        guide = _force_guide(sink)
        assert guide["retry_attempts"] == attempt
        assert guide["status"] == "retry_wait"
        remaining = dt.datetime.fromisoformat(guide["next_retry_at"]) - dt.datetime.now(dt.timezone.utc)
        assert dt.timedelta(seconds=delay - 15) <= remaining <= dt.timedelta(seconds=delay + 5)

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        extensions = json.loads(episode.extensions_json)
        extensions["premium_guide"]["status"] = "synthesizing"
        extensions["premium_guide"]["retry_attempts"] = CAPACITY_RETRY_MAX_ATTEMPTS
        extensions["processing_status"] = "synthesizing"
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    fail_premium_guide(sink.engine, "episode-force", error)
    fail_premium_guide(sink.engine, "episode-force", error)
    guide = _force_guide(sink)
    assert guide["status"] == "failed"
    assert guide["error_code"] == "tts_receipt_cache_full"
    assert guide["retry_attempts"] == CAPACITY_RETRY_MAX_ATTEMPTS + 1
    assert "next_retry_at" not in guide
    assert "回执缓存不足" in guide["error"]
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)


def test_exhausted_monthly_budget_waits_without_an_attempt_cap(tmp_path):
    """月度预算用完会自己恢复(换月 / 调额 / 别的合成结算):等待后重试,不设次数上限。"""

    from services.bailian_speech_client import BailianSpeechError
    from services.podcast_premium_guides import (
        BUDGET_RETRY_DELAY_SECONDS,
        BUDGET_WAIT_CODES,
    )

    sink = _eligible_force_sink(tmp_path, "budget-wait.db")
    config = _external_config()
    error = BailianSpeechError("tts_monthly_budget_exceeded")
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )

    _set_episode_status(sink.engine, "episode-force", "synthesizing")
    fail_premium_guide(sink.engine, "episode-force", error, budget_wait=True)  # run_premium_guide 记一次
    first_retry_at = _force_guide(sink)["next_retry_at"]
    fail_premium_guide(sink.engine, "episode-force", error, budget_wait=True)  # 调度器再记一次
    guide = _force_guide(sink)
    assert guide["status"] == "retry_wait"
    assert guide["error_code"] == "tts_monthly_budget_exceeded"
    assert guide["failed_stage"] == "synthesizing"
    assert guide["next_retry_at"] == first_retry_at  # 第二次记录没有把计时往后推
    assert "retry_attempts" not in guide  # 那是回执容量等待的计数,预算等待不碰
    assert guide["error"] == BUDGET_WAIT_CODES["tts_monthly_budget_exceeded"]
    remaining = dt.datetime.fromisoformat(guide["next_retry_at"]) - dt.datetime.now(dt.timezone.utc)
    assert (
        dt.timedelta(seconds=BUDGET_RETRY_DELAY_SECONDS - 15)
        <= remaining
        <= dt.timedelta(seconds=BUDGET_RETRY_DELAY_SECONDS + 5)
    )
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)

    # 远超回执容量那一类的 8 次上限,仍然只是等待。
    for _ in range(CAPACITY_RETRY_MAX_ATTEMPTS + 3):
        _set_episode_status(sink.engine, "episode-force", "synthesizing")
        fail_premium_guide(sink.engine, "episode-force", error, budget_wait=True)
        assert _force_guide(sink)["status"] == "retry_wait"

    # 等了这么多轮预算之后第一次遇到回执容量不足:容量的次数仍从 1 算起,不是直接用完。
    _set_episode_status(sink.engine, "episode-force", "synthesizing")
    fail_premium_guide(
        sink.engine, "episode-force", BailianSpeechError("tts_receipt_cache_full"), budget_wait=True
    )
    guide = _force_guide(sink)
    assert (guide["status"], guide["error_code"], guide["retry_attempts"]) == (
        "retry_wait", "tts_receipt_cache_full", 1,
    )
    _set_episode_status(sink.engine, "episode-force", "synthesizing")
    fail_premium_guide(sink.engine, "episode-force", error, budget_wait=True)
    guide = _force_guide(sink)
    assert (guide["error_code"], guide["retry_attempts"]) == ("tts_monthly_budget_exceeded", 1)

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        extensions = json.loads(episode.extensions_json)
        extensions["premium_guide"]["next_retry_at"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        ).isoformat()
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    assert pending_premium_guide_candidates(sink.engine, **scope) == ["episode-force"]


def _mark_failed(sink: DatabaseStorage, *, error: str, failed_stage: str, error_code: str = "") -> None:
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        extensions = json.loads(episode.extensions_json)
        guide = {"status": "failed", "error": error, "failed_stage": failed_stage, "mode": "solo_deep"}
        if error_code:
            guide["error_code"] = error_code
        extensions["premium_guide"] = guide
        extensions["processing_status"] = "failed"
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()


@pytest.mark.parametrize(("error", "failed_stage", "error_code", "restarted"), [
    # 旧代码把「月度预算已满」记成的终态:再拉起一次。
    ("Bailian speech: tts_monthly_budget_exceeded", "synthesizing", "", True),
    # 其余终态原样不动:供应商拒绝、旧的回执存储满、文字阶段的失败、已是终态的容量失败。
    ("Bailian speech: http_404:InvalidParameter", "synthesizing", "", False),
    ("Bailian speech: tts_receipt_storage_full", "synthesizing", "", False),
    ("Bailian speech: tts_per_run_budget_exceeded", "synthesizing", "", False),
    ("Bailian speech: tts_monthly_budget_exceeded", "summarizing", "", False),
    ("TTS 回执缓存不足", "synthesizing", "tts_receipt_cache_full", False),
])
def test_only_legacy_budget_failures_are_started_once_more(
    tmp_path, error, failed_stage, error_code, restarted
):
    from services.bailian_speech_client import BailianSpeechError

    sink = _eligible_force_sink(tmp_path, "legacy.db")
    config = _external_config()
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    _mark_failed(sink, error=error, failed_stage=failed_stage, error_code=error_code)
    picked = pending_premium_guide_candidates(sink.engine, **scope)
    assert picked == (["episode-force"] if restarted else [])
    if not restarted:
        return
    # 再拉起的那一次若预算仍不够,落成带错误码的等待——不再匹配「旧终态」,不会被反复拉起。
    _set_episode_status(sink.engine, "episode-force", "synthesizing")
    fail_premium_guide(
        sink.engine, "episode-force", BailianSpeechError("tts_monthly_budget_exceeded"),
        budget_wait=True,
    )
    assert _force_guide(sink)["status"] == "retry_wait"
    assert pending_premium_guide_candidates(sink.engine, **scope) == []


@pytest.mark.parametrize("scenario", ["forced_below_threshold", "manual_run_below_auto_duration"])
def test_explicit_runs_fail_for_good_on_an_exhausted_budget_instead_of_waiting(tmp_path, scenario):
    """到期的等待只有巡检能拉起;巡检拉不起来的单集一旦进入等待就永远停在「生成中」。

    两种拉不起来的情形:强制 / 点播一期达不到自动门槛的节目;管理端手动运行一期
    短于「自动生成时长」的节目。它们遇到预算满仍是终态,可以再发起。
    """

    from services.bailian_speech_client import BailianSpeechError
    from services.podcast_premium_guides import evaluate_reader_ondemand_premium_guide

    sink = DatabaseStorage(f"sqlite:///{tmp_path / f'explicit-{scenario}.db'}")
    _seed_force_candidate(sink, duration=30 * 60)
    forced = scenario == "forced_below_threshold"
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-force")
        analysis.quality_score = analysis.podcast_final_score = 7.2 if forced else 9.0
        session.add(analysis)
        session.commit()
    config = replace(
        _external_config(),
        premium_min_duration_seconds=20 * 60 if forced else 60 * 60,
    )
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    assert pending_premium_guide_candidates(sink.engine, **scope) == []  # 巡检本来就不会碰它

    class OutOfBudget(TtsProvider):
        async def synthesize(self, _text):
            raise BailianSpeechError("tts_monthly_budget_exceeded")

    with pytest.raises(BailianSpeechError, match="tts_monthly_budget_exceeded"):
        asyncio.run(run_premium_guide(
            sink.engine, None, episode_id="episode-force", config=config,
            text_provider=TextProvider(), tts_provider=OutOfBudget(),
            selection_override=forced,
        ))
    # 外层调度按同一口径再记一次,不能把终态改成等待。
    fail_premium_guide(
        sink.engine, "episode-force", BailianSpeechError("tts_monthly_budget_exceeded")
    )
    guide = _force_guide(sink)
    assert guide["status"] == "failed" and "next_retry_at" not in guide
    assert pending_premium_guide_candidates(sink.engine, **scope) == []
    try:
        outcome = evaluate_reader_ondemand_premium_guide(
            sink.engine, episode_id="episode-force", config=config, actor="reader",
        )["outcome"]
    except Exception as exc:  # 评分过低等拒绝也算「没有被当成生成中」
        outcome = type(exc).__name__
    assert outcome != "in_progress"


def test_manual_run_endpoint_does_not_enter_the_budget_wait():
    source = inspect.getsource(run_premium_guide_endpoint)
    assert "schedule_podcast_premium_guide(episode_id, budget_wait=False)" in source


def test_budget_wait_retries_tts_without_another_llm_call(tmp_path):
    """预算不够时文字已经生成并发布;预算回来后的重试只做合成。"""

    from config_bailian import BailianSpeechConfig
    from models.db import BailianTtsCallRecord
    from services.bailian_speech_client import BailianSpeechError
    from services.bailian_tts import BailianPremiumGuideTtsProvider, split_narration

    sink = _eligible_force_sink(tmp_path, "budget-retry.db")
    narration = "这是内部中文播客导读。"
    assert len(split_narration(narration, 8)) == 2  # 两段,各预留 1 分
    root = tmp_path / "receipts"

    def speech_config(monthly_budget: int) -> BailianSpeechConfig:
        return BailianSpeechConfig(
            enabled=True, api_key="secret-test", account_scope="test-account",
            tts_monthly_budget_minor=monthly_budget, tts_per_run_budget_minor=50,
            tts_chunk_chars=8, tts_receipt_root=str(root), tts_cache_max_bytes=40_000_000,
            asr_entitlement_ends_at="2027-01-01T00:00:00+00:00",
        )

    class Supplier:
        texts: list[str] = []

        def __init__(self, _config):
            pass

        def close(self):
            pass

        def synthesize(self, text):
            Supplier.texts.append(text)
            return {
                "request_id": "req-" + str(len(Supplier.texts)),
                "usage": {"characters": len(text)},
                "output": {"audio": {"url": "https://result.oss-ap-southeast-1.aliyuncs.com/a.wav"}},
            }

    async def download(*_args, **_kwargs):
        return _pcm_wav()

    class CountingText(TextProvider):
        blogs = 0
        scripts = 0

        async def create_blog(self, **kwargs):
            self.blogs += 1
            return await super().create_blog(**kwargs)

        async def create_narration(self, **_kwargs):
            self.scripts += 1
            return narration

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}', stderr="",
        ),
    )
    text = CountingText()
    config = _external_config()

    def run(monthly_budget: int):
        tts = BailianPremiumGuideTtsProvider(
            speech_config(monthly_budget), engine=sink.engine, episode_id="episode-force",
            voice_profile="narrator_zh", max_audio_bytes=store.max_bytes,
            client_factory=Supplier, downloader=download,
        )
        return asyncio.run(run_premium_guide(
            engine=sink.engine, store=store, episode_id="episode-force", config=config,
            text_provider=text, tts_provider=tts, budget_wait=True,
        ))

    with pytest.raises(BailianSpeechError, match="tts_monthly_budget_exceeded"):
        run(monthly_budget=1)
    assert (text.blogs, text.scripts) == (1, 1)
    assert Supplier.texts == []  # 预算在发出任何请求之前就拦住了
    guide = _force_guide(sink)
    assert (guide["status"], guide["error_code"]) == ("retry_wait", "tts_monthly_budget_exceeded")
    with Session(sink.engine) as session:
        assert session.exec(select(BailianTtsCallRecord)).all() == []
        assert session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh") is not None

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        extensions = json.loads(episode.extensions_json)
        extensions["premium_guide"]["next_retry_at"] = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        ).isoformat()
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
    assert pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == ["episode-force"]

    result = run(monthly_budget=100)
    assert result["audio_artifact_id"]
    assert (text.blogs, text.scripts) == (1, 1)  # 文字没有重新生成
    assert len(Supplier.texts) == 2
    guide = _force_guide(sink)
    assert guide["status"] == "ready"
    assert not {"retry_attempts", "next_retry_at", "error_code", "error"} & set(guide)


def test_force_during_retry_wait_clears_the_timer_and_is_not_auto_scheduled(tmp_path):
    from services.bailian_speech_client import BailianSpeechError

    sink = _eligible_force_sink(tmp_path, "force-wait.db")
    config = _external_config()
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    fail_premium_guide(
        sink.engine, "episode-force", BailianSpeechError("tts_receipt_disk_full"),
    )
    _set_episode_status(sink.engine, "episode-force", "synthesizing")
    fail_premium_guide(
        sink.engine, "episode-force", BailianSpeechError("tts_receipt_disk_full"),
    )
    assert _force_guide(sink)["retry_attempts"] == 2
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is False
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)

    first = prepare_forced_premium_guide(
        sink.engine,
        episode_id="episode-force",
        config=config,
        score_threshold=config.premium_score_threshold,
        idempotency_key="force-during-wait",
        reason="容量等待期间强制补一次",
        actor="admin",
    )
    replay = prepare_forced_premium_guide(
        sink.engine,
        episode_id="episode-force",
        config=config,
        score_threshold=config.premium_score_threshold,
        idempotency_key="force-during-wait",
        reason="容量等待期间强制补一次",
        actor="admin",
    )
    guide = _force_guide(sink)
    assert first["should_schedule"] is True and first["replayed"] is False
    assert replay["replayed"] is True and replay["status"] == "queued"
    assert guide["status"] == "queued"
    assert guide["retry_attempts"] == 2
    assert "next_retry_at" not in guide
    assert "error_code" not in guide
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is False
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)


def test_schedule_gate_lets_failed_and_mode_change_start(tmp_path, monkeypatch):
    """Manual run and a ready guide with a new mode still start.

    Only a future capacity wait and a force-owned queue stay blocked.
    """

    from types import SimpleNamespace

    import api.app as app_module

    sink = _eligible_force_sink(tmp_path, "schedule-gate.db")
    config = _external_config()
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=sink.engine))

    def start(episode_id: str) -> bool:
        async def once() -> bool:
            started = app_module.schedule_podcast_premium_guide(episode_id)
            task = app_module._PODCAST_PREMIUM_GUIDE_TASKS.pop(episode_id, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return started

        return asyncio.run(once())

    _set_episode_status(sink.engine, "episode-force", "failed", error="earlier failure")
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is True
    assert start("episode-force") is True

    _set_episode_status(sink.engine, "episode-force", "not_required")
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is True
    assert start("episode-force") is True

    _set_episode_status(
        sink.engine, "episode-force", "ready",
        guide_metadata={"mode": "brief_zh", "language": "en"},
    )
    assert pending_premium_guide_candidates(sink.engine, **scope) == ["episode-force"]
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is True
    assert start("episode-force") is True

    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
    _set_episode_status(
        sink.engine, "episode-force", "retry_wait",
        error="TTS 回执缓存不足", next_retry_at=future, error_code="tts_receipt_cache_full",
    )
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is False
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)
    assert start("episode-force") is False

    _set_episode_status(sink.engine, "episode-force", "queued")
    assert premium_guide_auto_schedule_allowed(sink.engine, "episode-force") is False
    assert "episode-force" not in pending_premium_guide_candidates(sink.engine, **scope)
    assert start("episode-force") is False


def test_premium_guide_tasks_only_list_premium_episodes_and_paginate(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium-list.db'}")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-list", name="Podcast list", source_type="podcast",
            url="https://example.test/list.xml", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
        for index in range(207):
            session.add(ArticleRecord(
                id=f"episode-{index:03}",
                title=f"Episode {index:03}",
                content_type="podcast_episode",
                source_id="podcast-list",
                source_url=f"https://example.test/episodes/{index}",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
            ))
        session.commit()
        for index in range(207):
            score = 9.0 if index < 205 else (8.5 if index == 205 else 8.4)
            session.add(ArticleAnalysisRecord(
                article_id=f"episode-{index:03}",
                status="succeeded",
                quality_score=score,
                podcast_final_score=score,
                analysis_basis="publisher_transcript",
                created_at=STAMP,
                updated_at=STAMP,
            ))
        session.commit()

    first = list_premium_guide_tasks(
        sink.engine, threshold=8.5, page=1, page_size=100
    )
    last = list_premium_guide_tasks(
        sink.engine, threshold=8.5, page=3, page_size=100
    )

    assert first["total"] == 206
    assert first["total_pages"] == 3
    assert len(first["items"]) == 100
    assert all(item["quality_score"] >= 8.5 for item in first["items"])
    assert len(last["items"]) == 6
    assert {item["episode_id"] for item in last["items"]} == {
        f"episode-{index:03}" for index in range(6)
    }
    with pytest.raises(ValueError, match="page_size"):
        list_premium_guide_tasks(
            sink.engine, threshold=8.5, page=1, page_size=101
        )


def _wav() -> bytes:
    pcm = b"\x00\x00" * 80
    return (
        b"RIFF"
        + (36 + len(pcm)).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8_000).to_bytes(4, "little")
        + (16_000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + len(pcm).to_bytes(4, "little")
        + pcm
    )


class TextProvider:
    async def create_blog(self, **_kwargs):
        return PremiumGuideDraft("# 精品导读\n\n核心内容。")

    async def create_narration(self, **_kwargs):
        return "这是内部中文播客导读。"


class TtsProvider:
    async def synthesize(self, text):
        assert text == "这是内部中文播客导读。"
        return SynthesizedAudio(_wav(), "audio/wav", "provider-task")


def _seed_force_candidate(sink: DatabaseStorage, *, duration: int = 19 * 60) -> None:
    transcript = json.dumps({"text": "complete forced transcript"})
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-force", name="Force", source_type="podcast",
            url="https://example.test/force.xml", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
        session.add(ArticleRecord(
            id="episode-force", title="Force", content_type="podcast_episode",
            source_id="podcast-force", source_url="https://example.test/force",
            publish_date=STAMP, fetched_date=STAMP, content="show notes",
            extensions_json=json.dumps({"duration_seconds": duration}),
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-force", status="succeeded", quality_score=7.2,
            podcast_final_score=7.2, analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-force",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.add(PodcastTextArtifactRecord(
            id="transcript-force", episode_id="episode-force",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language="en", authority_id="test-authority",
            provenance_json='{"provider":"fake"}', created_at=STAMP,
        ))
        session.commit()


@pytest.mark.parametrize("kind", ["normalized_transcript", "publisher_transcript"])
def test_premium_guide_runs_from_asr_to_published_blog_and_audio(tmp_path, kind):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium.db'}")
    transcript = (json.dumps({"text": "detailed source transcript"})
                  if kind == "normalized_transcript" else "detailed publisher transcript")
    transcript_hash = hashlib.sha256(transcript.encode()).hexdigest()
    with Session(sink.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-test",
                name="Podcast",
                source_type="podcast",
                url="https://example.test/feed.xml",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.commit()
        session.add(
            ArticleRecord(
                id="episode-1",
                title="Security episode",
                content_type="podcast_episode",
                source_id="podcast-test",
                source_url="https://example.test/episode",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
                extensions_json='{"duration_seconds":1800}',
            )
        )
        session.commit()
        session.add(
            ArticleAnalysisRecord(
                article_id="episode-1",
                status="succeeded",
                quality_score=9.0,
                score_reason="简介中的第一手安全披露",
                summary="简介初评摘要",
                analysis_basis="asr_transcript" if kind == "normalized_transcript" else "publisher_transcript",
                transcript_artifact_id="transcript-1",
                analysis_input_hash="authoritative-input-hash",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            PodcastTextArtifactRecord(
                id="transcript-1",
                episode_id="episode-1",
                kind=kind,
                version=1,
                content_hash=transcript_hash,
                inline_text=transcript,
                language="en",
                authority_id="test-authority",
                provenance_json='{"provider":"fake"}',
                created_at=STAMP,
            )
        )
        session.commit()

    store = PodcastArtifactStore(
        sink.engine,
        tmp_path / "audio",
        max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=60,
        allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [],
            0,
            stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}',
            stderr="",
        ),
    )
    with Session(sink.engine) as session:
        session.add(PodcastTextArtifactRecord(
            id="unscored-newer-transcript", episode_id="episode-1", kind=kind,
            version=2, content_hash=hashlib.sha256(b"unscored transcript").hexdigest(),
            inline_text="unscored transcript", language="en", authority_id="test-authority",
            created_at=STAMP,
        ))
        session.commit()
    assert pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=1200, score_threshold=8.5
    ) == ["episode-1"]

    class BoundTextProvider(TextProvider):
        async def create_blog(self, **kwargs):
            assert kwargs["transcript"] == (
                "detailed source transcript" if kind == "normalized_transcript"
                else "detailed publisher transcript"
            )
            return await super().create_blog(**kwargs)

    result = asyncio.run(
        run_premium_guide(
            sink.engine,
            store,
            episode_id="episode-1",
            config=_external_config(),
            text_provider=BoundTextProvider(),
            tts_provider=TtsProvider(),
        )
    )

    assert result["is_premium"] is True
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-1")
        assert analysis.quality_score == 9.0
        assert analysis.score_reason == "简介中的第一手安全披露"
        assert analysis.summary == "简介初评摘要"
        assert analysis.analysis_basis == ("asr_transcript" if kind == "normalized_transcript" else "publisher_transcript")
        assert analysis.analysis_input_hash == "authoritative-input-hash"
        assert session.get(PodcastTextPublicationRecord, "episode-1:digest_blog_zh")
        assert session.get(PodcastTextPublicationRecord, "episode-1:narration_script_zh")
    tasks = list_premium_guide_tasks(sink.engine, threshold=8.5)
    assert tasks["total"] == 1
    assert tasks["items"][0] | {
        "is_premium": True,
        "blog_ready": True,
        "audio_ready": True,
        "status": "ready",
    } == tasks["items"][0]


def test_forced_premium_guide_bypasses_automatic_selection_and_records_status(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium-force.db'}")
    _seed_force_candidate(sink)
    config = _external_config()

    first = prepare_forced_premium_guide(
        sink.engine,
        episode_id="episode-force",
        config=config,
        score_threshold=8.5,
        idempotency_key="force-episode-0001",
        reason="管理员验收强制 TTS",
        actor="admin",
    )
    replay = prepare_forced_premium_guide(
        sink.engine,
        episode_id="episode-force",
        config=config,
        score_threshold=8.5,
        idempotency_key="force-episode-0001",
        reason="管理员验收强制 TTS",
        actor="admin",
    )
    assert first == {
        "episode_id": "episode-force",
        "status": "queued",
        "forced": True,
        "replayed": False,
        "should_schedule": True,
    }
    assert replay["replayed"] is True
    with pytest.raises(PremiumGuideForceError) as conflict:
        prepare_forced_premium_guide(
            sink.engine,
            episode_id="episode-force",
            config=config,
            score_threshold=8.5,
            idempotency_key="force-episode-0001",
            reason="同一个键却改变原因",
            actor="admin",
        )
    assert conflict.value.code == "podcast_force_tts_idempotency_conflict"

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-force")
        request = json.loads(episode.extensions_json)["premium_guide"]["force_request"]
        assert request | {
            "idempotency_key": "force-episode-0001",
            "reason": "管理员验收强制 TTS",
            "requested_by": "admin",
            "score": 7.2,
            "score_threshold": 8.5,
            "selection_override": True,
        } == request

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "force-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0,
            stdout='{"streams":[{"codec_type":"audio","duration":"1"}],"format":{"duration":"1"}}',
            stderr="",
        ),
    )
    result = asyncio.run(run_premium_guide(
        sink.engine,
        store,
        episode_id="episode-force",
        config=config,
        text_provider=TextProvider(),
        tts_provider=TtsProvider(),
        score_threshold=8.5,
        selection_override=True,
    ))
    assert result["is_premium"] is False
    assert result["selection_override"] is True
    assert result["audio_artifact_id"]
    item = dashboard(sink.engine)["items"][0]
    assert item["is_premium"] is False
    assert item["tts_status"] == "ready"
    assert item["tts_status_label"] == "音频已生成"
    assert item["tts_forced"] is True
    assert item["tts_audio_artifact_id"] == result["audio_artifact_id"]
    assert item["tts_error"] == ""
    assert item["can_force_tts"] is False
    assert item["reason"] == "已强制生成 TTS；全文终评仍未达到当前优质门槛"


def test_forced_premium_guide_still_requires_known_duration(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium-force-no-duration.db'}")
    _seed_force_candidate(sink, duration=0)
    with pytest.raises(PremiumGuideForceError) as rejected:
        prepare_forced_premium_guide(
            sink.engine,
            episode_id="episode-force",
            config=_external_config(),
            score_threshold=8.5,
            idempotency_key="force-episode-no-duration-0001",
            reason="管理员强制 TTS",
            actor="admin",
        )
    assert rejected.value.code == "podcast_force_tts_not_ready"
    assert "时长未知" in rejected.value.message


@pytest.mark.parametrize("score", [7.5, 8.4])
def test_premium_guide_score_below_threshold_never_calls_provider(
    tmp_path, score
):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium-low.db'}")
    transcript = json.dumps({"text": "complete transcript"})
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-low", name="Low", source_type="podcast",
            url="https://example.test/low.xml", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
        session.add(ArticleRecord(
            id="episode-low", title="Low", content_type="podcast_episode",
            source_id="podcast-low", source_url="https://example.test/low",
            publish_date=STAMP, fetched_date=STAMP, content="notes",
            extensions_json='{"duration_seconds":2400}',
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-low", status="succeeded", quality_score=score,
            score_reason="权威简介初评", analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-low",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.add(PodcastTextArtifactRecord(
            id="transcript-low", episode_id="episode-low",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language="en", authority_id="test-authority",
            provenance_json='{"provider":"fake"}', created_at=STAMP,
        ))
        session.commit()

    class ForbiddenProvider:
        async def create_blog(self, **_kwargs):
            raise AssertionError("low authoritative score must skip blog generation")

        async def create_narration(self, **_kwargs):
            raise AssertionError("low authoritative score must skip narration")

        async def synthesize(self, _text):
            raise AssertionError("low authoritative score must skip TTS")

    provider = ForbiddenProvider()
    store = PodcastArtifactStore(
        sink.engine, tmp_path / "low-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: None,
    )
    config = _external_config()
    assert pending_premium_guide_candidates(
        sink.engine,
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == []
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-low", config=config,
        text_provider=provider, tts_provider=provider,
    ))
    assert result == {
        "episode_id": "episode-low",
        "is_premium": False,
        "score": score,
    }
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-low")
        assert analysis.quality_score == score
        assert analysis.score_reason == "权威简介初评"
        assert analysis.analysis_basis == "asr_transcript"


@pytest.mark.parametrize("language", ["en", "fr-FR", "ja", "und"])
def test_non_chinese_full_analysis_publishes_text_only_guide(
    tmp_path, language
):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / f'brief-{language}.db'}")
    transcript = json.dumps({"text": "complete transcript", "language": language})
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-brief", name="Brief", source_type="podcast",
            url="https://example.test/brief.xml", created_at=STAMP, updated_at=STAMP,
            params_json=(
                '{"catalog":"test","language":"en"}'
                if language == "und"
                else "{}"
            ),
        ))
        session.add(ArticleRecord(
            id="episode-brief", title="Brief", content_type="podcast_episode",
            source_id="podcast-brief", source_url="https://example.test/brief",
            publish_date=STAMP, fetched_date=STAMP, content="notes",
            extensions_json='{"duration_seconds":2400}',
        ))
        session.commit()
        session.add(PodcastTextArtifactRecord(
            id="transcript-brief", episode_id="episode-brief",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language=language, authority_id="test-authority",
            provenance_json='{"provider":"fake"}', created_at=STAMP,
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-brief", status="succeeded", quality_score=7.2,
            podcast_initial_score=6.4, podcast_final_score=7.2,
            score_reason="全文分析完成", analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-brief",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()

    calls = {"blog": 0}

    class BriefTextProvider(TextProvider):
        async def create_blog(self, **kwargs):
            calls["blog"] += 1
            assert kwargs["max_chars"] == 3500
            return PremiumGuideDraft(blog_markdown="一篇短小中文导读。")

        async def create_narration(self, **_kwargs):
            pytest.fail("brief_zh must not generate narration")

    class ForbiddenTts:
        async def synthesize(self, _text):
            pytest.fail("brief_zh must not call TTS")

    store = PodcastArtifactStore(
        sink.engine, tmp_path / f"brief-audio-{language}", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: None,
    )
    config = _external_config()
    assert pending_premium_guide_candidates(
        sink.engine,
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == ["episode-brief"]
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-brief", config=config,
        text_provider=BriefTextProvider(), tts_provider=ForbiddenTts(),
    ))
    assert result["mode"] == "brief_zh"
    assert result["reason"] == "non_chinese_text_guide"
    assert result["audio_artifact_id"] is None
    assert calls == {"blog": 1}
    assert pending_premium_guide_candidates(
        sink.engine,
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    ) == []
    task = dashboard(sink.engine)["items"][0]
    assert task["guide_mode"] == "brief_zh"
    assert task["blog_ready"] is True
    assert task["audio_ready"] is False
    assert task["tts_status"] == "not_started"
    assert task["tts_error"] == ""
    with Session(sink.engine) as session:
        assert session.get(
            PodcastTextPublicationRecord, "episode-brief:digest_blog_zh"
        ) is not None
        assert session.get(
            PodcastTextPublicationRecord, "episode-brief:narration_script_zh"
        ) is None
        guide = json.loads(
            session.get(ArticleRecord, "episode-brief").extensions_json
        )["premium_guide"]
        assert guide["mode"] == "brief_zh"
        assert guide["language"] == ("en" if language == "und" else language.lower())
        assert guide["language_source"] == (
            "source_config" if language == "und" else "transcript_artifact"
        )


@pytest.mark.parametrize(
    "language", ["zh-CN", "und", "mul", "unknown", "not_a_language"]
)
def test_chinese_or_unknown_low_score_does_not_auto_generate_guide(
    tmp_path, language
):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / f'no-brief-{language}.db'}")
    transcript = json.dumps({"text": "complete transcript"})
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="episode-no-brief", title="No brief", content_type="podcast_episode",
            source_id="podcast-no-brief", source_url="https://example.test/no-brief",
            publish_date=STAMP, fetched_date=STAMP, content="notes",
            extensions_json='{"duration_seconds":2400}',
        ))
        session.commit()
        session.add(PodcastTextArtifactRecord(
            id="transcript-no-brief", episode_id="episode-no-brief",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language=language, authority_id="test-authority",
            provenance_json="{}", created_at=STAMP,
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-no-brief", status="succeeded", quality_score=7.2,
            podcast_initial_score=6.4, podcast_final_score=7.2,
            analysis_basis="asr_transcript", transcript_artifact_id="transcript-no-brief",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
    assert pending_premium_guide_candidates(
        sink.engine, minimum_duration_seconds=1200, score_threshold=8.5,
    ) == []


def test_premium_guide_skips_episode_not_over_twenty_minutes(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'short.db'}")
    transcript = json.dumps({"text": "short episode"})
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-short", name="Short", source_type="podcast",
            url="https://example.test/short.xml", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
        session.add(ArticleRecord(
            id="episode-short", title="Short", content_type="podcast_episode",
            source_id="podcast-short", source_url="https://example.test/short",
            publish_date=STAMP, fetched_date=STAMP, content="notes",
            extensions_json='{"duration_seconds":1199}',
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-short", status="succeeded", quality_score=9.0,
            podcast_final_score=9.0, analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-short",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.add(PodcastTextArtifactRecord(
            id="transcript-short", episode_id="episode-short",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language="en", authority_id="test-authority",
            provenance_json='{"provider":"fake"}',
            created_at=STAMP,
        ))
        session.commit()

    class FailIfTtsCalled(TtsProvider):
        async def synthesize(self, _text):
            raise AssertionError("TTS must not be called for episodes under 20 minutes")

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "short-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: None,
    )
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-short", config=_external_config(),
        text_provider=TextProvider(), tts_provider=FailIfTtsCalled(),
    ))
    assert result["reason"] == "duration_not_over_minimum"
    assert result["audio_artifact_id"] is None
    assert result["is_premium"] is True
    with Session(sink.engine) as session:
        assert session.get(ArticleAnalysisRecord, "episode-short").quality_score == 9.0
        assert session.get(PodcastTextPublicationRecord, "episode-short:digest_blog_zh") is not None
        assert session.get(PodcastTextPublicationRecord, "episode-short:narration_script_zh") is None


def test_short_episode_runs_without_tts_provider_or_tts_stages(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'short-no-tts.db'}")
    _seed_force_candidate(sink, duration=15 * 60)
    config = PodcastConfig(
        installation="external",
        authority_id="podcast-external-test",
        allowed_stages=("fetch", "asr", "translate", "analyze", "digest", "local_publish"),
        premium_score_threshold=7.0,
    )
    store = PodcastArtifactStore(
        sink.engine, tmp_path / "short-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: None,
    )
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-force", config=config,
        text_provider=TextProvider(), tts_provider=None,
    ))
    assert result["reason"] == "duration_not_over_minimum"
    assert result["audio_artifact_id"] is None
    with Session(sink.engine) as session:
        assert session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh") is not None
        assert session.get(PodcastTextPublicationRecord, "episode-force:narration_script_zh") is None


def test_premium_guide_twenty_minutes_exact_enters_audio_queue(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'exact20.db'}")
    transcript = json.dumps({"text": "twenty minute episode"})
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-exact", name="Exact", source_type="podcast",
            url="https://example.test/exact.xml", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()
        session.add(ArticleRecord(
            id="episode-exact-20", title="Exact 20m", content_type="podcast_episode",
            source_id="podcast-exact", source_url="https://example.test/exact",
            publish_date=STAMP, fetched_date=STAMP, content="notes",
            extensions_json='{"duration_seconds":1200}',
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-exact-20", status="succeeded", quality_score=9.0,
            podcast_final_score=9.0, analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-exact-20",
            created_at=STAMP, updated_at=STAMP,
        ))
        session.add(PodcastTextArtifactRecord(
            id="transcript-exact-20", episode_id="episode-exact-20",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language="en", authority_id="test-authority",
            provenance_json='{"provider":"fake"}',
            created_at=STAMP,
        ))
        session.commit()

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "exact-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0,
            stdout='{"streams":[{"codec_type":"audio","duration":"360"}],"format":{"duration":"360"}}',
            stderr="",
        ),
    )
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-exact-20", config=_external_config(),
        text_provider=TextProvider(), tts_provider=TtsProvider(),
    ))
    assert result["audio_artifact_id"] is not None
    assert result["is_premium"] is True
    with Session(sink.engine) as session:
        assert session.get(PodcastTextPublicationRecord, "episode-exact-20:digest_blog_zh") is not None
        assert session.get(PodcastTextPublicationRecord, "episode-exact-20:narration_script_zh") is not None


def test_solo_deep_duration_plan_tiers():
    from services.podcast_premium_guides import calculate_solo_deep_plan

    # Tier 1: < 20 min (< 1200s)
    plan_short = calculate_solo_deep_plan(1199)
    assert plan_short.tier == "short"
    assert plan_short.should_synthesize_audio is False

    # Forced override for short
    plan_short_forced = calculate_solo_deep_plan(1199, selection_override=True)
    assert plan_short_forced.tier == "tier_20_45"
    assert plan_short_forced.should_synthesize_audio is True

    # Tier 2: 20-45 min (1200s to 2700s)
    plan_tier2 = calculate_solo_deep_plan(1200)
    assert plan_tier2.tier == "tier_20_45"
    assert plan_tier2.should_synthesize_audio is True
    assert plan_tier2.min_audio_minutes == 5
    assert plan_tier2.max_audio_minutes == 8
    assert plan_tier2.min_chars == 1430
    assert plan_tier2.max_chars == 2310

    plan_tier2_upper = calculate_solo_deep_plan(2700)
    assert plan_tier2_upper.tier == "tier_20_45"

    # Tier 3: 45-90 min (2700s to 5400s)
    plan_tier3 = calculate_solo_deep_plan(2701)
    assert plan_tier3.tier == "tier_45_90"
    assert plan_tier3.should_synthesize_audio is True
    assert plan_tier3.min_audio_minutes == 8
    assert plan_tier3.max_audio_minutes == 12
    assert plan_tier3.min_chars == 2310
    assert plan_tier3.max_chars == 3410

    # Tier 4: > 90 min (> 5400s)
    plan_tier4 = calculate_solo_deep_plan(5401)
    assert plan_tier4.tier == "tier_gt_90"
    assert plan_tier4.should_synthesize_audio is True
    assert plan_tier4.min_audio_minutes == 12
    assert plan_tier4.max_audio_minutes == 15
    assert plan_tier4.min_chars == 3410
    assert plan_tier4.max_chars == 4290


def test_audio_qa_triggers_retry_and_succeeds(tmp_path):
    from services.podcast_premium_guides import PremiumGuideError

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'qa-retry.db'}")
    _seed_force_candidate(sink, duration=30 * 60)
    config = _external_config()

    call_count = {"probe": 0, "narration": 0, "synthesize": 0}

    def fake_probe(*_a, **_k):
        call_count["probe"] += 1
        # First probe returns 600s (10 min, exceeding 8 min max for 30m episode)
        # Second probe returns 420s (7 min, within 5-8 min)
        dur = "600" if call_count["probe"] == 1 else "420"
        return subprocess.CompletedProcess(
            [], 0,
            stdout=f'{{"streams":[{{"codec_type":"audio","duration":"{dur}"}}],"format":{{"duration":"{dur}"}}}}',
            stderr="",
        )

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "qa-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=fake_probe,
    )

    class CountingTextProvider(TextProvider):
        async def create_narration(self, **kwargs):
            call_count["narration"] += 1
            if kwargs.get("retry_shorter"):
                return "这是缩短后的中文播客导读口播稿。"
            return "这是内部中文播客导读。"

    class CountingTtsProvider:
        async def synthesize(self, text):
            call_count["synthesize"] += 1
            return SynthesizedAudio(_wav(), "audio/wav", f"task-{call_count['synthesize']}")

    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-force", config=config,
        text_provider=CountingTextProvider(), tts_provider=CountingTtsProvider(),
        selection_override=True,
    ))
    assert result["audio_artifact_id"]
    assert call_count["narration"] == 2
    assert call_count["synthesize"] == 2


def test_audio_qa_fails_when_exceeding_hard_ceiling_and_keeps_blog(tmp_path):
    from services.podcast_premium_guides import PremiumGuideError

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'qa-fail.db'}")
    _seed_force_candidate(sink, duration=100 * 60)
    config = _external_config()

    # Always returns 960s (16 min), exceeding 15 min hard ceiling
    fake_probe = lambda *_a, **_k: subprocess.CompletedProcess(
        [], 0,
        stdout='{"streams":[{"codec_type":"audio","duration":"960"}],"format":{"duration":"960"}}',
        stderr="",
    )

    store = PodcastArtifactStore(
        sink.engine, tmp_path / "qa-fail-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=fake_probe,
    )

    with pytest.raises(PremiumGuideError, match="超过 .* 分钟限制"):
        asyncio.run(run_premium_guide(
            sink.engine, store, episode_id="episode-force", config=config,
            text_provider=TextProvider(), tts_provider=TtsProvider(),
            selection_override=True,
        ))

    with Session(sink.engine) as session:
        # Blog was created and published before TTS failed
        assert session.get(PodcastTextPublicationRecord, "episode-force:digest_blog_zh") is not None
        # Audio was not published
        audios = session.exec(
            select(PodcastArtifactRecord).where(
                PodcastArtifactRecord.episode_id == "episode-force",
                PodcastArtifactRecord.kind == "digest_audio_zh",
                PodcastArtifactRecord.status == "published",
            )
        ).all()
        assert audios == []


# ---------- 候选巡检的读路径(issue #318) ----------
#
# ``podcast_text_artifacts`` 的 ``language / authority_id / provenance_json`` 等列在行里
# 排在 ``inline_text`` 之后。SQLite 读它们要沿溢出页链走完整段逐字稿,所以哪怕查询
# 不取正文,每拍也会把全部逐字稿读一遍(生产实测一拍 340MB)。


def _statements_during(engine, call):
    from sqlalchemy import event

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return result, statements


_AFTER_TRANSCRIPT_TEXT = (
    "podcast_text_artifacts.language",
    "podcast_text_artifacts.authority_id",
    "podcast_text_artifacts.source_artifact_id",
    "podcast_text_artifacts.source_content_hash",
    "podcast_text_artifacts.provenance_json",
)


def test_candidate_scan_reads_each_transcript_tail_once_per_process(tmp_path):
    sink = _eligible_force_sink(tmp_path, "scan-tail.db")
    config = _external_config()
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )

    def scan():
        return pending_premium_guide_candidates(sink.engine, **scope)

    first, first_statements = _statements_during(sink.engine, scan)
    second, second_statements = _statements_during(sink.engine, scan)
    assert first == second == ["episode-force"]

    def select_list(statement: str) -> str:
        return statement[: statement.upper().index(" FROM ")]

    def touching_transcripts(statements):
        return [s for s in statements if "podcast_text_artifacts" in s]

    for statements in (first_statements, second_statements):
        joins = [s for s in touching_transcripts(statements) if " JOIN " in s.upper()]
        assert len(joins) == 1
        # 主查询只取排在正文之前的转写列。
        for column in (*_AFTER_TRANSCRIPT_TEXT, "podcast_text_artifacts.inline_text"):
            assert column not in select_list(joins[0])
        # 任何语句都不取逐字稿正文。
        for statement in statements:
            assert "inline_text" not in select_list(statement)

    def authority_probes(statements):
        return [
            s for s in touching_transcripts(statements)
            if select_list(s).replace(" ", "") == "SELECTid,authority_id"
        ]

    def tail_reads(statements):
        probes = authority_probes(statements)
        return [
            s for s in touching_transcripts(statements)
            if s not in probes
            and any(column in select_list(s) for column in _AFTER_TRANSCRIPT_TEXT)
        ]

    # 第一拍为这份转写读一次尾部列;之后同一进程不再读。
    assert len(tail_reads(first_statements)) == 1
    assert tail_reads(second_statements) == []
    # authority_id 每拍现取(缓存键的一部分),但只读索引、不回表——回表就要走完
    # 整段逐字稿。
    for statements in (first_statements, second_statements):
        [probe] = authority_probes(statements)
        assert "INDEXED BY uq_podcast_text_artifacts_authority_slot" in probe
    with sink.engine.connect() as connection:
        plan = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT id, authority_id FROM podcast_text_artifacts "
            "INDEXED BY uq_podcast_text_artifacts_authority_slot WHERE id IN ('transcript-force')"
        ).fetchall()
    assert any("COVERING INDEX uq_podcast_text_artifacts_authority_slot" in row[-1] for row in plan)


def test_transcript_column_order_keeps_the_light_columns_before_the_text():
    """这条守卫记录前提:哪几列在正文之前(便宜)、哪几列在之后(要走溢出页链)。"""

    names = [column.name for column in PodcastTextArtifactRecord.__table__.columns]
    text_at = names.index("inline_text")
    assert max(names.index(n) for n in ("id", "episode_id", "kind", "content_hash")) < text_at
    assert min(
        names.index(n.split(".", 1)[1]) for n in _AFTER_TRANSCRIPT_TEXT
    ) > text_at


def test_transcript_tail_cache_is_per_database_and_bounded(tmp_path, monkeypatch):
    from services import podcast_premium_guides as guides

    def sink_with(name: str, language: str) -> DatabaseStorage:
        sink = DatabaseStorage(f"sqlite:///{tmp_path / name}")
        _seed_force_candidate(sink)
        with Session(sink.engine) as session:
            for index in range(3):
                session.add(PodcastTextArtifactRecord(
                    id=f"extra-{index}", episode_id="episode-force",
                    kind="transcript_zh", version=index + 1, content_hash=f"{index}" * 64,
                    inline_text="x" * 10, language=language, authority_id="test-authority",
                    provenance_json="{}", created_at=STAMP,
                ))
            session.commit()
        return sink

    english = sink_with("tail-en.db", "en")
    french = sink_with("tail-fr.db", "fr")
    # 两个库里同名的产物各记各的,不串。
    with Session(english.engine) as session:
        assert guides._artifact_tails(session, ["extra-0"])["extra-0"].language == "en"
    with Session(french.engine) as session:
        assert guides._artifact_tails(session, ["extra-0"])["extra-0"].language == "fr"
    with Session(english.engine) as session:
        assert guides._artifact_tails(session, ["extra-0", "missing"]).keys() == {"extra-0"}

    monkeypatch.setattr(guides, "_ARTIFACT_TAIL_CACHE_LIMIT", 2)
    with Session(french.engine) as session:
        loaded = guides._artifact_tails(session, ["extra-0", "extra-1", "extra-2"])
        assert set(loaded) == {"extra-0", "extra-1", "extra-2"}
        assert len(guides._artifact_tail_caches[session.get_bind()]) == 2


def test_reimported_artifact_with_the_same_id_is_not_served_from_the_cache(tmp_path):
    """单集删除后经同步原样导回:产物 id 不变,所属方变了,尾部列必须重新读。

    「行不可变」只管 UPDATE;删除单集会连带删掉产物,之后可以用同一个 id 重建。
    """

    from sqlalchemy import text

    from services import podcast_premium_guides as guides

    sink = _eligible_force_sink(tmp_path, "reimport.db")
    config = _external_config()
    scope = dict(
        minimum_duration_seconds=config.premium_min_duration_seconds,
        score_threshold=config.premium_score_threshold,
    )
    assert pending_premium_guide_candidates(sink.engine, **scope) == ["episode-force"]
    with Session(sink.engine) as session:
        before = guides._artifact_tails(session, ["transcript-force"])["transcript-force"]
    assert (before.authority_id, before.language) == ("test-authority", "en")

    with Session(sink.engine) as session:
        # 删单集;分析与产物随外键级联删除(产物的删除触发器只在单集已不存在时放行)。
        session.exec(text("DELETE FROM articles WHERE id = 'episode-force'"))
        session.commit()
    with Session(sink.engine) as session:
        assert session.get(PodcastTextArtifactRecord, "transcript-force") is None
    assert pending_premium_guide_candidates(sink.engine, **scope) == []

    transcript = json.dumps({"text": "complete forced transcript"})
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="episode-force", title="Force", content_type="podcast_episode",
            source_id="podcast-force", source_url="https://example.test/force",
            publish_date=STAMP, fetched_date=STAMP, content="show notes",
            extensions_json=json.dumps({"duration_seconds": 21 * 60}),
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-force", status="succeeded", quality_score=9.0,
            podcast_final_score=9.0, analysis_basis="asr_transcript",
            transcript_artifact_id="transcript-force", created_at=STAMP, updated_at=STAMP,
        ))
        session.add(PodcastTextArtifactRecord(
            id="transcript-force", episode_id="episode-force",
            kind="normalized_transcript", version=1,
            content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
            inline_text=transcript, language="fr", authority_id="upstream-authority",
            provenance_json='{"provider":"fake"}', created_at=STAMP,
        ))
        session.flush()
        session.add(PodcastTextPublicationRecord(
            identity="episode-force:normalized_transcript", episode_id="episode-force",
            kind="normalized_transcript", artifact_id="transcript-force",
            authority_id="upstream-authority", status="published", published_at=STAMP,
            updated_at=STAMP,
        ))
        session.commit()

    with Session(sink.engine) as session:
        after = guides._artifact_tails(session, ["transcript-force"])["transcript-force"]
    assert (after.authority_id, after.language) == ("upstream-authority", "fr")
    # 发布指针的所属方与产物一致:候选不该因为缓存里的旧所属方被跳过。
    assert pending_premium_guide_candidates(sink.engine, **scope) == ["episode-force"]


def test_authority_probe_survives_a_missing_index(tmp_path, monkeypatch, captured_log):
    from services import podcast_premium_guides as guides

    sink = _eligible_force_sink(tmp_path, "no-index.db")
    monkeypatch.setattr(guides, "_AUTHORITY_SLOT_INDEX", "no_such_index")
    with captured_log(guides.logger) as logged:
        with Session(sink.engine) as session:
            tails = guides._artifact_tails(session, ["transcript-force"])
    assert tails["transcript-force"].authority_id == "test-authority"
    assert any("no_such_index" in record.getMessage() for record in logged)
