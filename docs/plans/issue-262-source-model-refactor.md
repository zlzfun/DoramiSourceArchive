# issue #262 配套:源模型重构——源 / 采集绑定 / 抓取器三层分离(R2)

> 状态:进行中(2026-11-06 起实施;§8 拍板项 E1–E7 已于 2026-11-06 由用户按推荐拍板。R0、R1a 局部、R1b、R1c、R4 最小闭环随 #262 波 1 首个 PR 落地,R2 / R3 在后续 PR;实施偏差见 §12)。
> 关系:本稿展开 [issue-262-agent-ingest-sources.md](./issue-262-agent-ingest-sources.md) 的 §5.1 / §5.4 / §6 波 2,取代那里「2a → 2b → 2c」的粗分;#262 的推送通道(波 1)依赖本稿 R1c 与 R4。
> 执行:主代理 Claude Code(Opus 5.5)。§2 的分档来自对注册表全部 69 个类的反射盘点(只看类自身 `__dict__` 里定义的方法与数据属性),检视方已独立复核。
> 基线:`main` @ `454f90da`(v3.67.4)。文中 `file:line` 均对此版本。

## 0. 结论

1. **三层**:**源**(谁说的、长什么样、给谁看)/ **采集绑定**(源 → 抓取器 + 参数,0..1 个)/ **抓取器**(怎么拿,只有代码、不带身份)。另有两个相邻概念:**卡片**是源在读者面的视图,**条目来路**是条目上的事实(#262 波 1)。
2. **适用于所有源家族,不只 X**。管理员配置源、读者自定 RSS、37 个播客目录源本来就是三层;Archive Sync 接收的行恰好是「0 个绑定的源」。只有 62 个预设类把三层压进一个 Python 类。
3. **能一并改,分两件事**:**身份出代码**对 62 个一视同仁(身份字段全是类属性字面量);**代码收拢**只针对 30 个纯数据类(删类、绑通用模板),另 32 个保留为无身份的站点适配器。
4. **节点 ID 不变**:采集任务存的节点 ID 本来就等于 `source_id`。`FetchRunRecord.fetcher_id` 记逻辑节点 ID(不变),`SourceStateRecord.fetcher_id` 记执行抓取器 ID(R3 后会变,历史保留),两者语义不同(§6 R3)。
5. **最大风险在源表的消费方、源的生命周期和同步**,不在抓取器:`src/` 里 94 处读(39 处枚举 + 55 处主键查),另有构造、ORM 修改与 SQL 触发器;其中一部分依赖「查不到行」的语义。R1 因此拆成 R1a / R1b / R1c 三步,不是纯加法。
6. **同步保持「外网是唯一写者」**:内网从不在本地写公共源行;内网拉取时声明能力,外网**按请求投影**——新内网拿到完整源目录,旧内网拿到与今天相同的导出。投影整轮固定、切换时重放;内网在拿到完整目录之前用只读兼容视图。两种混版本窗口都安全(§3.4、§6.1)。
7. **分步**:R0 → R1a → R1b → R1c → R4 →(#262 波 1)→ R2 → R3a → R3b;每步写明回退路径。

## 1. 三层模型

### 1.1 定义

| 层 | 回答 | 承载 | 带身份 | 谁定义 |
|---|---|---|---|---|
| **源** Source | 谁说的;名称、头像、形态、策展、可见性 | `source_configs` 表(沿用物理表名,加列;E1) | 是,`source_id` | 目录文件(策展源)/ 管理员 / 读者 / Agent / 同步 |
| **采集绑定** Binding | 怎么定期拿到它的内容 | 源行上的 `fetcher_id` + `params_json`,0..1 个 | 否 | 目录文件 / 管理员 / 读者(自定 RSS) |
| **抓取器** Fetcher | 拿内容的代码 | `FetcherRegistry`,按抓取器 id 登记 | 否 | 代码 |

- 抓取器分**通用模板** `generic_*`(参数化,任何源都能绑)与**站点适配器** `site_*`(为某站点结构写的专用代码,通常只有一个源绑它,同样不带身份)。
- **绑定参数分三类**:
  - **静态参数**:目录或管理员写定的抓取配置,如 `feed_url`、`handle`、`page_url`;
  - **实例身份参数**:由部署历史形成、决定条目主键的参数,如播客收养留下的 `entry_id_namespace`(`podcast_catalog.py:240-245`、`podcast_rss_fetcher.py:451`)——一经写入,对账与编辑都不得覆盖;
  - **可临时覆盖参数**:每个抓取器在参数表里显式声明的白名单(如 `limit`、测试限额),只有它们能被单次运行覆盖,管理表单也只公开它们。
- 两个相邻概念:**卡片**(§4)与**条目来路**(fetch / push / manual / sync,落在 `articles`,#262 波 1)。推送、手工录入、同步都是**通道**,不是绑定。

### 1.2 关系与基数

```
源 1 ──── 0..1 ──── 采集绑定 ──── 1 抓取器
                                   抓取器 1 ──── 0..N 采集绑定
```

- **0 个绑定**:Agent 推送的临时源、手工导入源、内网上的同步源(同步不带绑定,接收端还会清空 `fetcher_id` / `params_json`,`archive_sync_v2.py:2059-2060`)、`dorami_daily_brief`。
- **「有绑定」≠「本机可以执行」**:后者由 `sync_consumer_policy.local_source_operation_allowed` 决定(内网只允许本地自定 RSS);节点枚举、执行、内网权限统一按「有绑定 ∧ 本机允许」。
- **为什么 0..1**:现在没有一个源需要两种定时抓法。出现「同一源两条抓取通道」再拆 `source_bindings` 表(E2)。
- **前提**:今天空 `fetcher_id` 并不表示无绑定——`resolve_source_fetcher_id` 会按 `source_type` 推导模板,模型默认类型还是 `rss`(`collection_nodes.py:39-51`)。R1b 先把隐式绑定写实,之后空值才表示无绑定。

### 1.3 不变量

1. **每个 `articles.source_id` 恰有一行源**,并覆盖完整生命周期:
   - 所有写入路径在**同一事务里**确保源行存在:`DatabaseStorage.save`、公共日报直写(`daily_brief.py:2193-2200`)、推送、手工录入;
   - 被文章引用的源**不再物理删除**,改为保留身份的退役(`retired_at` 非空 + `is_active=false`);
   - 在内网,源行只经同步到达(不变量 6);严格的「缺行即错」只在协商到 `catalog-v1` 投影、且 sources 全量重放完成之后启用,此前用只读兼容视图(§3.4-4);
   - 守卫测试扫描全库孤儿。
2. **抓取器不声明身份**(R3 之后):注册表元数据只剩抓取器 id、种类、产出 `content_type`、`platform`、参数表。
3. **条目的 `source_id` 来自执行上下文**:上下文在进入 `DataPipeline` 之前建立(Pipeline 在调用 `fetch()` 之前就读 `fetcher.source_id` 作进度键,`pipeline/core.py:97`),同模板多源并发时进度互不串。
4. **消费方只读源行上的显式属性**:删除「查不到即 X」的兜底(§5);但 `user_rss_` 前缀保护是**结构性规则**,不是兜底,保留。
5. **节点 ID = `source_id`**;节点 = 有绑定 ∧ 本机允许执行的源。
6. **内网不在本地写公共源行**:Archive Sync「外网 authority 唯一写者」不变量保持;内网对源目录只读(§3.4)。

## 2. 适用面:逐家族盘点

### 2.1 已经是三层的

| 家族 | 规模 | 现状 |
|---|---|---|
| 管理员配置源 | 运行时数据 | 行 + `fetcher_id` / `params_json`;`resolve_collection_node` 执行前解析到模板(`collection_nodes.py:117`);`build_source_fetch_params` 在 overrides 之后强制回写 `source_id` / `feed_url`(`:67`) |
| 读者自定 RSS | 每人 ≤ 20 | 同上,`owner_username` 非空 |
| 播客目录 | 37 | 代码常量 `PODCAST_CATALOG` → 行,绑 `generic_podcast_rss`(`podcast_catalog.py:213`);启动导入只插不改(`ensure_default_podcast_sources`,`update_existing=False`),管理员显式更新时保留 `entry_id_namespace` |
| 同步接收行(内网) | 运行时数据 | 只有源、没有绑定(`_source_payload`,`archive_sync_v2.py:292-299`) |

### 2.2 压成一层的 62 个预设类

| 档 | 数 | 构成 → 去向 |
|---|---|---|
| **A 纯数据**(类自身 0 个方法) | 30 | X 7(只有 `handle` / `user_id`)→ `generic_x_timeline`;GitHub Releases 3、GitHub 仓库 1、HF 模型 2 → 各自已有的模板;RSS 16 → `generic_rss`;`docs_gemma_release_notes` 1 → 新模板 `generic_devsite_release_notes` |
| **B 少量覆写**(1–3 个方法,多为正文清洗 / 详情选择) | 15 | RSS 10:apple_mlr、deepmind_blog、hf_blog、lilianweng、mistral_news、the_decoder、geoffrey_litt、import_ai、reddit_localllama、microsoft_ai_models;网页 4:cursor_changelog、claude_blog、minimax_research、artificial_analysis;`docs_deepseek_api_changelog` 1 |
| **C 自有抓取逻辑**(自写 `_run` 或 ≥ 4 个方法) | 17 | rss_openai_news、rss_hn_ai、web_aiera、web_qbitai、web_anthropic_news、web_ithome_ai、web_qwen_blog、web_kimi_research、web_meta_ai_blog、7 个单页拆分类(arena / claude_code / codex / xai / zai / bytedance_seed / hf_daily_papers)、github_trending_daily |

- B、C 两档 32 个 → 站点适配器(E5、E6);身份照样进目录文件。B 档不改成「模板 + 声明式清洗正则」。
- A 档迁移的参数缺口:
  - 已有模板只缺一个:`generic_rss` 的 `feed_content_as_markdown`(RSS 16 个里 14 个用到);GitHub 的 `default_include_prereleases` 对应已有参数 `include_prereleases`;X 的 `handle` / `user_id` 已在参数表(`x_timeline_fetcher.py:118`)。
  - 新模板要补参数化:`DevsiteReleaseNotesFetcher` 的参数表今天只有 `limit`(`curated_core_fetcher.py:455`),执行还读 `page_url`、`site_name`、`source_section`、`listing_source_label`(`:540` 起),这些都要成为静态参数。
- 关键事实:不论哪档,身份字段(`source_id / name / icon / description / admission_status / content_shape / default_kind / platform / source_owner / source_brand / source_scope / source_channel / source_url / provenance_tier / signal_strength / noise_risk / fetch_reliability`)都是类属性字面量。 但有执行路径会把 `name` 写进输出:`feed_name`(`rss_fetcher.py:398`)、`site_name` 兜底(`webpage_fetcher.py:527`、`curated_core_fetcher.py:141`)、标题前缀(`curated_core_fetcher.py:527`)——所以 R2 迁走身份时必须回投(§6 R2)。

### 2.3 散在代码里的源级策展常量

| 常量 | 现状 | 去向 |
|---|---|---|
| `ESSENTIAL_FETCHER_IDS`(62) | 恰好等于全部预设源;注册表用它生成 `default_visible` / `curation_reason`,这两个输出字段没有运行时行为消费方 | 删除;「进过审阅」由 `origin=catalog` + `admission_status` 表达 |
| `FOCUSED_FETCHER_CURATION` | 空字典 | 删除 |
| `DECOMMISSIONED_FETCHER_IDS`(7) | 两处重复(`registry.py:103`、`source_naming.py:19-29`) | 目录文件墓碑段 → 行上 `retired_at` |
| `SOURCE_COLLECTIONS` | 代码注册表 | R2 搬进目录文件合集段;R4 期间仍在代码 |
| `DEFAULT_SUBSCRIPTION_SOURCE_IDS`(9) | 代码缺省 + KV 覆盖(`reader_defaults.py:29-39`) | 缺省进目录文件;KV 覆盖保留(运维拥有) |
| `PODCAST_CATALOG`(37) | 常量 → 行 | 目录文件播客段;并入方式见 §3.2 |
| `dorami_daily_brief` | 特殊常量 | 目录文件系统段(`origin=system`,无绑定) |
| 每日采集默认范围 | 脚本按「注册表里非模板、非观察期」枚举(`ensure_daily_collection_job.py:103`) | **独立政策**:目录条目显式 `daily = true`;R1c 投影与今天的集合逐个相等,扩围另行拍板 |

## 3. 目录文件与同步

### 3.1 形态

- 路径:`src/sources/catalog/*.toml`(待建),按家族分文件:`x.toml`、`rss.toml`、`web.toml`、`docs.toml`、`github.toml`、`podcasts.toml`、`collections.toml`、`system.toml`(含墓碑)。Python 3.12 自带 `tomllib`,零依赖。
- 分文件是为了 PR 审阅与合并冲突;用 TOML 而不是 Python 数据模块,是让「目录只是数据」由格式本身保证。

```toml
[[source]]                       # A 档:身份 + 通用模板绑定
id = "x_deepseek_ai"
name = "X · DeepSeek"
shape = "social"
platform = "x"
admission = "incubating"
daily = false
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
daily = true
binding = { fetcher = "site_anthropic_news" }

[[source]]                       # 系统源:无绑定
id = "dorami_daily_brief"
origin = "system"
name = "哆啦美·AI资讯日报"
shape = "article"
```

### 3.2 对账(只在外网)

- **时机与位置**:外网 authority 启动时,`ensure_migrated` 之后;内网 consumer 节点**不对账**(不变量 6)。
- **字段归属(E4)**:
  - 目录拥有:身份、呈现、策展维度、准入、身份键、`daily`、绑定的**静态参数**;对账以文件为准。
  - 不可覆盖:**实例身份参数**(`entry_id_namespace` 等);已有值时对账跳过并在差异报告里列出。
  - 运维拥有:`is_active`、隐藏、`ai_analysis_enabled`、调度;对账不碰。
  - 管理面对目录行的目录字段只读。
- **播客目录并入**:先跑一次只读差异报告(名称、简介、参数、准入、收养状态,目录值 vs 库内值),逐条拍板后才切成「以文件为准」;覆盖前把被改字段的旧值存成前像,供回退。
- **删除**:目录里删掉一个源必须同时写进墓碑段,对账把行标为退役;守卫比对上一版快照,「消失但没进墓碑」直接失败。对账永不物理删行。

### 3.3 守卫

- 结构:id 唯一、身份键唯一、`shape` / `admission` / `origin` 取值合法、绑定的抓取器存在、静态参数通过该抓取器的参数表。
- 墓碑不得复用;`daily = true` 的集合与上一版快照一致(变化须显式更新快照);实例身份参数不可被覆盖。
- 取代 `tests/test_rss_fetcher.py` 的准入快照,以及 `test_fetcher_curation.py` / `test_source_admission.py` 里针对 `ESSENTIAL_FETCHER_IDS` 的断言。

### 3.4 同步:接收端声明能力 + 外网按请求投影(E3)

内网拉取是普通 GET:`/api/archive/v2/export/{stream}.jsonl?snapshot=&since=&after=&limit=`(`api/routers/archive_sync.py:421`),由 `remote_sync.run_pull_v2` 发起。端点没有拒绝额外查询参数的模型,所以可以向后兼容地加一个参数。

1. **只有两种有效投影**:`legacy` 与 `catalog-v1`。内网拉取时带 `receiver_capabilities=source-catalog-v1`;外网据此选定**一个**有效投影,在 manifest 里回显,**整轮同步固定不变**;内网把实际协商成功的投影记进同步进度。旧外网忽略参数、不回显 → 内网按 `legacy` 记账,不得提前标记为已切换。
2. **两种投影的内容**(只作用于 sources 与 source_states 两流):
   - `catalog-v1`:导出全部公共源行(目录行、legacy 行、临时源行),带新字段;退役 = upsert(带 `retired_at`),状态照常导出。
   - `legacy`:**成员资格稳定**——只导出「重构前就会导出的行」,用持久列 `legacy_export` 标记(R1c 迁移时给已有的公共源行置真;之后新建的非目录行置真;镜像行与 legacy 回填行置假;转正**不改**这一列,原本已导出的管理员源转成目录行后照旧导出)。退役 = sources tombstone,同时该源的 source_states 也按 tombstone 投影,避免后置的状态 upsert 在接收端把状态重新建出来;`source_states.fetcher_id` 投影为逻辑节点 ID。
   - 投影只能「换一种载荷」或「不出现在输出里」,实现上不得返回空 payload(当前导出会把空 payload 变成 tombstone)。
3. **分页契约不变**:现在是取 `limit+1` 条原始记录、游标取最后一条输出记录,接收端拒绝空的非终页(`archive_sync_v2.py:1306`、`:1647`)。投影过滤必须**先过滤后分页**(或继续扫描到有输出或真正结束),游标仍取最后一条输出记录,不得改成最后扫描位置。
4. **只读兼容视图**:外网未回显 `catalog-v1`(旧外网,或协商失败)时,新内网的读服务使用「同步来的源行 + 本地目录文件 + 文章 / 订阅占位」的只读视图——目录文件只读、占位是虚拟的,**不落库**。注册表兜底只在 R2 之前有效,所以兼容视图不能依赖它。
5. **切换时重放**:投影从 `legacy` 切到 `catalog-v1` 是协议变化,沿分析代次的做法(`remote_sync.py:136-150`):在同一事务里清掉该 authority 的 sources 与 source_states 两流 checkpoint **和**逐行应用记账(`ArchiveSyncEntityStateRecord`,否则同一 revision 会被当成已应用跳过,`archive_sync_v2.py:1800-1805`),然后全量重拉。重放完成后才启用严格不变量。
6. **presence 不扩展**:sources / source_states 今天不参与 presence 查询与全量清理(`archive_sync_v2.py:3505`),本稿保持这一点,只靠明确的 upsert / tombstone 生命周期。
7. **不再触发的副作用**:内网从不本地建公共源行,所以 `_apply_sources` 的接管(删本地状态、把文章分析转 pending 并清租约,`archive_sync_v2.py:2008-2056`)与文章流给本地行盖权威标记并删状态(`:2355-2361`)都不会被本稿触发;首次同步准备把公共源行置 inactive(`remote_sync.py:270-279`)之后,随即由全量 upsert 恢复——与今天的配置源同一路径。

## 4. 卡片 = 源的视图

现在发现目录(`_reader_sources_catalog`,`reader.py:1299`)在读取时从注册表、配置行、`articles` 里出现过的 `source_id` 三处拼出来,另外还包含「只有订阅、没有文章也没有注册」的源(`reader.py:1420`)。可见性规则散在多处。重构后分两件事计算:

### 4.1 资格(按 viewer 计算,不做全局缓存)

| 资格 | 规则 |
|---|---|
| 能否看见源页 / 条目 | 公共源:所有人(含游客);私有源:**只认订阅关系**,`owner_username` 只用于溯源、不授予权限(`models/db.py:1307`);隐藏源:不可见 |
| 能否订阅 | `listed` 与 `unlisted` 的公共源;私有源按自定 RSS 现行规则 |
| 能否交付(feed / MCP / 早报 / 日报 / 分享) | 隐藏源**全量排除**(补 #262 B6:日报候选池、`/api/feed/articles`);退役源停止新增,历史条目照旧可读、可搜;私有与凭证源沿 `dshr_` 现行检查 |

### 4.2 展示状态

| 状态 | 条件 | 发现页 | 已订阅者 |
|---|---|---|---|
| `retired` | `retired_at` 非空(含墓碑) | 不列 | 保留退订入口(现行) |
| `hidden` | 在 KV 隐藏名单 | 不列 | **保留灰显退订入口**(现行,`reader.py:1324`) |
| `private` | 私有源 | 只对订阅者 | 正常 |
| `unlisted` | 临时源(#262 波 1) | 不列 | 可经条目上的来源名进入、可单独订阅 |
| `listed` | 其余 | 列 | 正常 |

- 孤儿订阅(订阅了已不存在或无权访问的源)保留管理视图,供退订。
- 合集:静态守卫只校验成员存在且不是私有源;运行时动态过滤隐藏 / 退役成员,不要求成员永远 `listed`。R4 期间合集仍在代码里,R2 再搬进目录文件。
- 卡片属性(名称、图标 / 头像、角色、分组、形态、平台、展示状态、能否订阅)由服务端算好下发;前端 `sourceTaxonomy.js` 只保留「分组 → 文案」映射,删掉推导与兜底;`registered` / `configured` 两个无人消费的标志(`reader.py:1378-1379`)换成 `origin` / `listing`。
- 必须一并对齐、不能只靠一个展示状态替代的消费方:冻结的订阅名称与早报版面快照;游客默认订阅的差量镜像;`dshr_` 分享的隐藏与凭证检查;OPML 导出里的隐藏订阅备份与稳定 feed URL;匿名 MCP `list_sources`(无 viewer)。

## 5. 要删的兜底、要改的消费方

| 类别 | 位置 |
|---|---|
| 角色 / 名称兜底 | `source_naming.py:45,71,84,115`(`DEFAULT_SOURCE_ROLE`)、`:135-145`(名称退回裸 id);`frontend/src/sourceTaxonomy.js:247-254` |
| 形态 / 类型兜底 | `api/sources.py:87-100`、`:103-150`;`articles.py:271`;`event_assignment.py:376-383`、`event_board.py:168-171` |
| 信任兜底 | `user_sources.py:235-243`、`article_analysis.py:397-403`(「无配置行 = 平台源」);**保留** `user_rss_` 前缀的结构性保护(`user_sources.py:232`) |
| 注册表被当成源目录 | `mcp_server.py:57-62`(`list_sources`;状态按 `SourceStateRecord.fetcher_id` 对注册表 id 建索引);`x_api.py:95,140`;`content_classification.py:277`;`user_sources.py:454`;`ingest.py:59`;`personal_digest.py:978-985`;`event_board.py:149-156`;`opml.py:159`;`source_naming.py:90,126`;`event_assignment.py:346`;`api/sources.py:168`;`collection_nodes.py:185-206`;`reader.py:1407` |
| 执行与同步边界 | `pipeline/core.py:97`(进度键);`remote_sync.py:270-279`(首次同步准备);`sync_consumer_policy.py:116`;`podcast_source_adoption.py`(收养改写 id 与 `entry_id_namespace`);`archive_sync_v2.py`(导出投影、`_SOURCE_FIELDS`、载荷代次) |
| 脚本 | `scripts/ensure_daily_collection_job.py:103`;`smoke_analysis_release.py:95`;`collect_taxonomy_bootstrap_sources.py:76` |
| 运维面 | `monitoring.py:214,245`(按 `SourceStateRecord.fetcher_id` 过滤,并兼容旧记录) |

- 94 处 `SourceConfigRecord` 读(39 select + 55 get)另加 4 处构造、ORM 修改与 SQL 触发器,**按消费语义逐处审计**:只取名称 / 权限 / 配置的照常;依赖「缺行」语义的改为读显式属性;不机械地给所有查询加 `origin != catalog`。
- 新服务 `services/source_catalog.py`:`get_source(sid)`、`list_sources(...)`、`eligibility(source, viewer)`、`display_state(source, viewer)`。进程内缓存只缓存源行本身,**在事务提交后**按版本号失效,覆盖 CRUD、同步、对账、收养、隐藏操作;按 viewer 计算的资格不缓存成全局结果。

## 6. 分步路径

| 步 | 内容 | 可见变化 | 回退 |
|---|---|---|---|
| **R0 前置修复** | #262 波 0 里与本稿相关的:B4(X 模板身份,先写失败用例)、B7(前后端角色兜底统一)、B8(事件归并形态兜底) | **有**,属预期缺陷修正:未登记源的角色分组、事件归并结果会变 | 代码回退 |
| **R1a 兼容读服务** | 新增 `source_catalog`,按今天的优先级读「注册表 + 配置行」;§5 的消费方改读它;不改任何数据 | 无(生产库副本上 `/api/reader/sources`、`/api/fetchers`、早报构建、日报构建前后 diff 为空) | 代码回退 |
| **R1b 显式绑定** | 迁移:空 `fetcher_id` 的配置行按 `resolve_source_fetcher_id` 写实(跳过带 `collection_authority_id` 的同步行——它们按设计没有绑定);此后空值 = 无绑定;节点枚举改按「有绑定 ∧ 本机允许」 | 无(每日任务集合与 `/api/fetchers` 集合逐个相等,测试钉住) | 写实的值与推导结果相同,旧代码照常工作,无需回退数据 |
| **R1c 源表成为全集** | Alembic 加列(`origin`、`shape`、`platform`、`icon`、`avatar_url`、`homepage_url`、`default_kind`、`language`、`daily`、`legacy_export`;`retired_at` 已有);外网启动时为 62 个预设类生成镜像行(`origin=catalog`,绑定 = 自身类 id);回填三类(公共孤儿 → `legacy`;`user_rss_*` 孤儿 → 私有隔离行,`ai_analysis_enabled=false`、凭证状态未知按「带凭证」处理、永不列出、禁外发与分享;只在订阅里出现的 id 一并回填);墓碑 7 个 → 退役;生命周期(写入时确保、被引用不物理删);同步投影(§3.4);94 处语义审计;写入闸强制;本步新建的每一行记进**迁移台账**(id + 前像);新读模型带运行时开关 `[sources] model = compat \| full` | 无(镜像行与类属性逐字段相等;`daily` 集合与今天的每日任务集合逐个相等) | **正常回退 = 保留 R1c 的结构、数据与兼容导出层,把开关切回 `compat`**,不回退到 R1c 之前的代码;确需 down 时只按迁移台账里的精确 id 处理,先保住升级前已有的 `SourceStateRecord`(源删除触发器会连带删 `authority_id=''` 的状态并产生 tombstone,`archive_sync_revision.py:116-124`),并处理同步记账——**不得按 `origin` 批量删除** |
| **R4 卡片视图收口** | 目录只读源表;资格 / 展示状态分算;前端删兜底;隐藏覆盖交付全路径;合集仍在代码 | 发现页应无变化(需目检);B6 补齐属预期修正 | 代码回退 |
| **#262 波 1** | 推送通道、临时源(`unlisted`) | 有(新功能) | 见 #262 方案 |
| **R2 目录文件** | **只迁描述性身份**:名称、策展维度、准入等搬进 `src/sources/catalog/*.toml`,类上的 `source_id` 作为注册键保留;注册时把目录值**回投**到类属性(`name`、`site_name` 等仍被执行路径读取的字段),输出逐条对拍不变;镜像行改由文件对账;收编 §2.3 常量(播客先出差异报告);管理面对目录字段只读 | 管理面只读态(小,需目检) | 正常回退 = 保留目录文件与回投层,把对账切回只读报告;被覆盖的库内字段按前像恢复 |
| **R3a 执行机制原子切换** | 一次切换:注册键改为抓取器 id(B / C 档改名 `site_*`,旧 id 进**别名表**过渡期可解析);目录与非目录配置行里引用旧 id 的绑定一并迁移;执行上下文在进 Pipeline 前建立;参数锁定(绑定参数优先,只放行各抓取器声明的可覆盖白名单,取代 `base.py:129-137` 依赖预设类的规则);MCP / monitoring 改按 `source_id` 关联 | 无(62 个源全量对拍:条目 id、血统、输出逐条相等;同模板多源并发进度隔离) | 代码回退 + 别名表反向映射还原绑定 |
| **R3b A 档删类** | A 档 30 个分批删类、绑定改指通用模板(X 7 → GitHub / HF 6 → RSS 16 → Gemma 1);`generic_rss` 加 `feed_content_as_markdown`,Devsite 模板参数化 | 无(每批对拍) | 每批单独回退 |

### 6.1 部署顺序与混版本

前提:外网按 tag 部署先上,内网经 `master` 同步再手工部署,常晚几天,外网也可能回滚;同步是内网拉取。旧接收端对未知**字段**安全(`_apply_sources` 只遍历本端 `_SOURCE_FIELDS`),对未知**枚举值**不安全(`admission_status_of()` 把未知值当 `admitted`,`registry.py:130-133`)。

| 步 | 新外网 + 旧内网(默认窗口) | 旧外网 + 新内网 | 结论 |
|---|---|---|---|
| R0 | 只改外网采集与展示判定;线格式不变 | 无影响 | 任意顺序 |
| R1a / R1b | 节点本地;R1b 跳过同步行 | 同左 | 任意顺序 |
| R1c | 旧内网不声明能力 → `legacy` 投影,与今天的导出相同;退役按 tombstone(状态同步投影) | 旧外网不认参数、不回显 → 新内网按 `legacy` 记账,用只读兼容视图,严格不变量不启用 | 任意顺序 |
| R4 | 节点本地 | 新内网在兼容模式下运行,目录与今天一致 | 任意顺序;窗口期两端目录可能不同 |
| #262 波 1 | 临时源的 `provisional` 到了旧内网会被当成正式源;旧内网也会把推送条目的源当成「只在 articles 里的源」列进发现页 | — | **外网不能先放开**:推送是新功能,开关默认关,内网升级到 `catalog-v1` 并完成重放后再开;或内网先升级。对照 #218:那里功能默认开,外网若须先上要先显式关开关 |
| R2 | 播客目录行照旧导出(值来自目录文件);62 个目录行仍只给声明能力的内网 | 新内网收不到目录行 → 兼容模式 | 任意顺序 |
| R3a / R3b | `source_states.fetcher_id` 对旧内网投影为逻辑节点 ID,旧版 MCP `list_sources` 的状态关联不断 | 内网不采集,无影响 | 任意顺序 |

验证:R1c、R3a、#262 波 1 合入前各做一次跨版本实测(旧 tag 起接收端、新代码起生产端,再反过来),沿 #178 的做法;发版说明写部署顺序。

## 7. 影响面

- **后端**:§5 全部位置;`fetchers/registry.py`(注册键、元数据瘦身、删常量、别名表)、`fetchers/base.py`(血统覆写改读上下文、参数锁定)、`pipeline/core.py`、`services/collection_nodes.py`、`services/source_collections.py`、`services/reader_defaults.py`、`services/podcast_catalog.py`、`services/podcast_source_adoption.py`、`services/archive_sync_v2.py`、`services/remote_sync.py`、`api/routers/archive_sync.py`、`storage/impl/db_storage.py`、`services/daily_brief.py`(直写路径)、Alembic 迁移。
- **前端**:`DiscoverPage.jsx`、`sourceTaxonomy.js`、`FetchTab.jsx`、`CustomNodeBuilder.jsx`(只露可覆盖参数)、`FetchRunsTab.jsx`、`admin/TaxonomyLedger.jsx`。
- **测试**:A 档类级测试改为「模板 + 参数」并保留行为断言;B / C 档跟随改名;新增镜像相等、`daily` 集合相等(覆盖 R1b / R1c)、回填三类、生命周期(删除变退役)、同步投影双向(成员资格、退役与状态、分页、重放)、兼容视图、迁移台账回退、R2 回投输出对拍、对账幂等与实例身份参数不可覆盖、墓碑快照、62 源对拍、并发进度隔离、跨版本实测。
- **契约与文档**:`docs/contracts/archive_sync.md`(接收端能力参数、按请求投影、sources 载荷代次、退役语义);`AGENTS.md` 抓取器一行(「加源 = 硬化 preset fetcher」→「加策展源 = 目录文件一条;代码只写站点适配器」);`docs/architecture/fetchers-and-sources.md`、`content-model.md`、`reader-distribution.md`、`archive-sync.md`;`docs/sources/curation_policy.md`、`node_audit_playbook.md`;`decisions.md` 第 318 行(双路径 preset 类 + config 源)与第 333 行(合集存代码注册表)标为被取代并加新决策行。
- **内网 `master`**:`origin/master` 相对 `main` 在 `src/fetchers/` 只差 `base.py`(3 增 1 删)与 `web_content/legacy_backend.py`(2 增),没有内网独有的抓取器、合集或播客目录;R0 / R3a 改 `base.py` 时 tag 同步会冲突,按 `INTRANET_DELTA.md` 处理。

## 8. 拍板项

| # | 问题 | 选项(推荐在前) |
|---|---|---|
| E1 | 源实体落在哪 | **扩 `source_configs` 加列,按消费语义审计 94 处**(检视方同意)/ 新建 `sources` 表(权限、同步、生命周期审计一样免不了,过渡期两张表都表达「源」) |
| E2 | 绑定基数 | **行上 0..1,先把隐式绑定写实;出现「一源两条抓取通道」再拆表** / 现在就建 `source_bindings` 0..N |
| E3 | 目录行怎么到内网 | **内网声明能力、外网按请求投影,内网不本地写公共源行**(R1 稿的「每个节点本地对账」已撤回:违背外网唯一写者,并会触发接管与停用副作用)/ 不下发目录行,内网长期按注册表兜底 |
| E4 | 字段归属 | **目录拥有静态身份 / 呈现 / 策展 / 准入 / `daily` / 静态参数;实例身份参数不可覆盖;运维字段归库;播客并入前先出差异报告并存前像** / 只插不改(播客现状) |
| E5 | B 档 15 个 | **保留为站点适配器** / 给模板加声明式清洗参数后收为纯数据 |
| E6 | 适配器 id | **新命名空间 `site_*` + 别名表**,迁移所有引用旧 id 的绑定,历史运行记录保留原值 / 沿用旧源 id(适配器与源同名,易混) |
| E7 | 顺序 | **R0 → R1a → R1b → R1c → R4 → #262 波 1 → R2 → R3a → R3b** / 先做完 R1–R4 与 R2 / R3 再做推送 |

#262 方案的 D6(源目录数据化做到哪)与 D7(转正的事实来源)由本稿 E1–E4 细化;若采纳本稿,D6 / D7 随之定。

## 9. 风险

- **R1c 审计面与生命周期**:94 处读 + 构造与触发器;删除变退役改变了运维面「删除」的含义。缓解:每处先写钉子测试再改;R1c 合入前在生产库副本上做前后 diff;运维面「删除」按钮文案与确认框同步改。
- **同步投影**:同一 revision 对不同接收端有不同载荷,投影必须是确定性的纯函数,且整轮固定;切换投影须清 checkpoint 与逐行记账后重放;过滤不得破坏分页契约。缓解:双向跨版本实测;投影单测覆盖「退役 → tombstone(含状态)」「转正后 legacy 成员资格不变」「状态 fetcher_id 映射」「先过滤后分页、无空的非终页」「切换投影后重放不被逐行记账跳过」。
- **性能**:注册表是进程内常量,改读库后热路径可能 N+1;缓存失效时机见 §5。
- **抓取稳定性**:R3a 一次切换 62 个线上节点的注册与执行路径。缓解:全量对拍、R3a 只换机制不删类、R3b 分批;前后对比一周每源入库数。
- **播客语义变化**:从「只插不改」到「以文件为准」。缓解:差异报告逐条拍板、实例身份参数不可覆盖、前像可恢复。
- **测试搬迁量大**:保留行为断言,不只是替换类名。

## 10. 不做

- 不消灭站点适配器代码;不把正文清洗正则写进数据文件(E5)。
- 不把策展源的事实来源放进数据库 + 管理面 CRUD:失去 PR 审阅、`git` 即审计、开发机与新部署开箱一致。
- 不改 `source_configs` 表名、不改 `source_id` 取值、不迁历史条目 id、不改写历史运行记录。
- 不做 0..N 绑定(E2 触发条件出现前)。
- 内网不本地对账目录文件(E3)。

## 11. 检视

**R1(2026-11-05)**:检视方 Codex CLI,模型 `gpt-6-astra`,`model_reasoning_effort=ultra`,只读沙箱;实现方 Claude Code(Opus 5.5)。检视方复核了反射分档、AST 统计与 `master` 差异,未跑 pytest。结论:方向成立,R1 稿不能直接作为实现任务书。

| # | 级 | 问题 | 表态与修订 |
|---|---|---|---|
| F1 | P1 | R1 不能持续保证「每篇文章都有源行」:删源仍物理删除,另有直写路径 | 接受:不变量覆盖生命周期(§1.3-1),被引用的源改退役,写入闸覆盖全部写入路径 |
| F2 | P1 | 统一回填 legacy 行会解除孤儿 `user_rss_*` 的外发保护 | 接受:回填分三类,私有孤儿隔离;前缀保护保留(§5、§6 R1c) |
| F3 | P1 | 内网本地建目录行会被首次同步准备停用,并被文章流接管删状态 | 接受,与 F6 合并为 E3 改形:内网不本地写公共源行(§3.4) |
| F4 | P1 | 目录全量覆盖绑定参数会改变已收养播客的条目 id | 接受:实例身份参数不可覆盖(§1.1、§3.2) |
| F5 | P1 | R2 删类身份后缺少可独立运行的兼容阶段;Pipeline 在 `fetch()` 前读进度键 | 接受:R2 只迁描述性身份,R3a 原子切换注册键与执行上下文(§6) |
| F6 | P2 | 混版本协议不完整:载荷代次、空 payload 变 tombstone、反向混部 | 接受:接收端能力 + 按请求投影 + 载荷代次重拉(§3.4、§6.1);补充:旧内网收不到新目录源与今天新增预设类时相同,非新回归 |
| F7 | P2 | 空 `fetcher_id` 今天会被推导成模板,不是「无绑定」 | 接受:R1b 先写实隐式绑定 |
| F8 | P2 | 列出状态不等价于现有可见性规则 | 接受:资格与展示分算,保留隐藏已订阅的退订入口,`owner_username` 不授权,补消费方清单(§4) |
| F9 | P2 | Gemma 不是「登记基类即可」 | 接受:Devsite 模板参数化;参数分绑定参数与可覆盖参数(§1.1、§2.2) |
| F10 | P2 | `site_*` 改名不免费;两张记录表的 `fetcher_id` 语义不同 | 接受:别名表、迁移非目录绑定、字段语义表(§0-4、§6 R3a) |
| F11 | P2 | 每日任务与 `/api/fetchers` 的新枚举会改变范围 | 接受:每日范围作为独立政策(`daily`),R1c 投影与旧集合相等 |

事实核对更正已并入正文:94 处(不是约 93,且只有一部分依赖缺行语义);`_apply_sources` 接管时 `preserve_result=True` 保留旧结果、转 pending;R0 的 B7 / B8 有可见变化。

**R2 复检(最后一轮)**:同一会话 resume,`gpt-6-astra` / `ultra` / 只读。结论:F2、F3、F4、F7–F11 与事实更正判「够」;F1、F5、F6 判「不够」;新增 F12(P1)、F13(P3)。按轮次封顶,以下修订按检视方给出的形状直接落实,未再开第三轮。

| # | 级 | 检视方意见 | 修订 |
|---|---|---|---|
| F1 | P1 余项 | 严格不变量的启用时机、兼容视图与降级路径未定 | 内网只在协商到 `catalog-v1` 且 sources 全量重放完成后启用严格不变量;此前用只读兼容视图(§1.3-1、§3.4-4) |
| F5 | P1 余项 | R2 不能只保留 `source_id`,执行路径还读 `name` 等 | R2 注册时把目录值回投到类属性,输出逐条对拍(§2.2、§6 R2) |
| F6 | P2 余项 | 只清 checkpoint 不够;注册表兜底会随 R2 / R3 消失;legacy 成员资格要稳定;退役须协调状态流;过滤不得破坏分页;presence 不能直接复用 | 两种有效投影整轮固定并回显记账;切换时同事务清 checkpoint 与逐行记账后重放;兼容视图 = 源行 + 本地目录文件 + 虚拟占位;`legacy_export` 持久列;legacy 退役连带状态 tombstone;先过滤后分页;presence 不扩展(§3.4) |
| F12 | P1(新) | 按 `origin` 删生成行不是安全回退,删除触发器还会连带删已有状态并发 tombstone | 正常回退 = 保留 R1c 数据与兼容导出层、开关切回 `compat`;硬回退只按迁移台账精确 id,先保住已有状态并处理记账(§6 R1c) |
| F13 | P3 | 仓内规则两处口径:「未知键安全」要限定范围(candidate evidence 拒绝未知字段);#218 是「须先显式关开关」而非「默认关」 | 已改 `AGENTS.md` 硬规则 12 与 `docs/dev/workflow.md` §0;本稿 §6.1 #262 行同步改 |

## 12. 实施记录

**拍板(2026-11-06)**:用户「拍板项按你的推荐做」——E1 扩表、E2 行上 0..1、E3 接收端声明能力 + 按请求投影、E4 字段归属、E5 B 档保留适配器、E6 `site_*` + 别名表、E7 顺序 R0 → R1 → R4 → #262 波 1 → R2 → R3。

**首个 PR(#262 波 0 + R0 + R1b + R1c + R4 最小闭环 + 波 1)已落地的形状**:

- R1b:迁移 `c5e1a7b3d9f2` 写实空 `fetcher_id`(跳过同步行);新建与改类型时写实默认绑定;`resolve_source_fetcher_id` 对 `agent / legacy / system` 来路与退役行不再按 `source_type` 推导。
- R1c:迁移 `d7f3b9e2a614` 加列与两张表(身份键、迁移台账);外网启动对账 `services/source_rows.py`(镜像行 `source_type=preset`、系统行、墓碑、播客目录来路、孤儿回填三类);写入闸在 `DatabaseStorage.save` 与日报直写里同事务补建;被引用的配置源删除改退役;配置源列表与改删入口排除镜像 / 系统 / 回填 / 临时源;读法开关 `[sources] model = compat | full`(默认 compat)。
- R1a:新服务 `services/source_catalog.py`(`SourceView` + 批量 `describe`);本 PR 只把形态判定收成 `api.sources.row_shape`(显式列 > 绑定类 > 类型)并改了 7 个消费方,其余消费方迁移随 R2。
- R4 最小闭环:目录条目带 `origin` / `listing`;临时源 `unlisted`(发现页与批量订阅不列,源栏归「收录」组);退役行与 `user_rss_` 孤儿只对订阅者可见。
- 波 1 见 [#262 方案](./issue-262-agent-ingest-sources.md) §10。

**相对 §3.4 / §6.1 的三处简化**(更简单且等价,均有测试钉住):

1. **legacy 投影不过滤、发 tombstone**:不属于 `legacy_export` 的行与退役行都输出 tombstone,不从页里剔除。旧接收端对本地不存在的源应用 tombstone 是空操作,对已有行等同于重构前的物理删除;于是 §3.4-3 的「先过滤后分页」不再需要,分页契约原样不变。
2. **注入通道建的源 `legacy_export=False`**:旧内网收不到这些行,条目照常同步,表现与改动前的「导入源」完全相同。§6.1 #262 行所说的「推送开关须等内网升级」因此不再必要:推送以令牌签发为门,不另设开关。
3. **回显核对以实际为准**:接收端不再要求「生产者广播能力就必须回显 catalog-v1」,只要求整轮内回显一致;回显随 checkpoint 记账,`v2_source_catalog_ready_at` 只在 sources 流确实按 catalog-v1 完成时置上。

**R1c 无可见变化的对拍**(`tests/test_source_model.py`):对账前后阅读器目录逐项相同;节点目录、个人早报角色 / 形态、事件归并源元数据逐源相同。预期差异两处:只在 articles 里出现的社交源有了平台 `x` 与 𝕏 图标;主人不可考的 `user_rss_` 孤儿只对订阅者可见。

**已知回退边界**:外网回滚到 R1c 之前的版本时,旧代码会把镜像行、回填行、系统行当成普通配置行——配置源列表多出这些行,墓碑源可能在旧版发现页出现。为了不错判形态,镜像行的 `source_type` 对 X 预设记 `x_timeline`、其余记 `preset`(旧代码「配置行优先、只按类型判形态」也判对,`preset` 不被任何按类型枚举的旧入口执行;测试钉住)。正常回退仍按 F12:保留数据、`[sources] model` 切回 compat,不回滚代码版本。

**跨版本实测(2026-11-06,§6.1 要求)**:`v3.67.4` 检出到临时 worktree,与本分支互为生产端 / 接收端,在全新库上导入真实导出页(脚本在会话草稿区,未入库):

| 组合 | sources 页 | 终态 |
|---|---|---|
| 旧 → 旧(基线) | 1 upsert | 1 个源(`rss_admin`)、2 条状态 |
| 新外网 → 旧内网(legacy) | 1 upsert + 72 tombstone(镜像 / 回填 / 系统 / 墓碑 / 临时源) | 与基线逐项相同;72 条 tombstone 应用删除数 0 |
| 旧外网 → 新内网 | 1 upsert(无回显 = legacy) | 与基线逐项相同 |
| 新 → 新(catalog-v1) | 73 upsert,回显 `catalog-v1` | 73 个源(含 `manual` 回填、`web_pushed` 临时源、`x_openrouter` 墓碑、`dorami_daily_brief` 系统行) |

接收端的 tombstone 应用代码(`_apply_tombstones`)本 PR 未改,新旧接收端对退役 tombstone 的处理相同。

**真实库副本对拍(2026-11-06)**:本机 dev 库副本(43 个配置行、7194 篇文章、74 个来源 id)上同时起 `v3.67.4`(8090)与本分支(8089),逐项比较——阅读器目录(管理员与读者各 99 个源)、节点目录(106 个)、配置源列表(43 个,镜像行未混入)、合集、按形态的文章列表、事件榜、源健康、OPML、`/api/feed/articles` 全部一致;发现并修复一处:墓碑与「只在订阅里出现」的回填行混进了默认订阅候选与合法域(`f6164125`)。第二次启动对账全部 `unchanged`。端到端推送(未登记 X 账号 / 预设 handle / 已知站点前缀 / 陌生博客 / 播客单集 / 跨源同链接)回执与读者目录均符合契约。

**实现检视 R1(2026-11-06)**:检视方 Codex CLI,`gpt-6-astra`,`model_reasoning_effort=ultra`,只读沙箱;实现方 Claude Code(Opus 5.5)。12 条全部接受,无分歧(表态与修复清单见 `.review/response-impl-r1.md`,未入库)。

| # | 级 | 问题 | 修订 |
|---|---|---|---|
| 1 | P1 | 系统行 / 回填行有了源级形态,绕过榜单对 daily_brief / release 的内容类型排除 | 榜单与文章列表的源级形态表跳过 `system / legacy / agent` 来路与退役行(对拍时另发现墓碑行同样问题,一并修) |
| 2 | P1 | 预检看到新外网、实际整轮 legacy 时,顶层仍记 catalog-v1,再升级不重放 | 改为「未就绪即重放」:就绪只认实际按 catalog-v1 完成的 sources 流 |
| 3 | P1 | 跨源 URL 去重能探出私有条目 id 并吞掉公开推送 | 去重只在可公开交付的条目里查 |
| 4 | P1 | feed 规范化删掉 query,不同 feed 串源 | 统一用保留 query 的 `canonical_feed_url` |
| 5 | P1 | 旧 handle 被他人接手时按 handle 认错源 | 给了 user_id 且与命中源登记的不同 → 建独立源 |
| 6 | P1 | 收养播客的 `entry_id_namespace` 与同源异公式条目会重复入库 | id 前缀沿用 namespace;同源同链接返回 duplicate |
| 7 | P1 | 删除改退役后公共播客仍可采集入库 | 采集入口先拒退役源;存储层新增 `rejected_retired` |
| 8 | P2 | 墓碑进默认订阅 | `f6164125`(对拍先发现) |
| 9 | P2 | source-builder 现有 id 集合在有配置行时抛错 | 用查询返回的字符串集合 |
| 10 | P2 | 改类型后显式形态 / 平台残留 | 类型或绑定变化时清空派生字段 |
| 11 | P2 | 白名单扩展被丢 | 模型无字段的键走 `extra_extensions`,播客 episode / season 写入 |
| 12 | P2 | 坏扩展值整批 500、漏审计 | 按 kind 校验规整,单条拒收,审计照常 |

**实现检视复检(最后一轮)**:同一会话 resume,`gpt-6-astra` / `ultra` / 只读。#1、#3、#4、#7–#11 判「够」;#2、#5、#6、#12 有余项,另报新 P1 一条。均已核实,按检视方给出的形状直接修,未开第三轮:

| # | 余项 | 修订 |
|---|---|---|
| 2 | 已就绪后又出现「实际 legacy」一轮,就绪不撤销 | sources 流实际按 legacy 完成即撤销 `v2_source_catalog_ready_at` |
| 5 | 同批里同 handle、不同 user_id 被并进一个新源 | 批内已规划的临时源同样核对稳定 user_id;新 id 避开同批已规划 id |
| 6 | 无 GUID 播客先推后抓仍落两行 | 播客条目 id 与 `podcast_rss_fetcher` 同序取 guid → 单集链接;两样都没有拒收(契约写明「有 guid 就带上」) |
| 12 | `1e309` 解析成 inf 后 `int()` 溢出、整批异常 | 数值扩展检查有限性与上限;路由兜底 `OverflowError` |
| 新 P1 | 同源 URL 去重把共用节目页链接的不同单集并成一条 | 按链接去重只用于文章与社交帖,播客单集只认单集身份 |

