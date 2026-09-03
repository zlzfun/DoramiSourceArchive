"""Podcast publisher metadata merge rules shared by ingest and Archive Sync.

Podcast feeds may rotate enclosure URLs or add duration/transcript metadata after an
episode was first archived.  Only publisher-owned fields are refreshed here; reader-
side derived fields such as ``summary_zh`` and condensed-audio state remain untouched.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


PODCAST_FEED_EXTENSION_FIELDS = (
    "show_title",
    "author",
    "tags",
    "guid",
    "summary",
    "updated_date",
    "audio_url",
    "audio_mime",
    "audio_bytes",
    "duration_seconds",
    "episode",
    "season",
    "explicit",
    "image_url",
    "transcripts",
    "chapters_url",
    "chapters_mime",
    "raw_data",
)


def json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_metadata_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def merge_podcast_publisher_metadata(
    existing: Any,
    incoming: Any,
    incoming_extensions: Mapping[str, Any],
) -> bool:
    """Merge a newer Podcast record without erasing application-derived fields."""
    existing_extensions = json_object(getattr(existing, "extensions_json", "{}"))
    extensions_changed = False

    for field_name in PODCAST_FEED_EXTENSION_FIELDS:
        incoming_value = incoming_extensions.get(field_name)
        # A transiently incomplete feed must not erase previously captured metadata.
        if not _has_metadata_value(incoming_value):
            continue
        if existing_extensions.get(field_name) != incoming_value:
            existing_extensions[field_name] = incoming_value
            extensions_changed = True

    changed = extensions_changed
    for field_name in ("title", "source_url"):
        incoming_value = getattr(incoming, field_name, "")
        if incoming_value and getattr(existing, field_name) != incoming_value:
            setattr(existing, field_name, incoming_value)
            changed = True

    if (
        not getattr(existing, "has_content", False)
        and getattr(incoming, "has_content", False)
        and getattr(incoming, "content", None)
    ):
        existing.has_content = True
        existing.content = incoming.content
        changed = True

    if extensions_changed:
        existing.extensions_json = json.dumps(existing_extensions, ensure_ascii=False)
    return changed
