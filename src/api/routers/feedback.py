"""读者反馈 Router(v3.18 互通波):读者提交诉求/问题,管理员收件处理并回复。

- 读者面(/api/reader/feedback,中间件 reader 门控自动覆盖):提交/查看自己的/撤回 open 态。
- 管理面(/api/admin/feedback,中间件 admin 前缀自动覆盖):全量列表 + 状态流转 + 回复。

设计见 docs/engage-sync-wave-plan.md。数据访问经 deps.get_session()。
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, func, select

from api import deps
from api.textutils import _now_iso
from models.db import FEEDBACK_CATEGORIES, FEEDBACK_STATUSES, FeedbackRecord

router = APIRouter(tags=["feedback"])

_MAX_TEXT_LENGTH = 2000


def _app():
    """延迟取 api.app(避免导入环 + 兼容测试 monkeypatch)。"""
    return importlib.import_module("api.app")


def _serialize(record: FeedbackRecord) -> Dict[str, Any]:
    """反馈在读者面与管理面的统一序列化格式。"""
    return {
        "id": record.id,
        "owner_username": record.owner_username,
        "category": record.category,
        "content": record.content,
        "status": record.status,
        "admin_note": record.admin_note,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _validate_category(value: Any) -> str:
    category = (value or "").strip()
    if category not in FEEDBACK_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category 非法,须为 {sorted(FEEDBACK_CATEGORIES)} 之一",
        )
    return category


def _validate_content(value: Any) -> str:
    content = (value or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    if len(content) > _MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"content 不能超过 {_MAX_TEXT_LENGTH} 字",
        )
    return content


def _validate_status(value: Any) -> str:
    status = (value or "").strip()
    if status not in FEEDBACK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status 非法,须为 {sorted(FEEDBACK_STATUSES)} 之一",
        )
    return status


class FeedbackCreate(BaseModel):
    category: Optional[str] = None
    content: Optional[str] = None


class FeedbackStatusUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None


# ==================== 读者面 ====================


@router.post("/api/reader/feedback")
def create_feedback(
    body: FeedbackCreate,
    request: Request,
    session: Session = Depends(deps.get_session),
):
    """提交反馈;同一用户按 created_at 当日日期前缀最多 10 条。"""
    username = _app().current_username(request)
    category = _validate_category(body.category)
    content = _validate_content(body.content)
    now = _now_iso()
    today = now[:10]
    submitted_today = session.exec(
        select(func.count())
        .select_from(FeedbackRecord)
        .where(
            FeedbackRecord.owner_username == username,
            FeedbackRecord.created_at.like(f"{today}%"),
        )
    ).one()
    if int(submitted_today) >= 10:
        raise HTTPException(status_code=429, detail="今日反馈提交次数已达上限")

    record = FeedbackRecord(
        owner_username=username,
        category=category,
        content=content,
        status="open",
        admin_note="",
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _serialize(record)


@router.get("/api/reader/feedback")
def list_reader_feedback(
    request: Request,
    session: Session = Depends(deps.get_session),
):
    """仅返回当前用户自己的反馈,按创建时间倒序。"""
    username = _app().current_username(request)
    records = session.exec(
        select(FeedbackRecord)
        .where(FeedbackRecord.owner_username == username)
        .order_by(FeedbackRecord.created_at.desc())
    ).all()
    return {"items": [_serialize(record) for record in records]}


@router.delete("/api/reader/feedback/{feedback_id}")
def withdraw_feedback(
    feedback_id: int,
    request: Request,
    session: Session = Depends(deps.get_session),
):
    """仅 owner 可撤回 open 反馈;越权与不存在统一 404,避免泄漏。"""
    username = _app().current_username(request)
    record = session.exec(
        select(FeedbackRecord).where(
            FeedbackRecord.id == feedback_id,
            FeedbackRecord.owner_username == username,
        )
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    if record.status != "open":
        raise HTTPException(status_code=409, detail="仅可撤回待处理反馈")
    session.delete(record)
    session.commit()
    return {"status": "success", "id": feedback_id}


# ==================== 管理面 ====================


@router.get("/api/admin/feedback")
def list_admin_feedback(
    status: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(deps.get_session),
):
    """全量反馈列表;counts 始终聚合完整数据集,不受 status 过滤影响。"""
    normalized_status = None
    if status is not None:
        normalized_status = _validate_status(status)
    safe_limit = min(max(int(limit), 1), 500)

    query = select(FeedbackRecord)
    if normalized_status is not None:
        query = query.where(FeedbackRecord.status == normalized_status)
    records = session.exec(
        query.order_by(FeedbackRecord.created_at.desc()).limit(safe_limit)
    ).all()

    counts = {item_status: 0 for item_status in sorted(FEEDBACK_STATUSES)}
    status_rows = session.exec(
        select(FeedbackRecord.status, func.count())
        .select_from(FeedbackRecord)
        .group_by(FeedbackRecord.status)
    ).all()
    for item_status, count in status_rows:
        counts[item_status] = int(count)
    counts["total"] = sum(counts[item_status] for item_status in FEEDBACK_STATUSES)
    return {
        "items": [_serialize(record) for record in records],
        "counts": counts,
    }


@router.post("/api/admin/feedback/{feedback_id}/status")
def update_feedback_status(
    feedback_id: int,
    body: FeedbackStatusUpdate,
    session: Session = Depends(deps.get_session),
):
    """管理员流转状态并可覆盖回复;任一变更都会刷新 updated_at。"""
    status = _validate_status(body.status)
    if body.admin_note is not None and len(body.admin_note) > _MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"admin_note 不能超过 {_MAX_TEXT_LENGTH} 字",
        )

    record = session.get(FeedbackRecord, feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    record.status = status
    if body.admin_note is not None:
        record.admin_note = body.admin_note
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return _serialize(record)
