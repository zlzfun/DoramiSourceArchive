import calendar
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, AsyncGenerator, Dict, List

import feedparser
import httpx

from fetchers.impl.article_extractor import clean_text, extract_article_detail, extract_detail_from_html
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
    category = "advanced"

    name = "通用 RSS/Atom"
    description = "抓取任意 RSS/Atom Feed，适合官方博客、产品更新、论文与社区资讯源。"
    icon = "🛰️"
    default_fetch_detail_if_missing = True
    default_detail_min_chars = 200
    default_detail_max_chars = 12000

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "feed_url", "label": "RSS/Atom 地址", "type": "url", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "feed_name", "label": "数据源名称", "type": "text", "default": ""},
            {"field": "category", "label": "业务分类", "type": "text", "default": "official"},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 20},
            {"field": "fetch_detail_if_missing", "label": "短正文时抓取详情页", "type": "boolean", "default": cls.default_fetch_detail_if_missing},
            {"field": "detail_min_chars", "label": "触发详情抓取的正文长度", "type": "number", "default": cls.default_detail_min_chars},
            {"field": "detail_max_chars", "label": "详情页正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
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

    def _datetime_from_entry_field(self, entry: Any, field_name: str) -> datetime | None:
        parsed_key = f"{field_name}_parsed"
        parsed_value = entry[parsed_key] if parsed_key in entry else None
        if parsed_value:
            timestamp = calendar.timegm(parsed_value)
            return datetime.fromtimestamp(timestamp, timezone.utc)
        raw_value = entry[field_name] if field_name in entry else None
        if raw_value:
            try:
                value = parsedate_to_datetime(str(raw_value))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
            except (TypeError, ValueError, IndexError, OverflowError):
                return None
        return None

    def _entry_datetime(self, entry: Any, field_name: str) -> str:
        fallback_fields = ["updated", "created"] if field_name == "published" else []
        for name in [field_name, *fallback_fields]:
            value = self._datetime_from_entry_field(entry, name)
            if value:
                return value.isoformat()
        raw_value = entry.get(field_name)
        if raw_value:
            return str(raw_value)
        return datetime.now(timezone.utc).isoformat()

    def _entry_sort_timestamp(self, entry: Any) -> int | None:
        for field_name in ["published", "updated", "created"]:
            value = self._datetime_from_entry_field(entry, field_name)
            if value:
                return int(value.timestamp())
        return None

    def _sort_entries_newest_first(self, entries: List[Any]) -> List[Any]:
        indexed_entries = list(enumerate(entries))
        sorted_entries = sorted(
            indexed_entries,
            key=lambda pair: (
                (timestamp := self._entry_sort_timestamp(pair[1])) is not None,
                timestamp or 0,
                -pair[0],
            ),
            reverse=True,
        )
        return [entry for _, entry in sorted_entries]

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
        return clean_text(html_text)

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
            "comments": entry.get("comments", ""),
            "published": entry["published"] if "published" in entry else "",
            "updated": entry["updated"] if "updated" in entry else "",
        }

    def _finalize_content_text(self, entry: Any, content_text: str, detail_text: str) -> str:
        """yield 前对正文做最终裁决的可覆盖钩子。默认原样返回。

        子类（如 Hacker News 这类「链接聚合 / 讨论」源）可借此把无正文价值的
        条目降级为纯发现条目。``detail_text`` 是本轮详情抓取拿到的正文（未抓或失败
        时为空），便于子类区分「确实抓到外链正文」与「只有 RSS 模板 summary」。
        """
        return content_text

    def _entry_limit(self, raw_limit: Any, default: int = 20) -> int:
        if raw_limit in (None, ""):
            return default
        try:
            return max(int(raw_limit), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"RSS 条数参数无效，使用默认值: {raw_limit}")
            return default

    def _bool_param(self, raw_value: Any, default: bool) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value in (None, ""):
            return default
        return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _positive_int_param(self, raw_value: Any, default: int) -> int:
        if raw_value in (None, ""):
            return default
        try:
            return max(int(raw_value), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"RSS 正文长度参数无效，使用默认值: {raw_value}")
            return default

    def _extract_detail(self, html: str, max_chars: int) -> Dict[str, str]:
        detail = extract_detail_from_html(html, max_chars, self.default_detail_min_chars)
        return {"title": detail.title, "text": detail.text, "method": detail.method}

    async def _detail_for_url(
        self,
        client: httpx.AsyncClient,
        url: str,
        max_chars: int,
        detail_min_chars: int,
    ) -> Dict[str, str]:
        response = await self._safe_get(client, url)
        if not response:
            return {"title": "", "text": "", "method": "", "url": ""}
        detail = await extract_article_detail(
            client,
            self._safe_get,
            str(response.url),
            response.text,
            max_chars,
            detail_min_chars,
        )
        return {"title": detail.title, "text": detail.text, "method": detail.method, "url": detail.url}

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        feed_url = str(kwargs.get("feed_url", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        feed_name = str(kwargs.get("feed_name", "")).strip()
        category = str(kwargs.get("category", "")).strip()
        limit = self._entry_limit(kwargs.get("limit"), 20)
        fetch_detail_if_missing = self._bool_param(
            kwargs.get("fetch_detail_if_missing"),
            self.default_fetch_detail_if_missing,
        )
        detail_min_chars = self._positive_int_param(kwargs.get("detail_min_chars"), self.default_detail_min_chars)
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)

        if not feed_url:
            raise ValueError("RSS/Atom 地址不能为空")

        # BaseFetcher 会在 yield 后统一写入 self.source_id，因此这里把实例身份切换到具体配置源。
        self.source_id = runtime_source_id

        response = await self._safe_get(client, feed_url)
        if not response:
            raise RuntimeError(f"RSS/Atom 请求失败: {feed_url}")

        parsed_feed = feedparser.parse(response.content)
        if parsed_feed.bozo:
            self.logger.warning(f"RSS 解析存在异常: {parsed_feed.bozo_exception}")

        resolved_feed_name = feed_name or parsed_feed.feed.get("title", "") or runtime_source_id
        entries = self._sort_entries_newest_first(parsed_feed.entries)[:limit]

        # 去重预检：entry 的稳定 id 在抓正文前即可算出，先批量查库。已入库且已有正文的
        # 条目无需再访问正文 URL（避免对重复条目重复请求，这是抓取慢的主因）；仅库中
        # 缺席（全新）或已存在但仍空正文（需回填）的条目才触发详情抓取。
        existing_flags = await self._lookup_existing_content_flags(
            self._entry_id(runtime_source_id, entry) for entry in entries
        )

        for entry in entries:
            entry_id = self._entry_id(runtime_source_id, entry)
            html_text = self._entry_html(entry)
            content_text = self._clean_text(html_text)
            publish_date = self._entry_datetime(entry, "published")
            updated_date = self._entry_datetime(entry, "updated") if "updated" in entry or "updated_parsed" in entry else ""
            title = entry.get("title", "未命名 RSS 条目")
            source_url = entry.get("link", feed_url)
            detail = {"title": "", "text": ""}

            # 已入库且已有正文 → 跳过正文请求；缺席或空正文 → 允许抓取（回填）。
            already_has_content = existing_flags.get(entry_id, False)

            if (
                fetch_detail_if_missing
                and not already_has_content
                and source_url
                and source_url != feed_url
                and len(content_text) < detail_min_chars
            ):
                detail = await self._detail_for_url(client, source_url, detail_max_chars, detail_min_chars)
                if detail["text"] and len(detail["text"]) > len(content_text):
                    content_text = detail["text"]

            content_text = self._finalize_content_text(entry, content_text, detail.get("text", ""))

            raw_data = self._raw_entry(entry)
            raw_data.update({
                "detail_fetched": bool(detail["text"]),
                "detail_title": detail["title"],
                "detail_text_length": len(detail["text"]),
                "detail_extraction_method": detail.get("method", ""),
                "detail_source_url": detail.get("url", ""),
            })
            yield RssArticleContent(
                id=entry_id,
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
                raw_data=raw_data,
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
            {"field": "fetch_detail_if_missing", "label": "短正文时抓取详情页", "type": "boolean", "default": cls.default_fetch_detail_if_missing},
            {"field": "detail_min_chars", "label": "触发详情抓取的正文长度", "type": "number", "default": cls.default_detail_min_chars},
            {"field": "detail_max_chars", "label": "详情页正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
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
    source_owner = "openai"
    source_brand = "openai"
    source_scope = "company"
    source_channel = "newsroom_rss"
    source_url = feed_url
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "product_update", "api_platform", "research_paper", "developer_tool"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"

    # Playwright 渲染 openai.com 正文页时，标题/正文之间会夹一个独立的 "Loading…" 占位行
    # （异步区块的加载提示，渲染快照里残留），需在提取后剔除。只匹配「整行就是 Loading[…/...]」
    # 的孤立行，避免误删正文中合法的 "Loading ..." 句子。
    _render_placeholder_re = re.compile(r"(?m)^[ \t]*Loading(?:…|\.\.\.)?[ \t]*$\n?")

    # OpenAI 文章正文页（/index/{slug}）有 Cloudflare Managed Challenge，纯 httpx 只能拿到
    # 403 挑战壳页，正文需浏览器执行 JS 通过挑战后才渲染。RSS 自带的 summary 只是人工
    # 摘要——对“把原文概括成一段话”的日报场景而言，在 summary 上再概括等于零增量或幻觉，
    # 因此这里覆盖详情抓取：优先用 Playwright 渲染正文页，渲染失败时优雅降级回 summary。
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._renderer = None
        # 允许测试注入渲染函数（签名 async (url) -> html），免于真正启动浏览器。
        self._render_override = None

    async def _run(self, client, **kwargs):
        # 仅在确有详情抓取需求时才启动浏览器，避免无谓的进程开销。
        fetch_detail = self._bool_param(
            kwargs.get("fetch_detail_if_missing"), self.default_fetch_detail_if_missing
        )
        if self._render_override is not None or not fetch_detail:
            async for item in super()._run(client, **kwargs):
                yield item
            return

        from fetchers.impl.playwright_renderer import PlaywrightRenderer

        async with PlaywrightRenderer() as renderer:
            self._renderer = renderer
            try:
                async for item in super()._run(client, **kwargs):
                    yield item
            finally:
                self._renderer = None

    def _strip_render_placeholders(self, text: str) -> str:
        if not text:
            return text
        return self._render_placeholder_re.sub("", text)

    async def _detail_for_url(self, client, url, max_chars, detail_min_chars):
        html = ""
        if self._render_override is not None:
            html = await self._render_override(url)
        elif self._renderer is not None and getattr(self._renderer, "available", False):
            html = await self._renderer.render(url)

        if html:
            detail = extract_detail_from_html(html, max_chars, detail_min_chars)
            text = self._strip_render_placeholders(detail.text)
            if text and len(text) >= detail_min_chars:
                return {
                    "title": detail.title,
                    "text": text,
                    "method": f"playwright_{detail.method}" if detail.method else "playwright",
                    "url": url,
                }

        # 浏览器不可用 / 渲染失败 / 正文仍不足 → 退回 httpx 详情（多半同样被 CF 拦截，
        # 拿不到正文时返回空，由通用 RSS 逻辑降级为 summary）。
        return await super()._detail_for_url(client, url, max_chars, detail_min_chars)


class GoogleGeminiModelsRssFetcher(PresetRssFetcher):
    source_id = "rss_google_gemini_models"
    name = "Google Blog Gemini Models"
    description = "抓取 Google Blog Gemini Models 分类 RSS 中的 Gemini 模型、能力与产品公告。"
    icon = "🔷"
    feed_url = "https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/"
    category = "official"
    source_owner = "google"
    source_brand = "gemini"
    source_scope = "model_family"
    source_channel = "blog_category_rss"
    source_url = "https://blog.google/innovation-and-ai/models-and-research/gemini-models/"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "research_paper", "product_update", "developer_tool"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public_rss"


class HackerNewsAiRssFetcher(PresetRssFetcher):
    source_id = "rss_hn_ai"
    name = "Hacker News: AI"
    description = "Hacker News 中已获得社区投票/讨论的 AI 相关提交（按最低分数过滤掉招聘贴、0 赞自荐等噪声）。"
    icon = "🟧"
    # hnrss 的 ?q=AI 是无过滤的「最新提交」全文搜索 firehose——把 AI 当关键词捞新帖，
    # 噪声极高（招聘贴、0 赞自荐、与 AI 弱相关的随手提问）。hnrss 原生支持 points/comments
    # 数值门槛（底层走 Algolia numericFilters），因此默认叠加最低分数门槛，只保留社区已
    # 投票/讨论过的提交，把这个高噪声搜索源收敛成可用的开发者社区信号。
    base_feed_url = "https://hnrss.org/newest"
    search_query = "AI"
    default_min_points = 10
    default_min_comments = 0
    feed_url = "https://hnrss.org/newest?q=AI"
    category = "community"
    source_owner = "ycombinator"
    source_brand = "hacker_news"
    source_scope = "developer_community"
    source_channel = "search_rss"
    source_url = feed_url
    provenance_tier = "tier1_curated"
    content_tags = ["developer_tool", "market_news", "opinion", "product_update"]
    signal_strength = "medium_signal"
    noise_risk = "high_noise"
    fetch_reliability = "stable_public"

    # HN 是链接聚合 / 讨论源，不是内容平台：外链帖的正文在任意第三方域名（付费墙、
    # CF 挑战、SPA、视频/仓库等），逐条硬抓既慢又大面积失败，对归档价值低。因此把它
    # 当「发现源」用——默认关闭外链详情抓取，外链帖只保留标题 + 外链 + 讨论页 + 热度
    # 元数据；只有站内帖（Ask/Show/Tell HN，summary 即作者正文）才保留正文。
    default_fetch_detail_if_missing = False

    _points_re = re.compile(r"Points:\s*(\d+)")
    _num_comments_re = re.compile(r"#\s*Comments:\s*(\d+)")

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "min_points", "label": "最低分数门槛", "type": "number", "default": cls.default_min_points},
            {"field": "min_comments", "label": "最低评论数门槛", "type": "number", "default": cls.default_min_comments},
            {"field": "fetch_detail_if_missing", "label": "抓取外链正文（默认关闭，建议保持）", "type": "boolean", "default": cls.default_fetch_detail_if_missing},
            {"field": "detail_min_chars", "label": "触发详情抓取的正文长度", "type": "number", "default": cls.default_detail_min_chars},
            {"field": "detail_max_chars", "label": "详情页正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _finalize_content_text(self, entry: Any, content_text: str, detail_text: str) -> str:
        # 真抓到了外链正文（用户手动开启详情抓取且成功）→ 保留。
        if detail_text:
            return content_text
        link = entry.get("link", "")
        comments = entry.get("comments", "")
        # 外链帖（link != 讨论页）：RSS summary 只是 Article/Comments URL 模板，无正文价值
        # → 降级为纯发现条目（正文留空）。站内帖（link == comments，Ask/Show/Tell HN）的
        # summary 是作者写的真实正文 → 保留。
        if comments and link and link != comments:
            return ""
        return content_text

    def _raw_entry(self, entry: Any) -> Dict[str, Any]:
        raw = super()._raw_entry(entry)
        # HN 的社区热度（分数 / 评论数）是这个源的核心信号，写进 RSS summary 的
        # 「Points: N」「# Comments: N」段；即便外链帖正文被降级，热度元数据仍要保留。
        summary = entry.get("summary", "") or ""
        points_match = self._points_re.search(summary)
        comments_match = self._num_comments_re.search(summary)
        raw["hn_points"] = int(points_match.group(1)) if points_match else None
        raw["hn_num_comments"] = int(comments_match.group(1)) if comments_match else None
        raw["discussion_url"] = entry.get("comments", "")
        return raw

    def _build_feed_url(self, min_points: int, min_comments: int) -> str:
        from urllib.parse import urlencode

        params = {"q": self.search_query}
        if min_points > 0:
            params["points"] = min_points
        if min_comments > 0:
            params["comments"] = min_comments
        return f"{self.base_feed_url}?{urlencode(params)}"

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        min_points = self._positive_int_param(kwargs.get("min_points"), self.default_min_points)
        min_comments = self._positive_int_param(kwargs.get("min_comments"), self.default_min_comments)
        # 父类 PresetRssFetcher._run 会读取 self.feed_url 拼装参数，这里按门槛动态改写。
        # 与 GenericRssFetcher._run 改写 self.source_id 同属「单次运行内的实例身份切换」模式。
        self.feed_url = self._build_feed_url(min_points, min_comments)
        async for item in super()._run(client, **kwargs):
            yield item
