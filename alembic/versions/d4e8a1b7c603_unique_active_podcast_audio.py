"""keep one active published podcast audio per episode

Revision ID: d4e8a1b7c603
Revises: c154a7d90001
Create Date: 2026-09-24 22:10:00
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "d4e8a1b7c603"
down_revision = "c154a7d90001"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_podcast_artifacts_active_episode"
PUBLISHED_PREDICATE = "kind = 'digest_audio_zh' AND status = 'published'"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("podcast_artifacts"):
        return

    # Historical orchestration could publish the same narration more than once.
    # Preserve the newest publication and retain older rows as withdrawn audit
    # history before installing the database-level invariant.
    now = datetime.now(timezone.utc).isoformat()
    bind.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT id, ROW_NUMBER() OVER ("
            "  PARTITION BY episode_id ORDER BY "
            "  CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,"
            "  published_at DESC, created_at DESC, id DESC"
            " ) AS position"
            " FROM podcast_artifacts"
            " WHERE kind = 'digest_audio_zh' AND status = 'published'"
            ") "
            "UPDATE podcast_artifacts SET status = 'withdrawn', "
            "withdrawn_at = :now, updated_at = :now "
            "WHERE id IN (SELECT id FROM ranked WHERE position > 1)"
        ),
        {"now": now},
    )

    indexes = {item["name"] for item in inspector.get_indexes("podcast_artifacts")}
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "podcast_artifacts",
            ["episode_id"],
            unique=True,
            sqlite_where=sa.text(PUBLISHED_PREDICATE),
            postgresql_where=sa.text(PUBLISHED_PREDICATE),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("podcast_artifacts") and INDEX_NAME in {
        item["name"] for item in inspector.get_indexes("podcast_artifacts")
    }:
        op.drop_index(INDEX_NAME, table_name="podcast_artifacts")
