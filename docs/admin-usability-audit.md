# 管理面/运维面可用性审计与共识清单(v3.40.3)

> 状态签:◉ 活跃(整改推进中随进度更新)。
> 日期:2026-09-01。触发:内网读者上量至约 150 人,产品负责人反馈「管理面好看但实用性不足」,
> 点名两个痛点——①逐账户开功能(如 AI 开关)无批量、只能逐行手点;②账户管理看不到「当前管理员有哪些」、
> 也无法按阅读量/登录次数排序(缺筛选/排序)。
> 方法:Claude 与 codex(gpt-5.6-sol xhigh)**各自独立审计**(避免锚定)后交叉核验、三轮讨论达成共识。
> 本文是审计快照,权威事实来源仍是代码;file:line 为审计当时行号,整改时以现码为准。

## 整改记录

- **v3.40.4(2026-09-01)P0 安全收口波:M01–M04 已修**,两个假绿灯测试同步修正,新增 7 项护栏测试。
  - M01:`/api/mcp/toggle` 单列进 `account_admin_required`(admin-only)并入 `AUDIT_PATH_PREFIXES`
    (语义摘要「切换全站 MCP 总闸」);`GET /api/mcp/status` 保持读者可读。
  - M02:`summarize` 三处补登记——`ai_usage.VALID_PURPOSES` / `_AI_DAILY_CALL_LIMITS`(50/日,同 translate
    档,整文级调用)/ 端点接 `_enforce_ai_daily_quota`;全站日预算自此真实吃到速读消耗。
  - M03:**删除语义拍板(负责人 2026-09-01)= 资产物理删除 + 计量墓碑化**。`delete_user` 单事务收口:
    订阅/令牌/收藏/公开分享/读态与水位/反馈/公告 dismiss 物理删除,自定源随退订走「无人订阅即物理删」
    (`user_sources.purge_account_user_sources`),AI 用量/阅读/登录事件改写 `deleted:<原名>` 墓碑
    (看板与成本历史保真,同名重建零继承);审计日志与 JobRecord.created_by 保留原名。router 不再两段提交。
  - M04:`users.session_epoch` 会话世代列(迁移 `b4a1c7e5d2f8`)——登录 token 带 `gen`,校验须与列值一致;
    改密/重置轮换世代即吊销既有 Cookie,建号随机初始化令同名重建不复活旧 Cookie;自助改密就地续签本人
    Cookie 不断线。存量行/旧 token 按 "" 对齐,升级不强制全员重登。
- **v3.41.0(2026-09-01)账户管理 V2 波:M05/M06/M07/M19 已修**(整改分层第二梯队,两个点名痛点收口)。
  - M05:`POST /api/accounts/batch`(原子语义全成或全不成,末位保护按整批终态裁决,批量入审计)
    + 前端复选列/页级全选/批量条(开关 AI、启停、改角色,破坏性动作过 confirm)。
  - M06:`GET /api/admin/accounts` 加 `role/status/ai` 组合过滤 + `sort/order` 八键服务端排序;
    前端筛选条(三组 mini-seg)+ 可点排序表头(aria-sort)+ 账户 KPI「管理员/停用」下钻即筛;
    summary 与 Top 榜恒聚合全量。
  - M07:`usage_by_user`/`reads_by_user`/`logins_by_user`/`reads_by_source` 改 SQL GROUP BY,
    不再整窗明细进内存;账户面聚合排除 `deleted:*` 墓碑(成本看板 `summarize` 口径保留)。
  - M19:活动详情抽屉加就地管理动作行(AI/角色/启停/重置密码/删除,操作后抽屉快照同步刷新;
    activity 端点 account 载荷补 `role`)。待负责人决策第 3 项的「全站均值基线」未做(产品分析增强,另议)。
- 其余项(M08 起)未动;M10 仍待确认内网机 `[runtime] role`。

## 协作与互补结论

双方独立审计后交叉核验,互补明显:
- **codex 独立轮**抓出 Claude 漏掉的**越权与身份生命周期**类问题(M01 MCP 越权、M03 删号残留、
  M04 会话不吊销、M12 组头全选绕过、M13 全量轮询、M21 计量写竞态)。
- **Claude 独立轮**抓出 codex 漏掉的**计费护栏穿透 bug**(M02 summarize 三层穿透)。
- 双方对彼此的新主张均做了代码级实锤验证;Claude 清单被 codex 修正 10 处表述(见文末)。
- 三项属产品拍板、双方不裁决,标记「待负责人决策」。

---

## P0 — 安全、成本护栏与身份完整性

### M01 普通读者可切换全局 MCP 总闸,且操作不入审计 [越权]
`POST /api/mcp/toggle`(app.py:1882)处理器自身无授权检查;`/api/mcp` 落在 `READER_API_PREFIXES`
(app.py:326 起),reader 前缀在中间件里**短路放行**,`account_admin_required`(app.py:374)不含
`/api/mcp`。`/api/mcp` 也不在 `admin_audit.AUDIT_PATH_PREFIXES`(admin_audit.py:19),故越权操作不留审计。
前端设置柜的 MCP 启停 switch 只在 adminConsole 露出——「后端裸奔、前端遮羞」形态。
- 影响:任一读者账户可关闭全站 MCP 服务且无痕。
- 证据:app.py:326 / app.py:374 / app.py:1882 / admin_audit.py:19 / tests/test_mcp.py:252,345(现有测试甚至以 role=user 断言 toggle 成功=假绿灯)。

### M02 速读(summarize)token 三层护栏全穿透 [计费/成本护栏]
`/ai/summarize` 端点(reader.py:1214 起)**根本没调用** `_enforce_ai_daily_quota`(对比 translate
端点 reader.py:1195 有调);且 `_AI_DAILY_CALL_LIMITS`(reader.py:1167)无 `summarize` 键;
且 `ai_usage.VALID_PURPOSES`(ai_usage.py:19)不含 `summarize`→`record_usage` 静默 return(ai_usage.py:61),
AiUsageRecord 永无 summarize 行;而 `accounts.READER_AI_BUDGET_PURPOSES`(accounts.py:42)**含** summarize。
- 影响:速读消耗同时**不受逐用户调用限额、不计入全站日预算累计、也不进用量看板**——v3.34 成本护栏对速读完全失效,可被持续触发刷爆共享 LLM 预算。
- 证据:reader.py:1167,1195,1214,1226 / ai_usage.py:19,61 / accounts.py:42 / tests/test_reader_ai.py:454(只断言 UsageMeta 传参,未断言落库=假绿灯)。

### M03 删除账户只清 users/订阅/feed token,大量个人资产残留;同名重建可继承 [身份生命周期]
删除流程(accounts.py:444、accounts.py:92-98)先单独提交 users 删除,随后只清订阅与 feed token。
收藏、公开分享(article_shares)、逐篇读态、阅读水位、AI/阅读/登录计量、反馈、公告 dismiss、
用户自定源创建者信息均保留。username 是这些记录的身份键→日后重建同名账户可能重新看到旧收藏/读态/反馈/分享。
旧公开分享链接也不随账户删除而撤销。两阶段提交在第二阶段失败时还会产生部分删除。
- 证据:accounts.py:444 / accounts.py(router):92,98 / db.py:212,226,255,344,416。

### M04 重置密码不吊销既有会话;删号后同名同角色重建可令旧 Cookie 复活 [身份生命周期]
会话令牌只含用户名/角色/过期时间,无密码版本或会话世代(app.py:603,624)。重置密码只更新哈希
(accounts.py:174),既有 Cookie 继续有效(默认最长 604800s=7 天,config.py:59)。删号后旧 Cookie
暂因查不到用户失效,但同名同角色账户重建后旧 Cookie 又重新通过校验。前端文案「设置后该账户需用新密码登录」
(AdminOpsTab.jsx:1011)与实际语义不符。
- 证据:config.py:59 / app.py:603,624 / accounts.py:174 / AdminOpsTab.jsx:1011。

---

## P1 — 150 人规模下的核心管理可用性(含两个点名痛点)

### M05 账户状态变更无任何批量能力 [点名痛点①]
表格无选择态/复选框/批量栏;AI、角色、启停均逐行调单账户接口。后端只有 `PUT /api/accounts/{username}`,
无批量目标、无全选当前过滤集、无预检、无原子提交、无逐项结果契约。给 150 账户批量开 AI = 约 150 次
点击+请求+刷新。放大器:每次单行动作后 `reloadAccounts()` 会为**全部账户**重算窗口聚合(admin.py:199-212)。
- 证据:AdminOpsTab.jsx:312,322,563 / api.js:98 / accounts.py(router):56。

### M06 账户表无角色/状态/AI/活跃过滤,也无服务端排序 [点名痛点②]
服务端参数仅 days/skip/limit/q,固定按用户名排序(accounts.py:137);前端表头纯静态文本
(AdminOpsTab.jsx:551)。管理员数量只在 KPI 展示、不可下钻列出身份;阅读/登录仅生成独立 Top 8 图
(AdminOpsTab.jsx:420),不能按该指标重排完整账户表。→「当前管理员有哪些」需逐页找,「按阅读/登录倒序」
只能看前八名且无法翻页。role/is_active/ai_beta_enabled 三列 DB 有索引(db.py:479-484)却无端点使用。
- 证据:admin.py:176,245 / accounts.py:137 / AdminOpsTab.jsx:420,551。

### M07 账户「服务端分页」只裁响应行数,不裁数据库与 Python 聚合工作
每次分页/搜索都先读全部账户,再分别构建全量订阅/AI/阅读/登录/最近登录映射,用户名过滤与切片最后在
Python 执行(admin.py:198,245)。AI/阅读/登录聚合把整个时间窗明细行载入内存再累计(ai_usage.py:169、
reader_activity.py:66、accounts.py:296)。搜索防抖后每次关键词变化重复整套。内容看板默认全量载
reader_reads 按源累计(reader_activity.py:81,不带 days 全表扫);公开分享 KPI 读全部分享记录 Python 判活
(admin.py:478)。耗时随活动记录数增长而非随每页行数。
- 证据:admin.py:198,245,478 / ai_usage.py:169 / reader_activity.py:66,81 / accounts.py:296。

### M08 用户自定源治理不可规模化(N+1 + 前端零规模化)
`admin_overview`(user_sources.py:753)返回全量源,对每个源逐一调 `active_subscriber_usernames`
(user_sources.py:193),后者每次全表载活跃订阅+解析过滤+查对应用户→形态 ≈ 源数×全订阅扫描+每源用户查询。
前端(UserSourcesPanel.jsx:15)全量渲染,无搜索/失败态过滤/创建者过滤/排序/批量停用。连带:全部 user_source
被注入 FetchTab 节点板,而该板无分页无文本搜索(FetchTab.jsx:213-222)。行数上界 ≈ 读者数×人均自定源数。
- 证据:admin.py:500 / user_sources.py:193,753,768 / UserSourcesPanel.jsx:15。

### M09 运行历史固定截断 100/200 条后本地过滤分页,却按完整窗口展示
FetchRunsTab 固定只拉最近 100 任务级 + 200 节点运行(FetchRunsTab.jsx:395),状态/触发方式/任务/时间窗
过滤全在前端对这部分数据执行,页脚仍显示「X 次运行·近 N 天」。窗口内运行超上限时较老失败记录静默消失;
日统计与截断列表可互相矛盾。后端支持 skip/limit 但不返回 total,前端未真正用服务端分页。
- 证据:FetchRunsTab.jsx:395,643,661,1180 / collection.py:254 / monitoring.py:197。

### M10 reader-only(或独立 reader 数据库)不执行 retention [条件性,需确认内网 runtime role]
留存任务只在 `runtime_collector_enabled()` 时注册(app.py:406,413);reader 角色不启动调度器。而登录/AI/阅读/
(可能)管理审计数据恰主要由 reader 服务写入。**若内网是 reader-only 部署或 reader 与 collector 用不同库,
这些表永不自动清理**。共享同库且 collector 常驻时可降 P2。
- 证据:app.py:406,413,419 / retention.py:58。
- 行动项:确认内网机的 `[runtime] role`——若 reader,此项对本部署即实打实 P1。

### M11 审计覆盖与检索能力不足
`AUDIT_PATH_PREFIXES` 漏 `/api/articles`、`/api/fetch`、`/api/archive`、`/api/mcp` 等管理写入口→
批量删文、手工录入、触发采集、归档导入、MCP 总闸操作不入审计。审计面只有时间窗+翻页+刷新,后端只有
days/skip/limit;无法按操作者/目标/动作/状态码搜索过滤,也不能导出。
- 证据:admin_audit.py:19 / api.js:262,284,477 / AdminAuditPanel.jsx:20 / admin.py:335。

### M12 FetchTab 组头全选绕过「自定源不可批量」限制 [已实锤]
单行自定源复选框正确禁用、全选可见也正确排除自定源;但组头 checkbox 的 onChange(FetchTab.jsx:1181-1186)
直接把 `section.fetchers` 全量加入选择集(含自定源),`runChecked`/`saveCheckedAsJob` 再从 visibleFetchers
取所有已选 ID、不二次过滤→可把自定源发给不支持它的 registry 批量运行/任务接口。
- 证据:FetchTab.jsx:511,521,636,1170-1186。

---

## P2 — 运维可靠性、响应体与工作流完善

### M13 source-health 每 45 秒读取全部 180 天抓取运行史 [已实锤]
前端固定 45s 轮询(FetchTab.jsx:62,254);后端无条件 `select` 全部相关 FetchRunRecord 再 Python 按 fetcher
分组(monitoring.py:140,150),即便多数源已有 SourceStateRecord 快照。180 天保留期下为持续 SQLite I/O 与对象创建负担。
- 证据:FetchTab.jsx:62,254 / monitoring.py:140,150。

### M14 留存覆盖不完整,多张运维表无限增长
当前仅覆盖 fetch_runs(180d)/login_events(365d)/admin_audit_logs(365d)/ai_usage(730d)/reader_reads(730d)
(retention.py:57-64)。未纳入:collection_job_runs、jobs、feedbacks、article_shares、announcement_dismissals。
`list_jobs(limit=50)` 只限读取不清理。长期出现子运行已清/父运行仍在、后台任务与消息记录持续累积。
- 证据:retention.py:57 / db.py:66,321 / jobs.py:103,143。

### M15 运维面一次性预取所有子页数据,之后长期不刷新
AdminOpsTab 挂载即并发载 全局开关/LLM/内容/媒体/X 状态(AdminOpsTab.jsx:147,210),之后无统一手动刷新
或轮询。账户/AI 仅在页码/搜索词/时间窗/行操作时刷新;反馈/公告只在挂载或自身操作后重取。tab 常驻不重挂
→组件长开时新登录/阅读/反馈/公告/配额变化不主动出现。全站面板级刷新按钮仅审计面板一个。
- 证据:AdminOpsTab.jsx:147,210,453 / FeedbackInboxPanel.jsx:50 / AnnouncementsPanel.jsx:37。

### M16 AI 用量端点返回全部日×用户组合,前端只画 Top 6
`by_day_user` 返回窗口内全部实际日×用户组合、无后端 Top-N 或「其它」裁剪(ai_usage.py:157),前端仅画 Top 6。
高基数(非稠密笛卡尔积,但仍随用户×天增长)。应服务端选 Top N 并按日聚合「其它」。
- 证据:ai_usage.py:100-159。建议整改前先测当前 90 天生产响应体积,若达 MB 级可升 P1。

### M17 消息治理仍是小团队形态
反馈有状态过滤+服务端分页,但无按用户名/分类/正文/日期搜索,无负责人/优先级/批量关闭/导出;已完成反馈无留存。
公告接口每次全量返回+全表聚合关闭人数(announcements.py:98),前端全量渲染所有历史公告,无分页/搜索/归档态。
- 证据:FeedbackInboxPanel.jsx:14 / feedback.py:229 / announcements.py:98 / AnnouncementsPanel.jsx:149。

### M18 核心运维数据无产品化导出;知识台账导出与当前筛选脱节
账户/审计/AI 用量/反馈/运行无面向管理员的下载或专用导出端点(系统已有文章 JSONL 归档导出,故非「全系统零导出」)。
文章导出入口在设置页,只支持抓取日期、强制 source_ids=''、skip=0、最多 5000 条,无法导出台账当前的来源/形态/
正文态/搜索结果/已选条目,也无法连续导出 5000 条之后。
- 证据:api.js:120,129,138,695(缺)/ api.js:470(文章导出存在,反证)/ articles.py:115,221 / DataSyncSection.jsx:79。

### M19 逐用户活动抽屉无管理闭环
抽屉查看异常后无法就地停用/开关 AI/改角色/重置密码(AdminOpsTab.jsx:880-953 仅关闭钮),须回表格行再操作。
(注:codex 指出「无全站均值基线」属产品分析增强而非缺陷,已从本项剥离,见待决策项。)
- 证据:AdminOpsTab.jsx:880-953。

### M20 无批量开户/导入或邀请流程
前后端只有单账户创建(accounts.py(router):45),无 CSV/批量开户契约。150 人初始建号只能逐条录入。
codex 建议:长期做批量邀请/临时凭据/SSO,不建议简单上传明文密码 CSV;核心负担是人工逐条录入+初始密码交付+失败重试
(PBKDF2 200k 轮非主要瓶颈)。
- 证据:accounts.py(router):45 / AdminOpsTab.jsx:277 / accounts.py:44。

### M21 AI/阅读日聚合写入无复合唯一约束,存在并发重复行/丢增量风险
ai_usage 无 (day,username,purpose,model) 唯一约束,reader_reads 无 (day,username,source_id) 唯一约束
(db.py:344,365)。两条写路径均「先查→无则插/有则 Python 递增→提交」(ai_usage.py:71、reader_activity.py:46)。
并发请求可能同时判「无记录」各插一行,或同时基于旧值写回→阅读量/AI 排名不准。
- 证据:db.py:344,365 / ai_usage.py:71 / reader_activity.py:46。

---

## P3 — 低频优化、文档债与产品边界

### M22 单用户活动抽屉兜底最近登录时会聚合全部用户
仅当账户快照 last_login_at 缺失时触发(admin.py:318 → accounts.py last_login_by_user 全表 GROUP BY)。
应改为当前用户名 MAX(at)。days 已在 service 层夹到 1-365,不算无保护。
- 证据:admin.py:270,318 / accounts.py:280。

### M23 审计端点 docstring 与实际 retention 状态不符 [纯文档债]
admin.py:342 仍写「当前尚未实现留存期限与自动清理」,但 retention.py:61 已覆盖审计表且 app.py:413 已注册每日任务。
- 证据:admin.py:342 / retention.py:61 / app.py:413。

### M24 移动端不提供任何管理能力 [显式产品边界]
≤767px 只渲染 MobileReader,移动设置隐藏管理组(App.jsx:631,633、MobileSettings.jsx:13),注释写明管理台不下放。
非 bug 而是既有设计决策。若需手机值班(紧急熔断 AI/MCP、公告、停号)可升 P2。
- 证据:App.jsx:631,633 / MobileSettings.jsx:13。

### M25 宽泛 FTS 搜索先物化全部命中 rowid 再生成大型 IN 条件
FTS 查询无候选上限,先把所有命中 rowid/rank 拉进 Python 字典,再把全部 ID 放进文章列表与 count 查询的 IN 条件
(fts.py:167,193、articles_view.py:83)。高频词或大库产生大结果集与超长 SQL 参数;<3 字符搜索退化为标题 LIKE。
- 证据:fts.py:167,193 / articles_view.py:83。

---

## 待负责人决策(双方不裁决)

1. **移动管理是否近期需求**:若管理员需手机上紧急熔断 AI/MCP、处理公告/停号,M24 从 P3 升 P2;否则维持。
2. **账户删除的产品语义**:「彻底物理删除」vs「匿名化保留运营统计」。无论哪种,都必须消除 M03 同名继承与 M04 旧会话复活。
3. **抽屉均值/分位数基线**:「就地管理动作」(M19)已成共识;全站均值基线属产品分析增强,做不做由负责人定。

## 整改建议分层

- **先做**:M01/M02(安全与成本护栏,一旦利用后果直接)、M03/M04(身份生命周期,与账户改造同期)。
- **账户管理 V2 一波交付**:M05+M06+M07+M19(服务端组合过滤/排序 + 当前页与全过滤集批量 + SQL 聚合 + 抽屉管理闭环)。
- **运维列表规模化**:M08(自定源治理)、M09(运行历史真分页)、M11(审计过滤+覆盖)、M17(消息治理)。
- **数据生命周期与观测**:M10(确认 runtime role)、M14(补留存)、M13/M15(刷新语义)、M21(唯一约束)、M16(响应体裁剪)。
- **随量优化/文档**:M18(导出)、M20(批量开户)、M22/M23/M25、M24(视产品决策)。

## Claude 清单被 codex 修正的 10 处表述(已接受,存证)

1. 「账户面是全站唯一没有批量的列表」撤回——自定源/反馈/公告同样缺,账户面是点名+影响最大而非唯一。
2. M08 的「2×源数+3」改为近似查询形态(identity map/KV/状态/文章计数影响实际条数),N+1 核心结论不变。
3. 「Python 内聚合全线」收窄为「关键窗口指标仍大量 Python 聚合」(订阅数/最近登录 MAX/部分内容统计已用 SQL GROUP BY)。
4. M16「稠密笛卡尔积」改为「高基数、无后端 Top/裁剪」,降 P2。
5. last_login 兜底(M22)由高估降 P3(days 已夹取、仅快照缺失触发)。
6. 逐用户抽屉「无全站均值」与反馈「一次只展开一条」剥离为产品增强,真共识分别是 M19「抽屉无管理闭环」与 M17「反馈无搜索/批处理」。
7. M09 页码不窗口化当前无规模后果(硬截断最多约 6 页),并入真分页验收。
8. M23 只作 P3 文档债,不与留存调度缺陷同级。
9. 「零导出能力」标题收窄为「核心运维数据无导出」(文章 JSONL 导出已存在)。
10. M24 定性为显式产品边界,非实现 bug。
