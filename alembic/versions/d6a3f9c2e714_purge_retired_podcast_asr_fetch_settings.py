"""Purge retired Podcast ASR public-fetch settings.

Revision ID: d6a3f9c2e714
Revises: b2d8e4f6a9c1
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6a3f9c2e714"
down_revision: Union[str, Sequence[str], None] = "b2d8e4f6a9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RETIRED_KEYS = (
    "podcast_asr_fetch_public_base_url",
    "podcast_asr_fetch_signing_secret",
    "podcast_asr_fetch_previous_signing_secret",
    "podcast_asr_fetch_url_ttl_seconds",
    "podcast_asr_fetch_clock_skew_seconds",
    "podcast_asr_fetch_min_remaining_seconds",
)


def upgrade() -> None:
    app_settings = sa.table(
        "app_settings",
        sa.column("key", sa.String()),
    )
    op.execute(sa.delete(app_settings).where(app_settings.c.key.in_(_RETIRED_KEYS)))


def downgrade() -> None:
    # Retired secrets and deployment-specific values cannot be reconstructed.
    pass
