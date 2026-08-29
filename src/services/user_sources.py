"""用户自定 RSS 源(v3.40):读者自助添加私有消息来源的服务层。

设计方案见 docs/user-custom-rss-wave-plan.md。核心定调:

- **私有订阅资产,不是归档策展资产**(方案 B 隔离):不进公共发现页目录、不进
  scope=all 检索域、不进日报、不进 archive sync export。隔离面各消费点经
  ``user_source_ids()`` / ``USER_SOURCE_PREFIX`` 收口。
- **最简正文**:配置行固定 ``fetch_detail_if_missing=false``,feed 给什么存什么,
  不做全文/摘要分型与详情补抓(preview 仅守门:能解析出条目才允许保存)。
- **同 URL 去重共享**:source_id = 前缀 + sha256(规范化 URL) 截断,第二人添加同一
  feed 退化为订阅既有配置行,一份抓取多人共享;``owner_username`` 记首建者,仅作
  身份标记与溯源,不承担权限差异。
- **删除语义**:「移除」= 退订本人 + 无其他活跃订阅者时物理删除(配置行+文章+
  抓取状态+各用户水位);收藏/已读态/分享的孤儿行沿既有「无害」口径不清
  (与 DELETE /api/articles 行为一致)。

护栏数值是代码常量而非配置(沿 DAILY_SHARE_LIMIT 范式:防滥用护栏不是运营旋钮);
总闸与刷新间隔是运营旋钮,走 KV(运维管理→内容 可改,刷新间隔改后热生效)。
"""

import datetime
import hashlib
import json
import threading
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx
from sqlmodel import Session, delete, select

from models.db import (
    AppSettingRecord,
    ArticleRecord,
    ArticleShareRecord,
    ReaderReadCursorRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserRecord,
)

# 写路径串行锁(2026-08-28 检视返修 F7):建源/移除/强删的 check-then-write 段以
# 进程内锁串行化——单进程部署(uvicorn 单 worker,本项目两条部署路径的现状)下
# 等价于事务级互斥,免去 SQLite BEGIN IMMEDIATE 的编排;多 worker 部署时此锁
# 失效,届时按方案 backlog 升级为成员表+DB 级约束。锁内全同步无网络 IO,极短。
_WRITE_LOCK = threading.Lock()

USER_SOURCE_PREFIX = "user_rss_"

# 防滥用护栏(常量,非配置):正常使用远够不到,够到的是脚本刷量。
MAX_SOURCES_PER_USER = 20      # 单用户订阅中的用户源总数上限
DAILY_ADD_LIMIT = 10           # 单用户单日新建配置行上限(复用既有源的添加不计)
AUTO_DISABLE_FAILURES = 10     # 连续失败达此数即自动停抓(用户可重启)

# 运营旋钮(KV):总闸沿 public_share_enabled 即时熔断语义;刷新间隔改后热生效。
ENABLED_KEY = "user_sources_enabled"
REFRESH_MINUTES_KEY = "user_sources_refresh_minutes"
DEFAULT_REFRESH_MINUTES = 60
MIN_REFRESH_MINUTES = 15       # 下限保护:对目标站与本机负载都别太密

# feed 拉取护栏:preview 与正式抓取(generic_rss 经 params 承接)共用同一上限。
FEED_MAX_BYTES = 5 * 1024 * 1024
FEED_TIMEOUT_SECONDS = 20
PREVIEW_ENTRY_COUNT = 5

# 日增计数 KV 前缀(检视返修 F9:改不可随删除回退的动作计数,建→删循环不再绕过日限)
DAILY_ADD_KEY_PREFIX = "user_sources_added:"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


def is_user_source(source_id: str) -> bool:
    return bool(source_id) and str(source_id).startswith(USER_SOURCE_PREFIX)


# ==================== URL 规范化与身份 ====================

def canonical_feed_url(url: str) -> str:
    """feed URL 保守规范化:小写 scheme/host、去默认端口、去 fragment、去尾斜杠。

    query 原样保留(hnrss 等 feed 以 query 区分内容)。非 http(s) 抛 ValueError。
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("feed 地址不能为空")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("仅支持 http(s) 地址")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("feed 地址缺少主机名")
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def source_id_for_url(canonical_url: str, *, full: bool = False) -> str:
    """规范化 URL → source_id。full=True 用全长哈希(截断碰撞时的重建路径)。"""
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return USER_SOURCE_PREFIX + (digest if full else digest[:12])


# ==================== KV 旋钮 ====================

def _kv_get(session: Session, key: str) -> Optional[str]:
    record = session.get(AppSettingRecord, key)
    return record.value if record is not None else None


def _kv_set(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)
    session.commit()


def feature_enabled(session: Session) -> bool:
    """功能总闸,默认开。关闭=添加/preview 403 + 调度跳过,已有源与文章数据不动。"""
    value = _kv_get(session, ENABLED_KEY)
    if value is None:
        return True
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def set_feature_enabled(session: Session, enabled: bool) -> None:
    _kv_set(session, ENABLED_KEY, "1" if enabled else "0")


def refresh_minutes(session: Session) -> int:
    value = _kv_get(session, REFRESH_MINUTES_KEY)
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_MINUTES
    return max(minutes, MIN_REFRESH_MINUTES)


def set_refresh_minutes(session: Session, minutes: int) -> int:
    minutes = max(int(minutes), MIN_REFRESH_MINUTES)
    _kv_set(session, REFRESH_MINUTES_KEY, str(minutes))
    return minutes


# ==================== 隔离面与查询 ====================

def user_source_ids(session: Session) -> Set[str]:
    """全部用户源 source_id 集合(隔离面单一取数点:all 检索域/归档导出/目录过滤)。"""
    rows = session.exec(
        select(SourceConfigRecord.source_id).where(SourceConfigRecord.owner_username != "")
    ).all()
    return {str(value) for value in rows}


def exclude_user_sources_condition(session: Session = None):  # noqa: ARG001 - 签名兼容既有调用
    """「排除全部用户源」的 SQL 条件(检视返修 F1 收口原语)。

    凡**没有归属主体上下文**的交付查询(/api/feed、空范围 dsub 订阅等)一律应用
    本条件——私有内容只经「订阅者本人」的域可达。判定按 `user_rss_` 前缀而非查
    配置表(codex 复检二轮):删除与在途抓取竞态产生的孤儿文章没有配置行,按表
    判定会漏进公共交付;前缀是结构性兜底,孤儿同样被隔离。
    """
    from sqlalchemy import or_

    return or_(
        ArticleRecord.source_id.is_(None),
        ~ArticleRecord.source_id.startswith(USER_SOURCE_PREFIX, autoescape=True),
    )


def get_user_source(session: Session, source_id: str) -> Optional[SourceConfigRecord]:
    record = session.get(SourceConfigRecord, source_id)
    if record is None or not record.owner_username:
        return None
    return record


def _subscription_source_ids(subscription: ReaderSubscriptionRecord) -> List[str]:
    from api.sources import subscription_source_ids  # 纯助手模块,延迟导入避编排层依赖

    return subscription_source_ids(subscription)


def active_subscriber_usernames(session: Session, source_id: str) -> List[str]:
    """当前活跃订阅了该源的用户名(去重;账户须仍存在——删号路径的订阅级联在
    router 层,此处以 users 表存在性兜底,防僵尸订阅把孤儿 GC 判成有主)。"""
    owners: Set[str] = set()
    for record in session.exec(
        select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.is_active == True)  # noqa: E712
    ).all():
        if source_id in _subscription_source_ids(record):
            owners.add(record.owner_username or "")
    owners.discard("")
    if not owners:
        return []
    existing = {
        str(u) for u in session.exec(
            select(UserRecord.username).where(UserRecord.username.in_(sorted(owners)))
        ).all()
    }
    return sorted(owners & existing)


def subscribed_user_source_ids(session: Session, username: str) -> Set[str]:
    """该用户订阅中的用户源集合(配额分母:去重共享下订阅他人首建的源同样计入)。"""
    all_user = user_source_ids(session)
    if not all_user:
        return set()
    mine: Set[str] = set()
    for record in session.exec(
        select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == username,
            ReaderSubscriptionRecord.is_active == True,  # noqa: E712
        )
    ).all():
        mine.update(sid for sid in _subscription_source_ids(record) if sid in all_user)
    return mine


def daily_created_count(session: Session, username: str) -> int:
    """当日该用户的新建动作计数(KV 事件计数,检视返修 F9:不随删除回退,
    「建→删」循环无法绕过日限;与建行同事务写入)。"""
    key = f"{DAILY_ADD_KEY_PREFIX}{username}:{datetime.date.today().isoformat()}"
    try:
        return int(_kv_get(session, key) or 0)
    except (TypeError, ValueError):
        return 0


def _bump_daily_created(session: Session, username: str) -> None:
    """日增计数 +1(不 commit,随建行事务提交;历史日期 key 由 retention 清理兜底)。"""
    key = f"{DAILY_ADD_KEY_PREFIX}{username}:{datetime.date.today().isoformat()}"
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value="1")
    else:
        try:
            record.value = str(int(record.value or 0) + 1)
        except (TypeError, ValueError):
            record.value = "1"
    session.add(record)


# ==================== 系统源撞库检测 ====================

def system_feed_urls(session: Session) -> Dict[str, Dict[str, str]]:
    """规范化 feed URL → 系统源 {source_id, name}。

    集合 = registry 中 RSS preset 的 feed_url 类属性 ∪ 平台 config(owner 为空、
    source_type=rss/atom)的 url。用户添加撞中即转引导订阅系统源,防平行双源。
    """
    result: Dict[str, Dict[str, str]] = {}
    try:
        from fetchers.registry import fetcher_registry

        for source_id, fetcher_class in fetcher_registry._fetchers.items():  # noqa: SLF001 - 既有枚举惯例(ingest.py 同)
            feed_url = getattr(fetcher_class, "feed_url", "") or ""
            if not feed_url or getattr(fetcher_class, "is_template", False):
                continue
            try:
                result[canonical_feed_url(feed_url)] = {
                    "source_id": source_id,
                    "name": getattr(fetcher_class, "name", source_id),
                }
            except ValueError:
                continue
    except Exception:  # noqa: BLE001 - 撞库检测失败不阻断添加主流程(退回平行源残余)
        pass
    for record in session.exec(
        select(SourceConfigRecord).where(
            SourceConfigRecord.owner_username == "",
            SourceConfigRecord.source_type.in_(["rss", "atom"]),
        )
    ).all():
        if not record.url:
            continue
        try:
            result[canonical_feed_url(record.url)] = {
                "source_id": record.source_id,
                "name": record.name,
            }
        except ValueError:
            continue
    return result


# ==================== feed 拉取与解析(preview 守门) ====================

async def fetch_feed_preview(
    url: str, *, transport: Optional[httpx.AsyncBaseTransport] = None
) -> Dict[str, Any]:
    """SSRF 守门 + 拉取 + feedparser 解析;解析不出条目抛 ValueError(添加即拒绝)。

    transport 供测试注入 httpx.MockTransport(与 x_timeline 测试同惯例,不打真网)。
    """
    canonical = canonical_feed_url(url)
    from services.media_store import ensure_public_host  # SSRF 判定单点(含 fake-ip 豁免)

    await ensure_public_host(urlsplit(canonical).hostname or "")

    # 流式限量(检视返修 F5):先按 Content-Length 预拒,再逐块累计、超限即断开——
    # 巨大/无限 chunked body 不再先整段读入内存才检查。
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FEED_TIMEOUT_SECONDS,
        headers={"User-Agent": "DoramiSourceArchive/feed-preview"},
        transport=transport,
    ) as client:
        async with client.stream("GET", canonical) as response:
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > FEED_MAX_BYTES:
                raise ValueError("feed 体积超过上限")
            chunks: List[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > FEED_MAX_BYTES:
                    raise ValueError("feed 体积超过上限")
                chunks.append(chunk)
            body = b"".join(chunks)

    parsed = feedparser.parse(body)
    entries = getattr(parsed, "entries", None) or []
    if not entries:
        raise ValueError("无法从该地址解析出任何条目,请确认这是一个 RSS/Atom feed")

    feed_title = str((getattr(parsed, "feed", None) or {}).get("title") or "").strip()
    preview_entries: List[Dict[str, Any]] = []
    for entry in entries[:PREVIEW_ENTRY_COUNT]:
        content_text = ""
        content_list = entry.get("content") or []
        if content_list:
            content_text = str(content_list[0].get("value") or "")
        if not content_text:
            content_text = str(entry.get("summary") or "")
        preview_entries.append({
            "title": str(entry.get("title") or "").strip(),
            "publish_date": str(entry.get("published") or entry.get("updated") or ""),
            "content_chars": len(content_text),
        })
    return {
        "canonical_url": canonical,
        "feed_title": feed_title,
        "entry_count": len(entries),
        "entries": preview_entries,
    }


# ==================== 建源 ====================

def _resolve_config_slot(session: Session, canonical: str) -> tuple[str, Optional[SourceConfigRecord]]:
    """canonical URL → (source_id, 既有同 URL 配置行或 None);处理截断哈希碰撞。"""
    source_id = source_id_for_url(canonical)
    record = session.get(SourceConfigRecord, source_id)
    if record is not None and canonical_matches(record.url, canonical) is False:
        # 48bit 截断相撞(概率可忽略但不为零):改用全长哈希重建身份。
        source_id = source_id_for_url(canonical, full=True)
        record = session.get(SourceConfigRecord, source_id)
    return source_id, record


def canonical_matches(stored_url: str, canonical: str) -> Optional[bool]:
    """既有行 url 与 canonical 是否同一 feed;存储值不可解析时返回 None(视作匹配)。"""
    try:
        return canonical_feed_url(stored_url) == canonical
    except ValueError:
        return None


def prepare_check(session: Session, url: str) -> Dict[str, Any]:
    """撞库检测的只读半边(preview/添加前置引导用,不建行)。

    返回 ``{"blocked": True}``(撞中被隐藏系统源)/``{"existing": {source_id, name,
    kind}}``(撞中可见系统源或既有用户源)/``{}``(无冲突)。URL 非法抛 ValueError。
    """
    canonical = canonical_feed_url(url)
    conflict = system_feed_urls(session).get(canonical)
    if conflict:
        from services import source_visibility

        if conflict["source_id"] in source_visibility.hidden_source_ids(session):
            return {"blocked": True}
        return {"existing": {**conflict, "kind": "system"}}
    _, record = _resolve_config_slot(session, canonical)
    if record is not None:
        from services import source_visibility

        if record.source_id in source_visibility.hidden_source_ids(session):
            # 被 admin 隐藏的既有用户源同样拒绝(检视返修 F6:防重新添加绕过止损)
            return {"blocked": True}
        if not record.is_active and not _auto_disabled(session, record.source_id):
            # admin 手动停用(计数未达自动阈值)不可经再次添加复活
            return {"blocked": True}
        return {"existing": {"source_id": record.source_id, "name": record.name, "kind": "user"}}
    return {}


def unauthorized_user_source_ids(
    session: Session, username: str, source_ids: List[str]
) -> List[str]:
    """给定 source_id 名单中「请求者无权订阅」的用户源(检视返修 F2)。

    用户源的准入凭证是 feed URL(custom-sources 通道),不是 source_id——facets 等
    途径泄露的 id 不能变成订阅资格。已订阅本人(原始并集,含隐藏)的用户源放行
    (幂等/改订阅场景),其余用户源 id 一律视为不可见。
    """
    candidates = [sid for sid in source_ids if is_user_source(sid)]
    if not candidates:
        return []
    from api.feed_service import resolve_subscribed_source_ids

    mine = set(resolve_subscribed_source_ids(session, username, include_hidden=True))
    return sorted(sid for sid in candidates if sid not in mine)


def _auto_disabled(session: Session, source_id: str) -> bool:
    """停用来源判别(不加列):连续失败达阈值=自动停抓特征,允许经再次添加复活;
    计数低于阈值的停用行=admin 手动止损,复活须走运维面(检视返修 F6)。"""
    state = session.get(SourceStateRecord, source_id)
    return state is not None and state.consecutive_failures >= AUTO_DISABLE_FAILURES


class UserSourceQuotaError(ValueError):
    """配额拒绝(源数上限/日增上限),router 译作 400/429。"""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def prepare_user_source(
    session: Session, username: str, url: str, name: str = ""
) -> Dict[str, Any]:
    """撞库检测 + 配额 + 建/复用配置行(不 commit,订阅动作由 router 编排)。

    返回:
    - ``{"existing_system": {source_id, name}}``:撞中可见系统源,不建用户源
    - ``{"blocked": True}``:撞中被隐藏系统源(router 统一 404「暂不可用」)
    - ``{"source_id", "created": bool, "record"}``:用户源就绪(新建或复用)
    """
    canonical = canonical_feed_url(url)

    conflict = system_feed_urls(session).get(canonical)
    if conflict:
        from services import source_visibility

        if conflict["source_id"] in source_visibility.hidden_source_ids(session):
            return {"blocked": True}
        return {"existing_system": conflict}

    from services import source_visibility

    mine = subscribed_user_source_ids(session, username)
    source_id, record = _resolve_config_slot(session, canonical)
    if record is not None:
        # 复用既有用户源(本人重复添加,或第二人添加同 URL→去重共享)。
        # 隐藏/admin 手动停用的源拒绝复用(F6:防绕过止损);自动停抓(连续失败达
        # 阈值)的源允许经再次添加复活并清计数(有人还要看,值得再试)。
        if record.source_id in source_visibility.hidden_source_ids(session):
            return {"blocked": True}
        if not record.is_active and not _auto_disabled(session, record.source_id):
            return {"blocked": True}
        if source_id not in mine and len(mine) >= MAX_SOURCES_PER_USER:
            raise UserSourceQuotaError(
                f"自定源数量已达上限({MAX_SOURCES_PER_USER} 个)", status_code=400
            )
        if not record.is_active:
            record.is_active = True
            record.updated_at = _now_iso()
            session.add(record)
            state = session.get(SourceStateRecord, record.source_id)
            if state is not None:
                state.consecutive_failures = 0  # 复活即重置失败态,给新一轮观察窗
                session.add(state)
        return {"source_id": source_id, "created": False, "record": record}

    if len(mine) >= MAX_SOURCES_PER_USER:
        raise UserSourceQuotaError(
            f"自定源数量已达上限({MAX_SOURCES_PER_USER} 个)", status_code=400
        )
    if daily_created_count(session, username) >= DAILY_ADD_LIMIT:
        raise UserSourceQuotaError("今日新增自定源已达上限,请明天再试", status_code=429)
    _bump_daily_created(session, username)

    now = _now_iso()
    record = SourceConfigRecord(
        source_id=source_id,
        name=(name or "").strip()[:80] or canonical,
        source_type="rss",
        url=canonical,
        category="user",
        fetcher_id="",  # resolve_source_fetcher_id 按 source_type 路由 generic_rss
        description="",
        owner_username=username,
        is_active=True,
        # 最简正文拍板:feed 给什么存什么,不触发详情页补抓。ssrf_guard 与响应上限
        # 由 generic_rss 执行层承接(检视返修 D2/D3:首抓/调度/手工抓取全通道生效)。
        params_json=json.dumps({
            "fetch_detail_if_missing": False,
            "limit": 12,
            "ssrf_guard": True,
            "max_response_bytes": FEED_MAX_BYTES,
        }, ensure_ascii=False),
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    return {"source_id": source_id, "created": True, "record": record}


# ==================== 列表与健康 ====================

def list_user_sources(session: Session, username: Optional[str] = None) -> List[Dict[str, Any]]:
    """用户源列表(username 限定=读者「我的自定源」;None=admin 全量)。"""
    query = select(SourceConfigRecord).where(SourceConfigRecord.owner_username != "")
    if username is not None:
        allowed = subscribed_user_source_ids(session, username)
        records = [r for r in session.exec(query).all() if r.source_id in allowed]
    else:
        records = list(session.exec(query).all())
    state_map = {
        s.source_id: s
        for s in session.exec(
            select(SourceStateRecord).where(
                SourceStateRecord.source_id.in_([r.source_id for r in records])
            )
        ).all()
    } if records else {}
    items: List[Dict[str, Any]] = []
    for record in sorted(records, key=lambda r: r.created_at, reverse=True):
        state = state_map.get(record.source_id)
        items.append({
            "source_id": record.source_id,
            "name": record.name,
            "feed_url": record.url,
            "owner_username": record.owner_username,
            "is_active": record.is_active,
            "created_at": record.created_at,
            "status": state.status if state else "never_run",
            "consecutive_failures": state.consecutive_failures if state else 0,
            "last_success_at": (state.last_success_at or "") if state else "",
        })
    return items


# ==================== 删除(退订 + 无人订阅即清) ====================

def remove_source_from_subscriptions(
    session: Session, source_id: str, username: Optional[str] = None
) -> List[str]:
    """把 source_id 从订阅行剔除(username=None 时作用于所有用户;不 commit)。

    与 reader.py 单源退订同语义:单源订阅删行、多源订阅改 filters;逐用户清水位。
    返回受影响用户名。
    """
    query = select(ReaderSubscriptionRecord)
    if username is not None:
        query = query.where(ReaderSubscriptionRecord.owner_username == username)
    affected: Set[str] = set()
    for record in session.exec(query).all():
        ids = _subscription_source_ids(record)
        if source_id not in ids:
            continue
        affected.add(record.owner_username or "")
        remaining = [sid for sid in ids if sid != source_id]
        if remaining:
            try:
                filters = json.loads(record.filters_json) if record.filters_json else {}
            except (TypeError, json.JSONDecodeError):
                filters = {}
            filters.pop("source_id", None)
            filters["source_ids"] = ",".join(remaining)
            record.filters_json = json.dumps(filters or {}, ensure_ascii=False)
            record.updated_at = _now_iso()
            session.add(record)
        else:
            session.delete(record)
    for owner in affected:
        if owner:
            cursor = session.get(ReaderReadCursorRecord, (owner, source_id))
            if cursor is not None:
                session.delete(cursor)
    affected.discard("")
    return sorted(affected)


def purge_user_source(session: Session, source_id: str) -> Dict[str, int]:
    """物理删除用户源:配置行 + 全部文章(FTS trigger 同步)+ 抓取状态 + 各用户水位
    + 分享记录(检视返修 F8:分享行留存会在「我的分享」显示死链并占每日额度)。

    收藏/已读态的孤儿行沿既有「无害」口径不清(与 DELETE /api/articles 一致)。
    不 commit,由调用方统一提交。
    """
    # 清一切残余订阅引用(codex 复检二轮 F7/F8:purge 只在无**活跃**订阅者时发生,
    # 但 inactive 订阅行/REST 路径的引用若留存,之后 is_active 翻回 true 就成悬空
    # 订阅——统一在物理删除前剔除,含所有用户)。
    remove_source_from_subscriptions(session, source_id, username=None)
    article_ids = [
        str(row) for row in session.exec(
            select(ArticleRecord.id).where(ArticleRecord.source_id == source_id)
        ).all()
    ]
    if article_ids:
        session.exec(delete(ArticleRecord).where(ArticleRecord.source_id == source_id))
        session.exec(delete(ArticleShareRecord).where(ArticleShareRecord.article_id.in_(article_ids)))
    session.exec(delete(ReaderReadCursorRecord).where(ReaderReadCursorRecord.source_id == source_id))
    state = session.get(SourceStateRecord, source_id)
    if state is not None:
        session.delete(state)
    record = session.get(SourceConfigRecord, source_id)
    if record is not None:
        session.delete(record)
    return {"articles_deleted": len(article_ids)}


def remove_user_source(session: Session, username: str, source_id: str) -> Dict[str, Any]:
    """读者「移除自定源」:退订本人;无其他活跃订阅者时物理删除。commit 由本函数负责。"""
    with _WRITE_LOCK:
        record = get_user_source(session, source_id)
        if record is None:
            raise LookupError("自定源不存在")
        remove_source_from_subscriptions(session, source_id, username=username)
        session.commit()
        remaining = active_subscriber_usernames(session, source_id)
        purged = {}
        if not remaining:
            purged = purge_user_source(session, source_id)
            session.commit()
    return {"source_id": source_id, "purged": bool(purged), **purged}


def admin_delete_user_source(session: Session, source_id: str) -> Dict[str, Any]:
    """admin 强删:级联清所有订阅者的订阅行与水位后物理删除(读者路径碰不到这支)。"""
    with _WRITE_LOCK:
        record = get_user_source(session, source_id)
        if record is None:
            raise LookupError("自定源不存在")
        affected = remove_source_from_subscriptions(session, source_id, username=None)
        purged = purge_user_source(session, source_id)
        session.commit()
    return {"source_id": source_id, "affected_subscribers": affected, **purged}


def purge_orphan_user_sources(session: Session) -> List[str]:
    """孤儿 GC(检视返修 F8):无任何活跃订阅者的用户源整体物理删除,返回清单。

    覆盖所有绕过专用移除端点的退订路径(REST 订阅更新/删除、账户删除等)——
    每轮定时刷新末尾执行,专用移除仍即时清理,GC 只是兜底。commit 由本函数负责。
    """
    with _WRITE_LOCK:
        purged: List[str] = []
        for source_id in sorted(user_source_ids(session)):
            if not active_subscriber_usernames(session, source_id):
                purge_user_source(session, source_id)
                purged.append(source_id)
        # 孤儿数据清理(codex 复检二/三轮):删除与在途抓取竞态可能在配置行已删后
        # 写回文章/重建 SourceStateRecord,REST 路径可能提交引用已删配置的订阅——
        # 前缀隔离保证不泄露,此处把三类脏数据统一收走。
        remaining_ids = user_source_ids(session)
        orphan_rows = session.exec(
            select(ArticleRecord.id, ArticleRecord.source_id)
            .where(ArticleRecord.source_id.startswith(USER_SOURCE_PREFIX, autoescape=True))
        ).all()
        orphan_article_ids = [str(aid) for aid, sid in orphan_rows if str(sid) not in remaining_ids]
        if orphan_article_ids:
            session.exec(delete(ArticleRecord).where(ArticleRecord.id.in_(orphan_article_ids)))
            session.exec(delete(ArticleShareRecord).where(
                ArticleShareRecord.article_id.in_(orphan_article_ids)))
            purged.append(f"(孤儿文章 ×{len(orphan_article_ids)})")
        orphan_states = [
            s for s in session.exec(
                select(SourceStateRecord).where(
                    SourceStateRecord.source_id.startswith(USER_SOURCE_PREFIX, autoescape=True))
            ).all() if s.source_id not in remaining_ids
        ]
        for state in orphan_states:
            session.delete(state)
        if orphan_states:
            purged.append(f"(孤儿状态 ×{len(orphan_states)})")
        # 悬空订阅引用:filters 里指向已删用户源配置的 source_id 剔除
        dangling_cleaned = 0
        for sub in session.exec(select(ReaderSubscriptionRecord)).all():
            ids = _subscription_source_ids(sub)
            dangling = [sid for sid in ids if is_user_source(sid) and sid not in remaining_ids]
            if dangling:
                for sid in dangling:
                    remove_source_from_subscriptions(session, sid, username=None)
                dangling_cleaned += len(dangling)
        if dangling_cleaned:
            purged.append(f"(悬空订阅引用 ×{dangling_cleaned})")
        if purged:
            session.commit()
    return purged


def cleanup_stale_daily_counters(session: Session) -> int:
    """清理历史日期的日增计数 KV(codex 复检二轮新发现3:按 用户×日期 增长,
    挂每日 retention 维护路径)。commit 由本函数负责,返回清理行数。"""
    today = datetime.date.today().isoformat()
    rows = session.exec(
        select(AppSettingRecord).where(AppSettingRecord.key.startswith(DAILY_ADD_KEY_PREFIX, autoescape=True))
    ).all()
    stale = [r for r in rows if not r.key.endswith(f":{today}")]
    for record in stale:
        session.delete(record)
    if stale:
        session.commit()
    return len(stale)


# ==================== 调度治理 ====================

def auto_disable_failing(session: Session) -> List[str]:
    """连续失败达阈值的活跃用户源自动停抓(commit 由本函数负责),返回停用名单。

    停用后「我的源」列表可见「已暂停」,用户重新添加同 URL 即重启(prepare 里复活)。
    """
    disabled: List[str] = []
    ids = user_source_ids(session)
    if not ids:
        return disabled
    states = session.exec(
        select(SourceStateRecord).where(SourceStateRecord.source_id.in_(ids))
    ).all()
    failing = {
        s.source_id for s in states if s.consecutive_failures >= AUTO_DISABLE_FAILURES
    }
    if not failing:
        return disabled
    for record in session.exec(
        select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(failing))
    ).all():
        if record.is_active:
            record.is_active = False
            record.updated_at = _now_iso()
            session.add(record)
            disabled.append(record.source_id)
    if disabled:
        session.commit()
    return disabled


def admin_overview(session: Session) -> Dict[str, Any]:
    """运维「用户自定源」区取数:KPI + 全量源列表(含订阅人数)。"""
    items = list_user_sources(session, username=None)
    ids = [item["source_id"] for item in items]
    article_counts: Dict[str, int] = {}
    if ids:
        from sqlalchemy import func

        rows = session.exec(
            select(ArticleRecord.source_id, func.count(ArticleRecord.id))
            .where(ArticleRecord.source_id.in_(ids))
            .group_by(ArticleRecord.source_id)
        ).all()
        article_counts = {str(sid): int(count) for sid, count in rows}
    covered_users: Set[str] = set()
    for item in items:
        subscribers = active_subscriber_usernames(session, item["source_id"])
        item["subscriber_count"] = len(subscribers)
        item["article_count"] = article_counts.get(item["source_id"], 0)
        covered_users.update(subscribers)
    return {
        "items": items,
        "kpi": {
            "source_count": len(items),
            "covered_users": len(covered_users),
            "article_count": sum(article_counts.values()),
            "failing_count": sum(1 for i in items if i["consecutive_failures"] > 0 or i["status"] == "failing"),
        },
        "config": {
            "enabled": feature_enabled(session),
            "refresh_minutes": refresh_minutes(session),
        },
    }
