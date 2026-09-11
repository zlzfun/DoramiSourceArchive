"""Strict, provider-neutral Podcast processing input bindings."""

from __future__ import annotations

import math

from services.podcast_processing import deterministic_input_fingerprint


class SourceMediaDurationError(ValueError):
    """Persisted source-media duration is unsafe for accounting."""


def source_media_duration_ms(duration_seconds: object) -> int:
    """Convert persisted probe seconds to exact positive milliseconds."""

    if isinstance(duration_seconds, bool) or not isinstance(
        duration_seconds, (int, float)
    ):
        raise SourceMediaDurationError(
            "source media duration must be a finite number"
        )
    seconds = float(duration_seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise SourceMediaDurationError(
            "source media duration must be finite and positive"
        )
    milliseconds = round(seconds * 1000)
    if milliseconds <= 0:
        raise SourceMediaDurationError(
            "source media duration must be at least one millisecond"
        )
    return milliseconds


def processing_input_fingerprint(
    *,
    episode_id: str,
    entry_stage: str,
    artifact_id: str,
    content_hash: str,
    kind: str,
    language: str,
    audio_duration_ms: int | None,
    admission_fingerprint: str,
    voice_profile_id: str = "",
) -> str:
    """Bind immutable input evidence to a non-secret provider admission plan."""

    if audio_duration_ms is not None and (
        isinstance(audio_duration_ms, bool)
        or not isinstance(audio_duration_ms, int)
        or audio_duration_ms <= 0
    ):
        raise ValueError("audio_duration_ms must be a positive integer or None")
    admission = str(admission_fingerprint or "")
    if admission and (
        len(admission) != 64
        or any(character not in "0123456789abcdef" for character in admission)
    ):
        raise ValueError("admission_fingerprint must be lowercase SHA-256 hex")
    if not admission:
        return deterministic_input_fingerprint(
            {
                "schema": "podcast-processing-input-v1",
                "episode_id": episode_id,
                "entry_stage": entry_stage,
                "artifact_id": artifact_id,
                "content_hash": content_hash,
                "kind": kind,
                "language": language,
                "voice_profile_id": voice_profile_id,
            }
        )
    return deterministic_input_fingerprint(
        {
            "schema": "podcast-processing-input-v2",
            "episode_id": episode_id,
            "entry_stage": entry_stage,
            "artifact_id": artifact_id,
            "content_hash": content_hash,
            "kind": kind,
            "language": language,
            "audio_duration_ms": audio_duration_ms,
            "admission_fingerprint": admission,
            "voice_profile_id": voice_profile_id,
        }
    )


__all__ = [
    "SourceMediaDurationError",
    "processing_input_fingerprint",
    "source_media_duration_ms",
]
