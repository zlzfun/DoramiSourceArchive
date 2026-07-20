"""X API 运行时配置与源级 User 缓存。

机密 Bearer Token 可由 env/ini 提供，也可按管理端契约写入
``AppSettingRecord`` 作为运行时覆盖。本模块不记录配置值，API 也只返回脱敏预览。
User 缓存以 source_id 为粒度，同时记录 handle；handle 变更时缓存自动失效。它主要
服务只有 handle 的 SourceConfig 源；策展 preset 已固化稳定 user_id，不靠缓存避免解析。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any, Dict

from sqlmodel import Session, select

import config
from models.db import AppSettingRecord


KEY_BEARER_TOKEN = "x_api_bearer_token"
KEY_BASE_URL = "x_api_base_url"
KEY_TIMEOUT_SECONDS = "x_api_timeout_seconds"
KEY_MAX_RESULTS = "x_api_max_results"
KEY_MONTHLY_BUDGET_USD = "x_api_monthly_budget_usd"
USER_CACHE_KEY_PREFIX = "x_api_user_cache:"

_FIELD_KEYS = {
    "bearer_token": KEY_BEARER_TOKEN,
    "base_url": KEY_BASE_URL,
    "timeout_seconds": KEY_TIMEOUT_SECONDS,
    "max_results": KEY_MAX_RESULTS,
    "monthly_budget_usd": KEY_MONTHLY_BUDGET_USD,
}


def _get_setting(session: Session, key: str) -> str:
    record = session.get(AppSettingRecord, key)
    return str(record.value or "") if record is not None else ""


def set_setting(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def resolve_x_api_config(session: Session) -> config.XApiConfig:
    """合并 env/ini 基线与 AppSettingRecord 运行时覆盖。"""
    base = config.settings.x_api

    def _str(key: str, fallback: str) -> str:
        value = _get_setting(session, key).strip()
        return value if value else fallback

    def _int(key: str, fallback: int) -> int:
        value = _get_setting(session, key).strip()
        try:
            return int(value) if value else fallback
        except ValueError:
            return fallback

    def _float(key: str, fallback: float) -> float:
        value = _get_setting(session, key).strip()
        try:
            return float(value) if value else fallback
        except ValueError:
            return fallback

    return config.XApiConfig(
        bearer_token=_str(KEY_BEARER_TOKEN, base.bearer_token),
        base_url=_str(KEY_BASE_URL, base.base_url).rstrip("/"),
        timeout_seconds=_int(KEY_TIMEOUT_SECONDS, base.timeout_seconds),
        max_results=_int(KEY_MAX_RESULTS, base.max_results),
        monthly_budget_usd=_float(KEY_MONTHLY_BUDGET_USD, base.monthly_budget_usd),
    )


def config_field_sources(session: Session) -> Dict[str, str]:
    """返回各字段有效值来源：runtime_kv | env | ini | default。"""
    parser = config._read_config_file()  # 与 load_config 使用同一路径裁决
    sources: Dict[str, str] = {}
    for field, key in _FIELD_KEYS.items():
        if _get_setting(session, key).strip():
            sources[field] = "runtime_kv"
        elif field == "bearer_token" and os.getenv("DORAMI_X_BEARER_TOKEN", "").strip():
            sources[field] = "env"
        elif parser.has_option("x_api", field):
            sources[field] = "ini"
        else:
            sources[field] = "default"
    return sources


def overall_config_source(field_sources: Dict[str, str]) -> str:
    """按优先级概括当前配置来源，详情仍以 field_sources 为准。"""
    for source in ("runtime_kv", "env", "ini", "default"):
        if source in field_sources.values():
            return source
    return "default"


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
