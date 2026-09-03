"""clean up data written by intermediate analysis/digest revisions

Revision ID: b5c1e3f7a9d2
Revises: a4b9d2e6f1c3
Create Date: 2026-09-03

This revision is intentionally a no-op for a production database upgraded
directly from main: the affected tables are introduced empty in the same
migration chain.  It repairs development/gray databases that ran an earlier
PR revision and produced work before the lifecycle hardening migration.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c1e3f7a9d2"
down_revision: Union[str, Sequence[str], None] = "a4b9d2e6f1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SUPERSEDED_JOB_ERROR = "superseded while enforcing one active full-analysis job"
_CANCELLED_ITEM_ERROR = "job_superseded_during_migration"


def _cleanup_superseded_backfills(bind: sa.engine.Connection, stamp: str) -> None:
    stale_rows = bind.execute(sa.text(
        "SELECT item.id, item.article_id, item.status "
        "FROM tag_retag_job_items AS item "
        "JOIN tag_retag_jobs AS job ON job.id = item.job_id "
        "WHERE job.operation = 'full_analysis' "
        "AND job.status = 'cancelled' "
        "AND job.last_error = :job_error "
        "AND item.status IN ('pending','queued')"
    ), {"job_error": _SUPERSEDED_JOB_ERROR}).mappings().all()

    queued_article_ids = {
        str(row["article_id"])
        for row in stale_rows
        if row["status"] == "queued" and row["article_id"]
    }
    if queued_article_ids:
        live_rows = bind.execute(sa.text(
            "SELECT DISTINCT item.article_id "
            "FROM tag_retag_job_items AS item "
            "JOIN tag_retag_jobs AS job ON job.id = item.job_id "
            "WHERE item.article_id IS NOT NULL "
            "AND item.status IN ('pending','queued') "
            "AND job.operation = 'full_analysis' "
            "AND job.status IN ('queued','running','paused')"
        )).all()
        live_article_ids = {str(row[0]) for row in live_rows if row[0]}
        revoke_ids = sorted(queued_article_ids - live_article_ids)
        if revoke_ids:
            ids_param = sa.bindparam("article_ids", expanding=True)
            bind.execute(
                sa.text(
                    "UPDATE article_analysis_attempts "
                    "SET status = 'skipped', ended_at = :stamp, error = :reason "
                    "WHERE status = 'running' AND article_id IN :article_ids"
                ).bindparams(ids_param),
                {
                    "article_ids": revoke_ids,
                    "stamp": stamp,
                    "reason": _CANCELLED_ITEM_ERROR,
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE article_analyses "
                    "SET status = CASE "
                    "WHEN quality_score IS NOT NULL AND analyzed_at IS NOT NULL "
                    "THEN 'succeeded' ELSE 'skipped' END, "
                    "started_at = NULL, next_attempt_at = NULL, "
                    "lease_owner = NULL, lease_expires_at = NULL, "
                    "last_error = :reason, updated_at = :stamp "
                    "WHERE status IN ('pending','running') "
                    "AND article_id IN :article_ids"
                ).bindparams(ids_param),
                {
                    "article_ids": revoke_ids,
                    "stamp": stamp,
                    "reason": _CANCELLED_ITEM_ERROR,
                },
            )

    bind.execute(sa.text(
        "UPDATE tag_retag_job_items "
        "SET status = 'skipped', last_error = :reason, "
        "completed_at = :stamp, updated_at = :stamp "
        "WHERE id IN ("
        "SELECT item.id FROM tag_retag_job_items AS item "
        "JOIN tag_retag_jobs AS job ON job.id = item.job_id "
        "WHERE job.operation = 'full_analysis' "
        "AND job.status = 'cancelled' "
        "AND job.last_error = :job_error "
        "AND item.status IN ('pending','queued')"
        ")"
    ), {
        "job_error": _SUPERSEDED_JOB_ERROR,
        "reason": _CANCELLED_ITEM_ERROR,
        "stamp": stamp,
    })


def _cleanup_personal_digest_snapshots(bind: sa.engine.Connection, stamp: str) -> None:
    bind.execute(sa.text(
        "UPDATE personal_digest_editions "
        "SET status = 'superseded', generation_token = NULL, "
        "generation_lease_expires_at = NULL, updated_at = :stamp "
        "WHERE status IN ('pending','generating')"
    ), {"stamp": stamp})

    updates: list[dict[str, object]] = []
    for row in bind.execute(sa.text(
        "SELECT id, snapshot_json FROM personal_digest_items"
    )).mappings():
        try:
            snapshot = json.loads(str(row["snapshot_json"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict) or "content" not in snapshot:
            continue
        snapshot.pop("content", None)
        updates.append({
            "item_id": int(row["id"]),
            "snapshot_json": json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        })
    if updates:
        bind.execute(sa.text(
            "UPDATE personal_digest_items "
            "SET snapshot_json = :snapshot_json WHERE id = :item_id"
        ), updates)


def upgrade() -> None:
    bind = op.get_bind()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    _cleanup_superseded_backfills(bind, stamp)
    _cleanup_personal_digest_snapshots(bind, stamp)


def downgrade() -> None:
    # Privacy-minimized snapshot content and superseded lifecycle state cannot
    # be reconstructed safely.  Schema downgrade remains a no-op.
    pass
