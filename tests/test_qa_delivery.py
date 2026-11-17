"""Batch-four failure shapes, synthetic text only; no real model score claims."""
import asyncio
import json

import pytest

from config import LLMConfig
from llm.client import LLMStructuredOutputError
from services import reader_ai
from services.qa_contract import QAState, render_structured_answer
from services.qa_evidence import build_catalog
from tests.test_qa_evidence import answer, render_current, state


def test_model_selects_short_ids_while_application_retains_binding_hash_and_ranges():
    s = state("SPEAKER_1: 没有做过对照实验，不能保证收益。")
    context, catalog = build_catalog(s.sources)
    document = json.loads(context)["sources"][0]
    assert document["metadata"]["id"] == "s1m"
    assert document["passages"][0]["id"] == "s1p1"
    assert "sha256" not in context and "original-hash" not in context
    assert len(catalog["s1p1"]["binding_sha256"]) == 64
    render_current(json.dumps(answer(s)), s)
    proof = s.checked_parts[1]["evidence"][0]
    assert proof["binding_sha256"] == catalog["s1p1"]["binding_sha256"]
    assert proof["range_start"] == 70 and proof["artifact_id"] == "current-artifact"


def test_eight_body_ids_plus_title_are_kept_without_dropping_evidence():
    s = state("\n".join(f"SPEAKER_{i}: " + "x" * 780 for i in range(8)))
    data = answer(s)
    assert len(data["parts"][1]["evidence_ids"]) == 8
    data["parts"][1]["metadata_ids"] = ["s1m"]
    render_current(json.dumps(data), s)
    assert len(s.checked_parts[1]["evidence"]) == 9
    assert [e["kind"] for e in s.checked_parts[1]["evidence"]].count("content") == 8


@pytest.mark.parametrize("kind", ["content", "metadata"])
def test_each_category_still_has_its_own_limit(kind):
    s = QAState("标题和内容？", parts=[{"question": "标题"}, {"question": "内容"}])
    s.sources = [{"id": str(i), "header": f"[{i}] Title {i}", "body": f"Evidence {i}", "evidence": []}
                 for i in range(1, 10)]
    s.prepare_evidence()
    data = answer(s)
    data["parts"][0]["metadata_ids"] = ["s1m"]
    data["parts"][1]["evidence_ids"] = ["s1p1"]
    field = "evidence_ids" if kind == "content" else "metadata_ids"
    data["parts"][1][field] = [key for key, entry in s.evidence_catalog.items() if entry["kind"] == kind]
    with pytest.raises(LLMStructuredOutputError, match="qa_part_invalid"):
        render_current(json.dumps(data), s)
    assert s.checked_parts == []


def test_only_selected_markers_become_source_links_with_local_negation_unchanged():
    s = state("尚无证据可以取消审核。")
    s.sources.append({"id": "other", "header": "[2] Second", "body": "仅测了速度，没有测安全。", "evidence": []})
    s.prepare_evidence()
    data = answer(s)
    data["parts"][0].update(metadata_ids=["s1m"], answer="第一期节目[s1m]。")
    data["parts"][1].update(evidence_ids=["s1p1", "s2p1"],
        answer="尚无证据可以取消审核[s1p1]；仅测了速度，没有测安全[s2p1]，不能据此保证安全。")
    text = render_current(json.dumps(data), s)
    assert text == "第一期节目[1]。\n\n尚无证据可以取消审核[1]；仅测了速度，没有测安全[2]，不能据此保证安全。"


@pytest.mark.parametrize("prose", [
    "不确定[s1p999]", "不确定[s2p1]", "不确定[s1m]",  # exists but not selected in this part
    "不确定[s1p1-deadbeef1234]", "不确定[s1p1-typo]", "不确定s1p1。",
    "不确定[s1p]", "不确定[s1p1](https://example.test)", "不确定\\[s1p1]",
    "![s1p1]", "![s1p1](https://example.test)", "[s1p1]: https://example.test",
    "[s1p1] \t: https://example.test", "[s1p1] (https://example.test)",
])
def test_unknown_unselected_legacy_or_unwrapped_internal_ids_are_not_guessed(prose):
    s = state("尚无对照。")
    s.sources.append({"id": "other", "header": "[2] Other", "body": "Other.", "evidence": []})
    s.prepare_evidence()
    data = answer(s)
    data["parts"][1].update(evidence_ids=["s1p1"], answer=prose)
    with pytest.raises(LLMStructuredOutputError, match="qa_evidence_marker"):
        render_current(json.dumps(data), s)
    assert s.checked_parts == []


def test_adjacent_markers_are_converted_but_code_literals_are_not_rewritten():
    s = state("Evidence.")
    data = answer(s)
    data["parts"][1]["answer"] = (
        "证据[s1p1][s1p1]；代码 `items[s9p9]` 和 ``[s1p1] ` [99]``。\n"
        "```python\nitems[s9p9]\n```\n~~~\n[s1p1]\n~~~\n仍有局限[s1p1]。")
    expected = data["parts"][1]["answer"].replace("证据[s1p1][s1p1]", "证据[1][1]").replace("仍有局限[s1p1]", "仍有局限[1]")
    assert render_current(json.dumps(data), s).endswith(expected)


def test_equal_short_ids_in_two_requests_cannot_swap_application_bindings():
    first, second = state("First evidence."), state("Second evidence.")
    assert first.evidence_catalog.keys() == second.evidence_catalog.keys()
    assert first.evidence_catalog["s1p1"]["binding_sha256"] != second.evidence_catalog["s1p1"]["binding_sha256"]
    with pytest.raises(LLMStructuredOutputError, match="qa_catalog_changed"):
        render_structured_answer(json.dumps(answer(first)), second, expected_binding=first.answer_binding())


@pytest.mark.parametrize("mutation", ["source", "catalog", "plan", "question", "reprepared"])
def test_runtime_binds_before_await_and_rejects_changed_input_even_after_reprepare(monkeypatch, mutation):
    s = state("First evidence.")
    calls = []
    async def changed(**kwargs):
        calls.append(kwargs)
        value = json.dumps(answer(s))
        if mutation == "catalog":
            s.evidence_catalog["s1p1"]["source"] = 2
        elif mutation == "plan":
            s.parts[0]["question"] = "A different question"
        elif mutation == "question":
            s.question = "A different request"
        else:
            s.sources[0]["evidence"][0]["artifact_id"] = "new-artifact"
            if mutation == "reprepared":
                s.prepare_evidence()
        return value
    monkeypatch.setattr(reader_ai, "chat_completion", changed)
    with pytest.raises(LLMStructuredOutputError, match="qa_catalog_changed"):
        asyncio.run(reader_ai.answer_question(s.question, "ignored", scope="article", qa_state=s,
                    llm_config=LLMConfig(base_url="https://fixture.test", api_key="fixture", model="fixture")))
    assert len(calls) == 1 and s.checked_parts == []


def test_legacy_hashed_id_is_not_silently_accepted_in_evidence_field():
    s = state("Evidence.")
    data = answer(s)
    data["parts"][1]["evidence_ids"] = ["s1p1-" + s.evidence_catalog["s1p1"]["binding_sha256"][:12]]
    with pytest.raises(LLMStructuredOutputError, match="qa_evidence_id"):
        render_current(json.dumps(data), s)


def test_structured_prompt_keeps_short_quote_and_uncertainty_requirements():
    s = state("SPEAKER_2: twenty; I do not recall the unit.")
    prompt = reader_ai.build_qa_messages(s.question, "", scope="article", qa_state=s)[0].content
    assert "证据字段只选编号，不复制原句；这不限制自然答案中的必要短引" in prompt
    assert "与正文编号分别计数" in prompt
    assert "没有证据可以取消" in prompt and "无法保证收益" in prompt
    # This verifies instruction delivery, not that a real model follows it.
    assert s.diagnostic()["semantic_status"] == "not_evaluated"
