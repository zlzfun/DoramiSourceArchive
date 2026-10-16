"""Issue #194: metadata scans stay small and analysis SQL stays off the loop."""

import asyncio
import datetime as dt
import hashlib
import json
import re
import threading

import pytest
from sqlalchemy import event
from sqlmodel import Session

from config import LLMConfig
from models.db import (
    ArticleAnalysisRecord, ArticleRecord, PodcastTextArtifactRecord,
    PodcastTextPublicationRecord, SourceConfigRecord,
)
from services import article_analysis as analysis_service
from services.podcast_premium import dashboard
from services.podcast_premium_guides import pending_premium_guide_candidates
from storage.impl.db_storage import DatabaseStorage


NOW = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)
STAMP = NOW.isoformat()


@pytest.mark.parametrize("kind", ["normalized_transcript", "publisher_transcript"])
@pytest.mark.parametrize("case,expected", [
    ("new", True), ("ready", False), ("mode_changed", True),
    ("language_changed", True), ("retry_due", True), ("retry_future", False),
    ("queued", False), ("suppressed", False), ("short", False),
    ("authority_changed", False), ("withdrawn", False),
    ("no_publication", True), ("locator_changed", True),
])
def test_candidate_scan_reads_only_metadata(tmp_path, kind, case, expected):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'candidates.db'}")
    locator = "https://example.test/transcript.txt"
    guide = {"status": "ready", "mode": "solo_deep", "language": "en"}
    if case in {"new", "no_publication", "locator_changed"}:
        guide = {}
    elif case == "mode_changed":
        guide["mode"] = "brief_zh"
    elif case == "language_changed":
        guide["language"] = "zh"
    elif case.startswith("retry_"):
        guide = {"status": "retry_wait", "next_retry_at": (
            "2000-01-01T00:00:00+00:00" if case == "retry_due"
            else "2099-01-01T00:00:00+00:00"
        )}
    elif case == "queued":
        guide = {"status": "queued"}
    extensions = {
        "duration_seconds": 10 if case == "short" else 2400,
        "transcripts": [{"url": locator, "type": "text/plain", "language": "en"}],
        "premium_guide": guide,
        "premium_guide_auto_suppressed": case == "suppressed",
    }
    if case == "locator_changed":
        extensions["transcripts"][0]["url"] = "https://example.test/replacement.txt"
        expected = kind == "normalized_transcript"
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="show", name="Show", source_type="podcast",
            url="https://example.test/feed", params_json='{"language":"en"}',
            created_at=STAMP, updated_at=STAMP,
        ))
        session.add(ArticleRecord(
            id="episode", title="Episode", source_id="show",
            source_url="https://example.test/episode", content_type="podcast_episode",
            content="show notes" * 1024, extensions_json=json.dumps(extensions),
            fetched_date=STAMP, publish_date=STAMP,
        ))
        session.commit()
        session.add(PodcastTextArtifactRecord(
            id="transcript", episode_id="episode", kind=kind, version=1,
            content_hash="a" * 64, inline_text="逐字稿正文" * 8192, language="en",
            authority_id="local", created_at=STAMP,
            provenance_json=json.dumps({"url_sha256": hashlib.sha256(locator.encode()).hexdigest()}),
        ))
        session.add(PodcastTextArtifactRecord(
            id="blog", episode_id="episode", kind="digest_blog_zh", version=1,
            content_hash="b" * 64, inline_text="导读正文" * 8192, language="zh-CN",
            source_artifact_id="transcript", source_content_hash="a" * 64,
            provenance_json='{"guide_mode":"solo_deep"}', created_at=STAMP,
        ))
        if case == "authority_changed":
            session.add(PodcastTextArtifactRecord(
                id="other-transcript", episode_id="episode", kind=kind, version=2,
                content_hash="c" * 64, inline_text="replacement", language="en",
                authority_id="other", created_at=STAMP,
            ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id="episode", status="succeeded", quality_score=9,
            podcast_initial_score=6.5, podcast_final_score=9,
            analysis_basis="publisher_transcript" if kind == "publisher_transcript" else "asr_transcript",
            transcript_artifact_id="transcript", summary="摘要" * 8192,
            created_at=STAMP, updated_at=STAMP,
        ))
        transcript_publication = PodcastTextPublicationRecord(
            identity=f"episode:{kind}", episode_id="episode", kind=kind,
            artifact_id="other-transcript" if case == "authority_changed" else "transcript",
            authority_id="other" if case == "authority_changed" else "local",
            status="unpublished" if case == "withdrawn" else "published",
            published_at=STAMP, unpublished_at=STAMP if case == "withdrawn" else None,
            updated_at=STAMP,
        )
        if case != "no_publication":
            session.add(transcript_publication)
        session.add(PodcastTextPublicationRecord(
            identity="episode:digest_blog_zh", episode_id="episode", kind="digest_blog_zh",
            artifact_id="blog", status="published", published_at=STAMP, updated_at=STAMP,
        ))
        session.commit()

    statements = []

    def check_projection(_conn, _cursor, statement, _params, _context, _many):
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        projection = re.split(r"\bFROM\b", statement, maxsplit=1, flags=re.I)[0]
        assert not re.search(r"\b(?:inline_text|articles\.content|article_analyses\.summary)\b", projection)
        statements.append(statement)

    event.listen(sink.engine, "before_cursor_execute", check_projection)
    try:
        result = pending_premium_guide_candidates(
            sink.engine, minimum_duration_seconds=1800, score_threshold=7.5,
        )
        assert result == (["episode"] if expected else [])
        row = dashboard(sink.engine)["items"][0]
        assert row["final_score"] == 9
        assert row["blog_ready"] is True
        assert statements
    finally:
        event.remove(sink.engine, "before_cursor_execute", check_projection)
        sink.engine.dispose()


@pytest.mark.parametrize("phase", [
    "scan_analysis_backfill", "claim_analysis_tasks", "_input_for", "_persist_tags", "_mark_failure",
])
def test_slow_analysis_database_keeps_loop_responsive(tmp_path, monkeypatch, phase):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'analysis.db'}")
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="article", title="News", content_type="article", source_id="public",
            source_url="https://example.test/news", content="Useful news", has_content=True,
            fetched_date=STAMP, publish_date=STAMP,
        ))
        session.commit()

    async def scenario():
        owner = threading.get_ident()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        original = getattr(analysis_service, phase)
        blocked = False
        sql_threads = []
        opened_sessions = []
        closed_sessions = []

        class WorkerSession(Session):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.creator_thread = threading.get_ident()
                assert self.creator_thread != owner
                opened_sessions.append(id(self))

            def close(self):
                assert threading.get_ident() == self.creator_thread
                closed_sessions.append(id(self))
                super().close()

        def slow_database(*args, **kwargs):
            nonlocal blocked
            assert threading.get_ident() != owner
            if not blocked:
                blocked = True
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(3), "event loop failed to release the slow DB phase"
            return original(*args, **kwargs)

        def check_sql_thread(*_args):
            thread = threading.get_ident()
            assert thread != owner, "analysis SQL ran on the event-loop thread"
            sql_threads.append(thread)

        async def analyzer(*_args):
            assert threading.get_ident() == owner
            assert asyncio.get_running_loop() is loop
            if phase == "_mark_failure":
                raise ValueError("model request failed")
            return {"quality_score": 7, "score_reason": "实质进展", "summary": "报道新进展。",
                    "content_kind": "news", "content_value": 6,
                    "content_value_reason": "具体进展", "content_genre": "product_update",
                    "tag_assignments": [], "tag_candidates": []}

        async def image_notes(*_args):
            assert asyncio.get_running_loop() is loop
            return ""

        monkeypatch.setattr(analysis_service, phase, slow_database)
        monkeypatch.setattr(analysis_service, "Session", WorkerSession)
        event.listen(sink.engine, "before_cursor_execute", check_sql_thread)
        task = asyncio.create_task(analysis_service.run_analysis_cycle(
            sink.engine, worker_id="test", llm_config=LLMConfig(model="fake"),
            analyzer=analyzer, enabled=True, candidate_enabled=False,
            max_batches=1, now_fn=lambda: NOW, image_notes_provider=image_notes,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert not task.done()
            # This callback is analogous to servicing a health-check request.
            heartbeat = loop.create_future()
            loop.call_soon(heartbeat.set_result, "responsive")
            assert await asyncio.wait_for(heartbeat, .5) == "responsive"
        finally:
            release.set()
            results = await asyncio.wait_for(task, 10)
            event.remove(sink.engine, "before_cursor_execute", check_sql_thread)
        assert sql_threads
        assert sorted(opened_sessions) == sorted(closed_sessions)
        expected_status = "failed" if phase == "_mark_failure" else "succeeded"
        assert [(row.article_id, row.status) for row in results] == [("article", expected_status)]

    try:
        asyncio.run(scenario())
    finally:
        sink.engine.dispose()


def test_cancelled_input_worker_closes_session_and_lease_can_recover(tmp_path, monkeypatch):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'cancel.db'}")
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="cancel", title="News", content_type="article", source_id="public",
            source_url="https://example.test/news", content="Useful news", has_content=True,
            fetched_date=STAMP, publish_date=STAMP,
        ))
        session.commit()
        analysis_service.queue_article_analysis(session, "cancel", enabled=True, now=NOW)
        session.commit()
        claimed = analysis_service.claim_analysis_tasks(session, worker_id="cancel-test", now=NOW)[0]

    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        closed = threading.Event()
        original = analysis_service._input_for

        class ClosingSession(Session):
            def close(self):
                super().close()
                closed.set()

        def slow_input(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3)
            return original(*args)

        async def forbidden_model(*_args):
            pytest.fail("cancelled preparation must not start a model request")

        monkeypatch.setattr(analysis_service, "Session", ClosingSession)
        monkeypatch.setattr(analysis_service, "_input_for", slow_input)
        task = asyncio.create_task(analysis_service.process_claimed_analysis(
            sink.engine, claimed, llm_config=LLMConfig(model="fake"),
            analyzer=forbidden_model, now_fn=lambda: NOW,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            assert await asyncio.to_thread(closed.wait, 3)

    try:
        asyncio.run(scenario())
        with Session(sink.engine) as session:
            assert analysis_service.recover_expired_leases(session, now=NOW + dt.timedelta(minutes=6)) == 1
            record = session.get(ArticleAnalysisRecord, "cancel")
            assert record.status == "timeout"
            assert record.lease_owner is None
            assert record.next_attempt_at
    finally:
        sink.engine.dispose()
