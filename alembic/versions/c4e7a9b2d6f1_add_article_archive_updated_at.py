"""add article archive update cursor

Revision ID: c4e7a9b2d6f1
Revises: b5c1e3f7a9d2
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e7a9b2d6f1"
down_revision: Union[str, Sequence[str], None] = "b5c1e3f7a9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("articles")}
    if "archive_updated_at" not in columns:
        op.add_column(
            "articles",
            sa.Column(
                "archive_updated_at",
                sa.String(),
                nullable=False,
                server_default="",
            ),
        )

    # Existing rows were last changed when they were first fetched.  A separate
    # cursor keeps future metadata refreshes from disturbing reader/feed ordering.
    bind.execute(
        sa.text(
            "UPDATE articles SET archive_updated_at = fetched_date "
            "WHERE archive_updated_at IS NULL OR archive_updated_at = ''"
        )
    )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("articles")}
    if "ix_articles_archive_updated_at" not in indexes:
        op.create_index(
            "ix_articles_archive_updated_at",
            "articles",
            ["archive_updated_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("articles")}
    if "ix_articles_archive_updated_at" in indexes:
        op.drop_index("ix_articles_archive_updated_at", table_name="articles")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("articles")}
    if "archive_updated_at" in columns:
        op.drop_column("articles", "archive_updated_at")
