from __future__ import annotations
import datetime
import json
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
# 内部模块路径:mcp 顶层与 mcp.server.fastmcp 都不导出 TransportSecuritySettings。
# pyproject 的 mcp<2 上限是这行成立的前提,2.x 会移动该模块。
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import literal_column
from sqlmodel import Session, select
from models.db import (
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    ReaderFavoriteRecord,
    ReaderReadRecord,
    ReaderSubscriptionRecord,
    SourceStateRecord,
    UserInterestTagRecord,
)
from storage.fts import fts_search_ids
from storage.impl.db_storage import DatabaseStorage
from services.source_naming import friendly_source_name
from services.paper_content import delivery_body
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
    from services.paper_content import delivery_body, delivery_metadata
    return {
        "id": record.id,
        "title": record.title,
        "content": delivery_body(record),
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "source_url": record.source_url,
        "extensions": delivery_metadata(record),
    }


# get_user_context(issue #175 v1):实时拼装调用者的阅读侧上下文,不用 LLM 预生成、
# 不落库。四段按固定优先级排列(兴趣 > 收藏 > 常读来源 > 订阅名),超 token 预算时
# 段粒度从低到高整段丢弃。仅供内部调试,不做读者设置/纠正界面。
# token 估算:CJK 字符 ≈1 token,其他字符 ≈0.5——足够「整体预算」量级判定,
# 不追求与任何具体 tokenizer 精确一致。

def _estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk + 1) // 2


def _date_days_ago(days: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def _load_user_context_segments(
    session: Session,
    username: str,
    *,
    interest_tag_limit: int,
    favorite_limit: int,
    favorite_days: int,
    source_limit: int,
    source_days: int,
    subscription_limit: int,
) -> dict[str, Any]:
    """按优先级从高到低拼装四段;每段条目上限在查询处生效。"""
    username = (username or "").strip()

    # 1. 兴趣标签:显式关注且目录状态仍为 active(与 load_interest_map 同口径)。
    tags: list[str] = []
    if username and interest_tag_limit > 0:
        rows = session.exec(
            select(CmsTagRecord.name_zh, CmsTagRecord.name_en)
            .join(UserInterestTagRecord, UserInterestTagRecord.tag_id == CmsTagRecord.id)
            .where(
                UserInterestTagRecord.owner_username == username,
                CmsTagRecord.status == "active",
            )
            .order_by(UserInterestTagRecord.created_at)
            .limit(interest_tag_limit)
        ).all()
        tags = [(zh or en or "").strip() for zh, en in rows]
        tags = [t for t in tags if t]

    # 2. 最近 N 天收藏(按收藏时间倒序):标题 + 文章分析产生的标签。
    favorites: list[dict[str, Any]] = []
    if username and favorite_limit > 0:
        fav_rows = session.exec(
            select(ArticleRecord.id, ArticleRecord.title, ReaderFavoriteRecord.created_at)
            .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
            .where(
                ReaderFavoriteRecord.owner_username == username,
                ReaderFavoriteRecord.created_at >= _date_days_ago(favorite_days),
            )
            .order_by(ReaderFavoriteRecord.created_at.desc())
            .limit(favorite_limit)
        ).all()
        if fav_rows:
            article_ids = [row[0] for row in fav_rows]
            titles = {row[0]: row[1] for row in fav_rows}
            tag_rows = session.exec(
                select(
                    ArticleTagAssignmentRecord.article_id,
                    CmsTagRecord.name_zh,
                    CmsTagRecord.name_en,
                )
                .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
                .where(
                    ArticleTagAssignmentRecord.article_id.in_(article_ids),
                    CmsTagRecord.status == "active",
                )
                .order_by(
                    ArticleTagAssignmentRecord.is_primary.desc(),
                    ArticleTagAssignmentRecord.relevance.desc(),
                )
            ).all()
            tags_by_article: dict[str, list[str]] = {}
            for article_id, name_zh, name_en in tag_rows:
                name = (name_zh or name_en or "").strip()
                if name:
                    tags_by_article.setdefault(article_id, []).append(name)
            favorites = [
                {"title": titles[article_id], "tags": tags_by_article.get(article_id, [])[:5]}
                for article_id in article_ids
            ]

    # 3. 常读来源:近 N 天阅读按来源聚合次数(reader_reads 是按天聚合行,直接求和)。
    top_sources: list[dict[str, Any]] = []
    if username and source_limit > 0:
        rows = session.exec(
            select(ReaderReadRecord.source_id, ReaderReadRecord.reads)
            .where(
                ReaderReadRecord.username == username,
                ReaderReadRecord.day >= _date_days_ago(source_days),
            )
        ).all()
        if rows:
            counts: dict[str, int] = {}
            for source_id, reads in rows:
                counts[source_id] = counts.get(source_id, 0) + int(reads)
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:source_limit]
            top_sources = [
                {"source": friendly_source_name(source_id), "reads": reads}
                for source_id, reads in ranked
            ]

    # 4. 已启用订阅名(弱信号):只取名,不展开过滤条件。
    subscription_names: list[str] = []
    if username and subscription_limit > 0:
        rows = session.exec(
            select(ReaderSubscriptionRecord.name)
            .where(
                ReaderSubscriptionRecord.owner_username == username,
                ReaderSubscriptionRecord.is_active == True,  # noqa: E712
            )
            .order_by(ReaderSubscriptionRecord.id)
            .limit(subscription_limit)
        ).all()
        subscription_names = list(rows)

    return {
        "interest_tags": tags,
        "recent_favorites": favorites,
        "top_sources": top_sources,
        "active_subscriptions": subscription_names,
    }


def _get_user_context_impl(
    db_sink: DatabaseStorage,
    username: Optional[str],
    *,
    interest_tag_limit: int = 20,
    favorite_limit: int = 10,
    favorite_days: int = 14,
    source_limit: int = 8,
    source_days: int = 14,
    subscription_limit: int = 10,
    max_tokens: int = 800,
) -> dict:
    owner = (username or "").strip()
    if not owner:
        return {"error": _TOKEN_REQUIRED_MSG}
    with Session(db_sink.engine) as session:
        segments = _load_user_context_segments(
            session,
            owner,
            interest_tag_limit=interest_tag_limit,
            favorite_limit=favorite_limit,
            favorite_days=favorite_days,
            source_limit=source_limit,
            source_days=source_days,
            subscription_limit=subscription_limit,
        )
    payload = json.dumps(segments, ensure_ascii=False, sort_keys=True)
    tokens = _estimate_tokens(payload)
    # 整体 token 预算:按段优先级从低到高整段丢弃;全段命中预算仍超(数学上仅
    # max_tokens 极小时可发生)时保留最高优先级段,预算不写死为 0。
    for key in ("active_subscriptions", "top_sources", "recent_favorites"):
        if tokens <= max_tokens:
            break
        if segments[key]:
            segments[key] = []
            payload = json.dumps(segments, ensure_ascii=False, sort_keys=True)
            tokens = _estimate_tokens(payload)
    # 兴趣标签是最高优先级段:仅在丢弃后预算仍不满足、且丢弃确实能使预算命中时
    # 才动它;全段清空仍超预算(数学上仅 max_tokens 极小时可发生)时保留本段,
    # 不返回空上下文。
    if tokens > max_tokens and segments["interest_tags"]:
        trimmed = dict(segments, interest_tags=[])
        trimmed_payload = json.dumps(trimmed, ensure_ascii=False, sort_keys=True)
        if _estimate_tokens(trimmed_payload) <= max_tokens:
            segments = trimmed
            payload = trimmed_payload
            tokens = _estimate_tokens(payload)
    segments["token_estimate"] = tokens
    return segments


# 检索实现(v3.30 检索扶正波):FTS5 全文检索取代向量召回——工具名/出入参形状
# 保持兼容(distance 字段退役),RAG 开关不再影响 MCP 的搜索能力。
# FTS 不可用(老 SQLite)时降级为标题 LIKE;命中按发布日期倒序(新闻域的合理次序,
# FTS5 trigram 不暴露 bm25 排序,相关性排序交给消费端 Agent 自行取舍)。

_SEARCH_MAX_ROWIDS = 10000  # rowid IN (...) 防御性上限(SQLite 变量数限制)


def _fts_hit_records(
    db_sink: DatabaseStorage,
    query: str,
    *,
    top_k: int,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    publish_date_gte: Optional[str] = None,
) -> list[ArticleRecord]:
    """FTS 全文检索(标题+正文)+ 元数据过滤,返回命中文章记录。"""
    with Session(db_sink.engine) as session:
        stmt = select(ArticleRecord)
        if source_ids is not None:
            unique_ids = [sid for sid in dict.fromkeys(source_ids) if sid]
            if not unique_ids:
                return []
            stmt = stmt.where(ArticleRecord.source_id.in_(unique_ids))
        if source_id:
            stmt = stmt.where(ArticleRecord.source_id == source_id)
        if content_type:
            stmt = stmt.where(ArticleRecord.content_type == content_type)
        if publish_date_gte:
            stmt = stmt.where(ArticleRecord.publish_date >= publish_date_gte)

        fts_ids = fts_search_ids(session, query)
        if fts_ids is not None:
            if not fts_ids:
                return []
            stmt = stmt.where(
                literal_column("articles.rowid").in_(fts_ids[:_SEARCH_MAX_ROWIDS])
            )
        else:
            stmt = stmt.where(ArticleRecord.title.contains(query.strip()))
        stmt = stmt.order_by(ArticleRecord.publish_date.desc()).limit(top_k)
        return list(session.exec(stmt).all())


async def _search_articles_impl(
    db_sink: DatabaseStorage,
    query: str,
    top_k: int = 10,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    publish_date_gte: Optional[str] = None,
) -> list[dict]:
    records = _fts_hit_records(
        db_sink, query, top_k=top_k, content_type=content_type,
        source_id=source_id, source_ids=source_ids, publish_date_gte=publish_date_gte,
    )
    return [
        {
            "id": r.id,
            "title": r.title,
            "source_id": r.source_id,
            "content_type": r.content_type,
            "publish_date": r.publish_date,
            "summary": " ".join(delivery_body(r).split())[:200],
        }
        for r in records
    ]


async def _get_rag_context_impl(
    db_sink: DatabaseStorage,
    query: str,
    top_k: int = 8,
    max_chars: int = 4000,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_ids: Optional[list[str]] = None,
    publish_date_gte: Optional[str] = None,
    context_separator: str = "\n\n---\n\n",
) -> str:
    records = _fts_hit_records(
        db_sink, query, top_k=top_k, content_type=content_type,
        source_id=source_id, source_ids=source_ids, publish_date_gte=publish_date_gte,
    )
    parts: list[str] = []
    total_chars = 0
    for rank, record in enumerate(records, start=1):
        source_name = friendly_source_name(record.source_id or "")
        pub_date = (record.publish_date or "")[:10]
        excerpt = delivery_body(record).strip()[:1500]
        block = (
            f"[{rank}] 来源: {source_name} | 日期: {pub_date}\n"
            f"标题: {record.title}\n"
            f"链接: {record.source_url}\n\n"
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
    subscription_resolver=None,
    token_owner_resolver=None,
    user_context_limits: Optional[dict[str, int]] = None,
) -> FastMCP:
    """构建 FastMCP 实例。

    subscription_resolver: 可选的 ``token -> Optional[list[str]]`` 回调。
    当工具收到 ``subscription_token`` 时，用它把检索范围约束到该订阅覆盖的 source_id。
    返回 None 表示令牌无效；返回 [] 表示订阅未限定来源。
    token_owner_resolver: 可选的 ``token -> Optional[str]`` 回调，把交付令牌折算成
    归属用户名（get_user_context 用；None 视为未接线，工具统一返回缺令牌报错）。
    user_context_limits: get_user_context 各段条目上限与整体 token 预算；
    缺省用内置默认值（与 config.McpUserContextConfig 一致）。
    """
    limits = dict(user_context_limits or {})
    mcp = FastMCP(
        "dorami-archive",
        instructions="哆啦美·归档中枢 MCP Server — AI资讯检索与上下文组装",
        streamable_http_path="/",
        # Public transport: content access is token-scoped, not trusted by Host.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
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
        subscription_token: Optional[str] = None,
    ) -> list[dict]:
        """关键词全文搜索文章（标题+正文，中英文子串匹配），按发布日期倒序。
        Keyword full-text search over title + body (Chinese & English substrings).
        Scenarios: 「具身智能相关的资讯」「Find articles mentioning multimodal LLMs」
        query: 关键词或短语（≥3 字符；多词以空格分隔时须全部命中）。归档中英文来源
            混杂，中文查不到时换英文关键词再试（反之亦然）。
        publish_date_gte: YYYY-MM-DD. Results ordered by publish_date desc.
        subscription_token: 把检索限定在访问令牌覆盖的来源内（个性化视图）。
            可经本参数传入，或由客户端在请求头 Authorization: Bearer 提供（二者皆缺或无效将被拒绝）；
            支持单订阅令牌（dsub_）或个人订阅令牌（dfeed_，覆盖你的全部订阅）。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return [{"error": _TOKEN_REQUIRED_MSG}]
        return await _search_articles_impl(
            db_sink, query=query, top_k=top_k,
            content_type=content_type, source_id=source_id, source_ids=scope_ids,
            publish_date_gte=publish_date_gte,
        )

    @mcp.tool()
    async def get_rag_context(
        query: str,
        top_k: int = 8,
        max_chars: int = 4000,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        publish_date_gte: Optional[str] = None,
        context_separator: str = "\n\n---\n\n",
        subscription_token: Optional[str] = None,
    ) -> str:
        """关键词检索后组装格式化上下文字符串（来源/日期/标题/链接+正文摘录），
        可直接拼入 LLM System Prompt。
        Keyword-search the archive and assemble a formatted context string ready
        to inject into an LLM prompt. Returns empty string when nothing matches.
        query: 关键词或短语（≥3 字符）；中英文来源混杂，必要时换语言重试。
        publish_date_gte: YYYY-MM-DD.
        subscription_token: 把上下文限定在访问令牌覆盖来源内。可经本参数传入，
            或由客户端在请求头 Authorization: Bearer 提供；二者皆缺或无效将被拒绝。
        """
        ok, scope_ids = _resolve_subscription_scope(subscription_token)
        if not ok:
            return f"ERROR: {_TOKEN_REQUIRED_MSG}"
        return await _get_rag_context_impl(
            db_sink, query=query, top_k=top_k, max_chars=max_chars,
            content_type=content_type, source_id=source_id, source_ids=scope_ids,
            publish_date_gte=publish_date_gte, context_separator=context_separator,
        )

    @mcp.tool()
    def get_user_context(subscription_token: Optional[str] = None) -> dict:
        """实时拼装调用者本人的阅读侧上下文（不涉及 LLM，直接读库）。
        Assemble the caller's own reading-side profile on the fly. Four segments
        by descending priority: interest tags, recent favorites (title + tags),
        most-read sources, active subscription names.
        subscription_token: 必须是你本人的访问令牌——数据只来自令牌归属用户，
            不会返回他人数据。可经本参数传入，或由客户端在请求头
            Authorization: Bearer 提供；二者皆缺或无效将被拒绝。
        段条目上限与整体 token 预算超限时，低优先级段整段丢弃（截断 silently）。
        """
        token = subscription_token or _token_from_header()
        owner = token_owner_resolver(token) if (token and token_owner_resolver) else None
        return _get_user_context_impl(db_sink, owner, **limits)

    return mcp
