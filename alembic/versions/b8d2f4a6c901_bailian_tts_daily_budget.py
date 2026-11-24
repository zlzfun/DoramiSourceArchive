"""Track TTS authorization days for daily budget limits (issue #298).

Historical created_at is not an authorization timestamp: reserved chunks may
have been submitted on a later day. Keep their day unknown, rather than invent
billing dates. When daily limits are enabled, unknown days count conservatively
until operator reconciliation. Receipt identities and costs are never reset.

Revision ID: b8d2f4a6c901
Revises: a63f1d2c9b70
"""

from alembic import op
import sqlalchemy as sa

revision = "b8d2f4a6c901"
down_revision = "a63f1d2c9b70"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy bootstrap adoption may already have current create_all() metadata.
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("bailian_tts_calls")}
    if "budget_day" not in columns:
        op.add_column("bailian_tts_calls", sa.Column("budget_day", sa.String(), nullable=False, server_default=""))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("bailian_tts_calls")}
    if "ix_bailian_tts_scope_day" not in indexes:
        op.create_index("ix_bailian_tts_scope_day", "bailian_tts_calls", ["account_scope", "budget_day"])


def downgrade():
    # Operational rollback uses a backup; this schema downgrade removes only
    # the new projection, never paid-call identities, amounts or receipts.
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("bailian_tts_calls")}
    if "ix_bailian_tts_scope_day" in indexes:
        op.drop_index("ix_bailian_tts_scope_day", table_name="bailian_tts_calls")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("bailian_tts_calls")}
    if "budget_day" in columns:
        op.drop_column("bailian_tts_calls", "budget_day")
