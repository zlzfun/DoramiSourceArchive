"""Deterministic ``taxonomy-bootstrap-v1`` sampling and evidence ingestion.

The bootstrap is intentionally separate from normal collection.  Operators run
the frozen public-source manifest through the existing collectors, then this
module selects a bounded, auditable sample from newly landed and recent content.
It never activates a concept: every proposal becomes Candidate evidence and the
global automatic-activation switch is forced off before processing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session, select

from models.db import ArticleAnalysisRecord, ArticleRecord, SourceConfigRecord
from services.taxonomy import (
    CandidateEvidenceInput,
    activate_taxonomy_version,
    create_taxonomy_version,
    normalize_label,
    queue_retag_job,
    record_candidate_evidence,
    set_auto_activation_enabled,
    taxonomy_coverage_metrics,
)


BOOTSTRAP_ID = "taxonomy-bootstrap-v1"
BOOTSTRAP_LOOKBACK_DAYS = 30
BOOTSTRAP_PER_SOURCE_LIMIT = 15

# This tuple, not the mutable runtime registry, is the v1 operational manifest.
# It deliberately covers official blogs/docs, research, media, newsletters,
# GitHub releases and benchmark sources while excluding X (credentialed), generic
# templates, workflows, private RSS and the synthesized Dorami daily brief.
TAXONOMY_BOOTSTRAP_V1_SOURCE_IDS: tuple[str, ...] = (
    "rss_openai_news",
    "docs_openai_codex_changelog",
    "web_anthropic_news",
    "web_claude_blog",
    "rss_google_gemini_models",
    "rss_deepmind_blog",
    "rss_mistral_news",
    "rss_microsoft_ai_models",
    "web_meta_ai_blog",
    "rss_apple_mlr",
    "rss_nvidia_genai",
    "rss_hf_blog",
    "web_qwen_blog",
    "docs_deepseek_api_changelog",
    "web_bytedance_seed_research",
    "web_huggingface_daily_papers",
    "rss_bair_blog",
    "web_ithome_ai",
    "web_qbitai",
    "rss_the_decoder",
    "rss_hn_ai",
    "rss_reddit_localllama",
    "rss_simonwillison",
    "rss_latent_space",
    "rss_import_ai",
    "web_artificial_analysis",
    "docs_arena_leaderboard_changelog",
    "github_trending_daily",
    "github_opencode_releases",
    "web_kimi_research",
    "web_minimax_research",
)


# Source lifecycle / transport labels are evidence metadata, not content concepts.
STRUCTURAL_LABELS = frozenset(
    normalize_label(value)
    for value in (
        "official",
        "incubating",
        "advanced",
        "general",
        "workflow",
        "webpage",
        "website",
        "rss",
        "atom",
        "github",
        "github release",
        "blog",
        "news",
        "article",
        "social post",
        "tier0 primary",
        "tier1 curated",
        "官方",
        "观察期",
        "网页",
        "网站",
        "博客",
        "新闻",
        "文章",
    )
)


@dataclass(frozen=True)
class BootstrapManifest:
    bootstrap_id: str
    as_of: str
    lookback_days: int
    per_source_limit: int
    source_ids: tuple[str, ...]
    article_ids: tuple[str, ...]
    structural_labels: tuple[str, ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_ids"] = list(self.source_ids)
        result["article_ids"] = list(self.article_ids)
        result["structural_labels"] = list(self.structural_labels)
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(frozen=True)
class BootstrapProposal:
    article_id: str
    label: str
    proposed_kind: str
    confidence: float
    context_excerpt: str = ""
    prompt_version: str = BOOTSTRAP_ID


def _parse_datetime(value: str) -> Optional[dt.datetime]:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _language_bucket(article: ArticleRecord) -> str:
    sample = f"{article.title or ''} {(article.content or '')[:300]}"
    cjk = len(re.findall(r"[\u3400-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    return "zh" if cjk > latin * 0.2 else "other"


def _normalized_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return str(value or "").strip()
    host = parsed.netloc.casefold()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _event_key(article: ArticleRecord) -> str:
    title = normalize_label(article.title)
    # A deterministic lightweight duplicate guard. Event clustering remains a
    # separate shared asset; bootstrap only needs to stop obvious reposts from
    # dominating the initial vocabulary.
    return " ".join(title.split()[:16])


def _round_robin_strata(
    rows: Sequence[tuple[ArticleRecord, Optional[ArticleAnalysisRecord]]],
    limit: int,
    as_of: dt.datetime,
) -> list[ArticleRecord]:
    buckets: dict[tuple[str, str, str], list[ArticleRecord]] = {}
    for article, analysis in rows:
        genre = analysis.content_genre if analysis and analysis.content_genre else "unknown"
        landed_at = _parse_datetime(article.fetched_date) or _parse_datetime(article.publish_date)
        age_bucket = "new" if landed_at and landed_at >= as_of - dt.timedelta(days=1) else "recent"
        buckets.setdefault((age_bucket, _language_bucket(article), genre), []).append(article)
    for values in buckets.values():
        values.sort(key=lambda item: (item.publish_date or item.fetched_date or "", item.id), reverse=True)
    selected: list[ArticleRecord] = []
    keys = sorted(buckets)
    while keys and len(selected) < limit:
        remaining: list[tuple[str, str, str]] = []
        for key in keys:
            values = buckets[key]
            if values and len(selected) < limit:
                selected.append(values.pop(0))
            if values:
                remaining.append(key)
        keys = remaining
    return selected


def bootstrap_source_is_eligible(session: Session, source_id: str) -> bool:
    """Return whether a source is public and concrete enough for bootstrap."""

    if (
        source_id == "dorami_daily_brief"
        or source_id.startswith("generic_")
        or source_id.startswith("user_rss_")
    ):
        return False
    config = session.get(SourceConfigRecord, source_id)
    return not (config and config.owner_username)


def validate_manifest_sources(session: Session, manifest: BootstrapManifest) -> None:
    """Reject a validly hashed manifest that names an ineligible/private source."""

    excluded = [
        source_id
        for source_id in manifest.source_ids
        if not bootstrap_source_is_eligible(session, source_id)
    ]
    if excluded:
        raise ValueError(f"bootstrap manifest contains excluded sources: {excluded}")


def build_bootstrap_manifest(
    session: Session,
    *,
    as_of: dt.datetime,
    source_ids: Sequence[str] = TAXONOMY_BOOTSTRAP_V1_SOURCE_IDS,
    lookback_days: int = BOOTSTRAP_LOOKBACK_DAYS,
    per_source_limit: int = BOOTSTRAP_PER_SOURCE_LIMIT,
) -> BootstrapManifest:
    """Freeze the concrete source and article sample used by bootstrap.

    Repeating with the same database snapshot and ``as_of`` yields the same
    article order and digest.  No collection cursor is modified.
    """

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=dt.timezone.utc)
    if not 10 <= per_source_limit <= 20:
        raise ValueError("bootstrap per-source limit must stay between 10 and 20")
    if not 14 <= lookback_days <= 30:
        raise ValueError("bootstrap lookback must stay between 14 and 30 days")
    frozen_sources = tuple(dict.fromkeys(str(item) for item in source_ids if str(item)))
    private_or_structural = [
        item for item in frozen_sources if not bootstrap_source_is_eligible(session, item)
    ]
    if private_or_structural:
        raise ValueError(f"bootstrap manifest contains excluded sources: {private_or_structural}")

    since = as_of - dt.timedelta(days=lookback_days)
    selected: list[ArticleRecord] = []
    seen_urls: set[str] = set()
    seen_events: set[str] = set()
    for source_id in frozen_sources:
        rows = list(
            session.exec(
                select(ArticleRecord, ArticleAnalysisRecord)
                .join(
                    ArticleAnalysisRecord,
                    ArticleAnalysisRecord.article_id == ArticleRecord.id,
                    isouter=True,
                )
                .where(
                    ArticleRecord.source_id == source_id,
                    ArticleRecord.has_content == True,  # noqa: E712
                )
            ).all()
        )
        eligible: list[tuple[ArticleRecord, Optional[ArticleAnalysisRecord]]] = []
        for article, analysis in rows:
            landed_at = _parse_datetime(article.publish_date) or _parse_datetime(article.fetched_date)
            if landed_at is None or landed_at < since or landed_at > as_of:
                continue
            if not str(article.content or "").strip():
                continue
            url_key = _normalized_url(article.source_url)
            event_key = _event_key(article)
            if url_key in seen_urls or event_key in seen_events:
                continue
            eligible.append((article, analysis))
        for article in _round_robin_strata(eligible, per_source_limit, as_of):
            url_key = _normalized_url(article.source_url)
            event_key = _event_key(article)
            if url_key in seen_urls or event_key in seen_events:
                continue
            seen_urls.add(url_key)
            seen_events.add(event_key)
            selected.append(article)

    payload = {
        "bootstrap_id": BOOTSTRAP_ID,
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "per_source_limit": per_source_limit,
        "source_ids": list(frozen_sources),
        "article_ids": [article.id for article in selected],
        "structural_labels": sorted(STRUCTURAL_LABELS),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return BootstrapManifest(
        bootstrap_id=BOOTSTRAP_ID,
        as_of=as_of.isoformat(),
        lookback_days=lookback_days,
        per_source_limit=per_source_limit,
        source_ids=frozen_sources,
        article_ids=tuple(article.id for article in selected),
        structural_labels=tuple(sorted(STRUCTURAL_LABELS)),
        manifest_sha256=digest,
    )


def validate_manifest(manifest: BootstrapManifest) -> None:
    payload = {
        "bootstrap_id": manifest.bootstrap_id,
        "as_of": manifest.as_of,
        "lookback_days": manifest.lookback_days,
        "per_source_limit": manifest.per_source_limit,
        "source_ids": list(manifest.source_ids),
        "article_ids": list(manifest.article_ids),
        "structural_labels": list(manifest.structural_labels),
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    if manifest.bootstrap_id != BOOTSTRAP_ID or expected != manifest.manifest_sha256:
        raise ValueError("bootstrap manifest is not the frozen taxonomy-bootstrap-v1 manifest")


def proposal_is_structural(label: str, structural_labels: Iterable[str] = STRUCTURAL_LABELS) -> bool:
    normalized = normalize_label(label)
    allowed = set(structural_labels)
    return not normalized or normalized in allowed


def ingest_bootstrap_proposals(
    session: Session,
    *,
    manifest: BootstrapManifest,
    proposals: Iterable[BootstrapProposal | Mapping[str, Any]],
    now: Optional[dt.datetime] = None,
) -> dict[str, int]:
    """Write only Candidate evidence; repeated input remains idempotent."""

    validate_manifest(manifest)
    validate_manifest_sources(session, manifest)
    set_auto_activation_enabled(session, False)
    allowed_articles = set(manifest.article_ids)
    counts = {"accepted": 0, "structural_filtered": 0, "outside_manifest": 0, "known_or_private": 0}
    for raw in proposals:
        proposal = raw if isinstance(raw, BootstrapProposal) else BootstrapProposal(**raw)
        if proposal.article_id not in allowed_articles:
            counts["outside_manifest"] += 1
            continue
        if proposal_is_structural(proposal.label, manifest.structural_labels):
            counts["structural_filtered"] += 1
            continue
        article = session.get(ArticleRecord, proposal.article_id)
        if (
            article is None
            or article.source_id not in manifest.source_ids
            or not bootstrap_source_is_eligible(session, article.source_id)
        ):
            counts["known_or_private"] += 1
            continue
        config = session.get(SourceConfigRecord, article.source_id)
        owner_or_domain = ""
        if config:
            owner_or_domain = config.source_owner or urlsplit(config.url or config.base_url).netloc
        candidate = record_candidate_evidence(
            session,
            CandidateEvidenceInput(
                article_id=article.id,
                source_id=article.source_id,
                label=proposal.label,
                proposed_kind=proposal.proposed_kind,
                confidence=proposal.confidence,
                source_owner_or_domain=owner_or_domain or urlsplit(article.source_url).netloc or article.source_id,
                published_date=article.publish_date,
                context_excerpt=proposal.context_excerpt,
                prompt_version=proposal.prompt_version,
            ),
            is_private=False,
            now=now,
        )
        if candidate is None:
            counts["known_or_private"] += 1
        else:
            counts["accepted"] += 1
    return counts


def publish_taxonomy_v1(
    session: Session,
    *,
    actor_id: str,
    now: dt.datetime,
    change_summary: str = "Reviewed taxonomy-bootstrap-v1 catalog",
) -> dict[str, Any]:
    """Explicit post-review publish step plus seven-day closed-set retag queue.

    Calling this function is a human governance decision; bootstrap ingestion
    never calls it.  Repeated calls reuse version 1 and its existing bootstrap
    retag job, so an operator retry cannot create parallel work.
    """

    from models.db import TagRetagJobRecord, TaxonomyVersionRecord

    active = session.exec(
        select(TaxonomyVersionRecord).where(TaxonomyVersionRecord.status == "active")
    ).first()
    if active is not None and active.version != 1:
        raise ValueError(f"cannot reactivate taxonomy v1 while v{active.version} is active")
    version = session.get(TaxonomyVersionRecord, 1)
    if version is None:
        version = create_taxonomy_version(session, change_summary=change_summary, now=now)
    if version.version != 1:
        raise ValueError("taxonomy-bootstrap-v1 can only publish taxonomy version 1")
    if version.status != "active":
        version = activate_taxonomy_version(session, 1, actor_id=actor_id, now=now)
    since = (now - dt.timedelta(days=7)).isoformat()
    jobs = list(
        session.exec(
            select(TagRetagJobRecord).where(TagRetagJobRecord.taxonomy_version == 1)
        ).all()
    )
    job = next(
        (
            item
            for item in jobs
            if json.loads(item.scope_json or "{}").get("bootstrap_id") == BOOTSTRAP_ID
        ),
        None,
    )
    if job is None:
        job = queue_retag_job(
            session,
            taxonomy_version=1,
            scope={"bootstrap_id": BOOTSTRAP_ID, "closed_set": True, "since": since},
            now=now,
        )
    return {
        "taxonomy_version": version.version,
        "retag_job_id": job.id,
        "retag_status": job.status,
        "coverage_before_retag": taxonomy_coverage_metrics(session, since=since),
    }
