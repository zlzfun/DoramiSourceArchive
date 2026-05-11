import calendar
import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

import feedparser
import httpx
from bs4 import BeautifulSoup

from fetchers.base import BaseFetcher
from models.content import BaseContent, RssArticleContent


class GenericRssFetcher(BaseFetcher):
    """
    通用 RSS/Atom 抓取器。

    该抓取器通过运行时参数承载具体数据源身份，因此一次执行只处理一个 feed。
    后续 SourceConfig 调度可以把配置中的 source_id/url/name/category 转换为这些参数。
    """
    source_id = "generic_rss"
    content_type = "rss_article"

    name = "通用 RSS/Atom"
    description = "抓取任意 RSS/Atom Feed，适合官方博客、产品更新、论文与社区资讯源。"
    icon = "🛰️"

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "feed_url", "label": "RSS/Atom 地址", "type": "url", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "feed_name", "label": "数据源名称", "type": "text", "default": ""},
            {"field": "category", "label": "业务分类", "type": "text", "default": "official"},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 20},
        ]

    def _entry_id(self, runtime_source_id: str, entry: Any) -> str:
        stable_value = (
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
            or entry.get("title")
            or repr(entry)
        )
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    def _entry_datetime(self, entry: Any, field_name: str) -> str:
        parsed_value = entry.get(f"{field_name}_parsed")
        if parsed_value:
            timestamp = calendar.timegm(parsed_value)
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        raw_value = entry.get(field_name)
        if raw_value:
            return str(raw_value)
        return datetime.now(timezone.utc).isoformat()

    def _entry_tags(self, entry: Any, category: str) -> List[str]:
        tags = []
        for tag in entry.get("tags", []) or []:
            term = tag.get("term") if isinstance(tag, dict) else getattr(tag, "term", "")
            if term:
                tags.append(str(term))
        if category and category not in tags:
            tags.append(category)
        return tags

    def _entry_html(self, entry: Any) -> str:
        contents = entry.get("content") or []
        if contents:
            first_content = contents[0]
            if isinstance(first_content, dict):
                return first_content.get("value", "")
            return getattr(first_content, "value", "")
        return entry.get("summary", "") or entry.get("description", "")

    def _clean_text(self, html_text: str) -> str:
        if not html_text:
            return ""
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    def _media_url(self, entry: Any) -> str:
        media_content = entry.get("media_content") or []
        if media_content:
            first_media = media_content[0]
            if isinstance(first_media, dict):
                return first_media.get("url", "")
        links = entry.get("links") or []
        for link in links:
            link_type = link.get("type", "") if isinstance(link, dict) else getattr(link, "type", "")
            href = link.get("href", "") if isinstance(link, dict) else getattr(link, "href", "")
            if link_type.startswith("image/") and href:
                return href
        return ""

    def _raw_entry(self, entry: Any) -> Dict[str, Any]:
        return {
            "id": entry.get("id", ""),
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "updated": entry.get("updated", ""),
        }

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        feed_url = str(kwargs.get("feed_url", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        feed_name = str(kwargs.get("feed_name", "")).strip()
        category = str(kwargs.get("category", "")).strip()
        limit = int(kwargs.get("limit", 20))

        if not feed_url:
            self.logger.error("RSS/Atom 地址不能为空，放弃抓取。")
            return

        # BaseFetcher 会在 yield 后统一写入 self.source_id，因此这里把实例身份切换到具体配置源。
        self.source_id = runtime_source_id

        response = await self._safe_get(client, feed_url)
        if not response:
            return

        parsed_feed = feedparser.parse(response.content)
        if parsed_feed.bozo:
            self.logger.warning(f"RSS 解析存在异常: {parsed_feed.bozo_exception}")

        resolved_feed_name = feed_name or parsed_feed.feed.get("title", "") or runtime_source_id
        entries = parsed_feed.entries[:max(limit, 0)]

        for entry in entries:
            html_text = self._entry_html(entry)
            content_text = self._clean_text(html_text)
            publish_date = self._entry_datetime(entry, "published")
            updated_date = self._entry_datetime(entry, "updated") if entry.get("updated") else ""
            title = entry.get("title", "未命名 RSS 条目")
            source_url = entry.get("link", feed_url)

            yield RssArticleContent(
                id=self._entry_id(runtime_source_id, entry),
                title=title,
                source_url=source_url,
                publish_date=publish_date,
                content=content_text,
                has_content=bool(content_text),
                feed_name=resolved_feed_name,
                author=entry.get("author", ""),
                tags=self._entry_tags(entry, category),
                guid=entry.get("id", "") or entry.get("guid", ""),
                summary=self._clean_text(entry.get("summary", "")),
                updated_date=updated_date,
                media_url=self._media_url(entry),
                raw_data=self._raw_entry(entry),
            )
