import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.webpage_fetcher import IThomeAiWebFetcher


class DummyResponse:
    def __init__(self, text: str, url: str = "https://next.ithome.com/ai"):
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/html"}


def test_ithome_ai_fetcher_reads_category_listing_instead_of_broad_rss():
    listing_html = """
    <html>
      <body>
        <div id="list">
          <ul class="bl">
            <li>
              <a href="https://www.ithome.com/0/956/628.htm" class="img">
                <img data-original="https://img.ithome.com/newsuploadfiles/thumbnail/2026/5/956628_240.jpg" />
              </a>
              <div class="c" data-ot="2026-05-28T15:58:13.8230000+08:00">
                <h2><a class="title" href="https://www.ithome.com/0/956/628.htm">马斯克称 SpaceX 与 Anthropic 仅为六个月算力合作，必要时将收回资源</a></h2>
                <div class="m">马斯克澄清 SpaceX 与 Anthropic 的 AI 算力合作仅为期 6 个月。</div>
                <div class="tags"><a>马斯克</a><a>SpaceX</a><a>Anthropic</a></div>
              </div>
            </li>
            <li>
              <a href="https://www.ithome.com/0/956/593.htm" class="img"></a>
              <div class="c" data-ot="2026-05-28T15:22:16.8770000+08:00">
                <h2><a class="title" href="https://www.ithome.com/0/956/593.htm">开发者反馈 Gemini 3.5 AI 删光 2.8 万行代码、搞崩后台、编造修复报告</a></h2>
                <div class="m">开发者反馈谷歌 Gemini 3.5 模型越权删除代码。</div>
                <div class="tags"><a>谷歌</a><a>Gemini</a></div>
              </div>
            </li>
          </ul>
        </div>
        <a class="title" href="https://www.ithome.com/0/001/001.htm">导航区非列表文章</a>
      </body>
    </html>
    """
    fetcher = IThomeAiWebFetcher()

    async def fake_safe_get(client, url):
        assert url == "https://next.ithome.com/ai"
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=5, fetch_detail=False)]

    items = asyncio.run(collect_items())

    assert [item.title for item in items] == [
        "马斯克称 SpaceX 与 Anthropic 仅为六个月算力合作，必要时将收回资源",
        "开发者反馈 Gemini 3.5 AI 删光 2.8 万行代码、搞崩后台、编造修复报告",
    ]
    assert items[0].publish_date == "2026-05-28T07:58:13.823000+00:00"
    assert items[0].raw_data["listing_source"] == "ithome_ai_category_html"
    assert items[0].raw_data["media_url"].endswith("956628_240.jpg")
    assert "Anthropic" in items[0].tags


def test_ithome_detail_uses_post_content_container_without_site_chrome():
    listing_html = """
    <html>
      <body>
        <div id="list">
          <ul class="bl">
            <li>
              <div class="c" data-ot="2026-05-28T15:58:13.8230000+08:00">
                <h2><a class="title" href="https://www.ithome.com/0/956/628.htm">马斯克称 SpaceX 与 Anthropic 仅为六个月算力合作，必要时将收回资源</a></h2>
                <div class="m">列表摘要。</div>
              </div>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """
    detail_html = """
    <html>
      <body>
        <div id="top">首页 IT圈 最会买 设置 日夜间 随系统 浅色 深色</div>
        <div id="dt">
          <div class="content">
            <div class="cv">首页 &gt; 智能时代 &gt; 人工智能</div>
            <h1>马斯克称 SpaceX 与 Anthropic 仅为六个月算力合作，必要时将收回资源</h1>
            <div class="post_content" id="paragraph">
              <div class="tougao-user">感谢IT之家网友投稿。</div>
              <p>马斯克澄清 SpaceX 与 Anthropic 的 AI 算力合作仅为期 6 个月。</p>
              <p>SpaceX 保留因自身需求随时收回算力资源的权利。</p>
              <p class="ad-tips">广告声明：文内含有的对外跳转链接结果仅供参考。</p>
            </div>
            <div class="related_post">相关文章 非正文内容</div>
          </div>
        </div>
        <footer>软媒旗下网站 关于IT之家 联系我们</footer>
      </body>
    </html>
    """
    fetcher = IThomeAiWebFetcher()

    async def fake_safe_get(client, url):
        if url == "https://next.ithome.com/ai":
            return DummyResponse(listing_html, url)
        if url == "https://www.ithome.com/0/956/628.htm":
            return DummyResponse(detail_html, url)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=1, fetch_detail=True)]

    items = asyncio.run(collect_items())

    assert len(items) == 1
    assert items[0].raw_data["detail_extraction_method"] == "ithome_post_content"
    assert items[0].content == (
        "马斯克澄清 SpaceX 与 Anthropic 的 AI 算力合作仅为期 6 个月。\n\n"
        "SpaceX 保留因自身需求随时收回算力资源的权利。"
    )
    assert "首页 IT圈" not in items[0].content
    assert "智能时代" not in items[0].content
    assert "广告声明" not in items[0].content
    assert "相关文章" not in items[0].content
    assert "软媒旗下网站" not in items[0].content


def test_ithome_ai_fetcher_accepts_million_range_article_ids():
    """2026-09-10 起文章 ID 跨过一百万,链接首段目录由 /0/ 变为 /1/(issue #79)。

    旧的「ithome.com/0/」子串匹配会让整页一条都对不上而静默停产;新老两种形状
    都要能进列表,导航区/专题页等非文章链接仍不进。
    """
    listing_html = """
    <html>
      <body>
        <div id="list">
          <ul class="bl">
            <li>
              <a href="https://www.ithome.com/1/002/341.htm" class="img"></a>
              <div class="c" data-ot="2026-09-15T06:45:38.3070000+08:00">
                <h2><a class="title" href="https://www.ithome.com/1/002/341.htm">OpenAI 联合创始人布罗克曼：人类已迈入通用人工智能时代</a></h2>
                <div class="m">在接受 a16z 采访时，布罗克曼表示人类已经迈入通用人工智能时代。</div>
                <div class="tags"><a>OpenAI</a></div>
              </div>
            </li>
            <li>
              <a href="https://www.ithome.com/0/999/999.htm" class="img"></a>
              <div class="c" data-ot="2026-09-14T23:23:11.4670000+08:00">
                <h2><a class="title" href="https://www.ithome.com/0/999/999.htm">旧 ID 形状仍可入列</a></h2>
                <div class="m">摘要。</div>
              </div>
            </li>
            <li>
              <a href="https://www.ithome.com/tags/AI/" class="img"></a>
              <div class="c" data-ot="2026-09-14T21:46:45.9600000+08:00">
                <h2><a class="title" href="https://www.ithome.com/tags/AI/">标签页不是文章</a></h2>
              </div>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """
    fetcher = IThomeAiWebFetcher()

    async def fake_safe_get(client, url):
        return DummyResponse(listing_html, url)

    fetcher._safe_get = fake_safe_get

    async def collect_items():
        return [item async for item in fetcher._run(None, limit=5, fetch_detail=False)]

    items = asyncio.run(collect_items())

    assert [item.source_url for item in items] == [
        "https://www.ithome.com/1/002/341.htm",
        "https://www.ithome.com/0/999/999.htm",
    ]
    assert items[0].publish_date == "2026-09-14T22:45:38.307000+00:00"
    assert not fetcher._matches_article_url("https://www.ithome.com/tags/AI/")
    assert not fetcher._matches_article_url("https://next.ithome.com/1/002/341.htm")
    # 主机与路径分别精确校验:相似域名、路径带多余段、非 .htm 都不是文章
    assert not fetcher._matches_article_url("https://evilithome.com/1/002/341.htm")
    assert not fetcher._matches_article_url("https://www.ithome.com/redirect?to=/1/002/341.htm")
    assert not fetcher._matches_article_url("https://www.ithome.com/1/002/341.htm/extra")
    assert fetcher._matches_article_url("https://ithome.com/1/002/341.htm")
    assert fetcher._matches_article_url("https://www.ithome.com/0/956/628.htm?from=list")


def _page(start, count=30):
    return '<ul class="bl">' + ''.join(
        f'<li><div class="c" data-ot="2026-09-{20-start//30:02d}T12:00:00+08:00">'
        f'<a class="title" href="https://www.ithome.com/1/004/{i:03d}.htm">新闻 {i}</a>'
        '<div class="m">列表摘要</div></div></li>' for i in range(start, start + count)
    ) + '</ul>'


def test_ithome_paginates_past_known_items_and_does_not_spend_new_budget():
    import httpx
    f = IThomeAiWebFetcher()
    calls = []
    async def lookup(ids):
        known = {f._content_id(f'https://www.ithome.com/1/004/{i:03d}.htm'): True for i in range(29)}
        return {i: known[i] for i in ids if i in known}
    async def get(client, url):
        return DummyResponse(_page(0))
    async def post(client, url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={'success': True, 'content': {'count': 30, 'html': _page(30)}}, request=httpx.Request('POST', url))
    f.dedup_lookup = lookup; f._safe_get = get; f._safe_post = post
    async def run(): return [x async for x in f._run(None, limit=3, fetch_detail=False)]
    items = asyncio.run(run())
    assert [x.title for x in items] == ['新闻 29', '新闻 30', '新闻 31']
    assert len(calls) == 1 and 'domain=next&subdomain=ai&ot=' in calls[0]


def test_ithome_short_known_page_ends_without_detail_or_pagination():
    f = IThomeAiWebFetcher()
    async def lookup(ids): return {i: True for i in ids}
    async def get(client, url): return DummyResponse(_page(0, 1))
    async def unexpected(*args, **kwargs): raise AssertionError('unnecessary request')
    f.dedup_lookup = lookup; f._safe_get = get; f._safe_post = unexpected; f._detail_for_url = unexpected
    async def run(): return [x async for x in f._run(None, limit=18)]
    assert asyncio.run(run()) == []


def test_ithome_pagination_failure_is_not_successful_partial_coverage():
    import pytest
    f = IThomeAiWebFetcher()
    async def get(client, url): return DummyResponse(_page(0))
    async def post(*args, **kwargs): return None
    f._safe_get = get; f._safe_post = post
    async def run(): return [x async for x in f._run(None, limit=40, fetch_detail=False)]
    with pytest.raises(RuntimeError, match='第 2 页请求失败'):
        asyncio.run(run())


def test_ithome_known_empty_article_is_retried_for_body():
    f = IThomeAiWebFetcher()
    async def lookup(ids): return {i: False for i in ids}
    async def get(client, url): return DummyResponse(_page(0, 1))
    async def detail(*args): return {'title': '', 'text': 'Recovered body', 'method': 'test'}
    f.dedup_lookup = lookup; f._safe_get = get; f._detail_for_url = detail
    async def run(): return [x async for x in f._run(None, limit=1)]
    assert asyncio.run(run())[0].content == 'Recovered body'


def test_ithome_resumes_after_fully_known_first_page():
    import httpx
    f = IThomeAiWebFetcher()
    async def lookup(ids):
        known = {f._content_id(f'https://www.ithome.com/1/004/{i:03d}.htm'): True for i in range(45)}
        return {i: known[i] for i in ids if i in known}
    async def get(client, url): return DummyResponse(_page(0))
    async def post(client, url, **kwargs):
        return httpx.Response(200, json={'success': True, 'content': {'count': 30, 'html': _page(30)}}, request=httpx.Request('POST', url))
    f.dedup_lookup = lookup; f._safe_get = get; f._safe_post = post
    async def run(): return [x async for x in f._run(None, limit=3, fetch_detail=False)]
    assert [x.title for x in asyncio.run(run())] == ['新闻 45', '新闻 46', '新闻 47']


def test_ithome_repeated_page_fails_instead_of_looping():
    import httpx
    import pytest
    f = IThomeAiWebFetcher()
    async def get(client, url): return DummyResponse(_page(0))
    async def post(client, url, **kwargs):
        return httpx.Response(200, json={'success': True, 'content': {'count': 30, 'html': _page(0)}}, request=httpx.Request('POST', url))
    f._safe_get = get; f._safe_post = post
    async def run(): return [x async for x in f._run(None, limit=60, fetch_detail=False)]
    with pytest.raises(RuntimeError, match='没有新的有效文章链接'):
        asyncio.run(run())
