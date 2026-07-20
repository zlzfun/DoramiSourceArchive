"""X API 按量费用的本地守卫。

X 按返回资源计费，并对同一 UTC 日内重复返回的同一资源做去重。这里把本月累计量
写入 ``AppSettingRecord``，只保留当天的资源 ID 集合用于去重；跨日时清空集合但
保留月累计。守卫是应用侧保险，Developer Console 的 spending limit 仍应保留。
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Literal

from sqlmodel import Session

from models.db import AppSettingRecord


POST_READ_MICROS = 5_000
MEDIA_READ_MICROS = 5_000
NOTE_READ_MICROS = 5_000
USER_READ_MICROS = 10_000
X_USAGE_KEY_PREFIX = "x_api_usage:"
_USAGE_LOCK = threading.Lock()


class XApiQuotaExceeded(RuntimeError):
    """本地月度预算不足以继续发起 X API 请求。"""


@dataclass(frozen=True)
class XApiUsageSnapshot:
    month: str
    key: str
    monthly_budget_usd: float
    post_reads: int = 0
    media_reads: int = 0
    note_reads: int = 0
    user_reads: int = 0
    estimated_cost_micros: int = 0
    updated_at: str = ""
    by_source: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def estimated_cost_usd(self) -> float:
        return self.estimated_cost_micros / 1_000_000

    @property
    def remaining_usd(self) -> float:
        return max(self.monthly_budget_usd - self.estimated_cost_usd, 0.0)

    @property
    def blocked(self) -> bool:
        return self.estimated_cost_usd >= self.monthly_budget_usd

    def as_dict(self) -> Dict[str, Any]:
        return {
            "month": self.month,
            "setting_key": self.key,
            "monthly_budget_usd": self.monthly_budget_usd,
            "post_reads": self.post_reads,
            "media_reads": self.media_reads,
            "note_reads": self.note_reads,
            "user_reads": self.user_reads,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "remaining_usd": round(self.remaining_usd, 6),
            "blocked": self.blocked,
            "updated_at": self.updated_at or None,
            "by_source": self.by_source,
        }


def _utc_now(now: dt.datetime | None = None) -> dt.datetime:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _month_and_day(now: dt.datetime | None = None) -> tuple[str, str, str]:
    value = _utc_now(now)
    return value.strftime("%Y-%m"), value.strftime("%Y-%m-%d"), value.isoformat()


def _empty_state(month: str, day: str) -> Dict[str, Any]:
    return {
        "month": month,
        "counts": {"posts": 0, "media": 0, "notes": 0, "users": 0},
        "daily_seen": {"date": day, "posts": [], "media": [], "notes": [], "users": []},
        "by_source": {},
        "estimated_cost_micros": 0,
        "updated_at": "",
    }


def _load_state(record: AppSettingRecord | None, month: str, day: str) -> Dict[str, Any]:
    state = _empty_state(month, day)
    if record and record.value:
        try:
            loaded = json.loads(record.value)
            if isinstance(loaded, dict) and loaded.get("month") == month:
                state.update(loaded)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
    state["counts"] = {
        "posts": max(int(counts.get("posts", 0) or 0), 0),
        "media": max(int(counts.get("media", 0) or 0), 0),
        "notes": max(int(counts.get("notes", 0) or 0), 0),
        "users": max(int(counts.get("users", 0) or 0), 0),
    }
    seen = state.get("daily_seen") if isinstance(state.get("daily_seen"), dict) else {}
    if seen.get("date") != day:
        state["daily_seen"] = _empty_state(month, day)["daily_seen"]
    else:
        state["daily_seen"] = {
            "date": day,
            **{
                name: list(dict.fromkeys(str(item) for item in (seen.get(name) or []) if item))
                for name in ("posts", "media", "notes", "users")
            },
        }
    by_source = state.get("by_source") if isinstance(state.get("by_source"), dict) else {}
    normalized_by_source: Dict[str, Dict[str, int]] = {}
    for source_id, raw_counts in by_source.items():
        if not source_id or not isinstance(raw_counts, dict):
            continue
        counts_for_source = {
            name: max(int(raw_counts.get(name, 0) or 0), 0)
            for name in ("posts", "media", "notes", "users")
        }
        normalized_by_source[str(source_id)] = counts_for_source
    state["by_source"] = normalized_by_source
    return state


def _cost_micros(counts: Dict[str, int]) -> int:
    return (
        counts["posts"] * POST_READ_MICROS
        + counts["media"] * MEDIA_READ_MICROS
        + counts["notes"] * NOTE_READ_MICROS
        + counts["users"] * USER_READ_MICROS
    )


def _snapshot(state: Dict[str, Any], budget_usd: float, key: str) -> XApiUsageSnapshot:
    counts = state["counts"]
    by_source = {
        source_id: {
            **source_counts,
            "estimated_cost_usd": round(_cost_micros(source_counts) / 1_000_000, 6),
        }
        for source_id, source_counts in sorted(state.get("by_source", {}).items())
    }
    return XApiUsageSnapshot(
        month=state["month"],
        key=key,
        monthly_budget_usd=max(float(budget_usd), 0.0),
        post_reads=counts["posts"],
        media_reads=counts["media"],
        note_reads=counts["notes"],
        user_reads=counts["users"],
        estimated_cost_micros=_cost_micros(counts),
        updated_at=str(state.get("updated_at") or ""),
        by_source=by_source,
    )


def read_x_api_usage(
    session: Session,
    *,
    monthly_budget_usd: float,
    now: dt.datetime | None = None,
) -> XApiUsageSnapshot:
    month, day, _ = _month_and_day(now)
    key = f"{X_USAGE_KEY_PREFIX}{month}"
    state = _load_state(session.get(AppSettingRecord, key), month, day)
    return _snapshot(state, monthly_budget_usd, key)


def resource_seen_today(
    session: Session,
    resource: Literal["posts", "media", "notes", "users"],
    resource_id: str,
    *,
    now: dt.datetime | None = None,
) -> bool:
    """本地账本中某资源是否已在当前 UTC 日返回（连通性探针估费用）。"""
    month, day, _ = _month_and_day(now)
    state = _load_state(
        session.get(AppSettingRecord, f"{X_USAGE_KEY_PREFIX}{month}"), month, day
    )
    return str(resource_id) in set(state["daily_seen"][resource])


def _items(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _ids(items: Iterable[Dict[str, Any]], *fields: str) -> set[str]:
    values: set[str] = set()
    for item in items:
        for field in fields:
            value = item.get(field)
            if value not in (None, ""):
                values.add(str(value))
                break
    return values


class XApiQuotaGuard:
    """基于一个 SQLModel engine 的 X API 月度费用守卫。"""

    def __init__(self, engine, *, monthly_budget_usd: float = 5.0):
        self.engine = engine
        self.monthly_budget_usd = max(float(monthly_budget_usd), 0.0)

    def snapshot(self, now: dt.datetime | None = None) -> XApiUsageSnapshot:
        with Session(self.engine) as session:
            return read_x_api_usage(
                session, monthly_budget_usd=self.monthly_budget_usd, now=now
            )

    def ensure_available(
        self,
        *,
        minimum_cost_micros: int = POST_READ_MICROS,
        now: dt.datetime | None = None,
    ) -> XApiUsageSnapshot:
        snapshot = self.snapshot(now)
        budget_micros = round(self.monthly_budget_usd * 1_000_000)
        if snapshot.estimated_cost_micros + max(minimum_cost_micros, 0) > budget_micros:
            raise XApiQuotaExceeded(
                "X API 月度配额已用尽或不足以完成下一次最小请求："
                f"{snapshot.estimated_cost_usd:.3f}/{self.monthly_budget_usd:.2f} USD"
            )
        return snapshot

    def cap_post_results(self, requested: int, now: dt.datetime | None = None) -> int:
        """按剩余预算约束主 Post 数量；X user timeline 的 API 下限为 5。"""
        snapshot = self.ensure_available(minimum_cost_micros=5 * POST_READ_MICROS, now=now)
        budget_micros = round(self.monthly_budget_usd * 1_000_000)
        remaining = max(budget_micros - snapshot.estimated_cost_micros, 0)
        allowed = min(max(int(requested), 0), remaining // POST_READ_MICROS)
        if allowed < 5:
            raise XApiQuotaExceeded("X API 月度剩余配额不足 5 个 Post 的最小时间线请求")
        return allowed

    def record_response(
        self,
        payload: Dict[str, Any],
        *,
        primary_resource: Literal["post", "user"],
        source_id: str = "",
        now: dt.datetime | None = None,
    ) -> XApiUsageSnapshot:
        """按 X 返回资源记账；同一 UTC 日内相同 ID 只累计一次。"""
        month, day, updated_at = _month_and_day(now)
        key = f"{X_USAGE_KEY_PREFIX}{month}"
        primary = _items(payload.get("data"))
        includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
        included_posts = _items(includes.get("tweets"))
        included_users = _items(includes.get("users"))
        included_media = _items(includes.get("media"))

        posts = included_posts
        users = included_users
        if primary_resource == "post":
            posts = primary + posts
        else:
            users = primary + users

        resource_ids = {
            "posts": _ids(posts, "id"),
            "media": _ids(included_media, "media_key", "id"),
            "users": _ids(users, "id"),
            "notes": {
                str(item["id"])
                for item in posts
                if item.get("id") and isinstance(item.get("note_tweet"), dict)
            },
        }

        with _USAGE_LOCK:
            with Session(self.engine) as session:
                record = session.get(AppSettingRecord, key)
                state = _load_state(record, month, day)
                seen = state["daily_seen"]
                counts = state["counts"]
                source_counts = None
                if source_id:
                    source_counts = state["by_source"].setdefault(
                        source_id,
                        {"posts": 0, "media": 0, "notes": 0, "users": 0},
                    )
                for name, ids in resource_ids.items():
                    already_seen = set(seen[name])
                    fresh = ids - already_seen
                    counts[name] += len(fresh)
                    if source_counts is not None:
                        source_counts[name] += len(fresh)
                    seen[name] = sorted(already_seen | ids)

                state["estimated_cost_micros"] = _cost_micros(counts)
                state["updated_at"] = updated_at
                value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                if record is None:
                    record = AppSettingRecord(key=key, value=value)
                else:
                    record.value = value
                session.add(record)
                session.commit()
                return _snapshot(state, self.monthly_budget_usd, key)
