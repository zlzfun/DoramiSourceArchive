"""bind normalized transcript artifacts to their producing attempts

Revision ID: 6b8d2f4a9c70
Revises: 3f6b9d2a7c41
Create Date: 2026-09-06
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "6b8d2f4a9c70"
down_revision: Union[str, Sequence[str], None] = "3f6b9d2a7c41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ATTEMPT_COLUMNS = (
    "settings_fingerprint",
    "output_artifact_id",
    "output_artifact_kind",
)
_TEXT_COLUMNS = ("processing_id", "producing_attempt_id")


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast normalized-transcript migration requires an online "
            "database connection for writer fencing and trigger restoration"
        )


def _lock_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_processings, podcast_stage_attempts, "
            "podcast_text_artifacts, podcast_text_publications "
            "IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "UPDATE podcast_processings SET updated_at = updated_at WHERE 0"
        )


def _drop_attempt_identity_trigger(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            'DROP TRIGGER IF EXISTS "podcast_stage_attempt_identity_immutable"'
        )
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "DROP TRIGGER IF EXISTS podcast_stage_attempt_identity_immutable "
            "ON podcast_stage_attempts"
        )


def _drop_sqlite_audio_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name != "sqlite":
        return
    for name in (
        "podcast_audio_dependency_insert",
        "podcast_audio_dependency_update",
        "podcast_audio_processing_insert",
        "podcast_audio_processing_update",
        "podcast_audio_binding_immutable",
        "podcast_script_audio_invalidate_update",
        "podcast_script_audio_invalidate_delete",
    ):
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')


def _install_audio_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_audio_dependency_trigger_sql

        statements = _podcast_audio_dependency_trigger_sql()
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        statements = _podcast_audio_dependency_postgresql_sql()
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _install_attempt_audit(bind: sa.Connection, *, current: bool) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_audit_trigger_sql

        statements = _podcast_processing_audit_trigger_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=current,
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=current,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _index_names(table: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes(table)
        if item.get("name")
    }


def _create_index(name: str, table: str, columns: list[str]) -> None:
    if name not in _index_names(table):
        op.create_index(name, table, columns, unique=False)


def _assert_downgrade_safe(bind: sa.Connection) -> None:
    parent_path = Path(__file__).with_name(
        "3f6b9d2a7c41_add_podcast_poll_reconciliation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_dorami_normalized_transcript_parent_revision", parent_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load normalized-transcript parent revision")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._assert_parent_downgrade_safe(bind)
    used = bind.execute(sa.text(
        "SELECT 1 FROM podcast_stage_attempts "
        "WHERE settings_fingerprint IS NOT NULL "
        "OR output_artifact_id IS NOT NULL OR output_artifact_kind IS NOT NULL "
        "UNION ALL SELECT 1 FROM podcast_text_artifacts "
        "WHERE kind = 'normalized_transcript' OR processing_id IS NOT NULL "
        "OR producing_attempt_id IS NOT NULL LIMIT 1"
    )).first()
    if used is not None:
        raise RuntimeError(
            "refusing to discard normalized transcript attempt bindings; "
            "stop workers and restore the pre-upgrade backup"
        )


def _assert_upgrade_safe(bind: sa.Connection) -> None:
    active = bind.execute(sa.text(
        "SELECT 1 FROM podcast_stage_attempts WHERE submission_state IN "
        "('prepared','submitted','request_unknown','reconciling') LIMIT 1"
    )).first()
    if active is not None:
        raise RuntimeError(
            "refusing to upgrade active legacy Podcast attempts without a "
            "recorded settings fingerprint; reconcile or finish them first"
        )


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    attempt_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("podcast_stage_attempts")
    }
    text_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("podcast_text_artifacts")
    }
    present_attempt = set(_ATTEMPT_COLUMNS) & attempt_columns
    present_text = set(_TEXT_COLUMNS) & text_columns
    if present_attempt == set(_ATTEMPT_COLUMNS) and present_text == set(_TEXT_COLUMNS):
        _drop_attempt_identity_trigger(bind)
        _install_attempt_audit(bind, current=True)
        install_archive_sync_revision_triggers(bind)
        _install_audio_triggers(bind)
        return
    if present_attempt or present_text:
        raise RuntimeError(
            "partial Podcast normalized-transcript schema exists; restore a "
            "consistent backup before retrying migration"
        )
    _assert_upgrade_safe(bind)

    drop_archive_sync_revision_triggers(bind)
    _drop_attempt_identity_trigger(bind)
    _drop_sqlite_audio_triggers(bind)
    for column in _ATTEMPT_COLUMNS:
        op.add_column(
            "podcast_stage_attempts",
            sa.Column(column, sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
    for column in _TEXT_COLUMNS:
        op.add_column(
            "podcast_text_artifacts",
            sa.Column(column, sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_settings_fingerprint",
            "settings_fingerprint IS NULL OR "
            "(length(settings_fingerprint) = 64 AND "
            "settings_fingerprint = lower(settings_fingerprint))",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_output_binding",
            "(output_artifact_id IS NULL AND output_artifact_kind IS NULL) OR "
            "(output_artifact_id IS NOT NULL AND output_artifact_kind IS NOT NULL "
            "AND length(trim(output_artifact_id)) > 0 "
            "AND length(trim(output_artifact_kind)) > 0 "
            "AND length(output_hash) = 64 AND output_hash = lower(output_hash))",
        )

    with op.batch_alter_table(
        "podcast_text_artifacts", recreate=recreate
    ) as batch:
        batch.drop_constraint("ck_podcast_text_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_text_artifacts_kind",
            "kind IN ('publisher_transcript','normalized_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
        )
        batch.create_check_constraint(
            "ck_podcast_text_artifacts_processing_attempt_pair",
            "(processing_id IS NULL AND producing_attempt_id IS NULL) OR "
            "(processing_id IS NOT NULL AND producing_attempt_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_text_artifacts_normalized_bound",
            "kind <> 'normalized_transcript' OR processing_id IS NOT NULL OR "
            "length(trim(authority_id)) > 0",
        )
        batch.create_unique_constraint(
            "uq_podcast_text_artifacts_producing_attempt",
            ["producing_attempt_id"],
        )
        batch.create_unique_constraint(
            "uq_podcast_text_artifacts_processing_kind",
            ["processing_id", "kind"],
        )
        batch.create_foreign_key(
            "fk_podcast_text_artifacts_attempt_owner",
            "podcast_stage_attempts",
            ["producing_attempt_id", "processing_id"],
            ["id", "processing_id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table(
        "podcast_text_publications", recreate=recreate
    ) as batch:
        batch.drop_constraint("ck_podcast_text_publications_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_text_publications_kind",
            "kind IN ('publisher_transcript','normalized_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
        )

    _create_index(
        "ix_podcast_stage_attempts_settings_fingerprint",
        "podcast_stage_attempts",
        ["settings_fingerprint"],
    )
    _create_index(
        "ix_podcast_stage_attempts_output_artifact_id",
        "podcast_stage_attempts",
        ["output_artifact_id"],
    )
    _create_index(
        "ix_podcast_text_artifacts_processing_id",
        "podcast_text_artifacts",
        ["processing_id"],
    )
    _create_index(
        "ix_podcast_text_artifacts_producing_attempt_id",
        "podcast_text_artifacts",
        ["producing_attempt_id"],
    )
    _install_attempt_audit(bind, current=True)
    install_archive_sync_revision_triggers(bind)
    _install_audio_triggers(bind)


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    _assert_downgrade_safe(bind)
    drop_archive_sync_revision_triggers(bind)
    _drop_attempt_identity_trigger(bind)
    _drop_sqlite_audio_triggers(bind)

    for name, table in (
        ("ix_podcast_text_artifacts_producing_attempt_id", "podcast_text_artifacts"),
        ("ix_podcast_text_artifacts_processing_id", "podcast_text_artifacts"),
        ("ix_podcast_stage_attempts_output_artifact_id", "podcast_stage_attempts"),
        ("ix_podcast_stage_attempts_settings_fingerprint", "podcast_stage_attempts"),
    ):
        if name in _index_names(table):
            op.drop_index(name, table_name=table)

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_text_publications", recreate=recreate
    ) as batch:
        batch.drop_constraint("ck_podcast_text_publications_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_text_publications_kind",
            "kind IN ('publisher_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
        )

    with op.batch_alter_table(
        "podcast_text_artifacts", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "fk_podcast_text_artifacts_attempt_owner", type_="foreignkey"
        )
        batch.drop_constraint(
            "uq_podcast_text_artifacts_processing_kind", type_="unique"
        )
        batch.drop_constraint(
            "uq_podcast_text_artifacts_producing_attempt", type_="unique"
        )
        batch.drop_constraint(
            "ck_podcast_text_artifacts_normalized_bound", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_text_artifacts_processing_attempt_pair", type_="check"
        )
        batch.drop_constraint("ck_podcast_text_artifacts_kind", type_="check")
        batch.create_check_constraint(
            "ck_podcast_text_artifacts_kind",
            "kind IN ('publisher_transcript','transcript_zh',"
            "'digest_blog_zh','narration_script_zh')",
        )
        batch.drop_column("producing_attempt_id")
        batch.drop_column("processing_id")

    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "ck_podcast_stage_attempts_output_binding", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_settings_fingerprint", type_="check"
        )
        batch.drop_column("output_artifact_kind")
        batch.drop_column("output_artifact_id")
        batch.drop_column("settings_fingerprint")

    _install_attempt_audit(bind, current=False)
    install_archive_sync_revision_triggers(bind)
    _install_audio_triggers(bind)
