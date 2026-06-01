import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.rss_fetcher import GenericRssFetcher, GoogleGeminiModelsRssFetcher, OpenAINewsRssFetcher


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


def test_google_gemini_models_uses_category_rss():
    assert GoogleGeminiModelsRssFetcher.feed_url == "https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/"


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
