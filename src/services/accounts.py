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

from sqlmodel import Session, delete, func, select, update

from models.db import (
    AiUsageRecord,
    AnnouncementDismissRecord,
    AppSettingRecord,
    ArticleShareRecord,
    FeedbackRecord,
    LoginEventRecord,
    ReaderArticleReadStateRecord,
    ReaderFavoriteRecord,
    ReaderFeedTokenRecord,
    ReaderReadCursorRecord,
    ReaderReadRecord,
    ReaderSubscriptionRecord,
    UserRecord,
)

VALID_ROLES = ("admin", "user")
VALID_SURFACES = ("console", "reader")

# AI Beta 全局总开关：存 app_settings KV，默认开启。关闭即全员 AI 熔断。
AI_BETA_GLOBAL_KEY = "ai_beta_global_enabled"
# 新账号 AI 默认值:存 app_settings KV,默认开——新建账户的逐账户 AI 开关按此播种。
# 只影响「创建时刻」的初值,存量账户与后续逐账户开关互不干扰;总闸仍是即时熔断层。
AI_BETA_NEW_USER_DEFAULT_KEY = "ai_beta_new_user_default"
# 读者面 AI 全局日 token 预算(v3.34):存 app_settings KV,0 = 不限(默认)。
# 与逐用户日调用限额(routers/reader._AI_DAILY_CALL_LIMITS)互补——那是防单账户
# 刷爆,这是护全站总成本(多账户/IM bot 代答渠道的放大器);范式同 x_api 月预算。
AI_DAILY_TOKEN_BUDGET_KEY = "ai_beta_daily_token_budget"
# 计入日预算的用途:读者面三件套。日报等系统任务是 admin 排程的,不受此闸。
READER_AI_BUDGET_PURPOSES = ("translate", "ask", "summarize")

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


def new_session_epoch() -> str:
    """会话世代值（v3.40.4 M04）：建号/改密时轮换。

    登录 token 携带签发时的世代，校验时须与 users 行一致——密码重置即吊销既有
    Cookie；建号随机初始化使删号后同名重建不复活旧 Cookie。存量行为 ""，旧 token
    无世代字段按 "" 对待（升级不强制重登，首次改密后收紧）。
    """
    return secrets.token_hex(8)


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
        ai_beta_enabled=ai_beta_new_user_default(session),
        session_epoch=new_session_epoch(),
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
    # 改密即轮换会话世代：该账户既有登录 Cookie 下一次请求即失效（M04）。
    record.session_epoch = new_session_epoch()
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


# 批量操作上限：一次请求最多动这么多账户（150 人规模一页全选远在其下，防误提交巨批）。
BATCH_UPDATE_MAX = 500


def batch_update_users(
    session: Session,
    usernames: List[str],
    *,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    ai_beta_enabled: Optional[bool] = None,
) -> dict:
    """批量更新一组账户的角色/启停/AI 开关（v3.41 账户管理 V2，审计 M05）。

    **原子语义（全成或全不成）**：任一账户不存在、或整批生效后活跃管理员数将归零，
    整批拒绝（AccountError）并回滚——比逐个套用单账户守卫更严也更可预期：
    「批量停用全部管理员」直接拒绝，而不是停到剩最后一个才报错、留下半批已停的状态。
    末位保护以**整批后的终态**裁决，天然覆盖「两个 admin 各自被批量停用」类组合。
    单事务一次 commit；幂等项（值未变）不计入 updated 也不 bump updated_at。
    """
    names = [str(u or "").strip() for u in usernames]
    names = [u for u in names if u]
    if not names:
        raise AccountError("请至少选择一个账户")
    if len(names) > BATCH_UPDATE_MAX:
        raise AccountError(f"单次批量最多 {BATCH_UPDATE_MAX} 个账户")
    if role is None and is_active is None and ai_beta_enabled is None:
        raise AccountError("请指定要批量更新的字段")
    if role is not None:
        role = _normalize_role(role)

    unique_names = list(dict.fromkeys(names))
    records = [get_user(session, u) for u in unique_names]
    missing = [u for u, r in zip(unique_names, records) if r is None]
    if missing:
        raise AccountError(f"账户不存在：{'、'.join(missing[:5])}{'…' if len(missing) > 5 else ''}")

    now = _now_iso()
    updated = 0
    try:
        for record in records:
            changed = False
            if role is not None and record.role != role:
                record.role = role
                changed = True
            if is_active is not None and record.is_active != bool(is_active):
                record.is_active = bool(is_active)
                changed = True
            if ai_beta_enabled is not None and record.ai_beta_enabled != bool(ai_beta_enabled):
                record.ai_beta_enabled = bool(ai_beta_enabled)
                changed = True
            if changed:
                record.updated_at = now
                session.add(record)
                updated += 1
        # 末位保护按整批终态裁决（角色/启停都可能触及）。
        if (role is not None or is_active is not None) and count_active_admins(session) < 1:
            raise AccountError("系统至少需保留一名活跃管理员，该批量操作会移除全部活跃管理员")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"updated": updated, "unchanged": len(records) - updated, "total": len(records)}


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


def last_login_for_user(session: Session, username: str) -> Optional[str]:
    """事件流口径的单用户最近登录时间（v3.43 审计 M22）。

    单用户抽屉的快照兜底此前误用 `last_login_by_user`——为看一个人的时间对
    事件表做全表 GROUP BY；这里改成对该用户名的一次 MAX 标量查询。"""
    return session.exec(
        select(func.max(LoginEventRecord.at)).where(LoginEventRecord.username == username)
    ).one()


def logins_by_user(session: Session, *, days: int = 30) -> dict:
    """窗口内按用户聚合登录次数 `{username: count}`（供账户列表/活跃榜富化）。

    v3.41（审计 M07）：SQL 端 GROUP BY；排除删号墓碑（`deleted:*`）。
    """
    since = _since(days)
    rows = session.exec(
        select(LoginEventRecord.username, func.count())
        .where(
            LoginEventRecord.at >= since,
            LoginEventRecord.username.not_like(f"{DELETED_USER_PREFIX}%"),
        )
        .group_by(LoginEventRecord.username)
    ).all()
    return {username: int(count or 0) for username, count in rows}


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


def ai_beta_new_user_default(session: Session) -> bool:
    """新账号 AI 默认值，未设置时默认开启（创建账户时播种逐账户开关）。"""
    record = session.get(AppSettingRecord, AI_BETA_NEW_USER_DEFAULT_KEY)
    if record is None:
        return True
    return record.value.strip().lower() == "true"


def set_ai_beta_new_user_default(session: Session, enabled: bool) -> None:
    """写入新账号 AI 默认值。只影响此后新建的账户，存量账户不动。"""
    record = session.get(AppSettingRecord, AI_BETA_NEW_USER_DEFAULT_KEY)
    value = "true" if enabled else "false"
    if record is None:
        record = AppSettingRecord(key=AI_BETA_NEW_USER_DEFAULT_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def ai_daily_token_budget(session: Session) -> int:
    """读者面 AI 全局日 token 预算；0 = 不限（默认/坏值回落）。"""
    record = session.get(AppSettingRecord, AI_DAILY_TOKEN_BUDGET_KEY)
    if record is None:
        return 0
    try:
        return max(0, int((record.value or "").strip() or "0"))
    except ValueError:
        return 0


def set_ai_daily_token_budget(session: Session, budget: int) -> None:
    """写入读者面 AI 全局日 token 预算（0 = 不限）。"""
    value = str(max(0, int(budget)))
    record = session.get(AppSettingRecord, AI_DAILY_TOKEN_BUDGET_KEY)
    if record is None:
        record = AppSettingRecord(key=AI_DAILY_TOKEN_BUDGET_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def reader_ai_tokens_today(session: Session) -> int:
    """今日读者面 AI（translate/ask/summarize）全账户合计 token 消耗。"""
    today = datetime.date.today().isoformat()
    used = session.exec(
        select(func.coalesce(func.sum(AiUsageRecord.total_tokens), 0)).where(
            AiUsageRecord.day == today,
            AiUsageRecord.purpose.in_(READER_AI_BUDGET_PURPOSES),
        )
    ).one()
    return int(used or 0)


def reader_ai_budget_exhausted(session: Session) -> bool:
    """全局日预算已配置且今日读者面 AI 消耗已达上限。"""
    budget = ai_daily_token_budget(session)
    return bool(budget) and reader_ai_tokens_today(session) >= budget


# 删号计量墓碑前缀：用户名不允许含冒号（create_user 拒绝），墓碑名天然不与任何
# 真实账户冲突，也无法登录（users 表无此行）。
DELETED_USER_PREFIX = "deleted:"


def delete_user(session: Session, username: str) -> None:
    """删除账户并收口其全部身份数据（v3.40.4 审计 M03，单事务原子提交）。

    语义（2026-09-01 拍板）：
    - **个人资产物理删除并即时失效**：订阅行与聚合令牌、收藏、公开分享链接
      （行删即 404）、逐篇读态与按源水位、反馈、公告 dismiss；该用户订阅的
      自定源随退订走既有「无人订阅即物理删」语义。
    - **计量历史墓碑化**：AI 用量/阅读计量/登录事件改写为 ``deleted:<原名>``——
      运维看板与成本历史保持完整真实，同名重建不继承任何历史。
    - **保留原名**：管理审计日志（操作历史即历史）与 JobRecord.created_by。
    此前版本只删账户行+订阅+令牌且分两次提交——收藏/分享/计量等残留可被同名
    重建继承，后段失败还会部分删除；现全部改在一个事务内，末尾一次 commit。
    """
    # 函数级导入：user_sources 依赖 feedparser/httpx，避免账户服务的轻量消费方
    # 背上抓取栈依赖（models → services 单向，无环）。
    from services import user_sources as user_sources_service

    record = get_user(session, username)
    if record is None:
        raise AccountError(f"账户 '{username}' 不存在")
    # 删除最后一个活跃管理员前守卫（非活跃 admin 不计入活跃数，可删）。
    _guard_last_active_admin(session, record, "删除")

    tombstone = f"{DELETED_USER_PREFIX}{username}"

    # ① 自定源级联（须先于订阅行整批删除——退订语义要读订阅行判断剩余订阅者）。
    user_sources_service.purge_account_user_sources(session, username)
    user_sources_service.tombstone_owner_username(session, username, tombstone)

    # ② 个人资产物理删除。
    for model, owner_col in (
        (ReaderSubscriptionRecord, ReaderSubscriptionRecord.owner_username),
        (ReaderFavoriteRecord, ReaderFavoriteRecord.owner_username),
        (ArticleShareRecord, ArticleShareRecord.owner_username),
        (ReaderArticleReadStateRecord, ReaderArticleReadStateRecord.owner_username),
        (ReaderReadCursorRecord, ReaderReadCursorRecord.owner_username),
        (FeedbackRecord, FeedbackRecord.owner_username),
        (AnnouncementDismissRecord, AnnouncementDismissRecord.owner_username),
    ):
        session.exec(delete(model).where(owner_col == username))
    feed_token = session.get(ReaderFeedTokenRecord, username)
    if feed_token is not None:
        session.delete(feed_token)

    # ③ 计量历史墓碑化。
    for model, user_col in (
        (AiUsageRecord, AiUsageRecord.username),
        (ReaderReadRecord, ReaderReadRecord.username),
        (LoginEventRecord, LoginEventRecord.username),
    ):
        session.exec(update(model).where(user_col == username).values(username=tombstone))

    # ④ 账户行本体；一次 commit 原子落地（任一步失败即整体回滚，无部分删除）。
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
            ai_beta_enabled=ai_beta_new_user_default(session),
            session_epoch=new_session_epoch(),
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return True
