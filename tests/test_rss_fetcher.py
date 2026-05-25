import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.rss_fetcher import GenericRssFetcher, MicrosoftAiBlogRssFetcher


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


def test_microsoft_ai_blog_uses_current_cloud_blog_feed():
    assert MicrosoftAiBlogRssFetcher.feed_url == "https://www.microsoft.com/en-us/microsoft-cloud/blog/feed/"


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
