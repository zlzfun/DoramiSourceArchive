"""add daily-brief readiness generation reason

Revision ID: a4b9d2e6f1c3
Revises: f3a8c1d9e2b4
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b9d2e6f1c3"
down_revision: Union[str, Sequence[str], None] = "f3a8c1d9e2b4"
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
            "'manual_rebuild','daily_brief_ready','recovery')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE personal_digest_editions SET generation_reason = 'subscription_changed' "
        "WHERE generation_reason = 'daily_brief_ready'"
    ))
    with op.batch_alter_table("personal_digest_editions") as batch_op:
        batch_op.drop_constraint(
            "ck_personal_digest_editions_generation_reason", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_personal_digest_editions_generation_reason",
            "generation_reason IN ("
            "'scheduled','first_open','interest_changed','subscription_changed',"
            "'manual_rebuild','recovery')",
        )
