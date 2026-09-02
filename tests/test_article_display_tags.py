"""Reader display-tag projection tests."""

import json

from sqlmodel import Session

from models.db import ArticleAnalysisRecord, ArticleRecord, CmsTagCandidateRecord
from services.article_display_tags import (
    article_ids_for_flexible_label,
    load_display_tags,
    rank_display_tags,
)
from services.taxonomy import create_tag, delete_candidate
from storage.impl.db_storage import DatabaseStorage


STAMP = "2026-09-02T12:00:00+08:00"


def test_rank_display_tags_keeps_primary_first_and_caps_at_six():
    canonical = [
        {"code": "topic.primary", "kind": "topic", "name_zh": "主标签", "is_primary": True, "relevance": 0.4},
        {"code": "industry.ai", "kind": "industry", "name_zh": "人工智能", "is_primary": False, "relevance": 0.95},
    ]
    extracted = [
        {"label": "人工智能", "kind": "industry", "confidence": 0.99},  # canonical duplicate
        *[
            {"label": f"自由标签 {index}", "kind": "topic", "confidence": 0.9 - index / 100}
            for index in range(7)
        ],
    ]

    result = rank_display_tags(canonical, extracted)

    assert len(result) == 6
    assert result[0]["code"] == "topic.primary"
    assert result[1]["code"] == "industry.ai"
    assert [row["type"] for row in result[:2]] == ["canonical", "canonical"]
    assert sum(row["label"] == "人工智能" for row in result) == 1
    assert [row["label"] for row in result[2:]] == [
        "自由标签 0", "自由标签 1", "自由标签 2", "自由标签 3",
    ]


def test_load_display_tags_honors_merge_reject_and_delete_governance():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            article = ArticleRecord(
                id="display-a",
                title="Display tags",
                content_type="article",
                source_id="public",
                source_url="https://example.com/display",
                publish_date=STAMP,
                fetched_date=STAMP,
                has_content=True,
                content="body",
            )
            target = create_tag(
                session,
                code="topic.security",
                kind="topic",
                name_zh="AI 安全",
                status="active",
            )
            session.add(article)
            session.flush()
            candidates = []
            for label, status in (("模型安全", "merged"), ("营销噪声", "rejected"), ("坏标签", "candidate")):
                row = CmsTagCandidateRecord(
                    label=label,
                    normalized_label=label,
                    proposed_kind="topic",
                    status=status,
                    resolution_tag_id=target.id if status == "merged" else None,
                    first_seen_at=STAMP,
                    last_seen_at=STAMP,
                    created_at=STAMP,
                    updated_at=STAMP,
                )
                session.add(row)
                session.flush()
                candidates.append(row)
            session.add(ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                display_tags_json=json.dumps([
                    {"candidate_id": candidates[0].id, "label": "模型安全", "kind": "topic", "confidence": 0.92},
                    {"candidate_id": candidates[1].id, "label": "营销噪声", "kind": "topic", "confidence": 0.91},
                    {"candidate_id": candidates[2].id, "label": "坏标签", "kind": "topic", "confidence": 0.90},
                    {"candidate_id": None, "label": "灵活标签", "kind": "topic", "confidence": 0.89},
                ], ensure_ascii=False),
                created_at=STAMP,
                updated_at=STAMP,
            ))
            session.commit()
            deleted_id = int(candidates[2].id)
            delete_candidate(
                session,
                deleted_id,
                actor_id="admin",
                reason="低质量测试标签",
            )

            result = load_display_tags(session, [article.id])[article.id]

            assert [(row["type"], row["label"]) for row in result] == [
                ("canonical", "AI 安全"),
                ("extracted", "灵活标签"),
            ]
            assert article_ids_for_flexible_label(session, " 灵活标签 ") == [article.id]
            assert article_ids_for_flexible_label(session, "模型安全") == []
            assert article_ids_for_flexible_label(session, "营销噪声") == []
    finally:
        storage.engine.dispose()
