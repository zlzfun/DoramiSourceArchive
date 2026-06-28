"""阶段1：FastAPI 依赖注入与声明式权限守卫。

这是把 5354 行的 ``api/app.py`` 拆成应用工厂 + 按域 Router 的基础设施。本模块只提供
**可注入的依赖**，不改变现有行为——现阶段全局鉴权仍由 ``app.py`` 的中间件 + 前缀表负责；
这些依赖供后续逐域迁出的 Router 以 ``Depends(...)`` 声明式使用，最终替代前缀表。

### 与现有测试的兼容性（关键约束）
现有测试大量 ``monkeypatch.setattr(api.app, "db_sink", ...)`` / ``vector_sink`` 等。为保证
被 patch 的单例在依赖里也能取到**最新值**，本模块一律通过 :func:`_app` 在**调用时**从
``api.app`` 动态取属性，绝不在导入期把单例的值绑定到本模块。``_app`` 用延迟 import 避免与
``api.app`` 形成导入环。
"""

import importlib
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from fastapi import HTTPException, Request
from sqlmodel import Session

_RAG_DISABLED_DETAIL = (
    "RAG 功能未启用。请在 config/backend.ini 中设置 [rag] enabled = true 后重启后端。"
)


def _app():
    """动态取 ``api.app`` 模块（延迟 import 防环 + 兼容测试 monkeypatch）。"""
    return importlib.import_module("api.app")


# ── 资源依赖 ────────────────────────────────────────────────────────────────

def get_db_sink():
    """关系库 sink（DatabaseStorage）。动态读取以兼容测试替换。"""
    return _app().db_sink


def get_vector_sink():
    """向量库 sink；RAG 未启用（vector_sink is None）时与现有 require_vector_sink 一致抛 503。"""
    vector_sink = _app().vector_sink
    if vector_sink is None:
        raise HTTPException(status_code=503, detail=_RAG_DISABLED_DETAIL)
    return vector_sink


def get_session() -> Iterator[Session]:
    """每请求一个 SQLModel Session 的生成器依赖，自动关闭。"""
    with Session(get_db_sink().engine) as session:
        yield session


# ── 会话与角色 ──────────────────────────────────────────────────────────────

def get_current_session(request: Request) -> Optional[Dict[str, Any]]:
    """当前登录会话（未登录返回 None）。复用 app.py 的 cookie/HMAC 校验逻辑。"""
    return _app().current_auth_session(request)


def require_login(request: Request) -> Dict[str, Any]:
    session = get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return session


def require_admin(request: Request) -> Dict[str, Any]:
    """仅 admin（账户管理面 / 运维管理面）。独立于 runtime 采集轴。"""
    session = require_login(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="该操作需要管理员账号")
    return session


def require_collector(request: Request) -> Dict[str, Any]:
    """采集面（runtime 允许 collector 且账户为 admin）。"""
    app = _app()
    session = require_login(request)
    if not app.collector_role_enabled(session):
        raise HTTPException(
            status_code=403,
            detail=f"collector API surface is disabled for runtime role '{app.runtime_role()}'",
        )
    return session


def require_reader(request: Request) -> Dict[str, Any]:
    """读者面（runtime 允许 reader 且账户为 admin/user）。"""
    app = _app()
    session = require_login(request)
    if not app.reader_role_enabled(session):
        raise HTTPException(
            status_code=403,
            detail=f"reader API surface is disabled for runtime role '{app.runtime_role()}'",
        )
    return session


# ── 访问策略（runtime role × account role 的统一视图）──────────────────────────

@dataclass(frozen=True)
class AccessPolicy:
    """把双轴（部署 runtime 角色 × 登录账户角色）折叠成一个可读视图。"""

    runtime_role: str
    account_role: Optional[str]
    collector_enabled: bool
    reader_enabled: bool

    @classmethod
    def from_session(cls, session: Optional[Dict[str, Any]]) -> "AccessPolicy":
        app = _app()
        return cls(
            runtime_role=app.runtime_role(),
            account_role=(session or {}).get("role"),
            collector_enabled=app.collector_role_enabled(session),
            reader_enabled=app.reader_role_enabled(session),
        )
