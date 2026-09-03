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

   本次发布的单一 head 为 `f3a8c1d9e2b4`。其中 `d2c4f6a8b0e1` 先把
   Taxonomy 功能支线与 v3.43 主干收敛，随后本迁移补齐分析/日报租约、FK 索引和
   `full_analysis` 单活约束。SQLite 的整个 revision 使用显式事务；中途失败不会
   留下 schema 已变化但版本号未推进的半迁移状态。

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

## 已经发布 Taxonomy 的数据库

安装器只接受 active taxonomy version 为 0 的新环境。任何已经发布 v1 的数据库都
不得用冷启动目录原位覆盖：开发验收库应恢复备份或重建；生产目录若需要语义变更，
必须形成 v2 变更集并重新走治理和发布流程。这样可以避免复活已合并/废弃标签、覆盖
人工配置，或在 Alias 冲突时留下半套状态。

## 上线验收点

- 批准目录 manifest 为 `b021bc7c8fa64fe0de324e8caebfa0bbafbea181790cc64247a21d8dc9e0a090`。
- 规范标签共 96：Topic 37、Industry 14、Entity 45；用户可选共 94。
- `topic.pretraining`、`topic.post-training` 可筛选和推荐，但不可作为长期兴趣独立选择。
- `industry.cybersecurity` 中文为“网络安全产业”；`industry.robotics` 没有无条件 Alias“机器人”。
- `review_basis=label_set_only`、`coverage_decision=not_applicable`；不得伪造文章或来源覆盖数字。
- 发布前 active taxonomy version 为 0，发布后为 1；文章分析只能在发布成功后开启。
- 私有自定 RSS 在首版硬禁用第三方 AI 分析；将来必须先具备逐订阅者授权与费用归属，
  才能另行开放。

如批准目录本身发生任何语义变更，必须更新 manifest 并重新走产品审核；把同一 manifest 导入另一套全新数据库不属于重新审核。

## 回滚

SQLite 发布回滚使用第 3 步生成的完整数据库备份，不执行 `alembic downgrade`。
本版本包含分支合并迁移和多张有关联的数据表，按单个 revision 向下回退既不能表达
产品状态回滚，也可能选择错误分支；恢复备份后再按当时版本重新启动服务。
