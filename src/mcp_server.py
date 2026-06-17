from __future__ import annotations
import json
from typing import Optional
from mcp.server.fastmcp import FastMCP
from sqlmodel import Session, select
from models.db import ArticleRecord, SourceStateRecord
from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage, SOURCE_FRIENDLY_NAMES
from fetchers.registry import fetcher_registry


# 内容类工具缺/错令牌时的统一报错：明确指引去哪里取令牌，便于 Agent 转告用户。
_TOKEN_REQUIRED_MSG = (
    "subscription_token is required and must be valid — "
    "请在哆啦美「接入集成 → 访问令牌」获取 dfeed_ 令牌后重试。"
)


# ── Helper functions (testable independently) ─────────────────────────────────

def _parse_bearer(auth: Optional[str]) -> Optional[str]:
    """从 ``Authorization`` 头解析 Bearer 令牌；非 Bearer 或空值返回 None。"""
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _resolve_scope(resolver, token: Optional[str], *, require_token: bool = True):
    """把访问令牌折算成检索作用域，返回 (ok, source_ids)。

    内容类工具（search/browse/get_article/get_rag_context）必须携带令牌：``/mcp``
    传输层没有登录会话，令牌是唯一的鉴权手段，因此无令牌一律拒绝（ok=False），
    避免任何人连上端口即可读取全库。``list_sources`` 只返回来源目录，可
    ``require_token=False`` 放行。

    - ok=False → 缺令牌或令牌无效，调用方应返回报错；
    - ok=True 且 source_ids=None → 不限定作用域（仅 require_token=False 时可能出现）；
    - ok=True 且 source_ids=[...] → 限定到这些来源（空列表表示零订阅，应返回空）。
    """
    if not token:
        return (False, None) if require_token else (True, None)
    if resolver is None:
        return False, None
    source_ids = resolver(token)
    if source_ids is None:
        return False, None
    return True, source_ids


def _list_sources_impl(db_sink: DatabaseStorage) -> list[dict]:
    fetchers = fetcher_registry.get_all_metadata()
    with Session(db_sink.engine) as session:
        states = {s.fetcher_id: s for s in session.exec(select(SourceStateRecord)).all()}
    result = []
    for f in fetchers:
        state = states.get(f["id"])
        result.append({
            "source_id": f["id"],
            "name": f["name"],
            "icon": f.get("icon", ""),
            "content_type": f.get("content_type", ""),
            "category": f.get("category", "general"),
            "last_fetch_time": state.last_completed_at if state else None,
            "last_fetch_status": state.status if state else None,
        })
    return result


def _browse_articles_impl(
    db_sink: DatabaseStorage,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    content_type: Optional[str] = None,
    publish_date_start: Optional[str] = None,
    publish_date_end: Optional[str] = None,
    has_content: Optional[bool] = None,
    limit: int = 20,
    skip: int = 0,
) -> list[dict]:
    effective_limit = min(limit, 100)
    with Session(db_sink.engine) as session:
        q = select(ArticleRecord)
        if source_ids is not None:
            # 订阅范围白名单：空列表显式匹配不到任何记录。
            q = q.where(ArticleRecord.source_id.in_(source_ids or ["__none__"]))
        elif source_id:
            q = q.where(ArticleRecord.source_id == source_id)
        if content_type:
            q = q.where(ArticleRecord.content_type == content_type)
        if publish_date_start:
            q = q.where(ArticleRecord.publish_date >= publish_date_start)
        if publish_date_end:
            end = publish_date_end if "T" in publish_date_end else f"{publish_date_end}T23:59:59"
            q = q.where(ArticleRecord.publish_date <= end)
        if has_content is True:
            q = q.where(ArticleRecord.has_content == True)
        elif has_content is False:
            q = q.where(ArticleRecord.has_content == False)
        q = q.order_by(ArticleRecord.publish_date.desc()).offset(skip).limit(effective_limit)
        records = session.exec(q).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "source_id": r.source_id,
            "content_type": r.content_type,
            "publish_date": r.publish_date,
            "source_url": r.source_url,
            "is_vectorized": r.is_vectorized,
        }
        for r in records
    ]


def _get_article_impl(
    db_sink: DatabaseStorage,
    article_id: str,
    source_ids: Optional[list[str]] = None,
) -> dict:
    with Session(db_sink.engine) as session:
        record = session.get(ArticleRecord, article_id)
    if not record:
        return {"error": f"Article '{article_id}' not found."}
    if source_ids is not None and record.source_id not in set(source_ids):
        return {"error": "article is outside the subscription scope"}
    return {
        "id": record.id,
        "title": record.title,
        "content": record.content or "",
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "source_url": record.source_url,
        "extensions": json.loads(record.extensions_json or "{}"),
    }


async def _search_articles_impl(
    vector_sink: Optional[ChromaVectorStorage],
    query: str,
    top_k: int = 10,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    publish_date_gte: Optional[str] = None,
    distance_threshold: float = 1.5,
) -> list[dict]:
    if vector_sink is None:
        return [{"error": "RAG disabled", "detail": "向量检索功能未启用，请在后端启用 [rag] enabled = true 后重启。"}]
    raw = await vector_sink.search(
        query,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=publish_date_gte,
    )
    best: dict[str, dict] = {}
    for chunk in raw:
        if chunk["distance"] > distance_threshold:
            continue
        pid = chunk["metadata"].get("parent_id", chunk["id"])
        if pid not in best or chunk["distance"] < best[pid]["distance"]:
            best[pid] = chunk
    ranked = sorted(best.values(), key=lambda x: x["distance"])[:top_k]
    return [
        {
            "id": item["metadata"].get("parent_id", item["id"]),
            "title": item["metadata"].get("title", ""),
            "source_id": item["metadata"].get("source_id", ""),
            "content_type": item["metadata"].get("content_type", ""),
            "publish_date": item["metadata"].get("publish_date", ""),
            "summary": item["document"][:200] if item.get("document") else "",
            "distance": round(item["distance"], 4),
        }
        for item in ranked
    ]


async def _get_rag_context_impl(
    db_sink: DatabaseStorage,
    vector_sink: Optional[ChromaVectorStorage],
    query: str,
    top_k: int = 8,
    max_chars: int = 4000,
    distance_threshold: float = 1.5,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    publish_date_gte: Optional[str] = None,
    context_separator: str = "\n\n---\n\n",
) -> str:
    if vector_sink is None:
        return ""
    raw = await vector_sink.search(
        query,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=publish_date_gte,
    )
    best: dict[str, dict] = {}
    for chunk in raw:
        if chunk["distance"] > distance_threshold:
            continue
        pid = chunk["metadata"].get("parent_id", chunk["id"])
        if pid not in best or chunk["distance"] < best[pid]["distance"]:
            best[pid] = chunk

    ranked = sorted(best.values(), key=lambda x: x["distance"])[:top_k]

    parts = []
    total_chars = 0
    for rank, res in enumerate(ranked, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)
        source_id_val = res["metadata"].get("source_id", "")
        source_name = SOURCE_FRIENDLY_NAMES.get(source_id_val, source_id_val)
        pub_date = res["metadata"].get("publish_date", "")
        title = record.title if record else res["metadata"].get("title", "")
        source_url = record.source_url if record else ""
        raw_doc = res.get("document", "")
        body_start = raw_doc.find("\n\n")
        excerpt = raw_doc[body_start + 2:].strip() if body_start != -1 else raw_doc.strip()
        block = (
            f"[{rank}] 来源: {source_name} | 日期: {pub_date}\n"
            f"标题: {title}\n"
            f"链接: {source_url}\n\n"
            f"{excerpt}"
        )
        if total_chars + len(block) > max_chars and parts:
            break
        parts.append(block)
        total_chars += len(block)

    return context_separator.join(parts)


# ── FastMCP server factory ────────────────────────────────────────────────────

def build_mcp_app(
    db_sink: DatabaseStorage,
    vector_sink: Optional[ChromaVectorStorage],
    subscription_resolver=None,
) -> FastMCP:
    """构建 FastMCP 实例。

    subscription_resolver: 可选的 ``token -> Optional[list[str]]`` 回调。
    当工具收到 ``subscription_token`` 时，用它把检索范围约束到该订阅覆盖的 source_id。
    返回 None 表示令牌无效；返回 [] 表示订阅未限定来源。
    """
    mcp = FastMCP(
        "dorami-archive",
        instructions="哆啦美·归档中枢 MCP Server — AI资讯检索与RAG上下文组装",
        streamable_http_path="/",
    )

    def _token_from_header() -> Optional[str]:
        """从当前请求的 ``Authorization: Bearer <token>`` 头兜底取访问令牌。

        ``/mcp`` 是流式 HTTP 传输：客户端可在 JSON 配置的 ``headers`` 里写一次
        ``Authorization: Bearer dfeed_…``，每次工具调用便自动带上，无需 Agent
        逐次显式传 ``subscription_token``。取不到时返回 None，由调用方继续按入参判定。
        """
        try:
            request = mcp.get_context().request_context.request
            auth = request.headers.get("authorization") if request else None
        except Exception:
            return None
        return _parse_bearer(auth)

    def _resolve_subscription_scope(subscription_token: Optional[str]):
        """返回 (ok, source_ids)。内容类工具必须携带有效令牌，否则 ok=False。

        令牌来源优先级：显式入参 ``subscription_token`` > 请求头 ``Authorization: Bearer``。
        """
        token = subscription_token or _token_from_header()
        return _resolve_scope(subscription_resolver, token, require_token=True)

    @mcp.tool()
    def list_sources() -> list[dict]:
        """列出平台中所有已知数据来源。
        调用 browse_articles 或 search_articles 前先用本工具了解可用的 source_id 和 content_type。
        List all known data sources in the archive. Call this first to discover
        valid source_id and content_type values for other tools.
        """
        return _list_sources_impl(db_sink)

    @mcp.tool()
    def browse_articles(
        source_id: Optional[str] = None,
        content_type: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        has_content: Optional[bool] = None,
        limit: int = 20,
        skip: int = 0,
        subscription_token: Optional[str] = None,
    ) -> list[dict]:
        """按条件过滤浏览文章列表，适合「Anthropic最新动态」或生成日报等场景。
        Filter and browse articles by metadata. Use for source-specific or date-range queries.
        Scenarios: 「某来源最新资讯」「生成今日日报」「列出某类型内容」
        publish_date_start/end: YYYY-MM-DD. limit max 100.
        subscription_token: 访问令牌把结果限定在你订阅覆盖的来源内（个性化视图）。
            可经本参数传入，或由客户端在请求头 Authorization: Bearer 提供（二者皆缺或无效将被拒绝）；
            支持单订阅令牌（dsub_）或个人订阅令牌（dfeed_，覆盖你的全部订阅）。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return [{"error": _TOKEN_REQUIRED_MSG}]
        return _browse_articles_impl(
            db_sink, source_id=source_id, source_ids=scope_ids, content_type=content_type,
            publish_date_start=publish_date_start, publish_date_end=publish_date_end,
            has_content=has_content, limit=limit, skip=skip,
        )

    @mcp.tool()
    def get_article(article_id: str, subscription_token: Optional[str] = None) -> dict:
        """按 ID 获取单篇文章完整内容（含正文、extensions 元数据）。
        Get full content of a single article by its ID.
        Use article IDs from browse_articles or search_articles results.
        extensions contains content-type-specific metadata parsed from JSON.
        subscription_token: 仅允许读取访问令牌覆盖来源内的文章。可经本参数传入，
            或由客户端在请求头 Authorization: Bearer 提供；二者皆缺或无效将被拒绝。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return {"error": _TOKEN_REQUIRED_MSG}
        return _get_article_impl(db_sink, article_id, source_ids=scope_ids)

    @mcp.tool()
    async def search_articles(
        query: str,
        top_k: int = 10,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        publish_date_gte: Optional[str] = None,
        distance_threshold: float = 1.5,
        subscription_token: Optional[str] = None,
    ) -> list[dict]:
        """语义向量搜索文章，支持中英文，按相关性排序。适合主题查询场景。
        Semantic vector search. Supports Chinese and English queries.
        Scenarios: 「最近的具身智能资讯有哪些？」「Find papers on multimodal LLMs」
        distance_threshold: cosine distance cutoff (lower = stricter, default 1.5).
        publish_date_gte: YYYY-MM-DD. Returns articles ranked by relevance (distance asc).
        subscription_token: 把检索限定在访问令牌覆盖的来源内（个性化视图）。
            可经本参数传入，或由客户端在请求头 Authorization: Bearer 提供（二者皆缺或无效将被拒绝）；
            支持单订阅令牌（dsub_）或个人订阅令牌（dfeed_，覆盖你的全部订阅）。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return [{"error": _TOKEN_REQUIRED_MSG}]
        return await _search_articles_impl(
            vector_sink, query=query, top_k=top_k,
            content_type=content_type, source_id=source_id, source_ids=scope_ids,
            publish_date_gte=publish_date_gte, distance_threshold=distance_threshold,
        )

    @mcp.tool()
    async def get_rag_context(
        query: str,
        top_k: int = 8,
        max_chars: int = 4000,
        distance_threshold: float = 1.5,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        publish_date_gte: Optional[str] = None,
        context_separator: str = "\n\n---\n\n",
        subscription_token: Optional[str] = None,
    ) -> str:
        """语义检索后组装格式化RAG上下文字符串，可直接拼入LLM System Prompt。
        Assemble a formatted RAG context string ready to inject into an LLM prompt.
        Scenarios: 需要用归档资讯回答用户提问时的上下文准备。
        Returns empty string when no relevant results found.
        publish_date_gte: YYYY-MM-DD. distance_threshold: cosine distance cutoff (default 1.5).
        subscription_token: 把上下文限定在访问令牌覆盖来源内。可经本参数传入，
            或由客户端在请求头 Authorization: Bearer 提供；二者皆缺或无效将被拒绝。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return f"ERROR: {_TOKEN_REQUIRED_MSG}"
        return await _get_rag_context_impl(
            db_sink, vector_sink, query=query, top_k=top_k,
            max_chars=max_chars, distance_threshold=distance_threshold,
            content_type=content_type, source_id=source_id, source_ids=scope_ids,
            publish_date_gte=publish_date_gte, context_separator=context_separator,
        )

    return mcp
