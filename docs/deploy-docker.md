# Docker 部署(生产推荐路径)

> 两条官方部署路径之一,**能装 Docker 就走这条**;装不了 Docker 的机器走
> [`deploy-baremetal.md`](./deploy-baremetal.md)(`deploy.sh`,uv + PM2 + 宿主 Nginx)。
>
> 2026-07 部署重构:取代「裸机 venv + PM2 + 宿主 Nginx + 服务器现场构建前端」的
> `deploy.sh` 路径(生产实测一个完整采集日 + 日报 cron 后,该路径于 v3.15.1
> 连同 `ecosystem.config.js`、ini `[nginx]` 节退役删除;v3.39.0 因「公网机不便装
> Docker」的真实场景扶正回归,与本路径并列)。动机与完整分析要点:
> 依赖版本锁定(uv.lock `--frozen`)、Playwright/Chromium 环境固化(镜像内 OS 恒为
> bookworm,宿主 OS 兼容性兜底全删)、发布原子化(整镜像切换)、重启自愈
> (`restart: unless-stopped` 取代缺失的 `pm2 save/startup`)、为迁移部署铺路
> (迁移 = 装 docker + 拷 `data/` 与 `production.ini` + `docker compose up -d`)。

## 形态

```
docker-compose.yml
├── backend  ← docker/backend.Dockerfile(python:3.12-slim-bookworm + uv 锁定依赖
│              + ffmpeg/ffprobe + playwright chromium;入口 docker/entrypoint.py:
│              ensure_migrated → taxonomy reconcile → uvicorn 0.0.0.0:8088)
└── nginx    ← docker/nginx.Dockerfile(多阶段:node 构建 frontend/dist → nginx:alpine
               + docker/nginx.conf;对外唯一端口)
```

- 数据全部在宿主 `./data`（SQLite / 图片媒体库 / Podcast 本地音频 CAS），卷挂载进 `/app/data`;
  容器无状态,可随意重建。
- `config/production.ini` 只读挂载,不进镜像(`.dockerignore` 同时兜底)。
- 批准的 Taxonomy catalog 是非机密运行时资产，随 backend 镜像复制；外网配置
  `[taxonomy] deployment = authority`，内网配置 `replica`，不可从 `role=all` 推断。
- 两端均显式保持 `DORAMI_RUNTIME_ROLE=all`；Podcast stage allowlist 与稳定 installation ID
  通过环境变量注入，不从 role/hostname/container ID 推断。
- 机密经环境变量注入（`DORAMI_X_BEARER_TOKEN`、`ALIYUN_AK_*`、`NLS_*` 等），
  只放宿主环境或权限受控的项目根 `.env`，不写进 INI/镜像/版本库。

## 用法

```bash
# 首次:准备配置(同裸机路径)
cp config/production.example.ini config/production.ini   # 改 secret / taxonomy deployment 等

# 二选一写入权限受控的 .env；installation ID 首次生成后须跨容器重建保持稳定
# 外网 all:
cat >> .env <<'EOF'
DORAMI_PODCAST_INSTALLATION=external
DORAMI_PODCAST_AUTHORITY_ID=<stable-external-id>
DORAMI_PODCAST_ALLOWED_STAGES=fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish
ALIYUN_AK_ID=<secret>
ALIYUN_AK_SECRET=<secret>
NLS_APP_KEY=<secret>
NLS_ACCESS_TOKEN=<secret>
NLS_TOKEN_EXPIRES_AT=<provider-unix-seconds>
EOF
# 内网 all 改为 installation=internal、stable internal authority ID，并将
# DORAMI_PODCAST_ALLOWED_STAGES 留空；内网不注入 ASR/TTS 凭据。

# 部署 / 升级(构建 → 起容器 → 健康验证一条龙)
./deploy-docker.sh

# 常用运维
docker compose logs -f backend      # 后端日志(uvicorn stdout,自动轮转 10m×3)
docker compose ps                   # 状态与健康
docker compose restart backend      # 仅重启后端
docker compose down                 # 停站(数据在宿主目录,安全)
```

Podcast ASR 服务商拉取原音频时，HMAC 授权位于固定路径
`/api/public/podcast-asr/source-audio` 的 query 中。容器 Nginx 对该精确路径关闭
access/error request log 并关闭代理缓冲；Uvicorn 在应用加载后再对同一路径
清除查询参数，其他 API 的访问日志不受影响。若使用宿主 TLS Nginx，必须
保留 [`docker/edge-nginx.conf.example`](../docker/edge-nginx.conf.example) 中的同名
exact location；改成 LB/CDN 终止 TLS 时，也要在边缘访问日志中对该路径
关闭 query 记录。

对外监听默认 80,`DORAMI_HTTP_LISTEN` 可改端口(`8080`)或收进环回
(`127.0.0.1:8080`,配合外层 TLS 反代);时区默认
`Asia/Shanghai`(影响采集任务/日报的 cron 语义),`TZ` 环境变量可覆盖。

## ini 在容器内的语义差异

| ini 节 | 容器内行为 |
|---|---|
| `[server] host/port` | **不生效**。入口固定监听 `0.0.0.0:8088`(nginx 容器经服务名 `backend` 访问);对外端口由 compose 端口映射决定 |
| `[nginx] *` | **不生效**。站点配置在 `docker/nginx.conf`(与 deploy.sh 生成版同构) |
| 其余各节 | 照常生效。`[storage]`/`[media]` 的相对路径以 `/app` 为基准；Podcast artifact root 由 Compose 显式固定为 `/app/data/podcast-artifacts`，全部落在宿主持久卷 `data/` 下 |

## HTTPS

容器只做 HTTP,TLS 终止放外层,三选一:
1. 宿主继续跑一个带证书的 Nginx 全站反代到容器(`DORAMI_HTTP_LISTEN=127.0.0.1:8080` 起栈,宿主 443 → `proxy_pass http://127.0.0.1:8080`;生产即此形态,**模板见 [`docker/edge-nginx.conf.example`](../docker/edge-nginx.conf.example)**);
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

# 4. 写 .env：先选择本机 Podcast 拓扑，再选择监听形态
cat > .env <<'EOF'
DORAMI_PODCAST_INSTALLATION=<external-or-internal>
DORAMI_PODCAST_AUTHORITY_ID=<stable-external-or-internal-id>
DORAMI_PODCAST_ALLOWED_STAGES=<external-full-list-or-empty-for-internal>
# 只有 external 节点注入 ALIYUN_AK_ID / ALIYUN_AK_SECRET / NLS_*。
DORAMI_HTTP_LISTEN=127.0.0.1:8080
EOF
#    A:上例为外层有 TLS 边缘(推荐,生产即此)
#    (然后照下方「HTTPS」节配宿主 Nginx/Caddy + 证书)
#    B:纯 HTTP 直出则不写 .env,容器 nginx 直接占 80

# 5. 构建 + 起站 + 健康验证一条龙
./deploy-docker.sh
```

迁移收尾:老机 `docker compose down`(或 PM2 时代 `pm2 delete`),DNS 切到新机。
数据只有 `data/` 一个目录 + `production.ini` 一个文件；`data/podcast-artifacts` 与数据库
必须作为同一恢复点一起备份/迁移。仅恢复数据库会留下缺失音频，仅恢复 CAS 会产生孤儿文件。

## Podcast 音频部署检查

- backend 镜像通过 Debian `ffmpeg` 包同时提供 `ffmpeg`/`ffprobe`，并在同一 apt layer 清理索引。
- Compose 将 `DORAMI_PODCAST_ARTIFACT_ROOT_DIR` 固定在持久卷内的
  `/app/data/podcast-artifacts`；不要改到容器临时文件系统。
- 上线前按卷容量设置 `total_quota_mb` 与 `minimum_free_mb`；默认分别为 10240 MiB 和
  1024 MiB。启动会自动清理过期上传临时文件和无引用且过宽限期的孤儿 blob，绝不会
  删除数据库仍引用的音频；管理端统计中的 `storage_pressure` 必须保持为 false。
- 外网 stage 为 `fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish`，
  注入 ASR/TTS secret；内网 stage 留空且不注入供应商凭据；两端 role 都是 `all`。
- 升级前备份整个 `data/`，升级后至少验证 `ffmpeg -version`、`ffprobe -version`、
  artifact 管理统计和一条已发布音频的 `HEAD`/Range 请求。普通页面访问不得产生 provider 调用。

## PM2 裸机路径(退役 → v3.39.0 扶正回归)

生产已于 2026-07-22 完成同机切换(v3.15),观察一个完整采集日 + 日报 cron 正常后,
v3.15.1 删除了 `deploy.sh` / `ecosystem.config.js` 与 ini `[nginx]` 节;切换前的
DB 热备与 nginx 旧站点配置在生产机 `/root/backups/`。

删除期间该路径在 intranet 分支为内网环境(Docker 过旧不可用)复活并持续维护,
2026-08 出现「公网机不便装 Docker」的场景后回迁 main,现为并列的第二条官方路径 ——
用法与护栏见 [`deploy-baremetal.md`](./deploy-baremetal.md)。

## 网络受限环境

- PyPI / npm 镜像:见 `docker-compose.yml` 两个 build args 注释(`PIP_INDEX` / `NPM_REGISTRY`)。
- torch 恒走 PyTorch 官方 CPU 索引(`UV_TORCH_BACKEND=cpu`),体积从 CUDA 版数 GB 降到数百 MB;
  该索引若不可达,在 backend.Dockerfile 里按注释换 `--extra-index-url` 方案。
- Docker Hub 拉不动基础镜像时,配置 daemon 的 registry mirror。

## RAG(已退役)

向量/RAG 子系统已于 v3.31 退役清仓(方案与来龙去脉见 `docs/rag-retirement-plan.md`):
compose 的 `--profile rag` 服务组(chroma + TEI)、镜像 `WITH_RAG` 构建分支与
`docker/requirements-rag.txt` 均已删除。检索由 SQLite FTS5 承担,无独立服务、无额外内存需求;
问答走「LLM 计划检索 + FTS5」两段式(算力在外部 LLM API 侧)。考古看 git 历史(tag v3.30.0 之前)。
