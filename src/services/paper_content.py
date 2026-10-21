"""Paper reading content and automatic-analysis input are separate contracts."""
from __future__ import annotations

import json
import re
from typing import Any


ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v[1-9]\d*)?$", re.I)
HF_METHOD = "huggingface_daily_papers_json"


def strip_layout_markers(body: str) -> str:
    """Plain previews must not expose the reader's Markdown reference metadata."""
    return re.sub(r"(?im)^[ \t]*\[zqpl\]:[^\r\n]*(?:\r?\n|$)", "", body)


def metadata(article: Any) -> dict:
    try:
        value = json.loads(article.extensions_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def paper_metadata(article: Any) -> dict:
    value = metadata(article).get("paper", {})
    return value if isinstance(value, dict) else {}


def is_paper(article: Any) -> bool:
    from services.content_classification import rule_kind

    return bool(paper_metadata(article)) or rule_kind(
        source_id=article.source_id,
        source_url=article.source_url,
        content_type=article.content_type,
    ) == "paper"


def hf_arxiv_id(article: Any) -> str:
    """Only the known HF abstract producer is eligible for automatic enrichment."""
    ext = metadata(article)
    raw = ext.get("raw_data")
    if not isinstance(raw, dict) or raw.get("detail_extraction_method") != HF_METHOD:
        return ""
    value = str(raw.get("arxiv_id") or "").strip()
    return value if ARXIV_ID_RE.fullmatch(value) else ""


def analysis_body(article: Any) -> str:
    """Never use an enriched paper's full text as an implicit analysis fallback."""
    if not is_paper(article):
        return article.content or ""
    ext = metadata(article)
    paper = paper_metadata(article)
    if "abstract" in paper:
        return str(paper.get("abstract") or "")
    raw = ext.get("raw_data") or {}
    if isinstance(raw, dict):
        if raw.get("paper_abstract"):
            return str(raw["paper_abstract"])
        if (raw.get("detail_extraction_method") == HF_METHOD
                and paper.get("content_level") not in {"fulltext", "partial"}):
            # Legacy HF records were saved as abstracts. Keep their hash stable.
            return article.content or ""
    for key in ("abstract", "summary"):
        if isinstance(ext.get(key), str) and ext[key].strip():
            return ext[key]
    # Other paper sources may already carry full Markdown. Use only a marked
    # abstract; missing abstracts deliberately degrade to title-only analysis.
    match = re.search(
        r"(?ims)^#{1,6}\s+(?:abstract|摘要)\s*\n(.*?)(?=^#{1,6}\s|\Z)",
        article.content or "",
    )
    return match.group(1).strip() if match else ""


def content_hash_body(article: Any) -> str:
    """Keep legacy archive hashes except for stable, managed abstract contracts.

    Other paper sources still analyze abstracts, but changing their historical
    archive hash would invalidate scores and break mixed-version Archive Sync.
    analysis_input_hash separately fingerprints the actual model input.
    """
    raw = metadata(article).get("raw_data")
    if "abstract" in paper_metadata(article) or (
        isinstance(raw, dict) and raw.get("detail_extraction_method") == HF_METHOD
    ):
        return analysis_body(article)
    return article.content or ""


def delivery_body(article: Any) -> str:
    """External delivery keeps the abstract until full-text reuse is opted in."""
    if paper_metadata(article).get("content_level") in {"fulltext", "partial"}:
        return analysis_body(article)
    return article.content or ""


def delivery_metadata(article: Any) -> dict:
    ext = metadata(article)
    if paper_metadata(article).get("content_level") in {"fulltext", "partial"}:
        for key in ("translation_zh", "translation_zh_fp", "summary_zh", "summary_zh_fp", "image_notes"):
            ext.pop(key, None)
    return ext
