"""Reader-facing article tags: stable canonical concepts plus flexible AI labels.

Canonical assignments remain the only authority for filtering, interests and
digest selection.  This module builds a bounded display projection without
promoting free labels into that authority boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, select

from models.db import (
    ArticleAnalysisRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
)
from services.taxonomy import normalize_label


DISPLAY_TAG_LIMIT = 6
_FACET_ORDER = {"topic": 0, "industry": 1, "entity": 2}


def extracted_tag_snapshot(
    candidates: Sequence[Any],
    candidate_ids: Mapping[tuple[str, str], int] | None = None,
) -> list[dict[str, Any]]:
    """Serialize LLM free labels without making them canonical assignments."""

    ids = candidate_ids or {}
    rows: list[dict[str, Any]] = []
    for item in candidates:
        label = str(getattr(item, "label", "") or "").strip()
        kind = str(getattr(item, "proposed_kind", "") or "")
        if not label or kind not in _FACET_ORDER:
            continue
        key = (kind, normalize_label(label))
        rows.append({
            "candidate_id": ids.get(key),
            "label": label,
            "kind": kind,
            "confidence": float(getattr(item, "confidence", 0.0) or 0.0),
        })
    return rows


def _json_rows(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def rank_display_tags(
    canonical_tags: Sequence[Mapping[str, Any]],
    extracted_tags: Sequence[Mapping[str, Any]],
    *,
    limit: int = DISPLAY_TAG_LIMIT,
) -> list[dict[str, Any]]:
    """Deduplicate and rank a single article's display-only tag projection."""

    canonical: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    seen_labels: set[tuple[str, str]] = set()

    for raw in canonical_tags:
        code = str(raw.get("code") or "").strip()
        kind = str(raw.get("kind") or "")
        label = str(raw.get("name_zh") or raw.get("name_en") or code).strip()
        if not code or not label or code in seen_codes:
            continue
        row = dict(raw)
        row.update({
            "label": label,
            "type": "canonical",
            "score": float(raw.get("relevance", raw.get("score", 0.0)) or 0.0),
        })
        canonical.append(row)
        seen_codes.add(code)
        seen_labels.add((kind, normalize_label(label)))
        for value in (raw.get("name_zh"), raw.get("name_en")):
            if value:
                seen_labels.add((kind, normalize_label(str(value))))

    for raw in extracted_tags:
        kind = str(raw.get("kind") or raw.get("proposed_kind") or "")
        label = str(raw.get("label") or "").strip()
        identity = (kind, normalize_label(label))
        if not label or kind not in _FACET_ORDER or identity in seen_labels:
            continue
        row = {
            "candidate_id": raw.get("candidate_id"),
            "label": label,
            "kind": kind,
            "type": "extracted",
            "score": float(raw.get("confidence", raw.get("score", 0.0)) or 0.0),
        }
        extracted.append(row)
        seen_labels.add(identity)

    primary = sorted(
        (item for item in canonical if item.get("is_primary")),
        key=lambda item: (-item["score"], _FACET_ORDER.get(str(item.get("kind")), 9), str(item.get("code"))),
    )
    primary_ids = {str(item.get("code")) for item in primary}
    remainder = [item for item in canonical if str(item.get("code")) not in primary_ids] + extracted
    remainder.sort(key=lambda item: (
        -float(item.get("score", 0.0)),
        0 if item.get("type") == "canonical" else 1,
        _FACET_ORDER.get(str(item.get("kind")), 9),
        normalize_label(str(item.get("label") or "")),
    ))
    return (primary + remainder)[: max(0, int(limit))]


def load_display_tags(
    session: Session,
    article_ids: Sequence[str],
    *,
    analyses: Mapping[str, ArticleAnalysisRecord] | None = None,
    canonical_tags: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Batch-build current display tags, honoring Candidate governance state."""

    ids = list(dict.fromkeys(str(value) for value in article_ids if value))
    if not ids:
        return {}
    analysis_map = dict(analyses or {})
    if not analyses:
        analysis_map = {
            row.article_id: row
            for row in session.exec(
                select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(ids))
            ).all()
        }

    stored: dict[str, list[dict[str, Any]]] = {
        article_id: _json_rows(getattr(analysis_map.get(article_id), "display_tags_json", "[]"))
        for article_id in ids
    }
    has_stored_snapshot = {
        article_id: bool(values) for article_id, values in stored.items()
    }
    # Existing analyses predate display_tags_json. Public Candidate evidence is
    # a lossless fallback; private-source labels are captured on the next analysis.
    evidence_rows = session.exec(
        select(CmsTagCandidateEvidenceRecord).where(
            CmsTagCandidateEvidenceRecord.article_id.in_(ids)
        )
    ).all()
    for evidence in evidence_rows:
        if has_stored_snapshot.get(evidence.article_id):
            continue
        stored.setdefault(evidence.article_id, []).append({
            "candidate_id": evidence.candidate_id,
            "label": evidence.raw_label,
            "kind": "",
            "confidence": evidence.confidence,
        })

    candidate_ids = {
        int(item["candidate_id"])
        for values in stored.values()
        for item in values
        if item.get("candidate_id") is not None
    }
    candidates = {
        int(row.id): row
        for row in session.exec(
            select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.id.in_(candidate_ids or {-1})
            )
        ).all()
        if row.id is not None
    }
    active_tags = list(
        session.exec(select(CmsTagRecord).where(CmsTagRecord.status == "active")).all()
    )
    active_by_id = {int(row.id): row for row in active_tags if row.id is not None}
    names: dict[tuple[str, str], CmsTagRecord] = {}
    for tag in active_tags:
        for value in (tag.code, tag.name_zh, tag.name_en):
            if value:
                names[(tag.kind, normalize_label(value))] = tag
    for alias, tag in session.exec(
        select(CmsTagAliasRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == CmsTagAliasRecord.tag_id)
        .where(CmsTagRecord.status == "active")
    ).all():
        names[(tag.kind, alias.normalized_alias)] = tag

    result: dict[str, list[dict[str, Any]]] = {}
    for article_id in ids:
        free: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []
        for raw in stored.get(article_id, []):
            candidate_id = raw.get("candidate_id")
            candidate = candidates.get(int(candidate_id)) if candidate_id is not None else None
            if candidate_id is not None and candidate is None:
                continue  # Candidate was deliberately deleted.
            kind = str(raw.get("kind") or (candidate.proposed_kind if candidate else ""))
            label = str(raw.get("label") or (candidate.label if candidate else "")).strip()
            confidence = float(raw.get("confidence", 0.0) or 0.0)
            if candidate is not None:
                if candidate.status == "rejected":
                    continue
                if candidate.status in {"merged", "activated"} and candidate.resolution_tag_id:
                    target = active_by_id.get(int(candidate.resolution_tag_id))
                    if target is not None:
                        resolved.append({
                            "id": target.id,
                            "code": target.code,
                            "kind": target.kind,
                            "name_zh": target.name_zh,
                            "name_en": target.name_en,
                            "is_primary": False,
                            "relevance": confidence,
                            "assignment_source": "llm",
                        })
                    continue
            target = names.get((kind, normalize_label(label)))
            if target is not None:
                resolved.append({
                    "id": target.id,
                    "code": target.code,
                    "kind": target.kind,
                    "name_zh": target.name_zh,
                    "name_en": target.name_en,
                    "is_primary": False,
                    "relevance": confidence,
                    "assignment_source": "llm",
                })
                continue
            free.append({**raw, "label": label, "kind": kind, "confidence": confidence})
        base = list(canonical_tags.get(article_id, ())) if canonical_tags else []
        result[article_id] = rank_display_tags([*base, *resolved], free)
    return result


def article_ids_for_flexible_label(session: Session, label: str) -> list[str]:
    """Return articles whose current display projection contains a free label.

    This intentionally excludes canonical tags: those already have the durable
    ``tag_ids`` filter contract.  The scan is only used for an explicit click on
    a flexible display chip, so it keeps that temporary discovery path separate
    from taxonomy, interests and digest selection.
    """

    wanted = normalize_label(label)
    if not wanted:
        return []

    candidate_ids: set[str] = set()
    analysis_rows = session.exec(
        select(ArticleAnalysisRecord).where(
            or_(
                ArticleAnalysisRecord.status == "succeeded",
                ArticleAnalysisRecord.analyzed_at.is_not(None),
            )
        )
    ).all()
    analysis_map = {row.article_id: row for row in analysis_rows}
    for row in analysis_rows:
        if any(
            normalize_label(str(item.get("label") or "")) == wanted
            for item in _json_rows(row.display_tags_json)
        ):
            candidate_ids.add(row.article_id)

    # Analyses created before display_tags_json used Candidate evidence as their
    # lossless source.  Include those article ids, then let load_display_tags
    # apply the current merged/rejected/deleted governance state below.
    for evidence in session.exec(select(CmsTagCandidateEvidenceRecord)).all():
        if normalize_label(evidence.raw_label) == wanted:
            candidate_ids.add(evidence.article_id)

    if not candidate_ids:
        return []
    ordered_ids = sorted(candidate_ids)
    current = load_display_tags(
        session,
        ordered_ids,
        analyses={key: value for key, value in analysis_map.items() if key in candidate_ids},
    )
    return [
        article_id
        for article_id in ordered_ids
        if any(
            item.get("type") == "extracted"
            and normalize_label(str(item.get("label") or "")) == wanted
            for item in current.get(article_id, [])
        )
    ]


def build_article_tags_text(session: Session, article_id: str) -> str:
    """聚合单篇文章/播客的所有标签文本（含受控概念、别名与动态提取标签），供检索匹配。"""
    if not article_id:
        return ""
    labels: list[str] = []
    seen: set[str] = set()

    def _add(text: Any):
        if not text:
            return
        clean = str(text).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            labels.append(clean)

    # 1. 规范标签
    canonical_rows = session.exec(
        select(CmsTagRecord, ArticleTagAssignmentRecord.tag_id)
        .join(ArticleTagAssignmentRecord, ArticleTagAssignmentRecord.tag_id == CmsTagRecord.id)
        .where(ArticleTagAssignmentRecord.article_id == article_id)
    ).all()
    tag_ids = []
    for tag_rec, tid in canonical_rows:
        _add(tag_rec.name_zh)
        _add(tag_rec.name_en)
        tag_ids.append(tid)

    # 2. 标签别名
    if tag_ids:
        for alias_row in session.exec(
            select(CmsTagAliasRecord.alias).where(CmsTagAliasRecord.tag_id.in_(tag_ids))
        ).all():
            _add(alias_row)

    # 3. AI 提取展示标签
    analysis = session.exec(
        select(ArticleAnalysisRecord.display_tags_json).where(
            ArticleAnalysisRecord.article_id == article_id
        )
    ).first()
    if analysis:
        for item in _json_rows(analysis):
            _add(item.get("label") or item.get("name"))

    # 4. 候选词证据
    for ev in session.exec(
        select(CmsTagCandidateEvidenceRecord.raw_label).where(
            CmsTagCandidateEvidenceRecord.article_id == article_id
        )
    ).all():
        _add(ev)

    return " ".join(labels)


def sync_article_tags_text(session: Session, article_id: str) -> None:
    """根据最新标签状态重新计算并同步 articles.tags 字段（触发 FTS5 自动更新）。"""
    from models.db import ArticleRecord

    article = session.exec(select(ArticleRecord).where(ArticleRecord.id == article_id)).first()
    if not article:
        return
    new_tags_text = build_article_tags_text(session, article_id)
    if article.tags != new_tags_text:
        article.tags = new_tags_text
        session.add(article)

