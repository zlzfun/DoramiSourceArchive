# issue #263 Archive Sync 节点不分角色:去掉整机 consumer 模式 + 转发(中转)

> 状态:排队中(2026-11-10 用户决定搁置、择机再动;方向已重估,见 §13。**§0–§10 是重估前的方案稿,不要直接据其实现**)。分支 `feat/issue-263-sync-symmetric`,开发版 3.68.0-alpha(不 bump)。
> 相关:[issue #263](https://github.com/zlzfun/DoramiSourceArchive/issues/263) · 契约 [`../contracts/archive_sync.md`](../contracts/archive_sync.md) · 架构 [`../architecture/archive-sync.md`](../architecture/archive-sync.md) · 前波 [#261 节点同步控制台](../archive/issue-261-sync-console.md)(归档)· 并行 [#262 源模型重构](issue-262-source-model-refactor.md)(不变量 6 由本波改写)
> 实现方:Claude Code(Fable 5.1)· 检视方:Codex(见 §11)
> 行号按 `ff0e494e`(main,2026-11-09)核对。

## 0. 结论

1. **采集与分析和同步解耦**:删除整机 `remote_sync:v2_consumer_mode` 围栏。同步只保留一条约束——**上游发来的源归上游**(`collection_authority_id` 非空 → 本机不采不析不改),这条约束今天已由按源归属实现,与整机开关无关。公共源采不采、分不分析回到普通开关(采集任务 / 源启停 / 分析开关),随时可改可撤。
2. **拉来的也导出(中转)**:导出范围从「本机自产」改成「本机持有的全部公开数据」,每行带**原产地** `origin_authority_id`,按**本机**事务时钟排序;接收端收到原产地是自己的行直接跳过(防环)。读者数据、自定 RSS、凭据源照旧永不外传——这是数据范围约束,与数据从哪来无关。
3. **单一来源不变**:一台节点只有一个上游,拓扑是树。**互拉(A↔B)与多来源不做**(§2)。
4. 出网约束不变:接收端发往上游的请求种类与字段与今天完全一致(§4)。
5. 改「外网 authority 唯一写者、内网 replica 只读公共数据」不变量 → 新不变量:**每个源恰有一个写者(采集它的那台节点);每台节点可同时是读者(有上游)与出版者(被拉取);数据沿树单向流动,原产地随行**。`AGENTS.md` 架构表 Archive Sync 行随本波改一次。

## 1. 现状:三条限制在代码里的位置

| # | 限制 | 位置 |
|---|---|---|
| 1 | 首次 v2 拉取 / 启用 v2 定时即进入整机 consumer 模式,公共源采集与分析全停,界面不可撤回 | `services/sync_consumer_policy.py:34` `activate_v2_consumer_mode`;三处激活:`api/routers/remote_sync.py:173`(首次拉取)、`services/remote_sync.py:1760`(保存 v2 定时)、`:1770` `activate_consumer_for_enabled_v2_schedule`(启动恢复);判定 `v2_consumer_mode_active`:67;首次 prepare 还把本机全部公共源 `is_active=False`(`services/remote_sync.py:411-435`) |
| 2 | 不转发:导出只取 `authority_id == ""` 的实体状态;`_upsert_payload` 对归属行一律返回 None(→ tombstone) | `services/archive_sync_v2.py` `export_page` 查询 1591-1597、`pending_state_counts` 298、`_media_references` 540、`_export_media_rows` 1369、`_upsert_payload` 1082-1240 各分支的 `record.*authority_id` 排除;二进制与存在性同口径:`is_public_media_reference` 578、`is_public_podcast_audio_reference` 4250、`authority_present_identities` 3811、`_article_is_public` 794 |
| 3 | 单一来源:逐行 authority 围栏 + taxonomy authority 交接 | `_accepted_remote_rows` 2085(2101 "belongs to another authority")、`import_page` taxonomy 3540-3547、`remote_sync.run_pull_v2` 1298-1308、`prepare_transaction_revision_consumer` 357-363 |

整机开关的 16 个引用点见 §3.5 表。按源归属(`local_source_operation_allowed` 第一条:`collection_authority_id` 非空即 False,`sync_consumer_policy.py:130`)是真正需要的约束,**保留**。

## 2. 目标形态与取舍

| 形态 | 做否 | 理由 |
|---|---|---|
| **中转** A→B→C(B 既拉 A 又被 C 拉;B 自己采的源 C 也拿到) | **做** | issue 本意;树形拓扑下转发与防环可收口 |
| **互拉** A↔B | **不做** | 它是 2 环,与「单一来源 → 树」矛盾。即使靠防环跳过本机行,两边同时首拉会撞上**对称交接竞态**:A、B 各有本地源 S,若两边都在对方应用自己的 S 之前先拉到了对方的 S,则两边都把 S 交给对方,S 从此无人采集。接收端只能看到上游一侧,无法安全仲裁 |
| **多来源** 一台从多台汇聚 | **不做** | 用户 2026-11-05 认可单一来源;多上游要重做逐行围栏、checkpoint、存在性核对三件事 |
| **按源 / 按流细化的 consumer 模式**(接收端选择不收上游的某个源) | **不做** | 去掉整机开关后已没有「模式」;「不收某源」= 两写者问题,见 §7-2 |

防环仍然要做(§3.4):它防的是**误配**(把本机指向自己的下游、或指向同一棵树里的旁支),不是为了支持互拉。

## 3. 设计

### 3.1 三个身份与它们落在哪

| 身份 | 含义 | 落点 |
|---|---|---|
| **本机** | `producer_authority_id(engine)`(`archive_sync_v2.py:203`,env `DORAMI_ARCHIVE_AUTHORITY_ID` 或 KV 懒生成) | 导出 manifest 的 `authority_id`;接收端防环比对值 |
| **上游(hop)** | 我从谁拉 | **不变**:逐行归属列(`ArticleRecord.analysis_authority_id`、`ArticleAnalysisRecord.authority_id`、`SourceConfigRecord.collection_authority_id`、`SourceStateRecord.authority_id`、`MediaAssetRecord.sync_authority_id`、`EventRecord.authority_id`、Podcast 两表 `authority_id`)、`archive_sync_entity_states.authority_id`、KV `taxonomy:authority_id`、checkpoint 的 `authority_id`。所有「不是我的,别碰」「只从一个上游收」「整流收尾按上游盘点」的判定全部照旧 |
| **原产地(origin)** | 这行最初由谁采集 / 分析 | **新增** `archive_sync_entity_states.origin_authority_id`(本机行 `''`);线上只在信封里出现 |

今天 hop ≡ origin,所以现有列只存一个;转发后二者分离,只在实体状态表上多存一列,逐行业务列不动。

### 3.2 实体状态表:加 `local_revision`,不改 `revision` 语义

现状(`storage/archive_sync_revision.py`):触发器只给**本机公共行**写状态(`WHEN authority_id = ''`),`revision` = 本机 `archive_sync_clock`;接收端对拉来的行由 `_save_remote_entity_states`(`archive_sync_v2.py:2112`)写状态,`revision` = **上游**的 revision(用于 `_accepted_remote_rows` 幂等去重、`_refetch_outdated_analysis_payloads` 等)。一张表两种时钟,靠 `authority_id` 区分。

转发要求拉来的行也能按**本机**时钟排序分页。两种做法:

- A. **新增 `local_revision`**:触发器为本机行写 `local_revision = revision`;`_save_remote_entity_states` 每页 tick 一次本机时钟、给整页远端行写 `local_revision = clock`(keyset 用 `(local_revision, identity)`,同值按 identity 排即可)。导出侧全部改用 `local_revision`。**旧代码完全不认识这一列**,回滚零风险。
- B. 把 `revision` 改成「本机时钟」、另加 `upstream_revision` 存上游值。改动更少,但接收端一旦回滚到旧代码,旧代码会把本机时钟值当成上游 revision 去比较,可能跳过之后真正的上游更新 → **回滚后数据静默过期**,除非先 `alembic downgrade`。

**选 A**(外网内网都可能回滚,硬规则 12)。代价:本机行两列同值;触发器 SQL 里约 20 处 `INSERT INTO archive_sync_entity_states` 加一列(集中在 `_state_values` 与几处内联 SELECT)。

迁移(新 Alembic revision,`heads` 口径):
1. `archive_sync_entity_states` 加 `local_revision INTEGER NOT NULL DEFAULT 0`、`origin_authority_id TEXT NOT NULL DEFAULT ''`,加索引 `(stream, local_revision, identity)`。
2. 数据:`authority_id = ''` 的行 `local_revision = revision`;`authority_id <> ''` 的行 `origin_authority_id = authority_id`(今天 hop ≡ origin)、`local_revision = clock + 1`,然后时钟 +1(一次 tick 给全部存量远端行,ties 按 identity)。外网(无远端行)只走第 1 条。
3. 删 KV `remote_sync:v2_consumer_mode`。
4. 触发器由 `install_archive_sync_revision_triggers`(`archive_sync_revision.py:1163`)在启动时 DROP / CREATE,迁移后随新代码重建。
5. **启动自愈**(与触发器安装同处,幂等):`local_revision < revision AND authority_id = ''` 的行补齐;`local_revision = 0 AND authority_id <> ''` 的行打新 tick——覆盖「升级 → 回滚(旧触发器不写新列)→ 再升级」窗口。

`tests/test_migrations.py` drift 测试要求 `create_all()` ≡ `upgrade heads`;其中 `test_archive_sync_v2_downgrade_refuses_to_reopen_live_writers` 的 `consumer_marker` 分支随 KV 删除改写。

### 3.3 导出:本机持有的全部公开数据

- `export_page` 非 taxonomy 流查询:去掉 `authority_id == ""`,按 `local_revision` 过滤与排序;`pending_state_counts` 同。
- `_upsert_payload` 各分支:**删除**所有 `record.*authority_id` / `source.collection_authority_id` 的排除,**保留** `owner_username`、`_public_source`(非 `user_rss_`)、凭据源(`_article_is_public` 的 `source_is_credentialed`)、legacy 投影的 `legacy_export` / `retired_at`、分析 `content_hash` 失配 → tombstone、事件无可导出成员 → tombstone。`_article_payload` 里的 `payload["analysis_authority_id"]` 仍填 **hop(本机)**,线格式与校验和对老接收端不变。
- **剥离本机读者层数据**:转发行的 `extensions_json` 里由 `translation_sync.preserve_local_translations` 保留下来的本机译文缓存不得外传(读者数据不外传)。导出 `origin_authority_id <> ''` 的文章时按同一套键表剔除本机译文键(实现时在 `translation_sync` 加 `strip_local_translations`,与保留逻辑同源)。分析行的 `_analysis_payload` 只导 `assignment_source == 'llm'`,人工叠加标签本就不外传,不变。
- 信封新键:`_line` 对 `origin_authority_id <> ''` 的状态行加 `"origin_authority_id": <origin>`;本机行不加 → **外网今天不拉任何人,线格式逐字节不变**。`parse_page`(`archive_sync_v2.py:1865-1940`)只校验指定键,不拒绝未知信封键,老接收端安全。
- taxonomy 流:manifest 加 `taxonomy_origin_authority_id`(= 本机 KV `taxonomy:origin_authority_id`;本机 taxonomy 自产时为本机 id)。**不能用 `taxonomy:authority_id` 充当**:那一项记的是上一跳,第二跳之后就丢了树根(2026-11-10 画图解时发现)。老接收端忽略未知 manifest 键(#261 的 `pending` 同一路)。
- 媒体:`_media_references` / `_export_media_rows` 去掉 `analysis_authority_id == ""` / `sync_authority_id == ""` / 状态 `authority_id == ""`,改按 `local_revision`;二进制路由的 `is_public_media_reference`、`is_public_podcast_audio_reference` 同口径放开(hub 本地媒体库 / 播客产物库里就有从上游下载的字节,`/api/archive/v2/media/{url_hash}` 直接供给)。
- 存在性核对 `authority_present_identities`:范围从 `authority_id == ""` 改为「可导出范围」(与 `_upsert_payload` 同口径)。**必须与导出同一 PR**,否则下游整流收尾会把所有转发行当「上游没有」全部删掉。
- 事件:`_upsert_payload` events 分支放开 `record.authority_id`;hub 的本地事件池本就不收上游文章(`event_assignment.py:484`),所以 hub 导出 = 上游事件(转发)+ 本机事件(本机文章)。
- `_tombstone_payload` 不变;远端 tombstone 到达 hub 后状态行 operation=tombstone、打本机时钟 → 自然转发。

### 3.4 接收:原产地、防环、本机时钟

- `parse_page` 读信封 `origin_authority_id`(可缺省,缺省 = manifest `authority_id`),校验为非空字符串。
- **连接时拦环(第一道)**:`probe` 本就 GET 目标的 taxonomy 快照做就绪检查。目标 manifest 的 `taxonomy_origin_authority_id == 本机` → 目标在本机的下游,测试连接报错「目标在本机的下游」,开始同步 / 保存定时拒绝,发生在任何写入之前。完备性:单一上游 + 不支持更换远端 → 成环只可能是「没有上游的根节点去接自己的后代」,而后代报出的 taxonomy 原产地必然是这个根。不新增请求。
- 接收端导入 taxonomy 时把 `taxonomy_origin_authority_id`(缺省 = manifest `authority_id`)存进 KV `taxonomy:origin_authority_id`,与 `taxonomy:authority_id`(上一跳)并存,供本机再导出。
- `import_page`(第二道,覆盖连接后上游拓扑被改动):`origin == producer_authority_id(engine)` 的行**跳过**(不应用、不记状态),计入返回值 `skipped`(events 流已有该字段,控制台已显示)。taxonomy 流:`taxonomy_origin_authority_id == 本机` → `SyncV2Error("taxonomy 回灌:远端的 taxonomy 原产地是本机")`,整流失败、不推进 checkpoint。
- `_save_remote_entity_states`:每页先 `UPDATE archive_sync_clock SET revision = revision + 1`,再对本页全部 accepted 行写 `revision = 上游 revision`(不变)、`local_revision = clock`、`origin_authority_id = 信封 origin`、`authority_id = hop`(不变)。
- `_accepted_remote_rows`、逐行归属检查、整流收尾、`_refetch_outdated_analysis_payloads`、`_switch_source_projection`、`prepare` 清状态——全部仍按 `authority_id = hop`,**不改**。
- 观察项(实现时顺手修,记入检视节):本机行与远端 tombstone 同 identity 时,`_apply_tombstones` 不删本机行,但 `_save_remote_entity_states` 仍把该 identity 的状态改成 hop/tombstone,本机行从此不再导出。改为只对**实际应用**(或已归 hop)的行记状态。

### 3.5 去掉整机 consumer 模式:逐引用点

| 位置 | 今天 | 改为 |
|---|---|---|
| `services/sync_consumer_policy.py` | `activate_v2_consumer_mode` / `v2_consumer_mode_active` / `V2_CONSUMER_MODE_KEY` | 三者删除(KEY 只留给迁移文件);模块名**保留**(16 个导入方,改名无收益,§7-5);`v2_receiver_state_present` 去掉 marker 分句,只按逐行归属列 + taxonomy KV 判定,更名 `remote_authority_rows_present`;`local_source_operation_allowed` 收成:源归上游 → False;collection / governance → True;analysis → 凭据自定源 False、其余 True(凭据源不经 MaaS 的规则从「只在 consumer 模式」改为**全局**,§7-4) |
| `api/routers/remote_sync.py:173` · `services/remote_sync.py:1760` · `:1770-1782` | 激活围栏 | 删除;`app.py` 启动处对 `activate_consumer_for_enabled_v2_schedule` 的调用一并删 |
| `services/remote_sync.py:411-435` prepare | 首次 rebase 把本机全部公共源 `is_active=False`、删本机 source_states | **不再停用本机源、不删本机状态**;只清 `authority_id == hop` 的实体状态与 source_states(幂等重基线)。首拉与源到达之间本机对某源多采的几篇,随该源交接归上游、整流收尾按存在性核对清掉——这是既有自愈路径 |
| `api/app.py:3034` · `services/taxonomy.py:1345` | consumer 模式下不跑 taxonomy 自动激活 | 改为 KV `taxonomy:authority_id` 非空(同步来的 taxonomy 只读)即不跑;`routers/taxonomy.py:42` 已有该判定,删 consumer 分句 |
| `api/routers/source_configs.py:73` | 接收端不能新建本地公共源 | 删除(上游没有的源本机照常建、采、析) |
| `api/routers/ingest_push.py:130 / 181 / 267` | 接收端不接受收录 / 不签发令牌 / 临时源 409 | 删 consumer 分句;267 保留 `collection_authority_id` 判定 |
| `services/source_rows.py:73 / 416 / 472` `local_public_writes_allowed` | 接收端不对账、写入闸不补建源行 | 函数删除。`reconcile_source_rows` 每台都跑:`_reconcile_mirror` 已跳过归属行(`:193`);`backfill_orphan_sources` 加「`analysis_authority_id <> ''` 的文章不回填源行」(上游的源行先于文章到达,孤儿只会是本机的);`ensure_source_row` 照常补建(调用方 `DatabaseStorage.save` 已被按源归属挡住) |
| `storage/impl/db_storage.py:254` · `article_analysis.py` 7 处 · `analysis_backfill.py` 2 处 · `daily_brief.py:2203` · `podcast_processing.py:270` · `paper_fulltext.py:40` · `title_translation.py:98` · `routers/articles.py:111` · `app.py` 5 处 · `source_configs.py` 4 处 · `taxonomy.py:2184` | 调 `local_source_operation_allowed` | **调用处不改**,语义自动收成按源归属。`article_analysis.py:614` 的 reason 字串 `v2_consumer_quiesced` 改 `remote_authority`,返回值 `consumer_quiesced` 改 `remote_authority`(grep 消费方) |
| `services/sync_console.py:135-189` | status `console.consumer_fence` | 改为 `ownership: {remote_owned_sources, local_public_sources}`、`legacy_import_locked: bool`(= `remote_authority_rows_present`) |
| `routers/archive_sync.py:401` · `remote_sync.py:1683 / 1743` | v1 导入 / v1 定时在有 v2 状态时 409 | 保留(改用更名后的判定);文案去「consumer 模式」 |
| 文档 | 见 §10 | |

### 3.6 源目录对账与 #262 不变量 6

#262 不变量 6「内网不在本地写公共源行」改为「**节点只写不归上游的源行**」:每台都跑对账与写入闸;上游有的源由上游行覆盖(到达即交接);上游没有而本机目录里有的源本机建行并可采集。#262 方案 §1.3-6、§3.2 加一行指向本波。`catalog-v1` 严格模式(缺行即错)与兼容视图不受影响。

### 3.7 「撤回」的含义

去掉整机开关后,接收端随时可以关定时、停拉取、开关任一源的采集与分析。但**已归上游的源仍归上游**(冻结:不采不析、可读、不随本机导出变化)。理论回程只有上游物理删除该源(tombstone → 本机行删除 → 本机重建即可采);而 #262 之后上游对被文章引用的源只退役不删除,`catalog-v1` 投影下退役是带 `retired_at` 的 upsert,本机行仍归上游。**所以实际上一个源归了上游就收不回来,断开同步后也一样**。「手动解除归属」与「更换远端 / 重新基线」(#261 拍板不做)本波不做,见 §7-2。

### 3.8 控制台与前端

- `SyncConnect.jsx`:删「开始首次同步」确认框与 `fenceOn`;「首次整流全量」前置检查保留。
- `SyncPops.jsx:159` 定时弹层:删 `needsFence` 警示与 danger 态,一律「保存」。
- `SyncLink.jsx:11` 连接栏「本地采集」行 → 「归远端的源 · N 个」(来自 `ownership.remote_owned_sources`;0 时显示「—」)。
- `SyncDrawers.jsx:432` v1 导入锁 ← `con.legacy_import_locked`;`SyncPage.jsx:265` 去 `fenceActive`。
- 源管理页(#272):源列表显示归属——归远端的源加一枚小标「远端」,行内采集 / 分析开关对其禁用(今天后端已 409),筛选加「本机 / 远端」。它同时就是 §5 的盘点清单(§7-3)。
- 读者面无改动;移动壳无改动。

## 4. 部署顺序与混版本(硬规则 12)

**前提**:生产拓扑今天是「外网 A 采集 → 内网 B 拉取」,A 不拉任何人、B 不被任何人拉。外网按 tag 先上,内网经 `master` 常晚几天,外网也可能回滚。

**1. 改了哪些跨节点契约**
- 导出范围:从「本机自产」到「本机持有的全部公开数据」。**对今天的外网导出集合无变化**(外网没有远端行)。
- 信封可选键 `origin_authority_id`(只在转发行出现);taxonomy manifest 可选键 `taxonomy_origin_authority_id`。
- 存在性核对 `/api/archive/v2/presence` 的范围与导出同步扩大(外网今天无变化)。
- 请求侧:**零变化**。不加 `receiver_capabilities` 取值、不加查询参数、不加端点(出网不扩大)。
- 内网神灯流水线 / IM 机器人 / feed:不读同步流字段,无影响。`ANALYSIS_PAYLOAD_GENERATION` 不动(分析载荷未变)。

**2. 两个窗口**

| 窗口 | 会怎样 | 结论 |
|---|---|---|
| **新外网 + 旧内网**(默认) | 外网无远端行 → 不出现新信封键;taxonomy manifest 多一个键,旧内网 `parse_page` 忽略未知 manifest 键(与 #261 `pending` 同路);presence 范围扩大但集合相同。旧内网仍带 consumer 围栏,行为与今天一致 | **安全,任意顺序** |
| **旧外网 + 新内网**(内网先上 / 外网回滚) | 新内网读不到新键 → origin = manifest authority,与今天等价;本机防环比对永不命中;consumer 围栏被迁移删除 → 内网「上游没有且采集任务开着」的公共源**恢复采集**(§5 盘点);`prepare` 不再停用本机源 | **安全**;唯一行为变化是内网本地采集恢复,这是本波目的 |
| 新 hub(B 转发)+ **旧**下游 C(未来形态) | C 把 A 原产的行记成 hop B 的行,叶子节点无感;若有人把旧 C 误配成环,旧 C 没有防环会把自己的行收成「B 的」(今天不转发故无此险) | 树内叶子安全;**环是配置错误**,新接收端响亮失败,旧接收端不防——写进运维手册 |

**3. 不能双向兼容的点**:无。线格式对现有拓扑逐字节不变,新键全部可选、仅在新形态出现。

**4. 验证**:合入前跨版本实测两组——旧 tag(v3.67.4)起接收端 × 新代码起生产端;新代码起接收端 × 旧 tag 起生产端;各跑首次全量 + 一次增量,比对条数与 checkpoint。再加三节点链 A→B→C 实测(§6)。发版说明写:外网先上;内网升级前按 §5 盘点。

**回滚**:新列旧代码不读(§3.2 选 A 的理由);KV 围栏被删后旧代码下次定时拉取会重建它(行为回到旧版,无数据风险)。

## 5. 上线前盘点与运维

- **内网盘点**(用户在内网跑,外网无真实读者 / 无上游):升级前列出「不归上游、非自定、未退役」的公共源与其启用中的 `CollectionJobRecord`:
  ```sql
  SELECT source_id, name, is_active FROM source_configs
   WHERE owner_username = '' AND collection_authority_id = '' AND retired_at IS NULL
     AND source_id NOT LIKE 'user\_rss\_%' ESCAPE '\';
  ```
  内网多半连不上公网源,这类源恢复采集只会产生失败记录;不想采的在升级后于源管理页关掉即可(现在可以关了)。升级后源管理页「本机」筛选即同一清单(§3.8)。
- `docs/ops/aliyun-dual-node-deployment.md:279` 第 4 步去掉 consumer 模式确认文案;`docs/ops/configuration.md:288-300`、`docs/ops/taxonomy-v1-deployment.md:49` 重写围栏段;运维手册加「同步拓扑必须是树;把节点指向自己的下游会在 taxonomy 流响亮失败」。

## 6. 测试与验收

**单测 / 集成(pytest)**
- 迁移:存量远端行 `origin_authority_id` / `local_revision` 回填与时钟 tick;KV 删除;drift 测试;downgrade 守卫改写;启动自愈幂等。
- 导出:hub 导出含远端行、按 `local_revision` 分页、转发行带 `origin_authority_id`、本机行不带;媒体 / 音频 / presence 口径一致;转发文章剥离本机译文;分析 content_hash 失配仍 tombstone。
- 接收:缺省 origin = manifest authority;origin == 本机跳过并计 `skipped`;taxonomy 回灌报错不推进 checkpoint;`_save_remote_entity_states` 的本机时钟与上游 revision 并存;`_accepted_remote_rows` 幂等不受影响。
- 三节点链(进程内,沿 `tests/test_remote_sync.py` 的 `FakeRemote` 思路起三个 sink):A 本地源 + B 本地源;B 拉 A;C 拉 B → C 有 A、B 两方文章 / 分析 / 源,`collection_authority_id = B`;A 删一篇 → B、C 依次删除;B 整流收尾不删 A 的行;C 整流收尾不删 A、B 的行;把 A 指向 C(误配环)→ 测试连接与开始同步拒绝且无写入;绕过连接检查直接导入页面时,A 跳过自己的行、taxonomy 流报错;C 再导出的 taxonomy 原产地仍是 A(第二跳不丢树根)。
- consumer 删除:`test_sync_consumer_policy.py` 重写为按源归属;ingest / source_configs / taxonomy 在「有上游」的节点可用;prepare 不再停用本机源;`reconcile_source_rows` 在有上游的节点运行且不碰归属行;`backfill_orphan_sources` 跳过上游文章。
- 跨版本实测两组(§4-4)。

**沙箱目检(用户)**:两个隔离栈(`dorami-sandbox-acceptance`,8089/5174 与顺延端口),B 以真实库副本为底、C 空库拉 B:
1. B 的运维 → 节点同步:接入卡无确认框即开始首次同步(此例 B 不配上游,只作为被拉方);连接栏无「本地采集 仅自定 RSS」;
2. C 接入 B 并首次同步:流水线九站完成;C 阅读器可见 B 的文章、图片、播客;C 的源管理全部源带「远端」标且开关禁用;
3. 在 B 的源管理新开一个 C 没有的源并抓一次 → C 再同步后出现该源与文章;
4. C 的源管理新建本地源(今天 409)→ 成功并可抓取;C 的收录令牌可签发;
5. 把 B 指向 C 测试连接 → 报错「目标在本机的下游」,开始同步被拒绝,B 库无任何写入。

## 7. 拍板项

| # | 项 | 建议 |
|---|---|---|
| 1 | 互拉(A↔B)与多来源**不做**;防环只为误配 | 不做(§2) |
| 2 | 「本机要自己采须先解除该源归属」怎么落:本波不做手动解除。代价:一个源归了上游就收不回来,断开上游后也冻结(§3.7) | 不做手动解除;触发 = 真实需要(届时与「更换远端 / 重新基线」一起做,需要逐源本地覆盖标记 + 冲突规则) |
| 3 | 源管理页加「远端」标 + 本机 / 远端筛选(兼作盘点清单) | 做(小改动,否则盘点只能查库) |
| 4 | 凭据自定源不经 MaaS 分析的规则由「只在 consumer 模式」改为全局 | 改为全局(数据范围规则,与节点形态无关;外网无读者,实际无行为变化) |
| 5 | `sync_consumer_policy` 模块名保留,只删三个函数、更名 `v2_receiver_state_present` | 保留模块名 |
| 6 | 新 hub 对旧叶子节点在误配成环时不防(§4 表第三行);连接时拦环只在新版本节点上生效 | 接受,写运维手册 |

## 8. 风险与观察

- **存在性核对与导出口径分叉**是本波最大的数据风险(下游整流收尾会删「上游没有」的行):两处共用一个谓词函数并加守卫测试(对同一库,`authority_present_identities` 的结果必须 ⊆ 该流 `_upsert_payload` 非 None 的集合)。
- hub 的 `pending` 计数包含下游会跳过的行(仅误配环时),控制台百分比略偏;可忽略。
- 转发行的媒体字节依赖 hub 本地媒体库已下载;hub 下载失败 / 被 #264 自检标 failed 的图,下游同样拿不到(与今天上游→下游一致)。
- 内网恢复公共源采集后产生失败记录与 `source_states`(本机行)→ 这些会导出给内网的下游(今天没有)。无影响。
- 本机译文剥离依赖 `translation_sync` 的键表,新增译文种类时要同时更新(加守卫:保留键表与剥离键表同源)。

## 9. 不做

- 互拉、多来源、按源 / 按流的接收端排除(§2)。
- 手动解除归属、更换远端 / 重新基线(§3.7)。
- 「被拉取」视图(2026-11-05 用户决定维持现状)。
- hub 转发候选证据(内网自定 RSS 向 taxonomy authority 上报):仍只报给直接上游;hub 收到下游证据后的处置另议。
- 出网请求任何扩展(含「接收端声明自己的 authority 让生产端预过滤」——改为接收端跳过)。

## 10. 影响面

- 后端:`services/archive_sync_v2.py`(导出 / 接收 / 存在性 / 二进制口径)、`services/sync_consumer_policy.py`、`services/remote_sync.py`(prepare、定时、启动)、`api/routers/remote_sync.py`、`api/routers/archive_sync.py`、`api/routers/source_configs.py`、`api/routers/ingest_push.py`、`api/routers/taxonomy.py`、`api/app.py`、`services/taxonomy.py`、`services/source_rows.py`、`services/sync_console.py`、`services/translation_sync.py`、`services/article_analysis.py`(reason 字串)、`storage/archive_sync_revision.py`(触发器 + 自愈)、`models/db.py`、`alembic/versions/<new>.py`。
- 前端:`components/admin/sync/{SyncConnect,SyncPops,SyncLink,SyncDrawers}.jsx`、`pages/SyncPage.jsx`、源管理页(#272)、`utils/syncCatalog.js`(文案)。
- 文档:`contracts/archive_sync.md`(「Direction and authority」「Streams and readiness fence」改写 + 新节「Forwarding and origin」)、`architecture/archive-sync.md`、`architecture/overview.md`、`AGENTS.md` 架构表 Archive Sync 行、`ops/aliyun-dual-node-deployment.md`、`ops/configuration.md`、`ops/taxonomy-v1-deployment.md`、`ops/runbook.md`、`architecture/llm-and-ai.md:20`、`plans/issue-262-source-model-refactor.md`(不变量 6 注)、`design/issue-261-sync-console/README.md`(注)、`version-history` / `backlog` / `decisions`。
- 测试:`tests/test_sync_consumer_policy.py`(重写)、`test_archive_sync_v2.py`、`test_remote_sync.py`、`test_migrations.py`、`test_ingest_push.py`、`test_source_model.py`、`test_sync_console.py`、`test_analysis_personal_api.py`、`test_content_value.py`、`test_paper_fulltext.py`、`test_auto_title_translation.py`;前端 `frontend/test/*.test.mjs` 同步目录文案。

## 11. 检视

(待 Codex 首轮;任务书列「混版本」关注面:§3.3 导出与 §3.3 存在性同口径、§3.4 防环、§4 两窗口、迁移回滚。)

**方案稿自查修订(2026-11-10,画图解时发现)**:① taxonomy 原产地原写「取 `taxonomy:authority_id`」,第二跳后丢树根 → 改为逐跳另存 KV `taxonomy:origin_authority_id`(§3.3 / §3.4);② 防环原只在 taxonomy 流报错,此时 sources 流已写入 → 提前到测试连接 / 开始同步,写入前拒绝(§3.4);③ §3.7 原写「上游不再导出即可收回」,核实 `catalog-v1` 下退役是 upsert → 实际收不回,§7-2 写明代价;④ 文章先到时(`_apply_articles`)只改源与该篇,同源旧文章仍盖「本机」章但被源列冻结,直到下次整流全量核对——判定点均含源列,不影响按源展示的结论。

## 12. 实施记录

(待实现。)

## 13. 方向重估与搁置(2026-11-10)

**用户观点**:没有「判断某个源或某篇文章来自哪个 server」的需求;方案太复杂。更简单的做法:无论同步还是抓取,得到一篇文章,已在库中就更新;问题只在于如何判断「已存在」。随后决定:**暂时搁置,之后挑合适的时机改动**。

**评估结论(实现方)**:方向成立,且比 §0–§10 更接近 issue 本意。归属标记是手段不是需求;§0–§10 把它当成必须保留的不变量,因此背上了原产地、两套时钟、防环、「归上游就收不回」(§3.7)。按身份合并的模型下互拉与多来源自然成立,「收不回」问题消失。

**「是否已存在」在文章上已解决**:文章 id = `{source_id}_{sha1(guid|link|title)[:16]}`(`fetchers/impl/rss_fetcher.py:79` `_entry_id`),与哪台节点抓取无关;抓取(`DatabaseStorage.save_with_outcome`)与同步(`_apply_articles`)都按 id 查已有行;同源换链接的合流见 `services/item_url_identity`。源(`source_id`)、图片(`url_hash`)、播客文稿(`episode_id:kind`)同为全局身份。跨源同一报道不合并(#262 拍板)。

**拆掉归属后必须另给答案的三件事**(重写方案前由用户定):

1. **存在时谁赢**。今天两条路径规则相反且都是有意的:抓取遇到已有文章不覆盖(忠实归档,`db_storage.py` 注释「绝不因上游改标题而改写忠实归档」),同步覆盖。都无条件覆盖则两台同采一个源时内容来回翻、分析反复作废。候选:逐篇修订号 `archive_updated_at` 新者胜(它是本机无时区墙上时钟,`models/db.py:247`,跨节点要求时钟与时区口径一致)。
2. **付费的活不做两遍**(分析 / 转写 / TTS)。文章流先到、分析流后到,空档里本机 worker 会把未分析的文章送去付费分析;归属标记今天挡的是这个。候选:同步建出的源默认关采集、关分析,走普通开关,随时可撤;同步之后不再覆盖这些节点本地开关(今天 `_SOURCE_FIELDS` 含 `is_active` / `ai_analysis_enabled`)。
3. **删除**。增量靠 tombstone 按 id 删,可行;整流全量后的「上游没有就删」(`finalize_full_authority_stream`)需要知道哪些行是上游的。候选:放弃该清理,或只对本机没有采集绑定的源做。

**两种方向都躲不开的硬伤:事件 id**。`events.id` 是本机自增整数,同步时原样照抄(`_apply_events`);本地同 id 事件一律 fail-closed 整页回滚。今天不出事是因为接收端的公共文章全归上游、不进本机事件池(`event_assignment.py:484`)。只要一台节点既收上游事件、又为自己采的文章建事件,整数必然相撞、events 流卡住。**§3.3「hub 导出 = 上游事件 + 本机事件」不成立**,事件需要全局身份(例如 `(产生节点, 序号)` 或不透明全局 id)。

**代价**:终态更简单,改动更大。`authority_id` 类字段铺在 41 个文件、约 580 处引用;播客几张表把它写进 CHECK / 唯一约束与触发器。混版本窗口更难:旧内网仍按归属行事。

**本稿哪些仍可用**:§1 现状与行号、§3.5 整机 consumer 开关的逐引用点表(去掉整机开关在两种方向下都要做)、§4 混版本四问的框架、§5 上线前盘点。**被方向重估取代**:§3.1–§3.4(原产地 / `local_revision` / 防环)、§3.7、§3.8 源管理页「远端」标、§7 拍板项 1–3 与 6。

**配套材料**:图解页 [`../design/issue-263-sync-symmetric/explainer-v1.html`](../design/issue-263-sync-symmetric/explainer-v1.html)(画的是重估前的方案;其中 ① 归属今天怎么运作、③ 整机开关、⑥ 互拉竞态仍是对现状的准确说明)。

**现场**:worktree `~/Codes/worktrees/DoramiSourceArchive/issue-263-sync-symmetric`,分支 `feat/issue-263-sync-symmetric`(自 `ff0e494e`),本稿与索引行未提交、未推送。issue #263 已于 2026-11-10 追加「方向重估与搁置」评论,两条 2026-11-05 旧评论顶部加了更新注;`docs/backlog.md` 条目仍是重估前的口径,恢复时更新。
