"""Automatic title outbox, shared leases, system budget and Chinese title preference.

Revision ID: a218e6b9c402
Revises: d5b8e2a7c913
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "a218e6b9c402"
down_revision = "d5b8e2a7c913"
branch_labels = None
depends_on = None
S = sqlmodel.sql.sqltypes.AutoString


def upgrade():
    bind = op.get_bind()
    if "prefer_chinese_titles" not in {col["name"] for col in sa.inspect(bind).get_columns("users")}:
        op.add_column("users", sa.Column("prefer_chinese_titles", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    if not sa.inspect(bind).has_table("title_translation_jobs"):
        op.create_table("title_translation_jobs",
            sa.Column("article_id", S(), nullable=False), sa.Column("title_fp", S(), nullable=False),
            sa.Column("status", S(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("retry_at", sa.Float(), nullable=False), sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("error", S(), nullable=False),
            sa.CheckConstraint("status IN ('pending','running','succeeded','failed','skipped')", name="ck_title_translation_job_status"),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("article_id"))
        op.create_index("ix_title_translation_jobs_due", "title_translation_jobs", ["status", "retry_at"])
    if not sa.inspect(bind).has_table("title_translation_leases"):
        op.create_table("title_translation_leases",
            sa.Column("article_id", S(), nullable=False), sa.Column("title_fp", S(), nullable=False),
            sa.Column("token", S(), nullable=False), sa.Column("until", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("article_id"))
    if not sa.inspect(bind).has_table("title_translation_calls"):
        op.create_table("title_translation_calls",
            sa.Column("id", S(), nullable=False), sa.Column("article_id", S(), nullable=False),
            sa.Column("day", S(), nullable=False), sa.Column("reserved_tokens", sa.Integer(), nullable=False),
            sa.Column("status", S(), nullable=False), sa.Column("recorded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.CheckConstraint("status IN ('reserved','settled','unknown','released')", name="ck_title_translation_call_status"),
            sa.PrimaryKeyConstraint("id"))
        op.create_index("ix_title_translation_calls_budget", "title_translation_calls", ["day", "status"])


def downgrade():
    op.drop_table("title_translation_calls")
    op.drop_table("title_translation_leases")
    op.drop_table("title_translation_jobs")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("prefer_chinese_titles")
