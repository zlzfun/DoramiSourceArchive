"""数据接入 Router（collector/admin）：AI 节点构建。

阶段1 从 app.py 迁出的「把源接进来」的 collector 端点：
- /api/source-builder/analyze|preview —— 由列表页 URL 生成配置节点建议 + 试抓预览。

社交动态导入 /api/import/social-posts 已于 issue #262 推送入口统一波退役:外部内容一律经
收录推送 ``POST /api/public/ingest/v1/items``(``services/ingest_batch.py``)进来。

说明：路径不变；collector 网关仍由中间件统一强制（COLLECTOR_API_PREFIXES 含
/api/source-builder）。数据访问经 Depends(deps.get_session)/deps.get_db_sink()；
current_username 经 _app() 延迟动态调用（避免导入环）。
"""

import importlib
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from fetchers.registry import fetcher_registry
from models.db import SourceConfigRecord
from services.media_store import SSRFError

router = APIRouter(tags=["ingest"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的 current_username）。"""
    return importlib.import_module("api.app")


# ==================== AI 节点构建（source-builder）====================

class SourceBuilderAnalyzeRequest(BaseModel):
    url: str


class SourceBuilderPreviewRequest(BaseModel):
    source_id: str = ""
    name: str = ""
    source_type: str = "web"
    url: str = ""
    params: Dict[str, Any] = PydanticField(default_factory=dict)


@router.post("/api/source-builder/analyze")
async def source_builder_analyze(
    req: SourceBuilderAnalyzeRequest, request: Request, session: Session = Depends(deps.get_session)
):
    """输入一个列表页 URL → 判类型 + 分析 + (LLM) 生成抓取节点配置建议。"""
    from services import source_builder

    # select 单列返回字符串;旧写法 row.source_id 在有配置行时抛 AttributeError(源表全集后必然触发)。
    existing = {str(source_id) for source_id in session.exec(select(SourceConfigRecord.source_id)).all()}
    existing |= set(fetcher_registry._fetchers.keys())
    try:
        result = await source_builder.analyze_url(
            req.url, session=session, existing_ids=existing,
            usage_username=_app().current_username(request) or None,
        )
    except SSRFError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "分析失败"))
    return result


@router.post("/api/source-builder/preview")
async def source_builder_preview(req: SourceBuilderPreviewRequest):
    """用建议配置试抓样例条目做验证（不落库）。"""
    from services import source_builder

    try:
        result = await source_builder.preview_config(req.model_dump())
    except SSRFError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "试抓失败"))
    return result


# ==================== 社交动态导入 ====================
