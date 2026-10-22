"""guest visits (issue #213)

游客访问按天聚合表:一行 = 某天某位访客(当天打开次数 / 首个入口 / 转化)。
阅读与 AI 用量落在 reader_reads / ai_usage 的 guest:v:* 名下,不在本表。

Revision ID: 660028d4ab6f
Revises: b202a7e41c63
Create Date: 2026-10-22 21:46:48.741105

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


# revision identifiers, used by Alembic.
revision: str = '660028d4ab6f'
down_revision: Union[str, Sequence[str], None] = 'b202a7e41c63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if sa.inspect(op.get_bind()).has_table("guest_visits"):
        return
    op.create_table('guest_visits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('day', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('visitor_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('entry', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('visits', sa.Integer(), nullable=False),
    sa.Column('first_seen_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('last_seen_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('converted_username', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('converted_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('guest_visits', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_guest_visits_converted_at'), ['converted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_guest_visits_day'), ['day'], unique=False)
        batch_op.create_index(batch_op.f('ix_guest_visits_visitor_id'), ['visitor_id'], unique=False)
        batch_op.create_index('uq_guest_visits_day_visitor', ['day', 'visitor_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('guest_visits', schema=None) as batch_op:
        batch_op.drop_index('uq_guest_visits_day_visitor')
        batch_op.drop_index(batch_op.f('ix_guest_visits_visitor_id'))
        batch_op.drop_index(batch_op.f('ix_guest_visits_day'))
        batch_op.drop_index(batch_op.f('ix_guest_visits_converted_at'))
    op.drop_table('guest_visits')
