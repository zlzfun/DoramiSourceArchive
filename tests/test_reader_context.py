"""reader 问答三档上下文组装（阶段4 D11 编排下沉）。

直接单测 reader_ai.assemble_reader_context 的 graceful-degrade 分支，脱离 HTTP 请求
与 LLM：article 取正文、subscription+RAG 走注入的 rag_fetch、RAG 关走 recent_fetch。
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


_BASE = dict(
    question="q",
    username="u",
    rag_enabled=False,
    rag_fetch=None,
    recent_fetch=lambda user: [],
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


def test_subscription_scope_rag_enabled_uses_rag_fetch():
    async def fake_rag(question):
        assert question == "q"
        return {"context_text": "RAG 召回上下文", "sources": [{"title": "s1"}]}

    ctx, sources = _run(**{**_BASE, "scope": "subscription", "article_id": None,
                           "db_sink": _FakeDb(None), "rag_enabled": True, "rag_fetch": fake_rag})
    assert ctx == "RAG 召回上下文"
    assert sources == [{"title": "s1"}]


def test_subscription_scope_rag_disabled_uses_recent_fetch():
    recent = [
        SimpleNamespace(title="A", content="正文A", source_id="s", source_url="http://a"),
        SimpleNamespace(title="B", content="正文B", source_id="s", source_url="http://b"),
    ]
    ctx, sources = _run(**{**_BASE, "scope": "subscription", "article_id": None,
                           "db_sink": _FakeDb(None), "rag_enabled": False,
                           "recent_fetch": lambda user: recent})
    assert "A" in ctx and "B" in ctx
    assert [s["source_url"] for s in sources] == ["http://a", "http://b"]
