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

PM2 使用方案 A：启动 `src/main.py`，由应用代码读取 `[server] host/port/reload` 后调用 Uvicorn。仓库根目录提供的 `ecosystem.config.js` 只保留进程管理配置和 `DORAMI_CONFIG_FILE` 路径，不再注入代理、模型或业务密钥。默认读取 `./config/production.ini`，也可以在启动 PM2 前覆盖：

```bash
DORAMI_CONFIG_FILE=/opt/dorami/config/production.ini pm2 start ecosystem.config.js
```

代理配置迁移到后端配置文件：

```ini
[proxy]
http_proxy = http://user:password@proxy.company.com:8080
https_proxy = http://user:password@proxy.company.com:8080
no_proxy = 127.0.0.1,localhost
```

应用启动时会把这组配置同步到 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 及其小写形式，供底层网络库使用。

前端配置集中在 `frontend/app.config.json`：

- `apiBaseUrl`：浏览器请求 API 的基础路径。
- `logoPath`：控制台 logo 静态资源路径。
- `devServer.port`：Vite 本地开发端口。
- `devServer.proxyTarget`：Vite `/api` 代理的后端地址。
