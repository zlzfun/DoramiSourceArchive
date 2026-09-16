# 从 OSS 恢复为本地存储

`local` 表示停止云读写，不会替你恢复已回收的文件。已有 OSS 对象的节点必须完成下面的离线流程；从未启用 OSS 的普通节点无需执行。

## 恢复与预检

1. 备份数据库与必要回执，保留当前 OSS 连接配置和凭据。
2. 停止全部 API、调度器、worker 和缓存回收进程；整个恢复与切换过程保持停机。
3. 保持待回退的数据类型配置为 `oss`，恢复本地副本并检查结果：

```bash
export DORAMI_CONFIG_FILE=/path/to/config/production.ini
python scripts/migrate_media_oss.py restore --apply --offline > oss-restore.jsonl
python scripts/migrate_media_oss.py check-local > oss-local-check.jsonl
```

检查最后的 `summary`：必须同时满足 `errors = 0` 与 `local_ready = true`。
`check-local` 逐项读取本地文件，校验大小和 SHA-256，不访问云端，不修改数据库。
缺失、损坏或不安全的路径均会失败；不能把检查失败当成可忽略的告警。

## 收尾与切换

4. 仍保持所有进程停止，先查看收尾计划，再执行：

```bash
python scripts/migrate_media_oss.py finalize-local > oss-finalize-plan.jsonl
python scripts/migrate_media_oss.py finalize-local --apply --offline > oss-finalize.jsonl
```

实际执行会重新校验全部选中类型的业务引用。全部通过后，在同一数据库事务中删除这些类型的 OSS 位置索引；任一文件失败则不删除任何索引。
它不删除 OSS 对象、不修改文件、不修改 INI。没有现存业务引用的远端对象保留在桶中，原位置写入 JSONL，后续清理必须考虑保留备份的引用。
保存该报告与切换前数据库备份；检查 `summary.errors = 0`、`local_ready = true` 和 `removed_registry_records`。

5. 将 `[oss] media_backend` 与 `podcast_backend` 改为 `local`，同时检查对应环境变量未覆盖配置，再重启服务。
6. 验证图片、生成音频、Range 播放和 Archive Sync。确认不再依赖 OSS 后，才移除云凭据。

可以给每条命令增加 `--namespace media` 或 `--namespace podcast`，只恢复一种文件。收尾只移除所选类型的索引；另一类型仍按其配置运行。

`--offline` 是操作者对停止进程的确认，不会自动停机。预检通过以后，如果继续运行 OSS 进程或缓存回收，再次删除本地副本，先前检查结论就不再有效。

## 回退程序版本

上述操作是在当前版本中恢复为 local，不等同于回退程序版本。
OSS 的 Alembic 迁移仅在 `object_blobs` 为空时允许降至直接前一版本 `b715a91c4e02`；有任何对象索引时拒绝降级。
回退到更早版本仍受原有迁移边界约束，可能需要恢复对应的升级前数据库备份。不要手工清空对象索引来绕过文件恢复与校验。
