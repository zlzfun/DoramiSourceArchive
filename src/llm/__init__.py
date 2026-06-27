"""大模型（OpenAI 兼容协议）调用层。

统一走 {base_url}/chat/completions，纯 httpx 实现，不引入厂商 SDK。
"""

from .client import (
    ChatMessage,
    LLMError,
    LLMNotConfigured,
    UsageMeta,
    chat_completion,
    parse_json_object,
    ping,
    set_usage_recorder,
)

__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMNotConfigured",
    "UsageMeta",
    "chat_completion",
    "parse_json_object",
    "ping",
    "set_usage_recorder",
]
