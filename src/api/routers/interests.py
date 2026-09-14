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


# brief_rebuild_status 契约(issue #56,codex 检视 P2「有 edition ≠ 重编成功」):
# None = 未触发(含触发了但无可编排内容);否则 ∈ {ready, degraded, pending, generating, failed}——
# 封闭值域,_ensure 的其它内部状态不透传(empty_subscriptions → None,未知 → failed);
# 异常记 failed 以区分「未触发」与「触发失败」。brief_rebuilt 只在 ready/degraded 为真。
REBUILT_STATUSES = frozenset({"ready", "degraded"})
REBUILD_STATUSES = frozenset({"ready", "degraded", "pending", "generating", "failed"})


def _rebuild_after_onboarding(session: Session, auth: dict[str, Any], username: str) -> str | None:
    """首登引导首次完成后的就地重编(v3.51.1「只记录不重编」的唯一显式例外)。

    只面向 role=user——admin 的登录态恒序列化为「已完成引导」,不该经 API 走进这条例外
    (codex 检视 P2)。失败吞异常记 failed,不影响兴趣保存。
    """
    if (auth or {}).get("role") != "user":
        return None
    try:
        outcome = personal_briefs._ensure(  # noqa: SLF001 - shared rebuild path
            session,
            username,
            reason=DigestGenerationReason.INTEREST_CHANGED,
            first_open=False,
        )
        status = str(outcome.get("status") or "")
        if status == "empty_subscriptions":
            return None
        return status if status in REBUILD_STATUSES else "failed"
    except Exception:  # noqa: BLE001 - 重编是附带动作,不能让兴趣保存失败
        # 数据库型异常会让 Session 进入 pending-rollback,不回滚则后续 get_interests 直接 500
        # (兴趣与引导完成在此之前已 commit,回滚不丢数据)
        session.rollback()
        logger.exception("首登引导完成后的早报重编失败(username=%s)", username)
        return "failed"


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
        # 条件 UPDATE 与兴趣替换同一事务;rowcount 是「首次完成」的唯一凭据(并发双 PUT 只有一个为 True)
        onboarding_transition = accounts_service.complete_interest_onboarding(session, username)
    session.commit()

    # v3.51.1(issue #33 §5):兴趣变更只记录,不再触发当日早报重编排——早报重编只剩
    # 读者手动「重新编排」与次日定时两个入口,今日版面落后于当前兴趣时由
    # /api/reader/briefs/today 的 interest_stale 提示读者自行决定。
    # 唯一显式例外(issue #56 方案 B):首登引导**首次**完成且选了兴趣时就地重编一次,见 _rebuild_after_onboarding。
    rebuild_status = (
        _rebuild_after_onboarding(session, auth, username) if onboarding_transition and requested else None
    )
    result = get_interests(auth=auth, session=session)
    result["onboarding_completed"] = bool(body.complete_onboarding)
    result["brief_rebuild_status"] = rebuild_status
    result["brief_rebuilt"] = rebuild_status in REBUILT_STATUSES
    return result


@router.post("/onboarding/complete")
def complete_onboarding(
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    """只做「首登引导完成」的条件迁移,不碰兴趣集合(早报页横幅「稍后再说」专用)。

    此前「稍后再说」是 GET 兴趣 + 整集 PUT,两步之间另一标签页保存的兴趣会被整集替换掉
    (codex 检视 P2);无 body 的专用端点根治覆盖。迁移成功且账号已有关注兴趣时,与 PUT 路径
    共用同一条重编例外——读者可能先在兴趣页自动保存了几项再回早报点「稍后再说」。
    """
    personal_briefs._require_enabled(session)  # noqa: SLF001 - shared feature gate
    username = _username(auth)
    transitioned = accounts_service.complete_interest_onboarding(session, username)
    session.commit()
    rebuild_status = None
    if transitioned:
        # 「已有兴趣」按个人早报同一口径:关联标签须 active——失效/废弃标签的遗留兴趣行不算,
        # 否则会为一份注定 empty 的早报空跑一次 _ensure(codex 复检 P2)
        has_interests = session.exec(
            select(UserInterestTagRecord.tag_id)
            .join(CmsTagRecord, CmsTagRecord.id == UserInterestTagRecord.tag_id)
            .where(UserInterestTagRecord.owner_username == username, CmsTagRecord.status == "active")
            .limit(1)
        ).first() is not None
        if has_interests:
            rebuild_status = _rebuild_after_onboarding(session, auth, username)
    return {
        "onboarding_completed": True,
        "brief_rebuild_status": rebuild_status,
        "brief_rebuilt": rebuild_status in REBUILT_STATUSES,
    }
