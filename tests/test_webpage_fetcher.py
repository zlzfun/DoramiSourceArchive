import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.webpage_fetcher import BaseWebPageListFetcher


class DummyResponse:
    def __init__(self, text: str, url: str = "https://example.test/news"):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/html"}


class ExampleNewsFetcher(BaseWebPageListFetcher):
    source_id = "web_example_news"
    name = "Example News"
    listing_url = "https://example.test/news"
    site_name = "Example"
    source_section = "News"
    article_url_patterns = ["example.test/news/"]


def test_webpage_fetcher_reads_embedded_next_rsc_article_records():
    listing_html = r"""
    <html>
      <body>
        <h1>Latest updates from Example.</h1>
        <a href="/news/latest">
          <article>
            <h2>Latest visible card</h2>
            <p>Product</p>
          </article>
        </a>
        <a href="/news/page/2">2</a>
        <script>
          self.__next_f.push([1,"[{\"id\":\"1\",\"slug\":\"latest\",\"date\":\"2026-04-29T12:00:00\",\"title\":\"Latest visible card\",\"description\":\"Visible summary\"},{\"id\":\"2\",\"slug\":\"workflows\",\"date\":\"2026-04-27T12:00:00\",\"title\":\"Workflows for work\",\"description\":\"Workflow summary\"},{\"id\":\"3\",\"slug\":\"connectors\",\"date\":\"2026-04-15T16:00:00\",\"title\":\"Connectors for Studio\",\"description\":\"Connector summary\"}]"])
        </script>
      </body>
    </html>
    """
    detail_html = """
    <html>
      <body>
        <article>
          <h1>Article Detail</h1>
          <p>This is a full article body from the detail page. It is intentionally long enough for the shared article extractor to accept it as a real body instead of falling back to SPA markdown asset discovery. The paragraph mirrors a normal official news article with enough context, product detail, release timing, and implementation notes for downstream storage and vectorization tests.</p>
        </article>
      </body>
    </html>
    """
    fetcher = ExampleNewsFetcher()

    async def fake_safe_get(client, url):
        if url == "https://example.test/news":
            return DummyResponse(listing_html, url)
        if url.startswith("https://example.test/news/"):
            return DummyResponse(detail_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                limit=3,
                fetch_detail=True,
                detail_max_chars=2000,
            )
        ]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == [
        "Latest visible card",
        "Workflows for work",
        "Connectors for Studio",
    ]
    assert [item.source_url for item in items] == [
        "https://example.test/news/latest",
        "https://example.test/news/workflows",
        "https://example.test/news/connectors",
    ]
    assert "https://example.test/news/page/2" not in [item.source_url for item in items]
    assert items[0].publish_date == "2026-04-29T12:00:00"
    assert items[1].raw_data["listing_source"] == "embedded_json"
    assert all("full article body" in item.content for item in items)
