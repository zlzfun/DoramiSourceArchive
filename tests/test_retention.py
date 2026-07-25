"""明细表滚动窗清理服务回归测试（services/retention.py）。

覆盖：逐表窗口删除的正确性（窗口内保留 / 窗口外删除，含边界日）、空表无害、
返回汇总的结构与逐表键齐全。
"""
import datetime
import os
import sys

from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AdminAuditRecord,
    AiUsageRecord,
    FetchRunRecord,
    LoginEventRecord,
    ReaderReadRecord,
)
from services import retention  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _engine(tmp_path, name="retention.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}").engine


def _day(offset_days: int) -> str:
    """今天回退 offset_days 的 YYYY-MM-DD。"""
    return (datetime.date.today() - datetime.timedelta(days=offset_days)).isoformat()


def _iso(offset_days: int) -> str:
    """今天回退 offset_days 的 ISO datetime 串（明细事件表用）。"""
    d = datetime.date.today() - datetime.timedelta(days=offset_days)
    return datetime.datetime(d.year, d.month, d.day, 12, 0, 0).isoformat()


def _labels():
    return {t.label for t in retention._TABLES}


def test_returns_summary_structure_on_empty_db(tmp_path):
    """空库：所有表删 0 行，汇总键齐全、值全 0。"""
    engine = _engine(tmp_path)
    result = retention.run_retention_cleanup(engine)
    assert set(result.keys()) == _labels()
    assert all(v == 0 for v in result.values())


def test_fetch_runs_window(tmp_path):
    """fetch_runs 保留窗 180 天：窗内（含边界）保留、窗外删除。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(FetchRunRecord(fetcher_id="f", started_at=_iso(10)))    # 窗内
        session.add(FetchRunRecord(fetcher_id="f", started_at=_iso(179)))   # 边界内（保留最近 180 天含今天）
        session.add(FetchRunRecord(fetcher_id="f", started_at=_iso(365)))   # 窗外
        session.add(FetchRunRecord(fetcher_id="f", started_at=_iso(200)))   # 窗外
        session.commit()

    result = retention.run_retention_cleanup(engine)
    assert result["fetch_runs"] == 2

    with Session(engine) as session:
        remaining = sorted(r.started_at for r in session.exec(select(FetchRunRecord)).all())
    assert remaining == sorted([_iso(10), _iso(179)])


def test_login_and_audit_window_365(tmp_path):
    """login_events / admin_audit_logs 保留窗 365 天。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(LoginEventRecord(username="u", at=_iso(100)))   # 窗内
        session.add(LoginEventRecord(username="u", at=_iso(400)))   # 窗外
        session.add(AdminAuditRecord(username="a", method="POST", path="/x",
                                     status_code=200, at=_iso(300)))  # 窗内
        session.add(AdminAuditRecord(username="a", method="POST", path="/y",
                                     status_code=200, at=_iso(500)))  # 窗外
        session.commit()

    result = retention.run_retention_cleanup(engine)
    assert result["login_events"] == 1
    assert result["admin_audit_logs"] == 1

    with Session(engine) as session:
        assert [r.at for r in session.exec(select(LoginEventRecord)).all()] == [_iso(100)]
        assert [r.at for r in session.exec(select(AdminAuditRecord)).all()] == [_iso(300)]


def test_aggregate_tables_window_730(tmp_path):
    """ai_usage / reader_reads 按天聚合，保留窗 730 天（窗更长）。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(AiUsageRecord(day=_day(400), username="u", purpose="ask", updated_at=_iso(400)))   # 窗内
        session.add(AiUsageRecord(day=_day(800), username="u", purpose="ask", updated_at=_iso(800)))   # 窗外
        session.add(ReaderReadRecord(day=_day(500), username="u", source_id="s", updated_at=_iso(500)))  # 窗内
        session.add(ReaderReadRecord(day=_day(900), username="u", source_id="s", updated_at=_iso(900)))  # 窗外
        session.commit()

    result = retention.run_retention_cleanup(engine)
    assert result["ai_usage"] == 1
    assert result["reader_reads"] == 1

    with Session(engine) as session:
        assert [r.day for r in session.exec(select(AiUsageRecord)).all()] == [_day(400)]
        assert [r.day for r in session.exec(select(ReaderReadRecord)).all()] == [_day(500)]


def test_all_within_window_deletes_nothing(tmp_path):
    """全部行都在窗口内：一行不删。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(FetchRunRecord(fetcher_id="f", started_at=_iso(1)))
        session.add(LoginEventRecord(username="u", at=_iso(1)))
        session.add(AiUsageRecord(day=_day(1), username="u", purpose="ask", updated_at=_iso(1)))
        session.commit()

    result = retention.run_retention_cleanup(engine)
    assert result["fetch_runs"] == 0
    assert result["login_events"] == 0
    assert result["ai_usage"] == 0
    with Session(engine) as session:
        assert len(session.exec(select(FetchRunRecord)).all()) == 1
