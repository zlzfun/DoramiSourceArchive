# 配置文件说明

> 本文偏**部署操作视角**(改哪、何时需重启);各配置节的完整语义与环境变量总表见 `CLAUDE.md` 的 Configuration / Environment Variables 节。

后端配置集中在 INI 文件中读取。默认查找顺序：

1. `DORAMI_CONFIG_FILE` 指定的文件。
2. 仓库内 `config/backend.ini`。
3. 仓库内 `config/local.ini`。
4. 代码内默认值。

仓库提供两份示例文件：

- `config/backend.example.ini`：本地/通用后端配置模板。
- `config/production.example.ini`：生产部署模板，默认 `reload = false`，模型路径示例指向本地目录。

真实部署文件可能包含管理员密码、auth secret、代理账密、小鲁班凭证、图床 secret 等敏感值，已通过 `.gitignore` 排除，不应提交。

运行角色读取 `[runtime] role`（也可用 `DORAMI_RUNTIME_ROLE` 覆盖）。**单机部署保持默认 `all` 即可**，无需改动；此时访问控制只看登录账号角色（见下文「账号角色」）：

```ini
[runtime]
role = all
```

> 以下「分离部署」属高级可选场景。若你只在单台机器（含公网 ECS）上跑、内网也能访问该域名，可直接跳过本节。

`collector` / `reader` 仅用于把采集与分发拆到不同主机的**分离部署**：

- `collector`：外网采集归档层，开启抓取、调度、采集任务和运行观测，关闭 MCP/Dify/RAG 等 reader 交付面。
- `reader`：内网分发订阅层，开启内容阅览、向量/RAG、Dify 和 MCP，关闭抓取、调度和采集任务。

分离部署步骤：

1. 外网采集归档层部署在可访问公开站点的个人电脑或外网服务器，配置 `role = collector`。
2. 内网分发订阅层部署在公司内网服务器，配置 `role = reader`。
3. 采集层通过 `/api/archive/export/articles.jsonl` 导出归档，分发层通过 `/api/archive/import/articles.jsonl` 导入归档。同步契约见 `docs/contracts/archive_sync.md`。
4. 下游应用优先访问分发层的个人聚合接口 `/api/public/feed/articles`（`dfeed_` 令牌，覆盖用户全部订阅源）；订阅源在前端“阅读器”左栏增删，聚合令牌在“接入集成”页面生成/轮换。（按源隔离的 `/api/public/subscriptions/{id}/...` + `dsub_` 令牌仍可用，属高级/自动化路径。）

分离部署最小示例：

```ini
# 外网 collector
[runtime]
role = collector

# 内网 reader
[runtime]
role = reader
```

生产部署走 Docker(唯一路径,详见 [`deploy-docker.md`](./deploy-docker.md)):容器入口固定监听 `0.0.0.0:8088`,`[server]` 节在容器内不生效(仅 dev 裸起 `python src/main.py` 使用);TLS 由宿主边缘 Nginx 终止。原 PM2/deploy.sh 路径及其 `[nginx]` 配置节已于 v3.15.1 退役,考古看 git 历史。

代理配置迁移到后端配置文件：

```ini
[proxy]
http_proxy = http://user:password@proxy.company.com:8080
https_proxy = http://user:password@proxy.company.com:8080
no_proxy = 127.0.0.1,localhost
```

应用启动时会把这组配置同步到 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式，供底层网络库使用。

登录账户已迁移到**数据库托管**（`users` 表，密码以 PBKDF2 哈希存储）。配置文件中的 `[auth]` 只在**首次启动（`users` 表为空）时作为初始种子**，之后账户以数据库为准——改 `[auth]` 不再影响已存在的账户。

```ini
[auth]
admin_users = admin:change-me,ops:another-secret
user_users = user:change-me,reader:reader-secret
secret = change-me-to-a-long-random-string
```

`admin_users` 和 `user_users` 都是逗号分隔的种子白名单，单项格式为 `账号:密码`；`admin_users` 播种为 admin 角色、`user_users` 播种为 user 角色。`secret` 用于会话 token 与订阅/聚合令牌的 HMAC 签名，**请保持稳定**（变更会使已签发的会话与令牌失效）。

**首次启动后请尽快改掉种子密码**：管理员在前端「设置 → 账户管理」可创建账户、分配角色、重置密码、停用/删除账户（停用或删除会立即让对应账户的会话失效，且不能删除最后一个启用的管理员）；任意账户可在「设置 → 账户」自助修改自己的登录密码。账户运维不再需要改配置文件或重启后端。

账号角色是默认 `all` 部署下唯一生效的访问控制轴：

- admin 账号：超级用户，可访问全部采集与分发能力；在 reader 面检索时不受个人订阅范围限制。
- user 账号：受限读者，登录后是一个“阅读器”（仅阅读已订阅来源，左栏增删订阅）外加“接入集成”（聚合接口令牌、MCP、Skill）；检索、阅读与下游分发均硬限定在个人订阅范围内。
- 内容台账读取对两类账号开放；手工录入、编辑、删除、离线归档导入等归档写操作只对 admin 账号开放。
- （仅分离部署）账号角色会再和 `[runtime] role` 取交集：`role = collector` / `reader` 时，部署角色作为外层硬限制叠加在账号角色之上。

账户增删改在前端「账户管理」即时生效、无需重启；但 `[auth]` 的其余项（`cookie_name`、`session_seconds`、`secret`、`cookie_secure`）以及种子白名单只在后端进程启动时读取，修改这些后需要重启后端(`docker compose restart backend`;dev 裸起则重启进程)。

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
```

- 环境变量覆盖:`DORAMI_LLM_BASE_URL` / `DORAMI_LLM_API_KEY` / `DORAMI_LLM_MODEL`。
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
