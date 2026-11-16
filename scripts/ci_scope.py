#!/usr/bin/env python3
"""CI 改动面判定(issue #288):PR 只动前端 / 文档时,跳过两个重型后端 job。

`ci.yml` 的 `changes` job 运行本脚本,把 `backend=true|false` 写进 `$GITHUB_OUTPUT`:

- `true`  → 照常跑 `backend-unit` + `backend-deploy`(全量);
- `false` → 两个重型 job 跳过,改跑 `backend-light`:只含 `LIGHT_TESTS` 这几条「读前端 / 文档文件的后端守卫」。

判定只有一条规则,且偏向全量:PR 的**每一个**改动路径都落在 `SKIP_SAFE_DIRS` 或是仓根的 `*.md` 时才是 `false`;
任一路径在外、没有改动路径、不是 `pull_request` 事件、以及一切看不准的情形(HEAD 不是这个 PR 的合并提交、git 出错)
都是 `true`。push 到 main 永远全量——那是「PR 分支落后 main」与本脚本自身出错时的兜底。

这里的两张表是单一事实来源:

- `tests/conftest.py` 的读取审计按它们判定——不在 `LIGHT_TESTS` 里的测试读了 `SKIP_SAFE` 路径即失败,
  所以「跳过重型 job 不丢覆盖」是被执行的,不靠人记;
- `tests/test_ci_workflow.py` 守 `ci.yml` 里 `backend-light` 的命令与 `LIGHT_TESTS` 逐字一致。

只用标准库:runner 的系统 `python3` 直接跑,不装依赖。
"""
from __future__ import annotations

import os
import subprocess
import sys

# 这些目录里的改动不需要重型后端 job(以 `/` 结尾,按仓根相对路径的首段匹配);另加仓根的 `*.md`。
SKIP_SAFE_DIRS = ("frontend/", "docs/", "docs-site/")
# 读上述路径的后端测试:重型 job 被跳过时由 `backend-light` 跑这几条。
LIGHT_TESTS = ("tests/test_docs_index.py", "tests/test_pwa_support.py")


def is_skip_safe(path: str) -> bool:
    """仓根相对的 posix 路径是否属于「改它不需要重型后端 job」的面。形状可疑的路径一律不算。"""
    if not path or path.startswith("/") or "\\" in path:
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if len(parts) == 1:
        return path.endswith(".md")
    return f"{parts[0]}/" in SKIP_SAFE_DIRS


def _git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, timeout=60).stdout


def pr_changed_paths(repo: str, pr_head_sha: str) -> tuple[list[str] | None, str]:
    """PR 合并提交相对 base 的改动路径;看不准时返回 `(None, 原因)`。

    `pull_request` 事件检出的是 `refs/pull/N/merge`:第一父提交是 base 分支尖端,第二父提交是 PR head。
    两者的 diff 正是「这次 CI 测的东西相对 main 改了什么」。只在 HEAD 确实是这样一个合并提交时才信它——
    否则(检出方式被改、浅克隆深度不够)`HEAD^1..HEAD` 只是最后一个提交的改动,会把整条 PR 误判成文档改动。
    `--no-renames`:把改名拆成删除 + 新增两条路径,`src/x.py → docs/x.md` 不会只露出后者。
    """
    try:
        fields = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode("ascii", "replace").split()
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 读不到 HEAD({type(exc).__name__})"
    if len(fields) != 3:
        return None, f"HEAD 有 {max(len(fields) - 1, 0)} 个父提交,不是 PR 的合并提交"
    merge, base, head = fields
    if not pr_head_sha or head != pr_head_sha:
        return None, "合并提交的第二父提交不是事件里的 PR head"
    try:
        raw = _git(repo, "diff", "--name-only", "-z", "--no-renames", base, merge)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git diff 失败({type(exc).__name__})"
    return [os.fsdecode(p) for p in raw.split(b"\0") if p], ""


def decide(event: str, repo: str, pr_head_sha: str) -> tuple[bool, str]:
    """返回 `(是否需要重型后端 job, 给人看的原因)`。"""
    if event != "pull_request":
        return True, f"事件 {event or '(未知)'}:只有 pull_request 按改动面选 job"
    paths, why = pr_changed_paths(repo, pr_head_sha)
    if paths is None:
        return True, f"看不准改动面,按全量:{why}"
    if not paths:
        return True, "PR 没有改动路径,按全量"
    outside = [p for p in paths if not is_skip_safe(p)]
    if outside:
        return True, f"{len(paths)} 个改动路径里有 {len(outside)} 个在前端 / 文档之外,如 {outside[0]}"
    return False, f"{len(paths)} 个改动路径全在 {' '.join(SKIP_SAFE_DIRS)} 与仓根 *.md 之内"


def main() -> int:
    needed, reason = decide(os.environ.get("GITHUB_EVENT_NAME", ""), ".", os.environ.get("PR_HEAD_SHA", ""))
    line = f"backend={'true' if needed else 'false'}"
    print(f"{line}  # {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
