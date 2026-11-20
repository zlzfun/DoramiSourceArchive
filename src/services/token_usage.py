"""令牌交付计量服务 (src/services/token_usage.py,issue #312)

读取类令牌(``dfeed_`` 个人聚合令牌 / ``dsub_`` 单订阅令牌)每一次成功取走内容,按
「日期 × 账号 × 令牌 × 接口」聚合落库(``TokenDeliveryUsageRecord``),给运维面把
**账户 → 它的令牌 → 接口调用与调用量** 连起来。

口径:
- 一次调用 = 令牌有效、成功返回的一次请求(MCP 是带令牌的一次工具调用);
- 条数 = 这次返回了多少条(文章 / 事件 / 检索结果;取单篇与事件详情记 1);
- 认不出的令牌不记(不知道是谁的)。一枚令牌作废之后仍被调,只在它以前留过记录
  (能对上账号)时,在它名下的 ``rejected`` 加一;
- 一枚令牌是不是「现用」不落库:读的时候拿指纹与令牌表当前的哈希比对。

写侧 ``record_call`` / ``record_rejected`` 由交付路径调用(见 ``api.delivery_metering``),
计量绝不阻断交付:写入异常由调用方吞掉。读侧给账户列表(``calls_by_user``)、页头汇总
(``window_summary``)、单用户抽屉(``summarize_user``)与读者自己的令牌卡(``feed_token_usage``)。
本模块只依赖 ORM,不认识令牌明文——哈希由调用方算好传进来。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, func, select

from models.db import ReaderFeedTokenRecord, ReaderSubscriptionRecord, TokenDeliveryUsageRecord

KIND_FEED = "feed"
KIND_SUBSCRIPTION = "subscription"

FINGERPRINT_LENGTH = 16

# 接口(channel)→ 所属的一组。界面按组上色、按组画每日走势;新接口先在这里登记。
GROUP_ARTICLES = "articles"
GROUP_EVENTS = "events"
GROUP_SUBSCRIPTION = "subscription"
GROUP_MCP = "mcp"
GROUPS = (GROUP_ARTICLES, GROUP_EVENTS, GROUP_SUBSCRIPTION, GROUP_MCP)

FEED_ARTICLES = "feed/articles"
FEED_ARTICLES_MARKDOWN = "feed/articles.md"
FEED_ARTICLES_ATOM = "feed/articles.xml"
FEED_EVENTS = "feed/events"
FEED_EVENT_DETAIL = "feed/events/{id}"
SUBSCRIPTION_ARTICLES = "subscriptions/{id}/articles"
SUBSCRIPTION_SEARCH = "subscriptions/{id}/vector/search"
MCP_PREFIX = "mcp:"

CHANNEL_GROUPS: Dict[str, str] = {
    FEED_ARTICLES: GROUP_ARTICLES,
    FEED_ARTICLES_MARKDOWN: GROUP_ARTICLES,
    FEED_ARTICLES_ATOM: GROUP_ARTICLES,
    FEED_EVENTS: GROUP_EVENTS,
    FEED_EVENT_DETAIL: GROUP_EVENTS,
    SUBSCRIPTION_ARTICLES: GROUP_SUBSCRIPTION,
    SUBSCRIPTION_SEARCH: GROUP_SUBSCRIPTION,
}


def mcp_channel(tool: str) -> str:
    return f"{MCP_PREFIX}{tool}"


def channel_group(channel: str) -> str:
    if channel.startswith(MCP_PREFIX):
        return GROUP_MCP
    return CHANNEL_GROUPS.get(channel, GROUP_ARTICLES)


def fingerprint(token_hash: Optional[str]) -> str:
    return (token_hash or "")[:FINGERPRINT_LENGTH]


@dataclass(frozen=True)
class TokenIdentity:
    """一枚已通过校验的令牌是谁的、是哪一枚。"""

    username: str
    kind: str
    token_hash: str
    preview: str = ""
    subscription_id: Optional[int] = None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.token_hash)


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _since(days: int) -> str:
    days = max(1, min(int(days or 30), 365))
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()


def _window_days(days: int) -> int:
    return max(1, min(int(days or 30), 365))


# ── 写 ───────────────────────────────────────────────────────────────────────

def record_call(
    session: Session,
    identity: TokenIdentity,
    channel: str,
    *,
    items: int = 0,
    day: Optional[str] = None,
    at: Optional[str] = None,
    commit: bool = True,
) -> None:
    """把一次成功调用累加进当天这枚令牌、这个接口的聚合行(不存在则建)。

    没有归属账号的令牌(历史全局订阅,``owner_username`` 为空)不记:记了也挂不到任何账户上。
    """
    username = (identity.username or "").strip()
    fp = identity.fingerprint
    if not username or not fp or not channel:
        return
    now = at or _now_iso()
    count = max(0, int(items or 0))
    table = TokenDeliveryUsageRecord.__table__
    # 列名 items 与列集合的 .items() 方法重名,只能按下标取(table.c["items"])。
    stmt = sqlite_insert(table).values(
        day=day or _today(),
        username=username,
        token_kind=identity.kind,
        token_fp=fp,
        token_preview=identity.preview or "",
        subscription_id=identity.subscription_id,
        channel=channel,
        calls=1,
        items=count,
        rejected=0,
        last_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "username", "token_fp", "channel"],
        set_={
            "calls": table.c.calls + 1,
            "items": table.c["items"] + count,
            "last_at": now,
            "updated_at": now,
        },
    )
    session.execute(stmt)
    if commit:
        session.commit()


def record_rejected(
    session: Session,
    token_hash: str,
    channel: str,
    *,
    day: Optional[str] = None,
    at: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """一枚没通过校验的令牌:若它以前留过记录(= 曾经有效、现在作废了),在它名下记一次被拒。

    认不出的令牌返回 False、什么都不写——随手试出来的字符串不该在库里留行。
    """
    fp = fingerprint(token_hash)
    if not fp or not channel:
        return False
    known = session.exec(
        select(TokenDeliveryUsageRecord)
        .where(TokenDeliveryUsageRecord.token_fp == fp)
        .order_by(TokenDeliveryUsageRecord.day.desc(), TokenDeliveryUsageRecord.id.desc())
    ).first()
    if known is None:
        return False
    now = at or _now_iso()
    table = TokenDeliveryUsageRecord.__table__
    stmt = sqlite_insert(table).values(
        day=day or _today(),
        username=known.username,
        token_kind=known.token_kind,
        token_fp=fp,
        token_preview=known.token_preview,
        subscription_id=known.subscription_id,
        channel=channel,
        calls=0,
        items=0,
        rejected=1,
        last_at=None,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "username", "token_fp", "channel"],
        set_={"rejected": table.c.rejected + 1, "updated_at": now},
    )
    session.execute(stmt)
    if commit:
        session.commit()
    return True


def tombstone_user(session: Session, username: str, tombstone: str) -> None:
    """删号:把该账号的计量行合并式改写到墓碑名下(不提交,由删号事务收口)。

    与 AI 用量 / 阅读计量同一语义(``accounts._merge_into_tombstone``):计数守恒,同名账号
    「删 → 重建 → 再删」不撞聚合键唯一索引。这里多带几列非键非计数的描述列,所以单写一份。
    """
    table = TokenDeliveryUsageRecord.__table__
    rows = session.exec(
        select(TokenDeliveryUsageRecord).where(TokenDeliveryUsageRecord.username == username)
    ).all()
    for row in rows:
        stmt = sqlite_insert(table).values(
            day=row.day,
            username=tombstone,
            token_kind=row.token_kind,
            token_fp=row.token_fp,
            token_preview=row.token_preview,
            subscription_id=row.subscription_id,
            channel=row.channel,
            calls=row.calls,
            items=row.items,
            rejected=row.rejected,
            last_at=row.last_at,
            updated_at=row.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["day", "username", "token_fp", "channel"],
            set_={
                "calls": table.c.calls + row.calls,
                "items": table.c["items"] + row.items,
                "rejected": table.c.rejected + row.rejected,
                "last_at": func.max(func.coalesce(table.c.last_at, ""), row.last_at or ""),
                "updated_at": func.max(table.c.updated_at, row.updated_at),
            },
        )
        session.execute(stmt)
        session.delete(row)


# ── 读 ───────────────────────────────────────────────────────────────────────

def _account_rows_filter():
    """账户口径:排除删号墓碑与游客身份(同阅读 / 登录计量)。"""
    return (
        TokenDeliveryUsageRecord.username.not_like("deleted:%"),
        TokenDeliveryUsageRecord.username.not_like("guest:%"),
    )


def calls_by_user(session: Session, *, days: int = 30) -> Dict[str, int]:
    """窗口内按账号聚合 ``{username: 调用次数}``,供账户列表批量富化与活跃榜。"""
    rows = session.exec(
        select(
            TokenDeliveryUsageRecord.username,
            func.coalesce(func.sum(TokenDeliveryUsageRecord.calls), 0),
        )
        .where(TokenDeliveryUsageRecord.day >= _since(days), *_account_rows_filter())
        .group_by(TokenDeliveryUsageRecord.username)
    ).all()
    return {username: int(total or 0) for username, total in rows if int(total or 0) > 0}


def window_summary(session: Session, *, days: int = 30) -> Dict[str, int]:
    """窗口内全站合计:调用次数、返回条数、在用的令牌数(窗口内至少成功调过一次)。"""
    since = _since(days)
    calls, items = session.exec(
        select(
            func.coalesce(func.sum(TokenDeliveryUsageRecord.calls), 0),
            func.coalesce(func.sum(TokenDeliveryUsageRecord.items), 0),
        ).where(TokenDeliveryUsageRecord.day >= since, *_account_rows_filter())
    ).one()
    active = session.exec(
        select(TokenDeliveryUsageRecord.username, TokenDeliveryUsageRecord.token_fp)
        .where(
            TokenDeliveryUsageRecord.day >= since,
            TokenDeliveryUsageRecord.calls > 0,
            *_account_rows_filter(),
        )
        .distinct()
    ).all()
    return {"calls": int(calls or 0), "items": int(items or 0), "tokens_active": len(active)}


def _later(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def summarize_user(session: Session, username: str, *, days: int = 30) -> Dict[str, Any]:
    """单账号窗口内的令牌交付全貌:合计、令牌清单(各带接口明细)、逐日 × 接口组。

    列出的令牌 = 现用的聚合令牌(没调过也列)+ 窗口内留过记录的任何令牌(含已作废的、
    订阅令牌)。订阅令牌人人每个订阅都自带一枚,没调过的不列。现用的排前,同档按调用多寡。
    """
    window = _window_days(days)
    since = _since(days)
    rows: List[TokenDeliveryUsageRecord] = list(
        session.exec(
            select(TokenDeliveryUsageRecord).where(
                TokenDeliveryUsageRecord.day >= since,
                TokenDeliveryUsageRecord.username == username,
            )
        ).all()
    )
    feed_token = session.get(ReaderFeedTokenRecord, username)
    current_feed_fp = fingerprint(feed_token.token_hash) if feed_token else ""
    sub_ids = sorted({row.subscription_id for row in rows if row.subscription_id is not None})
    subscriptions: Dict[int, ReaderSubscriptionRecord] = {}
    if sub_ids:
        for record in session.exec(
            select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.id.in_(sub_ids))
        ).all():
            subscriptions[record.id] = record

    def _is_current(kind: str, fp: str, subscription_id: Optional[int]) -> bool:
        if kind == KIND_FEED:
            return bool(current_feed_fp) and fp == current_feed_fp
        record = subscriptions.get(subscription_id) if subscription_id is not None else None
        return bool(
            record is not None
            and record.is_active
            and record.owner_username == username
            and fingerprint(record.token_hash) == fp
        )

    tokens: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[tuple, Dict[str, int]] = {}
    total_calls = total_items = 0
    for row in rows:
        token = tokens.get(row.token_fp)
        if token is None:
            subscription = None
            if row.token_kind == KIND_SUBSCRIPTION and row.subscription_id is not None:
                record = subscriptions.get(row.subscription_id)
                subscription = {"id": row.subscription_id, "name": record.name if record else ""}
            token = tokens[row.token_fp] = {
                "kind": row.token_kind,
                "fingerprint": row.token_fp,
                "preview": row.token_preview,
                "subscription": subscription,
                "status": "current" if _is_current(row.token_kind, row.token_fp, row.subscription_id) else "retired",
                "calls": 0,
                "items": 0,
                "rejected": 0,
                "last_at": None,
                "_channels": {},
            }
        token["calls"] += row.calls
        token["items"] += row.items
        token["rejected"] += row.rejected
        token["last_at"] = _later(token["last_at"], row.last_at)
        channel = token["_channels"].setdefault(
            row.channel,
            {"channel": row.channel, "group": channel_group(row.channel), "calls": 0, "items": 0, "last_at": None},
        )
        channel["calls"] += row.calls
        channel["items"] += row.items
        channel["last_at"] = _later(channel["last_at"], row.last_at)
        if row.calls:
            cell = by_day.setdefault((row.day, channel["group"]), {"calls": 0, "items": 0})
            cell["calls"] += row.calls
            cell["items"] += row.items
        total_calls += row.calls
        total_items += row.items

    if feed_token is not None and current_feed_fp not in tokens:
        tokens[current_feed_fp] = {
            "kind": KIND_FEED,
            "fingerprint": current_feed_fp,
            "preview": feed_token.token_preview,
            "subscription": None,
            "status": "current",
            "calls": 0,
            "items": 0,
            "rejected": 0,
            "last_at": None,
            "_channels": {},
        }

    token_list = []
    for token in tokens.values():
        channels = [c for c in token.pop("_channels").values() if c["calls"] > 0]
        channels.sort(key=lambda c: (-c["calls"], c["channel"]))
        token["channels"] = channels
        # 窗口内只有「被拒」而没有任何成功调用、且已作废的令牌仍列出:管理员要看的正是「谁还在用旧令牌」。
        token_list.append(token)
    token_list.sort(key=lambda t: (t["status"] != "current", -t["calls"], t["preview"]))

    return {
        "window_days": window,
        "totals": {"calls": total_calls, "items": total_items},
        "tokens": token_list,
        "by_day": [
            {"day": day, "group": group, "calls": cell["calls"], "items": cell["items"]}
            for (day, group), cell in sorted(by_day.items())
        ],
    }


def feed_token_usage(session: Session, username: str, token_hash: Optional[str], *, days: int = 30) -> Dict[str, Any]:
    """读者自己那枚现用聚合令牌的使用情况:窗口内调用次数 + 最近一次(不限窗口)。"""
    window = _window_days(days)
    fp = fingerprint(token_hash)
    if not fp:
        return {"window_days": window, "calls": 0, "last_used_at": None}
    calls = session.exec(
        select(func.coalesce(func.sum(TokenDeliveryUsageRecord.calls), 0)).where(
            TokenDeliveryUsageRecord.username == username,
            TokenDeliveryUsageRecord.token_fp == fp,
            TokenDeliveryUsageRecord.day >= _since(days),
        )
    ).one()
    last = session.exec(
        select(func.max(TokenDeliveryUsageRecord.last_at)).where(
            TokenDeliveryUsageRecord.username == username,
            TokenDeliveryUsageRecord.token_fp == fp,
        )
    ).one()
    return {"window_days": window, "calls": int(calls or 0), "last_used_at": last or None}
