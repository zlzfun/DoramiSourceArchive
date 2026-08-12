"""drop articles.is_vectorized / index_status (RAG 退役清仓波 v3.31)

Revision ID: f2c9d4e07a11
Revises: 9548caa15eea
Create Date: 2026-08-12

向量子系统退役（docs/rag-retirement-plan.md Wave B）：ArticleRecord 的两个
向量索引状态列（布尔派生位 is_vectorized + 五态枚举 index_status）随之删除。
生产/开发两侧该两列的值全部为 False/pending（RAG 从未运行），删列零信息损失。

SQLite 注意事项（本迁移的全部小心之处）：
- 索引列不能原地 DROP COLUMN → 先显式删除两列的声明式索引（幂等判存在）；
- batch_alter_table 走「建新表→拷贝→删旧表→改名」，**旧表上的 FTS 同步
  triggers（articles_fts_ai/ad/au）会随旧表一起被删** → 收尾用 ensure_fts
  幂等补回（articles_fts 虚拟表本体不在 articles 上、不受影响；数据在迁移中
  未变，无需 rebuild 回填）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = 'f2c9d4e07a11'
down_revision: Union[str, Sequence[str], None] = '9548caa15eea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("is_vectorized", "index_status")
_INDEXES = ("ix_articles_is_vectorized", "ix_articles_index_status")


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing_columns = {c["name"] for c in insp.get_columns("articles")}
    existing_indexes = {ix["name"] for ix in insp.get_indexes("articles")}

    for index_name in _INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="articles")

    to_drop = [c for c in _COLUMNS if c in existing_columns]
    if to_drop:
        with op.batch_alter_table("articles", schema=None) as batch_op:
            for column_name in to_drop:
                batch_op.drop_column(column_name)

    # batch 重建表会连带删掉 articles 上的 FTS 同步 triggers；幂等补回。
    # （延迟导入：alembic 脚本目录扫描不经 env.py 的 sys.path 注入。）
    from storage.fts import ensure_fts

    ensure_fts(bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    existing_columns = {c["name"] for c in insp.get_columns("articles")}

    with op.batch_alter_table("articles", schema=None) as batch_op:
        if "is_vectorized" not in existing_columns:
            batch_op.add_column(sa.Column(
                "is_vectorized", sa.Boolean(), nullable=False, server_default=sa.text("0"),
            ))
        if "index_status" not in existing_columns:
            batch_op.add_column(sa.Column(
                "index_status", sa.VARCHAR(), nullable=False, server_default="pending",
            ))
    op.create_index("ix_articles_is_vectorized", "articles", ["is_vectorized"])
    op.create_index("ix_articles_index_status", "articles", ["index_status"])

    from storage.fts import ensure_fts

    ensure_fts(bind)
