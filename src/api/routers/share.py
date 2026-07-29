"""公开分享 Router：免登录只读单篇（``GET /api/public/share/{token}``）。

读者在阅读器里为一篇内容签发分享令牌（``/api/reader/articles/{id}/share``，见
routers/reader.py），拿到形如 ``#/s/{token}`` 的链接发给同事；同事无需账号即可打开
本端点渲染的只读页。签发/撤销/限额与护栏在 services/article_share.py。

门控：``/api/public/*`` 整段在鉴权中间件里免登录（``is_public_subscription_path``），
与既有的订阅令牌拉取同一豁免，故本文件无需任何中间件改动。

**响应刻意贫瘠**——只给这一篇的标题/来源/时间/正文，不带任何目录、检索、相邻篇目或
账户信息。令牌泄露的最坏后果被钉死在「这一篇被看到」，不会成为进入归档的入口。
"""

import importlib
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session

from api import deps
from models.db import ArticleRecord, ArticleShareRecord
from services import article_share as article_share_service
from services import source_visibility as source_visibility_service
from services.media_store import extract_image_urls
from storage.impl.vector_storage import friendly_source_name

router = APIRouter(tags=["share"])


def _resolve_live_share(
    session: Session, token: str
) -> Tuple[Optional[ArticleShareRecord], Optional[ArticleRecord]]:
    """文章端点与媒体端点共用的全套护栏（总闸 + 令牌 + 隐藏源），失败返回 (None, None)。"""
    if not article_share_service.public_share_enabled(session):
        return None, None
    hidden = source_visibility_service.hidden_source_ids(session)
    return article_share_service.resolve_share(session, token, hidden_source_ids=hidden)


def _friendly_source(source_id: str) -> str:
    """来源展示名：走站内取名运行时单点（现役源取注册表 name，日报等特殊源查兜底映射）。"""
    try:
        return friendly_source_name(source_id) or source_id
    except Exception:  # noqa: BLE001 — 取名失败不该让分享页打不开
        return source_id


def _serialize_shared_article(
    article: ArticleRecord, share: ArticleShareRecord
) -> Dict[str, Any]:
    return {
        "title": article.title or "",
        "content": article.content or "",
        "source_name": _friendly_source(article.source_id or ""),
        "source_url": article.source_url or "",
        "publish_date": article.publish_date,
        "content_type": article.content_type or "",
        "shared_by": share.owner_username,
        "shared_at": share.created_at,
        "expires_at": share.expires_at,
    }


@router.get("/api/public/share/{token}")
def get_shared_article(token: str, session: Session = Depends(deps.get_session)):
    """按分享令牌取单篇内容（免登录）。

    总闸关闭时一并 404：管理员停用公开分享后，已发出去的链接必须当场失效，
    否则「关掉」只是关掉了新签发，形同虚设。失败原因一律不区分（见 resolve_share）。
    """
    share, article = _resolve_live_share(session, token)
    if share is None or article is None:
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")

    article_share_service.touch_view(session, share)
    return _serialize_shared_article(article, share)


@router.get("/api/public/share/{token}/media")
async def get_shared_article_media(
    token: str,
    url: str = Query(..., description="正文中出现的原始图片 URL"),
    session: Session = Depends(deps.get_session),
):
    """分享页正文图的免登录供给。

    为什么需要它：``/api/media/proxy`` 在鉴权中间件里是 reader 登录门控（对访客 401），
    而防盗链 CDN 的源（qbitai/mmbiz——图床波的直接动因）直连也 403，没有本端点时
    分享出去的中文源文章就是整页裂图。

    为什么它不是开放图片代理：走与文章端点同一套护栏（总闸/令牌/隐藏源），且 **url
    必须属于该文章自身的图链集合**（extract_image_urls，与随文预取同一口径）——
    令牌能取到的图被钉死在「这一篇里出现过的」。不计 view_count（一篇十图会把
    打开数灌成十几次）。降级链与 media_proxy 相同：库关闭/取图失败一律 302 回源。
    """
    share, article = _resolve_live_share(session, token)
    if share is None or article is None:
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")

    target = (url or "").strip()
    if target not in extract_image_urls(article.content, article.extensions_json):
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")

    def _redirect() -> RedirectResponse:
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    store = importlib.import_module("api.app").media_store
    if store is None:
        return _redirect()
    record = await store.get_or_fetch(target)
    if record is None:
        return _redirect()
    path = store.file_path_for(record)
    if not path.is_file():
        return _redirect()
    return FileResponse(
        path,
        media_type=record.mime or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
