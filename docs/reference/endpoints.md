# 端点清单与门控语义

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Endpoints」原文迁出(2026-10-21)。**权威是运行中的 `GET /docs`(OpenAPI)与 `src/api/routers/`**;本表的价值是逐族的门控语义与设计注记,新增端点不必逐条登记,改动门控规则时才更新。
> 相关:[`architecture/overview.md`](../architecture/overview.md) · 契约 [`contracts/`](../contracts/)

**Articles**
- `GET /api/articles` — list/query (filters: `content_type`, `source_id`, `has_content`, `search`(FTS5 全文,标题+正文), `publish_date_start/end`, `fetched_date_start/end`, `subscribed_scope` = `off`|`only`|`prioritize`, `skip`, `limit`)
- `POST /api/articles` — manual entry
- `PUT /api/articles/{id}` — update
- `DELETE /api/articles/{id}` — delete(必要时回退日报游标)
- `POST /api/articles/batch-delete`

**Article analysis / Taxonomy / Personal briefs (v3.44)**
- `/api/admin/analysis/*` — analysis worker status, retry, release smoke and resumable historical backfill.
- `/api/admin/cms-tags*`, `/api/admin/taxonomy/*` — canonical catalog, aliases, Candidate review, target-DB receipt and manual publication.
- `/api/reader/interests/*` — onboarding catalog and followed-tag preferences (mute retired in v3.56).
- `/api/reader/briefs/*` — personal brief readiness, generation and immutable edition retrieval.

**Feed Delivery** (recommended contract for downstream LLM consumers)
- `GET /api/feed/articles` — delivery-shaped JSON with `metadata.extensions` parsed; supports `content_types` (CSV), `source_ids` (CSV), `has_content`, `include_content`; `limit` capped at 500
- `GET /api/feed/articles.md` — same filtered records as a Markdown batch; capped at 200 records

**Import Bridge**
- `POST /api/import/social-posts` — ingest external social posts (X/Twitter, etc.) as `social_post` content; idempotent by `source_id + post_id`

**Archive Sync** (collector → reader, see [Archive Sync](../architecture/archive-sync.md))
- `GET /api/archive/export/articles.jsonl` — collector exports archive records as JSON Lines
- `POST /api/archive/import/articles.jsonl` — reader imports them (admin-only; no public fetch)

**Daily Brief** (collector/admin-gated; surfaced in the 「AI 日报」 tab)
- `GET`/`POST /api/daily-brief/config` — read/set cron, top-N, LLM overrides (persisted in `AppSettingRecord` KV)
- `POST /api/daily-brief/generate` — submit a background digest job; returns `{status, job_id}` (poll `GET /api/jobs/{job_id}` for the result)
- `GET /api/daily-brief/runs` — recent run history; `GET /api/daily-brief/progress` — live in-memory generation progress; `GET /api/daily-brief/pipeline` — stage/pipeline view
- `GET /api/skill/daily-brief` — download the templated `dorami-daily-brief` Claude skill zip

**Collection Jobs** (the single scheduling entity; see [Collection Jobs](../architecture/jobs-and-scheduling.md))
- `GET/POST/PUT/DELETE /api/collection-jobs` — savable, schedulable multi-node job CRUD
- `POST /api/collection-jobs/{job_id}/run` — submit a background job (阶段3) that runs the collection job (still writes an aggregating `CollectionJobRunRecord`); returns `{status, job_id}`, poll `GET /api/jobs/{job_id}` for the aggregate (validation 404/400 stays synchronous)
- `GET /api/collection-job-runs` + `GET /api/collection-job-runs/{job_run_id}` — job-level run history
- `GET /api/fetch-runs/running-progress` — in-memory per-fetcher live progress
- (retired: `/api/node-groups*`, `/api/tasks*`, `/api/collection-jobs/migrate-legacy-tasks` — 实体简化阶段 2; existing data auto-migrated by Alembic `8f6d93196258`)

**Fetchers**
- `GET /api/fetchers` — list all discovered fetchers with parameter schemas
- `POST /api/fetch/{fetcher_id}` — trigger a specific fetcher (also writes `FetchRunRecord` and updates `SourceStateRecord`)

**Source Configs** (user-defined source definitions, advanced extension surface)
- `GET /api/source-configs` — list all source configs
- `POST /api/source-configs` — create a new source config
- `PUT /api/source-configs/{source_id}` — update a source config
- `POST /api/source-configs/{source_id}/toggle` — enable/disable a source
- `DELETE /api/source-configs/{source_id}` — delete a source config
- `POST /api/source-configs/{source_id}/fetch` — trigger fetch for a specific source config
- `POST /api/source-configs/fetch-active-rss` — trigger all active RSS source configs (阶段3: background job, returns `{status, job_id}`)
- `POST /api/source-configs/fetch-active-web` — trigger all active web/webpage source configs (via `generic_web`; 阶段3: background job, returns `{status, job_id}`)

**Source Builder** (AI node onboarding, collector-gated; frontend entry currently gated off — backend only)
- `POST /api/source-builder/analyze` — body `{url}`; detect page type + analyze + (LLM) propose a `SourceConfigCreate`-shaped node config (+ sample-article detail `CrawlProfile`)
- `POST /api/source-builder/preview` — body = proposed config; trial-run `generic_web`/`generic_rss` and return sample entries (no persist). Save via `POST /api/source-configs`.

**Monitoring & Observability**
- `GET /api/source-health` — per-fetcher health summary (derived from `SourceStateRecord`, falls back to `FetchRunRecord` aggregation); sorted by name; rows carry `admission_status`
- `GET /api/source-states` — raw `SourceStateRecord` rows (filterable by `status`, `fetcher_id`)
- `GET /api/fetch-runs` — paginated fetch run history
- `GET /api/fetch-runs/{run_id}` — single run detail

**Reader Subscriptions & Personal Feed** (reader surface)
- `GET /api/reader/sources` — subscribable source catalog (registry ∪ archived ∪ subscribed; enriched name/description/icon; `subscribed` flag)
- `POST`/`DELETE /api/reader/sources/{source_id}/subscribe` — one-click subscribe / unsubscribe (per-user)
- `POST /api/reader/articles/{article_id}/read` — record one deliberate article open (reader-gated, fire-and-forget; bumps `ReaderReadRecord` by the article's source)
- `GET/POST/PUT/DELETE /api/subscriptions` + `POST /api/subscriptions/{id}/rotate-token` — subscription lifecycle (owner-scoped); REST-only advanced/custom path
- `GET /api/reader/feed-token` + `POST /api/reader/feed-token/rotate` — the per-user aggregated feed token (`dfeed_`)
- `GET /api/public/feed/articles[.md]` — token-auth aggregated pull across all the user's subscribed sources (filters: `publish_date_start/end`, `content_types`, `source_ids`, `search`, `include_content`); per-subscription pulls at `GET /api/public/subscriptions/{id}/articles` and `POST .../vector/search`(路径留存,v3.31 起 FTS5 全文检索芯)

**MCP** (reader surface)
- `/mcp` — FastMCP streamable-HTTP server (`build_mcp_app`); tools accept an optional `subscription_token` (`dsub_` or `dfeed_`) to scope results to that subscription / the user's whole subscription union

**Reader AI Beta** (reader surface; gated by global master switch + per-account flag + LLM configured)
- `POST /api/reader/ai/translate` — translate an article body to 简体中文 (cached in `extensions_json`)
- `POST /api/reader/ai/social/{article_id}/translate` — 推文(主推 + 引用推)一次调用译为中文,返回 `{text, quoted, cached}`,缓存 `extensions_json.translation_zh_social`(issue #84)
- `POST /api/reader/ai/ask` — multi-turn QA,scope 四档 `article|articles|subscription|all`(v3.32:显式名单 vs 检索圈定;检索档走「LLM 计划检索 + FTS5」两段式);响应 `sources` 与上下文编号 [n] 同源同序(行内引用锚);可带 `ask_id` 登记阶段进度
- `GET /api/reader/ai/ask/progress?ask_id=` — ask 阶段进度轮询(plan→search→select→answer,内存瞬时态,完成即清;阶段化等待态数据源)
- `POST /api/reader/ai/podcasts/{episode_id}/ondemand`、`POST /api/reader/ai/articles/{article_id}/ondemand` — 读者点播精品导读 / 文章精简旁白，共用每日额度。总闸 `GET`/`POST /api/admin/reader-ondemand`（KV `reader_ondemand_enabled`，缺省开）在运维管理 → 内容；关闭只收读者入口并 403 新请求，已有音频与排队不动。能不能跑不由这个开关改：`/api/runtime` 的 `ondemand.podcast` / `ondemand.article` 还要求 LLM、TTS 和默认音色，播客侧另要求处理阶段授权。缺阶段时读者只看到「当前部署未开启播客点播」，缺什么列在管理面 `blockers`。

**Media（图床）** (see [媒体库](../architecture/media-and-storage.md))
- `GET /api/media/proxy?url=` — 正文外链图片代理（reader surface）：命中本地缓存回文件（长缓存头），未命中即时下载入库，失败/停用 302 回源降级
- `GET /api/admin/media/stats` — 缓存统计（按 URL 计数 / 内容去重文件数 / 落盘字节 / 失败数，admin）
- `GET /api/admin/storage/status` — 图片/音频云端登记量、本地缓存和自动备份状态（admin；不返回存储凭据）
- `GET /api/admin/media/heatmap?days=` — 逐日缓存覆盖聚合（热点图数据；现算不落表，admin）
- `GET /api/admin/media/days/{date}` — 当日逐篇明细 + 逐图链状态与失败原因（格子抽屉，admin）
- `POST /api/admin/media/articles/{id}/prefetch` — 单篇定点重抓（`force` 绕过失败退避冷却，admin）
- `POST /api/admin/media/backfill` — 全量回填后台 job；**前端入口已撤**，保留作脚本化应急通道 (admin)

**X 社交源**(admin-only,见 [社交媒体流](../architecture/social-x.md))
- `GET`/`POST /api/x-api/config` — 读/写 Bearer Token 与采集参数(token 只写不回显;`field_sources` 标出逐字段来源)
- `POST /api/x-api/config/test` — 连通性自测(最省钱探针,响应含本次 `estimated_cost_usd`/`deduplicated_today`)
- `GET /api/x-api/quota` — 本月 X API 资源计数与估算成本 + `by_source` 按源分解(按量付费守卫读数)

**互通波(v3.18):反馈 / 公告 / 远程同步**(设计 `docs/archive/engage-sync-wave-plan.md`;读者端点走 `/api/reader/*` 前缀天然 reader 门控,管理端点走 `/api/admin/*` 前缀天然 admin-only——零中间件改动)
- `POST`/`GET /api/reader/feedback` + `DELETE /api/reader/feedback/{id}` — 读者提交(单日 10 条限额 429)/查看自己的反馈(含管理员回复)/撤回 open 态;`GET /api/admin/feedback?status=` + `POST /api/admin/feedback/{id}/status` — 管理员收件箱(items+counts)与状态流转/回复
- `GET/POST/PUT/DELETE /api/admin/announcements`(+`/{id}/toggle`,GET 含 dismiss_count 触达计数)— 公告管理;`GET /api/reader/announcements` + `POST /api/reader/announcements/{id}/dismiss` — 读者取未关闭的 active 公告 / 逐条一次性关闭(`announcement_dismissals` 复合主键落库,跨设备一致;删除公告级联清 dismissal)
- `POST /api/admin/remote-sync/test|start` + `GET /api/admin/remote-sync/status` — 远程内容同步(接收方拉取):`services/remote_sync.py` 登录远端(Set-Cookie 手工抽取显式回传,兼容远端 cookie_secure)→ 分页拉 `GET /api/archive/export/articles.jsonl` → 本地 `import_archive_sync_jsonl` 幂等导入,跑 `remote_archive_sync` 后台 job;成功后把最大 `fetched_date` 记入 KV `remote_sync:state`(按 base_url 分目标)支撑「增量自上次」;**手动同步凭据只进任务内存,job payload 与 KV 游标绝不含密码**;带过滤时不设进度 total(远端全库总量会误导进度条)
- `GET`/`POST /api/admin/remote-sync/schedule` — 定时同步(v3.19.2):无人值守到点自动增量拉取。**凭据存 KV `remote_sync:schedule`,只写不回显**(定时任务必须持久化凭据——有意的契约例外,沿 X API token 范式:GET 只给 `password_set`,POST 空密码=保留已存;日志/审计摘要绝不碰密码);cron 经 `CronTrigger.from_crontab` 校验,保存即 `reload_remote_sync_schedule()` 热生效 APScheduler job `remote_sync`;`execute_remote_sync_job` 以该远端 KV 游标为起点增量、上轮 job 未完则跳过本轮(防重叠),经 `launch_remote_sync_job`(与手动 start 同构)提交,`created_by="system"` 与手动运行同列

**分享波(v3.25):把一篇内容发给同事**(两档并列,深浅分明;设计取舍见 `services/article_share.py` 模块注释)
- **站内深链**(零后端):`#/reader/a/{article_id}`,纯前端 URL、不落库、不外泄,收到的人登录后直达该篇。拼装与解析在 `frontend/src/utils/shareLink.js`;`App.jsx` 在 `commitNav` 初始播种 replace 掉 hash **之前**就地取走 id(晚一步就读不到),admin 带深链进来一律 `enterReader()`——压过 `default_surface` 与本 tab 会话的界面记忆,否则链接会开在看不见的隐藏阅读器里;ReaderTab 深链落地用 `deepLinkKeepRef` 通知作用域清场 effect **保留右栏**——深链要「切作用域+选中该篇」一次完成,而清场 effect(切源即 setActiveArticle(null))会把刚选中的文章抹掉,v3.25 首版即栽在这上
- **公开链接**(免登录只读单篇,**一篇一链**——同一用户对同一篇至多一条存活链接,再次生成先撤销旧的[rotate 语义,同 feed token],否则重复点生成会堆出一串各自有效的平行链接):`POST /api/reader/articles/{id}/share`(body `{expires_in_days: 7|30|null}`,返回明文令牌)/ `GET /api/reader/shares[?article_id=]` / `DELETE /api/reader/shares/{id}`(撤销,幂等,owner-scoped);访客侧 `GET /api/public/share/{token}` 走 `/api/public/*` 既有免登录豁免(零中间件改动),响应**刻意贫瘠**——只给这一篇的标题/来源/时间/正文,不带目录、检索、相邻篇目,令牌泄露的最坏后果钉死在「这一篇被看到」;正文图走 `GET /api/public/share/{token}/media?url=`(同套护栏 + **url 必须属于该文章图链集合** extract_image_urls——不是开放图片代理;`/api/media/proxy` 对访客 401、防盗链 CDN 直连又 403,没有它分享出去的中文源文章整页裂图;前端经 ReaderMarkdown 的 `resolveImageSrc` prop 注入,不计 view_count)
- **令牌明文入库**是与 `dsub_`/`dfeed_` 的**有意口径差异**:那两者存 hash、明文只回显一次(能拉走整个订阅库);分享令牌权限极小(单篇/只读/可过期/可撤销)而天然是「生成一次、反复复制」的东西,只存 hash 会让读者关掉浮层后再也复制不到、只能重新生成,链接越堆越多且旧的仍有效
- **护栏**:`dshr_` + 32 字节 urlsafe 不可枚举;隐藏源(source_visibility)不可分享且既有链接立即 404(与「读者面隐藏 = 内容交付全量排除」同口径);单人单日 30 条上限(429);**失败一律不区分原因**(不存在/已撤销/已过期/源已隐藏/文章已删 → 同一个 404,区分等于把令牌是否有效告诉猜令牌的人)
- **总闸** `GET`/`POST /api/admin/public-share`(KV `public_share_enabled`,默认开,读数含 live_count/total_count):**关闭立即让已发出的链接全部 404**——不必逐条撤销,且不销毁签发记录,重开即回归;开关在运维管理 → 内容(与媒体库、X 接入同类:对外的内容出口)
- 前端:阅读窗工具条 `Share2` 钮 → `ShareMenu.jsx` 浮层(站内链接=wash 可点行整行即复制;公开链接 hairline 之下退一档,两态:未生成=mini-seg 档位+唯一实心 CTA / 已生成=存活点+过期·触达 meta+复制/撤销 rowact 钮;换有效期=撤销后重生成);公开页 `SharedArticlePage.jsx` 在 **`main.jsx` 于 App 之外分流**——访客没有账号不能走登录门,且 App 的历史锚点会把令牌从 hash 里冲掉

**Admin Ops** (admin-only via `account_admin_required`, `/api/admin/*` prefix)
- `GET /api/admin/overview` — account/archive/AI stat board + recent logins
- `GET /api/admin/accounts?days=` — account list enriched with `subscription_count` + **窗口指标** (`reads`, `logins`, `ai_calls`/`ai_tokens` over the last `days`, `logged_in_window`) + 埋点 fields
- `GET /api/admin/accounts/{username}/activity?days=` — per-reader activity detail: windowed AI usage (`ai_usage.summarize_user`) + reads (`reader_activity.summarize_user_reads`) + logins (`accounts.summarize_user_logins`: count/by_day/recent) + `source_engagement` (per-source reads ∪ favorites, friendly-named) + `favorites_total` + account/subscription snapshot
- `GET /api/admin/ai-usage?days=` — AI usage dashboard (calls + tokens by 用途/用户/日期)
- `GET /api/admin/audit-log?days=&limit=` — 管理操作审计(v3.19):中间件落行的管理写操作,按时间倒序(见 [Admin audit log](../architecture/accounts-and-access.md))
- `GET`/`POST /api/admin/ai-beta/global` — read/set the AI Beta global master switch
- `GET /api/admin/credentials` — 设置柜凭据区只读补充:部署级 env-only 机密的存在性(`github_token_set`,只回布尔绝不回值;LLM/X 状态走各自 config 端点)
- `GET /api/admin/source-visibility` + `POST /api/admin/source-visibility/{source_id}` — 读者面源可见性(管理面隐藏节点):隐藏名单存 KV `reader_hidden_source_ids`(`services/source_visibility.py`,无新表),按 `source_id` 生效。语义=「读者面临时下架」(观察期故障/文章缺陷即时止损,不改代码)。**内容交付全量排除**:文章列表与单条详情(非 admin 会话)、收藏列表、聚合 feed(dfeed/dsub)、MCP/问答检索范围——范围收窄的单一咽喉是 `feed_service.resolve_subscribed_source_ids` 默认减隐藏(`include_hidden=True` 取原始并集,供目录/subscribe 响应/播种幂等判定)。**目录呈现分订阅态**(2026-07-27 拍板,替代初版「已订阅者也整体消失」——静默消失像数据丢失):未订阅者完全不可见(发现页不出现,新订阅 404);已订阅者源栏保留条目、`/api/reader/sources` 标 `hidden:true` → 前端灰显+「暂不可用」细签,点击短路成空态(不发列表请求,admin/读者同观感),读者可退订也可留着等恢复;既有订阅与归档原样保留、恢复即回归。admin 会话内容不受影响(知识台账/管理员令牌仍见全档)。开关在节点管理检视器(「在读者面隐藏」+ 节点卡「已隐藏」徽记),操作入审计
- `GET`/`POST /api/llm/config` + `POST /api/llm/config/test` — global model config (shared by Daily Brief + reader AI; edited from 运维管理)
- `POST /api/auth/preferences` — 任意登录账户写自己的偏好(目前仅 `default_surface`,管理员登录落地界面)
