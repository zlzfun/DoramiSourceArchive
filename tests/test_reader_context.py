"""reader 问答上下文组装（阶段4 D11 编排下沉;v3.30 检索扶正波改版;v3.32 范围四档）。

直接单测 reader_ai.assemble_reader_context 的分支，脱离 HTTP 请求与 LLM：
article/articles 显式名单（编号上下文 + sources 同源同序）、subscription/all
委托注入的 search_fetch（检索管线本体单测在 test_reader_search.py）。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services import reader_ai  # noqa: E402


def _rec(article_id, title, content, source_id="rss_x"):
    return SimpleNamespace(
        id=article_id, title=title, content=content, source_id=source_id,
        source_url=f"https://example.test/{article_id}", publish_date="2026-08-01T00:00:00",
    )


class _FakeDb:
    def __init__(self, records):
        # records: dict(id → record) 或单条记录（旧式便捷）
        self._records = records if isinstance(records, dict) else {"__any__": records}

    async def get(self, article_id):
        if "__any__" in self._records:
            return self._records["__any__"]
        return self._records.get(article_id)


def _run(**kwargs):
    return asyncio.run(reader_ai.assemble_reader_context(**kwargs))


async def _noop_search(question, username):
    return "", []


_BASE = dict(
    question="q",
    username="u",
    search_fetch=_noop_search,
)


def test_article_scope_uses_body_and_returns_source():
    rec = _rec("a1", "标题", "正文内容")
    ctx, sources = _run(**{**_BASE, "scope": "article", "article_id": "a1", "db_sink": _FakeDb(rec)})
    # 编号上下文（[1] 即引用锚）+ sources 同源：单篇也带出处（供行内引用/出处列表）
    assert ctx.startswith("[1] 标题")
    assert "正文内容" in ctx
    assert [s["id"] for s in sources] == ["a1"]
    assert sources[0]["source_name"]  # 来源名经 friendly_source_name 兜底非空
    assert sources[0]["publish_date"] == "2026-08-01"


def test_article_scope_missing_id_raises_400():
    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "article", "article_id": None, "db_sink": _FakeDb(None)})
    assert ei.value.status_code == 400


def test_article_scope_missing_record_raises_404():
    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "article", "article_id": "x", "db_sink": _FakeDb(None)})
    assert ei.value.status_code == 404


def test_articles_scope_numbers_multiple_records():
    db = _FakeDb({
        "a1": _rec("a1", "第一篇", "甲正文"),
        "a2": _rec("a2", "第二篇", "乙正文"),
    })
    ctx, sources = _run(**{**_BASE, "scope": "articles",
                           "article_id": None, "article_ids": ["a1", "a2", "a1"],  # 重复去重
                           "db_sink": db})
    assert "[1] 第一篇" in ctx and "[2] 第二篇" in ctx
    assert [s["id"] for s in sources] == ["a1", "a2"]


def test_articles_scope_skips_missing_and_caps():
    db = _FakeDb({"a1": _rec("a1", "存活", "正文")})
    ids = ["gone1", "a1", "gone2"] + [f"pad{i}" for i in range(20)]
    ctx, sources = _run(**{**_BASE, "scope": "articles",
                           "article_id": None, "article_ids": ids, "db_sink": db})
    # 缺失 id 跳过；名单超上限截断（pad* 全缺失不致 404，因 a1 在上限窗口内存活）
    assert [s["id"] for s in sources] == ["a1"]

    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "articles",
                "article_id": None, "article_ids": ["gone"], "db_sink": db})
    assert ei.value.status_code == 404

    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "articles",
                "article_id": None, "article_ids": [], "db_sink": db})
    assert ei.value.status_code == 400


def test_search_scopes_delegate_to_search_fetch():
    async def fake_search(question, username):
        assert question == "q" and username == "u"
        return "检索召回上下文", [{"title": "s1"}]

    for scope in ("subscription", "all"):
        ctx, sources = _run(**{**_BASE, "scope": scope, "article_id": None,
                               "db_sink": _FakeDb(None), "search_fetch": fake_search})
        assert ctx == "检索召回上下文"
        assert sources == [{"title": "s1"}]
