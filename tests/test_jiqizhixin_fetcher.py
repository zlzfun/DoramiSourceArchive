import asyncio
import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.curated_core_fetcher import JiqizhixinWebsiteFetcher


class DummyResponse:
    def __init__(self, text: str, url: str, content: bytes | None = None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/plain"}


def test_jiqizhixin_sitemap_normalizes_malformed_article_urls():
    fetcher = JiqizhixinWebsiteFetcher()
    sitemap = """
    <urlset>
      <url>
        <loc>http://www.jiqizhixin.com/https://www.jiqizhixin.com/articles/2026-05-27-10</loc>
        <lastmod>2026-05-28T01:00:00+00:00</lastmod>
      </url>
      <url>
        <loc>https://www.jiqizhixin.com/about</loc>
        <lastmod>2026-05-28T01:00:00+00:00</lastmod>
      </url>
    </urlset>
    """

    candidates = fetcher._parse_sitemap_candidates(sitemap, lookback_days=30, scan_limit=10)

    assert candidates == ["https://www.jiqizhixin.com/articles/2026-05-27-10"]


def test_jiqizhixin_fetcher_reads_article_body_through_reader_proxy():
    fetcher = JiqizhixinWebsiteFetcher()
    article_url = "https://www.jiqizhixin.com/articles/2026-05-27-10"
    list_url = "https://www.jiqizhixin.com/articles/2026-05-28"
    sitemap = f"""
    <urlset>
      <url><loc>{list_url}</loc><lastmod>2026-05-28T09:00:00+00:00</lastmod></url>
      <url><loc>http://www.jiqizhixin.com/{article_url}</loc><lastmod>2026-05-28T08:00:00+00:00</lastmod></url>
    </urlset>
    """
    article_body = "这是一篇机器之心正文，覆盖模型、论文和产业动态。" * 20
    reader_article = f"""
Title: AI离爱因斯坦还有多远？诺奖得主Demis Hassabis谈AI时代的科学终极追问 ｜ 机器之心

URL Source: {article_url}

Markdown Content:
{article_body}
"""
    reader_listing = f"""
Title: 文章库 ｜ 机器之心

URL Source: {list_url}

Markdown Content:
机器之心文章库列表
"""

    async def fake_safe_get(client, url):
        if url == fetcher.sitemap_url:
            return DummyResponse("", url, gzip.compress(sitemap.encode("utf-8")))
        if url == fetcher._reader_url(list_url):
            return DummyResponse(reader_listing, url)
        if url == fetcher._reader_url(article_url):
            return DummyResponse(reader_article, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                limit=1,
                article_lookback_days=30,
                sitemap_scan_limit=10,
                detail_max_chars=2000,
            )
        ]

    items = asyncio.run(collect_items())

    assert len(items) == 1
    assert items[0].title.startswith("AI离爱因斯坦还有多远")
    assert items[0].source_url == article_url
    assert "机器之心正文" in items[0].content
    assert items[0].publish_date == "2026-05-27T00:00:00+00:00"
    assert items[0].raw_data["reader_service"] == "r.jina.ai"
    assert items[0].raw_data["listing_source"] == "sitemap_reader_proxy"
