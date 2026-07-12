"""GET /api/stats/daily 契约(A 每日聚合端点波)。

- runs 按 day×job_id×scope 聚合且状态分列;articles 按 day×source_id 计数;
- days 夹取 [1,90];窗口外数据不计;
- collector-gated:reader 账户 403。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import ArticleRecord, CollectionJobRunRecord, FetchRunRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _day(offset: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()


def _setup(monkeypatch, tmp_path):
    import api.app as app_module
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'stats.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for u, p, r in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(username=u, password_hash=accounts_service.hash_password(p),
                                   role=r, is_active=True, created_at=now, updated_at=now))
        # 今日:任务 7 一次成功 + 一次部分失败;临时一次失败。昨日:任务 7 一次成功。窗口外(100 天前)一次。
        runs = [
            (f"{_day(0)}T08:00:00", 7, "saved_job", "success", 12),
            (f"{_day(0)}T09:00:00", 7, "saved_job", "partial_failed", 3),
            (f"{_day(0)}T10:00:00", None, "ad_hoc", "failed", 0),
            (f"{_day(1)}T08:00:00", 7, "saved_job", "success", 5),
            (f"{_day(100)}T08:00:00", 7, "saved_job", "success", 99),
        ]
        for started, job_id, scope, status, saved in runs:
            session.add(CollectionJobRunRecord(
                job_id=job_id, run_scope=scope, status=status, started_at=started,
                saved_count=saved, node_count=1,
            ))
        # 无父单节点直跑(solo)×2:今日一成一败;有父的子运行不应计入 solo
        session.add(FetchRunRecord(fetcher_id="fx", status="success", started_at=f"{_day(0)}T11:00:00",
                                   saved_count=4, fetched_count=6, skipped_count=2))
        session.add(FetchRunRecord(fetcher_id="fy", status="failed", started_at=f"{_day(0)}T11:30:00"))
        session.add(FetchRunRecord(fetcher_id="fz", status="success", started_at=f"{_day(0)}T12:00:00",
                                   job_run_id=1, saved_count=99))
        for day, source, n in ((_day(0), "src_a", 2), (_day(1), "src_a", 1), (_day(0), "src_b", 3)):
            for i in range(n):
                session.add(ArticleRecord(
                    id=f"{source}-{day}-{i}", source_id=source, content_type="rss",
                    title="t", content="c", source_url="", publish_date=day,
                    fetched_date=f"{day}T12:00:00",
                ))
        session.commit()
    return app_module


def _login(client, u, p):
    assert client.post("/api/auth/login", json={"username": u, "password": p}).status_code == 200


def test_daily_stats_aggregates_runs_and_articles(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    client = TestClient(app_module.app)
    _login(client, "admin", "admin")
    body = client.get("/api/stats/daily?days=7").json()
    assert body["days"][-1] == _day(0) and len(body["days"]) == 7

    job_today = next(r for r in body["runs"] if r["day"] == _day(0) and r["job_id"] == 7)
    assert job_today == {"day": _day(0), "job_id": 7, "scope": "saved_job",
                         "runs": 2, "success": 1, "partial": 1, "failed": 0, "running": 0,
                         "saved": 15, "fetched": 0, "skipped": 0}
    adhoc_today = next(r for r in body["runs"] if r["day"] == _day(0) and r["scope"] == "ad_hoc")
    assert adhoc_today["failed"] == 1 and adhoc_today["job_id"] is None
    # 窗口外(100 天前)不计
    assert all(r["saved"] != 99 for r in body["runs"])

    solo_today = next(r for r in body["solo"] if r["day"] == _day(0))
    assert solo_today["runs"] == 2 and solo_today["success"] == 1 and solo_today["failed"] == 1
    assert solo_today["saved"] == 4  # 有父子运行(saved 99)不计入 solo

    counts = {(a["day"], a["source_id"]): a["count"] for a in body["articles"]}
    assert counts[(_day(0), "src_a")] == 2 and counts[(_day(0), "src_b")] == 3 and counts[(_day(1), "src_a")] == 1


def test_daily_stats_days_clamped_and_collector_gated(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    client = TestClient(app_module.app)
    _login(client, "admin", "admin")
    assert len(client.get("/api/stats/daily?days=999").json()["days"]) == 90
    assert len(client.get("/api/stats/daily?days=0").json()["days"]) == 1

    reader = TestClient(app_module.app)
    _login(reader, "user", "user")
    assert reader.get("/api/stats/daily").status_code == 403
