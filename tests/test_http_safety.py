"""Tests for bounded remote HTTP streaming without IP filtering."""

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
    resolve_public_url_redirects,
    stream_public_url_to_file,
)


def _client_factory(handler, creations: list[dict] | None = None):
    def create(**kwargs):
        if creations is not None:
            creations.append(kwargs.copy())
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return create


def _sync_client_factory(handler):
    def create(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return create


class _UnreadableBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("redirect resolution must not read response bodies")


def test_resolve_public_redirects_uses_head_without_body_or_url_logs(caplog):
    seen: list[tuple[str, str]] = []
    redirects = {
        "/start": "/one",
        "/one": "https://tracker.example/two?token=secret",
        "/two": "https://cdn.example/three",
        "/three": "https://audio.example/final.mp3?delivery=signed",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        location = redirects.get(request.url.path)
        if location is not None:
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg"},
            stream=_UnreadableBody(),
        )

    validated: list[str] = []

    def validate(value: str) -> str:
        validated.append(value)
        return value

    caplog.set_level(logging.INFO, logger="httpx")
    result = resolve_public_url_redirects(
        "https://podcast.example/start",
        url_validator=validate,
        max_redirects=4,
        timeout_seconds=5,
        client_factory=_sync_client_factory(handler),
    )

    assert result == "https://audio.example/final.mp3?delivery=signed"
    assert [method for method, _url in seen] == ["HEAD"] * 5
    assert validated == [url for _method, url in seen]
    assert "podcast.example" not in caplog.text
    assert "audio.example" not in caplog.text
    assert "delivery=signed" not in caplog.text
    # Another test or embedding process may intentionally disable the httpx
    # logger. Silence cannot leak a URL; when a record is emitted, it must use
    # the resolver's complete-URL redaction marker.
    if caplog.text:
        assert "[REDACTED_URL]" in caplog.text


def test_resolve_public_redirects_falls_back_to_unread_range_get():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(206, stream=_UnreadableBody())

    result = resolve_public_url_redirects(
        "https://audio.example/episode.mp3",
        url_validator=lambda value: value,
        client_factory=_sync_client_factory(handler),
    )

    assert result == "https://audio.example/episode.mp3"
    assert [request.method for request in seen] == ["HEAD", "GET"]
    assert seen[1].headers["Range"] == "bytes=0-0"
    assert seen[1].headers["Accept-Encoding"] == "identity"


def test_resolve_public_redirects_range_probes_ambiguous_head_response():
    seen: list[tuple[str, str]] = []
    final = "https://cdn.example/final.mp3"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.url.host == "tracker.example" and request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Type": "text/html"})
        if request.url.host == "tracker.example":
            return httpx.Response(302, headers={"Location": final})
        return httpx.Response(200, headers={"Content-Type": "audio/mpeg"})

    result = resolve_public_url_redirects(
        "https://tracker.example/episode",
        url_validator=lambda value: value,
        client_factory=_sync_client_factory(handler),
    )

    assert result == final
    assert seen == [
        ("HEAD", "https://tracker.example/episode"),
        ("GET", "https://tracker.example/episode"),
        ("HEAD", final),
    ]


def test_resolve_public_redirects_enforces_hop_limit():
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"Location": "/again"})

    with pytest.raises(PublicDownloadError, match="重定向次数过多"):
        resolve_public_url_redirects(
            "https://podcast.example/start",
            url_validator=lambda value: value,
            max_redirects=2,
            client_factory=_sync_client_factory(handler),
        )

    assert requests == 3


def test_public_download_result_repr_hides_signed_final_url():
    result = PublicDownloadResult(
        final_url="https://audio.example/file.wav?Signature=secret&Expires=123",
        content_type="audio/wav",
        size=4,
        sha256="a" * 64,
    )

    assert "Signature=secret" not in repr(result)
    assert "Expires=123" not in repr(result)


def test_stream_download_uses_logical_host_and_returns_metadata():
    body = b"podcast-audio"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(body))},
            stream=httpx.ByteStream(body),
        )

    sink = io.BytesIO()
    result = asyncio.run(
        stream_public_url_to_file(
            "https://media.example:8443/audio.mp3?signature=secret#ignored",
            sink,
            max_bytes=100,
            client_factory=_client_factory(handler),
        )
    )

    assert sink.getvalue() == body
    assert result.final_url == "https://media.example:8443/audio.mp3?signature=secret"
    assert result.content_type == "audio/mpeg"
    assert result.size == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert str(seen[0].url) == "https://media.example:8443/audio.mp3?signature=secret"
    assert seen[0].headers["Accept-Encoding"] == "identity"


def test_redirect_uses_logical_base_without_ip_pinning():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/start/feed.xml":
            return httpx.Response(302, headers={"Location": "/next/episode.mp3?x=1"})
        if request.url.path == "/next/episode.mp3":
            return httpx.Response(307, headers={"Location": "https://cdn.example/final.mp3"})
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    result = asyncio.run(
        stream_public_url_to_file(
            "https://origin.example/start/feed.xml",
            io.BytesIO(),
            max_bytes=10,
            client_factory=_client_factory(handler),
        )
    )

    assert result.final_url == "https://cdn.example/final.mp3"
    assert requests == [
        "https://origin.example/start/feed.xml",
        "https://origin.example/next/episode.mp3?x=1",
        "https://cdn.example/final.mp3",
    ]


def test_private_and_fake_ip_literals_are_not_filtered():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    for url in (
        "http://127.0.0.1/audio.mp3",
        "http://192.168.1.10/audio.mp3",
        "http://198.18.0.8/audio.mp3",
    ):
        sink = io.BytesIO()
        result = asyncio.run(
            stream_public_url_to_file(
                url,
                sink,
                max_bytes=10,
                client_factory=_client_factory(handler),
            )
        )
        assert sink.getvalue() == b"ok"
        assert result.final_url == url

    assert seen == [
        "http://127.0.0.1/audio.mp3",
        "http://192.168.1.10/audio.mp3",
        "http://198.18.0.8/audio.mp3",
    ]


def test_redirect_revalidates_host_allowlist_before_second_request():
    requests = 0
    validated: list[str] = []
    allowed = host_suffix_validator(("aliyuncs.com",))

    def validate(hostname: str) -> None:
        validated.append(hostname)
        allowed(hostname)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"Location": "https://public.example/final.wav"})

    with pytest.raises(PublicDownloadError, match="域名不在允许范围"):
        asyncio.run(
            stream_public_url_to_file(
                "https://audio.aliyuncs.com/start.wav?Signature=secret",
                io.BytesIO(),
                max_bytes=10,
                host_validator=validate,
                client_factory=_client_factory(handler),
            )
        )

    assert validated == ["audio.aliyuncs.com", "public.example"]
    assert requests == 1


def test_https_only_download_rejects_initial_http_before_request():
    requested = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, stream=httpx.ByteStream(b"unexpected"))

    with pytest.raises(PublicDownloadError, match="HTTPS"):
        asyncio.run(
            stream_public_url_to_file(
                "http://audio.aliyuncs.com/result.wav",
                io.BytesIO(),
                max_bytes=10,
                client_factory=_client_factory(handler),
                require_https=True,
            )
        )

    assert requested is False


def test_https_only_download_rejects_redirect_downgrade():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://audio.aliyuncs.com/result.wav"})

    with pytest.raises(PublicDownloadError, match="HTTPS"):
        asyncio.run(
            stream_public_url_to_file(
                "https://audio.aliyuncs.com/start.wav",
                io.BytesIO(),
                max_bytes=10,
                client_factory=_client_factory(handler),
                require_https=True,
            )
        )

    assert requests == ["https://audio.aliyuncs.com/start.wav"]


def test_https_only_download_allows_https_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "audio.aliyuncs.com":
            return httpx.Response(
                302,
                headers={"Location": "https://result.aliyuncs.com/final.wav?token=signed"},
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"audio"))

    sink = io.BytesIO()
    result = asyncio.run(
        stream_public_url_to_file(
            "https://audio.aliyuncs.com/start.wav",
            sink,
            max_bytes=10,
            host_validator=host_suffix_validator(("aliyuncs.com",)),
            client_factory=_client_factory(handler),
            require_https=True,
        )
    )

    assert sink.getvalue() == b"audio"
    assert result.final_url == "https://result.aliyuncs.com/final.wav?token=signed"


def test_host_suffix_validator_accepts_exact_subdomain_and_trailing_dot_only():
    validate = host_suffix_validator(("aliyuncs.com",))
    validate("aliyuncs.com")
    validate("audio.oss.aliyuncs.com")
    validate("audio.oss.aliyuncs.com.")

    for hostname in ("evilaliyuncs.com", "aliyuncs.com.evil.example"):
        with pytest.raises(PublicDownloadError, match="域名不在允许范围"):
            validate(hostname)


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

    with pytest.raises(PublicDownloadError):
        asyncio.run(
            stream_public_url_to_file(
                url,
                io.BytesIO(),
                max_bytes=10,
                client_factory=_client_factory(handler),
            )
        )
    assert requested is False


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({"Content-Length": "11"}, b"", "超过大小上限"),
        ({"Content-Length": "invalid"}, b"", "Content-Length 无效"),
        ({}, b"123456", "超过大小上限"),
        ({"Content-Encoding": "gzip"}, b"123", "不允许的内容编码"),
    ],
)
def test_stream_download_enforces_declared_streamed_and_encoding_limits(headers, body, message):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))

    with pytest.raises(PublicDownloadError, match=message):
        asyncio.run(
            stream_public_url_to_file(
                "https://media.example/audio.mp3",
                io.BytesIO(),
                max_bytes=5,
                client_factory=_client_factory(handler),
                chunk_size=2,
            )
        )


def test_stream_download_enforces_redirect_limit():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    with pytest.raises(PublicDownloadError, match="重定向次数过多"):
        asyncio.run(
            stream_public_url_to_file(
                "https://media.example/audio.mp3",
                io.BytesIO(),
                max_bytes=5,
                max_redirects=1,
                client_factory=_client_factory(handler),
            )
        )


def test_stream_download_rejects_malformed_redirect_as_safe_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://[bad"})

    with pytest.raises(PublicDownloadError, match="目标地址无效"):
        asyncio.run(
            stream_public_url_to_file(
                "https://media.example/audio.mp3",
                io.BytesIO(),
                max_bytes=5,
                client_factory=_client_factory(handler),
            )
        )


def test_http_timeout_is_distinct_from_other_download_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(PublicDownloadTimeout, match="超时"):
        asyncio.run(
            stream_public_url_to_file(
                "https://media.example/audio.mp3",
                io.BytesIO(),
                max_bytes=5,
                client_factory=_client_factory(handler),
            )
        )


def test_stream_download_outer_timeout_covers_slow_body():
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"late"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowBody())

    with pytest.raises(PublicDownloadError, match="总超时"):
        asyncio.run(
            stream_public_url_to_file(
                "https://media.example/audio.mp3",
                io.BytesIO(),
                max_bytes=10,
                timeout_seconds=0.01,
                client_factory=_client_factory(handler),
            )
        )


def test_download_errors_do_not_expose_signed_query():
    secret = "do-not-log-this-token"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not connect to {request.url}", request=request)

    with pytest.raises(PublicDownloadError) as caught:
        asyncio.run(
            stream_public_url_to_file(
                f"https://media.example/audio.mp3?token={secret}",
                io.BytesIO(),
                max_bytes=10,
                client_factory=_client_factory(handler),
            )
        )

    assert secret not in str(caught.value)
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
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
            client_factory=_client_factory(handler),
        )
    )
    logging.getLogger("httpcore.http11").debug(
        "send_request_headers.started url=%s",
        f"https://198.18.0.8/audio.mp3?token={secret}",
    )
    assert secret not in caplog.text
    assert "?[REDACTED]" in caplog.text
