@AGENTS.md

> ## ⚠️ 本分支是 `master`(内网特殊适配分支)——开工前必读
>
> 本块及下述适配**只存在于 master 分支**,main 上没有;若你在 main 上看不到这段属正常。
> (本分支曾名 `intranet`,2026-08 更名 `master`——内网代码托管平台的内部开源仓以
> master 为默认主干名,同名对齐后,外网适配分支与内网主干一一对应,免去第三个分支名的来回同步。)
>
> **定位**:公网主线 main 的内网部署适配分支,长期存在、永不废弃合并。目标环境:
> 出网被企业网关 MITM(自签证书链重签一切 TLS)、Docker 过旧不可用(18.x/无 compose)、
> CentOS + 手动安装的 nginx/node(非包管理器路径)、站点以纯 HTTP + IP 访问。
>
> **三条铁律**:
> 1. **一切内网适配只提交本分支,绝不合并/cherry-pick 回 main**——即使改动看似通用
>    (先例:`disable_tls_verify` 曾按"通用能力"被提交到 main,后按用户要求回退)。
> 2. **同步方向单一**:main → master,反向永远禁止;也不要把本分支推成 PR。
>    **2026-09-08 起同步按 main 的版本 tag(`v*`)触发**(GitHub `Sync main → master`
>    工作流 `--no-ff` 合并;此前逐 push 触发,冲突随机落在任何一次合入上),冲突时任务
>    失败、人工在 worktree 里 `git merge <tag>` 处理。
> 3. **每次开工前先核对与 origin/main 的差异**(SessionStart hook 会自动注入概况):
>    落后较多时,先 merge 上游 tag、解决冲突、验证后再做新工作,避免差异滚雪球。
>
> **本分支独有内容以 [`INTRANET_DELTA.md`](./INTRANET_DELTA.md) 为唯一权威登记簿**
> (2026-09-08 立,上游基线 `UPSTREAM_BASE` 与逐条差异/冲突原则/可否上游化都在那里;
> 新增内网改动与合并上游都必须更新它)。GitHub 侧 master 的差异面概括:
> `[network] disable_tls_verify` 开关及 7 处 httpx `verify=settings.network.tls_verify`
> 接线(fetchers/base、legacy_backend、media_store、remote_sync、source_builder、
> x_api、llm/client)、本须知块、`.claude/settings.json` + hooks、IM 机器人两册文档、
> 内网修复脚本;内网托管仓在此之上的 SSO 等差异由内网 Agent 按清单 §6 指引补登。
>
> **部署面文件已不再是本分支独有**:`deploy.sh` / `ecosystem.config.js` /
> `production.example.ini` 的 `[server]`/`[nginx]` 两节于 **v3.39.0 回迁 main 扶正**——
> 起因是一台**公网**机不便装 Docker,证明该路径服务的是「宿主没有 Docker」而非
> 「内网适配」,继续挂在本分支会让分支名与用途脱节。回迁时清理了 RAG 形态判定等内网痕迹,
> 本分支自此**直接用 main 版本,不再本地维护**(裸机路径用法见 `docs/ops/deploy-baremetal.md`)。
> 注意这不是铁律 1 的例外:回迁的是通用裸机部署能力,`disable_tls_verify` 及其接线
> 明确**未随行**——出网跳过 TLS 校验是内网 MITM 网关的专属妥协,公网开它等于自毁。
>
> **merge 上游冲突解决原则**(详见 INTRANET_DELTA.md §1):部署面文件(deploy.sh/ecosystem/ini 两节)**以 main 为准**
> (v3.39.0 前是「以本分支为准」,已反转);`src/` 冲突以 main 的演进为准,但**必须保留
> `verify=settings.network.tls_verify` 接线**(main 新增 httpx client 时也要顺手接上);
> 本文件/AGENTS.md 冲突 = 保留须知块 + 采纳 main 的其余更新。
>
> **内网部署**:同步最新 master 后执行 `./deploy.sh --here`,保留当前分支的内网适配。
> 默认部署或指定版本 tag 会切到 main 发布版(不含内网适配);通用用法见 `docs/ops/deploy-baremetal.md`。
> 内网姿态需在 `production.ini` 反转两处:`[network] disable_tls_verify = true`(公网默认 false)、
> `[auth] cookie_secure = false`(纯 HTTP + IP 访问,公网走 HTTPS 时应为 true)。


# Claude Code 专属附注

- 与 Codex 共用的全部须知在 `AGENTS.md`(上面已导入,以它为准);本文只放 Claude Code 独有的事。
- **路径作用域规则** `.claude/rules/`:`frontend.md`(`frontend/**`)、`migrations.md`(`src/models/**` `alembic/**` `src/storage/**`)、`deploy-scripts.md`(部署脚本 / `docker/` / workflows)、`tests.md`(`tests/**`)。触到匹配文件时自动加载;它们只是硬规则 + 指针,正文仍在 `docs/`。
- **项目技能** `.claude/skills/`:`dorami-issue-loop`(一条 issue 从开工到合入的闭环)、`dorami-sandbox-acceptance`(隔离栈 Playwright 验收)、`dorami-wave-docs`(文档同波 / 归档 / 决策行 / 守卫)。
- **自动记忆**(`~/.claude/projects/…/memory/`)只放:用户偏好与纠正、本机环境、仓外项目、私密事项。事实与规则进仓、可复用流程进技能;凡 `AGENTS.md` / `docs/` 已说的不写记忆。生产主机与运维实录在个人技能(`~/.claude/skills/dorami-prod-ops`),**不入库**。
- **Write / Edit 工具会把源码里的 `\uXXXX` 转义解码成真字符**(正则里的零宽空格、全角空格等会变成不可见字符,肉眼看不出):带转义的行用 Bash 跑 Python 脚本写入(以 `chr(92)` 拼反斜杠),写完用 Python 扫一遍非 ASCII 字符。
- 发版、部署、生产库只读统计等高影响操作先问用户;生产数据拷回本机须由用户本人执行(权限分类器会拦)。
