"""管理员公告 Router(v3.18 互通波):管理面发布 → 读者面横幅,逐用户一次性 dismiss。

- 管理面(/api/admin/announcements,中间件 admin 前缀自动覆盖):CRUD + 启停 + 触达计数。
- 读者面(/api/reader/announcements,中间件 reader 门控自动覆盖):取 active 且本人未
  dismiss 的公告;逐条 dismiss。

content 为受限 markdown 子集(**加粗** 与 [文字](http(s)链接)),渲染端白名单解析。
设计见 docs/engage-sync-wave-plan.md。
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, func, select

from api import deps
from api.textutils import _now_iso
from models.db import (
    ANNOUNCEMENT_LEVELS,
    AnnouncementDismissRecord,
    AnnouncementRecord,
)

router = APIRouter(tags=["announcements"])

_MAX_CONTENT = 2000
_MAX_TITLE = 200


def _app():
    """延迟取 api.app(避免导入环 + 兼容测试 monkeypatch)。"""
    return importlib.import_module("api.app")


def _serialize(record: AnnouncementRecord, *, dismiss_count: Optional[int] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": record.id,
        "title": record.title,
        "content": record.content,
        "level": record.level,
        "is_active": record.is_active,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if dismiss_count is not None:
        payload["dismiss_count"] = dismiss_count
    return payload


def _validate_content(value: Any) -> str:
    """content 校验:strip 后非空且 ≤2000 字。返回 strip 后的正文。"""
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="content 不能为空")
    if len(text) > _MAX_CONTENT:
        raise HTTPException(status_code=400, detail=f"content 不能超过 {_MAX_CONTENT} 字")
    return text


def _validate_level(value: Any) -> str:
    """level 校验:必须 ∈ ANNOUNCEMENT_LEVELS。"""
    level = (value or "info").strip() or "info"
    if level not in ANNOUNCEMENT_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"level 非法,须为 {sorted(ANNOUNCEMENT_LEVELS)} 之一",
        )
    return level


def _validate_title(value: Any) -> str:
    """title 校验:≤200 字,默认空。"""
    title = (value or "").strip()
    if len(title) > _MAX_TITLE:
        raise HTTPException(status_code=400, detail=f"title 不能超过 {_MAX_TITLE} 字")
    return title


# ==================== 管理面 CRUD ====================


class AnnouncementCreate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    level: Optional[str] = None


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    level: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/api/admin/announcements")
def list_announcements(session: Session = Depends(deps.get_session)):
    """全量倒序(created_at desc),每条附 dismiss_count(该公告的 dismissal 行数)。"""
    records = session.exec(
        select(AnnouncementRecord).order_by(AnnouncementRecord.created_at.desc())
    ).all()
    counts = dict(
        session.exec(
            select(
                AnnouncementDismissRecord.announcement_id,
                func.count(),
            ).group_by(AnnouncementDismissRecord.announcement_id)
        ).all()
    )
    items = [
        _serialize(record, dismiss_count=int(counts.get(record.id, 0)))
        for record in records
    ]
    return {"items": items}


@router.post("/api/admin/announcements")
def create_announcement(
    body: AnnouncementCreate, request: Request, session: Session = Depends(deps.get_session)
):
    """发布公告:content 必填校验;level 默认 info;title 默认空;created_by = 当前登录用户名。"""
    content = _validate_content(body.content)
    level = _validate_level(body.level)
    title = _validate_title(body.title)
    now = _now_iso()
    record = AnnouncementRecord(
        title=title,
        content=content,
        level=level,
        is_active=True,
        created_by=_app().current_username(request),
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _serialize(record, dismiss_count=0)


@router.put("/api/admin/announcements/{announcement_id}")
def update_announcement(
    announcement_id: int, body: AnnouncementUpdate, session: Session = Depends(deps.get_session)
):
    """局部更新:传了才改,同样校验;更新 updated_at;404 若无。"""
    record = session.get(AnnouncementRecord, announcement_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    if body.content is not None:
        record.content = _validate_content(body.content)
    if body.level is not None:
        record.level = _validate_level(body.level)
    if body.title is not None:
        record.title = _validate_title(body.title)
    if body.is_active is not None:
        record.is_active = bool(body.is_active)
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    count = session.exec(
        select(func.count()).where(
            AnnouncementDismissRecord.announcement_id == record.id
        )
    ).one()
    return _serialize(record, dismiss_count=int(count))


@router.post("/api/admin/announcements/{announcement_id}/toggle")
def toggle_announcement(announcement_id: int, session: Session = Depends(deps.get_session)):
    """翻转 is_active,404 若无。"""
    record = session.get(AnnouncementRecord, announcement_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    record.is_active = not record.is_active
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    count = session.exec(
        select(func.count()).where(
            AnnouncementDismissRecord.announcement_id == record.id
        )
    ).one()
    return _serialize(record, dismiss_count=int(count))


@router.delete("/api/admin/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, session: Session = Depends(deps.get_session)):
    """删除公告并连带删除其全部 dismissal 行;404 若无。"""
    record = session.get(AnnouncementRecord, announcement_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    dismissals = session.exec(
        select(AnnouncementDismissRecord).where(
            AnnouncementDismissRecord.announcement_id == announcement_id
        )
    ).all()
    for dismissal in dismissals:
        session.delete(dismissal)
    session.delete(record)
    session.commit()
    return {"status": "success", "id": announcement_id}


# ==================== 读者面 ====================


@router.get("/api/reader/announcements")
def list_reader_announcements(request: Request, session: Session = Depends(deps.get_session)):
    """仅 active 且当前用户未 dismiss 的公告,按 created_at 升序(旧的在上)。"""
    username = _app().current_username(request)
    dismissed_ids = set(
        session.exec(
            select(AnnouncementDismissRecord.announcement_id).where(
                AnnouncementDismissRecord.owner_username == username
            )
        ).all()
    )
    records = session.exec(
        select(AnnouncementRecord)
        .where(AnnouncementRecord.is_active == True)  # noqa: E712 - SQLModel 需值比较
        .order_by(AnnouncementRecord.created_at.asc())
    ).all()
    items = [
        _serialize(record) for record in records if record.id not in dismissed_ids
    ]
    return {"items": items}


@router.post("/api/reader/announcements/{announcement_id}/dismiss")
def dismiss_announcement(
    announcement_id: int, request: Request, session: Session = Depends(deps.get_session)
):
    """写入 dismissal 行(幂等——已有行直接成功);公告不存在 → 404;已下线也允许 dismiss。"""
    username = _app().current_username(request)
    record = session.get(AnnouncementRecord, announcement_id)
    if record is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    existing = session.get(
        AnnouncementDismissRecord, (username, announcement_id)
    )
    if existing is None:
        session.add(
            AnnouncementDismissRecord(
                owner_username=username,
                announcement_id=announcement_id,
                dismissed_at=_now_iso(),
            )
        )
        session.commit()
    return {"status": "success", "id": announcement_id, "dismissed": True}
