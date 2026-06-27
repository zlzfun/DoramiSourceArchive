"""内容看板聚合服务 (src/services/content_analytics.py)

为运维管理「内容看板」提供纯读侧聚合：每源的文章数/内容类型分布/最新抓取
时间/向量化数，以及收藏数（按源 + 按文章），全部来自已有归档表，无需新埋点。

本服务只做 DB-pure 聚合（不依赖 app.py 的展示辅助函数，避免导入环）：
source_id → 展示名/图标/分类、订阅数展开均由 `GET /api/admin/content` 端点
在拿到聚合结果后再富化。
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlmodel import Session, select
from sqlalchemy import func, Integer

from models.db import ArticleRecord, ReaderFavoriteRecord


def summarize(session: Session, *, top_n: int = 12) -> Dict[str, Any]:
    """内容侧聚合：by_source + favorites_by_source + top_articles + totals。

    - by_source: {source_id: {article_count, vectorized_count, last_fetched,
      primary_content_type, content_types: {type: count}}}
    - favorites_by_source: {source_id: 收藏数}
    - top_articles: 收藏数最高的 top_n 篇文章（文章级收藏榜）
    - totals: {articles, vectorized, favorites}
    """
    top_n = max(1, min(int(top_n or 12), 100))

    # —— 每源文章聚合（复刻 reader/sources 的 group-by，追加向量化计数）——
    by_source: Dict[str, Dict[str, Any]] = {}
    article_rows = session.exec(
        select(
            ArticleRecord.source_id,
            ArticleRecord.content_type,
            func.count(ArticleRecord.id),
            func.max(ArticleRecord.fetched_date),
            func.sum(func.cast(ArticleRecord.is_vectorized, Integer)),
        )
        .where(ArticleRecord.source_id.isnot(None))
        .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
    ).all()

    totals_articles = 0
    totals_vectorized = 0
    for source_id, content_type, count, last_fetched, vec_count in article_rows:
        if not source_id:
            continue
        count = int(count or 0)
        vec_count = int(vec_count or 0)
        totals_articles += count
        totals_vectorized += vec_count
        entry = by_source.get(source_id)
        if entry is None:
            entry = {
                "article_count": 0,
                "vectorized_count": 0,
                "last_fetched": "",
                "primary_content_type": content_type or "",
                "content_types": {},
                "_primary_count": -1,
            }
            by_source[source_id] = entry
        entry["article_count"] += count
        entry["vectorized_count"] += vec_count
        if (last_fetched or "") > entry["last_fetched"]:
            entry["last_fetched"] = last_fetched or ""
        if content_type:
            entry["content_types"][content_type] = entry["content_types"].get(content_type, 0) + count
        if count > entry["_primary_count"]:
            entry["_primary_count"] = count
            entry["primary_content_type"] = content_type or ""

    for entry in by_source.values():
        entry.pop("_primary_count", None)

    # —— 收藏聚合（join 文章拿 source_id；孤儿收藏被 inner join 自然过滤）——
    fav_article_rows = session.exec(
        select(
            ArticleRecord.id,
            ArticleRecord.title,
            ArticleRecord.source_id,
            ArticleRecord.publish_date,
            func.count(ReaderFavoriteRecord.owner_username),
        )
        .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .group_by(ArticleRecord.id)
        .order_by(func.count(ReaderFavoriteRecord.owner_username).desc(), ArticleRecord.id.desc())
        .limit(top_n)
    ).all()

    top_articles: List[Dict[str, Any]] = [
        {
            "article_id": aid,
            "title": title or "",
            "source_id": source_id or "",
            "publish_date": publish_date or "",
            "favorite_count": int(fav or 0),
        }
        for aid, title, source_id, publish_date, fav in fav_article_rows
    ]

    favorites_by_source: Dict[str, int] = {}
    totals_favorites = 0
    fav_source_rows = session.exec(
        select(
            ArticleRecord.source_id,
            func.count(ReaderFavoriteRecord.owner_username),
        )
        .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .group_by(ArticleRecord.source_id)
    ).all()
    for source_id, fav in fav_source_rows:
        if not source_id:
            continue
        favorites_by_source[source_id] = int(fav or 0)
        totals_favorites += int(fav or 0)

    return {
        "by_source": by_source,
        "favorites_by_source": favorites_by_source,
        "top_articles": top_articles,
        "totals": {
            "articles": totals_articles,
            "vectorized": totals_vectorized,
            "favorites": totals_favorites,
        },
    }
