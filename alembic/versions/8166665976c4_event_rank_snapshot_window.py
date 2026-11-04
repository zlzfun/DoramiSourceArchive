"""event rank snapshots per reader window (issue #221)

热点事件榜默认窗口改 7 天、段内可切近 2 天(issue #221 拍板 4);名次变化只与同窗口的快照比(拍板 7),
所以 `event_rank_snapshots` 加 `window_hours` 列,唯一键与名次索引都带上窗口。存量行都是 48 小时窗写的,
server_default 48 即其真值。表不同步(接收端自建快照,契约 archive_sync.md),上面没有同步触发器。

Revision ID: 8166665976c4
Revises: b248c7a10e31
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8166665976c4"
down_revision: Union[str, Sequence[str], None] = "b248c7a10e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "event_rank_snapshots"
OLD_UNIQUE = "uq_event_rank_snapshots_date_event"
NEW_UNIQUE = "uq_event_rank_snapshots_date_window_event"
OLD_INDEX = "ix_event_rank_snapshots_date_rank"
NEW_INDEX = "ix_event_rank_snapshots_date_window_rank"


def _state() -> tuple[set[str], set[str], set[str]]:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    uniques = {item["name"] for item in inspector.get_unique_constraints(TABLE) if item.get("name")}
    indexes = {item["name"] for item in inspector.get_indexes(TABLE) if item.get("name")}
    return columns, uniques, indexes


def upgrade() -> None:
    columns, uniques, indexes = _state()
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(TABLE, recreate=recreate) as batch:
        if "window_hours" not in columns:
            batch.add_column(sa.Column("window_hours", sa.Integer(), server_default=sa.text("48"), nullable=False))
        if OLD_UNIQUE in uniques:
            batch.drop_constraint(OLD_UNIQUE, type_="unique")
        if NEW_UNIQUE not in uniques:
            batch.create_unique_constraint(NEW_UNIQUE, ["snapshot_date", "window_hours", "event_id"])
        if OLD_INDEX in indexes:
            batch.drop_index(OLD_INDEX)
        if NEW_INDEX not in indexes:
            batch.create_index(NEW_INDEX, ["snapshot_date", "window_hours", "rank"], unique=False)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(f"DELETE FROM {TABLE} WHERE window_hours != 48")
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(TABLE, recreate=recreate) as batch:
        batch.drop_index(NEW_INDEX)
        batch.drop_constraint(NEW_UNIQUE, type_="unique")
        batch.create_unique_constraint(OLD_UNIQUE, ["snapshot_date", "event_id"])
        batch.create_index(OLD_INDEX, ["snapshot_date", "rank"], unique=False)
        batch.drop_column("window_hours")
