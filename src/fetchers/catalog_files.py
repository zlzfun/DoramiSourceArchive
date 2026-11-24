"""策展源目录文件的加载与校验(issue #262 R2):预设源「是谁」的事实来源。

``src/sources/catalog/*.toml`` 里每个 ``[[source]]`` 是一个策展源的身份——名称、图标、简介、形态、
准入、策展维度。抓取器类(``fetchers/impl/``)只写「怎么抓」,不再声明这些;类定义的那一刻由
``BaseFetcher.__init_subclass__`` 把目录里的值回投到类属性上(``fetchers.base.apply_catalog_identity``),
所以读类属性的代码(执行路径里的 ``feed_name`` / ``site_name``、注册元数据、源目录读服务)看到的值
与搬迁前逐字相同,直接 ``import`` 具体类的脚本也一样。

加一个策展源 = 目录文件里加一条 + 一个带 ``source_id`` 的抓取器类;转正 = 把那一条的
``admission`` 从 ``incubating`` 改成 ``admitted``。都走 PR,``git`` 即审计。

目录有任何结构错误、或者 ``fetchers/impl`` 里的预设类在目录里没有条目,都直接抛 ``CatalogError``
——宁可起不来,也不带着半份目录跑(缺一个文件不能让观察期的源悄悄变成「未命名 + 正式」)。

TOML 解析用标准库 ``tomllib``(Python 3.11+);3.10 上用 ``tomli``(``pyproject`` 里的条件依赖)。
"""

from __future__ import annotations

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


class CatalogError(ValueError):
    """目录文件结构错误(缺键、未知键、取值不合法、id 重复、放错文件)。"""


@dataclass(frozen=True)
class CatalogEntry:
    """目录里的一条策展源。``fields`` 只含文件里写了的键(不含 ``id``)。"""

    id: str
    file: str
    fields: Mapping[str, str]


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


def load_catalog(directory: Path | str = CATALOG_DIR) -> dict[str, CatalogEntry]:
    """读一个目录下的全部 ``*.toml``,返回 id → 条目(按文件名、文件内顺序)。结构错误抛 ``CatalogError``。"""
    directory = Path(directory)
    files = sorted(directory.glob("*.toml"))
    if not files:
        raise CatalogError(f"目录文件缺失:{directory} 下没有 *.toml(部署包是否带上了 src/sources/catalog?)")
    entries: dict[str, CatalogEntry] = {}
    for path in files:
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise CatalogError(f"{path.name}:TOML 解析失败:{exc}") from exc
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


@lru_cache(maxsize=1)
def catalog() -> dict[str, CatalogEntry]:
    """仓内目录(进程内只读一次)。"""
    return load_catalog()


def identity_attributes(entry: CatalogEntry, defaults: type) -> dict[str, str]:
    """一条目录条目对应的全部身份类属性。文件里没写的键取 ``defaults`` 类(``BaseFetcher``)上的
    缺省值——而不是让某个中间父类的值漏下来:目录是这些属性的唯一来源。"""
    return {
        attr: entry.fields[key] if key in entry.fields else getattr(defaults, attr)
        for key, attr in FIELD_TO_ATTR.items()
    }
