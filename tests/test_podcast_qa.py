"""Podcast QA searches the whole current transcript without calling providers."""

import datetime as dt
import hashlib
import json

from sqlalchemy import text
from sqlmodel import Session, select

from models.db import ArticleRecord, PodcastQaChunkRecord, PodcastTextArtifactRecord, PodcastTextPublicationRecord
from services import podcast_qa
from storage.impl.db_storage import DatabaseStorage


def _sink():
    return DatabaseStorage(db_url="sqlite:///:memory:")


def _seed(engine, body, *, episode_id="episode", source="podcast-public", version=1, kind="publisher_transcript", notes="节目简介"):
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    artifact_id = f"{episode_id}-{kind}-{version}"
    with Session(engine) as session:
        if session.get(ArticleRecord, episode_id) is None:
            session.add(ArticleRecord(id=episode_id, title="Technical discussion", content_type="podcast_episode",
                source_id=source, source_url="https://example.test/episode", publish_date=stamp,
                fetched_date=stamp, content=notes, has_content=bool(notes)))
            session.commit()
        artifact = PodcastTextArtifactRecord(id=artifact_id, episode_id=episode_id, kind=kind, version=version,
            content_hash=hashlib.sha256(body.encode()).hexdigest(), inline_text=body, language="en",
            authority_id="test-authority", provenance_json=json.dumps({"format": "text"}), created_at=stamp)
        session.add(artifact)
        session.commit()
        publication = session.get(PodcastTextPublicationRecord, f"{episode_id}:{kind}")
        if publication is None:
            publication = PodcastTextPublicationRecord(identity=f"{episode_id}:{kind}", episode_id=episode_id,
                kind=kind, artifact_id=artifact_id, authority_id="test-authority", status="published",
                published_at=stamp, updated_at=stamp)
        else:
            publication.artifact_id = artifact_id
            publication.status = "published"
            publication.published_at = stamp
            publication.unpublished_at = None
            publication.updated_at = stamp
        session.add(publication)
        session.commit()
    return artifact_id


def _search(engine, words, **kwargs):
    return podcast_qa.search_episodes(engine, keywords=words, source_ids=["podcast-public"], **kwargs)


def test_tail_passage_and_explicit_budget():
    sink = _sink()
    body = "Opening unrelated discussion.\n" * 900 + "Unique discovery: mooncheese uses quantum torque.\n" * 10
    _seed(sink.engine, body, notes="")
    hits = _search(sink.engine, ["mooncheese"])
    assert len(hits) == 1
    assert "mooncheese" in hits[0]["content"]
    assert hits[0]["qa_evidence"][0]["range_start"] > 12000
    prepared = podcast_qa.prepare_records(sink.engine, hits, keywords=["mooncheese"], per_episode_chars=500)
    assert len(prepared[0]["content"]) <= 500
    assert "mooncheese" in prepared[0]["content"]
    for item in prepared[0]["qa_evidence"]:
        assert body[item["range_start"]:item["range_end"]] == item["text"]
        assert item["text"] in prepared[0]["content"]
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "episode").content == ""


def test_publication_replace_withdraw_republish_delete():
    sink = _sink()
    _seed(sink.engine, "oldkeyword is the first version")
    old = _search(sink.engine, ["oldkeyword"])
    assert old
    _seed(sink.engine, "newkeyword belongs to the replacement", version=2)
    assert podcast_qa.revalidate_records(sink.engine, old) == []
    assert _search(sink.engine, ["oldkeyword"]) == []
    latest = _search(sink.engine, ["newkeyword"])
    assert latest
    with Session(sink.engine) as session:
        pub = session.get(PodcastTextPublicationRecord, "episode:publisher_transcript")
        pub.status = "unpublished"
        pub.unpublished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        session.add(pub)
        session.commit()
        assert session.exec(select(PodcastQaChunkRecord)).all() == []
    assert _search(sink.engine, ["newkeyword"]) == []
    assert podcast_qa.revalidate_records(sink.engine, [{"id": "episode", "source_id": "podcast-public", "evidence": latest[0]["qa_evidence"]}]) == []
    with Session(sink.engine) as session:
        pub = session.get(PodcastTextPublicationRecord, "episode:publisher_transcript")
        pub.status = "published"
        pub.unpublished_at = None
        session.add(pub)
        session.commit()
    assert _search(sink.engine, ["newkeyword"])
    with Session(sink.engine) as session:
        session.delete(session.get(ArticleRecord, "episode"))
        session.commit()
        assert session.exec(select(PodcastQaChunkRecord)).all() == []


def test_short_word_and_fts_unavailable_search_transcript():
    sink = _sink()
    _seed(sink.engine, "这场访谈深入讨论豆包推理与后续测试。")
    assert _search(sink.engine, ["豆包"])
    with sink.engine.begin() as conn:
        for suffix in ("ai", "ad", "au"):
            conn.execute(text(f"DROP TRIGGER podcast_qa_chunks_{suffix}"))
        conn.execute(text("DROP TABLE podcast_qa_fts"))
    assert _search(sink.engine, ["后续测试"])


def test_recent_empty_notes_and_scope_date_filter():
    sink = _sink()
    _seed(sink.engine, "transcript content", notes="")
    _seed(sink.engine, "other content", episode_id="other", source="private-other")
    result = _search(sink.engine, [])
    assert [r["id"] for r in result] == ["episode"]
    tomorrow = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).date().isoformat()
    assert _search(sink.engine, ["transcript"], date_gte=tomorrow) == []


def test_normalized_json_and_plain_article_unchanged():
    sink = _sink()
    body = json.dumps({"audio_duration_ms": 1000, "language": "en", "text": "secretphrase in audio",
        "segments": [{"channel": 0, "start_ms": 0, "end_ms": 1000, "text": "secretphrase in audio", "words": []}]})
    _seed(sink.engine, body, kind="normalized_transcript")
    hits = _search(sink.engine, ["secretphrase"])
    assert hits[0]["qa_basis"] == "ASR逐字稿"
    assert '"segments"' not in hits[0]["content"]
    ordinary = {"id": "article", "content_type": "rss_article", "content": "untouched"}
    assert podcast_qa.prepare_records(sink.engine, [ordinary], keywords=[])[0] is ordinary
    assert podcast_qa.revalidate_records(sink.engine, [ordinary])[0] is ordinary


def test_no_transcript_honest_show_notes():
    sink = _sink()
    with Session(sink.engine) as session:
        episode = ArticleRecord(id="episode", title="Episode", content_type="podcast_episode", source_id="podcast-public",
            source_url="https://example.test", publish_date="", fetched_date="", content="Only show notes")
        session.add(episode)
        session.commit()
        session.refresh(episode)
        result = podcast_qa.prepare_records(sink.engine, [episode], keywords=["unknown"])
    assert result[0]["qa_basis"] == "节目简介"
    assert result[0]["qa_evidence"] == []
    assert "未读取音频" in result[0]["content"]


def test_multi_keyword_coverage_and_whole_episode_sampling():
    sink = _sink()
    body = ("alphaconcept common evidence. " * 400 + "\n" +
            "middlemarker contrasting story. " * 80 + "\n" +
            "omegaconcept rare final conclusion. " * 20)
    _seed(sink.engine, body)
    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, "episode")
        evidence = podcast_qa.prepare_records(sink.engine, [record], keywords=["alphaconcept", "omegaconcept"], per_episode_chars=3000)[0]
        assert "alphaconcept" in evidence["content"]
        assert "omegaconcept" in evidence["content"]
        sampled = podcast_qa.prepare_records(sink.engine, [record], keywords=[], per_episode_chars=6000)[0]
    assert "omegaconcept" in sampled["content"]
    assert "alphaconcept" in sampled["content"]
    assert "非全文" in sampled["content"]


def test_hidden_and_credentialed_source_fail_closed():
    from models.db import SourceConfigRecord
    from services import source_visibility
    sink = _sink()
    _seed(sink.engine, "sensitivekeyword transcript")
    found = _search(sink.engine, ["sensitivekeyword"])
    assert found
    with Session(sink.engine) as session:
        source_visibility.set_source_hidden(session, "podcast-public", True)
    assert _search(sink.engine, ["sensitivekeyword"]) == []
    assert podcast_qa.revalidate_records(sink.engine, found) == []
    with Session(sink.engine) as session:
        source_visibility.set_source_hidden(session, "podcast-public", False)
        session.add(SourceConfigRecord(source_id="podcast-public", name="Private feed", source_type="rss",
            url="https://example.test/feed?token=secret", category="podcast", fetcher_id="generic_podcast_rss",
            created_at=dt.datetime.now(dt.timezone.utc).isoformat(), updated_at=dt.datetime.now(dt.timezone.utc).isoformat()))
        session.commit()
    assert _search(sink.engine, ["sensitivekeyword"]) == []
    assert podcast_qa.revalidate_records(sink.engine, found) == []


def test_malformed_artifact_cached_but_never_used(monkeypatch):
    sink = _sink()
    _seed(sink.engine, "not-json", kind="normalized_transcript")
    calls = []
    original = podcast_qa._plain_text
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(podcast_qa, "_plain_text", tracked)
    assert _search(sink.engine, ["not-json"]) == []
    assert _search(sink.engine, ["not-json"]) == []
    assert len(calls) == 1


def test_hit_boundary_keeps_neighboring_argument_and_exact_offsets():
    sink = _sink()
    body = "x" * 1550 + " unique_detector\n" + "Every night a cron produces five pull requests. " * 10
    _seed(sink.engine, body)
    result = _search(sink.engine, ["unique_detector"])[0]
    assert "Every night a cron produces five pull requests." in result["content"]
    for item in result["qa_evidence"]:
        assert item["text"] == body[item["range_start"]:item["range_end"]]


def test_short_token_does_not_broaden_multiword_phrase():
    sink = _sink()
    _seed(sink.engine, "PR comments improve our review workflow.")
    _seed(sink.engine, "PR invitations mention unrelated travel.", episode_id="distractor")
    assert [r["id"] for r in _search(sink.engine, ["PR comments"])] == ["episode"]
