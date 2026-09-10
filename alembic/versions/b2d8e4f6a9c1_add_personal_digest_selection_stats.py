"""add personal_digest_editions.selection_stats_json

Revision ID: b2d8e4f6a9c1
Revises: a4d7c9e2f610
Create Date: 2026-09-10

v3.53(issue #33 §3):编排说明行需要「候选总数」,此前只在选篇过程的内存里;
加一列 JSON 统计,生成时一次写入,历史版本保持 NULL(前端省略对应半句)。
带收养回放列守卫(create_all 出生的库已有该列)。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect


revision: str = "b2d8e4f6a9c1"
down_revision: Union[str, Sequence[str], None] = "a4d7c9e2f610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("personal_digest_editions")
    }
    if "selection_stats_json" in columns:
        return
    with op.batch_alter_table("personal_digest_editions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "selection_stats_json",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("personal_digest_editions", schema=None) as batch_op:
        batch_op.drop_column("selection_stats_json")
