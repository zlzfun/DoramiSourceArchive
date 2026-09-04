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

运行角色读取 `[runtime] role`（也可用 `DORAMI_RUNTIME_ROLE` 覆盖）。单机以及当前生产双节点均保持 `all`；采集/分析的单写权威由 Archive Sync v2 的持久化 authority 字段控制，不再用运行角色猜测：

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
3. 内网配置远程同步，默认使用 Archive Sync v2 拉取 sources、taxonomy、articles、analyses、media、source_states。同步契约见 `docs/contracts/archive_sync.md`。
4. 下游应用优先访问分发层的个人聚合接口 `/api/public/feed/articles`（`dfeed_` 令牌，覆盖用户全部订阅源）；订阅源在前端“阅读器”左栏增删，聚合令牌在“接入集成”页面生成/轮换。（按源隔离的 `/api/public/subscriptions/{id}/...` + `dsub_` 令牌仍可用，属高级/自动化路径。）

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
