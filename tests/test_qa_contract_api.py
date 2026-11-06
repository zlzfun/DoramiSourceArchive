"""HTTP -> permissions -> real FTS/context -> provider transport -> response.

Only provider HTTP is mocked. Each test owns an isolated SQLite database.
"""
from collections import deque
from datetime import datetime, timezone
import json
import re

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlmodel import Session, select

from llm import client as llm_client
from models.db import ArticleRecord, PodcastTextPublicationRecord
from services import qa_diagnostics, reader_ai, reader_search
from tests.test_reader_ai import (_base_setup, _configure_llm, _enable_ai_beta, _login,
                            _seed_credentialed_source, _seed_podcast_transcript, _seed_users)


BODY = "SPEAKER_00: External secrets protect credentials.\nSPEAKER_01: Separate scope is still needed."
PARTS = [{"question": "密钥如何处理？", "keywords": ["External secrets"]},
         {"question": "还需要什么限制？", "keywords": ["Separate scope"]}]


def setup(monkeypatch, tmp_path, *, failure=None, body=BODY):
    app, sink = _base_setup(monkeypatch, tmp_path, "contract.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_podcast_transcript(sink.engine, episode_id="episode", text=body)
    monkeypatch.setattr(qa_diagnostics, "_records", deque(maxlen=100))
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "检索规划器" in payload["messages"][0]["content"]:
            value = {"keywords": ["secrets"], "passage_keywords": ["secrets", "scope"],
                     "subquestions": PARTS, "single_episode": True}
            reason = "stop"
        else:
            mode = (failure or "").split("+")[0]
            ids = re.findall(r'"id":"(s1p[^" ]+)"', payload["messages"][-1]["content"])
            assert ids
            value = {"parts": [
                {"id": "q1", "status": "answered", "answer": "密钥外置保护凭据。",
                 "evidence_ids": ids[:1]},
                {"id": "q2", "status": "answered", "answer": "仍需独立授权范围。",
                 "evidence_ids": ids[:1]},
            ]}
            reason = "stop"
            if mode == "provider_error":
                return httpx.Response(503, json={"error": "fixture unavailable"})
            if mode == "partial":
                value["parts"][1].update(status="partial", answer="仍需独立授权范围，具体配置尚无法确定。")
            elif mode == "omission":
                value["parts"].pop()
            elif mode == "wrong_id":
                value["parts"][1]["evidence_ids"] = ["s1p999-invented"]
            elif mode == "missing_ids":
                value["parts"][1].pop("evidence_ids")
            elif mode == "metadata_as_content":
                value["parts"][1]["evidence_ids"] = re.findall(
                    r'"id":"(s1m)"', payload["messages"][-1]["content"])
            elif mode == "inline_ids":
                for part in value["parts"]:
                    part["answer"] += f"[{ids[0]}]"
            elif mode == "unknown_marker":
                value["parts"][1]["answer"] += "[s1p999]"
            elif mode == "eight_plus_title":
                assert len(ids) >= 8
                value["parts"][0].update(evidence_ids=ids[:8], metadata_ids=["s1m"])
            elif mode == "length":
                reason = "length"
            if "withdraw" in (failure or ""):
                with Session(sink.engine) as session:
                    p = session.exec(select(PodcastTextPublicationRecord)).first()
                    p.status = "unpublished"
                    p.unpublished_at = datetime.now(timezone.utc).isoformat()
                    session.add(p)
                    session.commit()
            if "delete" in (failure or ""):
                with Session(sink.engine) as session:
                    session.delete(session.get(ArticleRecord, "episode"))
                    session.commit()
            if "hidden" in (failure or ""):
                from services import source_visibility
                with Session(sink.engine) as session:
                    source_visibility.set_source_hidden(session, "podcast-source", True)
            if "credentialed" in (failure or ""):
                _seed_credentialed_source(sink.engine, "podcast-source")
        content = json.dumps(value)
        if (failure or "").startswith("bad_json") and "parts" in value:
            content = '{"parts":[{id:"q1"}]}'
        return httpx.Response(200, json={"model": "fixture-served", "choices": [{
            "message": {"content": content}, "finish_reason": reason,
        }], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    async def completion(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await llm_client.chat_completion(**kwargs, http_client=http)
    monkeypatch.setattr(reader_ai, "chat_completion", completion)
    monkeypatch.setattr(reader_search, "chat_completion", completion)
    return app, sink, requests


@pytest.mark.parametrize("scope", ["article", "subscription", "all"])
@pytest.mark.parametrize("presentation", [None, "inline_ids"])
def test_real_pipeline_returns_natural_answer_bound_to_current_transcript(monkeypatch, tmp_path, scope, presentation):
    app, sink, requests = setup(monkeypatch, tmp_path, failure=presentation)
    from api.routers import reader
    quota_calls = []
    monkeypatch.setattr(reader.accounts_service, "record_ai_usage", lambda *args: quota_calls.append(args[1:]))
    with TestClient(app.app) as client:
        _login(client)
        assert client.post("/api/reader/sources/podcast-source/subscribe").status_code == 200
        response = client.post("/api/reader/ai/ask", json={
            "scope": scope, "article_id": "episode", "question": "密钥如何处理，还需要什么限制？", "ask_id": "contract-test",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["answer"] == "密钥外置保护凭据。[1]\n\n仍需独立授权范围。[1]"
        assert len(requests) == 2 and len(data["sources"]) == 1
        assert quota_calls == [("user", "ask")]
        assert json.dumps(BODY)[1:-1] in requests[-1]["messages"][-1]["content"]
        assert data["sources"][0]["evidence"][0]["artifact_id"].startswith("episode-")
        assert "checked_parts" not in data and "q1" not in data["answer"]
        assert client.get("/api/reader/ai/ask/progress?ask_id=contract-test").json()["stage"] is None
        assert client.get("/api/admin/qa-diagnostics").status_code == 403
        _login(client, "admin", "admin")
        report = client.get("/api/admin/qa-diagnostics")
        assert report.status_code == 200, report.text
        assert report.headers["cache-control"] == "no-store"
        row = next(item for item in report.json()["items"] if item["trace_id"] == data["trace_id"])
        assert row["planned_parts"] == len(row["checked_parts"]) == 2
        assert row["calls"][-1]["finish_reason"] == "stop"
        assert row["semantic_status"] == "not_evaluated"
        assert all(text not in report.text for text in (BODY, "密钥如何处理", '"answer":', '"quote":'))
        assert row["sources"][0]["lexical_part_hits"] == ["q1", "q2"]


@pytest.mark.parametrize("failure", ["omission", "wrong_id", "length", "missing_ids", "metadata_as_content",
                                    "unknown_marker", "bad_json", "provider_error"])
@pytest.mark.parametrize("scope", ["article", "subscription", "all"])
def test_generation_failure_returns_only_authorized_references(monkeypatch, tmp_path, failure, scope):
    app, _, requests = setup(monkeypatch, tmp_path, failure=failure)
    from api.routers import reader
    quota_calls = []
    monkeypatch.setattr(reader.accounts_service, "record_ai_usage", lambda *args: quota_calls.append(args))
    with TestClient(app.app) as client:
        _login(client)
        client.post("/api/reader/sources/podcast-source/subscribe")
        response = client.post("/api/reader/ai/ask", json={"scope": scope, "article_id": "episode",
                              "question": "密钥如何处理，还需要什么限制？", "ask_id": "contract-fail"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == data["answer_mode"] == "references_only"
        assert "未能生成可用的回答" in data["answer"]
        assert "密钥外置保护凭据" not in response.text  # rejected draft never escapes
        assert data["sources"][0]["evidence"][0]["text"] == BODY
        assert "qa_" not in response.text  # diagnostic codes stay off the reader
        assert len(requests) == 2  # no corrective paid loop
        assert quota_calls == []  # token billing remains separate from completed-answer quota
        assert client.get("/api/reader/ai/ask/progress?ask_id=contract-fail").json()["stage"] is None
    trace = qa_diagnostics.recent()["items"][0]
    assert trace["outcome"] != "returned" and trace["stages"]["delivery"] == "references_only"


@pytest.mark.parametrize("failure,status", [("withdraw", 409), ("bad_json+withdraw", 409),
    ("length+withdraw", 409), ("bad_json+delete", 409), ("bad_json+hidden", 404), ("bad_json+credentialed", 403)])
def test_revoked_sources_never_escape_through_reference_fallback(monkeypatch, tmp_path, failure, status):
    app, _, requests = setup(monkeypatch, tmp_path, failure=failure)
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode",
                              "question": "密钥如何处理，还需要什么限制？"})
        assert response.status_code == status, response.text
        assert "sources" not in response.json() and BODY not in response.text
        assert len(requests) == 2


@pytest.mark.parametrize("failure", ["no_sources", "changed_sources", "catalog_changed", "source_invalid"])
def test_reference_fallback_does_not_bypass_missing_or_invalid_source_binding(monkeypatch, tmp_path, failure):
    app, _, _ = setup(monkeypatch, tmp_path)
    if failure == "no_sources":
        async def empty_context(**kwargs):
            return "", []
        monkeypatch.setattr(reader_ai, "assemble_reader_context", empty_context)
    async def fail_answer(*args, **kwargs):
        if failure == "changed_sources":
            kwargs["qa_state"].sources[0]["body"] = "UNAUTHORIZED_CHANGED_BODY"
        if failure in ("catalog_changed", "source_invalid"):
            raise llm_client.LLMStructuredOutputError("qa_" + failure, "fixture")
        raise llm_client.LLMError("fixture")
    monkeypatch.setattr(reader_ai, "answer_question", fail_answer)
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode",
                              "question": "密钥如何处理，还需要什么限制？"})
        assert response.status_code == 502, response.text
        assert "sources" not in response.json() and "UNAUTHORIZED" not in response.text


def test_partial_answer_keeps_good_content_and_its_specific_gap(monkeypatch, tmp_path):
    app, _, requests = setup(monkeypatch, tmp_path, failure="partial")
    from api.routers import reader
    quota_calls = []
    monkeypatch.setattr(reader.accounts_service, "record_ai_usage", lambda *args: quota_calls.append(args[1:]))
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode",
                              "question": "密钥如何处理，还需要什么限制？"})
        data = response.json()
        assert data["status"] == "success" and data["answer_mode"] == "partial"
        assert "密钥外置保护凭据。[1]" in data["answer"]
        assert "具体配置尚无法确定。[1]" in data["answer"]
        assert len(requests) == 2
        assert quota_calls == [("user", "ask")]


def test_real_transcript_allows_eight_content_ids_and_one_title(monkeypatch, tmp_path):
    body = "\n".join(f"SPEAKER_{i}: External secrets. Separate scope. " + "x" * 740 for i in range(8))
    app, _, requests = setup(monkeypatch, tmp_path, failure="eight_plus_title", body=body)
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode",
                               "question": "密钥如何处理，还需要什么限制？"})
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "密钥外置保护凭据。[1]\n\n仍需独立授权范围。[1]"
    assert len(requests) == 2
    trace = qa_diagnostics.recent()["items"][0]
    assert len(trace["checked_parts"][0]["evidence"]) == 9


def test_explicit_greeting_skips_planning_and_source_loading(monkeypatch, tmp_path):
    app, _, _ = setup(monkeypatch, tmp_path)
    calls = []
    async def chat(**kwargs):
        calls.append(kwargs)
        return "你好！"
    monkeypatch.setattr(reader_ai, "chat_completion", chat)
    monkeypatch.setattr(reader_search, "chat_completion", lambda **kwargs: pytest.fail("must not plan greeting"))
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode", "question": "你好，谢谢你"})
        assert response.status_code == 200
        assert response.json()["sources"] == []
        assert len(calls) == 1 and calls[0]["messages"][-1].content == "你好，谢谢你"
        assert BODY not in str(calls)


def test_non_root_admin_cannot_read_source_identity_diagnostics(monkeypatch, tmp_path):
    app, sink, _ = setup(monkeypatch, tmp_path)
    _seed_users(sink.engine, accounts=(("second-admin", "test-pass", "admin"),))
    with TestClient(app.app) as client:
        _login(client, "second-admin", "test-pass")
        assert client.get("/api/admin/qa-diagnostics").status_code == 403
