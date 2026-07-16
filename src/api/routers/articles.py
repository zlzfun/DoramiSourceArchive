"""文章档案 Router（知识台账 CRUD + 单条读取/手工录入/批量删除）。

阶段1 从 app.py 迁出的 /api/articles* 端点（路径不变）：
- GET  /api/articles                —— 列表/查询（含订阅作用域 off|only|prioritize）
- GET  /api/articles/{id}           —— 单条详情
- POST /api/articles                —— 手工录入
- PUT  /api/articles/{id}           —— 更新（改 content/title 重置向量状态并清块）
- DELETE /api/articles/{id}         —— 删除（清向量块 + 必要时回退日报游标）
- POST /api/articles/batch-delete   —— 批量删除

说明：article import（归档同步）与 /api/feed/articles[.md]（依赖采集投递作用域
helper）暂留 app.py。数据访问经 deps.get_session()/deps.get_db_sink()/
deps.get_vector_sink_optional()；current_username 经 _app() 延迟动态调用。
"""

import importlib
import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from api import deps
from api.articles_view import (
    GenericContent,
    apply_article_query_filters,
    article_recency_order,
    article_to_markdown,
    serialize_article_list_item,
    serialize_feed_article,
)
from api.collection_planning import resolve_delivery_source_ids
from api.feed_service import resolve_subscribed_source_ids
from api.schemas import BatchOpParams
from api.sources import (
    BULLETIN_CONTENT_TYPES,
    DAILY_BRIEF_SOURCE_ID,
    bulletin_registry_source_ids,
)
from api.textutils import _json_loads
from models.db import ArticleRecord
from services import reader_state as reader_state_service

router = APIRouter(tags=["articles"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的 current_username）。"""
    return importlib.import_module("api.app")


def _bulletin_shape_condition():
    """「动态形」文章的 SQL 条件：源级标记（注册表 bulletin 源）∪ content_type 兜底
    （覆盖注册表之外的历史归档源）。article 形 = 取反。"""
    conditions = [ArticleRecord.content_type.in_(sorted(BULLETIN_CONTENT_TYPES))]
    bulletin_sources = bulletin_registry_source_ids()
    if bulletin_sources:
        conditions.append(ArticleRecord.source_id.in_(bulletin_sources))
    return or_(*conditions)


@router.get("/api/articles")
def get_articles(
        request: Request,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,  # CSV:多类型筛选(类型归组分面用),与 feed 契约对齐
        source_id: Optional[str] = None,
        exclude_source_ids: Optional[str] = None,  # CSV：从结果中排除的来源（如知识台账排除日报源）
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        index_status: Optional[str] = None,
        has_content: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        subscribed_scope: str = "off",  # off | only | prioritize：相对当前用户订阅的源
        shape: Optional[str] = None,  # article | bulletin：内容形态分流（阅读器文章/动态视图）
        unread_only: bool = False,  # 只看未读（按当前用户订阅源的水位+逐篇已读判定）
        with_unread: bool = False,  # 给返回条目附 unread 标记（页级，reader 列表用）
        skip: int = 0,
        limit: int = 100,
        include_total: bool = False,
        include_content: bool = True,
        session: Session = Depends(deps.get_session),
):
    scope = (subscribed_scope or "off").strip().lower()
    safe_limit = min(max(int(limit), 1), 500)
    safe_skip = max(int(skip), 0)
    username = (
        _app().current_username(request)
        if (scope in {"only", "prioritize"} or unread_only or with_unread) else ""
    )
    subscribed_ids = (
        resolve_subscribed_source_ids(session, username)
        if scope in {"only", "prioritize"} or unread_only else []
    )
    filter_kwargs = {
        "content_type": content_type,
        "content_types": content_types,
        "source_id": source_id,
        "exclude_source_ids": exclude_source_ids,
        "job_id": job_id,
        "job_run_id": job_run_id,
        "fetch_run_id": fetch_run_id,
        "run_scope": run_scope,
        "is_vectorized": is_vectorized,
        "index_status": index_status,
        "has_content": has_content,
        "search": search,
        "publish_date_start": publish_date_start,
        "publish_date_end": publish_date_end,
        "fetched_date_start": fetched_date_start,
        "fetched_date_end": fetched_date_end,
    }
    query = apply_article_query_filters(select(ArticleRecord), **filter_kwargs)
    count_query = apply_article_query_filters(select(func.count(ArticleRecord.id)), **filter_kwargs)
    if scope == "only":
        # 仅当前用户已订阅的源；无订阅时显式返回空集。
        query = query.where(ArticleRecord.source_id.in_(subscribed_ids or ["__none__"]))
        count_query = count_query.where(ArticleRecord.source_id.in_(subscribed_ids or ["__none__"]))
    shape_value = (shape or "").strip().lower()
    if shape_value in {"article", "bulletin"}:
        bulletin_cond = _bulletin_shape_condition()
        cond = bulletin_cond if shape_value == "bulletin" else ~bulletin_cond
        query = query.where(cond)
        count_query = count_query.where(cond)
    if unread_only:
        # 未读 = 订阅源内 fetched_date 越过水位 ∧ 未逐篇读过；无可判定源时显式空集。
        unread_cond = reader_state_service.unread_filter_condition(
            session, username=username, source_ids=subscribed_ids
        )
        if unread_cond is None:
            query = query.where(ArticleRecord.id.in_(["__none__"]))
            count_query = count_query.where(ArticleRecord.id.in_(["__none__"]))
        else:
            query = query.where(unread_cond)
            count_query = count_query.where(unread_cond)
    if scope == "prioritize" and subscribed_ids:
        subscribed_first = case((ArticleRecord.source_id.in_(subscribed_ids), 0), else_=1)
        query = query.order_by(*article_recency_order(subscribed_first))
    else:
        query = query.order_by(*article_recency_order())
    total = int(session.exec(count_query).one() or 0) if include_total else None
    records = session.exec(query.offset(safe_skip).limit(safe_limit)).all()
    items = [serialize_article_list_item(record, include_content=include_content) for record in records]
    if with_unread:
        # 页级未读标记：只读现有水位（不写库）；水位由 /api/reader/unread-counts 挂载校准。
        unread_ids = reader_state_service.unread_ids_among(
            session, username=username, records=records
        )
        for item in items:
            item["unread"] = item.get("id") in unread_ids
    if not include_total:
        return items
    return {
        "items": items,
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
        "next_skip": safe_skip + len(records) if safe_skip + len(records) < total else None,
    }


@router.get("/api/articles/facets")
def get_article_facets(
        exclude_source_ids: Optional[str] = None,
        session: Session = Depends(deps.get_session),
):
    """分面目录:content_type / source_id 的全量 group-by 计数。

    台账分面栏的单一数据源——选项必须来自全量归档而非当前页,
    点击当前页不存在的类别也能对全量筛选;计数即样页分面右侧的 `.n`。
    访问口径与 GET /api/articles 相同(路径共享 reader/collector 判定)。
    """
    excludes = [x.strip() for x in (exclude_source_ids or "").split(",") if x.strip()]

    def _facet(column):
        q = select(column, func.count(ArticleRecord.id)).group_by(column)
        if excludes:
            q = q.where(ArticleRecord.source_id.notin_(excludes))
        rows = session.exec(q).all()
        return [
            {"value": value, "count": int(count)}
            for value, count in sorted(rows, key=lambda r: -r[1])
            if value
        ]

    total_q = select(func.count(ArticleRecord.id))
    if excludes:
        total_q = total_q.where(ArticleRecord.source_id.notin_(excludes))
    return {
        "total": int(session.exec(total_q).one() or 0),
        "content_types": _facet(ArticleRecord.content_type),
        "source_ids": _facet(ArticleRecord.source_id),
    }


@router.get("/api/articles/{article_id:path}")
async def get_article(article_id: str):
    record = await deps.get_db_sink().get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章未找到")
    return serialize_article_list_item(record, include_content=True)


@router.post("/api/articles")
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
    except Exception:
        pass

    success = await deps.get_db_sink().save(content_obj)
    if not success:
        raise HTTPException(status_code=400, detail="该条目 ID 已存在，请避免重复录入")

    return {"status": "success"}


def _maybe_rewind_daily_brief_cursor(record) -> None:
    """删除日报源记录时，若它正是最后推进游标的那一期，则把增量游标回退到
    生成该期之前的值（记录里存了 cursor_before / cursor_after），使删除最新一期
    后可直接重新生成。删除历史中间某期（cursor_after 不等于当前游标）则不动游标。
    """
    if getattr(record, "source_id", None) != DAILY_BRIEF_SOURCE_ID:
        return
    from services import daily_brief as daily_brief_service

    ext = _json_loads(record.extensions_json, {})
    cursor_after = ext.get("cursor_after")
    if not cursor_after:
        return
    with Session(deps.get_db_sink().engine) as session:
        if daily_brief_service.read_cursor(session) == cursor_after:
            daily_brief_service.set_setting(
                session, daily_brief_service.KEY_CURSOR, ext.get("cursor_before") or ""
            )


@router.delete("/api/articles/{article_id:path}")
async def delete_article(article_id: str):
    db_sink = deps.get_db_sink()
    vector_sink = deps.get_vector_sink_optional()
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章未找到")
    if record.is_vectorized and vector_sink is not None:
        await vector_sink.delete(article_id)
    await db_sink.delete(article_id)
    _maybe_rewind_daily_brief_cursor(record)
    return {"status": "success"}


@router.post("/api/articles/batch-delete")
async def batch_delete_articles(params: BatchOpParams):
    db_sink = deps.get_db_sink()
    vector_sink = deps.get_vector_sink_optional()
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record:
            if record.is_vectorized and vector_sink is not None:
                await vector_sink.delete(uid)
            await db_sink.delete(uid)
            _maybe_rewind_daily_brief_cursor(record)
    return {"status": "success"}


class ArticleUpdateParams(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    source_url: Optional[str] = None
    extensions_json: Optional[str] = None


@router.put("/api/articles/{article_id:path}")
async def update_article(article_id: str, params: ArticleUpdateParams):
    db_sink = deps.get_db_sink()
    vector_sink = deps.get_vector_sink_optional()
    update_data = {k: v for k, v in params.dict().items() if v is not None}
    if "content" in update_data or "title" in update_data:
        # 内容/标题改动使已有向量失效：清 chunk 并标陈旧（stale 仍会被 all-pending 重新拾取）。
        update_data["is_vectorized"] = False
        update_data["index_status"] = "stale"
        if vector_sink is not None:
            await vector_sink.delete(article_id)

    success = await db_sink.update(article_id, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="更新失败")
    return {"status": "success"}


# ==================== 投递视图（/api/feed/articles[.md]）====================
# 下游 LLM/RAG 消费者推荐契约：按采集投递作用域（source/job）过滤后的档案视图。
# （?group_id= 作用域随节点组退役移除——实体简化阶段 2。）

@router.get("/api/feed/articles")
def get_feed_articles(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
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
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    safe_limit = min(max(limit, 1), 500)
    delivery_source_ids = resolve_delivery_source_ids(
        session, source_id=source_id, source_ids=source_ids, job_id=job_id
    )
    if (source_id or source_ids or job_id is not None) and not delivery_source_ids:
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
        "items": [serialize_feed_article(record, include_content=include_content) for record in records],
    }


@router.get("/api/feed/articles.md")
def export_feed_articles_markdown(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
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
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    safe_limit = min(max(limit, 1), 200)
    delivery_source_ids = resolve_delivery_source_ids(
        session, source_id=source_id, source_ids=source_ids, job_id=job_id
    )
    if (source_id or source_ids or job_id is not None) and not delivery_source_ids:
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
