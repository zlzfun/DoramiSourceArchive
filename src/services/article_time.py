"""Canonical timestamp handling for persisted article dates.

Older collectors wrote a mixture of UTC ``Z`` values, explicit offsets and
naive Asia/Shanghai timestamps. String comparison is therefore not a safe
time-window predicate. Keep parsing in one place and compare aware instants.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_article_time(value: str | None) -> dt.datetime | None:
    """Return a UTC instant; legacy naive values are Asia/Shanghai local time."""

    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(dt.timezone.utc)


def in_time_window(
    value: str | None,
    *,
    start: dt.datetime,
    end: dt.datetime,
) -> bool:
    """Whether a mixed-format persisted timestamp is inside ``[start, end]``."""

    parsed = parse_article_time(value)
    if parsed is None:
        return False
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("time-window bounds must be timezone-aware")
    return start.astimezone(dt.timezone.utc) <= parsed <= end.astimezone(dt.timezone.utc)


__all__ = ["SHANGHAI", "in_time_window", "parse_article_time"]
