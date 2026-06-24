"""高级目标：source_builder（URL → 分析 → LLM 生成配置 → 试抓预览）测试。

离线：stub HTTP（monkeypatch _fetch / _safe_get）+ 注入假 LLM（monkeypatch chat_completion +
resolve_llm_config）。仿 test_daily_brief / test_llm_client 的打桩风格。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config  # noqa: E402
from services import source_builder  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


LISTING_HTML = """
<html lang="en"><head>
  <title>Demo Labs — News</title>
  <meta property="og:site_name" content="Demo Labs">
  <meta name="description" content="Demo Labs official news and product updates.">
  <link rel="alternate" type="application/rss+xml" href="/feed.xml">
</head><body>
  <nav><a href="/pricing">Pricing</a><a href="/about">About</a></nav>
  <main>
    <article><a href="/news/alpha-launch-2026"><h2>Alpha Launch 2026</h2>
      <p>We launched Alpha, a major release.</p></a></article>
    <article><a href="/news/beta-improvements"><h2>Beta Improvements</h2>
      <p>Beta gets faster and smarter.</p></a></article>
    <article><a href="/news/gamma-research-notes"><h2>Gamma Research Notes</h2>
      <p>New research on Gamma.</p></a></article>
  </main>
</body></html>
"""

ARTICLE_HTML = """
<html><head><title>Alpha Launch 2026</title></head><body>
  <article class="post-content"><h1>Alpha Launch 2026</h1>
  <p>This is the full article body of the Alpha launch with real substance.</p>
  <div class="related">Related stuff</div></article>
</body></html>
"""

RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
  <title>Demo Labs Feed</title><link>https://demo.test</link>
  <item><title>Alpha</title><link>https://demo.test/news/alpha</link></item>
</channel></rss>"""


# ---------- 纯函数 ----------

def test_detect_page_type():
    assert source_builder.detect_page_type(RSS_XML, "application/rss+xml") == "rss"
    assert source_builder.detect_page_type("<rss></rss>", "text/html") == "rss"
    assert source_builder.detect_page_type("{}", "application/json") == "json"
    assert source_builder.detect_page_type(LISTING_HTML, "text/html; charset=utf-8") == "web"


def test_find_rss_autodiscovery():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(LISTING_HTML, "html.parser")
    feed = source_builder.find_rss_autodiscovery(soup, "https://demo.test/news")
    assert feed == "https://demo.test/feed.xml"


def test_derive_article_patterns():
    links = [
        "https://demo.test/news/alpha-launch-2026",
        "https://demo.test/news/beta-improvements",
        "https://demo.test/news/gamma-research-notes",
        "https://demo.test/pricing",
        "https://other.test/news/x",
    ]
    patterns = source_builder.derive_article_patterns(links, "demo.test")
    assert "/news/" in patterns


def test_collect_html_signals():
    signals = source_builder.collect_html_signals("https://demo.test/news", LISTING_HTML)
    assert signals["page_title"] == "Demo Labs — News"
    assert signals["site_name"] == "Demo Labs"
    assert "/news/" in signals["pattern_candidates"]
    urls = {item["url"] for item in signals["sample_links"]}
    assert "https://demo.test/news/alpha-launch-2026" in urls


def test_slug_source_id_dedup():
    sid1 = source_builder.slug_source_id("https://demo.test/news", set())
    assert sid1.startswith("web_demo_test")
    sid2 = source_builder.slug_source_id("https://demo.test/news", {sid1})
    assert sid2 != sid1


# ---------- analyze_url ----------

def _patch_fetch(monkeypatch, mapping):
    async def fake_fetch(url, *, timeout=20):
        for key, (text, ct) in mapping.items():
            if key in url:
                return text, url, ct, 200
        return "", url, "", 404

    monkeypatch.setattr(source_builder, "_fetch", fake_fetch)


def test_analyze_url_heuristic_when_no_llm(monkeypatch):
    _patch_fetch(monkeypatch, {"/news": (LISTING_HTML, "text/html")})
    monkeypatch.setattr(
        "services.daily_brief.resolve_llm_config",
        lambda session: config.LLMConfig(),  # 未配置
    )
    result = _run(source_builder.analyze_url("https://demo.test/news", session=None))
    assert result["ok"] is True
    assert result["page_type"] == "web"
    assert result["llm_used"] is False
    cfg = result["proposed_config"]
    assert cfg["source_type"] == "web"
    assert cfg["source_id"].startswith("web_demo_test")
    assert "/news/" in cfg["params"]["article_url_patterns"]
    # RSS 自发现应进 warnings
    assert any("RSS" in w for w in result["warnings"])


def test_analyze_url_with_llm(monkeypatch):
    _patch_fetch(monkeypatch, {
        "/news/alpha-launch-2026": (ARTICLE_HTML, "text/html"),  # 样例文章详情
        "/news": (LISTING_HTML, "text/html"),                    # 列表
    })
    monkeypatch.setattr(
        "services.daily_brief.resolve_llm_config",
        lambda session: config.LLMConfig(base_url="http://x", api_key="k", model="m"),
    )

    async def fake_chat(*, messages, config, **kwargs):
        system = messages[0].content
        if source_builder.SOURCE_CONFIG_SYSTEM_PROMPT[:20] in system:
            return json.dumps({
                "name": "Demo Labs News",
                "category": "official_web",
                "article_url_patterns": ["/news/"],
                "exclude_url_patterns": ["/pricing"],
                "source_owner": "demo",
                "content_tags": ["product_update", "model_release"],
                "signal_strength": "high_signal",
            })
        if source_builder.DETAIL_PROFILE_SYSTEM_PROMPT[:20] in system:
            return json.dumps({
                "use_browser": True,
                "target_elements": ["article.post-content"],
                "excluded_selector": ".related",
                "wait_for": "",
            })
        return "{}"

    monkeypatch.setattr(source_builder.llm_client, "chat_completion", fake_chat)
    # 避免任何 crawl4ai 介入（样例文章走 httpx fake_fetch）
    result = _run(source_builder.analyze_url("https://demo.test/news", session=None))

    assert result["ok"] is True
    assert result["llm_used"] is True
    assert result["detail_profiled"] is True
    cfg = result["proposed_config"]
    assert cfg["name"] == "Demo Labs News"
    assert cfg["source_owner"] == "demo"
    assert "model_release" in cfg["content_tags"]
    assert cfg["params"]["article_url_patterns"] == "/news/"
    assert cfg["params"]["target_elements"] == "article.post-content"
    assert cfg["params"]["detail_use_browser"] is True
    assert cfg["params"]["excluded_selector"] == ".related"


def test_analyze_url_rss(monkeypatch):
    _patch_fetch(monkeypatch, {"feed": (RSS_XML, "application/rss+xml")})
    monkeypatch.setattr("services.daily_brief.resolve_llm_config", lambda session: config.LLMConfig())
    result = _run(source_builder.analyze_url("https://demo.test/feed.xml", session=None))
    assert result["ok"] is True
    assert result["page_type"] == "rss"
    assert result["proposed_config"]["source_type"] == "rss"
    assert result["proposed_config"]["source_id"].startswith("rss_")


# ---------- preview_config ----------

def test_preview_config_web(monkeypatch):
    from fetchers.impl.configurable_web_fetcher import ConfigurableWebFetcher

    class _Resp:
        def __init__(self, text, url):
            self.text = text
            self.url = url

    async def fake_safe_get(self, client, url, **kwargs):
        return _Resp(LISTING_HTML, url)

    monkeypatch.setattr(ConfigurableWebFetcher, "_safe_get", fake_safe_get)

    payload = {
        "source_id": "web_demo_preview",
        "name": "Demo",
        "source_type": "web",
        "url": "https://demo.test/news",
        "params": {"article_url_patterns": "/news/", "fetch_detail": False, "limit": 5},
    }
    result = _run(source_builder.preview_config(payload))
    assert result["ok"] is True
    assert result["count"] >= 3
    urls = {e["url"] for e in result["entries"]}
    assert any("/news/alpha-launch-2026" in u for u in urls)


def test_preview_config_rejects_unknown_type():
    result = _run(source_builder.preview_config({"source_type": "json", "url": "https://x"}))
    assert result["ok"] is False
