"""add users.session_epoch for session revocation

P0 安全收口(v3.40.4 审计 M04):users 加 session_epoch 会话世代列——登录 token
携带签发时的世代值,校验须与本列一致。密码重置/自助改密轮换 → 既有 Cookie 立即
吊销;建号随机初始化 → 删号后同名重建不复活旧 Cookie。存量行按 server_default
落空串:旧 token 无世代字段按 "" 对齐,升级不强制全员重登,首次改密后收紧。

Revision ID: b4a1c7e5d2f8
Revises: d9de7994582c
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4a1c7e5d2f8'
down_revision: Union[str, Sequence[str], None] = 'd9de7994582c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 收养回放守卫:create_all() 出生的新库(当前 metadata)已含本列,幂等跳过。
    insp = sa.inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("users")}
    if 'session_epoch' not in columns:
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'session_epoch',
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default='',
                )
            )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('session_epoch')
