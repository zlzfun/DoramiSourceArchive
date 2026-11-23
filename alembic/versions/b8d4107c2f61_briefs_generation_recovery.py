"""persist bounded brief attempts and frozen v1 bot projection

Revision ID: b8d4107c2f61
Revises: 9b7e2c4d1f60
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8d4107c2f61"
down_revision: Union[str, Sequence[str], None] = "9b7e2c4d1f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STRING_COLUMNS = (
    "generation_started_at",
    "generation_heartbeat_at",
    "generation_deadline_at",
    "briefs_payload_json",
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("personal_digest_editions")
    }
    if "generation_attempt_count" not in columns:
        op.add_column(
            "personal_digest_editions",
            sa.Column(
                "generation_attempt_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    for name in STRING_COLUMNS:
        if name not in columns:
            op.add_column(
                "personal_digest_editions",
                sa.Column(name, sa.String(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("personal_digest_editions")
    }
    to_drop = [
        name
        for name in (*reversed(STRING_COLUMNS), "generation_attempt_count")
        if name in columns
    ]
    if to_drop:
        with op.batch_alter_table("personal_digest_editions", recreate="always") as batch:
            for name in to_drop:
                batch.drop_column(name)
