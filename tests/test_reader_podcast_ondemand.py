"""读者点播精品导读：评估函数 + HTTP 端点契约。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import PodcastConfig  # noqa: E402
from models.db import (  # noqa: E402
    AiUsageRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.podcast_premium_guides import (  # noqa: E402
    PremiumGuideForceError,
    READER_ONDEMAND_FINAL_PENDING_MESSAGE,
    READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE,
    evaluate_reader_ondemand_premium_guide,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


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


def _premium_config(**updates) -> PodcastConfig:
    values = {
        "installation": "external",
        "authority_id": "podcast-ondemand-test",
        "allowed_stages": PREMIUM_STAGES,
        "premium_score_threshold": 8.0,
    }
    values.update(updates)
    return PodcastConfig(**values)


def _make_sink(tmp_path, name: str) -> DatabaseStorage:
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed_users(engine):
    from services import accounts as accounts_service
    from models.db import UserRecord

    with Session(engine) as session:
        for username, password, role in (
            ("admin", "admin", "admin"),
            ("user", "user", "user"),
        ):
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(password),
                    role=role,
                    is_active=True,
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
        session.commit()


def _configure_llm(engine):
    from services import daily_brief as db

    with Session(engine) as session:
        db.set_setting(session, db.KEY_LLM_BASE_URL, "https://llm.test/v1")
        db.set_setting(session, db.KEY_LLM_API_KEY, "sk-test")
        db.set_setting(session, db.KEY_LLM_MODEL, "test-model")


def _enable_ai_beta(engine, username="user"):
    from services import accounts as accounts_service

    with Session(engine) as session:
        accounts_service.set_ai_beta_enabled(session, username, True)


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def _base_setup(monkeypatch, tmp_path, name: str):
    import api.app as app_module

    sink = _make_sink(tmp_path, name)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, podcast=_premium_config()),
    )
    _seed_users(sink.engine)
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    return app_module, sink


def _seed_episode(
    engine,
    *,
    episode_id: str = "episode-ondemand",
    source_id: str = "podcast-ondemand",
    score: float | None = 8.2,
    guide_status: str | None = None,
    published_audio: bool = False,
    with_transcript: bool = True,
    processing_status: str | None = None,
    duration_seconds: int = 19 * 60,
    credentialed: bool = False,
):
    transcript = json.dumps({"text": "complete ondemand transcript"}, ensure_ascii=False)
    transcript_id = f"transcript-{episode_id}"
    extensions: dict = {"duration_seconds": duration_seconds}
    if guide_status:
        extensions["premium_guide"] = {
            "status": guide_status,
            "updated_at": STAMP,
        }

    with Session(engine) as session:
        session.add(
            SourceConfigRecord(
                source_id=source_id,
                name="Ondemand Podcast",
                source_type="podcast",
                url=(
                    "https://feeds.example.test/podcast?subscriber=Abc123Def456Ghi789Jkl012"
                    if credentialed
                    else "https://example.test/ondemand.xml"
                ),
                params_json='{"credentialed_private": true}' if credentialed else "{}",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            ArticleRecord(
                id=episode_id,
                title="Ondemand episode",
                content_type="podcast_episode",
                source_id=source_id,
                source_url=f"https://example.test/{episode_id}",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
                extensions_json=json.dumps(extensions, ensure_ascii=False),
            )
        )
        session.commit()

        if score is not None:
            session.add(
                ArticleAnalysisRecord(
                    article_id=episode_id,
                    status="succeeded",
                    quality_score=score,
                    podcast_final_score=score,
                    analysis_basis="asr_transcript",
                    transcript_artifact_id=transcript_id if with_transcript else "",
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )

        if with_transcript:
            session.add(
                PodcastTextArtifactRecord(
                    id=transcript_id,
                    episode_id=episode_id,
                    kind="normalized_transcript",
                    version=1,
                    content_hash=hashlib.sha256(transcript.encode()).hexdigest(),
                    inline_text=transcript,
                    language="en",
                    authority_id="test-authority",
                    provenance_json='{"provider":"fake"}',
                    created_at=STAMP,
                )
            )

        if published_audio:
            script_body = "导读旁白"
            script_id = f"script-{episode_id}"
            script_hash = hashlib.sha256(script_body.encode()).hexdigest()
            session.add(
                PodcastTextArtifactRecord(
                    id=script_id,
                    episode_id=episode_id,
                    kind="narration_script_zh",
                    version=1,
                    content_hash=script_hash,
                    inline_text=script_body,
                    language="zh",
                    authority_id="test-authority",
                    provenance_json='{"provider":"fake"}',
                    created_at=STAMP,
                )
            )
            session.add(
                PodcastTextPublicationRecord(
                    identity=f"{episode_id}:narration_script_zh",
                    episode_id=episode_id,
                    kind="narration_script_zh",
                    artifact_id=script_id,
                    status="published",
                    authority_id="test-authority",
                    published_at=STAMP,
                    updated_at=STAMP,
                )
            )
            session.commit()
            session.add(
                PodcastArtifactRecord(
                    id=f"audio-{episode_id}",
                    episode_id=episode_id,
                    kind="digest_audio_zh",
                    content_hash="c" * 64,
                    mime="audio/mpeg",
                    ext=".mp3",
                    size_bytes=12,
                    status="published",
                    provenance="test",
                    authority_id="test-authority",
                    narration_artifact_id=script_id,
                    narration_content_hash=script_hash,
                    created_at=STAMP,
                    updated_at=STAMP,
                    published_at=STAMP,
                )
            )

        if processing_status:
            session.add(
                PodcastProcessingRecord(
                    id=f"processing-{episode_id}",
                    episode_id=episode_id,
                    input_fingerprint="a" * 64,
                    pipeline_version="test-v1",
                    policy_version="test-v1",
                    requested_target="full_analysis",
                    selection_source="policy",
                    requested_by="system",
                    request_reason="简介初评达到全文处理线",
                    idempotency_key=f"full-{episode_id}",
                    input_artifact_id=f"audio-src-{episode_id}",
                    input_artifact_kind="source_media_snapshot",
                    input_content_hash="b" * 64,
                    input_language="und",
                    budget_scope="podcast-test",
                    budget_period="2026-09",
                    budget_limit_minor=1000,
                    per_run_budget_minor=100,
                    eligibility_status="eligible",
                    processing_status=processing_status,
                    stage="analyze",
                    queued_at=STAMP,
                    updated_at=STAMP,
                    created_at=STAMP,
                )
            )
        session.commit()


# ── evaluate_reader_ondemand_premium_guide ──────────────────────


def test_evaluate_score_too_low_when_final_missing(tmp_path):
    sink = _make_sink(tmp_path, "eval-low.db")
    _seed_episode(sink.engine, score=None, with_transcript=False)

    with pytest.raises(PremiumGuideForceError) as caught:
        evaluate_reader_ondemand_premium_guide(
            sink.engine,
            episode_id="episode-ondemand",
            config=_premium_config(),
            actor="user",
        )
    assert caught.value.code == "podcast_ondemand_score_too_low"
    assert caught.value.message == READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE
    assert caught.value.status_code == 409


def test_evaluate_final_pending_when_full_analysis_active(tmp_path):
    sink = _make_sink(tmp_path, "eval-pending.db")
    _seed_episode(
        sink.engine,
        score=None,
        with_transcript=False,
        processing_status="queued",
    )

    with pytest.raises(PremiumGuideForceError) as caught:
        evaluate_reader_ondemand_premium_guide(
            sink.engine,
            episode_id="episode-ondemand",
            config=_premium_config(),
            actor="user",
        )
    assert caught.value.code == "podcast_ondemand_final_pending"
    assert caught.value.message == READER_ONDEMAND_FINAL_PENDING_MESSAGE


def test_evaluate_ready_when_digest_audio_published(tmp_path):
    sink = _make_sink(tmp_path, "eval-ready.db")
    _seed_episode(sink.engine, published_audio=True)

    result = evaluate_reader_ondemand_premium_guide(
        sink.engine,
        episode_id="episode-ondemand",
        config=_premium_config(),
        actor="user",
    )
    assert result["outcome"] == "ready"
    assert result["should_schedule"] is False
    assert result["charged"] is False


def test_evaluate_in_progress_when_guide_active(tmp_path):
    sink = _make_sink(tmp_path, "eval-progress.db")
    _seed_episode(sink.engine, guide_status="synthesizing")

    result = evaluate_reader_ondemand_premium_guide(
        sink.engine,
        episode_id="episode-ondemand",
        config=_premium_config(),
        actor="user",
    )
    assert result["outcome"] == "in_progress"
    assert result["status"] == "synthesizing"
    assert result["should_schedule"] is False


def test_evaluate_can_queue_when_eligible(tmp_path):
    sink = _make_sink(tmp_path, "eval-queue.db")
    _seed_episode(sink.engine)

    result = evaluate_reader_ondemand_premium_guide(
        sink.engine,
        episode_id="episode-ondemand",
        config=_premium_config(),
        actor="user",
    )
    assert result["outcome"] == "can_queue"
    assert result["should_schedule"] is True


def test_evaluate_disabled_when_tts_stage_missing(tmp_path):
    sink = _make_sink(tmp_path, "eval-disabled.db")
    _seed_episode(sink.engine)
    config = _premium_config(
        allowed_stages=("fetch", "asr", "translate", "analyze", "digest", "local_publish")
    )

    with pytest.raises(PremiumGuideForceError) as caught:
        evaluate_reader_ondemand_premium_guide(
            sink.engine,
            episode_id="episode-ondemand",
            config=config,
            actor="user",
        )
    assert caught.value.code == "podcast_ondemand_disabled"
    assert caught.value.status_code == 503
    assert caught.value.message == "当前部署未开启播客点播"


# ── HTTP /api/reader/ai/podcasts/{id}/ondemand ─────────────────


def test_ondemand_api_ready_reuses_without_charge(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-ready.db")
    _seed_episode(sink.engine, published_audio=True)
    calls = []

    def schedule(*_a, **_k):
        calls.append(True)
        raise AssertionError("ready path must not schedule")

    monkeypatch.setattr(app_module, "schedule_forced_podcast_premium_guide", schedule)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "ready"
    assert body["charged"] is False
    assert body["started"] is False
    assert calls == []

    with Session(sink.engine) as session:
        used = session.exec(
            select(AiUsageRecord).where(AiUsageRecord.purpose == "podcast_ondemand")
        ).all()
    assert used == []


def test_ondemand_api_in_progress_reuses_without_charge(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-progress.db")
    _seed_episode(sink.engine, guide_status="queued")
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_forced_podcast_premium_guide",
        lambda *a, **k: calls.append(True),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "in_progress"
    assert resp.json()["charged"] is False
    assert calls == []


def test_ondemand_api_queues_and_charges_once(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-queue.db")
    _seed_episode(sink.engine)
    calls = []

    def schedule(episode_id, **kwargs):
        calls.append((episode_id, kwargs))
        return {
            "episode_id": episode_id,
            "status": "queued",
            "forced": True,
            "replayed": False,
            "should_schedule": True,
            "started": True,
        }

    monkeypatch.setattr(app_module, "schedule_forced_podcast_premium_guide", schedule)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "success",
        "episode_id": "episode-ondemand",
        "outcome": "queued",
        "guide_status": "queued",
        "charged": True,
        "started": True,
    }
    assert len(calls) == 1
    assert calls[0][0] == "episode-ondemand"
    assert calls[0][1]["reason"] == "读者点播精品导读音频"
    assert calls[0][1]["actor"] == "user"
    assert calls[0][1]["idempotency_key"].startswith(
        "reader-ondemand:user:episode-ondemand:"
    )

    with Session(sink.engine) as session:
        rows = session.exec(
            select(AiUsageRecord).where(
                AiUsageRecord.username == "user",
                AiUsageRecord.purpose == "podcast_ondemand",
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].calls == 1


def test_ondemand_api_quota_blocks_before_schedule(monkeypatch, tmp_path):
    from api.routers import reader as reader_router

    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-quota.db")
    _seed_episode(sink.engine)
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_forced_podcast_premium_guide",
        lambda *a, **k: calls.append(True) or {"status": "queued", "started": True},
    )

    limit = reader_router._AI_DAILY_CALL_LIMITS["podcast_ondemand"]
    today = dt.date.today().isoformat()
    with Session(sink.engine) as session:
        session.add(
            AiUsageRecord(
                day=today,
                username="user",
                purpose="podcast_ondemand",
                model="ondemand",
                calls=limit,
                total_tokens=0,
                updated_at=today,
            )
        )
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 429
    assert calls == []

    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-ondemand")
        extensions = json.loads(episode.extensions_json or "{}")
    assert "premium_guide" not in extensions


def test_ondemand_api_score_too_low_returns_friendly_message(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-low.db")
    _seed_episode(sink.engine, score=None, with_transcript=False)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 409
    assert resp.json() == {
        "code": "podcast_ondemand_score_too_low",
        "message": READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE,
    }


def test_ondemand_api_final_pending_message(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-pending.db")
    _seed_episode(
        sink.engine,
        score=None,
        with_transcript=False,
        processing_status="queued",
    )

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 409
    assert resp.json()["code"] == "podcast_ondemand_final_pending"
    assert resp.json()["message"] == READER_ONDEMAND_FINAL_PENDING_MESSAGE


def test_ondemand_api_rejects_credentialed_source(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-cred.db")
    _seed_episode(sink.engine, credentialed=True, source_id="rss_credentialed")
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_forced_podcast_premium_guide",
        lambda *a, **k: calls.append(True),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/episode-ondemand/ondemand")
    assert resp.status_code == 403
    assert "访问凭证" in resp.json()["detail"]
    assert calls == []


def test_ondemand_api_404_for_non_podcast(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-notpod.db")
    with Session(sink.engine) as session:
        session.add(
            ArticleRecord(
                id="article-1",
                title="Not a podcast",
                content_type="rss_article",
                source_id="rss_x",
                source_url="https://example.test/a1",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="body",
            )
        )
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/podcasts/article-1/ondemand")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "播客单集不存在"
