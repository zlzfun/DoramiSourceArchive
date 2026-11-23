from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig, PodcastConfig  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.article_analysis import queue_article_analysis  # noqa: E402
from services.podcast_full_analysis import (  # noqa: E402
    FullAnalysisWorkerStep,
    FullAnalysisWorkerConfig,
    OpenAiFullAnalysisProvider,
    analyze_transcript,
    run_full_analysis_worker_step,
    split_transcript,
    _input_hash,
)
from services.podcast_processing import (  # noqa: E402
    PodcastLeaseLost,
    begin_stage_attempt,
    claim_next_processing,
    heartbeat_processing,
    settle_attempt_cost,
)
from services.podcast_processing_admin import (  # noqa: E402
    PodcastAdminError,
    PodcastProcessingProviderRegistry,
    request_processing,
)
from services.podcast_stage_policy import PodcastStagePolicy  # noqa: E402
from services.podcast_publisher_transcripts import (  # noqa: E402
    publisher_transcript_refresh_revision,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from api.articles_view import (  # noqa: E402
    _podcast_projection,
    serialize_article_list_item,
)


NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def configured_llm(monkeypatch):
    monkeypatch.setattr(
        "services.daily_brief.resolve_llm_config",
        lambda _session: LLMConfig(
            base_url="https://llm.example.test", api_key="test", model="test"
        ),
    )


class _Store:
    def is_intact(self, _artifact) -> bool:
        return True


class _Provider:
    def __init__(self, score: float = 8.0) -> None:
        self.score = score
        self.chunks: list[str] = []

    async def map_chunk(self, **kwargs):
        self.chunks.append(kwargs["chunk"])
        return {
            "chunk_index": kwargs["chunk_index"],
            "evidence": kwargs["chunk"],
        }


    async def reduce(self, **_kwargs):
        return {
            "quality_score": self.score,
            "score_reason": "整期证据充分",
            "summary": "覆盖整期逐字稿的最终摘要",
            "content_genre": "opinion",
            "primary_tag_code": None,
            "tag_assignments": [],
            "tag_candidates": [],
            "content_features": [],
            "entities": [],
            "podcast_factors": {
                key: {"level": "high", "evidence": "整期证据"}
                for key in (
                    "guest_authority",
                    "topic_timeliness",
                    "novelty",
                    "evidence_depth",
                    "viewpoint_diversity",
                    "practical_value",
                )
            },
        }


class _FailProvider(_Provider):
    async def reduce(self, **_kwargs):
        raise RuntimeError("temporary LLM failure")


def _config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id="issue-44-test",
        allowed_stages=("fetch", "asr", "analyze"),
        processing_enabled=True,
        provider_ready_targets=("full_analysis",),
        monthly_budget_cny_minor=10_000,
        per_run_budget_cny_minor=1_000,
    )


def _registry() -> PodcastProcessingProviderRegistry:
    registry = PodcastProcessingProviderRegistry()
    registry.register_target(
        "full_analysis",
        stage_executors={"asr": lambda _ctx: None, "analyze": lambda _ctx: None},
        estimator=lambda _metadata: 0,
    )
    return registry


@pytest.fixture()
def engine(tmp_path):
    storage = DatabaseStorage(f"sqlite:///{tmp_path / 'issue-44.db'}")
    stamp = NOW.isoformat(timespec="microseconds")
    transcript = "开头证据\n" + "中段论据\n" * 20 + "结尾结论"
    transcript_hash = hashlib.sha256(transcript.encode()).hexdigest()
    with Session(storage.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-source",
                name="Podcast",
                source_type="podcast",
                url="https://example.test/feed.xml",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.flush()
        for episode_id, score in (("episode-49", 5.9), ("episode-50", 6.0)):
            session.add(
                ArticleRecord(
                    id=episode_id,
                    title="Long Podcast",
                    content_type="podcast_episode",
                    source_id="podcast-source",
                    source_url=f"https://example.test/{episode_id}",
                    publish_date=stamp,
                    fetched_date=stamp,
                    content="show notes",
                    extensions_json=json.dumps({"duration_seconds": None}),
                )
            )
            session.flush()
            session.add(
                ArticleAnalysisRecord(
                    article_id=episode_id,
                    status="succeeded",
                    quality_score=score,
                    score_reason="初评",
                    summary="简介摘要",
                    content_hash="a" * 64,
                    analysis_basis="podcast_show_notes",
                    analysis_input_hash="b" * 64,
                    prompt_version="podcast-initial",
                    scoring_version="podcast-score",
                    analyzed_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            artifact = PodcastTextArtifactRecord(
                id=f"publisher-{episode_id}",
                episode_id=episode_id,
                kind="publisher_transcript",
                version=1,
                content_hash=transcript_hash,
                inline_text=transcript,
                language="zh-CN",
                authority_id="",
                provenance_json='{"format":"text"}',
                created_at=stamp,
            )
            session.add(artifact)
            session.add(
                PodcastTextPublicationRecord(
                    identity=f"{episode_id}:publisher_transcript",
                    episode_id=episode_id,
                    kind="publisher_transcript",
                    artifact_id=artifact.id,
                    status="published",
                    authority_id="",
                    published_at=stamp,
                    updated_at=stamp,
                )
            )
        session.add(
            ArticleRecord(
                id="episode-asr",
                title="Audio-only Podcast",
                content_type="podcast_episode",
                source_id="podcast-source",
                source_url="https://example.test/episode-asr",
                publish_date=stamp,
                fetched_date=stamp,
                content="show notes",
                extensions_json=json.dumps(
                    {
                        "duration_seconds": 60,
                        "audio_url": "https://cdn.example.test/episode-asr.mp3",
                    }
                ),
            )
        )
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id="episode-asr",
                status="succeeded",
                quality_score=6.0,
                score_reason="初评",
                summary="简介摘要",
                content_hash="c" * 64,
                analysis_basis="podcast_show_notes",
                analysis_input_hash="d" * 64,
                prompt_version="podcast-initial",
                scoring_version="podcast-score",
                analyzed_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.add(
            PodcastSourceMediaSnapshotRecord(
                id="source-media-asr",
                episode_id="episode-asr",
                content_hash="e" * 64,
                mime="audio/mpeg",
                size_bytes=1024,
                duration_seconds=60,
                locator_hash=hashlib.sha256(
                    b"https://cdn.example.test/episode-asr.mp3"
                ).hexdigest(),
                created_at=stamp,
            )
        )
        session.commit()
    return storage.engine


def _request(engine, episode_id: str, *, override: bool, key: str):
    return request_processing(
        engine,
        _registry(),
        _config(),
        episode_id=episode_id,
        target="full_analysis",
        selection_override=override,
        idempotency_key=key,
        reason="测试全文处理",
        actor="system" if not override else "admin",
    )


def test_publisher_transcript_bypasses_initial_score_but_paid_asr_does_not(engine):
    publisher = _request(engine, "episode-49", override=False, key="auto-episode-49")
    assert publisher.selection_source == "policy"
    assert publisher.stage == "analyze"
    assert publisher.input_artifact_kind == "publisher_transcript"

    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-asr")
        analysis.quality_score = 5.9
        session.add(analysis)
        session.commit()
    with pytest.raises(PodcastAdminError) as rejected:
        _request(engine, "episode-asr", override=False, key="auto-asr-59")
    assert rejected.value.code == "podcast_selection_required"

    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-asr")
        analysis.quality_score = 6.0
        session.add(analysis)
        session.commit()
    exact = _request(engine, "episode-asr", override=False, key="auto-asr-60")
    assert exact.selection_source == "policy"
    assert exact.stage == "asr"
    assert exact.input_artifact_kind == "source_media_snapshot"


def test_paid_asr_refresh_uses_saved_initial_score_after_full_analysis(engine):
    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-asr")
        analysis.analysis_basis = "asr_transcript"
        analysis.podcast_initial_score = 6.0
        analysis.podcast_final_score = 8.0
        analysis.quality_score = 8.0
        session.add(analysis)
        session.commit()

    refreshed = _request(engine, "episode-asr", override=False, key="asr-refresh-60")
    assert refreshed.stage == "asr"
    assert refreshed.input_artifact_kind == "source_media_snapshot"


def test_same_full_analysis_request_is_idempotent(engine):
    first = _request(engine, "episode-50", override=False, key="same-request-key")
    second = _request(engine, "episode-50", override=False, key="same-request-key")
    assert first.id == second.id
    with Session(engine) as session:
        from sqlmodel import select

        rows = session.exec(
            select(PodcastProcessingRecord).where(
                PodcastProcessingRecord.episode_id == "episode-50",
                PodcastProcessingRecord.requested_target == "full_analysis",
            )
        ).all()
        assert len(rows) == 1


def test_audio_only_candidate_enters_existing_asr_state_machine(engine):
    process = _request(engine, "episode-asr", override=False, key="asr-fallback")
    assert process.input_artifact_kind == "source_media_snapshot"
    assert process.stage == "asr"
    assert process.processing_status == "queued"


def test_analysis_worker_skips_due_asr_rows_for_the_same_target(engine):
    asr_process = _request(
        engine, "episode-asr", override=False, key="asr-must-stay-with-asr-worker"
    )
    analyze_process = _request(
        engine, "episode-50", override=False, key="analyze-worker-owned-row"
    )
    provider = _Provider()
    config = _config()

    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="analysis-only-worker",
                    lease_seconds=120,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://llm.example.test",
                        api_key="test",
                        model="test-model",
                    ),
                ),
                podcast_config=config,
                policy=PodcastStagePolicy(config),
                provider=provider,
            )
        )
        assert step.action == "completed"
        assert step.processing_id == analyze_process.id
        untouched = session.get(PodcastProcessingRecord, asr_process.id)
        assert untouched.processing_status == "queued"
        assert untouched.stage == "asr"


@pytest.mark.parametrize("authority_field", ["article", "analysis"])
def test_full_analysis_admission_rejects_remote_analysis_authority(
    engine, authority_field
):
    with Session(engine) as session:
        if authority_field == "article":
            target = session.get(ArticleRecord, "episode-50")
            target.analysis_authority_id = "remote-producer"
        else:
            target = session.get(ArticleAnalysisRecord, "episode-50")
            target.authority_id = "remote-producer"
        session.add(target)
        session.commit()

    with pytest.raises(PodcastAdminError) as denied:
        _request(engine, "episode-50", override=True, key=f"remote-{authority_field}")
    assert denied.value.code == "podcast_stage_denied"
    with Session(engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []


def test_queued_full_analysis_is_parked_when_remote_authority_arrives(engine):
    process = _request(
        engine, "episode-50", override=False, key="authority-arrives-before-claim"
    )
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-50")
        episode.analysis_authority_id = "remote-producer"
        session.add(episode)
        session.commit()

    provider = _Provider()
    config = _config()
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="authority-aware-worker",
                    lease_seconds=120,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://llm.example.test",
                        api_key="test",
                        model="test-model",
                    ),
                ),
                podcast_config=config,
                policy=PodcastStagePolicy(config),
                provider=provider,
            )
        )
        assert step.action == "idle"
        parked = session.get(PodcastProcessingRecord, process.id)
        assert parked.processing_status == "not_required"
        assert parked.eligibility_status == "blocked_source"
        assert provider.chunks == []


def test_idempotent_replay_is_denied_after_remote_authority_arrives(engine):
    _request(engine, "episode-50", override=True, key="authority-replay")
    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        analysis.authority_id = "remote-producer"
        session.add(analysis)
        session.commit()

    with pytest.raises(PodcastAdminError) as denied:
        _request(engine, "episode-50", override=True, key="authority-replay")
    assert denied.value.code == "podcast_stage_denied"


class _AuthorityChangingProvider(_Provider):
    def __init__(self, engine) -> None:
        super().__init__(score=9.0)
        self.engine = engine

    async def reduce(self, **kwargs):
        with Session(self.engine) as session:
            episode = session.get(ArticleRecord, "episode-50")
            analysis = session.get(ArticleAnalysisRecord, "episode-50")
            episode.analysis_authority_id = "remote-producer"
            analysis.authority_id = "remote-producer"
            session.add(episode)
            session.add(analysis)
            session.commit()
        return await super().reduce(**kwargs)


def test_full_analysis_does_not_overwrite_authority_changed_during_llm(engine):
    process = _request(
        engine, "episode-50", override=False, key="authority-arrives-before-persist"
    )
    config = _config()
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="persistence-authority-worker",
                    lease_seconds=120,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://llm.example.test",
                        api_key="test",
                        model="test-model",
                    ),
                ),
                podcast_config=config,
                policy=PodcastStagePolicy(config),
                provider=_AuthorityChangingProvider(engine),
            )
        )
        assert step.action == "not_required"
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        assert analysis.authority_id == "remote-producer"
        assert analysis.quality_score == 6.0
        pending = session.get(PodcastProcessingRecord, process.id)
        assert pending.processing_status == "not_required"
        assert pending.eligibility_status == "blocked_source"


def test_map_reduce_covers_every_character_and_exact_runtime_threshold_is_premium(engine):
    process = _request(
        engine, "episode-50", override=False, key="worker-episode-50"
    )
    config = replace(_config(), premium_score_threshold=8.25)
    provider = _Provider(score=7.5)
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="analysis-worker",
                    lease_seconds=120,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://llm.example.test",
                        api_key="test",
                        model="test-model",
                    ),
                    chunk_chars=24,
                    map_concurrency=2,
                ),
                podcast_config=config,
                policy=PodcastStagePolicy(config),
                provider=provider,
            ),
        )
    assert step.action == "completed"
    assert "".join(provider.chunks).startswith("开头证据")
    assert "中段论据" in "".join(provider.chunks)
    assert "".join(provider.chunks).endswith("结尾结论")
    with Session(engine) as session:
        persisted = session.get(PodcastProcessingRecord, process.id)
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        assert persisted.processing_status == "ready"
        assert analysis.analysis_basis == "publisher_transcript"
        assert analysis.quality_score == 7.5
        assert analysis.podcast_initial_score == 6.0
        assert analysis.podcast_final_score == 7.5
        diagnostics = json.loads(analysis.analysis_diagnostics_json)
        assert diagnostics["coverage"]["source_chars"] == len("".join(provider.chunks))
        assert diagnostics["coverage"]["chunk_count"] == len(provider.chunks)
        assert diagnostics["final_premium_threshold"] == 7.5
        assert diagnostics["final_premium"] is True


def test_split_and_reduce_never_drop_middle_or_end(engine):
    text = "A" * 31 + "\n" + "M" * 31 + "\n" + "Z" * 31
    chunks = split_transcript(text, max_chars=20)
    assert "".join(chunk.text for chunk in chunks) == text
    provider = _Provider(score=7.9)
    episode = ArticleRecord(
        id="standalone",
        title="Standalone",
        content_type="podcast_episode",
        source_id="podcast-source",
        source_url="https://example.test/standalone",
        publish_date=NOW.isoformat(),
        fetched_date=NOW.isoformat(),
        content="show notes",
    )
    from services.article_analysis import analysis_input_from_article

    validated, mapped_chunks, _mapped = asyncio.run(
        analyze_transcript(
            title=episode.title,
            transcript=text,
            article_input=analysis_input_from_article(episode, None),
            active_tags=[],
            provider=provider,
            chunk_chars=20,
            map_concurrency=3,
        )
    )
    assert "".join(chunk.text for chunk in mapped_chunks) == text
    assert validated.result.quality_score == 7.9


def test_transcript_result_blocks_late_show_notes_refresh(engine):
    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        analysis.analysis_basis = "publisher_transcript"
        analysis.quality_score = 8.0
        article = session.get(ArticleRecord, "episode-50")
        article.content = "late changed show notes"
        session.add(article)
        session.add(analysis)
        session.commit()
        assert queue_article_analysis(
            session, "episode-50", force=True, now=NOW
        ) == "unchanged"
        persisted = session.get(ArticleAnalysisRecord, "episode-50")
        assert persisted.analysis_basis == "publisher_transcript"
        assert persisted.quality_score == 8.0
        from services.article_analysis import compute_content_hash

        assert persisted.content_hash == compute_content_hash(article)


def test_full_analysis_claim_filter_and_expired_lease_recovery(engine):
    process = _request(engine, "episode-50", override=False, key="lease-recovery")
    policy = PodcastStagePolicy(_config())
    with Session(engine) as session:
        first = claim_next_processing(
            session,
            worker_id="old-worker",
            lease_seconds=1,
            policy=policy,
            requested_target="full_analysis",
            now=NOW,
        )
        assert first is not None
        recovered = claim_next_processing(
            session,
            worker_id="new-worker",
            lease_seconds=30,
            policy=policy,
            requested_target="full_analysis",
            now=NOW + dt.timedelta(seconds=2),
        )
        assert recovered is not None
        assert recovered.processing_id == process.id
        assert recovered.fencing_token == first.fencing_token + 1
        with pytest.raises(PodcastLeaseLost):
            heartbeat_processing(
                session, first, lease_seconds=30, policy=policy, now=NOW
            )


def test_analysis_failure_retries_after_backoff(engine):
    process = _request(engine, "episode-50", override=False, key="retry-analysis")
    config = _config()
    worker = FullAnalysisWorkerConfig(
        worker_id="retry-worker",
        lease_seconds=30,
        retry_seconds=10,
        llm_config=LLMConfig(
            base_url="https://llm.example.test", api_key="test", model="test"
        ),
        chunk_chars=24,
    )
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=worker,
                podcast_config=config,
                policy=PodcastStagePolicy(config),
                provider=_FailProvider(),
                now=NOW,
            )
        )
        assert step.action == "retry_wait"
        failed = session.get(PodcastProcessingRecord, process.id)
        assert failed.processing_status == "retry_wait"
        session.rollback()
        recovered = claim_next_processing(
            session,
            worker_id="retry-worker-2",
            lease_seconds=30,
            policy=PodcastStagePolicy(config),
            requested_target="full_analysis",
            now=NOW + dt.timedelta(seconds=11),
        )
        assert recovered is not None
        assert recovered.processing_id == process.id


@pytest.mark.parametrize("settled", [False, True])
def test_local_crash_window_is_recomputed_after_lease_expiry(engine, settled):
    # Keep the persisted budget period valid while making the injected clock
    # unmistakably older than the wall clock.  Any boundary that accidentally
    # switches back to datetime.now() will therefore fence this lease.
    process = _request(engine, "episode-50", override=False, key="settled-crash")
    config = _config()
    # The period is stamped from the wall clock, so start from that month
    # instead of a fixed date that expires when the calendar month rolls over.
    logical_now = (
        dt.datetime.strptime(process.budget_period, "%Y-%m")
        .replace(tzinfo=ZoneInfo(config.budget_timezone))
        .astimezone(dt.timezone.utc)
    )
    policy = PodcastStagePolicy(config)
    with Session(engine) as session:
        claim = claim_next_processing(
            session,
            worker_id="crashed-worker",
            lease_seconds=1,
            policy=policy,
            requested_target="full_analysis",
            now=logical_now,
        )
        assert claim is not None
        persisted = session.get(PodcastProcessingRecord, process.id)
        budget = (
            persisted.budget_scope,
            persisted.budget_period,
            persisted.budget_limit_minor,
            persisted.input_content_hash,
        )
        session.rollback()
        attempt = begin_stage_attempt(
            session,
            claim,
            input_hash=budget[3],
            settings_fingerprint="2" * 64,
            provider_name="dorami",
            model_name="test",
            provider_revision="test",
            provider_request_key="settled-crash-request",
            execution_kind="local",
            estimated_cost_minor=0,
            budget_scope=budget[0],
            budget_period=budget[1],
            budget_limit_minor=budget[2],
            reservation_idempotency_key="settled-crash-reservation",
            policy=policy,
            now=logical_now,
        )
        if settled:
            settle_attempt_cost(
                session,
                claim,
                attempt_id=attempt.id,
                settlement_key="settled-crash-cost",
                actual_cost_minor=0,
                policy=policy,
                now=logical_now,
            )
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="recovery-worker",
                    lease_seconds=30,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://llm.example.test",
                        api_key="test",
                        model="test",
                    ),
                    chunk_chars=24,
                ),
                podcast_config=config,
                policy=policy,
                provider=_Provider(),
                now=logical_now + dt.timedelta(seconds=2),
            )
        )
        assert step.action == "completed"
        assert session.get(PodcastProcessingRecord, process.id).processing_status == "ready"


def test_new_publisher_artifact_creates_a_new_full_analysis_run(engine):
    original = _request(engine, "episode-50", override=False, key="refresh-original")
    with Session(engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        analysis.analysis_basis = "publisher_transcript"
        analysis.transcript_artifact_id = "publisher-episode-50"
        session.add(analysis)
        replacement_text = "new publisher transcript"
        replacement = PodcastTextArtifactRecord(
            id="publisher-episode-50-v2",
            episode_id="episode-50",
            kind="publisher_transcript",
            version=2,
            content_hash=hashlib.sha256(replacement_text.encode()).hexdigest(),
            inline_text=replacement_text,
            language="zh-CN",
            authority_id="",
            provenance_json='{"format":"text"}',
            created_at=(NOW + dt.timedelta(seconds=1)).isoformat(),
        )
        session.add(replacement)
        session.flush()
        publication = session.get(
            PodcastTextPublicationRecord, "episode-50:publisher_transcript"
        )
        publication.artifact_id = replacement.id
        publication.updated_at = (NOW + dt.timedelta(seconds=1)).isoformat()
        session.add(publication)
        session.commit()
    refreshed = _request(engine, "episode-50", override=False, key="refresh-v2")
    assert refreshed.id != original.id
    assert refreshed.input_artifact_id == "publisher-episode-50-v2"


def test_real_provider_reduction_keeps_first_middle_and_last_chunk_ids(monkeypatch):
    captured = {}

    async def fake_chat_completion(*, messages, **_kwargs):
        captured.setdefault("compactions", []).append(messages[-1].content)
        return '{"summary":"bounded evidence"}'

    async def fake_analysis(article, _tags, _config, **_kwargs):
        captured["article"] = article
        return {"quality_score": 8.0}

    import services.podcast_full_analysis as module

    monkeypatch.setattr(module, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(
        module.article_analysis, "analyze_article_with_llm", fake_analysis
    )
    provider = OpenAiFullAnalysisProvider(
        LLMConfig(
            base_url="https://llm.example.test", api_key="test", model="test"
        )
    )
    article = module.article_analysis.AnalysisInput(
        article_id="episode",
        title="Episode",
        body="ignored",
        content_type="podcast_episode",
        source_id="source",
        publish_date=NOW.isoformat(),
        fetched_date=NOW.isoformat(),
        credentialed_source=False,
        source_owner_or_domain="source",
        analysis_basis="publisher_transcript",
    )
    evidence = [
        {"_chunk_index": index, "evidence": f"chunk-{index}-" + "x" * 1800}
        for index in range(21)
    ]
    asyncio.run(
        provider.reduce(article=article, active_tags=[], mapped_evidence=evidence)
    )
    final_input = captured["article"]
    assert final_input.analysis_basis == "publisher_transcript"
    assert '"coverage_manifest":[0,1,2' in final_input.body
    assert ",10," in final_input.body
    assert final_input.body.find("20]") > 0
    assert len(final_input.body) < 24_000


def test_one_click_orchestration_ingests_publisher_before_audio(monkeypatch):
    import api.app as app_module

    sentinel = object()
    calls = {"enqueue": 0, "publisher": 0, "audio": 0}

    def fake_enqueue(*_args, **_kwargs):
        calls["enqueue"] += 1
        if calls["enqueue"] == 1:
            raise PodcastAdminError("podcast_artifact_not_ready", status_code=409)
        return sentinel

    async def fake_publisher(*_args, **_kwargs):
        calls["publisher"] += 1

    async def fake_audio(*_args, **_kwargs):
        calls["audio"] += 1

    monkeypatch.setattr(
        app_module.podcast_processing_admin_service,
        "request_processing",
        fake_enqueue,
    )
    monkeypatch.setattr(
        app_module.podcast_processing_admin_service,
        "require_full_analysis_authority",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_module.podcast_publisher_transcript_service,
        "ingest_publisher_transcript",
        fake_publisher,
    )
    monkeypatch.setattr(
        app_module.podcast_source_media_service,
        "validate_source_media",
        fake_audio,
    )
    result = asyncio.run(
        app_module.enqueue_podcast_processing_with_input(
            episode_id="episode",
            target="full_analysis",
            selection_override=True,
            idempotency_key="one-click-full-analysis",
            reason="force full analysis",
            actor="admin",
        )
    )
    assert result is sentinel
    assert calls == {"enqueue": 2, "publisher": 1, "audio": 0}


def test_podcast_projection_exposes_durable_status_basis_and_thresholds():
    initial = SimpleNamespace(
        status="succeeded",
        analysis_basis="podcast_show_notes",
        quality_score=6.0,
    )
    failed = SimpleNamespace(
        id="processing-1",
        requested_target="full_analysis",
        processing_status="retry_wait",
        stage="analyze",
        input_artifact_kind="publisher_transcript",
        attempt_count=3,
        error_message="temporary failure",
    )
    projected = _podcast_projection({}, initial, failed, include_diagnostics=True)
    assert projected["status"] == projected["processing_status"] == "retry_wait"
    assert projected["stage"] == "analyze"
    assert projected["error"] == "temporary failure"
    assert projected["retryable"] is True
    assert projected["attempt_count"] == 3
    assert projected["transcript_source"] == "publisher_transcript"
    assert projected["full_analysis_candidate"] is True
    assert projected["final_premium"] is None

    final = SimpleNamespace(
        status="succeeded",
        analysis_basis="asr_transcript",
        quality_score=8.5,
    )
    completed = SimpleNamespace(
        id="processing-2",
        requested_target="full_analysis",
        processing_status="ready",
        stage="analyze",
        input_artifact_kind="source_media_snapshot",
        error_message="",
    )
    projected = _podcast_projection(
        {}, final, completed, premium_score_threshold=8.5,
        include_diagnostics=True,
    )
    assert projected["transcript_source"] == "asr_transcript"
    assert projected["full_analysis_candidate"] is False
    assert projected["final_premium"] is True

    final.quality_score = 8.5001
    projected = _podcast_projection(
        {}, final, completed, premium_score_threshold=8.5,
        include_diagnostics=True,
    )
    assert projected["final_premium"] is True

    record = ArticleRecord(
        id="premium-boundary",
        title="Premium boundary",
        content_type="podcast_episode",
        source_id="podcast-source",
        source_url="https://example.test/premium-boundary",
        publish_date=NOW.isoformat(),
        fetched_date=NOW.isoformat(),
        content="show notes",
    )
    final.quality_score = 8.5
    item = serialize_article_list_item(
        record,
        include_content=False,
        analysis=final,
        premium_score_threshold=8.5,
        include_podcast_diagnostics=True,
    )
    assert item["is_premium_podcast"] is True
    assert item["podcast"]["final_premium"] is True
    final.quality_score = 8.5001
    item = serialize_article_list_item(
        record,
        include_content=False,
        analysis=final,
        premium_score_threshold=8.5,
        include_podcast_diagnostics=True,
    )
    assert item["is_premium_podcast"] is True
    assert item["podcast"]["final_premium"] is True

    projected = _podcast_projection(
        {
            "premium_guide": {
                "status": "failed",
                "failed_stage": "synthesizing",
                "error": "TTS provider timeout",
                "internal_detail": "must not leak",
            }
        },
        final,
        completed,
        include_diagnostics=True,
    )
    assert projected["premium_guide"] == {
        "status": "failed",
        "failed_stage": "synthesizing",
        "error": "TTS provider timeout",
        "error_code": "",
        "next_retry_at": "",
        "audio_ready": False,
        "blog_ready": False,
        "script_ready": False,
        "mode": "",
        "language": "",
        "language_source": "",
    }
    with_blog = _podcast_projection(
        {"premium_guide": {"status": "ready"}},
        final,
        completed,
        published_text_kinds={"digest_blog_zh"},
    )
    assert with_blog["premium_guide"]["blog_ready"] is True
    assert with_blog["premium_guide"]["audio_ready"] is False

    reader_safe = _podcast_projection(
        {
            "premium_guide": {
                "status": "failed",
                "failed_stage": "synthesizing",
                "error": "provider secret failure",
                "mode": "brief_zh",
            }
        },
        final,
        SimpleNamespace(
            processing_status="retry_wait",
            stage="asr",
            error_message="raw provider quota error",
            error_code="provider_usage_window_unavailable",
        ),
    )
    assert reader_safe["premium_guide"] == {
        "status": "failed",
        "audio_ready": False,
        "blog_ready": False,
        "script_ready": False,
        "mode": "brief_zh",
    }
    assert not {
        "id", "attempt_count", "next_retry_at", "stage", "error", "error_code",
        "transcript_source", "full_analysis_candidate",
    }.intersection(reader_safe)


def test_changed_publisher_locator_is_detected_before_reusing_publication(engine):
    old_url = "https://publisher.example.test/v1.vtt"
    new_url = "https://publisher.example.test/v2.vtt"
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-50")
        episode.extensions_json = json.dumps(
            {"transcripts": [{"url": new_url, "type": "text/vtt"}]}
        )
        artifact = PodcastTextArtifactRecord(
            id="publisher-old-locator",
            episode_id="episode-50",
            kind="publisher_transcript",
            version=2,
            content_hash=hashlib.sha256(b"old locator transcript").hexdigest(),
            inline_text="old locator transcript",
            language="zh-CN",
            authority_id="",
            provenance_json=json.dumps(
                {
                    "format": "text",
                    "url_sha256": hashlib.sha256(old_url.encode()).hexdigest(),
                }
            ),
            created_at=NOW.isoformat(),
        )
        publication = session.get(
            PodcastTextPublicationRecord, "episode-50:publisher_transcript"
        )
        publication.artifact_id = artifact.id
        publication.updated_at = NOW.isoformat()
        session.add(episode)
        session.add(artifact)
        session.add(publication)
        session.commit()
    assert publisher_transcript_refresh_revision(
        engine, episode_id="episode-50"
    ) == hashlib.sha256(new_url.encode()).hexdigest()


def test_stale_publisher_locator_falls_back_to_source_media_asr(engine):
    old_url = "https://publisher.example.test/old.vtt"
    new_url = "https://publisher.example.test/new.vtt"
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-asr")
        episode.extensions_json = json.dumps(
            {
                "audio_url": "https://cdn.example.test/episode-asr.mp3",
                "transcripts": [{"url": new_url, "type": "text/vtt"}],
            }
        )
        text = "stale publisher transcript"
        artifact = PodcastTextArtifactRecord(
            id="stale-publisher-asr",
            episode_id="episode-asr",
            kind="publisher_transcript",
            version=1,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            inline_text=text,
            language="zh-CN",
            authority_id="",
            provenance_json=json.dumps(
                {
                    "format": "text",
                    "url_sha256": hashlib.sha256(old_url.encode()).hexdigest(),
                }
            ),
            created_at=NOW.isoformat(),
        )
        session.add(episode)
        session.add(artifact)
        session.flush()
        session.add(
            PodcastTextPublicationRecord(
                identity="episode-asr:publisher_transcript",
                episode_id="episode-asr",
                kind="publisher_transcript",
                artifact_id=artifact.id,
                status="published",
                authority_id="",
                published_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
            )
        )
        session.commit()
    process = _request(engine, "episode-asr", override=False, key="stale-to-asr")
    assert process.input_artifact_kind == "source_media_snapshot"
    assert process.stage == "asr"


def test_analysis_input_hash_tracks_actual_map_and_reduce_context(engine):
    chunks = split_transcript("first\nmiddle\nlast", max_chars=7)
    article_input = __import__(
        "services.article_analysis", fromlist=["AnalysisInput"]
    ).AnalysisInput(
        article_id="episode-50",
        title="Episode",
        body="show notes",
        content_type="podcast_episode",
        source_id="podcast-source",
        publish_date=NOW.isoformat(),
        fetched_date=NOW.isoformat(),
        credentialed_source=False,
        source_owner_or_domain="podcast-source",
        analysis_basis="publisher_transcript",
    )
    with Session(engine) as session:
        artifact = session.get(PodcastTextArtifactRecord, "publisher-episode-50")
        first = _input_hash(
            artifact,
            chunks,
            mapped=[{"_chunk_index": 0, "evidence": "first"}],
            article_input=article_input,
            active_tags=[],
            reduce_trace={"bounded_evidence": "first"},
            model_name="model-v1",
        )
        second = _input_hash(
            artifact,
            chunks,
            mapped=[{"_chunk_index": 0, "evidence": "changed"}],
            article_input=article_input,
            active_tags=[],
            reduce_trace={"bounded_evidence": "changed"},
            model_name="model-v1",
        )
    assert first != second


@pytest.mark.parametrize("episode_id", ["episode-50", "episode-asr"])
def test_full_analysis_requires_llm_before_any_input_preparation(engine, monkeypatch, episode_id):
    import api.app as app_module

    monkeypatch.setattr("services.daily_brief.resolve_llm_config", lambda _s: LLMConfig())
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))

    def forbidden(*_args, **_kwargs):
        pytest.fail("unconfigured LLM must not prepare input or enqueue ASR")

    monkeypatch.setattr(app_module.podcast_publisher_transcript_service, "ingest_publisher_transcript", forbidden)
    monkeypatch.setattr(app_module.podcast_publisher_transcript_service, "publisher_transcript_refresh_revision", forbidden)
    monkeypatch.setattr(
        app_module.podcast_source_media_service,
        "validate_source_media",
        forbidden,
    )
    with pytest.raises(PodcastAdminError, match="处理能力") as error:
        asyncio.run(app_module.enqueue_podcast_processing_with_input(
            episode_id=episode_id, target="full_analysis", selection_override=True,
            idempotency_key="missing-llm-test", reason="test missing LLM", actor="admin",
        ))
    assert error.value.status_code == 503
    with pytest.raises(PodcastAdminError):
        _request(engine, episode_id, override=True, key="direct-missing-llm")
    with Session(engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []


def _seed_completed_asr(engine):
    process = _request(engine, "episode-asr", override=False, key="original-asr")
    transcript = json.dumps({"text": "complete ASR transcript", "language": "en"})
    content_hash = hashlib.sha256(transcript.encode()).hexdigest()
    with Session(engine) as session:
        original = session.get(PodcastProcessingRecord, process.id)
        original.stage = "analyze"
        original.attempt_count = 1
        session.add(original)
        session.add(PodcastStageAttemptRecord(
            id="successful-asr", processing_id=process.id, stage="asr", attempt_no=1,
            fencing_token=1, lease_token="previous-lease", input_hash=process.input_content_hash,
            output_hash=content_hash, output_artifact_id="complete-asr",
            output_artifact_kind="normalized_transcript", submission_state="succeeded",
            provider_request_key="successful-asr-request", created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(), started_at=NOW.isoformat(),
        ))
        session.flush()
        session.add(PodcastTextArtifactRecord(
            id="complete-asr", episode_id="episode-asr", kind="normalized_transcript",
            version=1, content_hash=content_hash, inline_text=transcript, language="en",
            processing_id=process.id, producing_attempt_id="successful-asr",
            source_artifact_id=process.input_artifact_id,
            source_content_hash=process.input_content_hash, created_at=NOW.isoformat(),
        ))
        session.commit()
    return process


@pytest.mark.parametrize("historical,current_publisher", [(False, False), (True, False), (False, True)])
def test_completed_asr_reuse_respects_current_publisher_locator(engine, historical, current_publisher):
    original = _seed_completed_asr(engine)
    with Session(engine) as session:
        if historical:
            process = session.get(PodcastProcessingRecord, original.id)
            process.processing_status = "ready"
            session.add(process)
        episode = session.get(ArticleRecord, "episode-asr")
        episode.extensions_json = json.dumps({"transcripts": [{"url": "https://example.test/new.vtt", "type": "text/vtt", "language": "en"}]})
        session.add(episode)
        text = "stale publisher"
        session.add(PodcastTextArtifactRecord(
            id="old-publisher", episode_id="episode-asr", kind="publisher_transcript",
            version=1, content_hash=hashlib.sha256(text.encode()).hexdigest(),
            inline_text=text, language="en", authority_id="",
            provenance_json=json.dumps({"format": "vtt" if current_publisher else "text", "url_sha256": hashlib.sha256(
                b"https://example.test/new.vtt" if current_publisher else b"https://example.test/old.vtt"
            ).hexdigest()}),
            created_at=NOW.isoformat(),
        ))
        session.flush()
        session.add(PodcastTextPublicationRecord(
            identity="episode-asr:publisher_transcript", episode_id="episode-asr",
            kind="publisher_transcript", artifact_id="old-publisher", status="published",
            authority_id="", published_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        ))
        session.commit()
    process = _request(engine, "episode-asr", override=True, key="reuse-completed-asr") if historical else original
    if historical:
        assert process.id != original.id
        assert process.input_artifact_kind == "normalized_transcript"
    provider = _Provider()
    with Session(engine) as session:
        step = asyncio.run(run_full_analysis_worker_step(
            session, config=FullAnalysisWorkerConfig(
                worker_id="asr-analysis", lease_seconds=120, retry_seconds=10,
                llm_config=LLMConfig(base_url="https://example.test", api_key="test", model="test"),
            ), podcast_config=_config(), policy=PodcastStagePolicy(_config()), provider=provider,
        ))
        if current_publisher:
            assert step.action == "failed"
            assert provider.chunks == []
            assert session.get(PodcastProcessingRecord, process.id).error_code == "publisher_transcript_preferred"
            return
        assert step.action == "completed"
        assert step.processing_id == process.id
        result = session.get(ArticleAnalysisRecord, "episode-asr")
        assert result.analysis_basis == "asr_transcript"
        assert result.transcript_artifact_id == "complete-asr"
    assert "".join(provider.chunks) == "complete ASR transcript"


def test_historical_asr_reuse_rejects_changed_content(engine):
    from services.podcast_processing import _evaluate_input_binding

    original = _seed_completed_asr(engine)
    with Session(engine) as session:
        process = session.get(PodcastProcessingRecord, original.id)
        process.processing_status = "ready"
        session.add(process)
        session.commit()
    reused = _request(engine, "episode-asr", override=True, key="reuse-with-corruption")
    with Session(engine) as session:
        artifact = session.get(PodcastTextArtifactRecord, "complete-asr")
        artifact.inline_text = '{"text":"changed"}'
        with session.no_autoflush:
            assert _evaluate_input_binding(session, reused, PodcastStagePolicy(_config()))[0] == "invalid_input"
        session.rollback()


def test_historical_asr_reuse_rejects_a_cross_episode_producer(engine):
    from services.podcast_processing import _evaluate_input_binding

    original = _seed_completed_asr(engine)
    with Session(engine) as session:
        producer = session.get(PodcastProcessingRecord, original.id)
        producer.episode_id = "episode-50"
        producer.processing_status = "ready"
        session.add(producer)
        session.commit()
    reused = _request(
        engine, "episode-asr", override=True, key="reuse-cross-episode-producer"
    )
    with Session(engine) as session:
        assert _evaluate_input_binding(
            session, reused, PodcastStagePolicy(_config())
        )[0] == "invalid_input"


@pytest.mark.parametrize("score,expected", [(7.4, []), (7.5, ["episode-50"]), (None, [])])
def test_scheduler_triggers_premium_only_after_successful_final_score(engine, monkeypatch, score, expected):
    from dataclasses import replace
    import api.app as app_module
    import services.podcast_full_analysis as full_analysis

    process = _request(engine, "episode-50", override=False, key="premium-auto")
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-50")
        episode.extensions_json = json.dumps({"duration_seconds": 1801})
        session.add(episode)
        session.commit()
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(app_module, "settings", replace(
        app_module.settings, podcast=replace(_config(), premium_score_threshold=7.5),
        podcast_worker=replace(app_module.settings.podcast_worker, max_steps_per_tick=1),
    ))
    monkeypatch.setattr(app_module, "_configured_podcast_asr_worker", lambda: None)
    monkeypatch.setattr(app_module, "_configured_podcast_full_analysis_worker", lambda: run_full_analysis_worker_step)
    monkeypatch.setattr(full_analysis, "OpenAiFullAnalysisProvider", lambda _config: _Provider(score) if score is not None else _FailProvider())
    scheduled = []

    def schedule(episode_id):
        # Must run on the parent event loop after the transaction is committed.
        assert asyncio.get_running_loop().is_running()
        with Session(engine) as session:
            assert session.get(PodcastProcessingRecord, process.id).processing_status == "ready"
        scheduled.append(episode_id)

    monkeypatch.setattr(app_module, "schedule_podcast_premium_guide", schedule)
    actions = asyncio.run(app_module.execute_podcast_asr_worker_job())
    assert actions == (("completed",) if score is not None else ("retry_wait",))
    assert scheduled == expected


def test_scheduler_recovers_a_committed_premium_candidate_after_restart(
    engine, monkeypatch
):
    from dataclasses import replace
    import api.app as app_module

    process = _request(engine, "episode-50", override=False, key="premium-recovery")
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-50")
        episode.extensions_json = json.dumps({"duration_seconds": 1801})
        session.add(episode)
        session.commit()
    with Session(engine) as session:
        completed = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=FullAnalysisWorkerConfig(
                    worker_id="analysis-before-restart",
                    lease_seconds=120,
                    retry_seconds=10,
                    llm_config=LLMConfig(
                        base_url="https://example.test",
                        api_key="test",
                        model="test",
                    ),
                ),
                podcast_config=_config(),
                policy=PodcastStagePolicy(_config()),
                provider=_Provider(8.6),
            )
        )
    assert completed.action == "completed"

    async def idle_worker(*_args, **_kwargs):
        return FullAnalysisWorkerStep("idle")

    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            podcast=replace(_config(), premium_score_threshold=8.5),
            podcast_worker=replace(
                app_module.settings.podcast_worker, max_steps_per_tick=1
            ),
        ),
    )
    monkeypatch.setattr(app_module, "_configured_podcast_asr_worker", lambda: None)
    monkeypatch.setattr(
        app_module, "_configured_podcast_full_analysis_worker", lambda: idle_worker
    )
    scheduled = []
    monkeypatch.setattr(
        app_module, "schedule_podcast_premium_guide", scheduled.append
    )

    assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == ("idle",)
    assert scheduled == [process.episode_id]


def test_scheduler_triggers_premium_after_asr_backed_full_analysis(
    engine, monkeypatch
):
    from dataclasses import replace
    import api.app as app_module
    import services.podcast_full_analysis as full_analysis

    process = _seed_completed_asr(engine)
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-asr")
        episode.extensions_json = json.dumps({"duration_seconds": 1801})
        session.add(episode)
        session.commit()
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            podcast=replace(_config(), premium_score_threshold=8.5),
            podcast_worker=replace(
                app_module.settings.podcast_worker, max_steps_per_tick=1
            ),
        ),
    )
    monkeypatch.setattr(app_module, "_configured_podcast_asr_worker", lambda: None)
    monkeypatch.setattr(
        app_module,
        "_configured_podcast_full_analysis_worker",
        lambda: run_full_analysis_worker_step,
    )
    monkeypatch.setattr(
        full_analysis,
        "OpenAiFullAnalysisProvider",
        lambda _config: _Provider(8.6),
    )
    scheduled = []
    monkeypatch.setattr(
        app_module, "schedule_podcast_premium_guide", scheduled.append
    )

    assert asyncio.run(app_module.execute_podcast_asr_worker_job()) == ("completed",)
    assert scheduled == [process.episode_id]


def test_premium_guide_rejects_show_notes_score_even_with_transcript(engine):
    from services.podcast_premium_guides import PremiumGuideError, _source_transcript

    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-50")
        episode.extensions_json = '{"duration_seconds":1800}'
        session.add(episode)
        session.commit()
        with pytest.raises(PremiumGuideError, match="全文分析尚未完成"):
            _source_transcript(session, "episode-50", _config())


@pytest.mark.parametrize("status", ["failed", "not_required"])
def test_full_analysis_terminal_retry_reuses_processing_and_checks_llm(engine, monkeypatch, status):
    from services.podcast_processing_admin import retry_processing

    process = _request(engine, "episode-50", override=True, key="terminal-original")
    with Session(engine) as session:
        record = session.get(PodcastProcessingRecord, process.id)
        record.processing_status = status
        session.add(record)
        session.commit()
    retried = retry_processing(
        engine, _registry(), _config(), processing_id=process.id,
        idempotency_key="terminal-retry", expected_attempt_count=0,
        reason="retry full analysis", actor="admin",
    )
    assert retried.id == process.id
    assert retried.processing_status == "queued"
    assert retried.attempt_count == 0
    with Session(engine) as session:
        record = session.get(PodcastProcessingRecord, process.id)
        record.processing_status = status
        session.add(record)
        session.commit()
    monkeypatch.setattr("services.daily_brief.resolve_llm_config", lambda _s: LLMConfig())
    with pytest.raises(PodcastAdminError) as error:
        retry_processing(
            engine, _registry(), _config(), processing_id=process.id,
            idempotency_key="terminal-retry-no-llm", expected_attempt_count=0,
            reason="retry without LLM", actor="admin",
        )
    assert error.value.code == "podcast_provider_unavailable"


# ---------- 租约路径与去重(issue #318 PR-2) ----------
#
# 生产上同一处理单连续 100 次、194 次以「租约到期」收场:准备输入的重查询排在认领
# 之后、第一次续租之前,超过租约后旧时间仍让 attempt 建得起来、模型调用照发,结果
# 提交不了,下一拍立刻重来——没有日志、没有退避、没有上限。


def _worker_config(*, lease_seconds: int, worker_id: str = "lease-worker") -> FullAnalysisWorkerConfig:
    return FullAnalysisWorkerConfig(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_seconds=10,
        llm_config=LLMConfig(
            base_url="https://llm.example.test", api_key="test", model="test"
        ),
        chunk_chars=24,
    )


def _attempts(engine, processing_id: str) -> list[PodcastStageAttemptRecord]:
    with Session(engine) as session:
        return list(session.exec(
            select(PodcastStageAttemptRecord)
            .where(
                PodcastStageAttemptRecord.processing_id == processing_id,
                PodcastStageAttemptRecord.stage == "analyze",
            )
            .order_by(PodcastStageAttemptRecord.attempt_no)
        ).all())


def _fast_heartbeat(monkeypatch, seconds: float = 0.02) -> None:
    """把续租节拍压到几十毫秒:用例靠事件对齐,不靠墙钟上的租约长短。"""

    from services import podcast_full_analysis

    real = podcast_full_analysis.run_under_lease

    async def fast(work, *, renew, heartbeat_seconds):
        return await real(work, renew=renew, heartbeat_seconds=seconds)

    monkeypatch.setattr(podcast_full_analysis, "run_under_lease", fast)


def test_slow_input_preparation_stays_inside_the_lease(engine, monkeypatch):
    """准备输入在 attempt 之内、续租照常进行:准备期间能看到租约被续上。"""

    import threading

    from services import article_analysis, podcast_full_analysis

    process = _request(engine, "episode-50", override=False, key="slow-prepare")
    _fast_heartbeat(monkeypatch)
    renewals = 0
    renewed_twice = threading.Event()
    real_renew = podcast_full_analysis.heartbeat_processing

    def counting(*args, **kwargs):
        nonlocal renewals
        claim = real_renew(*args, **kwargs)
        renewals += 1
        if renewals >= 2:
            renewed_twice.set()
        return claim

    monkeypatch.setattr(podcast_full_analysis, "heartbeat_processing", counting)
    real_tags = article_analysis.load_relevant_active_tags
    seen: list[bool] = []

    def slow(session, episode, **kwargs):
        # 准备输入「很慢」:一直等到租约被续了两次才返回。它若留在事件循环里,
        # 续租根本跑不起来,这里只会等到超时。
        seen.append(renewed_twice.wait(timeout=20))
        return real_tags(session, episode, **kwargs)

    monkeypatch.setattr(article_analysis, "load_relevant_active_tags", slow)
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=_worker_config(lease_seconds=600),
                podcast_config=_config(),
                policy=PodcastStagePolicy(_config()),
                provider=_Provider(),
            )
        )
    assert seen == [True]
    assert step.action == "completed"
    attempts = _attempts(engine, process.id)
    assert [a.submission_state for a in attempts] == ["succeeded"]
    with Session(engine) as session:
        assert session.get(PodcastProcessingRecord, process.id).processing_status == "ready"


class _StallingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.started = 0
        self.cancelled = 0
        self.reduced = False

    async def map_chunk(self, **kwargs):
        self.started += 1
        try:
            # 永远等不到:只有被取消才会结束(上限只为改坏时不至于挂死)。
            await asyncio.wait_for(asyncio.Event().wait(), timeout=20)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().map_chunk(**kwargs)

    async def reduce(self, **kwargs):
        self.reduced = True
        return await super().reduce(**kwargs)


def test_losing_the_lease_cancels_model_calls_and_leaves_recovery_to_the_reclaim(
    engine, monkeypatch, caplog
):
    import logging

    from services import podcast_full_analysis

    process = _request(engine, "episode-50", override=False, key="lease-lost")
    _fast_heartbeat(monkeypatch)
    provider = _StallingProvider()

    def fenced(*_args, **_kwargs):
        raise PodcastLeaseLost("processing lease is expired or fenced")

    monkeypatch.setattr(podcast_full_analysis, "heartbeat_processing", fenced)
    with caplog.at_level(logging.WARNING, logger="services.podcast_full_analysis"):
        with Session(engine) as session:
            step = asyncio.run(
                run_full_analysis_worker_step(
                    session,
                    config=_worker_config(lease_seconds=600),
                    podcast_config=_config(),
                    policy=PodcastStagePolicy(_config()),
                    provider=provider,
                )
            )
    # 第一次续租发现失租就中止:在途的分段调用被取消,而不是等它们跑完。
    assert step.action == "lease_lost"
    assert provider.started > 0 and provider.cancelled == provider.started
    assert provider.reduced is False
    assert any("失去租约" in record.getMessage() for record in caplog.records)
    # 失租的执行者不回写:attempt 留给重领路径。
    [attempt] = _attempts(engine, process.id)
    assert attempt.submission_state == "prepared"
    with Session(engine) as session:
        assert session.get(PodcastProcessingRecord, process.id).processing_status == "running"
    with Session(engine) as session:
        recovered = claim_next_processing(
            session,
            worker_id="next-worker",
            lease_seconds=30,
            policy=PodcastStagePolicy(_config()),
            requested_target="full_analysis",
            now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        )
    assert recovered is not None  # 第一次失租:立即重领
    [attempt] = _attempts(engine, process.id)
    assert attempt.error_code == "lease_expired_local_attempt"


def test_a_transient_renewal_failure_does_not_abort_the_analysis(
    engine, monkeypatch, caplog
):
    import logging

    from services import podcast_full_analysis

    _request(engine, "episode-50", override=False, key="renew-hiccup")
    _fast_heartbeat(monkeypatch)
    real = podcast_full_analysis.heartbeat_processing
    calls: list[str] = []

    def flaky(*args, **kwargs):
        calls.append("renew")
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(podcast_full_analysis, "heartbeat_processing", flaky)

    class Slow(_Provider):
        async def reduce(self, **kwargs):
            # 等到第一次(失败的)续租之后又成功续上一次,再交结果。
            for _ in range(2000):
                if len(calls) >= 2:
                    break
                await asyncio.sleep(0.01)
            return await super().reduce(**kwargs)

    with caplog.at_level(logging.WARNING, logger="services.podcast_full_analysis"):
        with Session(engine) as session:
            step = asyncio.run(
                run_full_analysis_worker_step(
                    session,
                    config=_worker_config(lease_seconds=600),
                    podcast_config=_config(),
                    policy=PodcastStagePolicy(_config()),
                    provider=Slow(),
                )
            )
    assert len(calls) >= 2 and step.action == "completed"
    assert any("续租暂时失败" in record.getMessage() for record in caplog.records)


def test_failures_are_logged_instead_of_silently_retried(engine, caplog):
    import logging

    _request(engine, "episode-50", override=False, key="logged-failure")
    with caplog.at_level(logging.WARNING, logger="services.podcast_full_analysis"):
        with Session(engine) as session:
            step = asyncio.run(
                run_full_analysis_worker_step(
                    session,
                    config=_worker_config(lease_seconds=30),
                    podcast_config=_config(),
                    policy=PodcastStagePolicy(_config()),
                    provider=_FailProvider(),
                )
            )
    assert step.action == "retry_wait"
    assert any("Podcast 全文分析失败" in record.getMessage() for record in caplog.records)


def _begin_local_attempt(session, claim, *, now, tag: str):
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    budget = (
        process.budget_scope, process.budget_period,
        process.budget_limit_minor, process.input_content_hash,
    )
    session.rollback()
    return begin_stage_attempt(
        session,
        claim,
        input_hash=budget[3],
        settings_fingerprint="3" * 64,
        provider_name="dorami",
        model_name="test",
        provider_revision="test",
        provider_request_key=f"ladder-request-{tag}",
        execution_kind="local",
        estimated_cost_minor=0,
        budget_scope=budget[0],
        budget_period=budget[1],
        budget_limit_minor=budget[2],
        reservation_idempotency_key=f"ladder-reservation-{tag}",
        policy=PodcastStagePolicy(_config()),
        now=now,
    )


def test_repeated_local_lease_expiry_backs_off_and_then_stops(engine, caplog):
    import logging

    from services.podcast_processing import (
        LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS,
        LOCAL_LEASE_EXPIRY_EXHAUSTED_CODE,
    )

    process = _request(engine, "episode-50", override=False, key="lease-ladder")
    config = _config()
    policy = PodcastStagePolicy(config)
    # 预算周期按墙上时钟落的月份:逻辑时钟从该月起算,整条阶梯约 1.5 小时,不跨月。
    clock = (
        dt.datetime.strptime(process.budget_period, "%Y-%m")
        .replace(tzinfo=ZoneInfo(config.budget_timezone))
        .astimezone(dt.timezone.utc)
    )

    def claim_at(moment):
        with Session(engine) as session:
            claim = claim_next_processing(
                session, worker_id="ladder", lease_seconds=1, policy=policy,
                requested_target="full_analysis", now=moment,
            )
            if claim is not None:
                _begin_local_attempt(session, claim, now=moment, tag=moment.isoformat())
            return claim

    def row():
        with Session(engine) as session:
            return session.get(PodcastProcessingRecord, process.id)

    assert LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS[0] == 0
    assert claim_at(clock) is not None
    with caplog.at_level(logging.WARNING, logger="services.podcast_processing"):
        for streak, delay in enumerate(LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS, start=1):
            clock += dt.timedelta(seconds=2)  # 租约 1 秒,已过期
            if delay == 0:
                assert claim_at(clock) is not None  # 第一次失租:立即重领(重启是常态)
                continue
            assert claim_at(clock) is None
            parked = row()
            assert parked.processing_status == "retry_wait"
            assert parked.error_code == "lease_expired_local_attempt"
            assert parked.lease_token is None
            assert dt.datetime.fromisoformat(parked.next_retry_at) == clock + dt.timedelta(seconds=delay)
            assert claim_at(clock + dt.timedelta(seconds=delay - 1)) is None  # 没到点不重领
            clock += dt.timedelta(seconds=delay)
            assert claim_at(clock) is not None, streak
        clock += dt.timedelta(seconds=2)
        assert claim_at(clock) is None  # 用完:转终态
    final = row()
    assert final.processing_status == "failed"
    assert final.error_code == LOCAL_LEASE_EXPIRY_EXHAUSTED_CODE
    assert final.finished_at and final.lease_token is None and final.next_retry_at is None
    attempts = _attempts(engine, process.id)
    assert len(attempts) == len(LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS) + 1
    assert {a.error_code for a in attempts} == {"lease_expired_local_attempt"}
    assert attempts[-1].submission_state == "failed_terminal"
    assert [a.submission_state for a in attempts[:-1]] == ["failed_retryable"] * (len(attempts) - 1)
    assert claim_at(clock + dt.timedelta(days=1)) is None  # 终态不会自己再起来
    messages = [record.getMessage() for record in caplog.records]
    assert sum("租约到期" in message for message in messages) == len(LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS)
    assert any("已达上限" in message for message in messages)


def test_a_different_failure_resets_the_lease_expiry_streak(engine):
    from services.podcast_processing import fail_stage_attempt

    process = _request(engine, "episode-50", override=False, key="streak-reset")
    config = _config()
    policy = PodcastStagePolicy(config)
    clock = (
        dt.datetime.strptime(process.budget_period, "%Y-%m")
        .replace(tzinfo=ZoneInfo(config.budget_timezone))
        .astimezone(dt.timezone.utc)
    )
    with Session(engine) as session:
        first = claim_next_processing(
            session, worker_id="w", lease_seconds=1, policy=policy,
            requested_target="full_analysis", now=clock,
        )
        _begin_local_attempt(session, first, now=clock, tag="a")
        clock += dt.timedelta(seconds=2)
        second = claim_next_processing(  # 第 1 次失租:立即重领
            session, worker_id="w", lease_seconds=30, policy=policy,
            requested_target="full_analysis", now=clock,
        )
        attempt = _begin_local_attempt(session, second, now=clock, tag="b")
        fail_stage_attempt(  # 这一次是普通失败,不是失租
            session, second, attempt_id=attempt.id, error_code="full_analysis_failed",
            redacted_error_message="temporary", retryable=True,
            retry_at=clock + dt.timedelta(seconds=10), policy=policy, now=clock,
        )
        clock += dt.timedelta(seconds=11)
        third = claim_next_processing(
            session, worker_id="w", lease_seconds=1, policy=policy,
            requested_target="full_analysis", now=clock,
        )
        _begin_local_attempt(session, third, now=clock, tag="c")
        clock += dt.timedelta(seconds=2)
        # 中间隔了一次别的失败:这又是「第 1 次」失租,立即重领而不是退避。
        assert claim_next_processing(
            session, worker_id="w", lease_seconds=30, policy=policy,
            requested_target="full_analysis", now=clock,
        ) is not None


def test_auto_landing_leaves_a_live_runs_own_transcript_alone(engine, monkeypatch):
    """从源音频起步的处理单产出转写后会自己接着分析;自动入队不该为它再建一单。"""

    import api.app as app_module

    process = _seed_completed_asr(engine)
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    with Session(engine) as session:
        assert session.get(PodcastProcessingRecord, process.id).processing_status in {
            "queued", "running", "retry_wait",
        }
        assert app_module._resolve_podcast_landing_candidate(session, "episode-asr") is None

    # 那一单终态失败后,这份转写重新成为候选(由新的一单来分析)。
    with Session(engine) as session:
        row = session.get(PodcastProcessingRecord, process.id)
        row.processing_status = "failed"
        row.finished_at = NOW.isoformat()
        session.add(row)
        session.commit()
    with Session(engine) as session:
        candidate = app_module._resolve_podcast_landing_candidate(session, "episode-asr")
    assert candidate is not None
    assert candidate.episode_id == "episode-asr" and candidate.needs_source_media is False


def test_a_lease_that_ran_out_before_the_attempt_starts_makes_no_model_calls(
    engine, monkeypatch
):
    """认领之后、建 attempt 之前就过了租约:用当下的时间校验,不建 attempt、不发模型调用。

    此前这里用步骤开始时的旧时间校验,过期的租约照样建得起 attempt。
    """

    import time

    from services import podcast_full_analysis

    process = _request(engine, "episode-50", override=False, key="late-attempt")
    real = podcast_full_analysis._transcript_text

    def slow(artifact, config):
        time.sleep(1.3)  # 租约 1 秒
        return real(artifact, config)

    monkeypatch.setattr(podcast_full_analysis, "_transcript_text", slow)
    provider = _Provider()
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=_worker_config(lease_seconds=1),
                podcast_config=_config(),
                policy=PodcastStagePolicy(_config()),
                provider=provider,
            )
        )
    assert step.action == "lease_lost"
    assert provider.chunks == []
    assert _attempts(engine, process.id) == []


def test_live_run_ownership_is_specific_to_the_transcript_and_the_episode(engine):
    from services.podcast_processing_admin import transcript_is_owned_by_live_processing

    process = _seed_completed_asr(engine)

    def owned(**overrides) -> bool:
        arguments = {"episode_id": "episode-asr", "artifact_id": "complete-asr", **overrides}
        with Session(engine) as session:
            return transcript_is_owned_by_live_processing(session, **arguments)

    assert owned() is True
    assert owned(artifact_id="some-other-transcript") is False  # 新出现的别的输入不受影响
    assert owned(episode_id="episode-50") is False
    assert owned(target="digest_blog") is False
    lease = dict(
        lease_owner="w", lease_token="t",
        lease_expires_at=(NOW + dt.timedelta(minutes=5)).isoformat(),
    )
    no_lease = dict(lease_owner=None, lease_token=None, lease_expires_at=None)
    for status, fields, expected in (
        ("retry_wait", {**no_lease, "next_retry_at": NOW.isoformat()}, True),
        ("running", {**lease, "next_retry_at": None}, True),
        ("failed", {**no_lease, "next_retry_at": None, "finished_at": NOW.isoformat()}, False),
        ("reconciliation_required", {**no_lease, "next_retry_at": None}, False),
    ):
        with Session(engine) as session:
            row = session.get(PodcastProcessingRecord, process.id)
            row.processing_status = status
            for name, value in fields.items():
                setattr(row, name, value)
            session.add(row)
            session.commit()
        assert owned() is expected, status


def test_run_under_lease_reports_a_lost_lease_even_if_the_work_finished_in_the_same_tick():
    from services.podcast_full_analysis import run_under_lease

    async def scenario():
        gate = asyncio.Event()

        async def work():
            await gate.wait()
            return "finished"

        async def renew():
            gate.set()  # 工作与失租在同一拍里结束
            raise PodcastLeaseLost("fenced")

        return await run_under_lease(work(), renew=renew, heartbeat_seconds=0.01)

    # 结果已经提交不了,不能当成功交出去。
    with pytest.raises(PodcastLeaseLost):
        asyncio.run(scenario())


def test_run_under_lease_passes_results_and_errors_through():
    from services.podcast_full_analysis import run_under_lease

    renewals: list[int] = []

    async def renew():
        renewals.append(1)

    async def slow_ok():
        while len(renewals) < 3:
            await asyncio.sleep(0.005)
        return 42

    async def boom():
        raise ValueError("model failed")

    assert asyncio.run(run_under_lease(slow_ok(), renew=renew, heartbeat_seconds=0.01)) == 42
    with pytest.raises(ValueError, match="model failed"):
        asyncio.run(run_under_lease(boom(), renew=renew, heartbeat_seconds=0.01))


def test_a_stale_executor_cannot_finalize_over_a_newer_lease(engine, monkeypatch):
    """落定事务从第一条语句起就持有写锁并校验租约:旁人在此之后重领不了。

    此前落定先把处理单读出来在 Python 里比对,再按主键写回——读完到写入之间别的
    执行者可以重领,随后的写入会把新租约和新执行者的结果一起覆盖掉。
    """

    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError

    from services import podcast_full_analysis

    process = _request(engine, "episode-50", override=False, key="stale-finalize")
    # 另一个执行者:独立连接,等锁只等 0.2 秒。
    intruder_engine = create_engine(
        str(engine.url), connect_args={"timeout": 0.2, "check_same_thread": False}
    )
    outcome: list[str] = []
    real_persist = podcast_full_analysis._persist_result

    def persist_with_intruder(session, **kwargs):
        # 此刻旧执行者已经通过了落定的租约检查、还没写结果。
        try:
            with Session(intruder_engine) as other:
                stolen = claim_next_processing(
                    other,
                    worker_id="intruder",
                    lease_seconds=30,
                    policy=PodcastStagePolicy(_config()),
                    requested_target="full_analysis",
                    now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
                )
            outcome.append("reclaimed" if stolen is not None else "nothing-to-claim")
        except OperationalError:
            outcome.append("blocked")
        return real_persist(session, **kwargs)

    monkeypatch.setattr(podcast_full_analysis, "_persist_result", persist_with_intruder)
    try:
        with Session(engine) as session:
            step = asyncio.run(
                run_full_analysis_worker_step(
                    session,
                    config=_worker_config(lease_seconds=600),
                    podcast_config=_config(),
                    policy=PodcastStagePolicy(_config()),
                    provider=_Provider(),
                )
            )
    finally:
        intruder_engine.dispose()
    assert outcome == ["blocked"]
    assert step.action == "completed"
    with Session(engine) as session:
        final = session.get(PodcastProcessingRecord, process.id)
        assert final.processing_status == "ready" and final.lease_token is None


def test_finalize_refuses_when_the_lease_already_moved_on(engine, monkeypatch):
    from services import podcast_full_analysis

    process = _request(engine, "episode-50", override=False, key="moved-on")
    real_finalize = podcast_full_analysis._finalize_result

    def finalize_after_reclaim(session, **kwargs):
        # 落定之前租约已被别人重领(fencing 加一)。
        with Session(engine) as other:
            assert claim_next_processing(
                other,
                worker_id="intruder",
                lease_seconds=30,
                policy=PodcastStagePolicy(_config()),
                requested_target="full_analysis",
                now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            ) is not None
        return real_finalize(session, **kwargs)

    monkeypatch.setattr(podcast_full_analysis, "_finalize_result", finalize_after_reclaim)
    with Session(engine) as session:
        step = asyncio.run(
            run_full_analysis_worker_step(
                session,
                config=_worker_config(lease_seconds=600),
                podcast_config=_config(),
                policy=PodcastStagePolicy(_config()),
                provider=_Provider(),
            )
        )
    assert step.action == "lease_lost"
    with Session(engine) as session:
        current = session.get(PodcastProcessingRecord, process.id)
        assert current.processing_status == "running" and current.lease_owner == "intruder"
        analysis = session.get(ArticleAnalysisRecord, "episode-50")
        assert analysis is None or analysis.analysis_basis == "podcast_show_notes"


def test_an_admin_retry_starts_a_fresh_lease_expiry_ladder(engine):
    from services.podcast_processing import LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS
    from services.podcast_processing_admin import retry_processing

    process = _request(engine, "episode-50", override=False, key="ladder-then-retry")
    config = _config()
    policy = PodcastStagePolicy(config)
    clock = (
        dt.datetime.strptime(process.budget_period, "%Y-%m")
        .replace(tzinfo=ZoneInfo(config.budget_timezone))
        .astimezone(dt.timezone.utc)
    )

    def claim_at(moment):
        with Session(engine) as session:
            claim = claim_next_processing(
                session, worker_id="ladder", lease_seconds=1, policy=policy,
                requested_target="full_analysis", now=moment,
            )
            if claim is not None:
                _begin_local_attempt(session, claim, now=moment, tag=moment.isoformat())
            return claim

    def status():
        with Session(engine) as session:
            row = session.get(PodcastProcessingRecord, process.id)
            return row.processing_status, row.attempt_count

    def exhaust(moment):
        assert claim_at(moment) is not None
        for delay in LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS:
            moment += dt.timedelta(seconds=2)
            if delay:
                assert claim_at(moment) is None
                moment += dt.timedelta(seconds=delay)
            assert claim_at(moment) is not None
        moment += dt.timedelta(seconds=2)
        assert claim_at(moment) is None
        return moment

    clock = exhaust(clock)
    state, attempt_count = status()
    assert state == "failed" and attempt_count == len(LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS) + 1

    retried = retry_processing(
        engine, _registry(), config, processing_id=process.id,
        idempotency_key="ladder-retry", expected_attempt_count=attempt_count,
        reason="再给一轮机会", actor="admin",
    )
    assert retried.processing_status == "queued"
    # 重试之后是完整的新一轮:第一次失租立即重领,之后逐级退避,再用完才失败。
    clock = exhaust(clock + dt.timedelta(seconds=5))
    state, second_count = status()
    assert state == "failed"
    assert second_count == 2 * (len(LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS) + 1)


def test_claim_rolls_back_when_it_fails_midway(engine, monkeypatch):
    """认领函数带着事务边界:中途出错不留下半个事务。"""

    from services import podcast_processing

    _request(engine, "episode-50", override=False, key="claim-rollback")

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom after the claim UPDATE")

    monkeypatch.setattr(podcast_processing, "_latest_active_attempt", broken)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="boom"):
            claim_next_processing(
                session, worker_id="w", lease_seconds=30,
                policy=PodcastStagePolicy(_config()),
                requested_target="full_analysis", now=NOW,
            )
        assert not session.in_transaction()
    with Session(engine) as session:
        row = session.exec(select(PodcastProcessingRecord)).first()
        assert row.processing_status == "queued" and row.lease_token is None


def test_auto_landing_still_surfaces_a_pending_publisher_transcript(engine, monkeypatch):
    """在途处理单拥有当前转写时,待取回的发布方逐字稿仍然要成为候选。"""

    import api.app as app_module

    _seed_completed_asr(engine)
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(
        app_module.podcast_publisher_transcript_service,
        "publisher_transcript_refresh_revision",
        lambda _engine, *, episode_id: "new-locator-0123456789",
    )
    with Session(engine) as session:
        candidate = app_module._resolve_podcast_landing_candidate(session, "episode-asr")
    assert candidate is not None
    assert candidate.revision == "new-locator-0123"


def test_auto_enqueue_reuses_the_live_run_that_owns_the_selected_transcript(engine):
    """巡检时 ASR 还没完成、入队时刚好完成:正式入队处要复用在途的那一单。"""

    owner = _seed_completed_asr(engine)

    def enqueue(**keys):
        return request_processing(
            engine, _registry(), _config(), episode_id="episode-asr",
            target="full_analysis", selection_override=False,
            reason="简介初评达到全文处理线", actor="system", **keys,
        )

    reused = enqueue(idempotency_key_prefix="full-analysis:auto:episode-asr")
    assert reused.id == owner.id
    with Session(engine) as session:
        rows = session.exec(
            select(PodcastProcessingRecord).where(
                PodcastProcessingRecord.episode_id == "episode-asr"
            )
        ).all()
    assert [row.id for row in rows] == [owner.id]
    # 那一单终态失败后,自动请求才为这份转写建新的一单。
    with Session(engine) as session:
        row = session.get(PodcastProcessingRecord, owner.id)
        row.processing_status = "failed"
        row.finished_at = NOW.isoformat()
        session.add(row)
        session.commit()
    fresh = enqueue(idempotency_key_prefix="full-analysis:auto:episode-asr")
    assert fresh.id != owner.id and fresh.input_artifact_id == "complete-asr"


def test_a_rejected_retry_does_not_reset_the_lease_expiry_streak(engine):
    from models.db import PodcastProcessingCommandRecord

    process = _request(engine, "episode-50", override=False, key="rejected-retry")
    config = _config()
    policy = PodcastStagePolicy(config)
    clock = (
        dt.datetime.strptime(process.budget_period, "%Y-%m")
        .replace(tzinfo=ZoneInfo(config.budget_timezone))
        .astimezone(dt.timezone.utc)
    )

    def claim_at(moment):
        with Session(engine) as session:
            claim = claim_next_processing(
                session, worker_id="w", lease_seconds=1, policy=policy,
                requested_target="full_analysis", now=moment,
            )
            if claim is not None:
                _begin_local_attempt(session, claim, now=moment, tag=moment.isoformat())
            return claim

    assert claim_at(clock) is not None
    clock += dt.timedelta(seconds=2)
    assert claim_at(clock) is not None  # 第 1 次失租:立即重领
    # 一条被拒绝的重试命令(例如 attempt 数对不上)不算「再给一轮机会」。
    with Session(engine) as session:
        session.add(PodcastProcessingCommandRecord(
            id="rejected-command", processing_id=process.id, command_type="manual_retry",
            idempotency_key="rejected-retry-key", expected_attempt_count=2,
            requested_by="admin", reason="stale retry", outcome="rejected",
            error_code="podcast_processing_conflict", created_at=clock.isoformat(),
        ))
        session.commit()
    clock += dt.timedelta(seconds=2)
    assert claim_at(clock) is None  # 第 2 次失租:照常退避,而不是又当成第 1 次
    with Session(engine) as session:
        assert session.get(PodcastProcessingRecord, process.id).processing_status == "retry_wait"
