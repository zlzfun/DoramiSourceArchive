"""数据源配置 Router（collector）：用户自定义来源的 CRUD + 触发抓取。

阶段1 从 app.py 迁出的 /api/source-configs* 端点（路径不变，collector 网关仍由中间件
统一强制）：列表/详情/创建/更新/启停/删除 + 单源触发 + 批量触发活跃 RSS/Web 源。

配置序列化与 source_type→fetcher 路由 helper（serialize_source_config /
normalize_source_id / parse_json_object / resolve_source_fetcher_id /
build_source_fetch_params）随迁入本文件，经 app.py re-export 保持 api.app.X 兼容
（test_configurable_web_fetcher 直接调用 app_module.resolve_source_fetcher_id 等）。

采集核心 run_single_fetch_as_collection / run_collection_items 仍留守 app.py（与
抓取追踪 + APScheduler 编排同源），经 _app() 延迟动态调用。数据访问经
deps.get_session()。
"""

import importlib
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from api.sources import X_SOURCE_TYPES, configured_source_platform, configured_source_shape
from api.textutils import _json_dumps, _now_iso
from models.db import SourceConfigRecord
from services import jobs

router = APIRouter(tags=["source-configs"])


def _app():
    """延迟取 api.app（避免导入环；动态调用留守的采集核心 run_*_collection*）。"""
    return importlib.import_module("api.app")


# ==================== 请求模型 ====================

class SourceConfigCreate(BaseModel):
    source_id: str
    name: str
    source_type: str = "rss"
    url: str = ""
    category: str = ""
    fetcher_id: str = ""
    description: str = ""
    source_owner: str = ""
    source_brand: str = ""
    source_scope: str = ""
    source_channel: str = ""
    base_url: str = ""
    provenance_tier: str = ""
    content_tags: List[str] = PydanticField(default_factory=list)
    signal_strength: str = ""
    noise_risk: str = ""
    fetch_reliability: str = ""
    is_active: bool = True
    fetch_interval_minutes: Optional[int] = None
    cron_expr: str = ""
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class SourceConfigUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    fetcher_id: Optional[str] = None
    description: Optional[str] = None
    source_owner: Optional[str] = None
    source_brand: Optional[str] = None
    source_scope: Optional[str] = None
    source_channel: Optional[str] = None
    base_url: Optional[str] = None
    provenance_tier: Optional[str] = None
    content_tags: Optional[List[str]] = None
    signal_strength: Optional[str] = None
    noise_risk: Optional[str] = None
    fetch_reliability: Optional[str] = None
    is_active: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None
    cron_expr: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class SourceFetchParams(BaseModel):
    params: Dict[str, Any] = PydanticField(default_factory=dict)


# ==================== 序列化 / 路由 helper ====================

def serialize_source_config(record: SourceConfigRecord) -> Dict[str, Any]:
    data = record.model_dump()
    try:
        data["params"] = json.loads(record.params_json or "{}")
    except json.JSONDecodeError:
        data["params"] = {}
    try:
        tags = json.loads(record.content_tags_json or "[]")
        data["content_tags"] = tags if isinstance(tags, list) else []
    except json.JSONDecodeError:
        data["content_tags"] = []
    data["shape"] = configured_source_shape(
        record.source_type, resolve_source_fetcher_id(record)
    )
    data["platform"] = configured_source_platform(
        record.source_type, resolve_source_fetcher_id(record)
    )
    return data


def normalize_source_id(source_id: str) -> str:
    return source_id.strip()


def parse_json_object(raw_json: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_source_fetcher_id(source_config: SourceConfigRecord) -> str:
    if source_config.fetcher_id:
        return source_config.fetcher_id
    source_type = (source_config.source_type or "").strip().lower()
    if source_type in {"rss", "atom"}:
        return "generic_rss"
    if source_type in {"web", "webpage"}:
        return "generic_web"
    if source_type in X_SOURCE_TYPES:
        return "generic_x_timeline"
    return ""


def _configured_x_handle(source_config: SourceConfigRecord, params: Dict[str, Any]) -> str:
    """优先使用 params.handle，并兼容把 url 填成 @handle 或 x.com/handle。"""
    handle = str(params.get("handle") or "").strip().lstrip("@")
    if handle:
        return handle
    raw_url = (source_config.url or "").strip()
    if not raw_url:
        return ""
    if "://" not in raw_url:
        return raw_url.strip("/").split("/", 1)[0].lstrip("@")
    parsed = urlparse(raw_url)
    return parsed.path.strip("/").split("/", 1)[0].lstrip("@")


def build_source_fetch_params(source_config: SourceConfigRecord, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = parse_json_object(source_config.params_json)
    params.update({
        "source_id": source_config.source_id,
        "category": source_config.category,
    })
    source_type = (source_config.source_type or "").strip().lower()
    if source_type in {"web", "webpage"}:
        # 通用网页抓取器（generic_web）：url 即列表页；其余 web 配置（URL 模式 / 详情 Profile /
        # listing_css）已在 params_json 内，随上面的 parse_json_object 透传。
        params.update({
            "listing_url": source_config.url,
            "site_name": params.get("site_name") or source_config.name,
        })
    elif source_type in X_SOURCE_TYPES:
        # X 通用模板：handle/user_id 均从 params_json 透传；url 只是
        # handle 未填时的便捷兜底，不注入 RSS/Web 专用参数。
        handle = _configured_x_handle(source_config, params)
        if handle:
            params["handle"] = handle
    else:
        # RSS/Atom 等：维持既有 feed_url/feed_name 语义不变。
        params.update({
            "feed_url": source_config.url,
            "feed_name": source_config.name,
        })
    if overrides:
        params.update(overrides)
    return params


# ==================== CRUD ====================

@router.get("/api/source-configs")
def get_source_configs(
        source_type: Optional[str] = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    query = select(SourceConfigRecord)
    if source_type:
        query = query.where(SourceConfigRecord.source_type == source_type)
    if category:
        query = query.where(SourceConfigRecord.category == category)
    if is_active is not None:
        query = query.where(SourceConfigRecord.is_active == is_active)
    if search:
        query = query.where(SourceConfigRecord.name.contains(search))
    query = query.order_by(SourceConfigRecord.source_type, SourceConfigRecord.name).offset(skip).limit(limit)
    return [serialize_source_config(record) for record in session.exec(query).all()]


@router.get("/api/source-configs/{source_id}")
def get_source_config(source_id: str, session: Session = Depends(deps.get_session)):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")
    return serialize_source_config(record)


@router.post("/api/source-configs")
def create_source_config(params: SourceConfigCreate, session: Session = Depends(deps.get_session)):
    source_id = normalize_source_id(params.source_id)
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")

    existing = session.get(SourceConfigRecord, source_id)
    if existing:
        raise HTTPException(status_code=400, detail="该 source_id 已存在")

    now = _now_iso()
    record = SourceConfigRecord(
        source_id=source_id,
        name=params.name.strip(),
        source_type=params.source_type.strip() or "rss",
        url=params.url.strip(),
        category=params.category.strip(),
        fetcher_id=params.fetcher_id.strip(),
        description=params.description.strip(),
        source_owner=params.source_owner.strip(),
        source_brand=params.source_brand.strip(),
        source_scope=params.source_scope.strip(),
        source_channel=params.source_channel.strip(),
        base_url=params.base_url.strip(),
        provenance_tier=params.provenance_tier.strip(),
        content_tags_json=json.dumps(params.content_tags or [], ensure_ascii=False),
        signal_strength=params.signal_strength.strip(),
        noise_risk=params.noise_risk.strip(),
        fetch_reliability=params.fetch_reliability.strip(),
        is_active=params.is_active,
        fetch_interval_minutes=params.fetch_interval_minutes,
        cron_expr=params.cron_expr.strip(),
        params_json=_json_dumps(params.params),
        created_at=now,
        updated_at=now
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return serialize_source_config(record)


@router.put("/api/source-configs/{source_id}")
def update_source_config(source_id: str, params: SourceConfigUpdate, session: Session = Depends(deps.get_session)):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")

    update_data = params.dict(exclude_unset=True)
    for key, value in update_data.items():
        if key == "params":
            record.params_json = _json_dumps(value)
        elif key == "content_tags":
            record.content_tags_json = json.dumps(value or [], ensure_ascii=False)
        elif isinstance(value, str):
            setattr(record, key, value.strip())
        else:
            setattr(record, key, value)

    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return serialize_source_config(record)


@router.post("/api/source-configs/{source_id}/toggle")
def toggle_source_config(
        source_id: str, is_active: bool = Body(..., embed=True), session: Session = Depends(deps.get_session)
):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")
    record.is_active = is_active
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return serialize_source_config(record)


@router.delete("/api/source-configs/{source_id}")
def delete_source_config(source_id: str, session: Session = Depends(deps.get_session)):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")
    session.delete(record)
    session.commit()
    return {"status": "success"}


# ==================== 触发抓取 ====================

@router.post("/api/source-configs/{source_id}/fetch")
async def fetch_source_config(
        source_id: str, body: Optional[SourceFetchParams] = None, session: Session = Depends(deps.get_session)
):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")
    if not record.is_active:
        raise HTTPException(status_code=400, detail="数据源已停用，无法触发抓取")

    fetcher_id = resolve_source_fetcher_id(record)
    if not fetcher_id:
        raise HTTPException(status_code=400, detail="该数据源未绑定可用抓取器")
    params = build_source_fetch_params(record, body.params if body else {})

    try:
        result = await _app().run_single_fetch_as_collection(
            fetcher_id,
            params,
            name=f"临时抓取: {source_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
        return {"source_id": source_id, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/source-configs/fetch-active-rss")
async def fetch_active_rss_sources(
        body: Optional[SourceFetchParams] = None, session: Session = Depends(deps.get_session)
):
    records = session.exec(
        select(SourceConfigRecord)
        .where(SourceConfigRecord.is_active == True)  # noqa: E712
        .where(SourceConfigRecord.source_type.in_(["rss", "atom"]))
        .order_by(SourceConfigRecord.name)
    ).all()

    items = []
    skipped_results = []
    for record in records:
        fetcher_id = resolve_source_fetcher_id(record)
        if not fetcher_id:
            skipped_results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue

        params = build_source_fetch_params(record, body.params if body else {})
        items.append({"source_id": record.source_id, "fetcher_id": fetcher_id, "params": params})

    async def _work(bg) -> Dict[str, Any]:
        result = await _app().run_collection_items(
            items,
            name="临时抓取: 活跃 RSS 数据源",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
        results = skipped_results + [
            {"source_id": item.get("source_id"), **item_result}
            for item, item_result in zip(items, result["results"])
        ]
        return {**result, "results": results}

    bg_job = jobs.launch(deps.get_db_sink().engine, "fetch_active_rss", _work,
                         payload={"count": len(items)})
    return {"status": "accepted", "job_id": bg_job.id}


@router.post("/api/source-configs/fetch-active-web")
async def fetch_active_web_sources(
        body: Optional[SourceFetchParams] = None, session: Session = Depends(deps.get_session)
):
    """批量触发所有启用的 web/webpage 数据源（经 generic_web 配置驱动抓取）。镜像 fetch-active-rss。"""
    records = session.exec(
        select(SourceConfigRecord)
        .where(SourceConfigRecord.is_active == True)  # noqa: E712
        .where(SourceConfigRecord.source_type.in_(["web", "webpage"]))
        .order_by(SourceConfigRecord.name)
    ).all()

    items = []
    skipped_results = []
    for record in records:
        fetcher_id = resolve_source_fetcher_id(record)
        if not fetcher_id:
            skipped_results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue
        params = build_source_fetch_params(record, body.params if body else {})
        items.append({"source_id": record.source_id, "fetcher_id": fetcher_id, "params": params})

    async def _work(bg) -> Dict[str, Any]:
        result = await _app().run_collection_items(
            items,
            name="临时抓取: 活跃网页数据源",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
        results = skipped_results + [
            {"source_id": item.get("source_id"), **item_result}
            for item, item_result in zip(items, result["results"])
        ]
        return {**result, "results": results}

    bg_job = jobs.launch(deps.get_db_sink().engine, "fetch_active_web", _work,
                         payload={"count": len(items)})
    return {"status": "accepted", "job_id": bg_job.id}
