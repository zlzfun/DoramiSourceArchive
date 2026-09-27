#!/bin/bash
# 发布 ref 核验(issue #102 自动部署,方案 §4.1):tag 推送、release.yml 的 dispatch、deploy.yml 的 dispatch
# 三条入口共用同一份核验,输出不可变的 target_sha。
#
#   scripts/verify-release-ref.sh vX.Y.Z            # 通过则 stdout 打印 40 位 target_sha,退出 0
#   GITHUB_OUTPUT 存在时同时写 tag=… / target_sha=…(供后续 job 引用)
#
# 三道核对(任一不过退出 1):
#   1. tag 指向的提交在 origin/main 线上(与 sync-master 同一条规则);
#   2. **目标 tag 里的** src/version.py 版本号 = tag 名——现有 release.yml 从工作区读版本,手动部署旧 tag 时
#      会拿 main 的版本号比对而误拒(codex 检视 P1-02),故一律 `git show <sha>:src/version.py`;
#   3. 轻量 tag 只告警(发版请用 scripts/release.sh 打 annotated tag)。
set -euo pipefail

TAG="${1:-}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::tag 格式错误: '${TAG}'(期望 vX.Y.Z)" >&2; exit 1; }

# 只刷新 origin/main,**不带 --tags**:actions/checkout 对 tag 事件会在全量 fetch 之后再做一次
# `fetch +<commit>:refs/tags/<tag>`,把本地 tag 引用改写成指向提交的轻量 tag;此后 `--tags` 会因
# 「would clobber existing tag」被拒,而 --quiet 连原因都吞掉,脚本静默退出 1(2026-09-17 v3.60.0 首次发版实证)。
# tag 只在本地缺失时单独取(dev 机手工核验的场景);fetch 失败一律显式报错,不再靠 set -e 静默退出。
git fetch --quiet --no-tags origin main || { echo "::error::git fetch origin main 失败" >&2; exit 1; }
if ! git rev-parse -q --verify "refs/tags/${TAG}^{commit}" >/dev/null 2>&1; then
    git fetch --quiet --no-tags origin "refs/tags/${TAG}:refs/tags/${TAG}" \
        || { echo "::error::本地没有 tag ${TAG} 且从 origin 取不到" >&2; exit 1; }
fi
# 远端此刻必须仍有它,且剥离后的提交与本地一致(远端删 tag = 撤销发布;本地 = 本次 run 检出时的那份)
remote="$(git ls-remote origin "refs/tags/${TAG}" "refs/tags/${TAG}^{}" 2>/dev/null)" \
    || { echo "::error::git ls-remote origin 失败" >&2; exit 1; }
[ -n "$remote" ] || { echo "::error::tag ${TAG} 不存在于 origin(已删除 = 撤销发布)" >&2; exit 1; }
peeled="$(printf '%s\n' "$remote" | awk '$2 ~ /\^\{\}$/ {print $1}' | head -1)"
annotated=1
[ -n "$peeled" ] || { annotated=0; peeled="$(printf '%s\n' "$remote" | awk '{print $1}' | head -1)"; }
sha="$(git rev-parse -q --verify "refs/tags/${TAG}^{commit}" 2>/dev/null)" \
    || { echo "::error::本地没有 tag ${TAG}" >&2; exit 1; }
[ "$peeled" = "$sha" ] || { echo "::error::origin 上 ${TAG} 指向 ${peeled:0:7},本地是 ${sha:0:7}(tag 被移动?)" >&2; exit 1; }
if ! git merge-base --is-ancestor "$sha" origin/main; then
    echo "::error::${TAG}(${sha:0:7})不在 main 线上,不是合格发布版。" >&2
    exit 1
fi
version="$(git show "${sha}:src/version.py" | grep -o '__version__ = "[^"]*"' | head -1 | sed 's/.*"\(.*\)"/\1/')"
if [ "v${version}" != "$TAG" ]; then
    echo "::error::tag ${TAG} 与目标提交的版本号 ${version:-?} 不一致——请用 scripts/release.sh 发版。" >&2
    exit 1
fi
# 轻量与否看 origin(ls-remote 有无 ^{} 剥离行):本地引用在 Actions 里可能已被 checkout 改写成指向提交,不可据以判断
if [ "$annotated" != 1 ]; then
    echo "::warning::${TAG} 是轻量 tag(无说明);发版请用 scripts/release.sh 打 annotated tag。" >&2
fi
echo "$sha"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    { echo "tag=${TAG}"; echo "target_sha=${sha}"; } >> "$GITHUB_OUTPUT"
fi
