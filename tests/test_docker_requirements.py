"""docker/requirements*.txt 钉版清单与 pyproject 的一致性守卫。

v3.17.0 生产事故复盘:uv.lock 不入库(含开发机镜像源改写),而 `uv export --frozen`
不校验锁与 pyproject 的一致性——生产机用旧锁构建,把已移入 extra 的 torch 栈静默装回。
自此镜像构建的版本事实来源改为入库的导出清单(docker/requirements.txt / -rag.txt),
本守卫确保清单与 pyproject 不漂移:改依赖后须重导出并一并提交
(`uv export --frozen --no-dev --no-hashes --no-emit-project [-​-extra rag-embedded] -o …`)。
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


def test_base_requirements_cover_core_deps_and_exclude_rag():
    if tomllib is None:
        return
    data = _pyproject()
    base = _requirement_names(os.path.join(ROOT, "docker", "requirements.txt"))

    for spec in data["project"]["dependencies"]:
        assert _dep_name(spec) in base, f"核心依赖 {spec} 不在 docker/requirements.txt——重导出后提交"

    # RAG 重依赖绝不该出现在瘦身清单里(v3.17.0 生产事故的直接断言)
    for banned in ("sentence-transformers", "torch"):
        assert banned not in base, f"{banned} 泄漏进瘦身清单 docker/requirements.txt"


def test_rag_requirements_include_extra():
    if tomllib is None:
        return
    data = _pyproject()
    rag = _requirement_names(os.path.join(ROOT, "docker", "requirements-rag.txt"))

    for spec in data["project"]["dependencies"]:
        assert _dep_name(spec) in rag, f"核心依赖 {spec} 不在 docker/requirements-rag.txt"
    for spec in data["project"]["optional-dependencies"]["rag-embedded"]:
        assert _dep_name(spec) in rag, f"extra 依赖 {spec} 不在 docker/requirements-rag.txt"
    assert "torch" in rag, "rag 变体清单应包含 torch(sentence-transformers 传递依赖)"
