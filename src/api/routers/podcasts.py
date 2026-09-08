"""Podcast artifact administration and stable local audio delivery."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import importlib
import os
import re
from email.utils import formatdate
from pathlib import Path
from typing import BinaryIO, Iterator, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session

from api import deps
from api.tokens import AUTH_SECRET
from models.db import ArticleRecord
from services.podcast_artifacts import (
    ARTIFACT_KINDS,
    PodcastArtifactConflict,
    PodcastArtifactError,
    PodcastArtifactNotFound,
    PodcastArtifactProbeUnavailable,
    PodcastArtifactStorageFull,
    PodcastArtifactTooLarge,
    PodcastArtifactUnsupportedMedia,
    serialize_artifact,
)
from services.podcast_asr_fetch_signing import (
    ASR_FETCH_PATH,
    PodcastAsrFetchSignatureError,
    clear_previous_signing_secret as clear_asr_fetch_previous_secret,
    field_sources as asr_fetch_field_sources,
    resolve_config as resolve_asr_fetch_config,
    resolve_signer as resolve_asr_fetch_signer,
)
from services.podcast_stage_policy import PodcastStageDenied, PodcastStagePolicy
from services.podcast_publisher_transcripts import (
    PublisherTranscriptConflict,
    PublisherTranscriptFetchFailed,
    PublisherTranscriptMalformed,
    PublisherTranscriptNotFound,
    PublisherTranscriptTimeout,
    PublisherTranscriptTooLarge,
    ingest_publisher_transcript,
)
from services.podcast_source_audio import (
    SourceAudioConflict,
    SourceAudioFetchFailed,
    SourceAudioNotFound,
    SourceAudioTimeout,
    SourceAudioTooLarge,
    cache_source_audio,
)
from services import source_visibility as source_visibility_service
from services import user_sources as user_sources_service
from services import podcast_text_reader as podcast_text_reader_service
from services import podcast_premium_guides as podcast_premium_guide_service


router = APIRouter(tags=["podcasts"])
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_PUBLIC_ASR_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class PodcastTextProvenanceResponse(BaseModel):
    label: str
    origin: Literal["publisher", "ai"]
    producer_authority_id: str | None = None
    pipeline_note: str | None = None


class PodcastReaderErrorResponse(BaseModel):
    code: Literal[
        "podcast_auth_required",
        "podcast_text_bad_request",
        "podcast_source_blocked",
        "podcast_text_artifact_invalid",
    ]
    message: str


class PodcastTextItemResponse(BaseModel):
    artifact_id: str
    content_hash: str
    kind: Literal["digest_blog_zh", "transcript_zh", "publisher_transcript"]
    language: str
    text_format: Literal["plain"]
    text: str
    total_chars: int
    range_start: int
    range_end: int
    next_cursor: str | None
    source_artifact_id: str | None
    source_content_hash: str | None
    created_at: str
    provenance: PodcastTextProvenanceResponse


class PodcastEpisodeTextsResponse(BaseModel):
    episode_id: str
    query: str | None = None
    items: list[PodcastTextItemResponse]


class PodcastDomainErrorResponse(BaseModel):
    code: Literal[
        "podcast_admin_required",
        "podcast_artifact_invalid",
        "podcast_artifact_not_ready",
        "podcast_auth_required",
        "podcast_bad_request",
        "podcast_budget_exceeded",
        "podcast_input_changed",
        "podcast_not_found",
        "podcast_preview_expired",
        "podcast_processing_conflict",
        "podcast_provider_unavailable",
        "podcast_source_audio_fetch_failed",
        "podcast_source_audio_timeout",
        "podcast_source_audio_too_large",
        "podcast_source_blocked",
        "podcast_stage_denied",
        "podcast_storage_full",
        "podcast_storage_unavailable",
        "podcast_text_artifact_invalid",
        "podcast_text_bad_request",
    ]
    message: str


class PodcastAsrFetchSecretStatusResponse(BaseModel):
    previous_signing_secret_set: bool
    previous_signing_secret_source: Literal[
        "runtime_kv", "env", "ini", "default"
    ]


class PodcastAudioArtifactResponse(BaseModel):
    id: str
    episode_id: str
    kind: Literal["source_audio", "digest_audio_zh"]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime: str
    size_bytes: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    status: Literal["ready", "published", "withdrawn", "expired"]
    provenance: str
    authority_id: str
    narration_artifact_id: str | None = None
    narration_content_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    processing_id: str | None = None
    published_at: dt.datetime | None = None
    withdrawn_at: dt.datetime | None = None
    source_locator_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    expires_at: dt.datetime | None = None
    expired_at: dt.datetime | None = None
    active_processing_refs: int = Field(ge=0)
    retention_state: Literal["temporary", "protected", "due", "expired", "durable"]
    created_at: dt.datetime
    updated_at: dt.datetime


def _app():
    return importlib.import_module("api.app")


def _store():
    store = _app().podcast_artifact_store
    if store is None:
        raise HTTPException(status_code=503, detail="Podcast artifact storage 未配置")
    return store


def _stage_policy() -> PodcastStagePolicy:
    return PodcastStagePolicy(_app().settings.podcast)


@router.get(
    "/api/podcasts/episodes/{episode_id}/texts",
    response_model=PodcastEpisodeTextsResponse,
    responses={
        401: {"model": PodcastReaderErrorResponse},
        400: {"model": PodcastReaderErrorResponse},
        403: {"model": PodcastReaderErrorResponse},
        404: {"model": PodcastReaderErrorResponse},
        422: {"model": PodcastReaderErrorResponse},
    },
)
def read_episode_texts(
    episode_id: str,
    response: Response,
    auth_session: dict = Depends(deps.require_reader),
    session: Session = Depends(deps.get_session),
    kind: Literal[
        "", "digest_blog_zh", "transcript_zh", "publisher_transcript"
    ] = Query(""),
    q: str = Query(""),
    cursor: str = Query(""),
    limit: int | None = Query(None, ge=1),
):
    """Return current public Podcast texts; this endpoint is strictly read-only."""

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    podcast_config = _app().settings.podcast
    cursor_secret = podcast_config.reader_cursor_secret or hmac.new(
        AUTH_SECRET.encode("utf-8"),
        b"dorami-podcast-reader-cursor-v1",
        hashlib.sha256,
    ).hexdigest()

    def error(status_code: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"code": code, "message": message},
            headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
        )

    try:
        return podcast_text_reader_service.read_episode_texts(
            session,
            episode_id=episode_id,
            username=str(auth_session.get("sub") or ""),
            config=podcast_config,
            cursor_secret=cursor_secret,
            kind=kind,
            query=q,
            cursor=cursor,
            limit=limit,
        )
    except podcast_text_reader_service.PodcastTextReaderNotFound as exc:
        return error(404, "podcast_source_blocked", str(exc))
    except podcast_text_reader_service.PodcastTextReaderMalformed as exc:
        return error(422, "podcast_text_artifact_invalid", str(exc))
    except podcast_text_reader_service.PodcastTextReaderBadRequest as exc:
        return error(400, "podcast_text_bad_request", str(exc))


def _as_http_error(exc: PodcastArtifactError) -> HTTPException:
    if isinstance(exc, PodcastArtifactStorageFull):
        return HTTPException(status_code=507, detail=str(exc))
    if isinstance(exc, PodcastArtifactTooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, PodcastArtifactProbeUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PodcastArtifactUnsupportedMedia):
        return HTTPException(status_code=415, detail=str(exc))
    if isinstance(exc, PodcastArtifactNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PodcastArtifactConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _podcast_error(status_code: int, code: str, exc: Exception) -> JSONResponse:
    """Return the stable domain-error envelope declared by the Podcast API."""

    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": str(exc)},
    )


def _source_cache_artifact_error(exc: PodcastArtifactError) -> JSONResponse:
    if isinstance(exc, PodcastArtifactStorageFull):
        return _podcast_error(507, "podcast_storage_full", exc)
    if isinstance(exc, PodcastArtifactTooLarge):
        return _podcast_error(413, "podcast_source_audio_too_large", exc)
    if isinstance(exc, PodcastArtifactProbeUnavailable):
        return _podcast_error(503, "podcast_provider_unavailable", exc)
    if isinstance(exc, PodcastArtifactUnsupportedMedia):
        return _podcast_error(415, "podcast_artifact_invalid", exc)
    if isinstance(exc, PodcastArtifactNotFound):
        return _podcast_error(404, "podcast_not_found", exc)
    if isinstance(exc, PodcastArtifactConflict):
        return _podcast_error(409, "podcast_processing_conflict", exc)
    return _podcast_error(400, "podcast_artifact_invalid", exc)


@router.get(
    "/api/admin/podcast-stages/capabilities",
    dependencies=[Depends(deps.require_admin)],
)
def stage_capabilities():
    return _stage_policy().capabilities()


@router.post(
    "/api/admin/podcast-transcripts/{episode_id}/ingest-publisher",
    dependencies=[Depends(deps.require_admin)],
)
async def ingest_episode_publisher_transcript(episode_id: str):
    """Explicitly fetch and publish one RSS-declared publisher transcript."""

    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            return await ingest_publisher_transcript(
                _app().db_sink.engine,
                episode_id=episode_id,
                config=_app().settings.podcast,
                client=client,
            )
    except PublisherTranscriptNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PublisherTranscriptTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except PublisherTranscriptTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except PublisherTranscriptFetchFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except PublisherTranscriptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PublisherTranscriptMalformed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PodcastStageDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _stream_bounded_audio(
    request: Request, store
) -> tuple[Path, str, int, int]:
    max_bytes = store.max_bytes
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的 Content-Length") from exc
    if declared < 0:
        raise HTTPException(status_code=400, detail="无效的 Content-Length")
    if declared > max_bytes:
        raise PodcastArtifactTooLarge(f"音频超出大小上限 {max_bytes} 字节")
    fd, path = store.create_upload_temp()
    digest = hashlib.sha256()
    size = 0
    timeout = _app().settings.podcast_artifacts.upload_timeout_seconds
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            async def write_chunks() -> None:
                nonlocal size
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > max_bytes:
                        raise PodcastArtifactTooLarge(
                            f"音频超出大小上限 {max_bytes} 字节"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            # asyncio.timeout() was added in Python 3.11 while the project still
            # supports Python 3.10. wait_for() preserves the whole-upload wall
            # clock deadline on every supported interpreter.
            await asyncio.wait_for(write_chunks(), timeout=timeout)
            handle.flush()
            os.fsync(handle.fileno())
    except asyncio.TimeoutError as exc:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=408, detail="Podcast 音频上传超时") from exc
    except Exception:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    return path, digest.hexdigest(), size, fd


@router.get(
    "/api/admin/podcast-artifacts/stats",
    dependencies=[Depends(deps.require_admin)],
)
def artifact_stats():
    return _store().stats()


@router.get(
    "/api/admin/podcast-artifacts",
    dependencies=[Depends(deps.require_admin)],
)
def list_artifacts(
    episode_id: str = "",
    status: str = "",
    kind: str = "",
    limit: int = Query(100, ge=1, le=500),
):
    try:
        rows = _store().list(
            episode_id=episode_id, status=status, kind=kind, limit=limit
        )
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc
    references = _store().active_processing_reference_counts(row.id for row in rows)
    return {
        "items": [
            serialize_artifact(
                row, active_processing_refs=references.get(row.id, 0)
            )
            for row in rows
        ]
    }


@router.post(
    "/api/admin/podcast-episodes/{episode_id}/cache-source-audio",
    response_model=PodcastAudioArtifactResponse,
    responses={
        status: {"model": PodcastDomainErrorResponse}
        for status in (400, 401, 403, 404, 409, 413, 415, 422, 502, 503, 504, 507)
    },
)
async def cache_episode_source_audio(episode_id: str, request: Request):
    """Explicitly cache one publisher enclosure on the external installation."""

    app = _app()
    auth_session = deps.get_current_session(request)
    if auth_session is None:
        return _podcast_error(
            401, "podcast_auth_required", RuntimeError("未登录或登录已过期")
        )
    if auth_session.get("role") != "admin":
        return _podcast_error(
            403, "podcast_admin_required", RuntimeError("该操作需要管理员账号")
        )
    store = app.podcast_artifact_store
    if store is None:
        return _podcast_error(
            503,
            "podcast_storage_unavailable",
            RuntimeError("Podcast artifact storage 未配置"),
        )
    try:
        return await cache_source_audio(
            app.db_sink.engine,
            store,
            episode_id=episode_id,
            podcast_config=app.settings.podcast,
            storage_config=app.settings.podcast_artifacts,
            client_factory=httpx.AsyncClient,
        )
    except SourceAudioNotFound as exc:
        return _podcast_error(404, "podcast_not_found", exc)
    except SourceAudioTooLarge as exc:
        return _podcast_error(413, "podcast_source_audio_too_large", exc)
    except SourceAudioTimeout as exc:
        return _podcast_error(504, "podcast_source_audio_timeout", exc)
    except SourceAudioFetchFailed as exc:
        return _podcast_error(502, "podcast_source_audio_fetch_failed", exc)
    except SourceAudioConflict as exc:
        return _podcast_error(409, "podcast_processing_conflict", exc)
    except PodcastStageDenied as exc:
        return _podcast_error(403, "podcast_stage_denied", exc)
    except PodcastArtifactError as exc:
        return _source_cache_artifact_error(exc)


@router.post(
    "/api/admin/podcast-artifacts/import/{episode_id}/{kind}",
    status_code=201,
    dependencies=[Depends(deps.require_admin)],
)
async def import_artifact(
    episode_id: str,
    kind: str,
    request: Request,
    provenance: str = Query("manual_upload", max_length=200),
    narration_artifact_id: str | None = Query(None, min_length=1, max_length=200),
    narration_content_hash: str | None = Query(None, min_length=64, max_length=64),
    processing_id: str | None = Query(None, min_length=1, max_length=200),
):
    if kind not in ARTIFACT_KINDS:
        raise HTTPException(status_code=400, detail="不支持的 Podcast artifact kind")
    policy = _stage_policy()
    try:
        policy.require_artifact_writer(kind, boundary="enqueue")
    except PodcastStageDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store = _store()
    path: Path | None = None
    staging_fd: int | None = None
    try:
        path, content_hash, size_bytes, staging_fd = await _stream_bounded_audio(
            request, store
        )
        policy.require_artifact_writer(kind, boundary="commit")
        record = await asyncio.to_thread(
            store.import_file,
            episode_id=episode_id,
            kind=kind,
            path=path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            declared_mime=request.headers.get("content-type", ""),
            provenance=provenance,
            authority_id=policy.config.authority_id,
            narration_artifact_id=narration_artifact_id,
            narration_content_hash=narration_content_hash,
            processing_id=processing_id,
        )
    except PodcastStageDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        if path is not None:
            path.unlink(missing_ok=True)
    return serialize_artifact(record)


@router.post(
    "/api/admin/podcast-artifacts/{artifact_id}/publish",
    dependencies=[Depends(deps.require_admin)],
)
def publish_artifact(
    artifact_id: str,
    expected_updated_at: str = Query(..., min_length=1),
):
    policy = _stage_policy()
    try:
        policy.require_artifact_writer("digest_audio_zh", boundary="commit")
        artifact = _store().get(artifact_id)
        if artifact is None:
            raise PodcastArtifactNotFound("Podcast artifact 不存在")
        if artifact.kind != "digest_audio_zh":
            raise PodcastArtifactConflict("source_audio 永远不能发布到 Reader")
        return serialize_artifact(_store().publish(
            artifact_id,
            expected_updated_at=expected_updated_at,
        ))
    except PodcastStageDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc


@router.post(
    "/api/admin/podcast-artifacts/{artifact_id}/withdraw",
    dependencies=[Depends(deps.require_admin)],
)
def withdraw_artifact(artifact_id: str):
    try:
        return serialize_artifact(_store().withdraw(artifact_id))
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc


@router.delete(
    "/api/admin/podcast-artifacts/{artifact_id}",
    dependencies=[Depends(deps.require_admin)],
)
def delete_artifact(artifact_id: str):
    try:
        blob_deleted = _store().delete(artifact_id)
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc
    return {"deleted": True, "blob_deleted": blob_deleted}


@router.post(
    "/api/admin/podcast-artifacts/reconcile",
    dependencies=[Depends(deps.require_admin)],
)
def reconcile_artifacts():
    return _store().reconcile_storage()


def _parse_range(value: str, size: int) -> tuple[int, int]:
    match = _RANGE_RE.fullmatch((value or "").strip())
    if not match or size <= 0:
        raise ValueError
    first, last = match.groups()
    if not first and not last:
        raise ValueError
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError
        start = max(size - suffix, 0)
        return start, size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError
    return start, min(end, size - 1)


def _file_chunks(handle: BinaryIO, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    try:
        handle.seek(start)
        while remaining:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def _ensure_reader_episode_visible(
    session: Session,
    record,
    auth_session: dict,
) -> None:
    if auth_session.get("role") == "admin":
        return
    episode = session.get(ArticleRecord, record.episode_id)
    if episode is None:
        raise PodcastArtifactNotFound("Podcast 音频不存在或尚未发布")
    if episode.source_id in source_visibility_service.reader_unavailable_source_ids(session):
        raise PodcastArtifactNotFound("Podcast 音频不存在或尚未发布")
    if user_sources_service.is_user_source(episode.source_id):
        viewer = str(auth_session.get("sub", ""))
        if not viewer or user_sources_service.unauthorized_user_source_ids(
            session, viewer, [episode.source_id]
        ):
            raise PodcastArtifactNotFound("Podcast 音频不存在或尚未发布")


def _audio_response(
    request: Request,
    artifact_id: str,
    *,
    admin: bool,
    auth_session: dict | None = None,
):
    store = _store()
    try:
        authorize = None
        if not admin:
            def authorize_reader(session, record):
                _ensure_reader_episode_visible(
                    session, record, auth_session or {}
                )

            authorize = authorize_reader
        record, handle = store.open_readable_audio(
            artifact_id,
            admin=admin,
            authorize=authorize,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Podcast 音频文件不存在") from exc
    except PodcastArtifactError as exc:
        raise _as_http_error(exc) from exc
    stat = os.fstat(handle.fileno())
    size = stat.st_size
    etag = f'"{record.content_hash}"'
    common_headers = {
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
    }
    # RFC 9110: Range is defined for GET; HEAD reports the full representation.
    range_header = request.headers.get("range", "") if request.method == "GET" else ""
    if range_header:
        try:
            start, end = _parse_range(range_header, size)
        except (ValueError, OverflowError):
            handle.close()
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{size}"},
            )
        length = end - start + 1
        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            _file_chunks(handle, start, length),
            status_code=206,
            media_type=record.mime,
            headers=headers,
        )
    if request.method == "HEAD":
        handle.close()
        return Response(
            status_code=200,
            media_type=record.mime,
            headers={**common_headers, "Content-Length": str(size)},
        )
    return StreamingResponse(
        _file_chunks(handle, 0, size),
        status_code=200,
        media_type=record.mime,
        headers={**common_headers, "Content-Length": str(size)},
    )


def _public_asr_error(status_code: int) -> Response:
    """Return a body-free, non-cacheable public capability failure."""

    return Response(status_code=status_code, headers=_PUBLIC_ASR_HEADERS)


def _public_asr_audio_response(request: Request):
    """Serve one signed source-audio capability without an existence oracle."""

    app = _app()
    store = app.podcast_artifact_store
    if store is None:
        return _public_asr_error(404)
    podcast_config = app.settings.podcast
    raw_query = request.scope.get("query_string", b"")
    canonical_path = str(request.scope.get("path", "") or "")

    def verify(session: Session):
        signer = resolve_asr_fetch_signer(
            session,
            podcast_config=podcast_config,
        )
        return signer.verify(
            method=request.method,
            canonical_path=canonical_path,
            raw_query=raw_query,
        )

    def authorize_asr(
        session: Session,
        _record,
        _processing,
        episode: ArticleRecord,
    ) -> None:
        if not user_sources_service.source_content_may_leave_deployment(
            session, episode.source_id
        ):
            raise PermissionError(
                "Podcast source content may not leave this deployment"
            )

    try:
        record, handle = store.open_asr_source_audio(
            verify=verify,
            authority_id=podcast_config.authority_id,
            authorize=authorize_asr,
        )
    except PermissionError:
        return _public_asr_error(403)
    except (
        PodcastAsrFetchSignatureError,
        PodcastArtifactError,
        TypeError,
        ValueError,
    ):
        return _public_asr_error(404)

    stat = os.fstat(handle.fileno())
    size = stat.st_size
    common_headers = {
        **_PUBLIC_ASR_HEADERS,
        "Accept-Ranges": "bytes",
        "ETag": f'"{record.content_hash}"',
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
    }
    range_header = request.headers.get("range", "") if request.method == "GET" else ""
    if range_header:
        try:
            start, end = _parse_range(range_header, size)
        except (ValueError, OverflowError):
            handle.close()
            return Response(
                status_code=416,
                headers={**common_headers, "Content-Range": f"bytes */{size}"},
            )
        length = end - start + 1
        return StreamingResponse(
            _file_chunks(handle, start, length),
            status_code=206,
            media_type=record.mime,
            headers={
                **common_headers,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(length),
            },
        )
    if request.method == "HEAD":
        handle.close()
        return Response(
            status_code=200,
            media_type=record.mime,
            headers={**common_headers, "Content-Length": str(size)},
        )
    return StreamingResponse(
        _file_chunks(handle, 0, size),
        status_code=200,
        media_type=record.mime,
        headers={**common_headers, "Content-Length": str(size)},
    )


@router.delete(
    "/api/admin/podcast-asr-fetch/previous-signing-secret",
    operation_id="clearPodcastAsrFetchPreviousSigningSecret",
    response_model=PodcastAsrFetchSecretStatusResponse,
    dependencies=[Depends(deps.require_admin)],
)
def clear_podcast_asr_fetch_previous_signing_secret(
    session: Session = Depends(deps.get_session),
):
    """End the runtime-KV grace period without exposing either signing key."""

    clear_asr_fetch_previous_secret(session)
    resolved = resolve_asr_fetch_config(session)
    sources = asr_fetch_field_sources(session)
    return PodcastAsrFetchSecretStatusResponse(
        previous_signing_secret_set=bool(resolved.previous_signing_secret),
        previous_signing_secret_source=sources["previous_signing_secret"],
    )


@router.head(ASR_FETCH_PATH, include_in_schema=False)
@router.get(ASR_FETCH_PATH, operation_id="fetchPodcastAsrSourceAudio")
def public_asr_source_audio(request: Request):
    return _public_asr_audio_response(request)


@router.head(
    "/api/reader/podcast-artifacts/{artifact_id}/audio",
    include_in_schema=False,
)
@router.get(
    "/api/reader/podcast-artifacts/{artifact_id}/audio",
    operation_id="readPodcastArtifactAudio",
)
def reader_audio(
    request: Request,
    artifact_id: str,
    auth_session: dict = Depends(deps.require_reader),
):
    return _audio_response(
        request, artifact_id, admin=False, auth_session=auth_session
    )


@router.head(
    "/api/admin/podcast-artifacts/{artifact_id}/audio",
    include_in_schema=False,
    dependencies=[Depends(deps.require_admin)],
)
@router.get(
    "/api/admin/podcast-artifacts/{artifact_id}/audio",
    operation_id="adminPodcastArtifactAudio",
    dependencies=[Depends(deps.require_admin)],
)
def admin_audio(request: Request, artifact_id: str):
    return _audio_response(request, artifact_id, admin=True)


@router.get(
    "/api/admin/podcast-premium-guides",
    dependencies=[Depends(deps.require_admin)],
)
def list_podcast_premium_guides(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
):
    app = _app()
    result = podcast_premium_guide_service.list_premium_guide_tasks(
        app.db_sink.engine,
        threshold=app.settings.podcast.premium_score_threshold,
        mode=app.settings.podcast.premium_guide_mode,
        page=page,
        page_size=page_size,
    )
    return {
        "threshold": app.settings.podcast.premium_score_threshold,
        **result,
    }


@router.post(
    "/api/admin/podcast-premium-guides/{episode_id}/run",
    status_code=202,
    dependencies=[Depends(deps.require_admin)],
)
async def run_podcast_premium_guide(episode_id: str):
    app = _app()
    with Session(app.db_sink.engine) as session:
        episode = session.get(ArticleRecord, episode_id)
        if episode is None or episode.content_type != "podcast_episode":
            raise HTTPException(status_code=404, detail="播客单集不存在")
    started = app.schedule_podcast_premium_guide(episode_id)
    return {"episode_id": episode_id, "status": "queued", "started": started}
