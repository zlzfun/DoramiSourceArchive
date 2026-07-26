"""读者面源可见性（管理面「隐藏节点」）。

隐藏名单存 AppSettingRecord KV（``reader_hidden_source_ids``，JSON 数组），按
``source_id`` 生效——预置 fetcher 与 SourceConfig 源同一键位，无需新表或迁移。
语义是「读者面临时下架」：观察期节点故障/文章缺陷等场景即时止损——发现页目录、
源栏、文章列表、聚合 feed、MCP/RAG 检索范围一并排除；订阅关系与归档数据原样
保留，恢复可见后一切照旧。管理面（知识台账/节点管理，admin 会话）不受影响。
"""

import json
from typing import Set

from sqlmodel import Session

from models.db import AppSettingRecord

HIDDEN_SOURCES_KEY = "reader_hidden_source_ids"


def hidden_source_ids(session: Session) -> Set[str]:
    """当前对读者隐藏的 source_id 集合；未配置/坏数据一律视为空集。"""
    record = session.get(AppSettingRecord, HIDDEN_SOURCES_KEY)
    if record is None or not record.value:
        return set()
    try:
        data = json.loads(record.value)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).strip() for item in data if str(item).strip()}


def set_source_hidden(session: Session, source_id: str, hidden: bool) -> list[str]:
    """把单个源标为隐藏/恢复可见（幂等），提交后返回最新的隐藏名单（有序）。"""
    source_id = (source_id or "").strip()
    if not source_id:
        raise ValueError("source_id 不能为空")
    current = hidden_source_ids(session)
    if hidden:
        current.add(source_id)
    else:
        current.discard(source_id)
    value = json.dumps(sorted(current), ensure_ascii=False)
    record = session.get(AppSettingRecord, HIDDEN_SOURCES_KEY)
    if record is None:
        record = AppSettingRecord(key=HIDDEN_SOURCES_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()
    return sorted(current)
