import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from fetchers.base import BaseFetcher
from fetchers.impl.article_extractor import extract_article_detail
from fetchers.impl.webpage_fetcher import BaseWebPageListFetcher
from models.content import BaseContent, WebPageArticleContent


def _version_sort_key(version: str) -> tuple:
    """把 '2.1.158' 解析为可排序的数值元组，非数字段按 0 处理。"""
    parts = []
    for part in (version or "").split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class SinglePageDocumentFetcher(BaseFetcher):
    """抓取单个官方文档、changelog 或 release notes 页面作为一条可归档内容。"""

    source_id = "unknown_source"
    content_type = "web_article"
    category = "official_doc"
    page_url = ""
    site_name = ""
    source_section = ""
    default_detail_max_chars = 20000
    default_detail_min_chars = 200

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "detail_max_chars", "label": "正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _positive_int_param(self, raw_value: Any, default: int) -> int:
        if raw_value in (None, ""):
            return default
        try:
            return max(int(raw_value), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"正文长度参数无效，使用默认值: {raw_value}")
            return default

    def _clean_text(self, text: str) -> str:
        return " ".join((text or "").split())

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if not self.page_url:
            raise ValueError("单页文档 URL 不能为空")

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"单页文档请求失败: {self.page_url}")

        detail = await extract_article_detail(
            client,
            self._safe_get,
            str(response.url),
            response.text,
            max_chars,
            self.default_detail_min_chars,
        )
        title = detail.title or self.name
        content = detail.text
        source_url = detail.url or str(response.url)

        yield WebPageArticleContent(
            id=f"{self.source_id}_page",
            title=title,
            source_url=source_url,
            publish_date=datetime.now(timezone.utc).isoformat(),
            content=content,
            has_content=bool(content),
            site_name=self.site_name or self.name,
            source_section=self.source_section,
            summary=content[:500],
            tags=[self.category, *list(self.content_tags or [])],
            raw_data={
                "listing_url": self.page_url,
                "url": source_url,
                "detail_title": detail.title,
                "detail_text_length": len(content),
                "detail_extraction_method": detail.method,
                "listing_source": "single_page_document",
            },
        )


class OpenAiCodexChangelogFetcher(SinglePageDocumentFetcher):
    source_id = "docs_openai_codex_changelog"
    name = "Codex Changelog"
    description = "抓取 OpenAI Codex 官方 Changelog 中的 Codex、CLI、IDE 与自动化更新。"
    icon = "🟢"
    page_url = "https://developers.openai.com/codex/changelog"
    source_url = page_url
    site_name = "OpenAI Codex"
    source_section = "Changelog"
    source_owner = "openai"
    source_brand = "codex"
    source_scope = "developer_tool"
    source_channel = "docs_changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update", "model_release", "api_platform"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    # Codex Changelog 按月份 <h2> 分组，但每条发布是一个独立的
    # <li data-product> 容器：内含 <time>(ISO 日期) + 首个标题(发布名，如
    # "Codex CLI 0.135.0" 或 "Appshots, goal mode, and more 26.519") + 正文。通用单页
    # 抓取会把所有发布糅成一篇长文、丢失逐发布日期；这里按 <li> 切分，每条发布一条记录。
    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 50},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 50
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"{self.name} 条数参数无效，使用默认值: {raw_limit}")
            return 50

    def _content_id(self, anchor: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{anchor}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        container = soup.select_one("main") or soup
        base_url = resolved_url.split("#", 1)[0]

        entries: List[Dict[str, Any]] = []
        seen = set()
        for item in container.find_all("li", attrs={"data-product": True}):
            anchor = self._clean_text(str(item.get("id") or ""))
            if not anchor or anchor in seen:
                continue

            time_node = item.find("time")
            raw_date = self._clean_text(time_node.get_text(" ", strip=True)) if time_node else ""
            publish_date = ""
            if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw_date):
                publish_date = f"{raw_date}T00:00:00+00:00"

            heading = item.find(["h2", "h3", "h4"])
            release_title = self._clean_text(heading.get_text(" ", strip=True)) if heading else ""
            if not release_title:
                continue

            body = self._clean_text(item.get_text(" ", strip=True))
            # 去掉正文开头重复的日期与标题前缀
            if raw_date and body.startswith(raw_date):
                body = body[len(raw_date):].strip()
            if release_title and body.startswith(release_title):
                body = body[len(release_title):].strip()
            body = body[:max_chars]
            if not body:
                continue

            seen.add(anchor)
            entries.append({
                "anchor": anchor,
                "title": f"{self.site_name}: {release_title}",
                "source_url": f"{base_url}#{anchor}",
                "publish_date": publish_date,
                "raw_date": raw_date,
                "content": body,
                "summary": body[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"{self.name} 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"] or "", reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["anchor"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"] or datetime.now(timezone.utc).isoformat(),
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "openai_codex_changelog_updates",
                    "release_anchor": entry["anchor"],
                    "release_date_text": entry["raw_date"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "openai_changelog_list_item",
                },
            )



class ClaudeCodeChangelogFetcher(SinglePageDocumentFetcher):
    source_id = "docs_claude_code_changelog"
    name = "Claude Code Changelog"
    description = "抓取 Claude Code 官方 Changelog 中的版本级工具更新。"
    icon = "🟧"
    page_url = "https://code.claude.com/docs/en/changelog"
    source_url = page_url
    site_name = "Claude Code"
    source_section = "Changelog"
    source_owner = "anthropic"
    source_brand = "claude_code"
    source_scope = "developer_tool"
    source_channel = "changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    # Claude Code Changelog 是 Mintlify <Update> 组件渲染：每个版本是一个独立块，
    # 由 data-component-part="update-label"(版本号) / "update-description"(发布日期，如
    # May 30, 2026) / "update-content"(更新条目) 三段组成。通用单页抓取会把全部版本糅成
    # 一篇长文、丢失版本号粒度与逐版本日期。这里按版本块切分，每个版本发布作为一条记录。
    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 50},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 50
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"Claude Code Changelog 条数参数无效，使用默认值: {raw_limit}")
            return 50

    def _content_id(self, version: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{version}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _parse_release_date(self, raw_date: str) -> str:
        raw_date = self._clean_text(raw_date)
        if not raw_date:
            return ""
        try:
            parsed = datetime.strptime(raw_date, "%B %d, %Y")
        except ValueError:
            return ""
        return parsed.replace(tzinfo=timezone.utc).isoformat()

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        content = soup.select_one("#content-area") or soup
        base_url = resolved_url.split("#", 1)[0]

        entries: List[Dict[str, Any]] = []
        seen_versions = set()
        for label in content.select('[data-component-part="update-label"]'):
            version = self._clean_text(label.get_text(" ", strip=True))
            if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version) or version in seen_versions:
                continue

            # 向上找到同时含描述与正文的版本块容器
            block: Tag | None = label
            for _ in range(6):
                block = block.parent if isinstance(block, Tag) else None
                if block is None:
                    break
                if block.select_one('[data-component-part="update-content"]'):
                    break
            if block is None:
                continue

            desc_node = block.select_one('[data-component-part="update-description"]')
            content_node = block.select_one('[data-component-part="update-content"]')
            if content_node is None:
                continue

            raw_date = desc_node.get_text(" ", strip=True) if desc_node else ""
            publish_date = self._parse_release_date(raw_date)

            bullets = [self._clean_text(node.get_text(" ", strip=True)) for node in content_node.select("li")]
            bullets = [bullet for bullet in bullets if bullet]
            body = "\n\n".join(bullets) if bullets else self._clean_text(content_node.get_text(" ", strip=True))
            body = body[:max_chars]
            if not body:
                continue

            seen_versions.add(version)
            entries.append({
                "version": version,
                "release_date_text": raw_date,
                "title": f"Claude Code {version}",
                "source_url": f"{base_url}#{version}",
                "publish_date": publish_date,
                "content": body,
                "summary": body[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"Claude Code Changelog 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(
            entries,
            key=lambda entry: (entry["publish_date"] or "", _version_sort_key(entry["version"])),
            reverse=True,
        )
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["version"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"] or datetime.now(timezone.utc).isoformat(),
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "claude_code_changelog_updates",
                    "version": entry["version"],
                    "release_date_text": entry["release_date_text"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "mintlify_update_component",
                },
            )


class DevsiteReleaseNotesFetcher(SinglePageDocumentFetcher):
    """抓取 Google devsite 风格的 release notes / changelog 页面，按日期标题切分。

    这类页面（ai.google.dev 等）在 ``devsite-content`` 容器里以 ``<h2>`` 日期标题
    （如 ``May 28, 2026``，带 ``id`` 锚点）分段，标题之后的兄弟节点（``ul``/``p``）
    是该日期的更新内容，直到下一个 ``<h2>``。通用单页抓取会把所有日期糅成一篇长文、
    丢失逐日期粒度；这里按日期标题切分，每个日期段作为一条记录。
    """

    listing_source_label = "devsite_release_notes_updates"
    detail_extraction_method = "devsite_release_notes_heading"

    # 兼容带逗号 "May 28, 2026" 与个别缺逗号 "December 13 2023" 的标题
    _date_heading_re = re.compile(r"^([A-Z][a-z]+)\s+(\d{1,2}),?\s+(20\d{2})$")
    _month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 50},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 50
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"{self.name} 条数参数无效，使用默认值: {raw_limit}")
            return 50

    def _content_id(self, anchor: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{anchor}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _parse_heading_date(self, raw_heading: str) -> str:
        match = self._date_heading_re.match(self._clean_text(raw_heading))
        if not match:
            return ""
        month_name, day, year = match.groups()
        month = self._month_map.get(month_name.lower())
        if not month:
            return ""
        return datetime(int(year), month, int(day), tzinfo=timezone.utc).isoformat()

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        container = soup.select_one("devsite-content") or soup.select_one(".devsite-article-body") or soup
        base_url = resolved_url.split("#", 1)[0]

        entries: List[Dict[str, Any]] = []
        seen = set()
        for heading in container.find_all("h2"):
            heading_text = self._clean_text(heading.get_text(" ", strip=True))
            publish_date = self._parse_heading_date(heading_text)
            if not publish_date:
                continue

            anchor = self._clean_text(str(heading.get("id") or "")) or heading_text
            if anchor in seen:
                continue

            parts: List[str] = []
            sibling = heading.find_next_sibling()
            while sibling is not None and getattr(sibling, "name", None) != "h2":
                if isinstance(sibling, Tag):
                    if sibling.name in {"ul", "ol"}:
                        parts.extend(
                            self._clean_text(li.get_text(" ", strip=True))
                            for li in sibling.find_all("li", recursive=False)
                        )
                    else:
                        text = self._clean_text(sibling.get_text(" ", strip=True))
                        if text:
                            parts.append(text)
                sibling = sibling.find_next_sibling()

            parts = [part for part in parts if part]
            body = "\n\n".join(parts)[:max_chars]
            if not body:
                continue

            seen.add(anchor)
            source_url = f"{base_url}#{anchor}" if heading.get("id") else base_url
            entries.append({
                "anchor": anchor,
                "heading": heading_text,
                "title": f"{self.site_name or self.name}: {heading_text}",
                "source_url": source_url,
                "publish_date": publish_date,
                "content": body,
                "summary": body[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return
        if not self.page_url:
            raise ValueError("release notes 页面 URL 不能为空")

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"{self.name} 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"], reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["anchor"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"],
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name or self.name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": self.listing_source_label,
                    "release_heading": entry["heading"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": self.detail_extraction_method,
                },
            )


class GemmaReleaseNotesFetcher(DevsiteReleaseNotesFetcher):
    source_id = "docs_gemma_release_notes"
    name = "Gemma Release Notes"
    description = "抓取 Gemma 官方 Release Notes 中的开放模型发布与更新。"
    icon = "💎"
    page_url = "https://ai.google.dev/gemma/docs/releases"
    source_url = page_url
    site_name = "Gemma"
    source_section = "Release Notes"
    listing_source_label = "gemma_release_notes_updates"
    source_owner = "google"
    source_brand = "gemma"
    source_scope = "open_model_family"
    source_channel = "docs_release_notes"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "research_paper", "api_platform"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


class XAiDeveloperReleaseNotesFetcher(SinglePageDocumentFetcher):
    source_id = "docs_xai_release_notes"
    name = "xAI Developer Release Notes"
    description = "抓取 xAI 开发者 Release Notes 中的 Grok 模型、API 与产品更新（按发布条目逐条切分）。"
    icon = "𝕏"
    page_url = "https://docs.x.ai/developers/release-notes"
    source_url = page_url
    site_name = "xAI"
    source_section = "Developer Release Notes"
    source_owner = "xai"
    source_brand = "xai_api"
    source_scope = "api_platform"
    source_channel = "docs_changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    # xAI Release Notes 是 Mintlify changelog grid：每条发布是一个
    # ``div.grid grid-cols-[5rem...]`` 卡片，左列(5rem)放日期(``May 29`` / 老条目用缩写
    # ``Dec 31``，均无年份)，右列(``div.min-w-0``)放标题(<h3>)与正文。年份由前一个
    # 月份 <h2>(``May`` / ``December 2025``)给出：带年份的取其年，无年份的视为当前年
    # (跨月时回退上一年)。通用单页抓取会把所有发布糅成一篇、丢失逐条日期；这里按 grid
    # 卡片切分，每条发布一条记录、保留真实发布日期。
    _date_text_re = re.compile(r"^([A-Za-z]+)\s+(\d{1,2})(?:,?\s+(20\d{2}))?$")
    _month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 50},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 50
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"{self.name} 条数参数无效，使用默认值: {raw_limit}")
            return 50

    def _content_id(self, anchor: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{anchor}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _infer_year(self, grid: Tag, month: int, now: datetime) -> int:
        """从最近的前序月份 <h2> 推断年份：带年份取其年，否则按当前日期回退。"""
        for heading in grid.find_all_previous("h2"):
            text = self._clean_text(heading.get_text(" ", strip=True))
            first = text.split()[0].lower() if text else ""
            if first in self._month_map:
                year_match = re.search(r"(20\d{2})", text)
                if year_match:
                    return int(year_match.group(1))
                break
        return now.year - 1 if month > now.month else now.year

    def _parse_grid_date(self, date_text: str, grid: Tag, now: datetime) -> str:
        match = self._date_text_re.match(date_text)
        if not match:
            return ""
        month_name, day, year = match.groups()
        month = self._month_map.get(month_name.lower())
        if not month:
            return ""
        resolved_year = int(year) if year else self._infer_year(grid, month, now)
        try:
            return datetime(resolved_year, month, int(day), tzinfo=timezone.utc).isoformat()
        except ValueError:
            return ""

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        container = soup.select_one("main") or soup
        base_url = resolved_url.split("#", 1)[0]
        now = datetime.now(timezone.utc)

        entries: List[Dict[str, Any]] = []
        seen = set()
        for grid in container.select("div.grid"):
            cols = grid.find_all("div", recursive=False)
            if len(cols) < 2:
                continue
            date_text = self._clean_text(cols[0].get_text(" ", strip=True))
            content_col = cols[1]
            heading = content_col.find(["h2", "h3", "h4"])
            if heading is None:
                continue

            release_title = self._clean_text(heading.get_text(" ", strip=True))
            anchor = self._clean_text(str(heading.get("id") or "")) or release_title
            if not release_title or anchor in seen:
                continue

            body = self._clean_text(content_col.get_text(" ", strip=True))[:max_chars]
            if not body:
                continue

            publish_date = self._parse_grid_date(date_text, grid, now)
            seen.add(anchor)
            entries.append({
                "anchor": anchor,
                "heading": release_title,
                "title": f"{self.site_name}: {release_title}",
                "source_url": f"{base_url}#{anchor}" if heading.get("id") else base_url,
                "publish_date": publish_date,
                "raw_date": date_text,
                "content": body,
                "summary": body[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"{self.name} 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"] or "", reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["anchor"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"] or datetime.now(timezone.utc).isoformat(),
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "xai_release_notes_updates",
                    "release_anchor": entry["anchor"],
                    "release_heading": entry["heading"],
                    "release_date_text": entry["raw_date"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "xai_release_notes_grid",
                },
            )


class DeepSeekApiChangeLogFetcher(DevsiteReleaseNotesFetcher):
    source_id = "docs_deepseek_api_changelog"
    name = "DeepSeek API Change Log"
    description = "抓取 DeepSeek API Change Log 中的模型、API 与平台更新（按发布日期逐条切分）。"
    icon = "🧠"
    page_url = "https://api-docs.deepseek.com/updates/"
    source_url = page_url
    site_name = "DeepSeek"
    source_section = "API Change Log"
    source_owner = "deepseek"
    source_brand = "deepseek"
    source_scope = "api_platform"
    source_channel = "docs_changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"

    # DeepSeek 的 Change Log 是 Docusaurus 文档页：在 <article> 容器里以
    # <h2> 日期标题(``Date: 2026-04-24``，id 形如 ``date-2026-04-24``)分段，标题之后
    # 是 <h3> 模型名(如 ``DeepSeek-V4``)与正文(p/ul)，直到下一个 <h2>。结构与 Google
    # devsite 同构，故复用基类的 _run/_entry_limit/_content_id/参数表，仅覆写日期解析
    # (Date: 前缀 + ISO 格式)与切分(容器换成 <article>，标题用段内 <h3> 模型名)。
    listing_source_label = "deepseek_api_changelog_updates"
    detail_extraction_method = "deepseek_api_changelog_heading"

    _deepseek_date_re = re.compile(r"^Date:\s*(20\d{2})-(\d{2})-(\d{2})$")

    def _clean_text(self, text: str) -> str:
        # Docusaurus 在标题/锚点里塞了零宽空格(​)，会让 "Date: 2026-04-24" 末尾
        # 带上不可见字符、破坏日期正则的 $ 锚定，也污染标题里的模型名；先剥掉再清洗。
        stripped = (text or "").replace("​", "").replace("﻿", "")
        return super()._clean_text(stripped)

    def _parse_heading_date(self, raw_heading: str) -> str:
        match = self._deepseek_date_re.match(self._clean_text(raw_heading))
        if not match:
            return ""
        year, month, day = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return ""

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        container = soup.select_one("article") or soup.select_one("main") or soup
        base_url = resolved_url.split("#", 1)[0]

        entries: List[Dict[str, Any]] = []
        seen = set()
        for heading in container.find_all("h2"):
            heading_text = self._clean_text(heading.get_text(" ", strip=True))
            publish_date = self._parse_heading_date(heading_text)
            if not publish_date:
                continue

            anchor = self._clean_text(str(heading.get("id") or "")) or heading_text
            if anchor in seen:
                continue

            model_names: List[str] = []
            parts: List[str] = []
            sibling = heading.find_next_sibling()
            while sibling is not None and getattr(sibling, "name", None) != "h2":
                if isinstance(sibling, Tag):
                    if sibling.name in {"h3", "h4"}:
                        model_name = self._clean_text(sibling.get_text(" ", strip=True))
                        if model_name:
                            model_names.append(model_name)
                            parts.append(model_name)
                    elif sibling.name in {"ul", "ol"}:
                        parts.extend(
                            self._clean_text(li.get_text(" ", strip=True))
                            for li in sibling.find_all("li", recursive=False)
                        )
                    else:
                        text = self._clean_text(sibling.get_text(" ", strip=True))
                        if text:
                            parts.append(text)
                sibling = sibling.find_next_sibling()

            body = "\n\n".join(part for part in parts if part)[:max_chars]
            if not body:
                continue

            # 标题用段内 <h3> 模型名（比裸日期更有信息量）；多模型同日则并列，缺失则回退日期文本。
            headline = ", ".join(model_names) if model_names else heading_text
            seen.add(anchor)
            entries.append({
                "anchor": anchor,
                "heading": heading_text,
                "title": f"{self.site_name} API: {headline}",
                "source_url": f"{base_url}#{anchor}" if heading.get("id") else base_url,
                "publish_date": publish_date,
                "content": body,
                "summary": body[:500],
            })
        return entries


class ZaiNewReleasedFetcher(SinglePageDocumentFetcher):
    source_id = "docs_zai_new_released"
    name = "Z.ai New Released"
    description = "抓取 Z.ai New Released 页面中的 GLM 模型、API、Agent 与产品更新。"
    icon = "🧩"
    page_url = "https://docs.z.ai/release-notes/new-released"
    source_url = page_url
    site_name = "Z.ai"
    source_section = "New Released"
    source_owner = "zai"
    source_brand = "glm"
    source_scope = "model_family"
    source_channel = "docs_release_notes"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 50},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 50
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"Z.ai 发布条数参数无效，使用默认值: {raw_limit}")
            return 50

    def _content_id(self, release_date: str, model_name: str) -> str:
        digest = hashlib.sha1(f"{release_date}:{model_name}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        entries: List[Dict[str, Any]] = []
        for item in soup.select("div.update.update-container"):
            release_date = self._clean_text(str(item.get("id") or ""))
            if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", release_date):
                continue

            text_parts = [
                self._clean_text(part)
                for part in item.stripped_strings
                if self._clean_text(part) and self._clean_text(part) != "\u200b"
            ]
            if len(text_parts) < 2:
                continue
            model_name = text_parts[1]

            bullets = [self._clean_text(node.get_text(" ", strip=True)) for node in item.select("li")]
            bullets = [bullet for bullet in bullets if bullet]
            content = "\n\n".join(bullets) if bullets else "\n\n".join(text_parts[2:])
            content = content[:max_chars]
            if not content:
                continue

            source_url = f"{resolved_url.split('#', 1)[0]}#{release_date}"
            entries.append({
                "release_date": release_date,
                "model_name": model_name,
                "title": f"Z.ai New Released: {model_name}",
                "source_url": source_url,
                "publish_date": f"{release_date}T00:00:00+00:00",
                "content": content,
                "summary": content[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"Z.ai New Released 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"], reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["release_date"], entry["model_name"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"],
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "zai_new_released_updates",
                    "release_date": entry["release_date"],
                    "model_name": entry["model_name"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "mintlify_update_container",
                },
            )


class ByteDanceSeedResearchFetcher(SinglePageDocumentFetcher):
    source_id = "web_bytedance_seed_research"
    name = "ByteDance Seed Research"
    description = "抓取 ByteDance Seed Research 页面 Publications 中的研究论文与技术报告（逐篇切分）。"
    icon = "🌱"
    page_url = "https://seed.bytedance.com/en/research"
    source_url = page_url
    site_name = "ByteDance Seed"
    source_section = "Research"
    source_owner = "bytedance_seed"
    source_brand = "seed"
    source_scope = "research_lab"
    source_channel = "research_index"
    provenance_tier = "tier0_primary"
    content_tags = ["research_paper", "model_release"]
    signal_strength = "medium_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    # Seed Research 页的 Publications 区把每篇论文渲染为一个 ``div.group.relative`` 卡片：
    # 内含日期 div(``Apr 22, 2026``)、标题 div(其直属文本即标题)、以及 ``div[class*=markdown]``
    # 摘要(响应式重复多份，取首份即可)。通用单页抓取会把所有论文标题糅成一篇无日期长文；
    # 这里按卡片逐篇切分，每篇一条记录、保留发布日期与摘要正文。页面为 JS 渲染但 SSR 已含
    # 这些卡片，故纯 httpx 即可解析；静态 HTML 无逐篇链接，source_url 回退到列表页。
    _pub_date_re = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}),\s+(20\d{2})$")
    _month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 30},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 30
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"{self.name} 条数参数无效，使用默认值: {raw_limit}")
            return 30

    def _content_id(self, key: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{key}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _parse_pub_date(self, raw_date: str) -> str:
        match = self._pub_date_re.match(self._clean_text(raw_date))
        if not match:
            return ""
        month = self._month_map.get(match.group(1).lower())
        if not month:
            return ""
        try:
            return datetime(int(match.group(3)), month, int(match.group(2)), tzinfo=timezone.utc).isoformat()
        except ValueError:
            return ""

    def _release_entries(self, html_text: str, resolved_url: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        base_url = resolved_url.split("#", 1)[0]

        entries: List[Dict[str, Any]] = []
        seen = set()
        for card in soup.select("div.group.relative"):
            date_node = card.find(string=self._pub_date_re)
            if not date_node:
                continue
            publish_date = self._parse_pub_date(date_node)

            title_div = date_node.parent.find_next_sibling()
            title = ""
            if title_div is not None:
                title = self._clean_text("".join(title_div.find_all(string=True, recursive=False)))
            if not title or title in seen:
                continue

            markdown = card.select_one('[class*="markdown"]')
            abstract = self._clean_text(markdown.get_text(" ", strip=True)) if markdown else ""
            body = (f"{title}\n\n{abstract}" if abstract else title)[:max_chars]

            seen.add(title)
            entries.append({
                "key": title,
                "heading": title,
                "title": f"{self.site_name}: {title}",
                "source_url": base_url,
                "publish_date": publish_date,
                "raw_date": self._clean_text(date_node),
                "content": body,
                "summary": (abstract or title)[:500],
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"{self.name} 页面请求失败: {self.page_url}")

        entries = self._release_entries(response.text, str(response.url), max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"] or "", reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["key"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"] or datetime.now(timezone.utc).isoformat(),
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "bytedance_seed_research_publications",
                    "release_heading": entry["heading"],
                    "release_date_text": entry["raw_date"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "bytedance_seed_research_card",
                },
            )


class HuggingFaceDailyPapersFetcher(SinglePageDocumentFetcher):
    source_id = "web_huggingface_daily_papers"
    name = "Hugging Face Daily Papers"
    description = "抓取 Hugging Face Daily Papers 中的社区热门论文（逐篇切分，含摘要与发布日期）。"
    icon = "🤗"
    page_url = "https://huggingface.co/papers"
    source_url = page_url
    site_name = "Hugging Face"
    source_section = "Daily Papers"
    source_owner = "huggingface"
    source_brand = "daily_papers"
    source_scope = "research_community"
    source_channel = "paper_ranking"
    provenance_tier = "tier1_curated"
    content_tags = ["research_paper", "model_release", "developer_tool"]
    signal_strength = "medium_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    # Daily Papers 页把当天数十篇论文渲染为一组 <article> 卡片；通用单页抓取会把它们糅成
    # 一篇无逐篇日期的长文。页面用 hydration 数据 <div data-target="DailyPapers" data-props="…">
    # 内嵌完整 JSON（dailyPapers 数组，每篇含 paper.id/title/summary(摘要)/publishedAt/upvotes
    # 等）。这里解析该 JSON 逐篇切分，每篇一条记录、保留 arxiv 发布日期与摘要正文，无需逐篇
    # 再请求详情页。
    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 40},
            {"field": "detail_max_chars", "label": "单条正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any) -> int:
        if raw_limit in (None, ""):
            return 40
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"{self.name} 条数参数无效，使用默认值: {raw_limit}")
            return 40

    def _content_id(self, arxiv_id: str) -> str:
        digest = hashlib.sha1(f"{self.source_id}:{arxiv_id}".encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}_{digest}"

    def _normalize_dt(self, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            return ""

    def _paper_entries(self, html_text: str, max_chars: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        node = None
        for el in soup.find_all(attrs={"data-props": True}):
            if el.get("data-target") == "DailyPapers":
                node = el
                break
        if node is None:
            return []
        try:
            data = json.loads(node.get("data-props") or "{}")
        except (ValueError, TypeError):
            self.logger.warning(f"{self.name} 解析 DailyPapers JSON 失败")
            return []

        entries: List[Dict[str, Any]] = []
        seen = set()
        for item in data.get("dailyPapers", []):
            paper = item.get("paper", {}) if isinstance(item, dict) else {}
            arxiv_id = self._clean_text(str(paper.get("id") or ""))
            if not arxiv_id or arxiv_id in seen:
                continue
            title = self._clean_text(item.get("title") or paper.get("title") or "")
            if not title:
                continue
            abstract = self._clean_text(paper.get("summary") or item.get("summary") or "")
            ai_keywords = paper.get("ai_keywords") or []
            publish_date = self._normalize_dt(
                paper.get("publishedAt") or item.get("publishedAt") or paper.get("submittedOnDailyAt")
            )
            body = (abstract or title)[:max_chars]

            seen.add(arxiv_id)
            entries.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "source_url": f"https://huggingface.co/papers/{arxiv_id}",
                "publish_date": publish_date,
                "content": body,
                "summary": (abstract or title)[:500],
                "upvotes": int(paper.get("upvotes") or 0),
                "num_authors": len(paper.get("authors") or []),
                "ai_keywords": ai_keywords if isinstance(ai_keywords, list) else [],
                "github_repo": paper.get("githubRepo") or "",
            })
        return entries

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            raise RuntimeError(f"{self.name} 页面请求失败: {self.page_url}")

        entries = self._paper_entries(response.text, max_chars)
        entries = sorted(entries, key=lambda entry: entry["publish_date"] or "", reverse=True)
        for entry in entries[:limit]:
            yield WebPageArticleContent(
                id=self._content_id(entry["arxiv_id"]),
                title=entry["title"],
                source_url=entry["source_url"],
                publish_date=entry["publish_date"] or datetime.now(timezone.utc).isoformat(),
                content=entry["content"],
                has_content=bool(entry["content"]),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=entry["summary"],
                tags=[self.category, *list(self.content_tags or [])],
                raw_data={
                    "listing_url": self.page_url,
                    "url": entry["source_url"],
                    "listing_source": "huggingface_daily_papers",
                    "arxiv_id": entry["arxiv_id"],
                    "upvotes": entry["upvotes"],
                    "num_authors": entry["num_authors"],
                    "ai_keywords": entry["ai_keywords"],
                    "github_repo": entry["github_repo"],
                    "detail_text_length": len(entry["content"]),
                    "detail_extraction_method": "huggingface_daily_papers_json",
                },
            )


class CursorChangelogWebFetcher(BaseWebPageListFetcher):
    source_id = "web_cursor_changelog"
    name = "Cursor Changelog"
    description = "抓取 Cursor 官方 Changelog 中的 AI 编程、Agent 与 IDE 产品更新。"
    icon = "⌨️"
    listing_url = "https://cursor.com/changelog"
    source_url = listing_url
    site_name = "Cursor"
    source_section = "Changelog"
    article_url_patterns = ["cursor.com/changelog/"]
    # 排除会匹配进来的导航/页脚链接（这些 /changelog/<nav> 详情页会 404、正文为空）。
    exclude_url_patterns = [
        "cursor.com/changelog#",
        "cursor.com/changelog/enterprise",
        "cursor.com/changelog/pricing",
        "cursor.com/changelog/community",
        "cursor.com/changelog/students",
    ]
    default_fetch_detail = True
    # 兜底：即便有新的导航链接漏过 exclude，也丢弃正文为空的垃圾条目。
    drop_empty_content = True
    # Cursor 列表页每页仅约 5 条，更早的在 /changelog/page/N 翻页里；逐页累积以凑够 limit。
    max_listing_pages = 8
    source_owner = "cursor"
    source_brand = "cursor"
    source_scope = "developer_tool"
    source_channel = "changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"

    _page_link_re = re.compile(r"/changelog/page/(\d+)/?$")

    def _next_listing_page_url(self, soup, current_url):
        # 当前页码（/changelog 视为第 1 页），下一页取页面里编号最小且大于当前页的 page 链接。
        current_match = self._page_link_re.search(current_url)
        current_page = int(current_match.group(1)) if current_match else 1
        next_candidates = []
        for link in soup.find_all("a", href=True):
            href = urljoin(current_url, str(link["href"]))
            match = self._page_link_re.search(href)
            if match and int(match.group(1)) > current_page:
                next_candidates.append((int(match.group(1)), href.split("#", 1)[0]))
        if not next_candidates:
            return None
        return min(next_candidates, key=lambda pair: pair[0])[1]


class QbitAiWebsiteFetcher(BaseWebPageListFetcher):
    source_id = "web_qbitai"
    name = "量子位 Website"
    description = "抓取量子位官网中的中文 AI 新闻、模型、产品和产业动态。"
    icon = "📰"
    listing_url = "https://www.qbitai.com/category/%E8%B5%84%E8%AE%AF"
    source_url = listing_url
    site_name = "量子位"
    source_section = "资讯"
    article_url_patterns = ["qbitai.com/"]
    exclude_url_patterns = ["qbitai.com/#", "qbitai.com/about", "qbitai.com/contact"]
    default_fetch_detail = True
    source_owner = "qbitai"
    source_brand = "量子位"
    source_scope = "ai_media"
    source_channel = "website"
    provenance_tier = "tier1_curated"
    content_tags = ["market_news", "model_release", "product_update", "research_paper", "opinion"]
    signal_strength = "medium_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"

    def _matches_article_url(self, url: str) -> bool:
        if not super()._matches_article_url(url):
            return False
        path = urlparse(url).path.strip("/")
        return bool(re.fullmatch(r"20\d{2}/\d{2}/\d+\.html", path))

    def _list_items(self, soup: BeautifulSoup) -> List[Tag]:
        return soup.select(".article_list > .picture_text")

    def _parse_listing_datetime(self, raw_value: str, now: datetime | None = None) -> str:
        raw_value = self._clean_text(raw_value)
        if not raw_value:
            return ""

        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        iso_date = self._extract_datetime_or_empty(raw_value)
        if iso_date:
            return iso_date

        hour_match = re.search(r"(\d+)\s*小时前", raw_value)
        if hour_match:
            return (now - timedelta(hours=int(hour_match.group(1)))).astimezone(timezone.utc).isoformat()

        minute_match = re.search(r"(\d+)\s*分钟前", raw_value)
        if minute_match:
            return (now - timedelta(minutes=int(minute_match.group(1)))).astimezone(timezone.utc).isoformat()

        time_match = re.search(r"(?:(昨天|前天)\s*)?(\d{1,2}):(\d{2})", raw_value)
        if time_match:
            relative_day, hour, minute = time_match.groups()
            days_back = 1 if relative_day == "昨天" else 2 if relative_day == "前天" else 0
            base_date = (now - timedelta(days=days_back)).date()
            return datetime(
                base_date.year,
                base_date.month,
                base_date.day,
                int(hour),
                int(minute),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ).astimezone(timezone.utc).isoformat()

        return ""

    def _extract_qbitai_detail(self, html_text: str, max_chars: int) -> Dict[str, str]:
        """限定到量子位文章正文容器 ``div.content > div.article``。

        通用提取器会落到 ``article``/``main`` 选择器，把同级的 logo 图(``.wx_img`` 里有一段
        非法的 ``< img …>`` 文本，因 ``<`` 后带空格不会被当标签剥除而泄漏)、标签、作者框
        以及页面的相关阅读/热门文章/页脚一并圈进来。正文真正只在 ``.article`` 里，故精确
        限定并只取段落级文本。"""
        soup = BeautifulSoup(html_text, "html.parser")
        title = self._detail_title(soup)
        body = soup.select_one(".content .article") or soup.select_one("div.article")
        if not body:
            return {"title": title, "text": "", "method": ""}

        for selector in ["script", "style", "noscript", "button", ".wx_img", ".share_pc", ".tags", ".person_box", ".xiangguan"]:
            for node in body.select(selector):
                node.decompose()

        text = "\n\n".join(
            self._clean_text(node.get_text(" ", strip=True))
            for node in body.find_all(["p", "blockquote", "li", "h2", "h3"], recursive=True)
        )
        if not text:
            text = self._clean_text(body.get_text(" ", strip=True))
        return {"title": title, "text": text[:max_chars], "method": "qbitai_article_body"}

    async def _detail_for_url(self, client: httpx.AsyncClient, url: str, max_chars: int) -> Dict[str, str]:
        response = await self._safe_get(client, url)
        if not response:
            return {"title": "", "text": "", "method": "", "url": ""}
        detail = self._extract_qbitai_detail(response.text, max_chars)
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
            raise RuntimeError(f"量子位首页请求失败: {self.listing_url}")

        soup = BeautifulSoup(response.text, "html.parser")
        seen_urls: set[str] = set()
        entries: List[Dict[str, Any]] = []
        reference_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        for order, item in enumerate(self._list_items(soup)):
            title_link = item.select_one(".text_box h4 a[href]")
            if not title_link:
                continue
            url = self._normalize_article_url(urljoin(str(response.url), str(title_link["href"])))
            if not self._matches_article_url(url) or url in seen_urls:
                continue
            seen_urls.add(url)

            title = self._clean_text(title_link.get_text(" ", strip=True)) or "未命名量子位条目"
            summary_node = item.select_one(".text_box > p")
            summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")[:500]
            time_node = item.select_one(".info .time")
            raw_publish_date = self._clean_text(time_node.get_text(" ", strip=True) if time_node else "")
            publish_date = self._parse_listing_datetime(raw_publish_date, reference_now) or self._extract_datetime(f"{title} {summary}")
            author_node = item.select_one(".info .author")
            author = self._clean_text(author_node.get_text(" ", strip=True) if author_node else "")
            tags = [self._clean_text(tag.get_text(" ", strip=True)) for tag in item.select(".tags_s a")]
            tags = [tag for tag in tags if tag]
            image_node = item.select_one(".picture img")
            media_url = ""
            if image_node:
                media_url = str(image_node.get("data-original") or image_node.get("src") or "")

            entries.append({
                "url": url,
                "title": title,
                "summary": summary,
                "publish_date": publish_date,
                "raw_publish_date": raw_publish_date,
                "author": author,
                "tags": tags,
                "media_url": media_url,
                "order": order,
            })

        entries = sorted(
            entries,
            key=lambda entry: (self._sort_datetime(entry["publish_date"]), -entry["order"]),
            reverse=True,
        )

        for entry in entries[:limit]:
            url = entry["url"]
            title = entry["title"]
            summary = entry["summary"]
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
                title=title,
                source_url=url,
                publish_date=entry["publish_date"],
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage", *entry["tags"]],
                raw_data={
                    "listing_url": self.listing_url,
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "listing_source": "qbitai_main_article_list",
                    "listing_publish_date": entry["raw_publish_date"],
                    "author": entry["author"],
                    "tags": entry["tags"],
                    "media_url": entry["media_url"],
                    "detail_fetched": detail_fetched,
                    "detail_title": detail["title"],
                    "detail_text_length": len(detail["text"]),
                    "detail_extraction_method": detail.get("method", ""),
                    "detail_source_url": detail.get("url", ""),
                },
            )


class AieraWebsiteFetcher(BaseWebPageListFetcher):
    source_id = "web_aiera"
    name = "新智元 Website"
    description = "抓取新智元官网公开文章列表中的中文 AI 模型、产品、产业和研究资讯。"
    icon = "📰"
    listing_url = "https://aiera.com.cn/"
    source_url = listing_url
    site_name = "新智元"
    source_section = "Website"
    article_url_patterns = ["aiera.com.cn/20"]
    exclude_url_patterns = [
        "aiera.com.cn/feed",
        "aiera.com.cn/comments/feed",
        "aiera.com.cn/wp-",
        "aiera.com.cn/search/",
        "aiera.com.cn/category/",
        "aiera.com.cn/tag/",
        "aiera.com.cn/author/",
    ]
    default_fetch_detail = True
    source_owner = "aiera"
    source_brand = "新智元"
    source_scope = "ai_media"
    source_channel = "website"
    provenance_tier = "tier1_curated"
    content_tags = ["market_news", "model_release", "product_update", "research_paper", "opinion"]
    signal_strength = "medium_signal"
    noise_risk = "high_noise"
    fetch_reliability = "stable_public_website"

    def _matches_article_url(self, url: str) -> bool:
        if not super()._matches_article_url(url):
            return False
        path = urlparse(url).path.strip("/")
        return bool(re.fullmatch(r"20\d{2}/\d{2}/\d{2}/.+", path))

    def _list_items(self, soup: BeautifulSoup) -> List[Tag]:
        items = soup.select("main#main .entries > article.entry-card")
        if items:
            return items
        return soup.select("main#main article.entry-card")

    def _parse_listing_datetime(self, raw_value: str) -> str:
        raw_value = self._clean_text(raw_value)
        if not raw_value:
            return ""

        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed.isoformat()
        except ValueError:
            pass

        chinese_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", raw_value)
        if chinese_match:
            year, month, day = (int(part) for part in chinese_match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()

        return self._extract_datetime_or_empty(raw_value)

    def _date_from_article_url(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        match = re.match(r"(20\d{2})/(\d{1,2})/(\d{1,2})/", path)
        if not match:
            return ""
        year, month, day = (int(part) for part in match.groups())
        return datetime(year, month, day, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()

    def _next_page_url(self, soup: BeautifulSoup, current_url: str) -> str:
        next_link = soup.select_one("nav.ct-pagination a.next[rel='next'][href], a.next.page-numbers[href]")
        if not next_link:
            return ""
        return urljoin(current_url, str(next_link["href"]))

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        seen_urls: set[str] = set()
        seen_pages: set[str] = set()
        entries: List[Dict[str, Any]] = []
        page_url = self.listing_url
        page_index = 0
        max_pages = 20
        while page_url and page_url not in seen_pages and len(entries) < limit and page_index < max_pages:
            seen_pages.add(page_url)
            response = await self._safe_get(client, page_url)
            if not response:
                if page_index == 0:
                    raise RuntimeError(f"新智元首页请求失败: {self.listing_url}")
                break

            resolved_page_url = str(response.url)
            soup = BeautifulSoup(response.text, "html.parser")
            page_order_base = page_index * 1000
            for order, item in enumerate(self._list_items(soup)):
                title_link = item.select_one(".entry-title a[href], h1 a[href], h2 a[href], h3 a[href]")
                if not title_link:
                    continue

                url = self._normalize_article_url(urljoin(resolved_page_url, str(title_link["href"])))
                if not self._matches_article_url(url) or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = self._clean_text(title_link.get_text(" ", strip=True)) or "未命名新智元条目"
                time_node = item.select_one("time[datetime], time")
                raw_publish_date = ""
                if time_node:
                    raw_publish_date = self._clean_text(
                        str(time_node.get("datetime") or "") or time_node.get_text(" ", strip=True)
                    )
                display_publish_date = self._clean_text(time_node.get_text(" ", strip=True) if time_node else "")
                publish_date = (
                    self._parse_listing_datetime(raw_publish_date)
                    or self._parse_listing_datetime(display_publish_date)
                    or self._date_from_article_url(url)
                    or self._extract_datetime(title)
                )

                image_node = item.select_one("img")
                media_url = ""
                if image_node:
                    media_url = str(image_node.get("src") or "")
                    if media_url:
                        media_url = urljoin(resolved_page_url, media_url)

                summary = self._summary_from_container(title, item)
                summary = re.sub(r"^发布于\s*[\d年月日:：+\-T ]+", "", summary).strip()
                summary = re.sub(r"点我查看.*$", "", summary).strip()[:500]

                entries.append({
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "publish_date": publish_date,
                    "raw_publish_date": display_publish_date or raw_publish_date,
                    "listing_datetime": raw_publish_date,
                    "media_url": media_url,
                    "order": page_order_base + order,
                    "listing_page_url": resolved_page_url,
                })
                if len(entries) >= limit:
                    break

            page_index += 1
            page_url = self._next_page_url(soup, resolved_page_url)

        entries = sorted(
            entries,
            key=lambda entry: (self._sort_datetime(entry["publish_date"]), -entry["order"]),
            reverse=True,
        )

        for entry in entries[:limit]:
            url = entry["url"]
            title = entry["title"]
            summary = entry["summary"]
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
                title=title,
                source_url=url,
                publish_date=entry["publish_date"],
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage", *self.content_tags],
                raw_data={
                    "listing_url": entry["listing_page_url"],
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "listing_source": "aiera_main_article_list",
                    "listing_publish_date": entry["raw_publish_date"],
                    "listing_datetime": entry["listing_datetime"],
                    "media_url": entry["media_url"],
                    "detail_fetched": detail_fetched,
                    "detail_title": detail["title"],
                    "detail_text_length": len(detail["text"]),
                    "detail_extraction_method": detail.get("method", ""),
                    "detail_source_url": detail.get("url", ""),
                },
            )
