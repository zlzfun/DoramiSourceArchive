# Stage 5 检视报告：UI Role Split and Deployment Polish

- 分支：`codex/ui-deployment-polish`
- 被检视提交：
  - `cc26bc7 fix: harden subscription token handling`（落实 Stage 4 检视意见）
  - `07cc637 feat: add reader subscription console`（Stage 5 主体）
- 检视日期：2026-05-25
- 检视者：Claude (Opus 4.7)

## 结论

实现完整、UI 角色切分清晰，达成 Stage 5 目标，可以合并。

- 新增 reader 侧「订阅分发」控制台（CRUD、轮换令牌、复制 Dify 接口、创建/轮换后一次性明文令牌展示）。
- 顶栏按角色显示「采集归档层 / 分发订阅层 / 双层一体」，角色可视化。
- `docs/configuration.md` 增补两层部署指引与最小 ini 示例。

已运行 `tests/test_subscriptions.py` + `test_archive_sync.py` + `test_runtime_role.py` —— **11 passed**；`SubscriptionTab.jsx` 与 `App.jsx` 通过 ESLint 无报错。

---

## ✅ 附带确认：Stage 4 检视意见已落实（`cc26bc7`）

上一轮 Stage 4 报告的可执行项已处理：

- 第 1 项（枚举 404 vs 401）：`resolve_subscription_by_token` 已统一为 401（不存在/停用/令牌错误一律 401），并新增 `/api/public/subscriptions/999/...` → 401 测试。
- 第 2 项（查询令牌进日志）：契约文档已写明「优先 Bearer，`?token=` 为较低安全级别回退并应更积极轮换」。
- 第 3 项（预览暴露首尾）：预览改为仅 `...{token[-6:]}`，测试同步更新。

第 4、5 项（前缀放行范围、血缘字段耦合）属「确认/观察」类，本阶段未新增写路由，维持原状即可。

---

## ✅ Stage 5 已验证正确

- **角色切分干净**：reader/all 才显示 `订阅分发`/`向量雷达`/`接入集成`；collector 才显示 `节点管理`/`任务与运行`。tab 用 `surface` + `runtimeInfo[`${surface}_enabled`]` 过滤，沿用 Stage 1 既有模式。
- **令牌「仅显示一次」**：列表只展示 `token_preview`；明文 `token` 仅在 create/rotate 响应后经 `TokenNotice` 展示，符合契约。
- **reader UI 有意只暴露简单过滤子集**（来源/类型/关键词/运行范围/有无正文），刻意不渲染 `job_id`/`job_run_id`/`fetch_run_id` 等 collector 血缘字段——正面契合计划 review focus「Reader concepts do not expose collector implementation details unnecessarily」。
- **部署文档到位**：collector 部署外网、reader 部署内网，串联 archive-sync 导出/导入与订阅交付路径，附最小角色配置示例。
- **细节稳健**：剪贴板有 `execCommand` 回退；图标按钮含 `aria-label`；`publicEndpoint` 对相对/绝对 `API_BASE_URL` 都正确拼接且不含令牌。

---

## ⚠️ 待办项

### 1.（中低）UI 限额范围与后端上限不一致，会出现「保存后数值回弹」

- 现象：后端 `normalize_delivery_policy` 把 `max_limit` 硬上限钳到 **500**（`default_limit ≤ max_limit`）；而前端 `buildPayload`/输入框允许 `default_limit` 至 **1000**、`max_limit` 至 **2000**。
- 后果：管理员填 `max_limit=2000` 保存后，后端静默钳为 500，重载后行内显示「上限 500」，无任何提示，易困惑。
- 建议：把前端 `numberInRange` 上界与 `<input max=...>` 对齐到 500（或有意提升后端上限并同步两端）。属本阶段范畴，建议小修。

### 2.（中低）编辑器对「高级过滤」有损：保存会丢弃 UI 未渲染的过滤项

- 现象：`buildPayload` 仅依据 UI 字段（source_ids/content_types/search/run_scope/has_content）重建 `filters`；后端 PUT 整体替换 `filters_json`。
- 后果：若某订阅最初经 API 创建并带 `job_id`/`job_run_id`/`fetch_run_id`/publish 或 fetched 日期窗等高级过滤，事后在 UI 里哪怕只改个名保存，也会静默清掉这些过滤。
- 建议：编辑时把 draft 未识别的 filter 键原样保留并回写（round-trip 透传），或在编辑器内注明「UI 仅管理基础过滤，其余将被重置」。

### 3.（低 / 观察）无 collector 侧归档导出/导入 UI

- 计划 Stage 5 提到 collector UI 应聚焦「…and archive export」。当前 `/api/archive/export|import` 仍为纯 API（Stage 3 即按自动化契约设计）。
- 缺一个采集端导出触发入口会让角色切分更完整，但非必须。作为小缺口提示，非阻塞。

### 4.（nit）一次性令牌横幅常驻

- `tokenInfo` 横幅在 create/rotate 后会一直显示，直到下次操作或删除该订阅；tab 因 `mountedTabs` 常挂载，切走再回来仍在。
- 属「让用户来得及复制」的预期 UX，安全影响极低。可选：在再次新建/轮换或一段时间后自动清除。

---

## 修改建议优先级

- 无强制阻塞项；Stage 5 可合并，整个 collector/reader 重构五阶段闭环完成。
- 建议本阶段小修：第 1、2 项（数值回弹与编辑器有损覆盖，均为可见的管理员体验问题）。
- 可选 / 观察：第 3、4 项。
