"""源表成为全集:外网侧的源行对账、回填与写入时补建(issue #262 R1c)。

不变量(方案 §1.3-1):每个 ``articles.source_id`` 在源表里恰有一行。本模块负责让它在外网
authority 上成立并保持:

- **镜像行**:62 个预设抓取器类各一行(``origin=catalog``、``source_type=preset``、绑定 = 自身类
  id),目录拥有的字段与类属性逐字段相等;``legacy_export=False``——旧接收端靠注册表认识它们;
- **系统行**:公共日报 ``dorami_daily_brief``(``origin=system``,无绑定);
- **墓碑**:已下线源(``DECOMMISSIONED_FETCHER_IDS``)保留身份、标退役;
- **回填**:只在 ``articles`` / 订阅里出现的 id——公共 id 记 ``origin=legacy``(照旧列出,存量
  不变);``user_rss_*`` 孤儿记私有隔离行(不分析、按「带凭证」处理、不列出、不外发);
- **写入时补建**:注入通道遇到未登记的 id,在同一事务里补一行(``origin=agent``,
  ``admission_status=provisional``:不进发现目录,方案 D2)。

接收端(v2 replica)从不在本地写公共源行(不变量 6):它们只经 Archive Sync 到达。本模块的所有
写入口在接收端直接返回。每一行新建 / 改写都记进迁移台账(F12)。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from models.db import (
    ArticleRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceIdentityKeyRecord,
    SourceMigrationLedgerRecord,
)
from services import source_catalog, source_identity, sync_consumer_policy


_logger = logging.getLogger("dorami.source_rows")

LEDGER_BATCH = "r1c"
PRESET_SOURCE_TYPE = "preset"
SYSTEM_SOURCE_TYPE = "system"
ARCHIVE_SOURCE_TYPE = "archive"

# 镜像行上「目录拥有」的字段(E4):对账以注册表类为准;运维字段(is_active、ai_analysis_enabled、
# 调度、隐藏)不在其中,对账永不改写。
CATALOG_OWNED_FIELDS = (
    "name",
    "description",
    "icon",
    "shape",
    "platform",
    "default_kind",
    "source_owner",
    "source_brand",
    "source_scope",
    "source_channel",
    "provenance_tier",
    "signal_strength",
    "noise_risk",
    "fetch_reliability",
    "base_url",
    "admission_status",
    "daily",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def local_public_writes_allowed(session: Session) -> bool:
    """本机能否写公共源行:v2 接收端(或已有接收状态的节点)一律不能。"""
    return not sync_consumer_policy.v2_receiver_state_present(session)


def _row_snapshot(row: SourceConfigRecord) -> dict[str, Any]:
    return {key: value for key, value in row.model_dump().items()}


def _ledger(
    session: Session,
    source_id: str,
    action: str,
    *,
    pre_image: Optional[dict[str, Any]] = None,
    now: str,
) -> None:
    session.add(SourceMigrationLedgerRecord(
        batch=LEDGER_BATCH,
        source_id=source_id,
        action=action,
        pre_image_json=json.dumps(pre_image or {}, ensure_ascii=False, sort_keys=True, default=str),
        created_at=now,
    ))


def _catalog_values(view: source_catalog.SourceView) -> dict[str, Any]:
    return {
        "name": view.name,
        "description": view.description,
        "icon": view.icon,
        "shape": view.shape,
        "platform": view.platform,
        "default_kind": view.default_kind,
        "source_owner": view.source_owner,
        "source_brand": view.source_brand,
        "source_scope": view.source_scope,
        "source_channel": view.source_channel,
        "provenance_tier": view.provenance_tier,
        "signal_strength": view.signal_strength,
        "noise_risk": view.noise_risk,
        "fetch_reliability": view.fetch_reliability,
        "base_url": view.base_url,
        "admission_status": view.admission_status,
        "daily": view.daily,
    }


def _register_identity_keys(session: Session, source_id: str, keys: Iterable[str], *, now: str) -> None:
    for key in keys:
        if not key:
            continue
        existing = session.get(SourceIdentityKeyRecord, key)
        if existing is None:
            session.add(SourceIdentityKeyRecord(key=key, source_id=source_id, created_at=now))
        elif existing.source_id != source_id:
            # 预设(策展源)赢:身份键指向策展源,临时源的同键记录让位。
            existing.source_id = source_id
            session.add(existing)


def _preset_identity_keys(source_id: str) -> list[str]:
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001
        return []
    cls = fetcher_registry.get_class(source_id)
    if cls is None or str(getattr(cls, "platform", "") or "").strip().lower() != "x":
        return []
    return source_identity.social_identity_keys(
        "x",
        handle=str(getattr(cls, "handle", "") or ""),
        user_id=str(getattr(cls, "user_id", "") or ""),
    )


# ── 对账:镜像行、系统行、墓碑 ────────────────────────────────────────────────


def mirror_source_type(view: source_catalog.SourceView) -> str:
    """镜像行的 ``source_type``:X 预设记 ``x_timeline``,其余记 ``preset``。

    新代码判形态不看它(显式 ``shape`` 列优先);这样选是为了外网回滚到 R1c 之前的版本时,
    旧代码「配置行优先、只按 source_type 判形态」的路径仍把 X 预设判成社交,而 ``preset``
    不被任何按类型枚举的旧入口(fetch-active-rss / web、自定源 feed 去重)当成可执行配置源。
    """
    if view.shape == "social" and view.platform == "x":
        return "x_timeline"
    return PRESET_SOURCE_TYPE


def is_mirror_row(row: SourceConfigRecord) -> bool:
    """预设抓取器的镜像行:来路 catalog 且注册表里有同名非模板类。"""
    if (row.origin or "") != source_catalog.ORIGIN_CATALOG:
        return False
    view = source_catalog.registry_view(row.source_id)
    return view is not None and not view.is_template


def _reconcile_mirror(session: Session, view: source_catalog.SourceView, *, now: str) -> str:
    row = session.get(SourceConfigRecord, view.source_id)
    values = _catalog_values(view)
    if row is None:
        row = SourceConfigRecord(
            source_id=view.source_id,
            source_type=mirror_source_type(view),
            url=view.base_url,
            fetcher_id=view.source_id,
            origin=source_catalog.ORIGIN_CATALOG,
            legacy_export=False,
            is_active=True,
            ai_analysis_enabled=True,
            created_at=now,
            updated_at=now,
            **values,
        )
        session.add(row)
        _ledger(session, view.source_id, "created", now=now)
        _register_identity_keys(session, view.source_id, _preset_identity_keys(view.source_id), now=now)
        return "created"
    if (row.owner_username or "").strip() or (row.collection_authority_id or "").strip():
        # 与预设同 id 的读者源 / 同步行:不碰(身份冲突交管理员处理)。
        _logger.warning("源 %s 与预设抓取器同 id 但不是本机公共行,跳过镜像", view.source_id)
        return "skipped"
    changed = {key: value for key, value in values.items() if getattr(row, key) != value}
    adopting = row.origin != source_catalog.ORIGIN_CATALOG
    if not changed and not adopting:
        return "unchanged"
    if adopting:
        # 已有同 id 的管理员配置行:收编为镜像行。目录字段以类为准(E4),前像记账;
        # legacy_export 不改——原本就向旧接收端导出的行照旧导出。
        _ledger(session, view.source_id, "adopted", pre_image=_row_snapshot(row), now=now)
        row.origin = source_catalog.ORIGIN_CATALOG
        if not row.fetcher_id:
            row.fetcher_id = view.source_id
    for key, value in changed.items():
        setattr(row, key, value)
    row.updated_at = now
    session.add(row)
    _register_identity_keys(session, view.source_id, _preset_identity_keys(view.source_id), now=now)
    return "adopted" if adopting else "updated"


def _ensure_system_row(session: Session, *, now: str) -> str:
    view = source_catalog._builtin_view(source_catalog.DAILY_BRIEF_SOURCE_ID)
    row = session.get(SourceConfigRecord, source_catalog.DAILY_BRIEF_SOURCE_ID)
    if row is not None:
        if row.origin != source_catalog.ORIGIN_SYSTEM and not (row.collection_authority_id or "").strip():
            _ledger(session, row.source_id, "adopted", pre_image=_row_snapshot(row), now=now)
            row.origin = source_catalog.ORIGIN_SYSTEM
            row.updated_at = now
            session.add(row)
            return "adopted"
        return "unchanged"
    session.add(SourceConfigRecord(
        source_id=view.source_id,
        name=view.name,
        source_type=SYSTEM_SOURCE_TYPE,
        origin=source_catalog.ORIGIN_SYSTEM,
        shape="article",
        source_owner=view.source_owner,
        source_scope=view.source_scope,
        provenance_tier=view.provenance_tier,
        legacy_export=False,
        is_active=True,
        ai_analysis_enabled=True,
        created_at=now,
        updated_at=now,
    ))
    _ledger(session, view.source_id, "created", now=now)
    return "created"


def _ensure_tombstone_rows(session: Session, *, now: str) -> int:
    try:
        from fetchers.registry import DECOMMISSIONED_FETCHER_IDS
    except Exception:  # noqa: BLE001
        return 0
    created = 0
    for source_id in sorted(DECOMMISSIONED_FETCHER_IDS):
        row = session.get(SourceConfigRecord, source_id)
        if row is not None:
            continue
        view = source_catalog._builtin_view(source_id)
        session.add(SourceConfigRecord(
            source_id=source_id,
            name=view.name if view else source_id,
            source_type=PRESET_SOURCE_TYPE,
            origin=source_catalog.ORIGIN_CATALOG,
            shape=view.shape if view else "article",
            platform=view.platform if view else "",
            legacy_export=False,
            is_active=False,
            ai_analysis_enabled=True,
            retired_at=now,
            created_at=now,
            updated_at=now,
        ))
        _ledger(session, source_id, "retired", now=now)
        created += 1
    return created


def _mark_podcast_catalog_rows(session: Session, *, now: str) -> int:
    try:
        catalog_ids = set(source_catalog._podcast_catalog())
    except Exception:  # noqa: BLE001
        return 0
    if not catalog_ids:
        return 0
    marked = 0
    for row in session.exec(
        select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(sorted(catalog_ids)))
    ).all():
        if row.origin == source_catalog.ORIGIN_CATALOG:
            continue
        if (row.owner_username or "").strip() or (row.collection_authority_id or "").strip():
            continue
        # 只改来路标记:播客目录在 R2 之前仍是「只插不改」,其余字段不动。
        row.origin = source_catalog.ORIGIN_CATALOG
        session.add(row)
        marked += 1
    return marked


# ── 回填:只在 articles / 订阅里出现的 id ────────────────────────────────────


def _dominant_content_types(session: Session, source_ids: list[str]) -> dict[str, str]:
    counts: dict[str, tuple[int, str]] = {}
    for start in range(0, len(source_ids), 400):
        chunk = source_ids[start:start + 400]
        for sid, content_type, count in session.exec(
            select(ArticleRecord.source_id, ArticleRecord.content_type, func.count())
            .where(ArticleRecord.source_id.in_(chunk))
            .group_by(ArticleRecord.source_id, ArticleRecord.content_type)
        ).all():
            current = counts.get(sid)
            if current is None or int(count or 0) > current[0]:
                counts[sid] = (int(count or 0), str(content_type or ""))
    return {sid: value[1] for sid, value in counts.items()}


def _source_type_for_shape(shape: str, platform: str) -> str:
    if shape == "podcast":
        return "podcast"
    if shape == "social":
        return "x" if platform == "x" else "social"
    return ARCHIVE_SOURCE_TYPE


def _quarantine_row(source_id: str, *, now: str) -> SourceConfigRecord:
    """``user_rss_*`` 孤儿:主人与凭证状态都不可考,一律按「带凭证的私有源」处理。"""
    return SourceConfigRecord(
        source_id=source_id,
        name=source_id,
        source_type=ARCHIVE_SOURCE_TYPE,
        origin=source_catalog.ORIGIN_READER,
        shape="article",
        legacy_export=False,
        is_active=False,
        ai_analysis_enabled=False,
        params_json=json.dumps({"credentialed_private": True, "quarantined": True}),
        created_at=now,
        updated_at=now,
    )


def _legacy_row(
    source_id: str,
    content_type: str,
    *,
    now: str,
    retired: bool = False,
) -> SourceConfigRecord:
    shape = source_catalog.shape_from_content_type(content_type)
    platform = "x" if shape == "social" and source_id.startswith("x_") else ""
    from services.source_naming import SOURCE_FRIENDLY_NAMES

    return SourceConfigRecord(
        source_id=source_id,
        name=SOURCE_FRIENDLY_NAMES.get(source_id, source_id),
        source_type=_source_type_for_shape(shape, platform),
        origin=source_catalog.ORIGIN_LEGACY,
        shape=shape,
        platform=platform,
        legacy_export=False,
        is_active=not retired,
        ai_analysis_enabled=True,
        retired_at=now if retired else None,
        created_at=now,
        updated_at=now,
    )


def _subscription_source_ids(session: Session) -> set[str]:
    from api.sources import subscription_source_ids

    ids: set[str] = set()
    for record in session.exec(select(ReaderSubscriptionRecord)).all():
        try:
            ids.update(subscription_source_ids(record))
        except Exception:  # noqa: BLE001 - 坏订阅行不阻断回填
            continue
    return ids


def backfill_orphan_sources(session: Session, *, now: Optional[str] = None) -> dict[str, int]:
    """为没有行的 id 回填源行;注册表类不在这里(由镜像行负责)。"""
    now = now or _now()
    known = set(session.exec(select(SourceConfigRecord.source_id)).all())
    registry = set(source_catalog.registry_source_ids(include_templates=True))
    article_ids = {
        str(sid) for sid in session.exec(select(ArticleRecord.source_id).distinct()).all() if sid
    }
    orphans = sorted(article_ids - known - registry)
    content_types = _dominant_content_types(session, orphans)
    result = {"legacy": 0, "quarantined": 0, "subscription_only": 0}
    for source_id in orphans:
        if source_id.startswith(source_catalog.USER_SOURCE_PREFIX):
            session.add(_quarantine_row(source_id, now=now))
            result["quarantined"] += 1
        else:
            session.add(_legacy_row(source_id, content_types.get(source_id, ""), now=now))
            result["legacy"] += 1
        _ledger(session, source_id, "created", now=now)
    subscription_only = sorted(
        _subscription_source_ids(session) - known - registry - article_ids
    )
    for source_id in subscription_only:
        if source_id.startswith(source_catalog.USER_SOURCE_PREFIX):
            session.add(_quarantine_row(source_id, now=now))
        else:
            # 订阅了、却既无内容也无登记:已不存在的源。保留身份并退役——订阅者保有退订入口,
            # 发现页不列(方案 §4.2)。
            session.add(_legacy_row(source_id, "", now=now, retired=True))
        _ledger(session, source_id, "created", now=now)
        result["subscription_only"] += 1
    return result


def reconcile_source_rows(session: Session, *, now: Optional[str] = None) -> dict[str, Any]:
    """外网启动对账(幂等):镜像行 → 系统行 → 墓碑 → 播客目录来路 → 孤儿回填。不提交。"""
    if not local_public_writes_allowed(session):
        return {"skipped": "receiver"}
    now = now or _now()
    summary: dict[str, Any] = {"mirror": {}, "system": "", "tombstones": 0, "podcast_catalog": 0}
    for source_id in source_catalog.registry_source_ids():
        view = source_catalog.registry_view(source_id)
        if view is None:
            continue
        outcome = _reconcile_mirror(session, view, now=now)
        summary["mirror"][outcome] = summary["mirror"].get(outcome, 0) + 1
    summary["system"] = _ensure_system_row(session, now=now)
    summary["tombstones"] = _ensure_tombstone_rows(session, now=now)
    summary["podcast_catalog"] = _mark_podcast_catalog_rows(session, now=now)
    session.flush()
    summary["backfill"] = backfill_orphan_sources(session, now=now)
    return summary


def run_startup_reconcile(engine) -> dict[str, Any]:
    with Session(engine) as session:
        summary = reconcile_source_rows(session)
        session.commit()
    if summary.get("skipped"):
        return summary
    _logger.info("源表对账完成: %s", summary)
    return summary


# ── 写入时补建 ──────────────────────────────────────────────────────────────


def ensure_source_row(
    session: Session,
    source_id: str,
    *,
    content_type: str = "",
    origin: str = source_catalog.ORIGIN_AGENT,
    admission_status: str = source_catalog.ADMISSION_PROVISIONAL,
    name: str = "",
    platform: str = "",
    avatar_url: str = "",
    homepage_url: str = "",
    description: str = "",
    identity_keys: Iterable[str] = (),
    now: Optional[str] = None,
) -> Optional[SourceConfigRecord]:
    """保证 ``source_id`` 有一行源;已有行原样返回。接收端返回 None(不本地建公共行)。

    与文章写入同一事务(调用方提交)。注册表类走镜像行;其余按注入通道建临时源。
    """
    sid = str(source_id or "").strip()
    if not sid:
        return None
    row = session.get(SourceConfigRecord, sid)
    if row is not None:
        return row
    if not local_public_writes_allowed(session):
        return None
    now = now or _now()
    registry = source_catalog.registry_view(sid)
    if registry is not None and registry.is_template:
        # 模板不是来源:条目带模板 id 是血统缺陷(如 #262 B4),不给它建源行掩盖。
        _logger.warning("条目 source_id 是抓取模板 %s,不建源行", sid)
        return None
    if registry is not None:
        _reconcile_mirror(session, registry, now=now)
        session.flush()
        return session.get(SourceConfigRecord, sid)
    if sid.startswith(source_catalog.USER_SOURCE_PREFIX):
        row = _quarantine_row(sid, now=now)
        session.add(row)
        _ledger(session, sid, "created", now=now)
        return row
    if sid == source_catalog.DAILY_BRIEF_SOURCE_ID:
        _ensure_system_row(session, now=now)
        session.flush()
        return session.get(SourceConfigRecord, sid)
    shape = source_catalog.shape_from_content_type(content_type)
    platform = (platform or ("x" if shape == "social" and sid.startswith("x_") else "")).strip().lower()
    row = SourceConfigRecord(
        source_id=sid,
        name=(name or "").strip()[:120] or sid,
        source_type=_source_type_for_shape(shape, platform),
        url=homepage_url or "",
        origin=origin,
        shape=shape,
        platform=platform,
        avatar_url=avatar_url or "",
        homepage_url=homepage_url or "",
        base_url=homepage_url or "",
        description=(description or "").strip()[:500],
        admission_status=source_catalog.normalize_admission(admission_status),
        # 旧接收端(legacy 投影)收不到注入通道建的行:那里的表现与今天导入内容相同
        # (条目照常同步、源是只在 articles 里的 id),不会把临时源当成正式源列出。
        legacy_export=False,
        is_active=True,
        ai_analysis_enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    _ledger(session, sid, "created", now=now)
    _register_identity_keys(session, sid, identity_keys, now=now)
    return row


def orphan_article_source_ids(session: Session, *, limit: int = 50) -> list[str]:
    """守卫用:有文章却没有源行的 id(外网上应恒为空)。"""
    known = select(SourceConfigRecord.source_id)
    return [
        str(sid) for sid in session.exec(
            select(ArticleRecord.source_id)
            .where(ArticleRecord.source_id.notin_(known))
            .distinct()
            .limit(limit)
        ).all()
    ]
