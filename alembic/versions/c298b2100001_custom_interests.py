"""Private custom interest conditions, matching jobs and revisions (issue #298).

Revision ID: c298b2100001
Revises: c298a1100001
"""
from alembic import op
import sqlalchemy as sa

revision = 'c298b2100001'
down_revision = 'c298a1100001'
branch_labels = None
depends_on = None


def upgrade():
    # Explicit frozen schema, independent of later model edits; legacy bootstrap
    # may already have these tables through current metadata.create_all().
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'user_interest_state' not in existing:
        op.create_table('user_interest_state',
            sa.Column('owner_username', sa.String(), sa.ForeignKey('users.username', ondelete='CASCADE'), primary_key=True),
            sa.Column('revision', sa.Integer(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False))
    if 'user_interest_conditions' not in existing:
        op.create_table('user_interest_conditions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('owner_username', sa.String(), sa.ForeignKey('users.username', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(), nullable=False), sa.Column('description', sa.String(), nullable=False),
            sa.Column('condition_json', sa.String(), nullable=False), sa.Column('schema_version', sa.Integer(), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False), sa.Column('status', sa.String(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False),
            sa.CheckConstraint("status IN ('active','paused','deleted')", name='ck_interest_condition_status'))
        op.create_index('ix_interest_conditions_owner_status','user_interest_conditions',['owner_username','status'])
    if 'interest_article_matches' not in existing:
        op.create_table('interest_article_matches',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('owner_username', sa.String(), sa.ForeignKey('users.username', ondelete='CASCADE'), nullable=False),
            sa.Column('condition_id', sa.Integer(), sa.ForeignKey('user_interest_conditions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('condition_revision', sa.Integer(), nullable=False),
            sa.Column('article_id', sa.String(), sa.ForeignKey('articles.id', ondelete='CASCADE'), nullable=False),
            sa.Column('fingerprint', sa.String(), nullable=False), sa.Column('matcher_version', sa.String(), nullable=False),
            sa.Column('outcome', sa.String(), nullable=False), sa.Column('evidence_json', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.UniqueConstraint('condition_id','article_id',name='uq_interest_match_condition_article'),
            sa.CheckConstraint("outcome IN ('core','mention','no_match','unknown')",name='ck_interest_match_outcome'))
        op.create_index('ix_interest_match_owner_outcome','interest_article_matches',['owner_username','outcome','article_id'])
    if 'interest_match_jobs' not in existing:
        op.create_table('interest_match_jobs',
            sa.Column('condition_id', sa.Integer(), sa.ForeignKey('user_interest_conditions.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('condition_revision', sa.Integer(), nullable=False), sa.Column('cursor', sa.String(), nullable=False),
            sa.Column('cycle_cutoff_at', sa.String(), nullable=False), sa.Column('next_attempt_at', sa.String(), nullable=False),
            sa.Column('lease_token', sa.String(), nullable=False), sa.Column('lease_expires_at', sa.String(), nullable=False),
            sa.Column('last_error', sa.String(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False))
        op.create_index('ix_interest_match_jobs_due','interest_match_jobs',['next_attempt_at','lease_expires_at'])


def downgrade():
    for table in ('interest_match_jobs','interest_article_matches','user_interest_conditions','user_interest_state'):
        op.drop_table(table)
