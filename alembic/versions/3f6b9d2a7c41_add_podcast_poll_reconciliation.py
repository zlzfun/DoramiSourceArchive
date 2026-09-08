"""add durable Podcast provider polling and reconciliation state

Revision ID: 3f6b9d2a7c41
Revises: 2e5c8a1d7b40
Create Date: 2026-09-06
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "3f6b9d2a7c41"
down_revision: Union[str, Sequence[str], None] = "2e5c8a1d7b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_POLL_COLUMNS = ("poll_count", "last_polled_at", "provider_deadline_at")


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast polling migration requires an online database connection "
            "for writer fencing and trigger restoration"
        )


def _lock_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_processings, podcast_stage_attempts, "
            "podcast_budget_reservations IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "UPDATE podcast_processings SET updated_at = updated_at WHERE 0"
        )


def _assert_parent_downgrade_safe(bind: sa.Connection) -> None:
    """Run the full parent safety chain before this head changes schema."""

    parent_path = Path(__file__).with_name(
        "2e5c8a1d7b40_add_podcast_attempt_execution_kind.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_dorami_podcast_polling_parent_revision", parent_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Podcast polling parent revision")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._assert_parent_downgrade_safe(bind)


def _drop_sqlite_rebuild_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name != "sqlite":
        return
    for name in (
        "podcast_stage_attempt_identity_immutable",
        "podcast_cost_ledger_immutable_update",
        "podcast_cost_ledger_immutable_delete",
        "podcast_budget_reservation_transition",
        "podcast_budget_reservation_immutable_delete",
        "podcast_processing_input_immutable",
        "podcast_processing_command_immutable_update",
        "podcast_processing_command_immutable_delete",
        "podcast_audio_dependency_insert",
        "podcast_audio_dependency_update",
        "podcast_audio_processing_insert",
        "podcast_audio_processing_update",
        "podcast_audio_binding_immutable",
        "podcast_script_audio_invalidate_update",
        "podcast_script_audio_invalidate_delete",
    ):
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')


def _install_current_triggers(
    bind: sa.Connection, *, include_poll_state: bool
) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import (
            _podcast_audio_dependency_trigger_sql,
            _podcast_processing_audit_trigger_sql,
            _podcast_processing_command_audit_sql,
        )

        groups = (
            _podcast_processing_audit_trigger_sql(
                include_execution_kind=True,
                include_poll_state=include_poll_state,
            ),
            _podcast_processing_command_audit_sql(),
            _podcast_audio_dependency_trigger_sql(),
        )
    elif bind.dialect.name == "postgresql":
        from models.db import (
            _podcast_audio_dependency_postgresql_sql,
            _podcast_processing_command_postgresql_sql,
            _podcast_processing_postgresql_audit_sql,
        )

        groups = (
            _podcast_processing_postgresql_audit_sql(
                include_execution_kind=True,
                include_poll_state=include_poll_state,
            ),
            _podcast_processing_command_postgresql_sql(),
            _podcast_audio_dependency_postgresql_sql(),
        )
    else:
        return
    for statements in groups:
        for statement in statements:
            bind.exec_driver_sql(statement)


def _replace_processing_status_constraint(*, include_reconciliation: bool) -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    statuses = (
        "'not_required','queued','running','retry_wait',"
        + ("'reconciliation_required'," if include_reconciliation else "")
        + "'awaiting_review','ready','failed','cancelled','superseded'"
    )
    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint("ck_podcast_processings_status", type_="check")
        batch.create_check_constraint(
            "ck_podcast_processings_status",
            f"processing_status IN ({statuses})",
        )


def _replace_processing_command_constraint(*, include_reconciliation: bool) -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    allowed = (
        "command_type IN ('manual_retry','provider_reconcile')"
        if include_reconciliation
        else "command_type = 'manual_retry'"
    )
    with op.batch_alter_table(
        "podcast_processing_commands", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "ck_podcast_processing_commands_type", type_="check"
        )
        batch.create_check_constraint(
            "ck_podcast_processing_commands_type", allowed
        )


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("podcast_stage_attempts")
    }
    present = set(_POLL_COLUMNS) & columns
    if present == set(_POLL_COLUMNS):
        # An adopted create_all database already has the current schema.
        _install_current_triggers(bind, include_poll_state=True)
        return
    if present:
        raise RuntimeError(
            "partial Podcast polling schema exists; restore a consistent "
            "backup before retrying migration"
        )

    _drop_sqlite_rebuild_triggers(bind)
    _replace_processing_status_constraint(include_reconciliation=True)
    _replace_processing_command_constraint(include_reconciliation=True)
    op.add_column(
        "podcast_stage_attempts",
        sa.Column("poll_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "podcast_stage_attempts",
        sa.Column(
            "last_polled_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
    )
    op.add_column(
        "podcast_stage_attempts",
        sa.Column(
            "provider_deadline_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
    )
    bind.execute(sa.text("UPDATE podcast_stage_attempts SET poll_count = 0"))
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_stage_attempts", recreate=recreate) as batch:
        batch.alter_column(
            "poll_count", existing_type=sa.Integer(), nullable=False
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_nonnegative", type_="check"
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_nonnegative",
            "attempt_no >= 1 AND fencing_token >= 1 AND estimated_cost_minor >= 0 "
            "AND actual_cost_minor >= 0 AND poll_count >= 0",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_poll_state",
            "(poll_count = 0 AND last_polled_at IS NULL) OR "
            "(poll_count > 0 AND last_polled_at IS NOT NULL AND "
            "provider_deadline_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_local_poll_state",
            "execution_kind <> 'local' OR (poll_count = 0 AND "
            "last_polled_at IS NULL AND provider_deadline_at IS NULL)",
        )
    _install_current_triggers(bind, include_poll_state=True)


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    _assert_parent_downgrade_safe(bind)
    unsafe = bind.execute(sa.text(
        "SELECT 1 FROM podcast_processings "
        "WHERE processing_status = 'reconciliation_required' "
        "UNION ALL SELECT 1 FROM podcast_stage_attempts "
        "WHERE poll_count <> 0 OR last_polled_at IS NOT NULL "
        "OR provider_deadline_at IS NOT NULL LIMIT 1"
    )).first()
    if unsafe is not None:
        raise RuntimeError(
            "refusing to discard durable Podcast polling or reconciliation state; "
            "stop workers and restore the pre-upgrade backup"
        )

    _drop_sqlite_rebuild_triggers(bind)
    _replace_processing_status_constraint(include_reconciliation=False)
    _replace_processing_command_constraint(include_reconciliation=False)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_stage_attempts", recreate=recreate) as batch:
        batch.drop_constraint(
            "ck_podcast_stage_attempts_local_poll_state", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_poll_state", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_nonnegative", type_="check"
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_nonnegative",
            "attempt_no >= 1 AND fencing_token >= 1 AND estimated_cost_minor >= 0 "
            "AND actual_cost_minor >= 0",
        )
        batch.drop_column("provider_deadline_at")
        batch.drop_column("last_polled_at")
        batch.drop_column("poll_count")
    _install_current_triggers(bind, include_poll_state=False)
