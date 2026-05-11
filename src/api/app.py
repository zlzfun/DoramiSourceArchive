# /src/api/app.py

import os
import json
import datetime
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field as PydanticField
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage
from pipeline.core import DataPipeline
from models.db import ArticleRecord, FetchTaskRecord, FetchRunRecord, SourceConfigRecord
from models.content import BaseContent

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


async def run_fetcher_with_tracking(
        fetcher_id: str,
        params: Dict[str, Any],
        trigger_type: str = "manual",
        task_id: Optional[int] = None
) -> Dict[str, Any]:
    run_id = create_fetch_run(fetcher_id, params, trigger_type=trigger_type, task_id=task_id)
    fetcher_class = fetcher_registry.get_class(fetcher_id)
    if not fetcher_class:
        message = f"未知的抓取器节点: {fetcher_id}"
        finish_fetch_run(run_id, status="failed", error_message=message)
        raise ValueError(message)

    try:
        fetcher = fetcher_class()
        result = await pipeline.run_task(fetcher, **params)
        finish_fetch_run(run_id, status="success", result=result)
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
        if content_type: query = query.where(ArticleRecord.content_type == content_type)
        if source_id: query = query.where(ArticleRecord.source_id == source_id)
        if is_vectorized is not None: query = query.where(ArticleRecord.is_vectorized == is_vectorized)
        if search: query = query.where(ArticleRecord.title.contains(search))

        # ✨ 日期区间过滤 (巧妙附加 23:59:59 将结束日的最后一秒包揽进来)
        if publish_date_start:
            query = query.where(ArticleRecord.publish_date >= publish_date_start)
        if publish_date_end:
            end_time = publish_date_end if "T" in publish_date_end else f"{publish_date_end}T23:59:59"
            query = query.where(ArticleRecord.publish_date <= end_time)

        if fetched_date_start:
            query = query.where(ArticleRecord.fetched_date >= fetched_date_start)
        if fetched_date_end:
            end_time = fetched_date_end if "T" in fetched_date_end else f"{fetched_date_end}T23:59:59"
            query = query.where(ArticleRecord.fetched_date <= end_time)

        query = query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(limit)
        return session.exec(query).all()


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
