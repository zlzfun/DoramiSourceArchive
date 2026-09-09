"""Minimal end-to-end premium Podcast guide orchestration.

The orchestration depends on provider-neutral text and speech ports.  Vendor
adapters live elsewhere, so changing the LLM or TTS service does not change the
artifact/publication workflow.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services.podcast_artifacts import (
    PodcastArtifactStore,
    withdraw_digest_audio_for_script_change,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services.article_analysis import has_authoritative_analysis


@dataclass(frozen=True)
class PremiumGuideDraft:
    blog_markdown: str


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    mime: str
    provider_task_id: str = ""


class PremiumGuideTextProvider(Protocol):
    async def create_blog(
        self, *, title: str, transcript: str, max_chars: int
    ) -> PremiumGuideDraft: ...

    async def create_narration(
        self, *, title: str, blog_markdown: str, max_chars: int, max_minutes: int
    ) -> str: ...


class PremiumGuideTtsProvider(Protocol):
    async def synthesize(self, text: str) -> SynthesizedAudio: ...


class PremiumGuideError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _extensions(article: ArticleRecord) -> dict:
    try:
        value = json.loads(article.extensions_json or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _set_episode_status(
    engine: Engine,
    episode_id: str,
    status: str,
    *,
    error: str = "",
    audio: PodcastArtifactRecord | None = None,
) -> None:
    with Session(engine) as session:
        episode = session.get(ArticleRecord, episode_id)
        if episode is None:
            return
        extensions = _extensions(episode)
        extensions["processing_status"] = status
        guide = extensions.get("premium_guide")
        if not isinstance(guide, dict):
            guide = {}
        guide.update({"status": status, "updated_at": _now()})
        if error:
            guide["error"] = error[:300]
        else:
            guide.pop("error", None)
        if audio is not None:
            guide["audio_artifact_id"] = audio.id
            extensions["condensed_audio_url"] = (
                f"/api/reader/podcast-artifacts/{audio.id}/audio"
            )
            extensions["condensed_duration_seconds"] = audio.duration_seconds
        extensions["premium_guide"] = guide
        episode.extensions_json = json.dumps(
            extensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        session.add(episode)
        session.commit()


def _publish_text(
    session: Session,
    *,
    episode: ArticleRecord,
    kind: str,
    text: str,
    source_artifact: PodcastTextArtifactRecord,
    pipeline_version: str,
) -> PodcastTextArtifactRecord:
    canonical = text.strip()
    if not canonical:
        raise PremiumGuideError(f"{kind} 生成结果为空")
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    identity = f"{episode.id}:{kind}"
    current_publication = session.get(PodcastTextPublicationRecord, identity)
    current = (
        session.get(PodcastTextArtifactRecord, current_publication.artifact_id)
        if current_publication is not None
        else None
    )
    if current is not None and current.content_hash == content_hash:
        return current

    version = session.exec(
        select(func.max(PodcastTextArtifactRecord.version)).where(
            PodcastTextArtifactRecord.episode_id == episode.id,
            PodcastTextArtifactRecord.kind == kind,
        )
    ).one()
    stamp = _now()
    artifact = PodcastTextArtifactRecord(
        id=f"podcast-guide-{uuid.uuid4().hex}",
        episode_id=episode.id,
        kind=kind,
        version=int(version or 0) + 1,
        content_hash=content_hash,
        inline_text=canonical,
        language="zh-CN",
        authority_id="",
        source_artifact_id=source_artifact.id,
        source_content_hash=source_artifact.content_hash,
        provenance_json=json.dumps(
            {"pipeline": pipeline_version, "source": "premium_guide"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        created_at=stamp,
    )
    session.add(artifact)
    session.flush()
    if current_publication is None:
        current_publication = PodcastTextPublicationRecord(
            identity=identity,
            episode_id=episode.id,
            kind=kind,
            artifact_id=artifact.id,
            status="published",
            authority_id="",
            published_at=stamp,
            updated_at=stamp,
        )
    else:
        current_publication.artifact_id = artifact.id
        current_publication.status = "published"
        current_publication.authority_id = ""
        current_publication.published_at = stamp
        current_publication.unpublished_at = None
        current_publication.updated_at = stamp
    session.add(current_publication)
    if kind == "narration_script_zh":
        withdraw_digest_audio_for_script_change(
            session, episode_id=episode.id, current_artifact_id=artifact.id
        )
    return artifact


def _source_transcript(
    session: Session, episode_id: str
) -> tuple[ArticleRecord, PodcastTextArtifactRecord, str]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None or episode.content_type != "podcast_episode":
        raise PremiumGuideError("播客单集不存在")
    duration = float(_extensions(episode).get("duration_seconds") or 0)
    if duration <= 0:
        raise PremiumGuideError("播客时长未知，暂不触发精品导读")
    artifact = session.exec(
        select(PodcastTextArtifactRecord)
        .where(
            PodcastTextArtifactRecord.episode_id == episode_id,
            PodcastTextArtifactRecord.kind == "normalized_transcript",
        )
        .order_by(
            PodcastTextArtifactRecord.version.desc(),
            PodcastTextArtifactRecord.created_at.desc(),
        )
    ).first()
    if artifact is None:
        raise PremiumGuideError("ASR 转录尚未完成")
    try:
        document = json.loads(artifact.inline_text)
        transcript = str(document.get("text") or "").strip()
    except (AttributeError, TypeError, ValueError):
        transcript = ""
    if not transcript:
        raise PremiumGuideError("ASR 转录内容为空")
    return episode, artifact, transcript


async def run_premium_guide(
    engine: Engine,
    store: PodcastArtifactStore,
    *,
    episode_id: str,
    config: PodcastConfig,
    text_provider: PremiumGuideTextProvider,
    tts_provider: PremiumGuideTtsProvider,
) -> dict:
    """Publish a guide using, but never replacing, the authoritative assessment."""

    policy = PodcastStagePolicy(config)
    for stage in (
        "translate",
        "analyze",
        "digest",
        "script",
        "tts",
        "audio_qa",
        "local_publish",
    ):
        policy.require_stage(stage, boundary="provider_submit")
    _set_episode_status(engine, episode_id, "summarizing")
    try:
        with Session(engine) as session:
            episode, transcript_artifact, transcript = _source_transcript(
                session, episode_id
            )
            analysis = session.get(ArticleAnalysisRecord, episode_id)
            if not has_authoritative_analysis(analysis):
                raise PremiumGuideError("播客简介初评尚未完成")
            score = float(analysis.quality_score)
            title = episode.title
            duration = float(_extensions(episode).get("duration_seconds") or 0)
        if duration <= config.premium_min_duration_seconds:
            _set_episode_status(engine, episode_id, "not_required")
            return {
                "episode_id": episode_id,
                "is_premium": False,
                "score": None,
                "reason": "duration_not_over_minimum",
            }
        if config.premium_guide_mode != "solo_preview":
            raise PremiumGuideError("当前原型仅开放单人速览模式")
        if score <= config.premium_score_threshold:
            _set_episode_status(engine, episode_id, "not_required")
            return {"episode_id": episode_id, "is_premium": False, "score": score}
        draft = await text_provider.create_blog(
            title=title,
            transcript=transcript[: config.premium_transcript_max_chars],
            max_chars=config.premium_blog_max_chars,
        )
        blog = draft.blog_markdown.strip()[: config.premium_blog_max_chars]
        with Session(engine) as session:
            episode = session.get(ArticleRecord, episode_id)
            if episode is None:
                raise PremiumGuideError("播客分析记录不存在")
            blog_artifact = _publish_text(
                session,
                episode=episode,
                kind="digest_blog_zh",
                text=blog,
                source_artifact=transcript_artifact,
                pipeline_version=config.text_pipeline_version,
            )
            blog_artifact_id = blog_artifact.id
            session.commit()

        narration = await text_provider.create_narration(
            title=title,
            blog_markdown=blog,
            max_chars=config.premium_narration_max_chars,
            max_minutes=config.premium_max_audio_minutes,
        )
        with Session(engine) as session:
            episode = session.get(ArticleRecord, episode_id)
            blog_artifact = session.get(
                PodcastTextArtifactRecord, blog_artifact_id
            )
            if episode is None or blog_artifact is None:
                raise PremiumGuideError("播客在导读生成期间被删除")
            narration_artifact = _publish_text(
                session,
                episode=episode,
                kind="narration_script_zh",
                text=narration[: config.premium_narration_max_chars],
                source_artifact=blog_artifact,
                pipeline_version=config.text_pipeline_version,
            )
            narration_id = narration_artifact.id
            narration_hash = narration_artifact.content_hash
            narration_text = narration_artifact.inline_text
            session.commit()

        _set_episode_status(engine, episode_id, "synthesizing")
        synthesized = await tts_provider.synthesize(narration_text)
        audio = await asyncio.to_thread(
            store.import_bytes,
            episode_id=episode_id,
            kind="digest_audio_zh",
            data=synthesized.data,
            declared_mime=synthesized.mime,
            provenance="premium_guide_tts",
            authority_id="",
            narration_artifact_id=narration_id,
            narration_content_hash=narration_hash,
        )
        if (
            audio.duration_seconds is not None
            and audio.duration_seconds > config.premium_max_audio_minutes * 60
        ):
            await asyncio.to_thread(store.withdraw, audio.id)
            raise PremiumGuideError(
                f"单人速览音频超过 {config.premium_max_audio_minutes} 分钟"
            )
        audio = await asyncio.to_thread(
            store.publish, audio.id, expected_updated_at=audio.updated_at
        )
        _set_episode_status(engine, episode_id, "ready", audio=audio)
        return {
            "episode_id": episode_id,
            "is_premium": True,
            "score": score,
            "audio_artifact_id": audio.id,
            "duration_seconds": audio.duration_seconds,
        }
    except Exception as exc:
        _set_episode_status(engine, episode_id, "failed", error=str(exc))
        raise


def list_premium_guide_tasks(
    engine: Engine,
    *,
    threshold: float,
    mode: str = "solo_preview",
    page: int = 1,
    page_size: int = 100,
) -> dict:
    if page < 1:
        raise ValueError("page must be at least 1")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    with Session(engine) as session:
        premium_filter = (
            ArticleRecord.content_type == "podcast_episode",
            ArticleAnalysisRecord.quality_score.is_not(None),
            ArticleAnalysisRecord.quality_score > threshold,
            or_(
                ArticleAnalysisRecord.status == "succeeded",
                ArticleAnalysisRecord.analyzed_at.is_not(None),
            ),
        )
        total = session.exec(
            select(func.count())
            .select_from(ArticleRecord)
            .join(
                ArticleAnalysisRecord,
                ArticleAnalysisRecord.article_id == ArticleRecord.id,
            )
            .where(*premium_filter)
        ).one()
        rows = session.exec(
            select(ArticleRecord, ArticleAnalysisRecord)
            .join(
                ArticleAnalysisRecord,
                ArticleAnalysisRecord.article_id == ArticleRecord.id,
            )
            .where(*premium_filter)
            .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        result = []
        for episode, analysis in rows:
            score = analysis.quality_score if analysis is not None else None
            extensions = _extensions(episode)
            guide = extensions.get("premium_guide")
            if not isinstance(guide, dict):
                guide = {}
            blog_publication = session.get(
                PodcastTextPublicationRecord, f"{episode.id}:digest_blog_zh"
            )
            audio = session.exec(
                select(PodcastArtifactRecord)
                .where(
                    PodcastArtifactRecord.episode_id == episode.id,
                    PodcastArtifactRecord.kind == "digest_audio_zh",
                    PodcastArtifactRecord.status == "published",
                )
                .order_by(PodcastArtifactRecord.published_at.desc())
            ).first()
            result.append(
                {
                    "episode_id": episode.id,
                    "title": episode.title,
                    "source_id": episode.source_id,
                    "quality_score": score,
                    "is_premium": True,
                    "status": str(
                        guide.get("status")
                        or extensions.get("processing_status")
                        or "not_started"
                    ),
                    "blog_ready": bool(
                        blog_publication and blog_publication.status == "published"
                    ),
                    "audio_ready": audio is not None,
                    "error": str(guide.get("error") or ""),
                    "updated_at": str(guide.get("updated_at") or ""),
                    "mode": mode,
                }
            )
        return {
            "items": result,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }


def pending_premium_guide_candidates(
    engine: Engine, *, minimum_duration_seconds: int, score_threshold: float = 8.5
) -> list[str]:
    """Return only episodes whose authoritative score requires guide generation."""

    with Session(engine) as session:
        rows = session.exec(
            select(ArticleRecord, ArticleAnalysisRecord)
            .join(
                PodcastTextArtifactRecord,
                PodcastTextArtifactRecord.episode_id == ArticleRecord.id,
            )
            .join(
                ArticleAnalysisRecord,
                ArticleAnalysisRecord.article_id == ArticleRecord.id,
            )
            .where(
                ArticleRecord.content_type == "podcast_episode",
                PodcastTextArtifactRecord.kind == "normalized_transcript",
                ArticleAnalysisRecord.quality_score > score_threshold,
            )
        ).all()
        return [
            row.id
            for row, analysis in rows
            if has_authoritative_analysis(analysis)
            and float(_extensions(row).get("duration_seconds") or 0)
            > minimum_duration_seconds
            and str(_extensions(row).get("processing_status") or "")
            not in {"summarizing", "synthesizing", "ready", "not_required", "failed"}
        ]


__all__ = [
    "PremiumGuideDraft",
    "PremiumGuideError",
    "PremiumGuideTextProvider",
    "PremiumGuideTtsProvider",
    "SynthesizedAudio",
    "list_premium_guide_tasks",
    "pending_premium_guide_candidates",
    "run_premium_guide",
]
