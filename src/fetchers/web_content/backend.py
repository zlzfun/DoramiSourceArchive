"""统一网页详情抓取的接口与结果模型。

``WebContentBackend`` 把"给定一个文章 URL → 返回干净正文"的能力抽象成一个可替换、
可对比、可降级的后端。当前有两套实现：现有 httpx 提取（Legacy）与 crawl4ai 浏览器后端。
``BaseFetcher`` 及各来源适配器后续只依赖本接口，而非任一具体后端，便于测试、回退与替换。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .profiles import CrawlProfile


@dataclass
class DetailResult:
    """一次详情提取的统一结果。

    是现有 ``ArticleDetail``（title/text/method/url）与 crawl4ai demo ``DetailResult`` 的并集，
    便于两套后端产出同构结果做旁路对比。
    """

    title: str = ""
    text: str = ""
    method: str = ""        # 提取方法标识，如 "html_selector" / "crawl4ai:ithome-article"
    url: str = ""           # 跟随重定向后的最终 URL
    success: bool = False
    backend: str = ""       # 产出该结果的后端名，如 "legacy" / "crawl4ai"
    status_code: Optional[int] = None
    profile_name: str = ""  # crawl4ai 命中的 Profile（legacy 恒为空）
    raw_chars: int = 0      # 过滤/截断前的原始字符数（用于评估过滤损耗）
    cleaned_html: str = ""  # 渲染清洗后的 HTML，供单页拆条 Segmenter 复用
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text or "")


class WebContentBackend(abc.ABC):
    """网页详情抓取后端。用作异步上下文管理器以管理共享资源（httpx 连接池 / 浏览器）。"""

    name: str = "base"

    @classmethod
    def is_available(cls) -> bool:
        """该后端在当前环境是否可用（依赖是否就绪）。默认可用；crawl4ai 后端会覆盖。"""
        return True

    async def __aenter__(self) -> "WebContentBackend":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    @abc.abstractmethod
    async def extract(
        self, url: str, *, max_chars: int = 8_000, detail_min_chars: int = 200,
        profile: "Optional[CrawlProfile]" = None,
    ) -> DetailResult:
        """抓取并提取单个详情页正文。任何失败都应返回 ``success=False`` 的结果，绝不抛断。

        ``profile`` 显式指定站点 Profile（配置驱动节点用）；为空时后端按 URL 匹配全局 PROFILES。
        不支持 Profile 的后端（如 legacy）可忽略此参数。"""
        raise NotImplementedError

    async def render_html(
        self, url: str, *, wait_for: Optional[str] = None, wait_for_timeout: int = 15_000
    ) -> str:
        """返回浏览器渲染后的**原始** HTML（保留全部结构锚点），失败返回空串。

        供两类调用方使用：
        - C 类单页拆条节点在 httpx 拿到 JS 空壳时兜底——它们的 Segmenter 吃的是原始 HTML 的
          结构锚点（``data-product``/``id``/``data-component-part``/日期标题/论文卡片），故
          **不能**用 ``extract`` 的 ``cleaned_html``（会被剥属性/重构）；
        - Cloudflare 挑战页（如 OpenAI News）作为 Playwright 渲染的替代——传入自定义
          ``wait_for``（如 ``js:`` 条件等待正文出现）以等待挑战通过。

        默认无渲染能力（返回空串），仅 crawl4ai 后端覆盖。"""
        return ""
