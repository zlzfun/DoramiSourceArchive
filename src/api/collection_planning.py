"""采集规划共享 helper（阶段1 共享 helper 模块化）。

把「采集任务 / 节点组 / 投递作用域 → fetcher_id 列表 + 抓取参数」的规划逻辑集中到此：
fetcher_id 归一化、采集任务/节点组的 fetcher 解析与 item 构建、运行期参数覆盖、投递
作用域解析。被 collection-jobs / node-groups / tasks（留守 app.py 的采集引擎）与
feed/articles 投递视图共享。

仅依赖 ORM 与 textutils 纯工具，不依赖 app 级可变全局，故可被任意模块安全 import、
不与 api.app 成环。
"""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session

from api.textutils import _json_loads, _split_csv
from models.db import CollectionJobRecord, NodeGroupRecord


def normalize_fetcher_ids(fetcher_ids: Optional[List[str]]) -> List[str]:
    seen = set()
    normalized = []
    for fetcher_id in fetcher_ids or []:
        clean_id = str(fetcher_id).strip()
        if clean_id and clean_id not in seen:
            normalized.append(clean_id)
            seen.add(clean_id)
    return normalized


def resolve_collection_job_fetcher_ids(job: CollectionJobRecord, session: Session) -> List[str]:
    fetcher_ids = normalize_fetcher_ids(_json_loads(job.fetcher_ids_json, []))
    if fetcher_ids:
        return fetcher_ids
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            return normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, []))
    return []


def build_collection_job_items(job: CollectionJobRecord, session: Session) -> List[Dict[str, Any]]:
    default_params = {}
    per_fetcher_params = {}
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            default_params.update(_json_loads(group.params_json, {}))
            per_fetcher_params.update(_json_loads(group.per_fetcher_params_json, {}))
    default_params.update(_json_loads(job.params_json, {}))
    job_per_fetcher_params = _json_loads(job.per_fetcher_params_json, {})
    items = []
    for fetcher_id in resolve_collection_job_fetcher_ids(job, session):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        params.update(job_per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def build_node_group_items(group: NodeGroupRecord) -> List[Dict[str, Any]]:
    default_params = _json_loads(group.params_json, {})
    per_fetcher_params = _json_loads(group.per_fetcher_params_json, {})
    items = []
    for fetcher_id in normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, [])):
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
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
) -> List[str]:
    explicit_ids = normalize_fetcher_ids(([source_id] if source_id else []) + _split_csv(source_ids))
    scope_ids = []
    if group_id is not None:
        group = session.get(NodeGroupRecord, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="采集范围不存在")
        scope_ids.extend(_json_loads(group.fetcher_ids_json, []))
    if job_id is not None:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        scope_ids.extend(resolve_collection_job_fetcher_ids(job, session))

    scope_ids = normalize_fetcher_ids(scope_ids)
    if explicit_ids and scope_ids:
        scope_set = set(scope_ids)
        return [item for item in explicit_ids if item in scope_set]
    return explicit_ids or scope_ids
