from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import asyncio

import pytest
from sqlmodel import Session

from config import PodcastConfig
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.podcast_artifacts import PodcastArtifactStore
from services.podcast_premium_guides import (
    PremiumGuideDraft,
    SynthesizedAudio,
    list_premium_guide_tasks,
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

    assert first["total"] == 205
    assert first["total_pages"] == 3
    assert len(first["items"]) == 100
    assert all(item["quality_score"] > 8.5 for item in first["items"])
    assert len(last["items"]) == 5
    assert {item["episode_id"] for item in last["items"]} == {
        f"episode-{index:03}" for index in range(5)
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
        return PremiumGuideDraft(9.1, "技术细节和案例完整", "# 精品导读\n\n核心内容。")

    async def create_narration(self, **_kwargs):
        return "这是内部中文播客导读。"


class TtsProvider:
    async def synthesize(self, text):
        assert text == "这是内部中文播客导读。"
        return SynthesizedAudio(_wav(), "audio/wav", "provider-task")


def test_premium_guide_runs_from_asr_to_published_blog_and_audio(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'premium.db'}")
    transcript = json.dumps({"text": "detailed source transcript"})
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
                quality_score=5.0,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            PodcastTextArtifactRecord(
                id="transcript-1",
                episode_id="episode-1",
                kind="normalized_transcript",
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
    result = asyncio.run(
        run_premium_guide(
            sink.engine,
            store,
            episode_id="episode-1",
            config=_external_config(),
            text_provider=TextProvider(),
            tts_provider=TtsProvider(),
        )
    )

    assert result["is_premium"] is True
    with Session(sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-1")
        assert analysis.quality_score == 9.1
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
            extensions_json='{"duration_seconds":1200}',
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode-short", status="succeeded", quality_score=5,
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
    store = PodcastArtifactStore(
        sink.engine, tmp_path / "short-audio", max_bytes=1024 * 1024,
        total_quota_bytes=10 * 1024 * 1024, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/wav",),
        probe_runner=lambda *_a, **_k: None,
    )
    result = asyncio.run(run_premium_guide(
        sink.engine, store, episode_id="episode-short", config=_external_config(),
        text_provider=TextProvider(), tts_provider=TtsProvider(),
    ))
    assert result["reason"] == "duration_not_over_minimum"
    with Session(sink.engine) as session:
        assert session.get(ArticleAnalysisRecord, "episode-short").quality_score == 5
