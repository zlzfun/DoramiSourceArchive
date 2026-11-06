"""运维首页聚合 /api/admin/home(issue #114)。

覆盖:逐日活跃账户(登录 ∪ 阅读、按天去重、排除游客与删号、逐日补零、窗口合计去重);
窗口内各源阅读(含游客、窗外不计、排行与 top 截断、源名富化);全量分析回填失败汇总
(最近一次成功之后才算未解决、进行中计数、只看 full_analysis);门控(全体管理员可见,
读者 403)。造数一律用相对日期。
"""
import datetime
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

TODAY = datetime.date.today()


def _day(offset: int) -> str:
    return (TODAY - datetime.timedelta(days=offset)).isoformat()


def _engine(tmp_path, name="home.db"):
    from storage.impl.db_storage import DatabaseStorage

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}").engine
    seed_default_accounts(engine)
    return engine


def _login_at(session, username, offset, hour=9):
    from models.db import LoginEventRecord

    session.add(LoginEventRecord(username=username, at=f"{_day(offset)}T{hour:02d}:00:00"))


def _read(session, username, source_id, offset, reads=1):
    from models.db import ReaderReadRecord

    session.add(ReaderReadRecord(day=_day(offset), username=username, source_id=source_id, reads=reads, updated_at=f"{_day(offset)}T10:00:00"))


_TICK = [0]


def _job(session, status, operation="full_analysis", failed=0):
    from models.db import TagRetagJobRecord

    _TICK[0] += 1  # 每条任务的终态时间依次推后一分钟
    stamp = f"{_day(0)}T00:{_TICK[0]:02d}:00+00:00"
    job = TagRetagJobRecord(
        operation=operation, status=status, taxonomy_version=1, affected_count=0, failed_count=failed,
        scope_json="{}", created_at=stamp, updated_at=stamp,
    )
    session.add(job)
    session.commit()
    return job


def test_active_accounts_union_dedup_and_exclusions(tmp_path):
    from services import admin_home

    engine = _engine(tmp_path)
    with Session(engine) as session:
        _login_at(session, "user", 1, 8)
        _login_at(session, "user", 1, 20)          # 同日两次登录只算一人
        _read(session, "user", "src-a", 1)          # 同日又阅读,仍是一人
        _read(session, "admin", "src-a", 3)         # 只阅读不登录也算活跃
        _login_at(session, "admin", 0)
        _read(session, "guest:v:abc", "src-a", 1)  # 游客身份不计
        _login_at(session, "deleted:old", 1)        # 删号墓碑不计
        _login_at(session, "user", 12)              # 窗外
        _login_at(session, "admin", -1)             # 明天(时钟漂移造的)不进窗口
        _read(session, "user", "src-a", 2, reads=0)  # reads=0 的行不算活跃
        session.commit()

        result = admin_home.active_accounts(session, days=7)

    series = result["series"]
    assert len(series) == 7 and series[0]["day"] == _day(6) and series[-1]["day"] == _day(0)
    by_day = {row["day"]: row["accounts"] for row in series}
    assert by_day[_day(1)] == 1
    assert by_day[_day(3)] == 1
    assert by_day[_day(0)] == 1
    assert by_day[_day(2)] == 0  # 逐日补零
    assert result["accounts"] == 2  # 窗口合计按人去重,不是逐日相加(1+1+1)


def test_reads_by_source_windowed_and_includes_guests(tmp_path):
    from services import admin_home

    engine = _engine(tmp_path)
    with Session(engine) as session:
        _read(session, "user", "src-a", 0, reads=3)
        _read(session, "guest:v:abc", "src-a", 1, reads=2)  # 与 /admin/content 同口径:游客阅读计入
        _read(session, "user", "src-b", 2, reads=4)
        _read(session, "user", "src-b", 40, reads=50)       # 窗外
        session.commit()
        assert admin_home.reads_by_source(session, days=7) == {"src-a": 5, "src-b": 4}
        assert admin_home.reads_by_source(session, days=90) == {"src-a": 5, "src-b": 54}


def test_backfill_summary_unresolved_after_last_success(tmp_path):
    from models.db import TaxonomyVersionRecord
    from services import admin_home

    engine = _engine(tmp_path)
    with Session(engine) as session:
        assert admin_home.backfill_summary(session) == {"active": 0, "unresolved_failed": 0, "latest": None}
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=f"{_day(0)}T00:00:00"))
        session.commit()
        _job(session, "failed")
        _job(session, "succeeded")           # 之前的失败视为已处理
        _job(session, "partial_failed", failed=4)
        _job(session, "failed", operation="retag_only")  # 只看 full_analysis
        summary = admin_home.backfill_summary(session)
        assert summary["unresolved_failed"] == 1
        assert summary["active"] == 0
        assert summary["latest"]["status"] == "partial_failed" and summary["latest"]["failed_count"] == 4
        _job(session, "running")
        summary = admin_home.backfill_summary(session)
        assert summary["active"] == 1 and summary["unresolved_failed"] == 1
        assert summary["latest"]["status"] == "running"


def test_backfill_summary_counts_retry_of_older_job_by_time(tmp_path):
    """「重试失败项」沿用原任务 id:#1 失败 → #2 成功 → 重试 #1 再失败,应算未解决(按终态时间而非 id,codex r1 #2)。"""
    from models.db import TaxonomyVersionRecord
    from services import admin_home

    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=f"{_day(0)}T00:00:00"))
        session.commit()
        first = _job(session, "partial_failed", failed=2)
        _job(session, "succeeded")
        assert admin_home.backfill_summary(session)["unresolved_failed"] == 0
        first.updated_at = f"{_day(0)}T23:59:00+00:00"  # 重试后再次结束于部分失败
        session.add(first)
        session.commit()
        summary = admin_home.backfill_summary(session)
        assert summary["unresolved_failed"] == 1
        assert summary["latest"]["id"] == first.id


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'app_home.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all")))
    seed_default_accounts(sink.engine)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_home_endpoint_shape_and_gating(monkeypatch, tmp_path):
    from services import accounts as accounts_service

    app_module = _setup_app(monkeypatch, tmp_path)
    with Session(app_module.db_sink.engine) as session:
        accounts_service.create_user(session, "ops", "ops-pass", "admin")
        for i, sid in enumerate(["src-a", "src-b", "src-c"]):
            _read(session, "user", sid, 1, reads=10 - i)
        _read(session, "user", "src-a", 60, reads=99)  # 7 天窗外
        session.commit()

    client = TestClient(app_module.app)
    assert _login(client, "user", "user").status_code == 200
    assert client.get("/api/admin/home").status_code == 403  # 读者不可见

    # 非根管理员同样可见:纯聚合、不含逐账户明细(与 account-growth 同类)。
    client = TestClient(app_module.app)
    assert _login(client, "ops", "ops-pass").status_code == 200
    resp = client.get("/api/admin/home?days=7&top=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert len(body["active"]["series"]) == 7
    assert body["active"]["series"][-1]["day"] == _day(0)
    assert body["active"]["accounts"] >= 2  # user 阅读 + ops 当天登录
    assert body["reads"]["total"] == 27
    assert [row["source_id"] for row in body["reads"]["by_source"]] == ["src-a", "src-b"]
    assert all(row["name"] for row in body["reads"]["by_source"])
    assert body["backfills"] == {"active": 0, "unresolved_failed": 0, "latest": None}
    # 节点同步磁贴(issue #261):没接入远端 = none。
    assert body["sync"]["configured"] is False and body["sync"]["status"] == "none"

    # days 越界收口,与其它运维端点一致。
    assert client.get("/api/admin/home?days=0").json()["days"] == 30
    assert client.get("/api/admin/home?days=9999").json()["days"] == 365
