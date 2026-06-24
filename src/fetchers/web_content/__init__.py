"""统一网页内容抓取后端（Web Content Runtime）。

阶段一：引入 ``WebContentBackend`` 抽象与两套实现，做旁路双路对比，**不改动生产抓取路径**。

- ``WebContentBackend`` / ``DetailResult`` —— 统一接口与结果模型（backend.py）
- ``LegacyArticleExtractorBackend`` —— 包裹现有 ``extract_article_detail`` 的 httpx 路径（legacy_backend.py）
- ``Crawl4AIContentBackend`` —— 基于 crawl4ai 的浏览器后端，**懒加载、可缺省降级**（crawl4ai_backend.py）
- ``CrawlProfile`` / ``PROFILES`` —— 站点级声明式 Profile（profiles.py）
- ``compare_detail`` —— 双路对比指标（compare.py）

设计原则（见 docs/analysis/crawl4ai-feasibility.md）：httpx 优先、按需浏览器；crawl4ai 为可选依赖，
未安装时 ``Crawl4AIContentBackend.is_available()`` 为 False 且不会在 import 期破坏其余子系统。
"""

from .backend import DetailResult, WebContentBackend
from .legacy_backend import LegacyArticleExtractorBackend
from .profiles import DEFAULT_PROFILE, PROFILES, CrawlProfile

__all__ = [
    "DetailResult",
    "WebContentBackend",
    "LegacyArticleExtractorBackend",
    "CrawlProfile",
    "PROFILES",
    "DEFAULT_PROFILE",
]
