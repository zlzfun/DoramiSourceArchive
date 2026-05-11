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

    def _entry_limit(self, raw_limit: Any, default: int = 20) -> int:
        if raw_limit in (None, ""):
            return default
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"RSS 条数参数无效，使用默认值: {raw_limit}")
            return default

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        feed_url = str(kwargs.get("feed_url", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        feed_name = str(kwargs.get("feed_name", "")).strip()
        category = str(kwargs.get("category", "")).strip()
        limit = self._entry_limit(kwargs.get("limit"), 20)

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
        entries = parsed_feed.entries[:limit]

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


class PresetRssFetcher(GenericRssFetcher):
    """
    预设 RSS/Atom 抓取器基类。

    子类只声明稳定 feed 地址和展示元数据，即可被注册中心自动发现为独立节点。
    """
    source_id = "unknown_source"
    feed_url = ""
    category = "official"
    default_limit = 20

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
        ]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = kwargs.get("limit", self.default_limit)
        if limit in (None, ""):
            limit = self.default_limit

        params = {
            **kwargs,
            "feed_url": self.feed_url,
            "source_id": self.source_id,
            "feed_name": self.name,
            "category": self.category,
            "limit": limit,
        }
        async for item in super()._run(client, **params):
            yield item


class OpenAINewsRssFetcher(PresetRssFetcher):
    source_id = "rss_openai_news"
    name = "OpenAI News"
    description = "OpenAI 官方新闻、产品、研究与工程博客动态。"
    icon = "🧠"
    feed_url = "https://openai.com/news/rss.xml"
    category = "official"


class HuggingFaceBlogRssFetcher(PresetRssFetcher):
    source_id = "rss_huggingface_blog"
    name = "Hugging Face Blog"
    description = "Hugging Face 官方博客、模型、数据集、工具与社区动态。"
    icon = "🤗"
    feed_url = "https://huggingface.co/blog/feed.xml"
    category = "official"


class LangChainBlogRssFetcher(PresetRssFetcher):
    source_id = "rss_langchain_blog"
    name = "LangChain Blog"
    description = "LangChain、LangGraph、LangSmith 官方博客与产品更新。"
    icon = "🦜"
    feed_url = "https://blog.langchain.com/rss/"
    category = "framework"


class GitHubBlogRssFetcher(PresetRssFetcher):
    source_id = "rss_github_blog"
    name = "GitHub Blog"
    description = "GitHub 官方博客，覆盖 Copilot、Actions、开源生态与工程平台更新。"
    icon = "🐙"
    feed_url = "https://github.blog/feed/"
    category = "developer_platform"


class ArxivAiRssFetcher(PresetRssFetcher):
    source_id = "rss_arxiv_cs_ai"
    name = "arXiv cs.AI"
    description = "arXiv 人工智能分类最新论文。"
    icon = "📄"
    feed_url = "https://export.arxiv.org/rss/cs.AI"
    category = "paper"
    default_limit = 30


class ArxivClRssFetcher(PresetRssFetcher):
    source_id = "rss_arxiv_cs_cl"
    name = "arXiv cs.CL"
    description = "arXiv 计算语言学与 NLP 分类最新论文。"
    icon = "📄"
    feed_url = "https://export.arxiv.org/rss/cs.CL"
    category = "paper"
    default_limit = 30


class ArxivLgRssFetcher(PresetRssFetcher):
    source_id = "rss_arxiv_cs_lg"
    name = "arXiv cs.LG"
    description = "arXiv 机器学习分类最新论文。"
    icon = "📄"
    feed_url = "https://export.arxiv.org/rss/cs.LG"
    category = "paper"
    default_limit = 30


class ArxivCvRssFetcher(PresetRssFetcher):
    source_id = "rss_arxiv_cs_cv"
    name = "arXiv cs.CV"
    description = "arXiv 计算机视觉分类最新论文。"
    icon = "📄"
    feed_url = "https://export.arxiv.org/rss/cs.CV"
    category = "paper"
    default_limit = 30


class HackerNewsAiRssFetcher(PresetRssFetcher):
    source_id = "rss_hn_ai"
    name = "Hacker News: AI"
    description = "Hacker News 中与 AI 相关的新讨论。"
    icon = "🟧"
    feed_url = "https://hnrss.org/newest?q=AI"
    category = "community"


class DifyReleasesRssFetcher(PresetRssFetcher):
    source_id = "rss_dify_releases"
    name = "Dify Releases"
    description = "Dify GitHub releases，跟踪开源 Agent 平台版本更新。"
    icon = "🚀"
    feed_url = "https://github.com/langgenius/dify/releases.atom"
    category = "product_update"


class VllmReleasesRssFetcher(PresetRssFetcher):
    source_id = "rss_vllm_releases"
    name = "vLLM Releases"
    description = "vLLM GitHub releases，跟踪推理引擎版本更新。"
    icon = "⚙️"
    feed_url = "https://github.com/vllm-project/vllm/releases.atom"
    category = "product_update"
