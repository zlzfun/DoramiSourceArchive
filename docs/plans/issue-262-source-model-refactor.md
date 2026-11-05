# issue #262 配套:源模型重构——源 / 采集绑定 / 抓取器三层分离(R1)

> 状态:排队中(R1 设计稿,2026-11-05;§8 拍板项待用户拍板;Codex 检视见 §11)。
> 关系:本稿展开 [issue-262-agent-ingest-sources.md](./issue-262-agent-ingest-sources.md) 的 §5.1 / §5.4 / §6 波 2,取代那里「2a → 2b → 2c」的粗分;#262 的推送通道(波 1)依赖本稿 R1。
> 执行:主代理 Claude Code(Opus 5.5)。§2 的分档来自对注册表全部 69 个类的反射盘点(只看类自身 `__dict__` 里定义的方法与数据属性),不是人工估计。
> 基线:`main` @ `454f90da`(v3.67.4)。文中 `file:line` 均对此版本。

## 0. 结论

1. **三层**:**源**(谁说的、长什么样、给谁看)/ **采集绑定**(源 → 抓取器 + 参数,0..1 个)/ **抓取器**(怎么拿,只有代码、不带身份)。另有两个相邻概念:**卡片**是源的读者视图,**条目来路**是条目上的事实(#262 波 1)。
2. **适用于所有源家族,不只 X**。管理员配置源、读者自定 RSS、37 个播客目录源本来就是三层;Archive Sync 接收的行恰好是「0 个绑定的源」(sources 流本来就不带 fetcher / params)。只有 62 个预设类把三层压进一个 Python 类。
3. **能一并改,但要分两件事看**:
   - **身份出代码**:62 个预设类的身份字段全部是纯类属性,没有一个由代码计算 → 全部机械地搬进目录文件,可以一次做完。
   - **代码收拢**:只有 30 个类自身不含任何方法 → 删类、绑定通用模板;另 32 个有自己的解析代码 → 保留为无身份的站点适配器。三层分离不要求消灭代码。
4. **节点 ID 不变**:采集任务、运行记录存的节点 ID 本来就等于 `source_id`(配置源已是如此),迁移后任务不用改;变的只是「节点 ID 解析到哪个抓取器」。
5. **最大风险不在抓取器,在源表的消费方**:约 93 处代码(38 处枚举 + 55 处按主键查)默认「`source_configs` 里有这一行 = 它不是预设源」。往表里补 62 行预设源不是零风险的纯加法,R1 必须逐处审计(§6、§9)。
6. **分五步 R0–R4**,每步可单独合入、可回退;R1 之后,不变量「每个 `source_id` 恰有一行」成立。

## 1. 三层模型

### 1.1 定义

| 层 | 回答 | 承载 | 带身份 | 谁定义 |
|---|---|---|---|---|
| **源** Source | 谁说的;名称、头像、形态、策展、可见性 | `source_configs` 表(沿用物理表名,加列;见 E1) | 是,`source_id` | 目录文件(策展源)/ 管理员 / 读者 / Agent / 同步 |
| **采集绑定** Binding | 怎么定期拿到它的内容 | 源行上的 `fetcher_id` + `params_json`,0..1 个 | 否 | 目录文件 / 管理员 / 读者(自定 RSS) |
| **抓取器** Fetcher | 拿内容的代码 | `FetcherRegistry`,按 `fetcher_id` 登记 | 否 | 代码 |

抓取器分两种:
- **通用模板** `generic_*`:参数化,任何源都能绑,如 `generic_rss`、`generic_x_timeline`;
- **站点适配器** `site_*`:为某个站点的结构写的专用代码,通常只有一个源绑它,但同样不带身份。

两个相邻概念(不在三层里,但要和三层对齐):
- **卡片**:源在读者面的视图,由源的属性决定是否出现、怎么出现(§4)。
- **条目来路**:这一条经哪条通道进来(fetch / push / manual / sync),落在 `articles` 上(#262 波 1)。推送、手工录入、同步都是**通道**,不是绑定。

### 1.2 关系与基数

```
源 1 ──── 0..1 ──── 采集绑定 ──── 1 抓取器
                                   抓取器 1 ──── 0..N 采集绑定
```

- **0 个绑定**:Agent 推送的临时源、手工导入源、内网 replica 上的同步源(绑定只在 authority)、`dorami_daily_brief`(系统生成)。
- **为什么是 0..1 而不是 0..N**:现在没有一个源需要两种定时抓法,推送也不算绑定。等真出现「同一个源两条抓取通道」(如 RSS 为主、网页兜底)再拆 `source_bindings` 表;触发条件写进 `decisions.md`(E2)。

### 1.3 不变量

1. `articles.source_id` 的每个取值,源表里都恰有一行(回填迁移 + 写入闸 + 守卫测试)。
2. 抓取器不声明身份:注册表元数据只剩 `fetcher_id`、种类(template / adapter)、产出的 `content_type`、`platform`、参数表;`name / description / source_owner / admission_status / …` 全部离开代码。
3. 条目的 `source_id` 来自绑定上下文(执行时的源行),不来自抓取器类属性 —— 从结构上消灭 #262 的 B4。
4. 消费方只读源行上的显式属性;删除所有「查不到即 X」的兜底(§5)。
5. 节点 ID = `source_id`;只有带绑定的源才是采集节点。

## 2. 适用面:逐家族盘点

### 2.1 已经是三层的

| 家族 | 规模 | 现状 |
|---|---|---|
| 管理员配置源 | 运行时数据 | 行 + `fetcher_id` / `params_json`;`resolve_collection_node` 执行前解析到模板(`collection_nodes.py:117`);`build_source_fetch_params` 在 overrides 之后强制回写 `source_id` / `feed_url`(`:67`) |
| 读者自定 RSS | 每人 ≤ 20 | 同上,`owner_username` 非空 |
| 播客目录 | 37 | 代码常量 `PODCAST_CATALOG` → 行,绑 `generic_podcast_rss`(`podcast_catalog.py:213`);**只插不改**(`update_existing=False`,`:258-290`) |
| 同步接收行(replica) | 运行时数据 | 只有源、没有绑定:`_source_payload` 有意不带 fetcher / params(`archive_sync_v2.py:292-299`),接收端还会把 `fetcher_id` / `params_json` 清空(`:2059-2060`) |

### 2.2 压成一层的 62 个预设类

| 档 | 数 | 构成 → 去向 |
|---|---|---|
| **A 纯数据**(类自身 0 个方法) | 30 | X 7(只有 `handle` / `user_id`)→ `generic_x_timeline`;GitHub Releases 3、GitHub 仓库 1、HF 模型 2 → 各自已有的模板;RSS 16(`feed_url` + 几个默认值)→ `generic_rss`;`docs_gemma_release_notes` 1 → 把 `DevsiteReleaseNotesFetcher` 登记为模板 `generic_devsite_release_notes` |
| **B 少量覆写**(1–3 个方法,多为正文清洗 / 详情选择) | 15 | RSS 10:apple_mlr、deepmind_blog、hf_blog、lilianweng、mistral_news、the_decoder、geoffrey_litt、import_ai、reddit_localllama、microsoft_ai_models;网页 4:cursor_changelog、claude_blog、minimax_research、artificial_analysis;`docs_deepseek_api_changelog` 1 |
| **C 自有抓取逻辑**(自写 `_run` 或 ≥ 4 个方法) | 17 | rss_openai_news(浏览器渲染)、rss_hn_ai(Algolia)、web_aiera(WP REST)、web_qbitai、web_anthropic_news、web_ithome_ai、web_qwen_blog、web_kimi_research、web_meta_ai_blog、7 个单页拆分类(arena / claude_code / codex / xai / zai / bytedance_seed / hf_daily_papers)、github_trending_daily |

- B、C 两档 32 个 → 站点适配器:去掉身份属性,改按 `site_*` 登记(E6);身份照样进目录文件。
- B 档**不**改成「模板 + 声明式清洗正则」:正则写进数据文件会失去单测与可读性,收益只是少十几个类(E5)。
- A 档迁移需要的模板参数缺口:`generic_rss` 缺 `feed_content_as_markdown`(16 个里 14 个用到);GitHub 的 `default_include_prereleases` 对应已有参数 `include_prereleases`;X 的 `handle` / `user_id` 已在参数表里。(#262 方案 §5.4 说「补两个参数」,实为一个。)

关键事实:不论哪一档,身份字段(`source_id / name / icon / description / admission_status / content_shape / default_kind / platform / source_owner / source_brand / source_scope / source_channel / source_url / provenance_tier / signal_strength / noise_risk / fetch_reliability`)都是类属性字面量。所以「身份出代码」对 62 个一视同仁。

### 2.3 散在代码里的源级策展常量

| 常量 | 现状 | 去向 |
|---|---|---|
| `ESSENTIAL_FETCHER_IDS`(62) | 恰好等于全部预设源;只喂 `get_all_metadata()` 的 `default_visible` / `curation_reason`,**运行时没有任何消费方**(只有测试与文档引用) | 删除。它表达的「进过审阅」改由 `origin=catalog` + `admission_status` 表达 |
| `FOCUSED_FETCHER_CURATION` | 空字典 | 删除 |
| `DECOMMISSIONED_FETCHER_IDS`(7) | 两处重复(`registry.py:103`、`source_naming.py:19-29`) | 目录文件墓碑段 → 行上 `retired_at` 非空 |
| `SOURCE_COLLECTIONS` | 代码注册表(`source_collections.py`) | 目录文件合集段 |
| `DEFAULT_SUBSCRIPTION_SOURCE_IDS`(9) | 代码缺省 + KV 覆盖(`reader_defaults.py:29-39`) | 缺省进目录文件;KV 覆盖保留(运维拥有) |
| `PODCAST_CATALOG`(37) | 常量 → 行,只插不改 | 目录文件播客段;字段归属与并入方式见 E4 |
| `dorami_daily_brief` | 特殊常量 | 目录文件系统段(`origin=system`,无绑定) |

## 3. 目录文件

### 3.1 形态

- 路径:`src/sources/catalog/*.toml`(待建),按家族分文件:`x.toml`、`rss.toml`、`web.toml`、`docs.toml`、`github.toml`、`podcasts.toml`、`collections.toml`、`system.toml`(含墓碑)。Python 3.12 自带 `tomllib`,零依赖。
- 为什么分文件:约 100 个源加上合集与墓碑,放一个文件会有一千多行,PR 审阅与合并冲突都难。
- 为什么用 TOML 而不是 Python 数据模块:TOML 写不了代码,「目录只是数据」由格式本身保证。

```toml
[[source]]                       # A 档:身份 + 通用模板绑定
id = "x_deepseek_ai"
name = "X · DeepSeek"
shape = "social"
platform = "x"
admission = "incubating"
owner = "deepseek"
brand = "deepseek"
scope = "frontier_ai_lab"
homepage = "https://x.com/deepseek_ai"
description = "DeepSeek 官方模型、论文与服务动态。"
identity_keys = ["x:user_id:1714580962569588736", "x:handle:deepseek_ai"]
binding = { fetcher = "generic_x_timeline", params = { handle = "deepseek_ai", user_id = "1714580962569588736", limit = 25 } }

[[source]]                       # C 档:身份 + 站点适配器绑定
id = "web_anthropic_news"
name = "Anthropic 新闻"
shape = "article"
admission = "admitted"
# …策展维度…
binding = { fetcher = "site_anthropic_news" }

[[source]]                       # 系统源:无绑定
id = "dorami_daily_brief"
origin = "system"
name = "哆啦美·AI资讯日报"
shape = "article"
```

### 3.2 对账

- **时机**:节点启动时,`ensure_migrated` 之后(沿 taxonomy reconcile 与播客目录导入的先例)。
- **在哪对账(E3,推荐)**:**每个节点都从自带的目录文件本地对账;sources 流不再导出 `origin=catalog` 的行**。理由:
  - 目录文件随代码发版,同版本节点的目录数据天然一致;今天预设源也是「随代码到内网」,语义不变。
  - 避开接收端的**接管副作用**:`_apply_sources` 遇到本地已有、无权威标记的同 id 行时会接管它,并删本地 `SourceStateRecord`、把该源**全部文章**的本地分析标为作废(`archive_sync_v2.py:2008-2056`)。如果 replica 先本地建了目录行、之后又经同步收到同 id 行,就会触发这条路径。
  - 避开混版本:新 authority 若开始导出 62 行,旧版 replica 上那 93 处消费方会突然多出 62 行、且不认识 `origin` 列。
  - 需要处理的边角:一个源从 `origin=admin` 改成 `origin=catalog`(转正)时,导出过滤不得发 tombstone(否则 replica 删行);replica 上已有的同步行保留权威标记,只更新目录拥有的字段。
- **字段归属(E4,推荐)**:目录拥有身份 / 呈现 / 策展维度 / 准入 / 身份键 / 绑定,对账时以文件为准;运维拥有 `is_active`、隐藏、`ai_analysis_enabled`、调度,对账不碰。管理面对目录行的目录字段只读。
- **删除**:目录里删掉一个源,必须同时写进墓碑段,对账才把行标 `retired_at`;守卫测试比对上一版快照,「消失但没进墓碑」直接失败。对账永不物理删行。
- **播客目录并入**:播客目录今天是「只插不改」,生产上可能有管理员改过的名称或简介。并入前先跑一次**只读差异报告**(目录值 vs 库内值),逐条拍板后再切成「以文件为准」。

### 3.3 守卫

- 结构:id 唯一、身份键唯一、`shape` / `admission` / `origin` 取值合法、绑定的抓取器存在、参数通过该抓取器的参数表。
- 墓碑:墓碑 id 不得复用;快照比对见上。
- 取代钉在 `tests/test_rss_fetcher.py` 的准入快照,以及 `test_fetcher_curation.py` / `test_source_admission.py` 里针对 `ESSENTIAL_FETCHER_IDS` 的断言。

## 4. 卡片 = 源的视图

现在发现目录(`_reader_sources_catalog`,`reader.py:1299`)在读取时从**三处并集**拼出来:注册表、配置行、`articles` 里出现过的 `source_id`。可见性规则散在 `ESSENTIAL_FETCHER_IDS`、`DECOMMISSIONED_FETCHER_IDS`、KV 隐藏名单、`owner_username`、准入状态等多处。不变量 1 成立后,目录只读源表一处,由一个服务端函数算出每个源的**列出状态**:

| 列出状态 | 条件 | 发现页 | 源页 / 已订阅者 | 交付(feed / MCP / 早报 / 日报) |
|---|---|---|---|---|
| `retired` | `retired_at` 非空(含墓碑) | 不列 | 已订阅者可见以便退订(现行口子) | 不再产出 |
| `hidden` | 在 KV 隐藏名单 | 不列 | 不可见 | **全量排除**(补 #262 B6:日报候选池、`/api/feed/articles`) |
| `private` | `owner_username` 非空 | 只对创建者 / 订阅者 | 同左 | 只对订阅者 |
| `unlisted` | `provisional`(#262 波 1) | 不列 | 可经条目上的来源名进入、可单独订阅 | 按 #262 D2 / D3 |
| `listed` | 其余 | 列 | 可见 | 正常 |

- **卡片类型**有两种:源卡片、合集卡片(聚合多个 `listed` 源;合集成员不得是非 `listed` 源,守卫校验)。
- **卡片上的属性由服务端算好下发**:名称、图标 / 头像、角色、分组、形态、平台、列出状态、能否订阅。前端 `sourceTaxonomy.js` 只保留「分组 → 文案」的展示映射,删掉 `sourceRoleOf` 等推导与兜底;下发的 `registered` / `configured` 两个无人消费的标志(`reader.py:1378-1379`)换成 `origin` / `listing`。
- **历史「只在 articles 里的源」**(如 `manual`、`import_social_posts`):R1 回填为 `origin=legacy` 的行,先保持 `listed`(无可见变化);#262 波 1 上线 `provisional` 后,按 D2 拍板改为 `unlisted`。

## 5. 要删的「查不到即 X」与注册表冒充源目录

| 类别 | 位置 |
|---|---|
| 角色 / 名称兜底 | `source_naming.py:45,71,84,115`(`DEFAULT_SOURCE_ROLE`)、`:135-145`(名称退回裸 id);`frontend/src/sourceTaxonomy.js:247-254`(角色退回 official) |
| 形态 / 类型兜底 | `api/sources.py:87-100`(按 `content_type` 猜形态)、`:103-150`(按 `source_type` 猜形态 / 类型 / 平台);`articles.py:271`;`event_assignment.py:376-383`、`event_board.py:168-171` |
| 信任兜底 | `user_sources.py:235-243`、`article_analysis.py:397-403`(「无配置行 = 平台源」) |
| 注册表被当成源目录 | `mcp_server.py:57-59`(`list_sources`,且状态按 `SourceStateRecord.fetcher_id` 建索引,配置源共用模板时会撞);`x_api.py:95,140`;`content_classification.py:277`;`user_sources.py:454`;`ingest.py:59`;`personal_digest.py:978-985`;`event_board.py:149-156`;`opml.py:159`;`source_naming.py:90,126`;`event_assignment.py:346`;`api/sources.py:168`;`collection_nodes.py:192`;`reader.py:1407` |
| 脚本 | `scripts/ensure_daily_collection_job.py:103`(每日任务 = 「注册表里非模板、非观察期」,改为「有绑定、`origin=catalog`、`admitted`」);`smoke_analysis_release.py:95`;`collect_taxonomy_bootstrap_sources.py:76` |
| 运维面 | `monitoring.py:214`(按 `SourceStateRecord.fetcher_id` 过滤) |

替代物:新服务 `services/source_catalog.py`,提供 `get_source(sid)`、`list_sources(...)`、`listing_of(source, viewer)`。注册表今天是进程内常量,改读库后,热路径(早报评分、事件归并)要有进程内缓存,写入时按版本号失效,避免 N+1。

## 6. 分步路径

| 步 | 内容 | 可见变化 | 依赖 |
|---|---|---|---|
| **R0 前置修复** | 即 #262 波 0 中与本稿相关的:B4(X 模板身份,先写失败用例)、B7(前后端角色兜底统一)、B8(事件归并形态兜底) | 无 | 无 |
| **R1 源表成为全集** | Alembic 加列(`origin`、`shape`、`platform`、`icon`、`avatar_url`、`homepage_url`、`default_kind`、`language`);每个节点启动时为注册表里 62 个预设类各生成一行(`origin=catalog`,属性取自类属性,绑定 = 自身类 id),为 `articles` 里无行的 `source_id` 回填 `origin=legacy` 行,墓碑 7 个回填为 `retired`;sources 流导出排除 `origin=catalog`;新增 `source_catalog` 读服务,§5 的注册表消费方改读它;**逐处审计约 93 处 `SourceConfigRecord` 读写**,枚举处显式写出 `origin` 谓词(默认排除目录行以保持现行为);写入闸先只记日志与计数 | 无(镜像行与类属性逐字段相等,有测试钉住) | R0 |
| **R2 目录文件** | 身份从类属性搬进 `src/sources/catalog/*.toml`;镜像行的来源从类属性切到文件;类删掉身份属性;收编 §2.3 全部常量(播客先跑差异报告);守卫测试;管理面对目录行的目录字段只读 | 管理面只读态(小,需目检) | R1;E3 / E4 |
| **R3 能力化** | A 档 30 个删类、绑定改指通用模板;`generic_rss` 加 `feed_content_as_markdown`、Devsite 基类登记为模板;B / C 档 32 个改名 `site_*` 登记为适配器;**参数锁定**:绑定参数优先于运行时覆盖,只放行白名单(`limit`、测试限额),取代 2026-07「非模板节点只接受自身参数表」那条依赖预设类的规则(`base.py:129-137`);状态表与运行记录里「执行抓取器 id」的消费方(`mcp_server.py:59`、`monitoring.py:214`)改按 `source_id` | 无 | R2 |
| **R4 卡片视图收口** | 目录只读源表 + 列出状态函数;前端删兜底;合集来自目录文件;隐藏名单覆盖交付全路径 | 发现页应无变化(需目检) | R1(可与 R2 / R3 并行) |

- **R3 分批**:X 7(依赖 B4)→ GitHub / HF 6 → RSS 16 → Gemma 1;B / C 档只是改名与去身份,一批完成。
- **R3 对拍**:每个从类迁到「模板 + 参数」的源,在录制样本上比对旧类与新绑定:条目 id 集合相等、正文逐字节相等(`generic_web` 当初的验证方式)。
- **与 #262 的顺序(E7,推荐)**:R0 → R1 → R4 → #262 波 1(推送)→ R2 → R3。推送需要「每个源都有行」(R1)和 `unlisted` 列出状态(R4);目录文件与能力化不阻塞推送。

## 7. 影响面

- **后端**:§5 全部位置;`fetchers/registry.py`(元数据瘦身、删常量)、`fetchers/base.py`(血统覆写改读上下文)、`services/collection_nodes.py`、`services/source_collections.py`、`services/reader_defaults.py`、`services/podcast_catalog.py`、`services/archive_sync_v2.py`(导出过滤、`_SOURCE_FIELDS`)、`storage/impl/db_storage.py`(写入闸)、Alembic 迁移。
- **前端**:`DiscoverPage.jsx`、`sourceTaxonomy.js`、`FetchTab.jsx`(节点列表 = 有绑定的源)、`CustomNodeBuilder.jsx`、`FetchRunsTab.jsx`、`admin/TaxonomyLedger.jsx`。
- **测试**:A 档 30 个的类级测试改为「模板 + 参数」;B / C 档测试跟随改名;准入快照与 `ESSENTIAL_FETCHER_IDS` 断言改为目录守卫;新增镜像相等、93 处审计的行为钉子、对拍、对账幂等、墓碑快照。
- **契约与文档**:`docs/contracts/archive_sync.md`(sources 流导出范围与新字段);`AGENTS.md` 抓取器那一行(「加源 = 硬化 preset fetcher」→「加策展源 = 目录文件一条;代码只写站点适配器」,L0 改动);`docs/architecture/fetchers-and-sources.md`、`content-model.md`、`reader-distribution.md`;`docs/sources/curation_policy.md`、`node_audit_playbook.md`;`decisions.md` 第 318 行(双路径 preset 类 + config 源)与第 333 行(合集存代码注册表)标为被取代并加新决策行。
- **内网 `master`**:已核 `origin/master` 相对 `main` 在 `src/fetchers/` 只差 `base.py` 4 行与 `web_content/legacy_backend.py` 2 行,没有内网独有的抓取器、合集或播客目录;R0 / R3 改 `base.py` 时 tag 同步会有冲突,按 `INTRANET_DELTA.md` 处理。

## 8. 拍板项

| # | 问题 | 选项(推荐在前) |
|---|---|---|
| E1 | 源实体落在哪 | **扩 `source_configs` 加列,R1 逐处审计约 93 处读写** / 新建 `sources` 表、`source_configs` 退为配置表(审计面小,但过渡期两张表都表达「源」,同步触发器与契约要重做) |
| E2 | 绑定基数 | **行上 0..1;出现「一源两条抓取通道」再拆表** / 现在就建 `source_bindings` 0..N |
| E3 | 目录行怎么到内网 | **每个节点从自带目录文件本地对账,sources 流不导出目录行** / 只在 authority 对账、经 sources 流同步(要处理接管副作用与混版本) |
| E4 | 字段归属 | **目录拥有身份 / 呈现 / 策展 / 准入 / 绑定,管理面对这些字段只读;播客并入前先出差异报告** / 只插不改(播客现状,目录改了不生效于已有行) |
| E5 | B 档 15 个 | **保留为站点适配器** / 给模板加声明式清洗参数后收为纯数据 |
| E6 | 适配器 id | **新命名空间 `site_*`**(节点 ID 是 `source_id`,适配器 id 只被绑定引用,改名免费)/ 沿用旧源 id(适配器 id 与源 id 同名,易混) |
| E7 | 与 #262 的顺序 | **R0 → R1 → R4 → #262 波 1 → R2 → R3** / 先做完 R1–R4 再做推送 |

#262 方案的 D6(源目录数据化做到哪)与 D7(转正的事实来源)由本稿 E1–E4 细化;若采纳本稿,D6 / D7 随之定。

## 9. 风险

- **R1 审计面**:约 93 处读写把「有配置行」当作「非预设源」。缓解:枚举处显式写 `origin` 谓词;对每处的现行为写钉子测试再改;R1 合入前全量套件 + 生产库副本上跑一次 `/api/reader/sources`、`/api/fetchers`、早报构建、日报构建,前后输出 diff 为空。
- **性能**:注册表是进程内常量,改读库后热路径可能 N+1;`source_catalog` 带进程缓存与版本号失效。
- **抓取稳定性**:R3 改了 62 个线上节点的执行路径。缓解:逐源对拍、分批、每批可单独回退;R3 前后对比一周的每源入库数。
- **播客语义变化**:从「只插不改」到「以文件为准」可能覆盖生产上的人工修改。缓解:差异报告 + 逐条拍板。
- **混版本**:E3 推荐方案下 sources 流对目录行静默;需确认接收端对新增字段「忽略未知键」(`_apply_sources` 只遍历本端 `_SOURCE_FIELDS`,静态看成立)。
- **测试搬迁量大**:A 档 30 个类的测试要改写;B / C 档测试只跟随改名。

## 10. 不做

- 不消灭站点适配器代码:C 档的浏览器渲染、私有 API、内嵌 JSON 解析本来就该是代码。
- 不把策展源的事实来源放进数据库 + 管理面 CRUD:失去 PR 审阅、`git` 即审计、开发机与新部署开箱一致。
- 不改 `source_configs` 表名、不改 `source_id` 取值、不迁历史条目 id。
- 不做 0..N 绑定(E2 触发条件出现前)。
- 不把正文清洗正则写进数据文件(E5)。

## 11. 检视

(待 Codex 检视后补记:检视方、模型、轮次、逐条表态与结论。)
