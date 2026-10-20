"""Translation integrity: real Markdown planning and mocked model transport."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import LLMConfig
from llm.client import LLMIncompleteOutput, UsageMeta
from llm import client as llm_client
from services import reader_ai
from services.translation_markdown import (
    TranslationStructureError, plan_translation,
)


CONFIG = LLMConfig(base_url="https://model.invalid/v1", api_key="test", model="test")


def test_literals_and_separators_round_trip():
    body = (
        "# Paper\n\n[zqpl]: #zq1.zq4\n\n## Abstract\n\n"
        + ("A sufficiently long sentence about science. " * 20)
        + "\n\n- First $x+y$ and `a | b`\n- Second [link label](https://a.test/f(a) \"title\")"
        + "\n\n$$\n\\begin{aligned}\nx &= y \\\\\n\nz &= 3\n\\end{aligned}\n$$"
        + "\n\n```python\nprint('$5')\n\n# blank line above\n```\n\n"
        + "![diagram](https://a.test/figure.svg)\n\n[ref]: https://a.test/a(b)"
        + "\n\n| Name | Value |\n| :--- | ---: |\n"
        + "\n".join(f"| Experiment {i} | $x_{{{i}}}$ |" for i in range(20))
    )
    plan = plan_translation(body, segment_chars=180)
    assert len(plan.chunks) > 5
    assert all(len(c.text) <= 180 for c in plan.chunks)
    assert plan.assemble([c.text for c in plan.chunks]) == reader_ai.strip_layout_markers(body)
    masked = "\n".join(c.text for c in plan.chunks)
    assert "https://" not in masked and "print(" not in masked and "\\begin" not in masked
    assert all(not c.text.startswith("label]") for c in plan.chunks)


def test_sentences_without_spaces_and_long_reference_list():
    body = "中文句子。" * 90 + "\n\n" + "\n".join(
        f"- Reference {i}: [A title with words](https://example.org/p({i}))." for i in range(50)
    )
    plan = plan_translation(body, segment_chars=180)
    assert plan.assemble([c.text for c in plan.chunks]) == body
    for chunk in plan.chunks:
        assert chunk.text.count("[") == chunk.text.count("]")


@pytest.mark.parametrize("body", ["| " + "large cell " * 100 + " |", "x" * 1000])
def test_indivisible_oversize_input_fails(body):
    with pytest.raises(TranslationStructureError):
        plan_translation(body, segment_chars=180)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered"])
def test_damaged_literal_rejected(mode):
    plan = plan_translation("First $x$ then `y`.")
    first, second = plan.literals
    text = plan.chunks[0].text
    if mode == "missing":
        text = text.replace(first, "")
    elif mode == "duplicate":
        text += first
    else:
        text = text.replace(first, "TEMP").replace(second, first).replace("TEMP", second)
    with pytest.raises(TranslationStructureError):
        plan.assemble([text])


def test_missing_table_row_and_missing_chunk_rejected():
    plan = plan_translation("| Name | Value |\n| --- | --- |\n| example | 7 |")
    with pytest.raises(TranslationStructureError):
        plan.assemble([plan.chunks[0].text.rsplit("\n", 1)[0]])
    with pytest.raises(TranslationStructureError):
        plan.assemble([])


@pytest.mark.parametrize("body", [
    "OpenAI raised $6.6 billion at a $157 billion valuation.",
    "The plans cost $5-$10.",
])
def test_currency_is_translated_instead_of_hidden_as_math(body):
    plan = plan_translation(body)
    assert not plan.literals
    assert plan.chunks[0].text == body


def test_valid_math_and_wide_display_blocks_stay_protected():
    body = "$x+1$ and $$x$$.\n\n$$$\n\\text{price $5}\n\nx=2\n$$$\n\n\\[\nx=3\n\nz=4\n\\]"
    plan = plan_translation(body)
    assert len(plan.literals) == 4
    assert plan.assemble([c.text for c in plan.chunks]) == body
    assert "x=2" not in plan.chunks[0].text


@pytest.mark.parametrize("prefix,suffix", [
    ("It`s fine.", "Use `pip`."),
    ("Bad [link](https://example.org/unclosed", "The remaining paragraph (still prose)."),
    ("Math $$x", "more prose and y$$ here."),
    ("Math \\(x", "more prose and y\\) here."),
    ("Math \\[x", "more prose and y\\] here."),
    ("$$", "There is no closing delimiter."),
])
def test_unclosed_inline_markup_cannot_swallow_paragraphs(prefix, suffix):
    prose = "This entire paragraph must reach the translator."
    body = prefix + "\n\n" + prose + "\n\n" + suffix
    plan = plan_translation(body)
    assert prose in "\n".join(c.text for c in plan.chunks)
    assert plan.assemble([c.text for c in plan.chunks]) == body


@pytest.mark.parametrize("ending", ["93.5 lambda mu", "et al., 2024 lambda mu"])
def test_long_line_does_not_split_decimals_or_abbreviations(ending):
    body = "alpha beta gamma delta epsilon zeta eta theta iota kappa " + ending
    plan = plan_translation(body, segment_chars=64)
    assert plan.assemble([c.text for c in plan.chunks]) == body
    assert all(not c.text.endswith(("93.", "al.")) for c in plan.chunks)


def test_reference_definitions_are_not_arbitrary_labelled_prose():
    prose = "[Update]: The company said this is a new version."
    reference = "[ref]: https://example.org/a(b) 'Some title'"
    body = prose + "\n\n" + reference + "\n\n[link](https://a.test/a(b) \"title with )\")"
    plan = plan_translation(body)
    assert prose in plan.chunks[0].text
    assert reference in plan.literals.values()
    assert "https://a.test/a(b) \"title with )\"" in plan.literals.values()
    assert plan.assemble([c.text for c in plan.chunks]) == body


class Sink:
    def __init__(self, body="A long paragraph about $x$.", ext=None):
        self.record = SimpleNamespace(title="中文标题", content=body,
                                      extensions_json=json.dumps(ext or {}))
        self.writes = 0

    async def get(self, article_id):
        return self.record

    async def update(self, article_id, fields):
        self.record.extensions_json = fields["extensions_json"]
        self.writes += 1
        return True


def mock_model(monkeypatch, *, reason="stop", mutate=None):
    calls = []

    @asynccontextmanager
    async def session(config):
        def respond(request):
            payload = json.loads(request.content)
            text = payload["messages"][-1]["content"].split("【待翻译正文】\n", 1)[-1]
            calls.append(text)
            if mutate:
                text = mutate(text)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": text}, "finish_reason": reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(reader_ai, "client_session", session)
    return calls


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", None])
def test_incomplete_response_is_metered_but_never_cached(monkeypatch, reason):
    mock_model(monkeypatch, reason=reason)
    usage = []
    monkeypatch.setattr(llm_client, "_usage_recorder", lambda *args: usage.append(args))
    sink = Sink()
    with pytest.raises(LLMIncompleteOutput):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG, UsageMeta("translate", "reader")))
    assert sink.writes == 0
    assert len(usage) == 1 and usage[0][1]["total_tokens"] == 30


def test_missing_literal_never_cached(monkeypatch):
    mock_model(monkeypatch, mutate=lambda text: "模型丢失了公式")
    sink = Sink()
    with pytest.raises(reader_ai.ReaderAIError, match="不完整"):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert sink.writes == 0


def test_paper_cache_upgrade_then_reuse(monkeypatch):
    calls = mock_model(monkeypatch)
    sink = Sink(ext={"paper": {"content_level": "fulltext"}, "translation_zh": "旧残缺缓存"})
    first = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    second = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert first["translation"] == sink.record.content
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1


def test_stop_reason_is_case_and_whitespace_tolerant(monkeypatch):
    mock_model(monkeypatch, reason=" STOP ")
    sink = Sink()
    assert asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))["translation"] == sink.record.content


def test_latest_metadata_merged_and_changed_source_rejected(monkeypatch):
    sink = Sink()
    def add_metadata(text):
        sink.record.extensions_json = '{"new_field":"preserved"}'
        return text
    mock_model(monkeypatch, mutate=add_metadata)
    asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert json.loads(sink.record.extensions_json)["new_field"] == "preserved"

    sink = Sink()
    def replace_body(text):
        sink.record.content = "New source version"
        return text
    mock_model(monkeypatch, mutate=replace_body)
    with pytest.raises(reader_ai.ReaderAIError, match="原文已更新"):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert sink.writes == 0


def test_failure_cancels_inflight_work(monkeypatch):
    sink = Sink(body=("First paragraph. " * 180) + "\n\n" + ("Second paragraph. " * 180))
    active = set()
    async def translate(title, text, *args):
        active.add(text)
        try:
            if text.startswith("First"):
                await asyncio.sleep(0.01)
                raise LLMIncompleteOutput("truncated")
            await asyncio.sleep(60)
            return text
        finally:
            active.remove(text)
    monkeypatch.setattr(reader_ai, "_translate_segment", translate)
    with pytest.raises(LLMIncompleteOutput):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert not active and sink.writes == 0


def test_oversize_rejected_before_cost_gate_or_model(monkeypatch):
    sink = Sink(body="| " + "a long cell " * 1000 + " |")
    calls = mock_model(monkeypatch)
    def cost_gate():
        raise AssertionError("Must validate input before charging quota")
    with pytest.raises(reader_ai.ReaderAIError) as raised:
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG, pre_llm_check=cost_gate))
    assert raised.value.status_code == 422 and not calls


def test_literal_only_body_needs_no_model_or_quota(monkeypatch):
    sink = Sink(body="```python\nprint('unchanged')\n```\n\n$$x=y$$")
    calls = mock_model(monkeypatch)
    def cost_gate():
        raise AssertionError("No model request means no cost admission")
    result = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG, pre_llm_check=cost_gate))
    assert result["translation"] == sink.record.content
    assert not calls


def test_database_cache_merges_metadata_and_rejects_new_body(tmp_path, monkeypatch):
    from models.db import ArticleRecord
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'translation.db'}")
    with Session(sink.engine) as session:
        session.add(ArticleRecord(id="p", title="中文标题", content="Source $x$.",
                                  source_id="test", content_type="web_article", source_url="https://example.org",
                                  publish_date="2026-10-20", fetched_date="2026-10-20"))
        session.commit()

    def modify_metadata(text):
        with Session(sink.engine) as session:
            record = session.get(ArticleRecord, "p")
            record.extensions_json = '{"new_key":7}'
            session.add(record)
            session.commit()
        return text

    mock_model(monkeypatch, mutate=modify_metadata)
    asyncio.run(reader_ai.translate_article(sink, "p", CONFIG))
    record = asyncio.run(sink.get("p"))
    assert json.loads(record.extensions_json)["new_key"] == 7
    assert json.loads(record.extensions_json)["translation_zh"] == "Source $x$."

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, "p")
        record.extensions_json = "{}"
        session.add(record)
        session.commit()

    def replace_source(text):
        with Session(sink.engine) as session:
            record = session.get(ArticleRecord, "p")
            record.content = "Updated paper."
            session.add(record)
            session.commit()
        return text

    mock_model(monkeypatch, mutate=replace_source)
    with pytest.raises(reader_ai.ReaderAIError, match="原文已更新"):
        asyncio.run(reader_ai.translate_article(sink, "p", CONFIG))
    assert "translation_zh" not in json.loads(asyncio.run(sink.get("p")).extensions_json)
