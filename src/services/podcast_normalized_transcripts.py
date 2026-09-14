"""Canonical, crash-replayable materialization of provider ASR transcripts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.db import (
    ArticleRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services.podcast_processing import (
    PodcastEligibilityDenied,
    PodcastLeaseLost,
    PodcastProcessingClaim,
    PodcastProcessingConflict,
    StagePolicyCheck,
    _evaluate_processing_eligibility,
    _require_stage,
)
from services.podcast_text_limits import validate_text_artifact


KIND = "normalized_transcript"
FORMAT_VERSION = "dorami-normalized-transcript-v1"
_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
_SEGMENT_FIELDS = frozenset(
    {"text", "start_ms", "end_ms", "channel", "words"}
)
_WORD_FIELDS = frozenset(
    {"text", "start_ms", "end_ms", "channel", "confidence"}
)


class NormalizedTranscriptError(ValueError):
    """Provider-normalized transcript data is malformed."""


class NormalizedTranscriptConflict(PodcastProcessingConflict):
    """Persisted attempt output disagrees with a materialization replay."""


def _utc(value: Optional[dt.datetime]) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _stamp(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NormalizedTranscriptError(f"{field} must be nonempty text")
    return value


def _milliseconds(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NormalizedTranscriptError(f"{field} must be nonnegative milliseconds")
    return value


def _channel(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NormalizedTranscriptError(f"{field} must be a nonnegative integer or null")
    return value


def _confidence(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormalizedTranscriptError(f"{field} must be a number between zero and one")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise NormalizedTranscriptError(f"{field} must be a number between zero and one")
    return normalized


def _mapping(value: Any, field: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NormalizedTranscriptError(f"{field} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise NormalizedTranscriptError(
            f"{field} contains unsupported fields: {', '.join(sorted(map(str, unknown)))}"
        )
    return value


def canonical_normalized_transcript(document: Mapping[str, Any]) -> str:
    """Validate and encode the complete provider-neutral ASR evidence."""

    if not isinstance(document, Mapping):
        raise NormalizedTranscriptError("transcript must be an object")
    unknown = set(document) - {
        "audio_duration_ms",
        "format_version",
        "text",
        "language",
        "segments",
    }
    if unknown:
        raise NormalizedTranscriptError(
            "transcript contains unsupported fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    if (
        "format_version" in document
        and document.get("format_version") != FORMAT_VERSION
    ):
        raise NormalizedTranscriptError(
            f"format_version must be {FORMAT_VERSION}"
        )
    transcript_text = _text(document.get("text"), "text")
    language_value = document.get("language")
    if not isinstance(language_value, str) or not _LANGUAGE_RE.fullmatch(
        language_value.strip()
    ):
        raise NormalizedTranscriptError("language must be a BCP 47 language tag")
    language = language_value.strip().lower()
    audio_duration_ms = _milliseconds(
        document.get("audio_duration_ms"), "audio_duration_ms"
    )
    if audio_duration_ms == 0:
        raise NormalizedTranscriptError("audio_duration_ms must be positive")
    raw_segments = document.get("segments")
    if not isinstance(raw_segments, Sequence) or isinstance(
        raw_segments, (str, bytes, bytearray)
    ):
        raise NormalizedTranscriptError("segments must be an array")
    if not raw_segments:
        raise NormalizedTranscriptError("segments must contain ASR evidence")

    segments: list[dict[str, Any]] = []
    previous_segment_end: dict[int | None, int] = {}
    for segment_index, raw_segment in enumerate(raw_segments):
        path = f"segments[{segment_index}]"
        segment = _mapping(raw_segment, path, _SEGMENT_FIELDS)
        segment_text = _text(segment.get("text"), f"{path}.text")
        start_ms = _milliseconds(segment.get("start_ms"), f"{path}.start_ms")
        end_ms = _milliseconds(segment.get("end_ms"), f"{path}.end_ms")
        channel = _channel(segment.get("channel"), f"{path}.channel")
        if (
            end_ms <= start_ms
            or end_ms > audio_duration_ms
            or start_ms < previous_segment_end.get(channel, 0)
        ):
            raise NormalizedTranscriptError("segment timecodes must be ordered")
        previous_segment_end[channel] = end_ms
        raw_words = segment.get("words")
        if not isinstance(raw_words, Sequence) or isinstance(
            raw_words, (str, bytes, bytearray)
        ):
            raise NormalizedTranscriptError(f"{path}.words must be an array")
        words: list[dict[str, Any]] = []
        previous_word_end: dict[int | None, int] = {}
        for word_index, raw_word in enumerate(raw_words):
            word_path = f"{path}.words[{word_index}]"
            word = _mapping(raw_word, word_path, _WORD_FIELDS)
            word_start = _milliseconds(word.get("start_ms"), f"{word_path}.start_ms")
            word_end = _milliseconds(word.get("end_ms"), f"{word_path}.end_ms")
            word_channel = _channel(
                word.get("channel", channel), f"{word_path}.channel"
            )
            if (
                word_start < start_ms
                or word_end <= word_start
                or word_end > end_ms
                or word_start < previous_word_end.get(word_channel, start_ms)
            ):
                raise NormalizedTranscriptError(
                    "word timecodes must be ordered within their segment"
                )
            previous_word_end[word_channel] = word_end
            words.append(
                {
                    "channel": word_channel,
                    "confidence": _confidence(
                        word.get("confidence"), f"{word_path}.confidence"
                    ),
                    "end_ms": word_end,
                    "start_ms": word_start,
                    "text": _text(word.get("text"), f"{word_path}.text"),
                }
            )
        segments.append(
            {
                "channel": channel,
                "end_ms": end_ms,
                "start_ms": start_ms,
                "text": segment_text,
                "words": words,
            }
        )
    return json.dumps(
        {
            "audio_duration_ms": audio_duration_ms,
            "format_version": FORMAT_VERSION,
            "language": language,
            "segments": segments,
            "text": transcript_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _is_fenced(
    process: PodcastProcessingRecord,
    claim: PodcastProcessingClaim,
    stamp: str,
) -> bool:
    return bool(
        process.id == claim.processing_id
        and process.episode_id == claim.episode_id
        and process.processing_status == "running"
        and process.stage == "asr"
        and claim.stage == "asr"
        and process.lease_owner == claim.lease_owner
        and process.lease_token == claim.lease_token
        and process.fencing_token == claim.fencing_token
        and process.lease_expires_at
        and process.lease_expires_at > stamp
    )


def _detach(session: Session, artifact: PodcastTextArtifactRecord) -> PodcastTextArtifactRecord:
    session.expunge(artifact)
    return artifact


def _publish_local_transcript(
    session: Session,
    *,
    artifact: PodcastTextArtifactRecord,
    publication: PodcastTextPublicationRecord | None,
    stamp: str,
    replay: bool,
) -> None:
    """Publish local ASR output without taking over a remote authority slot.

    A replay may be repairing data written by the pre-publication materializer.
    It must not, however, move a pointer backwards after a newer local run has
    already published another immutable version.
    """

    identity = f"{artifact.episode_id}:{KIND}"
    if publication is not None and publication.authority_id:
        raise NormalizedTranscriptConflict(
            "remote authority occupies the normalized transcript publication slot"
        )
    if publication is None:
        session.add(
            PodcastTextPublicationRecord(
                identity=identity,
                episode_id=artifact.episode_id,
                kind=KIND,
                artifact_id=artifact.id,
                status="published",
                authority_id="",
                published_at=stamp,
                unpublished_at=None,
                updated_at=stamp,
            )
        )
        return
    if publication.artifact_id == artifact.id and publication.status == "published":
        return
    if replay and publication.artifact_id != artifact.id:
        current = session.get(PodcastTextArtifactRecord, publication.artifact_id)
        if current is not None and current.version > artifact.version:
            return
    publication.artifact_id = artifact.id
    publication.status = "published"
    publication.authority_id = ""
    publication.published_at = stamp
    publication.unpublished_at = None
    publication.updated_at = stamp
    session.add(publication)


def materialize_normalized_transcript(
    session: Session,
    claim: PodcastProcessingClaim,
    *,
    attempt_id: str,
    document: Mapping[str, Any],
    policy: Optional[StagePolicyCheck] = None,
    now: Optional[dt.datetime] = None,
) -> PodcastTextArtifactRecord:
    """Bind one canonical transcript and its hash to the current ASR attempt."""

    if session.in_transaction():
        raise NormalizedTranscriptConflict(
            "normalized transcript materialization requires a clean Session"
        )
    canonical = canonical_normalized_transcript(document)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    current = _utc(now)
    stamp = _stamp(current)
    try:
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        process = session.get(PodcastProcessingRecord, claim.processing_id)
        if process is None or not _is_fenced(process, claim, stamp):
            raise PodcastLeaseLost("processing lease is expired or fenced")
        episode = session.get(ArticleRecord, process.episode_id)
        if (
            episode is None
            or episode.content_type != "podcast_episode"
            or episode.id != claim.episode_id
        ):
            raise NormalizedTranscriptConflict("Podcast episode binding changed")
        if connection.dialect.name == "postgresql":
            session.expire_all()
            process = session.exec(
                select(PodcastProcessingRecord)
                .where(PodcastProcessingRecord.id == claim.processing_id)
                .with_for_update()
            ).first()
            episode = session.exec(
                select(ArticleRecord)
                .where(ArticleRecord.id == claim.episode_id)
                .with_for_update()
            ).first()
            if process is None or not _is_fenced(process, claim, stamp):
                raise PodcastLeaseLost("processing lease is expired or fenced")
            if episode is None or episode.content_type != "podcast_episode":
                raise NormalizedTranscriptConflict("Podcast episode binding changed")

        writer = getattr(policy, "require_artifact_writer", None)
        if callable(writer):
            writer(KIND, boundary="commit")
        else:
            _require_stage(policy, "asr", boundary="commit")
        eligibility, reasons = _evaluate_processing_eligibility(
            session, process, policy
        )
        if eligibility != "eligible":
            raise PodcastEligibilityDenied(reasons[0] if reasons else eligibility)
        if process.input_artifact_kind != "source_media_snapshot":
            raise NormalizedTranscriptConflict(
                "ASR normalized transcript requires a source-media snapshot"
            )
        source_media = session.get(
            PodcastSourceMediaSnapshotRecord, process.input_artifact_id
        )
        if (
            source_media is None
            or source_media.episode_id != episode.id
            or source_media.content_hash != process.input_content_hash
        ):
            raise NormalizedTranscriptConflict("source-media input binding changed")
        config = getattr(policy, "config", None)
        if config is None:
            from config import settings

            config = settings.podcast
        source_duration_ms = round(float(source_media.duration_seconds) * 1000)
        transcript_duration_ms = int(document["audio_duration_ms"])
        tolerance_ms = int(config.transcript_duration_tolerance_seconds) * 1000
        if source_duration_ms > 0 and abs(
            transcript_duration_ms - source_duration_ms
        ) > tolerance_ms:
            raise NormalizedTranscriptConflict(
                "normalized transcript duration does not match source media"
            )

        attempt = session.exec(
            select(PodcastStageAttemptRecord)
            .where(PodcastStageAttemptRecord.id == attempt_id)
            .with_for_update()
        ).first()
        latest = session.exec(
            select(PodcastStageAttemptRecord)
            .where(
                PodcastStageAttemptRecord.processing_id == process.id,
                PodcastStageAttemptRecord.submission_state.in_(
                    ("prepared", "submitted", "request_unknown", "reconciling")
                ),
            )
            .order_by(PodcastStageAttemptRecord.attempt_no.desc())
        ).first()
        if (
            attempt is None
            or latest is None
            or latest.id != attempt.id
            or attempt.processing_id != process.id
            or attempt.stage != "asr"
            or attempt.execution_kind != "provider"
            or attempt.input_hash != process.input_content_hash
            or attempt.submission_state != "submitted"
            or attempt.request_unknown
            or not attempt.settings_fingerprint
        ):
            raise NormalizedTranscriptConflict(
                "ASR attempt is not the current fenced submitted attempt"
            )

        identity = f"{episode.id}:{KIND}"
        publication_query = select(PodcastTextPublicationRecord).where(
            PodcastTextPublicationRecord.identity == identity
        )
        if connection.dialect.name == "postgresql":
            publication_query = publication_query.with_for_update()
        publication = session.exec(publication_query).first()
        if publication is not None and publication.authority_id:
            raise NormalizedTranscriptConflict(
                "remote authority occupies the normalized transcript publication slot"
            )

        existing = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.producing_attempt_id == attempt.id
            )
        ).first()
        if existing is not None:
            if (
                existing.processing_id != process.id
                or existing.episode_id != episode.id
                or existing.kind != KIND
                or existing.inline_text != canonical
                or existing.content_hash != content_hash
                or existing.source_artifact_id != process.input_artifact_id
                or existing.source_content_hash != process.input_content_hash
                or attempt.output_hash != content_hash
                or attempt.output_artifact_id != existing.id
                or attempt.output_artifact_kind != KIND
            ):
                raise NormalizedTranscriptConflict(
                    "attempt replay conflicts with its persisted transcript output"
                )
            _publish_local_transcript(
                session,
                artifact=existing,
                publication=publication,
                stamp=stamp,
                replay=True,
            )
            session.flush()
            detached = _detach(session, existing)
            session.commit()
            return detached
        if attempt.output_hash or attempt.output_artifact_id or attempt.output_artifact_kind:
            raise NormalizedTranscriptConflict(
                "attempt is already bound to a different output"
            )
        processing_output = session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.processing_id == process.id,
                PodcastTextArtifactRecord.kind == KIND,
            )
        ).first()
        if processing_output is not None:
            raise NormalizedTranscriptConflict(
                "processing already has a different normalized transcript output"
            )

        validate_text_artifact(
            canonical,
            max_chars=config.text_artifact_max_chars,
            max_bytes=config.text_artifact_max_bytes,
        )
        latest_version = session.exec(
            select(func.max(PodcastTextArtifactRecord.version)).where(
                PodcastTextArtifactRecord.episode_id == episode.id,
                PodcastTextArtifactRecord.kind == KIND,
            )
        ).one()
        artifact = PodcastTextArtifactRecord(
            id=(
                "podcast-normalized-"
                + hashlib.sha256(attempt.id.encode("utf-8")).hexdigest()[:32]
            ),
            episode_id=episode.id,
            kind=KIND,
            version=int(latest_version or 0) + 1,
            content_hash=content_hash,
            inline_text=canonical,
            language=str(document["language"]).strip().lower(),
            authority_id="",
            source_artifact_id=process.input_artifact_id,
            source_content_hash=process.input_content_hash,
            processing_id=process.id,
            producing_attempt_id=attempt.id,
            provenance_json=json.dumps(
                {
                    "format": "dorami-normalized-transcript-v1",
                    "model": attempt.model_name,
                    "provider": attempt.provider_name,
                    "provider_revision": attempt.provider_revision,
                    "provider_task_id_ref": hashlib.sha256(
                        attempt.provider_task_id.encode("utf-8")
                    ).hexdigest()[:12],
                    "settings_fingerprint": attempt.settings_fingerprint,
                    "source_content_hash": process.input_content_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            created_at=stamp,
        )
        session.add(artifact)
        session.flush()
        attempt.output_hash = content_hash
        attempt.output_artifact_id = artifact.id
        attempt.output_artifact_kind = KIND
        attempt.updated_at = stamp
        session.add(attempt)
        _publish_local_transcript(
            session,
            artifact=artifact,
            publication=publication,
            stamp=stamp,
            replay=False,
        )
        session.flush()
        session.refresh(artifact)
        detached = _detach(session, artifact)
        session.commit()
        return detached
    except IntegrityError as exc:
        session.rollback()
        raise NormalizedTranscriptConflict(
            "normalized transcript materialization raced with another writer"
        ) from exc
    except Exception:
        session.rollback()
        raise


__all__ = [
    "KIND",
    "FORMAT_VERSION",
    "NormalizedTranscriptConflict",
    "NormalizedTranscriptError",
    "canonical_normalized_transcript",
    "materialize_normalized_transcript",
]
