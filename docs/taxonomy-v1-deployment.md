# Taxonomy v1 上线手册

> 适用目录：仓库内 `config/taxonomy-v1-approved-catalog.json`。该文件是 2026-09-02
> 已批准的产品决策，不依赖某个数据库里的文章、热度或 Candidate ID。其
> `manifest_sha256` 用于识别内容是否发生变化；生产导入不是再次做产品审核。

## 发布资产

- `config/taxonomy-v1-approved-catalog.json`：96 个规范标签及 Alias、层级、Entity 类型和用户资格的唯一批准目录。
- `scripts/install_taxonomy_v1.py`：把批准目录绑定到目标库、校验、导入并写审核回执；不会自动发布 taxonomy，也不会开启文章分析。
- 目标库 review JSON：每个环境独立生成并留档。它记录本次导入使用的 manifest 和目标库校验结果，不应提交回仓库。

## 全新生产数据库

上线前保持文章分析关闭。使用生产配置运行以下流程；下例的数据库 URL 和 review 路径需替换为目标环境的明确值。

1. 执行 schema 迁移：

   ```bash
   .venv/bin/alembic upgrade head
   ```

   本次发布的单一 head 为 `d2c4f6a8b0e1`。它是无数据操作的双父节点合并迁移：已在 v3.43 主干的生产库会执行 Taxonomy 功能支线；曾运行早期 Taxonomy 验收的测试库会补执行主干的账号会话与计量迁移；全新库会执行两条链后收敛到同一 head。

2. 用仓库批准目录生成目标库 review，并做 validation-only：

   ```bash
   .venv/bin/python scripts/install_taxonomy_v1.py \
     --database-url sqlite:////var/lib/dorami/cms_data.db \
     --review-out /var/lib/dorami/releases/taxonomy-v1-review.json \
     --actor release-operator
   ```

   成功输出必须包含 `"mode": "validation-only"` 和 `"approved_entries": 96`。脚本会拒绝非空未决 Candidate 池，避免把新库发现出的词静默跳过。

3. 用同一目录重新生成并导入，建议同时做一次 SQLite 在线备份：

   ```bash
   .venv/bin/python scripts/install_taxonomy_v1.py \
     --database-url sqlite:////var/lib/dorami/cms_data.db \
     --review-out /var/lib/dorami/releases/taxonomy-v1-review.json \
     --overwrite-review \
     --backup /var/lib/dorami/backups/cms_data.before-taxonomy-v1.db \
     --actor release-operator \
     --apply
   ```

   成功输出必须是 `"mode": "imported-awaiting-publish"`。此时 96 项已入库、审核回执已写入，但 active taxonomy version 仍为 0。

4. 在“运维管理 → 标签”确认 Topic 37、Industry 14、Entity 45，且发布 Gate 无阻断；人工点击一次“发布 Taxonomy v1”。

5. 发布成功、最近 7 天重标任务创建后，再开启文章分析。需要给全部历史文章补充灵活标签时，另行启动 `full_analysis`；仅 `retag_only` 不会重新调用模型抽取灵活标签。

## 已发布 v1 的开发/测试库

早期测试库可能已经发布过旧的 v1，不能再走“version 0 → 手工发布”路径。仅在开发/测试环境使用：

```bash
.venv/bin/python scripts/install_taxonomy_v1.py \
  --database-url sqlite:////absolute/path/to/cms_data.db \
  --review-out /absolute/path/to/taxonomy-v1-active-sync.json \
  --backup /absolute/path/to/cms_data.before-label-taxonomy-v1.db \
  --actor local-maintenance \
  --sync-active-v1 \
  --apply
```

该模式只允许当前版本恰好为 v1：原位新增/更新批准的 96 项、废弃已知错误 `topic.ai` 和 `topic.ai.vendor`、移除 Industry Robotics 的歧义 Alias“机器人”，并保留现有 Candidate 状态和文章关系。它不是生产升级捷径，也不会创建 v2。

## 上线验收点

- 批准目录 manifest 为 `b021bc7c8fa64fe0de324e8caebfa0bbafbea181790cc64247a21d8dc9e0a090`。
- 规范标签共 96：Topic 37、Industry 14、Entity 45；用户可选共 94。
- `topic.pretraining`、`topic.post-training` 可筛选和推荐，但不可作为长期兴趣独立选择。
- `industry.cybersecurity` 中文为“网络安全产业”；`industry.robotics` 没有无条件 Alias“机器人”。
- `review_basis=label_set_only`、`coverage_decision=not_applicable`；不得伪造文章或来源覆盖数字。
- 发布前 active taxonomy version 为 0，发布后为 1；文章分析只能在发布成功后开启。

如批准目录本身发生任何语义变更，必须更新 manifest 并重新走产品审核；把同一 manifest 导入另一套全新数据库不属于重新审核。
