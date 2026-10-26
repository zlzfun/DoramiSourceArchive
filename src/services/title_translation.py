"""Automatic new-title outbox and shared fenced title generation (issue #218).

No historical scan. A session flush adds the lightweight outbox in the article's
transaction; only committed rows can be claimed. Source and global AI policies
are rechecked at dispatch and publication. Bodies never enter this worker.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import datetime as dt
import logging
import re
import time
import uuid
from typing import Awaitable, Callable

from sqlalchemy import event, inspect, text, delete
from sqlalchemy.orm import Session as SASession
from sqlmodel import Session, func, select

from llm.client import UsageMeta, LLMHTTPError, client_session
from llm import prompts
from models.db import (
    AiUsageRecord, AppSettingRecord, ArticleRecord, TranslationJobRecord,
    TitleTranslationJobRecord as Job, TitleTranslationLeaseRecord as Lease,
    TitleTranslationCallRecord as Call,
)
from services import accounts, ai_usage, daily_brief, sync_consumer_policy, user_sources
from services.title_cache import TITLE_KEY, TITLE_FP_KEY, title_receipt, extensions, fingerprint

logger = logging.getLogger(__name__)
ENABLED_KEY = "auto_title_translation_enabled"
DAILY_TOKENS_KEY = "auto_title_translation_daily_tokens"
WATERMARK_KEY = "auto_title_translation_started_at"
SUPPRESS_ENQUEUE_KEY = "suppress_auto_title_translation"
DEFAULT_DAILY_TOKENS = 200_000
MAX_TITLE_CHARS = 1200
MAX_OUTPUT_TOKENS = 512
MAX_ATTEMPTS = 3
POLL_SECONDS = 10
USAGE_PURPOSE = "auto_title_translate"
_hooks_installed = False


class TitlePending(Exception):
    """Another title owner is active, or an operational gate is closed."""


@contextmanager
def _write(engine):
    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()


def _enabled(session) -> bool:
    setting = session.get(AppSettingRecord, ENABLED_KEY)
    return setting is None or setting.value.strip().lower() == "true"


def _daily_limit(session) -> int:
    setting = session.get(AppSettingRecord, DAILY_TOKENS_KEY)
    try:
        return max(0, min(100_000_000, int(setting.value))) if setting else DEFAULT_DAILY_TOKENS
    except (TypeError, ValueError):
        return DEFAULT_DAILY_TOKENS


def needs_translation(title: str) -> bool:
    from services.reader_ai import looks_chinese
    value = str(title or "").strip()
    if not value or len(value) > MAX_TITLE_CHARS or looks_chinese(value):
        return False
    if re.fullmatch(r"(?:https?://|www\.)\S+", value, re.I) or not any(character.isalpha() for character in value):
        return False
    # Machine identifiers, handles and plain release labels should retain their
    # exact spelling. Natural-language headlines with a version still translate.
    if re.fullmatch(r"[@#][\w.-]+|[A-Za-z0-9_./-]*[0-9][A-Za-z0-9_./-]*", value):
        return False
    if re.fullmatch(r"[\w./-]+\s+v?\d+(?:\.\d+)+(?:[-+][\w.-]+)?", value, re.I):
        return False
    if " " not in value and re.search(r"[a-z][A-Z]", value):
        return False
    return True


def _receipt_status(article) -> str | None:
    value = title_receipt(article)
    if value is None:
        return None
    return "skipped" if value == (article.title or "").strip() else "succeeded"


def _local_article(session, article) -> bool:
    return not (article.analysis_authority_id or "").strip() and sync_consumer_policy.local_source_operation_allowed(
        session, article.source_id, operation="collection",
    )


def _source_allowed(session, article) -> bool:
    return user_sources.source_content_may_leave_deployment(session, article.source_id)


def initialize(engine) -> None:
    """Record the first activation; never enumerate or backfill old articles."""
    with _write(engine) as session:
        if session.get(AppSettingRecord, WATERMARK_KEY) is None:
            session.add(AppSettingRecord(key=WATERMARK_KEY, value=dt.datetime.now(dt.timezone.utc).isoformat()))
    install_hooks()


def enqueue(session, article, *, title_changed=False) -> None:
    """Called only for a new record or a changed original, within its transaction."""
    if title_changed:
        ext = extensions(article)
        if ext.get(TITLE_FP_KEY) != fingerprint(article.title):
            ext.pop(TITLE_KEY, None)
            ext.pop(TITLE_FP_KEY, None)
            import json
            article.extensions_json = json.dumps(ext, ensure_ascii=False)
    if session.get(AppSettingRecord, WATERMARK_KEY) is None:
        return
    row = session.get(Job, article.id)
    if row is None:
        row = next((value for value in session.new if isinstance(value, Job) and value.article_id == article.id), None)
    fp = fingerprint(article.title)
    if session.info.get(SUPPRESS_ENQUEUE_KEY):
        # An imported replacement invalidates an older local pending job, but
        # must never create a new local automatic dispatch.
        if row is not None and row.title_fp != fp:
            row.title_fp, row.status = fp, _receipt_status(article) or "skipped"
            row.attempts, row.retry_at, row.error, row.updated_at = 0, 0, "", time.time()
        return
    if row is not None and row.title_fp == fp:
        return
    # Imported/public replica records never cause local automatic generation.
    # Excluded private sources are not queued; a later source-policy change must
    # not turn a historical library into an implicit backfill.
    if not _local_article(session, article) or not _source_allowed(session, article):
        if row is not None:
            row.title_fp, row.status, row.updated_at = fp, "skipped", time.time()
        return
    status = _receipt_status(article) or ("pending" if needs_translation(article.title) else "skipped")
    if row is None:
        row = Job(article_id=article.id, title_fp=fp, status=status, updated_at=time.time())
        session.add(row)
    else:
        row.title_fp, row.status, row.attempts, row.retry_at = fp, status, 0, 0
        row.error, row.updated_at = "", time.time()


def install_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    event.listen(SASession, "before_flush", _queue_changed_titles)
    _hooks_installed = True


def _queue_changed_titles(session, _context, _instances):
    # Snapshot before adding outbox records. Direct ORM writers, metadata-only
    # podcast refreshes and DatabaseStorage.update all get the same transaction.
    for article in list(session.new) + list(session.dirty):
        if not isinstance(article, ArticleRecord) or article in session.deleted:
            continue
        is_new = article in session.new
        changed = inspect(article).attrs.title.history.has_changes()
        if is_new or changed:
            enqueue(session, article, title_changed=not is_new and changed)


def _validate(session, article_id, title, *, automatic, require_ai_gate=True):
    from services.reader_ai import ReaderAIError
    article = session.get(ArticleRecord, article_id)
    if article is None or (article.title or "").strip() != title:
        raise ReaderAIError("标题已更新，请重新翻译", 409)
    if not _source_allowed(session, article):
        raise ReaderAIError("该来源不能发送到 AI 服务", 403)
    if (automatic or require_ai_gate) and not accounts.ai_beta_global_enabled(session):
        raise ReaderAIError("AI 功能已关闭", 403)
    if automatic and (not _enabled(session) or not _local_article(session, article)):
        raise TitlePending("automatic title generation is not allowed")
    return article


def _acquire(engine, article_id, title, automatic, lease_seconds, require_ai_gate=True):
    with _write(engine) as session:
        article = _validate(session, article_id, title, automatic=automatic, require_ai_gate=require_ai_gate)
        cached = title_receipt(article)
        if cached:
            return None, cached
        now, fp = time.time(), fingerprint(title)
        lease = session.get(Lease, article_id)
        if lease and lease.title_fp == fp and lease.until > now:
            return None, None
        token = uuid.uuid4().hex
        if lease is None:
            session.add(Lease(article_id=article_id, title_fp=fp, token=token, until=now + lease_seconds))
        else:
            lease.title_fp, lease.token, lease.until = fp, token, now + lease_seconds
        return token, None


def _renew(engine, article_id, title, token, automatic, lease_seconds, require_ai_gate=True):
    with _write(engine) as session:
        _validate(session, article_id, title, automatic=automatic, require_ai_gate=require_ai_gate)
        lease = session.get(Lease, article_id)
        if lease is None or lease.token != token or lease.until <= time.time():
            raise TitlePending("title lease lost")
        lease.until = time.time() + lease_seconds


def _publish(engine, article_id, title, translated, token, automatic, require_ai_gate=True):
    import json
    from services.reader_ai import ReaderAIError
    if not isinstance(translated, str) or not translated.strip() or len(translated) > MAX_TITLE_CHARS * 3:
        raise ReaderAIError("标题译文无效", 502)
    with _write(engine) as session:
        article = _validate(session, article_id, title, automatic=automatic, require_ai_gate=require_ai_gate)
        lease = session.get(Lease, article_id)
        if lease is None or lease.token != token or lease.until <= time.time():
            raise TitlePending("title lease lost")
        # A title-only result must merge into the latest extensions, never into
        # the stale ORM snapshot held while the provider was working.
        ext = extensions(article)
        ext[TITLE_KEY], ext[TITLE_FP_KEY] = translated.strip(), fingerprint(title)
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.delete(lease)
        return translated.strip()


def _release(engine, article_id, token):
    with _write(engine) as session:
        lease = session.get(Lease, article_id)
        if lease is not None and lease.token == token:
            session.delete(lease)


async def _acquire_owned(engine, article_id, title, automatic, lease_seconds, require_ai_gate=True):
    """Never lose a committed claim when cancellation interrupts its thread.

    Cancelling ``to_thread`` does not stop the SQLite transaction. Keep its
    result shielded, then release only the returned token before propagating
    cancellation. Repeated cancellation must not interrupt that ownership cleanup.
    """
    claim = asyncio.create_task(asyncio.to_thread(
        _acquire, engine, article_id, title, automatic, lease_seconds, require_ai_gate,
    ))
    try:
        return await asyncio.shield(claim)
    except asyncio.CancelledError:
        async def release_claim():
            token, _cached = await claim
            if token:
                await asyncio.to_thread(_release, engine, article_id, token)

        cleanup = asyncio.create_task(release_claim())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            cleanup.result()
        except Exception:
            # Database failure still leaves the existing expiring lease fence;
            # never mask the caller's cancellation with a cleanup exception.
            logger.exception("Cancelled title claim cleanup failed article=%s", article_id)
        raise


async def translate_shared(
    engine, article_id: str, title: str, call: Callable[[], Awaitable[str]], *,
    automatic: bool = False, wait_seconds: float = 90.0, require_ai_gate: bool = True,
) -> str:
    """Deduplicate all title callers; only the claim winner invokes ``call``.

    The caller owns provider configuration and metering. Keep its quota reserve
    inside call so cache hits or waiting for another owner do not consume quota.
    Digest callers may retain their existing system policy with require_ai_gate=False;
    automatic generation always checks the global gate regardless of that flag.
    """
    title = (title or "").strip()
    deadline = time.monotonic() + max(0, wait_seconds)
    lease_seconds = 180
    while True:
        token, cached = await _acquire_owned(engine, article_id, title, automatic, lease_seconds, require_ai_gate)
        if cached is not None:
            return cached
        if token:
            break
        if time.monotonic() >= deadline:
            raise TitlePending("标题翻译正在进行，请稍后继续")
        await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))

    async def renew():
        while True:
            await asyncio.sleep(10)
            await asyncio.to_thread(_renew, engine, article_id, title, token, automatic, lease_seconds, require_ai_gate)

    async def execute():
        value = await call()
        return await asyncio.to_thread(_publish, engine, article_id, title, value, token, automatic, require_ai_gate)

    work, keeper = asyncio.create_task(execute()), asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait((work, keeper), return_when=asyncio.FIRST_COMPLETED)
        if keeper in done:
            await keeper
        return await work
    finally:
        for task in (work, keeper):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, keeper, return_exceptions=True)
        await asyncio.to_thread(_release, engine, article_id, token)


def _pick(engine):
    with Session(engine) as session:
        if not _enabled(session) or not accounts.ai_beta_global_enabled(session):
            return None
        config = daily_brief.resolve_llm_config(session)
        if not config.configured:
            return None
        # Reader jobs go first. A serialized reservation below also enforces
        # one automatic provider request across multiple application workers.
        if session.exec(select(TranslationJobRecord.id).where(
            TranslationJobRecord.status.in_(("queued", "running")),
        ).limit(1)).first():
            return None
        job = session.exec(select(Job).where(
            Job.status.in_(("pending", "running")), Job.retry_at <= time.time(), Job.attempts < MAX_ATTEMPTS,
        ).order_by(Job.updated_at).limit(1)).first()
        if job is None:
            return None
        article = session.get(ArticleRecord, job.article_id)
        if article is None:
            return None
        title = (article.title or "").strip()
        if title_receipt(article) is None and not _budget_fits(
            session, _required_hold(title), dt.date.today().isoformat(),
        ):
            return None
        config = replace(config.for_aux(), thinking_mode="disabled", max_tokens=MAX_OUTPUT_TOKENS)
        return article.id, title, job.title_fp, config


def _required_hold(title):
    prompt = prompts.TRANSLATE_TITLE_SYSTEM_PROMPT + prompts.build_translate_title_user_prompt(title)
    return len(prompt.encode()) + MAX_OUTPUT_TOKENS + 256


def _budget_fits(session, hold, day):
    used = session.exec(select(func.coalesce(func.sum(AiUsageRecord.total_tokens), 0)).where(
        AiUsageRecord.day == day, AiUsageRecord.purpose == USAGE_PURPOSE,
    )).one()
    reserved = session.exec(select(func.coalesce(func.sum(Call.reserved_tokens), 0)).where(
        Call.day == day, Call.status.in_(("reserved", "unknown")),
    )).one()
    return used + reserved + hold <= _daily_limit(session)


def _reserve(engine, article_id, title, title_fp, hold):
    with _write(engine) as session:
        _validate(session, article_id, title, automatic=True)
        job = session.get(Job, article_id)
        if job is None or job.title_fp != title_fp or title_fp != fingerprint(title) or job.attempts >= MAX_ATTEMPTS:
            raise TitlePending("automatic title job changed")
        if session.exec(select(TranslationJobRecord.id).where(
            TranslationJobRecord.status.in_(("queued", "running")),
        ).limit(1)).first():
            raise TitlePending("reader translations have priority")
        active = session.exec(select(Call.id).join(Lease, Lease.article_id == Call.article_id).where(
            Call.status == "reserved", Lease.until > time.time(), Call.article_id != article_id,
        ).limit(1)).first()
        if active:
            raise TitlePending("another automatic title request is active")
        day = dt.date.today().isoformat()
        if not _budget_fits(session, hold, day):
            raise TitlePending("automatic title daily budget reached")
        call = Call(id=uuid.uuid4().hex, article_id=article_id, day=day,
                    reserved_tokens=hold, created_at=time.time())
        session.add(call)
        job.attempts += 1
        job.status, job.updated_at = "running", time.time()
        return call.id


def _settle(engine, call_id, receipts, rejected):
    with _write(engine) as session:
        call = session.get(Call, call_id)
        if call is None or call.recorded:
            return
        if receipts:
            # max_retries=1 below means at most one paid response; a rejected
            # thinking parameter retry is handled by client without usage.
            usage, model = receipts[-1]
            known = bool(usage) and any(k in usage for k in ("total_tokens", "prompt_tokens"))
            ai_usage.record_usage(session, username=None, purpose=USAGE_PURPOSE, model=model,
                                  usage=usage, day=call.day, commit=False)
            call.recorded, call.status = True, "settled" if known else "unknown"
        else:
            call.status = "released" if rejected else "unknown"


def _complete(engine, article_id, title_fp, *, error=None, deferred=False):
    with _write(engine) as session:
        job = session.get(Job, article_id)
        if job is None or job.title_fp != title_fp:
            return
        article = session.get(ArticleRecord, article_id)
        receipt_status = _receipt_status(article) if article else None
        if receipt_status:
            job.status, job.error = receipt_status, ""
        elif article is None or not _local_article(session, article) or not _source_allowed(session, article):
            job.status, job.error = "skipped", ""
        elif error:
            lease = session.get(Lease, article_id)
            if deferred and lease is not None and lease.until > time.time():
                return  # A competing worker must not reset the active owner's state.
            job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "pending"
            job.error = "标题翻译暂未完成" if not deferred else ""
            job.retry_at = time.time() + (60 if deferred else min(3600, 30 * 2 ** job.attempts))
        job.updated_at = time.time()


async def run_once(engine) -> bool:
    picked = await asyncio.to_thread(_pick, engine)
    if picked is None:
        return False
    article_id, title, title_fp, config = picked
    async def call():
        from services.reader_ai import _translate_title
        hold = _required_hold(title)
        call_id = await asyncio.to_thread(_reserve, engine, article_id, title, title_fp, hold)
        receipts, rejected = [], False
        meta = UsageMeta(USAGE_PURPOSE, None, recorder=lambda _meta, usage, model: receipts.append((usage, model)))
        try:
            async with client_session(config) as client:
                return await _translate_title(title, config, meta, client, max_retries=1)
        except LLMHTTPError as exc:
            rejected = 400 <= exc.status_code < 500 and exc.status_code != 408
            raise
        finally:
            await asyncio.to_thread(_settle, engine, call_id, receipts, rejected)
    error = None
    try:
        await translate_shared(engine, article_id, title, call, automatic=True, wait_seconds=0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # provider failures do not stop the worker
        error = exc
        logger.info("Automatic title deferred article=%s reason=%s", article_id, type(exc).__name__)
    await asyncio.to_thread(_complete, engine, article_id, title_fp, error=error, deferred=isinstance(error, TitlePending))
    return True


def _maintenance(engine):
    now = time.time()
    # An idle/budget-blocked worker should not acquire the SQLite writer lock
    # just to issue empty lease/job deletes on every polling tick.
    with Session(engine) as session:
        expired_lease = session.exec(select(Lease.article_id).where(Lease.until <= now).limit(1)).first()
        old_call = session.exec(select(Call.id).where(
            (Call.created_at < now - 30 * 86400)
            | ((Call.status == "reserved") & (Call.created_at < now - 600)),
        ).limit(1)).first()
        exhausted_job = session.exec(select(Job.article_id).outerjoin(Lease, Lease.article_id == Job.article_id).where(
            Job.status.in_(("pending", "running")), Job.attempts >= MAX_ATTEMPTS,
            (Lease.article_id.is_(None)) | (Lease.until <= now),
        ).limit(1)).first()
        if not (expired_lease or old_call or exhausted_job):
            return
    with _write(engine) as session:
        session.exec(delete(Call).where(Call.created_at < time.time() - 30 * 86400))
        session.exec(delete(Lease).where(Lease.until <= time.time()))
        for job in session.exec(select(Job).where(Job.status.in_(("pending", "running")), Job.attempts >= MAX_ATTEMPTS)).all():
            if session.get(Lease, job.article_id) is None:
                article = session.get(ArticleRecord, job.article_id)
                job.status = (_receipt_status(article) if article else None) or "failed"
                job.error = "标题翻译暂未完成" if job.status == "failed" else ""
                job.updated_at = time.time()
        # A terminated provider request's cost remains held for its dispatch day.
        for call in session.exec(select(Call).where(Call.status == "reserved", Call.created_at < time.time() - 600)).all():
            call.status = "unknown"


async def worker_loop(engine):
    while True:
        try:
            await asyncio.to_thread(_maintenance, engine)
            worked = await run_once(engine)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic title worker failed")
            worked = False
        await asyncio.sleep(1 if worked else POLL_SECONDS)
