"""Reading projection repairs word timestamps without changing publisher evidence."""

import json

import pytest

from services.podcast_publisher_transcripts import (
    PublisherTranscriptMalformed,
    parse_transcript,
)


def _body(segments):
    return json.dumps({"segments": segments}, ensure_ascii=False).encode()


def _words(texts, *, speaker="SPEAKER_01", timed=True):
    return [
        {"body": text, "speaker": speaker,
         **({"startTime": index / 2, "endTime": index / 2 + .4} if timed else {})}
        for index, text in enumerate(texts)
    ]


def _parse(body, *, for_reading=True, format="json", **limits):
    return parse_transcript(body, format, for_reading=for_reading, **{
        "max_segments": 20_000, "max_text_chars": 1_000_000, **limits,
    })


def test_word_timestamps_read_as_paragraph_without_changing_evidence_or_default():
    words = "I just happened to have some real world experience with this just recently.".split()
    body = _body(_words(words))
    default = _parse(body, for_reading=False)
    reading = _parse(body)
    assert default.text == "\n".join(f"SPEAKER_01: {word}" for word in words)
    assert parse_transcript(body, "json", max_segments=20_000, max_text_chars=1_000_000) == default
    assert reading.text == "SPEAKER_01: " + " ".join(words)
    assert reading.source_text == default.source_text == body.decode()
    assert reading.segment_count == default.segment_count == len(words)


def test_speaker_changes_and_unknown_boundaries_keep_short_turns_separate():
    segments = _words(["I", "agree.", "So", "do", "I.", "We", "agree", "too."])
    for index in (2, 3, 4):
        segments[index]["speaker"] = "GUEST"
    for index in (5, 6):
        del segments[index]["speaker"]
    assert _parse(_body(segments)).text == (
        "SPEAKER_01: I agree.\n\nGUEST: So do I.\n\nWe agree\n\nSPEAKER_01: too."
    )


def test_only_a_pause_greater_than_two_seconds_breaks_a_paragraph():
    segments = _words(["One", "two", "three", "four", "five", "six"])
    for index, segment in enumerate(segments):
        segment["startTime"] = [0, .5, 2.9, 5.401, 5.9, 6.4][index]
        segment["endTime"] = segment["startTime"] + .4
    assert _parse(_body(segments)).text == (
        "SPEAKER_01: One two three\n\nSPEAKER_01: four five six"
    )


@pytest.mark.parametrize("timing", ["missing", "zero", "mixed"])
def test_missing_and_zero_duration_times_do_not_disable_word_grouping(timing):
    segments = _words(["Words", "still", "form", "a", "sentence."], timed=False)
    for index, segment in enumerate(segments):
        if timing == "zero" or (timing == "mixed" and index % 2):
            segment.update(start=index / 2, end=index / 2)
    assert _parse(_body(segments)).text == "SPEAKER_01: Words still form a sentence."


def test_joining_preserves_punctuation_contractions_abbreviations_and_repetitions():
    tokens = ["Dr.", "Lee", "said", ":", '"', "I", "'", "m", "in", "the", "U.S.",
              ",", "I", "do", "n't", "know", "John", "'s", "plan", ".", '"', "Really", "really", "?"]
    assert _parse(_body(_words(tokens))).text == (
        'SPEAKER_01: Dr. Lee said: "I\'m in the U.S., I don\'t know John\'s plan." Really really?'
    )


def test_cjk_tokens_and_punctuation_join_without_spurious_spaces():
    assert _parse(_body(_words(["今天", "我们", "讨论", "AI", "模型", "，", "非常", "有趣", "。"]))).text == (
        "SPEAKER_01: 今天我们讨论 AI 模型，非常有趣。"
    )


def test_long_speech_breaks_at_sentences_then_at_hard_limit_without_losing_words():
    sentence = "These words form a sentence with a reasonable amount of detail."
    tokens = (sentence.split() * 20) + (["unpunctuated"] * 200)
    paragraphs = _parse(_body(_words(tokens, timed=False))).text.split("\n\n")
    assert len(paragraphs) > 4
    assert all(len(paragraph) <= 600 for paragraph in paragraphs)
    assert paragraphs[0].endswith("detail.")
    assert " ".join(paragraph.removeprefix("SPEAKER_01: ") for paragraph in paragraphs).split() == tokens


@pytest.mark.parametrize("texts", [
    ["This is already a complete sentence.", "And this is another sentence."],
    ["Yes.", "No!", "Maybe?", "Agreed."],
    ["One", "short", "fragment"],
    ["这是一句完整的中文。", "这是另外一个已有句段。"],
])
def test_existing_sentence_segments_and_ambiguous_short_runs_are_unchanged(texts):
    body = _body(_words(texts))
    assert _parse(body) == _parse(body, for_reading=False)


def test_mixed_word_and_sentence_segments_only_reflow_word_runs():
    body = _body(_words(["Existing sentence remains intact.", "These", "words", "form", "another", "sentence.", "A final sentence."]))
    assert _parse(body).text == (
        "SPEAKER_01: Existing sentence remains intact.\n"
        "SPEAKER_01: These words form another sentence.\n"
        "SPEAKER_01: A final sentence."
    )


@pytest.mark.parametrize(("format", "body"), [
    ("text", b"Alice: Hello\nAlice: world\n"),
    ("vtt", b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n\n00:00:01.000 --> 00:00:02.000\nworld\n"),
    ("srt", b"1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nworld\n"),
])
def test_non_json_formats_are_unchanged(format, body):
    assert _parse(body, format=format) == _parse(body, format=format, for_reading=False)


@pytest.mark.parametrize("for_reading", [True, False])
def test_segment_source_and_default_projection_limits_cannot_be_bypassed(for_reading):
    body = _body(_words(["a", "b", "c", "d"]))
    with pytest.raises(PublisherTranscriptMalformed, match="segment"):
        _parse(body, for_reading=for_reading, max_segments=3)
    with pytest.raises(PublisherTranscriptMalformed, match="字符上限"):
        _parse(body, for_reading=for_reading, max_text_chars=len(body.decode()) - 1)



@pytest.mark.parametrize("invalid", [
    {"body": "bad", "start": 1},
    {"body": "bad", "start": 2, "end": 1},
    {"body": "bad", "start": -1, "end": 1},
    {"body": "bad", "start": float("inf"), "end": float("inf")},
    {"body": ""},
])
def test_reading_never_masks_invalid_source_segments(invalid):
    with pytest.raises(PublisherTranscriptMalformed):
        _parse(_body(_words(["The", "valid", "words", "first"]) + [invalid]))
