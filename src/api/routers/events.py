"""事件热点榜 Router(issue #121 wave 1)。

读者面 ``/api/reader/events``(游客白名单放行,见 ``api/guest_policy.py``)与管理面 ``/api/admin/events``
(``account_admin_required`` 命中 ``/api/admin`` → 仅 admin;写操作经 ``services/admin_audit`` 入审计)。
鉴权仍由 app.py 中间件按前缀统一强制;本文件只做参数校验与服务层委托。
返回字段逐字段对齐方案 ``docs/plans/issue-121-event-layer.md`` §5.0。

对外交付面 ``/api/public/feed/events``(issue #310):``/api/public/*`` 在中间件里免会话,这里自己核对
``dfeed_`` 个人聚合令牌(与 ``/api/public/feed/articles`` 同一枚、同一解析口);榜单是全站口径,令牌只管
「能不能拿」,不按订阅范围裁剪。数据与读者面同源(``event_board.read_board`` / ``read_event``)。
契约见 ``docs/contracts/reader_subscription.md``「Hot Events Board」。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from api import deps
from api.feed_service import resolve_feed_token_owner
from api.tokens import read_bearer_or_query_token
from services import event_board as event_board_service

router = APIRouter(tags=["events"])


def _raise(exc: event_board_service.EventBoardError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ── 读者面 ──


def _check_window(window_hours: int) -> None:
    if not 1 <= window_hours <= event_board_service.MAX_WINDOW_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"窗口须在 1 到 {event_board_service.MAX_WINDOW_HOURS} 小时之间",
        )


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= event_board_service.BOARD_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"最多返回 {event_board_service.BOARD_LIMIT} 件",
        )


@router.get("/api/reader/events")
def get_reader_events(
    window_hours: int = event_board_service.READER_WINDOW_HOURS,
    limit: int = event_board_service.BOARD_LIMIT,
    session: Session = Depends(deps.get_session),
):
    """热点事件:当前窗内(默认 7 天,榜单页可切近 2 天)可见发布主体 ≥ 2 的事件,最多 10 件,不足不填。"""

    _check_window(window_hours)
    _check_limit(limit)
    return event_board_service.read_board(session, window_hours=window_hours, limit=limit)


@router.get("/api/reader/events/{event_id}")
def get_reader_event(
    event_id: int,
    window_hours: int = event_board_service.READER_WINDOW_HOURS,
    session: Session = Depends(deps.get_session),
):
    """事件详情;旧 ID 命中 redirect 时返回新事件并带 ``merged_into``;名次按 ``window_hours`` 那个榜算。"""

    _check_window(window_hours)
    result = event_board_service.read_event(session, event_id, window_hours=window_hours)
    if result is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return result


# ── 对外交付面(dfeed_ 令牌) ──


def _require_feed_token(request: Request, session: Session) -> None:
    """核对个人聚合令牌;先于参数取值校验——令牌无效时不回窗口 / 条数范围类的 400。"""

    if not resolve_feed_token_owner(session, read_bearer_or_query_token(request)):
        raise HTTPException(status_code=401, detail="个人聚合接口令牌无效")


@router.get("/api/public/feed/events")
def get_public_feed_events(
    request: Request,
    window_hours: int = event_board_service.READER_WINDOW_HOURS,
    limit: int = event_board_service.BOARD_LIMIT,
    include_members: bool = False,
    session: Session = Depends(deps.get_session),
):
    """热点事件榜的令牌化拉取:与读者面榜单同一份数据;``include_members=true`` 时每件事带成员报道。"""

    _require_feed_token(request, session)
    _check_window(window_hours)
    _check_limit(limit)
    board = event_board_service.read_board(
        session, window_hours=window_hours, limit=limit, include_members=include_members
    )
    return {"status": "success", "count": len(board["items"]), **board}


@router.get("/api/public/feed/events/{event_id}")
def get_public_feed_event(
    event_id: int,
    request: Request,
    window_hours: int = event_board_service.READER_WINDOW_HOURS,
    session: Session = Depends(deps.get_session),
):
    """单个事件的令牌化拉取:榜单字段 + 成员报道 + 后续事件;合并掉的旧 ID 返回新事件并带 ``merged_into``。"""

    _require_feed_token(request, session)
    _check_window(window_hours)
    result = event_board_service.read_event(session, event_id, window_hours=window_hours)
    if result is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"status": "success", "event": result}


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
