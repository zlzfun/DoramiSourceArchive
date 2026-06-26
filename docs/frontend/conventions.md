# 前端开发纪律（设计与实现约定）

> 借鉴 Vercel/Geist 设计规范，结合本项目实际沉淀。**这是之后所有前端工作的纪律依据**：
> 改动前先对照本文，能引用既有 token/类的就不要手写。token/类的**单一事实来源**是
> `frontend/src/index.css`（`:root` 令牌 + `@layer components` 角色类），本文是它的索引与规则说明。
>
> 贯穿原则——**区分场景**：数据**工作区**（知识台账/节点/任务运行/阅读器/向量雷达/接入集成/各模态）
> 一律克制、即时、token 化；**品牌/登录页**的电影感（aurora/流星/光束/逐词浮现）是刻意的品牌表达，
> 不受这些约定约束，勿"收敛"。

## 1. 文案（Voice & Content）

- **Toast 点名"变了什么"，不说"成功"，不带句号**：用"已 + 动作 + 对象"。
  ✅ `已订阅 机器之心`、`已删除 3 篇文章`、`已建立向量索引` ❌ `操作成功`、`删除成功。`
  （运行状态枚举标签如"成功/运行中/失败"是状态名，不在此列，保留。）
- **错误 = 发生了什么 + 下一步怎么办**：优先透传后端 `error.message`，兜底文案给原因+动作。
  ✅ `加载失败：后端未响应，请确认服务已启动后重试`、`复制失败，请手动选择文本复制`
- **空状态指向第一个动作**，不要冷冰冰的"暂无数据"。
  ✅ `还没有采集范围，点右上角「新建采集范围」创建第一个`
- **进行中态用现在进行式 + 省略号**：`生成中…`、`保存中…`、`抓取中…`。
- **动作命名用"动词+名词"**，不要"确认/确定/OK"：`删除文章`、`导出归档`。
- 读者可见文案不得泄露内部架构术语（归档/采集/分发/层）；副本按 readerOnly/isAdmin 分流。

## 2. 可访问性（Accessibility）

- **每个交互元素都要有 `:focus-visible` 焦点环**。原生 `button/input/select/textarea`
  已有全局焦点环（`index.css` 顶部）；自定义可点元素（裸 `div`、容器内 `outline:none` 的输入）
  必须自带替代焦点指示（参考 `.field-box:focus-within`、`.node-param-input:focus`）。
  **绝不移除 outline 而不给可见替代。**
- **文本对比度达 WCAG AA（4.5:1）**：最弱文字用 `--dorami-faint`(#64748b) 或 `text-slate-500`，
  **不要用 `text-slate-400` / #94a3b8 作正文**（仅 ~2.7:1，不达标）。
- **状态不靠颜色单独传达**：色块必须配图标或文字标签（参考 Toast、运行状态徽标）。

#### 灰阶深浅基准（统一标准，消除「同角色不同深浅」的摆动）

**灰色文字——只用 3 档**（中间档 `slate-600` 已废，归并入「次要」；lint 会拦 `text-slate-600`/`text-slate-400`）：

| 档 | 用途 | 写法 | 值 |
|---|---|---|---|
| 强 | 标题、主单元格、关键数字 | 角色类 `.card-title`/`.section-title`（ink/ink-2）；裸文本 `text-slate-800` | #1e293b / #0b1220 |
| 正文 | 可读正文、详情字段值、列表主文本 | `.body-text` 或 `text-slate-700` | #334155 |
| 次要 | 副标题/说明/标签/meta/时间戳/表头/占位/非激活态 | `.page-subtitle`/`.tiny-meta`/`.micro-label`；裸文本 `text-slate-500` | #64748b（AA 下限）|

- `text-slate-300` 仅限**图标/分隔/装饰**与深色面（`bg-slate-9xx`）上的浅字，**不可承载需阅读的文字**。
- 复选框等表单控件的描边可保留 `border-slate-300`（控件需可见边，属已知例外）。

**灰色描边——只用 2 档**（默认就是浅的那档；`border-slate-100/200` 一律收敛到令牌）：

| 档 | 用途 | 写法 | 值 |
|---|---|---|---|
| 默认 | 卡片/输入框/分隔线/表格/chip——**绝大多数** | `border-[var(--dorami-border)]` | rgba(11,18,32,0.09)≈slate-200 |
| 强（克制）| 仅需明显分隔的少数场景，**显式选用** | `border-[var(--dorami-border-strong)]` | rgba(11,18,32,0.15) |

## 3. 排版（Typography）

正文优先用 Tailwind 字号梯度（`text-xs/sm/base` + `font-*`）；**不要手写 `text-[Npx]`**。
固定语义角色引用 `index.css` 的角色类（详见该文件 `── Typography ──` 段注释）：

| 角色类 | 用途 | 规格 |
|---|---|---|
| `.page-title` | 页面 H1 | clamp(26–32px)/900 |
| `.page-subtitle` | 页面副标题/说明 | 14px/600·muted |
| `.card-title` / `.section-title` | 卡片/区块标题 | 15px/700 · 14px/700 |
| `.body-text` | 默认正文/可读多行 | 14px/1.6/600 |
| `.tiny-meta` | 次要 meta | 12px/500 |
| `.micro-label` | 微标签/徽标/角标 | 11px/700 |
| `.stat-number` | 指标数字 | 26px/900 |

语境化标签（`.form-label`/`.node-param-label`/`.active-filter-label` 等）各自就地维护，不必归一。

## 4. 颜色令牌（语义四套，互不混用）

详见 `index.css` `:root` 顶部「设计令牌分类法」注释。

- **背景/图底**：`--dorami-canvas / canvas-2 / surface / soft / card / well / wash` —— 勿用作文字色。
- **文字/墨色**：`--dorami-ink / ink-2 / muted / faint` —— 信息层级；勿用作背景。
- **描边**：`--dorami-border / border-strong`。
- **强调/状态**：`--dorami-accent`(=`--dorami-blue`) / `blue-2` / `accent-ink` ——
  **唯一饱和色，只用于「激活/状态」与「一个视图里最重要的那个动作（CTA）」。**
- 散落的 indigo/blue 原子类已在 `@theme` 折叠为同一 accent；emerald/amber/red/slate 语义中性色照常用。

## 5. 圆角（canonical 家族）

**不要手写 `rounded-[Npx]` / `border-radius: Npx`**，引用 token（JSX 用 `rounded-[var(--r-card)]`）：

| token | 值 | 用途 |
|---|---|---|
| `--r-sm` | 6px | 微元素：徽标、代码块、角标 |
| `--r-control` | 10px | 控件：按钮、输入框、下拉、开关、小 chip |
| `--r-card` | 14px | 卡片、面板、列表容器 |
| `--r-overlay` | 16px | 模态、浮层、hero/全屏面 |
| `--r-pill` | 9999px | 胶囊、头像、圆形控件 |

一个视图内只用同一圆角家族，勿混圆与方。Tailwind 具名 `rounded-lg/xl/2xl/full` 为标准梯度可共存；
`50%`（正圆）/`0`（显式去角）等特例保留。

## 6. 高程/阴影

层级**先靠色面 + 边框**，阴影克制（参考 `--sh-1/2/3`，工作区已调淡）。新建卡片优先用 `.surface-card`
而非自定义重阴影；模态等确需悬浮的浮层才用强高程。

## 7. 动效（区分场景）

- **工作区**：功能态 ~150ms、浮层/弹层 ~200–240ms、面板/模态 ~300ms。引用 motion token：
  `--motion-fast`(150) / `--motion-medium`(240) / `--motion-slow`(300) + `--motion-ease`。
  **动效只为澄清状态变化，勿装饰**；长列表逐行 stagger 已弱化封顶，勿再加长。
- **品牌/登录页**：电影感动画（秒级、硬编码）保留，不引用上述 token，不收敛。
- 所有动画都要在 `@media (prefers-reduced-motion: reduce)` 下降级（已有兜底，新增动画须遵守）。

## 8. 组件层级与一致性

- **一个视图一个主操作**：`.action-button-primary`（渐变实心）只给该视图/卡片/表单里最重要的那一个动作；
  次要动作用 `.action-button-secondary / -quiet`，破坏性动作用 `.action-button-danger`。
- 复用既有原语（`.action-button*` / `.icon-button` / `.surface-card` / `.modal-*` / `.toast-*`），
  不要为同类元素另起样式。

## 9. 暗色主题（已落地）

暗色主题通过 `[data-theme=dark]`（挂在 `<html>`，由 `src/theme.js` 的主题控制器写入）实现，三态偏好
（亮 / 暗 / 跟随系统）存 `localStorage('dorami-theme')`、跟随 `prefers-color-scheme`；入口在顶栏快捷图标
与「设置 → 外观」。`index.html` 有防闪烁内联脚本（React 挂载前先打 `data-theme`）。落地分三层（全部集中在
`index.css` 末尾的 `[data-theme=dark]` 段，零改动亮色）：

1. **语义令牌重定义**：`--dorami-*` / `--sh-*` 在暗色作用域换值，引用令牌的角色类/组件**自动翻转**。
2. **Tailwind 色阶重映射**：JSX 里散落的硬编码原子类（`text-slate-*`/`bg-slate-50/100`/状态色 `-50/-100/-200`）
   编译为 `var(--color-*)`，故按**主导角色**重映射这批 `--color-*`（slate 文字端提亮、浅底端压深、状态淡底/淡边·ring `-50/-100/-200` 转深染）。
   **`blue` 已折叠进 accent，其 `-50/-100/-200/-300` 必须与 `indigo` 同步深染**，否则 `bg-blue-50` 的胶囊、`ring/border-*-200` 的激活态亮边会在暗底残留浅色。
3. **组件层硬编码表面覆盖**：`@layer components` 里写死 白/玻璃/slate-hex 的工作区表面，用 unlayered
   `[data-theme=dark] .foo` 覆盖（特异性 + 来源优先级双重胜出）。

**核心是「一套表面阶 + 规则映射」，不是逐组件调色**。所有底色收敛到一条 **elevation scale**，同级靠描边分隔。
**关键：暗色必须保持与亮色相同的「相对明暗次序」**——亮色里 `well`/`soft` 是「比白卡更暗一点的凹陷填充」、`surface`(卡片)最亮；暗色照搬此次序（`canvas < well < soft < surface < raised`），否则 `bg-soft` 的淡填充会比卡片还亮、整片"发白"。

| 级 | token | 相对卡片 | 用途 |
|---|---|---|---|
| 画布 | `--dorami-canvas` | — | 页面底（最暗） |
| 深凹陷 | `--dorami-well` | 暗 | **输入框** / 轨道 / 表头 / 工具栏 / **代码·参数预览** |
| 浅凹陷 | `--dorami-soft` | 略暗 | 面板内淡填充 / 页脚 / 侧栏 / 信息条 |
| 卡片 | `--dorami-surface` | = | 卡片 / 浮层 / 模态 / **激活态控件** / 列表行·输入 hover·focus |
| 抬起 | `--dorami-raised` | 亮 | **唯一比卡片亮的一档**：次按钮 / 图标按钮 / 胶囊 / hover（仅给可点控件） |

强调淡底（旧版散落的 `indigo-50`/`rgba(238,237,252)` 等"发紫小块"）统一收敛到 `--dorami-wash`；强调实色（按钮/激活）在深底略提亮保对比。第 3 层覆盖按上表用**成组 `:is()` 选择器**批量套用，不写逐元素特例。**记住「可点才上 raised，被动容器只能 ≤ surface」**——被动框（代码/参数预览、内容框、信息条）popping 发白多半是误用了 raised/soft。

**新写样式的纪律（日后拓展无需再为暗色适配）**：背景只用上表 token（或 `.surface-card`/`.toolbar-card` 等已 token 化的角色类）、文字用 `--dorami-ink/-2/muted/faint`、强调用 `--dorami-accent`——即天然适配暗色。**不要给容器写死 `bg-white`/`#fff`/`rgba(255,255,255,…)` 或 slate-hex 底色**；标准 slate/状态原子类会自动重映射。仅 `bg-white`/`text-white` 这类**双角色**（同色既作文字又作白底）无法靠映射两全，才就地加 `dark:` 变体补丁。

> ⚠️ **`dark:` 变体接线（Tailwind v4 坑）**：v4 **不读** `tailwind.config.js` 的 `darkMode` 键，默认 `dark:` 走 `prefers-color-scheme`。本项目在 `index.css` 顶部用 `@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));` 把 `dark:` 绑到 `[data-theme=dark]`。**少了这行，所有 `dark:` 补丁会在浅色系统下静默失效**（白底/淡底残块全部漏出，且只在切到暗色却又是浅色 OS 时暴露）——排查暗色"补丁不生效"先查这行是否还在。

> ⚠️ **高特异性桥接会压过 `dark:` 补丁**：`index.css` 有一组「桥接」规则用后代选择器把卡片/模态内的嵌套工具类统一改写（如 `.surface-card .bg-white { … }`、`.modal-panel .bg-slate-50 { … }`，特异性 0,2,0）。它**压过** JSX 上的 `dark:bg-[var(--dorami-*)]`（0,1,0）——所以「卡片/模态内的 `bg-white`/`bg-slate-50`/`bg-indigo-50` 在暗色下仍发白」往往不是 `dark:` 没接通，而是被这组桥接强制。**每条亮色桥接都必须有 `[data-theme=dark]` 对应**（紧随其后、用 `[data-theme="dark"] :is(.surface-card,.modal-panel) …`，特异性 0,3,0 稳压），新增亮色桥接时同步加暗色版。

**登录/品牌页（`.auth-*` / `.login-panel`）刻意豁免**——暗色桥接不要纳入 `.login-panel`。

---

### 自检清单（提交前快速过一遍）

- [ ] Toast/空状态/错误/进行中文案是否符合 §1？
- [ ] 新增可点元素有焦点环？文本对比度达 AA？状态不靠纯色？（§2）
- [ ] 没有手写 `text-[Npx]` / `rounded-[Npx]` / 阴影硬编码？都引用了 token/角色类？（§3/§5/§6）
- [ ] 强调色只用于状态与唯一 CTA？一个视图一个主按钮？（§4/§8）
- [ ] 工作区动效时长在区间内、有 reduced-motion 兜底；品牌区未被误伤？（§7）
- [ ] 暗色下没有「亮色残块 / 黑字黑底」？硬编码原子类（尤其 `bg-white`、双角色 slate）已就地补 `dark:` 变体？（§9）
