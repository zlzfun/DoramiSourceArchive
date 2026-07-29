"""文章分享（读者把一篇内容发给同事）。

两档分享，只有第二档落到本模块：

1. **站内深链** —— 纯前端 URL（``#/reader/a/{article_id}``），收到的人登录后直达该篇。
   零外泄、无需后端，故本模块不涉及。
2. **公开链接** —— 免登录只读页（``#/s/{token}``）。签发一条 ``ArticleShareRecord``，
   访客经 ``GET /api/public/share/{token}`` 取内容。

护栏（逐条对应一个真实风险）：
- 令牌 ``dshr_`` + 32 字节 urlsafe 随机，不可枚举；
- 有效期可选 7/30 天或永久，过期即失效；签发者可随时撤销（软删，保留触达计数）；
- 全局总闸 ``public_share_enabled``（KV，默认开）——出事时管理员一键停掉所有公开链接，
  不必逐条撤销、也不动已签发的数据；
- 被隐藏源（source_visibility）的文章一律不可分享、既有链接立即 404，与
  「读者面隐藏 = 内容交付全量排除」同口径；
- 单人单日签发上限，防脚本刷量把归档整库摊开成公开链接。
"""

import datetime
import secrets
from typing import List, Optional, Tuple

from sqlmodel import Session, select

from models.db import AppSettingRecord, ArticleRecord, ArticleShareRecord

# 全局总闸：管理员可一键停用所有公开分享链接（站内深链不受影响）。
PUBLIC_SHARE_ENABLED_KEY = "public_share_enabled"

# 有效期档位（天）。None = 永久。前端选择器与本表同源。
SHARE_EXPIRY_CHOICES = (7, 30, None)

# 单人单日签发上限：正常分享每天个位数，30 足够宽裕又能拦住脚本刷量。
DAILY_SHARE_LIMIT = 30

TOKEN_PREFIX = "dshr_"


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def public_share_enabled(session: Session) -> bool:
    """公开分享总闸；未配置视为开启（开箱即用）。"""
    record = session.get(AppSettingRecord, PUBLIC_SHARE_ENABLED_KEY)
    if record is None or record.value is None:
        return True
    return str(record.value).strip().lower() in {"1", "true", "yes", "on"}


def set_public_share_enabled(session: Session, enabled: bool) -> bool:
    record = session.get(AppSettingRecord, PUBLIC_SHARE_ENABLED_KEY)
    value = "true" if enabled else "false"
    if record is None:
        record = AppSettingRecord(key=PUBLIC_SHARE_ENABLED_KEY, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()
    return enabled


def generate_share_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def is_expired(record: ArticleShareRecord, *, now: Optional[datetime.datetime] = None) -> bool:
    if not record.expires_at:
        return False
    try:
        expires = datetime.datetime.fromisoformat(record.expires_at)
    except ValueError:
        # 坏数据按已过期处理：宁可让链接失效，也不要让不可解析的期限变成永久有效。
        return True
    return expires <= (now or _now())


def is_live(record: ArticleShareRecord, *, now: Optional[datetime.datetime] = None) -> bool:
    return record.revoked_at is None and not is_expired(record, now=now)


def count_today(session: Session, username: str) -> int:
    """当日已签发条数（按本地日历日，与限额提示的口径一致）。"""
    day_start = _iso(_now().replace(hour=0, minute=0, second=0, microsecond=0))
    rows = session.exec(
        select(ArticleShareRecord).where(
            ArticleShareRecord.owner_username == username,
            ArticleShareRecord.created_at >= day_start,
        )
    ).all()
    return len(rows)


def create_share(
    session: Session,
    *,
    article_id: str,
    username: str,
    expires_in_days: Optional[int],
) -> ArticleShareRecord:
    """签发一条公开分享。调用方需已校验文章存在、源未隐藏、总闸开启、未超限额。

    **一篇一链（rotate 语义）**：同一用户对同一篇文章至多一条存活链接——再次生成时
    先撤销旧的（软删，保留触达计数），与 feed token rotate 同构。否则「重复点生成」
    会堆出一串平行有效的链接：用户自己都数不清发出去几条、每条都是独立暴露面，
    撤销时还得逐条找。存储不是动因（一行几百字节），可管理性才是。
    """
    now = _now()
    for old in list_shares(session, username, article_id=article_id):
        if is_live(old, now=now):
            old.revoked_at = _iso(now)
            session.add(old)
    expires_at = _iso(now + datetime.timedelta(days=expires_in_days)) if expires_in_days else None
    record = ArticleShareRecord(
        token=generate_share_token(),
        article_id=article_id,
        owner_username=username,
        created_at=_iso(now),
        expires_at=expires_at,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def revoke_share(session: Session, share_id: int, username: str) -> Optional[ArticleShareRecord]:
    """撤销自己签发的分享（幂等）；不属于该用户时返回 None，由调用方转 404。"""
    record = session.get(ArticleShareRecord, share_id)
    if record is None or record.owner_username != username:
        return None
    if record.revoked_at is None:
        record.revoked_at = _iso(_now())
        session.add(record)
        session.commit()
        session.refresh(record)
    return record


def list_shares(session: Session, username: str, *, article_id: Optional[str] = None) -> List[ArticleShareRecord]:
    """列出某用户签发的分享（默认全部，可按文章过滤），最新在前。"""
    statement = select(ArticleShareRecord).where(ArticleShareRecord.owner_username == username)
    if article_id:
        statement = statement.where(ArticleShareRecord.article_id == article_id)
    rows = session.exec(statement).all()
    return sorted(rows, key=lambda r: r.created_at or "", reverse=True)


def resolve_share(
    session: Session,
    token: str,
    *,
    hidden_source_ids: Optional[set] = None,
) -> Tuple[Optional[ArticleShareRecord], Optional[ArticleRecord]]:
    """按令牌解析出（分享, 文章）；任一护栏不通过均返回 (None, None)。

    **失败一律不区分原因**——不存在 / 已撤销 / 已过期 / 源已隐藏 / 文章已删,对访客
    都是同一个 404。区分原因等于把令牌是否有效告诉了猜令牌的人。
    """
    token = (token or "").strip()
    if not token or not token.startswith(TOKEN_PREFIX):
        return None, None
    record = session.exec(
        select(ArticleShareRecord).where(ArticleShareRecord.token == token)
    ).first()
    if record is None or not is_live(record):
        return None, None
    article = session.get(ArticleRecord, record.article_id)
    if article is None:
        return None, None
    if hidden_source_ids and article.source_id in hidden_source_ids:
        return None, None
    return record, article


def touch_view(session: Session, record: ArticleShareRecord) -> None:
    """记一次打开（访客无身份，只累加计数与时间）。计量失败绝不影响页面可读。"""
    try:
        record.view_count = int(record.view_count or 0) + 1
        record.last_viewed_at = _iso(_now())
        session.add(record)
        session.commit()
    except Exception:  # noqa: BLE001 — 计量是附带品，不能让它挡住内容
        session.rollback()
