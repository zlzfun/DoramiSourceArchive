"""Bounded, single-attempt Feishu custom-bot delivery for review summaries.

This adapter does not schedule, persist, approve proposals, or retry a POST.
The caller owns the outbox and must reconcile an ``unknown`` result first.
"""

from __future__ import annotations

import base64
import configparser
import contextvars
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import urlsplit

import httpx


MAX_REQUEST_BYTES = 20_000  # Conservative interpretation of Feishu's 20 KB limit.
_SUMMARY_BYTES = 19_500  # Leave room for timestamp/signature and JSON envelope.
_sending = contextvars.ContextVar("feishu_notification_sending", default=False)


class _PrivateRequestFilter(logging.Filter):
    """Do not let HTTP library diagnostics print a credential-bearing hook URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _sending.get()


_private_request_filter = _PrivateRequestFilter()


def _guard_http_logs() -> None:
    # Logger filters are not inherited; attach to the actual HTTP library loggers.
    names = {
        "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
        "httpcore.proxy", "httpcore.socks",
    }
    names.update(
        name for name in logging.Logger.manager.loggerDict
        if name.startswith(("httpx.", "httpcore."))
    )
    for name in names:
        logger = logging.getLogger(name)
        if _private_request_filter not in logger.filters:
            logger.addFilter(_private_request_filter)


def _webhook_valid(value: str) -> bool:
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value) or "?" in value or "#" in value:
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == "open.feishu.cn"
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]{16,128}", parsed.path)
        )
    except ValueError:
        return False


def _public_link_valid(value: str) -> bool:
    """Only clean HTTPS links; the caller must additionally establish public ACL."""
    if (
        not isinstance(value, str) or len(value) > 1500
        or any(ord(c) < 33 for c in value) or "?" in value or "#" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or "\\" in value or "%" in parsed.netloc
            or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
        ):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", host))
    except ValueError:
        return False


def _review_base_valid(value: str, *, allow_loopback: bool = False) -> bool:
    if _public_link_valid(value):
        return True
    if not allow_loopback or not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and "?" not in value and "#" not in value and "\\" not in value
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class FeishuNotificationConfig:
    enabled: bool = False
    webhook_url: str = field(default="", repr=False)
    signing_secret: str = field(default="", repr=False)
    review_base_url: str = ""
    timezone: str = "Asia/Shanghai"
    digest_hour: int = 18
    timeout_seconds: float = 15.0
    # Isolated desktop validation only; not useful from a phone or another host.
    allow_loopback_review_url: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Invalid Feishu enabled setting")
        if type(self.allow_loopback_review_url) is not bool:
            raise ValueError("Invalid Feishu local review setting")
        if self.timezone != "Asia/Shanghai" or type(self.digest_hour) is not int or not 0 <= self.digest_hour <= 23:
            raise ValueError("Invalid Feishu digest schedule")
        if not isinstance(self.timeout_seconds, (int, float)) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("Invalid Feishu request timeout")
        if self.webhook_url and not _webhook_valid(self.webhook_url):
            raise ValueError("Invalid Feishu webhook endpoint")
        if self.review_base_url and not _review_base_valid(self.review_base_url, allow_loopback=self.allow_loopback_review_url):
            raise ValueError("Invalid Feishu review base URL")
        if self.enabled and not (
            self.webhook_url and self.signing_secret.strip() and self.review_base_url
        ):
            raise ValueError("Feishu delivery requires webhook, signing secret, and review base URL")


def load_feishu_config(path: str | Path) -> FeishuNotificationConfig:
    """Read an explicitly supplied private INI, with no global config side effect."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with Path(path).open(encoding="utf-8") as handle:
            parser.read_file(handle)
        section = parser["feishu_notifications"]
        return FeishuNotificationConfig(
            enabled=section.getboolean("enabled", False),
            webhook_url=section.get("webhook_url", "").strip(),
            signing_secret=section.get("signing_secret", "").strip(),
            review_base_url=section.get("review_base_url", "").strip().rstrip("/"),
            timezone=section.get("timezone", "Asia/Shanghai"),
            digest_hour=section.getint("digest_hour", 18),
            timeout_seconds=section.getfloat("timeout_seconds", 15),
            allow_loopback_review_url=section.getboolean("allow_loopback_review_url", False),
        )
    except (OSError, KeyError, ValueError, configparser.Error):
        # ConfigParser errors can contain the offending secret-bearing INI line.
        raise ValueError("Invalid or unreadable private Feishu configuration") from None


@dataclass(frozen=True)
class PublicEvidenceLink:
    title: str
    url: str
    is_public: bool = False  # Explicit opt-in after the caller's ACL filtering.


@dataclass(frozen=True)
class ReviewDigestItem:
    name: str
    kind: str
    recommendation: str
    reason: str
    article_count: int
    source_count: int
    review_path: str
    evidence: tuple[PublicEvidenceLink, ...] = ()


def _text(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    text = "".join(c for c in text if ord(c) >= 32 and not 0x7F <= ord(c) <= 0x9F)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _review_link(config: FeishuNotificationConfig, path: str) -> str:
    # The integrator supplies a real, read-only review route. No auth/action query.
    if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9/_-]*", path) or "//" in path:
        raise ValueError("Invalid review route")
    if not config.review_base_url or not _review_base_valid(config.review_base_url, allow_loopback=config.allow_loopback_review_url):
        raise ValueError("A valid review base URL is required")
    return config.review_base_url.rstrip("/") + path


def _wire_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_daily_digest(
    config: FeishuNotificationConfig,
    items: Sequence[ReviewDigestItem],
    *,
    digest_date: dt.date,
    inbox_path: str,
    total_count: int | None = None,
) -> dict | None:
    """Build one bounded rich-text summary. Empty changed-item lists send nothing.

    ``items`` must already exclude resolved, unchanged and unauthorized proposals;
    ``total_count`` optionally reports the full changed set before DB pagination.
    """
    if not items:
        return None
    total = len(items) if total_count is None else total_count
    if type(total) is not int or total < len(items):
        raise ValueError("Invalid review summary count")
    inbox_url = _review_link(config, inbox_path)
    rows: list[list[dict]] = [[{"tag": "text", "text": f"新增或重要更新 {total} 组"}]]
    footer = [{"tag": "a", "text": "打开全部待审核清单", "href": inbox_url}]
    payload = {"msg_type": "post", "content": {"post": {"zh_cn": {
        "title": f"Dorami 新概念审核 · {digest_date.isoformat()}", "content": rows,
    }}}}
    shown = 0
    for item in items[:20]:
        if (
            type(item.article_count) is not int or item.article_count < 0
            or type(item.source_count) is not int or item.source_count < 0
        ):
            raise ValueError("Invalid review evidence counts")
        kind = {"topic": "主题", "industry": "行业", "entity": "实体"}.get(item.kind, "待归类")
        candidate_rows = [
            [{"tag": "text", "text": f"{shown + 1}. {_text(item.name, 80)} · {kind}"}],
            [{"tag": "text", "text": f"建议：{_text(item.recommendation, 180)}"}],
            [{"tag": "text", "text": f"依据：{_text(item.reason, 240)}（{item.article_count} 篇 / {item.source_count} 个独立来源）"}],
        ]
        links = [
            {"tag": "a", "text": _text(e.title, 70) or "公开证据", "href": e.url}
            for e in item.evidence if e.is_public and _public_link_valid(e.url)
        ][:3]
        if links:
            candidate_rows.append(links)
        candidate_rows.append([{"tag": "a", "text": "查看与处理", "href": _review_link(config, item.review_path)}])
        rows.extend(candidate_rows)
        rows.append(footer)
        fits = len(_wire_bytes(payload)) <= _SUMMARY_BYTES - 150
        rows.pop()
        if not fits:
            del rows[-len(candidate_rows):]
            break
        shown += 1
    if shown < total:
        rows.append([{"tag": "text", "text": f"本条展示 {shown} 组；其余 {total - shown} 组请在审核清单查看。"}])
    rows.append(footer)
    return payload


def sign_request(timestamp: int, signing_secret: str) -> str:
    """Feishu custom-bot signature: HMAC(empty message, timestamp + LF + secret)."""
    key = f"{timestamp}\n{signing_secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")


@dataclass(frozen=True)
class FeishuDeliveryResult:
    status: Literal["accepted", "failed", "unknown", "skipped"]
    error_code: str | None = None
    http_status: int | None = None
    provider_code: int | None = None


async def send_digest(
    config: FeishuNotificationConfig,
    payload: dict | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timestamp: int | None = None,
) -> FeishuDeliveryResult:
    """Make at most one POST. ``accepted`` is not delivered/read/approved.

    A timeout, malformed response or 5xx is ambiguous: persist ``unknown`` and do
    not automatically resend. The custom webhook offers no idempotency receipt.
    """
    if not config.enabled or payload is None:
        return FeishuDeliveryResult("skipped")
    if payload.get("msg_type") != "post":
        return FeishuDeliveryResult("failed", "invalid_payload")
    stamp = int(time.time()) if timestamp is None else timestamp
    if type(stamp) is not int or stamp <= 0:
        return FeishuDeliveryResult("failed", "invalid_timestamp")
    try:
        body = _wire_bytes({**payload, "timestamp": str(stamp), "sign": sign_request(stamp, config.signing_secret)})
    except (TypeError, ValueError, OverflowError):
        return FeishuDeliveryResult("failed", "invalid_payload")
    if len(body) > MAX_REQUEST_BYTES:
        return FeishuDeliveryResult("failed", "payload_too_large")
    _guard_http_logs()
    token = _sending.set(True)
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=config.timeout_seconds,
            follow_redirects=False, trust_env=False,
        ) as client:
            response = await client.post(
                config.webhook_url, content=body, headers={"Content-Type": "application/json"},
            )
        status = response.status_code
        if 300 <= status < 500:
            return FeishuDeliveryResult("failed", "http_rejected", http_status=status)
        if status != 200:
            return FeishuDeliveryResult("unknown", "http_uncertain", http_status=status)
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            return FeishuDeliveryResult("unknown", "invalid_response", http_status=status)
        code = data.get("code") if isinstance(data, dict) else None
        if type(code) is not int:
            return FeishuDeliveryResult("unknown", "invalid_response", http_status=status)
        if code == 0:
            return FeishuDeliveryResult("accepted", http_status=status, provider_code=code)
        return FeishuDeliveryResult("failed", "provider_rejected", http_status=status, provider_code=code)
    except (httpx.HTTPError, OSError):
        # Exception strings may contain URLs, credentials, raw response or payload.
        return FeishuDeliveryResult("unknown", "transport_uncertain")
    finally:
        _sending.reset(token)
