"""注入通道(手工录入 / 社交导入 / Agent 推送)共用的校验(issue #262)。

抓取路径有「血统覆写」约束(``BaseFetcher.fetch`` 统一写 ``source_id`` /
``content_type``),注入路径没有——这里补上同一层的服务端约束:

- 保留 id 不接受注入:读者自定源 ``user_rss_*``、抓取模板 ``generic_*``、
  公共日报 ``dorami_daily_brief``、已下线源墓碑;
- 条目 id 一律落在 ``{source_id}_`` 命名空间下(抓取器同一约定),
  调用方给的 id 不能借用他源的主键空间;
- ``extensions_json`` 只接受 JSON 对象,坏 JSON 显式拒绝而不是静默丢弃;
- 存储层 ``save_with_outcome`` 的结果映射成准确的错误,而不是一律「ID 已存在」。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from storage.impl import db_storage


DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"
USER_SOURCE_PREFIX = "user_rss_"
_ITEM_ID_UNSAFE = re.compile(r"[\s]+")


def _registry_template_ids() -> set[str]:
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001
        return set()
    return {
        source_id
        for source_id, cls in fetcher_registry._fetchers.items()
        if getattr(cls, "is_template", False)
    }


def _decommissioned_ids() -> frozenset[str]:
    try:
        from fetchers.registry import DECOMMISSIONED_FETCHER_IDS
    except Exception:  # noqa: BLE001
        return frozenset()
    return DECOMMISSIONED_FETCHER_IDS


def reserved_source_id_reason(source_id: str, *, allow_daily_brief: bool = False) -> str | None:
    """注入通道不得写入的 source_id;返回拒绝原因,可写时返回 None。

    ``allow_daily_brief``:管理员手工录入保留「手工补一期日报」的既有用法;Agent 推送不放行。
    """
    sid = str(source_id or "").strip()
    if not sid:
        return "source_id 不能为空"
    if sid.startswith(USER_SOURCE_PREFIX):
        return "读者自定源只能由其抓取器写入"
    if sid == DAILY_BRIEF_SOURCE_ID and not allow_daily_brief:
        return "公共日报源只能由日报生成写入"
    if sid in _registry_template_ids():
        return "抓取模板不是来源,不能直接写入"
    if sid in _decommissioned_ids():
        return "该来源已下线"
    return None


def namespaced_item_id(source_id: str, raw_id: str = "", *, stable_value: str = "") -> str:
    """条目 id 落在 ``{source_id}_`` 命名空间。

    - 调用方 id 已带本源前缀:原样使用;
    - 带了别的前缀:整体挂到本源前缀下(``manual_123`` → ``x_foo_manual_123``);
    - 没给 id:按稳定值(规范 URL 等)取 sha1 前 16 位,与抓取器同一公式。
    """
    sid = str(source_id or "").strip()
    prefix = f"{sid}_"
    raw = _ITEM_ID_UNSAFE.sub("_", str(raw_id or "").strip())
    if raw:
        return raw if raw.startswith(prefix) else f"{prefix}{raw}"
    stable = str(stable_value or "").strip()
    if not stable:
        raise ValueError("缺少条目 id 与可用于生成 id 的链接")
    return f"{prefix}{hashlib.sha1(stable.encode('utf-8')).hexdigest()[:16]}"


def parse_extensions_param(value: Any) -> dict:
    """``extensions_json`` 接受 JSON 对象或其字符串形式;其余显式拒绝。"""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"extensions_json 不是合法 JSON:{exc.msg}") from exc
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            raise ValueError("extensions_json 必须是 JSON 对象")
        return parsed
    raise ValueError("extensions_json 必须是 JSON 对象")


# save_with_outcome 结果 → (HTTP 状态码, 读者可读原因)。成功结果不在表里。
SAVE_OUTCOME_ERRORS: dict[str, tuple[int, str]] = {
    db_storage.SAVE_DUPLICATE: (409, "该条目已存在"),
    db_storage.SAVE_REFRESHED: (409, "该条目已存在(已刷新元数据)"),
    db_storage.SAVE_ID_CONFLICT: (409, "条目 ID 已被其它来源占用"),
    db_storage.SAVE_REJECTED_AUTHORITY: (409, "该来源由远端权威节点管理,本机不能写入"),
    db_storage.SAVE_REJECTED_PODCAST_SOURCE: (400, "播客单集需要一个已启用的播客源"),
    db_storage.SAVE_REJECTED_PODCAST_STAGE: (409, "播客采集阶段当前已关闭"),
}


def outcome_error(outcome: str) -> tuple[int, str] | None:
    if outcome in db_storage.SAVE_STORED_OUTCOMES:
        return None
    return SAVE_OUTCOME_ERRORS.get(outcome, (400, f"未入库:{outcome}"))
