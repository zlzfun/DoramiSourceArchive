"""Node-local candidate governance leases and atomic action ledger.

Revision ID: c298a1100001
Revises: b8d2f4a6c901
"""
from alembic import op
import sqlalchemy as sa

revision = "c298a1100001"
down_revision = "b8d2f4a6c901"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "candidate_governance_state" not in tables:
        op.create_table("candidate_governance_state",
            sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("cms_tag_candidates.id", ondelete="CASCADE"), primary_key=True),
            *[sa.Column(name, sa.String(), nullable=False) for name in (
                "lane", "evidence_fingerprint", "stats_at", "last_evaluated_at", "next_evaluation_at",
                "rule_version", "lease_token", "lease_until", "last_error_code", "independent_evidence_json",
                "created_at", "updated_at")],
            sa.Column("evidence_revision", sa.Integer(), nullable=False),
            sa.Column("failure_count", sa.Integer(), nullable=False))
        op.create_index("ix_candidate_governance_due", "candidate_governance_state", ["lane", "next_evaluation_at"])
    if "candidate_governance_actions" not in tables:
        op.create_table("candidate_governance_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action_key", sa.String(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("cms_tag_candidates.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_tag_id", sa.Integer(), sa.ForeignKey("cms_tags.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_id", sa.Integer(), sa.ForeignKey("cms_tag_events.id", ondelete="SET NULL")),
            sa.Column("retag_job_id", sa.Integer(), sa.ForeignKey("tag_retag_jobs.id", ondelete="SET NULL")),
            sa.Column("evidence_fingerprint", sa.String(), nullable=False),
            sa.Column("taxonomy_revision", sa.Integer(), nullable=False),
            sa.Column("rule_version", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.UniqueConstraint("action_key", name="uq_candidate_governance_action_key"))


def downgrade():
    op.drop_table("candidate_governance_actions")
    op.drop_index("ix_candidate_governance_due", table_name="candidate_governance_state")
    op.drop_table("candidate_governance_state")
