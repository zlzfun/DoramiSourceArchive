# issue #262 外部 Agent 主动收录:无源 / 临时源内容的语义审视与「源目录数据化」设计(R1)

> 状态:进行中(2026-11-06 起实施;§7 拍板项 D1–D8 已于 2026-11-06 由用户按推荐拍板;波 0 与波 1 随首个 PR 落地,实施记录见 §10;源目录数据化的后续步骤见 [源模型重构方案](./issue-262-source-model-refactor.md) §12)。
> 执行:主代理 Claude Code(Opus 5.5)+ 4 个只读子代理分域盘点(源定义层 / 入库与分析 / 读者分发 / 同步·鉴权·运维·治理);标「高」的问题由主代理逐条回源核实,未实跑的标「静态确认」。
> 基线:`main` @ `454f90da`(v3.67.4)。文中 `file:line` 均对此版本。

## 0. 结论(TL;DR)

1. **平台里没有「源」这个实体,只有一个被十几张表冗余引用的裸字符串 `source_id`**。它同时承担五个角色:出处身份(谁说的)、采集通道(怎么来的)、策展准入(该不该给人看)、可见性归属(公共 / 私有 / 权威)、呈现形态(文章 / 社交 / 播客)。代码预设类把五者打包进一个 Python 类,SourceConfig 打包进一行,而 Agent 注入的内容只有「出处」,其余四项全部缺失。
2. **缺失时各处兜底口径互相矛盾**——这是本议题最根本的语义问题:「查不到配置行」在分析 / 外发 AI / 读者目录处被当作「可信平台源」放行,在评分处被当作最低信任的 `media`,在前端被当作 `official`;准入状态只管采集调度、读者面不读;「一个 `source_id` = 一个独立策展信源」的假设被多源印证、每源配额、事件 publisher 计数广泛依赖,而注入方可以任意命名 `source_id`。
3. **现在就能踩到的缺陷(与设计取向无关)**:社交导入默认把所有账号塞进一个源;播客单集在没有播客配置行时**根本写不进库**,接口却报「ID 已存在」;注入 id 无命名空间,撞上他源占位行会覆写对方正文;X 配置源条目被打成模板 id(静态确认);内网对社交导入静默丢弃;两个入口零测试、社交导入不入审计。见 §4.1。
4. **设计建议**:把「源」立成一个数据实体——**每个出现在库里的 `source_id` 都恰有一行源目录记录**,属性显式、来路(origin)显式;代码只保留「怎么抓」(抓取能力 / 站点适配器),不再定义「谁」。Agent 收录走专用推送通道:带作用域的令牌 + 身份解析(认出已有源)+ 服务端生成条目 id + 未知出处自动建**临时源**(`provisional`,有完整属性、不进发现目录、不参与多源印证,可一键转正 / 合并 / 下架)。
5. **能否取缔硬编码源?** 能取缔「一个源 = 一个 Python 类」:62 个具体源里约 29 个是纯数据、约 15 个只差几个通用参数,只有约 17 个需要专门解析代码——它们改为**无身份的站点适配器**。但**不建议取缔「仓内声明」**:策展源的身份与准入改由一份声明式目录文件(数据,走 PR 审阅)承载、启动时对账入库,保留「git 即审计」;Agent / 管理员 / 读者创建的源只在数据库。播客目录(`PODCAST_CATALOG` → 配置行)已经是这条路的先例。
6. **分四波**:波 0 缺陷修复(可立即做);波 1 推送最小闭环(临时源 + 令牌 + 身份解析 + 来路字段);波 2 源目录数据化(先镜像行 → 目录文件 → A / B 档类收为能力 + 参数);波 3 别名合并、全通道跨源 URL 去重。见 §6。

## 1. 场景与边界

- 场景:用户的主动式 Agent 自行发现文章、推文(今后可能有播客单集),推送进平台。来源常常是平台从未定义过的:一个未登记的 X 账号、一个从未配置过的博客、一个大平台上的单篇。这会是**常态通道**,不是手工录入的边角。
- Agent 本身是用户可信的运营工具,但它**不是代码审阅过的策展者**:它的判断是「这一篇值得收」(条目级),而平台现有策展是「这个源值得长期看」(源级)。
- 范围:外网 authority 节点的收录;内网 replica 只经 Archive Sync 接收。读者自定源(`user_rss_`)不在本议题改动范围,只作为对照。
- 不在范围:Agent 自身的发现 / 筛选逻辑;付费 ASR / TTS 的成本策略(沿 #97)。

## 2. 现状:「源」从哪来、有哪些属性

### 2.1 产生渠道

| 渠道 | 创建者 | 存储 | `source_id` 形成 | 规模 |
|---|---|---|---|---|
| 预设抓取器类 | 开发者写代码,`impl/` 导入即注册(`fetchers/registry.py:185-209`) | 类属性 | 手写,前缀约定 `rss_ / web_ / docs_ / github_ / hf_ / x_` | 69 类 = **62 具体源 + 7 模板**;具体源 55 article / 7 social;38 观察期 / 24 正式;`ESSENTIAL_FETCHER_IDS` 恰等于全部 62 个,`FOCUSED_FETCHER_CURATION` 为空 |
| 管理员配置源 | `POST /api/source-configs`(`source_configs.py:246-290`);source_builder 产出也走这里 | `source_configs` 行 | 人工填;source_builder 用 `{web\|rss}_{host}_{seg}` | 运行时数据 |
| 读者自定源 | `/custom-sources`、OPML 导入 | 同表,`owner_username` 非空 | `user_rss_` + sha256(规范 URL)[:12] | 每人 ≤ 20 |
| 播客精选目录 | 启动自动导入(`app.py:747` → `podcast_catalog.py:399-411`) | **代码常量 `PODCAST_CATALOG` → 配置行** | `podcast_{slug}` | 37 条,一律 `incubating` |
| Archive Sync 接收 | 远端权威推送(`archive_sync_v2.py:2004-2049`) | 同表,`collection_authority_id` 非空 | 沿用对端 | 运行时数据 |
| **只在 `articles` 里的「导入源」** | `POST /api/articles`(默认 `manual`)、`POST /api/import/social-posts`(默认 `import_social_posts`) | **无登记**,读者目录从 `articles` 聚合出来(`reader.py:1401`) | 调用方随意 | 隐含概念,只在注释里出现 |
| 特殊源 / 墓碑 | 日报 `dorami_daily_brief`;`DECOMMISSIONED_FETCHER_IDS` | 代码常量(墓碑在 `registry.py:103-121` 与 `source_naming.py:19-29` 两处重复) | 固定 | 1 + 7 |

### 2.2 属性在哪、缺了等于什么

| 属性 | 代码源 | 配置源 | 只在 `articles` 里的源 |
|---|---|---|---|
| 名称 | `cls.name` | `name` 列 | 兜底表 → **裸 id**(`source_naming.py:135-145`) |
| 图标 / 头像 | `cls.icon`;X 头像在 KV `x_api_user_cache:<sid>`,只由抓取器写 | 无图标列 | 空 → 前端「通用能力节点 ⚙」(`sourceTaxonomy.js:58,78-80`) |
| 形态 | `cls.content_shape` | 由 fetcher / `source_type` 推导 | 按**多数** `content_type` 兜底(`api/sources.py:93-100`) |
| 平台 | 仅 7 个 X 类有 | `source_type` 推导 | **空**(`reader.py:1369`),尽管每条帖子自带 platform |
| 角色(官方 / 媒体 / 个人 / 榜单) | 由 scope / tier 推导 | 同 | 后端 **media**(`source_naming.py:115`),前端 **official**(`sourceTaxonomy.js:253`) |
| owner / brand / scope / tier / signal / noise / reliability | 类属性 | 同名列 | 空 |
| 准入 | 类属性,**只决定是否进每日采集** | 有列,**只影响徽记**(每日任务只枚举注册表) | 无 |
| 默认可见 | `ESSENTIAL_FETCHER_IDS` | 无 | 无 |
| 隐藏 | KV `reader_hidden_source_ids`,三类统一 | 同 | 同 |
| 私有 / 凭证 | 默认公开 | `owner_username` + 凭证推断 | 公开(除非 id 恰好以 `user_rss_` 开头) |
| AI 分析开关 | 默认开 | `ai_analysis_enabled` 列 | 默认开(`article_analysis.py:402-403`) |
| 内容类型先验 `default_kind` | 62 个里 16 个有 | **无此能力** | 按 `content_type` 兜底(可伪造) |
| 语言 / 地区 | 无 | 仅播客 `params_json.language` | 无 |
| 健康 / 游标 | `SourceStateRecord`(按 source_id) | 同 | 无 |

优先级也不统一:名称 / 形态 / 目录 / 执行是「注册表压配置」(`collection_nodes.py:126-128`、`reader.py:1312`),角色是「配置压注册表」(`source_naming.py:75-98`)。

## 3. 抽象审视:语义矛盾

**C1 · `source_id` 一身五职。** 出处、通道、策展、可见性、形态共用一个键。对抓取源这恰好成立(一个抓取器 = 一个出处 = 一条通道),对 Agent 收录不成立:@sama 的一条推文,出处是 `x_sama`,通道却是「Agent 推送」;一篇随手收录的博客,出处是那个博客,却没有任何通道会再给它产出。读者目录对「源」的承诺是「订阅以接收其后续产出」(`reader.py:1457`),**一个没有通道的源被订阅后永远不会再更新**——承诺落空。

**C2 · 策展粒度错位。** 平台策展是源级(准入、默认订阅、合集都是源级);Agent 的「收录」是条目级判断。现在条目级的判断只能伪装成源级的存在——给一篇文章造一个源,它就自动出现在发现目录、可被「订阅全部文章源」一键订上(`reader.py:1506-1527`),等于**未经审阅直接转正**。`docs/sources/curation_policy.md:118`「不进白名单不得默认可见」对导入源是一纸空文。

**C3 · 「缺失」被当成属性。** 三处默认值从「查不到配置行」推出,方向相反:

| 消费方 | 「无配置行」被解读为 | 出处 |
|---|---|---|
| 分析入队、外发 AI(翻译 / 识图 / ASR / 问答)、分享 | 可信平台源 → 放行 | `user_sources.py:235-243`、`article_analysis.py:397-403` |
| 评分与早报的来源角色 | 最低信任 `media` | `source_naming.py:115` |
| 前端发现页分组 | `official`(进「官方」组) | `sourceTaxonomy.js:253` |
| 事件归并的形态 | `article`(**不**按 content_type 兜底,推文可当代表稿) | `event_assignment.py:376-383` |
| 事件榜展示的形态 | 按 content_type 兜底 | `event_board.py:168-171` |

后端注释声称前后端角色判定「逐字一致」(`source_naming.py:12`),对未登记源不成立。根因不是哪个默认值选错了,而是**属性靠「不存在」推导**。

**C4 · 「一个 `source_id` = 一个独立信源」被计数逻辑依赖,而 id 由注入方决定。**
- 早报重大事件:≥ 2 个不同 `source_id` 且分 ≥ 9 即「多源印证」,推给订阅外的全体读者(含游客)(`digest_selection.py:780-784`);
- 榜单「必读」要求 ≥ 2 个不同 `source_id`(`rankings.py:485-489`);事件榜按 publisher(`source_owner`,无则 `source_id`)计数(`event_board.py:470`);
- 每源配额:早报每源 ≤ 2、日报单源 ≤ 60(`digest_selection.py:94,99`、`daily_brief.py`)。

所以同一件事,注入方用两个 id 推送 → 凭空造出「两家来源同时报道」;反过来全部塞进一个源 → N 个账号共用 2 条配额、印证数压成 1。**计数口径的正确性依赖于 `source_id` 是被策展过的出处**,注入打破了这个前提。

**C5 · 身份靠字符串推导,不靠身份键。** `x_<handle>` 的推导对不上已有预设(`Kimi_Moonshot` → `x_kimi_moonshot`,预设是 `x_moonshot_ai`);反之 `x_sama` 等 6 个又会**悄悄并进策展源**,继承官方角色、头像,并随默认订阅推给所有新用户和游客(`reader_defaults.py:40`)。全仓没有别名 / 重定向表;唯一一次源合并是播客收养,逐表改写 id(`podcast_source_adoption.py:317-427`)。

**C6 · 条目 id 无命名空间,存储只按主键去重。** 抓取器用 `{source_id}_{sha1(稳定值)[:16]}`,社交导入用 `{source_id}_{post_id}`,手工入口直接用调用方给的 id。旧行无正文而新条目有正文时,覆写旧行的标题 / URL / 正文 / extensions,`source_id` 却保留原源(`db_storage.py:264-280`)——**跨源改写忠实归档**。同一 URL 不同 `source_id` 则落两行、分析两次(只在事件层被 URL 直连合并)。

**C7 · `content_type` 名为「数据结构」,实为可伪造的标签。** `GenericContent` 不约束字段形状,`extensions_json` 任意键直通(`content.py:373-381`)。注入方可以:写 `content_type=arxiv` 得到论文类、写 `daily_brief` 得到汇编类(`content_classification.py:268-282`);伪造 `raw_data.listing_source` + upvotes 当社区信号、`detail_extraction_method` 触发论文全文抓取、`translation_zh_title` 冒充翻译回执(`article_analysis.py:1615-1628`、`paper_content.py:41-48`)。提示词 docstring 里「server-derived, never user-controlled」的前提(`article_analysis_prompt.py:334-336`)被打破。抓取路径有「血统覆写」约束(`fetchers/base.py:153-157`),注入路径没有。

**C8 · 来路不可追溯。** `ArticleRecord` 没有任何「谁、经哪条通道送进来」的字段;注入条目的血统列全空、`run_scope="ad_hoc"` 与临时抓取同值(`db_storage.py:332-336`);`create_article_manual` 连 Request 都不收,拿不到调用者;社交导入不在审计前缀里(`admin_audit.py:17-32`)。

**C9 · 代码源的元数据不随同步走。** sources 流只导出 `SourceConfigRecord` 行(`archive_sync_v2.py:269-301`),代码源的名称 / 角色要求内网跑同版本代码;只在 `articles` 里的源到内网后是裸 id 孤儿(接收端明确「must not synthesize a placeholder」,`archive_sync_v2.py:2340-2342`)。

## 4. 流程审视:逐环节问题

### 4.1 现在就成立的缺陷(与设计取向无关,波 0)

| # | 问题 | 出处 | 严重度 | 核实 |
|---|---|---|---|---|
| B1 | 社交导入批级 `source_id` 默认 `"import_social_posts"`(非空),`normalize_social_source_id` 有 fallback 即返回 → 所有账号挤进一个源,违反拍板「一账号一 source_id」(`decisions.md` X 社交波) | `ingest.py:126,131-132,144` | 高 | 已核实 |
| B2 | 播客单集必须有播客类型配置行才能入库;`/api/articles` 把 `save()` 的 False 一律报「该条目 ID 已存在」 | `db_storage.py:226-238`、`articles.py:834-835` | 高(#262 场景硬阻断 + 误导) | 已核实 |
| B3 | 注入 id 无命名空间、可撞他源占位行并覆写正文(见 C6) | `articles.py:813`、`db_storage.py:264-280` | 中(需调用方给出撞车 id) | 已核实 |
| B4 | X 配置源条目 `source_id` 被 `BaseFetcher.fetch` 覆写为 `generic_x_timeline`,主键前缀仍是配置源 id;RSS / 播客 / web 模板都在 `_run` 里切换了实例身份,X 模板漏了;测试直调 `_run` 绕过 `fetch()` | `base.py:155`、`x_timeline_fetcher.py:308`、对照 `rss_fetcher.py:284` | 中(当前生产 X 源都是预设类,潜伏) | 静态确认 |
| B5 | 内网对社交导入无 409 围栏,`save()` 内部拒绝 → 全部计入 `skipped`,与「重复」不可区分 | `ingest.py:188-201`、`db_storage.py:220-225` | 中 | 已核实 |
| B6 | 隐藏名单(对未登记源唯一的下架手段)不覆盖公共日报候选池与 `/api/feed/articles` | `daily_brief.py:601-612`、`articles.py:970-1000` | 中 | 子代理报告 |
| B7 | 前后端角色兜底相反(official / media) | 见 C3 | 中 | 已核实 |
| B8 | 事件归并的形态不按 `content_type` 兜底,与展示层、早报三处判法不同 | 见 C3 | 中 | 子代理报告 |
| B9 | 社交导入不入审计;手工录入审计不记 `source_id` | `admin_audit.py:17-32`、`app.py:2296-2317` | 中 | 子代理报告 |
| B10 | `extensions_json` 传对象或坏 JSON 被 `except: pass` 静默丢弃 | `articles.py:826-831` | 低 | 已核实 |
| B11 | 两个注入入口零测试(`tests/` 中无引用) | — | 中 | 已核实 |
| B12 | 文档过时:模板是 7 个不是 5 个、X 是 7 个不是 6 个(多 `x_zai_org`) | `fetchers-and-sources.md`、`curation_policy.md` | 低 | 已核实 |

### 4.2 各环节对「未登记源」的行为(设计问题,波 1 起解决)

| 环节 | 现在的行为 | 问题 |
|---|---|---|
| 入库 / FTS5 | 照常入库、触发器建索引;不调媒体预取(`app.py:1768-1808`) | 图片只在读者打开时按需缓存 |
| 分析评分 | 默认入队;来源名为裸 id、角色 `media`、`kind_hint` 来自可伪造的 `content_type` | C3、C7;不能按源关闭(除非先建配置行) |
| 外发 AI / 分享 | 非 `user_rss_` 前缀即放行 | 结论对(公开内容),但靠「缺失」推出 |
| 读者发现目录 | 有文章即进目录、可订阅、可被按形态批量订阅;名裸 id、图标 ⚙、分到「官方」组;`registered` / `configured` 标志下发了但前端无人消费(`reader.py:1378-1379`) | C1、C2、C3 |
| 订阅 | 单源订阅不校验源存在,订阅名冻结为裸 id(`app.py:4027-4035`),日后补配置也不改名 | — |
| 默认订阅 / 游客订阅 | 未登记 id 保存时被拒(`reader_defaults.py:90-123`) | 正确的门 |
| 个人早报 | 订阅外经兴趣池可入选;重大事件按 `source_id` 计印证 | C4 |
| 公共日报 | 源白名单 `daily_brief_source_ids` 未配置时全部源入池(`daily_brief.py:536-556`) | 生产是否配了白名单决定严重度(待核) |
| 榜单 / 事件 | 自定 `content_type` 算 article 进榜;未登记 social 被排除;必读按 `source_id` 计 | C4 |
| feed / MCP | Atom author 为裸 id;`list_sources` 只列注册表,检索却包含未登记源 | 口径分叉 |
| 播客处理 | 未登记源到不了(被 B2 挡) | — |
| Archive Sync | 文章照常同步,源元数据不同步;内网孤儿源裸 id 显示 | C9 |
| 运维面 | 源健康 / 节点目录看不到;节点行上的隐藏开关够不着(`FetchTab.jsx:238,430-433`);无「按源清除」;内容看板显示裸 id | 运维盲区 |
| 鉴权 | 只认 admin Cookie 会话;无带作用域的写令牌、无可写 MCP;`dsub_` / `dfeed_` / `dshr_` 均只读 | Agent 必须持有完整管理员权限 |

## 5. 设计:源目录数据化 + 推送通道

### 5.1 概念拆分

主线是三层:**源 / 采集绑定 / 抓取器**(2026-11-05 用户认可)。另补两条:采集绑定是 0..1 个,推送、手工录入、同步都是通道而不是绑定;发现页卡片是源的视图,不是源本身。逐家族适用面、目录文件、对账与分步展开见 [源模型重构方案](./issue-262-source-model-refactor.md)。

| 概念 | 回答的问题 | 落在哪 | 由谁定义 |
|---|---|---|---|
| **卡片** | 源在读者面出不出现、怎么出现 | 服务端由源属性算出「列出状态」(listed / unlisted / private / hidden / retired) | 服务端;前端只做展示映射 |
| **源 Source** | 谁说的(出处身份 + 呈现 + 策展 + 可见性) | 源目录表(沿用 `source_configs` 物理表,加列;不改表名,避开同步触发器与契约重命名) | 目录文件(策展源)/ 管理员 / 读者 / Agent / 同步 |
| **源身份键** | 怎么认出「这是同一个出处」 | 新表 `source_identity_keys(key_type, key_value) → source_id`,唯一约束 | 随源创建;身份解析器查 |
| **采集绑定** | 怎么定期拿到它的内容 | 沿用行上的 `fetcher_id` + `params_json`(0..1 个);**推送不是绑定** | 目录文件 / 管理员 |
| **抓取能力** | 拿内容的代码 | `FetcherRegistry`,**只登记能力,不再登记身份**:7 个通用模板 + 约 17 个站点适配器 | 代码 |
| **条目来路** | 这一条经哪条通道、由谁送来 | `articles` 加 `ingest_channel`(fetch / push / manual / sync)、`ingest_actor` | 服务端写,调用方不可控 |
| **收录记录**(可选,§7 D4) | 谁在什么时候「收」了这一条 | 新表 `article_picks(article_id, actor, picked_at, note)` | 推送通道写 |

核心不变量:**每个出现在 `articles.source_id` 里的值,源目录里都恰有一行**;所有消费方只读这一张表上的显式属性,删除一切「查不到即 X」的兜底。

源目录新增 / 显式化的列:`origin`(catalog / admin / reader / ingest / sync;实现时 2026-11-06 由 agent 改名为 ingest)、`shape`、`platform`、`icon`、`avatar_url`、`homepage_url`、`default_kind`、`language`;`admission_status` 取值扩为 `provisional | incubating | admitted`(另有 `retired_at` 表退役)。

### 5.2 临时源(provisional)的生命周期

```
推送遇到未知出处 ──自动建──▶ provisional ──管理员「列入」──▶ incubating(进发现页、参与印证与日报候选)
                                ├──「合并到」已有源 ──▶ 写别名 + 迁条目(波 3;波 1 先只支持推送前改指向)
                                ├──「下架」/「恢复」──▶ 隐藏 + 拒收后续推送 / 撤销
                                └──「改名」
```

临时源**不设采集绑定**(2026-11-06 用户拍板):它只是一次性收录时遇到的出处,内容是送进来的,平台不知道
怎么抓到它,谈不上也不需要定时触发。无绑定的源上,观察期与正式只差管理面徽记。「想定期跟踪同一出处」
是另一件事——新增一个策展源(R2 后在目录文件里加一条、走 PR),可沿用临时源的同一个 id,条目 id 公式
与抓取器一致,历史条目自然归并;它不属于临时源转正。

临时源在各环节的默认策略(推荐值,§7 D2 / D3 拍板):

| 环节 | `provisional` 的行为 | 理由 |
|---|---|---|
| 源属性 | 由推送载荷的出处描述填(X:名称 / handle / 头像 / user_id;网页:站名 / 主页);角色显式记 `unvetted`,评分与早报按 media 档处理、前端显示「收录」 | 解 C3:属性来自数据而不是缺失 |
| 发现目录 / 批量订阅 | **不列入**;条目卡片上的来源名可点开源页,源页可单独订阅 | 解 C1 / C2:不把「一篇」包装成「一个长期源」 |
| 全站列表 / 搜索 / 详情 / 问答 | 正常可见 | Agent 收的就是想让人看的 |
| 个人早报 | 订阅外经兴趣池可入选(现状);**不计入多源印证** | 解 C4 |
| 公共日报 | 默认不进候选池,管理员可按源勾入 | 显式准入,不依赖白名单是否配置 |
| 榜单「必读」/ 事件 publisher 计数 | 不计入独立信源数 | 解 C4 |
| 分析 / 外发 AI | 开;`kind_hint` 只来自服务端规则,不来自调用方 `content_type` | 解 C7 |
| 媒体预取 | 与抓取入库同路径 | — |
| 播客 | 允许建 `provisional` 播客源(满足入库围栏),只到元数据与简介初评;ASR / TTS 等付费阶段要求 ≥ incubating | 解 B2;成本闸不放松 |
| Archive Sync | 作为源目录行随 sources 流同步 → 内网有名字、有头像 | 解 C9 |
| 运维面 | 「收录源」列表:条目数、最近推送、调用方;行操作 列入 / 下架 / 恢复 / 改名(合并随波 3;不设绑定采集) | 补盲区 |

### 5.3 推送通道

- **端点**:新增 `POST /api/ingest/v1/items`(批量 ≤ 100),取代两个旧入口作为 Agent 的正式路径;旧入口保留给管理面手工录入,但统一走同一套校验(或标记弃用,§7 D5 一并定)。
- **鉴权**:带作用域的收录令牌(新前缀,如 `ding_`,与 `dsub_` 同法只存 hash),绑定一个 actor 名;作用域只放行推送端点;日额度、单批上限;**每次调用入审计**(创建条数、新建临时源、拒绝原因)。管理面签发 / 吊销。内网节点直接 409,Agent 只推外网 authority。
- **载荷**:`kind`(article / social_post / podcast_episode)+ 出处描述 + 内容字段 + `note`(收录理由,可选)。
  - 出处描述三选一:`{source_id}`(必须已存在且接受推送)/ `{platform, handle, user_id?, name?, avatar?}` / `{url | feed_url | site, name?}`。
  - `content_type` 由服务端按 `kind` 指定,调用方不可写;extensions 按 `kind` 走**键白名单**,系统键(社区信号、翻译回执、抽取方式、来路)一律剥离。
- **身份解析**(认出已有源,解 C5):
  1. 显式 `source_id` → 校验存在、非保留(`user_rss_*`、`generic_*` 模板、`dorami_daily_brief`、墓碑 id)、非权威托管;
  2. 身份键命中 → 用已有源:X 按 `x:user_id` 优先、`x:handle`(小写)次之(预设 `x_moonshot_ai` 登记 `Kimi_Moonshot`);网页按 `feed` 规范 URL、再按「主机 + 路径前缀」匹配已有源的 `base_url`(`anthropic.com/news/*` → `web_anthropic_news`);
  3. 都不中 → 建 `provisional` 源:X 记 `x_<handle>`(撞名加后缀),网页记 `web_<host>`;**多租户主机**(github.com、medium.com、youtube.com、arxiv.org 等;substack 这类一作者一子域的按主机即可)按「主机 + 首段路径」或作者建源,名单入代码常量。
- **条目 id**:服务端按对应抓取能力的**同一公式**生成(X `{sid}_{post_id}`;网页 `{sid}_{sha1(规范 URL)[:16]}`),于是 Agent 先推、抓取器后抓(或反之)天然幂等合流。调用方给的 id 一律不用。
- **跨源 URL 去重**:插入前按规范 URL 查已有条目;命中他源 → 不新建,返回 `duplicate_url` + 已有 id,并(若 D4 选是)写一条收录记录。
- **逐条结果**:`created | duplicate | duplicate_url | refreshed | rejected(reason)`,取代现在「ID 已存在」一种报错。
- **收录视图**(可选):有了 `ingest_channel` / `article_picks`,读者面可加一个「收录」筛选或合集式入口——这正是 `source_collections.py` 文首写下的触发条件「出现非代码路径的策展者」。是否做、放哪里留到波 1 样页再定。

### 5.4 取缔「一个源 = 一个类」

> 已由 [源模型重构方案](./issue-262-source-model-refactor.md) §2.2 细化:对注册表做反射盘点后为 A 30 / B 15 / C 17;A 档的模板参数缺口只有 `generic_rss` 的 `feed_content_as_markdown` 一个(GitHub 已有 `include_prereleases`);B 档推荐保留为站点适配器,不收成声明式参数。下表保留为 R1 评估时的粗分。

62 个具体源按「需不需要代码」分三档(子代理粗分,迁移时逐个复核):

| 档 | 数量 | 例子 | 去向 |
|---|---|---|---|
| A 纯数据 | ~29 | 16 个 RSS(如 `rss_simonwillison`)、3 个 GitHub Releases、3 个 GitHub 仓库 / HF 模型、7 个 X | 目录文件条目 + 通用模板;先补两个参数(`feed_content_as_markdown`、`default_include_prereleases`),X 先修 B4 |
| B 接近数据 | ~15 | Cursor 翻页、MiniMax URL 正则、DeepMind 详情、Reddit 清洗 | `generic_web` / `generic_rss` 增补「正文选择器 / 清洗正则 / 翻页」参数后收为条目;收不了的留作小适配器 |
| C 需要代码 | ~17 | OpenAI(Cloudflare)、HN(Algolia)、Aiera(WP REST)、Qwen / Kimi(内嵌 JSON)、7 个单页拆分类、GitHub Trending | 改为**无身份站点适配器**:类不再声明 `source_id` / 名称 / 策展维度,只声明能力与参数表;目录文件条目绑定它 |

**目录文件**(待建,建议 `src/sources/catalog.toml`,Python 3.12 自带 `tomllib`,零依赖):每个策展源一条,含身份、呈现、策展维度、准入、身份键、采集绑定;同时收编 `PODCAST_CATALOG`、`SOURCE_COLLECTIONS`、默认订阅名单、墓碑名单——它们今天散在 4 个代码常量里。

**对账**:沿播客目录与 taxonomy 的先例,启动时在 authority 上把目录文件对账进源目录表:目录拥有的字段(身份 / 呈现 / 策展 / 绑定)以文件为准,运维拥有的字段(`is_active`、隐藏、调度)保留库内值;replica 不对账,经 sources 流接收(顺带解 C9)。

**守卫**:原来钉在 `tests/test_rss_fetcher.py` 的准入快照,改为目录文件的结构校验(id 唯一、身份键唯一、绑定的能力存在、参数通过该能力的 schema)。每个从类迁成条目的源,用「旧类 vs 新能力 + 参数」在固定样本上逐条对拍(发现 URL 集合一致、正文逐字节一致)——`generic_web` 当初就是这样验证的。

**为什么不直接全进数据库**:策展源的增删改今天靠 PR 审阅,`git` 即审计、开发机与新部署开箱一致、测试能钉住;纯数据库要另建导出 / 导入、审计与环境一致性。目录文件保住这些,同时让运行时只有一种模型。非策展来源(管理员 / 读者 / Agent)本来就只在库里。

## 6. 分步路径

| 波 | 内容 | 依赖 | 面 |
|---|---|---|---|
| **0 缺陷修复** | B1–B12:社交导入默认值与 X 身份对齐、播客入库报错分流、注入 id 校验与保留前缀、X 配置源身份、内网 409、隐藏名单覆盖日报 / feed、前后端角色兜底统一、事件归并形态兜底、审计、两入口测试、文档数字 | 无(B7 取值随 D8) | 纯后端 + 一处前端判定;可走「检视 + CI 绿直接合入」口径 |
| **1 推送最小闭环** | 源目录加列 + `provisional`;身份键表;来路字段;推送端点 + 收录令牌 + 审计;身份解析;服务端 id 与跨源 URL 去重;§5.2 各环节策略;运维面「收录源」列表与 列入 / 下架 | 波 0;D1–D5 | 后端 + 管理面 + 读者面来源呈现(需样页) |
| **2a 镜像行** | 启动时为 62 个代码源各写一行源目录(`origin=catalog`,属性取自类属性)→ 不变量「每源一行」成立,删除所有「查不到即 X」兜底 | 波 1 的表结构 | 纯后端;同步契约加字段 |
| **2b 目录文件** | 身份与策展从类属性迁进 `catalog.toml`;收编播客目录、合集、默认订阅、墓碑;转正改为改目录文件 | 2a;D6 / D7 | 纯后端 + 文档 |
| **2c 能力化** | A 档、B 档类收为「通用模板 + 参数」条目;C 档改为无身份适配器;逐源对拍 | 2b | 纯后端,分批 |
| **3 合并与去重** | 别名 / 重定向表(合并临时源到已有源不改写全表);全通道(含抓取)跨源 URL 去重;收录过的出处要定期跟踪 → 在目录文件里新增策展源(可沿用同 id,不是给临时源绑采集) | 2c | 后端 + 管理面 |

波 2(2a–2c)已由 [源模型重构方案](./issue-262-source-model-refactor.md) 的 R0–R4 取代;推荐顺序为 R0 → R1 → R4 → 本稿波 1 → R2 → R3。

波 1 可以先于波 2 落地:波 1 只要求「推送进来的源都有行」,代码源暂时仍走注册表,消费方在过渡期按「行优先、注册表兜底」读取;2a 之后去掉兜底。

## 7. 拍板项

| # | 问题 | 选项(推荐在前) |
|---|---|---|
| D1 | 临时源按什么粒度建 | **按出处建**(一账号 / 一站点一源,与「一账号一 source_id」一致)/ 按 Agent 建一个通道源 / 全部归一个「收录」源 |
| D2 | 临时源在读者面怎么出现 | **不进发现目录,条目进全站面 + 源页可单独订阅**,另议「收录」视图 / 与普通源一样进目录(现状) |
| D3 | 临时源能否进公共日报、计入多源印证 | **默认都不,管理员可按源勾入日报**;转为 incubating 后计入印证 / 一律放行 |
| D4 | Agent 收录了一篇已存在的内容 | **记一条收录记录,不重复入库** / 忽略 |
| D5 | Agent 怎么鉴权;旧入口怎么办 | **新做带作用域的收录令牌;旧入口保留给手工录入并统一校验** / 先用专用 admin 账号走 Cookie(零开发,权限过大) |
| D6 | 源目录数据化做到哪 | **目录文件 + 数据库运行时(2a → 2b → 2c)** / 只做 2a 镜像行,类仍定义源 / 纯数据库 + 管理面 CRUD |
| D7 | 策展源「转正」的事实来源 | **改目录文件走 PR**(管理面对 catalog 源只读,隐藏除外)/ 管理面操作入审计 |
| D8 | 波 0 角色兜底统一成什么 | **统一为 media**(与后端「权威加成只授予身份可考的策展源」一致;波 1 再引入显式 `unvetted`)/ 波 0 就新增「未登记」角色 |

## 8. 风险与不做

- **同步契约**:sources 流加字段、`articles` 加来路列都是对外契约变化(`docs/contracts/archive_sync.md`);新旧版本混跑时接收端要能忽略未知键。2a 让代码源也成为同步行,内网 `master` 若有自有源须先在 `INTRANET_DELTA.md` 盘点。(已由重构方案 §3.4 / §6.1 细化:内网在拉取请求里声明能力、外网按请求投影;波 1 的推送开关须在内网升级后再开。)
- **抓取稳定性**:2c 是 62 个线上节点的搬迁,必须逐源对拍、分批、可回退;C 档不追求消灭代码。
- **身份解析会误判**:多租户主机名单不全、同一出处多个 handle。缓解:解析结果写进推送回执与运维面,合并操作可逆(波 3 别名表)。
- **「无源」不是选项**:曾考虑让条目可以没有 `source_id`,但订阅、未读、feed、隐藏、配额、同步全按 `source_id` 组织,代价远大于给它建一行临时源。不做。
- **不做**:Agent 写入 MCP 工具(先 HTTP 端点,稳定后再评估);临时源自动按条目数「晋升」进目录(阈值不透明,一律人工列入);付费 ASR 对临时源放开。

## 9. 盘点出处

四路只读盘点(2026-11-05)的原始报告未入库;本稿引用的 `file:line` 均可在基线 `454f90da` 上复核。高严重度项 B1 / B2 / B3 / B7、C2 / C3 / C4 / C6 与 §2.1 数字由主代理回源核实;B4 为静态阅读结论,波 0 先写失败用例证明再修。

## 10. 实施记录(波 0 + 波 1)

**拍板(2026-11-06)**:D1–D8 按推荐——D1 按出处建临时源;D2 不进发现目录、源页可单独订阅;D3 默认不进公共日报、不计入多源印证,「列入」后参与;D4 已存在的内容只记收录记录;D5 带作用域的收录令牌,旧入口保留并统一校验;D6 目录文件 + 数据库运行时(按重构方案 R0–R3);D7 改目录文件走 PR;D8 角色兜底统一为媒体。

**波 0(B1–B12)**:社交导入按作者身份认回已有源、去掉 `import_social_posts` 默认值;存储层 `save_with_outcome` 报告确切结果、同 id 被他源占用不改写(`id_conflict`);X 模板在 `_run` 内切换实例身份;接收端社交导入 409;隐藏源排除于 `/api/feed/articles[.md]` 与公共日报候选池;前端未登记源角色归媒体;事件归并对未登记源按 content_type 多数判形态;社交导入入审计、手工录入审计记来源;`extensions_json` 接受对象、坏 JSON 回执带 `warnings`(既有「整体忽略」契约保留);两个入口补测试;文档数字。B3 的落法相对 §4.1 收窄:手工录入的调用方 id 原样保留(既有契约,`tests/test_manual_entry_extensions.py` 钉住),跨源覆写由存储层闸拦下,不给 id 时才由服务端生成。

**波 1**:

- 推送端点 `POST /api/public/ingest/v1/items`(放在 `/api/public/*` 下,沿用令牌鉴权、免会话的既有门控);契约全文 [`docs/contracts/ingest_push.md`](../contracts/ingest_push.md)。
- 收录令牌 `ding_`(HMAC hash、actor、日额度);管理面「运维 → 自定源与收录 → 收录」签发 / 吊销,收录源列表与 列入 / 下架 / 恢复 / 改名;推送与管理操作都入审计(推送记在 `ingest:<actor>` 名下)。临时源生命周期到此为止,不设绑定采集(用户 2026-11-06 拍板,见 §5.2)。
- 身份解析:显式 id → X 身份键(`user_id` 优先)→ feed 规范地址 → 主机 + 路径前缀;都不中建临时源(`origin=ingest`、`admission_status=provisional`),多租户主机按「主机 + 首段路径」。身份键表 `source_identity_keys` 由 R1c 迁移建出。
- 条目 id 与抓取器同一公式;跨源 URL 去重返回 `duplicate_url` 并写收录记录 `article_picks`;条目来路 `articles.ingest_channel / ingest_actor`(push / manual / import;迁移 `e2a9c4f7b183`,不随同步导出)。
- 临时源的下游策略:发现目录与批量订阅不列(`listing=unlisted`,源栏「收录」组);早报重大事件多源印证、榜单「必读」来源数、事件榜发布主体数不计入;公共日报候选池默认排除;播客全文处理(付费 ASR)自动入队排除,读者点播随之挡住。
- 没做:`kind_hint` 来自服务端规则——推送的 `content_type` 由 `kind` 决定(`web_article` / `social_post` / `podcast_episode`),调用方不可写,等价于此;「收录」读者视图(§5.3 可选)与别名合并(波 3)留待后续。

