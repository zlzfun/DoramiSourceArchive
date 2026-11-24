"""Node-local review and notification state; never part of Archive Sync."""
from typing import Optional
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class ReviewProposalRecord(SQLModel, table=True):
    __tablename__ = 'review_proposals'
    __table_args__ = (
        UniqueConstraint('identity', name='uq_review_proposal_identity'),
        Index('ix_review_proposals_status_due', 'status', 'next_review_at'),
        CheckConstraint("status IN ('pending','deferred','rejected','approved','merged','split','resolved')", name='ck_review_proposal_status'),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    identity: str
    member_ids_json: str = '[]'
    canonical_name: str
    kind: str
    definition: str = ''
    relation: str = 'unknown'
    target_tag_id: Optional[int] = Field(default=None, foreign_key='cms_tags.id', ondelete='SET NULL')
    recommendation: str = '继续观察'
    reason: str = ''
    risks_json: str = '[]'
    status: str = 'pending'
    revision: int = 1
    taxonomy_revision: int = 0
    evidence_hash: str = ''
    evidence_json: str = '{}'
    priority: int = 0
    next_review_at: str = ''
    decided_by: str = ''
    decision_reason: str = ''
    draft_tag_id: Optional[int] = Field(default=None, foreign_key='cms_tags.id', ondelete='SET NULL')
    retag_job_id: Optional[int] = Field(default=None, foreign_key='tag_retag_jobs.id', ondelete='SET NULL')
    created_at: str
    updated_at: str


class ReviewProposalRevisionRecord(SQLModel, table=True):
    __tablename__ = 'review_proposal_revisions'
    proposal_id: int = Field(primary_key=True, foreign_key='review_proposals.id', ondelete='CASCADE')
    revision: int = Field(primary_key=True)
    snapshot_json: str
    actor_id: str = ''
    action: str
    reason: str = ''
    created_at: str


class ReviewInboxRecord(SQLModel, table=True):
    __tablename__ = 'review_inbox'
    recipient: str = Field(primary_key=True)
    proposal_id: int = Field(primary_key=True, foreign_key='review_proposals.id', ondelete='CASCADE')
    revision: int
    last_notified_revision: int = 0
    status: str = 'pending'
    updated_at: str


class ReviewNotificationOutboxRecord(SQLModel, table=True):
    __tablename__ = 'review_notification_outbox'
    __table_args__ = (
        UniqueConstraint('recipient','channel','proposal_id','proposal_revision',name='uq_review_outbox_revision'),
        Index('ix_review_outbox_pending','status','created_at'),
        CheckConstraint("status IN ('pending','sending','accepted','failed','unknown','cancelled')",name='ck_review_outbox_status'),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    recipient: str = 'taxonomy_admin'
    channel: str = 'feishu'
    proposal_id: int = Field(foreign_key='review_proposals.id',ondelete='CASCADE')
    proposal_revision: int
    status: str = 'pending'
    batch_id: str = ''
    created_at: str
    updated_at: str


class ReviewNotificationBatchRecord(SQLModel, table=True):
    __tablename__ = 'review_notification_batches'
    __table_args__ = (
        UniqueConstraint('recipient','channel','digest_date',name='uq_review_batch_daily'),
        CheckConstraint("status IN ('pending','sending','accepted','failed','unknown','cancelled')",name='ck_review_batch_status'),
    )
    id: str = Field(primary_key=True)
    recipient: str = 'taxonomy_admin'
    channel: str = 'feishu'
    digest_date: str
    destination_hash: str
    outbox_ids_json: str = '[]'
    status: str = 'pending'
    attempt_count: int = 0
    next_attempt_at: str = ''
    lease_token: str = ''
    lease_expires_at: str = ''
    error_code: str = ''
    created_at: str
    updated_at: str


class ReviewNotificationAttemptRecord(SQLModel, table=True):
    __tablename__ = 'review_notification_attempts'
    batch_id: str = Field(primary_key=True, foreign_key='review_notification_batches.id',ondelete='CASCADE')
    attempt: int = Field(primary_key=True)
    status: str
    error_code: str = ''
    http_status: Optional[int] = None
    provider_code: Optional[int] = None
    started_at: str
    ended_at: str = ''


class ReviewProposalMemberRecord(SQLModel, table=True):
    __tablename__ = 'review_proposal_members'
    candidate_id: int = Field(primary_key=True, foreign_key='cms_tag_candidates.id',ondelete='CASCADE')
    proposal_id: int = Field(foreign_key='review_proposals.id',ondelete='CASCADE',index=True)
