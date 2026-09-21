# 裸机部署回滚方案(issue #126)

> 状态:**R3 稿 → 2026-09-21 用户拍板 §7 七项全按推荐 → 已实现**(分支 `feat/issue-126-baremetal-rollback`,实现记录见 §9)。原 R3 状态:R1 经 codex(gpt-6-astra ultra)检视 25 条全部成立,结构性改形为「运行副本版本化」(§3.1);
> codex 对改形的 13 条答复、对 R2 的复检(7 处形状缺口 + 1 条新引入 P1)亦全部采纳(记录见 §8)。**待用户拍板 §7、codex 终审。**
> 范围由用户收窄:只做「健康门失败告警 + 手动一键回滚」,**不做自动回滚**(2026-09-21 拍板)。
> 分支 `docs/issue-126-baremetal-rollback`;实现分支另开。实现方 / 检视方:Claude Code 实现,Codex 检视(gpt-6-astra ultra)。

## 1. 背景

用户原话(2026-09-21):内网 master 用 `deploy.sh` 一键部署,「部署完成之后发现有一些**部署本身**相关的问题,
如版本起不来等,能够立刻进行回撤、回到一个可用状态」。核对现状(main `3b6e4b9`;master 与 main 的
`deploy.sh` / `scripts/deploy-lib.sh` / `ecosystem.config.js` 零差异,INTRANET_DELTA §1.4 规定部署面文件以 main 为准):

- **裸机 `deploy.sh` 没有健康门**:第 7 步 `pm2 reload` + `nginx -s reload` 之后直接打印 Deploy complete,后端起不来也算成功。
  Docker 路径已有 `/api/health` 五项核对(issue #102,`deploy-docker.sh` [7/7]);裸机路径连核对都没有,也没有部署锁
  (`acquire_deploy_lock` 只有 `deploy-docker.sh` 在调,且它先抢锁再 resolve)。
- **没有回滚机制**:现行「回滚」是 `docs/release-process.md` 的三行手工命令(停后端、覆盖库文件、重跑旧 tag)。
- **内网跑的是 `./deploy.sh --here`**(INTRANET_DELTA §0:切 tag 会切到 main 的发布版,丢掉内网适配),「回滚 = checkout 上一 tag」
  在内网不成立——**回滚点必须是上次成功部署的代码身份(commit / 固化快照)**。
- **运行时与 git 工作树绑死**:PM2 以 `<repo>` 为 cwd、`<repo>/src` 为 PYTHONPATH、`<repo>/venv` 为解释器(`ecosystem.config.js`),
  部署 = 在同一棵树上 checkout、就地重装 venv、`rm -rf html_dir/* && cp`;旧进程的延迟 import 会读到新文件,dist 与 venv 的旧状态
  在切换那一刻被销毁。这是 R1 逐条打补丁失败、改形为 §3.1 的根因。
- 数据库靠 Alembic 单向迁移:70 个迁移文件里 7 个 `downgrade` 为空、15 个含 `raise`(部分是条件围栏,如
  `c4d8e2f6a1b3_retire_interest_mute_stance.py`),`alembic downgrade` 不是回滚手段,只能恢复快照。
- Docker 流水线 worker(`docker/dorami-deploy-worker.example`)已有「in-progress / last-success 事务 + prev 采样 + 事务备份 +
  引用保护清理」状态机;本方案把同一形状搬到裸机部件上,不引入第二种状态语义,也不改 worker。

## 2. 目标与非目标

**目标**

- **健康门**(两级,§4.9):后端身份门 + 站点链路门都通过才算部署成功;任一不通过即**告警**:退出非零、事务保留、终端打印
  精确的回滚命令。系统不被自动改动。
- **`./deploy.sh --rollback`**:回到上次成功部署的可用状态(代码副本 + venv + dist + nginx 配置集合 + 视情况 DB),**不 checkout
  工作树、不出网、不重新构建**,回滚后同样过两级健康门。未完成的回滚可续做,不翻转方向。
- **DB 按迁移计划分流**(§4.8):目标代码认识当前库 → 不覆盖库(可能向前补迁移,先快照);不认识 → 默认拒绝并打印快照时刻与
  丢失窗口,`--restore-db` 显式确认才恢复。任何会执行迁移的切换都先快照。
- **`./deploy.sh --status`**:当前跑什么、上次成功是什么、有无未收口事务与中断阶段、回滚目标与 DB 处置预判、本机的恢复能力位。
- tag 模式、`--here` 模式(含 dirty 工作树)、`--code <sha|tag>` 模式同等支持。

**非目标(有意边界)**

- 不自动回滚(用户拍板);健康门失败只告警。
- 不接通知通道:告警 = 终端红字 + 退出码 + 事务文件;推送通道是 issue #82 第二层的事。
- 不改 Docker 路径行为:`deploy-docker.sh` 只做共用函数抽取的等价重构,默认值(健康预算 180 s / 90 次等)不变,
  `DORAMI_DEPLOY_PROTOCOL` 不 bump;裸机专属参数与检查只在 `deploy.sh` 生效(§4.14)。
- **只回一代**:回滚目标 = 相邻的上一个成功事务;更早的版本用 `./deploy.sh --code <sha>` 正向部署。
- 回滚**不重跑旧版 nginx 生成器**,而是恢复该版部署时记录在案、已校验的配置集合(§4.6)。
- 不备份 `data/media` / `data/podcast-artifacts`(可再生 / 向前兼容);不做迁移演练;不做 `alembic downgrade`。
- 不覆盖「部署成功、跑了半天才发现功能问题」的场景(issue #82 看护层);那种回滚会撞上 DB 丢失窗口提示,由人决定。
- **保证的显式例外**(§4.1):真正首装无回滚点;既有部署但证据冲突、或数据库不是 SQLite 时,默认**停止**;只有显式
  `--no-rollback-guarantee` 才继续,且事务与 `--status` 持续显示缺失的能力位。

## 3. 方案总览

### 3.1 形状:运行副本版本化(capistrano 式)

运行时**完全从 git 工作树解耦**:每次部署生成一个不可变的 release(代码副本 + 自己的 venv 指针 + dist + 已校验的 nginx 配置集合
+ 事务材料 + 固化的回滚执行体),PM2 从 release 的实路径启动。工作树只用于编排与取代码。

- 切换 / 回滚 = 用目标 release 的 `ecosystem.config.js` 重起 PM2 + 切 `html_dir` symlink + 恢复 nginx 配置集合。
  **没有任何运行中的路径被换目标**(release 内的 `venv` symlink 创建后不再改)。
- 回滚不 checkout;HEAD 留在用户放的地方。dirty `--here` 的字节被固化成 commit 归档,可原样重放。
- 回滚执行体随 release 固化,入口在树外(`deploy-state/rollback`),旧版 `deploy.sh` 不认识参数也无妨。
- 代价:实现面比 R1 大;内网现有安装要走一次**收养**(§4.11,一次 PM2 重启的维护窗);每个 release 的 `app/` 是整仓归档
  (clean HEAD 实测 823 文件 / 19.1 MiB,gz 约 9.3 MiB),保留 3 份可接受。

### 3.2 目录布局(启用后)

```
<repo>/                                    # 编排 checkout;运行时不读它
  releases/<txn>/
    app/                                   # git archive <code_sha>(src/ alembic/ ecosystem.config.js …)
    app/venv -> /abs/<repo>/venvs/<指纹>/  # 本 release 私有、不可变的 symlink
    app/{data,logs} -> <repo>/{data,logs}   # 共享挂点(按 §4.7 的路径探针动态补齐)
    app/config/production.ini -> <repo>/config/production.ini
    dist/                                  # 前端构建产物(或 NGINX_RELEASES_DIR/<txn>,sudo 归属)
    nginx/                                 # 本次写入的站点配置集合快照 + changes.json
    controller/{deploy-lib.sh,rollback.sh} # 开事务时当前脚本的副本(固化执行体)
    manifest.json  app.sha256  dist.sha256
  venvs/<指纹>/{inputs.json,.dorami-complete,…}
  deploy-state/{in-progress.json,last-success.json,closed/<txn>.json,rollback -> releases/<txn>/controller/rollback.sh}
  backups/baremetal/<txn>/<db>.sqlite       # 不落入 worker 的 backups/<db>.* 通配
  current -> releases/<txn>/app             # 只给人和 --status 看;PM2 用实路径
/var/www/my_site -> <dist of txn>           # nginx root 不变
```

`.gitignore` 增 `releases/`、`venvs/`、`deploy-state/`、`venv/`、`logs/`(后两项现状缺失,dirty 固化的排除规则另行独立于 `.gitignore`,§4.4)。

### 3.3 命令与流程

| 命令 | 语义 |
|---|---|
| `./deploy.sh [vX.Y.Z]` | tag 模式:锁 → **只读**解析目标 + 能力检查(不 checkout;目标 tag 的脚本须宣告 `DORAMI_BAREMETAL_TXN`,否则一律拒绝并提示 `--code`)→ 收养检测 / 续做(§4.11)→ checkout + exec 目标脚本(「tag 即发布」不变)→ 目标脚本按下面流程部署自己 |
| `./deploy.sh --here` | 当前工作树内容(dirty 也固化)由当前脚本部署 |
| `./deploy.sh --code <sha\|tag>` | **当前编排器**部署任意代码对象(不 checkout 工作树);「回到更早版本」「回到刚被回滚掉的版本」「旧 tag 没有事务能力」都走这里 |
| `./deploy.sh --rollback [--restore-db] [--yes] [--to <txn>]` | 转发到 `deploy-state/rollback`;目标选择见 §4.10 |
| `./deploy.sh --status` | 只读,不抢锁 |
| `./deploy.sh --discard-txn` | 人已手工处理,归档未收口事务(材料保留) |
| `./deploy.sh --adopt [--adopt-sha <sha>]` | 收养旧形态安装(首次也会被 deploy 自动触发,§4.11) |

正向部署时序(锁内;**阶段落盘顺序即恢复发现顺序**,§4.3):

```
锁 ─ 只读解析目标 + 能力检查(不 checkout,§4.2)─ 未收口事务:adopt / rollback 先续做,deploy 按 §4.3 判定
─ 收养检测 / 续做 / 完成(§4.11;完成之前不进入正向部署)─ resolve(tag 模式 checkout 编排树;运行时已在 release 副本,不受影响)
─ [1] 系统依赖 ─ [2] 配置校验 ─ 只读预检:身份与回滚点、证据冲突(§4.1)
─ 开事务:mkdir releases/<txn> ─ 复制 controller ─ 写 in-progress(stage=opened)─ 发布恢复分派入口(§4.2)  ← 此后任何宿主改动都有发现入口
─ [3] 取代码副本(app/ + app.sha256)─ venv 准备(§4.5)─ 挂点(§4.7)                       ← 只准备材料,不改在线服务
─ 目标上下文检查:路径探针(§4.7)─ requires-python(§4.5)─ 迁移计划 + 首装门(§4.8 / §4.1)─ DB 目标持久化(§4.8)
─ [4] 在 release 副本上构建前端 → dist/ + dist.sha256
─ [5] nginx 候选 → 写「首次宿主写入」intent → 记录受影响集合 → 落盘在线路径 → nginx -T/-t(失败按记录整体恢复)  ← 第一处宿主改动
─ [6] 切换序:db_snapshotted → db_migrated → taxonomy → pm2 delete(确认退出)
      → html_dir / current 切换 → pm2 start <release> → pm2 save → nginx reload
─ [7] 两级健康门 + 10 s 稳定窗 ─┬─ 通过:晋升 last-success ─ 更新 deploy-state/rollback ─ 清理(§4.12)
                                 └─ 不通过:告警(红字 + pm2 日志尾 + 精确回滚命令)─ 事务保留 ─ exit 1
```

## 4. 设计要点

### 4.1 身份与回滚点

- **代码身份 = `code_sha`**:tag / clean `--here` / `--code` = 该 commit;dirty `--here` = 固化快照 commit(§4.4)。manifest 分记
  `code_sha` / `head_sha` / `dirty` / `orchestrator_sha`(执行部署的脚本版本)。HEAD **永远不是**已部署身份。
- **prev 采样**(开事务前,只读):
  1. `last-success.target`,且与现场一致:`current` 指向其 release、后端 `/api/health` 的 `build.sha` = 其 `code_sha`
     (后端不可达时看 `pm2 jlist` 保存的 `DORAMI_BUILD_SHA` 与 cwd);
  2. 无 last-success(收养场景):由 `/api/health` 或 pm2 env 的构建 sha + 现场 symlink 目标得出,进收养流程(§4.11);
  3. **既有部署但证据冲突或缺失 → 默认停止(exit 24)**,保留 last-success 与材料;`--no-rollback-guarantee` 显式继续时
     `prev=null`,manifest `capabilities.rollback=false`,`--status` 持续显示。
  4. **停机例外**(实现检视 R1 P1-07 认可):pm2 查询**成功且有效列表里没有受管 app**、`/api/health` 不可达、`current` / `html_dir`
     与 last-success 一致、且 last-success 的 release 通过完整回滚材料门(代码 / dist 摘要、venv 凭据、nginx 快照、固化执行体)→ 保留
     last-success 为回滚点并明示「受管服务未运行,依据 last-success 及材料确认」;pm2 查询失败 / 坏 JSON、health 有响应但身份缺失、
     pm2 进程 cwd 或 sha 不符、材料不完整 → 仍是冲突(exit 24)。pm2 进程存在时无论身份来源都核 cwd,pm2 来源还核 sha。
- **首装门**:`plan.status=fresh`(库文件不存在)时,有既有部署证据(last-success / pm2 app / `html_dir` 非空 / `data/` 有媒体或播客目录 /
  `backups/` 非空)→ **一律拒绝(exit 23)**,这是数据目录配错,不提供覆盖;无证据的真首装才需要 `DORAMI_DEPLOY_FRESH_OK=1` 授权,
  `prev=null`、`capabilities.rollback=false`,文档写明首装无回滚点。
- 非 SQLite 数据库:同样默认停止,`--no-rollback-guarantee` 继续时 `capabilities.db_restore=false`。

### 4.2 锁与入口

- **锁先于一切**:`deploy.sh` 第一句 `acquire_deploy_lock`,再 `resolve_deploy_ref`(FD 随 exec 继承;与 Docker 路径同序)。
- 锁文件**与 Docker 路径同一把**:默认 `/run/lock/dorami-deploy.lock`,`DORAMI_DEPLOY_LOCK_FILE` 可覆盖;目录不存在或不可写
  **即失败并要求显式配置**,不做静默回退(两条路径若同机共用编排树 / DB,必须互斥)。`--rollback` / `--discard-txn` / `--adopt` 同锁,`--status` 不抢。
- 能力检查在 checkout **之前**:tag 模式 `git show <tag>:scripts/deploy-lib.sh | grep DORAMI_BAREMETAL_TXN`,目标无宣告 →
  **一律**拒绝(exit 11)并打印 `./deploy.sh --code <tag>`——不以本机是否已有裸机事务状态为前提,从新裸机入口首次调用同样在 checkout 前阻断;
  该检查是 `deploy.sh` 注入 `resolve_deploy_ref` 的钩子(`DORAMI_DEPLOY_PRE_EXEC_CHECK`),Docker 路径不设钩子、行为不变(§4.14)。
- 稳定恢复入口 `deploy-state/rollback` 是一段固定的**分派脚本**(内嵌绝对路径,不依赖调用时 cwd),在开事务时 controller 落盘之后、
  「首次宿主写入」intent 之前发布(adopt 事务同样发布):有 in-progress → 进入**该事务自己的** controller(adopt / rollback 续做,
  deploy 则回滚);无 in-progress → 进入 last-success 的 controller;两者都无(真首装 / 收养未开始)→ 报告状态并拒绝。
  **不以 last-success 存在为前提**;`./deploy.sh --rollback` 只是转发到它。

### 4.3 事务、阶段与固化执行体

- `txn_id = <UTC ts>-<code_sha7>-<4 hex 随机>`,`mkdir` 排他创建 `releases/<txn>/`,已存在即拒绝;ref / 描述串只进 manifest,不进任何文件名。
- manifest(原子写:同目录 tmp → fsync → rename → 父目录 fsync;与 worker 同法):

```json
{
  "txn_id": "20260921T073000Z-ab12cd3-9f3e", "kind": "deploy",              // deploy | rollback | adopt
  "mode": "here", "orchestrator_sha": "…",
  "target": {"ref": "v3.60.1-3-gab12cd3-dirty", "code_sha": "…", "head_sha": "…", "dirty": true,
             "release": "/abs/releases/<txn>", "venv": "/abs/venvs/<指纹>", "dist": "/abs/…/<txn>/dist"},
  "prev":   {"txn_id": "…", "ref": "v3.60.1", "code_sha": "…", "release": "…", "venv": "…", "dist": "…"},
  "db":     {"target": "/abs/data/cms_data.db", "snapshot": "/abs/backups/baremetal/<txn>/cms_data.sqlite",
             "snapshot_at": "…", "heads_before": ["…"], "plan": {"status": "compatible", "pending_count": 2}},
  "capabilities": {"rollback": true, "db_restore": true, "reproducible": true},
  "stage": {"completed": "links_switched", "intent": "process_started", "error": null},
  "recover_from": null, "opened_at": "…", "deployed_at": null
}
```

- **阶段**:`opened → code_archived → venv_ready → dist_built → nginx_prepared → db_snapshotted → db_migrated → process_stopped →
  links_switched → process_started → health_ok → promoted`。每步**先写 intent 再做、做完写 completed**;信号 trap 只补写
  `error`,SIGKILL / 断电靠「intent ≠ completed」被发现。每个副作用都定义重入判据(symlink 指向 / pm2 进程 cwd / 库文件身份 /
  nginx 文件内容 = 记录值)。
- **未收口事务纪律**:下次动作发现 in-progress——先按 `kind` 分派:adopt / rollback 一律**续做**(不归档);deploy 只在
  `completed` 与 `intent` **都早于**「首次宿主写入」intent **且**现场能证明未写入(`nginx/changes.json` 不存在或为空、`html_dir` /
  `current` / pm2 进程 cwd 等于 prev 记录)时自动归档 `closed/`,否则**拒绝(exit 20)**,提示 `--rollback`(续做或回滚)/ `--discard-txn`。
  同 txn_id 崩溃窗口(last-success 已写、in-progress 未删):先核对并修复分派入口的发布,再删 in-progress(覆盖「last-success 已写、
  入口尚未更新」的窗口)。
  晋升失败保留全部材料不清理;清理失败记 `maintenance_failed`,下次重试;`--status` 从阶段推导「中断于 X(intent Y)」。
- **固化执行体**:开事务时把当前 `scripts/deploy-lib.sh` 与 `deploy.sh` 中回滚所需函数导出为 `controller/rollback.sh`
  (bash 边读边执行的坑由此消失:回滚永远跑树外副本)。

### 4.4 代码副本与 dirty 固化

- `app/` = `git archive <code_sha>` 解包 + `app.sha256`(只覆盖归档文件;挂点按 symlink 目标身份记录,不进清单)。
- dirty `--here`:临时 index `add -A` **带独立于目标 `.gitignore` 的排除集合** = 固定项(`venv/ venvs/ releases/ deploy-state/ logs/ data/
  backups/ .venv/ frontend/node_modules/ frontend/dist/ *.db *.sqlite`)+ **由有效配置、环境覆盖与挂点映射生成的项**(`VENV_DIR`、
  `NGINX_HTML_DIR` / `NGINX_RELEASES_DIR` 若在仓内、`DORAMI_DEPLOY_STATE_DIR`、路径探针得出的共享根如 `state/`、`media-store`);
  归档前检查保留的源码路径与挂点是否冲突,冲突即拒绝 → `write-tree` →
  `commit-tree -p HEAD` → `update-ref refs/dorami-deploy/<txn>`;快照 blob 总量超阈值(默认 64 MiB)拒绝;`dirty = (tree ≠ HEAD tree)`
  (含未跟踪源码,不看 `git describe`);`DORAMI_BUILD_REF` 带 `-dirty` 后缀。pin ref 随 release 清理一起删(§4.12)。
- **前端构建与 venv 输入都取自这份快照**(在 `releases/<txn>/build/` 临时目录里 `npm install && npm run build`,产物移入 `dist/` 后删
  build 目录),不再从可继续编辑的工作树构建。

### 4.5 venv 版本化(按输入指纹)

- 指纹 = sha256(`docker/requirements.txt`)+ 所选 extras 各自钉版清单的 sha256 + 解释器身份(用**实际建 venv 的** python 跑
  `sys.version` + `realpath(sys.executable)`);`venvs/<指纹>/inputs.json` 记全部输入,复用前逐项比对 + `.dorami-complete` 标记;
  半成品在锁内删除重建(`kind=legacy` 的收养环境不适用此规则,永不当半成品删);直接在最终路径建(shebang 绑定)。
- **extras 钉版**:`docker/requirements-<extra>.txt`(`uv export --extra <extra>` 生成,入库;`tests/test_docker_requirements.py` 扩展守卫),
  `DORAMI_DEPLOY_EXTRAS=crawl4ai` 时按清单装——不再 `uv pip install -e ".[crawl4ai]"` 现解。
- **取消 editable 安装**:`pyproject` 无 console scripts,`src/version.py` 不依赖 `importlib.metadata`,运行时只靠 `PYTHONPATH=app/src`。
  由此**独立检查 `requires-python`**(目标 `pyproject.toml` 的声明 vs venv 解释器版本,预检阶段拒绝)。
- uv 从缓存链接装(Linux 默认 hardlink、macOS clone),同文件系统时增量小;空间预算仍按完整材料算。
- Playwright:`playwright install chromium` 时 `PLAYWRIGHT_SKIP_BROWSER_GC=1`(1.59.1 registry 已核 `index.js:1001`),浏览器保留改由
  我们按引用集合维护(`inputs.json` 记 playwright 版本);实现时对锁定版 1.59.0 实测;`PLAYWRIGHT_BROWSERS_PATH` 与平台默认路径写进文档;
  验收含「回滚后 OpenAI 渲染节点仍可用」。

### 4.6 dist 版本化与 nginx 变更集

- `html_dir` 变 symlink → `<dist>`;切换用 python `os.symlink(tmp) + os.rename`(同目录 tmp、sudo 归属、父目录 fsync);nginx `root` 不变。
- nginx:候选先写 `releases/<txn>/nginx/`;必须落到在线路径才能校验的项(站点文件、enabled 链接、default 站点删除、主配置 include 插入)
  在落之前把**完整受影响集合**(路径、原内容或「原不存在」)写入 `nginx/changes.json`;准备期任何失败(含 `nginx -T` 提前失败)按记录
  整体恢复;**回滚先按 `recover_from` 事务的完整 `changes.json` 撤销其受影响路径**(「原不存在」的新增项删除、被改写 / 删除项恢复——
  目标的快照里不可能预存 B 新增的站点文件与 enabled 链接),再与目标 release 的 `nginx/` 快照核对 → `nginx -t` → 之后 reload;
  回滚自身的 nginx 动作同样记录变更集与重入点。
- 保留 / 清理见 §4.12。

### 4.7 配置路径与共享挂点

`src/config.py` 把相对路径按 **`PROJECT_ROOT`(代码所在根)** 解析,不按 ini 文件位置——B 之下 `PROJECT_ROOT = releases/<txn>/app`。
- 预检阶段在**目标上下文**(目标 venv、cwd=`app/`、`PYTHONPATH=app/src`、`DORAMI_CONFIG_FILE` 绝对路径)跑只读路径探针,导出所有
  路径型配置的最终绝对值:`storage.database_url`、`media.media_dir`、`podcast_artifacts.root_dir`、`taxonomy.catalog_path`、
  `[backup]` / `[oss]` 本地目录等(探针清单随 config.py 演进,由测试守卫)。
- 规则:**可变存储**必须解析到 release 之外的共享位置——默认相对路径 `data/…` 由 `app/data -> <repo>/data` 挂点承接;探针发现其它
  相对根(如 `state/`、`media-store`)则同法建 `app/<component> -> <repo>/<component>`;解析结果须与**基准**逐项相等才放行,否则
  **切换前拒绝(exit 33)**;基准分三种:已有 release → **上次成功部署 / 收养时持久化的探针结果**(manifest `paths.mutable`,不用当前 ini
  重算——实现检视 R1 P1-02:重算会把运维改 ini 换库放过去);收养 → 原安装的运行上下文(cwd=`<repo>`、`PYTHONPATH=<repo>/src`、
  `<repo>/venv`)的探针,同样持久化;真首装 → 无基准,只要求可变存储在 release 之外。旧 manifest 缺 `paths` 时无法证明历史布局,
  不回退成重算后视为可信。**显式出口**(运维确要迁移存储,如换盘):`DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1` 且 `--no-rollback-guarantee`
  同用——本次记 `prev=null` / `capabilities.rollback=false` / `paths.rebased_from`,`--status` 明示「此次重设存储基准,没有跨存储布局的
  回滚保证」;新布局成为之后部署的基准;搬数据是运维自己的事,开关不绕过首装门 / release 外存储要求 / 迁移计划。
  探针在候选事务的代码、venv、挂点就绪之后、在线 nginx 写入与迁移之前运行(§3.3);「只准备材料」不等于「已修改在线服务」。
  随代码发布的资源(如内置 taxonomy catalog)绑定目标 release。
- DB 目标路径以目标上下文的探针值为准并持久化进 manifest,备份 / 计划 / 恢复共用同一个值(消除「两份库」)。

### 4.8 数据库:目标解析、快照、计划、恢复

- **目标解析**:SQLAlchemy `make_url`(与 `storage.migrations._sqlite_target` 同语义)在目标上下文解析;非 SQLite 见 §4.1。
- **快照**:`backups/baremetal/<txn>/<db>.sqlite`,在线 `.backup`,之后 `PRAGMA integrity_check`,记录真实时刻,manifest 落盘后才允许迁移 /
  覆盖。**任何可能执行迁移的切换(正向与回滚)都先快照。**
- **迁移计划自持但在目标上下文运行**:算法是执行体自带的 python 段(与 `plan_migrations` 同语义:fresh / legacy_adoption_required /
  compatible(+pending)/ incompatible / error,含业务表存在性检查),用目标 venv 的 python、cwd=`app/`、`PYTHONPATH=app/src`、
  `script_location=app/alembic` 运行——revision 文件顶层 import `sqlmodel` 与项目模块(10 个文件),必须有目标的导入上下文;
  库以 `?mode=ro&uri=true` 打开,不存在即报「缺失」不建;读图失败报 `error` 不下断言。`--status` 与 `--rollback` 共用。
- **分流**(回滚时,目标 = 被回滚到的 release):

| plan.status | 处置 |
|---|---|
| `compatible`,pending = 0 | 不覆盖库;仍先快照(救援) |
| `compatible`,pending > 0 | 放行,明示「回滚将向前补 N 个迁移」;先快照 |
| `incompatible` | **默认拒绝(exit 32)**,打印快照文件、时刻、现在、丢失窗口;`--restore-db` 才执行恢复 |
| `fresh` / `legacy_adoption_required` | 拒绝,打印原因(库缺失 / 老库形态 = 现场异常,人看) |
| `error` | **目标图读取失败**(`target_graph`)→ 拒绝;**当前库损坏**(`db_corrupt`:`SQLITE_CORRUPT` / `SQLITE_NOTADB` 或 `integrity_check` 明确非 ok)→ 允许 `--restore-db --no-rescue-snapshot` 的受控路径:恢复源已记录、校验有效且其 `alembic_version` 在目标图内,先确认写进程退出,允许理由 `db.skip_rescue_reason=db_corrupt` 落盘,`db_rescued` 阶段再核一次仍是 `db_corrupt` 才跳过;**权限 / 磁盘 / I/O / 无法归类**(`db_access`)→ 拒绝,不属契约例外;非 SQLite(`db.backend≠sqlite`)→ 不处置,`--restore-db` / `--no-rescue-snapshot` 明确拒绝 |

- `--no-rescue-snapshot` 只能与 `--restore-db` 同用,且只对 `db_corrupt` 生效;`compatible`(含 pending>0)/ `incompatible` 的健康库必须做救援快照(实现检视 R1 P1-04)。
- **恢复协议**(`--restore-db`,固定顺序、每步落盘):确认本部署管理的写进程已退出(pm2 delete + 进程消失)→ 救援快照**只创建一次**
  (路径进 manifest,重试不得覆盖)→ 校验恢复源(可打开、`integrity_check`、其 `alembic_version` 在目标图内)→ 同文件系统临时文件
  写入 + fsync → 记录「即将替换」→ 删 `-wal/-shm` → rename → 核对库身份(revision 集合)→ 允许启动。

### 4.9 切换序与两级健康门

切换序(点 of no return 起于 `db_snapshotted` 之后):`db_migrated` → taxonomy reconcile → `pm2 delete <app>` 并轮询确认退出
(`process_stopped`)→ 切 `html_dir` / `current`(`links_switched`)→ `pm2 start <release>/app/ecosystem.config.js --update-env`
(导出 `DORAMI_BUILD_REF/SHA`、`DORAMI_CONFIG_FILE` 绝对路径)→ **`pm2 save`**(失败 = 阶段失败 → 告警;开机 resurrect 必须指向已确认的 release)
→ `nginx -s reload` → 健康门。停机时长 = 停止 + 应用就绪(启动仍跑迁移核对、taxonomy、lifespan 存储对账),不承诺固定秒数。

健康门:
1. **后端身份门**:探 `http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health` 五项(`status=ok` / `version` / `build.ref` /
   `build.sha` / `build.source=env`),判定段与 Docker 路径共用(`deploy_health_verdict` / `deploy_wait_healthy`,默认预算由调用方传入,
   Docker 保持 180 s / 90 次);
2. **站点链路门**:经 nginx 取 `/index.html`(200、含本 release dist 的 asset 引用)+ **index 引用的主 JS / CSS**(200、Content-Type
   为脚本 / 样式而非 HTML、sha256 = dist 内文件)+ `/api/health`(与①同值)。探针主机:`server_name` 首个非 `_` 名字,`_` 则
   `127.0.0.1` 不带 Host;TLS 用 `curl --resolve <name>:<ssl_port>:127.0.0.1`,系统 CA 或 `DORAMI_DEPLOY_PROBE_CACERT`,不提供 `-k`;
   `ssl_redirect` 时 HTTP 入口期望 301 且 `Location` 指向 https 入口。
3. 五项一致后**连续观察 10 s、PID 不变**才算通过,计入总预算(裸机默认 180 s)。
4. **任一门失败 = 部署失败**:stderr 红色横幅「部署 <ref>(<sha7>)健康核对未通过:<原因>。系统已切换到该版本且未自动回滚。
   回滚:./deploy.sh --rollback」+ `pm2 describe` + `pm2 logs --nostream --lines 50`;in-progress 保留;exit 1。
5. 通过 → 晋升:in-progress + `deployed_at` → `last-success.json`;更新 `deploy-state/rollback`;清理(§4.12)。

### 4.10 `--rollback`

- 目标选择(`select_rollback_target`,与 `--status` 共用):① 存在未完成的 `kind=rollback` 事务 → **续做同一 target**(按阶段重入,永不翻转);
  ② 存在已持久化「首次宿主写入」intent、或现场无法证明未写入的 in-progress(失败部署)→ 目标 = 其 `prev`,`recover_from` = 该事务;③ 否则 `last-success.prev`;
  ④ last-success 本身是 rollback 的结果 → 默认拒绝,打印「上一版是刚被回滚掉的 <ref>;要回去请 `./deploy.sh --code <code_sha>`
  或 `--rollback --to <recover_from txn>`」——`--to` 只接受相邻的那个事务,不扩成多代。都没有 → exit 30,打印 `--code` 命令。
- 前置(只读):目标材料完整(`app.sha256` / `dist.sha256` 核对、venv `.dorami-complete`(`kind=fingerprint` 或 `legacy`)+ inputs 一致、`nginx/` 快照存在);
  迁移计划(§4.8;当前库不可读时按 §4.8 `error` 行的受控例外);打印摘要(从 → 到、DB 处置、将执行的动作);交互式要求输入 `yes`,`--yes` 跳过,非交互无 `--yes` → exit 2。
- 开回滚事务(`kind=rollback`、固定 `target` / `recover_from`、controller = 当前入口所在副本、快照路径)→ 阶段:撤销 `recover_from` 的 nginx 变更集 → 恢复目标 nginx 集合 + `nginx -t`
  → `pm2 delete` 确认退出 → 救援快照(一次)→(需要时)恢复库 → 切 `html_dir` / `current` → `pm2 start <target release>` → `pm2 save`
  → `nginx reload` → 两级健康门(期望 = target 身份)→ 晋升(last-success = target,`kind=rollback`,`prev` = 被回滚掉的那版)。
- 失败 → 告警 + 事务保留 + exit 1;再次 `--rollback` 续做同一目标;人工介入用 `pm2 logs` / `--status`。
- 回滚不 checkout、不要求工作树 clean;所有打印的命令按模式给**精确形式**(内网 `--here` 场景一律给 `--code <sha>`,不给 tag 名)。

### 4.11 收养(旧形态安装 → release 形态)

`kind=adopt` 事务,锁内、可重入、每步阶段落盘;由首次运行新脚本时自动触发(无 last-success 且有既有部署证据),或 `--adopt` 手动。
完成判据:**旧服务已从 legacy release 启动并过两级健康门**——在此之前不进入任何正向部署;收养中断由分派入口按 `kind=adopt` 续做,
**不**交给普通 deploy 的自动归档分支。整个收养排在 tag 模式 checkout **之前**(§3.3)。

1. 身份:运行中 sha 取自 `/api/health` / pm2 env;取不到 → 拒绝,`--adopt-sha <sha>` 由人指定,manifest 记 `adopt_sha_source=operator`
   且 `capabilities.reproducible=false`;运行 ref 带 `-dirty` 时同样标不可复现(旧脚本没保存 dirty 字节)。
2. `releases/legacy-<txn>/app` = `git archive <sha>`;**venv 不移动**:`app/venv -> <repo>/venv`(绝对路径),并从该 venv **移除 editable
   finder**(`__editable__*.pth` / 对应 dist-info),在 legacy 上下文核对 `sys.path` 不含 `<repo>/src`;核对通过后写 `inputs.json` 与 **`.dorami-complete`(`kind=legacy` + inputs 摘要)**——
   与 §4.10 回滚材料门同一凭据,不参与指纹复用、永不当半成品删。
3. dist:**复制** `html_dir/*` → legacy dist,校验文件数与字节;`mv html_dir html_dir.adopt-<txn>` + 建 symlink(两步,阶段记录保证断电后补建);
   跨文件系统只复制不 mv;先检查设备 / 权限 / 空间。
4. nginx:把当前生效的站点配置集合快照进 `legacy/nginx/`(作为日后回滚到 legacy 的恢复源)。
5. **重启到 legacy release**:`pm2 delete` → `pm2 start legacy/app/ecosystem.config.js` → `pm2 save` → 两级健康门 → 持久化
   `current -> legacy/app` → 核对 pm2 进程 cwd = legacy app → 发布分派入口(§4.2)→ 写 last-success(`kind=adopt`,`prev=null`)。
   这是唯一的维护窗(一次重启);晋升前 legacy release 已具备回滚门所需的代码 / dist / nginx 清单与身份记录。

### 4.12 清理与引用集合

- 引用根(取 realpath):last-success 与 in-progress 的 `target` / `prev` / `recover_from`;未完成回滚的源与救援材料;`deploy-state/rollback`
  所指的 controller 所在 release;`current` / `html_dir` 当前指向;**pm2 运行进程的 cwd 与 `dump.pm2` 记录的 cwd**;`closed/` 内
  保留期(默认 7 天)内事务的引用。
- 只对引用集合之外的 release / venv / 快照按数量清(release 3、venv 2、快照 10;按 manifest 时间排序,不看 mtime);pin ref
  `refs/dorami-deploy/<txn>` 随 release 一起删;清理失败非致命。失败的 venv 构建立即删目录。

### 4.13 `--status` / `--discard-txn`;退出码

- `--status`:HEAD(ref / sha / dirty)与「已部署身份」分开显示;`current` / `html_dir` 指向;last-success(kind / target / prev / deployed_at);
  in-progress(阶段 completed / intent / error,标红);`/api/health` 实时读数与是否与 last-success 一致;回滚目标(与 `--rollback` 同规则)
  与 DB 预判(同一计划段,只读);能力位;pm2 进程 cwd 是否等于 `current`。
- `--discard-txn`:抢锁,把 in-progress 归档为 `closed/<txn>.json`;不删材料;若已持久化「首次宿主写入」intent 或现场无法证明未写入,要求 `--yes`。
- 退出码(沿 worker 编号,不重号):`0` 成功 / `1` 步骤失败或健康门未通过 / `2` 用法 / `4` 锁被占 / `11` 目标脚本无裸机事务能力 /
  `20` 已改宿主未收口事务阻断 / `23` 首装门 / `24` 身份证据冲突或收养未完成 / `30` 无回滚点或材料缺失 / `32` 需 `--restore-db` /
  `33` 路径探针不一致。

### 4.14 与 Docker 路径的共用与隔离

- 抽到 `scripts/deploy-lib.sh` 共用:健康判定与等待(`deploy_health_verdict` / `deploy_wait_healthy`,预算由调用方传)、原子写、原子 symlink、
  DB 目标解析与快照(带后缀参数)、`_deploy_lib_referenced_backups`(分别读 worker 与裸机两处,缺哪个跳哪个,不早退)。
- 裸机专属(参数 `--rollback/--status/--discard-txn/--adopt/--code/--restore-db/--to/--yes`、能力钩子、事务函数)只在 `deploy.sh` 装配;
  `resolve_deploy_ref` 对未知参数的拒绝行为在 Docker 路径不变;`DORAMI_DEPLOY_PROTOCOL` 不 bump;`test_deploy_docker_*` 全绿是等价护栏,
  另加「默认预算仍 180 s / 裸机参数在 deploy-docker.sh 仍被拒绝」两条守卫。
- 裸机快照目录 `backups/baremetal/` 不落入 worker 的 `backups/<db>.*` 通配;两条路径同机共用编排树 / DB 时靠同一把锁互斥。

### 4.15 文档

`docs/deploy-baremetal.md`(形态节改 release 布局;用法加五个入口;新节「回滚」「收养」「恢复能力位」;护栏清单加锁 / 事务 / 两级健康门 /
nginx 变更集;`pm2 save` 改为脚本执行)、`docs/release-process.md` 回滚节裸机段改引用、`CLAUDE.md` *Production deploy* 一句 + 年表一行、
`config/production.example.ini`(`html_dir` 将成 symlink;路径型配置须指向共享位置)、`.gitignore`、`docs/README.md`、`docs/backlog.md`、
`docs/version-history.md`。INTRANET_DELTA 不动(main 文件随 tag 同步)。

## 5. 落地切分

一条 PR(`feat/issue-126-baremetal-rollback`),提交按依赖分层:
1. deploy-lib 抽共用 + `deploy-docker.sh` 等价重构 + 两条等价守卫;
2. 锁前置 + 事务 / 阶段 / 固化执行体 + 能力钩子 + 首装门 + 身份采样(此时仍旧形态发布,已能「失败大声说」);
3. release 形态:代码副本 / dirty 固化 / venv 指纹与 extras 钉版 / 挂点与路径探针 / dist / nginx 变更集 / pm2 起法;
4. 收养;
5. `--rollback` / `--status` / `--discard-txn` / `--code` + DB 协议;
6. 桩测试(§6 矩阵)+ 文档。

## 6. 验收

**桩测试**(新 `tests/test_deploy_baremetal.py`;真 git 临时仓 + PATH 桩 `pm2 / nginx / npm / uv / node / sudo / curl`;真 symlink / rename /
SQLite / Alembic 不桩;`FAKE_HEALTH_FILE` 喂读数但期望值必须来自实际选中目标;**整脚本级用例只在 Linux 跑**(GNU `stat -c` / `sed -i` /
`sort -V`),macOS 只跑函数级;sudo 桩不得透传到真实系统路径,html / site / main-conf / releases / lock / data 全部隔离):

1. **身份**:A 运行、HEAD 已是 B(tag 与 `--here` 两模式)必须采到 A;无 last-success 的收养;手改链接;历史 dirty;`--adopt-sha`;证据冲突默认停止 /
   `--no-rollback-guarantee` 继续且能力位持续显示;首装门(有证据 + fresh 拒绝;无证据需 FRESH_OK)。
2. **中断矩阵**:在 checkout(编排树)、nginx 落盘、每个链接切换、pm2 delete、救援快照、库替换、pm2 start、晋升 rename 前后、删 in-progress 前、
   清理中注入失败 / 进程终止;下一次 `--status` 描述实际阶段,`--rollback` 续做同一目标;材料仍可达;adopt 中断后由入口续做而非归档;
   `completed=dist_built, intent=nginx_prepared` 且站点文件已写一半的事务被判「已改宿主」而非自动归档。
3. **DB 矩阵**:compatible / pending>0 / incompatible / fresh / legacy / error;A(H1)→B(H2)→A→B→A 链;损坏 / 缺失快照;救援快照只创建一次;
   `--no-rescue-snapshot` 仅在库不可读时生效;用小型真实 SQLite + Alembic 图验证 revision 与数据。
4. **材料寿命**:A→B→A→C 后回滚;KEEP 边界;失败安装再试;extras 开关;自定义状态目录;closed 保留期;pm2 dump 引用;`--code` 部署被回滚掉的版本;
   pin ref 随 release 删除;清理失败不删 target。
5. **副作用隔离**:构建期失败后 release 目录外零改动;nginx 变更集在 `-T` 提前失败 / `-t` 失败 / 后续预检失败时整体恢复;root / non-root;
   源码装 nginx 路径;同 / 跨文件系统收养失败;**B 新增站点文件 / enabled 链接并删除 default 站点后失败,回滚 A 后新增项消失、default 恢复**(真实文件)。
6. **Docker 等价**:既有真实脚本用例全绿;默认预算仍 180 s;裸机参数在 `deploy-docker.sh` 仍拒绝;无 worker conf 也能读裸机引用;两处清理互不越界。
7. **旧脚本 fixture**:固化本波之前的 `deploy.sh` / `deploy-lib.sh`,验证 tag 模式对无能力目标拒绝且 HEAD 不变、`--code` 能部署它。

**实机验收**(内网或本机 PM2 + nginx):记录 PM2 版本;收养一次(一次重启)→ 部署新版 → 两级门通过 → `--here` 部署一个故意起不来的提交
(如 `src/main.py` 顶部 `sys.exit(1)`)→ 健康门失败告警 → `--rollback` → 站点与 `/api/health` 回到旧版、`--status` 一致 → 机器重启后
resurrect 起的是当前 release → 部署带迁移的版本 → `--rollback` 被拒并打印窗口 → `--restore-db` 成功且数据回到快照点 → OpenAI 渲染节点仍可用。

## 7. 待拍板项

| # | 决策 | 推荐 | 理由 |
|---|---|---|---|
| 1 | **形状 B(运行副本版本化)** vs R1 的「工作树 + symlink」 | **B** | R1 的 14 条 P1 里 8 条源于运行时绑在工作树上;B 让「不出网、不构建、不 checkout 即回滚」成为可证明的性质;代价是实现面与一次收养重启 |
| 2 | 有迁移时 `--rollback` 默认拒绝 + `--restore-db` | **默认拒绝** | 丢失窗口来自实际所选事务的快照时刻;确认前不改现场 |
| 3 | 只回一代;nginx 恢复记录在案的旧集合(不重跑旧生成器) | **接受** | 多代回滚用 `--code` 正向表达;配置集合快照比重跑更可证 |
| 4 | 迁移在切换序、快照在其前 | **接受** | 切换前只写 release / venv / 事务目录与已记录的 nginx 变更集 |
| 5 | 一条 PR | **一条** | 六层提交便于检视;仅健康告警不兑现「立刻回撤」 |
| 6 | extras 钉版导出 `docker/requirements-<extra>.txt`(新增维护项) | **做** | 指纹要能描述 extra 的锁内容;现解安装会漂 |
| 7 | 锁默认 `/run/lock/dorami-deploy.lock` 与 Docker 同一把,不可写即失败要求显式配置 | **做** | 两条路径同机时必须互斥;静默回退会造出两把锁 |

## 8. 检视记录

### R1(2026-09-21,codex gpt-6-astra ultra,设计检视先于实现)

首轮 25 条(P1×14 / P2×8 / P3×3)**全部成立,无不成立项**;我方表态为「全盘接受 + 结构性改形 B」,并实测五点
(symlink 目录 venv 的 `sys.prefix`、`rename` 替换 symlink、dirty 树固化为 commit、`PLAYWRIGHT_SKIP_BROWSER_GC`、无 console scripts)。
codex 对改形 B 的答复 13 条(风险 A1–A5 / 不够 B1–B4 / 矛盾 C1–C4)亦全部采纳。对应关系:

| finding | 结论落点 |
|---|---|
| P1-01 HEAD 误采为 prev;C4 证据冲突默认继续 | §4.1:HEAD 永不是身份;冲突默认停止,显式 `--no-rollback-guarantee` + 能力位 |
| P1-02 先 resolve 后锁;C2 两把锁 / 共用 guard | §4.2:锁第一句;与 Docker 同一把、不可写即失败;能力检查经钩子只在 deploy.sh |
| P1-03 回滚依赖新代码入口;A5 迁移图要目标导入上下文 | §4.2 / §4.3 / §4.8:树外固化执行体;计划算法自持但在目标上下文运行 |
| P1-04 checkout 先于事务;A1 收养未脱离工作树 | §3.1 / §4.11:运行副本化,回滚不 checkout;收养以「从 legacy release 启动并过门」为完成判据 |
| P1-05 回滚重入翻转;B1 阶段粒度与恢复协议 | §4.3 / §4.8 / §4.10:completed / intent / error 三字段;续做同一 target;恢复协议固定顺序、救援快照只一次 |
| P1-06 补迁移不备份 | §4.8:任何可能迁移的切换先快照 |
| P1-07 清理删当前;B2 引用根漏项 | §4.12:引用集合(含入口 controller、pm2 进程与 dump、pin ref) |
| P1-08 换 symlink 目标污染导入 | §3.1:release 内 venv symlink 不可变,pm2 用实路径 |
| P1-09 收养非原子 | §4.11:adopt 事务、venv 不移动、dist 复制、阶段可重入 |
| P1-10 dirty 不可复现;A3 排除规则与构建输入 | §4.4:独立排除规则、体积阈值、`dirty = tree≠HEAD`、构建取自快照 |
| P1-11 首装门 | §4.1:有证据 + fresh 一律拒绝;FRESH_OK 只授权真首装 |
| P1-12 nginx 变更在事务外;C3 时序 | §3.3 / §4.6:事务先开、变更集先记后写、失败整体恢复 |
| P1-13 后端门不证明站点;B4 资产可取 | §4.9:两级门,主资产内容摘要核对 |
| P1-14 status 另一套规则 | §4.8 / §4.13:共用目标选择与计划段,只读打开 |
| P2-01 收口语义 | §4.3 |
| P2-02 / B3 指纹与 extras / requires-python | §4.5:extras 钉版导出;取消 editable;独立检查 requires-python |
| P2-03 半成品 venv | §4.5 |
| P2-04 旧脚本破坏;C1 兜底命令矛盾 | §3.3 / §4.2 / §4.10:能力检查阻断;`--code` 作为稳定正向入口;打印精确命令 |
| P2-05 文件名与 txn_id | §4.3 |
| P2-06 DB 目标解析;A2 相对路径按代码根 | §4.7 / §4.8:目标上下文路径探针,持久化单一 DB 目标 |
| P2-07 worker 清理裸机备份 | §4.14:`backups/baremetal/` |
| P2-08 Playwright GC | §4.5 |
| P3-01 / 02 / 03;A4 pm2 save 与停机时长 | §1 / §4.5 / §4.9:数字口径、平台条件、`pm2 save` 入切换序、稳定窗 |
| §7 五项 | 1 / 2 / 4 / 5 赞成;3 拆开采纳(只回一代 + 恢复记录集合) |

### R2 复检(同日,对照 38 条清单)

22 条已落实;16 条归结为 7 处形状缺口 + 1 条 R2 新引入 P1,**全部采纳**,落点如下(P2 / P3 无新增;§7 七项 codex 判断与推荐一致):

| 复检项 | 落点 |
|---|---|
| R-01 收养未排到 checkout 前;能力检查带前提;收养完成态未对齐身份门 | §3.3 流程重排(锁 → 只读解析 + 能力检查 → 收养 → checkout);§4.2 去掉「本机已有事务状态」前提;§4.11 完成序补 `current` / pm2 cwd / 入口 |
| R-02 用 completed 判「未改宿主」 | §4.3:以「首次宿主写入」intent + 现场证明为边界,adopt / rollback 先续做;§4.10 ②、§4.13 `--yes` 同边界 |
| R-03 首次中断无稳定入口 | §4.2:入口改为分派脚本,开事务即发布,不以 last-success 为前提;同 txn 收口先修入口再删 in-progress |
| R-04 目标上下文检查排在材料之前;基准缺三种 | §3.3:检查移到代码 / venv / 挂点就绪之后、在线写入之前;§4.7 基准三种 |
| R-05 legacy venv 无完成凭据 | §4.5 / §4.10 / §4.11:`.dorami-complete`(`kind=legacy`)与统一门同凭据 |
| R-06 当前库损坏时例外不可达 | §4.8 `error` 行拆两类,受控路径明确 |
| R-07 dirty 排除表不含自定义位置 | §4.4:排除集合由配置 / 环境 / 挂点映射生成 + 冲突检查 |
| N-P1-01 回滚只恢复目标快照撤不掉新增配置 | §4.6 / §4.10:先撤销 `recover_from` 的变更集再核对目标快照;§6.5 补真实文件用例 |

### R3 终审(同日,只核上表 8 条)

codex 逐条判「已落实」(落点行号见 `.review/recheck-codex-r2.md`),**未发现新引入 P1,限定范围终审通过**。
四轮全程:R1 全面检视(25 条)→ 改形 B 答复(13 条)→ R2 对照复检(8 条)→ R3 终审(0 条);全部采纳、无分歧遗留。
方案自此进入**待用户拍板 §7 七项**状态;拍板后按 §5 六层提交开实现分支,实现后另起代码检视。

### 实现检视 R1(2026-09-21,codex gpt-6-astra ultra,对象 PR #129 代码;`.review/report-codex-impl-r1.md`)

首轮 14 条(P1×7 / P2×6 / P3×1)**全部成立**;我方表态(`.review/response-impl-r1.md`)12 条接受、P1-07 一处分歧、P1-02 一点请其拍板,codex 答复
(`.review/reply-codex-impl-r1.md`)接受分歧处的替代判据并给出三条修法约束,一致后成批修复。落点:

| finding | 结论落点 |
|---|---|
| P1-01 收养漏动态挂点与原上下文基准 | §4.7 / §4.11:收养 `venv_ready` 末尾在原安装上下文探针作基准,对 legacy 跑路径检查(补挂点、逐项相等,维护窗之前),`paths` 与 `db.target` 持久化 |
| P1-02 基准用当前 ini 重算 + 跳过缺挂点键能放过换库 | §4.7:基准 = 持久化 `paths.mutable`,不重算、不跳过;缺字段即停止;显式出口 `DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1` 须与 `--no-rollback-guarantee` 同用(codex 拍板:跨存储布局没有回滚保证,`prev=null`) |
| P1-03 失败部署 → 回滚事务交接窗口 | §4.3 / §4.10:先复制 closed 副本(幂等、不动 in-progress),回滚 manifest 再原子覆盖 in-progress;任一中断点可重选 |
| P1-04 健康库 / 权限错误能跳过救援 | §4.8:`db_corrupt` / `db_access` 分类(sqlite 原始错误码 / integrity_check),`--no-rescue-snapshot` 只与 `--restore-db` 同用且只对损坏库,执行阶段再核 |
| P1-05 快照清理不保护回滚事务引用 | §4.12:快照引用集合独立计算(全部事务 manifest 的 `db.snapshot` / `rescue_snapshot` / `restore_source`) |
| P1-06 树外入口不能续做已停机的收养 | §4.2:controller 按 in-progress 的 kind 分派,`adopt` 由固化库在 controller 上下文续做(站点 / 配置 / venv 取自 manifest) |
| P1-07 pm2 SHA 相符掩盖 cwd 冲突;无证据直接采纳 | §4.1 第 4 条:pm2 存在一律核 cwd(+pm2 来源核 sha);停机例外以 pm2 有效空列表 + 材料门代替运行身份(我方替代判据,codex 接受并收口条件) |
| P2-01 dirty 排除不独立于 `.gitignore`、缺动态根 | §4.4:排除集合由 deploy.sh 按 ini + 与 config.py 同语义的环境覆盖生成(`DORAMI_PODCAST_ARTIFACT_ROOT_DIR` / `DORAMI_BACKUP_LOCAL_DIR` / `NGINX_RELEASES_DIR`),临时 index 里显式移除目录项与数据库通配项;指进已入库源码路径的不排除、交挂点冲突检查拒绝 |
| P2-02 三个存储根退出 33 | §4.7:有进展就继续、最后一轮重新探测 |
| P2-03 非 SQLite 显式继续不可达 | §4.1 / §4.8:`db.target=null` + `db.backend` + `db.url_summary`(脱敏),按后端分流;正向跳过计划 / 快照,迁移照常;回滚不处置,`--restore-db` 明确拒绝 |
| P2-04 `--to` 提示指向未晋升事务 | §4.10:未晋升(无 `manifest.json`)只给 `--code`;材料门文案「未曾晋升」 |
| P2-05 HTTPS 跳转只看协议 | §4.9:Location 的 origin 与路径须等于本站 HTTPS 入口;生成器非 443 端口带端口 |
| P2-06 站点门与稳定窗不计入预算 | §4.9:三道门一个 deadline,站点探针 `--max-time` 取剩余,剩余不足稳定窗即失败 |
| P3-01 未完成回滚不自动续做 | §4.3:正向入口在同一把锁内经稳定入口续做(`--rollback --yes`,已由人确认过),成功后重新采样继续 |

codex 沙箱禁 `/bin/ps` 导致 `test_deploy_scripts.py` 31 例失败,与实现无关(本机全绿,CI 待跑);其对测试覆盖表述的意见按实修订 §9。

**复检 R1**(`.review/recheck-codex-impl-r1.md`):14 条里 10 条判「已落实」,4 条残留 + 2 条新观察,全部接受并修:
P1-02 残留(基准装配曾受 prev 非空限制——身份冲突用 `--no-rollback-guarantee` 放行就绕过了双开关;缺字段被 continue)→ 基准只取决于
last-success 是否存在,`mutable` 缺字段 / 缺 `database` = 无法确认历史布局(33),显式空串才是已知未启用,另核 `last-success.db.target`;
P1-07 残留(有 HTTP 响应但非 JSON / 空 / 4xx-5xx 被当成不可达)→ 采样不带 `-f`,分记 `BM_HEALTH_HTTP`(响应码 / none)与身份,有响应无身份一律冲突;
P2-01 残留(工作树内的绝对存储根被跳过;只匹配 `.db-wal/.db-shm`)→ 绝对路径归一化后判包含关系,通配统一 `*.db *.sqlite *.sqlite3 *-wal *-shm *-journal`;
P2-06 残留(入窗后不受 deadline 约束)→ 每轮 sleep / `--max-time` 取剩余、成功前再核余额;
新观察 P2(重核失败的提示不可执行)→ 续做时不带 `--no-rescue-snapshot` 即改为做救援快照(只能收紧),提示改成可执行命令;
新观察 P3(§9 的 pending>0 覆盖表述)→ 补真实回滚 `migrate` 分支用例(`--to` 前进到目标比库新的 release)。
另修:开事务记录现场 `scene`(current / html_dir / pm2 cwd),「未改宿主」证明与 scene 比对,prev=null(首装 / 放弃保证)也能证明。

**复检 2**(`.review/recheck2-codex-impl-r1.md`,只对照上面 6 条 + 附带修复):**通过**——4 条残留、2 条观察、附带修复(scene / 未晋升证据不收养 /
快照 commit 固定身份 / 两个守卫测试)全部判「已落实」,无 P1;codex 在固定提交上重跑裸机 52 例全绿,另做 26 组路径基准 / HTTP 身份定向补验。
新增 1 条非阻断 P2(接受并修于 9418e81,codex 只核该处 diff 一行「确认」,不再开轮):续做时去掉 `--no-rescue-snapshot` 的收紧逻辑没核 `db_rescued` 是否已完成——救援阶段已按跳过决策
结束(例如中断在 `pm2 save`)再续做,会把 `db.no_rescue` 改成 false、清掉理由并宣称「先做救援快照」,但阶段重入实际跳过,记录不再反映已发生的动作。
修法按其建议:只在救援阶段尚未完成时改写决策;阶段已结束的保留当时的跳过记录与理由、提示「已按当时的跳过决策结束,不补做」,不回退阶段补造救援。
用例拆成三条:救援前中断(`pm2 delete` 失败)→ 库修好后续做真实创建救援快照;救援阶段已结束 → 记录原样、无快照;反向事后加跳过 → exit 2。

## 9. 实现记录(2026-09-21,分支 `feat/issue-126-baremetal-rollback`)

§7 七项全按推荐拍板后,按 §5 六层提交实现(Claude Code 实现)。落点:`deploy.sh`(裸机专属参数与流程)、`scripts/deploy-baremetal.sh`
(事务 / release / 收养 / 回滚库,随 release 固化进 `controller/`)、`scripts/deploy-lib.sh`(共用:健康核对、JSON 原子写、原子 symlink、
SQLite 快照、checkout 前钩子、`DORAMI_BAREMETAL_TXN` 宣告)、`deploy-docker.sh`(只改调共用健康轮询,默认预算与输出不变)、
`docker/requirements-crawl4ai.txt`、`.gitignore`、`config/production.example.ini`、`docs/deploy-baremetal.md`、
`tests/test_deploy_baremetal.py`(+ `tests/fixtures/deploy_scripts_pre_issue_126/`)、`tests/test_docker_requirements.py`、
`tests/test_deploy_scripts.py`(两条 Docker 等价守卫)。

**与方案的偏差 / 细化**(检视时请对照):

| 项 | 方案 | 实现 |
|---|---|---|
| controller 内容 | `controller/{deploy-lib.sh,rollback.sh}` | `controller/{deploy-lib.sh,deploy-baremetal.sh,rollback.sh,env.sh}`:执行体是三份原样副本 + 入口 + 开事务时的环境(仓库 / 状态目录 / 锁 / PATH),不做函数导出拼接 |
| rollback 事务的材料 | 未定 | 无新 release;`deploy-state/txns/<txn>/{nginx/changes.json,manifest.json}`;晋升时不覆盖目标 release 的原 `manifest.json` |
| 失败部署的 in-progress | 回滚以它为 recover_from | 原子交接:先把 manifest **复制**到 `closed/`(幂等、不动 in-progress),回滚事务 manifest 再原子覆盖 in-progress(材料仍在 `releases/<txn>`),recover_from 按 txn id 解析材料目录 |
| 收养事务的首次宿主写入 | 未定 | `process_stopped`(pm2 delete);adopt 事务永不自动归档,只影响 `--discard-txn` 是否要 `--yes`;固化 controller 也能续做收养 |
| 首装门的证据 | 列表 | 在脚本建任何目录 / 开事务**之前**采样一次(自己建的 `data/podcast-artifacts`、事务目录不能当证据);媒体 / 播客目录须非空;`releases/` 只有含 `manifest.json` 的(已晋升)算;证据只来自 release 形态自己(首装事务失败后 `--discard-txn`,`current` / `html_dir` 已是 symlink 而无仓库内 venv)时不进收养,按首装候选继续 |
| 路径探针基准 | 逐项相等 | 基准 = 持久化的 `paths.mutable`(deploy / adopt / rollback 事务都带),不重算、不跳过;显式重设基准 `DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1` + `--no-rollback-guarantee`(§4.7) |
| 停机时的 prev | 缺失 → 停止 | §4.1 第 4 条停机例外(pm2 有效空列表 + health 无任何 HTTP 响应 + 材料门);health 有响应(任何状态码 / 非 JSON)而无有效身份 = 冲突 |
| 「未改宿主」的现场证明 | 等于 prev 记录 | 等于开事务时记录的 `scene`(current / html_dir / pm2 cwd),prev=null 也能证明;旧 manifest 无 scene 才退回 prev |
| dist 位置 | `dist/`(或 `NGINX_RELEASES_DIR/<txn>`) | 两者都实现:`[nginx] releases_dir` / `NGINX_RELEASES_DIR` 非空时 dist 复制到宿主目录(sudo 归属、校验清单),清理时同删;穿越位补不上只告警并提示该项 |
| 迁移计划自持段 | 与 `plan_migrations` 同语义 | 同语义,另加 `PRAGMA integrity_check` 与 `error_kind`(target_graph / db_corrupt / db_access / not_sqlite)供分流表 `error` 行区分;legacy 的 pending 计整链 |
| 非 SQLite | `capabilities.db_restore=false` | 另记 `db.target=null` / `db.backend` / `db.url_summary`(脱敏);正向跳过计划 / 快照,迁移 / taxonomy 照常;回滚不处置库 |
| 未完成的回滚在正向入口 | 一律续做 | 同(经稳定入口 `--rollback --yes`,同一把锁 FD 继承),成功后重新采样继续正向流程 |
| `--to` 提示 | 相邻事务 | 只有晋升过的相邻事务才提示 `--to`;失败过的部署只给 `--code` |
| HTTPS 跳转 | Location 指向 https | Location 的 origin + 路径 == 本站 HTTPS 入口;生成器非 443 端口带端口 |
| 健康预算 | 计入总预算 | 三道门一个 deadline:站点探针每个请求 `--max-time = min(20, 剩余)`,剩余不足稳定窗即失败 |
| 快照清理 | 引用集合 | 快照引用集合独立于 release 引用(所有事务 manifest 的 db 字段,含 `txns/` 与 `closed/` 保留期内) |
| 阶段 `db_migrated` | 切换序内、快照后 | 同;迁移与 taxonomy reconcile 在目标上下文(目标 venv + cwd=app + PYTHONPATH=app/src)执行,旧进程仍在跑(与旧脚本时序一致) |
| 健康门 ② 的资产选择 | index 引用的主 JS / CSS | 第一个 `type=module` 脚本(无则第一个脚本)+ 第一个 stylesheet;外链资产跳过 |
| 退出码 | §4.13 | 同;另 `--discard-txn` 需 `--yes` 与非交互 `--rollback` 缺 `--yes` 都是 2 |
| 收养前提 | 未写 | 运行中的代码须透出构建身份(`/api/health` `build.*`,v3.56+),否则健康门过不了;身份取不到时 `--adopt-sha` |
| dirty 固化的排除集合 | 固定项 + 生成项 | 固定项另含 `config/production.ini` / `config/backend.ini` / `.env`;生成项由 deploy.sh 按 ini + 环境覆盖算出(库目录 / 媒体 / 播客产物 / 备份目录 / `NGINX_RELEASES_DIR`);临时 excludes 文件只挡未跟踪文件,目录项与 `*.db` `*.sqlite` `*-wal` `*-shm` 在临时 index 里显式移除(`!` 否定与已跟踪都挡不住);指进已入库源码路径的项不排除,交挂点冲突检查拒绝 |
| `--status` | §4.13 | 同;回滚预判段与 `--rollback` 共用目标选择、材料门与计划段(只读) |

**验收矩阵覆盖**(`tests/test_deploy_baremetal.py`,macOS / Linux 都跑——脚本刻意不用 GNU 专属命令,文件改写经 python):
§6.1 身份(首装门两向、prev 与运行身份、dirty 固化、`--code`、能力检查 exit 11、旧脚本自举 fixture、`--adopt-sha`);§6.2 中断
(构建期失败自动归档、nginx 校验失败整体恢复且判「已改宿主」、健康门失败事务保留 + `--status` 描述阶段 + 再部署 exit 20、收养 / 回滚中断续做同一目标不翻转);
§6.3 DB(compatible / incompatible 默认 32 + `--restore-db` 回到快照点 / `--restore-db` 在 compatible 时被拒 / 健康库与权限错误不得跳过救援 /
损坏库走受控路径且理由落盘 / 救援快照只一次——回滚中断续做后路径与 mtime 不变 / 真实回滚 `migrate` 分支:`--restore-db` 回到 0001 后
`--to` 前进到含 0002 的目标,回滚事务向前补迁移 / 续做时去掉 `--no-rescue-snapshot` 只在救援阶段未完成时改为做救援、阶段已结束保留记录);
§6.4 材料(venv 指纹复用与新建、extras 进指纹、KEEP 边界、被回滚掉的版本 `--code` 重部署、`--to` 相邻事务、回滚事务引用的救援快照不被数量清理删);
§6.5 副作用隔离(构建期失败在线零改动;B 新增站点文件 / enabled 链接并删除 default 后失败,回滚 A 后新增项消失、default 恢复——真实文件;
交接中断点 closed 副本已写 + in-progress 仍为 B 时重选幂等);
§6.6 Docker 等价(既有 64 例全绿 + 默认预算守卫 + 裸机参数被拒);§6.7 旧脚本 fixture;实现检视 R1 补:持久化基准拦换库 / 显式重设基准 /
停机例外四向(有效空列表放行、查询失败 / 坏 JSON / cwd 冲突 / 材料缺失拒绝)/ 有响应无身份(HTML / 空 200 / `{}` / 500)一律冲突 /
历史基准缺字段无法确认 + `db.target` 核对 / 身份覆盖不绕过路径基准 / 收养原上下文基准与动态挂点 / 三根存储根 / `!` 否定与已跟踪 sqlite
与环境覆盖根(含工作树内绝对路径)与 `-wal/-shm/-journal` 不进快照 / 非 SQLite 完整分支 / HTTPS 跳转 origin / 预算不足稳定窗 /
入窗后请求变慢超预算判失败 / 树外入口续做收养 / 正向入口自动续做回滚。
**未逐点注入的中断矩阵**(SIGKILL 在两个链接之间、救援写入与记账之间、库替换前后、晋升各步之间):靠阶段 intent / completed 的重入判据 +
现有中断注入(pm2 save 失败、npm 构建失败、nginx -t 失败、健康门失败)覆盖,§6.2 如实记为未逐点注入。桩的边界:pm2 桩不跑真实 ecosystem、
nginx 桩把 conf.d / sites-enabled 都视为已包含、curl 桩忽略 Host / TLS 参数。留实机:TLS `--resolve` 探针、`pm2 startup` resurrect、
Playwright 浏览器保留、`releases_dir` 的 sudo 归属、源码装 nginx 的主配置 include 插入(桩只验 include 行被 -T 识别)。
