"""add local Podcast audio artifact registry

Revision ID: c2d7e4f8a901
Revises: b8f1c2d3e4a5
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c2d7e4f8a901"
down_revision: Union[str, Sequence[str], None] = "b8f1c2d3e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index(name: str, columns: list[str]) -> None:
    existing = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes("podcast_artifacts")
    }
    if name not in existing:
        op.create_index(name, "podcast_artifacts", columns, unique=False)


def _assert_parent_downgrade_safe() -> None:
    """Run the downstream Archive Sync fence before this revision changes state.

    SQLite DDL is not transactional. Without this preflight, a refused downgrade
    through the parent would already have removed this table and moved the
    version marker one step, defeating the existing all-or-nothing guard.
    """

    bind = op.get_bind()
    for table, column in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
        ("podcast_artifacts", "authority_id"),
    ):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' LIMIT 1"
        )).first() is not None:
            raise RuntimeError(
                "拒绝降级 Archive Sync：数据库仍含远端 authority。"
                "请先停止 worker，并恢复升级前备份。"
            )
    if bind.execute(sa.text(
        "SELECT 1 FROM remote_candidate_evidence LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "拒绝降级 Archive Sync：数据库仍含远端 Candidate 证据。"
            "请先停止 worker，并恢复升级前备份。"
        )
    if bind.execute(sa.text(
        "SELECT 1 FROM personal_digest_editions WHERE desired_generation_reason IS NOT NULL "
        "OR desired_requested_at IS NOT NULL OR desired_first_open_at IS NOT NULL "
        "OR sync_stale = 1 OR analysis_incomplete = 1 LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "拒绝降级 Archive Sync：数据库仍含新版个人早报状态。"
            "请先停止 worker，并恢复升级前备份。"
        )
    marker = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:v2_consumer_mode'"
    )).scalar_one_or_none()
    schedule = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:schedule'"
    )).scalar_one_or_none()
    compact_schedule = str(schedule or "").replace(" ", "")
    if marker is not None or (
        '"enabled":true' in compact_schedule and '"protocol":"v2"' in compact_schedule
    ):
        raise RuntimeError(
            "拒绝降级 Archive Sync：consumer 围栏仍生效。"
            "请先停止 worker，并恢复升级前备份。"
        )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "podcast_artifacts" not in tables:
        op.create_table(
            "podcast_artifacts",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("mime", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("ext", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("duration_seconds", sa.Float(), nullable=True),
            sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("provenance", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column(
                "authority_id",
                sqlmodel.sql.sqltypes.AutoString(),
                server_default=sa.text("''"),
                nullable=False,
            ),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("published_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("withdrawn_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.CheckConstraint(
                "duration_seconds IS NULL OR duration_seconds >= 0",
                name="ck_podcast_artifacts_duration_seconds",
            ),
            sa.CheckConstraint(
                "kind IN ('source_audio','digest_audio_zh')",
                name="ck_podcast_artifacts_kind",
            ),
            sa.CheckConstraint(
                "size_bytes >= 0", name="ck_podcast_artifacts_size_bytes"
            ),
            sa.CheckConstraint(
                "status IN ('ready','published','withdrawn')",
                name="ck_podcast_artifacts_status",
            ),
            sa.ForeignKeyConstraint(["episode_id"], ["articles.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _index("ix_podcast_artifacts_authority_id", ["authority_id"])
    _index("ix_podcast_artifacts_content_hash", ["content_hash"])
    _index("ix_podcast_artifacts_episode_id", ["episode_id"])
    _index(
        "ix_podcast_artifacts_episode_kind_created",
        ["episode_id", "kind", "created_at"],
    )
    _index("ix_podcast_artifacts_kind", ["kind"])
    _index("ix_podcast_artifacts_status", ["status"])


def downgrade() -> None:
    _assert_parent_downgrade_safe()
    op.drop_index("ix_podcast_artifacts_status", table_name="podcast_artifacts")
    op.drop_index("ix_podcast_artifacts_kind", table_name="podcast_artifacts")
    op.drop_index(
        "ix_podcast_artifacts_episode_kind_created", table_name="podcast_artifacts"
    )
    op.drop_index("ix_podcast_artifacts_episode_id", table_name="podcast_artifacts")
    op.drop_index("ix_podcast_artifacts_content_hash", table_name="podcast_artifacts")
    op.drop_index("ix_podcast_artifacts_authority_id", table_name="podcast_artifacts")
    op.drop_table("podcast_artifacts")
