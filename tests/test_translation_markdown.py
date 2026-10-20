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
    TranslationStructureError, plan_translation, translation_units, restore_translation_units,
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


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_damaged_literal_rejected(mode):
    plan = plan_translation("First $x$ then `y`.")
    first, second = plan.literals
    text = plan.chunks[0].text
    if mode == "missing":
        text = text.replace(first, "")
    elif mode == "duplicate":
        text += first
    with pytest.raises(TranslationStructureError):
        plan.assemble([text])


def test_chinese_word_order_within_unit_and_cross_unit_guard():
    plan = plan_translation("Samples $outputs$ from $policy$.\n\nNext $value$.")
    units = translation_units(plan.chunks[0].text)
    import re
    tokens = re.findall(r"DORAMITR[0-9a-f]{12}X\d+Z", units[0]["text"])
    units[0]["text"] = f"从 {tokens[1]} 采样 {tokens[0]}。"
    raw = json.dumps({"units": list(reversed(units))})
    restored = restore_translation_units(plan.chunks[0].text, raw)
    assert plan.assemble([restored]) == "从 $policy$ 采样 $outputs$。\n\nNext $value$."
    units[0]["text"], units[1]["text"] = units[1]["text"], units[0]["text"]
    with pytest.raises(TranslationStructureError, match="公式、代码或链接不完整"):
        restore_translation_units(plan.chunks[0].text, json.dumps({"units": units}))


@pytest.mark.parametrize("body", [
    "[A](https://a.test) [B](https://b.test)",
    "![A](https://a.test) ![B](https://b.test)",
    "[A [nested]](https://a.test) [B](https://b.test)",
])
def test_links_cannot_swap_destinations_but_whole_links_may_move(body):
    plan = plan_translation(body)
    first, second = plan.literals
    source = plan.chunks[0].text
    swapped = source.replace(first, "TEMP").replace(second, first).replace("TEMP", second)
    with pytest.raises(TranslationStructureError, match="标签与目标"):
        plan.assemble([swapped])
    boundary = source.rfind(" [") if not source.startswith("!") else source.rfind(" ![")
    assert plan.assemble([source[boundary + 1:] + " " + source[:boundary]])
    with pytest.raises(TranslationStructureError, match="目标结构"):
        plan.assemble([source.replace(f"]({first})", f"] {first}")])


def test_duplicate_link_labels_use_multisets_and_translated_labels_are_allowed():
    body = "[here](https://a.test) [here](https://b.test)"
    plan = plan_translation(body)
    source = plan.chunks[0].text
    assert plan.assemble([source.replace("[here]", "[这里]")]) == body.replace("[here]", "[这里]")
    assert plan.assemble([source]) == body


@pytest.mark.parametrize("damage", ["bracket", "image"])
def test_link_bracket_and_image_role_cannot_disappear(damage):
    plan = plan_translation("![Figure](https://example.org/figure.png)")
    text = plan.chunks[0].text
    text = text.replace("[", "", 1) if damage == "bracket" else text.replace("!", "", 1)
    with pytest.raises(TranslationStructureError, match="链接或图片结构"):
        plan.assemble([text])


def test_crlf_is_normalized_before_splitting_including_literals():
    body = "A.\r\n\r\n" + "B " * 40 + "\r\n\r\n```text\r\ncode\r\n```\r\n\r\n$$\r\nx=y\r\n$$"
    plan = plan_translation(body, segment_chars=64)
    restored = [restore_translation_units(c.text, json.dumps({"units": translation_units(c.text)}))
                for c in plan.chunks]
    assert plan.assemble(restored) == body.replace("\r\n", "\n")


def test_single_column_alignment_and_horizontal_rule_round_trip():
    body = "---\n\n| Header |\n| :--- |\n| Content |"
    plan = plan_translation(body)
    assert "---" in plan.literals.values() and "| :--- |" in plan.literals.values()
    assert plan.assemble([c.text for c in plan.chunks]) == body


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown", "bool_id", "newline", "empty", "non_string", "not_json"])
def test_invalid_unit_responses_fail_closed(damage):
    source = "First paragraph.\n\n| Header |\n| --- |\n| Second row |"
    units = translation_units(source)
    if damage == "missing":
        units.pop()
    elif damage == "duplicate":
        units.append(units[0])
    elif damage == "unknown":
        units[0]["id"] = 999
    elif damage == "bool_id":
        units[0]["id"] = False
    elif damage == "newline":
        units[0]["text"] += "\nextra"
    elif damage == "empty":
        units[0]["text"] = ""
    elif damage == "non_string":
        units[0]["text"] = None
    raw = json.dumps({"units": units}) if damage != "not_json" else "Bad JSON"
    with pytest.raises(TranslationStructureError, match="单元"):
        restore_translation_units(source, raw)


def test_table_units_preserve_wrapped_rows_numbers_and_separators():
    source = "| The assistant provides |\n| --- |\n| the answer. |\n\n| Model | 71.0 | 86.7 |"
    units = translation_units(source)
    assert len(units) == 3  # alignment and blank line stay local
    units[0]["text"] = "| 助手提供 |"
    units[1]["text"] = "| 答案。 |"
    translated = restore_translation_units(source, json.dumps({"units": units}))
    assert translated.startswith("| 助手提供 |\n| --- |\n| 答案。 |\n\n")
    units[2]["text"] = "| 模型 | 86.7 | 71.0 |"
    with pytest.raises(TranslationStructureError, match="数值"):
        restore_translation_units(source, json.dumps({"units": units}))


def test_table_formula_cannot_move_to_another_column():
    plan = plan_translation("| Model | $a$ | $b$ |")
    first, second = plan.literals
    text = plan.chunks[0].text.replace(first, "TEMP").replace(second, first).replace("TEMP", second)
    with pytest.raises(TranslationStructureError, match="跨列"):
        plan.assemble([text])


@pytest.mark.parametrize("replacement", ["1.5", "-1.5", "-1.5%"])
def test_table_numeric_sign_and_percentage_are_protected(replacement):
    plan = plan_translation("| Model | +1.5% |")
    with pytest.raises(TranslationStructureError, match="数值"):
        plan.assemble([plan.chunks[0].text.replace("+1.5%", replacement)])


def test_numeric_guard_distinguishes_model_hyphens_and_normalizes_minus():
    plan = plan_translation("| GPT-4o Llama-3.1-8B 2024-08 | -1.5% |")
    assert plan.assemble([plan.chunks[0].text.replace("-1.5", "−1.5")])


def test_numeric_guard_accepts_chinese_adjacent_to_numbers():
    plan = plan_translation("| score 71.0 | about 71.5% |")
    assert plan.assemble(["| 得分71.0 | 约71.5% |"]) == "| 得分71.0 | 约71.5% |"
    signed = plan_translation("| 得分-1.5 |")
    with pytest.raises(TranslationStructureError, match="数值"):
        signed.assemble(["| 得分1.5 |"])


@pytest.mark.parametrize("source,replacement", [
    ("o1-mini", "o3-mini"), ("H100", "H800"),
    ("Qwen2.5-Math-1.5B", "Qwen2.0-Math-1.5B"),
])
def test_numeric_guard_retains_digits_inside_identifiers(source, replacement):
    plan = plan_translation(f"| {source} | 71.0 |")
    with pytest.raises(TranslationStructureError, match="数值"):
        plan.assemble([plan.chunks[0].text.replace(source, replacement)])

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


def test_evaluation_terms_keep_bilingual_meaning_without_touching_literals():
    body = ("Change the few-shot prompt to a zero-shot setting. Few-shot may hurt.\n\n"
            "`zero-shot` $\\text{few-shot}$ [guide](https://example.org/zero-shot)\n\n"
            "```text\nfew-shot\n```\n\nzero-shot-model my_zero-shot_value")
    plan = plan_translation(body)
    masked = "".join(chunk.text for chunk in plan.chunks)
    assert "Change the few-shot" not in masked
    restored = plan.assemble([chunk.text for chunk in plan.chunks])
    assert restored.startswith("Change the 少样本（few-shot） prompt to a 零样本（zero-shot） setting. 少样本（Few-shot） may hurt.")
    assert "`zero-shot` $\\text{few-shot}$ [guide](https://example.org/zero-shot)" in restored
    assert "```text\nfew-shot\n```" in restored
    assert restored.endswith("zero-shot-model my_zero-shot_value")


def test_existing_bilingual_evaluation_terms_are_not_expanded_twice():
    body = "零样本（zero-shot）与少样本 (Few-shot)，zero-shot（零样本）和 few-shot (少样本)。"
    plan = plan_translation(body)
    assert plan.assemble([chunk.text for chunk in plan.chunks]) == body


def test_verified_is_part_of_the_benchmark_name_not_generic_results():
    body = ("SWE-Bench verified results and SWE-Bench Verified.\n\n"
            "`SWE-Bench verified` [name](https://example.org/SWE-Bench-Verified)")
    plan = plan_translation(body)
    restored = plan.assemble([chunk.text for chunk in plan.chunks])
    assert restored.startswith("SWE-Bench Verified results and SWE-Bench Verified.")
    assert "`SWE-Bench verified`" in restored
    assert "https://example.org/SWE-Bench-Verified" in restored


def test_missing_fixed_term_cannot_be_replaced_with_wrong_translation(monkeypatch):
    mock_model(monkeypatch, mutate=lambda text: "改成少样本设置。")
    sink = Sink(body="Change the prompt to a zero-shot setting.")
    with pytest.raises(reader_ai.ReaderAIError, match="公式、代码或链接不完整"):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert sink.writes == 0


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


def mock_model(monkeypatch, *, reason="stop", mutate=None, fenced=False, degrade=False):
    calls = []

    @asynccontextmanager
    async def session(config):
        def respond(request):
            payload = json.loads(request.content)
            text = payload["messages"][-1]["content"].split("【待翻译正文】\n", 1)[-1]
            calls.append(text)
            if degrade and "response_format" in payload:
                return httpx.Response(400, json={"error": "response_format unsupported"})
            if not degrade:
                assert payload["response_format"] == {"type": "json_object"}
            if mutate:
                data = json.loads(text)
                for unit in data["units"]:
                    unit["text"] = mutate(unit["text"])
                text = json.dumps(data)
            if fenced:
                text = "```json\n" + text + "\n```"
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
    with pytest.raises(reader_ai.ReaderAIError, match="公式、代码或链接不完整"):
        asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert sink.writes == 0


def test_json_mode_degrades_to_fenced_output_without_losing_structure(monkeypatch):
    calls = mock_model(monkeypatch, fenced=True, degrade=True)
    sink = Sink()
    result = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert result["translation"] == sink.record.content
    assert len(calls) == 2 and sink.writes == 1


def test_crlf_translation_preserves_source_and_source_fingerprint(monkeypatch):
    mock_model(monkeypatch)
    body = "First.\r\n\r\nSecond."
    sink = Sink(body=body)
    result = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert sink.record.content == body and result["translation"] == "First.\n\nSecond."
    assert json.loads(sink.record.extensions_json)["translation_zh_fp"] == reader_ai._body_fingerprint(body)


def test_paper_cache_upgrade_then_reuse(monkeypatch):
    calls = mock_model(monkeypatch)
    sink = Sink(ext={"paper": {"content_level": "fulltext"}, "translation_zh": "旧残缺缓存"})
    first = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    second = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert first["translation"] == sink.record.content
    assert first["cached"] is False and second["cached"] is True
    assert len(calls) == 1


def test_paper_version_two_cache_is_retranslated_for_term_fix(monkeypatch):
    calls = mock_model(monkeypatch)
    sink = Sink(body="Use zero-shot.", ext={
        "paper": {"content_level": "fulltext"}, "translation_zh": "使用少样本。",
        "translation_zh_version": 2,
    })
    result = asyncio.run(reader_ai.translate_article(sink, "paper", CONFIG))
    assert result["translation"] == "Use 零样本（zero-shot）."
    assert len(calls) == 1
    assert json.loads(sink.record.extensions_json)["translation_zh_version"] == 3


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
