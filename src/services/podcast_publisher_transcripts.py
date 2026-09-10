"""Bounded, explicit ingestion of publisher-provided Podcast transcripts.

The RSS collector only stores ``podcast:transcript`` locators.  This module is
the sole network path that resolves one of those locators, and callers must run
all three fetch-stage authority checks around it.  Publisher transcripts never
invoke ASR or any other provider.

Format selection is intentionally deterministic:

* a declared supported MIME is authoritative;
* when MIME is absent, a supported URL extension is used;
* a declared unsupported MIME is never rescued by its extension;
* conflicting, recognized MIME/extension pairs are skipped;
* timed VTT/SRT/Podcasting-JSON candidates precede plain text, preserving feed
  order within each group.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import html
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services import http_safety
from services.podcast_text_limits import (
    PodcastTextLimitExceeded,
    validate_text_artifact,
)
from services.podcast_stage_policy import PodcastStagePolicy


KIND = "publisher_transcript"
_MIME_FORMATS = {
    "text/vtt": "vtt",
    "application/x-subrip": "srt",
    "application/srt": "srt",
    "text/srt": "srt",
    "text/plain": "text",
    "application/json": "json",
    "application/podcast+json": "json",
}
_EXTENSION_FORMATS = {
    ".vtt": "vtt",
    ".srt": "srt",
    ".txt": "text",
    ".text": "text",
    ".json": "json",
}
_CANONICAL_MIME = {
    "vtt": "text/vtt",
    "srt": "application/x-subrip",
    "text": "text/plain",
    "json": "application/json",
}
_TIMING_LINE = re.compile(r"^(.+?)\s+-->\s+([^\s]+)(?:\s+.*)?$")
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"[ \t\f\v]+")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class PublisherTranscriptError(ValueError):
    """Base error for a rejected publisher transcript operation."""


class PublisherTranscriptNotFound(PublisherTranscriptError):
    """The episode or a supported locator does not exist."""


class PublisherTranscriptMalformed(PublisherTranscriptError):
    """The selected document cannot be parsed safely."""


class PublisherTranscriptTooLarge(PublisherTranscriptError):
    """The remote document crossed the configured byte ceiling."""


class PublisherTranscriptTimeout(PublisherTranscriptError):
    """The complete DNS/connect/read operation crossed its wall-clock limit."""


class PublisherTranscriptFetchFailed(PublisherTranscriptError):
    """The publisher endpoint failed before a document could be validated."""


class PublisherTranscriptConflict(PublisherTranscriptError):
    """A remote-authority publication occupies the local producer slot."""


@dataclass(frozen=True)
class TranscriptCandidate:
    url: str
    format: str
    mime: str
    language: str
    metadata_index: int


@dataclass(frozen=True)
class ParsedTranscript:
    # Validated publisher document after UTF-8 BOM/line-ending normalization.
    # This is the immutable evidence stored and hashed by the text artifact.
    source_text: str
    # Plain text is a bounded QA/summary projection only; it must never replace
    # timing/speaker-bearing publisher evidence in the artifact.
    text: str
    segment_count: int


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _mime(value: Any) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower()


def select_candidate(raw_transcripts: Any) -> TranscriptCandidate:
    """Choose the first safe supported locator using the module policy above."""

    if isinstance(raw_transcripts, dict):
        raw_transcripts = [raw_transcripts]
    if not isinstance(raw_transcripts, list):
        raise PublisherTranscriptNotFound("Podcast 单集没有发布者逐字稿元数据")
    candidates: list[TranscriptCandidate] = []
    for index, value in enumerate(raw_transcripts):
        if not isinstance(value, dict):
            continue
        url = str(value.get("url") or value.get("href") or "").strip()
        if not url:
            continue
        declared_mime = _mime(value.get("type"))
        ext_format = _EXTENSION_FORMATS.get(_extension(url))
        if declared_mime:
            selected_format = _MIME_FORMATS.get(declared_mime)
            if selected_format is None:
                continue
            if ext_format is not None and ext_format != selected_format:
                continue
        else:
            selected_format = ext_format
            if selected_format is None:
                continue
        language = str(value.get("language") or value.get("lang") or "und").strip()
        if language != "und" and not _LANGUAGE.fullmatch(language):
            language = "und"
        candidates.append(TranscriptCandidate(
            url=url,
            format=selected_format,
            mime=declared_mime or _CANONICAL_MIME[selected_format],
            language=language,
            metadata_index=index,
        ))
    if not candidates:
        raise PublisherTranscriptNotFound("Podcast 单集没有支持的发布者逐字稿")
    candidates.sort(key=lambda item: (item.format == "text", item.metadata_index))
    return candidates[0]


def _decode(body: bytes, *, max_text_chars: int) -> str:
    try:
        value = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise PublisherTranscriptMalformed("发布者逐字稿必须是 UTF-8 文本") from exc
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > max_text_chars:
        raise PublisherTranscriptMalformed("发布者逐字稿超过文本字符上限")
    if "\x00" in value:
        raise PublisherTranscriptMalformed("发布者逐字稿包含无效的 NUL 字符")
    return value


def _timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise PublisherTranscriptMalformed("发布者逐字稿包含畸形时间码")
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise PublisherTranscriptMalformed("发布者逐字稿包含畸形时间码")
        return result
    raw = str(value or "").strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) not in {2, 3}:
        raise PublisherTranscriptMalformed("发布者逐字稿包含畸形时间码")
    try:
        hours = int(parts[0]) if len(parts) == 3 else 0
        minutes = int(parts[-2])
        seconds = float(parts[-1])
    except ValueError as exc:
        raise PublisherTranscriptMalformed("发布者逐字稿包含畸形时间码") from exc
    if (
        not math.isfinite(seconds)
        or hours < 0
        or minutes < 0
        or minutes >= 60
        or seconds < 0
        or seconds >= 60
    ):
        raise PublisherTranscriptMalformed("发布者逐字稿包含畸形时间码")
    return hours * 3600 + minutes * 60 + seconds


def _clean_lines(lines: Iterable[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        line = html.unescape(_TAG.sub("", line)).strip()
        line = _SPACE.sub(" ", line)
        if line:
            cleaned.append(line)
    return " ".join(cleaned)


def _bounded_segments(
    texts: list[str], *, source_text: str, max_segments: int, max_text_chars: int
) -> ParsedTranscript:
    if len(texts) > max_segments:
        raise PublisherTranscriptMalformed("发布者逐字稿超过 segment 数量上限")
    normalized = "\n".join(value for value in texts if value).strip()
    if not normalized:
        raise PublisherTranscriptMalformed("发布者逐字稿内容为空")
    if len(normalized) > max_text_chars:
        raise PublisherTranscriptMalformed("发布者逐字稿超过文本字符上限")
    return ParsedTranscript(
        source_text=source_text.strip(),
        text=normalized,
        segment_count=len(texts),
    )


def _parse_vtt(value: str, *, max_segments: int, max_text_chars: int) -> ParsedTranscript:
    lines = value.split("\n")
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    header = lines[first].lstrip() if first is not None else ""
    if header != "WEBVTT" and not header.startswith("WEBVTT "):
        raise PublisherTranscriptMalformed("VTT 缺少 WEBVTT 文件头")
    blocks = re.split(r"\n[ \t]*\n", "\n".join(lines[first + 1 :]).strip())
    texts: list[str] = []
    previous_start = -1.0
    for block in blocks:
        cue = [line.strip() for line in block.split("\n") if line.strip()]
        if not cue or cue[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = 0 if "-->" in cue[0] else 1
        if timing_index >= len(cue):
            raise PublisherTranscriptMalformed("VTT cue 缺少时间码")
        match = _TIMING_LINE.fullmatch(cue[timing_index])
        if match is None:
            raise PublisherTranscriptMalformed("VTT cue 包含畸形时间码")
        start, end = _timestamp(match.group(1)), _timestamp(match.group(2))
        if end <= start or start < previous_start:
            raise PublisherTranscriptMalformed("VTT cue 时间码倒序或区间无效")
        previous_start = start
        text = _clean_lines(cue[timing_index + 1 :])
        if not text:
            raise PublisherTranscriptMalformed("VTT cue 内容为空")
        texts.append(text)
        if len(texts) > max_segments:
            raise PublisherTranscriptMalformed("发布者逐字稿超过 segment 数量上限")
    return _bounded_segments(
        texts,
        source_text=value,
        max_segments=max_segments,
        max_text_chars=max_text_chars,
    )


def _parse_srt(value: str, *, max_segments: int, max_text_chars: int) -> ParsedTranscript:
    blocks = re.split(r"\n[ \t]*\n", value.strip())
    texts: list[str] = []
    previous_start = -1.0
    for block in blocks:
        cue = [line.strip() for line in block.split("\n") if line.strip()]
        if not cue:
            continue
        timing_index = 0 if "-->" in cue[0] else 1
        if timing_index >= len(cue):
            raise PublisherTranscriptMalformed("SRT cue 缺少时间码")
        match = _TIMING_LINE.fullmatch(cue[timing_index])
        if match is None:
            raise PublisherTranscriptMalformed("SRT cue 包含畸形时间码")
        start, end = _timestamp(match.group(1)), _timestamp(match.group(2))
        if end <= start or start < previous_start:
            raise PublisherTranscriptMalformed("SRT cue 时间码倒序或区间无效")
        previous_start = start
        text = _clean_lines(cue[timing_index + 1 :])
        if not text:
            raise PublisherTranscriptMalformed("SRT cue 内容为空")
        texts.append(text)
        if len(texts) > max_segments:
            raise PublisherTranscriptMalformed("发布者逐字稿超过 segment 数量上限")
    return _bounded_segments(
        texts,
        source_text=value,
        max_segments=max_segments,
        max_text_chars=max_text_chars,
    )


def _json_time(segment: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in segment:
            return segment[name]
    return None


def _parse_json(value: str, *, max_segments: int, max_text_chars: int) -> ParsedTranscript:
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PublisherTranscriptMalformed("Podcasting 2.0 JSON 无法解析") from exc
    if isinstance(document, dict):
        segments = document.get("segments")
    else:
        segments = document
    if not isinstance(segments, list):
        raise PublisherTranscriptMalformed("Podcasting 2.0 JSON 缺少 segments 数组")
    if len(segments) > max_segments:
        raise PublisherTranscriptMalformed("发布者逐字稿超过 segment 数量上限")
    texts: list[str] = []
    previous_start = -1.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise PublisherTranscriptMalformed("Podcasting 2.0 segment 必须是对象")
        body = segment.get("body", segment.get("text"))
        if not isinstance(body, str):
            raise PublisherTranscriptMalformed("Podcasting 2.0 segment 缺少正文")
        start_value = _json_time(segment, ("startTime", "start_time", "start"))
        end_value = _json_time(segment, ("endTime", "end_time", "end"))
        if (start_value is None) != (end_value is None):
            raise PublisherTranscriptMalformed("Podcasting 2.0 segment 时间码不完整")
        if start_value is not None:
            start, end = _timestamp(start_value), _timestamp(end_value)
            if end <= start or start < previous_start:
                raise PublisherTranscriptMalformed(
                    "Podcasting 2.0 segment 时间码倒序或区间无效"
                )
            previous_start = start
        text = _clean_lines([body])
        if not text:
            raise PublisherTranscriptMalformed("Podcasting 2.0 segment 内容为空")
        speaker = str(segment.get("speaker") or "").strip()
        texts.append(f"{speaker}: {text}" if speaker else text)
    return _bounded_segments(
        texts,
        source_text=value,
        max_segments=max_segments,
        max_text_chars=max_text_chars,
    )


def parse_transcript(
    body: bytes,
    format: str,
    *,
    max_segments: int,
    max_text_chars: int,
) -> ParsedTranscript:
    value = _decode(body, max_text_chars=max_text_chars)
    if format == "vtt":
        return _parse_vtt(value, max_segments=max_segments, max_text_chars=max_text_chars)
    if format == "srt":
        return _parse_srt(value, max_segments=max_segments, max_text_chars=max_text_chars)
    if format == "json":
        return _parse_json(value, max_segments=max_segments, max_text_chars=max_text_chars)
    if format == "text":
        probe = value.lstrip().lower()
        if probe.startswith("<!doctype") or probe.startswith("<html"):
            raise PublisherTranscriptMalformed("plain text locator 返回了 HTML 页面")
        lines = [_SPACE.sub(" ", line.strip()) for line in value.split("\n") if line.strip()]
        return _bounded_segments(
            lines,
            source_text=value,
            max_segments=max_segments,
            max_text_chars=max_text_chars,
        )
    raise PublisherTranscriptMalformed("不支持的发布者逐字稿格式")


def _episode_and_candidate(
    session: Session, episode_id: str
) -> tuple[ArticleRecord, TranscriptCandidate]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None or episode.content_type != "podcast_episode":
        raise PublisherTranscriptNotFound("Podcast 单集不存在")
    try:
        extensions = json.loads(episode.extensions_json or "{}")
    except json.JSONDecodeError as exc:
        raise PublisherTranscriptMalformed("Podcast 单集的逐字稿元数据无效") from exc
    if not isinstance(extensions, dict):
        raise PublisherTranscriptMalformed("Podcast 单集的逐字稿元数据无效")
    return episode, select_candidate(extensions.get("transcripts"))


def publisher_transcript_refresh_revision(
    engine: Engine, *, episode_id: str
) -> str | None:
    """Return the changed locator revision, ``""`` if current, or None if absent."""

    with Session(engine) as session:
        try:
            _episode, candidate = _episode_and_candidate(session, episode_id)
        except PublisherTranscriptError:
            return None
        locator_hash = hashlib.sha256(candidate.url.encode("utf-8")).hexdigest()
        publication = session.get(
            PodcastTextPublicationRecord, f"{episode_id}:{KIND}"
        )
        artifact = (
            session.get(PodcastTextArtifactRecord, publication.artifact_id)
            if publication is not None and publication.status == "published"
            else None
        )
        if artifact is None:
            return locator_hash
        try:
            provenance = json.loads(artifact.provenance_json or "{}")
        except (TypeError, ValueError):
            return locator_hash
        return "" if provenance.get("url_sha256") == locator_hash else locator_hash


def publisher_artifact_matches_current_locator(
    session: Session,
    *,
    episode_id: str,
    artifact: PodcastTextArtifactRecord,
) -> bool:
    """Whether a published artifact was fetched from the current RSS locator."""

    try:
        _episode, candidate = _episode_and_candidate(session, episode_id)
        provenance = json.loads(artifact.provenance_json or "{}")
    except PublisherTranscriptNotFound:
        # Synced/manual publications may not retain a live RSS locator.
        return True
    except (PublisherTranscriptError, TypeError, ValueError):
        return False
    return provenance.get("url_sha256") == hashlib.sha256(
        candidate.url.encode("utf-8")
    ).hexdigest()


def _serialize(
    artifact: PodcastTextArtifactRecord,
    publication: PodcastTextPublicationRecord,
    *,
    created: bool,
    candidate: TranscriptCandidate,
) -> dict[str, Any]:
    try:
        provenance = json.loads(artifact.provenance_json)
    except (TypeError, ValueError):  # legacy evidence remains readable, never trusted
        provenance = {}
    return {
        "created": created,
        "artifact": {
            "id": artifact.id,
            "episode_id": artifact.episode_id,
            "kind": artifact.kind,
            "version": artifact.version,
            "content_hash": artifact.content_hash,
            "language": artifact.language,
            "created_at": artifact.created_at,
        },
        "publication": {
            "identity": publication.identity,
            "artifact_id": publication.artifact_id,
            "status": publication.status,
            "published_at": publication.published_at,
            "updated_at": publication.updated_at,
        },
        "source": {
            "mime": candidate.mime,
            "format": candidate.format,
            "language": candidate.language,
            "metadata_index": candidate.metadata_index,
        },
        "validation": {
            "segment_count": provenance.get("segment_count", 0),
            "plain_text_chars": provenance.get("plain_text_chars", 0),
            "source_text_chars": len(artifact.inline_text),
        },
        "provider_calls": 0,
    }


def _commit_publication(
    engine: Engine,
    *,
    episode_id: str,
    candidate: TranscriptCandidate,
    parsed: ParsedTranscript,
    raw_hash: str,
    policy: PodcastStagePolicy,
) -> dict[str, Any]:
    try:
        validate_text_artifact(
            parsed.source_text,
            max_chars=policy.config.text_artifact_max_chars,
            max_bytes=policy.config.text_artifact_max_bytes,
        )
    except PodcastTextLimitExceeded as exc:
        raise PublisherTranscriptMalformed(str(exc)) from exc
    content_hash = hashlib.sha256(parsed.source_text.encode("utf-8")).hexdigest()
    with Session(engine) as session:
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            session.exec(
                select(ArticleRecord)
                .where(ArticleRecord.id == episode_id)
                .with_for_update()
            ).first()
        else:  # serialize version assignment on databases with row locking
            session.exec(
                select(ArticleRecord)
                .where(ArticleRecord.id == episode_id)
                .with_for_update()
            ).first()
        episode, current_candidate = _episode_and_candidate(session, episode_id)
        if current_candidate != candidate:
            raise PublisherTranscriptConflict(
                "逐字稿元数据已在下载期间变化，请重试"
            )
        policy.require_artifact_writer(KIND, boundary="commit")
        identity = f"{episode.id}:{KIND}"
        publication = session.get(PodcastTextPublicationRecord, identity)
        if publication is not None and publication.authority_id:
            raise PublisherTranscriptConflict(
                "远端权威占用逐字稿发布槽，本机不能覆盖"
            )
        current = (
            session.get(PodcastTextArtifactRecord, publication.artifact_id)
            if publication is not None
            else None
        )
        if (
            current is not None
            and current.content_hash == content_hash
            and publication.status == "published"
        ):
            return _serialize(
                current, publication, created=False, candidate=candidate
            )
        latest_version = session.exec(
            select(func.max(PodcastTextArtifactRecord.version)).where(
                PodcastTextArtifactRecord.episode_id == episode.id,
                PodcastTextArtifactRecord.kind == KIND,
            )
        ).one()
        now = _now()
        provenance = json.dumps(
            {
                "format": candidate.format,
                "mime": candidate.mime,
                "plain_text_chars": len(parsed.text),
                "producer_authority_id": policy.config.authority_id,
                "raw_sha256": raw_hash,
                "segment_count": parsed.segment_count,
                "source": "podcast:transcript",
                "url_sha256": hashlib.sha256(candidate.url.encode("utf-8")).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        artifact = PodcastTextArtifactRecord(
            id=f"podcast-text-{uuid.uuid4().hex}",
            episode_id=episode.id,
            kind=KIND,
            version=int(latest_version or 0) + 1,
            content_hash=content_hash,
            inline_text=parsed.source_text,
            language=candidate.language,
            # Empty means locally authored. Sync v3 injects its stable producer
            # authority on import; setting the installation authority here would
            # cause the exporter to exclude its own record as remote-owned.
            authority_id="",
            source_artifact_id=None,
            source_content_hash=raw_hash,
            provenance_json=provenance,
            created_at=now,
        )
        session.add(artifact)
        session.flush()
        if publication is None:
            publication = PodcastTextPublicationRecord(
                identity=identity,
                episode_id=episode.id,
                kind=KIND,
                artifact_id=artifact.id,
                status="published",
                authority_id="",
                published_at=now,
                unpublished_at=None,
                updated_at=now,
            )
        else:
            publication.artifact_id = artifact.id
            publication.status = "published"
            publication.published_at = now
            publication.unpublished_at = None
            publication.updated_at = now
        session.add(publication)
        session.commit()
        session.refresh(artifact)
        session.refresh(publication)
        return _serialize(artifact, publication, created=True, candidate=candidate)


async def ingest_publisher_transcript(
    engine: Engine,
    *,
    episode_id: str,
    config: PodcastConfig,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch, parse, and atomically publish one official transcript."""

    policy = PodcastStagePolicy(config)
    if config.installation != "external":
        raise PublisherTranscriptConflict(
            "发布者逐字稿只能由 external Podcast installation 采集"
        )
    policy.require_artifact_writer(KIND, boundary="enqueue")
    with Session(engine) as session:
        episode, candidate = _episode_and_candidate(session, episode_id)

    policy.require_artifact_writer(KIND, boundary="provider_submit")
    with Session(engine) as session:
        episode, current_candidate = _episode_and_candidate(session, episode_id)
        if current_candidate != candidate:
            raise PublisherTranscriptConflict("逐字稿元数据已变化，请重试")
    try:
        # The helper enforces the same deadline per hop; the outer wait also
        # bounds its DNS safety resolution, which precedes httpx's own timeout.
        body = await asyncio.wait_for(
            http_safety.fetch_public_bytes_limited(
                client,
                candidate.url,
                max_bytes=min(
                    config.transcript_max_bytes,
                    config.text_artifact_max_bytes,
                ),
                timeout_seconds=config.transcript_timeout_seconds,
            ),
            timeout=config.transcript_timeout_seconds,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise PublisherTranscriptTimeout("发布者逐字稿下载超时") from exc
    except httpx.HTTPError as exc:
        raise PublisherTranscriptFetchFailed("发布者逐字稿上游请求失败") from exc
    except ValueError as exc:
        message = str(exc)
        if "大小上限" in message:
            raise PublisherTranscriptTooLarge("发布者逐字稿超过大小上限") from exc
        if "超时上限" in message:
            raise PublisherTranscriptTimeout("发布者逐字稿下载超时") from exc
        raise PublisherTranscriptMalformed(
            "发布者逐字稿下载失败或超过安全限制"
        ) from exc
    parsed = parse_transcript(
        body,
        candidate.format,
        max_segments=config.transcript_max_segments,
        max_text_chars=min(
            config.transcript_max_text_chars,
            config.text_artifact_max_chars,
        ),
    )
    return _commit_publication(
        engine,
        episode_id=episode_id,
        candidate=candidate,
        parsed=parsed,
        raw_hash=hashlib.sha256(body).hexdigest(),
        policy=policy,
    )
