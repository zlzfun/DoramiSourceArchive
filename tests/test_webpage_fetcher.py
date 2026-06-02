import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.webpage_fetcher import (
    AnthropicNewsWebFetcher,
    BaseWebPageListFetcher,
    QwenBlogWebFetcher,
)
from fetchers.impl.curated_core_fetcher import (
    AieraWebsiteFetcher,
    ClaudeCodeChangelogFetcher,
    GemmaReleaseNotesFetcher,
    OpenAiCodexChangelogFetcher,
    QbitAiWebsiteFetcher,
    XAiDeveloperReleaseNotesFetcher,
    ZaiNewReleasedFetcher,
)


class DummyResponse:
    def __init__(self, text: str, url: str = "https://example.test/news"):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/html"}

    def json(self):
        return json.loads(self.text)


class ExampleNewsFetcher(BaseWebPageListFetcher):
    source_id = "web_example_news"
    name = "Example News"
    listing_url = "https://example.test/news"
    site_name = "Example"
    source_section = "News"
    article_url_patterns = ["example.test/news/"]


def test_webpage_fetcher_skips_detail_fetch_for_already_stored_articles():
    # 回归 #1（扩到 webpage 列表节点）：已入库且有正文的条目不再请求详情页；
    # 缺席（全新）或已存在但空正文的条目仍抓详情，保留空正文回填语义。
    listing_html = """
    <html><body>
      <a href="/news/stored-full"><article><h2>Stored Full</h2></article></a>
      <a href="/news/stored-empty"><article><h2>Stored Empty</h2></article></a>
      <a href="/news/brand-new"><article><h2>Brand New</h2></article></a>
    </body></html>
    """
    detail_html = """
    <html><body><article><h1>Detail</h1>
    <p>This is the full detail article body fetched from the source page, long enough
    for the shared extractor to accept it as a real body and store it for backfill.</p>
    </article></body></html>
    """
    fetcher = ExampleNewsFetcher()

    full_id = fetcher._content_id("https://example.test/news/stored-full")
    empty_id = fetcher._content_id("https://example.test/news/stored-empty")
    looked_up = []

    # webpage 列表节点是 per-item 预检（每条传入单个 id）：已存且有正文返回 True。
    async def fake_dedup_lookup(item_ids):
        ids = list(item_ids)
        looked_up.extend(ids)
        return {i: True for i in ids if i == full_id}

    fetcher.dedup_lookup = fake_dedup_lookup

    detail_requests = []

    async def fake_safe_get(client, url, **kwargs):
        if url == "https://example.test/news":
            return DummyResponse(listing_html, url)
        detail_requests.append(url)
        return DummyResponse(detail_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None, limit=10, fetch_detail=True, detail_max_chars=5000
            )
        ]

    items = asyncio.run(collect_items())
    by_id = {item.id: item for item in items}

    # 已存且有正文：不请求该 url 的详情，detail_fetched 标记为 False。
    assert "https://example.test/news/stored-full" not in detail_requests
    assert by_id[full_id].raw_data["detail_fetched"] is False
    # 已存但空正文 + 全新：仍请求详情（回填语义保留）。
    assert "https://example.test/news/stored-empty" in detail_requests
    assert "https://example.test/news/brand-new" in detail_requests
    assert by_id[empty_id].raw_data["detail_fetched"] is True
    # 每条都在抓详情前经过去重预检。
    assert full_id in looked_up and empty_id in looked_up


def test_webpage_fetcher_without_dedup_hook_fetches_detail_as_before():
    # 未注入去重钩子时（dedup_lookup=None），行为与改动前一致：照常抓详情。
    listing_html = """
    <html><body>
      <a href="/news/needs-detail"><article><h2>Needs Detail</h2></article></a>
    </body></html>
    """
    detail_html = """
    <html><body><article><h1>Detail</h1>
    <p>This is the full detail article body fetched from the source page, long enough
    for the shared extractor to accept it as a real body.</p>
    </article></body></html>
    """
    fetcher = ExampleNewsFetcher()
    detail_requests = []

    async def fake_safe_get(client, url, **kwargs):
        if url == "https://example.test/news":
            return DummyResponse(listing_html, url)
        detail_requests.append(url)
        return DummyResponse(detail_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None, limit=10, fetch_detail=True, detail_max_chars=5000
            )
        ]

    items = asyncio.run(collect_items())
    assert "https://example.test/news/needs-detail" in detail_requests
    assert items[0].raw_data["detail_fetched"] is True


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


def test_anthropic_news_parses_rsc_post_objects_only():
    # 首屏 DOM 锚点会把 "DATE CATEGORY TITLE" 拼进可见文本，并混入导航/页脚噪声；
    # 真正干净且完整（含 See More 之后旧文）的列表在 self.__next_f 的 _type:"post" 对象里。
    posts = [
        {
            "_type": "post",
            "publishedOn": "2026-05-28T17:00:00.000Z",
            "slug": {"_type": "slug", "current": "claude-opus-4-8"},
            "subjects": [
                {"_type": "tag", "label": "Product", "value": "product"},
                {"_type": "tag", "label": "Announcements", "value": "announcements"},
            ],
            "summary": "An upgrade to our Opus class of models.",
            "title": "Introducing Claude Opus 4.8",
        },
        {
            "_type": "post",
            "publishedOn": "2026-05-14T12:00:00.000Z",
            "slug": {"_type": "slug", "current": "mid-screen-update"},
            "subjects": [{"_type": "tag", "label": "Announcements", "value": "announcements"}],
            "summary": "A first-screen boundary article.",
            "title": "Mid screen update",
        },
        {
            # 首屏 See More 之后才显示的旧文——通用锚点抓取拿不到，RSC 流里有。
            "_type": "post",
            "publishedOn": "2021-05-28T00:00:00-07:00",
            "slug": {"_type": "slug", "current": "anthropic-raises-124-million"},
            "subjects": [{"_type": "tag", "label": "Announcements", "value": "announcements"}],
            "summary": "Our Series A announcement.",
            "title": "Anthropic raises $124 million to build more reliable AI systems",
        },
    ]
    rsc_chunk = json.dumps(
        "9:" + json.dumps({"items": posts}, ensure_ascii=False),
        ensure_ascii=False,
    )
    listing_html = (
        "<html><body>"
        "<nav><a href=\"/news#latest\">News</a></nav>"
        # 首屏锚点把日期/分类拼进可见文本，且会被噪声/锚点逻辑污染。
        "<a href=\"/news/claude-opus-4-8\"><article><h2>May 28, 2026 Product Introducing Claude Opus 4.8</h2></article></a>"
        "<a href=\"/careers\">Careers</a>"
        "<footer><a href=\"/news#footer\">More news</a></footer>"
        "<script>self.__next_f.push([1," + rsc_chunk + "])</script>"
        "</body></html>"
    )
    detail_html = (
        "<html><body><article><h1>Detail</h1><p>"
        "This is a full article body from the detail page, long enough for the shared "
        "article extractor to treat it as a real body with enough product, release, and "
        "implementation context for downstream storage and vectorization tests to pass."
        "</p></article></body></html>"
    )

    fetcher = AnthropicNewsWebFetcher()

    async def fake_safe_get(client, url):
        if url == "https://www.anthropic.com/news":
            return DummyResponse(listing_html, url)
        if "anthropic.com/news/" in url:
            return DummyResponse(detail_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                limit=10,
                fetch_detail=True,
                detail_max_chars=2000,
            )
        ]

    items = asyncio.run(collect_items())

    # 三篇 post 全部解析（含首屏外旧文），且按发布时间倒序。
    assert [item.source_url for item in items] == [
        "https://www.anthropic.com/news/claude-opus-4-8",
        "https://www.anthropic.com/news/mid-screen-update",
        "https://www.anthropic.com/news/anthropic-raises-124-million",
    ]
    # 标题干净：不含拼接的 DATE / CATEGORY 前缀。
    assert items[0].title == "Introducing Claude Opus 4.8"
    assert "May 28" not in items[0].title and "Product" not in items[0].title
    # 发布日期取自 publishedOn 而非锚点文本，并统一规范化为 UTC isoformat。
    assert items[0].publish_date == "2026-05-28T17:00:00+00:00"
    assert items[2].publish_date == "2021-05-28T07:00:00+00:00"
    # 分类来自 subjects.label，并入 tags。
    assert "Product" in items[0].tags and "Announcements" in items[0].tags
    assert items[0].raw_data["subjects"] == ["Product", "Announcements"]
    assert items[0].raw_data["listing_source"] == "anthropic_news_rsc"
    # 噪声链接（导航/招聘/页脚锚点）不出现在结果里。
    urls = [item.source_url for item in items]
    assert all("/careers" not in u and "#" not in u for u in urls)


def test_qwen_blog_fetcher_uses_current_article_retrieval_api():
    payload = {
        "success": True,
        "data": {
            "articles": [
                {
                    "id": "479a326d-c932-49ff-a8bb-fe31849529d5",
                    "type": "qwen_ai",
                    "title": "Qwen3.7: The Agent Frontier",
                    "content": "<html><body><article><h1>Qwen3.7-Max</h1><p>Latest proprietary model designed for the agent era.</p></article></body></html>",
                    "path": "qwen3.7",
                    "language": "en-US",
                    "extra": {
                        "introduction": "Today we introduce Qwen3.7-Max.",
                        "tags": ["Release"],
                        "date": "2026-05-20T10:00:00+08:00",
                        "author": "QwenTeam",
                        "readTime": 25,
                        "wordCount": 4992,
                    },
                }
            ]
        },
    }
    fetcher = QwenBlogWebFetcher()

    async def fake_safe_get(client, url, **kwargs):
        assert url == "https://qwen.ai/api/v2/article/retrieval"
        assert kwargs["params"] == {"type": "qwen_ai", "language": "en-US"}
        return DummyResponse(json.dumps(payload), url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [
            item
            async for item in fetcher._run(
                None,
                limit=1,
                fetch_detail=True,
                detail_max_chars=2000,
            )
        ]

    items = asyncio.run(collect_items())

    assert len(items) == 1
    assert items[0].title == "Qwen3.7: The Agent Frontier"
    assert items[0].source_url == "https://qwen.ai/blog?id=qwen3.7"
    assert items[0].publish_date == "2026-05-20T10:00:00+08:00"
    assert "Latest proprietary model" in items[0].content
    assert items[0].raw_data["listing_source"] == "qwen_article_retrieval"
    assert items[0].raw_data["detail_extraction_method"] == "qwen_article_retrieval_html"


def test_qbitai_fetcher_uses_main_article_list_only():
    listing_html = """
    <html>
      <body>
        <nav>
          <a href="https://www.qbitai.com/meet/meet2026/">MEET大会</a>
        </nav>
        <div class="page_top">
          <a href="https://www.qbitai.com/2026/05/426069.html">
            <h3>焦点图文章</h3>
          </a>
        </div>
        <div class="main index_page">
          <div class="content">
            <div class="article_list">
              <div class="picture_text">
                <div class="picture">
                  <a href="https://www.qbitai.com/2026/05/426353.html"><img src="/second.png"></a>
                </div>
                <div class="text_box">
                  <h4><a href="https://www.qbitai.com/2026/05/426353.html">主列表较旧篇</a></h4>
                  <p>较旧篇摘要</p>
                  <div class="info">
                    <span class="author">作者乙</span>
                    <span class="time">4小时前</span>
                    <div class="tags_s"><a href="/tag/infra">AI infra</a></div>
                  </div>
                </div>
              </div>
              <div class="picture_text">
                <div class="picture">
                  <a href="https://www.qbitai.com/2026/05/426366.html"><img src="/latest.png"></a>
                </div>
                <div class="text_box">
                  <h4><a href="https://www.qbitai.com/2026/05/426366.html">主列表较新篇</a></h4>
                  <p>较新篇摘要</p>
                  <div class="info">
                    <span class="author">作者甲</span>
                    <span class="time">33分钟前</span>
                    <div class="tags_s"><a href="/tag/ai">AI</a></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="content_right">
            <div class="yaowen">
              <h3>热门文章</h3>
              <div class="picture_text">
                <a href="https://www.qbitai.com/2026/05/422738.html">
                  <div class="text_box"><h4>侧栏热门旧文</h4></div>
                  <div class="info">2026-05-22</div>
                </a>
              </div>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    fetcher = QbitAiWebsiteFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://www.qbitai.com/category/%E8%B5%84%E8%AE%AF"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=20, fetch_detail=False)]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == ["主列表较新篇", "主列表较旧篇"]
    assert [item.source_url for item in items] == [
        "https://www.qbitai.com/2026/05/426366.html",
        "https://www.qbitai.com/2026/05/426353.html",
    ]
    assert all(item.raw_data["listing_source"] == "qbitai_main_article_list" for item in items)
    assert items[0].raw_data["author"] == "作者甲"
    assert "AI infra" in items[1].tags


def test_aiera_fetcher_uses_main_article_list_dates_and_excludes_sidebar():
    listing_html = """
    <html>
      <body>
        <main id="main" class="site-main hfeed">
          <div class="entries">
            <article class="entry-card post type-post">
              <a class="ct-media-container" href="https://aiera.com.cn/2026/05/29/other/admin/96253/latest/">
                <img src="/latest.jpg" />
              </a>
              <div class="card-content">
                <h2 class="entry-title">
                  <a href="https://aiera.com.cn/2026/05/29/other/admin/96253/latest/" rel="bookmark">主列表较新篇</a>
                </h2>
                <ul class="entry-meta">
                  <li class="meta-date">
                    <span>发布于</span>
                    <time datetime="2026-05-29T08:01:43+08:00">2026年5月29日</time>
                  </li>
                </ul>
                <a class="entry-button" href="https://aiera.com.cn/2026/05/29/other/admin/96253/latest/">点我查看</a>
              </div>
            </article>
            <article class="entry-card post type-post">
              <div class="card-content">
                <h2 class="entry-title">
                  <a href="https://aiera.com.cn/2026/05/28/other/admin/96118/older/" rel="bookmark">主列表较旧篇</a>
                </h2>
                <ul class="entry-meta">
                  <li class="meta-date">
                    <span>发布于</span>
                    <time datetime="2026-05-28T08:02:00+08:00">2026年5月28日</time>
                  </li>
                </ul>
              </div>
            </article>
          </div>

          <aside class="sidebar">
            <h3>爆款文章</h3>
            <article class="wp-block-post">
              <h2>
                <a href="https://aiera.com.cn/2015/12/20/other/aiera-com-cn/14022/hot/">侧栏爆款旧文</a>
              </h2>
              <time datetime="2015-12-20T00:00:00+08:00">2015年12月20日</time>
            </article>
          </aside>
          <nav class="ct-pagination">
            <a class="next page-numbers" rel="next" href="https://aiera.com.cn/page/2/">下一个</a>
          </nav>
        </main>
      </body>
    </html>
    """
    page2_html = """
    <html>
      <body>
        <main id="main" class="site-main hfeed">
          <div class="entries">
            <article class="entry-card post type-post">
              <div class="card-content">
                <h2 class="entry-title">
                  <a href="https://aiera.com.cn/2026/05/28/other/admin/96118/older/" rel="bookmark">主列表较旧篇重复</a>
                </h2>
                <time datetime="2026-05-28T08:02:00+08:00">2026年5月28日</time>
              </div>
            </article>
            <article class="entry-card post type-post">
              <div class="card-content">
                <h2 class="entry-title">
                  <a href="https://aiera.com.cn/2026/05/27/other/admin/95967/page-two/" rel="bookmark">第二页主列表篇</a>
                </h2>
                <time datetime="2026-05-27T08:02:00+08:00">2026年5月27日</time>
              </div>
            </article>
          </div>
        </main>
      </body>
    </html>
    """
    fetcher = AieraWebsiteFetcher()
    fetched_urls = []

    async def fake_safe_get(client, url):
        fetched_urls.append(url)
        if url == "https://aiera.com.cn/":
            return DummyResponse(listing_html, url)
        if url == "https://aiera.com.cn/page/2/":
            return DummyResponse(page2_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=3, fetch_detail=False)]

    items = asyncio.run(collect_items())

    assert fetched_urls == ["https://aiera.com.cn/", "https://aiera.com.cn/page/2/"]
    assert [item.title for item in items] == ["主列表较新篇", "主列表较旧篇", "第二页主列表篇"]
    assert [item.source_url for item in items] == [
        "https://aiera.com.cn/2026/05/29/other/admin/96253/latest",
        "https://aiera.com.cn/2026/05/28/other/admin/96118/older",
        "https://aiera.com.cn/2026/05/27/other/admin/95967/page-two",
    ]
    assert [item.publish_date for item in items] == [
        "2026-05-29T08:01:43+08:00",
        "2026-05-28T08:02:00+08:00",
        "2026-05-27T08:02:00+08:00",
    ]
    assert all(item.raw_data["listing_source"] == "aiera_main_article_list" for item in items)
    assert items[0].raw_data["listing_publish_date"] == "2026年5月29日"
    assert "侧栏爆款旧文" not in [item.title for item in items]


def test_claude_code_changelog_splits_releases_by_version():
    # Mintlify <Update> 组件：每个版本是一个独立块，含 update-label(版本号) /
    # update-description(发布日期) / update-content(更新条目)。fixture 故意把版本顺序打乱，
    # 以验证 fetcher 会按 (发布日期, 版本号) 倒序排序，而不是沿用页面顺序。
    listing_html = """
    <html>
      <body>
        <div id="content-area">
          <h1>Changelog</h1>
          <div class="update-block">
            <div data-component-part="update-label">2.1.156</div>
            <div data-component-part="update-description">May 29, 2026</div>
            <div data-component-part="update-content">
              <ul><li>Fixed a thinking-block API error on Opus 4.8.</li></ul>
            </div>
          </div>
          <div class="update-block">
            <div data-component-part="update-label">2.1.158</div>
            <div data-component-part="update-description">May 30, 2026</div>
            <div data-component-part="update-content">
              <ul>
                <li>Auto mode is now available on Bedrock, Vertex, and Foundry.</li>
                <li>Opt in via CLAUDE_CODE_ENABLE_AUTO.</li>
              </ul>
            </div>
          </div>
          <div class="update-block">
            <div data-component-part="update-label">2.1.157</div>
            <div data-component-part="update-description">May 29, 2026</div>
            <div data-component-part="update-content">
              <ul><li>Plugins in .claude/skills directories are now auto-loaded.</li></ul>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    fetcher = ClaudeCodeChangelogFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://code.claude.com/docs/en/changelog"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    # 每个版本各成一条；按 (发布日期, 版本号) 倒序：5-30 在前，5-29 两条按版本号降序。
    assert [item.raw_data["version"] for item in items] == ["2.1.158", "2.1.157", "2.1.156"]
    assert [item.title for item in items] == [
        "Claude Code 2.1.158",
        "Claude Code 2.1.157",
        "Claude Code 2.1.156",
    ]
    assert [item.publish_date for item in items] == [
        "2026-05-30T00:00:00+00:00",
        "2026-05-29T00:00:00+00:00",
        "2026-05-29T00:00:00+00:00",
    ]
    assert items[0].source_url == "https://code.claude.com/docs/en/changelog#2.1.158"
    # 内容按版本切分，互不串味。
    assert "Auto mode is now available" in items[0].content
    assert "Bedrock" in items[0].content and "thinking-block" not in items[0].content
    assert "auto-loaded" in items[1].content
    assert items[0].raw_data["listing_source"] == "claude_code_changelog_updates"
    assert items[0].raw_data["release_date_text"] == "May 30, 2026"
    assert items[0].raw_data["detail_extraction_method"] == "mintlify_update_component"


def test_zai_new_released_fetcher_splits_updates_by_model():
    listing_html = """
    <html>
      <body>
        <div class="mdx-content">
          <div id="2026-04-07" class="update update-container">
            <div>
              <div>​</div>
              <div>2026-04-07</div>
              <div>GLM-5.1</div>
            </div>
            <ul>
              <li>Designed for long-horizon tasks.</li>
              <li>Improves stability and tool use over extended tasks.</li>
            </ul>
          </div>
          <div id="2026-04-01" class="update update-container">
            <div>
              <div>​</div>
              <div>2026-04-01</div>
              <div>GLM-5V-Turbo</div>
            </div>
            <ul>
              <li>Brings native multimodal understanding to images, video, and text.</li>
            </ul>
          </div>
        </div>
      </body>
    </html>
    """
    fetcher = ZaiNewReleasedFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://docs.z.ai/release-notes/new-released"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == [
        "Z.ai New Released: GLM-5.1",
        "Z.ai New Released: GLM-5V-Turbo",
    ]
    assert [item.publish_date for item in items] == [
        "2026-04-07T00:00:00+00:00",
        "2026-04-01T00:00:00+00:00",
    ]
    assert items[0].source_url == "https://docs.z.ai/release-notes/new-released#2026-04-07"
    assert "long-horizon tasks" in items[0].content
    assert items[0].raw_data["listing_source"] == "zai_new_released_updates"
    assert items[1].raw_data["model_name"] == "GLM-5V-Turbo"


def test_gemma_release_notes_split_by_date_and_anchor_by_id():
    # Gemma 的 <h2> id 是版本名（gemma-4 等），同样按日期标题切分；锚点复用 id。
    listing_html = """
    <html>
      <body>
        <devsite-content>
          <div class="devsite-article-body clearfix">
            <h1>Gemma releases</h1>
            <h2 id="gemma-4-mtp" data-text="April 16, 2026">April 16, 2026</h2>
            <ul><li>Release of Gemma 4 - MTP for E2B, E4B, 31B, and 26B A4B.</li></ul>
            <h2 id="gemma-4" data-text="March 31, 2026">March 31, 2026</h2>
            <ul><li>Release of Gemma 4 in E2B, E4B, 31B and 26B A4B sizes.</li></ul>
          </div>
        </devsite-content>
      </body>
    </html>
    """
    fetcher = GemmaReleaseNotesFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://ai.google.dev/gemma/docs/releases"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == [
        "Gemma: April 16, 2026",
        "Gemma: March 31, 2026",
    ]
    assert items[0].publish_date == "2026-04-16T00:00:00+00:00"
    # 锚点用 <h2> 的 id（版本名），而非日期。
    assert items[0].source_url == "https://ai.google.dev/gemma/docs/releases#gemma-4-mtp"
    assert "Gemma 4 - MTP" in items[0].content
    assert items[0].raw_data["listing_source"] == "gemma_release_notes_updates"
    assert items[0].raw_data["release_heading"] == "April 16, 2026"


def test_openai_codex_changelog_splits_releases_by_list_item():
    # Codex Changelog 按月份 <h2> 分组，但每条发布是一个 <li data-product> 容器：
    # 内含 <time>(ISO 日期) + 首个标题(发布名) + 正文。fixture 打乱日期以验证倒序。
    listing_html = """
    <html>
      <body>
        <main>
          <h1>Codex changelog</h1>
          <h2 id="month-2026-05">May 2026</h2>
          <ul>
            <li id="github-release-329640454" data-product="codex">
              <time>2026-05-26</time>
              <h3>Codex CLI 0.134.0</h3>
              <h2>New Features</h2>
              <p>Added richer diff rendering.</p>
            </li>
            <li id="codex-2026-05-28-app" data-product="codex">
              <time>2026-05-29</time>
              <h3>Computer use and mobile access on Windows 26.527</h3>
              <p>Computer Use now works on Windows.</p>
            </li>
          </ul>
        </main>
      </body>
    </html>
    """
    fetcher = OpenAiCodexChangelogFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://developers.openai.com/codex/changelog"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    # 每个 <li> 各成一条，按 <time> 日期倒序（不沿用页面先后）。
    assert [item.title for item in items] == [
        "OpenAI Codex: Computer use and mobile access on Windows 26.527",
        "OpenAI Codex: Codex CLI 0.134.0",
    ]
    assert [item.publish_date for item in items] == [
        "2026-05-29T00:00:00+00:00",
        "2026-05-26T00:00:00+00:00",
    ]
    assert items[0].source_url == "https://developers.openai.com/codex/changelog#codex-2026-05-28-app"
    # 内容按发布切分，互不串味，且不含重复的日期/标题前缀。
    assert "Computer Use now works on Windows" in items[0].content
    assert "Codex CLI" not in items[0].content
    assert not items[0].content.startswith("2026-05-29")
    assert items[0].raw_data["listing_source"] == "openai_codex_changelog_updates"
    assert items[0].raw_data["detail_extraction_method"] == "openai_changelog_list_item"


def test_xai_release_notes_splits_grid_cards_by_date():
    # xAI Release Notes 是 Mintlify changelog grid：每条发布是一个两列 grid 卡片，
    # 左列(日期，近期用全称 "May 29"、老条目用缩写 "Dec 14"，均无年份)，右列(<h3>+正文)。
    # 年份由前序月份 <h2>(带年份的取其年)给出。fixture 用显式年份的 <h2> 保证确定性，
    # 并打乱顺序以验证按日期倒序。
    listing_html = """
    <html>
      <body>
        <main>
          <h1 id="release-notes-1">Release Notes</h1>
          <h2 id="may-2026">May 2026</h2>
          <div class="grid grid-cols-[5rem_minmax(0,1fr)]">
            <div class="text-muted tabular-nums">May 27</div>
            <div class="min-w-0">
              <h3 id="image-search-in-web-search">Image Search in Web Search</h3>
              <p>Web Search now supports searching for images directly.</p>
            </div>
          </div>
          <div class="grid grid-cols-[5rem_minmax(0,1fr)]">
            <div class="text-muted tabular-nums">May 29</div>
            <div class="min-w-0">
              <h3 id="smart-turn-for-streaming-stt">Smart Turn for Streaming STT</h3>
              <p>An ML model predicts whether the speaker has finished their thought.</p>
            </div>
          </div>
          <h2 id="december-2025">December 2025</h2>
          <div class="grid grid-cols-[5rem_minmax(0,1fr)]">
            <div class="text-muted tabular-nums">Dec 14</div>
            <div class="min-w-0">
              <h3 id="grok-2-1212">Released the new grok-2-1212 models</h3>
              <p>New Grok 2 model and vision variant are now available.</p>
            </div>
          </div>
        </main>
      </body>
    </html>
    """
    fetcher = XAiDeveloperReleaseNotesFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://docs.x.ai/developers/release-notes"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    # 每个 grid 卡片各成一条，按日期倒序（不沿用页面先后）。
    assert [item.title for item in items] == [
        "xAI: Smart Turn for Streaming STT",
        "xAI: Image Search in Web Search",
        "xAI: Released the new grok-2-1212 models",
    ]
    # 缩写月份 "Dec 14" + 显式年份 <h2> "December 2025" → 真实日期。
    assert [item.publish_date for item in items] == [
        "2026-05-29T00:00:00+00:00",
        "2026-05-27T00:00:00+00:00",
        "2025-12-14T00:00:00+00:00",
    ]
    # 锚点用 <h3> 的 id。
    assert items[0].source_url == "https://docs.x.ai/developers/release-notes#smart-turn-for-streaming-stt"
    # 内容按卡片切分，互不串味。
    assert "finished their thought" in items[0].content
    assert "Image Search" not in items[0].content
    assert items[0].raw_data["listing_source"] == "xai_release_notes_updates"
    assert items[0].raw_data["detail_extraction_method"] == "xai_release_notes_grid"
    assert items[0].raw_data["release_date_text"] == "May 29"


def test_xai_release_notes_infers_current_year_for_undated_month_heading():
    # 月份 <h2> 不带年份（"May"）时按当前日期推断：当前年；若该月晚于当月则回退上一年。
    # 用与实现相同的规则计算期望年份，避免对运行时间的硬编码。
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    expected_year = now.year - 1 if 5 > now.month else now.year

    listing_html = """
    <html>
      <body>
        <main>
          <h2 id="may">May</h2>
          <div class="grid grid-cols-[5rem_minmax(0,1fr)]">
            <div class="text-muted tabular-nums">May 29</div>
            <div class="min-w-0">
              <h3 id="smart-turn">Smart Turn for Streaming STT</h3>
              <p>An ML model predicts end of turn.</p>
            </div>
          </div>
        </main>
      </body>
    </html>
    """
    fetcher = XAiDeveloperReleaseNotesFetcher()

    async def fake_safe_get(client, url):
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=10)]

    items = asyncio.run(collect_items())

    assert len(items) == 1
    assert items[0].publish_date == f"{expected_year}-05-29T00:00:00+00:00"
