# scripts/ — 运维、治理与导出脚本

逻辑上独立于后端运行时的一次性 / 周期性脚本。后端进程**不**调用它们；它们通过 HTTP API 或直连 ORM 与系统交互。从仓库根目录运行。凡是可写数据库的脚本，下表都会明确标注；生产执行前先备份并传入绝对数据库路径。

## 日常运维与导出

| 脚本 | 用途 | 数据边界 |
|---|---|---|
| [`ensure_daily_collection_job.py`](./ensure_daily_collection_job.py) | 幂等创建/更新“每日全量采集”任务。 | 写当前配置库；可重复执行。 |
| [`import_podcast_catalog.py`](./import_podcast_catalog.py) | 预览或幂等导入精选播客目录；默认 dry-run，`--apply` 才写，`--activate` 才启用。 | 可选写当前或显式数据库；阻断源默认跳过。 |
| [`export_shendeng_daily_news.py`](./export_shendeng_daily_news.py) | 导出公共日报的结构化条目和可选 Markdown，不调用 LLM；兼容 adapter off/on 的持久化条目。 | API 只读，文件写到指定输出路径。 |
| [`compare_web_backends.py`](./compare_web_backends.py) | 比较 legacy 与 crawl4ai 网页正文提取结果。 | 只访问目标网页并写对比产物，不写业务库。 |

## Taxonomy 发现与人工治理

这些脚本保留用于未来 taxonomy 版本发现和审计，不是 v1 生产安装步骤。自动激活始终关闭，生成物必须经产品审核。

| 阶段 | 脚本 | 数据边界 |
|---|---|---|
| 冻结范围 | [`bootstrap_taxonomy.py`](./bootstrap_taxonomy.py) | 生成 manifest；带提案输入时可幂等写 Candidate evidence，不发布。 |
| 隔离采样 | [`collect_taxonomy_bootstrap_sources.py`](./collect_taxonomy_bootstrap_sources.py) | 只允许写显式指定的隔离 SQLite。 |
| 开放抽取 | [`generate_taxonomy_bootstrap_proposals.py`](./generate_taxonomy_bootstrap_proposals.py) | 读取 manifest，输出 review-only JSONL，不写数据库。 |
| 审核草案 | [`draft_taxonomy_v1_review.py`](./draft_taxonomy_v1_review.py) | 读取 Candidate/manifest，输出 JSON 和 Markdown，`user_selectable` 不会自动生效。 |
| 标签集治理 | [`generate_label_only_taxonomy_proposal.py`](./generate_label_only_taxonomy_proposal.py) | 从现有标签词表生成可复核提案和批准目录候选；不发布 taxonomy。 |

治理脚本的默认输出位于已忽略的 `data/taxonomy-review/`，属于过程数据。只有经过产品批准并提升为发布资产的目录才进入版本控制。

## Taxonomy v1 生产安装

批准目录的唯一事实来源是 [`config/taxonomy-v1-approved-catalog.json`](../config/taxonomy-v1-approved-catalog.json)。正常上线只运行总入口；下层脚本保留用于测试、诊断和可组合性。

| 脚本 | 用途 | 数据边界 |
|---|---|---|
| [`install_taxonomy_v1.py`](./install_taxonomy_v1.py) | 总入口：迁移、绑定目标库生成 review、validation-only、可选备份与导入；默认不写，`--apply` 才写，且不会发布 taxonomy 或开启分析。 | 生产允许；必须按 [`docs/taxonomy-v1-deployment.md`](../docs/taxonomy-v1-deployment.md) 执行。 |
| [`prepare_taxonomy_v1_review.py`](./prepare_taxonomy_v1_review.py) | 把批准目录绑定到目标数据库并生成完整审核回执。 | 读数据库、写回执文件。 |
| [`apply_taxonomy_v1_review.py`](./apply_taxonomy_v1_review.py) | 校验审核回执；只有 `--apply` 才导入规范标签和审计记录。 | 生产允许；通常由总入口调用。 |

## 发布与历史回填 smoke

| 脚本 | 用途 | 数据边界 |
|---|---|---|
| [`smoke_analysis_release.py`](./smoke_analysis_release.py) | 真实/合成 RSS、可选真实 LLM、租约重启恢复、SQLite 并发和个人早报 15 分钟降级。 | 强制使用非生产文件型 SQLite。 |
| [`smoke_full_analysis_backfill.py`](./smoke_full_analysis_backfill.py) | 估算并可选调用真实模型执行小批量 `full_analysis`。 | 拒绝当前配置库并限制最大文章数。 |

已移除被完整 release smoke 取代且没有调用方的 `smoke_analysis_personal_digest.py`；持久化指标继续由管理 API 和 `services.analysis_observability` 提供，不保留两个含义重叠的命令入口。

## 关于导出产物

`export_shendeng_daily_news.py` 默认在**当前工作目录**生成：

- `daily-news-{date}.json` —— shendeng 接口的 batch body
- `daily-brief-{date}.md` —— 日报 Markdown 正文（`--markdown-output` 控制）

这些是**生成产物，不入版本控制**（已在 `.gitignore` 中按 `daily-news-*.json` / `daily-brief-*.md` 忽略）。配置优先级：脚本顶部常量 < 环境变量 < 命令行参数；凭证类字段（token / 账号密码）默认留空，运行时用环境变量或私有副本提供，**切勿提交真实凭证**。

对应的单元测试见 [`tests/test_ensure_daily_collection_job.py`](../tests/test_ensure_daily_collection_job.py) 与 [`tests/test_shendeng_export.py`](../tests/test_shendeng_export.py)。
