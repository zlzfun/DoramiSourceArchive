"""WebContentBackend 阶段一测试。

重点：
- import 期不依赖 crawl4ai（项目默认环境没有它）；
- Crawl4AIContentBackend 在 crawl4ai 缺失时优雅降级（不抛断、extract 返回失败结果）；
- LegacyArticleExtractorBackend 用打桩 HTML 离线提取正文；
- 对比指标函数自洽。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.web_content import (  # noqa: E402
    DetailResult,
    LegacyArticleExtractorBackend,
    WebContentBackend,
)
from fetchers.web_content.compare import compare_detail, text_similarity  # noqa: E402
from fetchers.web_content.crawl4ai_backend import Crawl4AIContentBackend  # noqa: E402
from fetchers.web_content.profiles import resolve_profile  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_detail_result_chars():
    assert DetailResult(text="hello").chars == 5
    assert DetailResult().chars == 0


def test_profile_routing():
    assert resolve_profile("https://www.ithome.com/0/967/506.htm").name == "ithome-article"
    assert resolve_profile("https://www.anthropic.com/news/foo").name == "anthropic-article"
    assert resolve_profile("https://claude.com/blog/foo").name == "claude-blog"
    assert resolve_profile("https://cursor.com/changelog/06-18-26").name == "cursor-changelog"
    assert resolve_profile("https://www.qbitai.com/2026/xxx.html").name == "qbitai-article"
    assert resolve_profile("https://aiera.com.cn/2026/06/23/x").name == "aiera-article"
    # 未匹配站点回退默认 Profile
    assert resolve_profile("https://example.com/whatever").name == "default-pruning"


def test_crawl4ai_backend_degrades_when_missing():
    """crawl4ai 未安装时：is_available False，上下文不抛断，extract 返回失败结果。"""
    if Crawl4AIContentBackend.is_available():
        # 安装了 crawl4ai 的环境跳过降级断言（该路径由旁路脚本覆盖）
        return

    async def scenario():
        async with Crawl4AIContentBackend() as backend:
            assert backend.available is False
            res = await backend.extract("https://example.com/x")
            assert isinstance(res, DetailResult)
            assert res.success is False
            assert res.backend == "crawl4ai"

    _run(scenario())


class _StubLegacyBackend(LegacyArticleExtractorBackend):
    """用打桩 HTML 覆盖网络抓取，离线验证提取链路。"""

    def __init__(self, html: str, **kwargs):
        super().__init__(**kwargs)
        self._html = html

    async def _fetch(self, url):
        return self._html, url, 200


def test_legacy_backend_extracts_offline():
    body = "<p>" + ("哆啦美归档中枢是一个内容聚合平台。" * 20) + "</p>"
    html = f"<html><head><title>测试标题</title></head><body><article>{body}</article></body></html>"

    async def scenario():
        async with _StubLegacyBackend(html) as backend:
            res = await backend.extract("https://example.com/post", detail_min_chars=50)
            assert res.success is True
            assert res.backend == "legacy"
            assert res.status_code == 200
            assert "哆啦美归档中枢" in res.text
            assert res.raw_chars == len(html)
        return res

    _run(scenario())


def test_legacy_backend_handles_fetch_failure():
    async def scenario():
        async with _StubLegacyBackend("") as backend:  # 空 HTML 模拟抓取失败
            res = await backend.extract("https://example.com/post")
            assert res.success is False
            assert res.error == "fetch failed"

    _run(scenario())


def test_text_similarity_and_compare():
    assert text_similarity("", "") == 1.0
    assert text_similarity("abc", "") == 0.0
    assert text_similarity("hello world", "hello   world") > 0.9

    legacy = DetailResult(text="a" * 100, method="html_selector", success=True, backend="legacy")
    crawl = DetailResult(text="a" * 100, method="crawl4ai:x", success=True, backend="crawl4ai",
                         profile_name="x", status_code=200)
    row = compare_detail("案例", "https://example.com", legacy, crawl)
    assert row.similarity == 1.0
    assert row.len_ratio == 1.0
    assert "实质一致" in row.note


def test_backend_is_abstract():
    # WebContentBackend.extract 是抽象方法，不能直接实例化
    import pytest

    with pytest.raises(TypeError):
        WebContentBackend()


def test_render_html_default_noop():
    """render_html 默认无渲染能力（返回空串）；legacy 后端继承该默认，crawl4ai 未装时也返回空串。"""

    async def scenario():
        async with _StubLegacyBackend("<html></html>") as legacy:
            assert await legacy.render_html("https://example.com/x") == ""
        if not Crawl4AIContentBackend.is_available():
            async with Crawl4AIContentBackend() as c4:
                assert await c4.render_html("https://example.com/x") == ""

    _run(scenario())


# —— 阶段四：C 类单页拆条的"httpx 优先 + 渲染兜底" ——

class _FakeResponse:
    def __init__(self, text, url="https://example.com/page"):
        self.text = text
        self.url = url


def _make_single_page_fetcher(rendered: str | None):
    """构造一个最小 SinglePageDocumentFetcher，stub 掉浏览器渲染。"""
    from fetchers.impl.curated_core_fetcher import SinglePageDocumentFetcher

    class _StubSinglePage(SinglePageDocumentFetcher):
        source_id = "test_single_page"
        name = "Test Single Page"
        description = "t"
        icon = "x"
        page_url = "https://example.com/page"

        async def _render_page_html(self, url):  # 不真启浏览器
            return rendered

    return _StubSinglePage()


def _line_segmenter(html: str):
    """把每行非空文本当作一条，用于驱动拆条逻辑。"""
    return [line.strip() for line in html.splitlines() if line.strip()]


def test_segment_fallback_prefers_httpx():
    """httpx 文本能拆出条目时，直接返回，绝不触发浏览器渲染。"""
    fetcher = _make_single_page_fetcher(rendered="R1\nR2\nR3")  # 若被误用会拆出 3 条

    async def scenario():
        resp = _FakeResponse("A\nB")
        return await fetcher._segment_with_render_fallback(resp, _line_segmenter)

    entries = _run(scenario())
    assert entries == ["A", "B"]  # 用的是 httpx 文本，没动用渲染


def test_segment_fallback_uses_render_when_empty():
    """httpx 拿到空壳（拆出 0 条）且渲染可用时，用渲染的原始 HTML 重跑同一 segmenter。"""
    fetcher = _make_single_page_fetcher(rendered="X\nY\nZ")

    async def scenario():
        resp = _FakeResponse("   \n  ")  # 空壳：拆不出条目
        return await fetcher._segment_with_render_fallback(resp, _line_segmenter)

    entries = _run(scenario())
    assert entries == ["X", "Y", "Z"]


def test_segment_fallback_graceful_when_no_browser():
    """httpx 空壳且渲染不可用（未装 crawl4ai → rendered None）时，保持空结果，不抛断。"""
    fetcher = _make_single_page_fetcher(rendered=None)

    async def scenario():
        resp = _FakeResponse("")
        return await fetcher._segment_with_render_fallback(resp, _line_segmenter)

    assert _run(scenario()) == []
