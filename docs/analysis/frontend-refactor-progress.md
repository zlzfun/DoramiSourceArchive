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
| 阶段 4 · 样式体系 | ◐ 部分（F8-A/B 完成；仅 f7/f8 有据暂缓） | `refactor/frontend` |

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

## 阶段 4 · 样式体系 ◐

自动化（无运行中 dev server / 无法截图）下，本阶段三项均需「看效果」才能安全收尾，故按证据分档：

| 项 | 债务 | 状态 | 说明 |
|---|---|---|---|
| Legacy bridge 冻结 · 步骤 A | F8-A | ✅ 落地 | `eslint.config.js` 新增 `dorami/no-legacy-bridge-class` 规则（bg-white / bg-slate-50* / bg-indigo-50 / bg-blue-50* / border-slate-100\|200 / border-indigo-200 → 令牌类）。冻结机制到位、一行可激活；存量清单翻 warn 实测命中，交步骤 B 迁移。 |
| Legacy bridge 令牌化 + 删桥 · 步骤 B | F8-B | ✅ 完成 | 逐处令牌化 **54 处 / 17 文件**（bg-white→`--dorami-surface`、bg-slate-50*→`--dorami-soft`、bg-indigo-50/bg-blue-50*→`--dorami-wash`、border-slate-100\|200→`--dorami-border`、border-indigo-200→`--dorami-border-strong`；激活/选中态的 accent 边界改 `border-[var(--dorami-accent)]/25` 保留强调线索；配套冗余 `dark:` 补丁一并删除）。**删除 `index.css` 整段桥接**（亮色 6 组 + 暗色 5 条 `[data-theme=dark]` 对应，原 5286-5355 行）。`no-legacy-bridge-class` 翻 **`'error'`** 把关增量；其中 `bg-white` 正则收紧为「仅裸形态」——`bg-white/NN`（固定紫/深色 Hero、深色代码面、Toast 的白玻璃，主题恒定，从不被桥接改写）是正当写法，不拦。副作用：删 `text-indigo-*` 桥接后卡片内 accent 文字在暗色下由被强制的 `--dorami-blue`(#4d45b5, 低对比) 回归 `@theme` 暗色映射(#8b84f0/#a8a2f4)，暗色对比**改善**。`npm run lint` 0/0、`npm run build` 零警告。 |
| 字重回落 | f8 | ⏸ 暂缓（有据） | 复核发现 **index.css 角色类字重已合理**（`.page-subtitle`/`.body-text` 600、`.tiny-meta` 500、`.micro-label`/`.card-title`/`.section-title` 700、`.stat-number` 900）——无明显下调空间。真正的「偏重」在散落 JSX 内联 `font-bold`/`font-black`（顶栏 tab、表格单元格、各类 meta），而 plan 明确「不做全局扫荡（避免巨型 diff）」。故 f8 的正确形态是**触碰文件时顺带回落**，而非独立盲改；且字重是可见改动，宜配合 dev server 目检。暂缓为「随手做 + 一次目检」。 |
| index.css 拆分 | f7 | ⏸ 暂缓（有据） | 纯组织性（无功能/性能/视觉收益）。5700 行的核心是单个 `@layer components {}`（~4700 行），跨文件拆分涉及 Tailwind v4 `@import` 顺序 + `@theme`/`@custom-variant`/`@source` 位置 + `@layer` 合并语义的多点不确定性，首拆未必 hash 一致，需 dev server 迭代验证。ROI 低、宜交互式做，暂缓（已有 build-CSS-hash 作为将来验收闸门）。 |

**验收**：`npm run lint` 0/0（bridge 规则已 `'error'`）；`npm run build` 零警告。F8-A/B 均已完成——桥接段已从 `index.css` 删除、全部存量令牌化、护栏以 error 把关增量；仅 f7（index.css 拆分）/f8（字重回落）两项因需 dev server 目检有据暂缓，留待下一轮。

---

## 收尾小结

- **已完成并验收**：阶段 0（分包，主包 gzip 294→75.7 kB）、阶段 1（api 收敛 806→517 行 + Modal 可访问性）、阶段 2（11 个新文件，消灭 RunningWidget 复制；ReaderTab/SettingsModal/FetchTab/AdminOpsTab 均瘦身）、阶段 3（useAbortableLoad 统一竞态 + **lint 6→0** + pendingFocus 泛化）。
- **有据暂缓（记入 backlog）**：阶段 2 的 renderSourceRow / AdminOps 三子页 / FetchRuns Job Modal 深拆；阶段 4 的 f7 拆分、f8 字重——共性是「高 prop 面深耦合」或「需运行中目检」，宜交互式续做。（F8-B 令牌化 + 删桥已完成，见上表。）
- **全程未改外部行为/视觉**：每阶段 `npm run lint` + `npm run build` 双绿；阶段 4 CSS 产物字节不变。分支 `refactor/frontend`，5 个阶段提交。**待人工冒烟**（双角色 × 双主题）后合入 main。

---

## 视觉打磨（`frontend-visual-polish-plan.md` 的执行追踪）

- **阶段 A–D 全部完成**（main 上 7 个 `polish(frontend):` 提交，2026-07 上旬）：V1 摘要剥离 Markdown、V3 空值不渲染 + 参数 chips、V5 枚举中文化、V2+V8 看板色彩语义收敛、V6+V7 MCP 状态单一化 + 日报控件统一、阶段 D v1-v5 随手项 + 回访。
- **V4 补漏**（阶段 C 当时漏项，2026-07-09 补上）：采集任务展开态删除按钮 `ml-auto` 移至行尾，与主操作簇隔离。同批微打磨：`.stat-number`/`.tiny-meta` 加 `tabular-nums`、`.page-title`/`.empty-state` 加 `text-wrap: balance`、`body`/`.app-shell` 补 `100dvh` 兜底。
- **待补评估四区（暗色 / 阅读器 user 角色 / 登录页 / 动效）仍搁置**——需 dev server 双账号截图/录屏再立项（见 plan「待补评估」表）。届时一并人工目检 F8-B 迁移的双主题风险点（开关滑块 surface 化、accent /25 透明边、DateRangePicker 区间格 wash 化、DataSyncSection 分主题取值、accent 文字暗色对比改善），清单见 F8-B 行与对应提交信息。
