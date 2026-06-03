# 前端优化方案（frontend-polish 专项）

> 分支：`frontend-polish`　基线：`main@dd466f5`
> 范围：`frontend/src`（约 9.8k 行：React 19 + Vite 8 + Tailwind v4 + lucide-react，无路由/状态库/组件库）
> 目标：在**不改动后端契约、不改变现有视觉语言**的前提下，降低维护成本、消除重复与死代码、补强体验与健壮性。

---

## 0. 现状基线

| 组件 | 行数 | useState | useEffect | 状态 |
|---|---|---|---|---|
| FetchTab | 1209 | 22 | 7 | 巨型 |
| FetchRunsTab | 845 | 15 | 5 | 偏大 |
| SubscriptionTab | 755 | 14 | 2 | 偏大 |
| SettingsModal | 650 | 14 | 4 | 偏大 |
| SourcesTab | 492 | 9 | 2 | **死代码** |
| DataTab | 487 | 12 | 4 | 中等 |
| index.css | 3090 | — | — | 单文件，全量 `@layer components` |

设计上做得好、**本方案不动**的部分：`api.js` 单一网络出口、`401 → dorami-auth-expired` 事件解耦、运行时角色/RAG 开关驱动 tab 与列、`config.js` 单点配置。

---

## 阶段一：清理与去重（低风险 · 高收益 · 先手）

### P1-1　删除死代码 `SourcesTab.jsx`
- **依据**：全仓 grep 仅命中自身定义，`App.jsx` 与 `CLAUDE.md` 组件清单均未列入；无动态/字符串引用。
- **动作**：删除 `frontend/src/components/SourcesTab.jsx`（492 行）。
- **风险**：极低。　**工作量**：~5 分钟。

### P1-2　抽取共享工具层 `src/utils/`
- **依据**：
  - `formatDateTime` 重复定义于 `FetchTab.jsx:75`、`FetchRunsTab.jsx:37`；`formatDate` 又单写于 `DateRangePicker.jsx:10`。
  - 剪贴板写入 `navigator.clipboard.writeText(...)` + fallback 在 **4 处**重复（SubscriptionTab / VectorTab / SettingsModal / MCPTab）。
- **动作**：新建 `src/utils/datetime.js`（`formatDate` / `formatDateTime` / `formatRelativeTime`）与 `src/utils/clipboard.js`（`copyToClipboard(text): Promise<boolean>`，内聚 fallback）。逐处替换。
- **风险**：低（纯函数，行为等价替换）。　**工作量**：~1 小时。

### P1-3　统一异步动作包装器
- **依据**：handler 内 `catch (e) { showToast(e.message || '网络异常', 'error'); }` 样板大量重复（DataTab 单文件 7 次）。
- **动作**：新建 `src/utils/runAction.js`，签名约 `runAction(fn, { showToast, success, error, setLoading })`，统一 try/catch + toast + loading 收尾。先在 DataTab 试点，再逐组件推广。
- **风险**：低（保持每处文案，仅收敛控制流）。　**工作量**：~1.5 小时（含试点）。

**阶段一验收**：`npm run lint` 通过、`npm run build` 通过；人工冒烟各 tab 的增删改 + 复制 + toast 文案不变。

---

## 阶段二：健壮性与体验补强（中风险 · 中收益）

### P2-1　列表查询竞态保护
- **依据**：`DataTab.loadArticles` 由筛选 useEffect 连续触发，快速切换筛选可能「后发先至」导致结果与筛选不一致；`api.js/apiFetch` 无 `AbortController`、无超时。
- **动作**：给列表类查询引入请求序号（或 `AbortController`），仅采纳最新一次响应；`apiFetch` 可选 `signal` 透传。
- **风险**：中（触碰核心网络层，需回归各列表）。　**工作量**：~2 小时。

### P2-2　统一确认弹窗替换原生 `window.confirm`
- **依据**：批量删除等危险操作用 `window.confirm`（DataTab:197 等），与品牌视觉割裂、移动端体验差。
- **动作**：新建 `ConfirmDialog` 组件（复用现有 `.modal` 样式），提供 `useConfirm()` 或受控用法。替换所有 `window.confirm`。
- **风险**：中（涉及危险操作交互，须确保「取消」默认安全）。　**工作量**：~2 小时。

### P2-3　列表加载骨架屏
- **依据**：`index.css` 已定义 `.skeleton` shimmer，但列表加载仅转一个图标，长查询体感差。
- **动作**：为 DataTab / FetchRunsTab 等列表补骨架行；首屏 loading 用骨架替代空白。
- **风险**：低。　**工作量**：~1.5 小时。

### P2-4　可访问性补齐
- **依据**：表格 checkbox 无 `aria-label`（DataTab:368/386）；图标按钮 aria 标注在 App.jsx 规范、子组件不统一。
- **动作**：补齐 checkbox / 图标按钮的 `aria-label` 与 `title`；确认弹窗补 focus trap 与 `Esc` 关闭。
- **风险**：低。　**工作量**：~1 小时。

**阶段二验收**：键盘可完成「全选 → 批量删除 → 确认/取消」；快速切换筛选无错位结果；构建通过。

---

## 阶段三：结构性重构（高工作量 · 需取舍）

> **裁决（2026-06-03，阶段一/二落地后复盘）**：阶段三整体**不立即执行**。阶段一/二已吃完高 ROI 项；阶段三为纯重构区——无用户可见收益、回归成本最高且集中在采集核心链路。逐项裁决见下。

### P3-1　巨型组件拆分　——【按需触发，绑定下次 FetchTab 特性】
- **目标**：`FetchTab`(22 state) 按子区拆为「抓取器目录 / 节点分组 / 参数表单 / 运行中浮窗」容器 + 展示组件；`FetchRunsTab`/`SubscriptionTab`/`SettingsModal` 同法。
- **裁决**：`FetchTab`(1198 行 / 22 state) 是真实维护隐患，但"行为等价重构"= 高回归 + 零用户收益。**不为重构而重构**：FetchTab 后续仍会迭代，故在**下次给它加特性时随同增量拆分**——只抽纯展示子组件到同目录（**不搬 state**），风险远低于一次性"状态下沉"大改。`FetchRunsTab`/`SubscriptionTab`/`SettingsModal` 暂不动。
- **风险**：中高（行为等价重构，回归成本高）。　**工作量**：每个组件 0.5–1 天。

### P3-2　CSS 分域治理　——【划掉，除非进入样式持续重写期】
- **目标**：`index.css` 3090 行单一 `@layer components` 拆分为按域文件，或将一次性专用类回收为组件内 Tailwind 原子类。
- **裁决**：**不做**。纯维护性收益、零用户可见变化，而全局样式耦合 → 拆分须逐页比对防视觉回归，风险高收益低。Tailwind v4 下该 `@layer` 主要装命名组件类，拆了只是换放法。仅当后续进入"样式大改"阶段才重新评估。
- **风险**：中高（样式全局耦合，易引入视觉回归，需逐页比对）。　**工作量**：1–2 天。

### P3-3　跨 tab 状态与 prop drilling 收敛　——【否决，假设已证伪】
- **目标**：`showToast`/`runtimeInfo` 深层透传 → 引入轻量 `ToastContext` / `RuntimeContext`。
- **裁决**：**放弃**。实测 prop 透传**只有一层**（`App → 各 Tab` 为直接父子）：`runtimeInfo` 已在 App 解构成语义化 prop 传一层即用，`articlesDirty`/`pendingFilter` 是跨 tab 协调、显式 prop 比藏进 Context 更可读。引入 Context 只是用 provider/useContext 的间接性换"少传一层"，净亏。原方案"深层透传"假设不成立。
- **风险**：中（涉及 App 顶层数据流）。　**工作量**：~0.5 天。

---

## 执行原则

1. **逐阶段、逐 PR**：阶段一可合一个 PR；阶段二按特性拆；阶段三每组件/每域独立 PR。
2. **行为等价优先**：去重与重构均不改变用户可见行为与文案，便于回归。
3. **每步守门**：`npm run lint` + `npm run build` + 关键路径人工冒烟。
4. **不碰后端契约**：所有改动限于 `frontend/src`。

## 建议起步顺序

`P1-1 → P1-2 → P1-3`（同一 PR）→ `P2-1 / P2-2`（健壮性）→ 视迭代需求决定是否进入阶段三。
