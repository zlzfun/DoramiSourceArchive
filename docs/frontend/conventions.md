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
- **`·` 间隔号配给（文案波 2026-07）**：仅用于行内同级 meta 的并列（`时间 · 来源 · 计数`），
  两侧各一空格，一行至多 3 段；不用于句子内部连接或标题。
- **破折号**：中文文案统一双横「——」且两侧不加空格；不用单「—」/「--」作中文破折号
  （单「—」仅限英文语境与表格空值占位）；toast 与说明句不以破折号做装饰性收尾。
- **相对时间**：阅读器等读者面的时间戳用相对时间（`x 分钟前 / x 小时前 / x 天前`，
  超 7 天回落绝对日期），hover title 给完整时间；工作区台账/流水保持绝对时间（审计语境）。

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
| `.page-title` | 页面 H1(页头范式 `.page-head`) | 24px/800 |
| `.page-subtitle` | 卡内说明(页级副标题已随页头范式退役) | 14px/600·muted |
| `.card-title` / `.section-title` | 卡片/区块标题 | 15px/700 · 14px/700 |
| `.body-text` | 默认正文/可读多行 | 14px/1.6/600 |
| `.tiny-meta` | 次要 meta | 12px/500 |
| `.micro-label` | 微标签/徽标/角标 | 11px/700 |
| `.stat-number` | 指标数字 | 26px/700·tabular |

**页头范式(2026-07,全站统一)**:每页页头 = `.page-head`(`h1.page-title` 24/800 + 右侧
`.page-head-actions`),**不带副标题**;流式页自带下边距,全屏壳页由壳选择器给内边距。
新页面照此,勿再造 page-header/自定义标题。

**静默仪器·字重纪律(2026-07)**:工作区 900/`font-black` 全面退场——页面级标题 800、
指标数字 700、列表条目标题 ≤700(阅读器文章卡 600);大字号配中粗,仪表感靠 tabular 对齐而非超粗。
数字列/计数/时间戳一律 `tabular-nums`。(登录/品牌面豁免。)

语境化标签（`.form-label`/`.node-param-label`/`.active-filter-label` 等）各自就地维护，不必归一。

**标题层级刻度(2026-07 字体审计波,硬约束)**:工作区标题按**职责层**取刻度,禁止就地发明字号:

| 职责层 | 刻度 | 在册类 |
|---|---|---|
| 页 | 24/800 | `.page-title` |
| 区(页内分区头) | 14/700 | `.zone-title`、`.tt-head-title`、`.sett-head-title`、`.section-title` |
| 卡(surface-card 的实体标题) | 15/700/ink | `.card-title`、`.brief-col-title` |
| 卡内小节 | 12.5–13/700 | `.tools-title`、`.sett-sync-title`、`.drawer-sec-title` |

三条规则:①**同职责同刻度**——同一页面上并列的同层标题必须同字号(肇因:brief 卡列标题 12px
与邻卡 `.card-title` 15px 并排);②**标题字号不得小于其辖区正文**(肇因:「定时配置」12px <
表单标签/输入 13px 的倒挂);③新标题**必须复用在册类**,需要新刻度先改本表再落码。
注意:无独立卡题的卡,其最高层分区标题**就是卡题职责**,取卡级刻度(brief-card 两列标题即此例)。
并列多列的标题要求**顶对齐**——列头行若含更高的控件(按钮),用 `align-items: flex-start` +
控件负 margin 配平,不许让控件把标题压离基线。

> ⚠️ **`button/input { font: inherit }` 陷阱(未分层压层)**:`index.css` 顶部这条全局规则是**未分层**的
> `font` 简写(作用于 button/input/select/textarea),按 cascade layers 规则会压过 `@layer components`
> 里任何类选择器的 `font-size`——给按钮/输入框类写 `font-size` 看似合法实则不生效(计算值回落到继承链)。
> 定字号要么写在**容器**上靠继承穿透(实例:`.ledger-scope`/`.sett-nav`),要么写进文件尾的**未分层修复区**
> (实例:`.reader-seg-btn`/`.reader-disc-search input`)。排查「字号怎么改都不生效」先想到这条。
> 已知踩坑记录:分段钮(弹窗波)、阅读器 全部/未读 分段(v3.6)、发现页搜索框(v3.10)。

## 4. 颜色令牌（语义四套，互不混用）

详见 `index.css` `:root` 顶部「设计令牌分类法」注释。

- **背景/图底**：`--dorami-canvas / canvas-2 / surface / soft / card / well / wash` —— 勿用作文字色。
- **文字/墨色**：`--dorami-ink / ink-2 / muted / faint` —— 信息层级；勿用作背景。
- **描边**：`--dorami-border / border-strong`。
- **强调/状态**：`--dorami-accent`(=`--dorami-blue`) / `blue-2` / `accent-ink` ——
  **唯一饱和色，只用于「激活/状态」与「一个视图里最重要的那个动作（CTA）」。**
- 散落的 indigo/blue 原子类已在 `@theme` 折叠为同一 accent；emerald/amber/red/slate 语义中性色照常用。

**静默仪器·accent 纪律（2026-07，全站已落地）**：
- **实心/渐变 accent 一屏 ≤2 处**：主按钮（CTA）+ 选中标记。chip/徽标激活态一律
  「`--dorami-wash` 底 + `--dorami-accent-ink` 字 + 无描边」，不再渐变实心。
- **hover 不得引入 accent**：hover 背景 `--dorami-soft`（暗色可点控件 `--dorami-raised`），
  文字/描边走 ink/border-strong；wash 基底元素的 hover 只允许同族加深。
  例外：focus 焦点环（a11y）、checkbox 类控件、CTA 自身的提亮。
- **选中语法(v3.8 轨语言统一后分双轨)**：
  ① **轨与轨延伸面**(应用导轨/阅读器视图轨/设置柜左导航)= **wash 块**
  (`--dorami-wash` 底 + `--dorami-accent-ink` 字,无描边无竖条;`.reader-vrail-btn.is-on`
  类族即全站轨语言的单一事实来源);
  ② **工作区纵向列表/导航项** = 3px `--dorami-blue` 左竖条(`inset 3px 0 0` 或 `::before`)
  + `--dorami-soft` 底。两者都**不用 indigo 描边/ring/glow**；
  横向控件：segmented 拇指 = surface 底 + ink 字 + 中性 `--sh-1`，顶栏 tab = 底部 accent 条；
  阅读器条目卡/源行选中 = surface 浮起 + `--sh-1`(阅读器样页语法)。
- 「运行中/进度」类活动态（进度线、运行 pulse、running 徽标）与 accent 同族，是有意的产品语义。
- **语义状态 token(2026-07)**:ok/warn/bad/run/idle 五态一律引用 `--state-*` / `--state-*-bg`
  成对 token(亮暗自动翻转),不再散写 emerald/rose/amber hex。
- **热度/密度色阶(图床波 v3.11)**:需要「连续量深浅」的可视化(热点图等)用 `--heat-0…4`
  ——**accent 靛的明度阶,不新开第二个饱和色系**(参照对象 GitHub 用绿,本仓不跟)。
  规矩:**深浅只编码一个连续量**(如覆盖率);异常/失败另走独立通道(角标、描边),
  勿把两个语义压进同一色轴——两条通道正交才读得清。

## 4.1 双通道编码(可视化)

同一个图元要同时表达「量」和「异常」时,拆成两条正交通道:量走色阶深浅,
异常走**形状标记**(热点图格子右上角 `--state-warn` 切角三角即此法)。
理由:色阶已被量占满,再插一个语义色会与相邻档位混淆;形状标记与色阶互不干涉,
且色盲用户仍可辨。数据密集网格的悬停浮标必须给出**全部三态数字**,不能只显总量。

## 4.2 呈现的诚实(只读系统 / 快照数据)

三条互相独立、都因 X 社交波(v3.12)沉淀:

- **易腐的快照数值不展示。** 归档保存的是**抓取那一刻**的值。推文的点赞/转发/评论数
  三天后早已翻倍,而页面仍显示当时的数字 —— 那不是"信息略旧",是**主动展示错误信息**。
  判据:该字段是否会在源头持续变化而我们不会重抓?会 → 入库但不展示。
  (要展示就得设计更新机制,那是另一笔账,别默认承担。)
- **没有写权限就不做可交互仿真。** 本系统对外部平台只读,故互动数字**不做成按钮**。
  只呈现我们真能做的动作(收藏 / 标读)。把不可用的动作画成可点的样子是欺骗。
- **重复即噪声,渐进披露。** 一个标识只在能消歧时才出现:社交卡的平台角标仅当订阅了
  ≥2 个平台才挂 —— 单平台时每张卡挂同一个图标不提供任何信息。
  平台/来源这类**属于「源」而非「每条内容」**的属性,优先在源栏、目录等源层面标注。

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

## 6. 高程/阴影与描边预算

**静默仪器·描边预算（2026-07）：同一层级只用一种分隔手段。**
- 卡片级容器：描边走 `--dorami-card-bd`（亮=transparent、暗=细白线），分隔靠 `--sh-2` +
  顶部内高光 `inset 0 1px 0 var(--dorami-edge)`；`.surface-card` 已内置，新卡片直接复用。
- 状态章/chip：淡底 + 深字，**无描边**（准绳：`.vector-status-*`）。
- 表格行分隔：至多一条 `--dorami-border` hairline；禁 zebra+行线双保险、禁 border-strong 行线。
- 禁止 border + inset ring + 外阴影三重叠加；卡片无 hover 抬升（装饰性位移已拆）。
模态等确需悬浮的浮层才用强高程（`--sh-3`）。

**组件范式速查(新增展示一律复用,勿另造)**:
- **状态章 `.stamp` + `.stamp-{ok|warn|bad|run|idle}`**:淡底+深字+形状点(方/三角/菱/圆呼吸/圆),无描边;可点章用 `button.stamp`(hover wash)。
- **页码 `.pager` / `.pager-btn`(`.is-on`=accent 实底,选中标记) / `.pager-ellipsis`**:所有分页统一。
- **微型分段 `.mini-seg` / `.mini-seg-btn`(`.is-on`=白拇指)**:表脚/工具条级轻量二择(如表格密度 舒适/紧凑),勿再造小开关。
- **滚动条**:全局范式已定——常态隐形、悬停所在容器显现(`--dorami-scrollbar*` token);组件内**禁止**再写 `::-webkit-scrollbar`。
- **主按钮扁平化**:`.action-button-primary`/`.primary-action` = 实心 accent + `--sh-1`,hover 加深;无渐变无 glow(登录 auth-* 豁免)。
- **分面数据源**:筛选栏选项一律来自聚合端点(`GET /api/articles/facets` 之类的全量 group-by),不得从当前页数据推导。

## 7. 动效（区分场景）

- **工作区（静默仪器，2026-07）：动效 = feedback-only。** 仪式性入场已整体拆除
  （`tab-enter`/`row-stagger`/`entrance-stagger` keyframes 已删，`.tab-panel`/`.row-stagger`/`.stagger`
  仅存为无动效锚点）——**不要再给切页/列表/卡片加入场编排**。
  保留白名单：modal/toast/popover 开合、`selection-bar` 上下文条、进度条/进度线、骨架屏 shimmer、
  开关/拇指滑动、运行中 pulse、hover 即时变底、瞬态定位高亮（`source-row-focus`）。
  时长引用 motion token：`--motion-fast`(150) / `--motion-medium`(240) / `--motion-slow`(300) + `--motion-ease`。
- **品牌/登录页**：电影感动画（秒级、硬编码）保留，不引用上述 token，不收敛。
- 所有动画都要在 `@media (prefers-reduced-motion: reduce)` 下降级（已有兜底，新增动画须遵守）。

## 8. 组件层级与一致性

- **一个视图一个主操作**：`.action-button-primary`（渐变实心）只给该视图/卡片/表单里最重要的那一个动作；
  次要动作用 `.action-button-secondary / -quiet`，破坏性动作用 `.action-button-danger`。
- 复用既有原语（`.action-button*` / `.icon-button` / `.surface-card` / `.modal-*` / `.toast-*`），
  不要为同类元素另起样式。

**按钮尺寸三档(2026-07 按钮规格波,硬约束)**——按**语境**取档,同语境同档,禁止就地发明高度:

| 档 | 规格 | 语境 |
|---|---|---|
| L | 裸 `.action-button`(40px/14px) | 模态脚部、页级批量条(board-batchbar)、空态 CTA |
| M | `min-h-[32px] px-3 text-xs` | **页头 `page-head-actions`**(用户拍板:页头取矮档更优雅)、卡头/卡内、抽屉脚部、设置面板、表格工具条 |
| S | ≤28px,各范式类自治 | 行内微操作:`.rowact-btn`/`.copybtn`(26)、`.tt-act-btn`(格条)、行内文字微钮 |

三条规则:①**同语境同档**——页头按钮一律 M 档三件套(肇因:运行页页头 36px 与节点页
40px 并存,切页违和;后拍板全站页头降 M);②M 档写法固定为 `min-h-[32px] px-3 text-xs`
三件套,30/34/36/48 等中间值一律并档(48 档已随向量雷达检索行降为 40 退役);
③chips(model-chip/stamp/target-chip)不是按钮档位,不受本表约束;④**同一排并列的按钮
必须等高**——页头 M 档 32 与 `.icon-button.signal-refresh`(32)天然同高;mini-seg 等
文字分段控件视作标签,不强行对齐。

**分段控件家族刻度(2026-07 补)**——三档,按语境取用,高度对齐所在排:

| 类 | 总高(壳+拇指) | 语境 |
|---|---|---|
| `.segmented-control` | 32(3+24+边2),字 12.5/650 | 页头视图/子页切换(与页头 M 档同轴) |
| `.sett-seg` | ~34(3+28) | 设置面板行内(外观三态等) |
| `.mini-seg` | ~26(2+22),字 11 | 工具条/表脚/卡头轻量刻度(时间窗、密度) |

注意:分段拇指是 button,**字号/字重必须写在容器上靠继承穿透**(`button{font:inherit}`
未分层陷阱,§3);`.scope-toggle` 已无使用者,属死类待清扫,新分段勿取用。

**输入框两档(2026-07 补)**:表单档 = `.form-input` 默认(36px,竖排表单配 `.form-label`);
行内工具档 = `.form-input.form-input-inline`(32px/12px,与 M 档按钮、segmented 同排等高——
工具行/zone-head 里的搜索框一律取此档;`.board-search` 已同轴 32)。`.catalog-search` 死类待清扫。

**小表单模态语法 `.form-sheet`(2026-07)**:头(`-head`:card-title + 30px 关闭 icon-button)/
身(`-body` + `-field`)/脚(`-foot`:取消 quiet + 主钮,右对齐),hairline 分隔、无色底条——
旧「well 标题条 + soft 脚条 + indigo 图标」三段横条语法退役;新小弹窗一律用此三件套
(在册消费者:运维新建读者/重置密码)。

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

> ℹ️ **Legacy bridge 已退役（F8-B，2026-07）**：`index.css` 曾有一组后代选择器桥接（`.surface-card .bg-white { … }` 等）把卡片/模态内的裸工具类改写成令牌语义，并因高特异性压过 `dark:` 补丁——该段已随 54 处存量令牌化**整体删除**。现在由 ESLint 规则 `dorami/no-legacy-bridge-class`（`'error'`）把关：新代码直接写 `bg-[var(--dorami-surface)]`/`--dorami-soft`/`--dorami-wash`/`border-[var(--dorami-border)]` 等令牌类，**不得再写** `bg-white`（裸形态）/`bg-slate-50*`/`bg-indigo-50`/`bg-blue-50*`/`border-slate-100|200`/`border-indigo-200`。`bg-white/NN` 半透明白玻璃（紫/深色 Hero、深色代码面、Toast 高光等主题恒定表面）是正当写法，不在拦截范围。

> ⚠️ **输入框：背景与文字色 token 必须成对**。表单输入优先**复用 `.form-input` 角色类**（它已把 `color: var(--dorami-ink)` 绑进去，暗色自动翻转）。若确实要手搓工具类串，凡用了会翻转的背景 token（`bg-[var(--dorami-soft)]`/`--dorami-well` 等），就**必须同时绑 `text-[var(--dorami-ink)]` 和 `placeholder:text-[var(--dorami-faint)]`**——只翻背景不翻文字是暗色「深字配深底、看不清」的典型来源：背景随主题翻转、文字色却走继承/默认值不翻，亮色下侥幸正常、暗色下糊成一团。（实例：`AdminOpsTab.jsx` 模型配置的 `INPUT_CLS` 曾漏掉文字色 token，亮色无碍、暗色翻车。）

**登录/品牌页（`.auth-*` / `.login-panel`）刻意豁免**——不做暗色适配改写，品牌面保持自身的电影感配色。

---

### 验收:build 通过不等于渲染正常

`npm run build` 与 `eslint` 抓不到运行时崩溃 —— `undefined.length`、空指针属性访问、
渲染期抛错都能编译通过却在浏览器里白屏。**改动了组件渲染逻辑就要真在页面里打开验证**,
不能只凭 build 绿就报完成(X 社交波曾因给共享枚举 `EDITORIAL_GROUPS` 加一项、而某个
消费它的 `buckets` 缺对应键,导致 `.filter(g=>g.list.length)` 白屏整个阅读器)。

无 Chrome 扩展时的替代:项目自带 **Playwright**(`.venv/bin/python` + `playwright.sync_api`)。
最小验收脚本 = 无头启动 → 登录 → 走一遍改动路径 → 挂 `page.on("pageerror")` 与
`page.on("console", type==='error')`,任何一条非空即回归。截图(`page.screenshot`)供视觉核对。

### 自检清单（提交前快速过一遍）

- [ ] 改了渲染逻辑?Playwright(或浏览器)真开过页面、`pageerror=0`?(见上「build≠渲染」)
- [ ] Toast/空状态/错误/进行中文案是否符合 §1？
- [ ] 新增可点元素有焦点环？文本对比度达 AA？状态不靠纯色？（§2）
- [ ] 没有手写 `text-[Npx]` / `rounded-[Npx]` / 阴影硬编码？都引用了 token/角色类？（§3/§5/§6）
- [ ] 强调色只用于状态与唯一 CTA？一个视图一个主按钮？（§4/§8）
- [ ] 工作区动效时长在区间内、有 reduced-motion 兜底；品牌区未被误伤？（§7）
- [ ] 暗色下没有「亮色残块 / 黑字黑底」？硬编码原子类（尤其 `bg-white`、双角色 slate）已就地补 `dark:` 变体？（§9）
- [ ] 输入框复用了 `.form-input`，或手搓串里背景 token 与 `text-`/`placeholder:text-` token 成对？（§9）
