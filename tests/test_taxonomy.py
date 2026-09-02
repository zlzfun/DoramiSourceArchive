"""WP-2 taxonomy, Candidate, governance and retag service tests."""

import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import ArticleTagAssignmentDTO  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagAliasRecord,
    CmsTagEventRecord,
    CmsTagRecord,
    SourceConfigRecord,
    TagRetagJobRecord,
    TaxonomyVersionRecord,
    UserInterestTagRecord,
    UserRecord,
)
from services.taxonomy import (  # noqa: E402
    AmbiguousTagError,
    AutoActivationThresholds,
    CandidateEvidenceInput,
    TaxonomyError,
    activate_taxonomy_version,
    assign_article_tags,
    auto_activation_enabled,
    candidate_meets_auto_activation_threshold,
    canonical_alias_gap_count,
    change_tag_parent,
    change_tag_flags,
    change_tag_descriptions,
    change_entity_metadata,
    claim_retag_job,
    create_tag,
    create_alias,
    delete_candidate,
    delete_alias,
    create_taxonomy_version,
    deprecate_tag,
    merge_tags,
    maybe_auto_activate_candidate,
    normalize_label,
    process_retag_batch,
    queue_retag_job,
    ranked_interest_catalog,
    record_candidate_evidence,
    retag_article_from_evidence,
    reject_candidate,
    reclassify_candidate,
    resolve_candidate_to_tag,
    rename_tag,
    resolve_tag,
    retry_failed_retag_job,
    run_auto_activation_cycle,
    set_auto_activation_enabled,
    set_interest_catalog_policy,
    taxonomy_governance_state,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 1, 8, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
NOW_ISO = NOW.isoformat()


@pytest.fixture
def storage():
    value = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        yield value
    finally:
        value.engine.dispose()


def article(article_id: str, source_id: str = "public-1", *, day: int = 1) -> ArticleRecord:
    published_at = NOW if day == 1 else NOW - dt.timedelta(days=1)
    published = published_at.isoformat()
    return ArticleRecord(
        id=article_id,
        title=f"Article {article_id}",
        content_type="article",
        source_id=source_id,
        source_url=f"https://{source_id}.example/{article_id}",
        publish_date=published,
        fetched_date=published,
        has_content=True,
        content="A useful article body.",
    )


def active_tag(session: Session, code: str, kind: str, name: str) -> CmsTagRecord:
    tag = create_tag(
        session,
        code=code,
        kind=kind,
        name_en=name,
        status="active",
        entity_type="organization" if kind == "entity" else "",
        now=NOW,
    )
    session.commit()
    return tag


def test_normalization_alias_resolution_and_cross_facet_ambiguity(storage):
    with Session(storage.engine) as session:
        topic = active_tag(session, "apple-topic", "topic", "AI-Agent")
        entity = active_tag(session, "apple-inc", "entity", "Apple")
        from services.taxonomy import add_alias

        add_alias(session, tag_id=topic.id, alias="ＡＩ Agent", alias_type="translation", now=NOW)
        session.commit()

        assert normalize_label("  ＡＩ—Agent  ") == "ai agent"
        assert resolve_tag(session, "ai_agent", kind="topic").id == topic.id
        assert resolve_tag(session, "AI-Agent", kind="topic").id == topic.id
        assert resolve_tag(session, "Apple", kind="entity").id == entity.id

        second = active_tag(session, "apple-industry", "industry", "Apple")
        assert second.id != entity.id
        with pytest.raises(AmbiguousTagError):
            resolve_tag(session, "Apple")


def test_entity_type_is_required_for_active_entities_and_can_be_governed(storage):
    with Session(storage.engine) as session:
        with pytest.raises(TaxonomyError, match="require entity_type"):
            create_tag(
                session,
                code="entity.mcp",
                kind="entity",
                name_en="MCP",
                status="active",
            )
        draft = create_tag(
            session,
            code="entity.mcp",
            kind="entity",
            name_en="MCP",
            status="draft",
        )
        changed = change_entity_metadata(
            session,
            int(draft.id),
            actor_id="admin",
            entity_type="protocol",
            external_key="protocol:mcp",
            reason="product classification",
            now=NOW,
        )
        assert changed.entity_type == "protocol"
        assert changed.external_key == "protocol:mcp"


def test_interest_catalog_uses_governed_top_n_and_keeps_current_choices(storage):
    with Session(storage.engine) as session:
        session.add_all([
            article("hot-1"),
            article("hot-2"),
            article("warm-1"),
            UserRecord(
                username="reader",
                password_hash="hash",
                role="user",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
        ])
        hot = active_tag(session, "topic.hot", "topic", "Hot")
        warm = active_tag(session, "topic.warm", "topic", "Warm")
        current = active_tag(session, "topic.current", "topic", "Current")
        for tag in (hot, warm, current):
            tag.user_selectable = True
            session.add(tag)
        for article_id, tag in (("hot-1", hot), ("hot-2", hot), ("warm-1", warm)):
            session.add(
                ArticleTagAssignmentRecord(
                    article_id=article_id,
                    tag_id=int(tag.id),
                    tag_kind="topic",
                    relevance=0.8,
                    assignment_source="llm",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                )
            )
        session.add(
            UserInterestTagRecord(
                owner_username="reader",
                tag_id=int(current.id),
                stance="follow",
                priority="normal",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()
        set_interest_catalog_policy(
            session,
            {"topic": 1, "industry": 0, "entity": 0},
            actor_id="admin",
            reason="test limits",
            now=NOW,
        )
        result = ranked_interest_catalog(session, owner_username="reader", now=NOW)
        assert [tag.code for tag in result["tags"]] == ["topic.hot", "topic.current"]
        assert result["metadata"][int(hot.id)]["heat_30d"] == 2
        assert result["metadata"][int(current.id)] == {
            "heat_30d": 0,
            "in_top_n": False,
            "is_current_interest": True,
        }


def test_bilingual_names_sync_translation_alias_and_alias_crud(storage):
    with Session(storage.engine) as session:
        tag = create_tag(
            session,
            code="topic.embodied-ai",
            kind="topic",
            name_zh="具身智能",
            name_en="Embodied AI",
            status="active",
            now=NOW,
        )
        session.commit()
        assert resolve_tag(session, "具身智能", kind="topic").id == tag.id
        assert resolve_tag(session, "embodied ai", kind="topic").id == tag.id
        assert canonical_alias_gap_count(session) == 0
        translation = session.exec(
            select(CmsTagAliasRecord).where(CmsTagAliasRecord.tag_id == tag.id)
        ).one()
        assert translation.alias == "Embodied AI"
        assert translation.alias_type == "translation"

        custom = create_alias(
            session,
            tag_id=tag.id,
            alias="Embodied Intelligence",
            alias_type="synonym",
            actor_id="admin",
            reason="reviewed synonym",
            now=NOW,
        )
        assert resolve_tag(session, "Embodied Intelligence", kind="topic").id == tag.id
        delete_alias(
            session,
            tag_id=tag.id,
            alias_id=custom.id,
            actor_id="admin",
            reason="remove broad synonym",
            now=NOW,
        )
        assert resolve_tag(session, "Embodied Intelligence", kind="topic") is None
        with pytest.raises(TaxonomyError, match="canonical translation"):
            delete_alias(
                session,
                tag_id=tag.id,
                alias_id=translation.id,
                actor_id="admin",
                now=NOW,
            )


def test_parent_hierarchy_rejects_cross_facet_and_cycles(storage):
    with Session(storage.engine) as session:
        root = active_tag(session, "topic.robotics", "topic", "Robotics")
        child = create_tag(
            session,
            code="topic.embodied-ai",
            kind="topic",
            name_en="Embodied AI",
            status="active",
            parent_id=root.id,
            now=NOW,
        )
        industry = active_tag(session, "industry.robotics", "industry", "Robotics")
        session.commit()
        assert child.parent_id == root.id
        with pytest.raises(TaxonomyError, match="same facet"):
            change_tag_parent(
                session,
                child.id,
                actor_id="admin",
                parent_id=industry.id,
                now=NOW,
            )
        with pytest.raises(TaxonomyError, match="cycle"):
            change_tag_parent(
                session,
                root.id,
                actor_id="admin",
                parent_id=child.id,
                now=NOW,
            )


def test_assignment_validation_facet_limits_and_single_display_primary(storage):
    with Session(storage.engine) as session:
        session.add(article("a1"))
        topic = active_tag(session, "agents", "topic", "AI Agents")
        entity = active_tag(session, "openai", "entity", "OpenAI")
        industry = active_tag(session, "finance", "industry", "Finance")
        session.add(
            ArticleAnalysisRecord(
                article_id="a1",
                status="succeeded",
                tagging_status="pending",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()

        rows = assign_article_tags(
            session,
            article_id="a1",
            assignments=[
                ArticleTagAssignmentDTO(code=entity.code, kind="entity", relevance=0.99),
                ArticleTagAssignmentDTO(code=industry.code, kind="industry", relevance=0.98),
                ArticleTagAssignmentDTO(code=topic.code, kind="topic", relevance=0.70),
            ],
            now=NOW,
        )
        primaries = [row for row in rows if row.is_primary]
        assert len(primaries) == 1
        assert primaries[0].tag_id == topic.id  # topic > entity > industry
        assert session.get(ArticleAnalysisRecord, "a1").primary_tag_id == topic.id

        extra_topics = [active_tag(session, f"topic-{idx}", "topic", f"Topic {idx}") for idx in range(6)]
        with pytest.raises(TaxonomyError, match="too many topic"):
            assign_article_tags(
                session,
                article_id="a1",
                assignments=[
                    {"code": tag.code, "kind": "topic", "relevance": 0.8}
                    for tag in extra_topics
                ],
                now=NOW,
            )


def test_candidate_evidence_is_idempotent_and_aggregates_7_and_30_days(storage):
    with Session(storage.engine) as session:
        inputs = []
        for idx in range(10):
            source_id = f"source-{idx % 3}"
            day = 1 if idx < 6 else 31
            row = article(f"a{idx}", source_id, day=day)
            session.add(row)
            inputs.append(
                CandidateEvidenceInput(
                    article_id=row.id,
                    source_id=source_id,
                    source_owner_or_domain=f"owner-{idx % 3}",
                    label="Agentic Workflow",
                    proposed_kind="topic",
                    confidence=0.95,
                    published_date=row.publish_date,
                )
            )
        session.commit()
        for item in inputs:
            record_candidate_evidence(session, item, now=NOW)
        # Retrying the same article/candidate pair does not add support.
        record_candidate_evidence(session, inputs[0], now=NOW)

        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        assert candidate.support_article_count_7d == 10
        assert candidate.distinct_source_count_7d == 3
        assert candidate.distinct_day_count_7d == 2
        assert candidate.mean_confidence == pytest.approx(0.95)
        evidence = session.exec(select(CmsTagCandidateEvidenceRecord)).all()
        assert len(evidence) == 10
        assert candidate_meets_auto_activation_threshold(candidate, AutoActivationThresholds())


def test_private_rss_unknown_term_never_enters_public_candidate(storage):
    with Session(storage.engine) as session:
        session.add_all(
            [
                article("private-a", "private-rss"),
                SourceConfigRecord(
                    source_id="private-rss",
                    name="My private RSS",
                    owner_username="reader",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
            ]
        )
        session.commit()
        result = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="private-a",
                source_id="private-rss",
                label="Secret Project",
                proposed_kind="entity",
                confidence=0.99,
                context_excerpt="private body must not leak",
            ),
            now=NOW,
        )
        assert result is None
        assert session.exec(select(CmsTagCandidateRecord)).first() is None
        assert session.exec(select(CmsTagCandidateEvidenceRecord)).first() is None


def test_auto_activation_requires_combined_thresholds_and_keeps_interest_flag_off(storage):
    with Session(storage.engine) as session:
        for idx in range(10):
            source_id = f"source-{idx % 3}"
            row = article(f"auto-{idx}", source_id, day=1 if idx < 5 else 31)
            session.add(row)
        session.commit()
        for idx in range(10):
            record_candidate_evidence(
                session,
                CandidateEvidenceInput(
                    article_id=f"auto-{idx}",
                    source_id=f"source-{idx % 3}",
                    source_owner_or_domain=f"owner-{idx % 3}",
                    label="Tool Protocol",
                    proposed_kind="topic",
                    confidence=0.94,
                    published_date=(NOW if idx < 5 else NOW - dt.timedelta(days=1)).isoformat(),
                ),
                now=NOW,
            )
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        assert maybe_auto_activate_candidate(
            session,
            candidate.id,
            thresholds=AutoActivationThresholds(),
            now=NOW,
        ) is None
        set_auto_activation_enabled(session, True)
        assert auto_activation_enabled(session)
        tag = maybe_auto_activate_candidate(
            session,
            candidate.id,
            thresholds=AutoActivationThresholds(),
            now=NOW,
        )
        assert tag is not None
        assert tag.status == "active"
        assert tag.activation_mode == "automatic"
        assert tag.user_selectable is False
        event = session.exec(select(CmsTagEventRecord)).one()
        assert event.action == "activate" and event.actor_type == "system"


def test_single_source_frequency_cannot_auto_activate(storage):
    with Session(storage.engine) as session:
        for idx in range(12):
            row = article(f"spam-{idx}", "spam-source", day=1 if idx < 6 else 31)
            session.add(row)
        session.commit()
        for idx in range(12):
            record_candidate_evidence(
                session,
                CandidateEvidenceInput(
                    article_id=f"spam-{idx}",
                    source_id="spam-source",
                    source_owner_or_domain="one-owner",
                    label="Spam Buzzword",
                    proposed_kind="topic",
                    confidence=0.99,
                    published_date=(NOW if idx < 6 else NOW - dt.timedelta(days=1)).isoformat(),
                ),
                now=NOW,
            )
        set_auto_activation_enabled(session, True)
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        assert candidate.support_article_count_7d == 12
        assert candidate.distinct_source_count_7d == 1
        assert maybe_auto_activate_candidate(
            session,
            candidate.id,
            thresholds=AutoActivationThresholds(),
            now=NOW,
        ) is None


def test_candidate_can_be_reclassified_or_resolved_to_existing_tag(storage):
    with Session(storage.engine) as session:
        session.add_all([article("candidate-a"), article("candidate-b")])
        target = active_tag(session, "topic.robotics", "topic", "Robotics")
        session.commit()
        first = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="candidate-a",
                source_id="public-1",
                label="机器人学习",
                proposed_kind="industry",
                confidence=0.92,
                published_date=NOW_ISO,
            ),
            now=NOW,
        )
        corrected = reclassify_candidate(
            session,
            first.id,
            kind="topic",
            actor_id="admin",
            reason="这是技术主题，不是行业",
            now=NOW,
        )
        assert corrected.proposed_kind == "topic"

        second = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="candidate-b",
                source_id="public-1",
                label="Robotics",
                proposed_kind="entity",
                confidence=0.95,
                published_date=NOW_ISO,
            ),
            now=NOW,
        )
        resolved = resolve_candidate_to_tag(
            session,
            second.id,
            target_tag_id=target.id,
            actor_id="admin",
            reason="英文同义候选",
            now=NOW,
        )
        assert resolved.status == "merged"
        assert resolved.resolution_tag_id == target.id
        assert resolve_tag(session, "Robotics", kind="topic").id == target.id


def test_candidate_delete_is_audited_and_resolved_history_is_protected(storage):
    with Session(storage.engine) as session:
        session.add_all([article("delete-a"), article("delete-b")])
        target = active_tag(session, "topic.security", "topic", "AI Safety")
        session.commit()
        candidate = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="delete-a",
                source_id="public-1",
                label="Bad Marketing Label",
                proposed_kind="topic",
                confidence=0.51,
                published_date=NOW_ISO,
            ),
            now=NOW,
        )
        candidate_id = int(candidate.id)

        payload = delete_candidate(
            session,
            candidate_id,
            actor_id="admin",
            reason="低质量且不可复用",
            now=NOW,
        )

        assert payload["evidence_count"] == 1
        assert session.get(CmsTagCandidateRecord, candidate_id) is None
        assert session.exec(select(CmsTagCandidateEvidenceRecord)).all() == []
        event = session.exec(
            select(CmsTagEventRecord).where(CmsTagEventRecord.action == "delete_candidate")
        ).one()
        assert event.actor_id == "admin"
        assert json.loads(event.payload_json)["label"] == "Bad Marketing Label"

        resolved = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="delete-b",
                source_id="public-1",
                label="Secure AI",
                proposed_kind="topic",
                confidence=0.95,
                published_date=NOW_ISO,
            ),
            now=NOW,
        )
        resolve_candidate_to_tag(
            session,
            int(resolved.id),
            target_tag_id=int(target.id),
            actor_id="admin",
            reason="同义标签",
            now=NOW,
        )
        with pytest.raises(TaxonomyError, match="resolved candidates cannot be deleted"):
            delete_candidate(
                session,
                int(resolved.id),
                actor_id="admin",
                reason="不应删除治理历史",
                now=NOW,
            )


def test_runtime_auto_activation_cycle_processes_topics_but_not_entities(storage):
    with Session(storage.engine) as session:
        for kind, label in (("topic", "Reasoning Runtime"), ("entity", "Stable Corp")):
            for idx in range(10):
                article_id = f"{kind}-{idx}"
                source_id = f"source-{idx % 3}"
                session.add(article(article_id, source_id, day=1 if idx < 5 else 31))
            session.commit()
            for idx in range(10):
                record_candidate_evidence(
                    session,
                    CandidateEvidenceInput(
                        article_id=f"{kind}-{idx}",
                        source_id=f"source-{idx % 3}",
                        source_owner_or_domain=f"owner-{idx % 3}",
                        label=label,
                        proposed_kind=kind,
                        confidence=0.96,
                        published_date=(NOW if idx < 5 else NOW - dt.timedelta(days=1)).isoformat(),
                    ),
                    now=NOW,
                )
        set_auto_activation_enabled(session, True)
        assert run_auto_activation_cycle(session, now=NOW) == []
        version = create_taxonomy_version(session, change_summary="reviewed taxonomy", now=NOW)
        activate_taxonomy_version(session, version.version, actor_id="admin", now=NOW)
        activated = run_auto_activation_cycle(session, now=NOW)
        assert [tag.name_en for tag in activated] == ["Reasoning Runtime"]
        assert activated[0].code == "topic.reasoning-runtime"
        entity = session.exec(
            select(CmsTagCandidateRecord).where(CmsTagCandidateRecord.proposed_kind == "entity")
        ).one()
        assert entity.status == "candidate"


def test_governance_state_requires_a_valid_review_receipt_and_auto_activation_off(storage):
    with Session(storage.engine) as session:
        for kind in ("topic", "industry", "entity"):
            create_tag(
                session,
                code=f"{kind}.seed",
                kind=kind,
                name_zh=f"{kind} 种子",
                name_en=f"{kind.title()} Seed",
                status="active",
                entity_type="organization" if kind == "entity" else "",
            )
        state = taxonomy_governance_state(session, now=NOW)
        assert state["publish_ready"] is False
        assert state["canonical_alias_gap_count"] == 0
        assert state["review_receipt_valid"] is False

        session.add(AppSettingRecord(key="taxonomy_v1_review_receipt", value="{}"))
        session.commit()
        assert taxonomy_governance_state(session, now=NOW)["publish_ready"] is False

        receipt = session.get(AppSettingRecord, "taxonomy_v1_review_receipt")
        receipt.value = (
            '{"manifest_sha256":"' + "a" * 64
            + '","actor_id":"product-owner","reviewed_at":"2026-09-01T08:30:00+08:00",'
            '"coverage_decision":"accept_bias"}'
        )
        session.add(receipt)
        session.commit()
        assert taxonomy_governance_state(session, now=NOW)["publish_ready"] is True

        set_auto_activation_enabled(session, True)
        state = taxonomy_governance_state(session, now=NOW)
        assert state["publish_ready"] is False
        assert any("自动激活" in blocker for blocker in state["publish_blockers"])


def test_merge_preserves_manual_assignment_mute_interest_and_old_name_alias(storage):
    with Session(storage.engine) as session:
        session.add_all(
            [
                article("merge-a"),
                UserRecord(
                    username="reader",
                    password_hash="hash",
                    role="user",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
            ]
        )
        source = active_tag(session, "old-agent", "topic", "Old Agent")
        target = active_tag(session, "ai-agent", "topic", "AI Agent")
        session.add_all(
            [
                ArticleTagAssignmentRecord(
                    article_id="merge-a",
                    tag_id=source.id,
                    tag_kind="topic",
                    is_primary=True,
                    relevance=0.7,
                    assignment_source="manual",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
                ArticleTagAssignmentRecord(
                    article_id="merge-a",
                    tag_id=target.id,
                    tag_kind="topic",
                    is_primary=False,
                    relevance=0.95,
                    assignment_source="llm",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
                UserInterestTagRecord(
                    owner_username="reader",
                    tag_id=source.id,
                    stance="follow",
                    priority="high",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
                UserInterestTagRecord(
                    owner_username="reader",
                    tag_id=target.id,
                    stance="mute",
                    priority="normal",
                    created_at=NOW_ISO,
                    updated_at=NOW_ISO,
                ),
            ]
        )
        session.commit()

        merge_tags(
            session,
            source.id,
            target.id,
            actor_id="admin",
            reason="same concept",
            now=NOW,
        )
        assignment = session.exec(select(ArticleTagAssignmentRecord)).one()
        assert assignment.tag_id == target.id
        assert assignment.assignment_source == "manual"
        assert assignment.is_primary is True
        interest = session.exec(select(UserInterestTagRecord)).one()
        assert interest.tag_id == target.id and interest.stance == "mute"
        old = session.get(CmsTagRecord, source.id)
        assert old.status == "merged" and old.replacement_id == target.id
        assert resolve_tag(session, "Old Agent", kind="topic").id == target.id
        event = session.exec(select(CmsTagEventRecord)).one()
        assert event.action == "merge" and event.source_tag_id == source.id


def test_manual_governance_changes_are_audited_and_old_name_still_resolves(storage):
    with Session(storage.engine) as session:
        session.add(article("govern-a"))
        source = active_tag(session, "old-runtime", "topic", "Old Runtime")
        replacement = active_tag(session, "agent-runtime", "topic", "Agent Runtime")
        session.commit()
        renamed = rename_tag(
            session,
            source.id,
            actor_id="admin",
            name_en="Runtime Framework",
            reason="clearer name",
            now=NOW,
        )
        assert renamed.name_en == "Runtime Framework"
        assert resolve_tag(session, "Old Runtime", kind="topic").id == source.id
        described = change_tag_descriptions(
            session,
            source.id,
            actor_id="admin",
            description="Human-facing scope",
            prompt_description="Use only for runtime framework architecture.",
            reason="clarify model boundary",
            now=NOW,
        )
        assert described.description == "Human-facing scope"
        assert described.prompt_description == "Use only for runtime framework architecture."
        change_tag_flags(
            session,
            source.id,
            actor_id="admin",
            user_selectable=True,
            reason="reviewed for interests",
            now=NOW,
        )
        deprecate_tag(
            session,
            source.id,
            replacement_id=replacement.id,
            actor_id="admin",
            reason="superseded",
            now=NOW,
        )
        candidate = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id="govern-a",
                source_id="public-1",
                label="Bad Candidate",
                proposed_kind="topic",
                confidence=0.8,
                published_date=NOW_ISO,
            ),
            now=NOW,
        )
        reject_candidate(
            session,
            candidate.id,
            actor_id="admin",
            reason="source metadata",
            now=NOW,
        )
        actions = [row.action for row in session.exec(select(CmsTagEventRecord)).all()]
        assert actions == ["rename", "change_flags", "change_flags", "deprecate", "reject"]
        description_event = session.exec(
            select(CmsTagEventRecord).where(CmsTagEventRecord.source_tag_id == source.id)
        ).all()[1]
        assert json.loads(description_event.payload_json)["operation"] == "change_descriptions"


def test_bilingual_rename_retypes_changed_translation_and_skips_noop_event(storage):
    with Session(storage.engine) as session:
        tag = create_tag(
            session,
            code="topic.ai-agents",
            kind="topic",
            name_zh="AI 智能体",
            name_en="AI Agent",
            status="active",
            now=NOW,
        )
        session.commit()
        old_alias = session.exec(
            select(CmsTagAliasRecord).where(CmsTagAliasRecord.alias == "AI Agent")
        ).one()
        assert old_alias.alias_type == "translation"

        renamed = rename_tag(
            session,
            tag.id,
            actor_id="admin",
            name_zh="AI 智能体",
            name_en="AI Agents",
            reason="plural canonical name",
            now=NOW,
        )
        aliases = {
            row.alias: row.alias_type
            for row in session.exec(
                select(CmsTagAliasRecord).where(CmsTagAliasRecord.tag_id == tag.id)
            ).all()
        }
        assert aliases == {"AI Agent": "former_name", "AI Agents": "translation"}

        rename_tag(
            session,
            tag.id,
            actor_id="admin",
            name_zh=renamed.name_zh,
            name_en=renamed.name_en,
            reason="no change",
            now=NOW,
        )
        events = session.exec(select(CmsTagEventRecord)).all()
        assert [event.action for event in events] == ["rename"]


def test_taxonomy_version_and_retag_lease_cursor_retry(storage):
    with Session(storage.engine) as session:
        for article_id in ("r1", "r2", "r3"):
            session.add(article(article_id))
        session.commit()
        version = create_taxonomy_version(session, change_summary="taxonomy v1", now=NOW)
        activate_taxonomy_version(session, version.version, actor_id="admin", now=NOW)
        job = queue_retag_job(
            session,
            taxonomy_version=version.version,
            scope={"since": "2026-08-25T00:00:00+08:00"},
            now=NOW,
        )
        claimed = claim_retag_job(session, lease_owner="worker-a", lease_seconds=1, now=NOW)
        assert claimed.id == job.id and claimed.status == "running"
        same_worker = claim_retag_job(
            session, lease_owner="worker-a", lease_seconds=1, now=NOW
        )
        assert same_worker.id == job.id and same_worker.lease_owner == "worker-a"
        assert claim_retag_job(session, lease_owner="worker-b", now=NOW) is None
        recovered = claim_retag_job(
            session,
            lease_owner="worker-b",
            now=NOW + dt.timedelta(seconds=2),
        )
        assert recovered.id == job.id and recovered.lease_owner == "worker-b"

        visited = []

        def retag(_session, row, taxonomy_version):
            visited.append((row.id, taxonomy_version))
            if row.id == "r2":
                raise RuntimeError("temporary model failure")

        process_retag_batch(
            session,
            recovered,
            retag,
            lease_owner="worker-b",
            batch_size=2,
            now=NOW,
        )
        running = session.get(TagRetagJobRecord, job.id)
        assert running.status == "running" and running.cursor == "r2"
        process_retag_batch(
            session,
            running,
            retag,
            lease_owner="worker-b",
            batch_size=2,
            now=NOW,
        )
        finished = session.get(TagRetagJobRecord, job.id)
        assert finished.status == "partial_failed"
        assert (finished.affected_count, finished.succeeded_count, finished.failed_count) == (3, 2, 1)
        assert [item[0] for item in visited] == ["r1", "r2", "r3"]
        retry = retry_failed_retag_job(session, finished, now=NOW)
        assert retry.status == "queued"
        assert '"article_ids": ["r2"]' in retry.scope_json


def test_retag_worker_never_claims_or_processes_full_analysis_job(storage):
    with Session(storage.engine) as session:
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW_ISO))
        session.flush()
        session.add(
            TagRetagJobRecord(
                taxonomy_version=1,
                operation="full_analysis",
                scope_json="{}",
                status="queued",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()

        assert claim_retag_job(session, lease_owner="retag-worker", now=NOW) is None

        job = session.exec(select(TagRetagJobRecord)).one()
        job.status = "running"
        job.lease_owner = "retag-worker"
        session.add(job)
        session.commit()
        with pytest.raises(TaxonomyError, match="full-analysis"):
            process_retag_batch(
                session,
                job,
                retag_article_from_evidence,
                lease_owner="retag-worker",
                now=NOW,
            )


def test_closed_set_retag_reuses_resolved_evidence_without_rescoring(storage):
    with Session(storage.engine) as session:
        row = article("retag-evidence")
        session.add(row)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=row.id,
                status="succeeded",
                tagging_status="pending",
                quality_score=8.4,
                score_reason="keep this score reason",
                one_sentence_summary="keep this one-line summary",
                summary="keep this full summary",
                prompt_version="article-analysis-v2",
                scoring_version="article-value-v1",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            )
        )
        session.commit()
        tag = active_tag(session, "topic.ai-agents", "topic", "AI Agents")
        candidate = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id=row.id,
                source_id=row.source_id,
                label="AI Agent",
                proposed_kind="topic",
                confidence=0.93,
                published_date=row.publish_date,
            ),
            now=NOW,
        )
        resolve_candidate_to_tag(
            session,
            int(candidate.id),
            target_tag_id=int(tag.id),
            actor_id="admin",
            reason="approved review",
            now=NOW,
        )
        version = create_taxonomy_version(session, change_summary="taxonomy v1", now=NOW)
        activate_taxonomy_version(session, version.version, actor_id="admin", now=NOW)
        job = queue_retag_job(
            session,
            taxonomy_version=version.version,
            scope={"article_ids": [row.id]},
            now=NOW,
        )
        claimed = claim_retag_job(session, lease_owner="runtime-all-retag", now=NOW)
        process_retag_batch(
            session,
            claimed,
            retag_article_from_evidence,
            lease_owner="runtime-all-retag",
            batch_size=50,
            now=NOW,
        )

        finished = session.get(TagRetagJobRecord, job.id)
        analysis = session.get(ArticleAnalysisRecord, row.id)
        assignment = session.exec(
            select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id == row.id
            )
        ).one()
        assert finished.status == "succeeded"
        assert assignment.tag_id == tag.id
        assert assignment.relevance == pytest.approx(0.93)
        assert assignment.taxonomy_version == version.version
        assert analysis.taxonomy_version == version.version
        assert analysis.tagging_status == "succeeded"
        assert analysis.quality_score == 8.4
        assert analysis.summary == "keep this full summary"
