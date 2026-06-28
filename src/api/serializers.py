"""跨 Router 复用的序列化器。

阶段1 拆分时，把原本散落在 app.py 的对外视图序列化集中到此，供多个域共享
（如账户视图同时被 accounts 域与 admin 运维视图使用）。仅依赖 ORM 模型，无副作用。
"""

from typing import Any, Dict

from models.db import UserRecord


def serialize_user(record: UserRecord) -> Dict[str, Any]:
    """账户对外视图（账户管理 + 运维列表共用）。"""
    return {
        "username": record.username,
        "role": record.role,
        "avatar": record.avatar or None,
        "is_active": record.is_active,
        "ai_beta_enabled": record.ai_beta_enabled,
        "last_login_at": record.last_login_at,
        "ai_translate_count": record.ai_translate_count or 0,
        "ai_ask_count": record.ai_ask_count or 0,
        "ai_last_used_at": record.ai_last_used_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
