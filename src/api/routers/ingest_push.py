"""收录推送 Router(issue #262 波 1)。

- ``POST /api/public/ingest/v1/items``:收录令牌(``Authorization: Bearer ding_…``)推送一批条目,
  或管理员会话做「单条收录」(通道 ``manual``);``/api/public/*`` 免会话、按令牌鉴权(与订阅 /
  聚合令牌同一门控),会话由本路由自己核对。批处理主循环在 ``services/ingest_batch.py``。
- ``POST /api/public/ingest/v1/sources``:有登记权限的收录令牌自己登记一个来源(issue #344 第二个 PR),
  登记的源先是待列入并绑定给这个令牌;只认令牌,管理员在 ``/api/admin/ingest/sources`` 登记。
- ``/api/admin/ingest/*``:令牌签发 / 吊销、推送绑定(令牌 ↔ 源)、登记不带抓取的源、收录源列表与
  「列入 / 下架 / 恢复 / 改名」;管理前缀,仅管理员,写操作入审计。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlalchemy import func
from sqlmodel import Session, select

from api import deps
from api.tokens import read_bearer_or_query_token
from models.db import ArticlePickRecord, ArticleRecord, IngestTokenRecord, SourceConfigRecord
from services import admin_audit as admin_audit_service
from services import ingest_batch, ingest_bindings, ingest_push, social_ingest
from services import source_catalog, source_visibility


router = APIRouter(tags=["ingest-push"])
_logger = logging.getLogger("dorami.ingest_push")


def _app():
    return importlib.import_module("api.app")


# ==================== 推送 ====================


class _Open(BaseModel):
    """推送载荷的模型:不认识的字段留在 ``model_extra`` 里,回执的 ``warnings`` 会点出来。

    默认的做法是静默丢弃——调用方把配图、引用帖放错位置时拿到的仍是 ``created``,无从知道
    (issue #341)。不改成拒收:已有调用方多带的字段不该让整批失败。
    """

    model_config = ConfigDict(extra="allow")


class IngestSource(_Open):
    source_id: str = ""
    platform: str = ""
    handle: str = ""
    user_id: str = ""
    name: str = ""
    avatar_url: str = ""
    url: str = ""
    site: str = ""
    feed_url: str = ""


class IngestAuthor(_Open):
    name: str = ""
    handle: str = ""
    user_id: str = ""
    avatar_url: str = ""


class IngestItem(_Open):
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
    # 下面五个是 issue #341 补的。类型故意不收紧:它们此前不在模型里、被整个忽略,旧调用方带着
    # 形状不对的值(字符串、null)也一直能推成功;收紧会让整批 422。形状由 social_ingest 逐条核对,
    # 不对的写进该条回执的 warnings。
    lang: Any = None
    media_urls: Any = None
    # 引用帖 / 被转推的原帖:用与外层帖子同样的字段描述(post_id / url / content / lang / author / media_urls)。
    quoted: Any = None
    reposted: Any = None
    # X API v2 的帖子对象 {"data": …, "includes": …}:给了就由服务端按抓取器同一套规则归一化。
    raw: Any = None
    # podcast_episode
    audio_url: str = ""
    audio_mime: str = ""
    duration_seconds: Optional[float] = None
    extensions: Dict[str, Any] = PydanticField(default_factory=dict)


class IngestBatch(BaseModel):
    items: List[IngestItem] = PydanticField(default_factory=list, max_length=ingest_push.MAX_BATCH)


def _token(request: Request, session: Session) -> Optional[IngestTokenRecord]:
    """请求带的收录令牌;没带返回 None,带了但无效 401,节点不收录 403。"""
    header = request.headers.get("authorization", "")
    raw = header[7:].strip() if header.lower().startswith("bearer ") else read_bearer_or_query_token(request)
    if not raw:
        return None
    record = ingest_push.authenticate(session, raw)
    if record is None:
        raise HTTPException(status_code=401, detail="收录令牌无效或已吊销")
    if not _app().runtime_collector_enabled():
        # 收录写的是归档:采集面关闭的节点(runtime role = reader)不接受,与旧入口经中间件的
        # collector 门控一致(检视 R1 #3)。
        raise HTTPException(status_code=403, detail="本节点未开启采集面,不接受收录")
    return record


def _caller(request: Request, session: Session) -> tuple[ingest_batch.IngestCaller, Optional[IngestTokenRecord]]:
    """收录令牌优先;没带令牌时认管理员会话(运维「单条收录」,通道 manual,不计令牌额度)。

    ``/api/public/*`` 在中间件里免会话,这里自己核对:只认 role=admin 的会话,游客与读者一律拒绝。
    会话 Cookie 是 SameSite=Lax,跨站 POST 带不上,与其它管理写接口同一水平。
    """
    record = _token(request, session)
    if record is not None:
        remaining = max(0, int(record.daily_limit or 0) - ingest_push.picks_today(session, record.actor))
        return ingest_batch.IngestCaller(
            actor=record.actor, remaining=remaining,
            # 令牌只能往它绑定的源推(issue #344);没有绑定就是空集,一条也推不进来。
            token_id=record.id, bound=ingest_bindings.bound_source_ids(session, record.id),
            can_register=bool(record.can_register_sources),
        ), record
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


_KIND_LABEL = {"article": "文章", "social_post": "社交帖", "podcast_episode": "播客单集"}


def _strip_extras(model: BaseModel, path: str, unknown: list[str]) -> dict[str, Any]:
    """模型 → 只含已声明字段的字典;不认识的字段名(带路径)记进 ``unknown``。"""
    unknown.extend(f"{path}{name}" for name in (model.model_extra or {}))
    out: dict[str, Any] = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        out[name] = _strip_extras(value, f"{path}{name}.", unknown) if isinstance(value, BaseModel) else value
    return out


def _payload(item: IngestItem) -> tuple[dict[str, Any], list[str]]:
    """一条载荷 →(交给批处理的字典,回执里的提示)。

    提示两类:模型不认识的字段;认识、但这一类条目不用的字段(给文章带 ``media_urls`` 之类)。
    """
    unknown: list[str] = []
    payload = _strip_extras(item, "", unknown)
    if item.kind == "social_post":
        unknown.extend(social_ingest.unknown_reference_fields(payload))
    used = ingest_push.FIELDS_BY_KIND[item.kind]
    unused = [
        name for name in type(item).model_fields
        if name in item.model_fields_set and name not in used and _has_value(getattr(item, name))
    ]
    warnings: list[str] = []
    if unknown:
        warnings.append(f"未识别的字段:{'、'.join(unknown)}(已忽略)")
    if unused:
        warnings.append(f"{_KIND_LABEL[item.kind]}不使用的字段:{'、'.join(unused)}(已忽略)")
    return payload, warnings


def _has_value(value: Any) -> bool:
    if isinstance(value, BaseModel):
        return bool(value.model_dump(exclude_defaults=True))
    return value not in (None, "", [], {})


def _after_stored(ids: list[str]) -> None:
    """新建与被补全的条目:分析入队 + 配图预取。两者对已处理过的条目都是空操作。"""
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
        if token is not None:
            token.last_used_at = ingest_push._now()
            session.add(token)
            session.commit()

    payloads = [_payload(item) for item in batch.items]
    results = await ingest_batch.ingest(
        db_sink,
        [payload for payload, _ in payloads],
        caller,
        on_stored=_after_stored,
        warnings=[notes for _, notes in payloads],
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


# ==================== 令牌自己登记来源(issue #344 第二个 PR) ====================


class IngestSourceSelfRegister(_Open):
    """字段与管理员登记(``IngestSourceRegister``)相同,没有 ``listed`` 与 ``token_id``:
    令牌登记的源一律先是待列入,并绑定给它自己。"""

    kind: str = ""
    name: str = ""
    handle: str = ""
    user_id: str = ""
    url: str = ""
    feed_url: str = ""
    source_id_suffix: str = ""
    avatar_url: str = ""
    homepage_url: str = ""
    description: str = ""


def _source_payload(session: Session, row: SourceConfigRecord) -> dict[str, Any]:
    view = source_catalog.get(session, row.source_id)
    return {
        "status": "success",
        "source_id": row.source_id,
        "name": view.name,
        "shape": view.shape,
        "admission_status": view.admission_status,
        "open": row.source_id.startswith(ingest_push.OPEN_SOURCE_PREFIX),
    }


# 路径写字面量:前端的接口对账测试按字面量读路由(frontend/test/feedEndpoints.test.mjs);
# 与 ``ingest_push.REGISTER_PATH``(拒收提示与审计用)是同一个地址。
@router.post("/api/public/ingest/v1/sources")
def register_source_by_token(
    params: IngestSourceSelfRegister, request: Request, session: Session = Depends(deps.get_session)
):
    """令牌登记一个来源:待列入、自动绑定给它;出处已有来源回 409 并给出那个源的 id。"""
    token = _token(request, session)
    if token is None:
        # 只认收录令牌:管理员登记走管理面接口(可以直接列入、可以绑给任意令牌)。
        raise HTTPException(status_code=401, detail="这个接口只接受收录令牌")
    unknown: list[str] = []
    fields = _strip_extras(params, "", unknown)
    actor = token.actor
    try:
        row = ingest_bindings.register_by_token(session, token, **fields)
    except ingest_bindings.BindingError as exc:
        session.rollback()
        detail: Any = str(exc)
        if exc.existing_source_id:
            detail = {"message": str(exc), "source_id": exc.existing_source_id, "bound": bool(exc.bound)}
        raise HTTPException(status_code=exc.status, detail=detail)
    session.commit()
    session.refresh(token)
    body = {
        **_source_payload(session, row),
        "bound": True,
        "registered_today": ingest_push.registered_today(token),
        "daily_limit": ingest_push.REGISTER_DAILY_LIMIT,
    }
    if unknown:
        body["warnings"] = [f"未识别的字段:{'、'.join(unknown)}(已忽略)"]
    try:
        admin_audit_service.record_audit(
            deps.get_db_sink().engine,
            username=f"ingest:{actor}",
            method="POST",
            path=ingest_push.REGISTER_PATH,
            status_code=200,
            body={"actor": actor, "source_id": row.source_id, "name": body["name"], "kind": fields.get("kind", "")},
        )
    except Exception:  # noqa: BLE001
        _logger.debug("登记来源的审计写入失败", exc_info=True)
    return body


# ==================== 管理面 ====================


class TokenCreate(BaseModel):
    actor: str
    daily_limit: int = ingest_push.DEFAULT_DAILY_LIMIT
    source_ids: List[str] = PydanticField(default_factory=list, max_length=200)
    can_register_sources: bool = False


class TokenUpdate(BaseModel):
    can_register_sources: Optional[bool] = None


class BindingCreate(BaseModel):
    source_id: str


class IngestSourceUpdate(BaseModel):
    name: Optional[str] = None


class IngestSourceRegister(BaseModel):
    kind: str
    name: str
    handle: str = ""
    user_id: str = ""
    url: str = ""
    feed_url: str = ""
    source_id_suffix: str = ""
    avatar_url: str = ""
    homepage_url: str = ""
    description: str = ""
    listed: bool = True
    token_id: Optional[int] = None


def _binding_error(exc: ingest_bindings.BindingError) -> HTTPException:
    detail: Any = str(exc)
    if exc.existing_source_id:
        detail = {"message": str(exc), "source_id": exc.existing_source_id}
    return HTTPException(status_code=exc.status, detail=detail)


def _token_payload(record: IngestTokenRecord, bindings: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    return {**ingest_push.serialize_token(record), "bindings": bindings.get(record.id, [])}


@router.get("/api/admin/ingest/tokens")
def list_tokens(session: Session = Depends(deps.get_session)):
    rows = session.exec(select(IngestTokenRecord).order_by(IngestTokenRecord.id.desc())).all()
    bindings = ingest_bindings.by_token(session)
    return {"items": [_token_payload(row, bindings) for row in rows]}


@router.post("/api/admin/ingest/tokens")
def create_token(params: TokenCreate, request: Request, session: Session = Depends(deps.get_session)):
    created_by = _app().current_username(request) or ""
    source_ids = list(dict.fromkeys(sid.strip() for sid in params.source_ids if sid.strip()))
    # 签发与绑定同一事务:有一个源绑不了就整体回滚,不留下「拿不到明文、又没有绑定」的令牌(检视 R1 #6)。
    try:
        record, token = ingest_push.create_token(
            session,
            actor=params.actor,
            created_by=created_by,
            daily_limit=params.daily_limit,
            can_register_sources=params.can_register_sources,
            commit=False,
        )
        for source_id in source_ids:
            try:
                ingest_bindings.bind(session, record.id, source_id, created_by=created_by)
            except ingest_bindings.BindingError as exc:
                raise HTTPException(status_code=exc.status, detail=f"{source_id}:{exc}")
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    session.commit()
    session.refresh(record)
    # 明文只在签发这一次回显;库里只有 hash。
    return {**_token_payload(record, ingest_bindings.by_token(session)), "token": token}


@router.post("/api/admin/ingest/tokens/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(deps.get_session)):
    record = ingest_push.revoke_token(session, token_id)
    if record is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    return _token_payload(record, ingest_bindings.by_token(session))


@router.patch("/api/admin/ingest/tokens/{token_id}")
def update_token(token_id: int, params: TokenUpdate, session: Session = Depends(deps.get_session)):
    """开 / 关这个令牌「可以自己登记来源」的权限。"""
    record = session.get(IngestTokenRecord, token_id)
    if record is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    if params.can_register_sources is not None:
        try:
            record = ingest_push.set_register_permission(session, token_id, params.can_register_sources)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return _token_payload(record, ingest_bindings.by_token(session))


# ── 推送绑定(issue #344):令牌只能往它绑定的源推 ──


@router.post("/api/admin/ingest/tokens/{token_id}/bindings")
def bind_source(
    token_id: int, params: BindingCreate, request: Request, session: Session = Depends(deps.get_session)
):
    try:
        ingest_bindings.bind(
            session, token_id, params.source_id, created_by=_app().current_username(request) or ""
        )
    except ingest_bindings.BindingError as exc:
        raise _binding_error(exc)
    session.commit()
    return _token_payload(session.get(IngestTokenRecord, token_id), ingest_bindings.by_token(session))


@router.delete("/api/admin/ingest/tokens/{token_id}/bindings/{source_id}")
def unbind_source(token_id: int, source_id: str, session: Session = Depends(deps.get_session)):
    record = session.get(IngestTokenRecord, token_id)
    if record is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    if not ingest_bindings.unbind(session, token_id, source_id):
        raise HTTPException(status_code=404, detail="这个令牌没有绑定该来源")
    session.commit()
    return _token_payload(record, ingest_bindings.by_token(session))


@router.post("/api/admin/ingest/sources")
def register_ingest_source(
    params: IngestSourceRegister, request: Request, session: Session = Depends(deps.get_session)
):
    """登记一个不带抓取的源:给了账号 / 站点 / feed 的是定向源,不给的是合集源(``push_<后缀>``)。"""
    try:
        row = ingest_bindings.register_source(
            session,
            kind=params.kind, name=params.name,
            handle=params.handle, user_id=params.user_id, url=params.url, feed_url=params.feed_url,
            source_id_suffix=params.source_id_suffix,
            avatar_url=params.avatar_url, homepage_url=params.homepage_url, description=params.description,
            listed=params.listed,
        )
        if row is None:
            raise ingest_bindings.BindingError("登记失败:这个 id 不能用作来源")
        if params.token_id is not None:
            ingest_bindings.bind(
                session, params.token_id, row.source_id, created_by=_app().current_username(request) or ""
            )
    except ingest_bindings.BindingError as exc:
        session.rollback()
        raise _binding_error(exc)
    session.commit()
    return _source_payload(session, row)


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
