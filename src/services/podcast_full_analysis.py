"""Durable whole-transcript Podcast analysis (Issue #44).

One processing row owns the ASR→analyze chain.  Publisher transcripts enter at
``analyze`` and therefore make zero ASR calls.  The local analyze worker maps
every deterministic transcript chunk, reduces the complete evidence set with
the existing Podcast scoring ruler, and replaces the current show-notes result.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from sqlmodel import Session, select

from config import LLMConfig, PodcastConfig
from llm.client import ChatMessage, UsageMeta, chat_completion, parse_json_object
from models.analysis_contracts import TaxonomyTagDTO
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    PodcastBudgetReservationRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services import article_analysis
from services import podcast_premium
from services.podcast_processing import (
    PodcastProcessingClaim,
    begin_stage_attempt,
    claim_next_processing,
    fail_stage_attempt,
    heartbeat_processing,
    resolve_claim_before_attempt,
    settle_attempt_cost,
    _evaluate_full_analysis_authority,
)
from services.podcast_processing_admin import AdmissionEstimate
from services.podcast_publisher_transcripts import (
    PublisherTranscriptMalformed,
    parse_transcript,
    publisher_artifact_matches_current_locator,
)


INITIAL_PROCESSING_THRESHOLD = podcast_premium.INITIAL_PROCESSING_THRESHOLD
FINAL_PREMIUM_THRESHOLD = podcast_premium.DEFAULT_PREMIUM_SCORE_THRESHOLD
FULL_ANALYSIS_PROMPT_VERSION = "podcast-full-map-reduce-v2"
DEFAULT_CHUNK_CHARS = 12_000
REDUCE_EVIDENCE_MAX_CHARS = 16_000
REDUCE_GROUP_MAX_CHARS = 10_000
REDUCE_GROUP_SUMMARY_CHARS = 1_200


class FullAnalysisProvider(Protocol):
    async def map_chunk(
        self,
        *,
        title: str,
        chunk: str,
        chunk_index: int,
        chunk_count: int,
    ) -> Mapping[str, Any]: ...

    async def reduce(
        self,
        *,
        article: article_analysis.AnalysisInput,
        active_tags: Sequence[TaxonomyTagDTO],
        mapped_evidence: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class TranscriptChunk:
    index: int
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class FullAnalysisWorkerConfig:
    worker_id: str
    lease_seconds: int
    retry_seconds: int
    llm_config: LLMConfig
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    map_concurrency: int = 4


@dataclass(frozen=True)
class FullAnalysisWorkerStep:
    action: str
    processing_id: str | None = None
    attempt_id: str | None = None


class OpenAiFullAnalysisProvider:
    """Use the configured OpenAI-compatible model and the shared Podcast ruler."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.last_reduce_trace: Mapping[str, Any] = {}
        self._map_trace: dict[int, Mapping[str, Any]] = {}
        self._compaction_trace: list[Mapping[str, Any]] = []

    async def map_chunk(
        self,
        *,
        title: str,
        chunk: str,
        chunk_index: int,
        chunk_count: int,
    ) -> Mapping[str, Any]:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "你是播客全文证据提取器。只提取本段实际出现的主题、人物观点、"
                    "新发现、论据、案例、技术细节、分歧、结论与可定位证据。"
                    "不要给局部分数。返回 JSON 对象。"
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    {
                        "title": title,
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "transcript": chunk,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        ]
        raw = await chat_completion(
            messages=messages,
            config=self.config.for_aux(),
            response_json=True,
            usage_meta=UsageMeta(purpose="article_analysis", username=None),
        )
        self._map_trace[chunk_index] = {
            "stage": "map",
            "chunk_indices": [chunk_index],
            "request_hash": _messages_hash(messages),
            "response_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }
        return parse_json_object(raw)

    async def reduce(
        self,
        *,
        article: article_analysis.AnalysisInput,
        active_tags: Sequence[TaxonomyTagDTO],
        mapped_evidence: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        evidence_body = await self._bounded_evidence(mapped_evidence)
        effective_config = self.config.for_aux()
        reduction_input = article_analysis.AnalysisInput(
            **{
                **article.__dict__,
                "body": (
                    "以下证据按逐字稿分段顺序覆盖整期节目。去重汇总后，使用播客"
                    "统一评分规则生成一个最终分数；不要对分段分别打分。\n"
                    + evidence_body
                ),
            }
        )
        self.last_reduce_trace = {
            "bounded_evidence": evidence_body,
            "analysis_basis": article.analysis_basis,
            "config_identity": {
                "base_url": effective_config.base_url,
                "model": effective_config.model,
                "timeout_seconds": effective_config.timeout_seconds,
                "temperature": effective_config.temperature,
                "max_tokens": effective_config.max_tokens,
                "thinking_mode": effective_config.thinking_mode,
            },
            "map_calls": [self._map_trace[index] for index in sorted(self._map_trace)],
            "compaction_calls": list(self._compaction_trace),
            "final_reduce_input_hash": article_analysis.compute_analysis_input_hash(
                reduction_input, active_tags
            ),
        }
        return await article_analysis.analyze_article_with_llm(
            reduction_input,
            active_tags,
            self.config,
            usage_meta=UsageMeta(purpose="article_analysis", username=None),
        )

    async def _bounded_evidence(
        self, mapped_evidence: Sequence[Mapping[str, Any]]
    ) -> str:
        """Hierarchically compact evidence without losing chunk membership."""

        self._compaction_trace = []
        nodes: list[dict[str, Any]] = []
        manifest: list[int] = []
        for position, evidence in enumerate(mapped_evidence):
            chunk_index = int(evidence.get("_chunk_index", position))
            manifest.append(chunk_index)
            nodes.append(
                {"chunk_indices": [chunk_index], "evidence": dict(evidence)}
            )

        def encoded(value: Any) -> str:
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )

        while len(encoded(nodes)) > REDUCE_EVIDENCE_MAX_CHARS and len(nodes) > 1:
            groups: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            current_chars = 2
            for node in nodes:
                size = len(encoded(node)) + 1
                if current and current_chars + size > REDUCE_GROUP_MAX_CHARS:
                    groups.append(current)
                    current = []
                    current_chars = 2
                current.append(node)
                current_chars += size
            if current:
                groups.append(current)
            compacted: list[dict[str, Any]] = []
            for group in groups:
                indices = [
                    index for node in group for index in node["chunk_indices"]
                ]
                messages = [
                    ChatMessage(
                        role="system",
                        content=(
                            "压缩播客逐字稿证据，保留主题、人物观点、数字、技术细节、"
                            "分歧与结论。不得评分，只返回 JSON 对象。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=encoded(
                            {"chunk_indices": indices, "evidence": group}
                        ),
                    ),
                ]
                raw = await chat_completion(
                    messages=messages,
                    config=self.config.for_aux(),
                    response_json=True,
                    usage_meta=UsageMeta(
                        purpose="article_analysis", username=None
                    ),
                )
                self._compaction_trace.append(
                    {
                        "stage": "compact",
                        "chunk_indices": indices,
                        "request_hash": _messages_hash(messages),
                        "response_hash": hashlib.sha256(
                            raw.encode("utf-8")
                        ).hexdigest(),
                    }
                )
                summary = encoded(parse_json_object(raw))[
                    :REDUCE_GROUP_SUMMARY_CHARS
                ]
                compacted.append(
                    {"chunk_indices": indices, "summary": summary}
                )
            nodes = compacted
        return encoded(
            {
                "coverage_manifest": sorted(manifest),
                "evidence_groups": nodes,
            }
        )


def _messages_hash(messages: Sequence[ChatMessage]) -> str:
    payload = json.dumps(
        [[str(message.role), message.content] for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _AnalyzePolicy:
    allowed = frozenset({"analyze"})

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.config = getattr(delegate, "config", None)

    def require_stage(self, stage: str, *, boundary: str) -> None:
        if stage != "analyze":
            raise ValueError("full-analysis worker only owns analyze")
        method = getattr(self.delegate, "require_stage", None)
        if not callable(method):
            raise TypeError("policy must provide require_stage")
        method(stage, boundary=boundary)


def split_transcript(text: str, *, max_chars: int) -> tuple[TranscriptChunk, ...]:
    """Split without omission; newline boundaries are preferred but never required."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        raise ValueError("transcript is empty")
    chunks: list[TranscriptChunk] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = text.rfind("\n", start + max_chars // 2, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(
            TranscriptChunk(
                index=len(chunks),
                start_char=start,
                end_char=end,
                text=text[start:end],
            )
        )
        start = end
    return tuple(chunks)


def _publisher_format(artifact: PodcastTextArtifactRecord) -> str:
    try:
        provenance = json.loads(artifact.provenance_json or "{}")
    except (TypeError, ValueError):
        provenance = {}
    value = str(provenance.get("format") or "").lower()
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


def _transcript_text(
    artifact: PodcastTextArtifactRecord, config: PodcastConfig
) -> str:
    if artifact.kind == "publisher_transcript":
        try:
            return parse_transcript(
                artifact.inline_text.encode("utf-8"),
                _publisher_format(artifact),
                max_segments=config.transcript_max_segments,
                max_text_chars=config.transcript_max_text_chars,
            ).text
        except (PublisherTranscriptMalformed, UnicodeEncodeError) as exc:
            raise ValueError("publisher transcript is not readable") from exc
    if artifact.kind == "normalized_transcript":
        try:
            document = json.loads(artifact.inline_text)
            value = str(document["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("normalized transcript is not readable") from exc
        if value:
            return value
    raise ValueError("full analysis requires a complete transcript")


def _current_publisher(
    session: Session, episode_id: str
) -> PodcastTextArtifactRecord | None:
    publication = session.get(
        PodcastTextPublicationRecord, f"{episode_id}:publisher_transcript"
    )
    if publication is None or publication.status != "published":
        return None
    artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
    if (
        artifact is None
        or artifact.episode_id != episode_id
        or artifact.kind != "publisher_transcript"
        or artifact.authority_id != publication.authority_id
        or not publisher_artifact_matches_current_locator(
            session, episode_id=episode_id, artifact=artifact
        )
    ):
        return None
    return artifact


def _analysis_artifact(
    session: Session, process: PodcastProcessingRecord
) -> PodcastTextArtifactRecord:
    if process.input_artifact_kind in {
        "publisher_transcript",
        "normalized_transcript",
    }:
        artifact = session.get(PodcastTextArtifactRecord, process.input_artifact_id)
    else:
        predecessor = session.exec(
            select(PodcastStageAttemptRecord)
            .where(
                PodcastStageAttemptRecord.processing_id == process.id,
                PodcastStageAttemptRecord.stage == "asr",
                PodcastStageAttemptRecord.submission_state == "succeeded",
                PodcastStageAttemptRecord.output_artifact_kind
                == "normalized_transcript",
            )
            .order_by(PodcastStageAttemptRecord.attempt_no.desc())
        ).first()
        artifact = (
            session.get(PodcastTextArtifactRecord, predecessor.output_artifact_id)
            if predecessor is not None and predecessor.output_artifact_id
            else None
        )
    if artifact is None or artifact.episode_id != process.episode_id:
        raise ValueError("full-analysis transcript artifact is missing")
    return artifact


def _input_hash(
    artifact: PodcastTextArtifactRecord,
    chunks: Sequence[TranscriptChunk],
    *,
    mapped: Sequence[Mapping[str, Any]],
    article_input: article_analysis.AnalysisInput,
    active_tags: Sequence[TaxonomyTagDTO],
    reduce_trace: Mapping[str, Any],
    model_name: str,
) -> str:
    payload = {
        "schema": FULL_ANALYSIS_PROMPT_VERSION,
        "model": model_name,
        "artifact_id": artifact.id,
        "content_hash": artifact.content_hash,
        "article": article_input.__dict__,
        "active_tags": [tag.model_dump(mode="json") for tag in active_tags],
        "chunks": [
            {
                "start": chunk.start_char,
                "end": chunk.end_char,
                "sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            }
            for chunk in chunks
        ],
        "mapped": list(mapped),
        "reduce_trace": dict(reduce_trace),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _persist_result(
    session: Session,
    *,
    claim: PodcastProcessingClaim,
    artifact: PodcastTextArtifactRecord,
    validated: article_analysis.ValidatedAnalysis,
    article_input: article_analysis.AnalysisInput,
    analysis_input_hash: str,
    chunks: Sequence[TranscriptChunk],
    model_name: str,
    premium_score_threshold: float,
    now: dt.datetime,
) -> None:
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    episode = session.get(ArticleRecord, claim.episode_id)
    record = session.get(ArticleAnalysisRecord, claim.episode_id)
    stamp = now.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")
    if (
        process is None
        or episode is None
        or process.processing_status != "running"
        or process.stage != "analyze"
        or process.lease_token != claim.lease_token
        or process.fencing_token != claim.fencing_token
        or not process.lease_expires_at
        or process.lease_expires_at <= stamp
    ):
        raise RuntimeError("full-analysis lease was lost before persistence")
    authority_status, _authority_reasons = _evaluate_full_analysis_authority(
        session,
        episode,
        requested_target=process.requested_target,
    )
    if authority_status != "eligible":
        raise RuntimeError("full-analysis authority changed before persistence")
    if record is None:
        record = ArticleAnalysisRecord(
            article_id=episode.id,
            created_at=stamp,
            updated_at=stamp,
        )
    result = validated.result
    effective_premium_threshold = podcast_premium.get_threshold(session)
    if (
        record.analysis_basis == "podcast_show_notes"
        and record.quality_score is not None
        and record.podcast_initial_score is None
    ):
        record.podcast_initial_score = record.quality_score
    record.status = "succeeded"
    record.quality_score = result.quality_score
    record.podcast_final_score = result.quality_score
    record.dimension_scores_json = json.dumps(
        {
            "schema_version": "podcast-factors-v1",
            "factors": validated.podcast_factors,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    record.score_reason = result.score_reason
    record.summary = result.summary
    record.content_genre = str(result.content_genre)
    record.content_features_json = json.dumps(
        result.content_features, ensure_ascii=False
    )
    record.entities_json = json.dumps(result.entities, ensure_ascii=False)
    record.display_tags_json = json.dumps(
        article_analysis.extracted_tag_snapshot(result.tag_candidates),
        ensure_ascii=False,
    )
    record.content_hash = article_analysis.compute_content_hash(episode)
    record.analysis_basis = (
        "publisher_transcript"
        if artifact.kind == "publisher_transcript"
        else "asr_transcript"
    )
    record.analysis_input_hash = analysis_input_hash
    record.transcript_artifact_id = artifact.id
    record.analysis_diagnostics_json = json.dumps(
        {
            "coverage": {
                "source_chars": sum(len(chunk.text) for chunk in chunks),
                "chunk_count": len(chunks),
                "chunks": [
                    {
                        "index": chunk.index,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                        "input_hash": hashlib.sha256(
                            chunk.text.encode("utf-8")
                        ).hexdigest(),
                    }
                    for chunk in chunks
                ],
            },
            "deterministic_source_hash": hashlib.sha256(
                json.dumps(
                    {
                        "artifact_id": artifact.id,
                        "content_hash": artifact.content_hash,
                        "chunks": [
                            hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                            for chunk in chunks
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            # Diagnostic snapshot only. Runtime qualification always re-reads
            # the current persisted threshold.
            "final_premium_threshold": effective_premium_threshold,
            "final_premium": float(result.quality_score)
            >= effective_premium_threshold,
            "podcast_factors": validated.podcast_factors,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _base_prompt, scoring_version = article_analysis.analysis_contract_versions(
        episode.content_type
    )
    record.model_name = model_name
    record.prompt_version = FULL_ANALYSIS_PROMPT_VERSION
    record.scoring_version = scoring_version
    record.taxonomy_version = article_analysis._active_taxonomy_version(session)
    tag_status = "partial" if validated.warnings else "succeeded"
    try:
        with session.begin_nested():
            persisted_status, primary_id, display_tags = article_analysis._persist_tags(
                session,
                article=article_input,
                result=result,
                prompt_version=FULL_ANALYSIS_PROMPT_VERSION,
                taxonomy_version=record.taxonomy_version,
                candidate_enabled=False,
                now=now,
            )
            session.flush()
        record.primary_tag_id = primary_id
        record.display_tags_json = json.dumps(display_tags, ensure_ascii=False)
        if tag_status == "succeeded":
            tag_status = persisted_status
    except Exception:
        tag_status = "failed"
    record.tagging_status = tag_status
    record.tagged_at = stamp if tag_status in {"succeeded", "partial"} else None
    record.analyzed_at = stamp
    record.started_at = None
    record.next_attempt_at = None
    record.lease_owner = None
    record.lease_expires_at = None
    record.last_error = None
    record.updated_at = stamp
    session.add(record)


def _finalize_result(
    session: Session,
    *,
    claim: PodcastProcessingClaim,
    attempt_id: str,
    output_hash: str,
    artifact: PodcastTextArtifactRecord,
    validated: article_analysis.ValidatedAnalysis,
    article_input: article_analysis.AnalysisInput,
    analysis_input_hash: str,
    chunks: Sequence[TranscriptChunk],
    model_name: str,
    premium_score_threshold: float,
    now: dt.datetime | None = None,
) -> None:
    """Atomically expose the transcript result and terminal processing state."""

    current = now or dt.datetime.now(dt.timezone.utc)
    stamp = current.isoformat(timespec="microseconds")
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    attempt = session.get(PodcastStageAttemptRecord, attempt_id)
    reservation = session.exec(
        select(PodcastBudgetReservationRecord).where(
            PodcastBudgetReservationRecord.attempt_id == attempt_id
        )
    ).first()
    if (
        process is None
        or attempt is None
        or reservation is None
        or process.processing_status != "running"
        or process.stage != "analyze"
        or process.lease_token != claim.lease_token
        or process.fencing_token != claim.fencing_token
        or not process.lease_expires_at
        or process.lease_expires_at <= stamp
        or attempt.processing_id != process.id
        or attempt.stage != "analyze"
        or attempt.execution_kind != "local"
        or attempt.submission_state != "prepared"
        or attempt.lease_token != claim.lease_token
        or attempt.fencing_token != claim.fencing_token
        or reservation.status != "settled"
    ):
        session.rollback()
        raise RuntimeError("full-analysis finalize boundary is no longer current")
    _persist_result(
        session,
        claim=claim,
        artifact=artifact,
        validated=validated,
        article_input=article_input,
        analysis_input_hash=analysis_input_hash,
        chunks=chunks,
        model_name=model_name,
        premium_score_threshold=premium_score_threshold,
        now=current,
    )
    attempt.submission_state = "succeeded"
    attempt.retry_state = "none"
    attempt.output_hash = output_hash
    attempt.error_code = ""
    attempt.error_message = ""
    attempt.completed_at = stamp
    attempt.updated_at = stamp
    process.processing_status = "ready"
    process.lease_owner = None
    process.lease_token = None
    process.lease_expires_at = None
    process.next_retry_at = None
    process.error_code = ""
    process.error_message = ""
    process.updated_at = stamp
    process.finished_at = stamp
    session.add(attempt)
    session.add(process)
    session.commit()


async def analyze_transcript(
    *,
    title: str,
    transcript: str,
    article_input: article_analysis.AnalysisInput,
    active_tags: Sequence[TaxonomyTagDTO],
    provider: FullAnalysisProvider,
    chunk_chars: int,
    map_concurrency: int,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    heartbeat_seconds: int = 30,
) -> tuple[
    article_analysis.ValidatedAnalysis,
    tuple[TranscriptChunk, ...],
    tuple[Mapping[str, Any], ...],
]:
    chunks = split_transcript(transcript, max_chars=chunk_chars)
    semaphore = asyncio.Semaphore(max(1, map_concurrency))

    async def run(chunk: TranscriptChunk) -> Mapping[str, Any]:
        async with semaphore:
            mapped = dict(await provider.map_chunk(
                title=title,
                chunk=chunk.text,
                chunk_index=chunk.index,
                chunk_count=len(chunks),
            ))
            mapped["_chunk_index"] = chunk.index
            return mapped

    heartbeat_task: asyncio.Task[None] | None = None

    async def keep_lease() -> None:
        while True:
            await asyncio.sleep(max(1, heartbeat_seconds))
            assert heartbeat is not None
            await heartbeat()

    if heartbeat is not None:
        heartbeat_task = asyncio.create_task(keep_lease())
    try:
        mapped = tuple(await asyncio.gather(*(run(chunk) for chunk in chunks)))
        if heartbeat is not None:
            await heartbeat()
        reduced = await provider.reduce(
            article=article_input,
            active_tags=active_tags,
            mapped_evidence=mapped,
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    return (
        article_analysis.validate_analysis_payload(reduced, active_tags=active_tags),
        chunks,
        mapped,
    )


async def run_full_analysis_worker_step(
    session: Session,
    *,
    config: FullAnalysisWorkerConfig,
    podcast_config: PodcastConfig,
    policy: object,
    provider: FullAnalysisProvider | None = None,
    now: dt.datetime | None = None,
) -> FullAnalysisWorkerStep:
    current = now or dt.datetime.now(dt.timezone.utc)
    monotonic_started = asyncio.get_running_loop().time()

    def operation_now() -> dt.datetime:
        if now is None:
            return dt.datetime.now(dt.timezone.utc)
        return current + dt.timedelta(
            seconds=asyncio.get_running_loop().time() - monotonic_started
        )

    analyze_policy = _AnalyzePolicy(policy)
    claim = claim_next_processing(
        session,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
        policy=analyze_policy,
        requested_target="full_analysis",
        now=current,
    )
    if claim is None:
        return FullAnalysisWorkerStep("idle")
    process = session.get(PodcastProcessingRecord, claim.processing_id)
    if process is None or process.requested_target != "full_analysis":
        session.rollback()
        resolve_claim_before_attempt(
            session,
            claim,
            error_code="invalid_full_analysis_target",
            redacted_error_message="analyze worker claimed an incompatible target",
            retryable=False,
            policy=analyze_policy,
            now=current,
        )
        return FullAnalysisWorkerStep("failed", claim.processing_id)
    abandoned = session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == process.id,
            PodcastStageAttemptRecord.stage == "analyze",
            PodcastStageAttemptRecord.execution_kind == "local",
            PodcastStageAttemptRecord.submission_state == "prepared",
            PodcastStageAttemptRecord.fencing_token < claim.fencing_token,
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()
    if abandoned is not None:
        abandoned_reservation = session.exec(
            select(PodcastBudgetReservationRecord).where(
                PodcastBudgetReservationRecord.attempt_id == abandoned.id,
            )
        ).first()
        if abandoned_reservation is not None and abandoned_reservation.status in {
            "reserved",
            "settled",
        }:
            # A local LLM attempt has no remote task to reconcile.  If it crashed
            # after zero-cost settlement but before the atomic final commit, fence
            # that lost result and recompute under this new lease.
            stamp = current.isoformat(timespec="microseconds")
            abandoned.submission_state = "failed_terminal"
            abandoned.retry_state = "exhausted"
            abandoned.error_code = "local_finalize_interrupted"
            abandoned.error_message = "local analysis restarted after finalization crash"
            abandoned.completed_at = stamp
            abandoned.updated_at = stamp
            if abandoned_reservation.status == "reserved":
                abandoned_reservation.status = "released"
                abandoned_reservation.released_at = stamp
                abandoned_reservation.updated_at = stamp
                session.add(abandoned_reservation)
            session.add(abandoned)
            session.commit()
            process = session.get(PodcastProcessingRecord, claim.processing_id)
            if process is None:
                raise ValueError("Podcast processing disappeared during recovery")
    artifact = _analysis_artifact(session, process)
    publisher = _current_publisher(session, claim.episode_id)
    if publisher is not None and artifact.kind != "publisher_transcript":
        session.rollback()
        resolve_claim_before_attempt(
            session,
            claim,
            error_code="publisher_transcript_preferred",
            redacted_error_message="a current publisher transcript superseded ASR input",
            retryable=False,
            policy=analyze_policy,
            now=current,
        )
        return FullAnalysisWorkerStep("failed", claim.processing_id)
    transcript = _transcript_text(artifact, podcast_config)
    episode = session.get(ArticleRecord, claim.episode_id)
    source = (
        session.get(SourceConfigRecord, episode.source_id)
        if episode is not None
        else None
    )
    if episode is None:
        raise ValueError("Podcast episode is missing")
    tags = article_analysis.load_relevant_active_tags(session, episode)
    article_input = article_analysis.analysis_input_from_article(
        episode,
        source,
        active_tags=tags,
        session=session,
        now=current,
    )
    article_input = replace(
        article_input,
        analysis_basis=(
            "publisher_transcript"
            if artifact.kind == "publisher_transcript"
            else "asr_transcript"
        ),
        transcript_artifact_id=artifact.id,
    )
    previous = session.exec(
        select(PodcastStageAttemptRecord)
        .where(
            PodcastStageAttemptRecord.processing_id == process.id,
            PodcastStageAttemptRecord.submission_state == "succeeded",
        )
        .order_by(PodcastStageAttemptRecord.attempt_no.desc())
    ).first()
    stage_input_hash = previous.output_hash if previous else artifact.content_hash
    # All provider work happens outside a DB transaction.  Keep immutable values
    # detached before rollback so later attribute access cannot implicitly open a
    # transaction ahead of begin_stage_attempt's explicit budget boundary.
    budget_scope = str(process.budget_scope)
    budget_period = str(process.budget_period)
    budget_limit_minor = int(process.budget_limit_minor or 0)
    episode_title = episode.title
    session.expunge(artifact)
    session.expunge(episode)
    session.rollback()
    settings_hash = hashlib.sha256(
        json.dumps(
            {
                "prompt": FULL_ANALYSIS_PROMPT_VERSION,
                "model": config.llm_config.for_aux().model,
                "chunk_chars": config.chunk_chars,
                "premium_score_threshold": podcast_config.premium_score_threshold,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    attempt = begin_stage_attempt(
        session,
        claim,
        input_hash=stage_input_hash,
        settings_fingerprint=settings_hash,
        provider_name="dorami",
        model_name=config.llm_config.for_aux().model,
        provider_revision=FULL_ANALYSIS_PROMPT_VERSION,
        provider_request_key=f"full-analysis:{claim.processing_id}:{claim.fencing_token}",
        execution_kind="local",
        estimated_cost_minor=0,
        budget_scope=budget_scope,
        budget_period=budget_period,
        budget_limit_minor=budget_limit_minor,
        reservation_idempotency_key=(
            f"full-analysis-reservation:{claim.processing_id}:{claim.fencing_token}"
        ),
        policy=analyze_policy,
        now=current,
    )
    attempt_id = attempt.id

    async def renew() -> None:
        nonlocal claim
        claim = heartbeat_processing(
            session,
            claim,
            lease_seconds=config.lease_seconds,
            policy=analyze_policy,
            now=operation_now(),
        )

    try:
        effective_provider = provider or OpenAiFullAnalysisProvider(config.llm_config)
        validated, chunks, mapped = await analyze_transcript(
            title=episode_title,
            transcript=transcript,
            article_input=article_input,
            active_tags=tags,
            provider=effective_provider,
            chunk_chars=config.chunk_chars,
            map_concurrency=config.map_concurrency,
            heartbeat=renew,
            heartbeat_seconds=max(1, min(30, config.lease_seconds // 3)),
        )
        analysis_hash = _input_hash(
            artifact,
            chunks,
            mapped=mapped,
            article_input=article_input,
            active_tags=tags,
            reduce_trace=getattr(effective_provider, "last_reduce_trace", {}),
            model_name=config.llm_config.for_aux().model,
        )
        settle_attempt_cost(
            session,
            claim,
            attempt_id=attempt_id,
            settlement_key=f"full-analysis-settle:{attempt_id}",
            actual_cost_minor=0,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_minor": 0},
            policy=analyze_policy,
            now=operation_now(),
        )
        result_hash = hashlib.sha256(
            json.dumps(
                validated.result.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        _finalize_result(
            session,
            claim=claim,
            attempt_id=attempt_id,
            output_hash=result_hash,
            artifact=artifact,
            validated=validated,
            article_input=article_input,
            analysis_input_hash=analysis_hash,
            chunks=chunks,
            model_name=config.llm_config.for_aux().model,
            premium_score_threshold=podcast_config.premium_score_threshold,
            now=operation_now(),
        )
        return FullAnalysisWorkerStep("completed", claim.processing_id, attempt_id)
    except Exception as exc:
        try:
            session.rollback()
            failure_now = operation_now()
            failed = fail_stage_attempt(
                session,
                claim,
                attempt_id=attempt_id,
                error_code="full_analysis_failed",
                redacted_error_message=article_analysis.sanitize_error(exc),
                retryable=True,
                retry_at=failure_now + dt.timedelta(seconds=config.retry_seconds),
                policy=analyze_policy,
                now=failure_now,
            )
        except Exception:
            session.rollback()
            return FullAnalysisWorkerStep("failed", claim.processing_id, attempt_id)
        return FullAnalysisWorkerStep(
            failed.processing_status, claim.processing_id, attempt_id
        )


def full_analysis_estimator(
    asr_estimator: Callable[[Session, dict[str, object], PodcastConfig], AdmissionEstimate]
) -> Callable[[Session, dict[str, object], PodcastConfig], AdmissionEstimate]:
    def estimate(
        session: Session,
        input_metadata: dict[str, object],
        config: PodcastConfig,
    ) -> AdmissionEstimate:
        if input_metadata.get("kind") != "source_media_snapshot":
            return AdmissionEstimate(
                cost_minor=0,
                admission_fingerprint=hashlib.sha256(
                    f"{config.text_pipeline_version}:local-analysis".encode("utf-8")
                ).hexdigest(),
            )
        return asr_estimator(session, input_metadata, config)

    return estimate


def register_full_analysis_worker(
    registry: Any,
    *,
    asr_estimator: Callable[[Session, dict[str, object], PodcastConfig], AdmissionEstimate],
) -> None:
    registry.register_worker_backed_target(
        "full_analysis", estimator=full_analysis_estimator(asr_estimator)
    )
    registry.register_stage_worker(
        "analyze",
        run_full_analysis_worker_step,
        readiness=lambda value: isinstance(value, LLMConfig) and value.configured,
    )


__all__ = [
    "FINAL_PREMIUM_THRESHOLD",
    "FULL_ANALYSIS_PROMPT_VERSION",
    "FullAnalysisWorkerConfig",
    "FullAnalysisWorkerStep",
    "INITIAL_PROCESSING_THRESHOLD",
    "OpenAiFullAnalysisProvider",
    "analyze_transcript",
    "full_analysis_estimator",
    "register_full_analysis_worker",
    "run_full_analysis_worker_step",
    "split_transcript",
]
