"""运维首页聚合(issue #114):首页磁贴用现有接口拿不到的三项,一次读齐。

- **逐日活跃账户**:活跃 = 当天登录过或阅读过。按账户口径统计,排除游客身份 ``guest:*``
  与删号墓碑 ``deleted:*``。登录事件按本地日期归日(``at`` 的前 10 位,与
  ``reader_reads.day`` 同为本地日期)。窗口合计是窗口内去重后的账户数,不是逐日相加。
- **窗口内各源阅读**:``reader_reads`` 在窗口内按 ``source_id`` 求和。与
  ``/api/admin/content`` 的各源阅读同口径(含游客阅读),区别只在加了时间窗;
  ``/content`` 给的是累计值。
- **全量分析回填的失败汇总**:``tag_retag_jobs`` 里 ``operation='full_analysis'`` 的任务。
  「未解决的失败」= 终态时间(``updated_at``)晚于最近一次成功的 failed / partial_failed 任务数,
  新的一次成功即视为已处理;按时间而非 id 比较——「重试失败项」沿用原任务 id,旧任务重试后再失败也要算(codex r1 #2)。
  另给进行中数与最近更新的一条任务。

只读、纯聚合,不含逐账户明细,全体管理员可见(与 ``account-growth`` 同类)。
"""

import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, func, select

from models.db import LoginEventRecord, ReaderReadRecord, TagRetagJobRecord

MAX_DAYS = 365
_FULL_ANALYSIS = "full_analysis"
_ACTIVE_JOB_STATUSES = ("queued", "running", "paused")
_FAILED_JOB_STATUSES = ("failed", "partial_failed")
_EXCLUDED_PREFIXES = ("guest:", "deleted:")


def clamp_days(days: Optional[int]) -> int:
    return max(1, min(int(days or 30), MAX_DAYS))


def window_days(days: int, today: Optional[datetime.date] = None) -> List[str]:
    """窗口内的本地日期,旧 → 新,含今天。"""
    today = today or datetime.date.today()
    n = clamp_days(days)
    return [(today - datetime.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _is_account(username: str) -> bool:
    return bool(username) and not username.startswith(_EXCLUDED_PREFIXES)


def active_accounts(session: Session, *, days: int, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """窗口内逐日活跃账户数 + 窗口去重合计。逐日补零,序列长度恒为 days。"""
    day_list = window_days(days, today)
    since, until = day_list[0], day_list[-1]
    by_day: Dict[str, set] = {}

    # 过滤用原列范围(走 ix_login_events_at),只在投影上截日期;函数包住索引列会退化成全表扫(codex r1 #6)。
    after_until = (datetime.date.fromisoformat(until) + datetime.timedelta(days=1)).isoformat()
    login_day = func.substr(LoginEventRecord.at, 1, 10)
    login_rows = session.exec(
        select(login_day, LoginEventRecord.username)
        .where(LoginEventRecord.at >= since, LoginEventRecord.at < after_until)
        .distinct()
    ).all()
    read_rows = session.exec(
        select(ReaderReadRecord.day, ReaderReadRecord.username)
        .where(
            ReaderReadRecord.day >= since,
            ReaderReadRecord.day <= until,
            ReaderReadRecord.reads > 0,
        )
        .distinct()
    ).all()
    for day, username in [*login_rows, *read_rows]:
        if _is_account(username):
            by_day.setdefault(day, set()).add(username)

    window_users = set().union(*by_day.values()) if by_day else set()
    return {
        "accounts": len(window_users),
        "series": [{"day": d, "accounts": len(by_day.get(d, ()))} for d in day_list],
    }


def reads_by_source(session: Session, *, days: int, today: Optional[datetime.date] = None) -> Dict[str, int]:
    """窗口内各源阅读次数 ``{source_id: reads}``(含游客,与 /admin/content 同口径)。"""
    day_list = window_days(days, today)
    rows = session.exec(
        select(ReaderReadRecord.source_id, func.coalesce(func.sum(ReaderReadRecord.reads), 0))
        .where(ReaderReadRecord.day >= day_list[0], ReaderReadRecord.day <= day_list[-1])
        .group_by(ReaderReadRecord.source_id)
    ).all()
    return {source_id: int(total or 0) for source_id, total in rows if int(total or 0) > 0}


def backfill_summary(session: Session) -> Dict[str, Any]:
    """全量分析回填:进行中数、最近一次成功之后的失败任务数、最近一条任务。"""
    jobs = TagRetagJobRecord
    base = jobs.operation == _FULL_ANALYSIS
    active = session.exec(
        select(func.count(jobs.id)).where(base, jobs.status.in_(_ACTIVE_JOB_STATUSES))
    ).one()
    last_success_at = session.exec(
        select(func.max(jobs.updated_at)).where(base, jobs.status == "succeeded")
    ).one()
    failed_query = select(func.count(jobs.id)).where(base, jobs.status.in_(_FAILED_JOB_STATUSES))
    if last_success_at is not None:
        failed_query = failed_query.where(jobs.updated_at > last_success_at)
    unresolved_failed = session.exec(failed_query).one()
    latest = session.exec(
        select(jobs).where(base).order_by(jobs.updated_at.desc(), jobs.id.desc()).limit(1)
    ).first()
    return {
        "active": int(active or 0),
        "unresolved_failed": int(unresolved_failed or 0),
        "latest": None
        if latest is None
        else {
            "id": latest.id,
            "status": latest.status,
            "failed_count": int(latest.failed_count or 0),
            "updated_at": latest.updated_at,
        },
    }
