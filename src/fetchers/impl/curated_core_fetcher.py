import gzip
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List
from urllib.parse import urlparse

import httpx

from fetchers.base import BaseFetcher
from fetchers.impl.article_extractor import extract_article_detail
from fetchers.impl.webpage_fetcher import BaseWebPageListFetcher
from models.content import BaseContent, WebPageArticleContent


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


class OpenAiApiChangelogFetcher(SinglePageDocumentFetcher):
    source_id = "docs_openai_api_changelog"
    name = "OpenAI API Changelog"
    description = "抓取 OpenAI API 官方 Changelog 中的模型、API、平台能力与生命周期更新。"
    icon = "🧠"
    page_url = "https://developers.openai.com/api/docs/changelog"
    source_url = page_url
    site_name = "OpenAI API"
    source_section = "Changelog"
    source_owner = "openai"
    source_brand = "openai_api"
    source_scope = "api_platform"
    source_channel = "docs_changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


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


class GeminiApiChangelogFetcher(SinglePageDocumentFetcher):
    source_id = "docs_gemini_api_changelog"
    name = "Gemini API Release Notes"
    description = "抓取 Gemini API 官方 Changelog 中的模型、API 与开发者平台更新。"
    icon = "🔷"
    page_url = "https://ai.google.dev/gemini-api/docs/changelog"
    source_url = page_url
    site_name = "Gemini API"
    source_section = "Changelog"
    source_owner = "google"
    source_brand = "gemini_api"
    source_scope = "api_platform"
    source_channel = "docs_changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"


class GemmaReleaseNotesFetcher(SinglePageDocumentFetcher):
    source_id = "docs_gemma_release_notes"
    name = "Gemma Release Notes"
    description = "抓取 Gemma 官方 Release Notes 中的开放模型发布与更新。"
    icon = "💎"
    page_url = "https://ai.google.dev/gemma/docs/releases"
    source_url = page_url
    site_name = "Gemma"
    source_section = "Release Notes"
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
    description = "抓取 xAI 开发者 Release Notes 中的 Grok API、模型与产品更新。"
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


class XAiModelsDocsFetcher(SinglePageDocumentFetcher):
    source_id = "docs_xai_models"
    name = "xAI Models Docs"
    description = "抓取 xAI Models 文档中的 Grok 模型目录与 API 能力信息。"
    icon = "𝕏"
    page_url = "https://docs.x.ai/developers/models"
    source_url = page_url
    site_name = "xAI"
    source_section = "Models"
    source_owner = "xai"
    source_brand = "grok"
    source_scope = "api_platform"
    source_channel = "docs_reference"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform"]
    signal_strength = "medium_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


class AlibabaModelStudioAnnouncementsFetcher(SinglePageDocumentFetcher):
    source_id = "docs_alibaba_model_studio_announcements"
    name = "Alibaba Model Studio Model Announcements"
    description = "抓取阿里云 Model Studio 模型公告中的 Qwen 商业模型与 API 更新。"
    icon = "🟦"
    page_url = "https://www.alibabacloud.com/help/en/model-studio/model-announcements"
    source_url = page_url
    site_name = "Alibaba Cloud Model Studio"
    source_section = "Model Announcements"
    source_owner = "alibaba_cloud"
    source_brand = "model_studio"
    source_scope = "api_platform"
    source_channel = "docs_release_notes"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "api_platform", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"


class DeepSeekApiChangeLogFetcher(SinglePageDocumentFetcher):
    source_id = "docs_deepseek_api_changelog"
    name = "DeepSeek API Change Log"
    description = "抓取 DeepSeek API Change Log 中的模型、API 与平台更新。"
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


class ByteDanceSeedModelsFetcher(SinglePageDocumentFetcher):
    source_id = "web_bytedance_seed_models"
    name = "ByteDance Seed Models"
    description = "抓取 ByteDance Seed Models 目录中的 Seed/Seedance 核心模型产品信息。"
    icon = "🌱"
    page_url = "https://seed.bytedance.com/en/models"
    source_url = page_url
    site_name = "ByteDance Seed"
    source_section = "Models"
    source_owner = "bytedance_seed"
    source_brand = "seed"
    source_scope = "model_family"
    source_channel = "model_catalog"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "product_update", "research_paper"]
    signal_strength = "medium_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


class ByteDanceSeedResearchFetcher(SinglePageDocumentFetcher):
    source_id = "web_bytedance_seed_research"
    name = "ByteDance Seed Research"
    description = "抓取 ByteDance Seed Research 页面中的研究论文、技术报告与模型相关研究。"
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


class HuggingFaceDailyPapersFetcher(SinglePageDocumentFetcher):
    source_id = "web_huggingface_daily_papers"
    name = "Hugging Face Daily Papers"
    description = "抓取 Hugging Face Daily Papers 页面中的社区热门论文与模型研究信号。"
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
    exclude_url_patterns = ["cursor.com/changelog#"]
    default_fetch_detail = True
    source_owner = "cursor"
    source_brand = "cursor"
    source_scope = "developer_tool"
    source_channel = "changelog"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update"]
    signal_strength = "high_signal"
    noise_risk = "low_noise"
    fetch_reliability = "stable_public"


class QbitAiWebsiteFetcher(BaseWebPageListFetcher):
    source_id = "web_qbitai"
    name = "量子位 Website"
    description = "抓取量子位官网中的中文 AI 新闻、模型、产品和产业动态。"
    icon = "📰"
    listing_url = "https://www.qbitai.com/"
    source_url = listing_url
    site_name = "量子位"
    source_section = "Website"
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
        return bool(path) and not path.startswith(("category", "tag", "author", "page"))


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
        return bool(path) and path[:4].isdigit()


class JiqizhixinWebsiteFetcher(BaseFetcher):
    source_id = "web_jiqizhixin"
    content_type = "web_article"
    category = "official_web"
    name = "机器之心 Website"
    description = "抓取机器之心官网公开文章中的中文 AI 新闻、研究论文、模型和产业动态。"
    icon = "📰"
    source_url = "https://www.jiqizhixin.com/"
    sitemap_url = "https://www.jiqizhixin.com/shared/sitemap.xml.gz"
    site_name = "机器之心"
    source_section = "Website"
    source_owner = "jiqizhixin"
    source_brand = "机器之心"
    source_scope = "ai_media"
    source_channel = "website_reader_proxy"
    provenance_tier = "tier1_curated"
    content_tags = ["market_news", "research_paper", "model_release", "product_update", "tutorial_or_practice"]
    signal_strength = "medium_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "reader_proxy_sitemap"
    default_limit = 10
    default_lookback_days = 14
    default_sitemap_scan_limit = 80
    default_detail_max_chars = 20000
    min_content_chars = 200
    reader_prefix = "https://r.jina.ai/http://"
    article_url_re = re.compile(r"https?://www\.jiqizhixin\.com/articles/(\d{4}-\d{2}-\d{2})(?:-\d+)?")

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "article_lookback_days", "label": "文章回看天数", "type": "number", "default": cls.default_lookback_days},
            {"field": "sitemap_scan_limit", "label": "站点地图扫描上限", "type": "number", "default": cls.default_sitemap_scan_limit},
            {"field": "detail_max_chars", "label": "正文最大字符", "type": "number", "default": cls.default_detail_max_chars},
        ]

    def _positive_int_param(self, raw_value: Any, default: int) -> int:
        if raw_value in (None, ""):
            return default
        try:
            return max(int(raw_value), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"数值参数无效，使用默认值: {raw_value}")
            return default

    def _reader_url(self, source_url: str) -> str:
        return f"{self.reader_prefix}{source_url}"

    def _read_sitemap_text(self, response: httpx.Response) -> str:
        body = response.content
        try:
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
        except (OSError, EOFError):
            self.logger.warning("机器之心 sitemap gzip 解压失败，尝试按文本解析")
        return body.decode("utf-8", errors="ignore")

    def _normalize_article_url(self, raw_url: str) -> str:
        raw_url = html.unescape(raw_url.strip())
        match = self.article_url_re.search(raw_url)
        if not match:
            return ""
        return match.group(0).replace("http://", "https://", 1)

    def _article_date(self, source_url: str) -> datetime | None:
        match = re.search(r"/articles/(\d{4}-\d{2}-\d{2})(?:-\d+)?$", source_url)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _parse_sitemap_candidates(self, sitemap_text: str, lookback_days: int, scan_limit: int) -> List[str]:
        today = datetime.now(timezone.utc).date()
        earliest = today - timedelta(days=lookback_days)
        candidates: Dict[str, str] = {}
        for block in re.findall(r"<url>(.*?)</url>", sitemap_text, flags=re.DOTALL | re.IGNORECASE):
            loc_match = re.search(r"<loc>(.*?)</loc>", block, flags=re.DOTALL | re.IGNORECASE)
            if not loc_match:
                continue
            url = self._normalize_article_url(loc_match.group(1))
            if not url:
                continue
            article_date = self._article_date(url)
            if not article_date or article_date.date() < earliest:
                continue
            lastmod_match = re.search(r"<lastmod>(.*?)</lastmod>", block, flags=re.DOTALL | re.IGNORECASE)
            candidates[url] = lastmod_match.group(1).strip() if lastmod_match else article_date.isoformat()

        ordered = sorted(
            candidates,
            key=lambda url: (self._article_date(url) or datetime.min.replace(tzinfo=timezone.utc), candidates[url]),
            reverse=True,
        )
        return ordered[:scan_limit] if scan_limit else ordered

    def _parse_reader_markdown(self, text: str, fallback_url: str) -> Dict[str, str]:
        title = ""
        source_url = fallback_url
        title_match = re.search(r"^Title:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
        url_match = re.search(r"^URL Source:\s*(.+?)\s*$", text, flags=re.MULTILINE)
        if url_match:
            source_url = self._normalize_article_url(url_match.group(1)) or fallback_url
        content = text.split("Markdown Content:", 1)[1] if "Markdown Content:" in text else text
        content = content.strip()
        return {"title": title, "source_url": source_url, "content": content}

    def _is_valid_article(self, title: str, content: str) -> bool:
        if not title or title.startswith("文章库"):
            return False
        if "机器之心·数据服务" in title or "还在费劲爬数据" in content:
            return False
        return len(content) >= self.min_content_chars

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        limit = self._positive_int_param(kwargs.get("limit"), self.default_limit)
        lookback_days = self._positive_int_param(kwargs.get("article_lookback_days"), self.default_lookback_days)
        scan_limit = self._positive_int_param(kwargs.get("sitemap_scan_limit"), self.default_sitemap_scan_limit)
        max_chars = self._positive_int_param(kwargs.get("detail_max_chars"), self.default_detail_max_chars)
        if limit <= 0:
            return

        sitemap_response = await self._safe_get(client, self.sitemap_url)
        if not sitemap_response:
            raise RuntimeError(f"机器之心 sitemap 请求失败: {self.sitemap_url}")

        candidates = self._parse_sitemap_candidates(
            self._read_sitemap_text(sitemap_response),
            lookback_days=lookback_days,
            scan_limit=scan_limit,
        )

        yielded = 0
        seen_titles: set[str] = set()
        for source_url in candidates:
            reader_url = self._reader_url(source_url)
            response = await self._safe_get(client, reader_url)
            if not response:
                continue

            parsed = self._parse_reader_markdown(response.text, source_url)
            title = parsed["title"]
            content = parsed["content"][:max_chars] if max_chars else parsed["content"]
            article_url = parsed["source_url"]
            if not self._is_valid_article(title, content) or title in seen_titles:
                continue

            seen_titles.add(title)
            publish_date = self._article_date(article_url) or datetime.now(timezone.utc)
            item_id = hashlib.sha1(article_url.encode("utf-8")).hexdigest()[:16]
            yield WebPageArticleContent(
                id=f"{self.source_id}_{item_id}",
                title=title,
                source_url=article_url,
                publish_date=publish_date.isoformat(),
                content=content,
                has_content=bool(content),
                site_name=self.site_name,
                source_section=self.source_section,
                summary=content[:500],
                tags=list(self.content_tags),
                raw_data={
                    "listing_url": self.sitemap_url,
                    "url": article_url,
                    "reader_url": reader_url,
                    "reader_service": "r.jina.ai",
                    "listing_source": "sitemap_reader_proxy",
                    "detail_text_length": len(content),
                    "sitemap_candidate_count": len(candidates),
                },
            )
            yielded += 1
            if yielded >= limit:
                break
