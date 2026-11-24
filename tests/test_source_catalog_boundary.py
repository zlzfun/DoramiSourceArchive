"""边界守卫:业务代码不直接读抓取器注册表(issue #262 R1a 余项)。

「有哪些源、叫什么、什么形态、feed 地址是什么」只经 ``services.source_catalog`` 读。直接引用
``fetcher_registry`` 的文件在下面这张名单里逐个登记理由;名单只许减不许增——R3 抓取器去身份时,
要改取值来源的就只有 ``source_catalog`` 一处。

名单里除注册表与读服务以外的文件属于执行层:它们只许 ``fetcher_registry.get_class``(按抓取器 id
取类去实例化 / 校验绑定),不许枚举注册表、不许读注册元数据——那是在读源的身份。

新代码需要源的身份:用 ``source_catalog.describe`` / ``layers`` / ``registry_view`` /
``preset_views`` / ``preset_binding`` / ``preset_metadata``;需要按抓取器 id 拿类做类型判定:用
``source_catalog.fetcher_class``。
"""

import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCANNED = ("src", "scripts")
_REFERENCE = re.compile(r"\bfetcher_registry\b")
_ATTRIBUTE = re.compile(r"\bfetcher_registry\s*\.\s*(\w+)")

# 文件 → 为什么它可以直接碰注册表。
ALLOWED = {
    "src/fetchers/registry.py": "注册表本身",
    "src/services/source_catalog.py": "读注册表身份的唯一入口",
    "src/services/collection_nodes.py": "采集绑定:校验绑定的抓取器存在、执行前把节点解析成抓取器类",
    "src/api/app.py": "采集执行:按抓取器 id 取类并实例化",
    "scripts/collect_taxonomy_bootstrap_sources.py": "治理脚本:实例化抓取器跑隔离采集",
    "scripts/smoke_analysis_release.py": "发版冒烟:实例化抓取器取一条真实条目",
}


# 可以枚举注册表、读注册元数据的只有这两个文件;名单里其余的是执行层。
CORE = {"src/fetchers/registry.py", "src/services/source_catalog.py"}
EXECUTION_LAYER_MAY_CALL = {"get_class"}


def _referencing_files(pattern: re.Pattern = _REFERENCE) -> set[str]:
    found: set[str] = set()
    for top in _SCANNED:
        for folder, _dirs, files in os.walk(os.path.join(_ROOT, top)):
            if "__pycache__" in folder:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    if pattern.search(handle.read()):
                        found.add(os.path.relpath(path, _ROOT).replace(os.sep, "/"))
    return found


def test_only_listed_files_touch_the_fetcher_registry():
    found = _referencing_files()
    unexpected = sorted(found - set(ALLOWED))
    assert not unexpected, (
        "这些文件直接引用了 fetcher_registry;源的身份请经 services.source_catalog 读"
        f"(确属执行层的,在 ALLOWED 里登记理由):{unexpected}"
    )


def test_allow_list_has_no_stale_entries():
    """名单里的文件已经不碰注册表了就把它划掉,免得名单只增不减。"""
    stale = sorted(set(ALLOWED) - _referencing_files())
    assert not stale, f"这些文件已不再引用 fetcher_registry,请从 ALLOWED 删除:{stale}"


def test_execution_layer_files_only_resolve_classes():
    """执行层只许按抓取器 id 取类;``get_all_metadata`` / ``_fetchers`` 这类枚举是读源身份,走读服务。"""
    assert CORE <= set(ALLOWED)
    offenders = {}
    for relative in sorted(set(ALLOWED) - CORE):
        with open(os.path.join(_ROOT, relative), encoding="utf-8") as handle:
            used = set(_ATTRIBUTE.findall(handle.read()))
        extra = used - EXECUTION_LAYER_MAY_CALL
        if extra:
            offenders[relative] = sorted(extra)
    assert not offenders, (
        "执行层文件只许调 fetcher_registry.get_class;枚举 / 读元数据请经 services.source_catalog:"
        f"{offenders}"
    )


# ── 目录文件加载器(R2):只有抓取器层与读服务直接用 ─────────────────────────────

_CATALOG_FILES = re.compile(r"\bcatalog_files\b")
CATALOG_FILES_ALLOWED = {
    "src/fetchers/base.py": "类定义时把目录里的身份回投到类属性",
    "src/fetchers/registry.py": "扫描前先把目录读一遍;派生 default_visible",
    "src/services/source_catalog.py": "读目录的唯一业务入口(墓碑 / 系统源 / 合集 / 默认订阅)",
}
# 搬进目录文件的旧常量:不许再在代码里定义或导入(文档串里提到历史名字不算)。
RETIRED_CONSTANTS = (
    "ESSENTIAL_FETCHER_IDS",
    "FOCUSED_FETCHER_CURATION",
    "DECOMMISSIONED_FETCHER_IDS",
    "SOURCE_FRIENDLY_NAMES",
    "SOURCE_COLLECTIONS",
)


def retired_constant_uses(source: str) -> list[str]:
    """源码里对旧常量的「定义」或「导入 / 取属性」;只在注释与文档串里出现的不算。"""
    import ast

    names = set(RETIRED_CONSTANTS)
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(alias.name.split(".")[-1] for alias in node.names if alias.name.split(".")[-1] in names)
            found.update(alias.asname for alias in node.names if alias.asname in names)
    return sorted(found)


def test_only_the_fetcher_layer_and_the_read_service_load_catalog_files():
    found = _referencing_files(_CATALOG_FILES)
    unexpected = sorted(found - set(CATALOG_FILES_ALLOWED))
    assert not unexpected, (
        "这些文件直接用了 fetchers.catalog_files;墓碑 / 系统源 / 合集 / 默认订阅请经 services.source_catalog 读:"
        f"{unexpected}"
    )
    stale = sorted(set(CATALOG_FILES_ALLOWED) - found)
    assert not stale, f"这些文件已不再用 catalog_files,请从 CATALOG_FILES_ALLOWED 删除:{stale}"


def test_retired_source_constants_do_not_come_back():
    """白名单、墓碑、内建名称、合集的名单只在目录文件里;代码里再出现同名常量就是又分叉出一份。"""
    offenders = {}
    for top in _SCANNED:
        for folder, _dirs, files in os.walk(os.path.join(_ROOT, top)):
            if "__pycache__" in folder:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    used = retired_constant_uses(handle.read())
                if used:
                    offenders[os.path.relpath(path, _ROOT).replace(os.sep, "/")] = used
    assert not offenders, f"这些名单已搬进 src/sources/catalog,请经 services.source_catalog 读:{offenders}"


def test_retired_constant_scanner_tells_code_from_prose():
    assert retired_constant_uses('"""以前叫 ESSENTIAL_FETCHER_IDS。"""\n# SOURCE_COLLECTIONS 已搬走\nx = 1\n') == []
    assert retired_constant_uses("ESSENTIAL_FETCHER_IDS = frozenset()\n") == ["ESSENTIAL_FETCHER_IDS"]
    assert retired_constant_uses("SOURCE_COLLECTIONS: tuple = ()\n") == ["SOURCE_COLLECTIONS"]
    assert retired_constant_uses("from fetchers.registry import DECOMMISSIONED_FETCHER_IDS\n") == [
        "DECOMMISSIONED_FETCHER_IDS"
    ]
    assert retired_constant_uses("from a import b as SOURCE_FRIENDLY_NAMES\n") == ["SOURCE_FRIENDLY_NAMES"]
    assert retired_constant_uses("import registry\nids = registry.DECOMMISSIONED_FETCHER_IDS\n") == [
        "DECOMMISSIONED_FETCHER_IDS"
    ]
    assert retired_constant_uses("def f():\n    return naming.SOURCE_FRIENDLY_NAMES.get('x')\n") == [
        "SOURCE_FRIENDLY_NAMES"
    ]
