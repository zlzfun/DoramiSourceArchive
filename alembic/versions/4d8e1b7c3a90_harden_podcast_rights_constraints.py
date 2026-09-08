"""harden Podcast rights constraints

Revision ID: 4d8e1b7c3a90
Revises: 9f6c4b2e1a07
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d8e1b7c3a90"
down_revision: Union[str, Sequence[str], None] = "9f6c4b2e1a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("podcast_rights", recreate="always") as batch_op:
        batch_op.create_check_constraint(
            "ck_podcast_rights_audio_implies_text",
            "derivative_audio_allowed = 0 OR derivative_text_allowed = 1",
        )
        batch_op.create_check_constraint(
            "ck_podcast_rights_text_implies_transcript",
            "derivative_text_allowed = 0 OR transcript_allowed = 1",
        )
        batch_op.create_check_constraint(
            "ck_podcast_rights_deny_policies",
            "policy NOT IN ('link_only','blocked','review_required') OR "
            "(transcript_allowed = 0 AND derivative_text_allowed = 0 AND "
            "derivative_audio_allowed = 0 AND public_distribution_allowed = 0)",
        )


def downgrade() -> None:
    _assert_archive_sync_downgrade_safe()
    with op.batch_alter_table("podcast_rights", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_podcast_rights_deny_policies", type_="check")
        batch_op.drop_constraint("ck_podcast_rights_text_implies_transcript", type_="check")
        batch_op.drop_constraint("ck_podcast_rights_audio_implies_text", type_="check")


def _assert_archive_sync_downgrade_safe() -> None:
    """Mirror the parent fence so refused downgrades leave this head intact."""
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
