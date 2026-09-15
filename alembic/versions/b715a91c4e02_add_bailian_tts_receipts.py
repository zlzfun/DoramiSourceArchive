"""Add local-only Bailian TTS paid-call receipts.

Revision ID: b715a91c4e02
Revises: df8f95fe0439
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "b715a91c4e02"
down_revision = "df8f95fe0439"
branch_labels = None
depends_on = None


def upgrade():
    _upgrade_usage_minimum()
    # create_all() bootstrap adoption replays the migration chain.
    if "bailian_tts_calls" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "bailian_tts_calls",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("account_scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("budget_period", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reserved_characters", sa.Integer(), nullable=False),
        sa.Column("actual_characters", sa.Integer(), nullable=False),
        sa.Column("cost_minor", sa.Integer(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("price_units", sa.Integer(), nullable=False),
        sa.Column(
            "pricing_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("request_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("audio_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved','authorized','generated','succeeded','rejected')",
            name="ck_bailian_tts_status",
        ),
        sa.CheckConstraint(
            "reserved_characters > 0 AND actual_characters >= 0 AND cost_minor >= 0",
            name="ck_bailian_tts_usage",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bailian_tts_calls_episode_id", "bailian_tts_calls", ["episode_id"]
    )
    op.create_index(
        "ix_bailian_tts_scope_period",
        "bailian_tts_calls",
        ["account_scope", "budget_period"],
    )


def downgrade():
    # Like the parent revision, reject before any DDL above the one-way boundary.
    raise RuntimeError(
        "Bailian receipts sit above a one-way boundary; restore the pre-upgrade database to downgrade"
    )


def _upgrade_usage_minimum():
    """Preserve the old floor by default; permit explicitly planned ASR billing."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_budget_reservations IN ACCESS EXCLUSIVE MODE"
        )
    else:
        bind.exec_driver_sql(
            "UPDATE podcast_processings SET updated_at=updated_at WHERE 0"
        )
    triggers = (
        ("podcast_stage_attempt_identity_immutable", "podcast_stage_attempts"),
        ("podcast_cost_ledger_binding_insert", "podcast_cost_ledger"),
        ("podcast_cost_ledger_immutable_update", "podcast_cost_ledger"),
        ("podcast_cost_ledger_immutable_delete", "podcast_cost_ledger"),
        ("podcast_budget_reservation_transition", "podcast_budget_reservations"),
        ("podcast_budget_reservation_immutable_delete", "podcast_budget_reservations"),
    )
    for name, table in triggers:
        suffix = f" ON {table}" if bind.dialect.name == "postgresql" else ""
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}{suffix}")
    # SQLite validates all triggers during the batch table rename, including
    # triggers on audio tables which refer to this reservation table.
    dependent_triggers = []
    if bind.dialect.name == "sqlite":
        dependent_triggers = bind.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND lower(sql) LIKE '%podcast_budget_reservations%'"
        ).all()
        for name, _ in dependent_triggers:
            bind.exec_driver_sql('DROP TRIGGER "' + name.replace('"', '""') + '"')
    columns = {
        c["name"] for c in sa.inspect(bind).get_columns("podcast_budget_reservations")
    }
    if "minimum_usage_units" not in columns:
        with op.batch_alter_table("podcast_budget_reservations") as batch:
            batch.add_column(
                sa.Column("minimum_usage_units", sa.Integer(), nullable=True)
            )
            batch.drop_constraint(
                "ck_podcast_budget_reservations_provider_settlement_truth",
                type_="check",
            )
            batch.create_check_constraint(
                "ck_podcast_budget_reservations_provider_settlement_truth",
                "status <> 'settled' OR provider_quota_scope IS NULL OR "
                "(actual_usage_units >= COALESCE(minimum_usage_units, reserved_usage_units) AND "
                "provider_quota_breached = (actual_usage_units > reserved_usage_units))",
            )
            batch.create_check_constraint(
                "ck_podcast_budget_reservations_usage_minimum",
                "minimum_usage_units IS NULL OR (provider_quota_unit IS NOT NULL AND "
                "provider_quota_unit = 'audio_seconds' AND reserved_usage_units IS NOT NULL AND "
                "minimum_usage_units >= 0 AND minimum_usage_units <= reserved_usage_units AND "
                "minimum_usage_units = CAST(minimum_usage_units AS INTEGER))",
            )
    for _, statement in dependent_triggers:
        bind.exec_driver_sql(statement)
    from models.db import (
        _podcast_processing_audit_trigger_sql,
        _podcast_processing_postgresql_audit_sql,
    )

    builder = (
        _podcast_processing_postgresql_audit_sql
        if bind.dialect.name == "postgresql"
        else _podcast_processing_audit_trigger_sql
    )
    for statement in builder(
        include_execution_kind=True,
        include_poll_state=True,
        include_output_binding=True,
        include_provider_usage=True,
        include_output_authority=True,
        include_usage_settlement_mode=True,
        include_usage_minimum=True,
    ):
        bind.exec_driver_sql(statement)
