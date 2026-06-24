"""基于 crawl4ai 的浏览器详情后端（可选依赖）。

crawl4ai **不是**本项目的默认依赖（见 pyproject 的 ``crawl4ai`` 可选 extra）。本模块在
import 期**绝不**导入 crawl4ai —— 所有 crawl4ai 符号都在方法内惰性导入，未安装时
``is_available()`` 返回 False、``__aenter__`` 进入降级态、``extract`` 恒返回 ``success=False``，
绝不破坏其余子系统或抛断抓取流程。这与"httpx 优先、按需浏览器"的策略一致。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from bs4 import BeautifulSoup

from fetchers.impl.article_extractor import compact_text, detail_title, node_to_markdown

from .backend import DetailResult, WebContentBackend
from .profiles import PROFILES, CrawlProfile, resolve_profile

logger = logging.getLogger(__name__)

# crawl4ai 的运行时目录（缓存/日志）落到项目 data/ 下，避免污染用户 home。
os.environ.setdefault(
    "CRAWL4_AI_BASE_DIRECTORY",
    str(Path(__file__).resolve().parents[3] / "data" / ".crawl4ai-runtime"),
)


class Crawl4AIContentBackend(WebContentBackend):
    name = "crawl4ai"

    def __init__(self, profiles: Tuple[CrawlProfile, ...] = PROFILES, *, text_mode: bool = False):
        self.profiles = profiles
        # text_mode=False 保留正文图片（与现有 node_to_markdown 的 ![](url) 一致），更贴合阅读器展示；
        # 设为 True 可关图加速纯文本抓取。
        self.text_mode = text_mode
        self._crawler = None  # AsyncWebCrawler，惰性创建
        self.available = False

    @classmethod
    def is_available(cls) -> bool:
        """crawl4ai 是否已安装。仅做 import 探测，不启动浏览器。"""
        import importlib.util

        return importlib.util.find_spec("crawl4ai") is not None

    def resolve_profile(self, url: str) -> CrawlProfile:
        return resolve_profile(url, self.profiles)

    async def __aenter__(self) -> "Crawl4AIContentBackend":
        if not self.is_available():
            logger.warning("⚠️ 未安装 crawl4ai，浏览器后端不可用，将降级。")
            return self
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig

            self._crawler = AsyncWebCrawler(
                config=BrowserConfig(
                    headless=True,
                    text_mode=self.text_mode,
                    # 锁定英文，与生产 httpx 一致；否则系统 zh-CN 会触发部分站点（如 Cursor）
                    # 跳转到 /cn/ 翻译版，导致与英文基线相似度骤降。（locale 在 CrawlerRunConfig 里另设）
                    headers={"Accept-Language": "en-US,en;q=0.9"},
                    verbose=False,
                )
            )
            await self._crawler.start()
            self.available = True
        except Exception as e:  # 浏览器二进制缺失/启动失败等
            logger.warning("⚠️ 启动 crawl4ai 失败，浏览器后端降级: %s", e)
            self._crawler = None
            self.available = False
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._crawler is not None:
            try:
                await self._crawler.close()
            finally:
                self._crawler = None
        self.available = False

    def _run_config(self, profile: CrawlProfile):
        from crawl4ai import CacheMode, CrawlerRunConfig

        # 正文 markdown 由项目自有的 node_to_markdown 在 cleaned_html 上生成（保留懒加载图片、
        # 不依赖 crawl4ai 的 fit_markdown/PruningContentFilter，避免图片被裁、占位图泄漏）。
        # 这里只让 crawl4ai 负责渲染 + 按 target/excluded 圈定去噪，产出 cleaned_html。
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            locale="en-US",
            target_elements=list(profile.target_elements) or None,
            excluded_selector=profile.excluded_selector,
            # 不开 remove_overlay_elements/remove_consent_popups：其启发式会把正文题图
            # （如 Anthropic 的 1000x1000 svg）误判为遮罩删掉；而正文已用 target_elements
            # 精确圈定，弹窗/同意框本就在范围外，无需这两个开关。
            remove_overlay_elements=False,
            remove_consent_popups=False,
            wait_for=profile.wait_for,
            wait_for_timeout=15_000 if profile.wait_for else None,
            scan_full_page=profile.scan_full_page,
            verbose=False,
        )

    async def render_html(
        self, url: str, *, wait_for: Optional[str] = None, wait_for_timeout: int = 15_000
    ) -> str:
        """返回浏览器渲染后的原始 HTML（``result.html``，保留全部结构锚点）。

        不设 ``target_elements``/``excluded_selector``（要的是完整 DOM 给调用方的
        Segmenter/article_extractor 处理），也不取 ``cleaned_html``（会剥掉
        ``data-product``/``id``/``data-component-part`` 等锚点）。``wait_for`` 缺省时用命中
        Profile 的等待条件；Cloudflare 挑战页（OpenAI News）由调用方显式传入 ``js:`` 条件
        等待正文出现。失败恒返回空串。"""
        if not self.available or self._crawler is None:
            return ""
        from crawl4ai import CacheMode, CrawlerRunConfig

        profile = self.resolve_profile(url)
        wait_condition = wait_for or profile.wait_for
        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            locale="en-US",
            remove_overlay_elements=False,
            remove_consent_popups=False,
            wait_for=wait_condition,
            wait_for_timeout=wait_for_timeout if wait_condition else None,
            scan_full_page=profile.scan_full_page,
            verbose=False,
        )
        try:
            result = await self._crawler.arun(url=url, config=config)
        except Exception as e:
            logger.warning("⚠️ render_html 渲染失败: %s", e)
            return ""
        if not result.success:
            logger.warning("⚠️ render_html 未成功: %s", result.error_message or result.status_code)
            return ""
        return result.html or ""

    async def extract(
        self, url: str, *, max_chars: int = 8_000, detail_min_chars: int = 200,
        profile: Optional[CrawlProfile] = None,
    ) -> DetailResult:
        if not self.available or self._crawler is None:
            return DetailResult(
                url=url, success=False, backend=self.name,
                error="crawl4ai unavailable",
            )

        # 配置驱动节点（generic_web）显式注入 Profile；其余按 URL 匹配全局 PROFILES（向后兼容）。
        profile = profile or self.resolve_profile(url)
        try:
            result = await self._crawler.arun(url=url, config=self._run_config(profile))
        except Exception as e:
            return DetailResult(
                url=url, success=False, backend=self.name,
                profile_name=profile.name, error=str(e),
            )

        if not result.success:
            return DetailResult(
                url=url, success=False, backend=self.name,
                profile_name=profile.name, status_code=result.status_code,
                error=result.error_message or "crawl failed",
            )

        final_url = result.redirected_url or result.url
        cleaned_html = result.cleaned_html or ""
        metadata = result.metadata or {}

        # 用项目自有的 node_to_markdown 在 cleaned_html 上生成正文（懒加载图片走 data-original，
        # 与 legacy 路径完全一致）。cleaned_html 为空时回退 crawl4ai 自带 raw_markdown。
        text = ""
        if cleaned_html:
            soup = BeautifulSoup(cleaned_html, "html.parser")
            root = soup.body or soup
            # 某些站点（如 Anthropic 的 Next.js SSR+水合）会在 cleaned_html 里产生多个顶层
            # <article>（同一正文的重复副本），target_elements=("article",) 会把它们全拼上导致
            # 正文重复。这里只取正文最长的那个顶层 article，避免重复。
            top_articles = [
                a for a in root.find_all("article") if a.find_parent("article") is None
            ]
            if len(top_articles) > 1:
                root = max(top_articles, key=lambda a: len(a.get_text(" ", strip=True)))
            text = compact_text(node_to_markdown(root, base_url=final_url))
        if not text:
            text = (result.markdown.raw_markdown or "").strip()
        title = str(metadata.get("title") or "")
        if not title and cleaned_html:
            title = detail_title(BeautifulSoup(cleaned_html, "html.parser"))

        return DetailResult(
            title=title,
            text=text[:max_chars],
            method=f"crawl4ai:{profile.name}",
            url=final_url,
            success=bool(text),
            backend=self.name,
            status_code=result.status_code,
            profile_name=profile.name,
            raw_chars=len(result.markdown.raw_markdown or ""),
            cleaned_html=cleaned_html,
            metadata=metadata,
        )
