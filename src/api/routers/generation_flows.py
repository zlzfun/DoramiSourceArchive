"""生成流程 Router(collector / admin;issue #72)。

管理面「生成流程」页用:一次取全所有流程的现状,改开关 / cron / 配置,手动触发,轮询进度,翻往期。
外壳在 ``services.generation_flows``;公共日报的 ``/api/daily-brief/*`` 原样保留,这里不替代它。

- collector 网关由 app.py 中间件统一强制(``COLLECTOR_API_PREFIXES`` 含 ``/api/generation-flows``);
- 写操作进管理审计(``admin_audit.AUDIT_PATH_PREFIXES``);
- 调度重排、当前用户名等仍留在 app.py 的编排 helper 经 ``_app()`` 延迟取(避免导入环,兼容测试 monkeypatch)。
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session

from api import deps
from services import generation_flows as flows_service

router = APIRouter(prefix="/api/generation-flows", tags=["generation-flows"])


def _app():
    return importlib.import_module("api.app")


def _flow_or_404(flow_id: str) -> flows_service.Flow:
    flow = flows_service.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="没有这个流程")
    return flow


def _next_run_at(flow_id: str) -> Optional[str]:
    """调度里这个流程下一次触发的时刻;没登记(关着,或本进程不跑调度)返回 None。"""
    try:
        job = _app().scheduler.get_job(flow_id)
    except Exception:  # noqa: BLE001 取不到调度状态不该让整页失败
        return None
    moment = getattr(job, "next_run_time", None)
    return moment.isoformat() if moment is not None else None


def _describe(session: Session, flow: flows_service.Flow) -> Dict[str, Any]:
    return flows_service.describe(session, flow, next_run_at=_next_run_at(flow.id))


class FlowUpdate(BaseModel):
    enabled: Optional[bool] = None
    cron: Optional[str] = None
    # 本流程自己的配置(只含要改的键);有哪些键看列表接口里的 knobs / config
    config: Optional[Dict[str, Any]] = None


@router.get("")
def list_generation_flows(session: Session = Depends(deps.get_session)):
    return {"flows": [_describe(session, flow) for flow in flows_service.all_flows()]}


@router.post("/{flow_id}/config")
def update_generation_flow(flow_id: str, payload: FlowUpdate, session: Session = Depends(deps.get_session)):
    flow = _flow_or_404(flow_id)
    try:
        flow.update(session, enabled=payload.enabled, cron=payload.cron, config=payload.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    # 仅在 collector 运行角色下有调度引擎
    app = _app()
    if app.runtime_collector_enabled():
        app.reload_generation_flow_schedule(flow.id)
    return _describe(session, flow)


@router.post("/{flow_id}/generate")
async def generate_generation_flow(flow_id: str, request: Request):
    """手动触发:提交持久化后台任务并立即返回 job_id;终态轮询 ``GET /api/jobs/{job_id}``,阶段进度轮询本路由的 progress。"""
    flow = _flow_or_404(flow_id)
    db_sink = deps.get_db_sink()
    triggered_by = _app().current_username(request) or None

    async def _work(job) -> Dict[str, Any]:
        return await flow.generate(storage=db_sink, trigger="manual", triggered_by=triggered_by)

    from services import jobs

    job = jobs.launch(db_sink.engine, flow.id, _work, created_by=triggered_by, payload={"trigger": "manual"})
    return {"status": "accepted", "job_id": job.id}


@router.get("/{flow_id}/progress")
def get_generation_flow_progress(flow_id: str):
    return _flow_or_404(flow_id).progress()


@router.get("/{flow_id}/periods")
def list_generation_flow_periods(
    flow_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=flows_service.PERIOD_PAGE_SIZE_MAX),
    session: Session = Depends(deps.get_session),
):
    return flows_service.list_periods(session, _flow_or_404(flow_id), page=page, page_size=page_size)
