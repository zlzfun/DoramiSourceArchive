"""Opt-in bounded publication discovery for three chronological presets.

Source dates retain their precision. This is a collection bound, never a clock
replacement or permission to rewrite existing archive rows.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json

UTC = timezone.utc


def parameter_schema():
    return [
        {"field": "publish_after", "label": "发布时间起点 (UTC，可选)", "type": "text", "default": ""},
        {"field": "publish_before", "label": "发布时间终点 (UTC，可选)", "type": "text", "default": ""},
        {"field": "window_max_pages", "label": "时间窗口分页上限", "type": "number", "default": 20},
    ]


def _stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("publication_window: invalid UTC timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("publication_window: aware UTC timestamps required")
    return result.astimezone(UTC)


def _integer(value, name, maximum):
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= maximum:
        raise ValueError(f"publication_window: {name} must be 1..{maximum}")
    return int(value)


@dataclass(frozen=True)
class PublicationWindow:
    after: datetime
    before: datetime
    max_pages: int
    limit: int

    @classmethod
    def from_params(cls, params, default_limit):
        after, before = params.get("publish_after"), params.get("publish_before")
        if after in (None, "") and before in (None, ""):
            return None
        if after in (None, "") or before in (None, ""):
            raise ValueError("publication_window: both publication bounds required")
        after, before = _stamp(after), _stamp(before)
        if not after < before or before - after > timedelta(days=7):
            raise ValueError("publication_window: range must be positive and at most seven days")
        if before > datetime.now(UTC):
            raise ValueError("publication_window: upper bound is in the future")
        pages = params.get("window_max_pages")
        limit = params.get("limit")
        return cls(after, before, _integer(20 if pages in (None, "") else pages, "window_max_pages", 20),
                   _integer(default_limit if limit in (None, "") else limit, "limit", 10000))

    def contains(self, stamp):
        return self.after <= stamp <= self.before

    def contains_day(self, start, end):
        # The upper day endpoint is exclusive; <= is conservative when it equals
        # the inclusive window endpoint and never widens the window.
        return self.after <= start and end <= self.before

    def bounds(self):
        return {"publish_after": self.after.isoformat(), "publish_before": self.before.isoformat()}


def point_proof(raw, stamp, precision, zone="UTC"):
    return {"raw_date": raw, "date_precision": precision, "date_timezone": zone,
            "published_at": stamp.astimezone(UTC).isoformat()}


class CoverageReport:
    def __init__(self, source_id, window):
        self.source_id, self.window = source_id, window
        self.pages = 0
        self.discovery_complete = False
        self.reasons = []
        self.items = []
        self.counters = {}

    def partial(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)

    def observe(self, article_id, url, proof, status, *, page, ordinal):
        self.items.append({"article_id": article_id, "url": url, "date_proof": proof,
                           "status": status, "page": page, "ordinal": ordinal})

    def mark_archive(self, existing):
        for row in self.items:
            aid = row["article_id"]
            row["archive_status"] = "reused_archive" if aid in existing else "new_candidate"
            row["body_status"] = ("available_unreviewed" if existing[aid] else "missing") if aid in existing else "unverified"

    def summary(self):
        encoded = json.dumps(self.items, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return {"source_id": self.source_id, **self.window.bounds(), "pages": self.pages,
                **self.counters,
                "reused_archive_count": sum(i.get("archive_status") == "reused_archive" for i in self.items),
                "discovery_complete": self.discovery_complete,
                "complete": self.discovery_complete and not self.reasons,
                "reasons": list(self.reasons), "discovered_count": len(self.items),
                "selected_count": sum(i["status"] == "in_window" for i in self.items),
                "date_unknown_count": sum(i["status"] == "date_unknown" for i in self.items),
                "quarantine_count": sum(i["status"] == "date_unknown" or bool(i.get("quarantine_reason")) for i in self.items),
                "max_top_ordinal": max((i["ordinal"] for i in self.items), default=0),
                "inventory_sha256": hashlib.sha256(encoded).hexdigest()}

    def finish(self, logger):
        summary = self.summary()
        # Externally observable even when nothing is emitted or archive de-dup
        # skips all rows. The normal pipeline records the raised source failure.
        logger.info("publication_window_coverage %s", json.dumps({**summary, "items": self.items}, ensure_ascii=False))
        if not summary["complete"]:
            raise RuntimeError("publication_window_incomplete: " + json.dumps(summary, ensure_ascii=False))
