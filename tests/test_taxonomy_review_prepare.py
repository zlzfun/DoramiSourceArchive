"""Approved catalog binding produces a complete review without writes."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import Session


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prepare_taxonomy_v1_review as review_prepare  # noqa: E402
from models.db import CmsTagCandidateRecord  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _candidate(label: str, kind: str) -> CmsTagCandidateRecord:
    return CmsTagCandidateRecord(
        label=label,
        normalized_label=label.casefold(),
        proposed_kind=kind,
        status="candidate",
        created_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-01T00:00:00+00:00",
        first_seen_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-01T00:00:00+00:00",
    )


def test_prepare_review_maps_aliases_keeps_merge_only_out_of_aliases_and_rejects_rest():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            session.add_all([
                _candidate("AI Agent", "topic"),
                _candidate("机器人", "industry"),
                _candidate("一次性事件", "topic"),
            ])
            session.commit()
            report = review_prepare.prepare_review(
                session,
                {
                    "status": "product_approved",
                    "manifest_sha256": "a" * 64,
                    "coverage": {"sampled_source_count": 1, "manifest_source_count": 1},
                    "coverage_decision": "complete",
                    "reject_unmatched_candidates_for_v1": True,
                    "entries": [
                        {
                            "decision": "accept",
                            "code": "topic.ai-agents",
                            "kind": "topic",
                            "name_zh": "AI 智能体",
                            "name_en": "AI Agents",
                            "candidate_matches": [{"kind": "topic", "labels": ["AI Agent"]}],
                        },
                        {
                            "decision": "accept",
                            "code": "industry.robotics",
                            "kind": "industry",
                            "name_zh": "机器人产业",
                            "name_en": "Robotics Industry",
                            "merge_only_candidates": [{"kind": "industry", "labels": ["机器人"]}],
                        },
                    ],
                },
            )
            assert report["decision_summary"] == {
                "accepted_entries": 2,
                "candidate_merges": 2,
                "candidate_rejections": 1,
            }
            agents = report["entries"][0]
            assert agents["source_labels"] == ["AI Agent"]
            assert agents["source_candidates"][0]["normalized_label"] == "ai agent"
            merge_only = report["unmapped_candidates"][0]
            assert merge_only["decision"] == "merge"
            assert merge_only["resolution_code"] == "industry.robotics"
            assert report["unmapped_candidates"][1]["decision"] == "reject"
    finally:
        storage.engine.dispose()


def test_label_set_only_catalog_requires_a_fresh_candidate_pool():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    catalog = {
        "status": "product_approved",
        "review_basis": "label_set_only",
        "unmatched_candidate_policy": "fail",
        "manifest_sha256": "b" * 64,
        "coverage": {
            "mode": "not_applicable",
            "sampled_source_count": 0,
            "manifest_source_count": 0,
            "article_count": 0,
            "candidate_count": 0,
        },
        "coverage_decision": "not_applicable",
        "entries": [{
            "decision": "accept",
            "code": "topic.ai-safety",
            "kind": "topic",
            "name_zh": "AI 对齐与安全",
            "name_en": "AI Safety",
        }],
    }
    try:
        with Session(storage.engine) as session:
            report = review_prepare.prepare_review(session, catalog)
            assert report["review_basis"] == "label_set_only"
            assert report["unmapped_candidates"] == []
            session.add(_candidate("未来的新候选", "topic"))
            session.commit()
            try:
                review_prepare.prepare_review(session, catalog)
            except ValueError as exc:
                assert "no unresolved Candidates" in str(exc)
            else:
                raise AssertionError("label-set-only bootstrap accepted a non-fresh Candidate pool")
    finally:
        storage.engine.dispose()
