"""配置驱动的通用网页抓取器（中级目标）。

与 ``GenericRssFetcher`` 之于 RSS 同理：暴露**唯一一个**网页抓取器，接入一个新网站 = 写一条
``SourceConfigRecord`` 配置（listing 地址 + URL 模式 + 可选详情 Profile / CSS schema），而非新写一个
``BaseWebPageListFetcher`` 子类。

复用栈：
- 发现：默认复用 ``BaseWebPageListFetcher`` 的启发式发现（锚点匹配 + 内嵌 JSON + 标题/日期推断），
  仅需 ``listing_url`` + ``article_url_patterns``；配置提供 ``listing_css`` 时改走 CSS 精确抽取。
- 详情：复用 ``_web_backend_detail`` → crawl4ai（配置开启浏览器时显式注入由配置构造的 ``CrawlProfile``），
  未装 crawl4ai / 未开浏览器时回退 legacy httpx 提取。
- 身份：仿 ``GenericRssFetcher`` 在 ``_run`` 内 ``self.source_id = 运行时配置 ID``，使入库条目带每配置身份。
"""

import json
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from fetchers.impl.webpage_fetcher import BaseWebPageListFetcher
from models.content import BaseContent, WebPageArticleContent


class ConfigurableWebFetcher(BaseWebPageListFetcher):
    is_template = True  # 通用模板节点:后端保留,前端目录不显现
    source_id = "generic_web"
    content_type = "web_article"
    category = "advanced"

    name = "通用网页"
    description = "按配置抓取任意网页列表页（官网/博客/新闻），无需为每个站点新写抓取器。"
    icon = "🌐"

    # 默认不常驻浏览器；仅当配置开启 detail_use_browser 时在 fetch() 内置为 True，避免无谓进程开销。
    web_backend_enabled = False

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "listing_url", "label": "列表页地址", "type": "url", "default": ""},
            {"field": "site_name", "label": "站点名称", "type": "text", "default": ""},
            {"field": "source_section", "label": "栏目", "type": "text", "default": ""},
            {"field": "category", "label": "业务分类", "type": "text", "default": "official_web"},
            {"field": "article_url_patterns", "label": "文章 URL 包含模式（逗号分隔）", "type": "text", "default": ""},
            {"field": "exclude_url_patterns", "label": "排除 URL 模式（逗号分隔）", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "fetch_detail", "label": "抓取正文页", "type": "boolean", "default": True},
            {"field": "detail_max_chars", "label": "正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
            {"field": "drop_empty_content", "label": "丢弃空正文条目", "type": "boolean", "default": False},
            {"field": "max_listing_pages", "label": "列表翻页上限", "type": "number", "default": 1},
            # —— 详情 Profile（仅 detail_use_browser=True 时生效）——
            {"field": "detail_use_browser", "label": "用浏览器渲染详情(crawl4ai)", "type": "boolean", "default": False},
            {"field": "target_elements", "label": "正文容器选择器（逗号分隔）", "type": "text", "default": ""},
            {"field": "excluded_selector", "label": "正文内排除选择器", "type": "text", "default": ""},
            {"field": "wait_for", "label": "渲染等待条件（css:/js:）", "type": "text", "default": ""},
            {"field": "scan_full_page", "label": "渲染时滚动整页", "type": "boolean", "default": False},
            # —— 可选 CSS 列表 schema（启发式失准时的精确兜底）——
            {"field": "listing_css", "label": "列表 CSS schema（JSON: item/url/title/date/summary）", "type": "text", "default": ""},
        ]

    # —— 参数解析助手 ——

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _flag(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value in (None, ""):
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _apply_config(self, kwargs: Dict[str, Any]) -> None:
        """把运行时配置注入实例属性，使复用的基类发现逻辑按配置运行。"""
        self.source_id = str(kwargs.get("source_id") or "").strip() or self.source_id
        # 兼容 source-config 既有的 url 字段（build_source_fetch_params 会映射为 listing_url）
        self.listing_url = str(kwargs.get("listing_url") or kwargs.get("url") or "").strip()
        self.site_name = str(kwargs.get("site_name") or "").strip()
        self.source_section = str(kwargs.get("source_section") or "").strip()
        self.category = str(kwargs.get("category") or self.category).strip() or self.category
        self.article_url_patterns = self._as_list(kwargs.get("article_url_patterns"))
        self.exclude_url_patterns = self._as_list(kwargs.get("exclude_url_patterns"))
        self.drop_empty_content = self._flag(kwargs.get("drop_empty_content"), False)
        self.max_listing_pages = self._positive_int_param(kwargs.get("max_listing_pages"), 1) or 1

        self._listing_css = self._as_dict(kwargs.get("listing_css"))
        self._configured_profile = self._build_profile(kwargs)

    def _build_profile(self, kwargs: Dict[str, Any]):
        """配置开启浏览器详情时，用配置构造一个显式 CrawlProfile（绕过 URL 匹配）。"""
        if not self._flag(kwargs.get("detail_use_browser"), False):
            return None
        from fetchers.web_content.profiles import COMMON_EXCLUDED_SELECTOR, CrawlProfile

        excluded = str(kwargs.get("excluded_selector") or "").strip()
        wait_for = str(kwargs.get("wait_for") or "").strip() or None
        return CrawlProfile(
            name=f"config:{self.source_id}",
            domains=(),
            target_elements=tuple(self._as_list(kwargs.get("target_elements"))),
            excluded_selector=excluded or COMMON_EXCLUDED_SELECTOR,
            wait_for=wait_for,
            scan_full_page=self._flag(kwargs.get("scan_full_page"), False),
        )

    # —— 浏览器生命周期：仅配置开启时常驻 ——

    async def fetch(self, **kwargs) -> AsyncGenerator[BaseContent, None]:
        # web_backend_enabled 在 fetch() 起始被 _open_web_backend 读取，故须在 super().fetch() 前据配置设定。
        self.web_backend_enabled = self._flag(kwargs.get("detail_use_browser"), False)
        async for item in super().fetch(**kwargs):
            yield item

    async def _web_backend_detail(self, url: str, max_chars: int, profile=None):
        # 注入配置构造的 Profile（若有），跳过 URL 匹配预检，让任意站点都能用配置的详情规则。
        return await super()._web_backend_detail(
            url, max_chars, profile=profile or getattr(self, "_configured_profile", None)
        )

    # —— 主流程 ——

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        self._apply_config(kwargs)
        if not self.listing_url:
            raise ValueError("通用网页抓取器缺少 listing_url（或 url）配置")

        if self._listing_css:
            async for item in self._run_with_css_schema(client, **kwargs):
                yield item
            return

        # 默认走基类启发式发现 + 排序 + 详情抓取（零额外代码）
        async for item in super()._run(client, **kwargs):
            yield item

    async def _run_with_css_schema(
        self, client: httpx.AsyncClient, **kwargs
    ) -> AsyncGenerator[BaseContent, None]:
        """配置提供 listing_css 时的精确发现：用 CSS 选择器逐条抽取，再复用详情/产出逻辑。"""
        limit = self._entry_limit(kwargs.get("limit"))
        fetch_detail = self._bool_param(kwargs.get("fetch_detail"))
        detail_max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        item_sel = str(self._listing_css.get("item") or "").strip()
        url_sel = str(self._listing_css.get("url") or "").strip()
        title_sel = str(self._listing_css.get("title") or "").strip()
        date_sel = str(self._listing_css.get("date") or "").strip()
        summary_sel = str(self._listing_css.get("summary") or "").strip()
        if not item_sel:
            raise ValueError("listing_css 至少需要 item 选择器")

        response = await self._safe_get(client, self.listing_url)
        if not response:
            raise RuntimeError(f"通用网页列表请求失败: {self.listing_url}")
        soup = BeautifulSoup(response.text, "html.parser")
        base_url = str(response.url)

        seen: set[str] = set()
        emitted = 0
        for item in soup.select(item_sel):
            if not isinstance(item, Tag):
                continue
            url = self._css_url(item, url_sel, base_url)
            if not url or url in seen:
                continue
            if self.article_url_patterns and not self._matches_article_url(url):
                continue
            seen.add(url)

            title = self._css_text(item, title_sel) or self._title_from_url(url) or "未命名网页条目"
            summary = self._css_text(item, summary_sel)[:500]
            date_text = self._css_text(item, date_sel) if date_sel else ""
            publish_date = self._extract_datetime_or_empty(date_text or f"{title} {summary}")

            content_id = self._content_id(url)
            detail = {"title": "", "text": "", "method": "", "url": ""}
            detail_fetched = fetch_detail and not await self._should_skip_detail_fetch(content_id)
            if detail_fetched:
                detail = await self._detail_for_url(client, url, detail_max_chars)
                if (title == "未命名网页条目") and detail["title"]:
                    title = detail["title"]
            content = detail["text"] or summary
            if self.drop_empty_content and not content:
                continue

            raw_data = self._raw_entry(url, title, summary)
            raw_data.update({
                "listing_source": "configurable_css_schema",
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
                publish_date=publish_date or self._extract_datetime(f"{title} {summary}"),
                content=content,
                has_content=bool(content),
                site_name=self.site_name or self.name,
                source_section=self.source_section,
                summary=summary,
                tags=[self.category, "webpage"],
                raw_data=raw_data,
            )
            emitted += 1
            if emitted >= limit:
                break

    def _css_url(self, item: Tag, url_sel: str, base_url: str) -> str:
        node = item.select_one(url_sel) if url_sel else (item if item.name == "a" else item.find("a"))
        if not isinstance(node, Tag):
            return ""
        href = node.get("href")
        if not href:
            return ""
        return self._normalize_article_url(urljoin(base_url, str(href)))

    def _css_text(self, item: Tag, selector: str) -> str:
        if not selector:
            return ""
        node = item.select_one(selector)
        if not isinstance(node, Tag):
            return ""
        return self._clean_text(node.get_text(" ", strip=True))
