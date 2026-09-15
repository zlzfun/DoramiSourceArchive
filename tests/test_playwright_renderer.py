import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl import playwright_renderer as pr


class FakePage:
    def __init__(self, context, behaviour):
        self.context = context
        self.behaviour = behaviour  # "challenge" | "pass"
        self.closed = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    async def title(self):
        return "Just a moment..." if self.behaviour == "challenge" else "Real article"

    async def evaluate(self, script):
        return "" if self.behaviour == "challenge" else "x" * 5000

    async def wait_for_timeout(self, ms):
        return None

    async def content(self):
        return "<html>challenge</html>" if self.behaviour == "challenge" else "<html><article>body</article></html>"

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, browser, behaviour, user_agent):
        self.browser = browser
        self.behaviour = behaviour
        self.user_agent = user_agent
        self.closed = False
        self.pages = []

    async def new_page(self):
        page = FakePage(self, self.behaviour)
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.contexts = []
        self.closed = False

    async def new_context(self, user_agent=None):
        behaviour = self.behaviours.pop(0) if self.behaviours else "pass"
        context = FakeContext(self, behaviour, user_agent)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = None

    async def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _install_fake_playwright(monkeypatch, browser):
    playwright_obj = FakePlaywright(browser)

    class _Starter:
        async def start(self):
            return playwright_obj

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda: _Starter()
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    return playwright_obj


def test_render_opens_fresh_context_per_attempt_and_recovers_from_stuck_challenge(monkeypatch):
    """2026-09-09 起 openai.com 的 Cloudflare 对共用上下文的二次导航恒回 403 挑战且永不放行,
    空上下文首访直接 200(issue #79 生产实测);渲染器必须每次尝试换新上下文,而不是在同一
    上下文里重开页面重试。"""
    browser = FakeBrowser(["challenge", "pass"])
    _install_fake_playwright(monkeypatch, browser)

    async def run():
        async with pr.PlaywrightRenderer(
            throttle_seconds=0, max_wait_ms=800, poll_interval_ms=400, retry_backoff_seconds=0, attempts=3
        ) as renderer:
            assert renderer.available is True
            html = await renderer.render("https://openai.com/index/example")
            return html

    html = asyncio.run(run())

    assert html == "<html><article>body</article></html>"
    # 两次尝试 = 两个全新的上下文,而不是同一上下文里的两个页面
    assert len(browser.contexts) == 2
    assert [ctx.behaviour for ctx in browser.contexts] == ["challenge", "pass"]
    assert all(len(ctx.pages) == 1 for ctx in browser.contexts)
    # 每个上下文用完即关(cookie 不跨篇),页面也关;浏览器进程直到退出上下文管理器才关
    assert all(ctx.closed and ctx.pages[0].closed for ctx in browser.contexts)
    assert browser.closed is True
    assert browser.contexts[0].user_agent == pr._DEFAULT_UA


def test_render_returns_empty_when_every_attempt_stays_on_challenge(monkeypatch):
    browser = FakeBrowser(["challenge", "challenge"])
    _install_fake_playwright(monkeypatch, browser)

    async def run():
        async with pr.PlaywrightRenderer(
            throttle_seconds=0, max_wait_ms=400, poll_interval_ms=400, retry_backoff_seconds=0, attempts=2
        ) as renderer:
            return await renderer.render("https://openai.com/index/example")

    assert asyncio.run(run()) == ""
    assert len(browser.contexts) == 2 and all(ctx.closed for ctx in browser.contexts)


def test_renderer_degrades_when_playwright_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    async def run():
        async with pr.PlaywrightRenderer() as renderer:
            assert renderer.available is False
            return await renderer.render("https://openai.com/index/example")

    assert asyncio.run(run()) == ""
