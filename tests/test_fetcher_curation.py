import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.registry import ESSENTIAL_FETCHER_IDS, fetcher_registry, focused_curation_for


GENERIC_ADVANCED_FETCHER_IDS = {
    "generic_rss",
    "generic_github_releases",
    "generic_github_repositories",
    "generic_huggingface_models",
}


def test_default_visible_fetchers_match_essential_whitelist():
    metadata = fetcher_registry.get_all_metadata()
    visible_ids = {item["id"] for item in metadata if item.get("default_visible") is not False}

    registered_ids = {item["id"] for item in metadata}
    assert ESSENTIAL_FETCHER_IDS <= registered_ids
    assert visible_ids == ESSENTIAL_FETCHER_IDS


def test_registry_contains_only_recommended_sources_plus_generic_capabilities():
    metadata = fetcher_registry.get_all_metadata()
    registered_ids = {item["id"] for item in metadata}

    assert registered_ids == ESSENTIAL_FETCHER_IDS | GENERIC_ADVANCED_FETCHER_IDS


def test_recommended_fetcher_metadata_has_source_dimensions():
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}
    item = metadata["web_anthropic_news"]

    assert item["source_owner"] == "anthropic"
    assert item["source_brand"] == "anthropic"
    assert item["base_url"] == "https://www.anthropic.com/news"
    assert item["provenance_tier"] == "tier0_primary"
    assert "model_release" in item["content_tags"]


def test_recovered_sources_are_default_visible_with_stable_metadata():
    metadata = {item["id"]: item for item in fetcher_registry.get_all_metadata()}

    assert metadata["docs_openai_api_changelog"]["base_url"] == "https://developers.openai.com/api/docs/changelog"
    assert metadata["rss_google_gemini_models"]["fetch_reliability"] == "stable_public_rss"
    assert metadata["web_qwen_blog"]["base_url"] == "https://qwen.ai/api/v2/article/retrieval"
    assert metadata["web_qwen_blog"]["fetch_reliability"] == "stable_public_api"
    assert metadata["web_ithome_ai"]["fetch_reliability"] == "stable_public_website_category"
    assert metadata["web_aiera"]["base_url"] == "https://aiera.com.cn/"
    assert metadata["web_jiqizhixin"]["fetch_reliability"] == "reader_proxy_sitemap"


def test_non_whitelisted_source_cannot_become_default_visible(monkeypatch):
    import fetchers.registry as registry_module

    monkeypatch.setitem(
        registry_module.FOCUSED_FETCHER_CURATION,
        "rss_huggingface_blog",
        {
            "default_visible": True,
            "curation_reason": "temporary test value",
        },
    )

    curation = focused_curation_for("rss_huggingface_blog", "official")

    assert curation["default_visible"] is False
    assert "精品源白名单" in curation["curation_reason"]
