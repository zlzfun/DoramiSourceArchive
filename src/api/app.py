# /src/api/app.py

import os
import json
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Body, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field as PydanticField
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from apscheduler.triggers.cron import CronTrigger

from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage
from pipeline.core import DataPipeline
from models.db import (
    ArticleRecord,
    CollectionJobRecord,
    CollectionJobRunRecord,
    FetchTaskRecord,
    FetchRunRecord,
    NodeGroupRecord,
    SourceConfigRecord,
    SourceStateRecord,
    AppSettingRecord,
)
from models.content import BaseContent, SocialPostContent

# 引入动态抓取器注册中心
from fetchers.registry import fetcher_registry
from api.skill_router import router as skill_router

from starlette.responses import JSONResponse as StarletteJSONResponse
from mcp_server import build_mcp_app


class GenericContent(BaseContent):
    # 拆分为结构类型与来源通道
    content_type = "restored_from_db"
    source_id = "database_restore"


_mcp_enabled: bool = True


class MCPGateApp:
    """ASGI gate for /mcp — checks _mcp_enabled and delegates to the MCP ASGI app.
    _app is set in the lifespan so each restart gets a fresh FastMCP instance."""
    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not _mcp_enabled:
            response = StarletteJSONResponse(
                {"detail": "MCP server is disabled"}, status_code=503
            )
            await response(scope, receive, send)
            return
        if self._app is None:
            response = StarletteJSONResponse({"detail": "MCP server not ready"}, status_code=503)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


_mcp_gate = MCPGateApp()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_enabled
    # Init MCP enabled state from DB
    with Session(db_sink.engine) as session:
        rec = session.get(AppSettingRecord, "mcp_enabled")
        if rec is None:
            session.add(AppSettingRecord(key="mcp_enabled", value="true"))
            session.commit()
            _mcp_enabled = True
        else:
            _mcp_enabled = rec.value.lower() == "true"
    # Build fresh FastMCP instance (session_manager can only be run() once per instance)
    mcp = build_mcp_app(db_sink, vector_sink)
    _mcp_gate._app = mcp.streamable_http_app()
    # Start scheduler
    load_tasks_to_scheduler()
    if scheduler.state == STATE_STOPPED:
        scheduler.start()
        print("⏰ APScheduler 定时调度引擎已启动！")
    async with mcp.session_manager.run():
        yield
    _mcp_gate._app = None


app = FastAPI(title="Dorami 数据归档中枢 API", lifespan=lifespan)

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
db_sink = DatabaseStorage(db_url=f"sqlite:///{os.path.join(base_path, 'data', 'cms_data.db')}")
vector_sink = ChromaVectorStorage(db_path=os.path.join(base_path, "data", "chroma_db"))
pipeline = DataPipeline(storages=[db_sink])

app.mount("/mcp", _mcp_gate)
app.include_router(skill_router)

scheduler = AsyncIOScheduler()


# ==================== 定时任务系统核心逻辑 ====================
def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _json_dumps(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


def _json_loads(raw_value: Optional[str], default: Any = None) -> Any:
    if not raw_value:
        return default if default is not None else {}
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else {}


def _split_csv(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _date_end_value(raw_value: str) -> str:
    return raw_value if "T" in raw_value else f"{raw_value}T23:59:59"


def apply_article_query_filters(
        query,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        has_content: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
):
    if content_type:
        query = query.where(ArticleRecord.content_type == content_type)
    content_type_list = _split_csv(content_types)
    if content_type_list:
        query = query.where(ArticleRecord.content_type.in_(content_type_list))

    if source_id:
        query = query.where(ArticleRecord.source_id == source_id)
    source_id_list = _split_csv(source_ids)
    if source_id_list:
        query = query.where(ArticleRecord.source_id.in_(source_id_list))

    if job_id is not None:
        query = query.where(ArticleRecord.job_id == job_id)
    if job_run_id is not None:
        query = query.where(ArticleRecord.job_run_id == job_run_id)
    if fetch_run_id is not None:
        query = query.where(ArticleRecord.fetch_run_id == fetch_run_id)
    if run_scope:
        query = query.where(ArticleRecord.run_scope == run_scope)

    if is_vectorized is not None:
        query = query.where(ArticleRecord.is_vectorized == is_vectorized)
    if has_content is not None:
        query = query.where(ArticleRecord.has_content == has_content)
    if search:
        query = query.where(ArticleRecord.title.contains(search))

    if publish_date_start:
        query = query.where(ArticleRecord.publish_date >= publish_date_start)
    if publish_date_end:
        query = query.where(ArticleRecord.publish_date <= _date_end_value(publish_date_end))

    if fetched_date_start:
        query = query.where(ArticleRecord.fetched_date >= fetched_date_start)
    if fetched_date_end:
        query = query.where(ArticleRecord.fetched_date <= _date_end_value(fetched_date_end))

    return query


def serialize_dify_article(record: ArticleRecord, include_content: bool = True) -> Dict[str, Any]:
    extensions = _json_loads(record.extensions_json, {})
    metadata = {
        "id": record.id,
        "title": record.title,
        "source_url": record.source_url,
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "fetch_run_id": record.fetch_run_id,
        "job_id": record.job_id,
        "job_run_id": record.job_run_id,
        "source_group_id": record.source_group_id,
        "run_scope": record.run_scope,
        "has_content": record.has_content,
        "is_vectorized": record.is_vectorized,
        "extensions": extensions,
    }

    item = {
        "id": record.id,
        "title": record.title,
        "url": record.source_url,
        "metadata": metadata,
    }
    if include_content:
        item["content"] = record.content or ""
    return item


def article_to_markdown(record: ArticleRecord) -> str:
    metadata = serialize_dify_article(record, include_content=False)["metadata"]
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    content = record.content or ""
    return f"---\n{frontmatter}\n---\n\n# {record.title}\n\n{content}".strip()


def serialize_node_group(record: NodeGroupRecord) -> Dict[str, Any]:
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
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_collection_job(record: CollectionJobRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "group_id": record.group_id,
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


def normalize_fetcher_ids(fetcher_ids: Optional[List[str]]) -> List[str]:
    seen = set()
    normalized = []
    for fetcher_id in fetcher_ids or []:
        clean_id = str(fetcher_id).strip()
        if clean_id and clean_id not in seen:
            normalized.append(clean_id)
            seen.add(clean_id)
    return normalized


def resolve_collection_job_fetcher_ids(job: CollectionJobRecord, session: Session) -> List[str]:
    fetcher_ids = normalize_fetcher_ids(_json_loads(job.fetcher_ids_json, []))
    if fetcher_ids:
        return fetcher_ids
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            return normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, []))
    return []


def build_collection_job_items(job: CollectionJobRecord, session: Session) -> List[Dict[str, Any]]:
    default_params = {}
    per_fetcher_params = {}
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            default_params.update(_json_loads(group.params_json, {}))
            per_fetcher_params.update(_json_loads(group.per_fetcher_params_json, {}))
    default_params.update(_json_loads(job.params_json, {}))
    job_per_fetcher_params = _json_loads(job.per_fetcher_params_json, {})
    items = []
    for fetcher_id in resolve_collection_job_fetcher_ids(job, session):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        params.update(job_per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def build_node_group_items(group: NodeGroupRecord) -> List[Dict[str, Any]]:
    default_params = _json_loads(group.params_json, {})
    per_fetcher_params = _json_loads(group.per_fetcher_params_json, {})
    items = []
    for fetcher_id in normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, [])):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def resolve_delivery_source_ids(
        session: Session,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
) -> List[str]:
    explicit_ids = normalize_fetcher_ids(([source_id] if source_id else []) + _split_csv(source_ids))
    scope_ids = []
    if group_id is not None:
        group = session.get(NodeGroupRecord, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="节点组不存在")
        scope_ids.extend(_json_loads(group.fetcher_ids_json, []))
    if job_id is not None:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        scope_ids.extend(resolve_collection_job_fetcher_ids(job, session))

    scope_ids = normalize_fetcher_ids(scope_ids)
    if explicit_ids and scope_ids:
        scope_set = set(scope_ids)
        return [item for item in explicit_ids if item in scope_set]
    return explicit_ids or scope_ids


def create_collection_job_run(
        name: str,
        trigger_type: str,
        node_count: int,
        job_id: Optional[int] = None,
        group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = CollectionJobRunRecord(
            job_id=job_id,
            group_id=group_id,
            run_scope=run_scope,
            trigger_type=trigger_type,
            status="running",
            name=name,
            node_count=node_count,
            started_at=_now_iso(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def finish_collection_job_run(
        job_run_id: int,
        status: str,
        child_run_ids: Optional[List[int]] = None,
        fetched_count: int = 0,
        saved_count: int = 0,
        skipped_count: int = 0,
        failed_count: int = 0,
        error_message: Optional[str] = None,
):
    ended_at = _now_iso()
    with Session(db_sink.engine) as session:
        run = session.get(CollectionJobRunRecord, job_run_id)
        if not run:
            return
        if not child_run_ids:
            child_run_ids = [
                item.id for item in session.exec(
                    select(FetchRunRecord).where(FetchRunRecord.job_run_id == job_run_id)
                ).all()
                if item.id is not None
            ]
        run.status = status
        run.ended_at = ended_at
        run.child_run_ids_json = _json_dumps(child_run_ids or [])
        run.fetched_count = fetched_count
        run.saved_count = saved_count
        run.skipped_count = skipped_count
        run.failed_count = failed_count
        run.error_message = error_message
        try:
            started_at = datetime.datetime.fromisoformat(run.started_at)
            ended_dt = datetime.datetime.fromisoformat(ended_at)
            run.duration_ms = int((ended_dt - started_at).total_seconds() * 1000)
        except ValueError:
            run.duration_ms = None
        session.add(run)
        session.commit()


def create_fetch_run(
        fetcher_id: str,
        params: dict,
        trigger_type: str,
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        source_group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = FetchRunRecord(
            fetcher_id=fetcher_id,
            task_id=task_id,
            job_id=job_id,
            job_run_id=job_run_id,
            source_group_id=source_group_id,
            run_scope=run_scope,
            trigger_type=trigger_type,
            status="running",
            params_json=_json_dumps(params),
            started_at=_now_iso()
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def finish_fetch_run(run_id: int, status: str, result: Any = None, error_message: Optional[str] = None):
    ended_at = _now_iso()
    with Session(db_sink.engine) as session:
        run = session.get(FetchRunRecord, run_id)
        if not run:
            return

        run.status = status
        run.ended_at = ended_at
        run.error_message = error_message
        try:
            started_at = datetime.datetime.fromisoformat(run.started_at)
            ended_dt = datetime.datetime.fromisoformat(ended_at)
            run.duration_ms = int((ended_dt - started_at).total_seconds() * 1000)
        except ValueError:
            run.duration_ms = None

        if result:
            run.fetched_count = getattr(result, "fetched_count", 0)
            run.saved_count = getattr(result, "saved_count", 0)
            run.skipped_count = getattr(result, "skipped_count", 0)

        session.add(run)
        session.commit()


async def execute_fetch_job(fetcher_id: str, params: dict, task_id: Optional[int] = None):
    print(f"⏰ 定时任务触发: 正在执行调度节点 {fetcher_id}...")
    job_run_id = create_collection_job_run(
        name=f"旧定时任务 #{task_id or fetcher_id}",
        trigger_type="scheduled",
        node_count=1,
        run_scope="legacy_task",
    )
    try:
        result = await run_fetcher_with_tracking(
            fetcher_id,
            params,
            trigger_type="scheduled",
            task_id=task_id,
            job_run_id=job_run_id,
            run_scope="legacy_task",
        )
        finish_collection_job_run(
            job_run_id,
            status="success",
            child_run_ids=[result["run_id"]],
            fetched_count=result["fetched_count"],
            saved_count=result["saved_count"],
            skipped_count=result["skipped_count"],
        )
    except ValueError as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        print(f"❌ {e}")
    except Exception as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        print(f"❌ 定时任务执行失败: {e}")
        raise


async def execute_collection_job(job_id: int):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job or not job.is_active:
            print(f"⚠️ 采集任务不可用或已停用: {job_id}")
            return
        items = build_collection_job_items(job, session)
        job_name = job.name
        group_id = job.group_id

    print(f"⏰ 采集任务触发: {job_name} ({len(items)} 个节点)")
    await run_collection_items(
        items,
        name=job_name,
        trigger_type="scheduled",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


async def execute_collection_job_node(job_id: int, fetcher_id: str):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job or not job.is_active:
            print(f"⚠️ 采集任务不可用或已停用: {job_id}")
            return
        items = [item for item in build_collection_job_items(job, session) if item["fetcher_id"] == fetcher_id]
        job_name = job.name
        group_id = job.group_id
    if not items:
        print(f"⚠️ 采集任务节点不可用: {job_id}/{fetcher_id}")
        return
    await run_collection_items(
        items,
        name=f"{job_name} / {fetcher_id}",
        trigger_type="scheduled",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


async def execute_node_group(group_id: int):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group or not group.is_active:
            print(f"⚠️ 节点组不可用或已停用: {group_id}")
            return
        items = build_node_group_items(group)
        group_name = group.name
    await run_collection_items(
        items,
        name=f"节点组定时: {group_name}",
        trigger_type="scheduled",
        group_id=group_id,
        run_scope="ad_hoc",
    )


async def execute_node_group_node(group_id: int, fetcher_id: str):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group or not group.is_active:
            print(f"⚠️ 节点组不可用或已停用: {group_id}")
            return
        items = [item for item in build_node_group_items(group) if item["fetcher_id"] == fetcher_id]
        group_name = group.name
    if not items:
        print(f"⚠️ 节点组节点不可用: {group_id}/{fetcher_id}")
        return
    await run_collection_items(
        items,
        name=f"节点组定时: {group_name} / {fetcher_id}",
        trigger_type="scheduled",
        group_id=group_id,
        run_scope="ad_hoc",
    )


def add_cron_job(job_id: str, callback, cron_expr: str, args: List[Any]):
    parts = cron_expr.split()
    if len(parts) != 5:
        return
    trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
    scheduler.add_job(callback, trigger, args=args, id=job_id, replace_existing=True)


def load_tasks_to_scheduler():
    scheduler.remove_all_jobs()
    with Session(db_sink.engine) as session:
        tasks = session.exec(select(FetchTaskRecord).where(FetchTaskRecord.is_active == True)).all()
        for task in tasks:
            params = json.loads(task.params_json)
            add_cron_job(f"task_{task.id}", execute_fetch_job, task.cron_expr, [task.fetcher_id, params, task.id])
        groups = session.exec(
            select(NodeGroupRecord)
            .where(NodeGroupRecord.is_active == True)
        ).all()
        for group in groups:
            if group.cron_expr:
                add_cron_job(f"node_group_{group.id}", execute_node_group, group.cron_expr, [group.id])
            for fetcher_id, cron_expr in _json_loads(group.per_fetcher_cron_json, {}).items():
                if cron_expr:
                    add_cron_job(
                        f"node_group_{group.id}_{fetcher_id}",
                        execute_node_group_node,
                        cron_expr,
                        [group.id, fetcher_id],
                    )
        jobs = session.exec(
            select(CollectionJobRecord)
            .where(CollectionJobRecord.is_active == True)
        ).all()
        for job in jobs:
            if job.cron_expr:
                add_cron_job(f"collection_job_{job.id}", execute_collection_job, job.cron_expr, [job.id])
            for fetcher_id, cron_expr in _json_loads(job.per_fetcher_cron_json, {}).items():
                if cron_expr:
                    add_cron_job(
                        f"collection_job_{job.id}_{fetcher_id}",
                        execute_collection_job_node,
                        cron_expr,
                        [job.id, fetcher_id],
                    )


# ==================== 1. 数据台账与 CRUD ====================
class BatchOpParams(BaseModel):
    ids: List[str]


class SourceConfigCreate(BaseModel):
    source_id: str
    name: str
    source_type: str = "rss"
    url: str = ""
    category: str = ""
    fetcher_id: str = ""
    description: str = ""
    is_active: bool = True
    fetch_interval_minutes: Optional[int] = None
    cron_expr: str = ""
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class SourceConfigUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    fetcher_id: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None
    cron_expr: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class SourceFetchParams(BaseModel):
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class SocialPostImportItem(BaseModel):
    platform: str = "x"
    post_id: str
    source_id: str = ""
    author_id: str = ""
    author_handle: str = ""
    author_name: str = ""
    text: str = ""
    title: str = ""
    source_url: str = ""
    publish_date: str = ""
    conversation_id: str = ""
    in_reply_to_id: str = ""
    quoted_post_id: str = ""
    reposted_post_id: str = ""
    lang: str = ""
    tags: List[str] = PydanticField(default_factory=list)
    media_urls: List[str] = PydanticField(default_factory=list)
    metrics: Dict[str, Any] = PydanticField(default_factory=dict)
    raw_data: Dict[str, Any] = PydanticField(default_factory=dict)


class SocialPostImportParams(BaseModel):
    source_id: str = "import_social_posts"
    posts: List[SocialPostImportItem]


def serialize_source_config(record: SourceConfigRecord) -> Dict[str, Any]:
    data = record.dict()
    try:
        data["params"] = json.loads(record.params_json or "{}")
    except json.JSONDecodeError:
        data["params"] = {}
    return data


def normalize_source_id(source_id: str) -> str:
    return source_id.strip()


def parse_json_object(raw_json: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_source_fetcher_id(source_config: SourceConfigRecord) -> str:
    if source_config.fetcher_id:
        return source_config.fetcher_id
    if source_config.source_type.lower() in {"rss", "atom"}:
        return "generic_rss"
    return ""


def build_source_fetch_params(source_config: SourceConfigRecord, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = parse_json_object(source_config.params_json)
    params.update({
        "source_id": source_config.source_id,
        "feed_url": source_config.url,
        "feed_name": source_config.name,
        "category": source_config.category,
    })
    if overrides:
        params.update(overrides)
    return params


def normalize_social_source_id(platform: str, author_handle: str, fallback: str) -> str:
    if fallback:
        return fallback.strip()
    safe_platform = (platform or "social").strip().lower().replace("/", "_")
    safe_handle = (author_handle or "unknown").strip().lower().lstrip("@").replace("/", "_")
    return f"{safe_platform}_{safe_handle}"


def build_social_post_content(post: SocialPostImportItem, batch_source_id: str) -> SocialPostContent:
    platform = post.platform.strip() or "x"
    post_id = post.post_id.strip()
    if not post_id:
        raise ValueError("post_id 不能为空")

    source_id = normalize_social_source_id(platform, post.author_handle, post.source_id or batch_source_id)
    title = post.title.strip() or (post.text.strip()[:80] if post.text else f"{platform} post {post_id}")
    source_url = post.source_url.strip()
    if not source_url and post.author_handle:
        source_url = f"https://x.com/{post.author_handle.lstrip('@')}/status/{post_id}"

    publish_date = post.publish_date.strip() or _now_iso()
    raw_data = dict(post.raw_data or {})
    raw_data.setdefault("import_source_id", batch_source_id)

    return SocialPostContent(
        id=f"{source_id}_{post_id}",
        title=title,
        source_url=source_url,
        publish_date=publish_date,
        source_id=source_id,
        content=post.text,
        has_content=bool(post.text),
        platform=platform,
        author_id=post.author_id,
        author_handle=post.author_handle,
        author_name=post.author_name,
        post_id=post_id,
        conversation_id=post.conversation_id,
        in_reply_to_id=post.in_reply_to_id,
        quoted_post_id=post.quoted_post_id,
        reposted_post_id=post.reposted_post_id,
        lang=post.lang,
        tags=post.tags,
        media_urls=post.media_urls,
        metrics=post.metrics,
        raw_data=raw_data,
    )


def resolve_state_source_id(fetcher_id: str, params: Optional[Dict[str, Any]], result: Any = None) -> str:
    if result and getattr(result, "latest_content_source_id", ""):
        return result.latest_content_source_id
    if params:
        source_id = str(params.get("source_id", "")).strip()
        if source_id:
            return source_id
    return fetcher_id


def classify_error(error: Exception | str | None) -> str:
    if not error:
        return ""
    message = str(error).lower()
    if "unknown" in message or "未知" in message:
        return "configuration_error"
    if "timeout" in message or "connect" in message or "network" in message:
        return "network_error"
    if "http" in message or "status" in message:
        return "http_error"
    if "parse" in message or "解析" in message:
        return "parse_error"
    return error.__class__.__name__ if isinstance(error, Exception) else "runtime_error"


def mark_source_state_started(fetcher_id: str, params: Dict[str, Any], run_id: int):
    source_id = resolve_state_source_id(fetcher_id, params)
    now = _now_iso()
    with Session(db_sink.engine) as session:
        state = session.get(SourceStateRecord, source_id)
        if not state:
            state = SourceStateRecord(
                source_id=source_id,
                fetcher_id=fetcher_id,
                status="running",
                last_started_at=now,
                last_run_id=run_id,
                updated_at=now
            )
        else:
            state.fetcher_id = fetcher_id
            state.status = "running"
            state.last_started_at = now
            state.last_run_id = run_id
            state.updated_at = now
        session.add(state)
        session.commit()


def mark_source_state_finished(
        fetcher_id: str,
        params: Dict[str, Any],
        run_id: int,
        status: str,
        result: Any = None,
        error: Exception | str | None = None
):
    source_id = resolve_state_source_id(fetcher_id, params, result)
    now = _now_iso()
    with Session(db_sink.engine) as session:
        state = session.get(SourceStateRecord, source_id)
        if not state:
            state = SourceStateRecord(
                source_id=source_id,
                fetcher_id=fetcher_id,
                status="unknown",
                last_run_id=run_id,
                updated_at=now
            )

        state.fetcher_id = fetcher_id
        state.last_run_id = run_id
        state.last_completed_at = now
        state.total_runs += 1
        state.latest_fetched_count = getattr(result, "fetched_count", 0) if result else 0
        state.latest_saved_count = getattr(result, "saved_count", 0) if result else 0
        state.latest_skipped_count = getattr(result, "skipped_count", 0) if result else 0

        if status == "success":
            state.status = "healthy"
            state.last_success_at = now
            state.success_runs += 1
            state.consecutive_failures = 0
            state.latest_error_type = ""
            state.latest_error_message = None
            latest_content_id = getattr(result, "latest_content_id", "") if result else ""
            latest_publish_date = getattr(result, "latest_content_publish_date", "") if result else ""
            if latest_content_id:
                state.last_content_id = latest_content_id
                state.last_cursor_value = latest_content_id
            if latest_publish_date:
                state.last_cursor_date = latest_publish_date
            if result and getattr(result, "latest_content_type", ""):
                state.content_type = result.latest_content_type
        else:
            state.status = "failing"
            state.last_failure_at = now
            state.failed_runs += 1
            state.consecutive_failures += 1
            state.latest_error_type = classify_error(error)
            state.latest_error_message = str(error) if error else None

        state.updated_at = now
        session.add(state)
        session.commit()


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


async def run_fetcher_with_tracking(
        fetcher_id: str,
        params: Dict[str, Any],
        trigger_type: str = "manual",
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        source_group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> Dict[str, Any]:
    run_id = create_fetch_run(
        fetcher_id,
        params,
        trigger_type=trigger_type,
        task_id=task_id,
        job_id=job_id,
        job_run_id=job_run_id,
        source_group_id=source_group_id,
        run_scope=run_scope,
    )
    mark_source_state_started(fetcher_id, params, run_id)
    fetcher_class = fetcher_registry.get_class(fetcher_id)
    if not fetcher_class:
        message = f"未知的抓取器节点: {fetcher_id}"
        finish_fetch_run(run_id, status="failed", error_message=message)
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=message)
        raise ValueError(message)

    try:
        fetcher = fetcher_class()
        result = await pipeline.run_task(
            fetcher,
            lineage={
                "fetch_run_id": run_id,
                "job_id": job_id,
                "job_run_id": job_run_id,
                "source_group_id": source_group_id,
                "run_scope": run_scope,
            },
            **params,
        )
        finish_fetch_run(run_id, status="success", result=result)
        mark_source_state_finished(fetcher_id, params, run_id, status="success", result=result)
        return {
            "status": "success",
            "run_id": run_id,
            "job_id": job_id,
            "job_run_id": job_run_id,
            "fetcher_id": fetcher_id,
            "fetched_count": result.fetched_count,
            "saved_count": result.saved_count,
            "skipped_count": result.skipped_count,
            "saved_content_ids": result.saved_content_ids,
        }
    except Exception as e:
        finish_fetch_run(run_id, status="failed", error_message=str(e))
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=e)
        raise


async def run_single_fetch_as_collection(
        fetcher_id: str,
        params: Dict[str, Any],
        name: str,
        trigger_type: str = "manual",
        run_scope: str = "ad_hoc",
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        group_id: Optional[int] = None,
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=1,
        job_id=job_id,
        group_id=group_id,
        run_scope=run_scope,
    )
    try:
        result = await run_fetcher_with_tracking(
            fetcher_id,
            params,
            trigger_type=trigger_type,
            task_id=task_id,
            job_id=job_id,
            job_run_id=job_run_id,
            source_group_id=group_id,
            run_scope=run_scope,
        )
        finish_collection_job_run(
            job_run_id,
            status="success",
            child_run_ids=[result["run_id"]],
            fetched_count=result["fetched_count"],
            saved_count=result["saved_count"],
            skipped_count=result["skipped_count"],
        )
        return {**result, "job_run_id": job_run_id}
    except Exception as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        raise


async def run_collection_items(
        items: List[Dict[str, Any]],
        name: str,
        trigger_type: str = "manual",
        job_id: Optional[int] = None,
        group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=len(items),
        job_id=job_id,
        group_id=group_id,
        run_scope=run_scope,
    )
    results = []
    child_run_ids = []
    fetched_count = 0
    saved_count = 0
    skipped_count = 0
    failed_count = 0
    errors = []
    saved_content_ids = []

    for item in items:
        fetcher_id = str(item.get("fetcher_id", "")).strip()
        params = item.get("params") or {}
        if not fetcher_id:
            failed_count += 1
            errors.append("空节点 ID")
            results.append({"fetcher_id": fetcher_id, "status": "failed", "error": "空节点 ID"})
            continue
        try:
            result = await run_fetcher_with_tracking(
                fetcher_id,
                params,
                trigger_type=trigger_type,
                job_id=job_id,
                job_run_id=job_run_id,
                source_group_id=group_id,
                run_scope=run_scope,
            )
            child_run_ids.append(result["run_id"])
            fetched_count += result.get("fetched_count", 0)
            saved_count += result.get("saved_count", 0)
            skipped_count += result.get("skipped_count", 0)
            saved_content_ids.extend(result.get("saved_content_ids", []))
            results.append(result)
        except Exception as e:
            failed_count += 1
            errors.append(f"{fetcher_id}: {e}")
            results.append({"fetcher_id": fetcher_id, "status": "failed", "error": str(e)})

    status = "success"
    if failed_count and failed_count == len(items):
        status = "failed"
    elif failed_count:
        status = "partial_failed"

    finish_collection_job_run(
        job_run_id,
        status=status,
        child_run_ids=child_run_ids,
        fetched_count=fetched_count,
        saved_count=saved_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        error_message="; ".join(errors[:3]) if errors else None,
    )
    return {
        "status": status,
        "job_id": job_id,
        "job_run_id": job_run_id,
        "count": len(items),
        "fetched_count": fetched_count,
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "saved_content_ids": saved_content_ids,
        "results": results,
    }


# ==================== 0. 数据源配置 ====================
@app.get("/api/source-configs")
def get_source_configs(
        source_type: Optional[str] = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(SourceConfigRecord)
        if source_type:
            query = query.where(SourceConfigRecord.source_type == source_type)
        if category:
            query = query.where(SourceConfigRecord.category == category)
        if is_active is not None:
            query = query.where(SourceConfigRecord.is_active == is_active)
        if search:
            query = query.where(SourceConfigRecord.name.contains(search))
        query = query.order_by(SourceConfigRecord.source_type, SourceConfigRecord.name).offset(skip).limit(limit)
        return [serialize_source_config(record) for record in session.exec(query).all()]


@app.get("/api/source-configs/{source_id}")
def get_source_config(source_id: str):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        return serialize_source_config(record)


@app.post("/api/source-configs")
def create_source_config(params: SourceConfigCreate):
    source_id = normalize_source_id(params.source_id)
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")

    with Session(db_sink.engine) as session:
        existing = session.get(SourceConfigRecord, source_id)
        if existing:
            raise HTTPException(status_code=400, detail="该 source_id 已存在")

        now = _now_iso()
        record = SourceConfigRecord(
            source_id=source_id,
            name=params.name.strip(),
            source_type=params.source_type.strip() or "rss",
            url=params.url.strip(),
            category=params.category.strip(),
            fetcher_id=params.fetcher_id.strip(),
            description=params.description.strip(),
            is_active=params.is_active,
            fetch_interval_minutes=params.fetch_interval_minutes,
            cron_expr=params.cron_expr.strip(),
            params_json=_json_dumps(params.params),
            created_at=now,
            updated_at=now
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_source_config(record)


@app.put("/api/source-configs/{source_id}")
def update_source_config(source_id: str, params: SourceConfigUpdate):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")

        update_data = params.dict(exclude_unset=True)
        for key, value in update_data.items():
            if key == "params":
                record.params_json = _json_dumps(value)
            elif isinstance(value, str):
                setattr(record, key, value.strip())
            else:
                setattr(record, key, value)

        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_source_config(record)


@app.post("/api/source-configs/{source_id}/toggle")
def toggle_source_config(source_id: str, is_active: bool = Body(..., embed=True)):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        record.is_active = is_active
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_source_config(record)


@app.delete("/api/source-configs/{source_id}")
def delete_source_config(source_id: str):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        session.delete(record)
        session.commit()
        return {"status": "success"}


@app.post("/api/source-configs/{source_id}/fetch")
async def fetch_source_config(source_id: str, body: Optional[SourceFetchParams] = None):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        if not record.is_active:
            raise HTTPException(status_code=400, detail="数据源已停用，无法触发抓取")

        fetcher_id = resolve_source_fetcher_id(record)
        if not fetcher_id:
            raise HTTPException(status_code=400, detail="该数据源未绑定可用抓取器")
        params = build_source_fetch_params(record, body.params if body else {})

    try:
        result = await run_single_fetch_as_collection(
            fetcher_id,
            params,
            name=f"临时抓取: {source_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
        return {"source_id": source_id, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/source-configs/fetch-active-rss")
async def fetch_active_rss_sources(body: Optional[SourceFetchParams] = None):
    results = []
    with Session(db_sink.engine) as session:
        records = session.exec(
            select(SourceConfigRecord)
            .where(SourceConfigRecord.is_active == True)
            .where(SourceConfigRecord.source_type.in_(["rss", "atom"]))
            .order_by(SourceConfigRecord.name)
        ).all()

    items = []
    skipped_results = []
    for record in records:
        fetcher_id = resolve_source_fetcher_id(record)
        if not fetcher_id:
            skipped_results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue

        params = build_source_fetch_params(record, body.params if body else {})
        items.append({"source_id": record.source_id, "fetcher_id": fetcher_id, "params": params})

    result = await run_collection_items(
        items,
        name="临时抓取: 活跃 RSS 数据源",
        trigger_type="manual",
        run_scope="ad_hoc",
    )
    results = skipped_results + [
        {"source_id": item.get("source_id"), **item_result}
        for item, item_result in zip(items, result["results"])
    ]
    return {**result, "results": results}


@app.post("/api/import/social-posts")
async def import_social_posts(params: SocialPostImportParams):
    saved_count = 0
    skipped_count = 0
    errors = []

    for index, post in enumerate(params.posts):
        try:
            content = build_social_post_content(post, params.source_id)
            if await db_sink.save(content):
                saved_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            errors.append({
                "index": index,
                "post_id": post.post_id,
                "error": str(e),
            })

    return {
        "status": "partial_success" if errors else "success",
        "received_count": len(params.posts),
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "errors": errors,
    }


@app.get("/api/articles")
def get_articles(
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,  # ✨ 升级：起始原始发布日期
        publish_date_end: Optional[str] = None,  # ✨ 升级：结束原始发布日期
        fetched_date_start: Optional[str] = None,  # ✨ 升级：起始中枢收录日期
        fetched_date_end: Optional[str] = None,  # ✨ 升级：结束中枢收录日期
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(ArticleRecord)
        query = apply_article_query_filters(
            query,
            content_type=content_type,
            source_id=source_id,
            job_id=job_id,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            is_vectorized=is_vectorized,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        query = query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(limit)
        return session.exec(query).all()


@app.get("/api/dify/articles")
def get_dify_articles(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        include_content: bool = True,
        skip: int = 0,
        limit: int = 100
):
    safe_limit = min(max(limit, 1), 500)
    with Session(db_sink.engine) as session:
        delivery_source_ids = resolve_delivery_source_ids(
            session, source_id=source_id, source_ids=source_ids, group_id=group_id, job_id=job_id
        )
        if (source_id or source_ids or group_id is not None or job_id is not None) and not delivery_source_ids:
            return {"status": "success", "count": 0, "skip": skip, "limit": safe_limit, "next_skip": None, "items": []}
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_ids=",".join(delivery_source_ids) if delivery_source_ids else None,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
        ).all()

    return {
        "status": "success",
        "count": len(records),
        "skip": skip,
        "limit": safe_limit,
        "next_skip": skip + len(records) if len(records) == safe_limit else None,
        "items": [serialize_dify_article(record, include_content=include_content) for record in records],
    }


@app.get("/api/dify/articles.md")
def export_dify_articles_markdown(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        skip: int = 0,
        limit: int = 100
):
    safe_limit = min(max(limit, 1), 200)
    with Session(db_sink.engine) as session:
        delivery_source_ids = resolve_delivery_source_ids(
            session, source_id=source_id, source_ids=source_ids, group_id=group_id, job_id=job_id
        )
        if (source_id or source_ids or group_id is not None or job_id is not None) and not delivery_source_ids:
            return Response(content="", media_type="text/markdown; charset=utf-8")
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_ids=",".join(delivery_source_ids) if delivery_source_ids else None,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
        ).all()

    markdown = "\n\n---\n\n".join(article_to_markdown(record) for record in records)
    return Response(content=markdown, media_type="text/markdown; charset=utf-8")


@app.post("/api/articles")
async def create_article_manual(params: dict = Body(...)):
    """接收前端传来的手工录入数据并入库"""
    content_obj = GenericContent(
        id=params.get("id"),
        title=params.get("title", "未命名"),
        source_url=params.get("source_url", ""),
        publish_date=params.get("publish_date", ""),
        content=params.get("content", ""),
        has_content=True if params.get("content") else False
    )
    content_obj.content_type = params.get("content_type", "manual_entry")
    content_obj.source_id = params.get("source_id", "manual")

    try:
        extensions = json.loads(params.get("extensions_json", "{}"))
        for k, v in extensions.items():
            setattr(content_obj, k, v)
    except Exception as e:
        pass

    success = await db_sink.save(content_obj)
    if not success:
        raise HTTPException(status_code=400, detail="该条目 ID 已存在，请避免重复录入")

    return {"status": "success"}


@app.delete("/api/articles/{article_id:path}")
async def delete_article(article_id: str):
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="文章未找到")
    if record.is_vectorized: await vector_sink.delete(article_id)
    await db_sink.delete(article_id)
    return {"status": "success"}


@app.post("/api/articles/batch-delete")
async def batch_delete_articles(params: BatchOpParams):
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record:
            if record.is_vectorized: await vector_sink.delete(uid)
            await db_sink.delete(uid)
    return {"status": "success"}


class ArticleUpdateParams(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    source_url: Optional[str] = None
    extensions_json: Optional[str] = None


@app.put("/api/articles/{article_id:path}")
async def update_article(article_id: str, params: ArticleUpdateParams):
    update_data = {k: v for k, v in params.dict().items() if v is not None}
    if "content" in update_data or "title" in update_data:
        update_data["is_vectorized"] = False
        await vector_sink.delete(article_id)

    success = await db_sink.update(article_id, update_data)
    if not success: raise HTTPException(status_code=404, detail="更新失败")
    return {"status": "success"}


# ==================== 2. 调度与抓取 (注册中心化) ====================

@app.get("/api/fetchers")
async def get_available_fetchers():
    return fetcher_registry.get_all_metadata()


@app.get("/api/source-health")
def get_source_health():
    fetchers = fetcher_registry.get_all_metadata()
    fetcher_ids = [fetcher["id"] for fetcher in fetchers]

    with Session(db_sink.engine) as session:
        runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.fetcher_id.in_(fetcher_ids))).all()
        states = session.exec(select(SourceStateRecord).where(SourceStateRecord.source_id.in_(fetcher_ids))).all()

    states_by_source = {state.source_id: state for state in states}
    runs_by_fetcher: Dict[str, List[FetchRunRecord]] = {fetcher_id: [] for fetcher_id in fetcher_ids}
    for run in runs:
        runs_by_fetcher.setdefault(run.fetcher_id, []).append(run)

    health_items = [
        build_fetcher_health_from_state(fetcher, states_by_source[fetcher["id"]])
        if fetcher["id"] in states_by_source
        else build_fetcher_health(fetcher, runs_by_fetcher.get(fetcher["id"], []))
        for fetcher in fetchers
    ]
    return sorted(health_items, key=lambda item: (item["category"], item["name"]))


@app.get("/api/source-states")
def get_source_states(
        status: Optional[str] = None,
        fetcher_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(SourceStateRecord)
        if status:
            query = query.where(SourceStateRecord.status == status)
        if fetcher_id:
            query = query.where(SourceStateRecord.fetcher_id == fetcher_id)
        query = query.order_by(SourceStateRecord.updated_at.desc()).offset(skip).limit(limit)
        return session.exec(query).all()


@app.post("/api/fetch/{fetcher_id}")
async def trigger_fetch_dynamic(fetcher_id: str, params: Dict[str, Any] = Body(...)):
    try:
        return await run_single_fetch_as_collection(
            fetcher_id,
            params,
            name=f"临时抓取: {fetcher_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ----------------- 向量化接口 -----------------
def _record_to_content(record: ArticleRecord) -> GenericContent:
    """将 ArticleRecord 转换为可向量化的 GenericContent 对象。"""
    obj = GenericContent(
        id=record.id, title=record.title, publish_date=record.publish_date,
        source_url=record.source_url, content=record.content,
        fetched_date=record.fetched_date, has_content=record.has_content,
    )
    obj.content_type = record.content_type
    obj.source_id = record.source_id
    return obj


@app.post("/api/vectorize/batch")
async def batch_vectorize_articles(params: BatchOpParams):
    success_count = 0
    for uid in params.ids:
        record = await db_sink.get(uid)
        if not record or record.is_vectorized: continue
        if await vector_sink.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(uid)
            success_count += 1
    return {"status": "success", "count": success_count}


@app.post("/api/vectorize/all-pending")
async def vectorize_all_pending():
    """对所有 is_vectorized=False 的文章执行向量化，跳过已索引的条目。"""
    with Session(db_sink.engine) as session:
        from sqlmodel import select as sm_select
        records = session.exec(
            sm_select(ArticleRecord).where(ArticleRecord.is_vectorized == False)
        ).all()
    success_count = 0
    for record in records:
        if await vector_sink.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(record.id)
            success_count += 1
    return {"status": "success", "count": success_count, "total_pending": len(records)}


@app.post("/api/vectorize/{article_id:path}")
async def vectorize_article(article_id: str):
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="文章不存在")
    if record.is_vectorized: return {"status": "skipped"}

    content_obj = _record_to_content(record)
    success = await vector_sink.save(content_obj)
    if success:
        await db_sink.mark_as_vectorized(article_id)
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="向量化处理失败")


# ==================== 3. 向量检索与状态 ====================
class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: float = 1.5   # T4: cosine distance 上限（越小越相关；>1.5 视为不相关）
    content_type: Optional[str] = None
    source_id: Optional[str] = None
    publish_date_gte: Optional[str] = None   # T5: 发布日期下限，格式 'YYYY-MM-DD'
    publish_date_lte: Optional[str] = None   # 发布日期上限，格式 'YYYY-MM-DD'
    rerank: bool = False                     # T12: cross-encoder 重排序


@app.post("/api/vector/search")
async def vector_search(query: SearchQuery):
    raw_results = await vector_sink.search(
        query.query,
        n_results=query.top_k * 4,
        content_type=query.content_type,
        source_id=query.source_id,
        publish_date_gte=query.publish_date_gte,
        publish_date_lte=query.publish_date_lte,
    )

    # T3: 按 parent_id 去重，保留相同文章中 distance 最小的 chunk（最相关那条）
    best_by_parent: Dict[str, Any] = {}
    for res in raw_results:
        pid = res["metadata"].get("parent_id", res["id"])
        if pid not in best_by_parent or res["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = res

    # T4: 过滤低相关性结果（distance 超过阈值则丢弃）
    candidates = [r for r in best_by_parent.values() if r["distance"] <= query.score_threshold]

    # T12: cross-encoder 重排序（可选）
    if query.rerank:
        candidates = vector_sink.rerank(query.query, candidates[:query.top_k * 2])
    else:
        candidates.sort(key=lambda x: x["distance"])

    unique_results = []
    for res in candidates[:query.top_k]:
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)
        if record:
            res["metadata"]["title"] = record.title
            res["metadata"]["source_url"] = record.source_url
            res["metadata"]["publish_date"] = record.publish_date
        else:
            res["metadata"]["title"] = f"未知文章 ({pid})"
        unique_results.append(res)

    return {"status": "success", "results": unique_results, "reranked": query.rerank}


@app.get("/api/vector/stats")
async def get_vector_stats():
    count = await vector_sink.count()
    return {"total_vectors": count}


@app.delete("/api/vector/{article_id:path}")
async def delete_vector_only(article_id: str):
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="记录不存在")
    if record.is_vectorized:
        await vector_sink.delete(article_id)
        await db_sink.mark_as_unvectorized(article_id)
    return {"status": "success"}


@app.post("/api/vector/batch-delete")
async def batch_delete_vectors(params: BatchOpParams):
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record and record.is_vectorized:
            await vector_sink.delete(uid)
            await db_sink.mark_as_unvectorized(uid)
    return {"status": "success"}


# ==================== 3b. RAG 检索上下文接口（供 Dify 等下游应用调用）====================

class RagContextQuery(BaseModel):
    query: str
    top_k: int = 5
    max_chars: int = 4000           # 上下文文本的最大字符数（控制 LLM token 预算）
    score_threshold: float = 1.5
    content_type: Optional[str] = None
    source_id: Optional[str] = None
    publish_date_gte: Optional[str] = None
    publish_date_lte: Optional[str] = None
    context_separator: str = "\n\n---\n\n"  # 各来源之间的分隔符
    rerank: bool = False            # T12: cross-encoder 重排序
    expand_context: bool = False    # T13: 拼接命中 chunk 的前后相邻 chunk 以扩展上下文


@app.post("/api/rag/context")
async def rag_context(query: RagContextQuery):
    """
    结构化检索上下文接口。
    返回组装好的 context_text（可直接注入 Dify/LLM prompt）及结构化 sources 列表。
    不调用任何 LLM，纯检索层输出。
    """
    from storage.impl.vector_storage import SOURCE_FRIENDLY_NAMES

    raw = await vector_sink.search(
        query.query,
        n_results=query.top_k * 4,
        content_type=query.content_type,
        source_id=query.source_id,
        publish_date_gte=query.publish_date_gte,
        publish_date_lte=query.publish_date_lte,
    )

    # 去重：同文章保留最优 chunk
    best_by_parent: Dict[str, Any] = {}
    for r in raw:
        pid = r["metadata"].get("parent_id", r["id"])
        if pid not in best_by_parent or r["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = r

    candidates = [r for r in best_by_parent.values() if r["distance"] <= query.score_threshold]

    # T12: cross-encoder 重排序（可选）
    if query.rerank:
        candidates = vector_sink.rerank(query.query, candidates[:query.top_k * 2])
    else:
        candidates.sort(key=lambda x: x["distance"])
    candidates = candidates[:query.top_k]

    # 组装结构化来源列表与上下文文本
    sources = []
    context_blocks = []
    total_chars = 0

    for rank, res in enumerate(candidates, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)

        source_id = res["metadata"].get("source_id", "")
        source_name = SOURCE_FRIENDLY_NAMES.get(source_id, source_id)
        pub_date = res["metadata"].get("publish_date", "")
        title = record.title if record else res["metadata"].get("title", "")
        source_url = record.source_url if record else ""

        # 摘录：取 chunk 文本，去掉头部（已有结构化字段），只保留正文部分
        raw_doc = res["document"]
        body_start = raw_doc.find("\n\n")
        excerpt = raw_doc[body_start + 2:].strip() if body_start != -1 else raw_doc.strip()

        # T13: 拼接前后相邻 chunk，扩展上下文窗口（可选）
        if query.expand_context:
            chunk_index = res["metadata"].get("chunk_index", 0)
            total_chunks = res["metadata"].get("total_chunks", 1)
            adj = await vector_sink.expand_chunk(pid, chunk_index, total_chunks)
            parts = []
            if adj["prev"]:
                parts.append(adj["prev"])
            parts.append(excerpt)
            if adj["next"]:
                parts.append(adj["next"])
            excerpt = "\n\n".join(parts)

        block = (
            f"[{rank}] 来源: {source_name} | 日期: {pub_date}\n"
            f"标题: {title}\n"
            f"链接: {source_url}\n\n"
            f"{excerpt}"
        )

        if total_chars + len(block) > query.max_chars and context_blocks:
            break

        context_blocks.append(block)
        total_chars += len(block)

        sources.append({
            "rank": rank,
            "parent_id": pid,
            "title": title,
            "source_id": source_id,
            "source_name": source_name,
            "publish_date": pub_date,
            "source_url": source_url,
            "distance": round(res["distance"], 4),
            "excerpt": excerpt[:500],
        })

    context_text = query.context_separator.join(context_blocks)

    return {
        "query": query.query,
        "context_text": context_text,
        "sources": sources,
        "total_chars": len(context_text),
        "retrieved_count": len(sources),
    }


@app.get("/api/rag/similar/{article_id:path}")
async def rag_similar(article_id: str, top_k: int = 5):
    """
    相似文章接口：找出与给定文章语义最接近的其他文章。
    用于"相关阅读"、知识图谱构建等场景。
    """
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章不存在")

    # 使用标题+正文片段作为查询向量
    query_text = f"{record.title}\n{(record.content or '')[:300]}"
    raw = await vector_sink.search(query_text, n_results=(top_k + 1) * 3)

    best_by_parent: Dict[str, Any] = {}
    for r in raw:
        pid = r["metadata"].get("parent_id", r["id"])
        if pid == article_id:
            continue  # 排除自身
        if pid not in best_by_parent or r["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = r

    candidates = sorted(best_by_parent.values(), key=lambda x: x["distance"])[:top_k]

    similar = []
    for rank, res in enumerate(candidates, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        rec = await db_sink.get(pid)
        similar.append({
            "rank": rank,
            "parent_id": pid,
            "title": rec.title if rec else res["metadata"].get("title", ""),
            "source_id": res["metadata"].get("source_id", ""),
            "publish_date": res["metadata"].get("publish_date", ""),
            "source_url": rec.source_url if rec else "",
            "distance": round(res["distance"], 4),
        })

    return {
        "article_id": article_id,
        "title": record.title,
        "similar": similar,
    }


@app.post("/api/vector/reindex-all")
async def reindex_all_articles():
    """
    T9: 删除并重建整个 ChromaDB collection，对所有文章重新向量化。
    适用于：更换 embedding 模型后的全库迁移。
    """
    vector_sink.rebuild_collection()

    with Session(db_sink.engine) as session:
        records = session.exec(select(ArticleRecord)).all()

    # 先批量重置 is_vectorized 标志
    for record in records:
        await db_sink.update(record.id, {"is_vectorized": False})

    # 重新向量化
    success_count = 0
    for record in records:
        if await vector_sink.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(record.id)
            success_count += 1

    return {"status": "success", "total_reindexed": success_count, "total_articles": len(records)}


# ==================== 4. 定时任务 ====================
class NodeGroupCreate(BaseModel):
    name: str
    description: str = ""
    fetcher_ids: List[str] = PydanticField(default_factory=list)
    params: Dict[str, Any] = PydanticField(default_factory=dict)
    per_fetcher_params: Dict[str, Dict[str, Any]] = PydanticField(default_factory=dict)
    cron_expr: str = ""
    per_fetcher_cron: Dict[str, str] = PydanticField(default_factory=dict)
    is_active: bool = True


class NodeGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    fetcher_ids: Optional[List[str]] = None
    params: Optional[Dict[str, Any]] = None
    per_fetcher_params: Optional[Dict[str, Dict[str, Any]]] = None
    cron_expr: Optional[str] = None
    per_fetcher_cron: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


class CollectionJobCreate(BaseModel):
    name: str
    description: str = ""
    group_id: Optional[int] = None
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
    group_id: Optional[int] = None
    fetcher_ids: Optional[List[str]] = None
    params: Optional[Dict[str, Any]] = None
    per_fetcher_params: Optional[Dict[str, Dict[str, Any]]] = None
    cron_expr: Optional[str] = None
    per_fetcher_cron: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None
    downstream_policy: Optional[Dict[str, Any]] = None


class TaskCreate(BaseModel):
    fetcher_id: str
    cron_expr: str
    params: dict


@app.get("/api/node-groups")
def get_node_groups(is_active: Optional[bool] = None):
    with Session(db_sink.engine) as session:
        query = select(NodeGroupRecord)
        if is_active is not None:
            query = query.where(NodeGroupRecord.is_active == is_active)
        query = query.order_by(NodeGroupRecord.name)
        return [serialize_node_group(record) for record in session.exec(query).all()]


@app.post("/api/node-groups")
def create_node_group(data: NodeGroupCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="节点组名称不能为空")
    now = _now_iso()
    with Session(db_sink.engine) as session:
        record = NodeGroupRecord(
            name=name,
            description=data.description.strip(),
            fetcher_ids_json=_json_dumps(normalize_fetcher_ids(data.fetcher_ids)),
            params_json=_json_dumps(data.params),
            per_fetcher_params_json=_json_dumps(data.per_fetcher_params),
            cron_expr=data.cron_expr.strip(),
            per_fetcher_cron_json=_json_dumps(data.per_fetcher_cron),
            is_active=data.is_active,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        result = serialize_node_group(record)
    load_tasks_to_scheduler()
    return result


@app.put("/api/node-groups/{group_id}")
def update_node_group(group_id: int, data: NodeGroupUpdate):
    with Session(db_sink.engine) as session:
        record = session.get(NodeGroupRecord, group_id)
        if not record:
            raise HTTPException(status_code=404, detail="节点组不存在")
        update_data = data.dict(exclude_unset=True)
        if "name" in update_data:
            name = (update_data["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="节点组名称不能为空")
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
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        result = serialize_node_group(record)
    load_tasks_to_scheduler()
    return result


@app.delete("/api/node-groups/{group_id}")
def delete_node_group(group_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(NodeGroupRecord, group_id)
        if not record:
            raise HTTPException(status_code=404, detail="节点组不存在")
        session.delete(record)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.post("/api/node-groups/{group_id}/fetch")
async def fetch_node_group(group_id: int):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="节点组不存在")
        if not group.is_active:
            raise HTTPException(status_code=400, detail="节点组已停用")
        items = build_node_group_items(group)
        group_name = group.name
    return await run_collection_items(
        items,
        name=f"临时抓取节点组: {group_name}",
        trigger_type="manual",
        group_id=group_id,
        run_scope="ad_hoc",
    )


@app.get("/api/collection-jobs")
def get_collection_jobs(is_active: Optional[bool] = None):
    with Session(db_sink.engine) as session:
        query = select(CollectionJobRecord)
        if is_active is not None:
            query = query.where(CollectionJobRecord.is_active == is_active)
        query = query.order_by(CollectionJobRecord.name)
        return [serialize_collection_job(record) for record in session.exec(query).all()]


@app.post("/api/collection-jobs")
def create_collection_job(data: CollectionJobCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="采集任务名称不能为空")
    if data.group_id is None and not normalize_fetcher_ids(data.fetcher_ids):
        raise HTTPException(status_code=400, detail="采集任务需要节点组或至少一个节点")
    now = _now_iso()
    with Session(db_sink.engine) as session:
        if data.group_id is not None and not session.get(NodeGroupRecord, data.group_id):
            raise HTTPException(status_code=404, detail="节点组不存在")
        record = CollectionJobRecord(
            name=name,
            description=data.description.strip(),
            group_id=data.group_id,
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
    load_tasks_to_scheduler()
    return serialize_collection_job(record)


@app.put("/api/collection-jobs/{job_id}")
def update_collection_job(job_id: int, data: CollectionJobUpdate):
    with Session(db_sink.engine) as session:
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
        if "group_id" in update_data:
            group_id = update_data["group_id"]
            if group_id is not None and not session.get(NodeGroupRecord, group_id):
                raise HTTPException(status_code=404, detail="节点组不存在")
            record.group_id = group_id
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
    load_tasks_to_scheduler()
    return serialize_collection_job(record)


@app.delete("/api/collection-jobs/{job_id}")
def delete_collection_job(job_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(CollectionJobRecord, job_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        session.delete(record)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.post("/api/collection-jobs/{job_id}/run")
async def run_collection_job_now(job_id: int):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        items = build_collection_job_items(job, session)
        if not items:
            raise HTTPException(status_code=400, detail="采集任务没有可执行节点")
        job_name = job.name
        group_id = job.group_id
    return await run_collection_items(
        items,
        name=job_name,
        trigger_type="manual",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


@app.post("/api/collection-jobs/migrate-legacy-tasks")
def migrate_legacy_tasks_to_collection_jobs():
    created = 0
    now = _now_iso()
    with Session(db_sink.engine) as session:
        tasks = session.exec(select(FetchTaskRecord)).all()
        for task in tasks:
            existing = session.exec(
                select(CollectionJobRecord).where(CollectionJobRecord.legacy_task_id == task.id)
            ).first()
            if existing:
                continue
            fetcher_class = fetcher_registry.get_class(task.fetcher_id)
            name = fetcher_class.name if fetcher_class else task.fetcher_id
            record = CollectionJobRecord(
                name=f"{name} 定时采集",
                description="由旧版单节点定时任务迁移生成。",
                fetcher_ids_json=_json_dumps([task.fetcher_id]),
                params_json=task.params_json,
                cron_expr=task.cron_expr,
                is_active=False,
                legacy_task_id=task.id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            created += 1
        session.commit()
    return {"status": "success", "created": created}


@app.get("/api/collection-job-runs")
def get_collection_job_runs(
        job_id: Optional[int] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        run_scope: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
):
    with Session(db_sink.engine) as session:
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


@app.get("/api/collection-job-runs/{job_run_id}")
def get_collection_job_run(job_run_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(CollectionJobRunRecord, job_run_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集运行记录不存在")
        child_run_ids = _json_loads(record.child_run_ids_json, [])
        child_runs = []
        if child_run_ids:
            child_runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.id.in_(child_run_ids))).all()
        return {**serialize_collection_job_run(record), "child_runs": child_runs}


@app.get("/api/tasks")
def get_tasks():
    with Session(db_sink.engine) as session:
        return session.exec(select(FetchTaskRecord)).all()


@app.post("/api/tasks")
def create_task(task_data: TaskCreate):
    with Session(db_sink.engine) as session:
        task = FetchTaskRecord(
            fetcher_id=task_data.fetcher_id, cron_expr=task_data.cron_expr,
            params_json=json.dumps(task_data.params), created_at=datetime.datetime.now().isoformat()
        )
        session.add(task)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with Session(db_sink.engine) as session:
        task = session.get(FetchTaskRecord, task_id)
        if task:
            session.delete(task)
            session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


# ==================== 5. 抓取运行历史 ====================
@app.get("/api/fetch-runs")
def get_fetch_runs(
        fetcher_id: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
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


@app.get("/api/fetch-runs/{run_id}")
def get_fetch_run(run_id: int):
    with Session(db_sink.engine) as session:
        run = session.get(FetchRunRecord, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="抓取运行记录不存在")
        return run


# ==================== MCP Server Management ====================

_MCP_TOOLS_MANIFEST = [
    {"name": "search_articles",
     "description": "语义向量搜索文章，支持中英文，可按日期/来源/类型过滤"},
    {"name": "browse_articles",
     "description": "按条件过滤浏览文章列表（来源、类型、日期区间），适合日报生成"},
    {"name": "get_article",
     "description": "按 ID 获取单篇文章完整内容（含正文）"},
    {"name": "list_sources",
     "description": "列出所有已知数据来源，获取可用的 source_id 和 content_type"},
    {"name": "get_rag_context",
     "description": "语义检索后组装格式化 RAG 上下文字符串，可直接拼入 LLM Prompt"},
]


@app.get("/api/mcp/status")
def get_mcp_status(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "enabled": _mcp_enabled,
        "url": f"{base}/mcp",
        "tools": _MCP_TOOLS_MANIFEST,
    }


@app.post("/api/mcp/toggle")
def toggle_mcp():
    global _mcp_enabled
    _mcp_enabled = not _mcp_enabled
    with Session(db_sink.engine) as session:
        rec = session.get(AppSettingRecord, "mcp_enabled")
        if rec is None:
            rec = AppSettingRecord(key="mcp_enabled", value=str(_mcp_enabled).lower())
            session.add(rec)
        else:
            rec.value = str(_mcp_enabled).lower()
            session.add(rec)
        session.commit()
    return {"enabled": _mcp_enabled}
