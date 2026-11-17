# issue #261 节点同步控制台

> **归档于 2026-11-05(issue #261,PR #265,开发版 3.67.5-alpha,用户目检放行、待发布)**:现状以 docs/architecture/(`admin-ops.md`、`archive-sync.md`)、`docs/contracts/archive_sync.md` 与代码为准,文中路径与状态为当时记录。
> 状态:已落地待发布(2026-11-05 开工;Codex 两轮交叉检视通过,同日用户沙箱目检放行,CI 通过后 PR #265 squash 合入 main)。分支 `feat/issue-261-sync-console`。
> 相关:issue #261 · 样页 [`../design/issue-261-sync-console/`](../design/issue-261-sync-console/README.md)(方向 A 流水线)· 协议 [`../contracts/archive_sync.md`](../contracts/archive_sync.md) · 运维页信息架构 [`../architecture/admin-ops.md`](../architecture/admin-ops.md)
> 实现方:Claude Code(Opus 5.5;后端本会话,前端子代理)· 检视方:Codex(见 §6)

## 1. 拍板(2026-11-05 用户)

| # | 项 | 结论 |
|---|---|---|
| 1 | 方向 | **只用 A 流水线**(样页 `?dir=a`);B / C 不实现 |
| 2 | 计数 | 第一期做:外网在**已有 export GET 的首页 manifest** 附带计数,不新增请求、不多带出网信息 |
| 3 | 生产端视角 | 不做生产端专属视图、不记录入站拉取。各节点**一致显示**「本机从其它 server 拉取」的同步:外网若也从别的 server 拉就照常显示,没拉过就显示空白(接入卡) |
| 7 | 节点不分角色(目检追问) | 页面不出现「接收端 / 生产端」:页头「已接入 / 未接入」、卡片「接入远端」、连接栏「本地采集 照常 / 仅自定 RSS」;「被拉取」视图不做(一台同时拉与被拉罕见,维持现状);首次 v2 同步与启用 v2 定时会让本机进入 consumer 模式(公共源采集与分析停止、界面不可撤回),先确认;协议层「中转 / 互拉 / 多来源」另议 #263 |
| 4 | 高危抽屉 | 重置单流 + 重放分析流 + v1 JSONL 兼容导出 / 导入;「更换远端并重新基线」不做 |
| 5 | 出网 | 控制台与文档不额外体现出网;方案保证出网不扩大(§3) |
| 6 | 放哪 | 运维新增一页「节点同步」(`#/admin/sync`),在「存储与外部资源」之后;首页目录加一块 S 磁贴;设置柜的「数据同步」区整体移除 |

## 2. 范围

**后端**
1. 导出端计数(producer):每条 transaction-revision 流**首页**(`after` 为空)manifest 附 `pending`(本次快照内将导出的行数,= 接收端本流处理量的分母);`sources` 首页另附 `pending_by_stream`(除 taxonomy / media 外各流按同一 `since` 的计数)。纯附加字段,老接收端忽略;老生产端不给就不显示百分比。
2. 接收端进度(receiver):`progress` 增加 `stream_total` / `page` / `order` / `streams_done`(已完成各流统计与起止时间)/ `run_pending`;`result.streams[*]` 增加 `started_at` / `completed_at` / `pending`。
3. 状态接口:`GET /api/admin/remote-sync/status` 增加 `console` 块与 `jobs[*].created_by`;新增 `GET /api/admin/remote-sync/inventory`(本机各类内容条数,按需取)。
4. 测试连接:`probe` 以目标 `sources` checkpoint 为 `since`,回传 `snapshot` / `pending`;结果落 KV 供页头常驻显示。
5. 单流重置:`POST /api/admin/remote-sync/streams/{stream}/reset`(清该流 checkpoint + 该 authority 的该流实体状态,下次整流重拉、收尾按协议核对存在性);重放分析流 = `stream=analyses`。入审计。
6. 首页:`GET /api/admin/home` 增加 `sync` 摘要。

**前端**
- 新页 `frontend/src/components/admin/pages/SyncPage.jsx`(+ 子组件),`opsNav` 注册 `sync`;首页 S 磁贴;移除 `SettingsModal` 的 `DataSyncSection`(组件与 `RemoteSyncProgress` 删除,`api.js` 函数保留复用)。
- 版式严格复刻样页方向 A:页头两行 · 「本次 / 上次同步」流水线卡(含当前流进度条 / 失败条 / 卡底「不同步」)· 运行记录 · 连接与定时;逐流抽屉 / 运行抽屉 / 高危抽屉;定时弹层、测试连接弹层;未配置态 = 接入卡。
- 与样页的差异:无生产端视图;无数据来源角标;高危抽屉无「更换远端」;「不同步」清单为前端常量(与内容盘点同源于一个 `syncCatalog.js`)。

## 3. 出网不扩大

接收端对外网发出的请求种类与字段**与现状一致**:登录 POST、各流 export / media / podcast-audio GET、存在性核对 POST、候选证据 POST。本波只改**外网响应**(manifest 多几个计数),以及「测试连接」原有的 sources 首页 GET 由 `since=""` 改为带目标 `sources` checkpoint(`since` 本就是每次同步发出的翻页参数)。控制台打开时**不自动探测外网**,页头远端信息用上次测试 / 同步落下的 KV。

## 4. 接口契约

### 4.1 manifest 附加字段(producer → receiver,附加、可缺省)

| 字段 | 出现位置 | 语义 |
|---|---|---|
| `pending: int` | 每条非 taxonomy 流的首页(`after==""`) | 首页预计量:`(since, snapshot]` 内该流将导出的行数(含 tombstone;media 为引用中的候选数);分页期间被再次修改的实体移出本快照、下次增量到,故实际处理量 ≤ `pending` |
| `pending_by_stream: {stream: int}` | `sources` 首页 | 同一 `since`、同一 `snapshot` 下各流计数;不含 `taxonomy` / `media`(media 计数需全量扫正文,只在 media 首页给) |

### 4.2 `GET /api/jobs/{id}` 的 `progress`(v2 远程同步)

现有:`stream` / `processed` / `stream_processed` / `reused` / `downloaded` / `reused_bytes` / `downloaded_bytes`。新增:

| 字段 | 语义 |
|---|---|
| `order: [stream]` | 协商后的拉取顺序(sources 首页后可得;之前为 null) |
| `stream_total: int \| null` | 当前流分母(首页 manifest 的 `pending`;老生产端为 null) |
| `page: int` | 当前流正在处理的页序号(从 1 起;0 = 尚未发出首页请求);失败时即「第 N 页」 |
| `stream_started_at: str` | 当前流开始时间(ISO) |
| `streams_done: {stream: {...}}` | 本次已完成各流:`count` / `inserted` / `updated` / `deleted` / `pruned` / `skipped` / `deferred` / `pages` / `downloaded` / `reused` / `downloaded_bytes` / `reused_bytes` / `pending` / `started_at` / `completed_at` |
| `run_pending: {stream: int} \| null` | 本次 sources 首页给的 `pending_by_stream`,只保留起始 checkpoint 与 sources `since` 相同的流(未开始各站的「待同步」;其余流首页到达前未知) |

`result.streams[stream]` 同样带 `started_at` / `completed_at` / `pending`。失败任务 `result` 为空,逐流结果取 `progress.streams_done`,失败流 = `progress.stream`,页 = `progress.page`。

### 4.3 `GET /api/admin/remote-sync/status`

在原 `{state, jobs}` 上:`jobs[*].created_by`(定时任务为 `system`,手动为用户名),新增 `console`:

```json
{
  "target": "https://dorami.cloud",
  "configured": true,
  "stream_order": ["sources", "taxonomy", "articles", "analyses", "events", "media", "podcast_texts", "podcast_audio", "source_states"],
  "all_streams": ["…V2_STREAM_ORDER…"],
  "authority_id": "7c1e…",
  "schema_version": "archive-sync-v3",
  "capabilities": ["…"],
  "last_synced_at": "…",
  "checkpoints": {"articles": {"snapshot": "2418520", "completed_at": "…", "pages": 1, "count": 412, "…": 0}},
  "consumer_fence": {"active": true, "activated_at": "…", "reason": "v2_pull"},
  "local_stores": {"media": true, "podcast": true},
  "probe": {"version": "3.67.4", "snapshot": "2418736", "pending": {"articles": 216}, "taxonomy_version": 12, "probed_at": "…"},
  "resets": {"analyses": {"at": "…", "by": "admin"}},
  "analysis_generation": {"current": 3, "recorded": 3, "refetch_at": "…"},
  "schedule": {"enabled": true, "cron": "0 */6 * * *", "protocol": "v2", "updated_at": "…", "migration_required": false}
}
```

`target` 取定时配置的 `base_url`(若有)否则最近同步过的目标。`configured` = 已与该目标建立 v2 同步(有 authority);`protocol` = `v2` / `v1`(只有 v1 兼容记录的老目标)/ 空。未接入(含只有 v1 记录)→ 前端显示接入卡(预填地址与账号),有运行记录时照常列出。

### 4.4 `GET /api/admin/remote-sync/inventory`

本机条数(只读聚合,打开页面与任务结束时取,不随轮询):

```json
{
  "authority_id": "7c1e…",
  "streams": {"articles": {"upsert": 46218, "tombstone": 324}, "…": {}},
  "items": {
    "articles": 46218, "daily_briefs": 74, "translations": 3412,
    "analyses": 41930, "summaries": 40877, "content_values": 41000, "llm_tags": 118524,
    "events": 1286, "podcast_transcripts": 642, "podcast_blogs": 418, "podcast_scripts": 136,
    "podcast_audio": 128, "media": 24816, "sources": 128, "source_states": 128,
    "taxonomy_tags": 860, "candidate_evidence": 214
  }
}
```

### 4.5 `POST /api/admin/remote-sync/test`

原返回 + `snapshot`(生产端当前修订号,仅展示,不换算条数)+ `pending`(`pending_by_stream`;老生产端为 null)。成功后写 KV,`status.console.probe` 可见。

`/test` 与 `/start` 的密码留空时:地址(规整后)与账号都与定时配置一致、且已存密码 → 服务端复用已存密码(只写不回显不变);否则仍 400。

### 4.6 `POST /api/admin/remote-sync/streams/{stream}/reset`

- 请求体可空;目标 = `console.target`。`stream ∈ V2_STREAM_ORDER`。
- 409:有排队 / 运行中的同步任务;目标没有 v2 authority。
- 效果:删 `v2_streams[stream]`;删 `archive_sync_entity_states(stream, authority)`;`taxonomy` 只删 checkpoint;记 `resets[stream] = {at, by}`;`analyses` 另记 `v2_analysis_refetch_at / by`。
- 返回 `{"stream": "analyses", "reset_at": "…"}`。审计摘要「重置同步流 · AI 分析」。

### 4.7 `GET /api/admin/home` 的 `sync`

```json
{"configured": true, "status": "idle|running|failed|none", "last_success_at": "…", "last_run_at": "…",
 "running": {"done": 5, "total": 9, "stream": "media"}, "failed": {"stream": "analyses", "page": 37}}
```

## 5. 自测与验收

- 后端:manifest 计数与实际导出行数对拍(含 tombstone、media、podcast_texts 分页);进度字段;重置单流后下一次整流重拉与存在性收尾;status / inventory / home 形状;审计;老生产端(无计数)回退。
- 前端:lint / test / build;沙箱双栈(外网 authority + 内网 replica 各一套隔离栈)跑一次完整同步,截空闲 / 运行中 / 失败 / 未配置 + 抽屉,亮暗。

## 6. 检视

实现方 Claude Code(Opus 5.5;后端主会话、前端子代理),检视方 Codex(`gpt-6-astra`,effort ultra,`codex exec` 本地只读),两轮。

**R1(提交 0ac31b13)**:1 × P1 + 6 × P2,逐条表态后成批修(提交 398a1761):

| # | 问题 | 表态 | 修法 |
|---|---|---|---|
| 1 P1 | 重置单流与启动任务无共同互斥,重置可被随后增量写回的 checkpoint 抵消 | 接受 | 按库的进程内锁 `sync_control_lock`:启动「查活动任务 → 落 queued」与重置「查活动任务 → 提交」全程串行 |
| 2 P2 | 已接入 v2 后手动操作继承旧 v1 定时协议 | 接受 | 已接入时手动同步 / 测试连接固定 v2 |
| 3 P2 | `run_pending` 把 sources 的 since 套到其他流 | 接受 | 只留起始 checkpoint 与 sources since 相同的流 |
| 4 P2 | 逐流推进时间被当成整次成功;checkpoint 回退推断成功 | 接受 | `v2_last_success_at`;回退改中性「各流进度」 |
| 5 P2 | 关闭的抽屉仍可 Tab 聚焦;遮罩 onClick 绕过 `<Modal closeOnOverlay>` | 部分接受 | 关闭时 `inert`;遮罩保持运维台账抽屉既有范式(分歧) |
| 6 P2 | 首页导入失败时终态进度丢分母与协商 | 接受 | manifest 校验后、导入前补报进度 |
| 7 P2 | `pending` 非严格等于处理量(分页间被修改的实体移出快照) | 接受(改口径) | 改称首页预计量,处理量 ≤ pending,契约同步 |

**目检追问(2026-11-05)**:用户问「如何确定某端是接收端还是发送端」,本意是不做区分。核对结论:代码没有角色配置,每台都开着导出接口(凭对方管理员账号即可拉),配了拉取来源的那台在拉;但协议有三条既有限制——首次 v2 拉取即进入 consumer 模式(公共源采集与分析停止)、导出只发本机产生的数据(不转发)、只能一个来源。处理:页面去角色词、首次拉取加确认(§1 #7);「被拉取」视图不做;协议放开另开 #263;联调发现的坏图问题另开 #264。纯措辞与确认交互,不改接口,未再起检视轮。

**R2(复检,只对照修复清单)**:7 条均已修,无新增 P1,结论「可放行」。第 5 条:检视方同意本轮保留既有台账遮罩、不迁整套外壳,但不同意「规范只针对居中模态」的解释——既有台账抽屉范式与 conventions 遮罩条款的差异记观察项(backlog),不阻断本波。
