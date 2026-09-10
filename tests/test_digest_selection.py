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


# ── v3.53「订阅 ∪ 兴趣」(issue #33 §3 前置):订阅外候选只进兴趣通道、门槛更高、每源硬上限 ──


def _external(number: int, **kwargs) -> DigestArticleCandidateDTO:
    return _candidate(number, **kwargs).model_copy(update={"subscribed": False})


def test_external_candidates_need_the_higher_floor_and_an_interest_hit():
    interests = [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)]
    candidates = [
        _candidate(1, source="sub", score=5.2, tags=("other",)),          # 订阅内质量通道,5.0 线上
        _external(2, source="ext-a", score=5.8, tags=("agent",)),          # 订阅外命中但 < 6.0 → 不进
        _external(3, source="ext-b", score=6.4, tags=("agent",)),          # 订阅外命中 ≥ 6.0 → 兴趣通道
        _external(4, source="ext-c", score=9.9, tags=("other",)),          # 订阅外不命中 → 永不进(质量通道只看订阅)
    ]

    selected = select_digest_articles(candidates, interests)

    assert [item.article_id for item in selected] == ["a03", "a01"]
    assert selected[0].lane == "interest"
    assert "来自你未订阅的「ext-b」" in selected[0].selection_reason
    assert "按新闻价值入选" in selected[0].selection_reason


def test_subscribed_interest_hits_rank_ahead_of_external_ones():
    interests = [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)]
    candidates = [
        _external(1, source="ext", score=9.8, tags=("agent",)),
        _candidate(2, source="sub", score=6.1, tags=("agent",)),
    ] + [_candidate(i, source=f"q{i}", score=7.0, tags=("other",)) for i in range(3, 6)]

    selected = select_digest_articles(candidates, interests)

    interest_items = [item for item in selected if item.lane == "interest"]
    assert [item.article_id for item in interest_items] == ["a02", "a01"]
    assert "今日订阅中的高质量内容" in interest_items[0].selection_reason


def test_external_per_source_cap_is_hard_and_never_relaxed():
    interests = [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)]
    # 一个高产的订阅外来源命中 5 篇 ≥ 6.0;订阅内只有 2 篇质量稿 → 目标 10 达不到,
    # 订阅内的每源上限会被放宽,订阅外的仍钉在 2
    candidates = [
        _external(i, source="hf-papers", score=8.0 - i * 0.1, tags=("agent",)) for i in range(1, 6)
    ] + [_candidate(i, source="sub", score=7.0, tags=("other",)) for i in range(6, 12)]

    selected = select_digest_articles(candidates, interests)

    external = [item for item in selected if item.article_id in {f"a{i:02d}" for i in range(1, 6)}]
    assert len(external) == 2
    assert all(item.lane == "interest" for item in external)
    assert not any(
        adj.startswith("source_limit_relaxed") for item in external for adj in item.coverage_adjustments
    )
    # 订阅内 sub 源放宽超过 2 条时才标 relaxed
    relaxed = [item for item in selected if any(a.startswith("source_limit_relaxed") for a in item.coverage_adjustments)]
    assert relaxed and all(item.article_id not in {f"a{i:02d}" for i in range(1, 6)} for item in relaxed)


def test_external_cap_override_via_policy():
    interests = [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)]
    candidates = [
        _external(i, source="hf", score=8.0 - i * 0.1, tags=("agent",)) for i in range(1, 6)
    ] + [_candidate(i, source=f"q{i}", score=7.0, tags=("other",)) for i in range(6, 12)]

    policy = DigestSelectionPolicy(external_per_source_max=1, external_min_quality_score=7.8)
    selected = select_digest_articles(candidates, interests, policy=policy)

    external = [item for item in selected if item.article_id.startswith("a0") and int(item.article_id[1:]) < 6]
    assert [item.article_id for item in external] == ["a01"]


def test_interest_tag_codes_drive_matching_but_mute_still_reads_the_full_set():
    interests = [
        UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW),
        UserInterestDTO(tag_code="crypto", stance=InterestStance.MUTE),
    ]
    weak_hit = _candidate(1, source="s1", score=8.0, tags=("agent",)).model_copy(
        update={"interest_tag_codes": ()}   # 指派相关度不过线:不算命中,但仍是合格的质量稿
    )
    muted_by_weak_tag = _candidate(2, source="s2", score=9.0, tags=("agent", "crypto")).model_copy(
        update={"interest_tag_codes": ("agent",)}  # 屏蔽看全集:任一指派即排除
    )
    strong_hit = _candidate(3, source="s3", score=7.0, tags=("agent",)).model_copy(
        update={"interest_tag_codes": ("agent",)}
    )

    selected = select_digest_articles([weak_hit, muted_by_weak_tag, strong_hit], interests)

    lanes = {item.article_id: item.lane for item in selected}
    assert lanes == {"a01": "quality", "a03": "interest"}


def test_interest_only_policy_lifts_the_ratio_and_keeps_the_half_size():
    from services.digest_selection import interest_only_policy

    interests = [UserInterestDTO(tag_code="agent", stance=InterestStance.FOLLOW)]
    candidates = [_external(i, source=f"e{i}", score=9.0 - i * 0.1, tags=("agent",)) for i in range(1, 9)]

    assert select_digest_articles(candidates, interests) == []  # 没有质量半可配 → 50% 上限下选不出
    only = select_digest_articles(candidates, interests, policy=interest_only_policy(DigestSelectionPolicy()))
    assert len(only) == 5
    assert all(item.lane == "interest" for item in only)


def test_policy_rejects_out_of_range_external_knobs():
    import pytest

    with pytest.raises(ValueError):
        DigestSelectionPolicy(external_min_quality_score=11)
    with pytest.raises(ValueError):
        DigestSelectionPolicy(external_per_source_max=0)
