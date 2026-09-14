"""Podcast 全文处理自动入队:增量游标、失败记忆/退避、守门(issue #68)。

生产 2026-09-14 实录:每分钟把 233 集全量重放入队,全部失败再全量重来,同步 DB
与下载校验跑在事件循环里,拖垮日报/采集 cron。本册钉住三层防线的契约。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.app as app_module  # noqa: E402
from models.db import AppSettingRecord, ArticleAnalysisRecord, ArticleRecord  # noqa: E402
from services import podcast_landing as landing  # noqa: E402
from services.podcast_artifacts import (  # noqa: E402
    PodcastArtifactStore,
    PodcastArtifactStorageFull,
)
from services.podcast_processing_admin import PodcastAdminError  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

NOW = dt.datetime(2026, 9, 14, 8, 30, tzinfo=dt.timezone.utc)


# ── 纯逻辑:分类与退避 ────────────────────────────────────────────────────────


def test_classify_failure_separates_deterministic_from_transient():
    assert landing.classify_failure(
        PodcastAdminError("podcast_provider_unavailable", status_code=503)
    ) == landing.FailureClass("deterministic", "podcast_provider_unavailable")
    assert landing.classify_failure(
        PodcastAdminError("podcast_processing_conflict", status_code=503)
    ).kind == "transient"
    assert landing.classify_failure(PodcastArtifactStorageFull("满")).kind == "deterministic"
    assert landing.classify_failure(httpx.ConnectError("boom")).kind == "transient"
    assert landing.classify_failure(RuntimeError("x")).kind == "transient"


def test_transient_backoff_doubles_from_five_minutes_and_caps():
    assert landing.transient_backoff_seconds(1) == 300
    assert landing.transient_backoff_seconds(2) == 600
    assert landing.transient_backoff_seconds(3) == 1200
    assert landing.transient_backoff_seconds(20) == landing.TRANSIENT_MAX_SECONDS


def test_record_failure_transient_ladder_then_exhausted():
    state: dict = {}
    transient = landing.FailureClass("transient", "ConnectError")
    for attempt in range(1, landing.TRANSIENT_MAX_ATTEMPTS):
        entry = landing.record_failure(state, "ep", "rev1", transient, NOW)
        assert entry["attempts"] == attempt
        assert entry["kind"] == "transient"
        retry_at = dt.datetime.fromisoformat(entry["retry_at"])
        assert retry_at == NOW + dt.timedelta(
            seconds=landing.transient_backoff_seconds(attempt)
        )
        # 未到 retry_at 不放行,到点放行
        assert not landing.is_allowed(state, "ep", "rev1", NOW)
        assert landing.is_allowed(state, "ep", "rev1", retry_at)
    entry = landing.record_failure(state, "ep", "rev1", transient, NOW)
    assert entry["kind"] == "exhausted"
    assert dt.datetime.fromisoformat(entry["retry_at"]) == NOW + dt.timedelta(
        seconds=landing.HALT_RECHECK_SECONDS
    )


def test_record_failure_deterministic_halts_until_recheck_or_revision_change():
    state: dict = {}
    entry = landing.record_failure(
        state, "ep", "prepare", landing.FailureClass("deterministic", "StorageFull"), NOW
    )
    assert entry["kind"] == "deterministic"
    assert entry["attempts"] == 1
    assert not landing.is_allowed(state, "ep", "prepare", NOW + dt.timedelta(hours=23))
    assert landing.is_allowed(state, "ep", "prepare", NOW + dt.timedelta(hours=24))
    # revision 变了(比如 publisher transcript 更新)立即放行,且计数重置
    assert landing.is_allowed(state, "ep", "rev2", NOW)
    entry = landing.record_failure(
        state, "ep", "rev2", landing.FailureClass("transient", "x"), NOW
    )
    assert entry["attempts"] == 1


def test_record_gated_and_success_and_prune():
    state: dict = {}
    landing.record_gated(state, "ep", "prepare", "source_media_gate", NOW)
    assert state["ep"]["kind"] == "gated"
    assert landing.due_retries(state, NOW) == []
    assert landing.due_retries(
        state, NOW + dt.timedelta(seconds=landing.GATE_RECHECK_SECONDS)
    ) == ["ep"]
    landing.record_success(state, "ep")
    assert state == {}
    landing.record_failure(state, "old", "r", landing.FailureClass("transient", "x"), NOW)
    landing.prune_state(state, NOW + dt.timedelta(seconds=landing.STATE_RETENTION_SECONDS + 1))
    assert state == {}


def test_state_roundtrip_tolerates_garbage(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-kv.db'}")
    with Session(sink.engine) as session:
        assert landing.load_state(session) == {}
        assert landing.load_cursor(session) == ""
        session.add(AppSettingRecord(key=landing.STATE_KEY, value="not json"))
        session.commit()
        assert landing.load_state(session) == {}
        landing.save_state(session, {"ep": {"kind": "gated"}})
        landing.save_cursor(session, "2026-09-14T00:00:00+00:00")
        session.commit()
    with Session(sink.engine) as session:
        assert landing.load_state(session) == {"ep": {"kind": "gated"}}
        assert landing.load_cursor(session) == "2026-09-14T00:00:00+00:00"


# ── 增量扫描 ──────────────────────────────────────────────────────────────────


def _analysis(article_id: str, *, score: float, updated_at: str, basis: str = "podcast_show_notes"):
    return ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        quality_score=score,
        score_reason="初评",
        summary="简介摘要",
        content_hash="a" * 64,
        analysis_basis=basis,
        analysis_input_hash="b" * 64,
        prompt_version="podcast-initial",
        scoring_version="podcast-score",
        analyzed_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
    )


def _episode(article_id: str, stamp: str):
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type="podcast_episode",
        source_id="podcast-ok",
        source_url=f"https://example.test/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        content="show notes",
        extensions_json=json.dumps(
            {"audio_url": f"https://cdn.example.test/{article_id}.mp3"}
        ),
    )


def test_scan_new_candidates_only_returns_rows_after_cursor(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-scan.db'}")
    with Session(sink.engine) as session:
        for article_id in ("a", "b", "c", "d"):
            session.add(_episode(article_id, "2026-09-10T00:00:00+00:00"))
        session.commit()
        session.add(_analysis("a", score=6.0, updated_at="2026-09-10T00:00:00+00:00"))
        session.add(_analysis("b", score=6.0, updated_at="2026-09-12T00:00:00+00:00"))
        session.add(_analysis("c", score=6.0, updated_at="2026-09-13T00:00:00+00:00"))
        # 非播客分析不进扫描
        session.add(_analysis("d", score=9.0, updated_at="2026-09-14T00:00:00+00:00", basis="article"))
        session.commit()
    with Session(sink.engine) as session:
        ids, cursor = landing.scan_new_candidates(session, "")
        assert ids == ["a", "b", "c"]
        assert cursor == "2026-09-13T00:00:00+00:00"
        ids, cursor2 = landing.scan_new_candidates(session, cursor)
        assert ids == [] and cursor2 == cursor
        ids, _ = landing.scan_new_candidates(session, "2026-09-11T00:00:00+00:00", limit=1)
        assert ids == ["b"]


# ── 调度轮次(app 层)────────────────────────────────────────────────────────


@pytest.fixture()
def landing_env(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-job.db'}")
    stamp = "2026-09-13T00:00:00+00:00"
    with Session(sink.engine) as session:
        for episode_id in ("ep-high", "ep-low", "ep-mid"):
            session.add(_episode(episode_id, stamp))
        session.commit()  # 先落 articles 行,analyses 有外键
        for episode_id, score in (("ep-high", 6.5), ("ep-low", 4.0), ("ep-mid", 5.0)):
            session.add(_analysis(episode_id, score=score, updated_at=stamp))
        session.commit()
    calls: list[str] = []
    outcomes: dict[str, BaseException | None] = {}

    async def fake_enqueue(*, episode_id, target, selection_override, idempotency_key, reason, actor):
        calls.append(episode_id)
        exc = outcomes.get(episode_id)
        if exc is not None:
            raise exc
        return {"episode_id": episode_id, "idempotency_key": idempotency_key}

    gate = {"runtime_code": None, "media_ready": True}
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "enqueue_podcast_processing_with_input", fake_enqueue)
    monkeypatch.setattr(
        app_module, "podcast_landing_gate", lambda: (gate["runtime_code"], gate["media_ready"])
    )
    monkeypatch.setattr(
        app_module.daily_brief_service,
        "resolve_llm_config",
        lambda _session: SimpleNamespace(configured=True),
    )
    return SimpleNamespace(sink=sink, calls=calls, outcomes=outcomes, gate=gate)


def _state(sink) -> dict:
    with Session(sink.engine) as session:
        return landing.load_state(session)


def _cursor(sink) -> str:
    with Session(sink.engine) as session:
        return landing.load_cursor(session)


def test_round_attempts_only_eligible_and_remembers_failures(landing_env):
    env = landing_env
    env.outcomes["ep-high"] = PodcastArtifactStorageFull("配额满")

    stats = asyncio.run(app_module.execute_podcast_landing_job())

    # ep-low(4.0)不够线;ep-high 与 ep-mid(恰好 5.0)入队
    assert sorted(env.calls) == ["ep-high", "ep-mid"]
    assert stats["scanned"] == 3 and stats["eligible"] == 2
    assert stats["attempted"] == 2 and stats["succeeded"] == 1 and stats["failed"] == 1
    state = _state(env.sink)
    assert set(state) == {"ep-high"}
    assert state["ep-high"]["kind"] == "deterministic"
    assert state["ep-high"]["code"] == "PodcastArtifactStorageFull"
    assert _cursor(env.sink) == "2026-09-13T00:00:00+00:00"

    # 第二轮:游标之后没有新行,失败的那集未到 retry_at → 什么都不发
    env.calls.clear()
    stats = asyncio.run(app_module.execute_podcast_landing_job())
    assert env.calls == []
    assert stats["scanned"] == 0 and stats["attempted"] == 0


def test_round_picks_up_new_rows_and_due_retries(landing_env, monkeypatch):
    env = landing_env
    env.outcomes["ep-high"] = httpx.ConnectError("boom")
    asyncio.run(app_module.execute_podcast_landing_job())
    assert env.calls == ["ep-high", "ep-mid"] or sorted(env.calls) == ["ep-high", "ep-mid"]
    state = _state(env.sink)
    assert state["ep-high"]["kind"] == "transient" and state["ep-high"]["attempts"] == 1

    # 新到一集(updated_at 在游标之后)+ 退避到期 → 两者都试;ep-mid 已成功不再试
    with Session(env.sink.engine) as session:
        session.add(_episode("ep-new", "2026-09-14T00:00:00+00:00"))
        session.commit()
        session.add(_analysis("ep-new", score=7.0, updated_at="2026-09-14T00:00:00+00:00"))
        session.commit()
    env.calls.clear()
    env.outcomes.pop("ep-high")

    monkeypatch.setattr(
        app_module,
        "_podcast_landing_now",
        lambda: dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc),
    )
    stats = asyncio.run(app_module.execute_podcast_landing_job())
    assert sorted(env.calls) == ["ep-high", "ep-new"]
    assert stats["succeeded"] == 2
    assert _state(env.sink) == {}
    assert _cursor(env.sink) == "2026-09-14T00:00:00+00:00"


def test_round_gates_source_media_downloads_without_touching_enqueue(landing_env, monkeypatch):
    env = landing_env
    env.gate["media_ready"] = False
    stats = asyncio.run(app_module.execute_podcast_landing_job())
    # 三集都没有本地输入、也没有 publisher transcript → 都需要下载源音频 → 全部守门
    assert env.calls == []
    assert stats["gated"] == 2 and stats["attempted"] == 0
    state = _state(env.sink)
    assert {entry["kind"] for entry in state.values()} == {"gated"}
    # 游标照常推进,守门的集靠 state 里的 retry_at 回来
    assert _cursor(env.sink) == "2026-09-13T00:00:00+00:00"

    env.gate["media_ready"] = True

    monkeypatch.setattr(
        app_module,
        "_podcast_landing_now",
        lambda: dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc),
    )
    stats = asyncio.run(app_module.execute_podcast_landing_job())
    assert sorted(env.calls) == ["ep-high", "ep-mid"]
    assert stats["succeeded"] == 2 and _state(env.sink) == {}


def test_round_skips_entirely_when_runtime_blocked(landing_env):
    env = landing_env
    env.gate["runtime_code"] = "podcast_provider_unavailable"
    stats = asyncio.run(app_module.execute_podcast_landing_job())
    assert env.calls == []
    assert stats == {
        "scanned": 0, "eligible": 0, "attempted": 0, "succeeded": 0,
        "failed": 0, "gated": 0, "skipped": 0,
    }
    assert _state(env.sink) == {} and _cursor(env.sink) == ""


def test_round_is_inert_without_llm(landing_env, monkeypatch):
    env = landing_env
    monkeypatch.setattr(
        app_module.daily_brief_service,
        "resolve_llm_config",
        lambda _session: SimpleNamespace(configured=False),
    )
    asyncio.run(app_module.execute_podcast_landing_job())
    assert env.calls == [] and _cursor(env.sink) == ""


def test_analysis_job_no_longer_replays_landing(monkeypatch):
    """The per-minute analysis job must not call the landing loop any more."""

    import inspect

    source = inspect.getsource(app_module.execute_article_analysis_job)
    assert "schedule_podcast_full_analysis" not in source
    assert "collect_podcast_landing_candidates" not in source
    assert not hasattr(app_module, "schedule_podcast_full_analysis")


# ── 调度器接线 ────────────────────────────────────────────────────────────────


class _Scheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[object, object, dict]] = {}

    def add_job(self, callback, trigger, **kwargs):
        self.jobs[kwargs["id"]] = (callback, trigger, kwargs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def test_cron_jobs_get_misfire_grace(monkeypatch):
    scheduler = _Scheduler()
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    app_module.add_cron_job("daily_brief", lambda: None, "30 8 * * *", [])
    _callback, _trigger, kwargs = scheduler.jobs["daily_brief"]
    assert kwargs["misfire_grace_time"] == app_module.CRON_MISFIRE_GRACE_SECONDS >= 60
    assert kwargs["coalesce"] is True


def test_landing_job_registered_once_per_minute(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-sched.db'}")
    scheduler = _Scheduler()
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    monkeypatch.setattr(scheduler, "remove_all_jobs", lambda: None, raising=False)
    app_module.load_tasks_to_scheduler()
    callback, trigger, kwargs = scheduler.jobs[app_module.PODCAST_LANDING_JOB_ID]
    assert callback is app_module.execute_podcast_landing_job
    assert trigger == "interval" and kwargs["minutes"] == 1
    assert kwargs["max_instances"] == 1 and kwargs["coalesce"] is True


# ── 产物库余量探针 ────────────────────────────────────────────────────────────


def test_store_has_capacity_reflects_quota(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'cap.db'}")
    root = tmp_path / "cas"
    store = PodcastArtifactStore(
        sink.engine,
        root,
        max_bytes=1024,
        total_quota_bytes=2048,
        minimum_free_bytes=0,
        staging_ttl_seconds=60,
        allowed_mime_types=("audio/mpeg",),
        orphan_grace_seconds=0,
    )
    assert store.has_capacity() is True
    assert store.has_capacity(2048) is True
    assert store.has_capacity(2049) is False
    blob_dir = root / "ab"
    blob_dir.mkdir(parents=True)
    (blob_dir / ("ab" * 32 + ".mp3")).write_bytes(b"x" * 2048)
    assert store.has_capacity() is True  # 恰好到线,0 字节请求仍不越界
    assert store.has_capacity(1) is False
