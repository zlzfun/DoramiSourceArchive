"""Admin-only release metrics, flags and governed history backfills."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from api import deps
from models.db import AppSettingRecord, TagRetagJobRecord
from services import analysis_backfill as backfill_service
from services import daily_brief as daily_brief_service
from services.analysis_observability import FEATURE_FLAG_KEYS, collect_release_metrics


router = APIRouter(
    prefix="/api/admin/analysis",
    tags=["analysis-ops"],
    dependencies=[Depends(deps.require_admin)],
)


class AnalysisFeatureFlagsPatch(BaseModel):
    article_analysis_enabled: bool | None = None
    taxonomy_candidate_enabled: bool | None = None
    taxonomy_auto_activation_enabled: bool | None = None
    personal_digest_enabled: bool | None = None
    public_digest_analysis_adapter_enabled: bool | None = None


class FullAnalysisScope(BaseModel):
    days: int | None = Field(default=30, ge=1, le=3650)
    selection: Literal["all", "missing_or_outdated"] = "all"
    source_ids: list[str] = Field(default_factory=list, max_length=200)


class FullAnalysisCreate(FullAnalysisScope):
    confirmation: str


def _flags(session: Session) -> dict[str, bool]:
    return {
        key: bool(
            (row := session.get(AppSettingRecord, key))
            and str(row.value or "").strip().casefold() in {"1", "true", "yes", "on"}
        )
        for key in FEATURE_FLAG_KEYS
    }


def _actor(auth: dict[str, Any]) -> str:
    return str(auth.get("sub") or auth.get("username") or auth.get("user") or "admin")


def _job_or_404(session: Session, job_id: int) -> TagRetagJobRecord:
    job = backfill_service.get_full_analysis_backfill(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="full_analysis 回填任务不存在")
    return job


def _domain_error(exc: backfill_service.AnalysisBackfillError) -> HTTPException:
    message = str(exc)
    status = 409 if any(
        phrase in message
        for phrase in ("unfinished", "active taxonomy", "can be paused", "can be resumed", "can be cancelled")
    ) else 400
    return HTTPException(status_code=status, detail=message)


@router.get("/config")
def get_config(session: Session = Depends(deps.get_session)):
    return {"feature_flags": _flags(session)}


@router.put("/config")
def update_config(
    body: AnalysisFeatureFlagsPatch,
    session: Session = Depends(deps.get_session),
):
    changes = body.model_dump(exclude_none=True)
    for key, enabled in changes.items():
        row = session.get(AppSettingRecord, key) or AppSettingRecord(key=key)
        row.value = "true" if enabled else "false"
        session.add(row)
    session.commit()
    return {"feature_flags": _flags(session)}


@router.get("/metrics")
def metrics(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(deps.get_session),
):
    try:
        return collect_release_metrics(session, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backfills/estimate")
def estimate_full_analysis_backfill(
    body: FullAnalysisScope,
    session: Session = Depends(deps.get_session),
):
    try:
        result = backfill_service.estimate_full_analysis_backfill(
            session,
            days=body.days,
            selection=body.selection,
            source_ids=body.source_ids,
        )
    except backfill_service.AnalysisBackfillError as exc:
        raise _domain_error(exc) from exc
    flags = _flags(session)
    llm_configured = daily_brief_service.resolve_llm_config(session).configured
    blockers = list(result["blockers"])
    if not flags["article_analysis_enabled"]:
        blockers.append("必须先开启文章分析")
    if not llm_configured:
        blockers.append("必须先配置可用的分析模型")
    return {
        **result,
        "analysis_enabled": flags["article_analysis_enabled"],
        "llm_configured": llm_configured,
        "ready": not blockers,
        "blockers": blockers,
    }


@router.post("/backfills")
def create_full_analysis_backfill(
    body: FullAnalysisCreate,
    auth: dict[str, Any] = Depends(deps.require_admin),
    session: Session = Depends(deps.get_session),
):
    if not _flags(session)["article_analysis_enabled"]:
        raise HTTPException(status_code=409, detail="请先开启文章分析，再创建历史回填")
    if not daily_brief_service.resolve_llm_config(session).configured:
        raise HTTPException(status_code=409, detail="请先配置可用的分析模型，再创建历史回填")
    try:
        job = backfill_service.create_full_analysis_backfill(
            session,
            days=body.days,
            selection=body.selection,
            source_ids=body.source_ids,
            actor_id=_actor(auth),
            confirmation=body.confirmation,
        )
    except backfill_service.AnalysisBackfillError as exc:
        raise _domain_error(exc) from exc
    return backfill_service.serialize_full_analysis_backfill(session, job, include_failures=True)


@router.get("/backfills")
def list_full_analysis_backfills(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(deps.get_session),
):
    return {"items": backfill_service.list_full_analysis_backfills(session, limit=limit)}


@router.get("/backfills/{job_id}")
def get_full_analysis_backfill(
    job_id: int,
    session: Session = Depends(deps.get_session),
):
    return backfill_service.serialize_full_analysis_backfill(
        session,
        _job_or_404(session, job_id),
        include_failures=True,
    )


def _transition(
    session: Session,
    job_id: int,
    action,
):
    try:
        job = action(session, _job_or_404(session, job_id))
    except backfill_service.AnalysisBackfillError as exc:
        raise _domain_error(exc) from exc
    return backfill_service.serialize_full_analysis_backfill(session, job, include_failures=True)


@router.post("/backfills/{job_id}/pause")
def pause_full_analysis_backfill(
    job_id: int,
    session: Session = Depends(deps.get_session),
):
    return _transition(session, job_id, backfill_service.pause_full_analysis_backfill)


@router.post("/backfills/{job_id}/resume")
def resume_full_analysis_backfill(
    job_id: int,
    session: Session = Depends(deps.get_session),
):
    return _transition(session, job_id, backfill_service.resume_full_analysis_backfill)


@router.post("/backfills/{job_id}/cancel")
def cancel_full_analysis_backfill(
    job_id: int,
    session: Session = Depends(deps.get_session),
):
    return _transition(session, job_id, backfill_service.cancel_full_analysis_backfill)


@router.post("/backfills/{job_id}/retry-failed")
def retry_failed_full_analysis_items(
    job_id: int,
    session: Session = Depends(deps.get_session),
):
    return _transition(session, job_id, backfill_service.retry_failed_full_analysis_items)
