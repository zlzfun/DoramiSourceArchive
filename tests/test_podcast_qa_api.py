"""Issue #248: HTTP QA uses real published passages and rechecks their lifetime.

Only the remote LLM is stubbed. SQLite FTS, publication pointers, context packing,
source permissions and the TestClient request path all run normally in tmp sinks.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_reader_ai import (
    _base_setup, _configure_llm, _enable_ai_beta, _login,
    _seed_credentialed_source, _seed_podcast_transcript,
)


def _setup(monkeypatch, tmp_path):
    app, sink = _base_setup(monkeypatch, tmp_path, "podcast_qa_api.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    return app, sink


def _episode(engine, episode_id="episode", source_id="podcast_public", *, body, notes=""):
    from models.db import ArticleRecord

    artifact_id, content_hash = _seed_podcast_transcript(
        engine, episode_id=episode_id, source_id=source_id, text=body,
    )
    with Session(engine) as session:
        article = session.get(ArticleRecord, episode_id)
        article.content = notes
        article.has_content = bool(notes)
        article.publish_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        article.fetched_date = datetime.now(timezone.utc).isoformat()
        session.add(article)
        session.commit()
    return artifact_id, content_hash


def _llm(monkeypatch, *, on_answer=None):
    from llm import prompts
    from services import reader_ai, reader_search

    calls = {"planning": [], "answer": []}

    async def fake_chat_completion(*, messages, config, **kwargs):
        content = [message.content for message in messages]
        if messages[0].content == prompts.SEARCH_PLAN_SYSTEM_PROMPT:
            calls["planning"].append(content)
            return json.dumps({"keywords": ["ORCHIDLIMIT"], "temporal": False})
        if messages[0].content == prompts.SEARCH_SELECT_SYSTEM_PROMPT:
            return json.dumps({"selected": [0]})
        calls["answer"].append(content)
        if on_answer:
            on_answer()
        return "The result depends on the late caveat. [1]"

    monkeypatch.setattr(reader_ai, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(reader_search, "chat_completion", fake_chat_completion)
    return calls


def _assert_evidence(payload, prompt, body, artifact_id, content_hash):
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["basis_label"] == "来源逐字稿"
    assert source["content_type"] == "podcast_episode"
    assert source["evidence"]
    assert any("ORCHIDLIMIT" in item["text"] for item in source["evidence"])
    for item in source["evidence"]:
        assert item["artifact_id"] == artifact_id
        assert item["content_hash"] == content_hash
        assert item["kind"] == "publisher_transcript"
        assert item["text"] == body[item["range_start"]:item["range_end"]]
        assert item["text"] in prompt
        assert f'【引用 [1]｜来源逐字稿｜相关片段 {item["range_start"]}–{item["range_end"]}' in prompt
        assert "start_seconds" not in item
    assert f'[1] {source["title"]}' in prompt


def test_article_finds_late_caveat_and_returns_exact_prompt_evidence(monkeypatch, tmp_path):
    app, sink = _setup(monkeypatch, tmp_path)
    body = ("Opening background without the requested condition. " * 800
            + "\n[zqpl]: ORCHIDLIMIT holds only for independently sampled participants.\n"
            + "Closing remarks. " * 100).strip()
    artifact_id, content_hash = _episode(sink.engine, body=body, notes="A broad interview about research.")
    calls = _llm(monkeypatch)
    with TestClient(app.app) as client:
        _login(client)
        response = client.post("/api/reader/ai/ask", json={
            "scope": "article", "article_id": "episode",
            "question": "What does the late ORCHIDLIMIT caveat imply for generalizing the result?",
        })
    assert response.status_code == 200, response.text
    prompt = calls["answer"][-1][-1]
    _assert_evidence(response.json(), prompt, body, artifact_id, content_hash)
    assert any(item["range_start"] > 12000 for item in response.json()["sources"][0]["evidence"])
    assert len(calls["answer"]) == 1


def test_subscription_finds_transcript_without_title_or_show_notes_match(monkeypatch, tmp_path):
    app, sink = _setup(monkeypatch, tmp_path)
    body = "Interview context. ORCHIDLIMIT requires independent sampling, not merely more data."
    artifact_id, content_hash = _episode(sink.engine, body=body, notes="")
    # A newer episode makes a recency fallback observably wrong.
    _episode(sink.engine, "unrelated", body="An unrelated discussion of gardening.", notes="")
    calls = _llm(monkeypatch)
    with TestClient(app.app) as client:
        _login(client)
        assert client.post("/api/reader/sources/podcast_public/subscribe").status_code == 200
        response = client.post("/api/reader/ai/ask", json={
            "scope": "subscription", "question": "Explain the ORCHIDLIMIT assumption and its implications.",
        })
    assert response.status_code == 200, response.text
    _assert_evidence(response.json(), calls["answer"][-1][-1], body, artifact_id, content_hash)
    assert response.json()["sources"][0]["id"] == "episode"
    assert "没有检索到与问题直接相关" not in calls["answer"][-1][-1]
    with Session(sink.engine) as session:
        hits = session.execute(text(
            "SELECT rowid FROM podcast_qa_fts WHERE podcast_qa_fts MATCH :query"
        ), {"query": '"orchidlimit"'}).all()
        assert hits, "The test must exercise a real passage FTS index."


@pytest.mark.parametrize("scope", ["article", "subscription"])
def test_withdrawal_during_answer_returns_409_without_old_answer_or_evidence(monkeypatch, tmp_path, scope):
    from models.db import PodcastTextPublicationRecord

    app, sink = _setup(monkeypatch, tmp_path)
    _episode(sink.engine, body="ORCHIDLIMIT is a formerly published claim.")

    def withdraw():
        with Session(sink.engine) as session:
            publication = session.get(PodcastTextPublicationRecord, "episode:publisher_transcript")
            publication.status = "unpublished"
            publication.unpublished_at = datetime.now(timezone.utc).isoformat()
            session.add(publication)
            session.commit()

    calls = _llm(monkeypatch, on_answer=withdraw)
    with TestClient(app.app) as client:
        _login(client)
        assert client.post("/api/reader/sources/podcast_public/subscribe").status_code == 200
        response = client.post("/api/reader/ai/ask", json={
            "scope": scope, "article_id": "episode", "question": "Explain ORCHIDLIMIT.",
        })
    assert len(calls["answer"]) == 1
    assert "formerly published claim" in calls["answer"][0][-1]
    assert response.status_code == 409, response.text
    assert "重新提问" in response.json()["detail"]
    assert "sources" not in response.json() and "answer" not in response.json()
    assert "formerly published claim" not in response.text


@pytest.mark.parametrize("restriction,source_id,expected", [
    ("credentialed", "credentialed_podcast", 403),
    ("hidden", "podcast_hidden", 404),
    ("private", "user_rss_other_reader", 404),
])
def test_protected_podcast_rejected_before_any_llm(monkeypatch, tmp_path, restriction, source_id, expected):
    from models.db import SourceConfigRecord
    from services import source_visibility

    app, sink = _setup(monkeypatch, tmp_path)
    _episode(sink.engine, source_id=source_id, body="ORCHIDLIMIT PRIVATE-PASSAGE-MUST-NOT-LEAVE")
    if restriction == "credentialed":
        _seed_credentialed_source(sink.engine, source_id)
    with Session(sink.engine) as session:
        if restriction == "hidden":
            source_visibility.set_source_hidden(session, source_id, True)
        elif restriction == "private":
            session.add(SourceConfigRecord(source_id=source_id, name="Other reader feed", owner_username="other", created_at=datetime.now(timezone.utc).isoformat(), updated_at=datetime.now(timezone.utc).isoformat()))
            session.commit()
    calls = _llm(monkeypatch)
    with TestClient(app.app) as client:
        _login(client)
        for scope in ("article", "articles"):
            response = client.post("/api/reader/ai/ask", json={
                "scope": scope, "article_id": "episode", "article_ids": ["episode"],
                "question": "Explain ORCHIDLIMIT.",
            })
            assert response.status_code == expected, response.text
    assert not calls["planning"] and not calls["answer"]


def test_search_scopes_exclude_credentialed_hidden_and_unsubscribed_private_transcripts(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord
    from services import source_visibility

    app, sink = _setup(monkeypatch, tmp_path)
    _episode(sink.engine, body="ORCHIDLIMIT PUBLIC-PASSAGE")
    for episode_id, source_id in [("secret", "podcast_credentialed"), ("hidden", "podcast_hidden"), ("private", "user_rss_other_reader")]:
        _episode(sink.engine, episode_id, source_id, body=f"ORCHIDLIMIT FORBIDDEN-{episode_id}-PASSAGE")
    _seed_credentialed_source(sink.engine, "podcast_credentialed")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(source_id="user_rss_other_reader", name="Private feed", owner_username="other", created_at=datetime.now(timezone.utc).isoformat(), updated_at=datetime.now(timezone.utc).isoformat()))
        session.commit()
    calls = _llm(monkeypatch)
    with TestClient(app.app) as client:
        _login(client)
        for source_id in ("podcast_public", "podcast_credentialed", "podcast_hidden"):
            assert client.post(f"/api/reader/sources/{source_id}/subscribe").status_code == 200
        with Session(sink.engine) as session:
            source_visibility.set_source_hidden(session, "podcast_hidden", True)
        for scope in ("subscription", "all"):
            response = client.post("/api/reader/ai/ask", json={"scope": scope, "question": "Explain ORCHIDLIMIT."})
            assert response.status_code == 200, response.text
            assert [source["id"] for source in response.json()["sources"]] == ["episode"]
    assert len(calls["answer"]) == 2
    sent = "\n".join(message for group in calls.values() for call in group for message in call)
    assert "PUBLIC-PASSAGE" in sent
    assert "FORBIDDEN-" not in sent
