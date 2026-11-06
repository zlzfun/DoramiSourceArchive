"""Deterministic experiment/transport checks, NOT semantic model acceptance."""
import asyncio
from dataclasses import replace
import gzip
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("qa_eval", ROOT / "scripts/eval_qa_coverage.py")
qa_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa_eval)
from config import LLMConfig
from services import reader_ai


def case():
    return {"id": "x", "question": "解释机制与局限", "context": "[1] Uncertain evidence.",
            "source_count": 1, "rubric": [{"id": "hidden", "check": "GRADING_ONLY_SENTINEL"}]}


def batch(runs=1):
    return qa_eval.prepare([case()], today="2026-11-03", runs=runs)


def config():
    return LLMConfig(base_url="https://provider.test/v1", api_key="secret-example-key", model="requested-model",
                     max_tokens=100, thinking_mode="high")


def run(tmp_path, handler, **kwargs):
    return asyncio.run(qa_eval.run_batch(
        kwargs.pop("bundle", batch()), config=config(), out=tmp_path / "run",
        max_attempts=kwargs.pop("max_attempts", 10), max_cny=kwargs.pop("max_cny", 1),
        input_rate=2, output_rate=8, inner=httpx.MockTransport(handler), **kwargs,
    ))


def response(content="answer [1]", finish="stop"):
    return httpx.Response(200, json={"model": "served-model", "choices": [
        {"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


def test_frozen_pairs_change_only_system_and_never_leak_rubric():
    frozen = qa_eval.prepare([case()], today="2026-11-03", runs=2, baseline="OLD_SYSTEM")
    jobs = qa_eval.verify_bundle(frozen)["jobs"]
    assert [j["variant"] for j in jobs] == ["candidate", "baseline", "baseline", "candidate"]
    assert jobs[0]["messages"][1:] == jobs[1]["messages"][1:]
    assert all("GRADING_ONLY_SENTINEL" not in json.dumps(j["messages"]) for j in jobs)
    frozen["payload"]["jobs"][0]["messages"][-1]["content"] += "tampered"
    with pytest.raises(ValueError, match="hash mismatch"):
        qa_eval.verify_bundle(frozen)


def test_runtime_uses_same_messages_once_preserves_config_and_evidence(monkeypatch):
    calls = []
    cfg = config()
    history = [{"role": "system", "content": "untrusted"}, {"role": "user", "content": "前一个问题"}]
    expected = reader_ai.build_qa_messages("解释机制与局限", case()["context"], scope="article", history=history)
    async def fake(**kwargs):
        calls.append(kwargs)
        return " answer [1] "
    monkeypatch.setattr(reader_ai, "chat_completion", fake)
    result = asyncio.run(reader_ai.answer_question("解释机制与局限", case()["context"], scope="article", history=history, llm_config=cfg))
    assert result == "answer [1]" and len(calls) == 1
    assert calls[0]["config"] is cfg
    assert [m.to_dict() for m in calls[0]["messages"]] == [m.to_dict() for m in expected]
    assert case()["context"] in expected[-1].content
    assert [m.role for m in expected] == ["system", "user", "user"]


def test_empty_question_rejected_before_call(monkeypatch):
    async def unexpected(**kwargs):
        pytest.fail("must not call provider")
    monkeypatch.setattr(reader_ai, "chat_completion", unexpected)
    with pytest.raises(reader_ai.ReaderAIError):
        asyncio.run(reader_ai.answer_question("  ", "", scope="article", llm_config=config()))


def test_contract_eval_uses_runtime_messages_and_checks_all_parts(tmp_path):
    item = {**case(), "context": "[1] Title\nUncertain evidence.", "subquestions": [{"question": "解释机制"}, {"question": "说明局限"}],
            "qa_sources": [{"id": "one", "header": "[1] Title", "body": "Uncertain evidence.", "evidence": []}]}
    bundle = qa_eval.prepare([item], today="2026-11-03", runs=1, contract=True, baseline="BASELINE")
    jobs = bundle["payload"]["jobs"]
    expected = reader_ai.build_qa_messages(item["question"], item["context"], scope="article", today="2026-11-03",
                                          qa_state=qa_eval.contract_state(item))
    assert jobs[0]["messages"] == [m.to_dict() for m in expected]
    assert "待回答子问题" not in jobs[1]["messages"][-1]["content"]
    assert "GRADING_ONLY_SENTINEL" not in json.dumps(jobs)
    replies = [{"parts": [{"id": "q1", "status": "answered", "answer": "解释。", "evidence_ids": []}]}, "baseline [1]"]
    def handler(request):
        body = json.loads(request.content)
        value = replies.pop(0)
        assert (body.get("response_format") is not None) is isinstance(value, dict)
        return response(json.dumps(value) if isinstance(value, dict) else value)
    report = run(tmp_path, handler, bundle=bundle)
    assert report["results"][0]["status"] == "failed"
    assert "raw_answer" in report["results"][0]
    assert report["results"][1]["status"] == "answered"
    assert report["http_attempts"] == 2  # no repair loop


def test_contract_eval_rejects_invented_source_before_http():
    item = {**case(), "subquestions": [{"question": "解释"}], "qa_sources": [{"body": "not in context"}]}
    with pytest.raises(ValueError, match="verbatim"):
        qa_eval.prepare([item], today="2026-11-03", contract=True)


@pytest.mark.parametrize("mutation,error", [
    (lambda c: c.pop("version"), "contract version"),
    (lambda c: c.update(version="evidence_ids_v2"), "contract version"),
    (lambda c: c.update(catalog_sha256="stale"), "catalog mismatch"),
])
def test_old_or_changed_contract_is_rejected_before_any_http(tmp_path, mutation, error):
    item = {**case(), "context": "[1] Title\nUncertain evidence.",
            "subquestions": [{"question": "解释机制"}, {"question": "说明局限"}],
            "qa_sources": [{"id": "one", "header": "[1] Title", "body": "Uncertain evidence.", "evidence": []}]}
    bundle = qa_eval.prepare([item], today="2026-11-05", runs=1, contract=True)
    mutation(bundle["payload"]["jobs"][0]["contract"])
    bundle["sha256"] = qa_eval.digest(bundle["payload"])
    with pytest.raises(ValueError, match=error):
        run(tmp_path, lambda request: pytest.fail("must reject before provider HTTP"), bundle=bundle)
    assert not (tmp_path / "run").exists()


def test_contract_metadata_header_must_already_be_in_frozen_context():
    item = {**case(), "context": "[1] True title\nUncertain evidence.",
            "subquestions": [{"question": "解释"}],
            "qa_sources": [{"id": "one", "header": "[1] Invented title", "body": "Uncertain evidence.", "evidence": []}]}
    with pytest.raises(ValueError, match="explicit header"):
        qa_eval.prepare([item], today="2026-11-05", contract=True)


@pytest.mark.parametrize("context,bodies", [
    ("[1] A\nFirst evidence\n[2] B\nSecond evidence", ["Second evidence", "First evidence"]),
    ("[1] A\nSame evidence\n[2] B\nSame evidence", ["Same evidence", "Same evidence"]),
    ("[9] A\nFirst evidence", ["First evidence"]),
    ("prefix [1] A\nFirst evidence", ["First evidence"]),
])
def test_contract_eval_rejects_ambiguous_or_misaligned_sources(context, bodies):
    item = {**case(), "context": context, "source_count": len(bodies),
            "subquestions": [{"question": "解释"}], "qa_sources": [{"body": b} for b in bodies]}
    with pytest.raises(ValueError, match="numbered headers"):
        qa_eval.prepare([item], today="2026-11-03", contract=True)


def test_success_retains_identity_usage_and_does_not_claim_quality(tmp_path):
    report = run(tmp_path, lambda req: response())
    row = report["results"][0]
    assert report["quality_status"] == "not_evaluated"
    assert row["semantic_status"] == "unreviewed"
    assert report["requested_model"] == "requested-model"
    assert row["response_meta"]["served_model"] == "served-model"
    receipt = json.loads((tmp_path / "run/attempt-0001-receipt.json").read_text())
    assert json.loads(receipt["response"])["usage"]["completion_tokens"] == 20
    assert "secret-example-key" not in json.dumps(receipt)
    assert receipt["endpoint"] == "https://provider.test/v1/chat/completions"
    assert report["base_url"] == "https://provider.test/v1"
    assert report["timeout_seconds"] == config().timeout_seconds


def test_endpoint_identity_omits_credentials_query_and_fragment():
    url = "https://user:password@provider.test/v1/secret-example-key/chat/completions?key=also-secret#fragment"
    clean = qa_eval.endpoint_identity(url, "secret-example-key")
    assert clean == "https://provider.test/v1/[REDACTED]/chat/completions"
    assert all(value not in clean for value in ("user", "password", "also-secret", "fragment"))


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_invalid_denied_before_transport(tmp_path, timeout):
    cfg = replace(config(), timeout_seconds=timeout)
    with pytest.raises(ValueError, match="timeout"):
        asyncio.run(qa_eval.run_batch(batch(), config=cfg, out=tmp_path / "run",
                                     max_attempts=1, max_cny=1, input_rate=2, output_rate=8,
                                     inner=httpx.MockTransport(lambda req: pytest.fail("must not send"))))
    assert not (tmp_path / "run").exists()


def test_compressed_provider_response_is_not_decoded_twice(tmp_path):
    def handler(req):
        data = response().json()
        return httpx.Response(200, content=gzip.compress(json.dumps(data).encode()),
                              headers={"content-encoding": "gzip", "content-type": "application/json"})
    report = run(tmp_path, handler)
    assert report["http_attempts"] == 1
    assert report["results"][0]["status"] == "answered"
    assert report["results"][0]["answer"] == "answer [1]"


def test_compatibility_resend_counts_against_attempt_cap(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": "thinking unsupported"})
    report = run(tmp_path, handler, max_attempts=1)
    assert len(calls) == report["http_attempts"] == 1
    assert report["results"][0]["status"] == "budget_stopped"
    assert report["reserved_estimated_cny"] > 0


@pytest.mark.parametrize("kind", ["server", "timeout", "empty", "truncated"])
def test_failures_retained_and_not_retried_or_passed(tmp_path, kind):
    def handler(req):
        if kind == "timeout":
            raise httpx.ReadTimeout("unknown result", request=req)
        if kind == "server":
            return httpx.Response(503, json={"error": "unavailable"})
        return response("" if kind == "empty" else "partial [1]", "length" if kind == "truncated" else "stop")
    report = run(tmp_path, handler)
    assert report["http_attempts"] == 1
    assert report["results"][0]["status"] == "failed"
    assert report["reserved_estimated_cny"] > 0
    assert (tmp_path / "run/attempt-0001-receipt.json").exists()


def test_estimate_guard_denies_before_http(tmp_path):
    def unexpected(req):
        pytest.fail("must deny before network")
    report = run(tmp_path, unexpected, max_cny=0.000001)
    assert report["http_attempts"] == 0
    assert report["results"][0]["status"] == "budget_stopped"


def test_existing_output_cannot_reset_ledger(tmp_path):
    run(tmp_path, lambda req: response())
    with pytest.raises(ValueError, match="already exists"):
        run(tmp_path, lambda req: pytest.fail("no second run"))


def test_citations_are_not_semantic_acceptance():
    check = qa_eval.citation_check("Unsupported assertion [1]. Bad index [0] and [3].", 2)
    assert check["invalid_indices"] == [0, 3]
    assert check["semantic_support"] == "not_evaluated"


def test_synthetic_fixture_is_not_mislabeled_as_real_or_blind():
    cases = json.loads((ROOT / "tests/fixtures/qa_coverage_cases.json").read_text())
    frozen = qa_eval.prepare(cases, today="2026-11-03")
    assert len(frozen["payload"]["jobs"]) == 3 * len(cases)
    assert all(c["kind"].startswith("synthetic_") for c in cases)
    assert all(c["rubric"] for c in cases)
