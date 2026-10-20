"""Durable local queue for optional paper HTML enrichment (issue #41)."""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "c41a8e20d610"
down_revision = "e5c1f7a2b938"
branch_labels = None
depends_on = None


def upgrade():
    # Runtime create_all() may already have created this table before an
    # unversioned database is adopted at the Alembic baseline.
    if not sa.inspect(op.get_bind()).has_table("paper_fulltext_jobs"):
        _create_table()
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("paper_fulltext_jobs")}
    if "ix_paper_fulltext_jobs_due" not in indexes:
        op.create_index("ix_paper_fulltext_jobs_due", "paper_fulltext_jobs", ["status", "retry_at"])


def _create_table():
    op.create_table(
        "paper_fulltext_jobs",
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("arxiv_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("abstract", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("original_body_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.Float(), nullable=False),
        sa.Column("lease_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','processing','succeeded','unavailable','failed','skipped')",
            name="ck_paper_fulltext_job_status",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )


def downgrade():
    op.drop_index("ix_paper_fulltext_jobs_due", table_name="paper_fulltext_jobs")
    op.drop_table("paper_fulltext_jobs")
