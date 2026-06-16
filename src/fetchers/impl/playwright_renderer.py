"""Playwright 渲染助手 (src/fetchers/impl/playwright_renderer.py)

部分站点（当前为 OpenAI News）对文章正文页启用了 Cloudflare Managed Challenge，
纯 httpx 请求只能拿到 403 挑战壳页，正文需由浏览器执行 JS 通过挑战后才渲染出来。

本模块提供一个轻量的浏览器渲染器：在一次抓取任务内复用单个 headless Chromium，
逐篇文章新建干净页面、按节流间隔访问、轮询等待挑战通过与正文出现，返回渲染后的
完整 HTML（交由调用方用既有的 article_extractor 提取正文）。任何失败都返回空串，
让调用方优雅降级（例如回退到 RSS summary），绝不抛断整个抓取流程。

设计要点（均由实测得出）：
- 每篇新建 page（干净上下文）+ 请求间隔节流，否则连续快抓会触发 CF 频率拦截；
- 不能用 networkidle（页面有持续后台请求，永不达成），改用 domcontentloaded + 轮询；
- 轮询条件：标题不再是 "Just a moment" 且 body 文本超过阈值，视为挑战已过、正文已现。
"""

import asyncio
import logging
import os
from typing import Optional


logger = logging.getLogger("PlaywrightRenderer")


_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 挑战过渡页的标志：标题含此串即说明仍停在 Cloudflare 挑战页。
_CHALLENGE_TITLE_MARKERS = ("just a moment", "verifying", "attention required")


class PlaywrightRenderer:
    """一次抓取任务内复用的浏览器渲染器。

    用作异步上下文管理器：进入时懒启动 Chromium，退出时关闭，确保浏览器进程
    随任务生命周期回收。`playwright` 未安装或启动失败时进入降级状态
    （`available` 为 False），`render` 恒返回空串。
    """

    def __init__(
        self,
        *,
        user_agent: str = _DEFAULT_UA,
        throttle_seconds: float = 1.5,
        max_wait_ms: int = 15000,
        poll_interval_ms: int = 400,
        min_body_chars: int = 1200,
        attempts: int = 3,
        retry_backoff_seconds: float = 2.0,
        nav_timeout_ms: int = 30000,
    ):
        self.user_agent = user_agent
        self.throttle_seconds = throttle_seconds
        self.max_wait_ms = max_wait_ms
        self.poll_interval_ms = poll_interval_ms
        self.min_body_chars = min_body_chars
        self.attempts = attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.nav_timeout_ms = nav_timeout_ms

        self._playwright = None
        self._browser = None
        self._context = None
        self._last_request_at: Optional[float] = None
        self.available = False

    async def __aenter__(self) -> "PlaywrightRenderer":
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("⚠️ 未安装 playwright，浏览器渲染不可用，将降级。")
            return self
        try:
            self._playwright = await async_playwright().start()
            launch_kwargs = {"headless": True}
            # 逃生口：Playwright 不支持当前 OS（如过新的 Ubuntu 拒绝下载/校验浏览器）时，
            # 通过 PLAYWRIGHT_CHROMIUM_EXECUTABLE 指向一个系统自带的 chromium/chrome 二进制，
            # 绕开 Playwright 自带浏览器的 OS 适配。未设置则沿用 Playwright 下载的浏览器。
            executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "").strip()
            if executable:
                launch_kwargs["executable_path"] = executable
                logger.info(f"ℹ️ 使用系统 Chromium: {executable}")
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(user_agent=self.user_agent)
            self.available = True
        except Exception as e:
            logger.warning(f"⚠️ 启动 Chromium 失败，浏览器渲染降级: {e}")
            await self._safe_close()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._safe_close()

    async def _safe_close(self) -> None:
        self.available = False
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                pass
        self._context = self._browser = self._playwright = None

    async def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = asyncio.get_event_loop().time() - self._last_request_at
        wait = self.throttle_seconds - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    def _looks_like_challenge(self, title: str) -> bool:
        lowered = (title or "").lower()
        return any(marker in lowered for marker in _CHALLENGE_TITLE_MARKERS)

    async def _render_once(self, url: str):
        """渲染一次，返回 ``(html, passed)``。

        ``passed`` 表示是否真正通过挑战并出现正文（标题不再是挑战页且 body 文本达阈值）。
        超时仍未过挑战时返回 ``(当前HTML, False)``——HTML 多半是过渡页/壳页，交给上层
        决定是否重试。区分这一点是关键：旧逻辑把超时返回的过渡页当成功，白白浪费了重试。
        """
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            waited = 0
            while waited < self.max_wait_ms:
                title = await page.title()
                if not self._looks_like_challenge(title):
                    body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    if len(body_text) >= self.min_body_chars:
                        return await page.content(), True
                await page.wait_for_timeout(self.poll_interval_ms)
                waited += self.poll_interval_ms
            return await page.content(), False
        finally:
            await page.close()

    async def render(self, url: str) -> str:
        """渲染单篇文章，返回挑战通过后的完整 HTML；全部尝试失败返回空串。

        把“拿到 HTML 但仍是过渡页/正文不足”也视为失败并继续重试（CF 挑战通过有时间
        不确定性，重试能显著提高单次抓取的完整率）。所有尝试都未真正通过时返回空串，
        让调用方优雅降级（例如回退 RSS summary）。渲染失败不抛异常。
        """
        if not self.available or not url:
            return ""
        for attempt in range(1, self.attempts + 1):
            await self._throttle()
            self._last_request_at = asyncio.get_event_loop().time()
            try:
                html, passed = await self._render_once(url)
                if passed:
                    return html
                logger.info(f"ℹ️ 挑战未通过/正文不足，重试 ({attempt}/{self.attempts}) [{url}]")
            except Exception as e:
                logger.warning(f"⚠️ 渲染失败 ({attempt}/{self.attempts}) [{url}]: {e}")
            if attempt < self.attempts:
                await asyncio.sleep(self.retry_backoff_seconds)
        return ""
