"""bind local digest audio to its exact published narration script

Revision ID: 8f3b2d1c7a90
Revises: 1d7c9a4e2b60
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "8f3b2d1c7a90"
down_revision: Union[str, Sequence[str], None] = "1d7c9a4e2b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TRIGGERS = (
    "podcast_audio_dependency_insert",
    "podcast_audio_dependency_update",
    "podcast_audio_processing_insert",
    "podcast_audio_processing_update",
    "podcast_audio_binding_immutable",
    "podcast_script_audio_invalidate_update",
    "podcast_script_audio_invalidate_delete",
)


def _install_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_audio_dependency_trigger_sql

        statements = _podcast_audio_dependency_trigger_sql(
            require_processing_narration=False,
            include_source_cache_fields=False,
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        statements = _podcast_audio_dependency_postgresql_sql(
            require_processing_narration=False,
            include_source_cache_fields=False,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _drop_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for name in _TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
        return
    if bind.dialect.name == "postgresql":
        for name, table in (
            ("podcast_audio_dependency_insert", "podcast_artifacts"),
            ("podcast_audio_dependency_update", "podcast_artifacts"),
            ("podcast_script_audio_invalidate_update", "podcast_text_publications"),
            ("podcast_script_audio_invalidate_delete", "podcast_text_publications"),
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')
        bind.exec_driver_sql(
            "DROP FUNCTION IF EXISTS podcast_audio_dependency_validate_fn()"
        )
        bind.exec_driver_sql(
            "DROP FUNCTION IF EXISTS podcast_script_audio_invalidate_fn()"
        )


def _assert_archive_sync_downgrade_safe() -> None:
    """Refuse before SQLite advances this head into an unsafe downgrade."""

    bind = op.get_bind()
    for table, column in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
        ("podcast_text_artifacts", "authority_id"),
        ("podcast_text_publications", "authority_id"),
    ):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} <> '' LIMIT 1"
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
        "SELECT 1 FROM personal_digest_editions "
        "WHERE desired_generation_reason IS NOT NULL "
        "OR desired_requested_at IS NOT NULL OR desired_first_open_at IS NOT NULL "
        "OR sync_stale IS TRUE OR analysis_incomplete IS TRUE LIMIT 1"
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
        '"enabled":true' in compact_schedule
        and '"protocol":"v2"' in compact_schedule
    ):
        raise RuntimeError(
            "拒绝降级 Archive Sync：consumer 围栏仍生效。"
            "请先停止 worker，并恢复升级前备份。"
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("podcast_artifacts")
    }
    dependency_columns = {
        "narration_artifact_id",
        "narration_content_hash",
        "processing_id",
    }
    if dependency_columns.issubset(columns):
        # Fresh/create_all databases already contain the current model.
        _install_triggers()
        return
    if dependency_columns & columns:
        raise RuntimeError(
            "partial Podcast narration dependency schema exists; restore a "
            "consistent backup before retrying migration"
        )

    op.add_column(
        "podcast_artifacts",
        sa.Column(
            "narration_artifact_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "podcast_artifacts",
        sa.Column(
            "narration_content_hash",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "podcast_artifacts",
        sa.Column(
            "processing_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )

    # Historical digest rows cannot prove which immutable script generated
    # their bytes. Preserve both registry and blob, but fail closed immediately.
    op.execute(
        sa.text(
            "UPDATE podcast_artifacts "
            "SET status = 'withdrawn', "
            "withdrawn_at = COALESCE(withdrawn_at, "
            "CAST(CURRENT_TIMESTAMP AS TEXT)), "
            "updated_at = CAST(CURRENT_TIMESTAMP AS TEXT) "
            "WHERE kind = 'digest_audio_zh' AND status <> 'withdrawn'"
        )
    )

    with op.batch_alter_table("podcast_artifacts", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_podcast_artifacts_narration_pair",
            "(narration_artifact_id IS NULL) = "
            "(narration_content_hash IS NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_narration_hash",
            "narration_content_hash IS NULL OR "
            "(length(narration_content_hash) = 64 AND "
            "narration_content_hash = lower(narration_content_hash))",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_source_has_no_narration",
            "kind <> 'source_audio' OR "
            "(narration_artifact_id IS NULL AND narration_content_hash IS NULL "
            "AND processing_id IS NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_active_digest_has_narration",
            "kind <> 'digest_audio_zh' OR status = 'withdrawn' OR "
            "(narration_artifact_id IS NOT NULL AND "
            "narration_content_hash IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_podcast_artifacts_narration_artifact",
            "podcast_text_artifacts",
            ["narration_artifact_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_podcast_artifacts_processing",
            "podcast_processings",
            ["processing_id"],
            ["id"],
            ondelete="SET NULL",
        )

    for name, column in (
        ("ix_podcast_artifacts_narration_artifact_id", "narration_artifact_id"),
        ("ix_podcast_artifacts_narration_content_hash", "narration_content_hash"),
        ("ix_podcast_artifacts_processing_id", "processing_id"),
    ):
        op.create_index(name, "podcast_artifacts", [column], unique=False)
    predicate = sa.text(
        "processing_id IS NOT NULL AND kind = 'digest_audio_zh'"
    )
    op.create_index(
        "uq_podcast_artifacts_digest_processing",
        "podcast_artifacts",
        ["processing_id"],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )
    _install_triggers()


def downgrade() -> None:
    _assert_archive_sync_downgrade_safe()
    lossy_audio = op.get_bind().execute(sa.text(
        "SELECT 1 FROM podcast_artifacts "
        "WHERE narration_artifact_id IS NOT NULL "
        "OR narration_content_hash IS NOT NULL "
        "OR processing_id IS NOT NULL "
        "OR (kind = 'digest_audio_zh' AND status IN ('ready','published')) "
        "LIMIT 1"
    )).first()
    if lossy_audio is not None:
        raise RuntimeError(
            "拒绝降级 Podcast 音频依赖：现有音频会丢失口播稿或处理任务绑定。"
            "请先撤下并导出相关记录，然后恢复升级前备份。"
        )
    _drop_triggers()
    for name in (
        "uq_podcast_artifacts_digest_processing",
        "ix_podcast_artifacts_processing_id",
        "ix_podcast_artifacts_narration_content_hash",
        "ix_podcast_artifacts_narration_artifact_id",
    ):
        op.drop_index(name, table_name="podcast_artifacts")
    with op.batch_alter_table("podcast_artifacts", recreate="always") as batch:
        batch.drop_constraint(
            "fk_podcast_artifacts_processing", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_podcast_artifacts_narration_artifact", type_="foreignkey"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_active_digest_has_narration", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_source_has_no_narration", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_narration_hash", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_narration_pair", type_="check"
        )
        batch.drop_column("processing_id")
        batch.drop_column("narration_content_hash")
        batch.drop_column("narration_artifact_id")
