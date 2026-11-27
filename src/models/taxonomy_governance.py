"""Node-local candidate maintenance state; excluded from Archive Sync."""
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class CandidateGovernanceStateRecord(SQLModel, table=True):
    __tablename__ = "candidate_governance_state"
    __table_args__ = (Index("ix_candidate_governance_due", "lane", "next_evaluation_at"),)

    candidate_id: int = Field(primary_key=True, foreign_key="cms_tag_candidates.id", ondelete="CASCADE")
    lane: str = "new"
    evidence_fingerprint: str = ""
    evidence_revision: int = 0
    stats_at: str = ""
    last_evaluated_at: str = ""
    next_evaluation_at: str = ""
    rule_version: str = ""
    lease_token: str = ""
    lease_until: str = ""
    failure_count: int = 0
    last_error_code: str = ""
    independent_evidence_json: str = "{}"
    created_at: str
    updated_at: str


class CandidateGovernanceActionRecord(SQLModel, table=True):
    __tablename__ = "candidate_governance_actions"
    __table_args__ = (UniqueConstraint("action_key", name="uq_candidate_governance_action_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    action_key: str
    candidate_id: int = Field(foreign_key="cms_tag_candidates.id", ondelete="CASCADE")
    target_tag_id: int = Field(foreign_key="cms_tags.id", ondelete="CASCADE")
    event_id: Optional[int] = Field(default=None, foreign_key="cms_tag_events.id", ondelete="SET NULL")
    retag_job_id: Optional[int] = Field(default=None, foreign_key="tag_retag_jobs.id", ondelete="SET NULL")
    evidence_fingerprint: str
    taxonomy_revision: int
    rule_version: str
    created_at: str
