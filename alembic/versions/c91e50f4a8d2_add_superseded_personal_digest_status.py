"""add superseded personal digest lifecycle status

Revision ID: c91e50f4a8d2
Revises: b7e29d4f6a30
Create Date: 2026-09-01 23:40:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c91e50f4a8d2"
down_revision: Union[str, Sequence[str], None] = "b7e29d4f6a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("personal_digest_editions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_personal_digest_editions_status", type_="check")
        batch_op.create_check_constraint(
            "ck_personal_digest_editions_status",
            "status IN ('pending','generating','ready','degraded','failed','superseded')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE personal_digest_editions SET status = 'failed', "
        "error = COALESCE(error, 'superseded before schema downgrade') "
        "WHERE status = 'superseded'"
    )
    with op.batch_alter_table("personal_digest_editions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_personal_digest_editions_status", type_="check")
        batch_op.create_check_constraint(
            "ck_personal_digest_editions_status",
            "status IN ('pending','generating','ready','degraded','failed')",
        )
