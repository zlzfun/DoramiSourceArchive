"""Provider-neutral, conservative de-duplication for multi-channel transcripts.

ASR vendors may return the same mixed programme once per physical audio channel.
This module works on structural timed-text objects instead of provider payloads so
every adapter can apply the same policy before materializing a canonical transcript.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


TIME_TOLERANCE_MS = 250
MIN_RAW_TEMPORAL_COVERAGE = 0.50
MIN_TEMPORAL_COVERAGE = 0.80
MIN_TOKEN_COVERAGE = 1.0
MIN_FUZZY_TOKENS = 4
CORROBORATED_MIN_TEMPORAL_COVERAGE = 0.95
CORROBORATED_MIN_TOKEN_COVERAGE = 1.0
CORROBORATED_MIN_FUZZY_TOKENS = 2
MAX_SEQUENCE_TOKENS = 2_048
MAX_SEQUENCE_CHARACTERS = 65_536

_OVERFLOW_TOKENS = ("",) * (MAX_SEQUENCE_TOKENS + 1)


class TimedText(Protocol):
    begin_ms: int
    end_ms: int
    text: str
    channel_id: int


SegmentT = TypeVar("SegmentT", bound=TimedText)
WordT = TypeVar("WordT", bound=TimedText)


@dataclass(frozen=True)
class DuplicateMatch:
    segment_index: int
    channel_id: int
    reference_channel_ids: tuple[int, ...]
    method: str
    temporal_coverage: float
    token_coverage: float


@dataclass(frozen=True)
class TranscriptDeduplication(Generic[SegmentT, WordT]):
    segments: tuple[SegmentT, ...]
    words: tuple[WordT, ...]
    matches: tuple[DuplicateMatch, ...]
    fully_removed_channel_ids: tuple[int, ...]
    partially_deduplicated_channel_ids: tuple[int, ...]

    @property
    def removed_segment_count(self) -> int:
        return len(self.matches)


def _tokens(value: str) -> tuple[str, ...]:
    """Tokenize Unicode text without silently discarding writing systems."""

    result: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            result.append("".join(current))
            current.clear()

    normalized = unicodedata.normalize("NFKC", value).casefold()
    if len(normalized) > MAX_SEQUENCE_CHARACTERS:
        return _OVERFLOW_TOKENS
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("M"):
            if current:
                current.append(character)
            elif result:
                result[-1] += character
        elif not category.startswith(("L", "N")):
            flush()
        elif category.startswith("N") or "LATIN" in unicodedata.name(
            character,
            "",
        ):
            current.append(character)
        else:
            flush()
            result.append(character)
        if len(result) > MAX_SEQUENCE_TOKENS:
            break
    flush()
    return tuple(result)


@dataclass(frozen=True)
class _ReferenceIndex:
    items: tuple[TimedText, ...]
    begin_times: tuple[int, ...]
    prefix_max_end_times: tuple[int, ...]

    @classmethod
    def build(cls, items: tuple[TimedText, ...]) -> "_ReferenceIndex":
        ordered = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.begin_ms,
                    item.end_ms,
                    item.channel_id,
                    item.text,
                ),
            )
        )
        prefix_max: list[int] = []
        maximum = -1
        for item in ordered:
            maximum = max(maximum, item.end_ms)
            prefix_max.append(maximum)
        return cls(
            ordered,
            tuple(item.begin_ms for item in ordered),
            tuple(prefix_max),
        )

    def overlapping(
        self, candidate: TimedText, *, tolerance_ms: int
    ) -> tuple[TimedText, ...]:
        right = bisect_left(
            self.begin_times,
            candidate.end_ms + tolerance_ms,
        )
        left = bisect_right(
            self.prefix_max_end_times,
            candidate.begin_ms - tolerance_ms,
            hi=right,
        )
        return tuple(
            item
            for item in self.items[left:right]
            if min(candidate.end_ms, item.end_ms + tolerance_ms)
            > max(candidate.begin_ms, item.begin_ms - tolerance_ms)
        )


def _merged_intervals(
    items: tuple[TimedText, ...], *, tolerance_ms: int = 0
) -> tuple[tuple[int, int], ...]:
    intervals: list[list[int]] = []
    for item in sorted(items, key=lambda value: (value.begin_ms, value.end_ms)):
        start = max(0, int(item.begin_ms) - tolerance_ms)
        end = int(item.end_ms) + tolerance_ms
        if end <= start:
            continue
        if not intervals or start > intervals[-1][1]:
            intervals.append([start, end])
            continue
        intervals[-1][1] = max(intervals[-1][1], end)
    return tuple((start, end) for start, end in intervals)


def _timeline_duration(items: tuple[TimedText, ...]) -> int:
    return sum(end - start for start, end in _merged_intervals(items))


def _interval_coverage(
    candidate: TimedText,
    references: tuple[TimedText, ...],
    *,
    tolerance_ms: int,
) -> float:
    duration = int(candidate.end_ms) - int(candidate.begin_ms)
    if duration <= 0:
        return 0.0
    overlap = 0
    for start, end in _merged_intervals(references, tolerance_ms=tolerance_ms):
        overlap += max(
            0,
            min(int(candidate.end_ms), end)
            - max(int(candidate.begin_ms), start),
        )
    return min(1.0, overlap / duration)


def _contiguous_match_starts(
    candidate_tokens: tuple[str, ...], reference_tokens: tuple[str, ...]
) -> tuple[int, ...]:
    if not candidate_tokens or not reference_tokens:
        return ()
    if (
        len(candidate_tokens) > MAX_SEQUENCE_TOKENS
        or len(reference_tokens) > MAX_SEQUENCE_TOKENS
    ):
        return ()
    if len(candidate_tokens) > len(reference_tokens):
        return ()
    prefix = [0] * len(candidate_tokens)
    matched = 0
    for index in range(1, len(candidate_tokens)):
        while matched and candidate_tokens[index] != candidate_tokens[matched]:
            matched = prefix[matched - 1]
        if candidate_tokens[index] == candidate_tokens[matched]:
            matched += 1
        prefix[index] = matched
    starts: list[int] = []
    matched = 0
    for index, token in enumerate(reference_tokens):
        while matched and token != candidate_tokens[matched]:
            matched = prefix[matched - 1]
        if token == candidate_tokens[matched]:
            matched += 1
            if matched == len(candidate_tokens):
                starts.append(index - len(candidate_tokens) + 1)
                matched = prefix[matched - 1]
    return tuple(starts)


def _reference_sequences(
    references: tuple[TimedText, ...],
) -> tuple[
    tuple[int, tuple[str, ...], tuple[TimedText, ...]],
    ...,
]:
    by_channel: dict[int, list[TimedText]] = {}
    for item in references:
        by_channel.setdefault(item.channel_id, []).append(item)
    ordered_groups = [
        sorted(items, key=lambda item: (item.begin_ms, item.end_ms))
        for _, items in sorted(by_channel.items())
    ]
    sequences: list[
        tuple[int, tuple[str, ...], tuple[TimedText, ...]]
    ] = []
    for group in ordered_groups:
        tokens: list[str] = []
        owners: list[TimedText] = []
        character_count = 0
        for item in group:
            character_count += len(item.text)
            if character_count > MAX_SEQUENCE_CHARACTERS:
                tokens = list(_OVERFLOW_TOKENS)
                owners = [item] * len(tokens)
                break
            item_tokens = _tokens(item.text)
            tokens.extend(item_tokens)
            owners.extend([item] * len(item_tokens))
            if len(tokens) > MAX_SEQUENCE_TOKENS:
                break
        sequences.append(
            (group[0].channel_id, tuple(tokens), tuple(owners))
        )
    return tuple(sequences)


def _duplicate_match(
    candidate: TimedText,
    references: tuple[TimedText, ...],
    *,
    segment_index: int,
    tolerance_ms: int,
    min_temporal_coverage: float,
    min_token_coverage: float,
    min_fuzzy_tokens: int,
) -> DuplicateMatch | None:
    exact = tuple(
        item
        for item in references
        if item.begin_ms == candidate.begin_ms
        and item.end_ms == candidate.end_ms
        and item.text == candidate.text
    )
    if exact:
        return DuplicateMatch(
            segment_index=segment_index,
            channel_id=candidate.channel_id,
            reference_channel_ids=tuple(
                sorted({item.channel_id for item in exact})
            ),
            method="exact",
            temporal_coverage=1.0,
            token_coverage=1.0,
        )

    candidate_tokens = _tokens(candidate.text)
    if len(candidate_tokens) < min_fuzzy_tokens:
        return None
    qualified_by_channel: dict[int, float] = {}
    for channel_id, sequence, owners in _reference_sequences(references):
        for start in _contiguous_match_starts(candidate_tokens, sequence):
            matched_owners = tuple(
                {
                    id(item): item
                    for item in owners[
                        start : start + len(candidate_tokens)
                    ]
                }.values()
            )
            raw_temporal_coverage = _interval_coverage(
                candidate,
                matched_owners,
                tolerance_ms=0,
            )
            temporal_coverage = _interval_coverage(
                candidate,
                matched_owners,
                tolerance_ms=tolerance_ms,
            )
            if (
                raw_temporal_coverage >= MIN_RAW_TEMPORAL_COVERAGE
                and temporal_coverage >= min_temporal_coverage
                and 1.0 >= min_token_coverage
            ):
                qualified_by_channel[channel_id] = max(
                    qualified_by_channel.get(channel_id, 0.0),
                    temporal_coverage,
                )
    qualified = tuple(sorted(qualified_by_channel.items()))
    if not qualified:
        return None
    return DuplicateMatch(
        segment_index=segment_index,
        channel_id=candidate.channel_id,
        reference_channel_ids=tuple(
            channel_id for channel_id, _ in qualified
        ),
        method="fuzzy",
        temporal_coverage=max(coverage for _, coverage in qualified),
        token_coverage=1.0,
    )


def deduplicate_transcript_evidence(
    segments: tuple[SegmentT, ...],
    words: tuple[WordT, ...],
) -> TranscriptDeduplication[SegmentT, WordT]:
    """Remove only cross-channel segments proven to repeat retained evidence.

    Channels are considered in deterministic evidence-richness order, but the
    decision and deletion unit is one segment.  A partly mirrored channel keeps
    every segment that contains unproven or unique content.
    """

    ordered_words = tuple(
        sorted(
            words,
            key=lambda item: (
                item.begin_ms,
                item.end_ms,
                item.channel_id,
                item.text,
            ),
        )
    )
    if not segments:
        return TranscriptDeduplication((), ordered_words, (), (), ())

    ordered_items = tuple(
        sorted(
            segments,
            key=lambda indexed: (
                indexed.begin_ms,
                indexed.end_ms,
                indexed.channel_id,
                indexed.text,
            ),
        )
    )
    ordered_segments = tuple(enumerate(ordered_items))
    by_channel: dict[int, list[tuple[int, SegmentT]]] = {}
    for index, item in ordered_segments:
        by_channel.setdefault(item.channel_id, []).append((index, item))
    words_by_channel: dict[int, tuple[WordT, ...]] = {
        channel_id: tuple(
            word for word in ordered_words if word.channel_id == channel_id
        )
        for channel_id in by_channel
    }
    ordered_channels = sorted(
        by_channel,
        key=lambda channel_id: (
            -_timeline_duration(
                tuple(item for _, item in by_channel[channel_id])
            ),
            -len(words_by_channel[channel_id]),
            -sum(len(word.text) for word in words_by_channel[channel_id]),
            -sum(len(item.text) for _, item in by_channel[channel_id]),
            channel_id,
        ),
    )

    retained_indices = {index for index, _ in by_channel[ordered_channels[0]]}
    matches: list[DuplicateMatch] = []
    for channel_id in ordered_channels[1:]:
        reference_items = tuple(
            item
            for reference_index, item in ordered_segments
            if reference_index in retained_indices
            and item.channel_id != channel_id
        )
        reference_index = _ReferenceIndex.build(reference_items)
        pending: list[tuple[int, SegmentT]] = []
        for index, candidate in by_channel[channel_id]:
            references = reference_index.overlapping(
                candidate,
                tolerance_ms=TIME_TOLERANCE_MS,
            )
            match = _duplicate_match(
                candidate,
                references,
                segment_index=index,
                tolerance_ms=TIME_TOLERANCE_MS,
                min_temporal_coverage=MIN_TEMPORAL_COVERAGE,
                min_token_coverage=MIN_TOKEN_COVERAGE,
                min_fuzzy_tokens=MIN_FUZZY_TOKENS,
            )
            if match is None:
                pending.append((index, candidate))
            else:
                matches.append(match)
        corroborated_channels = {
            reference_channel_id
            for match in matches
            if match.channel_id == channel_id
            and len(_tokens(ordered_items[match.segment_index].text))
            >= MIN_FUZZY_TOKENS
            for reference_channel_id in match.reference_channel_ids
        }
        corroborated_index = _ReferenceIndex.build(
            tuple(
                item
                for item in reference_items
                if item.channel_id in corroborated_channels
            )
        )
        for index, candidate in pending:
            references = corroborated_index.overlapping(
                candidate,
                tolerance_ms=TIME_TOLERANCE_MS,
            )
            match = _duplicate_match(
                candidate,
                references,
                segment_index=index,
                tolerance_ms=TIME_TOLERANCE_MS,
                min_temporal_coverage=max(
                    MIN_TEMPORAL_COVERAGE,
                    CORROBORATED_MIN_TEMPORAL_COVERAGE,
                ),
                min_token_coverage=max(
                    MIN_TOKEN_COVERAGE,
                    CORROBORATED_MIN_TOKEN_COVERAGE,
                ),
                min_fuzzy_tokens=min(
                    MIN_FUZZY_TOKENS,
                    CORROBORATED_MIN_FUZZY_TOKENS,
                ),
            )
            if match is None:
                retained_indices.add(index)
            else:
                matches.append(match)

    retained_segments = tuple(
        item for index, item in ordered_segments if index in retained_indices
    )
    retained_by_channel: dict[int, tuple[SegmentT, ...]] = {
        channel_id: tuple(
            item for item in retained_segments if item.channel_id == channel_id
        )
        for channel_id in by_channel
    }
    original_by_channel: dict[int, tuple[SegmentT, ...]] = {
        channel_id: tuple(item for _, item in indexed)
        for channel_id, indexed in by_channel.items()
    }

    retained_words: list[WordT] = []
    for word in ordered_words:
        original_containers = tuple(
            segment
            for segment in original_by_channel.get(word.channel_id, ())
            if segment.begin_ms <= word.begin_ms and word.end_ms <= segment.end_ms
        )
        retained_containers = tuple(
            segment
            for segment in retained_by_channel.get(word.channel_id, ())
            if segment.begin_ms <= word.begin_ms and word.end_ms <= segment.end_ms
        )
        if original_containers and not retained_containers:
            continue
        retained_words.append(word)

    removed_by_channel = {channel_id: 0 for channel_id in by_channel}
    for match in matches:
        removed_by_channel[match.channel_id] += 1
    fully_removed = tuple(
        sorted(
            channel_id
            for channel_id, indexed in by_channel.items()
            if indexed and removed_by_channel[channel_id] == len(indexed)
        )
    )
    partial = tuple(
        sorted(
            channel_id
            for channel_id, count in removed_by_channel.items()
            if 0 < count < len(by_channel[channel_id])
        )
    )
    return TranscriptDeduplication(
        segments=retained_segments,
        words=tuple(retained_words),
        matches=tuple(matches),
        fully_removed_channel_ids=fully_removed,
        partially_deduplicated_channel_ids=partial,
    )


__all__ = [
    "DuplicateMatch",
    "TranscriptDeduplication",
    "deduplicate_transcript_evidence",
]
