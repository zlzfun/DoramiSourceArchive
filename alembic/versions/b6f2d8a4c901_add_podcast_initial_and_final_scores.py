"""add independent Podcast initial and final score columns

Revision ID: b6f2d8a4c901
Revises: f3c8a1d6e205
Create Date: 2026-09-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "b6f2d8a4c901"
down_revision: Union[str, Sequence[str], None] = "f3c8a1d6e205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("article_analyses")
    }


def upgrade() -> None:
    columns = _columns()
    if "podcast_initial_score" not in columns or "podcast_final_score" not in columns:
        bind = op.get_bind()
        drop_archive_sync_revision_triggers(bind)
        with op.batch_alter_table("article_analyses") as batch:
            if "podcast_initial_score" not in columns:
                batch.add_column(sa.Column("podcast_initial_score", sa.Float(), nullable=True))
            if "podcast_final_score" not in columns:
                batch.add_column(sa.Column("podcast_final_score", sa.Float(), nullable=True))
            batch.create_check_constraint(
                "ck_article_analyses_podcast_initial_score",
                "podcast_initial_score IS NULL OR "
                "(podcast_initial_score >= 1.0 AND podcast_initial_score <= 10.0)",
            )
            batch.create_check_constraint(
                "ck_article_analyses_podcast_final_score",
                "podcast_final_score IS NULL OR "
                "(podcast_final_score >= 1.0 AND podcast_final_score <= 10.0)",
            )
        install_archive_sync_revision_triggers(
            bind,
            include_podcast_integrity=True,
            include_podcast_audio=True,
        )
    # Existing rows can recover the score matching their current authoritative
    # basis.  A pre-full-analysis initial score was not retained historically,
    # so transcript rows intentionally leave podcast_initial_score unknown.
    op.execute(
        """
        UPDATE article_analyses
        SET podcast_initial_score = quality_score
        WHERE analysis_basis = 'podcast_show_notes' AND quality_score IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE article_analyses
        SET podcast_final_score = quality_score
        WHERE analysis_basis IN ('publisher_transcript','asr_transcript')
          AND quality_score IS NOT NULL
        """
    )


def downgrade() -> None:
    # The direct parent is itself an irreversible data-retirement boundary.
    # Refuse before mutating this head so a request to cross that boundary does
    # not first leave the database partially downgraded at f3c8a1d6e205.
    raise RuntimeError(
        "Podcast source audio blobs were intentionally retired; restore the "
        "pre-upgrade database and CAS backup to downgrade"
    )
