"""make public podcast nodes always collectable

Revision ID: b34d9f1a72e1
Revises: a34c7e2f91d0
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "b34d9f1a72e1"
down_revision: Union[str, Sequence[str], None] = "a34c7e2f91d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "UPDATE source_configs SET is_active = 1, fetch_interval_minutes = NULL "
        "WHERE owner_username = '' "
        "AND lower(source_type) IN ('podcast', 'podcast_rss')"
    )


def downgrade() -> None:
    # Source-level activation for public podcasts has been retired. There is no
    # truthful prior value to restore after all rows have become collection nodes.
    pass
