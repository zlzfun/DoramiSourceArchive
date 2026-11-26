"""add content-arrived generation reason for personal digest editions

Revision ID: c3e5a91f7d24
Revises: b8d4107c2f61
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5a91f7d24"
down_revision: Union[str, Sequence[str], None] = "b8d4107c2f61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("personal_digest_editions") as batch_op:
        batch_op.drop_constraint(
            "ck_personal_digest_editions_generation_reason", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_personal_digest_editions_generation_reason",
            "generation_reason IN ("
            "'scheduled','first_open','interest_changed','subscription_changed',"
            "'manual_rebuild','daily_brief_ready','recovery','content_arrived')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    # 旧约束不认识 content_arrived;它与 scheduled 同属系统编排,回退时并入。
    bind.execute(sa.text(
        "UPDATE personal_digest_editions SET generation_reason = 'scheduled' "
        "WHERE generation_reason = 'content_arrived'"
    ))
    with op.batch_alter_table("personal_digest_editions") as batch_op:
        batch_op.drop_constraint(
            "ck_personal_digest_editions_generation_reason", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_personal_digest_editions_generation_reason",
            "generation_reason IN ("
            "'scheduled','first_open','interest_changed','subscription_changed',"
            "'manual_rebuild','daily_brief_ready','recovery')",
        )
