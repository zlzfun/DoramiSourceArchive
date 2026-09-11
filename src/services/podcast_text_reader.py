"""Read-only, fail-closed Podcast text projection for Reader clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
from typing import Any

from sqlmodel import Session

from config import PodcastConfig
from models.db import (
    ArticleRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services import source_visibility, user_sources
from services.podcast_publisher_transcripts import (
    PublisherTranscriptMalformed,
    parse_transcript,
)
from services.podcast_normalized_transcripts import (
    NormalizedTranscriptError,
    canonical_normalized_transcript,
)
from services.podcast_text_limits import (
    PodcastTextLimitExceeded,
    validate_text_artifact,
)


READER_TEXT_KINDS = (
    "digest_blog_zh",
    "transcript_zh",
    "publisher_transcript",
    "normalized_transcript",
)
_KIND_LABEL = {
    "digest_blog_zh": "AI 整理",
    "transcript_zh": "AI 整理的中文逐字稿",
    "publisher_transcript": "来源逐字稿",
    "normalized_transcript": "ASR 逐字稿",
}


class PodcastTextReaderError(RuntimeError):
    pass


class PodcastTextReaderNotFound(PodcastTextReaderError):
    pass


class PodcastTextReaderBadRequest(PodcastTextReaderError):
    pass


class PodcastTextReaderMalformed(PodcastTextReaderError):
    pass


def _safe_string(value: Any, *, maximum: int = 160) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    # Identifiers and untrusted producer notes must not smuggle terminal/UI
    # controls or Unicode direction overrides into Reader surfaces or logs.
    cleaned = "".join(
        character
        for character in str(value)
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
    )
    return " ".join(cleaned.split()).strip()[:maximum]


def _provenance_summary(artifact: PodcastTextArtifactRecord) -> dict[str, str]:
    """Return an allowlisted summary; raw provider metadata/URLs never cross Reader."""

    try:
        raw = json.loads(artifact.provenance_json or "{}")
    except (TypeError, ValueError, RecursionError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    # Only the database authority column is authenticated by the publication's
    # composite FK. provenance_json is producer-controlled explanatory data.
    authority = _safe_string(artifact.authority_id)
    pipeline = _safe_string(raw.get("pipeline_version") or raw.get("pipeline"))
    result = {
        "label": _KIND_LABEL[artifact.kind],
        "origin": (
            "publisher"
            if artifact.kind == "publisher_transcript"
            else "asr"
            if artifact.kind == "normalized_transcript"
            else "ai"
        ),
    }
    if authority:
        result["producer_authority_id"] = authority
    if pipeline:
        result["pipeline_note"] = pipeline
    return result


def _publisher_format(artifact: PodcastTextArtifactRecord) -> str:
    try:
        raw = json.loads(artifact.provenance_json or "{}")
    except (TypeError, ValueError, RecursionError):
        raw = {}
    value = _safe_string(raw.get("format") if isinstance(raw, dict) else "").lower()
    if value in {"vtt", "srt", "json", "text"}:
        return value
    probe = artifact.inline_text.lstrip()
    if probe.startswith("WEBVTT"):
        return "vtt"
    if probe.startswith(("{", "[")):
        return "json"
    if "-->" in probe[:500]:
        return "srt"
    return "text"


def _plain_text(artifact: PodcastTextArtifactRecord, config: PodcastConfig) -> str:
    try:
        validate_text_artifact(
            artifact.inline_text,
            max_chars=config.text_artifact_max_chars,
            max_bytes=config.text_artifact_max_bytes,
        )
    except PodcastTextLimitExceeded as exc:
        raise PodcastTextReaderMalformed(str(exc)) from exc
    if artifact.kind == "normalized_transcript":
        try:
            document = json.loads(artifact.inline_text)
            canonical = canonical_normalized_transcript(document)
            projected = json.loads(canonical)["text"]
            validate_text_artifact(
                projected,
                max_chars=config.text_artifact_max_chars,
                max_bytes=config.text_artifact_max_bytes,
            )
            return projected
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            NormalizedTranscriptError,
            PodcastTextLimitExceeded,
        ) as exc:
            raise PodcastTextReaderMalformed(
                "已发布的 ASR 逐字稿无法生成安全纯文本投影"
            ) from exc
    if artifact.kind != "publisher_transcript":
        return artifact.inline_text
    try:
        projected = parse_transcript(
            artifact.inline_text.encode("utf-8"),
            _publisher_format(artifact),
            max_segments=config.transcript_max_segments,
            max_text_chars=min(
                config.transcript_max_text_chars,
                config.text_artifact_max_chars,
            ),
        ).text
        validate_text_artifact(
            projected,
            max_chars=config.text_artifact_max_chars,
            max_bytes=config.text_artifact_max_bytes,
        )
        return projected
    except (
        PodcastTextLimitExceeded,
        PublisherTranscriptMalformed,
        UnicodeEncodeError,
    ) as exc:
        raise PodcastTextReaderMalformed(
            "已发布的来源逐字稿无法生成安全纯文本投影"
        ) from exc


def _cursor_encode(
    artifact: PodcastTextArtifactRecord,
    *,
    offset: int,
    query: str,
    secret: str,
) -> str:
    payload = json.dumps(
        {
            "v": 2,
            "a": artifact.id,
            "h": artifact.content_hash,
            "k": artifact.kind,
            "o": offset,
            "q": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"),
        f"podcast-text-cursor-v2.{encoded}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    encoded_signature = (
        base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    )
    return f"{encoded}.{encoded_signature}"


def _cursor_offset(
    cursor: str,
    artifact: PodcastTextArtifactRecord,
    *,
    query: str,
    text_length: int,
    secret: str,
) -> int:
    if not cursor:
        return 0
    if len(cursor) > 1024:
        raise PodcastTextReaderBadRequest("Podcast 文本游标无效")
    try:
        encoded, encoded_signature = cursor.split(".", 1)
        padded_signature = encoded_signature + "=" * (-len(encoded_signature) % 4)
        signature = base64.b64decode(
            padded_signature.encode("ascii"), altchars=b"-_", validate=True
        )
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            f"podcast-text-cursor-v2.{encoded}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("signature mismatch")
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(
            base64.b64decode(
                padded.encode("ascii"), altchars=b"-_", validate=True
            ).decode("utf-8")
        )
        offset = payload["o"]
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise PodcastTextReaderBadRequest("Podcast 文本游标无效") from exc
    expected = {
        "v": 2,
        "a": artifact.id,
        "h": artifact.content_hash,
        "k": artifact.kind,
        "q": hashlib.sha256(query.encode("utf-8")).hexdigest(),
    }
    if (
        not isinstance(payload, dict)
        or any(payload.get(key) != value for key, value in expected.items())
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or offset > text_length
    ):
        raise PodcastTextReaderBadRequest("Podcast 文本游标已失效")
    return offset


def _page(
    artifact: PodcastTextArtifactRecord,
    *,
    text: str,
    query: str,
    cursor: str,
    limit: int,
    cursor_secret: str,
) -> dict[str, Any] | None:
    offset = _cursor_offset(
        cursor,
        artifact,
        query=query,
        text_length=len(text),
        secret=cursor_secret,
    )
    if query:
        # Regex IGNORECASE reports spans in the original string. Searching a
        # lower()/casefold() copy is incorrect for characters whose folded form
        # changes length (for example U+0130).
        matcher = re.compile(re.escape(query), re.IGNORECASE)
        match = matcher.search(text, offset)
        if match is None:
            return None
        start = max(0, match.start() - limit // 5)
        end = min(len(text), start + limit)
        next_offset = match.end()
        has_more = matcher.search(text, next_offset) is not None
    else:
        start = offset
        end = min(len(text), start + limit)
        next_offset = end
        has_more = end < len(text)
    return {
        "artifact_id": artifact.id,
        "content_hash": artifact.content_hash,
        "kind": artifact.kind,
        "language": artifact.language,
        "text_format": "plain",
        "text": text[start:end],
        "total_chars": len(text),
        "range_start": start,
        "range_end": end,
        "next_cursor": (
            _cursor_encode(
                artifact,
                offset=next_offset,
                query=query,
                secret=cursor_secret,
            )
            if has_more
            else None
        ),
        "source_artifact_id": artifact.source_artifact_id,
        "source_content_hash": artifact.source_content_hash,
        "created_at": artifact.created_at,
        "provenance": _provenance_summary(artifact),
    }


def read_episode_texts(
    session: Session,
    *,
    episode_id: str,
    username: str,
    config: PodcastConfig,
    cursor_secret: str,
    kind: str = "",
    query: str = "",
    cursor: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """Project current publications without enqueuing or invoking any provider."""

    if not cursor_secret:
        raise PodcastTextReaderMalformed("Podcast Reader 游标签名密钥未配置")
    # Explicitly establish a request-wide snapshot. SQLite legacy transaction
    # mode does not start one for SELECT; PostgreSQL READ COMMITTED otherwise
    # permits publication state to change between checks.
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )

    if kind and kind not in READER_TEXT_KINDS:
        raise PodcastTextReaderBadRequest("不支持的 Reader Podcast 文本类型")
    if cursor and not kind:
        raise PodcastTextReaderBadRequest("使用游标时必须指定 kind")
    query = (query or "").strip()
    if len(query) > config.reader_text_query_max_chars:
        raise PodcastTextReaderBadRequest("Podcast 文本搜索词超过配置上限")
    page_limit = limit or config.reader_text_default_chars
    if page_limit <= 0 or page_limit > config.reader_text_max_chars:
        raise PodcastTextReaderBadRequest("Podcast 文本分页大小超过配置上限")
    if query and page_limit < len(query):
        raise PodcastTextReaderBadRequest("Podcast 文本分页大小不能小于搜索词")

    episode = session.get(ArticleRecord, episode_id)
    if episode is None or episode.content_type != "podcast_episode":
        raise PodcastTextReaderNotFound("Podcast 单集不存在")
    if episode.source_id in source_visibility.reader_unavailable_source_ids(session):
        raise PodcastTextReaderNotFound("Podcast 单集不存在")
    if user_sources.is_user_source(episode.source_id) and (
        not username
        or user_sources.unauthorized_user_source_ids(
            session, username, [episode.source_id]
        )
    ):
        raise PodcastTextReaderNotFound("Podcast 单集不存在")

    allowed = (kind,) if kind else READER_TEXT_KINDS

    items = []
    for candidate in allowed:
        publication = session.get(
            PodcastTextPublicationRecord, f"{episode.id}:{candidate}"
        )
        if publication is None or publication.status != "published":
            continue
        artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
        if (
            artifact is None
            or artifact.episode_id != episode.id
            or artifact.kind != candidate
            or artifact.authority_id != publication.authority_id
            or not publication.published_at
            or hashlib.sha256(artifact.inline_text.encode("utf-8")).hexdigest()
            != artifact.content_hash
        ):
            continue
        projected = _page(
            artifact,
            text=_plain_text(artifact, config),
            query=query,
            cursor=cursor if kind else "",
            limit=page_limit,
            cursor_secret=cursor_secret,
        )
        if projected is not None:
            items.append(projected)
    return {
        "episode_id": episode.id,
        "query": query or None,
        "items": items,
    }
