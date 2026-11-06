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

from fetchers.registry import admission_status_of, fetcher_registry
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


def default_fetcher_id_for_source_type(source_type: str) -> str:
    """``source_type`` → 默认采集绑定的模板 id;没有对应模板返回空串。

    创建 / 改类型时用它把绑定写实(issue #262 R1b:迁移 c5e1a7b3d9f2 已把存量行写实),
    使「``fetcher_id`` 为空 = 没有采集绑定」逐步成立。
    """
    source_type = (source_type or "").strip().lower()
    if source_type in PODCAST_SOURCE_TYPES:
        return "generic_podcast_rss"
    if source_type in {"rss", "atom"}:
        return "generic_rss"
    if source_type in {"web", "webpage"}:
        return "generic_web"
    if source_type in X_SOURCE_TYPES:
        return "generic_x_timeline"
    return ""


# 没有采集通道的来路:收录通道建的临时源、R1c 回填的存量 id、系统源。它们的 source_type
# 只描述形态(x / podcast / archive),不能再被推导成「绑定了某个模板」(issue #262 F7)。
_UNBOUND_ORIGINS = frozenset({"ingest", "legacy", "system"})


def resolve_source_fetcher_id(source_config: SourceConfigRecord) -> str:
    if source_config.fetcher_id:
        return source_config.fetcher_id
    if str(getattr(source_config, "origin", "") or "").strip().lower() in _UNBOUND_ORIGINS:
        return ""
    if getattr(source_config, "retired_at", None):
        return ""
    return default_fetcher_id_for_source_type(source_config.source_type or "")


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

    stored_params = parse_json_object(source_config.params_json)
    params = dict(stored_params)
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
    if source_type in {"web", "webpage"}:
        params["listing_url"] = source_config.url
    if source_type in {"rss", "atom"} | PODCAST_SOURCE_TYPES:
        params["feed_url"] = source_config.url
        params["feed_name"] = source_config.name
    if source_type in PODCAST_SOURCE_TYPES:
        # Episode identity is persisted source policy, never a per-run override.
        # Promoted custom Podcasts use this compatibility namespace to hit their
        # historical Article primary keys after the public source-id changes.
        params["entry_id_namespace"] = str(
            stored_params.get("entry_id_namespace") or source_config.source_id
        ).strip()

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
        raise ValueError(f"未知的源: {clean_id}")
    if source.retired_at:
        # 退役(含「被引用的源删除改退役」,issue #262 R1c)先于播客「始终可采集」的豁免。
        raise ValueError(f"数据源 {clean_id} 已退役，拒绝采集")
    if not source.is_active and not is_public_podcast_source(source):
        raise ValueError(f"数据源 {clean_id} 未启用，拒绝采集")
    fetcher_id = resolve_source_fetcher_id(source)
    if not fetcher_id or not fetcher_registry.get_class(fetcher_id):
        raise ValueError(f"数据源 {clean_id} 未绑定可用抓取器")
    return fetcher_id, build_source_fetch_params(source, overrides)


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
        "admission_status": admission_status_of(record.admission_status),
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
        # 节点 = 有绑定 ∧ 未退役(issue #262 R1c):注入通道建的播客临时源没有采集通道。
        if record.source_id not in existing_ids
        and not record.retired_at
        and resolve_source_fetcher_id(record)
    )
    return metadata


# ── 源管理目录(issue #262 第三波):全部公共源,不只是有采集绑定的 ──────────────

_ALL_SOURCE_TYPES = frozenset({"rss", "atom", "web", "webpage"}) | PODCAST_SOURCE_TYPES | X_SOURCE_TYPES


def is_bound(record: SourceConfigRecord) -> bool:
    """有采集绑定:能解析到注册表里存在的抓取器,且未退役。"""
    if record.retired_at:
        return False
    fetcher_id = resolve_source_fetcher_id(record)
    return bool(fetcher_id and fetcher_registry.get_class(fetcher_id))


def _listing_of(admission_status: str) -> str:
    return "unlisted" if (admission_status or "").strip().lower() == "provisional" else "listed"


def unbound_source_metadata(record: SourceConfigRecord) -> Dict[str, Any]:
    """没有采集绑定的公共源(收录临时源 / 存量回填 / 系统源 / 退役)在源管理目录里的形状。

    字段与 ``configured_source_metadata`` 对齐;前端按 ``bound=False`` 隐藏运行 / 试抓 / 编入任务,
    按 ``origin`` 决定可做的治理动作(收录源:列入 / 改名;其余只有读者面隐藏)。
    """
    from services import source_catalog

    view = source_catalog.view_from_row(record)
    content_type = view.content_type or {
        "podcast": "podcast_episode", "social": "social_post",
    }.get(view.shape, "web_article")
    raw_status = (record.admission_status or "").strip().lower() or view.admission_status
    return {
        "id": record.source_id,
        "name": view.name or record.source_id,
        "icon": view.icon,
        "desc": view.description or view.homepage_url,
        "admission_status": raw_status,
        "content_type": content_type,
        "shape": view.shape,
        "is_template": False,
        "source_config_node": True,
        "source_type": view.source_type,
        "execution_fetcher_id": "",
        "bound": False,
        "origin": view.origin,
        "listing": _listing_of(raw_status),
        "platform": view.platform,
        "avatar_url": view.avatar_url,
        "homepage_url": view.homepage_url,
        "feed_url": record.url or "",
        "base_url": view.homepage_url or view.base_url or record.url or "",
        "source_owner": view.source_owner,
        "source_brand": view.source_brand,
        "source_scope": view.source_scope,
        "source_channel": view.source_channel,
        "provenance_tier": view.provenance_tier,
        "signal_strength": view.signal_strength,
        "noise_risk": view.noise_risk,
        "fetch_reliability": view.fetch_reliability,
        "ai_analysis_enabled": view.ai_analysis_enabled,
        "retired": bool(record.retired_at),
        "retired_at": record.retired_at or "",
        "parameters": [],
    }


def source_management_catalog(session: Session, *, include_retired: bool = False) -> list[Dict[str, Any]]:
    """源管理页的目录 = 采集节点目录(有绑定)+ 没有绑定的公共源行。

    用户拍板(2026-11-06):源管理列全部源——目录预设、配置源、收录临时源、存量、系统源都可见,
    只是没有采集绑定的不能运行 / 试抓 / 编入采集任务。退役源默认不列(``include_retired`` 时带
    ``retired=True`` 列出)。读者私有源(``owner_username`` 非空)仍由源健康汇总并入,不在这里。
    每个条目都带 ``bound`` / ``origin`` / ``listing`` / ``retired``。
    """
    nodes = [
        {**item, "bound": True, "origin": item.get("origin") or "catalog",
         "listing": _listing_of(str(item.get("admission_status") or "")), "retired": False}
        for item in collection_node_catalog(session, source_types=_ALL_SOURCE_TYPES)
    ]
    known = {item["id"]: item for item in nodes}
    rows_by_id = {
        row.source_id: row
        for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.owner_username == "")
        ).all()
    }
    for source_id, item in known.items():
        row = rows_by_id.get(source_id)
        if row is not None and item.get("source_config_node"):
            item["origin"] = (row.origin or "").strip().lower() or "admin"
    for source_id, row in sorted(rows_by_id.items()):
        if source_id in known:
            continue
        if row.retired_at and not include_retired:
            continue
        nodes.append(unbound_source_metadata(row))
    return nodes


def unbound_source_ids(session: Session, node_ids: Iterable[str]) -> list[str]:
    """给定逻辑 id 里没有采集绑定(或已退役)的公共源——编入采集任务前拒绝;未知 id 留给运行时报错。"""
    missing: list[str] = []
    for node_id in node_ids:
        clean = str(node_id or "").strip()
        if not clean or fetcher_registry.get_class(clean):
            continue
        row = session.get(SourceConfigRecord, clean)
        if row is not None and not is_bound(row):
            missing.append(clean)
    return missing
