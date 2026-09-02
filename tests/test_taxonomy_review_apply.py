"""Human approval import stays separate from taxonomy publication."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from sqlmodel import Session, select


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_taxonomy_v1_review as review_apply  # noqa: E402
from models.db import ArticleRecord, CmsTagCandidateRecord, CmsTagRecord, TaxonomyVersionRecord  # noqa: E402
from services.taxonomy import CandidateEvidenceInput, record_candidate_evidence, resolve_tag  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def test_apply_review_creates_aliases_resolves_candidates_but_does_not_publish():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            session.add(
                ArticleRecord(
                    id="robot-a",
                    title="Robot training",
                    content_type="article",
                    source_id="public-a",
                    source_url="https://example.com/robot-a",
                    publish_date=NOW.isoformat(),
                    fetched_date=NOW.isoformat(),
                    has_content=True,
                    content="Robotics learning and training.",
                )
            )
            session.commit()
            candidate = record_candidate_evidence(
                session,
                CandidateEvidenceInput(
                    article_id="robot-a",
                    source_id="public-a",
                    label="机器人",
                    proposed_kind="industry",
                    confidence=0.95,
                    published_date=NOW.isoformat(),
                ),
                now=NOW,
            )
            session.add(
                ArticleRecord(
                    id="robot-b",
                    title="Robot topic",
                    content_type="article",
                    source_id="public-b",
                    source_url="https://example.com/robot-b",
                    publish_date=NOW.isoformat(),
                    fetched_date=NOW.isoformat(),
                    has_content=True,
                    content="Robotics as a topic.",
                )
            )
            session.commit()
            other_facet = record_candidate_evidence(
                session,
                CandidateEvidenceInput(
                    article_id="robot-b",
                    source_id="public-b",
                    label="机器人",
                    proposed_kind="topic",
                    confidence=0.9,
                    published_date=NOW.isoformat(),
                ),
                now=NOW,
            )
            counts = review_apply.apply_review(
                session,
                [
                    {
                        "decision": "accept",
                        "code": "topic.robotics",
                        "kind": "topic",
                        "name_zh": "机器人",
                        "name_en": "Robotics",
                        "aliases": ["Robot Technology"],
                        "source_labels": ["机器人"],
                        "source_candidate_ids": [candidate.id],
                        "source_candidates": [
                            {
                                "candidate_id": candidate.id,
                                "kind": "industry",
                                "label": "机器人",
                                "normalized_label": "机器人",
                            }
                        ],
                    }
                ],
                actor_id="product-owner",
            )
            tag = session.exec(select(CmsTagRecord)).one()
            assert counts["created"] == 1
            assert counts["candidates_resolved"] == 1
            assert tag.user_selectable is True
            assert resolve_tag(session, "Robotics", kind="topic").id == tag.id
            assert resolve_tag(session, "Robot Technology", kind="topic").id == tag.id
            assert session.get(CmsTagCandidateRecord, candidate.id).status == "merged"
            assert session.get(CmsTagCandidateRecord, other_facet.id).status == "candidate"
            assert session.exec(select(TaxonomyVersionRecord)).first() is None
    finally:
        storage.engine.dispose()


def test_complete_review_requires_every_decision_and_explicit_coverage_bias():
    report = {
        "status": "human_review_required",
        "manifest_sha256": "a" * 64,
        "coverage": {"sampled_source_count": 7, "manifest_source_count": 31},
        "entries": [{"code": "topic.robotics", "decision": "pending"}],
        "unmapped_candidates": [],
    }
    try:
        review_apply.validate_complete_review(report)
    except ValueError as exc:
        assert "pending decisions" in str(exc)
    else:
        raise AssertionError("pending review unexpectedly passed")
    report["entries"][0]["decision"] = "accept"
    try:
        review_apply.validate_complete_review(report)
    except ValueError as exc:
        assert "source coverage is incomplete" in str(exc)
    else:
        raise AssertionError("unaccepted source bias unexpectedly passed")
    report["coverage_decision"] = "accept_bias"
    review_apply.validate_complete_review(report)


def test_approved_entity_requires_fixed_entity_type():
    report = {
        "status": "human_review_required",
        "entries": [
            {
                "decision": "accept",
                "code": "entity.mcp",
                "kind": "entity",
                "name_en": "MCP",
            }
        ],
    }
    try:
        review_apply.approved_entries(report)
    except ValueError as exc:
        assert "invalid entity_type" in str(exc)
    else:
        raise AssertionError("Entity without entity_type unexpectedly passed")
    report["entries"][0]["entity_type"] = "protocol"
    assert review_apply.approved_entries(report)[0]["entity_type"] == "protocol"


def test_v1_import_defaults_every_accepted_facet_to_selectable_but_allows_override():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            review_apply.apply_review(
                session,
                [
                    {
                        "code": "topic.agents",
                        "kind": "topic",
                        "name_zh": "智能体",
                        "name_en": "Agents",
                        "description": "可规划和调用工具的 AI 系统。",
                        "prompt_description": "仅当智能体是文章核心时使用。",
                    },
                    {
                        "code": "industry.software",
                        "kind": "industry",
                        "name_zh": "软件",
                        "name_en": "Software",
                        "user_selectable": False,
                    },
                    {
                        "code": "entity.mcp",
                        "kind": "entity",
                        "name_zh": "",
                        "name_en": "MCP",
                        "entity_type": "protocol",
                    },
                    {
                        "code": "topic.model-training",
                        "kind": "topic",
                        "name_zh": "模型训练",
                        "name_en": "Model Training",
                    },
                    {
                        "code": "topic.pretraining",
                        "kind": "topic",
                        "name_zh": "预训练",
                        "name_en": "Pre-training",
                        "parent_code": "topic.model-training",
                        "user_selectable": False,
                        "filterable": True,
                        "recommendable": True,
                    },
                ],
                actor_id="product-owner",
            )
            tags = {
                tag.code: tag
                for tag in session.exec(select(CmsTagRecord)).all()
            }
            assert tags["topic.agents"].user_selectable is True
            assert tags["topic.agents"].description == "可规划和调用工具的 AI 系统。"
            assert tags["topic.agents"].prompt_description == "仅当智能体是文章核心时使用。"
            assert tags["entity.mcp"].user_selectable is True
            assert tags["industry.software"].user_selectable is False
            assert tags["topic.pretraining"].parent_id == tags["topic.model-training"].id
            assert tags["topic.pretraining"].user_selectable is False
            assert tags["topic.pretraining"].filterable is True
            assert tags["topic.pretraining"].recommendable is True
    finally:
        storage.engine.dispose()


def test_label_set_only_review_has_no_fake_article_coverage():
    report = {
        "status": "human_review_required",
        "review_basis": "label_set_only",
        "manifest_sha256": "c" * 64,
        "coverage_decision": "not_applicable",
        "coverage": {
            "mode": "not_applicable",
            "sampled_source_count": 0,
            "manifest_source_count": 0,
            "article_count": 0,
            "candidate_count": 0,
        },
        "entries": [{"code": "topic.ai-safety", "decision": "accept"}],
        "unmapped_candidates": [],
    }
    review_apply.validate_complete_review(report)
    report["coverage"]["article_count"] = 1
    try:
        review_apply.validate_complete_review(report)
    except ValueError as exc:
        assert "must not carry article/source coverage" in str(exc)
    else:
        raise AssertionError("label-set-only review accepted fake article coverage")


def test_review_rejects_duplicate_candidate_mapping_and_target_drift():
    report = {
        "status": "human_review_required",
        "manifest_sha256": "b" * 64,
        "coverage": {"sampled_source_count": 1, "manifest_source_count": 1},
        "entries": [
            {
                "code": "topic.robotics",
                "decision": "accept",
                "source_candidates": [
                    {"kind": "topic", "normalized_label": "robotics"}
                ],
            },
            {
                "code": "topic.robot-learning",
                "decision": "reject",
                "source_candidates": [
                    {"kind": "topic", "normalized_label": "robotics"}
                ],
            },
        ],
        "unmapped_candidates": [],
    }
    try:
        review_apply.validate_complete_review(report)
    except ValueError as exc:
        assert "mapped to both" in str(exc)
    else:
        raise AssertionError("duplicate Candidate mapping unexpectedly passed")

    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            session.add(
                CmsTagCandidateRecord(
                    label="Unexpected",
                    normalized_label="unexpected",
                    proposed_kind="topic",
                    status="candidate",
                    created_at=NOW.isoformat(),
                    updated_at=NOW.isoformat(),
                    first_seen_at=NOW.isoformat(),
                    last_seen_at=NOW.isoformat(),
                )
            )
            session.commit()
            try:
                review_apply.validate_target_candidate_coverage(session, report)
            except ValueError as exc:
                assert "outside this review" in str(exc)
            else:
                raise AssertionError("target Candidate drift unexpectedly passed")
    finally:
        storage.engine.dispose()
