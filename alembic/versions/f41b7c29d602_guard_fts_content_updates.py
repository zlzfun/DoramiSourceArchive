"""Only reindex articles whose searchable content or rowid changed."""
from alembic import op

revision = "f41b7c29d602"
down_revision = "c41a8e20d610"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        from storage.fts import replace_fts_update_trigger
        replace_fts_update_trigger(op.get_bind())


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        from storage.fts import replace_fts_update_trigger
        replace_fts_update_trigger(op.get_bind(), changed_only=False)
