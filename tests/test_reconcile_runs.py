"""启动自愈：进程重启后残留的「运行中」记录应被标记为失败。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import SQLModel, Session, create_engine, select

import api.app as app_module
from models.db import FetchRunRecord, CollectionJobRunRecord, SourceStateRecord


def _isolated_engine(monkeypatch):
    """用内存 SQLite 替换真实 db_sink.engine，避免污染开发库。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(app_module.db_sink, "engine", engine)
    return engine


def test_reconcile_marks_orphaned_running_records_failed(monkeypatch):
    engine = _isolated_engine(monkeypatch)
    with Session(engine) as s:
        s.add(FetchRunRecord(id=1, fetcher_id="web_jiqizhixin", status="running",
                             started_at="2026-06-16T14:42:36.060255"))
        s.add(FetchRunRecord(id=2, fetcher_id="web_qbitai", status="success",
                             started_at="2026-06-16T10:00:00", ended_at="2026-06-16T10:01:00"))
        s.add(CollectionJobRunRecord(id=9, run_scope="ad_hoc", status="running",
                                     name="临时批量抓取", node_count=1,
                                     started_at="2026-06-16T14:42:36.060255"))
        s.add(SourceStateRecord(source_id="web_jiqizhixin", fetcher_id="web_jiqizhixin",
                                status="running", updated_at="2026-06-16T14:42:36"))
        s.commit()

    counts = app_module.reconcile_orphaned_runs()

    assert counts == {"fetch_runs": 1, "job_runs": 1, "source_states": 1}
    with Session(engine) as s:
        run = s.get(FetchRunRecord, 1)
        assert run.status == "failed"
        assert run.ended_at and run.duration_ms and run.duration_ms > 0
        assert "中断" in run.error_message
        # 已收尾的记录不受影响
        assert s.get(FetchRunRecord, 2).status == "success"
        assert s.get(CollectionJobRunRecord, 9).status == "failed"
        assert s.get(SourceStateRecord, "web_jiqizhixin").status == "unknown"


def test_reconcile_is_idempotent_noop_when_clean(monkeypatch):
    engine = _isolated_engine(monkeypatch)
    with Session(engine) as s:
        s.add(FetchRunRecord(id=1, fetcher_id="web_qbitai", status="success",
                             started_at="2026-06-16T10:00:00"))
        s.commit()

    counts = app_module.reconcile_orphaned_runs()

    assert counts == {"fetch_runs": 0, "job_runs": 0, "source_states": 0}
    with Session(engine) as s:
        assert s.get(FetchRunRecord, 1).status == "success"
