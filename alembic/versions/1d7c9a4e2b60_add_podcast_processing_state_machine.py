"""add Podcast processing lease, attempt and CNY cost state

Revision ID: 1d7c9a4e2b60
Revises: 7e4a1c9b2d63
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "1d7c9a4e2b60"
down_revision: Union[str, Sequence[str], None] = "7e4a1c9b2d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUDIT_TRIGGERS = (
    "podcast_stage_attempt_identity_immutable",
    "podcast_cost_ledger_immutable_update",
    "podcast_cost_ledger_immutable_delete",
    "podcast_budget_reservation_transition",
    "podcast_budget_reservation_immutable_delete",
)

_AUDIT_TRIGGER_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS podcast_stage_attempt_identity_immutable
    BEFORE UPDATE ON podcast_stage_attempts
    WHEN NEW.id IS NOT OLD.id
      OR NEW.processing_id IS NOT OLD.processing_id
      OR NEW.stage IS NOT OLD.stage
      OR NEW.attempt_no IS NOT OLD.attempt_no
      OR NEW.fencing_token IS NOT OLD.fencing_token
      OR NEW.lease_token IS NOT OLD.lease_token
      OR NEW.input_hash IS NOT OLD.input_hash
      OR NEW.provider_name IS NOT OLD.provider_name
      OR NEW.model_name IS NOT OLD.model_name
      OR NEW.provider_revision IS NOT OLD.provider_revision
      OR NEW.provider_request_key IS NOT OLD.provider_request_key
      OR (length(OLD.provider_task_id) > 0 AND NEW.provider_task_id IS NOT OLD.provider_task_id)
      OR NEW.cost_currency IS NOT OLD.cost_currency
      OR NEW.estimated_cost_minor IS NOT OLD.estimated_cost_minor
      OR NEW.started_at IS NOT OLD.started_at
      OR NEW.created_at IS NOT OLD.created_at
    BEGIN
      SELECT RAISE(ABORT, 'podcast stage attempt identity is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS podcast_cost_ledger_immutable_update
    BEFORE UPDATE ON podcast_cost_ledger
    BEGIN
      SELECT RAISE(ABORT, 'podcast cost ledger is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS podcast_cost_ledger_immutable_delete
    BEFORE DELETE ON podcast_cost_ledger
    WHEN EXISTS (
      SELECT 1 FROM podcast_processings p WHERE p.id = OLD.processing_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'podcast cost ledger is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS podcast_budget_reservation_transition
    BEFORE UPDATE ON podcast_budget_reservations
    WHEN NEW.id IS NOT OLD.id
      OR NEW.processing_id IS NOT OLD.processing_id
      OR NEW.attempt_id IS NOT OLD.attempt_id
      OR NEW.budget_scope IS NOT OLD.budget_scope
      OR NEW.budget_period IS NOT OLD.budget_period
      OR NEW.currency IS NOT OLD.currency
      OR NEW.reserved_minor IS NOT OLD.reserved_minor
      OR NEW.idempotency_key IS NOT OLD.idempotency_key
      OR NEW.created_at IS NOT OLD.created_at
      OR OLD.status <> 'reserved'
      OR NEW.status NOT IN ('settled','released')
      OR (NEW.status = 'settled' AND (
          NEW.settled_at IS NULL OR NEW.released_at IS NOT NULL
      ))
      OR (NEW.status = 'released' AND (
          NEW.released_at IS NULL OR NEW.settled_at IS NOT NULL
          OR NEW.actual_cost_minor <> 0
      ))
    BEGIN
      SELECT RAISE(ABORT, 'invalid podcast budget reservation transition');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS podcast_budget_reservation_immutable_delete
    BEFORE DELETE ON podcast_budget_reservations
    WHEN EXISTS (
      SELECT 1 FROM podcast_processings p WHERE p.id = OLD.processing_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'podcast budget reservation is immutable');
    END
    """,
)


def _install_audit_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        statements = _AUDIT_TRIGGER_SQL
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_processing_postgresql_audit_sql

        statements = _podcast_processing_postgresql_audit_sql()
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _drop_audit_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for name in _AUDIT_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
        return
    if bind.dialect.name == "postgresql":
        for name, table in (
            ("podcast_stage_attempt_identity_immutable", "podcast_stage_attempts"),
            ("podcast_cost_ledger_immutable_update", "podcast_cost_ledger"),
            ("podcast_cost_ledger_immutable_delete", "podcast_cost_ledger"),
            ("podcast_budget_reservation_transition", "podcast_budget_reservations"),
            (
                "podcast_budget_reservation_immutable_delete",
                "podcast_budget_reservations",
            ),
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')
        for name in (
            "podcast_stage_attempt_identity_immutable_fn",
            "podcast_cost_ledger_immutable_fn",
            "podcast_budget_reservation_transition_fn",
            "podcast_budget_reservation_immutable_delete_fn",
        ):
            bind.exec_driver_sql(f'DROP FUNCTION IF EXISTS "{name}"()')


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    processing_tables = {
        "podcast_processings",
        "podcast_stage_attempts",
        "podcast_budget_reservations",
        "podcast_cost_ledger",
    }
    if processing_tables.issubset(existing_tables):
        # ``ensure_migrated`` adopts an unversioned legacy database after
        # ``create_all`` has already materialized the current schema.
        _install_audit_triggers()
        return
    partial = processing_tables & existing_tables
    if partial:
        raise RuntimeError(
            "partial Podcast processing schema exists; restore a consistent "
            "backup before retrying migration"
        )
    op.create_table(
        "podcast_processings",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("episode_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("input_fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pipeline_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("policy_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("requested_target", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("selection_source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("requested_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("request_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("eligibility_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("eligibility_reasons_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("processing_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("lease_token", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("lease_expires_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("heartbeat_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("asr_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("asr_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("asr_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("llm_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("llm_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("llm_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tts_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tts_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tts_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("audio_minutes", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("tts_characters", sa.Integer(), nullable=False),
        sa.Column("tts_audio_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("estimated_cost_minor", sa.Integer(), nullable=False),
        sa.Column("actual_cost_minor", sa.Integer(), nullable=False),
        sa.Column("budget_breached", sa.Boolean(), nullable=False),
        sa.Column("stage_cost_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("queued_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "eligibility_status IN ('unknown','blocked_source','blocked_rights',"
            "'rejected_relevance','rejected_value','invalid_input','over_budget','eligible')",
            name="ck_podcast_processings_eligibility",
        ),
        sa.CheckConstraint(
            "processing_status IN ('not_required','queued','running','retry_wait',"
            "'awaiting_review','ready','failed','cancelled','superseded')",
            name="ck_podcast_processings_status",
        ),
        sa.CheckConstraint(
            "stage IN ('fetch','admission','asr','translate','analyze','digest','script',"
            "'tts','audio_qa','local_publish')",
            name="ck_podcast_processings_stage",
        ),
        sa.CheckConstraint(
            "selection_source IN ('policy','editor')",
            name="ck_podcast_processings_selection_source",
        ),
        sa.CheckConstraint(
            "requested_target IN ('transcript','digest_blog','digest_audio')",
            name="ck_podcast_processings_requested_target",
        ),
        sa.CheckConstraint(
            "selection_source <> 'editor' OR length(trim(request_reason)) > 0",
            name="ck_podcast_processings_editor_reason",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64 AND length(trim(pipeline_version)) > 0 "
            "AND length(trim(idempotency_key)) > 0",
            name="ck_podcast_processings_identity",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND fencing_token >= 0 AND estimated_cost_minor >= 0 "
            "AND actual_cost_minor >= 0",
            name="ck_podcast_processings_nonnegative",
        ),
        sa.CheckConstraint(
            "audio_minutes >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND tts_characters >= 0 AND tts_audio_tokens >= 0",
            name="ck_podcast_processings_usage_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost_minor = CAST(estimated_cost_minor AS INTEGER) AND "
            "actual_cost_minor = CAST(actual_cost_minor AS INTEGER)",
            name="ck_podcast_processings_integer_cost",
        ),
        sa.CheckConstraint("cost_currency = 'CNY'", name="ck_podcast_processings_currency"),
        sa.CheckConstraint(
            "processing_status <> 'running' OR "
            "(lease_owner IS NOT NULL AND length(lease_owner) > 0 AND "
            "lease_token IS NOT NULL AND length(lease_token) > 0 AND "
            "lease_expires_at IS NOT NULL)",
            name="ck_podcast_processings_running_lease",
        ),
        sa.CheckConstraint(
            "processing_status = 'running' OR "
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_podcast_processings_idle_lease",
        ),
        sa.CheckConstraint(
            "processing_status <> 'retry_wait' OR next_retry_at IS NOT NULL",
            name="ck_podcast_processings_retry_time",
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["articles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "episode_id",
            "input_fingerprint",
            "pipeline_version",
            "requested_target",
            name="uq_podcast_processings_effective_run",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_podcast_processings_idempotency_key"
        ),
    )
    for name, columns in (
        ("ix_podcast_processings_claim", ["processing_status", "next_retry_at", "lease_expires_at", "queued_at"]),
        ("ix_podcast_processings_episode_created", ["episode_id", "created_at"]),
        ("ix_podcast_processings_eligibility_status", ["eligibility_status"]),
        ("ix_podcast_processings_episode_id", ["episode_id"]),
        ("ix_podcast_processings_input_fingerprint", ["input_fingerprint"]),
        ("ix_podcast_processings_job_id", ["job_id"]),
        ("ix_podcast_processings_processing_status", ["processing_status"]),
        ("ix_podcast_processings_queued_at", ["queued_at"]),
        ("ix_podcast_processings_stage", ["stage"]),
        ("ix_podcast_processings_budget_breached", ["budget_breached"]),
    ):
        op.create_index(name, "podcast_processings", columns, unique=False)

    op.create_table(
        "podcast_stage_attempts",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("processing_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("lease_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("input_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("output_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_request_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("submission_state", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("request_unknown", sa.Boolean(), nullable=False),
        sa.Column("retry_state", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("usage_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cost_currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("estimated_cost_minor", sa.Integer(), nullable=False),
        sa.Column("actual_cost_minor", sa.Integer(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("submitted_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("completed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint(
            "stage IN ('fetch','admission','asr','translate','analyze','digest','script',"
            "'tts','audio_qa','local_publish')",
            name="ck_podcast_stage_attempts_stage",
        ),
        sa.CheckConstraint(
            "submission_state IN ('prepared','submitted','request_unknown','reconciling',"
            "'succeeded','failed_retryable','failed_terminal','cancelled')",
            name="ck_podcast_stage_attempts_submission_state",
        ),
        sa.CheckConstraint(
            "retry_state IN ('none','scheduled','reconcile_required','exhausted')",
            name="ck_podcast_stage_attempts_retry_state",
        ),
        sa.CheckConstraint(
            "attempt_no >= 1 AND fencing_token >= 1 AND estimated_cost_minor >= 0 "
            "AND actual_cost_minor >= 0",
            name="ck_podcast_stage_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost_minor = CAST(estimated_cost_minor AS INTEGER) AND "
            "actual_cost_minor = CAST(actual_cost_minor AS INTEGER)",
            name="ck_podcast_stage_attempts_integer_cost",
        ),
        sa.CheckConstraint("cost_currency = 'CNY'", name="ck_podcast_stage_attempts_currency"),
        sa.CheckConstraint(
            "request_unknown IS FALSE OR "
            "(submission_state IN ('request_unknown','reconciling') AND "
            "retry_state = 'reconcile_required')",
            name="ck_podcast_stage_attempts_unknown_request",
        ),
        sa.CheckConstraint(
            "request_unknown IS TRUE OR submission_state NOT IN ('request_unknown','reconciling')",
            name="ck_podcast_stage_attempts_unknown_state",
        ),
        sa.CheckConstraint(
            "length(trim(provider_request_key)) > 0",
            name="ck_podcast_stage_attempts_provider_request_key",
        ),
        sa.ForeignKeyConstraint(["processing_id"], ["podcast_processings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "processing_id", "attempt_no", name="uq_podcast_stage_attempts_number"
        ),
        sa.UniqueConstraint(
            "id", "processing_id", name="uq_podcast_stage_attempts_owner"
        ),
        sa.UniqueConstraint(
            "provider_request_key", name="uq_podcast_stage_attempts_provider_request_key"
        ),
    )
    for name, columns in (
        ("ix_podcast_stage_attempts_processing_id", ["processing_id"]),
        ("ix_podcast_stage_attempts_processing_stage", ["processing_id", "stage", "attempt_no"]),
        ("ix_podcast_stage_attempts_provider_task", ["provider_name", "provider_task_id"]),
        ("ix_podcast_stage_attempts_stage", ["stage"]),
        ("ix_podcast_stage_attempts_submission_state", ["submission_state"]),
    ):
        op.create_index(name, "podcast_stage_attempts", columns, unique=False)

    op.create_table(
        "podcast_budget_reservations",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("processing_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempt_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("budget_scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("budget_period", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reserved_minor", sa.Integer(), nullable=False),
        sa.Column("actual_cost_minor", sa.Integer(), nullable=False),
        sa.Column("budget_breached", sa.Boolean(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("settled_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("released_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.CheckConstraint(
            "status IN ('reserved','settled','released')",
            name="ck_podcast_budget_reservations_status",
        ),
        sa.CheckConstraint("currency = 'CNY'", name="ck_podcast_budget_reservations_currency"),
        sa.CheckConstraint(
            "reserved_minor >= 0 AND actual_cost_minor >= 0",
            name="ck_podcast_budget_reservations_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_minor = CAST(reserved_minor AS INTEGER) AND "
            "actual_cost_minor = CAST(actual_cost_minor AS INTEGER)",
            name="ck_podcast_budget_reservations_integer_cost",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "processing_id"],
            ["podcast_stage_attempts.id", "podcast_stage_attempts.processing_id"],
            name="fk_podcast_budget_reservations_attempt_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["processing_id"], ["podcast_processings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", name="uq_podcast_budget_reservations_attempt"),
        sa.UniqueConstraint(
            "id", "attempt_id", "processing_id",
            name="uq_podcast_budget_reservations_owner",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_podcast_budget_reservations_idempotency_key"
        ),
    )
    for name, columns in (
        ("ix_podcast_budget_reservations_attempt_id", ["attempt_id"]),
        ("ix_podcast_budget_reservations_capacity", ["budget_scope", "budget_period", "status"]),
        ("ix_podcast_budget_reservations_processing_id", ["processing_id"]),
        ("ix_podcast_budget_reservations_status", ["status"]),
        ("ix_podcast_budget_reservations_budget_breached", ["budget_breached"]),
    ):
        op.create_index(name, "podcast_budget_reservations", columns, unique=False)

    op.create_table(
        "podcast_cost_ledger",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("processing_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempt_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reservation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("budget_scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("budget_period", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("actual_cost_minor", sa.Integer(), nullable=False),
        sa.Column("budget_breached", sa.Boolean(), nullable=False),
        sa.Column("usage_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("settlement_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint("currency = 'CNY'", name="ck_podcast_cost_ledger_currency"),
        sa.CheckConstraint(
            "actual_cost_minor >= 0", name="ck_podcast_cost_ledger_nonnegative"
        ),
        sa.CheckConstraint(
            "actual_cost_minor = CAST(actual_cost_minor AS INTEGER)",
            name="ck_podcast_cost_ledger_integer_cost",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "processing_id"],
            ["podcast_stage_attempts.id", "podcast_stage_attempts.processing_id"],
            name="fk_podcast_cost_ledger_attempt_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["processing_id"], ["podcast_processings.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reservation_id", "attempt_id", "processing_id"],
            ["podcast_budget_reservations.id", "podcast_budget_reservations.attempt_id", "podcast_budget_reservations.processing_id"],
            name="fk_podcast_cost_ledger_reservation_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", name="uq_podcast_cost_ledger_attempt"),
        sa.UniqueConstraint("settlement_key", name="uq_podcast_cost_ledger_settlement_key"),
    )
    for name, columns in (
        ("ix_podcast_cost_ledger_attempt_id", ["attempt_id"]),
        ("ix_podcast_cost_ledger_budget_created", ["budget_scope", "budget_period", "created_at"]),
        ("ix_podcast_cost_ledger_processing_id", ["processing_id"]),
        ("ix_podcast_cost_ledger_reservation_id", ["reservation_id"]),
        ("ix_podcast_cost_ledger_stage", ["stage"]),
        ("ix_podcast_cost_ledger_budget_breached", ["budget_breached"]),
    ):
        op.create_index(name, "podcast_cost_ledger", columns, unique=False)

    _install_audit_triggers()


def _assert_archive_sync_downgrade_safe() -> None:
    """Fence a multi-revision SQLite downgrade before destructive DDL.

    SQLite cannot roll back the complete Alembic downgrade chain once a later
    revision refuses it.  Mirror the downstream Archive Sync guards at the
    current head so a refusal leaves both the schema and version intact.
    """

    bind = op.get_bind()
    for table, column in (
        ("articles", "analysis_authority_id"),
        ("article_analyses", "authority_id"),
        ("source_configs", "collection_authority_id"),
        ("source_states", "authority_id"),
        ("media_assets", "sync_authority_id"),
        ("podcast_artifacts", "authority_id"),
        ("podcast_text_artifacts", "authority_id"),
        ("podcast_text_publications", "authority_id"),
    ):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} <> '' LIMIT 1"
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
        "SELECT 1 FROM personal_digest_editions "
        "WHERE desired_generation_reason IS NOT NULL "
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
        '"enabled":true' in compact_schedule
        and '"protocol":"v2"' in compact_schedule
    ):
        raise RuntimeError(
            "拒绝降级 Archive Sync：consumer 围栏仍生效。"
            "请先停止 worker，并恢复升级前备份。"
        )


def downgrade() -> None:
    _assert_archive_sync_downgrade_safe()
    _drop_audit_triggers()
    op.drop_table("podcast_cost_ledger")
    op.drop_table("podcast_budget_reservations")
    op.drop_table("podcast_stage_attempts")
    op.drop_table("podcast_processings")
