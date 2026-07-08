"""采集规划共享 helper（阶段1 共享 helper 模块化）。

把「采集任务 / 投递作用域 → fetcher_id 列表 + 抓取参数」的规划逻辑集中到此：
fetcher_id 归一化、采集任务的 fetcher 解析与 item 构建、运行期参数覆盖、投递
作用域解析。被 collection-jobs（留守 app.py 的采集引擎）与 feed/articles 投递
视图共享。

（实体简化阶段 2：节点组 NodeGroupRecord 已退役，group 解析分支与
build_node_group_items 一并移除；存量引用已由迁移内联进采集任务。）

仅依赖 ORM 与 textutils 纯工具，不依赖 app 级可变全局，故可被任意模块安全 import、
不与 api.app 成环。
"""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session

from api.textutils import _json_loads, _split_csv
from models.db import CollectionJobRecord


def normalize_fetcher_ids(fetcher_ids: Optional[List[str]]) -> List[str]:
    seen = set()
    normalized = []
    for fetcher_id in fetcher_ids or []:
        clean_id = str(fetcher_id).strip()
        if clean_id and clean_id not in seen:
            normalized.append(clean_id)
            seen.add(clean_id)
    return normalized


def resolve_collection_job_fetcher_ids(job: CollectionJobRecord) -> List[str]:
    return normalize_fetcher_ids(_json_loads(job.fetcher_ids_json, []))


def build_collection_job_items(job: CollectionJobRecord) -> List[Dict[str, Any]]:
    default_params = _json_loads(job.params_json, {})
    per_fetcher_params = _json_loads(job.per_fetcher_params_json, {})
    items = []
    for fetcher_id in resolve_collection_job_fetcher_ids(job):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def apply_run_param_overrides(
        items: List[Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not overrides:
        return items
    normalized_overrides = {
        key: value
        for key, value in overrides.items()
        if value is not None and value != ""
    }
    if not normalized_overrides:
        return items
    return [
        {
            **item,
            "params": {
                **(item.get("params") or {}),
                **normalized_overrides,
            },
        }
        for item in items
    ]


def test_run_overrides(test_limit: Optional[int] = None) -> Dict[str, Any]:
    if test_limit is None:
        return {}
    return {"limit": max(int(test_limit), 1)}


def resolve_delivery_source_ids(
        session: Session,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
) -> List[str]:
    explicit_ids = normalize_fetcher_ids(([source_id] if source_id else []) + _split_csv(source_ids))
    scope_ids = []
    if job_id is not None:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        scope_ids.extend(resolve_collection_job_fetcher_ids(job))

    scope_ids = normalize_fetcher_ids(scope_ids)
    if explicit_ids and scope_ids:
        scope_set = set(scope_ids)
        return [item for item in explicit_ids if item in scope_set]
    return explicit_ids or scope_ids
