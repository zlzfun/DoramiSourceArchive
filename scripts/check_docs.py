#!/usr/bin/env python3
"""文档守卫(文档整理波 issue #210,docs/plans/issue-210-docs-tidy.md §3.4)。

检查项:
  1. 索引覆盖:docs/**/*.md 每篇被 docs/README.md 或所属目录(含上级,限 docs/ 内)的 README.md 以链接或反引号整名收录(不认子串);
  2. 链接:Markdown 相对链接(行内 / 带 title / 引用式定义)与反引号仓内路径指向存在的文件(归档目录只查链接不查反引号路径);
  3. 状态行:docs/{plans,architecture,dev,reference}/ 下的文档首 20 行须有 `> 状态:` 且用词表;
  4. L0 体量:AGENTS.md ≤ MAX_AGENTS_LINES 行且 ≤ 32 KiB;CLAUDE.md ≤ MAX_CLAUDE_LINES 行且含 `@AGENTS.md`;
  5. 文件名:docs/plans/*.md 形如 issue-<N>-<slug>.md。
用法:python3 scripts/check_docs.py [仓根];退出码 = 错误数是否为零。tests/test_docs_index.py 以库方式调用。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

STATUS_WORDS = ("进行中", "排队中", "已落地", "耐久参考", "归档")
STATUS_DIRS = ("docs/plans/", "docs/architecture/", "docs/dev/", "docs/reference/")
MAX_AGENTS_LINES, MAX_AGENTS_BYTES, MAX_CLAUDE_LINES = 220, 32 * 1024, 60
SKIP_PREFIXES = ("docs-site/", "docs/pr-assets/", "docs/assets/", "src/skill_templates/")
# 行内链接:](path) / ](<path>) / ](path "title");引用式定义:[id]: path
LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REF_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*$", re.M)
TICK_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:md|json|yaml|yml|html))`")
PATH_RE = re.compile(
    r"`((?:docs|src|scripts|frontend|tests|alembic|docker|config)/[A-Za-z0-9_./-]+"
    r"\.(?:md|py|jsx|js|mjs|sh|ini|html|json|css|yml|toml))`"
)
# 真实配置文件按设计不入库;部署脚本的测试夹具是冻结副本
PATH_WHITELIST = {"config/backend.ini", "config/production.ini"}
SKIP_PATHS_MARKER = "<!-- check-docs: skip-paths -->"
CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """链接扫描前去掉围栏代码块与行内代码,避免把代码示例里的 `[t](p "title")` 当链接。"""
    return INLINE_CODE_RE.sub("", CODE_BLOCK_RE.sub("", text))


def tracked_files(root: str) -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    return set(out.split())


def run(root: str) -> list[str]:
    errors: list[str] = []
    files = tracked_files(root)
    exists = lambda p: p in files or os.path.isdir(os.path.join(root, p))  # noqa: E731
    docs_md = sorted(f for f in files if f.startswith("docs/") and f.endswith(".md") and not f.startswith(SKIP_PREFIXES))
    # 每个 README 实际收录了什么:解析出的链接目标(归一化路径)+ 反引号整名(精确匹配,不做子串)
    def link_targets(path: str, text: str) -> set[str]:
        out = set()
        plain = strip_code(text)
        for raw in [m.group(1) for m in LINK_RE.finditer(plain)] + REF_DEF_RE.findall(plain):
            if re.match(r"^(https?:|mailto:|#)", raw):
                continue
            out.add(os.path.normpath(os.path.join(os.path.dirname(path), raw.split("#", 1)[0])))
        # 反引号里带目录的路径:按 README 所在目录解析成完整路径(同时接受仓根相对写法)
        for t in TICK_RE.findall(text):
            if "/" in t:
                out.add(os.path.normpath(os.path.join(os.path.dirname(path), t)))
                out.add(os.path.normpath(t))
        return out

    readmes: dict[str, tuple[set[str], set[str]]] = {}
    for f in docs_md:
        if os.path.basename(f) == "README.md":
            text = open(os.path.join(root, f), encoding="utf-8").read()
            readmes[f] = (link_targets(f, text), {t for t in TICK_RE.findall(text) if "/" not in t})
    # 1. 索引覆盖
    for f in docs_md:
        if os.path.basename(f) == "README.md" or f in ("docs/backlog.md", "docs/version-history.md", "docs/decisions.md"):
            continue
        base = os.path.basename(f)
        covered = False
        d = os.path.dirname(f)
        while d.startswith("docs"):
            idx = readmes.get(f"{d}/README.md")
            # 链接 / 带目录的反引号按完整路径精确匹配;裸文件名的反引号只对 README 同目录下的文件生效
            if idx and (f in idx[0] or (base in idx[1] and os.path.dirname(f) == d)):
                covered = True
                break
            d = os.path.dirname(d)
        if not covered:
            errors.append(f"index: {f} 未被 docs/README.md 或所属目录 README 以链接 / 反引号整名收录")
    # 2. 链接与路径
    for f in sorted(x for x in files if x.endswith(".md") and not x.startswith(SKIP_PREFIXES) and "node_modules" not in x):
        text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
        archived = f.startswith("docs/archive/")
        plain = strip_code(text)
        for t in [m.group(1) for m in LINK_RE.finditer(plain)] + REF_DEF_RE.findall(plain):
            if re.match(r"^(https?:|mailto:|#|/)", t) or t in ("url", "http(s", "…", "source_url"):
                continue
            target = os.path.normpath(os.path.join(os.path.dirname(f), t.split("#", 1)[0]))
            if not exists(target):
                errors.append(f"link: {f} -> {t}")
        if archived or SKIP_PATHS_MARKER in text:
            continue
        for m in PATH_RE.finditer(text):
            p = m.group(1)
            if p in PATH_WHITELIST or exists(p):
                continue
            # 行内标注「待建」的路径是计划项,放行
            line = text[text.rfind("\n", 0, m.start()) + 1 : text.find("\n", m.end())]
            if "待建" in line:
                continue
            errors.append(f"path: {f} -> {p}")
    # 3. 状态行
    for f in docs_md:
        if not f.startswith(STATUS_DIRS) or os.path.basename(f) == "README.md":
            continue
        head = "\n".join(open(os.path.join(root, f), encoding="utf-8").read().split("\n")[:20])
        m = re.search(r"^>\s*\*{0,2}状态\*{0,2}[::]\s*(.+)$", head, flags=re.M)
        if not m:
            errors.append(f"status: {f} 首 20 行缺 `> 状态:` 行")
        elif not any(w in m.group(1) for w in STATUS_WORDS):
            errors.append(f"status: {f} 状态词不在词表 {STATUS_WORDS}: {m.group(1)[:40]}")
    # 4. L0 体量
    agents = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
    if agents.count("\n") > MAX_AGENTS_LINES or len(agents.encode()) > MAX_AGENTS_BYTES:
        errors.append(f"l0: AGENTS.md {agents.count(chr(10))} 行 / {len(agents.encode())} B,超 {MAX_AGENTS_LINES} 行或 32 KiB")
    claude = open(os.path.join(root, "CLAUDE.md"), encoding="utf-8").read()
    if claude.count("\n") > MAX_CLAUDE_LINES:
        errors.append(f"l0: CLAUDE.md {claude.count(chr(10))} 行,超 {MAX_CLAUDE_LINES} 行(正文应在 AGENTS.md)")
    if "@AGENTS.md" not in claude:
        errors.append("l0: CLAUDE.md 缺 `@AGENTS.md` 导入")
    # 5. plans 文件名
    for f in docs_md:
        if f.startswith("docs/plans/") and os.path.basename(f) != "README.md" and not re.fullmatch(r"docs/plans/issue-\d+-[a-z0-9-]+\.md", f):
            errors.append(f"name: {f} 应为 docs/plans/issue-<N>-<slug>.md")
    return errors


def main(argv: list[str]) -> int:
    root = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    errors = run(root)
    for e in errors:
        print(e)
    print(f"check_docs: {len(errors)} 个问题")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
