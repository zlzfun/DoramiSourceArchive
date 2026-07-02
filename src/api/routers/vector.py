"""向量检索 / 向量化 / RAG 上下文 Router。

阶段1 从 app.py 迁出的 /api/vector*、/api/vectorize*、/api/rag* 端点（路径不变）：
向量化（单条/批量/all-pending 后台任务）、自动向量化开关、语义检索（含登录用户
订阅硬作用域）、向量统计/删除、RAG 结构化上下文、相似文章、全库重建索引。

构建/管理类端点 collector(admin) 网关、search/stats/subscribed-stats reader 网关，
均仍由中间件统一强制（READER_API_PREFIXES 短路特判 search/stats/subscribed-stats）。

向量 sink 经 deps.get_vector_sink()（RAG 关闭时抛 503）/ get_vector_sink_optional()
（旁路判空）；db 经 deps.get_db_sink()/get_session()；current_username /
current_auth_session 经 _app() 延迟动态调用（保持测试 monkeypatch 兼容、避免成环）。
run_vector_search / RagContextQuery / auto_vectorize_after_fetch 经 app.py re-export
供其它 Router 与抓取钩子复用。
"""

import asyncio
import importlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from api import deps
from api.articles_view import _record_to_content
from api.feed_service import resolve_subscribed_source_ids
from api.schemas import BatchOpParams
from models.db import AppSettingRecord, ArticleRecord
from services import background_jobs

router = APIRouter(tags=["vector"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的 current_username/current_auth_session）。"""
    return importlib.import_module("api.app")


# ==================== 向量化 ====================

@router.post("/api/vectorize/batch")
async def batch_vectorize_articles(params: BatchOpParams):
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    success_count = 0
    for uid in params.ids:
        record = await db_sink.get(uid)
        if not record or record.is_vectorized:
            continue
        if await vs.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(uid)
            success_count += 1
    return {"status": "success", "count": success_count}


@router.post("/api/vectorize/all-pending")
async def vectorize_all_pending():
    """对所有 is_vectorized=False 的文章执行向量化，跳过已索引的条目。

    全量向量化可能耗时数分钟到数小时，改为提交后台任务并立即返回 job_id；
    前端轮询 GET /api/jobs/{job_id} 获取进度与最终结果（count/total_pending）。
    """
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    with Session(db_sink.engine) as session:
        records = session.exec(
            select(ArticleRecord).where(ArticleRecord.is_vectorized == False)  # noqa: E712
        ).all()
    pending_ids = [record.id for record in records]

    async def _work(job: background_jobs.Job) -> Dict[str, Any]:
        job.set_total(len(pending_ids))
        success_count = 0
        for article_id in pending_ids:
            record = await db_sink.get(article_id)
            if record and not record.is_vectorized:
                if await vs.save(_record_to_content(record)):
                    await db_sink.mark_as_vectorized(article_id)
                    success_count += 1
            job.advance()
        return {"count": success_count, "total_pending": len(pending_ids)}

    job = background_jobs.launch("vectorize_all_pending", _work)
    return {"status": "accepted", "job_id": job.id, "total_pending": len(pending_ids)}


@router.post("/api/vectorize/{article_id:path}")
async def vectorize_article(article_id: str):
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章不存在")
    if record.is_vectorized:
        return {"status": "skipped"}

    content_obj = _record_to_content(record)
    success = await vs.save(content_obj)
    if success:
        await db_sink.mark_as_vectorized(article_id)
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="向量化处理失败")


# ==================== 订阅范围向量化进度 ====================

@router.get("/api/vector/subscribed-stats")
def subscribed_vector_stats(request: Request, session: Session = Depends(deps.get_session)):
    """当前用户订阅范围内的向量化进度（用于「向量雷达」的范围内构建）。"""
    username = _app().current_username(request)
    source_ids = resolve_subscribed_source_ids(session, username) if username else []
    if not source_ids:
        return {"subscribed_source_count": 0, "total": 0, "vectorized": 0, "pending": 0}
    total = session.exec(
        select(func.count(ArticleRecord.id)).where(ArticleRecord.source_id.in_(source_ids))
    ).one()
    vectorized = session.exec(
        select(func.count(ArticleRecord.id)).where(
            ArticleRecord.source_id.in_(source_ids),
            ArticleRecord.is_vectorized == True,  # noqa: E712
        )
    ).one()
    total = int(total or 0)
    vectorized = int(vectorized or 0)
    return {
        "subscribed_source_count": len(source_ids),
        "total": total,
        "vectorized": vectorized,
        "pending": total - vectorized,
    }


# ==================== 自动向量化开关 ====================

AUTO_VECTORIZE_SETTING_KEY = "auto_vectorize"


def is_auto_vectorize_enabled() -> bool:
    with Session(deps.get_db_sink().engine) as session:
        record = session.get(AppSettingRecord, AUTO_VECTORIZE_SETTING_KEY)
        return bool(record and record.value.lower() == "true")


async def auto_vectorize_after_fetch(content_ids: List[str]) -> None:
    """抓取保存后，如管理员开启了自动向量化，则把新入库文章写入向量库。

    失败不影响抓取主流程（向量化是尽力而为的旁路）。
    RAG 关闭时（vector_sink 为 None）直接 no-op。
    """
    vector_sink = deps.get_vector_sink_optional()
    if not content_ids or vector_sink is None or not is_auto_vectorize_enabled():
        return
    db_sink = deps.get_db_sink()
    for content_id in content_ids:
        try:
            record = await db_sink.get(content_id)
            if record and not record.is_vectorized:
                if await vector_sink.save(_record_to_content(record)):
                    await db_sink.mark_as_vectorized(content_id)
        except Exception as exc:  # noqa: BLE001 — 旁路任务不应中断抓取
            print(f"⚠️ 自动向量化失败 (id={content_id}): {exc}")


class AutoVectorizeConfig(BaseModel):
    enabled: bool


@router.get("/api/vector/auto-vectorize")
def get_auto_vectorize_config():
    """读取「抓取后自动向量化」开关（管理员配置）。"""
    deps.get_vector_sink()
    return {"enabled": is_auto_vectorize_enabled()}


@router.post("/api/vector/auto-vectorize")
def set_auto_vectorize_config(config: AutoVectorizeConfig, session: Session = Depends(deps.get_session)):
    """设置「抓取后自动向量化」开关。开启后，后续抓取入库的文章会自动写入向量库。"""
    deps.get_vector_sink()
    record = session.get(AppSettingRecord, AUTO_VECTORIZE_SETTING_KEY)
    if record is None:
        record = AppSettingRecord(key=AUTO_VECTORIZE_SETTING_KEY, value="")
    record.value = "true" if config.enabled else "false"
    session.add(record)
    session.commit()
    return {"enabled": config.enabled}


# ==================== 向量检索与状态 ====================

class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: float = 1.5   # T4: cosine distance 上限（越小越相关；>1.5 视为不相关）
    content_type: Optional[str] = None
    source_id: Optional[str] = None
    publish_date_gte: Optional[str] = None   # T5: 发布日期下限，格式 'YYYY-MM-DD'
    publish_date_lte: Optional[str] = None   # 发布日期上限，格式 'YYYY-MM-DD'
    rerank: bool = False                     # T12: cross-encoder 重排序


def enforced_search_scope(request: Request) -> tuple[Optional[List[str]], bool]:
    """登录用户的语义检索硬性限定在其订阅来源内。

    返回 (source_ids, enforced)：
      - enforced=True 且 source_ids=[] → 用户无订阅，调用方应返回空结果；
      - enforced=True 且 source_ids=[...] → 限定到这些来源；
      - enforced=False → 不做限定（管理员超级用户检索全库；未启用鉴权同理）。

    只有受限的 ``user`` 账号被硬性限定在其订阅范围内；``admin`` 作为超级用户检索全部归档。
    """
    session = _app().current_auth_session(request)
    username = str(session.get("sub")) if session else ""
    role = session.get("role") if session else None
    if not username or role != "user":
        return None, False
    with Session(deps.get_db_sink().engine) as session_db:
        return resolve_subscribed_source_ids(session_db, username), True


def resolve_scoped_search_args(
        request: Request,
        requested_source_id: Optional[str],
) -> tuple[Optional[str], Optional[List[str]], bool, bool]:
    """把"硬性订阅范围"折算成传给检索流水线的 (source_id, source_ids)。

    返回 (source_id, source_ids, enforced, empty)。empty=True 表示应直接返回空结果
    （用户无订阅，或请求的 source_id 落在订阅范围之外）。
    """
    scope_ids, enforced = enforced_search_scope(request)
    if not enforced:
        return requested_source_id, None, False, False
    if not scope_ids:
        return None, None, True, True
    if requested_source_id:
        if requested_source_id not in set(scope_ids):
            return None, None, True, True
        return None, [requested_source_id], True, False
    return None, scope_ids, True, False


async def run_vector_search(
        query_text: str,
        top_k: int,
        score_threshold: float = 1.5,
        rerank: bool = False,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
        publish_date_gte: Optional[str] = None,
        publish_date_lte: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """语义检索流水线：召回 → 按 parent_id 去重 → 阈值过滤 → 重排序 → 回填标题。"""
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    raw_results = await vs.search(
        query_text,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=publish_date_gte,
        publish_date_lte=publish_date_lte,
    )

    # T3: 按 parent_id 去重，保留相同文章中 distance 最小的 chunk（最相关那条）
    best_by_parent: Dict[str, Any] = {}
    for res in raw_results:
        pid = res["metadata"].get("parent_id", res["id"])
        if pid not in best_by_parent or res["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = res

    # T4: 过滤低相关性结果（distance 超过阈值则丢弃）
    candidates = [r for r in best_by_parent.values() if r["distance"] <= score_threshold]

    # T12: cross-encoder 重排序（可选）
    if rerank:
        candidates = await vs.rerank(query_text, candidates[:top_k * 2])
    else:
        candidates.sort(key=lambda x: x["distance"])

    unique_results = []
    for res in candidates[:top_k]:
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)
        if record:
            res["metadata"]["title"] = record.title
            res["metadata"]["source_url"] = record.source_url
            res["metadata"]["publish_date"] = record.publish_date
        else:
            res["metadata"]["title"] = f"未知文章 ({pid})"
        unique_results.append(res)
    return unique_results


@router.post("/api/vector/search")
async def vector_search(query: SearchQuery, request: Request):
    # 登录用户的检索硬性限定在其订阅来源范围内（无订阅则空集）。
    source_id, source_ids, scoped, empty = resolve_scoped_search_args(request, query.source_id)
    if empty:
        return {"status": "success", "results": [], "reranked": query.rerank, "scoped": True}

    unique_results = await run_vector_search(
        query.query,
        top_k=query.top_k,
        score_threshold=query.score_threshold,
        rerank=query.rerank,
        content_type=query.content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=query.publish_date_gte,
        publish_date_lte=query.publish_date_lte,
    )
    return {"status": "success", "results": unique_results, "reranked": query.rerank, "scoped": scoped}


@router.get("/api/vector/stats")
async def get_vector_stats():
    vs = deps.get_vector_sink()
    count = await vs.count()
    return {"total_vectors": count}


@router.get("/api/vector/reconcile")
async def reconcile_vector_report():
    """SQLite↔Chroma 对账（只读报告，不修复）：列出两侧向量化状态漂移。"""
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    from services import vector_reconcile
    return await vector_reconcile.reconcile(db_sink, vs, repair=False)


@router.post("/api/vector/reconcile")
async def reconcile_vector_repair():
    """SQLite↔Chroma 对账并修复：复位丢索引标记、采纳孤立标记、清除孤儿 chunk。"""
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    from services import vector_reconcile
    return await vector_reconcile.reconcile(db_sink, vs, repair=True)


@router.delete("/api/vector/{article_id:path}")
async def delete_vector_only(article_id: str):
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.is_vectorized:
        await vs.delete(article_id)
        await db_sink.mark_as_unvectorized(article_id)
    return {"status": "success"}


@router.post("/api/vector/batch-delete")
async def batch_delete_vectors(params: BatchOpParams):
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record and record.is_vectorized:
            await vs.delete(uid)
            await db_sink.mark_as_unvectorized(uid)
    return {"status": "success"}


# ==================== RAG 检索上下文（供下游应用调用）====================

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


@router.post("/api/rag/context")
async def rag_context(query: RagContextQuery, request: Request):
    """
    结构化检索上下文接口。
    返回组装好的 context_text（可直接注入 LLM prompt）及结构化 sources 列表。
    不调用任何 LLM，纯检索层输出。
    """
    from storage.impl.vector_storage import SOURCE_FRIENDLY_NAMES

    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()

    # 登录用户的检索硬性限定在其订阅来源范围内（无订阅则空集）。
    source_id, source_ids, scoped, empty = resolve_scoped_search_args(request, query.source_id)
    if empty:
        return {
            "status": "success", "query": query.query, "retrieved_count": 0,
            "total_chars": 0, "context_text": "", "sources": [], "scoped": True,
        }

    raw = await vs.search(
        query.query,
        n_results=query.top_k * 4,
        content_type=query.content_type,
        source_id=source_id,
        source_ids=source_ids,
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
        candidates = await vs.rerank(query.query, candidates[:query.top_k * 2])
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
            adj = await vs.expand_chunk(pid, chunk_index, total_chunks)
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


@router.get("/api/rag/similar/{article_id:path}")
async def rag_similar(article_id: str, top_k: int = 5):
    """
    相似文章接口：找出与给定文章语义最接近的其他文章。
    用于"相关阅读"、知识图谱构建等场景。
    """
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章不存在")

    # 使用标题+正文片段作为查询向量
    query_text = f"{record.title}\n{(record.content or '')[:300]}"
    raw = await vs.search(query_text, n_results=(top_k + 1) * 3)

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


@router.post("/api/vector/reindex-all")
async def reindex_all_articles():
    """
    T9: 删除并重建整个 ChromaDB collection，对所有文章重新向量化。
    适用于：更换 embedding 模型后的全库迁移。管理员（collector）操作。

    全库重索引耗时极长，改为提交后台任务并立即返回 job_id；前端轮询
    GET /api/jobs/{job_id} 获取进度与最终结果（total_reindexed/total_articles）。
    """
    vs = deps.get_vector_sink()
    db_sink = deps.get_db_sink()

    async def _work(job: background_jobs.Job) -> Dict[str, Any]:
        # 重建集合为同步重操作，卸载到线程池避免阻塞事件循环。
        await asyncio.to_thread(vs.rebuild_collection)

        with Session(db_sink.engine) as session:
            article_ids = list(session.exec(select(ArticleRecord.id)).all())
        job.set_total(len(article_ids))

        success_count = 0
        for article_id in article_ids:
            await db_sink.update(article_id, {"is_vectorized": False})
            record = await db_sink.get(article_id)
            if record and await vs.save(_record_to_content(record)):
                await db_sink.mark_as_vectorized(article_id)
                success_count += 1
            job.advance()
        return {"total_reindexed": success_count, "total_articles": len(article_ids)}

    job = background_jobs.launch("reindex_all", _work)
    return {"status": "accepted", "job_id": job.id}
