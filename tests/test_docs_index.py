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


def _fake_repo(tmp_path, readme: str, files: dict[str, str]):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "AGENTS.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(readme, encoding="utf-8")
    for rel, body in files.items():
        p = docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_index_guard_rejects_substring_and_same_name(tmp_path):
    """反向对照(codex R1-4):只提到 `foo-plan.md` 不算收录 `plan.md`;链到 a/x.md 不算收录 b/x.md;删掉索引行必须报错。"""
    check_docs = _load_check_docs()
    root = _fake_repo(
        tmp_path,
        "- [foo-plan.md](./foo-plan.md)\n- [x](./a/x.md)\n",
        {"foo-plan.md": "# a\n", "plan.md": "# b\n", "a/x.md": "# c\n", "b/x.md": "# d\n"},
    )
    errors = check_docs.run(root)
    assert any(e.startswith("index: docs/plan.md") for e in errors), errors
    assert any(e.startswith("index: docs/b/x.md") for e in errors), errors
    assert not any("docs/foo-plan.md" in e or "docs/a/x.md" in e for e in errors), errors


def test_index_guard_accepts_backtick_whole_name_only(tmp_path):
    check_docs = _load_check_docs()
    root = _fake_repo(tmp_path, "| `x_sources.md` | 覆盖 |\n", {"x_sources.md": "# a\n", "sources.md": "# b\n"})
    errors = check_docs.run(root)
    assert not any("docs/x_sources.md" in e for e in errors), errors
    assert any(e.startswith("index: docs/sources.md") for e in errors), errors


def test_link_guard_sees_titled_and_reference_links(tmp_path):
    """反向对照(codex R1-5):带 title 的行内链接与引用式定义都要查。"""
    check_docs = _load_check_docs()
    root = _fake_repo(
        tmp_path,
        "- [a.md](./a.md)\n- [b.md](./b.md)\n",
        {"a.md": '[gone](./missing-1.md "说明")\n', "b.md": "[gone][ref]\n\n[ref]: ./missing-2.md\n"},
    )
    errors = check_docs.run(root)
    assert any(e == "link: docs/a.md -> ./missing-1.md" for e in errors), errors
    assert any(e == "link: docs/b.md -> ./missing-2.md" for e in errors), errors


def test_index_guard_bare_backtick_only_covers_same_directory(tmp_path):
    """反向对照(codex 复检 R1-4):README 里裸文件名的反引号只算收录同目录文件,不算其它目录的同名文件。"""
    check_docs = _load_check_docs()
    root = _fake_repo(tmp_path, "- `x.md` 与 `a/y.md`\n", {"x.md": "# a\n", "b/x.md": "# b\n", "a/y.md": "# c\n", "b/y.md": "# d\n"})
    errors = check_docs.run(root)
    assert any(e.startswith("index: docs/b/x.md") for e in errors), errors
    assert any(e.startswith("index: docs/b/y.md") for e in errors), errors
    assert not any("docs/x.md" in e or "docs/a/y.md" in e for e in errors), errors


def test_link_guard_ignores_code_examples(tmp_path):
    """反向对照(codex 复检 R1-5):行内代码与围栏代码块里的链接语法不是链接,不得误报。"""
    check_docs = _load_check_docs()
    root = _fake_repo(
        tmp_path,
        "- [a.md](./a.md)\n",
        {"a.md": '示例 `[t](p "title")` 与\n\n```md\n[x](./nope.md)\n[ref]: ./nope2.md\n```\n'},
    )
    errors = check_docs.run(root)
    assert not any(e.startswith("link:") for e in errors), errors

