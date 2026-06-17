# scripts/ — 运维与导出脚本

逻辑上独立于后端运行时的一次性 / 周期性脚本。后端进程**不**调用它们；它们通过 HTTP API 或直连 ORM 与系统交互。从仓库根目录运行。

| 脚本 | 用途 | 运行方式 |
|---|---|---|
| [`ensure_daily_collection_job.py`](./ensure_daily_collection_job.py) | 幂等地创建/更新「每日全量采集」Collection Job，默认覆盖全部内置具体来源节点（泛化高级 fetcher 需逐源参数，默认排除，`--include-advanced` 才纳入）。 | `PYTHONPATH=src uv run python scripts/ensure_daily_collection_job.py` |
| [`export_shendeng_daily_news.py`](./export_shendeng_daily_news.py) | 把哆啦美某日日报导出为 shendeng「daily-news/batch」上传 JSON：从 API 拉取该日日报记录的结构化 `extensions.items`，做确定性字段改名（复刻原 Dify code 节点），不调 LLM。导出时把哆啦美内部的丰富分类（模型发布/行业资讯/开源动态/技术大会/…）**坍缩为 shendeng 二分类**（`学术论文` 保留，其余一律 → `产业资讯`，见 `collapse_to_shendeng_classification`）。可选同时导出日报 Markdown 正文。 | `PYTHONPATH=src .venv/bin/python scripts/export_shendeng_daily_news.py` |

## 关于导出产物

`export_shendeng_daily_news.py` 默认在**当前工作目录**生成：

- `daily-news-{date}.json` —— shendeng 接口的 batch body
- `daily-brief-{date}.md` —— 日报 Markdown 正文（`--markdown-output` 控制）

这些是**生成产物，不入版本控制**（已在 `.gitignore` 中按 `daily-news-*.json` / `daily-brief-*.md` 忽略）。配置优先级：脚本顶部常量 < 环境变量 < 命令行参数；凭证类字段（token / 账号密码）默认留空，运行时用环境变量或私有副本提供，**切勿提交真实凭证**。

对应的单元测试见 [`tests/test_ensure_daily_collection_job.py`](../tests/test_ensure_daily_collection_job.py) 与 [`tests/test_shendeng_export.py`](../tests/test_shendeng_export.py)。
