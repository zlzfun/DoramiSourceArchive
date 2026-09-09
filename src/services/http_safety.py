"""Bounded HTTP download helpers for remote feeds and media."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_PUBLIC_REDIRECTS = 5

PublicHostValidator = Callable[[str], None]
AsyncClientFactory = Callable[..., httpx.AsyncClient]

_URL_QUERY_RE = re.compile(
    r"(?i)(https?://[^\s\"'<>?]+)\?[^\s\"'<>#]*"
)
_HTTP_LOGGER_NAMES = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


def _redact_url_queries(value: str) -> str:
    return _URL_QUERY_RE.sub(r"\1?[REDACTED]", value)


class _HTTPQueryRedactionFilter(logging.Filter):
    """Remove URL queries before third-party HTTP records reach any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive against custom records
            return True
        redacted = _redact_url_queries(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


_HTTP_QUERY_REDACTION_FILTER = _HTTPQueryRedactionFilter()


def install_http_query_redaction_filters() -> None:
    """Install the shared full-query redactor on HTTP client loggers once."""

    for logger_name in _HTTP_LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(item, _HTTPQueryRedactionFilter) for item in logger.filters
        ):
            logger.addFilter(_HTTP_QUERY_REDACTION_FILTER)


install_http_query_redaction_filters()


def _default_async_client_factory(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kwargs)


class PublicDownloadError(ValueError):
    """A safe-to-display failure from a public HTTP download.

    Messages deliberately never interpolate the source URL because its query
    string can contain signed credentials.
    """


class PublicDownloadTimeout(PublicDownloadError):
    """The connect/read/stream operation exceeded its configured deadline."""


@dataclass(frozen=True)
class PublicDownloadResult:
    """Metadata produced by :func:`stream_public_url_to_file`."""

    final_url: str = field(repr=False)
    content_type: str
    size: int
    sha256: str


def host_suffix_validator(
    allowed_host_suffixes: Sequence[str],
) -> PublicHostValidator:
    """Build an exact-or-subdomain allowlist check for redirect-safe downloads."""

    if isinstance(allowed_host_suffixes, (str, bytes)):
        raise ValueError("下载域名允许列表必须是字符串序列")
    normalized: list[str] = []
    for raw_suffix in allowed_host_suffixes:
        suffix = str(raw_suffix or "").strip().lower().lstrip(".").rstrip(".")
        try:
            suffix = suffix.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("下载域名允许列表无效") from None
        if (
            not suffix
            or "." not in suffix
            or len(suffix) > 253
            or any(
                not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    label,
                )
                for label in suffix.split(".")
            )
        ):
            raise ValueError("下载域名允许列表无效")
        normalized.append(suffix)
    suffixes = tuple(dict.fromkeys(normalized))
    if not suffixes:
        raise ValueError("下载域名允许列表不能为空")

    def validate(hostname: str) -> None:
        candidate = str(hostname or "").strip().lower().rstrip(".")
        try:
            candidate = candidate.encode("idna").decode("ascii")
        except UnicodeError:
            raise PublicDownloadError("下载目标域名不在允许范围") from None
        if not any(
            candidate == suffix or candidate.endswith(f".{suffix}")
            for suffix in suffixes
        ):
            raise PublicDownloadError("下载目标域名不在允许范围")

    return validate


def _logical_url_parts(url: str, *, require_https: bool = False):
    value = str(url or "").strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PublicDownloadError("下载地址格式无效")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise PublicDownloadError("下载地址格式无效") from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or (require_https and scheme != "https")
        or not parsed.hostname
    ):
        protocol = "HTTPS" if require_https else "HTTP(S)"
        raise PublicDownloadError(f"下载地址必须是公开 {protocol} 地址")
    if parsed.username is not None or parsed.password is not None:
        raise PublicDownloadError("下载地址不能包含用户名或密码")
    try:
        ascii_host = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PublicDownloadError("下载地址格式无效") from exc
    # Fragments are client-side only and must never influence redirect bases or
    # the URL returned to a caller as the fetched resource identity.
    logical_url = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, "")
    )
    return logical_url, ascii_host


def _validated_url(
    url: str,
    *,
    host_validator: PublicHostValidator | None = None,
    require_https: bool = False,
) -> str:
    normalized_url, ascii_host = _logical_url_parts(url, require_https=require_https)
    if host_validator is not None:
        try:
            host_validator(ascii_host)
        except PublicDownloadError:
            raise
        except (TypeError, ValueError):
            raise PublicDownloadError("下载目标域名不在允许范围") from None
    return normalized_url


def _declared_length(headers: httpx.Headers, max_bytes: int) -> None:
    raw = headers.get("Content-Length")
    if raw is None:
        return
    try:
        declared = int(raw, 10)
    except ValueError as exc:
        raise PublicDownloadError("下载响应的 Content-Length 无效") from exc
    if declared < 0:
        raise PublicDownloadError("下载响应的 Content-Length 无效")
    if declared > max_bytes:
        raise PublicDownloadError("下载响应超过大小上限")


def _write_all(destination: BinaryIO, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = destination.write(view)
        if written is None:
            # BufferedWriter-compatible objects may document ``None`` only for
            # non-blocking operation. Treat it as a failed sink, not success.
            raise PublicDownloadError("下载目标写入失败")
        if written <= 0:
            raise PublicDownloadError("下载目标写入失败")
        view = view[written:]


async def _stream_public_url_to_file(
    url: str,
    destination: BinaryIO,
    *,
    max_bytes: int,
    max_redirects: int,
    timeout_seconds: float,
    host_validator: PublicHostValidator | None,
    client_factory: AsyncClientFactory,
    chunk_size: int,
    require_https: bool,
) -> PublicDownloadResult:
    current = str(url or "").strip()
    digest = hashlib.sha256()
    received = 0

    request_failed = False
    request_timed_out = False
    try:
        async with client_factory(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            for hop in range(max_redirects + 1):
                current = _validated_url(
                    current,
                    host_validator=host_validator,
                    require_https=require_https,
                )
                async with client.stream(
                    "GET",
                    current,
                    headers={"Accept-Encoding": "identity"},
                    follow_redirects=False,
                    timeout=httpx.Timeout(timeout_seconds),
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("Location", "").strip()
                        if not location:
                            raise PublicDownloadError("下载重定向缺少目标地址")
                        if hop >= max_redirects:
                            raise PublicDownloadError("下载重定向次数过多")
                        try:
                            current = urljoin(current, location)
                        except ValueError:
                            raise PublicDownloadError(
                                "下载重定向目标地址无效"
                            ) from None
                        continue

                    if response.status_code < 200 or response.status_code >= 300:
                        raise PublicDownloadError(
                            f"下载服务器返回异常状态码 {response.status_code}"
                        )
                    content_encoding = response.headers.get(
                        "Content-Encoding", ""
                    ).strip()
                    if content_encoding and content_encoding.lower() != "identity":
                        raise PublicDownloadError("下载响应使用了不允许的内容编码")
                    _declared_length(response.headers, max_bytes)
                    async for chunk in response.aiter_raw(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > max_bytes:
                            raise PublicDownloadError("下载响应超过大小上限")
                        _write_all(destination, chunk)
                        digest.update(chunk)
                    return PublicDownloadResult(
                        final_url=current,
                        content_type=response.headers.get("Content-Type", "").strip(),
                        size=received,
                        sha256=digest.hexdigest(),
                    )
    except PublicDownloadError:
        raise
    except httpx.TimeoutException:
        request_timed_out = True
    except httpx.HTTPError:
        # Raise after leaving the except block so the original exception
        # (which may contain a signed query) is not retained as context.
        request_failed = True
    if request_timed_out:
        raise PublicDownloadTimeout("下载请求超时") from None
    if request_failed:
        raise PublicDownloadError("下载请求失败") from None

    raise PublicDownloadError("下载重定向次数过多")


async def stream_public_url_to_file(
    url: str,
    destination: BinaryIO,
    *,
    max_bytes: int,
    max_redirects: int = MAX_PUBLIC_REDIRECTS,
    timeout_seconds: float = 30.0,
    host_validator: PublicHostValidator | None = None,
    client_factory: AsyncClientFactory | None = None,
    chunk_size: int = 64 * 1024,
    require_https: bool = False,
) -> PublicDownloadResult:
    """Stream an HTTP(S) resource into a caller-owned sink with hard limits.

    URLs use the host and proxy behaviour supplied by httpx. When
    ``require_https`` is true, every redirect target must remain HTTPS. The
    caller owns cleanup of a partially-written destination when this raises.
    """

    if max_bytes <= 0:
        raise ValueError("下载响应大小上限必须为正数")
    if max_redirects < 0:
        raise ValueError("下载重定向次数上限不能为负数")
    if timeout_seconds <= 0:
        raise ValueError("下载请求超时必须为正数")
    if chunk_size <= 0:
        raise ValueError("下载分块大小必须为正数")
    selected_client_factory = client_factory or _default_async_client_factory
    try:
        # Bound DNS, connect, redirects, reads and body streaming as one operation.
        return await asyncio.wait_for(
            _stream_public_url_to_file(
                url,
                destination,
                max_bytes=max_bytes,
                max_redirects=max_redirects,
                timeout_seconds=timeout_seconds,
                host_validator=host_validator,
                client_factory=selected_client_factory,
                chunk_size=chunk_size,
                require_https=require_https,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise PublicDownloadTimeout("下载请求超过总超时上限") from None


async def ensure_public_http_url(url: str) -> None:
    """Validate feed URLs with the existing custom-source network policy."""

    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("目标地址必须是公开可访问的 HTTP(S) 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("目标地址不能包含用户名或密码")
    from services import media_store

    await media_store.ensure_public_host(parsed.hostname)


async def fetch_public_bytes_limited(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = MAX_PUBLIC_REDIRECTS,
    timeout_seconds: float | None = None,
) -> bytes:
    """GET an HTTP(S) URL with redirect, size, and wall-clock limits."""

    if max_bytes <= 0:
        raise ValueError("RSS 响应大小上限必须为正数")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("RSS 请求超时必须为正数")

    current = str(url or "").strip()
    deadline = (
        time.monotonic() + float(timeout_seconds)
        if timeout_seconds is not None
        else None
    )
    for hop in range(max_redirects + 1):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ValueError("RSS 请求超过超时上限")
        await ensure_public_http_url(current)
        request_kwargs = {"follow_redirects": False}
        if remaining is not None:
            request_kwargs["timeout"] = httpx.Timeout(remaining)
        async with client.stream("GET", current, **request_kwargs) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("Location", "").strip()
                if not location:
                    raise ValueError("RSS 重定向缺少目标地址")
                if hop >= max_redirects:
                    raise ValueError("RSS 重定向次数过多")
                current = urljoin(str(response.url), location)
                continue

            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError("RSS 响应超过大小上限")
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("RSS 请求超过超时上限")
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError("RSS 响应超过大小上限")
                chunks.append(chunk)
            return b"".join(chunks)

    raise ValueError("RSS 重定向次数过多")
