"""multi_admin_audit_and_default_surface

Revision ID: 6cb03105eec2
Revises: 5f1e4b31e27a
Create Date: 2026-07-24 10:29:40.398059

v3.19 管理员平权波:
- users 加 default_surface（登录默认落地界面 console|reader，server_default=console 兼容存量行）;
- 新表 admin_audit_logs（管理操作审计流）。

（autogenerate 在本机漂移开发库上会额外产出一批 NOT NULL alter 噪声——那是历史
手写 ALTER 遗留的既有漂移，与本次变更无关，已剔除;全新库 upgrade head 与 metadata
零漂移由 tests/test_migrations.py 裁决。）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '6cb03105eec2'
down_revision: Union[str, Sequence[str], None] = '5f1e4b31e27a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    insp = inspect(op.get_bind())

    # users.default_surface：带 server_default 以兼容存量行（NOT NULL 新列在有数据表上需默认值）。
    columns = {c["name"] for c in insp.get_columns("users")}
    if "default_surface" not in columns:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                'default_surface', sqlmodel.sql.sqltypes.AutoString(),
                nullable=False, server_default='console',
            ))

    # admin_audit_logs 新表（幂等守卫,同仓内约定:运行期 create_all 可能抢先建好——老库收养场景）。
    existing = set(inspect(op.get_bind()).get_table_names())
    if "admin_audit_logs" not in existing:
        op.create_table('admin_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('method', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('path', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('target', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('admin_audit_logs', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_admin_audit_logs_at'), ['at'], unique=False)
            batch_op.create_index(batch_op.f('ix_admin_audit_logs_target'), ['target'], unique=False)
            batch_op.create_index(batch_op.f('ix_admin_audit_logs_username'), ['username'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('admin_audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_audit_logs_username'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_logs_target'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_logs_at'))
    op.drop_table('admin_audit_logs')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('default_surface')
