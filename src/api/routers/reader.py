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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, func, select

from api import deps
from api.sources import _friendly_source_name, _registry_source_meta, subscription_source_ids
from models.db import (
    ArticleRecord,
    ReaderFavoriteRecord,
    ReaderFeedTokenRecord,
    ReaderSubscriptionRecord,
)
from services import reader_activity as reader_activity_service

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
    items = [app.serialize_article_list_item(record, include_content=include_content) for record, _ in rows]
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
    token = app.generate_feed_token()
    now = _now_iso()
    record = session.get(ReaderFeedTokenRecord, username)
    if record is None:
        record = ReaderFeedTokenRecord(owner_username=username, created_at=now, updated_at=now)
    record.token_hash = app.hash_subscription_token(token)
    record.token_preview = app.subscription_token_preview(token)
    record.updated_at = now
    session.add(record)
    session.commit()
    return {"token": token, "token_preview": app.subscription_token_preview(token)}
