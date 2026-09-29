# 系统总览

> 面向人的整体设计导览，只讲稳定结构：产品定位、领域模型、核心数据流、模块边界与关键取舍。
> 事实以代码为准；字段、端点、参数与波次细节不在本文，按各节末尾链接下钻。
> Agent 用的完整架构简报是 [`CLAUDE.md`](https://github.com/zlzfun/DoramiSourceArchive/blob/main/CLAUDE.md)，全量文档索引是 [`docs/README.md`](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/README.md)。

## 结论先行

- **它是什么**：哆啦美是一套 AI 资讯聚合系统。一侧把上百个外部源（官博、媒体、RSS、GitHub、X、播客）采集进一个 SQLite 归档；另一侧把归档按人分发——订阅制阅读器、个人早报、公共日报、榜单、令牌化的 feed 与 MCP 接口。
- **谁在用**：两种登录角色。`admin` 是超级用户，管采集、治理与运维，同时也能当读者；`user` 只是读者，唯一的界面是阅读器。
- **数据怎么走**：抓取器 → 入库（触发器同步 FTS5 全文索引）→ 提交后排队做 AI 分析（新闻价值分 + 受治理的标签）→ 各分发面共用这份归档，需要分数或标签的产出复用同一份文章分析。
- **最重要的三条取舍**：归档是可靠性边界，AI 分析永远在入库之后、失败不影响归档；检索基础设施只有 SQLite 自带的 FTS5，不维护向量库；「源」只是一个 `source_id` 字符串身份，订阅、可见性、交付范围都挂在它上面。
- **改动该放哪**：见[第 6 节](#_6-改动该放哪)的判断表。

## 1. 产品定位与角色分面

系统分两层协作：**采集 / 归档侧**（抓取、入库、分析、治理）和**读者 / 分发侧**（订阅、阅读、检索问答、交付）。两层主要通过数据库里的归档记录衔接：读者侧不触发公共源采集，只读已归档内容；唯一例外是读者自定源，创建时会预取并提交首次抓取，之后由定时任务刷新。

主轴是**登录账号角色**（`users.role`）：

| 角色 | 能用的面 | 前端形态 |
|---|---|---|
| `admin` | 采集面（节点、采集任务、运行史、源配置、日报、LLM 配置）+ 运维管理 + 全部读者面 | 左侧应用导轨的管理台；轨底可切进同一个阅读器（「管理员双界面」） |
| `user` | 仅读者面（订阅、阅读、早报、问答、个人 feed / MCP 令牌） | 整页阅读器，其余都收在设置柜 |

门控在后端一处裁决：`src/api/app.py` 的 `COLLECTOR_API_PREFIXES` / `READER_API_PREFIXES` 按路径前缀判定，`/api/admin`、`/api/accounts` 另强制 admin；其中账户明细再收窄到「根管理员」。前端只镜像后端透出的能力位（`/api/runtime`），不自己判权。

还有一根**可选的部署轴** `[runtime] role`（`all` / `collector` / `reader`，默认 `all`），与账号角色取交集，只在把采集和分发拆到两台机器时才有意义。双节点的设计口径是两端都跑 `all`，分工靠[第 3 节](#_3-核心数据流)的「数据权威」而不是这根轴。

下钻：[CLAUDE.md · Key Design Decisions](https://github.com/zlzfun/DoramiSourceArchive/blob/main/CLAUDE.md#key-design-decisions) 中 *Access control*、*Accounts are database-managed*、*Admin dual-surface* 三段；根管理员见 [admin-root-admin-and-account-growth.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/admin-root-admin-and-account-growth.md)。

## 2. 领域模型

所有 ORM 表在 [`src/models/db.py`](https://github.com/zlzfun/DoramiSourceArchive/blob/main/src/models/db.py)（约 58 张）。按职责归成六组，每组只列承重实体：

**源（Source）——没有一张「源表」。** 源的身份是字符串 `source_id`，来自三处的并集：
- 代码里的抓取器类（`src/fetchers/impl/`，注册表启动时自动扫描），即「内置源」；
- `source_configs`（`SourceConfigRecord`）：配置驱动的源——管理员加的 RSS / 网页 / X 账号，以及读者自定源（RSS 或播客 feed，`source_id` 以 `user_rss_` 开头；同一 URL 复用一个源，谁能看由订阅关系决定，不进公共目录）；
- 归档里出现过的 `source_id`。

每个源有一种**内容形态**（`article` / `bulletin` / `social` / `podcast`，`src/api/sources.py`），决定它进阅读器的哪个容器。源的健康与游标在 `source_states`，每次抓取一行 `fetch_runs`。公共源调度的唯一实体是**采集任务** `collection_jobs`（一组节点 + 一个 cron），运行记录 `collection_job_runs` 聚合它派生的 `fetch_runs`。

**条目（Article）——一切内容都是 `articles` 的一行。** 文章、动态、推文、播客单集、公共日报都在同一张表，靠两个正交维度区分：`content_type`（数据形状）与 `source_id`（渠道）。形态特有字段序列化进 `extensions_json`，不为每种内容建表。FTS5 虚拟表 `articles_fts` 由触发器与 `articles` 同步。

**分析与标签（Analysis / Taxonomy）。** `article_analyses` 是每篇文章当前的 AI 结果（新闻价值分、摘要、体裁），`article_tag_assignments` 把它挂到受治理的规范标签 `cms_tags`；标签目录全局、版本化（`taxonomy_versions`），另有别名、候选与证据、重打标签任务，产品词汇不会被自动发布。

**读者状态（Reader）。** 订阅 `reader_subscriptions`、个人聚合令牌 `reader_feed_tokens`、收藏、已读状态与未读水位、兴趣 `user_interest_tags`、公开分享链接 `article_shares`。三个范围要分清：
- 订阅记录用 `filters_json` 描述范围，可含多个源；阅读器的一键订阅为每个源建一条单源记录；
- 普通用户的「我订阅」= 有效订阅的 `source_id` 并集减去被隐藏的源，它是阅读器订阅视图和该用户 feed / MCP 令牌的范围；
- admin 的聚合令牌覆盖全站可见的公共源，不依赖订阅。个人早报的兴趣部分也可以选进订阅外的公共条目。

**产出物（Outputs）。**
- 个人早报：`personal_digest_editions` + `personal_digest_items`，每期冻结订阅范围与兴趣版本，条目是含分析值的不可变快照；
- 公共日报：不单独建表，写成 `articles` 里 `source_id=dorami_daily_brief` 的一条记录，候选台账在 `daily_brief_candidates`；
- 全站榜单：`ranking_snapshots` 及其条目，每天由已有分析确定性生成，不调 LLM；
- 播客加工品：`podcast_processings` 多阶段状态机（转写、分析、中文精简稿与音频）及其文本 / 音频产物、预算与成本账；
- 媒体：`media_assets`（外链图片的本地缓存，按内容哈希去重）、`image_insights`（配图识别结果，按图片字节哈希缓存）、`object_blobs`（OSS 位置索引）。

**运维与平台。** `users`、`admin_audit_logs`、计量（`ai_usage` / `reader_reads` / `login_events`）、`jobs`（持久化后台任务）、反馈与公告、`app_settings`（运行时 KV 配置——多数开关和阈值在这里，不在 ini）、归档同步的修订时钟与实体状态。

## 3. 核心数据流

```
[采集 / 归档侧]
抓取器（采集任务 cron 或手动触发）
  └─▶ DataPipeline（去重）─▶ DatabaseStorage ─▶ articles ⇄ articles_fts（FTS5，触发器同步）
        提交后（app.py 采集跟踪函数）
          ├─▶ 新条目图片预取 ─▶ 媒体库
          └─▶ 分析队列 ─▶ 文章分析 worker（每分钟）─▶ article_analyses（分数 / 摘要 / 规范标签）
归档 + 分析 ─▶ 归档同步导出（采集面门控）─▶ 接收节点导入（仅 admin）

[读者 / 分发侧] 共用 articles；需要分数或标签的读 article_analyses
  ├─▶ 阅读器（订阅 / 兴趣 / 收藏 / 发现）
  ├─▶ 检索问答（LLM 规划 → FTS5 召回 → 选篇 → 作答）
  ├─▶ 个人早报（08:30 冻结范围编排）
  ├─▶ 公共日报（复用分析分 → 去重 → 编辑 → 确定性渲染，写回 articles）
  ├─▶ 全站榜单（07:00 快照）
  └─▶ feed（/api/feed，令牌版 /api/public/feed）与 MCP（/mcp）
```

要点：
1. **入库与分析解耦。** 流水线提交后，`app.py` 的采集跟踪函数在分析总闸打开时把新条目放进队列（存储层只在播客元数据刷新时另有入队）；`services/article_analysis.py` 的租约 worker 异步处理，可重启续跑。分析缺失就是「没有」，读者面不显示分数，不当零分。
2. **一把尺子。** 新闻价值分只在文章分析里算一次（`src/llm/article_analysis_prompt.py`）；早报、日报、榜单、阅读器都用这个分，日报对尚未分析的候选就地用同一个函数补评但不写回。
3. **检索基础设施只有 FTS5。** 阅读器搜索、问答、MCP 检索都用 `src/storage/fts.py`，各自组织召回；关键词过短或 FTS 不可用时退回标题匹配。问答在召回前后加 LLM 规划与选篇。
4. **调度在进程内。** APScheduler 与 API 同进程，采集任务、分析、早报、日报、榜单、播客 worker、留存清理、远程同步、自定源刷新各自注册；长任务提交后返回 `job_id`，状态持久化在 `jobs` 表。
5. **播客是一条旁路。** 单集照常作为条目入库（只取 RSS 元数据，不下载音频）；达到门槛的单集再进 `podcast_processings` 状态机。

**双节点归档同步的位置。** 外网节点采集并分析公共源，是**权威方**；内网节点经归档同步拉取，对内网读者服务，是**接收方**。接收方按固定顺序拉取多条各自带检查点的流，删除以墓碑传播。导入的记录带权威 ID（`articles.analysis_authority_id`、`source_configs.collection_authority_id`），接收方本地的采集与分析入口据此拒绝改写；内网读者自定源仍在本地采集分析，不回传。标签目录由权威方独占，接收方只读。

下钻：归档同步协议（流清单、顺序、部署先后）[contracts/archive_sync.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/contracts/archive_sync.md)；评分 [unified-news-scoring-plan.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/unified-news-scoring-plan.md)；检索 [reader-search-architecture.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/reader-search-architecture.md)；个人早报 [personal-brief-interest-union.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/personal-brief-interest-union.md)；播客 [podcast-wave-plan.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/podcast-wave-plan.md)；双节点上线 [aliyun-dual-node-deployment.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/aliyun-dual-node-deployment.md)。

## 4. 模块边界

后端（`src/`，Python FastAPI + SQLModel）。下表是各目录的主要职责，末列是改动时要知道的例外：

| 目录 | 主要职责 | 已知例外 |
|---|---|---|
| `fetchers/` | 抓取器基类、注册表、`impl/` 内置源、`web_content/` 可选浏览器正文后端；产出内容对象 | X 抓取器经注入的 engine 读运行配置与游标、记配额 |
| `pipeline/` | 驱动抓取器、去重、把结果交给存储 | — |
| `storage/` | SQLite 存储、FTS5 表与查询、迁移入口、同步修订时钟 | 提交前执行同步权威围栏与播客准入 |
| `models/` | ORM 表（`db.py`）与内容数据类（`content.py`），表的单一事实来源 | — |
| `services/` | 业务逻辑的主体：分析、标签、早报、日报、榜单、订阅、检索问答、媒体、播客、同步、账户、计量 | — |
| `api/` | `app.py`（鉴权中间件、前缀门控、调度器、采集跟踪、生命周期）+ `routers/`（按域拆分的端点）+ 共用查询层（`articles_view.py`、`feed_service.py`） | 部分路由含业务逻辑，如读者自定源创建、检索范围判断 |
| `llm/` | OpenAI 兼容客户端与提示词；不记录 api_key，计量经回调、不阻断主流程 | — |
| `mcp_server.py` | MCP 服务，挂在 `/mcp` | — |

表结构：启动时先 `ensure_migrated` 把文件库迁移到头，存储初始化再调用 `create_all()`（内存库靠它建表）；任何模型改动都必须配 Alembic 迁移。启动顺序（`src/main.py` 与 `docker/entrypoint.py` 相同）：迁移 → 按 `[taxonomy] deployment` 对齐标签目录 → 起 uvicorn 与调度器。

前端（`frontend/src/`，React + Vite + Tailwind v4）：`api.js` 是唯一的后端调用层；`main.jsx` 先把公开分享页分流出去，其余进 `App.jsx`（登录门 + 按能力位过滤的页签）；读者面是 `ReaderTab` / `ReaderWorkspace` 及早报、发现、榜单、兴趣页，移动端是 `components/mobile/` 独立外壳、与桌面共用 `hooks/useReaderState`；管理面是 `AdminOpsTab` 与 `components/admin/`。设计令牌与角色类只在 `index.css`，改前端先读 [frontend/conventions.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/frontend/conventions.md)。

下钻：[CLAUDE.md · Project Structure](https://github.com/zlzfun/DoramiSourceArchive/blob/main/CLAUDE.md#project-structure)、[Key Endpoints](https://github.com/zlzfun/DoramiSourceArchive/blob/main/CLAUDE.md#key-endpoints)、[Database migrations](https://github.com/zlzfun/DoramiSourceArchive/blob/main/CLAUDE.md#database-migrations-alembic)。

## 5. 关键设计取舍

| 取舍 | 为什么 | 代价 / 边界 |
|---|---|---|
| SQLite + FTS5，不上向量库 | 生产硬件小；触发器保证索引与正文零漂移，无需对账；向量 RAG 曾实现但生产从未跑起来 | 模糊语义查询靠 LLM 改写关键词弥补；重新引入的条件见 [rag-retirement-plan.md §4](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/rag-retirement-plan.md) |
| 单表 `articles` + `extensions_json` | 新增内容类型不改表；阅读、检索、交付、同步一套代码 | 形态差异在序列化层和前端容器里处理 |
| `source_id` 字符串身份 | 代码源、配置源、只剩归档的历史源统一成一个键 | 源元数据分散在注册表与 `source_configs`，取名走 `services/source_naming.py` 单点 |
| 新源优先写成硬化的内置抓取器 | 代码即策展记录，可测试可审计；通用参数化抓取器只作后端底座与模板 | 加源要发版；读者自定源是例外 |
| 归档先于分析 | 采集可靠性不受 LLM 可用性影响；分析可重跑、回填、换评分版本 | 读者面必须诚实处理「未分析」 |
| 一把新闻价值尺子 | 早报、日报、榜单口径一致；日报在分析关闭时仍能出报 | 改评分提示词前后要跑黄金集（`scripts/eval_news_value_golden.py`） |
| 双节点靠数据权威分工 | 两端都能完整运行；接收方凭权威 ID 拒绝改写导入数据 | 同步协议要随新增实体扩展 |
| 运行时开关放 KV，ini 只放部署级配置 | 管理员在界面上热切换，不用重启或改文件 | 配置分两处；逐字段来源由凭据层 / 配置端点标注 |
| 外部凭据统一保管层 | 只写不回显、来源标注、脱敏一套契约，新增一类凭据只需登记 | 自签发令牌与账号密码不属于这一层 |
| 调度与 API 同进程 | 部署简单，单机即可 | 长任务必须异步提交；事件循环阻塞会让 cron 错过触发，故统一给宽限期 |

下钻：各取舍的来龙去脉见 [version-history.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/version-history.md) 与 [archive/README.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/archive/README.md)（归档文档只讲历史，不代表现状）。

## 6. 改动该放哪

| 想做的事 | 从哪里入手 | 会碰到什么 |
|---|---|---|
| 接一个新源 | `src/fetchers/impl/` 写预置抓取器；先读 [sources/curation_policy.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/sources/curation_policy.md) 与 [node_audit_playbook.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/sources/node_audit_playbook.md) | 形态决定进哪个容器；新源先进观察期 |
| 加或改一张表的字段 | `models/db.py` + 一条 Alembic 迁移 | 迁移漂移测试强制二者一致；归档同步是否要带上这列 |
| 改评分或标签逻辑 | `llm/article_analysis_prompt.py`、`services/article_analysis.py`、`services/taxonomy.py` | 早报、日报、榜单同时受影响；评分版本与回填 |
| 改早报 / 日报选篇 | `services/personal_digest.py`、`digest_selection.py`、`daily_brief.py` | 冻结范围、订阅外兴趣条目、重编入口、运行时 KV 阈值 |
| 改阅读器列表、搜索或可见性 | 共用的 `/api/articles`（`api/routers/articles.py` + `api/articles_view.py`） | 订阅 / 兴趣 / 收藏三轴在这里组合；隐藏源排除走 `feed_service.resolve_subscribed_source_ids` |
| 加一个读者功能 | `api/routers/reader.py` 等 + `services/` + 前端 `ReaderTab` 与移动壳 | 前缀门控；桌面与移动两端 |
| 加一个管理面 | `/api/admin/*` 路由 + `AdminOpsTab` | 自动 admin 门控；`services/admin_audit.py` 只审计指定前缀下的非只读请求（少数例外）；是否只给根管理员 |
| 改对外接口 | 对应的 `docs/contracts/*` 与代码一起改 | 下游消费方与对端节点 |
| 部署与发布 | [release-process.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/release-process.md)、[deploy-docker.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/deploy-docker.md)、[deploy-baremetal.md](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/deploy-baremetal.md) | 只按 tag 部署；生产由作者操作 |
