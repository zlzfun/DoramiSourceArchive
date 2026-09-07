"""add immutable Podcast text artifacts and publication slots

Revision ID: 6c1f8a2d4e90
Revises: 4d8e1b7c3a90
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "6c1f8a2d4e90"
down_revision: Union[str, Sequence[str], None] = "4d8e1b7c3a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PODCAST_TEXT_TRIGGERS = (
    "archive_sync_podcast_text_insert",
    "archive_sync_podcast_text_update",
    "archive_sync_podcast_text_scope_exit",
    "archive_sync_podcast_text_scope_enter",
    "archive_sync_podcast_text_delete",
    "podcast_text_publication_identity_immutable",
    "podcast_text_artifact_immutable_update",
    "podcast_text_artifact_immutable_delete",
)


def _archive_state_supports_podcast_texts() -> bool:
    checks = sa.inspect(op.get_bind()).get_check_constraints(
        "archive_sync_entity_states"
    )
    return any(
        item.get("name") == "ck_archive_sync_entity_states_stream"
        and "podcast_texts" in str(item.get("sqltext") or "")
        for item in checks
    )


def _set_archive_state_stream_constraint(*, include_podcast_texts: bool) -> None:
    values = "'sources','articles','analyses','media','source_states'"
    if include_podcast_texts:
        values += ",'podcast_texts'"
    with op.batch_alter_table(
        "archive_sync_entity_states", recreate="always"
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_archive_sync_entity_states_stream", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_archive_sync_entity_states_stream",
            f"stream IN ({values})",
        )


def _create_artifact_table() -> None:
    op.create_table(
        "podcast_text_artifacts",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("inline_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "authority_id",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("source_artifact_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("rights_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provenance_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash = lower(content_hash) "
            "AND content_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_podcast_text_artifacts_content_hash",
        ),
        sa.CheckConstraint(
            "kind IN ('publisher_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
            name="ck_podcast_text_artifacts_kind",
        ),
        sa.CheckConstraint(
            "length(language) > 0", name="ck_podcast_text_artifacts_language"
        ),
        sa.CheckConstraint(
            "length(provenance_json) > 0",
            name="ck_podcast_text_artifacts_provenance",
        ),
        sa.CheckConstraint(
            "length(rights_version) > 0",
            name="ck_podcast_text_artifacts_rights_version",
        ),
        sa.CheckConstraint(
            "source_content_hash IS NULL OR (length(source_content_hash) = 64 "
            "AND source_content_hash = lower(source_content_hash) "
            "AND source_content_hash NOT GLOB '*[^0-9a-f]*')",
            name="ck_podcast_text_artifacts_source_content_hash",
        ),
        sa.CheckConstraint(
            "length(inline_text) > 0", name="ck_podcast_text_artifacts_text"
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_podcast_text_artifacts_version"
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["articles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "episode_id",
            "kind",
            "version",
            name="uq_podcast_text_artifacts_episode_kind_version",
        ),
        sa.UniqueConstraint(
            "id",
            "episode_id",
            "kind",
            name="uq_podcast_text_artifacts_pointer_slot",
        ),
    )
    op.create_index(
        "ix_podcast_text_artifacts_authority_id",
        "podcast_text_artifacts",
        ["authority_id"],
    )
    op.create_index(
        "ix_podcast_text_artifacts_content_hash",
        "podcast_text_artifacts",
        ["content_hash"],
    )
    op.create_index(
        "ix_podcast_text_artifacts_episode_id",
        "podcast_text_artifacts",
        ["episode_id"],
    )
    op.create_index(
        "ix_podcast_text_artifacts_episode_kind_created",
        "podcast_text_artifacts",
        ["episode_id", "kind", "created_at"],
    )
    op.create_index(
        "ix_podcast_text_artifacts_kind", "podcast_text_artifacts", ["kind"]
    )
    op.create_index(
        "ix_podcast_text_artifacts_source_artifact_id",
        "podcast_text_artifacts",
        ["source_artifact_id"],
    )
    op.create_index(
        "ix_podcast_text_artifacts_source_content_hash",
        "podcast_text_artifacts",
        ["source_content_hash"],
    )


def _create_publication_table() -> None:
    op.create_table(
        "podcast_text_publications",
        sa.Column("identity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("artifact_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "authority_id",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("published_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("unpublished_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "identity = episode_id || ':' || kind",
            name="ck_podcast_text_publications_identity",
        ),
        sa.CheckConstraint(
            "kind IN ('publisher_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
            name="ck_podcast_text_publications_kind",
        ),
        sa.CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL",
            name="ck_podcast_text_publications_published_at",
        ),
        sa.CheckConstraint(
            "status IN ('published','unpublished')",
            name="ck_podcast_text_publications_status",
        ),
        sa.CheckConstraint(
            "status <> 'unpublished' OR unpublished_at IS NOT NULL",
            name="ck_podcast_text_publications_unpublished_at",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "episode_id", "kind"],
            [
                "podcast_text_artifacts.id",
                "podcast_text_artifacts.episode_id",
                "podcast_text_artifacts.kind",
            ],
            name="fk_podcast_text_publications_artifact_slot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["articles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("identity"),
        sa.UniqueConstraint(
            "episode_id",
            "kind",
            name="uq_podcast_text_publications_episode_kind",
        ),
    )
    op.create_index(
        "ix_podcast_text_publications_artifact_id",
        "podcast_text_publications",
        ["artifact_id"],
    )
    op.create_index(
        "ix_podcast_text_publications_authority_id",
        "podcast_text_publications",
        ["authority_id"],
    )
    op.create_index(
        "ix_podcast_text_publications_episode_id",
        "podcast_text_publications",
        ["episode_id"],
    )
    op.create_index(
        "ix_podcast_text_publications_kind", "podcast_text_publications", ["kind"]
    )
    op.create_index(
        "ix_podcast_text_publications_status",
        "podcast_text_publications",
        ["status"],
    )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "podcast_text_artifacts" not in tables:
        _create_artifact_table()
    if "podcast_text_publications" not in tables:
        _create_publication_table()
    if not _archive_state_supports_podcast_texts():
        # Historical b8 installs triggers during its own upgrade, after env.py's
        # one-time preflight. Drop them again before recreating the state table.
        drop_archive_sync_revision_triggers(op.get_bind())
        _set_archive_state_stream_constraint(include_podcast_texts=True)
    install_archive_sync_revision_triggers(
        op.get_bind(), include_podcast_integrity=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _archive_state_supports_podcast_texts():
        # SQLite implements the constraint change by replacing the whole table.
        # Every Archive Sync trigger references that table, so keeping even a
        # source/article trigger installed makes the final table rename fail.
        drop_archive_sync_revision_triggers(bind)
        bind.execute(sa.text(
            "DELETE FROM archive_sync_entity_states WHERE stream = 'podcast_texts'"
        ))
        _set_archive_state_stream_constraint(include_podcast_texts=False)
    else:
        for name in _PODCAST_TEXT_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    tables = set(sa.inspect(bind).get_table_names())
    if "podcast_text_publications" in tables:
        op.drop_table("podcast_text_publications")
    if "podcast_text_artifacts" in tables:
        op.drop_table("podcast_text_artifacts")
    install_archive_sync_revision_triggers(
        bind, include_podcast_integrity=False
    )
