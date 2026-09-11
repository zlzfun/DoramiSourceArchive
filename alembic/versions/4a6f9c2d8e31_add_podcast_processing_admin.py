"""add provider-neutral Podcast processing administration

Revision ID: 4a6f9c2d8e31
Revises: 8f3b2d1c7a90
Create Date: 2026-09-06
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "4a6f9c2d8e31"
down_revision: Union[str, Sequence[str], None] = "8f3b2d1c7a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INPUT_COLUMNS = (
    "input_artifact_id",
    "input_artifact_kind",
    "input_content_hash",
    "input_language",
    "budget_scope",
    "budget_period",
    "budget_limit_minor",
    "per_run_budget_minor",
    "narration_artifact_id",
    "narration_content_hash",
    "voice_profile_id",
)

_COMMAND_COLUMNS = {
    "id",
    "processing_id",
    "command_type",
    "idempotency_key",
    "expected_attempt_count",
    "requested_by",
    "reason",
    "outcome",
    "error_code",
    "error_message",
    "created_at",
}


def _install_audit() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_command_audit_sql

        statements = _podcast_processing_command_audit_sql()
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_command_postgresql_sql

        statements = _podcast_processing_command_postgresql_sql()
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _drop_sqlite_rebuild_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for name in (
        "podcast_stage_attempt_identity_immutable",
        "podcast_cost_ledger_immutable_update",
        "podcast_cost_ledger_immutable_delete",
        "podcast_budget_reservation_transition",
        "podcast_budget_reservation_immutable_delete",
        "podcast_audio_dependency_insert",
        "podcast_audio_dependency_update",
        "podcast_audio_processing_insert",
        "podcast_audio_processing_update",
        "podcast_audio_binding_immutable",
        "podcast_script_audio_invalidate_update",
        "podcast_script_audio_invalidate_delete",
    ):
        op.get_bind().exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')


def _install_all_audit() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        from models.db import (
            _podcast_audio_dependency_trigger_sql,
            _podcast_processing_audit_trigger_sql,
        )

        for statement in _podcast_processing_audit_trigger_sql():
            bind.exec_driver_sql(statement)
        for statement in _podcast_audio_dependency_trigger_sql():
            bind.exec_driver_sql(statement)
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        for statement in _podcast_audio_dependency_postgresql_sql():
            bind.exec_driver_sql(statement)
    _install_audit()


def _drop_audit() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for name in (
            "podcast_processing_input_immutable",
            "podcast_processing_command_immutable_update",
            "podcast_processing_command_immutable_delete",
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
        return
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS podcast_processing_input_immutable "
            "ON podcast_processings"
        )
        for suffix in ("update", "delete"):
            bind.exec_driver_sql(
                "DROP TRIGGER IF EXISTS podcast_processing_command_immutable_"
                f"{suffix} ON podcast_processing_commands"
            )
        bind.exec_driver_sql(
            "DROP FUNCTION IF EXISTS podcast_processing_input_immutable_fn()"
        )
        bind.exec_driver_sql(
            "DROP FUNCTION IF EXISTS podcast_processing_command_immutable_fn()"
        )


def _assert_parent_downgrade_safe() -> None:
    """Run the parent revision's live-writer fence before leaving this head."""

    parent_path = Path(__file__).with_name(
        "8f3b2d1c7a90_bind_podcast_audio_to_narration.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_dorami_podcast_audio_parent_revision", parent_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Podcast parent downgrade fence")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._assert_archive_sync_downgrade_safe()
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


def _assert_downgrade_safe() -> None:
    """Reject every lossy 4a downgrade before a newer head is removed."""

    _assert_parent_downgrade_safe()
    bind = op.get_bind()
    used = bind.execute(sa.text(
        "SELECT 1 FROM podcast_processings WHERE input_artifact_id IS NOT NULL "
        "OR narration_artifact_id IS NOT NULL LIMIT 1"
    )).first()
    commands = bind.execute(sa.text(
        "SELECT 1 FROM podcast_processing_commands LIMIT 1"
    )).first()
    if used is not None or commands is not None:
        raise RuntimeError(
            "拒绝降级 Podcast processing 管理 schema：现有任务或命令含新版输入绑定。"
            "请恢复升级前备份。"
        )


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast processing admin migration requires an online database "
            "connection for data-loss and active-provider safety checks"
        )


def _lock_processing_writers(
    bind: sa.Connection, *, include_commands: bool = False
) -> None:
    """Fence workers before checking provider-facing migration safety."""

    if bind.dialect.name == "postgresql":
        tables = (
            "podcast_artifacts, podcast_processings, podcast_stage_attempts, "
            "podcast_budget_reservations"
        )
        if include_commands:
            tables += ", podcast_processing_commands"
        bind.exec_driver_sql(
            f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        # Alembic has already opened a transaction by this point. A no-op write
        # obtains SQLite's database-wide writer reservation; a worker that won
        # the race commits first and is then visible to the safety query, while
        # later workers remain blocked until this migration commits.
        bind.exec_driver_sql(
            "UPDATE podcast_processings SET updated_at = updated_at WHERE 0"
        )


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("podcast_processings")
    }
    tables = set(inspector.get_table_names())
    has_columns = set(_INPUT_COLUMNS).issubset(columns)
    has_commands = "podcast_processing_commands" in tables
    if has_columns and has_commands:
        # Legacy create_all databases already contain the current model.
        _install_all_audit()
        return
    if (
        bind.dialect.name == "sqlite"
        and has_commands
        and not (set(_INPUT_COLUMNS) & columns)
    ):
        # During the feature's development window, create_all could create the
        # command table next to an older processing table without adding the
        # latter's new columns or advancing Alembic. An empty, exact-shape table
        # contains no audit state to preserve, so remove and recreate it below
        # through the canonical migration. Anything nonempty or structurally
        # different remains fail-closed as a genuinely ambiguous partial schema.
        command_columns = {
            column["name"]
            for column in inspector.get_columns("podcast_processing_commands")
        }
        command_count = bind.execute(sa.text(
            "SELECT count(*) FROM podcast_processing_commands"
        )).scalar_one()
        if command_columns == _COMMAND_COLUMNS and command_count == 0:
            op.drop_table("podcast_processing_commands")
            has_commands = False
    if set(_INPUT_COLUMNS) & columns or has_commands:
        raise RuntimeError(
            "partial Podcast processing admin schema exists; restore a "
            "consistent backup before retrying migration"
        )

    # A provider-facing attempt may still incur charges after this migration.
    # Refuse instead of releasing its budget hold or losing reconciliation
    # truth. The write fence closes the check-to-DDL race with online workers;
    # operators must still stop workers and reconcile/settle existing work.
    _lock_processing_writers(bind)
    unsafe_attempt = bind.execute(sa.text(
        "SELECT 1 FROM podcast_stage_attempts a "
        "LEFT JOIN podcast_budget_reservations r ON r.attempt_id = a.id "
        "WHERE a.submission_state IN "
        "('prepared','submitted','request_unknown','reconciling') "
        "OR r.status = 'reserved' LIMIT 1"
    )).first()
    if unsafe_attempt is not None:
        raise RuntimeError(
            "Podcast processing migration requires all provider attempts to be "
            "reconciled and budget reservations settled or released"
        )

    for column_name in _INPUT_COLUMNS:
        op.add_column(
            "podcast_processings",
            sa.Column(
                column_name,
                (
                    sa.Integer()
                    if column_name in {"budget_limit_minor", "per_run_budget_minor"}
                    else sqlmodel.sql.sqltypes.AutoString()
                ),
                nullable=True,
            ),
        )

    # Historical runs did not persist a replayable input identity. Keep their
    # audit/billing rows and make the non-replayable run explicitly inert.
    stamp = "CAST(CURRENT_TIMESTAMP AS TEXT)"
    op.execute(sa.text(
        "UPDATE podcast_processings SET processing_status = 'superseded', "
        "eligibility_status = 'invalid_input', "
        "eligibility_reasons_json = "
        "'[\"historical run has no immutable input binding\"]', "
        "lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, "
        "next_retry_at = NULL, error_code = 'input_binding_migration', "
        "error_message = 'superseded during immutable input binding migration', "
        f"finished_at = COALESCE(finished_at, {stamp}), updated_at = {stamp}"
    ))
    # 8f allowed an audio row to point at a digest processing run before that
    # run persisted its exact narration input. The run is now superseded and
    # cannot satisfy the stronger trigger installed below, so fail closed by
    # withdrawing any still-reader-visible audio while preserving its immutable
    # processing/narration audit binding.
    op.execute(sa.text(
        "UPDATE podcast_artifacts SET status = 'withdrawn', "
        f"withdrawn_at = COALESCE(withdrawn_at, {stamp}), updated_at = {stamp} "
        "WHERE processing_id IS NOT NULL AND kind = 'digest_audio_zh' "
        "AND status IN ('ready','published')"
    ))

    _drop_sqlite_rebuild_triggers()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint(
            "uq_podcast_processings_effective_run", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_podcast_processings_effective_run",
            [
                "episode_id",
                "input_fingerprint",
                "pipeline_version",
                "policy_version",
                "requested_target",
                "budget_scope",
                "budget_period",
                "budget_limit_minor",
                "per_run_budget_minor",
            ],
        )
        batch.create_check_constraint(
            "ck_podcast_processings_input_binding",
            "((input_artifact_id IS NOT NULL AND length(trim(input_artifact_id)) > 0 "
            "AND input_artifact_kind IS NOT NULL AND input_content_hash IS NOT NULL "
            "AND input_language IS NOT NULL AND length(trim(input_language)) > 0) OR "
            "(processing_status IN ('not_required','superseded') AND "
            "eligibility_status IN ('blocked_source','blocked_rights','invalid_input') "
            "AND input_artifact_id IS NULL AND "
            "input_artifact_kind IS NULL AND input_content_hash IS NULL AND "
            "input_language IS NULL))",
        )
        batch.create_check_constraint(
            "ck_podcast_processings_budget_binding",
            "((budget_scope IS NOT NULL AND length(trim(budget_scope)) > 0 "
            "AND budget_period IS NOT NULL AND length(trim(budget_period)) > 0 "
            "AND budget_limit_minor IS NOT NULL AND per_run_budget_minor IS NOT NULL "
            "AND budget_limit_minor > 0 AND per_run_budget_minor > 0 "
            "AND per_run_budget_minor <= budget_limit_minor) OR "
            "(processing_status IN ('not_required','superseded') AND "
            "eligibility_status IN ('blocked_source','blocked_rights','invalid_input') "
            "AND budget_scope IS NULL AND budget_period IS NULL "
            "AND budget_limit_minor IS NULL AND per_run_budget_minor IS NULL))",
        )
        batch.create_check_constraint(
            "ck_podcast_processings_input_kind",
            "input_artifact_kind IS NULL OR input_artifact_kind IN "
            "('source_audio','publisher_transcript','normalized_transcript',"
            "'transcript_zh','digest_blog_zh','narration_script_zh')",
        )
        batch.create_check_constraint(
            "ck_podcast_processings_input_hash",
            "input_content_hash IS NULL OR (length(input_content_hash) = 64 AND "
            "input_content_hash = lower(input_content_hash))",
        )
        batch.create_check_constraint(
            "ck_podcast_processings_audio_binding",
            "(requested_target = 'digest_audio' AND narration_artifact_id IS NOT NULL "
            "AND narration_content_hash IS NOT NULL AND voice_profile_id IS NOT NULL "
            "AND input_artifact_id = narration_artifact_id "
            "AND input_content_hash = narration_content_hash "
            "AND input_artifact_kind = 'narration_script_zh') OR "
            "(requested_target = 'digest_audio' AND "
            "processing_status IN ('not_required','superseded') AND "
            "eligibility_status IN ('blocked_source','blocked_rights','invalid_input') "
            "AND narration_artifact_id IS NULL AND narration_content_hash IS NULL "
            "AND voice_profile_id IS NULL) OR "
            "(requested_target <> 'digest_audio' AND narration_artifact_id IS NULL "
            "AND narration_content_hash IS NULL AND voice_profile_id IS NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_processings_narration_hash",
            "narration_content_hash IS NULL OR "
            "(length(narration_content_hash) = 64 AND "
            "narration_content_hash = lower(narration_content_hash))",
        )
        batch.create_foreign_key(
            "fk_podcast_processings_narration_artifact",
            "podcast_text_artifacts",
            ["narration_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    for column_name in (
        "input_artifact_id",
        "input_artifact_kind",
        "input_content_hash",
        "budget_scope",
        "budget_period",
        "narration_artifact_id",
        "narration_content_hash",
        "voice_profile_id",
    ):
        op.create_index(
            f"ix_podcast_processings_{column_name}",
            "podcast_processings",
            [column_name],
            unique=False,
        )

    op.create_table(
        "podcast_processing_commands",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("processing_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("command_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expected_attempt_count", sa.Integer(), nullable=False),
        sa.Column("requested_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "command_type = 'manual_retry'",
            name="ck_podcast_processing_commands_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted','rejected')",
            name="ck_podcast_processing_commands_outcome",
        ),
        sa.CheckConstraint(
            "expected_attempt_count >= 0 AND length(trim(idempotency_key)) > 0 "
            "AND length(trim(requested_by)) > 0 AND length(trim(reason)) > 0",
            name="ck_podcast_processing_commands_required",
        ),
        sa.ForeignKeyConstraint(
            ["processing_id"], ["podcast_processings.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "processing_id",
            "command_type",
            "idempotency_key",
            name="uq_podcast_processing_commands_idempotency",
        ),
    )
    op.create_index(
        "ix_podcast_processing_commands_processing_id",
        "podcast_processing_commands",
        ["processing_id"],
        unique=False,
    )
    op.create_index(
        "ix_podcast_processing_commands_processing_created",
        "podcast_processing_commands",
        ["processing_id", "created_at"],
        unique=False,
    )
    _install_all_audit()


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_processing_writers(bind, include_commands=True)
    _assert_downgrade_safe()

    _drop_audit()
    op.drop_index(
        "ix_podcast_processing_commands_processing_created",
        table_name="podcast_processing_commands",
    )
    op.drop_index(
        "ix_podcast_processing_commands_processing_id",
        table_name="podcast_processing_commands",
    )
    op.drop_table("podcast_processing_commands")
    for column_name in reversed((
        "input_artifact_id",
        "input_artifact_kind",
        "input_content_hash",
        "budget_scope",
        "budget_period",
        "narration_artifact_id",
        "narration_content_hash",
        "voice_profile_id",
    )):
        op.drop_index(
            f"ix_podcast_processings_{column_name}",
            table_name="podcast_processings",
        )
    _drop_sqlite_rebuild_triggers()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint(
            "uq_podcast_processings_effective_run", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_podcast_processings_effective_run",
            [
                "episode_id",
                "input_fingerprint",
                "pipeline_version",
                "requested_target",
            ],
        )
        batch.drop_constraint(
            "fk_podcast_processings_narration_artifact", type_="foreignkey"
        )
        for name in (
            "ck_podcast_processings_narration_hash",
            "ck_podcast_processings_audio_binding",
            "ck_podcast_processings_input_hash",
            "ck_podcast_processings_input_kind",
            "ck_podcast_processings_budget_binding",
            "ck_podcast_processings_input_binding",
        ):
            batch.drop_constraint(name, type_="check")
        for column_name in reversed(_INPUT_COLUMNS):
            batch.drop_column(column_name)
    if bind.dialect.name == "sqlite":
        from models.db import (
            _podcast_audio_dependency_trigger_sql,
            _podcast_processing_audit_trigger_sql,
        )

        for statement in _podcast_processing_audit_trigger_sql():
            bind.exec_driver_sql(statement)
        for statement in _podcast_audio_dependency_trigger_sql(
            require_processing_narration=False,
        ):
            bind.exec_driver_sql(statement)
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        for statement in _podcast_audio_dependency_postgresql_sql(
            require_processing_narration=False,
        ):
            bind.exec_driver_sql(statement)
