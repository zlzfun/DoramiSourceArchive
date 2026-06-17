# 配置文件说明

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

PM2 使用方案 A：启动 `src/main.py`，由应用代码读取 `[server] host/port/reload` 后调用 Uvicorn。仓库根目录提供的 `ecosystem.config.js` 只保留进程管理配置和 `DORAMI_CONFIG_FILE` 路径，不再注入代理、模型或业务密钥。默认读取 `./config/production.ini`，也可以在启动 PM2 前覆盖：

```bash
DORAMI_CONFIG_FILE=/opt/dorami/config/production.ini pm2 start ecosystem.config.js
```

仓库根目录提供 `deploy.sh`，用于直接完成基础系统依赖安装、后端依赖安装、前端构建、Nginx 站点配置、静态资源同步、PM2 启动/重载和 Nginx reload：

```bash
./deploy.sh
```

`deploy.sh` 的 Nginx 行为默认从 `production.ini` 的 `[nginx]` 段读取；同名大写环境变量仍可临时覆盖配置文件。

默认配置项：

- `DORAMI_CONFIG_FILE`：`$(pwd)/config/production.ini`
- `VENV_DIR`：`venv`
- `PM2_APP_NAME`：`dorami-backend-v2`
- `[nginx] html_dir`：`/var/www/my_site`
- `[nginx] site_name`：`dorami`
- `[nginx] server_name`：`_`
- `[nginx] listen_port`：`80`
- `[nginx] listen_options`：`default_server`
- `[nginx] enable_ssl`：`false`
- `[nginx] ssl_listen_port`：`443`
- `[nginx] ssl_redirect`：`true`
- `[nginx] ssl_cert_file`：启用 SSL 且留空时默认为 `/etc/nginx/ssl/${server_name}.pem`
- `[nginx] ssl_key_file`：启用 SSL 且留空时默认为 `/etc/nginx/ssl/${server_name}.key`
- `[nginx] enable_hsts`：`false`
- `[nginx] backend_proxy_host`：`127.0.0.1`
- `[nginx] backend_proxy_port`：默认读取 `[server] port`，通常为 `8088`
- `[nginx] disable_default_site`：`true`

部署脚本假定 `uv` 已安装并配置好包源。它会在缺失时通过 `apt-get`、`dnf` 或 `yum` 安装 `nginx`、`nodejs`、`npm`，并通过 `npm install -g pm2` 安装 PM2。

脚本会写入 Nginx 站点配置：Debian/Ubuntu 风格环境使用 `/etc/nginx/sites-available/{site_name}` 并链接到 `sites-enabled`；其他环境使用 `/etc/nginx/conf.d/{site_name}.conf`。生成的站点配置会将静态根目录设为 `[nginx] html_dir`，并把 `/api/` 反代到 `http://{backend_proxy_host}:{backend_proxy_port}`。脚本随后用 `nginx -T` 和 `nginx -t` 校验启用后的配置确实包含该 root 和 proxy_pass。

如果已经有商业证书或云厂商证书，可以把 `.pem` 和 `.key` 放到 `/etc/nginx/ssl`，然后在 `production.ini` 中启用 SSL：

```ini
[nginx]
server_name = your.domain.com
enable_ssl = true
ssl_cert_file = /etc/nginx/ssl/your.domain.com.pem
ssl_key_file = /etc/nginx/ssl/your.domain.com.key
ssl_redirect = true
```

之后直接执行：

```bash
./deploy.sh
```

如果证书文件名正好是 `/etc/nginx/ssl/${server_name}.pem` 和 `/etc/nginx/ssl/${server_name}.key`，可以省略 `ssl_cert_file` / `ssl_key_file`。启用 SSL 时，脚本默认生成 HTTP 到 HTTPS 的 301 跳转，并在 HTTPS server 上启用 TLSv1.2/TLSv1.3 和基础安全响应头。HSTS 默认关闭，确认 HTTPS 证书续期和访问都稳定后再通过 `enable_hsts = true` 开启。

HTTPS 访问确认稳定后，建议把 `config/production.ini` 里的 `[auth] cookie_secure` 改为 `true` 并重新执行部署脚本；SSL 开启但该值不是 `true` 时，脚本会给出警告。

当 `[rag] enabled = true` 或设置了 `DORAMI_RAG_ENABLED=true` 时，脚本会检查 `[models] embedding_model` 和 `reranker_model` 指向的模型目录是否存在；RAG 关闭时跳过模型目录检查。

部署脚本会使用 `uv pip install -e .` 按 `pyproject.toml` 安装后端依赖到 `venv`。它不会读取 `uv.lock` 中锁定的包下载 URL，因此服务器可以通过 uv 环境变量或 uv 配置使用内网 PyPI 源：

```bash
UV_DEFAULT_INDEX=https://pypi.company.example/simple ./deploy.sh
```

前端依赖使用 `npm install --verbose --no-audit --no-fund --replace-registry-host=always` 安装，避免 lockfile 中的历史 registry host 覆盖服务器 npm registry 配置。

如果服务器路径不同，建议写入 `production.ini`：

```ini
[nginx]
html_dir = /var/www/my_site
```

需要临时指定另一份配置文件时，仍可使用 `DORAMI_CONFIG_FILE=/opt/dorami/config/production.ini ./deploy.sh`。

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

账户增删改在前端「账户管理」即时生效、无需重启；但 `[auth]` 的其余项（`cookie_name`、`session_seconds`、`secret`、`cookie_secure`）以及种子白名单只在后端进程启动时读取，修改这些后需要重新执行 `./deploy.sh` 或 `pm2 reload ecosystem.config.js --only dorami-backend-v2 --update-env`。

前端配置集中在 `frontend/app.config.json`：

- `apiBaseUrl`：浏览器请求 API 的基础路径。
- `logoPath`：控制台 logo 静态资源路径。
- `devServer.port`：Vite 本地开发端口。
- `devServer.proxyTarget`：Vite `/api` 代理的后端地址。
