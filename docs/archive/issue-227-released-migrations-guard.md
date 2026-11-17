# 已发布迁移文件逐字节守卫 + 部署健康验证诊断与宽限(issue #227)

> **归档于 2026-11-09(issue #227,开发版 3.68.0-alpha,纯后端 / 脚本机制修复,检视与 CI 通过后按例外直接合入、待发布)**:现状以 `docs/dev/migrations.md`、`docs/ops/release-process.md` 与代码为准,文中路径与状态为当时记录。
> 状态:已落地待发布(2026-11-09 开工;Codex 交叉检视见 §7)。

> 实现方:Claude Code(Fable 5.1);检视方:Codex(§7)。分支 `feat/issue-227-migration-byte-guard`。
> 起点:[issue #227](https://github.com/zlzfun/DoramiSourceArchive/issues/227)——2026-10-26 发 v3.67.0 时流水线与手工路径各失败一次。

## 1. 现象与根因

### 1.1 流水线:worker 第 7 道门 fail closed(规则对,守卫缺位)

v3.66.0 → v3.67.0 有 4 个既有迁移文件被改动:#211 文档整理波改了两处 docstring 里的文档路径(`df8f95fe0439`、`f2c9d4e07a11`),
#220 事件层 wave 1 给 `a3f64d8c2e11` / `f3a8c1d9e2b4` 加了收养守卫。生产机 worker 的 `step_migration_diff` 向前部署时对
`alembic/versions` 的任何 M / D 一律拒绝、没有放行开关(`docs/archive/auto-deploy-plan.md` 第 7 条)。规则没错——已发布的迁移
是历史,改了就说不清生产库到底是按哪个版本演进的;错的是**只在发版那天才知道**。本波用 `--baseline v3.66.0` 跑新守卫,精确复现这 4 个文件。

### 1.2 手工路径:`bad-json` 87 次,推测是探针打到了 TLS 边缘

issue 里的推测是「6 个迁移在 790 MB 库上执行期间 nginx 回 502,预算在站点就绪前耗尽」。两处对不上:

- 旧轮询用 `curl -fsS`:`-f` 对 4xx / 5xx 不输出响应体,判定会是「无响应」,不会是 `bad-json`;`bad-json` 只能来自 **2xx / 3xx 的非 JSON 体**。
- `docker-compose.yml` 里 nginx `depends_on: backend: condition: service_healthy`:`docker compose up -d` 启动 nginx 前会一直等
  backend healthy,等到了才返回,[7/7] 的轮询开始时迁移早已结束;迁移期如果真拖过 healthcheck 的宽限(start_period 60 s + 3 × 30 s),
  失败形态是 `up` 报「dependency failed to start … unhealthy」,根本到不了轮询(Codex R1 按 compose v2.39 的 `up.go` / `convergence.go`
  复核:先 create / recreate 再 start 并等依赖,所以那一刻新 nginx 可能已 Created、旧 nginx 已停——「nginx 未被重建」的说法不成立,已改)。

**推测**对得上的是探针地址:脚本只看 `DORAMI_HTTP_LISTEN` 环境变量,worker 从 `/etc/dorami-deploy.conf` 导出所以流水线没事;
人手工在 shell 里跑 `./deploy-docker.sh v3.67.0` 时若没导出,探针退到 `http://127.0.0.1:80`——生产 80 是宿主 TLS 边缘
(`docker/edge-nginx.conf.example`:`location / { return 301 https://$host$request_uri; }`),301 的 HTML 页在 `-f` 下照样输出
(3xx 不算错误)→ 每 2 s 一次 `bad-json`,87 次正好 180 s;容器那边早就好了,公网自然正常。两点保留(Codex R1 F5):旧函数遇到
空响应不清空此前的 verdict,issue 里的「87 次后报 bad-json」只能证明**至少一次**收到非 JSON 体,中间若有 502 会被隐藏;
v3.67.1 流水线通过只说明 worker 探的是 conf 里的 `127.0.0.1:8080`,不证明那次手工 shell 的环境。要确认历史根因需要当时的环境或
响应证据(待用户核实,不要求访问生产机);今后同类失败会直接打出状态码与响应体,不再靠推。

## 2. 做法

1. **仓内守卫** `scripts/check_released_migrations.py`:`git ls-remote --tags --refs origin` 取版本号最新的 `vX.Y.Z`
   (按数字元组排,不是字母序;`-rc` 等非正式 tag 不算),本地没有就只 `fetch` 那一个 tag(浅仓库保持浅),本地有则核对与远端
   是同一对象;`git ls-tree` 确认基线下目录非空;`git diff --no-renames --name-status <tag> -- alembic/versions` 对**工作树**比对:
   A 放行并计数,M / D / T 红(改名 = D + A,只点名旧路径);取不到基线 rc 2(远端无正式 tag、基线下无迁移文件、本地 tag 陈旧),
   CI 里一样是红,不静默放行。只比最新 tag 就够:每个 tag 都经过对上一个 tag 的核对,归纳下来任何曾发布的文件都不会变——**前提是
   发布纪律**(Codex R1 F6):正式版本号单调递增、tag 打在 main 上且不移动、每次发版前该提交已对上一正式版通过本守卫;直接推 main
   绕过 CI 后打 tag、在非 main 提交打更大的 tag、force 移动 tag 都破坏前提,全新 CI 检出没有旧引用、检测不出历史移动。本轮不扩成
   发布资格审计。
   CI `backend-deploy` 加无条件 step(装依赖之前,只用 git + 标准库);`tests/test_ci_workflow.py` 守它存在、不挂 `if`、不 `continue-on-error`,
   并加三个反向对照。`AGENTS.md` 硬规则 1、`.claude/rules/migrations.md`、`docs/dev/migrations.md` 写明:改历史行为加新 revision
   或改 `alembic/env.py` / `storage/migrations.py` 的收养逻辑,docstring / 路径过期也不回改旧文件;本开发波里新加、尚未发版的迁移仍可改。
2. **诊断**(`scripts/deploy-lib.sh` `deploy_wait_healthy`):去掉 `-f`,`-o` 收体、`-w '%{http_code}'` 收码;只有 2xx 交给五项核对,
   其它状态码判定记作 `http-<code>`,连不上 / 超时仍是「无响应」;新结果变量 `DEPLOY_HEALTH_LAST_HTTP` / `DEPLOY_HEALTH_BODY`,
   `deploy_health_body_excerpt` 把前 200 字节压成一行。失败报告:判定 + 探针地址与来源 + 「最后一次响应 HTTP 码,前 200 字节」+
   提示(3xx = 探到边缘、核对 `DORAMI_HTTP_LISTEN`;502 / 503 / 504 = 容器 nginx 连不上 backend)。裸机路径共用该函数,只多得信息、不变语义。
3. **探针地址以 compose 自己的解析为准**(Codex R1 F1 后改形;首版自己解析 `.env`,引号 + 行尾注释、`${VAR}` 展开、显式空值三种合法写法
   都会算错):`deploy_compose_published_hostport nginx 80` 读 `docker compose config --format json` 里 nginx 把容器 80 发布到宿主的
   `host_ip:published`(空 / 0.0.0.0 → 127.0.0.1),shell 环境 > `.env` > 默认、`${VAR:-80}` 对空值取默认等全是 compose 的语义;python
   只取这两个字段、不回显其它配置。解析不到才回退「环境变量 > 80」并提醒。日志打「探针 … 来源:compose 解析」;`/etc/dorami-deploy.conf`
   的值与实际发布端口不一致时提醒(流水线按 conf 探测),不采用。
4. **迁移期的预算放对地方**:`docker-compose.yml` healthcheck `start_period: ${DORAMI_BACKEND_HEALTH_START_PERIOD:-60s}`;脚本在 `up -d` 前
   用 `deploy_compose_healthcheck_start_period_seconds backend` 读 compose 解析出的 `start_period`(Go 时长串 `1m0s` / `20m0s` 折秒):
   不等于默认 60 s 即视为显式(shell 或 `.env` 里设的都算,Codex R1 F2),照旧不导出;否则按 `deploy_migration_grace_seconds`(待执行迁移数 ×
   `DORAMI_DEPLOY_HEALTH_PER_MIGRATION_SECONDS`(默认 60)× max(1, ceil(库 GiB)),封顶 `DORAMI_DEPLOY_HEALTH_MIGRATION_GRACE_MAX_SECONDS`
   (默认 1200,`deploy.yml` 整 job 40 min))导出 60 + 宽限(显式恰为 60 s 时也走自动值,结果 ≥ 60,无害)。`up -d` 失败单独报:
   「backend 在宽限 + 3 次探测内没有 healthy,nginx 可能尚未启动、站点可能不可达;按 `compose ps` / `logs backend` 判断迁移仍在执行还是已失败」
   并 dump 状态(Codex R1 F3 改口,不再说「会自行完成 / nginx 未重建」)。[7/7] 轮询预算仍 180 s(此时只等 nginx 起来),次数默认预算 / 2
   (原来写死 90,预算一调就被次数截断)。790 MB 库、6 个迁移 → 宽限 420 s;两个旋钮进 conf 样例。
5. **健康轮询的结果变量先初始化再 `mktemp`**(Codex R1 F4):临时目录不可写时判定记 `tmpfile-error`,Docker / 裸机调用方仍打出完整失败报告,
   不撞 `set -u`。

## 3. 有意不做

- 不给 worker 第 7 道门加放行开关,不改其规则;不回改 v3.66.0 → v3.67.0 那 4 个文件(已随 v3.67.x 发布,它们现在就是基线)。
- 不改 `.github/workflows/deploy.yml` 公网复核那段 `curl -f`:它看的是公网 https,不存在探错端口的问题。
- 裸机路径(`scripts/deploy-baremetal.sh`)不加宽限:迁移在 `pm2 start` 之前显式 `alembic upgrade`,健康门不经历迁移期。
- 不做「迁移完成后再起算」的日志探测:`up -d` 本身就等到 backend healthy,宽限放在 healthcheck 上更直接。
- 不改 healthcheck 的 `interval` / `retries`(宽限期内失败不计数即可;宽限之后 3 × 30 s 的判定节奏保持)。

## 4. 第 3 项核对:手工切换后 worker 的基线

2026-10-26 手工部署写了 `manual-switch.json`;2026-10-27 发 v3.67.1 流水线实测:worker 判 last-success 失效、从容器读基线 bb4870a、
迁移文件 0/0/0、七道门全过(issue 评论)。行为正确,无需改。

## 5. 部署顺序与混版本

不触及 Archive Sync、不改任何同步流。compose 变量有默认值 `60s`,旧脚本 / 手工 `compose up` 不受影响;新脚本配旧 conf(没有两个新旋钮)
按默认 60 / 1200 走;worker(仓外脚本)无需重装——它只透传 `DORAMI_HTTP_LISTEN` 与 `DORAMI_DEPLOY_CONF`,新旋钮经 conf 读。
内网 `master` 同步无特殊顺序。生产侧用户只需核对 `/etc/dorami-deploy.conf` 与项目 `.env` 的 `DORAMI_HTTP_LISTEN` 一致。

## 6. 自测

- `tests/test_released_migrations_guard.py`(15 例):一致 / 新增(含未提交)/ 改写 / 仅模式 / 删除 / 改名 / 类型变化 / 版本号排序
  (`v1.10.0` > `v1.9.0`,rc 不算)/ 显式基线 / 离线 / rc 2 三种(远端无正式 tag、基线下无文件、本地 tag 陈旧)/ CI 形态浅克隆
  (`file://` 才真浅;只取基线 tag、保持浅)/ 真实仓 `--offline` 冒烟(无本地 tag 时跳过)。真实仓:基线 v3.67.4 88 个一致、新增 4 个;
  `--baseline v3.66.0` 复现 4 个 M。
- `tests/test_ci_workflow.py`:+1 守卫、+3 反向对照(删步骤 / `continue-on-error` / 挂 `if`)。
- `tests/test_deploy_lib.py`:+2(compose 助手:发布端口 host_ip 归一 / 范围取首 / 只认 target 80、Go 时长折秒、缺字段与 docker 失败 → 空 + rc 1;
  宽限公式、稀疏 1.5 GiB 文件 → ×2、上限、conf 与环境变量优先级)。
- `tests/test_deploy_scripts.py`:+5(80 端口 301 边缘实况 → `http-301` + 提示 + conf 不一致提醒,`.env` 带引号 + 行尾注释后探 8080 通过,
  显式空环境变量仍按 compose 取 80,compose 解析失败回退并提醒;200 非 JSON 打 HTTP 码 + 前 200 字节 + 截断标记、502 提示、从未连上仍「无响应」;
  宽限随迁移数 / conf / shell 显式值 / `.env` 显式值不导出 / 上限;`up` 失败报告与 dump;`mktemp` 失败仍完整报告);
  curl 桩支持 `-o` / `-w`(含 rc 7 → `000`、`-f` 4xx+ 仍出状态码)/ 按地址回 301,docker 桩模拟 `compose config --format json` 的最终解析
  (环境 > `.env` > 默认)并记录 `up` 时的 `start_period`。
- CI 形态真实远端核验:对 GitHub 做 `git clone --depth 1 --no-tags`,脚本 4.9 s 内只取到 v3.67.4 一个 tag、仓库保持浅、88 个文件一致、新增 4 个。
- 全量 `tests/test_deploy_*.py tests/test_migrations.py`(CI backend-deploy 形态)220 例、其余单元 3457 例全绿;`scripts/check_docs.py` 0 问题。

## 7. 检视(Codex 本地协商式,`gpt-6-astra` + effort ultra,`codex exec` 只读沙箱)

- **R1**(对提交 `874cfa48`):0 P1、4 P2、3 P3,结论「需修后复检」。F1 探针自解析 `.env` 与 compose 不等价(引号 + 注释 / `${VAR}` / 显式空值,
  并指出测试用空串模拟 unset 把第三种错固化);F2 `.env` 里显式 `DORAMI_BACKEND_HEALTH_START_PERIOD` 被脚本导出覆盖;F3 `up` 失败文案
  「nginx 未重建、迁移会自行完成」不成立(compose 先 create 再 start 等依赖);F4 `mktemp` 失败后调用方撞 `set -u`、丢失失败报告;
  F5 301 根因只能写「推测」;F6 归纳保证要写前提;F7 观察项(响应体无大小上限、信号中断不清临时文件、curl 桩 `-w` 失败路径不真)。
  其余关注面(基线排序 / 浅 fetch / tag 对象核对 / 退出码、裸机不按 verdict 文本分支、宽限公式与封顶)未发现缺陷;它独立复核了
  「`up -d` 启动 nginx 前等 backend healthy」的前提并认可宽限放在 healthcheck 上。
- **表态**(`.review/response-r1.md`):全部接受、无分歧;F1 / F2 按其建议改形为「以 `docker compose config --format json` 的解析结果为准」,
  删掉 `deploy_dotenv_value`;F7 的 curl 桩顺手修,响应体上限与信号清理记观察期(backlog)。
- **R2**(对提交 `7bb54dce`,只对照 `.review/fixlist-r1.md`):F1–F7 全部落实、定向验证对得上,**未发现新引入的 P1**,结论「可合入」。
  三条非阻塞观察当场修掉:`deploy-lib.sh` 注释与本节标题两处「实为 / 真因」→「推测」;`up` 失败用例断言锁死新文案;compose 返回 IPv6
  `host_ip` 时 URL 加方括号(加 lib 用例)。它在只读沙箱里实跑了新 helper 13 项定向检查、`mktemp` 故障注入、curl 桩三种退出路径、Bash 语法与
  文档守卫;依赖临时目录的 pytest 与 Docker 未能独立复现,全量绿以实现方记录为准(见 §6)。
