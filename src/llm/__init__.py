"""大模型（OpenAI 兼容协议）调用层。

统一走 {base_url}/chat/completions，纯 httpx 实现，不引入厂商 SDK。
"""

from .client import (
    ChatMessage,
    LLMError,
    LLMNotConfigured,
    chat_completion,
    parse_json_object,
    ping,
)

__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMNotConfigured",
    "chat_completion",
    "parse_json_object",
    "ping",
]
