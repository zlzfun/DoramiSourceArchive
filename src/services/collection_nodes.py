"""Unified collection-node catalog and runtime resolution.

A collection job stores logical node IDs. Built-in nodes use their registry
fetcher ID directly; configured sources use ``SourceConfigRecord.source_id`` and
are resolved to their shared template fetcher immediately before execution.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable
from urllib.parse import urlparse

from sqlmodel import Session, select

from fetchers.registry import fetcher_registry
from models.db import SourceConfigRecord


PODCAST_SOURCE_TYPES = frozenset({"podcast", "podcast_rss"})
X_SOURCE_TYPES = frozenset({"x", "twitter", "x_timeline"})


def is_public_podcast_source(source_config: SourceConfigRecord) -> bool:
    return (
        not source_config.owner_username
        and (source_config.source_type or "").strip().lower() in PODCAST_SOURCE_TYPES
    )


def parse_json_object(raw_json: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_source_fetcher_id(source_config: SourceConfigRecord) -> str:
    if source_config.fetcher_id:
        return source_config.fetcher_id
    source_type = (source_config.source_type or "").strip().lower()
    if source_type in PODCAST_SOURCE_TYPES:
        return "generic_podcast_rss"
    if source_type in {"rss", "atom"}:
        return "generic_rss"
    if source_type in {"web", "webpage"}:
        return "generic_web"
    if source_type in X_SOURCE_TYPES:
        return "generic_x_timeline"
    return ""


def _configured_x_handle(source_config: SourceConfigRecord, params: Dict[str, Any]) -> str:
    handle = str(params.get("handle") or "").strip().lstrip("@")
    if handle:
        return handle
    raw_url = (source_config.url or "").strip()
    if not raw_url:
        return ""
    if "://" not in raw_url:
        return raw_url.strip("/").split("/", 1)[0].lstrip("@")
    parsed = urlparse(raw_url)
    return parsed.path.strip("/").split("/", 1)[0].lstrip("@")


def build_source_fetch_params(
    source_config: SourceConfigRecord,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build execution params from the current source row plus run overrides."""

    params = parse_json_object(source_config.params_json)
    source_type = (source_config.source_type or "").strip().lower()
    if source_type in {"web", "webpage"}:
        params.update({
            "listing_url": source_config.url,
            "site_name": params.get("site_name") or source_config.name,
        })
    elif source_type in X_SOURCE_TYPES:
        handle = _configured_x_handle(source_config, params)
        if handle:
            params["handle"] = handle
    else:
        params.update({
            "feed_url": source_config.url,
            "feed_name": source_config.name,
        })

    if overrides:
        params.update(overrides)

    # Source identity always comes from the persisted row. This also keeps a
    # saved job on the latest URL/name after an administrator edits the source.
    params["source_id"] = source_config.source_id
    params["category"] = source_config.category
    if source_type in {"web", "webpage"}:
        params["listing_url"] = source_config.url
    if source_type in {"rss", "atom"} | PODCAST_SOURCE_TYPES:
        params["feed_url"] = source_config.url
        params["feed_name"] = source_config.name

    if source_config.owner_username:
        # User RSS safety limits are owned by the configured-source path.
        params.pop("ssrf_guard", None)
        params.pop("max_response_bytes", None)
    return params


def resolve_collection_node(
    session: Session,
    node_id: str,
    overrides: Dict[str, Any] | None = None,
) -> tuple[str, Dict[str, Any]]:
    """Resolve a logical node ID to its executable fetcher and parameters."""

    clean_id = str(node_id or "").strip()
    if fetcher_registry.get_class(clean_id):
        return clean_id, dict(overrides or {})

    source = session.get(SourceConfigRecord, clean_id)
    if source is None:
        raise ValueError(f"未知的采集节点: {clean_id}")
    if not source.is_active and not is_public_podcast_source(source):
        raise ValueError(f"数据源 {clean_id} 未启用，拒绝采集")
    fetcher_id = resolve_source_fetcher_id(source)
    if not fetcher_id or not fetcher_registry.get_class(fetcher_id):
        raise ValueError(f"数据源 {clean_id} 未绑定可用抓取器")
    return fetcher_id, build_source_fetch_params(source, overrides)


def _content_tags(record: SourceConfigRecord) -> list[str]:
    try:
        value = json.loads(record.content_tags_json or "[]")
    except (TypeError, ValueError):
        value = []
    return value if isinstance(value, list) else []


def _configured_limit(record: SourceConfigRecord, default: int = 20) -> int:
    raw = parse_json_object(record.params_json).get("limit", default)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return default


def configured_source_metadata(record: SourceConfigRecord) -> Dict[str, Any]:
    """Project a public SourceConfig into the registry node shape."""

    source_type = (record.source_type or "").strip().lower()
    shape = "podcast" if source_type in PODCAST_SOURCE_TYPES else "article"
    content_type = "podcast_episode" if shape == "podcast" else "rss_article"
    return {
        "id": record.source_id,
        "name": record.name,
        "icon": "",
        "desc": record.description or record.url,
        "category": record.category or source_type,
        "content_type": content_type,
        "shape": shape,
        "is_template": False,
        "source_config_node": True,
        "source_type": source_type,
        "execution_fetcher_id": resolve_source_fetcher_id(record),
        "feed_url": record.url,
        "source_owner": record.source_owner,
        "source_brand": record.source_brand,
        "source_scope": record.source_scope,
        "source_channel": record.source_channel,
        "base_url": record.base_url or record.url,
        "provenance_tier": record.provenance_tier,
        "content_tags": _content_tags(record),
        "signal_strength": record.signal_strength,
        "noise_risk": record.noise_risk,
        "fetch_reliability": record.fetch_reliability,
        "ai_analysis_enabled": record.ai_analysis_enabled,
        "parameters": [{
            "field": "limit",
            "label": "单次获取上限",
            "type": "number",
            "default": _configured_limit(record),
        }],
    }


def collection_node_catalog(
    session: Session,
    *,
    source_types: Iterable[str] = PODCAST_SOURCE_TYPES,
) -> list[Dict[str, Any]]:
    """Return registry nodes plus public configured nodes in one catalog."""

    metadata = list(fetcher_registry.get_all_metadata())
    normalized_types = {str(value).strip().lower() for value in source_types}
    records = session.exec(
        select(SourceConfigRecord)
        .where(SourceConfigRecord.owner_username == "")
        .where(SourceConfigRecord.source_type.in_(normalized_types))
        .order_by(SourceConfigRecord.name)
    ).all()
    existing_ids = {item["id"] for item in metadata}
    metadata.extend(
        configured_source_metadata(record)
        for record in records
        if record.source_id not in existing_ids
    )
    return metadata
