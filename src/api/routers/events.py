"""事件热点榜 Router(issue #121 wave 1)。

读者面 ``/api/reader/events``(游客白名单放行,见 ``api/guest_policy.py``)与管理面 ``/api/admin/events``
(``account_admin_required`` 命中 ``/api/admin`` → 仅 admin;写操作经 ``services/admin_audit`` 入审计)。
鉴权仍由 app.py 中间件按前缀统一强制;本文件只做参数校验与服务层委托。
返回字段逐字段对齐方案 ``docs/plans/issue-121-event-layer.md`` §5.0。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from api import deps
from services import event_board as event_board_service

router = APIRouter(tags=["events"])


def _raise(exc: event_board_service.EventBoardError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ── 读者面 ──


@router.get("/api/reader/events")
def get_reader_events(
    window_hours: int = event_board_service.WINDOW_HOURS,
    limit: int = event_board_service.BOARD_LIMIT,
    session: Session = Depends(deps.get_session),
):
    """今日热点:当前窗内可见发布主体 ≥ 2 的事件,最多 10 件,不足不填。"""

    if not 1 <= window_hours <= event_board_service.MAX_WINDOW_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"窗口须在 1 到 {event_board_service.MAX_WINDOW_HOURS} 小时之间",
        )
    if not 1 <= limit <= event_board_service.BOARD_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"最多返回 {event_board_service.BOARD_LIMIT} 件",
        )
    return event_board_service.read_board(session, window_hours=window_hours, limit=limit)


@router.get("/api/reader/events/{event_id}")
def get_reader_event(event_id: int, session: Session = Depends(deps.get_session)):
    """事件详情;旧 ID 命中 redirect 时返回新事件并带 ``merged_into``。"""

    result = event_board_service.read_event(session, event_id)
    if result is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return result


# ── 管理面 ──


@router.get("/api/admin/events")
def list_admin_events(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = event_board_service.ADMIN_LIST_LIMIT,
    session: Session = Depends(deps.get_session),
):
    status_value = (status or "").strip()
    if status_value and status_value not in event_board_service.ADMIN_STATUSES:
        raise HTTPException(status_code=400, detail="status 须为 open / closed / merged")
    if not 1 <= limit <= event_board_service.ADMIN_LIST_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"limit 须在 1 到 {event_board_service.ADMIN_LIST_MAX} 之间",
        )
    return {"items": event_board_service.list_events(session, status=status_value or None, q=q, limit=limit)}


@router.post("/api/admin/events/{event_id}/merge-into/{target_event_id}")
def merge_admin_event(
    event_id: int,
    target_event_id: int,
    session: Session = Depends(deps.get_session),
):
    try:
        return event_board_service.merge_events(session, event_id, target_event_id)
    except event_board_service.EventBoardError as exc:
        _raise(exc)


@router.post("/api/admin/events/{event_id}/members/{article_id:path}/detach")
def detach_admin_event_member(
    event_id: int,
    article_id: str,
    session: Session = Depends(deps.get_session),
):
    try:
        return event_board_service.detach_member(session, event_id, article_id)
    except event_board_service.EventBoardError as exc:
        _raise(exc)
