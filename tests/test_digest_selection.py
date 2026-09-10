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
from services.digest_selection import (  # noqa: E402
    BreakingSelectionPolicy,
    DigestSelectionPolicy,
    select_breaking_events,
    select_digest_articles,
)


def _candidate(
    number: int,
    *,
    source: str | None = None,
    score: float = 8.0,
    tags: tuple[str, ...] = (),
    genre: ContentGenre = ContentGenre.INDUSTRY_NEWS,
    group: int | None = None,
    published: str | None = None,
    role: str = "media",
    shape: str = "article",
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
        content_genre=genre,
        tag_codes=tags,
        duplicate_group_id=group,
        source_role=role,
        content_shape=shape,
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
    assert "命中你的兴趣" in selected[0].selection_reason


def test_interest_ceiling_applies_to_actual_sparse_output():
    candidates = [
        *[_candidate(i, score=9.5 - i * 0.05, tags=("agent",)) for i in range(5)],
        _candidate(9, score=8.0, tags=("other",)),
    ]

    selected = select_digest_articles(
        candidates,
        [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)],
    )

    assert len(selected) == 2
    assert sum(item.lane == "interest" for item in selected) == 1
    assert sum(item.lane == "quality" for item in selected) == 1


def test_cross_lane_event_conflict_does_not_hide_a_larger_legal_set():
    candidates = [
        _candidate(1, score=9.5, tags=("agent",), group=7),
        _candidate(2, score=9.0, tags=("agent",)),
        _candidate(3, score=8.5, tags=("other",), group=7),
    ]

    selected = select_digest_articles(
        candidates,
        [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)],
    )

    assert [item.article_id for item in selected] == ["a03", "a02"]
    assert [item.lane for item in selected] == ["quality", "interest"]


def test_mute_and_quality_threshold_are_hard_boundaries():
    candidates = [
        _candidate(1, score=9.8, tags=("muted",)),
        _candidate(2, score=4.99),
        _candidate(3, score=5.0),
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


# ── 「重大事件」通道(v3.50,issue #33 §2)──

def test_breaking_official_single_source_qualifies_but_lone_media_does_not():
    candidates = [
        _candidate(1, source="rss_openai_news", role="official", score=9.5, tags=("entity.openai",)),
        _candidate(2, source="web_aiera", role="media", score=9.2, tags=("entity.anthropic",)),
    ]

    selected = select_breaking_events(candidates, source_display_names={"rss_openai_news": "OpenAI 新闻"})

    assert [item.article_id for item in selected] == ["a01"]
    assert selected[0].lane == "breaking"
    assert selected[0].breaking_basis == "official"
    assert selected[0].breaking_source_count == 1
    assert selected[0].event_entity_codes == ("entity.openai",)
    assert selected[0].selection_reason == "今日重大事件 · 「OpenAI 新闻」官方一手发布，不在你的订阅内也为你保留。"


def test_breaking_corroboration_needs_two_sources_and_one_over_the_line():
    corroborated = [
        _candidate(1, source="rss_testingcatalog", score=9.2, tags=("entity.anthropic", "entity.claude")),
        _candidate(2, source="web_aiera", score=8.6, tags=("entity.anthropic",)),
    ]
    both_below = [
        _candidate(3, source="rss_the_decoder", score=8.9, tags=("entity.nvidia",)),
        _candidate(4, source="web_qbitai", score=8.8, tags=("entity.nvidia",)),
    ]
    same_source_twice = [
        _candidate(5, source="web_aiera", score=9.4, tags=("entity.google",)),
        _candidate(6, source="web_aiera", score=9.1, tags=("entity.google",)),
    ]

    selected = select_breaking_events(
        corroborated + both_below + same_source_twice,
        subscribed_source_ids=["rss_testingcatalog"],
        source_display_names={"rss_testingcatalog": "TestingCatalog"},
    )

    assert [item.article_id for item in selected] == ["a01"]
    assert selected[0].breaking_basis == "corroborated"
    assert selected[0].breaking_source_count == 2
    assert selected[0].event_entity_codes == ("entity.anthropic", "entity.claude")
    assert selected[0].selection_reason == "今日重大事件 · 2 家来源同时报道，代表来源「TestingCatalog」。"


def test_breaking_merges_shared_entities_and_prefers_official_article_over_tweet():
    candidates = [
        _candidate(1, source="x_openai", role="official", shape="social", score=9.8, tags=("entity.openai",)),
        _candidate(2, source="rss_openai_news", role="official", shape="article", score=9.5, tags=("entity.openai", "entity.chatgpt")),
        _candidate(3, source="web_qbitai", role="media", score=9.5, tags=("entity.chatgpt",)),
        _candidate(4, source="x_sama", role="personal", shape="social", score=9.8, tags=("entity.openai",)),
    ]

    selected = select_breaking_events(candidates, policy=BreakingSelectionPolicy(max_items=3))

    assert [item.article_id for item in selected] == ["a02"]
    assert selected[0].breaking_source_count == 4
    assert selected[0].event_entity_codes == ("entity.chatgpt", "entity.openai")


def test_breaking_suppresses_entities_seen_in_recent_headlines_and_respects_cap():
    candidates = [
        _candidate(1, source="rss_openai_news", role="official", score=9.6, tags=("entity.openai",)),
        _candidate(2, source="rss_nvidia_genai", role="official", score=9.5, tags=("entity.nvidia", "entity.hugging-face")),
        _candidate(3, source="rss_deepmind_blog", role="official", score=9.1, tags=("entity.google-deepmind",)),
        _candidate(4, source="rss_mistral_news", role="official", score=9.0, tags=("entity.mistral",)),
    ]

    selected = select_breaking_events(
        candidates,
        previous_breaking_entities=[("entity.openai", "entity.chatgpt")],
        policy=BreakingSelectionPolicy(max_items=2),
    )

    assert [item.article_id for item in selected] == ["a02", "a03"]
    assert select_breaking_events(candidates, policy=BreakingSelectionPolicy(max_items=0)) == []


def test_breaking_mute_and_exclusions_are_hard_and_output_is_deterministic():
    candidates = [
        _candidate(1, source="rss_openai_news", role="official", score=9.9, tags=("entity.openai", "topic.agents")),
        _candidate(2, source="rss_nvidia_genai", role="official", score=9.5, tags=("entity.nvidia",)),
        _candidate(3, source="rss_deepmind_blog", role="official", score=9.5),
    ]
    interests = [UserInterestDTO(tag_code="topic.agents", stance=InterestStance.MUTE)]

    first = select_breaking_events(candidates, interests, excluded_article_ids={"a02"})
    second = select_breaking_events(list(reversed(candidates)), interests, excluded_article_ids={"a02"})

    assert first == second
    assert [item.article_id for item in first] == ["a03"]
    assert first[0].event_entity_codes == ()
