"""Runtime premium threshold and lightweight Podcast processing dashboard.

The show-notes processing line and the transcript-backed premium line are two
independent product rules.  Keep both constants and all qualification logic in
this module so a show-notes score can never accidentally award premium status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)


INITIAL_PROCESSING_THRESHOLD = 5.0
DEFAULT_PREMIUM_SCORE_THRESHOLD = 8.0
PREMIUM_SCORE_THRESHOLD_KEY = "podcast_premium_score_threshold"
TRANSCRIPT_BASES = frozenset({"publisher_transcript", "asr_transcript"})
ANALYSIS_BASIS_LABELS = {
    "podcast_show_notes": "节目简介",
    "publisher_transcript": "发布方逐字稿",
    "asr_transcript": "ASR 逐字稿",
}
ACTIVE_PROCESSING_STATUSES = frozenset(
    {"queued", "running", "awaiting_review"}
)
FAILED_PROCESSING_STATUSES = frozenset(
    {"failed", "retry_wait", "reconciliation_required"}
)
ACTIVE_TTS_STATUSES = frozenset({"queued", "summarizing", "synthesizing"})
VALID_FILTERS = frozenset(
    {"all", "pending_full", "processing", "premium", "below_threshold", "failed"}
)


def _episode_extensions(episode: ArticleRecord) -> dict[str, Any]:
    try:
        value = json.loads(episode.extensions_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _premium_guide(episode: ArticleRecord) -> dict[str, Any]:
    guide = _episode_extensions(episode).get("premium_guide")
    return guide if isinstance(guide, dict) else {}


def normalize_threshold(value: Any) -> float:
    """Validate 1.0..10.0 and exactly one-decimal runtime persistence."""

    if isinstance(value, bool):
        raise ValueError("优质门槛必须是 1.0–10.0 之间的一位小数")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("优质门槛必须是 1.0–10.0 之间的一位小数") from exc
    if not decimal.is_finite() or decimal < Decimal("1.0") or decimal > Decimal("10.0"):
        raise ValueError("优质门槛必须在 1.0–10.0 之间")
    if decimal != decimal.quantize(Decimal("0.1")):
        raise ValueError("优质门槛最多保留一位小数")
    return float(decimal.quantize(Decimal("0.1")))


def get_threshold(session: Session) -> float:
    row = session.get(AppSettingRecord, PREMIUM_SCORE_THRESHOLD_KEY)
    if row is None:
        return DEFAULT_PREMIUM_SCORE_THRESHOLD
    try:
        return normalize_threshold(row.value)
    except ValueError:
        return DEFAULT_PREMIUM_SCORE_THRESHOLD


def set_threshold(session: Session, value: Any) -> float:
    threshold = normalize_threshold(value)
    persisted = f"{threshold:.1f}"
    row = session.get(AppSettingRecord, PREMIUM_SCORE_THRESHOLD_KEY)
    if row is None:
        row = AppSettingRecord(key=PREMIUM_SCORE_THRESHOLD_KEY, value=persisted)
    else:
        row.value = persisted
    session.add(row)
    session.commit()
    return get_threshold(session)


def initial_score(analysis: ArticleAnalysisRecord | None) -> float | None:
    if analysis is None:
        return None
    explicit = getattr(analysis, "podcast_initial_score", None)
    if explicit is not None:
        return float(explicit)
    if analysis.analysis_basis == "podcast_show_notes" and analysis.quality_score is not None:
        return float(analysis.quality_score)
    return None


def final_score(analysis: ArticleAnalysisRecord | None) -> float | None:
    if analysis is None or analysis.analysis_basis not in TRANSCRIPT_BASES:
        return None
    explicit = getattr(analysis, "podcast_final_score", None)
    if explicit is not None:
        return float(explicit)
    if analysis.analysis_basis in TRANSCRIPT_BASES and analysis.quality_score is not None:
        return float(analysis.quality_score)
    return None


def is_premium(analysis: ArticleAnalysisRecord | None, threshold: float) -> bool:
    score = final_score(analysis)
    return score is not None and score >= threshold


@dataclass(frozen=True)
class _EpisodeState:
    episode: ArticleRecord
    analysis: ArticleAnalysisRecord | None
    processing: PodcastProcessingRecord | None
    source_name: str
    blog_ready: bool
    audio_ready: bool
    audio_artifact_id: str
    transcript_ready: bool


def _stage_and_reason(
    state: _EpisodeState, *, threshold: float
) -> tuple[str, str]:
    score = final_score(state.analysis)
    process = state.processing
    status = str(process.processing_status if process else "")
    stage = str(process.stage if process else "")
    if score is not None:
        if score >= threshold:
            if state.blog_ready or state.audio_ready:
                return "full_analyzed", "全文终评达到当前优质门槛，已有历史成品"
            return "full_analyzed", "全文终评达到当前优质门槛，待后续生成"
        if state.blog_ready or state.audio_ready:
            return "full_analyzed", "历史已生成，当前未达门槛"
        return "full_analyzed", f"全文终评 {score:.1f} 未达到当前优质门槛 {threshold:.1f}"
    if status == "reconciliation_required":
        return "failed", str(
            process.error_message or "供应方结果待对账，完成对账后再重试"
        )
    if status in FAILED_PROCESSING_STATUSES:
        return "failed", str(process.error_message or "全文处理失败，可重试")
    if status in ACTIVE_PROCESSING_STATUSES:
        if stage in {"fetch", "asr"}:
            return "asr_processing", "ASR 排队中" if status == "queued" else "ASR 转录中"
        if stage == "analyze":
            return "full_analysis", "全文分析排队中" if status == "queued" else "全文分析中"
        return "processing", "全文处理中"
    score_initial = initial_score(state.analysis)
    if score_initial is None:
        return "not_processed", "简介初评尚未完成"
    if score_initial < INITIAL_PROCESSING_THRESHOLD:
        return "not_selected", "简介初评未通过固定处理线，未自动进入全文处理"
    return "awaiting_transcript", "简介初评已通过处理线，但尚无逐字稿或全文处理任务"


def _matches_filter(
    state: _EpisodeState, *, status_filter: str, threshold: float
) -> bool:
    if status_filter == "all":
        return True
    score = final_score(state.analysis)
    process_status = str(state.processing.processing_status if state.processing else "")
    tts_status = str(_premium_guide(state.episode).get("status") or "")
    if status_filter == "premium":
        return score is not None and score >= threshold
    if status_filter == "below_threshold":
        return score is not None and score < threshold
    if status_filter == "processing":
        return (
            process_status in ACTIVE_PROCESSING_STATUSES
            or tts_status in ACTIVE_TTS_STATUSES
        )
    if status_filter == "failed":
        return process_status in FAILED_PROCESSING_STATUSES or tts_status == "failed"
    if status_filter == "pending_full":
        return (
            score is None
            and process_status not in ACTIVE_PROCESSING_STATUSES
            and process_status not in FAILED_PROCESSING_STATUSES
            and (initial_score(state.analysis) or 0) >= INITIAL_PROCESSING_THRESHOLD
        )
    return False


def dashboard(
    engine: Engine,
    *,
    status_filter: str = "all",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    if status_filter not in VALID_FILTERS:
        raise ValueError("未知的播客任务筛选")
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("分页参数无效")
    with Session(engine) as session:
        threshold = get_threshold(session)
        episodes = session.exec(
            select(ArticleRecord)
            .where(ArticleRecord.content_type == "podcast_episode")
            .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.desc())
        ).all()
        ids = [row.id for row in episodes]
        analyses = {
            row.article_id: row
            for row in session.exec(
                select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(ids))
            ).all()
        } if ids else {}
        transcript_ids = [
            str(row.transcript_artifact_id)
            for row in analyses.values()
            if row.transcript_artifact_id
        ]
        transcript_artifacts = {
            row.id: row
            for row in session.exec(
                select(PodcastTextArtifactRecord).where(
                    PodcastTextArtifactRecord.id.in_(transcript_ids)
                )
            ).all()
        } if transcript_ids else {}
        processing_rows = session.exec(
            select(PodcastProcessingRecord)
            .where(
                PodcastProcessingRecord.episode_id.in_(ids),
                PodcastProcessingRecord.requested_target == "full_analysis",
            )
            .order_by(PodcastProcessingRecord.created_at.desc(), PodcastProcessingRecord.id.desc())
        ).all() if ids else []
        processings: dict[str, PodcastProcessingRecord] = {}
        for row in processing_rows:
            processings.setdefault(row.episode_id, row)
        source_names = {
            row.source_id: row.name
            for row in session.exec(select(SourceConfigRecord)).all()
        }
        blog_ids = set(session.exec(
            select(PodcastTextPublicationRecord.episode_id).where(
                PodcastTextPublicationRecord.kind == "digest_blog_zh",
                PodcastTextPublicationRecord.status == "published",
            )
        ).all())
        audio_rows = session.exec(
            select(PodcastArtifactRecord).where(
                PodcastArtifactRecord.kind == "digest_audio_zh",
                PodcastArtifactRecord.status == "published",
            )
            .order_by(
                PodcastArtifactRecord.published_at.desc(),
                PodcastArtifactRecord.id.desc(),
            )
        ).all()
        audio_artifact_ids: dict[str, str] = {}
        for artifact in audio_rows:
            audio_artifact_ids.setdefault(artifact.episode_id, artifact.id)
        states = []
        for row in episodes:
            analysis = analyses.get(row.id)
            artifact = transcript_artifacts.get(
                str(analysis.transcript_artifact_id or "") if analysis else ""
            )
            expected_kind = (
                "publisher_transcript"
                if analysis and analysis.analysis_basis == "publisher_transcript"
                else "normalized_transcript"
            )
            states.append(_EpisodeState(
                episode=row,
                analysis=analysis,
                processing=processings.get(row.id),
                source_name=str(source_names.get(row.source_id) or row.source_id),
                blog_ready=row.id in blog_ids,
                audio_ready=row.id in audio_artifact_ids,
                audio_artifact_id=audio_artifact_ids.get(row.id, ""),
                transcript_ready=(
                    artifact is not None
                    and artifact.episode_id == row.id
                    and artifact.kind == expected_kind
                ),
            ))
        filtered = [
            row for row in states
            if _matches_filter(row, status_filter=status_filter, threshold=threshold)
        ]
        start = (page - 1) * page_size
        page_rows = filtered[start:start + page_size]

        def serialize(state: _EpisodeState) -> dict[str, Any]:
            score_initial = initial_score(state.analysis)
            score_final = final_score(state.analysis)
            stage, reason = _stage_and_reason(state, threshold=threshold)
            process = state.processing
            current_score = score_final if score_final is not None else score_initial
            process_status = str(process.processing_status if process else "")
            current_premium = score_final is not None and score_final >= threshold
            historical_generated = state.blog_ready or state.audio_ready
            raw_basis = str(getattr(state.analysis, "analysis_basis", "") or "")
            current_basis = ANALYSIS_BASIS_LABELS.get(
                raw_basis,
                raw_basis if raw_basis else "尚未分析",
            )
            guide = _premium_guide(state.episode)
            guide_status = str(guide.get("status") or "not_started")
            if state.audio_ready:
                # The published artifact is authoritative if a stale extension
                # survived an interrupted status update.
                guide_status = "ready"
            elif guide_status not in {
                "not_started",
                "queued",
                "summarizing",
                "synthesizing",
                "ready",
                "failed",
            }:
                guide_status = "not_started"
            guide_error = str(guide.get("error") or "")
            if guide_status == "ready" and not state.audio_ready:
                guide_status = "failed"
                guide_error = guide_error or "TTS 标记完成，但已发布音频成品不存在"
            force_request = guide.get("force_request")
            if not isinstance(force_request, dict):
                force_request = {}
            tts_forced = force_request.get("selection_override") is True
            tts_status_labels = {
                "not_started": "未开始",
                "queued": "已排队",
                "summarizing": "正在生成导读",
                "synthesizing": "正在合成音频",
                "ready": "音频已生成",
                "failed": "生成失败",
            }
            reason_text = reason
            if tts_forced and guide_status == "ready" and not current_premium:
                reason_text = "已强制生成 TTS；全文终评仍未达到当前优质门槛"
            return {
                "episode_id": state.episode.id,
                "title": state.episode.title,
                "source_id": state.episode.source_id,
                "source_name": state.source_name,
                "initial_score": score_initial,
                "final_score": score_final,
                "current_score": current_score,
                "current_basis": current_basis,
                "analysis_basis": raw_basis,
                "initial_eligible": score_initial is not None and score_initial >= INITIAL_PROCESSING_THRESHOLD,
                "is_premium": current_premium,
                "stage": stage,
                "processing_stage": str(process.stage if process else ""),
                "processing_status": process_status,
                "processing_id": str(process.id if process else ""),
                "attempt_count": int(process.attempt_count if process else 0),
                "reason": reason_text,
                "blog_ready": state.blog_ready,
                "audio_ready": state.audio_ready,
                "historical_generated": historical_generated,
                "pending_generation": current_premium and not historical_generated,
                "tts_status": guide_status,
                "tts_status_label": tts_status_labels[guide_status],
                "tts_error": guide_error,
                "tts_failed_stage": str(guide.get("failed_stage") or ""),
                "tts_forced": tts_forced,
                "tts_updated_at": str(guide.get("updated_at") or ""),
                "tts_audio_artifact_id": (
                    state.audio_artifact_id
                    or str(guide.get("audio_artifact_id") or "")
                ),
                "can_force_tts": (
                    score_final is not None
                    and state.transcript_ready
                    and not state.audio_ready
                    and guide_status not in {"queued", "summarizing", "synthesizing"}
                ),
                "can_force": (
                    score_final is None
                    and process_status not in ACTIVE_PROCESSING_STATUSES
                    and process_status != "reconciliation_required"
                ),
                "can_retry": (
                    process_status in {"failed", "retry_wait"}
                    and process is not None
                ),
            }

        completed = sum(final_score(row.analysis) is not None for row in states)
        premium_count = sum(is_premium(row.analysis, threshold) for row in states)
        pending_or_failed = sum(
            (
                str(row.processing.processing_status if row.processing else "")
                in ACTIVE_PROCESSING_STATUSES | FAILED_PROCESSING_STATUSES
            )
            or (
                str(_premium_guide(row.episode).get("status") or "")
                in ACTIVE_TTS_STATUSES | {"failed"}
            )
            or (
                final_score(row.analysis) is None
                and (initial_score(row.analysis) or 0)
                >= INITIAL_PROCESSING_THRESHOLD
            )
            for row in states
        )
        total = len(filtered)
        return {
            "threshold": threshold,
            "initial_processing_threshold": INITIAL_PROCESSING_THRESHOLD,
            "stats": {
                "total": len(states),
                "full_analyzed": completed,
                "premium": premium_count,
                "pending_or_failed": pending_or_failed,
            },
            "items": [serialize(row) for row in page_rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }


__all__ = [
    "DEFAULT_PREMIUM_SCORE_THRESHOLD",
    "INITIAL_PROCESSING_THRESHOLD",
    "PREMIUM_SCORE_THRESHOLD_KEY",
    "dashboard",
    "final_score",
    "get_threshold",
    "initial_score",
    "is_premium",
    "normalize_threshold",
    "set_threshold",
]
