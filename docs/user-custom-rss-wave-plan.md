# 用户自定 RSS 源波：读者自助添加消息来源(设计方案)

> 状态:已实现(v3.40.0,2026-08-28;设计三轮讨论收敛当日落地。实现按 §8 顺序全量
> 完成:迁移 d9de7994582c/服务层 services/user_sources.py/四 reader 端点/隔离面五落点/
> 调度 user_rss_refresh/admin 端点+运维「用户自定源」区/读者前端[发现页添加入口+
> AddCustomSourceModal+自定源第五分组+移除路径统一+阅读原文尾链];tests/
> test_user_sources.py 26 项,全套 643 项绿;Playwright 冒烟+真网 feed 端到端
> [hnrss 添加→首抓 12 篇→移除级联清零]验证)。
> 先例:Folo 自助添加 feed。
> 缘起:读者想看的源不在策展目录里时,此前唯一通路是反馈→admin 编码 preset→部署,
> 周期以「波」计。本波给读者一条自助通路:贴 RSS URL 即成为自己的私有源。
> 三处前置拍板(对话中已定):**方案 B 自助+隔离**(不做全自助 Folo 式,不做申请制);
> **最简正文策略**(feed 给什么存什么,不做全文/摘要分型与详情补抓 fallback);
> **入库存储**(否决「临时抓取仅前端显示」——全系统能力建立在 ArticleRecord 上,
> 且实测文本成本可忽略:现库 4147 篇均 3.3KB,用户源年增量估算 <10MB/人)。

## 0. 定调(复杂度阀门,全案前提)

**用户源是读者的私有订阅资产,不是归档策展资产。**

- 技术底座全部现成:`GenericRssFetcher`(generic_rss)参数驱动抓取 +
  `SourceConfigRecord` 配置源实体 + 读者目录已覆盖零产出配置源
  (`reader.py:_configured_source_meta`)。本波不写新 fetcher、不动抓取管线。
- **隔离铁律**(方案 B 的全部内涵,四处收口):
  ① 不进公共发现页目录(仅 owner 与订阅者可见);
  ② 不进 `resolve_all_visible_source_ids`(问答 scope=all、admin MCP 令牌、
     IM bot 检索域全部由此派生,一处减除全链生效);
  ③ 不进日报(allowlist 手工名单天然隔离,另加 `user_rss_` 前缀机械排除双保险);
  ④ 不进 archive sync export(内网 reader 不吃用户源)。
- **质量口径 = 如实呈现**:feed 自带什么正文就存什么(用户源固定
  `fetch_detail_if_missing=false`),不做站点级清剿、不做详情补抓、不做全文/摘要
  分型判定。所有用户源条目正文尾部一律附「阅读原文 ↗」链接(不分型,全文源不碍事,
  摘要源自然可用)。私有隔离让降档质量标准在产品上站得住:唯一受影响者是添加者
  本人,且 preview 环节所见即所得。
- **删除语义与策展档分开**:用户源不承担归档使命,移除后无人订阅即物理删除
  (含文章)。策展档「归档忠实、永不删」的口径不受影响。

## 1. 数据模型

### 1.1 `SourceConfigRecord` 加一列(Alembic 迁移)

```python
owner_username: str = Field(default="", index=True,
    description="用户自定源的创建者;空=平台源(admin 管理)")
```

- 空串=既有平台配置源,行为零变化;非空=用户源,进隔离面。
- drift 测试(`test_migrations`)要求模型改动配套迁移,SQLite batch 模式。

### 1.2 source_id 与去重共享

- `source_id = "user_rss_" + sha256(canonical_url)[:12]`。规范化:scheme/host
  小写、去默认端口、去 fragment、去尾斜杠(保守规范化,不动 query)。
- **同 URL 天然去重**:第二人添加同一 feed 时 source_id 相同→配置行已存在→
  添加动作退化为「订阅既有用户源」,一份抓取多人共享。`owner_username` 记首建者,
  仅作身份标记与审计,**不承担权限差异**(见 §2 删除语义)。
- 哈希截断碰撞防御:建行时 source_id 已存在但 `canonical_url` 不同(48bit 截断
  相撞,概率可忽略但不为零)→ 改用全长哈希重建 id。
- 与系统源的 URL 冲突经撞库检测处理,见 §3.1。
- 配置行固定值:`source_type="rss"`(→ `resolve_source_fetcher_id` 路由
  generic_rss)、`params_json` 固定 `{"fetch_detail_if_missing": false, "limit": 12}`、
  `name` 默认取 feed title(用户可改)、`category="user"`。

## 2. API(`/api/reader/custom-sources`,reader 前缀天然门控)

- **`POST /preview`** body `{url}`:守门三连——`ensure_public_host`(SSRF,复用
  media_store 判定含 fake-ip 豁免)→ httpx 拉取(大小/超时上限)→ feedparser 解析。
  解析不出任何条目→ 400 拒绝。返回 `{feed_title, canonical_url, entries:
  [{title, publish_date, content_chars} × ≤5], existing: {source_id, subscribed}?}`。
  **preview 只守门不分型**(拍板:避免阈值取值/误判/补抓质量三类新问题)。
- **`POST /`** body `{url, name?}`:重跑守门→**系统源撞库检测**(§3.1,preview
  同步返回检测结果供前端提前引导)→幂等建/复用配置行→为本人建
  `ReaderSubscriptionRecord`(复用单源订阅逻辑,含 `init_cursor_with_backlog`)→
  提交一次立即抓取(复用 `run_single_fetch_as_collection`,后台 job)→
  返回 `{source, job_id}`。配额:**每用户存活用户源 ≤20**(400)、
  **单日新增 ≤10**(429,沿反馈/分享限额范式)。
- **`GET /`**:我的用户源列表(配置 + `SourceStateRecord` 健康摘要:status/
  consecutive_failures/last_success_at,前端「暂不可用」细签数据源)。
- **`DELETE /{source_id}`**:语义=「移除这个源」。实现:退订本人→查该源剩余活跃
  订阅者→**无人订阅则物理删除**:删配置行 + 删该源全部文章(FTS 由 trigger 同步,
  收藏/已读/分享记录按 article_id 级联清理;媒体缓存不删——content_hash 跨 URL
  共享,删除需查引用,量小不值得,留给未来统一 GC)→有其他订阅者则仅退订,
  配置与文章留存(owner 不转移,字段只是史实)。
  用户源不在日报名单,删除无需回退日报游标。
- admin 治理面:用户源配置行照常出现在 `/api/source-configs` 列表(admin 可停用/
  删除作止损),`source_visibility` 隐藏机制对用户源同样生效。

## 3. 隔离面落点(方案 B 的实现清单)

| 收口点 | 改动 |
|---|---|
| 读者目录 `GET /api/reader/sources` | `_configured_source_meta` 透出 owner;组装时用户源 entry 仅当 `owner==me ∨ subscribed` 保留,否则剔除 |
| 问答 all 档/admin MCP/IM bot | `resolve_all_visible_source_ids` 减去全部用户源 id(查 `owner_username != ""` 的配置行;单一咽喉,一处生效) |
| 日报 | allowlist 天然隔离 + `collect_candidates` 机械排除 `user_rss_` 前缀(双保险一行) |
| archive sync | `GET /api/archive/export/articles.jsonl` 排除用户源 source_id |
| 文章列表按 source_id 直查 | 轻门槛:请求 `user_rss_*` 源且非 owner/订阅者→空结果(防 source_id 泄露后翻库);文章详情按 id 不设防(id 不可枚举,单篇泄露风险接受) |
| 公开分享 | 允许(单篇只读、owner 主动动作,与普通文章同权;隐藏源不可分享的既有规则照旧) |

订阅域(`resolve_subscribed_source_ids`)零改动——用户源就是普通订阅,未读/收藏/
FTS 搜索/订阅域问答/feed 令牌/MCP(dfeed_)全部自动贯通,这正是入库方案的红利。

### 3.1 与系统源的冲突面(2026-08-28 补节)

| 冲突轴 | 判定 | 处理 |
|---|---|---|
| source_id 身份 | **结构性免疫**:`user_rss_` 是独占前缀命名空间,系统源无一使用 | 测试守卫:断言 registry 无源以 `user_rss_` 开头 |
| **feed URL 与系统源一致** | 唯一需要真机制的轴。不处理=平行双源/同文双份/列表与问答重复条目/双份抓取 | **撞库检测**(见下) |
| 名称一致 | 无需机制:name 非身份键;私有可见+「自定源」独立分组+管理面「自定」细签,同名可区分;仿冒无受害者(只有本人可见) | 不做唯一性约束、不做保留字名单 |
| 取名链 | **实现缺口(必修)**:`friendly_source_name` 解析链(registry→兜底表→原样返回)不查 config 表,用户源在问答上下文块头/引用出处显示裸 id | 加第三级兜底查 `SourceConfigRecord.name`;既有 X config 源同病,本波顺修 |
| content_type / source_role / web·x 类 config 源 | 无冲突:rss_article 无隔离语义诉求;source_role 只被日报消费而用户源不进日报;listing/handle URL 与 feed URL 形态不同天然不撞 | — |

**撞库检测**(preview 与 POST 均执行):系统 feed URL 集合 = registry 中 RSS
preset 的规范化 `feed_url`(类属性可枚举,rss_fetcher.py 30 处)∪
`SourceConfigRecord(source_type=rss)` 的 url。

- 命中**可见系统源** → 不建用户源,返回 `{existing: {source_id, name,
  subscribed}}`,前端引导「该来源已收录为『量子位』」+ 一键订阅——自助添加
  顺势成为发现页的第二入口。
- 命中**被隐藏系统源** → 拒绝添加,统一文案「该来源暂不可用」,不泄露隐藏细节
  (与隐藏源 404 口径一致;否则可造影子源绕过 admin 下架止损)。
- 漏检边界(接受):URL 形态变体(feedburner 镜像、`/feed` vs `/rss.xml`)
  规范化认不出 → 退回平行源,后果自限于私有重复归档一份。

## 4. 抓取调度与治理

- **新 APScheduler job `user_rss_refresh`**:每 60 分钟(KV 可配,见 §4.1)跑一遍
  `owner_username != "" ∧ is_active` 的 rss 配置,内部镜像 `fetch_active_rss_sources`
  的 items 组装 + `run_collection_items`,`trigger_type="scheduled"`。
  防重叠沿 remote_sync 模式(上轮 job 未终态则跳过本轮)。
- **健康自动停用**:`SourceStateRecord.consecutive_failures`(现成字段)≥10 →
  置 `is_active=false`(调度即不再抓),「我的源」列表标「已暂停(连续失败)」,
  用户可一键重启(re-activate 并由下次成功归零计数)。死源不静默腐烂、不无限重试。
- **SSRF 纵深**:添加时校验 + 调度组装 items 时对 feed_url 复检(域名解析结果会变)。
  redirect 落点/DNS rebinding 与 source_builder 同水位,不加深(内部部署姿态)。
- **媒体豁免**:`schedule_media_prefetch` 对 `user_rss_` 前缀文章跳过预取——
  实测媒体是存储大头(258MB vs 主库 125MB)。图仍走 `/api/media/proxy` 首次打开
  按需缓存,没人看的文章零媒体成本。

### 4.1 配置分档(2026-08-28 拍板,沿项目既有二分范式)

| 配置项 | 落位 | 前端可配 |
|---|---|---|
| 功能总闸 `user_sources_enabled` | KV,默认开 | ✅ 运维管理→内容 |
| 刷新间隔(默认 60 分钟) | KV | ✅ 同一张卡 |
| 源数上限 20 / 单日新增 10 / 停用阈值 10 | 代码常量 | ❌ |

- **总闸**沿 `public_share_enabled` 即时熔断语义:关闭=添加/preview 端点 403 +
  调度跳过,**已有源与文章数据不动**,重开即回归;`/api/runtime` 透出能力位,
  前端自动隐藏添加入口。
- **刷新间隔**保存即 `reload_user_rss_schedule()` 热生效(沿 remote_sync/
  daily_brief 范式);读写并入 `GET/POST /api/admin/user-sources/config`。
- **三个护栏数值**沿 `DAILY_SHARE_LIMIT=30`/反馈 10 条日限范式:防滥用护栏不是
  运营旋钮,做成可配即「死配置」(v3.15.2 教训),常量带 rationale 注释即可。

## 5. 管理面表现(admin 视角)

**自动获得(零改动,入库方案红利)**:抓取写 `SourceStateRecord` → 节点管理
信号条/健康灯自动覆盖(信号条角色筛选轴加第五档「**自定**」,防稀释策展源健康
信号);文章自动进知识台账(admin 全档,源名挂「自定」细签);`user_rss_refresh`
轮次写 `CollectionJobRunRecord` → 任务运行历史自动可见;运维→用户抽屉订阅数
自动含用户源。

**新建核心面——运维管理 → 内容 →「用户自定源」区**(与媒体库/X 配额/公开分享
总闸并列,同属内容出入口的治理与观测):

- 写入口:总闸 ledger-switch + 刷新间隔输入(§4.1 两项)。
- 观测 KPI 行:用户源总数 / 覆盖读者数 / 累计文章数 / 失败中源数。
- 源列表:名称 / feed URL / 创建者 / 订阅人数 / 健康灯 / 文章数 / 最近成功;
  行动作 = 停用/启用、在读者面隐藏(复用 source_visibility 止损)、删除
  (与读者删除共用同一条 service 路径,确认框如实列出受影响订阅者数)。
  **admin 强删有订阅者的源须补级联**:清全部订阅行+未读水位(读者路径是
  「无人订阅才删」碰不到这支),防悬空订阅行。
- 端点:`GET /api/admin/user-sources`(列表+KPI)+ `GET/POST .../config` +
  `POST .../{id}/toggle` + `DELETE .../{id}`。`/api/admin/*` 前缀 → admin 操作
  **自动入审计**(前缀已在 `AUDIT_PATH_PREFIXES`)。
- 读者自助添加/删除**不入审计**(沿 reader-surface 豁免拍板);溯源靠配置行
  自带 `owner_username`/`created_at` + 本列表。
- **节点管理呈现(v3.40.1 需求变更,取代初版「不放进节点管理」拍板)**:用户目检
  提出「筛选自定源应看到刚触发的源」——source-health 以 fetcher-like 形状并入
  用户源行,节点管理「自定源」角色档下渲染简化节点行+专属简化检视器(feed 身份/
  健康事实/读者面隐藏开关/治理回指);手工抓取走 source-configs 通道(继承
  ssrf_guard/响应上限护栏),批量通道仍排除用户源。停用/删除治理仍收敛运维→内容。

## 6. 前端(读者面)

- **添加入口**:发现页头部「＋ 添加源」钮(桌面/移动同位;读者面与 admin 阅读器
  界面均可见)→ modal 两步:贴 URL → preview 条目样例(标题/日期/正文长度)+
  可改名 + 「我的自定源 n/20」余量 → 确认即订阅并触发首抓。
- **呈现**:源栏与发现页给用户源独立分组「**自定源**」(前端按 `user_rss_` 前缀
  判定)——不塞进官方/媒体/个人/榜单角色轴,不污染 `sourceRoleOf` 语义。
  源条目挂健康细签(「暂不可用」复用既有灰显语法)。
- **管理动作**:不做独立管理页。源条目右键菜单/移动长按单加「移除自定源」
  (替代普通退订项,文案如实说明「无其他订阅者时将删除其文章」)与「重新启用」;
  数据走 §2 的 GET/DELETE。
- **正文尾部「阅读原文 ↗」**:用户源条目渲染统一追加(既有外链渲染范式)。

## 7. 测试

`tests/test_user_sources.py`:preview 守门(非 feed 400/SSRF 拒绝)、URL 规范化与
去重共享(两人同 URL 一份配置双份订阅)、配额(源数 400/单日 429)、目录可见性隔离
(第三人不可见/订阅者可见)、`resolve_all_visible_source_ids` 排除、archive export
排除、日报候选排除、删除级联(独占删文清 FTS/共享仅退订)、调度 items 组装与
`is_active` 过滤、连续失败自动停用与重启、媒体预取豁免。
`test_migrations` drift 覆盖 owner 列。管理面补:总闸熔断(403+调度跳过+数据
留存)、admin 端点门控与审计落行、KPI 聚合、admin 删除与读者删除同路径、
admin 强删级联清订阅行。冲突面补(§3.1):`user_rss_` 前缀 registry 守卫、
撞库命中可见源转引导/命中隐藏源拒绝、哈希碰撞全长重建、
`friendly_source_name` config 兜底(含 X config 源顺修)。

## 8. 实施顺序

① 迁移+模型(owner 列)→ ② services/user_sources.py(规范化/守门/CRUD/配额/
总闸)+ 四个 reader 端点 → ③ 隔离面五落点 → ④ 调度 job+健康停用+媒体豁免+
间隔热生效 → ⑤ admin 端点+运维「用户自定源」区+信号条「自定」档 →
⑥ 读者前端(添加 modal/自定源分组/右键项/阅读原文尾链)→ ⑦ 测试补齐。
版本 v3.40.0,一波一提交。

## 9. 已拍板与遗留

**已拍板**(2026-08-28 对话):方案 B 隔离形态;最简正文(不分型不补抓,preview
仅守门);入库存储(媒体不预取);删源即清(无订阅者时物理删);配置分档
(总闸+刷新间隔 KV 可配,三护栏数值代码常量);admin 治理面收敛在
运维管理→内容(节点管理另有健康呈现,v3.40.1 需求变更见 §5);冲突面(§3.1:
URL 撞系统源做撞库检测转引导,名称不设唯一性,取名链补 config 兜底)。

### 9.1 安全收口(codex gpt-5.6-sol 交叉检视返修,2026-08-28)

首版隔离面漏了若干**非阅读器入口**的交付/订阅面,经 codex 检视逐条修复:

- **F1 交付面**:`/api/feed/articles[.md]`(无归属主体上下文)一律排除用户源,
  显式指定也不放行;空 filters 的 dsub 订阅=全库语义,交付减用户源
  (`query_subscription_articles`);公开 FTS 端点撤销 `or None` 放大(空范围=
  零订阅返回空,与 MCP 契约一致)。收口原语 `exclude_user_sources_condition`。
- **F2 成员资格**:用户源准入凭证=feed URL(custom-sources 通道),source_id 不是
  ——facets 对非 admin 剔除用户源 id;一键订阅与 REST 订阅 create/update 对
  「非本人已订阅」的用户源 404(`unauthorized_user_source_ids`)。
- **F3 动作级授权**:分享签发/AI 翻译/速读/问答显式名单对非订阅者的用户源文章
  404(`_deny_unsubscribed_user_source_article`);favorites 列表(内容出口)同滤。
  按 id 直达的只读详情维持豁免(id 不可枚举,拍板不变)。
- **F4/F5 网络护栏**:用户源 params_json 固定携带 `ssrf_guard`+`max_response_bytes`,
  generic_rss 执行层抓取前 SSRF 复检+流式响应限量(首抓/调度/手工全通道);调度
  组装另做复检双保险;preview 改流式(Content-Length 预拒+逐块限量)。
  redirect hop/连接 peer 级校验按既有拍板(与 source_builder 同水位)记 backlog。
- **F6 止损不可绕过**:隐藏用户源拒绝重新添加;停用来源判别不加列——
  `consecutive_failures>=阈值`=自动停抓可经再添加复活并清计数,低于阈值=admin
  手动停用,复活须走运维面。
- **F7 竞态**:建源/移除/强删/孤儿 GC 以进程内写锁串行化(单进程部署前提,
  多 worker 时升级为成员表+DB 约束,记 backlog);IntegrityError 幂等兜底。
- **F8 生命周期**:调度轮尾孤儿 GC(无活跃订阅者即 purge,订阅者以 users 表
  存在性校验,覆盖 REST 退订/删号等一切绕行路径);unsubscribe 端点对用户源
  后端分流移除;purge 顺清分享记录;source-configs 通用删除分流强删。
- **F9 滥用面**:日增配额改 KV 事件计数(不随删除回退);已订阅者重复 POST
  幂等返回不再触发抓取。
- **F10 分离部署**:非 collector runtime 下添加/preview 403(reader split 不出网)。
- **F11 配置原子**:admin config 端点先整体验证再写 KV。
- **F12**:节点管理呈现属需求变更,方案 §5 已同步(codex 撤回该项)。

全站正式抓取的统一响应上限(策展源共通)另记 backlog——全站级决策不随本波。

**明确不做,留观察后二期**:用户源转正进策展档(admin 借 source_builder 生成
CrawlProfile 后收编——「自助」反哺策展的漏斗);详情补抓/全文化;OPML 批量导入;
用户源媒体缓存 GC。
