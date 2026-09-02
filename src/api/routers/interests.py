"""Reader-managed explicit taxonomy interests."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api import deps
from api.routers import personal_briefs
from models.analysis_contracts import DigestGenerationReason
from models.db import CmsTagRecord, UserInterestTagRecord
from services import accounts as accounts_service
from services import taxonomy as taxonomy_service


router = APIRouter(
    prefix="/api/reader/interests",
    tags=["reader-interests"],
    dependencies=[Depends(deps.require_reader)],
)


class InterestInput(BaseModel):
    tag_id: int = Field(gt=0)
    stance: Literal["follow", "mute"] = "follow"


class InterestReplace(BaseModel):
    items: list[InterestInput] = Field(default_factory=list, max_length=200)
    complete_onboarding: bool = False


def _username(auth: dict[str, Any]) -> str:
    username = str(auth.get("sub") or auth.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return username


def _tag_payload(tag: CmsTagRecord, **metadata: Any) -> dict[str, Any]:
    return {
        "id": tag.id,
        "code": tag.code,
        "kind": tag.kind,
        "name_zh": tag.name_zh,
        "name_en": tag.name_en,
        "description": tag.description,
        "entity_type": tag.entity_type,
        **metadata,
    }


@router.get("/catalog")
def catalog(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    personal_briefs._require_enabled(session)  # noqa: SLF001 - shared feature gate
    result = taxonomy_service.ranked_interest_catalog(
        session,
        owner_username=_username(auth),
    )
    rows = result["tags"]
    payloads = [
        _tag_payload(row, **result["metadata"].get(int(row.id), {})) for row in rows
    ]
    grouped = {
        kind: [payload for row, payload in zip(rows, payloads) if row.kind == kind]
        for kind in taxonomy_service.INTEREST_CATALOG_DEFAULT_LIMITS
    }
    return {
        "items": payloads,
        "facets": grouped,
        "policy": result["policy"],
        "facet_stats": result["facet_stats"],
    }


@router.get("")
def get_interests(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    personal_briefs._require_enabled(session)  # noqa: SLF001 - shared feature gate
    rows = session.exec(
        select(UserInterestTagRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == UserInterestTagRecord.tag_id)
        .where(UserInterestTagRecord.owner_username == _username(auth))
        .order_by(CmsTagRecord.kind, CmsTagRecord.normalized_name)
    ).all()
    return {
        "items": [
            {
                "tag": _tag_payload(tag),
                "stance": interest.stance,
                "updated_at": interest.updated_at,
            }
            for interest, tag in rows
        ]
    }


@router.put("")
def replace_interests(
    body: InterestReplace,
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    personal_briefs._require_enabled(session)  # noqa: SLF001 - shared feature gate
    username = _username(auth)
    requested = {item.tag_id: item for item in body.items}
    if len(requested) != len(body.items):
        raise HTTPException(status_code=400, detail="同一标签不能重复配置")
    tags = {
        row.id: row
        for row in session.exec(
            select(CmsTagRecord).where(CmsTagRecord.id.in_(list(requested) or [-1]))
        ).all()
    }
    invalid = [
        tag_id
        for tag_id in requested
        if tag_id not in tags
        or tags[tag_id].status != "active"
        or not tags[tag_id].user_selectable
    ]
    if invalid:
        raise HTTPException(status_code=400, detail={"invalid_tag_ids": sorted(invalid)})

    existing = {
        row.tag_id: row
        for row in session.exec(
            select(UserInterestTagRecord).where(
                UserInterestTagRecord.owner_username == username
            )
        ).all()
    }
    for tag_id, row in existing.items():
        if tag_id not in requested:
            session.delete(row)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for tag_id, item in requested.items():
        row = existing.get(tag_id) or UserInterestTagRecord(
            owner_username=username,
            tag_id=tag_id,
            created_at=now,
            updated_at=now,
        )
        row.stance = item.stance
        row.priority = "normal"
        row.source = "explicit"
        row.updated_at = now
        session.add(row)
    if body.complete_onboarding:
        accounts_service.complete_interest_onboarding(session, username)
    session.commit()

    personal_briefs.trigger_today_revision(
        deps.get_db_sink().engine, username, DigestGenerationReason.INTEREST_CHANGED
    )
    result = get_interests(auth=auth, session=session)
    result["onboarding_completed"] = bool(body.complete_onboarding)
    return result
