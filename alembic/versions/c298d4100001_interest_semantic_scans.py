"""Durable private semantic scan pages and paid request fences (#298)."""
from alembic import op
import sqlalchemy as sa

revision = 'c298d4100001'
down_revision = 'c298c3100001'
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'interest_semantic_scans' not in existing:
        op.create_table('interest_semantic_scans',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('content_identity', sa.String(), nullable=False),
            sa.Column('contract_version', sa.String(), nullable=False),
            sa.Column('owner_username', sa.String(), sa.ForeignKey('users.username', ondelete='CASCADE'), nullable=False),
            sa.Column('condition_id', sa.Integer(), sa.ForeignKey('user_interest_conditions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('condition_revision', sa.Integer(), nullable=False),
            sa.Column('article_id', sa.String(), sa.ForeignKey('articles.id', ondelete='CASCADE'), nullable=False),
            sa.Column('projection_hash', sa.String(), nullable=False),
            sa.Column('source_metadata_hash', sa.String(), nullable=False),
            sa.Column('full_chars', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('page_count', sa.Integer(), nullable=False),
            sa.Column('completed_chunk_ids_json', sa.String(), nullable=False),
            sa.Column('reviewed_ranges_json', sa.String(), nullable=False),
            sa.Column('has_mention', sa.Boolean(), nullable=False),
            sa.Column('result_json', sa.String(), nullable=False),
            sa.Column('error_code', sa.String(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.UniqueConstraint('content_identity', 'contract_version', name='uq_interest_scan_content_contract'),
            sa.CheckConstraint("status IN ('deferred','partial','succeeded','unknown','superseded')", name='ck_interest_scan_status'),
        )
        op.create_index('ix_interest_scan_pending', 'interest_semantic_scans', ['condition_id','status','page_count','updated_at'])
        op.create_index('ix_interest_scan_content', 'interest_semantic_scans', ['content_identity','status'])
    if 'interest_semantic_requests' not in existing:
        op.create_table('interest_semantic_requests',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('request_identity', sa.String(), nullable=False),
            sa.Column('scan_id', sa.String(), sa.ForeignKey('interest_semantic_scans.id', ondelete='CASCADE'), nullable=False),
            sa.Column('content_identity', sa.String(), nullable=False),
            sa.Column('cycle_id', sa.String(), nullable=False),
            sa.Column('lease_token', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('reserved_calls', sa.Integer(), nullable=False),
            sa.Column('input_bytes', sa.Integer(), nullable=False),
            sa.Column('output_token_limit', sa.Integer(), nullable=False),
            sa.Column('payload_json', sa.String(), nullable=False),
            sa.Column('result_json', sa.String(), nullable=False),
            sa.Column('error_code', sa.String(), nullable=False),
            sa.Column('authorized_at', sa.String(), nullable=False),
            sa.Column('completed_at', sa.String(), nullable=False),
            sa.UniqueConstraint('request_identity', name='uq_interest_semantic_request_identity'),
            sa.CheckConstraint("status IN ('authorized','succeeded','unknown','failed_confirmed')", name='ck_interest_semantic_request_status'),
            sa.CheckConstraint('reserved_calls = 1 AND input_bytes >= 0 AND output_token_limit > 0', name='ck_interest_semantic_request_hold'),
        )
        op.create_index('ix_interest_semantic_request_content', 'interest_semantic_requests', ['content_identity','status'])
        op.create_index('ix_interest_semantic_request_cycle', 'interest_semantic_requests', ['cycle_id'])


def downgrade():
    op.drop_table('interest_semantic_requests')
    op.drop_table('interest_semantic_scans')
