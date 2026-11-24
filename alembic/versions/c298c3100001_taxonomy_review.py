"""Node-local review proposals and durable daily notifications (#298)."""
from alembic import op
import sqlalchemy as sa

revision = "c298c3100001"
down_revision = "c298b2100001"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'review_proposals' not in existing:
        op.create_table('review_proposals',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('identity', sa.String(), nullable=False, primary_key=False),
            sa.Column('member_ids_json', sa.String(), nullable=False, primary_key=False),
            sa.Column('canonical_name', sa.String(), nullable=False, primary_key=False),
            sa.Column('kind', sa.String(), nullable=False, primary_key=False),
            sa.Column('definition', sa.String(), nullable=False, primary_key=False),
            sa.Column('relation', sa.String(), nullable=False, primary_key=False),
            sa.Column('target_tag_id', sa.Integer(), sa.ForeignKey('cms_tags.id', ondelete='SET NULL'), nullable=True, primary_key=False),
            sa.Column('recommendation', sa.String(), nullable=False, primary_key=False),
            sa.Column('reason', sa.String(), nullable=False, primary_key=False),
            sa.Column('risks_json', sa.String(), nullable=False, primary_key=False),
            sa.Column('status', sa.String(), nullable=False, primary_key=False),
            sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('taxonomy_revision', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('evidence_hash', sa.String(), nullable=False, primary_key=False),
            sa.Column('evidence_json', sa.String(), nullable=False, primary_key=False),
            sa.Column('priority', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('next_review_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('decided_by', sa.String(), nullable=False, primary_key=False),
            sa.Column('decision_reason', sa.String(), nullable=False, primary_key=False),
            sa.Column('draft_tag_id', sa.Integer(), sa.ForeignKey('cms_tags.id', ondelete='SET NULL'), nullable=True, primary_key=False),
            sa.Column('retag_job_id', sa.Integer(), sa.ForeignKey('tag_retag_jobs.id', ondelete='SET NULL'), nullable=True, primary_key=False),
            sa.Column('created_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('updated_at', sa.String(), nullable=False, primary_key=False),
            sa.UniqueConstraint('identity', name='uq_review_proposal_identity'),
            sa.CheckConstraint("status IN ('pending','deferred','rejected','approved','merged','split','resolved')", name='ck_review_proposal_status'),
        )
        op.create_index('ix_review_proposals_status_due','review_proposals',['status', 'next_review_at'],unique=False)
    if 'review_proposal_revisions' not in existing:
        op.create_table('review_proposal_revisions',
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('review_proposals.id', ondelete='CASCADE'), nullable=False, primary_key=True),
            sa.Column('revision', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('snapshot_json', sa.String(), nullable=False, primary_key=False),
            sa.Column('actor_id', sa.String(), nullable=False, primary_key=False),
            sa.Column('action', sa.String(), nullable=False, primary_key=False),
            sa.Column('reason', sa.String(), nullable=False, primary_key=False),
            sa.Column('created_at', sa.String(), nullable=False, primary_key=False),
        )
    if 'review_inbox' not in existing:
        op.create_table('review_inbox',
            sa.Column('recipient', sa.String(), nullable=False, primary_key=True),
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('review_proposals.id', ondelete='CASCADE'), nullable=False, primary_key=True),
            sa.Column('revision', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('last_notified_revision', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('status', sa.String(), nullable=False, primary_key=False),
            sa.Column('updated_at', sa.String(), nullable=False, primary_key=False),
        )
    if 'review_notification_outbox' not in existing:
        op.create_table('review_notification_outbox',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('recipient', sa.String(), nullable=False, primary_key=False),
            sa.Column('channel', sa.String(), nullable=False, primary_key=False),
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('review_proposals.id', ondelete='CASCADE'), nullable=False, primary_key=False),
            sa.Column('proposal_revision', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('status', sa.String(), nullable=False, primary_key=False),
            sa.Column('batch_id', sa.String(), nullable=False, primary_key=False),
            sa.Column('created_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('updated_at', sa.String(), nullable=False, primary_key=False),
            sa.UniqueConstraint('recipient','channel','proposal_id','proposal_revision', name='uq_review_outbox_revision'),
            sa.CheckConstraint("status IN ('pending','sending','accepted','failed','unknown','cancelled')", name='ck_review_outbox_status'),
        )
        op.create_index('ix_review_outbox_pending','review_notification_outbox',['status', 'created_at'],unique=False)
    if 'review_notification_batches' not in existing:
        op.create_table('review_notification_batches',
            sa.Column('id', sa.String(), nullable=False, primary_key=True),
            sa.Column('recipient', sa.String(), nullable=False, primary_key=False),
            sa.Column('channel', sa.String(), nullable=False, primary_key=False),
            sa.Column('digest_date', sa.String(), nullable=False, primary_key=False),
            sa.Column('destination_hash', sa.String(), nullable=False, primary_key=False),
            sa.Column('outbox_ids_json', sa.String(), nullable=False, primary_key=False),
            sa.Column('status', sa.String(), nullable=False, primary_key=False),
            sa.Column('attempt_count', sa.Integer(), nullable=False, primary_key=False),
            sa.Column('next_attempt_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('lease_token', sa.String(), nullable=False, primary_key=False),
            sa.Column('lease_expires_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('error_code', sa.String(), nullable=False, primary_key=False),
            sa.Column('created_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('updated_at', sa.String(), nullable=False, primary_key=False),
            sa.CheckConstraint("status IN ('pending','sending','accepted','failed','unknown','cancelled')", name='ck_review_batch_status'),
            sa.UniqueConstraint('recipient','channel','digest_date', name='uq_review_batch_daily'),
        )
    if 'review_notification_attempts' not in existing:
        op.create_table('review_notification_attempts',
            sa.Column('batch_id', sa.String(), sa.ForeignKey('review_notification_batches.id', ondelete='CASCADE'), nullable=False, primary_key=True),
            sa.Column('attempt', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('status', sa.String(), nullable=False, primary_key=False),
            sa.Column('error_code', sa.String(), nullable=False, primary_key=False),
            sa.Column('http_status', sa.Integer(), nullable=True, primary_key=False),
            sa.Column('provider_code', sa.Integer(), nullable=True, primary_key=False),
            sa.Column('started_at', sa.String(), nullable=False, primary_key=False),
            sa.Column('ended_at', sa.String(), nullable=False, primary_key=False),
        )
    if 'review_proposal_members' not in existing:
        op.create_table('review_proposal_members',
            sa.Column('candidate_id', sa.Integer(), sa.ForeignKey('cms_tag_candidates.id', ondelete='CASCADE'), nullable=False, primary_key=True),
            sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('review_proposals.id', ondelete='CASCADE'), nullable=False, primary_key=False),
        )
        op.create_index('ix_review_proposal_members_proposal_id','review_proposal_members',['proposal_id'],unique=False)


def downgrade():
    for name in ('review_proposal_members', 'review_notification_attempts', 'review_notification_batches', 'review_notification_outbox', 'review_inbox', 'review_proposal_revisions', 'review_proposals'):
        op.drop_table(name)
