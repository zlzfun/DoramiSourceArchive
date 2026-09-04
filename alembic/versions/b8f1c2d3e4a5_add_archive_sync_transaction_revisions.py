"""add commit-ordered archive sync entity revisions

Revision ID: b8f1c2d3e4a5
Revises: a8e5f3c0d2b9
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from storage.archive_sync_revision import install_archive_sync_revision_triggers


revision: str = "b8f1c2d3e4a5"
down_revision: Union[str, Sequence[str], None] = "a8e5f3c0d2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRIGGERS = (
    "archive_sync_source_insert", "archive_sync_source_update", "archive_sync_source_delete",
    "archive_sync_source_nonpublic_insert", "archive_sync_source_remote_handoff",
    "archive_sync_source_scope_exit", "archive_sync_source_scope_enter",
    "archive_sync_article_insert", "archive_sync_article_update",
    "archive_sync_article_scope_exit", "archive_sync_article_scope_enter",
    "archive_sync_article_remote_handoff", "archive_sync_article_delete",
    "archive_sync_analysis_insert", "archive_sync_analysis_update", "archive_sync_analysis_delete",
    "archive_sync_assignment_insert", "archive_sync_assignment_update", "archive_sync_assignment_delete",
    "archive_sync_media_insert", "archive_sync_media_update",
    "archive_sync_source_state_insert", "archive_sync_source_state_update",
    "archive_sync_source_state_delete",
)


def _index(table: str, name: str, columns: list[str]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=False)


def _backfill() -> None:
    statements = (
        "SELECT 'sources', source_id, '', 0, 'upsert', updated_at FROM source_configs "
        "WHERE owner_username = '' AND collection_authority_id = ''",
        "SELECT 'articles', a.id, '', 0, 'upsert', "
        "COALESCE(NULLIF(a.archive_updated_at, ''), a.fetched_date) "
        "FROM articles a LEFT JOIN source_configs sc ON sc.source_id = a.source_id "
        "WHERE a.analysis_authority_id = '' "
        "AND a.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (sc.source_id IS NULL OR (sc.owner_username = '' AND sc.collection_authority_id = ''))",
        "SELECT 'analyses', aa.article_id, '', 0, 'upsert', aa.updated_at "
        "FROM article_analyses aa JOIN articles a ON a.id = aa.article_id "
        "LEFT JOIN source_configs sc ON sc.source_id = a.source_id "
        "WHERE aa.authority_id = '' AND a.analysis_authority_id = '' "
        "AND a.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (sc.source_id IS NULL OR (sc.owner_username = '' AND sc.collection_authority_id = ''))",
        "SELECT 'media', url_hash, '', 0, 'upsert', updated_at FROM media_assets "
        "WHERE sync_authority_id = ''",
        "SELECT 'source_states', ss.source_id, '', 0, 'upsert', ss.updated_at "
        "FROM source_states ss LEFT JOIN source_configs sc ON sc.source_id = ss.source_id "
        "WHERE ss.authority_id = '' "
        "AND ss.source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\' "
        "AND (sc.source_id IS NULL OR (sc.owner_username = '' AND sc.collection_authority_id = ''))",
    )
    prefix = (
        "INSERT INTO archive_sync_entity_states "
        "(stream, identity, authority_id, revision, operation, updated_at) "
    )
    for select_sql in statements:
        op.execute(prefix + select_sql + " ON CONFLICT(stream, identity) DO NOTHING")


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table, column in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
    ):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' LIMIT 1"
        )).first() is not None:
            raise RuntimeError("拒绝降级 Archive Sync：数据库仍含远端 authority。请先停止 worker，并恢复升级前备份。")
    if bind.execute(sa.text("SELECT 1 FROM remote_candidate_evidence LIMIT 1")).first() is not None:
        raise RuntimeError("拒绝降级 Archive Sync：数据库仍含远端 Candidate 证据。请先停止 worker，并恢复升级前备份。")
    if bind.execute(sa.text(
        "SELECT 1 FROM personal_digest_editions WHERE desired_generation_reason IS NOT NULL "
        "OR desired_requested_at IS NOT NULL OR desired_first_open_at IS NOT NULL "
        "OR sync_stale = 1 OR analysis_incomplete = 1 LIMIT 1"
    )).first() is not None:
        raise RuntimeError("拒绝降级 Archive Sync：数据库仍含新版个人早报状态。请先停止 worker，并恢复升级前备份。")
    marker = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:v2_consumer_mode'"
    )).scalar_one_or_none()
    schedule = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:schedule'"
    )).scalar_one_or_none()
    compact_schedule = str(schedule or "").replace(" ", "")
    if marker is not None or ('"enabled":true' in compact_schedule and '"protocol":"v2"' in compact_schedule):
        raise RuntimeError("拒绝降级 Archive Sync：consumer 围栏仍生效。请先停止 worker，并恢复升级前备份。")


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "archive_sync_clock" not in tables:
        op.create_table(
            "archive_sync_clock",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.CheckConstraint("revision >= 0", name="ck_archive_sync_clock_revision"),
            sa.PrimaryKeyConstraint("id"),
        )
    if "archive_sync_entity_states" not in tables:
        op.create_table(
            "archive_sync_entity_states",
            sa.Column("stream", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("identity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("operation", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.CheckConstraint("revision >= 0", name="ck_archive_sync_entity_states_revision"),
            sa.CheckConstraint(
                "stream IN ('sources','articles','analyses','media','source_states')",
                name="ck_archive_sync_entity_states_stream",
            ),
            sa.CheckConstraint(
                "operation IN ('upsert','tombstone')",
                name="ck_archive_sync_entity_states_operation",
            ),
            sa.PrimaryKeyConstraint("stream", "identity"),
        )
    _index("archive_sync_entity_states", "ix_archive_sync_entity_states_authority_id", ["authority_id"])
    _index("archive_sync_entity_states", "ix_archive_sync_entity_states_revision", ["revision"])
    _index(
        "archive_sync_entity_states",
        "ix_archive_sync_entity_states_stream_authority_revision_identity",
        ["stream", "authority_id", "revision", "identity"],
    )
    op.execute("INSERT OR IGNORE INTO archive_sync_clock (id, revision) VALUES (1, 0)")
    _backfill()
    install_archive_sync_revision_triggers(op.get_bind())


def downgrade() -> None:
    _assert_downgrade_safe()
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for name in _TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    for name in (
        "ix_archive_sync_entity_states_stream_authority_revision_identity",
        "ix_archive_sync_entity_states_revision",
        "ix_archive_sync_entity_states_authority_id",
    ):
        op.drop_index(name, table_name="archive_sync_entity_states")
    op.drop_table("archive_sync_entity_states")
    op.drop_table("archive_sync_clock")
