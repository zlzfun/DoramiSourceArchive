"""drop retired Podcast admission and rights data

Revision ID: 8b4e1d7c2a90
Revises: 7a3f9c2e6d10
Create Date: 2026-09-07
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "8b4e1d7c2a90"
down_revision: Union[str, Sequence[str], None] = "7a3f9c2e6d10"
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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    drop_archive_sync_revision_triggers(bind)
    _drop_audio_triggers(bind)

    if "podcast_text_artifacts" in tables:
        columns = {
            column["name"]
            for column in inspector.get_columns("podcast_text_artifacts")
        }
        if "rights_version" in columns:
            recreate = "always" if bind.dialect.name == "sqlite" else "auto"
            with op.batch_alter_table(
                "podcast_text_artifacts", recreate=recreate
            ) as batch:
                batch.drop_constraint(
                    "ck_podcast_text_artifacts_rights_version", type_="check"
                )
                batch.drop_column("rights_version")

    # Profiles reference reviews, so the projection must be dropped first.
    for table in (
        "podcast_source_profiles",
        "podcast_source_reviews",
        "podcast_rights",
    ):
        if table in tables:
            op.drop_table(table)

    install_archive_sync_revision_triggers(bind)
    _install_audio_triggers(bind)


def downgrade() -> None:
    # The removed decisions cannot be reconstructed. Recreate only the empty
    # legacy structure so older Alembic revisions can still be exercised and a
    # schema downgrade never pretends to restore deleted policy records.
    versions_dir = Path(__file__).resolve().parent
    governance = runpy.run_path(
        str(versions_dir / "9f6c4b2e1a07_add_podcast_governance.py")
    )
    governance["upgrade"]()
    bind = op.get_bind()
    for table in (
        "podcast_source_profiles",
        "podcast_source_reviews",
        "podcast_rights",
    ):
        bind.execute(sa.text(f'DELETE FROM "{table}"'))

    rights_constraints = runpy.run_path(
        str(versions_dir / "4d8e1b7c3a90_harden_podcast_rights_constraints.py")
    )
    rights_constraints["upgrade"]()

    drop_archive_sync_revision_triggers(bind)
    _drop_audio_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_text_artifacts", recreate=recreate
    ) as batch:
        batch.add_column(
            sa.Column(
                "rights_version",
                sa.String(),
                nullable=False,
                server_default="retired-governance",
            )
        )
        batch.create_check_constraint(
            "ck_podcast_text_artifacts_rights_version",
            "length(rights_version) > 0",
        )
    install_archive_sync_revision_triggers(bind)
    _install_audio_triggers(bind)
