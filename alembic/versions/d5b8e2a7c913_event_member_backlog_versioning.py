"""event member backlog keyed by (authority, article) with a revision basis (issue #121, review R2 N2)

待补账改为同一 authority 下一篇文章只留一行,并记下断言这次归属的事件页行 authority revision,
重放按新旧归属比较、不再按 event_id 顺序无条件覆盖。表是接收端本地队列:存量行搬到新形状
(revision 置 0 = 最旧依据,同文章多行只留一行),事件已撤回 / 本地事件的行丢弃。

Revision ID: d5b8e2a7c913
Revises: 7c3d9e1f5a24
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


revision: str = "d5b8e2a7c913"
down_revision: Union[str, Sequence[str], None] = "7c3d9e1f5a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "event_member_backlog"
STAGING = "event_member_backlog_v2"


def _create_new_shape(name: str) -> None:
    op.create_table(
        name,
        sa.Column("authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "payload_json",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("authority_id", "article_id"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if TABLE in existing:
        columns = {column["name"] for column in inspector.get_columns(TABLE)}
        if {"authority_id", "revision"} <= columns:
            return  # create_all 收养:已是新形状
        _create_new_shape(STAGING)
        bind.exec_driver_sql(
            f"INSERT INTO {STAGING}(authority_id, article_id, event_id, revision, "
            "payload_json, first_seen_at, attempts, updated_at) "
            "SELECT e.authority_id, b.article_id, MIN(b.event_id), 0, "
            "MIN(b.payload_json), MIN(b.first_seen_at), MAX(b.attempts), MAX(b.updated_at) "
            f"FROM {TABLE} b JOIN events e ON e.id = b.event_id "
            "WHERE e.authority_id <> '' GROUP BY e.authority_id, b.article_id"
        )
        op.drop_table(TABLE)
        op.rename_table(STAGING, TABLE)
    else:
        _create_new_shape(TABLE)
    op.create_index(
        op.f("ix_event_member_backlog_event_id"), TABLE, ["event_id"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(TABLE)
    op.create_table(
        TABLE,
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "payload_json",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "article_id"),
    )
