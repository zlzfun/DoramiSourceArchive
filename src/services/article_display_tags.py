"""Reader-facing article tags: stable canonical concepts plus flexible AI labels.

Canonical assignments remain the authority for durable taxonomy filters,
interests and digest selection. Temporary chip retrieval can also match the
current display projection without promoting it into that authority boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, inspect as sa_inspect, literal_column, or_
from sqlmodel import Session, select

from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
)
from services.taxonomy import normalize_label
from storage.fts import _sql_normalize_expr, normalize_for_search


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
    """Deduplicate and rank tags: canonical first, then flexible labels."""

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
        0 if item.get("type") == "canonical" else 1,
        -float(item.get("score", 0.0)),
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


def article_ids_for_canonical_projection(session: Session, tag_id: int) -> list[str]:
    """Find read-time canonical promotions without materializing assignments.

    Reuse the display resolver for active names/aliases, Candidate governance
    and legacy evidence, so retrieval cannot drift from the chip's identity.
    Durable assignment matching remains an independent SQL predicate.
    """

    article_ids = set(session.exec(
        select(ArticleAnalysisRecord.article_id).where(
            ArticleAnalysisRecord.display_tags_json.notin_(["", "[]"]),
        )
    ).all())
    article_ids.update(session.exec(
        select(CmsTagCandidateEvidenceRecord.article_id).distinct()
    ).all())
    ordered_ids = sorted(article_ids)
    matching_ids: list[str] = []
    for start in range(0, len(ordered_ids), 500):
        current = load_display_tags(session, ordered_ids[start:start + 500])
        matching_ids.extend(
            article_id for article_id, tags in current.items()
            if any(tag.get("type") == "canonical" and tag.get("id") == tag_id for tag in tags)
        )
    return matching_ids


def article_ids_for_flexible_label(session: Session, label: str) -> list[str]:
    """Return articles whose current display projection contains a free label.

    This intentionally excludes canonical tags, which use ``display_tag_id``
    for chip retrieval and ``tag_ids`` for durable assignment filtering. This
    scan is only used for an explicit click on a flexible chip, keeping that
    temporary discovery path separate from taxonomy, interests and digest selection.
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


# ── 搜索框关键词匹配标签（issue #94）──────────────────────────────────────────
# 与「点标签小签检索」（display_tag_id / display_tag，精确身份）不同，这里是关键词
# 检索的第三个字段：一个词命中「标题 / 正文 / 标签」任一即可。标签侧语义 =
# **读者在这篇文章上能看到的标签**含有该词（归一化后子串匹配，与 trigram 的
# 子串语义一致）；规范标签另认其别名（别名是同一概念的写法，不另画小签）。
#
# 不加冗余列、不改表：规范标签走 assignment 的 EXISTS 子查询；读时投影（候选
# 合并/激活、名称/别名解析）与灵活标签先用 SQL 粗筛出候选文章，再用与列表展示
# 同一个 `load_display_tags` 逐篇复核——命中的标签一定是读者看得见的那枚。

_TAG_SEARCH_VERIFY_CHUNK = 500
# 粗筛时把「命中规范标签的名称/别名」也当成 LIKE 词（免得 label 恰好是别名、
# 原文却不含搜索词的投影漏筛）；词太泛（如 2 字母）命中大量标签时只取前若干个。
_TAG_SEARCH_NAME_CAP = 50
_TAG_SEARCH_CANDIDATE_CAP = 200
_TAG_SEARCH_REQUIRED_TABLES = frozenset({
    CmsTagRecord.__tablename__,
    CmsTagAliasRecord.__tablename__,
    CmsTagCandidateRecord.__tablename__,
    CmsTagCandidateEvidenceRecord.__tablename__,
    ArticleTagAssignmentRecord.__tablename__,
    ArticleAnalysisRecord.__tablename__,
})


@dataclass(frozen=True)
class TagTextMatch:
    """一个搜索词在标签侧的命中。

    - ``tag_ids``：名称（中/英）或别名含该词的 **active 规范标签**——调用方据此
      对 durable assignment 做 EXISTS 过滤；
    - ``article_ids``：无 durable assignment、但**读时展示投影**里有命中标签的文章
      （候选已合并/激活到命中标签、名称/别名解析，或灵活标签本身含该词）。
    """

    needle: str
    tag_ids: frozenset[int]
    article_ids: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.tag_ids and not self.article_ids


def _tag_tables_available(session: Session) -> bool:
    """兼容历史迁移库：旧版本尚未建标签表或 display_tags_json 列时跳过标签检索。"""
    try:
        conn = session.connection()
        inspector = sa_inspect(conn)
        tables = set(inspector.get_table_names())
        if not _TAG_SEARCH_REQUIRED_TABLES.issubset(tables):
            return False
        cols = {
            col["name"]
            for col in inspector.get_columns(ArticleAnalysisRecord.__tablename__)
        }
        return "display_tags_json" in cols
    except Exception:  # noqa: BLE001
        return False


def _load_active_canonical_tags(
    session: Session, article_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """批量加载已持久化且仍为 active 的规范标签，供复核展示投影时与列表同口径截断。"""
    if not article_ids:
        return {}
    tags: dict[str, list[dict[str, Any]]] = {article_id: [] for article_id in article_ids}
    rows = session.exec(
        select(ArticleTagAssignmentRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(
            ArticleTagAssignmentRecord.article_id.in_(list(article_ids)),
            CmsTagRecord.status == "active",
        )
        .order_by(
            ArticleTagAssignmentRecord.article_id,
            ArticleTagAssignmentRecord.is_primary.desc(),
            ArticleTagAssignmentRecord.relevance.desc(),
            CmsTagRecord.code,
        )
    ).all()
    for assignment, tag in rows:
        tags.setdefault(assignment.article_id, []).append({
            "id": tag.id,
            "code": tag.code,
            "kind": tag.kind,
            "name_zh": tag.name_zh,
            "name_en": tag.name_en,
            "is_primary": assignment.is_primary,
            "relevance": assignment.relevance,
            "assignment_source": assignment.assignment_source,
        })
    return tags


def _coarse_text_conditions(
    column_expr: Any,
    query_text: str,
    needle: str,
    *,
    raw_json_column: Any = None,
) -> list[Any]:
    """构造 SQL 粗筛条件：宁多勿漏（下一步由 load_display_tags 精确复核）。"""
    conditions: list[Any] = []
    if query_text:
        conditions.append(column_expr.contains(query_text, autoescape=True))
        if raw_json_column is not None and not query_text.isascii():
            # 兼容历史/手工脚本用默认 ensure_ascii=True 写入的 \uXXXX 转义 JSON。
            escaped = json.dumps(query_text, ensure_ascii=True)[1:-1]
            if escaped != query_text:
                conditions.append(raw_json_column.contains(escaped, autoescape=True))
    if needle:
        parts = needle.split()
        if len(parts) > 1:
            # 覆盖标点/连接符差异（如搜索 AI-Agent 匹配标签 AI_Agent 或 AI Agent）
            conditions.append(and_(*[column_expr.contains(part, autoescape=True) for part in parts]))
        elif needle != query_text.casefold():
            # 覆盖 NFKC 全角字符等变体（如搜索 ＡＩ 匹配标签 AI）
            conditions.append(column_expr.contains(needle, autoescape=True))
    return conditions


def display_tag_matches(tag: Mapping[str, Any], match: TagTextMatch) -> bool:
    """展示投影里的一枚标签是否命中（供复核与列表 `search_tag_hits` 共用）。"""

    if not match.needle:
        return False
    tag_id = tag.get("id")
    if tag.get("type") != "extracted" and tag_id is not None:
        try:
            # 已落库的规范标签以 active 命中集合为准，避免退役/停用标签误命中。
            return int(tag_id) in match.tag_ids
        except (TypeError, ValueError):
            pass
    return any(
        match.needle in normalize_label(str(value))
        for value in (tag.get("label"), tag.get("name_zh"), tag.get("name_en"))
        if value
    )


def match_tags_by_text(session: Session, term: str) -> TagTextMatch:
    """把一个搜索词解析成标签侧命中（见 :class:`TagTextMatch`）。"""

    needle = normalize_label(term or "")
    if not needle or not _tag_tables_available(session):
        return TagTextMatch("", frozenset(), ())

    # 1) 规范标签：名称或别名（归一化后）含搜索词。
    names_by_tag: dict[int, set[str]] = {}
    for tag in session.exec(select(CmsTagRecord).where(CmsTagRecord.status == "active")).all():
        if tag.id is None:
            continue
        names_by_tag[int(tag.id)] = {value for value in (tag.name_zh, tag.name_en) if value}
    for alias, tag_id in session.exec(
        select(CmsTagAliasRecord.alias, CmsTagAliasRecord.tag_id)
        .join(CmsTagRecord, CmsTagRecord.id == CmsTagAliasRecord.tag_id)
        .where(CmsTagRecord.status == "active")
    ).all():
        if alias:
            names_by_tag.setdefault(int(tag_id), set()).add(alias)
    matched_tag_ids = frozenset(
        tag_id for tag_id, names in names_by_tag.items()
        if any(needle in normalize_label(name) for name in names)
    )
    match = TagTextMatch(needle, matched_tag_ids, ())

    # 2) 读时投影 / 灵活标签：SQL 粗筛候选文章（宁多勿漏，下一步复核）。
    query_text = normalize_for_search((term or "").strip()) or ""
    resolving_names = sorted({
        name for tag_id in matched_tag_ids for name in names_by_tag.get(tag_id, ())
    })[:_TAG_SEARCH_NAME_CAP]
    resolved_candidate_ids: list[int] = []
    if matched_tag_ids:
        resolved_candidate_ids = [
            int(value) for value in session.exec(
                select(CmsTagCandidateRecord.id).where(
                    CmsTagCandidateRecord.status.in_(("merged", "activated")),
                    CmsTagCandidateRecord.resolution_tag_id.in_(sorted(matched_tag_ids)),
                ).limit(_TAG_SEARCH_CANDIDATE_CAP)
            ).all()
            if value is not None
        ]

    # SQLite 下按 trigger 同一条 REPLACE 链归一化后再 LIKE，排版变体与 FTS 一致
    # （如 AI‑Native ↔ AI-Native）；非 SQLite 方言直接对原列 LIKE。
    is_sqlite = session.connection().dialect.name == "sqlite"
    snapshot_expr = (
        literal_column(_sql_normalize_expr(f"{ArticleAnalysisRecord.__tablename__}.display_tags_json"))
        if is_sqlite
        else ArticleAnalysisRecord.display_tags_json
    )
    evidence_expr = (
        literal_column(_sql_normalize_expr(f"{CmsTagCandidateEvidenceRecord.__tablename__}.raw_label"))
        if is_sqlite
        else CmsTagCandidateEvidenceRecord.raw_label
    )

    snapshot_conditions = _coarse_text_conditions(
        snapshot_expr,
        query_text,
        needle,
        raw_json_column=ArticleAnalysisRecord.display_tags_json,
    )
    for name in resolving_names:
        norm_name = normalize_for_search(name) or name
        snapshot_conditions.append(snapshot_expr.contains(norm_name, autoescape=True))
        if not name.isascii():
            escaped = json.dumps(name, ensure_ascii=True)[1:-1]
            if escaped != name:
                snapshot_conditions.append(
                    ArticleAnalysisRecord.display_tags_json.contains(escaped, autoescape=True)
                )
    for cid in resolved_candidate_ids:
        # 兼容默认带空格与紧凑 JSON 两种序列化形态，且不依赖键顺序。
        snapshot_conditions.append(
            ArticleAnalysisRecord.display_tags_json.contains(f'"candidate_id": {cid}', autoescape=True)
        )
        snapshot_conditions.append(
            ArticleAnalysisRecord.display_tags_json.contains(f'"candidate_id":{cid}', autoescape=True)
        )

    evidence_conditions = _coarse_text_conditions(evidence_expr, query_text, needle)
    evidence_conditions += [
        evidence_expr.contains(normalize_for_search(name) or name, autoescape=True)
        for name in resolving_names
    ]
    if resolved_candidate_ids:
        evidence_conditions.append(CmsTagCandidateEvidenceRecord.candidate_id.in_(resolved_candidate_ids))

    candidates: set[str] = set()
    if snapshot_conditions:
        candidates.update(session.exec(
            select(ArticleAnalysisRecord.article_id).where(
                ArticleAnalysisRecord.display_tags_json.notin_(["", "[]"]),
                or_(*snapshot_conditions),
            )
        ).all())
    if evidence_conditions:
        candidates.update(session.exec(
            select(CmsTagCandidateEvidenceRecord.article_id).where(or_(*evidence_conditions)).distinct()
        ).all())
    if not candidates:
        return match

    # 3) 复核：与列表展示同一个解析器（尊重候选 rejected/deleted/merged 治理状态
    #    与展示上限），只留下展示投影里真有命中标签的文章。
    ordered = sorted(candidates)
    verified: list[str] = []
    for start in range(0, len(ordered), _TAG_SEARCH_VERIFY_CHUNK):
        chunk = ordered[start:start + _TAG_SEARCH_VERIFY_CHUNK]
        chunk_canonical = _load_active_canonical_tags(session, chunk)
        current = load_display_tags(session, chunk, canonical_tags=chunk_canonical)
        verified.extend(
            article_id for article_id, tags in current.items()
            if any(display_tag_matches(tag, match) for tag in tags)
        )
    return TagTextMatch(needle, matched_tag_ids, tuple(verified))


def tag_match_condition(match: TagTextMatch):
    """将单个词的标签侧命中编译为 SQL 谓词（durable assignment EXISTS ∪ 读时展示投影 ID）。"""
    parts = []
    if match.tag_ids:
        parts.append(exists(select(1).where(
            ArticleTagAssignmentRecord.article_id == ArticleRecord.id,
            ArticleTagAssignmentRecord.tag_id.in_(sorted(match.tag_ids)),
        )))
    if match.article_ids:
        parts.append(ArticleRecord.id.in_(list(match.article_ids)))
    return or_(*parts) if parts else None


