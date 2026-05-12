# /src/api/app.py

import os
import json
import datetime
from fastapi import FastAPI, HTTPException, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field as PydanticField
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage
from pipeline.core import DataPipeline
from models.db import ArticleRecord, FetchTaskRecord, FetchRunRecord, SourceConfigRecord, SourceStateRecord
from models.content import BaseContent, SocialPostContent

# 引入动态抓取器注册中心
from fetchers.registry import fetcher_registry


class GenericContent(BaseContent):
    # 拆分为结构类型与来源通道
    content_type = "restored_from_db"
    source_id = "database_restore"


app = FastAPI(title="Dorami 数据归档中枢 API")

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


def create_fetch_run(fetcher_id: str, params: dict, trigger_type: str, task_id: Optional[int] = None) -> int:
    with Session(db_sink.engine) as session:
        run = FetchRunRecord(
            fetcher_id=fetcher_id,
            task_id=task_id,
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
    try:
        await run_fetcher_with_tracking(fetcher_id, params, trigger_type="scheduled", task_id=task_id)
    except ValueError as e:
        print(f"❌ {e}")
    except Exception as e:
        print(f"❌ 定时任务执行失败: {e}")
        raise


def load_tasks_to_scheduler():
    scheduler.remove_all_jobs()
    with Session(db_sink.engine) as session:
        tasks = session.exec(select(FetchTaskRecord).where(FetchTaskRecord.is_active == True)).all()
        for task in tasks:
            params = json.loads(task.params_json)
            parts = task.cron_expr.split()
            if len(parts) == 5:
                trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3],
                                      day_of_week=parts[4])
                scheduler.add_job(
                    execute_fetch_job, trigger, args=[task.fetcher_id, params, task.id], id=f"task_{task.id}",
                    replace_existing=True
                )


@app.on_event("startup")
async def startup_event():
    load_tasks_to_scheduler()
    scheduler.start()
    print("⏰ APScheduler 定时调度引擎已启动！")


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
        task_id: Optional[int] = None
) -> Dict[str, Any]:
    run_id = create_fetch_run(fetcher_id, params, trigger_type=trigger_type, task_id=task_id)
    mark_source_state_started(fetcher_id, params, run_id)
    fetcher_class = fetcher_registry.get_class(fetcher_id)
    if not fetcher_class:
        message = f"未知的抓取器节点: {fetcher_id}"
        finish_fetch_run(run_id, status="failed", error_message=message)
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=message)
        raise ValueError(message)

    try:
        fetcher = fetcher_class()
        result = await pipeline.run_task(fetcher, **params)
        finish_fetch_run(run_id, status="success", result=result)
        mark_source_state_finished(fetcher_id, params, run_id, status="success", result=result)
        return {
            "status": "success",
            "run_id": run_id,
            "fetcher_id": fetcher_id,
            "fetched_count": result.fetched_count,
            "saved_count": result.saved_count,
            "skipped_count": result.skipped_count
        }
    except Exception as e:
        finish_fetch_run(run_id, status="failed", error_message=str(e))
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=e)
        raise


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
        result = await run_fetcher_with_tracking(fetcher_id, params, trigger_type="manual")
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

    for record in records:
        fetcher_id = resolve_source_fetcher_id(record)
        if not fetcher_id:
            results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue

        try:
            params = build_source_fetch_params(record, body.params if body else {})
            result = await run_fetcher_with_tracking(fetcher_id, params, trigger_type="manual")
            results.append({"source_id": record.source_id, **result})
        except Exception as e:
            results.append({"source_id": record.source_id, "status": "failed", "error": str(e)})

    return {"status": "success", "count": len(results), "results": results}


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
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_id=source_id,
            source_ids=source_ids,
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
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_id=source_id,
            source_ids=source_ids,
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
        return await run_fetcher_with_tracking(fetcher_id, params, trigger_type="manual")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ----------------- 向量化接口 -----------------
@app.post("/api/vectorize/batch")
async def batch_vectorize_articles(params: BatchOpParams):
    success_count = 0
    for uid in params.ids:
        record = await db_sink.get(uid)
        if not record or record.is_vectorized: continue

        content_obj = GenericContent(
            id=record.id, title=record.title, publish_date=record.publish_date,
            source_url=record.source_url, content=record.content, fetched_date=record.fetched_date,
            has_content=record.has_content
        )
        content_obj.content_type = record.content_type
        content_obj.source_id = record.source_id

        if await vector_sink.save(content_obj):
            await db_sink.mark_as_vectorized(uid)
            success_count += 1
    return {"status": "success", "count": success_count}


@app.post("/api/vectorize/{article_id:path}")
async def vectorize_article(article_id: str):
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="文章不存在")
    if record.is_vectorized: return {"status": "skipped"}

    content_obj = GenericContent(
        id=record.id, title=record.title, publish_date=record.publish_date,
        source_url=record.source_url, content=record.content, fetched_date=record.fetched_date,
        has_content=record.has_content
    )
    content_obj.content_type = record.content_type
    content_obj.source_id = record.source_id

    success = await vector_sink.save(content_obj)
    if success:
        await db_sink.mark_as_vectorized(article_id)
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="向量化处理失败")


# ==================== 3. 向量检索与状态 ====================
class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    content_type: Optional[str] = None
    source_id: Optional[str] = None


@app.post("/api/vector/search")
async def vector_search(query: SearchQuery):
    raw_results = await vector_sink.search(
        query.query,
        n_results=query.top_k * 4,
        content_type=query.content_type,
        source_id=query.source_id
    )

    unique_results = []
    seen_parents = set()

    for res in raw_results:
        parent_id = res["metadata"].get("parent_id")

        if parent_id not in seen_parents:
            if parent_id:
                seen_parents.add(parent_id)
                record = await db_sink.get(parent_id)
                if record:
                    res["metadata"]["title"] = record.title
                else:
                    res["metadata"]["title"] = f"未知文章 ({parent_id})"

            unique_results.append(res)

        if len(unique_results) >= query.top_k:
            break

    return {"status": "success", "results": unique_results}


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


# ==================== 4. 定时任务 ====================
class TaskCreate(BaseModel):
    fetcher_id: str
    cron_expr: str
    params: dict


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
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(FetchRunRecord)
        if fetcher_id:
            query = query.where(FetchRunRecord.fetcher_id == fetcher_id)
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
