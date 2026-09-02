"""Pure policy tests for deterministic personal-digest selection."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import (  # noqa: E402
    ContentGenre,
    DigestArticleCandidateDTO,
    InterestStance,
    UserInterestDTO,
)
from services.digest_selection import DigestSelectionPolicy, select_digest_articles  # noqa: E402


def _candidate(
    number: int,
    *,
    source: str | None = None,
    score: float = 8.0,
    tags: tuple[str, ...] = (),
    genre: ContentGenre = ContentGenre.INDUSTRY_NEWS,
    group: int | None = None,
    published: str | None = None,
) -> DigestArticleCandidateDTO:
    article_id = f"a{number:02d}"
    return DigestArticleCandidateDTO(
        article_id=article_id,
        source_id=source or f"source-{number % 5}",
        title=f"Article {number}",
        source_url=f"https://example.com/{article_id}",
        publish_date=published or f"2026-09-01T{number % 10:02d}:00:00+08:00",
        fetched_date="2026-09-01T08:30:00+08:00",
        quality_score=score,
        score_reason="reason",
        one_sentence_summary="summary",
        content_genre=genre,
        tag_codes=tags,
        duplicate_group_id=group,
    )


def test_interest_is_capped_at_half_and_quality_fills_the_rest():
    candidates = [
        _candidate(i, score=9.5 - i * 0.05, tags=("agent",)) for i in range(8)
    ] + [
        _candidate(i, score=8.8 - (i - 8) * 0.05, tags=("other",)) for i in range(8, 16)
    ]
    interests = [UserInterestDTO(
        tag_code="agent", stance=InterestStance.FOLLOW
    )]

    selected = select_digest_articles(candidates, interests)

    assert len(selected) == 10
    assert sum(item.lane == "interest" for item in selected) == 5
    assert sum(item.lane == "quality" for item in selected) == 5
    assert all(item.matched_interest_codes == ("agent",) for item in selected[:5])
    assert "匹配你关注的" in selected[0].selection_reason


def test_mute_and_quality_threshold_are_hard_boundaries():
    candidates = [
        _candidate(1, score=9.8, tags=("muted",)),
        _candidate(2, score=6.99),
        _candidate(3, score=7.0),
    ]
    interests = [UserInterestDTO(tag_code="muted", stance=InterestStance.MUTE)]

    selected = select_digest_articles(candidates, interests)

    assert [item.article_id for item in selected] == ["a03"]


def test_no_interests_is_quality_only_and_repeatable():
    candidates = [
        _candidate(1, score=8.0),
        _candidate(2, score=9.0),
        _candidate(3, score=8.5),
    ]

    first = select_digest_articles(candidates)
    second = select_digest_articles(reversed(candidates))

    assert first == second
    assert [item.article_id for item in first] == ["a02", "a03", "a01"]
    assert {item.lane for item in first} == {"quality"}


def test_same_event_never_relaxes_and_source_limit_relaxes_deterministically():
    candidates = [
        _candidate(i, source="only-source", score=9.5 - i * 0.1, group=7 if i in {0, 1} else None)
        for i in range(6)
    ]
    policy = DigestSelectionPolicy(target_items=5, per_source_max=2)

    selected = select_digest_articles(
        candidates,
        policy=policy,
        topic_codes_by_article={item.article_id: item.tag_codes for item in candidates},
    )

    assert len(selected) == 5
    assert len({item.article_id for item in selected}.intersection({"a00", "a01"})) == 1
    assert any(
        adjustment.startswith("source_limit_relaxed:")
        for item in selected
        for adjustment in item.coverage_adjustments
    )
    assert selected == select_digest_articles(list(reversed(candidates)), policy=policy)


def test_soft_coverage_only_reorders_quality_near_candidates():
    candidates = [
        _candidate(1, score=9.0, tags=("same",), genre=ContentGenre.INDUSTRY_NEWS),
        _candidate(2, score=8.8, tags=("new",), genre=ContentGenre.TUTORIAL),
        _candidate(3, score=8.9, tags=("same",), genre=ContentGenre.INDUSTRY_NEWS),
        _candidate(4, score=8.0, tags=("far",), genre=ContentGenre.RESEARCH_PAPER),
    ]
    policy = DigestSelectionPolicy(target_items=4, coverage_quality_delta=0.3)

    selected = select_digest_articles(
        candidates,
        policy=policy,
        topic_codes_by_article={item.article_id: item.tag_codes for item in candidates},
    )

    assert [item.article_id for item in selected[:3]] == ["a01", "a02", "a03"]
    assert "soft_coverage:genre" in selected[1].coverage_adjustments
    assert "soft_coverage:topic" in selected[1].coverage_adjustments
    assert selected[-1].article_id == "a04"
