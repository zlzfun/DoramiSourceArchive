"""Aggregate, privacy-safe release metrics for analysis/taxonomy/personal digests."""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
from typing import Any, Iterable

from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    TagRetagJobRecord,
)


FEATURE_FLAG_KEYS = (
    "article_analysis_enabled",
    "taxonomy_candidate_enabled",
    "taxonomy_auto_activation_enabled",
    "personal_digest_enabled",
    "public_digest_analysis_adapter_enabled",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(collections.Counter(values).items()))


def _flag_value(record: AppSettingRecord | None) -> bool:
    return bool(
        record
        and str(record.value or "").strip().casefold() in {"1", "true", "yes", "on"}
    )


def collect_release_metrics(
    session: Session,
    *,
    days: int = 7,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return aggregates only; no user, article, source or Candidate evidence text."""

    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    since = (current - dt.timedelta(days=days)).isoformat()
    since_date = (current.date() - dt.timedelta(days=days - 1)).isoformat()

    analyses = list(
        session.exec(
            select(ArticleAnalysisRecord)
            .join(ArticleRecord, ArticleRecord.id == ArticleAnalysisRecord.article_id)
            .where(ArticleRecord.fetched_date >= since)
        ).all()
    )
    succeeded = [row for row in analyses if row.status == "succeeded"]
    scores = [float(row.quality_score) for row in succeeded if row.quality_score is not None]
    histogram = {
        str(bucket): sum(
            1
            for score in scores
            if bucket <= score < bucket + 1 or (bucket == 10 and score == 10)
        )
        for bucket in range(1, 11)
    }
    attempts = list(
        session.exec(
            select(ArticleAnalysisAttemptRecord).where(
                ArticleAnalysisAttemptRecord.created_at >= since
            )
        ).all()
    )
    succeeded_ids = [row.article_id for row in succeeded]
    assignments = (
        list(
            session.exec(
                select(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id.in_(succeeded_ids)
                )
            ).all()
        )
        if succeeded_ids
        else []
    )
    tagged_ids = {row.article_id for row in assignments}
    primary_ids = {row.article_id for row in assignments if row.is_primary}

    tags = list(session.exec(select(CmsTagRecord)).all())
    aliases = list(session.exec(select(CmsTagAliasRecord)).all())
    candidates = list(session.exec(select(CmsTagCandidateRecord)).all())
    retag_jobs = list(session.exec(select(TagRetagJobRecord)).all())

    editions = list(
        session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.report_date >= since_date
            )
        ).all()
    )
    edition_ids = [row.id for row in editions if row.id is not None]
    items = (
        list(
            session.exec(
                select(PersonalDigestItemRecord).where(
                    PersonalDigestItemRecord.edition_id.in_(edition_ids)
                )
            ).all()
        )
        if edition_ids
        else []
    )
    coverage_adjusted = 0
    for item in items:
        try:
            adjustments = json.loads(item.coverage_adjustments_json or "[]")
        except (TypeError, json.JSONDecodeError):
            adjustments = []
        coverage_adjusted += int(bool(adjustments))
    lane_counts = _counts(item.selection_lane for item in items)
    selected_total = sum(lane_counts.values())
    completed = [row for row in editions if row.status in {"ready", "degraded"}]

    flags = {
        key: _flag_value(session.get(AppSettingRecord, key)) for key in FEATURE_FLAG_KEYS
    }
    analyzed_total = len(analyses)
    succeeded_total = len(succeeded)
    return {
        "window_days": days,
        "generated_at": current.isoformat(),
        "feature_flags": flags,
        "article_analysis": {
            "status_counts": _counts(row.status for row in analyses),
            "tagging_status_counts": _counts(row.tagging_status for row in analyses),
            "attempt_status_counts": _counts(row.status for row in attempts),
            "attempt_operation_counts": _counts(row.operation for row in attempts),
            "success_rate": succeeded_total / analyzed_total if analyzed_total else 0.0,
            "score_histogram": histogram,
            "score_p50": _percentile(scores, 0.50),
            "score_p90": _percentile(scores, 0.90),
            "score_threshold_rates": {
                str(threshold): (
                    sum(score >= threshold for score in scores) / len(scores) if scores else 0.0
                )
                for threshold in (7.0, 8.0, 8.5, 9.0)
            },
        },
        "taxonomy": {
            "tag_status_counts": _counts(row.status for row in tags),
            "tag_kind_counts": _counts(row.kind for row in tags),
            "active_automatic_count": sum(
                row.status == "active" and row.activation_mode == "automatic" for row in tags
            ),
            "alias_count": len(aliases),
            "candidate_status_counts": _counts(row.status for row in candidates),
            "retag_status_counts": _counts(row.status for row in retag_jobs),
            "tagged_article_rate": (
                len(tagged_ids) / succeeded_total if succeeded_total else 0.0
            ),
            "primary_missing_rate": (
                (succeeded_total - len(primary_ids)) / succeeded_total
                if succeeded_total
                else 0.0
            ),
        },
        "personal_digest": {
            "edition_status_counts": _counts(row.status for row in editions),
            "degraded_reason_counts": _counts(
                row.degraded_reason for row in editions if row.degraded_reason
            ),
            "selection_lane_counts": lane_counts,
            "interest_ratio": (
                lane_counts.get("interest", 0) / selected_total if selected_total else 0.0
            ),
            "quality_ratio": (
                lane_counts.get("quality", 0) / selected_total if selected_total else 0.0
            ),
            "coverage_adjusted_item_rate": (
                coverage_adjusted / selected_total if selected_total else 0.0
            ),
            "average_items_per_completed_edition": (
                selected_total / len(completed) if completed else 0.0
            ),
        },
    }
