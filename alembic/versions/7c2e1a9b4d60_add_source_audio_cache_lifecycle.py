"""add bounded source-audio cache lifecycle

Revision ID: 7c2e1a9b4d60
Revises: 4a6f9c2d8e31
Create Date: 2026-09-06
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "7c2e1a9b4d60"
down_revision: Union[str, Sequence[str], None] = "4a6f9c2d8e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CACHE_COLUMNS = (
    "source_locator_hash",
    "expires_at",
    "expired_at",
)

_AUDIO_TRIGGERS = (
    "podcast_audio_dependency_insert",
    "podcast_audio_dependency_update",
    "podcast_audio_processing_insert",
    "podcast_audio_processing_update",
    "podcast_audio_binding_immutable",
    "podcast_script_audio_invalidate_update",
    "podcast_script_audio_invalidate_delete",
)


def _require_online_migration() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "Podcast source-audio cache migration requires an online database "
            "connection for writer fencing and downgrade safety checks"
        )


def _lock_writers(bind: sa.Connection) -> None:
    """Close the check/DDL race with cache importers and processing workers."""

    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE podcast_artifacts, podcast_processings, "
            "podcast_stage_attempts, podcast_budget_reservations, "
            "podcast_processing_commands "
            "IN ACCESS EXCLUSIVE MODE"
        )
    elif bind.dialect.name == "sqlite":
        # Alembic is already inside its revision transaction. This no-op write
        # obtains SQLite's database-wide writer reservation before inspection.
        bind.exec_driver_sql(
            "UPDATE podcast_artifacts SET updated_at = updated_at WHERE 0"
        )


def _drop_audio_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for name in _AUDIO_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    elif bind.dialect.name == "postgresql":
        for name, table in (
            ("podcast_audio_dependency_insert", "podcast_artifacts"),
            ("podcast_audio_dependency_update", "podcast_artifacts"),
            ("podcast_script_audio_invalidate_update", "podcast_text_publications"),
            ("podcast_script_audio_invalidate_delete", "podcast_text_publications"),
        ):
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')


def _install_audio_triggers(*, include_source_cache_fields: bool = True) -> None:
    """Install only trigger SQL valid at this revision's physical schema."""

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        from models.db import _podcast_audio_dependency_trigger_sql

        statements = _podcast_audio_dependency_trigger_sql(
            include_source_cache_fields=include_source_cache_fields
        )
    elif bind.dialect.name == "postgresql":
        from models.db import _podcast_audio_dependency_postgresql_sql

        statements = _podcast_audio_dependency_postgresql_sql(
            include_source_cache_fields=include_source_cache_fields
        )
    else:
        return
    for statement in statements:
        bind.exec_driver_sql(statement)


def _assert_parent_downgrade_safe() -> None:
    """Run the parent's fences before SQLite can advance off this head."""

    parent_path = Path(__file__).with_name(
        "4a6f9c2d8e31_add_podcast_processing_admin.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_dorami_podcast_processing_admin_parent_revision", parent_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Podcast parent downgrade fence")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._assert_downgrade_safe()


def upgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("podcast_artifacts")
    }
    if set(_CACHE_COLUMNS).issubset(columns):
        # Legacy create_all databases already contain the current model.
        _install_audio_triggers()
        return
    if set(_CACHE_COLUMNS) & columns:
        raise RuntimeError(
            "partial Podcast source-audio cache schema exists; restore a "
            "consistent backup before retrying migration"
        )

    for column_name in _CACHE_COLUMNS:
        op.add_column(
            "podcast_artifacts",
            sa.Column(
                column_name,
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
        )

    # Historical source audio had permanent semantics. Expire it conservatively
    # from its creation timestamp; active processing references may pin it when
    # the lifecycle reconciler first runs. Digest audio remains durable.
    bind.execute(sa.text(
        "UPDATE podcast_artifacts "
        "SET expires_at = created_at, "
        "status = CASE WHEN status = 'published' THEN 'ready' ELSE status END, "
        "published_at = CASE WHEN status = 'published' THEN NULL ELSE published_at END "
        "WHERE kind = 'source_audio'"
    ))

    _drop_audio_triggers()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_artifacts", recreate=recreate) as batch:
        batch.drop_constraint("ck_podcast_artifacts_status", type_="check")
        batch.create_check_constraint(
            "ck_podcast_artifacts_status",
            "status IN ('ready','published','withdrawn','expired')",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_source_locator_hash",
            "source_locator_hash IS NULL OR "
            "(length(source_locator_hash) = 64 AND "
            "source_locator_hash = lower(source_locator_hash))",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_source_expires",
            "kind <> 'source_audio' OR expires_at IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_source_never_published",
            "kind <> 'source_audio' OR status <> 'published'",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_digest_is_durable",
            "kind <> 'digest_audio_zh' OR "
            "(source_locator_hash IS NULL AND expires_at IS NULL "
            "AND expired_at IS NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_expired_status",
            "status <> 'expired' OR "
            "(kind = 'source_audio' AND expired_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_podcast_artifacts_expired_at",
            "expired_at IS NULL OR "
            "(kind = 'source_audio' AND status = 'expired')",
        )

    for name, column in (
        ("ix_podcast_artifacts_source_locator_hash", "source_locator_hash"),
        ("ix_podcast_artifacts_expires_at", "expires_at"),
        ("ix_podcast_artifacts_expired_at", "expired_at"),
    ):
        op.create_index(name, "podcast_artifacts", [column], unique=False)
    op.create_index(
        "ix_podcast_artifacts_kind_status_expires",
        "podcast_artifacts",
        ["kind", "status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_podcast_processings_input_status",
        "podcast_processings",
        ["input_artifact_id", "processing_status"],
        unique=False,
    )
    _install_audio_triggers()


def downgrade() -> None:
    _require_online_migration()
    bind = op.get_bind()
    _lock_writers(bind)
    _assert_parent_downgrade_safe()
    source_row = bind.execute(sa.text(
        "SELECT 1 FROM podcast_artifacts WHERE kind = 'source_audio' LIMIT 1"
    )).first()
    if source_row is not None:
        raise RuntimeError(
            "拒绝降级 Podcast 原音频缓存：数据库仍含 source_audio 记录，"
            "降级会恢复永久镜像语义。请先停止 worker、清理或导出记录，"
            "然后恢复升级前备份。"
        )

    _drop_audio_triggers()
    op.drop_index(
        "ix_podcast_processings_input_status",
        table_name="podcast_processings",
    )
    for name in (
        "ix_podcast_artifacts_kind_status_expires",
        "ix_podcast_artifacts_expired_at",
        "ix_podcast_artifacts_expires_at",
        "ix_podcast_artifacts_source_locator_hash",
    ):
        op.drop_index(name, table_name="podcast_artifacts")

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("podcast_artifacts", recreate=recreate) as batch:
        batch.drop_constraint("ck_podcast_artifacts_expired_at", type_="check")
        batch.drop_constraint("ck_podcast_artifacts_expired_status", type_="check")
        batch.drop_constraint(
            "ck_podcast_artifacts_digest_is_durable", type_="check"
        )
        batch.drop_constraint("ck_podcast_artifacts_source_expires", type_="check")
        batch.drop_constraint(
            "ck_podcast_artifacts_source_never_published", type_="check"
        )
        batch.drop_constraint(
            "ck_podcast_artifacts_source_locator_hash", type_="check"
        )
        batch.drop_constraint("ck_podcast_artifacts_status", type_="check")
        batch.create_check_constraint(
            "ck_podcast_artifacts_status",
            "status IN ('ready','published','withdrawn')",
        )
        batch.drop_column("expired_at")
        batch.drop_column("expires_at")
        batch.drop_column("source_locator_hash")
    _install_audio_triggers(include_source_cache_fields=False)
