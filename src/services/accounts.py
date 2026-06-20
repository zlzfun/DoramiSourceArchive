"""账户服务 (src/services/accounts.py)

数据库托管的登录账户：密码以 PBKDF2-HMAC-SHA256 哈希存储（标准库，无新增依赖），
集中提供哈希/校验、用户 CRUD、末位管理员保护与首次启动播种。

config 的 [auth] admin_users/user_users 仅在 users 表为空时作为初始种子；
之后账户以本表为准（改 ini 不再生效）。username 即全局唯一身份，不可重命名。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import secrets
from typing import List, Optional

from sqlmodel import Session, select

from models.db import UserRecord

VALID_ROLES = ("admin", "user")

# PBKDF2 参数
_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_SALT_BYTES = 16
# 用户不存在时用于抹平时序、避免用户枚举的占位哈希。
_DUMMY_HASH = None


class AccountError(ValueError):
    """账户操作的业务错误（用户名冲突、末位管理员保护等）。"""


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


# ==================== 密码哈希（PBKDF2-HMAC-SHA256） ====================
def hash_password(plain: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """返回编码串 pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>。"""
    if not plain:
        raise AccountError("密码不能为空")
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return "{}${}${}${}".format(
        _PBKDF2_ALGO,
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(plain: str, encoded: str) -> bool:
    """恒定时间校验明文与编码串；编码非法或为空一律返回 False。"""
    if not plain or not encoded:
        return False
    try:
        algo, iter_str, salt_b64, hash_b64 = encoded.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def _dummy_hash() -> str:
    """惰性生成一个固定的占位哈希，供登录失败路径做等时校验。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dorami-dummy-password-placeholder")
    return _DUMMY_HASH


def verify_against_dummy(plain: str) -> None:
    """对占位哈希跑一次校验，仅用于抹平用户不存在时的响应时序。"""
    verify_password(plain or "x", _dummy_hash())


# ==================== 用户查询 ====================
def get_user(session: Session, username: str) -> Optional[UserRecord]:
    if not username:
        return None
    return session.get(UserRecord, username)

def get_active_user(session: Session, username: str) -> Optional[UserRecord]:
    record = get_user(session, username)
    return record if record and record.is_active else None


def list_users(session: Session) -> List[UserRecord]:
    return list(session.exec(select(UserRecord).order_by(UserRecord.username)).all())


def count_active_admins(session: Session, *, exclude: Optional[str] = None) -> int:
    rows = session.exec(
        select(UserRecord).where(UserRecord.role == "admin", UserRecord.is_active == True)  # noqa: E712
    ).all()
    return sum(1 for r in rows if r.username != exclude)


# ==================== 用户增删改 ====================
def _normalize_role(role: str) -> str:
    role = (role or "").strip()
    if role not in VALID_ROLES:
        raise AccountError(f"角色必须是 {VALID_ROLES} 之一")
    return role


def create_user(session: Session, username: str, password: str, role: str) -> UserRecord:
    username = (username or "").strip()
    if not username:
        raise AccountError("用户名不能为空")
    if ":" in username:
        raise AccountError("用户名不能包含冒号")
    role = _normalize_role(role)
    if session.get(UserRecord, username) is not None:
        raise AccountError(f"账户 '{username}' 已存在")
    now = _now_iso()
    record = UserRecord(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_password(session: Session, username: str, new_password: str) -> UserRecord:
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    record.password_hash = hash_password(new_password)
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_avatar(session: Session, username: str, avatar: Optional[str]) -> UserRecord:
    """更新账户头像；avatar 为空字符串/None 表示清除（回退到首字母占位）。"""
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    record.avatar = avatar or None
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_role(session: Session, username: str, role: str) -> UserRecord:
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    role = _normalize_role(role)
    # 降级最后一个 active admin → 拒绝，避免系统失去管理员。
    if record.role == "admin" and role != "admin" and record.is_active:
        if count_active_admins(session, exclude=username) == 0:
            raise AccountError("不能降级最后一个启用的管理员")
    record.role = role
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_active(session: Session, username: str, is_active: bool) -> UserRecord:
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    if not is_active and record.role == "admin" and record.is_active:
        if count_active_admins(session, exclude=username) == 0:
            raise AccountError("不能停用最后一个启用的管理员")
    record.is_active = is_active
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def delete_user(session: Session, username: str) -> None:
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    if record.role == "admin" and record.is_active:
        if count_active_admins(session, exclude=username) == 0:
            raise AccountError("不能删除最后一个启用的管理员")
    session.delete(record)
    session.commit()


# ==================== 首次启动播种 ====================
def seed_users_if_empty(engine, auth_config) -> int:
    """users 表为空时，从 config 的 admin_users/user_users 播种。

    幂等：表非空直接返回 0。返回新建账户数。
    """
    created = 0
    with Session(engine) as session:
        if session.exec(select(UserRecord)).first() is not None:
            return 0
        now = _now_iso()
        seen: set[str] = set()

        def _seed(credentials, role: str) -> None:
            nonlocal created
            for credential in credentials or []:
                username = (credential.username or "").strip()
                if not username or username in seen:
                    continue
                seen.add(username)
                session.add(UserRecord(
                    username=username,
                    password_hash=hash_password(credential.password),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                ))
                created += 1

        _seed(auth_config.admin_users, "admin")
        _seed(auth_config.user_users, "user")
        session.commit()
    return created
