"""bind automatic digest audio artifacts to their producing TTS attempts

Revision ID: 5e9a1c7d3b42
Revises: 8c4e1a7b9d20
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "5e9a1c7d3b42"
down_revision: Union[str, Sequence[str], None] = "8c4e1a7b9d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ARTIFACT_COLUMN = "producing_attempt_id"
_ATTEMPT_COLUMN = "output_authority_id"
_TRIGGERS = (
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


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast TTS artifact binding migration requires an online database "
            "connection for writer fencing and trigger restoration"
        )


def _lock_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_processings, podcast_stage_attempts, "
            "podcast_budget_reservations, podcast_artifacts IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql("UPDATE podcast_processings SET updated_at=updated_at WHERE 0")


def _drop_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        for name in _TRIGGERS:
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


def _drop_attempt_audit(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            'DROP TRIGGER IF EXISTS "podcast_stage_attempt_identity_immutable"'
        )
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS podcast_stage_attempt_identity_immutable "
            "ON podcast_stage_attempts"
        )


def _install_attempt_audit(bind: sa.Connection, *, current: bool) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_audit_trigger_sql

        statements = _podcast_processing_audit_trigger_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=current,
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=current,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _install_triggers(bind: sa.Connection, *, current: bool) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_audio_dependency_trigger_sql

        statements = _podcast_audio_dependency_trigger_sql(
            include_attempt_binding=current
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        statements = _podcast_audio_dependency_postgresql_sql(
            include_attempt_binding=current
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _index_names(bind: sa.Connection) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(bind).get_indexes("podcast_artifacts")
        if item.get("name")
    }


def _assert_downgrade_safe(bind: sa.Connection) -> None:
    used = bind.execute(
        sa.text(
            "SELECT 1 FROM podcast_artifacts WHERE producing_attempt_id IS NOT NULL "
            "UNION ALL SELECT 1 FROM podcast_stage_attempts "
            "WHERE output_artifact_kind = 'digest_audio_zh' "
            "OR output_authority_id IS NOT NULL LIMIT 1"
        )
    ).first()
    if used is not None:
        raise RuntimeError(
            "refusing to discard digest audio attempt bindings; stop TTS workers "
            "and restore the pre-upgrade backup"
        )


def _assert_upgrade_safe(bind: sa.Connection) -> None:
    active = bind.execute(
        sa.text(
            "SELECT 1 FROM podcast_stage_attempts WHERE stage = 'tts' "
            "AND submission_state IN "
            "('prepared','submitted','request_unknown','reconciling') LIMIT 1"
        )
    ).first()
    if active is not None:
        raise RuntimeError(
            "refusing to upgrade active legacy TTS attempts without an immutable "
            "output authority; reconcile or finish them first"
        )
def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    artifact_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("podcast_artifacts")
    }
    attempt_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("podcast_stage_attempts")
    }
    artifact_present = _ARTIFACT_COLUMN in artifact_columns
    attempt_present = _ATTEMPT_COLUMN in attempt_columns
    if artifact_present and attempt_present:
        _drop_triggers(bind)
        _drop_attempt_audit(bind)
        _install_attempt_audit(bind, current=True)
        _install_triggers(bind, current=True)
        return
    if artifact_present or attempt_present:
        raise RuntimeError(
            "partial Podcast TTS artifact binding schema exists; restore a "
            "consistent backup before retrying migration"
        )

    _assert_upgrade_safe(bind)
    _drop_triggers(bind)
    _drop_attempt_audit(bind)
    op.add_column(
        "podcast_artifacts",
        sa.Column(_ARTIFACT_COLUMN, sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "podcast_stage_attempts",
        sa.Column(_ATTEMPT_COLUMN, sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    # The pre-binding schema could label static/manual history as ``tts`` but
    # had no attempt identity from which a truthful binding could be rebuilt.
    # Preserve the artifact while making that missing evidence explicit.  New
    # writes reserve the exact ``tts`` label for attempt-bound output.
    bind.execute(
        sa.text(
            "UPDATE podcast_artifacts SET provenance = 'legacy_tts_unbound' "
            "WHERE lower(trim(provenance)) = 'tts'"
        )
    )
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_stage_attempts", recreate=recreate) as batch:
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_output_authority",
            "output_authority_id IS NULL OR length(trim(output_authority_id)) > 0",
        )
    with op.batch_alter_table("podcast_artifacts", recreate=recreate) as batch:
        batch.create_check_constraint(
            "ck_podcast_artifacts_attempt_bound_digest",
            "producing_attempt_id IS NULL OR "
            "(kind = 'digest_audio_zh' AND processing_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_tts_provenance_binding",
            "(lower(trim(provenance)) = 'tts' AND producing_attempt_id IS NOT NULL) OR "
            "(lower(trim(provenance)) <> 'tts' AND producing_attempt_id IS NULL)",
        )
        batch.create_unique_constraint(
            "uq_podcast_artifacts_producing_attempt", ["producing_attempt_id"]
        )
        batch.create_foreign_key(
            "fk_podcast_artifacts_attempt_owner",
            "podcast_stage_attempts",
            ["producing_attempt_id", "processing_id"],
            ["id", "processing_id"],
            ondelete="RESTRICT",
        )
    if "ix_podcast_artifacts_producing_attempt_id" not in _index_names(bind):
        op.create_index(
            "ix_podcast_artifacts_producing_attempt_id",
            "podcast_artifacts",
            ["producing_attempt_id"],
            unique=False,
        )
    _install_attempt_audit(bind, current=True)
    _install_triggers(bind, current=True)


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    _assert_downgrade_safe(bind)
    _drop_triggers(bind)
    _drop_attempt_audit(bind)
    if "ix_podcast_artifacts_producing_attempt_id" in _index_names(bind):
        op.drop_index(
            "ix_podcast_artifacts_producing_attempt_id",
            table_name="podcast_artifacts",
        )
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_artifacts", recreate=recreate) as batch:
        batch.drop_constraint(
            "fk_podcast_artifacts_attempt_owner", type_="foreignkey"
        )
        batch.drop_constraint(
            "uq_podcast_artifacts_producing_attempt", type_="unique"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_attempt_bound_digest", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_tts_provenance_binding", type_="check"
        )
        batch.drop_column(_ARTIFACT_COLUMN)
    with op.batch_alter_table("podcast_stage_attempts", recreate=recreate) as batch:
        batch.drop_constraint(
            "ck_podcast_stage_attempts_output_authority", type_="check"
        )
        batch.drop_column(_ATTEMPT_COLUMN)
    _install_attempt_audit(bind, current=False)
    _install_triggers(bind, current=False)
