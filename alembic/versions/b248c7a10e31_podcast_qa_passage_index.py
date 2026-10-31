"""Add rebuildable Podcast QA passage projections and FTS5."""

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "b248c7a10e31"
down_revision = "a218e6b9c402"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("podcast_qa_indexes"):
        op.create_table(
            "podcast_qa_indexes",
            sa.Column("artifact_id", sqlmodel.AutoString(), nullable=False),
            sa.Column("episode_id", sqlmodel.AutoString(), nullable=False),
            sa.Column("content_hash", sqlmodel.AutoString(), nullable=False),
            sa.Column("projection_version", sa.Integer(), nullable=False),
            sa.Column("valid", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["artifact_id"], ["podcast_text_artifacts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("artifact_id"),
        )
    if "ix_podcast_qa_indexes_episode_id" not in {index["name"] for index in sa.inspect(bind).get_indexes("podcast_qa_indexes")}:
        op.create_index("ix_podcast_qa_indexes_episode_id", "podcast_qa_indexes", ["episode_id"])
    if not sa.inspect(bind).has_table("podcast_qa_chunks"):
        op.create_table(
            "podcast_qa_chunks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("artifact_id", sqlmodel.AutoString(), nullable=False),
            sa.Column("episode_id", sqlmodel.AutoString(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("range_start", sa.Integer(), nullable=False),
            sa.Column("range_end", sa.Integer(), nullable=False),
            sa.Column("content", sqlmodel.AutoString(), nullable=False),
            sa.ForeignKeyConstraint(["artifact_id"], ["podcast_qa_indexes.artifact_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("artifact_id", "chunk_index", name="uq_podcast_qa_chunks_artifact_index"),
        )
    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("podcast_qa_chunks")}
    for field in ("artifact_id", "episode_id"):
        if f"ix_podcast_qa_chunks_{field}" not in existing_indexes:
            op.create_index(f"ix_podcast_qa_chunks_{field}", "podcast_qa_chunks", [field])
    from storage.podcast_qa_fts import ensure_podcast_qa_fts
    ensure_podcast_qa_fts(op.get_bind())


def downgrade():
    from storage.podcast_qa_fts import drop_podcast_qa_fts
    drop_podcast_qa_fts(op.get_bind())
    op.drop_table("podcast_qa_chunks")
    op.drop_table("podcast_qa_indexes")
