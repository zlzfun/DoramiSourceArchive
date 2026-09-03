"""AI 用量计量服务 (src/services/ai_usage.py)

把每次 LLM 调用的 token usage 按「日期 × 用户 × 用途 × 模型」聚合落库
（`AiUsageRecord`），并为运维看板提供窗口聚合读取。

写侧 `record_usage` 由 `llm.client` 的 recorder 回调驱动；读侧 `summarize`
供 `GET /api/admin/ai-usage`。计量绝不阻断主流程：写入异常由调用方吞掉。
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, func, select

from models.db import AiUsageRecord

# 计入看板的用途标签（白名单，避免脏数据）。
# 注意：读者面新增 AI 用途必须同步登记此处 + reader.py _AI_DAILY_CALL_LIMITS +
# accounts.READER_AI_BUDGET_PURPOSES 三处——v3.40.4 前 summarize 漏登记本表，
# record_usage 静默丢行导致逐用户限额/全站日预算/用量看板三层护栏全部失效。
VALID_PURPOSES = (
    "translate",
    "ask",
    "summarize",
    "daily_brief_map",
    "daily_brief_dedup",
    "daily_brief_reduce",
    "article_analysis",
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

    v3.43（审计 M21）：SQLite `INSERT … ON CONFLICT DO UPDATE` 原子累加——旧的
    「先查→无则插/有则递增」两步在并发下会双插重复行或互踩旧值丢增量；聚合键
    唯一索引 `uq_ai_usage_day_user_purpose_model` 由迁移 a7e2f95c1d40 保证。
    """
    if purpose not in VALID_PURPOSES:
        return
    day = day or _today()
    owner = (username or "").strip() or SYSTEM_USERNAME
    model = (model or "").strip()

    prompt = _coerce_int(usage.get("prompt_tokens"))
    completion = _coerce_int(usage.get("completion_tokens"))
    total = _coerce_int(usage.get("total_tokens")) or (prompt + completion)

    table = AiUsageRecord.__table__
    stmt = sqlite_insert(table).values(
        day=day,
        username=owner,
        purpose=purpose,
        model=model,
        calls=1,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        updated_at=_now_iso(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["day", "username", "purpose", "model"],
        set_={
            "calls": table.c.calls + 1,
            "prompt_tokens": table.c.prompt_tokens + prompt,
            "completion_tokens": table.c.completion_tokens + completion,
            "total_tokens": table.c.total_tokens + total,
            "updated_at": _now_iso(),
        },
    )
    session.execute(stmt)
    session.commit()


# by_day_user 的服务端系列上限（v3.43 审计 M16）：与前端图表调色板 6 彩色槽 +
# 中性「其它」槽对齐——窗口内用户数随规模增长时，日×用户明细载荷曾随之无界膨胀
# 而前端只画得下 6 系；现由后端选 Top N（按窗口 total_tokens）并把其余按日聚成
# 「其它」行，载荷有界且前端 pivotDaily 的「其它」中性槽语义无缝承接。
BY_DAY_USER_TOP_N = 6
# 聚合桶的系列键带冒号(用户名禁冒号,见 accounts.create_user)——不能用展示文案
# 「其它」当聚合身份:它是合法用户名,真有此名用户进 Top 时尾部用户会被并进同名
# 桶污染其系列(codex 交叉检视实证)。前端在渲染层把 sentinel 映射回「其它」。
OTHER_SERIES_LABEL = "other:"


def summarize(session: Session, *, days: int = 30) -> Dict[str, Any]:
    """窗口内（近 days 天）用量聚合：totals + by_purpose + by_user + by_day +
    by_day_purpose / by_day_user（日×维度明细，供前端多系列时间序列图；
    by_day_user 仅含 Top N 用户 + 按日聚合的「其它」行，见 BY_DAY_USER_TOP_N）。"""
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

    # by_day_user 服务端裁剪（M16）：Top N 用户按窗口 total_tokens 选定，
    # 其余用户的量按日合并进「其它」行——总量守恒，只是系列有界。
    top_users = {
        name for name, _ in sorted(
            by_user.items(), key=lambda kv: (-kv[1]["total_tokens"], kv[0])
        )[:BY_DAY_USER_TOP_N]
    }
    if len(by_user) > len(top_users):
        trimmed: Dict[tuple, Dict[str, int]] = {}
        for (day_key, name), agg in by_day_user.items():
            key = (day_key, name if name in top_users else OTHER_SERIES_LABEL)
            slot = trimmed.setdefault(
                key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            )
            for field in slot:
                slot[field] += agg[field]
        by_day_user = trimmed

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
    """窗口内按用户聚合 `{username: {calls, total_tokens}}`，排除系统任务（system）
    与删号墓碑（`deleted:*`，见 accounts.DELETED_USER_PREFIX——账户列表/活跃榜只看
    现存账户；墓碑消耗仍进 `summarize` 的成本看板口径）。

    v3.41 账户管理 V2（审计 M07）：SQL 端 GROUP BY 聚合，不再把窗口明细行整批载入
    内存 Python 累加。
    """
    days = max(1, min(int(days or 30), 365))
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    rows = session.exec(
        select(
            AiUsageRecord.username,
            func.coalesce(func.sum(AiUsageRecord.calls), 0),
            func.coalesce(func.sum(AiUsageRecord.total_tokens), 0),
        )
        .where(
            AiUsageRecord.day >= since,
            AiUsageRecord.username != SYSTEM_USERNAME,
            AiUsageRecord.username.not_like("deleted:%"),
        )
        .group_by(AiUsageRecord.username)
    ).all()
    return {
        username: {"calls": int(calls or 0), "total_tokens": int(tokens or 0)}
        for username, calls, tokens in rows
    }


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
