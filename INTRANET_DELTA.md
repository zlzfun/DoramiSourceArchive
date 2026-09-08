# 内网差异清单(INTRANET_DELTA)

> **本文件只存在于 master 分支(内网适配分支)及其下游的内网托管仓,main 上没有。**
> 它是「内网相对公网主线 main 到底改了什么」的唯一权威登记簿。
> 上游同步走 merge 而非 rebase(内网仓多人协作、不重写历史),所以差异面不能靠 git 历史
> 一眼读出——必须靠本清单人工维护。**新增内网改动、合并上游版本,两件事都要更新本文件。**

## 0. 当前状态

| 字段 | 值 |
|---|---|
| `UPSTREAM_BASE` | `v3.48.0`(main `2bfd459`) |
| 本清单最近核对 | 2026-09-08(GitHub 侧 master,内网侧差异待内网 Agent 补登,见 §4) |
| 同步方式 | GitHub `Sync main → master` 工作流,**按 main 的版本 tag(`v*`)触发**,`--no-ff` 合并;冲突时任务失败、人工处理 |

每次合入上游版本后:更新 `UPSTREAM_BASE`,逐条复核 §2/§3 里「冲突原则」栏是否仍成立,
被上游吸收的条目移到 §5「已上游化」。

## 1. 维护规则

1. **上游优先**:凡是通用能力(不含内网机密、不含内网环境专属妥协)一律先提 main,
   再随版本同步下来;本清单只登记**真正无法公开或无法通用**的改动。先例:裸机 PM2 部署
   路径曾是内网专属,v3.39.0 证明它服务的是「宿主没有 Docker」而非「内网」,已回迁 main。
2. **一条一个关注点**:按「关注点」而非按文件登记(SSO 是一条,TLS 网关是一条),
   同一关注点涉及的文件列在条目内。
3. **每条五栏**:关注点 / 涉及文件 / 为什么必须在内网分支 / merge 上游的冲突解决原则 /
   能否上游化(能:写明阻碍;不能:写明原因)。
4. **冲突解决的总原则**:`src/` 以 main 的演进为准,再把本清单登记的接线补回去;
   部署面文件(`deploy.sh`/`ecosystem.config.js`/ini 示例)以 main 为准;
   `CLAUDE.md`/`AGENTS.md` = 保留顶部须知块 + 采纳 main 的其余更新;本文件永远以本分支为准。
5. **禁止反向**:本清单里的任何东西都不得 merge/cherry-pick 回 main;要上游化就在 main
   重新实现一个公网姿态安全的版本(见 §5 的先例)。

## 2. 代码与配置差异(GitHub 侧 master 已登记)

### 2.1 出网 TLS 校验全局开关

- **涉及文件**:`src/config.py`(`NetworkConfig.disable_tls_verify` + `tls_verify` 属性 +
  `load_config` 读取 `[network] disable_tls_verify` / `DORAMI_DISABLE_TLS_VERIFY`);
  7 处 httpx client 接线 `verify=settings.network.tls_verify`:`src/fetchers/base.py`、
  `src/fetchers/web_content/legacy_backend.py`、`src/services/media_store.py`、
  `src/services/remote_sync.py`、`src/services/source_builder.py`、`src/api/routers/x_api.py`、
  `src/llm/client.py`;`config/production.example.ini` 的 `[network]` 节 4 行说明。
- **为什么在内网分支**:内网出网被企业网关 MITM(自签证书链重签),httpx 校验必失败。
  用户拍板不进 main:公网开它等于自毁,「默认关闭无影响」也不例外(2026-07-24)。
- **冲突原则**:main 改动这些文件时以 main 为准,**再把 `verify=` 参数补回**;main 新增任何
  httpx client 时同步补接线(这是本条最常见的冲突来源,见 2026-09-08 播客波 `remote_sync`)。
- **能否上游化**:能,但要换形态——httpx 0.28 原生读取 `SSL_CERT_FILE`,若内网能导出网关 CA
  并合成 bundle,只需部署层设环境变量,7 处接线与开关可整体删除,校验也不必关闭。
  阻碍:内网是否能拿到网关 CA 未验证。

### 2.2 内网部署姿态(ini,不入库)

- **涉及文件**:各机 `config/production.ini`(不入库),相对公网姿态反转两处:
  `[network] disable_tls_verify = true`、`[auth] cookie_secure = false`(纯 HTTP + IP 访问)。
- **冲突原则**:无(文件不入库);`production.example.ini` 以 main 为准 + 保留 `[network]` 说明。

## 3. 文档与工具差异(GitHub 侧 master 已登记)

| 关注点 | 涉及文件 | 说明 / 冲突原则 |
|---|---|---|
| 分支须知块 | `CLAUDE.md` 顶部块、`AGENTS.md` 顶部块 | 分支纪律;冲突时保留块 + 采纳 main 其余更新 |
| 会话提示 hook | `.claude/settings.json`、`.claude/hooks/master-session-start.sh` | 开工注入落后主干提示;main 无对应文件,不冲突 |
| IM 机器人接入文档 | `docs/contracts/im_bot_integration.md`、`docs/im-bot-architecture.md`、`docs/README.md` 索引两行 | 纯内网场景文档按拍板只进本分支;`docs/README.md` 冲突 = 两边索引都保留 |
| 部署文档内网注记 | `docs/deploy-docker.md`(内网 Docker 过旧的说明段) | 以 main 为准 + 保留注记段 |
| 内网日报修复脚本 | `scripts/repair_intranet_briefs.py` | 一次性运维脚本,main 无对应文件 |
| 本清单 | `INTRANET_DELTA.md` | 永远以本分支为准 |

## 4. 内网托管仓独有差异(待内网 Agent 补登)

GitHub 侧 master 只含上面两节;内网托管平台的 master 在此之上还有内网 SSO 等逻辑,
这些差异 GitHub 侧看不到。**内网 Agent 接手后按 §6 指引把它们逐条补登到本节**,格式同 §2:

### 4.x 〈关注点,如:内网 SSO 登录〉

- **涉及文件**:
- **为什么在内网分支**:
- **冲突原则**:
- **能否上游化**:

## 5. 已上游化(历史记录,勿再作为差异维护)

| 曾经的差异 | 上游化版本 | 备注 |
|---|---|---|
| 裸机 PM2 部署路径(`deploy.sh`/`ecosystem.config.js`/ini `[server]`/`[nginx]`) | v3.39.0 | 回迁时清理 RAG 判定等内网痕迹;`disable_tls_verify` 明确未随行 |
| Alembic 多头容忍(`upgrade("heads")`) | v3.38.1 | 起因是内网仓自带迁移支线 |

## 6. 内网 Agent 接手指引

目标:把内网托管仓相对 `UPSTREAM_BASE` 的全部差异登记进 §4,之后每次动作都维护本文件。

1. **确定基线**:读 §0 的 `UPSTREAM_BASE`;在内网仓确认该 tag(或对应提交)存在,
   `git merge-base HEAD <tag>` 应等于该 tag 的提交。若内网仓没有 tag,用同步进来的
   merge 提交信息里的「auto-sync vX.Y.Z」定位。
2. **枚举差异**(两条命令互为补充,前者看当前状态,后者看来龙去脉):
   ```bash
   git diff --stat <UPSTREAM_BASE>..HEAD            # 当前相对上游改了哪些文件
   git log --no-merges --oneline <UPSTREAM_BASE>..HEAD   # 内网侧非合并提交(每条通常对应一个关注点)
   ```
   排除 §2/§3 已登记的文件,剩下的就是内网托管仓独有差异。
3. **按关注点归组登记**:同一个功能(如 SSO)涉及的所有文件放进一条;写清「为什么必须在内网」
   (机密 / 内网环境专属 / 依赖内网系统),以及 merge 上游时该文件冲突怎么解(通常是
   「以 main 为准,再把本条的接线/路由/中间件补回」)。
4. **标注上游化可能性**:能通用化的(比如「可插拔认证后端」这种抽象层)写明阻碍,
   作为之后向 main 提通用能力的候选——差异越薄,同步越便宜。
5. **每次合并上游之后**:更新 §0 `UPSTREAM_BASE` 与核对日期;把本次冲突里新出现的文件
   补进对应条目的「涉及文件」;上游已吸收的条目移到 §5。
6. **每次新增内网改动之后**:同一提交里更新本文件——没有登记的差异,下次合并时就会被
   当成「以 main 为准」而丢掉。

排查工具:`git diff <UPSTREAM_BASE>..HEAD -- <file>` 看单文件差异;
`git log -L :<function>:<file> <UPSTREAM_BASE>..HEAD` 追一个函数在内网侧的改动史。
