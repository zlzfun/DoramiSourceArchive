"""阅读器订阅/收藏/计量 Router（reader 面）。

阶段1 从 app.py 迁出的 reader 互动子簇：一键订阅/退订、阅读计量、收藏增删查、
个人聚合接口令牌。说明：
- 路径不变（prefix=/api/reader）；reader 网关仍由 app.py 中间件统一强制
  （READER_API_PREFIXES 含 /api/reader）；
- 数据访问经 Depends(deps.get_session)；
- 仍留守 app.py 且被多端点共用的业务 helper（当前用户名/订阅范围解析/单源订阅创建/
  文章列表序列化/订阅令牌生成与哈希）经 _app() 延迟动态调用，避免与 api.app 的导入环；
  这些 helper 不在测试 monkeypatch 名单内，动态调用安全。源目录元数据助手已在 api.sources。
"""

import datetime
import importlib
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, func, select

from api import deps
from api.articles_view import serialize_article_list_item
from api.tokens import generate_feed_token, hash_subscription_token, subscription_token_preview
from api.sources import (
    DAILY_BRIEF_SOURCE_ID,
    DAILY_BRIEF_SOURCE_META,
    _friendly_source_name,
    _registry_source_meta,
    _source_category,
    subscription_source_ids,
)
from fetchers.registry import DECOMMISSIONED_FETCHER_IDS
from llm.client import LLMError, UsageMeta
from models.db import (
    ArticleRecord,
    ReaderFavoriteRecord,
    ReaderFeedTokenRecord,
    ReaderSubscriptionRecord,
)
from services import accounts as accounts_service
from services import daily_brief as daily_brief_service
from services import reader_activity as reader_activity_service
from services import reader_ai as reader_ai_service

router = APIRouter(prefix="/api/reader", tags=["reader"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的共享业务 helper）。"""
    return importlib.import_module("api.app")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def resolve_favorite_article_ids(session: Session, username: str) -> List[str]:
    """当前用户全部收藏的文章 ID（不分页，供前端维护收藏态集合）。"""
    if not username:
        return []
    rows = session.exec(
        select(ReaderFavoriteRecord.article_id).where(
            ReaderFavoriteRecord.owner_username == username
        )
    ).all()
    return list(rows)


# ==================== 一键订阅 / 退订 ====================

@router.post("/sources/{source_id}/subscribe")
def subscribe_source(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """一键订阅单个内容源：尚未订阅则创建一个仅含该源的订阅，已订阅则幂等返回。

    交付令牌、限额等高级设置使用默认值，留待用户在「我的订阅」中按需编辑。
    """
    app = _app()
    username = app.current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    registry_meta = _registry_source_meta()
    already = source_id in set(app.resolve_subscribed_source_ids(session, username))
    if not already:
        app._create_single_source_subscription(
            session, username, source_id, _friendly_source_name(source_id, registry_meta)
        )
        session.commit()
    subscribed_ids = sorted(set(app.resolve_subscribed_source_ids(session, username)))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": True,
        "subscribed_source_ids": subscribed_ids,
    }


@router.delete("/sources/{source_id}/subscribe")
def unsubscribe_source(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """一键取消订阅：从当前用户的所有订阅范围内移除该源，因此清空的订阅会被删除。"""
    app = _app()
    username = app.current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    records = session.exec(
        select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
    ).all()
    for record in records:
        ids = subscription_source_ids(record)
        if source_id not in ids:
            continue
        remaining = [sid for sid in ids if sid != source_id]
        if remaining:
            try:
                filters = json.loads(record.filters_json) if record.filters_json else {}
            except (TypeError, json.JSONDecodeError):
                filters = {}
            filters.pop("source_id", None)
            filters["source_ids"] = ",".join(remaining)
            record.filters_json = json.dumps(filters or {}, ensure_ascii=False)
            record.updated_at = _now_iso()
            session.add(record)
        else:
            session.delete(record)
    session.commit()
    subscribed_ids = sorted(set(app.resolve_subscribed_source_ids(session, username)))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": False,
        "subscribed_source_ids": subscribed_ids,
    }


# ==================== 阅读计量 ====================

@router.post("/articles/{article_id}/read")
def record_article_read(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """记录一次主动阅读：读者在阅读器中打开某文章即按其来源累加阅读计量。

    前端 fire-and-forget 调用；计量绝不阻断阅读——文章不存在或写入异常都安静返回。
    """
    username = _app().current_username(request)
    article = session.get(ArticleRecord, article_id)
    if article is None:
        return {"status": "ignored"}
    source_id = article.source_id
    try:
        reader_activity_service.record_read(session, username=username, source_id=source_id)
    except Exception:  # noqa: BLE001 - 计量失败不影响阅读
        return {"status": "ignored"}
    return {"status": "ok", "source_id": source_id}


# ==================== 文章收藏 ====================

@router.get("/favorites")
def list_favorites(
    request: Request,
    search: Optional[str] = None,
    source_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    include_content: bool = False,
    session: Session = Depends(deps.get_session),
):
    """当前用户的收藏文章列表，按收藏时间倒序；同时回传全部收藏 ID 集合。

    join 文章表后，已被删除文章的孤儿收藏自然被过滤掉，不出现在列表里。
    """
    app = _app()
    username = app.current_username(request)
    safe_limit = min(max(int(limit), 1), 500)
    safe_skip = max(int(skip), 0)
    base = (
        select(ArticleRecord, ReaderFavoriteRecord.created_at)
        .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .where(ReaderFavoriteRecord.owner_username == username)
    )
    count_query = (
        select(func.count())
        .select_from(ReaderFavoriteRecord)
        .join(ArticleRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .where(ReaderFavoriteRecord.owner_username == username)
    )
    if source_id:
        base = base.where(ArticleRecord.source_id == source_id)
        count_query = count_query.where(ArticleRecord.source_id == source_id)
    if search:
        base = base.where(ArticleRecord.title.contains(search))
        count_query = count_query.where(ArticleRecord.title.contains(search))
    base = base.order_by(ReaderFavoriteRecord.created_at.desc(), ArticleRecord.id.desc())
    total = int(session.exec(count_query).one() or 0)
    rows = session.exec(base.offset(safe_skip).limit(safe_limit)).all()
    items = [serialize_article_list_item(record, include_content=include_content) for record, _ in rows]
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {
        "items": items,
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
        "next_skip": safe_skip + len(items) if safe_skip + len(items) < total else None,
        "favorite_ids": favorite_ids,
    }


@router.post("/favorites/{article_id}")
def add_favorite(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """收藏一篇文章（幂等）。"""
    username = _app().current_username(request)
    article_id = (article_id or "").strip()
    if not article_id:
        raise HTTPException(status_code=400, detail="article_id 不能为空")
    if session.get(ArticleRecord, article_id) is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    if session.get(ReaderFavoriteRecord, (username, article_id)) is None:
        session.add(ReaderFavoriteRecord(
            owner_username=username, article_id=article_id, created_at=_now_iso()
        ))
        session.commit()
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {"status": "success", "article_id": article_id, "favorited": True, "favorite_ids": favorite_ids}


@router.delete("/favorites/{article_id}")
def remove_favorite(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """取消收藏一篇文章（幂等）。"""
    username = _app().current_username(request)
    article_id = (article_id or "").strip()
    if not article_id:
        raise HTTPException(status_code=400, detail="article_id 不能为空")
    record = session.get(ReaderFavoriteRecord, (username, article_id))
    if record is not None:
        session.delete(record)
        session.commit()
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {"status": "success", "article_id": article_id, "favorited": False, "favorite_ids": favorite_ids}


# ==================== 个人聚合接口令牌 ====================

@router.get("/feed-token")
def get_feed_token(request: Request, session: Session = Depends(deps.get_session)):
    """当前用户的个人聚合接口令牌状态（仅返回预览，不回显明文）。"""
    app = _app()
    username = app.current_username(request)
    record = session.get(ReaderFeedTokenRecord, username)
    subscribed = app.resolve_subscribed_source_ids(session, username)
    return {
        "exists": record is not None,
        "token_preview": record.token_preview if record else "",
        "created_at": record.created_at if record else None,
        "updated_at": record.updated_at if record else None,
        "subscribed_source_count": len(subscribed),
    }


@router.post("/feed-token/rotate")
def rotate_feed_token(request: Request, session: Session = Depends(deps.get_session)):
    """创建或轮换当前用户的个人聚合接口令牌；明文仅在本次响应中返回一次。"""
    app = _app()
    username = app.current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    token = generate_feed_token()
    now = _now_iso()
    record = session.get(ReaderFeedTokenRecord, username)
    if record is None:
        record = ReaderFeedTokenRecord(owner_username=username, created_at=now, updated_at=now)
    record.token_hash = hash_subscription_token(token)
    record.token_preview = subscription_token_preview(token)
    record.updated_at = now
    session.add(record)
    session.commit()
    return {"token": token, "token_preview": subscription_token_preview(token)}


# ==================== 内容源目录 ====================

@router.get("/sources")
def get_reader_sources(request: Request, session: Session = Depends(deps.get_session)):
    """读者层内容源目录：可订阅来源 = 所有已注册抓取源 ∪ 已归档来源 ∪ 已订阅来源。

    即便某个源历史产出为 0，它仍会出现在目录里，用户可提前订阅以接收其后续产出。
    """
    app = _app()
    username = app.current_username(request)
    app.ensure_default_subscriptions(username)
    registry_meta = _registry_source_meta()
    rows = session.exec(
        select(
            ArticleRecord.source_id,
            ArticleRecord.content_type,
            func.count(ArticleRecord.id),
            func.max(ArticleRecord.fetched_date),
        )
        .where(ArticleRecord.source_id.isnot(None))
        .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
    ).all()
    subscribed_ids = set(app.resolve_subscribed_source_ids(session, username))

    by_source: Dict[str, Dict[str, Any]] = {}

    def _ensure_entry(source_id: str, content_type: Optional[str] = None) -> Dict[str, Any]:
        entry = by_source.get(source_id)
        if entry is None:
            meta = registry_meta.get(source_id, {})
            if source_id == DAILY_BRIEF_SOURCE_ID:
                meta = {**DAILY_BRIEF_SOURCE_META, **meta}
            resolved_type = content_type or meta.get("content_type") or ""
            entry = {
                "source_id": source_id,
                "name": meta.get("name") or _friendly_source_name(source_id, registry_meta),
                "description": meta.get("desc", ""),
                "icon": meta.get("icon", ""),
                "source_owner": meta.get("source_owner", ""),
                "source_brand": meta.get("source_brand", ""),
                "source_scope": meta.get("source_scope", ""),
                "source_channel": meta.get("source_channel", ""),
                "provenance_tier": meta.get("provenance_tier", ""),
                "base_url": meta.get("base_url", ""),
                "content_tags": meta.get("content_tags", []),
                "content_type": resolved_type,
                "category": _source_category(resolved_type),
                "count": 0,
                "last_fetched": "",
                "subscribed": source_id in subscribed_ids,
                "registered": source_id in registry_meta,
                "_primary_count": -1,
            }
            by_source[source_id] = entry
        return entry

    # 1. 所有已注册抓取源（含历史产出为 0 者，使新源可被提前订阅）。
    for source_id in registry_meta:
        _ensure_entry(source_id)

    # 1b. 日报特殊源：即使尚未生成过日报也预先出现，便于提前订阅。
    _ensure_entry(DAILY_BRIEF_SOURCE_ID, "daily_brief")

    # 2. 叠加归档文章聚合（含未注册的导入源，如 social_post）；主 content_type 取计数最高者。
    #    已下线节点（删类后仍留有历史归档）不再回流目录，除非当前用户已订阅（保留退订入口），
    #    以保持读者层订阅目录与节点管理同步。
    for source_id, content_type, count, last_fetched in rows:
        if not source_id:
            continue
        if source_id in DECOMMISSIONED_FETCHER_IDS and source_id not in subscribed_ids:
            continue
        entry = _ensure_entry(source_id, content_type)
        entry["count"] += int(count or 0)
        if (last_fetched or "") > entry["last_fetched"]:
            entry["last_fetched"] = last_fetched or ""
        if int(count or 0) > entry["_primary_count"]:
            entry["_primary_count"] = int(count or 0)
            entry["content_type"] = content_type
            entry["category"] = _source_category(content_type)

    # 3. 已订阅但既未注册也无归档的来源也要出现，便于退订。
    for source_id in subscribed_ids:
        _ensure_entry(source_id)

    sources = sorted(
        ({k: v for k, v in entry.items() if k != "_primary_count"} for entry in by_source.values()),
        key=lambda s: (s["category"], -s["count"], s["name"]),
    )
    return {
        "sources": sources,
        "subscribed_source_ids": sorted(subscribed_ids),
        "total_sources": len(sources),
    }


# ==================== 阅读器 AI（用户面：翻译 / 问答）====================

class ReaderTranslateParams(BaseModel):
    article_id: str


class ReaderChatTurn(BaseModel):
    role: str  # user | assistant
    content: str


class ReaderAskParams(BaseModel):
    question: str
    scope: str = "article"  # article | subscription
    article_id: Optional[str] = None
    history: Optional[List[ReaderChatTurn]] = None  # 多轮对话历史（纯文本问答，不含参考资料）


def _require_reader_ai(request: Request):
    """校验当前账户的 AI Beta 已开启且 LLM 已配置，返回 (username, llm_config)；否则 403/401。"""
    app = _app()
    username = app.current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    with Session(deps.get_db_sink().engine) as session:
        if not accounts_service.ai_beta_global_enabled(session):
            raise HTTPException(status_code=403, detail="AI 功能已临时关闭，请稍后再试")
        record = accounts_service.get_user(session, username)
        if record is None or not record.ai_beta_enabled:
            raise HTTPException(status_code=403, detail="AI 功能尚未开启，请联系管理员")
        llm_config = daily_brief_service.resolve_llm_config(session)
    if not llm_config.configured:
        raise HTTPException(status_code=403, detail="AI 服务暂未就绪")
    return username, llm_config


def _recent_subscribed_articles(username: str, limit: int) -> List[ArticleRecord]:
    """取该用户订阅来源内、按抓取时间倒序的最近若干篇有正文的文章（RAG 关闭时的问答上下文）。"""
    app = _app()
    with Session(deps.get_db_sink().engine) as session:
        source_ids = app.resolve_subscribed_source_ids(session, username)
        if not source_ids:
            return []
        statement = (
            select(ArticleRecord)
            .where(
                ArticleRecord.source_id.in_(source_ids),
                ArticleRecord.has_content == True,  # noqa: E712
            )
            .order_by(ArticleRecord.fetched_date.desc())
            .limit(limit)
        )
        return list(session.exec(statement).all())


@router.post("/ai/translate")
async def reader_ai_translate(params: ReaderTranslateParams, request: Request):
    """把指定文章正文译为简体中文（结果缓存复用）。"""
    username, llm_config = _require_reader_ai(request)
    db_sink = deps.get_db_sink()
    try:
        result = await reader_ai_service.translate_article(
            db_sink, params.article_id, llm_config,
            UsageMeta(purpose="translate", username=username),
        )
    except reader_ai_service.ReaderAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"翻译失败：{exc}")
    with Session(db_sink.engine) as session:
        accounts_service.record_ai_usage(session, username, "translate")
    return {"status": "success", **result}


@router.post("/ai/ask")
async def reader_ai_ask(params: ReaderAskParams, request: Request):
    """基于当前文章或用户订阅文章回答提问。

    三档上下文（graceful degrade）：
      - scope=article：直接用该文正文（零 RAG 依赖）；
      - scope=subscription 且 RAG 开启：走 /api/rag/context 语义召回（已自带订阅域硬隔离）；
      - scope=subscription 且 RAG 关闭：取订阅来源最近 N 篇标题+截断正文拼成上下文。
    """
    app = _app()
    username, llm_config = _require_reader_ai(request)
    db_sink = deps.get_db_sink()
    scope = params.scope if params.scope in ("article", "subscription") else "article"

    # 三档上下文组装下沉到 reader_ai.assemble_reader_context；此处注入 rag/recent 取数闭包
    # （闭包 over request 承载鉴权作用域），使组装逻辑与 HTTP 请求解耦、可独立单测（D11）。
    async def _rag_fetch(question: str) -> Dict[str, Any]:
        return await app.rag_context(
            app.RagContextQuery(query=question, top_k=6, max_chars=12000), request
        )

    try:
        context, sources = await reader_ai_service.assemble_reader_context(
            scope=scope,
            question=params.question,
            article_id=params.article_id,
            username=username,
            db_sink=db_sink,
            rag_enabled=bool(app.settings.rag.enabled and app.vector_sink is not None),
            rag_fetch=_rag_fetch,
            recent_fetch=lambda user: _recent_subscribed_articles(
                user, reader_ai_service.LIST_MAX_ARTICLES
            ),
        )
    except reader_ai_service.ReaderAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    history = [{"role": t.role, "content": t.content} for t in (params.history or [])]
    try:
        answer = await reader_ai_service.answer_question(
            params.question, context, scope=scope, llm_config=llm_config, history=history,
            usage_meta=UsageMeta(purpose="ask", username=username),
        )
    except reader_ai_service.ReaderAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"提问失败：{exc}")
    with Session(db_sink.engine) as session:
        accounts_service.record_ai_usage(session, username, "ask")
    return {"status": "success", "answer": answer, "sources": sources, "scope": scope}
