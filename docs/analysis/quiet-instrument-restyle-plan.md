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


## 七、运行页波(任务与运行 · 调度台改造,2026-07-10)

**样页 = 唯一视觉契约**:`docs/design/dorami-runs-quiet.html`(亮暗两态已 Playwright 验收)。
设计概念「调度台」:左栏时刻表(什么时候跑)+ 右区运行流水(跑得怎么样)+ 总账条运行中灯(什么在跑);
「采集任务/运行历史」两视图 segmented **退役**,合并为一张纸。临时运行三层处理:时刻表底部
「未存为任务·临时抓取」汇总行 / 对象 mini-seg 全部·任务·临时 / 行尾 hover「存为采集任务」升格回路。

**分工**:

| 项 | 内容 | 负责 | 状态 |
|---|---|---|---|
| R-0 | 后端 `next_run_at`(job+per-node cron 最早触发,serialize_collection_job;tests/test_collection_next_run.py) | 主线 | ✅ |
| R-1 | FetchRunsTab.jsx 全重写 + index.css 运行区段 + App.jsx 集成 | Opus 代理 | ✅ |
| R-2 | 验收:lint/build/pytest/Playwright 亮暗截图对照样页/审计/死类清理 | 主线 | ✅ |

**类名与复用边界(R-1 必读)**:
- 新建区段 `/* ── 任务与运行 静默仪器(运行波) ── */`:`runs-shell/runs-work/runs-paper`、
  时刻表 `tt-*`、流水 `flow-*`、上下文条 `jobbar-*`、行尾操作 `rowact*`——刻度照样页;
- 样页为独立预览**复制**的共享范式(`.stamp/.pager/.mini-seg`、滚动条、导轨、暗色 token、page-head)
  **一律不迁**(index.css 已有,直接引用);壳层数学照抄 `.ledger-shell` 模式(字号语境 13px 挂壳,
  `margin-top: -22px` 抵消 main 顶距、`height: calc(100vh - var(--app-top))`),移动端 <lg 纵排降级照台账;
- 状态→stamp 映射:success→`stamp-ok`、partial_failed→`stamp-warn`、failed→`stamp-bad`、running→`stamp-run`。

**数据推导决策(全部前端,窗口口径 = 已加载 100 任务级 + 200 节点级运行)**:
- 时刻表排序:活跃且有 `next_run_at` 按其升序 → 活跃无 cron(手动)按名称 → 临时抓取汇总行(「未存为任务」组)→ 已停用组;
- 倒计时 = `next_run_at - now` 每秒走字(H:MM:SS,「距下次」),台面时钟每秒;
- 7 日点阵/上次结果点:从已加载运行按日聚合最差状态(bad>warn>ok),无运行=空格;窗口有限为已知妥协;
- 流水:unifiedRuns(任务级 + 无 `job_run_id` 的节点级)按 `started_at` 日分组,日头行带当日摘要(次数/新增/失败,从当前过滤后行推导);
- **状态/触发/对象筛选全部本地过滤**(总账条计数不得随筛选塌缩——拉取不再带 status 服务端参数);
  `fetcher_id`(节点页跳转)保留服务端参数,UI 以 wash 上下文条显示(与 jobbar 同语法,「来源:xxx ✕」);
- 任务选中过滤:`run.job_id === job.id`;临时行选中:`run_scope !== 'saved_job'` 的任务级 + 全部无父节点级;
- 子运行展开:`fetchRuns.job_run_id === run.id`,窗口外无子行则不渲染 caret;
- rowacts:临时运行(任务级 ad_hoc/legacy 或单节点)→「存为采集任务」(从行/子行拼 fetcher_ids+params 预填新建模态,复用 pendingJobDraft 的本地路径);失败行 →「重跑」(单节点 `triggerFetch(fetcher_id, params)`,任务级 `runCollectionJob`);数据不可得则不渲染;
- 客户端分页(页大小 50,`.pager`);自动刷新 30s(localStorage 持久化,仅 isActive 时轮询);骨架屏保留。

**App.jsx 集成**:FetchRunsTab 撤掉 `view/setView` props;`jumpWithFocus('runs', …)` 的 view 参数传 null
(nav hash 结构不动,runs 视图值闲置);`pendingFilter` 直接落 fetcher_id/status 本地筛选;
`pendingJobDraft` 仍打开新建模态预填。功能不减:任务 CRUD 模态、立即/测试运行、删除、启停。

**验收记录(2026-07-10,铸 cookie + Playwright 亮暗截图 + 交互抽检零 pageerror)**:
- 主线验收修正三项:①任务级单节点临时运行主行名净化(后端拼名「临时抓取: {fetcher_id}」→
  节点友好名,scope 由副行表达);②单节点聚合运行不给展开 caret(展开无增量信息;
  「存为任务」改依 hasChildren 判定,不受影响);③页头刷新钮对齐节点页 `icon-button signal-refresh` 形态。
- 死类清理:`ActiveFilterBar.jsx`/`AnimatedNumber.jsx` 组件、`.active-filter-*`/`.metric-card`/
  `.run-object-mark` 类及其暗色引用(旧运行页是最后消费者)。
- 顺手修存量:`GET /api/articles/facets` 补记进路由权限冻结清单(台账优化波漏登记,
  tests/test_route_permissions.py;它是对 authenticated-any 的 GET /api/articles 的只读聚合,暴露面不新增)。
- 全量 pytest 314 绿;lint/build 绿;新区段审计(hover-accent/渐变/900)零违例。
- 代理越界发现待后续立项:全局无 `--mono` token(各处散写 mono 栈);`statusMeta.js` running 态残留
  `ring-indigo-200` 描边(StatusBadge 仍被 VectorTab 使用);App `SUBVIEW_TABS` 的 runs 视图值闲置。
- 已知妥协:7 日点阵/当日摘要/总账条计数为窗口口径(100 任务级+200 节点级);
  per-day 聚合端点仍与台账 sparkline 同一遗留立项。


## 八、集成波(接入集成 · 出口面板改造,2026-07-10)

**样页 = 唯一视觉契约**:`docs/design/dorami-integrations-quiet.html`(亮暗已 Playwright 验收)。
设计概念「出口面板」:页面即通道清单,每通道一张卡,卡内恒定「状态章 → 端点/凭据 → 接入片段」。
**用户已拍板的两项退役**:①「日报生成/Agent 接入」segmented 退役(单页纵排:交付通道区在上、
日报工坊区在下);② integration-hero 大渐变面撤除(品牌感归登录页,页头只留只读「模型」chip)。

**分工**:

| 项 | 内容 | 负责 | 状态 |
|---|---|---|---|
| I-0 | 契约 + 验收方案 | 主线 | ✅ |
| I-1 | MCPTab.jsx 重写 + FeedAccessSection/DailyBriefPanel 换肤重构 + index.css 集成区段 | Opus 代理 | ✅ |
| I-2 | 验收:lint/build/双角色(admin+reader)探针/死类清理(integration-hero/DailyBriefFlow)/提交 | 主线 | ✅ |

**结构映射(I-1)**:
- 页头 `.page-head`「接入集成」+ 右侧只读模型 chip(admin 才渲染;数据 = 既有 llmStatus);
- 区 1 交付通道(全角色):左 MCP 大卡(状态章/端点行/JSON·URL mini-seg/适用工具 chips/五工具 hairline
  行表/「启停与取数范围」details 折叠)+ 右列 个人聚合接口卡(FeedAccessSection 换肤:令牌行
  显示·复制·轮换/两端点行/curl 块)与 Claude 技能包卡(三步安装 + 下载按钮,GET /api/skill/daily-brief);
- 区 2 AI 资讯日报(canManage 才渲染,zone-badge「仅管理员」):DailyBriefPanel 重构为三段 hairline 卡
  (定时配置:启用 switch+cron+topN+保存 / 手动生成:唯一 primary CTA+五段流水线+进度微字 /
  近期日报:紧凑行,保留展开看内容与删除的既有行为,动作收 hover);
- **DailyBriefFlow 动画退役**:进度改用 `.ledger-pipeline` 家族步进条(收集/摘要/去重/精选/成稿,
  phase 映射沿用现有 progress 轮询数据),feedback-only——进行中段实底 + 计数走字,无流动编排。

**类名与复用边界**:
- 新区段 `/* ── 接入集成 静默仪器(集成波):出口面板 ── */`:样页的 `zone-*`/`endpoint*`/`codeblock`/
  `token-*`/`targets`/`target-chip`/`tool-*`/`brief-*`/`model-chip`/`steps`/`step`/`scope-note` 迁入;
  **迁入前逐一 grep 查重**,与既有类冲突的加 `integ-` 前缀;
- 复用不迁:`.stamp`/`.mini-seg`/`.page-head`/`.surface-card`(样页 .card 即它)/`.form-input`/
  `.ledger-switch`(开关)/`.ledger-pipeline` 家族(步进条)/`.action-button*`(主/次按钮)/
  copyText+runAction(toast 文案守 §1);
- 角色降级:reader 只见区 1(无日报区/无模型 chip);RAG 关闭时两个语义工具行 `stamp-idle`「RAG 未启用」
  + 行 opacity 灰显(不隐藏);MCP 停止时状态章 `stamp-bad` + 配置块折叠(沿用既有行为);
- App.jsx 无需改动(sub 状态是 MCPTab 内部 state,直接删除)。

**功能不减清单**:MCP 状态拉取/端点与 JSON 复制、feed 令牌 get/rotate、技能包下载、日报配置读写、
手动生成(后台 job 轮询 + 进度轮询)、历史展开/删除、LLM 状态 chip。

**验收记录(2026-07-10,双角色铸 cookie + Playwright 亮暗截图,零 pageerror)**:
- 真实边界全覆盖:MCP 停止态(stamp-bad + 配置折叠)、RAG 关闭(语义工具 stamp-idle + 行灰显)、
  reader 降级(仅交付通道区,无模型 chip/日报区)、日报三段卡(含代理保留的增量游标+重置);
- 主线裁决:代理收敛掉的 **OpenCode JSON 变体恢复**为 mini-seg 第三项(JSON 配置/OpenCode/URL——
  它是异构配置格式,属功能减项不属视觉收敛);其余四项「拿不准」采纳:进度 phase 五段映射
  (selecting 并段的粗粒度妥协)、feed 令牌明文仅轮换时可见(后端 hash-only 约束的诚实呈现)、
  FeedDocs 精简与 reader 日报指引撤除、历史章统一「已生成」;
- 死类清理:integration-hero/-kicker/-lede(含暗色块)、DailyBriefFlow.jsx、AccessTokenCard.jsx;
- lint/build/pytest 314 全绿;新区段审计(hover-accent/渐变/900)零违例;
- 遗留(与运行波共账):全局 `--mono` token(本波 .integ-page 再次局部定义,已两处重复,foundation 立项优先级上调)。


## 九、运维波(运维管理 · 仪表墙改造,2026-07-11)

**样页 = 唯一视觉契约**:`docs/design/dorami-admin-quiet.html`(亮暗与全交互态已 Playwright 验收)。
设计概念「机房仪表墙」:三子页 segmented **保留**(数据形态迥异),**时间窗控件上移页头**统一全页
(内容页收藏/订阅为累计口径,豁免注记);KPI 数字全 ink 化(彩色 KPI 是要拆的 AI-tell);
图表按 dataviz 纪律重做(校验记录见样页注记与 index.css `--chart-*` 注释)。

**分工**:

| 项 | 内容 | 负责 | 状态 |
|---|---|---|---|
| A-0 | 契约 | 主线 | ✅ |
| A-1 | 图表层(chartUtils/DashboardCharts + `--chart-*` token) | 主线(dataviz 纪律在其上下文) | ✅ |
| A-2 | AdminOpsTab.jsx 重写 + admin/adminShared·adminUtils 换肤 + index.css 运维区段 | Opus 代理 | ✅ |
| A-3 | 验收:lint/build/探针(三子页+抽屉+亮暗)/死类清理/提交 | 主线 | ✅ |

**A-1 已落地(A-2 按此消费,勿改 charts/)**:
- `--chart-1..6`/`--chart-other` token(:root 亮面 + dark 暗面,均过六项校验;「其它」恒中性灰);
- `chartUtils`:`CHART_SLOTS`/`C_OTHER`/`colorForEntity(namespace, name)`(色随实体的会话级槽位记忆)
  /`C_READ`/`C_FAVORITE`;**`CATEGORICAL` 只剩过渡别名,A-2 移除其 import 后由 A-3 删除**;
- `MultiSeriesArea`:自带 surface-card 容器 + card-head(标题+维度 mini-seg)+ 图例 + 末端 Top2 直标
  + 段间 2px 表面缝;新增 `namespace` prop(调用处传 'ai-usage'/'user-activity' 等唯一名);
- `RankBars`/`BarList` 的 `colorByIndex` 已退役(色编码排位违反纪律),调用处删该 prop;
  BarList 数值改默认常显 mono 直标。

**A-2 结构映射**:
- 页头 `.page-head`:标题 + 时间窗 mini-seg(7/14/30/90 天,单一 days state 驱动 用户+AI 两子页)
  + segmented(用户|内容|AI,沿用现有 sub state);
- 用户子页:KPI 总账条(样页 `.kpi-strip/.kpi*`,被动读数、数字全 ink)→ 活跃 Top 卡
  (RankBars 单系列 C_PRIMARY,按阅读/按登录 mini-seg)→ 账户表(样页 `.acct-*`,ledger-table 语法:
  46px 行/hover soft/选中竖条;管理动作收 hover `.rowacts`——复用运行波区段的基类,补
  `.acct-row:hover .rowacts` 显现规则;「新建读者」zone 头右侧 secondary)→ 行点开活动抽屉
  (ledger-drawer 语法换肤:tiles/登录 details 展开近 N 次/各源 BarList/每日 AI MultiSeriesArea
  dims=[[calls,调用],[tokens,tokens]]);
- AI 子页:总闸开关板(样页 `.ai-switchboard/.ai-light/.model-field*`:状态灯+switch+hairline 分隔
  +行内模型配置+测试连通/保存 quiet)→ KPI 条 → 双 MultiSeriesArea(调用/tokens,按用途/按用户);
- 内容子页:KPI 条 → 各源 BarList(阅读/收藏/订阅——订阅色 `C_OTHER` 中性)+ 收藏 TOP 榜
  (样页 `.toplist-*`);
- `adminUtils` 的 `KPI_COLOR` 彩色数字体系退役;`adminShared` 的 StatCard/ChartPanel/PanelHeader
  按新结构裁并(KPI 走 kpi-strip、图表卡容器已内聚进 MultiSeriesArea,余者删或简化);
- index.css 新区段 `/* ── 运维管理 静默仪器(运维波):仪表墙 ── */`:样页 `kpi-*`/`acct-*`/
  `ai-*`/`model-field*`/`tiles/tile`/`toplist-*`/`win-label` 迁入(先 grep 查重,冲突加 `admin-` 前缀;
  `zone-*`/`card-pad/-head/-desc`/`.rowacts` 等集成波/运行波已有的直接复用)。

**功能不减清单**:三子页全部数据拉取与窗口联动、账户管理五动作(新建/启停/AI 开关/重置密码/删除,
含确认与 toast)、per-user 活动抽屉全字段、AI 总闸读写、模型配置读写+测试、图表维度切换。

**验收记录(2026-07-11,铸 cookie + Playwright 三子页/抽屉/图表 hover/亮暗,零 pageerror)**:
- 主线裁决:代理砍掉的 **temperature/max_tokens 编辑恢复**为总闸板行内紧凑字段(与集成波 OpenCode
  同性质——「视觉收敛吃掉功能」是代理的系统性倾向,验收必查项);其余采纳:内容 KPI 保 5 格
  (信息保全)、AI KPI 收 3 格、抽屉常挂载右滑模式、AI 用量默认窗并入统一 30 天;
- 详情抽屉由居中 modal 改 ledger-drawer 右滑、模型配置 modal 退役内联进总闸板——均照样页;
- 死代码收口:`CATEGORICAL` 过渡别名删除、`adminShared.jsx` 孤儿文件删除;
- lint/build/pytest 314 全绿;运维区段(139 行)审计零违例;向量化率 0% 走 `.kpi-num.is-bad`
  语义色(「语义色只留异常」的正例);
- 全站布局波至此收官:台账/节点/运行/接入集成/运维管理五个工作区页面全部完成
  「从零样页 → 契约 → 代理迁移 → 铸 cookie 验收」流程;阅读器早于本轮已对齐。


## 十、残债清尾(B 波,2026-07-11,主线自做)

| 项 | 处理 | 验证 |
|---|---|---|
| 全局 `--mono` token | `:root` 立 token(取最全栈),25 处散写 `font-family: ui-monospace…` 收敛为 `var(--mono)`,runs-shell/integ-page/admin-page 三处局部定义删除;JSX 侧 Tailwind `font-mono` 默认栈与之等价不动;登录段本无 mono 声明无需豁免 | 栈等价纯替换,build 绿 |
| statusMeta running 态 | `ring-2 ring-indigo-200/60` 删除(保留 icon 旋转 + 徽标 pulse——运行中反馈白名单);顺手把 `TONE_CLASS.accent` 的 `border-accent/25` 收为 `border-transparent`(同族 indigo 描边残留) | 消费面核查:VectorTab 相关性徽标、FetchTab 仅用 tone/label |
| App runs 闲置视图 | `SUBVIEW_TABS`/`TAB_DEFAULT_VIEW`/`defaultViews` 移除 runs;旧书签 `#/runs/history|jobs` 因 runs 非 SUBVIEW 自然归一 `#/runs`,无需特判 | 实机探针:旧书签落「任务与运行」、hash 归一、零 pageerror |
| ESLint 防回潮 | 新规则 `dorami/no-ceremonial-entrance`('error'):禁 `font-black`、`row-stagger/entrance-stagger`、`slide-in-from-*`;popover/modal 开合的 `animate-in fade-in` 属 §7 白名单不禁(DateRangePicker 为在册合法消费者) | 探针文件触发 3 报错;全库 lint 零违例 |

pytest 314 全绿。剩余待立项:A 每日聚合端点波(点阵/sparkline 精确化)、D 文案波、E 三区扩审。


## 十一、A 每日聚合端点波 + D 文案波(并行,2026-07-11)

**E 三区扩审搁置**:用户日常人工做视觉审计,不再立项。

### A(主线自做):每日聚合端点

后端 `GET /api/stats/daily?days=N`(collector-gated,N cap 90):
```json
{ "days": ["2026-07-05", …],
  "runs":     [{"day","job_id","scope","runs","success","partial","failed","running","saved"}],
  "articles": [{"day","source_id","count"}] }
```
- `runs` = collection_job_runs 按 day×job_id×scope 聚合(状态分列计数,worst 由前端推:failed>0→bad、
  partial>0→warn、running>0→run、else ok);`articles` = articles 按 fetched_date 日×source_id 计数;
- 权限:`/api/stats` 计入 COLLECTOR_API_PREFIXES(路由冻结测试同步);tests/test_stats_daily.py。

前端消费(兑现三处「窗口口径」妥协):
1. 运行页:时刻表 7 日点阵/上次结果/「今日 N 次」与总账条五格计数改吃端点(近 30 天精确,
   sub 去「窗口内」字样;流水表本身仍是分页窗口,表脚口径不变);
2. 台账总账条:「近 7 天」格加 7 日收录 sparkline(mini 柱,charts/Sparkline.jsx 新共享件:
   无轴无网格、单色 C_PRIMARY、hover title 数值——sparkline 豁免图例,标题即命名);
3. 节点页调度板行:行内 7 日收录 mini 柱(articles by source_id 匹配 fetcher.id,无数据不渲染)。

### D(Opus 代理):文案波

1. 阅读器相对时间:utils/datetime 加 `formatRelativeTime`(<60s 刚刚;<7 天 分钟/小时/天前;
   否则绝对日期),ReaderTab 文章卡/阅读面时间接入,hover title 完整时间(conventions §1 新条);
2. `·` 间隔号与破折号纪律(conventions §1 已写定)全站 grep 审查修正;
3. reader 术语泄漏复查(归档/采集/分发/层)。
**边界**:不碰 FetchRunsTab/DataTab/FetchTab/api.js/index.css(A 主线独占);
docs/design 样页与 analysis 文档不属文案审查对象。

| 项 | 负责 | 状态 |
|---|---|---|
| A 后端端点+权限+测试 | 主线 | ✅ |
| A 前端三处接线 + Sparkline 共享件 | 主线 | ✅ |
| D 相对时间+分隔符审查+术语复查 | Opus 代理 | ✅ |
| 验收+分波提交 | 主线 | ✅ |

**验收记录(2026-07-11,四点位探针零 pageerror,pytest 316 全绿)**:
- A 端点在实现中补了 `solo` 组(无父单节点直跑)——否则运行页总账条与临时行会漏掉这批;
  运行页 stats 优先、窗口回退,且**来源过滤生效时保持窗口口径**(stats 不分来源,两口径不混);
  节点行 mini 柱列须常驻占位(board-node 是 grid,缺列会错位);
- D 裁决:AdminOpsTab 图表标题「范围 · 明细」面包屑与 ReaderTab「{源} · 收藏」保留
  (代理保守判断采纳——`·` 纪律针对「两侧空格的句内连接」,命名/面包屑不在其列);
  代理留给主线的两处顺手修已随 A 落地(运行页 tt-foot 改分号、节点页试抓 toast 改冒号);
  `formatRelativeTime` 系运行页波既有沉淀,D 仅接入阅读器(带 title 完整时间);
- 点阵/计数/趋势的「窗口口径」妥协自此全部兑现为精确聚合;表脚等描述表内窗口的文案保持诚实。


## 十二、弹窗波(任务编辑器 + 设置面板,2026-07-11)

**样页 = 唯一视觉契约**(用户已认可方向与两项拍板):
- `docs/design/dorami-job-editor-quiet.html`——班次编排单(用户注:总体认可,实装后逐项微调);
- `docs/design/dorami-settings-quiet.html`——控制柜(用户微调已落样页:MCP 行**无状态灯**
  (switch 即状态)、**不体现地址**;拍板:「向量雷达」区退役为服务区只读统计行、
  「接入集成」区更名「服务」)。

**CSS 已由主线预写入 index.css**(`je-*` 编辑器区段 + `sett-*` 设置区段,置于运维区段前)——
**执行侧只写 JSX,不改 CSS**(消除并发冲突面)。

**分工**:

| 项 | 内容 | 负责 | 状态 |
|---|---|---|---|
| P-0 | 样页微调 + 契约 + CSS 两区段预写 | 主线 | ✅ |
| P-1 | FetchRunsTab 任务编辑模态重写为班次编排单(JSX) | Opus 代理 | ✅ |
| P-2 | SettingsModal + settings/* 重构为控制柜(JSX) | 主线 | ✅ |
| P-3 | 验收:lint/build/双弹窗探针(打开态亮暗)/死码清理/提交 | 主线 | ✅ |

**验收记录(2026-07-11,双角色双弹窗亮暗探针零 pageerror,pytest 316 全绿)**:
- 编辑器:cron 解析器支持 `*`/数字/`*/N`/列表/范围(含 a-b/step)/周 0-7,越界或不识别 →
  「保存后按调度器口径生效」且不显示距下次(诚实降级,实机验证:无 cron 任务显示「手动触发,无定时」);
  逻辑零删减;代理三项「拿不准」采纳(距下次不自愈/参数计数字符串比对/目录计数全量口径);
- 设置:双栏被 `.modal-panel` 后定义的 `flex-direction: column` 压过(同特异性后者胜)——
  **技术陷阱**:与既有壳类复合使用的新区段,方向/布局属性要用复合选择器(`.modal-panel.sett-cab`)钉住;
- 服务区向量行在 RAG 关闭环境正确隐藏;reader 设置只见 账户/外观/关于;
- 死文件:IntegrationSection/VectorSection/SectionPrimitives 删除(L4 双入口遗留收账)。
- 用户注:编辑器实装后将逐项微调(方向已认可),待用户目检清单。

**P-1 要点**:头部启用 switch(从脚部 checkbox 升格);目录按 `sourceTaxonomy.groupBySection`
分组;cron 人话回显 + 距下次走字(前端粗解析常见五段式:`*`、数字、`*/N`、列表、范围、星期——
解析不了则回显「保存后按调度器口径生效」诚实降级;仅回显用,不做校验);编排单行 = cron 覆盖
(placeholder 明示「随整体 {cron}」)+ schema 参数 + hover 移除;逻辑(草稿状态机/保存/校验/
pendingJobDraft 预填)原样保留;Modal 外壳沿用。

**P-2 要点**:五区 = 账户/外观/服务/数据同步/关于;服务区 admin-only(MCP 启停一行 +
向量索引只读统计行(fetchVectorStats)+ location.hash 跳台账);VectorSection 文件退役;
现有 gates(canExport/canImport/canToggle)沿用;主题分段复用 .segmented-control。


## 十三、参数退场波(正文抓取参数砍除,2026-07-11,主线自做)

用户拍板:内置节点**恒抓全文**(下游阅读器/翻译/QA/向量化受益),正文相关用户参数退场。

- `fetch_detail`(抓取正文页)砍除,一刀切打开(基类默认 True,原 False 继承者一并打开;
  presets 的冗余覆盖行清除);「源正文抓不可靠」自此是 fetcher 实现该修的事,不是用户旋钮;
- `detail_max_chars` 及 rss 系 `fetch_detail_if_missing`/`detail_min_chars` 砍除;
  截断仅剩 **`DETAIL_HARD_CAP = 40_000`**(article_extractor 常量,病态页兜底,非参数);
- **有意保留**(28 节点仅此三者仍暴露正文参数):`generic_web`/`generic_rss`(config 驱动
  自定义源逃生阀)与 `rss_hn_ai`(外链贴 discovery source 的独立设计,默认关);
- 兼容:kwargs 读取保留作 fallback(实测库中存量任务 per_fetcher_params 为空,无残留生效面;
  前端 schema 驱动自动简化,localStorage 覆盖逻辑本就忽略 schema 外字段);
- **存量文章不自动回填全文**(已按 8000 截断入库者需重抓才变全文,另行决定);
- 编排单每节点行由 3-4 字段收敛为「cron + 单次获取上限」两字段(实机验证);
  pytest 316 全绿。


## 十四、单节点 cron 退场波(一任务一 cron,2026-07-11,主线自做)

用户拍板:任务 = 一组节点 + **一个时刻**;想要不同节奏 = 建新任务(节点可属多任务,表达力零损失)。

- 全链路退场:`per_fetcher_cron_json` 列(Alembic `d41acead77b0`)、调度器 per-node 注册分支与
  `execute_collection_job_node`、序列化/API 模型字段、`next_run_at` 回归单 cron、
  前端编辑器「该节点 cron」字段/「单独时刻」章/覆盖计数/jobbar meta;
- 迁移带**防御性拆分**(faithful):带覆盖的存量任务按 distinct cron 拆为「{原名} · 独立时刻N」
  新任务(覆盖节点与其参数随迁),原任务保留其余节点;基线采用路径(当前 metadata 建表,列本不存在)
  有列存在性守卫直接跳过——tests/test_migrations 新增拆分语义专项;
- 开发库随本次 upgrade 首次走完 Alembic 链:8f6d(实体简化)把遗留 node_groups/fetch_tasks
  转换出 3 个新任务(全量抓取/Anthropic/crawl4ai迁移节点),属历史迁移正当行为;
- 编排单最终形态:每节点行 = 身份 + schema 参数(多数只剩「单次获取上限」);
  pytest 316 全绿(含新专项),lint/build 绿。


## 十五、通用节点隐藏波(is_template 标志,2026-07-11,主线自做)

用户拍板:下游是阅读器,节点质量应在新增时由开发者保证;参数驱动的通用节点质量无保障,
**只在后端保留**(source-configs/source_builder 执行底座 + 新节点开发模板),前端目录不显现;
新增源的正道 = 写代码固化质量有保障的 preset。

- `BaseFetcher.is_template = False` 默认;五个 `generic_*` 置 True;
  **陷阱**:Preset 子类继承 Generic 基类会连带继承标志——四个 Preset 中间层显式重置 False
  (registry 断言恰为五个 generic);
- registry 元数据透出 `is_template`;App.jsx 过滤由硬编码 `generic_web` 升级为标志驱动;
- 实机:节点目录 28 → 23,「通用」零出现;库中无 generic 历史运行,名称解析无退化面;
- pytest 316 全绿,lint/build 绿;CLAUDE.md 事实同步。


## 十六、参数固化波(preset 非 limit 参数全退场,2026-07-11,主线自做)

用户拍板:特定节点的抓取偏好是固定的,全部固化为类默认;可见节点的用户参数**恰好只剩
「单次获取上限」**。

- 三组 schema 收敛:GitHub 仓库(fork/归档恒排除、README 恒补充且对齐全文 40k)、
  GitHub Releases(预发布与否 = 各 preset 按仓库发版习惯的类默认)、HN(去噪门槛 10/0 与
  「外链不抓正文」是设计本身);模板节点参数面不动;
- **BaseFetcher.fetch 加 schema 白名单过滤**(非模板节点只接受 schema 声明的参数,
  schema 即契约)——治本:任何历史/未来残留一律无效;
- **残留击穿修复**:cron 退场波的 8f6d 迁移把旧节点组参数(detail_max_chars 8000/12000/20000 等)
  搬进了新任务并经 fallback 生效,击穿「恒抓全文」——Alembic e7a3c19b5d02 数据清洗剔除
  已退场字段(generic_* 节点参数面防御性跳过);**教训:「库无残留」的判断会被后续迁移作废,
  fallback 兼容策略不如契约过滤**;
- 测试:白名单过滤契约(tests/test_fetch_param_whitelist.py)、清洗迁移语义、
  三个旧 schema 契约测试改写;pytest 319 全绿,lint/build 绿;
- 观察记录:迁移任务引用了一个已不在 registry 的化石节点 id(web_bytedance_seed_models),
  编排单以原始 id 兜底显示、运行时自动跳过——无害,用户可自行从任务中移除。


## 十七、点名册波(任务编辑器 v2,2026-07-11,主线自做)

**样页 = 唯一视觉契约**:`docs/design/dorami-job-roster-quiet.html`(用户认可;
替代 dorami-job-editor-quiet.html 的双面板体,其头部时刻区设计被继承)。

参数四波收敛后,任务编辑本体 = **勾人 + 定额 + 一个时刻**——「目录→编排单」双面板
(同批节点显示两遍、右栏为一个数字耗一行)随功能过时,重构为**单栏点名册**:

- 勾选与配置同一行:勾中行尾浮现「上限」输入;值=默认 muted、改动 ink 加粗(安静 diff 信号);
- 模态 5xl(1020px)→ 2xl(672px);工具行 = 搜索 +「已编入 N / 总数 · 改动 M」;
- 组头 sticky + hover 浮现 全选/清空(setGroupChecked 批量,保留已改参数防误触丢配置);
- **编外兜底组**(实装中发现):已编入但不在目录的化石节点(如迁移遗留
  web_bytedance_seed_models)原本只出现在计数里没法清理——列表尾追加「不在目录」组,
  可见可移除;
- 行尾定额按 schema 遍历渲染(当前非模板契约=仅 limit;未来长出新参数同款顺排,
  形态可增长不回退双面板);数据层(jobDraft/toggle/updateDraftParam/保存校验)零改动;
- css:je-* 区段整体替换为 jr-*(je 零残留);
- 实机验收:编辑/改值 diff/搜索/暗色零 pageerror;pytest 319 全绿,lint/build 绿。

**目检修复(97bc3d5)**:①模态夺焦根治——useModalA11y 的 effect 依赖含 onClose(调用方内联箭头
每渲染新引用),每秒走字重渲染触发 cleanup 归还焦点→重聚焦面板首元素;hook 内 ref 化 onClose
(**陷阱档案:含回调的 a11y/事件 hook,回调一律 ref 化,不进 effect 依赖**——全站模态受益);
②placeholder 去口语化;③jr-sheet 高度与 modal-panel 同步(88vh 低视口裁脚);④空 cron 不渲染 echo。

## 十八、调度台目检第三轮(三项退役 + 时间轴否决,2026-07-12,主线自做)

用户目检第三批(自动刷新钮/对象筛选/jobbar 上下文条/时间轴脑洞),三项实施一项否决:

- **「自动刷新」开关 UI 退役(运行页 + 节点页同款一并撤)**:用户不喜欢显式开关+倒计时
  ——「其它网页没见过这种设计」。要害:寻常网页不是没有自动刷新,是**不把机制摆上台面**。
  两页轮询全部转入静默(运行页 30s / 节点页 45s,页面激活/可见时后台拉取,无开关无读数),
  UI 只留手动刷新图标钮;localStorage 偏好、arLeft 走字、.signal-autorefresh/.signal-countdown
  css 全部退役(.signal-switch 保留,自动向量化等开关仍引用)。
- **「对象:任务/临时」mini-seg 退役**:时刻表点选任务行/「临时抓取」行即是对象维度的筛选,
  右上 mini-seg 与之全量重复(选中某任务后行集必然全是该任务)。strip-tools 只剩「触发」一组。
- **jobbar 上下文条退役,任务动作回归左栏**:原布局「左栏=任务列表,任务的动作却横在右栏顶」
  归属错位(用户直觉正确)。重构:时刻表行外包 .tt-item(选中态竖条/soft 底移到包裹层),
  选中任务行下方**就地展开动作条 .tt-acts**。首版散排文字钮换行毛糙被用户目检打回,
  二版定形为**卡片底部仪表条**:hairline 分格等宽单行(运行/测试/编辑/停用|启用/删除,
  短标签 + title 全称),通栏贴底,soft 底上 hover 用 paper 抬起;停用任务只给
  启用/编辑/删除;临时抓取行无动作。
  右栏回归纯流水;任务/临时/来源三种过滤指示统一收进表脚(文案 + .foot-clear ✕ 即点即清)。
  注意 .tt-row + .tt-row 相邻边框随包裹层改为 .tt-item + .tt-item。
- **时间轴(过去+未来运行规划,可挪缩)否决**:①当前任务数个位、cron 均为每日一点,
  轴上信息密度近乎为零;②未来触发是 cron 的确定性重复,时刻表按下次触发排序 + 「距下次」
  倒计时已是同一信息的列表投影;③可拖拽缩放属重交互组件,与静默仪器基调相悖。
  任务规模与 cron 多样性显著增长后可重估(轻量形态:静态 24h 刻度条,过去实点/未来空心点)。

验收(probe_runs7,用户 vite 5173 实机):两页 .signal-autorefresh 均 0;strip-tools mini-seg
仅 1 组;选中任务行 tt-acts 五钮齐、is-sel 竖条在包裹层、jobbar 零残留;表脚过滤文案 + ✕
清除联动;adhoc 行无动作区;零 pageerror。

## 十九、集成页目检波(八项,2026-07-12,主线+Opus 代理协作)

用户目检集成页八项,主线做根因诊断+后端+全局项,布局重排委派 Opus 代理:

- **① 流式页偶现锁滚(根因级修复,主线)**:`html:has(.ledger-shell,…){overflow:hidden}`
  的根滚动封死规则被**隐藏面板**误触发——App 的 tab 面板挂载后以内联 display:none 隐藏,
  而 `:has()` 不看 display,访问过任一壳页后根滚动就永久封死,流式页(集成页)随之锁滚。
  修复:面板隐藏改 class(`.tab-panel.is-off`),封死规则限定
  `html:has(.tab-panel:not(.is-off) :is(.ledger-shell,.nodes-shell,.reader-shell,.runs-shell))`
  ——只在壳页**可见**时锁根;.runs-shell 一并入列(此前靠台账常驻挂载偶然生效)。
  **陷阱档案::has() 命中不看 display,任何「挂着但藏起」的 DOM 都可能误触发状态类选择器;
  面板显隐一律走 class,让 :not() 可以参与判定。**
- **②③ 区顺序与三卡对齐(代理)**:「AI 资讯日报」区移页首(管理员;读者仍交付通道居首);
  交付通道改行式——MCP 卡独占全宽,个人聚合接口+Claude 技能包 `.channels-pair`
  两列等高(stretch),.channels-side 纵排退役,三卡边缘全部成线。
- **④ 日报卡消空白(代理)**:brief-card 三列(260/1.2fr/1fr)改两列(280px | 1fr)——
  「手动生成」列并入「近期日报」列头(紧凑主钮,不再 w-full),pipeline/失败提示/取材口径
  依序其下,历史列表填满右列。
- **⑤ 管理员令牌全库直通(主线,后端+契约)**:管理员不设订阅(订阅是读者面概念),
  `feed_articles_for_owner` 对 admin 不再按订阅收窄(显式 source_ids 原样生效),
  `resolve_subscription_sources_by_token` 对 admin 令牌显式返回 [](MCP 契约「未限定来源」,
  原先靠 admin 恰好零订阅偶然成立);测试 `test_personal_feed_admin_token_covers_whole_archive`;
  reader_subscription.md 增补 Admin exception 段。前端文案按 isAdmin 分流(代理)。
- **⑥ 模型 chip 跳转(主线 App 接线+代理组件)**:chip 改 button,onOpenModelConfig →
  jumpWithFocus('admin', …, {sub:'ai'});AdminOpsTab 接入 pendingFocus 单通道消费 {sub}。
- **⑦ 输入框全局收紧(主线)**:.form-input 14px/10px 内距 → 13px/7px(总高 ~42→36px),
  与 .mini-seg/.action-button 密度刻度对齐;编辑器/设置面板抽检 36px 无破版。
- **⑧ 日报区头去引导(代理)**:「仅管理员」zone-badge 与 zone-hint 引导句删除,
  .zone-badge 死类清扫。

验收:管理员/读者双视角探针全过(区顺序、pair 两卡底部像素相等、chip 跳转落 AI 子页、
台账挂载后集成页 scrollY=400 可滚、三壳页激活时根 overflow=hidden 不回退)、
pytest 315 过(5 失败为 HEAD 基线同现的事件循环环境旧疾,非回归)、lint/build 绿。
代理红线自查通过(无功能减项)。

### 十九-补:集成页右边距目检 → 注释终止符陷阱(2026-07-12,主线)

用户报「集成页卡片右边距比其它页大」。表因:.integ-page 卡 1200px 上限;深挖后真相反转——
.admin-page 声明的 1240px 上限**从未生效过**:运维波区段注释里列举复用类
「….ledger-drawer*/」「.action-button*/.form-input」中「星号+斜杠」拼成注释终止符,
注释提前收口,残文粘进下一条选择器,把 .admin-page 基础规则整条毒死(font-size:13px 也一并)。
所以运维页一直满幅渲染并通过历次目检——**满幅才是全站既成口径**(壳页亦满幅)。

处置:拆除两枚注释炸弹(星号后改「、」);.integ-page/.admin-page 均撤 max-width,
与全站满幅对齐(媒体查询里的 max-width:none 覆盖随之清冗余);.admin-page 的
font-size:13px 首次真正激活,截图目检运维页无破版。

**陷阱档案:CSS 注释里列举通配类名(`.foo*`)时,星号后禁止紧跟斜杠——「*/」在注释文本
里同样是终止符;修复时的说明注释里也不得原样引用该序列(本波自己踩了一次)。
排查用正则:`[a-zA-Z0-9-]\*/`。**

### 十九-补2:字体审计波(标题层级刻度,2026-07-12,主线)

用户目检:「定时配置」字号 < 正文、位置比「近期日报」靠上、两者字号 < 「MCP 服务」;
要求全站审计 + 规则性修改 + 规范性约束。

审计定性:三个症状同源——brief-card 是一张**没有卡标题的卡**,「定时配置/近期日报」承担的
就是卡标题职责,却取了 12px 小节刻度(< 正文 13、< 邻卡 .card-title 15)。全站标题类静态盘点:
区级(zone/tt-head/sett-head/section)14/700 一致,卡级 card-title 15/700,小节级 12.5–13/700,
唯 brief-col-title 倒挂。

处置:①.brief-col-title 升卡题刻度(15/700/ink);②列头顶对齐——.brief-col-head 改
align-items:flex-start,32px 生成钮负 margin 配平(不许控件把标题压离基线);③增量游标行
CSS 化(.brief-cursor-*):11px mono 刻度(同 token-meta),19 位时间戳 280px 列内完整展示,
顺手清掉 text-slate-500 硬编码;④**规范入 conventions §3**:标题层级刻度表(页 24/区 14/
卡 15/小节 12.5–13)+ 三条硬约束(同职责同刻度/标题不小于辖区正文/新标题必须复用在册类),
并明确「无卡题的卡,最高层分区标题即卡题职责,取卡级刻度」。

验收:两列标题 computed 15px 且 y 严格相等;游标 scrollWidth ≤ clientWidth(无截断);
五页扫描所有 *-title 类,每类单一字号(无同类多字号);零 pageerror。
