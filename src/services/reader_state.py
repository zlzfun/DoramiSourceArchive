"""读者未读体系服务 (src/services/reader_state.py)

维护「哪篇读过」的用户态：逐篇已读状态（`ReaderArticleReadStateRecord`）+
按源已读水位（`ReaderReadCursorRecord`）。与 `reader_activity`（按天聚合的
运维计量）职责分离——那边管「读了多少」，这边管未读驱动的阅读循环。

未读判定（显式覆盖优先，水位兜底）：
- 有逐篇状态行 → 行说了算（`is_read=False` 即使被水位覆盖也算未读——手动
  「标为未读」可撤销误触）；
- 无行 → 属于已订阅源 ∧ `fetched_date > 水位` 才算未读。
基准用 fetched_date 而非 publish_date（补抓历史文章不应人人弹未读）。

水位生命周期（初始化统一为「保留最近 INIT_UNREAD_BACKLOG 篇为未读」，
Folo 式——订阅/升级后立刻有可读的未读积压，而不是一片已读）：
- 订阅成功 → `init_cursor_with_backlog`（水位 = 该源第 K+1 新文章的
  fetched_date；不足 K+1 篇则空水位 = 全部未读）；
- 退订 → `drop_cursor` 清行（再订阅即重新起算 backlog）；
- 存量订阅无水位行（升级前就订着）→ `ensure_cursors` 懒初始化，同上;
- 全部标读 → `mark_all_read` 推进水位到当下并清掉被覆盖的逐篇状态行
  （防表膨胀；显式未读行同样被覆盖清除——「全部」就是全部）。

写侧由调用方掌控 commit 时机与异常吞吐（同 reader_activity 约定：
计量/状态写入绝不阻断阅读主流程）。
"""
from __future__ import annotations

import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy import delete
from sqlmodel import Session, and_, func, or_, select

from models.db import ArticleRecord, ReaderArticleReadStateRecord, ReaderReadCursorRecord


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


# 水位初始化时保留为未读的最近文章篇数（订阅成功 / 存量订阅懒初始化共用）
INIT_UNREAD_BACKLOG = 20


# ==================== 逐篇显式覆盖 ====================

def _set_read_state(session: Session, *, username: str, article_id: str, is_read: bool) -> None:
    """upsert 逐篇显式状态行（不 commit，随调用方事务提交）。"""
    username = (username or "").strip()
    article_id = (article_id or "").strip()
    if not username or not article_id:
        return
    record = session.get(ReaderArticleReadStateRecord, (username, article_id))
    if record is None:
        record = ReaderArticleReadStateRecord(
            owner_username=username, article_id=article_id,
            is_read=is_read, read_at=_now_iso(),
        )
    else:
        record.is_read = is_read
        record.read_at = _now_iso()
    session.add(record)


def mark_read(session: Session, *, username: str, article_id: str) -> None:
    """标一篇显式已读（打开文章 / 手动标已读；幂等，不 commit）。"""
    _set_read_state(session, username=username, article_id=article_id, is_read=True)


def mark_unread(session: Session, *, username: str, article_id: str) -> None:
    """标一篇显式未读（手动撤销已读；即使被水位覆盖也生效；不 commit）。"""
    _set_read_state(session, username=username, article_id=article_id, is_read=False)


def read_states_among(
    session: Session, *, username: str, article_ids: Sequence[str]
) -> Dict[str, bool]:
    """给定文章 ID 集合中，当前用户的显式状态子集 `{article_id: is_read}`。"""
    if not username or not article_ids:
        return {}
    rows = session.exec(
        select(ReaderArticleReadStateRecord).where(
            ReaderArticleReadStateRecord.owner_username == username,
            ReaderArticleReadStateRecord.article_id.in_(list(article_ids)),
        )
    ).all()
    return {row.article_id: row.is_read for row in rows}


# ==================== 按源水位 ====================

def load_cursors(session: Session, *, username: str) -> Dict[str, str]:
    """当前用户全部水位行 `{source_id: mark_read_before}`。"""
    if not username:
        return {}
    rows = session.exec(
        select(ReaderReadCursorRecord).where(
            ReaderReadCursorRecord.owner_username == username
        )
    ).all()
    return {row.source_id: row.mark_read_before for row in rows}


def _backlog_watermark(session: Session, source_id: str) -> str:
    """初始化水位 = 该源第 INIT_UNREAD_BACKLOG+1 新文章的 fetched_date——
    严格更新的最近 K 篇成为未读积压；源不足 K+1 篇则空水位（全部未读）。"""
    row = session.exec(
        select(ArticleRecord.fetched_date)
        .where(ArticleRecord.source_id == source_id)
        .order_by(ArticleRecord.fetched_date.desc())
        .offset(INIT_UNREAD_BACKLOG)
        .limit(1)
    ).first()
    return row or ""


def ensure_cursors(
    session: Session,
    *,
    username: str,
    source_ids: Iterable[str],
) -> Dict[str, str]:
    """给缺水位行的源懒初始化（backlog 语义），返回补齐后的水位映射。

    幂等：已有行不动。新建行在本函数内 commit 落库。
    """
    username = (username or "").strip()
    if not username:
        return {}
    cursors = load_cursors(session, username=username)
    missing = [sid for sid in source_ids if sid and sid not in cursors]
    if missing:
        now = _now_iso()
        for sid in missing:
            wm = _backlog_watermark(session, sid)
            session.add(ReaderReadCursorRecord(
                owner_username=username, source_id=sid,
                mark_read_before=wm, updated_at=now,
            ))
            cursors[sid] = wm
        session.commit()
    return cursors


def init_cursor_with_backlog(session: Session, *, username: str, source_id: str) -> None:
    """订阅成功时初始化/重置水位为 backlog 语义（不 commit，随订阅事务提交）。"""
    reset_cursor(
        session, username=username, source_id=source_id,
        watermark=_backlog_watermark(session, source_id),
    )


def reset_cursor(
    session: Session, *, username: str, source_id: str, watermark: Optional[str] = None
) -> None:
    """把某源水位重置为给定时刻（None = 当下；注意空字符串是合法水位 =「全部未读」，
    不可与缺省混同；不 commit，随调用方事务提交）。"""
    username = (username or "").strip()
    source_id = (source_id or "").strip()
    if not username or not source_id:
        return
    now = _now_iso()
    wm = watermark if watermark is not None else now
    record = session.get(ReaderReadCursorRecord, (username, source_id))
    if record is None:
        record = ReaderReadCursorRecord(
            owner_username=username, source_id=source_id,
            mark_read_before=wm, updated_at=now,
        )
    else:
        record.mark_read_before = wm
        record.updated_at = now
    session.add(record)


def drop_cursor(session: Session, *, username: str, source_id: str) -> None:
    """退订清水位行（不 commit，随退订事务提交）。再订阅由 reset_cursor 重新起算。"""
    if not username or not source_id:
        return
    record = session.get(ReaderReadCursorRecord, (username, source_id))
    if record is not None:
        session.delete(record)


# ==================== 未读统计 / 过滤 ====================

def _unread_condition(cursors: Dict[str, str], username: str):
    """构造未读 SQL 条件（显式覆盖优先，水位兜底）：
    （任一源命中 fetched_date > 水位 ∧ 无显式已读行）∨（订阅源内有显式未读行）。

    cursors 为空时返回 None（调用方按空集处理）。
    """
    if not cursors:
        return None
    per_source = [
        and_(ArticleRecord.source_id == sid, ArticleRecord.fetched_date > wm)
        for sid, wm in cursors.items()
    ]
    read_sub = select(ReaderArticleReadStateRecord.article_id).where(
        ReaderArticleReadStateRecord.owner_username == username,
        ReaderArticleReadStateRecord.is_read == True,  # noqa: E712
    )
    unread_sub = select(ReaderArticleReadStateRecord.article_id).where(
        ReaderArticleReadStateRecord.owner_username == username,
        ReaderArticleReadStateRecord.is_read == False,  # noqa: E712
    )
    return or_(
        and_(or_(*per_source), ArticleRecord.id.not_in(read_sub)),
        and_(
            ArticleRecord.source_id.in_(list(cursors.keys())),
            ArticleRecord.id.in_(unread_sub),
        ),
    )


def unread_counts(
    session: Session, *, username: str, source_ids: Sequence[str]
) -> Dict[str, int]:
    """按源统计未读数 `{source_id: n}`（只含 n>0 的源）。

    对给定源集合（调用方传入已订阅源）懒初始化缺失水位，故升级后首访为 0。
    """
    username = (username or "").strip()
    if not username or not source_ids:
        return {}
    cursors = ensure_cursors(session, username=username, source_ids=source_ids)
    cursors = {sid: wm for sid, wm in cursors.items() if sid in set(source_ids)}
    condition = _unread_condition(cursors, username)
    if condition is None:
        return {}
    rows = session.exec(
        select(ArticleRecord.source_id, func.count(ArticleRecord.id))
        .where(condition)
        .group_by(ArticleRecord.source_id)
    ).all()
    return {sid: int(n) for sid, n in rows if n}


def unread_filter_condition(
    session: Session, *, username: str, source_ids: Sequence[str]
):
    """给 GET /api/articles?unread_only=true 用的查询条件；无可判定源时返回 None。"""
    username = (username or "").strip()
    if not username or not source_ids:
        return None
    cursors = ensure_cursors(session, username=username, source_ids=source_ids)
    cursors = {sid: wm for sid, wm in cursors.items() if sid in set(source_ids)}
    return _unread_condition(cursors, username)


def unread_ids_among(
    session: Session, *, username: str, records: Sequence[ArticleRecord]
) -> Set[str]:
    """页级未读标记：给定文章记录集合，返回其中未读的 ID 子集。

    显式覆盖优先（is_read=False 即使无水位/被水位覆盖也算未读），水位兜底。
    只读现有水位行、不懒初始化（避免任意浏览路径写库）；无水位且无显式行的源
    视为无未读，与 unread_counts 的口径由「阅读器挂载即拉一次 unread-counts
    （会补水位）」对齐。
    """
    username = (username or "").strip()
    if not username or not records:
        return set()
    cursors = load_cursors(session, username=username)
    states = read_states_among(
        session, username=username, article_ids=[r.id for r in records]
    )
    unread: Set[str] = set()
    for r in records:
        explicit = states.get(r.id)
        if explicit is not None:
            if not explicit:
                unread.add(r.id)
            continue
        wm = cursors.get(r.source_id)
        if wm is not None and (r.fetched_date or "") > wm:
            unread.add(r.id)
    return unread


# ==================== 全部标读 ====================

def mark_all_read(
    session: Session, *, username: str, source_ids: Sequence[str]
) -> None:
    """把给定源的水位推进到当下，并清掉这些源被水位覆盖的逐篇已读行（防膨胀）。

    只清 `fetched_date <= 新水位` 的行：水位之后才入库的文章若已被逐篇读过，
    其已读行必须保留，否则会复活为未读。
    """
    username = (username or "").strip()
    ids: List[str] = [sid for sid in source_ids if sid]
    if not username or not ids:
        return
    watermark = _now_iso()
    for sid in ids:
        reset_cursor(session, username=username, source_id=sid, watermark=watermark)
    covered = select(ArticleRecord.id).where(
        ArticleRecord.source_id.in_(ids),
        ArticleRecord.fetched_date <= watermark,
    )
    session.execute(
        delete(ReaderArticleReadStateRecord).where(
            ReaderArticleReadStateRecord.owner_username == username,
            ReaderArticleReadStateRecord.article_id.in_(covered),
        )
    )
    session.commit()
