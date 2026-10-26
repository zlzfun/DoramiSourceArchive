# 部署与运维(ops/)

> 状态:耐久参考。总览两段自 `CLAUDE.md`「Production deploy」原文迁出(2026-10-21);逐篇手册见下表。

**两条官方部署路径**:Docker(推荐)与裸机 PM2(装不了 Docker 时)。**两者都按 tag 部署**(v3.56 起,见 *Versioning* 与 `docs/ops/release-process.md`):`./deploy-docker.sh [vX.Y.Z|--here]` / `./deploy.sh [vX.Y.Z|--here]`,缺省最新发布版;部署前 SQLite 在线备份到 `backups/`,回滚 = 切回上一 tag + 恢复备份。**Docker 节点 v3.59 起默认由流水线部署**(issue #102,`docs/archive/auto-deploy-plan.md`):tag 推上去 → `release.yml` 建 Release → 串联 `deploy.yml` 停在 Environment `production` 等批准 → runner 经 SSH forced command 触发生产机**仓库外**的 launcher / worker(`docker/dorami-deploy*.example` 模板;worker 是锁与跨版本状态的唯一 owner:ref / 协议核验、in-progress / last-success 事务、方向与单调护栏、首装门),worker 再跑目标 tag 自己的 `./deploy-docker.sh`(预检 → build → 目标镜像 `--check-config` / `--plan-migrations` → up → `/api/health` 五项核对);只部署宣告了 `DORAMI_DEPLOY_PROTOCOL` 的 tag,不自动回滚,手工 `./deploy-docker.sh` 保留为兜底且与流水线共用一把锁。

**Docker 是生产推荐路径**(2026-07 部署重构,见 [`docs/ops/deploy-docker.md`](../../docs/ops/deploy-docker.md)):`./deploy-docker.sh` = 构建镜像 → `docker compose up -d` → 全链路健康验证。双容器形态:`backend`(`docker/backend.Dockerfile`:python:3.12-slim-bookworm + uv.lock `--frozen` 锁定依赖 [torch 经 `UV_TORCH_BACKEND=cpu` 改道 CPU 轮子、CUDA 伴生包从安装清单滤除] + Playwright Chromium;入口 `docker/entrypoint.py` 固定监听 0.0.0.0:8088,ini 的 `[server]`/`[nginx]` 节在容器内不生效)+ `nginx`(`docker/nginx.Dockerfile` 多阶段:node 构建前端 → nginx:alpine + `docker/nginx.conf`)。数据(`./data`)与 `config/production.ini` 从宿主挂载,容器无状态;机密走环境变量(`DORAMI_X_BEARER_TOKEN`);`shm_size: 1gb` 是 Chromium 渲染的硬需求;`TZ` 默认 Asia/Shanghai(cron 语义)。

**裸机 PM2 路径**(`deploy.sh` + `scripts/deploy-baremetal.sh` + `ecosystem.config.js` + ini `[server]`/`[nginx]` 两节,见 [`docs/ops/deploy-baremetal.md`](../../docs/ops/deploy-baremetal.md)):**issue #126 起是「运行副本版本化」**——每次部署生成不可变的 `releases/<txn>/`(`git archive` 代码副本 + 按输入指纹版本化的私有 venv 指针 + 在副本上构建的 dist + nginx 配置集合快照 + 固化的回滚执行体),PM2 从 release 实路径起,`html_dir` / `current` 是 symlink;部署是锁内事务(与 Docker 路径同一把锁;阶段落盘;tag 模式在 checkout 前核对目标宣告 `DORAMI_BAREMETAL_TXN`,否则提示 `--code`),流程 = 系统依赖 → 配置 / 身份与回滚点 → 开事务 → 代码副本 / venv / 挂点 → 目标上下文检查(requires-python / 路径探针 / 迁移计划 / 首装门)→ 构建 → nginx 候选 + 变更集 → **DB 事务快照 → `ensure_migrated` → taxonomy** → `pm2 delete` → 切链接 → `pm2 start <release>` → `pm2 save` → reload → **两级健康门**(后端身份五项 + 经真实入口的站点链路)——不通过只**告警不自动回滚**;`./deploy.sh --rollback` 在树外固化执行体里回到上一 release(不 checkout / 不出网 / 不构建;有迁移差异时默认拒绝,`--restore-db` 显式恢复;只回一代,更早用 `--code <sha>`);`--status` / `--discard-txn` / `--adopt`(旧形态安装首次运行自动收养,一次 PM2 重启)。该路径 v3.15.1 随生产切 Docker 退役删除,删除期间在 intranet 分支为内网环境复活维护,**v3.39.0 因「公网机不便装 Docker」的真实场景回迁 main 扶正**(回迁时清理了当年的 RAG 形态判定——向量子系统已随 v3.31 退役)。裸机路径下 ini 的 `[server]`(PM2 后端监听)与 `[nginx]`(站点生成输入)两节**生效**;Docker 路径下二者是死节(容器不读),`[server]` 另在 dev 裸起(`python src/main.py`)时使用。开机自启注册 `pm2 startup` 手动跑一次(`pm2 save` 由脚本在每次切换后执行;Docker 路径由 `restart: unless-stopped` 承担)。**内网专属的 `[network] disable_tls_verify` 不随本次回迁进 main**,仍只在内网适配分支 master(曾名 intranet,2026-08 更名对齐内网托管平台主干名)。

## 文档

| 文档 | 用途 |
|---|---|
| [`deploy-docker.md`](./deploy-docker.md) | Docker 双容器部署(推荐路径):compose 形态 / 用法 / HTTPS / 全新服务器与迁移 / 受限网络镜像源 |
| [`deploy-baremetal.md`](./deploy-baremetal.md) | 裸机 PM2 路径(issue #126 起 release 形态):流程 / 护栏 / `--rollback` / 收养 / HTTPS |
| [`release-process.md`](./release-process.md) | tag 即发布:`scripts/release.sh`、CI 门禁、自动 Release 与部署流水线、回滚 |
| [`configuration.md`](./configuration.md) | `config/*.ini` 唯一配置参考(操作视角 + 配置节语义总表 + 环境变量) |
| [`oss-storage.md`](./oss-storage.md) / [`oss-local-fallback.md`](./oss-local-fallback.md) | OSS 持久媒体与 Archive Sync 路径 / 从 OSS 回退本地;策略样例 `oss-*.example.json` |
| [`storage-backups.md`](./storage-backups.md) | 自动备份与离线恢复(默认关闭) |
| [`taxonomy-v1-deployment.md`](./taxonomy-v1-deployment.md) | Taxonomy v1 发布资产与 authority / replica 启动姿态 |
| [`aliyun-dual-node-deployment.md`](./aliyun-dual-node-deployment.md) | 阿里云外网 / 内网双节点上线指导 |
| [`bailian-singapore-deployment.md`](./bailian-singapore-deployment.md) | 新加坡百炼 ASR / TTS 接入 |
| [`full-analysis-backfill.md`](./full-analysis-backfill.md) | 历史文章 `full_analysis` 回填操作 |
| [`runbook.md`](./runbook.md) | 排查手法沉淀:日报漏报 / cron 缺席 / 分析回填吞吐 / 部署后核对 |
