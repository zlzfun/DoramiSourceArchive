# 节点看护 · 第一层:抓取失败判定升级(issue #82 L1)

> 状态:◉ 已拍板(2026-09-19,用户「全按推荐」,§5 七点即定案),R2 稿经 codex 首轮检视 + 一轮协商 + 对照复检收敛(2026-09-15,记录见 §9)。落地顺序见 §8,PR-0 先行。上接 issue #68(cron 缺席一整天)与 #79(三源静默停产十天);
> 第二层「异常通知」与第三层「自主自愈」另立方案,本层**刻意不引入任何外部通道**,只做判定升级、事件与收据落库、面板露出。
> 实证全部来自 2026-09-15 对生产库的只读回放(`fetch_runs` 4266 行 / 06-15 起,`articles` 近 75 天),脚本留在会话 scratchpad,落地时把回放数据固化为金标测试(§7)。
> 检视分歧与结论见 §9;R1 → R2 的主要改动:`silent` 从执行轴剥离成独立**产出轴**、看护判定与采集成功隔离、调度缺席改「收据先行 + 对账」、
> 发现模式默认 `unknown` 显式 opt-in、S2 用严格新增数 + 观察世代、日报运行史改表、时区口径写死并自检。

## 1. 问题

系统只会说「运行成功」,不会说「运行成功但什么都没抓到」;也没有任何地方记得「本该跑的没跑」。

- `run_fetcher_with_tracking` 只要抓取器不抛异常就记 `status=success`,`SourceStateRecord.status` 置 `healthy`、`consecutive_failures` 清零;
  `fetched_count=0` 对健康判定零影响(`app.py:2865-2880`)。新智元 09-05 起、IT之家 09-10 起每天 `success f0/s0/k0`,节点管理全绿。
- `last_success_at` 在零产出运行上照常前进,所以库里**没有任何字段表示「上次真的抓到东西是什么时候」**;
  个人早报的 `calculate_due_source_ids` 之类的新鲜度判断也全部盲。
- APScheduler 用内存 jobstore(`AsyncIOScheduler(timezone=...)`,`app.py:1430`),`EVENT_JOB_MISSED` 无人监听;
  进程死掉再起来,过期的触发**根本不会产生 missed 事件**(重启后 `next_run_time` 重算为明天)。09-14 全天 0 次定时运行,
  库里唯一的证据是 `fetch_runs` 那天没有 `trigger_type=scheduled` 的行;`docker compose logs` 已随部署重建容器而消失。
- 日报 `daily_brief_last_run` 是单槽 KV,每次运行覆写;定时任务的失败路径只写三个键(`status/ended_at/error_message`),把 `report_date`
  和全部计数一并清空(`app.py:2336-2344`)。没有历史就谈不上基线。
- **检视中顺带发现的现役 bug**(codex R1-F1,已核实):`load_tasks_to_scheduler()` 第一句 `scheduler.remove_all_jobs()`,而采集任务的创建 / 更新 / 删除端点
  (`routers/collection.py:174,207,218`)每次都调它;留存清理、远程同步、用户源刷新、播客 ASR worker 只在 lifespan 的「调度器新鲜启动」分支注册(`app.py:482-496`)。
  **编辑一次采集任务,这四个任务就从调度器里消失,直到下次重启。** 与看护无关,先行修(§8 PR-0)。

## 2. 实证(决定了方案形状)

### 2.1 源节奏两极分化,「基线 > 0」不是有效门槛

近 45 天每源 45–46 次运行(每日一次),按「有产出的天数」分档:

| 档 | 例 | 日均新增 | 有产出天数 / 45 | 最长无产出间隔 |
|---|---|---|---|---|
| 日更高产 | `web_huggingface_daily_papers` / `web_ithome_ai` / `rss_reddit_localllama` / `rss_the_decoder` / `web_qbitai` / `web_aiera` | 5.4–13.8 | 35–46 | 1–3 天(事故除外) |
| 日更低产 | `rss_openai_news` / `rss_testingcatalog` / `x_openai` / `x_sama` / `rss_latent_space` / `github_trending_daily` | 1.0–2.6 | 24–45 | 3–6 天 |
| 周更以下 | `rss_deepmind_blog` / `rss_hf_blog` / `rss_simonwillison` / `rss_interconnects` / `rss_mistral_news` / 全部 `docs_*` / `github_*` / `hf_*` | 0.1–0.8 | 2–23 | 8–32 天 |
| 45 天零产出 | `rss_lilianweng` / `web_meta_ai_blog` / `docs_gemma_release_notes` / `web_kimi_research` | 0 | 0 | — |
| 一直失败 | `rss_import_ai`(46/46 失败,IP 封锁)/ `rss_bair_blog`(21 失败,已读者面隐藏) | 0 | 0 | — |

生产 98 个源里 **约六成的运行是 `success && saved=0`**(仅 `fetched>0` 全部跳过),这是常态而非异常。

### 2.2 issue 原案「基线 > 0 且连续 3 次零产出」回放:74 源误报

近 30 天逐日回放,基线 = 前 14 天日均 `saved_count`:

| 规则 | 命中源数 | 源·天 | 说明 |
|---|---|---|---|
| 基线>0 ∧ 连续 3 **次** success&saved=0 | 74 | 588 | 周更源天天命中 |
| 基线>0 ∧ 连续 3 **天** | 74 | 580 | 同上 |
| 基线>0 ∧ 连续 2 天 | 80 | 763 | — |

绝大多数源本来就 8–30 天才出一篇,「基线 > 0」筛不掉它们。**判定必须相对于源自身的历史间隔**。

### 2.3 `fetched_count = 0` 判据:精准命中两源,但误伤全部 X 账号

「连续 3 次 success 且 fetched=0」只命中 9 个源:`web_aiera`(09-07 起)、`web_ithome_ai`(09-13 起)——两个真事故;
其余 7 个全是 `x_*`。X 时间线用 `since_id` 增量,API 返回 0 条是「没新推」的正常态。
→ 「发现 0 个候选」是不是异常,取决于抓取器的**发现模式**:列表式(RSS 每次都带最近 N 条 / 列表页每次都有链接)0 即失配;
增量式(服务端游标)0 是常态。方案 §4.1 的 `discovery_mode` 由此而来。

### 2.4 间隔感知规则 + 三道护栏:近 30 天只命中 3 源,两个是真事故

规则(§4.2 S2):60 天窗内「有产出的日子」≥ 3 天;`阈值 = max(3, ⌈2 × 历史最长间隔⌉)`;当前沉默天数 ≥ 阈值;沉默期内 ≥ 2 次成功运行。
护栏:剔除源的**首次产出日**(新源首抓吃掉积压,会把间隔算成 1 天);源历史 ≥ 14 天;60 天窗。

| 参数 | 命中 | 明细 |
|---|---|---|
| 无护栏,×1.5 | 14 源 / 69 源·天 | 含 `x_karpathy` 14 天、`rss_bair_blog` 10 天、两个刚加的播客 |
| 无护栏,×2.0 | 9 源 / 42 | — |
| **护栏全开,×2.0(推荐)** | **3 源 / 12** | `web_aiera` **09-07 首报**(事故 09-05 起,人工发现 09-15);`web_ithome_ai` **09-13 首报**(事故 09-10 起;09-11 无定时运行);`rss_nvidia_genai` 08-31/09-01 沉默 16 天 vs 阈值 16(边界提示,随后恢复) |

再加 §4.2 S1(列表式且不同两日发现 0 候选)后,新智元 **09-06**、IT之家 **09-12** 即报——比人工发现分别早 9 天与 3 天。
回放用的是 `saved_count`;落地后 S2 改用严格新增数(§4.2),回放窗口内两者只在正文回填 / 元数据修复的运行上有差异,对上述结论无影响,但金标要按新口径重导。

### 2.5 正文退化:OpenAI 不是 09-09 才坏的,是 7 月中就坏了

`rss_openai_news` 逐日正文长度中位数(按 `fetched_date`):**当天只有 1–2 篇时中位 5000–19000 字;≥ 3 篇时中位 141–172 字**,
从 07-15 一路如此,直到 09-15 修复回填后才回到 5000+。这和 #79 的根因严丝合缝:同一浏览器上下文**第一篇过、其余全被 Cloudflare 拦**,
退回 RSS 摘要。按周看,近 5 周「正文 < 400 字」占比 64%。

两种判法回放:
- 相对法(本周中位 < 前四周中位 30%):**零命中**——退化早于窗口,基线本身就是坏的。
- 绝对法(近 7 天新文 ≥ 5 篇且 < 400 字占比 ≥ 50%,限文章形态、非用户源、抓取器声明「应有正文」):**只命中 `rss_openai_news`(0.52)**。
  其它 ≥ 0.5 的全是推文(`x_*`)、HN 外链帖(`rss_hn_ai` 0.94,设计上无正文)、用户自定源(默认不取详情)、`docs_*` 公告与模型卡(bulletin 形态)。

→ 用绝对法;范围**不由形态推断**,而由源级显式声明(§4.3),回放只证明「当前候选集合下唯一命中」,不证明形态推断对未来源安全。

### 2.6 cron 缺席:证据只在库里,不在日志里

- 09-01～09-13 每天 62–97 次 `scheduled` 运行,**09-14 为 0**(全部 97 次是止血后手动补跑);这是 #68 的库内实证。
- 部署重建容器后 `docker compose logs` 只剩当天 8 小时;今天 8 小时里 23 条 `was missed`,**全部是分钟级 interval 任务被晚了几秒**
  (重启、`/api/runtime` 阻塞窗口),没有一条是 cron。→ 监听器要按任务类别分流,否则全是噪声。
- 内存 jobstore + 进程死亡 = 事件永远不发;而日报 / 个人早报 / 远程同步等回调都在内部吞异常正常返回,`EVENT_JOB_ERROR` 也看不到它们(codex R1-F4)。
  → 「回调入口写收据 + 期望触发 vs 收据对账」是主机制,监听器只补 `missed`。

### 2.7 日报运行记录

`daily_brief_last_run` 现有 18 个键(`candidates_scanned/used`、`scored_*`、`below_threshold`、`score_histogram`、`threshold_backfilled`、
`near_miss_appendix`、`min_score/min_items` …),但只有一槽;`GET /api/daily-brief/runs` 的 `history` 是已发布日报文章列表,不是运行史。
「≥ 门槛条数」没有直接字段,可由 `sum(score_histogram) − below_threshold` **对任意门槛精确**得到(直方图计的是全部 `score_ok` 项,`below_threshold` 计的是其中低于门槛者)。
`empty` 载荷只到 `candidates_used`,没有评分字段;`failed` 载荷绕过 `_record_last_run`——三条写路径三种形状。

## 3. 现状判定链路(改动落点)

| 环节 | 位置 | 现状 |
|---|---|---|
| 抓取器 → 管线 | `fetchers/base.py:116-164` `fetch()` 是异步生成器,`_run()` 唯一钩子 | **没有任何逐次运行的统计通道**;先例只有逐条挂属性(`item._cursor_value`) |
| 管线计数 | `pipeline/core.py:81-134` `PipelineRunResult{fetched,saved,skipped,…}` | `saved` = 任一 sink 返回 True(**含正文回填/元数据修复**,不严格等于新文章);`skipped` = 全部 False;新插入的文章行已带 `fetch_run_id` |
| 运行落库 | `app.py:2093-2115` `create_fetch_run(logical_node_id, …)` | `fetch_runs` 无 `source_id` 列。**两种形态并存**:source-config 节点的 `fetcher_id` 已是逻辑源 id;用户自定源仍写 `fetcher_id=generic_rss`、逻辑源只在 `params_json.$.source_id`(本机库 2026-09-15 19:06 的行可证) |
| 身份解析 | `app.py:2779-2786` `resolve_state_source_id` | 有结果时取**条目**的 `source_id`,无结果时取 params / fetcher_id——同一运行可能在 A 源 started、在 B 源 finished |
| 成功收尾 | `app.py:3011-3016` → `except` `:3030-3046` | `finish_fetch_run(success)` + `mark_source_state_finished(success)` 之后的**任何异常**都走外层 except,把已入库的运行改记 `failed` |
| 源状态 | `app.py:2838-2891` `mark_source_state_finished` | success ⇒ `healthy`,不看计数;值域 `healthy/failing/running/never_run/unknown`;`mark_source_state_started` 无条件写 `running`,启动自愈写 `unknown` |
| 状态消费方 | `routers/personal_briefs.py:44,232` `TERMINAL_SOURCE_STATES = {healthy, failing, unknown}`;`mcp_server.py:66` `last_fetch_status`;`archive_sync_v2.py:476/2421` 载荷与固定字段循环 | 个人早报把 `status ∈ 终态集合` 当「今天调度已完成」——往执行轴加新值会让公共源等到 deadline |
| 健康端点 | `routers/monitoring.py:84-110` `build_fetcher_health_from_state` | 纯镜像 state 行;v3.43 M13 规定有 state 行的节点**不得再查 `fetch_runs`**(每 45 秒轮询) |
| 运行列表 | `monitoring.py:217-283` `/api/fetch-runs` | `{items,total}`;`fetcher_id` 参数已兼容 `or_(fetcher_id, json_extract(params_json,'$.source_id'))` |
| 运行聚合 | `routers/stats.py:27-115` `/api/stats/daily` | `runs` 聚合 `CollectionJobRunRecord`(只有 fetched/saved/skipped),`solo` 聚合无父的 `FetchRunRecord`;不接受源过滤 |
| 用户源自动停 | `services/user_sources.py:949-976` | `consecutive_failures ≥ 10` ⇒ `is_active=false`,只看异常 |
| 调度 | `app.py:2166-2181` `add_cron_job`(cron 类 300s 宽限 + coalesce);`app.py:2184` `load_tasks_to_scheduler` 首句 `remove_all_jobs()` | 无监听器;非 5 段 cron **静默 return**,5 段但字段非法由 `CronTrigger` 抛异常;见 §1 末条现役 bug |
| 日报记录 | `services/daily_brief.py:1426` `_record_last_run` → KV 单槽;`app.py:2331-2344` 定时失败路径 | 见 §2.7 |
| 留存 | `services/retention.py` `_TABLES` 纯时间窗;`_conditional_cleanups` 非终态永不清 | `fetch_runs`/`collection_job_runs` 180 天;`app_settings` 永不清理 |
| 时间口径 | `app.py` `_now_iso()`、`models/content.py:37` `fetched_date` 均 naive 本地时间;调度器 `Asia/Shanghai`;个人早报自带 `_as_shanghai` | 「日」的投影全项目未统一;生产 compose 钉 `TZ=Asia/Shanghai` |
| 前端状态语义 | `frontend/src/statusMeta.js:39` `healthMeta` 四态;`FetchTab.jsx:50-59` `HEALTH_SIGNAL`/`SIGNAL_STATS`;`index.css:5885` `.signal-dot-*` **硬编码 hex**;`App.jsx:336` `onViewRuns` 只透传 `status` | 状态章范式 `.stamp-{ok|warn|bad|run|idle}`(淡底+形状,无描边),conventions §6「新状态一律用它」 |

## 4. 方案

### 4.0 总原则

1. **执行轴与产出轴分离**:`FetchRunRecord.status` 与 `SourceStateRecord.status` 仍是「执行结果」(不抛异常即 success / healthy),零产出运行仍是 `success`——
   否则任务级 `partial_failed` 聚合、重跑语义、用户源自动停抓、个人早报终态判定全部串味。「有没有产出」是**新的独立轴** `yield_status`,失败与静默可以同时成立、各自陈述。
2. **看护判定是采集成功之后的 best-effort 派生步骤**:独立 Session、独立 `try/except`,它的任何异常只记日志与事件,绝不改写运行、文章与执行状态;巡检兜底重评。
3. **所有判定机械、纯函数、可回放**:规则只吃 `fetch_runs` / `articles` 行与常量,注入 `today`;判定依据(输入行、阈值、世代、排除的事故)固化进快照;生产回放数据即金标。不用 LLM。
4. **收据先行**:受看护的定时回调在入口写收据、终态写结果;「缺席」是「期望触发点没有收据」,不是「业务产物不存在」,更不是「日志里有 missed」。
5. **一切判定、事件、收据落库**;`watch_events` 是第二层告警状态机与第三层「统一事件流」的起点。
6. **不引入外部依赖、不加常驻负载**:逐次运行的判定是一条按日聚合的有界查询;其余走每小时一次的看护巡检,同步 DB 一律 `asyncio.to_thread`(#68 教训)。
7. **时区口径写死**:所有「日」显式投影到 Asia/Shanghai;巡检自检本地偏移 ≠ +08:00 时 fail-closed(§4.9)。
8. **可部署纵切 + feature gate**:schema 与上报通道先行、判定后开(KV `watchdog_enabled`),枚举消费者与后端字段同 PR 落地,不让新值先于消费者生效。

### 4.1 抓取器上报「发现候选数」`discovered_count`

**通道**:`BaseFetcher` 增实例属性 `run_stats: Dict[str, Any]`(`fetch()` 入口清空)与助手 `self.note_discovered(n, **extra)`;
`DataPipeline.run_task` 生成器耗尽后读 `getattr(fetcher, "run_stats", {})`,写入 `PipelineRunResult.discovered_count: Optional[int]`
(`None` = 抓取器未上报)。沿 `_cursor_value` 「运行期控制位、非归档字段」的先例,不动内容对象。

**发现模式**:`BaseFetcher.discovery_mode: str = "unknown"`——**默认不参与 S1**,只有审计过的族显式声明:
`listing`(列表每次完整暴露,0 即失配)/ `incremental`(服务端游标,0 为常态;`XTimelineFetcher`)/ `singleton`(整页单条固定 id;`SinglePageDocumentFetcher` 基类,`discovered` 恒 1)。
未来新增的抓取器忘记声明就是 `unknown`,不会误入 S1。

**各族触点与语义**(每族固定「网络解析后、`limit` 前、必需身份过滤后」这一点;覆写 `_run` 的每个类做参数化契约测试):

| 族 | 模式 | 触点 | 上报值 |
|---|---|---|---|
| RSS 全族(`GenericRssFetcher` + ~40 preset + OpenAI + HN) | listing | `rss_fetcher.py:307` `feedparser.parse` 之后、`[:limit]` 之前 | `len(parsed_feed.entries)`(HN 的 `min_points` 是 hnrss 服务端参数,entries 即发现数;`bozo` 只告警不抛,正是静默源头) |
| Podcast RSS | listing | `podcast_rss_fetcher.py:485/495` | `len(audio_entries)`,`feed_entries` 进 extra(两级:feed 坏 vs enclosure 结构坏) |
| Web 列表基类(含 Cursor、`_ScopedArticleBodyFetcher` 四家、`generic_web` 无 CSS 时) | listing | `webpage_fetcher.py:456` 分页循环结束、`entries[:limit]` 之前 | `len(entries_by_url)`(已过 `article_url_patterns`;循环达 limit 即 break,故是「至少发现」而非全量) |
| 覆写 `_run` 的 web 子类:Anthropic / IT之家 / Qwen JSON / 量子位 / 新智元 WP REST | listing | 各自一行(Anthropic **只由最终采用的发现路径写一次**;新智元跨页累加) | 身份过滤后的候选数 |
| 公告分段族(`_segment_with_render_fallback`,8 个调用点) | listing | 回落渲染之后 | `len(entries)`——它们把单页拆成多条,是列表语义,与真单页的 `singleton` 分开 |
| GitHub Releases / Repos / HF Models | listing | `:152` / `:213` / `:369` | API 列表长度(受 `per_page=limit` 结构性封顶,只用于 0 判定) |
| GitHub Trending | listing | `:535` | `len(items)`(已在 0 时 raise,是全库唯一自带此语义的抓取器) |
| X 时间线 | incremental | `:353` | `len(posts)` |
| `generic_web` 带 `listing_css` | listing | `configurable_web_fetcher.py:192-199` | **`len(seen)`**(URL / pattern / 去重之后、`drop_empty` 之前),`raw_nodes` 进 extra——原始节点数不是可产出候选数,selector 子字段坏了原始数仍 > 0 |

### 4.2 产出轴 `yield_status` 与两条规则

`SourceStateRecord` 新增:`yield_status`(`''` 未分类 / `ok` / `silent`,索引)、`yield_reason`(`no_candidates` | `no_new_items`)、`yield_since`、`yield_watch_json`(判定依据快照)、
`watch_epoch_at`(观察世代起点)。`status` 一字不动;`TERMINAL_SOURCE_STATES`、启动自愈、MCP `last_fetch_status`、`/api/source-states?status=` 全部不受影响。

**观察世代与样本资格**:规则只看 `started_at ≥ watch_epoch_at` 且**参数指纹与源当前指纹一致**的运行(指纹 = 物质参数的 hash:url / patterns / css / limit / 发现模式;
preview、`test_limit`、临时改 limit 的运行指纹不同,自动排除)。世代在以下时刻刷新:源启用、停用后重启、`SourceConfigRecord` 物质字段变化、管理员点「重置基线」。
预置抓取器的代码改动随部署生效,不刷新世代(回放显示修复后的正结果本身就会立即恢复)。

**S1 解析失配(`no_candidates`)**:`discovery_mode == listing` ∧ 最近两条合格运行 `success` 且**显式** `discovered_count == 0` ∧ 两条来自**两个不同的上海自然日**(触发类型不限——
隔日手动试抓是真证据,排除的只是同日连点)∧ 世代内曾有 `discovered > 0` 或 `inserted > 0`(从未产出过的新源不算失配,那是配置问题,由试抓预览负责)。
**没有 `None → fetched=0` 的回落**:部署后 S1 需要两条新运行(即两天)才能判定,诚实。手动正结果立即恢复。

**S2 产出静默(`no_new_items`)**:列表正常但没有新东西。60 天窗内以 `inserted_count > 0` 的日子为「产出日」(严格新增数,见 §4.6;正文回填 / 元数据修复不打断沉默),
剔除源的**首次产出日**;产出日 ≥ 3,否则 `yield_status=''`(**未分类**——月更源在此门槛下就是判不出,如实表述,不称「自然放宽」);
`expected_gap = max(3, ⌈2.0 × max(相邻产出日间隔)⌉)`,**与已 resolve 的 `source_silent` occurrence 条件区间重叠的间隔不计入 max**(事故间隔我们自己标记过,用事实剔除而非分位数——样本只有 3–20 个间隔,分位数无统计意义;
仍 unresolved 的事故与看护未观测到的停机不假定剔除,保守保留其影响);`silence = today − 最后产出日 ≥ expected_gap`;
沉默期内 ≥ 2 次 `success` 运行且来自**不同上海自然日**(排除「是调度没跑」与同日重试);源首次运行距今 ≥ 14 天。
基线先从当前 occurrence 之外的稳定历史算出,再判定本次——不自引用;输入行 id、排除的 occurrence id、阈值、`watch_epoch_at`、指纹一并固化进 `yield_watch_json`(带 `schema_version`)。

**评估时机**:`run_fetcher_with_tracking` 成功收尾(`finish_fetch_run` + `mark_source_state_finished`)不可逆完成后,再以独立 Session 调 `watchdog.evaluate_after_run(run_id, source_id)`,
外裹 `try/except`:异常只写日志 + `watch_events(kind=evaluator_error)`,**绝不改写运行 / 文章 / 执行状态**。查询是按日聚合(`date(started_at), sum(inserted), count, status`,≤ 60 行),走 `(source_id, started_at)` 复合索引。
巡检每小时兜底重评「最近运行晚于上次评估」的源。`/api/source-health` 仍是纯镜像,M13 不受影响(M13 约束的是 45 秒健康轮询)。

**恢复**:S1 源出现 `discovered > 0`、S2 源出现 `inserted > 0` 即 `yield_status=ok`,清 `yield_reason/yield_since`,resolve 对应 occurrence 并写 `condition_end`。失败运行不参与产出判定(执行轴自己记 `failing`)。

**用户自定源**:同规则(它们走 `generic_rss`,listing)。`silent` **不触发自动停抓**——那是失败计数的事;管理面「用户自定源」表的状态档加「静默」。

### 4.3 正文偏薄 Q1(不改任何 status,只做细签)

**范围由源级策略字段决定,不由形态推断**:`body_expectation` ∈ {`unknown`(默认)| `full_body` | `summary_only`};预置抓取器用类属性给默认,`SourceConfigRecord` 加同名列可覆盖(config 源 / 用户源默认 `unknown`);
首批显式 `full_body` = 开详情抓取的 web 族 + §2.5 里周中位 > 2000 字的 RSS 源(审计名单随 PR-C);`HackerNewsAiRssFetcher` 显式 `summary_only`。**`unknown` 永远不进 Q1**。
字段进 Archive Sync 的 sources 流(接收方只读);字段变化 → 该源开放的 Q1 occurrence 以 `policy_changed` resolve 并刷新世代。

规则:近 7 天(按 `fetched_date` 归上海日)该源新文 ≥ 5 篇 ∧ `length(coalesce(content,'')) < 400` 占比 ≥ 0.5 → occurrence `source_thin_bodies`(证据冻结 n / 窗口 / 阈值 / 占比);占比回落 < 0.3 关闭(滞回)。
由看护巡检每小时一条分组 SQL 算完(`ix_articles_source_id_fetched_date` 在)。节点卡文案「正文偏薄 · 近 7 天 52% 仅摘要」——不写「退化」,没有契约基线时不下结论。

### 4.4 调度:先修重载,再加收据与对账

**PR-0 先行修复(§1 末条现役 bug)**:`load_tasks_to_scheduler()` 改为**只差量同步 `collection_job_*` 命名空间**(按 DB 现状增 / 改 / 删,不碰其它 id;日报、分析、分类、播客、个人早报各自的注册仍在但改幂等),
`retention_cleanup` / `remote_sync` / `user_rss_refresh` / `podcast_asr_worker` 的 `reload_*` 保持幂等;集成测试「启动 → 增改删采集任务 → 断言五类任务仍在且不重复」。看护巡检 `watchdog_sweep` 依赖此修复。

**受看护回调统一包装 `guarded_schedule(job_key)`**:cron 类回调(采集任务 / 日报 / 个人早报定时 / 远程同步 / 留存 / 看护巡检)返回统一的 `ScheduleOutcome(status ∈ succeeded|failed|skipped, reason, error)`——
现有回调在 catch 分支显式报告失败、在合法 no-op(配置关闭 / 上轮未完 / 无活跃用户)报告 `skipped` 并带 reason;逃逸的异常由包装器记 `failed`。包装器入口写收据、终态写结果。分钟级 interval 任务不包装、不发收据。

**收据表 `schedule_receipts`**:`job_key` / `scheduled_for`(带 `+08:00` 的规范 ISO)/ `schedule_revision` / `entered_at` / `ended_at` / `status` / `reason` / `error` / `origin`;唯一键 **`(job_key, scheduled_for, schedule_revision)`**,重试 / 恢复只 upsert 同一行。
`scheduled_for` 的来源:监听 `EVENT_JOB_SUBMITTED`(在 AsyncIOScheduler 的 `_process_jobs` 里同步派发,先于回调协程启动)把每个 `scheduled_run_times` 入按 `job_id` 的有界队列,包装器入口 pop 匹配项;
**不用入口时刻向前对齐作常规回落**——取不到就记 `receipt_time_unknown`,对账保守处理。手动触发(日报手动生成、任务「立即运行」)走 `jobs.launch` 不经调度器,不产生收据。
`schedule_revision`:采集任务加 `cron_updated_at` 列(**仅** `cron_expr` / `is_active` 变化时更新,不复用通用 `updated_at`);日报 cron 保存时写 KV `daily_brief_cron_updated_at`;个人早报定时 / 留存 / 巡检为代码常量,revision = 部署版本。

**对账(巡检每小时)**:对每个 `is_active` 的采集任务与上述固定 cron,用 **aware 的上海时间**枚举过去 24h 的期望触发点(`CronTrigger.get_next_fire_time(None, window_start)` 起步,`(previous, previous + 1µs)` 迭代到 window end),
只枚举 `≥ schedule_revision` 时刻的触发点;触发点 `t` 在 `[t−60s, t+grace(300s)+60s]` 内没有收据 ⇒ occurrence `job_missed`(`scheduled_for=t`,`origin=reconcile`)。
**coalesce 语义写死**:晚到但在宽限内的合并补跑只覆盖它所在的那个触发点,更早被合并掉的触发点如实记缺席——它们确实没按时跑,这与 coalesce 是设计行为不矛盾。

**监听器(注册两个事件,各司其职)**:`scheduler.add_listener(handler, EVENT_JOB_SUBMITTED | EVENT_JOB_MISSED)`,两者都只入内存队列(不在调度线程写库)。
`EVENT_JOB_SUBMITTED` 是收据的 `scheduled_for` 来源(上文按 `job_id` 的有界队列,包装器入口 pop);`EVENT_JOB_MISSED` 补缺席:cron 类任务的 missed 记 occurrence(`origin=listener`,与对账同 dedup 键合并),
interval 任务的秒级迟到只累加当日计数(`subject=job_id`,`scheduled_for=当日`);missed 队列每次巡检 flush + shutdown 钩子 best-effort。`EVENT_JOB_ERROR` 不注册、不作领域错误来源(回调都吞异常,它看不到)。
顺手修 `add_cron_job`:非 5 段与字段非法两种解析错误都在**保存配置前**与 reload 时捕获,保存失败不 commit;记 `schedule_invalid`(表达式脱敏快照 + 解析错误)。

**事件表 `watch_events`(occurrence 模型)**:`id` / `kind`(`source_silent` | `source_thin_bodies` | `job_missed` | `job_error` | `schedule_invalid` | `brief_below_baseline` | `evaluator_error` | `tz_mismatch` | `receipt_time_unknown`)
/ `subject`(source_id / job_id / `daily_brief`)/ `severity`(`warn` | `bad`)/ `condition_start` / `condition_end`(真实条件区间——S2 用「最后产出日 → 恢复日」,不是 first_seen → resolved)
/ `first_seen_at` / `last_seen_at` / `resolved_at` / `count` / `origins`(集合)/ `scheduled_for`(**`NOT NULL DEFAULT ''`**:missed 类填规范时刻,其它类恒空串——SQLite 里多个 NULL 互不冲突,可空列进不了唯一索引)/ `detail_json`(带 `schema_version`:世代、阈值、边界、指纹、输入行 id)。
**活跃期唯一**:部分唯一索引 `(kind, subject, scheduled_for) WHERE resolved_at IS NULL`(三列均非空,`source_silent` 一源至多一条活跃行);`count/last_seen_at/origins` 的 upsert 原子且单调;resolve 与 reopen 以 `resolved_at IS NULL` 条件更新保护并发;resolve 后复发新建一行。
留存:只删 `resolved_at < cutoff(180d)`,未解决永不删(进 `_conditional_cleanups`)。第二层的投递尝试另表,本层不加 `notified_at`。

### 4.5 日报基线 B1

- 新表 `daily_brief_runs`:`run_id`(唯一;手动 = `JobRecord.id`,定时 = 收据键)/ `receipt_id` / `trigger` / `triggered_by` / `status`(`success|empty|failed`)/ `started_at` / `ended_at` / `report_date`
  / 关键数值列(`candidates_scanned` / `candidates_used` / `at_or_above` / `below_threshold` / `articles_count` / `threshold_backfilled`)/ `metrics_json`(全量 + `schema_version`)/ `error`;
  三条写路径收敛到一个 recorder,`empty` / `failed` 缺省 0,重试 / 恢复只 upsert 同一 `run_id`;`KEY_LAST_RUN` 保留为兼容快照。
- 基线:只取**本次之前**最近 7 次 `trigger=scheduled` 且 `success` 的运行,`candidates_scanned` 与 `at_or_above` 各取中位数;最小样本 3(不足不判);`min_score` / 名单 / cron 变化后 warm-up(重新累计样本)。
  本次任一项 < 中位 50% ⇒ occurrence `brief_below_baseline`(证据冻结两组数)。手动重跑不参与基线也不触发(游标已推进,scanned 天然小)。
- 露出:`GET /api/daily-brief/config` 与 `/runs` 加 `baseline{scanned_median, at_or_above_median, current, samples, warning}` 与 `history_runs`;面板沿 `scoringHint` 范式加一行 `pipeline-note is-warn`,正常静默。

### 4.6 数据模型与迁移

- `fetch_runs`:+`discovered_count INTEGER NULL`;+`inserted_count INTEGER NULL`(运行结束按 `fetch_run_id` 计新文章行,一条查询);+`source_id`——**运行前一次性解析并冻结**,同时写 fetch run / started / finished,
  结果里的 source id 只校验相等(不等记 warning + `evaluator_error`,不重选身份);复合索引 `(source_id, started_at)`。迁移分阶段:nullable 加列 → `CASE WHEN json_valid(params_json) THEN json_extract(...) END` 回填、空则 `fetcher_id` → batch 改 `NOT NULL DEFAULT ''` → 建索引;
  drift 测试未开 `compare_server_default`,补 schema 断言。`/api/fetch-runs` 的 `or_` 兼容判据保留一个留存窗(180 天)后删。
- `source_states`:+`yield_status`(索引)/ `yield_reason` / `yield_since` / `yield_watch_json` / `watch_epoch_at`。
- `collection_jobs`:+`cron_updated_at`。`source_configs`:+`body_expectation`。
- 新表:`watch_events`、`schedule_receipts`、`daily_brief_runs`(§4.4 / §4.5);三表进 retention(`watch_events` 条件清理;收据 / 日报运行 180 天纯时间窗)。
- `PipelineRunResult` +`discovered_count`;docstring 值域更新;`tests/test_migrations.py` 漂移守卫自然覆盖。

### 4.7 API

- `GET /api/source-health`:新增 `yield_status` / `yield_reason` / `yield_since` / `watch`(解析后的快照);**组合灯位** `health_status` = `failing > running > silent > healthy > never_run`(仅供信号灯);`latest_run_status` 不变。零新查询。
- `GET /api/source-states`:+`yield_status=` 参数(与 `status=` 正交)。
- `GET /api/fetch-runs`:行带 `discovered_count` / `inserted_count` / `source_id`;新参数 `Query(alias="yield")` ∈ `zero`(`success ∧ inserted=0`)/ `nocand`(`success ∧ discovered=0`)。
- `GET /api/stats/daily`:响应分 **`collection` 与 `node` 两个 facet**,一个 child 不会被两边各算一次:`collection` = 整次 job(零产出 = job `success ∧ saved_count=0`,无 `nocand`);`node` = child + solo 的 `FetchRunRecord`(有 `nocand`);
  接受与列表相同的 `fetcher_id / days / yield` 过滤——源过滤只作用于 `node` facet;每个 facet 返回互斥的 `success_with_output` / `success_zero_output`,分母固定为同层级同过滤集。
- `GET /api/admin/watchdog?days=7`(新 router `routers/watchdog.py`,`require_admin`,委托 `services/watchdog.py`):
  `{sources:{silent:[…], thin_bodies:[…], unclassified_count, counts}, scheduler:{occurrences:[…], missed_by_job:{…}, receipts_summary}, daily_brief:{history_runs, baseline}, tz_ok}`,形态照 `analysis_observability.collect_release_metrics`。
- `POST /api/admin/watchdog/sweep`(手动巡检,走审计);`POST /api/admin/watchdog/sources/{id}/reset-baseline`(刷新世代,走审计)。

### 4.8 前端

**节点管理**(`FetchTab.jsx`):
- `statusMeta.healthMeta` 加 `silent → {label:'静默', tone:'amber'}`(输入是组合灯位 `health_status`);`HEALTH_SIGNAL.silent='silent'`;`SIGNAL_STATS` 在「失败」后加「静默」灯位。
- 信号点 `.signal-dot-silent`:`--state-warn` **空心圆环**(2px 描边、透明芯)——与「运行中」的实心琥珀呼吸点靠形状区分(色板五态封闭,conventions §4/§4.1);借此把四个硬编码 hex 的 `.signal-dot-*` 一并改成 `--state-*` token。
- 节点行「上次运行」格(`:756-765`):`silent` 时「已 N 天无新文 · 历史最长 M 天」或「列表 0 候选 · 疑似解析失配」,`.board-node-last-res.is-warn`;失败披露行同款结构加「查看零产出运行 →」(`onViewRuns(id, {yield:'zero'})`——`App.jsx:336` 的 options 透传要从只认 `status` 扩到 `yield`)。
- 检视器:`inspector-stamp-silent`;标准节点分支补一块事实 `<dl>`(上次有新文 / 历史最长间隔 / 近 14 天日均 / 上次发现候选数 / 观察世代起点 + 「重置基线」钮);正文偏薄用 `stamp stamp-warn` 细签。默认选中顺序 `failing → silent → 首个`。
- 管理面「用户自定源」表 `ThFilter` 加 `['silent','静默']`,行状态章 `stamp-warn`「静默」。

**运行历史**(`FetchRunsTab.jsx`):
- 总账条加第六格「零产出」,计数来自 `/api/stats/daily` 的 `node` facet;它回答的是「有没有产出」而非「成不成功」,为守 §4.1.1 单轴,**成功格改为互斥的「成功·有产出」**——issue 原话「把这类运行从成功里剥出来」正是此意;
  `pending filter` / 请求参数 / 本地状态贯通 `yield`;聚合失败时保留 500 行回退口径标注。
- 行内「抓取」列前加「发现」列(`discovered_count`,`None` 显示 `–`);`discovered=0` 的成功行状态章 `stamp-warn`「零候选」;日分组行加 `· 零产出 z`。

**日报面板**(`DailyBriefPanel.jsx`):`baseline.warning` 时一行「本次候选 N 条、≥门槛 M 条,低于近 7 次中位(N₀ / M₀)的一半」(`pipeline-note is-warn`)。

**运维管理**:新增子页「看护」(`sub='watch'`,第六个 `segmented-option`)——第二层的告警 / 静默期 / 通知史本就规划落此,先把观测面立起来:
KPI 条(静默源 / 正文偏薄 / 近 7 天调度缺席 / 未分类源,非零才 `is-warn`)+ 三张 `acct-table`(节点产出:源 · 状态 · 原因 · 起始 · 依据,列头轮换筛选;调度:任务 · 计划时刻 · 收据状态 / 缺席 · 发现途径 · 次数;日报近 7 次:日期 · 扫描 · ≥门槛 · 入选 · 补足)+ `tz_ok` 不成立时区头 `stamp-bad`。
零描述文本,一句 `zone-hint`。移动端不做(M24 边界)。

**枚举 / 字段加值的消费点清单**(X 社交波白屏教训,与后端字段同 PR 落地):`healthMeta` / `HEALTH_SIGNAL` / `SIGNAL_STATS` / `.signal-dot-*` / `.inspector-stamp-*` / `UserSourcesPanel` 状态派生与 `ThFilter` /
`App.jsx` `onViewRuns` options / `FetchRunsTab` pending-filter 与请求 / `.source-health-*` 暗色样式核对;后端 `TERMINAL_SOURCE_STATES`(产出轴独立后**无需改**,列入清单是为了测试断言它不变)/ MCP `last_fetch_status`(同上)/
Archive Sync 两侧字段名单;读者主目录 `/api/reader/sources` 不消费 status,`/api/reader/custom-sources` 返回 status 但前端目前无调用——契约级,不是 UI 风险。

### 4.9 时间口径、调度与性能边界

- **「日」统一投影到 Asia/Shanghai**:S2 按运行 `started_at` 归日、Q1 按文章 `fetched_date` 归日、B1 按 `report_date`,三者不混用;cron 枚举与 `scheduled_for` 用 aware 上海时间、存带 `+08:00` 的 ISO。
  持久时间仍沿全项目 naive 本地约定(改 UTC 是独立波次,届时现有 naive 值必须按上海时间解释,不能按机器时区回读)。
- **时区自检 fail-closed**:巡检启动时本地偏移 ≠ +08:00 ⇒ 记 `tz_mismatch` occurrence 并**跳过本轮 S2 / Q1 / C1 / B1 的判定与事件关闭**,直到环境恢复或管理员显式确认;`tz_ok` 透出到看护页。
- 看护巡检 `watchdog_sweep`:`add_cron_job("watchdog_sweep", …, "7 * * * *")`,PR-0 之后与其它固定任务同一幂等注册路径,collector 角色启用;工作 = flush 监听队列 → 收据对账 → 兜底重评 → Q1 分组 SQL → B1 → 全部 `asyncio.to_thread`,单轮目标 < 1s,`max_instances=1`。
- 逐次运行的判定:一条按日聚合查询(≤ 60 行)走复合索引;生产 `/api/source-health` 45 秒轮询路径零新增查询。

### 4.10 拆分部署(Archive Sync)

`_source_state_payload` 加 `yield_status / yield_reason / yield_since / yield_watch_json / watch_epoch_at`,消费方 `fields` 名单同步加;语义定为**「缺键沿用」**——旧生产者永不发这些键、旧接收方永不读,
且 `status` 一字不动,滚动升级不再有「新值泄漏到旧前端」的问题(双向载荷测试锁定)。`sources` 流加 `body_expectation`(接收方只读)。`watch_events` / `schedule_receipts` / `daily_brief_runs` 不同步(本地运维事实)。

### 4.11 与个人早报的交互(显式条款)

readiness 仍按执行终态(`TERMINAL_SOURCE_STATES`)与收据判断,`silent` **不阻塞**、不延后 deadline;冻结快照另带 `yield_status` / `last_new_item_at` 作观测说明(进 `selection_stats_json` 的编排说明行素材,不改选篇);
`silent` 不触发 `consecutive_failures` 自动停抓;私有源 `calculate_due_source_ids` 仍看 `last_success_at`——若要让静默源影响选篇等待 / 降级,另拍板,不借 `last_success_at` 隐式改。

## 5. 决策点(2026-09-19 用户拍板「全按推荐」,以下即定案)

1. **产出轴独立成列**(`yield_status` 等),`status` 保持执行轴;组合灯位由 API 现算。理由:失败与静默可同时成立;`TERMINAL_SOURCE_STATES` / 启动自愈 / Sync / MCP / 旧前端零连锁。
2. **S2 常数保留** `max(3, ⌈2.0 × 最长间隔⌉)`(回放支持),但附条件:严格新增数、不同上海日、观察世代 + 指纹、显式 `discovered`、产出日 < 3 = 未分类、事故间隔按 resolved occurrence 剔除;观察两周再议上限 / 分位数。
3. 运行页第六格叫**「零产出」**(运行级事实),「静默」留给源级判定;成功格改互斥的「成功·有产出」。
4. Q1 用**绝对占比法**,不追加相对条件(会漏长期退化的 OpenAI);前提 = 源级 `body_expectation=full_body` 显式 opt-in、NULL 归一、0.5/0.3 滞回、文案「正文偏薄」。
5. **本层就开「看护」子页**(四类观测共享一个运维主语,塞进「内容」会混轴)。
6. **加 `fetch_runs.source_id`**,按 §4.6 收紧(运行前冻结、复合索引、分阶段回填)。
7. **巡检每小时**;前提是收据先行(§4.4),第二层上线后再评估 15 分钟,不用提频掩盖证据模型问题。

## 6. 明确不做(本层)

- 不做任何外部通知通道、不加凭据(第二层);不做自动补跑 / 自动改匹配模式 / 自动换渲染策略(第三层)。
- 不把零产出运行改成 `failed`,不动用户源自动停抓阈值,不让 `silent` 影响个人早报 readiness 与选篇。
- 不做相对基线的正文判定;不回填历史 `discovered_count` / `inserted_count`(旧行 `None`,S1 只认新运行)。
- 不给 `singleton` 整页源判 S1 / S2(单条固定 id,产出恒 0)。
- 不做 interval 任务(分析 worker / 播客 landing 等)的收据与逐 tick 对账——它们没有逐 tick 落库,秒级迟到也无意义;只累计缺席计数。
- 不改持久时间口径为 UTC(独立波次);不做 DST(上海无 DST,枚举仍 aware,测试覆盖可换时区)。
- 第二层的通知投递模型(多通道 / 重试 / 静默期)不在本层建表。

## 7. 测试与验收

- `tests/test_watchdog_rules.py`:纯规则单测——S1(不同日 / 同日连点不计 / `None` 不计 / `unknown` 与 `incremental` 不判 / 指纹不同不计 / 世代刷新重开)、S2(阈值与护栏逐一反例 / 未分类 / occurrence 剔除 / 不自引用 / 恢复)、
  Q1(`unknown` 不进 / NULL 归一 / 滞回 / 策略变化 resolve)、C1(aware 枚举 / revision 截止 / coalesce 语义 / `receipt_time_unknown` 保守)、B1(排除本次 / 最小样本 / warm-up / 50%)。
- **生产回放金标** `tests/fixtures/watchdog_runs_2026-09.json`(近 92 天 `fetch_runs` 按源的 `started_at/status/trigger_type/fetched/saved` + 按 `fetch_run_id` 重算的 `inserted`,无正文):
  **只锁 S2**——断言 30 天回放恰好命中 `web_aiera@09-07`、`web_ithome_ai@09-13`、`rss_nvidia_genai@08-31`,其余 95 源零命中;改常数前后各跑一次(同 `eval_news_value_golden.py` 用法)。S1 用部署后的新运行另建 fixture。
- 失效注入:evaluator 抛异常 ⇒ 文章 / fetch run / 执行状态仍 success;调度器增改删采集任务后五类任务仍在且不重复(PR-0);`ScheduleOutcome` 三态与逃逸异常各写对收据;监听队列 flush 与 shutdown;`tz_mismatch` 下四类判定全部跳过。
- `test_data_lifecycle::test_source_health_state_path_and_fallback`(M13)保持不查 `fetch_runs`;`test_personal_brief_*` 断言 `silent` 不改变 readiness;`test_migrations` 漂移 + `source_id` 分阶段回填 + server default 断言;
  `test_retention` 加三表(未解决 occurrence 永不删);`test_archive_sync_v2` 滚动升级矩阵(old→new / new→old 载荷,`status` 逐字不变);`test_daily_brief` 三条路径同一 recorder、`run_id` 幂等。
- 端到端目检:隔离栈按 `isolated-acceptance-stack` 起,播种「列表正常但 14 天零新增」「两日 discovered=0」「正文偏薄」「缺一次收据」四种源 / 任务,信号灯 / 检视器 / 运行页 / 看护页四处同源;
  `page.on("pageerror")` 挂着过一遍所有加了枚举值的视图。

## 8. 落地顺序(可部署纵切)

0. **PR-0 调度重载修复**(现役 bug,独立小 PR,不等看护波):`load_tasks_to_scheduler` 差量同步 + 幂等注册 + 集成测试。
1. **PR-A schema 与上报通道(判定关)**:§4.1 通道与各族触点、§4.6 全部迁移(`fetch_runs` 三列 + 索引 / `source_states` 五列 / 三新表 / 两新列)、Sync 字段与双向测试、
   `/api/source-health` / `/api/source-states` / `/api/fetch-runs` 字段透出、**全部前端枚举消费者**(`silent` 灯位 / 状态章 / 用户源筛选 / `onViewRuns` 透传)——判定由 KV `watchdog_enabled` 关着,合入无行为变化。
2. **PR-B 开判定**:S1 / S2 内联评估 + 巡检兜底 + 恢复 + 「重置基线」;`watchdog_enabled` 默认开;金标回放;节点管理与用户源表的完整 UI。合入即出现静默信号灯。
3. **PR-C 调度、日报与看护页**:`guarded_schedule` + 收据 + 对账 + 监听器、Q1、`daily_brief_runs` + B1、`/api/admin/watchdog`、「看护」子页、运行页「零产出」格与「发现」列、`stats` 双 facet、日报面板提示。
4. 部署后观察两周:静默 / 未分类 / 正文偏薄名单与人工核对,误报 / 漏报记 §9,再定第二层的触发规则常数。

## 9. 检视记录

### R1(2026-09-15,codex `gpt-5.6-sol`,本地 Herdr 协商式;报告 `.review/report-codex-r1.md`,表态 `.review/response-r1.md`,答复 `.review/reply-codex-r1.md`)

总评:「方向对、形状要改;不能按 R1 稿直接实现」。17 条(6 P1 / 10 P2 / 1 P3),我方 13 条全盘接受、4 条部分接受,一轮答复即一致;无整条不接受。

**改变了方案形状的 P1**:
- F1 `load_tasks_to_scheduler()` 全量 `remove_all_jobs()` 被采集任务 CRUD 调用 → 看护巡检会被删,且暴露出现役 bug(§1 末条)→ PR-0 先行。
- F2 内联评估异常会把成功运行改记失败 → 判定与成功收尾隔离(§4.0-2 / §4.2)。
- F3 `silent` 进 `status` 会让个人早报把公共源当未完成、且失败与静默无法并存 → 独立产出轴(§4.2,§5-1)。
- F4 / F5 `EVENT_JOB_ERROR` 看不到吞掉的异常、业务产物不是调度收据、`updated_at` 不是 schedule revision → `guarded_schedule` + `ScheduleOutcome` + `schedule_receipts`(`scheduled_for` 取自 `EVENT_JOB_SUBMITTED`,唯一键含 revision)+ coalesce 语义写死(§4.4)。
- F6 S1 会被旧行 / 同日手点 / 重启后陈旧历史误触发 → 取消 `None` 回落、不同日、观察世代 + 指纹(§4.2)。

**P2 全部采纳**:时区口径(F7)、身份冻结与复合索引与分阶段迁移(F8)、`discovery_mode` 默认 `unknown` 与 CSS `len(seen)` 与 singleton 分离(F9)、严格新增数与未分类与事故剔除(F10)、`body_expectation` 显式 opt-in 与 NULL 与滞回(F11)、
occurrence 模型与条件留存(F12)、`daily_brief_runs` 表与基线排除本次(F13)、stats 双 facet 与 `yield` alias 与 `onViewRuns` 透传(F14)、个人早报显式条款(F15)、三纵切 + feature gate + 滚动升级矩阵(F16)。P3 事实校正三处已改(F17)。

**四处分歧的结论**:
- S1 两条零候选「不同上海自然日、触发类型不限」(我方)vs「scheduled-only」(codex)→ 接受我方,附条件:同世代、同指纹、显式 0(preview / `test_limit` 排除)。
- 持久时间不改 UTC(我方,范围控制)→ 接受,附条件:时区自检 **fail-closed**(不是 warning 后继续判),未来迁移按上海解释 naive 值。
- 事故间隔用 resolved occurrence 剔除代替分位数(我方)→ 条件接受:occurrence 存真实条件区间(`condition_start/end`)、基线不自引用、unresolved 不假定剔除。
- `fetch_runs.fetcher_id` 形态:codex 称「新运行已多为逻辑 id」,核实为**两种形态并存**(source-config 节点是,用户源不是)——文档按并存改写,加 `source_id` 列的理由不变。

**codex 追加的六条形状要求**(全部采纳):`ScheduleOutcome` 契约(F4)、`scheduled_for` 来源与三元唯一键(F5)、`body_expectation` 持久化与 Sync 与失效(F11)、occurrence 快照 schema 与并发 upsert(F12)、`daily_brief_runs` 幂等 `run_id` 与收据关联(F13)、stats 双 facet 不重复计数(F14)。

### R2 对照复检(2026-09-15,codex `gpt-5.6-luna`;清单 `.review/recheck-r2.md`,结果 `.review/recheck-codex-r2.md`)

17 项对照:15 项「已落实」;2 项是 R2 文本自身引入的矛盾,已就地修正、不再开新一轮:
- F5:§4.4 写了监听器「只」注册 `EVENT_JOB_MISSED`,却依赖 `EVENT_JOB_SUBMITTED` 取 `scheduled_for` → 改为两事件均注册、各司其职,`EVENT_JOB_ERROR` 明确不注册。
- F12:occurrence 活跃期唯一索引含可空的 `scheduled_for`,SQLite 下多个 NULL 互不冲突 → 列改 `NOT NULL DEFAULT ''`,非 missed 类恒空串。

流程备注:codex 启动时本周额度 < 5%,首轮报告后自动降档到 `gpt-5.6-luna`,答复轮与复检轮在降档模型下完成;轮询脚本在 zsh 下不能用 `status` 作变量名(只读)。
