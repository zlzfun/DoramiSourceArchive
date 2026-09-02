"""add full analysis backfill items

Revision ID: e4f7b2a9c6d1
Revises: c91e50f4a8d2
Create Date: 2026-09-02 16:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect


revision: str = "e4f7b2a9c6d1"
down_revision: Union[str, Sequence[str], None] = "c91e50f4a8d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tag_retag_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_tag_retag_jobs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tag_retag_jobs_status",
            "status IN ('queued','running','paused','succeeded','partial_failed','failed','cancelled')",
        )

    schema = inspect(op.get_bind())
    if "tag_retag_job_items" in schema.get_table_names():
        expected_columns = {
            "id",
            "job_id",
            "article_id",
            "article_id_snapshot",
            "status",
            "target_content_hash",
            "last_error",
            "queued_at",
            "completed_at",
            "created_at",
            "updated_at",
        }
        actual_columns = {
            column["name"] for column in schema.get_columns("tag_retag_job_items")
        }
        if actual_columns != expected_columns:
            raise RuntimeError(
                "partial tag_retag_job_items schema exists: "
                f"expected {sorted(expected_columns)}, got {sorted(actual_columns)}"
            )
        return

    op.create_table(
        "tag_retag_job_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("article_id_snapshot", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("queued_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("completed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','queued','succeeded','failed','skipped')",
            name="ck_tag_retag_job_items_status",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["tag_retag_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "article_id_snapshot", name="uq_tag_retag_job_items_job_article"),
    )
    with op.batch_alter_table("tag_retag_job_items", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tag_retag_job_items_job_status_id",
            ["job_id", "status", "id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("tag_retag_job_items")
    op.execute("UPDATE tag_retag_jobs SET status = 'cancelled' WHERE status = 'paused'")
    with op.batch_alter_table("tag_retag_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_tag_retag_jobs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tag_retag_jobs_status",
            "status IN ('queued','running','succeeded','partial_failed','failed','cancelled')",
        )
