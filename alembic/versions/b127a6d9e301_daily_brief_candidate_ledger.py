"""Keep unprocessed daily brief candidates across cursor advances."""
from alembic import op
import sqlalchemy as sa

revision = "b127a6d9e301"
down_revision = "a101c7e4d932"
branch_labels = None
depends_on = None


def upgrade():
    if "daily_brief_candidates" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("daily_brief_candidates",
            sa.Column("article_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.CheckConstraint("status IN ('pending','processed')", name="ck_daily_brief_candidate_status"),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("article_id"))
        op.create_index("ix_daily_brief_candidates_status", "daily_brief_candidates", ["status"])


def downgrade():
    op.drop_table("daily_brief_candidates")
