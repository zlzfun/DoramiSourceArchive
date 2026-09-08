"""sync published Podcast digest audio

Revision ID: 9c5e2a7d1b40
Revises: 8b4e1d7c2a90
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "9c5e2a7d1b40"
down_revision: Union[str, Sequence[str], None] = "8b4e1d7c2a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_STREAM_CHECK = (
    "stream IN ('sources','articles','analyses','media','source_states','podcast_texts')"
)
_NEW_STREAM_CHECK = (
    "stream IN ('sources','articles','analyses','media','source_states',"
    "'podcast_texts','podcast_audio')"
)


def _replace_stream_check(expression: str, *, include_podcast_audio: bool) -> None:
    bind = op.get_bind()
    drop_archive_sync_revision_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "archive_sync_entity_states", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "ck_archive_sync_entity_states_stream", type_="check"
        )
        batch.create_check_constraint(
            "ck_archive_sync_entity_states_stream", expression
        )
    install_archive_sync_revision_triggers(
        bind, include_podcast_audio=include_podcast_audio
    )


def upgrade() -> None:
    _replace_stream_check(_NEW_STREAM_CHECK, include_podcast_audio=True)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "DELETE FROM archive_sync_entity_states WHERE stream = 'podcast_audio'"
    )
    _replace_stream_check(_OLD_STREAM_CHECK, include_podcast_audio=False)
