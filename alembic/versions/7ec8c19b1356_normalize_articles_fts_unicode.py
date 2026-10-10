"""rebuild articles FTS5 index with Unicode normalization (issue #94)

Revision ID: 7ec8c19b1356
Revises: d4e8a1b7c603
Create Date: 2026-10-09 16:40:00

trigram 按原始字符匹配：标题里的排版变体（如 U+2011 非破坏性连字符 `‑`）与键盘
输入的 ASCII `-` 互相搜不中。`storage.fts` 现在两端归一化（查询词 Python 端、
索引内容 trigger 内 REPLACE 链），但存量库里的 trigger 与索引仍是旧的原文形式。

upgrade：整表重建（drop 旧 trigger + 虚拟表 → 按归一化 DDL 重建 → 归一化回填）。
旧 trigger 不能就地替换——external-content 的 'delete' 必须带入索引时的原值，
新旧形式混用会让索引残留脏 token。

downgrade：恢复归一化之前的 trigger 与原文索引（本文件内自带旧 DDL，不依赖
运行期代码；回填用 FTS5 'rebuild'，正是旧实现的方式）。

仅 SQLite；FTS 不可用（老 SQLite 无 trigram）时两向均为零操作，与 a1f4c9d2e3b7 一致。
"""

from alembic import op


revision = "7ec8c19b1356"
down_revision = "d4e8a1b7c603"
branch_labels = None
depends_on = None


_FTS = "articles_fts"

# 归一化之前（a1f4c9d2e3b7 ~ d4e8a1b7c603）的原文 DDL，仅供 downgrade 使用。
_LEGACY_CREATE = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS} USING fts5("
    "title, content, content='articles', content_rowid='rowid', tokenize='trigram')"
)
_LEGACY_TRIGGERS = (
    f"""CREATE TRIGGER IF NOT EXISTS {_FTS}_ai AFTER INSERT ON articles BEGIN
  INSERT INTO {_FTS}(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {_FTS}_ad AFTER DELETE ON articles BEGIN
  INSERT INTO {_FTS}({_FTS}, rowid, title, content) VALUES('delete', old.rowid, old.title, old.content);
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {_FTS}_au AFTER UPDATE ON articles BEGIN
  INSERT INTO {_FTS}({_FTS}, rowid, title, content) VALUES('delete', old.rowid, old.title, old.content);
  INSERT INTO {_FTS}(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END""",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    # 延迟导入：alembic 脚本目录扫描不经 env.py 的 sys.path 注入。
    from storage.fts import fts_available, rebuild_fts_normalized

    if not fts_available(bind):
        return
    rebuild_fts_normalized(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    from storage.fts import drop_fts, fts_available

    if not fts_available(bind):
        return
    drop_fts(bind)
    bind.exec_driver_sql(_LEGACY_CREATE)
    for ddl in _LEGACY_TRIGGERS:
        bind.exec_driver_sql(ddl)
    bind.exec_driver_sql(f"INSERT INTO {_FTS}({_FTS}) VALUES('rebuild')")
