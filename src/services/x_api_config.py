"""X API 运行时配置与源级 User 缓存。

机密 Bearer Token 可由 env/ini 提供，也可按管理端契约写入
``AppSettingRecord`` 作为运行时覆盖。本模块不记录配置值，API 也只返回脱敏预览。
配置的保管契约（解析序/只写不回显/来源标注）走 ``services/credentials`` 统一层，
本模块只保留 X 特有的 User 缓存与头像派生。
User 缓存以 source_id 为粒度，同时记录 handle；handle 变更时缓存自动失效。它主要
服务只有 handle 的 SourceConfig 源；策展 preset 已固化稳定 user_id，不靠缓存避免解析。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Dict

from sqlmodel import Session, select

import config
from models.db import AppSettingRecord
from services import credentials


NAMESPACE = credentials.X_API_NAMESPACE

# KV key 沿用抽象层注册表(与历史存量一致,零迁移)。
KEY_BEARER_TOKEN = NAMESPACE.field_by_name("bearer_token").kv_key
KEY_BASE_URL = NAMESPACE.field_by_name("base_url").kv_key
KEY_TIMEOUT_SECONDS = NAMESPACE.field_by_name("timeout_seconds").kv_key
KEY_MAX_RESULTS = NAMESPACE.field_by_name("max_results").kv_key
KEY_MONTHLY_BUDGET_USD = NAMESPACE.field_by_name("monthly_budget_usd").kv_key
USER_CACHE_KEY_PREFIX = "x_api_user_cache:"


def _get_setting(session: Session, key: str) -> str:
    return credentials.get_setting(session, key)


def set_setting(session: Session, key: str, value: str) -> None:
    credentials.set_setting(session, key, value)


def resolve_x_api_config(session: Session) -> config.XApiConfig:
    """合并 env/ini 基线与 AppSettingRecord 运行时覆盖。"""
    values = credentials.resolve_values(session, NAMESPACE, config.settings.x_api)
    values["base_url"] = str(values["base_url"]).rstrip("/")
    return config.XApiConfig(**values)


def config_field_sources(session: Session) -> Dict[str, str]:
    """返回各字段有效值来源：runtime_kv | env | ini | default。"""
    return credentials.field_sources(session, NAMESPACE)


def overall_config_source(field_sources: Dict[str, str]) -> str:
    """按优先级概括当前配置来源，详情仍以 field_sources 为准。"""
    return credentials.overall_source(field_sources)


def user_cache_key(source_id: str) -> str:
    return f"{USER_CACHE_KEY_PREFIX}{(source_id or '').strip()}"


def _normalized_handle(handle: str) -> str:
    return (handle or "").strip().lstrip("@").casefold()


def larger_avatar_url(url: str) -> str:
    """X ``_normal`` 头像稳定替换为 400x400；非该形态时保持原 URL。"""
    value = (url or "").strip()
    return re.sub(r"_normal(?=\.[A-Za-z0-9]+(?:\?|$))", "_400x400", value, count=1)


def read_user_cache(
    session: Session,
    source_id: str,
    *,
    handle: str = "",
) -> Dict[str, Any] | None:
    raw = _get_setting(session, user_cache_key(source_id))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not str(value.get("user_id") or "").strip():
        return None
    if handle and _normalized_handle(str(value.get("handle") or "")) != _normalized_handle(handle):
        return None
    return value


def write_user_cache(
    session: Session,
    source_id: str,
    *,
    handle: str,
    user_id: str,
    user: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """幂等写源级身份/头像缓存；新响应缺字段时保留已有资料。"""
    existing = read_user_cache(session, source_id) or {}
    profile = user if isinstance(user, dict) else {}
    resolved_handle = str(profile.get("username") or handle).strip().lstrip("@")
    avatar_url = str(profile.get("profile_image_url") or "").strip()
    value = {
        **existing,
        "source_id": (source_id or "").strip(),
        "handle": resolved_handle,
        "user_id": str(profile.get("id") or user_id).strip(),
    }
    value.pop("updated_at", None)
    author_name = str(profile.get("name") or "").strip()
    if author_name:
        value["author_name"] = author_name
    if avatar_url:
        value["author_avatar_url"] = avatar_url
        value["author_avatar_url_large"] = larger_avatar_url(avatar_url)
    comparable_existing = dict(existing)
    comparable_existing.pop("updated_at", None)
    if value == comparable_existing:
        return existing
    value["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    set_setting(
        session,
        user_cache_key(source_id),
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )
    return value


def all_user_caches(session: Session) -> Dict[str, Dict[str, Any]]:
    rows = session.exec(
        select(AppSettingRecord).where(AppSettingRecord.key.startswith(USER_CACHE_KEY_PREFIX))
    ).all()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        source_id = row.key[len(USER_CACHE_KEY_PREFIX):]
        try:
            value = json.loads(row.value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if source_id and isinstance(value, dict):
            result[source_id] = value
    return result
