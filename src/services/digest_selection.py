"""Deterministic selection policy for a user's personal digest.

This module is deliberately free of database and LLM dependencies.  It consumes
the immutable WP-0 DTOs and returns the exact selection decisions that an edition
persists.  Subscription/permission filtering happens before this boundary; the
selector never invents candidates or relaxes the quality/mute/event boundaries.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models.analysis_contracts import (
    DigestArticleCandidateDTO,
    DigestSelectionDTO,
    InterestStance,
    PERSONAL_DIGEST_INTEREST_MAX_RATIO,
    PERSONAL_DIGEST_MIN_QUALITY_SCORE,
    PERSONAL_DIGEST_TARGET_ITEMS,
    SelectionLane,
    UserInterestDTO,
)


_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
})

GENRE_SECTIONS = {
    "model_release": "模型发布",
    "open_source_update": "开源动态",
    "research_paper": "学术论文",
    "conference": "技术大会",
    "social_discussion": "社交动态",
    "product_update": "行业资讯",
    "industry_news": "行业资讯",
    "security_incident": "行业资讯",
    "regulation": "行业资讯",
    "tutorial": "工程实践",
    "opinion": "观点洞察",
    "aggregation": "资讯聚合",
    "other": "其它",
}


@dataclass(frozen=True)
class DigestSelectionPolicy:
    """Frozen V1 policy knobs; callers may override them in focused tests/config."""

    target_items: int = PERSONAL_DIGEST_TARGET_ITEMS
    interest_max_ratio: float = PERSONAL_DIGEST_INTEREST_MAX_RATIO
    min_quality_score: float = PERSONAL_DIGEST_MIN_QUALITY_SCORE
    per_source_max: int = 2
    coverage_quality_delta: float = 0.3

    def __post_init__(self) -> None:
        if self.target_items < 1:
            raise ValueError("target_items 必须至少为 1")
        if not 0.0 <= self.interest_max_ratio <= 1.0:
            raise ValueError("interest_max_ratio 必须在 0～1 之间")
        if self.per_source_max < 1:
            raise ValueError("per_source_max 必须至少为 1")
        if self.coverage_quality_delta < 0:
            raise ValueError("coverage_quality_delta 不能为负数")


@dataclass(frozen=True)
class _RankedCandidate:
    candidate: DigestArticleCandidateDTO
    matched_codes: tuple[str, ...]
    match_priority: int
    coverage_topic_codes: tuple[str, ...]


def section_for_genre(content_genre: object) -> str:
    """Map the controlled genre to one deterministic display section."""

    value = getattr(content_genre, "value", content_genre)
    return GENRE_SECTIONS.get(str(value or "other"), "其它")


def _published_timestamp(value: str) -> float:
    raw = (value or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except ValueError:
        try:
            return dt.datetime.strptime(raw[:10], "%Y-%m-%d").replace(
                tzinfo=dt.timezone.utc
            ).timestamp()
        except ValueError:
            return 0.0


def _canonical_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.casefold()
    kept_query = [
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(kept_query), ""))


def _event_key(candidate: DigestArticleCandidateDTO) -> str:
    if candidate.duplicate_group_id is not None:
        return f"group:{candidate.duplicate_group_id}"
    canonical = _canonical_url(candidate.source_url)
    if canonical:
        return f"url:{canonical}"
    title = re.sub(r"[^\w\u3400-\u9fff]+", "", candidate.title.casefold())
    return f"title:{title or candidate.article_id}"


def _interest_maps(
    interests: Iterable[UserInterestDTO],
) -> tuple[set[str], dict[str, int]]:
    muted: set[str] = set()
    followed: dict[str, int] = {}
    for interest in interests:
        stance = getattr(interest.stance, "value", interest.stance)
        if stance == InterestStance.MUTE.value:
            muted.add(interest.tag_code)
            followed.pop(interest.tag_code, None)
        elif interest.tag_code not in muted:
            followed[interest.tag_code] = 1
    return muted, followed


def _ranked(
    candidates: Iterable[DigestArticleCandidateDTO],
    followed: dict[str, int],
    topic_codes_by_article: Mapping[str, Sequence[str]],
) -> list[_RankedCandidate]:
    rows: list[_RankedCandidate] = []
    for candidate in candidates:
        matched = tuple(sorted(code for code in set(candidate.tag_codes) if code in followed))
        rows.append(_RankedCandidate(
            candidate=candidate,
            matched_codes=matched,
            match_priority=max((followed[code] for code in matched), default=0),
            coverage_topic_codes=tuple(sorted(set(
                topic_codes_by_article.get(candidate.article_id, ())
            ))),
        ))
    return rows


def _sort_interest(row: _RankedCandidate) -> tuple[object, ...]:
    return (
        -row.match_priority,
        -row.candidate.quality_score,
        -_published_timestamp(row.candidate.publish_date),
        row.candidate.article_id,
    )


def _sort_quality(row: _RankedCandidate) -> tuple[object, ...]:
    return (
        -row.candidate.quality_score,
        -_published_timestamp(row.candidate.publish_date),
        row.candidate.article_id,
    )


def _coverage_order(
    rows: Sequence[_RankedCandidate],
    *,
    policy: DigestSelectionPolicy,
    preserve_interest_strength: bool = False,
) -> tuple[list[_RankedCandidate], dict[str, tuple[str, ...]]]:
    """Softly prefer unseen genre/tag only inside a narrow quality band."""

    remaining = list(rows)
    ordered: list[_RankedCandidate] = []
    seen_genres: set[str] = set()
    seen_tags: set[str] = set()
    adjustments: dict[str, tuple[str, ...]] = {}

    while remaining:
        anchor = remaining[0]
        band = [
            (idx, row) for idx, row in enumerate(remaining)
            if anchor.candidate.quality_score - row.candidate.quality_score
            <= policy.coverage_quality_delta + 1e-9
            and (
                not preserve_interest_strength
                or (
                    row.match_priority == anchor.match_priority
                )
            )
        ]

        def novelty(item: tuple[int, _RankedCandidate]) -> tuple[int, int, int]:
            idx, row = item
            genre = str(getattr(row.candidate.content_genre, "value", row.candidate.content_genre))
            new_genre = int(genre not in seen_genres)
            new_tags = sum(code not in seen_tags for code in row.coverage_topic_codes)
            return new_genre, new_tags, -idx

        chosen_index, chosen = max(band, key=novelty)
        labels: list[str] = []
        if chosen_index > 0:
            genre = str(getattr(chosen.candidate.content_genre, "value", chosen.candidate.content_genre))
            if genre not in seen_genres:
                labels.append("soft_coverage:genre")
            if any(code not in seen_tags for code in chosen.coverage_topic_codes):
                labels.append("soft_coverage:topic")
        if labels:
            adjustments[chosen.candidate.article_id] = tuple(labels)
        remaining.pop(chosen_index)
        ordered.append(chosen)
        seen_genres.add(str(getattr(chosen.candidate.content_genre, "value", chosen.candidate.content_genre)))
        seen_tags.update(chosen.coverage_topic_codes)
    return ordered, adjustments


def _choose_at_cap(
    interest_rows: Sequence[_RankedCandidate],
    quality_rows: Sequence[_RankedCandidate],
    *,
    target: int,
    interest_limit: int,
    source_cap: int,
) -> list[tuple[_RankedCandidate, str]]:
    selected: list[tuple[_RankedCandidate, str]] = []
    event_keys: set[str] = set()
    source_counts: dict[str, int] = {}

    def take(rows: Sequence[_RankedCandidate], lane: str, limit: int) -> None:
        for row in rows:
            if len(selected) >= target or limit <= 0:
                return
            candidate = row.candidate
            event_key = _event_key(candidate)
            if event_key in event_keys:
                continue
            if source_counts.get(candidate.source_id, 0) >= source_cap:
                continue
            selected.append((row, lane))
            event_keys.add(event_key)
            source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
            limit -= 1

    take(interest_rows, SelectionLane.INTEREST.value, interest_limit)
    take(quality_rows, SelectionLane.QUALITY.value, target - len(selected))
    return selected


def _selection_reason(
    row: _RankedCandidate,
    lane: str,
    followed: dict[str, int],
    tag_display_names: Mapping[str, str],
    source_display_names: Mapping[str, str],
) -> str:
    if lane == SelectionLane.INTEREST.value and row.matched_codes:
        code = row.matched_codes[0]
        display_name = tag_display_names.get(code, code)
        return f"匹配你关注的「{display_name}」，且是今日订阅中的高质量内容。"
    source_name = source_display_names.get(row.candidate.source_id, row.candidate.source_id)
    return f"来自你订阅的「{source_name}」，是今日订阅中的高质量内容。"


def select_digest_articles(
    candidates: Iterable[DigestArticleCandidateDTO],
    interests: Iterable[UserInterestDTO] = (),
    *,
    policy: DigestSelectionPolicy | None = None,
    topic_codes_by_article: Mapping[str, Sequence[str]] | None = None,
    tag_display_names: Mapping[str, str] | None = None,
    source_display_names: Mapping[str, str] | None = None,
) -> list[DigestSelectionDTO]:
    """Select one deterministic edition set.

    Hard rules are never relaxed: mute, minimum score, same-event uniqueness and
    the interest-share ceiling.  Only the per-source cap is relaxed, one step at a
    time, when it is the reason the target cannot otherwise be reached.
    """

    policy = policy or DigestSelectionPolicy()
    muted, followed = _interest_maps(interests)
    eligible = [
        candidate for candidate in candidates
        if candidate.quality_score >= policy.min_quality_score
        and not muted.intersection(candidate.tag_codes)
    ]
    rows = _ranked(eligible, followed, topic_codes_by_article or {})
    interest_rows = sorted((row for row in rows if row.matched_codes), key=_sort_interest)
    # A matching article belongs to the interest allocation even if its quality is
    # high; otherwise the quality lane could silently exceed the 50% product cap.
    quality_rows = sorted((row for row in rows if not row.matched_codes), key=_sort_quality)
    interest_rows, interest_adjustments = _coverage_order(
        interest_rows,
        policy=policy,
        preserve_interest_strength=True,
    )
    quality_rows, quality_adjustments = _coverage_order(quality_rows, policy=policy)

    interest_limit = min(
        len(interest_rows),
        math.floor(policy.target_items * policy.interest_max_ratio + 1e-9),
    )
    maximum_cap = max(
        policy.per_source_max,
        max(
            (
                sum(row.candidate.source_id == source_id for row in rows)
                for source_id in {row.candidate.source_id for row in rows}
            ),
            default=0,
        ),
    )
    selected: list[tuple[_RankedCandidate, str]] = []
    used_cap = policy.per_source_max
    for source_cap in range(policy.per_source_max, maximum_cap + 1):
        attempt = _choose_at_cap(
            interest_rows,
            quality_rows,
            target=policy.target_items,
            interest_limit=interest_limit,
            source_cap=source_cap,
        )
        selected = attempt
        used_cap = source_cap
        if len(attempt) >= policy.target_items:
            break

    result: list[DigestSelectionDTO] = []
    source_seen: dict[str, int] = {}
    for row, lane in selected:
        candidate = row.candidate
        source_seen[candidate.source_id] = source_seen.get(candidate.source_id, 0) + 1
        adjustments = list(
            (interest_adjustments if lane == SelectionLane.INTEREST.value else quality_adjustments)
            .get(candidate.article_id, ())
        )
        if used_cap > policy.per_source_max and source_seen[candidate.source_id] > policy.per_source_max:
            adjustments.append(f"source_limit_relaxed:{used_cap}")
        result.append(DigestSelectionDTO(
            article_id=candidate.article_id,
            lane=lane,
            matched_interest_codes=row.matched_codes,
            selection_reason=_selection_reason(
                row,
                lane,
                followed,
                tag_display_names or {},
                source_display_names or {},
            ),
            coverage_adjustments=tuple(adjustments),
        ))
    return result


__all__ = [
    "DigestSelectionPolicy",
    "GENRE_SECTIONS",
    "section_for_genre",
    "select_digest_articles",
]
