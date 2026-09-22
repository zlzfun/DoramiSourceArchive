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
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services.podcast_publisher_transcripts import PublisherTranscriptError, supported_candidates


INITIAL_PROCESSING_THRESHOLD = 6.0
DEFAULT_PREMIUM_SCORE_THRESHOLD = 7.5
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
# issue #76 管理面重构:列头即操作需要按「阶段 / 判定 / TTS」三根正交轴筛选,
# 而不是把七种情形压进一个 status 枚举。stage_code 是规范化阶段(待对账不再折进
# failed),verdict 只表优质线,tts 只表导读音频阶段;旧 status 参数保留兼容。
STAGE_CODES = (
    "not_processed",
    "not_selected",
    "awaiting_transcript",
    "processing",
    "full_analyzed",
    "reconciliation",
    "failed",
)
VERDICT_CODES = ("premium", "below_threshold", "unscored")
TTS_FILTERS = ("not_started", "active", "ready", "failed")
SORT_KEYS = ("publish", "score", "updated")
TTS_STATUS_LABELS = {
    "not_started": "未开始",
    "queued": "已排队",
    "summarizing": "正在生成导读",
    "synthesizing": "正在合成音频",
    "ready": "音频已生成",
    "failed": "生成失败",
}


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
        if stage in {"fetch", "asr"}:
            reason = (
                f"ASR 转录待对账（{process.error_message}），完成对账后重试 ASR"
                if process.error_message
                else "ASR 转录结果待对账，完成对账后重试 ASR"
            )
            return "failed", reason
        return "failed", str(
            process.error_message or "供应方结果待对账，完成对账后再重试"
        )
    if status in FAILED_PROCESSING_STATUSES:
        if stage in {"fetch", "asr"}:
            reason = (
                f"ASR 转录失败（{process.error_message}），可重试 ASR"
                if process.error_message
                else "ASR 转录失败，可重试 ASR"
            )
            return "failed", reason
        if stage == "analyze":
            reason = (
                f"全文分析失败（{process.error_message}），可重试"
                if process.error_message
                else "全文分析失败，可重试"
            )
            return "failed", reason
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


def _stage_code(state: _EpisodeState) -> str:
    """Normalized processing stage; same branch order as _stage_and_reason."""

    if final_score(state.analysis) is not None:
        return "full_analyzed"
    process = state.processing
    status = str(process.processing_status if process else "")
    if status == "reconciliation_required":
        return "reconciliation"
    if status in FAILED_PROCESSING_STATUSES:
        return "failed"
    if status in ACTIVE_PROCESSING_STATUSES:
        return "processing"
    score_initial = initial_score(state.analysis)
    if score_initial is None:
        return "not_processed"
    if score_initial < INITIAL_PROCESSING_THRESHOLD:
        return "not_selected"
    return "awaiting_transcript"


def _guide_status(state: _EpisodeState) -> tuple[str, str]:
    """Normalized premium-guide (TTS) status plus error text.

    The published artifact is authoritative if a stale extension survived an
    interrupted status update; a ``ready`` marker without a published audio
    artifact degrades to ``failed`` so the row never claims an audio that does
    not exist.
    """

    guide = _premium_guide(state.episode)
    status = str(guide.get("status") or "not_started")
    error = str(guide.get("error") or "")
    if state.audio_ready:
        status = "ready"
    elif status not in TTS_STATUS_LABELS:
        status = "not_started"
    if status == "ready" and not state.audio_ready:
        status = "failed"
        error = error or "TTS 标记完成，但已发布音频成品不存在"
    return status, error


def _verdict_code(state: _EpisodeState, *, threshold: float) -> str:
    score = final_score(state.analysis)
    if score is None:
        return "unscored"
    return "premium" if score >= threshold else "below_threshold"


def _tts_bucket(state: _EpisodeState) -> str:
    status, _ = _guide_status(state)
    if status in ACTIVE_TTS_STATUSES:
        return "active"
    if status == "ready":
        return "ready"
    if status == "failed":
        return "failed"
    return "not_started"


def _updated_at(state: _EpisodeState) -> str:
    """Latest touch across analysis / processing / guide (ISO strings compare)."""

    guide = _premium_guide(state.episode)
    candidates = [
        str(getattr(state.analysis, "updated_at", "") or ""),
        str(getattr(state.processing, "updated_at", "") or ""),
        str(guide.get("updated_at") or ""),
    ]
    return max((value for value in candidates if value), default="")


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


def _matches_axes(
    state: _EpisodeState,
    *,
    threshold: float,
    q: str,
    stage: str,
    verdict: str,
    tts: str,
) -> bool:
    if stage and _stage_code(state) != stage:
        return False
    if verdict and _verdict_code(state, threshold=threshold) != verdict:
        return False
    if tts and _tts_bucket(state) != tts:
        return False
    needle = q.strip().casefold()
    if needle:
        haystack = " ".join(
            [
                str(state.episode.title or ""),
                str(state.source_name or ""),
                str(state.episode.id or ""),
            ]
        ).casefold()
        if needle not in haystack:
            return False
    return True


def _load_states(
    session: Session, *, episode_ids: list[str] | None = None
) -> list[_EpisodeState]:
    query = (
        select(ArticleRecord)
        .where(ArticleRecord.content_type == "podcast_episode")
        .order_by(ArticleRecord.publish_date.desc(), ArticleRecord.id.desc())
    )
    if episode_ids is not None:
        query = query.where(ArticleRecord.id.in_(episode_ids))
    episodes = session.exec(query).all()
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
    return states


def _serialize_state(state: _EpisodeState, *, threshold: float) -> dict[str, Any]:
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
    guide_status, guide_error = _guide_status(state)
    force_request = guide.get("force_request")
    if not isinstance(force_request, dict):
        force_request = {}
    tts_forced = force_request.get("selection_override") is True
    reason_text = reason
    if tts_forced and guide_status == "ready" and not current_premium:
        reason_text = "已强制生成 TTS；全文终评仍未达到当前优质门槛"
    try:
        has_publisher_locator = bool(supported_candidates(
            _episode_extensions(state.episode).get("transcripts")
        ))
    except PublisherTranscriptError:
        has_publisher_locator = False
    return {
        "episode_id": state.episode.id,
        "title": state.episode.title,
        "source_id": state.episode.source_id,
        "source_name": state.source_name,
        "publish_date": str(state.episode.publish_date or ""),
        "updated_at": _updated_at(state),
        "initial_score": score_initial,
        "final_score": score_final,
        "current_score": current_score,
        "current_basis": current_basis,
        "analysis_basis": raw_basis,
        "publisher_transcript_available": has_publisher_locator,
        "initial_eligible": score_initial is not None and score_initial >= INITIAL_PROCESSING_THRESHOLD,
        "is_premium": current_premium,
        "stage": stage,
        "stage_code": _stage_code(state),
        "verdict": _verdict_code(state, threshold=threshold),
        "processing_stage": str(process.stage if process else ""),
        "processing_status": process_status,
        "processing_id": str(process.id if process else ""),
        "processing_error": str(process.error_message if process else ""),
        "attempt_count": int(process.attempt_count if process else 0),
        "reason": reason_text,
        "blog_ready": state.blog_ready,
        "audio_ready": state.audio_ready,
        "historical_generated": historical_generated,
        "pending_generation": current_premium and not historical_generated,
        "tts_status": guide_status,
        "tts_status_label": TTS_STATUS_LABELS[guide_status],
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
            process_status in {"failed", "retry_wait", "reconciliation_required"}
            and process is not None
        ),
    }


def _sort_key(state: _EpisodeState, sort: str):
    if sort == "score":
        score_final = final_score(state.analysis)
        score = score_final if score_final is not None else initial_score(state.analysis)
        return (score is not None, float(score or 0.0))
    if sort == "updated":
        return _updated_at(state)
    return (str(state.episode.publish_date or ""), str(state.episode.id or ""))


def dashboard(
    engine: Engine,
    *,
    status_filter: str = "all",
    page: int = 1,
    page_size: int = 100,
    q: str = "",
    stage: str = "",
    verdict: str = "",
    tts: str = "",
    sort: str = "publish",
    order: str = "desc",
) -> dict[str, Any]:
    if status_filter not in VALID_FILTERS:
        raise ValueError("未知的播客任务筛选")
    if stage and stage not in STAGE_CODES:
        raise ValueError("未知的播客阶段筛选")
    if verdict and verdict not in VERDICT_CODES:
        raise ValueError("未知的播客判定筛选")
    if tts and tts not in TTS_FILTERS:
        raise ValueError("未知的播客 TTS 筛选")
    if sort not in SORT_KEYS or order not in {"asc", "desc"}:
        raise ValueError("未知的播客任务排序")
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("分页参数无效")
    with Session(engine) as session:
        threshold = get_threshold(session)
        states = _load_states(session)
        filtered = [
            row for row in states
            if _matches_filter(row, status_filter=status_filter, threshold=threshold)
            and _matches_axes(
                row, threshold=threshold, q=q, stage=stage, verdict=verdict, tts=tts
            )
        ]
        if sort != "publish" or order != "desc":
            filtered.sort(key=lambda row: _sort_key(row, sort), reverse=(order == "desc"))
        start = (page - 1) * page_size
        page_rows = filtered[start:start + page_size]

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
        breakdown_stage = {code: 0 for code in STAGE_CODES}
        breakdown_verdict = {code: 0 for code in VERDICT_CODES}
        breakdown_tts = {code: 0 for code in TTS_FILTERS}
        for row in states:
            breakdown_stage[_stage_code(row)] += 1
            breakdown_verdict[_verdict_code(row, threshold=threshold)] += 1
            breakdown_tts[_tts_bucket(row)] += 1
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
            "breakdown": {
                "stage": breakdown_stage,
                "verdict": breakdown_verdict,
                "tts": breakdown_tts,
                "shows": len({row.episode.source_id for row in states}),
            },
            "items": [_serialize_state(row, threshold=threshold) for row in page_rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }


_PIPELINE_STAGES = ("fetch", "asr", "analyze")
_TIMELINE_LABELS = {
    "initial": "简介初评",
    "fetch": "音频准备",
    "asr": "ASR 转录",
    "analyze": "全文分析",
    "guide": "精品导读",
    "tts": "TTS 合成",
}


def _timeline(
    session: Session, state: _EpisodeState, *, threshold: float
) -> list[dict[str, Any]]:
    """Six fixed steps, each with a state the frontend paints as a stamp.

    States: done / run / fail / warn / pending / skipped.  Timestamps come from
    the records that actually carry them (analysis, stage attempts, processing,
    guide, artifact); no timestamp is invented.
    """

    rows: list[dict[str, Any]] = []
    analysis = state.analysis
    score_initial = initial_score(analysis)
    score_final = final_score(analysis)
    analysis_status = str(getattr(analysis, "status", "") or "")
    analysis_at = str(
        getattr(analysis, "analyzed_at", "") or getattr(analysis, "updated_at", "") or ""
    )
    if score_initial is not None:
        passed = score_initial >= INITIAL_PROCESSING_THRESHOLD
        rows.append({
            "step": "initial",
            "label": _TIMELINE_LABELS["initial"],
            "state": "done",
            "note": (
                f"{score_initial:.1f} · 过付费 ASR 线 {INITIAL_PROCESSING_THRESHOLD:.1f}，自动进入全文处理"
                if passed
                else f"{score_initial:.1f} · 未过付费 ASR 线 {INITIAL_PROCESSING_THRESHOLD:.1f}，可强制全文"
            ),
            "at": analysis_at,
        })
    elif analysis_status in {"failed", "timeout"}:
        rows.append({
            "step": "initial", "label": _TIMELINE_LABELS["initial"], "state": "fail",
            "note": str(getattr(analysis, "last_error", "") or "简介初评失败"),
            "at": analysis_at,
        })
    elif analysis_status in {"pending", "running"}:
        rows.append({
            "step": "initial", "label": _TIMELINE_LABELS["initial"], "state": "run",
            "note": "正在按节目简介初评", "at": analysis_at,
        })
    elif score_final is not None:
        rows.append({
            "step": "initial", "label": _TIMELINE_LABELS["initial"], "state": "skipped",
            "note": "直接进入全文处理，无简介初评", "at": "",
        })
    else:
        rows.append({
            "step": "initial", "label": _TIMELINE_LABELS["initial"], "state": "pending",
            "note": "等待简介初评", "at": "",
        })

    process = state.processing
    basis = str(getattr(analysis, "analysis_basis", "") or "")
    publisher_transcript = basis == "publisher_transcript" or str(
        getattr(process, "input_artifact_kind", "") or ""
    ) == "publisher_transcript"
    attempts: dict[str, Any] = {}
    if process is not None:
        for attempt in session.exec(
            select(PodcastStageAttemptRecord)
            .where(PodcastStageAttemptRecord.processing_id == process.id)
            .order_by(PodcastStageAttemptRecord.started_at.asc())
        ).all():
            attempts[attempt.stage] = attempt  # latest attempt per stage wins
    status = str(process.processing_status if process else "")
    current_stage = str(process.stage if process else "")
    current_index = (
        _PIPELINE_STAGES.index(current_stage) if current_stage in _PIPELINE_STAGES else -1
    )
    for index, stage in enumerate(_PIPELINE_STAGES):
        label = _TIMELINE_LABELS[stage]
        attempt = attempts.get(stage)
        at = ""
        if attempt is not None:
            at = str(attempt.completed_at or attempt.started_at or "")
        note = ""
        if stage == "asr" and attempt is not None and attempt.provider_name:
            note = attempt.provider_name
        if process is None:
            if score_final is not None:
                row_state, note = "done", note or "已完成"
            else:
                row_state, note = "pending", "尚未进入全文处理"
        elif status == "ready" or score_final is not None:
            row_state = "done"
        elif stage == "asr" and publisher_transcript:
            row_state, note = "skipped", "采用发布方逐字稿，无需 ASR"
        elif index < current_index:
            row_state = "done"
        elif index == current_index:
            if status == "running":
                row_state = "run"
            elif status == "queued":
                row_state, note = "pending", note or "排队中"
            elif status == "retry_wait":
                row_state = "warn"
                note = str(process.error_message or "等待自动重试")
            elif status == "reconciliation_required":
                row_state = "warn"
                note = str(process.error_message or "结果待对账，对账后重试")
            elif status == "failed":
                row_state = "fail"
                note = str(process.error_message or "失败，可重试")
            elif status in {"not_required", "cancelled", "superseded"}:
                row_state, note = "skipped", str(process.error_message or status)
            else:
                row_state = "pending"
        else:
            row_state = "pending"
        if stage == "analyze" and row_state == "done" and score_final is not None:
            verdict = "优质" if score_final >= threshold else "未达门槛"
            note = f"全文终评 {score_final:.1f} · 门槛 {threshold:.1f} → {verdict}"
            at = at or analysis_at
        if stage == "fetch" and row_state == "done" and not note:
            note = "发布方逐字稿已就绪" if publisher_transcript else "原节目音频已校验"
        rows.append({"step": stage, "label": label, "state": row_state, "note": note, "at": at})

    guide = _premium_guide(state.episode)
    guide_status, guide_error = _guide_status(state)
    failed_stage = str(guide.get("failed_stage") or "")
    guide_at = str(guide.get("updated_at") or "")
    premium_now = score_final is not None and score_final >= threshold
    if state.blog_ready or guide_status in {"synthesizing", "ready"}:
        guide_row = {"state": "done", "note": "导读博客 + 口播稿已发布"}
    elif guide_status in {"summarizing", "queued"}:
        guide_row = {"state": "run", "note": "正在生成导读与口播稿"}
    elif guide_status == "failed" and failed_stage != "synthesizing":
        guide_row = {"state": "fail", "note": guide_error or "导读生成失败"}
    elif score_final is None:
        guide_row = {"state": "pending", "note": "等待全文分析"}
    elif premium_now:
        guide_row = {"state": "pending", "note": "已达门槛，等待生成"}
    else:
        guide_row = {"state": "skipped", "note": "未达门槛，不自动生成"}
    rows.append({"step": "guide", "label": _TIMELINE_LABELS["guide"], "at": guide_at, **guide_row})
    if state.audio_ready:
        tts_row = {"state": "done", "note": "中文精简音频已发布，读者可听"}
    elif guide_status == "synthesizing":
        tts_row = {"state": "run", "note": "正在合成中文精简音频，完成后自动发布"}
    elif guide_status == "failed" and failed_stage == "synthesizing":
        tts_row = {"state": "fail", "note": guide_error or "合成失败"}
    elif guide_status == "ready":
        tts_row = {"state": "warn", "note": guide_error or "音频成品缺失"}
    elif guide_row["state"] in {"run", "pending"}:
        tts_row = {"state": "pending", "note": "等待导读完成"}
    elif guide_row["state"] == "done":
        tts_row = {"state": "pending", "note": "等待合成"}
    else:
        tts_row = {"state": "skipped", "note": "未达门槛，可强制 TTS"}
    rows.append({"step": "tts", "label": _TIMELINE_LABELS["tts"], "at": guide_at, **tts_row})
    return rows


def _texts(session: Session, episode_id: str) -> dict[str, Any]:
    publications = session.exec(
        select(PodcastTextPublicationRecord).where(
            PodcastTextPublicationRecord.episode_id == episode_id
        )
    ).all()
    artifact_ids = [row.artifact_id for row in publications if row.artifact_id]
    artifacts = {
        row.id: row
        for row in session.exec(
            select(PodcastTextArtifactRecord).where(
                PodcastTextArtifactRecord.id.in_(artifact_ids)
            )
        ).all()
    } if artifact_ids else {}
    result: dict[str, Any] = {}
    for row in publications:
        artifact = artifacts.get(row.artifact_id)
        result[row.kind] = {
            "status": row.status,
            "artifact_id": row.artifact_id,
            "version": int(artifact.version) if artifact else None,
            "chars": len(artifact.inline_text or "") if artifact else None,
            "language": artifact.language if artifact else "",
            "published_at": row.published_at or "",
            "updated_at": row.updated_at,
        }
    return result


def episode_detail(engine: Engine, episode_id: str) -> dict[str, Any] | None:
    """Single-episode drawer payload: row + timeline + texts + digest audios."""

    from services.podcast_artifacts import serialize_artifact

    with Session(engine) as session:
        threshold = get_threshold(session)
        states = _load_states(session, episode_ids=[episode_id])
        if not states:
            return None
        state = states[0]
        audio_rows = session.exec(
            select(PodcastArtifactRecord)
            .where(
                PodcastArtifactRecord.episode_id == episode_id,
                PodcastArtifactRecord.kind == "digest_audio_zh",
            )
            .order_by(PodcastArtifactRecord.created_at.desc(), PodcastArtifactRecord.id.desc())
        ).all()
        extensions = _episode_extensions(state.episode)
        duration = extensions.get("duration_seconds")
        return {
            "item": _serialize_state(state, threshold=threshold),
            "threshold": threshold,
            "initial_processing_threshold": INITIAL_PROCESSING_THRESHOLD,
            "episode": {
                "id": state.episode.id,
                "title": state.episode.title,
                "source_id": state.episode.source_id,
                "source_name": state.source_name,
                "publish_date": str(state.episode.publish_date or ""),
                "source_url": str(state.episode.source_url or ""),
                "duration_seconds": (
                    int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
                ),
                "show_title": str(extensions.get("show_title") or ""),
            },
            "timeline": _timeline(session, state, threshold=threshold),
            "texts": _texts(session, episode_id),
            "artifacts": [serialize_artifact(row) for row in audio_rows],
        }


__all__ = [
    "DEFAULT_PREMIUM_SCORE_THRESHOLD",
    "INITIAL_PROCESSING_THRESHOLD",
    "PREMIUM_SCORE_THRESHOLD_KEY",
    "SORT_KEYS",
    "STAGE_CODES",
    "TTS_FILTERS",
    "VERDICT_CODES",
    "dashboard",
    "episode_detail",
    "final_score",
    "get_threshold",
    "initial_score",
    "is_premium",
    "normalize_threshold",
    "set_threshold",
]
