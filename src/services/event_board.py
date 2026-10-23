"""事件热点榜读取侧(issue #121 wave 1):榜单 / 详情 / 日快照 / 管理员合并与摘出。

读取时现算:隐藏源 / 私有源(``user_rss_`` 前缀、``owner_username`` 非空、凭据源)的成员在读取时剔除,
标题 / 摘要 / 来源名单 / 计数全部只从可见成员算;可见发布主体 < 2 的事件不上榜。
排序 = 可见发布主体数 ↓ → 最新成员首见 ↓(方案 §4 首版口径);热度值 ``heat`` 只写进日快照供回放,
不参与排序、不进读者 API。07:00 日快照(``event_rank_snapshots``)供 ``previous_rank``;
没有前一日快照时 ``previous_rank`` 为 null、``snapshot_date`` 为 null。

管理员修正(合并 / 摘出)只在 authority 节点允许(``EventRecord.authority_id`` 为空);成员迁移后按
「官方 > 非社交 > 分数 > 发布更早」重算代表稿与计数;合并写 ``event_redirects``,旧 ID 查询返回新事件并带
``merged_into``;摘出成新单成员事件并在 ledger ``blocked_event_ids_json`` 登记原事件,自动分配不再回流。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from api.sources import configured_source_shape, normalize_shape, source_shape
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    EventAssignmentLedgerRecord,
    EventMemberBacklogRecord,
    EventMemberRecord,
    EventRankSnapshotRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import source_visibility, user_sources
from services.article_time import SHANGHAI, parse_article_time
from services.reader_ai import (
    TRANSLATION_TITLE_FP_KEY,
    TRANSLATION_TITLE_KEY,
    _body_fingerprint,
    _cache_valid,
    looks_chinese,
)
from services.source_naming import friendly_source_names, source_role


logger = logging.getLogger("dorami.event_board")

WINDOW_HOURS = 48
MAX_WINDOW_HOURS = 24 * 14
BOARD_LIMIT = 10
NEW_HOURS = 6
MIN_PUBLISHERS = 2
HEAT_HALF_LIFE_HOURS = 24.0
EMPTY_REASON_NO_EVENTS = "no_events"
SNAPSHOT_CRON = "0 7 * * *"
SNAPSHOT_JOB_ID = "event_rank_snapshot"
ADMIN_LIST_LIMIT = 50
ADMIN_LIST_MAX = 200
ADMIN_STATUSES = ("open", "closed", "merged")
_REDIRECT_MAX_HOPS = 32


class EventBoardError(Exception):
    """管理员修正被拒(路由按 ``status_code`` 回 404 / 409)。"""

    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ── 时间 ──


def _utcnow(now: dt.datetime | None = None) -> dt.datetime:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _ts(value: str | None) -> float:
    parsed = parse_article_time(value)
    return parsed.timestamp() if parsed else 0.0


def shanghai_date(now: dt.datetime | None = None) -> str:
    """快照日期(Asia/Shanghai 自然日);库内时间戳本身一律 UTC。"""

    return _utcnow(now).astimezone(SHANGHAI).date().isoformat()


def _title_lang(title: str) -> str:
    return "zh" if looks_chinese(title or "") else "en"


# ── 来源元数据与可见性 ──


@dataclass(frozen=True)
class _SourceInfo:
    source_id: str
    name: str
    role: str
    shape: str
    visible: bool


def _source_infos(
    session: Session,
    sources: dict[str, str],
    *,
    reader_scope: bool,
) -> dict[str, _SourceInfo]:
    """source_id → (名称 / 角色 / 形态 / 读者可见性);配置行优先、注册表其次。

    ``sources`` 是 source_id → 该源任一文章的 content_type(无配置也无注册表时据此推形态)。
    ``reader_scope`` 为假(管理员重算)时不做可见性判定,所有成员一视同仁。
    """

    wanted = sorted(sid for sid in sources if sid)
    if not wanted:
        return {}
    configs = {
        row.source_id: row
        for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(wanted))
        ).all()
    }
    hidden = source_visibility.reader_unavailable_source_ids(session) if reader_scope else set()
    private = user_sources.user_source_ids(session) if reader_scope else set()
    names = friendly_source_names(wanted, session)
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001 - 注册表不可用时退化为配置 / 内容类型推断
        fetcher_registry = None  # type: ignore[assignment]
    infos: dict[str, _SourceInfo] = {}
    for source_id in wanted:
        config = configs.get(source_id)
        fetcher_class = None
        if fetcher_registry is not None:
            try:
                fetcher_class = fetcher_registry.get_class(source_id)
            except Exception:  # noqa: BLE001
                fetcher_class = None
        if config is not None:
            role = source_role(
                source_id,
                source_scope=config.source_scope or "",
                provenance_tier=config.provenance_tier or "",
            )
            shape = configured_source_shape(config.source_type or "", config.fetcher_id or "")
        elif fetcher_class is not None:
            role = source_role(source_id)
            shape = normalize_shape(str(getattr(fetcher_class, "content_shape", "article") or "")) or "article"
        else:
            role = source_role(source_id)
            shape = source_shape(source_id, sources.get(source_id) or "", {})
        visible = True
        if reader_scope:
            visible = not (
                source_id in hidden
                or source_id in private
                or user_sources.is_user_source(source_id)
                or user_sources.source_is_credentialed(config)
            )
        infos[source_id] = _SourceInfo(
            source_id=source_id,
            name=names.get(source_id) or source_id,
            role=role,
            shape=shape,
            visible=visible,
        )
    return infos


# ── 成员与事件视图 ──


@dataclass
class _MemberView:
    member: EventMemberRecord
    article: ArticleRecord
    analysis: Optional[ArticleAnalysisRecord]
    info: _SourceInfo

    @property
    def publisher_key(self) -> str:
        return (self.member.publisher_key or "").strip() or self.article.source_id

    @property
    def score(self) -> float:
        if self.analysis is None or self.analysis.status != "succeeded":
            return 0.0
        return float(self.analysis.quality_score or 0.0)

    @property
    def summary(self) -> str:
        if self.analysis is None or self.analysis.status != "succeeded":
            return ""
        return str(self.analysis.summary or "")

    @property
    def report_ts(self) -> float:
        return _ts(self.member.report_at) or _ts(self.article.publish_date)

    @property
    def first_seen_ts(self) -> float:
        return _ts(self.member.first_seen_at)

    def order_key(self) -> tuple[object, ...]:
        """代表稿序:官方 > 非社交 > 分数 > 发布更早(与早报 ``_representative_key`` 同口径)。"""

        return (
            self.info.role != "official",
            self.info.shape == "social",
            -self.score,
            self.report_ts,
            self.article.id,
        )


@dataclass(frozen=True)
class _Publisher:
    key: str
    name: str
    count: int
    latest_first_seen_ts: float


@dataclass
class _EventView:
    event: EventRecord
    members: list[_MemberView]          # 已按代表稿序排好
    representative: _MemberView
    publishers: list[_Publisher]        # 按稿数 ↓、键 ↑
    latest_at: str
    latest_ts: float
    title: str
    title_lang: str

    @property
    def publisher_count(self) -> int:
        return len(self.publishers)

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def summary(self) -> str:
        return self.representative.summary


def _extensions(article: ArticleRecord) -> dict[str, Any]:
    try:
        ext = json.loads(article.extensions_json or "{}")
    except (TypeError, ValueError):
        return {}
    return ext if isinstance(ext, dict) else {}


def _translated_title(article: ArticleRecord) -> str:
    """阅读窗 / 早报共用的中文标题缓存;指纹失配(标题已被刷新改写)视为无缓存。"""

    ext = _extensions(article)
    if _cache_valid(ext, TRANSLATION_TITLE_KEY, TRANSLATION_TITLE_FP_KEY, _body_fingerprint(article.title or "")):
        return str(ext.get(TRANSLATION_TITLE_KEY) or "").strip()
    return ""


def _resolve_title(
    event: EventRecord,
    members_sorted: list[_MemberView],
    representative: _MemberView,
    *,
    allow_llm_title: bool = True,
) -> tuple[str, str]:
    """事件标题优先级(方案 §3 步骤 4):中文成员标题 → 代表稿译题缓存 → 模型拟题 → 代表稿原题。

    返回 (标题, title_source);读者面只用标题,管理员重算把 title_source 一并写回。
    ``allow_llm_title`` 为假时跳过模型拟题(codex R1 F1):拟题可能复述某个成员独有的信息,
    读者视图里只要有任一成员不可见,就无法证明它只依赖可见成员,退回可见报道标题。
    """

    for view in members_sorted:
        title = (view.article.title or "").strip()
        if title and looks_chinese(title):
            return title, "zh_member"
    translated = _translated_title(representative.article)
    if translated:
        return translated, "translation"
    if allow_llm_title and (event.title or "").strip() and event.title_source == "llm":
        return event.title.strip(), "llm"
    return (representative.article.title or "").strip(), "representative"


@dataclass
class _MemberLoad:
    """按事件分组的成员视图与「事件是否完整」的判定材料。

    - ``partially_hidden``:对当前读者至少有一名成员不可见的事件;
    - ``local_counts``:本地已落库(且文章在库)的成员行数,读者可见性过滤之前;
    - ``backlog``:在 ``event_member_backlog`` 有待补行的事件(接收端文章晚到)。
    """

    views: dict[int, list[_MemberView]]
    partially_hidden: set[int]
    local_counts: dict[int, int]
    backlog: set[int]

    def for_event(self, event_id: int | None) -> list[_MemberView]:
        return self.views.get(int(event_id or 0), [])

    def incomplete(self, event: EventRecord) -> bool:
        """读取侧「事件不完整」(codex R1 F1 / R2 G1):任一成员不可见 ∨ 待补账有行 ∨ 本地成员数 <
        authority 写入的 ``member_count``。不完整即不能证明模型题只依赖可见成员,禁用模型题;
        榜单 / 详情 / 关联事件共用这一判定。"""

        event_id = int(event.id or 0)
        return (
            event_id in self.partially_hidden
            or event_id in self.backlog
            or self.local_counts.get(event_id, 0) < int(event.member_count or 0)
        )


def _load_member_views(
    session: Session,
    event_ids: Iterable[int],
    *,
    reader_scope: bool,
) -> _MemberLoad:
    ids = sorted({int(value) for value in event_ids if value is not None})
    if not ids:
        return _MemberLoad({}, set(), {}, set())
    rows = session.exec(
        select(EventMemberRecord, ArticleRecord, ArticleAnalysisRecord)
        .join(ArticleRecord, ArticleRecord.id == EventMemberRecord.article_id)
        .outerjoin(ArticleAnalysisRecord, ArticleAnalysisRecord.article_id == ArticleRecord.id)
        .where(EventMemberRecord.event_id.in_(ids))
    ).all()
    sources: dict[str, str] = {}
    for _member, article, _analysis in rows:
        sources.setdefault(article.source_id, article.content_type or "")
    infos = _source_infos(session, sources, reader_scope=reader_scope)
    grouped: dict[int, list[_MemberView]] = {}
    partially_hidden: set[int] = set()
    local_counts: dict[int, int] = {}
    for member, article, analysis in rows:
        local_counts[int(member.event_id)] = local_counts.get(int(member.event_id), 0) + 1
        info = infos.get(article.source_id)
        if info is None or (reader_scope and not info.visible):
            partially_hidden.add(int(member.event_id))
            continue
        grouped.setdefault(int(member.event_id), []).append(_MemberView(member, article, analysis, info))
    backlog: set[int] = set()
    if reader_scope:
        backlog = {
            int(value) for value in session.exec(
                select(EventMemberBacklogRecord.event_id)
                .where(EventMemberBacklogRecord.event_id.in_(ids))
                .distinct()
            ).all()
        }
    return _MemberLoad(grouped, partially_hidden, local_counts, backlog)


def _build_view(
    event: EventRecord,
    members: list[_MemberView],
    *,
    allow_llm_title: bool = True,
) -> _EventView | None:
    if not members:
        return None
    ordered = sorted(members, key=lambda view: view.order_key())
    representative = ordered[0]
    groups: dict[str, list[_MemberView]] = {}
    for view in ordered:
        groups.setdefault(view.publisher_key, []).append(view)
    publishers = [
        _Publisher(
            key=key,
            # 键是某个 source_id 时直接取该源的名;键是 source_owner(多渠道合一)时借代表稿序
            # 最靠前那篇的来源名。
            name=next((v.info.name for v in views if v.info.source_id == key), views[0].info.name),
            count=len(views),
            latest_first_seen_ts=max(v.first_seen_ts for v in views),
        )
        for key, views in groups.items()
    ]
    publishers.sort(key=lambda item: (-item.count, item.key))
    latest = max(ordered, key=lambda view: view.first_seen_ts)
    title, title_source = _resolve_title(event, ordered, representative, allow_llm_title=allow_llm_title)
    return _EventView(
        event=event,
        members=ordered,
        representative=representative,
        publishers=publishers,
        latest_at=latest.member.first_seen_at,
        latest_ts=latest.first_seen_ts,
        title=title,
        # 译题缓存按来源即中文(短译题里英文专名占比高,字符占比启发式会误判成 en);其余按占比判。
        title_lang="zh" if title_source == "translation" else _title_lang(title),
    )


def _board_entries(
    session: Session,
    *,
    window_hours: int,
    limit: int,
    now: dt.datetime,
) -> list[_EventView]:
    """当前窗内可上榜的事件视图(已排序、已截断)。窗按**首篇**首见;含 closed,排 merged。"""

    window_start = now - dt.timedelta(hours=window_hours)
    # 字符串下界只做粗筛(库内格式可能混有 Z / 微秒 / 无时区),再按解析后的时刻精筛。
    coarse_bound = _iso(window_start - dt.timedelta(days=1))
    candidates = session.exec(
        select(EventRecord).where(
            EventRecord.status.in_(("open", "closed")),
            EventRecord.first_seen_at >= coarse_bound,
        )
    ).all()
    events = [event for event in candidates if _ts(event.first_seen_at) >= window_start.timestamp()]
    load = _load_member_views(session, [event.id for event in events], reader_scope=True)
    views: list[_EventView] = []
    for event in events:
        view = _build_view(event, load.for_event(event.id), allow_llm_title=not load.incomplete(event))
        if view is None or view.publisher_count < MIN_PUBLISHERS:
            continue
        views.append(view)
    views.sort(key=lambda view: (-view.publisher_count, -view.latest_ts, -view.member_count, int(view.event.id or 0)))
    return views[:limit]


def _previous_snapshot(session: Session, *, before_date: str) -> tuple[str | None, dict[int, int]]:
    """``before_date`` 之前最近一份日快照:(日期, event_id → rank);没有则 (None, {})。"""

    date = session.exec(
        select(EventRankSnapshotRecord.snapshot_date)
        .where(EventRankSnapshotRecord.snapshot_date < before_date)
        .order_by(EventRankSnapshotRecord.snapshot_date.desc())
        .limit(1)
    ).first()
    if not date:
        return None, {}
    rows = session.exec(
        select(EventRankSnapshotRecord).where(EventRankSnapshotRecord.snapshot_date == date)
    ).all()
    return str(date), {int(row.event_id): int(row.rank) for row in rows}


def _events_by_id(session: Session, ids: Iterable[int | None]) -> dict[int, EventRecord]:
    wanted = sorted({int(value) for value in ids if value is not None})
    if not wanted:
        return {}
    return {
        int(event.id): event
        for event in session.exec(select(EventRecord).where(EventRecord.id.in_(wanted))).all()
    }


def _visible_views(session: Session, events: Iterable[EventRecord]) -> dict[int, _EventView]:
    """读者可见成员视图(codex R1 F11):无可见成员的事件不出现在结果里,标题同样只从可见成员算。"""

    rows = [event for event in events if event is not None and event.status in ("open", "closed")]
    load = _load_member_views(session, [event.id for event in rows], reader_scope=True)
    views: dict[int, _EventView] = {}
    for event in rows:
        view = _build_view(event, load.for_event(event.id), allow_llm_title=not load.incomplete(event))
        if view is not None:
            views[int(event.id or 0)] = view
    return views


def _related_views(session: Session, ids: Iterable[int | None]) -> dict[int, _EventView]:
    return _visible_views(session, _events_by_id(session, ids).values())


def _link_payload(view: _MemberView) -> dict[str, Any]:
    return {
        "article_id": view.article.id,
        "title": view.article.title,
        "title_zh": _translated_title(view.article) or None,
        "source_id": view.article.source_id,
        "source_name": view.info.name,
        "source_role": view.info.role,
        "content_shape": view.info.shape,
        "url": view.article.source_url,
        "publish_date": view.article.publish_date,
    }


def visible_member_links(
    session: Session,
    event_ids: Iterable[int | None],
    *,
    limit: int = 10,
) -> dict[int, list[dict[str, Any]]]:
    """event_id → 读者可见成员的链接清单(代表稿序),供个人早报「另见 N 篇」快照(issue #103)。

    只读事件层、只取读者可见成员(隐藏 / 私有 / 凭据源成员一律不出现,与榜单同一判定),
    每事件最多 ``limit`` 条;事件状态不限(早报键命中的事件可能已 closed)。
    """

    ids = sorted({int(value) for value in event_ids if value is not None})
    if not ids:
        return {}
    load = _load_member_views(session, ids, reader_scope=True)
    result: dict[int, list[dict[str, Any]]] = {}
    for event_id in ids:
        views = sorted(load.for_event(event_id), key=lambda view: view.order_key())
        if views:
            result[event_id] = [_link_payload(view) for view in views[:limit]]
    return result


def _representative_payload(view: _MemberView) -> dict[str, Any]:
    return {
        "article_id": view.article.id,
        "title": view.article.title,
        "source_id": view.article.source_id,
        "source_name": view.info.name,
        "source_role": view.info.role,
        "url": view.article.source_url,
        "publish_date": view.article.publish_date,
    }


def _item_payload(
    view: _EventView,
    *,
    rank: int | None,
    previous_rank: int | None,
    now: dt.datetime,
    follows: dict[int, _EventView],
) -> dict[str, Any]:
    event = view.event
    first_seen_ts = _ts(event.first_seen_at)
    is_new = bool(first_seen_ts) and (now.timestamp() - first_seen_ts) <= NEW_HOURS * 3600
    # 前报对当前读者没有可见成员时整个 story 置空(连 ID 都不给,避免暴露隐藏事件的存在)。
    follows_view = follows.get(int(event.follows_event_id)) if event.follows_event_id is not None else None
    return {
        "event_id": int(event.id or 0),
        "rank": rank,
        "previous_rank": previous_rank,
        "title": view.title,
        "title_lang": view.title_lang,
        "summary": view.summary,
        "publisher_count": view.publisher_count,
        "member_count": view.member_count,
        "first_seen_at": event.first_seen_at,
        "latest_at": view.latest_at,
        "is_new": is_new,
        "representative": _representative_payload(view.representative),
        "publishers": [
            {"key": publisher.key, "name": publisher.name, "count": publisher.count}
            for publisher in view.publishers
        ],
        "story": {
            "follows_event_id": int(follows_view.event.id or 0) if follows_view is not None else None,
            "follows_title": (follows_view.title or None) if follows_view is not None else None,
        },
    }


# ── 读者面 ──


def read_board(
    session: Session,
    *,
    window_hours: int = WINDOW_HOURS,
    limit: int = BOARD_LIMIT,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """热点榜(方案 §5.0 契约):最多 ``limit`` 件,不足不填;空榜 ``empty_reason="no_events"``。"""

    current = _utcnow(now)
    views = _board_entries(session, window_hours=window_hours, limit=limit, now=current)
    snapshot_date, previous_ranks = _previous_snapshot(session, before_date=shanghai_date(current))
    follows = _related_views(session, (view.event.follows_event_id for view in views))
    items = [
        _item_payload(
            view,
            rank=rank,
            previous_rank=previous_ranks.get(int(view.event.id or 0)),
            now=current,
            follows=follows,
        )
        for rank, view in enumerate(views, start=1)
    ]
    return {
        "generated_at": _iso(current),
        "window_hours": int(window_hours),
        "snapshot_date": snapshot_date,
        "empty_reason": None if items else EMPTY_REASON_NO_EVENTS,
        "items": items,
    }


def resolve_redirect(session: Session, event_id: int) -> tuple[int, int | None]:
    """沿 ``event_redirects`` 解析到终点:(终点 ID, merged_into);未重定向时 merged_into 为 None。"""

    current = int(event_id)
    seen = {current}
    for _ in range(_REDIRECT_MAX_HOPS):
        redirect = session.get(EventRedirectRecord, current)
        if redirect is None:
            break
        target = int(redirect.new_event_id)
        if target in seen:
            break
        seen.add(target)
        current = target
    return current, (current if current != int(event_id) else None)


def _member_payload(view: _MemberView, *, representative_id: str) -> dict[str, Any]:
    return {
        "article_id": view.article.id,
        "title": view.article.title,
        "title_zh": _translated_title(view.article) or None,
        "source_id": view.article.source_id,
        "source_name": view.info.name,
        "source_role": view.info.role,
        "content_shape": view.info.shape,
        "url": view.article.source_url,
        "publish_date": view.article.publish_date,
        "report_at": view.member.report_at,
        "is_representative": view.article.id == representative_id,
        "summary": view.summary,
    }


def read_event(
    session: Session,
    event_id: int,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any] | None:
    """事件详情:榜单字段 + 可见成员(report_at 倒序)+ 后续事件 + merged_into;不存在或无可见成员返回 None。"""

    current = _utcnow(now)
    resolved_id, merged_into = resolve_redirect(session, event_id)
    event = session.get(EventRecord, resolved_id)
    if event is None:
        return None
    load = _load_member_views(session, [resolved_id], reader_scope=True)
    view = _build_view(event, load.for_event(resolved_id), allow_llm_title=not load.incomplete(event))
    if view is None:
        return None
    board = _board_entries(session, window_hours=WINDOW_HOURS, limit=BOARD_LIMIT, now=current)
    rank = next((index for index, entry in enumerate(board, start=1) if int(entry.event.id or 0) == resolved_id), None)
    _snapshot_date, previous_ranks = _previous_snapshot(session, before_date=shanghai_date(current))
    follows = _related_views(session, [event.follows_event_id])
    payload = _item_payload(
        view,
        rank=rank,
        previous_rank=previous_ranks.get(resolved_id),
        now=current,
        follows=follows,
    )
    ordered_members = sorted(
        view.members,
        key=lambda item: (-item.report_ts, item.article.id),
    )
    representative_id = view.representative.article.id
    payload["members"] = [
        _member_payload(item, representative_id=representative_id) for item in ordered_members
    ]
    followups = _visible_views(session, session.exec(
        select(EventRecord).where(
            EventRecord.follows_event_id == resolved_id,
            EventRecord.status.in_(("open", "closed")),
        )
    ).all())
    payload["followups"] = [
        {"event_id": int(item.event.id or 0), "title": item.title, "first_seen_at": item.event.first_seen_at}
        for item in sorted(
            followups.values(),
            key=lambda item: (_ts(item.event.first_seen_at), int(item.event.id or 0)),
        )
    ]
    payload["merged_into"] = merged_into
    return payload


# ── 日快照 ──


def _heat(view: _EventView, now: dt.datetime) -> float:
    """Σ_publisher max_i 0.5 ^ ((now − first_seen_at_i) / 24h):只记录供回放,不参与排序。"""

    now_ts = now.timestamp()
    total = 0.0
    for publisher in view.publishers:
        age_hours = max(0.0, (now_ts - publisher.latest_first_seen_ts) / 3600.0)
        total += 0.5 ** (age_hours / HEAT_HALF_LIFE_HOURS)
    return round(total, 6)


def build_rank_snapshot(
    engine: Engine,
    *,
    date: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """按当下榜单写一份日快照(同日重建 = 同一事务内整日替换);只存上榜行。"""

    current = _utcnow(now)
    snapshot_date = (date or "").strip() or shanghai_date(current)
    generated_at = _iso(current)
    with Session(engine) as session:
        views = _board_entries(session, window_hours=WINDOW_HOURS, limit=BOARD_LIMIT, now=current)
        session.exec(
            delete(EventRankSnapshotRecord).where(EventRankSnapshotRecord.snapshot_date == snapshot_date)
        )
        for rank, view in enumerate(views, start=1):
            session.add(EventRankSnapshotRecord(
                snapshot_date=snapshot_date,
                event_id=int(view.event.id or 0),
                rank=rank,
                publisher_count=view.publisher_count,
                member_count=view.member_count,
                heat=_heat(view, current),
                generated_at=generated_at,
            ))
        session.commit()
    return {"snapshot_date": snapshot_date, "generated_at": generated_at, "count": len(views)}


# ── 管理员 ──


def _admin_payload(event: EventRecord, representative_titles: dict[str, str]) -> dict[str, Any]:
    return {
        "id": int(event.id or 0),
        "title": event.title,
        "status": event.status,
        "publisher_count": int(event.publisher_count or 0),
        "member_count": int(event.member_count or 0),
        "first_seen_at": event.first_seen_at,
        "last_member_at": event.last_member_at,
        "representative_article_id": event.representative_article_id,
        "representative_title": representative_titles.get(event.representative_article_id or "", ""),
    }


def _representative_titles(session: Session, events: Iterable[EventRecord]) -> dict[str, str]:
    ids = sorted({event.representative_article_id for event in events if event.representative_article_id})
    if not ids:
        return {}
    rows = session.exec(
        select(ArticleRecord.id, ArticleRecord.title).where(ArticleRecord.id.in_(ids))
    ).all()
    return {str(article_id): str(title or "") for article_id, title in rows}


def list_events(
    session: Session,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = ADMIN_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """管理台抽屉列表(不做读者可见性过滤,计数取库内缓存值);首见倒序。"""

    stmt = select(EventRecord)
    if status:
        stmt = stmt.where(EventRecord.status == status)
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(EventRecord.title.contains(needle, autoescape=True))
    stmt = stmt.order_by(EventRecord.first_seen_at.desc(), EventRecord.id.desc()).limit(
        max(1, min(int(limit), ADMIN_LIST_MAX))
    )
    events = session.exec(stmt).all()
    titles = _representative_titles(session, events)
    return [_admin_payload(event, titles) for event in events]


def admin_event(session: Session, event: EventRecord) -> dict[str, Any]:
    return _admin_payload(event, _representative_titles(session, [event]))


def _assert_local(event: EventRecord) -> None:
    if (event.authority_id or "").strip():
        raise EventBoardError(
            "synced_event",
            "该事件由 Archive Sync 下发,请在 authority 节点修正",
            status_code=409,
        )


def _recompute_event(session: Session, event: EventRecord, *, stamp: str) -> None:
    """成员变动后重算代表稿 / 计数 / 首末时间 / 标题(全部成员,不做读者可见性过滤)。"""

    members = _load_member_views(session, [int(event.id or 0)], reader_scope=False).for_event(event.id)
    event.updated_at = stamp
    if not members:
        event.member_count = 0
        event.publisher_count = 0
        event.representative_article_id = None
        for row in session.exec(
            select(EventMemberRecord).where(EventMemberRecord.event_id == event.id)
        ).all():
            row.is_representative = False
            session.add(row)
        session.add(event)
        return
    ordered = sorted(members, key=lambda view: view.order_key())
    representative = ordered[0]
    for view in ordered:
        wanted = view is representative
        if bool(view.member.is_representative) != wanted:
            view.member.is_representative = wanted
            session.add(view.member)
    event.representative_article_id = representative.article.id
    event.member_count = len(ordered)
    event.publisher_count = len({view.publisher_key for view in ordered})
    event.first_seen_at = min(ordered, key=lambda view: view.first_seen_ts).member.first_seen_at
    event.last_member_at = max(ordered, key=lambda view: view.first_seen_ts).member.first_seen_at
    title, title_source = _resolve_title(event, ordered, representative)
    event.title = title
    event.title_source = title_source
    session.add(event)


def merge_events(
    session: Session,
    source_event_id: int,
    target_event_id: int,
    *,
    now: dt.datetime | None = None,
    reason: str = "manual",
) -> dict[str, Any]:
    """把 ``source`` 并入 ``target``:成员迁移、写 redirect、源事件置 merged、目标重算代表 / 计数。

    目标若自身已被合并则沿 redirect 落到终点;指向源事件的旧 redirect 与 story / follows 链一并改指目标。
    """

    stamp = _iso(_utcnow(now))
    source = session.get(EventRecord, int(source_event_id))
    if source is None:
        raise EventBoardError("event_not_found", "事件不存在", status_code=404)
    resolved_target, _ = resolve_redirect(session, int(target_event_id))
    target = session.get(EventRecord, resolved_target)
    if target is None:
        raise EventBoardError("target_not_found", "目标事件不存在", status_code=404)
    if int(source.id or 0) == int(target.id or 0):
        raise EventBoardError("same_event", "不能把事件并入自身", status_code=409)
    if source.status == "merged":
        raise EventBoardError("source_merged", "该事件已被合并", status_code=409)
    if target.status == "merged":
        raise EventBoardError("target_merged", "目标事件已被合并", status_code=409)
    _assert_local(source)
    _assert_local(target)
    source_id = int(source.id or 0)
    target_id = int(target.id or 0)

    session.exec(
        update(EventMemberRecord)
        .where(EventMemberRecord.event_id == source_id)
        .values(event_id=target_id, is_representative=False, assigned_by="manual", confidence=None)
    )
    session.exec(
        update(EventRedirectRecord)
        .where(EventRedirectRecord.new_event_id == source_id)
        .values(new_event_id=target_id)
    )
    session.exec(
        update(EventRecord)
        .where(EventRecord.follows_event_id == source_id, EventRecord.id != target_id)
        .values(follows_event_id=target_id, updated_at=stamp)
    )
    session.exec(
        update(EventRecord)
        .where(EventRecord.story_id == source_id, EventRecord.id != target_id)
        .values(story_id=target_id, updated_at=stamp)
    )
    session.expire_all()
    source = session.get(EventRecord, source_id)
    target = session.get(EventRecord, target_id)
    assert source is not None and target is not None
    session.add(EventRedirectRecord(
        old_event_id=source_id,
        new_event_id=target_id,
        reason=reason,
        created_at=stamp,
    ))
    source.status = "merged"
    source.member_count = 0
    source.publisher_count = 0
    source.representative_article_id = None
    if target.follows_event_id == source_id:
        target.follows_event_id = None
    if target.story_id == source_id:
        target.story_id = None
    source.updated_at = stamp
    session.add(source)
    _recompute_event(session, target, stamp=stamp)
    session.commit()
    session.refresh(target)
    return {"event": admin_event(session, target), "merged_from": source_id}


def detach_member(
    session: Session,
    event_id: int,
    article_id: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """把一篇成员摘出成新单成员事件;ledger ``blocked_event_ids_json`` 追加原事件,自动分配不再回流。"""

    current = _utcnow(now)
    stamp = _iso(current)
    event = session.get(EventRecord, int(event_id))
    if event is None:
        raise EventBoardError("event_not_found", "事件不存在", status_code=404)
    if event.status == "merged":
        raise EventBoardError("source_merged", "该事件已被合并", status_code=409)
    _assert_local(event)
    member = session.get(EventMemberRecord, (int(event_id), str(article_id)))
    if member is None:
        raise EventBoardError("member_not_found", "该文章不在这个事件里", status_code=404)
    member_total = len(session.exec(
        select(EventMemberRecord.article_id).where(EventMemberRecord.event_id == int(event_id))
    ).all())
    if member_total <= 1:
        raise EventBoardError("sole_member", "事件只有这一篇成员,无需摘出", status_code=409)
    old_id = int(event_id)
    first_seen_ts = _ts(member.first_seen_at)
    status = "open" if first_seen_ts and (current.timestamp() - first_seen_ts) <= WINDOW_HOURS * 3600 else "closed"
    new_event = EventRecord(
        status=status,
        title="",
        title_source="",
        representative_article_id=None,
        member_count=0,
        publisher_count=0,
        first_seen_at=member.first_seen_at,
        last_member_at=member.first_seen_at,
        assignment_version=event.assignment_version,
        authority_id="",
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(new_event)
    session.flush()
    new_id = int(new_event.id or 0)
    session.exec(
        update(EventMemberRecord)
        .where(EventMemberRecord.event_id == old_id, EventMemberRecord.article_id == str(article_id))
        .values(event_id=new_id, is_representative=True, assigned_by="manual", confidence=None)
    )
    session.expire_all()
    ledger = session.get(EventAssignmentLedgerRecord, str(article_id))
    if ledger is None:
        ledger = EventAssignmentLedgerRecord(
            article_id=str(article_id),
            status="assigned",
            created_at=stamp,
            updated_at=stamp,
        )
    try:
        blocked = json.loads(ledger.blocked_event_ids_json or "[]")
    except (TypeError, ValueError):
        blocked = []
    if not isinstance(blocked, list):
        blocked = []
    if old_id not in blocked:
        blocked.append(old_id)
    ledger.blocked_event_ids_json = json.dumps(blocked)
    ledger.updated_at = stamp
    session.add(ledger)
    old_event = session.get(EventRecord, old_id)
    new_event = session.get(EventRecord, new_id)
    assert old_event is not None and new_event is not None
    _recompute_event(session, old_event, stamp=stamp)
    _recompute_event(session, new_event, stamp=stamp)
    session.commit()
    session.refresh(new_event)
    return {"event": admin_event(session, new_event), "detached_from": old_id}


__all__ = [
    "ADMIN_STATUSES", "BOARD_LIMIT", "EMPTY_REASON_NO_EVENTS", "EventBoardError", "MAX_WINDOW_HOURS",
    "MIN_PUBLISHERS", "NEW_HOURS", "SNAPSHOT_CRON", "SNAPSHOT_JOB_ID", "WINDOW_HOURS",
    "build_rank_snapshot", "detach_member", "list_events", "merge_events", "read_board", "read_event",
    "resolve_redirect", "shanghai_date", "visible_member_links",
]
