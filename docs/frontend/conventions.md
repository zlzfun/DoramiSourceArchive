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

## 9. 暗色主题预留

目前**仅亮色**。但上述颜色/阴影令牌均为**语义令牌**（非字面 `gray-100`），未来上暗色只需在
`[data-theme=dark]` 作用域重定义这批令牌的值，引用语义令牌的组件层无需改动。新写样式时坚持引用
语义令牌（而非硬编码 hex），即为暗色预留。

---

### 自检清单（提交前快速过一遍）

- [ ] Toast/空状态/错误/进行中文案是否符合 §1？
- [ ] 新增可点元素有焦点环？文本对比度达 AA？状态不靠纯色？（§2）
- [ ] 没有手写 `text-[Npx]` / `rounded-[Npx]` / 阴影硬编码？都引用了 token/角色类？（§3/§5/§6）
- [ ] 强调色只用于状态与唯一 CTA？一个视图一个主按钮？（§4/§8）
- [ ] 工作区动效时长在区间内、有 reduced-motion 兜底；品牌区未被误伤？（§7）
