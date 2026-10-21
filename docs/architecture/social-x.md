# 社交媒体流(X 社交波)

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:归档 [`archive/social-x-wave-plan.md`](../archive/social-x-wave-plan.md) · [`archive/social-translate-plan.md`](../archive/social-translate-plan.md)

## 社交媒体流(X 社交波,v3.12)

阅读器的**第三容器**。`content_shape` 曾为 **`article | bulletin | social`**(issue #178 起 bulletin 并入 article,现为 `article | social | podcast`) —— 「动态」装的是 changelog/release notes/GitHub trending(短条目扫读形态),推文是卡片流直读形态,渲染差异大到要在容器内再分叉,就说明本不该是同一个容器(2026-07-20 用户目检后拍板)。全链路:fetcher 类属性 `content_shape` → `registry` → `api/sources.py:source_shape()`(三态互斥,`social_post` 兜底为 `social`;注意**不可再按「非 bulletin 即 article」二分**,那会把社交源误归文章容器)→ `GET /api/articles?shape=` → 阅读器 `mode`/视图轨/源栏分组。

采集走 **X API v2 官方按量付费**(`GET /2/users/{id}/tweets` + 应用级 Bearer,凭据 `DORAMI_X_BEARER_TOKEN`,不入库/不进 Git/不打日志)。`src/fetchers/impl/x_timeline_fetcher.py` = `XTimelineFetcher`(`is_template=True` 模板,参数驱动 `handle`/`user_id`)+ 8 个 `PresetXTimelineFetcher` 子类(V0 策展名单,全批 `incubating`)。**双路径与 RSS 完全同构**(`GenericRssFetcher` + 23 个 preset 并存):preset 类 = 策展名单(代码即记录),`SourceConfigRecord`(`source_type=x|x_timeline` → 路由到 `generic_x_timeline`)= 管理面自助加账号、不改代码不部署。**一账号一 `source_id`**(`x_karpathy`,平台前缀天然承接 `mastodon_*` 扩展)—— 订阅粒度/源栏/发现页/feed 交付范围全建立在 `source_id` 上,合并成单一源会让读者无法逐账号取舍。抓取策略:`exclude=replies` 保留 retweets/quote(reply 是对话碎片且占量最大;retweet 是账号的主动背书)、`since_id` 增量(游标存 `SourceStateRecord`)、**配额守卫**(`src/services/x_api_quota.py`:按**返回资源**计费而非请求次数 —— Post/Media/Note $0.005、User $0.010,UTC 日内去重;月度累计存 `AppSettingRecord` KV `x_api_usage:YYYY-MM`,预算不足最小请求时**发出请求前即停**;`GET /api/x-api/quota` 可读)。

## 凭据与开销的管理面

(v3.27 起分两半:**凭据编辑**收敛到 设置柜 → 凭据[见 [外部凭据统一保管层](accounts-and-access.md)],运维管理 → **内容**页保留**配额与开销观测面**[KPI 条+区头凭据 chip 回指],与媒体库并列——外部资源的开销,X 不是 AI 故不放 AI 页):`GET`/`POST /api/x-api/config` + `POST /api/x-api/config/test` 与 LLM 配置同构(`services/x_api_config.py`;token **只写不回显**,只返回 `bearer_token_set` + 掩码尾四位,响应与日志均无明文;`field_sources` 逐字段标出配置来源 `runtime_kv|env|ini|default`,因为凭据可能来自环境变量、此时运行时值不生效)。连通性自测走**最省钱探针**:优先重读已有游标对应的 Post,命中当日去重则 `$0`,否则约 $0.005;响应带 `estimated_cost_usd`/`deduplicated_today`,前端如实转述花费。`_resolve_user` 的 user_id 缓存(`AppSettingRecord` 键 `x_api_user_cache:<source_id>`)**主要服务只有 handle 的 config 源** —— 8 个 preset 已硬编码 user_id,本就不发 `by/username`(2026-07-20 一处误判的更正:实测 23 次 User 读取来自时间线响应的 `includes.users`,即顶层作者 + 引用/转推作者,那是渲染引用推卡的必需开销,省不掉)。**`max_results` 默认 25**:未使用的槽位不收费,调大只增加当次实际取回的内容、不增加参数本身成本,故它降低积压漏抓风险而不涨开销(残余边界:单次积压超 25 且不翻页仍可能漏)。

## 作者头像

`extensions` 带 `author_avatar_url` / `author_avatar_url_large`(X 默认 `_normal` 是 48px,后端派生 `_400x400`),`quoted`/`reposted` 内嵌结构同样带;`GET /api/reader/sources` 透出 `avatar_url`(取自 user 缓存),源栏/发现页的社交源据此用真实头像。前端 `SocialAvatar` 四级降级:媒体库代理 → 原链直连 → 源的 `LogoMark` → handle 首字母色块;转推时头像跟**原作者**走,与名字/handle 同主体。(v3.12 初版曾定「不引外链 avatar、一律 LogoMark」,但社交源在品牌表里没有条目,结果所有账号退化成同一个 X 图标 —— 既不是品牌也不是头像;图床 v3.11 落地后代理链路现成,故改用真实头像。)

## 跨平台抽象层

引用推/转推的可渲染内容**扁平化进 `extensions`**(`quoted` / `reposted` 各含 `author_name`/`author_handle`/`text`/`url`/`media_urls`;无则不写该键),前端不去挖平台原始 JSON —— 将来接 Mastodon/Bluesky 时 `raw_data` 形状完全不同,但 quoted/reposted 语义通用。**转推契约**(注释+测试锁定):顶层 `author_*` = 时间线账号(转推者),`reposted.author_*` = 原作者;顶层 `content` 是 X 的 `RT @xxx: …` 截断形式,**展示须用 `reposted.text`**(完整正文,`note_tweet` 优先)。推文图存 `extensions_json.media_urls`、**不塞进正文 markdown**(推文正文就是文本,把图伪造成正文会污染向量化/日报/feed 导出),`media_store.extract_image_urls()` 已扩展为同时读该键,故随文预取照常。

前端 `SocialFlow.jsx` 占「条目列 + 阅读窗」整幅(`grid-column: 3/-1`,与发现页同构)、单列宽卡、全文直出。**不展示点赞/转发/评论数** —— 不是取数成本(`public_metrics` 随请求返回、不额外计费),而是诚实:抓取时刻的数字是永久快照,三天后仍显示当时数值等于主动展示错误信息;**字段照常入库,只是不展示**。时间戳即原推链接(不另设外链按钮),右上角悬停浮出**收藏 / 标读** —— 只读权限下仅有的两个真实动作。**平台是「源」的属性、不是每条内容的属性**:源栏按「平台 · 分层」分组、发现页源卡标平台(始终),而卡片头像角标**仅当订阅了 ≥2 个平台时挂载**(单平台时每卡同一图标是纯噪声);`platform` 由 `GET /api/reader/sources` 透出。**社交流不计入 reads 指标**(该指标语义是「刻意打开」,而卡片流全文直出、没有 open 动作;灌进去会污染运维看板的活跃判断),只维护未读/已读态。「今日」混合流仍包含社交条目,以列表紧凑形 + 「社交」chip 呈现。方案与名单裁决见 `docs/archive/social-x-wave-plan.md`,样页 `docs/design/dorami-social-quiet.html`。
