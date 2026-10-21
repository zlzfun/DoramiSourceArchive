# 代码地图(带注释的目录树)

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Project Structure」原文迁出(2026-10-21)。目录树会随代码漂移,**以 `ls` / `git ls-files` 为准**;本文价值在逐文件注释里的设计意图。
> 相关:[`overview.md`](./overview.md) · 端点 [`reference/endpoints.md`](../reference/endpoints.md)

```
src/
├── main.py                  # Entry point: starts uvicorn with reload=True
├── config.py                # load_config() → settings singleton; reads DORAMI_CONFIG_FILE (else config/backend.ini)
├── api/
│   ├── app.py               # FastAPI app — 鉴权中间件/前缀门控表/include_router 注册 + APScheduler init(端点大多已拆入 routers/)
│   ├── routers/             # 按域拆分的 Router 群(articles/reader/admin/archive_sync/…); v3.18 新增 feedback.py / announcements.py / remote_sync.py; v3.25 新增 share.py(公开分享只读端点); vector.py 已随 v3.31 退役清仓删除
│   └── skill_router.py      # GET /api/skill/daily-brief: zips src/skill_templates/dorami-daily-brief on the fly
├── llm/
│   ├── client.py            # OpenAI-compatible chat_completion + parse_json_object + ping (httpx; never logs api_key); optional UsageMeta + set_usage_recorder surface token usage without changing the str return
│   └── prompts.py           # Daily-brief map/reduce + reader-AI translate/QA (哆啦美 persona) prompt templates
├── services/
│   ├── daily_brief.py       # Daily-brief map-reduce orchestration + same-event dedup_clusters + paper_cap + cursor dedup + in-memory progress; usage attributed to triggered_by (manual admin) else "system"
│   ├── reader_ai.py         # 用户面 AI Beta: translate_article (cached zh translation) + answer_question (multi-turn QA); shares resolve_llm_config; 核心层「文章名单→编号上下文+sources」(v3.32,[n] 即引用锚) + assemble_reader_context 范围四档(article/articles 显式名单,subscription/all 经 search_fetch 闭包注入)
│   ├── reader_search.py     # 订阅域问答检索(v3.30 检索扶正波): LLM 规划(中英关键词+日期窗+意图分流)→ FTS5 召回 → LLM 选篇 → 全文注入; 降级链自持; 日报即索引
│   ├── source_naming.py     # 来源取名与角色运行时单点(v3.31 自 vector_storage 迁出): friendly_source_name + SOURCE_FRIENDLY_NAMES 兜底表; v3.35 增 source_role(前端 sourceRoleOf 的后端镜像,scope 集合两侧须逐字同步,日报权威机械层消费)
│   ├── accounts.py          # Account CRUD + PBKDF2 + last-active-admin guard (v3.19 多管理员:可建/提升/降级 admin,末位活跃管理员不可降级/停用/删除) + seed_root_admin_if_empty(空库首启自动生成 admin/admin) + default_surface 偏好; login/AI-usage 埋点 (touch_login writes LoginEventRecord + last_login_at; record_ai_usage) + login aggregation (logins_by_user/summarize_user_logins) + AI Beta global master switch
│   ├── admin_audit.py       # 管理操作审计(v3.19): should_audit 前缀×方法判定 + record_audit(语义摘要注册表,吞异常不阻断) — 挂在 require_admin_session 中间件尾部
│   ├── ai_usage.py          # AI token metering: record_usage upserts AiUsageRecord per (day×user×purpose×model); summarize / usage_by_user / summarize_user → admin dashboards
│   ├── reader_activity.py   # 阅读计量: record_read upserts ReaderReadRecord per (day×user×source_id); reads_by_user / summarize_user_reads → admin user-OPS board
│   ├── article_share.py     # 文章分享(v3.25): 公开只读链接签发/撤销/解析 + 总闸 public_share_enabled + 单日限额; 失效原因一律不区分(过期/撤销/隐藏源/不存在 → 同一个 404)
│   ├── credentials.py       # 外部凭据统一保管层(v3.26): CredentialNamespace 声明式注册(LLM/X API)+ resolve/field_sources/save/sanitize 四能力 + mask_tail/apply_secret_update 共享助手; 自签发令牌与账号密码不属此层
│   ├── x_api_config.py      # X API 凭据/参数配置(保管契约委托 credentials 层)+ user_id/头像缓存
│   ├── x_api_quota.py       # X API 配额守卫:按返回资源计费(Post/Media/Note $0.005、User $0.010,UTC 日去重),月度累计存 KV,预算不足即停
│   ├── media_store.py       # 媒体库(图床): extract_image_urls + MediaStore(get_or_fetch/prefetch/stats) — URL 哈希寻址、内容哈希去重落盘、SSRF/魔数/大小防护、失败负缓存
│   ├── source_builder.py    # AI node onboarding: URL → detect type + signals + (LLM) config + detail-profile → preview (frontend gated off; backend only)
│   └── remote_sync.py       # 远程内容同步(v3.18): 登录远端(Set-Cookie 显式回传)→ 分页拉 archive export → 本地幂等导入; KV 增量游标; 手动同步凭据绝不落库(v3.19.2 例外:定时同步凭据存 KV remote_sync:schedule,只写不回显)
├── models/
│   ├── content.py           # Dataclass content models (BaseContent + subtypes)
│   └── db.py                # SQLModel ORM tables: ArticleRecord,
│                            #   FetchRunRecord, SourceStateRecord, SourceConfigRecord,
│                            #   CollectionJobRecord, CollectionJobRunRecord,
│                            #   ReaderSubscriptionRecord, ReaderFeedTokenRecord, AppSettingRecord,
│                            #   UserRecord (+ai_beta_enabled/default_surface/last_login_at/ai_*_count 列), AiUsageRecord, ReaderReadRecord, LoginEventRecord,
│                            #   AdminAuditRecord (v3.19 管理操作审计),
│                            #   JobRecord (阶段3 持久化后台任务状态机), MediaAssetRecord (图床缓存登记),
│                            #   FeedbackRecord + AnnouncementRecord + AnnouncementDismissRecord (v3.18 互通波),
│                            #   ArticleShareRecord (v3.25 分享波:公开只读链接,令牌明文存——与 dsub_/dfeed_ 的有意口径差异)
├── fetchers/
│   ├── base.py              # BaseFetcher: httpx client, retries, template method
│   ├── registry.py          # FetcherRegistry singleton — auto-discovers impl/ on import
│   └── impl/
│       ├── rss_fetcher.py               # GenericRssFetcher + PresetRssFetcher (23+ built-in RSS sources); OpenAINewsRssFetcher renders detail via Playwright (CF bypass); HackerNewsAiRssFetcher applies a configurable min_points/min_comments hnrss threshold to de-noise the q=AI firehose and is treated as a discovery source (external-link posts degrade to title+URL+discussion+heat with no body; only Ask/Show/Tell self-posts keep a body; external detail fetch off by default)
│       ├── github_release_fetcher.py    # GenericGitHubReleasesFetcher + preset subclasses (13 built-in)
│       ├── repository_model_fetcher.py  # GitHub repo + HuggingFace model fetchers (content_type=github_repository / huggingface_model); GitHub repo fetcher backfills a cleaned README excerpt when a repo has no description (dedup-gated, GITHUB_TOKEN-aware)
│       ├── webpage_fetcher.py           # BaseWebPageListFetcher + preset subclasses (6 built-in)
│       ├── curated_core_fetcher.py      # Curated AI-source presets: SinglePageDocumentFetcher (changelogs/release notes) + per-site BaseWebPageListFetcher/BaseFetcher subclasses (量子位, 新智元, HF Daily Papers, etc.)
│       ├── configurable_web_fetcher.py  # ConfigurableWebFetcher (generic_web): single config-driven web fetcher; params carry listing/patterns/detail-profile/listing_css (frontend gated off; backend only)
│       ├── article_extractor.py         # Shared HTML→article-body extractor (helper module, not a fetcher); used by webpage/rss fetchers to backfill detail
│       ├── x_timeline_fetcher.py         # X 时间线:XTimelineFetcher(is_template 模板)+ 8 个 preset;since_id 增量、exclude=replies、quoted/reposted 扁平化
│       ├── playwright_renderer.py       # PlaywrightRenderer: headless-Chromium detail rendering for Cloudflare-challenged sources (used by OpenAINewsRssFetcher)
│       └── webhook_trigger.py           # Outbound Dify workflow trigger (not an inbound content source)
├── fetchers/web_content/    # Optional crawl4ai Web Content backend: backend.py (WebContentBackend ABC + DetailResult), legacy_backend.py (httpx baseline), crawl4ai_backend.py (browser, opt-in extra), profiles.py (per-site CrawlProfile), compare.py (bypass A/B)
├── mcp_server.py            # build_mcp_app(): FastMCP streamable-HTTP server, registered at /mcp and /mcp/ by app.py
├── pipeline/
│   ├── core.py              # DataPipeline: drives fetcher → broadcasts to registered storages
│   └── progress.py          # In-memory per-fetcher {current,total} run progress (polled by frontend)
└── storage/
    ├── base.py              # BaseStorage abstract class
    ├── fts.py               # SQLite FTS5 全文索引(articles_fts 建表 DDL + trigger 同步 + fts_search_ids 查询;运行期与迁移共享单一实现)
    └── impl/
        └── db_storage.py    # SQLite storage(create_all 引导 + FTS 建表)

frontend/src/
├── api.js                   # All fetch() calls to the backend (single source of truth)
├── sourceTaxonomy.js        # 来源谱系: LogoMark 品牌注册表 + LOGO_SIZES 刻度 + **信息角色单轴** (sourceRoleOf/SOURCE_ROLES + groupByRole — 官方/媒体/个人/榜单, 判定序 个人→榜单→媒体→官方; 阅读器源栏/发现页/管理面共用一套词汇; 形态交给容器+seg, 组头只表角色; **v3.25 起管理面也用这根轴**——原「公司→板块」两级谱系 groupBySection/SECTIONS 已退役, 公司仅作组内排序键)
├── App.jsx                  # Root: login gate + tab routing. admin 走左侧应用导轨 (64px, 与阅读器视图轨同形制 — 复用 .reader-vrail-* 「轨语言」类族, 图标 + 11px 微标签常显 [v3.45 可发现性波, tabs.short 两字短名, 全名留 tooltip]; 轨底 = 阅读器/主题/设置 常态可见轨钮 + RailLogoutAvatar 头像退出钮 [v3.45: hover 滑出的 RailUserFlyout 退役——2026-07-24 拍板反转]; 退出两击防呆 v3.22——首点仅入 4 秒待确认态[红圈脉动+Toast 提示], 再点才退, 超时/Esc/点别处回落); readerOnly 隐藏应用导轨 (整页即阅读器, ReaderTab standalone 轨底同形制). tabs 按 runtime capabilities × account_role 过滤
└── components/
    ├── LoginScreen.jsx      # Account login
    ├── AnnouncementBanner.jsx # 公告横幅 (v3.18): **Apple 系悬浮玻璃胶囊卡**——absolute 悬浮、整页水平居中(width min(560px,·)), 不占布局、关闭/隐藏零位移(用户拍板:布局推移显得视图不固定); 玻璃四要素 = surface **18%** color-mix 半透明底 + **blur(6px)** saturate(1.8)(blur 刻意小于底下小字字高——半径过大会把透出内容抹平成均匀色,"物理透明、视觉实心") + 顶部光泽渐变与 inset --dorami-edge 高光 + hairline 外缘/--sh-3 落影(亮暗自动翻转); **入场动效两段式**(Chrome 硬限制:元素或祖先带 active 动画时 backdrop-filter 一律不取样,最小复现锁死)——entering = 纯 transform 从屏幕顶缘滑入(无 opacity!opacity 会把实底打薄透字)+ 78% 实底 + 无 blur,JS 380ms 摘类落定 → blur 挂上 + 底色 transition 溶解回 18% 玻璃,全程无"清晰直透"帧; **彩色玻璃 tint**(用户拍板:按档位上彩色半透明底;左缘竖条有 AI 感已去除)——`--ann-tint` 变量逐档覆写(通知=muted 10%/强调=accent 19%/警示=warn 19%), 文字与图标取同档 token 色(accent-ink/state-warn 亮暗成对), 三档语义 = 图标形状+色+tint 三通道(Info/Megaphone/AlertTriangle); 大圆角 --r-overlay; **8s 自动隐藏**(淡出上移动效, hover/聚焦暂停计时按剩余续走; 隐藏≠dismiss——不落库, 下次会话再现); 正文走 utils/announcementText.jsx 的白名单渲染器(仅 **加粗** 与 http(s) 链接, 零依赖零注入——不用 ReaderMarkdown); AnnouncementCard 供管理面预览复用(所见即所得); dismiss 本地即时移除 + fire-and-forget 上报
    ├── ReaderTab.jsx        # 阅读器 (读者唯一主界面, 四带式: 视图轨56/源栏232/条目列372/阅读窗): **容器模型** mode=article(默认)|podcast|social(issue #178 撤「动态」)与单源 activeSourceId 共存(social 走 SocialFlow 整幅卡片流,不用条目列+阅读窗; 点源跳所属容器); 「今日」跨容器混合流已取缔(它以文章形态渲染推文, 违反容器模型前提——三宇宙渲染形态不同才需分容器; 各容器默认倒序 + 未读体系已覆盖「最近/未看」); **左栏=一根轴**(v3.52 兴趣即透镜波五稿:栏头=容器名 + 「订阅 | 兴趣」互斥二段,其下列已订阅源或关注标签,点行下钻;兴趣轴=全站;收藏是列头一枚星;轴+收藏按用户按容器记住;「全部XX / 只看收藏」两行聚合入口自此退役;列头回显 订阅/源名/兴趣/标签名;命中胶囊 / 订阅外就地订阅[屏蔽折叠行随 v3.56 退役]);收藏文章在条目卡右缘挂琥珀星标(已收藏常显、未收藏悬停浮出可点亮);**未读=标题字重与墨色**(未读 600 墨色、已读 400 次墨色;社交卡读过整卡文字降 muted;左侧小蓝点 2026-10 随 issue #199 撤——#184 给标题加回读态后它成了重复信号,在播客封面旁也局促);**搜索命中高亮**(`utils/highlight.jsx` 的 `highlightMatch`——条目列标题/摘要、社交流推文、发现页源名/简介共用,accent 淡底 `.search-hl`);源栏组头与源行**不显示未读数字**(减噪);未读体系 (全部/未读 seg + 标读) + 日期分组 sticky 组头 + 衬线阅读窗 (serif 标题/进度线/上一下一篇/日报报头); **无限滚动**(IntersectionObserver 哨兵,提前 400/500px 触发,骨架条占位随文追加,取代「加载更多」按钮); **浮层滚动条**(条目列/社交流:隐藏原生条使卡片左右满宽铺满、右缘不被 gutter 占位,改用 `hooks/useOverlayScrollbar.js` 自绘半透明滑块 `.ovl-thumb` 压在卡片上——Folo 式;macOS「始终显示」下纯 CSS 无法让原生条 overlay,Chrome 124+ 已移除 `overflow:overlay`;`scrollbar-width:none` 须钉未分层区,否则被全局 `* {scrollbar-width:thin}` 压回;时间/星标右移 8px 避开滑块,正文/摘要仍满宽可压滑块下); 视图轨含「发现」(Compass, discover 态);搜索=各容器内容头就地展开(列头/社交头,图标↔输入框顶替标题,已从视图轨降级); **no default article auto-open** (deliberate-read 计量: POST /api/reader/articles/{id}/read fire-and-forget); AI Beta 入口 (标题下「原文 | 译为中文」二段 [v3.45 自右上角图标下沉] + 问哆啦美 fab — 默认收缩圆钮, 悬停展开) 当 ai_beta_enabled && llm_configured; 视图轨 v3.45 起 64px 图标+微标签, 轨底 主题/反馈/设置 常显 + 头像两击退出
    ├── mobile/              # 移动壳 (移动波 v3.29, ≤767px matchMedia 首帧分流, lazy 独立 chunk): MobileReader (底部4-Tab 页面栈壳+条目流) + MobileArticlePage (push 全屏正文页) + MobileSourceDrawer (源过滤抽屉) + MobileMePage (承接桌面轨底工具钮职责+退出确认框) + ActionSheet (长按动作单=桌面右键菜单的 bottom-sheet 翻译, items 同源同组序); 数据层与桌面共用 hooks/useReaderState.js (Wave1 自 ReaderTab 抽取), 长按手势 hooks/useLongPress.js, 样式 mobile.css (全 .m-* 前缀, index.css @import); v3.29.1 收口: MobileSettings (设置柜的两级页面栈翻译, 分区组件与桌面同源) + hooks/useLayerHistory (层×浏览器返回键握手) + SocialFlow headless 化 + 长按单 Web Share 项 + 登录 ≤480 轻档
    ├── SocialFlow.jsx       # 社交媒体流 (含 SocialAvatar 四级降级头像) (第三容器 shape=social, v3.12): 占条目列+阅读窗整幅、单列宽卡、全文直出; 图片网格/引用推嵌套/转推归属行; 不展示互动数字(快照会过时); 时间戳即原推链接, 卡片悬停浮出单条收藏/标读; 头部 = 标题(常显于左,不随搜索隐藏)+ seg+标读+搜索(**收藏过滤钮已移至源栏容器级入口**,搜索在右侧 actions 组内展开、**紧邻开关钮**——社交头太宽满宽会失衡;「N 个账号 · M 未读」副标已撤,收藏筛选时标题仅「收藏」); 推文文本 `highlightMatch` 命中高亮; 初次加载 + 无限滚动追加均用推文卡骨架(SocialCardsSkeleton),取代 spinner/「加载更多」; 平台角标仅在 >=2 平台时挂
    ├── DiscoverPage.jsx     # 发现页 (参照 Folo, v3.10): 全站源目录占据 条目列+阅读窗 (grid-column 3/-1), 编辑分层分组 + 双列源卡 (简介/收录 meta/订阅描边钮,issue #184) + 形态/搜索过滤; 整卡点开=预览(跳该源条目列表), 未订阅时 ReaderTab 渲染源栏锚点行 + 条目列「＋订阅」横幅 (activeUnsubscribed)
    ├── AdminOpsTab.jsx     # 运维管理 (admin-only): Grafana 式看板 — KPI + AI Beta 总闸 & global model config + AI usage charts (recharts, time-range dropdown) + content board charts (含媒体库统计 + MediaHeatmap 逐日热点图) + reader-account management; calls /api/admin/* + /api/llm/config; v3.18 增第四子页「消息」(sub='engage': FeedbackInboxPanel 反馈收件箱 + AnnouncementsPanel 公告管理)
    │   ├── charts/         # DashboardCharts.jsx (token 化 recharts 包装件 + ThemedTooltip) + chartUtils.js (配色常量 + fillDailySeries 零填充)
    │   └── admin/          # adminUtils.js + MediaHeatmap.jsx (媒体逐日热点图 53×7 格阵 + 当日明细抽屉 + 单篇定点重抓;手写 CSS grid,不用图表库) + AdminAuditPanel.jsx (操作审计列表,v3.19)
    ├── DataTab.jsx          # 知识台账: article list, filters, CRUD; admin-facing (hidden for `user`); 总账条=三格收录量看板(v3.31 向量列/自动向量化开关随 RAG 退役移除)
    ├── FetchTab.jsx         # 节点管理: fetcher catalog/triggers (collector)
    ├── FetchRunsTab.jsx     # 任务与运行: scheduled tasks + fetch-run history (collector)
    ├── DailyBriefTab.jsx    # AI 日报页签 (原「接入集成」页签瘦身改名 v3.9; tab id 已更名 'brief', 历史书签 #/mcp 由 hashToRoute 归一兼容; gating collector): DailyBriefPanel + 只读模型 chip; 交付通道内容 (MCP/聚合接口/技能包) 已并入设置柜
    ├── DailyBriefPanel.jsx   # 每日 AI 资讯日报: config + manual generate + run history (admin-managed)
    ├── DailyBriefFlow.jsx    # Animated map-reduce stage visualization for the daily-brief generation progress
    ├── SettingsModal.jsx    # 设置柜 (880×576, v3.9 「接入集成并入设置」; v3.19.2 整备): 分组左导航 通用(账户/外观/反馈与建议 — 反馈仅读者账号)/接入集成(聚合接口/MCP 接入/Agent 技能包 — 两角色同享)/管理(数据同步/凭据 — v3.27 凭据整合台)/关于; wash 块选中 (轨语言); initialSection 深链。**内容门控按「当前界面」而非仅角色**(v3.19.2): App 下传 readerSurface, adminConsole = admin 账号 × 管理台界面 —— MCP 启停 switch/「管理员令牌拉走全库」文案/数据同步组/归档中枢产品名只在 adminConsole 出现, admin 切阅读器后设置柜与读者同观感。分区组件在 components/settings/ (FeedTokenSection/McpAccessSection — adminConsole 含 MCP 启停 switch + 向量统计行, 取数范围说明分管理版/读者版/SkillSection/AccountSection/AppearanceSection/DataSyncSection — v3.19.2 重设计为单列整幅实线卡(根治 datetime 截断), 含 RemoteSyncCard 远程同步块(测连/范围三选/进度轮询)与 RemoteSyncScheduleCard 定时同步卡(开关+只写不回显凭据+频率预设)/CredentialsSection — v3.27 凭据整合台:LLM/X 编辑卡+只读回指行,onNavigate 柜内跳数据同步/FeedbackSection — v3.18 读者反馈入口/AboutSection)
    ├── SecretField.jsx      # 统一凭据输入件(v3.26): 只写不回显契约的表单半边——掩码预览即占位(无预览时「已设置」), 空值起步 留空=保留/输入=覆盖, 语义挂 title; X/LLM/定时同步三处共用
    ├── ManualAddModal.jsx   # Manual article entry form
    ├── ArticleDetailModal.jsx
    ├── DateRangePicker.jsx
    └── Toast.jsx
```

**User layer is a reader, not a console**: A `user` (restricted reader) account gets exactly one full-page surface — the 阅读器 (`ReaderTab`, app rail hidden, its own 视图轨 is the only chrome). Everything non-reading lives in the **设置柜** (`SettingsModal`): account/appearance plus the whole 接入集成 group (dfeed_ aggregated feed token, MCP client config, Agent skill download). Subscription management is in-reader: the 源栏 is the subscription list (hover 减号 to unsubscribe), the 发现页 (视图轨 Compass) is the full-page catalog with one-click subscribe/preview. So **subscription = the user's reading list (what the reader shows) + the downstream feed/MCP delivery scope**. Admin's rail keeps 台账/节点/运行/雷达/AI 日报/运维 + the same 设置柜 (with 管理 group); since v3.19 an admin is also a reader — the rail-bottom hidden switch enters the same standalone 阅读器 (see [Admin dual-surface](accounts-and-access.md)). The `/api/subscriptions` REST lifecycle remains as an advanced/automation path with no dedicated UI.
