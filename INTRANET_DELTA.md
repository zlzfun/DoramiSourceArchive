# 内网差异清单(INTRANET_DELTA)

> **本文件只存在于 master 分支(内网适配分支)及其下游的内网托管仓,main 上没有。**
> 它是「内网相对公网主线 main 到底改了什么」的唯一权威登记簿。
> 上游同步走 merge 而非 rebase(内网仓多人协作、不重写历史),所以差异面不能靠 git 历史
> 一眼读出——必须靠本清单人工维护。**新增内网改动、合并上游版本,两件事都要更新本文件。**

## 0. 当前状态

| 字段 | 值 |
|---|---|
| `UPSTREAM_BASE` | `v3.70.4` / main `732e4e1db46aabb3a94e683e00ed2629ce8921d1`(2026-11-24 正式发布,自动同步合入,见下) |
| 本清单最近核对 | 2026-11-24(GitHub 侧 master;内网托管仓独有差异仍待内网 Agent 补登,见 §4) |
| 同步方式 | GitHub `Sync main → master` 工作流,**按 main 的版本 tag(`v*`)触发**,`--no-ff` 合并;冲突时任务失败、人工处理 |

每次合入上游版本后:更新 `UPSTREAM_BASE`,逐条复核 §2/§3 里「冲突原则」栏是否仍成立,
被上游吸收的条目移到 §5「已上游化」。

2026-11-24 正式版 `v3.70.4` 已自动无冲突合入(`07f3c287`)。本版三个 PR、两件事:
精品导读语音等回执锁时先排队(issue #334,PR #336,方案 `docs/archive/issue-334-tts-receipt-queue.md`)——同一进程里要拿回执目录锁的调用(容量预检、合成)先在协程层按到达顺序排队,轮到的那一个才进线程,语音这一段占用的共享线程固定为每个回执目录至多一个;此前多期导读同时到期会把默认线程池(2 核的机器只有 6 个线程)占满十几分钟,期间别的后台任务整片不跑;
源的身份收口到源目录(issue #262 R1a 余项 PR #335、R2 首块 PR #337,方案 `docs/plans/issue-262-source-model-refactor.md` §12 末)——业务代码读源的名称 / 角色 / 形态统一经 `services/source_catalog`;62 个预设源的名称、图标、简介、形态、准入、策展维度从抓取器类属性搬进 `src/sources/catalog/<id 前缀>.toml` 六个文件,类定义时回投到类属性,读类属性的代码不变。对外接口与同步流在改动前后逐项对拍零差异。
后端新增 `src/fetchers/catalog_files.py` 与 `src/sources/catalog/*.toml`,改动 `src/api/sources.py`、`src/api/routers/` 下 `ingest` / `x_api`、`src/fetchers/` 下 `base` / `registry` 与六个 `impl` 文件、`src/mcp_server.py`、`src/services/` 下源相关的十余个文件与 `bailian_tts` / `podcast_premium_guides`、`scripts/ensure_daily_collection_job.py`。**§2.1 的 8 个适配文件里有两个上游这次也改了**(`src/fetchers/base.py`、`src/api/routers/x_api.py`),自动合并无冲突,两处 `verify=settings.network.tls_verify` 都在;没有新增 httpx client;
**本版无新迁移**,Archive Sync 的流、字段、枚举、导出范围与接收端进度都没动,部署脚本未动;相对上一版,内网源码差异(8 个适配文件 + `scripts/repair_intranet_briefs.py`)逐行对拍一致。
内网接手注意:① 无迁移,要回上一版不需要恢复库;
② **内网托管仓若自己加过预设源**(`src/fetchers/impl/` 下带 `source_id` 的抓取器类):本版起预设类的类体里不能再写名称 / 图标 / 简介 / 形态 / 准入这些身份属性,每个预设类要在 `src/sources/catalog/<id 前缀>.toml` 里有一条同 id 的 `[[source]]`,准入必填、没有整批默认值。目录结构有错或预设类缺条目,**进程启动即失败**(`CatalogError`),不会带着「未命名」的源起来。合并后先跑 `tests/test_source_catalog_files.py` 再部署;
③ 内网托管仓若有代码直接 `import fetcher_registry` 读源的名称 / 角色 / 形态:守卫 `tests/test_source_catalog_boundary.py` 的名单只许减,名单外的文件引用注册表会红,改走 `services/source_catalog`(执行层只许 `get_class`);
④ 目录文件在 `src/sources/catalog/`(只有 `.toml`,不是 Python 包),裸机部署随 `src/` 一起带上。自己打包或拷贝部署的,确认这个目录没有漏掉,漏了启动报「目录文件缺失」;
⑤ 依赖:`pyproject.toml` 新增条件依赖 `tomli`(只在 Python 3.11 之前安装,3.11 起用标准库 `tomllib`);`docker/requirements*.txt` 只多了一处 `# via` 注释,包集合没变(`tomli` 原本就因 alembic 在清单里),裸机部署照常按清单装;
⑥ 语音排队那处修复只在跑语音合成的节点上生效,内网不跑,行为不变。语音提供方协议 `PremiumGuideTtsProvider` 新增可选的协程 `check_capacity`,导读流程不再调用同步的 `ensure_capacity`——内网托管仓若有自写的语音提供方或在测试里给 `ensure_capacity` 打桩,改为提供 / 打桩协程 `check_capacity`;
⑦ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-24 正式版 `v3.70.3` 已自动无冲突合入(`053c6883`)。本版三件事:
W3 个人日报机对机接口(PR #329,契约 `docs/contracts/admin_reader_briefs.md`,冻结基线 `contracts/briefs-v1/shared/`)——新增管理员 Cookie 鉴权的 `GET /api/admin/reader/briefs/today` 与 `POST /api/admin/reader/briefs/today/ensure`,按 `users.w3_uid` 精确唯一映射账号;首版日报的创建经共享互斥门,由新的进程内运行器 `BriefRuntime` 带租约执行;原有的个人早报待处理任务 `personal_digest_pending` 从每分钟改为每 30 秒,并兼做卡住版本的租约恢复;
精品导读语音的回执缓存(issue #326,PR #327,方案 `docs/archive/issue-326-tts-receipt-eviction.md`)——缓存满时在压缩之后按从旧到新逐出已有成品的节目的回执音频,留空的 `<id>.evicted` 标记,每日备份认标记;被逐出后同一份口播稿再出音频时改用完好的成品;
每日备份的空间判定与重试间隔(issue #330,PR #331,方案 `docs/archive/issue-330-backup-space-preflight.md`)——做库快照之前先算清这一轮要暂存多少,空间不够直接失败、不拷任何文件;连续失败的重试间隔从十分钟翻倍到最长 6 小时;失败与成功各写一行日志(`dorami.backup`)。
后端新增 `src/api/routers/admin_reader_briefs.py`、`src/services/briefs_contract.py`、`src/services/briefs_runtime.py`,改动 `src/api/app.py`、`src/api/serializers.py`、`src/models/db.py`、`src/api/routers/` 下 `accounts` / `personal_briefs` / `podcasts`、`src/services/` 下 `accounts` / `personal_digest` / `personal_digest_titles` / `bailian_tts` / `podcast_premium_guides` / `storage_backup`,不涉 §2.1 的 8 个适配文件,没有新增 httpx client;
**本版带两条迁移**(`3cfd474ffd83` → `9b7e2c4d1f60` → `b8d4107c2f61`):`users` 加可空列 `w3_uid` 与唯一索引 `uq_users_w3_uid`;`personal_digest_editions` 加 `generation_attempt_count`(非空,默认 0)与四个可空文本列。都是加列,外网 1GB 的库上启动时几秒跑完。Archive Sync 的流、字段、枚举、导出范围与接收端进度都没动,部署脚本未动;相对上一版,内网源码差异(8 个适配文件 + `scripts/repair_intranet_briefs.py`)逐行对拍一致。
内网接手注意:① 带迁移,要回上一版须恢复升级前的库备份(旧代码不认识新的迁移版本号,起不来);
② `9b7e2c4d1f60` 在库里已有 `users.w3_uid` 列时不重复加列,但会先把空白值置空、再查重复——有重复就抛错、不建唯一索引。内网托管仓若已自带这一列,升级前先跑一遍 `SELECT w3_uid, COUNT(*) FROM users WHERE w3_uid IS NOT NULL AND w3_uid != '' GROUP BY 1 HAVING COUNT(*) > 1` 并清掉重复;
③ 新接口只按 `users.w3_uid`(带首字母的 W3 账号,如 `l00976966`)匹配,**不**匹配内网 SSO 的纯数字工号列 `w3_employee_number`,也不回退到 `username`。内网托管仓合并时不要把它改绑到工号列;SSO 建号流程要把 `w3_uid` 写上(管理面创建 / 修改账号的接口已带这个字段),否则 bot 来取时是 `404 / ACCOUNT_NOT_FOUND`;
④ 个人早报的后台节奏变了,**内网有真实读者,升级后留意**:`personal_digest_pending`(只在开了读者面的节点)从每分钟一次改为每 30 秒一次,语义从「立即生成待处理版本」变为「带租约与宽限地恢复卡住的 pending / generating 版本」;首版的生成改由 `BriefRuntime` 受监督执行(240 秒预算、300 秒租约、60 秒续租)。`BriefRuntime` 在每个节点启动时都会创建(不看运行角色),应用关闭时等它收尾;
⑤ 回执缓存与每日备份两处修复只在跑语音合成 / 开了 `[backup]` 的节点上生效。开了备份的内网节点升级后:`status.json` 多两个键 `consecutive_failures`、`retry_seconds`(旧版本读到会忽略);余盘不够时不再先拷后败,日志每十分钟一行 `备份失败: backup_insufficient_space`;其余原因连续失败时重试会变稀,修好后可手动 `python scripts/storage_backup.py create` 立刻试一次。门槛算法见 `docs/ops/storage-backups.md`「余盘门槛与重试间隔」;
⑥ 导读状态随文章同步(`extensions_json.premium_guide`):本版新增一个 `error_code` 取值 `tts_cached_audio_evicted`(状态仍是既有的 `failed`)。读者面只按状态判断、不看错误码,内网旧版本显示为普通的生成失败;
⑦ 发生过逐出的外网节点,其每日备份归档里会有 `receipts/<id>.evicted`,旧版本的恢复工具不认。内网若要拿外网的归档做恢复演练,用本版的 `scripts/storage_backup.py`;
⑧ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-23 正式版 `v3.70.2` 已自动无冲突合入(`454d2807`)。本版两件事,都在播客的付费处理路径上:
精品导读语音的预算记账(issue #322,PR #324,方案 `docs/archive/issue-322-tts-reservations.md`)——一次合成里没发出去的分段不再占着月度预算(账本行保留、占用额记 0,再合成同一份口播稿时重新计价),授权一步自己失败关闭,月度预算满时由候选巡检拉起的运行等待后重试;
源音频校验(issue #323,PR #325,方案 `docs/archive/issue-323-source-media-cap.md`)——下载上限用配置的 `max_audio_mb`,订阅源声明的大小只作预留参考;量过的「过大 / 过长」记在校验这一步,同一地址、上限仍容不下时不再重复下载。
后端动 `src/api/app.py`、`src/api/routers/podcasts.py`、`src/models/db.py`(只改了一段说明文字,表结构未变)与 `src/services/` 下四个文件(`bailian_tts` / `http_safety` / `podcast_premium_guides` / `podcast_source_media`),不涉 §2.1 的 8 个适配文件,没有新增 httpx client;
**本版无新迁移**,Archive Sync 的流、字段、枚举、导出范围与接收端进度都没动,部署脚本未动;相对上一版,内网源码差异(8 个适配文件 + `scripts/repair_intranet_briefs.py`)逐行对拍一致。
内网接手注意:① 无迁移,要回上一版不需要恢复库;
② 两处修复只在跑语音合成 / 付费转写的节点上生效,内网不跑这两条,行为不变。新增的 KV 键 `podcast_source_media:rejections` 是节点本地的,不同步,也只在音频校验运行时才会写;
③ 导读状态随文章同步(`extensions_json.premium_guide`):外网上因语音预算等待的导读是 `status = retry_wait`、`error_code = tts_monthly_budget_exceeded`,`error` 是一句中文说明。`retry_wait` 与 `error_code` 都是既有取值,内网读者面会把它显示为「生成中」(与回执容量等待一致)。外网上线后会有一批此前「合成失败」的导读陆续变为就绪;
④ `services/http_safety.py` 新增错误类型 `PublicDownloadTooLarge`(`PublicDownloadError` 的子类,文案仍是「下载响应超过大小上限」)。内网托管仓若有代码按这句文案判断超限,仍然有效;新代码按类型判断;
⑤ 几个函数多了仅限关键字的参数:`app.schedule_podcast_premium_guide(..., budget_wait=True)`、`fail_premium_guide` / `run_premium_guide(..., budget_wait=False)`、`validate_source_media(..., use_rejection_memory=True)`。内网托管仓若调用或在测试里打桩这几个函数,签名要跟上;
⑥ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-23 正式版 `v3.70.1` 已自动无冲突合入(`56c97b06`)。本版只有一件事:播客处理的内存与读盘根治(issue #318,PR #319 / #320,取证与方案见 `docs/archive/issue-318-podcast-memory-io.md`)。
读路径:标签热度查询只取聚合要用的小列并在 SQL 侧按日期粗筛、精品导读候选巡检不再每拍读全部转写(每 60 秒一次,worker 有进展时立即)、语音回执回收不再整表加载文章、interval 类周期任务加迟到宽限(一个周期,积压合并成一拍)。
租约路径:全文分析认领后立刻建 attempt、重活期间照常续租、失租立即取消分段调用;连续失租有界退避,到上限转终态(错误码 `lease_expired_retry_exhausted`);自动入队不为在途处理单自己产出的转写再建一单。
后端只动 `src/api/app.py` 与 `src/services/` 下六个文件(`article_analysis` / `bailian_tts` / `podcast_full_analysis` / `podcast_premium_guides` / `podcast_processing` / `podcast_processing_admin`),不涉 §2.1 的 8 个适配文件,没有新增 httpx client;
**本版无新迁移**,Archive Sync 的流、字段、枚举、导出范围与接收端进度都没动,部署脚本未动;相对上一版,内网源码差异(8 个适配文件 + `scripts/repair_intranet_briefs.py`)逐行对拍一致。
内网接手注意:① 无迁移,要回上一版不需要恢复库;
② 这次的问题(后端进程涨到几个 GB、磁盘长期读满)出在跑播客转写 / 全文分析的节点上;内网只在本机做播客简介初评或全文分析时才走热度查询那一处,否则这部分行为不变。对内网最直接的变化是 interval 类周期任务的迟到宽限从默认 1 秒放到一个周期——事件循环被拖住几秒时,`was missed` 告警会少很多;
③ `tests/test_scheduler_reload.py` 新增一条读 `src/api/app.py` 源码的守卫:文件里每处 `add_job(…, "interval", …)` 都要带 `**interval_job_options(...)`。内网托管仓若在 `app.py` 里自加过 interval 任务,这条测试会红,接上统一选项即可;
④ `tests/conftest.py` 新增 `captured_log` 夹具和一个自动复位巡检节流状态的夹具;断言日志一律用 `captured_log`(原因见 `docs/dev/testing.md`:迁移的日志配置会关掉已存在的记录器,`dorami.*` 记录器也不向上传播)。内网托管仓若改过 `conftest.py`,合并时保留两边;
⑤ 新增两个日志记录器 `dorami.podcast_full_analysis` / `dorami.podcast_processing`(失租与各失败出口的日志),此前这两条路径不打日志;
⑥ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-20 正式版 `v3.70.0` 已自动无冲突合入(`cfec07ce`)。本版三块:令牌交付计量(issue #312:带 `dfeed_` / `dsub_` 令牌取内容的每次成功调用按「日 × 账号 × 令牌 × 接口」记进新表 `token_delivery_usage`,
运维 → 账户加「接口」列与抽屉里的「接口调用」区)、设置 → 聚合接口列全令牌能调的五个接口(issue #315,纯前端 + 一条读后端源码的对账测试)、公共日报逐批记录跨天去重判定(issue #313 / PR #314,新表 `daily_brief_dedup_runs`,只在生成日报的节点落数)。
后端不涉 §2.1 的 8 个适配文件,没有新增 httpx client,Archive Sync 的流、字段、导出范围与接收端进度都没动,部署脚本未动;相对上一版,内网源码差异(8 个适配文件 + `scripts/repair_intranet_briefs.py`)逐行对拍一致。
内网接手注意:① **本版带两条迁移**(`a313b6d9c1e0` → `3cfd474ffd83`,各建一张新表,不动既有表),部署时照常 `upgrade heads`;升级后要回上一版须先恢复部署前的库备份(旧代码不认识新 revision);
② 令牌计量各节点各记各的、不随 Archive Sync 传,内网升级之后的调用才开始有记录,此前的补不回来;计量走后台队列写库,升级后留意 `令牌交付计量` 开头的告警日志(写入失败、队列已满丢弃);
③ 前端 `SettingsModal.jsx` 改了一行(给聚合接口卡多传一个 `onOpenMcp`),`App.jsx` 未动;内网托管仓若在该文件同一处有改动,合并时保留两边;`src/services/accounts.py` 的删号路径多了一步「把计量并入墓碑名」,内网托管仓在账号上的适配请顺带复核;
④ `frontend/test/feedEndpoints.test.mjs` 读 `src/api/` 下的路由声明与清单对账——内网若在 `/api/public/feed/` 下自加接口,这条测试会红,需同步改 `frontend/src/utils/feedEndpoints.js`;
⑤ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-19 正式版 `v3.69.0` 已自动无冲突合入(`4fd1e3b7`)。本版两块:读者面桌面动效重构(issue #301,纯前端,31 个前端文件,`App.jsx` / `SettingsModal.jsx` 未动)
与热点事件榜的令牌化交付口(issue #310:`GET /api/public/feed/events`、`GET /api/public/feed/events/{event_id}`,凭 `dfeed_` 个人聚合令牌,契约见 `docs/contracts/reader_subscription.md`)。
后端只动 `src/api/routers/events.py` 与 `src/services/event_board.py`,不涉 §2.1 的 8 个适配文件,也没有新增 httpx client;相对上一版,内网源码和迁移差异保持一致。
本版无新迁移、无同步协议或接收端进度变更,部署脚本未动。内网接手注意:① 新端点要等内网部署本版后才有,此前这两条路径回 404;
② 榜单由各节点按本机数据现算,名次快照 `event_rank_snapshots` 不随 Archive Sync 传,内网与外网的名次 / `previous_rank` 不保证一致;
③ 内网手动部署(`./deploy.sh --here`);重启 PM2 仍会中断正在运行的同步,若需保留同一任务,应待其结束再切换。

2026-11-18 正式版 `v3.68.1` 已自动合入（`664e30f1`）。发送端加速图片公开引用核验；相对上一版，内网源码和迁移差异保持一致。本版无新迁移、无同步协议或接收端进度变更。内网手动部署；重启 PM2 仍会中断正在运行的同步，若需保留同一任务，应待其结束再切换。

2026-11-17 正式发布 `v3.68.0` 后,`Sync main → master` 自动无冲突合入发布提交(合并节点 `a87604bb`),
在下述预同步基础上仅将 `src/version.py` / `pyproject.toml` / `uv.lock` 根包版本由 `3.68.0-alpha` 收口为 `3.68.0`。
§2/§3 差异面与冲突原则仍成立,原有 8 个源码文件的内网适配未变;没有新增业务代码或迁移。
GitHub 发布 / 部署流水线只覆盖外网 Docker 节点,内网接收包含本次合并的 master 后仍按 `./deploy.sh --here` 手工部署,
不要直接指定公网 `v3.68.0` tag,以免丢失内网适配。以下预同步验证及升级接手注意继续适用。

2026-11-17 按用户要求提前合入最新 main `74824899`(`3.68.0-alpha`,尚未正式发版):
GitHub master 此前已由自动流水线合入 `v3.67.1`～`v3.67.4`,实际起点为 `d60ad46b`;本清单原写 `v3.67.0` 是登记滞后,本次补正。
本次在独立 worktree 自最新 `origin/master` 执行 `git merge --no-ff origin/main`,无冲突;原有 8 个 `src/` 文件中的内网 TLS 开关与接线完整保留,
前端、部署脚本与迁移文件均采用 main。§2/§3 登记的差异面及冲突解决原则仍成立,没有新增内网功能或改动内网托管仓的 SSO。
此次只同步代码,不创建 tag、不部署;正式 tag 推送后,既有 `Sync main → master` 流水线仍会合入后续发布提交。
验证:后端全量累计 3,894 项通过 / 1 跳过(其中 31 项因沙箱禁止本机端口或进程检测先失败,解除对应环境限制后全部复验通过);
前端 lint / 398 项测试 / 构建通过;文档守卫 0 问题。内网适配 8 个源码文件的增删内容与原 master 逐项对拍一致。

内网接手注意:① 源模型、收录入口、同步合并规则及读者界面更新已包含在内;从实际部署的 `v3.67.0` 升级需执行全部 7 个新增迁移(`upgrade heads`),
GitHub master 自 `v3.67.4` 则新增 5 个;内网 SSO 迁移支线继续并存。② 接收节点迁移关闭公共源本机分析、含公共源的采集任务和公共日报定时,
保持内网只消费上游的现状;个人早报继续工作。③ 两端升级并完成来源目录 `catalog-v1` 补拉后,再使用新增收录 / 推送入口;
旧 `POST /api/articles` 返回 410,`POST /api/import/social-posts` 已移除,外部写入调用方改用 `POST /api/public/ingest/v1/items`。
④ 内网部署应使用含适配的 master / 内网 tag,不要直接部署公网 main tag;本次同步不是正式发布。

2026-09-16 已无冲突合入 `v3.58.2`(过期分析撤回与评分同步修复,issue #98 / PR #99;
含 `v3.58.1` 的 JSONL Unicode 分隔符修复,issue #95 / PR #96),
复核 §2/§3 的差异范围与冲突原则仍成立,保留 TLS 开关及原有接线。
内网接收最新 master 后运行 `./deploy.sh --here` 部署当前适配分支;默认部署或指定
`v3.58.2` 会切到 main 的发布 tag,不含本分支适配。
本次评分修复在外网导出端生效,内网 `v3.58.1` 接收端已兼容撤回协议,可先直接重试 v2 同步。

2026-09-22 合入 `v3.61.0`(裸机部署回滚 #126 / 下游身份源接入点 #130 / 跨平台文件锁 #133 / 时间炸弹用例热修 #135):
此前三段「预合入」记录对应的内容已随 tag 正式进入 main,三段删除;冲突只在 `CLAUDE.md` 年表与 `docs/version-history.md`
(按 §1.4:采纳 main、保留顶部须知块),复核 §2/§3 差异范围与冲突原则仍成立。内网接收后 `./deploy.sh --here`
(已是 release 形态,不再收养);#130 的内网配套(SSO 迁移加 `branch_labels`、拆 `passwordLoginable` 穿线、§4 补登)见 §4 待办。

2026-10-26 合入 `v3.67.0`(事件层 wave 1 / wave 2 #220 #222 #224 #226、游客模式 #213 正式进 main、R2 标题翻译 #225、OpenAI 图片修复 #219、文档整理波 #210):
GitHub `Sync main → master` 工作流冲突失败(19 个文件),在 worktree 人工 `git merge --no-ff v3.67.0`。此前「预合入 #213」一段对应的内容已随 tag 正式进入 main,删除;
冲突里的 `src/` / `frontend/` / 测试 / 版本文件全部取 main(本分支相对 v3.66.0 的改动只有 #213 预合入,被 main 的正式版覆盖,TLS 接线不在冲突文件内、原样保留);
`AGENTS.md` / `CLAUDE.md` 取 main 的新 L0 再补回顶部须知块;`docs/version-history.md` / `docs/backlog.md` / `docs/frontend/conventions.md` / 游客样页取 main;
`docs/README.md` 取 main 的新索引并在末尾加「内网(仅 master 分支)」一节登记本分支独有文档;`docs/archive/issue-213-guest-mode.md`(预合入带来的副本)删除,
以 main 的 `docs/archive/issue-213-guest-mode.md` 为准。复核 §2/§3 差异范围与冲突原则仍成立。
内网验收注意:① 版本号 `3.67.0`;新迁移六个(`660028d4ab6f` 游客、`ed530f8224f9` → `d5b8e2a7c913` 事件层四个、`a218e6b9c402` 标题翻译)沿 `upgrade heads` 与内网 SSO 迁移支线并存,不要 merge head;
② 事件层:内网 replica 升到本版后 Archive Sync 多拉一条 `events` 流(能力 `events_v1`),早报同事件去重即生效;外网 authority 已于 2026-10-26 上线 v3.67.0;
③ 游客总闸缺省开,不想开放到「运维管理 → 用户 → 游客」关掉;④ 内网登录页若有 SSO 定制,保留 `LoginScreen` 的 `guestEnabled` / `onGuestEnter` 与 `fromGuest` / `onBackToGuest` 两组入口;
⑤ 游客会话的 `password_login_enabled` 走 `auth_policy.password_login_enabled(None)`,内网按 `w3_uid` 的覆盖不影响游客。

## 1. 维护规则

1. **上游优先**:凡是通用能力(不含内网机密、不含内网环境专属妥协)一律先提 main,
   再随版本同步下来;本清单只登记**真正无法公开或无法通用**的改动。先例:裸机 PM2 部署
   路径曾是内网专属,v3.39.0 证明它服务的是「宿主没有 Docker」而非「内网」,已回迁 main。
2. **一条一个关注点**:按「关注点」而非按文件登记(SSO 是一条,TLS 网关是一条),
   同一关注点涉及的文件列在条目内。
3. **每条五栏**:关注点 / 涉及文件 / 为什么必须在内网分支 / merge 上游的冲突解决原则 /
   能否上游化(能:写明阻碍;不能:写明原因)。
4. **冲突解决的总原则**:`src/` 以 main 的演进为准,再把本清单登记的接线补回去;
   部署面文件(`deploy.sh`/`ecosystem.config.js`/ini 示例)以 main 为准;
   `CLAUDE.md`/`AGENTS.md` = 保留顶部须知块 + 采纳 main 的其余更新;本文件永远以本分支为准。
5. **禁止反向**:本清单里的任何东西都不得 merge/cherry-pick 回 main;要上游化就在 main
   重新实现一个公网姿态安全的版本(见 §5 的先例)。

## 2. 代码与配置差异(GitHub 侧 master 已登记)

### 2.1 出网 TLS 校验全局开关

- **涉及文件**:`src/config.py`(`NetworkConfig.disable_tls_verify` + `tls_verify` 属性 +
  `load_config` 读取 `[network] disable_tls_verify` / `DORAMI_DISABLE_TLS_VERIFY`);
  7 处 httpx client 接线 `verify=settings.network.tls_verify`:`src/fetchers/base.py`、
  `src/fetchers/web_content/legacy_backend.py`、`src/services/media_store.py`、
  `src/services/remote_sync.py`、`src/services/source_builder.py`、`src/api/routers/x_api.py`、
  `src/llm/client.py`;`config/production.example.ini` 的 `[network]` 节 4 行说明。
- **为什么在内网分支**:内网出网被企业网关 MITM(自签证书链重签),httpx 校验必失败。
  用户拍板不进 main:公网开它等于自毁,「默认关闭无影响」也不例外(2026-07-24)。
- **冲突原则**:main 改动这些文件时以 main 为准,**再把 `verify=` 参数补回**;main 新增任何
  httpx client 时同步补接线(这是本条最常见的冲突来源,见 2026-09-08 播客波 `remote_sync`)。
- **能否上游化**:能,但要换形态——httpx 0.28 原生读取 `SSL_CERT_FILE`,若内网能导出网关 CA
  并合成 bundle,只需部署层设环境变量,7 处接线与开关可整体删除,校验也不必关闭。
  阻碍:内网是否能拿到网关 CA 未验证。

### 2.2 内网部署姿态(ini,不入库)

- **涉及文件**:各机 `config/production.ini`(不入库),相对公网姿态反转两处:
  `[network] disable_tls_verify = true`、`[auth] cookie_secure = false`(纯 HTTP + IP 访问)。
- **冲突原则**:无(文件不入库);`production.example.ini` 以 main 为准 + 保留 `[network]` 说明。

## 3. 文档与工具差异(GitHub 侧 master 已登记)

| 关注点 | 涉及文件 | 说明 / 冲突原则 |
|---|---|---|
| 分支须知块 | `CLAUDE.md` 顶部块、`AGENTS.md` 顶部块 | 分支纪律;冲突时保留块 + 采纳 main 其余更新 |
| 会话提示 hook | `.claude/settings.json`、`.claude/hooks/master-session-start.sh` | 开工注入落后主干提示;main 无对应文件,不冲突 |
| IM 机器人接入文档 | `docs/contracts/im_bot_integration.md`、`docs/im-bot-architecture.md`、`docs/README.md` 索引两行 | 纯内网场景文档按拍板只进本分支;`docs/README.md` 冲突 = 两边索引都保留 |
| 部署文档内网注记 | `docs/ops/deploy-docker.md`(内网 Docker 过旧的说明段) | 以 main 为准 + 保留注记段 |
| 内网发布与部署指南 | `docs/intranet-release-guide.md`、`docs/README.md` 索引 | 通用流程仅进 master;内网独立 tag、同版本双包、部署与社区发布;环境专属信息另存内网文档,索引冲突保留两边 |
| 内网日报修复脚本 | `scripts/repair_intranet_briefs.py` | 一次性运维脚本,main 无对应文件 |
| 本清单 | `INTRANET_DELTA.md` | 永远以本分支为准 |

## 4. 内网托管仓独有差异(待内网 Agent 补登)

GitHub 侧 master 只含上面两节;内网托管平台的 master 在此之上还有内网 SSO 等逻辑,
这些差异 GitHub 侧看不到。**内网 Agent 接手后按 §6 指引把它们逐条补登到本节**,格式同 §2:

### 4.x 〈关注点,如:内网 SSO 登录〉

- **涉及文件**:
- **为什么在内网分支**:
- **冲突原则**:
- **能否上游化**:

## 5. 已上游化(历史记录,勿再作为差异维护)

| 曾经的差异 | 上游化版本 | 备注 |
|---|---|---|
| 裸机 PM2 部署路径(`deploy.sh`/`ecosystem.config.js`/ini `[server]`/`[nginx]`) | v3.39.0 | 回迁时清理 RAG 判定等内网痕迹;`disable_tls_verify` 明确未随行 |
| Alembic 多头容忍(`upgrade("heads")`) | v3.38.1 | 起因是内网仓自带迁移支线 |

## 6. 内网 Agent 接手指引

目标:把内网托管仓相对 `UPSTREAM_BASE` 的全部差异登记进 §4,之后每次动作都维护本文件。

1. **确定基线**:读 §0 的 `UPSTREAM_BASE`;在内网仓确认该 tag(或对应提交)存在,
   `git merge-base HEAD <tag>` 应等于该 tag 的提交。若内网仓没有 tag,用同步进来的
   merge 提交信息里的「auto-sync vX.Y.Z」定位。
2. **枚举差异**(两条命令互为补充,前者看当前状态,后者看来龙去脉):
   ```bash
   git diff --stat <UPSTREAM_BASE>..HEAD            # 当前相对上游改了哪些文件
   git log --no-merges --oneline <UPSTREAM_BASE>..HEAD   # 内网侧非合并提交(每条通常对应一个关注点)
   ```
   排除 §2/§3 已登记的文件,剩下的就是内网托管仓独有差异。
3. **按关注点归组登记**:同一个功能(如 SSO)涉及的所有文件放进一条;写清「为什么必须在内网」
   (机密 / 内网环境专属 / 依赖内网系统),以及 merge 上游时该文件冲突怎么解(通常是
   「以 main 为准,再把本条的接线/路由/中间件补回」)。
4. **标注上游化可能性**:能通用化的(比如「可插拔认证后端」这种抽象层)写明阻碍,
   作为之后向 main 提通用能力的候选——差异越薄,同步越便宜。
5. **每次合并上游之后**:更新 §0 `UPSTREAM_BASE` 与核对日期;把本次冲突里新出现的文件
   补进对应条目的「涉及文件」;上游已吸收的条目移到 §5。
6. **每次新增内网改动之后**:同一提交里更新本文件——没有登记的差异,下次合并时就会被
   当成「以 main 为准」而丢掉。

排查工具:`git diff <UPSTREAM_BASE>..HEAD -- <file>` 看单文件差异;
`git log -L :<function>:<file> <UPSTREAM_BASE>..HEAD` 追一个函数在内网侧的改动史。
