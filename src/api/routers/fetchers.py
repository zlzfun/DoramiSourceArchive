"""抓取器目录与即时触发 Router（collector）。

阶段1 从 app.py 迁出的端点（路径不变，collector 网关仍由中间件统一强制）：
- GET  /api/fetchers          —— 列出所有已发现抓取器及其参数 schema
- POST /api/fetch/batch       —— 临时批量触发多个抓取节点
- POST /api/fetch/{fetcher_id} —— 临时触发单个抓取节点

抓取核心 run_collection_items / run_single_fetch_as_collection 仍留守 app.py（与
抓取追踪 + APScheduler 编排同源），经 _app() 延迟动态调用；test_run_overrides 复用
api.collection_planning。请求模型随迁，经 app.py re-export 保持 api.app.X 兼容。
"""

import importlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field as PydanticField

from api.collection_planning import test_run_overrides
from fetchers.registry import fetcher_registry

router = APIRouter(tags=["fetchers"])


def _app():
    """延迟取 api.app（避免导入环；动态调用留守的 run_collection_items/run_single_fetch_as_collection）。"""
    return importlib.import_module("api.app")


class FetchBatchItem(BaseModel):
    fetcher_id: str
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class FetchBatchParams(BaseModel):
    items: List[FetchBatchItem] = PydanticField(default_factory=list)


@router.get("/api/fetchers")
async def get_available_fetchers():
    return fetcher_registry.get_all_metadata()


@router.post("/api/fetch/batch")
async def trigger_fetch_batch(
        params: FetchBatchParams,
        test_limit: Optional[int] = None,
):
    items = [
        {
            "fetcher_id": item.fetcher_id,
            "params": {**item.params, **test_run_overrides(test_limit)},
        }
        for item in params.items
    ]
    if not items:
        raise HTTPException(status_code=400, detail="至少需要一个抓取节点")
    return await _app().run_collection_items(
        items,
        name="临时批量抓取",
        trigger_type="manual",
        run_scope="ad_hoc",
    )


@router.post("/api/fetch/{fetcher_id}")
async def trigger_fetch_dynamic(
        fetcher_id: str,
        params: Dict[str, Any] = Body(...),
        test_limit: Optional[int] = None,
):
    try:
        return await _app().run_single_fetch_as_collection(
            fetcher_id,
            {**params, **test_run_overrides(test_limit)},
            name=f"临时抓取: {fetcher_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
