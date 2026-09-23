# 哆啦美：外网发版 → 内网发布与部署

以下以 **上游 `v3.62.0`、内网 `innersource/v3.62.0-r1`** 为例；每次替换版本及内网序号。同一上游再次发布用 `r2`、`r3`，已推送 tag 不移动、不覆盖。

| 操作位置 | 远端约定 | 命令环境 |
| --- | --- | --- |
| 内网工作 PC | `origin` = GitHub；`innersource` = 内网仓库 | Windows Git Bash，可调用 PowerShell |
| 内网部署机 | `origin` = 内网仓库 | Linux Bash |

命令从各自仓库根目录执行；逐段操作，任何一步失败先处理，再继续。

## 0. 前提：外网版本已验收

上游通过 `scripts/release.sh` 创建并推送 `va.b.c`，触发 GitHub Release / Deploy workflow；按环境设置批准后自动部署。确认部署成功，至少完成登录、阅读与本次主要功能的冒烟测试。

GitHub `master` 在上游 **版本 tag 推送时** 自动合入对应 `main` 提交。确认同步 workflow 成功；若冲突，先处理 GitHub 侧合并。取用的 `master` 应对应本次已验收版本，不能夹带尚未验收的新改动。

## 1. 同步代码并打内网 tag

在内网工作 PC 的同一个 Git Bash 会话中设置本次版本：

```bash
UPSTREAM=v3.62.0
REV=r1
TAG="innersource/${UPSTREAM}-${REV}"
NAME="dorami-${UPSTREAM}-innersource-${REV}"

git switch master
git pull --ff-only innersource master
git fetch origin master --tags
git merge origin/master
```

解决冲突、保留内网适配，更新 `INTRANET_DELTA.md` 的 `UPSTREAM_BASE`；提交改动并验证。若无法快进，先处理本地与内网 `master` 的分叉。确认最终代码全部提交后：

```bash
git push innersource master
git tag -a "$TAG" -m "内网发布 ${UPSTREAM}-${REV}" -m "Upstream: $UPSTREAM ($(git rev-parse "${UPSTREAM}^{commit}"))"
git push innersource "refs/tags/$TAG"
```

**内网 tag 就在这一步末尾打：完成同步与验证之后、准备正式包之前。** 只推送到 `innersource`；文案、发布包和部署统一对应这个 tag。后续若需改代码，创建下一个 `rN`，重新打包。

## 2. 准备发布材料与两个包

**文案：** 比较「当前实际部署的内网版本 → 本次 tag」，准备 `changelog.md` 和 `release-post.md`。内网独有变更较少时，可让外网 AI 比较两次发布对应的上游版本，再由内网补充差异。

- Changelog：精简列出主要用户功能，用“等”收尾即可。
- 更新动态：按主要功能用小标题分节，简短介绍，每个主要页面配 1–2 张真实截图。
- 两份读者文案都不包含管理面、部署和 CI 改动；升级操作单独记录。

**打包：** 在内网工作 PC 继续操作。源码用 [git archive](https://git-scm.com/docs/git-archive) 从固定 tag 导出；前端从该源码包解压构建，保证两包同源。

```bash
mkdir -p release
git archive --format=tar.gz --prefix="${NAME}/" -o "release/${NAME}-src.tar.gz" "$TAG"

# 首次解压到该版本的独立目录，使用内网配置的 npm 源
tar -xzf "release/${NAME}-src.tar.gz" -C release
cd "release/${NAME}/frontend"
npm ci && npm run build
cd ../../..

powershell -NoProfile -Command "Compress-Archive -Path 'release/${NAME}/frontend/dist/*' -DestinationPath 'release/${NAME}-frontend-dist.zip'"
```

确认构建成功、ZIP 根目录包含 `index.html` 和 `assets/`；`dist/*` 的压缩方式不会额外包一层 `dist` 目录。[Compress-Archive 说明](https://learn.microsoft.com/zh-cn/powershell/module/microsoft.powershell.archive/compress-archive?view=powershell-7.5)

上传材料位于 `release/`：

- `dorami-v3.62.0-innersource-r1-src.tar.gz`
- `dorami-v3.62.0-innersource-r1-frontend-dist.zip`

另记录本次内网 tag、commit SHA 和上游版本，供社区发布页填写。

## 3. 内网部署、检查与回滚

在内网部署机执行。先处理本次新增或变更的 `config/production.ini` 配置、环境变量；环境变量应在运行部署命令前注入。脚本不会自动替你填写新增配置值。

先同步内网 `master`，让部署脚本保持更新：

```bash
git switch master && git pull --ff-only origin master
```

然后二选一：

**A. 按固定 tag 部署（推荐正式发布）：**

```bash
git fetch origin tag innersource/v3.62.0-r1 &&
./deploy.sh --code innersource/v3.62.0-r1
```

`--code` 使用当前目录的部署脚本，部署 tag 对应的业务代码；tag 必须已在本机。当前界面仍可能显示“非发布版”，以 tag 和 SHA 核对实际版本。

**B. 沿用当前工作树部署：**

```bash
./deploy.sh --here
```

使用 B 时，确认当前 HEAD 与本次发布 tag 相同，且没有未提交的代码修改，否则部署内容会与发布包不同。不要用无参数的 `./deploy.sh`，它仍只选择上游 `v*` 标签。

部署脚本会现场构建前端，社区前端包用于分发，无需额外覆盖部署目录。部署后执行 `./deploy.sh --status` 核对实际版本，并冒烟检查内网 SSO、首页/文章阅读、本次主要功能；通过后再发布社区版本。

**需要回滚时：**

```bash
./deploy.sh --status              # 先看回滚目标、数据库预判与未完成事务
./deploy.sh --rollback --yes      # 回到 prev；不出网、不构建、不 checkout，只回一代
```

若提示必须恢复数据库，先确认快照时间及其后数据会丢失，再执行 `./deploy.sh --rollback --restore-db --yes`。本次手工改过的配置或环境变量，也要核对是否需要恢复。回滚后重新冒烟测试。

## 4. 内源社区发布与用户公告

1. 部署验收通过后，创建对应内网版本，填写精简 changelog，上传第 2 步两个包及版本信息。
2. 社区自动生成动态与用户群消息后，在动态中补充准备好的功能介绍和截图。
3. 在用户群补充主要亮点、使用入口，收集问题与反馈。

将「本次成功部署的 tag、SHA、上游版本、发布时间、社区链接」记入发布记录，作为下一次生成文案的起点；部署失败的 tag 不作为这个起点。
