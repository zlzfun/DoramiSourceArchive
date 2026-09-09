"""align podcast sources with collection jobs

Revision ID: a34c7e2f91d0
Revises: 9c5e2a7d1b40
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from storage.archive_sync_revision import drop_archive_sync_revision_triggers


revision: str = "a34c7e2f91d0"
down_revision: Union[str, Sequence[str], None] = "9c5e2a7d1b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "UPDATE source_configs SET "
        "signal_strength = CASE signal_strength "
        "WHEN 'high' THEN 'high_signal' WHEN 'medium' THEN 'medium_signal' "
        "ELSE signal_strength END, "
        "noise_risk = CASE noise_risk "
        "WHEN 'low' THEN 'low_noise' WHEN 'medium' THEN 'medium_noise' "
        "ELSE noise_risk END, "
        "fetch_reliability = CASE fetch_reliability "
        "WHEN 'high' THEN 'stable_public' WHEN 'blocked' THEN 'blocked_or_fragile' "
        "ELSE fetch_reliability END, "
        "fetch_interval_minutes = NULL "
        "WHERE lower(source_type) IN ('podcast', 'podcast_rss')"
    )
    # The preceding head installs archive-sync triggers that reference this
    # table. SQLite batch replacement temporarily renames it, so clear those
    # triggers first; alembic/env.py reinstalls the complete current set after
    # the migration transaction.
    columns = {column["name"] for column in sa.inspect(bind).get_columns("source_configs")}
    if "cron_expr" in columns:
        drop_archive_sync_revision_triggers(bind)
        with op.batch_alter_table("source_configs") as batch:
            batch.drop_column("cron_expr")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("source_configs")}
    if "cron_expr" not in columns:
        drop_archive_sync_revision_triggers(bind)
        with op.batch_alter_table("source_configs") as batch:
            batch.add_column(
                sa.Column("cron_expr", sa.String(), nullable=False, server_default="")
            )
