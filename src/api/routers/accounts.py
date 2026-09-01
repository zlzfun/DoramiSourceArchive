"""账户管理 Router（仅 admin）。

阶段1 从 app.py 迁出的第一个域，作为「按域拆分」的样板：
- 数据访问经 ``Depends(deps.get_session)``（动态解析 api.app.db_sink，兼容测试 monkeypatch）；
- admin 网关仍由 app.py 的中间件（account_admin_required 命中 /api/accounts）统一强制，
  本 Router 不重复声明，保持行为与迁出前完全一致；
- 路由路径保持不变（prefix=/api/accounts）。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from api import deps
from api.serializers import serialize_user
from services import accounts as accounts_service

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreateParams(BaseModel):
    username: str
    password: str
    role: str = "user"


class AccountUpdateParams(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    ai_beta_enabled: Optional[bool] = None


class AccountResetPasswordParams(BaseModel):
    new_password: str


class AccountBatchParams(BaseModel):
    usernames: list[str]
    role: Optional[str] = None
    is_active: Optional[bool] = None
    ai_beta_enabled: Optional[bool] = None


@router.get("")
def list_accounts(session: Session = Depends(deps.get_session)):
    return [serialize_user(r) for r in accounts_service.list_users(session)]


@router.post("")
def create_account(params: AccountCreateParams, session: Session = Depends(deps.get_session)):
    try:
        record = accounts_service.create_user(
            session, params.username, params.password, params.role
        )
    except accounts_service.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_user(record)


@router.post("/batch")
def batch_update_accounts(
    params: AccountBatchParams, session: Session = Depends(deps.get_session)
):
    """批量更新一组账户的角色/启停/AI 开关（v3.41 账户管理 V2，审计 M05）。

    原子语义：任一账户不存在、或整批生效后活跃管理员归零 → 整批 400 回滚，
    绝不留下半批已改的状态。语义细节见 accounts.batch_update_users。
    """
    try:
        result = accounts_service.batch_update_users(
            session,
            params.usernames,
            role=params.role,
            is_active=params.is_active,
            ai_beta_enabled=params.ai_beta_enabled,
        )
    except accounts_service.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.put("/{username}")
def update_account(
    username: str,
    params: AccountUpdateParams,
    session: Session = Depends(deps.get_session),
):
    try:
        if params.role is not None:
            accounts_service.set_role(session, username, params.role)
        if params.is_active is not None:
            accounts_service.set_active(session, username, params.is_active)
        if params.ai_beta_enabled is not None:
            accounts_service.set_ai_beta_enabled(session, username, params.ai_beta_enabled)
        record = accounts_service.get_user(session, username)
        if record is None:
            raise HTTPException(status_code=404, detail="账户不存在")
    except accounts_service.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_user(record)


@router.post("/{username}/reset-password")
def reset_account_password(
    username: str,
    params: AccountResetPasswordParams,
    session: Session = Depends(deps.get_session),
):
    if not params.new_password:
        raise HTTPException(status_code=400, detail="新密码不能为空")
    try:
        record = accounts_service.set_password(session, username, params.new_password)
    except accounts_service.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_user(record)


@router.delete("/{username}")
def delete_account(username: str, session: Session = Depends(deps.get_session)):
    # 级联收口（订阅/令牌/收藏/分享/读态/反馈/自定源退订 + 计量墓碑化）全部
    # 在 delete_user 单事务内完成（v3.40.4 M03），router 不再分段二次提交。
    try:
        accounts_service.delete_user(session, username)
    except accounts_service.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
