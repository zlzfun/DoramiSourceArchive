# /src/api/app.py

import asyncio
import json
import logging
import datetime
import base64
import hashlib
import hmac
import secrets
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
    SourceConfigRecord,
    SourceStateRecord,
    AppSettingRecord,
)
from models.content import BaseContent, SocialPostContent

# 引入动态抓取器注册中心
from fetchers.registry import fetcher_registry, DECOMMISSIONED_FETCHER_IDS
from api.skill_router import router as skill_router
from services import daily_brief as daily_brief_service
from llm.client import LLMNotConfigured, LLMError, ping as llm_ping

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


class GenericContent(BaseContent):
    # 拆分为结构类型与来源通道
    content_type = "restored_from_db"
    source_id = "database_restore"


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
    return {
        "role": runtime_role(),
        "account_role": session.get("role") if session else None,
        "collector_enabled": collector_role_enabled(session),
        "reader_enabled": reader_role_enabled(session),
        "rag_enabled": settings.rag.enabled,
    }


COLLECTOR_API_PREFIXES = (
    "/api/fetchers",
    "/api/source-health",
    "/api/source-states",
    "/api/fetch-runs",
    "/api/fetch/",
    "/api/archive/export",
    "/api/source-configs",
    "/api/import/social-posts",
    "/api/node-groups",
    "/api/collection-jobs",
    "/api/collection-job-runs",
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
# 向量库默认按需创建：[rag] enabled = false 时不构造 ChromaVectorStorage，
# 后端启动既快且不占用 embedding 模型所需内存。开启后实例仍会懒加载模型权重。
vector_sink: Optional[ChromaVectorStorage] = (
    ChromaVectorStorage(db_path=settings.storage.chroma_path)
    if settings.rag.enabled else None
)
pipeline = DataPipeline(storages=[db_sink])


def require_vector_sink() -> ChromaVectorStorage:
    if vector_sink is None:
        raise HTTPException(
            status_code=503,
            detail="RAG 功能未启用。请在 config/backend.ini 中设置 [rag] enabled = true 后重启后端。",
        )
    return vector_sink

app.mount("/mcp", _mcp_gate)
app.include_router(skill_router)

scheduler = AsyncIOScheduler()
COLLECTION_FETCH_CONCURRENCY = 4


# ==================== 管理员登录与会话 ====================
AUTH_COOKIE_NAME = settings.auth.cookie_name
AUTH_SESSION_SECONDS = settings.auth.session_seconds
AUTH_ACCOUNTS = {
    credential.username: {"password": credential.password, "role": "admin"}
    for credential in settings.auth.admin_users
}
for credential in settings.auth.user_users:
    if credential.username in AUTH_ACCOUNTS:
        raise ValueError(f"Auth user '{credential.username}' is configured in both admin_users and user_users")
    AUTH_ACCOUNTS[credential.username] = {"password": credential.password, "role": "user"}
AUTH_SECRET = settings.auth.secret or f"{settings.auth.admin_users}:{settings.auth.user_users}:{settings.storage.database_url}:dorami-auth-v2"


class AuthLoginParams(BaseModel):
    username: str
    password: str


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
    account = AUTH_ACCOUNTS.get(data.get("sub"))
    if not account or data.get("role") != account["role"]:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
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


@app.post("/api/auth/login")
def login_admin(params: AuthLoginParams, response: Response):
    username = params.username.strip()
    account = None
    for configured_username, configured_account in AUTH_ACCOUNTS.items():
        if hmac.compare_digest(username, configured_username):
            account = configured_account
            username = configured_username
            break
    if not account or not hmac.compare_digest(params.password, account["password"]):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    set_auth_cookie(response, username, account["role"])
    return {"authenticated": True, "user": {"username": username, "role": account["role"]}}


@app.get("/api/auth/session")
def get_auth_session(request: Request):
    session = current_auth_session(request)
    if session is None:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": {"username": session["sub"], "role": session["role"]}}


@app.get("/api/runtime")
def get_runtime(request: Request):
    return runtime_capabilities(current_auth_session(request))


@app.post("/api/auth/logout")
def logout_admin(response: Response):
    clear_auth_cookie(response)
    return {"authenticated": False}


# ==================== 定时任务系统核心逻辑 ====================
def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


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


def _json_dumps(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


def _json_loads(raw_value: Optional[str], default: Any = None) -> Any:
    if not raw_value:
        return default if default is not None else {}
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else {}


def _split_csv(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _date_end_value(raw_value: str) -> str:
    return raw_value if "T" in raw_value else f"{raw_value}T23:59:59"


def article_recency_order(*prefix_ordering):
    """Canonical newest-first ordering for cross-source archive views."""
    return (
        *prefix_ordering,
        ArticleRecord.publish_date.desc(),
        ArticleRecord.fetched_date.desc(),
        ArticleRecord.id.desc(),
    )


def apply_article_query_filters(
        query,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        exclude_source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        has_content: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
):
    if content_type:
        query = query.where(ArticleRecord.content_type == content_type)
    content_type_list = _split_csv(content_types)
    if content_type_list:
        query = query.where(ArticleRecord.content_type.in_(content_type_list))

    if source_id:
        query = query.where(ArticleRecord.source_id == source_id)
    source_id_list = _split_csv(source_ids)
    if source_id_list:
        query = query.where(ArticleRecord.source_id.in_(source_id_list))
    exclude_source_id_list = _split_csv(exclude_source_ids)
    if exclude_source_id_list:
        query = query.where(ArticleRecord.source_id.notin_(exclude_source_id_list))

    if job_id is not None:
        query = query.where(ArticleRecord.job_id == job_id)
    if job_run_id is not None:
        query = query.where(ArticleRecord.job_run_id == job_run_id)
    if fetch_run_id is not None:
        query = query.where(ArticleRecord.fetch_run_id == fetch_run_id)
    if run_scope:
        query = query.where(ArticleRecord.run_scope == run_scope)

    if is_vectorized is not None:
        query = query.where(ArticleRecord.is_vectorized == is_vectorized)
    if has_content is not None:
        query = query.where(ArticleRecord.has_content == has_content)
    if search:
        query = query.where(ArticleRecord.title.contains(search))

    if publish_date_start:
        query = query.where(ArticleRecord.publish_date >= publish_date_start)
    if publish_date_end:
        query = query.where(ArticleRecord.publish_date <= _date_end_value(publish_date_end))

    if fetched_date_start:
        query = query.where(ArticleRecord.fetched_date >= fetched_date_start)
    if fetched_date_end:
        query = query.where(ArticleRecord.fetched_date <= _date_end_value(fetched_date_end))

    return query


def serialize_feed_article(record: ArticleRecord, include_content: bool = True) -> Dict[str, Any]:
    extensions = _json_loads(record.extensions_json, {})
    metadata = {
        "id": record.id,
        "title": record.title,
        "source_url": record.source_url,
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "fetch_run_id": record.fetch_run_id,
        "job_id": record.job_id,
        "job_run_id": record.job_run_id,
        "source_group_id": record.source_group_id,
        "run_scope": record.run_scope,
        "has_content": record.has_content,
        "is_vectorized": record.is_vectorized,
        "extensions": extensions,
    }

    item = {
        "id": record.id,
        "title": record.title,
        "url": record.source_url,
        "metadata": metadata,
    }
    if include_content:
        item["content"] = record.content or ""
    return item


def article_to_markdown(record: ArticleRecord) -> str:
    metadata = serialize_feed_article(record, include_content=False)["metadata"]
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    content = record.content or ""
    return f"---\n{frontmatter}\n---\n\n# {record.title}\n\n{content}".strip()


def _model_dump(model: BaseModel, **kwargs) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _model_to_clean_dict(model: BaseModel) -> Dict[str, Any]:
    return {
        key: value
        for key, value in _model_dump(model).items()
        if value is not None and value != ""
    }


def normalize_delivery_policy(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = dict(policy or {})
    max_limit = min(max(int(raw.get("max_limit", 500)), 1), 500)
    default_limit = min(max(int(raw.get("default_limit", 100)), 1), max_limit)
    return {
        "include_content": _coerce_bool(raw.get("include_content", True)),
        "default_limit": default_limit,
        "max_limit": max_limit,
    }


def generate_subscription_token() -> str:
    return f"dsub_{secrets.token_urlsafe(32)}"


def hash_subscription_token(token: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def subscription_token_preview(token: str) -> str:
    return f"...{token[-6:]}"


def read_bearer_or_query_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.query_params.get("token") or "").strip()


def serialize_subscription(record: ReaderSubscriptionRecord, token: Optional[str] = None) -> Dict[str, Any]:
    data = {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "filters": _json_loads(record.filters_json, {}),
        "delivery_policy": normalize_delivery_policy(_json_loads(record.delivery_policy_json, {})),
        "token_preview": record.token_preview,
        "is_active": record.is_active,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if token:
        data["token"] = token
    return data


def resolve_subscription_by_token(
        session: Session,
        subscription_id: int,
        token: str,
) -> ReaderSubscriptionRecord:
    if not token:
        raise HTTPException(status_code=401, detail="缺少订阅源访问令牌")
    record = session.get(ReaderSubscriptionRecord, subscription_id)
    if (
        not record
        or not record.is_active
        or not hmac.compare_digest(record.token_hash, hash_subscription_token(token))
    ):
        raise HTTPException(status_code=401, detail="订阅源访问令牌无效")
    return record


def query_subscription_articles(
        session: Session,
        subscription: ReaderSubscriptionRecord,
        skip: int = 0,
        limit: Optional[int] = None,
) -> tuple[list[ArticleRecord], Dict[str, Any]]:
    filters = _json_loads(subscription.filters_json, {})
    policy = normalize_delivery_policy(_json_loads(subscription.delivery_policy_json, {}))
    effective_limit = limit if limit is not None else policy["default_limit"]
    safe_limit = min(max(int(effective_limit), 1), policy["max_limit"])
    query = apply_article_query_filters(
        select(ArticleRecord),
        content_type=filters.get("content_type"),
        content_types=filters.get("content_types"),
        source_id=filters.get("source_id"),
        source_ids=filters.get("source_ids"),
        job_id=filters.get("job_id"),
        job_run_id=filters.get("job_run_id"),
        fetch_run_id=filters.get("fetch_run_id"),
        run_scope=filters.get("run_scope"),
        has_content=filters.get("has_content", True),
        search=filters.get("search"),
        publish_date_start=filters.get("publish_date_start"),
        publish_date_end=filters.get("publish_date_end"),
        fetched_date_start=filters.get("fetched_date_start"),
        fetched_date_end=filters.get("fetched_date_end"),
    )
    records = session.exec(
        query.order_by(ArticleRecord.fetched_date.desc()).offset(skip).limit(safe_limit)
    ).all()
    return records, {"limit": safe_limit, "policy": policy, "filters": filters}


def subscription_source_ids(subscription: ReaderSubscriptionRecord) -> List[str]:
    """Extract the source_id scope a subscription filters on (source_ids/source_id)."""
    filters = _json_loads(subscription.filters_json, {})
    ids: List[str] = []
    for key in ("source_ids", "source_id"):
        value = filters.get(key)
        if value:
            ids.extend(part.strip() for part in str(value).split(",") if part.strip())
    return ids


def resolve_subscribed_source_ids(session: Session, username: str) -> List[str]:
    """Union of source_ids across the user's active subscriptions, sorted & de-duplicated."""
    if not username:
        return []
    subs = session.exec(
        select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == username,
            ReaderSubscriptionRecord.is_active == True,  # noqa: E712
        )
    ).all()
    collected: set[str] = set()
    for sub in subs:
        collected.update(subscription_source_ids(sub))
    return sorted(collected)


def resolve_subscription_sources_by_token(token: str) -> Optional[List[str]]:
    """令牌 → 检索可见的 source_id 列表（供 MCP 个性化作用域使用）。

    支持两类令牌：单订阅令牌（``dsub_``）限定到该订阅；个人聚合令牌（``dfeed_``）
    限定到该用户全部订阅的并集。令牌无效返回 None。
    """
    if not token:
        return None
    token_hash = hash_subscription_token(token)
    with Session(db_sink.engine) as session:
        record = session.exec(
            select(ReaderSubscriptionRecord).where(
                ReaderSubscriptionRecord.token_hash == token_hash,
                ReaderSubscriptionRecord.is_active == True,  # noqa: E712
            )
        ).first()
        if record:
            return subscription_source_ids(record)
        owner = resolve_feed_token_owner(session, token)
        if owner is not None:
            return resolve_subscribed_source_ids(session, owner)
    return None


def generate_feed_token() -> str:
    return f"dfeed_{secrets.token_urlsafe(32)}"


def resolve_feed_token_owner(session: Session, token: str) -> Optional[str]:
    """个人聚合令牌 → 归属用户名；令牌缺失/无效返回 None。"""
    if not token:
        return None
    token_hash = hash_subscription_token(token)
    record = session.exec(
        select(ReaderFeedTokenRecord).where(ReaderFeedTokenRecord.token_hash == token_hash)
    ).first()
    return record.owner_username if record else None


def feed_articles_for_owner(
        session: Session,
        username: str,
        *,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_ids: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = True,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
) -> list[ArticleRecord]:
    """聚合拉取：当前用户全部已订阅来源的文章，按发布时间倒序。

    仅按发布时间过滤（不暴露归档时间这一内部细节）。若调用方传入 source_ids，
    取其与已订阅集合的交集，避免越权拉取未订阅来源。
    """
    subscribed = resolve_subscribed_source_ids(session, username)
    if not subscribed:
        return []
    requested = [s.strip() for s in (source_ids or "").split(",") if s.strip()]
    allowed = [s for s in requested if s in set(subscribed)] if requested else subscribed
    if not allowed:
        return []
    query = apply_article_query_filters(
        select(ArticleRecord),
        content_type=content_type,
        content_types=content_types,
        source_ids=",".join(allowed),
        has_content=has_content,
        search=search,
        publish_date_start=publish_date_start,
        publish_date_end=publish_date_end,
    )
    return session.exec(
        query.order_by(ArticleRecord.publish_date.desc()).offset(skip).limit(limit)
    ).all()


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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
class BatchOpParams(BaseModel):
    ids: List[str]


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


class FetchBatchItem(BaseModel):
    fetcher_id: str
    params: Dict[str, Any] = PydanticField(default_factory=dict)


class FetchBatchParams(BaseModel):
    items: List[FetchBatchItem] = PydanticField(default_factory=list)


class SocialPostImportItem(BaseModel):
    platform: str = "x"
    post_id: str
    source_id: str = ""
    author_id: str = ""
    author_handle: str = ""
    author_name: str = ""
    text: str = ""
    title: str = ""
    source_url: str = ""
    publish_date: str = ""
    conversation_id: str = ""
    in_reply_to_id: str = ""
    quoted_post_id: str = ""
    reposted_post_id: str = ""
    lang: str = ""
    tags: List[str] = PydanticField(default_factory=list)
    media_urls: List[str] = PydanticField(default_factory=list)
    metrics: Dict[str, Any] = PydanticField(default_factory=dict)
    raw_data: Dict[str, Any] = PydanticField(default_factory=dict)


class SocialPostImportParams(BaseModel):
    source_id: str = "import_social_posts"
    posts: List[SocialPostImportItem]


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
    if source_config.source_type.lower() in {"rss", "atom"}:
        return "generic_rss"
    return ""


def build_source_fetch_params(source_config: SourceConfigRecord, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = parse_json_object(source_config.params_json)
    params.update({
        "source_id": source_config.source_id,
        "feed_url": source_config.url,
        "feed_name": source_config.name,
        "category": source_config.category,
    })
    if overrides:
        params.update(overrides)
    return params


def normalize_social_source_id(platform: str, author_handle: str, fallback: str) -> str:
    if fallback:
        return fallback.strip()
    safe_platform = (platform or "social").strip().lower().replace("/", "_")
    safe_handle = (author_handle or "unknown").strip().lower().lstrip("@").replace("/", "_")
    return f"{safe_platform}_{safe_handle}"


def build_social_post_content(post: SocialPostImportItem, batch_source_id: str) -> SocialPostContent:
    platform = post.platform.strip() or "x"
    post_id = post.post_id.strip()
    if not post_id:
        raise ValueError("post_id 不能为空")

    source_id = normalize_social_source_id(platform, post.author_handle, post.source_id or batch_source_id)
    title = post.title.strip() or (post.text.strip()[:80] if post.text else f"{platform} post {post_id}")
    source_url = post.source_url.strip()
    if not source_url and post.author_handle:
        source_url = f"https://x.com/{post.author_handle.lstrip('@')}/status/{post_id}"

    publish_date = post.publish_date.strip() or _now_iso()
    raw_data = dict(post.raw_data or {})
    raw_data.setdefault("import_source_id", batch_source_id)

    return SocialPostContent(
        id=f"{source_id}_{post_id}",
        title=title,
        source_url=source_url,
        publish_date=publish_date,
        source_id=source_id,
        content=post.text,
        has_content=bool(post.text),
        platform=platform,
        author_id=post.author_id,
        author_handle=post.author_handle,
        author_name=post.author_name,
        post_id=post_id,
        conversation_id=post.conversation_id,
        in_reply_to_id=post.in_reply_to_id,
        quoted_post_id=post.quoted_post_id,
        reposted_post_id=post.reposted_post_id,
        lang=post.lang,
        tags=post.tags,
        media_urls=post.media_urls,
        metrics=post.metrics,
        raw_data=raw_data,
    )


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


# ==================== Reader 内容源目录 ====================
CONTENT_TYPE_CATEGORY = {
    "rss_article": "RSS 资讯",
    "web_article": "网页文章",
    "wechat_article": "微信公众号",
    "arxiv": "arXiv 论文",
    "github_release": "GitHub 发布",
    "github_repository": "代码仓库",
    "hf_model": "模型",
    "huggingface_model": "模型",
    "tech_conference": "技术会议",
    "social_post": "社交动态",
    "webhook_trigger": "工作流",
    "daily_brief": "AI 日报",
}

# 日报作为「特殊源」的展示元数据。日报不是抓取器（不进 FetcherRegistry），
# 在内容源目录里直接特判其名称/图标/简介，避免被采集触发流程误调。
DAILY_BRIEF_SOURCE_ID = daily_brief_service.DAILY_BRIEF_SOURCE_ID
DAILY_BRIEF_SOURCE_META = {
    "name": "哆啦美·AI资讯日报",
    "icon": "🤖",
    "desc": "由后端大模型每日自动生成的 AI 资讯日报，汇总择优近期归档内容。",
    "content_type": "daily_brief",
    "category": "AI 日报",
    # 归到哆啦美自有品牌身份，使前端徽标走品牌色「美」字而非通用齿轮兜底。
    "source_owner": "dorami",
}


def _source_category(content_type: Optional[str]) -> str:
    if not content_type:
        return "其它"
    return CONTENT_TYPE_CATEGORY.get(content_type, content_type)


def _registry_source_meta() -> Dict[str, Dict[str, Any]]:
    """source_id -> 抓取器注册元数据（名称/简介/图标），用于内容源目录展示。"""
    return {meta["id"]: meta for meta in fetcher_registry.get_all_metadata()}


def _friendly_source_name(source_id: str, registry_meta: Dict[str, Dict[str, Any]]) -> str:
    meta = registry_meta.get(source_id)
    if meta and meta.get("name"):
        return meta["name"]
    return SOURCE_FRIENDLY_NAMES.get(source_id, source_id)


@app.get("/api/reader/sources")
def get_reader_sources(request: Request):
    """读者层内容源目录：可订阅来源 = 所有已注册抓取源 ∪ 已归档来源 ∪ 已订阅来源。

    即便某个源历史产出为 0，它仍会出现在目录里，用户可提前订阅以接收其后续产出。
    """
    username = current_username(request)
    ensure_default_subscriptions(username)
    registry_meta = _registry_source_meta()
    with Session(db_sink.engine) as session:
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
        subscribed_ids = set(resolve_subscribed_source_ids(session, username))

    by_source: Dict[str, Dict[str, Any]] = {}

    def _ensure_entry(source_id: str, content_type: Optional[str] = None) -> Dict[str, Any]:
        entry = by_source.get(source_id)
        if entry is None:
            meta = registry_meta.get(source_id, {})
            if source_id == DAILY_BRIEF_SOURCE_ID:
                meta = {**DAILY_BRIEF_SOURCE_META, **meta}
            resolved_type = content_type or meta.get("content_type") or ""
            entry = {
                "source_id": source_id,
                "name": meta.get("name") or _friendly_source_name(source_id, registry_meta),
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
                "count": 0,
                "last_fetched": "",
                "subscribed": source_id in subscribed_ids,
                "registered": source_id in registry_meta,
                "_primary_count": -1,
            }
            by_source[source_id] = entry
        return entry

    # 1. 所有已注册抓取源（含历史产出为 0 者，使新源可被提前订阅）。
    for source_id in registry_meta:
        _ensure_entry(source_id)

    # 1b. 日报特殊源：即使尚未生成过日报也预先出现，便于提前订阅。
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

    # 3. 已订阅但既未注册也无归档的来源也要出现，便于退订。
    for source_id in subscribed_ids:
        _ensure_entry(source_id)

    sources = sorted(
        ({k: v for k, v in entry.items() if k != "_primary_count"} for entry in by_source.values()),
        key=lambda s: (s["category"], -s["count"], s["name"]),
    )
    return {
        "sources": sources,
        "subscribed_source_ids": sorted(subscribed_ids),
        "total_sources": len(sources),
    }


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


@app.post("/api/reader/sources/{source_id}/subscribe")
def subscribe_source(source_id: str, request: Request):
    """一键订阅单个内容源：尚未订阅则创建一个仅含该源的订阅，已订阅则幂等返回。

    交付令牌、限额等高级设置使用默认值，留待用户在「我的订阅」中按需编辑。
    """
    username = current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    registry_meta = _registry_source_meta()
    with Session(db_sink.engine) as session:
        already = source_id in set(resolve_subscribed_source_ids(session, username))
        if not already:
            _create_single_source_subscription(
                session, username, source_id, _friendly_source_name(source_id, registry_meta)
            )
            session.commit()
        subscribed_ids = sorted(set(resolve_subscribed_source_ids(session, username)))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": True,
        "subscribed_source_ids": subscribed_ids,
    }


@app.delete("/api/reader/sources/{source_id}/subscribe")
def unsubscribe_source(source_id: str, request: Request):
    """一键取消订阅：从当前用户的所有订阅范围内移除该源，因此清空的订阅会被删除。"""
    username = current_username(request)
    source_id = (source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    with Session(db_sink.engine) as session:
        records = session.exec(
            select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
        ).all()
        for record in records:
            ids = subscription_source_ids(record)
            if source_id not in ids:
                continue
            remaining = [sid for sid in ids if sid != source_id]
            if remaining:
                filters = _json_loads(record.filters_json, {}) or {}
                filters.pop("source_id", None)
                filters["source_ids"] = ",".join(remaining)
                record.filters_json = _json_dumps(filters)
                record.updated_at = _now_iso()
                session.add(record)
            else:
                session.delete(record)
        session.commit()
        subscribed_ids = sorted(set(resolve_subscribed_source_ids(session, username)))
    return {
        "status": "success",
        "source_id": source_id,
        "subscribed": False,
        "subscribed_source_ids": subscribed_ids,
    }


# ==================== Reader 个人聚合接口令牌 ====================
@app.get("/api/reader/feed-token")
def get_feed_token(request: Request):
    """当前用户的个人聚合接口令牌状态（仅返回预览，不回显明文）。"""
    username = current_username(request)
    with Session(db_sink.engine) as session:
        record = session.get(ReaderFeedTokenRecord, username)
        subscribed = resolve_subscribed_source_ids(session, username)
    return {
        "exists": record is not None,
        "token_preview": record.token_preview if record else "",
        "created_at": record.created_at if record else None,
        "updated_at": record.updated_at if record else None,
        "subscribed_source_count": len(subscribed),
    }


@app.post("/api/reader/feed-token/rotate")
def rotate_feed_token(request: Request):
    """创建或轮换当前用户的个人聚合接口令牌；明文仅在本次响应中返回一次。"""
    username = current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="需要登录")
    token = generate_feed_token()
    now = _now_iso()
    with Session(db_sink.engine) as session:
        record = session.get(ReaderFeedTokenRecord, username)
        if record is None:
            record = ReaderFeedTokenRecord(owner_username=username, created_at=now, updated_at=now)
        record.token_hash = hash_subscription_token(token)
        record.token_preview = subscription_token_preview(token)
        record.updated_at = now
        session.add(record)
        session.commit()
    return {"token": token, "token_preview": subscription_token_preview(token)}


@app.get("/api/public/feed/articles")
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
):
    """个人聚合拉取接口：用个人聚合令牌一次性拉取当前用户全部已订阅来源的文章。

    支持按发布时间（publish_date_start/end）、来源、类型、关键词筛选；适合日报等下游场景。
    """
    safe_limit = min(max(limit, 1), 500)
    token = read_bearer_or_query_token(request)
    with Session(db_sink.engine) as session:
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


@app.get("/api/public/feed/articles.md")
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
):
    """个人聚合拉取接口的 Markdown 批量导出变体（最多 200 条）。"""
    safe_limit = min(max(limit, 1), 200)
    token = read_bearer_or_query_token(request)
    with Session(db_sink.engine) as session:
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


# ==================== Reader 订阅源 ====================
def _owned_subscription_or_404(session: Session, subscription_id: int, username: str) -> ReaderSubscriptionRecord:
    record = session.get(ReaderSubscriptionRecord, subscription_id)
    if not record or record.owner_username != username:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    return record


@app.get("/api/subscriptions")
def get_subscriptions(request: Request, is_active: Optional[bool] = None):
    username = current_username(request)
    with Session(db_sink.engine) as session:
        query = select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == username)
        if is_active is not None:
            query = query.where(ReaderSubscriptionRecord.is_active == is_active)
        records = session.exec(query.order_by(ReaderSubscriptionRecord.name)).all()
        return [serialize_subscription(record) for record in records]


@app.get("/api/subscriptions/{subscription_id}")
def get_subscription(subscription_id: int, request: Request):
    with Session(db_sink.engine) as session:
        record = _owned_subscription_or_404(session, subscription_id, current_username(request))
        return serialize_subscription(record)


@app.post("/api/subscriptions")
def create_subscription(params: SubscriptionCreate, request: Request):
    name = params.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="订阅源名称不能为空")
    token = generate_subscription_token()
    now = _now_iso()
    record = ReaderSubscriptionRecord(
        owner_username=current_username(request),
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
    with Session(db_sink.engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_subscription(record, token=token)


@app.put("/api/subscriptions/{subscription_id}")
def update_subscription(subscription_id: int, params: SubscriptionUpdate, request: Request):
    with Session(db_sink.engine) as session:
        record = _owned_subscription_or_404(session, subscription_id, current_username(request))
        update_data = _model_dump(params, exclude_unset=True)
        if "name" in update_data:
            name = (update_data["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="订阅源名称不能为空")
            record.name = name
        if "description" in update_data:
            record.description = (update_data["description"] or "").strip()
        if "filters" in update_data and update_data["filters"] is not None:
            record.filters_json = _json_dumps(
                {key: value for key, value in update_data["filters"].items() if value not in (None, "")}
            )
        if "delivery_policy" in update_data and update_data["delivery_policy"] is not None:
            record.delivery_policy_json = _json_dumps(normalize_delivery_policy(update_data["delivery_policy"]))
        if "is_active" in update_data:
            record.is_active = update_data["is_active"]
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_subscription(record)


@app.post("/api/subscriptions/{subscription_id}/rotate-token")
def rotate_subscription_token(subscription_id: int, request: Request):
    token = generate_subscription_token()
    with Session(db_sink.engine) as session:
        record = _owned_subscription_or_404(session, subscription_id, current_username(request))
        record.token_hash = hash_subscription_token(token)
        record.token_preview = subscription_token_preview(token)
        record.updated_at = _now_iso()
        session.add(record)
        session.commit()
        session.refresh(record)
        return serialize_subscription(record, token=token)


@app.delete("/api/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: int, request: Request):
    with Session(db_sink.engine) as session:
        record = _owned_subscription_or_404(session, subscription_id, current_username(request))
        session.delete(record)
        session.commit()
        return {"status": "success"}


@app.get("/api/public/subscriptions/{subscription_id}/articles")
def get_public_subscription_articles(
        subscription_id: int,
        request: Request,
        skip: int = 0,
        limit: Optional[int] = None,
):
    with Session(db_sink.engine) as session:
        subscription = resolve_subscription_by_token(
            session,
            subscription_id,
            read_bearer_or_query_token(request),
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


class PublicSubscriptionSearchBody(BaseModel):
    query: str
    top_k: int = 5
    score_threshold: float = 1.5
    rerank: bool = False


@app.post("/api/public/subscriptions/{subscription_id}/vector/search")
async def public_subscription_vector_search(
        subscription_id: int,
        body: PublicSubscriptionSearchBody,
        request: Request,
):
    """带令牌的、按订阅源范围约束的语义检索（供下游 Agent 应用个性化使用）。"""
    with Session(db_sink.engine) as session:
        subscription = resolve_subscription_by_token(
            session, subscription_id, read_bearer_or_query_token(request),
        )
        filters = _json_loads(subscription.filters_json, {})
        source_ids = subscription_source_ids(subscription)
        sub_id, sub_name = subscription.id, subscription.name

    results = await run_vector_search(
        body.query,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
        rerank=body.rerank,
        content_type=filters.get("content_type"),
        source_ids=source_ids or None,
    )
    return {
        "status": "success",
        "subscription": {"id": sub_id, "name": sub_name},
        "scoped_source_ids": source_ids,
        "count": len(results),
        "results": results,
        "reranked": body.rerank,
    }


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


@app.post("/api/import/social-posts")
async def import_social_posts(params: SocialPostImportParams):
    saved_count = 0
    skipped_count = 0
    errors = []

    for index, post in enumerate(params.posts):
        try:
            content = build_social_post_content(post, params.source_id)
            if await db_sink.save(content):
                saved_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            errors.append({
                "index": index,
                "post_id": post.post_id,
                "error": str(e),
            })

    return {
        "status": "partial_success" if errors else "success",
        "received_count": len(params.posts),
        "saved_count": saved_count,
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "errors": errors,
    }


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


@app.get("/api/articles")
def get_articles(
        request: Request,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        exclude_source_ids: Optional[str] = None,  # CSV：从结果中排除的来源（如知识台账排除日报源）
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,  # ✨ 升级：起始原始发布日期
        publish_date_end: Optional[str] = None,  # ✨ 升级：结束原始发布日期
        fetched_date_start: Optional[str] = None,  # ✨ 升级：起始中枢收录日期
        fetched_date_end: Optional[str] = None,  # ✨ 升级：结束中枢收录日期
        subscribed_scope: str = "off",  # off | only | prioritize：相对当前用户订阅的源
        skip: int = 0,
        limit: int = 100,
        include_total: bool = False,
):
    scope = (subscribed_scope or "off").strip().lower()
    safe_limit = min(max(int(limit), 1), 500)
    safe_skip = max(int(skip), 0)
    with Session(db_sink.engine) as session:
        subscribed_ids = (
            resolve_subscribed_source_ids(session, current_username(request))
            if scope in {"only", "prioritize"} else []
        )
        filter_kwargs = {
            "content_type": content_type,
            "source_id": source_id,
            "exclude_source_ids": exclude_source_ids,
            "job_id": job_id,
            "job_run_id": job_run_id,
            "fetch_run_id": fetch_run_id,
            "run_scope": run_scope,
            "is_vectorized": is_vectorized,
            "search": search,
            "publish_date_start": publish_date_start,
            "publish_date_end": publish_date_end,
            "fetched_date_start": fetched_date_start,
            "fetched_date_end": fetched_date_end,
        }
        query = apply_article_query_filters(select(ArticleRecord), **filter_kwargs)
        count_query = apply_article_query_filters(select(func.count(ArticleRecord.id)), **filter_kwargs)
        if scope == "only":
            # 仅当前用户已订阅的源；无订阅时显式返回空集。
            query = query.where(ArticleRecord.source_id.in_(subscribed_ids or ["__none__"]))
            count_query = count_query.where(ArticleRecord.source_id.in_(subscribed_ids or ["__none__"]))
        if scope == "prioritize" and subscribed_ids:
            subscribed_first = case((ArticleRecord.source_id.in_(subscribed_ids), 0), else_=1)
            query = query.order_by(*article_recency_order(subscribed_first))
        else:
            query = query.order_by(*article_recency_order())
        total = int(session.exec(count_query).one() or 0) if include_total else None
        records = session.exec(query.offset(safe_skip).limit(safe_limit)).all()
        if not include_total:
            return records
        return {
            "items": records,
            "total": total,
            "skip": safe_skip,
            "limit": safe_limit,
            "next_skip": safe_skip + len(records) if safe_skip + len(records) < total else None,
        }


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


@app.post("/api/articles")
async def create_article_manual(params: dict = Body(...)):
    """接收前端传来的手工录入数据并入库"""
    content_obj = GenericContent(
        id=params.get("id"),
        title=params.get("title", "未命名"),
        source_url=params.get("source_url", ""),
        publish_date=params.get("publish_date", ""),
        content=params.get("content", ""),
        has_content=True if params.get("content") else False
    )
    content_obj.content_type = params.get("content_type", "manual_entry")
    content_obj.source_id = params.get("source_id", "manual")

    try:
        extensions = json.loads(params.get("extensions_json", "{}"))
        for k, v in extensions.items():
            setattr(content_obj, k, v)
    except Exception as e:
        pass

    success = await db_sink.save(content_obj)
    if not success:
        raise HTTPException(status_code=400, detail="该条目 ID 已存在，请避免重复录入")

    return {"status": "success"}


def _maybe_rewind_daily_brief_cursor(record) -> None:
    """删除日报源记录时，若它正是最后推进游标的那一期，则把增量游标回退到
    生成该期之前的值（记录里存了 cursor_before / cursor_after），使删除最新一期
    后可直接重新生成。删除历史中间某期（cursor_after 不等于当前游标）则不动游标。
    """
    if getattr(record, "source_id", None) != DAILY_BRIEF_SOURCE_ID:
        return
    ext = _json_loads(record.extensions_json, {})
    cursor_after = ext.get("cursor_after")
    if not cursor_after:
        return
    with Session(db_sink.engine) as session:
        if daily_brief_service.read_cursor(session) == cursor_after:
            daily_brief_service.set_setting(
                session, daily_brief_service.KEY_CURSOR, ext.get("cursor_before") or ""
            )


@app.delete("/api/articles/{article_id:path}")
async def delete_article(article_id: str):
    record = await db_sink.get(article_id)
    if not record: raise HTTPException(status_code=404, detail="文章未找到")
    if record.is_vectorized and vector_sink is not None:
        await vector_sink.delete(article_id)
    await db_sink.delete(article_id)
    _maybe_rewind_daily_brief_cursor(record)
    return {"status": "success"}


@app.post("/api/articles/batch-delete")
async def batch_delete_articles(params: BatchOpParams):
    for uid in params.ids:
        record = await db_sink.get(uid)
        if record:
            if record.is_vectorized and vector_sink is not None:
                await vector_sink.delete(uid)
            await db_sink.delete(uid)
            _maybe_rewind_daily_brief_cursor(record)
    return {"status": "success"}


class ArticleUpdateParams(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    source_url: Optional[str] = None
    extensions_json: Optional[str] = None


@app.put("/api/articles/{article_id:path}")
async def update_article(article_id: str, params: ArticleUpdateParams):
    update_data = {k: v for k, v in params.dict().items() if v is not None}
    if "content" in update_data or "title" in update_data:
        update_data["is_vectorized"] = False
        if vector_sink is not None:
            await vector_sink.delete(article_id)

    success = await db_sink.update(article_id, update_data)
    if not success: raise HTTPException(status_code=404, detail="更新失败")
    return {"status": "success"}


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
def _record_to_content(record: ArticleRecord) -> GenericContent:
    """将 ArticleRecord 转换为可向量化的 GenericContent 对象。"""
    obj = GenericContent(
        id=record.id, title=record.title, publish_date=record.publish_date,
        source_url=record.source_url, content=record.content,
        fetched_date=record.fetched_date, has_content=record.has_content,
    )
    obj.content_type = record.content_type
    obj.source_id = record.source_id
    return obj


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
    """对所有 is_vectorized=False 的文章执行向量化，跳过已索引的条目。"""
    vs = require_vector_sink()
    with Session(db_sink.engine) as session:
        from sqlmodel import select as sm_select
        records = session.exec(
            sm_select(ArticleRecord).where(ArticleRecord.is_vectorized == False)
        ).all()
    success_count = 0
    for record in records:
        if await vs.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(record.id)
            success_count += 1
    return {"status": "success", "count": success_count, "total_pending": len(records)}


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


# ==================== LLM 配置 & 每日日报 ====================

def _llm_api_key_preview(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def _llm_config_response() -> Dict[str, Any]:
    with Session(db_sink.engine) as session:
        cfg = daily_brief_service.resolve_llm_config(session)
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "configured": cfg.configured,
        "api_key_set": bool(cfg.api_key),
        "api_key_preview": _llm_api_key_preview(cfg.api_key),
    }


class LLMConfigUpdate(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@app.get("/api/llm/config")
def get_llm_config():
    """读取大模型有效配置（脱敏，绝不返回明文 api_key）。"""
    return _llm_config_response()


@app.post("/api/llm/config")
def set_llm_config(payload: LLMConfigUpdate):
    """更新大模型运行期配置（写入 app_settings 覆盖 ini 默认）。

    api_key 留空（None 或空串）表示不修改；base_url/model 等同理按需覆盖。
    """
    with Session(db_sink.engine) as session:
        if payload.base_url is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_BASE_URL, payload.base_url.strip())
        if payload.model is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_MODEL, payload.model.strip())
        if payload.api_key:  # 仅在非空时更新，避免清空已有机密
            daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_API_KEY, payload.api_key.strip())
        if payload.temperature is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_TEMPERATURE, str(payload.temperature))
        if payload.max_tokens is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_LLM_MAX_TOKENS, str(payload.max_tokens))
    return _llm_config_response()


@app.post("/api/llm/config/test")
async def test_llm_config():
    """用当前有效配置测试连接。"""
    with Session(db_sink.engine) as session:
        cfg = daily_brief_service.resolve_llm_config(session)
    if not cfg.configured:
        raise HTTPException(status_code=400, detail="LLM 未配置（需 base_url / api_key / model）")
    try:
        return await llm_ping(cfg)
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"连接失败: {exc}")


class DailyBriefConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    cron: Optional[str] = None
    cursor: Optional[str] = None  # 手动设置/重置增量游标；空串=重置（下次用近 1 天兜底窗口）
    top_n: Optional[int] = None   # 日报精选条数


class DailyBriefGenerateParams(BaseModel):
    report_date: Optional[str] = None
    dry_run: bool = False
    top_n: Optional[int] = None   # 本次生成的精选条数（不传则用配置值）


def _daily_brief_config_response() -> Dict[str, Any]:
    with Session(db_sink.engine) as session:
        return {
            "enabled": daily_brief_service.daily_brief_enabled(session),
            "cron": daily_brief_service.daily_brief_cron(session),
            "cursor": daily_brief_service.read_cursor(session),
            "top_n": daily_brief_service.daily_brief_top_n(session),
            "last_run": daily_brief_service.get_json_setting(session, daily_brief_service.KEY_LAST_RUN, None),
        }


@app.get("/api/daily-brief/config")
def get_daily_brief_config():
    return _daily_brief_config_response()


@app.post("/api/daily-brief/config")
def set_daily_brief_config(payload: DailyBriefConfigUpdate):
    if payload.cron is not None:
        cron_expr = payload.cron.strip()
        if len(cron_expr.split()) != 5:
            raise HTTPException(status_code=400, detail="cron 表达式必须是 5 段，例如：30 8 * * *")
    if payload.top_n is not None and not (
        daily_brief_service.TOP_N_MIN <= payload.top_n <= daily_brief_service.TOP_N_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"精选条数需在 {daily_brief_service.TOP_N_MIN}–{daily_brief_service.TOP_N_MAX} 之间",
        )
    with Session(db_sink.engine) as session:
        if payload.enabled is not None:
            daily_brief_service.set_setting(
                session, daily_brief_service.KEY_ENABLED, "true" if payload.enabled else "false"
            )
        if payload.cron is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_CRON, payload.cron.strip())
        if payload.cursor is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_CURSOR, payload.cursor.strip())
        if payload.top_n is not None:
            daily_brief_service.set_setting(session, daily_brief_service.KEY_TOP_N, str(payload.top_n))
    # 仅在 collector 运行角色下有调度引擎；reader 角色不接日报 cron。
    if runtime_collector_enabled():
        reload_daily_brief_schedule()
    return _daily_brief_config_response()


@app.post("/api/daily-brief/generate")
async def generate_daily_brief_endpoint(payload: Optional[DailyBriefGenerateParams] = None):
    """手动触发日报生成（同步等待，耗时数十秒到数分钟）。"""
    params = payload or DailyBriefGenerateParams()
    if params.top_n is not None and not (
        daily_brief_service.TOP_N_MIN <= params.top_n <= daily_brief_service.TOP_N_MAX
    ):
        raise HTTPException(
            status_code=400,
            detail=f"精选条数需在 {daily_brief_service.TOP_N_MIN}–{daily_brief_service.TOP_N_MAX} 之间",
        )
    try:
        result = await daily_brief_service.generate_daily_brief(
            storage=db_sink,
            report_date=params.report_date,
            trigger="manual",
            dry_run=params.dry_run,
            top_n=params.top_n,
        )
    except LLMNotConfigured as exc:
        daily_brief_service.set_progress("error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        daily_brief_service.set_progress("error", f"生成失败: {exc}")
        raise HTTPException(status_code=502, detail=f"日报生成失败: {exc}")
    except Exception as exc:  # noqa: BLE001 兜底：让进度反映失败，再抛出
        daily_brief_service.set_progress("error", f"生成失败: {exc}")
        raise
    if not params.dry_run and result.get("article_id"):
        await auto_vectorize_after_fetch([result["article_id"]])
    return result


@app.get("/api/daily-brief/runs")
def get_daily_brief_runs():
    with Session(db_sink.engine) as session:
        last_run = daily_brief_service.get_json_setting(session, daily_brief_service.KEY_LAST_RUN, None)
        rows = session.exec(
            select(ArticleRecord)
            .where(ArticleRecord.source_id == DAILY_BRIEF_SOURCE_ID)
            .order_by(ArticleRecord.publish_date.desc())
            .limit(30)
        ).all()
    history = [
        {
            "id": row.id,
            "report_date": row.publish_date,
            "title": row.title,
            "fetched_date": row.fetched_date,
        }
        for row in rows
    ]
    return {"last_run": last_run, "history": history}


@app.get("/api/daily-brief/progress")
def get_daily_brief_progress():
    """当前日报生成的实时阶段进度（内存态，供前端轮询）。"""
    return daily_brief_service.get_progress()


@app.get("/api/daily-brief/pipeline")
def get_daily_brief_pipeline():
    """日报生成管线的真实提示词与关键参数，供前端流程图展示（与代码同步，不在前端硬抄）。"""
    prompts = daily_brief_service.prompts
    with Session(db_sink.engine) as session:
        cfg = daily_brief_service.resolve_llm_config(session)
        top_n = daily_brief_service.daily_brief_top_n(session)
    return {
        "model": cfg.model,
        "configured": cfg.configured,
        "params": {
            "top_n": top_n,
            "max_total": 120,          # collect_candidates 默认总量上限
            "per_source_cap": 15,      # collect_candidates 每来源候选上限
            "map_concurrency": cfg.map_concurrency,
            "map_max_body_chars": 6000,  # MAP 单篇正文截断
            "recent_brief_days": 3,    # REDUCE 注入的近期日报天数
        },
        "allowed_classifications": prompts.ALLOWED_CLASSIFICATIONS,
        "map_system_prompt": prompts.MAP_SYSTEM_PROMPT,
        "reduce_system_prompt": prompts.REDUCE_SYSTEM_PROMPT,
    }


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
        candidates = vs.rerank(query_text, candidates[:top_k * 2])
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
        candidates = vs.rerank(query.query, candidates[:query.top_k * 2])
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
    """
    vs = require_vector_sink()
    vs.rebuild_collection()

    with Session(db_sink.engine) as session:
        records = session.exec(select(ArticleRecord)).all()

    # 先批量重置 is_vectorized 标志
    for record in records:
        await db_sink.update(record.id, {"is_vectorized": False})

    # 重新向量化
    success_count = 0
    for record in records:
        if await vs.save(_record_to_content(record)):
            await db_sink.mark_as_vectorized(record.id)
            success_count += 1

    return {"status": "success", "total_reindexed": success_count, "total_articles": len(records)}


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
     "description": "语义向量搜索文章，支持中英文，可按日期/来源/类型过滤"},
    {"name": "browse_articles",
     "description": "按条件过滤浏览文章列表（来源、类型、日期区间），适合日报生成"},
    {"name": "get_article",
     "description": "按 ID 获取单篇文章完整内容（含正文）"},
    {"name": "list_sources",
     "description": "列出所有已知数据来源，获取可用的 source_id 和 content_type"},
    {"name": "get_rag_context",
     "description": "语义检索后组装格式化 RAG 上下文字符串，可直接拼入 LLM Prompt"},
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
