"""新账号默认订阅名单(issue #56 落地页早报波)。

读者账号登录点播种的默认订阅,此前是 ``api/app.py`` 的硬编码常量。落地页改成个人早报后,
新用户第一份早报的观感完全取决于这份名单当天有没有过线稿,公网与内网两套部署对「大众」
的理解也会分叉——名单因此改为 **代码缺省 + KV 覆盖**:

- ``DEFAULT_SUBSCRIPTION_SOURCE_IDS`` 是代码缺省(2026-09-14 按生产近两周逐源 ≥6.0 产量与
  中文/头部官方优先重定:日报 + 中文三大媒体 + 英文日更主力 The Decoder + OpenAI/Anthropic/
  DeepMind 官方 + X·OpenAI,播客暂不进);
- KV ``reader_default_source_ids``(JSON 数组)存在即整体覆盖,管理面 运维 → 内容 编辑;
  删除该 KV 即回落代码缺省。空数组是合法值(= 新账号不播种任何订阅)。

只影响播种时刻:存量账号不回填,退订过的不复活(播种一次性标记见 ``ensure_default_subscriptions``)。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from sqlmodel import Session, select

from models.db import AppSettingRecord, SourceConfigRecord
from services import daily_brief as daily_brief_service

PRIVATE_SOURCE_PREFIX = "user_rss_"  # 与 personal_digest / user_sources 同值,不反向 import 防循环
DEFAULT_SOURCE_IDS_KEY = "reader_default_source_ids"

DEFAULT_SUBSCRIPTION_SOURCE_IDS: List[str] = [
    daily_brief_service.DAILY_BRIEF_SOURCE_ID,  # 哆啦美·AI资讯日报(全站聚合;不进早报候选池,是文章容器的主菜)
    "web_qbitai",                   # 量子位(中文媒体)
    "web_ithome_ai",                # IT之家 AI(中文快讯)
    "web_aiera",                    # 新智元(中文媒体)
    "rss_the_decoder",              # The Decoder(英文日更,早报供给压舱石)
    "rss_openai_news",              # OpenAI 官方
    "web_anthropic_news",           # Anthropic 官方
    "rss_deepmind_blog",            # Google DeepMind 官方
    "x_openai",                     # X · OpenAI(社交容器)
]


def normalize_source_ids(raw: Iterable[Any]) -> List[str]:
    """去空/去重/保序;非字符串项丢弃。"""
    seen: set[str] = set()
    result: List[str] = []
    for item in raw or []:
        if not isinstance(item, str):
            continue
        source_id = item.strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        result.append(source_id)
    return result


def stored_source_ids(session: Session) -> Optional[List[str]]:
    """KV 覆盖值;未设置或损坏返回 None(回落代码缺省)。"""
    raw = daily_brief_service.get_setting(session, DEFAULT_SOURCE_IDS_KEY, "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return normalize_source_ids(parsed)


def default_source_ids(session: Session) -> List[str]:
    """播种时生效的名单:KV 覆盖 > 代码缺省。"""
    stored = stored_source_ids(session)
    return list(DEFAULT_SUBSCRIPTION_SOURCE_IDS) if stored is None else stored


def set_default_source_ids(session: Session, source_ids: Optional[Iterable[Any]]) -> List[str]:
    """写 KV 覆盖(``None`` = 删除覆盖回落代码缺省);返回生效名单。"""
    if source_ids is None:
        record = session.get(AppSettingRecord, DEFAULT_SOURCE_IDS_KEY)
        if record is not None:
            session.delete(record)
            session.commit()
        return list(DEFAULT_SUBSCRIPTION_SOURCE_IDS)
    normalized = normalize_source_ids(source_ids)
    daily_brief_service.set_setting(session, DEFAULT_SOURCE_IDS_KEY, json.dumps(normalized, ensure_ascii=False))
    return normalized


def unknown_source_ids(
    session: Session, source_ids: Iterable[str], registry_meta: Dict[str, Dict[str, Any]]
) -> List[str]:
    """名单里系统不认识的 id(播种它们只会得到一条指向空源的订阅)。

    合法域 = 注册表现役源 ∪ 公共 source_config(含播客目录)∪ 公共日报;用户私有自定源
    (``user_rss_`` 前缀 / 带 owner)一律不合法——默认订阅是公共资产。
    """
    ids = normalize_source_ids(source_ids)
    if not ids:
        return []
    configured = {
        row.source_id
        for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(ids))
        ).all()
        if not row.owner_username
    }
    unknown: List[str] = []
    for source_id in ids:
        if source_id.startswith(PRIVATE_SOURCE_PREFIX):
            unknown.append(source_id)
        elif source_id == daily_brief_service.DAILY_BRIEF_SOURCE_ID:
            continue
        elif source_id in registry_meta or source_id in configured:
            continue
        else:
            unknown.append(source_id)
    return unknown
