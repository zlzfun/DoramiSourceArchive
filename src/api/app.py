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
from pipeline.progress import get_all_progress
from models.db import (
    ArticleRecord,
    CollectionJobRecord,
    CollectionJobRunRecord,
    FetchTaskRecord,
    FetchRunRecord,
    NodeGroupRecord,
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
from api import deps
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
from services import daily_brief as daily_brief_service
from services import accounts as accounts_service
from services import reader_ai as reader_ai_service
from services import ai_usage as ai_usage_service
from services import reader_activity as reader_activity_service
from services import content_analytics as content_analytics_service
from services import background_jobs
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
    "/api/node-groups",
    "/api/collection-jobs",
    "/api/collection-job-runs",
    "/api/jobs",
    "/api/tasks",
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
    """账户管理面（/api/accounts*）与运维管理面（/api/admin*）一律仅限 admin 角色，独立于 runtime 采集轴。"""
    return _path_matches(path, ("/api/accounts", "/api/admin"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_enabled
    print(f"🧭 Dorami runtime role: {runtime_role()}")

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


ARCHIVE_SYNC_SCHEMA_VERSION = "articles-jsonl-v1"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def archive_article_payload(record: ArticleRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "content_type": record.content_type,
        "source_id": record.source_id,
        "source_url": record.source_url,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "fetch_run_id": record.fetch_run_id,
        "job_id": record.job_id,
        "job_run_id": record.job_run_id,
        "source_group_id": record.source_group_id,
        "run_scope": record.run_scope,
        "has_content": record.has_content,
        "content": record.content or "",
        "extensions": _json_loads(record.extensions_json, {}),
    }


def archive_article_checksum(article: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(article).encode("utf-8")).hexdigest()


def archive_sync_line(record: ArticleRecord) -> Dict[str, Any]:
    article = archive_article_payload(record)
    return {
        "kind": "article",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "checksum": archive_article_checksum(article),
        "article": article,
    }


def archive_manifest_line(count: int, filters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "manifest",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "content": "articles",
        "count": count,
        "filters": {key: value for key, value in filters.items() if value not in (None, "")},
    }


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def build_import_article_record(article: Dict[str, Any]) -> ArticleRecord:
    required_fields = ["id", "content_type", "source_id", "publish_date", "fetched_date"]
    missing = [field for field in required_fields if article.get(field) in (None, "")]
    if missing:
        raise ValueError(f"article missing required fields: {', '.join(missing)}")

    has_content = _coerce_bool(article.get("has_content", bool(article.get("content"))))
    extensions = article.get("extensions") or {}
    if not isinstance(extensions, dict):
        raise ValueError("article.extensions must be an object")

    return ArticleRecord(
        id=str(article["id"]),
        title=str(article.get("title") or article["id"]),
        content_type=str(article["content_type"]),
        source_id=str(article["source_id"]),
        source_url=str(article.get("source_url") or ""),
        publish_date=str(article["publish_date"]),
        fetched_date=str(article["fetched_date"]),
        fetch_run_id=_coerce_optional_int(article.get("fetch_run_id")),
        job_id=_coerce_optional_int(article.get("job_id")),
        job_run_id=_coerce_optional_int(article.get("job_run_id")),
        source_group_id=_coerce_optional_int(article.get("source_group_id")),
        run_scope=str(article.get("run_scope") or "ad_hoc"),
        has_content=has_content,
        content=str(article.get("content") or ""),
        extensions_json=json.dumps(extensions, ensure_ascii=False),
        is_vectorized=False,
    )


def import_archive_sync_jsonl(raw_text: str) -> Dict[str, Any]:
    imported_count = 0
    skipped_count = 0
    updated_count = 0
    error_count = 0
    manifest: Optional[Dict[str, Any]] = None
    errors = []

    with Session(db_sink.engine) as session:
        for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                kind = item.get("kind")
                if kind == "manifest":
                    manifest = item
                    if item.get("schema_version") != ARCHIVE_SYNC_SCHEMA_VERSION:
                        raise ValueError(f"unsupported schema_version: {item.get('schema_version')}")
                    continue
                if kind != "article":
                    raise ValueError(f"unsupported line kind: {kind}")
                if item.get("schema_version") != ARCHIVE_SYNC_SCHEMA_VERSION:
                    raise ValueError(f"unsupported schema_version: {item.get('schema_version')}")

                article = item.get("article")
                if not isinstance(article, dict):
                    raise ValueError("article line missing article object")
                expected_checksum = item.get("checksum", "")
                actual_checksum = archive_article_checksum(article)
                if expected_checksum and not hmac.compare_digest(str(expected_checksum), actual_checksum):
                    raise ValueError("checksum mismatch")

                incoming = build_import_article_record(article)
                existing = session.get(ArticleRecord, incoming.id)
                if not existing:
                    session.add(incoming)
                    imported_count += 1
                    continue
                if not existing.has_content and incoming.has_content and incoming.content:
                    existing.title = incoming.title
                    existing.content_type = incoming.content_type
                    existing.source_id = incoming.source_id
                    existing.source_url = incoming.source_url
                    existing.publish_date = incoming.publish_date
                    existing.fetched_date = incoming.fetched_date
                    existing.fetch_run_id = incoming.fetch_run_id
                    existing.job_id = incoming.job_id
                    existing.job_run_id = incoming.job_run_id
                    existing.source_group_id = incoming.source_group_id
                    existing.run_scope = incoming.run_scope
                    existing.has_content = True
                    existing.content = incoming.content
                    existing.extensions_json = incoming.extensions_json
                    existing.is_vectorized = False
                    session.add(existing)
                    updated_count += 1
                else:
                    skipped_count += 1
            except Exception as exc:
                error_count += 1
                errors.append({"line": line_number, "error": str(exc)})
        session.commit()

    return {
        "status": "partial_success" if error_count else "success",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "manifest": manifest,
        "imported_count": imported_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "errors": errors[:20],
    }


def serialize_node_group(record: NodeGroupRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "fetcher_ids": _json_loads(record.fetcher_ids_json, []),
        "params": _json_loads(record.params_json, {}),
        "per_fetcher_params": _json_loads(record.per_fetcher_params_json, {}),
        "cron_expr": record.cron_expr,
        "per_fetcher_cron": _json_loads(record.per_fetcher_cron_json, {}),
        "is_active": record.is_active,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_collection_job(record: CollectionJobRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "group_id": record.group_id,
        "fetcher_ids": _json_loads(record.fetcher_ids_json, []),
        "params": _json_loads(record.params_json, {}),
        "per_fetcher_params": _json_loads(record.per_fetcher_params_json, {}),
        "cron_expr": record.cron_expr,
        "per_fetcher_cron": _json_loads(record.per_fetcher_cron_json, {}),
        "is_active": record.is_active,
        "downstream_policy": _json_loads(record.downstream_policy_json, {}),
        "legacy_task_id": record.legacy_task_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_collection_job_run(record: CollectionJobRunRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "group_id": record.group_id,
        "run_scope": record.run_scope,
        "trigger_type": record.trigger_type,
        "status": record.status,
        "name": record.name,
        "node_count": record.node_count,
        "child_run_ids": _json_loads(record.child_run_ids_json, []),
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "duration_ms": record.duration_ms,
        "fetched_count": record.fetched_count,
        "saved_count": record.saved_count,
        "skipped_count": record.skipped_count,
        "failed_count": record.failed_count,
        "error_message": record.error_message,
    }


def normalize_fetcher_ids(fetcher_ids: Optional[List[str]]) -> List[str]:
    seen = set()
    normalized = []
    for fetcher_id in fetcher_ids or []:
        clean_id = str(fetcher_id).strip()
        if clean_id and clean_id not in seen:
            normalized.append(clean_id)
            seen.add(clean_id)
    return normalized


def resolve_collection_job_fetcher_ids(job: CollectionJobRecord, session: Session) -> List[str]:
    fetcher_ids = normalize_fetcher_ids(_json_loads(job.fetcher_ids_json, []))
    if fetcher_ids:
        return fetcher_ids
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            return normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, []))
    return []


def build_collection_job_items(job: CollectionJobRecord, session: Session) -> List[Dict[str, Any]]:
    default_params = {}
    per_fetcher_params = {}
    if job.group_id:
        group = session.get(NodeGroupRecord, job.group_id)
        if group and group.is_active:
            default_params.update(_json_loads(group.params_json, {}))
            per_fetcher_params.update(_json_loads(group.per_fetcher_params_json, {}))
    default_params.update(_json_loads(job.params_json, {}))
    job_per_fetcher_params = _json_loads(job.per_fetcher_params_json, {})
    items = []
    for fetcher_id in resolve_collection_job_fetcher_ids(job, session):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        params.update(job_per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def build_node_group_items(group: NodeGroupRecord) -> List[Dict[str, Any]]:
    default_params = _json_loads(group.params_json, {})
    per_fetcher_params = _json_loads(group.per_fetcher_params_json, {})
    items = []
    for fetcher_id in normalize_fetcher_ids(_json_loads(group.fetcher_ids_json, [])):
        params = dict(default_params)
        params.update(per_fetcher_params.get(fetcher_id, {}))
        items.append({"fetcher_id": fetcher_id, "params": params})
    return items


def apply_run_param_overrides(
        items: List[Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not overrides:
        return items
    normalized_overrides = {
        key: value
        for key, value in overrides.items()
        if value is not None and value != ""
    }
    if not normalized_overrides:
        return items
    return [
        {
            **item,
            "params": {
                **(item.get("params") or {}),
                **normalized_overrides,
            },
        }
        for item in items
    ]


def test_run_overrides(test_limit: Optional[int] = None) -> Dict[str, Any]:
    if test_limit is None:
        return {}
    return {"limit": max(int(test_limit), 1)}


def resolve_delivery_source_ids(
        session: Session,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
) -> List[str]:
    explicit_ids = normalize_fetcher_ids(([source_id] if source_id else []) + _split_csv(source_ids))
    scope_ids = []
    if group_id is not None:
        group = session.get(NodeGroupRecord, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="采集范围不存在")
        scope_ids.extend(_json_loads(group.fetcher_ids_json, []))
    if job_id is not None:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        scope_ids.extend(resolve_collection_job_fetcher_ids(job, session))

    scope_ids = normalize_fetcher_ids(scope_ids)
    if explicit_ids and scope_ids:
        scope_set = set(scope_ids)
        return [item for item in explicit_ids if item in scope_set]
    return explicit_ids or scope_ids


def create_collection_job_run(
        name: str,
        trigger_type: str,
        node_count: int,
        job_id: Optional[int] = None,
        group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = CollectionJobRunRecord(
            job_id=job_id,
            group_id=group_id,
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
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        source_group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> int:
    with Session(db_sink.engine) as session:
        run = FetchRunRecord(
            fetcher_id=fetcher_id,
            task_id=task_id,
            job_id=job_id,
            job_run_id=job_run_id,
            source_group_id=source_group_id,
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


async def execute_fetch_job(fetcher_id: str, params: dict, task_id: Optional[int] = None):
    print(f"⏰ 定时任务触发: 正在执行调度节点 {fetcher_id}...")
    job_run_id = create_collection_job_run(
        name=f"旧定时任务 #{task_id or fetcher_id}",
        trigger_type="scheduled",
        node_count=1,
        run_scope="legacy_task",
    )
    try:
        result = await run_fetcher_with_tracking(
            fetcher_id,
            params,
            trigger_type="scheduled",
            task_id=task_id,
            job_run_id=job_run_id,
            run_scope="legacy_task",
        )
        finish_collection_job_run(
            job_run_id,
            status="success",
            child_run_ids=[result["run_id"]],
            fetched_count=result["fetched_count"],
            saved_count=result["saved_count"],
            skipped_count=result["skipped_count"],
        )
    except ValueError as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        print(f"❌ {e}")
    except Exception as e:
        finish_collection_job_run(job_run_id, status="failed", failed_count=1, error_message=str(e))
        print(f"❌ 定时任务执行失败: {e}")
        raise


async def execute_collection_job(job_id: int):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job or not job.is_active:
            print(f"⚠️ 采集任务不可用或已停用: {job_id}")
            return
        items = build_collection_job_items(job, session)
        job_name = job.name
        group_id = job.group_id

    print(f"⏰ 采集任务触发: {job_name} ({len(items)} 个节点)")
    await run_collection_items(
        items,
        name=job_name,
        trigger_type="scheduled",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


async def execute_collection_job_node(job_id: int, fetcher_id: str):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job or not job.is_active:
            print(f"⚠️ 采集任务不可用或已停用: {job_id}")
            return
        items = [item for item in build_collection_job_items(job, session) if item["fetcher_id"] == fetcher_id]
        job_name = job.name
        group_id = job.group_id
    if not items:
        print(f"⚠️ 采集任务节点不可用: {job_id}/{fetcher_id}")
        return
    await run_collection_items(
        items,
        name=f"{job_name} / {fetcher_id}",
        trigger_type="scheduled",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


async def execute_node_group(group_id: int):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group or not group.is_active:
            print(f"⚠️ 采集范围不可用或已停用: {group_id}")
            return
        items = build_node_group_items(group)
        group_name = group.name
    await run_collection_items(
        items,
        name=f"采集范围定时: {group_name}",
        trigger_type="scheduled",
        group_id=group_id,
        run_scope="ad_hoc",
    )


async def execute_node_group_node(group_id: int, fetcher_id: str):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group or not group.is_active:
            print(f"⚠️ 采集范围不可用或已停用: {group_id}")
            return
        items = [item for item in build_node_group_items(group) if item["fetcher_id"] == fetcher_id]
        group_name = group.name
    if not items:
        print(f"⚠️ 采集范围节点不可用: {group_id}/{fetcher_id}")
        return
    await run_collection_items(
        items,
        name=f"采集范围定时: {group_name} / {fetcher_id}",
        trigger_type="scheduled",
        group_id=group_id,
        run_scope="ad_hoc",
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
        tasks = session.exec(select(FetchTaskRecord).where(FetchTaskRecord.is_active == True)).all()
        for task in tasks:
            params = json.loads(task.params_json)
            add_cron_job(f"task_{task.id}", execute_fetch_job, task.cron_expr, [task.fetcher_id, params, task.id])
        groups = session.exec(
            select(NodeGroupRecord)
            .where(NodeGroupRecord.is_active == True)
        ).all()
        for group in groups:
            if group.cron_expr:
                add_cron_job(f"node_group_{group.id}", execute_node_group, group.cron_expr, [group.id])
            for fetcher_id, cron_expr in _json_loads(group.per_fetcher_cron_json, {}).items():
                if cron_expr:
                    add_cron_job(
                        f"node_group_{group.id}_{fetcher_id}",
                        execute_node_group_node,
                        cron_expr,
                        [group.id, fetcher_id],
                    )
        jobs = session.exec(
            select(CollectionJobRecord)
            .where(CollectionJobRecord.is_active == True)
        ).all()
        for job in jobs:
            if job.cron_expr:
                add_cron_job(f"collection_job_{job.id}", execute_collection_job, job.cron_expr, [job.id])
            for fetcher_id, cron_expr in _json_loads(job.per_fetcher_cron_json, {}).items():
                if cron_expr:
                    add_cron_job(
                        f"collection_job_{job.id}_{fetcher_id}",
                        execute_collection_job_node,
                        cron_expr,
                        [job.id, fetcher_id],
                    )
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


# SourceBuilder* 请求模型已迁出至 api/routers/ingest.py。


class FetchBatchItem(BaseModel):
    fetcher_id: str
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class FetchBatchParams(BaseModel):
    items: List[FetchBatchItem] = PydanticField(default_factory=list)


# SocialPostImport* 请求模型已迁出至 api/routers/ingest.py。


# SubscriptionFilters / SubscriptionDeliveryPolicy / SubscriptionCreate /
# SubscriptionUpdate 已迁至 api/routers/subscriptions.py（下方 import re-export）。


def serialize_source_config(record: SourceConfigRecord) -> Dict[str, Any]:
    data = record.dict()
    try:
        data["params"] = json.loads(record.params_json or "{}")
    except json.JSONDecodeError:
        data["params"] = {}
    try:
        tags = json.loads(record.content_tags_json or "[]")
        data["content_tags"] = tags if isinstance(tags, list) else []
    except json.JSONDecodeError:
        data["content_tags"] = []
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
    source_type = source_config.source_type.lower()
    if source_type in {"rss", "atom"}:
        return "generic_rss"
    if source_type in {"web", "webpage"}:
        return "generic_web"
    return ""


def build_source_fetch_params(source_config: SourceConfigRecord, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = parse_json_object(source_config.params_json)
    params.update({
        "source_id": source_config.source_id,
        "category": source_config.category,
    })
    if source_config.source_type.lower() in {"web", "webpage"}:
        # 通用网页抓取器（generic_web）：url 即列表页；其余 web 配置（URL 模式 / 详情 Profile /
        # listing_css）已在 params_json 内，随上面的 parse_json_object 透传。
        params.update({
            "listing_url": source_config.url,
            "site_name": params.get("site_name") or source_config.name,
        })
    else:
        # RSS/Atom 等：维持既有 feed_url/feed_name 语义不变。
        params.update({
            "feed_url": source_config.url,
            "feed_name": source_config.name,
        })
    if overrides:
        params.update(overrides)
    return params


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
            latest_publish_date = getattr(result, "latest_content_publish_date", "") if result else ""
            if latest_content_id:
                state.last_content_id = latest_content_id
                state.last_cursor_value = latest_content_id
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


def derive_health_status(latest_run: Optional[FetchRunRecord], consecutive_failures: int) -> str:
    if not latest_run:
        return "never_run"
    if latest_run.status == "running":
        return "running"
    if consecutive_failures > 0:
        return "failing"
    if latest_run.status == "success":
        return "healthy"
    return "unknown"


def build_fetcher_health(fetcher_metadata: Dict[str, Any], runs: List[FetchRunRecord]) -> Dict[str, Any]:
    ordered_runs = sorted(runs, key=lambda run: run.started_at or "", reverse=True)
    latest_run = ordered_runs[0] if ordered_runs else None
    success_runs = [run for run in ordered_runs if run.status == "success"]
    failed_runs = [run for run in ordered_runs if run.status == "failed"]
    running_runs = [run for run in ordered_runs if run.status == "running"]

    consecutive_failures = 0
    for run in ordered_runs:
        if run.status == "failed":
            consecutive_failures += 1
        elif run.status == "success":
            break

    latest_success = success_runs[0] if success_runs else None
    latest_failure = failed_runs[0] if failed_runs else None

    return {
        "fetcher_id": fetcher_metadata["id"],
        "source_id": fetcher_metadata["id"],
        "name": fetcher_metadata["name"],
        "category": fetcher_metadata.get("category", "general"),
        "content_type": fetcher_metadata.get("content_type", ""),
        "health_status": derive_health_status(latest_run, consecutive_failures),
        "latest_run_status": latest_run.status if latest_run else None,
        "latest_run_at": latest_run.started_at if latest_run else None,
        "latest_success_at": latest_success.started_at if latest_success else None,
        "latest_failure_at": latest_failure.started_at if latest_failure else None,
        "latest_error_message": latest_run.error_message if latest_run and latest_run.status == "failed" else None,
        "consecutive_failures": consecutive_failures,
        "total_runs": len(ordered_runs),
        "success_runs": len(success_runs),
        "failed_runs": len(failed_runs),
        "running_runs": len(running_runs),
        "latest_fetched_count": latest_run.fetched_count if latest_run else 0,
        "latest_saved_count": latest_run.saved_count if latest_run else 0,
        "latest_skipped_count": latest_run.skipped_count if latest_run else 0,
    }


def build_fetcher_health_from_state(fetcher_metadata: Dict[str, Any], state: SourceStateRecord) -> Dict[str, Any]:
    return {
        "fetcher_id": fetcher_metadata["id"],
        "source_id": state.source_id,
        "name": fetcher_metadata["name"],
        "category": fetcher_metadata.get("category", "general"),
        "content_type": state.content_type or fetcher_metadata.get("content_type", ""),
        "health_status": state.status,
        "latest_run_status": "success" if state.status == "healthy" else "failed" if state.status == "failing" else state.status,
        "latest_run_at": state.last_started_at,
        "latest_success_at": state.last_success_at,
        "latest_failure_at": state.last_failure_at,
        "latest_error_type": state.latest_error_type,
        "latest_error_message": state.latest_error_message,
        "last_cursor_value": state.last_cursor_value,
        "last_cursor_date": state.last_cursor_date,
        "last_content_id": state.last_content_id,
        "consecutive_failures": state.consecutive_failures,
        "total_runs": state.total_runs,
        "success_runs": state.success_runs,
        "failed_runs": state.failed_runs,
        "running_runs": 1 if state.status == "running" else 0,
        "latest_fetched_count": state.latest_fetched_count,
        "latest_saved_count": state.latest_saved_count,
        "latest_skipped_count": state.latest_skipped_count,
    }


async def run_fetcher_with_tracking(
        fetcher_id: str,
        params: Dict[str, Any],
        trigger_type: str = "manual",
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        source_group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
) -> Dict[str, Any]:
    run_id = create_fetch_run(
        fetcher_id,
        params,
        trigger_type=trigger_type,
        task_id=task_id,
        job_id=job_id,
        job_run_id=job_run_id,
        source_group_id=source_group_id,
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
                "source_group_id": source_group_id,
                "run_scope": run_scope,
            },
            **params,
        )
        finish_fetch_run(run_id, status="success", result=result)
        mark_source_state_finished(fetcher_id, params, run_id, status="success", result=result)
        await auto_vectorize_after_fetch(result.saved_content_ids)
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
        task_id: Optional[int] = None,
        job_id: Optional[int] = None,
        group_id: Optional[int] = None,
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=1,
        job_id=job_id,
        group_id=group_id,
        run_scope=run_scope,
    )
    try:
        result = await run_fetcher_with_tracking(
            fetcher_id,
            params,
            trigger_type=trigger_type,
            task_id=task_id,
            job_id=job_id,
            job_run_id=job_run_id,
            source_group_id=group_id,
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
        group_id: Optional[int] = None,
        run_scope: str = "ad_hoc",
        max_concurrency: Optional[int] = None,
) -> Dict[str, Any]:
    job_run_id = create_collection_job_run(
        name=name,
        trigger_type=trigger_type,
        node_count=len(items),
        job_id=job_id,
        group_id=group_id,
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
                    source_group_id=group_id,
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
@app.get("/api/source-configs")
def get_source_configs(
        source_type: Optional[str] = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
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


@app.get("/api/source-configs/{source_id}")
def get_source_config(source_id: str):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        return serialize_source_config(record)


@app.post("/api/source-configs")
def create_source_config(params: SourceConfigCreate):
    source_id = normalize_source_id(params.source_id)
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")

    with Session(db_sink.engine) as session:
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


@app.put("/api/source-configs/{source_id}")
def update_source_config(source_id: str, params: SourceConfigUpdate):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
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


@app.post("/api/source-configs/{source_id}/toggle")
def toggle_source_config(source_id: str, is_active: bool = Body(..., embed=True)):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        record.is_active = is_active
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_source_config(record)


@app.delete("/api/source-configs/{source_id}")
def delete_source_config(source_id: str):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
        record = session.get(SourceConfigRecord, source_id)
        if not record:
            raise HTTPException(status_code=404, detail="数据源配置不存在")
        session.delete(record)
        session.commit()
        return {"status": "success"}


@app.post("/api/source-configs/{source_id}/fetch")
async def fetch_source_config(source_id: str, body: Optional[SourceFetchParams] = None):
    source_id = normalize_source_id(source_id)
    with Session(db_sink.engine) as session:
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
        result = await run_single_fetch_as_collection(
            fetcher_id,
            params,
            name=f"临时抓取: {source_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
        return {"source_id": source_id, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/source-configs/fetch-active-rss")
async def fetch_active_rss_sources(body: Optional[SourceFetchParams] = None):
    results = []
    with Session(db_sink.engine) as session:
        records = session.exec(
            select(SourceConfigRecord)
            .where(SourceConfigRecord.is_active == True)
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

    result = await run_collection_items(
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


@app.post("/api/source-configs/fetch-active-web")
async def fetch_active_web_sources(body: Optional[SourceFetchParams] = None):
    """批量触发所有启用的 web/webpage 数据源（经 generic_web 配置驱动抓取）。镜像 fetch-active-rss。"""
    with Session(db_sink.engine) as session:
        records = session.exec(
            select(SourceConfigRecord)
            .where(SourceConfigRecord.is_active == True)
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

    result = await run_collection_items(
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


# ==================== 数据接入（source-builder / import）====================
# /api/source-builder/* 与 /api/import/social-posts 已迁出至 api/routers/ingest.py
# （见 app.include_router）。collector 网关仍由中间件统一强制。


@app.get("/api/archive/export/articles.jsonl")
def export_archive_articles_jsonl(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = None,
        skip: int = 0,
        limit: int = 1000
):
    safe_limit = min(max(limit, 1), 5000)
    filters = {
        "content_type": content_type,
        "content_types": content_types,
        "source_id": source_id,
        "source_ids": source_ids,
        "job_id": job_id,
        "job_run_id": job_run_id,
        "fetch_run_id": fetch_run_id,
        "run_scope": run_scope,
        "publish_date_start": publish_date_start,
        "publish_date_end": publish_date_end,
        "fetched_date_start": fetched_date_start,
        "fetched_date_end": fetched_date_end,
        "search": search,
        "has_content": has_content,
        "skip": skip,
        "limit": safe_limit,
    }
    with Session(db_sink.engine) as session:
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_id=source_id,
            source_ids=source_ids,
            job_id=job_id,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.asc(), ArticleRecord.id.asc()).offset(skip).limit(safe_limit)
        ).all()

    lines = [archive_manifest_line(len(records), filters)]
    lines.extend(archive_sync_line(record) for record in records)
    body = "\n".join(_canonical_json(line) for line in lines) + "\n"
    return Response(content=body, media_type="application/x-ndjson; charset=utf-8")


@app.post("/api/archive/import/articles.jsonl")
async def import_archive_articles_jsonl(request: Request):
    raw_text = (await request.body()).decode("utf-8")
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="导入内容不能为空")
    result = import_archive_sync_jsonl(raw_text)
    status_code = 400 if result["error_count"] and not (result["imported_count"] or result["updated_count"]) else 200
    return StarletteJSONResponse(result, status_code=status_code)


# GET /api/articles（列表/查询，含订阅作用域）已迁出至 api/routers/articles.py
# （见 app.include_router）。下方 /api/feed/articles[.md] 暂留（依赖采集投递作用域 helper）。


@app.get("/api/feed/articles")
def get_feed_articles(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        include_content: bool = True,
        skip: int = 0,
        limit: int = 100
):
    safe_limit = min(max(limit, 1), 500)
    with Session(db_sink.engine) as session:
        delivery_source_ids = resolve_delivery_source_ids(
            session, source_id=source_id, source_ids=source_ids, group_id=group_id, job_id=job_id
        )
        if (source_id or source_ids or group_id is not None or job_id is not None) and not delivery_source_ids:
            return {"status": "success", "count": 0, "skip": skip, "limit": safe_limit, "next_skip": None, "items": []}
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_ids=",".join(delivery_source_ids) if delivery_source_ids else None,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
        ).all()

    return {
        "status": "success",
        "count": len(records),
        "skip": skip,
        "limit": safe_limit,
        "next_skip": skip + len(records) if len(records) == safe_limit else None,
        "items": [serialize_feed_article(record, include_content=include_content) for record in records],
    }


@app.get("/api/feed/articles.md")
def export_feed_articles_markdown(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        group_id: Optional[int] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        skip: int = 0,
        limit: int = 100
):
    safe_limit = min(max(limit, 1), 200)
    with Session(db_sink.engine) as session:
        delivery_source_ids = resolve_delivery_source_ids(
            session, source_id=source_id, source_ids=source_ids, group_id=group_id, job_id=job_id
        )
        if (source_id or source_ids or group_id is not None or job_id is not None) and not delivery_source_ids:
            return Response(content="", media_type="text/markdown; charset=utf-8")
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_ids=",".join(delivery_source_ids) if delivery_source_ids else None,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
        ).all()

    markdown = "\n\n---\n\n".join(article_to_markdown(record) for record in records)
    return Response(content=markdown, media_type="text/markdown; charset=utf-8")


# 单条读取/手工录入/更新/删除/批量删除（GET|POST|PUT|DELETE /api/articles*）
# 与 ArticleUpdateParams、_maybe_rewind_daily_brief_cursor 已迁出至
# api/routers/articles.py（见 app.include_router）。


# ==================== 2. 调度与抓取 (注册中心化) ====================

@app.get("/api/fetchers")
async def get_available_fetchers():
    return fetcher_registry.get_all_metadata()


@app.get("/api/source-health")
def get_source_health():
    fetchers = fetcher_registry.get_all_metadata()
    fetcher_ids = [fetcher["id"] for fetcher in fetchers]

    with Session(db_sink.engine) as session:
        runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.fetcher_id.in_(fetcher_ids))).all()
        states = session.exec(select(SourceStateRecord).where(SourceStateRecord.source_id.in_(fetcher_ids))).all()
        article_counts = session.exec(
            select(ArticleRecord.source_id, func.count(ArticleRecord.id))
            .where(ArticleRecord.source_id.in_(fetcher_ids))
            .group_by(ArticleRecord.source_id)
        ).all()

    article_count_by_source = {source_id: count for source_id, count in article_counts}
    states_by_source = {state.source_id: state for state in states}
    runs_by_fetcher: Dict[str, List[FetchRunRecord]] = {fetcher_id: [] for fetcher_id in fetcher_ids}
    for run in runs:
        runs_by_fetcher.setdefault(run.fetcher_id, []).append(run)

    health_items = []
    for fetcher in fetchers:
        item = (
            build_fetcher_health_from_state(fetcher, states_by_source[fetcher["id"]])
            if fetcher["id"] in states_by_source
            else build_fetcher_health(fetcher, runs_by_fetcher.get(fetcher["id"], []))
        )
        item["total_articles"] = article_count_by_source.get(fetcher["id"], 0)
        health_items.append(item)
    return sorted(health_items, key=lambda item: (item["category"], item["name"]))


@app.get("/api/source-states")
def get_source_states(
        status: Optional[str] = None,
        fetcher_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(SourceStateRecord)
        if status:
            query = query.where(SourceStateRecord.status == status)
        if fetcher_id:
            query = query.where(SourceStateRecord.fetcher_id == fetcher_id)
        query = query.order_by(SourceStateRecord.updated_at.desc()).offset(skip).limit(limit)
        return session.exec(query).all()


@app.get("/api/fetch-runs/running-progress")
def get_running_progress():
    return get_all_progress()


@app.post("/api/fetch/batch")
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
    return await run_collection_items(
        items,
        name="临时批量抓取",
        trigger_type="manual",
        run_scope="ad_hoc",
    )


@app.post("/api/fetch/{fetcher_id}")
async def trigger_fetch_dynamic(
        fetcher_id: str,
        params: Dict[str, Any] = Body(...),
        test_limit: Optional[int] = None,
):
    try:
        return await run_single_fetch_as_collection(
            fetcher_id,
            {**params, **test_run_overrides(test_limit)},
            name=f"临时抓取: {fetcher_id}",
            trigger_type="manual",
            run_scope="ad_hoc",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ----------------- 向量化接口 -----------------
# _record_to_content 已迁至 api/articles_view.py（共享，re-export 见顶部 import）。


@app.post("/api/vectorize/batch")
async def batch_vectorize_articles(params: BatchOpParams):
    vs = require_vector_sink()
    success_count = 0
    for uid in params.ids:
        record = await db_sink.get(uid)
        if not record or record.is_vectorized: continue
        if await vs.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(uid)
            success_count += 1
    return {"status": "success", "count": success_count}


@app.post("/api/vectorize/all-pending")
async def vectorize_all_pending():
    """对所有 is_vectorized=False 的文章执行向量化，跳过已索引的条目。

    全量向量化可能耗时数分钟到数小时，改为提交后台任务并立即返回 job_id；
    前端轮询 GET /api/jobs/{job_id} 获取进度与最终结果（count/total_pending）。
    """
    vs = require_vector_sink()
    with Session(db_sink.engine) as session:
        from sqlmodel import select as sm_select
        records = session.exec(
            sm_select(ArticleRecord).where(ArticleRecord.is_vectorized == False)
        ).all()
    pending_ids = [record.id for record in records]

    async def _work(job: background_jobs.Job) -> Dict[str, Any]:
        job.set_total(len(pending_ids))
        success_count = 0
        for article_id in pending_ids:
            record = await db_sink.get(article_id)
            if record and not record.is_vectorized:
                if await vs.save(_record_to_content(record)):
                    await db_sink.mark_as_vectorized(article_id)
                    success_count += 1
            job.advance()
        return {"count": success_count, "total_pending": len(pending_ids)}

    job = background_jobs.launch("vectorize_all_pending", _work)
    return {"status": "accepted", "job_id": job.id, "total_pending": len(pending_ids)}


@app.get("/api/vector/subscribed-stats")
def subscribed_vector_stats(request: Request):
    """当前用户订阅范围内的向量化进度（用于「向量雷达」的范围内构建）。"""
    username = current_username(request)
    with Session(db_sink.engine) as session:
        source_ids = resolve_subscribed_source_ids(session, username) if username else []
        if not source_ids:
            return {"subscribed_source_count": 0, "total": 0, "vectorized": 0, "pending": 0}
        total = session.exec(
            select(func.count(ArticleRecord.id)).where(ArticleRecord.source_id.in_(source_ids))
        ).one()
        vectorized = session.exec(
            select(func.count(ArticleRecord.id)).where(
                ArticleRecord.source_id.in_(source_ids),
                ArticleRecord.is_vectorized == True,  # noqa: E712
            )
        ).one()
    total = int(total or 0)
    vectorized = int(vectorized or 0)
    return {
        "subscribed_source_count": len(source_ids),
        "total": total,
        "vectorized": vectorized,
        "pending": total - vectorized,
    }


AUTO_VECTORIZE_SETTING_KEY = "auto_vectorize"


def is_auto_vectorize_enabled() -> bool:
    with Session(db_sink.engine) as session:
        record = session.get(AppSettingRecord, AUTO_VECTORIZE_SETTING_KEY)
        return bool(record and record.value.lower() == "true")


async def auto_vectorize_after_fetch(content_ids: List[str]) -> None:
    """抓取保存后，如管理员开启了自动向量化，则把新入库文章写入向量库。

    失败不影响抓取主流程（向量化是尽力而为的旁路）。
    RAG 关闭时（vector_sink 为 None）直接 no-op。
    """
    if not content_ids or vector_sink is None or not is_auto_vectorize_enabled():
        return
    for content_id in content_ids:
        try:
            record = await db_sink.get(content_id)
            if record and not record.is_vectorized:
                if await vector_sink.save(_record_to_content(record)):
                    await db_sink.mark_as_vectorized(content_id)
        except Exception as exc:  # noqa: BLE001 — 旁路任务不应中断抓取
            print(f"⚠️ 自动向量化失败 (id={content_id}): {exc}")


class AutoVectorizeConfig(BaseModel):
    enabled: bool


@app.get("/api/vector/auto-vectorize")
def get_auto_vectorize_config():
    """读取「抓取后自动向量化」开关（管理员配置）。"""
    require_vector_sink()
    return {"enabled": is_auto_vectorize_enabled()}


@app.post("/api/vector/auto-vectorize")
def set_auto_vectorize_config(config: AutoVectorizeConfig):
    """设置「抓取后自动向量化」开关。开启后，后续抓取入库的文章会自动写入向量库。"""
    require_vector_sink()
    with Session(db_sink.engine) as session:
        record = session.get(AppSettingRecord, AUTO_VECTORIZE_SETTING_KEY)
        if record is None:
            record = AppSettingRecord(key=AUTO_VECTORIZE_SETTING_KEY, value="")
        record.value = "true" if config.enabled else "false"
        session.add(record)
        session.commit()
    return {"enabled": config.enabled}


# ==================== LLM 配置 & 每日日报（collector/admin） ====================
# 端点已迁出至 api/routers/daily_brief.py（见 app.include_router）。
# collector 网关仍由中间件统一强制（COLLECTOR_API_PREFIXES 含 /api/llm、/api/daily-brief）。


@app.post("/api/vectorize/{article_id:path}")
async def vectorize_article(article_id: str):
    vs = require_vector_sink()
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="文章不存在")
    if record.is_vectorized: return {"status": "skipped"}

    content_obj = _record_to_content(record)
    success = await vs.save(content_obj)
    if success:
        await db_sink.mark_as_vectorized(article_id)
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="向量化处理失败")


# ==================== 3. 向量检索与状态 ====================
class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: float = 1.5   # T4: cosine distance 上限（越小越相关；>1.5 视为不相关）
    content_type: Optional[str] = None
    source_id: Optional[str] = None
    publish_date_gte: Optional[str] = None   # T5: 发布日期下限，格式 'YYYY-MM-DD'
    publish_date_lte: Optional[str] = None   # 发布日期上限，格式 'YYYY-MM-DD'
    rerank: bool = False                     # T12: cross-encoder 重排序


def enforced_search_scope(request: Request) -> tuple[Optional[List[str]], bool]:
    """登录用户的语义检索硬性限定在其订阅来源内。

    返回 (source_ids, enforced)：
      - enforced=True 且 source_ids=[] → 用户无订阅，调用方应返回空结果；
      - enforced=True 且 source_ids=[...] → 限定到这些来源；
      - enforced=False → 不做限定（管理员超级用户检索全库；未启用鉴权同理）。

    只有受限的 ``user`` 账号被硬性限定在其订阅范围内；``admin`` 作为超级用户检索全部归档。
    """
    session = current_auth_session(request)
    username = str(session.get("sub")) if session else ""
    role = session.get("role") if session else None
    if not username or role != "user":
        return None, False
    with Session(db_sink.engine) as session_db:
        return resolve_subscribed_source_ids(session_db, username), True


def resolve_scoped_search_args(
        request: Request,
        requested_source_id: Optional[str],
) -> tuple[Optional[str], Optional[List[str]], bool, bool]:
    """把"硬性订阅范围"折算成传给检索流水线的 (source_id, source_ids)。

    返回 (source_id, source_ids, enforced, empty)。empty=True 表示应直接返回空结果
    （用户无订阅，或请求的 source_id 落在订阅范围之外）。
    """
    scope_ids, enforced = enforced_search_scope(request)
    if not enforced:
        return requested_source_id, None, False, False
    if not scope_ids:
        return None, None, True, True
    if requested_source_id:
        if requested_source_id not in set(scope_ids):
            return None, None, True, True
        return None, [requested_source_id], True, False
    return None, scope_ids, True, False


async def run_vector_search(
        query_text: str,
        top_k: int,
        score_threshold: float = 1.5,
        rerank: bool = False,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
        publish_date_gte: Optional[str] = None,
        publish_date_lte: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """语义检索流水线：召回 → 按 parent_id 去重 → 阈值过滤 → 重排序 → 回填标题。"""
    vs = require_vector_sink()
    raw_results = await vs.search(
        query_text,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=publish_date_gte,
        publish_date_lte=publish_date_lte,
    )

    # T3: 按 parent_id 去重，保留相同文章中 distance 最小的 chunk（最相关那条）
    best_by_parent: Dict[str, Any] = {}
    for res in raw_results:
        pid = res["metadata"].get("parent_id", res["id"])
        if pid not in best_by_parent or res["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = res

    # T4: 过滤低相关性结果（distance 超过阈值则丢弃）
    candidates = [r for r in best_by_parent.values() if r["distance"] <= score_threshold]

    # T12: cross-encoder 重排序（可选）
    if rerank:
        candidates = await vs.rerank(query_text, candidates[:top_k * 2])
    else:
        candidates.sort(key=lambda x: x["distance"])

    unique_results = []
    for res in candidates[:top_k]:
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)
        if record:
            res["metadata"]["title"] = record.title
            res["metadata"]["source_url"] = record.source_url
            res["metadata"]["publish_date"] = record.publish_date
        else:
            res["metadata"]["title"] = f"未知文章 ({pid})"
        unique_results.append(res)
    return unique_results


@app.post("/api/vector/search")
async def vector_search(query: SearchQuery, request: Request):
    # 登录用户的检索硬性限定在其订阅来源范围内（无订阅则空集）。
    source_id, source_ids, scoped, empty = resolve_scoped_search_args(request, query.source_id)
    if empty:
        return {"status": "success", "results": [], "reranked": query.rerank, "scoped": True}

    unique_results = await run_vector_search(
        query.query,
        top_k=query.top_k,
        score_threshold=query.score_threshold,
        rerank=query.rerank,
        content_type=query.content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=query.publish_date_gte,
        publish_date_lte=query.publish_date_lte,
    )
    return {"status": "success", "results": unique_results, "reranked": query.rerank, "scoped": scoped}


@app.get("/api/vector/stats")
async def get_vector_stats():
    vs = require_vector_sink()
    count = await vs.count()
    return {"total_vectors": count}


@app.delete("/api/vector/{article_id:path}")
async def delete_vector_only(article_id: str):
    vs = require_vector_sink()
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="记录不存在")
    if record.is_vectorized:
        await vs.delete(article_id)
        await db_sink.mark_as_unvectorized(article_id)
    return {"status": "success"}


@app.post("/api/vector/batch-delete")
async def batch_delete_vectors(params: BatchOpParams):
    vs = require_vector_sink()
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record and record.is_vectorized:
            await vs.delete(uid)
            await db_sink.mark_as_unvectorized(uid)
    return {"status": "success"}


# ==================== 3b. RAG 检索上下文接口（供下游应用调用）====================

class RagContextQuery(BaseModel):
    query: str
    top_k: int = 5
    max_chars: int = 4000           # 上下文文本的最大字符数（控制 LLM token 预算）
    score_threshold: float = 1.5
    content_type: Optional[str] = None
    source_id: Optional[str] = None
    publish_date_gte: Optional[str] = None
    publish_date_lte: Optional[str] = None
    context_separator: str = "\n\n---\n\n"  # 各来源之间的分隔符
    rerank: bool = False            # T12: cross-encoder 重排序
    expand_context: bool = False    # T13: 拼接命中 chunk 的前后相邻 chunk 以扩展上下文


@app.post("/api/rag/context")
async def rag_context(query: RagContextQuery, request: Request):
    """
    结构化检索上下文接口。
    返回组装好的 context_text（可直接注入 LLM prompt）及结构化 sources 列表。
    不调用任何 LLM，纯检索层输出。
    """
    from storage.impl.vector_storage import SOURCE_FRIENDLY_NAMES

    vs = require_vector_sink()

    # 登录用户的检索硬性限定在其订阅来源范围内（无订阅则空集）。
    source_id, source_ids, scoped, empty = resolve_scoped_search_args(request, query.source_id)
    if empty:
        return {
            "status": "success", "query": query.query, "retrieved_count": 0,
            "total_chars": 0, "context_text": "", "sources": [], "scoped": True,
        }

    raw = await vs.search(
        query.query,
        n_results=query.top_k * 4,
        content_type=query.content_type,
        source_id=source_id,
        source_ids=source_ids,
        publish_date_gte=query.publish_date_gte,
        publish_date_lte=query.publish_date_lte,
    )

    # 去重：同文章保留最优 chunk
    best_by_parent: Dict[str, Any] = {}
    for r in raw:
        pid = r["metadata"].get("parent_id", r["id"])
        if pid not in best_by_parent or r["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = r

    candidates = [r for r in best_by_parent.values() if r["distance"] <= query.score_threshold]

    # T12: cross-encoder 重排序（可选）
    if query.rerank:
        candidates = await vs.rerank(query.query, candidates[:query.top_k * 2])
    else:
        candidates.sort(key=lambda x: x["distance"])
    candidates = candidates[:query.top_k]

    # 组装结构化来源列表与上下文文本
    sources = []
    context_blocks = []
    total_chars = 0

    for rank, res in enumerate(candidates, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)

        source_id = res["metadata"].get("source_id", "")
        source_name = SOURCE_FRIENDLY_NAMES.get(source_id, source_id)
        pub_date = res["metadata"].get("publish_date", "")
        title = record.title if record else res["metadata"].get("title", "")
        source_url = record.source_url if record else ""

        # 摘录：取 chunk 文本，去掉头部（已有结构化字段），只保留正文部分
        raw_doc = res["document"]
        body_start = raw_doc.find("\n\n")
        excerpt = raw_doc[body_start + 2:].strip() if body_start != -1 else raw_doc.strip()

        # T13: 拼接前后相邻 chunk，扩展上下文窗口（可选）
        if query.expand_context:
            chunk_index = res["metadata"].get("chunk_index", 0)
            total_chunks = res["metadata"].get("total_chunks", 1)
            adj = await vs.expand_chunk(pid, chunk_index, total_chunks)
            parts = []
            if adj["prev"]:
                parts.append(adj["prev"])
            parts.append(excerpt)
            if adj["next"]:
                parts.append(adj["next"])
            excerpt = "\n\n".join(parts)

        block = (
            f"[{rank}] 来源: {source_name} | 日期: {pub_date}\n"
            f"标题: {title}\n"
            f"链接: {source_url}\n\n"
            f"{excerpt}"
        )

        if total_chars + len(block) > query.max_chars and context_blocks:
            break

        context_blocks.append(block)
        total_chars += len(block)

        sources.append({
            "rank": rank,
            "parent_id": pid,
            "title": title,
            "source_id": source_id,
            "source_name": source_name,
            "publish_date": pub_date,
            "source_url": source_url,
            "distance": round(res["distance"], 4),
            "excerpt": excerpt[:500],
        })

    context_text = query.context_separator.join(context_blocks)

    return {
        "query": query.query,
        "context_text": context_text,
        "sources": sources,
        "total_chars": len(context_text),
        "retrieved_count": len(sources),
    }


@app.get("/api/rag/similar/{article_id:path}")
async def rag_similar(article_id: str, top_k: int = 5):
    """
    相似文章接口：找出与给定文章语义最接近的其他文章。
    用于"相关阅读"、知识图谱构建等场景。
    """
    vs = require_vector_sink()
    record = await db_sink.get(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="文章不存在")

    # 使用标题+正文片段作为查询向量
    query_text = f"{record.title}\n{(record.content or '')[:300]}"
    raw = await vs.search(query_text, n_results=(top_k + 1) * 3)

    best_by_parent: Dict[str, Any] = {}
    for r in raw:
        pid = r["metadata"].get("parent_id", r["id"])
        if pid == article_id:
            continue  # 排除自身
        if pid not in best_by_parent or r["distance"] < best_by_parent[pid]["distance"]:
            best_by_parent[pid] = r

    candidates = sorted(best_by_parent.values(), key=lambda x: x["distance"])[:top_k]

    similar = []
    for rank, res in enumerate(candidates, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        rec = await db_sink.get(pid)
        similar.append({
            "rank": rank,
            "parent_id": pid,
            "title": rec.title if rec else res["metadata"].get("title", ""),
            "source_id": res["metadata"].get("source_id", ""),
            "publish_date": res["metadata"].get("publish_date", ""),
            "source_url": rec.source_url if rec else "",
            "distance": round(res["distance"], 4),
        })

    return {
        "article_id": article_id,
        "title": record.title,
        "similar": similar,
    }


@app.post("/api/vector/reindex-all")
async def reindex_all_articles():
    """
    T9: 删除并重建整个 ChromaDB collection，对所有文章重新向量化。
    适用于：更换 embedding 模型后的全库迁移。管理员（collector）操作。

    全库重索引耗时极长，改为提交后台任务并立即返回 job_id；前端轮询
    GET /api/jobs/{job_id} 获取进度与最终结果（total_reindexed/total_articles）。
    """
    vs = require_vector_sink()

    async def _work(job: background_jobs.Job) -> Dict[str, Any]:
        # 重建集合为同步重操作，卸载到线程池避免阻塞事件循环。
        await asyncio.to_thread(vs.rebuild_collection)

        with Session(db_sink.engine) as session:
            article_ids = list(session.exec(select(ArticleRecord.id)).all())
        job.set_total(len(article_ids))

        success_count = 0
        for article_id in article_ids:
            await db_sink.update(article_id, {"is_vectorized": False})
            record = await db_sink.get(article_id)
            if record and await vs.save(_record_to_content(record)):
                await db_sink.mark_as_vectorized(article_id)
                success_count += 1
            job.advance()
        return {"total_reindexed": success_count, "total_articles": len(article_ids)}

    job = background_jobs.launch("reindex_all", _work)
    return {"status": "accepted", "job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def get_background_job(job_id: str):
    """查询后台任务状态/进度/结果（向量化、重索引等长任务）。"""
    job = background_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.to_dict()


# ==================== 4. 定时任务 ====================
class NodeGroupCreate(BaseModel):
    name: str
    description: str = ""
    fetcher_ids: List[str] = PydanticField(default_factory=list)
    params: Dict[str, Any] = PydanticField(default_factory=dict)
    per_fetcher_params: Dict[str, Dict[str, Any]] = PydanticField(default_factory=dict)
    cron_expr: str = ""
    per_fetcher_cron: Dict[str, str] = PydanticField(default_factory=dict)
    is_active: bool = True


class NodeGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    fetcher_ids: Optional[List[str]] = None
    params: Optional[Dict[str, Any]] = None
    per_fetcher_params: Optional[Dict[str, Dict[str, Any]]] = None
    cron_expr: Optional[str] = None
    per_fetcher_cron: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


class CollectionJobCreate(BaseModel):
    name: str
    description: str = ""
    group_id: Optional[int] = None
    fetcher_ids: List[str] = PydanticField(default_factory=list)
    params: Dict[str, Any] = PydanticField(default_factory=dict)
    per_fetcher_params: Dict[str, Dict[str, Any]] = PydanticField(default_factory=dict)
    cron_expr: str = ""
    per_fetcher_cron: Dict[str, str] = PydanticField(default_factory=dict)
    is_active: bool = True
    downstream_policy: Dict[str, Any] = PydanticField(default_factory=dict)


class CollectionJobUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[int] = None
    fetcher_ids: Optional[List[str]] = None
    params: Optional[Dict[str, Any]] = None
    per_fetcher_params: Optional[Dict[str, Dict[str, Any]]] = None
    cron_expr: Optional[str] = None
    per_fetcher_cron: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None
    downstream_policy: Optional[Dict[str, Any]] = None


class TaskCreate(BaseModel):
    fetcher_id: str
    cron_expr: str
    params: dict


@app.get("/api/node-groups")
def get_node_groups(is_active: Optional[bool] = None):
    with Session(db_sink.engine) as session:
        query = select(NodeGroupRecord)
        if is_active is not None:
            query = query.where(NodeGroupRecord.is_active == is_active)
        query = query.order_by(NodeGroupRecord.name)
        return [serialize_node_group(record) for record in session.exec(query).all()]


@app.post("/api/node-groups")
def create_node_group(data: NodeGroupCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="采集范围名称不能为空")
    now = _now_iso()
    with Session(db_sink.engine) as session:
        record = NodeGroupRecord(
            name=name,
            description=data.description.strip(),
            fetcher_ids_json=_json_dumps(normalize_fetcher_ids(data.fetcher_ids)),
            params_json=_json_dumps(data.params),
            per_fetcher_params_json=_json_dumps(data.per_fetcher_params),
            cron_expr=data.cron_expr.strip(),
            per_fetcher_cron_json=_json_dumps(data.per_fetcher_cron),
            is_active=data.is_active,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        result = serialize_node_group(record)
    load_tasks_to_scheduler()
    return result


@app.put("/api/node-groups/{group_id}")
def update_node_group(group_id: int, data: NodeGroupUpdate):
    with Session(db_sink.engine) as session:
        record = session.get(NodeGroupRecord, group_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集范围不存在")
        update_data = data.dict(exclude_unset=True)
        if "name" in update_data:
            name = (update_data["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="采集范围名称不能为空")
            record.name = name
        if "description" in update_data:
            record.description = (update_data["description"] or "").strip()
        if "fetcher_ids" in update_data:
            record.fetcher_ids_json = _json_dumps(normalize_fetcher_ids(update_data["fetcher_ids"]))
        if "params" in update_data:
            record.params_json = _json_dumps(update_data["params"])
        if "per_fetcher_params" in update_data:
            record.per_fetcher_params_json = _json_dumps(update_data["per_fetcher_params"])
        if "cron_expr" in update_data:
            record.cron_expr = (update_data["cron_expr"] or "").strip()
        if "per_fetcher_cron" in update_data:
            record.per_fetcher_cron_json = _json_dumps(update_data["per_fetcher_cron"])
        if "is_active" in update_data:
            record.is_active = update_data["is_active"]
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        result = serialize_node_group(record)
    load_tasks_to_scheduler()
    return result


@app.delete("/api/node-groups/{group_id}")
def delete_node_group(group_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(NodeGroupRecord, group_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集范围不存在")
        session.delete(record)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.post("/api/node-groups/{group_id}/fetch")
async def fetch_node_group(group_id: int, test_limit: Optional[int] = None):
    with Session(db_sink.engine) as session:
        group = session.get(NodeGroupRecord, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="采集范围不存在")
        if not group.is_active:
            raise HTTPException(status_code=400, detail="采集范围已停用")
        items = build_node_group_items(group)
        group_name = group.name
    items = apply_run_param_overrides(items, test_run_overrides(test_limit))
    return await run_collection_items(
        items,
        name=f"临时抓取采集范围: {group_name}",
        trigger_type="manual",
        group_id=group_id,
        run_scope="ad_hoc",
    )


@app.get("/api/collection-jobs")
def get_collection_jobs(is_active: Optional[bool] = None):
    with Session(db_sink.engine) as session:
        query = select(CollectionJobRecord)
        if is_active is not None:
            query = query.where(CollectionJobRecord.is_active == is_active)
        query = query.order_by(CollectionJobRecord.name)
        return [serialize_collection_job(record) for record in session.exec(query).all()]


@app.post("/api/collection-jobs")
def create_collection_job(data: CollectionJobCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="采集任务名称不能为空")
    if data.group_id is None and not normalize_fetcher_ids(data.fetcher_ids):
        raise HTTPException(status_code=400, detail="采集任务需要采集范围或至少一个节点")
    now = _now_iso()
    with Session(db_sink.engine) as session:
        if data.group_id is not None and not session.get(NodeGroupRecord, data.group_id):
            raise HTTPException(status_code=404, detail="采集范围不存在")
        record = CollectionJobRecord(
            name=name,
            description=data.description.strip(),
            group_id=data.group_id,
            fetcher_ids_json=_json_dumps(normalize_fetcher_ids(data.fetcher_ids)),
            params_json=_json_dumps(data.params),
            per_fetcher_params_json=_json_dumps(data.per_fetcher_params),
            cron_expr=data.cron_expr.strip(),
            per_fetcher_cron_json=_json_dumps(data.per_fetcher_cron),
            is_active=data.is_active,
            downstream_policy_json=_json_dumps(data.downstream_policy),
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
    load_tasks_to_scheduler()
    return serialize_collection_job(record)


@app.put("/api/collection-jobs/{job_id}")
def update_collection_job(job_id: int, data: CollectionJobUpdate):
    with Session(db_sink.engine) as session:
        record = session.get(CollectionJobRecord, job_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        update_data = data.dict(exclude_unset=True)
        if "name" in update_data:
            name = (update_data["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="采集任务名称不能为空")
            record.name = name
        if "description" in update_data:
            record.description = (update_data["description"] or "").strip()
        if "group_id" in update_data:
            group_id = update_data["group_id"]
            if group_id is not None and not session.get(NodeGroupRecord, group_id):
                raise HTTPException(status_code=404, detail="采集范围不存在")
            record.group_id = group_id
        if "fetcher_ids" in update_data:
            record.fetcher_ids_json = _json_dumps(normalize_fetcher_ids(update_data["fetcher_ids"]))
        if "params" in update_data:
            record.params_json = _json_dumps(update_data["params"])
        if "per_fetcher_params" in update_data:
            record.per_fetcher_params_json = _json_dumps(update_data["per_fetcher_params"])
        if "cron_expr" in update_data:
            record.cron_expr = (update_data["cron_expr"] or "").strip()
        if "per_fetcher_cron" in update_data:
            record.per_fetcher_cron_json = _json_dumps(update_data["per_fetcher_cron"])
        if "is_active" in update_data:
            record.is_active = update_data["is_active"]
        if "downstream_policy" in update_data:
            record.downstream_policy_json = _json_dumps(update_data["downstream_policy"])
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
    load_tasks_to_scheduler()
    return serialize_collection_job(record)


@app.delete("/api/collection-jobs/{job_id}")
def delete_collection_job(job_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(CollectionJobRecord, job_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        session.delete(record)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.post("/api/collection-jobs/{job_id}/run")
async def run_collection_job_now(job_id: int, test_limit: Optional[int] = None):
    with Session(db_sink.engine) as session:
        job = session.get(CollectionJobRecord, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        items = build_collection_job_items(job, session)
        if not items:
            raise HTTPException(status_code=400, detail="采集任务没有可执行节点")
        job_name = job.name
        group_id = job.group_id
    items = apply_run_param_overrides(items, test_run_overrides(test_limit))
    return await run_collection_items(
        items,
        name=job_name,
        trigger_type="manual",
        job_id=job_id,
        group_id=group_id,
        run_scope="saved_job",
    )


@app.post("/api/collection-jobs/migrate-legacy-tasks")
def migrate_legacy_tasks_to_collection_jobs():
    created = 0
    now = _now_iso()
    with Session(db_sink.engine) as session:
        tasks = session.exec(select(FetchTaskRecord)).all()
        for task in tasks:
            existing = session.exec(
                select(CollectionJobRecord).where(CollectionJobRecord.legacy_task_id == task.id)
            ).first()
            if existing:
                continue
            fetcher_class = fetcher_registry.get_class(task.fetcher_id)
            name = fetcher_class.name if fetcher_class else task.fetcher_id
            record = CollectionJobRecord(
                name=f"{name} 定时采集",
                description="由旧版单节点定时任务迁移生成。",
                fetcher_ids_json=_json_dumps([task.fetcher_id]),
                params_json=task.params_json,
                cron_expr=task.cron_expr,
                is_active=False,
                legacy_task_id=task.id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            created += 1
        session.commit()
    return {"status": "success", "created": created}


@app.get("/api/collection-job-runs")
def get_collection_job_runs(
        job_id: Optional[int] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        run_scope: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
):
    with Session(db_sink.engine) as session:
        query = select(CollectionJobRunRecord)
        if job_id is not None:
            query = query.where(CollectionJobRunRecord.job_id == job_id)
        if status:
            query = query.where(CollectionJobRunRecord.status == status)
        if trigger_type:
            query = query.where(CollectionJobRunRecord.trigger_type == trigger_type)
        if run_scope:
            query = query.where(CollectionJobRunRecord.run_scope == run_scope)
        query = query.order_by(CollectionJobRunRecord.started_at.desc()).offset(skip).limit(limit)
        return [serialize_collection_job_run(record) for record in session.exec(query).all()]


@app.get("/api/collection-job-runs/{job_run_id}")
def get_collection_job_run(job_run_id: int):
    with Session(db_sink.engine) as session:
        record = session.get(CollectionJobRunRecord, job_run_id)
        if not record:
            raise HTTPException(status_code=404, detail="采集运行记录不存在")
        child_run_ids = _json_loads(record.child_run_ids_json, [])
        child_runs = []
        if child_run_ids:
            child_runs = session.exec(select(FetchRunRecord).where(FetchRunRecord.id.in_(child_run_ids))).all()
        return {**serialize_collection_job_run(record), "child_runs": child_runs}


@app.get("/api/tasks")
def get_tasks():
    with Session(db_sink.engine) as session:
        return session.exec(select(FetchTaskRecord)).all()


@app.post("/api/tasks")
def create_task(task_data: TaskCreate):
    with Session(db_sink.engine) as session:
        task = FetchTaskRecord(
            fetcher_id=task_data.fetcher_id, cron_expr=task_data.cron_expr,
            params_json=json.dumps(task_data.params), created_at=datetime.datetime.now().isoformat()
        )
        session.add(task)
        session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with Session(db_sink.engine) as session:
        task = session.get(FetchTaskRecord, task_id)
        if task:
            session.delete(task)
            session.commit()
    load_tasks_to_scheduler()
    return {"status": "success"}


# ==================== 5. 抓取运行历史 ====================
@app.get("/api/fetch-runs")
def get_fetch_runs(
        fetcher_id: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
):
    with Session(db_sink.engine) as session:
        query = select(FetchRunRecord)
        if fetcher_id:
            query = query.where(FetchRunRecord.fetcher_id == fetcher_id)
        if job_id is not None:
            query = query.where(FetchRunRecord.job_id == job_id)
        if job_run_id is not None:
            query = query.where(FetchRunRecord.job_run_id == job_run_id)
        if run_scope:
            query = query.where(FetchRunRecord.run_scope == run_scope)
        if status:
            query = query.where(FetchRunRecord.status == status)
        if trigger_type:
            query = query.where(FetchRunRecord.trigger_type == trigger_type)
        query = query.order_by(FetchRunRecord.started_at.desc()).offset(skip).limit(limit)
        return session.exec(query).all()


@app.get("/api/fetch-runs/{run_id}")
def get_fetch_run(run_id: int):
    with Session(db_sink.engine) as session:
        run = session.get(FetchRunRecord, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="抓取运行记录不存在")
        return run


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
