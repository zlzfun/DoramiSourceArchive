"""event member backlog keyed by (authority, article) with a revision basis (issue #121, review R2 N2)

待补账改为同一 authority 下一篇文章只留一行,并记下断言这次归属的事件页行 authority revision,
重放按新旧归属比较、不再按 event_id 顺序无条件覆盖。表是接收端本地队列:存量行按 (authority, article)
只留一**整行**,选行首要判据 = 该事件在 ``archive_sync_entity_states`` 的已接收 revision(大者胜;
旧版重放会把所有待补行的 updated_at 抹成同一轮时间,时间不可靠),其次 updated_at,最后 event_id;
两行 revision 都查不到(0)时按时间 / ID 兜底并 WARNING「依据不足」。被淘汰的冲突行与事件已撤回 /
本地事件的行都记日志,不静默丢弃(检视 R3 / R4)。

Revision ID: d5b8e2a7c913
Revises: 7c3d9e1f5a24
Create Date: 2026-10-23
"""

from __future__ import annotations

import logging
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
logger = logging.getLogger("alembic.runtime.migration")


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


def _move_legacy_rows(bind) -> None:
    """旧形状 → 新形状:每个 (authority, article) 保留最新的一整行,revision 取该事件已接收的 revision。"""

    legacy = bind.exec_driver_sql(
        "SELECT b.event_id, b.article_id, b.payload_json, b.first_seen_at, b.attempts, "
        "b.updated_at, e.authority_id, "
        "(SELECT s.revision FROM archive_sync_entity_states s "
        " WHERE s.stream = 'events' AND s.identity = CAST(b.event_id AS TEXT) "
        " AND s.authority_id = e.authority_id) AS applied_revision "
        f"FROM {TABLE} b LEFT JOIN events e ON e.id = b.event_id"
    ).fetchall()
    winners: dict[tuple[str, str], tuple[tuple[int, str, int], tuple]] = {}
    discarded: list[tuple[int, str, str]] = []
    weak_basis: set[tuple[str, str]] = set()
    for row in legacy:
        event_id, article_id, payload_json, first_seen_at, attempts, updated_at, authority, applied = row
        if not authority:
            discarded.append((event_id, article_id, "event missing or node-local"))
            continue
        key = (str(authority), str(article_id))
        # 已接收 revision 是唯一可靠的新旧依据;updated_at 可能被旧版重放抹平,只作兜底。
        rank = (int(applied or 0), str(updated_at or ""), int(event_id))
        candidate = (
            str(authority), str(article_id), int(event_id), int(applied or 0),
            payload_json if payload_json is not None else "{}",
            str(first_seen_at or updated_at or ""), int(attempts or 0), str(updated_at or ""),
        )
        current = winners.get(key)
        if current is not None and rank[0] == 0 and current[0][0] == 0:
            weak_basis.add(key)
        if current is None or rank > current[0]:
            if current is not None:
                discarded.append((current[1][2], article_id, f"superseded by event {event_id}"))
            winners[key] = (rank, candidate)
        else:
            discarded.append((event_id, article_id, f"superseded by event {current[1][2]}"))
    for _rank, values in winners.values():
        bind.exec_driver_sql(
            f"INSERT INTO {STAGING}(authority_id, article_id, event_id, revision, "
            "payload_json, first_seen_at, attempts, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    for key in sorted(weak_basis):
        logger.warning(
            "event_member_backlog migration: insufficient basis for article=%s (authority=%s) — "
            "no applied revision for any conflicting row; kept event=%s by updated_at / event_id",
            key[1], key[0], winners[key][1][2],
        )
    for event_id, article_id, reason in discarded:
        logger.warning(
            "event_member_backlog migration dropped legacy row event=%s article=%s: %s",
            event_id, article_id, reason,
        )
    logger.info(
        "event_member_backlog migration moved %d of %d legacy rows", len(winners), len(legacy)
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
        _move_legacy_rows(bind)
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
