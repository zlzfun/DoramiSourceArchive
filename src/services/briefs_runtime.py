"""Durable, lease-fenced personal-brief generation runtime and recovery worker."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session

from models.db import PersonalDigestEditionRecord as Edition


SHANGHAI = dt.timezone(dt.timedelta(hours=8))
ATTEMPT_SECONDS = 240
LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 60
SCAN_SECONDS = 30
PENDING_GRACE_SECONDS = 30
MAX_ATTEMPTS = 2
RECOVERY_SECONDS = 1200  # 20 minutes overall recovery window (A06: no midnight truncation)

logger = logging.getLogger("dorami.briefs_runtime")


def now() -> dt.datetime:
    return dt.datetime.now(SHANGHAI)


def iso_shanghai(value: dt.datetime) -> str:
    aware = value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
    return aware.isoformat(timespec="microseconds")


def parse(value: str | None) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    parsed = dt.datetime.fromisoformat(normalized.replace("t", "T"))
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def recovery_deadline(edition: Edition) -> dt.datetime:
    """Overall recovery deadline: created_at + 20 minutes (A06 resolution).

    Midnight truncation is intentionally omitted so late-night editions (e.g.
    23:59 -> 00:01, D17 / C10) complete under their captured Shanghai report_date.
    """
    created = parse(edition.created_at)
    if created is None:
        raise ValueError(f"edition {edition.id} missing valid created_at")
    return created + dt.timedelta(seconds=RECOVERY_SECONDS)


def _snapshot_cas_conditions(edition: Edition) -> list[Any]:
    assert edition.id is not None
    conditions: list[Any] = [
        Edition.id == edition.id,
        Edition.status == edition.status,
        Edition.generation_attempt_count == edition.generation_attempt_count,
    ]
    if edition.generation_token is None:
        conditions.append(Edition.generation_token.is_(None))
    else:
        conditions.append(Edition.generation_token == edition.generation_token)
    if edition.generation_lease_expires_at is None:
        conditions.append(Edition.generation_lease_expires_at.is_(None))
    else:
        conditions.append(Edition.generation_lease_expires_at == edition.generation_lease_expires_at)
    return conditions


def token_still_valid(
    edition: Edition | None,
    token: str | None,
    current: dt.datetime,
) -> bool:
    if edition is None or not token:
        return False
    if edition.status != "generating" or edition.generation_token != token:
        return False
    lease_expires = parse(edition.generation_lease_expires_at)
    attempt_deadline = parse(edition.generation_deadline_at)
    if lease_expires is None or lease_expires <= current:
        return False
    if attempt_deadline is not None and attempt_deadline <= current:
        return False
    if recovery_deadline(edition) <= current:
        return False
    return True


def claim(
    engine: Engine,
    edition_id: int,
    *,
    current: dt.datetime | None = None,
    recovery: bool = False,
) -> str | None:
    current = (current or now()).astimezone(SHANGHAI)
    stamp = iso_shanghai(current)
    with Session(engine) as session:
        edition = session.get(Edition, edition_id)
        if edition is None or edition.status not in {"pending", "generating"}:
            return None

        created = parse(edition.created_at)
        if created is None:
            return None

        if edition.status == "pending":
            if (
                recovery
                and created + dt.timedelta(seconds=PENDING_GRACE_SECONDS) > current
                and current < recovery_deadline(edition)
            ):
                return None
        else:
            lease_expires = parse(edition.generation_lease_expires_at)
            if lease_expires is not None and lease_expires > current:
                return None

        cas_conditions = _snapshot_cas_conditions(edition)
        deadline = recovery_deadline(edition)
        attempts = int(edition.generation_attempt_count or 0)
        if current >= deadline or attempts >= MAX_ATTEMPTS:
            session.exec(
                update(Edition)
                .where(*cas_conditions)
                .values(
                    status="failed",
                    error="recovery_deadline_or_attempts_exhausted",
                    generated_at=None,
                    generation_token=None,
                    generation_lease_expires_at=None,
                    updated_at=stamp,
                )
            )
            session.commit()
            return None

        token = uuid.uuid4().hex
        attempt_deadline = min(
            current + dt.timedelta(seconds=ATTEMPT_SECONDS),
            deadline,
        )
        lease_until = current + dt.timedelta(seconds=LEASE_SECONDS)
        result = session.exec(
            update(Edition)
            .where(*cas_conditions, Edition.generation_attempt_count < MAX_ATTEMPTS)
            .values(
                status="generating",
                generation_token=token,
                generation_attempt_count=Edition.generation_attempt_count + 1,
                generation_started_at=stamp,
                generation_heartbeat_at=stamp,
                generation_deadline_at=iso_shanghai(attempt_deadline),
                generation_lease_expires_at=iso_shanghai(lease_until),
                updated_at=stamp,
            )
        )
        session.commit()
        return token if result.rowcount == 1 else None


def heartbeat(
    engine: Engine,
    edition_id: int,
    token: str,
    *,
    current: dt.datetime | None = None,
) -> bool:
    current = (current or now()).astimezone(SHANGHAI)
    with Session(engine) as session:
        edition = session.get(Edition, edition_id)
        if not token_still_valid(edition, token, current):
            return False
        assert edition is not None
        stamp = iso_shanghai(current)
        lease_until = iso_shanghai(current + dt.timedelta(seconds=LEASE_SECONDS))
        result = session.exec(
            update(Edition)
            .where(*_snapshot_cas_conditions(edition))
            .values(
                generation_heartbeat_at=stamp,
                generation_lease_expires_at=lease_until,
                updated_at=stamp,
            )
        )
        session.commit()
        return result.rowcount == 1


def fail(
    engine: Engine,
    edition_id: int,
    token: str,
    reason: str,
    *,
    timed_out: bool = False,
    current: dt.datetime | None = None,
) -> bool:
    current = (current or now()).astimezone(SHANGHAI)
    with Session(engine) as session:
        edition = session.get(Edition, edition_id)
        if edition is None or edition.status != "generating" or edition.generation_token != token:
            return False
        lease_expires = parse(edition.generation_lease_expires_at)
        # Never allow an expired worker to overwrite state even on local timeout.
        if lease_expires is None or lease_expires <= current:
            return False
        if not timed_out and not token_still_valid(edition, token, current):
            return False
        stamp = iso_shanghai(current)
        result = session.exec(
            update(Edition)
            .where(*_snapshot_cas_conditions(edition))
            .values(
                status="failed",
                error=(reason or "generation_failed").strip()[:1000],
                generated_at=None,
                generation_token=None,
                generation_lease_expires_at=None,
                updated_at=stamp,
            )
        )
        session.commit()
        return result.rowcount == 1


def _generate(engine: Engine, edition_id: int, token: str, business_now: dt.datetime) -> None:
    from api.routers.personal_briefs import _analysis_ready, _sources_ready
    from services import personal_digest as digest

    with Session(engine) as session:
        edition = session.get(Edition, edition_id)
        if edition is None:
            return
        sources_ready = _sources_ready(session, edition, business_now)
        analysis_ready = _analysis_ready(session, edition, business_now)
        digest.generate_personal_digest(
            session,
            edition.owner_username,
            report_date=edition.report_date,
            now=business_now,
            pending_edition_id=edition_id,
            generation_token=token,
            sync_stale=not sources_ready,
            analysis_incomplete=not analysis_ready,
            execution_clock=now,
            localize_before_publish=True,
        )


def run_attempt(engine: Engine, edition_id: int, *, recovery: bool = False) -> None:
    token = claim(engine, edition_id, recovery=recovery)
    if token is None:
        return
    with Session(engine) as session:
        edition = session.get(Edition, edition_id)
        if edition is None:
            return
        business_now = parse(edition.cutoff_at) or now()
        deadline = parse(edition.generation_deadline_at) or (
            now() + dt.timedelta(seconds=ATTEMPT_SECONDS)
        )
        attempt = edition.generation_attempt_count
        report_date = edition.report_date
        revision = edition.revision

    logger.info(
        "brief_attempt_start edition_id=%s report_date=%s revision=%s attempt=%s recovery=%s token=%s",
        edition_id,
        report_date,
        revision,
        attempt,
        recovery,
        token[:8],
    )
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="brief-attempt")
    future = pool.submit(_generate, engine, edition_id, token, business_now)
    started_mono = time.monotonic()
    try:
        while True:
            current = now()
            remaining = min(
                ATTEMPT_SECONDS - (time.monotonic() - started_mono),
                (deadline - current).total_seconds(),
            )
            if remaining <= 0:
                fail(engine, edition_id, token, "generation_budget_exhausted", timed_out=True)
                logger.warning(
                    "brief_attempt_timeout edition_id=%s attempt=%s token=%s",
                    edition_id,
                    attempt,
                    token[:8],
                )
                return
            try:
                future.result(timeout=min(HEARTBEAT_SECONDS, remaining))
                logger.info(
                    "brief_attempt_done edition_id=%s attempt=%s token=%s elapsed_ms=%s",
                    edition_id,
                    attempt,
                    token[:8],
                    round((time.monotonic() - started_mono) * 1000),
                )
                return
            except FutureTimeout:
                if future.done():
                    future.result()
                    return
                if now() >= deadline:
                    fail(engine, edition_id, token, "generation_budget_exhausted", timed_out=True)
                    return
                if not heartbeat(engine, edition_id, token):
                    logger.info(
                        "brief_attempt_lost_lease edition_id=%s attempt=%s token=%s",
                        edition_id,
                        attempt,
                        token[:8],
                    )
                    return
    except Exception:  # noqa: BLE001 - sanitize provider/internal exception details
        logger.warning(
            "brief_attempt_failed edition_id=%s attempt=%s token=%s",
            edition_id,
            attempt,
            token[:8],
            exc_info=False,
        )
        fail(engine, edition_id, token, "generation_failed")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class BriefRuntime:
    """Application-owned supervisor: tasks survive client disconnect; DB owns crash recovery."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self.stopping = False

    def submit(self, edition_id: int, *, recovery: bool = False) -> asyncio.Task[None]:
        if self.stopping:
            raise RuntimeError("brief runtime is stopping")
        existing = self.tasks.get(edition_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(
            asyncio.to_thread(run_attempt, self.engine, edition_id, recovery=recovery)
        )
        self.tasks[edition_id] = task

        def _cleanup(done: asyncio.Task[None]) -> None:
            if self.tasks.get(edition_id) is done:
                self.tasks.pop(edition_id, None)
            if not done.cancelled() and done.exception() is not None:
                logger.error("brief_supervisor_failed edition_id=%s", edition_id)

        task.add_done_callback(_cleanup)
        return task

    async def close(self) -> None:
        self.stopping = True
        current_loop = asyncio.get_running_loop()
        pending = [
            task
            for task in list(self.tasks.values())
            if not task.done() and task.get_loop() is current_loop
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def get_brief_runtime(app: Any, engine: Engine) -> BriefRuntime:
    runtime: BriefRuntime | None = getattr(app.state, "briefs_runtime", None)
    if runtime is None or (runtime.engine is not engine and not runtime.stopping and not runtime.tasks):
        runtime = BriefRuntime(engine)
        app.state.briefs_runtime = runtime
    if runtime.stopping:
        from services.briefs_contract import BriefsError

        raise BriefsError(503, "SERVICE_UNAVAILABLE", "brief runtime is shutting down")
    if runtime.engine is not engine:
        from services.briefs_contract import BriefsError

        raise BriefsError(503, "SERVICE_UNAVAILABLE", "brief runtime is attached to a different database")
    return runtime
