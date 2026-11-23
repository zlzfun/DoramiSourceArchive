# 播客:媒体边界、成本门控与自动入队

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:[`contracts/podcast_provider_ports.md`](../contracts/podcast_provider_ports.md) · [`sources/podcast_catalog.md`](../sources/podcast_catalog.md) · [`plans/issue-97-podcast-cost-and-scoring.md`](../plans/issue-97-podcast-cost-and-scoring.md) · [`podcast-transcript-dedup.md`](./podcast-transcript-dedup.md) · 归档 [`archive/podcast-wave-plan.md`](../archive/podcast-wave-plan.md)

## Podcast media boundary (Issue #7)

Podcast RSS uses `source_type=podcast` and `fetcher_id=generic_podcast_rss`. Episodes remain `ArticleRecord` rows with `content_type=podcast_episode`; podcast-specific fields live in `extensions_json` and are projected as a lightweight top-level `podcast` object. Feed fetching never downloads enclosure audio or transcript bodies, and the Reader plays the publisher `audio_url` directly. When ASR needs source audio, the default path submits that RSS URL directly to Aliyun; a local download exists only in temporary staging for safety/media validation, is deleted after validation, and leaves a lightweight `source_media_snapshot` rather than a persistent audio artifact. Only when Aliyun explicitly reports one of four RSS download failures may the worker perform one exact-snapshot re-download and submit a short-lived private OSS URL; the object is deleted at terminal state with a one-day lifecycle backstop. Decoder/format failures and local relay failures never trigger another submission. `PodcastArtifactStore` persists generated `digest_audio_zh` only; its quota, disk-reserve, publish/withdraw and Archive Sync behavior do not apply to original audio. No public original-audio route is part of the architecture.

## Podcast 成本门控现行策略(issue #97，覆盖下段 issue #68 的旧 5.0 资格/扫描描述)

：发布方逐字稿是独立的零 ASR 全文候选，不看简介分（缺失/失败也可）；landing 增量游标与 sweep 同时纳入带 publisher locator 的单集。官方稿不可用时才按简介初评 `>= 6.0` 决定是否允许付费 ASR 回退。全文终评 `>= 7.5` 才默认获得优质/TTS 资格，优质线仍可由运行时 KV 调整且不覆盖存量值。评分提示词暂不改，待六组 32 输入黄金集与来源鲁棒性对照完成。详见 `docs/plans/issue-97-podcast-cost-and-scoring.md`。

## Podcast 全文处理自动入队(landing,issue #68 返修)

简介初评 ≥ 5.0 的播客集由 `execute_podcast_landing_job`(独立 APScheduler interval 1 min,`max_instances=1`,并发上限 3)自动送入全文处理,**不再**由 `execute_article_analysis_job` 每分钟全表重放。纯逻辑在 `services/podcast_landing.py`,三层防线:① **复合 keyset 游标** `podcast_landing:cursor`(`(updated_at, article_id)` JSON,旧纯时间戳游标自动收养)只扫新更新的 succeeded 播客分析行 + **轮转 sweep 游标** `podcast_landing:sweep_cursor` 每轮再看 40 行全量(只做 DB 读),兜住不改分析行但改变输入 revision 的变更(RSS transcript locator / publisher transcript 发布 / source-media snapshot / archive sync 收养);② **失败记忆** `podcast_landing:attempts` 按 episode 记 revision/次数/类别/`retry_at`——瞬时失败 5 min 起指数退避封顶 6 h、8 次后长停;确定性失败(媒体不合规/过长/not_found)24 h 后重核或 revision 变化即放行;**守门类**(`podcast_provider_unavailable`/`StorageFull`/`PodcastLandingGated`)是配置或容量问题,10 min 回访;**入队成功记 `enqueued` + 重算后的最终 revision**(同 revision 永不再入队,豁免 30 天 prune,对账为 settled 才清除);每个 id 都解析为 eligible/settled/failed,到期但已不合格的条目当轮清除;每条结果**立即 checkpoint**,游标只在轮末推进;③ **轮级守门** `podcast_landing_gate`:运行时未就绪整轮跳过不动状态;需要下载源音频的集在 ASR **新提交 admission**(`stage_admission_ready`,比轮询就绪严格:app_key + 计费配置)未就绪、或产物库可预留余量(`reservable_bytes` = 配额余量 ∧ 磁盘 minimum-free 线之上余量)不足本集 `download_limit` 时记 `gated`。自动入队的幂等键由 `request_processing` **在锁内**按实际选中输入派生(`full-analysis:auto:<episode>:<content_hash[:16]>`,`idempotency_key_prefix` 形态),重启重算同输入即 replay 同一记录,不撞 conflict;手动 API 仍传显式 key。`enqueue_podcast_processing_with_input`(手动 API 同路)publisher transcript 只试一次,失败后进源音频回退前**再次**检查 admission + 本集容量,不满足抛 `PodcastLandingGated`(503);同步 DB/入队/ffprobe 全部经 `asyncio.to_thread`。cron 类任务(`add_cron_job`)统一 `misfire_grace_time=300`+`coalesce`——2026-09-14 生产实录:默认 1 s 宽限撞上一次事件循环阻塞,日报与四个采集任务整天缺席。


## 精品导读候选巡检的读路径(issue #318)

`pending_premium_guide_candidates` 把全部「有逐字稿分析」的单集过一遍,由播客 worker 在一拍结束后调用。两条约束:

- **不取排在正文之后的列。** `podcast_text_artifacts` 里只有 `id / episode_id / kind / version / content_hash` 排在 `inline_text` 之前;`language / authority_id / source_artifact_id / source_content_hash / provenance_json` 都在其后,SQLite 要沿溢出页链走完整段逐字稿才读得到——`load_only` 省的是内存,不是读盘(v3.70.0 实测一拍请求读取 340MB)。主查询只取前三列;尾部列经 `_artifact_tails` 在进程内缓存(按引擎分开、上限 8192 条),每份产物只走一次溢出页链。缓存键是 (id, `authority_id`):行不可变只管 UPDATE,单集删除后经同步原样导回会得到同 id、不同所属方的产物;`authority_id` 每拍用 `INDEXED BY uq_podcast_text_artifacts_authority_slot` 从覆盖索引现取(不点名的话 SQLite 走主键索引再回表,回表就要走完正文)。
- **不跟着 10 秒一拍跑。** `_podcast_premium_scan_due`:60 秒一次;本拍 worker 有非 `idle` 动作时立即巡检。

`articles` 同理:`extensions_json`、`analysis_authority_id` 等排在 `content` 之后。新增高频查询前先看 `PRAGMA table_info` 的列顺序。

## 精品导读语音的预算记账(issue #322)

`bailian_tts_calls` 一段一行,状态 `reserved → authorized →(POST)→ generated → succeeded`,被供应商明确拒绝是 `rejected`(费用 0)。月度预算合计 = 当期全部行的 `cost_minor` + 任何月份里仍是 `reserved / authorized / generated` 的行。

- **只有可能已经花出去的调用才长期占预算。** `authorized` / `generated` 结果未知或已计费:跨月仍计入,并拦住同一期节目的后续合成直到对账。`reserved` 一定没发出去过(它写在 `authorized` 之前,`authorized` 写在 POST 之前)。
- **合成没走完,没发出去的分段解除占用。** `_synthesize` 的异常出口与 `_reserve`(持回执目录锁,清同账号下不属于本次合成的)都会把这类行的 `cost_minor` 记 0。**行不删、状态不改**:`reserved` 且占用额为 0 就是「已解除」。
- **再合成同一份口播稿时重新计价。** `_reserve` 把本次要复用的旧 `reserved` 行按现在的账期与价格重新占用;不这样做,占用额为 0 的行会让预算检查少算。
- **月度预算满是等待,不是失败。** `tts_monthly_budget_exceeded` → 导读 `retry_wait`,6 小时后候选巡检重新拉起,不设上限;文字复用,预算检查在发请求之前,重试不花钱。`tts_per_run_budget_exceeded` 取决于稿子本身,是终态。

排查入口:`bailian_tts_calls` 按 `budget_period, status` 汇总 `cost_minor`(`reserved` 的合计长期不为 0 说明又有预留被丢下);日志搜 `因语音预算等待重试` / `未发出的预留没能解除`。

## 全文分析的租约路径(issue #318)

`run_full_analysis_worker_step` 的顺序与三条约束:

1. **认领后立刻建 attempt,用当下的时间校验租约。** 此后的一切耗时都记在这次 attempt 名下。到这一步已失租 → 记日志、返回 `lease_lost`,不发模型调用。
2. **重活在续租保护之内。** 准备输入(召回标签、话题热度)放进工作线程、用自己的 `Session`,与分段 / 汇总调用一起包在 `run_under_lease` 里。续租得到 `PodcastLeaseLost` → 立刻取消在途工作,失租的执行者不做任何回写;别的续租异常(写锁)只记日志、下一拍再续。
3. **连续失租有界。** 重领路径按「此前连续多少个本地 attempt 以租约到期收场」退避:第 1 次立即重领,之后 60 秒 / 5 分钟 / 15 分钟 / 60 分钟(`LOCAL_LEASE_EXPIRY_BACKOFF_SECONDS`),第 6 次处理单转 `failed`(`lease_expired_retry_exhausted`)等管理员重试。记账的是持有新租约的重领方;只数最近一次被接受的管理员重试之后的 attempt。
4. **落定先过 fence。** `_finalize_result` 的第一条语句是带完整 fence 的条件 UPDATE:校验租约,并让事务从这里起持有写锁;不成立抛 `PodcastLeaseLost`。先读后写、在 Python 里比对租约是拦不住读写之间的重领的。

自动入队不为在途处理单自己的 ASR 产出的转写再建一单(`live_owner_of_transcript`):那一单会自己接着分析。保证在正式入队处(`request_processing` 持锁、输入最终选定之后复用拥有者);巡检侧(`_resolve_podcast_landing_candidate`)的同类判断只是省一次入队,且有待刷新的发布方逐字稿时不拦。

排查入口:`podcast_stage_attempts` 按 `error_code` 计数(`lease_expired_local_attempt` 是单次租约到期,偶发属正常——进程重启就会有);日志里 `Podcast 全文分析失去租约` / `连续 N 次租约到期` 带耗时与处理单 id。

## 原稿进入问答（issue #248）

站内问答消费当前已发布的发布方原稿和 ASR 原稿，按阅读投影懒建本地片段索引，
可回答单集与订阅范围内的问题，并提供本次实际使用的原文片段。中文译稿与 AI 导读本波不作为
问答证据；只有简介时明确降级。发布/撤稿沿现有指针生效，回答前后复核，问答不触发 ASR/TTS。
字符范围只用于核对阅读文本，不生成音频时间戳。见 [检索实现](./search-and-qa.md) 与
[#248 实现记录](../archive/issue-248-podcast-qa.md)。
