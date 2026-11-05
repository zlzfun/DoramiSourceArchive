"""issue #261 节点同步控制台:首页计数、进度观测、测试连接计数、单流重置、状态 / 本机条数 / 首页摘要。"""

import asyncio
import json
import os
import sys
import time

import httpx
import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArchiveSyncEntityStateRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    JobRecord,
    TaxonomyVersionRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from services import sync_console  # noqa: E402
from services.article_analysis import compute_content_hash  # noqa: E402
from tests.test_archive_sync_v2 import (  # noqa: E402
    _V2Remote,
    _article,
    _prepare_and_pull,
    _sink,
    _source,
)

BASE_URL = "https://remote.test"
NOW = "2026-11-05T00:00:00+00:00"


def _seed_producer(producer, *, articles=3, analyses=True):
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW))
        for index in range(articles):
            article = _article(f"article-{index}", content=f"Body {index}")
            session.add(article)
            session.flush()
            if analyses:
                session.add(ArticleAnalysisRecord(
                    article_id=article.id, status="succeeded", tagging_status="succeeded",
                    quality_score=5.0, summary=f"摘要 {index}", content_hash=compute_content_hash(article),
                    created_at=NOW, updated_at=NOW,
                ))
        session.commit()


def _manifest(raw):
    return json.loads(raw.split("\n", 1)[0])


def _rows(raw):
    return [line for line in raw.split("\n")[1:] if line.strip()]


def test_first_page_pending_equals_rows_exported_across_pages(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    _seed_producer(producer, articles=3)
    first = archive_sync_v2.export_page(producer.engine, "articles", limit=2)
    manifest = _manifest(first)
    assert manifest["pending"] == 3
    exported = len(_rows(first))
    second = archive_sync_v2.export_page(
        producer.engine, "articles", snapshot=manifest["snapshot"],
        after=manifest["next_cursor"], limit=2,
    )
    assert "pending" not in _manifest(second)   # 只有首页带分母
    exported += len(_rows(second))
    assert exported == 3

    # tombstone 也是一行:删掉一篇后,增量首页的分母只数这一行
    snapshot = manifest["snapshot"]
    with Session(producer.engine) as session:
        session.delete(session.get(ArticleRecord, "article-0"))
        session.commit()
    delta = archive_sync_v2.export_page(producer.engine, "articles", since=snapshot)
    assert _manifest(delta)["pending"] == len(_rows(delta)) == 1

    sources = _manifest(archive_sync_v2.export_page(producer.engine, "sources", limit=1))
    by_stream = sources["pending_by_stream"]
    assert "media" not in by_stream and "taxonomy" not in by_stream
    assert by_stream["sources"] == sources["pending"] == 1
    # 全量计数:3 篇里 1 篇已是 tombstone,实体状态仍是 3 个
    assert by_stream["articles"] == 3
    assert by_stream["analyses"] >= 2
    # 老接收端照常解析(附加键被忽略)
    archive_sync_v2.parse_page(archive_sync_v2.export_page(producer.engine, "sources"), expected_stream="sources")


def test_taxonomy_manifest_has_no_pending(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    _seed_producer(producer, articles=1)
    raw = archive_sync_v2.export_page(producer.engine, "taxonomy")
    assert "pending" not in _manifest(raw)


def test_pull_progress_reports_totals_pages_and_completed_streams(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=3)
    remote = _V2Remote(producer)
    snapshots = []
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url=BASE_URL,
        username="admin",
        password="secret",
        media_root=tmp_path / "media",
        page_size=2,
        on_progress=lambda progress: snapshots.append(json.loads(json.dumps(progress))),
        transport=httpx.MockTransport(remote.handler),
    ))
    articles = [item for item in snapshots if item["stream"] == "articles"]
    # 首页 manifest 到达前分母未知(None),之后恒为 3
    assert {item["stream_total"] for item in articles if item["stream_total"] is not None} == {3}
    assert all(item["page"] <= 1 for item in articles if item["stream_total"] is None)
    assert max(item["page"] for item in articles) == 2
    assert articles[-1]["stream_processed"] == 3
    assert articles[-1]["order"] == list(remote_sync_service.V2_STREAM_ORDER)
    assert snapshots[-1]["run_pending"]["articles"] == 3
    final_done = snapshots[-1]["streams_done"]
    assert list(final_done) == list(remote_sync_service.V2_STREAM_ORDER)
    assert final_done["articles"]["count"] == 3 and final_done["articles"]["pending"] == 3
    assert final_done["articles"]["started_at"] <= final_done["articles"]["completed_at"]
    streamed = result["streams"]["articles"]
    assert streamed["pending"] == 3 and streamed["completed_at"] and streamed["started_at"]


def test_failed_pull_progress_keeps_stream_page_and_completed_streams(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=3)
    remote = _V2Remote(producer)
    calls = {"articles": 0}

    def handler(request):
        if request.url.path.endswith("/export/articles.jsonl"):
            calls["articles"] += 1
            if calls["articles"] == 2:
                return httpx.Response(400, json={"detail": "boom"})
        return remote.handler(request)

    snapshots = []
    with pytest.raises(remote_sync_service.RemoteSyncError):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url=BASE_URL,
            username="admin",
            password="secret",
            media_root=tmp_path / "media",
            page_size=2,
            on_progress=lambda progress: snapshots.append(json.loads(json.dumps(progress))),
            transport=httpx.MockTransport(handler),
        ))
    last = snapshots[-1]
    assert (last["stream"], last["page"], last["stream_total"]) == ("articles", 2, 3)
    assert list(last["streams_done"]) == ["sources", "taxonomy"]


def test_old_producer_without_counts_reports_unknown_totals(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=1)
    remote = _V2Remote(producer)

    def handler(request):
        response = remote.handler(request)
        if not request.url.path.startswith("/api/archive/v2/export/"):
            return response
        lines = response.text.split("\n")
        manifest = json.loads(lines[0])
        manifest.pop("pending", None)
        manifest.pop("pending_by_stream", None)
        lines[0] = json.dumps(manifest, ensure_ascii=False)
        return httpx.Response(200, text="\n".join(lines))

    snapshots = []
    asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url=BASE_URL,
        username="admin",
        password="secret",
        media_root=tmp_path / "media",
        on_progress=lambda progress: snapshots.append(dict(progress)),
        transport=httpx.MockTransport(handler),
    ))
    assert all(item["stream_total"] is None for item in snapshots)
    assert snapshots[-1]["run_pending"] is None


def test_probe_with_since_returns_pending_and_falls_back_when_since_is_ahead(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    _seed_producer(producer, articles=2)
    remote = _V2Remote(producer)

    def handler(request):
        try:
            return remote.handler(request)
        except archive_sync_v2.SyncV2Error as exc:   # 真实导出端点把它转成 400
            return httpx.Response(400, json={"detail": str(exc)})

    transport = httpx.MockTransport(handler)
    full = asyncio.run(remote_sync_service.probe(BASE_URL, "admin", "secret", transport=transport))
    assert full["pending"]["articles"] == 2 and full["pending_since"] == ""
    assert full["snapshot"]
    caught_up = asyncio.run(remote_sync_service.probe(
        BASE_URL, "admin", "secret", since=full["snapshot"], transport=transport,
    ))
    assert caught_up["pending"]["articles"] == 0
    assert caught_up["pending_since"] == full["snapshot"]
    ahead = str(int(full["snapshot"]) + 1000)
    fallback = asyncio.run(remote_sync_service.probe(
        BASE_URL, "admin", "secret", since=ahead, transport=transport,
    ))
    assert fallback["pending_since"] == "" and fallback["pending"]["articles"] == 2


def _entity_states(engine, stream):
    with Session(engine) as session:
        return session.exec(select(ArchiveSyncEntityStateRecord).where(
            ArchiveSyncEntityStateRecord.stream == stream,
            ArchiveSyncEntityStateRecord.authority_id != "",
        )).all()


def test_reset_stream_forces_full_repull_of_only_that_stream(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=2)
    remote = _V2Remote(producer)
    _prepare_and_pull(consumer, producer, remote, tmp_path, label="first")
    assert _entity_states(consumer.engine, "analyses")
    articles_before = {(row.identity, row.revision) for row in _entity_states(consumer.engine, "articles")}

    reset = sync_console.reset_stream(consumer.engine, "analyses", by="admin", at=NOW)
    assert reset == {"stream": "analyses", "reset_at": NOW, "target": BASE_URL}
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][BASE_URL]
    assert "analyses" not in target["v2_streams"] and "articles" in target["v2_streams"]
    assert target["v2_stream_resets"]["analyses"] == {"at": NOW, "by": "admin"}
    assert (target["v2_analysis_refetch_at"], target["v2_analysis_refetch_by"]) == (NOW, "admin")
    assert _entity_states(consumer.engine, "analyses") == []

    remote.requested_params.clear()
    result = _prepare_and_pull(consumer, producer, remote, tmp_path, label="after-reset")
    params = dict(remote.requested_params)
    assert not params["analyses"].get("since") and params["articles"].get("since")
    assert result["streams"]["analyses"]["count"] == 2
    assert result["streams"]["articles"]["count"] == 0
    assert {(row.identity, row.revision) for row in _entity_states(consumer.engine, "articles")} == articles_before
    assert len(_entity_states(consumer.engine, "analyses")) == 2

    # taxonomy 没有逐行记账,只清 checkpoint
    sync_console.reset_stream(consumer.engine, "taxonomy", by="admin", at=NOW)
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][BASE_URL]
    assert "taxonomy" not in target["v2_streams"]


def test_reset_stream_rejects_running_job_and_unconfigured_target(tmp_path):
    consumer = _sink(tmp_path, "consumer.db")
    with pytest.raises(sync_console.SyncConsoleError):
        sync_console.reset_stream(consumer.engine, "articles", by="admin", at=NOW)
    producer = _sink(tmp_path, "producer.db")
    _seed_producer(producer, articles=1)
    _prepare_and_pull(consumer, producer, _V2Remote(producer), tmp_path, label="first")
    with Session(consumer.engine) as session:
        session.add(JobRecord(
            id="job-running", type=remote_sync_service.REMOTE_SYNC_JOB_TYPE, status="running",
            created_at=time.time(),
        ))
        session.commit()
    with pytest.raises(sync_console.SyncConsoleError):
        sync_console.reset_stream(consumer.engine, "articles", by="admin", at=NOW)
    with pytest.raises(ValueError):
        sync_console.reset_stream(consumer.engine, "nope", by="admin", at=NOW)


def test_console_status_inventory_and_home_summary(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    assert sync_console.home_summary(consumer.engine)["status"] == "none"
    _seed_producer(producer, articles=2)
    _prepare_and_pull(consumer, producer, _V2Remote(producer), tmp_path, label="first")

    status = sync_console.console_status(consumer.engine, media_store_ready=True, podcast_store_ready=False)
    assert status["configured"] is True and status["target"] == BASE_URL
    assert status["authority_id"] == archive_sync_v2.producer_authority_id(producer.engine)
    assert status["stream_order"] == list(remote_sync_service.V2_STREAM_ORDER)
    assert status["checkpoints"]["articles"]["count"] == 2
    assert status["local_stores"] == {"media": True, "podcast": False}
    assert status["probe"] is None
    assert '"password":' not in json.dumps(status)

    sync_console.record_probe(consumer.engine, {
        "base_url": BASE_URL, "protocol": "v2", "version": "3.67.4", "snapshot": "9",
        "pending": {"articles": 4}, "password": "never-stored",
    }, probed_at=NOW)
    probe = sync_console.console_status(
        consumer.engine, media_store_ready=True, podcast_store_ready=True,
    )["probe"]
    assert probe["version"] == "3.67.4" and probe["pending"] == {"articles": 4}
    assert "password" not in probe

    inventory = sync_console.inventory(consumer.engine)
    assert inventory["items"]["articles"] == 2
    assert inventory["items"]["analyses"] == inventory["items"]["summaries"] == 2
    assert inventory["streams"]["articles"]["upsert"] == 2

    with Session(consumer.engine) as session:
        session.add(JobRecord(
            id="job-ok", type=remote_sync_service.REMOTE_SYNC_JOB_TYPE, status="succeeded",
            created_at=time.time() - 60, started_at=time.time() - 60, ended_at=time.time() - 30,
        ))
        session.add(JobRecord(
            id="job-failed", type=remote_sync_service.REMOTE_SYNC_JOB_TYPE, status="failed",
            created_at=time.time(), started_at=time.time(), ended_at=time.time(),
            error="v2 analyses 导入失败:boom",
            progress_json=json.dumps({"stream": "analyses", "page": 37, "streams_done": {"sources": {}}}),
        ))
        session.commit()
    home = sync_console.home_summary(consumer.engine)
    assert home["status"] == "failed"
    assert home["failed"]["stream"] == "analyses" and home["failed"]["page"] == 37
    assert home["last_success_at"]
    assert sync_console.job_creators(consumer.engine, ["job-ok"]) == {"job-ok": None}


def test_status_target_prefers_schedule_then_latest_synced():
    state = {"targets": {
        "https://a.test": {"last_synced_at": "2026-11-01T00:00:00+00:00"},
        "https://b.test": {"last_synced_at": "2026-11-03T00:00:00+00:00"},
    }}
    assert sync_console.resolve_target(state, {"base_url": ""}) == "https://b.test"
    assert sync_console.resolve_target(state, {"base_url": "https://a.test/"}) == "https://a.test"
    assert sync_console.resolve_target({"targets": {}}, {}) == ""


def test_probe_kv_ignores_v1_and_keeps_no_secret(tmp_path):
    sink = _sink(tmp_path, "consumer.db")
    sync_console.record_probe(sink.engine, {"base_url": BASE_URL, "protocol": "v1"}, probed_at=NOW)
    with Session(sink.engine) as session:
        assert session.get(AppSettingRecord, sync_console.PROBE_STATE_KEY) is None


def test_reset_audit_summary_names_the_stream():
    from services import admin_audit

    def render(path):
        for method, pattern, render_fn in admin_audit.AUDIT_SUMMARY_RULES:
            match = pattern.match(path)
            if method == "POST" and match:
                return render_fn(match, None)
        return None

    assert admin_audit.should_audit("/api/admin/remote-sync/streams/analyses/reset", "POST")
    assert render("/api/admin/remote-sync/streams/analyses/reset") == ("重放分析同步流", "analyses")
    assert render("/api/admin/remote-sync/streams/media/reset") == ("重置同步流 media", "media")


def test_v1_only_legacy_target_is_not_a_v2_console_target(tmp_path):
    """只有 v1 兼容记录的老目标:不算已接入(显示接入卡并预填),协议标 v1;首页磁贴同口径。"""
    sink = _sink(tmp_path, "legacy.db")
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key=remote_sync_service.REMOTE_SYNC_STATE_KEY, value=json.dumps({
            "targets": {"https://old.test": {
                "username": "admin", "last_fetched_date": "2026-07-24", "last_synced_at": "2026-07-24T09:38:00",
            }},
        })))
        session.commit()
    status = sync_console.console_status(sink.engine, media_store_ready=True, podcast_store_ready=True)
    assert (status["target"], status["configured"], status["protocol"]) == ("https://old.test", False, "v1")
    assert status["username"] == "admin"
    assert sync_console.home_summary(sink.engine)["configured"] is False


# ── 检视 R1 回归 ──

def _run_in_thread(fn):
    import threading

    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def test_reset_and_launch_share_one_control_lock(tmp_path, monkeypatch):
    """R1 #1:重置与启动任务串行;持锁期间两者都等,释放后各自完成。"""
    import importlib

    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=1)
    _prepare_and_pull(consumer, producer, _V2Remote(producer), tmp_path, label="first")
    lock = remote_sync_service.sync_control_lock(consumer.engine)
    assert lock is remote_sync_service.sync_control_lock(consumer.engine)

    lock.acquire()
    thread, box = _run_in_thread(
        lambda: sync_console.reset_stream(consumer.engine, "analyses", by="admin", at=NOW)
    )
    thread.join(0.3)
    assert thread.is_alive()                       # 被锁挡住,还没清任何东西
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][BASE_URL]
    assert "analyses" in target["v2_streams"]
    lock.release()
    thread.join(5)
    assert box["value"]["stream"] == "analyses"

    router = importlib.import_module("api.routers.remote_sync")
    launched = []
    monkeypatch.setattr(router.jobs, "launch", lambda *a, **k: launched.append(k) or object())
    # 跳过锁外的 v1 降级检查,让启动走进「查活动任务 → 落任务」临界区
    monkeypatch.setattr(router.sync_consumer_policy, "v2_receiver_state_present", lambda session: False)
    other = _sink(tmp_path, "other.db")
    lock.acquire()
    thread, box = _run_in_thread(lambda: router.launch_remote_sync_job(
        other.engine, base_url="https://v1.test", username="a", password="b", protocol="v1",
    ))
    thread.join(2)
    assert not thread.is_alive() and len(launched) == 1     # 锁按 engine 区分,别的库不受影响
    thread, box = _run_in_thread(lambda: router.launch_remote_sync_job(
        consumer.engine, base_url=BASE_URL, username="a", password="b", protocol="v1",
    ))
    thread.join(0.3)
    assert thread.is_alive() and len(launched) == 1          # 同一库:启动在锁外等
    lock.release()
    thread.join(5)
    assert len(launched) == 2 and "error" not in box


def test_run_pending_keeps_only_streams_sharing_the_sources_since(tmp_path):
    """R1 #3:重置 analyses 后,sources 首页的 pending_by_stream 不能当成 analyses 的待处理量。"""
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=3)
    _prepare_and_pull(consumer, producer, _V2Remote(producer), tmp_path, label="first")
    sync_console.reset_stream(consumer.engine, "analyses", by="admin", at=NOW)
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][BASE_URL]
    snapshots = []
    asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine, base_url=BASE_URL, username="admin", password="secret",
        media_root=tmp_path / "media", checkpoints=target["v2_streams"],
        on_progress=lambda progress: snapshots.append(json.loads(json.dumps(progress))),
        transport=httpx.MockTransport(_V2Remote(producer).handler),
    ))
    run_pending = next(item["run_pending"] for item in snapshots if item["run_pending"])
    assert "analyses" not in run_pending and run_pending["articles"] == 0
    analyses = [item for item in snapshots if item["stream"] == "analyses" and item["stream_total"] is not None]
    assert analyses[-1]["stream_total"] == 3 and analyses[-1]["stream_processed"] == 3


def test_first_page_import_failure_keeps_known_totals_in_final_progress(tmp_path, monkeypatch):
    """R1 #6:sources 首页 manifest 已给出分母与协商结果,导入失败时终态进度不能退回请求前快照。"""
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=2)

    def boom(*args, **kwargs):
        raise archive_sync_v2.SyncV2Error("injected import failure")

    monkeypatch.setattr(archive_sync_v2, "import_page", boom)
    snapshots = []
    with pytest.raises(remote_sync_service.RemoteSyncError):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine, base_url=BASE_URL, username="admin", password="secret",
            media_root=tmp_path / "media",
            on_progress=lambda progress: snapshots.append(json.loads(json.dumps(progress))),
            transport=httpx.MockTransport(_V2Remote(producer).handler),
        ))
    last = snapshots[-1]
    assert (last["stream"], last["page"], last["stream_total"]) == ("sources", 1, 1)
    assert last["order"] == list(remote_sync_service.V2_STREAM_ORDER)
    assert last["run_pending"]["articles"] == 2


def test_pending_is_a_first_page_estimate_when_rows_change_between_pages(tmp_path):
    """R1 #7:分页期间被再次修改的实体移出本快照(处理量 ≤ pending),下次增量拿到,不丢数据。"""
    producer = _sink(tmp_path, "producer.db")
    _seed_producer(producer, articles=3, analyses=False)
    first = archive_sync_v2.export_page(producer.engine, "articles", limit=2)
    manifest = _manifest(first)
    assert manifest["pending"] == 3
    exported = {json.loads(line)["identity"] for line in _rows(first)}
    remaining = ({"article-0", "article-1", "article-2"} - exported).pop()
    with Session(producer.engine) as session:
        row = session.get(ArticleRecord, remaining)
        row.title = "changed between pages"
        session.add(row)
        session.commit()
    second = archive_sync_v2.export_page(
        producer.engine, "articles", snapshot=manifest["snapshot"], after=manifest["next_cursor"], limit=2,
    )
    assert _manifest(second)["complete"] is True and _rows(second) == []
    assert len(exported) == 2 <= manifest["pending"]
    later = archive_sync_v2.export_page(producer.engine, "articles", since=manifest["snapshot"])
    assert [json.loads(line)["identity"] for line in _rows(later)] == [remaining]


def test_whole_run_success_is_separate_from_per_stream_progress(tmp_path):
    """R1 #4:只有逐流推进(首流成功后续失败)不算整次成功;成功后才记 v2_last_success_at。"""
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _seed_producer(producer, articles=1)
    _prepare_and_pull(consumer, producer, _V2Remote(producer), tmp_path, label="first")
    target = remote_sync_service.load_sync_state(consumer.engine)["targets"][BASE_URL]
    assert target["last_synced_at"] and "v2_last_success_at" not in target
    assert sync_console.home_summary(consumer.engine)["last_success_at"] == ""
    assert sync_console.console_status(
        consumer.engine, media_store_ready=True, podcast_store_ready=True,
    )["last_success_at"] == ""
    remote_sync_service.record_v2_run_success(consumer.engine, base_url=BASE_URL, synced_at=NOW)
    assert sync_console.home_summary(consumer.engine)["last_success_at"] == NOW
    assert sync_console.console_status(
        consumer.engine, media_store_ready=True, podcast_store_ready=True,
    )["last_success_at"] == NOW
