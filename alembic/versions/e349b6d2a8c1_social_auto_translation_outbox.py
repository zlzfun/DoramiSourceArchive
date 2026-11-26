"""social post auto-translation outbox and its isolated budget holds (issue #349)

Revision ID: e349b6d2a8c1
Revises: c3e5a91f7d24
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "e349b6d2a8c1"
down_revision: Union[str, Sequence[str], None] = "c3e5a91f7d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    bind = op.get_bind()
    # 运行期 create_all 可能先于迁移建出这两张表(开发机直接起服务),已存在就不重建。
    if not sa.inspect(bind).has_table("social_translation_jobs"):
        op.create_table(
            "social_translation_jobs",
            sa.Column("article_id", S(), nullable=False),
            sa.Column("source_fp", S(), nullable=False),
            sa.Column("status", S(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("retry_at", sa.Float(), nullable=False),
            sa.Column("lease_until", sa.Float(), nullable=False),
            sa.Column("lease_token", S(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("error", S(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending','running','succeeded','failed','skipped')",
                name="ck_social_translation_job_status",
            ),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("article_id"),
        )
        op.create_index(
            "ix_social_translation_jobs_due", "social_translation_jobs", ["status", "retry_at"],
        )
    if not sa.inspect(bind).has_table("social_translation_calls"):
        op.create_table(
            "social_translation_calls",
            sa.Column("id", S(), nullable=False),
            sa.Column("article_id", S(), nullable=False),
            sa.Column("day", S(), nullable=False),
            sa.Column("reserved_tokens", sa.Integer(), nullable=False),
            sa.Column("status", S(), nullable=False),
            sa.Column("recorded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.CheckConstraint(
                "status IN ('reserved','settled','unknown','released')",
                name="ck_social_translation_call_status",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_social_translation_calls_budget", "social_translation_calls", ["day", "status"],
        )


def downgrade() -> None:
    op.drop_table("social_translation_calls")
    op.drop_table("social_translation_jobs")
