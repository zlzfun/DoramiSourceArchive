# Stage 2 检视报告：Collector Terminology Cleanup

- 分支：`codex/collector-terminology-cleanup`
- 被检视提交：
  - `ecbb5f0 fix: tighten runtime role gate`（落实 Stage 1 检视意见）
  - `2d7df64 refactor: rename collector node groups as scopes`（Stage 2 主体）
- 检视日期：2026-05-25
- 检视者：Claude (Opus 4.7)

## 结论

实现干净、范围克制，达成 Stage 2 目标，可以合并。

- 采集端用户语言由「节点组」统一改为「采集范围」，覆盖完整。
- 未做破坏性 schema/API 迁移：表 `node_groups`、类 `NodeGroupRecord`、列 `group_id`/`source_group_id`、路由 `/api/node-groups` 全部保留为兼容内部名，符合计划「保守迁移」要求。
- 决策已写入 plan 与 roadmap changelog，并更新了 Open Decisions。

已运行 `tests/test_runtime_role.py` —— **3 passed**（含 Stage 1 修复新增的未鉴权 401 与 `/mcpfoo` 断言）。

---

## ✅ 附带确认：Stage 1 检视意见已落实（`ecbb5f0`）

上一轮 Stage 1 报告的三项小修已正确处理：

- 第 2 项（中间件顺序）：`OPTIONS`/public-auth 放行与 `/api/` 鉴权现在都跑在 runtime 闸门之前 → CORS 预检不再被 403，403 只发给已鉴权调用方。
- 第 3 项（路径匹配）：`/mcp` 改为精确 `path == "/mcp" or path.startswith("/mcp/")`，并新增 `disabled_runtime_surface("/mcpfoo") is None` 断言。
- 第 5 项（未用参数）：`disabled_runtime_surface` 去掉了 `method` 参数。

`/mcp` 在新顺序下仍可免 admin cookie 访问（reader 角色放行、collector 角色 403），行为正确。

---

## ✅ Stage 2 已验证正确

- 重命名覆盖完整：全仓 `节点组` 仅剩 roadmap changelog 一处（有意描述本次改名）；用户面无残留。英文 "node group" 仅存在于 plan 文档自身（描述抽象与决策），非用户面。
- 后端错误文案、`api.js` 错误提示、`FetchTab.jsx` / `FetchRunsTab.jsx` 标签与文案、ORM 字段 `description`、调度运行名前缀均已改为「采集范围」。
- `NodeGroupRecord` docstring 重写为「兼容性采集范围表：…不再作为用户层订阅抽象」，与计划「保留为内部迁移兼容形态」一致。
- 文案厘清了层级关系：模态副标题「采集范围只维护节点集合和参数模板，可被采集任务复用」，与「采集任务」「节点」形成清晰三层语言，不冲突。
- Dify delivery 文档将 `group_id` 描述为 "saved collector-side collection scope"，查询参数名 `group_id` 不变 → 下游消费方不受影响。

---

## ⚠️ 待办项 / 注意点

### 1.（低）历史运行名未迁移，运行历史将出现混合术语

- 现象：新触发的定时运行会以「采集范围定时: X」写入 `FetchRunRecord` / `CollectionJobRunRecord` 的 `name`，而本次改名前已落库的历史行仍是「节点组定时: X」。运行历史界面会同时出现两种叫法。
- 性质：是「保守不迁移数据」选择的预期结果，两种叫法都可理解，满足计划 review focus「historical lineage remain understandable」。
- 建议：无需改代码；如在意一致性，可在前端展示层对历史 `name` 做一次「节点组」→「采集范围」的显示替换，或接受现状。仅作知会。

### 2.（nit / 命名口味）「采集范围」偏抽象，请确认是最终产品用词

- 「采集范围」字面更像「筛选范围/区间」，而其实质是「可复用的节点集合」。当前靠模态副标题消歧，已足够清楚，且属于计划里 "task node scope" 的范畴。
- 备选如「采集节点集」更直白，但非必须。既然该词现在是用户可见的正式产品语言，请确认它就是你想固化的叫法。无需改代码。

### 3.（nit / 已知）内部标识符仍是 node-group 风味，与 UI 词出现永久落差

- `fetchNodeGroups` / `createNodeGroup` / `nodeGroups` / `handleSaveGroup` / `groupDraft` / `execute_node_group` / `NodeGroupCreate|Update` 等内部命名未改（有意，符合保守策略）。
- 这造成「UI=采集范围、代码=nodeGroup/group」的长期落差。plan 的 Open Decisions 已记录「未来是否物理重命名」，故无需本阶段处理；仅提示后续接手者注意对应关系。

### 4.（观察 / 面向 Stage 3）reader 侧 `group_id` 交付过滤依赖 collector 拥有的 `node_groups` 行

- `/api/dify` 受 reader 角色放行，但其 `group_id` 过滤需要读取 `node_groups` 表（采集范围由 collector 侧定义）。
- 在单仓 / `all` 模式同库下无问题；真正物理拆分后（Stage 3 同步契约），reader 库需要同步这些采集范围定义，`group_id` 过滤才有意义。
- 该耦合早于本阶段已存在，非 Stage 2 引入；仅作 Stage 3 设计时的提醒。

---

## 修改建议优先级

- 无强制修改项；Stage 2 可直接合并。
- 仅需产品确认：第 2 项（用词定稿）。
- 可选优化（非本阶段）：第 1 项历史运行名显示替换；第 3、4 项留待后续阶段。
