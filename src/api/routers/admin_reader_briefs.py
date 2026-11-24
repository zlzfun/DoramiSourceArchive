"""Contract v1.0 M2M HTTP boundary for Dorami personal briefs."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlmodel import Session, select
from starlette.responses import JSONResponse, Response

from api import deps
from api.routers.personal_briefs import serialize_edition
from models.db import PersonalDigestEditionRecord
from services import accounts, briefs_runtime, personal_digest as digest
from services.briefs_contract import (
    PATHS,
    VERSION,
    BriefsError,
    adapt_edition,
    employee_number,
    envelope,
)


router = APIRouter(
    prefix="/api/admin/reader/briefs",
    tags=["admin-reader-briefs"],
    dependencies=[Depends(deps.require_admin)],
)

logger = logging.getLogger("dorami.admin_reader_briefs")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ALLOWED_CODES = frozenset({
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "CSRF_REJECTED",
    "ACCOUNT_NOT_FOUND",
    "INVALID_ARGUMENT",
    "RATE_LIMITED",
    "INTERNAL_ERROR",
    "ACCOUNT_MAPPING_CONFLICT",
    "SERVICE_UNAVAILABLE",
})


def resolve_request_id(request: Request) -> str:
    existing = getattr(request.state, "briefs_request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
    request.state.briefs_request_id = request_id
    return request_id


def contract_headers(request_id: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Request-ID": request_id,
        "X-Briefs-Contract-Version": VERSION,
    }


def error_response(
    error: BriefsError,
    request_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = contract_headers(request_id)
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
            }
        },
        status_code=error.status,
        headers=headers,
    )


def _has_valid_error_envelope(body: bytes, request_id: str) -> bool:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(parsed, dict):
        return False
    err = parsed.get("error")
    if not isinstance(err, dict):
        return False
    return (
        err.get("code") in _ALLOWED_CODES
        and isinstance(err.get("message"), str)
        and bool(err.get("message").strip())
        and err.get("request_id") == request_id
    )


async def handle_request(
    request: Request,
    next_handler: Callable[[Request], Any],
    auth_resolver: Callable[[Request], dict[str, Any] | None],
) -> Response:
    started = time.monotonic()
    # R01 / C09: capture Shanghai business date immediately upon request entry,
    # before authentication or any potentially slow middleware/IO work.
    captured_now = briefs_runtime.now()
    request.state.briefs_now = captured_now
    request.state.briefs_report_date = captured_now.date().isoformat()
    request_id = resolve_request_id(request)
    target_hash = "-"

    try:
        auth = auth_resolver(request)
        if auth is None:
            raise BriefsError(401, "UNAUTHENTICATED", "valid admin Cookie session required")
        if auth.get("role") != "admin":
            raise BriefsError(403, "FORBIDDEN", "admin role required")

        csrf_verifier = getattr(request.app.state, "briefs_csrf_verifier", None)
        if callable(csrf_verifier) and not csrf_verifier(request, auth):
            raise BriefsError(403, "CSRF_REJECTED", "CSRF token validation failed")

        expected_method = "POST" if request.url.path.endswith("/ensure") else "GET"
        if request.method != expected_method:
            raise BriefsError(422, "INVALID_ARGUMENT", f"method {request.method} not supported")

        pairs = request.query_params.multi_items()
        unknown_keys = [key for key, _val in pairs if key != "employee_number"]
        employee_values = [val for key, val in pairs if key == "employee_number"]
        if unknown_keys or len(employee_values) != 1:
            raise BriefsError(
                422,
                "INVALID_ARGUMENT",
                "exactly one employee_number query parameter is required",
            )
        validated_employee = employee_number(employee_values[0])
        request.state.briefs_employee = validated_employee
        target_hash = hashlib.sha256(validated_employee.encode("utf-8")).hexdigest()[:12]

        # A03 / C02 / C03: inspect actual body bytes rather than trusting or
        # requiring a Content-Length request header.
        if request.method == "POST":
            body_bytes = await request.body()
            if len(body_bytes) > 0:
                raise BriefsError(422, "INVALID_ARGUMENT", "POST body must be empty")

        response: Response = await next_handler(request)
        if response.status_code >= 400:
            raw_body = getattr(response, "body", b"") or b""
            if not _has_valid_error_envelope(raw_body, request_id):
                code_map = {
                    401: "UNAUTHENTICATED",
                    403: "FORBIDDEN",
                    422: "INVALID_ARGUMENT",
                    429: "RATE_LIMITED",
                    503: "SERVICE_UNAVAILABLE",
                }
                code = code_map.get(response.status_code, "INTERNAL_ERROR")
                extra = {}
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    extra["Retry-After"] = retry_after
                response = error_response(
                    BriefsError(response.status_code, code, "brief request rejected"),
                    request_id,
                    extra_headers=extra,
                )
    except BriefsError as exc:
        response = error_response(exc, request_id)
        if exc.status >= 500:
            logger.error(
                "brief_error request_id=%s target=%s http=%s code=%s",
                request_id,
                target_hash,
                exc.status,
                exc.code,
            )
    except OperationalError:
        logger.error(
            "brief_db_unavailable request_id=%s target=%s code=SERVICE_UNAVAILABLE",
            request_id,
            target_hash,
        )
        response = error_response(
            BriefsError(503, "SERVICE_UNAVAILABLE", "database temporarily unavailable"),
            request_id,
        )
    except SQLAlchemyError:
        logger.error(
            "brief_db_error request_id=%s target=%s code=INTERNAL_ERROR",
            request_id,
            target_hash,
        )
        response = error_response(
            BriefsError(500, "INTERNAL_ERROR", "database operation failed"),
            request_id,
        )
    except Exception:  # noqa: BLE001 - never leak stack traces, credentials, or personal data
        logger.error(
            "brief_unhandled_error request_id=%s target=%s code=INTERNAL_ERROR",
            request_id,
            target_hash,
        )
        response = error_response(
            BriefsError(500, "INTERNAL_ERROR", "unable to complete brief request"),
            request_id,
        )

    for header_name, header_val in contract_headers(request_id).items():
        response.headers[header_name] = header_val

    elapsed_ms = round((time.monotonic() - started) * 1000)
    logger.info(
        "brief_request request_id=%s method=%s target=%s report_date=%s http=%s elapsed_ms=%s",
        request_id,
        request.method,
        target_hash,
        request.state.briefs_report_date,
        response.status_code,
        elapsed_ms,
    )
    return response


def _target(session: Session, employee: str) -> str:
    """Map query param ``employee_number`` (format ``l00976966``) via ``users.w3_uid``.

    Never queries ``users.w3_employee_number`` (``00976966``) or ``users.username``.
    """
    user = accounts.resolve_w3_user(session, employee)
    if user is None:
        raise BriefsError(
            404,
            "ACCOUNT_NOT_FOUND",
            "employee_number did not match an active Dorami account",
        )
    return user.username


def _latest_revision(
    session: Session,
    username: str,
    report_date: str,
) -> PersonalDigestEditionRecord | None:
    return session.exec(
        select(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.owner_username == username,
            PersonalDigestEditionRecord.report_date == report_date,
        )
        .order_by(PersonalDigestEditionRecord.revision.desc())
    ).first()


def _status_payload(
    session: Session,
    employee: str,
    username: str,
    edition: PersonalDigestEditionRecord | None,
) -> dict[str, Any]:
    projected: dict[str, Any] | None = None
    if edition is not None:
        if edition.owner_username != username:
            raise ValueError("edition owner mismatch")
        if edition.briefs_payload_json and edition.status in {"ready", "degraded"}:
            cached = json.loads(edition.briefs_payload_json)
            if (
                not isinstance(cached, dict)
                or cached.get("report_date") != edition.report_date
                or cached.get("revision") != edition.revision
            ):
                raise ValueError("frozen payload identity mismatch")
            projected = adapt_edition(cached)
        else:
            projected = adapt_edition(serialize_edition(session, edition))
    return envelope(employee, username, projected)


@router.get("/today")
def get_today(
    request: Request,
    session: Session = Depends(deps.get_session),
) -> dict[str, Any]:
    employee: str = request.state.briefs_employee
    report_date: str = request.state.briefs_report_date
    username = _target(session, employee)
    edition = _latest_revision(session, username, report_date)
    payload = _status_payload(session, employee, username, edition)
    logger.info(
        "brief_get request_id=%s edition_id=%s report_date=%s revision=%s status=%s",
        request.state.briefs_request_id,
        edition.id if edition is not None else None,
        report_date,
        payload["edition"]["revision"] if payload["edition"] is not None else None,
        payload["status"],
    )
    return payload


def _prepare_ensure(
    engine: Engine,
    employee: str,
    request_now: dt.datetime,
    report_date: str,
) -> tuple[str, int, bool, dict[str, Any]]:
    with Session(engine) as session:
        username = _target(session, employee)
        result = digest.ensure_first_personal_digest_edition(
            session,
            username,
            report_date=report_date,
            now=request_now,
            generation_reason="first_open",
            first_open_at=request_now,
            allow_empty=True,
        )
        if result.edition is None or result.edition.id is None:
            raise ValueError("ensure failed to materialize an edition")
        payload = _status_payload(session, employee, username, result.edition)
        return username, result.edition.id, result.created, payload


@router.post("/today/ensure")
async def ensure_today(request: Request) -> dict[str, Any]:
    engine = deps.get_db_sink().engine
    employee: str = request.state.briefs_employee
    request_now: dt.datetime = request.state.briefs_now
    report_date: str = request.state.briefs_report_date

    username, edition_id, created, initial_payload = await asyncio.to_thread(
        _prepare_ensure,
        engine,
        employee,
        request_now,
        report_date,
    )
    if not created:
        logger.info(
            "brief_ensure request_id=%s edition_id=%s report_date=%s revision=%s created=false status=%s",
            request.state.briefs_request_id,
            edition_id,
            report_date,
            initial_payload["edition"]["revision"],
            initial_payload["status"],
        )
        return initial_payload

    runtime = briefs_runtime.get_brief_runtime(request.app, engine)
    await asyncio.shield(runtime.submit(edition_id, recovery=False))

    def _read_created() -> dict[str, Any]:
        with Session(engine) as session:
            edition = session.get(PersonalDigestEditionRecord, edition_id)
            if edition is None:
                raise ValueError("created edition disappeared")
            return _status_payload(session, employee, username, edition)

    final_payload = await asyncio.to_thread(_read_created)
    logger.info(
        "brief_ensure request_id=%s edition_id=%s report_date=%s revision=%s created=true status=%s",
        request.state.briefs_request_id,
        edition_id,
        report_date,
        final_payload["edition"]["revision"],
        final_payload["status"],
    )
    return final_payload


__all__ = ["PATHS", "router", "handle_request", "resolve_request_id", "contract_headers"]
