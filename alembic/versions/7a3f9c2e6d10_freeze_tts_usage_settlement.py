"""freeze the usage settlement mode on every provider TTS attempt

Revision ID: 7a3f9c2e6d10
Revises: 5e9a1c7d3b42
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "7a3f9c2e6d10"
down_revision: Union[str, Sequence[str], None] = "5e9a1c7d3b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMN = "usage_settlement_mode"
_CONSTRAINT = "ck_podcast_stage_attempts_usage_settlement_mode"
_TRIGGER = "podcast_stage_attempt_identity_immutable"
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


def _require_online() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "TTS settlement-mode migration requires an online database connection"
        )


def _drop_audit(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{_TRIGGER}"')
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            f'DROP TRIGGER IF EXISTS "{_TRIGGER}" ON podcast_stage_attempts'
        )


def _drop_audio_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        for name in _AUDIO_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    elif bind.dialect.name == "postgresql":
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


def _install_audio_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_audio_dependency_trigger_sql

        statements = _podcast_audio_dependency_trigger_sql(
            include_attempt_binding=True
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        statements = _podcast_audio_dependency_postgresql_sql(
            include_attempt_binding=True
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _install_audit(bind: sa.Connection, *, current: bool) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_audit_trigger_sql

        statements = _podcast_processing_audit_trigger_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=True,
            include_usage_settlement_mode=current,
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=True,
            include_usage_settlement_mode=current,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _lock_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_stage_attempts IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "UPDATE podcast_stage_attempts SET updated_at=updated_at WHERE 0"
        )


def upgrade() -> None:
    _require_online()
    bind = op.get_bind()
    _lock_writers(bind)
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("podcast_stage_attempts")
    }
    if _COLUMN in columns:
        _drop_audit(bind)
        _install_audit(bind, current=True)
        return
    _drop_audit(bind)
    _drop_audio_triggers(bind)
    op.add_column(
        "podcast_stage_attempts",
        sa.Column(
            _COLUMN,
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
            server_default=sa.text("''"),
        ),
    )
    bind.execute(sa.text(
        "UPDATE podcast_stage_attempts SET usage_settlement_mode = "
        "CASE WHEN stage = 'tts' AND execution_kind = 'provider' "
        "THEN 'manual' ELSE '' END"
    ))
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.alter_column(
            _COLUMN,
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        )
        batch.create_check_constraint(
            _CONSTRAINT,
            "(stage = 'tts' AND execution_kind = 'provider' AND "
            "usage_settlement_mode IN ('manual','submitted_characters')) OR "
            "((stage <> 'tts' OR execution_kind <> 'provider') AND "
            "length(usage_settlement_mode) = 0)",
        )
    _install_audit(bind, current=True)
    _install_audio_triggers(bind)


def downgrade() -> None:
    _require_online()
    bind = op.get_bind()
    _lock_writers(bind)
    active = bind.execute(sa.text(
        "SELECT 1 FROM podcast_stage_attempts WHERE stage = 'tts' AND "
        "submission_state IN ('prepared','submitted','request_unknown','reconciling') "
        "LIMIT 1"
    )).first()
    if active is not None:
        raise RuntimeError(
            "refusing to discard settlement policy for an active TTS attempt"
        )
    _drop_audit(bind)
    _drop_audio_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.drop_column(_COLUMN)
    _install_audit(bind, current=False)
    _install_audio_triggers(bind)
