"""Reader-triggered Podcast transcript translation and publication.

The translated transcript is a first-class ``transcript_zh`` artifact.  It is
never appended to ``ArticleRecord.content`` or the article translation cache.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import LLMConfig, PodcastConfig
from llm import prompts
from llm.client import ChatMessage, UsageMeta, chat_completion, client_session
from models.db import (
    ArticleRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services import podcast_text_reader
from services.podcast_text_limits import PodcastTextLimitExceeded, validate_text_artifact
from services.reader_ai import _split_for_translation, looks_chinese


SOURCE_KINDS = frozenset({"publisher_transcript", "normalized_transcript"})
OUTPUT_KIND = "transcript_zh"
PIPELINE_VERSION = "reader-transcript-translation-v2"


class PodcastTranscriptTranslationError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# Interactive requests run in one ASGI process in the supported local
# deployment.  A per-input lock prevents a double click from paying twice.  DB
# constraints and the source-fingerprint recheck remain the final publication
# fence if multiple processes race.
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _translation_lock(episode_id: str, source_kind: str) -> asyncio.Lock:
    key = (episode_id, source_kind)
    async with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _published_artifact(
    session: Session, *, episode_id: str, kind: str
) -> PodcastTextArtifactRecord | None:
    publication = session.get(PodcastTextPublicationRecord, f"{episode_id}:{kind}")
    if publication is None or publication.status != "published":
        return None
    artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
    if (
        artifact is None
        or artifact.episode_id != episode_id
        or artifact.kind != kind
        or artifact.authority_id != publication.authority_id
        or hashlib.sha256(artifact.inline_text.encode("utf-8")).hexdigest()
        != artifact.content_hash
    ):
        raise PodcastTranscriptTranslationError(
            "已发布的逐字稿制品无效，请联系管理员重新生成",
            status_code=422,
        )
    return artifact


def _cached_translation(
    session: Session, *, episode_id: str, source: PodcastTextArtifactRecord
) -> PodcastTextArtifactRecord | None:
    translated = _published_artifact(session, episode_id=episode_id, kind=OUTPUT_KIND)
    if translated is None:
        return None
    try:
        provenance = json.loads(translated.provenance_json or "{}")
    except (TypeError, ValueError, RecursionError):
        provenance = {}
    if (
        translated.source_artifact_id == source.id
        and translated.source_content_hash == source.content_hash
        and isinstance(provenance, dict)
        and provenance.get("pipeline") == PIPELINE_VERSION
    ):
        return translated
    return None


def _clean_translated_segment(text: str) -> str:
    """Drop prompt headings occasionally echoed by OpenAI-compatible models."""

    lines = text.strip().splitlines()
    while lines and (
        lines[0].strip().startswith("【播客节目】")
        or lines[0].strip() == "【待翻译逐字稿片段】"
    ):
        lines.pop(0)
    return "\n".join(lines).strip()


def _load_input(
    session: Session,
    *,
    episode_id: str,
    source_kind: str,
    config: PodcastConfig,
) -> tuple[ArticleRecord, PodcastTextArtifactRecord, str]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None or episode.content_type != "podcast_episode":
        raise PodcastTranscriptTranslationError("播客单集不存在", status_code=404)
    source = _published_artifact(session, episode_id=episode_id, kind=source_kind)
    if source is None:
        label = "来源方逐字稿" if source_kind == "publisher_transcript" else "ASR 逐字稿"
        raise PodcastTranscriptTranslationError(f"{label}尚未就绪", status_code=409)
    try:
        text = podcast_text_reader._plain_text(source, config)  # noqa: SLF001
    except podcast_text_reader.PodcastTextReaderMalformed as exc:
        raise PodcastTranscriptTranslationError(str(exc), status_code=422) from exc
    maximum = min(config.premium_transcript_max_chars, config.text_artifact_max_chars)
    if len(text) > maximum:
        raise PodcastTranscriptTranslationError(
            f"逐字稿过长（{len(text)} 字），当前单次翻译上限为 {maximum} 字",
            status_code=413,
        )
    return episode, source, text


async def _translate(
    *,
    title: str,
    text: str,
    llm_config: LLMConfig,
    usage_meta: UsageMeta,
) -> str:
    segments = _split_for_translation(text)
    concurrency = max(1, int(getattr(llm_config, "map_concurrency", 4)))
    semaphore = asyncio.Semaphore(concurrency)
    async with client_session(llm_config) as http_client:

        async def translate_segment(index: int, segment: str) -> tuple[int, str]:
            async with semaphore:
                translated = await chat_completion(
                    messages=[
                        ChatMessage(
                            role="system",
                            content=prompts.TRANSLATE_TRANSCRIPT_SYSTEM_PROMPT,
                        ),
                        ChatMessage(
                            role="user",
                            content=prompts.build_translate_transcript_user_prompt(
                                title, segment
                            ),
                        ),
                    ],
                    config=llm_config,
                    usage_meta=usage_meta,
                    http_client=http_client,
                )
            return index, _clean_translated_segment(translated)

        translated_segments = await asyncio.gather(
            *(translate_segment(index, segment) for index, segment in enumerate(segments))
        )
    ordered = [text for _, text in sorted(translated_segments)]
    if any(not segment for segment in ordered):
        raise PodcastTranscriptTranslationError("逐字稿翻译返回了空片段，请稍后重试", status_code=502)
    return "\n\n".join(ordered).strip()


def _publish(
    session: Session,
    *,
    episode_id: str,
    source: PodcastTextArtifactRecord,
    translated_text: str,
    config: PodcastConfig,
) -> PodcastTextArtifactRecord:
    try:
        validate_text_artifact(
            translated_text,
            max_chars=config.text_artifact_max_chars,
            max_bytes=config.text_artifact_max_bytes,
        )
    except PodcastTextLimitExceeded as exc:
        raise PodcastTranscriptTranslationError(
            "翻译结果超过 Podcast 文本制品上限", status_code=422
        ) from exc
    content_hash = hashlib.sha256(translated_text.encode("utf-8")).hexdigest()
    version = session.exec(
        select(func.max(PodcastTextArtifactRecord.version)).where(
            PodcastTextArtifactRecord.episode_id == episode_id,
            PodcastTextArtifactRecord.kind == OUTPUT_KIND,
        )
    ).one()
    stamp = _now()
    artifact = PodcastTextArtifactRecord(
        id=f"podcast-translated-{uuid.uuid4().hex}",
        episode_id=episode_id,
        kind=OUTPUT_KIND,
        version=int(version or 0) + 1,
        content_hash=content_hash,
        inline_text=translated_text,
        language="zh-CN",
        authority_id="",
        source_artifact_id=source.id,
        source_content_hash=source.content_hash,
        provenance_json=json.dumps(
            {
                "pipeline": PIPELINE_VERSION,
                "source": "reader_transcript_translation",
                "source_kind": source.kind,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        created_at=stamp,
    )
    session.add(artifact)
    session.flush()
    identity = f"{episode_id}:{OUTPUT_KIND}"
    publication = session.get(PodcastTextPublicationRecord, identity)
    if publication is None:
        publication = PodcastTextPublicationRecord(
            identity=identity,
            episode_id=episode_id,
            kind=OUTPUT_KIND,
            artifact_id=artifact.id,
            status="published",
            authority_id="",
            published_at=stamp,
            updated_at=stamp,
        )
    else:
        publication.artifact_id = artifact.id
        publication.status = "published"
        publication.authority_id = ""
        publication.published_at = stamp
        publication.unpublished_at = None
        publication.updated_at = stamp
    session.add(publication)
    session.commit()
    session.refresh(artifact)
    return artifact


async def translate_transcript(
    engine: Engine,
    *,
    episode_id: str,
    source_kind: str,
    config: PodcastConfig,
    llm_config: LLMConfig,
    usage_meta: UsageMeta,
) -> tuple[PodcastTextArtifactRecord, bool]:
    """Translate and publish one current transcript, returning artifact + cache flag."""

    if source_kind not in SOURCE_KINDS:
        raise PodcastTranscriptTranslationError("不支持的逐字稿来源", status_code=400)
    lock = await _translation_lock(episode_id, source_kind)
    async with lock:
        with Session(engine) as session:
            episode, source, source_text = _load_input(
                session,
                episode_id=episode_id,
                source_kind=source_kind,
                config=config,
            )
            cached = _cached_translation(session, episode_id=episode_id, source=source)
            if cached is not None:
                return cached, True
            source_id = source.id
            source_hash = source.content_hash
            title = episode.title or ""

        if looks_chinese(source_text):
            translated = source_text
        else:
            translated = await _translate(
                title=title,
                text=source_text,
                llm_config=llm_config,
                usage_meta=usage_meta,
            )

        with Session(engine) as session:
            current = _published_artifact(
                session, episode_id=episode_id, kind=source_kind
            )
            if current is None or current.id != source_id or current.content_hash != source_hash:
                raise PodcastTranscriptTranslationError(
                    "逐字稿在翻译期间已更新，请重试", status_code=409
                )
            cached = _cached_translation(session, episode_id=episode_id, source=current)
            if cached is not None:
                return cached, True
            try:
                artifact = _publish(
                    session,
                    episode_id=episode_id,
                    source=current,
                    translated_text=translated,
                    config=config,
                )
            except IntegrityError as exc:
                session.rollback()
                raise PodcastTranscriptTranslationError(
                    "逐字稿翻译正在由另一请求发布，请稍后重试", status_code=409
                ) from exc
            return artifact, False


__all__ = [
    "OUTPUT_KIND",
    "SOURCE_KINDS",
    "PodcastTranscriptTranslationError",
    "translate_transcript",
]
