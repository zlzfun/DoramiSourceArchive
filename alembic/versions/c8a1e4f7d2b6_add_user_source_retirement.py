"""Add explicit retirement state for audit-bound user sources.

Revision ID: c8a1e4f7d2b6
Revises: b715a91c4e02
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "c8a1e4f7d2b6"
down_revision = "b715a91c4e02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("source_configs")}
    if "retired_at" not in columns:
        op.add_column(
            "source_configs",
            sa.Column(
                "retired_at",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("source_configs")}
    if "ix_source_configs_retired_at" not in indexes:
        op.create_index(
            "ix_source_configs_retired_at",
            "source_configs",
            ["retired_at"],
            unique=False,
        )


def downgrade():
    # The parent revision already sits above the repository's one-way migration
    # boundary. Reject before touching this revision's DDL so a failed downgrade
    # cannot leave the database one revision behind head.
    raise RuntimeError(
        "User-source retirement sits above a one-way boundary; "
        "restore the pre-upgrade database to downgrade"
    )
