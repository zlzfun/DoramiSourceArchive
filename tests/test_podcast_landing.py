"""Podcast 全文处理自动入队:增量游标、轮转 sweep、失败记忆/退避、守门(issue #68)。

生产 2026-09-14 实录:每分钟把 233 集全量重放入队,全部失败再全量重来,同步 DB
与下载校验跑在事件循环里,拖垮日报/采集 cron。本册钉住三层防线的契约,含 codex
首轮检视(P1-1/P1-2/P2-1…P2-5)逐条对应的用例。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.app as app_module  # noqa: E402
from config import AliyunIsiConfig  # noqa: E402
from models.db import AppSettingRecord, ArticleAnalysisRecord, ArticleRecord  # noqa: E402
from services import podcast_landing as landing  # noqa: E402
from services import podcast_publisher_transcripts as publisher  # noqa: E402
from services.aliyun_isi_asr_worker import aliyun_asr_admission_ready  # noqa: E402
from services.podcast_artifacts import (  # noqa: E402
    PodcastArtifactStorageFull,
    PodcastArtifactStore,
    PodcastArtifactTooLarge,
)
from services.podcast_processing_admin import (  # noqa: E402
    PodcastAdminError,
    PodcastProcessingProviderRegistry,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

NOW = dt.datetime(2026, 9, 14, 8, 30, tzinfo=dt.timezone.utc)
STAMP = "2026-09-13T00:00:00+00:00"


# ── 纯逻辑:分类与退避 ────────────────────────────────────────────────────────


def test_classify_failure_three_classes():
    deterministic = landing.classify_failure(
        PodcastAdminError("podcast_source_media_too_long", status_code=422)
    )
    assert deterministic == landing.FailureClass("deterministic", "podcast_source_media_too_long")
    assert landing.classify_failure(PodcastArtifactTooLarge("大")).kind == "deterministic"
    # 配置 / 容量类 → gated(随运维动作变化,按守门节奏回访)
    assert landing.classify_failure(
        PodcastAdminError("podcast_provider_unavailable", status_code=503)
    ) == landing.FailureClass("gated", "podcast_provider_unavailable")
    assert landing.classify_failure(PodcastArtifactStorageFull("满")).kind == "gated"
    assert landing.classify_failure(
        landing.PodcastLandingGated("asr_admission_not_ready")
    ) == landing.FailureClass("gated", "asr_admission_not_ready")
    # 其余 → transient
    assert landing.classify_failure(
        PodcastAdminError("podcast_processing_conflict", status_code=503)
    ).kind == "transient"
    assert landing.classify_failure(httpx.ConnectError("boom")).kind == "transient"


def test_landing_gated_is_an_admin_error_for_the_manual_api():
    exc = landing.PodcastLandingGated("artifact_store_capacity")
    assert isinstance(exc, PodcastAdminError)
    assert exc.status_code == 503 and exc.code == "podcast_landing_gated"
    assert "artifact_store_capacity" in exc.message


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
        assert entry["attempts"] == attempt and entry["kind"] == "transient"
        retry_at = dt.datetime.fromisoformat(entry["retry_at"])
        assert retry_at == NOW + dt.timedelta(seconds=landing.transient_backoff_seconds(attempt))
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
        state, "ep", "prepare:abc", landing.FailureClass("deterministic", "TooLong"), NOW
    )
    assert entry["kind"] == "deterministic" and entry["attempts"] == 1
    assert not landing.is_allowed(state, "ep", "prepare:abc", NOW + dt.timedelta(hours=23))
    assert landing.is_allowed(state, "ep", "prepare:abc", NOW + dt.timedelta(hours=24))
    assert landing.is_allowed(state, "ep", "prepare:def", NOW)
    entry = landing.record_failure(
        state, "ep", "prepare:def", landing.FailureClass("transient", "x"), NOW
    )
    assert entry["attempts"] == 1


def test_gated_failure_uses_gate_cadence_and_keeps_attempts_per_revision():
    """P2-5:revision 切换后先遇 gate,再失败时计数必须从 1 开始。"""

    state: dict = {}
    transient = landing.FailureClass("transient", "x")
    for _ in range(7):
        landing.record_failure(state, "ep", "old", transient, NOW)
    assert state["ep"]["attempts"] == 7
    entry = landing.record_gated(state, "ep", "new", "asr_admission_not_ready", NOW)
    assert entry["kind"] == "gated" and entry["attempts"] == 0
    assert dt.datetime.fromisoformat(entry["retry_at"]) == NOW + dt.timedelta(
        seconds=landing.GATE_RECHECK_SECONDS
    )
    entry = landing.record_failure(state, "ep", "new", transient, NOW)
    assert entry["attempts"] == 1 and entry["kind"] == "transient"
    # 同 revision 的 gate 保留计数
    landing.record_gated(state, "ep", "new", "artifact_store_capacity", NOW)
    assert state["ep"]["attempts"] == 1
    # gated 类失败经 record_failure 走同一条路
    entry = landing.record_failure(
        state, "ep2", "r", landing.FailureClass("gated", "StorageFull"), NOW
    )
    assert entry["kind"] == "gated" and entry["attempts"] == 0


def test_enqueued_entry_blocks_same_revision_forever_and_survives_prune():
    state: dict = {}
    landing.record_enqueued(state, "ep", "rev1", NOW)
    assert state["ep"]["retry_at"] is None
    assert not landing.is_allowed(state, "ep", "rev1", NOW + dt.timedelta(days=400))
    assert landing.is_allowed(state, "ep", "rev2", NOW)
    assert landing.due_retries(state, NOW + dt.timedelta(days=400)) == []
    landing.record_failure(state, "old", "r", landing.FailureClass("transient", "x"), NOW)
    landing.prune_state(state, NOW + dt.timedelta(seconds=landing.STATE_RETENTION_SECONDS + 1))
    assert set(state) == {"ep"}
    landing.record_settled(state, "ep")
    assert state == {}


def test_state_and_cursor_roundtrip_tolerate_garbage_and_legacy_cursor(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-kv.db'}")
    with Session(sink.engine) as session:
        assert landing.load_state(session) == {}
        assert landing.load_cursor(session) == landing.EMPTY_CURSOR
        assert landing.load_sweep_cursor(session) == ""
        session.add(AppSettingRecord(key=landing.STATE_KEY, value="not json"))
        session.add(AppSettingRecord(key=landing.CURSOR_KEY, value="2026-09-14T00:00:00+00:00"))
        session.commit()
        assert landing.load_state(session) == {}
        # 旧版纯时间戳游标按 (ts, "") 收养
        assert landing.load_cursor(session) == ("2026-09-14T00:00:00+00:00", "")
        landing.save_state(session, {"ep": {"kind": "gated"}})
        landing.save_cursor(session, ("2026-09-14T00:00:00+00:00", "ep-9"))
        landing.save_sweep_cursor(session, "ep-3")
        session.commit()
    with Session(sink.engine) as session:
        assert landing.load_state(session) == {"ep": {"kind": "gated"}}
        assert landing.load_cursor(session) == ("2026-09-14T00:00:00+00:00", "ep-9")
        assert landing.load_sweep_cursor(session) == "ep-3"


# ── 增量扫描与 sweep ──────────────────────────────────────────────────────────


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


def _episode(article_id: str, stamp: str, *, extensions: dict | None = None):
    payload = {"audio_url": f"https://cdn.example.test/{article_id}.mp3", "audio_bytes": 1000}
    if extensions:
        payload.update(extensions)
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type="podcast_episode",
        source_id="podcast-ok",
        source_url=f"https://example.test/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        content="show notes",
        extensions_json=json.dumps(payload),
    )


def _seed(sink, rows: list[tuple[str, float, str]], basis_by_id: dict | None = None):
    with Session(sink.engine) as session:
        for article_id, _score, stamp in rows:
            session.add(_episode(article_id, stamp))
        session.commit()  # analyses 有外键,先落 articles
        for article_id, score, stamp in rows:
            session.add(
                _analysis(
                    article_id,
                    score=score,
                    updated_at=stamp,
                    basis=(basis_by_id or {}).get(article_id, "podcast_show_notes"),
                )
            )
        session.commit()


def test_scan_new_candidates_keyset_never_skips_rows_inside_one_timestamp(tmp_path):
    """P1-1:同一 updated_at 下超过一页的行,靠 (updated_at, article_id) 复合游标翻页。"""

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-scan.db'}")
    same = "2026-09-14T00:00:00+00:00"
    ids = [f"e{i:03d}" for i in range(landing.SCAN_LIMIT + 1)]
    _seed(sink, [(i, 6.0, same) for i in ids] + [("later", 6.0, "2026-09-15T00:00:00+00:00")])
    with Session(sink.engine) as session:
        page1, cursor = landing.scan_new_candidates(session, landing.EMPTY_CURSOR)
        assert page1 == ids[: landing.SCAN_LIMIT]
        assert cursor == (same, ids[landing.SCAN_LIMIT - 1])
        page2, cursor = landing.scan_new_candidates(session, cursor)
        assert page2 == [ids[-1], "later"]
        assert cursor == ("2026-09-15T00:00:00+00:00", "later")
        page3, cursor3 = landing.scan_new_candidates(session, cursor)
        assert page3 == [] and cursor3 == cursor
        # 非播客分析不进扫描
        session.add(_episode("art", same)); session.commit()
        session.add(_analysis("art", score=9.0, updated_at="2026-09-16T00:00:00+00:00", basis="article"))
        session.commit()
        assert landing.scan_new_candidates(session, cursor)[0] == []


def test_scan_sweep_page_rotates_and_wraps(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-sweep.db'}")
    _seed(sink, [(f"s{i}", 6.0, STAMP) for i in range(5)])
    with Session(sink.engine) as session:
        ids, after = landing.scan_sweep_page(session, "", page=2)
        assert ids == ["s0", "s1"] and after == "s1"
        ids, after = landing.scan_sweep_page(session, after, page=2)
        assert ids == ["s2", "s3"] and after == "s3"
        ids, after = landing.scan_sweep_page(session, after, page=2)
        assert ids == ["s4"] and after == ""  # 到底回绕


# ── 调度轮次(app 层)────────────────────────────────────────────────────────


@pytest.fixture()
def landing_env(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'landing-job.db'}")
    _seed(sink, [("ep-high", 6.5, STAMP), ("ep-low", 4.0, STAMP), ("ep-mid", 5.0, STAMP)])
    calls: list[str] = []
    outcomes: dict[str, object] = {}

    keys: list[str | None] = []

    async def fake_enqueue(*, episode_id, target, selection_override, reason, actor,
                           idempotency_key=None, idempotency_key_prefix=None):
        calls.append(episode_id)
        keys.append(idempotency_key_prefix)
        exc = outcomes.get(episode_id)
        if isinstance(exc, BaseException):
            raise exc
        if callable(exc):
            await exc()
        return {"episode_id": episode_id, "idempotency_key": idempotency_key}

    gate = app_module.PodcastLandingGate(None, True, 1 << 40)
    holder = {"gate": gate, "now": NOW}
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "enqueue_podcast_processing_with_input", fake_enqueue)
    monkeypatch.setattr(app_module, "podcast_landing_gate", lambda: holder["gate"])
    monkeypatch.setattr(app_module, "_podcast_landing_now", lambda: holder["now"])
    monkeypatch.setattr(
        app_module.daily_brief_service,
        "resolve_llm_config",
        lambda _session: SimpleNamespace(configured=True),
    )
    return SimpleNamespace(sink=sink, calls=calls, outcomes=outcomes, holder=holder, keys=keys)


def _state(sink) -> dict:
    with Session(sink.engine) as session:
        return landing.load_state(session)


def _cursor(sink):
    with Session(sink.engine) as session:
        return landing.load_cursor(session)


def _run():
    return asyncio.run(app_module.execute_podcast_landing_job())


def test_round_attempts_only_eligible_and_remembers_outcomes(landing_env):
    env = landing_env
    env.outcomes["ep-high"] = PodcastArtifactTooLarge("太大")
    stats = _run()
    # ep-low(4.0)不够线;ep-high 与 ep-mid(恰好 5.0)入队
    assert sorted(env.calls) == ["ep-high", "ep-mid"]
    assert stats["scanned"] == 3 and stats["eligible"] == 2 and stats["swept"] == 3
    assert stats["attempted"] == 2 and stats["succeeded"] == 1 and stats["failed"] == 1
    # 幂等键不绑 revision:只传 per-episode 前缀,由 request_processing 在锁内按选中输入派生
    assert sorted(env.keys) == ["full-analysis:auto:ep-high", "full-analysis:auto:ep-mid"]
    state = _state(env.sink)
    assert state["ep-high"]["kind"] == "deterministic"
    assert state["ep-high"]["code"] == "PodcastArtifactTooLarge"
    # 成功的那集记 enqueued,revision 是重算后的最终值(仍无本地输入 → prepare:<enclosure>)
    assert state["ep-mid"]["kind"] == "enqueued"
    assert state["ep-mid"]["revision"].startswith("prepare:")
    assert _cursor(env.sink) == (STAMP, "ep-mid")

    # 第二轮:游标之后没有新行;sweep 回绕重看三集——失败的未到期、成功的 revision 未变 → 一个都不发
    env.calls.clear()
    stats = _run()
    assert env.calls == []
    assert stats["scanned"] == 0 and stats["attempted"] == 0 and stats["skipped"] == 2


def test_round_picks_up_new_rows_and_due_retries(landing_env):
    env = landing_env
    env.outcomes["ep-high"] = httpx.ConnectError("boom")
    _run()
    assert _state(env.sink)["ep-high"]["kind"] == "transient"
    with Session(env.sink.engine) as session:
        session.add(_episode("ep-new", "2026-09-14T00:00:00+00:00")); session.commit()
        session.add(_analysis("ep-new", score=7.0, updated_at="2026-09-14T00:00:00+00:00")); session.commit()
    env.calls.clear()
    env.outcomes.pop("ep-high")
    env.holder["now"] = NOW + dt.timedelta(minutes=6)  # 5 min 退避已过
    stats = _run()
    assert sorted(env.calls) == ["ep-high", "ep-new"]  # ep-mid 已 enqueued 不再发
    assert stats["succeeded"] == 2
    state = _state(env.sink)
    assert {k: v["kind"] for k, v in state.items()} == {
        "ep-high": "enqueued", "ep-mid": "enqueued", "ep-new": "enqueued",
    }
    assert _cursor(env.sink) == ("2026-09-14T00:00:00+00:00", "ep-new")


def test_sweep_catches_locator_change_without_analysis_update(landing_env):
    """P1-2:分析行 updated_at 不变、只改 RSS transcript locator → sweep 拾起并因 revision 变化放行。"""

    env = landing_env
    _run()
    assert _state(env.sink)["ep-mid"]["kind"] == "enqueued"
    old_revision = _state(env.sink)["ep-mid"]["revision"]
    env.calls.clear()
    with Session(env.sink.engine) as session:
        episode = session.get(ArticleRecord, "ep-mid")
        payload = json.loads(episode.extensions_json)
        payload["transcripts"] = [
            {"url": "https://cdn.publisher.example/ep-mid.vtt", "type": "text/vtt", "language": "en"}
        ]
        episode.extensions_json = json.dumps(payload)
        session.add(episode); session.commit()
    stats = _run()
    assert stats["scanned"] == 0  # 增量扫描看不到它
    assert env.calls == ["ep-mid"]  # sweep 看到了
    assert _state(env.sink)["ep-mid"]["revision"] != old_revision
    assert _state(env.sink)["ep-mid"]["revision"].startswith("prepare:")


def test_due_entry_that_is_no_longer_eligible_is_settled_once(landing_env, monkeypatch):
    """P2-1:到期但已不再是候选(分数降线 / 已完成)的 state 必须被清掉,不能每分钟 due。"""

    env = landing_env
    env.outcomes["ep-high"] = httpx.ConnectError("boom")
    _run()
    assert _state(env.sink)["ep-high"]["kind"] == "transient"
    with Session(env.sink.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "ep-high")
        analysis.quality_score = 3.0
        session.add(analysis); session.commit()
    env.calls.clear()
    env.holder["now"] = NOW + dt.timedelta(minutes=6)
    resolved: list[list[str]] = []
    real_collect = app_module.collect_podcast_landing_candidates

    def spy(ids):
        resolved.append(list(ids))
        return real_collect(ids)

    monkeypatch.setattr(app_module, "collect_podcast_landing_candidates", spy)
    stats = _run()
    assert stats["settled"] >= 1 and env.calls == []
    assert "ep-high" not in _state(env.sink)
    # 再一轮:它既不到期也不在增量里,只可能随 sweep 页被读一次,绝不再作为 due 重复出现
    env.holder["now"] = NOW + dt.timedelta(minutes=7)
    resolved.clear()
    _run()
    assert all(ids.count("ep-high") <= 1 for ids in resolved)
    assert "ep-high" not in _state(env.sink)


def test_gate_holds_back_source_media_downloads(landing_env):
    """P2-3:ASR admission 未就绪 / 余量不足本集下载上限 → gated,不碰入队。"""

    env = landing_env
    env.holder["gate"] = app_module.PodcastLandingGate(None, False, 1 << 40)
    stats = _run()
    assert env.calls == [] and stats["gated"] == 2
    assert {v["code"] for v in _state(env.sink).values()} == {"asr_admission_not_ready"}

    # 余量小于本集 declared_bytes(1000)→ 仍 gated;余量足够 → 放行
    env.holder["now"] = NOW + dt.timedelta(seconds=landing.GATE_RECHECK_SECONDS)
    env.holder["gate"] = app_module.PodcastLandingGate(None, True, 999)
    stats = _run()
    assert env.calls == [] and stats["gated"] == 2
    assert {v["code"] for v in _state(env.sink).values()} == {"artifact_store_capacity"}
    env.holder["now"] = NOW + dt.timedelta(seconds=2 * landing.GATE_RECHECK_SECONDS)
    env.holder["gate"] = app_module.PodcastLandingGate(None, True, 1000)
    stats = _run()
    assert sorted(env.calls) == ["ep-high", "ep-mid"] and stats["succeeded"] == 2


def test_gated_enqueue_failure_is_recorded_on_gate_cadence(landing_env):
    """P2-4 落地侧:入队过程中抛 PodcastLandingGated → gated,10 min 回访而非 24h 长停。"""

    env = landing_env
    env.outcomes["ep-high"] = landing.PodcastLandingGated("asr_admission_not_ready")
    _run()
    entry = _state(env.sink)["ep-high"]
    assert entry["kind"] == "gated" and entry["code"] == "asr_admission_not_ready"
    assert dt.datetime.fromisoformat(entry["retry_at"]) == NOW + dt.timedelta(
        seconds=landing.GATE_RECHECK_SECONDS
    )


def test_failure_is_checkpointed_before_the_round_finishes(landing_env):
    """P2-2:第一条失败落定后取消第二条,失败记忆已在库里,重启不重做第一条。"""

    env = landing_env
    env.outcomes["ep-high"] = httpx.ConnectError("boom")
    release = asyncio.Event()

    async def hang():
        await release.wait()

    env.outcomes["ep-mid"] = hang

    async def scenario():
        task = asyncio.create_task(app_module.execute_podcast_landing_job())
        for _ in range(200):
            await asyncio.sleep(0.02)
            if _state(env.sink).get("ep-high", {}).get("kind") == "transient":
                break
        assert _state(env.sink)["ep-high"]["kind"] == "transient"
        assert _cursor(env.sink) == landing.EMPTY_CURSOR  # 游标只在轮末推进
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    env.calls.clear()
    env.outcomes.pop("ep-mid")
    _run()
    assert env.calls == ["ep-mid"]  # ep-high 在退避中,不重做


def test_round_skips_entirely_when_runtime_blocked(landing_env):
    env = landing_env
    env.holder["gate"] = app_module.PodcastLandingGate("podcast_provider_unavailable", False, 0)
    stats = _run()
    assert env.calls == [] and stats["attempted"] == 0 and stats["scanned"] == 0
    assert _state(env.sink) == {} and _cursor(env.sink) == landing.EMPTY_CURSOR


def test_round_is_inert_without_llm(landing_env, monkeypatch):
    env = landing_env
    monkeypatch.setattr(
        app_module.daily_brief_service,
        "resolve_llm_config",
        lambda _session: SimpleNamespace(configured=False),
    )
    _run()
    assert env.calls == [] and _cursor(env.sink) == landing.EMPTY_CURSOR


def test_analysis_job_no_longer_replays_landing():
    import inspect

    source = inspect.getsource(app_module.execute_article_analysis_job)
    assert "schedule_podcast_full_analysis" not in source
    assert "collect_podcast_landing_candidates" not in source
    assert not hasattr(app_module, "schedule_podcast_full_analysis")


# ── 入队路径(真实函数,P2-4)────────────────────────────────────────────────


@pytest.fixture()
def enqueue_env(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'enqueue.db'}")
    _seed(sink, [("ep-vtt", 6.5, STAMP)])
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "ep-vtt")
        payload = json.loads(episode.extensions_json)
        payload["transcripts"] = [
            {"url": "https://cdn.publisher.example/ep-vtt.vtt", "type": "text/vtt", "language": "en"}
        ]
        episode.extensions_json = json.dumps(payload)
        session.add(episode); session.commit()
    ingests: list[str] = []

    async def failing_ingest(_engine, *, episode_id, config, client):
        ingests.append(episode_id)
        raise publisher.PublisherTranscriptFetchFailed("fetch failed")

    def not_ready(*_args, **_kwargs):
        raise PodcastAdminError("podcast_artifact_not_ready", status_code=409)

    async def forbidden_validate(*_args, **_kwargs):
        pytest.fail("source media must not be downloaded while the gate is closed")

    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module.podcast_publisher_transcript_service, "ingest_publisher_transcript", failing_ingest)
    monkeypatch.setattr(app_module.podcast_processing_admin_service, "request_processing", not_ready)
    monkeypatch.setattr(app_module.podcast_processing_admin_service, "require_full_analysis_authority", lambda *a, **k: None)
    monkeypatch.setattr(app_module.podcast_processing_admin_service, "require_full_analysis_llm", lambda *a, **k: None)
    monkeypatch.setattr(app_module.podcast_source_media_service, "validate_source_media", forbidden_validate)
    return SimpleNamespace(sink=sink, ingests=ingests)


def _enqueue():
    return asyncio.run(
        app_module.enqueue_podcast_processing_with_input(
            episode_id="ep-vtt",
            target="full_analysis",
            selection_override=False,
            idempotency_key="k",
            reason="test",
            actor="system",
        )
    )


def test_enqueue_reapplies_asr_gate_after_publisher_failure(enqueue_env, monkeypatch):
    env = enqueue_env
    monkeypatch.setattr(app_module, "_podcast_asr_admission_ready", lambda: False)
    with pytest.raises(landing.PodcastLandingGated) as info:
        _enqueue()
    assert info.value.reason == "asr_admission_not_ready"
    assert env.ingests == ["ep-vtt"]  # publisher 只试一次

    monkeypatch.setattr(app_module, "_podcast_asr_admission_ready", lambda: True)
    monkeypatch.setattr(app_module.podcast_artifact_store, "has_capacity", lambda _limit: False)
    with pytest.raises(landing.PodcastLandingGated) as info:
        _enqueue()
    assert info.value.reason == "artifact_store_capacity"
    assert env.ingests == ["ep-vtt", "ep-vtt"]


def test_success_then_failed_reresolution_keeps_memory(landing_env, monkeypatch):
    """round2-F1:入队成功后重解析瞬时失败 → 保留 enqueued 记忆而非当 settled 抹掉。"""

    env = landing_env
    real_collect = app_module.collect_podcast_landing_candidates
    seen: list[list[str]] = []

    def flaky(ids):
        seen.append(list(ids))
        if ids == ["ep-mid"]:  # 成功后的单集重解析
            resolution = app_module.PodcastLandingResolution()
            resolution.failed["ep-mid"] = RuntimeError("db hiccup")
            return resolution
        return real_collect(ids)

    monkeypatch.setattr(app_module, "collect_podcast_landing_candidates", flaky)
    stats = _run()
    assert stats["succeeded"] == 2
    state = _state(env.sink)
    assert state["ep-mid"]["kind"] == "enqueued" and state["ep-mid"]["revision"].startswith("prepare:")
    # 下一轮 sweep 同 revision → 不再入队
    env.calls.clear()
    monkeypatch.setattr(app_module, "collect_podcast_landing_candidates", real_collect)
    _run()
    assert env.calls == []


def test_request_processing_derives_auto_key_inside_lock(tmp_path, monkeypatch):
    """round2-F1:自动键在 request_processing 锁内按实际选中输入派生,同输入同键、换输入换键。"""

    from services import podcast_processing_admin as admin
    from config import PodcastConfig

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'auto-key.db'}")
    _seed(sink, [("ep-key", 6.5, STAMP)])
    selected_hashes = iter(["c" * 64, "c" * 64, "d" * 64])
    captured: list[str] = []

    def fake_select(_session, *, episode_id, target):
        return admin.SelectedInput("asr", "snap-1", next(selected_hashes), "source_media_snapshot", "und", 1000)

    def fake_enqueue_locked(_session, *, idempotency_key, **_kwargs):
        captured.append(idempotency_key)
        raise admin.PodcastAdminError("podcast_processing_conflict", status_code=409)

    class _Registry:
        def estimate(self, *_a, **_k):
            return SimpleNamespace(admission_fingerprint="fp")

    monkeypatch.setattr(admin, "_select_external_input", fake_select)
    monkeypatch.setattr(admin, "_enqueue_locked", fake_enqueue_locked)
    monkeypatch.setattr(admin, "_replay_request", lambda *_a, **_k: None)
    monkeypatch.setattr(admin, "_require_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(admin, "require_full_analysis_llm", lambda *_a, **_k: None)
    monkeypatch.setattr(admin, "require_full_analysis_authority", lambda *_a, **_k: None)
    config = PodcastConfig(
        installation="external", authority_id="t", processing_enabled=True,
        allowed_stages=("fetch", "asr", "analyze"), provider_ready_targets=("full_analysis",),
        monthly_budget_cny_minor=10_000, per_run_budget_cny_minor=1_000,
    )
    for _ in range(3):
        with pytest.raises(admin.PodcastAdminError):
            admin.request_processing(
                sink.engine, _Registry(), config, episode_id="ep-key", target="full_analysis",
                selection_override=False, idempotency_key_prefix="full-analysis:auto:ep-key",
                reason="t", actor="system",
            )
    assert captured[0] == captured[1] == "full-analysis:auto:ep-key:" + "c" * 16
    assert captured[2] == "full-analysis:auto:ep-key:" + "d" * 16
    with pytest.raises(admin.PodcastAdminError) as info:
        admin.request_processing(
            sink.engine, _Registry(), config, episode_id="ep-key", target="full_analysis",
            selection_override=False, idempotency_key="k", idempotency_key_prefix="p",
            reason="t", actor="system",
        )
    assert info.value.status_code == 422


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

    def remove_all_jobs(self):
        self.jobs.clear()


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
    app_module.load_tasks_to_scheduler()
    callback, trigger, kwargs = scheduler.jobs[app_module.PODCAST_LANDING_JOB_ID]
    assert callback is app_module.execute_podcast_landing_job
    assert trigger == "interval" and kwargs["minutes"] == 1
    assert kwargs["max_instances"] == 1 and kwargs["coalesce"] is True


# ── 探针 ──────────────────────────────────────────────────────────────────────


def test_store_capacity_probes(tmp_path):
    """round2-F2:轮级探针同时受配额与磁盘 minimum-free 线约束,与 has_capacity 同尺。"""

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'cap.db'}")
    root = tmp_path / "cas"
    store = PodcastArtifactStore(
        sink.engine, root, max_bytes=1024, total_quota_bytes=2048, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/mpeg",), orphan_grace_seconds=0,
    )
    assert store.reservable_bytes() == 2048
    assert store.has_capacity(2048) is True and store.has_capacity(2049) is False
    blob_dir = root / "ab"; blob_dir.mkdir(parents=True)
    (blob_dir / ("ab" * 32 + ".mp3")).write_bytes(b"x" * 2048)
    assert store.reservable_bytes() == 0
    assert store.has_capacity(1) is False
    # 无配额但磁盘保留线高于容量 → 0,且 has_capacity 同样拒绝
    starved = PodcastArtifactStore(
        sink.engine, tmp_path / "cas2", max_bytes=1024, total_quota_bytes=0,
        minimum_free_bytes=1 << 60, staging_ttl_seconds=60,
        allowed_mime_types=("audio/mpeg",), orphan_grace_seconds=0,
    )
    assert starved.reservable_bytes() == 0 and starved.has_capacity(1) is False
    # 无配额、磁盘充裕 → 受磁盘余量约束的正数
    unlimited = PodcastArtifactStore(
        sink.engine, tmp_path / "cas3", max_bytes=1024, total_quota_bytes=0, minimum_free_bytes=0,
        staging_ttl_seconds=60, allowed_mime_types=("audio/mpeg",), orphan_grace_seconds=0,
    )
    assert unlimited.reservable_bytes() > 0


def test_registry_admission_readiness_is_stricter_than_poll_readiness():
    registry = PodcastProcessingProviderRegistry()
    registry.register_stage_worker(
        "asr", lambda *_a, **_k: None, readiness=lambda _c: True, admission_readiness=lambda c: c == "ok"
    )
    assert registry.stage_worker_ready("asr", "anything") is True
    assert registry.stage_admission_ready("asr", "ok") is True
    assert registry.stage_admission_ready("asr", "missing-app-key") is False
    assert registry.stage_admission_ready("tts", "ok") is False
    fallback = PodcastProcessingProviderRegistry()
    fallback.register_stage_worker("asr", lambda *_a, **_k: None, readiness=lambda _c: True)
    assert fallback.stage_admission_ready("asr", object()) is True


def test_aliyun_admission_requires_app_key_and_accounting():
    poll_only = AliyunIsiConfig(access_key_id="ak", access_key_secret="sk")
    assert poll_only.asr_poll_configured and not poll_only.asr_configured
    assert aliyun_asr_admission_ready(poll_only) is False
    with_key = replace(poll_only, app_key="app")
    assert with_key.asr_configured
    assert aliyun_asr_admission_ready(with_key) is with_key.asr_accounting_ready
    assert aliyun_asr_admission_ready(object()) is False
