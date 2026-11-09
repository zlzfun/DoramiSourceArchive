#!/usr/bin/env python3
"""已发布迁移文件逐字节守卫(issue #227)。

比较**最新正式 tag**(`vX.Y.Z`)里的 `alembic/versions/*` 与当前工作树:已发布的每个迁移文件都必须仍在原路径、
字节(含文件模式)不变;**新增允许**,改写 / 删除 / 改名(= 删除 + 新增)/ 类型变化一律 fail。

为什么:流水线 worker 第 7 道门按方向解释迁移文件 diff,向前部署时任何 M / D 一律 fail closed、没有放行开关
(`docker/dorami-deploy-worker.example` step_migration_diff)。v3.67.0 发版时 4 个既有迁移被改过(#211 改两处
docstring 里的文档路径、#220 给两个旧 revision 加收养守卫),流水线拒绝、只能走手工路径。这里把同一条规则前移到
CI:PR 一开就红,不等发版那天。只比最新 tag 即可——每个 tag 都经过了对上一个 tag 的核对,归纳下来任何曾发布的
文件都不会变。

用法(仓库根目录):
  python3 scripts/check_released_migrations.py                    # git ls-remote origin 取最新 vX.Y.Z,本地没有就只 fetch 那一个 tag
  python3 scripts/check_released_migrations.py --offline          # 不碰网络:按本地 tag 选基线
  python3 scripts/check_released_migrations.py --baseline v3.67.4 # 显式基线(tag 或提交)
退出码:0 一致 / 1 有违规 / 2 无法确定基线或 git 失败(CI 里都算红,不会静默跳过)。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FORMAL_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
STATUS_WORDS = {"M": "改写", "D": "删除(或改名)", "T": "类型变化", "U": "未合并"}


class GuardError(Exception):
    """无法完成比对(基线缺失 / git 失败):退出码 2。"""


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GuardError(f"git {' '.join(args)} 失败(rc={r.returncode}):{(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def version_key(tag: str) -> tuple[int, int, int]:
    m = FORMAL_TAG.match(tag)
    assert m
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def latest_formal(tags: list[str]) -> str | None:
    formal = [t for t in tags if FORMAL_TAG.match(t)]
    return max(formal, key=version_key) if formal else None


def remote_tags(repo: Path, remote: str) -> dict[str, str]:
    """远端 refs/tags/<name> → 该引用指向的对象 sha(annotated tag 即 tag 对象)。"""
    out = git(repo, "ls-remote", "--tags", "--refs", remote)
    found: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/tags/"):
            found[ref[len("refs/tags/"):]] = sha.strip()
    return found


def local_tags(repo: Path) -> list[str]:
    return [t for t in git(repo, "tag", "--list", "v*").split() if t]


def is_shallow(repo: Path) -> bool:
    return git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"


def ensure_tag_available(repo: Path, remote: str, tag: str, remote_sha: str) -> None:
    """本地没有该 tag 就只取这一个引用(浅仓库保持 depth 1);有则核对与远端是同一个对象。"""
    local = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).strip()
    if not local:
        args = ["fetch", "--no-tags", "--quiet"]
        if is_shallow(repo):
            args.append("--depth=1")
        args += [remote, f"+refs/tags/{tag}:refs/tags/{tag}"]
        git(repo, *args)
        local = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).strip()
        if not local:
            raise GuardError(f"fetch 后本地仍没有 tag {tag}")
    if local != remote_sha:
        raise GuardError(
            f"本地 tag {tag} 指向 {local[:12]},远端 {remote} 是 {remote_sha[:12]}:本地 tag 陈旧或被改写,"
            f"先 `git fetch --tags --force {remote}` 再跑"
        )


def resolve_baseline(repo: Path, remote: str, explicit: str | None, offline: bool) -> tuple[str, str]:
    """返回 (基线名, 提交 sha)。"""
    if explicit:
        sha = git(repo, "rev-parse", "--verify", "--quiet", f"{explicit}^{{commit}}", check=False).strip()
        if not sha:
            raise GuardError(f"基线 {explicit!r} 在本地解析不到提交")
        return explicit, sha
    if offline:
        tag = latest_formal(local_tags(repo))
        if not tag:
            raise GuardError("本地没有任何正式 tag(vX.Y.Z);去掉 --offline 从远端取,或用 --baseline 指定")
    else:
        tags = remote_tags(repo, remote)
        tag = latest_formal(list(tags))
        if not tag:
            raise GuardError(f"远端 {remote} 没有任何正式 tag(vX.Y.Z)")
        ensure_tag_available(repo, remote, tag, tags[tag])
    sha = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}", check=False).strip()
    if not sha:
        raise GuardError(f"tag {tag} 解析不到提交")
    return tag, sha


def released_files(repo: Path, commit: str, versions_dir: str) -> list[str]:
    out = git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", versions_dir)
    return [p for p in out.split("\0") if p]


def diff_against_worktree(repo: Path, commit: str, versions_dir: str) -> list[tuple[str, str]]:
    """基线提交 vs 当前工作树(含索引)的 (状态, 路径);--no-renames 让改名显形为 D + A。"""
    out = git(repo, "diff", "--no-renames", "--name-status", "-z", commit, "--", versions_dir)
    parts = [p for p in out.split("\0")]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(parts):
        status, path = parts[i], parts[i + 1]
        if status:
            pairs.append((status[0], path))
        i += 2
    return pairs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--repo", default=".", help="仓库路径(默认当前目录)")
    ap.add_argument("--remote", default="origin", help="取正式 tag 的远端(默认 origin)")
    ap.add_argument("--baseline", help="显式基线(tag 或提交);给了就不查远端")
    ap.add_argument("--offline", action="store_true", help="不碰网络,按本地 tag 选最新正式版")
    ap.add_argument("--versions-dir", default="alembic/versions", help="迁移目录(默认 alembic/versions)")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    try:
        baseline, commit = resolve_baseline(repo, ns.remote, ns.baseline, ns.offline)
        released = released_files(repo, commit, ns.versions_dir)
        if not released:
            raise GuardError(f"基线 {baseline}({commit[:7]})下 {ns.versions_dir}/ 没有任何文件:基线或目录不对,拒绝放行")
        changes = diff_against_worktree(repo, commit, ns.versions_dir)
    except GuardError as e:
        print(f"check_released_migrations: 无法比对 —— {e}", file=sys.stderr)
        return 2
    added = [p for s, p in changes if s == "A"]
    bad = [(s, p) for s, p in changes if s != "A"]
    if bad:
        print(f"check_released_migrations: 基线 {baseline}({commit[:7]})里的已发布迁移文件被改动:", file=sys.stderr)
        for s, p in sorted(bad, key=lambda x: x[1]):
            print(f"  {s}  {p}  ← {STATUS_WORDS.get(s, s)}", file=sys.stderr)
        print(
            "  已发布的迁移文件一字不改(流水线 worker 向前部署时按字节 fail closed)。要改历史行为请加新 revision 或改\n"
            "  alembic/env.py 的收养逻辑;docstring / 注释 / 路径也不回改旧文件。详见 docs/dev/migrations.md。",
            file=sys.stderr,
        )
        return 1
    print(
        f"check_released_migrations: OK —— 基线 {baseline}({commit[:7]}):{len(released)} 个已发布迁移文件逐字节一致"
        + (f",新增 {len(added)} 个" if added else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
