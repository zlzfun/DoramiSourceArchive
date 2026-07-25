import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup

from fetchers.impl.article_extractor import (
    extract_article_detail,
    extract_detail_from_html,
    node_to_markdown,
)


class DummyResponse:
    def __init__(self, text: str, url: str, content_type: str = "text/plain", status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def test_node_to_markdown_preserves_images_lists_and_paragraphs():
    html = """
    <div class="post_content">
      <p>第一段 <a href="/x">链接</a> 后文。</p>
      <p><img src="/img/a.png" alt="图A"></p>
      <ul><li>要点一</li><li>要点二</li></ul>
      <h2>小标题</h2>
      <p>结尾段。</p>
    </div>
    """
    node = BeautifulSoup(html, "html.parser").select_one(".post_content")
    md = node_to_markdown(node, "https://it.com/post/1.htm")

    # 图片解析为绝对 URL 的 markdown 语法
    assert "![图A](https://it.com/img/a.png)" in md
    # 相对链接解析为绝对 URL
    assert "[链接](https://it.com/x)" in md
    # 列表项保留为 markdown bullet
    assert "- 要点一" in md
    assert "- 要点二" in md
    # 标题保留
    assert "## 小标题" in md
    # 段落以空行分隔，而非挤成一段
    assert "\n\n" in md


def test_node_to_markdown_prefers_lazy_attr_over_placeholder_src():
    # IT之家式懒加载：src 是 1px 占位图，真实地址在 data-original
    html = (
        '<div><img class="lazy" src="//img.ithome.com/images/v2/t.png" '
        'data-original="https://img.ithome.com/x/real.jpg" alt="配图"></div>'
    )
    node = BeautifulSoup(html, "html.parser").select_one("div")
    md = node_to_markdown(node, "https://www.ithome.com/0/1/2.htm")
    assert "![配图](https://img.ithome.com/x/real.jpg)" in md
    assert "images/v2/t.png" not in md


def test_node_to_markdown_drops_data_uri_and_empty_images():
    html = '<div><img src="data:image/png;base64,AAAA"><img src=""><p>正文</p></div>'
    node = BeautifulSoup(html, "html.parser").select_one("div")
    md = node_to_markdown(node, "https://x.com/a")
    assert "![" not in md
    assert "正文" in md


def test_extract_detail_from_html_keeps_image_markdown():
    html = """
    <html><body><article class="article-body">
      <p>正文段落。</p>
      <figure><img src="https://cdn.example.com/p.jpg" alt="配图"></figure>
    </article></body></html>
    """
    detail = extract_detail_from_html(html, 8000, base_url="https://example.com/post")
    assert detail.method == "html_selector"
    assert "![配图](https://cdn.example.com/p.jpg)" in detail.text


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


def test_node_to_markdown_renders_table_as_gfm_block():
    """<table> 应转成单块 GFM 表格,而非逐文本节点散块(Lil'Log 抽检回归)。"""
    from bs4 import BeautifulSoup
    from fetchers.impl.article_extractor import node_to_markdown

    html = """
    <div>
      <p>before</p>
      <table><tbody>
        <tr><td>File system</td><td>- File discovery: <code>glob</code>, <code>grep</code><br/>- File read: <code>read</code></td></tr>
        <tr><td>Shell</td><td>Run <code>bash</code></td></tr>
      </tbody></table>
      <p>after</p>
    </div>
    """
    md = node_to_markdown(BeautifulSoup(html, "html.parser").div)
    blocks = md.split("\n\n")
    assert blocks[0] == "before" and blocks[-1] == "after"
    table_block = blocks[1]
    lines = table_block.split("\n")
    # 单块表格:首行数据 + 分隔行 + 第二行数据;cell 内 <br> 折叠为「; 」
    assert lines[0].startswith("| File system |")
    assert "glob, grep ; - File read: read" in lines[0]
    assert set(lines[1].replace("|", "").split()) == {"---"}
    assert lines[2] == "| Shell | Run bash |"
    # 逐词散块的旧症状不复现:code 词不再独立成块
    assert "\n\nglob\n\n" not in md


def test_node_to_markdown_single_row_table_kept():
    from bs4 import BeautifulSoup
    from fetchers.impl.article_extractor import node_to_markdown

    md = node_to_markdown(BeautifulSoup(
        "<div><table><tr><td>a</td><td>b</td></tr></table></div>", "html.parser"
    ).div)
    assert md.split("\n") == ["| a | b |", "| --- | --- |"]


def test_node_to_markdown_skips_html_comments():
    """HTML 注释(Reddit feed 的 <!-- SC_OFF --> 等)不得混入正文(Comment 是
    NavigableString 子类,不先判会被当文本 emit)。"""
    from bs4 import BeautifulSoup
    from fetchers.impl.article_extractor import node_to_markdown

    html = "<div><!-- SC_OFF --><p>real <!-- inline note -->body</p><!-- SC_ON --></div>"
    md = node_to_markdown(BeautifulSoup(html, "html.parser").div)
    assert md == "real body"


def test_inline_nested_boundary_whitespace_preserved():
    # 嵌套行内元素内侧的边界空格不得丢失（OpenAI 页 "take shape inAbilene" 黏连问题）
    html = (
        '<div><p><span>take shape in </span>'
        '<a href="https://ex.com/a"><u><span>Abilene, Texas</span></u>'
        '<span class="sr-only">(opens in a new window)</span></a>'
        '<span>, where our first campus…</span></p></div>'
    )
    node = BeautifulSoup(html, "html.parser").div
    md = node_to_markdown(node, "https://openai.com/index/x/")
    assert "take shape in [Abilene, Texas](https://ex.com/a)," in md
    # 视觉隐藏的无障碍文案不进正文/链接文本
    assert "opens in a new window" not in md


def test_inline_bold_and_italic_markers_with_boundary_space():
    # <b>/<strong> 转 **，<em>/<i> 转 *；元素内侧尾随空格垫到标记外，避免与后文黏连
    html = (
        "<div><p><b><span>1. Rates will not go up. </span></b>"
        "<span>Georgia families will not subsidize.</span> "
        "An <em>emphasis</em> word.</p></div>"
    )
    node = BeautifulSoup(html, "html.parser").div
    md = node_to_markdown(node, "https://x.com/a")
    assert "**1. Rates will not go up.** Georgia families" in md
    assert "An *emphasis* word." in md


def test_compact_text_strips_zero_width_chars():
    from fetchers.impl.article_extractor import compact_text

    assert compact_text("A⁠B​C﻿D") == "ABCD"


def test_heading_permalink_anchor_stripped():
    # Hugo/Docusaurus 标题自链([#]/¶)不是正文,须剥离(cursor changelog/lilianweng)
    html = (
        '<div><h2><a href="https://cursor.com/changelog#run">#</a>Run Bugbot</h2>'
        '<h1>Harness Design Patterns<a href="https://x.io/p#h">#</a></h1></div>'
    )
    node = BeautifulSoup(html, "html.parser").div
    md = node_to_markdown(node, "https://cursor.com/changelog")
    assert "## Run Bugbot" in md
    assert "# Harness Design Patterns" in md
    assert "[#]" not in md and "](" not in md


def test_google_redirect_link_unwrapped():
    # Google Docs 导出的 google.com/url?q= 包裹链接应解包为真实地址(rss_raschka)
    html = (
        '<div><p>See <a href="https://www.google.com/url?q=https://example.com/post&amp;sa=D&amp;ust=1">'
        'the post</a> here.</p></div>'
    )
    node = BeautifulSoup(html, "html.parser").div
    md = node_to_markdown(node, "https://x.com/a")
    assert "[the post](https://example.com/post)" in md
    assert "google.com/url" not in md


def test_video_player_chrome_skipped():
    # Ghost 视频卡的 <video> 子树与 kg-video-player 控件条(时间码)不是正文
    html = (
        '<div><p>正文前。</p>'
        '<figure class="kg-video-card"><video src="/v.mp4">fallback</video>'
        '<div class="kg-video-player-container"><span>0:00</span><div>/</div><span>1:34</span></div>'
        '<figcaption>视频标题</figcaption></figure>'
        '<p>正文后。</p></div>'
    )
    node = BeautifulSoup(html, "html.parser").div
    md = node_to_markdown(node, "https://x.com/a")
    assert "0:00" not in md and "1:34" not in md and "fallback" not in md
    assert "视频标题" in md and "正文前。" in md and "正文后。" in md
