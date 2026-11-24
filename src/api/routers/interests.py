"""Reader-managed explicit taxonomy interests."""

from __future__ import annotations

import datetime as dt
import logging
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from sqlalchemy import or_, exists, func

from api import deps
from api.routers import personal_briefs
from models.analysis_contracts import DigestGenerationReason
from models.db import CmsTagRecord, CmsTagAliasRecord, UserInterestTagRecord
from services import accounts as accounts_service
from services import taxonomy as taxonomy_service
from services import custom_interests as custom_service
from services import reader_interests as projection


logger = logging.getLogger(__name__)

def _private_response(response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie'


router = APIRouter(
    prefix="/api/reader/interests",
    tags=["reader-interests"],
    dependencies=[Depends(deps.require_reader), Depends(_private_response)],
)


class InterestInput(BaseModel):
    """v3.56(issue #27)起兴趣只有「关注」:不再接受 stance(旧客户端传来的 stance 字段被忽略)。"""

    tag_id: int = Field(gt=0)


class InterestReplace(BaseModel):
    items: list[InterestInput] = Field(default_factory=list, max_length=200)
    complete_onboarding: bool = False
    expected_revision: int | None = Field(default=None, ge=0)


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
        "status": tag.status,
        "user_selectable": tag.user_selectable,
        **metadata,
    }


@router.get("/catalog")
def catalog(
    q: str = "",
    cursor: int = 0,
    limit: int = 100,
    auth: dict[str, Any] = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
):
    personal_briefs._require_enabled(session)  # noqa: SLF001 - shared feature gate
    if len(q) > 100 or cursor < 0 or not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="搜索参数超出范围")
    if q.strip():
        term = "%" + q.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        alias = select(CmsTagAliasRecord.id).where(CmsTagAliasRecord.tag_id == CmsTagRecord.id,
            func.lower(CmsTagAliasRecord.alias).like(term, escape="\\"))
        rows = session.exec(select(CmsTagRecord).where(
            CmsTagRecord.status == "active", CmsTagRecord.user_selectable.is_(True), CmsTagRecord.id > cursor,
            or_(func.lower(CmsTagRecord.name_zh).like(term, escape="\\"),
                func.lower(CmsTagRecord.name_en).like(term, escape="\\"),
                func.lower(CmsTagRecord.code).like(term, escape="\\"), exists(alias)),
        ).order_by(CmsTagRecord.id).limit(limit + 1)).all()
        items = [_tag_payload(row) for row in rows[:limit]]
        return {"items": items, "facets": {kind: [item for item in items if item["kind"] == kind]
            for kind in taxonomy_service.INTEREST_CATALOG_DEFAULT_LIMITS},
            "next_cursor": rows[limit-1].id if len(rows) > limit else None, "query": q.strip()}
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
        "next_cursor": None,
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
        "capabilities": {"interest_match_projection": 1, "custom_interest_filter": True, "match_contract": 1},
        "revision": custom_service.state_revision(session, _username(auth)),
        "custom_items": [custom_service.payload(row) for row in custom_service.list_conditions(session, _username(auth))],
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
    custom_service.lock_mutation(session)
    if body.expected_revision is not None and body.expected_revision != custom_service.state_revision(session, username):
        raise HTTPException(status_code=409, detail="兴趣已在其他页面更新，请刷新后重试")
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
    changed = set(existing) != set(requested) or any(
        row.stance != "follow" or row.priority != "normal" or row.source != "explicit" for row in existing.values()
    )
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
    if changed:
        custom_service.bump_revision(session, username)
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
        if has_interests or custom_service.list_conditions(session, username, active_only=True):
            rebuild_status = _rebuild_after_onboarding(session, auth, username)
    return {
        "onboarding_completed": True,
        "brief_rebuild_status": rebuild_status,
        "brief_rebuilt": rebuild_status in REBUILT_STATUSES,
    }


@router.post("/custom/preview")
def preview_custom_interest(body: custom_service.ConditionInput,
    auth: dict[str, Any] = Depends(deps.require_reader), session: Session = Depends(deps.get_session)):
    personal_briefs._require_enabled(session)
    return custom_service.preview(session, _username(auth), body.condition)


def _save_custom(body, auth, session, condition_id=None):
    personal_briefs._require_enabled(session)
    try:
        row = custom_service.save_condition(session, _username(auth), body, condition_id)
        session.commit()
        return {"item": custom_service.payload(row), "revision": custom_service.state_revision(session, _username(auth))}
    except custom_service.RevisionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/custom")
def create_custom_interest(body: custom_service.ConditionInput,
    auth: dict[str, Any] = Depends(deps.require_reader), session: Session = Depends(deps.get_session)):
    return _save_custom(body, auth, session)


@router.put("/custom/{condition_id}")
def update_custom_interest(condition_id: int, body: custom_service.ConditionInput,
    auth: dict[str, Any] = Depends(deps.require_reader), session: Session = Depends(deps.get_session)):
    return _save_custom(body, auth, session, condition_id)


@router.delete("/custom/{condition_id}")
def delete_custom_interest(condition_id: int, expected_revision: int = Query(ge=1),
    auth: dict[str, Any] = Depends(deps.require_reader), session: Session = Depends(deps.get_session)):
    personal_briefs._require_enabled(session)
    try:
        custom_service.delete_condition(session, _username(auth), condition_id, expected_revision)
        session.commit()
        return {"revision": custom_service.state_revision(session, _username(auth))}
    except custom_service.RevisionConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/custom/{condition_id}/matches")
def custom_interest_matches(condition_id: int, cursor: int = Query(default=0, ge=0),
    pagination_token: str | None = None, match_contract: int | None = None,
    auth: dict[str, Any] = Depends(deps.require_reader), session: Session = Depends(deps.get_session)):
    personal_briefs._require_enabled(session)
    username = _username(auth)
    if match_contract not in {None, 1}:
        raise HTTPException(400, '无效的 match_contract')
    projection.require_custom(session, username, condition_id)
    cache = {}
    all_rows = custom_service.current_matches(session, username, condition_ids=[condition_id],
        limit=None, snapshot_cache=cache, order_by_id=True)
    token = projection.pagination_token(session, username,
        query={'kind': 'custom', 'id': condition_id, 'order': 'match_id_asc'},
        results=[(m.id, a.id, m.fingerprint) for m, _, a in all_rows], validated_matches=all_rows)
    projection.validate_page_token(pagination_token, token, required=match_contract == 1 and cursor > 0)
    rows = [(m, c, a) for m, c, a in all_rows if m.id > cursor]
    items = []
    for match, condition, article in rows[:50]:
        raw = json.loads(match.evidence_json)
        method, evidence = projection.safe_evidence(raw, article,
            custom_service.bound_evidence_snapshot(session, article, raw, cache=cache))
        typed = projection._match({'kind': 'custom', 'id': str(condition.id)}, condition.name,
            condition.revision, 'current', method, evidence)
        items.append({'article_id': article.id, 'title': article.title, 'source_id': article.source_id,
                      'outcome': match.outcome, 'evidence': evidence, 'interest_matches': [typed]})
    return {'items': items, 'next_cursor': rows[49][0].id if len(rows) > 50 else None,
            'pagination_token': token, 'total': len(all_rows), 'count_status': 'exact'}
