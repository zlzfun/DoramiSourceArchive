# 配置文件说明

> 本文偏**部署操作视角**(改哪、何时需重启);各配置节的完整语义与环境变量总表见 `CLAUDE.md` 的 Configuration / Environment Variables 节。

后端配置集中在 INI 文件中读取。默认查找顺序：

1. `DORAMI_CONFIG_FILE` 指定的文件。
2. 仓库内 `config/backend.ini`。
3. 代码内默认值。

仓库提供两份示例文件：

- `config/backend.example.ini`：本地/通用后端配置模板。
- `config/production.example.ini`：生产部署模板，默认 `reload = false`，模型路径示例指向本地目录。

真实部署文件可能包含管理员密码、auth secret、代理账密、小鲁班凭证、图床 secret 等敏感值，已通过 `.gitignore` 排除，不应提交。

运行角色读取 `[runtime] role`（也可用 `DORAMI_RUNTIME_ROLE` 覆盖）。单机以及当前生产双节点均保持 `all`；采集/分析的单写权威由 Archive Sync v3 的持久化 authority 字段控制，不再用运行角色猜测：

```ini
[runtime]
role = all
```

> 以下 `collector` / `reader` 角色只保留给需要物理关闭某一类 API 的部署。当前外网 Dorami + 内网 Dorami 的生产拓扑不是这种模式，两端均为 `all`。

`collector` / `reader` 可用于把采集与分发拆到不同主机的严格隔离部署：

- `collector`：外网采集归档层，开启抓取、调度、采集任务和运行观测，关闭 MCP/feed 等 reader 交付面。
- `reader`：内网分发订阅层，开启内容阅览、全文检索、feed 和 MCP，关闭抓取、调度和采集任务。

当前双 `all` 生产部署步骤：

1. 外网 Dorami 采集并分析平台/公共源；内网 Dorami 同步并提供服务，两端配置 `role = all`。
2. 内网 Dorami 自行采集用户自定 RSS，并可直接调用外部 MaaS；自定源正文不上传外网 Dorami。
3. 内网配置远程同步，使用 v2 API 上的 Archive Sync v3 manifest 拉取 sources、taxonomy、articles、analyses、media、source_states。同步契约见 `docs/contracts/archive_sync.md`。
4. 下游应用优先访问分发层的个人聚合接口 `/api/public/feed/articles`（`dfeed_` 令牌，覆盖用户全部订阅源）；订阅源在前端“阅读器”左栏增删，聚合令牌在“接入集成”页面生成/轮换。（按源隔离的 `/api/public/subscriptions/{id}/...` + `dsub_` 令牌仍可用，属高级/自动化路径。）

Podcast 处理能力与 `runtime.role` 正交。安装类型决定默认处理姿态，安装 ID 必须在
容器/进程重建后保持稳定，不能取 hostname 或容器 ID：

| `installation` | 默认处理 | 默认阶段 | 默认目标 |
| --- | --- | --- | --- |
| `external` | 开启 | `fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish` | `transcript,full_analysis,digest_blog,digest_audio` |
| `internal` | 关闭 | 空（只同步） | 空 |
| `development` | 关闭 | `fetch,local_publish` | 空 |

`processing_enabled`、`allowed_stages` 和 `provider_ready_targets` 仍可显式覆盖。
外网默认开启只代表允许执行；缺少真实供应商凭据、计价/额度或可安全提交的媒体地址时，运行时仍会
拒绝付费请求。

```bash
# 外网 all：完成采集、ASR、中文博客、TTS 与发布
export DORAMI_PODCAST_INSTALLATION=external
export DORAMI_PODCAST_AUTHORITY_ID=<stable-external-id>
export ALIYUN_AK_ID=<secret>
export ALIYUN_AK_SECRET=<secret>
export NLS_APP_KEY=<secret>
export NLS_ACCESS_TOKEN=<secret>
export NLS_TOKEN_EXPIRES_AT=<provider-unix-seconds>

# 内网 all：不执行 Podcast 处理，只通过 Archive Sync 同步并展示
export DORAMI_PODCAST_INSTALLATION=internal
export DORAMI_PODCAST_AUTHORITY_ID=<stable-internal-id>
```

当前阿里云 ISI 接入中，外网 ASR 需要 AK/SK + Appkey，外网 TTS 需要 Appkey + NLS
Token，并用 AK/SK 按服务端到期时间刷新 Token。内网不配置供应商凭据。可选 STS 另加
`ALIYUN_SECURITY_TOKEN`。这些值不写入 INI、`.env` 示例或版本库。页面读取和音频播放
不会触发 provider。Docker Compose 会强制要求安装类型与安装 ID；裸机使用当前
shell 环境并由 PM2 `--update-env` 继承。

Reader 的已发布播客文字按字符游标分页，默认页长、单页上限与搜索词上限均可调整，
避免把长逐字稿一次载入浏览器：

```ini
[podcast]
reader_text_default_chars = 12000
reader_text_max_chars = 50000
reader_text_query_max_chars = 200
text_artifact_max_bytes = 8388608
text_artifact_max_chars = 4000000
transcript_duration_tolerance_seconds = 5
text_sync_page_max_bytes = 16777216
text_sync_page_max_rows = 1000
# 可选独立 HMAC 密钥（至少 32 字符）；缺省从 [auth] secret 做用途隔离派生
# reader_cursor_secret =
# external 默认 true/全目标，internal 默认 false/空目标；仅覆盖时填写
# processing_enabled = true
# provider_ready_targets = transcript,full_analysis,digest_blog,digest_audio
text_pipeline_version = podcast-text-v1
audio_pipeline_version = podcast-audio-v1
processing_policy_version = podcast-processing-policy-v1
# external 默认 100000 / 5000（人民币分），internal 默认 0 / 0
# monthly_budget_cny_minor = 100000
# per_run_budget_cny_minor = 5000
budget_scope = podcast-paid-processing
budget_timezone = Asia/Shanghai
# external 默认 narrator_zh，internal 默认空
# voice_profiles = narrator_zh
# default_voice_profile = narrator_zh
# 旧精品导读流程的启动基线；管理面保存后以运行时 KV 为准
premium_score_threshold = 8.0
premium_min_duration_seconds = 1200
premium_guide_mode = solo_deep
premium_max_audio_minutes = 15
premium_transcript_max_chars = 120000
premium_blog_max_chars = 6000
premium_narration_max_chars = 4500
```

播客评分分两步但阅读面只展示一个当前分数：简介初评达到 `>= 5.0`（或管理员强制）
后进入 `full_analysis`；发布方完整逐字稿优先，否则才使用 ASR。全文评分完成后替换
简介初评。简介线固定不变；“优质播客”只按全文终评与 AppSettingRecord KV
`podcast_premium_score_threshold` 判定，缺省 `8.0`、范围 `1.0–10.0`、最多一位小数。
管理面修改后即时重算历史全文资格与待生成状态，不重排简介候选，也不删除已发布成品。

ASR worker 的轮询与租约参数单独配置；启动时首轮总会延后一个 `tick_seconds`，不会因
进程启动直接调用 provider：

```ini
[podcast_worker]
tick_seconds = 10
lease_seconds = 120
fallback_retry_seconds = 30
max_steps_per_tick = 1
```

对应环境变量为 `DORAMI_PODCAST_WORKER_TICK_SECONDS`、
`DORAMI_PODCAST_WORKER_LEASE_SECONDS`、
`DORAMI_PODCAST_WORKER_FALLBACK_RETRY_SECONDS` 与
`DORAMI_PODCAST_WORKER_MAX_STEPS_PER_TICK`。两台 `runtime.role=all` 主机都可以保留本节；
ASR job 只由 Podcast stage policy 与运行时 ASR stage-worker registry 决定是否注册；
管理端 target 的完整 executor/estimator readiness 只限制新任务入队，不会阻断已有付费任务的
轮询与结算。具体 provider bundle 只有在其 pre-claim readiness 能证明当前有效配置足以安全查询
已有远端任务时才注册 worker；readiness 失败会在领取数据库租约前停止本轮。

对应环境变量为 `DORAMI_PODCAST_READER_TEXT_DEFAULT_CHARS`、
`DORAMI_PODCAST_READER_TEXT_MAX_CHARS` 与
`DORAMI_PODCAST_READER_TEXT_QUERY_MAX_CHARS`。存储文本的字符/UTF-8 字节硬上限对应
`DORAMI_PODCAST_TEXT_ARTIFACT_MAX_CHARS` 与 `DORAMI_PODCAST_TEXT_ARTIFACT_MAX_BYTES`；
ASR 标准化结果与源音频的时长绝对误差上限对应
`DORAMI_PODCAST_TRANSCRIPT_DURATION_TOLERANCE_SECONDS`；
Podcast 文字同步单页的总字节数/行数还受 `DORAMI_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES` 与
`DORAMI_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS` 限制（单页字节上限必须大于单工件上限）；
游标签名可用 `DORAMI_PODCAST_READER_CURSOR_SECRET` 单独轮换。Reader 响应使用 `no-store`，每次读取
都重新核对来源和单集的可见状态。

外网付费处理默认开启，内网和开发环境默认关闭。只有 `processing_enabled=true`、目标/阶段/逻辑 voice 配置一致、
CNY 月度与单次预算均为正数，并且运行时的具体 provider 集成同时注册执行器与费用估算器，
管理 API 才允许任务入队；只改 INI 不会把 provider 误判为可用。对应配置均支持
`DORAMI_PODCAST_*` 环境变量覆盖。Provider URL、token、模型名、计价参数和真实 voice ID
属于主机侧秘密配置，不进入本节或版本库。外网拥有全部 Podcast 处理阶段；内网不拥有任何
Podcast 处理阶段，只接收已发布的中文博客和导读音频。两台服务的 `[runtime] role` 均保持 `all`。

外网 ASR 默认直接把 Podcast RSS 当前 `audio_url` 作为阿里云 `file_link`。请求处理/入队前
会核对单集当前 enclosure，并把原音频下载到临时 staging
完成大小、媒体格式与探测校验；校验结束即删除原始字节，不写入 Podcast artifact CAS。
数据库只持久化轻量 `source_media_snapshot`，用于把本次处理绑定到经过校验的源媒体事实；
原始签名 URL query 不进入快照、错误或响应。worker 提交前会再次核对当前 enclosure 与快照
绑定，随后阿里云仍从 RSS 原地址读取 `file_link`；
供应商 URL 护栏会拒绝非 HTTPS 域名、IP 字面量和本地地址。只有阿里云明确返回
`FILE_DOWNLOAD_FAILED`、`FILE_404_NOT_FOUND`、`FILE_403_FORBIDDEN` 或
`FILE_SERVER_ERROR` 时，才允许一次本地重下载并上传私有 OSS，再用同地域内网签名 URL
重新提交；解码、格式、采样率、Content-Length 等错误不会走 OSS。Dorami 下载或上传失败
也不会再次提交。OSS 对象在回退任务终态后 best-effort 立即删除，Bucket 生命周期规则负责
异常兜底。已取得 TaskId 的任务只按持久化身份轮询；任何 `request_unknown` 都保持人工核对，
不会自动重提。

阿里云 ISI 的非秘密协议参数集中在 `[aliyun_isi]`，均可由对应
`DORAMI_ALIYUN_ISI_*` 环境变量覆盖。ASR 与 TTS 鉴权不同，不能互换：

```ini
[aliyun_isi]
region_id = cn-shanghai
asr_domain = filetrans.cn-shanghai.aliyuncs.com
asr_product = nls-filetrans
asr_api_version = 2018-08-17
asr_task_version = 4.0
asr_enable_words = true
asr_auto_split = true
asr_enable_sample_rate_adaptive = true
# 可选 OSS 回退；上海 ASR 对应上海外网/内网 Endpoint。留空即禁用。
asr_oss_endpoint = https://oss-cn-shanghai.aliyuncs.com
asr_oss_internal_endpoint = https://oss-cn-shanghai-internal.aliyuncs.com
asr_oss_bucket = dorami-asr-relay-cn-shanghai-<unique-suffix>
asr_oss_prefix = asr-relay
asr_oss_signed_url_ttl_seconds = 86400
token_url = https://nls-meta.cn-shanghai.aliyuncs.com/
tts_url = https://nls-gateway-cn-shanghai.aliyuncs.com/rest/v1/tts/async
tts_product = async-long-text-tts
tts_api_version = rest-v1
tts_device_id = dorami-source-archive
# JSON mapping from DORAMI_PODCAST_VOICE_PROFILES aliases to provider settings.
# Keep empty until a voice is selected and tested.
tts_voice_profiles_json =
tts_result_allowed_host_suffixes = aliyuncs.com
tts_max_chars = 100000
request_timeout_seconds = 30
asr_poll_interval_seconds = 10
tts_poll_interval_seconds = 10
token_refresh_skew_seconds = 300

# 供应商额度与价格快照；scope/window/revision 为空或 limit/deadline 为 0 时，
# 对应 provider stage fail-closed。price 可为 0，明确表示免费额度/试用期。
# ASR 日界固定按上海时区，时长按毫秒向上取整到秒。
asr_quota_scope =
asr_quota_timezone = Asia/Shanghai
asr_daily_audio_seconds_limit = 0
# 单集 ASR 时长上限；独立于每日累计额度，阿里录音文件识别当前硬上限为 12 小时。
asr_max_audio_seconds_per_file = 43200
asr_entitlement_ends_at =
asr_provider_deadline_seconds = 0
asr_price_cny_minor_per_hour = 0
asr_pricing_revision =

# TTS 以一个明确 campaign 累计字符；新一轮审批必须换 campaign_id。
tts_quota_scope =
tts_campaign_id =
tts_campaign_starts_at =
tts_campaign_ends_at =
tts_campaign_character_limit = 0
tts_provider_deadline_seconds = 0
tts_price_cny_minor_per_10000_chars = 0
tts_pricing_revision =
tts_usage_settlement_mode = manual
```

OSS 回退复用 `ALIYUN_AK_ID` / `ALIYUN_AK_SECRET`（以及可选的
`ALIYUN_SECURITY_TOKEN`），不增加另一套密钥。对应环境变量为
`DORAMI_ALIYUN_ISI_ASR_OSS_ENDPOINT`、
`DORAMI_ALIYUN_ISI_ASR_OSS_INTERNAL_ENDPOINT`、
`DORAMI_ALIYUN_ISI_ASR_OSS_BUCKET`、`DORAMI_ALIYUN_ISI_ASR_OSS_PREFIX` 和
`DORAMI_ALIYUN_ISI_ASR_OSS_SIGNED_URL_TTL_SECONDS`。签名使用 OSS V4，有效期最大
7 天；配置值必须覆盖 ASR provider deadline。Bucket 应保持私有、关闭版本控制，并对
`asr-relay/` 配置 1 天删除生命周期，正常路径仍由 worker 在任务终态主动删除。

额度配置与 AK/SK/Appkey/Token 的“能否鉴权”是两套独立门槛：凭据齐全但额度配置
不完整时仍禁止提交。ASR 在提交前按 `ceil(audio_duration_ms / 1000)` 预占当日秒数，
日窗口以 `Asia/Shanghai` 的 `[00:00, 次日 00:00)` 为界且不越过 entitlement
截止时刻；管理员可在“设置 → 凭据 → 播客 ASR”按小时调整每日上限，修改后同一
scope/period 内已使用和已预占的时长继续累计。这里限制的是每日累计处理量，并非单日
只有 24 小时的墙钟时长，因此可按并行处理能力配置大于 24 小时的正数。
`asr_max_audio_seconds_per_file` 则是完全独立的单集准入边界，默认 43,200 秒（12 小时）；超过
该边界的单集会在 API 入队前被拒绝，不占每日额度，也不会发起供应商请求。TTS 按实际
送给供应商的计费字符数预占 campaign 总量。价格全部用人民币分的整数配置，并以向上
取整计算，避免浮点误差。

`request_unknown` / `reconciling` 会继续持有人民币预算与供应商额度；只有供应商明确
确认 `not_submitted` 才释放。结算实际用量超过预占会写入 breach 审计并冻结同一
scope/period 的后续提交，必须人工核对供应商账单和配置后再开启新的 period。
`provider_deadline_seconds` 在 attempt 开始时冻结为绝对截止时间，重启或改配置不会延长
已经提交的任务。

阿里 TTS 任务查询响应没有已核验的实际计费字符字段，因此
`tts_usage_settlement_mode` 默认 `manual`：任务到达终态后保留预占并进入人工账单对账。
只有明确接受“冻结的实际提交文本字符数”作为保守的本地结算口径时，才设置为
`submitted_characters`；该数不是供应商回传账单量。结算模式被纳入 admission fingerprint，
排队后修改会 fail-closed，不会静默改变已有任务的财务语义。

这些非秘密字段均可由同名大写前缀环境变量覆盖，例如
`DORAMI_ALIYUN_ISI_ASR_DAILY_AUDIO_SECONDS_LIMIT` 与
`DORAMI_ALIYUN_ISI_ASR_MAX_AUDIO_SECONDS_PER_FILE`、
`DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_CHARACTER_LIMIT`；完整名称见
`config/production.example.ini` 和 `docker-compose.yml`。不要把真实凭据、供应商任务 ID
或临时 Token 写进这些字段或提交到仓库。

Taxonomy 部署姿态必须另行显式配置，不能由两端共同的 `role = all` 推断。外网在迁移后、
API/worker 前幂等安装仓库批准目录；内网不安装本地目录，只从 Archive Sync 接收：

```ini
# 外网
[taxonomy]
deployment = authority
catalog = config/taxonomy-v1-approved-catalog.json

# 内网
[taxonomy]
deployment = replica
```

未配置时安全默认是 `manual`（启动不动作）。环境变量
`DORAMI_TAXONOMY_DEPLOYMENT` / `DORAMI_TAXONOMY_CATALOG` 可覆盖 INI。catalog digest
相同则启动 no-op；receipt 或现存数据冲突时 fail closed。人工发布步骤和恢复说明见
[`taxonomy-v1-deployment.md`](./taxonomy-v1-deployment.md)。

内网管理员启用 v2 定时同步或手动启动第一次 v2 拉取时，系统会先持久化
`remote_sync:v2_consumer_mode` 围栏，再创建网络任务；因此第一次 authority 全量尚未
落地时，普通公共源的本地采集与分析也已经停止。该围栏不写伪造的 authority，用户
自定 RSS 仍由内网采集；其中普通自定源可分析，签名/凭据源只采集、不调用 MaaS。
升级前遗留的定时配置若没有 `protocol` 且带 `source_ids`，会以
`migration_required=true` 安全停用，管理员必须在「数据同步」明确保存 v1 或 v2；
系统不会替管理员猜测范围。v1 只作为显式兼容模式运行。

生产双节点最小示例：

```ini
# 外网 Dorami
[runtime]
role = all
[taxonomy]
deployment = authority

# 内网 Dorami
[runtime]
role = all
[taxonomy]
deployment = replica
```

`authority_id` 默认首次运行时持久化到数据库。需要跨数据库迁移时可在生产者显式固定
`DORAMI_ARCHIVE_AUTHORITY_ID`；值一旦投入同步不得随主机名、容器或运行角色变化。

生产部署有两条官方路径,`[server]`/`[nginx]` 两节的语义随路径而异:
- **Docker**(推荐,详见 [`deploy-docker.md`](./deploy-docker.md)):容器入口固定监听 `0.0.0.0:8088`,这两节**在容器内不生效**;TLS 由宿主边缘 Nginx 终止。
- **裸机**(装不了 Docker 时,详见 [`deploy-baremetal.md`](./deploy-baremetal.md)):`deploy.sh` 读 `[server]` 作为 PM2 后端监听地址、读 `[nginx]` 生成宿主站点配置(含可选 TLS)。该路径 v3.15.1 退役、v3.39.0 扶正回归。

`[server]` 节在 dev 裸起(`python src/main.py`)下始终生效。

代理配置迁移到后端配置文件：

```ini
[proxy]
http_proxy = http://user:password@proxy.company.com:8080
https_proxy = http://user:password@proxy.company.com:8080
no_proxy = 127.0.0.1,localhost
```

应用启动时会把这组配置同步到 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式，供底层网络库使用。

登录账户**全部数据库托管**（`users` 表，密码以 PBKDF2 哈希存储）。v3.19 起 `[auth]` 不再承担账户种子：**首次启动（`users` 表为空）时系统自动生成根管理员 `admin`/`admin`**，之后一切账户由管理员在前端「运维管理 → 用户」创建与管理（可创建任意数量的管理员或读者）。旧配置项 `admin_users`/`user_users` 已移除，写了也不会被读取。

```ini
[auth]
secret = change-me-to-a-long-random-string
```

`secret` 用于会话 token 与订阅/聚合令牌的 HMAC 签名，**请保持稳定**（变更会使已签发的会话与令牌失效）。

**首次登录后请立即修改根管理员的初始密码**：管理员在前端「运维管理 → 用户」可创建账户（读者或管理员）、提升/取消管理员、重置密码、停用/删除账户（停用/删除/改角色会立即让对应账户的会话失效）；唯一护栏是**末位管理员保护**——系统中最后一个活跃管理员不可被降级/停用/删除。任意账户可在「设置 → 账户」自助修改自己的登录密码。管理面的写操作会记入操作审计（「运维管理 → 用户 → 操作审计」）。

账号角色是默认 `all` 部署下唯一生效的访问控制轴：

- admin 账号：超级用户，可访问全部采集与分发能力；在 reader 面检索时不受个人订阅范围限制。
- user 账号：受限读者，登录后是一个“阅读器”（仅阅读已订阅来源，左栏增删订阅）外加“接入集成”（聚合接口令牌、MCP、Skill）；检索、阅读与下游分发均硬限定在个人订阅范围内。
- 内容台账读取对两类账号开放；手工录入、编辑、删除、离线归档导入等归档写操作只对 admin 账号开放。
- （仅分离部署）账号角色会再和 `[runtime] role` 取交集：`role = collector` / `reader` 时，部署角色作为外层硬限制叠加在账号角色之上。

账户增删改在前端「运维管理 → 用户」即时生效、无需重启；但 `[auth]` 各项（`cookie_name`、`session_seconds`、`secret`、`cookie_secure`）只在后端进程启动时读取，修改这些后需要重启后端(`docker compose restart backend`;dev 裸起则重启进程)。

前端配置集中在 `frontend/app.config.json`：

- `apiBaseUrl`：浏览器请求 API 的基础路径。
- `logoPath`：控制台 logo 静态资源路径。
- `devServer.port`：Vite 本地开发端口。
- `devServer.proxyTarget`：Vite `/api` 代理的后端地址。

## `[llm]`——OpenAI 兼容模型(日报 / 读者 AI 共用)

日报生成、读者面翻译/问答、AI 建源等全部 LLM 能力共用一份 OpenAI 兼容配置:

```ini
[llm]
base_url =            ; 形如 https://api.deepseek.com/v1(留空则 LLM 功能整体惰性关闭)
api_key =
model =
timeout_seconds = 120
temperature = 0.3
max_tokens = 4096
map_concurrency = 4   ; 日报 map 阶段的并发数
thinking_mode =       ; 思考模式(v3.33.1):留空=不发送思考参数(默认,兼容一切端点);
                      ; disabled=关闭思考;low/high/max=开启思考并指定努力档。
                      ; 仅 DeepSeek V4 系等支持该参数的端点可设;不支持时 400 自动降级去掉重试。
aux_model =           ; 辅助轻模型(v3.34,可选):同端点同 api_key 下的第二个模型名,
                      ; 供检索规划/选篇、日报 map/去重聚类等轻量结构化调用使用——
                      ; 主模型走旗舰/思考档时这些调用不必陪跑高延迟高成本;
                      ; 问答作答与翻译仍走主模型。留空=全部调用走主模型。
                      ; 辅助档不下发思考参数(轻任务输出短 JSON,思考反易截断)。
```

- 环境变量覆盖:`DORAMI_LLM_BASE_URL` / `DORAMI_LLM_API_KEY` / `DORAMI_LLM_MODEL` / `DORAMI_LLM_THINKING_MODE` / `DORAMI_LLM_AUX_MODEL`。
- **思考型模型注意**(2026-08 生产事故教训):DeepSeek V4 系默认开思考且努力档 high,
  思考 token 计入 `max_tokens`——日报 reduce 这类长输出任务可能被思考耗尽配额导致正文
  空产。对策:`thinking_mode = disabled`,或保留思考但把 `max_tokens` 调大(≥16384)。
  空正文自 v3.33.1 起会被判为调用失败(不写库、不推游标),不再静默落成空日报。
- **运行时可在「运维管理」页编辑并持久化**(存 `AppSettingRecord` KV,优先级高于 ini);
  三者(base_url+api_key+model)齐备才算已配置,前端各 AI 入口据此显隐。
- 兼容 OpenAI/DeepSeek/Kimi/智谱/通义/火山方舟/OpenRouter/Ollama/vLLM 等任意 `/chat/completions` 端点。

## `[media]`——媒体库(图床)

正文外链图片的本地缓存:抓取入库后自动预取、阅读器经 `/api/media/proxy` 取图、
管理面可对存量回填。归档正文里的原始图链**从不改写**,缓存只是显示层供给。

```ini
[media]
enabled = true            ; 关闭后代理 302 回源、不再预取,整体退回外链直连
media_dir = data/media    ; 缓存落盘目录(按内容 sha256 去重分桶)
max_file_mb = 20          ; 单文件大小上限
timeout_seconds = 20      ; 单图下载超时
prefetch_concurrency = 4  ; 抓取后预取/回填的并发数
```

- 环境变量覆盖:`DORAMI_MEDIA_ENABLED`。
- 下载防护:仅 http(s)、SSRF 拦截(环回/私网/链路本地拒绝;豁免 Clash/Surge fake-ip 段
  `198.18.0.0/15`,否则本机代理环境整体误杀)、魔数嗅探确认图片、失败负缓存退避。

## `[podcast_artifacts]`——Podcast 生成音频 CAS

首发只支持本地 content-addressed storage；S3-compatible provider 后置。CAS 只持久保存
生成的中文导读音频；原节目保持发布者外链，ASR 校验下载只进入 staging，校验结束即删除，
另以轻量 `source_media_snapshot` 固化处理输入事实。Docker 镜像和裸机部署都必须提供
`ffmpeg` 与 `ffprobe`。

```ini
[podcast_artifacts]
root_dir = data/podcast-artifacts
max_audio_mb = 512
total_quota_mb = 0
minimum_free_mb = 1024
allowed_mime_types = audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/webm
upload_timeout_seconds = 120
download_timeout_seconds = 120
download_max_redirects = 5
ffprobe_binary = ffprobe
probe_timeout_seconds = 15
orphan_grace_seconds = 3600
staging_ttl_seconds = 3600
```

`total_quota_mb` 设为 `0` 表示不设置固定业务硬上限，但统计仍按生成音频的实际占用量
计算；显式正数继续作为硬上限。无论是否设置固定配额，`max_audio_mb` 单文件上限、
`minimum_free_mb` 磁盘安全余量与 staging/孤儿文件清理流程始终生效。

环境变量覆盖为 `DORAMI_PODCAST_ARTIFACT_ROOT_DIR`、
`DORAMI_PODCAST_ARTIFACT_MAX_AUDIO_MB`、
`DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_MB`、
`DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_MB`、
`DORAMI_PODCAST_ARTIFACT_ALLOWED_MIME_TYPES`、
`DORAMI_PODCAST_ARTIFACT_UPLOAD_TIMEOUT_SECONDS` 与
`DORAMI_PODCAST_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS`、
`DORAMI_PODCAST_ARTIFACT_DOWNLOAD_MAX_REDIRECTS`；需要精确字节值时可用
`DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_BYTES` 与
`DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_BYTES` 覆盖 MiB 配置；临时 staging TTL 使用
`DORAMI_PODCAST_ARTIFACT_STAGING_TTL_SECONDS`。Docker Compose 把 root 固定为
`/app/data/podcast-artifacts`，由宿主 `./data:/app/data` 持久化；裸机默认落在仓库
`data/podcast-artifacts`。新 unique blob 在原子安装前同时检查 CAS 总配额和磁盘最低
余量；已存在且哈希校验通过的 blob 去重登记不重复占用配额。每次 API 启动及管理员手动
对账只会清理超过 TTL 且未被活跃上传/下载锁定的 `.incoming/*.part` 和失效预留标记，
并仅清理宽限期已过、数据库已无任何引用的生成音频孤儿 blob。发布者音频由外网处理节点
按原始 http(s) 地址下载到 staging 做校验；临时文件不登记为 artifact，并在校验结束后删除。
下载仍受大小、总超时和重定向次数限制，且不会在日志、快照或 artifact 响应中暴露签名 URL query。
迁移、备份和恢复时必须连同整个 `data/` 目录处理。
