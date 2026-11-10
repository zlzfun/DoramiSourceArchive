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

机制上做三件事:**拆**整机 consumer 模式与按源归属(`collection_authority_id`);把逐行的同步印记**降级**为内部簿记(只表示「这一行的当前版本是同步写进来的」,§3.2);给导出**加本机时钟**让拉来的行也能分页转发(§3.4)。外网(不拉任何人的节点)行为与线格式不变;两种混版本窗口都安全(§4)。

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
| 文章 | `id`(= 源 id + 条目 guid / 链接哈希,与节点无关) | 库里没有 → 写入;库里是同步写入的版本 → 更新;库里是**本机写入的版本** → 来者修订号严格更新才替换,否则保留本机版本 | 当前版本是本机写入的,且源的分析开关开着 → 本机分析 / 译标题 / 取全文 / 播客处理;当前版本是同步写入的 → 等上游的结果,不重复付费 |
| 分析 | `article_id` | 跟随文章:文章当前版本来自同步 → 应用;文章是本机版本 → 跳过 | 同上 |
| 源 | `source_id` | 没有 → 建行,**采集关、分析关**;已有 → 只更新目录字段,**不碰本机开关与采集绑定** | 开关与绑定是本机的:启停、分析开关、采集任务点名 |
| 源状态 | `source_id` | 本机自己跑过且更新 → 保留本机的;否则应用 | 本机运行即改写为本机状态 |
| 图片 / 播客产物 | 链接哈希 / 产物 id | 同今天;撞上本机自己产出的播客文稿槽位 → 保留本机的,跳过 | — |
| 事件 | 事件 id(本机自增) | 同今天 | **本机持有上游事件时不做本地归并**(§3.6) |
| 删除 | — | 删除标记只删「当前版本由同步写入」的行;本机版本不动。整流全量后不再核对存在性、不再清理 | — |

抓取路径不变:抓到已在库的文章仍只补正文 / 修占位标题 / 合并播客元数据,不改写已归档内容(忠实归档);这类写入会让该行成为「本机版本」。

## 3. 机制

### 3.1 拆掉的

- **整机 consumer 模式**:删 `remote_sync:v2_consumer_mode` KV、`activate_v2_consumer_mode` 与三处激活(`api/routers/remote_sync.py:173`、`services/remote_sync.py:1760` / `:1770-1782`)、`v2_consumer_mode_active` 的全部判定。
- **按源归属**:`SourceConfigRecord.collection_authority_id` 不再写入,迁移清空(列保留、恒为空,后续波次再删列)。`_apply_sources` 不再「接管」:不改归属、不改写同源文章、不清空 `fetcher_id` / `params_json`。`_apply_articles` / `_apply_source_states` 不再顺手给源盖章。
- **源级策略函数** `sync_consumer_policy.local_source_operation_allowed`:拆掉后对公共源恒为真,函数与约 30 处调用一并删除(各处保留的是文章级判断,见 §3.2)。运行路径里「采集途中被远端接管 → 作废本次采集」的清理分支(`api/app.py:3825 / 3888`)随之删除。
- **首次同步的破坏性准备**:`prepare_transaction_revision_consumer` 不再把本机全部公共源置为停用、不再删本机源状态(`services/remote_sync.py:411-435`);只保留对「该上游写入的实体状态」的重基线清理。
- **整流全量后的存在性核对与清理**:拉取循环不再调 `full_authority_stale_identities` / presence / `finalize_full_authority_stream`(`services/remote_sync.py:1407-1445`),两个函数删除。生产端的 presence 端点保留给旧版本接收端。

### 3.2 留下并降级的:逐行同步印记

`ArticleRecord.analysis_authority_id`、`ArticleAnalysisRecord.authority_id`、`SourceStateRecord.authority_id`、`MediaAssetRecord.sync_authority_id`、播客三表与 `EventRecord` 的 `authority_id` **保留**,含义从「这行归上游、本机不得碰」改为「**这行的当前版本是同步写进来的**」。值仍是上一跳的 id,但没有任何规则、界面或文案依赖「是哪一台」。

不能连它一起拆的两个原因(都是核对代码后才确认的):

1. **本机批注会推进文章修订号**。`models/db.py:269` 的 ORM 监听器在任何归档字段变化时推进 `archive_updated_at`,其中包括 `extensions_json`——读者触发的正文译文、标题译文回执都写在这里。印记非空的行不推进(`:280`)。如果没有印记,内网读者翻译一次,该文章在内网的修订号就比外网新,此后外网对这篇的更新(补全文、改标题)都会被「新者胜」挡在外面。印记正好区分了「本机写了一版」和「本机只是在上游的版本上做了批注」。
2. **同步来的文章不重复付费**。文章流先到、分析流晚到;仅凭源开关,空档里本机会把未分析的文章送去付费分析。分析入队、认领、播客处理、标题翻译、论文全文今天都看文章印记(`article_analysis.py:592`、`podcast_processing.py:258`、`title_translation.py:98`、`paper_fulltext.py:40`、`event_assignment.py:484`),保留它就没有空档问题,源开关只需管本机自己写入的文章。

印记的两个转换:

- **本机写入 → 清印记**:抓取 / 收录路径对已有行做出改动时(`DatabaseStorage.save_with_outcome` 的补正文、修元数据、播客元数据合并三个分支)清掉文章印记与其分析行印记,该行成为本机版本;本机运行写源状态时(`api/app.py` `mark_source_state_started` / `_finished`)清源状态印记。
- **同步写入 → 盖印记**:§3.3。

### 3.3 导入规则(逐流)

`import_page` 的页级原子性、校验与流顺序不变。变化:

- **articles**(`_apply_articles`):库里没有 → 写入并盖印记;库里有且已盖印记 → 照今天更新;库里有且**无印记**(本机版本)→ 比 `archive_updated_at`(解析为时间比较,解析失败回退字符串比较),来者**严格更新**才接管(盖印记、`_supersede_local_analysis`),否则跳过并计 `skipped`。相等不接管:本机自己的行经别处绕回来时不会被自己「接管」。`preserve_local_translations` 改为不依赖印记相等,接管本机版本时同样保留仍有效的本机译文缓存。源行存在与否不再影响文章导入。
- **analyses**(`_apply_analyses`):文章缺失、或文章是本机版本(无印记)→ 跳过并计数(今天是整页失败)。文章印记匹配 → 照今天应用;`content_hash` 不符仍整页失败(同一快照内文章先到,不符即数据错误)。
- **sources**(`_apply_sources`):没有 → 建行,`is_active = False`(公共播客类型例外,见 §3.5)、`ai_analysis_enabled = False`、`fetcher_id` / `params_json` 留空;已有 → 只覆盖目录字段(`_SOURCE_FIELDS` ∪ `_CATALOG_SOURCE_FIELDS` 去掉 `is_active`、`ai_analysis_enabled`、`fetch_interval_minutes`),不碰本机开关与绑定。读者自定源同 id 仍整页失败。源不做修订号比较:目录字段与本机字段不重叠,没有冲突可言。
- **source_states**(`_apply_source_states`):本机状态行无印记且 `updated_at` 不早于来者 → 跳过;否则应用并盖印记。覆盖已有行时不再清空本机游标字段(只在新建时置空)。
- **media**:不变。
- **podcast_texts / podcast_audio**:单集文章是本机版本、或文稿槽位已被本机自己的产物占用 → 跳过该行并计数(今天是整页失败);其余不变。依赖的讲稿被跳过时,对应音频行同样跳过。
- **events**:不变(成员文章不是该上游写入的版本 → 今天就已跳过并计数)。
- **tombstone**(`_apply_tombstones`):规则不变——只删印记等于该上游的行;sources 没有印记后改为:本机无该源文章 → 删行,有文章 → 置退役(与 #262「被引用的源只退役」一致);播客槽位属于本机 → 跳过而非失败。
- **实体状态记账**(`_save_remote_entity_states`):只给**实际应用**的行记账(今天连没应用的行也改写,会把本机行的导出状态盖成远端 tombstone),并写本机时钟(§3.4)。

### 3.4 导出与转发:本机时钟

`archive_sync_entity_states.revision` 今天对本机行是本机时钟、对同步写入的行是上游时钟(用于「这行我应用过没有」与事件成员先后)。转发要按本机时钟给下游翻页:

- 新增列 `local_revision INTEGER NOT NULL DEFAULT 0` 与索引 `(stream, local_revision, identity)`。`revision` 与 `authority_id` 语义不动,所以旧代码回滚后照常工作。
- 本机行:在状态表上加两个触发器,`authority_id = ''` 的行插入或 `revision` 变化时令 `local_revision = revision`。现有约 20 处写状态的业务触发器一字不改。
- 同步写入的行:`_save_remote_entity_states` 每页先推进一次 `archive_sync_clock`,本页应用的行 `local_revision = clock`(keyset 为 `(local_revision, identity)`)。
- 导出(`export_page`、`pending_state_counts`、`_media_references`、`_export_media_rows`):去掉 `authority_id == ""` 过滤,改按 `local_revision` 过滤与排序。行信封的 `revision` 填 `local_revision`。
- `_upsert_payload`、`_article_is_public`、`is_public_media_reference`、`is_public_podcast_audio_reference`、`authority_present_identities`:去掉所有「印记为空」条件,保留数据范围条件(非 `user_rss_`、无 `owner_username`、非凭据源、legacy 投影的 `legacy_export` / `retired_at`、分析 `content_hash` 失配 → tombstone)。读者数据、自定 RSS、凭据源照旧永不外传。
- 上游的 tombstone 到达后状态行为 tombstone、带本机时钟,自然转发。
- 启动自愈(幂等):`authority_id <> '' AND local_revision = 0` 的行补一次时钟,覆盖「升级 → 回滚 → 再升级」窗口里旧代码写下的行。

线格式:不新增任何键。不拉任何人的节点 `local_revision ≡ revision`,导出逐字节不变。

### 3.5 源的普通开关

- **采集**:公共源只有被编入启用的采集任务、或管理员手动运行时才会采;同步不会把源编入任何任务,所以同步建出的源天然不采。`is_active` 对配置源是采集前提(`collection_nodes.py:152`),同步建行时置 `False`;公共播客源级启停本就不支持(`source_configs.py:338`),建行时保持 `True`。
- **分析**:`ai_analysis_enabled`。同步建行时置 `False`。它只影响**本机写入的文章**;同步写入的文章不看它(§3.2)。
- **入口**:配置源已有启停与编辑接口;预设镜像行今天没有任何开关入口(`_require_config_managed` 拒绝)。新增 `POST /api/admin/sources/{source_id}/analysis`(`{enabled}`,公共源行通用,入审计),源管理检视器加一个「本机分析」开关,样式与同页「在读者面隐藏」一致。
- **有上游的节点上新出现的源**:启动对账(`source_rows.reconcile_source_rows`,现改为每台都跑)与孤儿回填在「已与某上游建立 v2 同步」的节点上新建行时同样默认关,避免对账先于同步建行而漏掉默认值。`local_public_writes_allowed` 删除;`_reconcile_mirror` 对已有行只动目录字段的行为不变。
- 收录推送、新建公共源、签发收录令牌在任何节点都可用(`ingest_push.py:130 / 181 / 267`、`source_configs.py:73` 的拒绝删除)。

### 3.6 事件:一个节点一个 id 空间

`events.id` 是本机自增整数,同步时原样照抄,本地同 id 事件会让整页回滚(`_apply_events`)。今天接收端不产生公共事件所以不撞;本波之后接收端可能有自己采的文章。处理:**本机存在印记非空的事件(用着上游的事件)时,事件归并任务不运行**(`execute_event_assignment_sweep_job` 入口加判断)。代价:这类节点上自己采的文章不进热点事件榜。事件的全局身份留待后续(§9)。taxonomy 仍由 `taxonomy:authority_id` 决定只读与否,`api/app.py:3034`、`services/taxonomy.py:1345`、`routers/taxonomy.py:42` 的 consumer 判定改为只看该 KV。

### 3.7 控制台与前端

- `sync_console.console_status` 去掉 `consumer_fence`,加 `legacy_import_locked`(v1 导入是否被锁)。
- `SyncConnect.jsx` 删「开始首次同步」确认框;`SyncPops.jsx` 定时弹层删警示与 danger 态;`SyncLink.jsx` 删「本地采集」行;`SyncDrawers.jsx` 的 v1 导入锁改读新字段;`SyncPage.jsx` 去 `fenceActive`。
- 源管理检视器加「本机分析」开关(§3.5)。读者面、移动壳无改动。

### 3.8 迁移(一个 Alembic revision,`heads` 口径)

1. `archive_sync_entity_states` 加 `local_revision` 与索引;本机行 `local_revision = revision`;同步写入的行统一取 `clock + 1` 后时钟 +1。
2. `source_configs`:`collection_authority_id <> ''` 的行置 `ai_analysis_enabled = 0`,非播客类型另置 `is_active = 0`,然后清空 `collection_authority_id`;为这些行补本机实体状态(它们从此由本机导出)。
3. 曾处于 consumer 模式的节点(存在 `remote_sync:v2_consumer_mode`):把现有采集任务置为停用、`daily_brief_enabled` 置 `false`——这两样在该节点上今天本来就被围栏挡着跑不成,保持现状,由管理员按需重新打开;然后删该 KV。
4. 触发器在迁移内先整体 DROP、结束后按新定义重建(沿用既有做法)。`downgrade` 只回退结构,不恢复印记。

`tests/test_migrations.py` 的 drift 测试与 `test_archive_sync_v2_downgrade_refuses_to_reopen_live_writers` 的 `consumer_marker` 分支随之更新。

## 4. 部署顺序与混版本(硬规则 12)

用户已表态两端尽量同批次更新;下面是不同批次时的结果。

**1. 改了哪些跨节点契约**:线格式零变化(不加键、不加流、不加请求参数)。变化的是语义:导出范围(持有的全部公开数据)、接收端合并规则、接收端不再发 presence 请求。出网请求只减不增。神灯流水线、IM 机器人、feed 不读同步流字段;`ANALYSIS_PAYLOAD_GENERATION` 不动。

**2. 两个窗口**

| 窗口 | 会怎样 | 结论 |
|---|---|---|
| 新外网 + 旧内网 | 外网没有同步写入的行,`local_revision ≡ revision`,导出集合与字节都不变;旧内网照旧运行在 consumer 模式下 | 安全 |
| 旧外网 + 新内网 | 新内网按新规则合并、不再发 presence;迁移已停用内网原有采集任务与日报定时,不会突然开始采集 | 安全 |
| 新中转节点 + 旧下游(未来形态) | 旧下游把转发来的行当成中转节点的行;presence 口径已同步放开,旧下游的全量清理不会误删转发行 | 安全 |

**3. 不能双向兼容的点**:无。

**4. 回滚**:旧代码不读 `local_revision`,`revision` / `authority_id` 语义未变,文章与分析印记仍在。回滚后旧代码启动时会按 v2 定时重新建立 consumer 围栏;源行的归属印记已被清空,旧代码在源下次变更时重新接管。无数据破坏。

**5. 验证**:旧 tag(v3.67.4)接收端 × 新生产端、新接收端 × 旧 tag 生产端各一次(首次全量 + 一次增量);三节点链 A→B→C 实测(§6)。

## 5. 上线说明(写进发版说明)

- 任意顺序;同批次最好。
- 内网升级时迁移会做三件保守的事:同步来的源置为「采集关、分析关」;原有采集任务全部置为停用;公共日报定时置为关。它们今天在内网本来就不生效。升级后想让内网自己采某些源:在源管理打开该源的「本机分析」(配置源另需启用),把它编入采集任务并启用任务。
- 升级后内网可以新建公共源、签发收录令牌。

## 6. 测试与验收

**pytest**
- 迁移:`local_revision` 回填与时钟;源印记清空与开关置关(播客例外);consumer 节点的采集任务 / 日报停用与 KV 删除;drift;downgrade;启动自愈幂等。
- 合并:同步写入的版本被更新;本机版本遇到更旧 / 相等 / 更新的来者;接管时保留本机译文;本机抓取改动已盖印记的行后清印记并成为本机版本;印记行上的本机批注不推进修订号。
- 逐流跳过:分析 / 播客文稿 / 播客音频撞本机版本时跳过不失败;源状态本机更新者保留;tombstone 不删本机版本;源 tombstone 有文章时置退役。
- 源:同步建行默认关;更新不碰开关与绑定;有上游的节点对账新建行默认关;`POST /api/admin/sources/{id}/analysis`。
- 导出:中转节点导出含同步写入的行、按 `local_revision` 分页;不拉任何人的节点导出与改动前逐字节一致(固定样本对拍);媒体 / 音频 / presence 口径一致;私有与凭据源不外传。
- 三节点链(进程内三个 sink):B 拉 A、C 拉 B → C 有 A 与 B 的文章、分析、源;A 删一篇 → B、C 依次删除;B 自己采的源 C 也拿到;B 首次同步不停用本机源、不删本机数据;任何一步都没有 presence 请求。
- 拆 consumer:首次同步与启用定时不再写 KV;有上游的节点可新建公共源、签发收录令牌、跑对账;taxonomy 只读仍由 KV 决定;持有上游事件的节点事件归并不运行。
- 跨版本实测两组(§4-5)。

**沙箱目检(用户)**:两个隔离栈,B 以真实库副本为底、C 空库拉 B。
1. C 的运维 → 节点同步:接入卡直接开始首次同步,无确认框;完成后流水线九站完成。
2. C 的阅读器能看到 B 的文章、图片、播客与分析结果。
3. C 的源管理:同步来的源「本机分析」为关;打开一个源的开关并手动运行一次,新抓到的文章在 C 本机分析。
4. 在 B 新建一个 C 没有的源并抓一次 → C 再同步后出现该源与文章。
5. C 新建公共源、签发收录令牌均成功。

## 7. 需要用户知情的取舍(按此实现,若不认可请指出)

| # | 取舍 | 理由 |
|---|---|---|
| 1 | 逐行同步印记保留为内部簿记,没有全部拆掉 | §3.2 的两个原因;没有任何规则或界面依赖「来自哪台」 |
| 2 | 打开某个源的「本机分析」只对本机自己抓到的文章生效,不会去分析上游发来的那些 | 上游的文章由上游的分析结果覆盖;否则两边各付一次费 |
| 3 | 用着上游事件的节点不做本地事件归并,该节点自己采的文章不进热点事件榜 | 事件 id 是本机自增整数,两个来源必然相撞(§3.6) |
| 4 | 抓取遇到已在库的文章仍只补正文 / 修占位,不改写已归档内容 | 「忠实归档」是既有产品规则,本波不动 |
| 5 | 两台都采同一个源时,各自保留自己抓到的较新版本并各自负责分析 | 「新者胜」的直接结果;相等时不接管是为了行绕回原节点时不出错 |
| 6 | 迁移在内网停用原有采集任务与日报定时 | 它们今天被围栏挡着本就不跑;静默恢复会让内网突然开始抓公网 |
| 7 | 互拉与多来源在数据规则上不再被禁止,但本波不做界面、不做验证 | 同步控制台仍只管一个上游;taxonomy 治理仍要求单一来源 |

## 8. 风险与观察

- `archive_updated_at` 是无时区的本机墙上时钟(`models/db.py:255`)。「新者胜」只在本机版本与来者之间比较;两台时区口径不一致时,同采一个源的胜负会偏向时区靠前的一方。只同步不自采的节点不受影响。
- 本机版本一旦比上游新,就不再接收上游对这篇的更新与分析,直到上游出现更新的版本。只发生在两台都采同一个源时。
- 拆掉约 30 处源级判断后,行为等价性靠测试保证:重点是采集运行路径与分析队列。
- 内网恢复「可以自己采」后产生的失败运行记录、源状态会随导出流向它的下游(今天没有下游)。

## 9. 不做

- 事件全局身份(多来源事件合并);hub 转发候选证据;多上游的控制台界面。
- 删除 `collection_authority_id` 等已废弃列(后续波次,避免本波再碰 SQLite 表重建)。
- 「被拉取」视图(2026-11-05 用户决定)。
- 抓取路径的覆盖语义(§7-4)。

## 10. 影响面

- 后端:`services/archive_sync_v2.py`、`services/remote_sync.py`、`services/sync_consumer_policy.py`(收缩为 v1 兼容判定)、`services/sync_console.py`、`services/source_rows.py`、`services/translation_sync.py`、`services/event_assignment.py` 或其任务入口、`storage/archive_sync_revision.py`、`storage/impl/db_storage.py`、`models/db.py`、`alembic/versions/<new>.py`;调用点清理:`api/app.py`、`api/routers/{remote_sync,archive_sync,source_configs,ingest_push,taxonomy,articles,admin}.py`、`services/{article_analysis,analysis_backfill,daily_brief,podcast_processing,paper_fulltext,title_translation,taxonomy}.py`。
- 前端:`components/admin/sync/{SyncConnect,SyncPops,SyncLink,SyncDrawers}.jsx`、`pages/SyncPage.jsx`、`FetchTab.jsx`(开关)、`api.js`。
- 文档:`contracts/archive_sync.md`、`architecture/archive-sync.md`、`architecture/overview.md`、`AGENTS.md` 架构表 Archive Sync 行、`ops/{aliyun-dual-node-deployment,configuration,taxonomy-v1-deployment,runbook}.md`、`reference/endpoints.md`、`plans/issue-262-source-model-refactor.md`(不变量 6 注)、`version-history` / `backlog` / `decisions`。
- 测试:`tests/test_sync_consumer_policy.py`(重写)、`test_archive_sync_v2.py`、`test_remote_sync.py`、`test_migrations.py`、`test_ingest_push.py`、`test_source_model.py`、`test_sync_console.py` 及引用源级围栏的其它用例;前端 `frontend/test/*.test.mjs`。

## 11. 检视

(待 Codex 方案检视;任务书关注面:§3.2 印记降级是否自洽、§3.3 逐流规则的遗漏、§3.4 两时钟与回滚、§3.8 迁移对内网的三件保守动作、§4 混版本。)

## 12. 实施记录

(待实现。)

## 13. 方向演变(备查)

- **第一稿(2026-11-09,已作废)**:保留按源归属,同源上游有即归上游;导出带原产地 `origin_authority_id`、防环、源管理页「远端」标。配套图解页 [`../design/issue-263-sync-symmetric/explainer-v1.html`](../design/issue-263-sync-symmetric/explainer-v1.html) 画的是这一稿,其中「归属今天怎么运作」「整机开关」两部分仍是对改动前现状的准确说明。
- **重估(2026-11-10)**:用户指出没有按 server 记归属的需求、方案太复杂,提出按身份合并。评估确认文章身份已是全局确定性 id;归属标记实际在替三件事把关(存在时谁赢、付费不重做、删除),三问由用户拍板(§1)。
- **核对中发现并改变设计的三件事**:① 文章修订号会被本机批注推进(§3.2-1),所以「新者胜」不能脱离逐行印记;② 事件 id 是本机自增整数(§3.6);③ 预设镜像行没有任何开关入口(§3.5)。
