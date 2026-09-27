"""5 段 cron 表达式的单点解析。

调度注册(`api.app.add_cron_job`)、采集任务 CRUD 校验(`routers/collection.py`)与时刻表倒计时
(`_next_fire_iso`)共用同一解析语义:`分 时 日 月 周`,字段取值交给 APScheduler 的 `CronTrigger` 判定。
非 5 段与字段越界(`61 8 * * *`、`0 25 * * *`、`0 8 * nope *`)一律返回 None、不抛——
CRUD 据此在 commit 前拒绝(400),调度装载据此跳过历史脏行而不是让整个进程起不来(issue #82 PR-0 检视 F1)。
"""

from __future__ import annotations

from typing import Optional

from apscheduler.triggers.cron import CronTrigger

CRON_INVALID_DETAIL = "cron 表达式非法:需 5 段(分 时 日 月 周)且字段在取值范围内"


def parse_cron_expr(expr: str) -> Optional[CronTrigger]:
    """把 5 段 cron 解析成 CronTrigger;任何非法形态返回 None。"""
    parts = (expr or "").split()
    if len(parts) != 5:
        return None
    try:
        return CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
    except ValueError:
        return None
