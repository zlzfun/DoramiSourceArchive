"""uv.lock 入库守卫(issue #171)。

背景:v3.17.0 之前 `uv.lock` 按惯例不入库(理由是开发机的镜像源改写会跟着提交),
镜像构建改用 `uv export` 产出的钉版清单当版本事实来源。代价是**锁文件悄悄陈旧**:
main 上的 uv.lock 长期停在 RAG 时代(chromadb / sentence-transformers 仍在、
mcp 丢了 `<2` 硬上限、oss2 缺席、registry 还是开发机镜像源),而没有任何守卫会因为
「锁与 pyproject 漂移」变红——`docker/requirements.txt` 是从**本地那份**锁导出的,
本地锁是新的,于是两道守卫全绿、入库的锁烂掉。

本守卫只看文本,不 import uv(它不是 Python 包,钉版清单里也没有)、不 import yaml:
1. `uv.lock` 的根包(doramisourcearchive)必须与 pyproject 逐条对上——依赖名集合、
   每条 specifier、extras、dev 组;根包 version 必须等于 src/version.py 的
   单一事实来源(发版会同步改三处,漏掉 uv.lock 在这里变红);
2. 锁里不得出现非常规 registry:开发机换源(tuna / 各云厂商镜像)不许入库;
3. 已退役的重型栈(chromadb / sentence-transformers / torch)不得回到锁里。

每条断言都以**锁文本为输入**(`_assert_*` 函数),配一组反向对照:把真实锁按
#171 描述的那几种坏法突变,守卫必须拒绝。
"""
import os
import re

from tests.test_docker_requirements import _dep_name, _pyproject

ROOT = os.path.join(os.path.dirname(__file__), "..")
LOCK = os.path.join(ROOT, "uv.lock")
VERSION_PY = os.path.join(ROOT, "src", "version.py")

# 允许的索引。官方 pypi 之外的一律拒绝:镜像源只该活在开发机,入库就会把
# 别人的构建指到某个内网/区域镜像上(uv 会按索引 URL 重写锁里的 source 行)。
ALLOWED_REGISTRIES = {"https://pypi.org/simple"}

BANNED_PACKAGES = ("sentence-transformers", "torch", "chromadb")


def _lock_text() -> str:
    with open(LOCK, encoding="utf-8") as fh:
        return fh.read()


def _source_version() -> str:
    with open(VERSION_PY, encoding="utf-8") as fh:
        m = re.search(r'__version__ = "([^"]+)"', fh.read())
    assert m, "src/version.py 里没有 __version__"
    return m.group(1)


# ── 极简 TOML 读取(只覆盖 uv.lock 根包用到的形态,不引 tomli/tomlkit)──

def _root_package(text: str) -> dict:
    """解析 `[[package]] name = "doramisourcearchive"` 那一块。"""
    for block in text.split("[[package]]\n")[1:]:
        if re.match(r'^name = "doramisourcearchive"$', block, re.M):
            return _parse_block(block)
    raise AssertionError("uv.lock 里没有根包 doramisourcearchive——锁文件结构变了?")


def _parse_block(block: str) -> dict:
    """够用的 TOML 解析:顶层键、`[section]` 下的键、字符串数组与 inline table 数组。"""
    out: dict = {}
    section: dict = out
    key = None
    buf: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if key is not None:  # 续读多行数组
            buf.append(line)
            if "]" in line:
                section[key] = _array_items("\n".join(buf))
                key, buf = None, []
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            section = out.setdefault(m.group(1), {})
            continue
        m = re.match(r"^([A-Za-z0-9_-]+) = (.*)$", line)
        if not m:
            continue
        k, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and not value.rstrip().endswith("]"):
            key, buf = k, [value]
        elif value.startswith("["):
            section[k] = _array_items(value)
        else:
            section[k] = value.strip('"')
    return out


def _array_items(value: str) -> list[str]:
    """把 `["a", "b"]` 或 `[{ name = "a", specifier = ">=1" }, …]` 拆成元素。

    inline table 里自带逗号(`{ name = "mcp", specifier = ">=1.27.1,<2" }`),
    所以按**括号深度为 0 的逗号**切,不能用 str.split(",")。
    """
    body = value.strip()
    body = body[body.index("[") + 1: body.rindex("]")]
    items, depth, cur, in_str = [], 0, [], False
    for ch in body:
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in "{[":
            depth += 1
        elif not in_str and ch in "}]":
            depth -= 1
        if ch == "," and depth == 0 and not in_str:
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        items.append(tail)
    # 纯字符串元素去掉引号;inline table 元素原样保留,给 _surface 用正则取键
    return [i[1:-1] if i.startswith('"') and i.endswith('"') else i for i in items if i]


def _surface(entries: list[str]) -> dict[str, str]:
    """inline table 列表 → {规范名: specifier}(无 specifier 时为空串)。"""
    out = {}
    for entry in entries:
        name = re.search(r'name = "([^"]+)"', entry)
        spec = re.search(r'specifier = "([^"]+)"', entry)
        if name:
            out[name.group(1).lower().replace("_", "-")] = spec.group(1) if spec else ""
    return out


def _specifier(spec: str) -> str:
    """`mcp>=1.27.1,<2` → `>=1.27.1,<2`;裸名 → 空串(PEP 508 的 marker 部分不算 specifier)。"""
    head = re.split(r"\[|;", spec, maxsplit=1)[0]
    m = re.search(r"[<>=!~].*$", head)
    return m.group(0).strip() if m else ""


def _pyproject_surfaces() -> dict[str, dict]:
    """pyproject 的声明面,与 uv.lock 根包里的面一一对应。

    分开比而不是摊平成一个 dict:同一个包在不同面可以有不同约束
    (dependency-group dev 里是 pytest>=9.0.3,而 dev extra 里是 pytest>=7.0.0),
    摊平会假报漂移。
    """
    data = _pyproject()
    project = data.get("project", {})
    return {
        "dependencies": {_dep_name(s): _specifier(s) for s in project.get("dependencies", [])},
        "optional-dependencies": {
            extra: {_dep_name(s): _specifier(s) for s in specs}
            for extra, specs in project.get("optional-dependencies", {}).items()
        },
        "dev-dependencies": {
            group: {_dep_name(s): _specifier(s) for s in specs}
            for group, specs in data.get("dependency-groups", {}).items()
        },
    }


# ── 守卫本体(输入是锁文本,便于反向对照)──

def _assert_names(where: str, expected: dict[str, str], locked: dict[str, str]) -> None:
    """裸名面(`dependencies` / `package.optional-dependencies` / `package.dev-dependencies`)。

    uv 只在这些面写名字;**specifier 一律记在 `package.metadata.requires-dist` /
    `requires-dev` 的 inline table 里**,所以这里只比名字集合,约束交给 _assert_specifiers。
    """
    missing = sorted(set(expected) - set(locked))
    extra = sorted(set(locked) - set(expected))
    assert not missing, f"{where}:pyproject 声明了但锁里没有 {missing}——改依赖后跑 `uv lock` 并提交 uv.lock"
    assert not extra, f"{where}:锁里有但 pyproject 没声明 {extra}——通常是锁陈旧,重跑 `uv lock`"


def _assert_specifiers(where: str, expected: dict[str, str], locked: dict[str, str]) -> None:
    _assert_names(where, expected, locked)
    drifted = {n: (expected[n], locked[n]) for n in expected if expected[n] != locked[n]}
    assert not drifted, (
        f"{where}:锁里的 specifier 与 pyproject 不一致(改约束后忘了重解析锁):"
        + "; ".join(f"{n}: pyproject {d!r} vs lock {l!r}" for n, (d, l) in sorted(drifted.items()))
    )


def _assert_registries(text: str) -> None:
    registries = set(re.findall(r'registry = "([^"]+)"', text))
    assert registries, "uv.lock 里一个 registry 都没有——解析或结构变了"
    unexpected = registries - ALLOWED_REGISTRIES
    assert not unexpected, (
        f"uv.lock 里出现了非官方索引 {sorted(unexpected)}——开发机的镜像源改写不许入库,"
        "用 `env -u UV_INDEX_URL -u UV_DEFAULT_INDEX uv lock` 重解析"
    )


def _assert_no_heavy_stack(text: str) -> None:
    packages = {m.lower() for m in re.findall(r'^name = "([^"]+)"', text, re.M)}
    for banned in BANNED_PACKAGES:
        assert banned not in packages, (
            f"{banned} 回到了 uv.lock——它已随 v3.31 RAG 退役从 pyproject 移除,锁是用旧 pyproject 解析的"
        )


def _assert_root_matches_pyproject(text: str) -> None:
    """根包各面对齐 pyproject:裸名面 + 带 specifier 的 metadata.requires-dist / requires-dev。"""
    root = _root_package(text)
    expected = _pyproject_surfaces()

    # 裸名面:名字集合对齐(uv 在这三面不写 specifier)
    _assert_names("dependencies", expected["dependencies"], _surface(root.get("dependencies", [])))

    for extra, specs in expected["optional-dependencies"].items():
        assert extra in root.get("package.optional-dependencies", {}), (
            f"pyproject 声明了 extra {extra!r},锁里没有——重跑 `uv lock`"
        )
        _assert_names(
            f"optional-dependencies.{extra}", specs, _surface(root["package.optional-dependencies"][extra])
        )
    for group, specs in expected["dev-dependencies"].items():
        assert group in root.get("package.dev-dependencies", {}), (
            f"pyproject 声明了 dependency-group {group!r},锁里没有——重跑 `uv lock`"
        )
        _assert_names(f"dev-dependencies.{group}", specs, _surface(root["package.dev-dependencies"][group]))

    # specifier 面:uv 只在这里记约束,必须逐条逐字对上
    metadata = root.get("package.metadata", {})
    requires_dist = _surface(metadata.get("requires-dist", []))
    # requires-dist 覆盖 dependencies + 各 extra(带 marker),不含 dependency-group
    flat_extras = {n: s for specs in expected["optional-dependencies"].values() for n, s in specs.items()}
    _assert_specifiers("metadata.requires-dist", {**expected["dependencies"], **flat_extras}, requires_dist)
    _assert_specifiers(
        "metadata.requires-dev.dev",
        expected["dev-dependencies"]["dev"],
        _surface(root.get("package.metadata.requires-dev", {}).get("dev", [])),
    )

    # 上限/来源这类硬约束单独钉一遍:它们是「锁比 pyproject 旧」最容易吃掉的东西
    # (v3.63 的旧锁里 mcp 就丢过 `<2`,oss2 整条缺席)。
    assert requires_dist.get("mcp", "").startswith(">=1.27.1,<2"), (
        f"锁里 mcp 的 specifier 是 {requires_dist.get('mcp')!r},硬上限丢了"
    )
    assert "oss2" in requires_dist, "锁里没有 oss2(对象存储依赖)"
    assert set(metadata.get("provides-extras", [])) == set(expected["optional-dependencies"]), (
        "provides-extras 与 pyproject 的 optional-dependencies 不一致——重跑 `uv lock`"
    )


def _assert_root_version(text: str) -> None:
    root = _root_package(text)
    source = _source_version()
    assert root.get("version") == source, (
        f"uv.lock 根包 version={root.get('version')!r} 与 src/version.py 的 {source!r} 不一致——"
        "发版要同步三处:src/version.py / pyproject.toml / uv.lock 根包 version 行"
    )


def test_lock_registries_are_official_pypi():
    _assert_registries(_lock_text())


def test_lock_has_no_retired_heavy_stack():
    _assert_no_heavy_stack(_lock_text())


def test_lock_root_package_matches_pyproject():
    _assert_root_matches_pyproject(_lock_text())


def test_lock_root_version_matches_source_of_truth():
    _assert_root_version(_lock_text())


# ── 反向对照:把锁按「陈旧得怎么个陈旧法」突变,守卫必须拒绝 ──

_V63_STALE_MCP = '{ name = "mcp", specifier = ">=1.27.1,<2" }'


def _mutate(text: str, old: str, new: str, count: int = 1) -> str:
    n = text.count(old)
    assert n >= 1, f"突变锚点没找到:{old!r}"
    assert n == count, f"突变锚点 {old!r} 出现 {n} 次,预期 {count}"
    return text.replace(old, new)


MUTATIONS = {
    # v3.63 旧锁的四种真实病征
    "mcp_upper_bound_dropped": (
        lambda t: _mutate(t, _V63_STALE_MCP, '{ name = "mcp", specifier = ">=1.0.0" }'),
        _assert_root_matches_pyproject,
    ),
    "oss2_missing": (
        # v3.63 旧锁整条没有 oss2:裸名面与 specifier 面都缺
        lambda t: _mutate(
            _mutate(t, '    { name = "oss2" },\n', "", count=1),
            '    { name = "oss2", specifier = ">=2.18.4" },\n', "", count=1,
        ),
        _assert_root_matches_pyproject,
    ),
    "dev_group_specifier_stale": (
        # dependency-groups.dev 是 pytest>=9.0.3,旧锁停在 extra 那档 >=7.0.0
        lambda t: _mutate(t, '{ name = "pytest", specifier = ">=9.0.3" }', '{ name = "pytest", specifier = ">=7.0.0" }'),
        _assert_root_matches_pyproject,
    ),
    "extra_added_in_pyproject_but_lock_stale": (
        lambda t: _mutate(t, '    { name = "crawl4ai" },\n', "", count=1),
        _assert_root_matches_pyproject,
    ),
    "mirror_registry_leaked": (
        lambda t: _mutate(t, 'registry = "https://pypi.org/simple"',
                          'registry = "https://pypi.tuna.tsinghua.edu.cn/simple"', count=148),
        _assert_registries,
    ),
    "retired_rag_stack_came_back": (
        lambda t: _mutate(t, '[[package]]\nname = "playwright"\n', '[[package]]\nname = "chromadb"\n'),
        _assert_no_heavy_stack,
    ),
    "root_version_left_behind": (
        # 发版只改了两处:pyproject / src/version.py 已 bump,锁没跟上
        lambda t: _mutate(
            t,
            f'name = "doramisourcearchive"\nversion = "{_source_version()}"',
            'name = "doramisourcearchive"\nversion = "3.0.0-previous"',
        ),
        _assert_root_version,
    ),
}


def test_mutations_are_rejected():
    """每个突变真的改到了文本,且对应守卫抛 AssertionError(否则守卫是空转的)。"""
    import pytest

    original = _lock_text()
    for name, (mutate, check) in sorted(MUTATIONS.items()):
        mutated = mutate(original)
        assert mutated != original, f"突变 {name} 没有改到文本"
        with pytest.raises(AssertionError):
            check(mutated)
