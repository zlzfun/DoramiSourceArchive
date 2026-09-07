"""add Podcast source admission and rights governance

Revision ID: 9f6c4b2e1a07
Revises: f7a4c2d91e30
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "9f6c4b2e1a07"
down_revision: Union[str, Sequence[str], None] = "f7a4c2d91e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_fail_closed_baselines() -> None:
    now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    op.execute(sa.text(
        "INSERT OR IGNORE INTO podcast_source_profiles "
        "(source_id, canonical_feed_url, podcast_guid, admission_status, admission_policy, "
        "source_scope_class, ai_episode_ratio, in_scope_episode_ratio, scope_confidence, "
        "sample_size, hard_reject_reasons_json, current_review_id, reviewed_at, next_review_at, "
        "drift_score, manual_override_by, manual_override_reason, manual_override_expires_at, "
        "row_version, created_at, updated_at) "
        "SELECT source_id, url, '', 'pending', 'manual', 'unknown', 0, 0, 0, 0, '[]', NULL, "
        f"NULL, NULL, 0, '', '', NULL, 0, {now_sql}, {now_sql} "
        "FROM source_configs WHERE source_type = 'podcast'"
    ))
    op.execute(sa.text(
        "INSERT OR IGNORE INTO podcast_rights "
        "(id, source_id, episode_id, policy, transcript_allowed, derivative_text_allowed, "
        "derivative_audio_allowed, public_distribution_allowed, license_name, license_url, "
        "evidence_url, note, reviewed_by, reviewed_at, expires_at, policy_version, "
        "row_version, created_at) "
        "SELECT 'migration-source:' || source_id, source_id, NULL, 'link_only', 0, 0, 0, 0, "
        "'', '', '', 'Fail-closed migration baseline', 'migration', "
        f"{now_sql}, NULL, 'podcast-rights-v1', 0, {now_sql} "
        "FROM source_configs WHERE source_type = 'podcast'"
    ))


def upgrade() -> None:
    # Legacy databases are bootstrapped with current metadata.create_all() and
    # then stamped at the baseline before Alembic catches up. In that path all
    # three tables and indexes already exist; only compatibility backfill is due.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    governance_tables = {
        "podcast_source_reviews",
        "podcast_source_profiles",
        "podcast_rights",
    }
    if governance_tables.issubset(existing):
        _backfill_fail_closed_baselines()
        return

    op.create_table(
        "podcast_source_reviews",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("trigger", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sampled_episode_guids_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sampled_inputs_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("classifications_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ai_episode_ratio", sa.Float(), nullable=False),
        sa.Column("in_scope_episode_ratio", sa.Float(), nullable=False),
        sa.Column("scope_confidence", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("proposed_decision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("final_decision", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("provider_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("policy_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reviewer", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("rationale", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("completed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.CheckConstraint(
            "ai_episode_ratio >= 0 AND ai_episode_ratio <= 1 AND "
            "in_scope_episode_ratio >= 0 AND in_scope_episode_ratio <= 1 AND "
            "scope_confidence >= 0 AND scope_confidence <= 1",
            name="ck_podcast_source_reviews_ratios",
        ),
        sa.CheckConstraint("sample_size >= 0", name="ck_podcast_source_reviews_sample_size"),
        sa.CheckConstraint(
            "final_decision IS NULL OR final_decision IN "
            "('pending','sampling','review_required','approved','rejected_scope','blocked','failed')",
            name="ck_podcast_source_reviews_final_decision",
        ),
        sa.CheckConstraint(
            "proposed_decision IN ('pending','sampling','review_required','approved',"
            "'rejected_scope','blocked','failed')",
            name="ck_podcast_source_reviews_proposed_decision",
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','cancelled')",
            name="ck_podcast_source_reviews_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('initial','scheduled','drift','manual','migration')",
            name="ck_podcast_source_reviews_trigger",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.source_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "sampled_inputs_hash",
            "model_name",
            "prompt_version",
            name="uq_podcast_source_reviews_reusable_input",
        ),
    )
    op.create_index("ix_podcast_source_reviews_source_id", "podcast_source_reviews", ["source_id"])
    op.create_index(
        "ix_podcast_source_reviews_source_created",
        "podcast_source_reviews",
        ["source_id", "created_at"],
    )
    op.create_index("ix_podcast_source_reviews_status", "podcast_source_reviews", ["status"])
    op.create_index(
        "ix_podcast_source_reviews_status_created",
        "podcast_source_reviews",
        ["status", "created_at"],
    )

    op.create_table(
        "podcast_source_profiles",
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("canonical_feed_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("podcast_guid", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("admission_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("admission_policy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_scope_class", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ai_episode_ratio", sa.Float(), nullable=False),
        sa.Column("in_scope_episode_ratio", sa.Float(), nullable=False),
        sa.Column("scope_confidence", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("hard_reject_reasons_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("current_review_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reviewed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("next_review_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("drift_score", sa.Float(), nullable=False),
        sa.Column("manual_override_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("manual_override_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("manual_override_expires_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "admission_policy IN ('ai_core','tech_core','mixed','manual')",
            name="ck_podcast_source_profiles_policy",
        ),
        sa.CheckConstraint(
            "admission_status IN ('pending','sampling','review_required','approved',"
            "'rejected_scope','blocked','failed')",
            name="ck_podcast_source_profiles_status",
        ),
        sa.CheckConstraint(
            "ai_episode_ratio >= 0 AND ai_episode_ratio <= 1 AND "
            "in_scope_episode_ratio >= 0 AND in_scope_episode_ratio <= 1 AND "
            "scope_confidence >= 0 AND scope_confidence <= 1 AND "
            "drift_score >= 0 AND drift_score <= 1",
            name="ck_podcast_source_profiles_ratios",
        ),
        sa.CheckConstraint("row_version >= 0", name="ck_podcast_source_profiles_row_version"),
        sa.CheckConstraint("sample_size >= 0", name="ck_podcast_source_profiles_sample_size"),
        sa.CheckConstraint(
            "source_scope_class IN ('ai_core','tech_adjacent','mixed','off_topic','unknown')",
            name="ck_podcast_source_profiles_scope",
        ),
        sa.ForeignKeyConstraint(["current_review_id"], ["podcast_source_reviews.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.source_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index(
        "ix_podcast_source_profiles_admission_status",
        "podcast_source_profiles",
        ["admission_status"],
    )
    op.create_index(
        "ix_podcast_source_profiles_next_review_at",
        "podcast_source_profiles",
        ["next_review_at"],
    )

    op.create_table(
        "podcast_rights",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("policy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("transcript_allowed", sa.Boolean(), nullable=False),
        sa.Column("derivative_text_allowed", sa.Boolean(), nullable=False),
        sa.Column("derivative_audio_allowed", sa.Boolean(), nullable=False),
        sa.Column("public_distribution_allowed", sa.Boolean(), nullable=False),
        sa.Column("license_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("license_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("evidence_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reviewed_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reviewed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("policy_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "policy IN ('link_only','transcribe_private','derivative_text',"
            "'derivative_audio','blocked','review_required')",
            name="ck_podcast_rights_policy",
        ),
        sa.CheckConstraint("row_version >= 0", name="ck_podcast_rights_row_version"),
        sa.ForeignKeyConstraint(["episode_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.source_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_podcast_rights_episode_id", "podcast_rights", ["episode_id"])
    op.create_index(
        "ix_podcast_rights_episode_created", "podcast_rights", ["episode_id", "created_at"]
    )
    op.create_index("ix_podcast_rights_expires_at", "podcast_rights", ["expires_at"])
    op.create_index("ix_podcast_rights_policy", "podcast_rights", ["policy"])
    op.create_index("ix_podcast_rights_source_id", "podcast_rights", ["source_id"])
    op.create_index(
        "ix_podcast_rights_source_created", "podcast_rights", ["source_id", "created_at"]
    )
    op.create_index(
        "uq_podcast_rights_source_version",
        "podcast_rights",
        ["source_id", "row_version"],
        unique=True,
        sqlite_where=sa.text("episode_id IS NULL"),
    )
    op.create_index(
        "uq_podcast_rights_episode_version",
        "podcast_rights",
        ["episode_id", "row_version"],
        unique=True,
        sqlite_where=sa.text("episode_id IS NOT NULL"),
    )

    # Compatibility is deliberately fail-closed: an installed/active catalog
    # source is not proof of topical review or derivative/publication rights.
    _backfill_fail_closed_baselines()


def _assert_archive_sync_downgrade_safe() -> None:
    """Refuse before dropping this revision when the parent would refuse too."""
    bind = op.get_bind()
    for table, column in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
        ("podcast_artifacts", "authority_id"),
    ):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' LIMIT 1"
        )).first() is not None:
            raise RuntimeError(
                "拒绝降级 Archive Sync：数据库仍含远端 authority。"
                "请先停止 worker，并恢复升级前备份。"
            )
    if bind.execute(sa.text("SELECT 1 FROM remote_candidate_evidence LIMIT 1")).first() is not None:
        raise RuntimeError(
            "拒绝降级 Archive Sync：数据库仍含远端 Candidate 证据。"
            "请先停止 worker，并恢复升级前备份。"
        )
    if bind.execute(sa.text(
        "SELECT 1 FROM personal_digest_editions WHERE desired_generation_reason IS NOT NULL "
        "OR desired_requested_at IS NOT NULL OR desired_first_open_at IS NOT NULL "
        "OR sync_stale = 1 OR analysis_incomplete = 1 LIMIT 1"
    )).first() is not None:
        raise RuntimeError(
            "拒绝降级 Archive Sync：数据库仍含新版个人早报状态。"
            "请先停止 worker，并恢复升级前备份。"
        )
    marker = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:v2_consumer_mode'"
    )).scalar_one_or_none()
    schedule = bind.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = 'remote_sync:schedule'"
    )).scalar_one_or_none()
    compact_schedule = str(schedule or "").replace(" ", "")
    if marker is not None or (
        '"enabled":true' in compact_schedule and '"protocol":"v2"' in compact_schedule
    ):
        raise RuntimeError(
            "拒绝降级 Archive Sync：consumer 围栏仍生效。"
            "请先停止 worker，并恢复升级前备份。"
        )


def downgrade() -> None:
    _assert_archive_sync_downgrade_safe()
    op.drop_index("uq_podcast_rights_episode_version", table_name="podcast_rights")
    op.drop_index("uq_podcast_rights_source_version", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_source_created", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_source_id", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_policy", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_expires_at", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_episode_created", table_name="podcast_rights")
    op.drop_index("ix_podcast_rights_episode_id", table_name="podcast_rights")
    op.drop_table("podcast_rights")
    op.drop_index("ix_podcast_source_profiles_next_review_at", table_name="podcast_source_profiles")
    op.drop_index("ix_podcast_source_profiles_admission_status", table_name="podcast_source_profiles")
    op.drop_table("podcast_source_profiles")
    op.drop_index("ix_podcast_source_reviews_status_created", table_name="podcast_source_reviews")
    op.drop_index("ix_podcast_source_reviews_status", table_name="podcast_source_reviews")
    op.drop_index("ix_podcast_source_reviews_source_created", table_name="podcast_source_reviews")
    op.drop_index("ix_podcast_source_reviews_source_id", table_name="podcast_source_reviews")
    op.drop_table("podcast_source_reviews")
