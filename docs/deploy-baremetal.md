# 裸机部署(第二条官方路径)

> v3.39.0 扶正。本路径 = `deploy.sh`(uv venv + PM2 + 宿主 Nginx + 现场构建前端)。
> 它曾于 v3.15.1 随生产切 Docker 退役删除,其后在 intranet 分支为内网环境复活并持续
> 维护;2026-08 出现「公网机不便安装 Docker」的真实场景后回迁 main,与
> [`deploy-docker.md`](./deploy-docker.md) 并列为两条官方路径之一。
>
> **issue #126(2026-09-21,[方案](./baremetal-rollback-plan.md))起改为「运行副本版本化」**:每次部署生成一个不可变的
> release,PM2 从 release 实路径启动;部署是有阶段落盘的事务,两级健康门不通过只**告警**不自动回滚;`./deploy.sh --rollback`
> 一键回到上次成功部署(不 checkout、不出网、不构建)。旧形态安装首次运行新脚本时自动**收养**(一次 PM2 重启)。
>
> **内网专属的 `[network] disable_tls_verify`(出网跳过 TLS 校验)不在本路径内**,
> 它仍只存在于内网适配分支 master(曾名 intranet)——公网部署既不需要也不该开。

## 选哪条路径

| | Docker(`./deploy-docker.sh`) | 裸机(`./deploy.sh`) |
|---|---|---|
| 前提 | docker + compose 插件 | uv、Node ≥20.19、Nginx(PM2 脚本代装) |
| 依赖版本 | `docker/requirements.txt` 钉版 | **同一份钉版清单**(v3.39.1 起;extras 走 `docker/requirements-<extra>.txt`) |
| OS 兼容 | 镜像内恒为 bookworm,Playwright 环境固化 | 随宿主 OS,Chromium 有三层兜底 |
| 发布 | 整镜像原子切换 | release 副本 + symlink / PM2 重起,旧 release 原样保留 |
| 回滚 | 切回上一 tag + 恢复备份(流水线不自动回滚) | `./deploy.sh --rollback`(有迁移差异时 `--restore-db` 显式恢复) |
| 重启自愈 | `restart: unless-stopped` | 脚本每次 `pm2 save`;`pm2 startup` 手动跑一次 |
| HTTPS | 容器只做 HTTP,TLS 交外层边缘 | 脚本直接生成带 TLS 的站点配置 |
| 锁 | `/run/lock/dorami-deploy.lock` | **同一把**(两条路径同机时互斥;`DORAMI_DEPLOY_LOCK_FILE` 可改,不可写即失败) |

**能装 Docker 就走 Docker**;装不了(版本过旧、策略不允许、环境受限)走本路径。

## 形态(release 布局)

```
<repo>/                                    ← 编排 checkout;运行时不读它
  releases/<txn>/
    app/                                   ← git archive <code_sha>(src/ alembic/ ecosystem.config.js …)
    app/venv -> <repo>/venvs/<指纹>/       ← 本 release 私有的 venv 指针(创建后不再改)
    app/{data,logs} -> <repo>/{data,logs}   ← 共享挂点;app/config/production.ini -> <repo>/config/production.ini
    dist/  dist.sha256  app.sha256         ← 前端产物与摘要清单(或 [nginx] releases_dir/<txn>)
    nginx/{site.conf,changes.json,snapshot.json}   ← 站点配置候选 / 本次落盘前的原状 / 落盘后的集合快照
    controller/{deploy-lib.sh,deploy-baremetal.sh,rollback.sh,env.sh}   ← 固化的回滚执行体
    manifest.json                          ← 晋升时写入(身份 / prev / DB 快照 / 阶段 / 能力位)
  venvs/<指纹>/{inputs.json,.dorami-complete,…}   ← 指纹 = requirements + extras 清单 sha + 解释器身份
  deploy-state/{in-progress.json,last-success.json,closed/,txns/,rollback}   ← 事务状态 + 稳定恢复入口(分派脚本)
  backups/baremetal/<txn>/<db>.sqlite     ← 事务 DB 快照(回滚的救援快照 *.rescue.sqlite 也在此)
  current -> releases/<txn>/app           ← 只给人和 --status 看;PM2 用实路径
<html_dir> -> releases/<txn>/dist          ← nginx root 不变,指向当前 release 的 dist
PM2: dorami-backend-v2 ← cwd = releases/<txn>/app,interpreter=./venv/bin/python(NODE_ENV=production 强制关 reload)
```

`.gitignore` 已含 `releases/ venvs/ deploy-state/ venv/ logs/ current`。`--here` 固化 dirty 工作树时的排除集合独立于
`.gitignore`(固定项 + 由配置 / 挂点生成的项 + `*.db`)。

## 前置

| 组件 | 要求 | 脚本是否代装 |
|---|---|---|
| `uv` | 必须先装 | **否**,缺失即 fail |
| Python | ≥3.10(目标 `pyproject` 的 `requires-python` 在预检独立核对) | 由 uv 处理(`uv python find`) |
| Node/npm | **≥20.19(Vite 8 + React 19)**,建议 22 LTS | 试包管理器,失败则 fail |
| Nginx | 任意(源码装亦可) | 同上 |
| PM2 | — | `npm i -g pm2` |
| ffmpeg/ffprobe | 必须,Podcast 音频封装、响度 QA 与媒体探测 | 试系统 `ffmpeg` 包,缺任一命令则 fail |
| Chromium | 可选,仅 `rss_openai_news` 渲染节点用 | 试 `playwright install`(`PLAYWRIGHT_SKIP_BROWSER_GC=1`,旧版浏览器保留给回滚),失败降级不阻断 |
| 磁盘 | 仓库所在文件系统 ≥ `DORAMI_DEPLOY_MIN_FREE_GB`(默认 2)| — |

手装的 nginx / nvm-node 常不在非交互 shell 的 PATH 里:脚本已自动并入
`/usr/sbin:/usr/local/sbin:/usr/local/bin:/usr/local/nginx/sbin` 与 nvm 的最新版本目录,
仍找不到就 `export PATH="$PATH:<安装目录>"` 后重跑。

受限网络/镜像加速:`UV_DEFAULT_INDEX=<PyPI 镜像>`、`NPM_REGISTRY=<npm 镜像>`。回滚本身**不出网**。

**依赖版本来源**(v3.39.1;issue #126 起不再 editable 安装):venv 只按入库的钉版清单 `docker/requirements.txt`
(+ `DORAMI_DEPLOY_EXTRAS=crawl4ai` 时的 `docker/requirements-crawl4ai.txt`)安装,运行时靠 `PYTHONPATH=app/src`;
清单缺失即拒绝(release 形态不做现解安装)。venv 按输入指纹版本化:清单与解释器都没变的部署直接复用上一个 venv,
半成品(无 `.dorami-complete`)在锁内删除重建。起因回顾:v3.39.0 首次公网裸机部署撞上 **mcp 2.0**,`uv pip install -e .`
现解装到 2.x 后端起不来,而同版本 Docker 镜像因走清单安然无恙。

## 用法

```bash
cp config/production.example.ini config/production.ini   # 见下节「配置」
./deploy.sh                 # 版本号最新的发布版(一键;目标 tag 须有裸机事务能力,否则提示 --code)
./deploy.sh v3.61.0         # 指定版本
./deploy.sh --here          # 部署当前工作树(dirty 也固化成快照;内网适配分支 master 用这个)
./deploy.sh --code <sha|tag>  # 由当前编排器部署任意代码对象,不 checkout(回更早版本 / 部署无事务能力的旧 tag)
./deploy.sh --rollback      # 回到上次成功部署(交互确认;非交互加 --yes;有迁移差异时加 --restore-db)
./deploy.sh --status        # 只读:当前跑什么、上次成功是什么、未收口事务、回滚目标与 DB 预判、能力位
./deploy.sh --discard-txn   # 人已手工处理,归档未收口事务(已改宿主时要求 --yes)
./deploy.sh --adopt         # 收养旧形态安装(首次运行新脚本会自动做;身份取不到时 --adopt-sha <sha>)

# 常用运维
pm2 logs dorami-backend-v2        # 后端日志
pm2 startup                       # 开机自启注册,手动跑一次(pm2 save 由脚本在每次切换后执行)
```

正向部署的流程(锁内,阶段落盘顺序即恢复发现顺序):

```
锁 → 只读解析目标 + 能力检查(tag 模式不 checkout)→ 未收口事务分派(adopt / rollback 续做;deploy 仅可证明未改宿主时归档)
→ 收养检测 → resolve(tag 模式 checkout 编排树并以目标脚本重执行)
→ [1] 系统依赖 → [2] 配置校验、磁盘、身份与回滚点(prev 采样,证据冲突默认停止)
→ 开事务(mkdir releases/<txn> → controller → in-progress → 发布 deploy-state/rollback 入口)
→ [3] 代码副本 / venv / 挂点 → 目标上下文检查(requires-python、路径探针、迁移计划、首装门)
→ [4] 在 release 副本上构建前端 → [5] nginx 候选 → 记变更集 → 落盘 → nginx -T/-t(失败按记录整体恢复)
→ [6] DB 快照 → 迁移 → taxonomy → pm2 delete(确认退出)→ 切 html_dir / current → pm2 start <release> → pm2 save → nginx reload
→ [7] 两级健康门 + 稳定窗 ─┬─ 通过:晋升 last-success、清理
                           └─ 不通过:红字告警 + pm2 日志尾 + 回滚命令,事务保留,exit 1(不自动回滚)
```

两级健康门:① 后端身份(直连 `backend_proxy_host:port` 的 `/api/health` 五项:status / version / build.ref / build.sha /
build.source=env);② 站点链路(经真实 nginx 入口取 `index.html`、index 引用的主 JS / CSS 内容摘要、经站点的 `/api/health`;
TLS 用 `--resolve` + 系统 CA 或 `DORAMI_DEPLOY_PROBE_CACERT`,不提供 `-k`;`ssl_redirect` 时核对 HTTP 入口 301);
③ 连续 `DORAMI_DEPLOY_STABLE_SECONDS`(默认 10)秒读数一致且 PID 不变。三道门共用一个预算 `DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS`
(默认 180):后端就绪太晚以致剩余时间不够跑稳定窗,同样判失败。`ssl_redirect` 时 HTTP 入口的 301 必须落到本站 HTTPS 入口
(`server_name` + `ssl_listen_port`,非 443 端口生成器会把端口写进跳转)。

脚本自带的护栏:
- **锁先于一切**:与 Docker 路径同一把(默认 `/run/lock/dorami-deploy.lock`),抢不到 exit 4;
- **能力检查**:tag 模式在 checkout 之前核对目标 tag 的 `scripts/deploy-lib.sh` 宣告了 `DORAMI_BAREMETAL_TXN`,否则一律拒绝
  (exit 11)并提示 `--code <tag>`——旧脚本没有回滚入口,切过去就丢了;
- **身份与回滚点**:prev 只取 last-success 且须与现场一致(`current` 指向、`/api/health` 或 pm2 env 的构建 sha);
  冲突默认停止(exit 24),`--no-rollback-guarantee` 显式继续(prev=null,能力位持续显示);
- **首装门**:迁移计划报 fresh 时,有既有部署证据(last-success / pm2 进程 / `html_dir` 非空 / venv / 库 / 媒体 / 备份 /
  已晋升 release)一律拒绝(exit 23,不提供覆盖);无证据的真首装需 `DORAMI_DEPLOY_FRESH_OK=1`(首装无回滚点);
- **目标上下文检查**:`requires-python` 独立核对;路径探针在目标 venv + `PYTHONPATH=app/src` 里导出所有路径型配置的
  最终值,可变存储(库 / 媒体 / 播客产物 / 备份目录)必须解析到 release 之外(缺挂点按探针结果补建),与当前运行 release
  的基准逐项相等才放行(exit 33);迁移计划算法自持但在目标上下文运行(revision 文件顶层 import 项目模块);
- **事务 DB 快照**:任何可能执行迁移的切换(正向与回滚)之前先 `sqlite3 .backup` + `integrity_check`,快照路径记进 manifest
  (`backups/baremetal/<txn>/`,不落入 Docker worker 的备份通配);
- **nginx 变更集**:站点文件 / enabled 链接 / default 站点 / 主配置 include 的原状先记后写,`-T` / `-t` 失败按记录整体恢复;
  回滚先撤销失败部署的变更集(新增项删除、被改写项恢复),再恢复目标 release 的配置集合快照;
- **切换序**:`pm2 delete` 确认退出后再切链接、`pm2 start <release>/app/ecosystem.config.js --update-env`、`pm2 save`
  (失败 = 阶段失败)——开机 resurrect 永远指向已确认的 release;
- **构建来源透传**:`DORAMI_BUILD_REF/SHA` 经 `ecosystem.config.js` 进后端进程,`/api/runtime` 透出,设置 → 关于 可核对;
  dirty `--here` 的 ref 带 `-dirty`,代码身份是固化快照 commit(pin ref `refs/dorami-deploy/<txn>`),不是 HEAD;
- **Taxonomy 启动围栏**:迁移完成后、PM2 起新进程前在目标上下文执行;冲突终止部署;
- **站点 include 复核**、**目录穿越位**(补不上只告警,提示 `[nginx] releases_dir`)、**`proxy_buffering off`**、
  **`/mcp` 的 Host 改写**:与旧形态相同。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 步骤失败或健康门未通过(事务保留;终端有精确的回滚命令) |
| 2 | 用法 / 需要 `--yes` 确认 |
| 4 | 锁被占 |
| 11 | 目标 tag 的脚本无裸机事务能力(用 `--code`) |
| 20 | 存在已改动宿主的未收口事务(`--rollback` 或 `--discard-txn`) |
| 23 | 首装门 |
| 24 | 身份证据冲突 / 收养未完成 / 非 SQLite 库 |
| 30 | 无回滚点或材料缺失 |
| 32 | 回滚需要 `--restore-db` |
| 33 | 路径探针不一致 |

## 回滚

```bash
./deploy.sh --status                  # 先看:回滚目标、DB 预判(不覆盖 / 补迁移 / 需要 --restore-db)、未收口事务
./deploy.sh --rollback                # 交互输入 yes;脚本 / cron 里加 --yes
./deploy.sh --rollback --restore-db   # 当前库领先于目标代码的迁移图时才需要(默认拒绝并打印快照时刻与丢失窗口)
./deploy.sh --rollback --to <txn>     # 上一版是刚被回滚掉的那版时,--to 只接受相邻的那个事务(往回走);更早的用 --code
```

- **目标选择**:未完成的回滚事务 → 续做同一目标(永不翻转);已改动宿主的失败部署 → 它的 prev;否则 last-success 的 prev;
  **只回一代**,更早的版本用 `./deploy.sh --code <sha>` 正向部署(内网 `--here` 场景一律给 sha,不给 tag 名)。
- **动作**(全部在 `releases/<txn>/controller/` 里的固化副本上执行,不读工作树、不出网、不构建):撤销失败部署的 nginx 变更集
  → 恢复目标的配置集合快照 → `nginx -t` → `pm2 delete` → **救援快照**(只创建一次)→ DB 处置 → 切 `html_dir` / `current`
  → `pm2 start <目标 release>` → `pm2 save` → reload → 两级健康门 → 晋升(last-success 记 `kind=rollback`,prev = 被回滚掉的那版)。
- **DB 分流**(目标上下文的迁移计划):目标认识当前库且无待执行 → 不覆盖;有待执行 → 回滚将向前补迁移;当前库领先(incompatible)
  → 默认拒绝(exit 32),`--restore-db` 用**被回滚事务部署前的快照**覆盖——快照之后写入的数据丢失,命令会打印快照时刻与现在;
  任何一种都先做**救援快照**(只创建一次)。`--no-rescue-snapshot` 只能与 `--restore-db` 同用,且只对**已损坏**的库(文件不是数据库 /
  `integrity_check` 失败)生效;健康库不允许跳过,权限 / 磁盘 / I/O 错误也不属此例(先修环境)。非 SQLite 库回滚不处置,
  `--restore-db` 直接拒绝。
- 回滚失败同样只告警、事务保留;再次 `--rollback` 续做同一目标;下一次正向 `./deploy.sh …` 也会先在同一把锁内自动续做它(已由人确认过),
  完成后再继续部署。被回滚掉的版本可以 `--code <sha>` 重新部署;只有晋升过的相邻事务才会被提示 `--to`。
- **服务没在跑时**(机器重启但 `pm2 startup` 没配等):pm2 查询成功且列表里没有受管 app、`/api/health` 不可达、`current` / `html_dir`
  仍指向 last-success、其材料完整 → 部署照常进行并保留回滚点(日志明示「受管服务未运行,依据 last-success 及材料确认」);
  pm2 查询失败 / 进程从别的目录跑 / 材料缺失 → exit 24。
- **收养中断**(维护窗内):`./deploy.sh --rollback`(或直接跑 `deploy-state/rollback`)会先由固化执行体续做收养,不需要工作树。
- 材料寿命:引用集合(last-success / in-progress 的 target / prev / recover_from、当前入口的 controller、`current` /
  `html_dir` 指向、pm2 进程与 `dump.pm2` 的 cwd、7 天内的 closed 事务)之外按数量清理:release 3、venv 2、快照 10
  (`DORAMI_DEPLOY_KEEP_RELEASES` / `_VENVS` / `_SNAPSHOTS`);pin ref 随 release 一起删。

## 收养(旧形态 → release 形态)

首次用新脚本部署一台已在跑旧形态(仓库内 `venv/`、`html_dir` 真实目录、PM2 从仓库根起)的机器时自动触发,也可 `./deploy.sh --adopt`
手动做。它是 `kind=adopt` 事务:身份取运行中的 `/api/health` `build.sha`(或 pm2 env),取不到要 `--adopt-sha <sha>`
(记 reproducible=false);`git archive` 该 sha 到 `releases/legacy-<txn>/app`;**venv 不移动**(`app/venv -> <repo>/venv`,
并移除 editable finder,核对 legacy 上下文的 `sys.path` 不含工作树 `src`);复制 `html_dir/*` 到 legacy dist(校验清单);快照当前
生效的 nginx 集合;然后 `pm2 delete` → 旧 `html_dir` 挪到 `<html_dir>.adopt-<txn>` + 建 symlink → `pm2 start legacy` →
`pm2 save` → 两级健康门 → 晋升 last-success(`kind=adopt`,prev=null)。这是唯一的维护窗(一次重启);中断由未收口事务分派续做,
不归档。之后的第一次正向部署以 legacy release 为回滚点。
前提:运行中的代码要能透出构建身份(`/api/health` 的 `build.*`,v3.56+),否则健康门过不了——先人工核对再 `--adopt-sha`。

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

[runtime]
role = all                   # 外网/内网均保持 all

[podcast_artifacts]
root_dir = data/podcast-artifacts
total_quota_mb = 0             # 0 = 不设固定业务硬上限
minimum_free_mb = 1024
staging_ttl_seconds = 3600

[nginx]
server_name = your-domain.example.com   # enable_ssl 时不能是 _
enable_ssl = true
ssl_cert_file = /etc/letsencrypt/live/your-domain.example.com/fullchain.pem
ssl_key_file  = /etc/letsencrypt/live/your-domain.example.com/privkey.pem
# releases_dir = /var/www/dorami-releases   # 仓库在 /root 之类 nginx worker 穿不过的位置时,dist 复制到这里(sudo 归属)
```

- **`html_dir` 在 release 形态下是 symlink**(指向当前 release 的 dist),不要往里手工放文件;
- **路径型配置必须指向共享位置**:相对路径按代码根(release 的 `app/`)解析,`data/…` 由挂点承接;别的相对根(如 `state/media`)
  会被探针发现并自动建挂点 `app/state -> <repo>/state`;若与源码路径冲突(如 `src/media`)部署被拒绝;
- **改了存储位置会被拦**(exit 33):每次成功部署把库 / 媒体 / 播客产物 / 备份目录的最终路径记进 manifest 作基准,下次部署逐项相等才放行——
  把 `database_url` 指到另一份库(哪怕迁移版本相同)会被当作换库拒绝;确要迁移存储,先自行搬数据,再
  `DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 ./deploy.sh … --no-rollback-guarantee` 显式重设基准(本次没有回滚点);
- **非 SQLite 库**(外部 PostgreSQL 等):加 `--no-rollback-guarantee` 显式放弃库恢复能力后完整部署(迁移照常),回滚不处置库;
- `[server] reload` 必须为 `false`(`config.py` 的 fallback 是 `true`,`ecosystem.config.js` 的 `NODE_ENV=production`
  另有守卫兜底,显式写上更稳)。

运行 `deploy.sh` 前必须选择 Podcast installation 并给出跨重启稳定的 authority ID。
`external` 默认开启完整 ASR/TTS 处理链,`internal` 默认关闭全部处理阶段。当前阿里 ISI
的 ASR 使用 AK/SK 签名;TTS 使用 NLS Token,并用 AK/SK 刷新:

```bash
# 外网 all
export DORAMI_ARCHIVE_AUTHORITY_ID=<stable-external-archive-id>
export DORAMI_PODCAST_INSTALLATION=external
export DORAMI_PODCAST_AUTHORITY_ID=<stable-external-id>
export ALIYUN_AK_ID=<secret>
export ALIYUN_AK_SECRET=<secret>
export NLS_APP_KEY=<secret>
export NLS_ACCESS_TOKEN=<secret>
export NLS_TOKEN_EXPIRES_AT=<provider-unix-seconds>

# 内网 all(只同步和展示,不配置供应商凭据)
export DORAMI_ARCHIVE_AUTHORITY_ID=<stable-internal-archive-id>
export DORAMI_PODCAST_INSTALLATION=internal
export DORAMI_PODCAST_AUTHORITY_ID=<stable-internal-id>
```

首次启动前确认 artifact root 所在分区至少保留 `minimum_free_mb`;该目录只持久保存生成的
中文精简音频。启动会在跨进程 CAS 锁内清理过期 `.incoming` 文件和无引用孤儿,不会删除
数据库仍引用的生成音频。管理端 `/api/admin/podcast-artifacts/stats` 会报告实际占用、
分区可用空间、总配额、临时文件和压力状态。外网 ASR 把 RSS enclosure 原地址直接交给
阿里云;本地只在 staging 完成媒体校验,随后删除原始字节并持久化轻量
`source_media_snapshot`,无需配置原音频公网回源路由。

`pm2 start --update-env` 会继承这些变量(回滚时由固化执行体在同一 shell 环境里 `pm2 start`,同样继承)。不要把 provider secret 写入
`production.ini`、shell history 或仓库;建议由主机 secret manager 注入。部署脚本会安装并
复核 `ffmpeg` 与 `ffprobe`,并创建环境变量或 INI 指定的 artifact root。

### 环境变量(部署脚本)

| 变量 | 含义 |
|---|---|
| `DORAMI_DEPLOY_LOCK_FILE` | 锁文件(默认 `/run/lock/dorami-deploy.lock`,与 Docker 路径同一把;目录不可写即失败要求显式配置) |
| `DORAMI_DEPLOY_EXTRAS` | 逗号分隔的 extras(如 `crawl4ai`),按 `docker/requirements-<extra>.txt` 钉版安装并进 venv 指纹 |
| `DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS` / `_ATTEMPTS` | 健康门预算(默认 180 s / 90 次) |
| `DORAMI_DEPLOY_STABLE_SECONDS` | 稳定窗(默认 10) |
| `DORAMI_DEPLOY_PROBE_CACERT` | 站点链路门的 TLS CA 文件(默认系统 CA;不提供 `-k`) |
| `DORAMI_DEPLOY_FRESH_OK=1` | 真首装授权(无任何部署证据时才生效) |
| `DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1` | 运维确要迁移存储位置(换盘)时显式重设路径基准;**必须与 `--no-rollback-guarantee` 同用**(本次 prev=null,跨存储布局没有回滚保证);先自行搬数据 |
| `DORAMI_DEPLOY_MIN_FREE_GB` | 磁盘预算(默认 2) |
| `DORAMI_DEPLOY_SNAPSHOT_MAX_MB` | dirty `--here` 固化快照的体积阈值(默认 64) |
| `DORAMI_DEPLOY_KEEP_RELEASES` / `_VENVS` / `_SNAPSHOTS` / `_CLOSED_KEEP_DAYS` | 清理保留数(默认 3 / 2 / 10 / 7 天) |
| `DORAMI_DEPLOY_STATE_DIR` / `_RELEASES_DIR` / `_VENVS_DIR` / `_SNAPSHOT_DIR` | 布局目录(默认都在仓库内) |
| `DORAMI_NGINX_ETC_DIR` | nginx 配置根(默认 `/etc/nginx`) |
| `NGINX_*` / `BACKEND_PROXY_*` / `PM2_APP_NAME` / `VENV_DIR` | 与 ini 同名项的覆盖(旧形态沿用) |

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

⚠️ **别用 `certbot --nginx`**:每次部署都把站点文件整体渲染成候选再落盘,certbot 插进去的行下次部署就没了
(而且会被回滚的配置集合快照抹掉)。`certonly` + ini 里固定证书路径,续期只换文件内容、配置不动。

防火墙开 **80 + 443**(80 留给跳转与续期);后端只监听 `127.0.0.1:8088`,不要对外放行。
SELinux 开启时需 `setsebool -P httpd_can_network_connect 1`,否则 nginx 反代被拒。

## 全新服务器部署(含迁移)

```bash
# 1. 前置:uv / Node≥20.19 / Nginx / ffmpeg;时区 timedatectl set-timezone Asia/Shanghai(cron 语义)
# 2. 取代码 + 配置
git clone <repo> && cd DoramiSourceArchive
cp config/production.example.ini config/production.ini   # 按上节改

# 3.(迁移场景)搬数据——数据库状态与 Podcast 本地 CAS 必须取同一停机恢复点;
#    拷整个 data/ 才能同时带走账号、采集游标和已发布衍生音频:
#    老机先 pm2 stop dorami-backend-v2(静止 WAL),再整目录拷:
rsync -a old:/path/DoramiSourceArchive/data/ ./data/
#    全新空库则跳过(首启自动建库 + 根管理员 admin/admin,登录后立刻改密码)

# 4. 部署 + 自启(真首装没有任何部署证据时须显式授权;搬了数据的迁移场景库文件本身就是证据,不需要)
DORAMI_DEPLOY_FRESH_OK=1 ./deploy.sh
pm2 startup
```

机密走环境变量时(如 `DORAMI_X_BEARER_TOKEN`)需在跑 `deploy.sh` 前 `export`——
`pm2 start --update-env` 会把当时的 shell 环境带进后端进程;也可以登录后在
设置柜 → 凭据里填(KV 覆盖 env,见 CLAUDE.md 的*外部凭据统一保管层*)。

部署完成后检查 `ffmpeg -version`、`ffprobe -version`、artifact 管理统计和一条已发布
音频的 `HEAD`/Range 请求。备份必须覆盖数据库与 `data/podcast-artifacts`;普通 Reader
页面的验收同时断言 provider 调用为 0。

## 与内网适配分支(master)的关系

内网适配分支(曾名 `intranet`,2026-08 更名 `master`——内网代码托管平台内部开源仓以 master 为默认主干名,同名对齐免去第三个分支名的来回同步)自此不再自带 `deploy.sh`/`ecosystem.config.js`/ini 两节(改用 main 版本),
其独有面收敛为:`[network] disable_tls_verify` 开关及各 httpx client 的
`verify=settings.network.tls_verify` 接线、分支须知块与 `.claude/` 钩子。
内网跑 `./deploy.sh --here`(切 tag 会切到 main 的发布版而丢掉内网适配),回滚点是代码 sha(dirty 树固化成的快照 commit)
而不是 tag;`--status` / 告警里打印的正向命令一律是 `--code <sha>`。收养与回滚都不需要联通 GitHub。
