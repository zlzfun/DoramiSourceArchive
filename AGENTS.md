# AGENTS.md — DoramiSourceArchive 工作须知(L0)

> 面向所有在本仓工作的 Agent:Codex 直接读取本文;Claude Code 经 `CLAUDE.md` 的 `@AGENTS.md` 引入。
> 本文只放**每次会话都该知道的事**——定向、命令、硬规则、协作流程、架构不变量、找什么去哪、文档地图。
> 设计说明在 `docs/architecture/`,操作手册在 `docs/ops/`,开发流程全文在 `docs/dev/`,活跃方案在 `docs/plans/`。
> 体量守卫:≤ 220 行且 ≤ 32 KiB(`scripts/check_docs.py`;Codex 项目文档总量封顶 32 KiB)。**只在不变量或命令变化时改本文**,波次记录写 `docs/version-history.md`。

## 一分钟定向

- **项目**:哆啦美 · DoramiSourceArchive——自托管 AI 资讯阅读器 / 内容聚合 CMS。**采集归档侧**(fetcher → SQLite + FTS5 全文索引)+ **读者分发侧**(订阅制阅读器、个人早报、公共日报、令牌化 feed / MCP 交付、LLM 翻译与问答)。按登录角色分面:`admin` 是超级用户(管理台 + 阅读器),`user` 是只读读者;`[runtime] role` 第二轴只在拆分部署时生效(默认 `all`,可忽略)。
- **技术栈**:Python 3.12 + FastAPI + SQLModel + Alembic + APScheduler(`src/`,`uv` 管依赖);React + Vite + Tailwind v4(`frontend/`);SQLite(`data/cms_data.db`,FTS5 影子表);LLM 走任意 OpenAI 兼容端点(`[llm]`,未配置时采集与阅读照常)。
- **生产形态**:外网节点 Docker 双容器(`./deploy-docker.sh`,tag 即发布,GitHub Release → 流水线审批部署)+ 内网节点裸机 PM2(`./deploy.sh`,经 Archive Sync 从外网同步;内网适配只进 `master` 分支)。
- **核心数据流**:`Fetcher → DataPipeline → DatabaseStorage(SQLite)`;入库后分析 worker 评分 / 摘要 / 打标;早报、日报、feed、问答都消费归档与分析结果。检索只有 FTS5(向量 RAG 已退役)。

## 开发命令

```bash
uv sync                                  # 依赖(dev 全量:uv sync --extra crawl4ai);改依赖的完整流程见 docs/dev/dependencies.md
python src/main.py                       # 后端 http://127.0.0.1:8088(热重载;启动先 ensure_migrated + taxonomy reconcile);API 文档 /docs
cd frontend && npm install && npm run dev   # 前端 5173,/api 代理到后端;npm run lint / npm test / npm run build
.venv/bin/python -m pytest tests/         # 全量(本地并行 -n 4 --dist worksteal,CI 即此形态);单文件直接给路径
.venv/bin/alembic upgrade heads           # 升级到全部 head;revision --autogenerate -m "desc" 生成迁移;check 看漂移
python3 scripts/check_docs.py             # 文档守卫(索引覆盖 / 断链 / 状态词表 / L0 体量)
```

## 硬规则(违反即事故;链接给完整语义)

1. **模型改动必配 Alembic 迁移**:`create_all()`(= metadata)必须等于 `alembic upgrade heads`(`tests/test_migrations.py` drift 测试强制);口径一律 `heads`,收养路径勿加「stamp head」捷径;Taxonomy 启动姿态 `[taxonomy] deployment` 必须显式 `authority` / `replica` / `manual` → `docs/dev/migrations.md`、`docs/ops/taxonomy-v1-deployment.md`。
2. **改前端先读 `docs/frontend/conventions.md`**:token / 角色类单一事实来源 = `frontend/src/index.css`,不手写 `text-[Npx]` / `rounded-[Npx]` / hex;新增动效不写 OS 媒体查询(应用内开关 `<html data-motion>`);读者面文案不漏内部架构词(归档 / 采集 / 分发 / 层);遮罩关闭只用 `<Modal closeOnOverlay>`;口径统一叫「兴趣」不叫「关注」。
3. **版本只在发版时改**:`src/version.py` 单一事实来源,开发版 `X.Y.Z-alpha`,**PR 不 bump**;`scripts/release.sh X.Y.Z` 发版打 annotated tag,两条部署脚本只认 tag(`--here` 显式例外)→ `docs/ops/release-process.md`。
4. **`uv.lock` 入库但本机镜像改写永不提交**:改依赖走 `env -u UV_INDEX_URL -u UV_DEFAULT_INDEX uv lock` 并重导出 `docker/requirements*.txt`(守卫 `tests/test_uv_lock.py` / `tests/test_docker_requirements.py`);提交一律显式列文件,永不 `git commit -a` → `docs/dev/dependencies.md`。
5. **主仓不离开 `main`**:一波一分支 `feat/issue-<N>-<slug>` 自最新 `origin/main` 拉出,在 worktree 里实现;内网适配(含看似通用的 src 改动)只进 `master`,master 永不合回 main。
6. **部署脚本里不写 `| grep -q` 当条件**(`pipefail` 下生产者吃 SIGPIPE、条件翻转,issue #150 已修六处);bash 3.2 下中文文案里变量一律 `${NAME}`;`$( case … )` 先算变量。
7. **文件锁一律经 `services/file_lock`**(POSIX `flock` / Windows `msvcrt`),不裸 `import fcntl`;Windows 上先关句柄再 `os.replace` / `unlink`。
8. **cron 类任务统一 `misfire_grace_time=300` + `coalesce`**(`add_cron_job`):2026-09-14 默认 1 s 宽限撞上一次事件循环阻塞,日报与四个采集任务整天缺席。
9. **测试**:造数据用相对时间(滚动窗 + 固定日期 = 时间炸弹);涉及 DNS 公网性判定的用例一律打桩(开发机 fake-ip 代理会掩盖);测试命令 `cmd > log 2>&1; rc=$?` 不走管道;新 TestClient 测试显式建 tmp sink → `docs/dev/testing.md`。
10. **改「系统记录」的语义前先 grep 消费方**:日报记录的分析行被个人早报与 readiness 消费;给 Archive Sync 分析流加字段时 `archive_sync_v2.ANALYSIS_PAYLOAD_GENERATION` +1;`ScoredItem.to_reduce_dict` 字段被内网神灯流水线消费(只读 `extensions.items`)。
11. **文档同波**:每波改 `docs/version-history.md`(年表加一行 + 详细一段)、`docs/backlog.md`(有意不做的遗留)、`docs/decisions.md`(拍板 / 不做 / 触发各一行);方案落地后 `git mv` 到 `docs/archive/` 并加归档注;新方案文件名 `docs/plans/issue-<N>-<slug>.md`。跑 `scripts/check_docs.py`。

## 协作流程(分支 / 检视 / 合入;全文与「为什么」在 `docs/dev/workflow.md`)

- **一波一分支**:`feat/issue-<n>-<slug>` 自最新 `origin/main` 拉出,在 worktree 里实现;主仓库不停在非 main 分支。
- **检视是合入门禁,走本地协商式交叉检视**:PR 开出后不再在 GitHub `@codex`,默认由不同于实现方的模型检视:Codex 实现交给 Claude Code,Claude Code 实现交给 Codex。Herdr 环境可用时经 Herdr 调用,否则直接使用本机 CLI;在方案文档记录实际实现方、检视方与模型。
  收到意见先逐条表态(接受 / 分歧 + 论据),分歧点与检视方讨论到一致——**一致之前不改代码**;修复成批做完再请复检,
  复检只对照修复清单不重新开放全面检视。轮次由执行者控制,原则是两人合作检视、协商,避免「乒乓球」式来回传球与发散;
  再有新面先判严重性,非严重记观察期。分歧与结论写进方案文档的检视节。
- **合入前必须用户本地端到端验收 + 目检放行**,codex 通过也不例外;拍板后 squash 合入 main。例外(2026-09-19 用户口径):**没有可目检界面**的纯后端机制修复与纯文档 PR,在检视通过 + 全量套件绿 + CI 绿后可由执行者直接 squash 合入;纯文档 / 截图改动另有用户指定的轻量路径(先建 issue、直接提交 main、`refs #N`),见 `docs/dev/workflow.md` §10。
- **开发版使用 alpha(2026-09-24 拍板)**:从最新正式 tag 开始新一波开发时,立即把
  `src/version.py` / `pyproject.toml` / `uv.lock` 根包行同步改为下一版本的 `X.Y.Z-alpha`；
  同一开发波内后续特性沿用这个 alpha 版本,不为每个 PR 另抢版本号、不打 alpha tag。
  合入 main 的门禁 = PR + CI + 检视,正式发布 = 发版人在 main 上跑 `scripts/release.sh X.Y.Z`
  去掉 `-alpha` 并打 annotated tag `vX.Y.Z`,
  两条部署脚本按 tag 部署(`--here` 显式部署工作树)。波次叙述仍可写「vX.Y 某某波」,号以实际发版为准。见 `docs/ops/release-process.md`。

## 架构速览与不变量(全文在 `docs/architecture/`)

| 域 | 不变量(一句话) | 文档 |
|---|---|---|
| 整体 | 两侧一库;角色轴 admin / user;生产 = 外网 authority + 内网 replica | [`overview.md`](docs/architecture/overview.md) |
| 内容模型 | 每条内容带 `content_type`(数据形状)+ `source_id`(渠道);内容类型 / 事件类型**读取时推导不落库**;`content_shape` 三容器 article / social / podcast 互斥;子类字段进 `extensions_json` | [`content-model.md`](docs/architecture/content-model.md) |
| 抓取器 | `FetcherRegistry` 自动扫描 `src/fetchers/impl/`;加源 = 硬化 preset fetcher(`generic_*` 模板前端隐藏,`is_template`);每次运行写 `FetchRunRecord` + `SourceStateRecord`;新源进观察期 `incubating` | [`fetchers-and-sources.md`](docs/architecture/fetchers-and-sources.md)、[`docs/sources/`](docs/sources/) |
| 分析与评分 | 文章级只有一把尺子「新闻价值」(`quality_score`,`llm/article_analysis_prompt.py`)+ 第二维「内容价值」同一次调用产出;日报复用不写回;播客提示词逐字不动;Taxonomy 目录仓库受控、永不自动发布 | [`analysis-and-briefs.md`](docs/architecture/analysis-and-briefs.md) |
| 早报 / 日报 | 个人早报冻结订阅范围 + 兴趣修订,版面快照不可变,重大事件 / 深度阅读 / 播客精选是额外分区;公共日报是独立产品,三层去重 + 确定性渲染 | 同上 |
| 播客 | 只解析 enclosure 元数据**不下载音频**;原音频无公网回源路由;仅四类供应商下载失败才一次性 OSS 中转;简介初评只作付费 ASR 门 | [`podcast.md`](docs/architecture/podcast.md) |
| 媒体 | 正文原链**永不改写**,显示层经 `/api/media/proxy`;按内容哈希去重;只随文预取不全量回填;OSS / 备份默认关 | [`media-and-storage.md`](docs/architecture/media-and-storage.md) |
| 检索与问答 | FTS5 是唯一检索基础设施;问答 = LLM 计划检索 + FTS5,降级链自持;`[n]` 引用与 sources 同源同序 | [`search-and-qa.md`](docs/architecture/search-and-qa.md) |
| 账号与权限 | 账号在 `users` 表,空库首启 `admin/admin`;根管理员是平权之上唯一例外;末位活跃管理员不可降级;管理写操作全入审计;外部凭据经 `services/credentials.py`(只写不回显) | [`accounts-and-access.md`](docs/architecture/accounts-and-access.md) |
| 读者交付 | 订阅 = 阅读范围 + feed / MCP 交付范围;`dsub_` / `dfeed_` 只存 hash,分享 `dshr_` 明文(有意差异);隐藏源全量排除于交付 | [`reader-distribution.md`](docs/architecture/reader-distribution.md)、[`docs/contracts/`](docs/contracts/) |
| LLM 与读者 AI | `chat_completion` 永不打日志 api_key;三档模型 main / aux / vision;翻译 / 问答受「全局总闸 ∧ 账户开关 ∧ LLM 已配置」门控,日额度软上限 | [`llm-and-ai.md`](docs/architecture/llm-and-ai.md) |
| 社交(X) | 一账号一 `source_id`;按**返回资源**计费的配额守卫;不展示互动数字 | [`social-x.md`](docs/architecture/social-x.md) |
| 调度与任务 | `CollectionJobRecord` 是唯一采集调度实体;`load_tasks_to_scheduler()` 只差量协调自己的命名空间;长任务 = 持久 `JobRecord` + 轮询 | [`jobs-and-scheduling.md`](docs/architecture/jobs-and-scheduling.md) |
| Archive Sync | 外网 authority 唯一写者,内网 replica 只读公共数据(`authority_id` 围栏);六流独立 checkpoint | [`archive-sync.md`](docs/architecture/archive-sync.md)、[契约](docs/contracts/archive_sync.md) |
| 运维面 | 管理面纪律:列头即操作、零说明句、高危入抽屉;三类计量(AI 用量 / 阅读 / 登录)落聚合表 | [`admin-ops.md`](docs/architecture/admin-ops.md) |

## 找什么去哪

| 要找 | 去 |
|---|---|
| 后端入口、鉴权中间件、前缀门控表、APScheduler 初始化 | `src/api/app.py`;按域路由 `src/api/routers/` |
| ORM 表 / 迁移 | `src/models/db.py` / `alembic/versions/`;FTS5 `src/storage/fts.py` |
| 抓取器 | `src/fetchers/impl/`(注册表 `registry.py`;网页后端 `src/fetchers/web_content/`) |
| 业务服务 | `src/services/`(`daily_brief` / `article_analysis` / `personal_digest*` / `reader_search` / `reader_ai` / `media_store` / `credentials` / `podcast_*` …) |
| LLM 客户端与提示词 | `src/llm/`(`client.py`;评分尺子 `article_analysis_prompt.py`;其余 `prompts.py`) |
| 前端 | 根 `frontend/src/App.jsx`;API 单点 `api.js`;token `index.css`;阅读器 `components/ReaderTab.jsx` + `hooks/useReaderState.js`;移动壳 `components/mobile/`;管理面 `AdminOpsTab.jsx` + `components/admin/` |
| 测试 | `tests/test_*.py`(沙箱 `tests/conftest.py`);前端 `frontend/test/*.test.mjs`;E2E `scripts/check_mobile_reader_e2e.py` |
| 部署与 CI | `deploy-docker.sh` / `deploy.sh` / `scripts/deploy-*.sh` / `docker/` / `.github/workflows/` |
| 运维与治理脚本 | `scripts/`(索引 `scripts/README.md`) |
| 端点清单与门控语义 | 运行中的 `/docs`;注记版 [`docs/reference/endpoints.md`](docs/reference/endpoints.md) |
| 带注释的目录树 | [`docs/architecture/code-map.md`](docs/architecture/code-map.md)(快照,以 `ls` 为准) |
| 用户手册 | `docs-site/`(VitePress,`npm run dev`) |

## 文档地图

- **L1 全量索引** [`docs/README.md`](docs/README.md):按「架构 / 开发 / 运维 / 契约 / 前端 / 源 / 活跃方案 / 归档」分组,每篇一行摘要。
- **决策总表** [`docs/decisions.md`](docs/decisions.md):一行一决策(拍板 / 明确不做 / 再评估触发),问「为什么不做 X」先查这里。
- **待办总账** [`docs/backlog.md`](docs/backlog.md) · **波次史** [`docs/version-history.md`](docs/version-history.md)(年表 + 逐波详细记录)。
- **开发流程全文** [`docs/dev/workflow.md`](docs/dev/workflow.md) · 测试 [`docs/dev/testing.md`](docs/dev/testing.md) · 迁移 [`docs/dev/migrations.md`](docs/dev/migrations.md) · 依赖 [`docs/dev/dependencies.md`](docs/dev/dependencies.md) · 沙箱验收 [`docs/dev/sandbox-acceptance.md`](docs/dev/sandbox-acceptance.md)。
- **部署与运维** [`docs/ops/README.md`](docs/ops/README.md)(Docker / 裸机 / 发布 / 配置 / OSS / 备份 / 排查手法)。
- **前端纪律** [`docs/frontend/conventions.md`](docs/frontend/conventions.md) · **对外契约** [`docs/contracts/`](docs/contracts/) · **源策展** [`docs/sources/curation_policy.md`](docs/sources/curation_policy.md)(新源必读 `node_audit_playbook.md`)。
- **活跃方案** `docs/plans/` · **归档** [`docs/archive/README.md`](docs/archive/README.md)(查决策来龙去脉,勿据其判断现状)· **设计样页** [`docs/design/README.md`](docs/design/README.md)。
- 根 [`README.md`](README.md) 面向用户;`docs-site/` 是读者使用手册。
