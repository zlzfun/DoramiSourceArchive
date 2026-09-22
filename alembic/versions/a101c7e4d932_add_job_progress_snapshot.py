"""Persist structured job progress separately from terminal results.

Revision ID: a101c7e4d932
Revises: d17e9a4c2b61
"""
from alembic import op
import sqlalchemy as sa

revision = "a101c7e4d932"
down_revision = "d17e9a4c2b61"
branch_labels = None
depends_on = None


def upgrade():
    # Runtime create_all may already have created the current jobs schema before
    # a legacy DB is adopted at the baseline and the full migration chain runs.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    if "progress_json" not in columns:
        op.add_column("jobs", sa.Column("progress_json", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("progress_json")
