"""运维管理 Router（仅 admin）。

阶段1 从 app.py 迁出的运维看板域。与 accounts 域一致：
- 数据访问经 ``Depends(deps.get_session)``（动态解析 db_sink，兼容测试 monkeypatch）；
- admin 网关仍由 app.py 中间件统一强制（account_admin_required 命中 /api/admin）；
- 路由路径保持不变（prefix=/api/admin）。

只做读侧聚合 + AI Beta 全局开关读写，全部来自已有归档/订阅/收藏/用量表。
"""

import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from api import deps
from api.serializers import serialize_user
from api.sources import (
    DAILY_BRIEF_SOURCE_ID,
    DAILY_BRIEF_SOURCE_META,
    _friendly_source_name,
    _registry_source_meta,
    _source_category,
    subscription_source_ids,
)
from config import settings
from models.db import (
    ArticleRecord,
    ReaderFavoriteRecord,
    ReaderFeedTokenRecord,
    ReaderSubscriptionRecord,
)
from services import accounts as accounts_service
from services import ai_usage as ai_usage_service
from services import content_analytics as content_analytics_service
from services import daily_brief as daily_brief_service
from services import reader_activity as reader_activity_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AiBetaGlobalParams(BaseModel):
    enabled: bool


@router.get("/overview")
def admin_overview(session: Session = Depends(deps.get_session)):
    """运维统计大盘：账户分布、归档/订阅规模、AI 用量累计与全局开关状态。"""
    users = accounts_service.list_users(session)
    total_accounts = len(users)
    admin_count = sum(1 for u in users if u.role == "admin")
    reader_count = total_accounts - admin_count
    active_count = sum(1 for u in users if u.is_active)
    ai_beta_on_count = sum(1 for u in users if u.ai_beta_enabled)
    translate_total = sum(u.ai_translate_count or 0 for u in users)
    ask_total = sum(u.ai_ask_count or 0 for u in users)

    article_count = session.exec(select(func.count()).select_from(ArticleRecord)).one()
    subscription_count = session.exec(
        select(func.count()).select_from(ReaderSubscriptionRecord)
    ).one()
    feed_token_count = session.exec(
        select(func.count()).select_from(ReaderFeedTokenRecord)
    ).one()

    recent_logins = [
        {"username": u.username, "role": u.role, "last_login_at": u.last_login_at}
        for u in sorted(
            (u for u in users if u.last_login_at),
            key=lambda u: u.last_login_at,
            reverse=True,
        )[:8]
    ]

    llm_configured = daily_brief_service.resolve_llm_config(session).configured
    global_ai_on = accounts_service.ai_beta_global_enabled(session)

    return {
        "accounts": {
            "total": total_accounts,
            "admin": admin_count,
            "reader": reader_count,
            "active": active_count,
            "disabled": total_accounts - active_count,
            "ai_beta_enabled": ai_beta_on_count,
        },
        "archive": {
            "articles": int(article_count),
            "subscriptions": int(subscription_count),
            "feed_tokens": int(feed_token_count),
        },
        "ai": {
            "translate_total": translate_total,
            "ask_total": ask_total,
            "calls_total": translate_total + ask_total,
            "global_enabled": global_ai_on,
            "llm_configured": llm_configured,
        },
        "rag_enabled": settings.rag.enabled,
        "recent_logins": recent_logins,
    }


@router.get("/accounts")
def admin_list_accounts(days: int = 30, session: Session = Depends(deps.get_session)):
    """账户列表（运维视图）：基础账户字段 + 订阅数 + **近 days 天窗口指标**。

    窗口指标（ai_calls / ai_tokens 取自 AiUsageRecord 聚合，logged_in_window 由
    last_login_at 派生）让列表反映「近况」而非生命周期累计。管理员为系统唯一内置
    账号、不可管理，故不在此列表展示（仅列读者账户）。
    """
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows = session.exec(
        select(
            ReaderSubscriptionRecord.owner_username,
            func.count(),
        ).group_by(ReaderSubscriptionRecord.owner_username)
    ).all()
    sub_counts = {owner: int(count) for owner, count in rows}
    usage_map = ai_usage_service.usage_by_user(session, days=days)
    reads_map = reader_activity_service.reads_by_user(session, days=days)
    logins_map = accounts_service.logins_by_user(session, days=days)
    # 最近登录以事件流兜底快照:last_login_at 是缓存,历史疤痕可能缺失
    # (曾出现「窗口登录 241 次但最近登录为空」的矛盾行)。
    last_login_map = accounts_service.last_login_by_user(session)
    result = []
    for record in accounts_service.list_users(session):
        if record.role == "admin":
            continue
        payload = serialize_user(record)
        last_login = record.last_login_at or last_login_map.get(record.username)
        payload["last_login_at"] = last_login
        payload["subscription_count"] = sub_counts.get(record.username, 0)
        usage = usage_map.get(record.username, {"calls": 0, "total_tokens": 0})
        payload["window_days"] = days
        payload["ai_calls"] = usage["calls"]
        payload["ai_tokens"] = usage["total_tokens"]
        payload["reads"] = reads_map.get(record.username, 0)
        payload["logins"] = logins_map.get(record.username, 0)
        payload["logged_in_window"] = bool(last_login and last_login >= since)
        result.append(payload)
    return result


@router.get("/accounts/{username}/activity")
def admin_account_activity(
    username: str, days: int = 30, session: Session = Depends(deps.get_session)
):
    """单读者活动详情：近 days 天 AI 用量 + 各源阅读/收藏互动 + 登录活跃 + 账户快照。"""
    registry_meta = _registry_source_meta()
    record = accounts_service.get_user(session, username)
    if record is None or record.role == "admin":
        raise HTTPException(status_code=404, detail="账户不存在")
    usage = ai_usage_service.summarize_user(session, username, days=days)
    reads = reader_activity_service.summarize_user_reads(session, username, days=days)
    logins = accounts_service.summarize_user_logins(session, username, days=days)
    subscription_count = session.exec(
        select(func.count())
        .select_from(ReaderSubscriptionRecord)
        .where(ReaderSubscriptionRecord.owner_username == username)
    ).one()
    # 该用户各源收藏数（join 文章取 source_id；孤儿收藏被 inner join 过滤）。
    fav_rows = session.exec(
        select(ArticleRecord.source_id, func.count(ReaderFavoriteRecord.article_id))
        .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .where(ReaderFavoriteRecord.owner_username == username)
        .group_by(ArticleRecord.source_id)
    ).all()
    favorites_by_source = {sid: int(cnt) for sid, cnt in fav_rows if sid}

    # 各源互动 = 阅读 ∪ 收藏 的来源并集，每源带 reads + favorites（供分组柱状）。
    reads_by_source = {row["source_id"]: row["reads"] for row in reads["by_source"]}
    engagement = []
    for sid in set(reads_by_source) | set(favorites_by_source):
        engagement.append({
            "source_id": sid,
            "name": _friendly_source_name(sid, registry_meta),
            "reads": reads_by_source.get(sid, 0),
            "favorites": favorites_by_source.get(sid, 0),
        })
    engagement.sort(key=lambda x: (-x["reads"], -x["favorites"], x["name"]))
    return {
        "usage": usage,
        "reads": reads,
        "logins": logins,
        "source_engagement": engagement,
        "favorites_total": sum(favorites_by_source.values()),
        "account": {
            "username": record.username,
            "is_active": record.is_active,
            "ai_beta_enabled": record.ai_beta_enabled,
            # 快照缺失时以事件流兜底(与账户列表同一口径)
            "last_login_at": record.last_login_at
            or accounts_service.last_login_by_user(session).get(username),
            "ai_last_used_at": record.ai_last_used_at,
            "created_at": record.created_at,
            "subscription_count": int(subscription_count),
            "ai_translate_count": record.ai_translate_count or 0,
            "ai_ask_count": record.ai_ask_count or 0,
        },
    }


@router.get("/ai-usage")
def admin_ai_usage(days: int = 30, session: Session = Depends(deps.get_session)):
    """AI 用量看板：近 days 天按用途/用户/日期聚合的调用数与 token 消耗。"""
    return ai_usage_service.summarize(session, days=days)


@router.get("/content")
def admin_content(top: int = 12, session: Session = Depends(deps.get_session)):
    """内容看板：各源内容健康（文章数/类型/新鲜度/向量化率）+ 订阅数 + 收藏数，
    及文章级收藏热度榜（top 篇）。纯读侧聚合，全部来自已有归档/收藏/订阅表。"""
    registry_meta = _registry_source_meta()
    agg = content_analytics_service.summarize(session, top_n=top)

    # 每源订阅数：展开 active 订阅的 source_id 并集后计数（订阅表小，内存聚合）。
    subs_by_source: Dict[str, int] = {}
    active_subs = session.exec(
        select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.is_active == True  # noqa: E712
        )
    ).all()
    for sub in active_subs:
        for sid in subscription_source_ids(sub):
            subs_by_source[sid] = subs_by_source.get(sid, 0) + 1

    # 每源阅读次数（全量，与收藏/订阅同口径）。
    reads_by_source = reader_activity_service.reads_by_source(session)

    by_source = agg["by_source"]
    favorites_by_source = agg["favorites_by_source"]

    # 目录全集：有归档的源 ∪ 有订阅的源 ∪ 有收藏的源 ∪ 有阅读的源。
    source_ids = set(by_source) | set(subs_by_source) | set(favorites_by_source) | set(reads_by_source)
    sources: List[Dict[str, Any]] = []
    for source_id in source_ids:
        meta = registry_meta.get(source_id, {})
        if source_id == DAILY_BRIEF_SOURCE_ID:
            meta = {**DAILY_BRIEF_SOURCE_META, **meta}
        info = by_source.get(source_id, {})
        article_count = int(info.get("article_count", 0))
        vectorized_count = int(info.get("vectorized_count", 0))
        content_type = info.get("primary_content_type") or meta.get("content_type") or ""
        sources.append({
            "source_id": source_id,
            "name": meta.get("name") or _friendly_source_name(source_id, registry_meta),
            "icon": meta.get("icon", ""),
            "category": _source_category(content_type),
            "content_type": content_type,
            "article_count": article_count,
            "last_fetched": info.get("last_fetched", ""),
            "subscription_count": subs_by_source.get(source_id, 0),
            "favorite_count": favorites_by_source.get(source_id, 0),
            "read_count": reads_by_source.get(source_id, 0),
            "vectorized_rate": round(vectorized_count / article_count, 4) if article_count else 0.0,
        })
    sources.sort(key=lambda s: (-s["favorite_count"], -s["read_count"], -s["subscription_count"], -s["article_count"], s["name"]))

    # 富化收藏榜的源名。
    name_by_source = {s["source_id"]: s["name"] for s in sources}
    top_articles = [
        {**a, "source_name": name_by_source.get(a["source_id"]) or _friendly_source_name(a["source_id"], registry_meta)}
        for a in agg["top_articles"]
    ]

    totals = dict(agg["totals"])
    totals["sources"] = len(sources)
    totals["subscriptions"] = sum(subs_by_source.values())
    totals["reads"] = sum(reads_by_source.values())
    totals["vectorized_rate"] = (
        round(totals["vectorized"] / totals["articles"], 4) if totals.get("articles") else 0.0
    )

    return {"totals": totals, "sources": sources, "top_articles": top_articles}


@router.get("/ai-beta/global")
def admin_get_ai_beta_global(session: Session = Depends(deps.get_session)):
    return {"enabled": accounts_service.ai_beta_global_enabled(session)}


@router.post("/ai-beta/global")
def admin_set_ai_beta_global(
    params: AiBetaGlobalParams, session: Session = Depends(deps.get_session)
):
    accounts_service.set_ai_beta_global_enabled(session, params.enabled)
    return {"enabled": accounts_service.ai_beta_global_enabled(session)}
