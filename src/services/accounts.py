"""账户服务 (src/services/accounts.py)

数据库托管的登录账户：密码以 PBKDF2-HMAC-SHA256 哈希存储（标准库，无新增依赖），
集中提供哈希/校验、用户 CRUD、末位活跃管理员保护与空表自动种根管理员。

**多管理员平权（v3.19）**：管理员不再是系统唯一内置账号——任意多个管理员可互相
新建/提升/降级/停用/删除，人人平权。唯一护栏是「末位活跃管理员保护」：当某操作会
使活跃管理员数量降到 0（降级/停用/删除最后一个活跃管理员）时拒绝，保证系统始终至少
留一名活跃管理员可登录管理。停用态管理员不计入活跃数（可被删除）。竞态不设防
（单进程 SQLite 串行提交，两个并发请求同时删最后一个 admin 的窗口不构成现实威胁）。

首次启动（users 表为空）时自动落一行根管理员 admin/admin（不再从 ini 播种）；
表非空则一动不动，对存量生产零影响。username 即全局唯一身份，不可重命名。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import secrets
from typing import List, Optional

from sqlmodel import Session, func, select

from models.db import AppSettingRecord, LoginEventRecord, UserRecord

VALID_ROLES = ("admin", "user")
VALID_SURFACES = ("console", "reader")

# AI Beta 全局总开关：存 app_settings KV，默认开启。关闭即全员 AI 熔断。
AI_BETA_GLOBAL_KEY = "ai_beta_global_enabled"

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


# ==================== 末位活跃管理员保护 ====================
def count_active_admins(session: Session) -> int:
    """当前活跃管理员数量（role=admin AND is_active）。"""
    return int(
        session.exec(
            select(func.count())
            .select_from(UserRecord)
            .where(UserRecord.role == "admin", UserRecord.is_active == True)  # noqa: E712
        ).one()
    )


def _guard_last_active_admin(session: Session, record: UserRecord, action: str) -> None:
    """若 record 是活跃管理员且系统活跃管理员总数 ≤1，则拒绝该 action。

    竞态不设防（单进程 SQLite）：并发同时降级/删除最后一个 admin 的窗口不构成现实威胁。
    """
    if record.role == "admin" and record.is_active and count_active_admins(session) <= 1:
        raise AccountError(f"系统至少需保留一名活跃管理员，无法{action}最后一个管理员账户")


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
    # 幂等：角色未变直接返回，不触发末位保护（把 admin 设成 admin 不该被拒）。
    if role == record.role:
        return record
    # admin → user 降级前守卫：不能降掉最后一个活跃管理员。
    if record.role == "admin" and role != "admin":
        _guard_last_active_admin(session, record, "降级")
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
    # 停用最后一个活跃管理员前守卫。
    if not is_active:
        _guard_last_active_admin(session, record, "停用")
    record.is_active = is_active
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_ai_beta_enabled(session: Session, username: str, enabled: bool) -> UserRecord:
    """开关该账户的 AI Beta 功能（阅读器内翻译/问答）。无末位管理员约束。"""
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    record.ai_beta_enabled = bool(enabled)
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def set_default_surface(session: Session, username: str, surface: str) -> UserRecord:
    """设置账户登录默认落地界面（console 管理台 / reader 阅读器）。

    任意登录账户可为自己设置；user 恒为读者、该字段不生效但仍可写。非法值抛 AccountError。
    """
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    surface = (surface or "").strip()
    if surface not in VALID_SURFACES:
        raise AccountError(f"默认落地界面必须是 {VALID_SURFACES} 之一")
    record.default_surface = surface
    record.updated_at = _now_iso()
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


# ==================== 运维埋点 ====================
def touch_login(session: Session, username: str) -> None:
    """记录一次成功登录：刷新 last_login_at 快照 + 追加一条登录事件流。

    账户不存在时静默跳过（不阻断登录流程）。"""
    record = get_user(session, username)
    if record is None:
        return
    now = _now_iso()
    record.last_login_at = now
    session.add(record)
    session.add(LoginEventRecord(username=username, at=now))
    session.commit()


def _since(days: int) -> str:
    days = max(1, min(int(days or 30), 365))
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()


def last_login_by_user(session: Session) -> dict:
    """事件流口径的每用户最近登录时间 `{username: at}`。

    `UserRecord.last_login_at` 只是省查询的快照缓存,历史数据疤痕(迁移/手工操作)
    可能使其缺失——事件表才是最近登录的可靠源,读侧应以本函数兜底快照。"""
    rows = session.exec(
        select(LoginEventRecord.username, func.max(LoginEventRecord.at)).group_by(LoginEventRecord.username)
    ).all()
    return {username: at for username, at in rows}


def logins_by_user(session: Session, *, days: int = 30) -> dict:
    """窗口内按用户聚合登录次数 `{username: count}`（供账户列表/活跃榜富化）。"""
    since = _since(days)
    rows: List[LoginEventRecord] = list(
        session.exec(select(LoginEventRecord).where(LoginEventRecord.at >= since)).all()
    )
    out: dict = {}
    for row in rows:
        out[row.username] = out.get(row.username, 0) + 1
    return out


def summarize_user_logins(
    session: Session, username: str, *, days: int = 30, recent_limit: int = 10
) -> dict:
    """单用户登录聚合：窗口内 count + by_day 趋势 + 最近 recent_limit 次登录时间。"""
    since = _since(days)
    window_rows: List[LoginEventRecord] = list(
        session.exec(
            select(LoginEventRecord).where(
                LoginEventRecord.at >= since,
                LoginEventRecord.username == username,
            )
        ).all()
    )
    by_day: dict = {}
    for row in window_rows:
        day = (row.at or "")[:10]
        by_day[day] = by_day.get(day, 0) + 1
    by_day_list = sorted(
        [{"day": k, "logins": v} for k, v in by_day.items() if k], key=lambda x: x["day"]
    )
    recent = list(
        session.exec(
            select(LoginEventRecord)
            .where(LoginEventRecord.username == username)
            .order_by(LoginEventRecord.at.desc())
            .limit(max(1, int(recent_limit or 10)))
        ).all()
    )
    return {
        "count": len(window_rows),
        "by_day": by_day_list,
        "recent": [r.at for r in recent],
    }


def record_ai_usage(session: Session, username: str, kind: str) -> None:
    """记录一次成功的 AI 调用并刷新最近使用时间。

    仅在调用成功后写，失败不计数。账户不存在时静默跳过。轻量计数列只覆盖
    translate/ask 两个高频用途；其它 kind（如 summarize）只刷新 ai_last_used_at,
    其精确统计由 AiUsageRecord token 计量承担（运维看板的事实源）。
    """
    record = get_user(session, username)
    if record is None:
        return
    if kind == "translate":
        record.ai_translate_count = (record.ai_translate_count or 0) + 1
    elif kind == "ask":
        record.ai_ask_count = (record.ai_ask_count or 0) + 1
    record.ai_last_used_at = _now_iso()
    session.add(record)
    session.commit()


# ==================== AI Beta 全局总开关 ====================
def ai_beta_global_enabled(session: Session) -> bool:
    """读取 AI Beta 全局总开关，未设置时默认开启。"""
    record = session.get(AppSettingRecord, AI_BETA_GLOBAL_KEY)
    if record is None:
        return True
    return record.value.strip().lower() == "true"


def set_ai_beta_global_enabled(session: Session, enabled: bool) -> None:
    """写入 AI Beta 全局总开关。"""
    record = session.get(AppSettingRecord, AI_BETA_GLOBAL_KEY)
    value = "true" if enabled else "false"
    if record is None:
        record = AppSettingRecord(key=AI_BETA_GLOBAL_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def delete_user(session: Session, username: str) -> None:
    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    # 删除最后一个活跃管理员前守卫（非活跃 admin 不计入活跃数，可删）。
    _guard_last_active_admin(session, record, "删除")
    session.delete(record)
    session.commit()


# ==================== 首次启动播种 ====================
def seed_root_admin_if_empty(engine) -> bool:
    """users 表为空时落一行根管理员 admin/admin（role=admin），返回 True；否则 False。

    多管理员平权后不再从 ini 播种任意名单——新部署自动得到一个可登录的根管理员，
    登录后应立即改密并按需新建其它管理员/读者。幂等：表非空一动不动，存量生产零影响。
    """
    with Session(engine) as session:
        if session.exec(select(UserRecord)).first() is not None:
            return False
        now = _now_iso()
        session.add(UserRecord(
            username="admin",
            password_hash=hash_password("admin"),
            role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return True
