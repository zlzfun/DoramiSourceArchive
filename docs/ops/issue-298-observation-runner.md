# #298 本机 72 小时观察运行器

> 2026-11-24 状态：用户已取消 #298 持续验证，任务与 LaunchAgent 已停止；本文保留独立运行器的操作说明，不表示授权重启或当前仍在运行。

> 状态:进行中。运行器已实现；真实观察未启动。

运行器是 `scripts/issue298_observation.py`，使用当前 worktree 的 Python 环境。所有命令必须显式传 `--root`；只接受自己创建的新目录和数据库，不接管 UI 库或历史付费包。运行目录含私有凭据、账户密码与回执，不能提交 Git 或整体公开分享。

## 准备与配置

```sh
.venv/bin/python scripts/issue298_observation.py --root RUN prepare --sources SOURCE_MANIFEST
```

准备后显式执行 `publish-catalog`，将仓库已有审核回执的目录发布为实验基线 v1；该命令不批准任何新概念，保留 `taxonomy deployment=manual`，不会接入生产同步。已有 v1 重复执行不会创建新版本。未发布本地基线时，arm/run 会拒绝启动精确归并。

默认后端 `127.0.0.1:8099`、前端 `127.0.0.1:5199`；`--frontend-port 0` 可关闭前端。准备阶段所有采集、语音和治理开关关闭，LLM/ASR/TTS 截止均已过期。相同输入重复准备保留 run_id、数据库及回执；不删除或重建已有账本。中断准备需要先检查现场。

在 `RUN/private/runtime.ini` 配置：

- `[llm]` 的 endpoint/key/model 及已验证的模型参数；不沿用旧授权期限。
- `[bailian_speech]` 的新加坡 endpoint/key/account_scope、当前可用模型/音色和有理数计价字段；保留本实验 receipt_root、每日 2000 分 TTS 限额以及独立预算范围。
- `[taxonomy] feishu_config_path` 指本实验私有配置副本。机器人使用 `review_base_url=http://127.0.0.1:5199`、`allow_loopback_review_url=true`、北京时间 18:00；在预检结束前 `enabled=false`。

本机审核入口只能在这台电脑的飞书桌面端/浏览器使用。手机或其他电脑上的 127.0.0.1 不能访问本机实验服务。不得把它误写成公网可达入口。

凭据仅从已获授权的配置按字段白名单搬运，不复制旧数据库、回执目录、授权截止或其他部署开关。真实 smoke 与正式观察共享该实验账本及回执，未知结果不重提；开发 smoke 使用短期截止，结束恢复过期值后再完成正式启动预检。

语义扫描的 `authorized/unknown` 对原内容身份保持未决，即使升级匹配器/契约或切换模型也不会自动重发；真实条件修订或正文/来源身份变化才构成新的工作。状态报告分别统计扫描进度与持久调用授权，停费证明也比较语义授权总数，不能只看成功用量。历史窗外样本只用于离线说明，未处理、部分覆盖和 unknown 不可当作已判无关。

## 冻结与启动

先完成实现/迁移、前端构建及 UI、独立检视、真实语音 smoke 和原样付费重放。`RUN/readiness.json` 格式：

```json
{
  "checks": {
    "implementation": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"},
    "migration": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"},
    "ui": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"},
    "cross_review": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"},
    "speech_smoke": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"},
    "paid_replay": {"passed": true, "evidence": "/absolute/proof.json", "sha256": "文件SHA256"}
  }
}
```

每个 evidence 必须是真实结果文件，不能以占位文本代替检查。最终配置、飞书开关及前端构建完成后再 arm；arm 后修改配置/证据/代码会阻止 T0。

```sh
.venv/bin/python scripts/issue298_observation.py --root RUN install-agent --readiness RUN/readiness.json
# 上一步可提前执行；未 arm 时只记录 waiting_for_explicit_arm，不启动任何应用服务。
.venv/bin/python scripts/issue298_observation.py --root RUN arm --readiness RUN/readiness.json
```

LaunchAgent 每 75 秒检查一次，唯一工作进程开始后由其监督应用运行。下一次检查才启动 T0；manifest 中的 `started_at` 才是观察起点。前端 dist 被复制到本实验私有目录；代码、配置、source snapshot 和 readiness 哈希冻结。T0+72h 固定写入 LLM/ASR/TTS 新请求截止，重启不延期。

也可前台直接 `run --readiness RUN/readiness.json`，该命令本身就是显式启动；不要在另一个监督进程仍存活时再次启动。

## 休眠、重启与结束

macOS LaunchAgent 设置 `AbandonProcessGroup=false`，由 launchd 清理异常退出作业的同组子进程。定时间隔不保证在休眠中执行；机器醒来后先检查绝对截止，过期时只停费和核验，不重新启动付费服务。未到期的异常恢复仍使用原 T0、原账本、原回执及原源码哈希。记录到的旧 PID 仍存在时保守等待，不对可能复用的 PID 发送信号。

正常 SIGTERM、用户 stop、应用子进程退出或到期会结束该次观察，不静默开启新窗口。记录心跳缺口；休眠/缺测不能算成连续有效观测，也不能靠延长授权补足。自动生成报告如实保留不足项。

```sh
.venv/bin/python scripts/issue298_observation.py --root RUN status
.venv/bin/python scripts/issue298_observation.py --root RUN stop
.venv/bin/python scripts/issue298_observation.py --root RUN report
```

stop 先关闭开关并使截止过期。监督器停止其拥有的前后端后，跨至少 65 秒采两次提交计数；恢复路径由两次 LaunchAgent tick 完成。尚有工作进程或计数变化时保持 `stopping`，不能声称停止已证明。计数包含语音账本、provider attempts 与已记账 LLM；最后一项不是所有 HTTP 尝试的完整审计，必须同时确认工作进程已停止。

停止确认后保留 `report.html`、`metrics.json`、`source-counts.csv`、manifest、日志及账本/回执配对证据。质量审阅、相同内容对照和自然 72h 不因脚本成功启动就自动通过。LaunchAgent 在 stopped 状态只做无付费检查；清理时使用 manifest 中唯一 label 执行 `launchctl bootout gui/$(id -u)/LABEL`，再移除对应 plist，不能影响其他作业。
