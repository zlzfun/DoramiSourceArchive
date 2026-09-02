"""Open extraction contract tests for taxonomy-bootstrap-v1."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from config import LLMConfig
from llm.taxonomy_bootstrap_prompt import (
    MAX_BOOTSTRAP_BODY_CHARS,
    build_taxonomy_bootstrap_user_prompt,
)
from services.taxonomy_bootstrap_extraction import (
    BootstrapExtractionArticle,
    extract_bootstrap_proposals,
    parse_bootstrap_extraction_payload,
)


def test_bootstrap_prompt_bounds_body_and_does_not_add_url():
    prompt = build_taxonomy_bootstrap_user_prompt(
        [
            {
                "article_id": "a-1",
                "title": "Agent runtimes",
                "body": "x" * (MAX_BOOTSTRAP_BODY_CHARS + 100),
                "content_type": "article",
                "source_id": "rss_public",
            }
        ],
        structural_labels=("official",),
    )
    assert "x" * MAX_BOOTSTRAP_BODY_CHARS in prompt
    assert "x" * (MAX_BOOTSTRAP_BODY_CHARS + 1) not in prompt
    assert "source_url" not in prompt


def test_bootstrap_payload_filters_invalid_structural_duplicate_and_excess_candidates():
    topics = [
        {
            "label": f"Topic {index}",
            "proposed_kind": "topic",
            "confidence": 0.9,
            "context_excerpt": "evidence",
        }
        for index in range(6)
    ]
    payload = {
        "articles": [
            {
                "article_id": "a-1",
                "candidates": topics
                + [
                    {"label": "Topic 0", "proposed_kind": "topic", "confidence": 0.8},
                    {"label": "official", "proposed_kind": "topic", "confidence": 0.99},
                    {"label": "Finance", "proposed_kind": "industry", "confidence": 1.1},
                    {"label": "OpenAI", "proposed_kind": "entity", "confidence": 0.95},
                ],
            },
            {
                "article_id": "outside",
                "candidates": [
                    {"label": "Ignored", "proposed_kind": "topic", "confidence": 0.9}
                ],
            },
        ]
    }
    proposals = parse_bootstrap_extraction_payload(
        payload,
        allowed_article_ids=("a-1",),
        structural_labels=("official",),
    )
    assert [item.label for item in proposals if item.proposed_kind == "topic"] == [
        "Topic 0",
        "Topic 1",
        "Topic 2",
        "Topic 3",
        "Topic 4",
    ]
    assert [(item.label, item.proposed_kind) for item in proposals if item.proposed_kind == "entity"] == [
        ("OpenAI", "entity")
    ]


def test_bootstrap_extraction_batches_and_returns_review_only_proposals(monkeypatch):
    calls: list[list[str]] = []

    @asynccontextmanager
    async def fake_client_session(_config):
        yield object()

    async def fake_chat_completion(*, messages, **_kwargs):
        envelope = json.loads(messages[1].content.split("\n", 1)[1].rsplit("\n", 1)[0])
        article_ids = [item["article_id"] for item in envelope["articles"]]
        calls.append(article_ids)
        return json.dumps(
            {
                "articles": [
                    {
                        "article_id": article_id,
                        "candidates": [
                            {
                                "label": f"Entity {article_id}",
                                "proposed_kind": "entity",
                                "confidence": 0.9,
                                "context_excerpt": "central entity",
                            }
                        ],
                    }
                    for article_id in article_ids
                ]
            }
        )

    monkeypatch.setattr(
        "services.taxonomy_bootstrap_extraction.client_session",
        fake_client_session,
    )
    monkeypatch.setattr(
        "services.taxonomy_bootstrap_extraction.chat_completion",
        fake_chat_completion,
    )
    articles = [
        BootstrapExtractionArticle(
            article_id=f"a-{index}",
            title="Title",
            body="Body",
            content_type="article",
            source_id="rss_public",
        )
        for index in range(3)
    ]
    proposals = asyncio.run(
        extract_bootstrap_proposals(
            articles,
            structural_labels=("official",),
            llm_config=LLMConfig(
                base_url="https://example.test/v1",
                api_key="secret",
                model="test",
            ),
            batch_size=2,
            concurrency=1,
        )
    )
    assert calls == [["a-0", "a-1"], ["a-2"]]
    assert [item.article_id for item in proposals] == ["a-0", "a-1", "a-2"]
    assert all(item.prompt_version == "taxonomy-bootstrap-v1" for item in proposals)
