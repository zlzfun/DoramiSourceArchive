"""add personal digest desired intents and readiness state

Revision ID: a7d4e2f9c1b8
Revises: d8b3f1a6c9e2
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "a7d4e2f9c1b8"
down_revision: Union[str, Sequence[str], None] = "d8b3f1a6c9e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IRREVERSIBLE_CLEANUP_KEY = "migration:a7d4e2f9c1b8:digest_cleanup"


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def _supersede_duplicate_active_editions() -> bool:
    """Keep only the latest pre-upgrade lifecycle row per owner/day."""

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, owner_username, report_date, revision, status "
        "FROM personal_digest_editions WHERE status <> 'superseded' "
        "ORDER BY owner_username, report_date, revision DESC, id DESC"
    )).mappings()
    seen: set[tuple[str, str]] = set()
    stale_ids: list[int] = []
    for row in rows:
        key = (str(row["owner_username"]), str(row["report_date"]))
        if key in seen and row["status"] in {"pending", "generating"}:
            stale_ids.append(int(row["id"]))
        else:
            seen.add(key)
    for edition_id in stale_ids:
        bind.execute(sa.text(
            "UPDATE personal_digest_editions SET status = 'superseded', "
            "generation_token = NULL, generation_lease_expires_at = NULL, "
            "desired_generation_reason = NULL, desired_requested_at = NULL, "
            "desired_first_open_at = NULL WHERE id = :edition_id"
        ), {"edition_id": edition_id})
    return bool(stale_ids)


def upgrade() -> None:
    for column in (
        sa.Column("desired_generation_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("desired_requested_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("desired_first_open_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sync_stale", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("analysis_incomplete", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    ):
        _add_column_if_missing("personal_digest_editions", column)
    _create_index_if_missing(
        "personal_digest_editions",
        "ix_personal_digest_editions_desired_requested_at",
        ["desired_requested_at"],
    )
    if _supersede_duplicate_active_editions():
        bind = op.get_bind()
        exists = bind.execute(
            sa.text("SELECT 1 FROM app_settings WHERE key = :key"),
            {"key": _IRREVERSIBLE_CLEANUP_KEY},
        ).first()
        if exists is None:
            bind.execute(
                sa.text("INSERT INTO app_settings (key, value) VALUES (:key, :value)"),
                {
                    "key": _IRREVERSIBLE_CLEANUP_KEY,
                    "value": json.dumps({"active": True}, separators=(",", ":")),
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    active_state = bind.execute(sa.text(
        "SELECT 1 FROM personal_digest_editions "
        "WHERE desired_generation_reason IS NOT NULL "
        "OR desired_requested_at IS NOT NULL OR desired_first_open_at IS NOT NULL "
        "OR sync_stale = 1 OR analysis_incomplete = 1 LIMIT 1"
    )).first()
    cleanup_marker = bind.execute(
        sa.text("SELECT 1 FROM app_settings WHERE key = :key"),
        {"key": _IRREVERSIBLE_CLEANUP_KEY},
    ).first()
    if active_state is not None or cleanup_marker is not None:
        raise RuntimeError(
            "拒绝降级个人早报意图状态：请先停止 worker，并恢复升级前备份。"
        )
    op.drop_index(
        "ix_personal_digest_editions_desired_requested_at",
        table_name="personal_digest_editions",
    )
    op.drop_column("personal_digest_editions", "analysis_incomplete")
    op.drop_column("personal_digest_editions", "sync_stale")
    op.drop_column("personal_digest_editions", "desired_first_open_at")
    op.drop_column("personal_digest_editions", "desired_requested_at")
    op.drop_column("personal_digest_editions", "desired_generation_reason")
