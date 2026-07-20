# X 社交波方案(v3.12 · 进行中)

> 目标:把 X(Twitter)AI 圈的白名单账号接成**一等采集源**,并在阅读器「动态」容器里
> 给出区别于文章列表的**社交卡片流**形态。
> 立项 2026-07-20。前置 = 图床波(v3.11,推文配图靠它落地)已完成。
> 协作:Codex 负责 X 打通与后端;Claude 负责前端设计、子任务委派与验收。

## 0. 路线结论(Codex 于 2026-07-20 审计,已验证最小真实请求)

**主路径 = X API v2 官方按量付费 + 应用级 Bearer Token + 用户时间线增量轮询。**

推翻了仓库此前「先建 Cookie 账号池」的前提:X 已改为无固定月费的按量模式,
少量白名单账号成本可控。不存在同时满足免费/无人值守/长期稳定/合规的抓取方案。

| 路径 | 判断 |
|---|---|
| `GET /2/users/{id}/tweets` | **主路径** |
| Filtered Stream | 近实时,但断线补漏复杂,二阶段再议 |
| 官方嵌入时间线 | 只能显示,不能归档/检索/自有卡片 —— 不满足本项目 |
| RSSHub / Nitter / Playwright+Cookie | 依赖真实账号与风控,**不作生产路径** |
| TwitterAPI.io 等第三方 | 便宜但属非官方数据链,仅保留应急适配器位 |

凭据:`DORAMI_X_BEARER_TOKEN` 环境变量 / ini,**不入库、不进 Git、不打日志**
(比照 `LLMConfig.api_key` 的处理)。

## 1. V0 账号名单(2026-07-20 审计后裁决)

审计基线 = `docs/sources/curation_policy.md` 选择规则 + 日报 map 阶段的读者兴趣画像。
Codex 拟的 15 人清单经审计后:**删 2、降 2、增 5**,V0 先开 8 个跑观察期。

### V0 首批(立项 8 个;**2026-07-21 压缩成本后现存 6 个**)

| source_id | 账号 | 入选理由 |
|---|---|---|
| ~~`x_ai_at_meta`~~ | ~~@AIatMeta~~ | ~~归档零覆盖~~ **已下线(2026-07-21)**:低频(0.57 条/天)、增量小,删之省成本而丢的信号有限;Meta AI 重回零覆盖(backlog「M:Meta AI 源」)。 |
| `x_deepseek_ai` | @deepseek_ai | 发布首发常在 X,官网/中文源滞后 |
| `x_alibaba_qwen` | @Alibaba_Qwen | 同上,且发布节奏极快 |
| `x_moonshot_ai` | @Kimi_Moonshot | **归档零覆盖**,K2 系列已在开源第一梯队 |
| ~~`x_openrouter`~~ | ~~@openrouter~~ | ~~事件哨兵~~ **已下线(2026-07-21)**:高频(~4–5 条/天)是月成本大头,压缩成本优先删;交叉验证价值让位于预算。 |
| `x_karpathy` | @karpathy | 单账号信噪比最高 |
| `x_sama` | @sama | OpenAI 战略与发布口风的唯一一手来源,常先于官方公告 |
| `x_openai` | @OpenAI | 对照组:与 `rss_openai_news` 高度重叠,用来量化「X 比 blog 早多久」 |

选择偏向**归档零覆盖**与**哨兵型**,先验证社交容器本身的形态价值,
再补与文章流高度重叠的官方号。

**成本压缩决策(2026-07-21)**:据试抓密度估算,8 源每日增量月成本约 $9–13、
超 $5 预算(单条顶层推均摊全成本 ≈ $0.019,详见 §5)。用户拍板删
`x_ai_at_meta`(低频、删之丢信号少)+ `x_openrouter`(高频、成本大头),
现存 6 源月成本估降至约 **$6–8**。两源类已删、归档推文留库(登记进
`DECOMMISSIONED_FETCHER_IDS` 避免回流订阅目录)。

### 第二批候补(观察期后按数据决定)

@AnthropicAI · @GoogleDeepMind · @xai · @fchollet · @DrJimFan ·
@lmarena_ai(评测维度空白) · @OpenAIDevs / @claudeai(开发者/产品面) ·
@simonw(新模型第一手上手测评) · @MiniMax__AI · @teortaxesTex(观察期)

### 明确不收

- **@cohere** —— to-B 企业向,与读者画像相关度最低;此前三轮源扩容 curation 都未选它,那是判断不是遗漏。
- **@AndrewYNg** —— X 上以课程推广/The Batch 转发为主,营销属性重;日报 map 阶段本就 penalize 软广,与其下游罚分不如上游不进。要其观点走 Newsletter 通道更干净。
- **@elonmusk** —— Grok 首发确实常在此,但噪声会淹没整个社交容器;@xai 官号足以兜住发布事件。
- **@MistralAI / @huggingface** —— 降级至候补:RSS 已全覆盖且推文增量低/噪声高。

### 重叠率备案

V0 之外的原清单官方号与既有源高度重叠(`rss_openai_news`/`web_anthropic_news`/
`rss_deepmind_blog`/`rss_mistral_news`/`docs_xai_release_notes`/`rss_hf_blog`…)。
重叠**不等于无价值**——X 的增量在时效(常早于 blog 数小时)、形态(thread/demo 视频)、
非公告内容(口风、团队互动)。但由此产生一条待办:

> **跨容器去重**:同一次发布 = 一条推 + 一篇 blog。首版不做,记入 backlog;
> 观察期用 `x_openai` 对照组量化重复率后再定策略(候选:日报 dedup_clusters 复用)。

## 2. 契约(前后端共同遵守)

### 2.1 一账号一 source_id

`x_<handle_snake>`。**不得**把整条 X 时间线合并成单一 source。
理由:订阅粒度、阅读器源栏、发现页、feed/MCP 交付范围**全部**建立在 `source_id` 上;
合并后读者无法逐账号取舍,现成的四带式阅读器与发现页也全部失效。

### 2.2 双路径:preset 类(策展名单)+ config 源(管理面自助)

与 RSS 现状**完全同构**(`GenericRssFetcher` 模板 + 23 个 `PresetRssFetcher` 内置源并存):

| 路径 | 用途 | 载体 |
|---|---|---|
| **preset 类** | V0 八个策展账号 —— 代码即记录,与 curation_policy 表格对齐 | `PresetXTimelineFetcher` 子类,硬编码 handle |
| **config 源** | 管理面自助加账号,不改代码不部署 | `SourceConfigRecord`(`source_type=x` → 路由到 `generic_x_timeline` 模板) |

> 2026-07-20 修订:初版只写了 preset 一条路,用户目检时指出「加一个账号就要改代码 + 部署」
> 不合理。合并成单一 source 会撞上 §2.1 的 `source_id` 粒度约束(读者没法只订阅
> @karpathy 而不订阅 @sama),因此走双路径 —— 两边都保住一账号一 `source_id`。

类属性要求(两条路径都适用):
- `content_type = "social_post"`
- **`content_shape = "social"`** ← 关键,见 §2.2.1
- `category = "incubating"`(preset)← 全批走观察期(见 curation_policy)
- `icon` / `name` / `description` 按各账号填

### 2.2.1 社交独立成第三容器(2026-07-20 用户目检后新增)

阅读器的容器轴从 `article | bulletin` 扩为 **`article | bulletin | social`**。

理由:「动态」(bulletin)装的是 changelog / release notes / GitHub trending
—— **短条目扫读**形态;推文是**卡片流直读**形态。二者渲染差异大到需要在容器内
再分叉,那就说明它们本不该是同一个容器。

全链路涉及:fetcher 类属性 → `registry` 透出 `shape` → `api/sources.py:source_shape()`
→ `GET /api/articles?shape=` 过滤 → 阅读器 `mode` / 视图轨 / 源栏分组 → 发现页形态过滤。
注意 `source_shape()` 目前从 registry meta 取,**config 源不在 registry 里**,其 shape
需要另有来源(由 `SourceConfigRecord` 提供)。`BULLETIN_CONTENT_TYPES` 那套
「注册表之外的历史归档源按 content_type 兜底」的逻辑里,`social_post` 兜底为 `social`。

### 2.3 字段映射:X API v2 → `SocialPostContent`

`models/content.py` 的 `SocialPostContent` 字段已完整对齐,**无需改模型**:

| SocialPostContent | X API 来源 |
|---|---|
| `platform` | 固定 `"x"` |
| `post_id` / `conversation_id` | `data[].id` / `conversation_id` |
| `author_id` / `author_handle` / `author_name` | `includes.users[]` |
| `in_reply_to_id` / `quoted_post_id` / `reposted_post_id` | `referenced_tweets[]` 按 type 分派 |
| `lang` | `lang` |
| `media_urls` | `includes.media[]` 的 `url` / `preview_image_url` |
| `metrics` | `public_metrics` |
| `tags` | `entities.hashtags[]` |
| `raw_data` | 原始 JSON(保留,便于后续补字段不重抓) |

`BaseContent` 侧:`content` = 推文纯文本(**`note_tweet.text` 优先**,兜底 `text`);
`url` = `https://x.com/{handle}/status/{post_id}`;`publish_date` = `created_at`;
`title` = 正文首行截断(台账/feed 需要非空标题,卡片流不展示它)。

幂等键沿用导入桥语义:`source_id + post_id`。

### 2.4 图片走媒体库,但需要扩一处提取器

推文图存在 `extensions_json.media_urls`,**不塞进 `content` 的 markdown**
——推文正文就是文本,把图伪造成正文会污染向量化/日报/feed 导出,违反归档忠实性。

代价:`services/media_store.py` 的 `extract_image_urls()` 目前只扫 content 的
markdown/html 图链,推文图不会被随文预取。**本波需扩展它同时读
`extensions_json.media_urls`**(成本很小,`prefetch_articles` 接口不变)。
前端卡片的图片一律经 `mediaProxyUrl()`,与 `ReaderMarkdown` 同样的三层降级。

### 2.5 抓取策略

- **`exclude=replies`,保留 retweets 与 quote**。
  reply 是对话碎片,脱离上下文不可读且占量最大,排除它省配额最多;
  retweet 是账号的主动背书(官方号 RT 研究员 demo 是有效信号),
  quote 本身即原创推文,天然保留。
  *已知取舍*:X API 把 self-thread 的第 2..n 条也算 reply,故长 thread 会被截断成首条。
  首版接受(首条通常含核心信息与配图);观察期若发现 thread 丢失严重,
  再用 `conversation_id` 单独补齐。
- **`since_id` 增量**,游标存 `SourceStateRecord`(该表本就是权威 cursor 存储)。
- **配额守卫**(硬要求,$5 上限):月度 post 读取计数持久化到 `AppSettingRecord` KV,
  到顶即停抓并在运维面板可见;单次 `max_results` 有上限;失败退避。
- 请求字段按 Codex 已验证的 `tweet.fields` / `expansions` 组合。

### 2.6 隔离纪律

- 全批 `incubating` → **不进每日采集 job**(`ensure_daily_collection_job.py` 已排除该类别),先手动/观察运行。
- **不进日报名单**(`daily_brief_source_ids` 保持不含 `x_*`)。社交内容碎片化、营销密度高,
  直接灌进日报 map 会拉低报告质量;观察期数据说话后再逐个准入。

## 3. 前端:社交卡片流(Claude 负责)

样页:`docs/design/dorami-social-quiet.html`(v2,2026-07-20 目检后修订)。
社交流需要**宽流卡片**:占据条目列 + 阅读窗整幅(`grid-column: 3 / -1`,
发现页 `DiscoverPage` 已开此先例),单列、全文直出、最大行长约束。

卡片要素:头像 · 展示名 · @handle · **时间戳(即原推链接)** · 正文(长推折叠) ·
图片网格(1/2/3/4 张四种布局) · **引用推嵌套卡** · 转推归属行 · 收藏 / 标读。

### 七条设计决策(样页即论据)

1. **社交独立成容器** —— 见 §2.2.1。
2. **占全幅、单列宽卡、全文直出**。推文平均 2~4 行,四带式的阅读窗会空旷得荒谬;
   点一下才看到全文更是把「刷」变成了「翻」。
3. **头像用既有 LogoMark 品牌语言**(官方号)/ handle 首字母色块(个人号),
   **不引外链 avatar** —— 全站源标语言一致,且避免为头像再开一条媒体链路。
4. **不展示点赞/转发/评论数**。理由不是取数成本(`public_metrics` 随请求返回、
   不额外计费),而是诚实:抓取时刻的数字是**永久快照**,三天后仍显示当时数值
   等于主动展示错误信息;要更新就得重抓、烧配额,且违背「归档即快照」。
   **字段照常入库**(`metrics` + `raw_data` 都留),只是不展示。Folo 亦如此。
5. **时间戳即原推链接**,不设独立外链按钮。初版右上角的 ↗ 按钮被用户误读成
   「分享」—— 语义没传达到即设计失败。腾出的右上角改放悬停浮出的收藏 / 标读,
   那是本系统对 X 仅有的两个真实动作(只读权限,把指标做成按钮是不诚实的仿真)。
6. **转推 = 顶部归属行 + 原作者卡;引用推 = 内缩嵌套卡**。二者形态必须可区分,
   否则读者判断不了「这话是谁说的」。
7. **平台是「源」的属性,不是每条内容的属性**(2026-07-20 用户提出,为日后接
   Mastodon / Bluesky 预留)。三层落法:
   - **源栏分组标签带平台前缀**(`X · 官方` / `X · 个人`)—— 始终显示,零成本可扩展;
   - **发现页源卡标平台** —— 始终显示,订阅前就该知道源来自哪个平台;
   - **卡片头像角标仅当订阅了 ≥2 个平台时出现** —— 单平台时每卡挂同一图标是纯噪声
     (违反「不做无信息量的重复」),多平台混流时它才是「这话在哪儿说的」的关键。
     渐进披露,实现上只是一个 `platformCount > 1` 的布尔。

   数据侧已就位:`SocialPostContent.platform` 字段存在;`source_id` 命名带平台前缀
   (`x_karpathy` → 将来 `mastodon_xxx`),§2.1 定粒度时即已承接扩展。
   **待补**:`GET /api/reader/sources` 需要透出 `platform`,前端才能分组与判断平台数。

密度定**舒适档**(单一档,不做密度切换设置项)。

### 已知取舍:社交流不计入阅读量

`POST /api/reader/articles/{id}/read` 记的是「刻意打开」,而卡片流全文直出、
没有 open 动作。社交流因此**只维护未读/已读态,不计入 reads 指标** ——
把滚动流灌进该指标会污染运维看板的用户活跃判断。

纪律:全部走 `frontend/src/index.css` 的 `--dorami-*` / `--r-*` / `--motion-*`
token 与角色类,不手写 px/hex(见 `docs/frontend/conventions.md`)。

## 3.5 管理面的社交源管理

- **看**:X 源与其它源同构地出现在节点管理 / 源健康 / 运行历史(preset 走 registry,
  config 源走 `/api/source-configs`),无需为社交单开一套管理界面。
- **加**:管理面填一条 `SourceConfigRecord`(`source_type=x` + `params.handle`)即可,
  不改代码不部署 —— 见 §2.2。
- **配额可见**:`GET /api/x-api/quota` 透出本月资源计数与估算成本,运维面板可读。

## 4. 分工与验收

| 方 | 范围 |
|---|---|
| **Codex** | X API 打通、`XTimelineFetcher` + 8 个 preset、增量游标、配额守卫、字段映射入库、`extract_image_urls` 扩展、后端测试 |
| **Claude** | 社交卡片样页与前端实现、子任务委派、全波验收(lint/build/pytest/目检)、文档更新 |

验收门槛:`pytest tests/` 全绿 · `npm run lint` 干净 · `npm run build` 通过 ·
Alembic 无 drift(若动模型)· 真实 backend 端到端目检。

## 4.5 实施记录(2026-07-20)

**后端(Codex,三轮)**
1. X API 打通 + `XTimelineFetcher` + 8 preset + `since_id` 增量 + 配额守卫 + 字段映射
   + `extract_image_urls` 扩展。8 源真实跑通共 **39 条入库**,`since_id` 复跑 0 新增(增量有效)。
   配额按**返回资源**计费(非请求次数):Post/Media/Note $0.005、User $0.010,UTC 日内去重;
   首轮实耗 **$0.73 / $5**。实地发现 **@OpenRouterAI 已不存在**,订正为 `@openrouter`(`source_id` 不变)。
2. shape 三态全链路 + config 路径(`source_type=x|x_timeline` → `generic_x_timeline`)。
   **挖出真坑**:原 `article = NOT bulletin` 的二分会把 social 误归 article,已改互斥三态。
3. `quoted`/`reposted` 扁平化进 extensions(跨平台抽象层)+ 转推作者契约(注释+测试锁定)
   + `GET /api/reader/sources` 透出 `platform`。

**前端(Claude)**
- 新增 `components/SocialFlow.jsx`(整幅卡片流)、`utils/readerTime.js`(日期分组与条目列共用,
  从 ReaderTab 抽出);`sourceTaxonomy.js` 加 `platformLabelOf`;`index.css` 加 `.reader-social-*`/
  `.social-*` 组件层;`ReaderTab.jsx` 接入第四个视图轨容器 + `shapeOfSource` 三态 +
  社交源栏「平台 · 分层」分组 + `toggleArticleRead` 通用化(阅读窗与社交流共用)。
- 相对时刻复用既有 `utils/datetime.js:formatRelativeTime`(语义已一致),未重复实现。

**第四轮(用户目检六条反馈)**
- 后端:user_id/头像缓存(`x_api_user_cache:<source_id>`)、`author_avatar_url(_large)`
  (`_normal` → `_400x400`)、`/api/reader/sources` 透出 `avatar_url`、
  X API 配置三端点(脱敏 + `field_sources` 来源标注 + 最省钱探针)、`max_results` 默认 25、
  quota 加 `by_source`。
- 前端:运维管理→内容页的 X API 配置卡 + 六格用量条(含配置来源徽章、探针花费如实转述);
  `SocialAvatar` 四级降级头像(卡片/引用卡/源栏/发现页);发现页独立「社交 · 账号」分组
  + 形态过滤加「社交」+ 源卡标平台;卡间距 16/15 → 22/20。
- **一处误判的更正**:我曾断言「preset 每次抓取都解析 handle→user_id,占开销 31%」——
  错的。8 个 preset 已硬编码 user_id,`_resolve_user` 有 id 即返回、不发 `by/username`;
  23 次 User 读取来自 `includes.users`(顶层 + 引用/转推作者),是渲染引用推卡的必需开销。
  根因:我 grep 时模式里没有 `user_id`,把「没看到」当成了「不存在」。
  user_id 缓存的真实价值在**只有 handle 的 config 源**。

**验证**:`pytest tests/` **391 passed** · `npm run lint` 干净 · `npm run build` 通过 ·
真实后端确认 8 个 X 源 `shape=social`/`platform=x`、39 条社交文章(15 条含图)、
配置端点脱敏正确(`bearer_token_set`/`field_sources`)。

**未端到端目检**:引用推/转推卡 —— 现有 39 条是扁平化补齐**之前**抓的,老记录无 `quoted`/`reposted` 键;
该形态目前只有单测覆盖。补法 = 重置某源游标重抓(幂等更新会回填这两个键),成本约 $0.035。

## 4.6 目检修订轮(2026-07-21)

浏览器目检后的三条 + 一处白屏修复:

- **白屏(P0)**:给 `EDITORIAL_GROUPS` 加 `social` 组后,`subscribedGroups`/发现页的
  `buckets` 缺 `social` 键 → `.map` 出 `list:undefined` → `.filter(g=>g.list.length)`
  崩溃整个 ReaderTab。修:两处 `buckets[g.key] || []` 兜底。
  **教训**:`lint + build 通过 ≠ 运行时不崩`(`undefined.length` 是纯运行时)。此后改用
  **Playwright 无头渲染真实页面 + 捕获 pageerror/console-error** 验收前端,不再只靠 build。
- **取缔「今日」容器**(用户拍板,默认落「文章」):今日把三个渲染形态不同的宇宙混进
  一条文章样式的流,违反容器模型前提(也正是「X 源在今日显示成文章」的现象根因);
  且不可中栏筛选、时间轴语义(抓取/发布)不明。删后 `mode` 三态 `article|bulletin|social`,
  `mixedFlow`/逐条形态 chip 一并消失,各容器默认倒序 + 未读体系已覆盖「最近/未看」。
- **隐藏「通用 XXX」源**:`GET /api/reader/sources` 跳过 `is_template` 模板源
  (`generic_*` 是 source-config/builder 的执行基座,非可订阅来源;前端节点目录早已按
  此过滤,读者目录同步)。
- **社交容器收藏筛选**:`SocialFlow` 头部加收藏 toggle(复用 `.reader-fav-icon`);
  `list_favorites` 早已支持 `shape=social`,`loadArticles` 的 favOnly 分支补上
  `includeContent`(社交收藏也走卡片流,需 extensions)。

Playwright 端到端验证:15/15 头像真实加载、6 引用推 + 13 转推卡渲染、发现页无通用源、
收藏筛选 15→1→15、视图轨仅三容器且默认落文章、全程 `pageerror=0`。

## 5. 观察期出口(两周后按数据决定)

- 各账号发帖频率 / 有效条占比 / 与文章流重复率(`x_openai` 对照组)
- thread 截断的实际发生率
- 月度配额消耗速率 vs $5 上限
- 据此:转正 / 淘汰 / 开第二批 / 决定是否做跨容器去重
