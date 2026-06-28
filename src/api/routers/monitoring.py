"""采集监控 Router（只读）：源健康 / 源状态 / 抓取运行历史 + 实时进度。

阶段1 从 app.py 迁出的只读可观测端点（路径不变，collector 网关仍由中间件统一强制）：
- GET /api/source-health              —— 每源健康汇总（优先 SourceStateRecord，回退 FetchRunRecord 聚合）
- GET /api/source-states             —— 原始 SourceStateRecord 行（可按 status/fetcher_id 过滤）
- GET /api/fetch-runs/running-progress —— 内存态每 fetcher 实时进度
- GET /api/fetch-runs                 —— 分页抓取运行历史
- GET /api/fetch-runs/{run_id}        —— 单条运行详情

健康汇总 helper（derive_health_status / build_fetcher_health[_from_state]）随迁入本文件，
经 app.py re-export 保持 api.app.X 兼容。数据访问经 deps.get_session()。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from api import deps
from fetchers.registry import fetcher_registry
from models.db import ArticleRecord, FetchRunRecord, SourceStateRecord
from pipeline.progress import get_all_progress

router = APIRouter(tags=["monitoring"])


# ==================== 健康汇总 helper ====================

def derive_health_status(latest_run: Optional[FetchRunRecord], consecutive_failures: int) -> str:
    if not latest_run:
        return "never_run"
    if latest_run.status == "running":
        return "running"
    if consecutive_failures > 0:
        return "failing"
    if latest_run.status == "success":
        return "healthy"
    return "unknown"


def build_fetcher_health(fetcher_metadata: Dict[str, Any], runs: List[FetchRunRecord]) -> Dict[str, Any]:
    ordered_runs = sorted(runs, key=lambda run: run.started_at or "", reverse=True)
    latest_run = ordered_runs[0] if ordered_runs else None
    success_runs = [run for run in ordered_runs if run.status == "success"]
    failed_runs = [run for run in ordered_runs if run.status == "failed"]
    running_runs = [run for run in ordered_runs if run.status == "running"]

    consecutive_failures = 0
    for run in ordered_runs:
        if run.status == "failed":
            consecutive_failures += 1
        elif run.status == "success":
            break

    latest_success = success_runs[0] if success_runs else None
    latest_failure = failed_runs[0] if failed_runs else None

    return {
        "fetcher_id": fetcher_metadata["id"],
        "source_id": fetcher_metadata["id"],
        "name": fetcher_metadata["name"],
        "category": fetcher_metadata.get("category", "general"),
        "content_type": fetcher_metadata.get("content_type", ""),
        "health_status": derive_health_status(latest_run, consecutive_failures),
        "latest_run_status": latest_run.status if latest_run else None,
        "latest_run_at": latest_run.started_at if latest_run else None,
        "latest_success_at": latest_success.started_at if latest_success else None,
        "latest_failure_at": latest_failure.started_at if latest_failure else None,
        "latest_error_message": latest_run.error_message if latest_run and latest_run.status == "failed" else None,
        "consecutive_failures": consecutive_failures,
        "total_runs": len(ordered_runs),
        "success_runs": len(success_runs),
        "failed_runs": len(failed_runs),
        "running_runs": len(running_runs),
        "latest_fetched_count": latest_run.fetched_count if latest_run else 0,
        "latest_saved_count": latest_run.saved_count if latest_run else 0,
        "latest_skipped_count": latest_run.skipped_count if latest_run else 0,
    }


def build_fetcher_health_from_state(fetcher_metadata: Dict[str, Any], state: SourceStateRecord) -> Dict[str, Any]:
    return {
        "fetcher_id": fetcher_metadata["id"],
        "source_id": state.source_id,
        "name": fetcher_metadata["name"],
        "category": fetcher_metadata.get("category", "general"),
        "content_type": state.content_type or fetcher_metadata.get("content_type", ""),
        "health_status": state.status,
        "latest_run_status": "success" if state.status == "healthy" else "failed" if state.status == "failing" else state.status,
        "latest_run_at": state.last_started_at,
        "latest_success_at": state.last_success_at,
        "latest_failure_at": state.last_failure_at,
        "latest_error_type": state.latest_error_type,
        "latest_error_message": state.latest_error_message,
        "last_cursor_value": state.last_cursor_value,
        "last_cursor_date": state.last_cursor_date,
        "last_content_id": state.last_content_id,
        "consecutive_failures": state.consecutive_failures,
        "total_runs": state.total_runs,
        "success_runs": state.success_runs,
        "failed_runs": state.failed_runs,
        "running_runs": 1 if state.status == "running" else 0,
        "latest_fetched_count": state.latest_fetched_count,
        "latest_saved_count": state.latest_saved_count,
        "latest_skipped_count": state.latest_skipped_count,
    }


# ==================== 端点 ====================

@router.get("/api/source-health")
def get_source_health(session: Session = Depends(deps.get_session)):
    fetchers = fetcher_registry.get_all_metadata()
    fetcher_ids = [fetcher["id"] for fetcher in fetchers]

    runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.fetcher_id.in_(fetcher_ids))).all()
    states = session.exec(select(SourceStateRecord).where(SourceStateRecord.source_id.in_(fetcher_ids))).all()
    article_counts = session.exec(
        select(ArticleRecord.source_id, func.count(ArticleRecord.id))
        .where(ArticleRecord.source_id.in_(fetcher_ids))
        .group_by(ArticleRecord.source_id)
    ).all()

    article_count_by_source = {source_id: count for source_id, count in article_counts}
    states_by_source = {state.source_id: state for state in states}
    runs_by_fetcher: Dict[str, List[FetchRunRecord]] = {fetcher_id: [] for fetcher_id in fetcher_ids}
    for run in runs:
        runs_by_fetcher.setdefault(run.fetcher_id, []).append(run)

    health_items = []
    for fetcher in fetchers:
        item = (
            build_fetcher_health_from_state(fetcher, states_by_source[fetcher["id"]])
            if fetcher["id"] in states_by_source
            else build_fetcher_health(fetcher, runs_by_fetcher.get(fetcher["id"], []))
        )
        item["total_articles"] = article_count_by_source.get(fetcher["id"], 0)
        health_items.append(item)
    return sorted(health_items, key=lambda item: (item["category"], item["name"]))


@router.get("/api/source-states")
def get_source_states(
        status: Optional[str] = None,
        fetcher_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    query = select(SourceStateRecord)
    if status:
        query = query.where(SourceStateRecord.status == status)
    if fetcher_id:
        query = query.where(SourceStateRecord.fetcher_id == fetcher_id)
    query = query.order_by(SourceStateRecord.updated_at.desc()).offset(skip).limit(limit)
    return session.exec(query).all()


@router.get("/api/fetch-runs/running-progress")
def get_running_progress():
    return get_all_progress()


@router.get("/api/fetch-runs")
def get_fetch_runs(
        fetcher_id: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    query = select(FetchRunRecord)
    if fetcher_id:
        query = query.where(FetchRunRecord.fetcher_id == fetcher_id)
    if job_id is not None:
        query = query.where(FetchRunRecord.job_id == job_id)
    if job_run_id is not None:
        query = query.where(FetchRunRecord.job_run_id == job_run_id)
    if run_scope:
        query = query.where(FetchRunRecord.run_scope == run_scope)
    if status:
        query = query.where(FetchRunRecord.status == status)
    if trigger_type:
        query = query.where(FetchRunRecord.trigger_type == trigger_type)
    query = query.order_by(FetchRunRecord.started_at.desc()).offset(skip).limit(limit)
    return session.exec(query).all()


@router.get("/api/fetch-runs/{run_id}")
def get_fetch_run(run_id: int, session: Session = Depends(deps.get_session)):
    run = session.get(FetchRunRecord, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="抓取运行记录不存在")
    return run
