# 采集侧实体收敛路线

> 性质：**实体/概念简化路线 + 执行记录**（分阶段，含逐项状态）。
> 评估对象：采集侧调度实体（`节点 / 采集范围 / 采集任务 / 旧定时任务`）及其前端呈现（`FetchTab.jsx`「节点管理」、`FetchRunsTab.jsx`「任务与运行」）。
> 触发：用户反馈「用户面的一些实体定义和逻辑稍显复杂，如节点-采集范围-采集任务」。
> 日期：2026-07-08。带 `file:line` 锚点，经代码核实。

## 0. 一句话结论

采集侧当前有 **4 个概念**（节点 / 采集范围 / 采集任务 / 旧定时任务），概念上只需要 **2 个**（节点 / 采集任务）。「采集范围」是「采集任务」去掉下游策略后的子集、且已被作者标记为兼容遗留；「旧定时任务」是被采集任务取代的单节点调度模型。**大半简化收益可纯靠页面重构拿到（阶段 1，零迁移风险）**，数据模型退役留到阶段 2。

## 1. 现状实体地图

| 概念 | 载体（`src/models/db.py`） | 判断 |
|---|---|---|
| **节点 Fetcher** | 内置 `FetcherRegistry` + `SourceConfigRecord` | 必要，原子单元 |
| **采集范围** | `NodeGroupRecord`（`db.py:59`） | ⚠️ 冗余，退役目标 |
| **采集任务** | `CollectionJobRecord`（`db.py:76`） | 必要，唯一保留的编排概念 |
| **旧定时任务** | `FetchTaskRecord`（`db.py:47`） | ⚠️ 遗留，迁移后退役 |
| 运行追踪（聚合） | `CollectionJobRunRecord`（`db.py:96`） | 合理，父级 |
| 运行追踪（明细） | `FetchRunRecord`（`db.py:121`） | 合理，子级 |
| 健康/游标 | `SourceStateRecord`（`db.py:146`） | 合理，权威健康态 |

## 2. 冗余的三条证据

1. **采集范围 ≈ 去掉下游策略的采集任务。** `CollectionJobRecord` 字段是 `NodeGroupRecord` 的严格超集（多出 `group_id` / `downstream_policy_json` / `legacy_task_id`）。且 `NodeGroupRecord` docstring 自述「兼容性采集范围表……不再作为用户层订阅抽象」（`db.py:60`）。

2. **一个采集任务用两种互斥方式表达节点集合**：`group_id`（引用采集范围）**或** `fetcher_ids_json`（直接内联）。前端 `getJobFetchers()`（`FetchRunsTab.jsx:141`）、任务编辑器（`FetchRunsTab.jsx:779-813`）都得为此分叉——这是最直接的「概念税」。

3. **旧定时任务是并存的第二套调度模型**：`FetchTaskRecord`（单节点+cron）已被采集任务取代，`POST /api/collection-jobs/migrate-legacy-tasks` 提供迁移路径，运行历史里以「旧版计划」独立标签呈现（`FetchRunsTab.jsx:51`）。一个单节点采集任务即可替代。

## 3. 收敛路线（先页面、后模型）

### 阶段 1 · 纯前端重构（低风险，本文执行）
目标：把操作者心智模型从 4 概念降到 2，**不动任何表结构与后端接口**，保留对存量数据的读路径。

- **FetchTab（节点管理）**
  - 移除分段控件的「采集范围」入口（`FetchTab.jsx:739`）与整段 `view === 'groups'` 渲染块（`893-970`）。
  - 移除批量栏「保存为采集范围」按钮（`988-990`），保留「立即临时抓取」（有用的即席动作）。
  - 移除 `NodeGroupModal` 及其 state/handlers（`blankGroup` / `nodeGroups` / `groupModalOpen` / `openCreateGroup` / `handleSaveGroup` / `handleRunGroup` 等）与对应 api 导入。
  - 对陈旧书签 `#/fetch/groups` 做优雅降级：`view === 'groups'` 归一到 `catalog`。
- **FetchRunsTab（任务与运行）**
  - 任务编辑器移除「采集范围」`<select>`（`779-785`），节点面板去掉 `group_id` 分支（`796/798/806/811/813/842`），统一为「直接多选节点」。
  - **编辑即迁移**：`openEditJob` 打开一个引用采集范围的存量任务时，把该范围的 `fetcher_ids` 内联进来、清空 `group_id`（懒迁移，见阶段 2）。
  - `handleSaveJob` 恒发 `group_id: null`；校验文案改为「至少选择一个节点」。
  - 任务列表/详情移除「采集范围：xx」措辞（`510/526`），只显示节点数。
  - 保留 `getJobFetchers` / `groupsById` / `fetchNodeGroups` 读路径，使**尚未被编辑过**的存量范围任务仍能正确显示节点数。
  - 「旧版单节点定时计划」黄色管理块（`658-676`）**保留**：它是条件渲染（仅当存在遗留任务时出现），也是删除遗留调度的唯一入口，属自我隔离，留到阶段 2 迁移后再清。

### 阶段 2 · 数据模型收敛（需 Alembic 迁移 + drift 测试）
1. 迁移脚本：把存量 `NodeGroupRecord` 的 `fetcher_ids` 内联进引用它的 `CollectionJobRecord`，清空 `group_id`。
2. 跑 `migrate-legacy-tasks` 把 `FetchTaskRecord` 转成单节点采集任务；之后停止在调度器启动时加载 `FetchTaskRecord`。
3. 下线 `CollectionJobRecord.group_id` 分支与后端 node-group 端点；最终删除 `NodeGroupRecord`、`FetchTaskRecord`。
4. 按 CLAUDE.md 规矩：改 `models/db.py` → `alembic revision --autogenerate` → 复核 → 补 `test_migrations` 的 drift 校验。

> **已执行**（2026-07-08）。实际路径与原计划的两点偏差（见进度表）：① 数据合并不走 HTTP 端点，
> 全部收进单个 Alembic 迁移 `8f6d93196258`（deploy.sh 的 `ensure_migrated` 自动执行，无需人工步骤）；
> ② 旧任务转换 `is_active` **沿用原值**而非端点旧语义的 `False`——因旧调度路径同步移除，
> 置 False 会静默停掉仍在跑的定时。

### 读者侧（附带观察，暂不动）
读者侧已较干净（订阅折进阅读器侧栏）。残留小冗余：两种令牌 `dsub_`（单订阅）+ `dfeed_`（个人聚合）。当前 UI 只呈现 `dfeed_`，`dsub_` 保留为自动化 REST 高级路径——已恰当，暂不改。

## 4. 逐项进度

图例：✅ 已完成 · 🟡 部分 · ⬜ 未开始 · ⏸️ 有意暂缓

| 阶段 | 条目 | 状态 | 备注 |
|---|---|---|---|
| 1 | FetchTab 移除采集范围视图/按钮/Modal | ✅ | 删除 `NodeGroupModal.jsx`、`blankGroup` 及全部 group 状态/handlers |
| 1 | FetchTab 陈旧 `#/fetch/groups` 归一 | ✅ | `view: rawView` → `view` 归一到 `catalog`；ENABLE_CUSTOM 关闭时整条分段控件隐藏 |
| 1 | FetchRunsTab 任务编辑器去 group 分支 | ✅ | 移除 `<select>` 与节点面板 6 处 `group_id` 条件，统一直接多选 |
| 1 | FetchRunsTab 编辑即迁移（group→内联节点） | ✅ | `openEditJob` 用 `getJobFetchers(job)` 内联、`group_id` 恒 `''`；`handleSaveJob` 恒发 `null` |
| 1 | FetchRunsTab 列表/详情去「采集范围」措辞 | ✅ | 列表/详情/副标题；保留 `getJobFetchers`/`fetchNodeGroups` 只读兼容 |
| 1 | api.js 收敛 node-group 导出 | ✅ | 删除 create/update/delete/run，仅留只读 `fetchNodeGroups` |
| 1 | `npm run lint` + `npm run build` 通过 | ✅ | 两者全绿 |
| 1·回访 | 「编辑即迁移」后端语义核验 | ✅ | `PUT /api/collection-jobs` 为 `exclude_unset` 语义（`collection.py:315-327`）：显式 `group_id: null` 会真正清引用；运行时 `group_id` 优先（`collection_planning.py:36`）对未编辑存量任务仍正确 |
| 1·回访 | 死分支清理 | ✅ | `blankJob`/`openEditJob` 去 `group_id` 字段、`draftFetcherIds` 去不可达 group 分支；`handleSaveJob` 的 `group_id: null` 保留并注明缘由 |
| 1·回访 | `#/fetch/groups` 归一化上移到路由层 | ✅ | 从 FetchTab 组件内补丁移到 `App.jsx` `hashToRoute`，URL 与状态不再错位 |
| 1·回访 | CLAUDE.md 同步 | ✅ | Collection Jobs 段落改写 node-group 为 retired 概念（懒迁移语义）；FetchTab 结构注释更新 |
| 2 | 采集范围内联迁移脚本 | ✅ | Alembic `8f6d93196258`：引用任务按运行时优先级（group.params < job.params < group.per_fetcher < job.per_fetcher）内联，group 停用则任务同停；未引用/自带 cron 的范围转独立任务保调度 |
| 2 | 遗留任务迁移 + 调度器停载 | ✅ | fetch_tasks 转单节点任务（`legacy_task_id` 溯源、**is_active 沿用**——旧调度路径已删，置 False 会静默断跑）；`load_tasks_to_scheduler` 只载 collection_jobs |
| 2 | 下线 group_id / 删除 NodeGroupRecord、FetchTaskRecord | ✅ | 模型删两表 + `collection_jobs.group_id` 列 drop；`/api/node-groups*`、`/api/tasks*`、`migrate-legacy-tasks` 端点移除；feed 端点 `?group_id=` 作用域移除；历史列（运行/文章的 task_id/group_id/source_group_id）保留供回溯 |
| 2 | 引擎函数裁参 + 前端兜底清理 | ✅ | `run_collection_items`/`run_fetcher_with_tracking`/`create_fetch_run` 等去掉 task_id/group_id 经手；前端删只读兼容路径与「旧版计划」管理块（历史运行的 legacy_task 标签保留） |
| 2 | 测试 | ✅ | 基线 304 → **305 passed**（新增迁移数据测试：内联合并/独立范围转任务/旧任务转换/表列消失四组断言）；drift 护栏全绿；前端 lint+build 全绿 |
