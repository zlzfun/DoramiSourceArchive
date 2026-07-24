"""远程内容同步 Router(v3.18 互通波):接收方管理员从另一个存量后端拉取归档。

复用归档同步契约 articles-jsonl-v1(发送方零改动):登录远端 → 分页拉
GET /api/archive/export/articles.jsonl → 本地 import_archive_sync_jsonl 幂等导入。
端点全落 /api/admin/remote-sync/*,中间件 admin 前缀自动门控(改写整库,admin-only)。
核心实现在 services/remote_sync.py;拉取跑在持久化后台 job(services/jobs.py),
凭据只进任务内存,job payload 与 KV 游标绝不含密码。
设计见 docs/engage-sync-wave-plan.md。
"""

import importlib
from typing import Any, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import deps
from api.textutils import _now_iso
from services import jobs
from services import remote_sync as remote_sync_service
from services.remote_sync import REMOTE_SYNC_JOB_TYPE, RemoteSyncError

router = APIRouter(tags=["remote-sync"])


def _app():
    """延迟取 api.app(避免导入环 + 兼容测试 monkeypatch)。"""
    return importlib.import_module("api.app")


class RemoteSyncCredentials(BaseModel):
    base_url: str = ""
    username: str = ""
    password: str = ""


class RemoteSyncStartParams(RemoteSyncCredentials):
    # 增量起点(fetched_date ISO 前缀);空 = 全量。source_ids 可选限定来源。
    fetched_date_start: Optional[str] = None
    source_ids: Optional[List[str]] = None
    page_size: int = remote_sync_service.DEFAULT_PAGE_SIZE


class RemoteSyncScheduleParams(BaseModel):
    enabled: bool = False
    cron: str = remote_sync_service._SCHEDULE_DEFAULT_CRON
    base_url: str = ""
    username: str = ""
    # 空串 = 保留已存密码(只写不回显范式);非空则覆盖。
    password: str = ""
    source_ids: List[str] = []


def _validated_credentials(params: RemoteSyncCredentials) -> Dict[str, str]:
    base_url = remote_sync_service.normalize_base_url(params.base_url)
    username = (params.username or "").strip()
    if not username or not params.password:
        raise HTTPException(status_code=400, detail="远端管理员账号与密码不能为空")
    return {"base_url": base_url, "username": username, "password": params.password}


def launch_remote_sync_job(
    engine,
    *,
    base_url: str,
    username: str,
    password: str,
    fetched_date_start: Optional[str] = None,
    source_ids: Optional[List[str]] = None,
    page_size: int = remote_sync_service.DEFAULT_PAGE_SIZE,
    created_by: str = "",
) -> jobs.Job:
    """提交远程拉取后台 job(手动 start 端点与定时任务共用)。

    凭据只进任务内存;`jobs.launch` 的 payload 快照绝不含密码。成功后落 KV 游标。
    """
    archive_sync_router = importlib.import_module("api.routers.archive_sync")

    async def _work(job: jobs.Job) -> Dict[str, Any]:
        result = await remote_sync_service.run_pull(
            base_url=base_url,
            username=username,
            password=password,
            fetched_date_start=fetched_date_start,
            source_ids=source_ids,
            page_size=page_size,
            import_fn=archive_sync_router.import_archive_sync_jsonl,
            on_total=job.set_total,
            on_advance=job.advance,
        )
        remote_sync_service.record_sync_success(engine, result, synced_at=_now_iso())
        return result

    return jobs.launch(
        engine,
        REMOTE_SYNC_JOB_TYPE,
        _work,
        created_by=created_by,
        payload={
            "base_url": base_url,
            "username": username,
            "fetched_date_start": fetched_date_start or "",
            "source_ids": source_ids or [],
        },
    )


@router.post("/api/admin/remote-sync/test")
async def test_remote_sync(params: RemoteSyncCredentials):
    """「测试连接」:登录远端 → 版本/契约/总量探针。凭据不落任何存储。"""
    try:
        creds = _validated_credentials(params)
        return await remote_sync_service.probe(
            creds["base_url"], creds["username"], creds["password"]
        )
    except RemoteSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/admin/remote-sync/start")
async def start_remote_sync(params: RemoteSyncStartParams, request: Request):
    """提交远程拉取后台任务,立即返回 job_id(前端轮询 GET /api/jobs/{job_id})。"""
    try:
        creds = _validated_credentials(params)
    except RemoteSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    engine = deps.get_db_sink().engine
    triggered_by = _app().current_username(request)
    fetched_date_start = (params.fetched_date_start or "").strip() or None
    source_ids = [s.strip() for s in (params.source_ids or []) if s and s.strip()] or None

    job = launch_remote_sync_job(
        engine,
        base_url=creds["base_url"],
        username=creds["username"],
        password=creds["password"],
        fetched_date_start=fetched_date_start,
        source_ids=source_ids,
        page_size=params.page_size,
        created_by=triggered_by,
    )
    return {"status": "accepted", "job_id": job.id}


@router.get("/api/admin/remote-sync/schedule")
def get_remote_sync_schedule():
    """读定时同步配置(不回显密码,含 password_set)。"""
    engine = deps.get_db_sink().engine
    return remote_sync_service.load_schedule(engine)


@router.post("/api/admin/remote-sync/schedule")
def set_remote_sync_schedule(params: RemoteSyncScheduleParams):
    """写定时同步配置并热生效调度。凭据只写不回显(密码空串 = 保留已存)。"""
    engine = deps.get_db_sink().engine

    cron = (params.cron or "").strip()
    try:
        CronTrigger.from_crontab(cron)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="cron 表达式无效")

    source_ids = [s.strip() for s in (params.source_ids or []) if s and s.strip()]
    updates: Dict[str, Any] = {
        "enabled": bool(params.enabled),
        "cron": cron,
        "username": (params.username or "").strip(),
        "password": params.password,
        "source_ids": source_ids,
    }

    if params.enabled:
        try:
            base_url = remote_sync_service.normalize_base_url(params.base_url)
        except RemoteSyncError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        existing = remote_sync_service.load_schedule(engine, include_secret=True)
        has_password = bool(params.password) or bool(existing.get("password"))
        if not updates["username"] or not has_password:
            raise HTTPException(
                status_code=400, detail="启用定时同步需要远端地址、账号与密码"
            )
        updates["base_url"] = base_url
    else:
        # 停用时宽松保存:允许清着配置,base_url 仅规整不强校验。
        updates["base_url"] = (params.base_url or "").strip()

    saved = remote_sync_service.save_schedule(engine, updates, updated_at=_now_iso())
    _app().reload_remote_sync_schedule()
    return saved


@router.get("/api/admin/remote-sync/status")
def remote_sync_status():
    """同步目标游标(KV)+ 最近同步任务列表,供管理界面展示与「增量自上次」预填。"""
    engine = deps.get_db_sink().engine
    return {
        "state": remote_sync_service.load_sync_state(engine),
        "jobs": jobs.list_jobs(engine, job_type=REMOTE_SYNC_JOB_TYPE, limit=10),
    }
