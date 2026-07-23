# Docker 部署(生产推荐路径)

> 2026-07 部署重构:取代「裸机 venv + PM2 + 宿主 Nginx + 服务器现场构建前端」的
> `deploy.sh` 路径(生产实测一个完整采集日 + 日报 cron 后,该路径已于 v3.15.1
> 连同 `ecosystem.config.js`、ini `[nginx]` 节一起退役删除,历史见 git)。动机与完整分析要点:
> 依赖版本锁定(uv.lock `--frozen`)、Playwright/Chromium 环境固化(镜像内 OS 恒为
> bookworm,宿主 OS 兼容性兜底全删)、发布原子化(整镜像切换)、重启自愈
> (`restart: unless-stopped` 取代缺失的 `pm2 save/startup`)、为迁移部署铺路
> (迁移 = 装 docker + 拷 `data/` 与 `production.ini` + `docker compose up -d`)。

## 形态

```
docker-compose.yml
├── backend  ← docker/backend.Dockerfile(python:3.12-slim-bookworm + uv 锁定依赖
│              [torch 走 CPU 轮子] + playwright chromium;入口 docker/entrypoint.py:
│              ensure_migrated → uvicorn 0.0.0.0:8088)
└── nginx    ← docker/nginx.Dockerfile(多阶段:node 构建 frontend/dist → nginx:alpine
               + docker/nginx.conf;对外唯一端口)
```

- 数据全部在宿主 `./data`(SQLite / ChromaDB / 媒体库),卷挂载进 `/app/data`;
  容器无状态,可随意重建。
- `config/production.ini` 只读挂载,不进镜像(`.dockerignore` 同时兜底)。
- 机密经环境变量注入(`DORAMI_X_BEARER_TOKEN` 等),宿主 `export` 或项目根 `.env`。

## 用法

```bash
# 首次:准备配置(同裸机路径)
cp config/production.example.ini config/production.ini   # 改 secret / 账号种子等

# 部署 / 升级(构建 → 起容器 → 健康验证一条龙)
./deploy-docker.sh

# 常用运维
docker compose logs -f backend      # 后端日志(uvicorn stdout,自动轮转 10m×3)
docker compose ps                   # 状态与健康
docker compose restart backend      # 仅重启后端
docker compose down                 # 停站(数据在宿主目录,安全)
```

对外监听默认 80,`DORAMI_HTTP_LISTEN` 可改端口(`8080`)或收进环回
(`127.0.0.1:8080`,配合外层 TLS 反代);时区默认
`Asia/Shanghai`(影响采集任务/日报的 cron 语义),`TZ` 环境变量可覆盖。

## ini 在容器内的语义差异

| ini 节 | 容器内行为 |
|---|---|
| `[server] host/port` | **不生效**。入口固定监听 `0.0.0.0:8088`(nginx 容器经服务名 `backend` 访问);对外端口由 compose 端口映射决定 |
| `[nginx] *` | **不生效**。站点配置在 `docker/nginx.conf`(与 deploy.sh 生成版同构) |
| 其余各节 | 照常生效。`[storage]`/`[media]` 的相对路径以 `/app` 为基准,落在挂载卷 `data/` 下,与裸机版一致 |

## HTTPS

容器只做 HTTP,TLS 终止放外层,三选一:
1. 宿主继续跑一个带证书的 Nginx/Caddy 全站反代到容器(`DORAMI_HTTP_LISTEN=127.0.0.1:8080` 起栈,宿主 443 → `proxy_pass http://127.0.0.1:8080`;生产即此形态);
2. 云厂商 LB/CDN 终止 TLS;
3. 在 compose 里加一个 caddy 服务自动签发(将来需要再加)。
启用 HTTPS 后记得把 ini `[auth] cookie_secure = true`(启动安全校验的生产姿态随之生效)。

## 全新服务器部署(含迁移)

```bash
# 1. 装 Docker(Ubuntu 走发行版仓库即可;内存 ≤2G 的小机先确认有 swap)
apt-get install -y docker.io docker-compose-v2 && systemctl enable --now docker

# 2. 取代码 + 配置
git clone https://github.com/zlzfun/DoramiSourceArchive.git && cd DoramiSourceArchive
cp config/production.example.ini config/production.ini
#    必改:[auth] secret(长随机串)、admin_users/user_users 首启种子密码;
#    走 HTTPS 边缘则 cookie_secure = true

# 3.(迁移场景)搬数据——LLM 配置、X token、账号、订阅、采集游标全在 DB 的
#    运行时 KV 里,拷 data/ 即全部带走,无需在新机重配:
#    老机先 docker compose stop backend(静止 WAL),再整目录拷:
#    rsync -a old:/root/DoramiSourceArchive/data/ ./data/
#    全新空库则跳过本步(首启自动建库+种子账号,LLM/X 到管理面重配)

# 4. 监听形态二选一:
echo "DORAMI_HTTP_LISTEN=127.0.0.1:8080" > .env   # A:外层有 TLS 边缘(推荐,生产即此)
#    (然后照下方「HTTPS」节配宿主 Nginx/Caddy + 证书)
#    B:纯 HTTP 直出则不写 .env,容器 nginx 直接占 80

# 5. 构建 + 起站 + 健康验证一条龙
./deploy-docker.sh
```

迁移收尾:老机 `docker compose down`(或 PM2 时代 `pm2 delete`),DNS 切到新机。
数据只有 `data/` 一个目录 + `production.ini` 一个文件,这就是 SQLite 形态下迁移成本的全部。

## PM2 路径(已退役)

生产已于 2026-07-22 完成同机切换(v3.15),观察一个完整采集日 + 日报 cron 正常后,
v3.15.1 删除了 `deploy.sh` / `ecosystem.config.js` 与 ini `[nginx]` 节。切换前的
DB 热备与 nginx 旧站点配置在生产机 `/root/backups/`;需要考古看 git 历史(tag v3.15.0 之前)。

## 网络受限环境

- PyPI / npm 镜像:见 `docker-compose.yml` 两个 build args 注释(`PIP_INDEX` / `NPM_REGISTRY`)。
- torch 恒走 PyTorch 官方 CPU 索引(`UV_TORCH_BACKEND=cpu`),体积从 CUDA 版数 GB 降到数百 MB;
  该索引若不可达,在 backend.Dockerfile 里按注释换 `--extra-index-url` 方案。
- Docker Hub 拉不动基础镜像时,配置 daemon 的 registry mirror。

## RAG

生产默认关(`[rag] enabled = false`)。开启时:把模型目录挂进容器
(compose 里已留注释行,路径与 ini `[models]` 一致),并留意后端容器内存
(bge-m3 常驻约 2GB+,必要时给 backend 加 `mem_limit`)。
