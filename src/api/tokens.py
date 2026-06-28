"""订阅/聚合令牌与投递策略 helper（阶段1 共享 helper 模块化）。

把订阅令牌（``dsub_``）、个人聚合令牌（``dfeed_``）的生成/哈希/预览/读取，以及
投递策略归一化集中到此。这些 helper 仅依赖 ``config.settings`` 派生的 ``AUTH_SECRET``
与 textutils 纯工具，不依赖 app 级可变全局（db_sink 等），故可被任意 Router 安全
import、不与 api.app 成环。

``AUTH_SECRET`` 的推导以本模块为单一来源（app.py re-export），保持与历史已签发
令牌一致：用于会话 token 与订阅/聚合令牌的 HMAC-SHA256 签名。
"""

import hashlib
import hmac
import secrets
from typing import Any, Dict, Optional

from fastapi import Request

from api.textutils import _coerce_bool
from config import settings

# 账户已迁移到数据库托管；AUTH_SECRET 仍用于会话 token 与订阅/聚合令牌的 HMAC 签名，
# 保持原推导以兼容历史已签发的令牌（缺省时从首启播种配置 + 数据库 URL 派生）。
AUTH_SECRET = settings.auth.secret or (
    f"{settings.auth.admin_users}:{settings.auth.user_users}:"
    f"{settings.storage.database_url}:dorami-auth-v2"
)


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


def generate_feed_token() -> str:
    return f"dfeed_{secrets.token_urlsafe(32)}"


def read_bearer_or_query_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.query_params.get("token") or "").strip()
