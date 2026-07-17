import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.rss_fetcher import (
    BairBlogRssFetcher,
    GenericRssFetcher,
    GoogleDeepMindBlogRssFetcher,
    GoogleGeminiModelsRssFetcher,
    HackerNewsAiRssFetcher,
    HuggingFaceBlogRssFetcher,
    InterconnectsRssFetcher,
    LatentSpaceRssFetcher,
    LilianWengRssFetcher,
    MistralNewsRssFetcher,
    OneUsefulThingRssFetcher,
    OpenAINewsRssFetcher,
    RaschkaRssFetcher,
    RuanYifengRssFetcher,
    SimonWillisonRssFetcher,
    TestingCatalogRssFetcher as CatalogRssFetcher,
    TheDecoderRssFetcher,
)
from fetchers.registry import ESSENTIAL_FETCHER_IDS, fetcher_registry


class DummyResponse:
    def __init__(self, text: str, url: str = "https://example.test/response", status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": "text/plain"}


def test_rss_fetcher_sorts_newest_first_and_backfills_missing_detail():
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Example Feed</title>
        <item>
          <title>Old</title>
          <link>https://example.test/old</link>
          <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
          <description>old body that is already long enough to keep</description>
        </item>
        <item>
          <title>Newest Missing Body</title>
          <link>https://example.test/newest</link>
          <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate>
          <description></description>
        </item>
        <item>
          <title>Middle</title>
          <link>https://example.test/middle</link>
          <pubDate>Tue, 01 Jan 2025 00:00:00 GMT</pubDate>
          <description>middle body that is already long enough to keep without fetching the detail page again</description>
        </item>
      </channel>
    </rss>
    """
    detail_html = """
    <html>
      <body>
        <article>
          <h1>Newest Missing Body</h1>
          <p>This is the full detail article body fetched from the source page.</p>
        </article>
      </body>
    </html>
    """
    fetcher = GenericRssFetcher()

    async def fake_safe_get(client, url):
        if url == "https://example.test/feed.xml":
            return DummyResponse(feed_xml, url)
        if url == "https://example.test/newest":
            return DummyResponse(detail_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                feed_url="https://example.test/feed.xml",
                source_id="example_feed",
                limit=2,
                detail_min_chars=50,
            )
        ]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == ["Newest Missing Body", "Middle"]
    assert "full detail article body" in items[0].content
    assert items[0].has_content is True
    assert items[0].raw_data["detail_fetched"] is True


def test_rss_fetcher_skips_detail_fetch_for_already_stored_articles():
    # 回归 #1：已入库且已有正文的重复条目，不应再访问正文 URL（抓取慢的主因）；
    # 缺席（全新）或已存在但空正文的条目仍要抓取正文，以保留旧空正文的回填语义。
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Example Feed</title>
        <item>
          <title>Stored With Body</title>
          <link>https://example.test/stored-full</link>
          <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate>
          <description>short</description>
        </item>
        <item>
          <title>Stored But Empty</title>
          <link>https://example.test/stored-empty</link>
          <pubDate>Tue, 01 Jan 2025 00:00:00 GMT</pubDate>
          <description>short</description>
        </item>
        <item>
          <title>Brand New</title>
          <link>https://example.test/brand-new</link>
          <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
          <description>short</description>
        </item>
      </channel>
    </rss>
    """
    detail_html = """
    <html><body><article><h1>Detail</h1>
    <p>This is the full detail article body fetched from the source page for backfill.</p>
    </article></body></html>
    """
    fetcher = GenericRssFetcher()

    stored_full_id = fetcher._entry_id("example_feed", {"link": "https://example.test/stored-full"})
    stored_empty_id = fetcher._entry_id("example_feed", {"link": "https://example.test/stored-empty"})

    async def fake_dedup_lookup(item_ids):
        ids = list(item_ids)
        # 全部 id 都应被预检（一次批量查询）
        assert stored_full_id in ids and stored_empty_id in ids
        return {stored_full_id: True, stored_empty_id: False}

    fetcher.dedup_lookup = fake_dedup_lookup

    detail_requests = []

    async def fake_safe_get(client, url):
        if url == "https://example.test/feed.xml":
            return DummyResponse(feed_xml, url)
        detail_requests.append(url)
        return DummyResponse(detail_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                feed_url="https://example.test/feed.xml",
                source_id="example_feed",
                limit=3,
                detail_min_chars=50,
            )
        ]

    items = asyncio.run(collect_items())

    # 已存在且有正文的条目：跳过正文请求，正文沿用 RSS summary。
    stored_full = next(i for i in items if i.id == stored_full_id)
    assert "https://example.test/stored-full" not in detail_requests
    assert stored_full.raw_data["detail_fetched"] is False

    # 已存在但空正文 + 全新条目：仍抓取正文（回填语义保留）。
    assert "https://example.test/stored-empty" in detail_requests
    assert "https://example.test/brand-new" in detail_requests
    stored_empty = next(i for i in items if i.id == stored_empty_id)
    assert "full detail article body" in stored_empty.content


def test_rss_fetcher_without_dedup_hook_fetches_detail_as_before():
    # 未注入去重钩子时（dedup_lookup=None），行为与改动前一致：短正文条目照常抓详情。
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Example Feed</title>
        <item>
          <title>Needs Detail</title>
          <link>https://example.test/needs-detail</link>
          <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate>
          <description>short</description>
        </item>
      </channel>
    </rss>
    """
    detail_html = """
    <html><body><article><h1>Detail</h1>
    <p>This is the full detail article body fetched from the source page.</p>
    </article></body></html>
    """
    fetcher = GenericRssFetcher()
    detail_requests = []

    async def fake_safe_get(client, url):
        if url == "https://example.test/feed.xml":
            return DummyResponse(feed_xml, url)
        detail_requests.append(url)
        return DummyResponse(detail_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                feed_url="https://example.test/feed.xml",
                source_id="example_feed",
                limit=1,
                detail_min_chars=50,
            )
        ]

    items = asyncio.run(collect_items())
    assert "https://example.test/needs-detail" in detail_requests
    assert "full detail article body" in items[0].content


def test_database_storage_existing_content_flags_reports_presence_and_body():
    from models.content import RssArticleContent
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    full = RssArticleContent(
        id="feed_full", title="Full", source_url="https://e.test/full",
        publish_date="2026-01-01T00:00:00+00:00", content="a real body", has_content=True,
    )
    empty = RssArticleContent(
        id="feed_empty", title="Empty", source_url="https://e.test/empty",
        publish_date="2026-01-01T00:00:00+00:00", content="", has_content=False,
    )
    asyncio.run(sink.save(full))
    asyncio.run(sink.save(empty))

    flags = asyncio.run(sink.existing_content_flags(["feed_full", "feed_empty", "feed_absent"]))
    assert flags == {"feed_full": True, "feed_empty": False}  # 缺席 id 不出现在结果里
    assert asyncio.run(sink.existing_content_flags([])) == {}


def test_openai_news_uses_current_official_feed():
    assert OpenAINewsRssFetcher.feed_url == "https://openai.com/news/rss.xml"


def _openai_feed_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>OpenAI</title>
      <item>
        <title>Rendered Article</title>
        <link>https://openai.com/index/rendered-article</link>
        <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate>
        <description>Short human-written summary for the rendered article.</description>
      </item>
      <item>
        <title>Challenge Blocked Article</title>
        <link>https://openai.com/index/blocked-article</link>
        <pubDate>Tue, 01 Jan 2025 00:00:00 GMT</pubDate>
        <description>Short summary that must survive as fallback.</description>
      </item>
    </channel></rss>
    """


def test_openai_news_uses_playwright_detail_and_falls_back_to_summary():
    # OpenAI 文章页有 Cloudflare 挑战：渲染成功的条目应拿到完整正文；渲染失败（挑战未过）
    # 的条目应优雅降级为 RSS summary，而不是空正文或报错。
    rendered_html = (
        "<html><body><article><h1>Rendered Article</h1><p>"
        + "This is the full rendered OpenAI article body obtained after the challenge. " * 6
        + "</p></article></body></html>"
    )

    fetcher = OpenAINewsRssFetcher()

    rendered_urls = []

    async def fake_render(url):
        rendered_urls.append(url)
        # 第一篇渲染成功，第二篇渲染失败（返回空 → 触发降级）。
        return rendered_html if url.endswith("rendered-article") else ""

    fetcher._render_override = fake_render

    async def fake_safe_get(client, url, **kwargs):
        if url.endswith("rss.xml"):
            return DummyResponse(_openai_feed_xml(), url)
        # httpx 详情降级路径同样取不到正文（模拟被 CF 拦截）。
        return None

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                limit=2,
                fetch_detail_if_missing=True,
                detail_min_chars=200,
                detail_max_chars=12000,
            )
        ]

    items = asyncio.run(collect_items())
    by_title = {item.title: item for item in items}

    # 渲染成功：完整正文，方法标记为 playwright。
    rendered = by_title["Rendered Article"]
    assert "full rendered OpenAI article body" in rendered.content
    assert len(rendered.content) > 200
    assert rendered.raw_data["detail_extraction_method"].startswith("playwright")

    # 渲染失败：降级回 RSS summary，正文不空、不报错。
    blocked = by_title["Challenge Blocked Article"]
    assert blocked.content == "Short summary that must survive as fallback."
    assert blocked.has_content is True

    # 两篇都尝试过渲染。
    assert rendered_urls == [
        "https://openai.com/index/rendered-article",
        "https://openai.com/index/blocked-article",
    ]


def test_openai_news_skips_browser_when_detail_disabled():
    # 关闭详情抓取时不应启动浏览器渲染路径（renderer 不被触碰），直接走 summary。
    fetcher = OpenAINewsRssFetcher()

    async def fail_render(url):  # 不应被调用
        raise AssertionError("renderer should not run when detail fetch is disabled")

    fetcher._render_override = fail_render

    async def fake_safe_get(client, url, **kwargs):
        if url.endswith("rss.xml"):
            return DummyResponse(_openai_feed_xml(), url)
        raise AssertionError(f"Unexpected detail fetch: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None, limit=2, fetch_detail_if_missing=False
            )
        ]

    items = asyncio.run(collect_items())
    assert items[0].content == "Short human-written summary for the rendered article."


def test_openai_news_strips_loading_placeholder_only_on_isolated_lines():
    fetcher = OpenAINewsRssFetcher()
    # 标题与正文之间的孤立 "Loading…"/"Loading..." 占位行被剔除。
    assert fetcher._strip_render_placeholders("Title\nLoading…\nBody.") == "Title\nBody."
    assert fetcher._strip_render_placeholders("Title\nLoading...\nBody.") == "Title\nBody."
    assert fetcher._strip_render_placeholders("Head\n   Loading…  \nTail") == "Head\nTail"
    # 正文中合法的 "Loading ..." 句子不被误删。
    keep = "Loading the model takes time.\nReal body."
    assert fetcher._strip_render_placeholders(keep) == keep


def test_openai_news_rendered_body_drops_loading_placeholder():
    # 端到端：渲染快照里夹了 "Loading…" 占位行，入库正文不应包含它。
    rendered_html = (
        "<html><body><article><h1>Rendered Article</h1>"
        "<p>Loading…</p><p>"
        + "This is the full rendered OpenAI article body obtained after the challenge. " * 6
        + "</p></article></body></html>"
    )
    fetcher = OpenAINewsRssFetcher()

    async def fake_render(url):
        return rendered_html if url.endswith("rendered-article") else ""

    fetcher._render_override = fake_render

    async def fake_safe_get(client, url, **kwargs):
        if url.endswith("rss.xml"):
            return DummyResponse(_openai_feed_xml(), url)
        return None

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None, limit=1, fetch_detail_if_missing=True, detail_min_chars=200, detail_max_chars=12000
            )
        ]

    rendered = asyncio.run(collect_items())[0]
    assert "full rendered OpenAI article body" in rendered.content
    assert "Loading" not in rendered.content


def _rendered_article_html(marker="Body text from the renderer. "):
    return (
        "<html><body><article><h1>Rendered</h1><p>"
        + marker * 20
        + "</p></article></body></html>"
    )


class _FakeC4ABackend:
    """伪 crawl4ai 渲染后端：URL 含 'good' 时返回正文 HTML，否则空（模拟 CF 未过）。"""

    def __init__(self):
        self.calls = []

    async def render_html(self, url, *, wait_for=None, wait_for_timeout=15_000):
        self.calls.append((url, wait_for))
        return _rendered_article_html() if "good" in url else ""


def test_openai_news_prefers_crawl4ai():
    # crawl4ai 命中：方法标记为 crawl4ai_*，且确实传入了 CF 等待条件。
    fetcher = OpenAINewsRssFetcher()
    backend = _FakeC4ABackend()
    fetcher._crawl4ai_backend = backend

    detail = asyncio.run(
        fetcher._detail_for_url(None, "https://openai.com/index/good-article", 12000, 200)
    )
    assert detail["method"].startswith("crawl4ai")
    assert "Body text from the renderer" in detail["text"]
    assert backend.calls and backend.calls[0][1] == OpenAINewsRssFetcher._CRAWL4AI_WAIT


def test_openai_news_falls_back_to_playwright_when_crawl4ai_empty():
    # crawl4ai 渲染拿不到正文 → 退回 Playwright（原能跑通的方式）→ 方法标记 playwright_*。
    fetcher = OpenAINewsRssFetcher()
    fetcher._crawl4ai_backend = _FakeC4ABackend()  # 'good' 不在 url 中 → 返回 ""

    pw_calls = []

    class _FakeRenderer:
        available = True

        async def render(self, url):
            pw_calls.append(url)
            return _rendered_article_html("Playwright rendered body. ")

    async def fake_ensure():
        return _FakeRenderer()

    fetcher._ensure_playwright = fake_ensure

    detail = asyncio.run(
        fetcher._detail_for_url(None, "https://openai.com/index/blocked", 12000, 200)
    )
    assert detail["method"].startswith("playwright")
    assert "Playwright rendered body" in detail["text"]
    assert pw_calls == ["https://openai.com/index/blocked"]


def test_openai_news_falls_back_to_summary_when_all_renderers_fail():
    # crawl4ai 空 + Playwright 不可用 + httpx 详情取不到 → 返回空详情，交由通用逻辑降级 summary。
    fetcher = OpenAINewsRssFetcher()
    fetcher._crawl4ai_backend = _FakeC4ABackend()  # 返回 ""

    async def ensure_none():
        return None

    fetcher._ensure_playwright = ensure_none

    async def safe_get_none(client, url, **kwargs):
        return None

    fetcher._safe_get = safe_get_none

    detail = asyncio.run(
        fetcher._detail_for_url(None, "https://openai.com/index/blocked", 12000, 200)
    )
    assert detail["text"] == ""


def test_openai_news_strips_trailing_related_and_byline():
    # OpenAI 文章 <article> 内，正文块之后的署名 section 与 "Keep reading" 相关推荐应被剔除，
    # 正文块之前的标题/导语保留。
    fetcher = OpenAINewsRssFetcher()
    html = (
        "<html><body><article>"
        "<div>June 23, 2026 Applied AI The Real Title Intro lead paragraph.</div>"
        "<div>" + ("Actual article body sentence. " * 40) + "Final real sentence.</div>"
        "<section>2026 GPT Author OpenAI</section>"
        "<div><h2>Keep reading</h2><a>View all</a><p>Related article one</p></div>"
        "</article></body></html>"
    )
    cleaned = fetcher._strip_openai_trailers(html)
    assert "Keep reading" not in cleaned
    assert "Related article one" not in cleaned
    assert "Author OpenAI" not in cleaned
    # 正文与导语保留
    assert "Final real sentence." in cleaned
    assert "Intro lead paragraph." in cleaned


def test_openai_news_trailer_strip_is_noop_without_article():
    # 无 <article> 或子节点过少时原样返回，不抛断。
    fetcher = OpenAINewsRssFetcher()
    assert fetcher._strip_openai_trailers("") == ""
    plain = "<html><body><main><p>No article tag here at all.</p></main></body></html>"
    assert fetcher._strip_openai_trailers(plain) == plain


def test_google_gemini_models_uses_category_rss():
    assert GoogleGeminiModelsRssFetcher.feed_url == "https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/"


def test_full_text_rss_preset_uses_feed_body_without_detail_request():
    # Simon Willison 的 entries Atom 是全文源，类默认应关闭详情抓取。
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Simon Willison's Weblog</title>
      <entry>
        <title>Full-text entry</title>
        <id>https://simonwillison.net/2026/full-text-entry/</id>
        <link href="https://simonwillison.net/2026/full-text-entry/" />
        <updated>2026-07-17T00:00:00Z</updated>
        <content type="html">&lt;p&gt;This complete Atom article body must be kept without fetching its detail page.&lt;/p&gt;</content>
      </entry>
    </feed>
    """
    fetcher = SimonWillisonRssFetcher()
    requested_urls = []

    async def fake_safe_get(client, url):
        requested_urls.append(url)
        if url == fetcher.feed_url:
            return DummyResponse(feed_xml, url)
        raise AssertionError(f"全文 feed 不应请求详情页: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=1)]

    items = asyncio.run(collect_items())

    assert fetcher.default_fetch_detail_if_missing is False
    assert requested_urls == [fetcher.feed_url]
    assert items[0].content == "This complete Atom article body must be kept without fetching its detail page."
    assert items[0].raw_data["detail_fetched"] is False


def test_summary_rss_preset_backfills_body_from_detail_page():
    # DeepMind 是摘要 feed，继承默认详情抓取开关并用文章页正文回填。
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Google DeepMind Blog</title>
      <item>
        <title>Summary-only entry</title>
        <link>https://deepmind.google/blog/summary-only-entry/</link>
        <pubDate>Fri, 17 Jul 2026 00:00:00 GMT</pubDate>
        <description>Short feed summary.</description>
      </item>
    </channel></rss>
    """
    detail_html = (
        "<html><body><article><h1>Summary-only entry</h1><p>"
        + "This full DeepMind article body is fetched from the detail page and replaces the short summary. " * 4
        + "</p></article></body></html>"
    )
    fetcher = GoogleDeepMindBlogRssFetcher()
    detail_url = "https://deepmind.google/blog/summary-only-entry/"
    requested_urls = []

    async def fake_safe_get(client, url):
        requested_urls.append(url)
        if url == fetcher.feed_url:
            return DummyResponse(feed_xml, url)
        if url == detail_url:
            return DummyResponse(detail_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=1)]

    items = asyncio.run(collect_items())

    assert fetcher.default_fetch_detail_if_missing is True
    assert requested_urls == [fetcher.feed_url, detail_url]
    assert "full DeepMind article body" in items[0].content
    assert items[0].raw_data["detail_fetched"] is True


def test_second_expansion_rss_presets_are_registered_with_complete_curation_metadata():
    expansion_classes = (
        GoogleDeepMindBlogRssFetcher,
        MistralNewsRssFetcher,
        HuggingFaceBlogRssFetcher,
        TheDecoderRssFetcher,
        RuanYifengRssFetcher,
        CatalogRssFetcher,
        SimonWillisonRssFetcher,
        LatentSpaceRssFetcher,
        InterconnectsRssFetcher,
        RaschkaRssFetcher,
        OneUsefulThingRssFetcher,
        LilianWengRssFetcher,
        BairBlogRssFetcher,
    )
    expected_ids = {fetcher_class.source_id for fetcher_class in expansion_classes}
    metadata_by_id = {item["id"]: item for item in fetcher_registry.get_all_metadata()}
    required_dimensions = (
        "source_owner",
        "source_brand",
        "source_scope",
        "source_channel",
        "source_url",
        "provenance_tier",
        "signal_strength",
        "noise_risk",
        "fetch_reliability",
    )

    assert expected_ids <= ESSENTIAL_FETCHER_IDS
    assert expected_ids <= metadata_by_id.keys()
    # 观察期(incubating):新节点批次统一在此分类集中观察、不进每日自动采集
    # (curation_policy「Incubation」节)。质量验收转正时,改回各类注释里的
    # 目标分类(official/media/community)并同步更新本断言——这是转正的显式护栏。
    assert {item_id: metadata_by_id[item_id]["category"] for item_id in expected_ids} == {
        "rss_deepmind_blog": "incubating",
        "rss_mistral_news": "incubating",
        "rss_hf_blog": "incubating",
        "rss_the_decoder": "incubating",
        "rss_ruanyifeng": "incubating",
        "rss_testingcatalog": "incubating",
        "rss_simonwillison": "incubating",
        "rss_latent_space": "incubating",
        "rss_interconnects": "incubating",
        "rss_raschka": "incubating",
        "rss_oneusefulthing": "incubating",
        "rss_lilianweng": "incubating",
        "rss_bair_blog": "incubating",
    }
    for fetcher_class in expansion_classes:
        item = metadata_by_id[fetcher_class.source_id]
        assert all(item[dimension] for dimension in required_dimensions)
        assert item["content_tags"]
        assert item["shape"] == "article"
        assert "content_shape" not in fetcher_class.__dict__


def test_database_storage_backfills_existing_empty_article():
    from models.content import RssArticleContent
    from storage.impl.db_storage import DatabaseStorage
    from sqlmodel import Session
    from models.db import ArticleRecord

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    empty = RssArticleContent(
        id="rss_google_deepmind_news_antigravity",
        title="Introducing Google Antigravity 2.0",
        source_url="https://antigravity.google/blog/introducing-google-antigravity-2-0",
        publish_date="2026-05-17T19:43:45+00:00",
        content="",
        has_content=False,
        feed_name="Google DeepMind News",
    )
    filled = RssArticleContent(
        id=empty.id,
        title=empty.title,
        source_url=empty.source_url,
        publish_date=empty.publish_date,
        content="Google Antigravity 2.0 is a standalone desktop application with agent support.",
        has_content=True,
        feed_name=empty.feed_name,
    )

    assert asyncio.run(sink.save(empty)) is True
    assert asyncio.run(sink.save(filled)) is True

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, empty.id)
        assert record.has_content is True
        assert "standalone desktop application" in record.content
        assert record.is_vectorized is False


def _hn_feed_xml():
    # 两类 HN 条目：外链帖（link != 讨论页，summary 只是 URL 模板 + 热度），
    # 站内帖（link == 讨论页，summary 是作者写的真实正文）。
    return """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Hacker News - Newest: "AI"</title>
      <item>
        <title>Alphabet announces $80B AI infra raise</title>
        <link>https://example.test/alphabet</link>
        <comments>https://news.ycombinator.com/item?id=1</comments>
        <pubDate>Tue, 02 Jun 2026 12:00:00 GMT</pubDate>
        <description>&lt;p&gt;Article URL: &lt;a href="https://example.test/alphabet"&gt;link&lt;/a&gt;&lt;/p&gt;
&lt;p&gt;Comments URL: &lt;a href="https://news.ycombinator.com/item?id=1"&gt;c&lt;/a&gt;&lt;/p&gt;
&lt;p&gt;Points: 144&lt;/p&gt;
&lt;p&gt;# Comments: 48&lt;/p&gt;</description>
      </item>
      <item>
        <title>Ask HN: I'm done using AI</title>
        <link>https://news.ycombinator.com/item?id=2</link>
        <comments>https://news.ycombinator.com/item?id=2</comments>
        <pubDate>Tue, 02 Jun 2026 11:00:00 GMT</pubDate>
        <description>&lt;p&gt;I think AI tooling is quietly changing how I work, and here is my long take on it.&lt;/p&gt;</description>
      </item>
    </channel></rss>
    """


def _run_hn_capturing_url(**run_kwargs):
    """运行 HN 抓取器并捕获实际请求的 feed_url（不触发详情抓取/联网）。"""
    fetcher = HackerNewsAiRssFetcher()
    captured = {}

    async def fake_safe_get(client, url, **kwargs):
        captured["url"] = url
        return DummyResponse(_hn_feed_xml(), url=url)

    fetcher._safe_get = fake_safe_get

    async def collect():
        items = []
        async for item in fetcher._run(None, fetch_detail_if_missing=False, **run_kwargs):
            items.append(item)
        return items

    items = asyncio.run(collect())
    return captured.get("url", ""), items


def test_hn_ai_applies_default_points_threshold_to_filter_noise():
    url, items = _run_hn_capturing_url()
    # 默认门槛把 points=10 注入查询串，把 q=AI 的无过滤 firehose 收敛为社区已投票的提交。
    assert "q=AI" in url
    assert "points=10" in url
    assert {i.title for i in items} == {
        "Alphabet announces $80B AI infra raise",
        "Ask HN: I'm done using AI",
    }


def test_hn_external_link_post_becomes_discovery_entry():
    # 外链帖：正文留空（纯发现条目），但保留外链、讨论页与社区热度元数据。
    _, items = _run_hn_capturing_url()
    ext = next(i for i in items if i.title == "Alphabet announces $80B AI infra raise")
    assert ext.has_content is False
    assert ext.content == ""
    assert ext.source_url == "https://example.test/alphabet"
    assert ext.raw_data["discussion_url"] == "https://news.ycombinator.com/item?id=1"
    assert ext.raw_data["hn_points"] == 144
    assert ext.raw_data["hn_num_comments"] == 48


def test_hn_self_post_keeps_author_body():
    # 站内帖（Ask/Show/Tell HN，link == 讨论页）：summary 是作者正文，必须保留。
    _, items = _run_hn_capturing_url()
    self_post = next(i for i in items if i.title == "Ask HN: I'm done using AI")
    assert self_post.has_content is True
    assert "AI tooling is quietly changing how I work" in self_post.content


def test_hn_keeps_body_when_external_detail_actually_fetched():
    # 用户手动开启外链详情抓取且成功时，外链帖正文应保留（不再降级）。
    fetcher = HackerNewsAiRssFetcher()
    entry = {
        "link": "https://example.test/alphabet",
        "comments": "https://news.ycombinator.com/item?id=1",
    }
    kept = fetcher._finalize_content_text(entry, "real fetched body", detail_text="real fetched body")
    assert kept == "real fetched body"


def test_hn_default_disables_external_detail_fetch():
    # 参数固化波:「外链贴不抓正文」是设计本身,固化为类默认,不再暴露 schema。
    assert HackerNewsAiRssFetcher.default_fetch_detail_if_missing is False


def test_hn_ai_honors_custom_points_and_comments_thresholds():
    url, _ = _run_hn_capturing_url(min_points=30, min_comments=5)
    assert "points=30" in url
    assert "comments=5" in url


def test_hn_ai_zero_thresholds_fall_back_to_unfiltered_query():
    url, _ = _run_hn_capturing_url(min_points=0, min_comments=0)
    assert url.endswith("?q=AI")
    assert "points=" not in url
    assert "comments=" not in url


def test_hn_ai_parameter_schema_is_limit_only():
    # 参数固化波:去噪门槛固化为类默认(10 分/0 评),schema 只剩 limit;调整 = 改代码。
    fields = {f["field"] for f in HackerNewsAiRssFetcher.get_parameter_schema()}
    assert fields == {"limit"}
    assert HackerNewsAiRssFetcher.default_min_points == 10
    assert HackerNewsAiRssFetcher.default_min_comments == 0
