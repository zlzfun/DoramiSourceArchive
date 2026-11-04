# issue #114 运维管理信息架构 · R2 第二版样页

> 第一版(同目录 `v1.html` + `README-v1.md`)的方向「首页即目录 → 满幅功能页、面包屑横切、搜功能」已获认可,保留。
> 第二版回应三条反馈:**聚合与削减**、**打破死板(大小变化 + 大数字 + 图表)**、**参考 Codex 调研**。
> 本稿只是样页:单文件 `index.html`,离线可开;**所有数字为示意**(固定种子,量级取内网读者实例:约 190 账户、几十个源),互相自洽但不来自真实库。

## 怎么看

```bash
cd docs/design/issue-114-admin-ia && python3 -m http.server 8765
# 打开 http://127.0.0.1:8765/   (直接双击 index.html 用 file:// 打开也行;spec 栏「对照第一版」打开 v1.html)
```

- 顶部深色条是**样页 spec 栏**(不是产品界面),右端「收起」后右下角留一个「样页控制」胶囊。
- 路由与产品同形:`#/admin` 首页 · `#/admin/<页>` 功能页 · `#/admin/<页>/<板块>` 落到板块(开关网格里的单格也能深链)并短促 wash 高亮。浏览器后退可用;左轨「运维」与面包屑「运维管理」都回首页。
- spec 状态写在查询串里,可直接转发,例如 `index.html?theme=dark#/admin`、`?pending=0#/admin`、`?sw=toggle#/admin`、`?card=1#/admin/reader`、`?view=admin#/admin/accounts`、`?rm=tokens&rd=user#/admin/reader`、`?days=7#/admin`、`#/admin/reader-ai`(旧深链)。
- 自检截图在 `tmp/issue-114/shots-v2/`(不入库),报告 `tmp/issue-114/check-report-v2.json`。

## 本稿要回答的问题

1. 三条横带并进不等大磁贴后,首页还能不能「一眼看全局 + 一跳到功能」?(实测:1280×800 九块磁贴全部完整可见,末行标题行底 y = 663、末行底边 y = 790;1440×900 底边 y = 886;1920×1080 底边 y = 1066,占视口 99%,v1 只有 66%。)
2. 告警数字只出现一次(状态磁贴),其余磁贴只给正常指标 + 状态点——够不够让人发现问题?
3. 功能页 10 → 8、每页「主角区(大图 8 列 + KPI 竖排 4 列)→ 并排块 → 宽表」的主次,是否比 v1 的等宽横带好读?
4. 每个数字和图表后端是否真给得出;首页要不要扩一个聚合接口。

## v1 → v2 改了什么、为什么

| 维度 | v1 | v2 | 为什么 |
|---|---|---|---|
| 首页结构 | 待处理条 + 总闸一排 + 宏观五格 + 10 块等大磁贴 | 12 列不等大 bento:8 块功能磁贴 + 1 块「状态」磁贴 | 用户嫌死板、等大、数字重复 |
| 宏观五格 | 一排 5 个窗口数字 + 折线 sparkline | 取消,各数字归到所属磁贴,做成主角数字或图 | 「账户 186」等在横带和磁贴各出现一次 |
| 待处理 / 总闸 | 两条整宽横带 | 合成「状态」磁贴:上半待处理(按严重度)、下半 8 个总闸(默认只读,点了落到所在板块) | 去重;告警只出现一次 |
| 磁贴内容 | 两个当前态数字(含告警数)+ 板块链接 | 主角数字 + 一张图 + 小数 + 板块链接(≤ 4);有待处理时只给状态点 | 关键数据占大版面、要有图 |
| 功能页 | 10 页,板块等宽纵向平铺 | 8 页(读者 AI + 读者默认 → 读者功能;操作审计 → 账户页底部);主角区 + 并排块 + 宽表 | 合并「读者能用什么 / 默认是什么 / 用了多少」;审计本在用户档末尾 |
| 图表 | 手绘折线 sparkline、无悬停 | 逐日计数一律离散柱、账户增长阶梯线、分布直方图、构成分段条、覆盖热点格、预算环 / 计量条;全部带统一悬停浮层 | Carbon / shadcn 规范;v1 没做 Tooltip |
| 组名 | 磁贴右上角组名角标、面包屑弹层按 5 组分列 | 退役:磁贴按重要度排,弹层单列 8 项(与首页同序) | 8 页里 4 个组只有一页,角标是噪声(见待拍板⑦) |
| 首页请求 | 根管理员约 26 个 GET | 22 个(非根 21 个) | 削掉审计磁贴(2 个)、`/admin/overview`、`/admin/reader-defaults`;开关读数改从已有请求带出 |

## 聚合与削减清单

**首页**
- 宏观「账户 186」与账户磁贴重复 → 账户磁贴主角改为「近 N 天活跃」(根)/「账户」(非根),账户数降为小数。
- 告警六连(备份未完成 / 播客失败 3 / 回填失败 1 / 预算 82% / 自定源失败 2 / 反馈 5 / 标签候选 12)在横带与磁贴各出现一次 → 只在状态磁贴出现;所属磁贴右上角一个状态点(▲ warn / ◆ bad,形状 + 色双通道,悬停 title 写名称)。
- 磁贴里的告警数换成正常指标:播客「失败」→ 已发布音频 + 状态构成;分析「未归并候选」→ 新闻价值 P50 + 直方图;反馈「反馈待处理」→ 反馈累计 + 生效公告;自定源「失败中」→ 读者数 / 篇数;存储「自动备份 未完成」章 → 最近成功时间。
- 不再进首页:操作审计磁贴「近 7 天操作 / 被拒」(审计并入账户页)、「账户已开 AI 158」(`/admin/overview`)、「默认订阅源 8」(`/admin/reader-defaults`)、宏观「阅读次数」与「游客转化」(游客访客降为账户磁贴的小数)。
- 「活跃 / 阅读」逐日走势(需聚合)不放首页主角位。

**读者功能页**
- AI 用量 KPI「总调用」与图表重复 → 并成图表头主角数字;「调用」「tokens」两张图 → 一张图 + 指标切换(`调用 | tokens`)+ 维度切换(`按用途 | 按用户`,按用户仅根)。
- 日预算格旁「今日已用 412,300 / 500,000」与预算环重复 → 只留环(在汇总列)。
- 用户 AI 总闸 / 公开分享 / 读者点播三条整宽横带 + 新账号默认开 + 模型 chip + 日预算 → 一个 3 列 × 2 行开关网格。
- 描述句收进 `title`:用户 AI「关闭立即暂停全员翻译 / 问答…」、新账号默认开「此后新建的账号…」、日预算「读者面 AI…0 = 不限…」、读者点播常态说明「读者可按需生成…(两者共用每日额度池)」、公开分享「读者可为单篇内容生成免登录只读链接…」、默认订阅「新读者账号登录时自动订阅的来源…」、早报卡 meta「0 = 关闭」「名额 0 = 关闭」→ 对应输入框 title;每日用量 zone-hint「悬停看当日明细;系列色恒随实体,不随排位」删除。读者点播的 blockers 警示属于条件性状态警示,保留。

**账户页**
- 「账户 186」KPI 与账户增长 zone-hint「186 个 · 近 N 天新增 8」重复 → 合成图表头主角数字 + 一行小字(管理员 / 停用可点即筛表)。
- zone-hint「现存账户按加入日回溯,已删除不计」→ 标题 title;KPI 注脚「登录过 ≥1 次」→ title;游客 zone-hint「近 N 天 · 不计入上方账户口径」→ 标题 title;游客开关旁的状态说明句 → 开关标签 title。
- 操作审计并入页底(`#/admin/accounts/audit`)。

**播客页**
- v1 KPI「全文分析完成 312 · 待全文 8」「待处理 / 失败 · 失败 3 · 待对账 1」与单集表 zone-hint「1282 期 · 待全文 8 · 处理中 2 · 待对账 1 · 失败 3」同一组数出现两次 → 合成主角区三条构成(阶段 / 判定 / TTS,同一份 `breakdown`),图例计数可点即筛下方单集表;表头 hint 删除。
- 精简音频 zone-hint「141 份 · 已发布 128 · 待发布 4 · 已下架 9 · 1.9 GB」与概览 KPI 重复 → 删除(表脚给总数)。
- ASR「放大的是本地费用保护上限,不代表供应商账号真实额度同步增加」→ 「保存配额」title;「来源:运行时配置」「来源:默认值 · 硬边界 12h」→ 字段标签 title;两行模型文字 → 两枚 `model-chip`;「已用 · 预占 · 剩余」文字 → 计量条 + 图例;TTS 回执两行文字 → 计量条 + 一行小字,「单次最长口播预留约」→ title。

**低保真页(实装时照做,样页未画细)**:媒体库 KPI 注脚「多为签名过期 / 防盗链」、X API 单价注脚「$0.005 / 条」、分析开关 `FLAG_META` 第二列说明、「旧尺子待重跑 · 每轮 16 篇慢滴」→ 一律进 title。

**拿不准、列为待拍板而未擅自删的**:见待拍板 ②③⑦。

## 首页 bento 排布

12 列 × 3 行,行高 `minmax(280px, 1.5fr) / minmax(228px, 1.25fr) / minmax(164px, 0.9fr)`,整块高度跟视口走(`max(696px, 100vh − 108px)`),所以 1920 下磁贴与图一起长高。

| 行 | 列 1–6 | 列 7–9 | 列 10–12 |
|---|---|---|---|
| 1(高) | **读者功能**(6 列) | **账户**(3 列) | **状态**(纵跨 1–2 行) |
| 2(中) | **阅读与榜单**(3)· **播客**(3) | **分析与标签**(3) | ↑ |
| 3(矮) | **存储与外部资源**(6) | **自定源**(3) | **反馈与公告**(3) |

理由:任务书建议「读者 ~5 / 账户 ~4 / 状态 3」,但那样首行缝落在 5|6、9|10,第二行三等分缝落在 3|4、6|7,上下错位。本稿让**所有竖缝只落在 3|4、6|7、9|10 三条线上**,三行左右边界逐行对齐,眼睛可以按列扫;代价是账户磁贴从 4 列收到 3 列(281px @1280),但它比第二行高一截,阶梯线仍放得下。重要度由「宽 × 高」体现:读者功能最大,账户与状态次之,第三行最矮。

磁贴通则:页名 + 小图标(+ 时间窗标注 / 状态点)→ 主角数字(首行 36px、其余 28px,黑体 `tabular-nums`;新闻价值 P50 用分数字体 `--font-score`)→ 一张图 → 底部板块名链接(≤ 4)。点页名或磁贴空白处进页首,点板块名直达板块,**点图进该图的完整版**(如读者功能磁贴的柱 → `#/admin/reader/usage`)。时间窗(7/14/30/90)只驱动标了窗口的数:读者功能、账户、分析与标签;其余是当前态或累计(标签里写明「累计」「本月」)。

## 功能页清单与板块映射(8 页)

| 页 id | 名称 | 板块(页内顺序;`id` 即深链末段) | 版式 | 来自现行 |
|---|---|---|---|---|
| `reader` | 读者功能 | 每日用量 `usage`(8)· 汇总 `kpi`(4)· 用量构成 `mix` · 开关与默认 `switches`(单格 `ai` `newdefault` `model` `budget` `ondemand` `share`)· 新账号默认订阅 `defaults`(4)· 早报与兴趣 `brief`(8) | 高保真 | AI 档全部 + 内容档的分享 / 点播 / 默认订阅 + `BriefInterestZone` |
| `accounts` | 账户 | 账户增长 `growth`(8)· 概览 `kpi`(4)· 游客 `guest`(7)· 活跃用户 Top `top`(5,根)· 账户管理 `table`(根)· 操作审计 `audit` | 高保真(根 / 非根) | 用户档 + `AccountGrowth` + `GuestZone` + `AdminAuditPanel` |
| `reading` | 阅读与榜单 | 各源热度 `heat`(8,纵跨 2)· 内容概览 `kpi`(4)· 榜单快照 `rank`(4)· 收藏 TOP `top` | 低保真 | 内容档 KPI / 两卡 + `RankingSnapshotPanel` |
| `podcast` | 播客 | 单集状态 `stages`(8)· 概览 `kpi`(4)· ASR 配额 `asr`(8,纵跨 2)· 处理参数 `params`(4)· TTS 回执缓存 `tts`(4)· 单集处理 `tasks` · 中文精简音频 `audio` | 高保真 | `PodcastZone` 全部 |
| `analysis` | 分析与标签 | 新闻价值分布 `dist`(8)· 分析指标 `metrics`(4)· 分析开关 `flags`(4)· 完整分析回填 `backfill`(8)· 目录版本 `catalog` · 标签总账 `ledger` | 低保真 | `AdminTaxonomyPanel` |
| `external` | 存储与外部资源 | 媒体热点图 `heatmap`(8,纵跨 2)· 存储与备份 `storage`(4)· 媒体库 `media`(4)· X API `xapi` | 低保真 | `StorageStatusPanel` + 媒体库 + `MediaHeatmap` + X API |
| `user-sources` | 自定源 | 自定源总闸 `switch`(4)· 概览 `kpi`(8)· 源表 `table` | 低保真 | `UserSourcesPanel` |
| `inbox` | 反馈与公告 | 反馈收件箱 `feedback`(8)· 公告管理 `notice`(4) | 低保真 | `FeedbackInboxPanel` + `AnnouncementsPanel` |

括号里是 12 列中占几列;不写的整行铺满。非根管理员:账户页只剩增长 + 账户分布 + 游客(整行)+ 审计(看不到 `/api/accounts` 行);读者功能页没有「按用户」维度,用量构成只剩按用途(两栏排)。

**旧深链兼容**(样页已实现,`replaceState` 改写、不进历史):`#/admin/reader-ai` → `#/admin/reader/usage`;`reader-ai/{switches,budget,kpi,usage}` → `reader/{switches,budget,kpi,usage}`;`#/admin/reader-defaults` → `#/admin/reader/switches`;`reader-defaults/{share,ondemand,defaults,brief}` → `reader/{share,ondemand,defaults,brief}`;`#/admin/audit[/log]` → `#/admin/accounts/audit`。

## 数据来源

`现有` = 某个现有接口的某字段直接给出;`需聚合` = 数据在库里,要新加或扩展聚合接口才拿得到;`需新增` = 后端目前没有这个数。spec「数据来源 显」时首页每个数字 / 图旁出小标,悬停看接口与字段。v1 已逐项核过的沿用,本稿新增的图逐项读了路由与服务代码。

| 位置 | 首页项 | 接口 → 字段 | 来源 |
|---|---|---|---|
| 读者功能 | 近 N 天 AI 调用 / tokens(主角) | `GET /api/admin/ai-usage?days=N` → `totals.calls` / `total_tokens`(含系统用途) | 现有 |
| 读者功能 | 读者用途占比 | 同上 → `by_purpose[]`,前端按 `accounts.READER_AI_BUDGET_PURPOSES` 求和 | 现有 |
| 读者功能 | 逐日「读者 / 系统」堆叠离散柱 | 同上 → `by_day_purpose[]`(只含有数据的格,前端补零;按用途白名单分两组) | 现有 |
| 读者功能 | 今日预算环 | `GET /api/admin/ai-beta/global` → `tokens_used_today / daily_token_budget`(只计读者用途;0 = 不限时不画) | 现有 |
| 账户 | 近 N 天活跃(根,主角) | `GET /api/admin/accounts?days=N&limit=1` → `summary.logged_in_window` | 现有 |
| 账户 | 账户(非根主角 / 根小数)、停用、管理员 | `GET /api/admin/account-growth` → `totals.accounts / disabled / admins` | 现有 |
| 账户 | 新增 + 阶梯线 | 同上 → `series[{day,new}]`(全量,前端按窗口切、前缀和成阶梯) | 现有 |
| 账户 | 游客访客 | `GET /api/admin/guest?days=N` → `stats.kpi.visitors` | 现有 |
| 阅读与榜单 | 累计阅读(主角) | `GET /api/admin/content` → `totals.reads`(累计,无窗口) | 现有 |
| 阅读与榜单 | 各源阅读 Top 横条(按高度显示 3–8 行) | 同上 → `sources[].read_count`(累计;整表返回,前端排序) | 现有 |
| 阅读与榜单 | 榜单快照章 | `GET /api/admin/rankings/status` → `snapshot.generated_at / status` | 现有 |
| 播客 | 已发布音频(主角)/ 待发布 / 已下架 | `GET /api/admin/podcast-artifacts/stats` → `published / ready / withdrawn` | 现有 |
| 播客 | 单集状态分段条 | `GET /api/admin/podcast-premium-tasks?page_size=1` → `breakdown.stage`(全量,不随筛选) | 现有 |
| 分析与标签 | 新闻价值 P50(主角)/ P90 / 篇数 | `GET /api/admin/analysis/metrics?days=N` → `article_analysis.score_p50 / score_p90`,篇数 = `score_histogram` 求和 | 现有 |
| 分析与标签 | 新闻价值分布直方图 | 同上 → `article_analysis.score_histogram`(1–10 整数档,窗口按入库日) | 现有 |
| 分析与标签 | 分析待处理 | 同上 → `article_analysis.status_counts.pending` | 现有 |
| 存储与外部资源 | 自动备份最近成功 / 完成 | `GET /api/admin/storage/status` → `backup.status / last_success_at` | 现有 |
| 存储与外部资源 | 媒体库占用 | `GET /api/admin/media/stats` → `disk_bytes` | 现有 |
| 存储与外部资源 | 媒体缓存覆盖热点格(近 20 周) | `GET /api/admin/media/heatmap?days=140` → `days[]`(现算不落表,扫窗口内正文,见口径提醒) | 现有 |
| 存储与外部资源 | X API 本月额度条 | `GET /api/x-api/quota` → `estimated_cost_usd / monthly_budget_usd / post_reads` | 现有 |
| 自定源 | 源数 / 读者 / 篇数 | `GET /api/admin/user-sources` → `kpi.source_count / covered_users / article_count`(整表返回) | 现有 |
| 反馈与公告 | 反馈累计 / 处理中 / 已解决 | `GET /api/admin/feedback?limit=1` → `counts.total / in_progress / resolved`(不随筛选) | 现有 |
| 反馈与公告 | 生效公告 | `GET /api/admin/announcements` → 数 `items[].is_active`(无计数字段,公告量小) | 现有 |
| 状态 · 待处理 | 备份未完成 | `storage/status` → `backup.status = failed` | 现有 |
| 状态 · 待处理 | 播客失败 N | `podcast-premium-tasks` → `breakdown.stage.failed` | 现有 |
| 状态 · 待处理 | 回填失败 N | `GET /api/admin/analysis/backfills` → 逐条扫 `items[].status ∈ failed / partial_failed`(分页,无汇总字段) | **需聚合** |
| 状态 · 待处理 | 读者 AI 预算 ≥ 80% | `ai-beta/global`(同上) | 现有 |
| 状态 · 待处理 | 自定源失败 N | `user-sources` → `kpi.failing_count` | 现有 |
| 状态 · 待处理 | 反馈待处理 N | `feedback` → `counts.open` | 现有 |
| 状态 · 待处理 | 标签候选 N | `GET /api/admin/taxonomy/state` → `unresolved_candidate_count` | 现有 |
| 状态 · 待处理 | X API ≥ 80% / ASR 冻结 / 播客存储保护(示意数据不触发) | `x-api/quota`;`GET /api/admin/podcast-asr-quota` → `usage_status`;`podcast-artifacts/stats` → `storage_pressure` | 现有 |
| 状态 · 开关 | 用户 AI / 读者点播 / 公开分享 | `ai-beta/global` → `enabled`;`GET /api/admin/reader-ondemand` → `enabled`;`GET /api/admin/public-share` → `enabled` | 现有 |
| 状态 · 开关 | 自定源 | `user-sources` → `config.enabled`(同一请求带出,不再单打 `/user-sources/config`) | 现有 |
| 状态 · 开关 | 游客访问 / 游客 AI | `guest` → `settings.enabled / ai_enabled` | 现有 |
| 状态 · 开关 | 文章分析 / 个人早报 | `analysis/metrics` → `feature_flags.article_analysis_enabled / personal_digest_enabled`(同一请求带出,不再单打 `/analysis/config`) | 现有 |

**功能页新增图表的来源**(都是现有):读者功能主图 = `ai-usage` 的 `by_day_purpose` / `by_day_user`(后者仅根,后端已裁成 Top 6 + 其它 + 游客哨兵);用量构成 = `by_purpose` / `by_user`;输入 / 输出 tokens = `totals.prompt_tokens / completion_tokens`。账户主图 = `account-growth.series`;活跃用户 Top = `/admin/accounts` 逐行窗口数。播客三条构成 = `podcast-premium-tasks` 的 `breakdown.stage / verdict / tts`;ASR 计量条 = `podcast-asr-quota` 的已用 / 预占 / 剩余;TTS 回执计量条 = 现行 `PodcastZone` 已读的回执缓存用量。

**需聚合**:回填失败汇总(首页唯一一项,沿用 v1)。可选但**未放**:站点级逐日活跃 / 阅读走势(`LoginEventRecord`、`ReaderReadRecord` 已有,缺站点级按日 group-by)。
**需新增**:无项放进首页。未放的:近 N 天播客处理量(无窗口、无逐日)、存储占用趋势(只有快照)、读者点播今日全局用量(只有逐用户日限额)。

**口径提醒(实装注意)**:①首页「AI 调用」含系统用途(日报、文章分析、事件归并…),所以读者功能磁贴把柱分成「读者用途 / 系统用途」两组,而「今日预算」只计读者用途,二者不是一个数;②`/admin/content` 的阅读是**累计**,磁贴标签写「累计阅读」;③`/admin/media/heatmap` 现算不落表(每次扫窗口内正文提图链),首页取 140 天仍是全量扫描,实装宜给首页单独缓存或缩到 8–12 周;④`podcast-premium-tasks` 每次装载全部单集状态再分页,首页只要 `breakdown`,量级上去后宜加轻量计数端点;⑤`/admin/overview` 不收参数、没有窗口,本稿不再用它。

**首页一次要打的请求**:根管理员 22 个 GET(`ai-usage`、`ai-beta/global`、`accounts`、`account-growth`、`guest`、`content`、`rankings/status`、`podcast-premium-tasks`、`podcast-artifacts/stats`、`podcast-asr-quota`、`analysis/metrics`、`analysis/backfills`、`taxonomy/state`、`storage/status`、`media/stats`、`media/heatmap`、`x-api/quota`、`user-sources`、`feedback`、`announcements`、`reader-ondemand`、`public-share`),非根少 `accounts`。这是待拍板④的依据。

## 图表规范与实装映射

| 样页图 | 规格 | 实装落点 |
|---|---|---|
| 逐日堆叠离散柱(读者功能磁贴 / 主图、游客走势) | 柱宽 ≤ 8px、段间 1px 表面缝、无数据日画 2px 刻度点;Y 轴 3 档(0 / 半 / 顶,`k`/`M` 缩写),X 轴首中尾 `MM-DD`;≥ 2 系列常备图例;色随实体(按指标分命名空间,同现行) | `DashboardCharts.MultiSeriesBars`(加「指标」切换与图表头主角数字) |
| 阶梯线(账户) | `stepAfter`,抬阶处空心点 + `+N` 直标(磁贴里只画点);十字准线吸附到日 | `admin/AccountGrowth` |
| 整数档直方图(分析) | 十档等宽,柱色复用分数分档(灰线 6.0 以下中性灰,其上按 85 / 60 / 30 / 0% 把 accent-ink 向灰混),P50 细线直标 | `AdminTaxonomyPanel.ScoreHistogram`(`.score-hist`)改色 + 加标线 |
| 热点格(存储) | 深浅 = 覆盖率四档 `--heat-1…4`,右上切角 = 当日有下载失败(`--state-warn`),两通道正交 | `admin/MediaHeatmap` 加「紧凑 / 近 N 周」变体 |
| 横条(阅读 Top、活跃 Top、用量构成、抽屉各源) | 单系列 `C_READ` / `C_PRIMARY` 直标数值;用量构成沿用主图的实体色,Top 6 之外为中性灰 | `RankBars` / `BarList` |
| 分段条(播客构成) | 磁贴细条 8px 圆头;页内 20px 块、4px 圆角、2px 缝;极小段按 3px 下限画,数值以图例 / 浮层为准 | 新件(或 `Kpi` 旁的小组件) |
| 预算环 / 计量条 | 轨 = 同色相淡一档,填充 = accent,≥ 80% 换 `--state-warn` | 新件 |

**统一浮层**(shadcn 口径):头 = 日期 `MM-DD 周X`(今天加「· 今天」,提示当天未过完)+ 右侧单位;行 = 短色线 + 系列名 + 右对齐等宽数值,**按图例固定顺序**、0 值转灰不隐藏;多系列加「合计」行;贴右缘 / 贴底时翻面,不越出视口。现行 `ThemedTooltip` 是「按值倒序、过滤 0」,本稿改了口径,见待拍板⑥。

## 采纳的参考(Codex 视觉调研稿,未入库)

- **Carbon 图表规范**:逐日计数一律离散柱、账户增长阶梯线,不改平滑面积图;坐标轴刻度少、日期 `MM-DD`、数值带单位;单系列直标、多系列简洁图例。v1 的折线 sparkline 全部换掉。
- **shadcn Charts**:统一图表头(图题 + 主角数字 + `mini-seg` 维度 / 指标切换)与统一浮层(见上)。
- **Langfuse**:读者功能页 AI 部分按「汇总 → 趋势 → 构成」:图表头主角数字 + 右侧汇总列 → 每日堆叠柱 → 用量构成(按用途 / 按用户)。
- **Cloudscape Service Dashboard**:首页不等大磁贴;观测(图、数)与配置(开关网格、旋钮)在功能页分区。
- **Supabase Studio**:账户抽屉打开时不重绘列表,筛选 / 排序 / 滚动位置原样保留;抽屉内按「摘要 → 明细 → 操作」分层,操作(含高危)收在抽屉底栏。
- **不采纳**:新依赖 / 组件库、可拖拽看板、健康历史条(`pivotDaily` 补零不等于「无故障」证据)。Impeccable 定点审阅留给实装阶段。

## spec 开关

| 开关 | 档位 | 作用 |
|---|---|---|
| 主题 | 亮 / 暗 | 挂 `<html data-theme>`,与左轨「主题」同步 |
| 视角 | 根管理员 / 普通管理员 | 普通管理员:账户磁贴主角换成账户数,板块链接去掉「账户表」;账户页、读者功能页按现行门控收敛 |
| 首页总闸 | 只读(默认)/ 可拨 | 只读 = 状态灯 + 名称 + 开/关,点了落到所在板块;可拨 = 行内 `ledger-switch`,状态全样页共享 |
| 待处理 | 有事 / 无事 | 无事:状态磁贴上半收成一行「无待处理」、开关改单列;磁贴状态点消失;同一份数据喂所有页 |
| 功能页 | 去卡片 / 保留卡片 | 去卡片 = 板块顶一条细线、卡片退成平面(旋钮卡保留细框);保留卡片 = 现行 `surface-card` |
| 跨页索引 | 关 / 开 | 开:搜功能结果含其它页签与设置柜分区;首页底部多一行入口(需滚动) |
| 数据来源 | 隐 / 显 | 首页每个数字 / 图旁出 `现有` / `需聚合` / `需新增` 小标 |

另:spec 栏右侧「对照第一版」打开 `v1.html`。

## 本稿新增类

`ops-*`(实装时进 `index.css`):
- 页头 / 浮层:`ops-search` `ops-search-icon` `ops-search-input` `ops-kbd` `ops-pop` `ops-search-pop` `ops-hit-opt` `ops-hit-name` `ops-hit-page` `ops-hit-enter` `ops-hit-empty` · `ops-crumb` `ops-crumb-root` `ops-crumb-sep` `ops-crumb-cur` `ops-crumb-pop` `ops-crumb-item` `ops-crumb-check`
- 首页:`ops-home` `ops-bento`(落位类 `t-reader` `t-accounts` `t-status` `t-reading` `t-podcast` `t-analysis` `t-external` `t-usrc` `t-inbox`)`ops-tile` `ops-tile-head` `ops-tile-icon` `ops-tile-name` `ops-tile-meta` `ops-tile-dot` `ops-mark` `ops-tile-body` `ops-tile-links` `ops-tile-li` `ops-tile-link` · `ops-hero` `ops-hero-lbl` `ops-hero-num` `ops-hero-sub` `ops-hero-row` `ops-minis` `ops-mini` `ops-mini-lbl` `ops-mini-num` `ops-ringbox` `ops-col` `ops-ext` `ops-kv` `ops-stamp-row` · `ops-status` `ops-status-sec` `ops-status-div` `ops-row-lbl` `ops-count` `ops-pend` `ops-pend-name` `ops-pend-num` `ops-pend-none` `ops-sw-grid` `ops-sw2` `ops-sw2-name` `ops-sw2-state` · `ops-xidx` `ops-xidx-item`
- 图表件:`ops-chart-box` `ops-chart` `ops-gl` `ops-hit` `ops-cur` `ops-xhair` `ops-xdot` `ops-mark-line` `ops-cell` `ops-legend` `ops-lg` `ops-lg-sub` `ops-hb` `ops-hb-row` `ops-hb-name` `ops-hb-track` `ops-hb-bar` `ops-hb-val` `ops-seg` `ops-seg-p` `ops-meter` `ops-meter-legend` `ops-ring` `ops-tip` `ops-tip-head` `ops-tip-unit` `ops-tip-row` `ops-tip-name` `ops-tip-val`(样页手绘 SVG,实装走 recharts / 现有组件,见上表)
- 功能页:`ops-page` `ops-grid` `ops-sec` `ops-sec-body` `ops-span-{4,5,6,7,8,12}` `ops-rows-2` `ops-flash` `ops-flat` · `ops-hero-card` `ops-ch-head` `ops-kcol` `ops-kpi-ring` · `ops-swgrid` `ops-swcell` `ops-swcell-end` · `ops-sub-grid` `ops-subcard` `ops-subhead` · `ops-chiplist` `ops-chiprow` `ops-chip-add` · `ops-comp` `ops-comp-row` `ops-comp-lbl` `ops-comp-main` `ops-stage-legend` `ops-asr-grid` · `ops-drawer-sec` `ops-drawer-foot` `ops-acct-foot` · `ops-lofi` `ops-lofi-stack`(低保真骨架,实装不要)

`spec-*`(样页 chrome,实装不要):`spec-bar` `spec-title` `spec-group` `spec-name` `spec-seg` `spec-btn` `spec-link` `spec-hide` `spec-pill` `spec-src`。

其余全部复用现行在册类(`page-head` `page-title` `mini-seg` `win-label` `form-input-inline` `surface-card` `card-pad` `card-head` `card-title` `card-foot` `kpi-strip` `kpi*` `zone-head` `zone-title` `zone-hint` `zone-acts` `ai-switchboard` `ai-light` `ai-switch-lbl` `ai-divider` `ledger-switch` `model-chip` `knob*` `knob-cards` `knob-grid` `knob-field` `sett-field*` `interest-chip-dot` `rdef-select` `stamp*` `acct-*` `cell-2` `score-pair` `rowact-btn` `rowacts` `table-foot` `pager*` `ledger-batchbar` `ledger-drawer*` `tiles` `tile*` `guest-*` `app-rail` `reader-vrail-*` `rail-*`);图标为 lucide-react 1.14.0 同形内联 SVG;分数数字为自托管 Dorami Serif 600 的「0-9 .」子集(约 2 KB)。

## 待拍板

1. **功能页 8 页**:读者 AI + 读者默认 → 读者功能、审计并回账户。是否再并「自定源」进「阅读与榜单」(7 页)?本稿不并:自定源有自己的总闸与整张源表。
2. **读者功能磁贴的柱只分「读者用途 / 系统用途」两组**(页内主图仍按用途 Top 6 + 其它)。好处是一眼看出读者占多少、和预算环同口径;代价是首页看不到具体用途。备选:磁贴也按用途 Top 3 + 其它。
3. **首页削掉的三个数**:「账户已开 AI」「默认订阅源数」「审计近 7 天 / 被拒」。各省一个请求;若要保留,建议放回对应磁贴的小数位而不是另起一行。
4. **首页聚合是否扩展后端**:以现有接口拼要 22 个请求,其中 `media/heatmap` 与 `podcast-premium-tasks` 是重查询,回填失败拿不到汇总。建议新开只读端点(如 `GET /api/admin/home?days=N`,按角色剥字段)一次给齐并缓存——会突破 issue 里「不改后端」的边界。不扩:首页去掉「回填失败」,热点格缩窗。
5. **首页总闸默认只读**(本稿默认),spec 可切「可拨」看效果。关闭「公开分享」「游客访问」会立即作废外链 / 踢掉在线游客,放首页可拨误触面大;倾向只读,或「可拨但关闭方向过确认」。
6. **浮层口径改为「图例固定顺序 + 0 值转灰 + 合计行」**(现行 `ThemedTooltip` 是按值倒序、过滤 0)。固定顺序便于逐日横向比对,是 shadcn / Carbon 口径;改它会影响全站运维图。
7. **组名退役**:磁贴按重要度排后组名角标不再有意义,面包屑弹层单列 8 项(与首页同序)。备选:弹层按「用户与读者 / 内容 / 治理」三组分列。
8. **新增字号档**:首行磁贴主角数字 36px、其余 28px(= `.kpi-num`)。36 是新刻度,实装要先进 conventions §3 的表。
9. **去卡片还是保留卡片**:沿用 v1 倾向——功能页去卡片(旋钮卡这类「一张卡一个保存」的保留细框),首页磁贴保留卡片(磁贴本身是可点对象)。
10. **跨页索引**:沿用 v1 倾向——只进搜索,不进首页磁贴行(开了以后首页要滚动才看得到)。

## 偏离任务书与已知局限

- **账户磁贴 3 列而非 ~4 列**,读者功能 6 列而非 ~5 列:为了让三行竖缝对齐(见「首页 bento 排布」)。
- **一屏实测超额完成**:任务书只要求 1280×800 下标题行可见,本稿在 1280×800 产品视口下九块磁贴全部完整可见(底 790);spec 栏展开时末行底 856,标题行底 729 仍在视口内。
- 播客主角区在任务书的「状态构成分段横条」之外**多画了判定、TTS 两条**(同一份 `breakdown`,不加请求),用来让主角区与右侧 KPI 列等高,并把三根列头筛选轴变成可点的图例。
- 低保真 5 页的骨架也按「主角区 + 并排块 + 宽表」排,但内容是静态灰块,不表示加载态。
- 用量构成的「按用户」在调用 / tokens 两个指标下各自做色彩记忆(与现行两张图的命名空间一致):两个指标的 Top 6 并集可能超过 6 个实体,共用一套会撞色;代价是切指标时同一实体颜色可能变。
- 热点格在 1280 宽时格子 7px(只有 7 行 × 20 周的空间),1440 起 9–10px。
- 单集抽屉、游客设置抽屉、新建账户弹窗、列头就地搜索仍只给 toast 示意;单集表的列头筛选接了阶段 / 判定 / TTS,其它表只接了账户表三列。

**顺带发现的现网小毛病**(实装顺手修):
- `admin/adminUtils.js` 的 `PURPOSE_LABELS` 漏了 `event_assignment`(issue #121 起已进 `VALID_PURPOSES`),现网 AI 用量图例会露出原始键;样页按「事件·归并」示意。
- `BriefInterestZone` 的三字段旋钮卡在窄列里会把「内容价值门槛」折成两行,同排输入框上下错位;样页压成单行省略 + title。
- `PodcastZone` 处理参数里 `<strong>` 在 600 的 `.knob` 内按 `bolder` 落成 900(v1 已报,仍在)。

## 构建与自检

源与脚本在 `tmp/issue-114/kit/`(已 gitignore;第一版的 `src/`、`build.py`、`check.py` 原样保留):`src-v2/{icons,data,registry,charts,app}.js` + `src-v2/ops.css` + `fonts-v2/score.css`;`build_v2.py` 拼单文件(现网样式取自与本 worktree 逐字节相同的 `index.css` / `mobile.css` 的 vite 构建产物,按本页实际用到的类名抽取),`check_v2.py --collect` 走遍状态收类名,`check_v2.py` 跑断言与截图(需带 Playwright 的解释器)。自检 15 项全过:一屏(1280 / 1440 / 1920 × spec 收起 / 展开)、无横向滚动(3 宽 × 7 状态 × 9 页)、字号 ≥ 12px、字重只 400 / 600、首页 54 个入口落位、面包屑横切 8 页 + 回首页 + 后退、搜功能 12 例 + 方向键 + 跨页索引、深链落位、旧深链 7 例、图表浮层 12 处不越出视口、控制台零报错。
