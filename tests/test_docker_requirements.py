"""docker/requirements.txt 钉版清单与 pyproject 的一致性守卫。

v3.17.0 生产事故复盘:uv.lock 不入库(含开发机镜像源改写),而 `uv export --frozen`
不校验锁与 pyproject 的一致性——生产机用旧锁构建,把已移入 extra 的 torch 栈静默装回。
自此镜像构建的版本事实来源改为入库的导出清单(docker/requirements.txt),
本守卫确保清单与 pyproject 不漂移:改依赖后须重导出并一并提交
(`uv export --frozen --no-dev --no-hashes --no-emit-project -o …`)。
(requirements-rag.txt 已随 v3.31 RAG 退役清仓删除。)
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _requirement_names(path):
    names = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
            if match:
                names.add(match.group(1).lower().replace("_", "-"))
    return names


def _pyproject():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)


def _dep_name(spec: str) -> str:
    return re.split(r"[<>=!\[; ]", spec, 1)[0].lower().replace("_", "-")


def test_base_requirements_cover_core_deps_and_exclude_heavy_ml_stack():
    if tomllib is None:
        return
    data = _pyproject()
    base = _requirement_names(os.path.join(ROOT, "docker", "requirements.txt"))

    for spec in data["project"]["dependencies"]:
        assert _dep_name(spec) in base, f"核心依赖 {spec} 不在 docker/requirements.txt——重导出后提交"

    # 重型 ML 栈绝不该出现在清单里(v3.17.0 生产事故的直接断言;
    # chromadb/sentence-transformers 已随 v3.31 RAG 退役从依赖面整体移除)
    for banned in ("sentence-transformers", "torch", "chromadb"):
        assert banned not in base, f"{banned} 泄漏进瘦身清单 docker/requirements.txt"


def _requirement_pins(path):
    """{名: 整行} —— 同一包多行(按环境标记分)时保留全部行,以行集合比较。"""
    pins = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
            if match:
                pins.setdefault(match.group(1).lower().replace("_", "-"), set()).add(line)
    return pins


def test_extras_pinned_lists_agree_with_base_and_pyproject():
    """extras 钉版清单 docker/requirements-<extra>.txt(issue #126 §4.5,裸机 venv 指纹的输入):
    每个文件对应 pyproject 里声明的 extra;与基础清单同一份 uv.lock 导出——基础清单的每一行必须原样出现在
    extra 清单里(否则 `-r base -r extra` 会互相打架);extra 自己的包必须在里面;重型 ML 栈同样禁入。"""
    if tomllib is None:
        return
    data = _pyproject()
    extras = data["project"].get("optional-dependencies", {})
    base_pins = _requirement_pins(os.path.join(ROOT, "docker", "requirements.txt"))
    docker_dir = os.path.join(ROOT, "docker")
    files = sorted(f for f in os.listdir(docker_dir) if re.match(r"^requirements-[A-Za-z0-9_-]+\.txt$", f))
    assert files, "至少应有 docker/requirements-crawl4ai.txt(拍板 ⑥ extras 钉版导出)"
    for name in files:
        extra = name[len("requirements-"):-len(".txt")]
        assert extra in extras, f"{name} 对应的 extra {extra!r} 未在 pyproject [project.optional-dependencies] 声明"
        pins = _requirement_pins(os.path.join(docker_dir, name))
        for pkg, lines in base_pins.items():
            assert pkg in pins, f"{name} 缺基础清单里的 {pkg}——两份清单须从同一份 uv.lock 导出(见 CLAUDE.md 改依赖流程)"
            assert pins[pkg] == lines, f"{name} 里 {pkg} 的钉版与基础清单不一致: {sorted(pins[pkg])} vs {sorted(lines)}"
        for spec in extras[extra]:
            assert _dep_name(spec) in pins, f"extra {extra} 的 {spec} 不在 {name}"
        for banned in ("sentence-transformers", "torch", "chromadb"):
            assert banned not in pins, f"{banned} 泄漏进 {name}"
