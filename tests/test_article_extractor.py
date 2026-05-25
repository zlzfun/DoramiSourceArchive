import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.article_extractor import extract_article_detail


class DummyResponse:
    def __init__(self, text: str, url: str, content_type: str = "text/plain", status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def test_spa_markdown_asset_backfills_article_detail():
    html = """
    <html>
      <head>
        <title>Google Antigravity</title>
        <script src="main.js" type="module"></script>
      </head>
      <body><main><app-root></app-root></main></body>
    </html>
    """
    markdown = """---
title: Introducing Google Antigravity 2.0
date: 2026-05-19
---

# Introducing Google Antigravity 2.0

Antigravity 2.0 is a command center for managing multiple local agents.

- Group conversations into Projects.
- Operate across multiple workspaces.
"""
    seen_urls = []

    async def fake_safe_get(client, url):
        seen_urls.append(url)
        if url == "https://antigravity.google/assets/blog-posts/introducing-google-antigravity-2-0.md":
            return DummyResponse(markdown, url)
        if url == "https://antigravity.google/main.js":
            return DummyResponse('fetch(`/assets/blog-posts/${e}.md`)', url, "text/javascript")
        return None

    async def run():
        return await extract_article_detail(
            None,
            fake_safe_get,
            "https://antigravity.google/blog/introducing-google-antigravity-2-0",
            html,
            12000,
        )

    detail = asyncio.run(run())

    assert detail.method == "markdown_asset"
    assert detail.title == "Introducing Google Antigravity 2.0"
    assert "command center for managing multiple local agents" in detail.text
    assert detail.url == "https://antigravity.google/assets/blog-posts/introducing-google-antigravity-2-0.md"
    assert "https://antigravity.google/assets/blog-posts/introducing-google-antigravity-2-0.md" in seen_urls


def test_spa_markdown_asset_can_be_inferred_from_js_template():
    html = """
    <html>
      <head>
        <title>SPA Article</title>
        <script src="/main.js" type="module"></script>
      </head>
      <body><main><app-root></app-root></main></body>
    </html>
    """
    markdown = "# SPA Article\n\nThis body was loaded from a markdown path inferred from JavaScript."

    async def fake_safe_get(client, url):
        if url == "https://example.test/assets/blog-posts/spa-article.md":
            return None
        if url == "https://example.test/assets/blog/spa-article.md":
            return None
        if url == "https://example.test/assets/posts/spa-article.md":
            return None
        if url == "https://example.test/main.js":
            return DummyResponse('fetch(`/content/articles/${slug}.md`)', url, "text/javascript")
        if url == "https://example.test/content/articles/spa-article.md":
            return DummyResponse(markdown, url)
        return None

    async def run():
        return await extract_article_detail(
            None,
            fake_safe_get,
            "https://example.test/blog/spa-article",
            html,
            12000,
        )

    detail = asyncio.run(run())

    assert detail.method == "markdown_asset"
    assert "loaded from a markdown path inferred" in detail.text
    assert detail.url == "https://example.test/content/articles/spa-article.md"
