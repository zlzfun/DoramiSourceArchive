"""文档守卫(issue #210):索引覆盖 / 断链 / 状态词表 / L0 体量 / plans 文件名。实现在 scripts/check_docs.py。"""
from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_check_docs():
    path = os.path.join(ROOT, "scripts", "check_docs.py")
    spec = importlib.util.spec_from_file_location("check_docs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_docs_guard_passes():
    check_docs = _load_check_docs()
    errors = check_docs.run(ROOT)
    assert errors == [], "\n".join(errors)


def test_docs_guard_catches_broken_link(tmp_path):
    """反向对照:守卫会咬人——伪造一个仓库快照里加一条断链。"""
    check_docs = _load_check_docs()
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "AGENTS.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("- [a.md](./a.md)\n", encoding="utf-8")
    (docs / "a.md").write_text("[gone](./missing.md)\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    errors = check_docs.run(str(tmp_path))
    assert any(e.startswith("link: docs/a.md") for e in errors), errors
    assert not any(e.startswith("index:") for e in errors), errors
