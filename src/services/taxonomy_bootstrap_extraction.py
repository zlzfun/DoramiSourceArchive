"""Read-only LLM extraction pass for a frozen taxonomy bootstrap manifest."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session

from config import LLMConfig
from llm.client import (
    ChatMessage,
    UsageMeta,
    chat_completion,
    client_session,
    parse_json_object,
)
from llm.taxonomy_bootstrap_prompt import (
    TAXONOMY_BOOTSTRAP_PROMPT_VERSION,
    TAXONOMY_BOOTSTRAP_SYSTEM_PROMPT,
    build_taxonomy_bootstrap_user_prompt,
)
from models.db import ArticleRecord
from services.taxonomy import normalize_label
from services.taxonomy_bootstrap import (
    BootstrapManifest,
    BootstrapProposal,
    bootstrap_source_is_eligible,
    proposal_is_structural,
    validate_manifest,
    validate_manifest_sources,
)


_FACET_LIMITS = {"topic": 5, "industry": 2, "entity": 3}


@dataclass(frozen=True)
class BootstrapExtractionArticle:
    article_id: str
    title: str
    body: str
    content_type: str
    source_id: str

    def prompt_dict(self) -> dict[str, str]:
        return {
            "article_id": self.article_id,
            "title": self.title,
            "body": self.body,
            "content_type": self.content_type,
            "source_id": self.source_id,
        }


def _clean_text(value: object, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def parse_bootstrap_extraction_payload(
    payload: dict[str, Any],
    *,
    allowed_article_ids: Sequence[str],
    structural_labels: Sequence[str],
) -> list[BootstrapProposal]:
    """Validate model output, filter structural labels and enforce facet caps."""

    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError("taxonomy bootstrap output must contain an articles list")
    allowed = set(allowed_article_ids)
    proposals: list[BootstrapProposal] = []
    seen: set[tuple[str, str, str]] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for raw_article in payload["articles"]:
        if not isinstance(raw_article, dict):
            continue
        article_id = _clean_text(raw_article.get("article_id"), max_chars=240)
        candidates = raw_article.get("candidates")
        if article_id not in allowed or not isinstance(candidates, list):
            continue
        for raw in candidates[:20]:
            if not isinstance(raw, dict):
                continue
            label = _clean_text(raw.get("label"), max_chars=120)
            kind = _clean_text(raw.get("proposed_kind"), max_chars=20).casefold()
            if kind not in _FACET_LIMITS or proposal_is_structural(label, structural_labels):
                continue
            try:
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError):
                continue
            if not 0.0 <= confidence <= 1.0:
                continue
            key = (article_id, kind, normalize_label(label))
            count_key = (article_id, kind)
            if not key[2] or key in seen or counts[count_key] >= _FACET_LIMITS[kind]:
                continue
            seen.add(key)
            counts[count_key] += 1
            proposals.append(
                BootstrapProposal(
                    article_id=article_id,
                    label=label,
                    proposed_kind=kind,
                    confidence=confidence,
                    context_excerpt=_clean_text(raw.get("context_excerpt"), max_chars=400),
                    prompt_version=TAXONOMY_BOOTSTRAP_PROMPT_VERSION,
                )
            )
    return proposals


def load_manifest_articles(
    engine: Engine,
    manifest: BootstrapManifest,
    *,
    limit: Optional[int] = None,
) -> list[BootstrapExtractionArticle]:
    """Load only the public articles named by the already validated manifest."""

    validate_manifest(manifest)
    article_ids = manifest.article_ids[: max(0, limit)] if limit is not None else manifest.article_ids
    result: list[BootstrapExtractionArticle] = []
    with Session(engine) as session:
        validate_manifest_sources(session, manifest)
        for article_id in article_ids:
            article = session.get(ArticleRecord, article_id)
            if article is None or not article.has_content or not str(article.content or "").strip():
                raise ValueError(f"manifest article is missing usable content: {article_id}")
            if (
                article.source_id not in manifest.source_ids
                or not bootstrap_source_is_eligible(session, article.source_id)
            ):
                raise ValueError(f"manifest article belongs to an excluded source: {article_id}")
            result.append(
                BootstrapExtractionArticle(
                    article_id=article.id,
                    title=article.title or "",
                    body=article.content or "",
                    content_type=article.content_type or "",
                    source_id=article.source_id or "",
                )
            )
    return result


async def extract_bootstrap_proposals(
    articles: Sequence[BootstrapExtractionArticle],
    *,
    structural_labels: Sequence[str],
    llm_config: LLMConfig,
    batch_size: int = 4,
    concurrency: int = 2,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[BootstrapProposal]:
    """Extract proposals without writing candidates, tags, flags or article state."""

    if not 1 <= batch_size <= 5:
        raise ValueError("taxonomy bootstrap batch_size must be between 1 and 5")
    if not 1 <= concurrency <= 4:
        raise ValueError("taxonomy bootstrap concurrency must be between 1 and 4")
    batches = [list(articles[index : index + batch_size]) for index in range(0, len(articles), batch_size)]
    if not batches:
        return []
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    completed_lock = asyncio.Lock()
    llm_cfg = llm_config.for_aux()

    async with client_session(llm_cfg) as http_client:
        async def extract_one(batch: list[BootstrapExtractionArticle]) -> list[BootstrapProposal]:
            nonlocal completed
            async with semaphore:
                raw = await chat_completion(
                    messages=[
                        ChatMessage(role="system", content=TAXONOMY_BOOTSTRAP_SYSTEM_PROMPT),
                        ChatMessage(
                            role="user",
                            content=build_taxonomy_bootstrap_user_prompt(
                                [article.prompt_dict() for article in batch],
                                structural_labels=structural_labels,
                            ),
                        ),
                    ],
                    config=llm_cfg,
                    temperature=0.1,
                    response_json=True,
                    usage_meta=UsageMeta(purpose="taxonomy_bootstrap", username=None),
                    http_client=http_client,
                )
                parsed = parse_bootstrap_extraction_payload(
                    parse_json_object(raw),
                    allowed_article_ids=[article.article_id for article in batch],
                    structural_labels=structural_labels,
                )
                async with completed_lock:
                    completed += len(batch)
                    if progress:
                        progress(completed, len(articles))
                return parsed

        results = await asyncio.gather(*(extract_one(batch) for batch in batches))
    return [proposal for batch in results for proposal in batch]
