# 静默仪器(Quiet Instrument)全量风格改造 —— 规范与进度

> 分支 `style/quiet-instrument`。范围:**风格语法层**(全部工作区页面),布局重构另立项。
> 基调结论与样页:`scratchpad/dorami-quiet-sample.html`(台账/节点)、`dorami-reader-quiet.html`(阅读器)、
> 三基调对比 `dorami-tone-specimens.html`。基调宣言:**贵在安静**——密度、静止、排印刚性扛高级感,
> 动效只做状态反馈,accent 稀缺化。
> 本文是各子任务的**唯一契约**;与 `docs/frontend/conventions.md` 冲突处以本文为准(收尾时回写 conventions)。

## 一、风格语法规则(R1–R8)

### R1 选中语法·纵向列表/导航项 —— 统一为「accent 竖条」
适用:侧栏来源行、目录行、列表行、表格选中行、阅读器来源行/文章卡、设置导航等一切纵向可选项。

```css
/* ✅ after:竖条 + 灰阶底,无 accent 描边/无 wash 底/无 glow */
.foo-row-selected {
  position: relative;            /* 若无 */
  background: var(--dorami-soft);
  border-color: transparent;     /* 原 indigo 描边一律移除 */
  box-shadow: none;              /* 原 indigo glow/inset ring 移除 */
}
.foo-row-selected::before {      /* 或复用 inset box-shadow: inset 3px 0 0 var(--dorami-blue) */
  content: ""; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 3px; background: var(--dorami-blue);
  border-radius: 0 var(--r-pill) var(--r-pill) 0;
}
```
- 竖条统一 3px、`--dorami-blue`、圆头;贴行左缘,上下留 ~8px。
- 已符合该语法的(如 `.reader-article-card-active` 的 inset 竖条)保留竖条、**去掉叠加的 indigo border**。

### R2 选中语法·横向控件
- **segmented/scope-toggle(白拇指型)**:拇指 = `--dorami-surface` 底 + **ink 文字**(不再 indigo 文字)+
  中性 `--sh-1`(去 indigo 染色阴影)。
- **filter/category/tier chips(原渐变实心)**:激活态 = `--dorami-wash` 底 + `--dorami-accent-ink` 文字,
  **去渐变、去 glow**;计数徽标等子元素同步换色。
- 顶栏 Tab 由 foundation 统一处理,子任务勿动。

### R3 hover 全面灰阶化
- hover 背景一律 `var(--dorami-soft)`(暗色可点控件按 conventions §9 用 `--dorami-raised`);
- **hover 不得引入 accent**:不出现 indigo 背景/描边/文字变色;图标按钮 hover 保持 ink;
- 链接类 hover 用加深(ink)或下划线,不从灰变紫;
- 暗色段的 `border-color: rgba(124,112,235,…)` hover 一律改中性 `--dorami-border-strong`。

### R4 描边预算 —— 同层级只用一种分隔手段
- 卡片容器:描边走新 token `--dorami-card-bd`(亮=transparent,暗=细白线),分隔靠阴影;
  顶部内高光统一 `inset 0 1px 0 var(--dorami-edge)`。已由 foundation 改 `.surface-card`;
  各页面**自建的卡片式容器**照此收敛(有 border+shadow 双保险的去 border)。
- 状态章/chip:淡底+深字,**无描边**(现 `.vector-status-*` 已合规,以它为准绳)。
- 表格行分隔:至多一条 `--dorami-border` hairline;去 zebra、去双线、去 border-strong 行线。
- 禁止 border + ring(inset shadow) + 外阴影三重叠加。

### R5 accent 纪律 —— 实心 indigo 一屏 ≤ 2 处
- 允许:`.action-button-primary`(唯一 CTA)+ 选中标记(竖条/顶栏 active);
- 其余实心/渐变 indigo(chip 激活、hover 实心化、次按钮)全部降级为 wash/灰阶;
- wash(`--dorami-wash`)只作「状态标记底」(批量条、chip 激活、功能开启态),不作 hover。

### R6 字重降档(大而不糙)
- 900 全面退场(工作区):`.page-title` 900→800、`.stat-number` 900→700(foundation 已改);
- JSX `font-black` → 页面级标题 `font-extrabold`、指标数字 `font-bold`;
- 列表条目标题 ≤ `font-semibold/bold`;数字列一律 `tabular-nums`(多数已有,补漏)。

### R7 动效预算 —— feedback-only
- **删除仪式性入场**:`.tab-panel` 的 tab-enter、`.row-stagger`、`.tab-panel .stagger`、
  JSX 里卡片/列表用的 `animate-in fade-in slide-in-from-bottom-4` 等入场类(foundation 已把 CSS 端拆除,
  子任务负责删 JSX 里残留的入场 class 字符串);
- **保留状态反馈**:modal/toast/popover 开合、进度条/进度线、骨架屏 shimmer、开关/thumb 滑动、
  运行中 pulse、hover 即时变底;
- 保留的动效仍用 motion token + reduced-motion 兜底(conventions §7)。

### R8 不许动的
- 布局/DOM 结构/业务逻辑/文案语义(样式 class 与排印可改,字符串内容不改);
- 登录/品牌面(`.auth-*`/`.login-panel`)与其电影感动画 —— 完全豁免;
- 暗色三层机制(conventions §9)——所有改动必须亮暗两态成立,新增背景只用表面阶 token。

## 二、分工与边界

| 阶段 | 内容 | 负责 | 状态 |
|---|---|---|---|
| P0 基线 | 阅读器体验波 commit + 切分支 | 主线 | ✅ c4803f6 |
| P1 foundation | index.css 共享层:token(card-bd/edge)、共享选中类(top-tab/nav-pill/segmented/scope-toggle/filter-chip/category-chip/tier-segment/config-toggle/source-row-selected/catalog-chip)、hover 灰阶化(含暗色段)、仪式动效拆除、字重角色类 | 主线(自做) | ⬜ |
| P2-A | ReaderTab + ReaderMarkdown + reader 区段 css(R1/R3/R6:来源行/文章卡选中竖条、pin/load-more hover、字重) | Opus 代理 | ⬜ |
| P2-B | DataTab + ArticleDetailModal + ManualAddModal + DateRangePicker + 相应 css 区段 | Opus 代理 | ⬜ |
| P2-C | FetchTab + FetchRunsTab + 相应 css 区段(node-card/source-run/试抓等) | Opus 代理 | ⬜ |
| P3-D | MCPTab + FeedAccessSection + DailyBriefPanel + DailyBriefFlow + AccessTokenCard | Opus 代理 | ⬜ |
| P3-E | AdminOpsTab + charts/ + SettingsModal + settings/* + VectorTab | Opus 代理 | ⬜ |
| P3-F | App.jsx 壳层 + Toast + ConfirmDialog + 杂项组件 | Opus 代理 | ⬜ |
| P4 验收 | lint + build + grep 审计 + 亮暗抽查 + conventions.md 回写 + 提交 | 主线 | ⬜ |

**子任务边界(每个代理)**:只改分工表里自己的 JSX 文件 + index.css 中**自己组件的区段**
(区段以 `/* ── <组件名> ── */` 注释为界;共享类已由 P1 处理,发现越界需求记录在报告里,不要改)。
改完自查:`cd frontend && npm run lint` 通过;报告格式 = 改动清单 + 拿不准的点 + 越界发现。

## 三、验收 grep 审计(P4)

```bash
# 1. 工作区不应再有 indigo hover(允许 auth-*/login 段)
grep -n -A3 ":hover" frontend/src/index.css | grep "91, 84, 232\|124, 112, 235\|--dorami-wash\|dorami-blue"
# 2. 选中态不应再有 indigo 描边
grep -rn "border.*rgba(91, 84, 232\|border-color: var(--dorami-blue)" frontend/src/index.css
# 3. 工作区无 900/font-black(登录段除外)
grep -rn "font-weight: 900\|font-black" frontend/src
# 4. 仪式动效已拆
grep -n "tab-enter\|row-enter\|entrance-stagger" frontend/src/index.css
grep -rn "row-stagger\|animate-in fade-in slide-in-from-bottom" frontend/src/components
# 5. 渐变实心只剩 primary 按钮族
grep -n "linear-gradient(135deg, var(--dorami-blue)" frontend/src/index.css
```
