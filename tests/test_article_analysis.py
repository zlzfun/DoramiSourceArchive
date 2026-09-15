"""WP-1 article-analysis state machine, validation, and privacy guards."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.articles_view import serialize_article_list_item  # noqa: E402
from config import LLMConfig  # noqa: E402
from llm.article_analysis_prompt import (  # noqa: E402
    ARTICLE_ANALYSIS_SYSTEM_PROMPT,
    PODCAST_ANALYSIS_PROMPT_VERSION,
    PODCAST_ANALYSIS_SCORING_VERSION,
    PODCAST_ANALYSIS_SYSTEM_PROMPT,
    PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT,
    build_article_analysis_user_prompt,
)
from models.analysis_contracts import TaxonomyTagDTO  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    SourceConfigRecord,
    TaxonomyVersionRecord,
)
from services.article_analysis import (  # noqa: E402
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
    PODCAST_PEOPLE_DIRTY_REASON,
    build_topic_heat_context,
    claim_analysis_tasks,
    compute_analysis_input_hash,
    compute_content_hash,
    get_article_analysis,
    load_relevant_active_tags,
    process_claimed_analysis,
    queue_article_analysis,
    recover_expired_leases,
    resolve_summary_with_legacy_fallback,
    run_analysis_cycle,
    sanitize_error,
    scan_analysis_backfill,
    source_allows_analysis,
    validate_analysis_payload,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 1, 1, 0, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat(timespec="seconds")
LLM_CONFIG = LLMConfig(base_url="https://llm.invalid/v1", api_key="test", model="fake")


@pytest.fixture
def storage():
    value = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        yield value
    finally:
        value.engine.dispose()


def _article(
    article_id: str,
    *,
    source_id: str = "public_source",
    fetched: dt.datetime = NOW,
    title: str | None = None,
    content: str = "Useful body about Agents.",
) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=title or f"Agents update {article_id}",
        content_type="article",
        source_id=source_id,
        source_url=f"https://private.example/{article_id}?token=secret",
        publish_date=fetched.isoformat(timespec="seconds"),
        fetched_date=fetched.isoformat(timespec="seconds"),
        has_content=True,
        content=content,
        extensions_json=json.dumps({"summary_zh": "legacy summary"}),
    )


def _source(
    source_id: str,
    *,
    private: bool = False,
    enabled: bool = True,
    credentialed: bool = False,
) -> SourceConfigRecord:
    return SourceConfigRecord(
        source_id=source_id,
        name=source_id,
        owner_username="reader" if private else "",
        ai_analysis_enabled=enabled,
        params_json=json.dumps({"credentialed_private": credentialed}),
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _tag(
    code: str = "agents",
    *,
    prompt_description: str = "",
) -> CmsTagRecord:
    return CmsTagRecord(
        code=code,
        kind="topic",
        name_zh="智能体",
        name_en="Agents",
        prompt_description=prompt_description,
        normalized_name="agents",
        status="active",
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def test_analysis_has_result_accepts_genre_or_machine_tags_but_not_manual_tags():
    article = _article("historical-projection")
    genre_only = SimpleNamespace(
        status="succeeded",
        tagging_status="succeeded",
        quality_score=None,
        content_genre="opinion",
    )
    no_fields = SimpleNamespace(
        status="succeeded",
        tagging_status="succeeded",
        quality_score=None,
        content_genre=None,
    )
    assert serialize_article_list_item(
        article, analysis=genre_only, tags=[], display_tags=[]
    )["analysis_has_result"] is True
    assert serialize_article_list_item(
        article,
        analysis=no_fields,
        tags=[],
        display_tags=[{"label": "Agent Memory", "kind": "topic", "type": "extracted"}],
    )["analysis_has_result"] is True
    assert serialize_article_list_item(
        article,
        analysis=no_fields,
        tags=[{"code": "manual", "kind": "topic", "assignment_source": "manual"}],
    )["analysis_has_result"] is False


def test_relevant_tag_prompt_boundary_survives_the_dto_contract(storage):
    with Session(storage.engine) as session:
        article = _article("prompt-boundary")
        session.add_all(
            [
                article,
                _tag(
                    prompt_description=(
                        "Only use when the article is primarily about autonomous agents."
                    )
                ),
            ]
        )
        session.commit()

        tags = load_relevant_active_tags(session, article)
        assert len(tags) == 1
        assert tags[0].prompt_description == (
            "Only use when the article is primarily about autonomous agents."
        )
        prompt = build_article_analysis_user_prompt(
            title=article.title,
            body=article.content or "",
            content_type=article.content_type,
            source_id=article.source_id,
            taxonomy_tags=[tags[0].model_dump()],
        )
        assert "Only use when the article is primarily about autonomous agents." in prompt


def test_relevant_tag_recall_ignores_generic_ai_overlap_and_honors_aliases(storage):
    with Session(storage.engine) as session:
        generic = _article(
            "generic-ai",
            title="AI improves cyclone forecasting",
            content="A weather model predicts cyclone paths more accurately.",
        )
        alias_match = _article(
            "alias-match",
            title="AI智能体调用工具完成任务",
            content="系统进行多步规划。",
        )
        tag = _tag(prompt_description="Only use for autonomous agents.")
        session.add_all([generic, alias_match, tag])
        session.flush()
        session.add(
            CmsTagAliasRecord(
                tag_id=tag.id,
                kind="topic",
                alias="AI智能体",
                normalized_alias="ai智能体",
                alias_type="synonym",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()

        assert load_relevant_active_tags(session, generic) == []
        recalled = load_relevant_active_tags(session, alias_match)
        assert [item.code for item in recalled] == ["agents"]


def test_relevant_tag_recall_uses_prompt_description_for_astra_safety_case(storage):
    with Session(storage.engine) as session:
        article = _article(
            "astra-safety",
            title="Astra 达到关键网络安全能力门槛并限制功能开放",
            content=(
                "模型在安全评估过程中自主利用零日漏洞。OpenAI 随后增加安全护栏，"
                "监控未经授权的行为，并限制高风险能力发布。"
            ),
        )
        safety = CmsTagRecord(
            code="topic.ai-safety",
            kind="topic",
            name_zh="AI 对齐与安全",
            name_en="AI Safety",
            normalized_name="ai 对齐与安全",
            prompt_description=(
                "仅当文章核心讨论 AI 对齐、模型行为安全、能力风险或 AI 安全评估时使用；"
                "普通网络攻击、账号安全或隐私事件不使用。"
            ),
            status="active",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
        generic_product = CmsTagRecord(
            code="entity.generic-product",
            kind="entity",
            name_zh="无关产品",
            name_en="Unrelated Product",
            normalized_name="unrelated product",
            prompt_description="仅当该产品、模型、功能或使用体验是文章核心时使用。",
            status="active",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
        generic_industry = CmsTagRecord(
            code="industry.generic",
            kind="industry",
            name_zh="无关行业",
            name_en="Unrelated Industry",
            normalized_name="unrelated industry",
            prompt_description="仅当文章核心涉及该产业、企业、制造与供应链时使用。",
            status="active",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        )
        session.add_all([article, safety, generic_product, generic_industry])
        session.commit()

        recalled = load_relevant_active_tags(session, article)
        assert [item.code for item in recalled] == ["topic.ai-safety"]


def test_prompt_and_validator_rank_tags_by_relevance_and_align_primary():
    assert "tag_assignments 必须按 relevance 从高到低排列" in ARTICLE_ANALYSIS_SYSTEM_PROMPT
    assert "词序变化，不得再输出为 tag_candidates" in ARTICLE_ANALYSIS_SYSTEM_PROMPT
    assert "对通用规则【不看时效】的播客内容类型例外并覆盖它" in PODCAST_ANALYSIS_SYSTEM_PROMPT
    assert "必须使用 topic_heat" in PODCAST_ANALYSIS_SYSTEM_PROMPT
    assert "人物名气与公共影响力本身是明确的正向信号" in PODCAST_ANALYSIS_SYSTEM_PROMPT
    assert "当前输入是播客单集的标题与节目简介" in PODCAST_ANALYSIS_SYSTEM_PROMPT
    assert "当前输入是播客单集的标题与节目简介" not in (
        PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT
    )
    assert "从零生成唯一最终分数" in PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT
    assert "不推测、继承或" in PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT
    assert "参考任何简介初评分数" in PODCAST_TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT
    active_tags = [
        TaxonomyTagDTO(
            id=1,
            code="entity.openai",
            kind="entity",
            name_zh="OpenAI",
            name_en="OpenAI",
        ),
        TaxonomyTagDTO(
            id=2,
            code="topic.ai-safety",
            kind="topic",
            name_zh="AI 对齐与安全",
            name_en="AI Safety",
        ),
        TaxonomyTagDTO(
            id=3,
            code="topic.ai-agents",
            kind="topic",
            name_zh="AI 智能体",
            name_en="AI Agents",
        ),
    ]
    payload = _payload()
    payload.update(
        {
            "primary_tag_code": "entity.openai",
            "tag_assignments": [
                {"code": "topic.ai-agents", "kind": "topic", "relevance": 0.72},
                {"code": "entity.openai", "kind": "entity", "relevance": 0.96},
                {"code": "topic.ai-safety", "kind": "topic", "relevance": 0.96},
            ],
            "tag_candidates": [
                {
                    "label": "Lower confidence",
                    "proposed_kind": "topic",
                    "confidence": 0.61,
                    "evidence": "secondary",
                },
                {
                    "label": "Higher confidence",
                    "proposed_kind": "industry",
                    "confidence": 0.91,
                    "evidence": "core",
                },
            ],
        }
    )

    validated = validate_analysis_payload(payload, active_tags=active_tags)

    assert [item.code for item in validated.result.tag_assignments] == [
        "topic.ai-safety",
        "entity.openai",
        "topic.ai-agents",
    ]
    assert [item.is_primary for item in validated.result.tag_assignments] == [True, False, False]
    assert validated.result.primary_tag_code == "topic.ai-safety"
    assert [item.label for item in validated.result.tag_candidates] == [
        "Higher confidence",
        "Lower confidence",
    ]


def _payload(*, candidate: bool = False) -> dict:
    return {
        "quality_score": 8.6,
        "score_reason": "原创信息充分，并有明确实践价值。",
        "summary": "文章解释了能力边界、实现方式与实际影响。",
        "content_genre": "product_update",
        "primary_tag_code": "agents",
        "tag_assignments": [{"code": "agents", "kind": "topic", "relevance": 0.94}],
        "tag_candidates": (
            [{"label": "Agent Memory", "proposed_kind": "topic", "confidence": 0.93, "evidence": "核心能力"}]
            if candidate
            else []
        ),
        "content_features": ["official_release"],
        "entities": [{"name": "Dorami", "type": "product", "relevance": 0.8}],
    }


def _seed_and_claim(storage, article: ArticleRecord, *, worker: str = "worker-1"):
    with Session(storage.engine) as session:
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "created"
        session.commit()
        tasks = claim_analysis_tasks(session, worker_id=worker, now=NOW)
        assert len(tasks) == 1
        return tasks[0]


def test_queue_is_idempotent_and_content_change_invalidates_authority(storage):
    article = _article("a1")
    with Session(storage.engine) as session:
        session.add_all([article, _tag()])
        session.commit()

        assert queue_article_analysis(session, article.id, now=NOW) == "created"
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert record.status == "pending"
        assert record.prompt_version == ARTICLE_ANALYSIS_PROMPT_VERSION
        assert record.scoring_version == ARTICLE_ANALYSIS_SCORING_VERSION
        first_hash = record.content_hash
        assert queue_article_analysis(session, article.id, now=NOW) == "unchanged"

        record.status = "succeeded"
        record.quality_score = 8.0
        record.summary = "old"
        record.attempt_count = 3
        article.content = "Changed complete body"
        session.add_all([record, article])
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "invalidated"
        session.commit()
        session.refresh(record)
        assert record.status == "pending"
        assert record.quality_score is None
        assert record.summary == ""
        assert record.attempt_count == 0
        assert record.content_hash != first_hash


def test_source_ai_switch_skips_new_work_but_preserves_success(storage):
    article = _article("private-off", source_id="user_rss_private")
    with Session(storage.engine) as session:
        session.add_all([article, _source("user_rss_private", private=True, enabled=False)])
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "skipped"
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert record.status == "skipped"
        assert record.last_error == "source_ai_analysis_disabled"

        record.status = "succeeded"
        record.quality_score = 8.2
        record.summary = "existing asset"
        session.add(record)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "unchanged"
        assert session.get(ArticleAnalysisRecord, article.id).summary == "existing asset"


def test_force_queue_preserves_current_asset_and_never_interrupts_running_lease(storage):
    article = _article("forced")
    with Session(storage.engine) as session:
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "created"
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        record.status = "succeeded"
        record.quality_score = 8.2
        record.summary = "current asset"
        session.add(record)
        session.commit()

        assert queue_article_analysis(session, article.id, force=True, now=NOW) == "invalidated"
        session.commit()
        assert record.status == "pending"
        assert record.quality_score == 8.2
        assert record.summary == "current asset"
        assert get_article_analysis(session, article.id)["summary"] == "current asset"

        record.status = "running"
        record.lease_owner = "another-worker"
        record.lease_expires_at = (NOW + dt.timedelta(minutes=5)).isoformat()
        session.add(record)
        session.commit()
        assert queue_article_analysis(session, article.id, force=True, now=NOW) == "busy"
        session.refresh(record)
        assert record.status == "running"
        assert record.lease_owner == "another-worker"


def test_version_refresh_preserves_old_asset_across_compensation_scans(storage):
    article = _article("version-refresh")
    with Session(storage.engine) as session:
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "created"
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        record.status = "succeeded"
        record.quality_score = 7.8
        record.summary = "old result remains readable"
        record.analyzed_at = NOW_ISO
        record.prompt_version = "article-analysis-v0"
        session.add(record)
        session.commit()

        assert queue_article_analysis(session, article.id, now=NOW) == "invalidated"
        session.commit()
        session.refresh(record)
        assert record.status == "pending"
        assert record.quality_score == 7.8
        assert record.summary == "old result remains readable"
        assert queue_article_analysis(session, article.id, now=NOW) == "unchanged"
        session.refresh(record)
        assert record.quality_score == 7.8


def test_backfill_is_seven_days_only_and_claims_newest_first(storage):
    with Session(storage.engine) as session:
        session.add_all(
            [
                _article("new", fetched=NOW),
                _article("recent", fetched=NOW - dt.timedelta(days=6)),
                _article("old", fetched=NOW - dt.timedelta(days=8)),
            ]
        )
        session.commit()
        first = scan_analysis_backfill(session, now=NOW, limit=1)
        assert first.scanned == 1
        assert first.created == 1
        # The action limit must not pin the scanner forever on the already
        # current newest row; the next cycle progresses into older backfill.
        # v3.48: the scan only visits rows that need action (the pending "new"
        # row is no longer loaded or re-hashed), so ``scanned`` counts 1 here.
        second = scan_analysis_backfill(session, now=NOW, limit=1)
        assert second.scanned == 1
        assert second.created == 1
        assert session.get(ArticleAnalysisRecord, "old") is None
        tasks = claim_analysis_tasks(session, worker_id="w", limit=2, now=NOW)
        assert [task.article_id for task in tasks] == ["new", "recent"]


def test_success_persists_base_tags_attempt_and_candidate_evidence(storage):
    with Session(storage.engine) as session:
        session.add(_tag())
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW_ISO))
        session.commit()
    task = _seed_and_claim(storage, _article("success"))

    async def fake_analyzer(_article_input, tags, _config):
        assert [tag.code for tag in tags] == ["agents"]
        return _payload(candidate=True)

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=fake_analyzer,
            candidate_enabled=True,
            now_fn=lambda: NOW + dt.timedelta(seconds=2),
        )
    )
    assert (result.status, result.tagging_status) == ("succeeded", "succeeded")
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "success")
        assert record.quality_score == 8.6
        assert record.content_genre == "product_update"
        assert record.taxonomy_version == 1
        assert record.lease_owner is None
        assignment = session.exec(select(ArticleTagAssignmentRecord)).one()
        assert assignment.is_primary is True
        assert record.primary_tag_id == assignment.tag_id
        attempt = session.exec(select(ArticleAnalysisAttemptRecord)).one()
        assert attempt.status == "succeeded"
        assert "summary" not in attempt.result_summary_json
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        evidence = session.exec(select(CmsTagCandidateEvidenceRecord)).one()
        assert candidate.support_article_count_7d == 1
        assert evidence.article_id == "success"
        assert get_article_analysis(session, "success")["summary"] == record.summary


def test_podcast_initial_assessment_persists_actual_basis_input_and_diagnostics(storage):
    article = _article("podcast-initial", title="Agents with Ada")
    article.content_type = "podcast_episode"
    article.extensions_json = json.dumps(
        {
            "persons": [
                {
                    "name": "Ada",
                    "role": "guest",
                    "scope": "episode",
                    "evidence": "podcast:person",
                }
            ]
        }
    )
    with Session(storage.engine) as session:
        session.add_all([article, _tag()])
        session.commit()
    task = _seed_and_claim(storage, article)
    captured = {}

    async def analyzer(article_input, tags, _config):
        captured["input"] = article_input
        captured["hash"] = compute_analysis_input_hash(article_input, tags)
        return {
            **_payload(),
            "podcast_factors": {
                key: {"level": "high", "evidence": "简介中的明确证据"}
                for key in (
                    "guest_authority",
                    "topic_timeliness",
                    "novelty",
                    "evidence_depth",
                    "viewpoint_diversity",
                    "practical_value",
                )
            },
        }

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=analyzer,
            now_fn=lambda: NOW + dt.timedelta(seconds=2),
        )
    )
    assert result.status == "succeeded"
    assert captured["input"].analysis_basis == "podcast_show_notes"
    assert captured["input"].people[0]["name"] == "Ada"
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "podcast-initial")
        assert record.prompt_version == PODCAST_ANALYSIS_PROMPT_VERSION
        assert record.scoring_version == PODCAST_ANALYSIS_SCORING_VERSION
        assert record.analysis_basis == "podcast_show_notes"
        assert record.analysis_input_hash == captured["hash"]
        assert record.analysis_input_hash != record.content_hash
        diagnostics = json.loads(record.analysis_diagnostics_json)
        assert diagnostics["people"][0]["role"] == "guest"
        assert diagnostics["topic_heat"]["window_days"] == 7
        assert diagnostics["podcast_factors"]["novelty"]["level"] == "high"

        episode = session.get(ArticleRecord, "podcast-initial")
        extensions = json.loads(episode.extensions_json)
        extensions["persons"].append(
            {
                "name": "Grace",
                "role": "host",
                "scope": "episode",
                "evidence": "podcast:person",
            }
        )
        episode.extensions_json = json.dumps(extensions)
        session.add(episode)
        session.commit()
        assert queue_article_analysis(session, episode.id, now=NOW) == "invalidated"
        session.commit()
        session.refresh(record)
        assert record.status == "pending"
        assert record.quality_score == 8.6  # old authority stays readable during refresh


def test_podcast_topic_heat_counts_distinct_sources_and_daily_brief(storage):
    with Session(storage.engine) as session:
        tag = _tag()
        target = _article("heat-target", fetched=NOW, title="Agents roundtable")
        target.content_type = "podcast_episode"
        evidence = [
            _article("heat-a", source_id="feed-a", fetched=NOW - dt.timedelta(days=1)),
            _article("heat-a-repeat", source_id="feed-a", fetched=NOW - dt.timedelta(days=2)),
            _article("heat-b", source_id="feed-b", fetched=NOW - dt.timedelta(days=3)),
            _article("heat-old", source_id="feed-c", fetched=NOW - dt.timedelta(days=8)),
        ]
        brief = _article("brief", source_id="dorami_daily_brief", fetched=NOW)
        brief.extensions_json = json.dumps({"included_article_ids": ["heat-b"]})
        session.add_all([tag, target, brief, *evidence])
        session.flush()
        for index, item in enumerate(evidence):
            session.add(
                ArticleTagAssignmentRecord(
                    article_id=item.id,
                    tag_id=tag.id,
                    tag_kind="topic",
                    relevance=0.9,
                    assignment_source="llm",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
            session.add(
                ArticleAnalysisRecord(
                    article_id=item.id,
                    status="succeeded",
                    quality_score=6.0 + index,
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
        # The derived public brief is an inclusion flag, never a third source
        # or a score signal of its own.
        session.add(ArticleTagAssignmentRecord(
            article_id=brief.id,
            tag_id=tag.id,
            tag_kind="topic",
            relevance=1.0,
            assignment_source="llm",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.add(ArticleAnalysisRecord(
            article_id=brief.id,
            status="succeeded",
            quality_score=10.0,
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.commit()
        tags = load_relevant_active_tags(session, target)
        heat = build_topic_heat_context(
            session, article_id=target.id, active_tags=tags, now=NOW
        )

    assert heat["window_days"] == 7
    assert heat["snapshot_at"].startswith("2026-09-01T01:00:00")
    assert heat["signals"] == [
        {
            "code": "agents",
            "kind": "topic",
            "name": "智能体",
            "distinct_source_count": 2,
            "max_quality_score": 8.0,
            "latest_seen_at": (NOW - dt.timedelta(days=1)).isoformat(
                timespec="microseconds"
            ),
            "in_public_daily_brief": True,
        }
    ]


def test_user_rss_can_analyze_and_contribute_candidate_when_enabled(storage):
    with Session(storage.engine) as session:
        session.add_all([_tag(), _source("user_rss_private", private=True)])
        article = _article("private", source_id="user_rss_private")
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "created"
        session.commit()
        [task] = claim_analysis_tasks(session, worker_id="custom-rss", now=NOW)

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(candidate=True),
            candidate_enabled=True,
            now_fn=lambda: NOW,
        )
    )
    assert result.status == "succeeded"
    with Session(storage.engine) as session:
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        evidence = session.exec(select(CmsTagCandidateEvidenceRecord)).one()
        assert candidate.label == "Agent Memory"
        assert evidence.source_id == "user_rss_private"
        source = session.get(SourceConfigRecord, "user_rss_private")
        source.params_json = json.dumps({"credentialed_private": True})
        source.ai_analysis_enabled = False
        session.add(source)
        session.commit()
        assert queue_article_analysis(session, "private", now=NOW) == "unchanged"
        session.commit()
        session.refresh(candidate)
        assert session.exec(select(CmsTagCandidateEvidenceRecord)).all() == []
        assert candidate.support_article_count_7d == 0


def test_credentialed_user_rss_is_never_queued_for_analysis(storage):
    with Session(storage.engine) as session:
        source = _source(
            "user_rss_credentialed",
            private=True,
            enabled=True,
            credentialed=True,
        )
        article = _article("credentialed", source_id=source.source_id)
        session.add_all([source, article])
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "skipped"
        session.commit()
        record = session.get(ArticleAnalysisRecord, article.id)
        assert record.status == "skipped"
        assert record.last_error == "source_ai_analysis_disabled"
        assert claim_analysis_tasks(session, worker_id="credentialed", now=NOW) == []
        assert session.exec(select(CmsTagCandidateRecord)).all() == []
        assert session.exec(select(CmsTagCandidateEvidenceRecord)).all() == []


def test_user_rss_policy_fails_closed_without_config_and_accepts_truthy_flag(storage):
    with Session(storage.engine) as session:
        orphan = _article("orphan", source_id="user_rss_orphan")
        flagged = _source("user_rss_flagged", private=True)
        flagged.params_json = json.dumps({"credentialed_private": "true"})
        session.add_all([orphan, flagged])
        session.commit()
        assert source_allows_analysis(session, orphan.source_id) is False
        assert queue_article_analysis(session, orphan.id, now=NOW) == "skipped"
        assert source_allows_analysis(session, flagged.source_id) is False


def test_policy_flip_during_llm_discards_result_and_candidates(storage):
    source_id = "user_rss_flip"
    source = _source(source_id, private=True)
    task = None
    with Session(storage.engine) as session:
        session.add_all([_tag(), source, _article("flip", source_id=source.source_id)])
        session.commit()
        assert queue_article_analysis(session, "flip", now=NOW) == "created"
        session.commit()
        task = claim_analysis_tasks(session, worker_id="flip-worker", now=NOW)[0]

    def analyzer(*_args):
        with Session(storage.engine) as session:
            current = session.get(SourceConfigRecord, source_id)
            current.params_json = json.dumps({"credentialed_private": True})
            current.ai_analysis_enabled = False
            session.add(current)
            session.commit()
        return _payload(candidate=True)

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=analyzer,
            candidate_enabled=True,
            now_fn=lambda: NOW + dt.timedelta(seconds=1),
        )
    )
    assert result.status == "skipped"
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "flip")
        assert record.quality_score is None
        assert session.exec(select(CmsTagCandidateRecord)).all() == []
        assert session.exec(select(CmsTagCandidateEvidenceRecord)).all() == []


def test_malformed_tags_are_partial_but_malformed_base_retries(storage):
    with Session(storage.engine) as session:
        session.add(_tag())
        session.commit()
    task = _seed_and_claim(storage, _article("partial"))
    partial = _payload()
    partial["tag_assignments"].append(
        {"code": "hallucinated", "kind": "topic", "relevance": 0.99}
    )
    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: partial,
            now_fn=lambda: NOW,
        )
    )
    assert (result.status, result.tagging_status) == ("succeeded", "partial")

    bad_task = _seed_and_claim(storage, _article("bad-base"), worker="worker-2")
    bad = _payload()
    bad["quality_score"] = 11
    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            bad_task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: bad,
            now_fn=lambda: NOW,
        )
    )
    assert result.status == "failed"
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "bad-base")
        assert record.next_attempt_at is not None
        assert record.quality_score is None


def test_timeout_and_restart_lease_recovery_schedule_bounded_retry(storage):
    timeout_task = _seed_and_claim(storage, _article("timeout"))

    async def slow(*_args):
        await asyncio.sleep(0.05)
        return _payload()

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            timeout_task,
            llm_config=LLM_CONFIG,
            analyzer=slow,
            timeout_seconds=0.001,
            now_fn=lambda: NOW + dt.timedelta(seconds=1),
        )
    )
    assert result.status == "timeout"
    with Session(storage.engine) as session:
        timeout_record = session.get(ArticleAnalysisRecord, "timeout")
        assert timeout_record.status == "timeout"
        assert timeout_record.next_attempt_at is not None

    abandoned = _seed_and_claim(storage, _article("abandoned"), worker="dead-worker")
    with Session(storage.engine) as session:
        assert recover_expired_leases(session, now=NOW + dt.timedelta(minutes=6)) == 1
        record = session.get(ArticleAnalysisRecord, abandoned.article_id)
        assert record.status == "timeout"
        attempt = session.exec(
            select(ArticleAnalysisAttemptRecord).where(
                ArticleAnalysisAttemptRecord.article_id == abandoned.article_id
            )
        ).one()
        assert attempt.status == "timeout"


def test_same_worker_id_cannot_commit_with_an_expired_lease_token(storage):
    first = _seed_and_claim(storage, _article("lease-aba"), worker="runtime-all")
    with Session(storage.engine) as session:
        assert recover_expired_leases(
            session, now=NOW + dt.timedelta(minutes=6)
        ) == 1
        [second] = claim_analysis_tasks(
            session,
            worker_id="runtime-all",
            now=NOW + dt.timedelta(minutes=8),
        )
    assert second.lease_token != first.lease_token

    stale = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            first,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(),
            now_fn=lambda: NOW + dt.timedelta(minutes=8),
        )
    )
    assert stale.status == "superseded"
    current = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            second,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(),
            now_fn=lambda: NOW + dt.timedelta(minutes=8, seconds=1),
        )
    )
    assert current.status == "succeeded"
    with Session(storage.engine) as session:
        attempts = session.exec(
            select(ArticleAnalysisAttemptRecord)
            .where(ArticleAnalysisAttemptRecord.article_id == "lease-aba")
            .order_by(ArticleAnalysisAttemptRecord.attempt_no)
        ).all()
        assert [(row.attempt_no, row.status) for row in attempts] == [
            (1, "timeout"),
            (2, "succeeded"),
        ]


def test_retry_waits_for_backoff_and_uses_a_new_attempt_number(storage):
    with Session(storage.engine) as session:
        session.add(_tag())
        session.commit()
    task = _seed_and_claim(storage, _article("retry"))
    failed = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: {"not": "the schema"},
            now_fn=lambda: NOW,
        )
    )
    assert failed.status == "failed"
    with Session(storage.engine) as session:
        assert claim_analysis_tasks(
            session, worker_id="retry-worker", now=NOW + dt.timedelta(seconds=59)
        ) == []
        retry_tasks = claim_analysis_tasks(
            session, worker_id="retry-worker", now=NOW + dt.timedelta(seconds=61)
        )
        assert [item.attempt_no for item in retry_tasks] == [2]

    succeeded = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            retry_tasks[0],
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(),
            now_fn=lambda: NOW + dt.timedelta(seconds=62),
        )
    )
    assert succeeded.status == "succeeded"
    with Session(storage.engine) as session:
        attempts = session.exec(
            select(ArticleAnalysisAttemptRecord)
            .where(ArticleAnalysisAttemptRecord.article_id == "retry")
            .order_by(ArticleAnalysisAttemptRecord.attempt_no)
        ).all()
        assert [(item.attempt_no, item.status) for item in attempts] == [
            (1, "failed"),
            (2, "succeeded"),
        ]


def test_candidate_evidence_remains_idempotent_after_reanalysis(storage):
    with Session(storage.engine) as session:
        session.add(_tag())
        session.commit()
    task = _seed_and_claim(storage, _article("candidate-retry"))
    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(candidate=True),
            candidate_enabled=True,
            now_fn=lambda: NOW,
        )
    )
    assert result.status == "succeeded"

    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, "candidate-retry")
        article.content += " Corrected paragraph."
        session.add(article)
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW + dt.timedelta(minutes=1)) == "invalidated"
        session.commit()
        second_task = claim_analysis_tasks(
            session, worker_id="candidate-worker", now=NOW + dt.timedelta(minutes=1)
        )[0]
    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            second_task,
            llm_config=LLM_CONFIG,
            analyzer=lambda *_args: _payload(candidate=True),
            candidate_enabled=True,
            now_fn=lambda: NOW + dt.timedelta(minutes=1, seconds=1),
        )
    )
    assert result.status == "succeeded"
    with Session(storage.engine) as session:
        evidence = session.exec(select(CmsTagCandidateEvidenceRecord)).all()
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        assert len(evidence) == 1
        assert candidate.support_article_count_7d == 1


def test_content_change_during_llm_call_discards_stale_result(storage):
    task = _seed_and_claim(storage, _article("race"))

    async def edits_while_running(*_args):
        with Session(storage.engine) as session:
            article = session.get(ArticleRecord, "race")
            article.content = "new body while old analysis is running"
            session.add(article)
            session.commit()
            assert queue_article_analysis(session, article.id, now=NOW) == "invalidated"
            session.commit()
        return _payload()

    result = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=edits_while_running,
            now_fn=lambda: NOW,
        )
    )
    assert result.status == "superseded"
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "race")
        assert record.status == "pending"
        assert record.quality_score is None
        attempt = session.exec(
            select(ArticleAnalysisAttemptRecord).where(
                ArticleAnalysisAttemptRecord.article_id == "race"
            )
        ).one()
        assert attempt.status == "skipped"


def test_podcast_people_change_during_llm_call_supersedes_and_requeues(storage):
    article_id = "podcast-people-race"
    article = _article(article_id)
    article.content_type = "podcast_episode"
    article.extensions_json = json.dumps(
        {
            "persons": [
                {
                    "name": "Old Guest",
                    "role": "guest",
                    "scope": "episode",
                    "evidence": "podcast:person",
                }
            ]
        }
    )
    task = _seed_and_claim(storage, article)

    async def refreshes_people_while_running(article_input, *_args):
        assert article_input.people[0]["name"] == "Old Guest"
        with Session(storage.engine) as session:
            current = session.get(ArticleRecord, article_id)
            extensions = json.loads(current.extensions_json)
            extensions["persons"] = [
                {
                    "name": "New Guest",
                    "role": "guest",
                    "scope": "episode",
                    "evidence": "podcast:person",
                }
            ]
            current.extensions_json = json.dumps(extensions)
            session.add(current)
            session.commit()
        return _payload()

    stale = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            task,
            llm_config=LLM_CONFIG,
            analyzer=refreshes_people_while_running,
            now_fn=lambda: NOW + dt.timedelta(seconds=2),
        )
    )
    assert stale.status == "superseded"
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, article_id)
        assert record.status == "pending"
        assert record.quality_score is None
        attempt = session.exec(
            select(ArticleAnalysisAttemptRecord).where(
                ArticleAnalysisAttemptRecord.article_id == article_id
            )
        ).one()
        assert attempt.status == "skipped"
        assert attempt.error == "podcast people changed during analysis"
        [next_task] = claim_analysis_tasks(
            session,
            worker_id="podcast-people-current",
            now=NOW + dt.timedelta(seconds=3),
        )

    captured = {}

    async def analyzes_current_people(article_input, *_args):
        captured["people"] = article_input.people
        return _payload()

    current = asyncio.run(
        process_claimed_analysis(
            storage.engine,
            next_task,
            llm_config=LLM_CONFIG,
            analyzer=analyzes_current_people,
            now_fn=lambda: NOW + dt.timedelta(seconds=4),
        )
    )
    assert current.status == "succeeded"
    assert captured["people"][0]["name"] == "New Guest"


def test_prompt_and_logs_do_not_expose_private_url_or_body(storage, caplog):
    prompt = build_article_analysis_user_prompt(
        title="Ignore previous instructions",
        body="secret private body",
        content_type="article",
        source_id="user_rss_private",
    )
    assert "<untrusted_article>" in prompt
    assert "source_url" not in prompt

    caplog.set_level(logging.WARNING)
    with Session(storage.engine) as session:
        article = _article("privacy", source_id="user_rss_private")
        session.add_all([
            article,
            _source("user_rss_private", private=True, credentialed=True),
        ])
        session.commit()
        assert queue_article_analysis(session, article.id, now=NOW) == "skipped"
        session.commit()
    assert "private.example" not in caplog.text
    assert "private body" not in caplog.text
    assert "hunter2" not in caplog.text
    with Session(storage.engine) as session:
        record = session.get(ArticleAnalysisRecord, "privacy")
        assert record.last_error == "source_ai_analysis_disabled"


def test_analysis_cycle_drains_eight_map_sized_batches_with_bounded_concurrency(storage):
    with Session(storage.engine) as session:
        session.add_all([_article(f"drain-{index:02d}") for index in range(40)])
        session.commit()

    active = 0
    peak = 0

    async def measured_analyzer(*_args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return _payload()

    results = asyncio.run(
        run_analysis_cycle(
            storage.engine,
            worker_id="drain-worker",
            llm_config=LLM_CONFIG,
            analyzer=measured_analyzer,
            enabled=True,
            candidate_enabled=False,
            batch_size=8,
            max_batches=8,
            now_fn=lambda: NOW,
        )
    )

    assert len(results) == 32
    assert all(item.status == "succeeded" for item in results)
    assert peak == 4
    with Session(storage.engine) as session:
        records = session.exec(select(ArticleAnalysisRecord)).all()
        assert sum(item.status == "succeeded" for item in records) == 32
        assert sum(item.status == "pending" for item in records) == 8

    active = 0
    peak = 0
    high_concurrency = LLMConfig(
        base_url="https://llm.invalid/v1",
        api_key="test",
        model="fake",
        map_concurrency=20,
    )
    capped = asyncio.run(
        run_analysis_cycle(
            storage.engine,
            worker_id="capped-worker",
            llm_config=high_concurrency,
            analyzer=measured_analyzer,
            enabled=True,
            candidate_enabled=False,
            batch_size=8,
            max_batches=1,
            now_fn=lambda: NOW,
        )
    )
    assert len(capped) == 4
    assert peak == 4
    assert asyncio.run(
        run_analysis_cycle(
            storage.engine,
            worker_id="zero-worker",
            llm_config=high_concurrency,
            analyzer=measured_analyzer,
            enabled=True,
            candidate_enabled=False,
            max_batches=0,
            now_fn=lambda: NOW,
        )
    ) == []


def test_validation_limits_score_genre_and_active_tag_codes():
    # 闭集外体裁降级为 other 并记 warning,分数与摘要照常保留(issue #33 F6);
    # 分数缺失/非法仍是 base-field 错误
    validated = validate_analysis_payload({**_payload(), "content_genre": "made_up"}, active_tags=[])
    assert str(validated.result.content_genre) == "other"
    assert "unknown_genre_fallback" in validated.warnings
    assert validated.result.quality_score == float(_payload()["quality_score"])
    with pytest.raises(ValueError):
        validate_analysis_payload({**_payload(), "quality_score": "n/a"}, active_tags=[])
    assert "hunter2" not in sanitize_error("password=hunter2")


def test_prompt_and_validator_keep_score_reason_a_footnote():
    """issue #13:score_reason 是分数注脚——提示词要求 ≤40 字且先于分数输出、
    不复述内容;解析层不再给它 1200 字空间,超写即截断;one_sentence_summary 已取缔。"""
    from llm.article_analysis_prompt import ARTICLE_ANALYSIS_SYSTEM_PROMPT

    assert "one_sentence_summary" not in ARTICLE_ANALYSIS_SYSTEM_PROMPT
    assert "不复述文章内容" in ARTICLE_ANALYSIS_SYSTEM_PROMPT
    assert ARTICLE_ANALYSIS_SYSTEM_PROMPT.index("score_reason、quality_score") > 0

    long_reason = "理由" * 400
    validated = validate_analysis_payload({**_payload(), "score_reason": long_reason}, active_tags=[])
    assert 0 < len(validated.result.score_reason) < len(long_reason)
    assert len(validated.result.score_reason) <= 120
    with pytest.raises(ValueError):
        validate_analysis_payload({**_payload(), "score_reason": ""}, active_tags=[])


def test_reader_summary_prefers_unified_asset_then_legacy(storage):
    article = _article("summary")
    with Session(storage.engine) as session:
        session.add(article)
        session.commit()
        assert resolve_summary_with_legacy_fallback(session, article) == "legacy summary"
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=8.0,
                score_reason="reason",
                summary="unified summary",
                content_genre="opinion",
                content_hash=compute_content_hash(article),
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()
        assert resolve_summary_with_legacy_fallback(session, article) == "unified summary"


def test_scan_only_visits_rows_that_need_action(storage):
    """v3.48 收口:扫描不再每分钟载入 7 天正文——pending/failed/当前 succeeded 行不进扫描,
    无分析行与 skipped 行才会被访问。"""
    with Session(storage.engine) as session:
        session.add_all([
            _article("fresh"), _article("pending"), _article("failed"), _article("done"), _article("skipped"),
        ])
        session.commit()
        for article_id, status in (("pending", "pending"), ("failed", "failed"), ("done", "succeeded"), ("skipped", "skipped")):
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status=status, tagging_status="pending",
                content_hash=compute_content_hash(session.get(ArticleRecord, article_id)),
                prompt_version=ARTICLE_ANALYSIS_PROMPT_VERSION,
                scoring_version=ARTICLE_ANALYSIS_SCORING_VERSION,
                created_at=NOW_ISO, updated_at=NOW_ISO,
            ))
        session.commit()
        stats = scan_analysis_backfill(session, now=NOW)
        assert stats.scanned == 2  # fresh + skipped
        assert stats.created == 1
        assert stats.invalidated == 1  # skipped 行:源开关已开(无配置行=允许),重新入队
        assert session.get(ArticleAnalysisRecord, "fresh").status == "pending"
        assert session.get(ArticleAnalysisRecord, "skipped").status == "pending"
        assert session.get(ArticleAnalysisRecord, "failed").status == "failed"


def test_scan_throttles_version_refresh_but_never_new_articles(storage):
    """版本键过期的 succeeded 行每 tick 最多失效 version_refresh_limit 篇(慢滴重跑),
    新文章不占该预算、永远优先。"""
    with Session(storage.engine) as session:
        session.add(_article("brand-new"))
        for i in range(3):
            article = _article(f"stale-{i}", fetched=NOW - dt.timedelta(hours=i + 1))
            session.add(article)
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=article.id, status="succeeded", tagging_status="succeeded",
                quality_score=7.0, summary="old ruler", analyzed_at=NOW_ISO,
                content_hash=compute_content_hash(article),
                prompt_version="article-analysis-v0", scoring_version="reading-quality-v0",
                created_at=NOW_ISO, updated_at=NOW_ISO,
            ))
        session.commit()
        first = scan_analysis_backfill(session, now=NOW, version_refresh_limit=2)
        assert first.created == 1 and first.invalidated == 2 and first.deferred == 1
        assert session.get(ArticleAnalysisRecord, "brand-new").status == "pending"
        # 旧结果在重跑前仍可读(preserve_authority),分数没被清
        assert session.get(ArticleAnalysisRecord, "stale-0").quality_score == 7.0
        second = scan_analysis_backfill(session, now=NOW, version_refresh_limit=2)
        assert second.invalidated == 1 and second.deferred == 0


def test_disabled_dirty_podcasts_do_not_starve_legal_version_refresh(storage):
    with Session(storage.engine) as session:
        for index in range(16):
            source_id = f"disabled-podcast-{index:02d}"
            session.add(
                SourceConfigRecord(
                    source_id=source_id,
                    name=source_id,
                    source_type="podcast",
                    ai_analysis_enabled=False,
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
            article = _article(
                f"disabled-dirty-{index:02d}",
                source_id=source_id,
                fetched=NOW - dt.timedelta(minutes=index),
            )
            article.content_type = "podcast_episode"
            article.extensions_json = json.dumps(
                {"persons": [{"name": "New Guest", "role": "guest"}]}
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleAnalysisRecord(
                    article_id=article.id,
                    status="succeeded",
                    tagging_status="succeeded",
                    quality_score=7.0,
                    content_hash=compute_content_hash(article),
                    analysis_diagnostics_json=json.dumps(
                        {"people": [{"name": "Old Guest", "role": "guest"}]}
                    ),
                    prompt_version=PODCAST_ANALYSIS_PROMPT_VERSION,
                    scoring_version=PODCAST_ANALYSIS_SCORING_VERSION,
                    last_error=PODCAST_PEOPLE_DIRTY_REASON,
                    analyzed_at=NOW_ISO,
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )

        legal = _article("legal-version-stale", fetched=NOW - dt.timedelta(hours=1))
        session.add(legal)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=legal.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=7.5,
                content_hash=compute_content_hash(legal),
                prompt_version="article-analysis-v0",
                scoring_version="news-value-v0",
                analyzed_at=NOW_ISO,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()

        stats = scan_analysis_backfill(
            session, now=NOW, version_refresh_limit=1
        )
        assert stats.invalidated == 1
        assert stats.deferred == 0
        assert session.get(ArticleAnalysisRecord, legal.id).status == "pending"
        assert all(
            session.get(ArticleAnalysisRecord, f"disabled-dirty-{index:02d}").status
            == "succeeded"
            for index in range(16)
        )


def test_dirty_podcast_people_scan_is_bounded_and_advances_cursor(
    storage, monkeypatch
):
    import services.article_analysis as analysis_module

    monkeypatch.setattr(analysis_module, "PODCAST_PEOPLE_SCAN_PAGE_SIZE", 2)
    with Session(storage.engine) as session:
        for index in range(3):
            article = _article(
                f"old-dirty-{index}",
                fetched=NOW - dt.timedelta(days=30 + index),
            )
            article.content_type = "podcast_episode"
            article.extensions_json = json.dumps(
                {"persons": [{"name": f"New Guest {index}", "role": "guest"}]}
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleAnalysisRecord(
                    article_id=article.id,
                    status="succeeded",
                    tagging_status="succeeded",
                    quality_score=7.0,
                    content_hash=compute_content_hash(article),
                    analysis_diagnostics_json=json.dumps(
                        {"people": [{"name": f"Old Guest {index}", "role": "guest"}]}
                    ),
                    prompt_version=PODCAST_ANALYSIS_PROMPT_VERSION,
                    scoring_version=PODCAST_ANALYSIS_SCORING_VERSION,
                    last_error=PODCAST_PEOPLE_DIRTY_REASON,
                    analyzed_at=NOW_ISO,
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
        session.commit()

        first = scan_analysis_backfill(
            session, now=NOW, lookback_days=7, version_refresh_limit=10
        )
        assert first.invalidated == 2
        assert session.get(ArticleAnalysisRecord, "old-dirty-2").status == "succeeded"

        second = scan_analysis_backfill(
            session, now=NOW, lookback_days=7, version_refresh_limit=10
        )
        assert second.invalidated == 1
        assert session.get(ArticleAnalysisRecord, "old-dirty-2").status == "pending"
