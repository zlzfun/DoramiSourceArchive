# issue #263 Archive Sync 节点不分角色:按身份合并、新者胜、普通开关

> 状态:进行中(2026-11-10 方向重估并由用户拍板三问后重写;待 Codex 方案检视 → 实现)。分支 `feat/issue-263-sync-symmetric`,开发版 3.68.0-alpha(不 bump)。
> 相关:[issue #263](https://github.com/zlzfun/DoramiSourceArchive/issues/263) · 契约 [`../contracts/archive_sync.md`](../contracts/archive_sync.md) · 架构 [`../architecture/archive-sync.md`](../architecture/archive-sync.md) · 前波 [#261 节点同步控制台](../archive/issue-261-sync-console.md)(归档)· 并行 [#262 源模型重构](issue-262-source-model-refactor.md)(不变量 6 由本波改写)
> 实现方:Claude Code(Fable 5.2)· 检视方:Codex(见 §11)
> 行号按 `efc8b32e`(main,2026-11-10)核对。第一稿(保留按源归属 + 原产地转发)已作废,演变见 §13。

## 0. 结论

用户口径(§1)下的规则只有四条:

1. **节点没有模式**。拉取别人不改变本机能做什么;采集、分析都是普通开关,随时可改可撤。
2. **文章按 id 合并,修订号新者胜**。无论同步还是抓取,得到一篇已在库中的文章就按规则更新,不问它来自哪台。
3. **同步建出来的源默认关采集、关分析**。管理员想自己采就打开。
4. **拉来的也导出**;**不再做「上游没有就删」**。

机制上做三件事:**拆**整机 consumer 模式与按源归属(`collection_authority_id`);把逐行的同步印记**降级**为内部簿记(只表示「这一行的当前版本是同步写进来的」,§3.2);给导出**加本机时钟**让拉来的行也能分页转发(§3.4)。本波只保证**单上游的链式转发**(A→B→C);互拉与多来源不在承诺内。外网(不拉任何人的节点)行为与线格式不变(§4)。

## 1. 拍板记录

| 时间 | 口径 |
|---|---|
| 2026-11-05 | 采集与分析必须和同步解耦;拉来的也应导出;「被拉取」视图不做;出网约束不变 |
| 2026-11-10 | 没有「判断某个源 / 某篇文章来自哪个 server」的需求;无论同步还是抓取,文章已在库中就更新 |
| 2026-11-10 | ① 存在时谁赢:**修订号新者胜**;② 付费的活不做两遍:**按源的普通开关,同步建出的源默认关采集、关分析**;③ 删除:**放弃「上游没有就删」的清理**;④ 外网与内网由用户人为尽量同批次更新 |

2026-11-05 的「同源上游有即归上游」「单一来源」「导出带原产地」三条随方向重估作废(issue 评论已标注)。

## 2. 规则一览

| 对象 | 身份 | 同步到达时 | 本机能否处理 |
|---|---|---|---|
| 文章 | `id`(= 源 id + 条目 guid / 链接哈希,与节点无关) | 库里没有 → 写入;库里是同步写入的版本 → 更新;库里是**本机写入的版本** → 来者修订号严格更新才替换,否则保留本机版本 | 当前版本是本机写入的 → 按本机各自既有的开关处理(源的分析开关、自动标题翻译、论文全文、播客阶段开关);当前版本是同步写入的 → 等上游的结果,不重复付费 |
| 分析 | `article_id` | 跟随文章:文章当前版本来自同步 → 应用;文章是本机版本 → 跳过 | 同上 |
| 源 | `source_id` | 没有 → 建行,**分析关**,不编入任何采集任务(= 采集关);已有 → 只更新目录字段,不碰本机的分析开关与采集绑定;**本机在用的源**不接受上游的删除、退役与停用 | 分析开关与绑定是本机的;采集由采集任务点名或手动运行决定 |
| 源状态 | `source_id` | 本机自己跑过且不比来者旧 → 保留本机的;否则应用 | 本机运行即改写为本机状态 |
| 图片 / 播客产物 | 链接哈希 / 产物 id | 同今天;撞上本机自己产出的播客文稿 → 保留本机的,跳过 | — |
| 事件 | 事件 id(本机自增) | 本机没有自己的事件 → 同今天;本机有自己的事件 → 整条 events 流不拉 | 用着上游事件的节点不做本地归并(§3.6) |
| 删除 | — | 删除标记只删「当前版本由同步写入」的行;本机版本不动。整流全量后不再核对存在性、不再清理 | — |

「本机在用的源」= 存在无印记的本机源状态(本机跑过),或被任一采集任务点名。

抓取路径不变:抓到已在库的文章仍只补正文 / 修占位标题 / 合并播客元数据,不改写已归档内容(忠实归档);这类写入会让该行成为「本机版本」。

**输入来源与结果**(本波只承诺单上游,下表是统一入口 `_accepted_remote_rows` 与逐流入口对三类已有行的处理):

| 库里已有的行 | 来自当前上游的行到达 |
|---|---|
| 印记 = 当前上游 | 接收修订更新才应用(既有去重),否则跳过 |
| 印记为空(本机版本) | 文章按「新者胜」;分析 / 播客 / 事件成员跟随文章被跳过;源状态按时间;媒体照今天接管 |
| 印记 = 另一个上游 | 整页拒绝(与今天相同;一台节点只配一个上游) |

## 3. 机制

### 3.1 拆掉的

- **整机 consumer 模式**:删 `remote_sync:v2_consumer_mode` KV、`activate_v2_consumer_mode` 与三处激活(`api/routers/remote_sync.py:173`、`services/remote_sync.py:1760` / `:1770-1782`)、`v2_consumer_mode_active` 的全部判定。
- **按源归属**:`SourceConfigRecord.collection_authority_id` 不再写入,迁移清空(列保留、恒为空,后续波次再删列)。`_apply_sources` 不再「接管」:不改归属、不改写同源文章、不清空 `fetcher_id` / `params_json`。`_apply_articles` / `_apply_source_states` 不再顺手给源盖章。
- **源级策略函数** `sync_consumer_policy.local_source_operation_allowed`:**保留函数与约 30 处调用点**,只去掉里面的 consumer 模式与源归属两个分支。公共源恒为真;私有 RSS 的规则(源须启用、功能总闸须开、凭据源与关了分析的源不经 MaaS,`sync_consumer_policy.py:137-157`)原样留下,并从「只在 consumer 模式下」变为对所有节点生效。采集运行路径的清理分支(私有源被删 / 退役、播客撤销)不动。
- **首次同步的破坏性准备**:`prepare_transaction_revision_consumer` 不再把本机全部公共源置为停用、不再删本机源状态(`services/remote_sync.py:411-435`);重基线只把「该上游写入的实体状态」的接收修订置 0(§3.4),不删行。
- **整流全量后的存在性核对与清理**:拉取循环不再调 `full_authority_stale_identities` / presence / `finalize_full_authority_stream`(`services/remote_sync.py:1407-1445`),两个函数删除。生产端的 presence 端点保留给旧版本接收端。

### 3.2 留下并降级的:逐行同步印记

`ArticleRecord.analysis_authority_id`、`ArticleAnalysisRecord.authority_id`、`SourceStateRecord.authority_id`、`MediaAssetRecord.sync_authority_id`、播客三表与 `EventRecord` 的 `authority_id` **保留**,含义从「这行归上游、本机不得碰」改为「**这行的当前版本是同步写进来的**」。值仍是上一跳的 id,但没有任何规则、界面或文案依赖「是哪一台」。

不能连它一起拆的两个原因(都是核对代码后才确认的):

1. **本机批注会推进文章修订号**。`models/db.py:269` 的 ORM 监听器在任何归档字段经 ORM 变化时推进 `archive_updated_at`,其中包括 `extensions_json`;标题译文回执等批注走 ORM,会推进(正文译文走 Core UPDATE,`reader_ai.py:209`,不推进)。印记非空的行不推进(`:280`)。如果没有印记,内网给一篇上游文章写一次标题回执,它在内网的修订号就比外网新,此后外网对这篇的更新都会被「新者胜」挡在外面。印记正好区分了「本机写了一版」和「本机只是在上游的版本上做了批注」。
2. **同步来的文章不重复付费**。文章流先到、分析流晚到;仅凭源开关,空档里本机会把未分析的文章送去付费分析。分析入队、认领、播客处理、标题翻译、论文全文今天都看文章印记(`article_analysis.py:592`、`podcast_processing.py:258`、`title_translation.py:98`、`paper_fulltext.py:40`、`event_assignment.py:484`),保留它就没有空档问题,源开关只需管本机自己写入的文章。

印记的两个转换:

- **本机写入 → 清印记**:抓取 / 收录路径对已有行做出改动时(`DatabaseStorage.save_with_outcome` 的补正文、修元数据、播客元数据合并三个分支)清掉文章印记与其分析行印记,该行成为本机版本;本机运行写源状态时(`api/app.py` `mark_source_state_started` / `_finished`)清源状态印记。
- **同步写入 → 盖印记**:§3.3。

### 3.3 导入规则(逐流)

`import_page` 的页级原子性、校验与流顺序不变。变化:

- **articles**(`_apply_articles`):库里没有 → 写入并盖印记;库里有且已盖印记 → 照今天更新;库里有且**无印记**(本机版本)→ 比 `archive_updated_at`,来者**严格更新**才接管(盖印记、`_supersede_local_analysis`),否则跳过并计 `skipped`。相等不接管。导入时清空 `fetch_run_id` / `job_id` / `job_run_id` / `source_group_id`:它们是产生节点的本地整数关联,原样照抄会和本机的运行串号(`api/articles_view.py:276` 按整数筛)。`preserve_local_translations` 只放宽「印记须相等」这一条前提,身份、正文 / 社交输入指纹、缓存版本校验不变(它本来就不保留标题回执)。源行存在与否不再影响文章导入。
- **修订号比较规则**(文章与源状态共用):两侧都解析为时间点;无时区的值按本机时区解释,带时区的值直接换算,统一到 UTC 比较;某一侧解析失败则视为最旧;相等视为「不更新」。
- **analyses**(`_apply_analyses`):文章缺失、或文章是本机版本 → 跳过并计数(今天是整页失败)。文章印记匹配 → 照今天应用;`content_hash` 不符仍整页失败(同一快照内文章先到,不符即数据错误)。
- **sources**(`_apply_sources`):没有 → 建行,`ai_analysis_enabled = False`、`fetcher_id` / `params_json` 留空,其余字段照来者(含 `is_active`,它是早报范围与 readiness 的目录字段,`personal_digest.py:361 / 428`,不能动);已有 → 覆盖目录字段,不碰 `ai_analysis_enabled` 与绑定;已有且**本机在用** → 另外不接受来者的 `is_active` 与 `retired_at`。读者自定源同 id 仍整页失败。
- **source_states**(`_apply_source_states`):本机状态行无印记且 `updated_at` 不早于来者 → 跳过;否则应用并盖印记。覆盖已有行时不清空本机游标字段(只在新建时置空)。
- **media**:不变。
- **podcast_texts / podcast_audio**:单集文章是本机版本、文稿槽位已被本机产物占用、或 `(episode_id, kind, version)` 撞上本机产物 → 跳过该行并计数(今天是整页失败);依赖的讲稿被跳过时对应音频行同样跳过;其余不变。
- **events**:不变(成员文章不是该上游写入的版本 → 今天就已跳过并计数)。
- **tombstone**(`_apply_tombstones`):文章 / 分析 / 源状态规则不变——只删印记等于该上游的行。sources:本机在用 → 跳过;否则本机有该源文章 → 置退役,没有 → 删行。播客槽位属于本机 → 跳过而非失败。目标本来就不存在的 tombstone 算成功应用。
- **记账**(`_save_remote_entity_states`):只给成功应用的行记账(含目标已不存在的 tombstone);因本机版本而跳过的行不记。今天连被跳过的行也改写,会把本机行的导出状态盖成远端 tombstone。
- **返回值**:`import_page` 增加「因本机版本而跳过」的身份集合与「接管了本机版本的文章数」,供拉取循环使用(下两条)。
- **二进制阶段**(`run_pull_v2`):只跳过上述集合里的行;因接收去重而跳过的行仍进入复用 / 下载(覆盖「元数据已应用、文件未装好」的失败重试)。今天它遍历原始行,被跳过的播客音频会在这里报「belongs to another authority」。
- **依赖流补回**:本轮 articles 流里只要接管过本机版本,同一轮内 analyses / events / podcast_texts / podcast_audio 以 `since=""` 整流重放。之前因「文章是本机版本」被跳过的依赖行没有记账,重放时补上;已应用的行靠接收去重跳过。接管只在两台同采一个源时发生。

### 3.4 导出与转发:本机时钟

状态表今天一列两用:本机行的 `revision` 是本机时钟(给下游翻页),同步写入的行的 `revision` 是上游修订(判断「应用过没有」与事件成员先后)。转发需要把两个职责分开:

- **接收修订**:`revision`(同步写入的行)语义不变。
- **导出修订**:新增 `local_revision INTEGER NOT NULL DEFAULT 0` 与索引 `(stream, local_revision, identity)`,所有导出只看它。
- 本机行:状态表上加两个镜像触发器,`authority_id = ''` 的行插入或 `revision` 变化时令 `local_revision = revision`。现有约 20 处写状态的业务触发器不改。
- 同步写入的行:`_save_remote_entity_states` 每页推进一次 `archive_sync_clock`,本页成功应用的行 `local_revision = clock`(keyset 为 `(local_revision, identity)`)。
- **文件就绪也是一次发布**:同步写入的媒体(`pending_sync → cached`)与播客音频(`ready → published`)是先落元数据、后装文件,导出只认装好的。新增触发器:同步写入的 `media_assets` / `podcast_artifacts` 行状态变化时推进时钟并更新对应状态行的 `local_revision`(撤回、恢复同理)。否则下游若恰好在两个阶段之间拉取,文件装好后没有新修订通知它。
- **重放不删行**:单流重置(`sync_console.reset_stream`)、分析代次补拉、源投影切换、重基线今天都 DELETE 该上游的状态行;转发后那等于把导出索引一起删了。改为把这些行的 `revision` 置 0(之后任何来者都算更新),保留 `local_revision` 与 `operation`。
- 导出(`export_page`、`pending_state_counts`、`_media_references`、`_export_media_rows`):去掉 `authority_id == ""` 过滤,改按 `local_revision` 过滤与排序;行信封的 `revision` 填 `local_revision`;媒体行取 `max(媒体状态, 所引文章状态)` 的 `local_revision`。
- `_upsert_payload`、`_article_is_public`、`is_public_media_reference`、`is_public_podcast_audio_reference`、`authority_present_identities`:去掉所有「印记为空」条件,保留数据范围条件(非 `user_rss_`、无 `owner_username`、非凭据源、legacy 投影的 `legacy_export` / `retired_at`、分析 `content_hash` 失配 → tombstone)。读者数据、自定 RSS、凭据源照旧永不外传。
- 上游的 tombstone 到达后状态行为 tombstone、带本机时钟,自然转发。
- 触发器进统一的 DROP / CREATE 生成器,并按 `local_revision` 列是否存在门控(历史迁移会调用当前的安装器,沿用 `_event_schema_ready` 的做法)。

线格式:不新增任何键。不拉任何人的节点 `local_revision ≡ revision`,迁移不推进它的时钟,导出逐字节不变。

### 3.5 源的普通开关

- **采集**:公共源只有被编入启用的采集任务、或管理员手动运行时才会采。同步不会把源编入任何任务,所以同步建出的源天然不采——这就是「默认关采集」。`is_active` 不动:它同时决定个人早报的范围与 readiness,仍作为目录字段同步。
- **分析**:`ai_analysis_enabled`,含义与今天完全一致(评分分析及依赖它的播客处理)。同步建行时置 `False`,之后同步不再覆盖它。它只对**本机版本**的文章起作用;同步写入的文章不在本机分析(§7-2)。标题翻译、论文全文不归这个开关管,按各自既有的开关运行,且同样只对本机版本。
- **入口**:配置源已有编辑接口;预设镜像行今天没有任何开关入口(`_require_config_managed` 拒绝)。新增 `POST /api/admin/sources/{source_id}/analysis`(`{enabled}`,公共源行通用,入审计),源管理检视器加一个「本机分析」开关,样式与同页「在读者面隐藏」一致。
- **有上游的节点上新出现的源**:启动对账(`source_rows.reconcile_source_rows`,现改为每台都跑)与孤儿回填在「已与某上游建立 v2 同步」的节点上新建行时分析默认关,避免对账先于同步建行而漏掉默认值。`local_public_writes_allowed` 删除;`_reconcile_mirror` 对已有行只动目录字段的行为不变。
- 收录推送、新建公共源、签发收录令牌在任何节点都可用(`ingest_push.py:130 / 181 / 267`、`source_configs.py:73` 的拒绝删除)。

### 3.6 事件:一个节点一个 id 空间

`events.id` 是本机自增整数,同步时原样照抄;本地同 id 事件会让整页回滚(`_apply_events`,`archive_sync_v2.py:3173`)。本波不做全局身份,改为让两种事件**不同表共存**,由库里已有的事件决定:

| 库里的事件 | 拉取 events 流 | 本地事件归并 |
|---|---|---|
| 有本机事件(印记为空) | 跳过整条流 | 照常 |
| 只有上游事件 | 照常 | 不运行 |
| 都没有,且已接入带 events 能力的上游 | 照常 | 不运行 |
| 都没有,没有上游 | — | 照常 |

- 归并门控放进 `event_assignment.assign_pending`,覆盖分析周期末尾(`article_analysis.py:2970`)与 10 分钟兜底两个入口;管理员摘出(`event_board.py:1058`)只对本机事件开放,在用上游事件的节点上没有本机事件可操作。
- 跳过流:`run_pull_v2` 接受「本机跳过的流」参数,不请求、不推进其 checkpoint;控制台把它显示为未同步。
- 代价:用着上游事件的节点上,自己采的文章不进热点事件榜;先自己归并过、后来才接入上游的节点保留自己的事件、不收上游的。两种节点互换需要清空事件,本波不提供入口。
- taxonomy 仍由 `taxonomy:authority_id` 决定只读与否,`api/app.py:3034`、`services/taxonomy.py:1345`、`routers/taxonomy.py:42` 的 consumer 判定改为只看该 KV。

### 3.7 控制台与前端

- `sync_console.console_status` 去掉 `consumer_fence`,加 `legacy_import_locked`(v1 导入是否被锁)。
- `SyncConnect.jsx` 删「开始首次同步」确认框;`SyncPops.jsx` 定时弹层删警示与 danger 态;`SyncLink.jsx` 删「本地采集」行;`SyncDrawers.jsx` 的 v1 导入锁改读新字段;`SyncPage.jsx` 去 `fenceActive`。
- 源管理检视器加「本机分析」开关(§3.5)。读者面、移动壳无改动。

### 3.8 迁移(一个 Alembic revision,`heads` 口径)

1. `archive_sync_entity_states` 加 `local_revision` 与索引;本机行 `local_revision = revision`;**存在**同步写入的行时,这些行统一取 `clock + 1` 并把时钟 +1(没有这类行的节点时钟不动)。
2. `source_configs`:清空 `collection_authority_id`,并为这些行补本机实体状态(它们从此由本机导出)。
3. 同步写入的文章(印记非空)清空 `fetch_run_id` / `job_id` / `job_run_id` / `source_group_id`。
4. **接收节点**(存在 `remote_sync:v2_consumer_mode`,或迁移前有任一带归属印记的源)按旧围栏实际挡住的范围保持现状:
   - 全部公共源(`owner_username = ''` 且非 `user_rss_`)置 `ai_analysis_enabled = 0`——不止带印记的,旧围栏对接收节点上所有公共源的分析都挡着;
   - `fetcher_ids_json` 里含公共源的采集任务置停用(纯私有任务不动);
   - `daily_brief_enabled` 置 `false`;
   - 删 consumer KV。
5. 触发器在迁移内先整体 DROP、结束后按新定义重建。`downgrade` 只回退结构,不恢复印记与开关。

测试:为本波 head 新增升级 / 降级 / 再升级用例与 drift;`tests/test_migrations.py` 里针对历史 revision `d6a3f9c2e714` 的 downgrade 守卫(含 `consumer_marker` 分支)原样保留,它不经过本波迁移。

## 4. 部署顺序与混版本(硬规则 12)

用户已表态两端尽量同批次更新;下面是不同批次时的结果。

**1. 改了哪些跨节点契约**:线格式零变化(不加键、不加流、不加请求参数)。变化的是语义:导出范围(持有的全部公开数据)、接收端合并规则、接收端不再发 presence 请求、本机有自己的事件时不请求 events 流。出网请求只减不增。神灯流水线、IM 机器人、feed 不读同步流字段;`ANALYSIS_PAYLOAD_GENERATION` 不动。

**2. 窗口**

| 窗口 | 会怎样 | 结论 |
|---|---|---|
| 新外网 + 旧内网 | 外网没有同步写入的行,`local_revision ≡ revision`、时钟不被迁移推进,导出集合与字节都不变;旧内网照旧运行在 consumer 模式下 | 安全 |
| 旧外网 + 新内网 | 新内网按新规则合并、不再发 presence。源的 `is_active` 照旧同步,个人早报范围与 readiness 不变(readiness 靠源状态印记)。旧外网只会发 legacy 投影:退役 = tombstone,新内网对没在用的源按「有文章置退役、无文章删行」处理,与旧行为等效。迁移已停用内网含公共源的采集任务与日报定时,不会突然开始采集 | 安全 |
| 新中转节点 + 旧下游(未来形态) | 旧下游把转发来的行当成中转节点的行;presence 口径已同步放开,旧下游的全量清理不会误删转发行;文件就绪会推进导出修订,旧下游不会漏图 | 安全 |

**3. 不能双向兼容的点**:无。

**4. 回滚**:旧代码不认识本波的迁移版本,启动即判 incompatible(`storage/migrations.py:330`)。回滚 = 按 [`../ops/release-process.md`](../ops/release-process.md) 恢复升级前备份;不支持旧代码直接跑在升级后的库上。

**5. 验证**:旧 tag(v3.67.4)接收端 × 新生产端、新接收端 × 旧 tag 生产端各一次(首次全量 + 一次增量);三节点链 A→B→C 实测(§6)。

## 5. 上线说明(写进发版说明)

- 任意顺序;同批次最好。升级前照常备份,回滚靠备份。
- 内网升级时迁移会做三件保守的事:全部公共源的「本机分析」置关;含公共源的采集任务置为停用;公共日报定时置为关。它们今天在内网本来就被围栏挡着不生效。
- 升级后想让内网自己采某个源:把它编入采集任务并启用任务(或手动运行),需要本机分析就在源管理打开该源的「本机分析」。
- 升级后内网可以新建公共源、签发收录令牌。

## 6. 测试与验收

**pytest**
- 迁移:`local_revision` 回填;无同步写入行时时钟不动;源印记清空;同步写入文章的本地关联字段清空;接收节点矩阵(全部公共源分析关、含公共源的任务停用、纯私有任务不动、日报关、KV 删除);本波 head 的升级 / 降级 / 再升级;drift;历史 downgrade 守卫不变。
- 合并:同步写入的版本被更新;本机版本遇到更旧 / 相等 / 更新的来者;时间比较覆盖无时区、带时区、两者混合、坏值;接管时按指纹保留本机正文译文;本机抓取改动已盖印记的行后清印记;印记行上的 ORM 批注不推进修订号;导入清空本地关联字段(两节点运行 id 相同时按运行筛选不串)。
- 逐流跳过不卡流(都走完整拉取循环):分析、播客文稿、播客音频(含二进制阶段)、`(episode, kind, version)` 撞本机产物;源状态本机较新者保留;tombstone 不删本机版本、目标不存在也记账。
- 依赖补回:先因本机版本跳过分析 / 事件成员 / 文稿 / 音频,之后仅文章变化并被接管 → 同一轮整流重放补回。
- 源:同步建行分析默认关、`is_active` 照来者;更新不碰分析开关与绑定;本机在用的源不接受删除 / 退役 / 停用;没在用的源 tombstone 有文章置退役、无文章删行;旧生产端 legacy 投影的 tombstone;有上游的节点对账新建行分析默认关;`POST /api/admin/sources/{id}/analysis`;个人早报范围与 readiness 在迁移前后一致。
- 导出:中转节点导出含同步写入的行、按 `local_revision` 分页;不拉任何人的节点导出与改动前逐字节一致(固定样本对拍,含 manifest snapshot);媒体 / 音频 / presence 口径一致;私有与凭据源不外传。
- 文件就绪:C 在 B「元数据已应用、文件未装好」时拉取并推进 checkpoint,B 装好后 C 下一轮能拿到(媒体、播客音频各一例);撤回与恢复同理。
- 重放:单流重置、分析代次补拉、投影切换之后,中转节点对新下游的全量导出仍包含这些行;重放完成后内容正确。
- 三节点链(进程内三个 sink):B 拉 A、C 拉 B → C 有 A 与 B 的文章、分析、源;A 删一篇 → B、C 依次删除;B 自己采的源 C 也拿到;B 首次同步不停用本机源、不删本机数据;任何一步都没有 presence 请求。
- 事件:先有本机事件的节点接入上游 → events 流被跳过、其余流正常;用着上游事件的节点 `assign_pending` 不建事件(两个入口);已接入带 events 能力的上游但事件表还空的节点不建本地事件。
- 拆 consumer:首次同步与启用定时不再写 KV;有上游的节点可新建公共源、签发收录令牌、跑对账;taxonomy 只读仍由 KV 决定;私有 RSS 规则(停用源、功能总闸、凭据源)在非接收节点上同样生效。
- 跨版本实测两组(§4-5)。

**沙箱目检(用户)**:两个隔离栈,B 以真实库副本为底、C 空库拉 B。
1. C 的运维 → 节点同步:接入卡直接开始首次同步,无确认框;完成后流水线九站完成。
2. C 的阅读器能看到 B 的文章、图片、播客与分析结果;个人早报照常生成。
3. C 的源管理:同步来的源「本机分析」为关;打开一个源的开关并手动运行一次,新抓到的文章在 C 本机分析。
4. 在 B 新建一个 C 没有的源并抓一次 → C 再同步后出现该源与文章。
5. C 新建公共源、签发收录令牌均成功。

## 7. 需要用户知情的取舍(按此实现,若不认可请指出)

| # | 取舍 | 理由 |
|---|---|---|
| 1 | 逐行同步印记保留为内部簿记,没有全部拆掉 | §3.2 的两个原因;没有界面或文案依赖「来自哪台」 |
| 2 | 同步写入的文章不在本机分析,即使打开了该源的「本机分析」;开关只对本机自己抓到的文章生效 | 上游的分析结果会随同步到达;否则两边各付一次费。这是在「按源的普通开关」之上额外加的一条限制 |
| 3 | 事件二选一:用着上游事件的节点不做本地归并(自己采的文章不进热点事件榜);先有本机事件的节点不收上游事件 | 事件 id 是本机自增整数,两个来源同表必然相撞(§3.6) |
| 4 | 抓取遇到已在库的文章仍只补正文 / 修占位,不改写已归档内容 | 「忠实归档」是既有产品规则,本波不动 |
| 5 | 两台都采同一个源时,各自保留自己抓到的较新版本并各自负责分析 | 「新者胜」的直接结果;相等时不接管 |
| 6 | 迁移在接收节点上把全部公共源的分析置关、停用含公共源的采集任务、关掉日报定时 | 它们今天被围栏挡着本就不跑;静默恢复会让内网突然开始抓公网、调模型 |
| 7 | 本波只保证单上游的链式转发;互拉、多来源不承诺、不验证 | 逐行印记仍按「同一个上游」校验;taxonomy 治理与同步控制台也都是单上游 |
| 8 | 「同步建出的源默认关采集」落为「不编入任何采集任务」,不改源的启用状态 | 启用状态同时决定个人早报的范围,关掉会把同步来的源全部踢出早报 |
| 9 | 回滚靠备份,不支持旧代码直接跑在升级后的库上 | 仓内既有规则 |

## 8. 风险与观察

- `archive_updated_at` 是无时区的本机墙上时钟(`models/db.py:255`)。「新者胜」只在本机版本与来者之间比较;两台时区口径不一致时,同采一个源的胜负会偏向时区靠前的一方。只同步不自采的节点不受影响。
- 本机版本一旦比上游新,就不再接收上游对这篇的更新与分析,直到上游出现更新的版本并触发接管与依赖流重放。只发生在两台都采同一个源时。
- 依赖流整流重放的代价(分析流全量下载)只在发生接管的那一轮付出;若某节点长期与上游同采大量源,重放会变频繁——观察 `skipped` 与接管计数。
- 私有 RSS 规则从「只在 consumer 模式下」变为所有节点生效:外网上被停用的自定源、功能总闸关闭时,其文章不再被采集与分析。外网无真实读者,预期无可见变化。
- 内网恢复「可以自己采」后产生的失败运行记录、源状态会随导出流向它的下游(今天没有下游)。

## 9. 不做

- 事件全局身份(本机事件与上游事件合并)、「用上游 / 自己归并」互换的入口;互拉与多来源;hub 转发候选证据;多上游的控制台界面。
- 删除 `collection_authority_id` 等已废弃列(后续波次,避免本波再碰 SQLite 表重建)。
- 「被拉取」视图(2026-11-05 用户决定)。
- 抓取路径的覆盖语义(§7-4)。

## 10. 影响面

- 后端:`services/archive_sync_v2.py`、`services/remote_sync.py`、`services/sync_consumer_policy.py`(去 consumer 模式与源归属,留私有 RSS 规则与 v1 兼容判定)、`services/sync_console.py`、`services/source_rows.py`、`services/translation_sync.py`、`services/event_assignment.py`、`storage/archive_sync_revision.py`、`storage/impl/db_storage.py`、`models/db.py`、`alembic/versions/<new>.py`;consumer 判定的直接调用点:`api/app.py`、`api/routers/{remote_sync,archive_sync,source_configs,ingest_push,taxonomy,admin}.py`、`services/taxonomy.py`;只读核对不改:`services/personal_digest.py`。
- 前端:`components/admin/sync/{SyncConnect,SyncPops,SyncLink,SyncDrawers}.jsx`、`pages/SyncPage.jsx`、`FetchTab.jsx`(开关)、`api.js`。
- 文档:`contracts/archive_sync.md`、`architecture/archive-sync.md`、`architecture/overview.md`、`AGENTS.md` 架构表 Archive Sync 行、`ops/{aliyun-dual-node-deployment,configuration,taxonomy-v1-deployment,runbook}.md`、`reference/endpoints.md`、`plans/issue-262-source-model-refactor.md`(不变量 6 注)、`version-history` / `backlog` / `decisions`。
- 测试:`tests/test_sync_consumer_policy.py`(重写)、`test_archive_sync_v2.py`、`test_remote_sync.py`、`test_migrations.py`、`test_ingest_push.py`、`test_source_model.py`、`test_sync_console.py` 及引用源级围栏的其它用例;前端 `frontend/test/*.test.mjs`。

## 11. 检视

实现方 Claude Code(Fable 5.2),检视方 Codex(`gpt-6-astra`,effort ultra)。往返文件在 worktree `.review/`。

**方案检视 R1(2026-11-10)**:6 条 P1、8 条 P2,全部接受,方案据此改写。

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | P1 | 迁移关 `is_active` 会把同步来的源踢出个人早报(`personal_digest.py:361 / 428`) | 不动 `is_active`;默认关采集 = 不编入采集任务(§3.5) |
| 2 | P1 | 上游的源删除 / 退役会删掉或停掉本机在用的配置 | 「本机在用的源」不接受删除、退役、停用(§2、§3.3) |
| 3 | P1 | 只停定时归并挡不住其它入口,也解决不了已有事件相撞 | 事件二选一由库内已有事件决定,门控进 `assign_pending`(§3.6) |
| 4 | P1 | 音频行在导入阶段跳过后,二进制阶段仍会报错卡流 | `import_page` 返回因本机版本跳过的集合,二进制阶段跳过(§3.3) |
| 5 | P1 | 文件装好不推进导出修订,下游可能永久漏图、漏音频 | 状态变化触发器推进 `local_revision`(§3.4) |
| 6 | P1 | 不能承诺旧代码直接跑在新库上 | 撤回承诺;回滚靠备份(§4) |
| 7 | P2 | 重放删状态行会把导出索引一起删掉 | 改为接收修订置 0,不删行;不存在的目标 tombstone 也记账(§3.4、§3.3) |
| 8 | P2 | 印记仍按上游判定,不能宣称多来源成立 | 只承诺单上游链式转发;加三类输入结果表(§2、§7-7) |
| 9 | P2 | 时间比较缺少带时区历史值的规则;批注论据过宽 | 写明比较规则;修正论据;译文保留只放宽印记条件(§3.2、§3.3) |
| 10 | P2 | 因本机版本跳过的依赖行,文章后来被接管时补不回来 | 发生接管的那一轮整流重放四条依赖流(§3.3) |
| 11 | P2 | consumer 迁移与策略删除并非保持现状;私有 RSS 规则不能一并删 | 迁移矩阵按旧围栏实际范围;策略函数保留私有分支;清理分支不删(§3.1、§3.8) |
| 12 | P2 | 「本机分析」开关范围未与入口对齐;同步文章不分析是新增限制 | 开关含义与今天一致;限制作为明确取舍列入 §7-2 |
| 13 | P2 | 文章上的本地运行整数 id 被原样同步,恢复本机采集后会串号 | 导入与迁移清空四个本地关联字段(§3.3、§3.8) |
| 14 | P2 | 不能因新 head 删 KV 而削弱历史 downgrade 守卫;新触发器要按列门控 | 历史守卫保留;新增本波 head 的升降级测试;触发器门控(§3.4、§3.8) |

## 12. 实施记录

(待实现。)

## 13. 方向演变(备查)

- **第一稿(2026-11-09,已作废)**:保留按源归属,同源上游有即归上游;导出带原产地 `origin_authority_id`、防环、源管理页「远端」标。配套图解页 [`../design/issue-263-sync-symmetric/explainer-v1.html`](../design/issue-263-sync-symmetric/explainer-v1.html) 画的是这一稿,其中「归属今天怎么运作」「整机开关」两部分仍是对改动前现状的准确说明。
- **重估(2026-11-10)**:用户指出没有按 server 记归属的需求、方案太复杂,提出按身份合并。评估确认文章身份已是全局确定性 id;归属标记实际在替三件事把关(存在时谁赢、付费不重做、删除),三问由用户拍板(§1)。
- **核对中发现并改变设计的三件事**:① 文章修订号会被本机批注推进(§3.2-1),所以「新者胜」不能脱离逐行印记;② 事件 id 是本机自增整数(§3.6);③ 预设镜像行没有任何开关入口(§3.5)。
