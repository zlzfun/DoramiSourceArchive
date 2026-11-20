"""token delivery usage: per-day call metering for read tokens (issue #312)

令牌交付计量:读取类令牌(dfeed_ / dsub_)的调用按「日 × 账号 × 令牌 × 接口」聚合。
只加一张新表,不动既有表;各节点各记各的,不进 Archive Sync。

Revision ID: 3cfd474ffd83
Revises: a63f1d2c9b70
Create Date: 2026-11-20 09:35:47.088631

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


# revision identifiers, used by Alembic.
revision: str = '3cfd474ffd83'
down_revision: Union[str, Sequence[str], None] = 'a63f1d2c9b70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 老库收养路径(create_all 建过全量表再 stamp 基线、upgrade heads):表已在就不重建。
    if sa.inspect(op.get_bind()).has_table("token_delivery_usage"):
        return
    op.create_table('token_delivery_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('day', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('token_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('token_fp', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('token_preview', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('subscription_id', sa.Integer(), nullable=True),
    sa.Column('subscription_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('channel', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('items', sa.Integer(), nullable=False),
    sa.Column('rejected', sa.Integer(), nullable=False),
    sa.Column('last_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('token_delivery_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_token_delivery_usage_day'), ['day'], unique=False)
        batch_op.create_index(batch_op.f('ix_token_delivery_usage_token_fp'), ['token_fp'], unique=False)
        batch_op.create_index(batch_op.f('ix_token_delivery_usage_username'), ['username'], unique=False)
        batch_op.create_index('uq_token_delivery_usage_day_user_token_channel', ['day', 'username', 'token_fp', 'channel'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('token_delivery_usage', schema=None) as batch_op:
        batch_op.drop_index('uq_token_delivery_usage_day_user_token_channel')
        batch_op.drop_index(batch_op.f('ix_token_delivery_usage_username'))
        batch_op.drop_index(batch_op.f('ix_token_delivery_usage_token_fp'))
        batch_op.drop_index(batch_op.f('ix_token_delivery_usage_day'))

    op.drop_table('token_delivery_usage')
