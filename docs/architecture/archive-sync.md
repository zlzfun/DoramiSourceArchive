# Archive Sync:外网 → 内网同步协议

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:契约全文 [`contracts/archive_sync.md`](../contracts/archive_sync.md) · [`ops/aliyun-dual-node-deployment.md`](../ops/aliyun-dual-node-deployment.md)
> 观测与管理:运维 → 节点同步(`#/admin/sync`,issue #261)——流水线看各流进度 / 失败流与页,运行记录,单流重置与重放分析;只读本机、不探测远端,计数来自外网 manifest 的附加字段(见契约「Issue #261」段)。

## Archive Sync v2 endpoint / v3 transaction protocol (external → internal, v3.46)

Production split deployments may both remain `role=all`; topology authority, not runtime role, decides work. External Dorami collects and analyses platform/public sources, while internal Dorami pulls nine ordered, independently checkpointed streams (`sources → taxonomy → articles → analyses → events → media → podcast_texts → podcast_audio → source_states`; `events` and the two Podcast streams are capability-negotiated and skipped by older peers) and serves them. The `events` stream (issue #121) replicates whole events with their member set and merge redirects under the same `authority_id` fence: the receiver copies authority ids verbatim, never assigns locally into a synced event, rebuilds `event_rank_snapshots` from local visibility and keeps `event_assignment_ledger` node-local. All non-Taxonomy streams share one SQLite transaction-revision snapshot; database triggers atomically advance per-entity `upsert|tombstone` state for ORM and bulk writes. Imported public records carry a stable external `authority_id`, so internal queue/claim/worker-commit paths cannot analyse or overwrite them. The receiver verifies schema/capability/authority before fencing local public writers; the first v3 launch atomically clears old timestamp checkpoints and rebases old authority-owned data. Source toggle remains soft deactivation (`is_active=false`), while an explicit Source delete is physical and its tombstone physically removes the receiver copy; Article/Analysis/SourceState physical deletes likewise propagate as tombstones. Media deletion stays local-GC-only. Internal user-added RSS is collected and analysed locally (including through an externally reachable MaaS); signed/credentialed feeds stay private and never use MaaS by default. Taxonomy is externally authoritative and becomes read-only internally. Media sync includes body/social/Podcast cover images but excludes audio. V1 article JSONL remains available only as an explicit compatibility mode; scheduled/manual remote sync defaults to v2. Deploy the external producer before the internal receiver. Full protocol and merge rules: `docs/contracts/archive_sync.md`.

**源目录投影(issue #262 R1c)**:接收端在每次导出请求里声明 `receiver_capabilities=source-catalog-v1`,生产者对 sources / source_states 两流按请求投影——声明了的拿到完整源目录(镜像行、回填行、临时源,退役 = 带 `retired_at` 的 upsert),没声明的旧接收端拿到与重构前相同的导出(`legacy_export` 定成员资格,非成员与退役发 tombstone,旧端空操作)。投影切换时接收端同事务清两流 checkpoint 与逐行记账后重放。两种混版本窗口都安全,部署顺序任意。契约全文见 [`contracts/archive_sync.md`](../contracts/archive_sync.md)「Source catalog projection」。

**节点不分角色(issue #263)**:上面英文段里「外网唯一写者 / 接收端围栏 / 首次 v3 把本机公共源停用 / 整流后按 presence 清理 / 先部署外网」这些表述已被取代,其余仍成立。现状:

- **没有整机 consumer 模式**。拉取别人不改变本机能做什么:公共源照常新建、采集、分析、收录;首次同步没有确认框,也不再停用本机源、清本机源状态。
- **逐行同步印记只是簿记**:文章 / 分析 / 源状态 / 媒体 / 播客 / 事件上的 `*authority_id` 表示「这行的当前版本是同步写进来的」(值是上一跳的 id,没有规则或界面依赖是哪一台);源上的 `collection_authority_id` 表示「上游也有这个源」,不再拦本机的采集、分析、管理、收录与批量抓取。本机抓取 / 收录改动一篇带印记的文章即清印记(成为本机版本)。
- **合并规则**:文章按 id 合并,库里是本机版本时只有本机 `archive_updated_at` **严格更新**才保留,否则(更旧或相等)换成来者的;依赖行(分析、播客文稿 / 音频)遇到本机版本就跳过、不卡流,文章之后被接管时由持久化的待重放标记(KV `archive_sync:v2:dependency_replay`)触发依赖流整流补回。
- **源的普通开关**:同步建出来的源 `ai_analysis_enabled = false`、不带采集绑定、不进任何采集任务;`is_active` 照上游(它决定早报范围与 readiness)。上游印记第一次落到本机没在用的已有源上时分析也置关。本机在用的源(跑过 / 被任务点名 / 存过绑定)不接受上游的删除、退役与停用。管理入口:源管理检视器「本机分析」开关(`POST /api/admin/sources/{id}/analysis`)。同步写入的文章不在本机分析——开关只管本机自己抓到的条目。
- **不做「上游没有就删」**:接收端不再调 presence、整流后不清理;行只随 tombstone 消失。生产端的 presence 端点保留给旧版本接收端。
- **导出与转发**:导出 = 本机持有的全部公开数据(含同步写入的行)。`archive_sync_entity_states.local_revision` 是本机导出修订(本机行由镜像触发器保持 `= revision`,同步写入的行每页取一次本机时钟);同步来的图片 / 播客音频文件装好时再推进一次导出修订。重放(单流重置、分析代次、投影切换)只把接收修订置 0,不删状态行。
- **事件二选一**:`events.id` 是本机自增整数。库里有本机事件的节点不拉 events 流;用着上游事件(或已接入带 events 能力的上游而事件表还空)的节点不做本地归并;两边写事务共用一把写门并在事务内复核。
- **边界**:只支持单上游的链(A → B → C),不支持成环与多上游;读者数据、自定 RSS、凭据源永不外传;本机在上游文章上做的产物不转发。线格式零变化,部署顺序任意,回滚靠备份。迁移在原接收节点上把全部公共源的分析置关、停用含公共源的采集任务、关公共日报定时(它们此前被围栏挡着本就不跑)。

