# issue #114 运维管理信息架构 · R2 第三版样页

> 方向(首页即目录 → 满幅功能页、面包屑横切)已获认可。第二版(`v2.html` + `README-v2.md`)的反馈是:首页杂乱、不突出重点、磁贴大小差距不够、每块磁贴左上角堆四行而顶部中右空旷、要更清爽一致、合理用动效、重要度 账户 > 阅读与榜单 > 其它、不要搜功能框。
> 第三版回应这七条:**固定栅格 + 三档磁贴解剖**(同档结构完全一样)、**数字与标签同行**、**每块磁贴只留一个主角 + 一张图,其余进 hover**、**搜功能框退役**、**动效只在反馈处**。第一版 / 第二版副本在同目录(`v1.html` / `v2.html`)。
> 本稿只是样页:单文件 `index.html`,离线可开;**所有数字为示意**(固定种子,量级取内网读者实例:约 190 账户、几十个源),互相自洽但不来自真实库。
> **v3.1 小改(2026-11-03 用户目检后,三处):** ① 所有磁贴左下角的板块链接行退役(S 磁贴 hover 的链接槽随之去掉;板块级深链仍由待处理项、图表点进与功能页承担);② 播客磁贴的阶段构成由分段条改为**环图**(中心标单集总数,hover 段高亮、其余段淡出 + 浮层);③ 分析与标签的 P50 不再用分数衬线体,与其它磁贴数字同字体。

## 怎么看

```bash
cd docs/design/issue-114-admin-ia && python3 -m http.server 8765
# 打开 http://127.0.0.1:8765/   (直接双击 index.html 用 file:// 打开也行;spec 栏「对照 v1」「对照 v2」开旧版)
```

- 顶部深色条是**样页 spec 栏**(不是产品界面),右端「收起」后右下角留一个「样页控制」胶囊。
- 路由与产品同形:`#/admin` 首页 · `#/admin/<页>` 功能页 · `#/admin/<页>/<板块>` 落到板块并短促 wash 高亮。浏览器后退可用;左轨「运维」与面包屑「运维管理」都回首页;面包屑 `▾` 横切 8 页。
- spec 状态写在查询串里,可直接转发:`?theme=dark`、`?view=admin`(普通管理员)、`?agg=0`(无聚合端点)、`?pending=0`(无事)、`?src=1`(数据来源标注)、`?motion=reduce`(应用内「减少动效」)、`?days=7`;旧深链 `#/admin/reader-ai` 等照旧落位。
- 自检截图在 `tmp/issue-114/shots-v3/`(不入库),报告 `tmp/issue-114/check-report-v3.json`。

## 本稿要回答的问题

1. 三档磁贴(L 两块 / M 三块 / S 三块 + 状态)是否「一眼可辨」且不再杂乱?(实测 1280×800:行 1 / 2 / 3 高 = 370 / 234 / 56,行 1 : 行 3 = 6.6;1440×900 = 431 / 273 / 56 = 7.7;1920×1080 = 541 / 343 / 56 = 9.7;三个视口首页都不滚动。)
2. 把次级数字摊成一行、说明进 hover 后,首页还够不够「一眼看全局」?(默认态每块磁贴只有 1 个主角 + 1 张图;L 多 3 个次级数;告警数字只在状态磁贴出现。)
3. ~~S 磁贴的板块链接只在 hover 出现~~(v3.1 已随板块链接行一并退役,见页首小改。)
4. 账户磁贴的主角图(逐日活跃账户)依赖聚合端点;不扩后端时退回账户增长阶梯线——两种形态哪种值得?(spec「聚合端点 有 / 无」,见待拍板②。)

## v2 → v3 改了什么(逐条对照反馈)

| # | 反馈 | v3 落实 |
|---|---|---|
| 1 | 元素太多、杂乱、不突出重点;削不重要的数据与引导语 | 首页从 9 块各有「标题 + 标签 + 大数 + 小数 + 图例 + 图 + 链接」收成三档固定解剖:L = 1 主角 + 3 次级 + 1 图 + ≤ 4 链接;M = 1 主角 + 1 图 + ≤ 3 链接;S = 1 数字。削掉的全部进 hover(见下表);首页**零句子**,标签只有名词;图例全部退役(名称在浮层里);预算环、热点格、推文数、X API 条、抓取间隔、生效公告、已解决、tokens、读者占比、P90、篇数、待发布 / 已下架、「占账户 %」「日均」全部离开默认态 |
| 2 | 大小区分度:大的更大、小的更小 | 固定栅格:行 1 高 ≈ 300 → 1280 下实测 370、1440 下 431;行 3 固定 56px 单行。L 主角 36px / M 28px / S 20px;S 磁贴没有图、没有链接行,整块一行 |
| 3 | 左上角堆四行、顶部中右空旷 | 数字与标签同行基线对齐(「80 活跃账户」),不再「标签在上、数字在中、注脚在下」;L 的 1 主角 + 3 次级**横排等距铺满整行**(`justify-content: space-between`),右上角放时间窗 chip / 状态点;M 的主角数字居左、小图居右同一行;S 的数字居右 |
| 4 | 更清爽、同类元素一致 | 同档磁贴 DOM 完全一样(`tileHead` → `ops-kpis` / `ops-mrow` / `ops-srt` → `tileLinks`);所有磁贴页头同一行高、同一位置放 chip;次级数字统一 18px、标签统一 12px faint;状态磁贴的页头也是同一件 |
| 5 | 合理运用动效、部分信息 hover 再显示 | 见「动效清单」:磁贴 hover 轻抬、数字 hover 浮层、环图段 hover 高亮、首页图表首帧生长、切时间窗数字滚动、待处理行 › 右移、开关点名称浮出;全部挂 `data-motion`,`reduce` 时只剩不透明度变化 |
| 6 | 账户 > 阅读与榜单 > 其它;反馈 / 自定源最小;读者功能降级 | 行 1 = 账户 L(7 列)+ 阅读与榜单 L(5 列);行 2 = 读者功能 / 播客 / 分析与标签 M(各 3 列)+ 状态(3 列,纵跨 2–3);行 3 = 存储与外部资源 / 自定源 / 反馈与公告 S(各 3 列,56px)。面包屑弹层与 `PAGES` 顺序同步改为这个重要度 |
| 7 | 不要搜功能框 | 所有页头去掉搜功能框(含 ⌘K / `/` 快捷键、同义词索引、跨页索引);面包屑 `▾` 是唯一横切入口;spec 栏「跨页索引」开关随之删除 |

## 三档解剖(同档完全一致)

| 档 | 磁贴 | ① 页头 | ② 数字 | ③ 图 | ④ 链接 |
|---|---|---|---|---|---|
| **L** | 账户、阅读与榜单 | 图标 + 页名居左;右侧「近 N 天」chip(非根的阅读与榜单是「累计」)+ 状态点 | 1 主角(36px)+ ≤ 3 次级(18px)横排等距铺满整行 | 全宽,随行高拉伸(1280 下约 230px) | 无(v3.1 退役) |
| **M** | 读者功能、播客、分析与标签 | 同上(播客无时间窗,只有状态点) | 1 主角(28px)居左 | 小图居右同一行(两色迷你柱 / 环图 / 直方图,无坐标轴文字) | 无(v3.1 退役) |
| **S** | 存储与外部资源、自定源、反馈与公告 | 图标 + 页名居左(1280 下内容盒只有 246px,容器查询收起图标;≥ 1440 照常);存储多一个备份状态点 | 1 主角(20px)居右 | 无 | 无(v3.1:hover 链接槽退役,数字常显) |
| **状态** | — | 「状态」+ 右侧待处理小计数 | 待处理列表(严重度排序:◆ bad 在前、▲ warn 在后;名称 + 数字 + ›);无事时一行「无待处理」 | — | 底部 8 个开关点:只画点,名称 hover 浮出;关着的常显名称(琥珀点即「关」);只读,点击跳所在板块 |

数字与标签的规则:标签小字放数字**右侧基线对齐**,不放上面;标签只允许名词(「活跃账户」「新增」「源」「待处理」),「占 …」「日均 …」之类带解释的短语只在 hover 浮层出现。

## 保留 / hover / 删(决定性清单)

| 磁贴 | 保留(可见) | 削到 hover | 直接删 |
|---|---|---|---|
| 账户 L | 主角:近 N 天活跃账户(根)/ 账户总数(非根);次级:账户 186 · 新增 +12 · 游客访客 155(非根:新增 · 读者 · 游客访客);图:逐日活跃账户离散柱(`需聚合`;spec「聚合端点 无」时退回账户增长阶梯线 `现有`);链接:账户增长 · 游客 · 账户表 · 操作审计 | 主角数字 hover:占账户 43% · 登录次数 778 · 阅读次数 4,723(非根:管理员 4 · 停用 3) | 登录 / 阅读次数不再作次级(这块只讲人) |
| 阅读与榜单 L | 主角:近 N 天阅读次数(根;非根用累计阅读);次级:收藏 3,127 · 订阅 2,960 · 榜单 `07:00` chip;图:各源阅读 Top 5 横条(根 + 聚合端点「有」= 窗口内 `需聚合`;非根或「无」= 累计 `现有`,与主角同窗);链接:各源热度 · 榜单快照 · 收藏 TOP | 主角 hover:日均 157.4 · 累计阅读 52,340(非根:归档文章 · 内容源) | — |
| 读者功能 M | 主角:近 N 天 AI 调用;图:逐日两色迷你柱(读者 / 系统,hover 看当日两组数) | 主角 hover:tokens 27.3M · 读者用途 24% · 今日预算 412.3k / 500.0k · 82%(≥ 80% 转 warn 色) | 预算环(进功能页)、图例、「读者用途占 24%」注脚 |
| 播客 M | 主角:已发布音频 128;图:阶段构成**环图**(v3.1 由分段条改,中心标播客单集 1,282),**不画图例**,hover 段高亮并显示阶段名 / 数量 / 占比(进行中还给四个子阶段) | 主角 hover:待发布 4 · 已下架 9 · 播客单集 1,282 | 图例行、页头「1,282 期」chip(v3 自查时删:M 只许 1 个数字) |
| 分析与标签 M | 主角:新闻价值 P50 6.2(v3.1 起与其它磁贴数字同字体,不再用分数衬线体);图:分布直方图(保留 P50 标线;宽 ≥ 150px 时标「P50」字) | 主角 hover:P90 8.1 · 篇数 3,824 · 分析待处理 142 | 「分析待处理」小数位、P90 注脚 |
| 存储与外部 S | 媒体库 `2.4 GB`(单位小字)+ 备份状态点(绿点 = 成功 / 红菱 = 未完成,`title` 带时间) | 页名 hover:备份最近成功 11-02 03:10 · X API 2026-11 $3.85 / $50 · 8% | 热点格、推文数、X API 计量条(进功能页);备份 chip 文字(3 列 S 磁贴 1920 以下放不下「备份未完成」+ 数字,改成状态点,见偏离) |
| 自定源 S | 源数 `37 源` | 页名 hover:读者 29 · 篇数 4,120 · 抓取间隔 60 分 | 「29 位读者 · 4,120 篇」注脚、抓取间隔小数 |
| 反馈与公告 S | 待处理反馈数 `5 待处理`(> 0 时 warn 色;与状态磁贴同源也无妨) | 页名 hover:已解决 39 · 生效公告 1 | 反馈累计、处理中 |
| 状态 | 待处理列表 + 8 个开关点 | 开关名称(hover 浮出);关着的常显 | 「待处理 7」行标改成页头右侧小计数;「开关」行标 |

S 磁贴不另画待处理点:告警只在状态磁贴出现一次;存储的备份状态点、反馈的 warn 色数字就是这块自己的状态。

## 动效清单(全部挂 `data-motion`;`reduce` = 只保留不透明度变化)

| 处 | 动效 | reduce 时 |
|---|---|---|
| 磁贴 hover | 阴影 `--sh-2` → `--sh-3`、描边 → `--dorami-border-strong`、上移 1px,150ms | 不上移(transform none,实测) |
| 环图段 hover(播客 M) | 被指段不变、其余段 opacity → 0.4,`--motion-fast`;首帧各段按 `--i` 错峰淡入(只有不透明度) | 不淡入,hover 照常 |
| 数字 / S 页名 hover | 浮层(与图表浮层同一件 `#ops-tip`)锚在元素下方、左缘对齐,贴底翻到上方 | 照常 |
| 首页图表首帧 | 柱自底生长、横条 / 分段自左生长,300ms,错峰 ≤ 150ms(`--i` 按序写入);只在首页,功能页不做入场编排 | 不生长(animation none,实测) |
| 切时间窗 | 主角 / 次级数字从旧值滚到新值 ≈ 400ms(三次缓出,`tabular-nums` 不抖;实测 80 → 57 → 40);柱按新窗口重排后再生长一次 | 直落新值 |
| 待处理行 hover | 行底色 `--dorami-hover` + `›` 右移 2px | 不右移(实测) |
| 开关点 hover | 名称从点上方浮出(ink 底白字小标签,120ms,带 3px 上浮) | 只淡入 |
| 克制 | 不做循环动画、视差、切页入场;`ops-flash` 深链落位高亮沿用 v1 | — |

与 `docs/frontend/conventions.md` §7「工作区动效 = feedback-only、不要入场编排」的张力:首页图表**首帧生长**是入场编排,本稿按用户反馈 5 做了,但只限首页、只 300ms、不循环;要不要登记为具名例外见待拍板③。

## 参考(只借形态,不照搬)

- **Stripe Dashboard 首页**:借「KPI 横排 + 一张主图」的克制——L 磁贴就是这个形态;没借它的渐变 / 彩色图标。
- **Vercel Analytics**:借「单行页头、数字与标签同行」——所有磁贴的 `ops-kpi`;没借它的黑底与 sparkline。
- **Linear Insights**:借「三档卡片一致解剖」——同档 DOM 完全一样;没借它的可拖拽与自定义视图。
- **Plausible**:借「大数字一行 + 一张图、零说明」——M 磁贴;没借它的分段切换与地图。
- Codex 调研里 **Cloudscape**(不等大磁贴 / 观测与配置分区)、**shadcn Charts**(统一浮层)、**Carbon**(离散柱 / 阶梯线 / 直方图口径)沿用 v2;**Supabase**(抽屉不重绘列表)沿用。
- 不采纳:新依赖 / 组件库、可拖拽看板、健康历史条、循环动画。

## 首页栅格与一屏实测

12 列 × 3 行,16px 缝;`grid-template-rows: minmax(300px, 1.58fr) minmax(190px, 1fr) 56px`,整块高度 `max(578px, 100vh − 108px)`:行 3 固定一行,行 1 / 行 2 按 1.58 : 1 分余量,所以 1920 下行 1 / 行 2 按比例放高、不留大片空白。

| 行 | 列 1–7 | 列 8–12 |
|---|---|---|
| 1(L) | **账户**(7) | **阅读与榜单**(5) |
| 2(M) | **读者功能**(3)· **播客**(3)· **分析与标签**(3) | **状态**(3,纵跨 2–3) |
| 3(S) | **存储与外部资源**(3)· **自定源**(3)· **反馈与公告**(3) | ↑ |

| 视口 | 全部磁贴底 y | 行 1 / 2 / 3 高 | 行 1 : 行 3 |
|---|---|---|---|
| 1280×800(产品视口) | 786 / 800 | 370 / 234 / 56 | 6.6 |
| 1280×800(spec 栏展开) | 786 / 800 | 345 / 219 / 56 | 6.2 |
| 1440×900 | 886 / 900 | 431 / 273 / 56 | 7.7 |
| 1920×1080 | 1066 / 1080(占 99%) | 541 / 343 / 56 | 9.7 |

任务书写「L 高 ≈ 5 × S」是 1440 基准下行 1 ≈ 300 的算法;本稿让行 1 跟视口走,所以比值更大(更「一眼可辨」),1280 下也不滚动。

## 功能页清单与板块映射(8 页,沿用 v2;v3 只改账户页主角区与页头)

| 页 id | 名称 | 板块(页内顺序;`id` 即深链末段) | 版式 |
|---|---|---|---|
| `accounts` | 账户 | 账户增长 `growth`(12;内含横排 KPI 行 `kpi` = 账户 186 · 活跃账户 80 · 登录次数 · 阅读次数 · AI 调用 + 右侧「管理员 4 · 停用 3」可点即筛表;主图阶梯线)· 游客 `guest`(7)· 活跃用户 Top `top`(5,根)· 账户管理 `table`(根)· 操作审计 `audit` | 高保真(根 / 非根) |
| `reading` | 阅读与榜单 | 各源热度 `heat` · 内容概览 `kpi` · 榜单快照 `rank` · 收藏 TOP `top` | 低保真 |
| `reader` | 读者功能 | 每日用量 `usage`(8)· 汇总 `kpi`(4)· 用量构成 `mix` · 开关与默认 `switches`(单格 `ai` `newdefault` `model` `budget` `ondemand` `share`)· 默认订阅 `defaults`(4)· 早报与兴趣 `brief`(8) | 高保真(v3 只去搜索框、删「近 N 天 · 按调用」hint 与「日均」注脚) |
| `podcast` | 播客 | 单集状态 `stages`(8)· 概览 `kpi`(4)· ASR 配额 `asr`(8,纵跨 2)· 处理参数 `params`(4)· TTS 回执缓存 `tts`(4)· 单集处理 `tasks` · 中文精简音频 `audio` | 高保真(v3 只去搜索框) |
| `analysis` | 分析与标签 | 新闻价值分布 `dist` · 分析指标 `metrics` · 分析开关 `flags` · 完整分析回填 `backfill` · 目录版本 `catalog` · 标签总账 `ledger` | 低保真 |
| `external` | 存储与外部资源 | 媒体热点图 `heatmap` · 存储与备份 `storage` · 媒体库 `media` · X API `xapi` | 低保真 |
| `user-sources` | 自定源 | 自定源总闸 `switch` · 概览 `kpi` · 源表 `table` | 低保真 |
| `inbox` | 反馈与公告 | 反馈收件箱 `feedback` · 公告管理 `notice` | 低保真 |

账户页主角区按首页 L 磁贴同构:KPI **横排在主图上方**(不再竖排在右侧),`#/admin/accounts/kpi` 深链落到这一行;游客(7)与活跃用户 Top(5)并排;账户表、审计表铺满。非根:KPI 行 = 账户 · 新增 · 管理员 · 读者 · 停用,游客整行,无 Top / 账户表。功能页定为**去卡片**(首页磁贴保留卡),spec 不再提供切换。

**旧深链兼容**(沿用 v2):`#/admin/reader-ai[/x]` → `#/admin/reader/…`;`#/admin/reader-defaults[/x]` → `#/admin/reader/…`;`#/admin/audit[/log]` → `#/admin/accounts/audit`。

## 数据来源

`现有` = 某个现有接口的某字段直接给出;`需聚合` = 数据在库里,要新加或扩展聚合接口才拿得到;`需新增` = 后端目前没有这个数。spec「数据来源 显」时首页每个数字 / 图旁出小标(hover 浮层里的项也标),悬停看接口与字段。v2 已逐项核过的沿用;本稿新增 / 变动的项逐项读了 `src/api/routers/admin.py` 与 `src/models/db.py`。

| 位置 | 首页项 | 接口 → 字段 | 来源 |
|---|---|---|---|
| 账户 | 近 N 天活跃账户(根,主角) | `GET /api/admin/accounts?days=N&limit=1` → `summary.logged_in_window` | 现有 |
| 账户 | 账户 / 读者 / 管理员 / 停用 | `GET /api/admin/account-growth` → `totals.accounts / admins / disabled` | 现有 |
| 账户 | 新增 + 阶梯线(聚合端点「无」时的主角图) | 同上 → `series[{day,new}]` | 现有 |
| 账户 | 游客访客 | `GET /api/admin/guest?days=N` → `stats.kpi.visitors` | 现有 |
| 账户 | hover:登录次数 / 阅读次数 | `accounts` → `summary.logins / summary.reads`(窗口内全部账户之和;服务层已排除游客) | 现有 |
| 账户 | **逐日活跃账户离散柱**(主角图) | `login_events` 按日 `COUNT(DISTINCT username)`——数据在库里,现无接口给站点级逐日序列 | **需聚合** |
| 阅读与榜单 | 近 N 天阅读次数(根,主角) | `accounts` → `summary.reads`(同一请求) | 现有 |
| 阅读与榜单 | 累计阅读(非根主角 / 根 hover)、收藏、订阅、归档文章、内容源 | `GET /api/admin/content` → `totals.reads / favorites / subscriptions / articles / sources` | 现有 |
| 阅读与榜单 | 各源阅读 Top 5(聚合端点「无」) | 同上 → `sources[].read_count`(累计;整表返回,前端排序取 5) | 现有 |
| 阅读与榜单 | **各源阅读 Top 5(窗口内,聚合端点「有」)** | `reader_reads` 按 `source_id` 在窗口内 `SUM(reads)`——现接口只有累计 | **需聚合** |
| 阅读与榜单 | 榜单 `07:00` chip | `GET /api/admin/rankings/status` → `snapshot.generated_at / status` | 现有 |
| 读者功能 | 近 N 天 AI 调用(主角)/ hover tokens | `GET /api/admin/ai-usage?days=N` → `totals.calls / total_tokens`(含系统用途) | 现有 |
| 读者功能 | hover 读者用途占比 | 同上 → `by_purpose[]`,前端按 `READER_AI_BUDGET_PURPOSES` 求和 | 现有 |
| 读者功能 | 逐日两色迷你柱 | 同上 → `by_day_purpose[]`(前端补零;按用途白名单分读者 / 系统两组) | 现有 |
| 读者功能 | hover 今日预算 | `GET /api/admin/ai-beta/global` → `tokens_used_today / daily_token_budget` | 现有 |
| 播客 | 已发布音频(主角)/ hover 待发布 / 已下架 | `GET /api/admin/podcast-artifacts/stats` → `published / ready / withdrawn` | 现有 |
| 播客 | 阶段分段条 / hover 播客单集 | `GET /api/admin/podcast-premium-tasks?page_size=1` → `breakdown.stage` / `total` | 现有 |
| 分析与标签 | 新闻价值 P50(主角)/ hover P90 / 篇数 | `GET /api/admin/analysis/metrics?days=N` → `article_analysis.score_p50 / score_p90 / score_histogram`(篇数 = 直方图求和) | 现有 |
| 分析与标签 | 直方图 | 同上 → `score_histogram`(1–10 整数档) | 现有 |
| 分析与标签 | hover 分析待处理 | 同上 → `status_counts.pending` | 现有 |
| 存储与外部 | 媒体库 `2.4 GB` | `GET /api/admin/media/stats` → `disk_bytes` | 现有 |
| 存储与外部 | 备份状态点 / hover 最近成功 | `GET /api/admin/storage/status` → `backup.status / last_success_at` | 现有 |
| 存储与外部 | hover X API 额度 | `GET /api/x-api/quota` → `estimated_cost_usd / monthly_budget_usd` | 现有 |
| 自定源 | 源数 / hover 读者 / 篇数 / 抓取间隔 | `GET /api/admin/user-sources` → `kpi.source_count / covered_users / article_count`、`config.refresh_minutes` | 现有 |
| 反馈与公告 | 待处理(主角)/ hover 已解决 | `GET /api/admin/feedback?limit=1` → `counts.open / resolved` | 现有 |
| 反馈与公告 | hover 生效公告 | `GET /api/admin/announcements` → 数 `items[].is_active` | 现有 |
| 状态 · 待处理 | 备份未完成 / 播客失败 / 读者 AI 预算 / 自定源失败 / 反馈待处理 / 标签候选 / X API / ASR 冻结 / 播客存储保护 | 同 v2(`storage/status`、`podcast-premium-tasks`、`ai-beta/global`、`user-sources`、`feedback`、`taxonomy/state`、`x-api/quota`、`podcast-asr-quota`、`podcast-artifacts/stats`) | 现有 |
| 状态 · 待处理 | 回填失败 N(聚合端点「无」时隐藏) | `GET /api/admin/analysis/backfills` → 逐条扫 `items[].status`(分页,无汇总字段) | **需聚合** |
| 状态 · 开关 | 8 个开关点 | 同 v2(`ai-beta/global`、`reader-ondemand`、`public-share`、`user-sources`、`guest`、`analysis/metrics.feature_flags`) | 现有 |

**需聚合**(3 项,都随 spec「聚合端点」切换):逐日活跃账户柱、窗口内各源 Top 5、回填失败汇总。「无」时账户图退回阶梯线、Top 5 退回累计、回填失败行隐藏——都是 `现有`。
**需新增**:无项放进首页。
**不再请求**:`media/heatmap`(热点格离开首页)。首页一次要打的请求:根管理员 21 个 GET(v2 22 个),非根 20 个;要是走待拍板②的聚合端点,一次给齐。

口径提醒沿用 v2(AI 调用含系统用途而预算只计读者用途;`/admin/content` 阅读为累计;`podcast-premium-tasks` 每次装载全量)。新增一条:**阅读与榜单磁贴在「聚合端点 无」时主角(窗口)与图(累计)窗口不一致**,图的浮层写「累计阅读」以示区别;有聚合端点时两者同窗。

## 图表规范与实装映射

| 样页图 | 规格 | 实装落点 |
|---|---|---|
| 逐日离散柱(账户 L 全宽、读者功能 M 迷你) | 柱宽 ≤ 8px、单系列直标、Y 轴 3 档(niceCeil 加 3 / 4 档,27 → 30 不再 → 50)、X 轴首中尾 `MM-DD`;**mini** = 无坐标轴文字只留基线,柱宽随槽位自适应(90 天 ≈ 1px) | `DashboardCharts.MultiSeriesBars`(加 mini 变体) |
| 阶梯线(账户页主图 / 聚合端点「无」时的磁贴图) | `stepAfter`,抬阶处空心点 + `+N` 直标 | `admin/AccountGrowth` |
| 横条 Top 5(阅读与榜单 L) | 单系列直标;`is-fill` 变体按容器高 `space-evenly` 铺开,条 8px | `RankBars` |
| 分段条(播客功能页) | 10px 圆头、段间 2px 缝、极小段 3px 下限;**无图例**,hover 段 = 名称 / 数量 / 占比(+ 子阶段) | 新件 |
| 环图(播客 M,v3.1) | 外径 100 / 环宽 14 的 SVG `<circle>` dasharray 分段,段间 2.2° 缝、极小段 2.4° 下限(数值以浮层为准),中心标总数;hover 段其余段淡到 40% | 新件 |
| 直方图(分析 M) | 十档等宽、柱色复用分数分档、P50 细线;mini = 无档号,宽 ≥ 150px 才标「P50」 | `AdminTaxonomyPanel.ScoreHistogram` |
| 浮层 | 图元贴指针、出界翻面;数字 / S 页名锚在元素下方左对齐,贴底翻上;行 = 短色线 + 名 + 右对齐等宽数,0 值转灰,warn 行数值换 `--state-warn`;spec「数据来源 显」时行尾出小标 | `ThemedTooltip`(口径见 v2 待拍板⑥,仍待定) |

## spec 开关

| 开关 | 档位 | 作用 |
|---|---|---|
| 主题 | 亮 / 暗 | 挂 `<html data-theme>`,与左轨「主题」同步 |
| 视角 | 根管理员 / 普通管理员 | 非根:账户主角换账户总数、阅读与榜单主角换累计阅读、次级与 hover 按现行门控收敛;账户页无 Top / 账户表 |
| 聚合端点 | 有(默认)/ 无 | 「无」= 账户图退回阶梯线、各源 Top 5 退回累计、状态磁贴的「回填失败」隐藏——用户看一眼就知道待拍板②买到什么 |
| 待处理 | 有事 / 无事 | 无事:状态磁贴收成一行「无待处理」、开关点仍在底部;磁贴状态点消失、反馈 S 数字归 0 |
| 数据来源 | 隐 / 显 | 首页每个数字 / 图 / hover 行旁出 `现有` / `需聚合` 小标 |
| (查询串)`motion=reduce` | — | 应用内「减少动效」:生长 / 上移 / 右移全部归零,只剩不透明度 |

另:「对照 v1」「对照 v2」打开旧版副本。删掉的 v2 开关:首页总闸 可拨 / 只读(定为只读,见待拍板④)、功能页 去卡片 / 保留卡片(定为去卡片 + 首页磁贴有卡)、跨页索引(随搜索框退役)。

## 本稿新增类

`ops-*`(实装时进 `index.css`;v3 新增或改义的加粗):
- 页头:`ops-pop` `ops-crumb` `ops-crumb-root` `ops-crumb-sep` `ops-crumb-cur` `ops-crumb-pop` `ops-crumb-item` `ops-crumb-check`(搜功能的 `ops-search*` `ops-kbd` `ops-hit*` `ops-xidx*` 已删)
- 首页:`ops-home` `ops-bento`(落位类 `t-accounts` `t-reading` `t-reader` `t-podcast` `t-analysis` `t-status` `t-external` `t-usrc` `t-inbox`)`ops-tile`(档位 **`is-l` `is-m` `is-s`**)`ops-tile-head` `ops-tile-icon` `ops-tile-name` `ops-tile-meta` **`ops-win`** **`ops-count`** `ops-mark` `ops-tile-dot` · **`ops-kpi` `ops-kpi-num` `ops-kpi-lbl` `ops-kpis` `ops-kpi-acts`**(数字 + 标签同行;替代 v2 的 `ops-hero-*` / `ops-mini*` / `ops-ringbox` / `ops-ext` / `ops-kv`)· **`ops-tile-chart`**(`is-seg`)**`ops-mrow`** · ~~`ops-tile-links` `ops-tile-li` `ops-tile-link` `ops-srt-links`~~(v3.1 退役)· **`ops-srt` `ops-srt-num`** · **`ops-donut` `ops-donut-p` `ops-donut-num` `ops-donut-unit`**(v3.1 环图)· `ops-status` **`ops-pendlist`** `ops-pend` `ops-pend-name` `ops-pend-num` `ops-pend-none` **`ops-dots` `ops-dot` `ops-dot-name`**(替代 `ops-sw-grid` / `ops-sw2*` / `ops-row-lbl` / `ops-status-sec` / `ops-status-div`)
- 图表件:`ops-chart-box` `ops-chart` **`ops-bar`**(生长动画挂点)`ops-gl` `ops-hit` `ops-cur` `ops-xhair` `ops-xdot` `ops-mark-line` `ops-cell` `ops-legend` `ops-lg` `ops-lg-sub` `ops-hb`(**`is-fill`**)`ops-hb-row` `ops-hb-name` `ops-hb-track` `ops-hb-bar` `ops-hb-val` `ops-seg` `ops-seg-p` `ops-meter` `ops-meter-legend` `ops-ring` `ops-tip` `ops-tip-head` `ops-tip-unit` `ops-tip-row`(**`is-warn`**)`ops-tip-name` `ops-tip-val`
- 功能页(沿用 v2):`ops-page` `ops-grid` `ops-sec` `ops-sec-body` `ops-span-{4,5,6,7,8,12}` `ops-rows-2` `ops-flash` `ops-flat` · `ops-hero` `ops-hero-lbl` `ops-hero-num` `ops-hero-sub` `ops-hero-card` `ops-ch-head` `ops-kcol` `ops-kpi-ring`(读者功能 / 播客页主角区仍用 v2 的「标签在上」形态,账户页改 `ops-kpis is-page`)· `ops-swgrid` `ops-swcell` `ops-swcell-end` · `ops-sub-grid` `ops-subcard` `ops-subhead` · `ops-chiplist` `ops-chiprow` `ops-chip-add` · `ops-comp` `ops-comp-row` `ops-comp-lbl` `ops-comp-main` `ops-stage-legend` `ops-asr-grid` `ops-stamp-row` · `ops-drawer-sec` `ops-drawer-foot` `ops-acct-foot` · `ops-lofi` `ops-lofi-stack`

`spec-*`(样页 chrome,实装不要):`spec-bar` `spec-title` `spec-group` `spec-name` `spec-seg` `spec-btn` `spec-link` `spec-hide` `spec-pill` `spec-src`。

其余全部复用现行在册类(`page-head` `page-title` `mini-seg` `win-label` `surface-card` `card-pad` `card-head` `card-title` `card-foot` `kpi-strip` `kpi*` `zone-head` `zone-title` `zone-hint` `zone-acts` `ai-switchboard` `ai-light` `ai-switch-lbl` `ai-divider` `ledger-switch` `model-chip` `knob*` `sett-field*` `interest-chip-dot` `rdef-select` `stamp*` `acct-*` `cell-2` `score-pair` `rowact-btn` `rowacts` `table-foot` `pager*` `ledger-batchbar` `ledger-drawer*` `tiles` `tile*` `guest-*` `app-rail` `reader-vrail-*` `rail-*`);图标为 lucide-react 1.14.0 同形内联 SVG;分数数字为自托管 Dorami Serif 600 的「0-9 .」子集。新用到的 CSS 能力:`container-type: inline-size` + `@container`(S 磁贴窄时收图标)、`transform-box: fill-box`(SVG 柱自底生长)——与现有 `:has()` / `color-mix()` 同一代。

## 待拍板

1. ~~S 磁贴的板块链接只在 hover 出现~~ **已定(2026-11-03 用户目检):首页所有磁贴的板块链接行退役**,S 磁贴 hover 链接槽一并去掉;板块级深链仍由待处理项、图表点进与功能页承担。
2. **账户主角图依赖聚合端点**:逐日活跃账户(`login_events` 按日去重)、窗口内各源 Top 5、回填失败汇总三项都要新开只读端点(建议 `GET /api/admin/home?days=N`,按角色剥字段并缓存),突破 issue 里「不改后端」的边界。不扩:账户图退回阶梯线(spec「聚合端点 无」即这个形态,截图 `06-home-no-agg.png`),各源 Top 为累计,状态磁贴少「回填失败」。
3. **首页图表首帧生长动画**:conventions §7 工作区「不要入场编排」;本稿按反馈 5 做了,限首页、300ms、不循环、`reduce` 归零。要么登记为具名例外(「运维首页图表生长」),要么删掉只留 hover / 滚动数字两类反馈动效。
4. **首页总闸只读**(定):8 个开关点只读、点击跳所在板块;关闭「公开分享」「游客访问」会立即作废外链 / 踢掉在线游客,放首页可拨误触面大。
5. **备份状态 chip 改状态点**:任务书写「备份状态 chip(成功 / 未完成)」,但 3 列 S 磁贴 1920 以下放不下「备份未完成」文字 + 「2.4 GB 媒体库」;本稿用绿点 / 红菱(与待处理同一套形状 + 色双通道),文字进 `title` 与页名 hover 浮层。备选:S 磁贴的数字区在窄时隐藏标签「媒体库」而保留 chip。
6. **阅读与榜单在「无聚合端点」时窗口不一致**(主角是窗口内阅读次数、Top 5 是累计):要么接受(浮层标明「累计」),要么主角也退回累计阅读,要么拍板②。
7. **新增字号档**:L 主角 36px、次级 18px(与现行 `.kpi-num` 28 / `.stat-number` 26 都不同),实装要先进 conventions §3 的表。
8. **浮层口径改为「图例固定顺序 + 0 值转灰 + 合计行」**(沿用 v2 待拍板⑥,仍待定)。
9. **功能页 8 页、组名退役、面包屑弹层单列按重要度**(沿用 v2 ①⑦,v3 把顺序改为 账户 > 阅读与榜单 > 读者功能 > 播客 > 分析与标签 > 存储 > 自定源 > 反馈)。
10. **S 磁贴窄时收图标**(容器查询,内容盒 < 270px 即 1280 视口):1280 与 1440 下 S 磁贴页头长得不一样;备选是 S 磁贴一律不画图标。

## 偏离任务书与已知局限

- **行高跟视口走而不是固定 300 / 190**:任务书给的是 1440 基准的近似值;本稿让行 1 / 行 2 分余量,所以 1440 下行 1 = 431 而不是 300,行 1 : 行 3 = 7.7 而不是 5——更「一眼可辨」,且 1920 不留白。要固定 300 / 190 只需改 `grid-template-rows`,1440 下底部会空 214px。
- **播客磁贴页头「1,282 期」chip 删掉**(自查时发现 M 磁贴多了一个数字),单集总数进主角 hover。
- **备份状态用状态点而非 chip**(待拍板⑤)。
- **S 磁贴 hover 链接 ≤ 2 个短名**(备份 · X API / 总闸 · 源表 / 反馈 · 公告),任务书允许 ≤ 3;3 个在 1280 的 S 磁贴里放不下(会压到页名)。
- **S 磁贴不画待处理点**:告警只在状态磁贴出现一次;存储的备份状态点、反馈的 warn 色数字是这块自己的状态。
- **1280 下阅读与榜单的 KPI 行很满**(5 列磁贴内容盒 438px 放主角 + 3 次级),次级数字因此定 18px;1440 起宽裕。
- 切时间窗时柱是「重排后再生长一次」,不是逐柱过渡到新位置(窗口长度变了柱数也变,逐柱过渡意义不大;实装 recharts 自带过渡)。
- 单集抽屉、游客设置抽屉、新建账户弹窗、列头就地搜索仍只给 toast 示意;低保真 5 页未动。

**顺带发现的现网小毛病**(沿用 v2,实装顺手修):`adminUtils.PURPOSE_LABELS` 漏 `event_assignment`;`BriefInterestZone` 三字段旋钮卡窄列折行;`PodcastZone` 处理参数 `<strong>` 落成 900。

## 构建与自检

源与脚本在 `tmp/issue-114/kit/`(已 gitignore;v1 的 `src/`、v2 的 `src-v2/` 原样保留):`src-v3/{icons,data,registry,charts,app}.js` + `src-v3/ops.css` + `fonts-v2/score.css`;`build_v3.py` 拼单文件(现网样式取自与本 worktree 逐字节相同的 `index.css` / `mobile.css` 的 vite 构建产物,按本页实际用到的类名抽取),`check_v3.py --collect` 走遍状态收类名,`check_v3.py` 跑断言与截图(需带 Playwright 的解释器)。自检 18 项全过:一屏(1280 / 1440 / 1920 × spec 收起 / 展开,全部磁贴完整可见)、无横向滚动(3 宽 × 7 状态 × 9 页)、字号 ≥ 12px 与字重只 400 / 600(含 hover 态)、首页 50 个入口落位(含 S 磁贴 hover 链接与开关点)、面包屑横切 8 页 + 回首页 + 后退 + 页头无搜索框、深链与旧深链 8 例、浮层 19 处出现且不越出视口(含锚定位置)、S 磁贴 hover 高度不变、开关点名称 hover 浮出 / 关着常显、`data-motion="reduce"` 下 transform 为 none 且柱不生长、切时间窗数字滚动、控制台零报错。
