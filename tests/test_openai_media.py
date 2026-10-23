"""OpenAI image fallback safety and streaming limits; no real DNS/browser."""
import asyncio
import base64
from types import SimpleNamespace

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


URL = "https://openai.com/_next/static/immutable/media/face.hash.webp"
BODY = b"RIFF1234WEBPpixels"


def _response(status=200, content_type="image/webp", *, url=URL, size=None):
    headers = [{"name": "Content-Type", "value": content_type}]
    if size is not None:
        headers.append({"name": "Content-Length", "value": str(size)})
    return {"requestId": str(status) + url, "request": {"url": url},
            "responseStatusCode": status, "responseHeaders": headers}


class BrowserHarness(Stream):
    """Runs actual orchestration callbacks with a deterministic in-memory CDP."""
    def __init__(self, events=(), requests=()):
        super().__init__([(BODY, True)])
        self.chromium = self
        self.events = events
        self.requests = requests
        self.commands = []
        self.routes = []
        self.browser_closed = self.driver_closed = self.navigation_cancelled = False
        self.started = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.driver_closed = True

    async def launch(self, **kwargs):
        return self

    async def new_context(self, **kwargs):
        assert kwargs["service_workers"] == "block"
        return self

    async def new_page(self):
        return self

    async def route(self, pattern, guard):
        self.guard = guard

    async def new_cdp_session(self, page):
        return self

    def on(self, event, callback):
        self.callback = callback

    async def send(self, method, params):
        self.commands.append((method, params))
        if method.startswith("Fetch.") and method != "Fetch.takeResponseBodyAsStream":
            return {}
        return await super().send(method, params)

    async def goto(self, *args, **kwargs):
        self.started.set()
        for url in self.requests:
            async def abort():
                self.routes.append((url, "aborted"))
            async def proceed():
                self.routes.append((url, "allowed"))
            await self.guard(SimpleNamespace(request=SimpleNamespace(url=url), abort=abort, continue_=proceed))
        for event in self.events:
            self.callback(event)
            await asyncio.sleep(0)
        try:
            await asyncio.Future()
        finally:
            self.navigation_cancelled = True

    async def close(self):
        self.browser_closed = True


def _install_browser(monkeypatch, harness, *, blocked_host=None):
    import playwright.async_api
    from services import media_store
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: harness)
    async def public(host):
        if host == blocked_host:
            raise media_store.SSRFError("blocked")
    monkeypatch.setattr(media_store, "ensure_public_host", public)


def test_orchestration_guards_requests_and_streams_original_image(monkeypatch):
    allowed = [URL, "https://openai.com/cdn-cgi/challenge-platform/script.js"]
    denied = ["https://evil.test/a", "https://openai.com/other", "https://openai.com:444/cdn-cgi/x",
              "https://challenges.cloudflare.com/script"]
    browser = BrowserHarness([_response(403, "text/html"), _response()], allowed + denied)
    _install_browser(monkeypatch, browser, blocked_host="challenges.cloudflare.com")
    assert asyncio.run(download_openai_image(URL, max_bytes=100, timeout_seconds=1)) == BODY
    assert browser.routes == [(u, "allowed") for u in allowed] + [(u, "aborted") for u in denied]
    assert browser.browser_closed and browser.driver_closed and browser.navigation_cancelled and browser.closed
    assert any(method == "Fetch.continueRequest" for method, _ in browser.commands)


@pytest.mark.parametrize("event", [_response(200, size=101), _response(302),
                                   _response(200, "text/html"), _response(404, "text/html")])
def test_orchestration_rejects_oversize_redirect_and_non_images(monkeypatch, event):
    browser = BrowserHarness([event])
    _install_browser(monkeypatch, browser)
    assert asyncio.run(download_openai_image(URL, max_bytes=100, timeout_seconds=0.03)) is None
    assert browser.browser_closed and browser.driver_closed and browser.navigation_cancelled
    assert not any(method == "Fetch.takeResponseBodyAsStream" for method, _ in browser.commands)
    if event["responseStatusCode"] == 302:
        assert any(method == "Fetch.failRequest" for method, _ in browser.commands)


def test_orchestration_blocks_redirects_on_challenge_resources_too(monkeypatch):
    redirected = "https://challenges.cloudflare.com/script"
    browser = BrowserHarness([_response(302, "text/html", url=redirected)])
    _install_browser(monkeypatch, browser)
    assert asyncio.run(download_openai_image(URL, max_bytes=100, timeout_seconds=0.03)) is None
    assert any(method == "Fetch.failRequest" and params["requestId"].endswith(redirected)
               for method, params in browser.commands)
    assert ("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]}) in browser.commands


@pytest.mark.parametrize("cancel", [False, True])
def test_orchestration_reclaims_browser_and_tasks_on_timeout_or_cancellation(monkeypatch, cancel):
    browser = BrowserHarness()
    _install_browser(monkeypatch, browser)
    async def run():
        task = asyncio.create_task(download_openai_image(URL, max_bytes=100, timeout_seconds=0.03))
        await browser.started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task is None
    asyncio.run(run())
    assert browser.browser_closed and browser.driver_closed and browser.navigation_cancelled
