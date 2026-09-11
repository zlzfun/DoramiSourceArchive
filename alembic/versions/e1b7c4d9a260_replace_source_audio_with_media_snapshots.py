"""Replace persisted source audio with immutable media snapshots.

Revision ID: e1b7c4d9a260
Revises: d6a3f9c2e714
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "e1b7c4d9a260"
down_revision: Union[str, Sequence[str], None] = "d6a3f9c2e714"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUDIO_TRIGGERS = (
    "podcast_audio_dependency_insert",
    "podcast_audio_dependency_update",
    "podcast_audio_processing_insert",
    "podcast_audio_processing_update",
    "podcast_audio_attempt_insert",
    "podcast_audio_attempt_update",
    "podcast_audio_binding_immutable",
    "podcast_script_audio_invalidate_update",
    "podcast_script_audio_invalidate_delete",
)

_PROCESSING_TRIGGERS = (
    "podcast_stage_attempt_identity_immutable",
    "podcast_cost_ledger_binding_insert",
    "podcast_cost_ledger_immutable_update",
    "podcast_cost_ledger_immutable_delete",
    "podcast_budget_reservation_transition",
    "podcast_budget_reservation_immutable_delete",
    "podcast_processing_input_immutable",
    "podcast_processing_command_immutable_update",
    "podcast_processing_command_immutable_delete",
)


def _drop_runtime_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        from storage.archive_sync_revision import (
            drop_archive_sync_revision_triggers,
        )

        drop_archive_sync_revision_triggers(bind)
        for name in (*_AUDIO_TRIGGERS, *_PROCESSING_TRIGGERS):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    elif bind.dialect.name == "postgresql":
        for name, table in (
            ("podcast_audio_dependency_insert", "podcast_artifacts"),
            ("podcast_audio_dependency_update", "podcast_artifacts"),
            ("podcast_audio_binding_immutable", "podcast_artifacts"),
            ("podcast_script_audio_invalidate_update", "podcast_text_publications"),
            ("podcast_script_audio_invalidate_delete", "podcast_text_publications"),
            ("podcast_stage_attempt_identity_immutable", "podcast_stage_attempts"),
            ("podcast_cost_ledger_binding_insert", "podcast_cost_ledger"),
            ("podcast_cost_ledger_immutable_update", "podcast_cost_ledger"),
            ("podcast_cost_ledger_immutable_delete", "podcast_cost_ledger"),
            ("podcast_budget_reservation_transition", "podcast_budget_reservations"),
            ("podcast_budget_reservation_immutable_delete", "podcast_budget_reservations"),
            ("podcast_processing_input_immutable", "podcast_processings"),
            ("podcast_processing_command_immutable_update", "podcast_processing_commands"),
            ("podcast_processing_command_immutable_delete", "podcast_processing_commands"),
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')
        bind.exec_driver_sql(
            "DROP FUNCTION IF EXISTS podcast_source_audio_binding_immutable_fn()"
        )


def _install_runtime_triggers(bind: sa.Connection) -> None:
    from models.db import (
        _podcast_audio_dependency_postgresql_sql,
        _podcast_audio_dependency_trigger_sql,
        _podcast_processing_audit_trigger_sql,
        _podcast_processing_command_audit_sql,
        _podcast_processing_command_postgresql_sql,
        _podcast_processing_postgresql_audit_sql,
    )

    kwargs = dict(
        include_execution_kind=True,
        include_poll_state=True,
        include_output_binding=True,
        include_provider_usage=True,
        include_output_authority=True,
        include_usage_settlement_mode=True,
    )
    if bind.dialect.name == "sqlite":
        statements = (
            *_podcast_processing_audit_trigger_sql(**kwargs),
            *_podcast_processing_command_audit_sql(),
            *_podcast_audio_dependency_trigger_sql(
                include_attempt_binding=True,
            ),
        )
    elif bind.dialect.name == "postgresql":
        statements = (
            *_podcast_processing_postgresql_audit_sql(**kwargs),
            *_podcast_processing_command_postgresql_sql(),
            *_podcast_audio_dependency_postgresql_sql(
                include_attempt_binding=True,
            ),
        )
    else:
        statements = ()
    for statement in statements:
        bind.exec_driver_sql(statement)
    if bind.dialect.name == "sqlite":
        from storage.archive_sync_revision import (
            install_archive_sync_revision_triggers,
        )

        install_archive_sync_revision_triggers(
            bind,
            include_podcast_integrity=True,
            include_podcast_audio=True,
        )


def _install_snapshot_immutability(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS podcast_source_media_snapshot_immutable
            BEFORE UPDATE ON podcast_source_media_snapshots
            BEGIN
              SELECT RAISE(ABORT, 'podcast source media snapshot is immutable');
            END
            """
        )
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION podcast_source_media_snapshot_immutable_fn()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'podcast source media snapshot is immutable';
            END; $$
            """
        )
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS podcast_source_media_snapshot_immutable "
            "ON podcast_source_media_snapshots"
        )
        bind.exec_driver_sql(
            "CREATE TRIGGER podcast_source_media_snapshot_immutable "
            "BEFORE UPDATE ON podcast_source_media_snapshots FOR EACH ROW "
            "EXECUTE FUNCTION podcast_source_media_snapshot_immutable_fn()"
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_artifacts, podcast_processings IN ACCESS EXCLUSIVE MODE"
        )

    def sha256_check(column: str) -> str:
        stripped = column
        for character in "0123456789abcdef":
            stripped = f"replace({stripped}, '{character}', '')"
        return (
            f"length({column}) = 64 AND {column} = lower({column}) "
            f"AND length({stripped}) = 0"
        )
    snapshot_table = "podcast_source_media_snapshots"
    snapshot_exists = snapshot_table in set(sa.inspect(bind).get_table_names())
    if snapshot_exists:
        # Versionless databases can be created from current SQLModel metadata
        # and then adopted from the Alembic baseline. Earlier revisions replay
        # their retired columns before this revision removes them again, while
        # this current table already exists. Accept only the complete shape.
        expected_columns = {
            "id",
            "episode_id",
            "locator_hash",
            "content_hash",
            "mime",
            "size_bytes",
            "duration_seconds",
            "created_at",
        }
        actual_columns = {
            column["name"]
            for column in sa.inspect(bind).get_columns(snapshot_table)
        }
        if actual_columns != expected_columns:
            raise RuntimeError(
                "partial Podcast source-media snapshot schema exists; restore "
                "a consistent backup before retrying migration"
            )
    else:
        op.create_table(
            snapshot_table,
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("locator_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("mime", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("duration_seconds", sa.Float(), nullable=False),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.CheckConstraint(
                sha256_check("locator_hash"),
                name="ck_podcast_source_media_snapshots_locator_hash",
            ),
            sa.CheckConstraint(
                sha256_check("content_hash"),
                name="ck_podcast_source_media_snapshots_content_hash",
            ),
            sa.CheckConstraint(
                "size_bytes > 0", name="ck_podcast_source_media_snapshots_size_bytes"
            ),
            sa.CheckConstraint(
                "duration_seconds > 0",
                name="ck_podcast_source_media_snapshots_duration_seconds",
            ),
            sa.ForeignKeyConstraint(["episode_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "episode_id",
                "locator_hash",
                "content_hash",
                name="uq_podcast_source_media_snapshot_identity",
            ),
        )
        op.create_index(
            "ix_podcast_source_media_snapshots_episode_id",
            snapshot_table,
            ["episode_id"],
        )
        op.create_index(
            "ix_podcast_source_media_snapshots_locator_hash",
            snapshot_table,
            ["locator_hash"],
        )
        op.create_index(
            "ix_podcast_source_media_snapshots_content_hash",
            snapshot_table,
            ["content_hash"],
        )
        op.create_index(
            "ix_podcast_source_media_snapshots_episode_created",
            snapshot_table,
            ["episode_id", "created_at"],
        )

    _drop_runtime_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    # The legacy check rejects the replacement kind, while the final check
    # rejects rows that have not been backfilled yet. Use a short-lived union
    # constraint so the data rewrite stays valid on both SQLite and PostgreSQL.
    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint("ck_podcast_processings_input_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_processings_input_kind",
            "input_artifact_kind IS NULL OR input_artifact_kind IN "
            "('source_audio','source_media_snapshot','publisher_transcript',"
            "'normalized_transcript','transcript_zh','digest_blog_zh',"
            "'narration_script_zh')",
        )

    # A legacy row lacking locator_hash cannot safely prove which current RSS
    # URL produced its bytes. It is deliberately not promoted; subsequent use
    # fails closed and requires a fresh validation.
    bind.execute(sa.text(
        "INSERT INTO podcast_source_media_snapshots "
        "(id, episode_id, locator_hash, content_hash, mime, size_bytes, "
        "duration_seconds, created_at) "
        "SELECT id, episode_id, source_locator_hash, content_hash, mime, "
        "size_bytes, duration_seconds, created_at FROM podcast_artifacts "
        "WHERE kind = 'source_audio' AND source_locator_hash IS NOT NULL "
        "AND duration_seconds IS NOT NULL AND duration_seconds > 0 "
        "AND size_bytes > 0"
    ))
    bind.execute(sa.text(
        "UPDATE podcast_processings SET input_artifact_kind = 'source_media_snapshot' "
        "WHERE input_artifact_kind = 'source_audio' AND EXISTS ("
        "SELECT 1 FROM podcast_source_media_snapshots snapshot "
        "WHERE snapshot.id = podcast_processings.input_artifact_id "
        "AND snapshot.episode_id = podcast_processings.episode_id "
        "AND snapshot.content_hash = podcast_processings.input_content_hash)"
    ))
    # Do not manufacture or leave a dangling snapshot binding from incomplete
    # legacy cache metadata. No provider call can be reconciled safely without
    # that identity, so the run is made an explicit terminal invalid input.
    invalid_process = (
        "SELECT processing.id FROM podcast_processings processing "
        "WHERE processing.input_artifact_kind = 'source_audio'"
    )
    bind.execute(sa.text(
        "UPDATE podcast_budget_reservations SET status = 'released', "
        "released_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE status = 'reserved' AND processing_id IN (" + invalid_process + ")"
    ))
    bind.execute(sa.text(
        "UPDATE podcast_stage_attempts SET submission_state = 'cancelled', "
        "request_unknown = false, retry_state = 'exhausted', "
        "error_code = 'source_media_snapshot_missing', "
        "error_message = 'source media requires fresh validation', "
        "completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE submission_state IN ('prepared','submitted','request_unknown','reconciling') "
        "AND processing_id IN (" + invalid_process + ")"
    ))
    bind.execute(sa.text(
        "UPDATE podcast_processings SET eligibility_status = 'invalid_input', "
        "eligibility_reasons_json = '[\"source media requires fresh validation\"]', "
        "processing_status = 'not_required', error_code = 'source_media_snapshot_missing', "
        "error_message = 'source media requires fresh validation', "
        "input_artifact_id = NULL, input_artifact_kind = NULL, "
        "input_content_hash = NULL, input_language = NULL, "
        "lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, "
        "next_retry_at = NULL, finished_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE input_artifact_kind = 'source_audio'"
    ))
    bind.execute(sa.text("DELETE FROM podcast_artifacts WHERE kind = 'source_audio'"))

    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint("ck_podcast_processings_input_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_processings_input_kind",
            "input_artifact_kind IS NULL OR input_artifact_kind IN "
            "('source_media_snapshot','publisher_transcript','normalized_transcript',"
            "'transcript_zh','digest_blog_zh','narration_script_zh')",
        )

    for name in (
        "ix_podcast_artifacts_kind_status_expires",
        "ix_podcast_artifacts_expired_at",
        "ix_podcast_artifacts_expires_at",
        "ix_podcast_artifacts_source_locator_hash",
    ):
        op.drop_index(name, table_name="podcast_artifacts")
    artifact_check_constraints = {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_check_constraints(
            "podcast_artifacts"
        )
    }
    with op.batch_alter_table("podcast_artifacts", recreate=recreate) as batch:
        for name in (
            "ck_podcast_artifacts_kind",
            "ck_podcast_artifacts_status",
            "ck_podcast_artifacts_source_has_no_narration",
            "ck_podcast_artifacts_source_locator_hash",
            "ck_podcast_artifacts_source_expires",
            "ck_podcast_artifacts_source_never_published",
            "ck_podcast_artifacts_digest_is_durable",
            "ck_podcast_artifacts_expired_status",
            "ck_podcast_artifacts_expired_at",
        ):
            if name in artifact_check_constraints:
                batch.drop_constraint(name, type_="check")
        batch.create_check_constraint("ck_podcast_artifacts_kind", "kind = 'digest_audio_zh'")
        batch.create_check_constraint(
            "ck_podcast_artifacts_status", "status IN ('ready','published','withdrawn')"
        )
        batch.drop_column("expired_at")
        batch.drop_column("expires_at")
        batch.drop_column("source_locator_hash")

    _install_runtime_triggers(bind)
    _install_snapshot_immutability(bind)


def downgrade() -> None:
    raise RuntimeError(
        "Podcast source audio blobs were intentionally retired; restore the "
        "pre-upgrade database and CAS backup to downgrade"
    )
