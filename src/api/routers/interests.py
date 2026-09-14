"""Reader-managed explicit taxonomy interests."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api import deps
from api.routers import personal_briefs
from models.analysis_contracts import DigestGenerationReason
from models.db import CmsTagRecord, UserInterestTagRecord
from services import accounts as accounts_service
from services import taxonomy as taxonomy_service


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/reader/interests",
    tags=["reader-interests"],
    dependencies=[Depends(deps.require_reader)],
)


class InterestInput(BaseModel):
    """v3.56(issue #27)起兴趣只有「关注」:不再接受 stance(旧客户端传来的 stance 字段被忽略)。"""

    tag_id: int = Field(gt=0)


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
        row = existing.get(tag_id)
        if row is None:
            row = UserInterestTagRecord(
                owner_username=username,
                tag_id=tag_id,
                stance="follow",
                priority="normal",
                source="explicit",
                created_at=now,
                updated_at=now,
            )
        elif row.stance == "follow" and row.priority == "normal" and row.source == "explicit":
            # 原样重存不算变更:兴趣版本(personal_digest._interest_version)混入 updated_at,
            # 无谓改写会让今日早报被误标「兴趣已更新」。
            continue
        else:
            row.stance = "follow"
            row.priority = "normal"
            row.source = "explicit"
            row.updated_at = now
        session.add(row)
    onboarding_transition = False
    if body.complete_onboarding:
        record = accounts_service.get_user(session, username)
        onboarding_transition = bool(record and not record.interest_onboarding_completed_at)
        accounts_service.complete_interest_onboarding(session, username)
    session.commit()

    # v3.51.1(issue #33 §5):兴趣变更只记录,不再触发当日早报重编排——早报重编只剩
    # 读者手动「重新编排」与次日定时两个入口,今日版面落后于当前兴趣时由
    # /api/reader/briefs/today 的 interest_stale 提示读者自行决定。
    # 唯一显式例外(issue #56 方案 B「早报前置、引导内嵌」):首登引导**首次**完成且选了兴趣时,
    # 就地重编一次——新账号首屏早报是按预置来源、无兴趣编排的,引导完成后必须立刻看到兴趣起作用,
    # 否则「设置兴趣」的邀请没有兑现。跳过引导(空名单)或再次保存都不触发。失败不影响兴趣保存。
    brief_rebuilt = False
    if onboarding_transition and requested:
        try:
            outcome = personal_briefs._ensure(  # noqa: SLF001 - shared rebuild path
                session,
                username,
                reason=DigestGenerationReason.INTEREST_CHANGED,
                first_open=False,
            )
            brief_rebuilt = outcome.get("edition") is not None
        except Exception:  # noqa: BLE001 - 重编是附带动作,不能让兴趣保存失败
            logger.exception("首登引导完成后的早报重编失败(username=%s)", username)
    result = get_interests(auth=auth, session=session)
    result["onboarding_completed"] = bool(body.complete_onboarding)
    result["brief_rebuilt"] = brief_rebuilt
    return result
