"""收录推送 Router(issue #262 波 1)。

- ``POST /api/public/ingest/v1/items``:收录令牌(``Authorization: Bearer ding_…``)推送一批条目,
  或管理员会话做「单条收录」(通道 ``manual``);``/api/public/*`` 免会话、按令牌鉴权(与订阅 /
  聚合令牌同一门控),会话由本路由自己核对。批处理主循环在 ``services/ingest_batch.py``。
- ``/api/admin/ingest/*``:令牌签发 / 吊销、收录源列表与「列入 / 下架 / 恢复 / 改名」;管理前缀,
  仅管理员,写操作入审计。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import func
from sqlmodel import Session, select

from api import deps
from api.tokens import read_bearer_or_query_token
from models.db import ArticlePickRecord, ArticleRecord, IngestTokenRecord, SourceConfigRecord
from services import admin_audit as admin_audit_service
from services import ingest_batch, ingest_push
from services import source_catalog, source_visibility, sync_consumer_policy


router = APIRouter(tags=["ingest-push"])
_logger = logging.getLogger("dorami.ingest_push")


def _app():
    return importlib.import_module("api.app")


# ==================== 推送 ====================


class IngestSource(BaseModel):
    source_id: str = ""
    platform: str = ""
    handle: str = ""
    user_id: str = ""
    name: str = ""
    avatar_url: str = ""
    url: str = ""
    site: str = ""
    feed_url: str = ""


class IngestAuthor(BaseModel):
    name: str = ""
    handle: str = ""
    user_id: str = ""
    avatar_url: str = ""


class IngestItem(BaseModel):
    kind: Literal["article", "social_post", "podcast_episode"]
    source: IngestSource = PydanticField(default_factory=IngestSource)
    url: str = ""
    guid: str = ""
    title: str = ""
    content: str = ""
    summary: str = ""
    publish_date: str = ""
    note: str = ""
    # social_post
    platform: str = "x"
    post_id: str = ""
    author: IngestAuthor = PydanticField(default_factory=IngestAuthor)
    # podcast_episode
    audio_url: str = ""
    audio_mime: str = ""
    duration_seconds: Optional[float] = None
    extensions: Dict[str, Any] = PydanticField(default_factory=dict)


class IngestBatch(BaseModel):
    items: List[IngestItem] = PydanticField(default_factory=list, max_length=ingest_push.MAX_BATCH)


def _caller(request: Request, session: Session) -> tuple[ingest_batch.IngestCaller, Optional[IngestTokenRecord]]:
    """收录令牌优先;没带令牌时认管理员会话(运维「单条收录」,通道 manual,不计令牌额度)。

    ``/api/public/*`` 在中间件里免会话,这里自己核对:只认 role=admin 的会话,游客与读者一律拒绝。
    会话 Cookie 是 SameSite=Lax,跨站 POST 带不上,与其它管理写接口同一水平。
    """
    header = request.headers.get("authorization", "")
    raw = header[7:].strip() if header.lower().startswith("bearer ") else read_bearer_or_query_token(request)
    if raw:
        record = ingest_push.authenticate(session, raw)
        if record is None:
            raise HTTPException(status_code=401, detail="收录令牌无效或已吊销")
        if not _app().runtime_collector_enabled():
            # 收录写的是归档:采集面关闭的节点(runtime role = reader)不接受,与旧入口经中间件的
            # collector 门控一致(检视 R1 #3)。
            raise HTTPException(status_code=403, detail="本节点未开启采集面,不接受收录")
        remaining = max(0, int(record.daily_limit or 0) - ingest_push.picks_today(session, record.actor))
        return ingest_batch.IngestCaller(actor=record.actor, remaining=remaining), record
    auth = _app().current_auth_session(request)
    if auth and auth.get("role") == "admin" and not _app().is_guest_session(auth):
        if not _app().collector_role_enabled(auth):
            raise HTTPException(status_code=403, detail="本节点未开启采集面,不接受收录")
        return ingest_batch.IngestCaller(
            actor=str(auth.get("sub") or ""), channel=ingest_batch.CHANNEL_MANUAL
        ), None
    if auth:
        raise HTTPException(status_code=403, detail="该操作需要管理员账号或收录令牌")
    raise HTTPException(status_code=401, detail="收录令牌无效或已吊销")


def _after_created(ids: list[str]) -> None:
    try:
        _app().queue_article_analysis_after_commit(ids)
        _app().schedule_media_prefetch(ids)
    except Exception:  # noqa: BLE001 - 入库已提交;分析补偿扫描与按需缓存兜底
        _logger.warning("收录入库后的分析入队 / 媒体预取失败", exc_info=True)


@router.post("/api/public/ingest/v1/items")
async def push_items(batch: IngestBatch, request: Request):
    """收录一批条目(≤ 100);逐条返回 created / duplicate / duplicate_url / duplicate_content / refreshed / rejected。"""
    db_sink = deps.get_db_sink()
    # 先解析会话再借连接:结果缓存在 request.state,免得持有连接时再借一条(current_auth_session 注释)。
    _app().current_auth_session(request)
    with Session(db_sink.engine) as session:
        caller, token = _caller(request, session)
        if sync_consumer_policy.v2_consumer_mode_active(session):
            raise HTTPException(status_code=409, detail="接收端节点不接受收录,请推送到外网 authority")
        if token is not None:
            token.last_used_at = ingest_push._now()
            session.add(token)
            session.commit()

    results = await ingest_batch.ingest(
        db_sink, [item.model_dump() for item in batch.items], caller, on_created=_after_created
    )
    body = ingest_push.audit_body(results, actor=caller.actor, channel=caller.channel)
    try:
        admin_audit_service.record_audit(
            db_sink.engine,
            # 令牌调用记在 ingest:<actor> 名下;管理员会话记在本人名下。
            username=f"ingest:{caller.actor}" if token is not None else caller.actor,
            method="POST",
            path="/api/public/ingest/v1/items",
            status_code=200,
            body=body,
        )
    except Exception:  # noqa: BLE001
        _logger.debug("收录审计写入失败", exc_info=True)
    return {
        "status": "success",
        "actor": caller.actor,
        "counts": ingest_push.summarize(results),
        "results": results,
    }


# ==================== 管理面 ====================


class TokenCreate(BaseModel):
    actor: str
    daily_limit: int = ingest_push.DEFAULT_DAILY_LIMIT


class IngestSourceUpdate(BaseModel):
    name: Optional[str] = None


@router.get("/api/admin/ingest/tokens")
def list_tokens(session: Session = Depends(deps.get_session)):
    rows = session.exec(select(IngestTokenRecord).order_by(IngestTokenRecord.id.desc())).all()
    return {"items": [ingest_push.serialize_token(row) for row in rows]}


@router.post("/api/admin/ingest/tokens")
def create_token(params: TokenCreate, request: Request, session: Session = Depends(deps.get_session)):
    if sync_consumer_policy.v2_consumer_mode_active(session):
        raise HTTPException(status_code=409, detail="接收端节点不签发收录令牌")
    try:
        record, token = ingest_push.create_token(
            session,
            actor=params.actor,
            created_by=_app().current_username(request) or "",
            daily_limit=params.daily_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 明文只在签发这一次回显;库里只有 hash。
    return {**ingest_push.serialize_token(record), "token": token}


@router.post("/api/admin/ingest/tokens/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(deps.get_session)):
    record = ingest_push.revoke_token(session, token_id)
    if record is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    return ingest_push.serialize_token(record)


def _ingest_source_rows(session: Session) -> list[SourceConfigRecord]:
    return list(session.exec(
        select(SourceConfigRecord)
        .where(
            (SourceConfigRecord.origin == source_catalog.ORIGIN_INGEST)
            | (SourceConfigRecord.admission_status == source_catalog.ADMISSION_PROVISIONAL)
        )
        .order_by(SourceConfigRecord.created_at.desc())
    ).all())


@router.get("/api/admin/ingest/sources")
def list_ingest_sources(session: Session = Depends(deps.get_session)):
    """收录源:注入通道建的源与仍在临时状态的源;条目数、最近收录、收录者。"""
    rows = _ingest_source_rows(session)
    ids = [row.source_id for row in rows]
    counts: dict[str, tuple[int, str]] = {}
    actors: dict[str, set[str]] = {}
    if ids:
        for sid, count, last in session.exec(
            select(ArticleRecord.source_id, func.count(ArticleRecord.id), func.max(ArticleRecord.fetched_date))
            .where(ArticleRecord.source_id.in_(ids))
            .group_by(ArticleRecord.source_id)
        ).all():
            counts[sid] = (int(count or 0), str(last or ""))
        for sid, actor in session.exec(
            select(ArticleRecord.source_id, ArticlePickRecord.actor)
            .join(ArticlePickRecord, ArticlePickRecord.article_id == ArticleRecord.id)
            .where(ArticleRecord.source_id.in_(ids))
            .distinct()
        ).all():
            actors.setdefault(sid, set()).add(actor)
    hidden = source_visibility.hidden_source_ids(session)
    items = []
    for row in rows:
        view = source_catalog.view_from_row(row)
        count, last = counts.get(row.source_id, (0, ""))
        items.append({
            "source_id": row.source_id,
            "name": view.name,
            "shape": view.shape,
            "platform": view.platform,
            "avatar_url": view.avatar_url,
            "homepage_url": view.homepage_url,
            "admission_status": view.admission_status,
            "origin": view.origin,
            "hidden": row.source_id in hidden,
            "retired": view.retired,
            "count": count,
            "last_fetched": last,
            "actors": sorted(actors.get(row.source_id, set())),
            "created_at": row.created_at,
        })
    return {"items": items}


def _ingest_source_or_404(session: Session, source_id: str) -> SourceConfigRecord:
    row = session.get(SourceConfigRecord, source_id)
    if row is None or not (
        row.origin == source_catalog.ORIGIN_INGEST
        or row.admission_status == source_catalog.ADMISSION_PROVISIONAL
    ):
        raise HTTPException(status_code=404, detail="收录源不存在")
    if (row.collection_authority_id or "").strip() or sync_consumer_policy.v2_consumer_mode_active(session):
        raise HTTPException(status_code=409, detail="该来源由远端权威节点管理")
    return row


@router.post("/api/admin/ingest/sources/{source_id}/admit")
def admit_ingest_source(source_id: str, session: Session = Depends(deps.get_session)):
    """列入:临时源 → 观察期,进入发现目录、参与多源印证与日报候选。"""
    row = _ingest_source_or_404(session, source_id)
    if row.admission_status == source_catalog.ADMISSION_PROVISIONAL:
        row.admission_status = "incubating"
        row.updated_at = ingest_push._now()
        session.add(row)
        session.commit()
    return {"status": "success", "source_id": source_id, "admission_status": row.admission_status}


@router.post("/api/admin/ingest/sources/{source_id}/hide")
def hide_ingest_source(source_id: str, session: Session = Depends(deps.get_session)):
    """下架:读者面隐藏(交付全量排除)并拒收后续推送。"""
    _ingest_source_or_404(session, source_id)
    source_visibility.set_source_hidden(session, source_id, True)
    return {"status": "success", "source_id": source_id, "hidden": True}


@router.post("/api/admin/ingest/sources/{source_id}/unhide")
def unhide_ingest_source(source_id: str, session: Session = Depends(deps.get_session)):
    _ingest_source_or_404(session, source_id)
    source_visibility.set_source_hidden(session, source_id, False)
    return {"status": "success", "source_id": source_id, "hidden": False}


@router.patch("/api/admin/ingest/sources/{source_id}")
def update_ingest_source(
    source_id: str, params: IngestSourceUpdate, session: Session = Depends(deps.get_session)
):
    row = _ingest_source_or_404(session, source_id)
    if params.name is not None:
        name = params.name.strip()[:120]
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        row.name = name
        row.updated_at = ingest_push._now()
        session.add(row)
        session.commit()
    return {"status": "success", "source_id": source_id, "name": row.name}
