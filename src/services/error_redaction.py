"""异常文本脱敏(单一实现,供 article_analysis / image_insights 等落库、落日志前复用)。

供应商 4xx 响应体会原样进入 ``LLMError`` 的消息(前 500 字),其中常见的
``Incorrect API key provided: sk-…`` 并不长成 ``api_key=…`` 的形状——两处各自维护正则
曾让这类形状漏网(issue #69 codex 检视 F9)。规则:
- 可识别的 HTTP 401/403 只保留状态与分类,**丢弃响应正文**(凭据回显的形状不可穷举);
- 其它文本走 :func:`redact_secrets`:URL、``key=value`` 形、Bearer、``provided: <token>`` 形、
  常见 key 前缀(``sk-`` 等)一律打码,再截断到有界长度。
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"(?i)\b(?:https?|feed)://[^\s<>\]\[)('\"]+")
_KV_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|authorization|password|secret)\s*[=:]\s*[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PROVIDED_RE = re.compile(r"(?i)\bprovided:?\s*['\"]?[A-Za-z0-9._~+/=-]{8,}")
# 常见 key 前缀:OpenAI/DeepSeek sk-、Anthropic sk-ant-、GitHub ghp_/gho_、Slack xox?-、Google AIza
_KEY_PREFIX_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9]{10,}|gho_[A-Za-z0-9]{10,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,})"
)
_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b")


def redact_secrets(text: str) -> str:
    """打码常见凭据形状与 URL;不截断。"""
    raw = str(text or "")
    raw = _URL_RE.sub("[redacted-url]", raw)
    raw = _KV_SECRET_RE.sub("[redacted-secret]", raw)
    raw = _BEARER_RE.sub("Bearer [redacted-secret]", raw)
    raw = _PROVIDED_RE.sub("provided: [redacted-secret]", raw)
    raw = _KEY_PREFIX_RE.sub("[redacted-secret]", raw)
    return raw


def sanitize_error(error: BaseException | str, *, max_chars: int = 500) -> str:
    """异常 → 可落库/落日志的有界摘要。

    401/403 只留「HTTP 4xx auth rejected (body withheld)」;其余先脱敏再压空白再截断。
    """
    if isinstance(error, BaseException):
        raw = f"{type(error).__name__}: {error}"
    else:
        raw = str(error)
    status = _HTTP_STATUS_RE.search(raw)
    if status and status.group(1) in ("401", "403"):
        prefix = raw.split(":", 1)[0] if isinstance(error, BaseException) else "LLMError"
        return f"{prefix}: HTTP {status.group(1)} auth rejected (body withheld)"[:max_chars]
    raw = redact_secrets(raw)
    raw = " ".join(raw.split())
    return raw[:max_chars]
