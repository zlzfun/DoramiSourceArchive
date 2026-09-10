from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig, PodcastConfig  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.article_analysis import queue_article_analysis  # noqa: E402
from services.podcast_full_analysis import (  # noqa: E402
    FINAL_PREMIUM_THRESHOLD,
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
from api.articles_view import _podcast_projection  # noqa: E402


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
        for episode_id, score in (("episode-49", 4.9), ("episode-50", 5.0)):
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
                extensions_json=json.dumps({"duration_seconds": 60}),
            )
        )
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id="episode-asr",
                status="succeeded",
                quality_score=5.0,
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
            PodcastArtifactRecord(
                id="source-audio-asr",
                episode_id="episode-asr",
                kind="source_audio",
                content_hash="e" * 64,
                mime="audio/mpeg",
                ext="mp3",
                size_bytes=1024,
                duration_seconds=60,
                status="ready",
                expires_at="2099-01-01T00:00:00.000000+00:00",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.commit()
    return storage.engine


def _request(engine, episode_id: str, *, override: bool, key: str):
    return request_processing(
        engine,
        _Store(),
        _registry(),
        _config(),
        episode_id=episode_id,
        target="full_analysis",
        selection_override=override,
        idempotency_key=key,
        reason="测试全文处理",
        actor="system" if not override else "admin",
    )


def test_initial_assessment_boundary_and_editor_override(engine):
    with pytest.raises(PodcastAdminError) as rejected:
        _request(engine, "episode-49", override=False, key="auto-episode-49")
    assert rejected.value.code == "podcast_selection_required"

    exact = _request(engine, "episode-50", override=False, key="auto-episode-50")
    assert exact.selection_source == "policy"
    assert exact.stage == "analyze"
    assert exact.input_artifact_kind == "publisher_transcript"

    forced = _request(engine, "episode-49", override=True, key="force-episode-49")
    assert forced.selection_source == "editor"
    assert forced.stage == "analyze"


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
    assert process.input_artifact_kind == "source_audio"
    assert process.stage == "asr"
    assert process.processing_status == "queued"


def test_map_reduce_covers_every_character_and_exact_eight_is_premium(engine):
    process = _request(
        engine, "episode-50", override=False, key="worker-episode-50"
    )
    provider = _Provider(score=FINAL_PREMIUM_THRESHOLD)
    config = _config()
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
        assert analysis.quality_score == 8.0
        diagnostics = json.loads(analysis.analysis_diagnostics_json)
        assert diagnostics["coverage"]["source_chars"] == len("".join(provider.chunks))
        assert diagnostics["coverage"]["chunk_count"] == len(provider.chunks)
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
    logical_now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    process = _request(engine, "episode-50", override=False, key="settled-crash")
    config = _config()
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
        app_module.podcast_publisher_transcript_service,
        "ingest_publisher_transcript",
        fake_publisher,
    )
    monkeypatch.setattr(
        app_module.podcast_source_audio_service,
        "cache_source_audio",
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
        quality_score=5.0,
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
    projected = _podcast_projection({}, initial, failed)
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
        quality_score=8.0,
    )
    completed = SimpleNamespace(
        id="processing-2",
        requested_target="full_analysis",
        processing_status="ready",
        stage="analyze",
        input_artifact_kind="source_audio",
        error_message="",
    )
    projected = _podcast_projection({}, final, completed)
    assert projected["transcript_source"] == "asr_transcript"
    assert projected["full_analysis_candidate"] is False
    assert projected["final_premium"] is True


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


def test_stale_publisher_locator_falls_back_to_source_audio_asr(engine):
    old_url = "https://publisher.example.test/old.vtt"
    new_url = "https://publisher.example.test/new.vtt"
    with Session(engine) as session:
        episode = session.get(ArticleRecord, "episode-asr")
        episode.extensions_json = json.dumps(
            {"transcripts": [{"url": new_url, "type": "text/vtt"}]}
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
    assert process.input_artifact_kind == "source_audio"
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
    monkeypatch.setattr(app_module.podcast_source_audio_service, "cache_source_audio", forbidden)
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
        episode.extensions_json = json.dumps({"transcripts": [{"url": "https://example.test/new.vtt", "type": "text/vtt"}]})
        session.add(episode)
        text = "stale publisher"
        session.add(PodcastTextArtifactRecord(
            id="old-publisher", episode_id="episode-asr", kind="publisher_transcript",
            version=1, content_hash=hashlib.sha256(text.encode()).hexdigest(),
            inline_text=text, language="en", authority_id="",
            provenance_json=json.dumps({"format": "text", "url_sha256": hashlib.sha256(
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


@pytest.mark.parametrize("score,expected", [(8.5, []), (8.6, ["episode-50"]), (None, [])])
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
        app_module.settings, podcast=replace(_config(), premium_score_threshold=8.5),
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
