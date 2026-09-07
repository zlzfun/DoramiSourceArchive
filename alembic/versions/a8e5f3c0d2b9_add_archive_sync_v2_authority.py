"""add archive sync v2 authority and candidate evidence

Revision ID: a8e5f3c0d2b9
Revises: a7d4e2f9c1b8
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
from typing import Sequence, Union
from urllib.parse import parse_qsl, urlsplit

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "a8e5f3c0d2b9"
down_revision: Union[str, Sequence[str], None] = "a7d4e2f9c1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CREDENTIAL_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "auth", "authkey", "authorization",
    "bearer", "credential", "credentials", "jwt", "key", "pass", "password",
    "pwd", "secret", "session", "session_id", "sessionid", "sig", "signature", "token",
}
_CREDENTIAL_KEY_MARKERS = (
    "auth", "bearer", "credential", "jwt", "key", "pass", "pwd", "secret",
    "session", "sig", "token",
)
_CREDENTIAL_PATH_MARKERS = {
    "auth", "bearer", "credential", "jwt", "key", "pass", "private", "pwd",
    "secret", "session", "signed", "token",
}
_PUBLIC_QUERY_KEYS = {
    "category", "channel_id", "feed", "filter", "format", "lang", "language",
    "limit", "order", "output", "page", "playlist_id", "q", "query", "search",
    "sort", "tag", "tags", "topic", "type",
}
_V2_CONSUMER_MODE_KEY = "remote_sync:v2_consumer_mode"
_REMOTE_SYNC_SCHEDULE_KEY = "remote_sync:schedule"


def _looks_like_opaque_credential(value: str) -> bool:
    compact = "".join(char for char in str(value or "") if char.isalnum())
    return len(compact) >= 24


def _url_has_credentials(url: str) -> bool:
    parsed = urlsplit(str(url or ""))
    if parsed.username is not None or parsed.password is not None:
        return True
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = {key.casefold() for key, _value in query}
    if query_keys & _CREDENTIAL_QUERY_KEYS or any(
        marker in key for key in query_keys for marker in _CREDENTIAL_KEY_MARKERS
    ):
        return True
    if any(
        key.casefold() not in _PUBLIC_QUERY_KEYS and _looks_like_opaque_credential(value)
        for key, value in query
    ):
        return True
    return any(
        segment.casefold() in _CREDENTIAL_PATH_MARKERS
        or _looks_like_opaque_credential(segment)
        for segment in parsed.path.split("/") if segment
    )


def _backfill_custom_rss_analysis_policy() -> None:
    """Enable public custom feeds while retaining signed-feed MaaS protection."""

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT source_id, url, params_json FROM source_configs WHERE owner_username <> ''"
    )).mappings()
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
            params_valid = isinstance(params, dict)
        except (TypeError, ValueError, json.JSONDecodeError):
            params_valid = False
            params = None
        classified = params.get("credentialed_private", False) if params_valid else True
        credentialed = bool(
            not params_valid
            or classified is True
            or str(classified or "").strip().casefold() in {"1", "true", "yes", "on"}
            or _url_has_credentials(str(row["url"] or ""))
        )
        if params_valid:
            params["credentialed_private"] = credentialed
            bind.execute(sa.text(
                "UPDATE source_configs SET ai_analysis_enabled = :enabled, "
                "params_json = :params WHERE source_id = :source_id"
            ), {
                "enabled": not credentialed,
                "params": json.dumps(params, ensure_ascii=False),
                "source_id": row["source_id"],
            })
        else:
            bind.execute(sa.text(
                "UPDATE source_configs SET ai_analysis_enabled = 0 WHERE source_id = :source_id"
            ), {"source_id": row["source_id"]})


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def _activate_explicit_v2_consumer_mode() -> None:
    bind = op.get_bind()
    if "app_settings" not in set(sa.inspect(bind).get_table_names()):
        return
    row = bind.execute(
        sa.text("SELECT value FROM app_settings WHERE key = :key"),
        {"key": _REMOTE_SYNC_SCHEDULE_KEY},
    ).mappings().first()
    if row is None:
        return
    try:
        schedule = json.loads(row["value"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(schedule, dict) or schedule.get("enabled") is not True:
        return
    source_ids = list(schedule.get("source_ids") or [])
    inferred_full_v2 = "protocol" not in schedule and not source_ids
    if schedule.get("protocol") != "v2" and not inferred_full_v2:
        return
    exists = bind.execute(
        sa.text("SELECT 1 FROM app_settings WHERE key = :key"),
        {"key": _V2_CONSUMER_MODE_KEY},
    ).first()
    if exists is None:
        bind.execute(sa.text(
            "INSERT INTO app_settings (key, value) VALUES (:key, :value)"
        ), {
            "key": _V2_CONSUMER_MODE_KEY,
            "value": json.dumps(
                {"active": True, "reason": "v2_schedule_migration"},
                separators=(",", ":"),
            ),
        })


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table_name, column_name in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
    ):
        present = bind.execute(sa.text(
            f"SELECT 1 FROM {table_name} WHERE {column_name} <> '' LIMIT 1"
        )).first()
        if present is not None:
            raise RuntimeError(
                "拒绝降级 Archive Sync v2：数据库仍含远端 authority。"
                "请先停止 worker，并恢复升级前备份。"
            )
    if bind.execute(sa.text("SELECT 1 FROM remote_candidate_evidence LIMIT 1")).first():
        raise RuntimeError(
            "拒绝降级 Archive Sync v2：数据库仍含远端 Candidate 证据。"
            "请先停止 worker，并恢复升级前备份。"
        )
    schedule_raw = bind.execute(
        sa.text("SELECT value FROM app_settings WHERE key = :key"),
        {"key": _REMOTE_SYNC_SCHEDULE_KEY},
    ).scalar_one_or_none()
    if schedule_raw:
        try:
            schedule = json.loads(schedule_raw)
        except (TypeError, json.JSONDecodeError):
            schedule = {}
        if isinstance(schedule, dict) and schedule.get("enabled") is True and schedule.get("protocol") == "v2":
            raise RuntimeError(
                "拒绝降级 Archive Sync v2：v2 定时同步仍启用。"
                "请先停止 worker，并恢复升级前备份。"
            )
    marker = bind.execute(
        sa.text("SELECT value FROM app_settings WHERE key = :key"),
        {"key": _V2_CONSUMER_MODE_KEY},
    ).scalar_one_or_none()
    if marker is not None:
        try:
            payload = json.loads(marker or "{}")
            active = bool(payload.get("active", True)) if isinstance(payload, dict) else True
        except (TypeError, json.JSONDecodeError):
            active = True
        if active:
            raise RuntimeError(
                "拒绝降级 Archive Sync v2：接收端 consumer 围栏仍生效。"
                "请先停止 worker，并恢复升级前备份。"
            )


def upgrade() -> None:
    for table_name, column in (
        ("articles", sa.Column("analysis_authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("article_analyses", sa.Column("authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("article_analyses", sa.Column("authority_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("source_configs", sa.Column("collection_authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("source_states", sa.Column("authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("source_states", sa.Column("authority_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("media_assets", sa.Column("sync_authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
        ("media_assets", sa.Column("sync_authority_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
    ):
        _add_column_if_missing(table_name, column)
    for table_name, index_name, columns in (
        ("articles", "ix_articles_analysis_authority_id", ["analysis_authority_id"]),
        ("article_analyses", "ix_article_analyses_authority_id", ["authority_id"]),
        ("source_configs", "ix_source_configs_collection_authority_id", ["collection_authority_id"]),
        ("source_states", "ix_source_states_authority_id", ["authority_id"]),
        ("media_assets", "ix_media_assets_sync_authority_id", ["sync_authority_id"]),
    ):
        _create_index_if_missing(table_name, index_name, columns)

    if "remote_candidate_evidence" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "remote_candidate_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
            sa.Column("authority_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("article_fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("source_provenance", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("label", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("normalized_label", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("proposed_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("sync_snapshot", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_remote_candidate_evidence_confidence"),
            sa.CheckConstraint("proposed_kind IN ('topic','industry','entity')", name="ck_remote_candidate_evidence_kind"),
            sa.ForeignKeyConstraint(["candidate_id"], ["cms_tag_candidates.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "authority_id", "article_fingerprint", "proposed_kind", "normalized_label",
                name="uq_remote_candidate_evidence_identity",
            ),
        )
    _add_column_if_missing(
        "remote_candidate_evidence",
        sa.Column("sync_snapshot", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
    )
    for index_name, columns in (
        ("ix_remote_candidate_evidence_sync_snapshot", ["sync_snapshot"]),
        ("ix_remote_candidate_evidence_authority_id", ["authority_id"]),
        ("ix_remote_candidate_evidence_candidate", ["candidate_id", "created_at"]),
    ):
        _create_index_if_missing("remote_candidate_evidence", index_name, columns)
    _backfill_custom_rss_analysis_policy()
    _activate_explicit_v2_consumer_mode()


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_safe()
    bind.execute(sa.text(
        "DELETE FROM app_settings WHERE key = :key"
    ), {"key": _V2_CONSUMER_MODE_KEY})
    bind.execute(sa.text(
        "UPDATE source_configs SET ai_analysis_enabled = 0 WHERE owner_username <> ''"
    ))
    op.drop_index("ix_remote_candidate_evidence_candidate", table_name="remote_candidate_evidence")
    op.drop_index("ix_remote_candidate_evidence_authority_id", table_name="remote_candidate_evidence")
    op.drop_index("ix_remote_candidate_evidence_sync_snapshot", table_name="remote_candidate_evidence")
    op.drop_table("remote_candidate_evidence")
    op.drop_index("ix_source_configs_collection_authority_id", table_name="source_configs")
    op.drop_column("source_configs", "collection_authority_id")
    op.drop_index("ix_source_states_authority_id", table_name="source_states")
    op.drop_column("source_states", "authority_revision")
    op.drop_column("source_states", "authority_id")
    op.drop_index("ix_media_assets_sync_authority_id", table_name="media_assets")
    op.drop_column("media_assets", "sync_authority_revision")
    op.drop_column("media_assets", "sync_authority_id")
    op.drop_index("ix_article_analyses_authority_id", table_name="article_analyses")
    op.drop_column("article_analyses", "authority_revision")
    op.drop_column("article_analyses", "authority_id")
    op.drop_index("ix_articles_analysis_authority_id", table_name="articles")
    op.drop_column("articles", "analysis_authority_id")
