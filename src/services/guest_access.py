"""游客访问 (src/services/guest_access.py, issue #213)

身份口径见 ``services/accounts``:共享保留账号 ``guest:shared``(订阅范围 / 共享早报 / 授权)+
每位访客一个身份 ``guest:v:<vid>``(已读、AI 计次、阅读计量按它分开)。门控白名单在
``api/guest_policy.py``,订阅镜像在 ``api/app.sync_guest_subscriptions``。本模块只放与 HTTP 无关的
三块:总闸与访客额度(KV)、访问计量(``GuestVisitRecord``)、运维统计与访客状态回收。
"""
from __future__ import annotations

import datetime
import json
import re
import secrets
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, delete, func, select

from models.db import (
    AiUsageRecord,
    AnnouncementDismissRecord,
    AppSettingRecord,
    GuestVisitRecord,
    ReaderArticleReadStateRecord,
    ReaderReadCursorRecord,
    ReaderReadRecord,
    UserRecord,
)
from services import accounts

# 游客总闸:KV,缺省开(2026-10-22 拍板)。关闸即停用保留账号并轮换世代,在线游客当场失效。
GUEST_ACCESS_ENABLED_KEY = "guest_access_enabled"
# 每位游客的 AI 日额度覆盖:KV JSON {key: int},只存管理员改过的项;缺项 = 与正式读者同额。
GUEST_AI_LIMITS_KEY = "guest_ai_daily_limits"
# 额度键与读者面一致:translate / ask / summarize 按调用计,ondemand = 播客导读 + 文章旁白共池。
GUEST_AI_LIMIT_KEYS = ("translate", "ask", "summarize", "ondemand")
GUEST_AI_LIMIT_MAX = 100_000

# 访问入口:当天首个入口记一次(深链 / 公开分享页 / 登录页「游客登录」/ 已有会话回访)。
ENTRIES = ("deep_link", "share_page", "login_page", "return")
# 访客 30 天无访问即回收其个人状态(已读、公告关闭);访问聚合行保留 365 天(同登录事件)。
VISITOR_STATE_TTL_DAYS = 30
VISITS_RETENTION_DAYS = 365

_VISITOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# 访客当日 AI 额度用尽:由 app 的异常处理器转成 429 {"code": GUEST_QUOTA_CODE},前端据此弹登录提示。
GUEST_QUOTA_CODE = "guest_quota_exhausted"
GUEST_QUOTA_MESSAGE = "游客今日的 AI 试用已用完，登录后继续使用"


class GuestQuotaExhausted(Exception):
    """访客当日 AI 额度用尽(issue #213)。retry_after = 距服务端下一个本地零点的秒数。"""

    def __init__(self, retry_after: Optional[int] = None):
        super().__init__(GUEST_QUOTA_MESSAGE)
        self.retry_after = retry_after


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _today() -> str:
    return datetime.date.today().isoformat()


def _since(days: int) -> str:
    days = max(1, min(int(days or 30), 365))
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()


def _get_kv(session: Session, key: str) -> Optional[str]:
    record = session.get(AppSettingRecord, key)
    return None if record is None else record.value


def _set_kv(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


# ==================== 总闸 ====================
def guest_access_enabled(session: Session) -> bool:
    """游客总闸;未配置视为开启。"""
    raw = _get_kv(session, GUEST_ACCESS_ENABLED_KEY)
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def set_guest_access_enabled(session: Session, enabled: bool) -> bool:
    _set_kv(session, GUEST_ACCESS_ENABLED_KEY, "true" if enabled else "false")
    accounts.ensure_guest_account(session, enabled=bool(enabled))
    return bool(enabled)


def guest_ai_enabled(session: Session) -> bool:
    """「游客 AI」开关 = 保留账号的 ai_beta_enabled(缺行视为开,建行时即为开)。"""
    record = session.get(UserRecord, accounts.GUEST_USERNAME)
    return True if record is None else bool(record.ai_beta_enabled)


# ==================== 访客 AI 额度 ====================
def guest_ai_limit_overrides(session: Session) -> Dict[str, int]:
    """管理员改过的每位游客日额度;损坏或缺省返回空(= 全部与读者同额)。"""
    raw = _get_kv(session, GUEST_AI_LIMITS_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, int] = {}
    for key in GUEST_AI_LIMIT_KEYS:
        value = parsed.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        out[key] = max(0, min(int(value), GUEST_AI_LIMIT_MAX))
    return out


def set_guest_ai_limit_overrides(session: Session, updates: Dict[str, Any]) -> Dict[str, int]:
    """合并写入覆盖值:int(0 = 不限)写入,None = 恢复与读者同额;未知键与非法值报错。"""
    current = guest_ai_limit_overrides(session)
    for key, value in (updates or {}).items():
        if key not in GUEST_AI_LIMIT_KEYS:
            raise ValueError(f"未知的额度项:{key}")
        if value is None:
            current.pop(key, None)
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > GUEST_AI_LIMIT_MAX:
            raise ValueError(f"额度须为 0–{GUEST_AI_LIMIT_MAX} 的整数(0 = 不限)")
        current[key] = int(value)
    _set_kv(session, GUEST_AI_LIMITS_KEY, json.dumps(current, ensure_ascii=False, sort_keys=True))
    return current


def guest_ai_limit(session: Session, key: str, reader_default: int) -> int:
    """某用途每位游客的日额度:管理员覆盖 > 与读者同额。0 = 不限。"""
    overrides = guest_ai_limit_overrides(session)
    return overrides[key] if key in overrides else int(reader_default)


# ==================== 访客身份 ====================
def new_visitor_id() -> str:
    return secrets.token_urlsafe(16)


def valid_visitor_id(visitor_id: Optional[str]) -> bool:
    return bool(visitor_id) and bool(_VISITOR_ID_RE.match(str(visitor_id)))


def visitor_id_of(username: Optional[str]) -> Optional[str]:
    """访客身份 guest:v:<vid> → vid;其余 None。"""
    name = str(username or "")
    if not name.startswith(accounts.GUEST_VISITOR_PREFIX):
        return None
    vid = name[len(accounts.GUEST_VISITOR_PREFIX):]
    return vid if valid_visitor_id(vid) else None


# ==================== 访问计量 ====================
def _upsert_visit(session: Session, visitor_id: str, *, entry: str, visits: int, now: str) -> None:
    table = GuestVisitRecord.__table__
    stmt = sqlite_insert(table).values(
        day=now[:10], visitor_id=visitor_id, entry=entry, visits=visits,
        first_seen_at=now, last_seen_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "visitor_id"],
        set_={"visits": table.c.visits + visits, "last_seen_at": now},
    )
    session.execute(stmt)
    session.commit()


def record_entry(session: Session, visitor_id: str, entry: str, *, now: Optional[str] = None) -> None:
    """签发游客会话时记当天首个入口(已有当天行则入口不变,只刷新最近出现时间)。"""
    if not valid_visitor_id(visitor_id):
        return
    _upsert_visit(session, visitor_id, entry=entry if entry in ENTRIES else "return",
                  visits=0, now=now or _now_iso())


def record_app_open(session: Session, visitor_id: str, *, now: Optional[str] = None) -> None:
    """游客打开一次应用(前端启动拉 /api/runtime)= 一次访问;当天首次出现记入口 return。"""
    if not valid_visitor_id(visitor_id):
        return
    _upsert_visit(session, visitor_id, entry="return", visits=1, now=now or _now_iso())


def record_conversion(session: Session, visitor_id: str, username: str, *, now: Optional[str] = None) -> bool:
    """这位访客在本浏览器登录成账号:记在其 30 天内最近一行上(无访问行则不算转化)。"""
    if not valid_visitor_id(visitor_id) or not username or accounts.is_guest_username(username):
        return False
    row = session.exec(
        select(GuestVisitRecord)
        .where(GuestVisitRecord.visitor_id == visitor_id)
        .where(GuestVisitRecord.day >= _since(VISITOR_STATE_TTL_DAYS))
        .order_by(GuestVisitRecord.day.desc())
        .limit(1)
    ).first()
    if row is None or row.converted_username == username:
        return False
    row.converted_username = username
    row.converted_at = now or _now_iso()
    session.add(row)
    session.commit()
    return True


# ==================== 运维统计 ====================
def _guest_owner_clause(column):
    return column.like(f"{accounts.GUEST_VISITOR_PREFIX}%")


def guest_stats(session: Session, *, days: int = 30) -> Dict[str, Any]:
    """游客区聚合:KPI(访客 / 新访客 / 访问 / 阅读 / AI 调用与 tokens / 转化)、逐日走势、入口分布。

    口径(issue #213 检视 F7):访问、入口、逐日访客按访问行的 `day` 落在窗口内;**转化按事件时间**——
    `converted_at` 落在窗口内即算,不管它记在哪一天的访问行上(转化记在 30 天内最近一行,可能早于窗口)。
    「访客」= 窗口内有访问行的访客 ∪ 窗口内转化的访客,于是转化人数恒 ≤ 访客人数,转化率 ≤ 100%。
    """
    since = _since(days)
    rows = list(session.exec(select(GuestVisitRecord).where(GuestVisitRecord.day >= since)).all())
    conversion_rows = list(session.exec(
        select(GuestVisitRecord).where(GuestVisitRecord.converted_at >= since)
    ).all())
    converted = {r.visitor_id for r in conversion_rows}
    visitor_ids = {r.visitor_id for r in rows} | converted
    earlier: set[str] = set()
    if visitor_ids:
        earlier = set(session.exec(
            select(GuestVisitRecord.visitor_id)
            .where(GuestVisitRecord.day < since)
            .where(GuestVisitRecord.visitor_id.in_(sorted(visitor_ids)))
        ).all())

    reads_total = int(session.exec(
        select(func.coalesce(func.sum(ReaderReadRecord.reads), 0))
        .where(ReaderReadRecord.day >= since)
        .where(_guest_owner_clause(ReaderReadRecord.username))
    ).one() or 0)
    ai_calls, ai_tokens = session.exec(
        select(func.coalesce(func.sum(AiUsageRecord.calls), 0), func.coalesce(func.sum(AiUsageRecord.total_tokens), 0))
        .where(AiUsageRecord.day >= since)
        .where(_guest_owner_clause(AiUsageRecord.username))
    ).one()

    by_day: Dict[str, Dict[str, int]] = {}
    for r in rows:
        slot = by_day.setdefault(r.day, {"visitors": 0, "visits": 0, "conversions": 0})
        slot["visitors"] += 1
        slot["visits"] += int(r.visits or 0)
    for r in conversion_rows:
        slot = by_day.setdefault(r.converted_at[:10], {"visitors": 0, "visits": 0, "conversions": 0})
        slot["conversions"] += 1
    entries = {key: 0 for key in ENTRIES}
    for r in rows:
        entries[r.entry if r.entry in entries else "return"] += 1

    visitors = len(visitor_ids)
    return {
        "days": int(days),
        "kpi": {
            "visitors": visitors,
            "new_visitors": len(visitor_ids - earlier),
            "visits": sum(int(r.visits or 0) for r in rows),
            "reads": reads_total,
            "ai_calls": int(ai_calls or 0),
            "ai_tokens": int(ai_tokens or 0),
            "conversions": len(converted),
            "conversion_rate": round(len(converted) / visitors, 4) if visitors else 0.0,
        },
        "by_day": [{"day": d, **v} for d, v in sorted(by_day.items())],
        "entries": entries,
        "as_of_day": _today(),
    }


# ==================== 访客状态回收 ====================
def stale_visitor_ids(session: Session, *, today: Optional[datetime.date] = None) -> List[str]:
    """30 天内没有访问行的访客(其个人状态可回收)。"""
    base = today or datetime.date.today()
    cutoff = (base - datetime.timedelta(days=VISITOR_STATE_TTL_DAYS - 1)).isoformat()
    owners: set[str] = set()
    for model in (ReaderReadCursorRecord, ReaderArticleReadStateRecord, AnnouncementDismissRecord):
        owners.update(session.exec(
            select(model.owner_username).where(_guest_owner_clause(model.owner_username)).distinct()
        ).all())
    vids = {visitor_id_of(o) for o in owners} - {None}
    if not vids:
        return []
    alive = set(session.exec(
        select(GuestVisitRecord.visitor_id)
        .where(GuestVisitRecord.day >= cutoff)
        .where(GuestVisitRecord.visitor_id.in_(sorted(vids)))
    ).all())
    return sorted(v for v in vids if v not in alive)


def purge_visitor_state(session: Session, visitor_ids: Iterable[str]) -> int:
    """删掉这些访客的已读两表与公告关闭行(不提交,随留存清理同事务);返回删除行数。"""
    names = [accounts.guest_visitor_username(v) for v in visitor_ids if valid_visitor_id(v)]
    if not names:
        return 0
    removed = 0
    for model in (ReaderReadCursorRecord, ReaderArticleReadStateRecord, AnnouncementDismissRecord):
        result = session.exec(delete(model).where(model.owner_username.in_(names)))
        removed += int(getattr(result, "rowcount", 0) or 0)
    return removed
