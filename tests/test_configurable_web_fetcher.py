"""中级目标：配置驱动通用网页抓取器 ConfigurableWebFetcher 的测试。

覆盖：
- 启发式发现 + 每配置身份（content_item.source_id == 配置 source_id）；
- listing_css 精确发现；
- 详情 Profile 显式注入（配置构造的 CrawlProfile 传到后端）；
- 未开浏览器时回退 legacy httpx 详情；
- source-config → fetcher 路由（resolve_source_fetcher_id / build_source_fetch_params）。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.configurable_web_fetcher import ConfigurableWebFetcher  # noqa: E402
from fetchers.web_content.backend import DetailResult  # noqa: E402
from fetchers.web_content.profiles import CrawlProfile  # noqa: E402


class DummyResponse:
    def __init__(self, text: str, url: str = "https://example.test/news"):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/html"}


def _run(coro):
    return asyncio.run(coro)


def test_generic_web_heuristic_discovery_and_identity():
    listing = (
        "<html><body>"
        "<article><a href='/news/alpha-launch'><h2>Alpha Launch</h2>"
        "<p>A genuine summary paragraph worth archiving for Alpha.</p></a></article>"
        "<article><a href='/news/beta-update'><h2>Beta Update</h2>"
        "<p>Another real summary paragraph describing Beta.</p></a></article>"
        "</body></html>"
    )

    fetcher = ConfigurableWebFetcher()

    async def fake_safe_get(client, url, **kwargs):
        return DummyResponse(listing, url)

    fetcher._safe_get = fake_safe_get

    async def collect():
        return [
            item
            async for item in fetcher.fetch(
                source_id="web_demo_site",
                listing_url="https://example.test/news",
                site_name="Demo",
                article_url_patterns="example.test/news/",
                limit=10,
                fetch_detail=False,
            )
        ]

    items = _run(collect())
    urls = sorted(item.source_url for item in items)
    assert urls == [
        "https://example.test/news/alpha-launch",
        "https://example.test/news/beta-update",
    ]
    # 每配置身份：入库条目 source_id 应为配置的 source_id（fetch() 回写）
    assert all(item.source_id == "web_demo_site" for item in items)
    # 稳定 ID 以配置 source_id 为前缀
    assert all(item.id.startswith("web_demo_site_") for item in items)
    assert all(item.site_name == "Demo" for item in items)


def test_generic_web_css_schema_discovery():
    listing = (
        "<html><body><div class='feed'>"
        "<div class='card'><a class='lnk' href='/p/one'>One Title</a>"
        "<span class='dt'>2026-05-01</span><p class='sum'>Summary one.</p></div>"
        "<div class='card'><a class='lnk' href='/p/two'>Two Title</a>"
        "<span class='dt'>2026-04-01</span><p class='sum'>Summary two.</p></div>"
        "</div></body></html>"
    )
    fetcher = ConfigurableWebFetcher()

    async def fake_safe_get(client, url, **kwargs):
        return DummyResponse(listing, url)

    fetcher._safe_get = fake_safe_get

    listing_css = json.dumps({
        "item": ".card",
        "url": "a.lnk",
        "title": "a.lnk",
        "date": ".dt",
        "summary": ".sum",
    })

    async def collect():
        return [
            item
            async for item in fetcher.fetch(
                source_id="web_css_site",
                listing_url="https://example.test/blog",
                listing_css=listing_css,
                limit=10,
                fetch_detail=False,
            )
        ]

    items = _run(collect())
    by_url = {item.source_url: item for item in items}
    assert set(by_url) == {"https://example.test/p/one", "https://example.test/p/two"}
    one = by_url["https://example.test/p/one"]
    assert one.title == "One Title"
    assert one.summary == "Summary one."
    assert one.publish_date.startswith("2026-05-01")
    assert one.raw_data["listing_source"] == "configurable_css_schema"


def test_generic_web_builds_and_injects_configured_profile():
    fetcher = ConfigurableWebFetcher()
    fetcher._apply_config({
        "source_id": "web_profiled",
        "listing_url": "https://example.test/news",
        "detail_use_browser": True,
        "target_elements": "article, .post-body",
        "excluded_selector": ".ad, .promo",
        "wait_for": "css:article",
        "scan_full_page": True,
    })
    profile = fetcher._configured_profile
    assert isinstance(profile, CrawlProfile)
    assert profile.name == "config:web_profiled"
    assert profile.target_elements == ("article", ".post-body")
    assert profile.excluded_selector == ".ad, .promo"
    assert profile.wait_for == "css:article"
    assert profile.scan_full_page is True

    # _web_backend_detail 应把该 Profile 传给后端 extract（而非按 URL 匹配）
    captured = {}

    class FakeBackend:
        async def extract(self, url, *, max_chars=8000, detail_min_chars=200, profile=None):
            captured["profile"] = profile
            return DetailResult(
                title="T", text="x" * 500, method="crawl4ai:config",
                url=url, success=True, backend="crawl4ai",
            )

    fetcher._web_backend = FakeBackend()
    detail = _run(fetcher._web_backend_detail("https://example.test/news/x", 8000))
    assert captured["profile"] is profile
    assert detail["method"].startswith("crawl4ai")


def test_generic_web_no_browser_falls_back_to_legacy_detail():
    # detail_use_browser 关闭 → 不构造 Profile、不常驻浏览器；详情走 legacy httpx 提取。
    listing = (
        "<html><body><article><a href='/news/real-post'><h2>Real Post</h2>"
        "<p>Short listing summary.</p></a></article></body></html>"
    )
    detail_html = (
        "<html><head><title>Real Post</title></head><body><article>"
        + "<p>" + ("This is the full article body paragraph with real content. " * 8) + "</p>"
        + "</article></body></html>"
    )

    fetcher = ConfigurableWebFetcher()

    async def fake_safe_get(client, url, **kwargs):
        if url.endswith("/news/real-post"):
            return DummyResponse(detail_html, url)
        return DummyResponse(listing, url)

    fetcher._safe_get = fake_safe_get

    async def collect():
        return [
            item
            async for item in fetcher.fetch(
                source_id="web_legacy_site",
                listing_url="https://example.test/news",
                article_url_patterns="example.test/news/",
                limit=5,
                fetch_detail=True,
                detail_max_chars=4000,
            )
        ]

    items = _run(collect())
    assert fetcher.web_backend_enabled is False  # 未开浏览器
    post = items[0]
    assert "full article body paragraph" in post.content
    method = post.raw_data["detail_extraction_method"]
    assert method and not method.startswith("crawl4ai")


def test_source_config_routing_to_generic_web():
    import api.app as app_module
    from models.db import SourceConfigRecord

    record = SourceConfigRecord(
        source_id="web_routed",
        name="Routed Web",
        source_type="web",
        url="https://example.test/articles",
        category="official_web",
        params_json=json.dumps({"article_url_patterns": "example.test/articles/"}),
        created_at="2026-06-24T00:00:00+00:00",
        updated_at="2026-06-24T00:00:00+00:00",
    )

    assert app_module.resolve_source_fetcher_id(record) == "generic_web"
    params = app_module.build_source_fetch_params(record)
    assert params["listing_url"] == "https://example.test/articles"
    assert params["source_id"] == "web_routed"
    assert params["site_name"] == "Routed Web"
    assert params["article_url_patterns"] == "example.test/articles/"
    # web 类型不应注入 RSS 专用的 feed_url
    assert "feed_url" not in params


def test_source_config_rss_routing_unchanged():
    import api.app as app_module
    from models.db import SourceConfigRecord

    record = SourceConfigRecord(
        source_id="rss_demo",
        name="RSS Demo",
        source_type="rss",
        url="https://example.test/feed.xml",
        category="official",
        created_at="2026-06-24T00:00:00+00:00",
        updated_at="2026-06-24T00:00:00+00:00",
    )
    assert app_module.resolve_source_fetcher_id(record) == "generic_rss"
    params = app_module.build_source_fetch_params(record)
    assert params["feed_url"] == "https://example.test/feed.xml"
    assert params["feed_name"] == "RSS Demo"
    assert "listing_url" not in params
