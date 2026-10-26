"""Pure title-cache validation shared by readers, workers and Archive Sync."""
from __future__ import annotations

import hashlib
import json
import re

TITLE_KEY = "translation_zh_title"
TITLE_FP_KEY = "translation_zh_title_fp"


def fingerprint(title: str) -> str:
    return hashlib.sha256((title or "").strip().encode("utf-8")).hexdigest()[:16]


def extensions(article) -> dict:
    # Reader citation inputs may be ORM rows or dictionaries carrying the raw
    # extension receipt. Never trust a preprojected top-level title_zh value.
    raw = (article.get("extensions_json", article.get("extensions"))
           if isinstance(article, dict) else getattr(article, "extensions_json", None))
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def title_receipt(article, *, title: str | None = None) -> str | None:
    """Read a strictly validated generation receipt, including retained names.

    A provider may correctly return the original spelling of a proper name.
    That is still a completed generation and must not cause another paid call.
    Missing fingerprints never establish a receipt for the current original.
    """
    original = article.get("title") if isinstance(article, dict) else getattr(article, "title", None)
    title = str((original if title is None else title) or "").strip()
    if not title:
        return None
    ext = extensions(article)
    value = ext.get(TITLE_KEY)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip() if ext.get(TITLE_FP_KEY) == fingerprint(title) else None


def cached_title(article, *, title: str | None = None) -> str | None:
    """Project a receipt into an optional Chinese display title.

    Retained original names and already-Chinese originals need no second title,
    even though their generation receipts remain reusable by every caller.
    """
    original = article.get("title") if isinstance(article, dict) else getattr(article, "title", None)
    title = str((original if title is None else title) or "").strip()
    value = title_receipt(article, title=title)
    if value is None or value == title:
        return None
    sample = title[:2000]
    if len(re.findall(r"[\u3400-\u9fff]", sample)) > len(re.findall(r"[A-Za-z]", sample)) * 0.2:
        return None
    return value


def preserve_title_receipt(existing, incoming: dict, *, title: str) -> dict:
    """Keep only a matching title receipt across an extension replacement.

    A valid incoming receipt wins. Otherwise preserve the existing receipt only
    when it belongs to the new original; unrelated metadata follows the input.
    """
    merged = dict(incoming)
    value = title_receipt({"title": title, "extensions": merged})
    if value is None:
        value = title_receipt(existing, title=title)
    merged.pop(TITLE_KEY, None)
    merged.pop(TITLE_FP_KEY, None)
    if value is not None:
        merged[TITLE_KEY], merged[TITLE_FP_KEY] = value, fingerprint(title)
    return merged
