from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import asyncio

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
    PremiumGuideDraft,
    PremiumGuideForceError,
    SynthesizedAudio,
    _set_episode_status,
    list_premium_guide_tasks,
    pending_premium_guide_candidates,
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

