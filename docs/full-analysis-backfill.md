# 历史文章 `full_analysis` 回填

> 状态：已实现并通过隔离数据库、真实模型和管理 API 端到端验收（2026-09-02）。

## 用途与边界

`full_analysis` 会重新调用文章分析模型，并重算评分、摘要、内容类型、规范标签和
Candidate evidence。它用于新增规范标签、修改 `prompt_description` 或升级分析 Prompt 后，
让历史文章重新接受当前语义判断。

它不同于 `retag_only`：后者不调用模型，只根据已有 Assignment 和已经人工解决的
Candidate evidence 重建闭集标签，不能发现旧证据中从未出现的概念。

历史 edition 和日报快照不会被回填改写。来源关闭 `ai_analysis_enabled` 后不会进入估算或派发。

## 管理面操作

入口：运维管理 → 标签 → 历史文章完整分析。

1. 选择时间范围：7、30、90、365 天或全部历史。
2. 选择策略：
   - `全部强制重分析`：当前版本已经成功的文章也重跑，适合新增标签后的语义补标；
   - `仅缺失或版本过期`：只处理无成功结果、正文变化、Prompt/评分版本变化、taxonomy
     版本变化或标签落库失败的文章。
3. 首次运行可填写一个或多个 `source_id` 限定 canary 来源；先执行估算，核对文章数、来源数、
   首轮/最大模型调用数和文章输入 token 估算。
4. 二次确认后创建任务；同一时间只允许一个未完成的 `full_analysis` 回填。
5. 页面显示排队、分析、成功、失败、跳过和总进度，可暂停、继续、取消或重试失败项。

创建任务的前置条件：

- 已发布 active taxonomy；
- 已开启文章分析；
- 已配置可用的分析模型。

## 调度和恢复语义

- 作业头复用 `tag_retag_jobs(operation=full_analysis)`；逐文章目标冻结在
  `tag_retag_job_items`，不把标题、正文或 URL 放进作业范围。
- 调度器每轮先扫描并领取新文章/正常重试，再把剩余并发容量分配给历史回填；文章领取仍按
  `fetched_date DESC`，所以新内容始终优先。
- 作业租约和文章分析租约相互独立。进程中断后，过期作业租约可被新 worker 接管，已排队的
  文章沿用原有超时、退避和最多 4 次尝试机制。
- 暂停只停止派发新历史文章；已经开始的单篇分析会正常收尾。取消会把尚未结束的作业项记为
  skipped，但不会粗暴中断正在调用的模型。
- 作业冻结 taxonomy、Prompt 和评分版本。运行中发生版本切换时任务失败并要求重新估算，避免
  一个作业混用多套语义。
- 存量 `fetched_date` 同时存在无时区的上海本地时间和带 offset 的时间；范围计算先统一到 UTC，
  不使用字符串大小比较。

## 上线顺序

1. 备份生产 SQLite，部署并迁移到包含 `tag_retag_job_items` 的 Alembic head。
2. 完成并发布最终 Taxonomy v1；自动激活继续关闭。
3. 确认新文章分析稳定后，先限定一个小来源完成 canary，再估算并运行最近 7 天强制回填。
4. 观察成功率、失败重试、标签覆盖和模型用量，再依次扩大到 30、90、365 天。
5. 最后单独估算“全部历史”，确认调用规模后执行。

不要把默认 7 天扫描窗口直接改成超大值。管理作业通过低优先级派发、范围冻结和进度审计控制
历史成本，而正常扫描继续承担新文章的可靠补偿。

## 可重复验收

先用 SQLite `.backup` 生成隔离库并迁移，再执行：

```bash
.venv/bin/python scripts/smoke_full_analysis_backfill.py \
  --database-url sqlite:////tmp/dorami-full-analysis.db \
  --source-id dorami_daily_brief \
  --max-articles 1 \
  --live-llm
```

脚本拒绝当前配置的应用数据库，且模型调用前必须通过文章数上限。2026-09-02 验收结果：
1 篇目标一轮完成，作业 `succeeded=1/1`、失败 0、跳过 0；分析与标签状态均 succeeded，
Prompt `article-analysis-v3`、评分 `content-value-v1`、taxonomy v1，外键检查为 0。
