# 协作流程全文:波次 · 分支 · 检视 · 验收 · 合入 · 发版

> 状态:耐久参考(2026-10-21 自 `CLAUDE.md`「协作流程」与 Claude Code 自动记忆沉淀,文档整理波 issue #210)。摘要在 `AGENTS.md`;本文是每一步的做法与**为什么**。工具专属的操作细节(Herdr 起 codex、轮询脚本、模型参数)不在仓内,见各 Agent 的个人技能。

## 0. 节奏与原则

- **一波一 issue、一 issue 一分支**。设计类工作先出**样页**(scratchpad HTML,本机看;不主动提议发布私有 Artifact 链接)→ 用户逐条拍板(期待**严格复刻样页**而非意译进既有惯例)→ 实现 → 用户**目检**(常有两三轮)→ 检视 → 合入。
- 用户**提问**(「怎么办?/ 是否有必要?」)时先给评估与建议,等拍板再动工;**拍板后自主执行到底**,不再逐步请示。
- 代表平台行为的样页 / 演示不得依赖平台尚未实现的能力:移植线上逻辑并逐例对拍,简化处要明说(2026-10-08 教训:样页里手工标注的事件归并被当成已实现)。
- 样页阶段 Windows 兼容只需理论确认(引擎 / 合成属性 / DPR / ClearType / 字宽),不做测帧矩阵;实现后的 Windows 验收按 issue 约定(如 #176 矩阵)。
- 产品已过「追求夺人眼球」阶段,**好用与稳定优先**:hover 才见的入口一律退役,常态可见的结构性修复优先于导览层。

## 1. 开工

1. `gh issue view N` 拉背景并核对现场(issue 里的行号可能对着旧版代码);没有 issue 先建一条。
2. 主仓库 `git fetch`,**不停在非 main 分支**。主仓有用户的本地脏文件(`uv.lock` 镜像改写、未提交 docs)时**别 pull 别 stash**,直接从 `origin/main` 开 worktree。
3. worktree 路径惯例 `~/Codes/worktrees/DoramiSourceArchive/<slug>`,分支 `feat/issue-N-<slug>`;软链主仓 `.venv` 与 `frontend/node_modules` 即可 lint / build / pytest / Playwright(`.venv` 已在 `.git/info/exclude`)。
4. **worktree 里别跑带项目同步的 `uv run` / `uv sync`**(它按 worktree 的锁改的是主仓 venv,2026-10-15 #184 教训);一次性工具用 `uv run --no-project --with …`。
5. 版本号:从最新正式 tag 开新波时把 `src/version.py` / `pyproject.toml` / `uv.lock` 根包行同步改为下一版本 `X.Y.Z-alpha`;同一波内后续 PR **不再碰版本号**。

## 2. 实现与自测

- 改动按 issue 范围做全:「所有同类处」要独立盘点,不只信 issue 清单。
- 前端 `npm run lint` / `npm test` / `npm run build`;后端 `pytest`;测试命令一律 `cmd > log 2>&1; rc=$?`,不在链里用管道截尾(PR #37 / #80 都因 `| tail` 吞退出码带红用例推送)。
- 有条件就补自动化守卫:前端纯函数 → `node --test`(`frontend/test/*.test.mjs`),交互 / 样式 → `e2e/` 流程接入 `scripts/check_mobile_reader_e2e.py`;**守卫要做一次反向对照证明它会咬人**。
- 改「系统记录」的语义(日报记录、分析行、同步流字段)前先 grep 谁在消费它的派生行(个人早报、readiness、内网流水线)。
- 带 `\uXXXX` 转义的源码不要经会解码转义的编辑工具写入(见各 Agent 附注),写完扫不可见字符。

## 3. 沙箱自查与用户目检

见 [`sandbox-acceptance.md`](./sandbox-acceptance.md):临时 ini + 真实库副本 + `alembic upgrade heads` + 播种账号,后端 8089 / 前端 5174 起步(被占顺延),Playwright 截图 + 断言,亮暗两主题 + 移动壳。
交用户目检时给**可直接复制执行**的验收步骤(沙箱地址、账号、要看的页面与预期);**用户在当前会话里明确说「可以」才进入下一步**,早先的「全按推荐实施」不算放行。

## 4. 文档同波

每波在合入前完成(守卫 `scripts/check_docs.py`):

- `docs/version-history.md`:年表加一行(「现状是什么 / 落在哪」)+ 详细一段(设计取舍、目检返修、检视返修、自测结论)。
- `docs/backlog.md`:进行中条目更新;有意不做的遗留与观察期写清。
- `docs/decisions.md`:拍板 / 明确不做 / 再评估触发各一行,带出处链接。
- 方案文档落地后 `git mv docs/plans/issue-N-<slug>.md docs/archive/`,文首加归档注;立了新规则的话更新 `docs/frontend/conventions.md` 或对应 `docs/architecture/*.md`。
- **`AGENTS.md` / `CLAUDE.md` 只在不变量或命令变化时改**(它们每会话必载,也是 main → master 同步的固定冲突点)。
- 版本号**不动**。

## 5. 提交

- 显式列文件(永不 `git commit -a`);`uv.lock` 的本机镜像改写不带——没改依赖就 `git checkout -- uv.lock`,改了依赖 / 版本则按 [`dependencies.md`](./dependencies.md) 重解析后**必须**提交;中文 message,末尾 `Co-Authored-By: <实际模型> <noreply@anthropic.com>` 或等价署名;先不 push。
- 主仓 stash 栈里用户的长期条目任何会话都不得 pop / drop。

## 6. 交叉检视(合入门禁)

- **本地协商式**:默认由不同于实现方的模型检视(Codex 实现交 Claude Code,Claude Code 实现交 Codex);任务书给 commit 范围 + 方案文档 + 关注面清单 + 已知不必追 + 输出格式,要求编号 findings 按严重度排序;报告写文件再读(worktree `.review/`,已在 `.git/info/exclude`)。
- 收到意见**逐条表态**(接受 / 分歧 + 论据,可援引既有拍板与文档),分歧点讨论到一致——**一致之前不改代码**;修复**成批**做完再请复检,复检只对照修复清单不重新开放全面检视。
- 轮次由执行者控制:默认首轮 + 复检一轮;再有新面先按「触发概率 × 影响」判严重性,非严重记观察期并合入,不追求零意见;P0 / P1 必修。边角问题摆给用户拍板。
- 「检视先于实现」的审计式用法:双方各自独立写报告(不读对方文件防锚定),再逐条表态 + 分歧对齐。
- 设计稿也值得复检(只对照清单,分钟级);跨切面状态机每改一处都可能牵动别处。
- 记录:实现方 / 检视方 / 模型、分歧与结论写进方案文档的检视节。

## 7. PR 与合入

- push → `gh pr create`,正文 `Closes #N` + 验证摘要 + 署名行;等 CI 绿(`ci.yml`:后端两 job 并行 + 汇总门,前端 lint / build)。
- **合入前必须用户本地端到端验收 + 目检放行**,检视通过也不例外;用户放行后 `gh pr merge --squash`,再清 worktree;issue 随 PR 关闭。
- 例外口径(2026-09-19 用户表态):没有可目检界面的纯后端机制修复、纯文档 PR,在检视通过 + 全量套件绿 + CI 绿后可由执行者直接 squash 合入;有前端 / 交互面的仍走目检。合入后不擅自打 tag。
- `gh pr merge` 报冲突时先看输出再做下一步;合入与打 tag 用 `&&` 串起来,tag 前核对 `git log -1 origin/main` 是自己的 squash 提交。

## 8. 发版与部署(独立于合入)

- 合入 ≠ 上线。到点或攒够一个用户可见特性,发版人在 main 上跑 `scripts/release.sh X.Y.Z -m "说明"`(校验 main / 干净 / 与 origin 同步 / 号递增 / tag 不存在 → 去 `-alpha` → 提交 `release: vX.Y.Z` → annotated tag → 推送);`release.yml` 建 Release 并串联 `deploy.yml` 等批准,`sync-master.yml` 同步内网 master。
- worktree 会话里发版:在 scratchpad `git clone` 一份干净 main 跑脚本(worktree 守卫拒绝 `git -C <主仓>`)。
- 全文:[`../ops/release-process.md`](../ops/release-process.md);部署手册 [`../ops/README.md`](../ops/README.md)。

## 9. 内网 `master` 分支

- 内网适配(TLS 校验开关、SSO、内网专属文档 / 契约)**只进 `master`,不进 `main`**;用 worktree 操作 master,主仓不 checkout。
- 同步单向 main → master,按版本 tag 触发(`sync-master.yml`);冲突在 master worktree 手工 `git merge <tag>`,`docs/version-history.md` 整文件取 main;master 上的 `INTRANET_DELTA.md` 是差异面唯一权威登记簿,每次合并都要更新。
- 迁移多头:下游带 `branch_labels` 的支线可长期并存,不做 alembic merge(守卫 `test_main_migration_chain_has_a_single_unlabeled_head`)。

## 10. 纯文档改动的轻量路径(用户 2026-09-15 指定的例外)

这是 §6–§7「PR + 检视 + 用户验收」通用门禁之外、由用户显式指定的例外,只适用于**纯文档 / 截图类**改动(不含 `.claude/`、CI、脚本等会改变 Agent 或流水线行为的文件):可直接在 main 提交推送、不走 PR / CI——先 `gh issue create` 记背景,提交信息 `refs #N`,推送后 `gh issue close N --comment` 带提交号;只暂存目标文件。代码改动仍走分支 + PR + 检视。
