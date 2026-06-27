"""AI 用量计量服务 (src/services/ai_usage.py)

把每次 LLM 调用的 token usage 按「日期 × 用户 × 用途 × 模型」聚合落库
（`AiUsageRecord`），并为运维看板提供窗口聚合读取。

写侧 `record_usage` 由 `llm.client` 的 recorder 回调驱动；读侧 `summarize`
供 `GET /api/admin/ai-usage`。计量绝不阻断主流程：写入异常由调用方吞掉。
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from models.db import AiUsageRecord

# 计入看板的用途标签（白名单，避免脏数据）。
VALID_PURPOSES = (
    "translate",
    "ask",
    "daily_brief_map",
    "daily_brief_dedup",
    "daily_brief_reduce",
    "source_config",
    "detail_profile",
)

SYSTEM_USERNAME = "system"


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def record_usage(
    session: Session,
    *,
    username: Optional[str],
    purpose: str,
    model: str,
    usage: Dict[str, Any],
    day: Optional[str] = None,
) -> None:
    """把一次调用的 token 用量累加进当天聚合行（不存在则建）。

    usage 取 OpenAI 兼容响应的 usage 段（prompt_tokens/completion_tokens/total_tokens）；
    缺失时按 0 计，仍累加 calls。
    """
    if purpose not in VALID_PURPOSES:
        return
    day = day or _today()
    owner = (username or "").strip() or SYSTEM_USERNAME
    model = (model or "").strip()

    prompt = _coerce_int(usage.get("prompt_tokens"))
    completion = _coerce_int(usage.get("completion_tokens"))
    total = _coerce_int(usage.get("total_tokens")) or (prompt + completion)

    record = session.exec(
        select(AiUsageRecord).where(
            AiUsageRecord.day == day,
            AiUsageRecord.username == owner,
            AiUsageRecord.purpose == purpose,
            AiUsageRecord.model == model,
        )
    ).first()
    if record is None:
        record = AiUsageRecord(
            day=day,
            username=owner,
            purpose=purpose,
            model=model,
            calls=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            updated_at=_now_iso(),
        )
    record.calls += 1
    record.prompt_tokens += prompt
    record.completion_tokens += completion
    record.total_tokens += total
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()


def summarize(session: Session, *, days: int = 30) -> Dict[str, Any]:
    """窗口内（近 days 天）用量聚合：totals + by_purpose + by_user + by_day +
    by_day_purpose / by_day_user（日×维度明细，供前端多系列时间序列图）。"""
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows: List[AiUsageRecord] = list(
        session.exec(select(AiUsageRecord).where(AiUsageRecord.day >= since)).all()
    )

    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    by_purpose: Dict[str, Dict[str, int]] = {}
    by_user: Dict[str, Dict[str, int]] = {}
    by_day: Dict[str, Dict[str, int]] = {}
    # 「日 × 维度」明细：供前端把每日图按用途/用户拆成多系列（不同颜色）。
    by_day_purpose: Dict[tuple, Dict[str, int]] = {}
    by_day_user: Dict[tuple, Dict[str, int]] = {}

    def _bump(bucket: Dict, key, row: AiUsageRecord) -> None:
        agg = bucket.setdefault(
            key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        agg["calls"] += row.calls
        agg["prompt_tokens"] += row.prompt_tokens
        agg["completion_tokens"] += row.completion_tokens
        agg["total_tokens"] += row.total_tokens

    for row in rows:
        totals["calls"] += row.calls
        totals["prompt_tokens"] += row.prompt_tokens
        totals["completion_tokens"] += row.completion_tokens
        totals["total_tokens"] += row.total_tokens
        _bump(by_purpose, row.purpose, row)
        _bump(by_user, row.username, row)
        _bump(by_day, row.day, row)
        _bump(by_day_purpose, (row.day, row.purpose), row)
        _bump(by_day_user, (row.day, row.username), row)

    def _sorted(bucket: Dict[str, Dict[str, int]], key_name: str, *, by_key: bool = False):
        items = [{key_name: k, **v} for k, v in bucket.items()]
        if by_key:
            return sorted(items, key=lambda x: x[key_name])
        return sorted(items, key=lambda x: x["total_tokens"], reverse=True)

    def _sorted_pair(bucket: Dict[tuple, Dict[str, int]], dim_name: str):
        # 展平为 [{day, <dim_name>, calls, total_tokens}]，按日升序便于绘图。
        items = [
            {"day": k[0], dim_name: k[1], "calls": v["calls"], "total_tokens": v["total_tokens"]}
            for k, v in bucket.items()
        ]
        return sorted(items, key=lambda x: x["day"])

    return {
        "window_days": days,
        "totals": totals,
        "by_purpose": _sorted(by_purpose, "purpose"),
        "by_user": _sorted(by_user, "username"),
        "by_day": _sorted(by_day, "day", by_key=True),
        "by_day_purpose": _sorted_pair(by_day_purpose, "purpose"),
        "by_day_user": _sorted_pair(by_day_user, "username"),
    }


def usage_by_user(session: Session, *, days: int = 30) -> Dict[str, Dict[str, int]]:
    """窗口内按用户聚合 `{username: {calls, total_tokens}}`，排除系统任务（system）。

    供运维账户列表批量富化每账户的近 N 天 AI 活跃度（一次查询、内存聚合）。
    """
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows: List[AiUsageRecord] = list(
        session.exec(
            select(AiUsageRecord).where(
                AiUsageRecord.day >= since,
                AiUsageRecord.username != SYSTEM_USERNAME,
            )
        ).all()
    )
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        agg = out.setdefault(row.username, {"calls": 0, "total_tokens": 0})
        agg["calls"] += row.calls
        agg["total_tokens"] += row.total_tokens
    return out


def summarize_user(session: Session, username: str, *, days: int = 30) -> Dict[str, Any]:
    """单用户窗口聚合：totals + by_purpose（用途排行）+ by_day（日趋势，
    每行含 calls/total_tokens）+ by_day_purpose（日×用途明细，供前端堆叠）。"""
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows: List[AiUsageRecord] = list(
        session.exec(
            select(AiUsageRecord).where(
                AiUsageRecord.day >= since,
                AiUsageRecord.username == username,
            )
        ).all()
    )

    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    by_purpose: Dict[str, Dict[str, int]] = {}
    by_day: Dict[str, Dict[str, int]] = {}
    by_day_purpose: Dict[tuple, Dict[str, int]] = {}

    def _bump(bucket: Dict, key, row: AiUsageRecord) -> None:
        agg = bucket.setdefault(
            key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        agg["calls"] += row.calls
        agg["prompt_tokens"] += row.prompt_tokens
        agg["completion_tokens"] += row.completion_tokens
        agg["total_tokens"] += row.total_tokens

    for row in rows:
        totals["calls"] += row.calls
        totals["prompt_tokens"] += row.prompt_tokens
        totals["completion_tokens"] += row.completion_tokens
        totals["total_tokens"] += row.total_tokens
        _bump(by_purpose, row.purpose, row)
        _bump(by_day, row.day, row)
        _bump(by_day_purpose, (row.day, row.purpose), row)

    by_purpose_list = sorted(
        [{"purpose": k, **v} for k, v in by_purpose.items()],
        key=lambda x: x["calls"],
        reverse=True,
    )
    by_day_list = sorted(
        [{"day": k, **v} for k, v in by_day.items()], key=lambda x: x["day"]
    )
    by_day_purpose_list = sorted(
        [
            {"day": k[0], "purpose": k[1], "calls": v["calls"], "total_tokens": v["total_tokens"]}
            for k, v in by_day_purpose.items()
        ],
        key=lambda x: x["day"],
    )

    return {
        "window_days": days,
        "username": username,
        "totals": totals,
        "by_purpose": by_purpose_list,
        "by_day": by_day_list,
        "by_day_purpose": by_day_purpose_list,
    }
