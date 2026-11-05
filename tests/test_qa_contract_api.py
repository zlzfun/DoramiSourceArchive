"""HTTP -> permissions -> real FTS/context -> provider transport -> response.

Only provider HTTP is mocked. Each test owns an isolated SQLite database.
"""
from collections import deque
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlmodel import Session, select

from llm import client as llm_client
from models.db import PodcastTextPublicationRecord
from services import qa_diagnostics, reader_ai, reader_search
from tests.test_reader_ai import (_base_setup, _configure_llm, _enable_ai_beta, _login,
                            _seed_podcast_transcript, _seed_users)


BODY = "SPEAKER_00: External secrets protect credentials.\nSPEAKER_01: Separate scope is still needed."
PARTS = [{"question": "密钥如何处理？", "keywords": ["External secrets"]},
         {"question": "还需要什么限制？", "keywords": ["Separate scope"]}]


def setup(monkeypatch, tmp_path, *, failure=None):
    app, sink = _base_setup(monkeypatch, tmp_path, "contract.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_podcast_transcript(sink.engine, episode_id="episode", text=BODY)
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
            value = {"parts": [
                {"id": "q1", "status": "answered", "answer": "密钥外置保护凭据。",
                 "evidence": [{"source": 1, "quote": "External secrets protect credentials."}]},
                {"id": "q2", "status": "answered", "answer": "仍需独立授权范围。",
                 "evidence": [{"source": 1, "quote": "Separate scope is still needed."}]},
            ]}
            reason = "stop"
            if failure == "omission":
                value["parts"].pop()
            elif failure == "wrong_quote":
                value["parts"][1]["evidence"][0]["quote"] = "Scope is unnecessary."
            elif failure == "length":
                reason = "length"
            elif failure == "withdraw":
                with Session(sink.engine) as session:
                    p = session.exec(select(PodcastTextPublicationRecord)).first()
                    p.status = "unpublished"
                    p.unpublished_at = datetime.now(timezone.utc).isoformat()
                    session.add(p)
                    session.commit()
        return httpx.Response(200, json={"model": "fixture-served", "choices": [{
            "message": {"content": json.dumps(value)}, "finish_reason": reason,
        }], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    async def completion(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await llm_client.chat_completion(**kwargs, http_client=http)
    monkeypatch.setattr(reader_ai, "chat_completion", completion)
    monkeypatch.setattr(reader_search, "chat_completion", completion)
    return app, sink, requests


@pytest.mark.parametrize("scope", ["article", "subscription", "all"])
def test_real_pipeline_returns_natural_answer_bound_to_current_transcript(monkeypatch, tmp_path, scope):
    app, sink, requests = setup(monkeypatch, tmp_path)
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
        assert BODY in requests[-1]["messages"][-1]["content"]
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


@pytest.mark.parametrize("failure,status", [("omission", 502), ("wrong_quote", 502), ("length", 502), ("withdraw", 409)])
def test_invalid_or_revoked_result_never_returns_partial_success(monkeypatch, tmp_path, failure, status):
    app, _, requests = setup(monkeypatch, tmp_path, failure=failure)
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={"scope": "article", "article_id": "episode",
                              "question": "密钥如何处理，还需要什么限制？", "ask_id": "contract-fail"})
        assert response.status_code == status, response.text
        assert "answer" not in response.json()
        assert "qa_" not in response.text  # diagnostic codes stay off the reader
        assert len(requests) == 2  # no corrective paid loop
        assert client.get("/api/reader/ai/ask/progress?ask_id=contract-fail").json()["stage"] is None
    assert qa_diagnostics.recent()["items"][0]["outcome"] != "returned"


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
