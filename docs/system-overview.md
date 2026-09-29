# 系统总览

> 面向人的整体设计导览，只讲稳定结构：产品定位、领域模型、核心数据流、模块边界与关键取舍。
> 事实以代码为准；字段、端点、参数与波次细节不在本文，按各节末尾链接下钻。
> Agent 用的完整架构简报是 [`CLAUDE.md`](../CLAUDE.md)，全量文档索引是 [`docs/README.md`](./README.md)。

## 结论先行

- **它是什么**：哆啦美是一套 AI 资讯聚合系统。一侧把上百个外部源（官博、媒体、RSS、GitHub、X、播客）采集进一个 SQLite 归档；另一侧把归档按人分发——订阅制阅读器、个人早报、公共日报、令牌化的 feed 与 MCP 接口。
- **谁在用**：两种登录角色。`admin` 是超级用户，管采集、治理与运维，同时也能当读者；`user` 只是读者，唯一的界面是阅读器。
- **数据怎么走**：抓取器 → 入库（同时写 FTS5 全文索引）→ 入库后排队做 AI 分析（新闻价值分 + 受治理的标签）→ 阅读器 / 早报 / 日报 / 榜单 / feed / MCP 都读同一份归档与同一份分析结果。
- **最重要的三条取舍**：归档是可靠性边界，AI 分析永远在入库之后、失败不影响归档；检索只用 SQLite 自带的 FTS5，不维护向量库；「源」只是一个 `source_id` 字符串身份，订阅、可见性、交付范围都挂在它上面。
- **改动该放哪**：见第 6 节的判断表。

## 1. 产品定位与角色分面

系统分两层协作：**采集 / 归档侧**（抓取、入库、分析、治理）和**读者 / 分发侧**（订阅、阅读、检索问答、交付）。两层之间唯一的接口是数据库里的归档记录——读者侧从不触发抓取，只读已经归档的内容。

主轴是**登录账号角色**（`users.role`）：

| 角色 | 能用的面 | 前端形态 |
|---|---|---|
| `admin` | 采集面（节点、采集任务、运行史、源配置、日报、LLM 配置）+ 运维管理 + 全部读者面 | 左侧应用导轨的管理台；轨底可切进同一个阅读器（「管理员双界面」） |
| `user` | 仅读者面（订阅、阅读、早报、问答、个人 feed / MCP 令牌） | 整页阅读器，其余都收在设置柜 |

门控在后端一处裁决：`src/api/app.py` 的 `COLLECTOR_API_PREFIXES` / `READER_API_PREFIXES` 按路径前缀判定，`/api/admin`、`/api/accounts` 另强制 admin；其中账户明细再收窄到「根管理员」。前端只镜像后端透出的能力位（`/api/runtime`），不自己判权。

还有一根**可选的部署轴** `[runtime] role`（`all` / `collector` / `reader`，默认 `all`），只在把采集和分发拆到两台机器时才有意义，与账号角色取交集。现行双节点生产两端都是 `all`，分工靠下文的「归档同步权威」而不是这根轴。

下钻：[CLAUDE.md · Key Design Decisions](../CLAUDE.md#key-design-decisions) 中 *Access control*、*Accounts are database-managed*、*Admin dual-surface* 三段；根管理员见 [admin-root-admin-and-account-growth.md](./admin-root-admin-and-account-growth.md)。

## 2. 领域模型

所有 ORM 表在 [`src/models/db.py`](../src/models/db.py)（约 58 张）。按职责归成六组，每组只列承重实体：

**源（Source）——没有一张「源表」。** 源的身份是字符串 `source_id`，来自三处的并集：
- 代码里的抓取器类（`src/fetchers/impl/`，注册表启动时自动扫描），即「内置源」；
- `source_configs`（`SourceConfigRecord`）：配置驱动的源，管理员加的 RSS / 网页 / X 账号，以及读者自助添加的私有 RSS（`owner_username` 非空、`source_id` 以 `user_rss_` 开头）；
- 归档里出现过的 `source_id`。

每个源有一种**内容形态**（`article` / `bulletin` / `social` / `podcast`，`src/api/sources.py`），决定它进阅读器的哪个容器。源的健康与游标在 `source_states`，每次抓取一行 `fetch_runs`。采集调度的唯一实体是**采集任务** `collection_jobs`（一组节点 + 一个 cron），运行记录 `collection_job_runs` 聚合它派生的 `fetch_runs`。

**条目（Article）——一切内容都是 `articles` 的一行。** 文章、动态、推文、播客单集、公共日报都在同一张表，靠两个正交维度区分：`content_type`（数据形状）与 `source_id`（渠道）。形态特有字段序列化进 `extensions_json`，不为每种内容建表。FTS5 虚拟表 `articles_fts` 由触发器与 `articles` 同步。

**分析与标签（Analysis / Taxonomy）。** `article_analyses` 是每篇文章当前的 AI 结果（新闻价值分、摘要、体裁），`article_tag_assignments` 把它挂到受治理的规范标签 `cms_tags`；标签有版本（`taxonomy_versions`）、别名、候选与证据、重打标签任务。标签目录是全局的、版本化的，产品词汇不会被自动发布。

**读者状态（Reader）。** 订阅 `reader_subscriptions`（一键订阅 = 每用户每源一行）、个人聚合令牌 `reader_feed_tokens`、收藏、已读状态与未读水位、兴趣 `user_interest_tags`、公开分享链接 `article_shares`。「我订阅」= 该用户所有有效订阅的 `source_id` 并集，它同时是阅读器的范围和 feed / MCP 的交付范围。

**产出物（Outputs）。**
- 个人早报：`personal_digest_editions` + `personal_digest_items`，每期冻结订阅范围与兴趣版本，条目是不可变快照；
- 公共日报：不单独建表，写成 `articles` 里 `source_id=dorami_daily_brief` 的一条记录，候选台账在 `daily_brief_candidates`；
- 全站榜单：`ranking_snapshots` 及其条目，每天由已有分析确定性生成，不调 LLM；
- 播客加工品：`podcast_processings` 状态机（抓取 → 准入 → ASR → 翻译 → 分析 → 精简稿 → TTS → 质检 → 发布）及其文本 / 音频产物、预算与成本账；
- 媒体：`media_assets`（外链图片的本地缓存，按内容哈希去重）、`image_insights`（配图识别结果，按图片字节哈希缓存）、`object_blobs`（OSS 位置索引）。

**运维与平台。** `users`、`admin_audit_logs`、计量（`ai_usage` / `reader_reads` / `login_events`）、`jobs`（持久化后台任务）、反馈与公告、`app_settings`（运行时 KV 配置——多数开关和阈值在这里，不在 ini）、归档同步的修订时钟与实体状态。

## 3. 核心数据流

```
[采集 / 归档侧]
抓取器（采集任务 cron 或手动触发）
  └─▶ DataPipeline（去重）
        └─▶ DatabaseStorage ─▶ articles ⇄ articles_fts（FTS5，触发器同步）
              ├─▶ 新条目图片预取 ─▶ 媒体库
              └─▶ 分析队列 ─▶ 文章分析 worker（每分钟）─▶ article_analyses（分数 / 摘要 / 规范标签）

[读者 / 分发侧] 都读 articles + article_analyses
  ├─▶ 阅读器（订阅 / 兴趣 / 收藏 / 发现）
  ├─▶ 检索问答（LLM 规划 → FTS5 召回 → 选篇 → 作答）
  ├─▶ 个人早报（08:30 冻结范围编排）
  ├─▶ 公共日报（复用分析分 → 去重 → 编辑 → 确定性渲染，写回 articles）
  ├─▶ 全站榜单（07:00 快照）
  ├─▶ feed（/api/feed，令牌版 /api/public/feed）与 MCP（/mcp）
  └─▶ 归档同步导出 ─▶ 内网接收节点（见下文）
```

要点：
1. **入库与分析解耦。** `DatabaseStorage` 写完文章后只在分析总闸打开时往队列里放一条；分析由 `services/article_analysis.py` 的租约 worker 异步处理，可重启续跑。分析缺失就是「没有」，读者面不显示分数，不当零分。
2. **一把尺子。** 新闻价值分只在文章分析里算一次（`src/llm/article_analysis_prompt.py`）；早报、日报、榜单、阅读器都读这个分，日报对尚未分析的候选就地用同一个函数补评但不写回。
3. **检索只有 FTS5。** 阅读器搜索、问答、MCP 检索都落到 `src/storage/fts.py`；问答在它前面加一层 LLM 规划与选篇。
4. **调度在进程内。** APScheduler 与 API 同进程，采集任务、分析、早报、日报、榜单、播客 worker、留存清理、远程同步各自注册；长任务提交后返回 `job_id`，状态持久化在 `jobs` 表。
5. **播客是一条旁路。** 单集照常作为条目入库（只取 RSS 元数据，不下载音频）；达到门槛的单集再进 `podcast_processings` 状态机做转写、分析与中文精简音频。

**双节点归档同步的位置。** 生产是两台都跑 `role=all` 的节点：外网节点采集并分析公共源（**权威方**），内网节点拉取后对内网读者提供服务（**接收方**）。接收方按固定顺序拉取多条独立检查点的流（源 → 标签目录 → 条目 → 分析 → 媒体 → 源状态 → 播客文本 → 播客音频），删除以墓碑传播。导入的记录带权威 ID（`articles.analysis_authority_id`、`source_configs.collection_authority_id`），接收方的本地采集与分析入口据此拒绝改写；内网自己加的私有 RSS 仍在本地采集分析，不回传。标签目录由外网独占，内网只读。

下钻：[CLAUDE.md · Core Data Flow](../CLAUDE.md#core-data-flow)；归档同步协议 [contracts/archive_sync.md](./contracts/archive_sync.md)；评分 [unified-news-scoring-plan.md](./unified-news-scoring-plan.md)；检索 [reader-search-architecture.md](./reader-search-architecture.md)；个人早报 [personal-brief-interest-union.md](./personal-brief-interest-union.md)；播客 [podcast-wave-plan.md](./podcast-wave-plan.md)；双节点上线 [aliyun-dual-node-deployment.md](./aliyun-dual-node-deployment.md)。

## 4. 模块边界

后端（`src/`，Python FastAPI + SQLModel）：

| 目录 | 职责 | 边界 |
|---|---|---|
| `fetchers/` | 抓取器基类、注册表、`impl/` 内置源、`web_content/` 可选浏览器正文后端 | 只产出内容对象，不碰数据库 |
| `pipeline/` | 驱动抓取器、去重、把结果交给存储 | 不含业务规则 |
| `storage/` | SQLite 存储、FTS5 表与查询、Alembic 迁移入口、同步修订时钟 | 表结构演进只经 Alembic |
| `models/` | ORM 表（`db.py`）与内容数据类（`content.py`） | 表的单一事实来源 |
| `services/` | 全部业务逻辑：分析、标签、早报、日报、榜单、订阅、检索问答、媒体、播客、同步、账户、计量 | 路由层只调它；规则集中在这里 |
| `api/` | `app.py`（鉴权中间件、前缀门控、调度器、生命周期）+ `routers/`（按域拆分的端点） | 端点保持薄 |
| `llm/` | OpenAI 兼容客户端与提示词 | 不记录 api_key；计量经回调，不阻断主流程 |
| `mcp_server.py` | MCP 服务，挂在 `/mcp` | 与检索端点复用同一实现 |

前端（`frontend/src/`，React + Vite + Tailwind v4）：`api.js` 是唯一的后端调用层；`main.jsx` 先把公开分享页分流出去，其余进 `App.jsx`（登录门 + 按能力位过滤的页签）；读者面是 `ReaderTab` / `ReaderWorkspace` 及早报、发现、榜单、兴趣页，移动端是 `components/mobile/` 独立外壳、与桌面共用 `hooks/useReaderState`；管理面是 `AdminOpsTab` 与 `components/admin/`。设计令牌与角色类只在 `index.css`，改前端先读 [frontend/conventions.md](./frontend/conventions.md)。

启动顺序（`src/main.py`、`docker/entrypoint.py` 同一路径）：`ensure_migrated` 把库升到迁移头 → 按 `[taxonomy] deployment` 对齐标签目录 → 起 uvicorn 与调度器。

下钻：[CLAUDE.md · Project Structure](../CLAUDE.md#project-structure)、[Key Endpoints](../CLAUDE.md#key-endpoints)、[Database migrations](../CLAUDE.md#database-migrations-alembic)。

## 5. 关键设计取舍

| 取舍 | 为什么 | 代价 / 边界 |
|---|---|---|
| 单个 SQLite 文件 + FTS5，不上向量库 | 生产硬件小；FTS5 由触发器保证与正文零漂移，无需对账；向量 RAG 曾实现但生产从未跑起来，v3.31 退役 | 模糊语义查询靠 LLM 改写关键词弥补；重新引入的条件见 [rag-retirement-plan.md §4](./rag-retirement-plan.md) |
| 所有内容一张 `articles` 表 + `extensions_json` | 新增内容类型不改表；阅读、检索、交付、同步一套代码 | 形态差异在序列化层和前端容器里处理 |
| `source_id` 字符串身份，而非源实体外键 | 内置源是代码、配置源是数据、历史源只剩归档，三者统一成一个键；订阅、可见性、交付都挂在它上面 | 源的元数据分散在注册表与 `source_configs`，取名走 `services/source_naming.py` 单点 |
| 新源优先写成硬化的内置抓取器 | 代码即策展记录，可测试可审计；通用参数化抓取器只作后端底座与模板，前端入口已关 | 加源要发版；读者私有 RSS 是例外，走 `source_configs` |
| 归档先于分析，分析异步、可缺失 | 采集可靠性不受 LLM 可用性影响；分析可以重跑、回填、换评分版本 | 读者面必须诚实处理「未分析」 |
| 一把新闻价值尺子，多处复用 | 早报、日报、榜单口径一致；日报在分析关闭时仍能出报 | 改评分提示词前后要跑黄金集（`scripts/eval_news_value_golden.py`） |
| 双节点靠数据权威分工，不靠运行角色 | 两端都能完整运行；接收方凭权威 ID 围栏拒绝改写导入数据 | 必须先部署外网权威方，再部署内网接收方 |
| 运行时开关放 KV（`app_settings`），ini 只放部署级配置 | 管理员在界面上热切换，不用重启或改文件 | 配置分两处；逐字段来源由凭据层 / 配置端点标注 |
| 外部凭据统一保管层 | 只写不回显、来源标注、脱敏一套契约，新增一类凭据只需登记 | 本系统自签发的令牌与账号密码不属于这一层 |
| 调度与 API 同进程 | 部署简单，单机即可 | 长任务必须异步提交；事件循环阻塞会让 cron 错过触发，故统一给宽限期 |

下钻：各取舍的来龙去脉见 [version-history.md](./version-history.md) 与 [archive/README.md](./archive/README.md)（归档文档只讲历史，不代表现状）。

## 6. 改动该放哪

| 想做的事 | 从哪里入手 | 会碰到什么 |
|---|---|---|
| 接一个新源 | `src/fetchers/impl/` 写预置抓取器；先读 [sources/curation_policy.md](./sources/curation_policy.md) 与 [node_audit_playbook.md](./sources/node_audit_playbook.md) | 形态决定进哪个容器；新源先进观察期 |
| 加或改一张表的字段 | `models/db.py` + 一条 Alembic 迁移 | 迁移漂移测试强制二者一致；同步流是否要带上这列 |
| 改评分或标签逻辑 | `llm/article_analysis_prompt.py`、`services/article_analysis.py`、`services/taxonomy.py` | 早报、日报、榜单同时受影响；评分版本与回填 |
| 改早报 / 日报选篇 | `services/personal_digest.py`、`digest_selection.py`、`daily_brief.py` | 冻结范围、重编入口、运行时 KV 阈值 |
| 加一个读者功能 | `api/routers/reader.py` 等 + `services/` + 前端 `ReaderTab` 与移动壳 | 前缀门控、隐藏源排除（`feed_service.resolve_subscribed_source_ids`）、桌面与移动两端 |
| 加一个管理面 | `/api/admin/*` 路由 + `AdminOpsTab` | 自动 admin 门控与操作审计；是否只给根管理员 |
| 改对外接口 | 对应的 `docs/contracts/*` 与代码一起改 | 下游消费方与对端节点 |
| 部署与发布 | [release-process.md](./release-process.md)、[deploy-docker.md](./deploy-docker.md)、[deploy-baremetal.md](./deploy-baremetal.md) | 只按 tag 部署；生产由作者操作 |
