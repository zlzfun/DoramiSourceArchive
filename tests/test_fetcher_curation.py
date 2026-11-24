import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers import catalog_files
from fetchers.registry import fetcher_registry, focused_curation_for

# 进过审阅的策展源 = 策展源目录(src/sources/catalog)里的条目。issue #262 R2 之前另有一份手写白名单
# ESSENTIAL_FETCHER_IDS,内容恰好就是全部预设源;现在目录是唯一的名单。
CATALOG_SOURCE_IDS = frozenset(catalog_files.catalog())


GENERIC_ADVANCED_FETCHER_IDS = {
    "generic_rss",
    "generic_podcast_rss",
    "generic_web",
    "generic_github_releases",
    "generic_github_repositories",
    "generic_huggingface_models",
    "generic_x_timeline",
}


def test_default_visible_fetchers_match_the_catalog():
    metadata = fetcher_registry.get_all_metadata()
    visible_ids = {item["id"] for item in metadata if item.get("default_visible") is not False}

    registered_ids = {item["id"] for item in metadata}
    assert CATALOG_SOURCE_IDS <= registered_ids
    assert visible_ids == CATALOG_SOURCE_IDS


def test_registry_contains_only_recommended_sources_plus_generic_capabilities():
    metadata = fetcher_registry.get_all_metadata()
    registered_ids = {item["id"] for item in metadata}

    assert registered_ids == CATALOG_SOURCE_IDS | GENERIC_ADVANCED_FETCHER_IDS


def test_recommended_fetcher_metadata_has_source_dimensions():
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}
    item = metadata["web_anthropic_news"]

    assert item["source_owner"] == "anthropic"
    assert item["source_brand"] == "anthropic"
    assert item["base_url"] == "https://www.anthropic.com/news"
    assert item["provenance_tier"] == "tier0_primary"
    # issue #178:源级 category / content_tags 退役,准入状态独立成 admission_status
    assert "content_tags" not in item and "category" not in item
    assert item["admission_status"] == "admitted"


def test_recovered_sources_are_default_visible_with_stable_metadata():
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}

    assert metadata["docs_openai_codex_changelog"]["base_url"] == "https://developers.openai.com/codex/changelog"
    assert metadata["rss_google_gemini_models"]["fetch_reliability"] == "stable_public_rss"
    assert metadata["web_qwen_blog"]["base_url"] == "https://qwen.ai/api/v2/article/retrieval"
    assert metadata["web_qwen_blog"]["fetch_reliability"] == "stable_public_api"
    assert metadata["web_ithome_ai"]["fetch_reliability"] == "stable_public_website_category"
    assert metadata["web_aiera"]["base_url"] == "https://aiera.com.cn/"


def test_fourth_expansion_sources_are_visible_incubating_and_fully_classified():
    source_ids = {
        "rss_microsoft_ai_models",
        "web_artificial_analysis",
        "web_meta_ai_blog",
        "web_kimi_research",
        "web_minimax_research",
        "rss_import_ai",
        "docs_arena_leaderboard_changelog",
    }
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}
    required_dimensions = (
        "source_owner",
        "source_brand",
        "source_scope",
        "source_channel",
        "base_url",
        "provenance_tier",
        "signal_strength",
        "noise_risk",
        "fetch_reliability",
    )

    assert source_ids <= CATALOG_SOURCE_IDS
    for source_id in source_ids:
        item = metadata[source_id]
        assert item["admission_status"] == "incubating"
        assert item["default_visible"] is True
        assert all(item[dimension] for dimension in required_dimensions)

    assert metadata["docs_arena_leaderboard_changelog"]["default_kind"] == "release"
    assert all(metadata[source_id]["shape"] == "article" for source_id in source_ids)


def test_only_catalog_sources_are_default_visible():
    """``default_visible`` 只看在不在策展源目录里:目录外的 id 不管叫什么、是不是模板,都不可见。"""
    listed = focused_curation_for("rss_openai_news")
    assert listed == {
        "default_visible": True,
        "curation_reason": "推荐审查源：已按 v1.0 维度完成书面化标注，进入从 0 做加法的当前批次。",
    }
    # 在目录里就可见,与调用方传不传 is_template 无关(与旧白名单同序:先看名单)
    assert focused_curation_for("rss_openai_news", is_template=True) == listed

    stranger = focused_curation_for("rss_huggingface_blog")          # 不在目录里的 id
    assert stranger["default_visible"] is False
    assert "弱相关" in stranger["curation_reason"]

    template = focused_curation_for("generic_rss", is_template=True)
    assert template["default_visible"] is False
    assert "通用扩展能力" in template["curation_reason"]

    # 墓碑、系统源不是「现役策展源」
    assert focused_curation_for("x_openrouter")["default_visible"] is False
    assert focused_curation_for("dorami_daily_brief")["default_visible"] is False


def test_registry_module_no_longer_carries_hand_written_source_lists():
    import fetchers.registry as registry_module

    for name in ("ESSENTIAL_FETCHER_IDS", "FOCUSED_FETCHER_CURATION", "DECOMMISSIONED_FETCHER_IDS"):
        assert not hasattr(registry_module, name), name
