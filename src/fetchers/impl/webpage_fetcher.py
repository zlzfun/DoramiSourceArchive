import hashlib
import html
import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Iterable, List
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from fetchers.base import BaseFetcher
from fetchers.impl.article_extractor import extract_article_detail, extract_detail_from_html
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
    default_fetch_detail = False
    default_detail_max_chars = 12000
    generic_link_titles = {"read more", "learn more", "blog", "news", "publication", "publications"}

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "fetch_detail", "label": "抓取正文页", "type": "boolean", "default": cls.default_fetch_detail},
            {"field": "detail_max_chars", "label": "正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
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
            return self.default_fetch_detail
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

    def _title_from_url(self, url: str) -> str:
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        if not slug:
            return ""
        words = [word for word in re.split(r"[-_]+", slug) if word]
        if not words:
            return ""
        title = " ".join(word.upper() if word.lower() in {"ai", "api", "llm"} else word.capitalize() for word in words)
        return self._clean_text(title)

    def _entry_url_from_slug(self, slug: str) -> str:
        slug = (slug or "").strip()
        if not slug:
            return ""
        if slug.startswith(("http://", "https://")):
            return self._normalize_article_url(slug)
        if slug.startswith("/"):
            return self._normalize_article_url(urljoin(self.listing_url, slug))
        listing_path = urlparse(self.listing_url).path.rstrip("/")
        base_path = listing_path or ""
        return self._normalize_article_url(urljoin(self.listing_url, f"{base_path}/{slug}"))

    def _normalize_article_url(self, url: str) -> str:
        parsed = urlparse(url)
        query = urlencode([
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"ref", "spm", "view_from"}
        ])
        path = parsed.path.rstrip("/") or "/"
        return parsed._replace(path=path, query=query, fragment="").geturl()

    def _matches_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if re.search(r"/page/\d+/?$", parsed.path):
            return False
        if any(pattern in url for pattern in self.exclude_url_patterns):
            return False
        return any(pattern in url for pattern in self.article_url_patterns)

    def _candidate_container(self, link: Tag) -> Tag:
        if link.find(["article", "h1", "h2", "h3", "h4"]):
            return link
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
            if title and title.lower() not in self.generic_link_titles:
                return title

        link_text = self._clean_text(link.get_text(" ", strip=True))
        if link_text and link_text.lower() not in self.generic_link_titles:
            return link_text

        text = self._clean_text(container.get_text(" ", strip=True))
        text = re.sub(r"\b(read more|learn more)\b", "", text, flags=re.IGNORECASE).strip()
        if text.lower() not in self.generic_link_titles:
            return text[:120] or "未命名网页条目"
        return self._title_from_url(str(link.get("href") or "")) or "未命名网页条目"

    def _summary_from_container(self, title: str, container: Tag) -> str:
        text = self._clean_text(container.get_text(" ", strip=True))
        text = re.sub(r"\b(read more|learn more)\b", "", text, flags=re.IGNORECASE).strip()
        if title and text.startswith(title):
            text = text[len(title):].strip(" -|")
        return text[:500]

    def _extract_datetime_or_empty(self, text: str) -> str:
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

        return ""

    def _extract_datetime(self, text: str) -> str:
        extracted = self._extract_datetime_or_empty(text)
        if extracted:
            return extracted
        return datetime.now(timezone.utc).isoformat()

    def _sort_datetime(self, raw_value: str) -> datetime:
        if not raw_value:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    def _script_payloads(self, soup: BeautifulSoup) -> List[str]:
        payloads: List[str] = []
        for script in soup.find_all("script"):
            text = script.get_text() or ""
            if not text:
                continue
            payloads.append(text)
            unescaped = html.unescape(text)
            if unescaped != text:
                payloads.append(unescaped)

            # Next.js React Server Components often wrap JSON fragments in JS
            # string chunks like self.__next_f.push([1,"..."]).
            for match in re.finditer(r"__next_f\.push\(\[1,((?:\"(?:\\.|[^\"\\])*\")|null)", text, re.DOTALL):
                chunk = match.group(1)
                if chunk == "null":
                    continue
                try:
                    decoded = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, str):
                    payloads.append(decoded)
        return payloads

    def _json_values_from_text(self, text: str) -> Iterable[Any]:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\{\[]", text):
            try:
                value, _ = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            yield value

    def _walk_json(self, value: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_json(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_json(child)

    def _embedded_article_entries(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        seen_keys = set()
        for payload in self._script_payloads(soup):
            for value in self._json_values_from_text(payload):
                for record in self._walk_json(value):
                    title = self._clean_text(str(
                        record.get("title")
                        or record.get("headline")
                        or record.get("label")
                        or record.get("name")
                        or ""
                    ))
                    raw_url = record.get("url") or record.get("link") or record.get("href") or record.get("slug")
                    if not title or not isinstance(raw_url, str):
                        continue
                    url = self._entry_url_from_slug(raw_url)
                    if not url or not self._matches_article_url(url):
                        continue
                    key = (url, title)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    summary_value = record.get("description") or record.get("summary") or record.get("excerpt") or ""
                    if not isinstance(summary_value, str):
                        summary_value = ""
                    raw_date = (
                        record.get("date")
                        or record.get("publishedAt")
                        or record.get("published_at")
                        or record.get("publishDate")
                        or record.get("publishedDate")
                        or record.get("createdAt")
                        or record.get("updatedAt")
                        or ""
                    )
                    if not isinstance(raw_date, str):
                        raw_date = ""
                    publish_date = self._extract_datetime_or_empty(raw_date)
                    if not publish_date and raw_date:
                        publish_date = raw_date
                    entries.append({
                        "url": url,
                        "title": title,
                        "summary": self._clean_text(summary_value)[:500],
                        "publish_date": publish_date,
                        "listing_source": "embedded_json",
                    })
        return entries

    def _merge_entry(self, entries_by_url: Dict[str, Dict[str, Any]], entry: Dict[str, Any]) -> None:
        url = entry["url"]
        existing = entries_by_url.get(url)
        if not existing:
            entries_by_url[url] = entry
            return
        if existing.get("title") == "未命名网页条目" and entry.get("title"):
            existing["title"] = entry["title"]
        if not existing.get("summary") and entry.get("summary"):
            existing["summary"] = entry["summary"]
        if (
            entry.get("publish_date")
            and (
                not existing.get("publish_date")
                or entry.get("listing_source") == "embedded_json"
            )
        ):
            existing["publish_date"] = entry["publish_date"]
        sources = set(str(existing.get("listing_source", "")).split("+"))
        source = entry.get("listing_source")
        if source:
            sources.add(str(source))
        existing["listing_source"] = "+".join(sorted(source for source in sources if source))

    def _raw_entry(self, url: str, title: str, summary: str) -> Dict[str, Any]:
        return {
            "listing_url": self.listing_url,
            "url": url,
            "title": title,
            "summary": summary,
        }

    def _detail_title(self, soup: BeautifulSoup) -> str:
        detail = extract_detail_from_html(str(soup), self.default_detail_max_chars)
        return detail.title

    def _extract_detail(self, html: str, max_chars: int) -> Dict[str, str]:
        detail = extract_detail_from_html(html, max_chars)
        return {"title": detail.title, "text": detail.text, "method": detail.method}

    async def _detail_for_url(self, client: httpx.AsyncClient, url: str, max_chars: int) -> Dict[str, str]:
        response = await self._safe_get(client, url)
        if not response:
            return {"title": "", "text": "", "method": "", "url": ""}
        detail = await extract_article_detail(
            client,
            self._safe_get,
            str(response.url),
            response.text,
            max_chars,
        )
        return {"title": detail.title, "text": detail.text, "method": detail.method, "url": detail.url}

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if not self.listing_url:
            self.logger.error("网页列表地址不能为空，放弃抓取。")
            return

        response = await self._safe_get(client, self.listing_url)
        if not response:
            return

        soup = BeautifulSoup(response.text, "html.parser")
        entries_by_url: Dict[str, Dict[str, Any]] = {}
        order = 0

        for link in soup.find_all("a", href=True):
            url = self._normalize_article_url(urljoin(str(response.url), str(link["href"])))
            if not self._matches_article_url(url):
                continue

            container = self._candidate_container(link)
            title = self._title_from_container(link, container)
            summary = self._summary_from_container(title, container)
            publish_date = self._extract_datetime_or_empty(f"{title} {summary}")
            self._merge_entry(entries_by_url, {
                "url": url,
                "title": title,
                "summary": summary,
                "publish_date": publish_date,
                "listing_source": "html_anchor",
                "order": order,
            })
            order += 1

        for embedded_entry in self._embedded_article_entries(soup):
            embedded_entry["order"] = order
            self._merge_entry(entries_by_url, embedded_entry)
            order += 1

        entries = sorted(
            entries_by_url.values(),
            key=lambda entry: (self._sort_datetime(entry.get("publish_date", "")), -entry.get("order", 0)),
            reverse=True,
        )

        for entry in entries[:limit]:
            url = entry["url"]
            title = entry["title"]
            summary = entry["summary"]
            publish_date = entry.get("publish_date") or self._extract_datetime(f"{title} {summary}")
            detail = {"title": "", "text": ""}
            if fetch_detail:
                detail = await self._detail_for_url(client, url, detail_max_chars)
                if (title == "未命名网页条目" or title.lower() in self.generic_link_titles) and detail["title"]:
                    title = detail["title"]

            raw_data = self._raw_entry(url, title, summary)
            raw_data.update({
                "listing_source": entry.get("listing_source", ""),
                "detail_fetched": fetch_detail,
                "detail_title": detail["title"],
                "detail_text_length": len(detail["text"]),
                "detail_extraction_method": detail.get("method", ""),
                "detail_source_url": detail.get("url", ""),
            })
            content = detail["text"] or summary

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
    default_fetch_detail = True
    source_owner = "anthropic"
    source_brand = "anthropic"
    source_scope = "company"
    source_channel = "newsroom"
    source_url = listing_url
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "product_update", "api_platform", "research_paper", "safety_policy"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


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
    default_fetch_detail = True
    source_owner = "anthropic"
    source_brand = "claude"
    source_scope = "product_family"
    source_channel = "blog"
    source_url = listing_url
    provenance_tier = "tier0_primary"
    content_tags = ["product_update", "developer_tool", "tutorial_or_practice", "api_platform", "model_release"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"


class QwenBlogWebFetcher(BaseWebPageListFetcher):
    source_id = "web_qwen_blog"
    name = "Qwen Blog"
    description = "抓取 Qwen 官方 Blog 中的模型、产品、多模态与 Agent 动态。"
    icon = "🟦"
    listing_url = "https://qwen.ai/api/page_config?code=news.news-list"
    site_name = "Qwen"
    source_section = "Blog"
    article_url_patterns = ["qwen.ai/blog", "docs.qwenlm.ai/"]
    exclude_url_patterns = ["qwen.ai/blog#"]
    default_fetch_detail = True
    source_owner = "alibaba"
    source_brand = "qwen"
    source_scope = "model_family"
    source_channel = "blog_api"
    source_url = "https://qwen.ai/blog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "product_update", "research_paper", "developer_tool"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "fragile_js_api"

    def _qwen_block_text(self, value: Any) -> List[str]:
        texts: List[str] = []
        if isinstance(value, dict):
            text = self._clean_text(str(value.get("text") or ""))
            if text:
                texts.append(text)
            for key in ["tokens", "children", "items"]:
                texts.extend(self._qwen_block_text(value.get(key)))
        elif isinstance(value, list):
            for item in value:
                texts.extend(self._qwen_block_text(item))
        return texts

    async def _qwen_json_detail(self, client: httpx.AsyncClient, detail_url: str, max_chars: int) -> Dict[str, Any]:
        if not detail_url.endswith(".json"):
            return {"text": "", "method": ""}
        response = await self._safe_get(client, detail_url)
        if not response:
            return {"text": "", "method": ""}
        try:
            blocks = response.json()
        except ValueError:
            return {"text": "", "method": ""}
        texts = self._qwen_block_text(blocks)
        content = "\n\n".join(dict.fromkeys(texts)).strip()
        return {
            "text": content[:max_chars],
            "method": "qwen_index_json",
            "url": str(response.url),
        }

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        response = await self._safe_get(client, self.listing_url)
        if not response:
            return

        try:
            records = response.json()
        except ValueError:
            self.logger.warning("Qwen Blog API 返回了非 JSON 内容。")
            return
        if not isinstance(records, list):
            self.logger.warning("Qwen Blog API 返回了非列表结构。")
            return

        records = sorted(
            (record for record in records if isinstance(record, dict) and not record.get("draft")),
            key=lambda record: self._sort_datetime(str(record.get("date") or "")),
            reverse=True,
        )
        for record in records[:limit]:
            article_id = str(record.get("id") or "").strip()
            token_links = str(record.get("tokenLinks") or "").strip()
            source_url = f"https://qwen.ai/blog?id={article_id}" if article_id else token_links or "https://qwen.ai/blog"
            title = self._clean_text(str(record.get("title") or "")) or "未命名 Qwen Blog 条目"
            summary = self._clean_text(str(record.get("description") or record.get("introduction") or ""))[:500]
            tags = [str(tag) for tag in record.get("tags") or []]
            content = summary
            raw_data = {
                "listing_url": self.listing_url,
                "url": source_url,
                "tokenLinks": token_links,
                "author": record.get("author") or "",
                "readTime": record.get("readTime") or "",
                "word_count": record.get("word_count") or "",
                "tags": tags,
                "listing_source": "qwen_page_config",
            }

            if self._bool_param(kwargs.get("fetch_detail")):
                detail_url = token_links or source_url
                max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
                json_detail = await self._qwen_json_detail(client, detail_url, max_chars)
                if json_detail["text"]:
                    content = json_detail["text"]
                    raw_data.update({
                        "detail_text_length": len(json_detail["text"]),
                        "detail_extraction_method": json_detail["method"],
                        "detail_source_url": json_detail["url"],
                    })
                else:
                    detail = await extract_article_detail(client, self._safe_get, detail_url, "", max_chars)
                    if detail.text and len(detail.text) > len(content):
                        content = detail.text
                    raw_data.update({
                        "detail_title": detail.title,
                        "detail_text_length": len(detail.text),
                        "detail_extraction_method": detail.method,
                        "detail_source_url": detail.url,
                    })

            yield WebPageArticleContent(
                id=self._content_id(source_url),
                title=title,
                source_url=source_url,
                publish_date=str(record.get("date") or datetime.now(timezone.utc).isoformat()),
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage", *tags],
                raw_data=raw_data,
            )
