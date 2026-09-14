# 阿里云双节点上线指导

> 适用于外网 Dorami 作为 Archive Sync authority、内网 Dorami 作为
> replica 的生产拓扑。本文以本地受限文件
> `../docs/dorami-aliyun-e2e.ini` 中的阿里云验收配置为对照基线，不复制其中的明文凭据。

## 1. 最终拓扑

| 项目 | 外网节点 | 内网节点 |
|---|---|---|
| `runtime.role` | `all` | `all` |
| Taxonomy | `authority`，启动 reconcile，人工发布 | `replica`，只接收 `taxonomy.jsonl` |
| Podcast installation | `external` | `internal` |
| Podcast 处理 | ASR/翻译/分析/导读/TTS/发布 | 关闭，只同步和展示 |
| 阿里云凭据 | 注入 AK/SK、AppKey、NLS Token | 不配置 |
| Archive Sync | 对内网提供八流导出 | 主动拉取八流 |
| 公网入口 | HTTPS 443 | 仅内网/VPN/SSO 可达 |

内网拉取顺序固定为：

`sources → taxonomy → articles → analyses → media → podcast_texts → podcast_audio → source_states`

双节点各自使用唯一、稳定的 Archive authority ID 和 Podcast authority ID。
这四个 ID 不得使用 hostname/container ID，不得在重启、重建容器、换机或数据迁移时变更。

## 2. 验收配置不能原样上生产的项

| 验收配置现状 | 生产操作 |
|---|---|
| `server.port = 8088` | 可作为单机后端回环端口；不得在 ECS 安全组公开。同机双节点验收必须使用不同端口。 |
| SQLite 和 Podcast artifact 位于 `/tmp` | 改为持久盘上的 `data/cms_data.db`、`data/media`和 `data/podcast-artifacts`；备份时三者取同一停机点。 |
| `taxonomy.deployment = authority` | 仅外网保留；内网必须改为 `replica`。 |
| `network.disable_ca_bundle = true` | 公网生产改为 `false`，恢复标准 CA 验证。 |
| `cookie_secure = false` | HTTPS 生产改为 `true`。 |
| `cors.allow_origins = *` 且携带 Cookie | 改为精确的 HTTPS 域名白名单；否则生产安全检查会拒绝启动。 |
| `acceptance-*` authority/scope/revision/campaign | 替换为长期稳定的生产标识；不得沿用验收期身份。 |
| ASR/TTS 权益截止于 `2026-09-15` | 按已购生产权益重新填写开始/截止时间和限额；未确认前保持 fail-closed。 |
| ASR/TTS 价格为 `0`、revision 为验收版 | 按账单/合同填真实人民币分单价和新 revision；只有真实免费额度才能保留 `0`。 |
| TTS campaign 只有 10,000 字符 | 创建新 campaign ID，填审批后的时间窗、字符上限和单价。 |
| `tts_usage_settlement_mode = submitted_characters` | 默认改回 `manual`；只有明确接受“本地提交字符数”口径时才使用自动结算。 |
| AK/SK、AppKey、Token、X/LLM key 写在 INI | 生产全部改为主机 Secret/环境变量。上线后禁用或轮换验收凭据。 |
| OSS Bucket 为验收专用 | 新建生产私有 Bucket，限定 `asr-relay/` 前缀，配置 1 天删除生命周期。 |
| `premium_score_threshold = 8.5` | 这是产品阈值，不是阿里云参数。确认继续用 8.5，否则回到仓库默认 8.0。 |

验收 INI 当前权限为 `0600`，但明文凭据仍不应提升到生产、不应复制进镜像或版本库。

## 3. 生产 INI 关键差异

先从 `config/production.example.ini` 分别生成两台主机的
`config/production.ini`。其他未列参数沿用生产模板，不从验收 INI 整件复制。

### 外网节点

```ini
[runtime]
role = all

[taxonomy]
deployment = authority
catalog = config/taxonomy-v1-approved-catalog.json

[network]
disable_ca_bundle = false
hf_endpoint = https://hf-mirror.com

[auth]
cookie_name = dorami_external_session
session_seconds = 604800
secret = <external-long-random-secret>
cookie_secure = true

[storage]
database_url = sqlite:///data/cms_data.db

[media]
enabled = true
media_dir = data/media

[podcast]
installation = external
authority_id = <stable-external-podcast-id>
processing_enabled = true
allowed_stages = fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish
provider_ready_targets = transcript,full_analysis,digest_blog,digest_audio
monthly_budget_cny_minor = <approved-monthly-budget>
per_run_budget_cny_minor = <approved-per-run-budget>
budget_scope = podcast-paid-processing-prod
budget_timezone = Asia/Shanghai

[podcast_artifacts]
root_dir = data/podcast-artifacts

[cors]
allow_origins = https://<external-domain>
allow_credentials = true
allow_methods = *
allow_headers = *
```

`[aliyun_isi]` 的协议域名、`region_id=cn-shanghai`、ASR 4.0、超时、
轮询间隔和文件上限可沿用验收值。必须替换下列运营数据：

```ini
[aliyun_isi]
asr_oss_endpoint = https://oss-cn-shanghai.aliyuncs.com
asr_oss_internal_endpoint = https://oss-cn-shanghai-internal.aliyuncs.com
asr_oss_bucket = <private-production-bucket>
asr_oss_prefix = asr-relay
asr_oss_signed_url_ttl_seconds = 86400

asr_quota_scope = <prod-asr-scope>
asr_quota_timezone = Asia/Shanghai
asr_daily_audio_seconds_limit = <approved-daily-seconds>
asr_max_audio_seconds_per_file = 43200
asr_entitlement_ends_at = <contract-end-iso8601>
asr_provider_deadline_seconds = <approved-deadline>
asr_price_cny_minor_per_hour = <contract-price>
asr_pricing_revision = <prod-pricing-revision>

tts_quota_scope = <prod-tts-scope>
tts_campaign_id = <new-prod-campaign-id>
tts_campaign_starts_at = <iso8601>
tts_campaign_ends_at = <iso8601>
tts_campaign_character_limit = <approved-limit>
tts_provider_deadline_seconds = <approved-deadline>
tts_price_cny_minor_per_10000_chars = <contract-price>
tts_pricing_revision = <prod-pricing-revision>
tts_usage_settlement_mode = manual
```

### 内网节点

```ini
[runtime]
role = all

[taxonomy]
deployment = replica

[network]
disable_ca_bundle = false

[auth]
cookie_name = dorami_internal_session
session_seconds = 604800
secret = <different-internal-long-random-secret>
cookie_secure = true

[storage]
database_url = sqlite:///data/cms_data.db

[media]
enabled = true
media_dir = data/media

[podcast]
installation = internal
authority_id = <stable-internal-podcast-id>
processing_enabled = false
allowed_stages =
provider_ready_targets =
monthly_budget_cny_minor = 0
per_run_budget_cny_minor = 0

[podcast_artifacts]
root_dir = data/podcast-artifacts

[cors]
allow_origins = https://<internal-domain>
allow_credentials = true
allow_methods = *
allow_headers = *
```

内网不配置 `[aliyun_isi]` 凭据，也不注入 X API 凭据。如果内网阅读器
需调用外部 MaaS，只单独配置经批准的 LLM 凭据。

## 4. 主机 Secret/环境变量

推荐用主机 Secret Manager 注入。如暂时使用项目根目录 `.env`，必须
`chmod 600 .env`，不得提交、打印或上传到工单。

外网：

```dotenv
DORAMI_ARCHIVE_AUTHORITY_ID=<stable-external-archive-id>
DORAMI_PODCAST_INSTALLATION=external
DORAMI_PODCAST_AUTHORITY_ID=<stable-external-podcast-id>

ALIYUN_AK_ID=<production-ram-access-key-id>
ALIYUN_AK_SECRET=<production-ram-access-key-secret>
# 仅使用 STS 时设置：
# ALIYUN_SECURITY_TOKEN=<temporary-security-token>
NLS_APP_KEY=<production-project-appkey>
NLS_ACCESS_TOKEN=<current-access-token>
NLS_TOKEN_EXPIRES_AT=<provider-unix-seconds>

DORAMI_X_BEARER_TOKEN=<production-x-token>
DORAMI_LLM_BASE_URL=<approved-openai-compatible-endpoint>
DORAMI_LLM_API_KEY=<production-llm-key>
DORAMI_LLM_MODEL=<approved-model>
```

内网：

```dotenv
DORAMI_ARCHIVE_AUTHORITY_ID=<stable-internal-archive-id>
DORAMI_PODCAST_INSTALLATION=internal
DORAMI_PODCAST_AUTHORITY_ID=<stable-internal-podcast-id>
```

`auth.secret` 可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。
两端使用不同 secret，一旦投产不随意更换，否则会使现有会话和已签发令牌失效。

## 5. 阿里云控制台配置

1. **RAM**：为 Dorami 生产创建独立的程序身份，不使用主账号
   AccessKey；权限收窄到 NLS 任务和指定 OSS Bucket/前缀。优先使用
   STS/实例角色，如必须使用长期 AK，则建立轮换和禁用流程。
2. **智能语音交互**：在生产项目中开通录音文件识别和异步长文本
   TTS，取得同一阿里云账号下的 AppKey。AppKey 和 NLS Token 不能跨账号混用。
   Token 到期时间以签发服务返回的 `ExpireTime` 为准。
3. **OSS 回退 Bucket**：使用 `cn-shanghai` 私有 Bucket，关闭公共读和版本控制；
   只授权 `asr-relay/` 前缀。配置“最后修改后 1 天删除”的生命周期，
   并清理未完成的多段上传。程序在任务终态会主动删除，生命周期是异常兜底。
4. **ECS 安全组**：外网只对用户/边缘负载均衡开放 80/443，SSH 只允许
   管理员固定 IP；不开放 8088/8089、SQLite 或任何 artifact 目录。内网节点
   仅向内网/VPN/SSO 网段开放 HTTPS。
5. **DNS/TLS**：域名指向外网 EIP 或负载均衡，在 Nginx/LB 终止 TLS。
   应用后端仅绑定回环或 Docker 内网。

对照的阿里云官方文档：[RAM 身份与最小权限](https://help.aliyun.com/zh/ram/product-overview/best-practices-for-identity-and-access-control)、
[NLS Token](https://help.aliyun.com/zh/isi/getting-started/obtain-an-access-token)、
[录音文件识别](https://help.aliyun.com/zh/isi/developer-reference/api-reference-2)、
[异步长文本 TTS](https://help.aliyun.com/zh/isi/developer-reference/restful-api)、
[OSS 生命周期](https://help.aliyun.com/zh/oss/user-guide/lifecycle-rules-based-on-the-last-modified-time/)、
[ECS 安全组](https://help.aliyun.com/zh/ecs/user-guide/security-groups-for-different-use-cases)。

## 6. 上线操作顺序

### 6.1 发布前

1. 从同一个受信 tag/commit 构建两台节点。
2. 停止旧进程，备份整个 `data/` 和两台的 `production.ini`/Secret 版本；
   SQLite DB、媒体和 Podcast CAS 必须取同一停机点。
3. 在外网节点创建专用同步管理员账号，只供内网 Archive Sync 登录。
4. 确认四个 authority ID 已登记在发布记录中。
5. 先在隔离环境运行第 7 节的双节点 E2E。

### 6.2 先发布外网

```bash
cp config/production.example.ini config/production.ini
# 按本文修改 INI，通过 Secret Manager 或 0600 .env 注入凭据
./deploy-docker.sh
docker compose ps
docker compose logs --tail=200 backend
```

日志中 Taxonomy 首次 reconcile 应为 `installed_awaiting_publish`，后续启动应为
`unchanged`。管理员在“运维管理 → 标签”核对 Gate 后人工发布 Taxonomy；
只有 `taxonomy_version > 0` 后才允许内网首次 v2 同步。

### 6.3 再发布内网

```bash
cp config/production.example.ini config/production.ini
# 改为 taxonomy=replica、podcast=internal，不注入阿里云凭据
./deploy-docker.sh
docker compose ps
docker compose logs --tail=200 backend
```

在内网“设置 → 数据同步”中：

1. 填写外网 HTTPS base URL、专用同步管理员账号和密码。
2. 协议选择 v2（Archive Sync v3 transaction protocol）。
3. 先“测试连接”，核对 schema、capabilities 和外网 authority ID。
4. 手动执行首次全量拉取；确认八流 checkpoint 都已完成。
5. 立即再执行一次；无新数据时八流 `count` 应全为 0。
6. 再开启定时同步，观察至少一个调度周期。

以上命令以 Docker 部署为例。如果当前阿里云测试机沿用裸机/PM2 部署，配置
替换项保持不变：分别设置各节点 `[server] port`，确保 `[nginx] backend_proxy_port`
指向同一端口，把第 4 节环境变量写入 PM2/systemd 的受控环境，再运行
`./deploy.sh`。后端端口仍只绑定回环且不加入安全组公网规则；完整裸机步骤见
`docs/deploy-baremetal.md`。

## 7. 双节点端到端验证

两个脚本都创建完全隔离的 SQLite/媒体/CAS 目录，为外网与内网分别
分配两个不同的 `127.0.0.1` 随机端口，并在结果中输出 `ports_distinct=true`。

```bash
uv run python scripts/verify_split_sync_e2e.py
uv run python scripts/verify_podcast_all_all_e2e.py
```

验收点：

- 外网/内网 URL 和 port 不同；
- 八流顺序、首轮全量、增量、删除传播和零增量重跑通过；
- Taxonomy 只导入最新已发布原子快照；
- Podcast 文字流为非空四页，中文导读音频为非空一件；
- 原节目音频只在外网验证，不进内网；
- 两端重启后 checkpoint 稳定，内网 provider attempt 为 0。

如果要手工同机起两个节点，可使用外网 `18088`、内网 `18089`；
两个进程必须使用不同的 INI、SQLite、media、Podcast CAS、Cookie 名和
authority ID。生产如位于不同主机，两端的用户入口都可是 HTTPS 443；
“端口不同”是同机 E2E 的进程隔离要求，不是要求两台生产主机使用不同的 HTTPS 端口。

## 8. 上线后检查与回滚

- 检查 `/api/auth/session`、登录、文章列表、Podcast 文本分页和已发布音频
  `HEAD`/Range 请求。
- 确认内网没有 Podcast stage worker，没有 AK/SK/NLS 凭据，也没有 provider attempt。
- 确认 `storage_pressure=false`，持久盘空间高于 `minimum_free_mb`。
- 确认定时同步一个周期后八流 checkpoint 仍指向同一外网 authority。
- 查看 ASR/TTS 预占、结算、breach 和 `request_unknown` 审计；任何异常先关闭
  `processing_enabled`，不手工重提不确定任务。

回滚时停止进程，同时恢复备份的 `data/`、上一版镜像/代码和对应配置，
然后再启动。不要通过修改 authority ID “解决” checkpoint 冲突；如确需更换外网
authority，必须停机审核并按 Archive Sync rebase 流程处理。
