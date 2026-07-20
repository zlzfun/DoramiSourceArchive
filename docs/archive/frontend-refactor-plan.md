# 前端重构路线（书面化）

> 性质：**重构路线**（分阶段执行计划），非现状诊断。
> 来源：一轮全量前端代码检视（架构 / 可读性 / 工程健康 / 样式体系 / UI·UX），结论均经代码核实带 `file:line` 锚点。
> 评估对象：`frontend/src/`。日期：2026-07-03。
> 基线状态：`npm run lint` 0 error / 6 warning；`npm run build` 通过（1 条 CSS 警告）；产物 `index.js` 1,024.73 kB（gzip 294.25 kB）。
> 逐项进度追踪：执行启动后建 `frontend-refactor-progress.md`（活文档，每增量更新状态与提交映射，体例同后端）。

## 0. 总原则

1. **先摘低垂果实（分包/构建警告/明显 UX 硬伤），再做基建收敛，然后组件拆分，最后样式体系与桥接债清理。** 每一步独立可回退，不与后一步耦合。
2. **体系侧（令牌、护栏、暗色、角色分离）方向正确，不推翻任何现有设计决策**；本轮只把组件工程（文件粒度、样板收敛、分包、加载模型）拉齐到体系侧的水准。
3. **每阶段以「不改变外部行为/视觉」为先**。前端无自动化测试，每阶段验收 = `npm run lint`（目标零警告递减）+ `npm run build`（零警告）+ 双角色（admin / 读者账号）双主题（亮/暗）手工冒烟：登录 → 各 Tab 切换 → 核心操作各一次。
4. **遵守既有纪律**：改样式前对照 `docs/frontend/conventions.md`；令牌/角色类唯一事实来源是 `frontend/src/index.css`；ESLint 设计护栏（`dorami/no-hardcoded-style`）保持 error 级不放松。

---

## 1. 现状债务摘要

八类结构性债务，均经代码核实：

| # | 债务 | 核实依据（`file:line`） | 严重度 |
|---|---|---|---|
| F1 | Bundle 1 MB 未分包：recharts（仅管理员用）与 react-markdown 全进主包，登录页即全量下载 | `npm run build` 输出 `index-*.js 1,024.73 kB`；recharts 仅 `components/charts/DashboardCharts.jsx`（AdminOpsTab 专用）引用 | 高 |
| F2 | 大组件单文件多职责，已出现整段 JSX 复制 | `FetchTab.jsx` 1190 行（running-widget 在 `1015-1055` 与 `1156-1187` 复制两遍）；`ReaderTab.jsx` 965；`AdminOpsTab.jsx` 958；`FetchRunsTab.jsx` 876 | 高 |
| F3 | `api.js` 806 行中约 600 行为同构样板（`apiFetch → handleApiError → res.json()` 复制） | `api.js` 全文；良性例外：`pollJob`（`api.js:558`）已抽象 | 中 |
| F4 | 竞态防护三种实现各自为政 | ReaderTab `listAbortRef`+`activeIdRef`（`ReaderTab.jsx:136-142`）；DataTab 双 abortRef（`DataTab.jsx:38-39`）；FetchRunsTab 请求序号 `loadRequestRef`（`FetchRunsTab.jsx:120`） | 中 |
| F5 | DataTab 加载模型脆弱：手拼 `activeFilterKey` + `searchReloadTick` 自增 hack，贡献 6 条 lint 警告中的 3 条 | `DataTab.jsx:56, 118-127, 194-232`；对照 ReaderTab 的干净实现 `ReaderTab.jsx:318-365` | 中 |
| F6 | App.jsx 跨页聚焦机制线性膨胀：每种跳转 = 1 个 pending state + 2 个回调 + 1 个 applyFocus 分支 | `App.jsx:92-165`（已有 dataFilter / runsFilter / fetchFocus 三组） | 中 |
| F7 | Modal 无 Esc 关闭、无焦点管理；折叠栏 `aria-hidden` 内仍有可聚焦元素 | `Modal.jsx` 全文无 keydown；AdminOpsTab 三个 Portal 弹窗同缺（`AdminOpsTab.jsx:784/838/868`）；`ReaderTab.jsx:497,614` | 中 |
| F8 | 样式桥接债：后代选择器改写 utility 语义（`.surface-card .bg-white ≠ 白`），暗色侧已用更高特异性压回，形成军备竞赛 | `index.css:5262-5331`（暗色段注释自述 0,2,0 压过 `dark:` 工具类、再用 0,3,0 压回） | 中（长期） |

**次级问题清单**（随所属阶段顺带处理）：

| # | 问题 | 锚点 |
|---|---|---|
| f1 | Tailwind v4 自动内容探测扫到 `eslint.config.js:16` 护栏文案中的示例 class，构建期生成非法 CSS 并报警 | build 输出 `Unexpected token Delim('\|')` |
| f2 | `window.prompt` 重置密码：明文回显、与全站 Modal 体系不符、部分环境不可用 | `AdminOpsTab.jsx:305` |
| f3 | `runtimeInfo` 三处初始化/重置字段不一致（重置时丢 `ai_beta_enabled`/`llm_configured`，靠 undefined-falsy 侥幸正确） | `App.jsx:87,245,274` |
| f4 | 领域 helper 跨文件复制且已分叉：`collectionRunMessage`（两版签名不同）、`normalizeIds`、`TEST_RUN_LIMIT` | `FetchTab.jsx:55,103-113` vs `FetchRunsTab.jsx:77-88` |
| f5 | ReaderTab 正文/译文缓存无上限（`bodyCacheRef`/`translationCacheRef` 随阅读无限增长） | `ReaderTab.jsx:125,138` |
| f6 | Toast 单槽无队列，连续操作丢消息；error 与 success 同为 3s、无手动关闭 | `App.jsx:189-194` |
| f7 | `index.css` 5702 行单文件，组件与样式相距过远（reader 段 4246-4816 与 `ReaderTab.jsx` 分离） | `index.css` 分区注释 |
| f8 | 字重普遍偏重（正文/meta/按钮大量 bold/black），层级钝化、小字号中文糊化 | `index.css` 角色类 + 各组件 |
| f9 | 分区色条语汇已在 MCPTab 组件化（`GroupHeader`）但 AdminOpsTab 手写 4 遍 | `MCPTab.jsx:45-53` vs `AdminOpsTab.jsx:448/536/595/731` |
| f10 | AdminOpsTab 两个独立时间窗口状态（`userDays`/`usageDays`）且两处 select 样式不一致 | `AdminOpsTab.jsx:136,156,492,540` |

---

## 阶段 0 · 低垂果实（P0，半天级）

**目标**：一次提交量级消掉最大性能问题与最刺眼的三处硬伤。零结构改动。

1. **AdminOpsTab 懒加载分包**（F1）：`App.jsx` 改 `const AdminOpsTab = lazy(() => import('./components/AdminOpsTab'))` + `<Suspense fallback>`（复用现有 checking-state 视觉）。App.jsx 已按 `mountedTabs` 首次激活才挂载，与 `lazy` 天然契合。recharts 随之切出主包。读者与登录页不再下载运维图表库。
   - 顺手评估 `ReaderTab`（react-markdown 链）同法拆出；若首屏收益不明显可只拆 AdminOpsTab。
2. **修构建 CSS 警告**（f1）：`index.css` 顶部加 `@source not "../eslint.config.js";`（或改写 `eslint.config.js:16` 提示文案为 `rounded-[var(--r-*)]` 使其不再被匹配）。
3. **`window.prompt` → 重置密码小弹窗**（f2）：复用 `useModalTransition` + `modal-overlay/modal-panel` 结构（同 AdminOpsTab 现有三个 Portal 弹窗），输入框 `type="password"` + 至少 6 位校验。
4. **`INITIAL_RUNTIME` 常量**（f3）：抽模块级常量，`App.jsx:87/245/274` 三处共用。

**验收**：`npm run build` 零警告；构建产物主 chunk 明显缩减（预期 −300~400 kB min）并出现独立的 admin/charts chunk；读者账号登录后 Network 面板确认不加载 admin chunk；重置密码走弹窗且不回显明文。

---

## 阶段 1 · 基建收敛（api 层 + Modal 可访问性 + 共享 helper）

**目标**：把「每处自己写一遍」的三类样板收敛到单点。不动组件结构。

1. **`api.js` 收敛 `request()` helper**（F3）：

   ```js
   async function request(path, { method = 'GET', body, errorMsg, ...opts } = {}) {
     const res = await apiFetch(`${API_BASE_URL}${path}`, {
       method,
       ...(body !== undefined && {
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify(body),
       }),
       ...opts,
     });
     if (!res.ok) await handleApiError(res, errorMsg);
     return res.json();
   }
   ```

   全部导出函数改为一行式包装，**导出名与签名不变**（调用方零改动）。特殊路径保留原样：`exportArchiveArticles`（`res.text()`）、`importArchiveArticlesJsonl`（ndjson body）、`recordArticleRead`（fire-and-forget）、`fetchRunningProgress`/`fetchAuthSession`（失败静默返回默认值）、`pollJob` 三个封装。预期文件从 806 行降到 ~400 行以内。
2. **Modal 统一补键盘/焦点/语义**（F7）：在 `Modal.jsx`（或 `useModalTransition` 层）统一实现：`keydown Escape → onClose`、打开时焦点移入面板、简单 focus trap（Tab 循环）、`role="dialog" aria-modal="true"`。AdminOpsTab / SettingsModal 的手写 `modal-overlay` Portal 弹窗迁移到统一 `Modal` 组件（顺带消除它们各自重复的 body 滚动锁 effect，`AdminOpsTab.jsx:198-203` / `SettingsModal.jsx:697-703`）。
3. **折叠栏加 `inert`**（F7）：`ReaderTab.jsx:497,614` 在 `aria-hidden` 之外补 `inert={sourcesCollapsed || undefined}`（React 19 原生支持），消除「隐藏列仍可 Tab 聚焦」的违例。
4. **抽 `utils/collection.js`**（f4）：`collectionRunMessage`（统一为带可选 `successCount` 的单一签名）、`normalizeIds`、`blankGroup` 类构造、`TEST_RUN_LIMIT`，`FetchTab.jsx` 与 `FetchRunsTab.jsx` 改引用。
5. **Toast 小改**（f6，可选并入本阶段）：error 类型停留 5-6s + 关闭按钮；队列暂不做（见「暂缓」）。

**验收**：lint/build 双绿；所有弹窗 Esc 可关、打开后焦点在面板内；`api.js` 调用方零 diff；两个采集 Tab 的运行结果 toast 文案与改前一致。

---

## 阶段 2 · 组件拆分（按子视图 = 子文件）

**目标**：四个大 Tab 拆到「单文件单职责」，消灭复制的 JSX。**只搬代码不改行为**，每拆一个跑一遍冒烟。

1. **FetchTab（1190 → 预期 ~400 行壳）**（F2）：
   - 先抽 `RunningWidget.jsx`（消灭 `1015-1055`/`1156-1187` 的整段复制，props：`runningFetcherIds`/`fetchProgress`/`fetchersById`/`onViewRunning`/`variant`）；
   - 再拆 `FetchCatalog.jsx`（目录 + 双维筛选 + `renderSourceRow`）、`NodeGroupsView.jsx`（采集范围列表）、`NodeGroupModal.jsx`（范围编辑弹窗）；
   - 运行进度轮询逻辑（`FetchTab.jsx:174-210`）抽 `hooks/useRunningProgress.js`，供拆出的子组件共享。
2. **ReaderTab（965 → 预期 ~500 行壳）**：抽 `ReaderAiPanel.jsx`（问答浮层 + scope 下拉 + 多轮线程 + composer，约 150 行，与主体只共享 `activeArticle`/`aiEnabled`）；`MarkdownImage`/`MARKDOWN_COMPONENTS`/`excerptOf` 抽 `readerMarkdown.jsx` 共享模块（日报面板同可复用）。
   - 顺带（f5）：`bodyCacheRef`/`translationCacheRef` 加「超过 100 条删最旧」的简单 LRU。
3. **AdminOpsTab（958 → 壳 + 三子页 + 弹窗）**：`admin/AiOpsPanel.jsx`、`admin/UserOpsPanel.jsx`（含账户列表/分页/详情抽屉）、`admin/ContentPanel.jsx`、`admin/LlmConfigModal.jsx`、`admin/UserActivityModal.jsx`；`PURPOSE_LABELS`/`KPI_COLOR`/`fmtNum`/`pct`/`formatStamp` 归入 `charts/chartUtils.js` 或新 `admin/adminUtils.js`。
   - 顺带（f9）：分区色条统一复用 `GroupHeader`（从 MCPTab 提升为共享组件）。
   - 顺带（f10）：两个时间窗口 select 统一外观；是否合并为单一窗口状态由产品意图决定，默认仅统一样式。
4. **FetchRunsTab（876）**：拆 `CollectionJobModal.jsx` 与 `RunHistoryView.jsx`。
5. **SettingsModal（774）**：内部 5 个 Section 组件已拆好，机械分文件到 `settings/` 目录即可。

**验收**：无单文件 >600 行（`index.css` 除外，见阶段 4）；`rg -n "running-widget" src/components` 只命中单一组件；双角色双主题冒烟通过；git diff 以「移动」为主、逻辑 diff 接近零。

---

## 阶段 3 · 数据加载模型统一（hook 化 + 消 lint 警告 + 聚焦通道泛化）

**目标**：竞态防护与跨页跳转从「模式约定」变成「基建保证」；lint 警告清零。

1. **抽 `hooks/useAbortableLoad.js`**（F4）：封装「发新弃旧（AbortController）+ 卸载中止 + AbortError 静默 + loading 归位守卫」。ReaderTab / DataTab / FetchRunsTab 三处迁移到同一 hook（FetchRunsTab 的请求序号模式一并替换）。不引入 TanStack Query（见「暂缓」）。
2. **DataTab 加载模型对齐 ReaderTab**（F5）：`loadArticles` 改 `useCallback`（依赖 filters 对象本身），列表刷新由 effect 依赖驱动，删除 `activeFilterKey` 手拼字符串与 `searchReloadTick` hack；搜索保持提交式语义（回车/清除触发 `setSearchQuery` 类的已提交态，参照 `ReaderTab.jsx:109-110` 的 input/query 双态）。**消掉 DataTab 的 3 条 `exhaustive-deps` 警告。**
3. **FetchRunsTab / FetchTab 的剩余 3 条警告同法处理**（`setView`/`onRunsRefreshed` 等回调依赖：上游 App.jsx 已是 useCallback，直接补进依赖数组即可）。
4. **App.jsx 聚焦通道泛化**（F6）：三组 `pending*` state + 回调对收敛为单一 `pendingFocus: { tab, view, payload } | null` + `clearPendingFocus`；目标 Tab 自行解释 payload（现有 `focus.kind` 字段保留为 payload 判别）。`history.state` 回放（`applyFocus`）同步简化为单分支。

**验收**：`npm run lint` **0 error / 0 warning**（此后可考虑 CI 把 warning 升 error）；三个列表页快速切换筛选/来源无「后发先至」回归（手工快速连点验证）；跨页跳转（台账→节点定位、节点→运行历史、健康→失败记录）三条链路全部回放正常，浏览器返回逐级退回。

---

## 阶段 4 · 样式体系（字重回落 + CSS 拆分 + 桥接债退役）

**目标**：视觉层级修复 + 样式可维护性 + 拔掉特异性军备竞赛的引信。本阶段有可见视觉变化，需逐页核对。

1. **字重回落**（f8）：正文与次要信息从 bold/extrabold 回落 medium/normal，粗体只留标题、KPI 数字、激活态。優先改 `index.css` 角色类（`page-subtitle`、meta 类、表格单元格），一处改全局生效；JSX 内联的 `font-bold text-xs` 类在触碰到的文件里顺带回落，不做全局扫荡（避免巨型 diff）。改前后各截一套双主题关键页对比图存档。
2. **`index.css` 按分区拆文件**（f7）：按现有分区注释机械切分为 `styles/tokens.css`（`:root`/`@theme`/暗色令牌）、`styles/base.css`（scrollbar/keyframes/animate 助手）、`styles/components.css`（通用 `@layer components`）、`styles/catalog.css`、`styles/reader.css`、`styles/auth.css`、`styles/dark.css`，`index.css` 只留 `@import` 序列与 `@custom-variant dark` 接线。**纯移动，禁止顺带改值**；拆分前后 build 产物 CSS 做 diff 确认零变化。
3. **桥接债退役（分两步，第二步可延到下一轮）**（F8）：
   - **步骤 A（本阶段做）**：冻结增量——ESLint 护栏新增一条规则：组件类名中禁止新写 `bg-white`/`bg-slate-50*`/`bg-indigo-50`/`border-slate-100|200`/`border-indigo-200`（提示改用 `--dorami-*` 令牌类），先以 warn 级落地；把现有命中清单（`rg` 一次性统计）登记进 progress 文档。
   - **步骤 B（存量清零后）**：逐文件把存量工具类替换为令牌类 → 全部清零后删除 `index.css:5262-5331` 亮暗两段桥接 → 护栏升 error。每替换一批跑双主题冒烟（桥接曾是暗色发白问题的补丁，删除顺序必须是「先替换后删桥」）。

**验收**：双主题逐页目检通过（重点：暗色下卡片/弹窗内嵌区无「发白」回归）；build CSS 拆分前后 diff 为零（第 2 项）；护栏新规则上线且存量清单入册；字重对比图评审通过。

---

## 暂缓 / 不做（本轮明确出局）

| 项 | 理由 |
|---|---|
| 迁移 react-router | 手写 hash 路由（`App.jsx:31-59`）注释充分、行为正确、体量小；仅当出现「文章详情可分享 URL」类需求时整体迁移，不做半吊子替换 |
| 引入 TanStack Query | 当前规模下自定义 `useAbortableLoad` 足够；引入换来缓存/重试的同时带来 invalidation 心智与包体，收益不成比例 |
| Toast 队列 | 单槽覆盖在现有操作密度下可接受；阶段 1 的 error 延时 + 关闭按钮已覆盖主要痛点 |
| DataTab 窄屏卡片化 | 管理员桌面为主，`min-w-[980px]` 横滚可接受；出现平板运维场景再评估 |
| 看板「hover 才显数字」改常显 | 属审美决策（Grafana 式）而非缺陷；如需调整走产品讨论，不进重构 |
| 顶栏 tab 改 `role=tablist` 语义 | 收益有限且涉及键盘导航模型（方向键切换）整套约定，与本轮解耦，记入 backlog |

---

## 执行顺序与节奏

```
阶段 0（半天）→ 阶段 1（1-2 天）→ 阶段 2（2-4 天，按 Tab 分 4-5 个独立提交）
→ 阶段 3（1-2 天）→ 阶段 4（1-2 天 + 桥接步骤 B 长尾）
```

- 每阶段一个（或阶段 2 内每 Tab 一个）独立提交，消息带阶段标号（如 `refactor(frontend): 阶段2 拆分 FetchTab（F2）`）。
- 每次增量后更新 `frontend-refactor-progress.md`：状态（待办/进行/完成/暂缓）+ 提交哈希 + 验收结论。
- 阶段 0/1 与后端任何工作无冲突可并行；阶段 2-4 建议在无并行前端 feature 分支时进行，避免大范围移动代码造成合并地狱。
