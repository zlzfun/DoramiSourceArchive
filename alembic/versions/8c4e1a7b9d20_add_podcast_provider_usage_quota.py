"""add durable Podcast provider usage quota accounting

Revision ID: 8c4e1a7b9d20
Revises: 6b8d2f4a9c70
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "8c4e1a7b9d20"
down_revision: Union[str, Sequence[str], None] = "6b8d2f4a9c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RESERVATION_COLUMNS = (
    "provider_quota_scope",
    "provider_quota_period",
    "provider_quota_unit",
    "provider_quota_window_start_at",
    "provider_quota_window_end_at",
    "provider_quota_limit_units",
    "reserved_usage_units",
    "actual_usage_units",
    "unit_price_cny_minor",
    "price_unit_count",
    "pricing_revision",
    "provider_quota_breached",
)
_LEDGER_COLUMNS = (
    "provider_quota_scope",
    "provider_quota_period",
    "provider_quota_unit",
    "actual_usage_units",
    "provider_quota_breached",
)


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast provider quota migration requires an online database "
            "connection for writer fencing and trigger restoration"
        )


def _lock_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_processings, podcast_stage_attempts, "
            "podcast_budget_reservations, podcast_cost_ledger "
            "IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "UPDATE podcast_processings SET updated_at = updated_at WHERE 0"
        )


def _drop_audit_triggers(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        for name in (
            "podcast_stage_attempt_identity_immutable",
            "podcast_cost_ledger_binding_insert",
            "podcast_cost_ledger_immutable_update",
            "podcast_cost_ledger_immutable_delete",
            "podcast_budget_reservation_transition",
            "podcast_budget_reservation_immutable_delete",
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    elif bind.dialect.name == "postgresql":
        for name, table in (
            ("podcast_stage_attempt_identity_immutable", "podcast_stage_attempts"),
            ("podcast_cost_ledger_binding_insert", "podcast_cost_ledger"),
            ("podcast_cost_ledger_immutable_update", "podcast_cost_ledger"),
            ("podcast_cost_ledger_immutable_delete", "podcast_cost_ledger"),
            ("podcast_budget_reservation_transition", "podcast_budget_reservations"),
            ("podcast_budget_reservation_immutable_delete", "podcast_budget_reservations"),
        ):
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name} ON {table}")


def _install_audit_triggers(bind: sa.Connection, *, current: bool) -> None:
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_audit_trigger_sql

        statements = _podcast_processing_audit_trigger_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=current,
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=current,
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _assert_downgrade_safe(bind: sa.Connection) -> None:
    used = bind.execute(sa.text(
        "SELECT 1 FROM podcast_budget_reservations WHERE "
        "provider_quota_scope IS NOT NULL OR actual_usage_units <> 0 OR "
        "provider_quota_breached IS TRUE UNION ALL "
        "SELECT 1 FROM podcast_cost_ledger WHERE provider_quota_scope IS NOT NULL "
        "OR actual_usage_units <> 0 OR provider_quota_breached IS TRUE LIMIT 1"
    )).first()
    if used is not None:
        raise RuntimeError(
            "refusing to discard durable Podcast provider usage accounting; "
            "stop workers and restore the pre-upgrade backup"
        )


def _assert_upgrade_safe(bind: sa.Connection) -> None:
    active_provider_hold = bind.execute(sa.text(
        "SELECT 1 FROM podcast_budget_reservations r "
        "JOIN podcast_stage_attempts a ON a.id = r.attempt_id "
        "AND a.processing_id = r.processing_id "
        "WHERE r.status = 'reserved' AND a.execution_kind = 'provider' LIMIT 1"
    )).first()
    if active_provider_hold is not None:
        raise RuntimeError(
            "refusing to upgrade active legacy provider budget reservations "
            "without a durable usage quota; drain or reconcile provider work first"
        )


def _add_columns(bind: sa.Connection) -> None:
    string = sqlmodel.sql.sqltypes.AutoString
    existing_reservation_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("podcast_budget_reservations")
    }
    for name in _RESERVATION_COLUMNS:
        if name in existing_reservation_columns:
            continue
        if name in {"provider_quota_limit_units", "reserved_usage_units", "unit_price_cny_minor", "price_unit_count"}:
            column = sa.Column(name, sa.Integer(), nullable=True)
        elif name == "actual_usage_units":
            column = sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        elif name == "provider_quota_breached":
            column = sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        else:
            column = sa.Column(name, string(), nullable=True)
        op.add_column("podcast_budget_reservations", column)
    existing_ledger_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("podcast_cost_ledger")
    }
    for name in _LEDGER_COLUMNS:
        if name in existing_ledger_columns:
            continue
        if name == "actual_usage_units":
            column = sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        elif name == "provider_quota_breached":
            column = sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        else:
            column = sa.Column(name, string(), nullable=True)
        op.add_column("podcast_cost_ledger", column)


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    reservation_columns = {
        item["name"]
        for item in sa.inspect(bind).get_columns("podcast_budget_reservations")
    }
    ledger_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("podcast_cost_ledger")
    }
    present_reservation = set(_RESERVATION_COLUMNS) & reservation_columns
    present_ledger = set(_LEDGER_COLUMNS) & ledger_columns
    schema_is_current = (
        present_reservation == set(_RESERVATION_COLUMNS)
        and present_ledger == set(_LEDGER_COLUMNS)
    )
    schema_is_legacy = not present_reservation and not present_ledger
    if not schema_is_current and not schema_is_legacy:
        raise RuntimeError(
            "partial Podcast provider usage quota schema exists; restore a "
            "consistent backup before retrying migration"
        )
    if schema_is_legacy:
        _assert_upgrade_safe(bind)
    _drop_audit_triggers(bind)
    _add_columns(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    reservation_checks = {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints("podcast_budget_reservations")
    }
    reservation_indexes = {
        item["name"]
        for item in sa.inspect(bind).get_indexes("podcast_budget_reservations")
    }
    reservation_changes = not {
        "ck_podcast_budget_reservations_provider_binding",
        "ck_podcast_budget_reservations_provider_integer",
        "ck_podcast_budget_reservations_provider_settlement",
        "ck_podcast_budget_reservations_provider_settlement_truth",
    }.issubset(reservation_checks) or (
        "ix_podcast_budget_reservations_provider_capacity" not in reservation_indexes
    )
    if reservation_changes:
        with op.batch_alter_table(
            "podcast_budget_reservations", recreate=recreate
        ) as batch:
            if (
                "ck_podcast_budget_reservations_provider_binding"
                not in reservation_checks
            ):
                batch.create_check_constraint(
                    "ck_podcast_budget_reservations_provider_binding",
                    "((provider_quota_scope IS NULL AND provider_quota_period IS NULL AND "
                    "provider_quota_unit IS NULL AND provider_quota_window_start_at IS NULL AND "
                    "provider_quota_window_end_at IS NULL AND provider_quota_limit_units IS NULL AND "
                    "reserved_usage_units IS NULL AND unit_price_cny_minor IS NULL AND "
                    "price_unit_count IS NULL AND pricing_revision IS NULL AND "
                    "actual_usage_units = 0 AND provider_quota_breached IS FALSE) OR "
                    "(provider_quota_scope IS NOT NULL AND length(trim(provider_quota_scope)) > 0 AND "
                    "provider_quota_period IS NOT NULL AND length(trim(provider_quota_period)) > 0 AND "
                    "provider_quota_unit IN ('audio_seconds','tts_characters') AND "
                    "provider_quota_window_start_at IS NOT NULL AND provider_quota_window_end_at IS NOT NULL AND "
                    "provider_quota_limit_units > 0 AND reserved_usage_units > 0 AND "
                    "reserved_usage_units <= provider_quota_limit_units AND unit_price_cny_minor >= 0 AND "
                    "price_unit_count > 0 AND pricing_revision IS NOT NULL AND length(trim(pricing_revision)) > 0))",
                )
            if (
                "ck_podcast_budget_reservations_provider_integer"
                not in reservation_checks
            ):
                batch.create_check_constraint(
                    "ck_podcast_budget_reservations_provider_integer",
                    "actual_usage_units >= 0 AND actual_usage_units = CAST(actual_usage_units AS INTEGER) AND "
                    "(reserved_usage_units IS NULL OR reserved_usage_units = CAST(reserved_usage_units AS INTEGER)) AND "
                    "(provider_quota_limit_units IS NULL OR provider_quota_limit_units = CAST(provider_quota_limit_units AS INTEGER)) AND "
                    "(unit_price_cny_minor IS NULL OR unit_price_cny_minor = CAST(unit_price_cny_minor AS INTEGER)) AND "
                    "(price_unit_count IS NULL OR price_unit_count = CAST(price_unit_count AS INTEGER))",
                )
            if (
                "ck_podcast_budget_reservations_provider_settlement"
                not in reservation_checks
            ):
                batch.create_check_constraint(
                    "ck_podcast_budget_reservations_provider_settlement",
                    "status = 'settled' OR actual_usage_units = 0",
                )
            if (
                "ck_podcast_budget_reservations_provider_settlement_truth"
                not in reservation_checks
            ):
                batch.create_check_constraint(
                    "ck_podcast_budget_reservations_provider_settlement_truth",
                    "status <> 'settled' OR provider_quota_scope IS NULL OR "
                    "(actual_usage_units >= reserved_usage_units AND "
                    "provider_quota_breached = "
                    "(actual_usage_units > reserved_usage_units))",
                )
            if (
                "ix_podcast_budget_reservations_provider_capacity"
                not in reservation_indexes
            ):
                batch.create_index(
                    "ix_podcast_budget_reservations_provider_capacity",
                    [
                        "provider_quota_scope",
                        "provider_quota_period",
                        "provider_quota_unit",
                        "status",
                    ],
                    unique=False,
                )
            batch.alter_column(
                "actual_usage_units",
                existing_type=sa.Integer(),
                nullable=False,
                server_default=None,
            )
            batch.alter_column(
                "provider_quota_breached",
                existing_type=sa.Boolean(),
                nullable=False,
                server_default=None,
            )
    ledger_checks = {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints("podcast_cost_ledger")
    }
    ledger_indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("podcast_cost_ledger")
    }
    ledger_changes = (
        not {
            "ck_podcast_cost_ledger_provider_binding",
            "ck_podcast_cost_ledger_provider_integer",
        }.issubset(ledger_checks)
        or "ix_podcast_cost_ledger_provider_usage" not in ledger_indexes
    )
    if ledger_changes:
        with op.batch_alter_table("podcast_cost_ledger", recreate=recreate) as batch:
            if "ck_podcast_cost_ledger_provider_binding" not in ledger_checks:
                batch.create_check_constraint(
                    "ck_podcast_cost_ledger_provider_binding",
                    "((provider_quota_scope IS NULL AND provider_quota_period IS NULL AND "
                    "provider_quota_unit IS NULL AND actual_usage_units = 0 AND provider_quota_breached IS FALSE) OR "
                    "(provider_quota_scope IS NOT NULL AND length(trim(provider_quota_scope)) > 0 AND "
                    "provider_quota_period IS NOT NULL AND length(trim(provider_quota_period)) > 0 AND "
                    "provider_quota_unit IN ('audio_seconds','tts_characters') AND actual_usage_units >= 0))",
                )
            if "ck_podcast_cost_ledger_provider_integer" not in ledger_checks:
                batch.create_check_constraint(
                    "ck_podcast_cost_ledger_provider_integer",
                    "actual_usage_units = CAST(actual_usage_units AS INTEGER)",
                )
            if "ix_podcast_cost_ledger_provider_usage" not in ledger_indexes:
                batch.create_index(
                    "ix_podcast_cost_ledger_provider_usage",
                    [
                        "provider_quota_scope",
                        "provider_quota_period",
                        "provider_quota_unit",
                    ],
                    unique=False,
                )
            batch.alter_column(
                "actual_usage_units",
                existing_type=sa.Integer(),
                nullable=False,
                server_default=None,
            )
            batch.alter_column(
                "provider_quota_breached",
                existing_type=sa.Boolean(),
                nullable=False,
                server_default=None,
            )
    _install_audit_triggers(bind, current=True)


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    _assert_downgrade_safe(bind)
    _drop_audit_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_cost_ledger", recreate=recreate) as batch:
        batch.drop_index("ix_podcast_cost_ledger_provider_usage")
        batch.drop_constraint("ck_podcast_cost_ledger_provider_integer", type_="check")
        batch.drop_constraint("ck_podcast_cost_ledger_provider_binding", type_="check")
        for name in reversed(_LEDGER_COLUMNS):
            batch.drop_column(name)
    with op.batch_alter_table("podcast_budget_reservations", recreate=recreate) as batch:
        batch.drop_index("ix_podcast_budget_reservations_provider_capacity")
        batch.drop_constraint(
            "ck_podcast_budget_reservations_provider_settlement_truth",
            type_="check",
        )
        batch.drop_constraint("ck_podcast_budget_reservations_provider_settlement", type_="check")
        batch.drop_constraint("ck_podcast_budget_reservations_provider_integer", type_="check")
        batch.drop_constraint("ck_podcast_budget_reservations_provider_binding", type_="check")
        for name in reversed(_RESERVATION_COLUMNS):
            batch.drop_column(name)
    _install_audit_triggers(bind, current=False)
