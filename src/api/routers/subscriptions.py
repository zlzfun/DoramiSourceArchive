"""订阅与个人聚合 feed 的对外 Router（reader 面）。

阶段1 从 app.py 迁出的订阅分发端点（路径不变，reader 网关仍由中间件统一强制）：
- /api/subscriptions/* —— 订阅生命周期（owner 作用域 CRUD + 轮换令牌）；
- /api/public/subscriptions/{id}/articles|vector/search —— 单订阅令牌拉取/全文检索(v3.31 起 FTS5 芯)；
- /api/public/feed/articles[.md] —— 个人聚合令牌一次性拉取全部订阅来源。

数据访问经 Depends(deps.get_session)/deps.get_db_sink()；查询/令牌/序列化复用
api.feed_service、api.tokens、api.articles_view 等共享模块；current_username 与
全文检索与 MCP search_articles 同源（mcp_server._search_articles_impl,FTS5）。
"""

import datetime
import importlib
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from api import deps
from api.articles_view import article_to_markdown, serialize_feed_article
from api.feed_service import (
    feed_articles_for_owner,
    query_subscription_articles,
    resolve_feed_token_owner,
    resolve_subscription_by_token,
    serialize_subscription,
)
from api.sources import _friendly_source_name, _registry_source_meta, subscription_source_ids
from api.textutils import _json_dumps, _json_loads, _model_dump, _model_to_clean_dict, _now_iso
from api.tokens import (
    generate_subscription_token,
    hash_subscription_token,
    normalize_delivery_policy,
    read_bearer_or_query_token,
    subscription_token_preview,
)
from models.db import ReaderSubscriptionRecord

router = APIRouter(tags=["subscriptions"])

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
PUBLIC_FEED_EXPORT_LIMIT = 200
ET.register_namespace("", ATOM_NAMESPACE)


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的 current_username 等）。"""
    return importlib.import_module("api.app")


# ==================== 请求模型 ====================

class SubscriptionFilters(BaseModel):
    content_type: Optional[str] = None
    content_types: Optional[str] = None
    source_id: Optional[str] = None
    source_ids: Optional[str] = None
    job_id: Optional[int] = None
    job_run_id: Optional[int] = None
    fetch_run_id: Optional[int] = None
    run_scope: Optional[str] = None
    publish_date_start: Optional[str] = None
    publish_date_end: Optional[str] = None
    fetched_date_start: Optional[str] = None
    fetched_date_end: Optional[str] = None
    search: Optional[str] = None
    has_content: Optional[bool] = True


class SubscriptionDeliveryPolicy(BaseModel):
    include_content: bool = True
    default_limit: int = 100
    max_limit: int = 500


class SubscriptionCreate(BaseModel):
    name: str
    description: str = ""
    filters: SubscriptionFilters = PydanticField(default_factory=SubscriptionFilters)
    delivery_policy: SubscriptionDeliveryPolicy = PydanticField(default_factory=SubscriptionDeliveryPolicy)
    is_active: bool = True


class SubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    filters: Optional[SubscriptionFilters] = None
    delivery_policy: Optional[SubscriptionDeliveryPolicy] = None
    is_active: Optional[bool] = None


class PublicSubscriptionSearchBody(BaseModel):
    query: str
    top_k: int = 5
    # v3.31 检索改 FTS5 全文检索后无相关性阈值/重排语义;两字段保留兼容旧调用方,忽略。
    score_threshold: float = 1.5
    rerank: bool = False


# ==================== 个人聚合拉取（dfeed_ 令牌）====================

def _atom_tag(name: str) -> str:
    return f"{{{ATOM_NAMESPACE}}}{name}"


def _parse_atom_datetime(value: Optional[str]) -> Optional[datetime.datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _format_atom_datetime(value: datetime.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _xml_text(value: Optional[str]) -> str:
    """Return XML 1.0-safe text; ElementTree handles reserved-character escaping."""
    return "".join(
        char
        for char in str(value or "")
        if (
            char in "\t\n\r"
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
    )


def _atom_feed_xml(owner: str, records: list, include_content: bool) -> bytes:
    now = datetime.datetime.now(datetime.timezone.utc)
    entries = [
        (
            record,
            _parse_atom_datetime(record.publish_date)
            or _parse_atom_datetime(record.fetched_date)
            or now,
        )
        for record in records
    ]
    feed_updated = max((timestamp for _, timestamp in entries), default=now)
    registry_meta = _registry_source_meta()

    feed = ET.Element(_atom_tag("feed"))
    owner_id = urllib.parse.quote(owner, safe="")
    ET.SubElement(feed, _atom_tag("id")).text = f"tag:dorami.local,2026:feed:{owner_id}"
    ET.SubElement(feed, _atom_tag("title")).text = _xml_text(f"哆啦美订阅聚合 · {owner}")
    ET.SubElement(feed, _atom_tag("updated")).text = _format_atom_datetime(feed_updated)

    for record, timestamp in entries:
        entry = ET.SubElement(feed, _atom_tag("entry"))
        article_id = record.source_url or (
            f"tag:dorami.local,2026:article:{urllib.parse.quote(record.id, safe='')}"
        )
        ET.SubElement(entry, _atom_tag("id")).text = _xml_text(article_id)
        ET.SubElement(entry, _atom_tag("title")).text = _xml_text(record.title)
        if record.source_url:
            ET.SubElement(
                entry,
                _atom_tag("link"),
                {"href": _xml_text(record.source_url), "rel": "alternate"},
            )
        atom_timestamp = _format_atom_datetime(timestamp)
        ET.SubElement(entry, _atom_tag("updated")).text = atom_timestamp
        ET.SubElement(entry, _atom_tag("published")).text = atom_timestamp

        author = ET.SubElement(entry, _atom_tag("author"))
        source_name = _friendly_source_name(record.source_id, registry_meta)
        ET.SubElement(author, _atom_tag("name")).text = _xml_text(source_name)

        if include_content:
            ET.SubElement(entry, _atom_tag("content"), {"type": "text"}).text = _xml_text(
                record.content
            )
        else:
            ET.SubElement(entry, _atom_tag("summary"), {"type": "text"}).text = _xml_text(
                (record.content or "")[:280]
            )
        ET.SubElement(
            entry,
            _atom_tag("category"),
            {"term": _xml_text(record.content_type), "scheme": "urn:dorami:content-type"},
        )
        ET.SubElement(
            entry,
            _atom_tag("category"),
            {"term": _xml_text(record.source_id), "scheme": "urn:dorami:source-id"},
        )

    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)

@router.get("/api/public/feed/articles")
def get_public_feed_articles(
        request: Request,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_ids: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        include_content: bool = True,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    """个人聚合拉取接口：用个人聚合令牌一次性拉取当前用户全部已订阅来源的文章。

    支持按发布时间（publish_date_start/end）、来源、类型、关键词筛选；适合日报等下游场景。
    """
    safe_limit = min(max(limit, 1), 500)
    token = read_bearer_or_query_token(request)
    owner = resolve_feed_token_owner(session, token)
    if not owner:
        raise HTTPException(status_code=401, detail="个人聚合接口令牌无效")
    records = feed_articles_for_owner(
        session, owner,
        content_type=content_type, content_types=content_types, source_ids=source_ids,
        search=search, has_content=has_content,
        publish_date_start=publish_date_start, publish_date_end=publish_date_end,
        skip=skip, limit=safe_limit,
    )
    return {
        "status": "success",
        "count": len(records),
        "skip": skip,
        "limit": safe_limit,
        "next_skip": skip + len(records) if len(records) == safe_limit else None,
        "items": [serialize_feed_article(record, include_content=include_content) for record in records],
    }


@router.get("/api/public/feed/articles.md")
def export_public_feed_articles_markdown(
        request: Request,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_ids: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    """个人聚合拉取接口的 Markdown 批量导出变体（最多 200 条）。"""
    safe_limit = min(max(limit, 1), PUBLIC_FEED_EXPORT_LIMIT)
    token = read_bearer_or_query_token(request)
    owner = resolve_feed_token_owner(session, token)
    if not owner:
        raise HTTPException(status_code=401, detail="个人聚合接口令牌无效")
    records = feed_articles_for_owner(
        session, owner,
        content_type=content_type, content_types=content_types, source_ids=source_ids,
        search=search, has_content=has_content,
        publish_date_start=publish_date_start, publish_date_end=publish_date_end,
        skip=skip, limit=safe_limit,
    )
    body = "\n\n---\n\n".join(article_to_markdown(record) for record in records)
    return Response(content=body, media_type="text/markdown; charset=utf-8")


@router.get("/api/public/feed/articles.xml")
def export_public_feed_articles_atom(
        request: Request,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_ids: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        include_content: bool = True,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
        session: Session = Depends(deps.get_session),
):
    """个人聚合拉取接口的 Atom 1.0 变体（最多 200 条）。"""
    safe_limit = min(max(limit, 1), PUBLIC_FEED_EXPORT_LIMIT)
    token = read_bearer_or_query_token(request)
    owner = resolve_feed_token_owner(session, token)
    if not owner:
        raise HTTPException(status_code=401, detail="个人聚合接口令牌无效")
    records = feed_articles_for_owner(
        session, owner,
        content_type=content_type, content_types=content_types, source_ids=source_ids,
        search=search, has_content=has_content,
        publish_date_start=publish_date_start, publish_date_end=publish_date_end,
        skip=skip, limit=safe_limit,
    )
    return Response(
        content=_atom_feed_xml(owner, records, include_content),
        media_type="application/atom+xml",
    )


# ==================== 订阅生命周期（owner 作用域）====================

def _owned_subscription_or_404(session: Session, subscription_id: int, username: str) -> ReaderSubscriptionRecord:
    record = session.get(ReaderSubscriptionRecord, subscription_id)
    if not record or record.owner_username != username:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    return record


@router.get("/api/subscriptions")
def get_subscriptions(
        request: Request, is_active: Optional[bool] = None, session: Session = Depends(deps.get_session)
):
    username = _app().current_username(request)
    query = select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
    if is_active is not None:
        query = query.where(ReaderSubscriptionRecord.is_active == is_active)
    records = session.exec(query.order_by(ReaderSubscriptionRecord.name)).all()
    return [serialize_subscription(record) for record in records]


@router.get("/api/subscriptions/{subscription_id}")
def get_subscription(subscription_id: int, request: Request, session: Session = Depends(deps.get_session)):
    record = _owned_subscription_or_404(session, subscription_id, _app().current_username(request))
    return serialize_subscription(record)


def _guard_filter_user_sources(session: Session, username: str, filters: dict) -> None:
    """订阅 filters 中的用户源 id 归属校验(检视返修 F2:高级订阅路径同守门)。"""
    from services import user_sources as user_sources_service

    ids: list[str] = []
    for key in ("source_ids", "source_id"):
        value = (filters or {}).get(key)
        if value:
            ids.extend(part.strip() for part in str(value).split(",") if part.strip())
    denied = user_sources_service.unauthorized_user_source_ids(session, username, ids)
    if denied:
        raise HTTPException(status_code=404, detail="来源不存在或暂不可用")


@router.post("/api/subscriptions")
def create_subscription(
        params: SubscriptionCreate, request: Request, session: Session = Depends(deps.get_session)
):
    name = params.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="订阅源名称不能为空")
    username = _app().current_username(request)
    from services.user_sources import _WRITE_LOCK  # noqa: PLC0415

    # 校验与提交同持用户源写锁(三轮收口):防「校验时配置还在→提交前被 purge」
    # 竞态产生悬空引用(每日 GC 另有兜底)。
    with _WRITE_LOCK:
        _guard_filter_user_sources(session, username, _model_to_clean_dict(params.filters))
        token = generate_subscription_token()
        now = _now_iso()
        record = ReaderSubscriptionRecord(
            owner_username=username,
            name=name,
            description=params.description.strip(),
            filters_json=_json_dumps(_model_to_clean_dict(params.filters)),
            delivery_policy_json=_json_dumps(normalize_delivery_policy(_model_dump(params.delivery_policy))),
            token_hash=hash_subscription_token(token),
            token_preview=subscription_token_preview(token),
            is_active=params.is_active,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
    session.refresh(record)
    return serialize_subscription(record, token=token)


@router.put("/api/subscriptions/{subscription_id}")
def update_subscription(
        subscription_id: int, params: SubscriptionUpdate, request: Request,
        session: Session = Depends(deps.get_session),
):
    record = _owned_subscription_or_404(session, subscription_id, _app().current_username(request))
    update_data = _model_dump(params, exclude_unset=True)
    from services.user_sources import _WRITE_LOCK  # noqa: PLC0415

    with _WRITE_LOCK:  # 同 create:filters 校验与提交互斥于用户源 purge
        if "name" in update_data:
            name = (update_data["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="订阅源名称不能为空")
            record.name = name
        if "description" in update_data:
            record.description = (update_data["description"] or "").strip()
        if "filters" in update_data and update_data["filters"] is not None:
            clean_filters = {key: value for key, value in update_data["filters"].items() if value not in (None, "")}
            _guard_filter_user_sources(session, record.owner_username, clean_filters)
            record.filters_json = _json_dumps(clean_filters)
        if "delivery_policy" in update_data and update_data["delivery_policy"] is not None:
            record.delivery_policy_json = _json_dumps(normalize_delivery_policy(update_data["delivery_policy"]))
        if "is_active" in update_data:
            record.is_active = update_data["is_active"]
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
    session.refresh(record)
    return serialize_subscription(record)


@router.post("/api/subscriptions/{subscription_id}/rotate-token")
def rotate_subscription_token(
        subscription_id: int, request: Request, session: Session = Depends(deps.get_session)
):
    token = generate_subscription_token()
    record = _owned_subscription_or_404(session, subscription_id, _app().current_username(request))
    record.token_hash = hash_subscription_token(token)
    record.token_preview = subscription_token_preview(token)
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return serialize_subscription(record, token=token)


@router.delete("/api/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: int, request: Request, session: Session = Depends(deps.get_session)):
    record = _owned_subscription_or_404(session, subscription_id, _app().current_username(request))
    session.delete(record)
    session.commit()
    return {"status": "success"}


# ==================== 单订阅令牌拉取/检索（dsub_ 令牌）====================

@router.get("/api/public/subscriptions/{subscription_id}/articles")
def get_public_subscription_articles(
        subscription_id: int,
        request: Request,
        skip: int = 0,
        limit: Optional[int] = None,
        session: Session = Depends(deps.get_session),
):
    subscription = resolve_subscription_by_token(
        session, subscription_id, read_bearer_or_query_token(request),
    )
    records, query_info = query_subscription_articles(session, subscription, skip=skip, limit=limit)

    include_content = query_info["policy"]["include_content"]
    safe_limit = query_info["limit"]
    return {
        "status": "success",
        "subscription": {
            "id": subscription.id,
            "name": subscription.name,
        },
        "count": len(records),
        "skip": skip,
        "limit": safe_limit,
        "next_skip": skip + len(records) if len(records) == safe_limit else None,
        "items": [serialize_feed_article(record, include_content=include_content) for record in records],
    }


@router.post("/api/public/subscriptions/{subscription_id}/vector/search")
async def public_subscription_vector_search(
        subscription_id: int,
        body: PublicSubscriptionSearchBody,
        request: Request,
):
    """带令牌的、按订阅源范围约束的全文检索（供下游 Agent 应用个性化使用）。

    v3.31 检索扶正/退役清仓:向量检索退役,同路径换 FTS5 全文检索芯(标题+正文,
    中英文子串匹配,按发布日期倒序)——与 MCP search_articles 同源实现。
    """
    from mcp_server import _search_articles_impl

    with Session(deps.get_db_sink().engine) as session:
        subscription = resolve_subscription_by_token(
            session, subscription_id, read_bearer_or_query_token(request),
        )
        filters = _json_loads(subscription.filters_json, {})
        source_ids = subscription_source_ids(subscription)
        if not source_ids:
            # 空 filters 订阅=「全库」语义:与文章交付端点同口径,收敛为公共可见域
            # (减隐藏源与全部用户源;codex 复检二轮:此前空列表被 FTS 解释为零结果,
            # 与文章接口的「公共全库」形成两套契约)。
            from api.feed_service import resolve_all_visible_source_ids

            source_ids = resolve_all_visible_source_ids(session)
        sub_id, sub_name = subscription.id, subscription.name

    results = await _search_articles_impl(
        deps.get_db_sink(),
        query=body.query,
        top_k=body.top_k,
        content_type=filters.get("content_type"),
        # 空范围保持空列表语义(=零订阅返回空,与 MCP 契约一致;检视返修 F1:
        # 曾 `or None` 把空 filters 订阅放大成全库检索,连私有用户源一起搜)。
        source_ids=source_ids,
    )
    return {
        "status": "success",
        "subscription": {"id": sub_id, "name": sub_name},
        "scoped_source_ids": source_ids,
        "count": len(results),
        "results": results,
    }
