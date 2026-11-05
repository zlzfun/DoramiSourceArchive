"""源身份解析:把「一个出处」认回已有的 ``source_id``(issue #262)。

平台的计数、配额与订阅都按 ``source_id`` 组织,所以注入通道不能让调用方随手
命名一个新 id——同一个 X 账号用 ``x_kimi_moonshot`` 推一次、预设
``x_moonshot_ai`` 又抓一次,就凭空多出一个「独立信源」。这里按身份键
(X 的 ``user_id`` 优先、``handle`` 小写次之)查已有源;查不到才派生新 id。

身份键的来源:注册表里的 X 预设类(类属性 ``handle`` / ``user_id``),以及
绑定 X 抓取器的配置行(``params_json`` 里的 ``handle`` / ``user_id``)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from sqlmodel import Session, select

from models.db import SourceConfigRecord


X_PLATFORM = "x"
X_SOURCE_TYPES = frozenset({"x", "twitter", "x_timeline"})
X_TEMPLATE_FETCHER_ID = "generic_x_timeline"
_UNSAFE_ID_CHARS = re.compile(r"[^a-z0-9_]+")


def normalize_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@").strip().casefold()


def identity_key(platform: str, key_type: str, value: str) -> str:
    """``x:user_id:123`` / ``x:handle:deepseek_ai``——身份键的规范写法。"""
    value = str(value or "").strip()
    if key_type == "handle":
        value = normalize_handle(value)
    return f"{(platform or '').strip().casefold()}:{key_type}:{value}"


def social_identity_keys(platform: str, *, handle: str = "", user_id: str = "") -> list[str]:
    keys = []
    if str(user_id or "").strip():
        keys.append(identity_key(platform, "user_id", user_id))
    if normalize_handle(handle):
        keys.append(identity_key(platform, "handle", handle))
    return keys


def _config_params(row: SourceConfigRecord) -> dict:
    try:
        params = json.loads(row.params_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return params if isinstance(params, dict) else {}


def _is_x_config(row: SourceConfigRecord) -> bool:
    return (
        (row.fetcher_id or "").strip() == X_TEMPLATE_FETCHER_ID
        or (row.source_type or "").strip().casefold() in X_SOURCE_TYPES
    )


def _registry_x_sources() -> Iterable[tuple[str, str, str]]:
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001 - 注册表不可用时只按配置行解析
        return []
    out = []
    for source_id, cls in fetcher_registry._fetchers.items():
        if getattr(cls, "is_template", False):
            continue
        if str(getattr(cls, "platform", "") or "").strip().casefold() != X_PLATFORM:
            continue
        out.append((
            source_id,
            str(getattr(cls, "handle", "") or ""),
            str(getattr(cls, "user_id", "") or ""),
        ))
    return out


def x_identity_index(session: Session) -> dict[str, str]:
    """身份键 → source_id。配置行先登记、预设类后登记(同键时预设类赢:它是策展源)。"""
    index: dict[str, str] = {}
    for row in session.exec(select(SourceConfigRecord)).all():
        if not _is_x_config(row) or (row.owner_username or "").strip():
            continue
        if row.retired_at:
            continue
        params = _config_params(row)
        for key in social_identity_keys(
            X_PLATFORM,
            handle=str(params.get("handle") or ""),
            user_id=str(params.get("user_id") or ""),
        ):
            index.setdefault(key, row.source_id)
    for source_id, handle, user_id in _registry_x_sources():
        for key in social_identity_keys(X_PLATFORM, handle=handle, user_id=user_id):
            index[key] = source_id
    return index


@dataclass(frozen=True)
class SocialIdentity:
    source_id: str
    matched: bool          # True = 认回了已有源;False = 派生的新 id
    matched_by: str = ""   # 命中的身份键


def derive_social_source_id(platform: str, handle: str) -> str:
    safe_platform = _UNSAFE_ID_CHARS.sub("_", (platform or "social").strip().casefold()).strip("_") or "social"
    safe_handle = _UNSAFE_ID_CHARS.sub("_", normalize_handle(handle)).strip("_")
    if not safe_handle:
        raise ValueError("缺少作者 handle,无法确定来源")
    return f"{safe_platform}_{safe_handle}"


def _match(
    session: Session,
    platform: str,
    *,
    handle: str = "",
    user_id: str = "",
    index: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    if platform != X_PLATFORM:
        return None
    lookup = index if index is not None else x_identity_index(session)
    for key in social_identity_keys(platform, handle=handle, user_id=user_id):
        if key in lookup:
            return lookup[key], key
    return None


def match_social_source(
    session: Session,
    *,
    platform: str,
    handle: str = "",
    user_id: str = "",
    index: dict[str, str] | None = None,
) -> str | None:
    """只认回已有源;认不出返回 None(不派生)。"""
    platform = (platform or X_PLATFORM).strip().casefold() or X_PLATFORM
    hit = _match(session, platform, handle=handle, user_id=user_id, index=index)
    return hit[0] if hit else None


def resolve_social_source(
    session: Session,
    *,
    platform: str,
    handle: str = "",
    user_id: str = "",
    index: dict[str, str] | None = None,
) -> SocialIdentity:
    """按身份键认回已有源;都不中时派生 ``{platform}_{handle}``。"""
    platform = (platform or X_PLATFORM).strip().casefold() or X_PLATFORM
    hit = _match(session, platform, handle=handle, user_id=user_id, index=index)
    if hit:
        return SocialIdentity(source_id=hit[0], matched=True, matched_by=hit[1])
    return SocialIdentity(source_id=derive_social_source_id(platform, handle), matched=False)
