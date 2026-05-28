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
