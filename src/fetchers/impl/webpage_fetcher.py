import hashlib
import html
import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from fetchers.base import BaseFetcher
from fetchers.impl.article_extractor import (
    extract_article_detail,
    extract_detail_from_html,
    node_to_markdown,
)
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
    default_limit = 12
    default_fetch_detail = False
    default_detail_max_chars = 8000
    generic_link_titles = {"read more", "learn more", "blog", "news", "publication", "publications"}
    # 列表页常把导航/页脚链接（定价、企业版等）也匹配进来：它们既无正文、详情页又多为 404。
    # 置 True 时丢弃正文为空的条目，避免把这类导航垃圾入库。默认 False，保持既有行为不变。
    drop_empty_content = False
    # 列表翻页上限：默认 1（不翻页）。子类设大并实现 _next_listing_page_url 才会逐页累积。
    max_listing_pages = 1

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

    def _next_listing_page_url(self, soup: BeautifulSoup, current_url: str) -> Optional[str]:
        """返回下一页列表 URL；无翻页则返回 None。子类按站点分页规则实现。"""
        return None

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
        backend_detail = await self._web_backend_detail(url, max_chars)
        if backend_detail:
            return backend_detail
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

    async def _should_skip_detail_fetch(self, content_id: str) -> bool:
        """详情请求前的去重预检：该条目是否已入库且已有正文。

        命中则跳过昂贵的详情页请求（重复抓取时的主要耗时）；库中缺席或仍空正文的
        条目返回 False，照常抓详情以保留空正文回填语义。未注入去重钩子时恒为 False，
        即维持原抓取行为。
        """
        if not content_id:
            return False
        flags = await self._lookup_existing_content_flags([content_id])
        return flags.get(content_id, False)

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if not self.listing_url:
            raise ValueError("网页列表地址不能为空")

        entries_by_url: Dict[str, Dict[str, Any]] = {}
        order = 0
        page_url = self.listing_url
        visited_pages = set()
        pages_fetched = 0

        # 部分列表页只展示最近若干条，更早的需翻页（如 Cursor 的 /changelog/page/N）。
        # 默认 max_listing_pages=1（不翻页，保持原行为）；子类设大并实现 _next_listing_page_url
        # 即可逐页累积，直到凑够 limit、翻完上限或没有下一页。
        while page_url and page_url not in visited_pages and pages_fetched < max(1, self.max_listing_pages):
            visited_pages.add(page_url)
            response = await self._safe_get(client, page_url)
            if not response:
                if pages_fetched == 0:
                    raise RuntimeError(f"网页列表请求失败: {self.listing_url}")
                break

            soup = BeautifulSoup(response.text, "html.parser")
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

            pages_fetched += 1
            if len(entries_by_url) >= limit:
                break
            page_url = self._next_listing_page_url(soup, str(response.url))

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
            content_id = self._content_id(url)
            detail = {"title": "", "text": ""}
            # 已入库且有正文则跳过详情请求，避免对重复条目重复抓取正文。
            detail_fetched = fetch_detail and not await self._should_skip_detail_fetch(content_id)
            if detail_fetched:
                detail = await self._detail_for_url(client, url, detail_max_chars)
                if (title == "未命名网页条目" or title.lower() in self.generic_link_titles) and detail["title"]:
                    title = detail["title"]

            raw_data = self._raw_entry(url, title, summary)
            raw_data.update({
                "listing_source": entry.get("listing_source", ""),
                "detail_fetched": detail_fetched,
                "detail_title": detail["title"],
                "detail_text_length": len(detail["text"]),
                "detail_extraction_method": detail.get("method", ""),
                "detail_source_url": detail.get("url", ""),
            })
            content = detail["text"] or summary
            if self.drop_empty_content and not content:
                continue

            yield WebPageArticleContent(
                id=content_id,
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
    default_limit = 10
    default_fetch_detail = True
    # 旁路验收：crawl4ai 详情与生产路径相似度 0.97，已迁移（装了 crawl4ai 时走浏览器后端，否则回退）
    web_backend_enabled = True
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

    # Anthropic News 是 Next.js RSC 流式渲染：初始 DOM 里只有首屏约 11 条 <a>，更早的
    # 文章（页面里要点 “See More” 才显示）都嵌在 self.__next_f 的转义 JSON 流中，作为
    # Sanity 风格的 {"_type":"post", "publishedOn", "slug":{"current":...}, "title",
    # "summary", "subjects":[...]} 对象出现。通用锚点抓取既会把日期+分类拼进标题、混入
    # 导航页脚噪声，也抓不到首屏之外的旧文。这里复用基类已解码 __next_f 的能力，只筛选
    # _type=="post" 对象，拿到全部条目与干净标题、发布日期，再复用正文抓取/排序逻辑。
    def _anthropic_news_entries(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        seen = set()
        for payload in self._script_payloads(soup):
            for value in self._json_values_from_text(payload):
                for record in self._walk_json(value):
                    if record.get("_type") != "post":
                        continue
                    slug = record.get("slug")
                    if isinstance(slug, dict):
                        slug = slug.get("current")
                    if not isinstance(slug, str) or not slug:
                        continue
                    url = self._entry_url_from_slug(slug)
                    if not self._matches_article_url(url) or url in seen:
                        continue
                    seen.add(url)

                    title = self._clean_text(str(record.get("title") or ""))
                    summary_value = record.get("summary") or record.get("description") or ""
                    if not isinstance(summary_value, str):
                        summary_value = ""
                    raw_date = record.get("publishedOn") or record.get("date") or ""
                    if not isinstance(raw_date, str):
                        raw_date = ""
                    # publishedOn 是完整 ISO（带 Z 或时区偏移），date 是纯日期；统一规范化为
                    # UTC isoformat，避免下游字符串比较时混用 Z / +00:00 两种格式。
                    publish_date = self._extract_datetime_or_empty(raw_date)
                    if not publish_date and raw_date:
                        parsed = self._sort_datetime(raw_date)
                        if parsed != datetime.min.replace(tzinfo=timezone.utc):
                            publish_date = parsed.astimezone(timezone.utc).isoformat()
                        else:
                            publish_date = raw_date
                    subjects = [
                        str(tag.get("label", "")).strip()
                        for tag in (record.get("subjects") or [])
                        if isinstance(tag, dict) and tag.get("label")
                    ]
                    entries.append({
                        "url": url,
                        "title": title or "未命名网页条目",
                        "summary": self._clean_text(summary_value)[:500],
                        "publish_date": publish_date,
                        "subjects": subjects,
                        "listing_source": "anthropic_news_rsc",
                    })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.listing_url)
        if not response:
            raise RuntimeError(f"Anthropic News 页面请求失败: {self.listing_url}")

        soup = BeautifulSoup(response.text, "html.parser")
        entries = self._anthropic_news_entries(soup)
        if not entries:
            # RSC 结构变更时回退到通用锚点抓取，保证不静默失败
            async for article in super()._run(client, **kwargs):
                yield article
            return

        entries.sort(key=lambda entry: self._sort_datetime(entry.get("publish_date", "")), reverse=True)

        emitted = 0
        for entry in entries:
            url = entry["url"]
            title = entry["title"]
            summary = entry["summary"]
            publish_date = entry.get("publish_date") or self._extract_datetime(title)
            content_id = self._content_id(url)

            detail = {"title": "", "text": "", "method": "", "url": ""}
            # 已入库且有正文则跳过详情请求，避免对重复条目重复抓取正文。
            detail_fetched = fetch_detail and not await self._should_skip_detail_fetch(content_id)
            if detail_fetched:
                detail = await self._detail_for_url(client, url, detail_max_chars)
                if (title == "未命名网页条目" or title.lower() in self.generic_link_titles) and detail["title"]:
                    title = detail["title"]
            content = detail["text"] or summary

            raw_data = self._raw_entry(url, title, summary)
            raw_data.update({
                "listing_source": entry["listing_source"],
                "subjects": entry.get("subjects", []),
                "detail_fetched": detail_fetched,
                "detail_title": detail["title"],
                "detail_text_length": len(detail["text"]),
                "detail_extraction_method": detail.get("method", ""),
                "detail_source_url": detail.get("url", ""),
            })

            yield WebPageArticleContent(
                id=content_id,
                title=title,
                source_url=url,
                publish_date=publish_date,
                content=content,
                has_content=bool(content),
                site_name=self.site_name or self.name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage"] + entry.get("subjects", []),
                raw_data=raw_data,
            )
            emitted += 1
            if emitted >= limit:
                break


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
    default_limit = 10
    default_fetch_detail = True
    # 旁路验收：crawl4ai(main 容器) 与生产路径相似度 0.855，已迁移；未装 crawl4ai 时回退通用提取器
    web_backend_enabled = True
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


class IThomeAiWebFetcher(BaseWebPageListFetcher):
    source_id = "web_ithome_ai"
    name = "IT之家 AI"
    description = "抓取 IT之家智能时代人工智能分类页中的 AI 模型、产品、智能体和产业资讯。"
    icon = "📰"
    listing_url = "https://next.ithome.com/ai"
    site_name = "IT之家"
    source_section = "人工智能"
    category = "media"
    article_url_patterns = ["ithome.com/0/"]
    exclude_url_patterns = ["next.ithome.com", "m.ithome.com", "quan.ithome.com"]
    # 旁路验收：crawl4ai 详情与生产路径相似度 0.81（≥0.8 门槛），已迁移；未装 crawl4ai 时回退专用提取器
    web_backend_enabled = True
    default_limit = 18
    default_fetch_detail = True
    source_owner = "ithome"
    source_brand = "IT之家"
    source_scope = "tech_media"
    source_channel = "website_category"
    source_url = listing_url
    provenance_tier = "tier1_curated"
    content_tags = ["market_news", "model_release", "product_update", "developer_tool"]
    signal_strength = "medium_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public_website_category"

    def _parse_listing_datetime(self, raw_value: str) -> str:
        raw_value = (raw_value or "").strip()
        if not raw_value:
            return ""
        raw_value = re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r"\1", raw_value)
        try:
            parsed = datetime.fromisoformat(raw_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return ""

    def _list_items(self, soup: BeautifulSoup) -> List[Tag]:
        items = soup.select("#list ul.bl > li")
        return items or soup.select("ul.bl > li")

    def _extract_ithome_detail(self, html_text: str, max_chars: int, base_url: str = "") -> Dict[str, str]:
        soup = BeautifulSoup(html_text, "html.parser")
        title = self._detail_title(soup)
        body = soup.select_one("#paragraph.post_content") or soup.select_one(".post_content")
        if not body:
            return {"title": title, "text": "", "method": ""}

        for selector in [".tougao-user", ".ad-tips", "script", "style", "noscript", "button"]:
            for node in body.select(selector):
                node.decompose()

        # 用 node_to_markdown 保留正文图片(`![](url)`)与段落/列表换行
        text = node_to_markdown(body, base_url)
        if not text:
            text = self._clean_text(body.get_text(" ", strip=True))
        return {
            "title": title,
            "text": text[:max_chars],
            "method": "ithome_post_content",
        }

    async def _detail_for_url(self, client: httpx.AsyncClient, url: str, max_chars: int) -> Dict[str, str]:
        backend_detail = await self._web_backend_detail(url, max_chars)
        if backend_detail:
            return backend_detail
        response = await self._safe_get(client, url)
        if not response:
            return {"title": "", "text": "", "method": "", "url": ""}
        detail = self._extract_ithome_detail(response.text, max_chars, str(response.url))
        if detail["text"]:
            return {**detail, "url": str(response.url)}
        return await super()._detail_for_url(client, url, max_chars)

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.listing_url)
        if not response:
            raise RuntimeError(f"IT之家 AI 分类页请求失败: {self.listing_url}")

        soup = BeautifulSoup(response.text, "html.parser")
        emitted = 0
        seen_urls: set[str] = set()
        for item in self._list_items(soup):
            title_link = item.select_one("a.title[href]")
            if not title_link:
                continue
            url = self._normalize_article_url(urljoin(str(response.url), str(title_link["href"])))
            if not self._matches_article_url(url) or url in seen_urls:
                continue
            seen_urls.add(url)

            title = self._clean_text(title_link.get_text(" ", strip=True)) or str(title_link.get("title") or "")
            summary_node = item.select_one(".m")
            summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")[:500]
            content_node = item.select_one(".c")
            raw_publish_date = str(content_node.get("data-ot") or "") if content_node else ""
            publish_date = self._parse_listing_datetime(raw_publish_date) or self._extract_datetime(f"{title} {summary}")
            tags = [self._clean_text(tag.get_text(" ", strip=True)) for tag in item.select(".tags a")]
            tags = [tag for tag in tags if tag]
            image_node = item.select_one("a.img img")
            media_url = ""
            if image_node:
                media_url = str(image_node.get("data-original") or image_node.get("src") or "")

            content_id = self._content_id(url)
            detail = {"title": "", "text": "", "method": "", "url": ""}
            # 已入库且有正文则跳过详情请求，避免对重复条目重复抓取正文。
            detail_fetched = fetch_detail and not await self._should_skip_detail_fetch(content_id)
            if detail_fetched:
                detail = await self._detail_for_url(client, url, detail_max_chars)
                if detail["title"] and not title:
                    title = detail["title"]
            content = detail["text"] or summary

            yield WebPageArticleContent(
                id=content_id,
                title=title or "未命名 IT之家 AI 条目",
                source_url=url,
                publish_date=publish_date,
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage", *tags],
                raw_data={
                    "listing_url": self.listing_url,
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "listing_source": "ithome_ai_category_html",
                    "listing_publish_date": raw_publish_date,
                    "tags": tags,
                    "media_url": media_url,
                    "detail_fetched": detail_fetched,
                    "detail_title": detail["title"],
                    "detail_text_length": len(detail["text"]),
                    "detail_extraction_method": detail.get("method", ""),
                    "detail_source_url": detail.get("url", ""),
                },
            )
            emitted += 1
            if emitted >= limit:
                break


class QwenBlogWebFetcher(BaseWebPageListFetcher):
    source_id = "web_qwen_blog"
    name = "Qwen Blog"
    description = "抓取 Qwen 官方 Blog 中的模型、产品、多模态与 Agent 动态。"
    icon = "🟦"
    listing_url = "https://qwen.ai/api/v2/article/retrieval"
    site_name = "Qwen"
    source_section = "Blog"
    article_url_patterns = ["qwen.ai/blog", "docs.qwenlm.ai/"]
    exclude_url_patterns = ["qwen.ai/blog#"]
    default_limit = 10
    default_fetch_detail = True
    source_owner = "alibaba"
    source_brand = "qwen"
    source_scope = "model_family"
    source_channel = "blog_api"
    source_url = listing_url
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "product_update", "research_paper", "developer_tool"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public_api"

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
        response = await self._safe_get(
            client,
            self.listing_url,
            params={"type": "qwen_ai", "language": "en-US"},
        )
        if not response:
            raise RuntimeError(f"Qwen Blog API 请求失败: {self.listing_url}")

        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Qwen Blog API 返回了非 JSON 内容")
        records = payload.get("data", {}).get("articles", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            self.logger.warning("Qwen Blog API 返回了非列表结构。")
            return

        records = sorted(
            (record for record in records if isinstance(record, dict) and not record.get("draft")),
            key=lambda record: self._sort_datetime(str(record.get("date") or record.get("extra", {}).get("date") or "")),
            reverse=True,
        )
        for record in records[:limit]:
            extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
            article_id = str(record.get("path") or record.get("id") or "").strip()
            token_links = str(record.get("tokenLinks") or "").strip()
            source_url = f"https://qwen.ai/blog?id={article_id}" if article_id else token_links or "https://qwen.ai/blog"
            title = self._clean_text(str(record.get("title") or "")) or "未命名 Qwen Blog 条目"
            summary = self._clean_text(str(extra.get("description") or extra.get("introduction") or record.get("description") or record.get("introduction") or ""))[:500]
            tags = [str(tag) for tag in extra.get("tags") or record.get("tags") or []]
            content = summary
            html_content = str(record.get("content") or "")
            raw_data = {
                "listing_url": self.listing_url,
                "url": source_url,
                "tokenLinks": token_links,
                "author": extra.get("author") or record.get("author") or "",
                "readTime": extra.get("readTime") or record.get("readTime") or "",
                "word_count": extra.get("wordCount") or record.get("word_count") or "",
                "tags": tags,
                "listing_source": "qwen_article_retrieval",
            }

            if self._bool_param(kwargs.get("fetch_detail")):
                max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
                if html_content:
                    soup = BeautifulSoup(html_content, "html.parser")
                    for tag in soup(["script", "style", "noscript"]):
                        tag.decompose()
                    text = self._clean_text(soup.get_text(" "))
                    if text:
                        content = text[:max_chars]
                    raw_data.update({
                        "detail_text_length": len(content),
                        "detail_extraction_method": "qwen_article_retrieval_html",
                        "detail_source_url": source_url,
                    })
                # 列表 API 未内联正文时才需二次请求；若该条已入库且有正文则跳过，避免重复抓取。
                elif await self._should_skip_detail_fetch(self._content_id(source_url)):
                    pass
                else:
                    detail_url = token_links or source_url
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
                publish_date=str(record.get("date") or extra.get("date") or datetime.now(timezone.utc).isoformat()),
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage", *tags],
                raw_data=raw_data,
            )
