"""Private, node-local interest definitions and reproducible match projections."""
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserInterestStateRecord(SQLModel, table=True):
    __tablename__ = 'user_interest_state'
    owner_username: str = Field(primary_key=True, foreign_key='users.username', ondelete='CASCADE')
    revision: int = 0
    updated_at: str = ''


class UserInterestConditionRecord(SQLModel, table=True):
    __tablename__ = 'user_interest_conditions'
    __table_args__ = (
        Index('ix_interest_conditions_owner_status', 'owner_username', 'status'),
        CheckConstraint("status IN ('active','paused','deleted')", name='ck_interest_condition_status'),
    )
    id: int | None = Field(default=None, primary_key=True)
    owner_username: str = Field(foreign_key='users.username', ondelete='CASCADE')
    name: str
    description: str = ''
    condition_json: str
    schema_version: int = 1
    revision: int = 1
    status: str = 'active'
    created_at: str
    updated_at: str


class InterestArticleMatchRecord(SQLModel, table=True):
    __tablename__ = 'interest_article_matches'
    __table_args__ = (
        UniqueConstraint('condition_id', 'article_id', name='uq_interest_match_condition_article'),
        Index('ix_interest_match_owner_outcome', 'owner_username', 'outcome', 'article_id'),
        CheckConstraint("outcome IN ('core','mention','no_match','unknown')", name='ck_interest_match_outcome'),
    )
    id: int | None = Field(default=None, primary_key=True)
    owner_username: str = Field(foreign_key='users.username', ondelete='CASCADE')
    condition_id: int = Field(foreign_key='user_interest_conditions.id', ondelete='CASCADE')
    condition_revision: int
    article_id: str = Field(foreign_key='articles.id', ondelete='CASCADE')
    fingerprint: str
    matcher_version: str
    outcome: str
    evidence_json: str = '{}'
    updated_at: str


class InterestMatchJobRecord(SQLModel, table=True):
    __tablename__ = 'interest_match_jobs'
    __table_args__ = (Index('ix_interest_match_jobs_due', 'next_attempt_at', 'lease_expires_at'),)
    condition_id: int = Field(primary_key=True, foreign_key='user_interest_conditions.id', ondelete='CASCADE')
    condition_revision: int
    cursor: str = ''
    cycle_cutoff_at: str
    next_attempt_at: str
    lease_token: str = ''
    lease_expires_at: str = ''
    last_error: str = ''
    updated_at: str


class InterestSemanticScanRecord(SQLModel, table=True):
    """One owner's stable paid input, paginated under an explicit contract."""
    __tablename__ = 'interest_semantic_scans'
    __table_args__ = (
        UniqueConstraint('content_identity', 'contract_version', name='uq_interest_scan_content_contract'),
        Index('ix_interest_scan_pending', 'condition_id', 'status', 'page_count', 'updated_at'),
        Index('ix_interest_scan_content', 'content_identity', 'status'),
        CheckConstraint("status IN ('deferred','partial','succeeded','unknown','superseded')", name='ck_interest_scan_status'),
    )
    id: str = Field(primary_key=True)
    content_identity: str
    contract_version: str
    owner_username: str = Field(foreign_key='users.username', ondelete='CASCADE')
    condition_id: int = Field(foreign_key='user_interest_conditions.id', ondelete='CASCADE')
    condition_revision: int
    article_id: str = Field(foreign_key='articles.id', ondelete='CASCADE')
    projection_hash: str
    source_metadata_hash: str
    full_chars: int
    status: str = 'deferred'
    page_count: int = 0
    completed_chunk_ids_json: str = '[]'
    reviewed_ranges_json: str = '[]'
    has_mention: bool = False
    result_json: str = '{}'
    error_code: str = ''
    created_at: str
    updated_at: str


class InterestSemanticRequestRecord(SQLModel, table=True):
    """Commit authorization before HTTP; ambiguous attempts never auto-retry."""
    __tablename__ = 'interest_semantic_requests'
    __table_args__ = (
        UniqueConstraint('request_identity', name='uq_interest_semantic_request_identity'),
        Index('ix_interest_semantic_request_content', 'content_identity', 'status'),
        Index('ix_interest_semantic_request_cycle', 'cycle_id'),
        CheckConstraint("status IN ('authorized','succeeded','unknown','failed_confirmed')", name='ck_interest_semantic_request_status'),
        CheckConstraint('reserved_calls = 1 AND input_bytes >= 0 AND output_token_limit > 0', name='ck_interest_semantic_request_hold'),
    )
    id: str = Field(primary_key=True)
    request_identity: str
    scan_id: str = Field(foreign_key='interest_semantic_scans.id', ondelete='CASCADE')
    content_identity: str
    cycle_id: str
    lease_token: str
    status: str = 'authorized'
    reserved_calls: int = 1
    input_bytes: int
    output_token_limit: int
    payload_json: str
    result_json: str = '{}'
    error_code: str = ''
    authorized_at: str
    completed_at: str = ''
