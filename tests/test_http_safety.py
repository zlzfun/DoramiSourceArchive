"""Unit tests for DNS-pinned, bounded public HTTP streaming."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import traceback

import httpx
import pytest

from services.http_safety import (
    PublicDownloadError,
    PublicDownloadResult,
    PublicDownloadTimeout,
    host_suffix_validator,
    stream_public_url_to_file,
)


def test_public_download_result_repr_hides_signed_final_url():
    result = PublicDownloadResult(
        final_url="https://audio.example/file.wav?Signature=secret&Expires=123",
        content_type="audio/wav",
        size=4,
        sha256="a" * 64,
    )

    assert "Signature=secret" not in repr(result)
    assert "Expires=123" not in repr(result)


def _resolver(mapping: dict[str, list[str]]):
    calls: list[str] = []

    async def resolve(host: str):
        calls.append(host)
        return mapping[host]

    return resolve, calls


def _client_factory(handler, creations: list[dict] | None = None):
    def create(**kwargs):
        if creations is not None:
            creations.append(kwargs.copy())
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    return create


def test_stream_download_pins_connection_and_returns_metadata():
    body = b"podcast-audio"
    seen: list[httpx.Request] = []
    resolver, calls = _resolver({"media.example": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Length": str(len(body)),
            },
            stream=httpx.ByteStream(body),
        )

    async def exercise():
        sink = io.BytesIO()
        result = await stream_public_url_to_file(
            "https://media.example:8443/audio.mp3?signature=secret#ignored",
            sink,
            max_bytes=100,
            resolver=resolver,
            client_factory=_client_factory(handler),
        )
        return sink.getvalue(), result

    saved, result = asyncio.run(exercise())
    assert saved == body
    assert (
        result.final_url
        == "https://media.example:8443/audio.mp3?signature=secret"
    )
    assert result.content_type == "audio/mpeg"
    assert result.size == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert calls == ["media.example"]
    assert str(seen[0].url) == "https://93.184.216.34:8443/audio.mp3?signature=secret"
    assert seen[0].headers["Host"] == "media.example:8443"
    assert seen[0].headers["Accept-Encoding"] == "identity"
    assert seen[0].extensions["sni_hostname"] == "media.example"


def test_redirect_uses_logical_base_and_revalidates_then_repins_each_host():
    resolver, calls = _resolver({
        "origin.example": ["93.184.216.34"],
        "cdn.example": ["1.1.1.1"],
    })
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.headers["Host"]))
        if request.url.path == "/start/feed.xml":
            return httpx.Response(302, headers={"Location": "/next/episode.mp3?x=1"})
        if request.url.path == "/next/episode.mp3":
            return httpx.Response(
                307,
                headers={"Location": "https://cdn.example/final.mp3"},
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    async def exercise():
        return await stream_public_url_to_file(
            "https://origin.example/start/feed.xml",
            io.BytesIO(),
            max_bytes=10,
            resolver=resolver,
            client_factory=_client_factory(handler),
        )

    asyncio.run(exercise())
    assert calls == ["origin.example", "origin.example", "cdn.example"]
    assert requests == [
        ("https://93.184.216.34/start/feed.xml", "origin.example"),
        ("https://93.184.216.34/next/episode.mp3?x=1", "origin.example"),
        ("https://1.1.1.1/final.mp3", "cdn.example"),
    ]


def test_redirect_revalidates_host_allowlist_before_dns_or_second_request():
    resolver, calls = _resolver({"audio.aliyuncs.com": ["93.184.216.34"]})
    requests = 0
    validated: list[str] = []
    allowed = host_suffix_validator(("aliyuncs.com",))

    def validate(hostname: str) -> None:
        validated.append(hostname)
        allowed(hostname)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            302,
            headers={"Location": "https://public.example/final.wav"},
        )

    with pytest.raises(PublicDownloadError, match="域名不在允许范围"):
        asyncio.run(
            stream_public_url_to_file(
                "https://audio.aliyuncs.com/start.wav?Signature=secret",
                io.BytesIO(),
                max_bytes=10,
                resolver=resolver,
                host_validator=validate,
                client_factory=_client_factory(handler),
            )
        )

    assert validated == ["audio.aliyuncs.com", "public.example"]
    assert calls == ["audio.aliyuncs.com"]
    assert requests == 1


def test_https_only_download_rejects_initial_http_before_dns_or_request():
    resolved = False
    requested = False

    async def resolver(_host: str):
        nonlocal resolved
        resolved = True
        return ["93.184.216.34"]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, stream=httpx.ByteStream(b"unexpected"))

    with pytest.raises(PublicDownloadError, match="HTTPS") as caught:
        asyncio.run(
            stream_public_url_to_file(
                "http://audio.aliyuncs.com/result.wav?Signature=secret",
                io.BytesIO(),
                max_bytes=10,
                resolver=resolver,
                client_factory=_client_factory(handler),
                require_https=True,
            )
        )

    assert resolved is False
    assert requested is False
    assert "Signature=secret" not in str(caught.value)


def test_generic_download_keeps_supporting_public_http_by_default():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b"legacy"))

    sink = io.BytesIO()
    result = asyncio.run(
        stream_public_url_to_file(
            "http://media.example/audio.mp3",
            sink,
            max_bytes=10,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )
    )

    assert sink.getvalue() == b"legacy"
    assert result.final_url == "http://media.example/audio.mp3"
    assert seen == ["http://93.184.216.34/audio.mp3"]


def test_https_only_download_rejects_redirect_downgrade_before_dns_or_request():
    secret = "redirect-secret"
    resolver, calls = _resolver({"audio.aliyuncs.com": ["93.184.216.34"]})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={
                "Location": (
                    "http://downgrade.aliyuncs.com/result.wav"
                    f"?Signature={secret}"
                )
            },
        )

    with pytest.raises(PublicDownloadError, match="HTTPS") as caught:
        asyncio.run(
            stream_public_url_to_file(
                "https://audio.aliyuncs.com/start.wav",
                io.BytesIO(),
                max_bytes=10,
                resolver=resolver,
                client_factory=_client_factory(handler),
                require_https=True,
            )
        )

    assert calls == ["audio.aliyuncs.com"]
    assert requests == ["https://93.184.216.34/start.wav"]
    assert secret not in str(caught.value)
    rendered = "".join(
        traceback.format_exception(
            type(caught.value), caught.value, caught.value.__traceback__
        )
    )
    assert secret not in rendered


def test_https_only_download_allows_safe_https_redirects():
    resolver, calls = _resolver({
        "audio.aliyuncs.com": ["93.184.216.34"],
        "result.aliyuncs.com": ["1.1.1.1"],
    })
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.headers["Host"]))
        if request.headers["Host"] == "audio.aliyuncs.com":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://result.aliyuncs.com/final.wav?token=signed"
                },
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"audio"))

    sink = io.BytesIO()
    result = asyncio.run(
        stream_public_url_to_file(
            "https://audio.aliyuncs.com/start.wav",
            sink,
            max_bytes=10,
            resolver=resolver,
            host_validator=host_suffix_validator(("aliyuncs.com",)),
            client_factory=_client_factory(handler),
            require_https=True,
        )
    )

    assert sink.getvalue() == b"audio"
    assert result.final_url == "https://result.aliyuncs.com/final.wav?token=signed"
    assert calls == ["audio.aliyuncs.com", "result.aliyuncs.com"]
    assert requests == [
        ("https://93.184.216.34/start.wav", "audio.aliyuncs.com"),
        ("https://1.1.1.1/final.wav?token=signed", "result.aliyuncs.com"),
    ]


def test_host_suffix_validator_accepts_exact_subdomain_and_trailing_dot_only():
    validate = host_suffix_validator(("aliyuncs.com",))
    validate("aliyuncs.com")
    validate("audio.oss.aliyuncs.com")
    validate("audio.oss.aliyuncs.com.")

    for hostname in ("evilaliyuncs.com", "aliyuncs.com.evil.example"):
        with pytest.raises(PublicDownloadError, match="域名不在允许范围"):
            validate(hostname)


def test_same_ip_cross_host_redirect_uses_isolated_clients_and_transports():
    resolver, _calls = _resolver({
        "first.example": ["93.184.216.34"],
        "second.example": ["93.184.216.34"],
    })
    client_kwargs: list[dict] = []
    transports: list[httpx.MockTransport] = []
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), request.headers["Host"]))
        if request.headers["Host"] == "first.example":
            return httpx.Response(
                302,
                headers={"Location": "https://second.example/final.mp3"},
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"safe"))

    def factory(**kwargs):
        client_kwargs.append(kwargs.copy())
        transport = httpx.MockTransport(handler)
        transports.append(transport)
        return httpx.AsyncClient(transport=transport, **kwargs)

    result = asyncio.run(
        stream_public_url_to_file(
            "https://first.example/start.mp3",
            io.BytesIO(),
            max_bytes=10,
            resolver=resolver,
            client_factory=factory,
        )
    )
    assert result.size == 4
    assert result.final_url == "https://second.example/final.mp3"
    assert requests == [
        ("https://93.184.216.34/start.mp3", "first.example"),
        ("https://93.184.216.34/final.mp3", "second.example"),
    ]
    assert len(client_kwargs) == len(transports) == 2
    assert transports[0] is not transports[1]
    for kwargs in client_kwargs:
        assert kwargs["http2"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["limits"].max_connections == 1
        assert kwargs["limits"].max_keepalive_connections == 0


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://media.example/audio.mp3",
        "https://user:password@media.example/audio.mp3",
        "https://user@media.example/audio.mp3",
    ],
)
def test_stream_download_rejects_non_http_and_userinfo_before_request(url: str):
    requested = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=b"unexpected")

    async def exercise():
        await stream_public_url_to_file(
            url,
            io.BytesIO(),
            max_bytes=10,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError):
        asyncio.run(exercise())
    assert requested is False


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],
        ["not-an-ip"],
        [],
    ],
)
def test_stream_download_requires_every_resolved_address_to_be_global(addresses):
    requested = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=b"unexpected")

    async def exercise():
        await stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=10,
            resolver=lambda _host: addresses,
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError):
        asyncio.run(exercise())
    assert requested is False


def test_redirect_to_private_address_is_rejected_without_second_request():
    resolver, _calls = _resolver({
        "origin.example": ["93.184.216.34"],
        "private.example": ["192.168.1.10"],
    })
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            302,
            headers={"Location": "http://private.example/internal"},
        )

    async def exercise():
        await stream_public_url_to_file(
            "https://origin.example/audio.mp3",
            io.BytesIO(),
            max_bytes=10,
            resolver=resolver,
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError, match="不是公网地址"):
        asyncio.run(exercise())
    assert requests == 1


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({"Content-Length": "11"}, b"", "超过大小上限"),
        ({"Content-Length": "invalid"}, b"", "Content-Length 无效"),
        ({}, b"123456", "超过大小上限"),
        ({"Content-Encoding": "gzip"}, b"123", "不允许的内容编码"),
    ],
)
def test_stream_download_enforces_declared_streamed_and_encoding_limits(
    headers,
    body,
    message,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))

    async def exercise():
        await stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=5,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
            chunk_size=2,
        )

    with pytest.raises(PublicDownloadError, match=message):
        asyncio.run(exercise())


def test_stream_download_enforces_redirect_limit():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    async def exercise():
        await stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=5,
            max_redirects=1,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError, match="重定向次数过多"):
        asyncio.run(exercise())


def test_stream_download_rejects_malformed_redirect_as_safe_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://[bad"})

    with pytest.raises(PublicDownloadError, match="目标地址无效"):
        asyncio.run(stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=5,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        ))


def test_http_timeout_is_distinct_from_other_download_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(PublicDownloadTimeout, match="超时"):
        asyncio.run(stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=5,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        ))


def test_stream_download_outer_timeout_covers_slow_body():
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"late"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowBody())

    async def exercise():
        await stream_public_url_to_file(
            "https://media.example/audio.mp3",
            io.BytesIO(),
            max_bytes=10,
            timeout_seconds=0.01,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError, match="总超时"):
        asyncio.run(exercise())


def test_download_errors_do_not_expose_signed_query():
    secret = "do-not-log-this-token"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not connect to {request.url}", request=request)

    async def exercise():
        await stream_public_url_to_file(
            f"https://media.example/audio.mp3?token={secret}",
            io.BytesIO(),
            max_bytes=10,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )

    with pytest.raises(PublicDownloadError) as caught:
        asyncio.run(exercise())
    assert secret not in str(caught.value)
    rendered = "".join(
        traceback.format_exception(
            type(caught.value), caught.value, caught.value.__traceback__
        )
    )
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_http_library_logs_redact_signed_query(caplog, monkeypatch):
    secret = "signed-query-must-not-appear"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    for logger_name in ("httpx", "httpcore.http11"):
        logger = logging.getLogger(logger_name)
        monkeypatch.setattr(logger, "disabled", False)
        monkeypatch.setattr(logger, "propagate", True)
        caplog.set_level(logging.DEBUG, logger=logger_name)
    asyncio.run(
        stream_public_url_to_file(
            f"https://media.example/audio.mp3?token={secret}&expires=99",
            io.BytesIO(),
            max_bytes=10,
            resolver=lambda _host: ["93.184.216.34"],
            client_factory=_client_factory(handler),
        )
    )
    # Exercise the httpcore filter directly because MockTransport does not emit
    # wire-level traces.
    logging.getLogger("httpcore.http11").debug(
        "send_request_headers.started url=%s",
        f"https://93.184.216.34/audio.mp3?token={secret}",
    )
    assert secret not in caplog.text
    assert "?[REDACTED]" in caplog.text
