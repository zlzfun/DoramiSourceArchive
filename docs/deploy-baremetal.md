# 裸机部署(第二条官方路径)

> v3.39.0 扶正。本路径 = `deploy.sh`(uv venv + PM2 + 宿主 Nginx + 现场构建前端)。
> 它曾于 v3.15.1 随生产切 Docker 退役删除,其后在 intranet 分支为内网环境复活并持续
> 维护;2026-08 出现「公网机不便安装 Docker」的真实场景后回迁 main,与
> [`deploy-docker.md`](./deploy-docker.md) 并列为两条官方路径之一。
>
> **内网专属的 `[network] disable_tls_verify`(出网跳过 TLS 校验)不在本路径内**,
> 它仍只存在于 intranet 分支——公网部署既不需要也不该开。

## 选哪条路径

| | Docker(`./deploy-docker.sh`) | 裸机(`./deploy.sh`) |
|---|---|---|
| 前提 | docker + compose 插件 | uv、Node ≥20.19、Nginx(PM2 脚本代装) |
| 依赖版本 | `docker/requirements.txt` 钉版 | **同一份钉版清单**(v3.39.1 起) |
| OS 兼容 | 镜像内恒为 bookworm,Playwright 环境固化 | 随宿主 OS,Chromium 有三层兜底 |
| 发布 | 整镜像原子切换 | 逐步骤就地更新 |
| 重启自愈 | `restart: unless-stopped` | 需 `pm2 save && pm2 startup` |
| HTTPS | 容器只做 HTTP,TLS 交外层边缘 | 脚本直接生成带 TLS 的站点配置 |

**能装 Docker 就走 Docker**;装不了(版本过旧、策略不允许、环境受限)走本路径。

## 形态

```
宿主
├── venv/                  ← uv venv + `uv pip install -e .`
├── PM2: dorami-backend-v2 ← ecosystem.config.js(interpreter=./venv/bin/python,
│                            NODE_ENV=production 强制关 uvicorn reload)
├── Nginx 站点             ← deploy.sh 按 ini [nginx] 节生成并 `nginx -T` 复核
│                            (/api/ 反代 + /mcp 反代 + SPA try_files + 可选 TLS)
└── data/ logs/ backups/   ← SQLite / 媒体库 / PM2 日志 / 迁移前 DB 备份
```

## 前置

| 组件 | 要求 | 脚本是否代装 |
|---|---|---|
| `uv` | 必须先装 | **否**,缺失即 fail |
| Python | ≥3.10(`uv venv` 建 `venv/`) | 由 uv 处理 |
| Node/npm | **≥20.19(Vite 8 + React 19)**,建议 22 LTS | 试包管理器,失败则 fail |
| Nginx | 任意(源码装亦可) | 同上 |
| PM2 | — | `npm i -g pm2` |
| Chromium | 可选,仅 `rss_openai_news` 渲染节点用 | 试 `playwright install`,失败降级不阻断 |

手装的 nginx / nvm-node 常不在非交互 shell 的 PATH 里:脚本已自动并入
`/usr/sbin:/usr/local/sbin:/usr/local/bin:/usr/local/nginx/sbin` 与 nvm 的最新版本目录,
仍找不到就 `export PATH="$PATH:<安装目录>"` 后重跑。

受限网络/镜像加速:`UV_DEFAULT_INDEX=<PyPI 镜像>`、`NPM_REGISTRY=<npm 镜像>`。

**依赖版本来源**(v3.39.1):脚本按入库的钉版清单 `docker/requirements.txt` 装运行时依赖,
再以 `--no-deps` 装项目本身 —— 与 Docker 路径共享同一份版本事实来源。
起因是 v3.39.0 首次公网裸机部署撞上 **mcp 2.0**(2026-07-28 把 `FastMCP` 改名 `MCPServer`
并变更 API):彼时 `uv pip install -e .` 按 `mcp>=1.0.0` 现解装到 2.x,后端
`ModuleNotFoundError: No module named 'mcp.server.fastmcp'` 起不来,而同版本 Docker
镜像因走清单安然无恙。同轮给 pyproject 的 mcp 加了 `<2` 上限双保险。
清单缺失时退回现解安装(版本由 pyproject 约束兜底)。

## 用法

```bash
cp config/production.example.ini config/production.ini   # 见下节「配置」
./deploy.sh

# 常用运维
pm2 logs dorami-backend-v2        # 后端日志
pm2 restart dorami-backend-v2     # 重启后端
pm2 save && pm2 startup           # 开机自启(脚本不做,必须手动执行一次)
```

七个步骤:装系统依赖 → 校验配置 → uv 装后端(+Playwright)+ **DB 备份** + 迁移预检 +
`ensure_migrated` → npm 构建前端 → 写并校验 Nginx 站点 → 发布 dist 到 `html_dir` →
`pm2 reload` + `nginx -s reload`。

脚本自带的护栏:
- **迁移前自动备份 SQLite** 到 `backups/`(保留最近 10 份),迁移炸了可直接回滚文件;
- **迁移预检观测**:打印库当前 revision 与迁移链 head 数——分叉仓合入后 DAG 双头时
  (`ensure_migrated` 自 v3.38.1 起并行全升)在部署日志里可追;
- **站点 include 复核**:源码装的 nginx 默认什么都不 include,写了站点文件也不生效;
  脚本用 `nginx -T`(实际生效配置)复核,缺失则备份主配置后往 `http {}` 插一行 include;
- **目录穿越位**:`html_dir` 各级父目录缺 others 的 `x` 位会让 worker stat 失败 →
  try_files 内部重定向循环 → 500;脚本逐级补 `o+x`(只补穿越位);
- **`proxy_buffering off`**:大响应落 proxy_temp 而 worker 无写权限时会静默截断
  (上游 200、浏览器 Failed to fetch);同时也是 `/mcp` SSE 流的正确形态;
- **`/mcp` 的 Host 改写**:MCP Python SDK 的 DNS-rebinding 防护只认 localhost 形态的
  Host,经域名/EIP 反代进来会 421 Invalid Host header,故 `/mcp`(且仅 `/mcp`)
  改写 Host 为 `backend_proxy_host:port`,`/api/` 仍透传 `$host`。

## 配置

`config/production.ini` 两条路径共用,裸机路径额外读 `[server]`(后端监听)与
`[nginx]`(站点生成)两节。公网部署必改:

```ini
[auth]
secret = <长随机串>          # 占位符在生产姿态下拒绝启动
cookie_secure = true         # 走 HTTPS 后置 true,同时开启启动期安全校验的生产姿态

[cors]
allow_origins = https://your-domain.example.com   # * + allow_credentials 是 error 不是告警

[network]
disable_ca_bundle = false    # 默认 true 会清空 CURL/REQUESTS_CA_BUNDLE,公网置 false

[nginx]
server_name = your-domain.example.com   # enable_ssl 时不能是 _
enable_ssl = true
ssl_cert_file = /etc/letsencrypt/live/your-domain.example.com/fullchain.pem
ssl_key_file  = /etc/letsencrypt/live/your-domain.example.com/privkey.pem
```

`[server] reload` 必须为 `false`(`config.py` 的 fallback 是 `true`,
`ecosystem.config.js` 的 `NODE_ENV=production` 另有守卫兜底,显式写上更稳)。

## HTTPS(两趟部署)

`deploy.sh` 在 `enable_ssl = true` 时先校验证书文件存在,不存在直接 fail,所以:

```bash
# 1. DNS A 记录指向本机,先按 HTTP 部署(enable_ssl=false, cookie_secure=false)
./deploy.sh
# 2. 签证书
certbot certonly --webroot -w /var/www/my_site -d your-domain.example.com
# 3. 改 ini:enable_ssl/cookie_secure/cors/disable_ca_bundle → 再跑一次
./deploy.sh
```

⚠️ **别用 `certbot --nginx`**:`write_nginx_site_config` 每次部署都用 heredoc 整体覆写
站点文件,certbot 插进去的行下次部署就没了。`certonly` + ini 里固定证书路径,续期只换
文件内容、配置不动。

防火墙开 **80 + 443**(80 留给跳转与续期);后端只监听 `127.0.0.1:8088`,不要对外放行。
SELinux 开启时需 `setsebool -P httpd_can_network_connect 1`,否则 nginx 反代被拒。

## 全新服务器部署(含迁移)

```bash
# 1. 前置:uv / Node≥20.19 / Nginx;时区 timedatectl set-timezone Asia/Shanghai(cron 语义)
# 2. 取代码 + 配置
git clone <repo> && cd DoramiSourceArchive
cp config/production.example.ini config/production.ini   # 按上节改

# 3.(迁移场景)搬数据——LLM 配置、X token、账号、订阅、采集游标、日报配置全在
#    DB 的运行时 KV 里,拷 data/ 即全部带走,无需在新机重配:
#    老机先 pm2 stop dorami-backend-v2(静止 WAL),再整目录拷:
rsync -a old:/path/DoramiSourceArchive/data/ ./data/
#    全新空库则跳过(首启自动建库 + 根管理员 admin/admin,登录后立刻改密码)

# 4. 部署 + 自启
./deploy.sh
pm2 save && pm2 startup
```

机密走环境变量时(如 `DORAMI_X_BEARER_TOKEN`)需在跑 `deploy.sh` 前 `export`——
`pm2 start/reload --update-env` 会把当时的 shell 环境带进后端进程;也可以登录后在
设置柜 → 凭据里填(KV 覆盖 env,见 CLAUDE.md 的*外部凭据统一保管层*)。

## 与 intranet 分支的关系

intranet 分支自此不再自带 `deploy.sh`/`ecosystem.config.js`/ini 两节(改用 main 版本),
其独有面收敛为:`[network] disable_tls_verify` 开关及各 httpx client 的
`verify=settings.network.tls_verify` 接线、分支须知块与 `.claude/` 钩子。
