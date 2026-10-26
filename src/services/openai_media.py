"""Bounded Chromium fallback for OpenAI's challenged first-party image assets.

Only used after an HTTP 403 challenge. A fresh browser may complete the site's
automatic check on an image navigation; no user profile or CAPTCHA interaction.
The response is streamed through CDP instead of buffering an unbounded body.
"""

import asyncio
import base64
import logging
import os
import re
from contextlib import suppress
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_ASSET_PATH = re.compile(r"/_next/static/(?:immutable/)?media/[\w.-]+\.(?:webp|png|jpe?g|gif|avif)", re.ASCII)


def is_openai_image(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "openai.com"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.fragment and bool(_ASSET_PATH.fullmatch(parsed.path)))
    except ValueError:
        return False


async def _read_stream(cdp, request_id: str, max_bytes: int) -> bytes:
    handle = (await cdp.send("Fetch.takeResponseBodyAsStream", {"requestId": request_id}))["stream"]
    chunks = []
    size = 0
    try:
        while True:
            part = await cdp.send("IO.read", {"handle": handle, "size": min(65536, max_bytes - size + 1)})
            chunk = base64.b64decode(part["data"]) if part.get("base64Encoded") else part["data"].encode()
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("browser image exceeds size limit")
            chunks.append(chunk)
            if part.get("eof"):
                return b"".join(chunks)
    finally:
        with suppress(Exception):
            await cdp.send("IO.close", {"handle": handle})


async def download_openai_image(url: str, *, max_bytes: int, timeout_seconds: float) -> bytes | None:
    if not is_openai_image(url):
        return None
    # Import lazily: a missing browser must not break ordinary HTTP media.
    from services.media_store import ensure_public_host

    browser = None
    tasks = set()
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await ensure_public_host("openai.com")
                    launch = {"headless": True}
                    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "").strip()
                    if executable:
                        launch["executable_path"] = executable
                    browser = await playwright.chromium.launch(**launch)
                    from fetchers.impl.playwright_renderer import _DEFAULT_UA
                    context = await browser.new_context(service_workers="block", user_agent=_DEFAULT_UA)
                    page = await context.new_page()
                    checked_hosts = {"openai.com"}

                    async def guard(route):
                        target = route.request.url
                        try:
                            parsed = urlsplit(target)
                            allowed = (parsed.scheme == "https" and parsed.port in (None, 443)
                                       and not parsed.username and not parsed.password
                                       and ((parsed.hostname == "openai.com" and
                                             (target == url or parsed.path.startswith("/cdn-cgi/")))
                                            or parsed.hostname == "challenges.cloudflare.com"))
                        except ValueError:
                            return await route.abort()
                        if not allowed:
                            logger.debug("Blocked browser media request: %s", parsed.path)
                            return await route.abort()
                        try:
                            if parsed.hostname not in checked_hosts:
                                await ensure_public_host(parsed.hostname)
                                checked_hosts.add(parsed.hostname)
                        except Exception:
                            return await route.abort()
                        await route.continue_()

                    await context.route("**/*", guard)
                    cdp = await context.new_cdp_session(page)
                    result = asyncio.get_running_loop().create_future()

                    async def response(event):
                        request_id = event["requestId"]
                        headers = {h["name"].lower(): h["value"] for h in event.get("responseHeaders", [])}
                        logger.debug("Browser image response: %s %s", event.get("responseStatusCode"), headers.get("content-type"))
                        # Playwright's route callback does not guard every redirect
                        # hop. Reject redirects for the image AND challenge resources
                        # at response stage before Chromium follows them.
                        if 300 <= event.get("responseStatusCode", 0) < 400:
                            await cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "Aborted"})
                            if event["request"]["url"] == url and not result.done():
                                result.set_result(None)
                            return
                        if (event["request"]["url"] != url or event.get("responseStatusCode") != 200
                                or not headers.get("content-type", "").lower().startswith("image/")):
                            await cdp.send("Fetch.continueRequest", {"requestId": request_id})
                            return
                        try:
                            if int(headers.get("content-length") or 0) > max_bytes:
                                raise ValueError("browser image exceeds declared size limit")
                            body = await _read_stream(cdp, request_id, max_bytes)
                            if not result.done():
                                result.set_result(body)
                        except Exception as exc:
                            if not result.done():
                                result.set_exception(exc)
                        finally:
                            with suppress(Exception):
                                await cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "Aborted"})

                    def completed(task):
                        tasks.discard(task)
                        if not task.cancelled() and (error := task.exception()) and not result.done():
                            result.set_exception(error)

                    def schedule(event):
                        task = asyncio.create_task(response(event))
                        tasks.add(task)
                        task.add_done_callback(completed)

                    cdp.on("Fetch.requestPaused", schedule)
                    # Check all responses for redirects; only the target image body is read.
                    await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})
                    async def navigate():
                        # Taking the image stream intentionally aborts navigation.
                        with suppress(Exception):
                            await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                    navigation = asyncio.create_task(navigate())
                    tasks.add(navigation)
                    navigation.add_done_callback(completed)
                    return await result
            finally:
                pending = list(tasks)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if browser is not None:
                    await browser.close()
    except Exception as exc:
        logger.info("OpenAI image browser fallback unavailable: %s", type(exc).__name__)
        return None
