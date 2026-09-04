# Taxonomy v1 上线手册

> 适用资产：仓库内 `config/taxonomy-v1-approved-catalog.json`。这是已经人工批准的
> 96 项标签目录；部署自动化只负责把它可靠地装入外网权威库，不代替人工发布。

## 部署角色必须显式配置

Taxonomy 部署姿态与 `[runtime] role` 是两条独立的轴。当前外网和内网都使用
`role = all`，因此绝不能根据运行角色猜测谁是 Taxonomy 权威。

```ini
# 外网 Dorami：批准目录的唯一部署权威
[runtime]
role = all
[taxonomy]
deployment = authority
catalog = config/taxonomy-v1-approved-catalog.json

# 内网 Dorami：只通过 Archive Sync 接收已发布 Taxonomy
[runtime]
role = all
[taxonomy]
deployment = replica
```

`deployment` 只接受：

- `authority`：每次正式启动在数据库迁移完成后、API/worker 启动前 reconcile 批准目录；
- `replica`：不读取也不写本地 catalog，只接受 Archive Sync 的 Taxonomy 快照；
  管理写 API 从进程启动起即为只读，即使首次同步尚未执行；
- `manual`：启动时不动作，是未配置时的安全默认值，适合独立开发库或人工恢复窗口。

可分别用 `DORAMI_TAXONOMY_DEPLOYMENT` 和 `DORAMI_TAXONOMY_CATALOG` 覆盖。生产双机
必须明确写 `authority` / `replica`；两端都留成 `manual` 不会自动安装，两端都写
`authority` 会绕过“内网只同步”的边界，属于必须在发布检查中阻止的错误配置。

## 正常上线顺序

Docker、裸机 `deploy.sh` 和开发入口 `python src/main.py` 都执行同一顺序：

1. `ensure_migrated` 将 schema 升到当前 Alembic head；
2. 读取显式 Taxonomy 部署姿态；
3. 外网 `authority` 在单个数据库事务中校验并 reconcile 批准目录和 receipt；
4. 成功后才启动 API 与进程内 worker；任何冲突都会使启动失败；
5. 管理员在“运维管理 → 标签”核对发布 Gate，人工点击“发布 Taxonomy v1”；
6. 内网 `replica` 之后通过 Archive Sync 接收已经发布的 Taxonomy。

Archive Sync v3 的连接预检会读取 Taxonomy 原子快照并要求
`taxonomy_version > 0`。因此第 5 步没有完成时，内网不会安装 consumer fence、
不会执行一次性 rebase，也不会推进任何 checkpoint。

首次 authority 启动日志状态为 `installed_awaiting_publish`，只会导入标签、Alias、层级、
审计事件和 review receipt，active taxonomy version 仍为 0。相同 catalog digest 的后续
启动返回 `unchanged`，不会重复写事件或覆盖人工发布后的治理状态。

以下情况一律 fail closed，不启动 API/worker：catalog 内容与其 digest 不符、已存在 receipt
的 digest 不同或格式损坏、没有 receipt 却已经存在 TaxonomyVersion、存在未决 Candidate，
以及已有标签/Alias 与批准目录不一致。reconcile 在写前完成全量校验，并在一个事务中提交；
失败不会留下半套目录或 receipt。

## 人工验证与恢复工具

正常部署不需要单独跑安装脚本。`scripts/install_taxonomy_v1.py` 是同一 runtime reconciler
的薄封装，保留给发布前校验、SQLite 在线备份和明确的恢复操作：

```bash
# 只校验仓库资产，不连库、不写库
.venv/bin/python scripts/install_taxonomy_v1.py

# 明确恢复到一套兼容的空库/部分导入库；仍然不会发布
.venv/bin/python scripts/install_taxonomy_v1.py \
  --database-url sqlite:////var/lib/dorami/cms_data.db \
  --backup /var/lib/dorami/backups/cms_data.before-taxonomy-v1.db \
  --actor release-operator \
  --apply
```

已有 Candidate、审核/合并记录或复杂历史库不能用冷启动 reconciler 猜测处理。通用治理恢复
能力仍由 `prepare_taxonomy_v1_review.py`（绑定目标库生成 review）与
`apply_taxonomy_v1_review.py`（校验并显式 apply）提供；完成恢复并确认 receipt 后，再恢复
正常 authority 启动。

## 上线验收点

- catalog digest 为 `b021bc7c8fa64fe0de324e8caebfa0bbafbea181790cc64247a21d8dc9e0a090`；
- 规范标签 96 项：Topic 37、Industry 14、Entity 45；用户可选 94 项；
- `topic.pretraining`、`topic.post-training` 可筛选和推荐，但不可独立订阅；
- `review_basis=label_set_only`、`coverage_decision=not_applicable`；
- 外网首次 reconcile 后 active version 为 0，只有人工发布后才变为 1；
- 内网部署日志为 `replica`，其本地 catalog 路径即使不存在也不会被读取。

批准目录的任何语义变化都必须重新走产品审核并更新 digest，不能原地伪装成同一 v1。
SQLite 灾难恢复使用部署前完整备份，不用 `alembic downgrade` 表达产品状态回滚。
