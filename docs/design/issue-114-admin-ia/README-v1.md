# issue #114 运维管理信息架构 · R2 样页

> R1(「域 → 左侧子页栏 → 三带」)已被否:分得太碎、侧栏挤正文。R2 改成两层:**运维首页 = 宏观数据 + 目录索引,点一下进满幅功能页**。
> 本稿只是样页:单文件 `index.html`,离线可开;**所有数字为示意**(固定种子,量级取内网读者实例:约 190 账户、几十个源),互相自洽但不来自真实库。

## 怎么看

```bash
cd docs/design/issue-114-admin-ia && python3 -m http.server 8765
# 打开 http://127.0.0.1:8765/   (直接双击 index.html 用 file:// 打开也行)
```

- 顶部深色条是**样页 spec 栏**(不是产品界面),右端「收起」后右下角留一个「样页控制」胶囊。
- 路由与产品同形:`#/admin` 首页 · `#/admin/<页>` 功能页 · `#/admin/<页>/<板块>` 落到板块并短促 wash 高亮。浏览器后退可用;左轨「运维」与面包屑「运维管理」都回首页。
- spec 状态写在查询串里,可直接转发某个状态,例如 `index.html?theme=dark&sw=read#/admin`、`index.html?card=1#/admin/podcast`、`index.html?view=admin#/admin/accounts`、`index.html#/admin/podcast/asr`(深链落位)。
- 自检截图在 `tmp/issue-114/shots/`(不入库),报告 `tmp/issue-114/check-report.json`。

## 本稿要回答的问题

1. 首页能否同时做「宏观视角」和「目录」,且 1280×800 一屏看全 10 个入口?(实测:待处理条在场时磁贴底边 y = 658.5,余 141px;spec 栏展开时 695.5。)
2. 管理员「不知道某块功能在哪」:磁贴底部直接列板块名(一跳到板块)+ 常驻「搜功能」(带同义词)+ 面包屑 ▾ 横切——三条路够不够?
3. 去掉二级侧栏、功能页满幅后,「去卡片」是否比现行卡片更省横向空间、更好读?
4. 首页上每个数字后端是否真给得出;要不要为首页扩一个聚合接口(会突破 issue 里「不改后端」的边界)。

## spec 开关

| 开关 | 档位 | 作用 |
|---|---|---|
| 主题 | 亮 / 暗 | 挂 `<html data-theme>`,与左轨「主题」同步 |
| 视角 | 根管理员 / 普通管理员 | 普通管理员:账户页只剩聚合 KPI + 账户增长 + 游客;首页账户磁贴板块链接收敛;宏观一排去掉「活跃账户」「阅读次数」(`/admin/accounts` 对非根 403) |
| 首页总闸 | 可拨 / 只读 | 可拨 = 行内 `ledger-switch`(与功能页同件,状态全样页共享,拨了弹现行文案的 toast);只读 = 状态灯 + 名称 + 开/关,点击落到所在功能页的该板块 |
| 待处理 | 有事 / 无事 | 无事时待处理条整行消失、下面上提;磁贴里对应数字同步归零变灰 |
| 功能页 | 去卡片 / 保留卡片 | 去卡片 = 板块间 zone 标题 + 一条细线、表格铺满内容宽;保留卡片 = 现行 `surface-card` 外框 |
| 跨页索引 | 关 / 开 | 开:搜功能结果含其它页签与设置柜分区;首页底部多一行这些入口(点了 toast 示意) |
| 数据来源 | 隐 / 显 | 首页每个数字旁出 `现有` / `需聚合` / `需新增` 小标,悬停看接口与字段 |

## 页面结构

**首页**(高保真,自上而下):页头(`运维管理` + 搜功能框 + 时间窗 `mini-seg`)→ 待处理条(有事才出现)→ 总闸一排(8 个)→ 宏观一排(5 格 + 迷你走势,只有这一排吃时间窗)→ 目录磁贴 10 块 →(跨页索引开时)其它页签与设置入口。

**磁贴排布 = 5 列 × 2 行,同组同列**:用户 | 内容 | 内容 | 读者 | 分析 / 消息。理由:①两行等高、五列等宽,头部与网格都平衡,1280 宽每块约 222px 刚好放下「两个数 + 板块链接」;②组按列聚拢,眼睛竖着扫一列就是一个组;③组名作右上角标(12px faint),不另起组头行——组头行要么给第 5 列写「分析 · 消息」,要么让「消息」悬在第二行,都比角标别扭。代价:第 5 列是两个单页组拼的,「内容」占两列。替代方案见待拍板②。

磁贴内容:页名 + 小图标 + 组角标;两个**当前态**数字(不吃时间窗,有待处理时 warn / bad 色 + 形状标记双通道);底部一行板块名链接(点板块名直达,点磁贴其余处进页首)。

**功能页**:满幅无侧栏。页头 = 面包屑 `运维管理 › 播客 ▾`(根在原位不动,进页只是向右长出一段;▾ 弹出全部功能页,按组分列、当前页打勾)+ 搜功能框 + 页内动作(用时间窗的页才带时间窗)。页内顺序统一「数字 → 开关 / 参数 → 表」。

高保真只做首页、`podcast`、`accounts`(含用户活动抽屉、勾选批量条、列头排序 / 轮换筛选、分页);其余 8 页低保真(真实页头 + 真实 zone 标题 + 灰色骨架,高度示意内容量),跳转与目录关系可走通。

## 功能页清单与板块映射(对照 `AdminOpsTab.jsx` 与 `components/admin/*.jsx` 现行代码核过)

| 组 | 页 id | 名称 | 板块(页内顺序;`id` 即深链末段) | 来自现行 |
|---|---|---|---|---|
| 用户 | `accounts` | 账户 | 概览 `kpi`(根 / 非根两版)· 账户增长 `growth` · 游客 `guest` · 活跃用户 Top `top`(根)· 账户管理 `table`(根;含批量条、用户活动抽屉) | 用户档 + `AccountGrowth` + `GuestZone`(含游客设置抽屉) |
| 用户 | `audit` | 操作审计 | 审计表 `log` | `AdminAuditPanel`(现行在用户档末尾) |
| 内容 | `reading` | 阅读与榜单 | 内容概览 `kpi` · 各源热度 `heat` · 收藏 TOP `top` · 榜单快照 `rank` | 内容档 KPI / 两卡 + `RankingSnapshotPanel` |
| 内容 | `podcast` | 播客 | 概览 `kpi` · 处理参数 `params` · ASR 配额策略 `asr` · **TTS 回执缓存** `tts` · 单集处理 `tasks` · 中文精简音频 `audio`(+ 单集抽屉) | `PodcastZone` 全部 |
| 内容 | `user-sources` | 自定源 | 总闸 `switch` · 概览 `kpi` · 源表 `table` | `UserSourcesPanel` |
| 内容 | `external` | 存储与外部资源 | 存储与备份 `storage` · 媒体库 `media` · 媒体热点图 `heatmap` · X API `xapi` | `StorageStatusPanel` + 媒体库 KPI + `MediaHeatmap` + X API chip / KPI |
| 读者 | `reader-ai` | 读者 AI | AI 总闸 `switches`(总闸 / 新账号默认开 / 模型 chip)· 日预算 `budget` · AI 用量 `kpi` · 每日用量 `usage` | AI 档 |
| 读者 | `reader-defaults` | 读者默认与开关 | 公开分享 `share` · 读者点播 `ondemand` · 新账号默认订阅 `defaults` · 早报与兴趣 `brief` | 内容档三条开关卡 + `BriefInterestZone` |
| 分析 | `analysis` | 分析与标签 | 分析开关 `flags` · 分析指标 `metrics`(含新闻价值分布)· 完整分析回填 `backfill` · 目录版本 `catalog` · 标签总账 `ledger` | `AdminTaxonomyPanel`(含 `FullAnalysisBackfillCard`、`TaxonomyLedger`) |
| 消息 | `inbox` | 反馈与公告 | 反馈收件箱 `feedback` · 公告管理 `notice` | `FeedbackInboxPanel` + `AnnouncementsPanel` |

任务书表里漏掉、本稿补归的:**TTS 回执缓存**(`PodcastZone` 内,归播客)、**新闻价值分布**(`AdminTaxonomyPanel` 分析指标内)、**目录版本 KPI**(标签治理区,单列为 `catalog` 板块便于搜索直达)、**游客设置抽屉**(游客区齿轮,四类 AI 日额度)。现行代码里没有其它未归位的板块。

## 首页数据来源

`现有` = 某个现有接口的某字段直接给出;`需聚合` = 数据在库里,要新加或扩展聚合接口才拿得到;`需新增` = 后端目前没有这个数。下表逐项核过路由代码(`src/api/routers/admin.py` 等)与 `frontend/src/api.js`。

| 位置 | 首页项 | 接口 → 字段 | 来源 |
|---|---|---|---|
| 宏观 | 近 N 天活跃账户(数) | `GET /api/admin/accounts?days=N&limit=1` → `summary.logged_in_window`(仅根) | 现有 |
| 宏观 | 活跃账户(走势) | 站点级逐日去重登录:`LoginEventRecord` 已有,现只有单用户 `logins.by_day` | 需聚合 |
| 宏观 | 阅读次数(数) | 同上 → `summary.reads`(仅根) | 现有 |
| 宏观 | 阅读次数(走势) | 站点级逐日阅读:`ReaderReadRecord(day, reads)` 已有,现只有单用户 `reads.by_day` | 需聚合 |
| 宏观 | AI 总调用 / tokens(数 + 走势) | `GET /api/admin/ai-usage?days=N` → `totals.calls` / `total_tokens` / `by_day[]`(只含有数据的日子,前端补零) | 现有 |
| 宏观 | 游客访客 / 转化(数 + 走势) | `GET /api/admin/guest?days=N` → `stats.kpi.visitors` / `conversions` / `stats.by_day[]` | 现有 |
| 宏观 | 新增账户(数 + 走势)、现存 | `GET /api/admin/account-growth` → `series[{day,new}]`(全量,前端按窗口切)/ `totals.accounts` | 现有 |
| 待处理 | 备份未完成 | `GET /api/admin/storage/status` → `backup.status = failed` / `last_error` | 现有 |
| 待处理 | 播客失败 N | `GET /api/admin/podcast-premium-tasks?page_size=1` → `breakdown.stage.failed`(另有 `reconciliation`) | 现有 |
| 待处理 | 回填失败 N | `GET /api/admin/analysis/backfills` → 逐条扫 `items[].status ∈ failed / partial_failed`(分页,无汇总字段) | 需聚合 |
| 待处理 | 读者 AI 预算 ≥ 80% | `GET /api/admin/ai-beta/global` → `tokens_used_today / daily_token_budget`(只计读者用途;0 = 不限时不出) | 现有 |
| 待处理 | 自定源失败 N | `GET /api/admin/user-sources` → `kpi.failing_count` | 现有 |
| 待处理 | 反馈待处理 N | `GET /api/admin/feedback?limit=1` → `counts.open`(不随筛选) | 现有 |
| 待处理 | 标签候选 N | `GET /api/admin/taxonomy/state` → `unresolved_candidate_count` | 现有 |
| 待处理 | X API 预算 ≥ 80% / 已封顶(示意数据不触发) | `GET /api/x-api/quota` → `estimated_cost_usd / monthly_budget_usd`、`blocked`(按 UTC 月) | 现有 |
| 待处理 | ASR 配额冻结(不触发) | `GET /api/admin/podcast-asr-quota` → `usage_status = frozen` | 现有 |
| 待处理 | 播客存储保护(不触发) | `GET /api/admin/podcast-artifacts/stats` → `storage_pressure` | 现有 |
| 总闸 | 用户 AI | `GET/POST /api/admin/ai-beta/global` → `enabled` | 现有 |
| 总闸 | 读者点播 | `GET/POST /api/admin/reader-ondemand` → `enabled`(`blockers[]` 非空时实装应亮 warn) | 现有 |
| 总闸 | 公开分享 | `GET/POST /api/admin/public-share` → `enabled` | 现有 |
| 总闸 | 自定源 | `GET/POST /api/admin/user-sources/config` → `enabled` | 现有 |
| 总闸 | 游客访问 / 游客 AI | `GET /api/admin/guest` → `settings.enabled` / `ai_enabled`;写 `PUT /api/admin/guest/settings` | 现有 |
| 总闸 | 文章分析 / 个人早报 | `GET/PUT /api/admin/analysis/config` → `feature_flags.article_analysis_enabled` / `personal_digest_enabled` | 现有 |
| 磁贴 | 账户 · 账户数 / 停用 | `GET /api/admin/account-growth` → `totals.accounts` / `disabled`(全体管理员可见) | 现有 |
| 磁贴 | 操作审计 · 近 7 天操作 / 被拒 | `GET /api/admin/audit-log?days=7&limit=1` → `total`;加 `status=denied` 再取一次(非根看不到 `/api/accounts` 行,数会小) | 现有 |
| 磁贴 | 阅读与榜单 · 累计阅读 / 榜单快照 | `GET /api/admin/content` → `totals.reads`;`GET /api/admin/rankings/status` → `snapshot.generated_at` / `status` | 现有 |
| 磁贴 | 播客 · 失败 / 已发布音频 | `breakdown.stage.failed`(同上);`GET /api/admin/podcast-artifacts/stats` → `published` | 现有 |
| 磁贴 | 自定源 · 源数 / 失败中 | `GET /api/admin/user-sources` → `kpi.source_count` / `failing_count` | 现有 |
| 磁贴 | 存储与外部资源 · 自动备份 / 媒体库 | `storage/status` → `backup.status`;`GET /api/admin/media/stats` → `disk_bytes` | 现有 |
| 磁贴 | 读者 AI · 今日预算 / 账户已开 AI | `ai-beta/global`(同上);`GET /api/admin/overview` → `accounts.ai_beta_enabled` | 现有 |
| 磁贴 | 读者默认与开关 · 有效分享 / 默认订阅源 | `public-share` → `live_count`;`GET /api/admin/reader-defaults` → `source_ids.length` | 现有 |
| 磁贴 | 分析与标签 · 分析待处理 / 未归并候选 | `GET /api/admin/analysis/metrics?days=7` → `article_analysis.status_counts.pending`;`taxonomy/state` → `unresolved_candidate_count` | 现有 |
| 磁贴 | 反馈与公告 · 反馈待处理 / 生效公告 | `feedback` → `counts.open`;`GET /api/admin/announcements` → 数 `items[].is_active`(无计数字段,公告量小,前端数) | 现有 |

**需新增、默认未放进首页的**:近 N 天播客处理量(无窗口、无逐日)、存储占用趋势(只有快照)、读者点播今日用量(只有逐用户日限额,无全局用量)。

**口径提醒**(实装注意):①首页「AI 总调用」取 `/admin/ai-usage`,**含系统用途**(日报、文章分析…),与账户页 KPI「AI 调用」(`summary.ai_calls`,按账户)不是一个数,所以首页标签写「AI 总调用」(与读者 AI 页「总调用」同名);②「今日预算」只计读者用途,对不上 `ai-usage` 的今天那一行;③`/admin/overview` **不收任何参数、没有窗口**,AI 数是用户表上的累计计数,不能拿来做时间窗数字。

**首页一次要打的请求**:以现有接口拼,根管理员首页约 **26 个 GET**(上表去重),其中 `/admin/user-sources` 与 `/admin/content` 整表返回、首页只用其 KPI。这是待拍板④的依据。

## 本稿新增类

`ops-*`(实装时进 `index.css`):`ops-search` `ops-search-icon` `ops-search-input` `ops-kbd` `ops-pop` `ops-search-pop` `ops-hit` `ops-hit-name` `ops-hit-page` `ops-hit-enter` `ops-hit-empty` · `ops-crumb` `ops-crumb-root` `ops-crumb-sep` `ops-crumb-cur` `ops-crumb-pop` `ops-crumb-col` `ops-crumb-group` `ops-crumb-item` `ops-crumb-check` · `ops-row-lbl` `ops-pending` · `ops-switches` `ops-sw` `ops-sw-name` `ops-sw-state` · `ops-macro-row` `ops-macro-line` `ops-spark` · `ops-dir` `ops-tile` `ops-tile-head` `ops-tile-icon` `ops-tile-name` `ops-tile-group` `ops-tile-nums` `ops-tile-stat` `ops-tile-lbl` `ops-tile-num` `ops-tile-mark` `ops-tile-links` `ops-tile-li` `ops-tile-link` · `ops-xidx` `ops-xidx-item` · `ops-sec` `ops-sec-body` `ops-flash` `ops-flat` · `ops-chart` `ops-rank*` `ops-bars*`(样页手绘图,实装仍走 recharts)· `ops-lofi*`(低保真骨架,实装不要)。

`spec-*`(样页 chrome,实装不要):`spec-bar` `spec-title` `spec-group` `spec-name` `spec-seg` `spec-btn` `spec-hide` `spec-pill` `spec-src` `spec-srcline`。

其余全部复用现行在册类:`page-head` `page-title` `page-head-actions` `win-label` `mini-seg` `form-input-inline` `surface-card` `kpi-strip` `kpi*` `zone-head` `zone-title` `zone-hint` `zone-acts` `ai-switchboard` `ai-light` `ai-switch-lbl` `ai-divider` `ledger-switch` `stamp*` `card-title` `acct-table` `acct-th` `acct-row` `acct-*` `cell-2` `score-pair` `rowact-btn` `rowacts` `knob` `sett-field*` `table-foot` `pager*` `ledger-batchbar` `ledger-drawer*` `tiles` `tile*` `login-card` `guest-*` `app-rail` `reader-vrail-*` `rail-*`;图标为 lucide-react 1.14.0 同形内联 SVG。

## 待拍板

1. **功能页粒度 10 页**是否合适?可选更粗:把「读者 AI」并进「读者默认与开关」成一页「读者」(9 页),把「操作审计」并回「账户」(8 页)。本稿倾向 10 页:每页一屏半以内,磁贴两行刚好铺满。
2. **磁贴排布**:本稿 5 × 2「同组同列 + 组角标」。备选 A:组头行 + 不等宽(内容组占 2 列宽);备选 B:按「管谁的什么」改组,把「反馈与公告」归「用户」、「操作审计」与「分析与标签」并成「治理」,凑成 5 组 × 2 页、组名可作列头(会改动任务书的组划分)。
3. **首页总闸可拨还是只读**:可拨省一跳,但 8 个实心开关一排,accent 很满;关「公开分享」「游客访问」会立即作废外链 / 踢掉在线游客,现行功能页也是一点即生效、不确认——放首页后误触面更大。本稿倾向**只读**(状态灯 + 开/关字,点了落到板块),或「可拨但关闭方向过确认」。`候选证据`、`候选自动激活`(开启需确认与前置条件)本稿不放首页。
4. **首页聚合是否扩展后端**:以现有接口拼要约 26 个请求,且「活跃 / 阅读」走势、回填失败汇总拿不到。建议新开一个只读聚合端点(如 `GET /api/admin/home?days=N`,按角色剥字段)一次给齐;这会突破 issue 里「不改后端」的边界。不扩:首页去掉两条需聚合走势(只留数字)和「回填失败」。
5. **跨页索引是否纳入**:纳入后搜功能能找到「凭据」「数据同步」「MCP 接入」等设置柜分区,首页底部多一行入口;代价是运维首页开始承担全站导航。本稿倾向**只进搜索、不进首页磁贴行**。
6. **去卡片还是保留卡片**:去卡片后表格与 KPI 条铺满内容宽、少一层阴影,板块间靠 zone 标题 + 细线;保留卡片与现行其它页一致。本稿倾向功能页**去卡片**、首页磁贴保留卡片(磁贴本身是可点对象)。

## 偏离任务书与已知局限

- 时间窗**只驱动宏观一排**;磁贴数字一律是当前态(审计磁贴固定写「近 7 天」)。
- 总闸一排放 8 个(任务书举例的「分析发布」取 `文章分析`、`个人早报` 两个;两个候选开关留在分析页)。
- 首页磁贴的板块链接只列 2–4 个常用板块(`tile` 标记),完整板块靠页内与搜索。
- 1440 / 1920 下磁贴随视口高度略增高(上限 188px),1920×1080 下首页下方仍有空白——两行目录本来就不填满大屏,未硬撑。
- 顺带发现的现网小毛病:`PodcastZone` 处理参数里 `<strong>` 在 600 的 `.knob` 内按 `bolder` 落成 900 字重,违反「字重只 400 / 600」;样页已就地压回 600,实装顺手修。
- 低保真页的骨架是静态灰块(不闪烁),不表示加载态。
- 样页图表为手绘 SVG,行为(悬停浮层等)未复刻;单集抽屉、游客设置抽屉、新建账户弹窗只给 toast 示意。

## 构建

源与脚本在 `tmp/issue-114/kit/`(已 gitignore):kit 内 `src/` 下的 data / registry / app 三个脚本与 ops 样式表;现网样式取自与本 worktree 逐字节相同的 `index.css` / `mobile.css` 的 vite 构建产物,按本页实际用到的类名抽取(`check.py --collect` 走遍状态收类名 → `build.py` 抽取)。`check.py` 跑任务书 §6 的断言与截图。
