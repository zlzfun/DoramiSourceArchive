#!/bin/bash
# SessionStart hook(随 intranet 分支入库,main 上不存在——天然分支隔离):
# 向会话上下文注入 intranet 分支纪律提要与主干同步状态。只检测与提示,
# 不自动 merge——冲突解决是判断性工作,由 Agent/人按 CLAUDE.md 顶部原则执行。
cd "$(dirname "$0")/../.." || exit 0
branch="$(git branch --show-current 2>/dev/null)"
[ "$branch" = "intranet" ] || exit 0

# 尽力刷新 origin/main 引用;无网/超时则降级用本地缓存并注明可能过期
fetch_note=""
if command -v timeout >/dev/null 2>&1; then
    timeout 8 git fetch origin main --quiet 2>/dev/null || fetch_note="(fetch origin 失败,以下基于本地缓存的 origin/main,可能过期)"
else
    git fetch origin main --quiet 2>/dev/null || fetch_note="(fetch origin 失败,以下基于本地缓存的 origin/main,可能过期)"
fi

behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"

echo "【intranet 分支会话提示】当前在内网特殊适配分支 intranet。"
echo "纪律:一切改动只提交本分支,绝不合并/cherry-pick 回 main;同步方向单一 main→intranet。完整须知见 CLAUDE.md 顶部块。"
echo "主干同步状态:落后 origin/main ${behind} 个提交;本分支独有(领先)${ahead} 个。${fetch_note}"
if [ "$behind" != "0" ] && [ "$behind" != "?" ]; then
    echo "⚠️ 已落后主干 ${behind} 个提交。开始新工作前建议先 git merge main 并按 CLAUDE.md 顶部的冲突解决原则处理(部署面以本分支为准;src 以 main 为准且保留 verify=settings.network.tls_verify 接线)。落后的提交:"
    git log --oneline HEAD..origin/main 2>/dev/null | head -10
fi
exit 0
