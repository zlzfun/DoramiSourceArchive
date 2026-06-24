"""站点级声明式抓取 Profile。

把站点差异（正文容器、噪声排除、等待条件）下沉为配置，是中级目标"配置化抓取器"的雏形。
匹配键为 ``domain + URL pattern``；后续可扩展 page type 维度。crawl4ai 后端据此路由。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse


# 跨站通用的噪声选择器：导航、页脚、侧栏、广告、相关推荐、分享、评论、订阅、Cookie 横幅等
COMMON_EXCLUDED_SELECTOR = (
    "nav, footer, aside, form, "
    ".advertisement, .ads, .ad, "
    ".related, .related-content, .related-articles, .recommendations, "
    ".social-share, .share, .comments, .newsletter, .cookie, .cookie-banner"
)


@dataclass(frozen=True)
class CrawlProfile:
    name: str
    domains: Tuple[str, ...]
    path_pattern: str = ".*"
    target_elements: Tuple[str, ...] = ()
    excluded_selector: str = COMMON_EXCLUDED_SELECTOR
    wait_for: Optional[str] = None
    scan_full_page: bool = False
    min_content_chars: int = 200

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        domain_matches = any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self.domains
        )
        return domain_matches and bool(re.search(self.path_pattern, parsed.path))


DEFAULT_PROFILE = CrawlProfile(name="default-pruning", domains=())


# 阶段二将逐步把各站点专用提取（_extract_*_detail）收敛到这里。
PROFILES: Tuple[CrawlProfile, ...] = (
    CrawlProfile(
        name="anthropic-article",
        domains=("anthropic.com",),
        path_pattern=r"^/news/",
        target_elements=("article",),
        # <article> 末尾含 "Related content" 区块（其标题与 Read more 卡片都带 LinkGrid-* 哈希类，
        # 语义前缀 LinkGrid 稳定），用属性子串选择器剔除，避免相关文章混入正文。
        excluded_selector=COMMON_EXCLUDED_SELECTOR + ', [class*="LinkGrid"]',
        wait_for="css:article",
    ),
    CrawlProfile(
        name="ithome-article",
        domains=("ithome.com",),
        path_pattern=r"^/0/",
        target_elements=("#paragraph.post_content", ".post_content"),
        excluded_selector="script, style, noscript, button, .tougao-user, .ad-tips",
        wait_for="css:.post_content",
    ),
    CrawlProfile(
        name="deepseek-changelog",
        domains=("api-docs.deepseek.com",),
        path_pattern=r"^/updates/",
        target_elements=("article",),
        wait_for="css:article",
    ),
    # —— 批次 2（B 类，待旁路验收）——
    CrawlProfile(
        name="claude-blog",
        domains=("claude.com",),
        path_pattern=r"^/blog/",
        # Webflow 页无 <article>；正文在 .blog_post_section_wrap。其内尾部还混入
        # testimonials/FAQ/资源卡等 CMS 控件，它们都带 Webflow 隐藏/空标记类
        # （w-condition-invisible=浏览器中本就隐藏、w-dyn-empty=空集合），一并剔除。
        target_elements=(".blog_post_section_wrap",),
        excluded_selector=COMMON_EXCLUDED_SELECTOR + ", .w-condition-invisible, .w-dyn-empty",
        wait_for="css:.blog_post_section_wrap",
    ),
    CrawlProfile(
        name="cursor-changelog",
        domains=("cursor.com",),
        # 允许 /changelog/ 与 locale 前缀（如 /cn/changelog/）；已用 locale=en-US 锁英文
        path_pattern=r"/changelog/",
        target_elements=("article", "main"),
        wait_for="css:main",
    ),
    CrawlProfile(
        name="qbitai-article",
        domains=("qbitai.com",),
        path_pattern=r".*",
        # 照搬专用提取器 _extract_qbitai_detail 的容器与噪声规则
        target_elements=(".content .article", "div.article"),
        excluded_selector=(
            "script, style, noscript, button, "
            ".wx_img, .share_pc, .tags, .person_box, .xiangguan, img.avatar, .avatar"
        ),
    ),
    CrawlProfile(
        name="aiera-article",
        domains=("aiera.com.cn",),
        path_pattern=r".*",
        # WordPress：页面有 9 个 <article>（含相关文章），正文唯一容器是 article .entry-content。
        # 服务端渲染，正文在初始 HTML 中，无需 wait_for（加了反而易误判超时）。
        target_elements=("article .entry-content",),
    ),
)


def resolve_profile(
    url: str, profiles: Tuple[CrawlProfile, ...] = PROFILES
) -> CrawlProfile:
    """返回首个匹配的 Profile，无匹配时返回 DEFAULT_PROFILE。"""
    return next((p for p in profiles if p.matches(url)), DEFAULT_PROFILE)
