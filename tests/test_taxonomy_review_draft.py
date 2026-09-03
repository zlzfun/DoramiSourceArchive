"""Validation guards for the human-only taxonomy v1 review draft."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import draft_taxonomy_v1_review as review_draft  # noqa: E402


def test_review_draft_rejects_bad_codes_facets_and_unknown_source_labels():
    entries = review_draft.validate_entries(
        {
            "entries": [
                {
                    "code": "topic.agentic-ai",
                    "kind": "topic",
                    "name_zh": "智能体",
                    "name_en": "Agentic AI",
                    "aliases": ["AI Agent", "AI Agent"],
                    "suggested_user_selectable": True,
                    "source_labels": ["智能体", "not-in-input"],
                    "rationale": "stable concept",
                    "risk": "",
                },
                {
                    "code": "bad code",
                    "kind": "topic",
                    "name_zh": "坏 code",
                },
                {
                    "code": "industry.software",
                    "kind": "industry",
                    "name_zh": "软件",
                },
            ]
        },
        kind="topic",
        source_labels={"智能体"},
    )
    assert entries == [
        {
            "decision": "pending",
            "code": "topic.agentic-ai",
            "kind": "topic",
            "name_zh": "智能体",
            "name_en": "Agentic AI",
            "aliases": ["AI Agent"],
            "description": "",
            "prompt_description": "",
            "parent_code": "",
            "user_selectable": True,
            "suggested_user_selectable": True,
            "source_labels": ["智能体"],
            "rationale": "stable concept",
            "risk": "",
        }
    ]


def test_review_markdown_states_pending_and_incomplete_coverage():
    report = {
        "manifest_sha256": "abc",
        "coverage": {
            "article_count": 10,
            "sampled_source_count": 1,
            "manifest_source_count": 31,
            "candidate_count": 2,
            "evidence_count": 4,
        },
        "entries": [
            {
                "decision": "pending",
                "code": "topic.agentic-ai",
                "kind": "topic",
                "name_zh": "智能体",
                "name_en": "Agentic AI",
                "aliases": ["AI Agent"],
                "description": "",
                "prompt_description": "",
                "user_selectable": True,
                "suggested_user_selectable": True,
                "source_labels": ["智能体"],
                "rationale": "stable",
                "risk": "",
            }
        ],
    }
    markdown = review_draft.markdown_review(report)
    assert "未激活、未发布、未回填" in markdown
    assert "有样本来源：1 / 31" in markdown
    assert "`topic.agentic-ai`" in markdown
    assert "接受后默认用户可选：是" in markdown
    assert "规范名解析入口：智能体 / Agentic AI" in markdown
    assert "尚未归并的 Candidate（0）" in markdown


def test_review_validation_does_not_silently_truncate_long_tail():
    raw_entries = [
        {
            "code": f"topic.item-{index}",
            "kind": "topic",
            "name_en": f"Item {index}",
            "source_labels": [f"Item {index}"],
        }
        for index in range(60)
    ]
    entries = review_draft.validate_entries(
        {"entries": raw_entries},
        kind="topic",
        source_labels={f"Item {index}" for index in range(60)},
    )
    assert len(entries) == 60


def test_entity_review_draft_carries_a_confirmable_entity_type():
    entries = review_draft.validate_entries(
        {
            "entries": [
                {
                    "code": "entity.mcp",
                    "kind": "entity",
                    "name_en": "MCP",
                    "description": "Model Context Protocol ecosystem concept.",
                    "prompt_description": "Use only when MCP itself is central; do not tag every tool integration.",
                    "entity_type": "protocol",
                    "source_labels": ["MCP"],
                }
            ]
        },
        kind="entity",
        source_labels={"MCP"},
    )
    assert entries[0]["entity_type"] == "protocol"
    assert entries[0]["description"] == "Model Context Protocol ecosystem concept."
    assert entries[0]["prompt_description"].startswith("Use only when MCP itself")
    assert entries[0]["external_key"] is None
    assert entries[0]["user_selectable"] is True


def test_review_entries_include_exact_candidate_ids_and_combined_evidence():
    entries = [{"kind": "topic", "source_labels": ["模型推理", "LLM inference"]}]
    candidates = [
        {
            "candidate_id": 7,
            "label": "模型推理",
            "normalized_label": "模型推理",
            "proposed_kind": "topic",
            "article_ids": ["a", "b"],
            "source_ids": ["official"],
            "evidence_days": ["2026-08-31"],
            "confidence_sum": 1.8,
            "evidence_count": 2,
        },
        {
            "candidate_id": 9,
            "label": "LLM inference",
            "normalized_label": "llm inference",
            "proposed_kind": "topic",
            "article_ids": ["b", "c"],
            "source_ids": ["official", "research"],
            "evidence_days": ["2026-08-31", "2026-09-01"],
            "confidence_sum": 0.8,
            "evidence_count": 1,
        },
    ]
    result = review_draft.attach_evidence_metrics(entries, candidates)[0]
    assert result["source_candidate_ids"] == [7, 9]
    assert [(item["kind"], item["normalized_label"]) for item in result["source_candidates"]] == [
        ("topic", "llm inference"),
        ("topic", "模型推理"),
    ]
    assert result["support_articles_30d"] == 3
    assert result["distinct_sources_30d"] == 2
    assert result["distinct_days_30d"] == 2
    assert result["mean_confidence"] == 0.867
