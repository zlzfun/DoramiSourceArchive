"""Publish successful normalized transcripts missing their current pointer.

Revision ID: f3c8a1d6e205
Revises: e1b7c4d9a260
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c8a1d6e205"
down_revision: Union[str, Sequence[str], None] = "e1b7c4d9a260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # The old materializer committed immutable normalized artifacts and their
    # successful attempts, but omitted the Reader publication pointer. Publish
    # the newest proven local output only. Existing local or remote authority
    # slots are left untouched.
    bind.execute(sa.text(
        "INSERT INTO podcast_text_publications "
        "(identity,episode_id,kind,artifact_id,status,authority_id,published_at,"
        "unpublished_at,updated_at) "
        "SELECT artifact.episode_id || ':' || 'normalized_transcript', "
        "artifact.episode_id, 'normalized_transcript', artifact.id, 'published', '', "
        "artifact.created_at, NULL, artifact.created_at "
        "FROM podcast_text_artifacts artifact "
        "JOIN podcast_stage_attempts attempt "
        "ON attempt.id = artifact.producing_attempt_id "
        "AND attempt.processing_id = artifact.processing_id "
        "WHERE artifact.kind = 'normalized_transcript' "
        "AND artifact.authority_id = '' "
        "AND attempt.stage = 'asr' "
        "AND attempt.submission_state = 'succeeded' "
        "AND attempt.output_artifact_id = artifact.id "
        "AND attempt.output_artifact_kind = 'normalized_transcript' "
        "AND attempt.output_hash = artifact.content_hash "
        "AND NOT EXISTS ("
        "SELECT 1 FROM podcast_text_publications publication "
        "WHERE publication.identity = artifact.episode_id || ':' || 'normalized_transcript') "
        "AND NOT EXISTS ("
        "SELECT 1 FROM podcast_text_artifacts newer "
        "JOIN podcast_stage_attempts newer_attempt "
        "ON newer_attempt.id = newer.producing_attempt_id "
        "AND newer_attempt.processing_id = newer.processing_id "
        "WHERE newer.episode_id = artifact.episode_id "
        "AND newer.kind = 'normalized_transcript' "
        "AND newer.authority_id = '' "
        "AND newer.version > artifact.version "
        "AND newer_attempt.stage = 'asr' "
        "AND newer_attempt.submission_state = 'succeeded' "
        "AND newer_attempt.output_artifact_id = newer.id "
        "AND newer_attempt.output_artifact_kind = 'normalized_transcript' "
        "AND newer_attempt.output_hash = newer.content_hash)"
    ))


def downgrade() -> None:
    # The parent revision irreversibly retired source-audio blobs. Refuse here
    # before Alembic moves the version marker and only then discovers that
    # lower irreversible boundary.
    raise RuntimeError(
        "Podcast source audio blobs were intentionally retired; restore the "
        "pre-upgrade database and CAS backup to downgrade"
    )
