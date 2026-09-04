"""drop article_analyses.one_sentence_summary (issue #13 摘要复用与精简)

Revision ID: d8b3f1a6c9e2
Revises: c4e7a9b2d6f1
Create Date: 2026-09-04

v3.45.1：文章分析一次调用的产出精简为「一句短理由 + 唯一 summary」。
one_sentence_summary 的消费方（个人早报条目行 / 公共日报 adapter 空兜底）
都已有 summary 回退，删列零信息损失；历史 edition 快照是不可变 JSON，
其中仍带该键，不受本迁移影响。

SQLite 无原地 DROP COLUMN → batch_alter_table 建新表拷贝。该表没有 FTS
trigger，无需补回。列存在性判断兼容「create_all 新库被收养回放」路径。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "d8b3f1a6c9e2"
down_revision: Union[str, Sequence[str], None] = "c4e7a9b2d6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "article_analyses"
_COLUMN = "one_sentence_summary"


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    if _COLUMN in columns:
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.drop_column(_COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in columns:
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(_COLUMN, sa.VARCHAR(), nullable=False, server_default="")
            )
