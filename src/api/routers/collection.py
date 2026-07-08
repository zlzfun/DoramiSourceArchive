"""采集调度 CRUD Router（collector）：采集任务。

阶段1 从 app.py 迁出的采集调度元数据读写端点（路径不变，collector 网关仍由中间件
统一强制）：
- /api/collection-jobs*      —— 可保存可调度的多节点采集任务 CRUD + 立即运行
- /api/collection-job-runs*  —— 任务级运行历史（聚合子运行）

（实体简化阶段 2：/api/node-groups* 与 /api/tasks* 已退役——节点组与旧版单节点
定时任务的存量数据由 Alembic 迁移内联/转换为采集任务；历史运行记录中的
run_scope=legacy_task 与 group_id 列保留供回溯。）

抓取核心 run_collection_items 与调度注册 load_tasks_to_scheduler 仍留守 app.py（与
APScheduler 启动编排同源），经 _app() 延迟动态调用。序列化与请求模型随迁入本文件，
经 app.py re-export 保持 api.app.X 兼容。规划 helper 复用 api.collection_planning。
数据访问经 deps.get_session()。
"""

import importlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from api.collection_planning import (
    apply_run_param_overrides,
    build_collection_job_items,
    normalize_fetcher_ids,
    test_run_overrides,
)
from api.textutils import _json_dumps, _json_loads, _now_iso
from models.db import (
    CollectionJobRecord,
    CollectionJobRunRecord,
    FetchRunRecord,
)
from services import jobs

router = APIRouter(tags=["collection"])


def _app():
    """延迟取 api.app（避免导入环；动态调用留守的 run_collection_items/load_tasks_to_scheduler）。"""
    return importlib.import_module("api.app")


# ==================== 序列化 ====================

def serialize_collection_job(record: CollectionJobRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "fetcher_ids": _json_loads(record.fetcher_ids_json, []),
        "params": _json_loads(record.params_json, {}),
        "per_fetcher_params": _json_loads(record.per_fetcher_params_json, {}),
        "cron_expr": record.cron_expr,
        "per_fetcher_cron": _json_loads(record.per_fetcher_cron_json, {}),
        "is_active": record.is_active,
        "downstream_policy": _json_loads(record.downstream_policy_json, {}),
        "legacy_task_id": record.legacy_task_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_collection_job_run(record: CollectionJobRunRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "group_id": record.group_id,
        "run_scope": record.run_scope,
        "trigger_type": record.trigger_type,
        "status": record.status,
        "name": record.name,
        "node_count": record.node_count,
        "child_run_ids": _json_loads(record.child_run_ids_json, []),
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "duration_ms": record.duration_ms,
        "fetched_count": record.fetched_count,
        "saved_count": record.saved_count,
        "skipped_count": record.skipped_count,
        "failed_count": record.failed_count,
        "error_message": record.error_message,
    }


# ==================== 请求模型 ====================

class CollectionJobCreate(BaseModel):
    name: str
    description: str = ""
    fetcher_ids: List[str] = PydanticField(default_factory=list)
    params: Dict[str, Any] = PydanticField(default_factory=dict)
    per_fetcher_params: Dict[str, Dict[str, Any]] = PydanticField(default_factory=dict)
    cron_expr: str = ""
    per_fetcher_cron: Dict[str, str] = PydanticField(default_factory=dict)
    is_active: bool = True
    downstream_policy: Dict[str, Any] = PydanticField(default_factory=dict)


class CollectionJobUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    fetcher_ids: Optional[List[str]] = None
    params: Optional[Dict[str, Any]] = None
    per_fetcher_params: Optional[Dict[str, Dict[str, Any]]] = None
    cron_expr: Optional[str] = None
    per_fetcher_cron: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None
    downstream_policy: Optional[Dict[str, Any]] = None


# ==================== 采集任务（collection jobs）====================

@router.get("/api/collection-jobs")
def get_collection_jobs(is_active: Optional[bool] = None, session: Session = Depends(deps.get_session)):
    query = select(CollectionJobRecord)
    if is_active is not None:
        query = query.where(CollectionJobRecord.is_active == is_active)
    query = query.order_by(CollectionJobRecord.name)
    return [serialize_collection_job(record) for record in session.exec(query).all()]


@router.post("/api/collection-jobs")
def create_collection_job(data: CollectionJobCreate, session: Session = Depends(deps.get_session)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="采集任务名称不能为空")
    if not normalize_fetcher_ids(data.fetcher_ids):
        raise HTTPException(status_code=400, detail="采集任务至少需要一个节点")
    now = _now_iso()
    record = CollectionJobRecord(
        name=name,
        description=data.description.strip(),
        fetcher_ids_json=_json_dumps(normalize_fetcher_ids(data.fetcher_ids)),
        params_json=_json_dumps(data.params),
        per_fetcher_params_json=_json_dumps(data.per_fetcher_params),
        cron_expr=data.cron_expr.strip(),
        per_fetcher_cron_json=_json_dumps(data.per_fetcher_cron),
        is_active=data.is_active,
        downstream_policy_json=_json_dumps(data.downstream_policy),
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    _app().load_tasks_to_scheduler()
    return serialize_collection_job(record)


@router.put("/api/collection-jobs/{job_id}")
def update_collection_job(job_id: int, data: CollectionJobUpdate, session: Session = Depends(deps.get_session)):
    record = session.get(CollectionJobRecord, job_id)
    if not record:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    update_data = data.dict(exclude_unset=True)
    if "name" in update_data:
        name = (update_data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="采集任务名称不能为空")
        record.name = name
    if "description" in update_data:
        record.description = (update_data["description"] or "").strip()
    if "fetcher_ids" in update_data:
        record.fetcher_ids_json = _json_dumps(normalize_fetcher_ids(update_data["fetcher_ids"]))
    if "params" in update_data:
        record.params_json = _json_dumps(update_data["params"])
    if "per_fetcher_params" in update_data:
        record.per_fetcher_params_json = _json_dumps(update_data["per_fetcher_params"])
    if "cron_expr" in update_data:
        record.cron_expr = (update_data["cron_expr"] or "").strip()
    if "per_fetcher_cron" in update_data:
        record.per_fetcher_cron_json = _json_dumps(update_data["per_fetcher_cron"])
    if "is_active" in update_data:
        record.is_active = update_data["is_active"]
    if "downstream_policy" in update_data:
        record.downstream_policy_json = _json_dumps(update_data["downstream_policy"])
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    _app().load_tasks_to_scheduler()
    return serialize_collection_job(record)


@router.delete("/api/collection-jobs/{job_id}")
def delete_collection_job(job_id: int, session: Session = Depends(deps.get_session)):
    record = session.get(CollectionJobRecord, job_id)
    if not record:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    session.delete(record)
    session.commit()
    _app().load_tasks_to_scheduler()
    return {"status": "success"}


@router.post("/api/collection-jobs/{job_id}/run")
async def run_collection_job_now(job_id: int, test_limit: Optional[int] = None, session: Session = Depends(deps.get_session)):
    """触发采集任务：提交持久化后台任务并立即返回 job_id（不再占满长请求）。

    校验/建节点同步完成（提前抛 4xx）；实际 run_collection_items 收进后台任务，前端轮询
    GET /api/jobs/{job_id} 取聚合结果，细粒度进度仍走 GET /api/fetch-runs/running-progress。
    """
    job = session.get(CollectionJobRecord, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    items = build_collection_job_items(job)
    if not items:
        raise HTTPException(status_code=400, detail="采集任务没有可执行节点")
    job_name = job.name
    items = apply_run_param_overrides(items, test_run_overrides(test_limit))

    async def _work(bg) -> Dict[str, Any]:
        return await _app().run_collection_items(
            items,
            name=job_name,
            trigger_type="manual",
            job_id=job_id,
            run_scope="saved_job",
        )

    bg_job = jobs.launch(
        deps.get_db_sink().engine, "collection_job_run", _work,
        payload={"job_id": job_id, "test_limit": test_limit},
    )
    return {"status": "accepted", "job_id": bg_job.id}


@router.get("/api/collection-job-runs")
def get_collection_job_runs(
        job_id: Optional[int] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        run_scope: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    query = select(CollectionJobRunRecord)
    if job_id is not None:
        query = query.where(CollectionJobRunRecord.job_id == job_id)
    if status:
        query = query.where(CollectionJobRunRecord.status == status)
    if trigger_type:
        query = query.where(CollectionJobRunRecord.trigger_type == trigger_type)
    if run_scope:
        query = query.where(CollectionJobRunRecord.run_scope == run_scope)
    query = query.order_by(CollectionJobRunRecord.started_at.desc()).offset(skip).limit(limit)
    return [serialize_collection_job_run(record) for record in session.exec(query).all()]


@router.get("/api/collection-job-runs/{job_run_id}")
def get_collection_job_run(job_run_id: int, session: Session = Depends(deps.get_session)):
    record = session.get(CollectionJobRunRecord, job_run_id)
    if not record:
        raise HTTPException(status_code=404, detail="采集运行记录不存在")
    child_run_ids = _json_loads(record.child_run_ids_json, [])
    child_runs = []
    if child_run_ids:
        child_runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.id.in_(child_run_ids))).all()
    return {**serialize_collection_job_run(record), "child_runs": child_runs}
