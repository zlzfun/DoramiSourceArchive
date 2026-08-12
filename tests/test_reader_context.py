"""reader 问答上下文组装（阶段4 D11 编排下沉;v3.30 检索扶正波改版）。

直接单测 reader_ai.assemble_reader_context 的分支，脱离 HTTP 请求与 LLM：
article 取正文、subscription 委托注入的 search_fetch（检索管线本体单测在
test_reader_search.py）。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services import reader_ai  # noqa: E402


class _FakeDb:
    def __init__(self, record):
        self._record = record

    async def get(self, article_id):
        return self._record


def _run(**kwargs):
    return asyncio.run(reader_ai.assemble_reader_context(**kwargs))


async def _noop_search(question, username):
    return "", []


_BASE = dict(
    question="q",
    username="u",
    search_fetch=_noop_search,
)


def test_article_scope_uses_body():
    rec = SimpleNamespace(title="标题", content="正文内容")
    ctx, sources = _run(**{**_BASE, "scope": "article", "article_id": "a1", "db_sink": _FakeDb(rec)})
    assert "标题" in ctx and "正文内容" in ctx
    assert sources == []


def test_article_scope_missing_id_raises_400():
    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "article", "article_id": None, "db_sink": _FakeDb(None)})
    assert ei.value.status_code == 400


def test_article_scope_missing_record_raises_404():
    with pytest.raises(reader_ai.ReaderAIError) as ei:
        _run(**{**_BASE, "scope": "article", "article_id": "x", "db_sink": _FakeDb(None)})
    assert ei.value.status_code == 404


def test_subscription_scope_delegates_to_search_fetch():
    async def fake_search(question, username):
        assert question == "q" and username == "u"
        return "检索召回上下文", [{"title": "s1"}]

    ctx, sources = _run(**{**_BASE, "scope": "subscription", "article_id": None,
                           "db_sink": _FakeDb(None), "search_fetch": fake_search})
    assert ctx == "检索召回上下文"
    assert sources == [{"title": "s1"}]
