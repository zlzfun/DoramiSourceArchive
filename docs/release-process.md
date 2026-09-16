# 发布流程:tag 即发布

> 状态:活跃(2026-09-14 拍板落地)。适用于两条官方部署路径(Docker / 裸机)与内网 master 同步。

## 为什么

个人开发时「合入即上线」没有问题;两人并行后,「等他那个特性一起发」让 main 上已合入未发布的
提交越攒越多,生产跑的是哪一版只能靠记忆(两条部署脚本此前都部署「当前工作树」)。
自此把**合入**与**发布**拆成两件事:

- **合入 main** 的门禁是 PR + CI(`.github/workflows/ci.yml`:pytest + 前端 lint/build)+ 交叉检视;
  合入不等于上线,半成品可以合入,只要入口藏在开关后面。
- **发布**是给 main 上某个提交起一个不再移动的名字——annotated tag `vX.Y.Z`。
  没打 tag 的合入就是「已合入未发布」,`git describe` 的 `-N-g…` 后缀就是它的度量。
- **部署脚本只认 tag**:生产上跑的永远是一个有名字的版本;想部署别的必须显式 `--here`。

节奏:到点(或攒够一个用户可见特性)就发,main 上有什么发什么,没赶上的等下一班。

## tag 是什么(速记)

- 分支是会动的指针,tag 是打上就不动的指针。发布一律用 annotated tag(带打标人/日期/说明的 git 对象)。
- tag 不随 `git push` 自动上传,要单独 `git push origin vX.Y.Z`;`--tags` 会推所有本地 tag,慎用。
- **打错不挪**:`-f` 覆盖后别人 fetch 过的旧 tag 不会更新,同名两内容。打错就发下一个 PATCH。
- `git checkout vX.Y.Z` 是 detached HEAD,部署机不需要分支;回滚 = checkout 上一个 tag。
- 两个 tag 之间的提交就是一版的变更集;GitHub Release 是 tag 之上的一层(说明/附件/通知),
  tag 是 git 概念,Release 是 GitHub 概念。
- 名字带 `v` 前缀,`git tag --sort=-v:refname` 按版本号排序(字母序会把 3.9 排到 3.10 后面)。

## 发版(发版人在 main 上执行)

```bash
git checkout main && git pull
scripts/release.sh 3.56.0 -m "一句话说明"     # 功能波 MINOR / 修复 PATCH
```

脚本做的事(任一校验不过即退出,什么都不改):

1. 校验:在 main 上、入库文件无未提交修改(`uv.lock` 除外)、与 `origin/main` 完全同步、
   版本号大于最近的 tag、tag 本地与远端都不存在、`src/version.py` 尚未是该版本;
2. 预览 `<上一 tag>..HEAD` 的提交清单,确认;
3. 改 `src/version.py`(单一事实来源)、`pyproject.toml`、`uv.lock` 根包 `version` 行
   (`uv.lock` 只写索引不动工作区——开发机那份常带镜像源改写,永不入库);
4. 提交 `release: vX.Y.Z`,打 annotated tag(首行 `-m` 说明,正文附提交清单),推送 main 与 tag。

tag 推上去后两个 workflow 自动接手:`release.yml` 建 GitHub Release(核对由 `scripts/verify-release-ref.sh` 承担:
tag 在 main 线上、**目标 tag 里的**版本号等于 tag 名;不合格则任务标红不建 Release;说明 = tag 正文 + 自动生成的
PR 清单),随后在同一次 run 里串联 `deploy.yml`,停在 Environment `production` 等发版人批准(见下节);
`sync-master.yml` 把版本节点合进内网 master。

**PR 不改版本号**。两人并行时各自在 PR 里 bump 必然抢号(2026-09-10 实证);版本号只在发版
那一刻由发版人统一定。CLAUDE.md 里各波次的「vX.Y 某某波」叙述照旧,号以发版为准。

## 部署

**流水线(默认,issue #102,方案全文 `docs/auto-deploy-plan.md`)**:tag 推上去后 `release.yml` 建完 Release 即串联
`deploy.yml`,job 停在 Environment `production` 等批准;批准后 runner 用部署专用密钥 ssh 到生产机,forced command
`/root/bin/dorami-deploy` 起仓库外的 worker(锁与跨版本状态的唯一 owner:核验 tag→sha / 在 main 线上 / 版本号 / 协议、
in-progress 事务、方向与单调护栏、首装门),worker 再跑目标 tag 自己的 `./deploy-docker.sh vX.Y.Z`(预检 → build →
目标镜像 `--check-config` / `--plan-migrations` → up → `/api/health` 五项核对);runner 最后从公网再核一次 `/api/health`,
结果与元数据落在 Actions Job Summary 与 Deployments 面板。

- **审批纪律**:多个 run 同时等批准时(`queue: max` 保留所有等待项),只批最新合格版本,对被取代的 run 点 Reject。
  GitHub 不保证执行顺序,乱序造成的无意降级由生产机 worker 的单调护栏兜住(目标必须高于当前基线)。
- **手动部署 / 代码降级入口**:Actions → Deploy → Run workflow,填一个已发布的 tag;降级或基线未知须勾 `allow_downgrade`,
  对已成功的同 tag 重新部署须勾 `force_redeploy`(默认回放成功、不重跑)。
- **只部署宣告了部署协议的 tag**:目标 tag 的 `scripts/deploy-lib.sh` 须有 `DORAMI_DEPLOY_PROTOCOL=<n>`(v3.59 起);
  更早的 tag 流水线拒绝,走下面的手工路径。
- **首装 / 基线未知也要勾 `allow_downgrade`**:全新机器(或运行容器没有构建身份)时 worker 读不到基线,单调护栏按「未知」拒绝;
  这属于人为确认的范围,首次经流水线部署一台机器用 Run workflow 并勾选它。
- 失败不自动回滚:job 标红、Deployment 标红。**看失败发生在哪一步**:SSH 那一步失败 → 生产机上 in-progress 事务保留
  (暂存的上一版镜像 + 事务备份),同一 tag 重跑会复用该事务,要换目标须先在生产机 `/root/bin/dorami-deploy-worker --close-in-progress`;
  SSH 已成功、只是**公网核对**那一步失败 → worker 已晋升 last-success、事务已收口,这时是站点对外不可达之类的问题,不是半部署。
- 手动 Run workflow 时 ref 选 `main`(checkout 的是所选 ref,核验脚本从那里来)。

**手工兜底**(生产机上直接跑,与流水线共用同一把锁,互斥):

```bash
./deploy-docker.sh            # 一键:拉取 tag,部署版本号最新的发布版
./deploy-docker.sh v3.56.0    # 指定版本(代码回滚也是这一句,见下节)
./deploy-docker.sh --here     # 部署当前工作树:非发布版,联调/应急用,输出会标注
./deploy.sh [同上]            # 裸机路径参数完全相同
```

两条脚本共用 `scripts/deploy-lib.sh`:`git fetch --tags`(离线时按本地 tag)→ 选版本 →
拒绝入库文件有手改的工作树(未跟踪的 `production.ini`/`.env`/`logs` 不算)→
`git checkout --detach <tag>` → **以切换后的那份脚本重执行**(bash 边读边跑,不重执行会读到
半新半旧的字节)→ 核对 tag 名 = `v` + `__version__` → 备份 SQLite → 原流程。

构建来源随部署带进运行时:Docker 路径经 compose build args 烤进镜像(镜像里没有 `.git`),
裸机路径经 `ecosystem.config.js` 透传 PM2 进程;`/api/runtime` 透出 `build = {ref, sha, source}`,
**设置 → 关于** 显示「构建:发布版 v3.56.0 · abc1234」或「非发布版 v3.56.0-3-gdef5678」。
生产上有人手改代码或用 `--here` 部署过,这一行会如实暴露。

## 回滚

代码回滚是一句话:`./deploy-docker.sh v3.55.0`。但 **Alembic 迁移不一定可逆**(已有不可逆的
Podcast 迁移),所以每次部署前脚本都把 SQLite 在线备份到 `backups/`(保留 10 份;`sqlite3 .backup`
在线一致快照,WAL 模式下裸 `cp` 会丢最近写入)。完整回滚 = 切回上一 tag **+** 用对应备份覆盖库文件:

```bash
docker compose stop backend            # 裸机:pm2 stop dorami-backend-v2
cp backups/cms_data.db.20260914-110000 data/cms_data.db && rm -f data/cms_data.db-wal data/cms_data.db-shm
./deploy-docker.sh v3.55.0
```

备份不含 `data/media` 与 `data/podcast-artifacts`(可再生 / 体量大),按需另备。

**经流水线降级**是「代码降级入口」,不是完整回滚:Actions → Deploy → Run workflow 填旧 tag 并勾 `allow_downgrade`;
生产机 worker 会先让目标镜像算迁移计划,数据库已走过目标代码不认识的迁移时判 `incompatible` 并 fail closed——此时先恢复备份。

**恢复哪一份备份,按代际分两种情况**(流水线的事务备份都在 `backups/…pre-<tag>`,manifest 在 `/var/lib/dorami-deploy/`):

- **撤销一次失败的升级**(B 部署失败,要回到 B 之前):读 **`in-progress.json` 的 `prev.db_backup`**(已用 `--close-in-progress`
  关闭则读对应的 `closed-<txn>.json`)。它是部署 B **之前**、含 A 运行期间全部数据的快照。`last-success.json` 的 `prev` 是部署 A
  之前的快照,用它会多丢一段数据。
- **回退最近一次成功的部署**(A 已成功运行、要回到 A 之前):才读 `last-success.json` 的 `prev.db_backup`。

恢复前核对 manifest 里的 `target` / `prev.ref` / `prev.sha` / 备份路径,记下当前状态,再停后端、覆盖库文件、
`/root/bin/dorami-deploy-worker --close-in-progress` 关闭未收口事务,最后重跑目标 tag。

## 分支保护(GitHub 仓库设置,手动开一次)

Settings → Branches → Add rule for `main`:

- Require a pull request before merging(禁止直推;两人一视同仁);
- Require status checks to pass:勾 `backend (pytest)` 与 `frontend (lint + build)`(CI 跑过一次后才会出现在列表里);
- Do not allow force pushes / deletions。

tag 不设保护(2026-09-14 拍板:任何人可打 tag);`release.yml` 的核对负责把不合格的 tag 标红。

Environment(一次性,自动部署用,见 `docs/auto-deploy-plan.md` §4.2):Settings → Environments → New `production`:

- Required reviewers:只写发版人(「Prevent self-review」按需);
- Deployment branches and tags:Selected → 加 Tag 规则 `v*` 与 Branch 规则 `main`;
- Environment secrets:`PROD_SSH_KEY`(部署专用 ed25519 私钥,与日常运维密钥分开);
- Environment variables:`PROD_HOST`、`PROD_USER`(root)、`PROD_KNOWN_HOSTS`(`ssh-keyscan -t ed25519 <host>` 的一行,
  **只扫描一次存成文件、带外核验后保存同一份内容**:在已验证的运维 SSH 会话里 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
  取主机指纹,与 `ssh-keygen -lf <扫描文件>` 比对一致才把该文件内容存进变量;扫描只采集网络对端给的 key,不证明它属于目标主机,
  核验后重新扫描保存的也不再是核验过的那份;步骤见 `docs/deploy-docker.md`)、`PROD_PUBLIC_URL`(缺省 https://www.dorami.cloud)。
生产机侧的安装(launcher / worker / conf / authorized_keys)见 `docs/deploy-docker.md`「自动部署流水线」。

## 边界与不做

- 部署脚本要求仓库有 `.git`(`git clone` 取的代码);tar 包搬进内网的机器用 `--here`,
  关于页会显示非发布版——这是诚实的,不是缺陷。
- 不做「部署即打 tag」:发布决定属于人,脚本只执行。
- 自动部署只覆盖外网 Docker 节点(issue #102,2026-09-16 拍板翻案);裸机路径仍由人跑 `./deploy.sh`。
  流水线只部署宣告了部署协议的 tag;不自动回滚;不引入第二种 tag——防误触的边界是 Environment 的批准人,不是 tag 名。
- CI 跑全量 pytest 约 5 分钟;真变慢再拆快慢两档。
