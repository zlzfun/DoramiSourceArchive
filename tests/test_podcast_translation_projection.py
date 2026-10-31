"""Translation consumes bounded reading text while retaining source provenance."""

import asyncio
import hashlib
import json

import pytest
from sqlmodel import Session

from config import LLMConfig, PodcastConfig
from models.db import PodcastTextArtifactRecord, PodcastTextPublicationRecord
from services import podcast_text_reader, podcast_transcript_translation as translation
from tests.test_podcast_text_reader import NORMALIZED_BODY, _artifact, _setup


def _word_document(words):
    return json.dumps({"segments": [
        {"speaker": "SPEAKER_01", "body": word} for word in words
    ]}, separators=(",", ":"))


def _set_publisher(sink, body, format="json"):
    with Session(sink.engine) as session:
        source = _artifact("episode-reader", "publisher_transcript", body, version=2)
        source.provenance_json = json.dumps({"format": format})
        session.add(source)
        session.flush()
        publication = session.get(PodcastTextPublicationRecord, "episode-reader:publisher_transcript")
        publication.artifact_id = source.id
        session.add(publication)
        session.commit()
        return source.id, source.content_hash


def _load(sink, kind="publisher_transcript"):
    with Session(sink.engine) as session:
        return translation._load_input(
            session, episode_id="episode-reader", source_kind=kind, config=PodcastConfig(),
        )


def test_repeated_speaker_overhead_no_longer_exceeds_translation_limit(monkeypatch, tmp_path):
    _, sink = _setup(monkeypatch, tmp_path)
    words = ["hello"] * 9707 + ["helloworld!"]
    body = _word_document(words)
    source_id, source_hash = _set_publisher(sink, body)
    _, source, text = _load(sink)
    assert len(podcast_text_reader._plain_text(source, PodcastConfig())) == 174_749
    assert len(text) < 120_000 == PodcastConfig().premium_transcript_max_chars
    assert text == podcast_text_reader._plain_text(source, PodcastConfig(), for_reading=True)
    assert source.id == source_id
    assert source.content_hash == source_hash == hashlib.sha256(body.encode()).hexdigest()
    assert source.inline_text == body
    assert " ".join(p.removeprefix("SPEAKER_01: ") for p in text.split("\n\n")).split() == words

    # Readable paragraphs stay below the translator's chunk ceiling, so it
    # batches whole paragraphs without cutting words or dropping separators.
    chunks = translation._split_for_translation(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 3500 for chunk in chunks)
    assert "\n\n".join(chunks) == text
    assert all(chunk.startswith("SPEAKER_01: ") for chunk in chunks)


def test_truly_long_readable_input_still_rejected(monkeypatch, tmp_path):
    _, sink = _setup(monkeypatch, tmp_path)
    _set_publisher(sink, _word_document(["helloworld"] * 12_000))
    with pytest.raises(translation.PodcastTranscriptTranslationError, match="120000") as error:
        _load(sink)
    assert error.value.status_code == 413


@pytest.mark.parametrize("kind", ["publisher_transcript", "normalized_transcript"])
def test_plain_text_and_asr_inputs_remain_unchanged(monkeypatch, tmp_path, kind):
    _, sink = _setup(monkeypatch, tmp_path)
    body = "First sentence.\nSecond sentence."
    if kind == "publisher_transcript":
        _set_publisher(sink, body, format="text")
        expected = body
    else:
        expected = json.loads(NORMALIZED_BODY)["text"]
    _, source, text = _load(sink, kind)
    assert text == expected == podcast_text_reader._plain_text(source, PodcastConfig())


def test_old_pipeline_cache_is_replaced_then_new_projection_is_cached(monkeypatch, tmp_path):
    _, sink = _setup(monkeypatch, tmp_path)
    source_id, source_hash = _set_publisher(sink, _word_document(["These", "words", "form", "one", "paragraph."]))
    _, source, expected_input = _load(sink)
    with Session(sink.engine) as session:
        # The Reader fixture starts with a remote-authority translation. This
        # scenario models an existing local reader translation instead.
        session.delete(session.get(PodcastTextPublicationRecord, "episode-reader:transcript_zh"))
        session.commit()
        with monkeypatch.context() as old_pipeline:
            old_pipeline.setattr(translation, "PIPELINE_VERSION", "reader-transcript-translation-v2")
            old = translation._publish(
                session, episode_id="episode-reader", source=source,
                translated_text="旧逐词译文", config=PodcastConfig(),
            )
        old_id = old.id
        assert translation._cached_translation(session, episode_id="episode-reader", source=source) is None

    inputs = []

    async def fake_translate(*, text, **kwargs):
        inputs.append(text)
        return "这些单词组成一个自然段。"

    monkeypatch.setattr(translation, "_translate", fake_translate)

    async def exercise():
        args = dict(
            episode_id="episode-reader", source_kind="publisher_transcript",
            config=PodcastConfig(), llm_config=LLMConfig(), usage_meta=None,
        )
        first, cached = await translation.translate_transcript(sink.engine, **args)
        assert not cached
        second, cached = await translation.translate_transcript(sink.engine, **args)
        assert cached
        assert second.id == first.id != old_id
        assert first.source_artifact_id == source_id
        assert first.source_content_hash == source_hash
        assert json.loads(first.provenance_json)["pipeline"] == "reader-transcript-translation-v3"

    asyncio.run(exercise())
    assert inputs == [expected_input] == ["SPEAKER_01: These words form one paragraph."]
    with Session(sink.engine) as session:
        stored_source = session.get(PodcastTextArtifactRecord, source_id)
        assert stored_source.inline_text == source.inline_text
        assert stored_source.content_hash == source_hash
