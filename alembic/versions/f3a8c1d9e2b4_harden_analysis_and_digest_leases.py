"""harden analysis and personal-digest leases

Revision ID: f3a8c1d9e2b4
Revises: d2c4f6a8b0e1
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a8c1d9e2b4"
down_revision: Union[str, Sequence[str], None] = "d2c4f6a8b0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    edition_columns = {
        column["name"] for column in inspector.get_columns("personal_digest_editions")
    }
    if "interest_snapshot_json" not in edition_columns:
        op.add_column(
            "personal_digest_editions",
            sa.Column("interest_snapshot_json", sa.String(), nullable=False, server_default="[]"),
        )
    if "generation_token" not in edition_columns:
        op.add_column(
            "personal_digest_editions",
            sa.Column("generation_token", sa.String(), nullable=True),
        )
    if "generation_lease_expires_at" not in edition_columns:
        op.add_column(
            "personal_digest_editions",
            sa.Column("generation_lease_expires_at", sa.String(), nullable=True),
        )

    def create_index_unless_present(name: str, table: str, columns: list[str], **kwargs) -> None:
        if name not in {index["name"] for index in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, columns, **kwargs)

    create_index_unless_present(
        "ix_personal_digest_editions_generation_token",
        "personal_digest_editions",
        ["generation_token"],
        unique=False,
    )
    create_index_unless_present(
        "ix_tag_retag_job_items_article_id",
        "tag_retag_job_items",
        ["article_id"],
        unique=False,
    )
    create_index_unless_present(
        "ix_duplicate_groups_representative_article_id",
        "duplicate_groups",
        ["representative_article_id"],
        unique=False,
    )

    # Existing custom RSS rows were created before per-subscriber AI consent
    # existed.  Keep their content local even if the old default was true.
    bind.execute(
        sa.text(
            "UPDATE source_configs SET ai_analysis_enabled = 0 "
            "WHERE owner_username <> ''"
        )
    )

    # Old development databases may contain multiple unfinished full-analysis
    # jobs. Keep the newest resumable job before enforcing the invariant.
    active_ids = [
        int(row[0]) for row in bind.execute(sa.text(
            "SELECT id FROM tag_retag_jobs "
            "WHERE operation = 'full_analysis' "
            "AND status IN ('queued','running','paused') ORDER BY id DESC"
        ))
    ]
    if len(active_ids) > 1:
        bind.execute(
            sa.text(
                "UPDATE tag_retag_jobs SET status = 'cancelled', "
                "lease_owner = NULL, lease_expires_at = NULL, "
                "last_error = 'superseded while enforcing one active full-analysis job' "
                "WHERE id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": active_ids[1:]},
        )
    create_index_unless_present(
        "uq_tag_retag_jobs_one_active_full_analysis",
        "tag_retag_jobs",
        ["operation"],
        unique=True,
        sqlite_where=sa.text(
            "operation = 'full_analysis' AND status IN ('queued','running','paused')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_tag_retag_jobs_one_active_full_analysis", table_name="tag_retag_jobs")
    op.drop_index("ix_duplicate_groups_representative_article_id", table_name="duplicate_groups")
    op.drop_index("ix_tag_retag_job_items_article_id", table_name="tag_retag_job_items")
    op.drop_index("ix_personal_digest_editions_generation_token", table_name="personal_digest_editions")
    op.drop_column("personal_digest_editions", "generation_lease_expires_at")
    op.drop_column("personal_digest_editions", "generation_token")
    op.drop_column("personal_digest_editions", "interest_snapshot_json")
