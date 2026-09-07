"""cascade Podcast artifacts when their episode is deleted

Revision ID: f7a4c2d91e30
Revises: c2d7e4f8a901
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a4c2d91e30"
down_revision: Union[str, Sequence[str], None] = "c2d7e4f8a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_FK_NAME = "fk_podcast_artifacts_episode_id_articles"


def _replace_foreign_key(*, ondelete: str | None) -> None:
    # SQLite cannot alter FK clauses in place. Batch mode recreates the table
    # while preserving data, indexes and check constraints.
    # A database first opened by a newer application can already have trigger
    # bodies from the current model even while Alembic still reports this old
    # revision. SQLite reparses every trigger while a batch table is renamed,
    # so even a trigger on another table can block this rebuild when it refers
    # to columns introduced by a later migration. Drop every forward processing
    # trigger here; their owning migrations reinstall the matching versions.
    if op.get_bind().dialect.name == "sqlite":
        for name in (
            "podcast_stage_attempt_identity_immutable",
            "podcast_cost_ledger_binding_insert",
            "podcast_cost_ledger_immutable_update",
            "podcast_cost_ledger_immutable_delete",
            "podcast_budget_reservation_transition",
            "podcast_budget_reservation_immutable_delete",
            "podcast_processing_input_immutable",
            "podcast_processing_command_immutable_update",
            "podcast_processing_command_immutable_delete",
            "podcast_audio_dependency_insert",
            "podcast_audio_dependency_update",
            "podcast_audio_processing_insert",
            "podcast_audio_processing_update",
            "podcast_audio_attempt_insert",
            "podcast_audio_attempt_update",
            "podcast_audio_binding_immutable",
            "podcast_script_audio_invalidate_update",
            "podcast_script_audio_invalidate_delete",
        ):
            op.get_bind().exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    with op.batch_alter_table(
        "podcast_artifacts", recreate="always", naming_convention=_NAMING
    ) as batch_op:
        batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
        batch_op.create_foreign_key(
            _FK_NAME,
            "articles",
            ["episode_id"],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_foreign_key(ondelete="CASCADE")


def _assert_archive_sync_downgrade_safe() -> None:
    """Mirror the parent fence so a refused downgrade leaves this head intact."""
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
    if bind.execute(sa.text(
        "SELECT 1 FROM remote_candidate_evidence LIMIT 1"
    )).first() is not None:
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
    # Keep the downstream Archive Sync downgrade fence atomic: if the parent
    # would refuse, this revision must not advance its version marker first.
    _assert_archive_sync_downgrade_safe()
    _replace_foreign_key(ondelete=None)
