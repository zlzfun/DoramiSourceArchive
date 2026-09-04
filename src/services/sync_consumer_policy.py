"""Archive Sync v2 receiver-side collection and analysis fence.

Entering v2 consumer mode is a durable deployment decision: it happens before
the first pull starts, so an upgraded inner node cannot collect/analyse public
sources locally while it is still waiting for the producer's first authority
snapshot.  The marker is deliberately separate from per-row authority fields;
no synthetic authority value participates in sync conflict resolution.
"""

from __future__ import annotations

import datetime as dt
import json

from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    MediaAssetRecord,
    SourceConfigRecord,
    SourceStateRecord,
)


V2_CONSUMER_MODE_KEY = "remote_sync:v2_consumer_mode"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def activate_v2_consumer_mode(
    session: Session,
    *,
    reason: str,
    activated_at: str | None = None,
    commit: bool = True,
) -> bool:
    """Persist the receiver fence before scheduling any v2 network work.

    Returns true only when this call created the marker.  Repeated manual or
    scheduled pulls are idempotent and do not rewrite the original audit time.
    """

    record = session.get(AppSettingRecord, V2_CONSUMER_MODE_KEY)
    if record is not None:
        return False
    value = json.dumps(
        {
            "active": True,
            "activated_at": activated_at or _now_iso(),
            "reason": str(reason or "v2_pull"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    session.add(AppSettingRecord(key=V2_CONSUMER_MODE_KEY, value=value))
    if commit:
        session.commit()
    else:
        session.flush()
    return True


def v2_consumer_mode_active(session: Session) -> bool:
    record = session.get(AppSettingRecord, V2_CONSUMER_MODE_KEY)
    if record is None:
        return False
    try:
        payload = json.loads(record.value or "{}")
    except (TypeError, json.JSONDecodeError):
        # A corrupt/legacy marker must fail safe: once the deployment was marked
        # as a receiver, malformed metadata must not reopen public writers.
        return True
    return bool(payload.get("active", True)) if isinstance(payload, dict) else True


def v2_receiver_state_present(session: Session) -> bool:
    """Whether reverting to a v1 writer would violate existing v2 ownership.

    The durable consumer marker is the normal rollout signal.  Per-row
    authority is also checked so an accidentally missing marker cannot make an
    already-synchronised database writable by the legacy v1 path.
    """

    if v2_consumer_mode_active(session):
        return True
    authority_columns = (
        ArticleRecord.analysis_authority_id,
        ArticleAnalysisRecord.authority_id,
        SourceConfigRecord.collection_authority_id,
        SourceStateRecord.authority_id,
        MediaAssetRecord.sync_authority_id,
    )
    for column in authority_columns:
        if session.exec(select(column).where(column != "").limit(1)).first() is not None:
            return True
    taxonomy_authority = session.get(AppSettingRecord, "taxonomy:authority_id")
    if taxonomy_authority is not None and (taxonomy_authority.value or "").strip():
        return True
    return False


def _is_inner_custom_source(source: SourceConfigRecord | None, source_id: str) -> bool:
    return bool(
        source is not None
        and (
            bool((source.owner_username or "").strip())
            or str(source_id or "").startswith("user_rss_")
        )
    )


def local_source_operation_allowed(
    session: Session,
    source_id: str,
    *,
    operation: str,
) -> bool:
    """Single receiver-side policy for local collection and MaaS analysis.

    Outside consumer mode existing behavior is unchanged.  Inside consumer
    mode only enabled inner custom RSS may be collected locally; credentialed
    feeds are still collected here but cannot be sent to MaaS for analysis.
    Existing per-source remote authority always wins independently of the mode.
    """

    if operation not in {"collection", "analysis", "governance"}:
        raise ValueError(f"unsupported local source operation: {operation}")
    source_id = str(source_id or "").strip()
    source = session.get(SourceConfigRecord, source_id) if source_id else None
    if source is not None and (source.collection_authority_id or "").strip():
        return False
    if not v2_consumer_mode_active(session):
        return True
    if not _is_inner_custom_source(source, source_id):
        return False

    if operation == "governance":
        return True

    # Import locally to keep this policy module independent of the user-source
    # service's write paths while sharing its credential and feature switches.
    from services import user_sources

    if not source.is_active or not user_sources.feature_enabled(session):
        return False
    # Signed/private custom RSS remains an inner collection responsibility; it
    # is only forbidden from leaving the node through MaaS analysis.
    if operation == "analysis" and (
        user_sources.source_is_credentialed(source)
        or not source.ai_analysis_enabled
    ):
        return False
    return True
