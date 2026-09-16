# 自动部署流水线方案(issue #102)

> 状态:方案稿 R5(2026-09-16,codex 首轮 15 条 + 三轮复检 13 条全部收口,另 3 条观察项一并并入,见 §7),待用户拍板后开工。
> 落地后本文档进 `docs/README.md` 索引;`docs/release-process.md` 的「边界与不做」同步删去「不做自动部署」一条。

## 1. 背景

2026-09-14「tag 即发布」落地后,发布链路前半段已自动:`scripts/release.sh` 打 tag → `release.yml`
两道核对 → 建 GitHub Release。后半段仍是人 SSH 到生产机 `nohup ./deploy-docker.sh vX.Y.Z &` 再轮询日志。
issue #102 记录了近一个月的痛点:生产长期落后 main、SSH 链路抖动、前置项靠人记、无部署记录面。

2026-09-16 生产实测(清理后):40G 盘用 65%,余 14G;`data/media` 4.9G 且近 30 天 +2.4G;后端镜像
2.3GB(torch CPU + Playwright Chromium);机器 1.6GB 内存 / 2 核;生产上已有人**手工**把上一版镜像打成
`dorami-backend-rollback:vX` 保留;部署备份每份约 360MB、库日增约 30MB;宿主没有 Python venv / uv。

## 2. 目标与非目标

**目标**:tag 推上去后,人只保留「点一下批准」,GitHub 自动登生产机跑部署,部署完核对版本与提交,
结果落在 GitHub Deployments 面板,失败以 workflow / Deployment 标红为准。

**非目标(首期刻意不做,已拍板)**:

- 不自动打 tag,不「部署即发布」;发布决定仍属于人。
- **不引入第二种 tag**(2026-09-16 拍板):tag 名不是权限,能打 `v*` 的人也能打别的名字;两种 tag 会造出
  「生产跑的是哪一版」的两个事实,且部署脚本「默认部署最高 `v*`」的语义也得跟着改。防误触的边界是
  「谁能批准」,不是「叫什么名字」。
- 不自动回滚:失败停下告警,人决定。**手动触发旧 tag 是「代码降级入口」,不是现行规则里的完整回滚**
  (完整回滚 = 旧 tag + 恢复对应 DB 备份,`docs/release-process.md` 回滚节不变)。
- **流水线只部署宣告了部署协议(§4.4)的 tag**:早于 PR-2 的 tag 没有协议,流水线拒绝并提示走手工路径。
  目标 tag 的脚本保护的是它自己那一版的构建与验证;跨版本的状态与门禁不随 tag 切换(§4.4 / §4.5 的职责划分)。
- 不承诺「两个 tag 连推只上最新」(latest-wins)的合并语义;不做候选合并层。等待中的每个 run 都可见,
  审批人对已被新版取代的自动 run 明确 Reject。
- 不把构建搬到 GitHub / GHCR(二期,另开 issue);不做 SQLite 快照上的迁移演练(二期可选,见 §4.6)。
- 不接 #82 的通知通道;不用 self-hosted runner(仓库 public);不覆盖裸机 PM2 路径;不改 PR / 合入 / 发版规则。

## 3. 方案总览

```
scripts/release.sh X.Y.Z(本机)── push tag vX.Y.Z
   ├─ sync-master.yml(不变)
   └─ release.yml
        ├─ job release:verify-release-ref(共用脚本)→ 建 Release;输出 tag + target_sha
        └─ job deploy(needs: release;caller 只有 needs/if/uses/with):uses ./.github/workflows/deploy.yml
             └─ deploy.yml 唯一 job:environment: production(required reviewers)
                  concurrency: {group: production, queue: max, cancel-in-progress: false};timeout-minutes: 40
                  ├─ 等批准
                  ├─ ssh <deploy-key> prod "vX.Y.Z <sha40> downgrade=0 redeploy=0"
                  │    └─ 生产机 forced command → /root/bin/dorami-deploy(仓库外 launcher / attacher)
                  │         解析 token → 起(或 attach)与 SSH 会话脱离的 worker(setsid;worker 才是锁与状态的 owner)
                  │              worker(仓库外,不随 tag 变;跨版本状态与门禁):
                  │                fetch(fail closed)→ 核 tag→sha / main 祖先 / 版本 / 协议版本
                  │                → in-progress 冲突判定 → 方向 + 单调护栏 → 首装门
                  │                → 开 in-progress 事务(prev 镜像 + worker 自己做的 DB 备份)
                  │                → 子进程 ./deploy-docker.sh vX.Y.Z(目标 tag 的脚本;它自己那一版的构建与验证)
                  │                     预检(磁盘 / compose config)→ build → check-config → 迁移计划 → up → 健康五项
                  │                → 成功晋升 last-success / 失败保留 in-progress → 清理 → 原子写 .rc 与 state
                  │         launcher 只 tail worker 日志直到 complete,以 .rc 作退出码;断线只死 launcher
                  └─ runner 公网核对 /api/health(version / ref / sha / source)→ Job Summary(always)
deploy.yml 另有 workflow_dispatch(tag, allow_downgrade, force_redeploy)= 手动部署 / 代码降级入口
```

方案候选取舍见 issue #102 的表格:首期 A(SSH 推送),二期 B(GHCR 拉取),C / D 不采。

## 4. 设计要点

### 4.1 触发点与工作流形状

- **共用核验脚本** `scripts/verify-release-ref.sh <tag>`:`git fetch origin main --tags` → 解析 `${TAG}^{commit}`
  → 必须是 `origin/main` 祖先 → `git show "${TAG}^{commit}:src/version.py"` 读**目标 tag 里的**版本号并与 tag 名
  比对(现有 `release.yml` 从工作区读版本,手动部署旧 tag 时会拿 main 的版本号比对而误拒)→ 输出 `target_sha`。
  push 路径与两个 dispatch 路径都用它。
- `release.yml`:`release` job 改用该脚本,输出 `tag` / `target_sha`;新增 `deploy` job,`needs: release`,
  `uses: ./.github/workflows/deploy.yml`,`with: {tag, target_sha}`;**不写** `secrets: inherit`(Environment secrets
  由被调用 job 自己的 `environment` 声明获得,`on.workflow_call` 不支持 `environment`)。
  `workflow_dispatch`(补建 Release)加布尔输入 `deploy`(默认 false)。
- `deploy.yml`:`on: workflow_call(inputs: tag, target_sha)` + `on: workflow_dispatch(inputs: tag, allow_downgrade=false,
  force_redeploy=false)`;dispatch 路径自己跑 `verify-release-ref.sh` 得 `target_sha`,并核对该 tag 已有**非 draft**
  GitHub Release(部署单元仍是经 release 流程核对的 tag)。`permissions: contents: read`。
  **两个布尔位随 SSH 命令以 token 形式传到生产机**(§4.3),workflow inputs 不会自动出现在远端环境里。
- 唯一的实际 job 上声明:`runs-on`、`environment: {name: production, url: https://www.dorami.cloud}`、
  `concurrency: {group: production, cancel-in-progress: false, queue: max}`、`timeout-minutes: 40`。
  `queue: max` 让所有等待项保留(默认只留一个 pending,后来者会顶掉先到的——这会把等待中的紧急手动降级顶掉);
  GitHub 不保证执行顺序,乱序造成的无意降级由 §4.4 的单调护栏兜住。
- **审批纪律**:多个 run 同时等批准时,审批人只批最新合格版本,对被取代的 run 明确 Reject(它们在 Deployments
  面板留下 rejected 记录,不是静默消失)。

### 4.2 人工闸门

- GitHub Environment `production`:required reviewers 只写发版人(public 仓库免费计划可用;审批等待不计
  Actions 分钟,30 天未批自动失败)。「Prevent self-review」由用户定,两人协作时默认不开。
- **Deployment branches and tags**:`Selected branches and tags` = tag `v*` + branch `main`。否则同仓其它 ref 上
  引用 `production` 的 job 也能申请审批;root 部署密钥不能只靠审批人每次辨认来源。
- Secrets 只有 `PROD_SSH_KEY`(部署专用 ed25519 私钥);`PROD_HOST` / `PROD_USER` / `PROD_KNOWN_HOSTS` 是
  Environment **variables**(非机密,减少误处理面)。fork PR 与其它 ref 拿不到。
- 可选加固(不在首期,记档):Ruleset 限制只有指定人能创建 `v*` tag。这会推翻 2026-09-14「任何人可打 tag」的
  决定;误建的 Release 本身无副作用,暂不做。

### 4.3 runner 侧

- 原生 `ssh`,不用第三方 action。私钥写 `${RUNNER_TEMP}/deploy_key`(0600,不 echo),`known_hosts` 从 variable 写入,
  `StrictHostKeyChecking=yes`;job 结束 `if: always()` 删除。
- 命令只有一条:`ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6 <user>@<host> "<tag> <sha40> downgrade=<0|1> redeploy=<0|1>"`。
  四个 token 全部严格格式;push 路径两个布尔位恒为 0,dispatch 路径取 inputs。远端 forced command 决定跑什么。
  `set -o pipefail` 后 `tee` 到临时日志,ssh 退出码即结果。
- **远端日志不能被 Actions 当 workflow command**:流式输出前 runner 先打印 `::stop-commands::<随机 token>`,SSH 结束后
  打印 `::<token>::` 恢复(用 `trap` / `if: always()` 保证恢复,否则后续步骤的注解全部失效);元数据从临时日志按 allowlist
  解析,不依赖 stdout 解释。远端元数据行前缀 `DORAMI_DEPLOY_META key=value`(值限 `[A-Za-z0-9._:/=-]`),进 Job Summary
  (版本 / sha / 用时 / 待执行迁移数 / 备份文件名 / in-progress / last-success);summary 步骤 `if: always()`。
- 部署完从公网再核一次:`GET https://www.dorami.cloud/api/health` 满足 `status == ok`、`version == ${tag#v}`、
  `build.ref == tag`、`build.sha == target_sha`、`build.source == env`;180 秒预算内重试,超时才失败。响应带
  `Cache-Control: no-store`;两层 nginx 当前都没有 API 代理缓存,SHA 核对用于覆盖错容器 / 旧连接。
- **重跑语义**(由 §4.4 状态机决定,判定顺序固定):worker 仍在跑同 target → attach;存在 in-progress 且 target 不同 →
  拒绝;存在同 target 的 in-progress → 复用该事务重新部署;无 in-progress 且 last-success 等于本次 → 回放成功;
  `redeploy=1` 才对已成功的同 target 重新完整部署。

### 4.4 生产机 launcher 与 worker(仓库外)

**职责划分(R4 定稿)**:worker 在仓库外、不随 tag 切换,承担一切**跨版本**的状态与门禁——token / ref / 协议核验、锁、
in-progress / last-success 事务(含 prev 镜像采样与 worker 自己做的 DB 备份)、方向判定与单调护栏、首装门、清理与晋升、
退出码。目标 tag 里的 `deploy-docker.sh` 只承担**它自己那一版**的构建与验证(磁盘 / compose 预检、build、check-config、
迁移计划、up、健康五项)。这样 dispatch 旧 tag 时执行的是旧 tag 的构建脚本,但事务、护栏与首装门不会随之降级;
再往前的 tag(没有协议宣告)流水线直接拒绝。

- 文件 `/root/bin/dorami-deploy`(launcher / attacher,forced command 入口)与 `/root/bin/dorami-deploy-worker`
  (与 SSH 会话脱离的 owner),模板入库为 `docker/dorami-deploy.example` 与 `docker/dorami-deploy-worker.example`
  (与 `edge-nginx.conf.example` 同做法;两文件头部写 `DORAMI_DEPLOY_WORKER_VERSION`,升级 worker 是显式手工步骤);
  配置 `/etc/dorami-deploy.conf`(root-only 0600,`REPO_DIR` / `DORAMI_DEPLOY_MIN_FREE_GB` / `DORAMI_DEPLOY_BACKUP_KEEP` 等,
  由 worker `source` 后显式导出。**注意** compose 会读项目 `.env` 做插值,但部署 shell 不会把 `.env` 导出为自己的环境,
  部署参数不写进 `.env`)。
- **必须放仓库外**:`deploy-lib.sh` 会 `git checkout <tag>` 换掉仓库内文件并 `exec` 重执行,forced command 指向的入口
  本身不能是会被换掉的文件(09-14「生产上旧脚本不认 tag 参数」即此坑);跨版本状态同理不能由会被换掉的脚本维护。
- `authorized_keys` 一行:`restrict,command="/root/bin/dorami-deploy" ssh-ed25519 …`(`restrict` 一并关掉 pty / 转发 /
  user-rc 及未来新增项;按目标 Ubuntu 的 OpenSSH 版本验证)。`from=` 不设:GitHub 托管 runner 的 IP 段数千条且每周变。
- 环境卫生:固定 `PATH` / `umask 077`、绝对 `cd "$REPO_DIR"`、清空 `GIT_*` / `DOCKER_*` / `COMPOSE_*`;两个脚本、
  状态目录 `/var/lib/dorami-deploy/`、仓库目录均 root 属主且不可被组 / 其它用户写。
- **token 解析(launcher)**:`$SSH_ORIGINAL_COMMAND` 必须恰为
  `^v[0-9]+\.[0-9]+\.[0-9]+ [0-9a-f]{40} downgrade=[01] redeploy=[01]$`,无多余空白;其余拒绝。参数数组调用子进程,无 `eval` / `sh -c`。
- **launcher 职责只有三件**:解析 token → 若无存活 worker 则 `setsid` 起 worker(把四个 token 作参数传入,worker 立即脱离
  会话、stdin/stdout 指向日志文件)→ tail worker 日志直到 state 为 `complete`,以 `.rc` 内容退出。SSH 断线只杀 launcher;
  worker、锁、状态、`.rc` 都不受影响;重跑的 launcher 依据 worker 的 `pid + start_id` attach。
- **worker = 唯一 owner**:打开固定 FD 9 对 `/run/lock/dorami-deploy.lock` `flock -n`;状态文件
  `/var/lib/dorami-deploy/state.json`(临时文件 + fsync + `mv` 原子写)记
  `{tag, target_sha, downgrade, redeploy, worker_pid, worker_start_id, child_pid, child_start_id, phase, log, started_at}`,
  `phase ∈ starting | verifying | running | finalizing | complete`;`start_id` 取 `/proc/<pid>/stat` 的 starttime 防 PID 复用。
  worker 流程按序:
  1. **ref 核验(fail closed)**:`git fetch --tags origin` 失败即退出(现有 deploy-lib 拉取失败会警告后用本地 tag,流水线来源
     不允许);`git rev-parse "refs/tags/<tag>^{commit}" == sha` ∧ sha 在新鲜 `origin/main` 上 ∧ `git show sha:src/version.py`
     与 tag 一致。这样部署私钥泄露 / 误用 / 本地 tag 陈旧或被移动都不会部署错提交。
  2. **协议核验**:`git show sha:scripts/deploy-lib.sh` 必须宣告 `DORAMI_DEPLOY_PROTOCOL=<n>` 且 `n` 在 worker 支持范围内;
     没有宣告(早于 PR-2 的 tag)或高于 worker 支持(worker 需先手工升级)→ fail closed,提示走手工路径或升级 worker。
  3. **in-progress 冲突判定(先于一切回放)**:存在 `in-progress.json` 且 target ≠ 本次——若其 `switched_at` 为空且切换标记文件
     不存在(上次在 `up` 之前就失败,系统未被改动)→ 自动关闭该事务(manifest 归档为 `closed-<txn_id>.json`,managed tag 与备份留给
     正常清理)并继续;否则 fail closed(「上次部署已切换未收口,先人工关闭事务」);target = 本次 → 标记「复用事务」;不存在 → 继续。
  4. **回放判定**:无 in-progress ∧ `last-success.json` 的 target 等于本次 ∧ `redeploy=0` → 回放成功(退出 0,打印 manifest)。
  5. **基线与方向**:`baseline` 优先读 last-success,次读运行容器的 `DORAMI_BUILD_SHA/REF`,都缺为 `unknown`;
     `baseline` 是 `target` 祖先 → 向前;`target` 是 `baseline` 祖先 → 降级;互不为祖先或 unknown → 未知。
  6. **单调护栏**:向前放行;降级或未知 → fail closed,除非 `downgrade=1`(显式人为风险确认;即使迁移计划 pending=0 也不把
     任意降级视为安全)。公网 health 不作基线(站点故障时不可达)。
  7. **迁移文件变化提示**:`git diff --name-status <baseline_sha> <target_sha> -- alembic/versions`,**按方向解释**:向前时 M / D =
     迁移历史被改写 / 删除,fail closed;降级(已确认)时 D 是「目标尚无后续 revision」的预期结果,兼容性交给目标镜像的迁移计划;
     两边共有的 revision 文件出现 M 一律 fail closed;方向未知时任何 M / D fail closed;基线 unknown 输出 `unknown` + 强警告。
  8. **首装门**:「部署证据」= 任一存在——`docker compose ps -a -q`(含 stopped / exited)非空、`backups/` 非空、任何
     `dorami-*-managed:*` / `dorami-*-rollback:*` 镜像 tag、last-success / in-progress、`data/cms_data.db` 文件存在。
     **复用事务时跳过证据判定**,以事务内的 `fresh_authorized` 为准(首装失败后的同 target 重试不能被自己的 in-progress 推翻)。
     无任何证据 ∧ 一次性令牌文件 `/var/lib/dorami-deploy/first-install.token` 存在 → 允许 `fresh`:把 `fresh_authorized=true` 与
     令牌摘要写进本次 in-progress,事务原子落盘后**立即消费**(删除)令牌;崩溃在「事务已落盘、令牌未删」之间时,下次启动按摘要幂等
     消费,同一令牌不会开第二个事务;不同 target / 无匹配事务 / 事务已关闭均不继承授权。
     有证据 → 目标镜像的迁移计划若返回 `fresh` 必须 fail closed(库路径 / 卷 / 文件丢失,绝不起空站)。令牌不是 conf 里的持久布尔。
  9. **开 in-progress 事务(切换前的持久回滚点)**:复用事务时**不重新采样**(此时 DB 可能已迁移、容器可能已是失败的新版本);
     否则从 `docker compose ps -q backend|nginx` inspect 出当前 image ID,打本次唯一 managed tag
     `dorami-backend-managed:<from_ref>-<epoch>` / `dorami-nginx-managed:<…>`;worker **自己**做 SQLite 在线备份
     (`sqlite3 .backup`,与 deploy-lib 同算法,文件名带 target)——事务的备份不依赖目标脚本是否打印文件名;确认两镜像可 inspect、
     备份存在,生成 `txn_id`,以临时文件 + fsync + `mv` + fsync 状态目录原子提交 `/var/lib/dorami-deploy/in-progress.json`
     (`switched_at` 此时为空)。首装无容器则 prev 为空并记录。
     `dorami-*-rollback:auto` 只是便捷 alias,恢复以 manifest 中的不可变 image ID / managed tag 为准;**绝不枚举删除**手工打的
     `rollback:vX`。人工放弃一次失败部署或恢复备份后,须显式关闭事务(`dorami-deploy-worker --close-in-progress`)。
  10. **起子进程**:`./deploy-docker.sh <tag>`——**位置参数**,现有脚本认识,站在旧 tag 上的仓库也能自举到新 tag;经环境传入并随
      checkout + exec 重执行继承:`DORAMI_DEPLOY_ORIGIN=pipeline`(**新变量**,不复用 `DORAMI_DEPLOY_MODE`——它在现有 deploy-lib
      里是 `tag | here` 且解析时会被覆盖)、`DORAMI_EXPECTED_SHA=<sha>`、`DORAMI_DEPLOY_LOCK_FD=9`、
      `DORAMI_DEPLOY_SWITCH_MARK=/var/lib/dorami-deploy/<txn_id>.switch`(目标脚本在 `up` 前 touch 它)、首装放行时
      `DORAMI_DEPLOY_FRESH_OK=1`。写 `running`;`wait` 子进程;子进程退出后若切换标记存在,把 `switched_at` 写入 in-progress。
  11. **收口**:子进程退出码 0 → 用 in-progress 的内容生成 `last-success.json.tmp`(同 schema,补 `deployed_at`),fsync,`mv`
      为 `last-success.json`,再删 in-progress,把 `:auto` alias 指向 prev 的两个 image ID;非 0 → in-progress **保留**
      (暂存镜像与备份不动,供重试复用或人工恢复)。崩溃窗口「last-success 已提交、in-progress 未删」在下次启动时可判定:
      `in-progress.txn_id == last-success.txn_id` → 视为已晋升,幂等删除(按 `txn_id` 判定,不依赖挂钟)。
  12. **清理(仅成功)**:删除**不被** last-success 引用的更旧 managed tag;备份按 `DORAMI_DEPLOY_BACKUP_KEEP`(默认 10)计数清理,
      被 in-progress / last-success 引用的备份**永不参与**计数;`docker image prune -f` 清悬空;**不**跑 `builder prune`。
  13. **退出码**:进入 `finalizing`,捕获退出码写临时文件再 `mv` 成 `/var/lib/dorami-deploy/<tag>-<sha:7>.rc`(与目标 tag 的脚本
      是否认识 `.rc` 无关),写 `complete`,释放锁。
- **manifest schema(两文件同构)**:`{txn_id, target: {tag, sha}, prev: {ref, sha, backend_image_id, nginx_image_id, managed_tags,
  db_backup}, opened_at, switched_at?, fresh_authorized?, token_digest?, deployed_at?}`;last-success 比 in-progress 多 `deployed_at`。
  所有读取方按 `target.tag/sha` 取值,崩溃收口按 `txn_id`;所有原子写 = tmp + fsync 文件 + rename + fsync 状态目录(断电持久)。
- **attach(launcher 侧)**:锁被占且 state 的 `tag/sha` 相同 ∧ worker 存活(pid + start_id)→ tail 直到 `complete`;
  `phase == finalizing` 时 child 已死不算失败,等 `.rc`。`tag/sha` 不同 → 拒绝并报出正在跑的 target(Actions 队列自然等待)。
  worker 不存活但锁被占 / state 陈旧 → fail closed 报「状态不一致,人工检查」(worker 与 SSH 脱离后,这只剩机器崩溃一类场景)。
- 手工兜底 `./deploy-docker.sh` 也走同一把锁:仓库内脚本在**没有** `DORAMI_DEPLOY_LOCK_FD` 时自行打开同一锁文件
  `flock -n`(拿不到即报正在跑的部署),有则只校验该 FD 持锁、不二次抢。手工来源保留离线语义(fetch 失败可用本地 tag),
  由 `DORAMI_DEPLOY_ORIGIN` 缺席 = `manual` 决定;流水线来源必须显式 `pipeline`。手工路径不开 in-progress 事务,
  但会让 last-success 失效(worker 下次以容器读基线并警告「上次为手工部署」)。

### 4.5 部署脚本改动(`deploy-docker.sh` / `scripts/deploy-lib.sh`,随 tag)

`deploy-lib.sh` 宣告 `DORAMI_DEPLOY_PROTOCOL=1`(PR-2 起;协议 = worker 与脚本之间的环境变量、元数据行与步骤契约,改契约即 bump)。
`DORAMI_DEPLOY_ORIGIN=pipeline` 时在 checkout + exec 重执行后再次要求 `HEAD == DORAMI_EXPECTED_SHA`(最终执行的目标与 Actions
核验值闭环)。以下步骤按序,任一不过即退出、退出码非零、**不自动回滚**:

1. **预检(构建之前,fail fast)**
   - 磁盘:对 `docker info --format '{{.DockerRootDir}}'`、仓库、`data/`、`backups/` 所在的每个文件系统检查可用字节与
     inode;阈值 `DORAMI_DEPLOY_MIN_FREE_GB` 默认 5(按「当前镜像 + 暂存镜像 + 新镜像层 + 一份备份」给的下限);
     打印 swap 状态(1.6GB 机构建依赖 swap)。
   - `docker compose config -q`:能拦下 `.env` 里 `:?` 必填变量缺失;**固定带 `-q`**,只记退出码与脱敏错误摘要,
     禁止 `set -x`、禁止裸 `config` / `--environment`(compose 含大量密钥环境变量,会进 Actions 日志)。
   - `config/production.ini` 存在(已有)、工作树对入库文件无手改(已有)、tag = `v` + 目标文件版本(已有)。
2. **build**:`docker compose build`。
3. **配置与当前状态预检**:`docker compose run --rm --no-deps backend python docker/entrypoint.py --check-config`
   (目标镜像;见 §4.6)。覆盖「新增 ini 节 / 安全检查 / taxonomy 姿态与目录」类失败,避免新容器起不来的停机窗;
   **不承诺**覆盖迁移之后才暴露的 reconcile 失败(那需要快照演练,二期可选)。
4. **迁移计划**:`docker compose run --rm --no-deps backend python docker/entrypoint.py --plan-migrations`(目标镜像,只读,见 §4.6)。
   `compatible(pending=[…])` 继续并把 pending 数写元数据;`incompatible` → fail closed,打印「先按
   release-process 恢复对应备份再重跑」;`legacy_adoption_required` → 提示并继续(entrypoint 的 `ensure_migrated` 会收养);
   `fresh` → 只有 worker 经首装门放行(`DORAMI_DEPLOY_FRESH_OK=1` 随环境传入)时继续,否则 fail closed。
5. **备份 DB**:**仅手工来源执行**(已有逻辑,按 mtime 留 10 份)。`DORAMI_DEPLOY_ORIGIN=pipeline` 时**跳过**——worker 已做权威的
   事务备份;现有 `backup_sqlite_db()` 的计数清理不认识 manifest,若在流水线下继续跑,同 target 多次重试后会把仍被 in-progress 引用的
   原始备份删掉,事务名存实亡。
6. **up**:先 `touch "$DORAMI_DEPLOY_SWITCH_MARK"`(向 worker 表明「系统即将被改动」),再 `docker compose up -d --remove-orphans`。
7. **健康核对**:本机 `GET /api/health`(经容器 nginx),180 秒预算内重试,核 `status / version / build.ref / build.sha /
   build.source` 五项;失败打印 `docker compose ps` 与后端 50 行日志,退出非零。

元数据行 `DORAMI_DEPLOY_META …` 在各步产出;失败也尽量产出已知项。

### 4.6 后端(PR-1)

- **`GET /api/health`** → `{"status": "ok", "version": __version__, "build": build_info()}`,`Cache-Control: no-store`。
  加入 `is_public_auth_path` 的 **exact-path** 白名单(与 `/api/auth/session` 同级,在鉴权与 `disabled_runtime_surface`
  之前短路;不用前缀)。版本号对匿名可见可接受:它已出现在 Release 页与前端构建产物里,响应不含配置 / 能力位 / 账号。
  测试:匿名 200;runtime role collector / reader / all 三态均 200;无效 cookie 仍 200;响应只含三个字段;相邻伪路径
  (`/api/healthz`、`/api/health/x`)仍 401。compose 的 healthcheck 与部署探针改指向它,`/api/auth/session` 白名单不动。
- **`docker/entrypoint.py --check-config`**:只 import 无副作用模块(`config`、`api/security_checks`、taxonomy 校验),
  **不 import `api.app`**(装配阶段会建 storage、种账号);跑 settings 加载 → 安全检查(按 posture)→ taxonomy 姿态 /
  目录自校验(`taxonomy_deployment.validate_catalog` 等);若 DB 已在目标 revision 集合内,再跑从 `reconcile_catalog_session`
  校验段(receipt / 既有 version / 未决 Candidate / tag-alias 冲突)抽出的 **read-only validator**(query-only 连接,不跑
  `ensure_migrated`,不写)。退出码 0 / 非 0。正式 entrypoint 的 migration + reconcile 仍是最终权威。
- **`docker/entrypoint.py --plan-migrations`** → `storage.migrations.plan_migrations(db_url)`:**不 shell `alembic current`**
  (在线命令会加载 `alembic/env.py`,其 online 路径在 `begin_transaction` 内 drop / reinstall Archive Sync 触发器且
  `BEGIN IMMEDIATE`,不是只读)。实现:query-only 连接上 `MigrationContext.configure(conn).get_current_heads()`(复数)
  + `ScriptDirectory.from_config(cfg)` 的 revision graph:
  - 任一 DB head 不在目标脚本图 → `incompatible`(典型是 DB 领先于目标代码——旧 tag 的脚本目录没有新 revision
    文件;也可能是目标缺支线或文件损坏,不武断断言具体原因);
  - heads 全部已知 → `compatible`,`pending = 目标闭包 − 已应用闭包`(拓扑序;多头 / merge revision 自然成立,执行时与
    `ensure_migrated` 一样 upgrade `heads`)。**没有**单独的「已知 head 但不在目标闭包」状态:目标图里每个 revision
    必是某个 head 的祖先(叶子本身就是 head),PR-1 实现时推演证实该状态不可达,故不设;
  - 有业务表无 `alembic_version` → `legacy_adoption_required`;无业务表 → `fresh`(是否放行由 worker 的首装门决定);
  输出 JSON,供部署脚本与 Job Summary 消费。
- 测试:`tests/test_health_endpoint.py`、`tests/test_entrypoint_check_config.py`(校验段只读、不 import app)、
  `tests/test_migration_plan.py`(四种状态各一例,含多头与 legacy)。

### 4.7 安全边界

- 部署专用 ed25519 密钥,与日常运维密钥分开;`restrict,command=`;`PermitRootLogin prohibit-password`(生产已是)。
- runner 上没有生产机密:`.env` 与 `production.ini` 留在宿主;唯一 secret 是私钥。
- 首期仍是 root 登录(`data/`、`backups/` 属主为 root;改专用用户要处理属主与 docker 组),记二期。
- 审计 = Actions run 日志 + Deployments 记录 + 生产机 `/var/lib/dorami-deploy/`(state / in-progress / last-success / `.rc`)
  + `/var/log/dorami-deploy/<tag>-<sha:7>.log`。

### 4.8 文档

- `docs/release-process.md`:「边界与不做」删自动部署一条,加「流水线只部署宣告协议的 tag」;「部署」节加「流水线部署(默认)」
  「手动兜底」「审批纪律」;「回滚」节**保留**「有迁移要恢复 DB」的定义,加「代码降级入口 = 在 GitHub 上 dispatch 旧 tag +
  `allow_downgrade`,迁移计划不兼容时先恢复备份,再关闭 in-progress 事务后重跑」;分支保护清单加 Environment / variables / secret
  的一次性配置。
- `docs/deploy-docker.md`:launcher / worker 安装与升级、`/etc/dorami-deploy.conf`、部署密钥与 `authorized_keys` 行、状态目录、
  in-progress / last-success 语义与人工关闭、首装令牌。
- `CLAUDE.md`:*Production deploy* 一行;*Versioning* 里「tag 推上去后 release.yml + sync-master.yml」的链路描述加部署一步。
- `scripts/release.sh` 头注释与成功提示(现仍说「生产机随后手工部署」)。
- 本文档进 `docs/README.md`。

## 5. 落地切分

1. **PR-1 后端**:`/api/health`;`entrypoint.py --check-config`(含从 reconciler 抽出的只读 validator);
   `storage.migrations.plan_migrations` + `--plan-migrations`;三组测试。
2. **PR-2 脚本**:`docker/dorami-deploy.example`(launcher)+ `docker/dorami-deploy-worker.example`(worker,含 `--close-in-progress`)+
   `/etc/dorami-deploy.conf` 样例;`deploy-lib.sh` 宣告协议 1、`DORAMI_DEPLOY_ORIGIN` 来源、锁协议、预检、check-config / plan 调用、
   健康五项核对、元数据行;`scripts/verify-release-ref.sh`;**进程级测试**(fake `git` / `docker` / `sqlite3` / `curl` 放临时 PATH):
   launcher 被 kill 后 worker 继续并原子产出 `.rc`、同 target 二次调用 attach 不起第二个 worker、旧 deploy-lib(只认位置 tag 参数、
   会覆盖 `DORAMI_DEPLOY_MODE`)exec 到新脚本后 `ORIGIN` / expected sha / 锁 FD 仍在、无协议宣告的旧 tag 被拒、新 worker 对协议 1 的
   旧目标脚本仍能捕获退出码并维护事务、stale pid / start_id / 旧 `.rc` / 不同 target 抢锁各 fail closed、判定顺序(in-progress 冲突
   先于回放)、同 target 重试复用 in-progress 不重采样、崩溃窗口幂等收口、token 解析、fetch 失败 fail closed、方向判定三态 × M/D 组合、
   首装门(stopped 容器 / 备份 / managed tag / 库文件任一存在即拒,令牌一次性消费)、流水线来源下目标脚本不自做备份且同 target
   连续失败超过 keep 次后 in-progress 引用的原始备份仍在、首装失败后同 target 重试沿用事务内 `fresh_authorized`、未切换的 in-progress
   被不同 target 自动关闭而已切换的被拒、崩溃收口按 `txn_id`。
3. **PR-3 流水线与文档**:`deploy.yml`、`release.yml` 改造、`actionlint` 进 CI、`stop-commands` 包裹、§4.8 文档。
4. **生产机一次性手工**(用户执行,PR-3 文档即手册):生成部署密钥、装 launcher / worker 与 conf、写 `authorized_keys`;
   GitHub 建 Environment `production` + reviewers + branches/tags 策略 + 1 secret + 3 variables。
5. **首次上线前隔离演练**(不进 CI,按清单执行一次):真实 SSH 断线(kill launcher 后重跑 attach)、三个快速 tag 的队列与审批、
   从旧仓库脚本自举到新 tag、dispatch 一个协议 1 的旧 tag 走 `allow_downgrade`(迁移兼容 / 不兼容 + 恢复备份后关闭事务各一次)、
   dispatch 一个无协议的旧 tag 被拒、`up` 后健康失败再重试复用回滚点、缺一个 `:?` 变量、非 `main` / 非 `v*` ref 申请 production 被拒。
   记录当前 tag / sha / image ID / 备份名 / 公网 health。
6. **首次真实发版**:失败先看 worker 状态与锁确认远端任务已结束,再按 in-progress manifest 恢复;不直接再起手工脚本抢锁。
7. **二期(另开 issue)**:CI 构建镜像推 GHCR、compose 加 `image:`、registry cache;专用部署用户;SQLite 快照上的迁移 +
   reconcile 演练;候选合并层(若确有需要);worker 自动升级机制(若手工升级成为负担)。

## 6. 验收

- [ ] main 上 `scripts/release.sh X.Y.Z` 推 tag 后,不登生产机,经一次批准该版本部署到生产。
- [ ] 部署后本机与公网 `/api/health` 均满足 `version = X.Y.Z`、`build.ref = vX.Y.Z`、`build.sha = target_sha`、
      `build.source = env`;登录后 `/api/runtime` 与 设置 → 关于 显示「发布版」(人工目检项)。
- [ ] tag / sha / main 祖先 / 目标版本 / 协议版本任一不一致在构建前失败;流水线来源下生产机 fetch 失败 fail closed;dispatch 路径同样被拦;
      无协议宣告的旧 tag 被拒并提示手工路径。
- [ ] `actionlint` 通过;environment 只声明在被调用 job;批准前拿不到 Environment secret;非 `main` / 非 `v*` ref 与 fork PR
      进不了 `production`;远端日志里人为放入的 `::error::fake` 不产生注解(stop-commands 生效且事后恢复)。
- [ ] `.env` 缺一个 `:?` 项时在构建前失败,且日志中不存在 `.env` 里的哨兵 secret;目标版本 `--check-config` 失败发生在 `up` 之前。
- [ ] 迁移计划:基线 unknown 输出 `unknown` 而不是 0;`incompatible`(DB 领先于旧 tag)演练一次并 fail closed;
      向前部署时 M / D fail closed;已确认降级时反向 diff 的 D 放行、由计划判定;有部署证据(含 stopped 容器 / 备份 / managed tag /
      库文件)却 `fresh` 时 fail closed,首次安装持一次性令牌时放行且令牌被消费。
- [ ] `allow_downgrade` / `force_redeploy` 经 SSH token 到达生产机并生效:旧 tag 不带前者被单调护栏拦下、带则放行;
      已成功同 target 不带后者回放成功、带则重新部署。
- [ ] 判定顺序:存在不同 target 的 in-progress 时,即使 last-success 等于本次也不回放、直接拒绝。
- [ ] 同 target:worker 运行中重跑 attach、launcher 被 kill 后 worker 完成并产出 `.rc`、完成后重跑回放、`up` 后失败重试复用
      in-progress 的 prev 与备份不重采样;不同 target:生产锁拒绝且 Actions 队列保留等待项(`queue: max`);未切换(`up` 前失败)的
      in-progress 被不同 target 自动关闭、已切换的拦下不同 target;手工入口与流水线互斥;「last-success 已提交、in-progress 未删」的
      崩溃窗口下次启动按 `txn_id` 幂等收口;首装失败后同 target 重试仍放行且令牌只消费一次。
- [ ] 流水线来源下目标脚本不再自做备份;同 target 反复失败超过保留数后,in-progress 引用的原始备份仍在。
- [ ] 绝不并发、绝不无意降级(人为制造 job 乱序,旧版本被单调护栏拦下);被取代的 run 以 rejected / cancelled 可见。
- [ ] 健康核对 180 秒预算边界与超时诊断输出;失败时 job 标红、Deployment 标红,in-progress 保留、last-success 不变。
- [ ] managed 两镜像 + manifest + worker 备份配对;现存手工 `rollback:vX` 不变;prune 不删 manifest 引用;被引用备份不被计数清理。
- [ ] 站在旧 tag 上的仓库用旧 deploy-lib 自举到新 tag 成功;dispatch 协议 1 的旧 tag 时事务、护栏、首装门仍由 worker 执行,退出码准确。
- [ ] Deployments 面板可见版本、时间、触发人、结果;首次上线清单做一次审批人 / 发版人的 GitHub 通知设置与邮件实收测试
      (邮件到达不作机器验收)。
- [ ] 文档四处已更新(§4.8)。

## 7. 检视记录

**R1(2026-09-16,codex gpt-5.6-sol,设计稿检视)**:首轮 5 P1 / 8 P2 / 2 P3 全部有据,全部接受;四个分歧点一轮答复即收敛,
产出文件 `.review/{prompt,report-codex,response,reply-codex}-r1.md`。改变方案形状的结论:

- **锁与退出码单一所有者**(P1-01):原稿让 supervisor 与部署脚本各自抢同一把锁会自锁;改为唯一 owner 持锁、FD 继承、
  双 pid + phase 状态机、`.rc` 由 owner 原子产出、同 target 已成功则回放。
- **核对要读目标 tag 的文件**(P1-02):现有 `release.yml` 从工作区读版本号,手动部署旧 tag 必被误拒;抽共用 `verify-release-ref.sh`。
- **并发**(P1-03,分歧 D1):我方原倾向默认「替换 pending + 单调护栏」以得到 latest-wins;codex 指出三类兜不住的场景——
  running 中等审批的旧 tag 不会被替换、最新 tag 自身失败后两版都没上、**等待中的紧急手动降级会被后到的自动发布顶掉**。
  定稿 `queue: max` + 审批纪律 + 单调护栏(基线读 manifest / 容器,不读公网 health),不承诺 latest-wins。
- **forced command 绑定 SHA**(P1-04):SSH 传 `tag sha`,生产机 fetch fail closed 并核 tag→sha / 祖先 / 版本;exec 重执行后
  再核 `HEAD == expected`;来源显式传递,不由环境变量缺席隐式决定。
- **手动旧 tag ≠ 完整回滚**(P1-05):定义为代码降级入口;迁移计划不兼容 fail closed;`allow_downgrade` 是显式人为确认。
- **配置自检**(分歧 D2):`--check-config` 只 import 无副作用模块、只读 DB、只在 DB 已在目标 revision 集合时跑 taxonomy
  状态校验;称为「配置与当前状态预检」,不承诺覆盖迁移后的 reconcile 失败(快照演练记二期)。
- **迁移判定**(分歧 D3):不 shell `alembic current`(`env.py` 在线路径动触发器);用 `MigrationContext.get_current_heads()`
  + 脚本图做 DAG 闭包比较(PR-1 落地时收为四种状态,见 §4.6);pending=0 不等于降级安全。
- **备份与测试分界**(分歧 D4):备份保留数不改、可配、manifest 引用者 pin 住;锁 / attach / `.rc` / 自举场景做进程级 CI 测试,
  真实断网与完整新旧 tag 部署留上线前演练。
- 其余:environment 声明在被调用 job(`on.workflow_call` 不支持 `environment`,Environment secrets 不经 `secrets: inherit`);
  Deployment branches / tags 策略;`docker compose config -q` 固定且禁打印;健康核对五项与 `no-store`;managed tag 唯一命名 +
  manifest 提交点,不碰手工 `rollback:vX`;SSH `restrict`;元数据前缀不用 `::`;邮件不作机器验收。

**R2 复检(同日,对照清单)**:15 条中 8 ✓ 7 △,△ 全部源于 R2 文本自己新引入的 6 P1 + 1 P2,已在 R3 收口:
dispatch 的两个布尔位未跨 SSH 传到生产机 → 四 token;新 `--pipeline` 参数会被旧 deploy-lib 拒绝且 `DORAMI_DEPLOY_MODE` 会被覆盖 →
位置 tag 参数 + 新变量 `DORAMI_DEPLOY_ORIGIN`;forced command 进程当 owner 会随 SSH 断线死亡 → 拆 launcher / `setsid` worker;
迁移文件 D 一律 fail closed 会永久拦下恢复备份后的降级 → 先判方向;`up` 后失败重试重采样 prev → in-progress 事务;
库文件丢失被当 `fresh` 起空站 → 首装门;远端日志 `::error::` 被 Actions 解释 → `stop-commands` 包裹。

**R3 复检(同日,对照 7 条)**:5 ✓ 2 △,另 3 P1 + 1 P2,已在 R4 收口:

- **切到旧 tag 后执行的是旧 tag 的脚本,R3 的安全协议随之消失**(R3-P1-01,结构性):`deploy-lib.sh:117–121` checkout 后 exec
  目标 tag 的脚本,dispatch 旧 tag 时事务 / 护栏 / 首装门都不会执行。定稿**职责划分**:跨版本状态与门禁全部归仓库外 worker
  (锁、事务含 prev 采样与 worker 自做备份、方向与单调护栏、首装门、清理晋升、退出码),目标 tag 脚本只做它自己那一版的构建与
  验证;`deploy-lib.sh` 宣告 `DORAMI_DEPLOY_PROTOCOL`,无宣告或高于 worker 支持的 tag 流水线拒绝。
- **in-progress `mv` 成不同 schema 的 last-success**(R3-P1-02):两 manifest 同构(`target.tag/sha`),晋升 = 生成 tmp + fsync +
  rename + 删 in-progress,崩溃窗口幂等收口。
- **`fresh` 放行把「无运行容器」当「无既有部署」**(R3-P1-03):部署证据扩为 `ps -a`(含 stopped)/ 备份 / managed 或 rollback tag /
  manifest / 库文件任一;首装确认改为一次性令牌文件,成功后原子消费。
- **回放判定排在 in-progress 跨 target 冲突之前**(R3-P2-01):判定顺序固定为 in-progress 冲突 → 复用 → 回放。

**R4 复检(同日,对照 4 条,最后一轮)**:3 ✓ 1 △,另 2 P1 + 3 观察项,已在 R5 收口:

- **目标脚本的旧备份清理会删掉 worker 事务备份**(R4-P1-01):现有 `backup_sqlite_db()` 按 mtime 留 10 份、不认识 manifest;
  流水线来源下目标脚本**跳过**自做备份,只在手工来源执行。
- **首装失败后自己的 in-progress 会被当成部署证据**(R4-P1-02):`fresh_authorized` 与令牌摘要写进事务,令牌在事务落盘后立即消费,
  同 target 重试复用授权。
- 观察项三条一并并入:worker 在目标脚本 fail-fast 之前就开事务会留下阻塞其它 target 的 in-progress → 引入切换标记,未切换的事务由
  不同 target 自动关闭;崩溃收口依赖挂钟 → 改 `txn_id`;原子写未提目录 fsync → 补上。

检视到此收束(首轮 + 三轮复检)。后续实现阶段的检视按 PR 逐个进行,不再重开设计面。

已核实并保留的正确决策:`GITHUB_TOKEN` 建 Release 不触发 `on: release`,同 run `needs: release` 串联正确;Environment
required reviewers 在 public 仓库可用;launcher / worker 必须在仓库外;`/api/health` exact 白名单会在鉴权与 surface 判断前短路;
`build.source` 真实值是 `env`(issue 原稿写 `tag`,以 `env` 验收);已拍板边界(不自动回滚 / 不接通知 / 单一 tag /
`--here` 不进流水线 / A 首期 B 二期)全部保留。
