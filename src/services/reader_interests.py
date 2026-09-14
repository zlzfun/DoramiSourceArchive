"""读者兴趣在阅读器列表里的消费方(issue #27 第一波:兴趣即透镜)。

兴趣(关注的规范标签;v3.56 issue #27 起屏蔽一极退役,兴趣只有关注)此前只有个人早报一个消费方。
本模块把它接到文章列表:
- ``interest_filter_condition``:「兴趣」谓词——文章命中读者关注的标签(SQL exists,与
  订阅 / 收藏谓词 AND 联合;三谓词两两正交,由 ``GET /api/articles`` 组合);
- ``favorite_filter_condition``:「收藏」谓词;
- ``annotate_interest``:逐条标注 ``interest_hits``(命中的关注标签名)——列表侧的命中胶囊据此渲染。

准入门槛(``INTEREST_MATCH_MIN_RELEVANCE``):兴趣谓词决定的是「放什么进来」而非「筛什么」——
订阅关掉时它在全站范围内生效,所以只认主标签或相关度 ≥ 0.8 的指派(开发库分布:≥0.8 占六成,
主标签每篇恰一个)。
未打标的文章在兴趣谓词下不出现、在标注里命中为空——「未知」不是「不命中」,分析积压不能让内容消失
(它们在订阅谓词下照常出现)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import and_, or_
from sqlmodel import Session, exists, select

from models.db import (
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    ReaderFavoriteRecord,
    UserInterestTagRecord,
)

INTEREST_MATCH_MIN_RELEVANCE = 0.8


@dataclass(frozen=True)
class InterestMap:
    """读者兴趣的运行时形状:关注的 tag_id → 展示名。"""

    followed: Mapping[int, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.followed


def load_interest_map(session: Session, username: str) -> InterestMap:
    """只取 active 标签(被合并 / 废弃的标签不再产生命中,与早报 ``_load_interests`` 同口径)。"""

    username = (username or "").strip()
    if not username:
        return InterestMap()
    rows = session.exec(
        select(UserInterestTagRecord.tag_id, CmsTagRecord.name_zh, CmsTagRecord.name_en)
        .join(CmsTagRecord, CmsTagRecord.id == UserInterestTagRecord.tag_id)
        .where(
            UserInterestTagRecord.owner_username == username,
            CmsTagRecord.status == "active",
        )
    ).all()
    followed: dict[int, str] = {}
    for tag_id, name_zh, name_en in rows:
        followed[int(tag_id)] = (name_zh or name_en or "").strip() or str(tag_id)
    return InterestMap(followed=followed)


def _assignment_qualifies():
    return or_(
        ArticleTagAssignmentRecord.is_primary.is_(True),
        ArticleTagAssignmentRecord.relevance >= INTEREST_MATCH_MIN_RELEVANCE,
    )


def interest_filter_condition(followed_tag_ids: Iterable[int]):
    """「兴趣」谓词:文章至少有一条命中关注标签且过准入门槛的指派。

    无关注标签时返回恒假条件(显式空集,与 ``subscribed_scope=only`` 零订阅的 ``__none__`` 哨兵同理)。
    """

    ids = sorted({int(value) for value in followed_tag_ids})
    if not ids:
        return ArticleRecord.id.in_(["__none__"])
    return exists(
        select(1).where(
            ArticleTagAssignmentRecord.article_id == ArticleRecord.id,
            ArticleTagAssignmentRecord.tag_id.in_(ids),
            _assignment_qualifies(),
        )
    )


def favorite_filter_condition(username: str):
    """「收藏」谓词:当前用户收藏过这篇。"""

    username = (username or "").strip()
    if not username:
        return ArticleRecord.id.in_(["__none__"])
    return exists(
        select(1).where(
            ReaderFavoriteRecord.owner_username == username,
            ReaderFavoriteRecord.article_id == ArticleRecord.id,
        )
    )


def interest_matches(tags: Sequence[Mapping[str, Any]], interests: InterestMap) -> list[str]:
    """按一篇文章的规范标签指派算命中关注名单;名单按目录序(指派序即主标签优先)。"""

    if interests.empty or not tags:
        return []
    hits: list[str] = []
    for tag in tags:
        try:
            tag_id = int(tag.get("id"))
        except (TypeError, ValueError):
            continue
        if tag_id in interests.followed and interests.followed[tag_id] not in hits:
            qualifies = bool(tag.get("is_primary")) or float(tag.get("relevance") or 0.0) >= INTEREST_MATCH_MIN_RELEVANCE
            if qualifies:
                hits.append(interests.followed[tag_id])
    return hits


def annotate_interest(
    items: Iterable[dict[str, Any]],
    tags_by_article: Mapping[str, Sequence[Mapping[str, Any]]],
    interests: InterestMap,
) -> None:
    """就地给列表项补 ``interest_hits``;无兴趣时为空列表(形状稳定)。"""

    for item in items:
        item["interest_hits"] = interest_matches(tags_by_article.get(item.get("id"), ()), interests)


__all__ = [
    "INTEREST_MATCH_MIN_RELEVANCE",
    "InterestMap",
    "annotate_interest",
    "favorite_filter_condition",
    "interest_filter_condition",
    "interest_matches",
    "load_interest_map",
]
