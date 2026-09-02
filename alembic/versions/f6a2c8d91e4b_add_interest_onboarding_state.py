"""add interest onboarding state

Revision ID: f6a2c8d91e4b
Revises: e4f7b2a9c6d1
Create Date: 2026-09-02 18:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect


revision: str = "f6a2c8d91e4b"
down_revision: Union[str, Sequence[str], None] = "e4f7b2a9c6d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("users")
    }
    if "interest_onboarding_completed_at" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "interest_onboarding_completed_at",
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=True,
                )
            )

    # 存量账号不应在发布后突然看到首次引导；只有迁移后创建的普通账号从未完成开始。
    op.execute(
        "UPDATE users SET interest_onboarding_completed_at = updated_at "
        "WHERE interest_onboarding_completed_at IS NULL"
    )
    # 产品收敛为关注/屏蔽两种显式状态，旧重点关注等价迁移为普通关注。
    op.execute("UPDATE user_interest_tags SET priority = 'normal' WHERE priority = 'high'")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("interest_onboarding_completed_at")
