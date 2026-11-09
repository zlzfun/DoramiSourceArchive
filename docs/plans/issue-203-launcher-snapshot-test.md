# 部署 launcher 快照用例:固定时序竞争改读取触发屏障(issue #203)

> 状态:进行中(2026-11-09 开工;只改 `tests/test_deploy_scripts.py`,生产 launcher `docker/dorami-deploy.example` 一字不改)。
> 实现方:Claude Code(Fable 5.1);检视方:Codex(见 §6)。分支 `feat/issue-203-launcher-snapshot-race`。
> 关联:[issue #203](https://github.com/zlzfun/DoramiSourceArchive/issues/203);用例与 launcher 来自 #102 自动部署波([方案](../archive/auto-deploy-plan.md),「launcher 单次快照读 rc」是当时脚本复检的修复项)。

## 1. 现象与归因

- PR #201 的完整 CI 里 `tests/test_deploy_scripts.py::test_launcher_final_rc_comes_from_the_same_snapshot` 失败:期望 rc=7,实际 rc=5,日志「worker 30 秒内未写出本次状态」;同组其余 206 项通过,本机单跑 2.3 s 通过。
- 用例机制(#111 首版):假 worker 一起来写本轮 `complete/rc=7`,**固定 0.3 s** 后把 state 覆写成另一轮(`run_id=ffff…`、rc=0),再睡 1 s。launcher 必须在这 0.3 s 内完成:`setsid` 起 worker → 每 0.2 s 轮询 `json_get run_id`(每次一个 python3 冷启动)→ `json_get log` → `touch` / `tail` → `state_snapshot`。本机 python3 冷启动约 30 ms;4 vCPU runner 上 4 个 xdist worker 各自 spawn 进程,100–300 ms 常见。
- 本机复现(给 launcher 副本注入延迟,跑原用例):「等 run_id」轮询前 `sleep 0.6` → rc=5(CI 形态);取快照前 `sleep 0.4` → rc=6「state 已被另一次运行覆盖」。两种失败都发生在用例想验的「取到快照之后被覆写」阶段之前。

## 2. 目标与边界

- 「读取快照前 / 后」两阶段由**确定事件**分隔,不靠 sleep 对齐;断言不变:最终退出码只能来自同一份快照(7),且 state 确已被另一轮覆盖。
- 不扩大固定 sleep;不改 launcher 的正确性语义,也不给它加测试钩子——launcher 是仓外安装件(`/root/bin/dorami-deploy`),改一行就要在生产机人工重装。

## 3. 方案:读取触发的覆写(python3 垫片)

launcher 读 state 全靠 PATH 上的 `python3`:`json_get` 是 `python3 - <file> <key> <default>`,`state_snapshot` 是 `python3 - <file>`(一次读出 run_id / phase / rc)。`DEPLOY_PATH` 本来就是 conf 可配项,测试已用它放假 docker / curl / df;本用例再前置一个 `python3` 垫片:

1. 原样转发给真解释器(`sys.executable`),退出码透传;每次调用的参数追加到垫片日志(失败时附在断言消息里)。
2. 若本次调用恰为 `python3 - <state.json>`(= state_snapshot)且读到的是本轮 `complete`,在真解释器**返回之后**把 state 覆写成另一轮 `complete/rc=0`,并落标记文件(内容 = 被覆写那轮的 run_id);只触发一次。
3. 假 worker 只负责写本轮 `complete/rc=7`,然后等标记出现再退出,不再按时间覆写。

于是 launcher 拿到的快照必是本轮(覆写发生在它之后),之后任何一次再读全局 state 都只能读到另一轮——「同一份快照」属性成为确定可判的事实,与 runner 快慢无关。

断言:rc=7;标记存在(垫片触发过;否则报「launcher 读快照的方式变了?」并附垫片日志);`state.run_id` 已是另一轮;stdout 的「worker 退出码 7(run X)」里的 X 等于标记内容(被覆写的正是本轮)。

## 4. 反向对照(收进仓库)

`test_snapshot_race_harness_catches_second_read[…]`:把 launcher 文本改回「二次读取 rc」的旧错误(锚点必须恰出现一次),用同一夹具跑改坏的副本,必须报出另一轮的 rc=0 而非 7:

- `reread_rc_in_loop`:循环里取到 complete 后再 `json_get rc`(首版脚本检视前的写法);
- `reread_rc_at_exit`:退出前再 `json_get rc`。

## 5. 被否方案

- **扩大 sleep**(0.3 → 2 s):只把窗口挪远,runner 更慢时照样错过;issue 明确不要。
- **launcher 加一行「已取快照」输出,测试监听 stdout 后再覆写**:直观,但要改仓外安装件并人工重装;旧错误若把二次读取放在那行输出之前就逃过检测。垫片方案对任何位置的二次读取都成立。
- **用 `ps` 观察 launcher 取快照后的 `sleep 0.5` 子进程 / 看 state.json 的 atime**:平台相关,与脚本内部耦合更紧。
- **`sitecustomize` 拦截 `open()`**:同样确定,但比 PATH 垫片多一层魔法,且同样要靶定 state_snapshot 的调用形。

垫片对 launcher 的唯一依赖是「state_snapshot 以 `python3 - <state.json>` 读取」;若 launcher 改读取方式,垫片不触发 → 前置断言失败并附垫片日志,改垫片即可,不会静默放行。

## 6. 自测与检视

- 新 3 例单跑 6.3 s 通过;循环 10 遍 0 失败;在注入 0.6 s / 2 s(等 run_id 前)与 0.4 s / 1.5 s(取快照前)延迟的 launcher 副本上,新夹具 4 例全部通过(原用例在 0.6 s / 0.4 s 下分别 rc=5 / rc=6)。
- 部署分组 CI 同形(`tests/test_deploy_*.py tests/test_migrations.py -n 4 --dist worksteal`):216 passed,4 min 02 s(2026-11-09 本机);`python3 scripts/check_docs.py` 0 问题。
- 检视:待填。
