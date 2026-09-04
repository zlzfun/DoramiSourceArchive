"""Small HTTP safety helpers for user-controlled URLs.

The caller-owned ``httpx.AsyncClient`` may be configured to follow redirects for
trusted platform sources.  User-controlled RSS URLs must not inherit that
behaviour: every redirect target is resolved and checked before the next request.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

import httpx

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_PUBLIC_REDIRECTS = 5


async def ensure_public_http_url(url: str) -> None:
    """Reject non-HTTP, credential-bearing, local, and private-network URLs."""

    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("目标地址必须是公开可访问的 HTTP(S) 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("目标地址不能包含用户名或密码")
    # Resolve through the module on every call so tests and deployments can
    # replace the resolver without leaving a stale imported function behind.
    from services import media_store

    await media_store.ensure_public_host(parsed.hostname)


async def fetch_public_bytes_limited(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = MAX_PUBLIC_REDIRECTS,
) -> bytes:
    """GET a public URL with per-hop SSRF checks and a streaming size limit."""

    current = str(url or "").strip()
    for hop in range(max_redirects + 1):
        await ensure_public_http_url(current)
        async with client.stream("GET", current, follow_redirects=False) as response:
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
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError("RSS 响应超过大小上限")
                chunks.append(chunk)
            return b"".join(chunks)

    raise ValueError("RSS 重定向次数过多")
