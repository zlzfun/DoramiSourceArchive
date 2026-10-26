# 运维排查手法(runbook)

> 状态:耐久参考(2026-10-21 自 Claude Code 自动记忆沉淀,文档整理波 issue #210)。只记**手法**,不含任何主机地址、账号或凭据;生产节点的连接方式与升级实录在运维者的私有笔记 / 个人技能里,不入库。

## 日报篇数不足 / 漏报

1. 先看候选池不是管线:`fetch_runs` 里 `fetched_count=0` 且 `status=success` 的源——站点改版会让采集器「成功抓到 0 条」静默停产(2026-09:IT之家 ID 进位、新智元改 WP REST 壳、OpenAI 上下文级 CF 挑战)。按 `fetched_date` 逐日看正文长度中位数可发现「正文退化成摘要」。
2. 逐环节:文章行(`fetched_date` vs `daily_brief_cursor`)→ KV `daily_brief_source_ids` 名单 → `last_run` 的 scanned / used / below_threshold / score_histogram → 日报 `extensions.items` / `extra_sources` → 容器日志里各阶段计数。
3. 重跑当日日报前先等分析 worker 评完新文章(约 40 篇 / 5 分钟),否则日报就地补评白费调用。
4. 评分可在容器内复现:脚本放挂载的 `data/` 目录,`resolve_llm_config().for_aux()` 即生产同款模型。

## cron 缺席 / 后端间歇停顿

- `docker compose logs backend | grep "was missed"` 看被 APScheduler 跳过的任务;库里 `fetch_runs` 按日计数(scheduled vs manual)是容器重建后唯一证据。
- 每 2 s curl `/api/runtime` 70 s,画出分钟级阻塞窗口;`/proc/<pid>/io` 两次采样看读写速率;`strace -f -y -e trace=openat,pread64 -p <pid>` 3–4 s 定位在读哪个文件。
- 已知病因与修法:播客全文处理每分钟全表重放(#68,改复合游标 + 失败记忆 + 轮级守门)、同步 DB / 下载校验跑在事件循环(#194,改 `asyncio.to_thread` + 元数据查询不 SELECT 正文)、cron 默认 1 s 宽限(统一 `misfire_grace_time=300`)。

## 分析回填(`full_analysis`)

- 全历史 `backfills/estimate` 与创建要扫全库正文算哈希,客户端会超时但服务端照常完成——超时后先 `GET /api/admin/analysis/backfills?limit=2` 看任务是否已建,别盲目重试。
- 同一时间只允许一个完整分析任务(`_active_full_job`);「补内容价值」须等「仅从未分析」结束。
- 完整回填会顺带识别历史配图(`image_insight` 用量,约占总费用三成),估算费用要算上;吞吐约每分钟 6 篇。
- `version_stale` 停在几十不归零是正常的(巡检不碰无正文行),别当成等待条件。
- 门槛校准脚本做法:容器内 `sys.path.insert(0,"/app/src")` + `create_engine("sqlite://", creator=lambda: sqlite3.connect("file:/app/data/cms_data.db?mode=ro", uri=True))`,直接调线上选篇函数,只打印计数。

## 生产库只读聚合统计

- 容器内 `docker compose exec -T backend python -` 跑脚本,`sqlite3.connect("file:<db>?mode=ro", uri=True)`(路径取 `config.settings.storage.database_url`),只打印 JSON 计数、不落文件。
- 生产代码比分支旧时,把新逻辑在本地编译成 SQL 文本(`literal_binds`)带过去;**含相关标量子查询的表达式必须嵌在 `select(ArticleRecord.id, expr)` 里整体编译**,单独编译会退化成不关联子查询(全表同值,`.correlate()` 救不回);上生产前先在本地副本上对拍脚本结果与 ORM。
- 「只读」的验证标准:主库 sha + `-wal` 文件操作前后不变,且能读到 WAL 里未 checkpoint 的数据——`PRAGMA query_only` 达不到。
- 导出的数据拷回本机由运维者本人执行,临时文件用完即删。

## 部署前后核对(Docker 路径)

- **磁盘**:worker 先做库备份再进预检,预检按整数 GB 向下取整要求 ≥ `DORAMI_DEPLOY_MIN_FREE_GB`(默认 5),所以实际要留 ≥ 5 GB + 库大小 + 余量;腾盘首选 `docker builder prune -f`(不带 `-a`,保留层缓存),再考虑旧备份与旧回滚镜像;`DORAMI_DEPLOY_BACKUP_KEEP` 按库大小与日增调。
- 部署成功 ≠ 静态资源可读:**部署后 curl 公网 `/fonts/*.css`、`/manifest.webmanifest`、`/brand/pwa-*.png` 看 200**,不能只看 `/api/health`(#192:`umask 077` 检出的文件 0600 进 nginx 镜像,字体 / manifest / PWA 图标 403 了四周)。
- 流水线失败在 `up` 之前时系统未动、事务保留;同 target 重试复用事务且不重做备份,`gh run rerun <release run> --failed` 再批准(`gh api -X POST …/pending_deployments -F "environment_ids[]=<id>" -f state=approved`)。`gh run watch` 会因网络断提前 exit 1,别当部署失败。
- 新版 compose 的 `:?` 必填变量(`DORAMI_ARCHIVE_AUTHORITY_ID`、`DORAMI_PODCAST_INSTALLATION` / `DORAMI_PODCAST_AUTHORITY_ID`)缺了 build / config 直接拒绝;authority id 一旦设定**永不改**。
- 旧版部署脚本不认 tag 参数时先 `git fetch --tags && git checkout vX.Y.Z` 再跑新脚本(新脚本自带 fetch / checkout / re-exec)。
- `pkill -f deploy-docker.sh` 会杀掉自己的 ssh 会话(命令行含同字串),用 `pgrep -f "[d]eploy"` 写法;长命令 `nohup … > log 2>&1 < /dev/null &` 后轮询,链路抖动时直连输出会假死。

## 源静默停产的修补手法

- 列表能抓、正文退化(如 Cloudflare 对同一浏览器上下文二次导航恒 403):容器内 `docker compose exec -T backend python -` 跑项目 `PlaywrightRenderer` 逐篇取正文写 JSON 到挂载 `data/`,宿主脚本 `PUT /api/articles/{id}` 回写(改正文会自动重排分析);只有两三百字碎片的不回写。
- 写操作一律走正式 API(由后端进程串行,带 FetchRun 跟踪 / 媒体预取 / 分析入队),不直接改库——直改库的采集任务 cron 不会热注册。
- 生产 `cookie_secure=true` 时登录 cookie 带 Secure,httpx / curl 不会回发到明文 `http://127.0.0.1`:登录后抓 `Set-Cookie`,拼成显式 `Cookie` 头附到后续请求。
