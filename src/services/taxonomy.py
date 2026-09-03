"""CMS taxonomy domain service.

This module owns the invariants which are deliberately stronger than the table
constraints: names and aliases resolve within a facet, only active concepts may
be assigned, one article has one display-primary concept, Candidate frequency is
derived from idempotent evidence, and governance operations always leave an
audit event.

The functions accept an existing :class:`sqlmodel.Session`.  Callers therefore
reuse ``DatabaseStorage.engine`` (WAL, busy timeout and foreign keys) instead of
creating a second SQLite engine.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, select

from models.analysis_contracts import (
    AnalysisOperation,
    ArticleTagAssignmentDTO,
    RetagJobStatus,
    TagActivationMode,
    TagAliasType,
    TagAssignmentSource,
    TagCandidateStatus,
    TagEventAction,
    TagKind,
    TagStatus,
)
from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagEventRecord,
    CmsTagRecord,
    TagRetagJobRecord,
    TaxonomyVersionRecord,
    UserInterestTagRecord,
)
from services.article_time import in_time_window, parse_article_time


AUTO_ACTIVATION_SETTING_KEY = "taxonomy_auto_activation_enabled"
AUTO_ACTIVATION_THRESHOLDS_SETTING_KEY = "taxonomy_auto_activation_thresholds"
TAXONOMY_V1_REVIEW_RECEIPT_KEY = "taxonomy_v1_review_receipt"
INTEREST_CATALOG_POLICY_SETTING_KEY = "taxonomy_interest_catalog_policy"
DEFAULT_FACET_LIMITS: Mapping[str, int] = {"topic": 5, "industry": 2, "entity": 3}
PRIMARY_KIND_ORDER: Mapping[str, int] = {"topic": 0, "entity": 1, "industry": 2}
INTEREST_CATALOG_DEFAULT_LIMITS: Mapping[str, int] = {
    "topic": 30,
    "industry": 15,
    "entity": 20,
}
INTEREST_CATALOG_WINDOW_DAYS = 30
INTEREST_CATALOG_MAX_LIMIT = 200
ENTITY_TYPES: tuple[str, ...] = (
    "organization",
    "product",
    "model",
    "protocol",
    "project",
)


class TaxonomyError(ValueError):
    """A domain invariant was violated."""


class AmbiguousTagError(TaxonomyError):
    """A label resolves to more than one facet and the caller omitted ``kind``."""


@dataclass(frozen=True)
class AutoActivationThresholds:
    """Conservative defaults; production may inject values from configuration."""

    support_article_count_7d: int = 10
    distinct_source_count_7d: int = 3
    distinct_day_count_7d: int = 2
    mean_confidence: float = 0.90
    nearest_active_similarity: float = 0.85


@dataclass(frozen=True)
class CandidateEvidenceInput:
    article_id: str
    source_id: str
    label: str
    proposed_kind: str
    confidence: float
    source_owner_or_domain: str = ""
    published_date: str = ""
    context_excerpt: str = ""
    prompt_version: str = ""


def now_iso(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).isoformat()


def normalize_label(value: str) -> str:
    """NFKC/case/space/punctuation/hyphen normalization for exact lookup.

    Letters and numbers from every script are preserved.  Separators and
    punctuation collapse to one ASCII space, which makes ``AI-Agent``,
    ``ＡＩ Agent`` and ``ai_agent`` resolve consistently without performing any
    semantic or embedding based merge.
    """

    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    pieces: list[str] = []
    pending_space = False
    for char in value:
        category = unicodedata.category(char)
        if char.isspace() or char in "-_‐‑‒–—―/\\" or category.startswith("P"):
            pending_space = bool(pieces)
            continue
        if category.startswith("C"):
            continue
        if pending_space:
            pieces.append(" ")
            pending_space = False
        pieces.append(char)
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _validate_kind(kind: str) -> str:
    kind = _enum_value(kind)
    if kind not in {item.value for item in TagKind}:
        raise TaxonomyError(f"unsupported taxonomy facet: {kind!r}")
    return kind


def validate_entity_type(value: str, *, required: bool = False) -> str:
    entity_type = str(value or "").strip().lower()
    if required and not entity_type:
        raise TaxonomyError("entity tags require entity_type")
    if entity_type and entity_type not in ENTITY_TYPES:
        raise TaxonomyError(
            f"unsupported entity_type: {entity_type!r}; expected one of {', '.join(ENTITY_TYPES)}"
        )
    return entity_type


def _json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _follow_replacement(session: Session, tag: CmsTagRecord) -> CmsTagRecord:
    seen: set[int] = set()
    while tag.status in {TagStatus.MERGED.value, TagStatus.DEPRECATED.value} and tag.replacement_id:
        if tag.id is None or tag.id in seen:
            raise TaxonomyError("taxonomy replacement cycle detected")
        seen.add(tag.id)
        replacement = session.get(CmsTagRecord, tag.replacement_id)
        if replacement is None:
            break
        tag = replacement
    return tag


def resolve_tag(
    session: Session,
    value: str,
    *,
    kind: Optional[str] = None,
    active_only: bool = True,
) -> Optional[CmsTagRecord]:
    """Resolve a code, canonical name or alias, following merge replacements.

    Cross-facet names are legal.  Omitting ``kind`` is only accepted when the
    result is unambiguous.
    """

    raw = str(value or "").strip()
    normalized = normalize_label(raw)
    if not normalized:
        return None
    facet = _validate_kind(kind) if kind is not None else None

    code_query = select(CmsTagRecord).where(CmsTagRecord.code == raw)
    if facet:
        code_query = code_query.where(CmsTagRecord.kind == facet)
    direct = list(session.exec(code_query).all())

    name_query = select(CmsTagRecord).where(CmsTagRecord.normalized_name == normalized)
    alias_query = (
        select(CmsTagRecord)
        .join(CmsTagAliasRecord, CmsTagAliasRecord.tag_id == CmsTagRecord.id)
        .where(CmsTagAliasRecord.normalized_alias == normalized)
    )
    if facet:
        name_query = name_query.where(CmsTagRecord.kind == facet)
        alias_query = alias_query.where(CmsTagAliasRecord.kind == facet)
    matches = direct + list(session.exec(name_query).all()) + list(session.exec(alias_query).all())

    resolved: dict[int, CmsTagRecord] = {}
    for match in matches:
        canonical = _follow_replacement(session, match)
        if active_only and canonical.status != TagStatus.ACTIVE.value:
            continue
        if canonical.id is not None:
            resolved[canonical.id] = canonical
    if len(resolved) > 1:
        raise AmbiguousTagError(f"{value!r} exists in multiple taxonomy facets; pass kind")
    return next(iter(resolved.values()), None)


def current_taxonomy_version(session: Session) -> int:
    active = session.exec(
        select(TaxonomyVersionRecord).where(TaxonomyVersionRecord.status == "active")
    ).first()
    return int(active.version) if active else 0


def _event(
    session: Session,
    action: str,
    *,
    source_tag_id: Optional[int] = None,
    target_tag_id: Optional[int] = None,
    actor_type: str = "user",
    actor_id: str = "",
    reason: str = "",
    payload: Optional[Mapping[str, Any]] = None,
    now: Optional[dt.datetime] = None,
) -> CmsTagEventRecord:
    if action not in {item.value for item in TagEventAction}:
        raise TaxonomyError(f"unsupported taxonomy event: {action!r}")
    if actor_type not in {"user", "system"}:
        raise TaxonomyError("actor_type must be user or system")
    record = CmsTagEventRecord(
        action=action,
        source_tag_id=source_tag_id,
        target_tag_id=target_tag_id,
        actor_type=actor_type,
        actor_id=str(actor_id or ""),
        reason=str(reason or ""),
        payload_json=json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True),
        created_at=now_iso(now),
    )
    session.add(record)
    session.flush()
    return record


def add_alias(
    session: Session,
    *,
    tag_id: int,
    alias: str,
    alias_type: str = "synonym",
    locale: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagAliasRecord:
    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    normalized = normalize_label(alias)
    if not normalized:
        raise TaxonomyError("alias must not be empty")
    alias_type = _enum_value(alias_type)
    if alias_type not in {item.value for item in TagAliasType}:
        raise TaxonomyError("unsupported alias type")
    canonical_conflict = session.exec(
        select(CmsTagRecord).where(
            CmsTagRecord.kind == tag.kind,
            CmsTagRecord.normalized_name == normalized,
            CmsTagRecord.id != tag_id,
        )
    ).first()
    conflict_resolves_to_tag = (
        canonical_conflict is not None
        and canonical_conflict.status in {"merged", "deprecated"}
        and canonical_conflict.replacement_id == tag_id
    )
    if canonical_conflict and not conflict_resolves_to_tag:
        raise TaxonomyError("alias conflicts with another canonical tag name in this facet")
    existing = session.exec(
        select(CmsTagAliasRecord).where(
            CmsTagAliasRecord.kind == tag.kind,
            CmsTagAliasRecord.normalized_alias == normalized,
        )
    ).first()
    if existing:
        if existing.tag_id != tag_id:
            raise TaxonomyError("alias already belongs to another tag in this facet")
        return existing
    stamp = now_iso(now)
    record = CmsTagAliasRecord(
        tag_id=tag_id,
        kind=tag.kind,
        locale=locale,
        alias=str(alias).strip(),
        normalized_alias=normalized,
        alias_type=alias_type,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(record)
    session.flush()
    return record


def _sync_current_name_aliases(
    session: Session,
    tag: CmsTagRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> None:
    """Keep both canonical display languages resolvable within the facet.

    ``normalized_name`` can store only one display form.  The other canonical
    language therefore has to be represented by a translation Alias; otherwise
    a bilingual tag such as ``具身智能 / Embodied AI`` resolves in only one
    language despite both names being present on the record.
    """

    if tag.id is None:
        raise TaxonomyError("tag must be persisted before syncing aliases")
    for locale, value in (("zh", tag.name_zh), ("en", tag.name_en)):
        if value and normalize_label(value) != tag.normalized_name:
            add_alias(
                session,
                tag_id=int(tag.id),
                alias=value,
                alias_type=TagAliasType.TRANSLATION.value,
                locale=locale,
                now=now,
            )


def create_alias(
    session: Session,
    *,
    tag_id: int,
    alias: str,
    actor_id: str,
    alias_type: str = "synonym",
    locale: str = "",
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagAliasRecord:
    record = add_alias(
        session,
        tag_id=tag_id,
        alias=alias,
        alias_type=alias_type,
        locale=locale,
        now=now,
    )
    _event(
        session,
        TagEventAction.RENAME.value,
        source_tag_id=tag_id,
        actor_id=actor_id,
        reason=reason,
        payload={"operation": "alias_add", "alias_id": record.id, "alias": record.alias},
        now=now,
    )
    session.commit()
    session.refresh(record)
    return record


def delete_alias(
    session: Session,
    *,
    tag_id: int,
    alias_id: int,
    actor_id: str,
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> None:
    tag = session.get(CmsTagRecord, tag_id)
    record = session.get(CmsTagAliasRecord, alias_id)
    if tag is None or record is None or record.tag_id != tag_id:
        raise TaxonomyError("alias does not exist on this tag")
    canonical_alternates = {
        normalize_label(value)
        for value in (tag.name_zh, tag.name_en)
        if value and normalize_label(value) != tag.normalized_name
    }
    if record.normalized_alias in canonical_alternates:
        raise TaxonomyError("canonical translation alias cannot be deleted; rename the tag instead")
    payload = {"operation": "alias_delete", "alias_id": record.id, "alias": record.alias}
    session.delete(record)
    _event(
        session,
        TagEventAction.RENAME.value,
        source_tag_id=tag_id,
        actor_id=actor_id,
        reason=reason,
        payload=payload,
        now=now,
    )
    session.commit()


def _validated_parent_id(
    session: Session,
    *,
    kind: str,
    parent_id: Optional[int],
    tag_id: Optional[int] = None,
) -> Optional[int]:
    if parent_id is None:
        return None
    parent = session.get(CmsTagRecord, parent_id)
    if parent is None or parent.kind != kind:
        raise TaxonomyError("parent tag must exist in the same facet")
    if tag_id is not None and parent.id == tag_id:
        raise TaxonomyError("tag cannot be its own parent")
    seen: set[int] = set()
    current = parent
    while current.parent_id is not None:
        if current.id is not None:
            if current.id in seen:
                raise TaxonomyError("taxonomy parent cycle detected")
            seen.add(current.id)
        if tag_id is not None and current.parent_id == tag_id:
            raise TaxonomyError("taxonomy parent cycle detected")
        next_parent = session.get(CmsTagRecord, current.parent_id)
        if next_parent is None:
            break
        current = next_parent
    return int(parent_id)


def create_tag(
    session: Session,
    *,
    code: str,
    kind: str,
    name_zh: str = "",
    name_en: str = "",
    description: str = "",
    prompt_description: str = "",
    status: str = "draft",
    user_selectable: bool = False,
    filterable: bool = True,
    recommendable: bool = True,
    activation_mode: str = "manual",
    entity_type: str = "",
    external_key: Optional[str] = None,
    parent_id: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    facet = _validate_kind(kind)
    status_value = _enum_value(status)
    code = str(code or "").strip()
    if not code or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", code):
        raise TaxonomyError("tag code must be a stable lowercase slug")
    display = str(name_zh or name_en or code).strip()
    normalized = normalize_label(display)
    if not normalized:
        raise TaxonomyError("tag display name must not be empty")
    alias_conflict = session.exec(
        select(CmsTagAliasRecord).where(
            CmsTagAliasRecord.kind == facet,
            CmsTagAliasRecord.normalized_alias == normalized,
        )
    ).first()
    if alias_conflict:
        raise TaxonomyError("tag name conflicts with an existing alias in this facet")
    if facet != TagKind.ENTITY.value and (entity_type or external_key):
        raise TaxonomyError("entity_type/external_key are only valid for entity tags")
    validated_entity_type = (
        validate_entity_type(entity_type, required=status_value == TagStatus.ACTIVE.value)
        if facet == TagKind.ENTITY.value
        else ""
    )
    stamp = now_iso(now)
    record = CmsTagRecord(
        code=code,
        kind=facet,
        name_zh=str(name_zh or "").strip(),
        name_en=str(name_en or "").strip(),
        normalized_name=normalized,
        description=str(description or "").strip(),
        prompt_description=str(prompt_description or "").strip(),
        status=status_value,
        entity_type=validated_entity_type,
        external_key=str(external_key).strip() if external_key else None,
        parent_id=_validated_parent_id(session, kind=facet, parent_id=parent_id),
        user_selectable=bool(user_selectable),
        filterable=bool(filterable),
        recommendable=bool(recommendable),
        activation_mode=_enum_value(activation_mode),
        taxonomy_version=current_taxonomy_version(session),
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(record)
    session.flush()
    _sync_current_name_aliases(session, record, now=now)
    return record


def assign_article_tags(
    session: Session,
    *,
    article_id: str,
    assignments: Iterable[ArticleTagAssignmentDTO | Mapping[str, Any]],
    assignment_source: str = "llm",
    prompt_version: str = "",
    taxonomy_version: Optional[int] = None,
    primary_tag_code: Optional[str] = None,
    facet_limits: Mapping[str, int] = DEFAULT_FACET_LIMITS,
    commit: bool = True,
    now: Optional[dt.datetime] = None,
) -> list[ArticleTagAssignmentRecord]:
    """Validate and upsert active assignments, preserving manual overrides.

    The database permits one primary *per facet* as a corruption guard; the
    service applies the product rule of one display-primary across all facets.
    """

    if session.get(ArticleRecord, article_id) is None:
        raise TaxonomyError("article does not exist")
    source = _enum_value(assignment_source)
    if source not in {item.value for item in TagAssignmentSource}:
        raise TaxonomyError("invalid assignment source")

    validated: dict[int, tuple[CmsTagRecord, float, bool]] = {}
    per_facet: dict[str, int] = {kind: 0 for kind in DEFAULT_FACET_LIMITS}
    for item in assignments:
        dto = item if isinstance(item, ArticleTagAssignmentDTO) else ArticleTagAssignmentDTO(**item)
        tag = resolve_tag(session, dto.code, kind=_enum_value(dto.kind), active_only=True)
        if tag is None or tag.id is None:
            raise TaxonomyError(f"unknown or inactive tag: {dto.code!r}")
        if tag.kind != _enum_value(dto.kind):
            raise TaxonomyError(f"tag facet mismatch for {dto.code!r}")
        previous = validated.get(tag.id)
        candidate = (tag, float(dto.relevance), bool(dto.is_primary))
        if previous is None or candidate[1] > previous[1]:
            validated[tag.id] = candidate

    for tag, _, _ in validated.values():
        per_facet[tag.kind] = per_facet.get(tag.kind, 0) + 1
    for facet, count in per_facet.items():
        limit = int(facet_limits.get(facet, DEFAULT_FACET_LIMITS[facet]))
        if count > limit:
            raise TaxonomyError(f"too many {facet} assignments: {count} > {limit}")

    existing = list(
        session.exec(
            select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id == article_id
            )
        ).all()
    )
    by_tag = {row.tag_id: row for row in existing}
    if source != TagAssignmentSource.MANUAL.value:
        for row in existing:
            if row.tag_id not in validated and row.assignment_source != TagAssignmentSource.MANUAL.value:
                session.delete(row)
                by_tag.pop(row.tag_id, None)
        session.flush()
    stamp = now_iso(now)
    version = current_taxonomy_version(session) if taxonomy_version is None else taxonomy_version
    result: list[ArticleTagAssignmentRecord] = []
    for tag_id, (tag, relevance, _) in validated.items():
        row = by_tag.get(tag_id)
        if row is not None and row.assignment_source == TagAssignmentSource.MANUAL.value and source != "manual":
            result.append(row)
            continue
        if row is None:
            row = ArticleTagAssignmentRecord(
                article_id=article_id,
                tag_id=tag_id,
                tag_kind=tag.kind,
                created_at=stamp,
                updated_at=stamp,
            )
        row.tag_kind = tag.kind
        row.relevance = relevance
        row.assignment_source = source
        row.prompt_version = prompt_version
        row.taxonomy_version = int(version)
        row.updated_at = stamp
        session.add(row)
        result.append(row)

    session.flush()
    all_rows = list(
        session.exec(
            select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id == article_id
            )
        ).all()
    )
    manual_primary = [row for row in all_rows if row.is_primary and row.assignment_source == "manual"]
    if len(manual_primary) > 1:
        raise TaxonomyError("article has multiple manual primary assignments")

    chosen: Optional[ArticleTagAssignmentRecord] = manual_primary[0] if manual_primary else None
    if chosen is None and primary_tag_code:
        primary_tag = resolve_tag(session, primary_tag_code, active_only=True)
        chosen = next((row for row in all_rows if primary_tag and row.tag_id == primary_tag.id), None)
        if chosen is None:
            raise TaxonomyError("primary tag must be one of the article assignments")
    if chosen is None and all_rows:
        chosen = min(
            all_rows,
            key=lambda row: (
                PRIMARY_KIND_ORDER.get(row.tag_kind, 99),
                -float(row.relevance),
                int(row.tag_id),
            ),
        )
    for row in all_rows:
        desired = chosen is not None and row.id == chosen.id
        if row.is_primary != desired:
            row.is_primary = desired
            row.updated_at = stamp
            session.add(row)
    analysis = session.get(ArticleAnalysisRecord, article_id)
    if analysis is not None:
        analysis.primary_tag_id = chosen.tag_id if chosen else None
        analysis.taxonomy_version = int(version)
        analysis.updated_at = stamp
        session.add(analysis)
    if commit:
        session.commit()
    else:
        session.flush()
    return all_rows


def _is_private_source(session: Session, source_id: str) -> bool:
    # Import lazily through a direct query so archived public sources without a
    # SourceConfig row remain public, while every current custom RSS is isolated.
    from models.db import SourceConfigRecord

    record = session.get(SourceConfigRecord, source_id)
    return bool(record and record.owner_username)


def record_candidate_evidence(
    session: Session,
    evidence: CandidateEvidenceInput,
    *,
    is_private: Optional[bool] = None,
    now: Optional[dt.datetime] = None,
) -> Optional[CmsTagCandidateRecord]:
    """Idempotently persist public unknown-label evidence and refresh aggregates.

    Known names/Aliases return ``None`` because they should become an assignment.
    Private RSS unknown terms also return ``None`` and never create a public CMS
    sample row or excerpt.
    """

    facet = _validate_kind(evidence.proposed_kind)
    label = str(evidence.label or "").strip()
    normalized = normalize_label(label)
    if not normalized:
        raise TaxonomyError("candidate label must not be empty")
    confidence = float(evidence.confidence)
    if not 0.0 <= confidence <= 1.0:
        raise TaxonomyError("candidate confidence must be between 0 and 1")
    if session.get(ArticleRecord, evidence.article_id) is None:
        raise TaxonomyError("candidate evidence article does not exist")
    if resolve_tag(session, label, kind=facet, active_only=True) is not None:
        return None
    private = _is_private_source(session, evidence.source_id) if is_private is None else is_private
    if private:
        return None

    stamp = now_iso(now)
    candidate = session.exec(
        select(CmsTagCandidateRecord).where(
            CmsTagCandidateRecord.proposed_kind == facet,
            CmsTagCandidateRecord.normalized_label == normalized,
        )
    ).first()
    if candidate is None:
        candidate = CmsTagCandidateRecord(
            label=label,
            normalized_label=normalized,
            proposed_kind=facet,
            first_seen_at=stamp,
            last_seen_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(candidate)
        session.flush()
    elif candidate.status in {TagCandidateStatus.REJECTED.value, TagCandidateStatus.MERGED.value}:
        # Preserve governance decisions. New evidence remains ignored unless an
        # administrator explicitly returns the candidate to reviewing.
        return candidate

    key = (candidate.id, evidence.article_id)
    row = session.get(CmsTagCandidateEvidenceRecord, key)
    if row is None:
        row = CmsTagCandidateEvidenceRecord(
            candidate_id=int(candidate.id),
            article_id=evidence.article_id,
            source_id=evidence.source_id,
            source_owner_or_domain=str(evidence.source_owner_or_domain or evidence.source_id),
            published_date=str(evidence.published_date or ""),
            confidence=confidence,
            raw_label=label,
            context_excerpt=str(evidence.context_excerpt or "")[:500],
            prompt_version=str(evidence.prompt_version or ""),
            created_at=stamp,
        )
        session.add(row)
    # A retry does not overwrite the first evidence and cannot increase counts.
    candidate.last_seen_at = max(candidate.last_seen_at, stamp)
    candidate.updated_at = stamp
    session.add(candidate)
    session.flush()
    aggregate_candidate(session, int(candidate.id), now=now, commit=False)
    session.commit()
    session.refresh(candidate)
    return candidate


def _evidence_date(row: CmsTagCandidateEvidenceRecord) -> dt.date:
    for raw in (row.published_date, row.created_at):
        try:
            return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
    return dt.date.min


def aggregate_candidate(
    session: Session,
    candidate_id: int,
    *,
    now: Optional[dt.datetime] = None,
    commit: bool = True,
) -> CmsTagCandidateRecord:
    """Recompute 7/30 day materialized statistics solely from evidence rows."""

    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    today = (now or dt.datetime.now(dt.timezone.utc)).date()
    rows = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord).where(
                CmsTagCandidateEvidenceRecord.candidate_id == candidate_id
            )
        ).all()
    )

    def window(days: int) -> list[CmsTagCandidateEvidenceRecord]:
        since = today - dt.timedelta(days=days - 1)
        return [row for row in rows if since <= _evidence_date(row) <= today]

    seven, thirty = window(7), window(30)
    candidate.support_article_count_7d = len({row.article_id for row in seven})
    candidate.support_article_count_30d = len({row.article_id for row in thirty})
    candidate.distinct_source_count_7d = len(
        {row.source_owner_or_domain or row.source_id for row in seven}
    )
    candidate.distinct_source_count_30d = len(
        {row.source_owner_or_domain or row.source_id for row in thirty}
    )
    candidate.distinct_day_count_7d = len({_evidence_date(row) for row in seven})
    candidate.distinct_day_count_30d = len({_evidence_date(row) for row in thirty})
    candidate.mean_confidence = (
        sum(float(row.confidence) for row in thirty) / len(thirty) if thirty else 0.0
    )
    candidate.sample_article_ids_json = json.dumps(
        [row.article_id for row in sorted(thirty, key=lambda item: item.created_at, reverse=True)[:10]],
        ensure_ascii=False,
    )
    candidate.updated_at = now_iso(now)
    session.add(candidate)
    if commit:
        session.commit()
        session.refresh(candidate)
    return candidate


def auto_activation_enabled(session: Session) -> bool:
    record = session.get(AppSettingRecord, AUTO_ACTIVATION_SETTING_KEY)
    return bool(record and str(record.value or "").strip().lower() in {"1", "true", "yes", "on"})


def set_auto_activation_enabled(session: Session, enabled: bool) -> None:
    record = session.get(AppSettingRecord, AUTO_ACTIVATION_SETTING_KEY)
    if record is None:
        record = AppSettingRecord(key=AUTO_ACTIVATION_SETTING_KEY, value="false")
    record.value = "true" if enabled else "false"
    session.add(record)
    session.commit()


def load_auto_activation_thresholds(session: Session) -> AutoActivationThresholds:
    """Load combination thresholds from the backend KV configuration."""

    record = session.get(AppSettingRecord, AUTO_ACTIVATION_THRESHOLDS_SETTING_KEY)
    if record is None or not record.value:
        return AutoActivationThresholds()
    try:
        values = json.loads(record.value)
        thresholds = AutoActivationThresholds(**values)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaxonomyError("taxonomy auto-activation threshold configuration is invalid") from exc
    if (
        thresholds.support_article_count_7d < 1
        or thresholds.distinct_source_count_7d < 1
        or thresholds.distinct_day_count_7d < 1
        or not 0 <= thresholds.mean_confidence <= 1
        or not 0 <= thresholds.nearest_active_similarity <= 1
    ):
        raise TaxonomyError("taxonomy auto-activation thresholds are out of range")
    return thresholds


def load_interest_catalog_policy(session: Session) -> dict[str, Any]:
    """Load the governed reader-interest display policy.

    ``user_selectable`` remains the manual eligibility gate.  These limits only
    choose which eligible concepts are displayed based on recent content heat.
    """

    limits = dict(INTEREST_CATALOG_DEFAULT_LIMITS)
    record = session.get(AppSettingRecord, INTEREST_CATALOG_POLICY_SETTING_KEY)
    if record is not None and record.value:
        try:
            payload = json.loads(record.value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaxonomyError("interest catalog policy is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TaxonomyError("interest catalog policy must be an object")
        configured = payload.get("limits", payload)
        if not isinstance(configured, dict):
            raise TaxonomyError("interest catalog policy limits must be an object")
        for kind in limits:
            if kind not in configured:
                continue
            value = configured[kind]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TaxonomyError(f"interest catalog {kind} limit must be an integer")
            if not 0 <= value <= INTEREST_CATALOG_MAX_LIMIT:
                raise TaxonomyError(
                    f"interest catalog {kind} limit must be between 0 and {INTEREST_CATALOG_MAX_LIMIT}"
                )
            limits[kind] = value
    return {"window_days": INTEREST_CATALOG_WINDOW_DAYS, "limits": limits}


def set_interest_catalog_policy(
    session: Session,
    limits: Mapping[str, int],
    *,
    actor_id: str,
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    expected = set(INTEREST_CATALOG_DEFAULT_LIMITS)
    if set(limits) != expected:
        raise TaxonomyError(f"interest catalog policy requires limits for {sorted(expected)}")
    normalized: dict[str, int] = {}
    for kind in INTEREST_CATALOG_DEFAULT_LIMITS:
        value = limits[kind]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TaxonomyError(f"interest catalog {kind} limit must be an integer")
        if not 0 <= value <= INTEREST_CATALOG_MAX_LIMIT:
            raise TaxonomyError(
                f"interest catalog {kind} limit must be between 0 and {INTEREST_CATALOG_MAX_LIMIT}"
            )
        normalized[kind] = value
    policy = {"window_days": INTEREST_CATALOG_WINDOW_DAYS, "limits": normalized}
    record = session.get(AppSettingRecord, INTEREST_CATALOG_POLICY_SETTING_KEY)
    if record is None:
        record = AppSettingRecord(key=INTEREST_CATALOG_POLICY_SETTING_KEY, value="")
    record.value = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    session.add(record)
    _event(
        session,
        TagEventAction.CHANGE_FLAGS.value,
        actor_id=actor_id,
        reason=reason,
        payload={"operation": "interest_catalog_policy", **policy},
        now=now,
    )
    session.commit()
    return policy


def ranked_interest_catalog(
    session: Session,
    *,
    owner_username: str = "",
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Return eligible tags capped by per-facet recent heat.

    A user's already configured eligible tags are appended when they fall out
    of the current Top N.  This keeps a policy refresh from silently deleting
    explicit interests when the editor next saves.
    """

    policy = load_interest_catalog_policy(session)
    eligible = list(
        session.exec(
            select(CmsTagRecord).where(
                CmsTagRecord.status == TagStatus.ACTIVE.value,
                CmsTagRecord.user_selectable.is_(True),
            )
        ).all()
    )
    eligible_ids = [int(tag.id) for tag in eligible if tag.id is not None]
    current = now or dt.datetime.now(dt.timezone.utc)
    current_utc = parse_article_time(current.isoformat())
    assert current_utc is not None
    since_time = current_utc - dt.timedelta(days=INTEREST_CATALOG_WINDOW_DAYS)
    heat: dict[int, int] = {}
    if eligible_ids:
        coarse_start = (since_time - dt.timedelta(days=1)).date().isoformat()
        heat_rows = session.exec(
            select(
                ArticleTagAssignmentRecord.tag_id,
                ArticleTagAssignmentRecord.article_id,
                ArticleRecord.fetched_date,
            )
            .join(ArticleRecord, ArticleRecord.id == ArticleTagAssignmentRecord.article_id)
            .where(
                ArticleTagAssignmentRecord.tag_id.in_(eligible_ids),
                func.substr(ArticleRecord.fetched_date, 1, 10) >= coarse_start,
            )
        ).all()
        article_ids_by_tag: dict[int, set[str]] = {}
        for tag_id, article_id, fetched_date in heat_rows:
            if in_time_window(fetched_date, start=since_time, end=current_utc):
                article_ids_by_tag.setdefault(int(tag_id), set()).add(str(article_id))
        heat = {tag_id: len(article_ids) for tag_id, article_ids in article_ids_by_tag.items()}
    selected_ids: set[int] = set()
    if owner_username:
        selected_ids = {
            int(tag_id)
            for tag_id in session.exec(
                select(UserInterestTagRecord.tag_id).where(
                    UserInterestTagRecord.owner_username == owner_username
                )
            ).all()
        }
    visible: list[CmsTagRecord] = []
    metadata: dict[int, dict[str, Any]] = {}
    facet_stats: dict[str, dict[str, int]] = {}
    for kind in INTEREST_CATALOG_DEFAULT_LIMITS:
        rows = sorted(
            (tag for tag in eligible if tag.kind == kind and tag.id is not None),
            key=lambda tag: (
                -heat.get(int(tag.id), 0),
                tag.normalized_name,
                int(tag.id),
            ),
        )
        limit = int(policy["limits"][kind])
        top_ids = {int(tag.id) for tag in rows[:limit]}
        chosen = [tag for tag in rows if int(tag.id) in top_ids or int(tag.id) in selected_ids]
        visible.extend(chosen)
        facet_stats[kind] = {
            "eligible_count": len(rows),
            "visible_count": len(chosen),
            "top_n_count": len(top_ids),
        }
        for tag in chosen:
            tag_id = int(tag.id)
            metadata[tag_id] = {
                "heat_30d": heat.get(tag_id, 0),
                "in_top_n": tag_id in top_ids,
                "is_current_interest": tag_id in selected_ids,
            }
    return {
        "tags": visible,
        "metadata": metadata,
        "policy": policy,
        "facet_stats": facet_stats,
    }


def candidate_meets_auto_activation_threshold(
    candidate: CmsTagCandidateRecord,
    thresholds: AutoActivationThresholds,
) -> bool:
    risks = _json_list(candidate.risk_flags_json)
    similarity = candidate.nearest_similarity
    return (
        candidate.status == TagCandidateStatus.CANDIDATE.value
        and candidate.support_article_count_7d >= thresholds.support_article_count_7d
        and candidate.distinct_source_count_7d >= thresholds.distinct_source_count_7d
        and candidate.distinct_day_count_7d >= thresholds.distinct_day_count_7d
        and candidate.mean_confidence >= thresholds.mean_confidence
        and (similarity is None or similarity < thresholds.nearest_active_similarity)
        and not risks
    )


def _candidate_code(
    session: Session,
    candidate: CmsTagCandidateRecord,
    *,
    kind: Optional[str] = None,
) -> str:
    facet = _validate_kind(kind) if kind is not None else candidate.proposed_kind
    slug = normalize_label(candidate.label).replace(" ", "-")
    slug = re.sub(r"[^a-z0-9._-]+", "", slug).strip("-._")
    if not slug:
        slug = f"candidate-{candidate.id}"
    code = f"{facet}.{slug}"
    if session.exec(select(CmsTagRecord).where(CmsTagRecord.code == code)).first() is None:
        return code
    return f"{code}-{candidate.id}"


def activate_candidate(
    session: Session,
    candidate_id: int,
    *,
    code: Optional[str] = None,
    name_zh: str = "",
    name_en: str = "",
    actor_type: str = "user",
    actor_id: str = "",
    reason: str = "",
    automatic: bool = False,
    user_selectable: bool = False,
    entity_type: str = "",
    external_key: Optional[str] = None,
    kind: Optional[str] = None,
    parent_id: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    if candidate.resolution_tag_id:
        existing = session.get(CmsTagRecord, candidate.resolution_tag_id)
        if existing is not None:
            return _follow_replacement(session, existing)
    if candidate.status not in {"candidate", "reviewing"}:
        raise TaxonomyError(f"candidate cannot be activated from {candidate.status}")
    effective_kind = _validate_kind(kind) if kind is not None else candidate.proposed_kind
    if effective_kind == "entity" and automatic and not external_key:
        raise TaxonomyError("automatic entity activation requires a stable external key")
    canonical_zh = str(name_zh or "").strip()
    canonical_en = str(name_en or "").strip()
    if not canonical_zh and not canonical_en:
        if re.search(r"[\u3400-\u9fff]", candidate.label):
            canonical_zh = candidate.label
        else:
            canonical_en = candidate.label
    tag = create_tag(
        session,
        code=code or _candidate_code(session, candidate, kind=effective_kind),
        kind=effective_kind,
        name_zh=canonical_zh,
        name_en=canonical_en,
        status="active",
        user_selectable=user_selectable,
        activation_mode="automatic" if automatic else "manual",
        entity_type=entity_type,
        external_key=external_key,
        parent_id=parent_id,
        now=now,
    )
    if normalize_label(candidate.label) != tag.normalized_name:
        add_alias(session, tag_id=int(tag.id), alias=candidate.label, now=now)
    candidate.status = "activated"
    candidate.resolution_tag_id = tag.id
    candidate.updated_at = now_iso(now)
    session.add(candidate)
    _event(
        session,
        TagEventAction.ACTIVATE.value,
        target_tag_id=tag.id,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        payload={
            "candidate_id": candidate.id,
            "automatic": automatic,
            "user_selectable": user_selectable,
            "proposed_kind": candidate.proposed_kind,
            "activated_kind": effective_kind,
        },
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def reclassify_candidate(
    session: Session,
    candidate_id: int,
    *,
    kind: str,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> CmsTagCandidateRecord:
    """Correct a Candidate facet without losing or duplicating its evidence."""

    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    if candidate.status not in {"candidate", "reviewing"}:
        raise TaxonomyError(f"candidate cannot be reclassified from {candidate.status}")
    facet = _validate_kind(kind)
    if candidate.proposed_kind == facet:
        return candidate
    conflict = session.exec(
        select(CmsTagCandidateRecord).where(
            CmsTagCandidateRecord.proposed_kind == facet,
            CmsTagCandidateRecord.normalized_label == candidate.normalized_label,
            CmsTagCandidateRecord.id != candidate_id,
        )
    ).first()
    if conflict is not None:
        raise TaxonomyError(
            "the corrected facet already has this Candidate; resolve both to an existing tag"
        )
    before = candidate.proposed_kind
    candidate.proposed_kind = facet
    candidate.updated_at = now_iso(now)
    session.add(candidate)
    _event(
        session,
        TagEventAction.CHANGE_FLAGS.value,
        actor_id=actor_id,
        reason=reason,
        payload={
            "operation": "candidate_reclassify",
            "candidate_id": candidate_id,
            "before": before,
            "after": facet,
        },
        now=now,
    )
    session.commit()
    session.refresh(candidate)
    return candidate


def resolve_candidate_to_tag(
    session: Session,
    candidate_id: int,
    *,
    target_tag_id: int,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> CmsTagCandidateRecord:
    """Resolve a duplicate/translation Candidate to an existing active tag."""

    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    target = session.get(CmsTagRecord, target_tag_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    if target is None or target.status != TagStatus.ACTIVE.value:
        raise TaxonomyError("candidate merge target must be an active tag")
    if candidate.status not in {"candidate", "reviewing"}:
        if candidate.resolution_tag_id == target_tag_id:
            return candidate
        raise TaxonomyError(f"candidate cannot be resolved from {candidate.status}")
    if normalize_label(candidate.label) != target.normalized_name:
        add_alias(
            session,
            tag_id=target_tag_id,
            alias=candidate.label,
            alias_type=TagAliasType.TRANSLATION.value
            if candidate.proposed_kind != target.kind
            else TagAliasType.SYNONYM.value,
            now=now,
        )
    candidate.status = TagCandidateStatus.MERGED.value
    candidate.resolution_tag_id = target_tag_id
    candidate.updated_at = now_iso(now)
    session.add(candidate)
    _event(
        session,
        TagEventAction.MERGE.value,
        target_tag_id=target_tag_id,
        actor_id=actor_id,
        reason=reason,
        payload={
            "operation": "candidate_resolve",
            "candidate_id": candidate_id,
            "proposed_kind": candidate.proposed_kind,
            "target_kind": target.kind,
        },
        now=now,
    )
    session.commit()
    session.refresh(candidate)
    return candidate


def maybe_auto_activate_candidate(
    session: Session,
    candidate_id: int,
    *,
    thresholds: Optional[AutoActivationThresholds] = None,
    entity_external_key: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> Optional[CmsTagRecord]:
    """Activate an eligible public candidate, never exposing it to interests."""

    if not auto_activation_enabled(session):
        return None
    candidate = aggregate_candidate(session, candidate_id, now=now)
    configured = thresholds or load_auto_activation_thresholds(session)
    if not candidate_meets_auto_activation_threshold(candidate, configured):
        return None
    if candidate.proposed_kind == "entity" and not entity_external_key:
        return None
    return activate_candidate(
        session,
        candidate_id,
        automatic=True,
        actor_type="system",
        actor_id="taxonomy-auto-activation",
        reason="candidate met configured cross-source thresholds",
        user_selectable=False,
        external_key=entity_external_key,
        now=now,
    )


def run_auto_activation_cycle(
    session: Session,
    *,
    limit: int = 100,
    now: Optional[dt.datetime] = None,
) -> list[CmsTagRecord]:
    """Apply the configured conservative rule to the oldest unresolved rows.

    Bootstrap cannot reach this function: its ingestion path forces the switch
    off and never invokes the runtime scheduler.  Entity Candidates remain
    manual because discovery does not provide stable external keys.
    """

    # Runtime discovery must never activate concepts against an unpublished
    # bootstrap vocabulary, even if an operator enables the switch early.
    if not auto_activation_enabled(session) or current_taxonomy_version(session) <= 0:
        return []
    thresholds = load_auto_activation_thresholds(session)
    candidates = list(
        session.exec(
            select(CmsTagCandidateRecord)
            .where(CmsTagCandidateRecord.status == TagCandidateStatus.CANDIDATE.value)
            .order_by(CmsTagCandidateRecord.last_seen_at, CmsTagCandidateRecord.id)
            .limit(max(1, min(int(limit), 500)))
        ).all()
    )
    activated: list[CmsTagRecord] = []
    for candidate in candidates:
        if candidate.id is None or candidate.proposed_kind == TagKind.ENTITY.value:
            continue
        tag = maybe_auto_activate_candidate(
            session,
            int(candidate.id),
            thresholds=thresholds,
            now=now,
        )
        if tag is not None:
            activated.append(tag)
    return activated


def reject_candidate(
    session: Session,
    candidate_id: int,
    *,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> CmsTagCandidateRecord:
    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    if candidate.status == TagCandidateStatus.REJECTED.value:
        return candidate
    if candidate.status not in {
        TagCandidateStatus.CANDIDATE.value,
        TagCandidateStatus.REVIEWING.value,
    }:
        raise TaxonomyError(f"resolved candidate cannot be rejected from {candidate.status}")
    candidate.status = "rejected"
    candidate.updated_at = now_iso(now)
    session.add(candidate)
    _event(
        session,
        "reject",
        actor_id=actor_id,
        reason=reason,
        payload={"candidate_id": candidate_id},
        now=now,
    )
    session.commit()
    session.refresh(candidate)
    return candidate


def delete_candidate(
    session: Session,
    candidate_id: int,
    *,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Delete a low-quality unresolved Candidate and its evidence.

    Rejection is the durable suppression decision.  Deletion intentionally
    permits a later analysis to discover the same normalized label again, so
    resolved rows are protected and the deleted row is copied into the audit
    payload before its evidence is cascade-deleted.
    """

    candidate = session.get(CmsTagCandidateRecord, candidate_id)
    if candidate is None:
        raise TaxonomyError("candidate does not exist")
    if candidate.status not in {
        TagCandidateStatus.CANDIDATE.value,
        TagCandidateStatus.REVIEWING.value,
        TagCandidateStatus.REJECTED.value,
    }:
        raise TaxonomyError("resolved candidates cannot be deleted; keep their resolution history")
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise TaxonomyError("candidate deletion requires a reason")
    evidence_count = int(session.exec(
        select(func.count(CmsTagCandidateEvidenceRecord.article_id)).where(
            CmsTagCandidateEvidenceRecord.candidate_id == candidate_id
        )
    ).one() or 0)
    payload = {
        "candidate_id": candidate_id,
        "label": candidate.label,
        "normalized_label": candidate.normalized_label,
        "proposed_kind": candidate.proposed_kind,
        "status": candidate.status,
        "evidence_count": evidence_count,
    }
    _event(
        session,
        TagEventAction.DELETE_CANDIDATE.value,
        actor_id=actor_id,
        reason=clean_reason,
        payload=payload,
        now=now,
    )
    session.delete(candidate)
    session.commit()
    return payload


def change_tag_flags(
    session: Session,
    tag_id: int,
    *,
    actor_id: str,
    reason: str = "",
    user_selectable: Optional[bool] = None,
    filterable: Optional[bool] = None,
    recommendable: Optional[bool] = None,
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    changes: dict[str, bool] = {}
    for field, value in {
        "user_selectable": user_selectable,
        "filterable": filterable,
        "recommendable": recommendable,
    }.items():
        if value is not None and getattr(tag, field) != bool(value):
            setattr(tag, field, bool(value))
            changes[field] = bool(value)
    if not changes:
        return tag
    tag.updated_at = now_iso(now)
    session.add(tag)
    _event(
        session,
        "change_flags",
        source_tag_id=tag.id,
        actor_id=actor_id,
        reason=reason,
        payload=changes,
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def change_tag_descriptions(
    session: Session,
    tag_id: int,
    *,
    actor_id: str,
    description: Optional[str] = None,
    prompt_description: Optional[str] = None,
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    """Update human-facing and model-facing concept boundaries with audit."""

    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    before = {
        "description": tag.description,
        "prompt_description": tag.prompt_description,
    }
    if description is not None:
        tag.description = str(description).strip()
    if prompt_description is not None:
        tag.prompt_description = str(prompt_description).strip()
    after = {
        "description": tag.description,
        "prompt_description": tag.prompt_description,
    }
    if before == after:
        return tag
    tag.updated_at = now_iso(now)
    session.add(tag)
    _event(
        session,
        TagEventAction.CHANGE_FLAGS.value,
        source_tag_id=tag_id,
        actor_id=actor_id,
        reason=reason,
        payload={"operation": "change_descriptions", "before": before, "after": after},
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def change_entity_metadata(
    session: Session,
    tag_id: int,
    *,
    actor_id: str,
    entity_type: str,
    external_key: Optional[str],
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    if tag.kind != TagKind.ENTITY.value:
        raise TaxonomyError("entity metadata is only valid for entity tags")
    normalized_type = validate_entity_type(
        entity_type,
        required=tag.status == TagStatus.ACTIVE.value,
    )
    normalized_key = str(external_key or "").strip() or None
    before = {"entity_type": tag.entity_type, "external_key": tag.external_key}
    after = {"entity_type": normalized_type, "external_key": normalized_key}
    if before == after:
        return tag
    tag.entity_type = normalized_type
    tag.external_key = normalized_key
    tag.updated_at = now_iso(now)
    session.add(tag)
    _event(
        session,
        TagEventAction.CHANGE_FLAGS.value,
        source_tag_id=tag_id,
        actor_id=actor_id,
        reason=reason,
        payload={"operation": "entity_metadata", "before": before, "after": after},
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def change_tag_parent(
    session: Session,
    tag_id: int,
    *,
    actor_id: str,
    parent_id: Optional[int],
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    validated = _validated_parent_id(
        session,
        kind=tag.kind,
        parent_id=parent_id,
        tag_id=tag_id,
    )
    if tag.parent_id == validated:
        return tag
    before = tag.parent_id
    tag.parent_id = validated
    tag.updated_at = now_iso(now)
    session.add(tag)
    _event(
        session,
        TagEventAction.CHANGE_FLAGS.value,
        source_tag_id=tag_id,
        target_tag_id=validated,
        actor_id=actor_id,
        reason=reason,
        payload={"operation": "change_parent", "before": before, "after": validated},
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def rename_tag(
    session: Session,
    tag_id: int,
    *,
    actor_id: str,
    name_zh: Optional[str] = None,
    name_en: Optional[str] = None,
    reason: str = "",
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    """Rename a concept while retaining its former display names as aliases."""

    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise TaxonomyError("tag does not exist")
    old = {"name_zh": tag.name_zh, "name_en": tag.name_en}
    next_names = {
        "name_zh": str(name_zh).strip() if name_zh is not None else tag.name_zh,
        "name_en": str(name_en).strip() if name_en is not None else tag.name_en,
    }
    if next_names == old:
        return tag
    tag.name_zh = next_names["name_zh"]
    tag.name_en = next_names["name_en"]
    display = tag.name_zh or tag.name_en or tag.code
    normalized = normalize_label(display)
    if not normalized:
        raise TaxonomyError("tag display name must not be empty")
    alias_conflict = session.exec(
        select(CmsTagAliasRecord).where(
            CmsTagAliasRecord.kind == tag.kind,
            CmsTagAliasRecord.normalized_alias == normalized,
            CmsTagAliasRecord.tag_id != tag_id,
        )
    ).first()
    if alias_conflict:
        raise TaxonomyError("renamed tag conflicts with an existing alias in this facet")
    tag.normalized_name = normalized
    tag.updated_at = now_iso(now)
    session.add(tag)
    session.flush()
    for locale, field in (("zh", "name_zh"), ("en", "name_en")):
        value = old[field]
        if not value or value == next_names[field] or normalize_label(value) == tag.normalized_name:
            continue
        existing = session.exec(
            select(CmsTagAliasRecord).where(
                CmsTagAliasRecord.tag_id == tag_id,
                CmsTagAliasRecord.normalized_alias == normalize_label(value),
            )
        ).first()
        if existing is not None:
            existing.alias_type = TagAliasType.FORMER_NAME.value
            existing.locale = locale
            existing.updated_at = now_iso(now)
            session.add(existing)
        else:
            add_alias(
                session,
                tag_id=tag_id,
                alias=value,
                alias_type=TagAliasType.FORMER_NAME.value,
                locale=locale,
                now=now,
            )
    _sync_current_name_aliases(session, tag, now=now)
    _event(
        session,
        "rename",
        source_tag_id=tag.id,
        actor_id=actor_id,
        reason=reason,
        payload={"before": old, "after": {"name_zh": tag.name_zh, "name_en": tag.name_en}},
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def deprecate_tag(
    session: Session,
    tag_id: int,
    *,
    replacement_id: Optional[int] = None,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    tag = session.get(CmsTagRecord, tag_id)
    replacement = session.get(CmsTagRecord, replacement_id) if replacement_id else None
    if tag is None:
        raise TaxonomyError("tag does not exist")
    if replacement_id and (
        replacement is None
        or replacement.id == tag.id
        or replacement.kind != tag.kind
        or replacement.status != TagStatus.ACTIVE.value
    ):
        raise TaxonomyError("replacement must be a different tag in the same facet")
    cursor = replacement
    visited: set[int] = set()
    while cursor is not None:
        cursor_id = int(cursor.id or 0)
        if cursor_id == tag_id:
            raise TaxonomyError("replacement chain must not create a cycle")
        if cursor_id in visited:
            raise TaxonomyError("replacement chain already contains a cycle")
        visited.add(cursor_id)
        cursor = session.get(CmsTagRecord, cursor.replacement_id) if cursor.replacement_id else None
    tag.status = "deprecated"
    tag.replacement_id = replacement_id
    tag.user_selectable = False
    tag.recommendable = False
    tag.updated_at = now_iso(now)
    session.add(tag)
    _event(
        session,
        "deprecate",
        source_tag_id=tag.id,
        target_tag_id=replacement_id,
        actor_id=actor_id,
        reason=reason,
        now=now,
    )
    session.commit()
    session.refresh(tag)
    return tag


def _merge_interest_rows(source: UserInterestTagRecord, target: UserInterestTagRecord) -> None:
    if source.stance == "mute" or target.stance == "mute":
        target.stance = "mute"
        target.priority = "normal"
    else:
        target.stance = "follow"
        target.priority = "high" if "high" in {source.priority, target.priority} else "normal"
    target.updated_at = max(source.updated_at, target.updated_at)


def merge_tags(
    session: Session,
    source_tag_id: int,
    target_tag_id: int,
    *,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> CmsTagRecord:
    """Merge source into target, preserving manual assignments and mute interests."""

    source_tag = session.get(CmsTagRecord, source_tag_id)
    target_tag = session.get(CmsTagRecord, target_tag_id)
    if source_tag is None or target_tag is None:
        raise TaxonomyError("source and target tags must exist")
    if source_tag.id == target_tag.id or source_tag.kind != target_tag.kind:
        raise TaxonomyError("merge requires different tags in the same facet")
    if target_tag.status != "active":
        raise TaxonomyError("merge target must be active")
    stamp = now_iso(now)

    source_rows = list(
        session.exec(
            select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.tag_id == source_tag_id
            )
        ).all()
    )
    target_rows = {
        row.article_id: row
        for row in session.exec(
            select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.tag_id == target_tag_id
            )
        ).all()
    }
    for source_row in source_rows:
        target_row = target_rows.get(source_row.article_id)
        if target_row is None:
            source_row.tag_id = target_tag_id
            source_row.tag_kind = target_tag.kind
            source_row.updated_at = stamp
            session.add(source_row)
            continue
        source_wins = (
            source_row.assignment_source == "manual"
            and target_row.assignment_source != "manual"
        )
        desired_primary = bool(source_row.is_primary or target_row.is_primary)
        if source_wins:
            target_row.assignment_source = source_row.assignment_source
            target_row.relevance = source_row.relevance
            target_row.prompt_version = source_row.prompt_version
            target_row.taxonomy_version = source_row.taxonomy_version
        elif source_row.assignment_source == target_row.assignment_source:
            target_row.relevance = max(target_row.relevance, source_row.relevance)
        # Delete and flush before promoting target, otherwise SQLite's partial
        # unique primary index briefly sees two primary rows in this facet.
        session.delete(source_row)
        session.flush()
        target_row.is_primary = desired_primary
        target_row.updated_at = stamp
        session.add(target_row)

    analyses = list(
        session.exec(
            select(ArticleAnalysisRecord).where(
                ArticleAnalysisRecord.primary_tag_id == source_tag_id
            )
        ).all()
    )
    for analysis in analyses:
        analysis.primary_tag_id = target_tag_id
        analysis.updated_at = stamp
        session.add(analysis)

    source_interests = list(
        session.exec(
            select(UserInterestTagRecord).where(UserInterestTagRecord.tag_id == source_tag_id)
        ).all()
    )
    target_interests = {
        row.owner_username: row
        for row in session.exec(
            select(UserInterestTagRecord).where(UserInterestTagRecord.tag_id == target_tag_id)
        ).all()
    }
    for source_interest in source_interests:
        target_interest = target_interests.get(source_interest.owner_username)
        if target_interest is None:
            source_interest.tag_id = target_tag_id
            source_interest.updated_at = stamp
            session.add(source_interest)
        else:
            _merge_interest_rows(source_interest, target_interest)
            session.add(target_interest)
            session.delete(source_interest)

    source_tag.status = "merged"
    source_tag.replacement_id = target_tag_id
    source_tag.user_selectable = False
    source_tag.recommendable = False
    source_tag.updated_at = stamp
    session.add(source_tag)
    session.flush()

    aliases = list(
        session.exec(
            select(CmsTagAliasRecord).where(CmsTagAliasRecord.tag_id == source_tag_id)
        ).all()
    )
    canonical_aliases = [(source_tag.name_zh, "translation", "zh"), (source_tag.name_en, "former_name", "en")]
    for alias in aliases:
        conflict = session.exec(
            select(CmsTagAliasRecord).where(
                CmsTagAliasRecord.kind == target_tag.kind,
                CmsTagAliasRecord.normalized_alias == alias.normalized_alias,
                CmsTagAliasRecord.id != alias.id,
            )
        ).first()
        if conflict and conflict.tag_id != target_tag_id:
            raise TaxonomyError(f"cannot merge conflicting alias {alias.alias!r}")
        if conflict:
            session.delete(alias)
        else:
            alias.tag_id = target_tag_id
            alias.updated_at = stamp
            session.add(alias)
    session.flush()
    for alias, alias_type, locale in canonical_aliases:
        if alias and normalize_label(alias) != target_tag.normalized_name:
            add_alias(
                session,
                tag_id=target_tag_id,
                alias=alias,
                alias_type=alias_type,
                locale=locale,
                now=now,
            )

    _event(
        session,
        "merge",
        source_tag_id=source_tag_id,
        target_tag_id=target_tag_id,
        actor_id=actor_id,
        reason=reason,
        now=now,
    )
    session.commit()
    session.refresh(target_tag)
    return target_tag


def create_taxonomy_version(
    session: Session,
    *,
    change_summary: str,
    now: Optional[dt.datetime] = None,
) -> TaxonomyVersionRecord:
    versions = list(session.exec(select(TaxonomyVersionRecord.version)).all())
    version = max((int(item) for item in versions), default=0) + 1
    record = TaxonomyVersionRecord(
        version=version,
        status="draft",
        change_summary=change_summary,
        created_at=now_iso(now),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def activate_taxonomy_version(
    session: Session,
    version: int,
    *,
    actor_id: str,
    now: Optional[dt.datetime] = None,
) -> TaxonomyVersionRecord:
    record = session.get(TaxonomyVersionRecord, version)
    if record is None:
        raise TaxonomyError("taxonomy version does not exist")
    stamp = now_iso(now)
    active = list(
        session.exec(
            select(TaxonomyVersionRecord).where(TaxonomyVersionRecord.status == "active")
        ).all()
    )
    for old in active:
        old.status = "retired"
        session.add(old)
    session.flush()
    record.status = "active"
    record.activated_by = actor_id
    record.activated_at = stamp
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def queue_retag_job(
    session: Session,
    *,
    taxonomy_version: int,
    scope: Mapping[str, Any],
    event_id: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    if session.get(TaxonomyVersionRecord, taxonomy_version) is None:
        raise TaxonomyError("taxonomy version does not exist")
    stamp = now_iso(now)
    record = TagRetagJobRecord(
        event_id=event_id,
        taxonomy_version=taxonomy_version,
        operation=AnalysisOperation.RETAG_ONLY.value,
        scope_json=json.dumps(dict(scope), ensure_ascii=False, sort_keys=True),
        status=RetagJobStatus.QUEUED.value,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def retag_article_from_evidence(
    session: Session,
    article: ArticleRecord,
    taxonomy_version: int,
    *,
    now: Optional[dt.datetime] = None,
) -> None:
    """Rebuild closed-set machine assignments without recomputing analysis.

    Existing active assignments remain eligible, and Candidate evidence that a
    human resolved to an active canonical tag is folded in by maximum
    confidence.  Manual assignments are preserved by ``assign_article_tags``.
    The callback deliberately does not commit so ``process_retag_batch`` owns
    the batch cursor transaction.
    """

    analysis = session.get(ArticleAnalysisRecord, article.id)
    if analysis is None or analysis.status != "succeeded":
        return
    active_tags = {
        int(tag.id): tag
        for tag in session.exec(
            select(CmsTagRecord).where(CmsTagRecord.status == TagStatus.ACTIVE.value)
        ).all()
        if tag.id is not None
    }
    relevance_by_tag: dict[int, float] = {}
    for assignment in session.exec(
        select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == article.id,
            ArticleTagAssignmentRecord.assignment_source != TagAssignmentSource.MANUAL.value,
        )
    ).all():
        if assignment.tag_id in active_tags:
            relevance_by_tag[assignment.tag_id] = max(
                relevance_by_tag.get(assignment.tag_id, 0.0),
                float(assignment.relevance),
            )
    for evidence in session.exec(
        select(CmsTagCandidateEvidenceRecord).where(
            CmsTagCandidateEvidenceRecord.article_id == article.id
        )
    ).all():
        candidate = session.get(CmsTagCandidateRecord, evidence.candidate_id)
        if candidate is None or candidate.status not in {
            TagCandidateStatus.ACTIVATED.value,
            TagCandidateStatus.MERGED.value,
        } or candidate.resolution_tag_id not in active_tags:
            continue
        tag_id = int(candidate.resolution_tag_id)
        relevance_by_tag[tag_id] = max(
            relevance_by_tag.get(tag_id, 0.0),
            float(evidence.confidence),
        )

    selected: list[tuple[CmsTagRecord, float]] = []
    for facet, limit in DEFAULT_FACET_LIMITS.items():
        facet_rows = sorted(
            (
                (active_tags[tag_id], relevance)
                for tag_id, relevance in relevance_by_tag.items()
                if active_tags[tag_id].kind == facet
            ),
            key=lambda item: (-item[1], item[0].code),
        )
        selected.extend(facet_rows[: int(limit)])
    assignments = [
        ArticleTagAssignmentDTO(
            code=tag.code,
            kind=tag.kind,
            relevance=relevance,
            is_primary=False,
        )
        for tag, relevance in selected
    ]
    assign_article_tags(
        session,
        article_id=article.id,
        assignments=assignments,
        assignment_source=TagAssignmentSource.LLM.value,
        prompt_version=analysis.prompt_version,
        taxonomy_version=taxonomy_version,
        commit=False,
        now=now,
    )
    analysis.tagging_status = "succeeded"
    analysis.tagged_at = now_iso(now)
    analysis.taxonomy_version = int(taxonomy_version)
    analysis.updated_at = now_iso(now)
    session.add(analysis)
    session.flush()


def claim_retag_job(
    session: Session,
    *,
    lease_owner: str,
    lease_seconds: int = 300,
    now: Optional[dt.datetime] = None,
) -> Optional[TagRetagJobRecord]:
    current = now or dt.datetime.now(dt.timezone.utc)
    stamp = now_iso(current)
    candidates = list(
        session.exec(
            select(TagRetagJobRecord)
            .where(
                TagRetagJobRecord.operation == AnalysisOperation.RETAG_ONLY.value,
                or_(
                    TagRetagJobRecord.status == "queued",
                    (TagRetagJobRecord.status == "running")
                    & (TagRetagJobRecord.lease_owner == lease_owner),
                    (TagRetagJobRecord.status == "running")
                    & (TagRetagJobRecord.lease_expires_at < stamp),
                )
            )
            .order_by(TagRetagJobRecord.id)
        ).all()
    )
    if not candidates:
        return None
    job = candidates[0]
    job.status = "running"
    job.lease_owner = lease_owner
    job.lease_expires_at = now_iso(current + dt.timedelta(seconds=lease_seconds))
    job.updated_at = stamp
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def process_retag_batch(
    session: Session,
    job: TagRetagJobRecord,
    retag_article: Callable[[Session, ArticleRecord, int], None],
    *,
    lease_owner: str,
    batch_size: int = 100,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    """Process a deterministic cursor batch; callback must not commit.

    A failed article is counted and the batch continues.  A process crash keeps
    the cursor at the last committed batch and the expired lease can be reclaimed.
    """

    if job.status != "running" or job.lease_owner != lease_owner:
        raise TaxonomyError("retag job is not leased by this worker")
    if job.operation != AnalysisOperation.RETAG_ONLY.value:
        raise TaxonomyError("retag worker cannot process a full-analysis job")
    try:
        scope = json.loads(job.scope_json or "{}")
    except ValueError as exc:
        raise TaxonomyError("retag scope is invalid JSON") from exc
    article_ids = [str(item) for item in scope.get("article_ids", []) if str(item)]
    since = str(scope.get("since") or "")
    since_time = parse_article_time(since) if since else None
    if since and since_time is None:
        raise TaxonomyError("retag scope since time is invalid")
    # Historical rows mix UTC offsets and naive Shanghai wall-clock strings.
    # Page by stable article id, then apply the lower bound to parsed instants;
    # lexical timestamp predicates would silently miss part of the seven-day set.
    page_cursor = job.cursor
    page_size = max(100, max(1, int(batch_size)) * 4)
    batch: list[ArticleRecord] = []
    has_more = False
    while not has_more:
        query = select(ArticleRecord)
        if page_cursor:
            query = query.where(ArticleRecord.id > page_cursor)
        if article_ids:
            query = query.where(ArticleRecord.id.in_(article_ids))
        page = list(
            session.exec(query.order_by(ArticleRecord.id).limit(page_size)).all()
        )
        if not page:
            break
        for article in page:
            page_cursor = article.id
            if since_time is not None:
                fetched = parse_article_time(article.fetched_date)
                if fetched is None or fetched < since_time:
                    continue
            if len(batch) >= max(1, int(batch_size)):
                has_more = True
                break
            batch.append(article)
        if has_more or len(page) < page_size:
            break
    stamp = now_iso(now)
    failed_ids = [str(item) for item in scope.get("failed_article_ids", []) if str(item)]
    for article in batch:
        try:
            with session.begin_nested():
                retag_article(session, article, int(job.taxonomy_version))
            job.succeeded_count += 1
        except Exception as exc:  # noqa: BLE001 - record individual failures and continue
            job.failed_count += 1
            if article.id not in failed_ids:
                failed_ids.append(article.id)
            job.last_error = f"{type(exc).__name__}: {str(exc)}"[:500]
        job.cursor = article.id
        job.affected_count += 1
    job.updated_at = stamp
    scope["failed_article_ids"] = failed_ids
    job.scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
    if has_more:
        job.lease_expires_at = now_iso(
            (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(seconds=300)
        )
    else:
        job.status = "partial_failed" if job.failed_count else "succeeded"
        job.lease_owner = None
        job.lease_expires_at = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def retry_failed_retag_job(
    session: Session,
    job: TagRetagJobRecord,
    *,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    """Queue a new, auditable retry limited to a terminal job's failed IDs."""

    if job.status not in {"partial_failed", "failed"}:
        raise TaxonomyError("only a failed retag job can be retried")
    try:
        old_scope = json.loads(job.scope_json or "{}")
    except ValueError as exc:
        raise TaxonomyError("retag scope is invalid JSON") from exc
    failed_ids = [str(item) for item in old_scope.get("failed_article_ids", []) if str(item)]
    if not failed_ids:
        raise TaxonomyError("retag job has no failed article IDs to retry")
    return queue_retag_job(
        session,
        taxonomy_version=job.taxonomy_version,
        event_id=job.event_id,
        scope={"article_ids": failed_ids, "retry_of_job_id": job.id},
        now=now,
    )


def fail_retag_job(
    session: Session,
    job: TagRetagJobRecord,
    error: Exception | str,
    *,
    retryable: bool,
    now: Optional[dt.datetime] = None,
) -> TagRetagJobRecord:
    job.status = "queued" if retryable else "failed"
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = str(error)[:500]
    job.updated_at = now_iso(now)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def taxonomy_coverage_metrics(
    session: Session,
    *,
    since: str,
    until: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Coverage snapshot used after the closed-set seven-day retag pass."""

    since_time = parse_article_time(since)
    if since_time is None:
        raise TaxonomyError("coverage since time is invalid")
    end_time = until or dt.datetime.now(dt.timezone.utc)
    end_utc = parse_article_time(end_time.isoformat())
    assert end_utc is not None
    coarse_start = (since_time - dt.timedelta(days=1)).date().isoformat()
    rows = session.exec(
        select(ArticleAnalysisRecord, ArticleRecord.fetched_date)
        .join(ArticleRecord, ArticleRecord.id == ArticleAnalysisRecord.article_id)
        .where(
            func.substr(ArticleRecord.fetched_date, 1, 10) >= coarse_start,
            ArticleAnalysisRecord.status == "succeeded",
        )
    ).all()
    analyses = [
        analysis
        for analysis, fetched_date in rows
        if in_time_window(fetched_date, start=since_time, end=end_utc)
    ]
    article_ids = [row.article_id for row in analyses]
    assignments = (
        list(
            session.exec(
                select(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id.in_(article_ids)
                )
            ).all()
        )
        if article_ids
        else []
    )
    tagged_ids = {row.article_id for row in assignments}
    primary_ids = {row.article_id for row in assignments if row.is_primary}
    facet_counts = {
        kind: sum(1 for row in assignments if row.tag_kind == kind)
        for kind in ("topic", "industry", "entity")
    }
    total = len(article_ids)
    return {
        "analyzed_articles": total,
        "tagged_articles": len(tagged_ids),
        "coverage_rate": len(tagged_ids) / total if total else 0.0,
        "primary_missing_rate": (total - len(primary_ids)) / total if total else 0.0,
        "average_tags_per_facet": {
            kind: count / total if total else 0.0 for kind, count in facet_counts.items()
        },
    }


def canonical_alias_gap_count(session: Session) -> int:
    """Count bilingual tags whose non-primary display name cannot resolve."""

    gaps = 0
    tags = list(session.exec(select(CmsTagRecord)).all())
    aliases_by_tag: dict[int, set[str]] = {}
    for alias in session.exec(select(CmsTagAliasRecord)).all():
        aliases_by_tag.setdefault(alias.tag_id, set()).add(alias.normalized_alias)
    for tag in tags:
        known = aliases_by_tag.get(int(tag.id or 0), set())
        for value in (tag.name_zh, tag.name_en):
            normalized = normalize_label(value)
            if normalized and normalized != tag.normalized_name and normalized not in known:
                gaps += 1
    return gaps


def backfill_canonical_aliases(
    session: Session,
    *,
    actor_id: str,
    reason: str,
    now: Optional[dt.datetime] = None,
) -> int:
    before = canonical_alias_gap_count(session)
    for tag in session.exec(select(CmsTagRecord)).all():
        _sync_current_name_aliases(session, tag, now=now)
    if before:
        _event(
            session,
            TagEventAction.RENAME.value,
            actor_id=actor_id,
            reason=reason,
            payload={"operation": "canonical_alias_backfill", "created": before},
            now=now,
        )
    session.commit()
    return before


def taxonomy_governance_state(
    session: Session,
    *,
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    current = now or dt.datetime.now(dt.timezone.utc)
    versions = list(
        session.exec(select(TaxonomyVersionRecord).order_by(TaxonomyVersionRecord.version.desc())).all()
    )
    tags = list(session.exec(select(CmsTagRecord)).all())
    candidates = list(session.exec(select(CmsTagCandidateRecord)).all())
    receipt_record = session.get(AppSettingRecord, TAXONOMY_V1_REVIEW_RECEIPT_KEY)
    try:
        review_receipt = json.loads(receipt_record.value) if receipt_record and receipt_record.value else None
    except (TypeError, ValueError):
        review_receipt = None
    review_receipt_valid = bool(
        isinstance(review_receipt, dict)
        and re.fullmatch(r"[0-9a-f]{64}", str(review_receipt.get("manifest_sha256") or ""))
        and str(review_receipt.get("actor_id") or "").strip()
        and str(review_receipt.get("reviewed_at") or "").strip()
        and review_receipt.get("coverage_decision") in {"complete", "accept_bias", "not_applicable"}
        and (
            review_receipt.get("coverage_decision") != "not_applicable"
            or review_receipt.get("review_basis") == "label_set_only"
        )
    )
    active_by_kind = {
        kind: sum(1 for tag in tags if tag.kind == kind and tag.status == TagStatus.ACTIVE.value)
        for kind in (TagKind.TOPIC.value, TagKind.INDUSTRY.value, TagKind.ENTITY.value)
    }
    blockers = [
        f"{kind} 分面还没有 active 标签"
        for kind, count in active_by_kind.items()
        if count == 0
    ]
    invalid_entities = [
        tag
        for tag in tags
        if tag.kind == TagKind.ENTITY.value
        and tag.status == TagStatus.ACTIVE.value
        and tag.entity_type not in ENTITY_TYPES
    ]
    if invalid_entities:
        blockers.append(
            f"仍有 {len(invalid_entities)} 个 active Entity 缺少有效 entity_type"
        )
    alias_gaps = canonical_alias_gap_count(session)
    if alias_gaps:
        blockers.append(f"仍有 {alias_gaps} 个规范中英文名称缺少可解析 Alias")
    if auto_activation_enabled(session):
        blockers.append("bootstrap 审核期间必须先关闭候选自动激活")
    unresolved = sum(1 for item in candidates if item.status in {"candidate", "reviewing"})
    if unresolved:
        blockers.append(f"仍有 {unresolved} 个 Candidate 未接受、归并或拒绝")
    if not review_receipt_valid:
        blockers.append("尚未导入完成产品决策的 taxonomy v1 审核清单")
    since = (current - dt.timedelta(days=7)).isoformat()
    active_version = next((item for item in versions if item.status == "active"), None)
    if active_version is not None:
        blockers.append(f"taxonomy v{active_version.version} 已发布，不能重复执行 v1 首发")
    return {
        "active_version": active_version.version if active_version else 0,
        "versions": [
            {
                "version": item.version,
                "status": item.status,
                "change_summary": item.change_summary,
                "activated_by": item.activated_by,
                "activated_at": item.activated_at,
                "created_at": item.created_at,
            }
            for item in versions
        ],
        "active_tags_by_kind": active_by_kind,
        "entity_types": list(ENTITY_TYPES),
        "invalid_active_entity_count": len(invalid_entities),
        "tag_count": len(tags),
        "candidate_count": len(candidates),
        "unresolved_candidate_count": unresolved,
        "review_receipt": review_receipt,
        "review_receipt_valid": review_receipt_valid,
        "canonical_alias_gap_count": alias_gaps,
        "auto_activation_enabled": auto_activation_enabled(session),
        "coverage_7d": taxonomy_coverage_metrics(session, since=since, until=current),
        "publish_ready": not blockers,
        "publish_blockers": blockers,
    }
