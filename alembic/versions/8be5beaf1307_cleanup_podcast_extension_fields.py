"""cleanup_podcast_extension_fields

Revision ID: 8be5beaf1307
Revises: b6f2d8a4c901
Create Date: 2026-09-11
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)

# revision identifiers, used by Alembic.
revision: str = "8be5beaf1307"
down_revision: Union[str, Sequence[str], None] = "b6f2d8a4c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    drop_archive_sync_revision_triggers(bind)
    try:
        rows = bind.execute(
            sa.text(
                """
                SELECT id, extensions_json
                FROM articles
                WHERE content_type = 'podcast_episode'
                  AND extensions_json IS NOT NULL
                  AND extensions_json != ''
                """
            )
        ).all()
        for article_id, extensions_json in rows:
            try:
                extensions = json.loads(extensions_json or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(extensions, dict):
                continue
            modified = False
            for key in ("condensed_audio_url", "condensed_duration_seconds"):
                if key in extensions:
                    extensions.pop(key, None)
                    modified = True
            guide = extensions.get("premium_guide")
            if isinstance(guide, dict):
                for subkey in (
                    "condensed_audio_url",
                    "condensed_duration_seconds",
                    "audio_url",
                ):
                    if subkey in guide:
                        guide.pop(subkey, None)
                        modified = True
            if modified:
                new_json = json.dumps(
                    extensions,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                bind.execute(
                    sa.text(
                        "UPDATE articles SET extensions_json = :new_json WHERE id = :id"
                    ),
                    {"new_json": new_json, "id": article_id},
                )
    finally:
        install_archive_sync_revision_triggers(
            bind,
            include_podcast_integrity=True,
            include_podcast_audio=True,
        )


def downgrade() -> None:
    # The parent revision irreversibly retired source-audio blobs. Refuse here
    # before Alembic moves the version marker and only then discovers that
    # lower irreversible boundary.
    raise RuntimeError(
        "Podcast source audio blobs were intentionally retired; restore the "
        "pre-upgrade database and CAS backup to downgrade"
    )
