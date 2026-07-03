# 前端重构进度（活文档）

> 性质：**执行进度追踪**。配套计划见 [`frontend-refactor-plan.md`](./frontend-refactor-plan.md)。
> 每增量更新：状态（待办/进行/完成/暂缓）+ 提交哈希 + 验收结论。
> 基线（重构前）：`npm run build` 主包 `index.js` 1,024.73 kB（gzip 294.25）；lint 0 error / 6 warning。

## 阶段总览

| 阶段 | 状态 | 提交 |
|---|---|---|
| 阶段 0 · 低垂果实 | ✅ 完成 | `refactor/frontend` |
| 阶段 1 · 基建收敛 | ✅ 完成 | `refactor/frontend` |
| 阶段 2 · 组件拆分 | ⏳ 进行 | — |
| 阶段 3 · 数据加载模型统一 | 待办 | — |
| 阶段 4 · 样式体系 | 待办 | — |

---

## 阶段 0 · 低垂果实 ✅

| 项 | 债务 | 状态 | 说明 |
|---|---|---|---|
| Tab 懒加载分包 | F1 | ✅ | `App.jsx` 7 个 Tab 组件改 `lazy()` + 各自独立 `Suspense`（`TabFallback` 占位）。SettingsModal 等保持 eager。 |
| 修构建 CSS 警告 | f1 | ✅ | `index.css` 加 `@source not "../eslint.config.js"`，排除护栏文案里的示例 class。 |
| 重置密码弹窗 | f2 | ✅ | `AdminOpsTab` `window.prompt` → `createPortal` 弹窗（`type=password`，不回显明文，复用 `useModalTransition`）。 |
| INITIAL_RUNTIME 常量 | f3 | ✅ | `App.jsx` 抽模块级常量，初始化 + 两处重置（会话过期/登出）共用，补齐 `ai_beta_enabled`/`llm_configured` 字段。 |

**验收**：
- `npm run build` 零警告（CSS 警告 + 500 kB chunk 警告均消失）。
- 分包结果：主包 `index.js` **239.93 kB（gzip 75.32）**，较基线 1024.73（gzip 294）降 74%；AdminOpsTab（含 recharts）独立 421.52 kB、ReaderTab（含 react-markdown）176.88 kB、MCPTab 50.06 kB、各采集 Tab 28–33 kB，均按需加载。
- `npm run lint` 仍 0 error / 6 warning（DataTab×3 + FetchRunsTab×2 + FetchTab×1），属阶段 3 处理范围，本阶段未触碰。
- 待手工冒烟：双角色双主题登录 → 切 Tab（确认切换已挂载 Tab 不闪 fallback）→ 重置密码走弹窗。

---

## 阶段 1 · 基建收敛 ✅

| 项 | 债务 | 状态 | 说明 |
|---|---|---|---|
| `api.js` `request()` 收敛 | F3 | ✅ | 抽 `request(path, {method,body,errorMsg,...opts})` + `withFilters()`；全部导出改一行式包装，**导出名/签名不变**。特殊路径保留定制：`fetchAuthSession`/`fetchRunningProgress`（失败静默默认值）、`exportArchiveArticles`（text）、`importArchiveArticlesJsonl`（ndjson）、`fetchMcpStatus`/`toggleMcp`（无 ok 校验）、`recordArticleRead`（fire-and-forget）、`pollJob` 及 5 个提交-轮询接口。文件 806 → 517 行。 |
| Modal 可访问性 | F7 | ✅ | 新增 `hooks/useModalA11y.js`（Esc 关闭 + 焦点移入 + Tab 焦点陷阱 + 焦点归还，尊重 React autoFocus，捕获阶段监听）。接入 `Modal.jsx`（+`role=dialog`/`aria-modal`/`tabIndex=-1`）、`SettingsModal` 外层、`AdminOpsTab` 4 个 Portal 弹窗（llm/create/reset/detail）。 |
| 折叠栏 `inert` | F7 | ✅ | `ReaderTab` 左/中栏折叠时补 `inert`（React 19 原生），消除隐藏列仍可 Tab 聚焦的违例。 |
| 抽 `utils/collection.js` | f4 | ✅ | `TEST_RUN_LIMIT`/`normalizeIds`/`collectionRunMessage`（统一为带可选 successCount 的超集签名，FetchRunsTab 输出逐字不变）；FetchTab / FetchRunsTab 删本地副本改引用。 |
| Toast error 延时 + 关闭 | f6 | ✅ | error 停留 6s（info/success 维持 3s）；Toast 增关闭按钮 + `onClose`；App 增 `hideToast`。队列按计划暂缓。 |

**注**：F7 计划提及「AdminOpsTab/SettingsModal 手写 Portal 迁移到统一 `<Modal>` 组件」——本阶段先以 `useModalA11y` hook 就地补齐可访问性（用户面目标已达成），结构迁移留到阶段 2 提取文件时一并做（避免与拆分重复改动）。

**验收**：`npm run lint` 0 error / 6 warning（未变，属阶段 3）；`npm run build` 零警告；主包 gzip 75.69 kB（+0.37，来自 a11y hook + collection utils，可忽略）。待手工冒烟：各弹窗 Esc 可关 / 打开焦点入面板 / Tab 不逸出；error toast 停留更久且可手动关闭；阅读器折叠栏键盘不可聚焦。

---

## 阶段 2 · 组件拆分 ⏳

待执行：FetchTab（先 RunningWidget 消复制，再 Catalog/NodeGroups/Modal）、ReaderTab（AiPanel + markdown 共享）、AdminOpsTab（三子页 + 弹窗，顺带 GroupHeader/时间窗口样式）、FetchRunsTab（Job Modal + 历史）、SettingsModal（Section 分文件）。
