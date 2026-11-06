"""源目录读服务:「源 X 的属性是什么」的唯一答案(issue #262 源模型重构 R1a / R1c)。

三层模型里的「源」:谁说的、长什么样、给谁看。今天它散在三处——抓取器注册表的类属性
(62 个预设源)、``source_configs`` 行(管理员配置源 / 读者自定源 / 播客目录 / 同步行),
以及只在 ``articles`` 里出现的 id;每个消费方各自决定谁压谁、查不到时当成什么,口径互相
矛盾(方案 §3 C3、§5)。本模块把合并规则收成一处:

- **行优先的字段**:可见性与运维相关的事实只在行上——``owner_username``、
  ``collection_authority_id``、``ai_analysis_enabled``、``is_active``、``retired_at``、绑定。
- **身份 / 呈现 / 策展字段**:注册表类 > 行 > 内建兜底(日报、下线墓碑、播客目录)。
  R1c 之后 62 个预设源在外网都有镜像行且与类属性逐字段相等,所以这条顺序不改变结果。
- **都没有**:返回 ``origin="archive"`` 的占位视图——名称是裸 id、角色 ``media``、
  形态按调用方给的 ``content_type`` 证据推断。占位只在这里生成,消费方不再各写兜底。

本模块只读,不写库;按 viewer 计算的资格(谁能看、能订阅)在调用方,不在这里缓存。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Iterable, Mapping, Optional

from sqlmodel import Session, select

from models.db import SourceConfigRecord


USER_SOURCE_PREFIX = "user_rss_"
DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"

SHAPES = ("article", "social", "podcast")
SOCIAL_CONTENT_TYPES = frozenset({"social_post"})
PODCAST_CONTENT_TYPES = frozenset({"podcast_episode"})
X_SOURCE_TYPES = frozenset({"x", "twitter", "x_timeline"})
PODCAST_SOURCE_TYPES = frozenset({"podcast", "podcast_rss"})

# 来路(origin):源行从哪来。空串 = R1c 之前的存量行或同步行(按配置源处理)。
ORIGIN_CATALOG = "catalog"      # 策展目录(预设类镜像行、播客目录;R2 起来自目录文件)
ORIGIN_ADMIN = "admin"          # 管理员配置源
ORIGIN_READER = "reader"        # 读者自定源(user_rss_)
ORIGIN_INGEST = "ingest"        # 收录通道遇到未登记出处自动建的源(推送 / 手工录入 / 社交导入)
ORIGIN_LEGACY = "legacy"        # R1c 回填:重构前只在 articles 里出现的公共 id
ORIGIN_SYSTEM = "system"        # 系统源(公共日报)
ORIGIN_ARCHIVE = "archive"      # 没有行也没有注册表类:只读占位,从不落库
ORIGINS = (
    ORIGIN_CATALOG, ORIGIN_ADMIN, ORIGIN_READER, ORIGIN_INGEST,
    ORIGIN_LEGACY, ORIGIN_SYSTEM,
)

ADMISSION_PROVISIONAL = "provisional"
ADMISSION_STATUSES = ("admitted", "incubating", ADMISSION_PROVISIONAL)

# 信息角色(与 source_naming / sourceTaxonomy.js 同一判定序:个人→榜单→媒体→官方)。
PERSONAL_SCOPES = frozenset({
    "personal_commentary", "expert_commentary", "executive_commentary", "expert_newsletter",
})
LEADERBOARD_SCOPES = frozenset({
    "community", "developer_community", "research_community", "forum",
    "ai_benchmark_platform", "ai_benchmark_analysis",
})
MEDIA_SCOPES = frozenset({"ai_media", "tech_media"})
DEFAULT_ROLE = "media"

_CURATION_FIELDS = (
    "source_owner",
    "source_brand",
    "source_scope",
    "source_channel",
    "provenance_tier",
    "signal_strength",
    "noise_risk",
    "fetch_reliability",
)


def role_from_metadata(scope: str, tier: str) -> str:
    """策展元数据 → 角色;两项都空 = 身份不可考,不给官方待遇。"""
    scope = str(scope or "")
    tier = str(tier or "")
    if tier == "tier2_personal_social" or scope in PERSONAL_SCOPES:
        return "personal"
    if scope in LEADERBOARD_SCOPES:
        return "leaderboard"
    if scope in MEDIA_SCOPES or tier == "tier1_curated":
        return "media"
    return "official" if (scope or tier) else DEFAULT_ROLE


def shape_from_content_type(content_type: str) -> str:
    content_type = str(content_type or "")
    if content_type in SOCIAL_CONTENT_TYPES:
        return "social"
    if content_type in PODCAST_CONTENT_TYPES:
        return "podcast"
    return "article"


def normalize_admission(value: object) -> str:
    """未知准入值按正式处理(与注册表 ``admission_status_of`` 同口径),``provisional`` 是新值。"""
    status = str(value or "").strip().lower()
    return status if status in ADMISSION_STATUSES else "admitted"


@dataclass(frozen=True)
class SourceView:
    """一个源的全部属性(只读快照)。"""

    source_id: str
    origin: str
    name: str
    description: str = ""
    icon: str = ""
    avatar_url: str = ""
    shape: str = "article"
    content_type: str = ""
    platform: str = ""
    default_kind: str = ""
    language: str = ""
    homepage_url: str = ""
    base_url: str = ""
    source_owner: str = ""
    source_brand: str = ""
    source_scope: str = ""
    source_channel: str = ""
    provenance_tier: str = ""
    signal_strength: str = ""
    noise_risk: str = ""
    fetch_reliability: str = ""
    admission_status: str = "admitted"
    daily: bool = False
    owner_username: str = ""
    collection_authority_id: str = ""
    ai_analysis_enabled: bool = True
    is_active: bool = True
    retired_at: str = ""
    fetcher_id: str = ""
    source_type: str = ""
    params: Mapping[str, object] = field(default_factory=dict)
    registered: bool = False   # 注册表里有同名抓取器类(兼容期标志)
    configured: bool = False   # 库里有源行
    is_template: bool = False

    @property
    def role(self) -> str:
        if self.is_private:
            return "custom"
        return role_from_metadata(self.source_scope, self.provenance_tier)

    @property
    def is_private(self) -> bool:
        """读者自定源:``owner_username`` 非空或 ``user_rss_`` 前缀(结构性规则,不是兜底)。"""
        return bool(self.owner_username) or self.source_id.startswith(USER_SOURCE_PREFIX)

    @property
    def known(self) -> bool:
        return self.origin != ORIGIN_ARCHIVE

    @property
    def provisional(self) -> bool:
        return self.admission_status == ADMISSION_PROVISIONAL

    @property
    def retired(self) -> bool:
        return bool(self.retired_at)

    @property
    def synced(self) -> bool:
        return bool(self.collection_authority_id)

    @property
    def publisher(self) -> str:
        return (self.source_owner or "").strip()


# ── 读法开关([sources] model) ───────────────────────────────────────────────

MODEL_COMPAT = "compat"
MODEL_FULL = "full"
_model = MODEL_COMPAT


def configure(*, model: str = MODEL_COMPAT) -> None:
    """启动时按 ``[sources] model`` 设定:compat = 注册表类压源行;full = 源行为准。"""
    global _model
    _model = MODEL_FULL if str(model or "").strip().lower() == MODEL_FULL else MODEL_COMPAT


def current_model() -> str:
    return _model


# ── 注册表与内建兜底(进程内常量,缓存) ──────────────────────────────────────


@lru_cache(maxsize=1)
def _registry_views() -> dict[str, SourceView]:
    try:
        from fetchers.registry import admission_status_of, fetcher_registry
    except Exception:  # noqa: BLE001 - 注册表不可用时只按行与占位
        return {}
    if not fetcher_registry._fetchers:
        fetcher_registry.discover()
    views: dict[str, SourceView] = {}
    for source_id, cls in fetcher_registry._fetchers.items():
        is_template = bool(getattr(cls, "is_template", False))
        shape = str(getattr(cls, "content_shape", "") or "article").strip().lower()
        views[source_id] = SourceView(
            source_id=source_id,
            origin=ORIGIN_CATALOG,
            name=str(getattr(cls, "name", "") or source_id),
            description=str(getattr(cls, "description", "") or ""),
            icon=str(getattr(cls, "icon", "") or ""),
            shape=shape if shape in SHAPES else "article",
            content_type=str(getattr(cls, "content_type", "") or ""),
            platform=str(getattr(cls, "platform", "") or "").strip().lower(),
            default_kind=str(getattr(cls, "default_kind", "") or ""),
            base_url=str(
                getattr(cls, "source_url", "")
                or getattr(cls, "listing_url", "")
                or getattr(cls, "feed_url", "")
                or ""
            ),
            admission_status=admission_status_of(getattr(cls, "admission_status", "")),
            daily=(
                not is_template
                and admission_status_of(getattr(cls, "admission_status", "")) == "admitted"
            ),
            fetcher_id=source_id,
            registered=True,
            is_template=is_template,
            **{name: str(getattr(cls, name, "") or "") for name in _CURATION_FIELDS},
        )
    return views


def registry_view(source_id: str) -> Optional[SourceView]:
    return _registry_views().get(source_id)


def registry_source_ids(*, include_templates: bool = False) -> list[str]:
    return sorted(
        sid for sid, view in _registry_views().items()
        if include_templates or not view.is_template
    )


def clear_registry_cache() -> None:
    """测试或热重载后重建注册表视图。"""
    _registry_views.cache_clear()


def _builtin_view(source_id: str) -> Optional[SourceView]:
    """没有行也没有注册表类的内建 id:公共日报、下线墓碑、播客目录常量。"""
    if source_id == DAILY_BRIEF_SOURCE_ID:
        from services.source_naming import SOURCE_FRIENDLY_NAMES

        return SourceView(
            source_id=source_id,
            origin=ORIGIN_SYSTEM,
            name=SOURCE_FRIENDLY_NAMES.get(source_id, source_id),
            shape="article",
            content_type="daily_brief",
            source_owner="dorami",
            source_scope="aggregator",
            provenance_tier="tier1_curated",
            fetcher_id="",
        )
    try:
        from fetchers.registry import DECOMMISSIONED_FETCHER_IDS
        from services.source_naming import SOURCE_FRIENDLY_NAMES
    except Exception:  # noqa: BLE001
        DECOMMISSIONED_FETCHER_IDS, SOURCE_FRIENDLY_NAMES = frozenset(), {}
    if source_id in DECOMMISSIONED_FETCHER_IDS:
        return SourceView(
            source_id=source_id,
            origin=ORIGIN_CATALOG,
            name=SOURCE_FRIENDLY_NAMES.get(source_id, source_id),
            shape="social" if source_id.startswith("x_") else "article",
            content_type="social_post" if source_id.startswith("x_") else "",
            platform="x" if source_id.startswith("x_") else "",
            retired_at="decommissioned",
            is_active=False,
        )
    try:
        from services.podcast_catalog import catalog_by_id

        catalog_source = _podcast_catalog().get(source_id)
    except Exception:  # noqa: BLE001
        catalog_source = None
    if catalog_source is not None:
        return SourceView(
            source_id=source_id,
            origin=ORIGIN_CATALOG,
            name=str(catalog_source.name or source_id),
            description=str(getattr(catalog_source, "description", "") or ""),
            shape="podcast",
            content_type="podcast_episode",
            source_owner=str(getattr(catalog_source, "source_owner", "") or ""),
            source_scope=str(catalog_source.source_scope or ""),
            provenance_tier=str(catalog_source.provenance_tier or ""),
            admission_status="incubating",
            fetcher_id="generic_podcast_rss",
        )
    return None


@lru_cache(maxsize=1)
def _podcast_catalog() -> dict:
    from services.podcast_catalog import catalog_by_id

    return dict(catalog_by_id())


# ── 行 → 视图 ───────────────────────────────────────────────────────────────


def _params(row: SourceConfigRecord) -> dict:
    try:
        value = json.loads(row.params_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bound_class(fetcher_id: str):
    if not fetcher_id:
        return None
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001
        return None
    return fetcher_registry.get_class(fetcher_id)


def _row_shape(row: SourceConfigRecord, source_type: str) -> str:
    explicit = str(getattr(row, "shape", "") or "").strip().lower()
    if explicit in SHAPES:
        return explicit
    cls = _bound_class(row.fetcher_id or "")
    if cls is not None:
        shape = str(getattr(cls, "content_shape", "") or "").strip().lower()
        if shape in SHAPES:
            return shape
    if source_type in X_SOURCE_TYPES:
        return "social"
    if source_type in PODCAST_SOURCE_TYPES:
        return "podcast"
    return "article"


def _row_content_type(row: SourceConfigRecord, source_type: str, shape: str) -> str:
    cls = _bound_class(row.fetcher_id or "")
    if cls is not None and getattr(cls, "content_type", ""):
        return str(cls.content_type)
    if shape == "social" or source_type in X_SOURCE_TYPES:
        return "social_post"
    if shape == "podcast" or source_type in PODCAST_SOURCE_TYPES:
        return "podcast_episode"
    if source_type in {"rss", "atom"}:
        return "rss_article"
    if source_type in {"web", "webpage"}:
        return "web_article"
    return ""


def _row_platform(row: SourceConfigRecord, source_type: str) -> str:
    explicit = str(getattr(row, "platform", "") or "").strip().lower()
    if explicit:
        return explicit
    cls = _bound_class(row.fetcher_id or "")
    if cls is not None and getattr(cls, "platform", ""):
        return str(cls.platform).strip().lower()
    if source_type in X_SOURCE_TYPES:
        return "x"
    return ""


def _row_origin(row: SourceConfigRecord) -> str:
    origin = str(getattr(row, "origin", "") or "").strip().lower()
    if origin in ORIGINS:
        return origin
    if (row.owner_username or "").strip() or row.source_id.startswith(USER_SOURCE_PREFIX):
        return ORIGIN_READER
    return ORIGIN_ADMIN


def view_from_row(row: SourceConfigRecord) -> SourceView:
    source_type = (row.source_type or "").strip().lower()
    shape = _row_shape(row, source_type)
    return SourceView(
        source_id=row.source_id,
        origin=_row_origin(row),
        name=str(row.name or "") or row.source_id,
        description=str(row.description or ""),
        icon=str(getattr(row, "icon", "") or ""),
        avatar_url=str(getattr(row, "avatar_url", "") or ""),
        shape=shape,
        content_type=_row_content_type(row, source_type, shape),
        platform=_row_platform(row, source_type),
        default_kind=str(getattr(row, "default_kind", "") or ""),
        language=str(getattr(row, "language", "") or ""),
        homepage_url=str(getattr(row, "homepage_url", "") or ""),
        base_url=str(row.base_url or row.url or ""),
        admission_status=normalize_admission(row.admission_status),
        daily=bool(getattr(row, "daily", False)),
        owner_username=str(row.owner_username or ""),
        collection_authority_id=str(row.collection_authority_id or ""),
        ai_analysis_enabled=bool(row.ai_analysis_enabled),
        is_active=bool(row.is_active),
        retired_at=str(row.retired_at or ""),
        fetcher_id=str(row.fetcher_id or ""),
        source_type=source_type,
        params=_params(row),
        configured=True,
        **{name: str(getattr(row, name, "") or "") for name in _CURATION_FIELDS},
    )


_ROW_OWNED_FIELDS = (
    "owner_username",
    "collection_authority_id",
    "ai_analysis_enabled",
    "is_active",
    "retired_at",
    "source_type",
    "params",
    "avatar_url",
    "language",
    "homepage_url",
)


_IDENTITY_FIELDS = (
    "name", "description", "icon", "shape", "content_type", "platform", "default_kind",
    "base_url", "admission_status", "daily", *_CURATION_FIELDS,
)


def _merge(registry: SourceView, row: SourceView) -> SourceView:
    """注册表类与行并存:身份 / 呈现 / 策展取类(compat)或取行(full),运维与可见性事实取行。"""
    if _model == MODEL_FULL:
        identity = {
            name: getattr(row, name) if getattr(row, name) not in ("", None) else getattr(registry, name)
            for name in _IDENTITY_FIELDS
        }
        registry = replace(registry, **identity)
    merged = replace(
        registry,
        configured=True,
        origin=ORIGIN_CATALOG,
        fetcher_id=row.fetcher_id or registry.fetcher_id,
        **{name: getattr(row, name) for name in _ROW_OWNED_FIELDS},
    )
    if not merged.admission_status:
        merged = replace(merged, admission_status=row.admission_status)
    return merged


def archive_placeholder(source_id: str, content_type: str = "") -> SourceView:
    """没有行、没有注册表类、也不是内建 id:只读占位,永不落库。"""
    return SourceView(
        source_id=source_id,
        origin=ORIGIN_ARCHIVE,
        name=source_id,
        shape=shape_from_content_type(content_type),
        content_type=str(content_type or ""),
        platform="x" if content_type in SOCIAL_CONTENT_TYPES and source_id.startswith("x_") else "",
    )


def _chunks(items: list[str], size: int = 400) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def describe(
    session: Session,
    source_ids: Iterable[str],
    *,
    content_types: Optional[Mapping[str, str]] = None,
) -> dict[str, SourceView]:
    """批量取源视图:一次 IN 查询取行,注册表与内建兜底走进程内缓存。

    ``content_types``(source_id → 该源条目的 content_type)只用于给占位视图推断形态。
    """
    wanted = sorted({str(sid).strip() for sid in source_ids if str(sid or "").strip()})
    if not wanted:
        return {}
    rows: dict[str, SourceConfigRecord] = {}
    for chunk in _chunks(wanted):
        for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(chunk))
        ).all():
            rows[row.source_id] = row
    content_types = content_types or {}
    result: dict[str, SourceView] = {}
    for source_id in wanted:
        row = rows.get(source_id)
        registry = registry_view(source_id)
        if row is not None and registry is not None:
            result[source_id] = _merge(registry, view_from_row(row))
        elif row is not None:
            result[source_id] = view_from_row(row)
        elif registry is not None:
            result[source_id] = registry
        else:
            result[source_id] = _builtin_view(source_id) or archive_placeholder(
                source_id, content_types.get(source_id, "")
            )
    return result


def get(session: Session, source_id: str, *, content_type: str = "") -> SourceView:
    sid = str(source_id or "").strip()
    return describe(session, [sid], content_types={sid: content_type})[sid] if sid else archive_placeholder("")


def provisional_source_ids(session: Session) -> set[str]:
    """临时源(``admission_status=provisional``):不计入多源印证 / 独立信源数,默认不进公共日报。"""
    return {
        str(sid) for sid in session.exec(
            select(SourceConfigRecord.source_id).where(
                SourceConfigRecord.admission_status == ADMISSION_PROVISIONAL
            )
        ).all()
    }


def all_known_source_ids(session: Session, *, include_templates: bool = False) -> list[str]:
    """注册表 ∪ 源行(不含 archive 占位)。"""
    ids = set(registry_source_ids(include_templates=include_templates))
    ids.update(session.exec(select(SourceConfigRecord.source_id)).all())
    return sorted(ids)
