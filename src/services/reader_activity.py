"""阅读活动计量服务 (src/services/reader_activity.py)

把读者在阅读器中「主动打开一篇文章」的事件按「日期 × 用户 × 来源」聚合落库
（`ReaderReadRecord`），并为运维面板提供窗口聚合读取。

写侧 `record_read` 由 `POST /api/reader/articles/{id}/read` 驱动；读侧
`reads_by_user` 富化账户列表、`summarize_user_reads` 供单用户详情。计量绝不
阻断阅读主流程：写入异常由调用方吞掉。
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from models.db import ReaderReadRecord


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _since(days: int) -> str:
    days = max(1, min(int(days or 30), 365))
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()


def record_read(
    session: Session,
    *,
    username: str,
    source_id: str,
    day: Optional[str] = None,
) -> None:
    """把一次阅读累加进当天该来源的聚合行（不存在则建）。空用户/来源静默跳过。"""
    username = (username or "").strip()
    source_id = (source_id or "").strip()
    if not username or not source_id:
        return
    day = day or _today()
    record = session.exec(
        select(ReaderReadRecord).where(
            ReaderReadRecord.day == day,
            ReaderReadRecord.username == username,
            ReaderReadRecord.source_id == source_id,
        )
    ).first()
    if record is None:
        record = ReaderReadRecord(
            day=day, username=username, source_id=source_id, reads=0, updated_at=_now_iso()
        )
    record.reads += 1
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()


def reads_by_user(session: Session, *, days: int = 30) -> Dict[str, int]:
    """窗口内按用户聚合 `{username: total_reads}`，供账户列表批量富化。"""
    since = _since(days)
    rows: List[ReaderReadRecord] = list(
        session.exec(select(ReaderReadRecord).where(ReaderReadRecord.day >= since)).all()
    )
    out: Dict[str, int] = {}
    for row in rows:
        out[row.username] = out.get(row.username, 0) + row.reads
    return out


def reads_by_source(session: Session, *, days: Optional[int] = None) -> Dict[str, int]:
    """按来源聚合阅读次数 `{source_id: total_reads}`，供内容看板各源热度。

    与收藏/订阅同口径——默认全量（`days=None`，不设时间窗口）；传入 `days` 时
    只统计窗口内。
    """
    query = select(ReaderReadRecord)
    if days is not None:
        query = query.where(ReaderReadRecord.day >= _since(days))
    rows: List[ReaderReadRecord] = list(session.exec(query).all())
    out: Dict[str, int] = {}
    for row in rows:
        out[row.source_id] = out.get(row.source_id, 0) + row.reads
    return out


def summarize_user_reads(session: Session, username: str, *, days: int = 30) -> Dict[str, Any]:
    """单用户窗口阅读聚合：total + by_source（各源浏览次数排行）+ by_day（每日趋势）。"""
    since = _since(days)
    rows: List[ReaderReadRecord] = list(
        session.exec(
            select(ReaderReadRecord).where(
                ReaderReadRecord.day >= since,
                ReaderReadRecord.username == username,
            )
        ).all()
    )
    total = 0
    by_source: Dict[str, int] = {}
    by_day: Dict[str, int] = {}
    for row in rows:
        total += row.reads
        by_source[row.source_id] = by_source.get(row.source_id, 0) + row.reads
        by_day[row.day] = by_day.get(row.day, 0) + row.reads

    by_source_list = sorted(
        [{"source_id": k, "reads": v} for k, v in by_source.items()],
        key=lambda x: x["reads"],
        reverse=True,
    )
    by_day_list = sorted(
        [{"day": k, "reads": v} for k, v in by_day.items()], key=lambda x: x["day"]
    )
    return {"total": total, "by_source": by_source_list, "by_day": by_day_list}
