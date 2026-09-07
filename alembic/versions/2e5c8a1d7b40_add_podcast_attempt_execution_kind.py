"""classify Podcast stage attempts as provider or local execution

Revision ID: 2e5c8a1d7b40
Revises: 7c2e1a9b4d60
Create Date: 2026-09-06
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "2e5c8a1d7b40"
down_revision: Union[str, Sequence[str], None] = "7c2e1a9b4d60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IDENTITY_TRIGGER = "podcast_stage_attempt_identity_immutable"


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast attempt execution-kind migration requires an online "
            "database connection for writer fencing and data backfill"
        )


def _lock_attempt_writers(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_stage_attempts, podcast_budget_reservations "
            "IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "UPDATE podcast_stage_attempts SET updated_at = updated_at WHERE 0"
        )


def _drop_identity_trigger(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{_IDENTITY_TRIGGER}"')
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            f'DROP TRIGGER IF EXISTS "{_IDENTITY_TRIGGER}" '
            "ON podcast_stage_attempts"
        )


def _install_audit(*, include_execution_kind: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_processing_audit_trigger_sql

        statements = _podcast_processing_audit_trigger_sql(
            include_execution_kind=include_execution_kind
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql(
            include_execution_kind=include_execution_kind
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _assert_parent_downgrade_safe(bind: sa.Connection) -> None:
    """Keep SQLite at this head if a deeper chained downgrade is unsafe."""

    parent_path = Path(__file__).with_name(
        "7c2e1a9b4d60_add_source_audio_cache_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_dorami_podcast_source_audio_parent_revision", parent_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Podcast execution-kind parent revision")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._assert_parent_downgrade_safe()
    source_row = bind.execute(sa.text(
        "SELECT 1 FROM podcast_artifacts WHERE kind = 'source_audio' LIMIT 1"
    )).first()
    if source_row is not None:
        raise RuntimeError(
            "拒绝降级 Podcast 执行类型：后续迁移仍有 source_audio 记录，"
            "链式降级会恢复永久镜像语义。请先停止 worker、清理或导出记录，"
            "然后恢复升级前备份。"
        )


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_attempt_writers(bind)
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("podcast_stage_attempts")
    }
    if "execution_kind" in columns:
        # An adopted create_all database already has the current schema.
        _drop_identity_trigger(bind)
        _install_audit(include_execution_kind=True)
        return

    unsafe_local = bind.execute(sa.text(
        "SELECT 1 FROM podcast_stage_attempts "
        "WHERE stage IN ('audio_qa','local_publish') AND "
        "(actual_cost_minor <> 0 OR request_unknown IS TRUE OR "
        "submission_state IN ('submitted','request_unknown','reconciling') OR "
        "length(provider_task_id) > 0) LIMIT 1"
    )).first()
    if unsafe_local is not None:
        raise RuntimeError(
            "Podcast local-stage history contains provider state or billed cost; "
            "reconcile the affected attempts before execution-kind migration"
        )

    _drop_identity_trigger(bind)
    op.add_column(
        "podcast_stage_attempts",
        sa.Column(
            "execution_kind",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    bind.execute(sa.text(
        "UPDATE podcast_stage_attempts SET "
        "execution_kind = CASE WHEN stage IN ('audio_qa','local_publish') "
        "THEN 'local' ELSE 'provider' END, "
        "estimated_cost_minor = CASE WHEN stage IN ('audio_qa','local_publish') "
        "THEN 0 ELSE estimated_cost_minor END"
    ))
    # Older code could persist a confirmed provider submission without the
    # task identity needed for polling. Preserve it as ambiguous rather than
    # pretending that it is safely submitted.
    bind.execute(sa.text(
        "UPDATE podcast_stage_attempts SET submission_state = 'request_unknown', "
        "request_unknown = TRUE, retry_state = 'reconcile_required' "
        "WHERE execution_kind = 'provider' AND submission_state = 'submitted' "
        "AND length(trim(provider_task_id)) = 0"
    ))

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.alter_column(
            "execution_kind",
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_execution_kind",
            "execution_kind IN ('provider','local')",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_local_zero_estimate",
            "execution_kind <> 'local' OR estimated_cost_minor = 0",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_local_zero_actual",
            "execution_kind <> 'local' OR actual_cost_minor = 0",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_local_provider_state",
            "execution_kind <> 'local' OR "
            "(submission_state NOT IN ('submitted','request_unknown','reconciling') "
            "AND request_unknown IS FALSE AND length(provider_task_id) = 0)",
        )
        batch.create_check_constraint(
            "ck_podcast_stage_attempts_submitted_task_id",
            "execution_kind <> 'provider' OR submission_state <> 'submitted' OR "
            "length(trim(provider_task_id)) > 0",
        )
    _install_audit(include_execution_kind=True)


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_attempt_writers(bind)
    _assert_parent_downgrade_safe(bind)
    _drop_identity_trigger(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "podcast_stage_attempts", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "ck_podcast_stage_attempts_submitted_task_id", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_local_provider_state", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_local_zero_actual", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_local_zero_estimate", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_stage_attempts_execution_kind", type_="check"
        )
        batch.drop_column("execution_kind")
    _install_audit(include_execution_kind=False)
