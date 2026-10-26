---
name: dorami-issue-loop
description: 本仓一条 issue 从开工到合入的固定闭环(worktree 分支 → 实现 + 自测 → 沙箱自查 → 文档同波 → 提交 → 交叉检视 → 用户目检 → PR / CI → 放行后 squash 合入)。用户说「开工 issue N」「按流程推进」「直接开工」时使用;发版另走 tag。
---

# Dorami issue 闭环

全文与每步的「为什么」在 `docs/dev/workflow.md`;本技能是执行清单。用户 2026-09-16 确认:说「直接开工 / 按流程推进」即按此执行,不必再问。

1. **开工**:`gh issue view N`;主仓 `git fetch` 不离开 main;从 `origin/main` 开 worktree `~/Codes/worktrees/DoramiSourceArchive/<slug>`,分支 `feat/issue-N-<slug>`;软链主仓 `.venv` 与 `frontend/node_modules`;**worktree 里不跑 `uv sync` / 带项目同步的 `uv run`**。主仓有脏文件时别 pull 别 stash。
2. **实现 + 自测**:范围做全(同类处独立盘点);`npm run lint` / `npm test` / `npm run build`、pytest;测试命令 `cmd > log 2>&1; rc=$?`;补守卫并做反向对照。
3. **沙箱自查**:按 `dorami-sandbox-acceptance` 技能 / `docs/dev/sandbox-acceptance.md`;Playwright 截图 + 断言,亮暗 + 移动壳。
4. **文档同波**:按 `dorami-wave-docs` 技能(version-history 年表 + 详细段、backlog、decisions、方案归档);版本号不动;跑 `python3 scripts/check_docs.py`。
5. **提交**:显式列文件(永不 `-a`;`uv.lock` 只在改依赖 / 改版本时按 `docs/dev/dependencies.md` 流程重解析后提交,否则还原本机镜像改写不带),中文 message + `Co-Authored-By` 行;先不 push。
6. **交叉检视(PR 前)**:由不同于实现方的模型检视;任务书 + 报告写 worktree `.review/`;逐条表态、一致前不改、成批修、复检只对照清单;默认两轮;工具操作细节在个人技能 `dorami-codex-review`。分歧与结论写进方案文档检视节。
7. **交用户目检**:可复制的验收步骤(沙箱地址、账号、页面与预期);**当前会话明确说「可以」才继续**。难验收的纯后端 / 纯文档 PR 可在检视 + CI 绿后直接合入(2026-09-19 口径)。
8. **push + PR**:正文 `Closes #N` + 验证摘要 + 署名行;等 CI 绿;用户放行后 `gh pr merge --squash`;清 worktree。
9. **发版独立**:合入 ≠ 上线;发版人跑 `scripts/release.sh X.Y.Z`,部署按 tag(`docs/ops/release-process.md`)。
