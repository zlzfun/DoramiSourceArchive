"""Provider-neutral transcript de-duplication policy."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.podcast_transcript_dedup import (  # noqa: E402
    deduplicate_transcript_evidence,
)


@dataclass(frozen=True)
class Evidence:
    begin_ms: int
    end_ms: int
    text: str
    channel_id: int


def test_rechunked_offset_mirror_is_removed_without_provider_types():
    segments = (
        Evidence(0, 4_000, "AI is becoming more powerful", 0),
        Evidence(4_000, 8_000, "resources are concentrated", 0),
        Evidence(
            3,
            8_003,
            "AI is becoming more powerful resources are concentrated",
            2,
        ),
    )

    result = deduplicate_transcript_evidence(segments, ())

    text = " ".join(item.text for item in result.segments)
    assert text.count("AI is becoming more powerful") == 1
    assert text.count("resources are concentrated") == 1
    assert len(result.segments) == 1
    assert len(result.fully_removed_channel_ids) == 1
    assert {match.method for match in result.matches} == {"fuzzy"}


def test_partial_mirror_keeps_unique_tail_and_its_words():
    segments = (
        Evidence(0, 4_000, "the model adds a new reasoning capability", 0),
        Evidence(4_000, 8_000, "the release is available today", 0),
        Evidence(
            5,
            8_005,
            "the model adds a new reasoning capability the release is available today",
            1,
        ),
        Evidence(8_000, 12_000, "the host adds separate primary evidence", 0),
        Evidence(12_000, 14_000, "the guest gives a unique closing argument", 1),
    )
    words = (
        Evidence(100, 500, "model", 1),
        Evidence(12_100, 12_500, "unique", 1),
    )

    result = deduplicate_transcript_evidence(segments, words)

    assert result.segments == (segments[0], segments[1], segments[3], segments[4])
    assert result.words == (words[1],)
    assert result.fully_removed_channel_ids == ()
    assert result.partially_deduplicated_channel_ids == (1,)


def test_simultaneous_distinct_speaker_is_retained():
    segments = (
        Evidence(0, 6_000, "the host asks about open source model economics", 0),
        Evidence(0, 6_000, "the guest disagrees and presents new benchmark data", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert result.segments == segments
    assert result.matches == ()


def test_short_or_reordered_utterances_are_not_fuzzy_deduplicated():
    segments = (
        Evidence(0, 1_000, "yes", 0),
        Evidence(10, 1_010, "yes", 1),
        Evidence(2_000, 5_000, "alpha beta gamma delta", 0),
        Evidence(2_010, 5_010, "delta gamma beta alpha", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert result.segments == segments
    assert result.matches == ()


def test_exact_short_match_does_not_corroborate_later_short_offset():
    segments = (
        Evidence(0, 500, "yes", 0),
        Evidence(1_000, 2_000, "okay now", 0),
        Evidence(0, 500, "yes", 1),
        Evidence(1_005, 2_005, "okay now", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert len(result.segments) == 3
    assert segments[3] in result.segments
    assert len(result.matches) == 1
    assert result.matches[0].method == "exact"


def test_chinese_rechunking_uses_ordered_character_evidence():
    segments = (
        Evidence(0, 3_000, "人工智能正在改变软件开发", 0),
        Evidence(3_000, 6_000, "开发者需要新的工作方式", 0),
        Evidence(
            20,
            6_020,
            "人工智能正在改变软件开发开发者需要新的工作方式",
            1,
        ),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert result.segments == segments[:2]
    assert result.fully_removed_channel_ids == (1,)


def test_exact_cross_channel_duplicate_is_removed_even_when_short():
    segments = (
        Evidence(0, 500, "yes", 0),
        Evidence(0, 500, "yes", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert result.segments == (segments[0],)
    assert result.matches[0].method == "exact"


def test_mirror_evidence_does_not_delete_garbled_or_unique_segment():
    segments = (
        Evidence(0, 8_000, "alpha section with enough primary evidence", 0),
        Evidence(8_000, 16_000, "beta section with more primary evidence", 0),
        Evidence(16_000, 18_000, "the reference closing sentence", 0),
        Evidence(18_000, 20_000, "separate primary evidence", 0),
        Evidence(0, 8_000, "alpha section with enough primary evidence", 1),
        Evidence(8_000, 16_000, "beta section with more primary evidence", 1),
        Evidence(16_000, 18_000, "badly garbled recognition", 1),
        Evidence(20_000, 22_000, "a unique non-overlapping tail", 1),
    )
    words = (
        Evidence(16_100, 16_500, "garbled", 1),
        Evidence(20_100, 20_500, "unique", 1),
    )

    result = deduplicate_transcript_evidence(segments, words)

    assert result.segments == (
        segments[4],
        segments[5],
        segments[2],
        segments[6],
        segments[3],
        segments[7],
    )
    assert result.words == words
    assert result.partially_deduplicated_channel_ids == (0,)


def test_shared_intro_does_not_turn_distinct_tracks_into_mirror_channel():
    segments = (
        Evidence(0, 2_000, "shared programme introduction", 0),
        Evidence(2_000, 12_000, "the host presents the primary narrative", 0),
        Evidence(0, 2_000, "shared programme introduction", 1),
        Evidence(2_000, 12_000, "the guest gives a separate translated track", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert len(result.segments) == 3
    assert sum(item.text == "shared programme introduction" for item in result.segments) == 1
    assert segments[1] in result.segments
    assert segments[3] in result.segments
    assert {match.method for match in result.matches} == {"exact"}


def test_same_segment_unique_token_is_not_deleted():
    segments = (
        Evidence(0, 2_000, "one two three four five six seven eight", 0),
        Evidence(2_000, 4_000, "extra primary timeline", 0),
        Evidence(
            0,
            2_000,
            "one two three four five six seven eight EXCLUSIVE",
            1,
        ),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert set(result.segments) == set(segments)
    assert result.matches == ()


def test_words_of_retained_distinct_segments_are_not_cross_channel_deduplicated():
    segments = (
        Evidence(0, 2_000, "AI is good", 0),
        Evidence(0, 2_000, "AI is bad", 1),
    )
    words = (
        Evidence(0, 500, "AI", 0),
        Evidence(0, 500, "AI", 1),
    )

    result = deduplicate_transcript_evidence(segments, words)

    assert result.segments == segments
    assert result.words == words


def test_unicode_tokenization_preserves_languages_and_accent_differences():
    segments = (
        Evidence(0, 2_000, "été café déjà rôle", 0),
        Evidence(0, 2_000, "ôté café déjà rôle", 1),
        Evidence(3_000, 5_000, "مرحبا بالعالم هنا اليوم", 0),
        Evidence(3_005, 5_005, "مرحبا بالعالم هنا اليوم", 2),
        Evidence(6_000, 8_000, "인공 지능 개발 방식", 0),
        Evidence(6_005, 8_005, "인공 지능 개발 방식", 3),
        Evidence(
            9_000,
            11_000,
            "ปัญญาประดิษฐ์เปลี่ยนโลก",
            0,
        ),
        Evidence(
            9_005,
            11_005,
            "ปัญญาประดิษฐ์เปลี่ยนโลก",
            4,
        ),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert segments[0] in result.segments
    assert segments[1] in result.segments
    assert len(result.segments) == 5
    assert {match.channel_id for match in result.matches} == {2, 3, 4}
    assert {match.method for match in result.matches} == {"fuzzy"}


def test_tolerance_does_not_create_overlap_between_adjacent_utterances():
    segments = (
        Evidence(0, 300, "one two three four", 0),
        Evidence(299, 599, "one two three four", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert result.segments == segments
    assert result.matches == ()


def test_output_order_is_stable_for_permuted_input():
    segments = (
        Evidence(0, 2_000, "the primary segment has enough tokens", 0),
        Evidence(0, 2_000, "the primary segment has enough tokens", 1),
        Evidence(2_000, 4_000, "a later independent segment", 2),
    )
    words = (
        Evidence(200, 500, "primary", 0),
        Evidence(2_200, 2_500, "later", 2),
    )

    original = deduplicate_transcript_evidence(segments, words)
    permuted = deduplicate_transcript_evidence(
        tuple(reversed(segments)),
        tuple(reversed(words)),
    )

    assert original.segments == permuted.segments
    assert original.words == permuted.words
    assert original.matches == permuted.matches


def test_candidate_is_not_matched_by_combining_distinct_reference_channels():
    segments = (
        Evidence(0, 4_000, "alpha beta", 0),
        Evidence(4_000, 8_000, "host primary continuation", 0),
        Evidence(0, 4_000, "gamma delta", 1),
        Evidence(4_000, 8_000, "guest separate continuation", 1),
        Evidence(0, 4_000, "alpha beta gamma delta", 2),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert set(result.segments) == set(segments)
    assert result.matches == ()


def test_short_match_is_corroborated_only_by_actual_reference_channel():
    segments = (
        Evidence(0, 4_000, "alpha beta gamma delta", 0),
        Evidence(4_000, 8_000, "host primary continuation", 0),
        Evidence(0, 4_000, "unrelated overlapping speech", 1),
        Evidence(4_000, 8_000, "yes indeed plus unrelated", 1),
        Evidence(5, 3_995, "alpha beta gamma delta", 2),
        Evidence(4_005, 6_005, "yes indeed", 2),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert segments[4] not in result.segments
    assert segments[5] in result.segments
    assert len(result.matches) == 1
    assert result.matches[0].reference_channel_ids == (0,)


def test_tokens_scattered_through_unrelated_reference_do_not_match():
    segments = (
        Evidence(
            0,
            4_000,
            "the model is powerful while the product is not always safe",
            0,
        ),
        Evidence(4_000, 6_000, "extra primary timeline", 0),
        Evidence(0, 4_000, "model is not safe", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert set(result.segments) == set(segments)
    assert result.matches == ()


def test_exact_mirror_keeps_channel_with_richer_word_evidence():
    segments = (
        Evidence(0, 2_000, "the same mirrored sentence", 0),
        Evidence(0, 2_000, "the same mirrored sentence", 1),
    )
    words = (
        Evidence(100, 400, "the", 1),
        Evidence(500, 900, "same", 1),
        Evidence(1_000, 1_500, "sentence", 1),
    )

    result = deduplicate_transcript_evidence(segments, words)

    assert result.segments == (segments[1],)
    assert result.words == words
    assert result.fully_removed_channel_ids == (0,)


def test_time_and_text_evidence_must_come_from_same_reference_channel():
    segments = (
        Evidence(800, 1_800, "alpha beta gamma delta", 0),
        Evidence(1_800, 3_000, "primary timeline extension", 0),
        Evidence(0, 1_000, "completely unrelated speech", 1),
        Evidence(1_000, 3_000, "separate channel extension", 1),
        Evidence(0, 1_000, "alpha beta gamma delta", 2),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert set(result.segments) == set(segments)
    assert result.matches == ()


def test_time_coverage_comes_only_from_segments_that_contribute_tokens():
    segments = (
        Evidence(0, 800, "unrelated filler words", 0),
        Evidence(800, 1_800, "alpha beta gamma delta", 0),
        Evidence(1_800, 3_000, "primary timeline extension", 0),
        Evidence(0, 1_000, "alpha beta gamma delta", 1),
    )

    result = deduplicate_transcript_evidence(segments, ())

    assert set(result.segments) == set(segments)
    assert result.matches == ()
