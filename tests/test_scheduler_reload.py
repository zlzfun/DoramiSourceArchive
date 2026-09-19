"""调度重载差量同步(issue #82 PR-0)。

历史 ``load_tasks_to_scheduler()`` 第一句 ``scheduler.remove_all_jobs()`` 再整体重建采集任务,而采集任务的
创建 / 更新 / 删除端点每次都调它——留存清理、播客 ASR worker、远程同步、用户自定源刷新只在 lifespan
「调度器新鲜启动」分支注册,编辑一次任务就全部消失到下次重启。本文件锁定新语义:

- 其它命名空间的任务在任意次数重载后原样保留、不重复;
- ``collection_job_*`` 按库里 is_active 任务差量增 / 改 / 删,停用、删除、cron 改坏都会摘除注册;
- 日报 job 随启用开关幂等出现 / 消失。
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.app as app_module  # noqa: E402
from models.db import CollectionJobRecord  # noqa: E402
from services import daily_brief as daily_brief_service  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

# lifespan 新鲜启动分支才注册、且各自带 reload_* 的任务——重载采集任务不得碰它们。
OTHER_JOB_IDS = (
    "retention_cleanup",
    "podcast_asr_worker",
    "remote_sync",
    "user_rss_refresh",
    "storage_maintenance",
)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'sched.db'}")
    # 真 APScheduler,start(paused=True):jobstore 生效、replace_existing 真替换,但不派发任务——
    # 未 start 的调度器只把 add_job 追加进 pending 列表,同 id 会重复,与生产(运行态)不符。
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.start(paused=True)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    for job_id in OTHER_JOB_IDS:
        scheduler.add_job(lambda: None, "interval", minutes=5, id=job_id)
    try:
        yield sink, scheduler
    finally:
        scheduler.shutdown(wait=False)


def _make_job(session: Session, cron: str, *, active: bool = True, name: str = "每日采集") -> int:
    now = dt.datetime.now().isoformat()
    job = CollectionJobRecord(
        name=name,
        fetcher_ids_json='["rss_the_decoder"]',
        cron_expr=cron,
        is_active=active,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job.id


def _ids(scheduler) -> list[str]:
    return sorted(str(job.id) for job in scheduler.get_jobs())


def _collection_ids(scheduler) -> list[str]:
    return [job_id for job_id in _ids(scheduler) if job_id.startswith(app_module.COLLECTION_JOB_ID_PREFIX)]


def test_reload_keeps_jobs_of_other_namespaces(sandbox):
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        job_id = _make_job(session, "0 8 * * *")
    for _ in range(3):
        app_module.load_tasks_to_scheduler()
        ids = _ids(scheduler)
        for other in OTHER_JOB_IDS:
            assert ids.count(other) == 1, f"{other} 被重载删掉或重复注册"
        assert _collection_ids(scheduler) == [f"collection_job_{job_id}"]
        # 与采集任务同函数注册的固定任务也只有一份
        for fixed in ("article_analysis", "taxonomy_retag", app_module.PODCAST_LANDING_JOB_ID,
                      "personal_digest_schedule", "personal_digest_pending"):
            assert ids.count(fixed) == 1


def test_reload_tracks_cron_changes_without_duplicates(sandbox):
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        job_id = _make_job(session, "0 8 * * *")
    app_module.load_tasks_to_scheduler()
    registered = scheduler.get_job(f"collection_job_{job_id}")
    assert registered.args == (job_id,)
    assert "hour='8'" in str(registered.trigger)
    assert registered.misfire_grace_time == app_module.CRON_MISFIRE_GRACE_SECONDS

    with Session(sink.engine) as session:
        record = session.get(CollectionJobRecord, job_id)
        record.cron_expr = "30 9 * * *"
        session.add(record)
        session.commit()
    app_module.load_tasks_to_scheduler()
    assert _collection_ids(scheduler) == [f"collection_job_{job_id}"]
    assert "hour='9'" in str(scheduler.get_job(f"collection_job_{job_id}").trigger)


def test_reload_removes_deactivated_deleted_and_stale_entries(sandbox):
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        keep_id = _make_job(session, "0 7 * * *", name="保留")
        off_id = _make_job(session, "0 8 * * *", name="待停用")
        gone_id = _make_job(session, "0 9 * * *", name="待删除")
        no_cron_id = _make_job(session, "", name="无 cron 不调度")
    # 调度器里残留一个库里已不存在的任务注册(比如上一进程留下的)
    scheduler.add_job(lambda: None, "interval", minutes=5, id="collection_job_999")
    app_module.load_tasks_to_scheduler()
    assert _collection_ids(scheduler) == sorted(
        [f"collection_job_{keep_id}", f"collection_job_{off_id}", f"collection_job_{gone_id}"]
    )
    assert f"collection_job_{no_cron_id}" not in _ids(scheduler)

    with Session(sink.engine) as session:
        off = session.get(CollectionJobRecord, off_id)
        off.is_active = False
        session.add(off)
        session.delete(session.get(CollectionJobRecord, gone_id))
        session.commit()
    app_module.load_tasks_to_scheduler()
    assert _collection_ids(scheduler) == [f"collection_job_{keep_id}"]
    for other in OTHER_JOB_IDS:
        assert scheduler.get_job(other) is not None


@pytest.mark.parametrize("broken", ["every-morning", "61 8 * * *", "0 25 * * *", "0 8 * nope *"])
def test_broken_cron_drops_previous_registration_without_raising(sandbox, broken):
    """非 5 段与五段字段越界一视同仁:不抛、不注册、摘除旧注册(历史脏行的容错路径)。"""
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        job_id = _make_job(session, "0 8 * * *")
        good_id = _make_job(session, "0 7 * * *", name="好的")
    app_module.load_tasks_to_scheduler()
    assert scheduler.get_job(f"collection_job_{job_id}") is not None

    with Session(sink.engine) as session:
        record = session.get(CollectionJobRecord, job_id)
        record.cron_expr = broken
        session.add(record)
        session.commit()
    assert app_module.add_cron_job("probe", lambda: None, broken, []) is False
    app_module.load_tasks_to_scheduler()  # 不抛
    assert scheduler.get_job(f"collection_job_{job_id}") is None
    assert scheduler.get_job("probe") is None
    assert scheduler.get_job(f"collection_job_{good_id}") is not None
    for other in OTHER_JOB_IDS:
        assert scheduler.get_job(other) is not None


def _snapshot(scheduler, job_id):
    job = scheduler.get_job(job_id)
    assert job is not None, job_id
    return (str(job.trigger), tuple(job.args or ()), job.next_run_time)


def test_unrelated_reload_does_not_reschedule_untouched_jobs(sandbox):
    """replace_existing 会把 next_run_time 重置为现在起算;未变化的任务必须原样保留(检视 F2)。"""
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        keep_id = _make_job(session, "0 8 * * *", name="未改动")
    app_module.load_tasks_to_scheduler()
    watched = ("article_analysis", "taxonomy_retag", app_module.PODCAST_LANDING_JOB_ID,
               "personal_digest_pending", "personal_digest_schedule", f"collection_job_{keep_id}", *OTHER_JOB_IDS)
    before = {job_id: _snapshot(scheduler, job_id) for job_id in watched}

    with Session(sink.engine) as session:
        new_id = _make_job(session, "0 9 * * *", name="新任务")
    app_module.load_tasks_to_scheduler()
    assert scheduler.get_job(f"collection_job_{new_id}") is not None
    assert {job_id: _snapshot(scheduler, job_id) for job_id in watched} == before

    with Session(sink.engine) as session:
        session.delete(session.get(CollectionJobRecord, new_id))
        session.commit()
    app_module.load_tasks_to_scheduler()
    assert scheduler.get_job(f"collection_job_{new_id}") is None
    assert {job_id: _snapshot(scheduler, job_id) for job_id in watched} == before


def test_load_before_start_then_start_registers_each_job_once(monkeypatch, tmp_path):
    """lifespan 首启路径:未 start 时装载(pending)再 start,每个任务恰一份。"""
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'boot.db'}")
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    with Session(sink.engine) as session:
        job_id = _make_job(session, "0 8 * * *")
    app_module.load_tasks_to_scheduler()
    app_module.load_tasks_to_scheduler()  # 启动前重复装载也不该堆出重复的 pending 项
    scheduler.start(paused=True)
    try:
        ids = _ids(scheduler)
        for expected in (f"collection_job_{job_id}", "article_analysis", "taxonomy_retag",
                         app_module.PODCAST_LANDING_JOB_ID, "personal_digest_schedule", "personal_digest_pending"):
            assert ids.count(expected) == 1, (expected, ids)
    finally:
        scheduler.shutdown(wait=False)


def test_daily_brief_job_follows_enable_flag(sandbox):
    sink, scheduler = sandbox
    with Session(sink.engine) as session:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_ENABLED, "true")
        daily_brief_service.set_setting(session, daily_brief_service.KEY_CRON, "30 8 * * *")
    app_module.load_tasks_to_scheduler()
    assert scheduler.get_job("daily_brief") is not None
    with Session(sink.engine) as session:
        daily_brief_service.set_setting(session, daily_brief_service.KEY_ENABLED, "false")
    app_module.load_tasks_to_scheduler()
    assert scheduler.get_job("daily_brief") is None
    for other in OTHER_JOB_IDS:
        assert scheduler.get_job(other) is not None
