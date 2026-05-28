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
