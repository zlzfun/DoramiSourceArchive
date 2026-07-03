# 前端重构进度（活文档）

> 性质：**执行进度追踪**。配套计划见 [`frontend-refactor-plan.md`](./frontend-refactor-plan.md)。
> 每增量更新：状态（待办/进行/完成/暂缓）+ 提交哈希 + 验收结论。
> 基线（重构前）：`npm run build` 主包 `index.js` 1,024.73 kB（gzip 294.25）；lint 0 error / 6 warning。

## 阶段总览

| 阶段 | 状态 | 提交 |
|---|---|---|
| 阶段 0 · 低垂果实 | ✅ 完成 | `refactor/frontend` |
| 阶段 1 · 基建收敛 | ✅ 完成 | `refactor/frontend` |
| 阶段 2 · 组件拆分 | ✅ 完成（含分档暂缓） | `refactor/frontend` |
| 阶段 3 · 数据加载模型统一 | ✅ 完成 | `refactor/frontend` |
| 阶段 4 · 样式体系 | ⏳ 进行 | — |

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

## 阶段 2 · 组件拆分 ✅

| Tab | 债务 | 结果 | 抽出的文件 |
|---|---|---|---|
| ReaderTab | F2 | 965 → 677 行 | `ReaderMarkdown.jsx`（统一 md 渲染，单组件导出）、`utils/readerText.js`（formatDate/excerptOf）、`ReaderAiPanel.jsx`（问答浮层自持全部 QA 态，仅收 aiEnabled/activeArticle/showToast） |
| FetchTab | F2 | 1190 → 1007 行 | **`RunningWidget.jsx`（消灭内嵌版/浮动版整段复制——本阶段首要目标）**、`NodeGroupModal.jsx`（采集范围编辑弹窗，自持搜索态；保存逻辑经 onSave 留父） |
| SettingsModal | F2 | 774 → 135 行 | `settings/`：`SectionPrimitives.jsx`（SectionHeading/FieldRow）+ Account/Appearance/Vector/Integration/DataSync/About 六个 Section（各自携带专属 helper） |
| AdminOpsTab | F2/f9 | 958 → 935 行 | `admin/adminUtils.js`（KPI_COLOR/PURPOSE_LABELS/formatStamp/fmtNum/pct/truncLabel）、`admin/adminShared.jsx`（ChartPanel/StatCard/**PanelHeader** 收敛重复 4 遍的卡片头，即 f9） |

**共享化收益**：`RunningWidget` 消灭 FetchTab 内唯一的整段 JSX 复制；`Modal.jsx` 的 a11y 惠及新 `NodeGroupModal`；`ReaderMarkdown` 供正文/译文/问答复用。

**分档暂缓（本阶段不做，风险/收益不划算，记入 backlog）**：
- **FetchTab `renderSourceRow`（~170 行）** 抽 `SourceRow.jsx`：需透传 ~13 个 props（health/running/progress/选择/展开态 + 一串 setter/回调 + renderCatalogParamInput 依赖 fetchConfigs），且不连带抽 catalog 网格仍到不了 600 行——高 prop 面、易引入回归，暂缓。
- **AdminOpsTab 三子页（AI/用户/内容）+ 4 个 Portal 弹窗** 抽独立文件：三子页与顶层 state/handler 深度耦合（globalAi/llm/usage/accounts/detail…），拆分需大面积提升 state 或穿 props；AdminOpsTab 已是 admin-only 独立懒加载 chunk，收益有限、风险高，暂缓（仅做了安全的 util/展示件/头部抽取）。
- **FetchRunsTab CollectionJobModal**：采集任务弹窗含节点选择 + 逐节点参数 + 分级 cron + downstream policy，草稿态复杂度高于 NodeGroupModal，暂缓。

**验收**：`npm run lint` 0 error / 6 warning（未变，属阶段 3）；`npm run build` 零警告；主包 gzip 75.71 kB（稳定）；各 Tab chunk 随拆分微调但按需加载不变。新增 11 个文件，逻辑 diff 以「移动」为主。待手工冒烟：阅读器问答浮层（多轮/范围切换/放大/新对话）、节点采集范围新建/编辑/保存、设置各分区、运维三子页 + 重置密码/新建/详情弹窗。

---

## 阶段 3 · 数据加载模型统一 ✅

| 项 | 债务 | 状态 | 说明 |
|---|---|---|---|
| 抽 `useAbortableLoad` | F4 | ✅ | `hooks/useAbortableLoad.js`：`run(fn)` 发新弃旧 + 卸载自动中止 + AbortError 静默（返回 undefined 表「被取代」）。DataTab（列表 + 详情两个实例）与 ReaderTab 列表迁入；FetchRunsTab 保留其请求序号（`loadRequestRef`）——它 Promise.all 5 个未接 signal 的接口，abort 化过于侵入，仅补齐依赖修警告。 |
| DataTab 加载模型对齐 | F5 | ✅ | `loadArticles` 改 `useCallback([filters, appliedSearch, runList, showToast])`；搜索拆 `searchInput`（即时）/`appliedSearch`（提交式，回车/清除时更新）；删除 `activeFilterKey` 手拼串 + `listFilterKeyRef` + `searchReloadTick` 三处 hack，改用单一「loaderRef 区分筛选变化 vs 翻页」驱动 effect。行为等价（提交式搜索 / 越界页修正 / dirty 重载 / pendingFilter 均保留）。 |
| lint 警告清零 | — | ✅ | **`npm run lint` 0 error / 0 warning**（原 6 条：DataTab×3 随 F5 消除；FetchTab×1 + FetchRunsTab×2 补齐 `setView`/`loadAll`/`onRunsRefreshed`/`onPendingFilterApplied` 稳定回调依赖）。 |
| App.jsx 聚焦通道泛化 | F6 | ✅ | 三组 `pending*` state + 三个 clear 回调 + `applyFocus` 三分支 → 单一 `pendingFocus {tab,payload}` + `clearPendingFocus`；子组件 prop 由 `pendingFocus?.tab===X ? payload : null` 派生（子组件 API 不变）。焦点对象 `{kind}` → `{tab}`，history.state 回放 `applyFocus` 收敛为单行。 |

**验收**：`npm run lint` **0/0**；`npm run build` 零警告；主包 gzip 75.68 kB（稳定）。待手工冒烟：知识台账筛选/搜索（打字不加载、回车才查）/翻页/清除筛选/越界页；阅读器快速切源不串列表；跨页跳转三链路（台账→节点定位、节点→运行历史、健康→失败记录）+ 浏览器前进后退逐级回退。

---

## 阶段 4 · 样式体系 ⏳

待执行：字重回落（f8，改 index.css 角色类）、`index.css` 按分区拆文件（f7，纯移动 + build CSS diff 校零）、legacy bridge 退役步骤 A（F8，ESLint 护栏冻结增量 + 存量清单入册）。
