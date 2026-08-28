"""阅读器订阅/收藏/计量 Router（reader 面）。

阶段1 从 app.py 迁出的 reader 互动子簇：一键订阅/退订、阅读计量、收藏增删查、
个人聚合接口令牌。说明：
- 路径不变（prefix=/api/reader）；reader 网关仍由 app.py 中间件统一强制
  （READER_API_PREFIXES 含 /api/reader）；
- 数据访问经 Depends(deps.get_session)；
- 仍留守 app.py 且被多端点共用的业务 helper（当前用户名/订阅范围解析/单源订阅创建/
  文章列表序列化/订阅令牌生成与哈希）经 _app() 延迟动态调用，避免与 api.app 的导入环；
  这些 helper 不在测试 monkeypatch 名单内，动态调用安全。源目录元数据助手已在 api.sources。
"""

import datetime
import importlib
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import literal_column, or_
from sqlmodel import Session, func, select

from api import deps
from api.articles_view import serialize_article_list_item
from api.routers.articles import content_shape_condition
from api.tokens import generate_feed_token, hash_subscription_token, subscription_token_preview
from api.sources import (
    DAILY_BRIEF_SOURCE_ID,
    DAILY_BRIEF_SOURCE_META,
    _friendly_source_name,
    _registry_source_meta,
    _source_category,
    VALID_CONTENT_SHAPES,
    configured_source_content_type,
    configured_source_platform,
    configured_source_shape,
    source_shape,
    subscription_source_ids,
)
from fetchers.registry import DECOMMISSIONED_FETCHER_IDS
from llm.client import LLMError, UsageMeta
from storage.fts import fts_search_ids
from models.db import (
    AiUsageRecord,
    ArticleRecord,
    ReaderFavoriteRecord,
    ReaderFeedTokenRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
)
from services import accounts as accounts_service
from services import article_share as article_share_service
from services import daily_brief as daily_brief_service
from services import reader_activity as reader_activity_service
from services import reader_ai as reader_ai_service
from services import reader_search as reader_search_service
from services import reader_state as reader_state_service
from services import source_collections as source_collections_service
from services import source_visibility as source_visibility_service
from services import user_sources as user_sources_service
from services import x_api_config as x_api_config_service

router = APIRouter(prefix="/api/reader", tags=["reader"])


def _app():
    """延迟取 api.app（避免导入环；动态调用其留守的共享业务 helper）。"""
    return importlib.import_module("api.app")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def _configured_source_meta(session: Session) -> Dict[str, Dict[str, Any]]:
    """SourceConfig 的读者目录元数据；尤其覆盖尚无产出的配置源形态。"""
    result: Dict[str, Dict[str, Any]] = {}
    for record in session.exec(select(SourceConfigRecord)).all():
        try:
            tags = json.loads(record.content_tags_json or "[]")
        except (TypeError, json.JSONDecodeError):
            tags = []
        result[record.source_id] = {
            "id": record.source_id,
            "name": record.name,
            "desc": record.description,
            "icon": "𝕏" if configured_source_shape(record.source_type, record.fetcher_id) == "social" else "",
            "content_type": configured_source_content_type(record.source_type, record.fetcher_id),
            "shape": configured_source_shape(record.source_type, record.fetcher_id),
            "platform": configured_source_platform(record.source_type, record.fetcher_id),
            "source_owner": record.source_owner,
            "source_brand": record.source_brand,
            "source_scope": record.source_scope,
            "source_channel": record.source_channel,
            "provenance_tier": record.provenance_tier,
            "base_url": record.base_url or record.url,
            "content_tags": tags if isinstance(tags, list) else [],
        }
    return result


def _primary_content_types(session: Session, source_ids: List[str]) -> Dict[str, str]:
    """未注册/未配置源按归档主 content_type 解析形态。"""
    if not source_ids:
        return {}
    rows = session.exec(
        select(ArticleRecord.source_id, ArticleRecord.content_type, func.count(ArticleRecord.id))
        .where(ArticleRecord.source_id.in_(source_ids))
        .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
    ).all()
    primary: Dict[str, tuple[int, str]] = {}
    for source_id, content_type, count in rows:
        candidate = (int(count or 0), content_type)
        if candidate[0] > primary.get(source_id, (-1, ""))[0]:
            primary[source_id] = candidate
    return {source_id: value[1] for source_id, value in primary.items()}


def resolve_favorite_article_ids(session: Session, username: str) -> List[str]:
    """当前用户全部收藏的文章 ID（不分页，供前端维护收藏态集合）。"""
    if not username:
        return []
    rows = session.exec(
        select(ReaderFavoriteRecord.article_id).where(
            ReaderFavoriteRecord.owner_username == username
        )
    ).all()
    return list(rows)


# ==================== 一键订阅 / 退订 ====================

@router.post("/sources/{source_id}/subscribe")
def subscribe_source(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """一键订阅单个内容源：尚未订阅则创建一个仅含该源的订阅，已订阅则幂等返回。

    交付令牌、限额等高级设置使用默认值，留待用户在「我的订阅」中按需编辑。
    """
    app = _app()
    username = app.current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    if source_id in source_visibility_service.hidden_source_ids(session):
        # 管理面隐藏的源不接受新订阅（目录里也不可见；防旧页面状态/直连 API 绕过）。
        raise HTTPException(status_code=404, detail="该内容源暂不可用")
    if user_sources_service.unauthorized_user_source_ids(session, username, [source_id]):
        # 用户源的准入凭证是 feed URL(custom-sources 通道),知道 source_id 不构成
        # 订阅资格(检视返修 F2:防 facets 泄露的 id 被用来伪造成员资格)。
        raise HTTPException(status_code=404, detail="该内容源暂不可用")
    registry_meta = _registry_source_meta()
    already = source_id in set(app.resolve_subscribed_source_ids(session, username))
    if not already:
        app._create_single_source_subscription(
            session, username, source_id, _friendly_source_name(source_id, registry_meta)
        )
        # 订阅即初始化未读水位（backlog 语义：最近 K 篇成为未读积压，Folo 式;
        # 再订阅也重新起算）。
        reader_state_service.init_cursor_with_backlog(session, username=username, source_id=source_id)
        session.commit()
    subscribed_ids = sorted(set(
        # 目录口径:含被隐藏的已订阅源(源栏「暂不可用」条目依赖此集合渲染)。
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    ))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": True,
        "subscribed_source_ids": subscribed_ids,
    }


@router.delete("/sources/{source_id}/subscribe")
def unsubscribe_source(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """一键取消订阅：从当前用户的所有订阅范围内移除该源，因此清空的订阅会被删除。"""
    app = _app()
    username = app.current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    if user_sources_service.is_user_source(source_id):
        # 用户源的退订=移除语义(检视返修 F8:普通退订会留下无人订阅的孤儿源仍被
        # 调度;前端已分流,此处是 API 直连的后端兜底,行为与专用移除端点一致)。
        try:
            result = user_sources_service.remove_user_source(session, username, source_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="该内容源暂不可用")
        subscribed_ids = sorted(set(
            app.resolve_subscribed_source_ids(session, username, include_hidden=True)
        ))
        return {
            "status": "success", "source_id": source_id, "subscribed": False,
            "subscribed_source_ids": subscribed_ids, **result,
        }
    records = session.exec(
        select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
    ).all()
    for record in records:
        ids = subscription_source_ids(record)
        if source_id not in ids:
            continue
        remaining = [sid for sid in ids if sid != source_id]
        if remaining:
            try:
                filters = json.loads(record.filters_json) if record.filters_json else {}
            except (TypeError, json.JSONDecodeError):
                filters = {}
            filters.pop("source_id", None)
            filters["source_ids"] = ",".join(remaining)
            record.filters_json = json.dumps(filters or {}, ensure_ascii=False)
            record.updated_at = _now_iso()
            session.add(record)
        else:
            session.delete(record)
    # 退订清未读水位（逐篇已读行保留无害：无水位即不判未读，且不影响其他源）。
    reader_state_service.drop_cursor(session, username=username, source_id=source_id)
    session.commit()
    subscribed_ids = sorted(set(
        # 目录口径:含被隐藏的已订阅源(源栏「暂不可用」条目依赖此集合渲染)。
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    ))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": False,
        "subscribed_source_ids": subscribed_ids,
    }


# ==================== 源合集(策展合集) ====================

@router.get("/collections")
def list_source_collections():
    """发现页合集目录(代码注册表直出)。

    轻载荷:只给合集自身元数据与成员 id 名单,成员的订阅态/头像/计数由前端
    与已持有的 GET /api/reader/sources 目录 join,不重复下发。
    """
    return {
        "collections": [
            source_collections_service.serialize_collection(collection)
            for collection in source_collections_service.list_collections()
        ]
    }


@router.post("/collections/{collection_id}/subscribe")
def subscribe_collection(collection_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """一键订阅合集 = 批量订阅其当前成员(批量动作,非持久绑定)。

    逐成员沿用单源订阅的两条纪律:隐藏源与注册表外成员跳过(不整体 404,
    回报在 unavailable),幂等基线用 include_hidden 的原始订阅并集;
    整批单事务一次 commit(复刻 ensure_default_subscriptions 范式)。
    """
    app = _app()
    username = app.current_username(request)
    collection = source_collections_service.get_collection((collection_id or "").strip())
    if collection is None:
        raise HTTPException(status_code=404, detail="合集不存在")
    hidden = source_visibility_service.hidden_source_ids(session)
    registry_meta = _registry_source_meta()
    existing = set(app.resolve_subscribed_source_ids(session, username, include_hidden=True))
    added: List[str] = []
    already_subscribed: List[str] = []
    unavailable: List[str] = []
    for source_id in collection.source_ids:
        if source_id in hidden or source_id not in registry_meta:
            # 隐藏源不接受新订阅;注册表外成员(注册表漂移的运行时兜底)不下单。
            unavailable.append(source_id)
            continue
        if source_id in existing:
            already_subscribed.append(source_id)
            continue
        app._create_single_source_subscription(
            session, username, source_id, _friendly_source_name(source_id, registry_meta)
        )
        # 与单源一键订阅同语义:订阅即初始化未读水位(backlog 式)。
        reader_state_service.init_cursor_with_backlog(session, username=username, source_id=source_id)
        added.append(source_id)
    if added:
        session.commit()
    subscribed_ids = sorted(set(
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    ))
    return {
        "status": "success",
        "collection_id": collection.collection_id,
        "added": added,
        "already_subscribed": already_subscribed,
        "unavailable": unavailable,
        "subscribed_source_ids": subscribed_ids,
    }


@router.delete("/collections/{collection_id}/subscribe")
def unsubscribe_collection(collection_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """取消订阅合集 = 批量退订其当前成员。

    无绑定记录的诚实推论:同属其它合集的成员也会被退订(前端确认框如实列出)。
    逐成员语义与单源退订一致:剔 filters、清空即删订阅记录、清未读水位。
    """
    app = _app()
    username = app.current_username(request)
    collection = source_collections_service.get_collection((collection_id or "").strip())
    if collection is None:
        raise HTTPException(status_code=404, detail="合集不存在")
    member_ids = set(collection.source_ids)
    removed: set[str] = set()
    records = session.exec(
        select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
    ).all()
    for record in records:
        ids = subscription_source_ids(record)
        remaining = [sid for sid in ids if sid not in member_ids]
        if len(remaining) == len(ids):
            continue
        removed.update(set(ids) & member_ids)
        if remaining:
            try:
                filters = json.loads(record.filters_json) if record.filters_json else {}
            except (TypeError, json.JSONDecodeError):
                filters = {}
            filters.pop("source_id", None)
            filters["source_ids"] = ",".join(remaining)
            record.filters_json = json.dumps(filters or {}, ensure_ascii=False)
            record.updated_at = _now_iso()
            session.add(record)
        else:
            session.delete(record)
    # 与单源退订同语义:清全部成员的水位(未订阅成员无水位,drop 幂等无害)。
    for source_id in collection.source_ids:
        reader_state_service.drop_cursor(session, username=username, source_id=source_id)
    session.commit()
    subscribed_ids = sorted(set(
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    ))
    return {
        "status": "success",
        "collection_id": collection.collection_id,
        "removed": sorted(removed),
        "subscribed_source_ids": subscribed_ids,
    }


# ==================== 用户自定源(v3.40) ====================
# 读者自助添加私有 RSS 源;方案 docs/user-custom-rss-wave-plan.md。
# 业务在 services/user_sources.py;此处只做门控/编排(订阅动作复用 app 既有助手)。


class CustomSourceParams(BaseModel):
    url: str
    name: Optional[str] = None


def _deny_unsubscribed_user_source_article(session: Session, username: str, article) -> None:
    """用户源文章的动作级授权(检视返修 F3):分享签发/AI 翻译/问答/速读等**动作**
    要求请求者是该源订阅者——按 id 直达的只读详情维持既有豁免(id 不可枚举)。"""
    source_id = getattr(article, "source_id", "") or ""
    if user_sources_service.is_user_source(source_id) and \
            user_sources_service.unauthorized_user_source_ids(session, username, [source_id]):
        raise HTTPException(status_code=404, detail="文章不存在")


def _require_user_sources_enabled(session: Session) -> None:
    """功能总闸(KV,默认开):关闭时添加/preview 403;列表/删除不挡(允许清理)。

    分离部署(检视返修 F10):reader runtime 明确不承担公网抓取,而添加/preview
    都要出网拉 feed——非 collector 运行角色一律 403(单机 all 形态不受影响)。
    """
    if not _app().runtime_collector_enabled():
        raise HTTPException(status_code=403, detail="当前部署形态不支持自助添加来源")
    if not user_sources_service.feature_enabled(session):
        raise HTTPException(status_code=403, detail="自定源功能未开放")


def _custom_source_conflict(session: Session, url: str) -> Optional[Dict[str, Any]]:
    """撞库检测的端点半边:命中隐藏系统源直接 404;命中可见系统源/既有用户源返回引导载荷。"""
    result = user_sources_service.prepare_check(session, url)
    if result.get("blocked"):
        # 与隐藏源既有口径一致的统一文案,不泄露「存在但被隐藏」这一事实。
        raise HTTPException(status_code=404, detail="该来源暂不可用")
    return result.get("existing") or None


@router.post("/custom-sources/preview")
async def preview_custom_source(
        params: CustomSourceParams, request: Request, session: Session = Depends(deps.get_session)
):
    """贴 URL → 守门校验 + 条目样例预览(不落库)。撞中系统源/既有用户源时返回引导。"""
    app = _app()
    username = app.current_username(request)
    _require_user_sources_enabled(session)
    try:
        existing = _custom_source_conflict(session, params.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if existing:
        subscribed = existing["source_id"] in set(
            app.resolve_subscribed_source_ids(session, username, include_hidden=True)
        )
        return {"status": "exists", "existing": {**existing, "subscribed": subscribed}}
    try:
        preview = await user_sources_service.fetch_feed_preview(params.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:  # noqa: BLE001 - 网络失败归一为可读文案,不透传栈
        raise HTTPException(status_code=400, detail="无法访问该地址,请检查 URL 或稍后再试")
    quota_used = len(user_sources_service.subscribed_user_source_ids(session, username))
    return {
        "status": "ok",
        **preview,
        "quota": {"used": quota_used, "max": user_sources_service.MAX_SOURCES_PER_USER},
    }


@router.post("/custom-sources")
async def create_custom_source(
        params: CustomSourceParams, request: Request, session: Session = Depends(deps.get_session)
):
    """添加自定源:守门 → 撞库 → 建/复用配置行 → 订阅本人 → 提交首抓后台 job。"""
    app = _app()
    username = app.current_username(request)
    _require_user_sources_enabled(session)
    try:
        existing = _custom_source_conflict(session, params.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if existing and existing.get("kind") == "system":
        # 撞中可见系统源:不建用户源,前端引导走普通订阅(该来源已收录)。
        return {"status": "exists", "existing": existing}
    preview = {"feed_title": ""}
    if not existing:
        # 新 feed 才重跑守门(不能信任前端一定先走了 preview);顺带拿 feed_title 作默认名。
        # 既有用户源(去重共享:第二人添加同 URL)跳过网络校验,直接进复用+订阅。
        try:
            preview = await user_sources_service.fetch_feed_preview(params.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="无法访问该地址,请检查 URL 或稍后再试")
    # 建行→订阅→commit 是 check-then-write 段,以 service 写锁串行化(检视返修 F7;
    # 段内全同步无 await,锁窗口极短)。prepared 的 blocked(隐藏/admin 停用的既有
    # 用户源)统一按「暂不可用」处理。
    with user_sources_service._WRITE_LOCK:  # noqa: SLF001 - 与 service 写路径同一把锁
        try:
            prepared = user_sources_service.prepare_user_source(
                session, username, params.url, name=(params.name or preview["feed_title"] or "")
            )
        except user_sources_service.UserSourceQuotaError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if prepared.get("blocked"):
            raise HTTPException(status_code=404, detail="该来源暂不可用")
        source_id = prepared["source_id"]
        record = prepared["record"]
        already = source_id in set(app.resolve_subscribed_source_ids(session, username, include_hidden=True))
        if not already:
            app._create_single_source_subscription(session, username, source_id, record.name)
            reader_state_service.init_cursor_with_backlog(session, username=username, source_id=source_id)
        session.commit()

    if already and not prepared["created"]:
        # 幂等重放(检视返修 F9):已订阅者重复 POST 相同 URL 不再触发抓取——
        # 否则可被脚本化成无限外网请求/抓取运行的放大器。
        return {
            "status": "success", "source_id": source_id, "name": record.name,
            "created": False, "first_fetch": "skipped", "saved_count": 0,
        }

    # 首抓同步等待(2026-08-28 返修:原后台 job 形态下,添加后立即点开该源列表为空,
    # 像「没文章」,刷新才出现——单 feed 首抓仅数秒,同步等完再返回,modal 的 busy 态
    # 自然覆盖,关闭浮层即一切就绪)。失败不阻断:源已建成,随定时调度重试。
    from api.routers.source_configs import build_source_fetch_params

    fetch_params = build_source_fetch_params(record, {})
    first_fetch = "ok"
    saved_count = 0
    try:
        fetch_result = await app.run_single_fetch_as_collection(
            "generic_rss", fetch_params,
            name=f"自定源首抓: {record.name}", trigger_type="manual", run_scope="ad_hoc",
        )
        saved_count = int(fetch_result.get("saved_count")
                          or (fetch_result.get("results") or [{}])[0].get("saved_count", 0) or 0)
    except Exception:  # noqa: BLE001 - 首抓失败不还原添加,健康面/调度接手
        first_fetch = "failed"
    return {
        "status": "success",
        "source_id": source_id,
        "name": record.name,
        "created": prepared["created"],
        "first_fetch": first_fetch,
        "saved_count": saved_count,
    }


@router.get("/custom-sources")
def list_custom_sources(request: Request, session: Session = Depends(deps.get_session)):
    """我的自定源(含健康摘要:暂停/失败态供前端细签)。"""
    app = _app()
    username = app.current_username(request)
    items = user_sources_service.list_user_sources(session, username=username)
    return {
        "items": items,
        "quota": {"used": len(items), "max": user_sources_service.MAX_SOURCES_PER_USER},
        "enabled": user_sources_service.feature_enabled(session),
    }


@router.delete("/custom-sources/{source_id}")
def remove_custom_source(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """移除自定源:退订本人;无其他活跃订阅者时物理删除(配置行+文章)。"""
    app = _app()
    username = app.current_username(request)
    try:
        result = user_sources_service.remove_user_source(session, username, source_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="自定源不存在")
    subscribed_ids = sorted(set(
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    ))
    return {"status": "success", **result, "subscribed_source_ids": subscribed_ids}


# ==================== 阅读计量 ====================

@router.post("/articles/{article_id}/read")
def record_article_read(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """记录一次主动阅读：读者在阅读器中打开某文章即按其来源累加阅读计量。

    前端 fire-and-forget 调用；计量绝不阻断阅读——文章不存在或写入异常都安静返回。
    """
    username = _app().current_username(request)
    article = session.get(ArticleRecord, article_id)
    if article is None:
        return {"status": "ignored"}
    source_id = article.source_id
    try:
        # 三写：逐篇已读状态 + 文章级累计阅读数（均不自 commit），由紧随的计量写入一并提交。
        reader_state_service.mark_read(session, username=username, article_id=article.id)
        article.read_count = (article.read_count or 0) + 1
        session.add(article)
        reader_activity_service.record_read(session, username=username, source_id=source_id)
    except Exception:  # noqa: BLE001 - 计量/状态失败不影响阅读
        return {"status": "ignored"}
    # 回传累计阅读数：前端就地刷新阅读窗「累计阅读 N 次」，含读者本次这一下。
    return {"status": "ok", "source_id": source_id, "read_count": article.read_count}


# ==================== 未读体系 ====================

def _set_article_read_state(article_id: str, request: Request, session: Session, *, is_read: bool):
    """单篇显式标读/标未读的公共实现：写覆盖行（不计阅读量），404 于文章不存在。"""
    username = _app().current_username(request)
    article = session.get(ArticleRecord, (article_id or "").strip())
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    if is_read:
        reader_state_service.mark_read(session, username=username, article_id=article.id)
    else:
        reader_state_service.mark_unread(session, username=username, article_id=article.id)
    session.commit()
    return {"status": "success", "article_id": article.id, "is_read": is_read}


@router.post("/articles/{article_id}/mark-read")
def mark_article_read(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """手动把单篇标为已读（显式覆盖；不同于 /read，不累计阅读计量）。"""
    return _set_article_read_state(article_id, request, session, is_read=True)


@router.post("/articles/{article_id}/mark-unread")
def mark_article_unread(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """手动把单篇标回未读（显式覆盖：即使被水位盖过也生效，可撤销误触的已读）。"""
    return _set_article_read_state(article_id, request, session, is_read=False)


@router.get("/unread-counts")
def get_unread_counts(request: Request, session: Session = Depends(deps.get_session)):
    """当前用户各订阅源的未读数（只回 n>0 的源）+ 总数。

    对缺水位的存量订阅懒初始化为当下（升级后首访未读从 0 起算），
    故本端点也是水位体系的「挂载即校准」入口——阅读器进入即拉一次。
    """
    app = _app()
    username = app.current_username(request)
    source_ids = app.resolve_subscribed_source_ids(session, username)
    by_source = reader_state_service.unread_counts(
        session, username=username, source_ids=source_ids
    )
    return {"by_source": by_source, "total": sum(by_source.values())}


def _mark_all_read_response(app, session: Session, username: str, source_ids: List[str]):
    reader_state_service.mark_all_read(session, username=username, source_ids=source_ids)
    subscribed = app.resolve_subscribed_source_ids(session, username)
    by_source = reader_state_service.unread_counts(
        session, username=username, source_ids=subscribed
    )
    return {"status": "success", "by_source": by_source, "total": sum(by_source.values())}


@router.post("/mark-all-read")
def mark_all_read(
    request: Request,
    shape: Optional[str] = None,
    session: Session = Depends(deps.get_session),
):
    """把当前用户订阅源标为已读（推进各源水位到当下），返回更新后的未读统计。

    可选 shape=article|bulletin|social:只标对应形态的源，不越容器边界。
    未传 = 全部订阅。
    """
    app = _app()
    username = app.current_username(request)
    source_ids = app.resolve_subscribed_source_ids(session, username)
    shape_value = (shape or "").strip().lower()
    if shape_value in VALID_CONTENT_SHAPES:
        registry_meta = _registry_source_meta()
        source_meta = {**_configured_source_meta(session), **registry_meta}
        content_types = _primary_content_types(session, source_ids)
        source_ids = [
            sid for sid in source_ids
            if source_shape(sid, content_types.get(sid), source_meta) == shape_value
        ]
    return _mark_all_read_response(app, session, username, source_ids)


@router.post("/sources/{source_id}/mark-all-read")
def mark_source_all_read(source_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """把单个来源标为已读，返回更新后的未读统计。"""
    app = _app()
    username = app.current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    return _mark_all_read_response(app, session, username, [source_id])


# ==================== 文章收藏 ====================

@router.get("/favorites")
def list_favorites(
    request: Request,
    search: Optional[str] = None,
    source_id: Optional[str] = None,
    shape: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    include_content: bool = False,
    session: Session = Depends(deps.get_session),
):
    """当前用户的收藏文章列表，按收藏时间倒序；同时回传全部收藏 ID 集合。

    join 文章表后，已被删除文章的孤儿收藏自然被过滤掉，不出现在列表里。
    可选 shape=article|bulletin|social:容器内收藏过滤器。
    """
    app = _app()
    auth_session = app.current_auth_session(request)
    username = str(auth_session.get("sub", "")) if auth_session else ""
    safe_limit = min(max(int(limit), 1), 500)
    safe_skip = max(int(skip), 0)
    base = (
        select(ArticleRecord, ReaderFavoriteRecord.created_at)
        .join(ReaderFavoriteRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .where(ReaderFavoriteRecord.owner_username == username)
    )
    count_query = (
        select(func.count())
        .select_from(ReaderFavoriteRecord)
        .join(ArticleRecord, ReaderFavoriteRecord.article_id == ArticleRecord.id)
        .where(ReaderFavoriteRecord.owner_username == username)
    )
    if not (auth_session and auth_session.get("role") == "admin"):
        # 与 /api/articles 同口径：管理面隐藏的源在读者收藏列表里也临时不可见
        # （收藏行保留，恢复可见即回归）。
        hidden = source_visibility_service.hidden_source_ids(session)
        if hidden:
            hidden_cond = or_(
                ArticleRecord.source_id.is_(None),
                ArticleRecord.source_id.notin_(sorted(hidden)),
            )
            base = base.where(hidden_cond)
            count_query = count_query.where(hidden_cond)
        # 用户自定源(v3.40 检视返修 D1):include_content 会带正文,收藏列表也是
        # 内容出口——退订后(或经泄露 id 收藏的)未订阅用户源文章一并剔除。
        blocked_user = user_sources_service.unauthorized_user_source_ids(
            session, username, sorted(user_sources_service.user_source_ids(session))
        )
        if blocked_user:
            user_cond = or_(
                ArticleRecord.source_id.is_(None),
                ArticleRecord.source_id.notin_(blocked_user),
            )
            base = base.where(user_cond)
            count_query = count_query.where(user_cond)
    if source_id:
        base = base.where(ArticleRecord.source_id == source_id)
        count_query = count_query.where(ArticleRecord.source_id == source_id)
    shape_value = (shape or "").strip().lower()
    if shape_value in VALID_CONTENT_SHAPES:
        cond = content_shape_condition(shape_value, session)
        base = base.where(cond)
        count_query = count_query.where(cond)
    if search:
        # FTS5 全文检索（标题+正文）；不可用/过短时回退标题 LIKE。base 与 count 同条件。
        fts_ids = fts_search_ids(session, search)
        if fts_ids is not None:
            cond = literal_column("articles.rowid").in_(fts_ids)
        else:
            cond = ArticleRecord.title.contains(search)
        base = base.where(cond)
        count_query = count_query.where(cond)
    base = base.order_by(ReaderFavoriteRecord.created_at.desc(), ArticleRecord.id.desc())
    total = int(session.exec(count_query).one() or 0)
    rows = session.exec(base.offset(safe_skip).limit(safe_limit)).all()
    items = [serialize_article_list_item(record, include_content=include_content) for record, _ in rows]
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {
        "items": items,
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
        "next_skip": safe_skip + len(items) if safe_skip + len(items) < total else None,
        "favorite_ids": favorite_ids,
    }


@router.post("/favorites/{article_id}")
def add_favorite(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """收藏一篇文章（幂等）。"""
    username = _app().current_username(request)
    article_id = (article_id or "").strip()
    if not article_id:
        raise HTTPException(status_code=400, detail="article_id 不能为空")
    if session.get(ArticleRecord, article_id) is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    if session.get(ReaderFavoriteRecord, (username, article_id)) is None:
        session.add(ReaderFavoriteRecord(
            owner_username=username, article_id=article_id, created_at=_now_iso()
        ))
        session.commit()
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {"status": "success", "article_id": article_id, "favorited": True, "favorite_ids": favorite_ids}


@router.delete("/favorites/{article_id}")
def remove_favorite(article_id: str, request: Request, session: Session = Depends(deps.get_session)):
    """取消收藏一篇文章（幂等）。"""
    username = _app().current_username(request)
    article_id = (article_id or "").strip()
    if not article_id:
        raise HTTPException(status_code=400, detail="article_id 不能为空")
    record = session.get(ReaderFavoriteRecord, (username, article_id))
    if record is not None:
        session.delete(record)
        session.commit()
    favorite_ids = resolve_favorite_article_ids(session, username)
    return {"status": "success", "article_id": article_id, "favorited": False, "favorite_ids": favorite_ids}


# ==================== 文章分享 ====================

class ShareCreateParams(BaseModel):
    # None = 永久有效；其余取 SHARE_EXPIRY_CHOICES 中的天数档位。
    expires_in_days: Optional[int] = 7


def _serialize_share(record, *, article: Optional[ArticleRecord] = None) -> Dict[str, Any]:
    return {
        "id": record.id,
        "token": record.token,
        "article_id": record.article_id,
        "article_title": (article.title if article else "") or "",
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
        "live": article_share_service.is_live(record),
        "view_count": int(record.view_count or 0),
        "last_viewed_at": record.last_viewed_at,
    }


@router.post("/articles/{article_id}/share")
def create_article_share(
    article_id: str,
    params: ShareCreateParams,
    request: Request,
    session: Session = Depends(deps.get_session),
):
    """为一篇文章签发公开分享链接（免登录只读）。明文令牌随响应返回。"""
    username = _app().current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    if not article_share_service.public_share_enabled(session):
        raise HTTPException(status_code=403, detail="公开分享当前已关闭，可改用站内链接分享给同事")

    article_id = (article_id or "").strip()
    article = session.get(ArticleRecord, article_id) if article_id else None
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    # 与「读者面隐藏 = 内容交付全量排除」同口径：暂不可用的源不能被摊开成公开链接。
    if article.source_id in source_visibility_service.hidden_source_ids(session):
        raise HTTPException(status_code=403, detail="该内容暂不可用，无法分享")
    # 用户自定源(v3.40 检视返修 F3):非订阅者不得把私有源内容摊开成公开链接——
    # 分享是把内容公开化的动作,授权门槛高于按 id 直达的只读详情。
    _deny_unsubscribed_user_source_article(session, username, article)

    expires_in_days = params.expires_in_days
    if expires_in_days is not None:
        expires_in_days = int(expires_in_days)
        if expires_in_days not in article_share_service.SHARE_EXPIRY_CHOICES:
            raise HTTPException(status_code=400, detail="有效期取值不合法")

    if article_share_service.count_today(session, username) >= article_share_service.DAILY_SHARE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"今天创建的分享链接已达 {article_share_service.DAILY_SHARE_LIMIT} 条上限，请明天再试",
        )

    record = article_share_service.create_share(
        session, article_id=article_id, username=username, expires_in_days=expires_in_days,
    )
    return _serialize_share(record, article=article)


@router.get("/shares")
def list_article_shares(
    request: Request,
    article_id: Optional[str] = None,
    session: Session = Depends(deps.get_session),
):
    """当前用户签发过的分享链接（可按文章过滤），最新在前。"""
    username = _app().current_username(request)
    records = article_share_service.list_shares(session, username, article_id=article_id)
    titles: Dict[str, ArticleRecord] = {}
    for record in records:
        if record.article_id not in titles:
            titles[record.article_id] = session.get(ArticleRecord, record.article_id)
    return {
        "items": [_serialize_share(r, article=titles.get(r.article_id)) for r in records],
        "public_share_enabled": article_share_service.public_share_enabled(session),
    }


@router.delete("/shares/{share_id}")
def revoke_article_share(share_id: int, request: Request, session: Session = Depends(deps.get_session)):
    """撤销自己签发的分享链接（幂等）；链接立即失效。"""
    username = _app().current_username(request)
    record = article_share_service.revoke_share(session, share_id, username)
    if record is None:
        raise HTTPException(status_code=404, detail="分享不存在")
    return _serialize_share(record, article=session.get(ArticleRecord, record.article_id))


# ==================== 个人聚合接口令牌 ====================

@router.get("/feed-token")
def get_feed_token(request: Request, session: Session = Depends(deps.get_session)):
    """当前用户的个人聚合接口令牌状态（仅返回预览，不回显明文）。"""
    app = _app()
    username = app.current_username(request)
    record = session.get(ReaderFeedTokenRecord, username)
    subscribed = app.resolve_subscribed_source_ids(session, username)
    return {
        "exists": record is not None,
        "token_preview": record.token_preview if record else "",
        "created_at": record.created_at if record else None,
        "updated_at": record.updated_at if record else None,
        "subscribed_source_count": len(subscribed),
    }


@router.post("/feed-token/rotate")
def rotate_feed_token(request: Request, session: Session = Depends(deps.get_session)):
    """创建或轮换当前用户的个人聚合接口令牌；明文仅在本次响应中返回一次。"""
    app = _app()
    username = app.current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    token = generate_feed_token()
    now = _now_iso()
    record = session.get(ReaderFeedTokenRecord, username)
    if record is None:
        record = ReaderFeedTokenRecord(owner_username=username, created_at=now, updated_at=now)
    record.token_hash = hash_subscription_token(token)
    record.token_preview = subscription_token_preview(token)
    record.updated_at = now
    session.add(record)
    session.commit()
    return {"token": token, "token_preview": subscription_token_preview(token)}


# ==================== 内容源目录 ====================

@router.get("/sources")
def get_reader_sources(request: Request, session: Session = Depends(deps.get_session)):
    """读者层内容源目录：可订阅来源 = 所有已注册抓取源 ∪ 已归档来源 ∪ 已订阅来源。

    即便某个源历史产出为 0，它仍会出现在目录里，用户可提前订阅以接收其后续产出。
    """
    app = _app()
    username = app.current_username(request)
    app.ensure_default_subscriptions(username)
    registry_meta = _registry_source_meta()
    configured_meta = _configured_source_meta(session)
    source_meta = {**configured_meta, **registry_meta}
    x_user_caches = x_api_config_service.all_user_caches(session)
    rows = session.exec(
        select(
            ArticleRecord.source_id,
            ArticleRecord.content_type,
            func.count(ArticleRecord.id),
            func.max(ArticleRecord.fetched_date),
        )
        .where(ArticleRecord.source_id.isnot(None))
        .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
    ).all()
    # 目录用原始订阅并集(include_hidden):被隐藏的已订阅源要以「暂不可用」形态留在
    # 源栏(订阅是读者资产,静默消失像数据丢失);检索/列表/feed 范围仍走默认减隐藏的解析。
    subscribed_ids = set(
        app.resolve_subscribed_source_ids(session, username, include_hidden=True)
    )

    # 全站各源订阅人数（按 owner 去重）：发现页选源参考。扫描全部订阅行在当前
    # 账号规模下开销可忽略；一行订阅可含多个 source_id，逐一归集。
    subscriber_owners: Dict[str, set] = {}
    for record in session.exec(
        select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.is_active == True)  # noqa: E712
    ).all():
        for sid in subscription_source_ids(record):
            subscriber_owners.setdefault(sid, set()).add(record.owner_username or "")
    subscriber_counts = {sid: len(owners) for sid, owners in subscriber_owners.items()}

    by_source: Dict[str, Dict[str, Any]] = {}

    def _ensure_entry(source_id: str, content_type: Optional[str] = None) -> Dict[str, Any]:
        entry = by_source.get(source_id)
        if entry is None:
            meta = source_meta.get(source_id, {})
            if source_id == DAILY_BRIEF_SOURCE_ID:
                meta = {**DAILY_BRIEF_SOURCE_META, **meta}
            resolved_type = content_type or meta.get("content_type") or ""
            cached_profile = x_user_caches.get(source_id, {})
            entry = {
                "source_id": source_id,
                "name": meta.get("name") or _friendly_source_name(source_id, source_meta),
                "description": meta.get("desc", ""),
                "icon": meta.get("icon", ""),
                "source_owner": meta.get("source_owner", ""),
                "source_brand": meta.get("source_brand", ""),
                "source_scope": meta.get("source_scope", ""),
                "source_channel": meta.get("source_channel", ""),
                "provenance_tier": meta.get("provenance_tier", ""),
                "base_url": meta.get("base_url", ""),
                "content_tags": meta.get("content_tags", []),
                "content_type": resolved_type,
                "category": _source_category(resolved_type),
                "shape": source_shape(source_id, resolved_type, source_meta),
                "platform": meta.get("platform") or "",
                # 源栏优先使用 400x400 头像；同时保留原始 URL 供降级/调试。
                "avatar_url": cached_profile.get("author_avatar_url_large")
                or cached_profile.get("author_avatar_url") or "",
                "avatar_url_original": cached_profile.get("author_avatar_url") or "",
                "count": 0,
                "last_fetched": "",
                "subscriber_count": subscriber_counts.get(source_id, 0),
                "subscribed": source_id in subscribed_ids,
                "registered": source_id in registry_meta,
                "configured": source_id in configured_meta,
                "_primary_count": -1,
            }
            by_source[source_id] = entry
        return entry

    # 1. 所有已注册抓取源（含历史产出为 0 者，使新源可被提前订阅）。
    #    跳过 is_template 模板源（generic_rss / generic_x_timeline 等）：它们是
    #    source-config/source-builder 的执行基座、非可订阅的具体来源，前端节点目录
    #    已按 is_template 过滤，读者订阅目录同样不应出现「通用 XXX」。
    for source_id, meta in registry_meta.items():
        if meta.get("is_template"):
            continue
        _ensure_entry(source_id)

    # 1b. 所有 SourceConfig 源：即使尚未产出，也携带正确形态进入可订阅目录。
    for source_id in configured_meta:
        _ensure_entry(source_id)

    # 1c. 日报特殊源：即使尚未生成过日报也预先出现，便于提前订阅。
    _ensure_entry(DAILY_BRIEF_SOURCE_ID, "daily_brief")

    # 2. 叠加归档文章聚合（含未注册的导入源，如 social_post）；主 content_type 取计数最高者。
    #    已下线节点（删类后仍留有历史归档）不再回流目录，除非当前用户已订阅（保留退订入口），
    #    以保持读者层订阅目录与节点管理同步。
    for source_id, content_type, count, last_fetched in rows:
        if not source_id:
            continue
        if source_id in DECOMMISSIONED_FETCHER_IDS and source_id not in subscribed_ids:
            continue
        entry = _ensure_entry(source_id, content_type)
        entry["count"] += int(count or 0)
        if (last_fetched or "") > entry["last_fetched"]:
            entry["last_fetched"] = last_fetched or ""
        if int(count or 0) > entry["_primary_count"]:
            entry["_primary_count"] = int(count or 0)
            entry["content_type"] = content_type
            entry["category"] = _source_category(content_type)
            entry["shape"] = source_shape(source_id, content_type, source_meta)

    # 3. 已订阅但既未注册也无归档的来源也要出现，便于退订。
    for source_id in subscribed_ids:
        _ensure_entry(source_id)

    # 管理面隐藏的源:未订阅者完全不可见(发现页/目录不出现,也不可新订);已订阅者
    # 保留条目并标 hidden=True——前端呈「暂不可用」灰显形态,读者可退订也可留着等
    # 恢复(临时下架的常态结局是恢复,静默消失会像数据丢失)。内容交付(列表/未读/
    # feed/检索)仍全量排除,见 resolve_subscribed_source_ids。
    hidden_ids = source_visibility_service.hidden_source_ids(session)
    # 用户自定源(v3.40 私有):仅订阅者可见——非订阅者的目录/发现页完全不出现
    # (添加即订阅、移除即退订,故「我的自定源」恒在 subscribed_ids 内)。
    all_user_source_ids = user_sources_service.user_source_ids(session)
    sources = sorted(
        (
            {
                **{k: v for k, v in entry.items() if k != "_primary_count"},
                "hidden": entry["source_id"] in hidden_ids,
                "user_source": entry["source_id"] in all_user_source_ids,
            }
            for entry in by_source.values()
            if (entry["source_id"] not in hidden_ids or entry["source_id"] in subscribed_ids)
            and (entry["source_id"] not in all_user_source_ids or entry["source_id"] in subscribed_ids)
        ),
        key=lambda s: (s["category"], -s["count"], s["name"]),
    )
    return {
        "sources": sources,
        "subscribed_source_ids": sorted(subscribed_ids),
        "total_sources": len(sources),
    }


# ==================== 阅读器 AI（用户面：翻译 / 问答）====================

class ReaderTranslateParams(BaseModel):
    article_id: str


class ReaderChatTurn(BaseModel):
    role: str  # user | assistant
    content: str


class ReaderAskParams(BaseModel):
    question: str
    scope: str = "article"  # article | articles | subscription | all（后两档 v3.32；前端当前只露 article/subscription）
    article_id: Optional[str] = None
    article_ids: Optional[List[str]] = None  # scope=articles 的显式名单（≤ EXPLICIT_ARTICLES_MAX）
    history: Optional[List[ReaderChatTurn]] = None  # 多轮对话历史（纯文本问答，不含参考资料）
    ask_id: Optional[str] = None  # 可选：客户端生成的进度标识（阶段化等待态轮询用）


# ── ask 阶段进度（v3.32 阶段化等待态）──
# 进程内存字典：ask_id → {stage, detail, ts}。ask_id 由客户端生成（UUID 级随机），
# 内容只有阶段名与计数（低敏），完成即清；TTL 清扫防（客户端崩溃等）残留泄漏。
# 刻意不落库：这是单次请求内的瞬时观测面，与 daily_brief 的内存 progress 同范式。
_ASK_PROGRESS: Dict[str, Dict[str, Any]] = {}
_ASK_PROGRESS_TTL_SECONDS = 300
_ASK_PROGRESS_MAX_ENTRIES = 500


def _valid_ask_id(ask_id: Optional[str]) -> Optional[str]:
    """清洗客户端进度标识：非空、≤64、URL 安全字符集；不合法一律当没有。"""
    value = (ask_id or "").strip()
    if not value or len(value) > 64:
        return None
    if not all(c.isalnum() or c in "-_" for c in value):
        return None
    return value


def _ask_progress_update(ask_id: Optional[str], stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """登记一个阶段。条目内**累积全量阶段历史**(stages 列表)——瞬时阶段(如 FTS
    召回前后的 search)存活可能只有几毫秒,轮询采样必然漏拍;历史由服务端权威
    累积、GET 全量返回,前端才不会把「没轮询到」误判成「没发生」。"""
    if not ask_id:
        return
    now = datetime.datetime.now().timestamp()
    if len(_ASK_PROGRESS) >= _ASK_PROGRESS_MAX_ENTRIES:
        cutoff = now - _ASK_PROGRESS_TTL_SECONDS
        for stale in [k for k, v in _ASK_PROGRESS.items() if v.get("ts", 0) < cutoff]:
            _ASK_PROGRESS.pop(stale, None)
    entry = _ASK_PROGRESS.get(ask_id) or {"stages": []}
    stages = [s for s in entry["stages"] if s.get("stage") != stage]
    stages.append({"stage": stage, "detail": detail or {}})
    _ASK_PROGRESS[ask_id] = {"stage": stage, "detail": detail or {}, "stages": stages, "ts": now}


def _require_reader_ai(request: Request):
    """校验当前账户的 AI Beta 已开启且 LLM 已配置，返回 (username, llm_config)；否则 403/401。"""
    app = _app()
    username = app.current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    with Session(deps.get_db_sink().engine) as session:
        if not accounts_service.ai_beta_global_enabled(session):
            raise HTTPException(status_code=403, detail="AI 功能已临时关闭，请稍后再试")
        record = accounts_service.get_user(session, username)
        if record is None or not record.ai_beta_enabled:
            raise HTTPException(status_code=403, detail="AI 功能尚未开启，请联系管理员")
        # 全局日 token 预算(v3.34):与逐用户日调用限额互补,护全站总成本
        # (多账户/IM bot 代答渠道的放大器)。0=不限;超限当日全员 429,次日自复。
        if accounts_service.reader_ai_budget_exhausted(session):
            raise HTTPException(status_code=429, detail="今日 AI 用量已达全站上限，请明日再试")
        llm_config = daily_brief_service.resolve_llm_config(session)
    if not llm_config.configured:
        raise HTTPException(status_code=403, detail="AI 服务暂未就绪")
    return username, llm_config


# 读者 AI 逐用户每日配额（常量，可调）：护住共享 LLM 预算不被单账户刷爆。
# 计数复用 AiUsageRecord.calls，即底层 LLM 调用次数——translate 会按段并发多次调用，
# 故该额度更接近「若干篇整文翻译」而非固定篇数；ask 通常一问一次调用。
_AI_DAILY_CALL_LIMITS = {"translate": 50, "ask": 100}


def _enforce_ai_daily_quota(username: str, purpose: str) -> None:
    """按当日 AiUsageRecord 聚合的 calls 判该账户此用途是否超额；超则 429。

    admin 不豁免：配额护的是共享 LLM 预算/成本，与账户角色无关，统一限最简单可预期。
    请求前置校验（未产生 LLM 调用即拦），与 feedback「单日 10 条限额」同范式。
    """
    limit = _AI_DAILY_CALL_LIMITS.get(purpose)
    if not limit:
        return
    today = datetime.date.today().isoformat()
    with Session(deps.get_db_sink().engine) as session:
        used = session.exec(
            select(func.coalesce(func.sum(AiUsageRecord.calls), 0)).where(
                AiUsageRecord.day == today,
                AiUsageRecord.username == username,
                AiUsageRecord.purpose == purpose,
            )
        ).one()
    if int(used or 0) >= limit:
        raise HTTPException(status_code=429, detail="今日 AI 使用次数已达上限，请明日再试")


@router.post("/ai/translate")
async def reader_ai_translate(params: ReaderTranslateParams, request: Request):
    """把指定文章正文译为简体中文（结果缓存复用）。"""
    username, llm_config = _require_reader_ai(request)
    _enforce_ai_daily_quota(username, "translate")
    db_sink = deps.get_db_sink()
    with Session(db_sink.engine) as session:
        _deny_unsubscribed_user_source_article(
            session, username, session.get(ArticleRecord, params.article_id)
        )
    try:
        result = await reader_ai_service.translate_article(
            db_sink, params.article_id, llm_config,
            UsageMeta(purpose="translate", username=username),
        )
    except reader_ai_service.ReaderAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"翻译失败：{exc}")
    with Session(db_sink.engine) as session:
        accounts_service.record_ai_usage(session, username, "translate")
    return {"status": "success", **result}


@router.post("/ai/summarize")
async def reader_ai_summarize(params: ReaderTranslateParams, request: Request):
    """为指定文章生成中文要点摘要（结果缓存复用；入参形状与 translate 相同）。"""
    username, llm_config = _require_reader_ai(request)
    db_sink = deps.get_db_sink()
    with Session(db_sink.engine) as session:
        _deny_unsubscribed_user_source_article(
            session, username, session.get(ArticleRecord, params.article_id)
        )
    try:
        result = await reader_ai_service.summarize_article(
            db_sink, params.article_id, llm_config,
            UsageMeta(purpose="summarize", username=username),
        )
    except reader_ai_service.ReaderAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"摘要生成失败：{exc}")
    with Session(db_sink.engine) as session:
        accounts_service.record_ai_usage(session, username, "summarize")
    return {"status": "success", **result}


@router.get("/ai/ask/progress")
async def reader_ai_ask_progress(request: Request, ask_id: str = ""):
    """ask 请求的阶段进度（阶段化等待态轮询）。未知/已完成的 ask_id 返回 stage=None。"""
    app = _app()
    if not app.current_username(request):
        raise HTTPException(status_code=401, detail="需要登录")
    entry = _ASK_PROGRESS.get(_valid_ask_id(ask_id) or "")
    if not entry:
        return {"stage": None, "detail": {}, "stages": []}
    return {"stage": entry["stage"], "detail": entry["detail"], "stages": entry.get("stages", [])}


@router.post("/ai/ask")
async def reader_ai_ask(params: ReaderAskParams, request: Request):
    """基于指定文章名单或检索圈定的文章回答提问（v3.32 四档 scope）。

    上下文组装（graceful degrade）：
      - scope=article / articles：显式名单直取正文（零检索依赖，article 是 n=1 特例）；
      - scope=subscription / all：「LLM 计划检索 + FTS5」两段式（services/reader_search，
        v3.30 检索扶正波）——规划→FTS 召回→选篇→全文注入，降级链在管线内部自持
        （规划失败→原词检索→零命中→时序窗口）；两档只差检索域（订阅并集 vs 全库
        可见源，后者与发现页预览同口径，隐藏源照旧排除）。
    响应 sources 与上下文编号 [n] 同源同序（行内引用锚，见 reader_ai 核心层）。
    """
    app = _app()
    username, llm_config = _require_reader_ai(request)
    _enforce_ai_daily_quota(username, "ask")
    db_sink = deps.get_db_sink()
    scope = params.scope if params.scope in ("article", "articles", "subscription", "all") else "article"
    ask_id = _valid_ask_id(params.ask_id)

    # 用户源文章的动作级授权(检视返修 F3):显式名单档把正文送入外部 LLM,
    # 非订阅者的私有源文章按不存在处理(subscription/all 档的检索域天然不含)。
    if scope in ("article", "articles"):
        explicit_ids = [params.article_id] if scope == "article" else list(params.article_ids or [])
        with Session(db_sink.engine) as session:
            for aid in explicit_ids:
                if aid:
                    _deny_unsubscribed_user_source_article(
                        session, username, session.get(ArticleRecord, aid)
                    )

    # 上下文组装下沉到 reader_ai.assemble_reader_context；此处注入检索闭包
    # （承载鉴权作用域与 LLM 配置），使组装逻辑与 HTTP 请求解耦、可独立单测（D11）。
    # 检索的规划/选篇两次 LLM 调用计费归因并入 ask。
    async def _search_fetch(question: str, user: str):
        with Session(db_sink.engine) as session:
            if scope == "all":
                source_ids = app.resolve_all_visible_source_ids(session)
            else:
                source_ids = app.resolve_subscribed_source_ids(session, user)
        return await reader_search_service.subscription_context(
            question,
            engine=db_sink.engine,
            source_ids=source_ids,
            llm_config=llm_config,
            usage_meta=UsageMeta(purpose="ask", username=username),
            progress=lambda stage, detail=None: _ask_progress_update(ask_id, stage, detail),
            # 检索说明的语料称呼跟档位走:all 的语料不归属提问者,不能说成「订阅」
            # (IM 机器人等代答渠道即 all 档,v3.33.2)。
            corpus_label="哆啦美收录内容" if scope == "all" else "读者订阅内容",
            # 与作答侧同一份多轮历史:规划器据此还原追问/指代(v3.34)。
            history=params.history,
        )

    try:
        try:
            context, sources = await reader_ai_service.assemble_reader_context(
                scope=scope,
                question=params.question,
                article_id=params.article_id,
                article_ids=params.article_ids,
                username=username,
                db_sink=db_sink,
                search_fetch=_search_fetch,
            )
        except reader_ai_service.ReaderAIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

        _ask_progress_update(ask_id, "answer", {"articles": len(sources)})
        history = [{"role": t.role, "content": t.content} for t in (params.history or [])]
        try:
            answer = await reader_ai_service.answer_question(
                params.question, context, scope=scope, llm_config=llm_config, history=history,
                usage_meta=UsageMeta(purpose="ask", username=username),
            )
        except reader_ai_service.ReaderAIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=f"提问失败：{exc}")
    finally:
        if ask_id:
            _ASK_PROGRESS.pop(ask_id, None)
    with Session(db_sink.engine) as session:
        accounts_service.record_ai_usage(session, username, "ask")
    return {"status": "success", "answer": answer, "sources": sources, "scope": scope}
