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

运行角色读取 `[runtime] role`，也可以用 `DORAMI_RUNTIME_ROLE` 覆盖：

```ini
[runtime]
role = all
```

可选值：

- `all`：本地兼容模式，保留现有采集和消费能力。
- `collector`：外网采集归档层，开启抓取、调度、采集任务和运行观测，关闭 MCP/Dify/RAG 等 reader 交付面。
- `reader`：内网分发订阅层，开启内容阅览、向量/RAG、Dify 和 MCP，关闭抓取、调度和采集任务。

两层部署建议：

1. 外网采集归档层部署在可访问公开站点的个人电脑或外网服务器，配置 `role = collector`。
2. 内网分发订阅层部署在公司内网服务器，配置 `role = reader`。
3. 采集层通过 `/api/archive/export/articles.jsonl` 导出归档，分发层通过 `/api/archive/import/articles.jsonl` 导入归档。同步契约见 `docs/archive_sync_contract.md`。
4. 下游应用优先访问分发层的订阅接口 `/api/public/subscriptions/{subscription_id}/dify/articles`，订阅源在前端“订阅分发”页面创建和轮换令牌。

最小示例：

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

仓库根目录提供 `deploy.sh`，用于直接完成依赖安装、前端构建、静态资源同步、PM2 启动/重载和 Nginx reload：

```bash
./deploy.sh
```

默认参数：

- `DORAMI_CONFIG_FILE`：`$(pwd)/config/production.ini`
- `VENV_DIR`：`venv`
- `PM2_APP_NAME`：`dorami-backend-v2`
- `NGINX_HTML_DIR`：`/var/www/my_site`

部署脚本会使用 `uv pip install -e .` 按 `pyproject.toml` 安装后端依赖到 `venv`。它不会读取 `uv.lock` 中锁定的包下载 URL，因此服务器可以通过 uv 环境变量或 uv 配置使用内网 PyPI 源：

```bash
UV_DEFAULT_INDEX=https://pypi.company.example/simple ./deploy.sh
```

前端依赖使用 `npm install --verbose --no-audit --no-fund --replace-registry-host=always` 安装，避免 lockfile 中的历史 registry host 覆盖服务器 npm registry 配置。

如果服务器路径不同，可以临时覆盖：

```bash
DORAMI_CONFIG_FILE=/opt/dorami/config/production.ini \
NGINX_HTML_DIR=/var/www/my_site \
./deploy.sh
```

代理配置迁移到后端配置文件：

```ini
[proxy]
http_proxy = http://user:password@proxy.company.com:8080
https_proxy = http://user:password@proxy.company.com:8080
no_proxy = 127.0.0.1,localhost
```

应用启动时会把这组配置同步到 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式，供底层网络库使用。

管理员登录读取后端配置文件中的 `[auth]`：

```ini
[auth]
admin_users = admin:change-me,ops:another-secret
user_users = user:change-me,reader:reader-secret
secret = change-me-to-a-long-random-string
```

`admin_users` 和 `user_users` 都是逗号分隔的白名单，单项格式为 `账号:密码`。
旧配置中的 `username/password` 仍会被兼容读取为一个 admin 账号，但建议迁移到白名单写法。

账号角色会和 `[runtime] role` 取交集：

- admin 账号：超级用户，可访问当前部署启用的采集归档层和订阅分发层；在 reader 面检索时不受个人订阅范围限制。
- user 账号：受限读者，只能访问订阅分发层，包括内容阅览、订阅源、向量/RAG、Dify 和 MCP 管理。
- 内容台账读取对两类账号开放；手工录入、编辑、删除、离线归档导入等归档写操作只对 admin 账号开放。
- 当 `role = all` 时，同一个部署可通过不同账号隔离两层。
- 当 `role = collector` 或 `role = reader` 时，部署角色仍是外层硬限制。

修改 `production.ini` 后需要重新执行 `./deploy.sh` 或 `pm2 reload ecosystem.config.js --only dorami-backend-v2 --update-env`，因为认证配置只在后端进程启动时读取。

前端配置集中在 `frontend/app.config.json`：

- `apiBaseUrl`：浏览器请求 API 的基础路径。
- `logoPath`：控制台 logo 静态资源路径。
- `devServer.port`：Vite 本地开发端口。
- `devServer.proxyTarget`：Vite `/api` 代理的后端地址。
