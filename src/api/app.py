# /src/api/app.py

import asyncio
import json
import logging
import datetime
import base64
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Body, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field as PydanticField
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select
from sqlalchemy import func, case
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from apscheduler.triggers.cron import CronTrigger

from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage, SOURCE_FRIENDLY_NAMES
from pipeline.core import DataPipeline
from models.db import (
    ArticleRecord,
    CollectionJobRecord,
    CollectionJobRunRecord,
    FetchRunRecord,
    ReaderSubscriptionRecord,
    ReaderFeedTokenRecord,
    ReaderFavoriteRecord,
    ReaderReadRecord,
    SourceConfigRecord,
    SourceStateRecord,
    AppSettingRecord,
    UserRecord,
)
from models.content import BaseContent

# 引入动态抓取器注册中心
from fetchers.registry import fetcher_registry, DECOMMISSIONED_FETCHER_IDS
from api.skill_router import router as skill_router
from version import __version__
from api import deps
from api.security_checks import enforce_security_config
from api.serializers import serialize_user
from api.textutils import (
    _split_csv, _date_end_value, _now_iso, _json_loads, _json_dumps, _coerce_bool,
    _model_dump, _model_to_clean_dict,
)
from api.tokens import (
    AUTH_SECRET,
    normalize_delivery_policy,
    generate_subscription_token,
    hash_subscription_token,
    subscription_token_preview,
    generate_feed_token,
    read_bearer_or_query_token,
)
from api.feed_service import (
    serialize_subscription,
    resolve_subscription_by_token,
    query_subscription_articles,
    resolve_subscribed_source_ids,
    resolve_subscription_sources_by_token,
    resolve_feed_token_owner,
    feed_articles_for_owner,
)
from api.articles_view import (
    GenericContent,
    _record_to_content,
    apply_article_query_filters,
    article_recency_order,
    serialize_feed_article,
    serialize_article_list_item,
    article_to_markdown,
)
from api.sources import (
    DAILY_BRIEF_SOURCE_ID,
    DAILY_BRIEF_SOURCE_META,
    subscription_source_ids,
    _source_category,
    _registry_source_meta,
    _friendly_source_name,
)
from api.routers import accounts as accounts_router
from api.routers import admin as admin_router
from api.routers import daily_brief as daily_brief_router
from api.routers import reader as reader_router
from api.routers import ingest as ingest_router
from api.routers import subscriptions as subscriptions_router
from api.routers.subscriptions import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionFilters,
    SubscriptionDeliveryPolicy,
    PublicSubscriptionSearchBody,
)
from api.routers import articles as articles_router
from api.routers.articles import ArticleUpdateParams, _maybe_rewind_daily_brief_cursor
from api.schemas import BatchOpParams
from api.collection_planning import (
    normalize_fetcher_ids,
    resolve_collection_job_fetcher_ids,
    build_collection_job_items,
    apply_run_param_overrides,
    test_run_overrides,
    resolve_delivery_source_ids,
)
from api.routers import vector as vector_router
from api.routers.vector import (
    SearchQuery,
    RagContextQuery,
    AutoVectorizeConfig,
    run_vector_search,
    rag_context,
    auto_vectorize_after_fetch,
    is_auto_vectorize_enabled,
    enforced_search_scope,
    resolve_scoped_search_args,
    AUTO_VECTORIZE_SETTING_KEY,
)
from api.routers import monitoring as monitoring_router
from api.routers.monitoring import (
    derive_health_status,
    build_fetcher_health,
    build_fetcher_health_from_state,
)
from api.routers import x_api as x_api_router
from api.routers import source_configs as source_configs_router
from api.routers.source_configs import (
    SourceConfigCreate,
    SourceConfigUpdate,
    SourceFetchParams,
    serialize_source_config,
    normalize_source_id,
    parse_json_object,
    resolve_source_fetcher_id,
    build_source_fetch_params,
)
from api.routers import archive_sync as archive_sync_router
from api.routers.archive_sync import (
    ARCHIVE_SYNC_SCHEMA_VERSION,
    _canonical_json,
    archive_article_payload,
    archive_article_checksum,
    archive_sync_line,
    archive_manifest_line,
    build_import_article_record,
    import_archive_sync_jsonl,
)
from api.routers import collection as collection_router
from api.routers.collection import (
    CollectionJobCreate,
    CollectionJobUpdate,
    serialize_collection_job,
    serialize_collection_job_run,
)
from api.routers import fetchers as fetchers_router
from api.routers import stats as stats_router
from api.routers import media as media_router
from api.routers.fetchers import FetchBatchItem, FetchBatchParams
from services import daily_brief as daily_brief_service
from services import accounts as accounts_service
from services import reader_ai as reader_ai_service
from services import ai_usage as ai_usage_service
from services import reader_activity as reader_activity_service
from services import content_analytics as content_analytics_service
from services import jobs as jobs_service
from services.media_store import MediaStore
from llm.client import LLMNotConfigured, LLMError, UsageMeta, ping as llm_ping
from llm.client import set_usage_recorder as _set_llm_usage_recorder

from starlette.responses import JSONResponse as StarletteJSONResponse
from mcp_server import build_mcp_app
from config import settings

# 让应用自有的 dorami.* 日志输出到控制台（uvicorn 默认只配置自己的 logger，
# 根 logger 为 WARNING，导致 logger.info 不可见）。独立挂 handler、不向上传播避免重复。
_dorami_logger = logging.getLogger("dorami")
if not _dorami_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _dorami_logger.addHandler(_handler)
    _dorami_logger.setLevel(logging.INFO)
    _dorami_logger.propagate = False


settings.apply_process_environment()


# GenericContent / _record_to_content 已迁至 api/articles_view.py（共享）。


_mcp_enabled: bool = True


class MCPGateApp:
    """ASGI gate for /mcp — checks _mcp_enabled and delegates to the MCP ASGI app.
    _app is set in the lifespan so each restart gets a fresh FastMCP instance."""
    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not _mcp_enabled:
            response = StarletteJSONResponse(
                {"detail": "MCP server is disabled"}, status_code=503
            )
            await response(scope, receive, send)
            return
        if self._app is None:
            response = StarletteJSONResponse({"detail": "MCP server not ready"}, status_code=503)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


_mcp_gate = MCPGateApp()


def runtime_role() -> str:
    return settings.runtime.role


def runtime_collector_enabled() -> bool:
    return runtime_role() in {"all", "collector"}


def runtime_reader_enabled() -> bool:
    return runtime_role() in {"all", "reader"}


def collector_role_enabled(session: Optional[Dict[str, Any]] = None) -> bool:
    account_role = session.get("role") if session else None
    return runtime_collector_enabled() and (account_role in (None, "admin"))


def reader_role_enabled(session: Optional[Dict[str, Any]] = None) -> bool:
    # admin 为超级用户（采集+读者通吃）；user 为受限读者；二者都可访问 reader 面。
    account_role = session.get("role") if session else None
    return runtime_reader_enabled() and (account_role in (None, "admin", "user"))


def runtime_capabilities(session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ai_beta_enabled, llm_configured = _ai_capabilities(session)
    return {
        "version": __version__,
        "role": runtime_role(),
        "account_role": session.get("role") if session else None,
        "collector_enabled": collector_role_enabled(session),
        "reader_enabled": reader_role_enabled(session),
        "rag_enabled": settings.rag.enabled,
        # 用户面 AI（阅读器内翻译/问答）：该账户开关 AND LLM 已配置才视为可用。
        "ai_beta_enabled": ai_beta_enabled,
        "llm_configured": llm_configured,
    }


def _ai_capabilities(session: Optional[Dict[str, Any]] = None) -> tuple[bool, bool]:
    """返回 (该账户是否开启 AI Beta, LLM 是否已配置)。任一异常时降级为 False。"""
    username = str(session.get("sub")) if session else ""
    ai_beta_enabled = False
    llm_configured = False
    if db_sink is None:
        return ai_beta_enabled, llm_configured
    try:
        with Session(db_sink.engine) as db:
            llm_configured = daily_brief_service.resolve_llm_config(db).configured
            # 全局总开关关闭即视为该账户 AI 不可用（前端入口随之隐藏）。
            global_on = accounts_service.ai_beta_global_enabled(db)
            if username and global_on:
                record = accounts_service.get_user(db, username)
                ai_beta_enabled = bool(record and record.ai_beta_enabled)
    except Exception:  # 能力探测不应阻断 runtime 接口
        return False, False
    return ai_beta_enabled, llm_configured


COLLECTOR_API_PREFIXES = (
    "/api/fetchers",
    "/api/source-health",
    "/api/source-states",
    "/api/x-api",
    "/api/fetch-runs",
    # 注意：必须是 "/api/fetch"（不带尾斜杠）。_path_matches 用 startswith(prefix + "/")，
    # 若写成 "/api/fetch/" 会比对 "/api/fetch//"，导致 /api/fetch/{id}、/api/fetch/batch
    # 永远匹配不到、漏过 collector 鉴权（受限 reader 可越权触发采集）。"/api/fetch" 既能
    # 覆盖 /api/fetch/* 子路径，又不会误伤 /api/fetchers、/api/fetch-runs（它们各有独立条目）。
    "/api/fetch",
    "/api/archive/export",
    "/api/source-configs",
    "/api/source-builder",
    "/api/import/social-posts",
    "/api/collection-jobs",
    "/api/collection-job-runs",
    # 每日聚合统计(运行页点阵/台账与节点 sparkline)——采集面只读。
    "/api/stats",
    "/api/jobs",
    # LLM 配置与日报生成/配置归管理员（collector）。
    "/api/llm",
    "/api/daily-brief",
    # 向量构建/管理归管理员（collector）；/api/vector/search|stats|subscribed-stats 例外归 reader。
    "/api/vectorize",
    "/api/vector",
)

READER_API_PREFIXES = (
    "/api/archive/import",
    "/api/feed",
    "/api/mcp",
    "/api/reader",
    "/api/public/feed",
    "/api/public/subscriptions",
    "/api/rag",
    "/api/skill",
    "/api/subscriptions",
    # 仅检索/只读统计归 reader（用户侧）；其余 /api/vector/* 与 /api/vectorize/* 归 collector。
    "/api/vector/search",
    "/api/vector/stats",
    "/api/vector/subscribed-stats",
    # 媒体库图片代理（阅读器正文图经此取图）；/api/admin/media/* 由 admin 前缀独立裁决。
    "/api/media",
)


def _path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def disabled_runtime_surface(path: str, session: Optional[Dict[str, Any]] = None) -> Optional[str]:
    # Reader-surface paths短路判定：命中即只按 reader 权限裁决，不再落到 collector 检查，
    # 从而允许 reader/collector 前缀重叠（如 /api/vector/search 归 reader、/api/vector/* 归 collector）。
    is_reader_path = (
        path == "/mcp"
        or path.startswith("/mcp/")
        or _path_matches(path, READER_API_PREFIXES)
    )
    if is_reader_path:
        return None if reader_role_enabled(session) else "reader"
    if _path_matches(path, COLLECTOR_API_PREFIXES):
        return None if collector_role_enabled(session) else "collector"
    return None


def article_write_requires_collector(path: str, method: str) -> bool:
    normalized_method = method.upper()
    if path == "/api/articles" and normalized_method == "POST":
        return True
    if path == "/api/articles/batch-delete" and normalized_method == "POST":
        return True
    if path.startswith("/api/articles/") and normalized_method in {"PUT", "DELETE"}:
        return True
    return False


def archive_import_requires_admin(path: str, method: str) -> bool:
    return method.upper() == "POST" and _path_matches(path, ("/api/archive/import",))


def account_admin_required(path: str) -> bool:
    """账户/运维/X API 机密与计费管理一律仅限 admin，独立于 runtime 采集轴。"""
    return _path_matches(path, ("/api/accounts", "/api/admin", "/api/x-api"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_enabled
    print(f"🧭 Dorami runtime role: {runtime_role()}")

    # 安全配置校验（阶段4 D10）：生产姿态下关键漏配（secret 未设/占位、CORS 不安全组合）
    # 拒绝启动；开发姿态仅告警。
    enforce_security_config(settings)

    mcp = None
    if runtime_reader_enabled():
        # Init MCP enabled state from DB
        with Session(db_sink.engine) as session:
            rec = session.get(AppSettingRecord, "mcp_enabled")
            if rec is None:
                session.add(AppSettingRecord(key="mcp_enabled", value="true"))
                session.commit()
                _mcp_enabled = True
            else:
                _mcp_enabled = rec.value.lower() == "true"
        # Build fresh FastMCP instance (session_manager can only be run() once per instance)
        mcp = build_mcp_app(db_sink, vector_sink, subscription_resolver=resolve_subscription_sources_by_token)
        # vector_sink 为 None 时 MCP 内部会让向量类工具直接报「RAG disabled」
        _mcp_gate._app = mcp.streamable_http_app()
    else:
        _mcp_gate._app = None
        _mcp_enabled = False

    if runtime_collector_enabled():
        reconcile_orphaned_runs()
        load_tasks_to_scheduler()
        if scheduler.state == STATE_STOPPED:
            scheduler.start()
            print("⏰ APScheduler 定时调度引擎已启动！")
            # 仅在调度器新鲜启动（绑定当前事件循环）时注册巡检，避免跨 loop add_job。
            # RAG 开启时注册每日向量索引对账巡检（只报告、发现漂移告警，每日 04:00）。
            if vector_sink is not None:
                add_cron_job("vector_reconcile", execute_vector_reconcile_job, "0 4 * * *", [])
    else:
        print("⏸️ 当前 reader 运行角色不启动抓取调度引擎。")

    if mcp is not None:
        async with mcp.session_manager.run():
            yield
    else:
        yield
    _mcp_gate._app = None


app = FastAPI(title="Dorami 数据归档中枢 API", lifespan=lifespan)

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors.allow_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)

db_sink = DatabaseStorage(db_url=settings.storage.database_url)


def _record_llm_usage(meta: UsageMeta, usage: Dict[str, Any], model: str) -> None:
    """LLM 客户端计量回调：把一次调用的 token 用量写入 AiUsageRecord。

    计量绝不阻断主流程——客户端侧已吞异常，这里再兜一层。
    """
    try:
        with Session(db_sink.engine) as session:
            ai_usage_service.record_usage(
                session,
                username=meta.username,
                purpose=meta.purpose,
                model=model,
                usage=usage,
            )
    except Exception:  # noqa: BLE001
        logging.getLogger("dorami.llm").debug("AI 用量写库失败（忽略）", exc_info=True)


_set_llm_usage_recorder(_record_llm_usage)

# 首次启动（users 表为空）时，从 config 的 [auth] 播种初始账户；之后以数据库为准。
_seeded_accounts = accounts_service.seed_users_if_empty(db_sink.engine, settings.auth)
if _seeded_accounts:
    logging.getLogger("dorami.auth").info("👤 从配置播种了 %d 个初始账户", _seeded_accounts)
# 向量库默认按需创建：[rag] enabled = false 时不构造 ChromaVectorStorage，
# 后端启动既快且不占用 embedding 模型所需内存。开启后实例仍会懒加载模型权重。
vector_sink: Optional[ChromaVectorStorage] = (
    ChromaVectorStorage(db_path=settings.storage.chroma_path)
    if settings.rag.enabled else None
)
pipeline = DataPipeline(storages=[db_sink])

# 媒体库（图床）：[media] enabled = false 时为 None，代理端点 302 回源、抓取后不预取，
# 行为完全退回「外链直连」。归档正文原链从不改写，本地缓存只是显示层供给。
media_store: Optional[MediaStore] = (
    MediaStore(
        db_sink.engine,
        Path(settings.media.media_dir),
        max_bytes=settings.media.max_file_mb * 1024 * 1024,
        timeout_seconds=settings.media.timeout_seconds,
    )
    if settings.media.enabled else None
)

# 抓取后媒体预取的 fire-and-forget 任务强引用（asyncio 只保弱引用）。
_MEDIA_PREFETCH_TASKS: set = set()


def schedule_media_prefetch(article_ids: List[str]) -> None:
    """抓取入库钩子：异步预取新文章正文里的外链图片，绝不阻塞抓取主流程。"""
    store = media_store
    if store is None or not article_ids:
        return

    async def _run() -> None:
        try:
            counts = await store.prefetch_articles(
                article_ids, concurrency=settings.media.prefetch_concurrency
            )
            if counts.get("cached") or counts.get("failed"):
                logging.getLogger("dorami.media").info(
                    "📦 媒体预取完成: %d 篇文章, 缓存 %d, 失败 %d",
                    counts["articles"], counts["cached"], counts["failed"],
                )
        except Exception:  # noqa: BLE001
            logging.getLogger("dorami.media").warning("媒体预取任务异常", exc_info=True)

    task = asyncio.create_task(_run())
    _MEDIA_PREFETCH_TASKS.add(task)
    task.add_done_callback(_MEDIA_PREFETCH_TASKS.discard)


def require_vector_sink() -> ChromaVectorStorage:
    # 委托给统一的依赖提供者（deps.get_vector_sink），保持单一实现；
    # deps 动态读取 api.app.vector_sink，故测试 monkeypatch 仍生效。
    return deps.get_vector_sink()

app.mount("/mcp", _mcp_gate)
app.include_router(skill_router)
# 阶段1：按域迁出的 Router（路径保持不变；鉴权仍由中间件统一强制）。
app.include_router(accounts_router.router)
app.include_router(admin_router.router)
app.include_router(daily_brief_router.router)
app.include_router(reader_router.router)
app.include_router(ingest_router.router)
app.include_router(subscriptions_router.router)
app.include_router(articles_router.router)
app.include_router(vector_router.router)
app.include_router(monitoring_router.router)
app.include_router(x_api_router.router)
app.include_router(source_configs_router.router)
app.include_router(archive_sync_router.router)
app.include_router(collection_router.router)
app.include_router(stats_router.router)
app.include_router(fetchers_router.router)
app.include_router(media_router.router)

scheduler = AsyncIOScheduler()
COLLECTION_FETCH_CONCURRENCY = 4


# ==================== 管理员登录与会话 ====================
AUTH_COOKIE_NAME = settings.auth.cookie_name
AUTH_SESSION_SECONDS = settings.auth.session_seconds
# 账户已迁移到数据库（UserRecord）托管：登录与每请求会话校验均查库，config 的
# [auth] 仅作首次启动播种（见 seed_users_if_empty）。AUTH_SECRET 与订阅/聚合令牌
# helper 现以 api.tokens 为单一来源（上方 import re-export），保持原推导以兼容历史令牌。


class AuthLoginParams(BaseModel):
    username: str
    password: str


class ChangePasswordParams(BaseModel):
    current_password: str
    new_password: str


class AvatarUpdateParams(BaseModel):
    # data:image/* base64 URL；空字符串或 null 表示清除头像。
    avatar: Optional[str] = None


def _auth_cookie_secure() -> bool:
    return settings.auth.cookie_secure


def _b64encode_json(data: Dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(raw_value: str) -> Dict[str, Any]:
    padded = raw_value + "=" * (-len(raw_value) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(decoded.decode("utf-8"))


def _sign_auth_payload(payload: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()


def create_auth_token(username: str, role: str) -> str:
    payload = _b64encode_json({
        "sub": username,
        "role": role,
        "exp": int(time.time()) + AUTH_SESSION_SECONDS,
    })
    return f"{payload}.{_sign_auth_payload(payload)}"


def read_auth_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign_auth_payload(payload)):
        return None
    try:
        data = _b64decode_json(payload)
    except (ValueError, json.JSONDecodeError):
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    # 账户必须仍存在、处于启用状态，且角色与 token 内一致；否则会话立即失效
    # （管理员停用/删除/改角色后，对应 cookie 在下一次请求即被吊销）。
    with Session(db_sink.engine) as session:
        record = accounts_service.get_active_user(session, data.get("sub"))
        if record is None or data.get("role") != record.role:
            return None
    return data


def current_auth_session(request: Request) -> Optional[Dict[str, Any]]:
    return read_auth_token(request.cookies.get(AUTH_COOKIE_NAME))


def current_admin_session(request: Request) -> Optional[Dict[str, Any]]:
    return current_auth_session(request)


def current_username(request: Request) -> str:
    session = current_auth_session(request)
    return str(session.get("sub")) if session else ""


def set_auth_cookie(response: Response, username: str, role: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=create_auth_token(username, role),
        max_age=AUTH_SESSION_SECONDS,
        httponly=True,
        secure=_auth_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/", samesite="lax")


def is_public_auth_path(path: str) -> bool:
    return path in {"/api/auth/login", "/api/auth/logout", "/api/auth/session"}


def is_public_subscription_path(path: str) -> bool:
    # 所有 /api/public/* 消费端（按订阅令牌 / 个人聚合令牌鉴权）均无需登录会话。
    return path == "/api/public" or path.startswith("/api/public/")


@app.middleware("http")
async def require_admin_session(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or is_public_auth_path(path):
        return await call_next(request)
    if is_public_subscription_path(path):
        disabled_surface = disabled_runtime_surface(path)
        if disabled_surface:
            return StarletteJSONResponse(
                {
                    "detail": (
                        f"{disabled_surface} API surface is disabled for runtime role "
                        f"'{runtime_role()}'"
                    ),
                    **runtime_capabilities(),
                },
                status_code=403,
            )
        return await call_next(request)
    auth_session = current_auth_session(request)
    if path.startswith("/api/"):
        if auth_session is None:
            return StarletteJSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
    disabled_surface = disabled_runtime_surface(path, auth_session)
    if disabled_surface is None and account_admin_required(path):
        if (auth_session or {}).get("role") != "admin":
            return StarletteJSONResponse(
                {
                    "detail": "该操作需要管理员账号",
                    **runtime_capabilities(auth_session),
                },
                status_code=403,
            )
    if disabled_surface is None and article_write_requires_collector(path, request.method):
        disabled_surface = None if collector_role_enabled(auth_session) else "collector"
    if disabled_surface is None and archive_import_requires_admin(path, request.method):
        if (auth_session or {}).get("role") != "admin":
            return StarletteJSONResponse(
                {
                    "detail": "该操作需要管理员账号",
                    **runtime_capabilities(auth_session),
                },
                status_code=403,
            )
    if disabled_surface:
        return StarletteJSONResponse(
            {
                "detail": (
                    f"{disabled_surface} API surface is disabled for runtime role "
                    f"'{runtime_role()}'"
                ),
                **runtime_capabilities(auth_session),
            },
            status_code=403,
        )
    return await call_next(request)


def _auth_user_payload(username: str, role: str, avatar: Optional[str] = None) -> Dict[str, Any]:
    """登录态对外暴露的账户视图：含头像，供前端头像展示。"""
    return {"username": username, "role": role, "avatar": avatar or None}


@app.post("/api/auth/login")
def login_admin(params: AuthLoginParams, response: Response):
    username = params.username.strip()
    with Session(db_sink.engine) as session:
        record = accounts_service.get_user(session, username)
        # 用户不存在/已停用：仍跑一次占位校验抹平时序，避免用户枚举。
        if record is None or not record.is_active:
            accounts_service.verify_against_dummy(params.password)
            raise HTTPException(status_code=401, detail="账号或密码错误")
        if not accounts_service.verify_password(params.password, record.password_hash):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        role = record.role
        avatar = record.avatar
        accounts_service.touch_login(session, username)
    set_auth_cookie(response, username, role)
    return {"authenticated": True, "user": _auth_user_payload(username, role, avatar)}


@app.get("/api/auth/session")
def get_auth_session(request: Request):
    session = current_auth_session(request)
    if session is None:
        return {"authenticated": False, "user": None}
    with Session(db_sink.engine) as db_session:
        record = accounts_service.get_user(db_session, session["sub"])
        avatar = record.avatar if record else None
    return {"authenticated": True, "user": _auth_user_payload(session["sub"], session["role"], avatar)}


@app.get("/api/runtime")
def get_runtime(request: Request):
    return runtime_capabilities(current_auth_session(request))


@app.post("/api/auth/logout")
def logout_admin(response: Response):
    clear_auth_cookie(response)
    return {"authenticated": False}


@app.post("/api/auth/change-password")
def change_own_password(params: ChangePasswordParams, request: Request):
    """任意已登录账户修改自己的登录密码（需校验旧密码）。"""
    username = current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    if not params.new_password:
        raise HTTPException(status_code=400, detail="新密码不能为空")
    with Session(db_sink.engine) as session:
        record = accounts_service.get_active_user(session, username)
        if record is None:
            raise HTTPException(status_code=401, detail="账户不存在或已停用")
        if not accounts_service.verify_password(params.current_password, record.password_hash):
            raise HTTPException(status_code=400, detail="当前密码错误")
        accounts_service.set_password(session, username, params.new_password)
    return {"ok": True}


# 头像上限：~1.5MB base64 字符串（约 1MB 原图），足够 256px 缩略图，且不撑爆会话/数据库。
_AVATAR_MAX_CHARS = 1_500_000


@app.post("/api/auth/avatar")
def update_own_avatar(params: AvatarUpdateParams, request: Request):
    """任意已登录账户更新/清除自己的头像（data:image/* base64 URL）。"""
    username = current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    avatar = (params.avatar or "").strip()
    if avatar:
        if not avatar.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="头像必须是 data:image/* 格式的图片")
        if len(avatar) > _AVATAR_MAX_CHARS:
            raise HTTPException(status_code=400, detail="头像体积过大，请上传更小的图片")
    with Session(db_sink.engine) as session:
        record = accounts_service.get_active_user(session, username)
        if record is None:
            raise HTTPException(status_code=401, detail="账户不存在或已停用")
        record = accounts_service.set_avatar(session, username, avatar or None)
        result = _auth_user_payload(record.username, record.role, record.avatar)
    return {"ok": True, "user": result}


# ==================== 账户管理（仅 admin） ====================
# 账户管理端点已迁出至 api/routers/accounts.py（见 app.include_router）。
# admin 网关仍由 require_admin_session 中间件统一强制（account_admin_required 命中 /api/accounts）。
# 账户对外视图序列化迁至 api.serializers.serialize_user（与运维视图共享）。


# ==================== 运维管理面板（仅 admin） ====================
# 运维端点已迁出至 api/routers/admin.py（见 app.include_router）。
# admin 网关仍由 require_admin_session 中间件统一强制（account_admin_required 命中 /api/admin）。


# ==================== 定时任务系统核心逻辑 ====================
# _now_iso 已迁至 api/textutils.py。


def reconcile_orphaned_runs() -> Dict[str, int]:
    """启动自愈：把上次进程残留的「运行中」记录标记为失败。

    抓取运行的进度只存活于进程内存，进程被杀/重启后任何仍是 ``running`` 的
    ``fetch_runs`` / ``collection_job_runs`` 都成了永不收尾的孤儿（前端会一直显示
    「运行中」）。启动时统一把它们标记为 ``failed`` 并补齐 ``ended_at``/``duration_ms``，
    源状态从 ``running`` 降级为 ``unknown``（结果未知）。幂等：无残留时不做任何写入。
    """
    now = _now_iso()
    counts = {"fetch_runs": 0, "job_runs": 0, "source_states": 0}
    note = "后端重启，运行被中断（启动自愈标记）"

    def _dur(started_at: Optional[str]) -> Optional[int]:
        try:
            delta = datetime.datetime.fromisoformat(now) - datetime.datetime.fromisoformat(started_at)
            return int(delta.total_seconds() * 1000)
        except (TypeError, ValueError):
            return None

    with Session(db_sink.engine) as session:
        for run in session.exec(select(FetchRunRecord).where(FetchRunRecord.status == "running")).all():
            run.status = "failed"
            run.ended_at = now
            run.duration_ms = _dur(run.started_at)
            if not run.error_message:
                run.error_message = note
            session.add(run)
            counts["fetch_runs"] += 1
        for job_run in session.exec(select(CollectionJobRunRecord).where(CollectionJobRunRecord.status == "running")).all():
            job_run.status = "failed"
            job_run.ended_at = now
            job_run.duration_ms = _dur(job_run.started_at)
            if not job_run.error_message:
                job_run.error_message = note
            session.add(job_run)
            counts["job_runs"] += 1
        for state in session.exec(select(SourceStateRecord).where(SourceStateRecord.status == "running")).all():
            state.status = "unknown"
            state.updated_at = now
            session.add(state)
            counts["source_states"] += 1
        session.commit()

    if any(counts.values()):
        print(
            f"🧹 启动自愈：清理 {counts['fetch_runs']} 条中断抓取运行、"
            f"{counts['job_runs']} 条采集任务运行、{counts['source_states']} 条源状态。"
        )
    return counts


# _json_dumps 已迁至 api/textutils.py。


# _json_loads 已迁至 api/textutils.py。


# _split_csv 已迁至 api/textutils.py（共享，re-export 见顶部 import）。


# _date_end_value 已迁至 api/textutils.py。


# article_recency_order 已迁至 api/articles_view.py（共享，re-export 见顶部 import）。


# apply_article_query_filters / serialize_feed_article / serialize_article_list_item /
# article_to_markdown 已迁至 api/articles_view.py（共享，re-export 见顶部 import）。


# _model_dump / _model_to_clean_dict 已迁至 api.textutils（通用 pydantic 工具，re-export 见顶部 import）。


# normalize_delivery_policy / generate_subscription_token / hash_subscription_token /
# subscription_token_preview / read_bearer_or_query_token / generate_feed_token：
# 已迁出至 api.tokens（上方 import re-export，保持 api.app.X 兼容）。


# serialize_subscription / resolve_subscription_by_token / query_subscription_articles /
# resolve_subscribed_source_ids / resolve_subscription_sources_by_token /
# resolve_feed_token_owner / feed_articles_for_owner：
# 已迁出至 api.feed_service（下方 import re-export，保持 api.app.X 兼容）。


# 归档同步 helper（ARCHIVE_SYNC_SCHEMA_VERSION/_canonical_json/archive_article_payload/
# archive_article_checksum/archive_sync_line/archive_manifest_line/build_import_article_record/
# import_archive_sync_jsonl 及 _coerce_optional_int）已迁至 api/routers/archive_sync.py
# （下方 import re-export，保持 api.app.X 兼容）。


# serialize_collection_job / serialize_collection_job_run
# 已迁至 api/routers/collection.py（下方 import re-export）。


# normalize_fetcher_ids / resolve_collection_job_fetcher_ids / build_collection_job_items /
# apply_run_param_overrides / test_run_overrides /
# resolve_delivery_source_ids 已迁至 api/collection_planning.py（共享，下方 import re-export）。


def create_collection_job_run(
        name: str,
        trigger_type: str,
        node_count: int,
        job_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = CollectionJobRunRecord(
            job_id=job_id,
            run_scope=run_scope,
            trigger_type=trigger_type,
            status="running",
            name=name,
            node_count=node_count,
            started_at=_now_iso(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def finish_collection_job_run(
        job_run_id: int,
        status: str,
        child_run_ids: Optional[List[int]] = None,
        fetched_count: int = 0,
        saved_count: int = 0,
        skipped_count: int = 0,
        failed_count: int = 0,
        error_message: Optional[str] = None,
):
    ended_at = _now_iso()
    with Session(db_sink.engine) as session:
        run = session.get(CollectionJobRunRecord, job_run_id)
        if not run:
            return
        if not child_run_ids:
            child_run_ids = [
                item.id for item in session.exec(
                    select(FetchRunRecord).where(FetchRunRecord.job_run_id == job_run_id)
                ).all()
                if item.id is not None
            ]
        run.status = status
        run.ended_at = ended_at
        run.child_run_ids_json = _json_dumps(child_run_ids or [])
        run.fetched_count = fetched_count
        run.saved_count = saved_count
        run.skipped_count = skipped_count
        run.failed_count = failed_count
        run.error_message = error_message
        try:
            started_at = datetime.datetime.fromisoformat(run.started_at)
            ended_dt = datetime.datetime.fromisoformat(ended_at)
            run.duration_ms = int((ended_dt - started_at).total_seconds() * 1000)
        except ValueError:
            run.duration_ms = None
        session.add(run)
        session.commit()


def create_fetch_run(
        fetcher_id: str,
        params: dict,
        trigger_type: str,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = FetchRunRecord(
            fetcher_id=fetcher_id,
            job_id=job_id,
            job_run_id=job_run_id,
            run_scope=run_scope,
            trigger_type=trigger_type,
            status="running",
            params_json=_json_dumps(params),
            started_at=_now_iso()
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def finish_fetch_run(run_id: int, status: str, result: Any = None, error_message: Optional[str] = None):
    ended_at = _now_iso()
    with Session(db_sink.engine) as session:
        run = session.get(FetchRunRecord, run_id)
        if not run:
            return

        run.status = status
        run.ended_at = ended_at
        run.error_message = error_message
        try:
            started_at = datetime.datetime.fromisoformat(run.started_at)
            ended_dt = datetime.datetime.fromisoformat(ended_at)
            run.duration_ms = int((ended_dt - started_at).total_seconds() * 1000)
        except ValueError:
            run.duration_ms = None

        if result:
            run.fetched_count = getattr(result, "fetched_count", 0)
            run.saved_count = getattr(result, "saved_count", 0)
            run.skipped_count = getattr(result, "skipped_count", 0)

        session.add(run)
        session.commit()


async def execute_collection_job(job_id: int):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job or not job.is_active:
            print(f"⚠️ 采集任务不可用或已停用: {job_id}")
            return
        items = build_collection_job_items(job)
        job_name = job.name

    print(f"⏰ 采集任务触发: {job_name} ({len(items)} 个节点)")
    await run_collection_items(
        items,
        name=job_name,
        trigger_type="scheduled",
        job_id=job_id,
        run_scope="saved_job",
    )


def add_cron_job(job_id: str, callback, cron_expr: str, args: List[Any]):
    parts = cron_expr.split()
    if len(parts) != 5:
        return
    trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
    scheduler.add_job(callback, trigger, args=args, id=job_id, replace_existing=True)


def load_tasks_to_scheduler():
    scheduler.remove_all_jobs()
    with Session(db_sink.engine) as session:
        jobs = session.exec(
            select(CollectionJobRecord)
            .where(CollectionJobRecord.is_active == True)
        ).all()
        for job in jobs:
            # 单节点 cron 覆盖已退役:一任务一 cron(想要不同节奏 = 建新任务)
            if job.cron_expr:
                add_cron_job(f"collection_job_{job.id}", execute_collection_job, job.cron_expr, [job.id])
        # 每日 AI 资讯日报（独立于采集任务，默认排在全量采集之后）
        if daily_brief_service.daily_brief_enabled(session):
            add_cron_job(
                "daily_brief",
                execute_daily_brief_job,
                daily_brief_service.daily_brief_cron(session),
                [],
            )


async def execute_daily_brief_job():
    """定时回调：生成每日日报。失败仅记录，不影响调度引擎。"""
    print("⏰ 定时任务触发: 正在生成每日 AI 资讯日报...")
    try:
        result = await daily_brief_service.generate_daily_brief(storage=db_sink, trigger="scheduled")
        await auto_vectorize_after_fetch([result["article_id"]] if result.get("article_id") else [])
        print(f"✅ 日报生成完成: {result.get('status')} ({result.get('article_id')})")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 日报生成失败: {exc}")
        try:
            with Session(db_sink.engine) as session:
                daily_brief_service.set_json_setting(session, daily_brief_service.KEY_LAST_RUN, {
                    "status": "failed",
                    "ended_at": datetime.datetime.now().isoformat(),
                    "error_message": str(exc),
                })
        except Exception:  # noqa: BLE001
            pass


async def execute_vector_reconcile_job():
    """定时巡检：SQLite↔Chroma 向量索引对账（只报告、不自动修复）。

    发现漂移仅告警（管理员可用 POST /api/vector/reconcile 择时修复），避免定时任务
    静默改动数据。RAG 关闭（vector_sink 为 None）时不注册本任务，故此处必有 sink。
    """
    from services import vector_reconcile
    try:
        report = await vector_reconcile.reconcile(db_sink, vector_sink, repair=False)
    except Exception as exc:  # noqa: BLE001 巡检失败不影响调度引擎
        _dorami_logger.error("向量索引对账巡检失败: %s", exc)
        return
    if report.get("in_sync"):
        _dorami_logger.info("向量索引对账巡检: 一致（db=%s, chroma=%s）",
                     report.get("db_total"), report.get("chroma_parents"))
    else:
        _dorami_logger.warning(
            "向量索引对账巡检发现漂移: 丢索引=%s 未标记=%s 孤儿chunk=%s（可 POST /api/vector/reconcile 修复）",
            report["flagged_but_absent"]["count"],
            report["present_but_unflagged"]["count"],
            report["orphan_chunks"]["count"],
        )


def reload_daily_brief_schedule():
    """日报配置变更后热生效：精准增删 daily_brief 这一个 job。"""
    with Session(db_sink.engine) as session:
        enabled = daily_brief_service.daily_brief_enabled(session)
        cron_expr = daily_brief_service.daily_brief_cron(session)
    if enabled:
        add_cron_job("daily_brief", execute_daily_brief_job, cron_expr, [])
    elif scheduler.get_job("daily_brief"):
        scheduler.remove_job("daily_brief")


# ==================== 1. 数据台账与 CRUD ====================
# BatchOpParams 已迁至 api/schemas.py（下方 import re-export，供 articles/vector 共用）。


# SourceConfigCreate / SourceConfigUpdate / SourceFetchParams 已迁至
# api/routers/source_configs.py（下方 import re-export）。
# SourceBuilder* 请求模型已迁出至 api/routers/ingest.py。


# FetchBatchItem / FetchBatchParams 已迁至 api/routers/fetchers.py（下方 import re-export）。
# SocialPostImport* 请求模型已迁出至 api/routers/ingest.py。


# SubscriptionFilters / SubscriptionDeliveryPolicy / SubscriptionCreate /
# SubscriptionUpdate 已迁至 api/routers/subscriptions.py（下方 import re-export）。


# serialize_source_config / normalize_source_id / parse_json_object /
# resolve_source_fetcher_id / build_source_fetch_params 已迁至
# api/routers/source_configs.py（下方 import re-export）。


# normalize_social_source_id / build_social_post_content 已迁出至 api/routers/ingest.py。


def resolve_state_source_id(fetcher_id: str, params: Optional[Dict[str, Any]], result: Any = None) -> str:
    if result and getattr(result, "latest_content_source_id", ""):
        return result.latest_content_source_id
    if params:
        source_id = str(params.get("source_id", "")).strip()
        if source_id:
            return source_id
    return fetcher_id


def classify_error(error: Exception | str | None) -> str:
    if not error:
        return ""
    message = str(error).lower()
    if "unknown" in message or "未知" in message:
        return "configuration_error"
    if "timeout" in message or "connect" in message or "network" in message:
        return "network_error"
    if "http" in message or "status" in message:
        return "http_error"
    if "parse" in message or "解析" in message:
        return "parse_error"
    return error.__class__.__name__ if isinstance(error, Exception) else "runtime_error"


def mark_source_state_started(fetcher_id: str, params: Dict[str, Any], run_id: int):
    source_id = resolve_state_source_id(fetcher_id, params)
    now = _now_iso()
    with Session(db_sink.engine) as session:
        state = session.get(SourceStateRecord, source_id)
        if not state:
            state = SourceStateRecord(
                source_id=source_id,
                fetcher_id=fetcher_id,
                status="running",
                last_started_at=now,
                last_run_id=run_id,
                updated_at=now
            )
        else:
            state.fetcher_id = fetcher_id
            state.status = "running"
            state.last_started_at = now
            state.last_run_id = run_id
            state.updated_at = now
        session.add(state)
        session.commit()


def mark_source_state_finished(
        fetcher_id: str,
        params: Dict[str, Any],
        run_id: int,
        status: str,
        result: Any = None,
        error: Exception | str | None = None
):
    source_id = resolve_state_source_id(fetcher_id, params, result)
    now = _now_iso()
    with Session(db_sink.engine) as session:
        state = session.get(SourceStateRecord, source_id)
        if not state:
            state = SourceStateRecord(
                source_id=source_id,
                fetcher_id=fetcher_id,
                status="unknown",
                last_run_id=run_id,
                updated_at=now
            )

        state.fetcher_id = fetcher_id
        state.last_run_id = run_id
        state.last_completed_at = now
        state.total_runs += 1
        state.latest_fetched_count = getattr(result, "fetched_count", 0) if result else 0
        state.latest_saved_count = getattr(result, "saved_count", 0) if result else 0
        state.latest_skipped_count = getattr(result, "skipped_count", 0) if result else 0

        if status == "success":
            state.status = "healthy"
            state.last_success_at = now
            state.success_runs += 1
            state.consecutive_failures = 0
            state.latest_error_type = ""
            state.latest_error_message = None
            latest_content_id = getattr(result, "latest_content_id", "") if result else ""
            latest_cursor_value = getattr(result, "latest_cursor_value", "") if result else ""
            latest_publish_date = getattr(result, "latest_content_publish_date", "") if result else ""
            if latest_content_id:
                state.last_content_id = latest_content_id
                state.last_cursor_value = latest_cursor_value or latest_content_id
            if latest_publish_date:
                state.last_cursor_date = latest_publish_date
            if result and getattr(result, "latest_content_type", ""):
                state.content_type = result.latest_content_type
        else:
            state.status = "failing"
            state.last_failure_at = now
            state.failed_runs += 1
            state.consecutive_failures += 1
            state.latest_error_type = classify_error(error)
            state.latest_error_message = str(error) if error else None

        state.updated_at = now
        session.add(state)
        session.commit()


# derive_health_status / build_fetcher_health / build_fetcher_health_from_state
# 已迁至 api/routers/monitoring.py（共享健康汇总 helper，re-export 见顶部 import）。


async def run_fetcher_with_tracking(
        fetcher_id: str,
        params: Dict[str, Any],
        trigger_type: str = "manual",
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> Dict[str, Any]:
    run_id = create_fetch_run(
        fetcher_id,
        params,
        trigger_type=trigger_type,
        job_id=job_id,
        job_run_id=job_run_id,
        run_scope=run_scope,
    )
    mark_source_state_started(fetcher_id, params, run_id)
    fetcher_class = fetcher_registry.get_class(fetcher_id)
    if not fetcher_class:
        message = f"未知的抓取器节点: {fetcher_id}"
        finish_fetch_run(run_id, status="failed", error_message=message)
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=message)
        raise ValueError(message)

    try:
        fetcher = fetcher_class()
        result = await pipeline.run_task(
            fetcher,
            lineage={
                "fetch_run_id": run_id,
                "job_id": job_id,
                "job_run_id": job_run_id,
                "run_scope": run_scope,
            },
            **params,
        )
        finish_fetch_run(run_id, status="success", result=result)
        mark_source_state_finished(fetcher_id, params, run_id, status="success", result=result)
        await auto_vectorize_after_fetch(result.saved_content_ids)
        schedule_media_prefetch(result.saved_content_ids)
        return {
            "status": "success",
            "run_id": run_id,
            "job_id": job_id,
            "job_run_id": job_run_id,
            "fetcher_id": fetcher_id,
            "fetched_count": result.fetched_count,
            "saved_count": result.saved_count,
            "skipped_count": result.skipped_count,
            "saved_content_ids": result.saved_content_ids,
        }
    except Exception as e:
        finish_fetch_run(run_id, status="failed", error_message=str(e))
        mark_source_state_finished(fetcher_id, params, run_id, status="failed", error=e)
        raise


async def run_single_fetch_as_collection(
        fetcher_id: str,
        params: Dict[str, Any],
        name: str,
        trigger_type: str = "manual",
        run_scope: str = "ad_hoc",
        job_id: Optional[int] = None,
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=1,
        job_id=job_id,
        run_scope=run_scope,
    )
    try:
        result = await run_fetcher_with_tracking(
            fetcher_id,
            params,
            trigger_type=trigger_type,
            job_id=job_id,
            job_run_id=job_run_id,
            run_scope=run_scope,
        )
        finish_collection_job_run(
            job_run_id,
            status="success",
            child_run_ids=[result["run_id"]],
            fetched_count=result["fetched_count"],
            saved_count=result["saved_count"],
            skipped_count=result["skipped_count"],
        )
        return {**result, "job_run_id": job_run_id}
    except Exception as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        raise


async def run_collection_items(
        items: List[Dict[str, Any]],
        name: str,
        trigger_type: str = "manual",
        job_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
        max_concurrency: Optional[int] = None,
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=len(items),
        job_id=job_id,
        run_scope=run_scope,
    )
    concurrency = max(int(max_concurrency or COLLECTION_FETCH_CONCURRENCY), 1)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(item: Dict[str, Any]) -> Dict[str, Any]:
        fetcher_id = str(item.get("fetcher_id", "")).strip()
        params = item.get("params") or {}
        if not fetcher_id:
            return {"fetcher_id": fetcher_id, "status": "failed", "error": "空节点 ID"}
        try:
            async with semaphore:
                return await run_fetcher_with_tracking(
                    fetcher_id,
                    params,
                    trigger_type=trigger_type,
                    job_id=job_id,
                    job_run_id=job_run_id,
                    run_scope=run_scope,
                )
        except Exception as e:
            return {"fetcher_id": fetcher_id, "status": "failed", "error": str(e)}

    results = await asyncio.gather(*(run_one(item) for item in items))
    child_run_ids = [result["run_id"] for result in results if result.get("run_id") is not None]
    fetched_count = sum(result.get("fetched_count", 0) for result in results)
    saved_count = sum(result.get("saved_count", 0) for result in results)
    skipped_count = sum(result.get("skipped_count", 0) for result in results)
    failed_count = sum(1 for result in results if result.get("status") == "failed")
    errors = [
        f"{result.get('fetcher_id', '')}: {result.get('error')}"
        for result in results
        if result.get("status") == "failed" and result.get("error")
    ]
    saved_content_ids = [
        content_id
        for result in results
        for content_id in result.get("saved_content_ids", [])
    ]

    status = "success"
    if failed_count and failed_count == len(items):
        status = "failed"
    elif failed_count:
        status = "partial_failed"

    finish_collection_job_run(
        job_run_id,
        status=status,
        child_run_ids=child_run_ids,
        fetched_count=fetched_count,
        saved_count=saved_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        error_message="; ".join(errors[:3]) if errors else None,
    )
    return {
        "status": status,
        "job_id": job_id,
        "job_run_id": job_run_id,
        "count": len(items),
        "fetched_count": fetched_count,
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "error_message": "; ".join(errors[:3]) if errors else None,
        "saved_content_ids": saved_content_ids,
        "results": results,
    }


# ==================== 内容源目录（reader 面）====================
# GET /api/reader/sources 已迁出至 api/routers/reader.py。
# 订阅创建辅助（_create_single_source_subscription / ensure_default_subscriptions）留守，
# 被订阅生命周期与 reader Router 共用。


def _create_single_source_subscription(session: Session, username: str, source_id: str, name: str) -> None:
    """创建一个仅含 source_id 的单源订阅（不提交，由调用方统一 commit）。"""
    token = generate_subscription_token()
    now = _now_iso()
    record = ReaderSubscriptionRecord(
        owner_username=username,
        name=name,
        description="",
        filters_json=_json_dumps({"source_ids": source_id, "has_content": True}),
        delivery_policy_json=_json_dumps(normalize_delivery_policy()),
        token_hash=hash_subscription_token(token),
        token_preview=subscription_token_preview(token),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(record)


# 新读者账号默认自带的订阅源（可随时取消，且取消后不会被再次播种）。
DEFAULT_SUBSCRIPTION_SOURCE_IDS = [DAILY_BRIEF_SOURCE_ID]
DEFAULTS_SEEDED_KEY_PREFIX = "reader_defaults_seeded"


def ensure_default_subscriptions(username: str) -> None:
    """为读者账号首次播种默认订阅（仅一次）。

    由读者门控端点调用，故调用方即可读账号；用每用户一次的 KV 标记守卫，
    保证取消默认订阅后不会被重新加回。
    """
    if not username:
        return
    key = f"{DEFAULTS_SEEDED_KEY_PREFIX}:{username}"
    registry_meta = _registry_source_meta()
    with Session(db_sink.engine) as session:
        if daily_brief_service.get_setting(session, key, ""):
            return
        existing = set(resolve_subscribed_source_ids(session, username))
        for source_id in DEFAULT_SUBSCRIPTION_SOURCE_IDS:
            if source_id in existing:
                continue
            if source_id == DAILY_BRIEF_SOURCE_ID:
                name = DAILY_BRIEF_SOURCE_META["name"]
            else:
                name = _friendly_source_name(source_id, registry_meta)
            _create_single_source_subscription(session, username, source_id, name)
        # set_setting 内部 commit，会一并提交上面新增的订阅记录。
        daily_brief_service.set_setting(session, key, _now_iso())


# ==================== 阅读器订阅/收藏/计量（reader 面）====================
# 一键订阅/退订、阅读计量、收藏增删查、个人聚合令牌已迁出至 api/routers/reader.py
# （见 app.include_router）。reader 网关仍由中间件统一强制（READER_API_PREFIXES 含 /api/reader）。

# ==================== 阅读器 AI（用户面：翻译 / 问答）====================
# translate/ask 已迁出至 api/routers/reader.py
#（_require_reader_ai / _recent_subscribed_articles 随迁）。
# 订阅生命周期（/api/subscriptions/*）、单订阅令牌拉取/检索
# （/api/public/subscriptions/{id}/articles|vector/search）、个人聚合拉取
# （/api/public/feed/articles[.md]）已迁出至 api/routers/subscriptions.py
# （含 SubscriptionCreate/Update/Filters/DeliveryPolicy/PublicSubscriptionSearchBody
#  与 _owned_subscription_or_404；见 app.include_router）。


# ==================== 0. 数据源配置 ====================
# /api/source-configs* 的 9 个端点（CRUD/toggle/fetch + fetch-active-rss/web）
# 已迁出至 api/routers/source_configs.py（含 SourceConfigCreate/Update/SourceFetchParams
# 与 serialize_source_config/normalize_source_id/parse_json_object/resolve_source_fetcher_id/
# build_source_fetch_params；见 app.include_router）。


# ==================== 数据接入（source-builder / import）====================
# /api/source-builder/* 与 /api/import/social-posts 已迁出至 api/routers/ingest.py
# （见 app.include_router）。collector 网关仍由中间件统一强制。


# /api/archive/export|import/articles.jsonl 已迁出至 api/routers/archive_sync.py
# （见 app.include_router）。


# GET /api/articles（列表/查询，含订阅作用域）已迁出至 api/routers/articles.py
# （见 app.include_router）。下方 /api/feed/articles[.md] 暂留（依赖采集投递作用域 helper）。


# /api/feed/articles[.md]（投递视图）已迁出至 api/routers/articles.py
# （依赖 collection_planning.resolve_delivery_source_ids；见 app.include_router）。


# 单条读取/手工录入/更新/删除/批量删除（GET|POST|PUT|DELETE /api/articles*）
# 与 ArticleUpdateParams、_maybe_rewind_daily_brief_cursor 已迁出至
# api/routers/articles.py（见 app.include_router）。


# ==================== 2. 调度与抓取 (注册中心化) ====================

# 抓取器目录与即时触发 —— GET /api/fetchers、POST /api/fetch/batch、
# POST /api/fetch/{fetcher_id}（含 FetchBatchItem/Params）已迁出至
# api/routers/fetchers.py（抓取核心 run_collection_items/run_single_fetch_as_collection
# 仍留守本文件，经其 _app() 调用。见 app.include_router）。
# GET /api/source-health 等监控端点见 api/routers/monitoring.py。


# ==================== LLM 配置 & 每日日报（collector/admin） ====================
# 端点已迁出至 api/routers/daily_brief.py（见 app.include_router）。
# collector 网关仍由中间件统一强制（COLLECTOR_API_PREFIXES 含 /api/llm、/api/daily-brief）。


# 向量化(单条/批量/all-pending)、自动向量化开关、向量检索/统计/删除、RAG 上下文/相似、
# 全库重建索引，及 SearchQuery/RagContextQuery/AutoVectorizeConfig、run_vector_search/
# enforced_search_scope/resolve_scoped_search_args/auto_vectorize_after_fetch/
# is_auto_vectorize_enabled 已迁出至 api/routers/vector.py（见 app.include_router）。


@app.get("/api/jobs/{job_id}")
async def get_background_job(job_id: str):
    """查询后台任务状态/进度/结果（向量化、重索引等长任务）。"""
    job = jobs_service.get_job(db_sink.engine, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


# ==================== 4. 定时任务 ====================
# CollectionJobCreate/Update 已迁至 api/routers/collection.py（下方 import re-export）。


# 采集调度 CRUD —— /api/collection-jobs*、/api/collection-job-runs* 已迁出至
# api/routers/collection.py（含 serialize_collection_job[_run] 与
# CollectionJob Create/Update；/api/node-groups* 与 /api/tasks* 已随实体简化
# 阶段 2 退役——存量数据由 Alembic 迁移内联/转换为采集任务）；
# 抓取核心 run_collection_items 与调度注册 load_tasks_to_scheduler 仍留守本文件，
# 经 collection 路由的 _app() 调用。见 app.include_router）。


# ==================== 5. 抓取运行历史 ====================
# GET /api/fetch-runs 与 /api/fetch-runs/{run_id} 已迁至 api/routers/monitoring.py。


# ==================== MCP Server Management ====================

_MCP_TOOLS_MANIFEST = [
    {"name": "search_articles",
     "description": "语义向量搜索文章，支持中英文，可按日期/来源/类型过滤；必须携带 subscription_token，结果限定到订阅范围"},
    {"name": "browse_articles",
     "description": "按条件过滤浏览文章列表（来源、类型、日期区间），适合日报生成；必须携带 subscription_token，结果限定到订阅范围"},
    {"name": "get_article",
     "description": "按 ID 获取单篇文章完整内容（含正文）；必须携带 subscription_token 并校验订阅范围"},
    {"name": "list_sources",
     "description": "列出所有已知数据来源，获取可用的 source_id 和 content_type（无需令牌）"},
    {"name": "get_rag_context",
     "description": "语义检索后组装格式化 RAG 上下文字符串；必须携带 subscription_token，结果限定到订阅范围"},
]


@app.get("/api/mcp/status")
def get_mcp_status(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "enabled": _mcp_enabled,
        "url": f"{base}/mcp",
        "tools": _MCP_TOOLS_MANIFEST,
    }


@app.post("/api/mcp/toggle")
def toggle_mcp():
    global _mcp_enabled
    _mcp_enabled = not _mcp_enabled
    with Session(db_sink.engine) as session:
        rec = session.get(AppSettingRecord, "mcp_enabled")
        if rec is None:
            rec = AppSettingRecord(key="mcp_enabled", value=str(_mcp_enabled).lower())
            session.add(rec)
        else:
            rec.value = str(_mcp_enabled).lower()
            session.add(rec)
        session.commit()
    return {"enabled": _mcp_enabled}
