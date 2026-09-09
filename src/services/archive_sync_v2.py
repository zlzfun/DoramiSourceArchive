"""Archive Sync v2: scoped, revisioned replication between Dorami deployments.

The v1 article JSONL contract remains available unchanged.  V2 deliberately
replicates *records*, not SQLite files: reader-owned users, subscriptions,
interests, briefs and manual/rule tag overlays can therefore never be replaced
by the external deployment.

Each page is a checksum-protected transaction.  A manifest pins a producer
``authority_id`` and a snapshot watermark; consumers advance the per-stream
checkpoint only after every line in the page has validated and committed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import and_, delete, exists, or_, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import (
    DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
    DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES,
    DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS,
)
from models.db import (
    AppSettingRecord,
    ArchiveSyncClockRecord,
    ArchiveSyncEntityStateRecord,
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    MediaAssetRecord,
    PODCAST_TEXT_ARTIFACT_KINDS,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    RemoteCandidateEvidenceRecord,
    SourceConfigRecord,
    SourceStateRecord,
    TaxonomyVersionRecord,
)
from services.article_analysis import compute_content_hash
from services.media_store import extract_image_urls, validate_synced_image
from services.podcast_artifacts import withdraw_digest_audio_for_script_change
from services.podcast_text_limits import (
    PodcastTextLimitExceeded,
    validate_text_artifact,
)
from services.taxonomy import (
    TAXONOMY_AUTHORITY_ID_KEY,
    TAXONOMY_SYNC_REVISION_KEY,
    current_taxonomy_sync_revision,
    normalize_label,
    reconcile_synced_taxonomy_candidates,
)


SCHEMA_VERSION = "archive-sync-v3"
TRANSACTION_REVISION_CAPABILITY = "transaction-revision-tombstone-v1"
AUTHORITATIVE_PRESENCE_CAPABILITY = "authoritative-presence-v1"
PODCAST_TEXT_PUBLICATIONS_CAPABILITY = "podcast-text-publications-v1"
PODCAST_AUDIO_PUBLICATIONS_CAPABILITY = "podcast-audio-publications-v1"
REQUIRED_CAPABILITIES = (
    TRANSACTION_REVISION_CAPABILITY,
    AUTHORITATIVE_PRESENCE_CAPABILITY,
)
ADVERTISED_CAPABILITIES = (
    *REQUIRED_CAPABILITIES,
    PODCAST_TEXT_PUBLICATIONS_CAPABILITY,
    PODCAST_AUDIO_PUBLICATIONS_CAPABILITY,
)
# Compatibility alias for integrations that display the producer's advertised
# feature set. Capability validation must use REQUIRED_CAPABILITIES instead.
CAPABILITIES = ADVERTISED_CAPABILITIES
AUTHORITY_ID_KEY = "archive_sync:v2:authority_id"
CANDIDATE_STAGING_KEY_PREFIX = "archive_sync:v2:candidate_staging:"
TAXONOMY_SNAPSHOT_DIGEST_KEY = "archive_sync:v2:taxonomy_snapshot_digest"
STREAMS = (
    "sources",
    "taxonomy",
    "articles",
    "analyses",
    "media",
    "source_states",
    "podcast_texts",
    "podcast_audio",
)
_USER_SOURCE_PREFIX = "user_rss_"
_SAFE_MEDIA_EXT = re.compile(r"^\.[a-z0-9]{1,10}$", re.IGNORECASE)


class SyncV2Error(ValueError):
    """The page cannot be safely applied; its checkpoint must not advance."""


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def producer_authority_id(engine: Engine) -> str:
    """Return a stable producer identity, independent from runtime role/name."""

    configured = os.getenv("DORAMI_ARCHIVE_AUTHORITY_ID", "").strip()
    if configured:
        return configured
    with Session(engine) as session:
        row = session.get(AppSettingRecord, AUTHORITY_ID_KEY)
        if row and row.value:
            return row.value
        value = f"dorami-{uuid.uuid4()}"
        session.add(AppSettingRecord(key=AUTHORITY_ID_KEY, value=value))
        session.commit()
        return value


def _encode_cursor(revision: str, identity: str) -> str:
    raw = _canonical([revision, identity]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    try:
        padded = value + "=" * (-len(value) % 4)
        revision, identity = json.loads(
            base64.urlsafe_b64decode(padded).decode("utf-8")
        )
        return str(revision), str(identity)
    except Exception as exc:  # noqa: BLE001 - public contract validation boundary
        raise SyncV2Error("invalid v2 cursor") from exc


def _line(
    stream: str,
    payload: dict[str, Any],
    *,
    revision: str,
    identity: str,
    operation: str = "upsert",
) -> dict[str, Any]:
    return {
        "kind": stream.rstrip("s"),
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "identity": identity,
        "operation": operation,
        "checksum": checksum(payload),
        "payload": payload,
    }


def _manifest(
    stream: str,
    authority_id: str,
    snapshot: str,
    after: str,
    rows: list[dict[str, Any]],
    *,
    complete: bool,
    since: str = "",
) -> dict[str, Any]:
    next_cursor = after
    if rows:
        next_cursor = _encode_cursor(rows[-1]["revision"], rows[-1]["identity"])
    manifest = {
        "kind": "manifest",
        "schema_version": SCHEMA_VERSION,
        "stream": stream,
        "authority_id": authority_id,
        "snapshot": snapshot,
        "since": since,
        "after": after,
        "next_cursor": next_cursor,
        "complete": complete,
        "count": len(rows),
        "generated_at": _now_iso(),
        "capabilities": list(ADVERTISED_CAPABILITIES),
    }
    return manifest


def encode_page(manifest: dict[str, Any], rows: Iterable[dict[str, Any]]) -> str:
    return "\n".join(_canonical(item) for item in (manifest, *rows)) + "\n"


def _public_source(source_id: str) -> bool:
    return not (source_id or "").startswith(_USER_SOURCE_PREFIX)


_SOURCE_FIELDS = (
    "source_id",
    "name",
    "source_type",
    "url",
    "category",
    "description",
    "source_owner",
    "source_brand",
    "source_scope",
    "source_channel",
    "base_url",
    "provenance_tier",
    "content_tags_json",
    "signal_strength",
    "noise_risk",
    "fetch_reliability",
    "ai_analysis_enabled",
    "is_active",
    "fetch_interval_minutes",
    "created_at",
    "updated_at",
)


def _source_payload(row: SourceConfigRecord) -> dict[str, Any]:
    # No fetcher/cron/params: the receiving all-role node must not start collecting
    # platform sources, and params may contain producer-only credentials.
    return {field: getattr(row, field) for field in _SOURCE_FIELDS}


def _article_payload(row: ArticleRecord, authority_id: str) -> dict[str, Any]:
    from api.routers.archive_sync import archive_article_payload

    payload = archive_article_payload(row)
    payload["analysis_authority_id"] = authority_id
    return payload


def _taxonomy_payload(session: Session, row: CmsTagRecord) -> dict[str, Any]:
    aliases = session.exec(
        select(CmsTagAliasRecord)
        .where(CmsTagAliasRecord.tag_id == row.id)
        .order_by(
            CmsTagAliasRecord.kind,
            CmsTagAliasRecord.locale,
            CmsTagAliasRecord.normalized_alias,
            CmsTagAliasRecord.id,
        )
    ).all()
    parent = session.get(CmsTagRecord, row.parent_id) if row.parent_id else None
    replacement = (
        session.get(CmsTagRecord, row.replacement_id) if row.replacement_id else None
    )
    return {
        "code": row.code,
        "kind": row.kind,
        "name_zh": row.name_zh,
        "name_en": row.name_en,
        "normalized_name": row.normalized_name,
        "description": row.description,
        "prompt_description": row.prompt_description,
        "status": row.status,
        "replacement_code": replacement.code if replacement else None,
        "parent_code": parent.code if parent else None,
        "entity_type": row.entity_type,
        "external_key": row.external_key,
        "user_selectable": row.user_selectable,
        "filterable": row.filterable,
        "recommendable": row.recommendable,
        "activation_mode": row.activation_mode,
        "taxonomy_version": row.taxonomy_version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "aliases": [
            {
                "kind": alias.kind,
                "locale": alias.locale,
                "alias": alias.alias,
                "normalized_alias": alias.normalized_alias,
                "alias_type": alias.alias_type,
                "created_at": alias.created_at,
                "updated_at": alias.updated_at,
            }
            for alias in aliases
        ],
    }


def _analysis_payload(session: Session, row: ArticleAnalysisRecord) -> dict[str, Any]:
    assignments = session.exec(
        select(ArticleTagAssignmentRecord, CmsTagRecord)
        .join(CmsTagRecord, CmsTagRecord.id == ArticleTagAssignmentRecord.tag_id)
        .where(
            ArticleTagAssignmentRecord.article_id == row.article_id,
            ArticleTagAssignmentRecord.assignment_source == "llm",
        )
    ).all()
    scalar_fields = (
        "article_id",
        "status",
        "tagging_status",
        "quality_score",
        "dimension_scores_json",
        "score_reason",
        "summary",
        "content_genre",
        "content_features_json",
        "entities_json",
        "display_tags_json",
        "content_hash",
        "analysis_basis",
        "analysis_input_hash",
        "transcript_artifact_id",
        "analysis_diagnostics_json",
        "model_name",
        "prompt_version",
        "scoring_version",
        "taxonomy_version",
        "attempt_count",
        "analyzed_at",
        "tagged_at",
        "created_at",
        "updated_at",
    )
    payload = {field: getattr(row, field) for field in scalar_fields}
    payload["primary_tag_code"] = None
    payload["assignments"] = []
    for assignment, tag in assignments:
        item = {
            "code": tag.code,
            "kind": assignment.tag_kind,
            "is_primary": assignment.is_primary,
            "relevance": assignment.relevance,
            "prompt_version": assignment.prompt_version,
            "taxonomy_version": assignment.taxonomy_version,
            "created_at": assignment.created_at,
            "updated_at": assignment.updated_at,
        }
        payload["assignments"].append(item)
        if assignment.is_primary:
            payload["primary_tag_code"] = tag.code
    return payload


def _media_references(session: Session, snapshot: int) -> dict[str, int]:
    """Map current public image URLs to their article transaction revision."""

    urls: dict[str, int] = {}
    articles = session.exec(
        select(ArticleRecord, ArchiveSyncEntityStateRecord.revision)
        .join(
            ArchiveSyncEntityStateRecord,
            and_(
                ArchiveSyncEntityStateRecord.stream == "articles",
                ArchiveSyncEntityStateRecord.identity == ArticleRecord.id,
            ),
        )
        .outerjoin(
            SourceConfigRecord, SourceConfigRecord.source_id == ArticleRecord.source_id
        )
        .where(
            ArchiveSyncEntityStateRecord.authority_id == "",
            ArchiveSyncEntityStateRecord.operation == "upsert",
            ArchiveSyncEntityStateRecord.revision <= snapshot,
            ~ArticleRecord.source_id.startswith(_USER_SOURCE_PREFIX, autoescape=True),
            ArticleRecord.analysis_authority_id == "",
            or_(
                SourceConfigRecord.source_id.is_(None),
                SourceConfigRecord.owner_username == "",
            ),
            or_(
                SourceConfigRecord.source_id.is_(None),
                SourceConfigRecord.collection_authority_id == "",
            ),
        )
    ).all()
    for article, revision in articles:
        for url in extract_image_urls(article.content, article.extensions_json):
            urls[url] = max(urls.get(url, 0), int(revision))
    return urls


def is_public_media_reference(session: Session, url: str) -> bool:
    """Whether a cached URL belongs to the public article replication scope."""

    target = str(url or "").strip()
    if not target:
        return False
    # Narrow in SQLite first instead of loading/parsing every article body for
    # every binary request. Exact extraction on the tiny candidate set keeps
    # the allow decision faithful while avoiding O(M × N) Python memory churn.
    cursor_id = ""
    while True:
        candidates = session.exec(
            select(ArticleRecord)
            .outerjoin(
                SourceConfigRecord,
                SourceConfigRecord.source_id == ArticleRecord.source_id,
            )
            .where(
                ArticleRecord.id > cursor_id,
                ~ArticleRecord.source_id.startswith(
                    _USER_SOURCE_PREFIX, autoescape=True
                ),
                ArticleRecord.analysis_authority_id == "",
                or_(
                    SourceConfigRecord.source_id.is_(None),
                    SourceConfigRecord.owner_username == "",
                ),
                or_(
                    SourceConfigRecord.source_id.is_(None),
                    SourceConfigRecord.collection_authority_id == "",
                ),
                or_(
                    ArticleRecord.content.contains(target, autoescape=True),
                    ArticleRecord.extensions_json.contains(target, autoescape=True),
                ),
            )
            .order_by(ArticleRecord.id)
            .limit(200)
        ).all()
        if any(
            target in extract_image_urls(row.content, row.extensions_json)
            for row in candidates
        ):
            return True
        if len(candidates) < 200:
            return False
        cursor_id = candidates[-1].id


def _canonical_revision(value: Any, *, field: str, allow_empty: bool = False) -> int:
    raw = str(value or "")
    if allow_empty and not raw:
        return -1
    try:
        revision = int(raw)
    except (TypeError, ValueError) as exc:
        raise SyncV2Error(f"{field} must be a canonical non-negative integer") from exc
    if revision < 0 or str(revision) != raw:
        raise SyncV2Error(f"{field} must be a canonical non-negative integer")
    return revision


def _tombstone_payload(stream: str, identity: str) -> dict[str, Any]:
    field = {
        "sources": "source_id",
        "articles": "id",
        "analyses": "article_id",
        "source_states": "source_id",
        "podcast_texts": "identity",
        "podcast_audio": "id",
    }[stream]
    return {field: identity, "tombstone": True}


def _source_state_payload(row: SourceStateRecord) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "fetcher_id": row.fetcher_id,
        "content_type": row.content_type,
        "status": row.status,
        "last_started_at": row.last_started_at,
        "last_completed_at": row.last_completed_at,
        "last_success_at": row.last_success_at,
        "last_failure_at": row.last_failure_at,
        "consecutive_failures": row.consecutive_failures,
        "total_runs": row.total_runs,
        "success_runs": row.success_runs,
        "failed_runs": row.failed_runs,
        "latest_fetched_count": row.latest_fetched_count,
        "latest_saved_count": row.latest_saved_count,
        "latest_skipped_count": row.latest_skipped_count,
        "latest_error_type": row.latest_error_type,
        "latest_error_message": row.latest_error_message,
        "updated_at": row.updated_at,
    }


def _podcast_text_payload(
    publication: PodcastTextPublicationRecord,
    artifact: PodcastTextArtifactRecord,
) -> dict[str, Any]:
    """Serialize one current public pointer and its immutable text value."""

    return {
        "identity": publication.identity,
        "episode_id": publication.episode_id,
        "kind": publication.kind,
        "artifact_id": publication.artifact_id,
        "status": "published",
        "published_at": publication.published_at,
        "updated_at": publication.updated_at,
        "artifact": {
            "id": artifact.id,
            "episode_id": artifact.episode_id,
            "kind": artifact.kind,
            "version": artifact.version,
            "content_hash": artifact.content_hash,
            "inline_text": artifact.inline_text,
            "language": artifact.language,
            "source_artifact_id": artifact.source_artifact_id,
            "source_content_hash": artifact.source_content_hash,
            "provenance_json": artifact.provenance_json,
            "created_at": artifact.created_at,
        },
    }


def _podcast_audio_payload(record: PodcastArtifactRecord) -> dict[str, Any]:
    """Serialize one published external digest-audio registry row."""

    return {
        "id": record.id,
        "episode_id": record.episode_id,
        "kind": "digest_audio_zh",
        "content_hash": record.content_hash,
        "mime": record.mime,
        "ext": record.ext,
        "size_bytes": record.size_bytes,
        "duration_seconds": record.duration_seconds,
        "status": "published",
        "provenance": record.provenance,
        "narration_artifact_id": record.narration_artifact_id,
        "narration_content_hash": record.narration_content_hash,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "published_at": record.published_at,
    }


def _validated_podcast_audio_payload(data: dict[str, Any]) -> dict[str, Any]:
    artifact_id = str(data.get("id") or "")
    episode_id = str(data.get("episode_id") or "")
    content_hash = _validate_sha256(data.get("content_hash"), field="content_hash")
    narration_hash = _validate_sha256(
        data.get("narration_content_hash"), field="narration_content_hash"
    )
    narration_id = str(data.get("narration_artifact_id") or "")
    mime = str(data.get("mime") or "").strip().lower()
    expected_ext = {
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
    }.get(mime)
    size_bytes = data.get("size_bytes")
    duration = data.get("duration_seconds")
    if not artifact_id or not episode_id:
        raise SyncV2Error("podcast audio id and episode_id are required")
    if data.get("kind") != "digest_audio_zh" or data.get("status") != "published":
        raise SyncV2Error("podcast audio must be a published digest_audio_zh")
    if expected_ext is None or data.get("ext") != expected_ext:
        raise SyncV2Error("podcast audio MIME and extension are inconsistent")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
    ):
        raise SyncV2Error("podcast audio size_bytes must be positive")
    if duration is not None and (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration < 0
    ):
        raise SyncV2Error("podcast audio duration_seconds is invalid")
    if not narration_id or not narration_hash:
        raise SyncV2Error("podcast audio narration dependency is required")
    for field in ("created_at", "updated_at", "published_at"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise SyncV2Error(f"podcast audio {field} is required")
    return {
        **data,
        "id": artifact_id,
        "episode_id": episode_id,
        "content_hash": content_hash,
        "narration_artifact_id": narration_id,
        "narration_content_hash": narration_hash,
        "mime": mime,
        "ext": expected_ext,
        "size_bytes": size_bytes,
        "duration_seconds": float(duration) if duration is not None else None,
    }


def _upsert_payload(
    session: Session,
    stream: str,
    identity: str,
    authority_id: str,
    *,
    podcast_text_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    podcast_text_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
) -> Optional[dict[str, Any]]:
    if stream == "sources":
        record = session.get(SourceConfigRecord, identity)
        if record is None or record.owner_username or record.collection_authority_id:
            return None
        return _source_payload(record)
    if stream == "articles":
        record = session.get(ArticleRecord, identity)
        if (
            record is None
            or record.analysis_authority_id
            or not _public_source(record.source_id)
        ):
            return None
        source = session.get(SourceConfigRecord, record.source_id)
        if source is not None and (
            source.owner_username or source.collection_authority_id
        ):
            return None
        return _article_payload(record, authority_id)
    if stream == "analyses":
        record = session.get(ArticleAnalysisRecord, identity)
        article = session.get(ArticleRecord, identity)
        if (
            record is None
            or article is None
            or record.authority_id
            or article.analysis_authority_id
            or not _public_source(article.source_id)
        ):
            return None
        source = session.get(SourceConfigRecord, article.source_id)
        if source is not None and (
            source.owner_username or source.collection_authority_id
        ):
            return None
        return _analysis_payload(session, record)
    if stream == "source_states":
        record = session.get(SourceStateRecord, identity)
        if (
            record is None
            or record.authority_id
            or not _public_source(record.source_id)
        ):
            return None
        source = session.get(SourceConfigRecord, record.source_id)
        if source is not None and (
            source.owner_username or source.collection_authority_id
        ):
            return None
        return _source_state_payload(record)
    if stream == "podcast_texts":
        publication = session.get(PodcastTextPublicationRecord, identity)
        if (
            publication is None
            or publication.authority_id
            or publication.status != "published"
            or not publication.published_at
        ):
            return None
        artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
        article = session.get(ArticleRecord, publication.episode_id)
        if (
            artifact is None
            or artifact.authority_id
            or artifact.episode_id != publication.episode_id
            or artifact.kind != publication.kind
            or article is None
            or article.content_type != "podcast_episode"
            or article.analysis_authority_id
            or not _public_source(article.source_id)
        ):
            return None
        source = session.get(SourceConfigRecord, article.source_id)
        if source is not None and (
            source.owner_username or source.collection_authority_id
        ):
            return None
        payload = _podcast_text_payload(publication, artifact)
        _validated_podcast_text_payload(
            payload,
            max_bytes=podcast_text_max_bytes,
            max_chars=podcast_text_max_chars,
        )
        return payload
    if stream == "podcast_audio":
        record = session.get(PodcastArtifactRecord, identity)
        if (
            record is None
            or record.kind != "digest_audio_zh"
            or record.status != "published"
            or record.authority_id
            or not record.published_at
        ):
            return None
        article = session.get(ArticleRecord, record.episode_id)
        narration = session.get(PodcastTextArtifactRecord, record.narration_artifact_id)
        if (
            article is None
            or article.content_type != "podcast_episode"
            or article.analysis_authority_id
            or not _public_source(article.source_id)
            or narration is None
            or narration.episode_id != record.episode_id
            or narration.kind != "narration_script_zh"
            or narration.content_hash != record.narration_content_hash
        ):
            return None
        source = session.get(SourceConfigRecord, article.source_id)
        if source is not None and (
            source.owner_username or source.collection_authority_id
        ):
            return None
        return _validated_podcast_audio_payload(_podcast_audio_payload(record))
    raise SyncV2Error(f"unsupported entity-state stream: {stream}")


def _state_after_filter(
    query, *, since_revision: int, cursor_revision: int, cursor_id: str
):
    if cursor_revision >= 0:
        return query.where(
            or_(
            ArchiveSyncEntityStateRecord.revision > cursor_revision,
            and_(
                ArchiveSyncEntityStateRecord.revision == cursor_revision,
                ArchiveSyncEntityStateRecord.identity > cursor_id,
            ),
            )
        )
    if since_revision >= 0:
        return query.where(ArchiveSyncEntityStateRecord.revision > since_revision)
    return query


def _export_media_rows(
    session: Session,
    *,
    snapshot: int,
    since_revision: int,
    cursor_revision: int,
    cursor_id: str,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    references = _media_references(session, snapshot)
    if not references:
        return [], True
    media_records = session.exec(
        select(MediaAssetRecord, ArchiveSyncEntityStateRecord)
        .join(
            ArchiveSyncEntityStateRecord,
            and_(
                ArchiveSyncEntityStateRecord.stream == "media",
                ArchiveSyncEntityStateRecord.identity == MediaAssetRecord.url_hash,
            ),
        )
        .where(
            ArchiveSyncEntityStateRecord.authority_id == "",
            ArchiveSyncEntityStateRecord.operation == "upsert",
            MediaAssetRecord.sync_authority_id == "",
            MediaAssetRecord.status == "cached",
            MediaAssetRecord.url.in_(list(references)),
        )
    ).all()
    candidates: list[tuple[int, str, MediaAssetRecord]] = []
    for record, state in media_records:
        revision = max(int(state.revision), int(references.get(record.url, 0)))
        key = (revision, record.url_hash)
        if revision > snapshot:
            continue
        if cursor_revision >= 0:
            if key <= (cursor_revision, cursor_id):
                continue
        elif since_revision >= 0 and revision <= since_revision:
            continue
        candidates.append((revision, record.url_hash, record))
    candidates.sort(key=lambda item: (item[0], item[1]))
    complete = len(candidates) <= limit
    rows = []
    for revision, identity, record in candidates[:limit]:
        payload = {
            "url_hash": record.url_hash,
            "url": record.url,
            "content_hash": record.content_hash,
            "mime": record.mime,
            "ext": record.ext,
            "size_bytes": record.size_bytes,
            "fetched_at": record.fetched_at,
            "updated_at": record.updated_at,
        }
        rows.append(_line("media", payload, revision=str(revision), identity=identity))
    return rows, complete


def export_page(
    engine: Engine,
    stream: str,
    *,
    snapshot: str = "",
    since: str = "",
    after: str = "",
    limit: int = 1000,
    podcast_text_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    podcast_text_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
    podcast_text_page_max_bytes: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES,
    podcast_text_page_max_rows: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS,
    podcast_text_requested_page_max_bytes: Optional[int] = None,
) -> str:
    """Export one commit-ordered snapshot/keyset page for a v3-capable peer."""

    if stream not in STREAMS:
        raise SyncV2Error(f"unsupported stream: {stream}")
    if after and not snapshot:
        raise SyncV2Error("snapshot is required when continuing a page")
    authority_id = producer_authority_id(engine)
    limit = min(max(int(limit), 1), 5000)
    if stream == "podcast_texts":
        if podcast_text_page_max_bytes <= 0 or podcast_text_page_max_rows <= 0:
            raise SyncV2Error("podcast text page limits must be positive")
        if podcast_text_requested_page_max_bytes is not None:
            if podcast_text_requested_page_max_bytes <= 0:
                raise SyncV2Error(
                    "requested podcast text page byte limit must be positive"
                )
            podcast_text_page_max_bytes = min(
                podcast_text_page_max_bytes,
                int(podcast_text_requested_page_max_bytes),
            )
        limit = min(limit, int(podcast_text_page_max_rows))
    cursor_raw, cursor_id = _decode_cursor(after)
    cursor_revision = _canonical_revision(
        cursor_raw, field="cursor revision", allow_empty=True
    )
    since_revision = _canonical_revision(since, field="since", allow_empty=True)

    with Session(engine) as session:
        if stream == "taxonomy":
            active = session.exec(
                select(TaxonomyVersionRecord)
                .where(TaxonomyVersionRecord.status == "active")
                .order_by(TaxonomyVersionRecord.version.desc())
            ).first()
            taxonomy_version = int(active.version if active else 0)
            effective_snapshot = snapshot or str(
                current_taxonomy_sync_revision(session)
            )
            snapshot_revision = _canonical_revision(
                effective_snapshot, field="taxonomy snapshot"
            )
            tags = session.exec(
                select(CmsTagRecord).order_by(CmsTagRecord.code.asc())
            ).all()
            if len(tags) > 5000:
                raise SyncV2Error("taxonomy exceeds the atomic snapshot safety limit")
            rows = (
                []
                if since_revision >= snapshot_revision
                else [
                _line(
                    "taxonomy",
                    _taxonomy_payload(session, tag),
                    revision=effective_snapshot,
                    identity=tag.code,
                )
                for tag in tags
            ]
            )
            manifest = _manifest(
                stream,
                authority_id,
                effective_snapshot,
                "",
                rows,
                complete=True,
                since=since,
            )
            manifest["taxonomy_version"] = taxonomy_version
            manifest["full_snapshot"] = since_revision < snapshot_revision
            return encode_page(manifest, rows)

        clock = session.get(ArchiveSyncClockRecord, 1)
        if clock is None:
            raise SyncV2Error("transaction revision clock is not initialized")
        effective_snapshot = snapshot or str(clock.revision)
        snapshot_revision = _canonical_revision(effective_snapshot, field="snapshot")
        if since_revision > snapshot_revision:
            raise SyncV2Error("since cannot exceed snapshot")
        if cursor_revision > snapshot_revision:
            raise SyncV2Error("cursor revision cannot exceed snapshot")

        if stream == "media":
            rows, complete = _export_media_rows(
                session,
                snapshot=snapshot_revision,
                since_revision=since_revision,
                cursor_revision=cursor_revision,
                cursor_id=cursor_id,
                limit=limit,
            )
        else:
            query = select(ArchiveSyncEntityStateRecord).where(
                ArchiveSyncEntityStateRecord.stream == stream,
                ArchiveSyncEntityStateRecord.authority_id == "",
                ArchiveSyncEntityStateRecord.revision <= snapshot_revision,
            )
            query = _state_after_filter(
                query,
                since_revision=since_revision,
                cursor_revision=cursor_revision,
                cursor_id=cursor_id,
            )
            states = session.exec(
                query.order_by(
                    ArchiveSyncEntityStateRecord.revision.asc(),
                    ArchiveSyncEntityStateRecord.identity.asc(),
                ).limit(limit + 1)
            ).all()
            complete = len(states) <= limit
            rows = []
            podcast_rows_size = 0
            for state in states[:limit]:
                operation = state.operation
                payload = None
                if operation == "upsert":
                    payload = _upsert_payload(
                        session,
                        stream,
                        state.identity,
                        authority_id,
                        podcast_text_max_bytes=podcast_text_max_bytes,
                        podcast_text_max_chars=podcast_text_max_chars,
                    )
                    if payload is None:
                        operation = "tombstone"
                if operation == "tombstone":
                    if stream == "media":
                        continue
                    payload = _tombstone_payload(stream, state.identity)
                rows.append(
                    _line(
                    stream,
                    payload or {},
                    revision=str(state.revision),
                    identity=state.identity,
                    operation=operation,
                    )
                )
                if stream == "podcast_texts":
                    # Do not first materialize up to row_limit × artifact_limit
                    # bytes and only then truncate: stop as soon as the next
                    # legal row would cross the aggregate page ceiling.
                    candidate_manifest = _manifest(
                        stream,
                        authority_id,
                        effective_snapshot,
                        after,
                        rows,
                        complete=False,
                        since=since,
                    )
                    try:
                        row_size = len(_canonical(rows[-1]).encode("utf-8")) + 1
                        manifest_size = (
                            len(_canonical(candidate_manifest).encode("utf-8")) + 1
                        )
                    except UnicodeEncodeError as exc:
                        raise SyncV2Error(
                            "podcast text page contains invalid Unicode"
                        ) from exc
                    if (
                        podcast_rows_size + row_size + manifest_size
                        > podcast_text_page_max_bytes
                    ):
                        rows.pop()
                        if not rows:
                            raise SyncV2Error(
                                "one podcast text sync row exceeds the aggregate page byte limit"
                            )
                        complete = False
                        break
                    podcast_rows_size += row_size

    if stream == "podcast_texts":
        # Each artifact is bounded independently above, but a page can contain
        # many legal artifacts. Shrink the keyset page until its encoded NDJSON
        # also fits the aggregate transport/parse ceiling.
        had_rows = bool(rows)
        while True:
            manifest = _manifest(
                stream,
                authority_id,
                effective_snapshot,
                after,
                rows,
                complete=complete,
                since=since,
            )
            encoded = encode_page(manifest, rows)
            try:
                encoded_size = len(encoded.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise SyncV2Error("podcast text page contains invalid Unicode") from exc
            if encoded_size <= podcast_text_page_max_bytes:
                if had_rows and not rows:
                    raise SyncV2Error(
                        "one podcast text sync row exceeds the aggregate page byte limit"
                    )
                return encoded
            if not rows:
                raise SyncV2Error("podcast text manifest exceeds the page byte limit")
            rows.pop()
            complete = False

    return encode_page(
        _manifest(
        stream,
        authority_id,
        effective_snapshot,
        after,
        rows,
        complete=complete,
        since=since,
        ),
        rows,
    )


def parse_page(
    raw_text: str,
    *,
    expected_stream: Optional[str] = None,
    requested_snapshot: Optional[str] = None,
    requested_since: Optional[str] = None,
    requested_after: Optional[str] = None,
    requested_limit: Optional[int] = None,
    podcast_text_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    podcast_text_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
    podcast_text_page_max_bytes: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES,
    podcast_text_page_max_rows: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fully validate a page before the caller opens its write transaction."""

    def validate_podcast_page_envelope() -> None:
        if podcast_text_page_max_bytes <= 0 or podcast_text_page_max_rows <= 0:
            raise SyncV2Error("podcast text page limits must be positive")
        try:
            size = len(raw_text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SyncV2Error("podcast text page contains invalid Unicode") from exc
        if size > podcast_text_page_max_bytes:
            raise SyncV2Error("podcast text page exceeds the aggregate byte limit")

    if expected_stream == "podcast_texts":
        validate_podcast_page_envelope()

    parsed: list[dict[str, Any]] = []
    for number, raw in enumerate(raw_text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SyncV2Error(f"line {number}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise SyncV2Error(f"line {number}: object required")
        parsed.append(item)
    if not parsed or parsed[0].get("kind") != "manifest":
        raise SyncV2Error("v2 page requires a leading manifest")
    manifest, rows = parsed[0], parsed[1:]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SyncV2Error("unsupported v2 schema_version")
    stream = str(manifest.get("stream") or "")
    if stream not in STREAMS or (expected_stream and stream != expected_stream):
        raise SyncV2Error("v2 stream mismatch")
    if stream == "podcast_texts":
        if expected_stream != "podcast_texts":
            validate_podcast_page_envelope()
        allowed_rows = int(podcast_text_page_max_rows)
        if requested_limit is not None:
            if int(requested_limit) <= 0:
                raise SyncV2Error("requested page limit must be positive")
            allowed_rows = min(allowed_rows, int(requested_limit))
        if len(rows) > allowed_rows:
            raise SyncV2Error("podcast text page exceeds the aggregate row limit")
    if not str(manifest.get("authority_id") or "").strip():
        raise SyncV2Error("v2 manifest missing authority_id")
    if not isinstance(manifest.get("complete"), bool):
        raise SyncV2Error("v2 manifest complete must be boolean")
    if not isinstance(manifest.get("count"), int) or isinstance(
        manifest.get("count"), bool
    ):
        raise SyncV2Error("v2 manifest count must be an integer")
    if manifest["count"] != len(rows):
        raise SyncV2Error("v2 manifest count mismatch")
    for field in ("snapshot", "since", "after", "next_cursor"):
        if not isinstance(manifest.get(field), str):
            raise SyncV2Error(f"v2 manifest {field} must be a string")
    if not manifest["snapshot"]:
        raise SyncV2Error("v2 manifest snapshot is required")
    snapshot_revision = _canonical_revision(manifest["snapshot"], field="snapshot")
    since_revision = _canonical_revision(
        manifest["since"], field="since", allow_empty=True
    )
    if since_revision > snapshot_revision:
        raise SyncV2Error("since cannot exceed snapshot")
    if stream == "taxonomy":
        if manifest["after"] or not manifest["complete"]:
            raise SyncV2Error("taxonomy must be one complete atomic snapshot")
        taxonomy_version = manifest.get("taxonomy_version")
        if (
            not isinstance(taxonomy_version, int)
            or isinstance(taxonomy_version, bool)
            or taxonomy_version < 0
        ):
            raise SyncV2Error("taxonomy_version must be a non-negative integer")
        if not isinstance(manifest.get("full_snapshot"), bool):
            raise SyncV2Error("taxonomy full_snapshot must be boolean")
        if rows and not manifest["full_snapshot"]:
            raise SyncV2Error("incremental taxonomy no-op cannot contain rows")
        if rows and taxonomy_version <= 0:
            raise SyncV2Error(
                "non-empty taxonomy requires a published taxonomy_version"
            )
    if (
        requested_snapshot is not None
        and requested_snapshot
        and manifest["snapshot"] != requested_snapshot
    ):
        raise SyncV2Error("v2 response snapshot does not match request")
    if requested_since is not None and manifest["since"] != requested_since:
        raise SyncV2Error("v2 response since does not match request")
    if requested_after is not None and manifest["after"] != requested_after:
        raise SyncV2Error("v2 response after does not match request")
    expected_kind = stream.rstrip("s")
    previous_revision_raw, previous_identity = _decode_cursor(manifest["after"])
    previous_key = (
        _canonical_revision(
            previous_revision_raw,
            field="cursor revision",
            allow_empty=True,
        ),
        previous_identity,
    )
    for number, item in enumerate(rows, start=2):
        payload = item.get("payload")
        if (
            item.get("kind") != expected_kind
            or item.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload, dict)
        ):
            raise SyncV2Error(f"line {number}: invalid v2 record")
        revision = item.get("revision")
        identity = item.get("identity")
        if (
            not isinstance(revision, str)
            or not revision
            or not isinstance(identity, str)
            or not identity
        ):
            raise SyncV2Error(f"line {number}: revision and identity are required")
        revision_number = _canonical_revision(revision, field=f"line {number} revision")
        operation = item.get("operation", "upsert")
        if operation not in {"upsert", "tombstone"}:
            raise SyncV2Error(f"line {number}: invalid operation")
        if stream in {"taxonomy", "media"} and operation != "upsert":
            raise SyncV2Error(f"line {number}: {stream} does not accept tombstones")
        try:
            valid_checksum = bool(item.get("checksum")) and checksum(payload) == str(
                item["checksum"]
            )
        except UnicodeEncodeError as exc:
            raise SyncV2Error(
                f"line {number}: payload contains invalid Unicode"
            ) from exc
        if not valid_checksum:
            raise SyncV2Error(f"line {number}: checksum mismatch")
        identity_field = {
            "sources": "source_id",
            "source_states": "source_id",
            "analyses": "article_id",
            "media": "url_hash",
            "taxonomy": "code",
            "podcast_texts": "identity",
            "podcast_audio": "id",
        }.get(stream, "id")
        payload_identity = str(payload.get(identity_field) or "")
        if payload_identity != identity:
            raise SyncV2Error(f"line {number}: identity does not match payload")
        if operation == "tombstone" and payload.get("tombstone") is not True:
            raise SyncV2Error(f"line {number}: tombstone marker is required")
        if operation == "upsert" and payload.get("tombstone") is True:
            raise SyncV2Error(f"line {number}: upsert cannot carry tombstone marker")
        if stream == "podcast_texts" and operation == "upsert":
            _validated_podcast_text_payload(
                payload,
                max_bytes=podcast_text_max_bytes,
                max_chars=podcast_text_max_chars,
            )
        if stream == "podcast_texts" and operation == "tombstone":
            episode_id, separator, kind = identity.rpartition(":")
            if (
                not separator
                or not episode_id
                or kind not in PODCAST_TEXT_ARTIFACT_KINDS
            ):
                raise SyncV2Error(
                    f"line {number}: podcast text tombstone identity is not canonical"
                )
        if stream == "podcast_audio" and operation == "upsert":
            _validated_podcast_audio_payload(payload)
        if stream != "taxonomy":
            if since_revision >= 0 and revision_number <= since_revision:
                raise SyncV2Error(f"line {number}: revision is not newer than since")
            if revision_number > snapshot_revision:
                raise SyncV2Error(f"line {number}: revision exceeds snapshot")
        elif revision != manifest["snapshot"]:
            raise SyncV2Error(
                f"line {number}: taxonomy revision does not match snapshot"
            )
        key = (revision_number, identity)
        if key <= previous_key:
            raise SyncV2Error(f"line {number}: keyset order did not advance")
        previous_key = key
    expected_cursor = (
        manifest["after"]
        if not rows
        else _encode_cursor(str(rows[-1]["revision"]), str(rows[-1]["identity"]))
    )
    if manifest["next_cursor"] != expected_cursor:
        raise SyncV2Error("v2 manifest next_cursor mismatch")
    if not manifest["complete"] and (
        not rows or manifest["next_cursor"] == manifest["after"]
    ):
        raise SyncV2Error("incomplete v2 page did not advance")
    return manifest, rows


def require_transaction_revision_capability(manifest: dict[str, Any]) -> None:
    """Fail closed when a peer lacks any required archive-sync-v3 fence."""

    capabilities = manifest.get("capabilities")
    missing = set(REQUIRED_CAPABILITIES) - set(
        capabilities if isinstance(capabilities, list) else []
    )
    if missing:
        raise SyncV2Error(
            f"remote peer lacks required capability set: {', '.join(sorted(missing))}"
        )


def _json_object(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _validate_sha256(value: Any, *, field: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    candidate = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise SyncV2Error(f"podcast text {field} must be a lowercase SHA-256")
    return candidate


def _validated_podcast_text_payload(
    data: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
) -> dict[str, Any]:
    """Validate the complete Podcast text wire value before any database write."""

    identity = str(data.get("identity") or "")
    episode_id = str(data.get("episode_id") or "")
    kind = str(data.get("kind") or "")
    artifact_id = str(data.get("artifact_id") or "")
    artifact = data.get("artifact")
    if kind not in PODCAST_TEXT_ARTIFACT_KINDS:
        raise SyncV2Error("podcast text kind is not canonical")
    if not episode_id or identity != f"{episode_id}:{kind}":
        raise SyncV2Error("podcast text identity must match episode and kind")
    if (
        data.get("status") != "published"
        or not isinstance(data.get("published_at"), str)
        or not data["published_at"]
    ):
        raise SyncV2Error("podcast text upsert must be a published pointer")
    if not isinstance(artifact, dict):
        raise SyncV2Error("podcast text artifact object is required")
    if (
        not artifact_id
        or str(artifact.get("id") or "") != artifact_id
        or str(artifact.get("episode_id") or "") != episode_id
        or str(artifact.get("kind") or "") != kind
    ):
        raise SyncV2Error("podcast text artifact does not match its publication slot")
    version = artifact.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SyncV2Error("podcast text artifact version must be a positive integer")
    inline_text = artifact.get("inline_text")
    if not isinstance(inline_text, str) or not inline_text:
        raise SyncV2Error("podcast text artifact inline_text is required")
    try:
        validate_text_artifact(
            inline_text,
            max_chars=max_chars,
            max_bytes=max_bytes,
        )
    except PodcastTextLimitExceeded as exc:
        raise SyncV2Error(str(exc)) from exc
    content_hash = _validate_sha256(artifact.get("content_hash"), field="content_hash")
    if hashlib.sha256(inline_text.encode("utf-8")).hexdigest() != content_hash:
        raise SyncV2Error("podcast text content_hash does not match inline_text")
    if kind == "normalized_transcript":
        from services.podcast_normalized_transcripts import (
            NormalizedTranscriptError,
            canonical_normalized_transcript,
        )

        try:
            document = json.loads(inline_text)
            canonical = canonical_normalized_transcript(document)
        except (json.JSONDecodeError, NormalizedTranscriptError) as exc:
            raise SyncV2Error(
                "normalized transcript inline_text is not canonical"
            ) from exc
        if canonical != inline_text:
            raise SyncV2Error("normalized transcript inline_text is not canonical")
        if artifact.get("language") != document.get("language"):
            raise SyncV2Error(
                "normalized transcript language does not match inline_text"
            )
    _validate_sha256(
        artifact.get("source_content_hash"),
        field="source_content_hash",
        optional=True,
    )
    for field in ("language", "provenance_json", "created_at"):
        if not isinstance(artifact.get(field), str) or not artifact[field]:
            raise SyncV2Error(f"podcast text artifact {field} is required")
    try:
        provenance = json.loads(artifact["provenance_json"])
    except json.JSONDecodeError as exc:
        raise SyncV2Error("podcast text provenance_json must be valid JSON") from exc
    if not isinstance(provenance, dict):
        raise SyncV2Error("podcast text provenance_json must contain an object")
    if not isinstance(data.get("updated_at"), str) or not data["updated_at"]:
        raise SyncV2Error("podcast text publication updated_at is required")
    source_artifact_id = artifact.get("source_artifact_id")
    if source_artifact_id is not None and not isinstance(source_artifact_id, str):
        raise SyncV2Error("podcast text source_artifact_id must be a string or null")
    return artifact


def _accepted_remote_rows(
    session: Session,
    stream: str,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for item in rows:
        identity = str(item["identity"])
        revision = _canonical_revision(item["revision"], field="remote revision")
        state = session.get(ArchiveSyncEntityStateRecord, (stream, identity))
        if (
            state is not None
            and state.authority_id
            and state.authority_id != authority_id
        ):
            raise SyncV2Error(f"{stream} {identity} belongs to another authority")
        if (
            state is not None
            and state.authority_id == authority_id
            and int(state.revision) >= revision
        ):
            continue
        accepted.append(item)
    return accepted


def _save_remote_entity_states(
    session: Session,
    stream: str,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> None:
    now = _now_iso()
    for item in rows:
        identity = str(item["identity"])
        state = session.get(ArchiveSyncEntityStateRecord, (stream, identity))
        if state is None:
            state = ArchiveSyncEntityStateRecord(stream=stream, identity=identity)
        state.authority_id = authority_id
        state.revision = _canonical_revision(item["revision"], field="remote revision")
        state.operation = str(item.get("operation") or "upsert")
        state.updated_at = now
        session.add(state)


def _require_podcast_publication_authority(
    publication: PodcastTextPublicationRecord,
    authority_id: str,
) -> None:
    """Reject implicit takeover of a stable Podcast publication slot."""

    if publication.authority_id == authority_id:
        return
    existing = publication.authority_id or "<local>"
    raise SyncV2Error(
        f"podcast_texts {publication.identity} authority mismatch: "
        f"existing {existing!r}, incoming {authority_id!r}; "
        "refusing implicit authority takeover"
    )


def _apply_tombstones(
    session: Session,
    stream: str,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> int:
    deleted = 0
    for item in rows:
        if item.get("operation") != "tombstone":
            continue
        identity = str(item["identity"])
        if stream == "sources":
            record = session.get(SourceConfigRecord, identity)
            if record is not None and record.collection_authority_id == authority_id:
                source_state = session.get(SourceStateRecord, identity)
                if (
                    source_state is not None
                    and source_state.authority_id == authority_id
                ):
                    session.delete(source_state)
                session.delete(record)
                deleted += 1
        elif stream == "articles":
            record = session.get(ArticleRecord, identity)
            if record is not None and record.analysis_authority_id == authority_id:
                session.delete(record)
                deleted += 1
        elif stream == "analyses":
            record = session.get(ArticleAnalysisRecord, identity)
            if record is not None and record.authority_id == authority_id:
                session.exec(
                    delete(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id == identity,
                    ArticleTagAssignmentRecord.assignment_source == "llm",
                    )
                )
                session.delete(record)
                deleted += 1
        elif stream == "source_states":
            record = session.get(SourceStateRecord, identity)
            if record is not None and record.authority_id == authority_id:
                session.delete(record)
                deleted += 1
        elif stream == "podcast_texts":
            record = session.get(PodcastTextPublicationRecord, identity)
            if record is not None:
                _require_podcast_publication_authority(record, authority_id)
                if record.kind == "narration_script_zh":
                    withdraw_digest_audio_for_script_change(
                        session,
                        episode_id=record.episode_id,
                        current_artifact_id=None,
                    )
                record.status = "unpublished"
                record.unpublished_at = _now_iso()
                record.updated_at = record.unpublished_at
                session.add(record)
                deleted += 1
        elif stream == "podcast_audio":
            record = session.get(PodcastArtifactRecord, identity)
            if record is not None:
                if record.authority_id != authority_id:
                    raise SyncV2Error(
                        f"podcast audio {identity} belongs to another authority"
                    )
                if record.status != "withdrawn":
                    record.status = "withdrawn"
                    record.withdrawn_at = _now_iso()
                    record.updated_at = record.withdrawn_at
                    session.add(record)
                    deleted += 1
        else:
            raise SyncV2Error(f"{stream} does not support tombstones")
    return deleted


def _supersede_local_analysis(
    session: Session,
    article_id: str,
    *,
    now: str,
    preserve_result: bool,
    authority_id: str,
) -> None:
    record = session.get(ArticleAnalysisRecord, article_id)
    if record is None or (record.authority_id and record.authority_id != authority_id):
        return
    session.exec(
        update(ArticleAnalysisAttemptRecord)
        .where(
            ArticleAnalysisAttemptRecord.article_id == article_id,
            ArticleAnalysisAttemptRecord.status == "running",
        )
        .values(status="skipped", ended_at=now, error="superseded by remote authority")
    )
    record.status = "pending"
    if not preserve_result:
        session.exec(
            delete(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id == article_id,
                ArticleTagAssignmentRecord.assignment_source == "llm",
            )
        )
        record.tagging_status = "pending"
        record.quality_score = None
        record.dimension_scores_json = "{}"
        record.analysis_basis = ""
        record.analysis_input_hash = ""
        record.transcript_artifact_id = None
        record.analysis_diagnostics_json = "{}"
        record.score_reason = ""
        record.summary = ""
        record.content_genre = None
        record.content_features_json = "[]"
        record.entities_json = "[]"
        record.display_tags_json = "[]"
        record.primary_tag_id = None
        record.analyzed_at = None
        record.tagged_at = None
    record.started_at = None
    record.next_attempt_at = None
    record.lease_owner = None
    record.lease_expires_at = None
    record.last_error = None
    record.updated_at = now
    session.add(record)


def _apply_sources(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> tuple[int, int]:
    inserted = updated = 0
    for item in rows:
        data = item["payload"]
        source_id = str(data.get("source_id") or "")
        if not source_id or not _public_source(source_id):
            raise SyncV2Error("invalid platform source")
        existing = session.get(SourceConfigRecord, source_id)
        taking_authority = existing is not None and not bool(
            existing.collection_authority_id
        )
        if existing is None:
            existing = SourceConfigRecord(
                source_id=source_id,
                name=str(data.get("name") or source_id),
                source_type=str(data.get("source_type") or "rss"),
                owner_username="",
                collection_authority_id=authority_id,
                created_at=str(data.get("created_at") or _now_iso()),
                updated_at=str(data.get("updated_at") or _now_iso()),
            )
            inserted += 1
        else:
            if (existing.owner_username or "").strip():
                raise SyncV2Error(
                    f"source {source_id} collides with a local custom source"
                )
            if (
                existing.collection_authority_id
                and existing.collection_authority_id != authority_id
            ):
                raise SyncV2Error(f"source {source_id} belongs to another authority")
            updated += 1
        for field in _SOURCE_FIELDS:
            if field != "source_id" and field in data:
                setattr(existing, field, data[field])
        if taking_authority:
            local_state = session.get(SourceStateRecord, source_id)
            if local_state is not None:
                session.delete(local_state)
            handoff_now = _now_iso()
            for article in session.exec(
                select(ArticleRecord).where(ArticleRecord.source_id == source_id)
            ).all():
                article.analysis_authority_id = authority_id
                _supersede_local_analysis(
                    session,
                    article.id,
                    now=handoff_now,
                    preserve_result=True,
                    authority_id=authority_id,
                )
                session.add(article)
        existing.collection_authority_id = authority_id
        existing.fetcher_id = ""
        existing.params_json = "{}"
        session.add(existing)
    return inserted, updated


def _apply_taxonomy(
    session: Session,
    rows: list[dict[str, Any]],
    *,
    replace_missing: bool,
) -> tuple[int, int]:
    inserted = updated = 0
    incoming_names: dict[tuple[str, str], str] = {}
    incoming_external_keys: dict[str, str] = {}
    incoming_aliases: dict[tuple[str, str], str] = {}
    for item in rows:
        data = item["payload"]
        code = str(data.get("code") or "")
        kind = str(data.get("kind") or "")
        normalized_name = str(data.get("normalized_name") or "")
        if not code:
            raise SyncV2Error("taxonomy tag missing code")
        name_key = (kind, normalized_name)
        previous = incoming_names.setdefault(name_key, code)
        if previous != code:
            raise SyncV2Error(
                f"taxonomy normalized_name is duplicated by {previous} and {code}"
            )
        external_key = str(data.get("external_key") or "")
        if kind == "entity" and external_key:
            previous = incoming_external_keys.setdefault(external_key, code)
            if previous != code:
                raise SyncV2Error(
                    f"taxonomy external_key is duplicated by {previous} and {code}"
                )
        for alias in data.get("aliases") or []:
            alias_key = (
                str(alias.get("kind") or kind),
                str(alias.get("normalized_alias") or ""),
            )
            previous = incoming_aliases.setdefault(alias_key, code)
            if previous != code:
                raise SyncV2Error(
                    f"taxonomy alias is duplicated by {previous} and {code}"
                )

    # Neutralize unique authority-owned fields before applying a full snapshot.
    # This allows legal A<->B swaps and lets a new authority code supersede a
    # retained local/deprecated row without transient UNIQUE violations.
    existing_rows = session.exec(select(CmsTagRecord)).all()
    incoming_codes = {str(item["payload"]["code"]) for item in rows}
    for existing in existing_rows:
        conflicts = (
            existing.code in incoming_codes
            or (existing.kind, existing.normalized_name) in incoming_names
            or (
                existing.kind == "entity"
                and bool(existing.external_key)
                and str(existing.external_key) in incoming_external_keys
            )
        )
        if conflicts:
            existing.normalized_name = (
                f"__archive_sync_pending_{existing.id}_{existing.code}"
            )
            existing.external_key = None
            session.add(existing)
    session.flush()

    by_code: dict[str, CmsTagRecord] = {}
    for item in rows:
        data = item["payload"]
        code = str(data.get("code") or "")
        row = session.exec(
            select(CmsTagRecord).where(CmsTagRecord.code == code)
        ).first()
        if row is None:
            row = CmsTagRecord(
                code=code,
                kind=str(data["kind"]),
                name_zh=str(data.get("name_zh") or ""),
                name_en=str(data.get("name_en") or ""),
                normalized_name=str(data["normalized_name"]),
                created_at=str(data.get("created_at") or _now_iso()),
                updated_at=str(data.get("updated_at") or _now_iso()),
            )
            session.add(row)
            session.flush()
            inserted += 1
        else:
            updated += 1
        for field in (
            "kind",
            "name_zh",
            "name_en",
            "normalized_name",
            "description",
            "prompt_description",
            "status",
            "entity_type",
            "external_key",
            "user_selectable",
            "filterable",
            "recommendable",
            "activation_mode",
            "taxonomy_version",
            "updated_at",
        ):
            if field in data:
                setattr(row, field, data[field])
        by_code[code] = row
        session.add(row)
    session.flush()
    incoming_codes = set(by_code)
    for item in rows:
        data = item["payload"]
        for reference_field in ("parent_code", "replacement_code"):
            reference = str(data.get(reference_field) or "")
            if reference and reference not in incoming_codes:
                raise SyncV2Error(
                    f"taxonomy {data['code']} references missing {reference_field}: {reference}"
                )
    if replace_missing:
        # A changed/full authority snapshot replaces the active catalog. Preserve
        # absent rows for FK/manual-history integrity, but retire them. A same-
        # version incremental no-op has zero rows and must never retire anything.
        stale_query = select(CmsTagRecord)
        if incoming_codes:
            stale_query = stale_query.where(~CmsTagRecord.code.in_(incoming_codes))
        for stale in session.exec(stale_query).all():
            if (
                stale.status != "deprecated"
                or stale.user_selectable
                or stale.filterable
                or stale.recommendable
            ):
                stale.status = "deprecated"
                stale.user_selectable = False
                stale.filterable = False
                stale.recommendable = False
                session.add(stale)
                updated += 1
    incoming_tag_ids = [int(row.id or 0) for row in by_code.values()]
    if incoming_tag_ids:
        session.exec(
            delete(CmsTagAliasRecord).where(
            CmsTagAliasRecord.tag_id.in_(incoming_tag_ids)
            )
        )
    alias_conflicts = [
        and_(
            CmsTagAliasRecord.kind == kind,
            CmsTagAliasRecord.normalized_alias == normalized_alias,
        )
        for kind, normalized_alias in incoming_aliases
    ]
    if alias_conflicts:
        session.exec(delete(CmsTagAliasRecord).where(or_(*alias_conflicts)))

    # Atomic full snapshot: references and aliases can now resolve by stable code.
    for item in rows:
        data = item["payload"]
        row = by_code[str(data["code"])]
        row.parent_id = (
            by_code.get(str(data.get("parent_code") or ""), None).id
            if data.get("parent_code")
            else None
        )
        row.replacement_id = (
            by_code.get(str(data.get("replacement_code") or ""), None).id
            if data.get("replacement_code")
            else None
        )
        for alias in data.get("aliases") or []:
            session.add(CmsTagAliasRecord(tag_id=int(row.id or 0), **alias))
        session.add(row)
    return inserted, updated


def _taxonomy_snapshot_digest(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    """Stable semantic digest used to distinguish replay from same-revision rewrite."""

    return checksum(
        {
        "authority_id": str(manifest["authority_id"]),
        "snapshot": str(manifest["snapshot"]),
        "taxonomy_version": int(manifest["taxonomy_version"]),
        "full_snapshot": bool(manifest["full_snapshot"]),
        "rows": [
            {
                "revision": str(item["revision"]),
                "identity": str(item["identity"]),
                "checksum": str(item["checksum"]),
            }
            for item in rows
        ],
        }
    )


def _taxonomy_import_is_newer(
    session: Session,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
) -> bool:
    """Fence an authority against out-of-order or rewritten taxonomy snapshots."""

    authority_id = str(manifest["authority_id"])
    incoming_revision = int(manifest["snapshot"])
    incoming_version = int(manifest["taxonomy_version"])
    full_snapshot = bool(manifest["full_snapshot"])
    authority_row = session.get(AppSettingRecord, TAXONOMY_AUTHORITY_ID_KEY)
    if (
        authority_row is not None
        and authority_row.value
        and authority_row.value != authority_id
    ):
        raise SyncV2Error("taxonomy belongs to another authority")
    if authority_row is None or not authority_row.value:
        if not full_snapshot:
            raise SyncV2Error(
                "initial taxonomy authority handoff requires a full snapshot"
            )
        return True

    revision_row = session.get(AppSettingRecord, TAXONOMY_SYNC_REVISION_KEY)
    try:
        current_revision = int(revision_row.value) if revision_row is not None else -1
    except (TypeError, ValueError) as exc:
        raise SyncV2Error("stored taxonomy authority revision is invalid") from exc
    active_version = session.exec(
        select(TaxonomyVersionRecord.version).where(
            TaxonomyVersionRecord.status == "active"
        )
    ).first()
    current_version = int(active_version or 0)
    if incoming_revision < current_revision:
        raise SyncV2Error("taxonomy snapshot revision would move authority backwards")
    if incoming_version < current_version:
        raise SyncV2Error("taxonomy_version would move authority backwards")
    if incoming_revision > current_revision:
        if not full_snapshot:
            raise SyncV2Error("new taxonomy revision requires a full snapshot")
        return True

    # Equal authority revision is not a last-write-wins update. A producer's
    # ordinary `since=<revision>` response is a verified empty no-op; a full
    # replay must have the exact semantic digest saved with the first import.
    if incoming_version != current_version:
        raise SyncV2Error("equal taxonomy revision has inconsistent taxonomy_version")
    if not full_snapshot:
        if rows:
            raise SyncV2Error("equal taxonomy no-op unexpectedly contains rows")
        return False
    digest_row = session.get(AppSettingRecord, TAXONOMY_SNAPSHOT_DIGEST_KEY)
    incoming_digest = _taxonomy_snapshot_digest(manifest, rows)
    if digest_row is None or digest_row.value != incoming_digest:
        raise SyncV2Error("equal taxonomy revision does not match the applied snapshot")
    return False


def _apply_articles(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> tuple[int, int]:
    from api.routers.archive_sync import build_import_article_record

    inserted = updated = 0
    for item in rows:
        incoming = build_import_article_record(item["payload"])
        if not _public_source(incoming.source_id):
            raise SyncV2Error(
                "custom RSS cannot enter external-to-internal article stream"
            )
        source = session.get(SourceConfigRecord, incoming.source_id)
        # The sources stream is authoritative and runs first. Historical
        # Articles may legitimately outlive a physically deleted Source config,
        # so dependent streams must not synthesize a placeholder and resurrect it.
        if source is not None and (source.owner_username or "").strip():
            raise SyncV2Error(
                f"source {incoming.source_id} collides with a local custom source"
            )
        elif (
            source is not None
            and source.collection_authority_id
            and source.collection_authority_id != authority_id
        ):
            raise SyncV2Error(
                f"source {incoming.source_id} belongs to another authority"
            )
        elif source is not None:
            if not source.collection_authority_id:
                local_state = session.get(SourceStateRecord, incoming.source_id)
                if local_state is not None:
                    session.delete(local_state)
            source.collection_authority_id = authority_id
            session.add(source)

        existing = session.get(ArticleRecord, incoming.id)
        if existing is None:
            incoming.analysis_authority_id = authority_id
            session.add(incoming)
            inserted += 1
            continue
        if (
            existing.analysis_authority_id
            and existing.analysis_authority_id != authority_id
        ):
            raise SyncV2Error(f"article {incoming.id} belongs to another authority")
        taking_authority = not bool(existing.analysis_authority_id)
        previous_content_hash = compute_content_hash(existing)
        existing.analysis_authority_id = authority_id
        for field in (
            "title",
            "content_type",
            "source_id",
            "source_url",
            "publish_date",
            "fetched_date",
            "archive_updated_at",
            "fetch_run_id",
            "job_id",
            "job_run_id",
            "source_group_id",
            "run_scope",
            "has_content",
            "content",
            "extensions_json",
        ):
            setattr(existing, field, getattr(incoming, field))
        content_unchanged = previous_content_hash == compute_content_hash(existing)
        if taking_authority or not content_unchanged:
            _supersede_local_analysis(
                session,
                existing.id,
                now=_now_iso(),
                preserve_result=content_unchanged,
                authority_id=authority_id,
            )
        session.add(existing)
        updated += 1
    return inserted, updated


def _apply_analyses(
    session: Session, rows: list[dict[str, Any]], authority_id: str
) -> tuple[int, int]:
    inserted = updated = 0
    for item in rows:
        data = item["payload"]
        article_id = str(data.get("article_id") or "")
        article = session.get(ArticleRecord, article_id)
        if article is None:
            raise SyncV2Error(f"analysis article missing: {article_id}")
        if article.analysis_authority_id != authority_id:
            raise SyncV2Error(f"analysis authority mismatch: {article_id}")
        if str(data.get("content_hash") or "") != compute_content_hash(article):
            raise SyncV2Error(f"analysis content_hash mismatch: {article_id}")
        revision = str(item["revision"])
        record = session.get(ArticleAnalysisRecord, article_id)
        if record and record.authority_id and record.authority_id != authority_id:
            raise SyncV2Error(f"analysis {article_id} belongs to another authority")
        if record is None:
            record = ArticleAnalysisRecord(
                article_id=article_id,
                created_at=str(data.get("created_at") or _now_iso()),
                updated_at=str(data.get("updated_at") or _now_iso()),
            )
            inserted += 1
        else:
            updated += 1
        # New analysis provenance fields are additive within Archive Sync v2.
        # A page from an older producer legitimately omits them; clear receiver
        # state instead of pairing a new score/reason with stale prior metadata.
        for field, default in (
            ("analysis_basis", ""),
            ("analysis_input_hash", ""),
            ("transcript_artifact_id", None),
            ("analysis_diagnostics_json", "{}"),
        ):
            setattr(record, field, data.get(field, default))
        for field in (
            "status",
            "tagging_status",
            "quality_score",
            "dimension_scores_json",
            "score_reason",
            "summary",
            "content_genre",
            "content_features_json",
            "entities_json",
            "display_tags_json",
            "content_hash",
            "model_name",
            "prompt_version",
            "scoring_version",
            "taxonomy_version",
            "attempt_count",
            "analyzed_at",
            "tagged_at",
            "created_at",
            "updated_at",
        ):
            if field in data:
                setattr(record, field, data[field])
        record.authority_id = authority_id
        record.authority_revision = revision
        record.started_at = None
        record.next_attempt_at = None
        record.lease_owner = None
        record.lease_expires_at = None
        record.last_error = (
            None
            if record.status in {"pending", "running", "succeeded"}
            else str(data.get("last_error") or "")
        )
        session.exec(
            delete(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == article_id,
            ArticleTagAssignmentRecord.assignment_source == "llm",
            )
        )
        has_manual_primary = (
            session.exec(
                select(ArticleTagAssignmentRecord.id)
                .where(
                ArticleTagAssignmentRecord.article_id == article_id,
                ArticleTagAssignmentRecord.assignment_source != "llm",
                ArticleTagAssignmentRecord.is_primary.is_(True),
                )
                .limit(1)
            ).first()
            is not None
        )
        overlay_tag_ids = set(
            session.exec(
            select(ArticleTagAssignmentRecord.tag_id).where(
                ArticleTagAssignmentRecord.article_id == article_id,
                ArticleTagAssignmentRecord.assignment_source != "llm",
            )
            ).all()
        )
        remote_primary_id: Optional[int] = None
        for assignment in data.get("assignments") or []:
            tag = session.exec(
                select(CmsTagRecord).where(CmsTagRecord.code == assignment.get("code"))
            ).first()
            if tag is None or tag.kind != assignment.get("kind"):
                raise SyncV2Error(f"unknown taxonomy code: {assignment.get('code')}")
            if tag.id in overlay_tag_ids:
                continue
            is_primary = bool(assignment.get("is_primary") and not has_manual_primary)
            session.add(
                ArticleTagAssignmentRecord(
                article_id=article_id,
                tag_id=int(tag.id or 0),
                tag_kind=tag.kind,
                is_primary=is_primary,
                relevance=float(assignment.get("relevance") or 0),
                assignment_source="llm",
                prompt_version=str(assignment.get("prompt_version") or ""),
                taxonomy_version=int(assignment.get("taxonomy_version") or 0),
                created_at=str(assignment.get("created_at") or _now_iso()),
                updated_at=str(assignment.get("updated_at") or _now_iso()),
                )
            )
            if is_primary and assignment.get("code") == data.get("primary_tag_code"):
                remote_primary_id = tag.id
        manual_primary = session.exec(
            select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == article_id,
            ArticleTagAssignmentRecord.assignment_source != "llm",
            ArticleTagAssignmentRecord.is_primary.is_(True),
            )
        ).first()
        record.primary_tag_id = (
            manual_primary.tag_id if manual_primary else remote_primary_id
        )
        session.add(record)
    return inserted, updated


def _apply_media(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> tuple[int, int]:
    inserted = updated = 0
    for item in rows:
        data = item["payload"]
        key = str(data.get("url_hash") or "")
        if (
            key
            != hashlib.sha256(str(data.get("url") or "").encode("utf-8")).hexdigest()
        ):
            raise SyncV2Error("media url_hash mismatch")
        ext = str(data.get("ext") or "")
        if ext and _SAFE_MEDIA_EXT.fullmatch(ext) is None:
            raise SyncV2Error("media extension is unsafe")
        record = session.get(MediaAssetRecord, key)
        incoming_revision = str(item["revision"])
        if record is None:
            record = MediaAssetRecord(
                url_hash=key,
                url=str(data["url"]),
                status="pending_sync",
                content_hash=str(data.get("content_hash") or "") or None,
                mime=str(data.get("mime") or ""),
                ext=ext,
                size_bytes=int(data.get("size_bytes") or 0),
                sync_authority_id=authority_id,
                sync_authority_revision=incoming_revision,
                created_at=_now_iso(),
                fetched_at=None,
                updated_at=str(data.get("updated_at") or _now_iso()),
            )
            session.add(record)
            inserted += 1
        else:
            if record.sync_authority_id and record.sync_authority_id != authority_id:
                raise SyncV2Error(f"media {key} belongs to another authority")
            incoming_hash = str(data.get("content_hash") or "") or None
            binary_changed = (
                incoming_hash != record.content_hash
                or ext != record.ext
                or int(data.get("size_bytes") or 0) != record.size_bytes
            )
            record.content_hash = incoming_hash
            record.mime = str(data.get("mime") or "")
            record.ext = ext
            record.size_bytes = int(data.get("size_bytes") or 0)
            record.sync_authority_id = authority_id
            record.sync_authority_revision = incoming_revision
            if binary_changed:
                record.status = "pending_sync"
                record.fetched_at = None
            record.updated_at = str(
                data.get("updated_at") or record.updated_at or _now_iso()
            )
            session.add(record)
            updated += 1
    return inserted, updated


_PODCAST_TEXT_ARTIFACT_WIRE_FIELDS = (
    "id",
    "episode_id",
    "kind",
    "version",
    "content_hash",
    "inline_text",
    "language",
    "source_artifact_id",
    "source_content_hash",
    "provenance_json",
    "created_at",
)


def _apply_podcast_texts(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
    *,
    podcast_text_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    podcast_text_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
) -> tuple[int, int]:
    """Install immutable text values and move their authority-owned pointers."""

    inserted = updated = 0
    for item in rows:
        data = item["payload"]
        artifact_data = _validated_podcast_text_payload(
            data,
            max_bytes=podcast_text_max_bytes,
            max_chars=podcast_text_max_chars,
        )
        identity = str(data["identity"])
        episode_id = str(data["episode_id"])
        kind = str(data["kind"])
        artifact_id = str(data["artifact_id"])

        episode = session.get(ArticleRecord, episode_id)
        if episode is None or episode.content_type != "podcast_episode":
            raise SyncV2Error(f"podcast text episode {episode_id} is missing")
        if episode.analysis_authority_id != authority_id:
            raise SyncV2Error(
                f"podcast text episode {episode_id} belongs to another authority"
            )

        publication = session.get(PodcastTextPublicationRecord, identity)
        if publication is not None:
            _require_podcast_publication_authority(publication, authority_id)
            if publication.episode_id != episode_id or publication.kind != kind:
                raise SyncV2Error("podcast text publication identity is immutable")

        artifact = session.get(PodcastTextArtifactRecord, artifact_id)
        if artifact is None:
            version_owner = session.exec(
                select(PodcastTextArtifactRecord).where(
                    PodcastTextArtifactRecord.episode_id == episode_id,
                    PodcastTextArtifactRecord.kind == kind,
                    PodcastTextArtifactRecord.version == int(artifact_data["version"]),
                )
            ).first()
            if version_owner is not None:
                raise SyncV2Error(
                    "podcast text artifact version belongs to another immutable value"
                )
            artifact = PodcastTextArtifactRecord(
                **{
                    field: artifact_data.get(field)
                    for field in _PODCAST_TEXT_ARTIFACT_WIRE_FIELDS
                },
                authority_id=authority_id,
            )
            session.add(artifact)
        else:
            if artifact.authority_id != authority_id:
                raise SyncV2Error(
                    f"podcast text artifact {artifact_id} belongs to another authority"
                )
            for field in _PODCAST_TEXT_ARTIFACT_WIRE_FIELDS:
                if getattr(artifact, field) != artifact_data.get(field):
                    raise SyncV2Error(
                        f"podcast text artifact {artifact_id} is immutable"
                    )

        if publication is None:
            publication = PodcastTextPublicationRecord(
                identity=identity,
                episode_id=episode_id,
                kind=kind,
                artifact_id=artifact_id,
                status="published",
                authority_id=authority_id,
                published_at=str(data["published_at"]),
                unpublished_at=None,
                updated_at=str(data["updated_at"]),
            )
            inserted += 1
        else:
            if kind == "narration_script_zh" and (
                publication.artifact_id != artifact_id
                or publication.status != "published"
            ):
                withdraw_digest_audio_for_script_change(
                    session,
                    episode_id=episode_id,
                    current_artifact_id=artifact_id,
                )
            publication.artifact_id = artifact_id
            publication.status = "published"
            publication.published_at = str(data["published_at"])
            publication.unpublished_at = None
            publication.updated_at = str(data["updated_at"])
            updated += 1
        session.add(publication)
    return inserted, updated


_PODCAST_AUDIO_IMMUTABLE_FIELDS = (
    "episode_id",
    "kind",
    "content_hash",
    "mime",
    "ext",
    "size_bytes",
    "duration_seconds",
    "narration_artifact_id",
    "narration_content_hash",
    "created_at",
    "published_at",
)


def _apply_podcast_audio(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> tuple[int, int]:
    """Install published digest-audio metadata; bytes are installed afterward."""

    inserted = updated = 0
    for item in rows:
        data = _validated_podcast_audio_payload(item["payload"])
        episode = session.get(ArticleRecord, data["episode_id"])
        narration = session.get(
            PodcastTextArtifactRecord, data["narration_artifact_id"]
        )
        if (
            episode is None
            or episode.content_type != "podcast_episode"
            or episode.analysis_authority_id != authority_id
        ):
            raise SyncV2Error(
                f"podcast audio episode {data['episode_id']} is missing or belongs to another authority"
            )
        if (
            narration is None
            or narration.authority_id != authority_id
            or narration.episode_id != data["episode_id"]
            or narration.kind != "narration_script_zh"
            or narration.content_hash != data["narration_content_hash"]
        ):
            raise SyncV2Error("podcast audio narration dependency is missing")

        record = session.get(PodcastArtifactRecord, data["id"])
        if record is None:
            record = PodcastArtifactRecord(
                id=data["id"],
                episode_id=data["episode_id"],
                kind="digest_audio_zh",
                content_hash=data["content_hash"],
                mime=data["mime"],
                ext=data["ext"],
                size_bytes=data["size_bytes"],
                duration_seconds=data["duration_seconds"],
                status="ready",
                provenance=f"archive_sync:{str(data.get('provenance') or 'external')[:180]}",
                authority_id=authority_id,
                narration_artifact_id=data["narration_artifact_id"],
                narration_content_hash=data["narration_content_hash"],
                processing_id=None,
                producing_attempt_id=None,
                source_locator_hash=None,
                expires_at=None,
                expired_at=None,
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                published_at=data["published_at"],
                withdrawn_at=None,
            )
            session.add(record)
            inserted += 1
            continue
        if record.authority_id != authority_id:
            raise SyncV2Error(f"podcast audio {record.id} belongs to another authority")
        for field in _PODCAST_AUDIO_IMMUTABLE_FIELDS:
            if getattr(record, field) != data[field]:
                raise SyncV2Error(f"podcast audio {record.id} is immutable")
        if record.status == "withdrawn":
            record.status = "ready"
            record.withdrawn_at = None
            record.updated_at = data["updated_at"]
            session.add(record)
            updated += 1
    return inserted, updated


def _apply_source_states(
    session: Session,
    rows: list[dict[str, Any]],
    authority_id: str,
) -> tuple[int, int]:
    """Publish producer terminal state only after all data streams completed."""

    inserted = updated = 0
    fields = (
        "fetcher_id",
        "content_type",
        "status",
        "last_started_at",
        "last_completed_at",
        "last_success_at",
        "last_failure_at",
        "consecutive_failures",
        "total_runs",
        "success_runs",
        "failed_runs",
        "latest_fetched_count",
        "latest_saved_count",
        "latest_skipped_count",
        "latest_error_type",
        "latest_error_message",
        "updated_at",
    )
    for item in rows:
        data = item["payload"]
        source_id = str(data.get("source_id") or "")
        if not source_id or not _public_source(source_id):
            raise SyncV2Error("invalid platform source state")
        source = session.get(SourceConfigRecord, source_id)
        if source is not None and (source.owner_username or "").strip():
            raise SyncV2Error(f"source {source_id} collides with a local custom source")
        elif (
            source is not None
            and source.collection_authority_id
            and source.collection_authority_id != authority_id
        ):
            raise SyncV2Error(f"source {source_id} belongs to another authority")
        elif source is not None:
            source.collection_authority_id = authority_id
            session.add(source)
        state = session.get(SourceStateRecord, source_id)
        revision = str(item["revision"])
        if state is not None:
            if state.authority_id and state.authority_id != authority_id:
                raise SyncV2Error(
                    f"source state {source_id} belongs to another authority"
                )
        if state is None:
            state = SourceStateRecord(
                source_id=source_id,
                fetcher_id=str(data.get("fetcher_id") or source_id),
                updated_at=str(data.get("updated_at") or _now_iso()),
            )
            inserted += 1
        else:
            updated += 1
        for field in fields:
            if field in data:
                setattr(state, field, data[field])
        state.authority_id = authority_id
        state.authority_revision = revision
        # Cross-database run IDs/cursors are intentionally not meaningful locally.
        state.last_run_id = None
        state.last_cursor_value = ""
        state.last_cursor_date = ""
        state.last_content_id = ""
        session.add(state)
    return inserted, updated


def import_page(
    engine: Engine,
    raw_text: str,
    *,
    expected_stream: Optional[str] = None,
    requested_limit: Optional[int] = None,
    podcast_text_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
    podcast_text_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
    podcast_text_page_max_bytes: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES,
    podcast_text_page_max_rows: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS,
) -> dict[str, Any]:
    """Validate then atomically apply one page.  No partial page can commit."""

    manifest, rows = parse_page(
        raw_text,
        expected_stream=expected_stream,
        requested_limit=requested_limit,
        podcast_text_max_bytes=podcast_text_max_bytes,
        podcast_text_max_chars=podcast_text_max_chars,
        podcast_text_page_max_bytes=podcast_text_page_max_bytes,
        podcast_text_page_max_rows=podcast_text_page_max_rows,
    )
    stream = str(manifest["stream"])
    authority_id = str(manifest["authority_id"])
    with Session(engine) as session:
        try:
            deleted = 0
            if stream == "taxonomy":
                apply_taxonomy = _taxonomy_import_is_newer(session, manifest, rows)
                if not apply_taxonomy:
                    inserted = updated = 0
                else:
                    inserted, updated = _apply_taxonomy(
                        session,
                        rows,
                        replace_missing=True,
                    )
                authority_row = session.get(AppSettingRecord, TAXONOMY_AUTHORITY_ID_KEY)
                if authority_row is None:
                    authority_row = AppSettingRecord(
                        key=TAXONOMY_AUTHORITY_ID_KEY,
                        value=authority_id,
                    )
                elif authority_row.value and authority_row.value != authority_id:
                    raise SyncV2Error("taxonomy belongs to another authority")
                else:
                    authority_row.value = authority_id
                session.add(authority_row)
                revision_row = session.get(AppSettingRecord, TAXONOMY_SYNC_REVISION_KEY)
                if revision_row is None:
                    revision_row = AppSettingRecord(
                        key=TAXONOMY_SYNC_REVISION_KEY,
                        value=str(manifest["snapshot"]),
                    )
                else:
                    revision_row.value = str(manifest["snapshot"])
                session.add(revision_row)
                version = int(manifest.get("taxonomy_version") or 0)
                if apply_taxonomy and version:
                    for row in session.exec(select(TaxonomyVersionRecord)).all():
                        row.status = "active" if row.version == version else "retired"
                        session.add(row)
                    if session.get(TaxonomyVersionRecord, version) is None:
                        session.add(
                            TaxonomyVersionRecord(
                            version=version,
                            status="active",
                            change_summary="Archive Sync v2 authority snapshot",
                            activated_by=None,
                            activated_at=_now_iso(),
                            created_at=_now_iso(),
                            )
                        )
                if apply_taxonomy:
                    digest = _taxonomy_snapshot_digest(manifest, rows)
                    digest_row = session.get(
                        AppSettingRecord, TAXONOMY_SNAPSHOT_DIGEST_KEY
                    )
                    if digest_row is None:
                        digest_row = AppSettingRecord(
                            key=TAXONOMY_SNAPSHOT_DIGEST_KEY,
                            value=digest,
                        )
                    else:
                        digest_row.value = digest
                    session.add(digest_row)
            else:
                accepted = _accepted_remote_rows(session, stream, rows, authority_id)
                upserts = [
                    item
                    for item in accepted
                    if item.get("operation", "upsert") == "upsert"
                ]
                tombstones = [
                    item for item in accepted if item.get("operation") == "tombstone"
                ]
                if stream == "sources":
                    inserted, updated = _apply_sources(session, upserts, authority_id)
                elif stream == "articles":
                    inserted, updated = _apply_articles(session, upserts, authority_id)
                elif stream == "analyses":
                    inserted, updated = _apply_analyses(session, upserts, authority_id)
                elif stream == "media":
                    inserted, updated = _apply_media(session, upserts, authority_id)
                elif stream == "podcast_texts":
                    inserted, updated = _apply_podcast_texts(
                        session,
                        upserts,
                        authority_id,
                        podcast_text_max_bytes=podcast_text_max_bytes,
                        podcast_text_max_chars=podcast_text_max_chars,
                    )
                elif stream == "podcast_audio":
                    inserted, updated = _apply_podcast_audio(
                        session, upserts, authority_id
                    )
                else:
                    inserted, updated = _apply_source_states(
                        session, upserts, authority_id
                )
                deleted = _apply_tombstones(session, stream, tombstones, authority_id)
                _save_remote_entity_states(session, stream, accepted, authority_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
    if (
        stream == "taxonomy"
        and bool(manifest.get("full_snapshot"))
        and int(manifest.get("taxonomy_version") or 0) > 0
    ):
        with Session(engine) as session:
            reconcile_synced_taxonomy_candidates(
                session,
                taxonomy_version=int(manifest["taxonomy_version"]),
                authority_revision=str(manifest["snapshot"]),
            )
    return {
        "status": "success",
        "stream": stream,
        "authority_id": authority_id,
        "snapshot": str(manifest.get("snapshot") or ""),
        "next_cursor": str(manifest.get("next_cursor") or ""),
        "complete": bool(manifest.get("complete")),
        "count": len(rows),
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
    }


def _authority_scope_predicates(stream: str, authority_id: str):
    identity_column = (
        ArticleRecord.id if stream == "articles" else ArticleAnalysisRecord.article_id
    )
    remote_upsert = exists(
        select(ArchiveSyncEntityStateRecord.identity).where(
            ArchiveSyncEntityStateRecord.stream == stream,
            ArchiveSyncEntityStateRecord.identity == identity_column,
            ArchiveSyncEntityStateRecord.authority_id == authority_id,
            ArchiveSyncEntityStateRecord.operation == "upsert",
        )
    )
    source_is_eligible = or_(
        ~exists(
            select(SourceConfigRecord.source_id).where(
            SourceConfigRecord.source_id == ArticleRecord.source_id
            )
        ),
        exists(
            select(SourceConfigRecord.source_id).where(
            SourceConfigRecord.source_id == ArticleRecord.source_id,
            SourceConfigRecord.owner_username == "",
            SourceConfigRecord.collection_authority_id.in_(("", authority_id)),
            )
        ),
    )
    article_is_eligible = and_(
        ~ArticleRecord.source_id.startswith(_USER_SOURCE_PREFIX, autoescape=True),
        ArticleRecord.analysis_authority_id.in_(("", authority_id)),
        source_is_eligible,
    )
    return identity_column, remote_upsert, article_is_eligible


def full_authority_stale_identities(
    engine: Engine,
    stream: str,
    authority_id: str,
) -> list[str]:
    """Return receiver rows that need an authoritative absence check."""

    authority_id = str(authority_id or "").strip()
    if (
        stream not in {"articles", "analyses", "podcast_texts", "podcast_audio"}
        or not authority_id
    ):
        raise SyncV2Error(
            "full authority finalization supports articles/analyses/podcast_texts/podcast_audio only"
        )
    if stream == "podcast_audio":
        with Session(engine) as session:
            return [
                str(identity)
                for identity in session.exec(
                    select(PodcastArtifactRecord.id)
                    .join(
                        ArticleRecord,
                        ArticleRecord.id == PodcastArtifactRecord.episode_id,
                    )
                    .where(
                        PodcastArtifactRecord.kind == "digest_audio_zh",
                        PodcastArtifactRecord.status == "published",
                        PodcastArtifactRecord.authority_id == authority_id,
                        ArticleRecord.content_type == "podcast_episode",
                        ~exists(
                            select(ArchiveSyncEntityStateRecord.identity).where(
                                ArchiveSyncEntityStateRecord.stream == "podcast_audio",
                                ArchiveSyncEntityStateRecord.identity
                                == PodcastArtifactRecord.id,
                                ArchiveSyncEntityStateRecord.authority_id
                                == authority_id,
                                ArchiveSyncEntityStateRecord.operation == "upsert",
        )
                        ),
                    )
                    .order_by(PodcastArtifactRecord.id)
                ).all()
            ]
    if stream == "podcast_texts":
        _, _, article_is_eligible = _authority_scope_predicates(
            "articles", authority_id
        )
        with Session(engine) as session:
            return [
                str(identity)
                for identity in session.exec(
                select(PodcastTextPublicationRecord.identity)
                .join(
                    ArticleRecord,
                    ArticleRecord.id == PodcastTextPublicationRecord.episode_id,
                )
                .where(
                        PodcastTextPublicationRecord.authority_id.in_(
                            ("", authority_id)
                        ),
                    PodcastTextPublicationRecord.status == "published",
                    ArticleRecord.content_type == "podcast_episode",
                    article_is_eligible,
                    ~exists(
                        select(ArchiveSyncEntityStateRecord.identity).where(
                            ArchiveSyncEntityStateRecord.stream == "podcast_texts",
                            ArchiveSyncEntityStateRecord.identity
                            == PodcastTextPublicationRecord.identity,
                                ArchiveSyncEntityStateRecord.authority_id
                                == authority_id,
                            ArchiveSyncEntityStateRecord.operation == "upsert",
                        )
                    ),
                )
                .order_by(PodcastTextPublicationRecord.identity)
                ).all()
            ]
    identity_column, remote_upsert, article_is_eligible = _authority_scope_predicates(
        stream, authority_id
    )
    query = select(identity_column)
    if stream == "analyses":
        query = query.join(
            ArticleRecord, ArticleRecord.id == ArticleAnalysisRecord.article_id
        )
        query = query.where(ArticleAnalysisRecord.authority_id.in_(("", authority_id)))
    query = query.where(article_is_eligible, ~remote_upsert).order_by(identity_column)
    with Session(engine) as session:
        return [str(identity) for identity in session.exec(query).all()]


def authority_present_identities(
    engine: Engine,
    stream: str,
    identities: Iterable[str],
) -> list[str]:
    """Confirm which candidate identities currently exist in public authority scope."""

    if stream not in {"articles", "analyses", "podcast_texts", "podcast_audio"}:
        raise SyncV2Error(
            "presence supports articles/analyses/podcast_texts/podcast_audio only"
        )
    requested = list(dict.fromkeys(str(value or "").strip() for value in identities))
    if not requested or any(not value for value in requested) or len(requested) > 1000:
        raise SyncV2Error("presence identities must contain 1..1000 non-empty values")
    with Session(engine) as session:
        if stream == "podcast_audio":
            return sorted(
                artifact_id
                for artifact_id in requested
                if is_public_podcast_audio_reference(session, artifact_id)
            )
        if stream == "podcast_texts":
            present = session.exec(
                select(PodcastTextPublicationRecord.identity)
                .join(
                    ArticleRecord,
                    ArticleRecord.id == PodcastTextPublicationRecord.episode_id,
                )
                .outerjoin(
                    SourceConfigRecord,
                    SourceConfigRecord.source_id == ArticleRecord.source_id,
                )
                .where(
                    PodcastTextPublicationRecord.identity.in_(requested),
                    PodcastTextPublicationRecord.authority_id == "",
                    PodcastTextPublicationRecord.status == "published",
                    ArticleRecord.content_type == "podcast_episode",
                    ArticleRecord.analysis_authority_id == "",
                    ~ArticleRecord.source_id.startswith(
                        _USER_SOURCE_PREFIX, autoescape=True
                    ),
                    or_(
                        SourceConfigRecord.source_id.is_(None),
                        and_(
                            SourceConfigRecord.owner_username == "",
                            SourceConfigRecord.collection_authority_id == "",
                        ),
                    ),
                )
            ).all()
            return sorted(str(identity) for identity in present)
        articles = session.exec(
            select(ArticleRecord)
            .outerjoin(
                SourceConfigRecord,
                SourceConfigRecord.source_id == ArticleRecord.source_id,
            )
            .where(
                ArticleRecord.id.in_(requested),
                ~ArticleRecord.source_id.startswith(
                    _USER_SOURCE_PREFIX, autoescape=True
                ),
                ArticleRecord.analysis_authority_id == "",
                or_(
                    SourceConfigRecord.source_id.is_(None),
                    and_(
                        SourceConfigRecord.owner_username == "",
                        SourceConfigRecord.collection_authority_id == "",
                    ),
                ),
            )
        ).all()
        present = {row.id for row in articles}
        if stream == "analyses":
            present &= set(
                session.exec(
                select(ArticleAnalysisRecord.article_id).where(
                    ArticleAnalysisRecord.article_id.in_(present),
                    ArticleAnalysisRecord.authority_id == "",
                )
                ).all()
            )
    return sorted(present)


def finalize_full_authority_stream(
    engine: Engine,
    stream: str,
    authority_id: str,
    *,
    absent_identities: Iterable[str],
) -> int:
    """Prune receiver leftovers absent from a completed full snapshot.

    Source handoff keeps matching local rows until their authoritative upsert so
    reader-local columns and manual overlays survive. Once a first full Article
    or Analysis stream completes, its applied entity states are the exact remote
    inventory and any authority-owned row without an upsert state is stale.
    """

    authority_id = str(authority_id or "").strip()
    if (
        stream not in {"articles", "analyses", "podcast_texts", "podcast_audio"}
        or not authority_id
    ):
        raise SyncV2Error(
            "full authority finalization supports articles/analyses/podcast_texts/podcast_audio only"
        )
    confirmed_absent = list(
        dict.fromkeys(str(value or "").strip() for value in absent_identities)
        )
    if not confirmed_absent:
        return 0
    if stream == "podcast_audio":
        now = _now_iso()
        with Session(engine) as session:
            rows = session.exec(
                select(PodcastArtifactRecord).where(
                    PodcastArtifactRecord.id.in_(confirmed_absent),
                    PodcastArtifactRecord.authority_id == authority_id,
                    PodcastArtifactRecord.status == "published",
                )
            ).all()
            for row in rows:
                row.status = "withdrawn"
                row.withdrawn_at = now
                row.updated_at = now
                session.add(row)
            session.commit()
            return len(rows)
    if stream == "podcast_texts":
        now = _now_iso()
        with Session(engine) as session:
            pruned = 0
            for start in range(0, len(confirmed_absent), 900):
                batch = confirmed_absent[start:start + 900]
                rows = session.exec(
                    select(PodcastTextPublicationRecord).where(
                        PodcastTextPublicationRecord.identity.in_(batch),
                        PodcastTextPublicationRecord.authority_id.in_(
                            ("", authority_id)
                        ),
                        PodcastTextPublicationRecord.status == "published",
                        ~exists(
                            select(ArchiveSyncEntityStateRecord.identity).where(
                                ArchiveSyncEntityStateRecord.stream == "podcast_texts",
                                ArchiveSyncEntityStateRecord.identity
                                == PodcastTextPublicationRecord.identity,
                                ArchiveSyncEntityStateRecord.authority_id
                                == authority_id,
                                ArchiveSyncEntityStateRecord.operation == "upsert",
                            )
                        ),
                    )
                ).all()
                for row in rows:
                    if row.kind == "narration_script_zh":
                        withdraw_digest_audio_for_script_change(
                            session,
                            episode_id=row.episode_id,
                            current_artifact_id=None,
                        )
                    row.status = "unpublished"
                    row.unpublished_at = now
                    row.updated_at = now
                    session.add(row)
                    pruned += 1
            session.commit()
            return pruned
    identity_column, remote_upsert, article_is_eligible = _authority_scope_predicates(
        stream, authority_id
    )
    with Session(engine) as session:
        pruned = 0
        for start in range(0, len(confirmed_absent), 900):
            batch = confirmed_absent[start:start + 900]
            if stream == "articles":
                result = session.exec(
                    delete(ArticleRecord).where(
                    ArticleRecord.id.in_(batch),
                    article_is_eligible,
                    ~remote_upsert,
                    )
                )
            else:
                stale_ids = (
                    select(ArticleAnalysisRecord.article_id)
                    .join(
                    ArticleRecord,
                    ArticleRecord.id == ArticleAnalysisRecord.article_id,
                    )
                    .where(
                    ArticleAnalysisRecord.article_id.in_(batch),
                    article_is_eligible,
                    ArticleAnalysisRecord.authority_id.in_(("", authority_id)),
                    ~remote_upsert,
                )
                )
                session.exec(
                    delete(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id.in_(stale_ids),
                    ArticleTagAssignmentRecord.assignment_source == "llm",
                    )
                )
                result = session.exec(
                    delete(ArticleAnalysisRecord).where(
                    ArticleAnalysisRecord.article_id.in_(stale_ids)
                    )
                )
            pruned += max(int(getattr(result, "rowcount", 0) or 0), 0)
        session.commit()
        return pruned


def install_media_bytes(
    engine: Engine,
    media_root: Path,
    url_hash: str,
    body: bytes,
    *,
    max_bytes: int = 20 * 1024 * 1024,
) -> MediaAssetRecord:
    """Install one manifest-declared binary only after size/hash verification."""

    with Session(engine) as session:
        record = session.get(MediaAssetRecord, url_hash)
        if (
            record is None
            or record.status not in {"pending_sync", "cached"}
            or not record.content_hash
        ):
            raise SyncV2Error("media asset was not declared by a v2 manifest")
        if (
            len(body) != record.size_bytes
            or hashlib.sha256(body).hexdigest() != record.content_hash
        ):
            raise SyncV2Error("media binary checksum/size mismatch")
        try:
            mime, normalized_ext = validate_synced_image(
                body,
                declared_mime=record.mime,
                declared_ext=record.ext,
                max_bytes=max_bytes,
            )
        except ValueError as exc:
            raise SyncV2Error(str(exc)) from exc
        record.mime = mime
        record.ext = normalized_ext
        ext = str(record.ext or "")
        if ext and _SAFE_MEDIA_EXT.fullmatch(ext) is None:
            raise SyncV2Error("media extension is unsafe")
        root = media_root.resolve()
        target = (
            root / record.content_hash[:2] / f"{record.content_hash}{ext}"
        ).resolve()
        if not target.is_relative_to(root):
            raise SyncV2Error("media target escapes configured root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(body)
            temporary.replace(target)
        record.status = "cached"
        record.fetched_at = _now_iso()
        # Keep the producer revision in updated_at. Using the consumer clock here
        # would make a later producer update look older under clock skew.
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def is_public_podcast_audio_reference(session: Session, artifact_id: str) -> bool:
    """Return whether one local digest audio is eligible for external export."""

    record = session.get(PodcastArtifactRecord, artifact_id)
    if (
        record is None
        or record.kind != "digest_audio_zh"
        or record.status != "published"
        or record.authority_id
        or not record.published_at
    ):
        return False
    article = session.get(ArticleRecord, record.episode_id)
    if (
        article is None
        or article.content_type != "podcast_episode"
        or article.analysis_authority_id
        or not _public_source(article.source_id)
    ):
        return False
    source = session.get(SourceConfigRecord, article.source_id)
    return source is None or not (
        source.owner_username or source.collection_authority_id
    )


def install_podcast_audio_bytes(
    engine: Engine,
    store: Any,
    artifact_id: str,
    body: bytes,
) -> PodcastArtifactRecord:
    """Verify one declared digest-audio blob, install it in CAS, then publish it."""

    with Session(engine) as session:
        record = session.get(PodcastArtifactRecord, artifact_id)
        if (
            record is None
            or record.kind != "digest_audio_zh"
            or record.status not in {"ready", "published"}
            or not record.authority_id
        ):
            raise SyncV2Error("podcast audio was not declared by a v2 manifest")
        if (
            len(body) != record.size_bytes
            or hashlib.sha256(body).hexdigest() != record.content_hash
        ):
            raise SyncV2Error("podcast audio binary checksum/size mismatch")
        try:
            canonical_mime = store.validate_audio(body, record.mime)
            target = store.file_path_for_hash(record.content_hash, canonical_mime)
        except ValueError as exc:
            raise SyncV2Error(str(exc)) from exc
        if target.suffix != record.ext:
            raise SyncV2Error("podcast audio CAS extension mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != body:
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            try:
                temporary.write_bytes(body)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        record.status = "published"
        record.withdrawn_at = None
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def import_candidate_evidence_page(engine: Engine, raw_text: str) -> dict[str, Any]:
    """Accept minimized custom-RSS evidence into review, never auto-activation."""

    manifest, rows = parse_candidate_evidence_page(raw_text)
    authority_id = str(manifest["authority_id"])
    snapshot = str(manifest["snapshot"])
    inserted = skipped = 0
    with Session(engine) as session:
        staging_key = f"{CANDIDATE_STAGING_KEY_PREFIX}{authority_id}"
        staging_row = session.get(AppSettingRecord, staging_key)
        staging = _json_object(staging_row.value, {}) if staging_row else {}
        requested_after = str(manifest["after"])
        if requested_after:
            if (
                not staging
                or str(staging.get("snapshot") or "") != snapshot
                or str(staging.get("next_cursor") or "") != requested_after
            ):
                raise SyncV2Error("candidate evidence staging cursor/snapshot mismatch")
        elif staging and str(staging.get("snapshot") or "") == snapshot:
            # A lost HTTP response may replay the first page of the same snapshot.
            pass
        for item in rows:
            data = item["payload"]
            label = str(data.get("label") or "").strip()
            kind = str(data.get("kind") or "")
            fingerprint = str(data.get("article_fingerprint") or "")
            if (
                not label
                or kind not in {"topic", "industry", "entity"}
                or len(fingerprint) < 16
            ):
                raise SyncV2Error("invalid minimized candidate evidence")
            normalized = normalize_label(label)
            existing = session.exec(
                select(RemoteCandidateEvidenceRecord).where(
                RemoteCandidateEvidenceRecord.authority_id == authority_id,
                RemoteCandidateEvidenceRecord.article_fingerprint == fingerprint,
                RemoteCandidateEvidenceRecord.proposed_kind == kind,
                RemoteCandidateEvidenceRecord.normalized_label == normalized,
                )
            ).first()
            if existing:
                existing.sync_snapshot = snapshot
                session.add(existing)
                skipped += 1
                continue
            candidate = session.exec(
                select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.proposed_kind == kind,
                CmsTagCandidateRecord.normalized_label == normalized,
                )
            ).first()
            now = _now_iso()
            if candidate is None:
                candidate = CmsTagCandidateRecord(
                    label=label,
                    normalized_label=normalized,
                    proposed_kind=kind,
                    status="candidate",
                    risk_flags_json='["user_added_source"]',
                    first_seen_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(candidate)
                session.flush()
            else:
                risks = _json_object(candidate.risk_flags_json, [])
                if "user_added_source" not in risks:
                    risks.append("user_added_source")
                    candidate.risk_flags_json = json.dumps(risks, ensure_ascii=False)
                candidate.last_seen_at = now
                candidate.updated_at = now
                session.add(candidate)
            session.add(
                RemoteCandidateEvidenceRecord(
                candidate_id=int(candidate.id or 0),
                authority_id=authority_id,
                article_fingerprint=fingerprint,
                source_provenance=str(data.get("source_provenance") or "")[:200],
                label=label[:200],
                normalized_label=normalized,
                proposed_kind=kind,
                confidence=min(max(float(data.get("confidence") or 0), 0.0), 1.0),
                prompt_version=str(data.get("prompt_version") or "")[:100],
                sync_snapshot=snapshot,
                created_at=now,
                )
            )
            inserted += 1
        if manifest["complete"]:
            stale_candidate_ids = set(
                session.exec(
                select(RemoteCandidateEvidenceRecord.candidate_id).where(
                    RemoteCandidateEvidenceRecord.authority_id == authority_id,
                    RemoteCandidateEvidenceRecord.sync_snapshot != snapshot,
                )
                ).all()
            )
            session.exec(
                delete(RemoteCandidateEvidenceRecord).where(
                RemoteCandidateEvidenceRecord.authority_id == authority_id,
                RemoteCandidateEvidenceRecord.sync_snapshot != snapshot,
                )
            )
            session.flush()
            for candidate_id in stale_candidate_ids:
                candidate = session.get(CmsTagCandidateRecord, candidate_id)
                if candidate is None or candidate.status != "candidate":
                    continue
                has_local = (
                    session.exec(
                        select(CmsTagCandidateEvidenceRecord.candidate_id)
                        .where(
                    CmsTagCandidateEvidenceRecord.candidate_id == candidate_id,
                        )
                        .limit(1)
                    ).first()
                    is not None
                )
                has_remote = (
                    session.exec(
                        select(RemoteCandidateEvidenceRecord.id)
                        .where(
                    RemoteCandidateEvidenceRecord.candidate_id == candidate_id,
                        )
                        .limit(1)
                    ).first()
                    is not None
                )
                if not has_local and not has_remote:
                    session.delete(candidate)
            if staging_row is not None:
                session.delete(staging_row)
        else:
            staging_value = json.dumps(
                {
                "snapshot": snapshot,
                "next_cursor": str(manifest["next_cursor"]),
                },
                ensure_ascii=False,
            )
            if staging_row is None:
                staging_row = AppSettingRecord(key=staging_key, value=staging_value)
            else:
                staging_row.value = staging_value
            session.add(staging_row)
        session.commit()
    return {"status": "success", "inserted": inserted, "skipped": skipped}


def export_custom_candidate_evidence_page(
    engine: Engine,
    *,
    snapshot: str = "",
    after: str = "",
    limit: int = 1000,
) -> str:
    """Build an atomic, minimized custom-RSS evidence page for authority upload."""

    from models.db import CmsTagCandidateEvidenceRecord
    from services.user_sources import source_is_credentialed

    authority_id = producer_authority_id(engine)
    effective_snapshot = snapshot or _now_iso()
    cursor_revision, cursor_id = _decode_cursor(after)
    cursor_candidate_id = 0
    cursor_article_id = ""
    if cursor_id:
        try:
            cursor_candidate, cursor_article_id = cursor_id.split(":", 1)
            cursor_candidate_id = int(cursor_candidate)
        except (ValueError, TypeError) as exc:
            raise SyncV2Error("invalid candidate evidence cursor") from exc
    safe_limit = min(max(int(limit), 1), 5000)
    rows: list[dict[str, Any]] = []
    with Session(engine) as session:
        configured_sources = session.exec(
            select(SourceConfigRecord).where(
                or_(
            SourceConfigRecord.owner_username != "",
                    SourceConfigRecord.source_id.startswith(
                        _USER_SOURCE_PREFIX, autoescape=True
                    ),
                )
            )
        ).all()
        allowed_source_ids = [
            source.source_id
            for source in configured_sources
            if not source_is_credentialed(source)
        ]
        query = (
            select(CmsTagCandidateEvidenceRecord, CmsTagCandidateRecord)
            .join(
                CmsTagCandidateRecord,
                CmsTagCandidateRecord.id == CmsTagCandidateEvidenceRecord.candidate_id,
            )
            # Fail closed: an orphan `user_rss_` evidence row has no access
            # classification, so it must not leave this deployment.
            .where(CmsTagCandidateEvidenceRecord.source_id.in_(allowed_source_ids))
            .where(CmsTagCandidateEvidenceRecord.created_at <= effective_snapshot)
        )
        if cursor_revision:
            query = query.where(
                or_(
                CmsTagCandidateEvidenceRecord.created_at > cursor_revision,
                    (CmsTagCandidateEvidenceRecord.created_at == cursor_revision)
                    & or_(
                        CmsTagCandidateEvidenceRecord.candidate_id
                        > cursor_candidate_id,
                        (
                            CmsTagCandidateEvidenceRecord.candidate_id
                            == cursor_candidate_id
                        )
                        & (
                            CmsTagCandidateEvidenceRecord.article_id > cursor_article_id
                        ),
                ),
                )
            )
        evidence_rows = session.exec(
            query.order_by(
                CmsTagCandidateEvidenceRecord.created_at.asc(),
                CmsTagCandidateEvidenceRecord.candidate_id.asc(),
                CmsTagCandidateEvidenceRecord.article_id.asc(),
            ).limit(safe_limit + 1)
        ).all()
        complete = len(evidence_rows) <= safe_limit
        evidence_rows = evidence_rows[:safe_limit]
        for evidence, candidate in evidence_rows:
            fingerprint = hashlib.sha256(
                f"{evidence.source_id}\0{evidence.article_id}".encode("utf-8")
            ).hexdigest()
            payload = {
                "label": evidence.raw_label or candidate.label,
                "kind": candidate.proposed_kind,
                "confidence": evidence.confidence,
                "article_fingerprint": fingerprint,
                # Stable source id only; URL, owner and正文均不出内网。
                "source_provenance": evidence.source_id,
                "prompt_version": evidence.prompt_version,
            }
            rows.append(
                {
                "kind": "candidate_evidence",
                "schema_version": SCHEMA_VERSION,
                "revision": evidence.created_at,
                "identity": f"{int(evidence.candidate_id):020d}:{evidence.article_id}",
                "checksum": checksum(payload),
                "payload": payload,
                }
            )
    next_cursor = after
    if rows:
        next_cursor = _encode_cursor(rows[-1]["revision"], rows[-1]["identity"])
    manifest = {
        "kind": "manifest",
        "schema_version": SCHEMA_VERSION,
        "stream": "candidate_evidence",
        "authority_id": authority_id,
        "snapshot": effective_snapshot,
        "after": after,
        "next_cursor": next_cursor,
        "complete": complete,
        "count": len(rows),
        "generated_at": _now_iso(),
    }
    return encode_page(manifest, rows)


def parse_candidate_evidence_page(
    raw_text: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        parsed = [json.loads(line) for line in raw_text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise SyncV2Error("candidate evidence contains invalid JSON") from exc
    if not parsed or parsed[0].get("kind") != "manifest":
        raise SyncV2Error("candidate evidence requires a manifest")
    manifest, rows = parsed[0], parsed[1:]
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("stream") != "candidate_evidence"
    ):
        raise SyncV2Error("candidate evidence stream mismatch")
    if (
        not str(manifest.get("authority_id") or "")
        or not isinstance(manifest.get("count"), int)
        or isinstance(manifest.get("count"), bool)
        or manifest.get("count") != len(rows)
        or not isinstance(manifest.get("complete"), bool)
        or not isinstance(manifest.get("snapshot"), str)
        or not isinstance(manifest.get("after"), str)
        or not isinstance(manifest.get("next_cursor"), str)
    ):
        raise SyncV2Error("invalid candidate evidence manifest")
    previous = _decode_cursor(manifest["after"])
    for item in rows:
        payload = item.get("payload")
        if (
            item.get("kind") != "candidate_evidence"
            or item.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload, dict)
            or checksum(payload) != str(item.get("checksum") or "")
        ):
            raise SyncV2Error("candidate evidence checksum mismatch")
        key = (str(item.get("revision") or ""), str(item.get("identity") or ""))
        if not key[0] or not key[1] or key <= previous or key[0] > manifest["snapshot"]:
            raise SyncV2Error("candidate evidence keyset mismatch")
        previous = key
        allowed_fields = {
            "label",
            "kind",
            "confidence",
            "article_fingerprint",
            "source_provenance",
            "prompt_version",
        }
        if set(payload) - allowed_fields:
            raise SyncV2Error("candidate evidence contains unknown content fields")
    expected_cursor = manifest["after"] if not rows else _encode_cursor(*previous)
    if manifest["next_cursor"] != expected_cursor:
        raise SyncV2Error("candidate evidence next_cursor mismatch")
    if not manifest["complete"] and not rows:
        raise SyncV2Error("candidate evidence page did not advance")
    return manifest, rows
