"""每日聚合统计 Router(collector,只读)——A 每日聚合端点波。

GET /api/stats/daily?days=N:一个端点供三处前端消费,把「已加载窗口口径」的
点阵/计数/趋势兑现为精确聚合(SQLite 侧 group-by,行数 = 天数×活跃维度,极小):
- runs:collection_job_runs 按 day×job_id×run_scope 聚合,状态分列计数
  (worst 由前端推:failed>0→bad、partial>0→warn、running>0→run、else ok);
- articles:articles 按 fetched_date 日×source_id 计数(台账总账条 7 日趋势、
  节点行 7 日收录 mini 柱)。

days 上限 90(与运维看板时间窗一致);日期键取 ISO 字符串前 10 位(本地时区写入口径)。
权限:/api/stats 已计入 COLLECTOR_API_PREFIXES,由中间件统一强制。
"""

from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, case, select

from api import deps
from models.db import ArticleRecord, CollectionJobRunRecord, FetchRunRecord

router = APIRouter(tags=["stats"])


@router.get("/api/stats/daily")
def get_daily_stats(days: int = 30, session: Session = Depends(deps.get_session)) -> Dict[str, Any]:
    days = max(1, min(int(days), 90))
    today = datetime.now().date()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    since = day_list[0]

    run_day = func.substr(CollectionJobRunRecord.started_at, 1, 10)
    run_rows = session.exec(
        select(
            run_day.label("day"),
            CollectionJobRunRecord.job_id,
            CollectionJobRunRecord.run_scope,
            func.count().label("runs"),
            func.sum(case((CollectionJobRunRecord.status == "success", 1), else_=0)).label("success"),
            func.sum(case((CollectionJobRunRecord.status == "partial_failed", 1), else_=0)).label("partial"),
            func.sum(case((CollectionJobRunRecord.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((CollectionJobRunRecord.status == "running", 1), else_=0)).label("running"),
            func.sum(CollectionJobRunRecord.saved_count).label("saved"),
            func.sum(CollectionJobRunRecord.fetched_count).label("fetched"),
            func.sum(CollectionJobRunRecord.skipped_count).label("skipped"),
        )
        .where(run_day >= since)
        .group_by("day", CollectionJobRunRecord.job_id, CollectionJobRunRecord.run_scope)
    ).all()

    # 无父的单节点直跑(不经任务级聚合的 fetch_runs)——运行页「临时」口径的另一半
    solo_day = func.substr(FetchRunRecord.started_at, 1, 10)
    solo_rows = session.exec(
        select(
            solo_day.label("day"),
            func.count().label("runs"),
            func.sum(case((FetchRunRecord.status == "success", 1), else_=0)).label("success"),
            func.sum(case((FetchRunRecord.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((FetchRunRecord.status == "running", 1), else_=0)).label("running"),
            func.sum(FetchRunRecord.saved_count).label("saved"),
            func.sum(FetchRunRecord.fetched_count).label("fetched"),
            func.sum(FetchRunRecord.skipped_count).label("skipped"),
        )
        .where(FetchRunRecord.job_run_id.is_(None))
        .where(solo_day >= since)
        .group_by("day")
    ).all()

    article_day = func.substr(ArticleRecord.fetched_date, 1, 10)
    article_rows = session.exec(
        select(
            article_day.label("day"),
            ArticleRecord.source_id,
            func.count().label("count"),
        )
        .where(article_day >= since)
        .group_by("day", ArticleRecord.source_id)
    ).all()

    return {
        "days": day_list,
        "runs": [
            {
                "day": r.day,
                "job_id": r.job_id,
                "scope": r.run_scope,
                "runs": int(r.runs or 0),
                "success": int(r.success or 0),
                "partial": int(r.partial or 0),
                "failed": int(r.failed or 0),
                "running": int(r.running or 0),
                "saved": int(r.saved or 0),
                "fetched": int(r.fetched or 0),
                "skipped": int(r.skipped or 0),
            }
            for r in run_rows
        ],
        "solo": [
            {
                "day": r.day,
                "runs": int(r.runs or 0),
                "success": int(r.success or 0),
                "failed": int(r.failed or 0),
                "running": int(r.running or 0),
                "saved": int(r.saved or 0),
                "fetched": int(r.fetched or 0),
                "skipped": int(r.skipped or 0),
            }
            for r in solo_rows
        ],
        "articles": [
            {"day": a.day, "source_id": a.source_id, "count": int(a.count or 0)}
            for a in article_rows
        ],
    }
