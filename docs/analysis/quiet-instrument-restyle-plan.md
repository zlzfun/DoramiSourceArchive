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
| P1 foundation | 同左 | 主线(自做) | ✅ 655f9a4 |
| P2-A | ReaderTab + ReaderMarkdown + reader 区段 css(R1/R3/R6:来源行/文章卡选中竖条、pin/load-more hover、字重) | Opus 代理 | ✅ |
| P2-B | DataTab + ArticleDetailModal + ManualAddModal + DateRangePicker + 相应 css 区段 | Opus 代理 | ✅ |
| P2-C | FetchTab + FetchRunsTab + 相应 css 区段(node-card/source-run/试抓等) | Opus 代理 | ✅ |
| P3-D | MCPTab + FeedAccessSection + DailyBriefPanel + DailyBriefFlow + AccessTokenCard | Opus 代理 | ✅ |
| P3-E | AdminOpsTab + charts/ + SettingsModal + settings/* + VectorTab | Opus 代理 | ✅ |
| P3-F | App.jsx 壳层 + Toast + ConfirmDialog + 杂项组件 | Opus 代理 | ✅ |
| P4 验收 | lint + build + grep 审计 + conventions.md 回写 + 死类清理 + 提交 | 主线 | ✅ |

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


## 四、完成记录与决策日志(2026-07-09)

提交链:基线 c4803f6 → P1 655f9a4 → Wave1 aed5015 → Wave2 e08684c → P4 收尾(本提交)。
lint + vite build 全程绿;审计 grep(§三)全部通过。

**有意保留(非遗漏)**:
- 「运行中/进度」活动态 = accent 家族(进度线/运行 pulse/running 徽标/statusMeta running tone),产品语义;
- integration-hero 大渐变面:接入页唯一品牌陈述面,是否降级留给布局波;
- 日历选中日实心 accent 圆、focus 焦点环、checkbox 控件 accent、DailyBriefFlow 点击披露 fade;
- wash 基底元素(source-run/subscription-action/reader-pin-off/active-filter-chip)的 hover 同族加深。

**显眼改动(用户目检重点)**:
- VectorTab「ChromaDB 挂载块数」渐变 hero → 中性仪表卡(嫌过头可单独回退该 commit 片段);
- 全站选中态从 indigo 描边/渐变实心 → 竖条/wash;顶栏 tab 文字 ink 化;
- 切页/列表入场动画消失(刻意);卡片 hover 不再抬升。

**遗留(另立项)**:
- 布局重构波(台账总账条并入主纸/节点三段一纸/常驻检视器,见样页);
- 阅读器相对时间显示(JSX 逻辑,非纯样式,未纳入本波);
- `·` 分隔符配给与文案层 em-dash 审查(文案波);
- eslint 可加规则拦截 font-black/入场类回潮(可选)。


## 五、布局波(L1–L4,2026-07-09)

目标:实机达到 dorami-quiet-sample.html(台账/节点)与 dorami-reader-quiet.html(阅读器)的布局。
提交链:L1 b5a5f3f(壳层)→ L2+L3 c884a66(两大页)→ L4 收尾(本提交)。

| 阶段 | 内容 | 负责 | 状态 |
|---|---|---|---|
| L1 壳层 | lg+ 左侧图标导轨替换顶栏;--rail-w/--app-top token;reader-shell 视口数学改造;移动端保留顶栏 | 主线 | ✅ b5a5f3f |
| L2 台账 | 分面栏+主纸(总账条=状态筛选器/覆盖率条/向量化动作区)+表格+详情抽屉(ArticleDetailDrawer,索引流水线);后端补 /api/articles has_content 透传 | Opus 代理 | ✅ c884a66 |
| L3 节点 | 一张纸:信号灯条(四灯+自动刷新)+分组调度板(行式/失败就地/行内进度)+常驻检视器(参数/试抓/存任务/近期运行);移动端检视器=底部抽屉 | Opus 代理 | ✅ c884a66 |
| L4 收尾 | 文档/记忆/终验提交 | 主线 | ✅ |

**决策记录**:
- 「告警」信号灯不落地:后端 SourceStateRecord 仅 healthy/failing/running/never_run 四态,无 warn 数据支撑;
- 试抓复用 test-fetch(testLimit=1,会写入台账),文案诚实标注,只借样页 preview-box 视觉;干跑端点属后端立项;
- 节点页 dept(主体)聚合层与行内多选退役,批量语义改「页头批量运行 × 筛选可见集」;
- 台账总账条计数 = 6 个并行 limit=1 聚合(全局口径,不随分面变化);
- 阅读器无需布局改动:三栏结构上一波已对齐,L1 的 token 化几何自动适配导轨。

**遗留(需后端聚合端点才能补齐样页细节)**:
- 总账条 7 日趋势 sparkline、节点行 7 日 sparkline(需 per-day 计数端点);
- 分面 per-value 计数(内容类型/来源);
- 死类清扫:旧 FetchTab 的 dept-*/section-band*/catalog-*/source-*(部分被 subscription-* 结构引用)、
  L2 退役的 ledger-filter-row 相关、.top-tab*/.brand-title(顶栏退役);建议单独一个清理提交;
- 设置面板 VectorSection 与总账条动作区的自动向量化/重索引入口重复,留设置波收敛;
- 「批量试抓/批量存任务」由多选语义降为单节点动作,如需恢复手选批量需产品决策。


## 六、台账优化波(用户验收清单 12+5+2+1 条,2026-07-09/10)

提交链:直移 18838ae → 范式+数据层 4d4775b → 装配 d2418bc → 遗留五条 0e0af96 →
揭示动效 882f319 → 滑动揭示+密度 dad9a1f → 揭示时序 a9a1777。全程 Playwright 探针/截图实证。

**沉淀的范式(conventions §4/§6 已回写,后续页面直接复用)**:
- 滚动条:常态隐形、悬停所在容器显现(--dorami-scrollbar* token,组件内禁自定义);
- 语义状态 token --state-{ok,warn,bad,run,idle}(-bg) + 状态章 .stamp 家族(形状点色弱可辨);
- 页码 .pager 家族(当前页=accent 实底);微型分段 .mini-seg(白拇指,表脚级轻量二择);
- 主按钮/头像扁平化(实心 accent + sh-1,无渐变无 glow);
- 分面数据源规则:选项一律来自聚合端点(GET /api/articles/facets),不得从当前页推导;
- DateRangePicker compact 变体(窄语境);全屏壳页 html:has(...){overflow:hidden} 封根滚动。

**后端**:/api/articles/facets(全量 group-by 计数)、content_types CSV 透传(tests/test_article_facets.py)。

**产品决策**:类型归组 CONTENT_TYPE_GROUPS(资讯文章=rss+web 等 11 组,utils/contentType 单一分类学);
来源分面取消厂商分组;个性化视图 segmented 退役(管理员不订阅);正文分面退役;
RAG 关闭时总账条降级为 总收录/今日/近7天 三格、索引状态列隐藏;表格密度 舒适/紧凑(持久化)。

**技术陷阱档案(排查同类问题先查这里)**:
- 工具类压层:utilities 层(text-*/line-clamp-*)压过 components 层同属性(实例:line-clamp-1
  的 display:-webkit-box 压 display:none);对策=去工具类或用未分层规则;
- button{font:inherit} 未分层简写压层内 font-size(字号挂容器靠继承穿透);
- <button> 上 overflow:hidden 触发 Chrome 按钮内部布局怪癖(内容高度坍缩)→ 用 contain:paint 裁切;
- overflow/text-overflow 离散属性不吃 transition 延时 → transition-behavior: allow-discrete 钉时点;
- fixed 弹层与类内 top:100% 过约束 → 内联补 top:auto。
