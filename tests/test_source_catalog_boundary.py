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


def _referencing_files() -> set[str]:
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
                    if _REFERENCE.search(handle.read()):
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
