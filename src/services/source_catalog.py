"""源目录读服务:「源 X 的属性是什么」的唯一答案(issue #262 源模型重构 R1a / R1c)。

三层模型里的「源」:谁说的、长什么样、给谁看。今天它散在三处——抓取器注册表的类属性
(62 个预设源)、``source_configs`` 行(管理员配置源 / 读者自定源 / 播客目录 / 同步行),
以及只在 ``articles`` 里出现的 id;每个消费方各自决定谁压谁、查不到时当成什么,口径互相
矛盾(方案 §3 C3、§5)。本模块把合并规则收成一处:

- **行优先的字段**:可见性与运维相关的事实只在行上——``owner_username``、
  ``collection_authority_id``、``ai_analysis_enabled``、``is_active``、``retired_at``、绑定。
- **身份 / 呈现 / 策展字段**:注册表类 > 行 > 内建兜底(日报、下线墓碑、播客目录)。
  本机对账维护的镜像行与类属性逐字段相等,所以这条顺序通常不改变结果;分得出先后的只有
  「行来自上游同步、上游与本机版本不同」的时候。
- **都没有**:返回 ``origin="archive"`` 的占位视图——名称是裸 id、角色 ``media``、
  形态按调用方给的 ``content_type`` 证据推断。占位只在这里生成,消费方不再各写兜底。

本模块只读,不写库;按 viewer 计算的资格(谁能看、能订阅)在调用方,不在这里缓存。

**读注册表身份的唯一入口**(R1a 余项):业务代码(``api/`` ``services/`` ``mcp_server``、治理脚本)
不再直接 ``import fetcher_registry`` 去读「哪些源、叫什么、什么形态、feed 地址是什么」,一律经这里的
``preset_views`` / ``preset_binding`` / ``preset_metadata`` / ``fetcher_class`` / ``describe`` /
``layers``。
直接碰注册表的只剩执行层(``api/app.py`` 的采集执行、``collection_nodes`` 的绑定解析、抓取器
自身)与本模块;守卫 ``tests/test_source_catalog_boundary.py``。R2 / R3 把身份与绑定参数搬出类
属性时,只改这一个模块的取值来源。

**两层与口径函数**:早报、事件榜、事件归并历来不用上面的合并视图,而是各自拿「源行」与「预设类」
两层按自己的顺序取(角色看行、形态早报与事件榜看行而事件归并看类、主体看类)。``layers`` 把两层
原样交出去,``info_role`` / ``reader_shape`` 是这些口径的唯一写法,与 ``[sources] model`` 无关。
把它们统一成「一律以行为准」是 full 模式的事,不在 R1a。

**目录文件里的系统级条目**(R2):系统源(公共日报)、已下线源的墓碑、源合集、新账号默认订阅的缺省
名单写在 ``src/sources/catalog/system.toml`` 与 ``collections.toml``,不再是散在各模块的常量;
业务代码经这里的 ``tombstone_ids`` / ``builtin_name`` / ``collection_entries`` /
``default_subscription_ids`` 读,``fetchers.catalog_files`` 只有抓取器层与本模块直接用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Iterable, Mapping, Optional

from sqlmodel import Session, select

from fetchers import catalog_files   # 只依赖标准库的目录文件加载器,不会连带导入注册表
from models.db import SourceConfigRecord
from services.source_naming import DEFAULT_SOURCE_ROLE, _role_from_metadata


USER_SOURCE_PREFIX = "user_rss_"
DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"

SHAPES = ("article", "social", "podcast")
# 旧客户端 / 旧数据仍可能带 bulletin(issue #178 撤销「动态」容器并入 article)。
LEGACY_SHAPE_ALIASES = {"bulletin": "article"}
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

# 信息角色词表与判定序的单一定义在 services/source_naming.py(与 sourceTaxonomy.js 逐字一致:
# 个人→榜单→媒体→官方);这里只引用,不另写一份。
DEFAULT_ROLE = DEFAULT_SOURCE_ROLE

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
    return _role_from_metadata(scope, tier, fallback="official" if (scope or tier) else DEFAULT_ROLE)


def normalize_shape(shape: object) -> str:
    """形态归一:合法值原样返回,旧别名(bulletin)映射到 article,其余返回空串。"""
    value = str(shape or "").strip().lower()
    value = LEGACY_SHAPE_ALIASES.get(value, value)
    return value if value in SHAPES else ""


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


# ── 注册表与内建兜底 ───────────────────────────────────────────────────────
#
# 视图按类缓存,但每次取用都对照类上的当前属性值:属性没变就复用同一个视图对象,变了(测试里
# monkeypatch 某个类的准入状态、热重载换了类)就重建。不用进程级的一次性缓存——那样「缓存建在
# 属性被临时改写的那一刻」会把后面的调用全带偏。

# 预设源的采集绑定事实:今天还是抓取器类属性(feed 地址、列表页、X 账号);R2 / R3 把它们搬进
# 目录文件与行上的绑定参数后,只改这里的取值来源。
_BINDING_ATTRS = ("feed_url", "listing_url", "source_url", "handle", "user_id")

_SIGNATURE_ATTRS = (
    "name", "description", "icon", "content_shape", "content_type", "platform", "default_kind",
    "source_url", "listing_url", "feed_url", "admission_status", "is_template", *_CURATION_FIELDS,
)
_view_cache: dict[str, tuple[tuple, SourceView]] = {}
_discovery_retried = False


def _registry():
    global _discovery_retried
    try:
        from fetchers.registry import fetcher_registry
    except Exception:  # noqa: BLE001 - 注册表不可用时只按行与占位
        return None
    if not fetcher_registry._fetchers and not _discovery_retried:
        # 导入期的扫描一个类都没挂上(少见):补扫一次,仍然为空就不再每次调用都重扫。
        _discovery_retried = True
        fetcher_registry.discover()
    return fetcher_registry


def _build_registry_view(source_id: str, cls) -> SourceView:
    from fetchers.registry import admission_status_of

    is_template = bool(getattr(cls, "is_template", False))
    shape = str(getattr(cls, "content_shape", "") or "article").strip().lower()
    admission = admission_status_of(getattr(cls, "admission_status", ""))
    return SourceView(
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
        admission_status=admission,
        daily=(not is_template and admission == "admitted"),
        fetcher_id=source_id,
        registered=True,
        is_template=is_template,
        **{name: str(getattr(cls, name, "") or "") for name in _CURATION_FIELDS},
    )


def _view_of_class(source_id: str, cls) -> SourceView:
    signature = (cls, *(getattr(cls, name, None) for name in _SIGNATURE_ATTRS))
    cached = _view_cache.get(source_id)
    if cached is not None and cached[0] == signature:
        return cached[1]
    view = _build_registry_view(source_id, cls)
    _view_cache[source_id] = (signature, view)
    return view


def _registry_views() -> dict[str, SourceView]:
    registry = _registry()
    if registry is None:
        return {}
    return {
        source_id: _view_of_class(source_id, cls)
        for source_id, cls in list(registry._fetchers.items())
    }


def registry_view(source_id: str) -> Optional[SourceView]:
    registry = _registry()
    if registry is None or not source_id:
        return None
    cls = registry.get_class(source_id)
    return _view_of_class(source_id, cls) if cls is not None else None


def registry_source_ids(*, include_templates: bool = False) -> list[str]:
    return sorted(
        sid for sid, view in _registry_views().items()
        if include_templates or not view.is_template
    )


def clear_registry_cache() -> None:
    """清掉按类缓存的视图(取用时本来就会对照类属性重建,留这个入口给想从零开始的测试)。"""
    _view_cache.clear()


def preset_views(*, include_templates: bool = False) -> dict[str, SourceView]:
    """注册表里的预设源视图,**按注册顺序**(不排序:X 探针取第一个有稳定 user_id 的预设、
    系统源撞库检测同地址后者覆盖前者,都依赖这个顺序)。"""
    return {
        sid: view for sid, view in _registry_views().items()
        if include_templates or not view.is_template
    }


def preset_binding(source_id: str) -> Mapping[str, str]:
    """预设源的采集绑定事实(``feed_url`` / ``listing_url`` / ``source_url`` / ``handle`` /
    ``user_id``,缺的为空串);不是预设源返回空映射。"""
    registry = _registry()
    cls = registry.get_class(str(source_id or "")) if registry is not None and source_id else None
    if cls is None:
        return {}
    return {name: str(getattr(cls, name, "") or "") for name in _BINDING_ATTRS}


def preset_default_kinds() -> dict[str, str]:
    """source_id → 类上声明的内容类型先验(含模板,原样、未归一);只读一个属性、不建视图——
    ``content_classification.source_default_kinds`` 在目录与列表接口里按源调用,要便宜。"""
    registry = _registry()
    if registry is None:
        return {}
    return {
        source_id: str(getattr(cls, "default_kind", "") or "")
        for source_id, cls in list(registry._fetchers.items())
    }


def template_ids() -> set[str]:
    """抓取模板(``generic_*``,``is_template``)的 id:它们是执行基座,不是来源。"""
    registry = _registry()
    if registry is None:
        return set()
    return {
        source_id for source_id, cls in list(registry._fetchers.items())
        if getattr(cls, "is_template", False)
    }


def preset_metadata() -> list[dict]:
    """预设源(含模板)的注册元数据字典——阅读器目录、源管理、MCP ``list_sources`` 沿用的字段全集
    (名称 / 图标 / 简介 / 形态 / 策展维度 / 参数表)。每次调用返回新对象,调用方可以就地改。"""
    registry = _registry()
    return registry.get_all_metadata() if registry is not None else []


def fetcher_class(fetcher_id: str):
    """抓取器 id → 类(没有返回 None)。给「这个源绑的是不是某一类抓取器」这种判定用;
    要源的身份走 ``describe`` / ``registry_view``。"""
    return _bound_class(str(fetcher_id or ""))


def podcast_catalog_source(source_id: str):
    """播客精选目录里的条目(代码常量,不是注册表类);没有返回 None。"""
    try:
        return _podcast_catalog().get(str(source_id or ""))
    except Exception:  # noqa: BLE001 - 目录不可用时按「不在目录里」
        return None


# ── 目录文件里的系统级条目:系统源 / 墓碑 / 合集 / 默认订阅 ──────────────────────

# 系统源产出的条目类型是代码里的事实(谁写的条目谁知道),不进目录文件。
_SYSTEM_CONTENT_TYPES = {DAILY_BRIEF_SOURCE_ID: "daily_brief"}
_TOMBSTONE_CONTENT_TYPES = {"social": "social_post", "podcast": "podcast_episode"}


def system_source_ids() -> frozenset[str]:
    """系统源:由本系统自己产出内容的源(公共日报,以及以后的其它生成流程)。

    它们的条目**不回流**——平台自己加工出来的东西不再当素材:不进公共日报候选、个人早报、榜单、兴趣与
    Taxonomy 的取证,推送也不能往里写。「是不是系统源」一律问这里,不认 ``dorami_`` 前缀、不写死某个 id。
    日报专属的行为(问答的「日报即索引」、删最新一期回退游标等)仍按 ``DAILY_BRIEF_SOURCE_ID`` 判断。
    """
    return frozenset(catalog_files.loaded().system)


def is_system_source(source_id: Optional[str]) -> bool:
    return bool(source_id) and source_id in catalog_files.loaded().system


def system_source_meta(source_id: Optional[str]) -> Optional[dict]:
    """系统源在读者目录与管理面里的展示元数据(名称 / 图标 / 简介 / 条目类型 / 出品方);不是系统源返回 None。

    系统源没有抓取器类,目录元数据不能从注册表来;以前是 ``api.sources.DAILY_BRIEF_SOURCE_META`` 一个常量。
    """
    entry = catalog_files.loaded().system.get(source_id or "")
    if entry is None:
        return None
    return {
        "name": entry.name,
        "icon": entry.icon,
        "desc": entry.description,
        "content_type": _SYSTEM_CONTENT_TYPES.get(entry.id, ""),
        "source_owner": entry.owner,
    }


def system_source_analysis_default(source_id: str) -> bool:
    """这个系统源的条目默认送不送入库分析(目录条目的 ``analysis``,缺省送)。"""
    entry = catalog_files.loaded().system.get(source_id)
    return True if entry is None else bool(entry.analysis)


def tombstone_ids() -> frozenset[str]:
    """已下线的策展源:类与目录条目已删,历史归档仍在。读者目录不回流、收录通道不写入、对账建退役行。"""
    return frozenset(catalog_files.loaded().tombstones)


def builtin_name(source_id: str) -> str:
    """没有抓取器类的内建 id(系统源、墓碑)在目录里登记的名称——历史文章的来源展示要可读名;
    不是内建 id 返回空串。"""
    files = catalog_files.loaded()
    entry = files.system.get(source_id) or files.tombstones.get(source_id)
    return entry.name if entry is not None else ""


def collection_entries() -> tuple[catalog_files.CollectionEntry, ...]:
    """源合集(目录文件里的顺序)。成员都是目录里的现役策展源,加载时已校验。"""
    return catalog_files.loaded().collections


def default_subscription_ids() -> tuple[str, ...]:
    """新账号默认订阅的缺省名单(有序);运维的 KV 覆盖在 ``services.reader_defaults``。"""
    return catalog_files.loaded().default_subscriptions


def _builtin_view(source_id: str) -> Optional[SourceView]:
    """没有行也没有注册表类的内建 id:系统源(公共日报等)、下线墓碑、播客目录常量。"""
    files = catalog_files.loaded()
    system = files.system.get(source_id)
    if system is not None:
        return SourceView(
            source_id=source_id,
            origin=ORIGIN_SYSTEM,
            name=system.name,
            shape=system.shape,
            content_type=_SYSTEM_CONTENT_TYPES.get(source_id, ""),
            source_owner=system.owner,
            source_scope=system.scope,
            provenance_tier=system.tier,
            fetcher_id="",
        )
    tombstone = files.tombstones.get(source_id)
    if tombstone is not None:
        return SourceView(
            source_id=source_id,
            origin=ORIGIN_CATALOG,
            name=tombstone.name,
            shape=tombstone.shape,
            content_type=_TOMBSTONE_CONTENT_TYPES.get(tombstone.shape, ""),
            platform=tombstone.platform,
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


def binding_shape(source_type: str, fetcher_id: str = "") -> str:
    """「类型 + 绑定」→ 形态:绑定的抓取器类优先;没有绑定时 X / social 类型归社交、播客类型归播客。"""
    if fetcher_id:
        cls = _bound_class(fetcher_id)
        if cls is not None:
            shape = normalize_shape(str(getattr(cls, "content_shape", "article")))
            if shape:
                return shape
    source_type = str(source_type or "").strip().lower()
    if source_type in X_SOURCE_TYPES | {"social"}:
        return "social"
    if source_type in PODCAST_SOURCE_TYPES:
        return "podcast"
    return "article"


def binding_content_type(source_type: str, fetcher_id: str = "") -> str:
    """「类型 + 绑定」→ 预期的条目 ``content_type``(零产出的源在目录里也要有类型)。"""
    if fetcher_id:
        cls = _bound_class(fetcher_id)
        if cls is not None:
            return str(getattr(cls, "content_type", "") or "")
    source_type = str(source_type or "").strip().lower()
    if source_type in X_SOURCE_TYPES | {"social"}:
        return "social_post"
    if source_type in PODCAST_SOURCE_TYPES:
        return "podcast_episode"
    if source_type in {"rss", "atom"}:
        return "rss_article"
    if source_type in {"web", "webpage"}:
        return "web_article"
    return ""


def binding_platform(source_type: str, fetcher_id: str = "") -> str:
    """「类型 + 绑定」→ 平台标识:绑定的抓取器类优先,X 类型兜底为 x。"""
    if fetcher_id:
        cls = _bound_class(fetcher_id)
        if cls is not None:
            return str(getattr(cls, "platform", "") or "").strip().lower()
    if str(source_type or "").strip().lower() in X_SOURCE_TYPES:
        return "x"
    return ""


def row_shape(row: object) -> str:
    """源行的形态(阅读侧口径:阅读器目录、早报、事件榜、榜单、文章列表):显式 ``shape`` 列
    (R1c)> 绑定的抓取器类 > ``source_type``。自 ``api.sources`` 原样搬来。"""
    explicit = normalize_shape(getattr(row, "shape", ""))
    if explicit:
        return explicit
    return binding_shape(
        str(getattr(row, "source_type", "") or ""),
        str(getattr(row, "fetcher_id", "") or ""),
    )


# 目录视图(``view_from_row``:源管理里无绑定的源、收录的形态门禁)自己的判法。与上面的阅读侧
# 判法在三处不同:不认 ``source_type=social``、不认旧别名 ``bulletin``、绑定类的形态不合法时不
# 归一。存量如此,R1a 不动;两套并成一套留到 full 模式(backlog)。
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


# ── 两层与口径函数(早报 / 事件榜 / 事件归并) ────────────────────────────────


@dataclass(frozen=True)
class SourceLayers:
    """一个源的两层来历,不合并:消费方按各自沿用的口径取。"""

    source_id: str
    record: Optional[SourceConfigRecord]   # 源行(没有为 None)
    preset: Optional[SourceView]           # 预设类的视图(注册表里没有同名类为 None)

    @property
    def anchored(self) -> bool:
        """有行或有预设类;两者都没有的 id 只在归档里出现(或是内建 id)。"""
        return self.record is not None or self.preset is not None


def layers(
    session: Session,
    source_ids: Iterable[str],
    *,
    rows: Optional[Mapping[str, SourceConfigRecord]] = None,
) -> dict[str, SourceLayers]:
    """批量取两层。键按调用方给的原样(不去空白):消费方拿条目上的 ``source_id`` 回查,
    带空白的脏 id 也要查得到——它没有行也没有类。

    ``rows``:调用方已经取好的源行(source_id → 行,须覆盖本次要查的全部 id)。传了就不再查库
    ——事件榜本来就要拿行做凭证 / 私有判定,不必同一批 id 查两遍。
    """
    wanted = sorted({str(sid) for sid in source_ids if sid})
    if not wanted:
        return {}
    if rows is None:
        loaded: dict[str, SourceConfigRecord] = {}
        for chunk in _chunks(wanted):
            for row in session.exec(
                select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(chunk))
            ).all():
                loaded[row.source_id] = row
        rows = loaded
    return {
        source_id: SourceLayers(source_id, rows.get(source_id), registry_view(source_id))
        for source_id in wanted
    }


def info_role(source: SourceLayers) -> str:
    """信息角色 official / media / personal / leaderboard(评分、早报、事件层用;不含阅读器的 custom)。

    有行:行上的策展范围与信源层级说了算,空值就是空值(两项都空 = 身份不可考 = media),
    不回退到同名的预设类。没有行:预设类 → 播客精选目录 → media。
    """
    from services.source_naming import source_role

    record = source.record
    if record is not None:
        return source_role(
            source.source_id,
            source_scope=record.source_scope or "",
            provenance_tier=record.provenance_tier or "",
        )
    return source_role(source.source_id)


def reader_shape(source: SourceLayers, content_type: str = "") -> str:
    """早报 / 事件榜用的形态:有行看行(``row_shape``),没行看预设类,都没有看条目的 ``content_type``。"""
    if source.record is not None:
        return row_shape(source.record)
    if source.preset is not None:
        return source.preset.shape
    return shape_from_content_type(content_type)


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
