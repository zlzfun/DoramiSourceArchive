"""OpenAI image fallback safety and streaming limits; no real DNS/browser."""
import asyncio
import base64

import pytest

from services.openai_media import _read_stream, download_openai_image, is_openai_image


@pytest.mark.parametrize("url", [
    "http://openai.com/_next/static/media/a.webp",
    "https://openai.com.evil.test/_next/static/media/a.webp",
    "https://user@openai.com/_next/static/media/a.webp",
    "https://openai.com:444/_next/static/media/a.webp",
    "https://openai.com/_next/static/media/../secret.webp",
    "https://openai.com/_next/static/media/%2e%2e/secret.webp",
    "https://openai.com/_next/static/media/file.html",
    "https://openai.com/other/a.webp",
    "https://127.0.0.1/_next/static/media/a.webp",
    "https://[bad",
])
def test_browser_fallback_rejects_non_asset_urls_without_starting_browser(url):
    assert not is_openai_image(url)
    assert asyncio.run(download_openai_image(url, max_bytes=100, timeout_seconds=1)) is None


def test_browser_fallback_accepts_static_raster_images():
    assert is_openai_image("https://openai.com/_next/static/immutable/media/neutral.hash.webp")
    assert is_openai_image("https://openai.com/_next/static/media/figure.hash.png?w=96&q=90")


class Stream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False
        self.sizes = []

    async def send(self, method, params):
        if method == "Fetch.takeResponseBodyAsStream":
            return {"stream": "test-stream"}
        if method == "IO.close":
            self.closed = True
            return {}
        assert method == "IO.read"
        self.sizes.append(params["size"])
        data, eof = next(self.chunks)
        return {"data": base64.b64encode(data).decode(), "base64Encoded": True, "eof": eof}


def test_browser_stream_reads_exact_original_bytes_and_closes():
    stream = Stream([(b"RIFF", False), (b"\x00\xffWEBP", True)])
    assert asyncio.run(_read_stream(stream, "image", 10)) == b"RIFF\x00\xffWEBP"
    assert stream.closed and stream.sizes == [11, 7]


def test_browser_stream_aborts_at_actual_size_limit_even_without_content_length():
    stream = Stream([(b"1234", False), (b"56", False)])
    with pytest.raises(ValueError, match="size limit"):
        asyncio.run(_read_stream(stream, "image", 5))
    assert stream.closed and stream.sizes == [6, 2]


def test_browser_stream_is_closed_on_cancellation():
    class CancelledStream(Stream):
        async def send(self, method, params):
            if method == "IO.read":
                raise asyncio.CancelledError
            return await super().send(method, params)
    stream = CancelledStream([])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_read_stream(stream, "image", 5))
    assert stream.closed
