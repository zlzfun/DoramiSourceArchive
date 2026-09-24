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
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import case, func, or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import PodcastConfig
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)
from services.podcast_artifacts import (
    PodcastArtifactStore,
    withdraw_digest_audio_for_script_change,
)
from services.podcast_stage_policy import PodcastStagePolicy
from services import podcast_premium, reader_ondemand
from services.article_analysis import has_authoritative_analysis
from services.bailian_speech_client import BailianSpeechError

logger = logging.getLogger("dorami.podcast_premium_guides")


@dataclass(frozen=True)
class PremiumGuideDraft:
    blog_markdown: str


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    mime: str
    provider_task_id: str = ""


@dataclass(frozen=True)
class SoloDeepDurationPlan:
    tier: str
    should_synthesize_audio: bool
    min_audio_minutes: int
    max_audio_minutes: int
    target_audio_minutes: float
    min_chars: int
    target_chars: int
    max_chars: int


@dataclass(frozen=True)
class GuideEligibility:
    mode: str
    is_premium: bool
    language: str
    language_source: str
    score: float | None
    initial_score: float | None

    @property
    def eligible(self) -> bool:
        return bool(self.mode)

    @property
    def should_synthesize_audio(self) -> bool:
        return self.mode == "solo_deep"


def _capped_plan(
    tier: str,
    natural_min: int,
    natural_max: int,
    natural_target: float,
    natural_min_chars: int,
    natural_target_chars: int,
    natural_max_chars: int,
    hard_ceiling: int,
) -> SoloDeepDurationPlan:
    max_minutes = min(natural_max, hard_ceiling)
    min_minutes = min(natural_min, max_minutes)
    if max_minutes < natural_max:
        target_minutes = round((min_minutes + max_minutes) / 2.0, 1)
        max_chars = min(natural_max_chars, int(max_minutes * 286))
        min_chars = min(natural_min_chars, int(min_minutes * 286))
        target_chars = min(natural_target_chars, int(target_minutes * 286))
    else:
        target_minutes = natural_target
        min_chars = natural_min_chars
        target_chars = natural_target_chars
        max_chars = natural_max_chars
    return SoloDeepDurationPlan(
        tier=tier,
        should_synthesize_audio=True,
        min_audio_minutes=min_minutes,
        max_audio_minutes=max_minutes,
        target_audio_minutes=target_minutes,
        min_chars=min_chars,
        target_chars=target_chars,
        max_chars=max_chars,
    )


def calculate_solo_deep_plan(
    duration_seconds: float,
    *,
    selection_override: bool = False,
    hard_max_audio_minutes: int = 15,
) -> SoloDeepDurationPlan:
    """Calculate the solo_deep tier, audio eligibility, and narration budget.

    Four tiers:
    1. < 20 min (< 1200s): blog only, no audio. (Unless selection_override is True)
    2. 20-45 min (1200s to 2700s): 5-8 min audio, 1430-2310 chars (target ~1870).
    3. 45-90 min (2700s to 5400s): 8-12 min audio, 2310-3410 chars (target ~2860).
    4. > 90 min (> 5400s): 12-15 min audio, 3410-4290 chars (target ~3850, hard ceiling 15 min).
    """
    if duration_seconds < 20 * 60 and not selection_override:
        return SoloDeepDurationPlan(
            tier="short",
            should_synthesize_audio=False,
            min_audio_minutes=0,
            max_audio_minutes=0,
            target_audio_minutes=0.0,
            min_chars=0,
            target_chars=0,
            max_chars=0,
        )
    ceiling = max(1, min(15, hard_max_audio_minutes))
    if duration_seconds < 20 * 60 and selection_override:
        return _capped_plan("tier_20_45", 5, 8, 6.5, 1430, 1870, 2310, ceiling)
    if duration_seconds <= 45 * 60:
        return _capped_plan("tier_20_45", 5, 8, 6.5, 1430, 1870, 2310, ceiling)
    if duration_seconds <= 90 * 60:
        return _capped_plan("tier_45_90", 8, 12, 10.0, 2310, 2860, 3410, ceiling)
    return _capped_plan("tier_gt_90", 12, 15, 13.5, 3410, 3850, 4290, ceiling)


class PremiumGuideTextProvider(Protocol):
    async def create_blog(
        self, *, title: str, transcript: str, max_chars: int
    ) -> PremiumGuideDraft: ...

    async def create_narration(
        self,
        *,
        title: str,
        blog_markdown: str,
        max_chars: int,
        max_minutes: int,
        **kwargs: Any,
    ) -> str: ...


class PremiumGuideTtsProvider(Protocol):
    async def synthesize(self, text: str) -> SynthesizedAudio: ...


class PremiumGuideError(RuntimeError):
    pass


class PremiumGuideForceError(PremiumGuideError):
    """Actionable rejection of an operator-requested TTS generation."""

    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _extensions(article: ArticleRecord) -> dict:
    try:
        value = json.loads(article.extensions_json or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


_UNKNOWN_LANGUAGES = frozenset({"", "und", "mul", "zxx"})
_CHINESE_LANGUAGES = frozenset({"zh", "cmn", "yue", "wuu", "nan", "hak"})


def _normalized_language(value: Any) -> str:
    return str(value or "").strip().replace("_", "-").lower()


def _known_language(value: Any) -> str:
    language = _normalized_language(value)
    return "" if language.split("-", 1)[0] in _UNKNOWN_LANGUAGES else language


def _is_non_chinese_language(value: str) -> bool:
    base = _normalized_language(value).split("-", 1)[0]
    return bool(base and base not in _UNKNOWN_LANGUAGES and base not in _CHINESE_LANGUAGES)


def resolve_episode_language(
    episode: ArticleRecord,
    transcript: PodcastTextArtifactRecord,
) -> tuple[str, str]:
    """Resolve a traceable primary language without title heuristics."""

    language = _known_language(transcript.language)
    if language:
        return language, "transcript_artifact"
    extensions = _extensions(episode)
    for key in ("language", "episode_language", "feed_language", "channel_language"):
        language = _known_language(extensions.get(key))
        if language:
            return language, f"rss_{key}"
    transcripts = extensions.get("transcripts")
    if isinstance(transcripts, dict):
        transcripts = [transcripts]
    if isinstance(transcripts, list):
        for candidate in transcripts:
            if not isinstance(candidate, dict):
                continue
            language = _known_language(candidate.get("language"))
            if language:
                return language, "rss_transcript"
    return "", "unknown"


def _guide_eligibility(
    episode: ArticleRecord,
    analysis: ArticleAnalysisRecord,
    transcript: PodcastTextArtifactRecord,
    *,
    score_threshold: float,
    selection_override: bool = False,
) -> GuideEligibility:
    score = podcast_premium.final_score(analysis)
    initial_score = podcast_premium.initial_score(analysis)
    language, language_source = resolve_episode_language(episode, transcript)
    if selection_override or (score is not None and score >= score_threshold):
        mode = "solo_deep"
    elif (
        score is not None
        and initial_score is not None
        and initial_score >= podcast_premium.INITIAL_PROCESSING_THRESHOLD
        and _is_non_chinese_language(language)
    ):
        mode = "brief_zh"
    else:
        mode = ""
    return GuideEligibility(
        mode=mode,
        is_premium=bool(score is not None and score >= score_threshold),
        language=language,
        language_source=language_source,
        score=score,
        initial_score=initial_score,
    )


def guide_eligibility(
    engine: Engine,
    *,
    episode_id: str,
    score_threshold: float,
) -> GuideEligibility:
    with Session(engine) as session:
        episode = session.get(ArticleRecord, episode_id)
        analysis = session.get(ArticleAnalysisRecord, episode_id)
        if episode is None or analysis is None or not analysis.transcript_artifact_id:
            return GuideEligibility("", False, "", "unknown", None, None)
        transcript = session.get(
            PodcastTextArtifactRecord, analysis.transcript_artifact_id
        )
        if transcript is None or not has_authoritative_analysis(analysis):
            return GuideEligibility("", False, "", "unknown", None, None)
        return _guide_eligibility(
            episode, analysis, transcript, score_threshold=score_threshold
        )


def _set_episode_status(
    engine: Engine,
    episode_id: str,
    status: str,
    *,
    error: str = "",
    failed_stage: str = "",
    audio: PodcastArtifactRecord | None = None,
    guide_metadata: dict[str, Any] | None = None,
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
        previous_status = str(guide.get("status") or "").strip()
        guide.update({"status": status, "updated_at": _now()})
        if guide_metadata:
            guide.update({
                key: value for key, value in guide_metadata.items()
                if value not in (None, "")
            })
        if status == "failed":
            effective_failed_stage = (
                str(failed_stage or "").strip()
                or (
                    previous_status
                    if previous_status in {"queued", "summarizing", "synthesizing"}
                    else str(guide.get("failed_stage") or "").strip()
                )
            )
            if effective_failed_stage:
                guide["failed_stage"] = effective_failed_stage
        else:
            guide.pop("failed_stage", None)
        if error:
            guide["error"] = error[:300]
        else:
            guide.pop("error", None)
        if audio is not None:
            guide["audio_artifact_id"] = audio.id
        extensions.pop("condensed_audio_url", None)
        extensions.pop("condensed_duration_seconds", None)
        extensions["premium_guide"] = guide
        episode.extensions_json = json.dumps(
            extensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        session.add(episode)
        session.commit()


def fail_premium_guide(
    engine: Engine,
    episode_id: str,
    error: Exception | str,
    *,
    failed_stage: str = "",
) -> None:
    """Persist a terminal guide error so admin polling never loses failures."""

    message = str(error).strip() or type(error).__name__
    if isinstance(error, BailianSpeechError):
        message = {
            "tts_receipt_cache_full": "TTS 回执缓存不足，请在播客管理台检查缺口并安全归档已完成回执",
            "tts_receipt_disk_full": "TTS 回执所在磁盘空间不足，请在播客管理台检查磁盘缺口",
        }.get(error.code, message)
    _set_episode_status(
        engine,
        episode_id,
        "failed",
        error=message,
        failed_stage=failed_stage,
    )


def _publish_text(
    session: Session,
    *,
    episode: ArticleRecord,
    kind: str,
    text: str,
    source_artifact: PodcastTextArtifactRecord,
    pipeline_version: str,
    guide_mode: str,
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
            {
                "pipeline": pipeline_version,
                "source": "premium_guide",
                "guide_mode": guide_mode,
            },
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
    session: Session, episode_id: str, config: PodcastConfig
) -> tuple[ArticleRecord, PodcastTextArtifactRecord, str]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None or episode.content_type != "podcast_episode":
        raise PremiumGuideError("播客单集不存在")
    duration = float(_extensions(episode).get("duration_seconds") or 0)
    if duration <= 0:
        raise PremiumGuideError("播客时长未知，暂不触发精品导读")
    analysis = session.get(ArticleAnalysisRecord, episode_id)
    if (
        not has_authoritative_analysis(analysis)
        or analysis.analysis_basis not in {"publisher_transcript", "asr_transcript"}
        or not analysis.transcript_artifact_id
    ):
        raise PremiumGuideError("播客全文分析尚未完成")
    artifact = session.get(PodcastTextArtifactRecord, analysis.transcript_artifact_id)
    expected_kind = (
        "publisher_transcript"
        if analysis.analysis_basis == "publisher_transcript"
        else "normalized_transcript"
    )
    if (
        artifact is None
        or artifact.episode_id != episode_id
        or artifact.kind != expected_kind
    ):
        raise PremiumGuideError("全文分析逐字稿不可用")
    try:
        from services.podcast_full_analysis import _transcript_text

        transcript = _transcript_text(artifact, config)
    except ValueError as exc:
        raise PremiumGuideError("全文分析逐字稿内容不可用") from exc
    return episode, artifact, transcript


def _reusable_guide_text(
    session: Session, episode_id: str, transcript: PodcastTextArtifactRecord,
    pipeline_version: str, guide_mode: str,
) -> tuple[PodcastTextArtifactRecord | None, PodcastTextArtifactRecord | None]:
    def published(kind: str) -> PodcastTextArtifactRecord | None:
        publication = session.get(PodcastTextPublicationRecord, f"{episode_id}:{kind}")
        if publication is None or publication.status != "published":
            return None
        return session.get(PodcastTextArtifactRecord, publication.artifact_id)

    def matches(artifact, source):
        if not artifact or artifact.source_artifact_id != source.id or artifact.source_content_hash != source.content_hash:
            return False
        try:
            provenance = json.loads(artifact.provenance_json or "{}")
            artifact_mode = str(provenance.get("guide_mode") or "solo_deep")
            return (
                provenance.get("pipeline") == pipeline_version
                and artifact_mode == guide_mode
            )
        except (TypeError, ValueError):
            return False

    blog = published("digest_blog_zh")
    if not matches(blog, transcript):
        return None, None
    narration = published("narration_script_zh")
    return blog, narration if matches(narration, blog) else None


async def run_premium_guide(
    engine: Engine,
    store: PodcastArtifactStore,
    *,
    episode_id: str,
    config: PodcastConfig,
    text_provider: PremiumGuideTextProvider,
    tts_provider: PremiumGuideTtsProvider | None = None,
    score_threshold: float | None = None,
    selection_override: bool = False,
) -> dict:
    """Publish a guide using, but never replacing, the authoritative assessment."""

    try:
        policy = PodcastStagePolicy(config)
        for stage in (
            "translate",
            "analyze",
            "digest",
            "local_publish",
        ):
            policy.require_stage(stage, boundary="provider_submit")
        with Session(engine) as session:
            episode, transcript_artifact, transcript = _source_transcript(
                session, episode_id, config
            )
            analysis = session.get(ArticleAnalysisRecord, episode_id)
            if not has_authoritative_analysis(analysis):
                raise PremiumGuideError("播客全文终评尚未完成")
            score = podcast_premium.final_score(analysis)
            if score is None:
                raise PremiumGuideError("播客全文终评尚未完成")
            title = episode.title
            duration = float(_extensions(episode).get("duration_seconds") or 0)
        if duration <= 0:
            raise PremiumGuideError("播客时长未知，暂不触发精品导读")
        if config.premium_guide_mode != "solo_deep":
            raise PremiumGuideError(
                f"当前仅开放 solo_deep 深度导读模式，当前模式为 '{config.premium_guide_mode}'"
            )
        effective_threshold = (
            config.premium_score_threshold
            if score_threshold is None
            else float(score_threshold)
        )
        eligibility = _guide_eligibility(
            episode,
            analysis,
            transcript_artifact,
            score_threshold=effective_threshold,
            selection_override=selection_override,
        )
        if not eligibility.eligible:
            _set_episode_status(engine, episode_id, "not_required")
            return {"episode_id": episode_id, "is_premium": False, "score": score}

        guide_metadata = {
            "mode": eligibility.mode,
            "language": eligibility.language,
            "language_source": eligibility.language_source,
        }
        _set_episode_status(
            engine,
            episode_id,
            "summarizing",
            guide_metadata=guide_metadata,
        )

        plan = calculate_solo_deep_plan(
            duration,
            selection_override=selection_override,
            hard_max_audio_minutes=config.premium_max_audio_minutes,
        )
        should_synthesize_audio = (
            eligibility.should_synthesize_audio and plan.should_synthesize_audio
        )

        if should_synthesize_audio:
            for stage in (
                "script",
                "tts",
                "audio_qa",
            ):
                policy.require_stage(stage, boundary="provider_submit")
            if tts_provider is None:
                raise PremiumGuideError("精品导读音频合成所需的 TTS 提供者未配置")

        with Session(engine) as session:
            blog_artifact, reusable_narration = _reusable_guide_text(
                session,
                episode_id,
                transcript_artifact,
                config.text_pipeline_version,
                eligibility.mode,
            )
        if blog_artifact is None:
            # Capacity is checked before any paid text generation. The provider
            # performs the locked final check immediately before TTS submission.
            if should_synthesize_audio and callable(getattr(tts_provider, "ensure_capacity", None)):
                try:
                    await asyncio.to_thread(tts_provider.ensure_capacity,
                        min(plan.max_chars, config.premium_narration_max_chars))
                except BailianSpeechError:
                    _set_episode_status(engine, episode_id, "synthesizing")
                    raise
            blog_max_chars = (
                min(config.premium_blog_max_chars, 3500)
                if eligibility.mode == "brief_zh"
                else config.premium_blog_max_chars
            )
            draft = await text_provider.create_blog(
                title=title,
                transcript=transcript[: config.premium_transcript_max_chars],
                max_chars=blog_max_chars,
            )
            blog = draft.blog_markdown.strip()[:blog_max_chars]
            with Session(engine) as session:
                episode = session.get(ArticleRecord, episode_id)
                if episode is None:
                    raise PremiumGuideError("播客分析记录不存在")
                blog_artifact = _publish_text(
                    session, episode=episode, kind="digest_blog_zh", text=blog,
                    source_artifact=transcript_artifact,
                    pipeline_version=config.text_pipeline_version,
                    guide_mode=eligibility.mode,
                )
                session.commit()
                session.refresh(blog_artifact)
                session.expunge(blog_artifact)
        blog_artifact_id = blog_artifact.id
        blog = blog_artifact.inline_text

        if not should_synthesize_audio:
            _set_episode_status(
                engine,
                episode_id,
                "ready",
                guide_metadata=guide_metadata,
            )
            return {
                "episode_id": episode_id,
                "is_premium": score >= effective_threshold,
                "selection_override": selection_override,
                "score": score,
                "blog_artifact_id": blog_artifact_id,
                "audio_artifact_id": None,
                "duration_seconds": None,
                "mode": eligibility.mode,
                "language": eligibility.language,
                "language_source": eligibility.language_source,
                "reason": (
                    "non_chinese_text_guide"
                    if eligibility.mode == "brief_zh"
                    else "duration_not_over_minimum"
                ),
            }

        narration_char_budget = min(plan.max_chars, config.premium_narration_max_chars)
        if reusable_narration is None:
            if callable(getattr(tts_provider, "ensure_capacity", None)):
                try:
                    await asyncio.to_thread(tts_provider.ensure_capacity, narration_char_budget)
                except BailianSpeechError:
                    _set_episode_status(engine, episode_id, "synthesizing")
                    raise
            narration = await text_provider.create_narration(
                title=title, blog_markdown=blog, max_chars=narration_char_budget,
                max_minutes=plan.max_audio_minutes, min_minutes=plan.min_audio_minutes,
                target_chars=plan.target_chars,
            )
            with Session(engine) as session:
                episode = session.get(ArticleRecord, episode_id)
                current_blog = session.get(PodcastTextArtifactRecord, blog_artifact_id)
                if episode is None or current_blog is None:
                    raise PremiumGuideError("播客在导读生成期间被删除")
                reusable_narration = _publish_text(
                    session, episode=episode, kind="narration_script_zh",
                    text=narration[: config.premium_narration_max_chars],
                    source_artifact=current_blog, pipeline_version=config.text_pipeline_version,
                    guide_mode=eligibility.mode,
                )
                session.commit()
                session.refresh(reusable_narration)
                session.expunge(reusable_narration)
        narration_id = reusable_narration.id
        narration_hash = reusable_narration.content_hash
        narration_text = reusable_narration.inline_text

        _set_episode_status(
            engine, episode_id, "synthesizing", guide_metadata=guide_metadata
        )
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
        actual_duration = float(audio.duration_seconds or 0.0)
        tier_limit_seconds = plan.max_audio_minutes * 60
        hard_limit_seconds = min(config.premium_max_audio_minutes, 15) * 60
        exceeded = (
            actual_duration > tier_limit_seconds + 15.0
            or actual_duration > hard_limit_seconds
        )
        if exceeded:
            await asyncio.to_thread(store.withdraw, audio.id)
            shorter_chars = min(int(plan.target_chars * 0.8), plan.min_chars)
            retry_narration = await text_provider.create_narration(
                title=title,
                blog_markdown=blog,
                max_chars=shorter_chars,
                max_minutes=plan.max_audio_minutes,
                min_minutes=plan.min_audio_minutes,
                target_chars=shorter_chars,
                retry_shorter=True,
            )
            with Session(engine) as session:
                episode = session.get(ArticleRecord, episode_id)
                blog_artifact = session.get(
                    PodcastTextArtifactRecord, blog_artifact_id
                )
                if episode is None or blog_artifact is None:
                    raise PremiumGuideError("播客在导读生成期间被删除")
                retry_narration_artifact = _publish_text(
                    session,
                    episode=episode,
                    kind="narration_script_zh",
                    text=retry_narration[: config.premium_narration_max_chars],
                    source_artifact=blog_artifact,
                    pipeline_version=config.text_pipeline_version,
                    guide_mode=eligibility.mode,
                )
                retry_id = retry_narration_artifact.id
                retry_hash = retry_narration_artifact.content_hash
                retry_text = retry_narration_artifact.inline_text
                session.commit()

            retry_synthesized = await tts_provider.synthesize(retry_text)
            audio2 = await asyncio.to_thread(
                store.import_bytes,
                episode_id=episode_id,
                kind="digest_audio_zh",
                data=retry_synthesized.data,
                declared_mime=retry_synthesized.mime,
                provenance="premium_guide_tts",
                authority_id="",
                narration_artifact_id=retry_id,
                narration_content_hash=retry_hash,
            )
            actual_duration2 = float(audio2.duration_seconds or 0.0)
            if (
                actual_duration2 > tier_limit_seconds + 15.0
                or actual_duration2 > hard_limit_seconds
            ):
                await asyncio.to_thread(store.withdraw, audio2.id)
                raise PremiumGuideError(
                    f"单人深度导读音频超过 {plan.max_audio_minutes} 分钟限制 (实际 {actual_duration2:.1f} 秒)"
                )
            audio = audio2

        audio = await asyncio.to_thread(
            store.publish, audio.id, expected_updated_at=audio.updated_at
        )
        _set_episode_status(
            engine,
            episode_id,
            "ready",
            audio=audio,
            guide_metadata=guide_metadata,
        )
        return {
            "episode_id": episode_id,
            "is_premium": score >= effective_threshold,
            "selection_override": selection_override,
            "score": score,
            "mode": eligibility.mode,
            "audio_artifact_id": audio.id,
            "duration_seconds": audio.duration_seconds,
        }
    except Exception as exc:
        fail_premium_guide(engine, episode_id, exc)
        raise


READER_ONDEMAND_REASON = "读者点播精品导读音频"
READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE = "评分过低，不值得点播哟～"
READER_ONDEMAND_FINAL_PENDING_MESSAGE = "全文终评还在进行中，稍后再试～"


def _latest_full_analysis_processing(
    session: Session, episode_id: str
) -> PodcastProcessingRecord | None:
    return session.exec(
        select(PodcastProcessingRecord)
        .where(
            PodcastProcessingRecord.episode_id == episode_id,
            PodcastProcessingRecord.requested_target == "full_analysis",
        )
        .order_by(
            PodcastProcessingRecord.created_at.desc(),
            PodcastProcessingRecord.id.desc(),
        )
    ).first()


def evaluate_reader_ondemand_premium_guide(
    engine: Engine,
    *,
    episode_id: str,
    config: PodcastConfig,
    actor: str,
) -> dict:
    """Inspect reader on-demand eligibility without mutating episode state.

    Outcomes:
    - ``ready`` / ``in_progress`` → reuse, do not charge
    - ``can_queue`` → caller must enforce quota, then schedule via force path
    """

    requested_by = str(actor or "").strip()
    if not requested_by:
        raise PremiumGuideForceError(
            "podcast_ondemand_request_invalid",
            "点播请求缺少读者身份",
            status_code=422,
        )

    # 阶段授权是部署边界,不是读者能处理的事:读者只看到「本部署不提供」,
    # installation / authority_id / 缺哪几个阶段留在服务端日志与管理面(issue #137)。
    missing_stages = reader_ondemand.missing_podcast_stages(config)
    if missing_stages:
        logger.warning(
            "reader on-demand premium guide denied: installation=%s authority_id=%s missing_stages=%s",
            config.installation,
            config.authority_id,
            ",".join(missing_stages),
        )
        raise PremiumGuideForceError(
            "podcast_ondemand_disabled",
            reader_ondemand.PODCAST_DISABLED_MESSAGE,
            status_code=503,
        )

    with Session(engine) as session:
        episode = session.get(ArticleRecord, episode_id)
        if episode is None or episode.content_type != "podcast_episode":
            raise PremiumGuideForceError(
                "podcast_ondemand_not_found",
                "播客单集不存在",
                status_code=404,
            )

        analysis = session.get(ArticleAnalysisRecord, episode_id)
        score = podcast_premium.final_score(analysis)
        if score is None:
            processing = _latest_full_analysis_processing(session, episode_id)
            process_status = str(
                getattr(processing, "processing_status", "") or ""
            ).strip()
            if process_status in podcast_premium.ACTIVE_PROCESSING_STATUSES:
                raise PremiumGuideForceError(
                    "podcast_ondemand_final_pending",
                    READER_ONDEMAND_FINAL_PENDING_MESSAGE,
                    status_code=409,
                )
            raise PremiumGuideForceError(
                "podcast_ondemand_score_too_low",
                READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE,
                status_code=409,
            )

        published_audio = session.exec(
            select(PodcastArtifactRecord.id).where(
                PodcastArtifactRecord.episode_id == episode_id,
                PodcastArtifactRecord.kind == "digest_audio_zh",
                PodcastArtifactRecord.status == "published",
            )
        ).first()
        extensions = _extensions(episode)
        guide = extensions.get("premium_guide")
        if not isinstance(guide, dict):
            guide = {}
        status = str(guide.get("status") or "not_started")
        if published_audio is not None:
            return {
                "episode_id": episode_id,
                "status": "ready",
                "outcome": "ready",
                "forced": True,
                "charged": False,
                "should_schedule": False,
            }
        if status in {"queued", "summarizing", "synthesizing"}:
            return {
                "episode_id": episode_id,
                "status": status,
                "outcome": "in_progress",
                "forced": True,
                "charged": False,
                "should_schedule": False,
            }

        try:
            _source_transcript(session, episode_id, config)
        except PremiumGuideError as exc:
            message = str(exc)
            status_code = 404 if message == "播客单集不存在" else 409
            raise PremiumGuideForceError(
                "podcast_ondemand_not_ready",
                message,
                status_code=status_code,
            ) from exc

    return {
        "episode_id": episode_id,
        "status": "not_started",
        "outcome": "can_queue",
        "forced": True,
        "charged": False,
        "should_schedule": True,
        "actor": requested_by,
    }


def prepare_forced_premium_guide(
    engine: Engine,
    *,
    episode_id: str,
    config: PodcastConfig,
    score_threshold: float,
    idempotency_key: str,
    reason: str,
    actor: str,
) -> dict:
    """Validate and persist one idempotent operator override before scheduling.

    The override changes only automatic selection (score and minimum duration).
    Transcript, full-analysis and stage-policy boundaries remain identical.
    The request metadata is kept with the episode so a process restart cannot
    turn an HTTP retry into an untraceable duplicate synthesis.
    """

    key = str(idempotency_key or "").strip()
    request_reason = str(reason or "").strip()
    requested_by = str(actor or "").strip()
    if not key or not request_reason or not requested_by:
        raise PremiumGuideForceError(
            "podcast_force_request_invalid",
            "强制 TTS 请求缺少幂等键、原因或操作者",
            status_code=422,
        )

    replay = lookup_forced_premium_guide_request(
        engine,
        episode_id=episode_id,
        idempotency_key=key,
        reason=request_reason,
        actor=requested_by,
    )
    if replay is not None:
        return replay

    policy = PodcastStagePolicy(config)
    try:
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
    except Exception as exc:
        raise PremiumGuideForceError(
            "podcast_force_tts_disabled",
            f"强制 TTS 所需处理阶段未启用：{exc}",
            status_code=503,
        ) from exc

    with Session(engine) as session:
        try:
            episode, _artifact, _transcript = _source_transcript(
                session, episode_id, config
            )
        except PremiumGuideError as exc:
            message = str(exc)
            status_code = 404 if message == "播客单集不存在" else 409
            raise PremiumGuideForceError(
                "podcast_force_tts_not_ready", message, status_code=status_code
            ) from exc

        analysis = session.get(ArticleAnalysisRecord, episode_id)
        score = podcast_premium.final_score(analysis)
        if score is None:
            raise PremiumGuideForceError(
                "podcast_force_tts_not_ready", "播客全文终评尚未完成"
            )
        published_audio = session.exec(
            select(PodcastArtifactRecord.id).where(
                PodcastArtifactRecord.episode_id == episode_id,
                PodcastArtifactRecord.kind == "digest_audio_zh",
                PodcastArtifactRecord.status == "published",
            )
        ).first()
        extensions = _extensions(episode)
        guide = extensions.get("premium_guide")
        if not isinstance(guide, dict):
            guide = {}
        status = str(guide.get("status") or "not_started")
        if status in {"queued", "summarizing", "synthesizing"}:
            raise PremiumGuideForceError(
                "podcast_force_tts_in_progress", "该播客的 TTS 任务正在处理中"
            )
        if published_audio is not None:
            raise PremiumGuideForceError(
                "podcast_force_tts_already_ready", "该播客的 TTS 音频已经生成"
            )

        requested_at = _now()
        guide.update(
            {
                "status": "queued",
                "updated_at": requested_at,
                "force_request": {
                    "episode_id": episode_id,
                    "idempotency_key": key,
                    "reason": request_reason,
                    "requested_by": requested_by,
                    "requested_at": requested_at,
                    "score": score,
                    "score_threshold": float(score_threshold),
                    "selection_override": True,
                },
            }
        )
        guide.pop("error", None)
        guide.pop("failed_stage", None)
        extensions["premium_guide"] = guide
        extensions["processing_status"] = "queued"
        episode.extensions_json = json.dumps(
            extensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        session.add(episode)
        session.commit()
        return {
            "episode_id": episode_id,
            "status": "queued",
            "forced": True,
            "replayed": False,
            "should_schedule": True,
        }


def lookup_forced_premium_guide_request(
    engine: Engine,
    *,
    episode_id: str,
    idempotency_key: str,
    reason: str,
    actor: str,
) -> dict | None:
    """Return an existing force request before mutable runtime checks.

    HTTP idempotency describes the persisted command, so a replay remains a
    replay even if credentials or stage toggles changed after acceptance.
    """

    key = str(idempotency_key or "").strip()
    request_reason = str(reason or "").strip()
    requested_by = str(actor or "").strip()
    with Session(engine) as session:
        episode = session.get(ArticleRecord, episode_id)
        if episode is None or episode.content_type != "podcast_episode":
            return None
        guide = _extensions(episode).get("premium_guide")
        if not isinstance(guide, dict):
            return None
        existing = guide.get("force_request")
        if not isinstance(existing, dict) or str(existing.get("idempotency_key")) != key:
            return None
        if (
            str(existing.get("reason") or "") != request_reason
            or str(existing.get("requested_by") or "") != requested_by
            or str(existing.get("episode_id") or "") != episode_id
        ):
            raise PremiumGuideForceError(
                "podcast_force_tts_idempotency_conflict",
                "幂等键已用于不同的强制 TTS 请求",
            )
        status = str(guide.get("status") or "not_started")
        return {
            "episode_id": episode_id,
            "status": status,
            "forced": True,
            "replayed": True,
            "should_schedule": status in {"queued", "summarizing", "synthesizing"},
        }


def list_premium_guide_tasks(
    engine: Engine,
    *,
    threshold: float,
    mode: str = "solo_deep",
    page: int = 1,
    page_size: int = 100,
) -> dict:
    if page < 1:
        raise ValueError("page must be at least 1")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    with Session(engine) as session:
        effective_final_score = func.coalesce(
            ArticleAnalysisRecord.podcast_final_score,
            ArticleAnalysisRecord.quality_score,
        )
        guide_filter = (
            ArticleRecord.content_type == "podcast_episode",
            ArticleAnalysisRecord.analysis_basis.in_(
                ("publisher_transcript", "asr_transcript")
            ),
            or_(
                effective_final_score >= threshold,
                case(
                    (
                        func.json_valid(ArticleRecord.extensions_json),
                        func.json_extract(
                            ArticleRecord.extensions_json,
                            "$.premium_guide.mode",
                        ),
                    ),
                    else_="",
                ) == "brief_zh",
            ),
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
            .where(*guide_filter)
        ).one()
        rows = session.exec(
            select(ArticleRecord, ArticleAnalysisRecord)
            .join(
                ArticleAnalysisRecord,
                ArticleAnalysisRecord.article_id == ArticleRecord.id,
            )
            .where(*guide_filter)
            .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        result = []
        for episode, analysis in rows:
            score = podcast_premium.final_score(analysis)
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
                    "is_premium": bool(score is not None and score >= threshold),
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
                    "mode": str(guide.get("mode") or mode),
                    "language": str(guide.get("language") or ""),
                    "language_source": str(guide.get("language_source") or ""),
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
    engine: Engine,
    *,
    minimum_duration_seconds: int = 0,
    score_threshold: float = podcast_premium.DEFAULT_PREMIUM_SCORE_THRESHOLD,
) -> list[str]:
    """Return premium audio or non-Chinese text-guide candidates.

    This query never creates transcript work: a candidate must already point at
    the exact transcript artifact used by a completed authoritative analysis.
    """

    with Session(engine) as session:
        rows = session.exec(
            select(
                ArticleRecord,
                ArticleAnalysisRecord,
                PodcastTextArtifactRecord,
            )
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
                PodcastTextArtifactRecord.id
                == ArticleAnalysisRecord.transcript_artifact_id,
                PodcastTextArtifactRecord.kind.in_(
                    ("normalized_transcript", "publisher_transcript")
                ),
                ArticleAnalysisRecord.analysis_basis.in_(
                    ("asr_transcript", "publisher_transcript")
                ),
            )
        ).all()
        candidates: list[str] = []
        for episode, analysis, transcript in rows:
            if not has_authoritative_analysis(analysis):
                continue
            duration = float(_extensions(episode).get("duration_seconds") or 0)
            if duration <= 0:
                continue
            eligibility = _guide_eligibility(
                episode,
                analysis,
                transcript,
                score_threshold=score_threshold,
            )
            if not eligibility.eligible:
                continue
            if (
                eligibility.mode == "solo_deep"
                and duration < minimum_duration_seconds
            ):
                continue
            extensions = _extensions(episode)
            guide = extensions.get("premium_guide")
            guide = guide if isinstance(guide, dict) else {}
            status = str(guide.get("status") or extensions.get("processing_status") or "")
            current_mode = str(guide.get("mode") or "")
            if status in {"summarizing", "synthesizing", "failed"}:
                continue
            if status == "ready" and current_mode == eligibility.mode:
                continue
            candidates.append(episode.id)
        return candidates


__all__ = [
    "PremiumGuideDraft",
    "PremiumGuideError",
    "PremiumGuideForceError",
    "PremiumGuideTextProvider",
    "PremiumGuideTtsProvider",
    "READER_ONDEMAND_FINAL_PENDING_MESSAGE",
    "READER_ONDEMAND_REASON",
    "READER_ONDEMAND_SCORE_TOO_LOW_MESSAGE",
    "SoloDeepDurationPlan",
    "SynthesizedAudio",
    "GuideEligibility",
    "calculate_solo_deep_plan",
    "fail_premium_guide",
    "list_premium_guide_tasks",
    "lookup_forced_premium_guide_request",
    "evaluate_reader_ondemand_premium_guide",
    "guide_eligibility",
    "pending_premium_guide_candidates",
    "prepare_forced_premium_guide",
    "run_premium_guide",
    "resolve_episode_language",
]
