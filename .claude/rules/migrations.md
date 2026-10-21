---
paths:
  - "src/models/**"
  - "alembic/**"
  - "src/storage/**"
---

# 迁移与存储硬规则(正文:docs/dev/migrations.md)

- 每次模型改动配 Alembic 迁移:`alembic revision --autogenerate -m "..."` 后人工审阅;`create_all()` 必须等于 `upgrade heads`(`tests/test_migrations.py::test_upgrade_head_has_no_drift_from_metadata`)。
- 口径一律 `heads`(下游带 `branch_labels` 的支线可长期并存,不做 alembic merge);收养路径勿加「探哨兵 stamp head」捷径(跳过数据语义迁移,`test_ensure_migrated_adopts_legacy_db` 是护栏)。
- SQLite 无原生 ALTER:`render_as_batch=True`;运行时连接 `PRAGMA foreign_keys=ON`,迁移期临时 OFF;表上挂归档同步触发器,删列需重建表并重装触发器。
- 给 Archive Sync 分析流加字段时 `archive_sync_v2.ANALYSIS_PAYLOAD_GENERATION` +1;改「系统记录」(日报 / 分析行)语义前先 grep 消费方。
- 文件锁一律经 `services/file_lock`,不裸 `import fcntl`;Windows 上先关句柄再 `os.replace` / `unlink`。
- FTS5 影子表与触发器在 `src/storage/fts.py`,运行期与迁移共享同一实现。
