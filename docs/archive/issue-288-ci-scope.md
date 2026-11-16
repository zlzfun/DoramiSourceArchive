# CI 提速(一):按改动面选 job、一轮返修一次推送、本地 / CI 分工(issue #288)

> **归档于 2026-11-16(issue #288,开发版 3.68.0-alpha,CI / 测试 / 文档、无运行时代码;Codex 两轮检视通过,无目检界面,按「检视通过 + CI 绿」直接合入)**:现状以 `.github/workflows/ci.yml`、`scripts/ci_scope.py`、`tests/conftest.py` 与 `docs/dev/testing.md` 为准,文中路径与状态为当时记录。
> 状态:已落地(2026-11-16 开工并当日收口;合入后用一条纯文档 PR 实测轻量路径,见 §8)。
> 实现方:Claude Code(Fable 5.2);检视方:Codex(见 §7)。分支 `feat/issue-288-ci-scope`。
> 关联:[issue #288](https://github.com/zlzfun/DoramiSourceArchive/issues/288);后续 [issue #289](https://github.com/zlzfun/DoramiSourceArchive/issues/289)(`backend-unit` 分片与用例开销);CI 两 job 拆分来自 issue #150。

## 1. 实测现状(2026-11-16)

用户的问题:每次 PR 推送都跑 CI、推送前本地又跑一遍,重复测试拖慢迭代、也浪费 token。先量了再动:最近 40 次 CI 运行、最近 12 个合入 PR、一次带计时插桩的本机 unit 全量。

| 事实 | 数字 |
|---|---|
| PR 一次 CI 的等待 | 约 10.5 分钟,由 `backend-unit` 决定(测试步骤 580–640 s) |
| 其余 job | `backend-deploy` 4–6 分钟;前端 lint + test + build 不到 1 分钟;docs-site 约 20 秒 |
| 同一条 unit 命令 | 本机 3 分 27 秒,CI 10 分 12 秒(约 3470 个用例) |
| 最近 12 个合入 PR | 8 个只动 `frontend/` 与 `docs/`,却都跑了全套后端 |
| 每个 PR 的 CI 次数 | 通常 2–3 次;最后一次常是晚 2–5 分钟推的纯文档提交(记检视结论 / 回填 PR 号),把在跑的那次取消重来 |
| 39 次已结束的运行 | 29 成功、10 取消、0 失败 |

结论:最大的浪费不在「本地一遍、CI 一遍」,而在 CI 不看改动面、且一个 PR 被重复触发。四件事按收益排:按改动面选 job、一轮返修只推一次、本地与 CI 分工、等 CI 不轮询。`backend-unit` 本身为什么要 10 分钟(分片、建库与哈希开销)另开 #289。

## 2. 做什么

1. **CI 按改动面选 job**(§3):PR 只动前端 / 文档时跳过两个重型后端 job,等待从约 10.5 分钟降到约 1 分钟。
2. **一轮返修只推一次**(§4):文档收尾并进最后一次代码推送。
3. **本地与 CI 分工**(§4):本地跑受影响的测试文件 + 快守卫,全量交给 CI 跑一次;直接合入口径从「检视通过 + 全量套件绿 + CI 绿」改为「检视通过 + CI 绿」。
4. **等 CI 不轮询**(§4):`gh pr checks --watch` 放后台,结束时自动唤醒。

## 3. 按改动面选 job

### 3.1 判定规则

`ci.yml` 新增 `changes` job,运行 `scripts/ci_scope.py`,输出 `backend=true|false`:

- **`false`(轻量)只有一种情形**:事件是 `pull_request`,且 PR 的**每一个**改动路径都落在 `frontend/` `docs/` `docs-site/` 之内或是仓根的 `*.md`。
- **其余一律 `true`(全量)**:任一路径在外(含未知的新目录、`.github/`、`.claude/`、`scripts/`、`tests/`)、没有改动路径、不是 `pull_request` 事件(push 到 main 永远全量)、以及一切看不准的情形。

偏向全量是有意的:判错成全量只是多等几分钟,判错成轻量会让后端回归带着绿灯合入。清单是「可以跳过」的白名单,不是「需要跑」的名单——新增顶层目录默认全量。

### 3.2 改动路径怎么算

`pull_request` 事件检出的是 `refs/pull/N/merge`:一个合并提交,第一父提交是 base 分支尖端,第二父提交是 PR head。「第一父提交 → 合并提交」的 diff 正是「这次 CI 测的东西相对 main 改了什么」;main 在分叉后另有的提交不算进来。

- `changes` job 的检出用 `fetch-depth: 2`(合并提交 + 两个父提交),不拉全量历史。
- 脚本只在 HEAD **确实是这个 PR 的合并提交**时才信这份 diff:恰有两个父提交,且第二父提交等于事件里的 `pull_request.head.sha`(经 `PR_HEAD_SHA` 传入)。否则(检出方式被改、深度退回 1、HEAD 是普通提交)`HEAD^1..HEAD` 只是最后一个提交的改动——PR 末尾一个纯文档提交就会把整条 PR 判成文档改动。这类情形一律全量,不报错。
- `git diff --name-only -z --no-renames`:`-z` 免去路径转义(中文名、空格);`--no-renames` 把改名拆成删除 + 新增,`src/x.py → docs/x.md` 不会只露出后者。
- 路径形状可疑的(绝对路径、含 `..` / 空段 / 反斜杠)不算白名单内。
- 只用标准库,runner 的系统 `python3` 直接跑,不引第三方 action。

### 3.3 job 结构与汇总门

```
changes ──backend=true──▶ backend-unit + backend-deploy ─┐
        └─backend=false─▶ backend-light ─────────────────┴─▶ backend (pytest) 汇总门
frontend / docs-site / workflows:不变,每次都跑(都不到 1 分钟)
```

- `backend-unit` / `backend-deploy`:`needs: [changes]`,`if: needs.changes.outputs.backend == 'true'`。
- `backend-light`:`if: … == 'false'`,只跑 `ci_scope.LIGHT_TESTS`(`tests/test_docs_index.py` 文档守卫、`tests/test_pwa_support.py` 读 `frontend/vite.config.js`),装依赖 + 跑完不到一分钟。
- 汇总门 `backend (pytest)` 名字不变(分支保护 required 的名字),`needs` 四个上游,`if: always()`。只放行两条路径:
  - `changes` 成功且 `backend=true`:两个重型 job 都 `success`;
  - `changes` 成功且 `backend=false`:两个重型 job 都 `skipped` **且** `backend-light` `success`。
  - 其余全拒:`changes` 没成功时 output 是空串,不落进任何一条——判定本身出错不能变成放行;「不是 true」不等于「是 false」。

分支保护开不开,这个门都成立:开了,required 仍只勾 `backend (pytest)`,被跳过的 job 不用勾;没开(2026-11-16 用 GitHub API 核实 main 当前未开启保护、无 rulesets),它照样是 PR 页上那一个看结论的检查。

### 3.4 「跳过不丢覆盖」靠执行,不靠记

跳过重型 job 的前提是:除了 `LIGHT_TESTS`,没有别的后端测试依赖 `frontend/` `docs/` 等路径下的文件。盘点下来现在确实只有两条。但前提靠人记会烂——以后有人写了读 `docs/contracts/*.md` 的测试却没登记,一次纯文档 PR 就能带着绿灯把它改红。

所以 `tests/conftest.py` 加了**读取审计**:进程级审计钩子(`sys.addaudithook`)记下每次落在这些路径上的 `open` / `os.listdir` / `os.scandir`,归到当前收集的模块或当前用例的阶段;所在测试文件不在 `LIGHT_TESTS` 里,这一阶段就判失败,报错信息直接写做法(登记进 `LIGHT_TESTS` 并同步 `ci.yml`,或别读)。新增这样的测试必然改 `tests/`,那次 PR 走全量,当场就红——洞开不出来。

- `SKIP_SAFE_DIRS` 与 `LIGHT_TESTS` 只在 `scripts/ci_scope.py` 定义一次;conftest、`ci.yml` 的轻量命令(守卫比对)都从它来。
- 开销:钩子对每个审计事件先做一次集合判断,只有路径字符串像是这些目录的才付 `realpath`;unit 全量带审计 207 s,与不带时相同。
- 看不见的两类:**子进程**里的读取(用例起的脚本自己去读真实仓)——现有这类用例(部署脚本、迁移守卫)都在临时仓里跑,不读真实仓的这些路径,`check_docs` 是进程内 `importlib` 载入、看得见;以及**只判存在不打开**(`Path.exists()` / `os.stat` 不产生审计事件)——现有用例没有这样依赖这些路径的。两类都由 main 上的全量兜底。
- 兜底:push 到 main 永远全量。轻量路径真放过了什么,合入后 main 上会红。

### 3.5 已知边界

- **PR 可以改判定本身**。`pull_request` 事件用的是 PR 自己的 `ci.yml` 与 `scripts/ci_scope.py`,一个 PR 把白名单改宽就能让自己走轻量——但它同样可以直接删掉 `ci.yml` 里的 job,信任边界没有变化:靠检视,以及合入后 main 上的全量。
- **base 落后**与以前一样:PR 的 CI 测的是当时的合并结果,之后 main 再前进不重跑;兜底仍是 main 上的全量。
- 前端 job 不按改动面跳过:不到 1 分钟,省不出等待,还多一条判错的路。

## 4. 流程口径(文档与技能)

- **一轮返修只推一次**(`docs/dev/workflow.md` §6):PR 开出后,每轮检视返修连同文档收尾(检视结论、version-history、PR 号回填)在本地攒齐再一次 `git push`。每次推送都会取消在跑的 CI 并从头计时。PR 号回填不单独推:随下一次本来要推的提交带上,赶不上就不写(squash 提交标题自带 `(#N)`)。
- **本地与 CI 分工**(`workflow.md` §2、`docs/dev/testing.md`):本地只跑受影响的测试文件 + 快守卫;全量由 CI 跑一次。两个例外:改部署脚本 / 迁移时本地跑 deploy 侧(bash 3.2 只有 macOS 本机测得到);改动面宽到说不清影响哪些文件(公共夹具、`conftest.py`、`models/db.py`)时本地先跑全量——CI 红一次的往返比本机 3.5 分钟贵。
- **直接合入口径**(`AGENTS.md` 协作流程、`workflow.md` §7):无目检界面的纯后端 / 纯文档 PR,「检视通过 + 全量套件绿 + CI 绿」改为「检视通过 + CI 绿」——CI 按改动面跑这次改动需要的那一套(动到后端即全量),不再要求本地另跑一遍。
- **等 CI 不轮询**(`workflow.md` §7、`dorami-issue-loop` 技能):`gh pr checks <N> --watch --fail-fast > log 2>&1; rc=$?` 放后台,结束时自动唤醒;不前台阻塞(全量 CI 比前台命令 10 分钟上限长)、不写 sleep 循环。

## 5. 守卫与自测

- `tests/test_ci_scope.py`(新,60 例):判定表(白名单内 9 例、之外与形状可疑 27 例);按 CI 形态造临时 git 仓(bare origin + `--depth 2` 的 `file://` 浅克隆 + `--no-ff` 合并提交)端到端——纯前端 / 文档 → `false`,任一路径在外 → `true`,改名(并对照证明带改名检测时 git 只报新路径)、base 另有后端提交、空 PR、深度 1、HEAD 是普通提交、PR head 不符、非 PR 事件、不在 git 仓里;读取审计的反向对照(仓外探针测试文件经 `-p conftest` 载入:用例体 / 夹具 / 列目录 / 相对路径读取各判失败,模块顶层读取判收集失败,读别处与临时目录里的同名路径不受影响;检视返修后另加:读完再 `skip` / `xfail`(命令式、标记式、夹具里)一律判失败而本来就失败的保留原因,类里的参数生成与模块级 skip 判收集失败,conftest 在同一进程执行两遍仍只有一份记录)。
- `tests/test_ci_workflow.py`(改):
  - 汇总门真值表从 16 种扩到 1024 种(`changes` 结果 4 × `backend` 取值 4 × 三个 job 结果各 4),真跑 `ci.yml` 里那段 shell,与独立重述的真值表逐项比对,恰有 5 种放行;
  - 新增 `_assert_scope`:`changes` 无条件运行、唯一命令是判定脚本、`outputs.backend` 与汇总门的 `BACKEND` 都接自它、`fetch-depth: 2`、`PR_HEAD_SHA` 接线;重型 / 轻量 job 的 `needs` 与 `if` 逐字;轻量命令的文件清单与 `LIGHT_TESTS` 逐字一致、不带筛选项、都落在 unit 侧;
  - 「job 成功」等于「用例通过」(检视返修):汇总门那一步的形状恰是 name + env(五个变量各一次)+ `run: |`、job 层没有 env;三个后端 job 里跑 pytest 的那一步只能是 name + 单行 run,job 层只认 name / needs / if / runs-on / steps;`ci.yml` 任何位置不得设置 `PYTEST_ADDOPTS`;
  - 反向对照共 51 条突变:原有 14 条按新文本更新;首版新增 19 条(门禁忽略 `changes` 结果、把未知取值当轻量、轻量路径不要求 light 成功 / 接受任意重型结果、全量路径接受 skipped、`BACKEND` 或 output 写死、判定步骤换成常量 / 另加一步写 output、判定 job 挂条件、深度 / PR head 接线丢失、重型 job 条件去掉或放宽、轻量条件反转、轻量清单缺 / 多 / 被 `-k` 筛空);检视返修新增 18 条(汇总门那一步以 `- if:` 开头被跳过、job 层 env + step 层同名常量、同名 env 写两次、多一步、改 shell;pytest 步骤挂条件 / `continue-on-error` / `PYTEST_ADDOPTS`、命令折行或同行接 `|| true`(带不带空格)、命令末尾 `&`、job 层 env / `continue-on-error`)。
- 自测(本机,`-n 4 --dist worksteal`):上述两文件 125 例通过;unit 侧全量 3567 passed / 1 skipped(约 3.5 分钟;首版实测带读取审计 207 s,与不带时相同,无误报);deploy 侧全量 223 passed(4–5 分钟);`scripts/check_docs.py` 0 个问题。`ci.yml` 经 YAML 解析核对 job 结构;actionlint 本机没有,由 CI 的 `workflows` job 查。

## 6. 取舍与不做

- **不裁 push 到 main 的全量**:没人等它,公共仓库分钟数免费,它是「PR 分支落后 main」与判定出错时的兜底。
- **不按改动面跳过前端 / docs-site / actionlint**:都不到 1 分钟。
- **不引第三方 paths-filter action**:判定要能在仓内被测试(判定表 + 临时仓端到端),且不想给合入门禁加外部依赖。
- **不把 `.claude/` 放进白名单**:很少改,且守卫规则 / 技能变化值得一次全量。
- **不开启分支保护、不开自动合并**:是仓库所有者的设置,记入 backlog 待定;开启后「可直接合入」的 PR 能推完设自动合并就走。
- **不动 `backend-unit` 的 10 分钟**:分片、PBKDF2 轮数、建库模板、`/dev/shm` 在 #289。

## 7. 交叉检视

实现方 Claude Code(Fable 5.2);检视方 Codex(`gpt-6-astra`,effort ultra,只读沙箱)。任务书、报告、表态与修复清单在 worktree `.review/`。

### R1(2026-11-16)

结论:无 P1,4 × P2 + 1 × P3,「可合入」。判定脚本与汇总门未发现会错误放行的路径,`LIGHT_TESTS` 未发现漏项;Actions 语义(隐式 `success()`、上游失败 / 跳过的传播、`always()` 下读 `needs`)与实现的假设一致。五条全部接受,无分歧。

| # | 级别 | 意见 | 修法 |
|---|---|---|---|
| 1 | P2 | 读取审计只处理 `pytest.Module` 的收集报告:类里的 `pytest_generate_tests` 在收集「类」时才跑,那里读文档再参数化的用例全绿、不必登记 | 归属改按收集器的 `path`(模块与它下面的类都是那个 `.py`);补「类级参数生成」探针 |
| 2 | P2 | 只把原本 `passed` 的报告改判失败:读完再 `pytest.skip()` / `pytest.xfail()` 的用例绕过登记 | 改为「没失败的都判失败」(跳过、xfail、xpass、模块级 skip),清掉 `wasxfail`;本来就失败的保留原因;钩子包装改 `tryfirst` 保证在内置 skip / xfail 处理之后执行;补 5 种写法的探针 |
| 3 | P2 | 汇总门守卫没有确保那一步真的执行、拿到的真是上游结果:`- if: false` 写在 step 首行逃过 `^\s+if:`;job 层放正确映射、step 层同名常量覆盖 | 汇总门 job 层键白名单(不许 env);唯一一步的形状白名单(name + env 五个变量各一次 + `run: |`);补 5 条突变 |
| 4 | P2 | 轻量 job 的守卫只看命令文本:step 级 `if` / `continue-on-error`、`PYTEST_ADDOPTS: --collect-only`、下一行更深缩进的 `|| true`(YAML 折进同一条命令)都让「job 成功」不等于「两条守卫通过」 | `_assert_plain_pytest_step`:跑 pytest 的那一步只能是 name + 单行 run,命令里不得串接,job 层键白名单,全文不得出现 `PYTEST_ADDOPTS`;同一条守卫一并用于 `backend-unit` / `backend-deploy`(同类处,此前也没有);补 11 条突变 |
| 5 | P3 | 文档:「被跳过的 required 检查会一直挂着」说反了(被 `if` 跳过的 job 算通过;挂着的是 workflow 被路径过滤没运行);`workflow.md` 的「CI 即全量」、`scripts/README.md` 的「backend-deploy 每次 PR 跑」、`release-process.md` 的「全量约 5 分钟」过时 | 四处照改 |

实现方自查另修一处:`tests/conftest.py` 若在同一进程里被执行两遍(换模块名再导入)会装两个审计钩子、各记各的——读取记录与「已安装」标记改挂进程级,补子进程用例。

Codex 核实后确认的边界(不改,记录):重跑旧运行保持原 SHA,不追随后来的 merge ref;`pull_request` 的 `edited`(只改 base)不在触发类型里,是既有配置;审计对「列仓根目录只取文件名」与「上一条用例的后台线程读取算到下一条头上」无能为力,现有用例没有落在这两处的。

### R2(复检,只对照修复清单)

结论:F1–F6 全部修到,未发现新增 P1,「可合入」。Codex 实测:`tryfirst=True` 的执行顺序有效,读后 skip / xfail / xpass 均改判失败并移除 `wasxfail`,setup 阶段改判后 call 不运行、teardown 照常;49 条突变全部被拒、汇总门 1024 种组合验证通过;步骤改名、空行与独立注释、增加准备步骤、调整 env 变量顺序都没有被形状白名单误拒。受只读沙箱限制它没有复跑完整套件(实现方本机:unit 3567 passed、deploy 223 passed)。

观察项一条:不带空格的 shell 串接符(`…cacheprovider||true`)绕过 argv 检查——分词不会把 `||` 拆成独立的词。一行可修,复检后顺手收紧:改看原始命令串,不许出现 `| & ; < >` 反引号与 `$(`,补两条突变(共 51 条)。这一处改动在复检之后,未再送检。

## 8. 合入后验证

(合入后用一条只改文档的 PR 走一遍轻量路径,把实际运行与耗时记在这里。)
