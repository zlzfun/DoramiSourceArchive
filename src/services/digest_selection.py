"""Deterministic selection policy for a user's personal digest.

This module is deliberately free of database and LLM dependencies.  It consumes
the immutable WP-0 DTOs and returns the exact selection decisions that an edition
persists.  Subscription/permission filtering happens before this boundary; the
selector never invents candidates or relaxes the quality/mute/event boundaries.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models.analysis_contracts import (
    DigestArticleCandidateDTO,
    DigestSelectionDTO,
    InterestStance,
    PERSONAL_DIGEST_BREAKING_CORROBORATION_SLACK,
    PERSONAL_DIGEST_BREAKING_CORROBORATION_SOURCES,
    PERSONAL_DIGEST_BREAKING_MAX_ITEMS,
    PERSONAL_DIGEST_BREAKING_MAX_ITEMS_LIMIT,
    PERSONAL_DIGEST_BREAKING_MIN_SCORE,
    PERSONAL_DIGEST_EXTERNAL_MIN_QUALITY_SCORE,
    PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX,
    PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX_LIMIT,
    PERSONAL_DIGEST_INTEREST_MAX_RATIO,
    PERSONAL_DIGEST_MIN_QUALITY_SCORE,
    PERSONAL_DIGEST_TARGET_ITEMS,
    SelectionLane,
    UserInterestDTO,
)
from services.article_time import parse_article_time


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
    # v3.53「订阅 ∪ 兴趣」:订阅外候选(candidate.subscribed=False)只走兴趣通道,门槛更高、
    # 每源上限是硬的(不参与下面「不足时逐级放宽」的循环)。
    external_min_quality_score: float = PERSONAL_DIGEST_EXTERNAL_MIN_QUALITY_SCORE
    external_per_source_max: int = PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX

    def __post_init__(self) -> None:
        if self.target_items < 1:
            raise ValueError("target_items 必须至少为 1")
        if not 0.0 <= self.interest_max_ratio <= 1.0:
            raise ValueError("interest_max_ratio 必须在 0～1 之间")
        if self.per_source_max < 1:
            raise ValueError("per_source_max 必须至少为 1")
        if self.coverage_quality_delta < 0:
            raise ValueError("coverage_quality_delta 不能为负数")
        if not 0.0 <= self.external_min_quality_score <= 10.0:
            raise ValueError("external_min_quality_score 必须在 0～10 之间")
        if not 1 <= self.external_per_source_max <= PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX_LIMIT:
            raise ValueError(
                f"external_per_source_max 必须在 1～{PERSONAL_DIGEST_EXTERNAL_PER_SOURCE_MAX_LIMIT} 之间"
            )

    @property
    def interest_slots(self) -> int:
        """The interest allocation size(target × ratio,floored)."""

        return math.floor(self.target_items * self.interest_max_ratio + 1e-9)


def interest_only_policy(policy: DigestSelectionPolicy) -> DigestSelectionPolicy:
    """Policy for a reader with interests but no subscriptions(v3.53).

    There is no quality lane to fill the other half, so the edition is just the
    interest allocation(at most ``interest_slots`` items)with the 50% ceiling
    lifted——otherwise a half that has no counterpart could never be filled.
    Thresholds and the external per-source cap are unchanged.
    """

    return replace(
        policy,
        target_items=max(1, policy.interest_slots),
        interest_max_ratio=1.0,
    )


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
    parsed = parse_article_time(value)
    return parsed.timestamp() if parsed else 0.0


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


def interest_codes_of(candidate: DigestArticleCandidateDTO) -> tuple[str, ...]:
    """Codes eligible for a follow match(qualifying assignments;full set as fallback)."""

    if candidate.interest_tag_codes is not None:
        return candidate.interest_tag_codes
    return candidate.tag_codes


def eligible_for_selection(
    candidate: DigestArticleCandidateDTO,
    *,
    policy: DigestSelectionPolicy,
    muted: set[str],
    followed: Mapping[str, int],
) -> bool:
    """Hard admission: mute, score floor per subscription status, external = interest only."""

    if muted.intersection(candidate.tag_codes):
        return False
    if candidate.subscribed:
        return candidate.quality_score >= policy.min_quality_score
    if candidate.quality_score < policy.external_min_quality_score:
        return False
    return any(code in followed for code in interest_codes_of(candidate))


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
        matched = tuple(sorted(code for code in set(interest_codes_of(candidate)) if code in followed))
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
    # 订阅内命中排在订阅外之前:订阅是读者明说的信任,同样命中时先给它
    return (
        not row.candidate.subscribed,
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
                    and row.candidate.subscribed == anchor.candidate.subscribed
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
    external_source_cap: int,
    quality_first: bool = False,
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
            # 订阅外来源的上限是硬的:它不随「不足时放宽」的 source_cap 走
            cap = source_cap if candidate.subscribed else external_source_cap
            if source_counts.get(candidate.source_id, 0) >= cap:
                continue
            selected.append((row, lane))
            event_keys.add(event_key)
            source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
            limit -= 1

    if quality_first:
        # Reserve the requested interest allocation, then fill any unused slots
        # from quality.  Trying both lane orders prevents one high-ranked
        # cross-lane duplicate from hiding an otherwise legal larger set.
        take(quality_rows, SelectionLane.QUALITY.value, max(0, target - interest_limit))
        take(interest_rows, SelectionLane.INTEREST.value, interest_limit)
        take(quality_rows, SelectionLane.QUALITY.value, target - len(selected))
    else:
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
    source_name = source_display_names.get(row.candidate.source_id, row.candidate.source_id)
    if lane == SelectionLane.INTEREST.value and row.matched_codes:
        code = row.matched_codes[0]
        display_name = tag_display_names.get(code, code)
        if not row.candidate.subscribed:
            # v3.53 订阅外命中:如实交代它不在订阅内、是按更高的新闻价值门槛进来的
            return f"命中你的兴趣「{display_name}」，来自你未订阅的「{source_name}」，按新闻价值入选。"
        return f"命中你的兴趣「{display_name}」，且是今日订阅中的高质量内容。"
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

    v3.53「订阅 ∪ 兴趣」: candidates flagged ``subscribed=False`` are admitted only
    into the interest lane, must clear ``external_min_quality_score`` and share a
    hard ``external_per_source_max`` that the relaxation loop never touches;
    subscribed matches rank ahead of external ones.  The quality lane remains
    subscription-only(the caller never passes external non-matching rows,and
    ``eligible_for_selection`` drops them anyway).
    """

    policy = policy or DigestSelectionPolicy()
    muted, followed = _interest_maps(interests)
    eligible = [
        candidate for candidate in candidates
        if eligible_for_selection(candidate, policy=policy, muted=muted, followed=followed)
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

    interest_limit = min(len(interest_rows), policy.interest_slots)
    subscribed_rows = [row for row in rows if row.candidate.subscribed]
    maximum_cap = max(
        policy.per_source_max,
        max(
            (
                sum(row.candidate.source_id == source_id for row in subscribed_rows)
                for source_id in {row.candidate.source_id for row in subscribed_rows}
            ),
            default=0,
        ),
    )
    selected: list[tuple[_RankedCandidate, str]] = []
    used_cap = policy.per_source_max
    for source_cap in range(policy.per_source_max, maximum_cap + 1):
        valid_attempts: list[list[tuple[_RankedCandidate, str]]] = []
        for actual_interest_limit in range(interest_limit, -1, -1):
            for quality_first in (False, True):
                candidate_attempt = _choose_at_cap(
                    interest_rows,
                    quality_rows,
                    target=policy.target_items,
                    interest_limit=actual_interest_limit,
                    source_cap=source_cap,
                    external_source_cap=policy.external_per_source_max,
                    quality_first=quality_first,
                )
                interest_count = sum(
                    lane == SelectionLane.INTEREST.value for _row, lane in candidate_attempt
                )
                if interest_count <= math.floor(
                    len(candidate_attempt) * policy.interest_max_ratio + 1e-9
                ):
                    valid_attempts.append(candidate_attempt)
        attempt = max(
            valid_attempts,
            key=lambda value: (
                len(value),
                sum(lane == SelectionLane.INTEREST.value for _row, lane in value),
            ),
            default=[],
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
        if (
            candidate.subscribed
            and used_cap > policy.per_source_max
            and source_seen[candidate.source_id] > policy.per_source_max
        ):
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


# ── 「重大事件」通道(v3.50,issue #33 §2)──
# 与兴趣/分数两通道并列的第三条通道,但语义不同:它不看订阅范围,只回答「今天有没有
# 对所有读者都是头条的事」。准入全是机械层,不靠 LLM 自觉:
#   ① 官方一手:source_role == official 且 分数 ≥ T;
#   ② 多源印证:同一事件下 ≥ N 个不同来源 ≥ T − slack,且至少一条 ≥ T。
# 同事件按 entity.* 标签连通分量归并(生产实证:DuplicateGroup 从未写入、URL/标题键
# 分不出 4 条 X·OpenAI 推文是同一件事);无实体标签的候选回落 _event_key。每事件只
# 出一条代表:官方 > 非社交形态 > 分数 > 发布更早。同实体前几期已上过头条即抑制。

ENTITY_TAG_PREFIX = "entity."
BREAKING_SECTION = "重大事件"
BREAKING_BASIS_OFFICIAL = "official"
BREAKING_BASIS_CORROBORATED = "corroborated"


@dataclass(frozen=True)
class BreakingSelectionPolicy:
    min_score: float = PERSONAL_DIGEST_BREAKING_MIN_SCORE
    max_items: int = PERSONAL_DIGEST_BREAKING_MAX_ITEMS
    corroboration_sources: int = PERSONAL_DIGEST_BREAKING_CORROBORATION_SOURCES
    corroboration_slack: float = PERSONAL_DIGEST_BREAKING_CORROBORATION_SLACK

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_score <= 10.0:
            raise ValueError("min_score 必须在 0～10 之间")
        if not 0 <= self.max_items <= PERSONAL_DIGEST_BREAKING_MAX_ITEMS_LIMIT:
            raise ValueError(f"max_items 必须在 0～{PERSONAL_DIGEST_BREAKING_MAX_ITEMS_LIMIT} 之间")
        if self.corroboration_sources < 2:
            raise ValueError("corroboration_sources 至少为 2,单源不构成印证")
        if self.corroboration_slack < 0:
            raise ValueError("corroboration_slack 不能为负数")


@dataclass(frozen=True)
class _BreakingEvent:
    members: tuple[DigestArticleCandidateDTO, ...]
    entity_codes: frozenset[str]
    basis: str
    representative: DigestArticleCandidateDTO

    @property
    def top_score(self) -> float:
        return max(item.quality_score for item in self.members)

    @property
    def source_count(self) -> int:
        return len({item.source_id for item in self.members})


def _entity_codes(candidate: DigestArticleCandidateDTO) -> frozenset[str]:
    return frozenset(code for code in candidate.tag_codes if code.startswith(ENTITY_TAG_PREFIX))


def _group_breaking_events(
    rows: Sequence[DigestArticleCandidateDTO],
) -> list[list[DigestArticleCandidateDTO]]:
    """Union-find over shared entity codes; entity-less rows fall back to _event_key."""

    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    for candidate in rows:
        node = f"article:{candidate.article_id}"
        find(node)
        entities = _entity_codes(candidate)
        if entities:
            for code in entities:
                union(node, f"entity:{code}")
        else:
            union(node, f"event:{_event_key(candidate)}")
    groups: dict[str, list[DigestArticleCandidateDTO]] = {}
    for candidate in rows:
        groups.setdefault(find(f"article:{candidate.article_id}"), []).append(candidate)
    # Deterministic order: by the smallest article_id inside each component.
    return sorted(groups.values(), key=lambda members: min(item.article_id for item in members))


def _representative_key(candidate: DigestArticleCandidateDTO) -> tuple[object, ...]:
    return (
        candidate.source_role != "official",
        candidate.content_shape == "social",
        -candidate.quality_score,
        _published_timestamp(candidate.publish_date),
        candidate.article_id,
    )


def _breaking_reason(
    event: _BreakingEvent,
    *,
    subscribed: set[str],
    source_display_names: Mapping[str, str],
) -> str:
    representative = event.representative
    name = source_display_names.get(representative.source_id, representative.source_id)
    tail = "。" if representative.source_id in subscribed else "，不在你的订阅内也为你保留。"
    if event.basis == BREAKING_BASIS_OFFICIAL:
        return f"今日重大事件 · 「{name}」官方一手发布{tail}"
    return f"今日重大事件 · {event.source_count} 家来源同时报道，代表来源「{name}」{tail}"


def select_breaking_events(
    candidates: Iterable[DigestArticleCandidateDTO],
    interests: Iterable[UserInterestDTO] = (),
    *,
    policy: BreakingSelectionPolicy | None = None,
    previous_breaking_entities: Iterable[Iterable[str]] = (),
    excluded_article_ids: Iterable[str] = (),
    subscribed_source_ids: Iterable[str] = (),
    source_display_names: Mapping[str, str] | None = None,
) -> list[DigestSelectionDTO]:
    """Pick at most ``policy.max_items`` cross-subscription headline events.

    Candidates are expected to span every reader-visible source (the caller applies
    hidden/private-source filtering).  Mute stays a hard exclusion; already-used
    article ids are skipped; an event sharing any entity with a recent breaking
    headline of the same reader is suppressed.  Deterministic for equal input.
    """

    policy = policy or BreakingSelectionPolicy()
    if policy.max_items <= 0:
        return []
    muted, _followed = _interest_maps(interests)
    excluded = set(excluded_article_ids)
    subscribed = set(subscribed_source_ids)
    floor = policy.min_score - policy.corroboration_slack
    eligible = [
        candidate for candidate in candidates
        if candidate.article_id not in excluded
        and candidate.quality_score >= floor - 1e-9
        and not muted.intersection(candidate.tag_codes)
    ]
    if not eligible:
        return []
    previous = [frozenset(codes) for codes in previous_breaking_entities]
    events: list[_BreakingEvent] = []
    for members in _group_breaking_events(eligible):
        top = max(item.quality_score for item in members)
        official_hit = any(
            item.source_role == "official" and item.quality_score >= policy.min_score - 1e-9
            for item in members
        )
        source_count = len({item.source_id for item in members})
        corroborated = (
            source_count >= policy.corroboration_sources
            and top >= policy.min_score - 1e-9
        )
        if official_hit:
            basis = BREAKING_BASIS_OFFICIAL
        elif corroborated:
            basis = BREAKING_BASIS_CORROBORATED
        else:
            continue
        entity_codes = frozenset().union(*(_entity_codes(item) for item in members))
        if entity_codes and any(entity_codes & seen for seen in previous):
            continue
        events.append(_BreakingEvent(
            members=tuple(members),
            entity_codes=entity_codes,
            basis=basis,
            representative=min(members, key=_representative_key),
        ))
    events.sort(key=lambda event: (
        -event.top_score,
        event.basis != BREAKING_BASIS_OFFICIAL,
        event.representative.article_id,
    ))
    names = source_display_names or {}
    return [
        DigestSelectionDTO(
            article_id=event.representative.article_id,
            lane=SelectionLane.BREAKING,
            matched_interest_codes=(),
            selection_reason=_breaking_reason(
                event, subscribed=subscribed, source_display_names=names
            ),
            coverage_adjustments=(),
            breaking_basis=event.basis,
            breaking_source_count=event.source_count,
            event_entity_codes=tuple(sorted(event.entity_codes)),
        )
        for event in events[: policy.max_items]
    ]


__all__ = [
    "BREAKING_SECTION",
    "BreakingSelectionPolicy",
    "DigestSelectionPolicy",
    "GENRE_SECTIONS",
    "eligible_for_selection",
    "interest_codes_of",
    "interest_only_policy",
    "section_for_genre",
    "select_breaking_events",
    "select_digest_articles",
]
