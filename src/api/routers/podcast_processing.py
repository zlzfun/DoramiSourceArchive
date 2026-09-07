"""Administrative Podcast processing commands and redacted state views."""

from __future__ import annotations

import importlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from api import deps
from services import podcast_processing_admin as admin


router = APIRouter(
    prefix="/api/admin",
    tags=["podcast-processing"],
    dependencies=[Depends(deps.require_admin)],
)

Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
]
IdempotencyKey = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=8, max_length=200)
]
def _app():
    return importlib.import_module("api.app")


def _actor(auth: dict[str, Any]) -> str:
    return str(auth.get("sub") or auth.get("username") or auth.get("user") or "admin")


class PodcastProcessRequest(BaseModel):
    target: Literal["transcript", "digest_blog"]
    selection_override: bool = False
    reason: Reason
    idempotency_key: IdempotencyKey


class PodcastRetryRequest(BaseModel):
    idempotency_key: IdempotencyKey
    expected_attempt_count: int = Field(ge=0)
    reason: Reason


def _response(record, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        admin.serialize_processing(record),
        status_code=status_code,
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
    )


def _error(exc: admin.PodcastAdminError) -> JSONResponse:
    body: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.processing_id:
        body["processing_id"] = exc.processing_id
    return JSONResponse(
        body,
        status_code=exc.status_code,
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
    )


@router.post("/podcast-episodes/{episode_id}/process", status_code=202)
def request_processing(
    episode_id: str,
    body: PodcastProcessRequest,
    auth: dict[str, Any] = Depends(deps.require_collector),
):
    app = _app()
    try:
        record = admin.request_processing(
            app.db_sink.engine,
            app.podcast_artifact_store,
            app.podcast_processing_providers,
            app.settings.podcast,
            episode_id=episode_id,
            target=body.target,
            selection_override=body.selection_override,
            idempotency_key=body.idempotency_key,
            reason=body.reason,
            actor=_actor(auth),
        )
        return _response(record, status_code=202)
    except admin.PodcastAdminError as exc:
        return _error(exc)


@router.get("/podcast-processings/{processing_id}")
def get_processing(
    processing_id: str,
    _auth: dict[str, Any] = Depends(deps.require_admin),
):
    app = _app()
    try:
        return _response(admin.get_processing(app.db_sink.engine, processing_id))
    except admin.PodcastAdminError as exc:
        return _error(exc)


@router.post("/podcast-processings/{processing_id}/retry", status_code=202)
def retry_processing(
    processing_id: str,
    body: PodcastRetryRequest,
    auth: dict[str, Any] = Depends(deps.require_collector),
):
    app = _app()
    try:
        record = admin.retry_processing(
            app.db_sink.engine,
            app.podcast_processing_providers,
            app.settings.podcast,
            processing_id=processing_id,
            idempotency_key=body.idempotency_key,
            expected_attempt_count=body.expected_attempt_count,
            reason=body.reason,
            actor=_actor(auth),
        )
        return _response(record, status_code=202)
    except admin.PodcastAdminError as exc:
        return _error(exc)
