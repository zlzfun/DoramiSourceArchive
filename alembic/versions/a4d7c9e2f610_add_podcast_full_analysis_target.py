"""add durable Podcast full-analysis target

Revision ID: a4d7c9e2f610
Revises: e9c4b7a1d2f6
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4d7c9e2f610"
down_revision: Union[str, Sequence[str], None] = "e9c4b7a1d2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_constraint(*, include_full_analysis: bool) -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    targets = (
        "'transcript','full_analysis','digest_blog','digest_audio'"
        if include_full_analysis
        else "'transcript','digest_blog','digest_audio'"
    )
    with op.batch_alter_table("podcast_processings", recreate=recreate) as batch:
        batch.drop_constraint(
            "ck_podcast_processings_requested_target", type_="check"
        )
        batch.create_check_constraint(
            "ck_podcast_processings_requested_target",
            f"requested_target IN ({targets})",
        )


def _drop_sqlite_rebuild_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name != "sqlite":
        return
    # SQLite's table recreation temporarily removes podcast_processings.  Drop
    # every trigger whose body references it, including guards installed on the
    # reservation/ledger tables, before Alembic renames the replacement table.
    rows = bind.execute(
        sa.text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'trigger' AND sql LIKE '%podcast_processings%'"
        )
    ).all()
    for (name,) in rows:
        escaped = str(name).replace('"', '""')
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{escaped}"')


def _restore_processing_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import (
            _podcast_audio_dependency_trigger_sql,
            _podcast_processing_audit_trigger_sql,
            _podcast_processing_command_audit_sql,
        )

        statements = (*_podcast_processing_audit_trigger_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=True,
            include_usage_settlement_mode=True,
        ), *_podcast_processing_command_audit_sql(),
            *_podcast_audio_dependency_trigger_sql(include_attempt_binding=True))
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=True,
            include_usage_settlement_mode=True,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def upgrade() -> None:
    bind = op.get_bind()
    _drop_sqlite_rebuild_triggers(bind)
    _replace_constraint(include_full_analysis=True)
    _restore_processing_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    live = bind.execute(
        sa.text(
            "SELECT 1 FROM podcast_processings "
            "WHERE requested_target = 'full_analysis' LIMIT 1"
        )
    ).first()
    if live is not None:
        raise RuntimeError(
            "cannot downgrade while full_analysis Podcast processings exist"
        )
    _drop_sqlite_rebuild_triggers(bind)
    _replace_constraint(include_full_analysis=False)
    _restore_processing_triggers(bind)
