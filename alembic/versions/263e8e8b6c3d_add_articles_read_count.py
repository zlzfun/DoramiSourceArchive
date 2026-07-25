"""add articles.read_count

Revision ID: 263e8e8b6c3d
Revises: a1f4c9d2e3b7
Create Date: 2026-07-25 10:03:29.976718

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '263e8e8b6c3d'
down_revision: Union[str, Sequence[str], None] = 'a1f4c9d2e3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """加 articles.read_count（全站累计阅读次数）；对已有列幂等跳过。

    带 server_default='0' 以兼容存量行（NOT NULL 新列在有数据表上必需）。
    autogenerate 同时探出的一批 nullable 对账噪声（旧 create_all 时代的历史列）
    与本变更无关，已裁剪不收。
    """
    insp = inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("articles")}
    if "read_count" not in columns:
        with op.batch_alter_table('articles', schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                'read_count', sa.Integer(), nullable=False, server_default='0',
            ))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('articles', schema=None) as batch_op:
        batch_op.drop_column('read_count')
