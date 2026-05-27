from datetime import datetime, timezone
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
            self.logger.error("单页文档 URL 不能为空，放弃抓取。")
            return

        response = await self._safe_get(client, self.page_url)
        if not response:
            return

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
