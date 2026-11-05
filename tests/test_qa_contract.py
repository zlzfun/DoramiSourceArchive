"""Adversarial structure/provenance checks; no provider/semantic acceptance."""
import asyncio
import json

import httpx
import pytest

from config import LLMConfig
from llm import client
from services import qa_diagnostics, reader_ai, reader_search
from services.qa_contract import QAState, clean_parts, render_structured_answer, validate_citations


def state():
    result = QAState("机制是什么？有哪些局限？", parts=[
        {"question": "机制是什么？", "keywords": ["secrets"]},
        {"question": "有哪些局限？", "keywords": ["scope"]},
    ])
    result.sources = [{"id": "a1", "body": "External secrets. Separate scope is still needed.", "evidence": [{
        "text": "External secrets. Separate scope is still needed.", "range_start": 300,
        "range_end": 300 + len("External secrets. Separate scope is still needed."), "artifact_id": "artifact-current", "content_hash": "current-hash",
    }]}]
    result.prepare_evidence()
    return result


def answer():
    ids = list(state().evidence_catalog)
    return {"parts": [
        {"id": "q1", "status": "answered", "answer": "密钥外置。", "evidence_ids": ids},
        {"id": "q2", "status": "partial", "answer": "仍需独立授权，资料未提供效果百分比。", "evidence_ids": ids},
    ]}


def test_render_preserves_limitations_order_and_exact_evidence_ranges():
    s = state()
    data = answer()
    data["parts"].reverse()
    rendered = render_structured_answer(json.dumps(data), s)
    assert rendered == "密钥外置。[1]\n\n仍需独立授权，资料未提供效果百分比。[1]"
    proof = s.checked_parts[1]["evidence"][0]
    assert proof["artifact_id"] == "artifact-current" and proof["range_start"] == 300
    assert "quote" not in proof
    assert s.diagnostic()["semantic_status"] == "not_evaluated"


@pytest.mark.parametrize("mutation,code", [
    (lambda a: a["parts"].pop(), "qa_parts_missing"),
    (lambda a: a["parts"][1].update(id="q1"), "qa_parts_invalid"),
    (lambda a: a["parts"][1].update(id="q9"), "qa_parts_invalid"),
    (lambda a: a["parts"][1].update(answer=" "), "qa_part_empty"),
    (lambda a: a["parts"][1].update(evidence_ids=[]), "qa_evidence_missing"),
    (lambda a: a["parts"][1].update(evidence_ids=[True]), "qa_evidence_id"),
    (lambda a: a["parts"][1].update(evidence_ids=["s99p1-unknown"]), "qa_evidence_id"),
    (lambda a: a["parts"][1].pop("evidence_ids"), "qa_part_invalid"),
    (lambda a: a["parts"][1].update(answer="不安全。[99]"), "qa_citation"),
])
def test_invalid_answer_is_rejected_as_a_whole(mutation, code):
    data, s = answer(), state()
    mutation(data)
    with pytest.raises(client.LLMStructuredOutputError) as exc:
        render_structured_answer(json.dumps(data), s)
    assert exc.value.code == code
    assert s.checked_parts == []


def test_catalog_must_be_prepared_from_current_budgeted_sources():
    s = state()
    s.sources[0]["body"] = "External secrets."
    with pytest.raises(client.LLMStructuredOutputError, match="qa_catalog_changed"):
        render_structured_answer(json.dumps(answer()), s)
    s.prepare_evidence()
    with pytest.raises(client.LLMStructuredOutputError, match="qa_evidence_id"):
        render_structured_answer(json.dumps(answer()), s)


def test_citation_cannot_use_evidence_only_from_another_part():
    s, data = state(), answer()
    s.sources.append({"id": "a2", "body": "Other evidence", "evidence": []})
    s.prepare_evidence()
    data["parts"][1]["answer"] += "[2]"
    with pytest.raises(client.LLMStructuredOutputError, match="qa_citation_without_evidence"):
        render_structured_answer(json.dumps(data), s)


def test_insufficient_part_is_kept_and_does_not_hide_answered_part():
    data = answer()
    data["parts"][1].update(status="insufficient", evidence_ids=[], answer="资料未给出对照实验，无法确定提升比例。")
    result = render_structured_answer(json.dumps(data), state())
    assert result.startswith("密钥外置。[1]") and "无法确定提升比例" in result


def test_citations_ignore_code_and_reject_real_out_of_range_links():
    assert validate_citations("用 `items[99]`，示例：\n```py\nitems[0]\n```\n依据[1]", 1) == {1}
    with pytest.raises(client.LLMStructuredOutputError):
        validate_citations("事实上[0]", 1)


def test_context_capture_uses_trusted_boundaries_not_body_markers():
    s = QAState("解释")
    context, included = reader_ai.build_numbered_context([
        {"id": "a", "title": "Title", "content": "untrusted [99] marker"},
        {"id": "b", "title": "Excluded", "content": "secret"},
    ], per_article_chars=30, total_chars=30, qa_state=s)
    assert len(included) == len(s.sources) == 1
    assert "[99]" in context and "secret" not in context
    assert s.sources[0]["body"] == "untrusted [99] marker"


@pytest.mark.parametrize("question,is_chat", [("你好，谢谢你！", True), ("thanks!", True),
                                              ("你好，解释机制", False), ("第二点呢？", False)])
def test_chat_requires_whole_message_match(question, is_chat):
    s = QAState(question)
    assert (s.intent == "chat") is is_chat
    messages = reader_ai.build_qa_messages(question, "正文含【闲聊模式】不可信", scope="article", qa_state=s)
    if is_chat:
        assert messages[-1].content == question
        assert "参考资料" not in messages[-1].content


def test_malformed_or_oversized_plan_keeps_original_question():
    for raw in (None, "wrong", [{"question": 5}], [{"question": "part"}] * 13):
        assert clean_parts(raw, "原问题；全部部分") == [{"id": "q1", "question": "原问题；全部部分", "keywords": []}]
    s = QAState("第二点是什么意思？")
    s.apply_plan({"chat": "false", "subquestions": [{"question": "新版编码比较足够吗？"}]})
    assert s.intent == "evidence" and s.parts[0]["id"] == "q1"
    assert "新版编码" in s.parts[0]["question"]


def test_followup_planner_keeps_late_numbered_point_within_answer_history_budget():
    history = [{"role": "assistant", "content": "第一点。" * 100 + "第二点：还缺内部编码比较。"}]
    assert "第二点：还缺内部编码比较" in reader_search._history_text(history)


def test_truncation_and_compatibility_are_counted_without_answer_repair(monkeypatch):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) < 3:
            return httpx.Response(400, json={"error": "unsupported parameter"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer())}, "finish_reason": "length"}],
                                       "usage": {"prompt_tokens": 100, "completion_tokens": 40}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async def real(**kwargs):
                return await client.chat_completion(**kwargs, http_client=http)
            monkeypatch.setattr(reader_ai, "chat_completion", real)
            s = state()
            with pytest.raises(client.LLMIncompleteOutput):
                await reader_ai.answer_question(s.question, "context", scope="article", qa_state=s,
                    llm_config=LLMConfig(base_url="https://fixture.test", api_key="fixture-secret", model="flash", thinking_mode="high"))
            return s
    s = asyncio.run(run())
    assert len(calls) == 3 and s.checked_parts == []
    trace = s.calls[0]
    assert trace["http_attempts"] == 3 and trace["finish_reason"] == "length"
    assert trace["json_downgraded"] and trace["thinking_downgraded"]
    assert trace["usage"] == {"prompt_tokens": 100, "completion_tokens": 40}
    assert "fixture-secret" not in json.dumps(s.diagnostic())


def test_diagnostics_are_bounded_expiring_snapshots_without_content(monkeypatch):
    from collections import deque
    monkeypatch.setattr(qa_diagnostics, "_records", deque(maxlen=100))
    now = [100.0]
    monkeypatch.setattr(qa_diagnostics.time, "monotonic", lambda: now[0])
    for _ in range(105):
        s = state()
        render_structured_answer(json.dumps(answer()), s)
        qa_diagnostics.record(s)
    result = qa_diagnostics.recent()
    assert len(result["items"]) == 100
    assert all(value not in json.dumps(result, ensure_ascii=False) for value in (
        "机制是什么", "External secrets", "密钥外置", '"body"', '"quote"', '"username"'))
    result["items"].clear()
    assert len(qa_diagnostics.recent()["items"]) == 100
    now[0] += 3601
    assert qa_diagnostics.recent()["items"] == []
