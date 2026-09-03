"""Admin taxonomy/Candidate governance, review and v1 publication router."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import Session, select

from api import deps
from models.db import (
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
)
from services import taxonomy as taxonomy_service


router = APIRouter(tags=["taxonomy"], dependencies=[Depends(deps.require_admin)])


class TagCreate(BaseModel):
    code: str
    kind: str
    name_zh: str = ""
    name_en: str = ""
    description: str = ""
    prompt_description: str = ""
    status: str = "draft"
    user_selectable: bool = False
    filterable: bool = True
    recommendable: bool = True
    entity_type: str = ""
    external_key: Optional[str] = None
    parent_id: Optional[int] = None


class TagPatch(BaseModel):
    name_zh: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    prompt_description: Optional[str] = None
    user_selectable: Optional[bool] = None
    filterable: Optional[bool] = None
    recommendable: Optional[bool] = None
    entity_type: Optional[str] = None
    external_key: Optional[str] = None
    parent_id: Optional[int] = None
    reason: str = ""


class CandidateActivate(BaseModel):
    code: Optional[str] = None
    name_zh: str = ""
    name_en: str = ""
    user_selectable: bool = False
    entity_type: str = ""
    external_key: Optional[str] = None
    kind: Optional[str] = None
    parent_id: Optional[int] = None
    reason: str = ""


class AliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    alias_type: str = "synonym"
    locale: str = Field(default="", max_length=16)
    reason: str = ""


class CandidatePatch(BaseModel):
    kind: str
    reason: str = Field(min_length=1)


class CandidateResolve(BaseModel):
    target_tag_id: int
    reason: str = Field(min_length=1)


class TaxonomyPublish(BaseModel):
    confirmation: str
    change_summary: str = Field(min_length=1, max_length=300)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1)


class TagMerge(BaseModel):
    target_tag_id: int
    reason: str = Field(min_length=1)


class TagDeprecate(BaseModel):
    replacement_id: Optional[int] = None
    reason: str = Field(min_length=1)


class RetagBody(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    article_ids: list[str] = Field(default_factory=list)


class InterestCatalogPolicyPatch(BaseModel):
    topic: int = Field(ge=0, le=taxonomy_service.INTEREST_CATALOG_MAX_LIMIT)
    industry: int = Field(ge=0, le=taxonomy_service.INTEREST_CATALOG_MAX_LIMIT)
    entity: int = Field(ge=0, le=taxonomy_service.INTEREST_CATALOG_MAX_LIMIT)
    reason: str = Field(min_length=1, max_length=300)


def _actor(auth: dict[str, Any]) -> str:
    return str(auth.get("sub") or auth.get("username") or auth.get("user") or "admin")


def _tag_payload(session: Session, tag: CmsTagRecord) -> dict[str, Any]:
    aliases = list(
        session.exec(
            select(CmsTagAliasRecord).where(CmsTagAliasRecord.tag_id == tag.id)
        ).all()
    )
    return {
        "id": tag.id,
        "code": tag.code,
        "kind": tag.kind,
        "name_zh": tag.name_zh,
        "name_en": tag.name_en,
        "description": tag.description,
        "prompt_description": tag.prompt_description,
        "status": tag.status,
        "replacement_id": tag.replacement_id,
        "parent_id": tag.parent_id,
        "entity_type": tag.entity_type,
        "external_key": tag.external_key,
        "user_selectable": tag.user_selectable,
        "filterable": tag.filterable,
        "recommendable": tag.recommendable,
        "activation_mode": tag.activation_mode,
        "taxonomy_version": tag.taxonomy_version,
        "aliases": [
            {
                "id": item.id,
                "locale": item.locale,
                "alias": item.alias,
                "alias_type": item.alias_type,
            }
            for item in aliases
        ],
        "created_at": tag.created_at,
        "updated_at": tag.updated_at,
    }


def _candidate_payload(session: Session, candidate: CmsTagCandidateRecord) -> dict[str, Any]:
    evidence = list(
        session.exec(
            select(CmsTagCandidateEvidenceRecord)
            .where(CmsTagCandidateEvidenceRecord.candidate_id == candidate.id)
            .order_by(CmsTagCandidateEvidenceRecord.created_at.desc())
            .limit(10)
        ).all()
    )
    return {
        "id": candidate.id,
        "label": candidate.label,
        "normalized_label": candidate.normalized_label,
        "proposed_kind": candidate.proposed_kind,
        "status": candidate.status,
        "support_article_count_7d": candidate.support_article_count_7d,
        "support_article_count_30d": candidate.support_article_count_30d,
        "distinct_source_count_7d": candidate.distinct_source_count_7d,
        "distinct_source_count_30d": candidate.distinct_source_count_30d,
        "distinct_day_count_7d": candidate.distinct_day_count_7d,
        "distinct_day_count_30d": candidate.distinct_day_count_30d,
        "mean_confidence": candidate.mean_confidence,
        "nearest_tag_id": candidate.nearest_tag_id,
        "nearest_similarity": candidate.nearest_similarity,
        "resolution_tag_id": candidate.resolution_tag_id,
        "risk_flags": json.loads(candidate.risk_flags_json or "[]"),
        "evidence": [
            {
                "article_id": row.article_id,
                "source_id": row.source_id,
                "source_owner_or_domain": row.source_owner_or_domain,
                "published_date": row.published_date,
                "confidence": row.confidence,
                "raw_label": row.raw_label,
                "context_excerpt": row.context_excerpt,
            }
            for row in evidence
        ],
        "first_seen_at": candidate.first_seen_at,
        "last_seen_at": candidate.last_seen_at,
    }


def _domain_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except taxonomy_service.AmbiguousTagError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except taxonomy_service.TaxonomyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/admin/cms-tags")
def list_tags(
    kind: Optional[str] = None,
    status: Optional[str] = None,
    user_selectable: Optional[bool] = None,
    session: Session = Depends(deps.get_session),
):
    query = select(CmsTagRecord)
    if kind:
        query = query.where(CmsTagRecord.kind == kind)
    if status:
        query = query.where(CmsTagRecord.status == status)
    if user_selectable is not None:
        query = query.where(CmsTagRecord.user_selectable == user_selectable)
    tags = list(session.exec(query.order_by(CmsTagRecord.kind, CmsTagRecord.normalized_name)).all())
    return {"items": [_tag_payload(session, tag) for tag in tags]}


@router.post("/api/admin/cms-tags")
def create_tag(
    body: TagCreate,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    tag = _domain_call(taxonomy_service.create_tag, session, **body.model_dump())
    # Creation of a draft is not a governance transition. Direct active creation
    # is audited through the same activation action used for candidates.
    if tag.status == "active":
        taxonomy_service._event(  # noqa: SLF001 - router is the governance boundary
            session,
            "activate",
            target_tag_id=tag.id,
            actor_id=_actor(auth),
            reason="manual tag creation",
            payload={"direct_creation": True},
        )
    session.commit()
    session.refresh(tag)
    return _tag_payload(session, tag)


@router.patch("/api/admin/cms-tags/{tag_id}")
def patch_tag(
    tag_id: int,
    body: TagPatch,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    actor = _actor(auth)
    tag = session.get(CmsTagRecord, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="标签不存在")
    if body.name_zh is not None or body.name_en is not None:
        tag = _domain_call(
            taxonomy_service.rename_tag,
            session,
            tag_id,
            actor_id=actor,
            name_zh=body.name_zh,
            name_en=body.name_en,
            reason=body.reason,
        )
    if body.description is not None or body.prompt_description is not None:
        tag = _domain_call(
            taxonomy_service.change_tag_descriptions,
            session,
            tag_id,
            actor_id=actor,
            description=body.description,
            prompt_description=body.prompt_description,
            reason=body.reason,
        )
    tag = _domain_call(
        taxonomy_service.change_tag_flags,
        session,
        tag_id,
        actor_id=actor,
        reason=body.reason,
        user_selectable=body.user_selectable,
        filterable=body.filterable,
        recommendable=body.recommendable,
    )
    if "parent_id" in body.model_fields_set:
        tag = _domain_call(
            taxonomy_service.change_tag_parent,
            session,
            tag_id,
            actor_id=actor,
            parent_id=body.parent_id,
            reason=body.reason,
        )
    if {"entity_type", "external_key"} & body.model_fields_set:
        tag = _domain_call(
            taxonomy_service.change_entity_metadata,
            session,
            tag_id,
            actor_id=actor,
            entity_type=(
                body.entity_type if "entity_type" in body.model_fields_set else tag.entity_type
            ),
            external_key=(
                body.external_key if "external_key" in body.model_fields_set else tag.external_key
            ),
            reason=body.reason,
        )
    return _tag_payload(session, tag)


@router.post("/api/admin/cms-tags/{tag_id}/aliases")
def create_alias(
    tag_id: int,
    body: AliasCreate,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    _domain_call(
        taxonomy_service.create_alias,
        session,
        tag_id=tag_id,
        alias=body.alias,
        alias_type=body.alias_type,
        locale=body.locale,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    tag = session.get(CmsTagRecord, tag_id)
    return _tag_payload(session, tag)


@router.delete("/api/admin/cms-tags/{tag_id}/aliases/{alias_id}")
def delete_alias(
    tag_id: int,
    alias_id: int,
    reason: str = Query(min_length=1),
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    _domain_call(
        taxonomy_service.delete_alias,
        session,
        tag_id=tag_id,
        alias_id=alias_id,
        actor_id=_actor(auth),
        reason=reason,
    )
    return {"status": "deleted", "alias_id": alias_id}


@router.get("/api/admin/cms-tag-candidates")
def list_candidates(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    q: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(deps.get_session),
):
    query = select(CmsTagCandidateRecord)
    if status:
        query = query.where(CmsTagCandidateRecord.status == status)
    if kind:
        query = query.where(CmsTagCandidateRecord.proposed_kind == kind)
    needle = taxonomy_service.normalize_label(q)
    if needle:
        query = query.where(
            or_(
                CmsTagCandidateRecord.normalized_label.contains(needle),
                CmsTagCandidateRecord.label.contains(q.strip()),
            )
        )
    total = int(session.exec(select(func.count()).select_from(query.subquery())).one())
    candidates = list(
        session.exec(
            query.order_by(
                CmsTagCandidateRecord.support_article_count_30d.desc(),
                CmsTagCandidateRecord.distinct_source_count_30d.desc(),
                CmsTagCandidateRecord.last_seen_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return {
        "items": [_candidate_payload(session, item) for item in candidates],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.patch("/api/admin/cms-tag-candidates/{candidate_id}")
def patch_candidate(
    candidate_id: int,
    body: CandidatePatch,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    candidate = _domain_call(
        taxonomy_service.reclassify_candidate,
        session,
        candidate_id,
        kind=body.kind,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _candidate_payload(session, candidate)


@router.post("/api/admin/cms-tag-candidates/{candidate_id}/resolve")
def resolve_candidate(
    candidate_id: int,
    body: CandidateResolve,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    candidate = _domain_call(
        taxonomy_service.resolve_candidate_to_tag,
        session,
        candidate_id,
        target_tag_id=body.target_tag_id,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _candidate_payload(session, candidate)


@router.post("/api/admin/cms-tag-candidates/{candidate_id}/activate")
def activate_candidate(
    candidate_id: int,
    body: CandidateActivate,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    tag = _domain_call(
        taxonomy_service.activate_candidate,
        session,
        candidate_id,
        code=body.code,
        name_zh=body.name_zh,
        name_en=body.name_en,
        user_selectable=body.user_selectable,
        entity_type=body.entity_type,
        external_key=body.external_key,
        kind=body.kind,
        parent_id=body.parent_id,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _tag_payload(session, tag)


@router.post("/api/admin/cms-tag-candidates/{candidate_id}/reject")
def reject_candidate(
    candidate_id: int,
    body: ReasonBody,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    candidate = _domain_call(
        taxonomy_service.reject_candidate,
        session,
        candidate_id,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _candidate_payload(session, candidate)


@router.delete("/api/admin/cms-tag-candidates/{candidate_id}")
def delete_candidate(
    candidate_id: int,
    reason: str = Query(min_length=1, max_length=300),
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    deleted = _domain_call(
        taxonomy_service.delete_candidate,
        session,
        candidate_id,
        actor_id=_actor(auth),
        reason=reason,
    )
    return {"status": "deleted", **deleted}


@router.get("/api/admin/taxonomy/state")
def taxonomy_state(session: Session = Depends(deps.get_session)):
    return taxonomy_service.taxonomy_governance_state(session)


@router.get("/api/admin/taxonomy/interest-catalog-policy")
def interest_catalog_policy(session: Session = Depends(deps.get_session)):
    ranked = _domain_call(taxonomy_service.ranked_interest_catalog, session)
    return {
        "policy": ranked["policy"],
        "facet_stats": ranked["facet_stats"],
        "entity_types": list(taxonomy_service.ENTITY_TYPES),
    }


@router.patch("/api/admin/taxonomy/interest-catalog-policy")
def update_interest_catalog_policy(
    body: InterestCatalogPolicyPatch,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    policy = _domain_call(
        taxonomy_service.set_interest_catalog_policy,
        session,
        {kind: getattr(body, kind) for kind in taxonomy_service.INTEREST_CATALOG_DEFAULT_LIMITS},
        actor_id=_actor(auth),
        reason=body.reason,
    )
    ranked = _domain_call(taxonomy_service.ranked_interest_catalog, session)
    return {
        "policy": policy,
        "facet_stats": ranked["facet_stats"],
        "entity_types": list(taxonomy_service.ENTITY_TYPES),
    }


@router.post("/api/admin/taxonomy/aliases/backfill")
def backfill_aliases(
    body: ReasonBody,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    created = _domain_call(
        taxonomy_service.backfill_canonical_aliases,
        session,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return {"created": created, "state": taxonomy_service.taxonomy_governance_state(session)}


@router.post("/api/admin/taxonomy/v1/publish")
def publish_taxonomy_v1(
    body: TaxonomyPublish,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    if body.confirmation != "PUBLISH TAXONOMY V1":
        raise HTTPException(status_code=400, detail="发布确认文本不匹配")
    state = taxonomy_service.taxonomy_governance_state(session)
    if not state["publish_ready"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "taxonomy v1 尚未达到发布条件", "blockers": state["publish_blockers"]},
        )
    from services.taxonomy_bootstrap import publish_taxonomy_v1 as publish_reviewed_v1

    result = _domain_call(
        publish_reviewed_v1,
        session,
        actor_id=_actor(auth),
        now=dt.datetime.now(dt.timezone.utc),
        change_summary=body.change_summary,
    )
    return {**result, "state": taxonomy_service.taxonomy_governance_state(session)}


@router.post("/api/admin/cms-tags/{tag_id}/merge")
def merge_tag(
    tag_id: int,
    body: TagMerge,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    tag = _domain_call(
        taxonomy_service.merge_tags,
        session,
        tag_id,
        body.target_tag_id,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _tag_payload(session, tag)


@router.post("/api/admin/cms-tags/{tag_id}/deprecate")
def deprecate_tag(
    tag_id: int,
    body: TagDeprecate,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    tag = _domain_call(
        taxonomy_service.deprecate_tag,
        session,
        tag_id,
        replacement_id=body.replacement_id,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return _tag_payload(session, tag)


@router.post("/api/admin/cms-tags/{tag_id}/retag")
def queue_retag(
    tag_id: int,
    body: RetagBody,
    session: Session = Depends(deps.get_session),
):
    if session.get(CmsTagRecord, tag_id) is None:
        raise HTTPException(status_code=404, detail="标签不存在")
    version = taxonomy_service.current_taxonomy_version(session)
    if version <= 0:
        raise HTTPException(status_code=409, detail="尚未发布 active taxonomy version")
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=body.days)).isoformat()
    job = _domain_call(
        taxonomy_service.queue_retag_job,
        session,
        taxonomy_version=version,
        # Closed-set retagging evaluates each article against the complete active
        # taxonomy.  A per-tag filter would be misleading and is not supported by
        # the worker contract; the path tag only identifies the admin action.
        scope={"since": since, "article_ids": body.article_ids},
    )
    return {
        "id": job.id,
        "status": job.status,
        "operation": job.operation,
        "taxonomy_version": job.taxonomy_version,
        "scope": json.loads(job.scope_json),
    }
