"""令牌交付计量的接线(issue #312)。

交付路径(文章 feed / 热点事件榜 / 单订阅接口 / MCP 工具)在组好返回值之后到这里记一笔。
用自己的短会话写:不碰请求会话(提交会让请求里已加载的对象过期),也不让计量的失败
波及交付——任何异常只打日志。口径与表见 ``services/token_usage``。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from api import deps
from api.feed_service import resolve_token_identity
from api.tokens import hash_subscription_token
from services import token_usage
from services.token_usage import TokenIdentity

logger = logging.getLogger(__name__)


def meter_call(identity: Optional[TokenIdentity], channel: str, items: int = 0) -> None:
    """记一次成功调用;identity 为空(没认出令牌)时什么都不做。"""
    if identity is None:
        return
    try:
        with Session(deps.get_db_sink().engine) as session:
            token_usage.record_call(session, identity, channel, items=items)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量写入失败(不影响交付):%s", channel, exc_info=True)


def meter_rejected(token: Optional[str], channel: str) -> None:
    """一枚没通过校验的令牌:它若曾经有效(留过记录),在它名下记一次被拒;认不出的不留行。"""
    if not token:
        return
    try:
        with Session(deps.get_db_sink().engine) as session:
            token_usage.record_rejected(session, hash_subscription_token(token), channel)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量写入失败(不影响交付):%s", channel, exc_info=True)


def record_mcp_usage(token: str, tool: str, items: int, ok: bool) -> None:
    """MCP 工具的计量回调(``mcp_server.build_mcp_app(usage_recorder=…)``;由它放线程池调)。"""
    channel = token_usage.mcp_channel(tool)
    if not ok:
        meter_rejected(token, channel)
        return
    try:
        identity = resolve_token_identity(token)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量解析令牌失败(不影响交付):%s", channel, exc_info=True)
        return
    meter_call(identity, channel, items)
