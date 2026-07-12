"""订阅与个人聚合 feed 的查询 service（阶段1 共享 helper 模块化）。

把原本散落在 app.py、被 subscriptions/public-feed/mcp/reader 多域共享的「令牌 →
作用域 → 文章」查询逻辑集中到此：订阅序列化、令牌解析订阅/归属用户、按订阅或
按用户聚合拉取文章、已订阅来源并集。

仅依赖 ORM、tokens（令牌哈希）、sources（subscription_source_ids）、articles_view
（查询过滤器）与 textutils 纯工具；自身打开 Session 的函数经 deps.get_db_sink() 取
引擎，不直接依赖 app 级可变全局，故可被任意 Router 安全 import、不与 api.app 成环。
"""

import hmac
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from api import deps
from api.articles_view import apply_article_query_filters
from api.sources import subscription_source_ids
from api.textutils import _json_loads
from api.tokens import hash_subscription_token, normalize_delivery_policy
from models.db import ArticleRecord, ReaderFeedTokenRecord, ReaderSubscriptionRecord, UserRecord


def serialize_subscription(record: ReaderSubscriptionRecord, token: Optional[str] = None) -> Dict[str, Any]:
    data = {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "filters": _json_loads(record.filters_json, {}),
        "delivery_policy": normalize_delivery_policy(_json_loads(record.delivery_policy_json, {})),
        "token_preview": record.token_preview,
        "is_active": record.is_active,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if token:
        data["token"] = token
    return data


def resolve_subscription_by_token(
        session: Session,
        subscription_id: int,
        token: str,
) -> ReaderSubscriptionRecord:
    if not token:
        raise HTTPException(status_code=401, detail="缺少订阅源访问令牌")
    record = session.get(ReaderSubscriptionRecord, subscription_id)
    if (
        not record
        or not record.is_active
        or not hmac.compare_digest(record.token_hash, hash_subscription_token(token))
    ):
        raise HTTPException(status_code=401, detail="订阅源访问令牌无效")
    return record


def query_subscription_articles(
        session: Session,
        subscription: ReaderSubscriptionRecord,
        skip: int = 0,
        limit: Optional[int] = None,
) -> tuple[list[ArticleRecord], Dict[str, Any]]:
    filters = _json_loads(subscription.filters_json, {})
    policy = normalize_delivery_policy(_json_loads(subscription.delivery_policy_json, {}))
    effective_limit = limit if limit is not None else policy["default_limit"]
    safe_limit = min(max(int(effective_limit), 1), policy["max_limit"])
    query = apply_article_query_filters(
        select(ArticleRecord),
        content_type=filters.get("content_type"),
        content_types=filters.get("content_types"),
        source_id=filters.get("source_id"),
        source_ids=filters.get("source_ids"),
        job_id=filters.get("job_id"),
        job_run_id=filters.get("job_run_id"),
        fetch_run_id=filters.get("fetch_run_id"),
        run_scope=filters.get("run_scope"),
        has_content=filters.get("has_content", True),
        search=filters.get("search"),
        publish_date_start=filters.get("publish_date_start"),
        publish_date_end=filters.get("publish_date_end"),
        fetched_date_start=filters.get("fetched_date_start"),
        fetched_date_end=filters.get("fetched_date_end"),
    )
    records = session.exec(
        query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
    ).all()
    return records, {"limit": safe_limit, "policy": policy, "filters": filters}


def resolve_subscribed_source_ids(session: Session, username: str) -> List[str]:
    """Union of source_ids across the user's active subscriptions, sorted & de-duplicated."""
    if not username:
        return []
    subs = session.exec(
        select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == username,
            ReaderSubscriptionRecord.is_active == True,  # noqa: E712
        )
    ).all()
    collected: set[str] = set()
    for sub in subs:
        collected.update(subscription_source_ids(sub))
    return sorted(collected)


def resolve_feed_token_owner(session: Session, token: str) -> Optional[str]:
    """个人聚合令牌 → 归属用户名；令牌缺失/无效返回 None。"""
    if not token:
        return None
    token_hash = hash_subscription_token(token)
    record = session.exec(
        select(ReaderFeedTokenRecord).where(ReaderFeedTokenRecord.token_hash == token_hash)
    ).first()
    return record.owner_username if record else None


def resolve_subscription_sources_by_token(token: str) -> Optional[List[str]]:
    """令牌 → 检索可见的 source_id 列表（供 MCP 个性化作用域使用）。

    支持两类令牌：单订阅令牌（``dsub_``）限定到该订阅；个人聚合令牌（``dfeed_``）
    限定到该用户全部订阅的并集。令牌无效返回 None。
    """
    if not token:
        return None
    token_hash = hash_subscription_token(token)
    with Session(deps.get_db_sink().engine) as session:
        record = session.exec(
            select(ReaderSubscriptionRecord).where(
                ReaderSubscriptionRecord.token_hash == token_hash,
                ReaderSubscriptionRecord.is_active == True,  # noqa: E712
            )
        ).first()
        if record:
            return subscription_source_ids(record)
        owner = resolve_feed_token_owner(session, token)
        if owner is not None:
            user = session.get(UserRecord, owner)
            if user is not None and user.role == "admin":
                return []  # 管理员令牌不限来源（[] 即调用方契约中的「未限定」）
            return resolve_subscribed_source_ids(session, owner)
    return None


def feed_articles_for_owner(
        session: Session,
        username: str,
        *,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_ids: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
) -> list[ArticleRecord]:
    """聚合拉取：当前用户全部已订阅来源的文章，按发布时间倒序。

    仅按发布时间过滤（不暴露归档时间这一内部细节）。若调用方传入 source_ids，
    取其与已订阅集合的交集，避免越权拉取未订阅来源。

    管理员例外：admin 不设订阅（订阅是读者面的概念），其聚合令牌直通全库——
    不按订阅收窄，显式传入的 source_ids 原样生效（与 MCP 侧「返回 [] = 不限
    来源」的管理员语义一致）。
    """
    requested = [s.strip() for s in (source_ids or "").split(",") if s.strip()]
    user = session.get(UserRecord, username)
    if user is not None and user.role == "admin":
        scoped_csv = ",".join(requested) if requested else None
    else:
        subscribed = resolve_subscribed_source_ids(session, username)
        if not subscribed:
            return []
        allowed = [s for s in requested if s in set(subscribed)] if requested else subscribed
        if not allowed:
            return []
        scoped_csv = ",".join(allowed)
    query = apply_article_query_filters(
        select(ArticleRecord),
        content_type=content_type,
        content_types=content_types,
        source_ids=scoped_csv,
        has_content=has_content,
        search=search,
        publish_date_start=publish_date_start,
        publish_date_end=publish_date_end,
    )
    return session.exec(
        query.order_by(ArticleRecord.publish_date.desc()).offset(skip).limit(limit)
    ).all()
