"""add owner_username to source_configs for user custom sources

用户自定 RSS 源波(v3.40):source_configs 加 owner_username 列——非空=读者自助
添加的私有源的创建者,空=平台源。存量行按 server_default 落空串,行为零变化。

Revision ID: d9de7994582c
Revises: f2c9d4e07a11
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd9de7994582c'
down_revision: Union[str, Sequence[str], None] = 'f2c9d4e07a11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 收养回放守卫:create_all() 出生的新库(当前 metadata)已含本列/索引,幂等跳过。
    insp = sa.inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("source_configs")}
    indexes = {ix["name"] for ix in insp.get_indexes("source_configs")}
    with op.batch_alter_table('source_configs', schema=None) as batch_op:
        if 'owner_username' not in columns:
            batch_op.add_column(
                sa.Column(
                    'owner_username',
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default='',
                )
            )
        if 'ix_source_configs_owner_username' not in indexes:
            batch_op.create_index(
                batch_op.f('ix_source_configs_owner_username'), ['owner_username'], unique=False
            )


def downgrade() -> None:
    with op.batch_alter_table('source_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_source_configs_owner_username'))
        batch_op.drop_column('owner_username')
