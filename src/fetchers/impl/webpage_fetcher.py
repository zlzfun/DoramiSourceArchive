import hashlib
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from fetchers.base import BaseFetcher
from models.content import BaseContent, WebPageArticleContent


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class BaseWebPageListFetcher(BaseFetcher):
    """
    官网/博客/新闻列表页抓取器基类。

    子类声明列表页、文章 URL 匹配规则和展示元数据；基类负责从 HTML 中提取文章链接、
    标题、摘要上下文和保守发布时间。默认保持轻量列表抓取；需要更高归档质量时可以开启
    `fetch_detail` 从正文页提取主内容。
    """

    source_id = "unknown_source"
    content_type = "web_article"
    category = "official_web"

    listing_url = ""
    site_name = ""
    source_section = ""
    article_url_patterns: List[str] = []
    exclude_url_patterns: List[str] = []
    default_limit = 20

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "fetch_detail", "label": "抓取正文页", "type": "boolean", "default": False},
            {"field": "detail_max_chars", "label": "正文最大字符", "type": "number", "default": 12000},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return self.default_limit
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"网页条数参数无效，使用默认值: {raw_limit}")
            return self.default_limit

    def _bool_param(self, raw_value: Any) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value in (None, ""):
            return False
        return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _positive_int_param(self, raw_value: Any, default: int) -> int:
        if raw_value in (None, ""):
            return default
        try:
            return max(int(raw_value), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"网页正文长度参数无效，使用默认值: {raw_value}")
            return default

    def _content_id(self, url: str) -> str:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _clean_text(self, text: str) -> str:
        return " ".join((text or "").split())

    def _matches_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if any(pattern in url for pattern in self.exclude_url_patterns):
            return False
        return any(pattern in url for pattern in self.article_url_patterns)

    def _candidate_container(self, link: Tag) -> Tag:
        current: Tag = link
        for _ in range(4):
            parent = current.parent
            if not isinstance(parent, Tag):
                return current
            current = parent
            if current.name in {"article", "li", "section"}:
                return current
            if current.find(["h1", "h2", "h3", "h4"]):
                return current
        return current

    def _title_from_container(self, link: Tag, container: Tag) -> str:
        for heading in container.find_all(["h1", "h2", "h3", "h4"], limit=3):
            title = self._clean_text(heading.get_text(" ", strip=True))
            if title and title.lower() != "read more":
                return title

        link_text = self._clean_text(link.get_text(" ", strip=True))
        if link_text and link_text.lower() != "read more":
            return link_text

        text = self._clean_text(container.get_text(" ", strip=True))
        text = re.sub(r"\b(read more|learn more)\b", "", text, flags=re.IGNORECASE).strip()
        return text[:120] or "未命名网页条目"

    def _summary_from_container(self, title: str, container: Tag) -> str:
        text = self._clean_text(container.get_text(" ", strip=True))
        text = re.sub(r"\b(read more|learn more)\b", "", text, flags=re.IGNORECASE).strip()
        if title and text.startswith(title):
            text = text[len(title):].strip(" -|")
        return text[:500]

    def _extract_datetime(self, text: str) -> str:
        iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
        if iso_match:
            year, month, day = (int(part) for part in iso_match.groups())
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()

        month_match = re.search(
            r"\b([A-Z][a-z]+)\s+(\d{1,2}),\s*(20\d{2})\b",
            text,
        )
        if month_match:
            month_name, day, year = month_match.groups()
            month = MONTHS.get(month_name.lower())
            if month:
                return datetime(int(year), month, int(day), tzinfo=timezone.utc).isoformat()

        return datetime.now(timezone.utc).isoformat()

    def _raw_entry(self, url: str, title: str, summary: str) -> Dict[str, Any]:
        return {
            "listing_url": self.listing_url,
            "url": url,
            "title": title,
            "summary": summary,
        }

    def _extract_detail_text(self, html: str, max_chars: int) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript", "svg", "nav", "header", "footer", "form"]):
            tag.decompose()

        selectors = [
            "article",
            "main",
            "[role='main']",
            ".post-content",
            ".article-content",
            ".entry-content",
            ".blog-post",
            ".markdown",
        ]
        candidates: List[str] = []
        for selector in selectors:
            for node in soup.select(selector):
                text = self._clean_text(node.get_text(" ", strip=True))
                if text:
                    candidates.append(text)

        if not candidates and soup.body:
            candidates.append(self._clean_text(soup.body.get_text(" ", strip=True)))

        if not candidates:
            return ""

        detail_text = max(candidates, key=len)
        return detail_text[:max_chars]

    async def _detail_text_for_url(self, client: httpx.AsyncClient, url: str, max_chars: int) -> str:
        response = await self._safe_get(client, url)
        if not response:
            return ""
        return self._extract_detail_text(response.text, max_chars)

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), 12000)
        if not self.listing_url:
            self.logger.error("网页列表地址不能为空，放弃抓取。")
            return

        response = await self._safe_get(client, self.listing_url)
        if not response:
            return

        soup = BeautifulSoup(response.text, "html.parser")
        seen_urls = set()
        emitted_count = 0

        for link in soup.find_all("a", href=True):
            url = urljoin(str(response.url), str(link["href"]))
            if url in seen_urls or not self._matches_article_url(url):
                continue

            container = self._candidate_container(link)
            title = self._title_from_container(link, container)
            summary = self._summary_from_container(title, container)
            publish_date = self._extract_datetime(f"{title} {summary}")
            detail_text = ""
            if fetch_detail:
                detail_text = await self._detail_text_for_url(client, url, detail_max_chars)
            seen_urls.add(url)
            emitted_count += 1

            raw_data = self._raw_entry(url, title, summary)
            raw_data.update({
                "detail_fetched": fetch_detail,
                "detail_text_length": len(detail_text),
            })
            content = detail_text or summary

            yield WebPageArticleContent(
                id=self._content_id(url),
                title=title,
                source_url=url,
                publish_date=publish_date,
                content=content,
                has_content=bool(content),
                site_name=self.site_name or self.name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage"],
                raw_data=raw_data,
            )

            if emitted_count >= limit:
                break


class AnthropicNewsWebFetcher(BaseWebPageListFetcher):
    source_id = "web_anthropic_news"
    name = "Anthropic News"
    description = "抓取 Anthropic 官网 News 页面中的产品、研究、企业与安全动态。"
    icon = "🟫"
    listing_url = "https://www.anthropic.com/news"
    site_name = "Anthropic"
    source_section = "News"
    article_url_patterns = ["anthropic.com/news/"]
    exclude_url_patterns = ["anthropic.com/news#"]


class ClaudeBlogWebFetcher(BaseWebPageListFetcher):
    source_id = "web_claude_blog"
    name = "Claude Blog"
    description = "抓取 Claude 官方 Blog 页面中的 Claude、Claude Code、Agent 与企业 AI 更新。"
    icon = "🟧"
    listing_url = "https://claude.com/blog"
    site_name = "Claude"
    source_section = "Blog"
    article_url_patterns = ["claude.com/blog/"]
    exclude_url_patterns = ["claude.com/blog/category/"]


class RunwayNewsWebFetcher(BaseWebPageListFetcher):
    source_id = "web_runway_news"
    name = "Runway News"
    description = "抓取 Runway 官网 News/Research 页面中的视频生成、世界模型与产品动态。"
    icon = "🎬"
    listing_url = "https://runwayml.com/news"
    site_name = "Runway"
    source_section = "News"
    article_url_patterns = ["runwayml.com/news/", "runwayml.com/research/"]
    exclude_url_patterns = [
        "runwayml.com/news/research",
        "runwayml.com/news/customers",
        "runwayml.com/research/publications",
    ]


class MistralNewsWebFetcher(BaseWebPageListFetcher):
    source_id = "web_mistral_news"
    name = "Mistral AI News"
    description = "抓取 Mistral AI 官网 News 页面中的模型、产品与企业动态。"
    icon = "🌬️"
    listing_url = "https://mistral.ai/news"
    site_name = "Mistral AI"
    source_section = "News"
    article_url_patterns = ["mistral.ai/news/"]
