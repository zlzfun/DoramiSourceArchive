"""数据源配置 Router（collector）：用户自定义来源的 CRUD + 触发抓取。

阶段1 从 app.py 迁出的 /api/source-configs* 端点（路径不变，collector 网关仍由中间件
统一强制）：列表/详情/创建/更新/启停/删除 + 单源触发 + 批量触发活跃 RSS/Web 源；
Podcast wave 增加精选目录查询与安全幂等导入。

配置序列化留在本 Router；source_type→fetcher 与参数绑定由
``services.collection_nodes`` 统一提供，并经本模块继续 re-export 以兼容既有调用方。

采集核心 run_single_fetch_as_collection / run_collection_items 仍留守 app.py（与
抓取追踪 + APScheduler 编排同源），经 _app() 延迟动态调用。数据访问经
deps.get_session()。
"""

import importlib
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from api.sources import configured_source_platform, configured_source_shape
from api.textutils import _json_dumps, _now_iso
from models.db import SourceConfigRecord
from services import jobs
from services import article_analysis as article_analysis_service
from services import podcast_catalog as podcast_catalog_service
from services import user_sources as user_sources_service
from services import sync_consumer_policy
from services.collection_nodes import (
    build_source_fetch_params,
    is_public_podcast_source,
    parse_json_object,
    resolve_source_fetcher_id,
)

router = APIRouter(tags=["source-configs"])


def _app():
    """延迟取 api.app（避免导入环；动态调用留守的采集核心 run_*_collection*）。"""
    return importlib.import_module("api.app")


def _require_local_source_governance(
    session: Session,
    record: SourceConfigRecord | None = None,
) -> None:
    """Block public-source mutations as soon as this node becomes a v2 receiver."""

    if record is None:
        if sync_consumer_policy.v2_consumer_mode_active(session):
            raise HTTPException(
                status_code=409,
                detail="v2 接收端不能创建本地公共数据源；请使用用户自定义 RSS",
            )
        return
    if not sync_consumer_policy.local_source_operation_allowed(
        session,
        record.source_id,
        operation="governance",
    ):
        raise HTTPException(
            status_code=409,
            detail="数据源由远端权威节点管理，本机不能修改、删除或抓取",
        )


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
    ai_analysis_enabled: bool = True
    is_active: bool = True
    fetch_interval_minutes: Optional[int] = None
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
    ai_analysis_enabled: Optional[bool] = None
    is_active: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


class SourceFetchParams(BaseModel):
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class PodcastCatalogImportParams(BaseModel):
    source_ids: List[str] = PydanticField(default_factory=list)
    update_existing: bool = False


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


@router.get("/api/source-configs/podcast-catalog")
def get_podcast_catalog(session: Session = Depends(deps.get_session)):
    """Return the reviewed podcast catalog with its current install state."""
    return podcast_catalog_service.list_podcast_catalog(session)


@router.post("/api/source-configs/podcast-catalog/import")
def import_podcast_catalog(
        params: PodcastCatalogImportParams,
        session: Session = Depends(deps.get_session),
):
    """Import catalog sources without overwriting local metadata by default."""
    _require_local_source_governance(session)
    try:
        result = podcast_catalog_service.import_podcast_catalog(
            session,
            source_ids=params.source_ids,
            update_existing=params.update_existing,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/source-configs/{source_id}")
def get_source_config(source_id: str, session: Session = Depends(deps.get_session)):
    source_id = normalize_source_id(source_id)
    record = session.get(SourceConfigRecord, source_id)
    if not record:
        raise HTTPException(status_code=404, detail="数据源配置不存在")
    return serialize_source_config(record)


@router.post("/api/source-configs")
def create_source_config(params: SourceConfigCreate, session: Session = Depends(deps.get_session)):
    _require_local_source_governance(session)
    source_id = normalize_source_id(params.source_id)
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")

    existing = session.get(SourceConfigRecord, source_id)
    if existing:
        raise HTTPException(status_code=400, detail="该 source_id 已存在")

    now = _now_iso()
    source_type = params.source_type.strip().lower() or "rss"
    record = SourceConfigRecord(
        source_id=source_id,
        name=params.name.strip(),
        source_type=source_type,
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
        ai_analysis_enabled=params.ai_analysis_enabled,
        is_active=True if source_type in {"podcast", "podcast_rss"} else params.is_active,
        fetch_interval_minutes=(
            None
            if source_type in {"podcast", "podcast_rss"}
            else params.fetch_interval_minutes
        ),
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
    _require_local_source_governance(session, record)
    update_data = params.model_dump(exclude_unset=True)
    retired_public_podcast_fields = {"is_active", "fetch_interval_minutes"} & update_data.keys()
    if is_public_podcast_source(record) and retired_public_podcast_fields:
        raise HTTPException(
            status_code=400,
            detail="公共 Podcast 不支持源级启停或抓取间隔，请在采集任务中控制运行节奏",
        )
    previous_type = (record.source_type or "").strip().lower()
    requested_type = str(update_data.get("source_type", previous_type) or "").strip().lower()
    if (
        previous_type in {"podcast", "podcast_rss"}
        and requested_type not in {"podcast", "podcast_rss"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Podcast 数据源不能直接改为其他类型；请创建新的数据源身份",
        )
    if (
        requested_type in {"podcast", "podcast_rss"}
        and previous_type not in {"podcast", "podcast_rss", "rss", "atom"}
    ):
        raise HTTPException(
            status_code=409,
            detail="仅 RSS/Atom 数据源可转换为 Podcast；其他类型请创建新的数据源身份",
        )
    if (
        record.owner_username
        and update_data.get("ai_analysis_enabled") is True
        and user_sources_service.source_is_credentialed(record)
    ):
        raise HTTPException(
            status_code=400,
            detail="含凭证的自定源默认不发送到 MaaS",
        )
    for key, value in update_data.items():
        if key == "params":
            record.params_json = _json_dumps(value)
        elif key == "content_tags":
            record.content_tags_json = json.dumps(value or [], ensure_ascii=False)
        elif isinstance(value, str):
            setattr(record, key, value.strip())
        else:
            setattr(record, key, value)

    if requested_type in {"podcast", "podcast_rss"}:
        record.source_type = "podcast"
        if not record.owner_username:
            record.is_active = True
            record.fetch_interval_minutes = None
        session.add(record)

    if user_sources_service.source_is_credentialed(record):
        # Classification changes are a privacy boundary, not just a future-run
        # switch: stop analysis and retract any previously public Candidate
        # evidence in the same transaction.
        record.ai_analysis_enabled = False
        article_analysis_service.purge_source_candidate_evidence(
            session,
            record.source_id,
        )

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
    _require_local_source_governance(session, record)
    if is_public_podcast_source(record):
        raise HTTPException(
            status_code=400,
            detail="公共 Podcast 节点始终可采集，请在采集任务中控制运行节奏",
        )
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
    _require_local_source_governance(session, record)
    if record.owner_username:
        # 用户自定源分流(v3.40 检视返修 F8):通用删除只删配置行会留下文章与订阅
        # 孤儿——改走专用强删路径(级联清订阅/水位/文章/分享,与 admin 面同语义)。
        from services import user_sources as user_sources_service

        result = user_sources_service.admin_delete_user_source(session, source_id)
        return {"status": "success", **result}
    # Preserve the established product distinction: toggle is a reversible
    # soft stop, while DELETE physically removes the source configuration.
    # Archive Sync emits a source tombstone from this transaction.
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
    if not record.is_active and not is_public_podcast_source(record):
        raise HTTPException(status_code=400, detail="数据源已停用，无法触发抓取")
    if not sync_consumer_policy.local_source_operation_allowed(
        session, record.source_id, operation="collection"
    ):
        raise HTTPException(
            status_code=409,
            detail="该数据源由远端权威节点采集，本机仅同步使用",
        )
    if not resolve_source_fetcher_id(record):
        raise HTTPException(status_code=400, detail="该数据源未绑定可用抓取器")

    try:
        result = await _app().run_single_fetch_as_collection(
            source_id,
            body.params if body else {},
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
        .where(SourceConfigRecord.collection_authority_id == "")
        .where(SourceConfigRecord.source_type.in_(["rss", "atom"]))
        .order_by(SourceConfigRecord.name)
    ).all()

    items = []
    skipped_results = []
    for record in records:
        if not sync_consumer_policy.local_source_operation_allowed(
            session, record.source_id, operation="collection"
        ):
            continue
        if not resolve_source_fetcher_id(record):
            skipped_results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue
        items.append({
            "source_id": record.source_id,
            "fetcher_id": record.source_id,
            "params": body.params if body else {},
        })

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
        .where(SourceConfigRecord.collection_authority_id == "")
        .where(SourceConfigRecord.source_type.in_(["web", "webpage"]))
        .order_by(SourceConfigRecord.name)
    ).all()

    items = []
    skipped_results = []
    for record in records:
        if not sync_consumer_policy.local_source_operation_allowed(
            session, record.source_id, operation="collection"
        ):
            continue
        if not resolve_source_fetcher_id(record):
            skipped_results.append({"source_id": record.source_id, "status": "skipped", "error": "未绑定可用抓取器"})
            continue
        items.append({
            "source_id": record.source_id,
            "fetcher_id": record.source_id,
            "params": body.params if body else {},
        })

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
