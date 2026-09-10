"""Issue #45: independent Podcast processing and premium thresholds."""

from __future__ import annotations

import hashlib
import os
import sys

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.articles_view import serialize_article_list_item  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastProcessingRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.podcast_premium import (  # noqa: E402
    DEFAULT_PREMIUM_SCORE_THRESHOLD,
    INITIAL_PROCESSING_THRESHOLD,
    dashboard,
    get_threshold,
    normalize_threshold,
    set_threshold,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


STAMP = "2026-09-10T00:00:00+00:00"


def _episode(episode_id: str) -> ArticleRecord:
    return ArticleRecord(
        id=episode_id,
        title=f"Episode {episode_id}",
        content_type="podcast_episode",
        source_id="podcast-premium-test",
        source_url=f"https://example.test/{episode_id}",
        publish_date=STAMP,
        fetched_date=STAMP,
        content="show notes",
    )


def _analysis(
    episode_id: str,
    *,
    initial: float | None,
    final: float | None = None,
) -> ArticleAnalysisRecord:
    return ArticleAnalysisRecord(
        article_id=episode_id,
        status="succeeded",
        quality_score=final if final is not None else initial,
        podcast_initial_score=initial,
        podcast_final_score=final,
        analysis_basis=("publisher_transcript" if final is not None else "podcast_show_notes"),
        created_at=STAMP,
        updated_at=STAMP,
    )


def _failed_processing(episode_id: str) -> PodcastProcessingRecord:
    return PodcastProcessingRecord(
        id=f"processing-{episode_id}",
        episode_id=episode_id,
        input_fingerprint="a" * 64,
        pipeline_version="test-v1",
        policy_version="test-v1",
        requested_target="full_analysis",
        selection_source="policy",
        requested_by="system",
        request_reason="简介初评达到全文处理线",
        idempotency_key=f"failed-{episode_id}",
        input_artifact_id=f"audio-{episode_id}",
        input_artifact_kind="source_media_snapshot",
        input_content_hash="b" * 64,
        input_language="und",
        budget_scope="podcast-test",
        budget_period="2026-09",
        budget_limit_minor=1000,
        per_run_budget_minor=100,
        eligibility_status="eligible",
        processing_status="failed",
        stage="asr",
        attempt_count=1,
        error_code="provider_failed",
        error_message="转录供应方失败",
        queued_at=STAMP,
        updated_at=STAMP,
        finished_at=STAMP,
        created_at=STAMP,
    )


def _published_blog(episode_id: str) -> tuple[PodcastTextArtifactRecord, PodcastTextPublicationRecord]:
    body = "historical guide"
    artifact = PodcastTextArtifactRecord(
        id=f"blog-{episode_id}",
        episode_id=episode_id,
        kind="digest_blog_zh",
        version=1,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        inline_text=body,
        language="zh-CN",
        provenance_json='{"pipeline_version":"legacy"}',
        created_at=STAMP,
    )
    publication = PodcastTextPublicationRecord(
        identity=f"{episode_id}:digest_blog_zh",
        episode_id=episode_id,
        kind="digest_blog_zh",
        artifact_id=artifact.id,
        status="published",
        authority_id="",
        published_at=STAMP,
        updated_at=STAMP,
    )
    return artifact, publication


@pytest.fixture()
def premium_engine(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'podcast-premium.db'}")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast-premium-test",
            name="Premium Test Show",
            source_type="podcast",
            url="https://example.test/feed.xml",
            fetcher_id="generic_podcast_rss",
            created_at=STAMP,
            updated_at=STAMP,
        ))
        ids = ("low-initial", "exact-initial", "low-final", "exact-final", "historical", "failed", "high-initial")
        for episode_id in ids:
            session.add(_episode(episode_id))
        session.commit()
        session.add(_analysis("low-initial", initial=4.9))
        session.add(_analysis("exact-initial", initial=5.0))
        session.add(_analysis("low-final", initial=5.0, final=7.9))
        session.add(_analysis("exact-final", initial=5.0, final=8.0))
        session.add(_analysis("historical", initial=5.0, final=8.1))
        session.add(_analysis("failed", initial=5.0))
        session.add(_analysis("high-initial", initial=9.9))
        session.add(_failed_processing("failed"))
        blog, publication = _published_blog("historical")
        session.add(blog)
        session.commit()
        session.add(publication)
        session.commit()
    yield sink.engine
    sink.engine.dispose()


def test_threshold_validation_and_persistence(premium_engine):
    assert INITIAL_PROCESSING_THRESHOLD == 5.0
    assert DEFAULT_PREMIUM_SCORE_THRESHOLD == 8.0
    with Session(premium_engine) as session:
        assert get_threshold(session) == 8.0
        assert set_threshold(session, 10.0) == 10.0
    with Session(premium_engine) as session:
        assert get_threshold(session) == 10.0
        assert set_threshold(session, 7.5) == 7.5
    with pytest.raises(ValueError, match="一位小数"):
        normalize_threshold(8.55)
    with pytest.raises(ValueError, match="1.0–10.0"):
        normalize_threshold(10.1)


def test_dashboard_uses_final_score_only_and_recalculates_without_changing_candidates(premium_engine):
    initial = dashboard(premium_engine)
    assert initial["stats"] == {
        "total": 7,
        "full_analyzed": 3,
        "premium": 2,
        "pending_or_failed": 3,
    }
    assert {item["episode_id"] for item in dashboard(premium_engine, status_filter="pending_full")["items"]} == {
        "exact-initial",
        "high-initial",
    }
    assert [item["episode_id"] for item in dashboard(premium_engine, status_filter="failed")["items"]] == ["failed"]

    with Session(premium_engine) as session:
        set_threshold(session, 7.5)
    lowered = dashboard(premium_engine, status_filter="premium")
    assert lowered["stats"]["premium"] == 3
    low_final = next(item for item in lowered["items"] if item["episode_id"] == "low-final")
    assert low_final["pending_generation"] is True
    # Changing the final threshold must not pull the 4.9 show-notes item into
    # the fixed >=5.0 full-processing candidate set.
    assert "low-initial" not in {
        item["episode_id"]
        for item in dashboard(premium_engine, status_filter="pending_full")["items"]
    }

    with Session(premium_engine) as session:
        set_threshold(session, 8.5)
    raised = dashboard(premium_engine, status_filter="below_threshold")
    historical = next(item for item in raised["items"] if item["episode_id"] == "historical")
    assert historical["historical_generated"] is True
    assert historical["reason"] == "历史已生成，当前未达门槛"


def test_reader_badge_requires_transcript_score_and_uses_inclusive_current_threshold():
    episode = _episode("projection")
    show_notes = _analysis("projection", initial=9.9)
    full = _analysis("projection", initial=5.0, final=8.0)
    assert serialize_article_list_item(
        episode, analysis=show_notes, premium_score_threshold=8.0
    )["is_premium_podcast"] is False
    assert serialize_article_list_item(
        episode, analysis=full, premium_score_threshold=8.0
    )["is_premium_podcast"] is True
    assert serialize_article_list_item(
        episode, analysis=full, premium_score_threshold=8.1
    )["is_premium_podcast"] is False

    # A stale final-score column must never award premium status after the
    # authoritative basis has returned to show notes.
    show_notes.podcast_final_score = 9.9
    assert serialize_article_list_item(
        episode, analysis=show_notes, premium_score_threshold=8.0
    )["is_premium_podcast"] is False
