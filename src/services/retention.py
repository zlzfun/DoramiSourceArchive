"""明细表滚动窗清理服务 (src/services/retention.py)

若干「只增不删」的埋点/明细表会随运行时长单调膨胀。本服务对它们做滚动窗
清理：超出各自保留窗口的历史行按时间字段删除，窗口内的行原样保留。

覆盖两类表，故窗口分档：
- **明细事件表**（每条操作/登录/抓取各一行，增速快）——窗口较短：
  `fetch_runs`（抓取运行历史）180 天、`login_events`（登录事件）365 天、
  `admin_audit_logs`（管理操作审计）365 天。
- **按天聚合表**（一天一用户一维度才一行，增速慢）——窗口更长：
  `ai_usage`（AI 用量）、`reader_reads`（阅读计量）各 730 天。

时间字段各表不一（见 `_TABLES`）：明细表用 ISO 时间串（`started_at`/`at`），
聚合表用 `YYYY-MM-DD` 的 `day`。二者都是字典序 == 时间序，故统一用一个
**date-only 截止串**做 `< cutoff` 比较即可正确裁剪（datetime 串带 date 前缀，
比较到截止日当天仍保留，边界取「保留截止日及其后」）。

`run_retention_cleanup(engine)` 逐表删除并返回 `{表名: 删除行数}` 汇总，写 info
日志；由 app.py 注册的每日一次定时任务（04:30）驱动。清理只删这些派生/埋点
明细：**归档正文（articles）、账户、订阅等业务实体不在其列**。

注意：`collection_job_runs.child_run_ids_json` 引用 `fetch_runs.id`，删除老
`fetch_runs` 后老的采集运行详情页会读到更少的 child 行——读取路径
（`GET /api/collection-job-runs/{id}` 用 `id.in_(...)`）对缺失 id 天然容错，
只是明细变少，不会报错（可接受的历史损耗）。
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlmodel import Session

from models.db import (
    AdminAuditRecord,
    AiUsageRecord,
    FetchRunRecord,
    LoginEventRecord,
    ReaderReadRecord,
)

logger = logging.getLogger("dorami.retention")


@dataclass(frozen=True)
class _TableRetention:
    model: Any          # ORM 表模型
    time_field: Any     # 用于窗口判定的时间列（ISO 串或 YYYY-MM-DD）
    retention_days: int  # 保留窗口天数
    label: str          # 日志/汇总用表名


# 逐表保留策略。窗口按「明细表短、聚合表长」分档（见模块 docstring）。
_TABLES: List[_TableRetention] = [
    _TableRetention(FetchRunRecord, FetchRunRecord.started_at, 180, "fetch_runs"),
    _TableRetention(LoginEventRecord, LoginEventRecord.at, 365, "login_events"),
    _TableRetention(AdminAuditRecord, AdminAuditRecord.at, 365, "admin_audit_logs"),
    _TableRetention(AiUsageRecord, AiUsageRecord.day, 730, "ai_usage"),
    _TableRetention(ReaderReadRecord, ReaderReadRecord.day, 730, "reader_reads"),
]


def _cutoff(retention_days: int, *, today: datetime.date) -> str:
    """保留窗口起点的 date-only 截止串：早于它（`< cutoff`）的行删除。

    保留最近 `retention_days` 天（含今天）：cutoff = today - (days - 1)。
    """
    days = max(1, int(retention_days))
    return (today - datetime.timedelta(days=days - 1)).isoformat()


def run_retention_cleanup(engine: Engine) -> Dict[str, int]:
    """对所有登记表做滚动窗清理，返回 `{表名: 删除行数}` 汇总。

    单表删除失败不阻断其余表（吞异常记 error，该表计 -1 便于日志排查）。
    """
    today = datetime.date.today()
    deleted: Dict[str, int] = {}
    with Session(engine) as session:
        for spec in _TABLES:
            cutoff = _cutoff(spec.retention_days, today=today)
            try:
                result = session.execute(
                    delete(spec.model).where(spec.time_field < cutoff)
                )
                session.commit()
                deleted[spec.label] = int(result.rowcount or 0)
            except Exception as exc:  # noqa: BLE001 单表失败不影响其余表
                session.rollback()
                deleted[spec.label] = -1
                logger.error("明细表清理失败 %s: %s", spec.label, exc)
    total = sum(v for v in deleted.values() if v > 0)
    logger.info("明细表滚动窗清理完成：共删除 %s 行，逐表明细=%s", total, deleted)
    return deleted
