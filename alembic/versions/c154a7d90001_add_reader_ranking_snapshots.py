"""add reader ranking snapshots

Revision ID: c154a7d90001
Revises: b127a6d9e301
Create Date: 2026-09-24 07:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c154a7d90001"
down_revision = "b127a6d9e301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``DatabaseStorage`` intentionally creates the current ORM schema before
    # legacy databases are adopted at the Alembic baseline.  Therefore every
    # post-baseline table migration must also accept an already-current table.
    # This is not a blanket error swallow: missing children/indexes are still
    # reconciled one by one below.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("ranking_snapshots"):
        op.create_table(
            "ranking_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("snapshot_date", sa.String(), nullable=False),
            sa.Column("window_start", sa.String(), nullable=False),
            sa.Column("window_end", sa.String(), nullable=False),
            sa.Column("taxonomy_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("article_eligible_count", sa.Integer(), nullable=False),
            sa.Column("article_analyzed_count", sa.Integer(), nullable=False),
            sa.Column("article_tagged_count", sa.Integer(), nullable=False),
            sa.Column("podcast_eligible_count", sa.Integer(), nullable=False),
            sa.Column("podcast_analyzed_count", sa.Integer(), nullable=False),
            sa.Column("podcast_tagged_count", sa.Integer(), nullable=False),
            sa.Column("generated_at", sa.String(), nullable=False),
            sa.CheckConstraint(
                "status IN ('complete','degraded')",
                name="ck_ranking_snapshots_status",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_date", name="uq_ranking_snapshots_date"),
        )
    inspector = sa.inspect(op.get_bind())
    snapshot_indexes = {
        item["name"] for item in inspector.get_indexes("ranking_snapshots")
    }
    if "ix_ranking_snapshots_generated" not in snapshot_indexes:
        op.create_index("ix_ranking_snapshots_generated", "ranking_snapshots", ["generated_at"])

    if not inspector.has_table("ranking_tag_items"):
        op.create_table(
            "ranking_tag_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), nullable=False),
            sa.Column("shape", sa.String(), nullable=False),
            sa.Column("axis", sa.String(), nullable=False),
            sa.Column("tag_id", sa.Integer(), nullable=True),
            sa.Column("tag_code", sa.String(), nullable=False),
            sa.Column("tag_name_zh", sa.String(), nullable=False),
            sa.Column("tag_name_en", sa.String(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("occurrence_count", sa.Integer(), nullable=False),
            sa.Column("distinct_source_count", sa.Integer(), nullable=False),
            sa.Column("previous_rank", sa.Integer(), nullable=True),
            sa.Column("count_delta", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "shape IN ('article','podcast')",
                name="ck_ranking_tag_items_shape",
            ),
            sa.CheckConstraint(
                "axis IN ('topic','industry','entity')",
                name="ck_ranking_tag_items_axis",
            ),
            sa.ForeignKeyConstraint(
                ["snapshot_id"], ["ranking_snapshots.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["tag_id"], ["cms_tags.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "snapshot_id", "shape", "axis", "tag_code",
                name="uq_ranking_tag_items_snapshot_shape_axis_code",
            ),
        )
    inspector = sa.inspect(op.get_bind())
    tag_indexes = {item["name"] for item in inspector.get_indexes("ranking_tag_items")}
    if "ix_ranking_tag_items_snapshot_shape_axis_rank" not in tag_indexes:
        op.create_index(
            "ix_ranking_tag_items_snapshot_shape_axis_rank",
            "ranking_tag_items", ["snapshot_id", "shape", "axis", "rank"],
        )

    if not inspector.has_table("ranking_content_items"):
        op.create_table(
            "ranking_content_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), nullable=False),
            sa.Column("shape", sa.String(), nullable=False),
            sa.Column("axis", sa.String(), nullable=False),
            sa.Column("tag_code", sa.String(), nullable=False),
            sa.Column("article_id", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("content_rank", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("score_basis", sa.String(), nullable=False),
            sa.Column("appearance_count", sa.Integer(), nullable=False),
            sa.Column("is_must_read", sa.Boolean(), nullable=False),
            sa.Column("must_rank", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                "shape IN ('article','podcast')",
                name="ck_ranking_content_items_shape",
            ),
            sa.CheckConstraint(
                "axis IN ('topic','industry','entity')",
                name="ck_ranking_content_items_axis",
            ),
            sa.CheckConstraint(
                "score_basis IN ('article_body','show_notes','full_transcript')",
                name="ck_ranking_content_items_score_basis",
            ),
            sa.ForeignKeyConstraint(
                ["article_id"], ["articles.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["snapshot_id"], ["ranking_snapshots.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "snapshot_id", "shape", "axis", "tag_code", "article_id",
                name="uq_ranking_content_items_occurrence",
            ),
        )
    inspector = sa.inspect(op.get_bind())
    content_indexes = {
        item["name"] for item in inspector.get_indexes("ranking_content_items")
    }
    if "ix_ranking_content_items_snapshot_tag_rank" not in content_indexes:
        op.create_index(
            "ix_ranking_content_items_snapshot_tag_rank",
            "ranking_content_items", ["snapshot_id", "shape", "axis", "tag_code", "content_rank"],
        )
    if "ix_ranking_content_items_snapshot_must" not in content_indexes:
        op.create_index(
            "ix_ranking_content_items_snapshot_must",
            "ranking_content_items", ["snapshot_id", "shape", "is_must_read", "must_rank"],
        )


def downgrade() -> None:
    op.drop_table("ranking_content_items")
    op.drop_table("ranking_tag_items")
    op.drop_table("ranking_snapshots")
