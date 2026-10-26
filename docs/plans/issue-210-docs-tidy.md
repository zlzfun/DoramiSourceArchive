# 文档整理方案(docs-tidy):让文档为 Agent 高效服务

> 状态:进行中——**2026-10-21 用户拍板 D1–D10 全按推荐,实施中**([issue #210](https://github.com/zlzfun/DoramiSourceArchive/issues/210),分支 `feat/issue-210-docs-tidy`,worktree `~/Codes/worktrees/DoramiSourceArchive/docs-tidy`,基线 `origin/main` d887cac v3.66.0)。文中 §2 的路径均为**迁移前**路径,迁移映射见 §6 与实施记录。
<!-- check-docs: skip-paths -->
> 范围:仓内全部文档(`CLAUDE.md` / `AGENTS.md` / `README.md` / `docs/` / `docs-site/` / `specs/` / `artifacts/` / `.claude/`)+ Claude Code 自动记忆(`~/.claude/projects/…/memory/`,42 条)的联合审视。
> 目标:不只是「人能读」,而是 **Claude Code / Codex 等 Agent 每次会话加载更少、定位更准、判断现状不被过期文档误导**。
> 上接 2026-07-20 文档整理波(建三层索引)与 2026-09-15 CLAUDE.md 年表迁出;回应 [issue #89](https://github.com/zlzfun/DoramiSourceArchive/issues/89)(缺整体设计文档)。
> 实现方:Claude Code(Fable 5.1);检视方:Codex(本地协商式交叉检视,合入前)。

## 0. 结论先行

**现状数字**(2026-10-21 盘点,脚本见 §7):

| 项 | 数字 |
|---|---|
| 仓内 Markdown 文档 | 162 篇(`docs/` 115 + `docs-site/` 15 + `specs/` 9 + `artifacts/` 4 + 根与其它 19) |
| `CLAUDE.md` | 500 行 / 119,465 字符 / 158 KB,估算 4–5 万 token,**每次会话必载**;自 5 月起改了 154 次 |
| `docs/version-history.md` | 187 KB(按需读,但排序混杂) |
| `docs/` 顶层方案类文档 | 50 篇,其中 **约 28 篇对应的波次已合入 / 已发版**,索引 `docs/README.md` 仍标 ◉ 活跃(48 个 ◉ 中实际活跃不到 10) |
| 索引盲区 | 顶层 4 篇不在 `docs/README.md`;`docs-site/`(VitePress 用户手册)、`specs/007-*`、`artifacts/issue-7/` 三个目录不在任何 L0/L1 索引;46 页设计样页 HTML 中 11 页零引用 |
| 失效引用 | 22 处反引号路径指向已不存在的文件(§2.4 列表) |
| 自动记忆 | 40 条主题文件 + 索引,约 165 KB;约 1/3 内容已被仓内文档覆盖,约 1/4 是操作手册(该成技能而非记忆) |

**四个结构性问题与处方**:

| # | 问题 | 处方(摘要) |
|---|---|---|
| P1 | **L0 过载**:`CLAUDE.md` 把「每会话必读的硬规则」与「按域查阅的设计说明 / 端点清单 / 目录树 / 年表」混在一起,500 行 vs 官方建议 200 行;Codex 只读 `AGENTS.md`(2 KB 指针,它得再读 158 KB 的 `CLAUDE.md`,且 Codex 项目文档总量封顶 32 KiB) | 三层分发:**L0 常驻 ≤ 200 行**(硬规则 + 命令 + 地图)→ **按路径加载**(`.claude/rules/` 四枚)→ **按需加载**(`docs/architecture/*` 分域设计说明、技能)。`AGENTS.md` 成为工具中立的正文,`CLAUDE.md` 用 `@AGENTS.md` 引入 + Claude 专属附注 |
| P2 | **状态签失真 + 根目录拥挤**:已完结方案与现行手册同层并列、都标活跃;Agent 据过期方案判断现状(例:`oss-storage.md` 仍写「尚未部署」,生产早已迁 OSS) | 完结方案整体归档(`git mv` 到 `docs/archive/`,保留原名);状态行受控词表;`docs/README.md` 按「现行手册 / 活跃方案 / 耐久参考 / 归档」重排;加**守卫脚本**(索引覆盖 + 链接有效 + 状态词表 + L0 行数) |
| P3 | **耐久知识埋在方案里**:「RAG 重引入触发器」「多维评分不做的结论」「明确不做」清单散在各方案与记忆 | 新建 `docs/decisions.md`(ADR-lite,一行一决策:日期 / 决策 / 出处),归档前先把每篇方案的「拍板 / 明确不做」摘一行进去 |
| P4 | **记忆 vs 文件边界不清**:流程(闭环清单 / 检视协议 / 隔离栈)写在记忆里,其它 Agent 看不到;生产运维实录(含主机地址)22 KB 当记忆存;而仓内已有的事实又被记忆重复 | **事实与规则 → 仓内文档**;**可复用操作流程 → 技能**(项目技能入库、含机密的个人技能不入库);**记忆只留**:用户偏好、本机环境、仓外项目、私密事项。记忆 40 条 → 约 15 条保留、11 条缩短、14 条删除(§3.6 逐条) |

**拍板项**在 §5(D1–D10),每项附推荐;**执行清单**在 §6(分三个 PR + 一次本机记忆迁移)。

## 1. 依据:官方机制与业界实践

### 1.1 Claude Code 官方(code.claude.com/docs,2026-10-21 核实)

- **长度**:「target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence」;超长时启动与 `/status` 会告警。**官方文档没有「150k 字符上限」这个数**(本仓 2026-09-15 的记录应是当时告警阈值的记忆,不必再按它当目标)。硬上限是单文件 4 MiB。
- **放什么**:「Keep it to facts Claude should hold in every session… If an entry is a multi-step procedure or only matters for one part of the codebase, move it to a skill or a path-scoped rule instead」。
- **`.claude/rules/`**:目录下 `.md` 递归发现;无 `paths:` 的规则启动即载(与 CLAUDE.md 同级);带 `paths:` glob 的规则**只在 Read/Write/Edit(含单文件 cat/head)命中文件时加载**。
- **`@path` 导入**:相对路径相对于引用文件;最多四跳;代码块内不解析;**导入是启动即载**(不省 token,只是拆文件)。
- **子目录 `CLAUDE.md`**:不在启动时载,首次读写该子树文件时才载。
- **技能**:描述常驻、正文按触发加载;`SKILL.md` ≤ 500 行,细节放 `reference.md` 等支持文件;`disable-model-invocation: true` 可限手动 `/name` 触发;项目技能放 `.claude/skills/<name>/`。
- **自动记忆**:`MEMORY.md` 只载前 200 行 / 25 KB,主题文件按需读;定位是「用户偏好、纠正、代码推不出的项目上下文」,**凡 CLAUDE.md 已说的不写记忆**。
- **`AGENTS.md`**:v2.1.277+ 原生读取,但**默认只在没有任何 `CLAUDE.md` 时读**;两者并存时推荐 `CLAUDE.md` 里 `@AGENTS.md`。
- 工具:`/doctor prompt-audit` 可检查 CLAUDE.md / rules / skills 的陈旧与冲突。

### 1.2 Codex(OpenAI 官方 AGENTS.md 指南)

- 只读 `AGENTS.md`(以及 `AGENTS.override.md`),从仓根到 cwd 逐级拼接,**总量默认封顶 32 KiB**(`project_doc_max_bytes`);嵌套 `AGENTS.md` 只沿 cwd 路径加载,不会因为任务触到某目录就加载。本机 `~/.codex/config.toml` 未改该上限,`~/.codex/AGENTS.md` 为空。
- 结论:给 Codex 的常驻正文必须是 **`AGENTS.md` 本体且 ≤ 32 KiB**;它不读 `.claude/rules/` 与技能,所以**规则与流程的正文要放在普通 docs 里**,rules / skills 只做 Claude 侧的薄指针。

### 1.3 业界

- **AGENTS.md 开放标准**:2025-12 由 OpenAI 与 Anthropic 捐给 Linux Foundation(Agentic AI Foundation);纯 Markdown、无特殊语法。
- **ETH Zurich 2026-02 研究**(Gloaguen 等,4 个 agent × 138 个真实 issue):自动生成的 README 式上下文文件在 8 组设置里 5 组**降低**成功率并让推理成本 +20–23%;人写的小幅提升(约 +4 个百分点)但仍抬成本。结论与官方一致:**只写代码推不出来的东西**(命令、陷阱、约定),不要复述代码结构。
- **WordPress Gutenberg《Agents and Skills》**:agent 文件**只路由到权威文档,不复制内容**——复制的副本会静默分叉;「这条信息对人类贡献者也有用吗」是判断该进公共文档还是 agent 文件的试金石。
- **Diátaxis**(Canonical 采用):教程 / 操作 / 参考 / 解释四类分开;最常见的病是解释混进操作指南。对本仓:`deploy-*.md` 是操作、`contracts/` 是参考、`CLAUDE.md` 的 Key Design Decisions 是解释、方案文档是「决策记录 + 执行记录」——四者该分目录。
- **ADR 轻量实践**(MADR 系):决策一条一记、带日期与状态,**被取代时不改旧文、只标 superseded 并链到新决策**。本仓的方案文档已经是重型 ADR,缺的是一张可 grep 的总表。

### 1.4 对本仓的三层分发模型

| 层 | 载体 | 加载时机 | 放什么 | 入库 |
|---|---|---|---|---|
| L0 常驻 | `AGENTS.md`(正文,≤ 200 行 / ≤ 32 KiB)+ `CLAUDE.md`(`@AGENTS.md` + Claude 专属 ≤ 30 行)+ `.claude/rules/` 无 paths 的规则(尽量不用) | 每次会话 | 一分钟定向、开发命令、硬规则、协作流程摘要、文档地图 | 是 |
| L1 按路径 | `.claude/rules/*.md`(带 `paths:`) | 触到匹配文件时 | 该子域的 5–10 条硬规则 + 指向正文的链接 | 是 |
| L2 按需(事实) | `docs/architecture/`、`docs/ops/`、`docs/dev/`、`docs/contracts/`、`docs/sources/`、`docs/frontend/`、`docs/decisions.md` | Agent 按索引定位后读 | 设计说明、操作手册、契约、决策 | 是 |
| L2 按需(流程) | 项目技能 `.claude/skills/dorami-*`(薄,指向 `docs/dev/`) | 触发描述命中 | 本仓固定流程(issue 闭环、沙箱验收、文档同波) | 是 |
| 私有 | 个人技能 `~/.claude/skills/dorami-*`、`CLAUDE.local.md`、自动记忆 | 触发 / 每会话 | 生产主机与运维实录、本机网络、仓外项目、用户偏好与纠正 | **否** |

## 2. 现状盘点

### 2.1 `CLAUDE.md` 节级体量与去向

| 节 | 字符 | 性质 | 去向 |
|---|---|---|---|
| Development Commands(后端 / 前端) | 1.3k | 命令 | **留 L0**(压到 15 行) |
| Database migrations (Alembic) | 3.9k | 规则 + 解释 | 规则 3 行留 L0;全文 → `docs/dev/migrations.md`;`.claude/rules/migrations.md`(paths: `src/models/**`, `alembic/**`) |
| Production deploy | 2.8k | 操作摘要 | L0 留 3 行指针;正文已在 `docs/ops/deploy-*.md` |
| 协作流程 | 0.9k | 流程 | **留 L0**(这段写得正好);全文 → `docs/dev/workflow.md` |
| Versioning(含年表 20 行) | 12.4k | 机制 5 行 + 年表 | 机制留 L0;**年表整体 → `docs/version-history.md` 顶部「年表」节**(以后每波只改 version-history,不再改 L0) |
| 文档地图 | 0.9k | 索引 | 留 L0(更新) |
| Architecture Overview + Core Data Flow | 1.0k | 定向 | 留 L0 |
| Key Design Decisions | **57.6k** | 解释(分域) | **→ `docs/architecture/<域>.md`**(先原文搬迁,再整理);L0 只留「关键不变量」12 行 + 链接 |
| Project Structure | 15.4k | 带注释的目录树(会漂移) | → `docs/architecture/code-map.md`(注明快照日期);L0 留 20 行「找什么去哪」 |
| Key Endpoints | 13.6k | 参考(会漂移;权威是 `/docs` 与 `src/api/routers/`) | → `docs/reference/endpoints.md`(保留门控语义,注明权威来源);L0 留「端点族与门控」8 行 |
| Tests | 5.3k | 规则 + 清单 | 规则 4 行留 L0;全文 → `docs/dev/testing.md`;`.claude/rules/tests.md`(paths: `tests/**`) |
| Configuration + Environment Variables | 3.6k | 参考 | → 并入 `docs/ops/configuration.md`(它现在反过来说「完整语义见 CLAUDE.md」,方向倒置);L0 留 3 行 |

瘦身后 L0 估算 **180–220 行 / 20–28 KB**(满足 Codex 32 KiB 与官方 200 行的量级)。所有迁出内容**先原文搬迁、不改写**,搬迁 PR 不承担内容更新。

### 2.2 `docs/` 顶层:逐文件裁决

状态词表(见 §3.4):`进行中` / `排队中` / `已落地 vX.Y.Z` / `耐久参考` / `归档`。「归档」= `git mv` 到 `docs/archive/`(保留文件名,不改内容,只在文首加一行归档注)。

**现行手册 / 耐久参考(留在活跃区,按 D2 分目录)**

| 文件 | 现状 | 裁决 |
|---|---|---|
| configuration.md | 506 行,部署视角 | → `docs/ops/`;吸收 CLAUDE.md Configuration/Env 节,改为唯一配置参考 |
| deploy-docker.md / deploy-baremetal.md / release-process.md | 现行 | → `docs/ops/` |
| oss-storage.md / oss-local-fallback.md / storage-backups.md + 两个 `oss-*-policy.example.json` | `oss-storage` 状态行「开发中,尚未部署」**已过期**(#109 已合入、生产已迁) | → `docs/ops/`;修状态行;`oss-local-fallback.md` 补进索引 |
| taxonomy-v1-deployment.md / aliyun-dual-node-deployment.md / bailian-singapore-deployment.md / full-analysis-backfill.md | 现行 | → `docs/ops/` |
| reader-search-architecture.md | ◇ 快照 v3.36 | → `docs/architecture/search-and-qa.md`(与 CLAUDE.md 检索节合并) |
| contracts/*(4)、frontend/*(4)、sources/*(6 + candidates 13) | 现行 | 不动 |
| backlog.md / version-history.md / README.md | 现行 | 不动位置;形态见 §3.5 |

**活跃方案(留 `docs/plans/`,D2 若选 B1 则留顶层)**

| 文件 | 依据 |
|---|---|
| content-classification-plan.md | 3b / 3c 排队、3a 观察期 |
| node-watchdog-layer1-plan.md | 已拍板、PR-0 已做、PR-A/B/C 未做 |
| podcast-cost-and-scoring-calibration-plan.md | P1 黄金集未启动 |
| intelligent-butler-plan.md(**未跟踪**,见 D7) | #132 排队中 |
| paper-fulltext-plan.md | 盘点时 #41 未收口；2026-10-22 用户确认首期完成，现已移入 [归档](../archive/issue-41-paper-fulltext.md)，后续边界进入 backlog 展望 |

**已完结方案 → 归档**(对应波次已合入或已发版;核对自 `git log` / backlog「已完结」/ 版本史)

admin-root-admin-and-account-growth(v3.55)、admin-surface-refactor-plan(#76 v3.58)、admin-usability-audit(四波已收口,余项在 backlog)、analysis-brief-review-plan(v3.48)、auto-deploy-plan(#102 v3.60.1)、backend-stall-fix-plan(#194 v3.65.2)、background-translation-plan(#202 v3.66.0)、baremetal-rollback-plan(#126)、engage-sync-wave-plan(v3.18)、image-understanding-wave-plan(#69 v3.57)、interest-lens-wave-plan(v3.52)、issue-101-media-reuse-plan(**零入链、不在索引**,状态需核对)、issue-93-tag-search-plan(#169 已合入,不在索引)、news-coverage-reliability-plan(#127 已发版)、personal-brief-breaking-lane-plan(v3.50)、personal-brief-grid-and-sections(#74;网格部分已随 #184 退役)、personal-brief-interest-union(v3.54)、personal-brief-rebuild-entrypoints(v3.51.1)、personal-brief-title-localization(v3.52.1)、podcast-transcript-dedup(已实现 `services/podcast_transcript_dedup.py`;可改标「耐久参考」留 architecture)、podcast-wave-plan(78 KB,状态行仍「Proposed」,已被 specs/ 与 CLAUDE.md 播客节取代)、r1-translation-validation(验证记录)、rag-retirement-plan(已完成;§4 重引入触发器 → decisions.md)、social-translate-plan(#84 v3.66.0)、social-x-wave-plan(v3.12,状态行仍「进行中」,不在索引)、source-collections-wave-plan(v3.38)、unified-news-scoring-plan(v3.48)、user-custom-rss-wave-plan(v3.40)、visual-refine-plan(#184 v3.65;Windows 矩阵余项在 backlog)。

归档前**每篇摘一行**进 `docs/decisions.md`(拍板 / 明确不做 / 后续触发条件),这是归档不丢知识的关键一步。

### 2.3 其它目录

| 目录 / 文件 | 现状 | 裁决 |
|---|---|---|
| `docs-site/`(VitePress,15 篇,PR #162) | 用户手册,**不在任何索引**;GitHub Pages 待仓库管理员开(issue #89);无 CI 构建 | `README.md`「给开发者与 Agent」段与 `docs/README.md` 各加一行指针;CI 加 `vitepress build` 校验(可选,D8) |
| `specs/007-podcast-intelligence/`(9 篇,spec-kit 形态)+ `artifacts/issue-7/`(4 篇报告) | Codex 播客波的规格与证据;`tasks.md` 引用 4 篇从未产出的报告;只被 `podcast-wave-plan.md` 与 `docs/README.md` 一行引用 | → `docs/archive/podcast-intelligence/{specs,artifacts}/`(D3);`voice-stack-decision.md` 的供应商结论摘进 decisions.md |
| `docs/design/`(46 页 HTML + 3 个 README) | 无目录级索引(L1 里有一段散文);登录 6 页 + credentials 2 页 + entry-analysis + 两页**未跟踪**(admin-ia / dept-pies)零引用 | 新建 `docs/design/README.md`(表:样页 / 波次 / issue / 现行 or 历史);零引用的历史样页 → `docs/archive/design/`(D6);未跟踪两页问用户 |
| `docs/pr-assets/`(3 组截图) | PR 配图 | 不动;design/README 注明用途 |
| `.claude/skills/full-output-enforcement`、`redesign-existing-projects` | 2026-07-08 入库的**通用**技能(非本仓专属),描述每会话常驻 | 从仓库删除(D5);需要的话装到 `~/.claude/skills/` |
| `frontend/README.md` | Vite 模板原文 | 改为 5 行指针(`docs/frontend/conventions.md` + 命令) |
| `README.md`(根) | 面向用户,质量好 | 补 docs-site 指针;「36 个精选播客」→ 37(与 `podcast_catalog.md` 对齐) |
| `AGENTS.md` | 2 KB 指针 | 成为 L0 正文(D1) |
| 未跟踪:`docs/intelligent-butler-plan.md`、`scripts/netcheck.{py,md}`、`docs/.obsidian/`、`.playwright-cli/`、`docs/design/dorami-admin-ia-quiet.html`、`docs/design/dorami-dept-pies-quiet.html` | 启动钩子已提示 | D7:`.obsidian` 与 `.playwright-cli` 进 `.gitignore`;其余逐项问 |

### 2.4 失效引用(22 处)

反引号路径指向已不存在的文件(脚本 §7 检出;Markdown 链接层面 `docs/` 内无断链,`docs-site/` 的 `/features/...` 是站内路由,非断链):

- 归档文档(历史快照,**不修**,归档注里统一声明「路径为当时状态」):`archive/backend-architecture-review.md` → `src/storage/impl/vector_storage.py`;`archive/frontend-optimization-plan.md` → 4 处旧前端路径;`archive/horizon-vs-dorami.md` → `src/scrapers/base.py`。
- 活跃文档(**修**):`auto-deploy-plan.md` → `tests/test_entrypoint_check_config.py`;`frontend/conventions.md` → `src/theme.js`;`node-watchdog-layer1-plan.md` → `tests/test_watchdog_rules.py`、`tests/fixtures/watchdog_runs_2026-09.json`(未实现项,改标「待建」);`podcast-cost-and-scoring-calibration-plan.md` → `scripts/eval_podcast_score_golden.py`、`tests/fixtures/golden_podcast_score.json`、`docs/podcast-score-golden-candidates.json`(P1 未启动,改标「待建」);`rag-retirement-plan.md` → 4 处已删文件(归档时加注);`source-collections-wave-plan.md` → `docs/design/dorami-collections-quiet.html`(样页从未入库,加注);`specs/…/tasks.md` → 4 篇未产出报告(归档时加注)。

### 2.5 命名与形态不一致

- 方案文件名四种模式并存:`<slug>-plan.md` / `<slug>-wave-plan.md` / `issue-<N>-<slug>-plan.md` / 无后缀(`personal-brief-*.md`、`r1-translation-validation.md`)。issue 号是全仓主键(分支、PR、backlog、版本史都按它),但多数文件名不带。
- 状态行自由文本,无法机器判断;`docs/README.md` 的 ◉/◇/⊘ 靠人手维护,已失真。
- `version-history.md` 三种排序并存:顶部按 issue 倒序、中段「纪元叙事 + 补丁节点」、底部「待发布」顺序追加——查某波要靠全文搜。
- `CLAUDE.md` 年表与 `version-history.md` 每波都改,是 main→master 同步**必冲突**的两处(记忆 master-branch-policy 2026-09-22 实录)。

## 3. 目标形态

### 3.1 目录结构(推荐 B2,见 D2)

```
AGENTS.md                      L0 正文(工具中立,≤ 200 行 / ≤ 32 KiB)
CLAUDE.md                      @AGENTS.md + Claude 专属附注(记忆 / 技能 / rules / Write 转义坑)
.claude/rules/                 路径作用域规则(薄):frontend.md · migrations.md · deploy-scripts.md · tests.md
.claude/skills/                项目技能(薄,指向 docs/dev):dorami-issue-loop · dorami-sandbox-acceptance · dorami-wave-docs
docs/
  README.md                    L1 索引:按「现行手册 / 架构 / 开发 / 活跃方案 / 耐久参考 / 归档」分组,状态由守卫校验
  decisions.md                 ADR-lite 总表(日期 · 决策 · 状态 · 出处链接)
  backlog.md · version-history.md
  architecture/                解释型(从 CLAUDE.md Key Design Decisions 迁出,分域):
    overview.md                整体设计(回应 #89):两侧 / 数据流 / 角色轴 / 部署形态 / 不变量
    content-model.md · analysis-and-briefs.md · podcast.md · media-and-storage.md
    social-x.md · search-and-qa.md · accounts-and-access.md · jobs-and-scheduling.md
    code-map.md                带注释目录树(快照日期)
  reference/endpoints.md       端点族与门控(权威 = /docs 与 routers)
  ops/                         操作:configuration · deploy-docker · deploy-baremetal · release-process · oss-* · storage-backups
                               · taxonomy-v1-deployment · aliyun-dual-node · bailian-singapore · full-analysis-backfill · runbook.md(新:排查手法沉淀)
  dev/                         开发:workflow.md(协作流程全文)· testing.md · migrations.md · dependencies.md · sandbox-acceptance.md
  frontend/ · contracts/ · sources/     不动
  plans/                       活跃方案(进行中 / 排队中),新文件一律 issue-<N>-<slug>.md
  design/                      样页 + README.md 索引
  archive/                     已完结方案(原名不改)+ podcast-intelligence/{specs,artifacts} + design/
docs-site/                     用户手册(VitePress),索引里有指针
```

### 3.2 `AGENTS.md` 提纲(目标 ≤ 200 行)

1. 一分钟定向(项目 / 栈 / 两侧 / 角色轴 / 当前生产形态)— 15 行
2. 开发命令(后端 / 前端 / 测试 / 迁移 / 改依赖)— 20 行
3. 硬规则(只收「违反就出事故」的,每条一行 + 出处链接):模型改动配迁移;前端改动先读 conventions;PR 不改版本号;uv.lock 流程;主仓不离开 main;部署脚本禁 `| grep -q` 当条件;文件锁经 `services/file_lock`;cron 任务 `misfire_grace_time`;滚动窗测试用相对时间;读者文案不漏架构词;文档同波改 version-history 不改 L0 — 15 行
4. 协作流程(现有 §协作流程 原文)— 15 行
5. 架构速览:数据流图 + 关键不变量(双维身份、内容分类读取时推导、评分唯一尺子、FTS5 唯一检索、Archive Sync 权威、媒体原链不改写、播客不下载音频…每条一行 + 链接)— 30 行
6. 「找什么去哪」速查(替代 15k 的目录树)— 20 行
7. 文档地图(L1 / 高频直达 / docs-site / 决策表 / 待办)— 15 行

`CLAUDE.md` = `@AGENTS.md` + 「Claude 专属」:记忆目录说明(含 MEMORY.md 25 KB 上限)、项目技能清单、rules 目录、Write/Edit 对 `\uXXXX` 的解码坑。

### 3.3 `.claude/rules/`(每枚 ≤ 40 行,只放硬规则 + 链接;Codex 不读,正文仍在 docs)

| 规则 | paths | 内容来源 |
|---|---|---|
| frontend.md | `frontend/**` | conventions.md 的「改前必读」摘要 + token 单一事实来源 + 动效不写 OS 媒体查询 + 读者文案 + 管理面纪律 |
| migrations.md | `src/models/**`, `alembic/**`, `src/storage/**` | Alembic 节的规则部分(drift 测试、heads 口径、勿加 stamp 捷径、FK 策略) |
| deploy-scripts.md | `deploy*.sh`, `scripts/deploy-*`, `scripts/release.sh`, `docker/**`, `.github/workflows/**` | 禁 `| grep -q`、`${NAME}`、bash 3.2 坑、tag 即发布、部署协议常量 |
| tests.md | `tests/**` | 沙箱 / xdist / 滚动窗 / SSRF 打桩 / 不依赖 git 深度 / 测试命令不走管道 |

### 3.4 状态行与命名规范(写进 `docs/README.md` 头部,守卫校验)

- 每篇 `docs/**/*.md`(索引与归档除外)第一段必含 `> 状态:<词>`,词表:`进行中` · `排队中` · `已落地 vX.Y.Z` · `耐久参考(快照 YYYY-MM-DD)` · `归档`。
- 方案类新文件:`docs/plans/issue-<N>-<slug>.md`;落地后不改名,`git mv` 到 `docs/archive/`,文首加 `> 归档于 YYYY-MM-DD,vX.Y.Z;现状以 docs/architecture 与代码为准`。
- 守卫 `scripts/check_docs.py` + `tests/test_docs_index.py`:① `docs/**/*.md` 每篇被 L1 或所属子目录 README 收录;② 相对链接与反引号仓内路径存在(归档目录只警告);③ 状态词表;④ `AGENTS.md` ≤ 220 行且 ≤ 32 KiB、`CLAUDE.md` ≤ 60 行;⑤ `docs/plans/` 文件名合规。

### 3.5 `version-history.md` 与 `backlog.md`

- `version-history.md`:顶部放「年表」(从 CLAUDE.md 迁入,一波一行,按版本倒序);正文**统一按版本 / 合入时间倒序**,每波 H2 固定形如 `## vX.Y.Z · issue #N 标题(合入 YYYY-MM-DD)`;「待发布」改在发版时把版本号补进标题。规模再涨时按季度拆文件(`version-history/2026-Q4.md`)。
- `backlog.md`:加一条 `issue #94 文章全局搜索`(记忆里有、仓内没有);「已完结」区只留指针不留叙述(叙述归版本史)。
- 文档同波规则改为:**每波只改 version-history(年表一行 + 详细一段)、backlog、decisions**;`AGENTS.md` / `CLAUDE.md` 只在不变量或命令变化时改。这同时消掉 main→master 同步在 L0 上的固定冲突。

### 3.6 记忆逐条裁决(40 条主题文件)

原则:事实与规则进仓;可复用流程进技能;记忆只留偏好、本机、仓外、私密。「缩短」= 保留 2–5 行「拍板 / why / 指针」。

| 记忆 | 裁决 | 去向 |
|---|---|---|
| admin-ui-aesthetics | 缩短 | 规则正文 → `docs/frontend/conventions.md` §8 新增「管理面纪律」(列头即操作 / 零说明句 / rowact 小图标 / 高危入抽屉 / 像素级验收);记忆只留偏好三行 |
| ci-speed-analysis | 删除 | 已在 version-history(#150) |
| cloudinsight-integration | 保留 | 仓外内网项目 |
| codex-cli-local-run | 删除 | → 个人技能 `dorami-codex-review/reference.md` |
| codex-cross-review | 删除 | → 同上(GitHub bot 段只留一句历史) |
| codex-default-model | 保留 | 用户明示偏好(短) |
| deepseek-models-2026-09 | 删除 | → `docs/ops/configuration.md` `[llm]` 节「模型注意事项」 |
| discoverability-over-quietness | 保留(缩短) | 产品偏好;规则已在 conventions |
| dual-role-axes | 删除 | CLAUDE.md 已覆盖 |
| e2e-harness-notes | 删除 | → `docs/frontend/e2e.md` 补「--flows 与已知基线失败」 |
| frontend-refine-proposal | 删除 | 已上线;余项在 backlog;「worktree 软链 venv 勿跑 uv run」→ `docs/dev/workflow.md` |
| im-bot-project | 保留 | 仓外 |
| innersource-1024-activity | 保留 | 私密、不入库 |
| interest-term-unified | 删除 | → conventions §1 术语表 |
| intranet-nginx-proxy-temp | 删除 | 内网专属 → master 分支 `INTRANET_DELTA.md` / `deploy-baremetal.md` 排障节(由内网同步时登记) |
| isolated-acceptance-stack | 删除 | → `docs/dev/sandbox-acceptance.md` + 项目技能 `dorami-sandbox-acceptance` |
| issue-102-auto-deploy-plan | 删除 | 已收官,坑在 auto-deploy-plan §7 / release-process |
| issue-126-baremetal-rollback | 删除 | 已合入;内网实测缺陷核对已进 deploy-baremetal 后删 |
| issue-130-downstream-identity-hook | 删除 | 已合入,CLAUDE.md 有 |
| issue-94-global-search | 缩短 | backlog 补条目(样页路径 / 七条拍板项);记忆留 2 行 |
| issue-closure-loop | 缩短 | 全文 → `docs/dev/workflow.md` + 项目技能 `dorami-issue-loop`;记忆留「用户 2026-09-16 确认可自动执行」 |
| local-codex-review-protocol(19 KB) | 缩短 | 流程 → `docs/dev/workflow.md` 检视节;Herdr / codex exec 操作与 14 次实录精华 → 个人技能 `dorami-codex-review`;记忆留 5 行 |
| local-network-env | 保留 | 本机环境(Docker 代理等可挪 `CLAUDE.local.md`) |
| master-branch-policy | 保留 | 含私密例外(本地 master 不推) |
| merge-needs-user-acceptance | 缩短 | 流程 → workflow.md;记忆留用户原话 + 「难验收 PR 可直接合入」口径 |
| multidim-scoring-verdict | 删除 | → `docs/decisions.md` 一条 + unified-news-scoring-plan 归档注 |
| netcheck-tool | 视 D7 | 若 `scripts/netcheck.*` 入库则 `scripts/README.md` 收录后删;否则保留 |
| prod-remote-ops(22 KB,含主机地址) | 缩短 | 连接手法 + 核对清单 + 升级实录 → 个人技能 `dorami-prod-ops`(**不入库**);无机密的「排查手法」(日报漏报逐环节、cron missed、分析回填吞吐)→ `docs/ops/runbook.md`;记忆留「当前生产版本 + 授权范围」4 行 |
| prod-source-outages-2026-09 | 删除 | version-history / backlog #79 已有 |
| reader-facing-copy | 删除 | conventions §1 已有(核对后) |
| reader-redesign-exploration | 缩短 | 留 artifact 链接 3 行 |
| reader-usability-plan-doc | 删除 | 过时指针 |
| review-rounds-bounded | 缩短 | 规则 → workflow.md 检视节;记忆留 2 行 |
| rolling-window-tests | 删除 | → `docs/dev/testing.md` |
| samples-match-platform-reality | 保留 | 用户偏好 |
| samples-windows-and-sharing | 保留 | 用户偏好 |
| shendeng-pipeline | 缩短 | 「下游只消费 extensions.items」→ `docs/contracts/feed_delivery.md`「已知消费方」;记忆留 2 行 |
| watchdog-direction | 删除 | node-watchdog-layer1-plan + backlog 已有 |
| work-rhythm | 缩短 | 流程 → workflow.md;记忆留偏好 5 行(样页→拍板→目检;uv.lock;stash 条目禁动) |
| write-tool-unicode-escapes | 保留 | Claude 工具专属(或进 CLAUDE.md Claude 附注) |

合计:保留 11、缩短 11、删除 18。反方向(文件 → 记忆)只有一类:`CLAUDE.md` 里的事故实录与日期叙述(如「2026-09-14 生产实录:默认 1 s 宽限…」)属项目史,去 version-history,不进记忆。**没有发现应从文件挪进记忆的内容**——记忆的正确边界是「文件里不该出现的东西」(私密、本机、偏好),而不是「文件里重要的东西」。

### 3.7 技能三枚(项目,入库)+ 三枚(个人,不入库)

| 技能 | 范围 | 触发 | 正文来源 |
|---|---|---|---|
| `.claude/skills/dorami-issue-loop` | 项目 | 「开工 issue N / 按流程推进」 | `docs/dev/workflow.md` 的闭环清单(技能只给步骤与检查点,≤ 60 行) |
| `.claude/skills/dorami-sandbox-acceptance` | 项目 | 「起沙箱 / 隔离栈验收 / 目检」 | `docs/dev/sandbox-acceptance.md`(临时 ini / 播种约束 / Playwright 选择器) |
| `.claude/skills/dorami-wave-docs` | 项目 | 「文档同波 / 收口 / 归档方案」 | §3.5 规则 + decisions 摘行模板 + 守卫脚本 |
| `~/.claude/skills/dorami-codex-review` | 个人 | 「codex 检视 / 交叉检视」 | 记忆 local-codex-review-protocol + codex-cross-review + codex-cli-local-run(Herdr 操作、轮询脚本、模型参数) |
| `~/.claude/skills/dorami-prod-ops` | 个人 | 「生产 / 部署核对 / 升级」 | 记忆 prod-remote-ops(主机、Cookie 手法、脚本传输、核对清单、实录) |
| `~/.claude/skills/dorami-intranet` | 个人 | 「内网 / master 分支 / 1024」 | 记忆 master-branch-policy + intranet-nginx + innersource(私密部分) |

个人技能用 `disable-model-invocation: false` 但描述写清触发词;含主机地址的内容**只在个人技能与 `CLAUDE.local.md`**,仓内任何文件不出现(守卫可加一条:仓内不含生产 IP 字面)。

## 4. 对 issue #89「整体设计文档」的回应

`docs/architecture/overview.md` 即整体设计文档:两侧(采集归档 / 读者分发)、数据流、三种内容容器、分析与双评分、早报两条产品线、部署拓扑(单节点 all / 外网 authority + 内网 replica)、角色轴、关键不变量与「为什么不做」(链 decisions.md)。首版由 CLAUDE.md 现有内容拼成(不新写事实),docs-site 的「设计理念」页链过来即可;Pages 部署仍等管理员开 Settings → Pages。

## 5. 拍板项

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| D1 | L0 正文放哪 | A `CLAUDE.md` 正文 + `AGENTS.md` 指针(现状,Codex 要再读一次)/ **B `AGENTS.md` 正文 + `CLAUDE.md` = `@AGENTS.md` + Claude 附注** | **B**:两家工具都自动加载同一份;文档与记忆里「以 CLAUDE.md 为准」的措辞一次性改为「以 AGENTS.md 为准」 |
| D2 | 目录重组幅度 | B1 只归档完结方案、其余留顶层 / **B2 归档 + 新建 architecture/ ops/ dev/ reference/ plans/** | **B2**:`ls docs/` 即能按用途定位;一次 PR 做完,链接由脚本改写 |
| D3 | `specs/` 与 `artifacts/` | 留原地 / **迁 `docs/archive/podcast-intelligence/`** | 迁:仓根只留代码与入口文件 |
| D4 | 记忆 → 技能 | 按 §3.6 / §3.7 执行(本机操作,不入库部分由我在本机写) | 执行;删除前先把去向写进仓或技能,再删记忆 |
| D5 | 仓内两枚通用技能 | 删 / 留 | 删(非本仓专属;需要时装用户级) |
| D6 | 零引用历史样页(登录 6 + credentials 2 + entry-analysis) | 删(git 历史可找)/ **迁 `docs/archive/design/`** | 迁,design/README 登记 |
| D7 | 未跟踪文件 | `intelligent-butler-plan.md`(入库 → plans/)· `scripts/netcheck.*`(入库并收进 scripts/README,或仍本地)· `docs/.obsidian/` `.playwright-cli/`(gitignore)· `dorami-admin-ia-quiet.html`(#114 样页?入库或删)· `dorami-dept-pies-quiet.html`(来历?) | 逐项由用户定;gitignore 两项直接做 |
| D8 | docs-site | 只加指针 / 加 CI `vitepress build` | 加指针 + CI 校验(约 1 分钟,防断链) |
| D9 | decisions.md | 建 / 不建 | 建;归档 28 篇时顺手摘行,之后每波一行 |
| D10 | 守卫 | 建 `scripts/check_docs.py` + 测试 / 不建 | 建;没有守卫,状态签半年内会再次失真(这次就是例证) |

## 6. 执行清单

**PR-1(本分支,低风险,先合)**:本方案;`.gitignore` 补 `docs/.obsidian/`、`.playwright-cli/`;`docs/README.md` 补 4 篇漏收 + docs-site / specs / artifacts 指针;活跃文档 9 处失效引用加注;`oss-storage.md` 等过期状态行;`README.md` 播客数与 docs-site 指针;`frontend/README.md` 改指针;backlog 补 #94;守卫脚本 + 测试(先只跑「索引覆盖 + 断链」两项,词表与行数阈值等 PR-3 落地后开)。

**PR-2(结构迁移,机械)**:`git mv` 28 篇完结方案 → `archive/`(文首加归档注)+ `specs/`、`artifacts/` → `archive/podcast-intelligence/` + 零引用样页 → `archive/design/` + 现行手册 → `ops/` + 活跃方案 → `plans/`;脚本改写全仓相对链接与反引号路径(含 CLAUDE.md、backlog、version-history、记忆里的路径由我本机改);`docs/README.md`、`archive/README.md`、新建 `design/README.md`;`decisions.md` 首版(每篇归档方案一行 + RAG 触发器 + 多维评分结论 + 语音栈选型)。

**PR-3(L0 瘦身 + 分发)**:Key Design Decisions 原文拆到 `architecture/*.md`;Project Structure → `code-map.md`;Key Endpoints → `reference/endpoints.md`;Tests / Alembic / Configuration → `dev/` `ops/`;年表 → version-history 顶部;写 `AGENTS.md` 新正文、`CLAUDE.md` 附注、四枚 rules、三枚项目技能、`dev/workflow.md`(协作流程全文,合并记忆里的闭环清单与检视协议)、`architecture/overview.md`(#89);守卫开满。改「文档同波」规则并写进 workflow.md。

**本机(不入库)**:写三枚个人技能;按 §3.6 缩短 / 删除记忆、重写 `MEMORY.md`;`CLAUDE.local.md` 放本机环境(可选)。

每个 PR 走既有闭环(Codex 本地协商检视 → 用户目检 → 放行合入);PR-2 / PR-3 因改动面大,检视只需核「搬迁是否原文、链接是否全通」。

## 6.1 实施记录(2026-10-21)

- 三个 PR 合并为**一个 PR、六个提交**(方案 → 结构迁移 → L0 瘦身 → 守卫与 CI → 记忆沉淀 → 标题修补),便于一次检视与一次验收;提交边界仍按原三组划分。
- 结构迁移脚本 `git mv` 70 个文件;全仓 Markdown 相对链接按「文件旧位置解析 → 目标新位置 → 文件新位置重算」改写,代码 / 配置 / 工作流注释里的 `docs/...` 字串同步改写;`tests/fixtures/deploy_scripts_pre_issue_*` 冻结夹具回退不动。
- CLAUDE.md 拆分脚本按 `**标题**` 段切分、按标题路由到 14 篇 architecture 文件(`**标题**` 提升为 `##`,仓根相对链接重算);机械核对旧 KDD 49 段每段正文前 120 字符都能在新文件找到(两处因链接文字随路径改写而未命中,人工确认在)。
- 守卫脚本 5 项全绿;快测 69 通过;部署脚本两套测试 121 通过;docs-site 本地 `vitepress build` 通过。
- 记忆迁移在本机完成(保留 11 / 缩短 12 / 删除 19,原文备份 `memory-backup-20261021.tar.gz`),个人技能三枚;与 §3.6 的差别:`netcheck-tool` 保留(文件按用户意图仍不入库),`intranet-nginx-proxy-temp` / `issue-126` / `issue-130` 原文并入个人技能 `dorami-intranet` 的 reference 而非删除。
- 偏离方案处:`podcast-transcript-dedup.md` 归 `architecture/`(算法说明)而非归档;活跃方案在移动时同步改名为 `issue-<N>-<slug>`;AGENTS.md 实际 101 行 / 14 KB(远低于上限)。

## 7. 盘点脚本与风险

- 盘点脚本(本会话 scratchpad `linkgraph.py`:git 跟踪 + 未跟踪 md 的出入链、断链、反引号路径核验)将收进 `scripts/check_docs.py`。
- **master 同步冲突**:文件移动会让下一次 tag 触发的 main→master 合并在 master 有改动的文档上产生 rename 冲突;对策:PR-2 合入后发版前,在 master worktree 手工 `git merge` 一次并按「文件取 main、INTRANET_DELTA 登记路径变化」处理,这是一次性的。
- **外部链接**:issue / PR 评论里引用的 `docs/<old>.md` 路径会失效;GitHub 对已移动文件不重定向。可接受(历史评论),`archive/README.md` 保留旧名索引即可。
- **记忆删除不可逆**:按 §3.6 先写去向再删,删除前把原文打包到 scratchpad 留一份到会话结束。
- 本方案**不改任何产品代码**;守卫脚本与测试是新增文件。

## 8. 检视记录

实现方:Claude Code(Fable 5.1);检视方:Codex(`gpt-6-astra`,reasoning ultra,`codex exec` 只读沙箱,2026-10-21)。往返文件在 worktree `.review/`(不入库)。

**R1(任务书 8 个关注面)**:6 P2 + 2 P3,**无 P1**——「指定迁移节经标题、空白与路径归一化逐项比对,设计决策、端点、依赖、迁移、测试、配置、目录树及 28 条年表记录均保留」;另核了 102 个 docs-site 链接、CI 守卫 16 种组合、rules glob 与技能 frontmatter。八条全部接受(无分歧),一次修完(提交 cbde343):

| # | 发现 | 修法 |
|---|---|---|
| 1 P2 | 新写的依赖说明与技能把整个 `uv.lock` 排除出提交,与「改依赖必须提交锁」矛盾 | 三处改为「只还原本机镜像改写;改依赖 / 改版本的合法锁变更必须提交」 |
| 2 P2 | L0「合入前必须用户验收」与流程正文「难验收 PR 可直接合入」「纯文档直接 main」三处口径不一 | 例外条件同步进 `AGENTS.md`;`workflow.md` §10 标明是用户指定的例外及适用范围 |
| 3 P2 | 决策表把已被取代的「不做阅读价值第二轴」「空订阅仍为空」仍列为有效 | 行尾加「→ 已被 … 取代」;新增 issue #178 已落地三波的条目(已落地阶段及时入表,不等整篇归档) |
| 4 P2 | 索引守卫按文件名子串判断(删掉一行索引仍能通过) | 改解析 README 实际链接 + 反引号整名精确匹配;加「删索引行 / 同名不同目录 / 后缀碰撞」反向用例 |
| 5 P2 | 断链守卫漏 `[t](p "title")` 与引用式 `[id]: p` | 支持三种形式;加反向用例 |
| 6 P2 | 版本史引言仍说「CLAUDE.md 年表只加一行」 | 改为年表与详细记录都在本文、版本机制指向 AGENTS.md |
| 7 P3 | 端点参考里 `see Archive Sync above` 等指向已不在本文的节 | 端点与架构文档共 8 处跨文件引用改为链接 |
| 8 P3 | Podcast 归档索引漏 `specs/contracts/podcast-api.yaml`,阶段写 P0–P3 实为 P0–P4 | 补行、改阶段 |

**复检 R1**(对照八条):6 ✓、1 △(索引守卫裸文件名跨目录仍漏报)、1 ✗(链接扫描把方案 §8 的代码示例当链接误报)→ 一次修完(提交 d7f399d:裸文件名只认同目录、带目录反引号按完整路径、链接扫描跳过代码块与行内代码,+2 反向用例)。**复检 R2**:两条 ✓,「可合入」。三轮共约 1 小时,无乒乓;守卫用例自 2 条增至 7 条,其中 5 条是检视方的突变法沉淀。
