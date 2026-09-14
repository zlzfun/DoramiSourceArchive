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

tag 推上去后两个 workflow 自动接手:`release.yml` 建 GitHub Release(核对 tag 在 main 线上且
等于代码版本号,不合格则任务标红不建 Release;说明 = tag 正文 + 自动生成的 PR 清单),
`sync-master.yml` 把版本节点合进内网 master。

**PR 不改版本号**。两人并行时各自在 PR 里 bump 必然抢号(2026-09-10 实证);版本号只在发版
那一刻由发版人统一定。CLAUDE.md 里各波次的「vX.Y 某某波」叙述照旧,号以发版为准。

## 部署

```bash
./deploy-docker.sh            # 一键:拉取 tag,部署版本号最新的发布版
./deploy-docker.sh v3.56.0    # 指定版本(回滚也是这一句)
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

## 分支保护(GitHub 仓库设置,手动开一次)

Settings → Branches → Add rule for `main`:

- Require a pull request before merging(禁止直推;两人一视同仁);
- Require status checks to pass:勾 `backend (pytest)` 与 `frontend (lint + build)`(CI 跑过一次后才会出现在列表里);
- Do not allow force pushes / deletions。

tag 不设保护(2026-09-14 拍板:任何人可打 tag);`release.yml` 的两道核对负责把不合格的 tag 标红。

## 边界与不做

- 部署脚本要求仓库有 `.git`(`git clone` 取的代码);tar 包搬进内网的机器用 `--here`,
  关于页会显示非发布版——这是诚实的,不是缺陷。
- 不做「部署即打 tag」:发布决定属于人,脚本只执行。
- 不做自动部署(tag 推上去自动上生产):两台生产机一台裸机一台 Docker,上线仍由人在机器上跑脚本。
- CI 跑全量 pytest 约 5 分钟;真变慢再拆快慢两档。
