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
    """窗口内（近 days 天）用量聚合：totals + by_purpose + by_user + by_day。"""
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows: List[AiUsageRecord] = list(
        session.exec(select(AiUsageRecord).where(AiUsageRecord.day >= since)).all()
    )

    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    by_purpose: Dict[str, Dict[str, int]] = {}
    by_user: Dict[str, Dict[str, int]] = {}
    by_day: Dict[str, Dict[str, int]] = {}

    def _bump(bucket: Dict[str, Dict[str, int]], key: str, row: AiUsageRecord) -> None:
        agg = bucket.setdefault(key, {"calls": 0, "total_tokens": 0})
        agg["calls"] += row.calls
        agg["total_tokens"] += row.total_tokens

    for row in rows:
        totals["calls"] += row.calls
        totals["prompt_tokens"] += row.prompt_tokens
        totals["completion_tokens"] += row.completion_tokens
        totals["total_tokens"] += row.total_tokens
        _bump(by_purpose, row.purpose, row)
        _bump(by_user, row.username, row)
        _bump(by_day, row.day, row)

    def _sorted(bucket: Dict[str, Dict[str, int]], key_name: str, *, by_key: bool = False):
        items = [{key_name: k, **v} for k, v in bucket.items()]
        if by_key:
            return sorted(items, key=lambda x: x[key_name])
        return sorted(items, key=lambda x: x["total_tokens"], reverse=True)

    return {
        "window_days": days,
        "totals": totals,
        "by_purpose": _sorted(by_purpose, "purpose"),
        "by_user": _sorted(by_user, "username"),
        "by_day": _sorted(by_day, "day", by_key=True),
    }
