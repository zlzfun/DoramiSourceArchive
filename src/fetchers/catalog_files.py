"""策展源目录文件的加载与校验(issue #262 R2):「有哪些策展源、各是谁」的事实来源。

``src/sources/catalog/`` 下两类文件:

- **源家族文件** ``<id 前缀>.toml``(``rss`` / ``web`` / ``docs`` / ``x`` / ``github`` / ``hf``):每个
  ``[[source]]`` 是一个预设源的身份——名称、图标、简介、形态、准入、策展维度。
- **保留文件**(不是源家族):``system.toml`` 放系统源 ``[[system]]``(公共日报:不经抓取器产出)、
  已下线源的墓碑 ``[[tombstone]]``、新账号默认订阅的缺省名单 ``[reader_defaults]``;
  ``collections.toml`` 放源合集 ``[[collection]]``。这些以前是散在代码里的常量
  (``ESSENTIAL_FETCHER_IDS`` / ``DECOMMISSIONED_FETCHER_IDS`` / ``SOURCE_FRIENDLY_NAMES`` /
  ``SOURCE_COLLECTIONS`` / ``DEFAULT_SUBSCRIPTION_SOURCE_IDS``)。

预设源的身份:抓取器类(``fetchers/impl/``)只写「怎么抓」,不再声明这些;类定义的那一刻由
``BaseFetcher.__init_subclass__`` 把目录里的值回投到类属性上(``fetchers.base.apply_catalog_identity``),
所以读类属性的代码(执行路径里的 ``feed_name`` / ``site_name``、注册元数据、源目录读服务)看到的值
与搬迁前逐字相同,直接 ``import`` 具体类的脚本也一样。

加一个策展源 = 目录文件里加一条 + 一个带 ``source_id`` 的抓取器类;转正 = 把那一条的
``admission`` 从 ``incubating`` 改成 ``admitted``。都走 PR,``git`` 即审计。

目录有任何结构错误、缺保留文件、合集 / 默认订阅引用了目录里没有的源、或者 ``fetchers/impl`` 里的预设类
在目录里没有条目,都直接抛 ``CatalogError``——宁可起不来,也不带着半份目录跑(缺一个文件不能让观察期的
源悄悄变成「未命名 + 正式」,也不能让下线的源悄悄回到读者目录)。

本模块只依赖标准库,不导入注册表;业务代码不直接用它,经 ``services.source_catalog`` 读
(守卫 ``tests/test_source_catalog_boundary.py``)。

TOML 解析用标准库 ``tomllib``(Python 3.11+);3.10 上用 ``tomli``(``pyproject`` 里的条件依赖)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ImportError:  # Python 3.10:项目的 requires-python 仍是 >=3.10
    import tomli as tomllib

# 数据目录在 src/sources/catalog(不是 Python 包,只有 .toml);本模块在 src/fetchers/。
CATALOG_DIR = Path(__file__).resolve().parent.parent / "sources" / "catalog"

# 目录键 → 抓取器类属性。键名取短(目录是给人写的),类属性名是存量。
FIELD_TO_ATTR: dict[str, str] = {
    "name": "name",
    "icon": "icon",
    "description": "description",
    "shape": "content_shape",
    "platform": "platform",
    "default_kind": "default_kind",
    "admission": "admission_status",
    "owner": "source_owner",
    "brand": "source_brand",
    "scope": "source_scope",
    "channel": "source_channel",
    "homepage": "source_url",
    "tier": "provenance_tier",
    "signal": "signal_strength",
    "noise": "noise_risk",
    "reliability": "fetch_reliability",
}
# 每条都必须写明的键。准入必须显式:漏写不能等于转正。
REQUIRED_FIELDS = ("id", "name", "description", "shape", "admission")
SHAPES = ("article", "social", "podcast")
ADMISSIONS = ("admitted", "incubating")
_ENUMS: dict[str, tuple[str, ...]] = {"shape": SHAPES, "admission": ADMISSIONS}

# 保留文件:文件名不是源 id 的前缀,各自只许放下面这些顶层表。
SYSTEM_STEM = "system"
COLLECTIONS_STEM = "collections"
RESERVED_STEMS = (SYSTEM_STEM, COLLECTIONS_STEM)
_SYSTEM_TABLES = ("system", "tombstone", "reader_defaults")
_COLLECTION_TABLES = ("collection",)
# 代码按 id 引用的系统源(``DAILY_BRIEF_SOURCE_ID``):目录里必须有,少了就是文件被改坏了。
REQUIRED_SYSTEM_IDS = ("dorami_daily_brief",)
_COLLECTION_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class CatalogError(ValueError):
    """目录文件结构错误(缺键、未知键、取值不合法、id 重复、放错文件)。"""


@dataclass(frozen=True)
class CatalogEntry:
    """目录里的一条策展源。``fields`` 只含文件里写了的键(不含 ``id``)。"""

    id: str
    file: str
    fields: Mapping[str, str]


@dataclass(frozen=True)
class SystemEntry:
    """系统源:由本系统自己产出内容,没有抓取器、没有采集绑定。"""

    id: str
    name: str
    shape: str
    owner: str = ""
    scope: str = ""
    tier: str = ""


@dataclass(frozen=True)
class TombstoneEntry:
    """已下线的策展源:类与目录条目已删,历史归档仍在;只留身份。"""

    id: str
    name: str
    shape: str
    platform: str = ""


@dataclass(frozen=True)
class CollectionEntry:
    """源合集:发现页的策展视图。``sources`` 有序。"""

    id: str
    name: str
    description: str
    provenance_note: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    """目录的全部内容。``sources`` 按文件名、文件内顺序;其余按文件内顺序。"""

    sources: Mapping[str, CatalogEntry]
    system: Mapping[str, SystemEntry]
    tombstones: Mapping[str, TombstoneEntry]
    collections: tuple[CollectionEntry, ...]
    default_subscriptions: tuple[str, ...]


def _parse_entry(raw: object, file_name: str, index: int) -> CatalogEntry:
    where = f"{file_name} 第 {index + 1} 条"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}:[[source]] 必须是表")
    source_id = raw.get("id")
    if not isinstance(source_id, str) or not source_id.strip() or source_id != source_id.strip():
        raise CatalogError(f"{where}:id 必须是首尾不带空白的非空字符串")
    where = f"{file_name} 的 {source_id}"
    missing = [key for key in REQUIRED_FIELDS if key not in raw]
    if missing:
        raise CatalogError(f"{where}:缺必填键 {missing}")
    unknown = sorted(set(raw) - set(FIELD_TO_ATTR) - {"id"})
    if unknown:
        raise CatalogError(f"{where}:未知键 {unknown}(可用:{sorted(FIELD_TO_ATTR)})")
    fields: dict[str, str] = {}
    for key, value in raw.items():
        if key == "id":
            continue
        if not isinstance(value, str):
            raise CatalogError(f"{where}:{key} 必须是字符串")
        if key in REQUIRED_FIELDS and not value.strip():
            raise CatalogError(f"{where}:{key} 不能为空")
        allowed = _ENUMS.get(key)
        if allowed is not None and value not in allowed:
            raise CatalogError(f"{where}:{key} = {value!r} 不在 {allowed} 里")
        fields[key] = value
    family = Path(file_name).stem
    if not source_id.startswith(f"{family}_"):
        raise CatalogError(f"{where}:id 的前缀与文件名不符(这个文件只放 {family}_* 的源)")
    return CatalogEntry(id=source_id, file=file_name, fields=fields)


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{path.name}:TOML 解析失败:{exc}") from exc


def load_catalog(directory: Path | str = CATALOG_DIR) -> dict[str, CatalogEntry]:
    """读一个目录下的全部源家族文件,返回 id → 条目(按文件名、文件内顺序)。结构错误抛 ``CatalogError``。

    只看 ``[[source]]``;保留文件(``system.toml`` / ``collections.toml``)不在这里,见 ``load``。
    """
    directory = Path(directory)
    files = [path for path in sorted(directory.glob("*.toml")) if path.stem not in RESERVED_STEMS]
    if not files:
        raise CatalogError(f"目录文件缺失:{directory} 下没有 *.toml(部署包是否带上了 src/sources/catalog?)")
    entries: dict[str, CatalogEntry] = {}
    for path in files:
        document = _read(path)
        extra = sorted(set(document) - {"source"})
        if extra:
            raise CatalogError(f"{path.name}:顶层只允许 [[source]],多了 {extra}")
        sources = document.get("source", [])
        if not isinstance(sources, list):
            raise CatalogError(f"{path.name}:source 必须写成 [[source]] 数组")
        if not sources:
            raise CatalogError(f"{path.name}:没有任何 [[source]](空文件多半是事故,不要的文件请删掉)")
        for index, raw in enumerate(sources):
            entry = _parse_entry(raw, path.name, index)
            if entry.id in entries:
                raise CatalogError(f"{path.name} 的 {entry.id}:id 重复(另一处在 {entries[entry.id].file})")
            entries[entry.id] = entry
    return entries


# ── 保留文件:系统源 / 墓碑 / 默认订阅 / 合集 ────────────────────────────────────


def _reserved_document(directory: Path, stem: str, tables: tuple[str, ...]) -> dict:
    path = directory / f"{stem}.toml"
    if not path.is_file():
        raise CatalogError(f"目录文件缺失:{path}(部署包是否带上了 src/sources/catalog?)")
    document = _read(path)
    extra = sorted(set(document) - set(tables))
    if extra:
        raise CatalogError(f"{path.name}:顶层只允许 {list(tables)},多了 {extra}")
    return document


def _tables(document: dict, key: str, file_name: str, *, required: bool) -> list:
    """取 ``[[key]]`` 数组。``required`` 时不许缺、不许空。"""
    if key not in document:
        if required:
            raise CatalogError(f"{file_name}:没有任何 [[{key}]]")
        return []
    value = document[key]
    if not isinstance(value, list):
        raise CatalogError(f"{file_name}:{key} 必须写成 [[{key}]] 数组")
    if required and not value:
        raise CatalogError(f"{file_name}:没有任何 [[{key}]]")
    return value


def _string_table(
    raw: object,
    file_name: str,
    kind: str,
    index: int,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    lists: tuple[str, ...] = (),
) -> tuple[str, dict]:
    """校验一张「全是字符串(``lists`` 里的键是字符串数组)」的表,返回 (id, 字段)。

    ``id`` 总是必填;``required`` 里的键必须有且非空白;枚举键(``shape``)取值受限。
    """
    where = f"{file_name} 第 {index + 1} 个 [[{kind}]]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}:必须是表")
    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip() or entry_id != entry_id.strip():
        raise CatalogError(f"{where}:id 必须是首尾不带空白的非空字符串")
    where = f"{file_name} 的 [[{kind}]] {entry_id}"
    allowed = {"id", *required, *optional, *lists}
    missing = [key for key in (*required, *lists) if key not in raw]
    if missing:
        raise CatalogError(f"{where}:缺必填键 {missing}")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CatalogError(f"{where}:未知键 {unknown}(可用:{sorted(allowed)})")
    fields: dict = {}
    for key, value in raw.items():
        if key == "id":
            continue
        if key in lists:
            fields[key] = _id_list(value, f"{where}:{key}")
            continue
        if not isinstance(value, str):
            raise CatalogError(f"{where}:{key} 必须是字符串")
        if key in required and not value.strip():
            raise CatalogError(f"{where}:{key} 不能为空")
        allowed_values = _ENUMS.get(key)
        if allowed_values is not None and value not in allowed_values:
            raise CatalogError(f"{where}:{key} = {value!r} 不在 {allowed_values} 里")
        fields[key] = value
    return entry_id, fields


def _id_list(value: object, where: str) -> tuple[str, ...]:
    """源 id 的有序名单:字符串数组,每项首尾不带空白、非空、不重复。"""
    if not isinstance(value, list):
        raise CatalogError(f"{where} 必须是字符串数组")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise CatalogError(f"{where} 里的每一项必须是首尾不带空白的非空字符串(这一项:{item!r})")
        if item in seen:
            raise CatalogError(f"{where} 里 {item} 重复")
        seen.append(item)
    return tuple(seen)


def _load_system(directory: Path) -> tuple[dict[str, SystemEntry], dict[str, TombstoneEntry], tuple[str, ...]]:
    file_name = f"{SYSTEM_STEM}.toml"
    document = _reserved_document(directory, SYSTEM_STEM, _SYSTEM_TABLES)

    system: dict[str, SystemEntry] = {}
    for index, raw in enumerate(_tables(document, "system", file_name, required=True)):
        entry_id, fields = _string_table(
            raw, file_name, "system", index, required=("name", "shape"), optional=("owner", "scope", "tier"),
        )
        if entry_id in system:
            raise CatalogError(f"{file_name} 的 [[system]] {entry_id}:id 重复")
        system[entry_id] = SystemEntry(id=entry_id, **fields)
    absent = [entry_id for entry_id in REQUIRED_SYSTEM_IDS if entry_id not in system]
    if absent:
        raise CatalogError(f"{file_name}:缺系统源 {absent}(代码按 id 引用它们)")

    tombstones: dict[str, TombstoneEntry] = {}
    for index, raw in enumerate(_tables(document, "tombstone", file_name, required=False)):
        entry_id, fields = _string_table(
            raw, file_name, "tombstone", index, required=("name", "shape"), optional=("platform",),
        )
        if entry_id in tombstones:
            raise CatalogError(f"{file_name} 的 [[tombstone]] {entry_id}:id 重复")
        if entry_id in system:
            raise CatalogError(f"{file_name} 的 [[tombstone]] {entry_id}:与系统源重 id")
        tombstones[entry_id] = TombstoneEntry(id=entry_id, **fields)

    defaults = document.get("reader_defaults")
    if not isinstance(defaults, dict):
        raise CatalogError(f"{file_name}:缺 [reader_defaults](新账号默认订阅的缺省名单;不想播种就写 subscriptions = [])")
    unknown = sorted(set(defaults) - {"subscriptions"})
    if unknown:
        raise CatalogError(f"{file_name} 的 [reader_defaults]:未知键 {unknown}(可用:['subscriptions'])")
    if "subscriptions" not in defaults:
        raise CatalogError(f"{file_name} 的 [reader_defaults]:缺必填键 ['subscriptions']")
    subscriptions = _id_list(defaults["subscriptions"], f"{file_name} 的 [reader_defaults]:subscriptions")
    return system, tombstones, subscriptions


def _load_collections(directory: Path) -> tuple[CollectionEntry, ...]:
    file_name = f"{COLLECTIONS_STEM}.toml"
    document = _reserved_document(directory, COLLECTIONS_STEM, _COLLECTION_TABLES)
    collections: dict[str, CollectionEntry] = {}
    for index, raw in enumerate(_tables(document, "collection", file_name, required=True)):
        entry_id, fields = _string_table(
            raw, file_name, "collection", index,
            required=("name", "description", "provenance_note"), lists=("sources",),
        )
        where = f"{file_name} 的 [[collection]] {entry_id}"
        if not _COLLECTION_ID.fullmatch(entry_id):
            raise CatalogError(f"{where}:id 必须是小写字母 / 数字的 kebab 标识(它是 API 路径段)")
        if entry_id in collections:
            raise CatalogError(f"{where}:id 重复")
        if not fields["sources"]:
            raise CatalogError(f"{where}:sources 不能为空")
        collections[entry_id] = CollectionEntry(id=entry_id, **fields)
    return tuple(collections.values())


def load(directory: Path | str = CATALOG_DIR) -> Catalog:
    """读整个目录:源家族文件 + 两个保留文件,并核对它们之间的引用。任何错误抛 ``CatalogError``。"""
    directory = Path(directory)
    sources = load_catalog(directory)
    system, tombstones, subscriptions = _load_system(directory)
    collections = _load_collections(directory)

    for entry_id in system:
        if entry_id in sources:
            raise CatalogError(f"{SYSTEM_STEM}.toml 的 [[system]] {entry_id}:与 {sources[entry_id].file} 里的现役源重 id")
    for entry_id in tombstones:
        if entry_id in sources:
            raise CatalogError(
                f"{SYSTEM_STEM}.toml 的 [[tombstone]] {entry_id}:它还在 {sources[entry_id].file} 里"
                "(下线要删掉那一条;已下线的 id 也不能再当现役源用)"
            )
    for collection in collections:
        strangers = [sid for sid in collection.sources if sid not in sources]
        if strangers:
            raise CatalogError(
                f"{COLLECTIONS_STEM}.toml 的 [[collection]] {collection.id}:成员 {strangers} 不是目录里的现役策展源"
            )
    strangers = [sid for sid in subscriptions if sid not in sources and sid not in system]
    if strangers:
        raise CatalogError(
            f"{SYSTEM_STEM}.toml 的 [reader_defaults]:subscriptions 里的 {strangers} 不是目录里的现役策展源或系统源"
        )
    return Catalog(
        sources=sources,
        system=system,
        tombstones=tombstones,
        collections=collections,
        default_subscriptions=subscriptions,
    )


@lru_cache(maxsize=1)
def loaded() -> Catalog:
    """仓内目录的全部内容(进程内只读一次)。"""
    return load()


def catalog() -> Mapping[str, CatalogEntry]:
    """仓内目录里的预设源(id → 条目)。取它就会把整个目录读一遍、校验一遍:注册表扫描前先调一次,
    所以保留文件缺失、合集引用了不存在的源这类错误同样让进程起不来。"""
    return loaded().sources


def identity_attributes(entry: CatalogEntry, defaults: type) -> dict[str, str]:
    """一条目录条目对应的全部身份类属性。文件里没写的键取 ``defaults`` 类(``BaseFetcher``)上的
    缺省值——而不是让某个中间父类的值漏下来:目录是这些属性的唯一来源。"""
    return {
        attr: entry.fields[key] if key in entry.fields else getattr(defaults, attr)
        for key, attr in FIELD_TO_ATTR.items()
    }
