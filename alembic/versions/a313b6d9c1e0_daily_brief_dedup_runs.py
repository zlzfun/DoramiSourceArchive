"""Record node-local cross-day decisions in the brief publication transaction."""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "a313b6d9c1e0"
down_revision = "a63f1d2c9b70"
branch_labels = None
depends_on = None


def upgrade():
    table = "daily_brief_dedup_runs"
    if not sa.inspect(op.get_bind()).has_table(table):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("report_date", sqlmodel.AutoString(), nullable=False),
            sa.Column("started_at", sqlmodel.AutoString(), nullable=False),
            sa.Column("trigger", sqlmodel.AutoString(), nullable=False),
            sa.Column("snapshot_json", sqlmodel.AutoString(), nullable=False),
        )
    index = "ix_daily_brief_dedup_runs_report_date"
    if index not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(index, table, ["report_date"])


def downgrade():
    op.drop_table("daily_brief_dedup_runs")
