"""Agent 收录 Router(issue #262 波 1)。

- ``POST /api/public/ingest/v1/items``:收录令牌(``Authorization: Bearer ding_…``)推送一批条目;
  ``/api/public/*`` 免会话、按令牌鉴权(与订阅 / 聚合令牌同一门控)。
- ``/api/admin/ingest/*``:令牌签发 / 吊销、收录源列表与「列入 / 下架 / 恢复 / 改名」;管理前缀,
  仅管理员,写操作入审计。
"""

from __future__ import annotations

import datetime as dt
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
from services import agent_ingest
from services import source_catalog, source_visibility, sync_consumer_policy
from storage.impl import db_storage


router = APIRouter(tags=["agent-ingest"])
_logger = logging.getLogger("dorami.agent_ingest")


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
    items: List[IngestItem] = PydanticField(default_factory=list, max_length=agent_ingest.MAX_BATCH)


def _authenticate(request: Request, session: Session) -> IngestTokenRecord:
    token = request.headers.get("authorization", "")
    raw = token[7:].strip() if token.lower().startswith("bearer ") else read_bearer_or_query_token(request)
    record = agent_ingest.authenticate(session, raw)
    if record is None:
        raise HTTPException(status_code=401, detail="收录令牌无效或已吊销")
    return record


def _social_source(item: IngestItem) -> dict[str, Any]:
    source = item.source.model_dump()
    # 社交帖的出处默认就是作者:没给 source 时用 author 填。
    if not (source.get("source_id") or source.get("handle") or source.get("user_id")):
        source.update({
            "platform": item.platform or "x",
            "handle": item.author.handle,
            "user_id": item.author.user_id,
            "name": source.get("name") or item.author.name,
            "avatar_url": source.get("avatar_url") or item.author.avatar_url,
        })
    source.setdefault("platform", item.platform or "x")
    if not source.get("platform"):
        source["platform"] = item.platform or "x"
    return source


@router.post("/api/public/ingest/v1/items")
async def push_items(batch: IngestBatch, request: Request):
    """Agent 推送一批条目(≤ 100);逐条返回 created / duplicate / duplicate_url / refreshed / rejected。"""
    db_sink = deps.get_db_sink()
    results: list[dict[str, Any]] = []
    created_ids: list[str] = []
    with Session(db_sink.engine) as session:
        token = _authenticate(request, session)
        if sync_consumer_policy.v2_consumer_mode_active(session):
            raise HTTPException(status_code=409, detail="接收端节点不接受推送,请推送到外网 authority")
        actor = token.actor
        remaining = max(0, int(token.daily_limit or 0) - agent_ingest.picks_today(session, actor))
        ctx = agent_ingest.new_context(session, actor)
        prepared: list[tuple[int, IngestItem, Optional[agent_ingest.ResolvedSource], Optional[str]]] = []
        for index, item in enumerate(batch.items):
            if index >= remaining:
                prepared.append((index, item, None, "超出今日收录额度"))
                continue
            source = _social_source(item) if item.kind == "social_post" else item.source.model_dump()
            try:
                resolved = agent_ingest.resolve_source(ctx, item.kind, source, item_url=item.url)
                prepared.append((index, item, resolved, None))
            except agent_ingest.IngestRejected as exc:
                prepared.append((index, item, None, str(exc)))
        token.last_used_at = agent_ingest._now()
        session.add(token)
        session.commit()

    for index, item, resolved, reason in prepared:
        base = {"index": index, "kind": item.kind}
        if resolved is None:
            results.append({**base, "status": "rejected", "reason": reason})
            continue
        try:
            payload = item.model_dump()
            with Session(db_sink.engine) as session:
                id_prefix = agent_ingest.item_id_prefix(session, resolved.source_id)
            content = agent_ingest.build_content(
                item.kind, resolved.source_id, payload, id_prefix=id_prefix
            )
        except (agent_ingest.IngestRejected, ValueError, TypeError, OverflowError) as exc:
            # 单条载荷问题只拒收这一条;整批照常回执、末尾审计照常执行(实现检视 R1 #12)。
            results.append({**base, "status": "rejected", "reason": str(exc), "source_id": resolved.source_id})
            continue
        content._ingest_actor = actor
        if resolved.created:
            hint = dict(resolved.hint)
            hint.pop("podcast_feed_url", None)
            content._source_hint = hint
        with Session(db_sink.engine) as session:
            existing = session.get(ArticleRecord, content.id)
            duplicate = None
            # 链接即身份只对文章与社交帖成立;播客单集常共用节目页链接,只认单集身份(guid / 单集
            # 链接 → 条目 id),否则会把同节目的不同单集并成一条(检视复检新 P1)。
            if existing is None and item.kind != "podcast_episode":
                duplicate = agent_ingest.existing_article_by_url(session, content.source_url)
            if duplicate is not None:
                # 同一链接已在库里:别的源 → duplicate_url;同一个源但条目 id 公式不同(抓取器按
                # guid、推送按链接等)→ duplicate。都只记收录,不新建(实现检视 R1 #6)。
                agent_ingest.record_pick(session, duplicate.id, actor, item.note)
                session.commit()
                results.append({
                    **base,
                    "status": "duplicate_url" if duplicate.source_id != resolved.source_id else "duplicate",
                    "id": duplicate.id,
                    "source_id": duplicate.source_id,
                })
                continue
            if item.kind == "podcast_episode" and resolved.created:
                agent_ingest.ensure_podcast_source(session, resolved)
                session.commit()
        outcome = await db_sink.save_with_outcome(content)
        with Session(db_sink.engine) as session:
            if outcome in db_storage.SAVE_STORED_OUTCOMES or outcome in {
                db_storage.SAVE_DUPLICATE, db_storage.SAVE_REFRESHED,
            }:
                agent_ingest.record_pick(session, content.id, actor, item.note)
                session.commit()
        if outcome == db_storage.SAVE_CREATED:
            created_ids.append(content.id)
            status = "created"
        elif outcome in {db_storage.SAVE_CONTENT_FILLED, db_storage.SAVE_METADATA_REPAIRED,
                         db_storage.SAVE_REFRESHED}:
            status = "refreshed"
        elif outcome == db_storage.SAVE_DUPLICATE:
            status = "duplicate"
        else:
            _code, detail = agent_ingest.ingest_policy.outcome_error(outcome) or (400, outcome)
            results.append({**base, "status": "rejected", "reason": detail, "source_id": resolved.source_id})
            continue
        results.append({
            **base, "status": status, "id": content.id, "source_id": resolved.source_id,
            "source_created": bool(resolved.created and status == "created"),
        })

    if created_ids:
        try:
            _app().queue_article_analysis_after_commit(created_ids)
            _app().schedule_media_prefetch(created_ids)
        except Exception:  # noqa: BLE001 - 入库已提交;分析补偿扫描与按需缓存兜底
            _logger.warning("推送入库后的分析入队 / 媒体预取失败", exc_info=True)
    body = agent_ingest.audit_body(results, actor=actor)
    try:
        admin_audit_service.record_audit(
            db_sink.engine,
            username=f"ingest:{actor}",
            method="POST",
            path="/api/public/ingest/v1/items",
            status_code=200,
            body=body,
        )
    except Exception:  # noqa: BLE001
        _logger.debug("收录审计写入失败", exc_info=True)
    return {
        "status": "success",
        "actor": actor,
        "counts": agent_ingest.summarize(results),
        "results": results,
    }


# ==================== 管理面 ====================


class TokenCreate(BaseModel):
    actor: str
    daily_limit: int = agent_ingest.DEFAULT_DAILY_LIMIT


class IngestSourceUpdate(BaseModel):
    name: Optional[str] = None


@router.get("/api/admin/ingest/tokens")
def list_tokens(session: Session = Depends(deps.get_session)):
    rows = session.exec(select(IngestTokenRecord).order_by(IngestTokenRecord.id.desc())).all()
    return {"items": [agent_ingest.serialize_token(row) for row in rows]}


@router.post("/api/admin/ingest/tokens")
def create_token(params: TokenCreate, request: Request, session: Session = Depends(deps.get_session)):
    if sync_consumer_policy.v2_consumer_mode_active(session):
        raise HTTPException(status_code=409, detail="接收端节点不签发收录令牌")
    try:
        record, token = agent_ingest.create_token(
            session,
            actor=params.actor,
            created_by=_app().current_username(request) or "",
            daily_limit=params.daily_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 明文只在签发这一次回显;库里只有 hash。
    return {**agent_ingest.serialize_token(record), "token": token}


@router.post("/api/admin/ingest/tokens/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(deps.get_session)):
    record = agent_ingest.revoke_token(session, token_id)
    if record is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    return agent_ingest.serialize_token(record)


def _ingest_source_rows(session: Session) -> list[SourceConfigRecord]:
    return list(session.exec(
        select(SourceConfigRecord)
        .where(
            (SourceConfigRecord.origin == source_catalog.ORIGIN_AGENT)
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
        row.origin == source_catalog.ORIGIN_AGENT
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
        row.updated_at = agent_ingest._now()
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
        row.updated_at = agent_ingest._now()
        session.add(row)
        session.commit()
    return {"status": "success", "source_id": source_id, "name": row.name}
