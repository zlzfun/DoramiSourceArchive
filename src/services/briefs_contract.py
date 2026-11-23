"""Contract v1.0 wire projection and response assembler for M2M personal briefs."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


VERSION = "1.0"
PATHS = frozenset({
    "/api/admin/reader/briefs/today",
    "/api/admin/reader/briefs/today/ensure",
})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


class BriefsError(Exception):
    """Typed application error mapped to the frozen v1 ErrorEnvelope."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def employee_number(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not (1 <= len(value) <= 128)
        or any(ch.isspace() for ch in value)
        or re.search(r"\s", value)
    ):
        raise BriefsError(
            422,
            "INVALID_ARGUMENT",
            "employee_number must be 1-128 characters without whitespace",
        )
    return value


def timestamp(value: Any, *, date_allowed: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if date_allowed and _DATE_RE.fullmatch(value):
            dt.date.fromisoformat(value)
            return value
        if not _DATETIME_RE.fullmatch(value):
            return None
        normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        parsed = dt.datetime.fromisoformat(normalized.replace("t", "T"))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return value


def tags(values: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    for entry in values:
        candidate = entry
        if isinstance(entry, Mapping):
            candidate = (
                text(entry.get("name_zh"))
                or text(entry.get("name"))
                or text(entry.get("name_en"))
                or text(entry.get("code"))
            )
        label = text(candidate)
        if label and label not in result:
            result.append(label)
    return result


def adapt_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    raw_snapshot = item.get("snapshot")
    if not isinstance(raw_snapshot, Mapping):
        return None
    snapshot = dict(raw_snapshot)
    title = text(snapshot.get("title_zh")) or text(snapshot.get("title"))
    source_name = text(snapshot.get("source_name"))
    source_url = url(snapshot.get("source_url"))
    if not (title and source_name and source_url):
        return None

    lane = item.get("selection_lane")
    # Reader value sections ("深度阅读" / "播客精选") project onto quality in v1.
    if lane in {"read", "listen"}:
        lane = "quality"
    if lane not in {"interest", "quality", "breaking"}:
        return None

    related: list[dict[str, Any]] = []
    seen_urls = {source_url}
    raw_related = snapshot.get("related_sources")
    if isinstance(raw_related, list):
        for link in raw_related:
            if not isinstance(link, Mapping):
                continue
            link_url = url(link.get("source_url") or link.get("url"))
            link_title = text(link.get("title_zh")) or text(link.get("title"))
            link_source = text(link.get("source_name"))
            if not (link_url and link_title and link_source) or link_url in seen_urls:
                continue
            seen_urls.add(link_url)
            related.append({
                "title": link_title,
                "source_name": link_source,
                "source_url": link_url,
                "publish_date": timestamp(
                    link.get("publish_date") or link.get("published_at"),
                    date_allowed=True,
                ),
            })

    raw_event_id = snapshot.get("event_id")
    if type(raw_event_id) is int:
        event_id: str | None = str(raw_event_id)
    else:
        event_id = text(raw_event_id)

    summary = text(snapshot.get("summary")) or text(snapshot.get("one_sentence_summary"))
    snapshot.update(
        title=title,
        source_name=source_name,
        source_url=source_url,
        publish_date=timestamp(snapshot.get("publish_date"), date_allowed=True),
        summary=summary,
        score_reason=text(snapshot.get("score_reason")),
        tags=tags(snapshot.get("tags")),
        display_tags=tags(snapshot.get("display_tags")),
        event_id=event_id,
        related_sources=related,
    )

    score = item.get("quality_score")
    if type(score) not in {int, float} or not math.isfinite(score):
        score = None

    return {
        **dict(item),
        "section": text(item.get("section")) or "精选",
        "selection_lane": lane,
        "quality_score": score,
        "selection_reason": text(item.get("selection_reason")),
        "snapshot": snapshot,
    }


def adapt_edition(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("edition payload must be a mapping")
    day = payload.get("report_date")
    revision = payload.get("revision")
    if not isinstance(day, str) or not _DATE_RE.fullmatch(day):
        raise ValueError("invalid edition report_date")
    dt.date.fromisoformat(day)
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid edition revision")

    status_map = {
        "pending": "pending",
        "generating": "processing",
        "processing": "processing",
        "ready": "ready",
        "degraded": "ready",
        "failed": "failed",
        # Internal reader lifecycle retirement maps to terminal failed on the wire.
        "superseded": "failed",
    }
    raw_status = payload.get("status")
    wire_status = status_map.get(raw_status) if isinstance(raw_status, str) else None
    if wire_status is None:
        raise ValueError(f"unsupported edition status: {raw_status!r}")

    raw_stats = payload.get("selection_stats")
    selection_stats = dict(raw_stats) if isinstance(raw_stats, Mapping) else {}
    degraded_reason = text(payload.get("degraded_reason"))

    result: dict[str, Any] = {
        "report_date": day,
        "revision": revision,
        "status": wire_status,
        "degraded_reason": degraded_reason,
        "selection_stats": selection_stats,
        "generated_at": None,
        "items": [],
    }
    if wire_status != "ready":
        return result

    generated_at = timestamp(payload.get("generated_at"), date_allowed=False)
    if generated_at is None:
        raise ValueError("ready edition has no valid generated_at")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("invalid edition items")

    valid_items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        adapted = adapt_item(raw_item)
        if adapted is not None:
            adapted["position"] = len(valid_items) + 1
            valid_items.append(adapted)

    reasons: list[str] = [degraded_reason] if degraded_reason else []
    if len(valid_items) != len(raw_items) and "invalid_items_filtered" not in reasons:
        reasons.append("invalid_items_filtered")
    if raw_status == "degraded" and not reasons:
        if payload.get("sync_stale"):
            reasons.append("sync_stale")
        if payload.get("analysis_incomplete"):
            reasons.append("analysis_incomplete")
        if not reasons:
            reasons.append("incomplete_generation")

    result["generated_at"] = generated_at
    result["items"] = valid_items
    result["degraded_reason"] = ";".join(reasons) or None
    return result


def envelope(
    employee: str,
    username: str,
    edition: dict[str, Any] | None,
) -> dict[str, Any]:
    validated_employee = employee_number(employee)
    owner = text(username)
    if not owner or owner != username:
        raise ValueError("invalid resolved owner username")

    if edition is None:
        status = "missing"
        pushable = False
        hint: str | None = "retry"
    else:
        wire_state = edition["status"]
        if wire_state in {"pending", "processing"}:
            status = wire_state
            pushable = False
            hint = "retry"
        elif wire_state == "failed":
            status = "failed"
            pushable = False
            hint = "public"
        elif wire_state == "ready":
            if not edition["items"]:
                status = "empty"
                pushable = False
                hint = "public"
            elif edition["degraded_reason"]:
                status = "degraded"
                pushable = True
                hint = None
            else:
                status = "ready"
                pushable = True
                hint = None
        else:
            raise ValueError(f"unexpected wire edition status: {wire_state!r}")

    return {
        "employee_number": validated_employee,
        "username": owner,
        "status": status,
        "pushable": pushable,
        "fallback_hint": hint,
        "edition": edition,
    }
