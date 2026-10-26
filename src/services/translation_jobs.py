"""Durable, reader-initiated translation with fenced checkpoints and cost holds.

One leased job per deployment, up to map_concurrency (capped at 8) model calls.
SQLite write transactions serialize admission, claims, cancellation and publish.
No source snapshot or provider credential is persisted in these local tables.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import logging
import time
import uuid

from sqlalchemy import delete, text
from sqlmodel import Session, func, select

from llm.client import UsageMeta, client_session, LLMHTTPError
from llm import prompts
from models.db import (
    AiUsageRecord, ArticleRecord, TranslationCallRecord as Call,
    TranslationChunkRecord as Chunk, TranslationJobRecord as Job,
)
from services import accounts, ai_usage, daily_brief, guest_access, reader_ai, source_visibility, user_sources
from services import translation_preview, translation_updates
from services.error_redaction import sanitize_error
from services.translation_markdown import plan_translation, translation_units, TranslationStructureError

logger = logging.getLogger(__name__)
Error = reader_ai.ReaderAIError


class InputChanged(Error):
    """The checkpoint identity no longer describes the current article/config."""


class GuestQuotaError(Error):
    """访客当日翻译额度用尽(issue #213):worker 按预算类错误停任务,请求路径转成游客额度码。"""


class LeaseLost(Error):
    """An expired owner cannot commit; another claim may safely continue."""


PIPELINE_VERSION = 1
TARGET = "zh-CN"
MAX_JOBS = 500
MAX_USER_JOBS = 3
MAX_BODY_BYTES = 2_000_000
MAX_CHUNKS = 1000
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 128 * 1024
RETENTION_SECONDS = 7 * 86400
MAX_ATTEMPTS = 3
DAILY_CALL_LIMIT = 50
POLL_SECONDS = 2
HEARTBEAT_SECONDS = 5
MIN_LEASE_SECONDS = 180


@contextmanager
def _write(engine):
    with Session(engine) as session:
        # The deployed store is SQLite. Acquire the writer before reading any
        # state used for quota/claim/publication decisions (no read-upgrade race).
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def config_key(config) -> str:
    values = asdict(config)
    # Only an opaque digest leaves this function. Credential rotation invalidates
    # checkpoints, but operational timeout/concurrency changes do not.
    for name in ("timeout_seconds", "map_concurrency", "aux_model", "vision_model"):
        values.pop(name, None)
    return _hash(values)


def input_key(article, config) -> str:
    return _hash([
        article.id, article.source_id, (article.content or "").strip(),
        (article.title or "").strip(), TARGET, reader_ai.TRANSLATION_VERSION,
        PIPELINE_VERSION, reader_ai._TRANSLATE_SEGMENT_CHARS, config_key(config),
        _hash(_cached_body(article)) if _cached_body(article) else "",
    ])


def authorize(session, username, article):
    # 访客身份按游客保留账号授权(issue #213):其 ai_beta_enabled 即「游客 AI」开关。
    user = accounts.get_account_for(session, username)
    if user is None or not user.is_active:
        raise Error("账户不可用", 403)
    if not accounts.ai_beta_global_enabled(session) or not user.ai_beta_enabled:
        raise Error("AI 功能已关闭，恢复后可继续翻译", 403)
    if article is None:
        raise Error("文章不存在", 404)
    if user.role != "admin" and article.source_id in source_visibility.reader_unavailable_source_ids(session):
        raise Error("文章不存在", 404)
    if (user_sources.is_user_source(article.source_id)
            and user_sources.unauthorized_user_source_ids(session, username, [article.source_id])):
        raise Error("文章不存在", 404)
    if not user_sources.source_content_may_leave_deployment(session, article.source_id):
        raise Error("该来源不能发送到 AI 服务", 403)


def _current(session, job, *, username=None):
    article = session.get(ArticleRecord, job.article_id)
    authorize(session, username or job.created_by, article)
    config = daily_brief.resolve_llm_config(session)
    if not config.configured:
        raise Error("AI 服务暂未就绪", 403)
    if input_key(article, config) != job.input_key:
        raise InputChanged("原文或模型配置已更新，请重新发起翻译", 409)
    return article, config


def _cached_body(article):
    ext = reader_ai._load_extensions(article)
    body = (article.content or "").strip()
    if not body:
        return None
    if not reader_ai._cache_valid(ext, reader_ai.TRANSLATION_KEY, reader_ai.TRANSLATION_FP_KEY,
                                 reader_ai._body_fingerprint(body)):
        return None
    if (reader_ai.paper_metadata(article).get("content_level") in {"fulltext", "partial"}
            and ext.get(reader_ai.TRANSLATION_VERSION_KEY) != reader_ai.TRANSLATION_VERSION):
        return None
    return ext[reader_ai.TRANSLATION_KEY]


def _cached_title(article):
    from services.title_cache import title_receipt
    title = (article.title or "").strip()
    return title_receipt(article) if title and not reader_ai.looks_chinese(title) else title


def cached_result(article, _config=None):
    body = _cached_body(article)
    if body is None:
        return None
    return {"translation": body, "title": _cached_title(article), "cached": True}


def _plan(article, key, *, total=None):
    body = (article.content or "").strip()
    if not body:
        raise Error("该文章暂无可翻译的正文", 400)
    if len(body.encode()) > MAX_BODY_BYTES:
        raise Error("正文超过后台翻译容量上限", 422)
    try:
        plan = (plan_translation(body, segment_chars=reader_ai._TRANSLATE_SEGMENT_CHARS, namespace=key[:12])
                if _cached_body(article) is None else plan_translation("", namespace=key[:12]))
    except TranslationStructureError as exc:
        raise Error(str(exc), 422) from exc
    if not plan.chunks and _cached_body(article) is None:
        raise Error("该文章暂无可翻译的正文", 400)
    title_needed = (_cached_title(article) is None if total is None else _planned_title(total, plan))
    if len(plan.chunks) + int(title_needed) > MAX_CHUNKS:
        raise Error("正文片段数超过后台翻译上限", 422)
    return plan, title_needed


def _payload(job, username, session=None):
    can_resume = job.status in {"failed", "cancelled"}
    retry_after = None
    if can_resume and session is not None:
        try:
            _cost_gate(session, username, 0)
        except Error as exc:
            if exc.status_code == 429:
                can_resume = False
                tomorrow = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time.min)
                retry_after = max(1, int((tomorrow - dt.datetime.now()).total_seconds()))
    return {
        "job_id": job.id, "article_id": job.article_id, "status": job.status,
        "completed": job.completed, "total": job.total, "error": job.error,
        "can_cancel": job.created_by == username and job.status in {"queued", "running"},
        "can_resume": can_resume, "retry_after": retry_after,
    }


def _preview(session, article, job, cursor=None):
    if job is None or job.status == "stale" or _cached_body(article) is not None:
        return None
    plan, _ = _plan(article, job.input_key, total=job.total)
    rows = session.exec(select(Chunk).where(Chunk.job_id == job.id)).all()
    return translation_preview.project(
        plan, {row.ordinal: row.text for row in rows if row.text},
        job_id=job.id, input_key=job.input_key, cursor=cursor,
    )


def _cleanup(session, now):
    stale = select(Job.id).where(Job.updated_at < now - RETENTION_SECONDS, Job.lease_until <= now)
    # Explicit child delete also works when a test engine has FK enforcement off.
    session.exec(delete(Chunk).where(Chunk.job_id.in_(stale)))
    session.exec(delete(Job).where(Job.id.in_(stale)))
    session.exec(delete(Call).where(Call.created_at < now - 30 * 86400))
    # Published translations live in articles; receipt rows must not fill the queue.
    _delete_jobs(session, select(Job.id).where(Job.status == "succeeded", Job.lease_until <= now))


def _delete_jobs(session, ids):
    session.exec(delete(Chunk).where(Chunk.job_id.in_(ids)))
    session.exec(delete(Job).where(Job.id.in_(ids)))


def _user_slot(session, username, exclude_id=""):
    rows = session.exec(select(Job).where(
        Job.created_by == username, Job.id != exclude_id,
        (Job.status.in_(("queued", "running"))) | (Job.completed > 0),
    ).order_by(Job.updated_at)).all()
    evicted = False
    while len(rows) >= MAX_USER_JOBS:
        oldest = next((item for item in rows if item.status in {"failed", "cancelled", "stale"}
                       and item.lease_until <= time.time()), None)
        if oldest is None:
            raise Error("已有 3 篇文章正在翻译，请等待完成后再试", 429)
        _delete_jobs(session, [oldest.id])
        rows.remove(oldest)
        evicted = True
    return evicted


def request(engine, username, article_id, *, resume_id=None):
    with _write(engine) as session:
        now = time.time()
        _cleanup(session, now)
        article = session.get(ArticleRecord, article_id)
        authorize(session, username, article)
        config = daily_brief.resolve_llm_config(session)
        if not config.configured:
            raise Error("AI 服务暂未就绪", 403)
        cached = cached_result(article, config)
        if cached and cached["title"] is not None:
            return {"status": "succeeded", "result": cached, "job": None}
        key = input_key(article, config)
        job = session.exec(select(Job).where(Job.input_key == key)).first()
        if resume_id and (job is None or job.id != resume_id):
            raise Error("原文或模型配置已更新，请重新发起翻译", 409)
        if job and job.status in {"queued", "running"}:
            return {"status": job.status, "job": _payload(job, username, session), "result": cached,
                    "preview": _preview(session, article, job)}
        if job and job.status in {"failed", "cancelled", "stale"} and not resume_id:
            return {"status": job.status, "job": _payload(job, username, session), "result": cached,
                    "preview": _preview(session, article, job)}
        # Check before allocating persistent storage. Recheck/reserve each actual
        # provider call in the worker; joining an existing job has no cost.
        plan, title_needed = _plan(article, key, total=job.total if job else None)
        if title_needed or any(translation_units(chunk.text) for chunk in plan.chunks):
            _cost_gate(session, username, 0)
        evicted = _user_slot(session, username, job.id if job else "")
        if job is None:
            if session.exec(select(func.count()).select_from(Job)).one() >= MAX_JOBS:
                _delete_jobs(session, select(Job.id).where(
                    Job.status.in_(("failed", "cancelled", "stale")),
                    Job.completed == 0, Job.lease_until <= now,
                ))
                if session.exec(select(func.count()).select_from(Job)).one() >= MAX_JOBS:
                    raise Error("后台翻译队列已满，请稍后再试", 429)
            job = Job(id=uuid.uuid4().hex, input_key=key, article_id=article_id,
                      created_by=username, total=len(plan.chunks) + int(title_needed),
                      created_at=now, updated_at=now)
            session.add(job)
            session.flush()
            for index in range(job.total):
                session.add(Chunk(job_id=job.id, ordinal=index))
        else:
            if job.lease_until > now:
                raise Error("任务正在停止，请稍后继续", 409)
            rows = session.exec(select(Chunk).where(Chunk.job_id == job.id)).all()
            if not rows:  # Completed cache removed by another article metadata update.
                for index in range(job.total):
                    session.add(Chunk(job_id=job.id, ordinal=index))
                job.completed = 0
            else:
                for row in rows:
                    if not row.text:
                        row.attempts = 0
            job.created_by, job.status, job.error, job.retry_at = username, "queued", "", 0
            job.updated_at = now
        session.flush()
        return {"status": job.status, "job": _payload(job, username, session), "result": cached,
                "preview": _preview(session, article, job), "evicted_progress": evicted}


def status(engine, username, article_id, preview_cursor=None):
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        authorize(session, username, article)
        config = daily_brief.resolve_llm_config(session)
        if not config.configured:
            raise Error("AI 服务暂未就绪", 403)
        cached = cached_result(article, config)
        job = session.exec(select(Job).where(Job.input_key == input_key(article, config))).first()
        if cached and cached["title"] is not None:
            return {"status": "succeeded", "result": cached, "job": _payload(job, username, session) if job else None}
        return {"status": job.status if job else "idle", "job": _payload(job, username, session) if job else None,
                "result": cached, "preview": _preview(session, article, job, preview_cursor)}


def cancel(engine, username, job_id):
    with _write(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.created_by != username:
            raise Error("翻译任务不存在", 404)
        # Cancelling sends no content externally; allow it even after AI is gated.
        if job.status in {"queued", "running"}:
            job.status, job.error, job.updated_at = "cancelled", "", time.time()
        result = {"status": job.status, "job": _payload(job, username, session)}
        article_id = job.article_id
    translation_updates.notify(engine, article_id)
    return result


def resume(engine, username, job_id):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise Error("翻译任务不存在", 404)
        article_id = job.article_id
    return request(engine, username, article_id, resume_id=job_id)


def _cost_gate(session, username, reserve_tokens):
    today = dt.date.today().isoformat()
    holds = (Call.day == today, Call.status.in_(("reserved", "unknown")))
    pending_tokens = session.exec(select(func.coalesce(func.sum(Call.reserved_tokens), 0)).where(*holds)).one()
    pending_calls = session.exec(select(func.count()).select_from(Call).where(
        *holds, Call.username == username, Call.recorded == False)).one()  # noqa: E712
    calls = session.exec(select(func.coalesce(func.sum(AiUsageRecord.calls), 0)).where(
        AiUsageRecord.day == today, AiUsageRecord.username == username, AiUsageRecord.purpose == "translate")).one()
    # 全站预算先判(与读者 AI 的 _enforce_ai_cost_gates 同序):两者同时耗尽时给预算文案——
    # 登录解决不了全站预算,不能把访客引去登录(issue #213 检视 F9)。
    budget = accounts.ai_daily_token_budget(session)
    used = accounts.reader_ai_tokens_today(session)
    if budget and (used + pending_tokens >= budget or used + pending_tokens + reserve_tokens > budget):
        raise Error("今日 AI 预算不足，已完成片段会保留，请明日继续", 429)
    if accounts.is_guest_visitor(username):
        # 访客分别计次,额度缺省与读者同额、管理员可调;0 = 不限。
        limit = guest_access.guest_ai_limit(session, "translate", DAILY_CALL_LIMIT)
        if limit and calls + pending_calls >= limit:
            raise GuestQuotaError(guest_access.GUEST_QUOTA_MESSAGE, 429)
    elif calls + pending_calls >= DAILY_CALL_LIMIT:
        raise Error("今日翻译次数已达上限，已完成片段会保留，请明日继续", 429)


def claim(engine):
    now = time.time()
    with _write(engine) as session:
        _cleanup(session, now)
        # Include a cancelled worker still draining its provider calls.
        if session.exec(select(Job.id).where(Job.lease_until > now).limit(1)).first():
            return None
        job = session.exec(select(Job).where(
            Job.status.in_(("queued", "running")), Job.retry_at <= now
        ).order_by(Job.created_at).limit(1)).first()
        if job is None:
            return None
        # An expired process may have sent a request without receiving its usage.
        for call in session.exec(select(Call).where(Call.job_id == job.id, Call.status == "reserved")).all():
            call.status = "unknown"
        try:
            _, config = _current(session, job)
        except Error as exc:
            job.status = "stale" if isinstance(exc, InputChanged) else "failed"
            job.error, job.updated_at = str(exc), now
            return None
        job.status, job.lease_token = "running", uuid.uuid4().hex
        job.error = ""
        job.lease_until = now + max(MIN_LEASE_SECONDS, config.timeout_seconds * 4 + 30)
        job.updated_at = now
        session.flush()
        return job.id, job.lease_token


def _fenced(session, job_id, token):
    job = session.get(Job, job_id)
    if job is None or job.status != "running" or job.lease_token != token or job.lease_until <= time.time():
        raise LeaseLost("翻译任务已停止或由其它进程接管", 409)
    return job


def heartbeat(engine, job_id, token):
    with _write(engine) as session:
        job = _fenced(session, job_id, token)
        _, config = _current(session, job)
        job.lease_until = time.time() + max(MIN_LEASE_SECONDS, config.timeout_seconds * 4 + 30)


def _reserve(engine, job_id, token, ordinal, reserve_tokens):
    with _write(engine) as session:
        job = _fenced(session, job_id, token)
        _current(session, job)
        row = session.get(Chunk, (job_id, ordinal))
        if row.text or row.attempts >= MAX_ATTEMPTS:
            raise Error("片段已完成或重试次数已用尽", 409)
        _cost_gate(session, job.created_by, reserve_tokens)
        row.attempts += 1
        call = Call(id=uuid.uuid4().hex, job_id=job_id, username=job.created_by,
                    day=dt.date.today().isoformat(), reserved_tokens=reserve_tokens, created_at=time.time())
        session.add(call)
        session.flush()
        return call.id, job.created_by


def _settle(engine, call_id, usage, model):
    with _write(engine) as session:
        call = session.get(Call, call_id)
        if call is None or call.recorded:
            return
        # Missing usage is not zero cost. Still record the successful response's
        # call count; retain its conservative token hold until the day rolls over.
        known = bool(usage) and any(name in usage for name in ("total_tokens", "prompt_tokens"))
        ai_usage.record_usage(session, username=call.username, purpose="translate",
                              model=model, usage=usage, day=call.day, commit=False)
        call.status = "settled" if known else "unknown"
        call.recorded = True


def _unknown(engine, call_id):
    with _write(engine) as session:
        call = session.get(Call, call_id)
        if call and call.status == "reserved":
            call.status = "unknown"


def _release(engine, call_id):
    with _write(engine) as session:
        call = session.get(Call, call_id)
        if call and call.status == "reserved" and not call.recorded:
            call.status = "released"


def _checkpoint(engine, job_id, token, ordinal, value):
    encoded = len(value.encode())
    if encoded > MAX_CHUNK_BYTES:
        raise Error("译文片段超过存储上限", 422)
    with _write(engine) as session:
        job = _fenced(session, job_id, token)
        _current(session, job)
        row = session.get(Chunk, (job_id, ordinal))
        size = session.exec(select(func.coalesce(func.sum(Chunk.size_bytes), 0))).one()
        if size - row.size_bytes + encoded > MAX_CHECKPOINT_BYTES:
            raise Error("翻译检查点空间已满，请稍后继续", 429)
        if not row.text:
            job.completed += 1
        row.text, row.size_bytes = value, encoded
        job.updated_at = time.time()
        article_id = job.article_id
    translation_updates.notify(engine, article_id)


def _planned_title(total, plan):
    # Title caches can be filled independently by the personal digest. Only the
    # persisted layout determines whether this job owns a title checkpoint.
    count = total - len(plan.chunks)
    if count not in (0, 1):
        raise InputChanged("翻译计划已变化，请重新发起翻译", 409)
    return bool(count)


def _available_title(engine, job_id, token):
    with Session(engine) as session:
        job = _fenced(session, job_id, token)
        article, _ = _current(session, job)
        return _cached_title(article)


def _finish(engine, job_id, token, plan):
    with _write(engine) as session:
        job = _fenced(session, job_id, token)
        article, _ = _current(session, job)
        title_needed = _planned_title(job.total, plan)
        rows = session.exec(select(Chunk).where(Chunk.job_id == job_id).order_by(Chunk.ordinal)).all()
        if len(rows) != job.total or any(not row.text for row in rows):
            raise Error("翻译片段尚未全部完成", 409)
        translated = (_cached_body(article) if not plan.chunks
                      else plan.assemble([row.text for row in rows[:len(plan.chunks)]]))
        title = rows[-1].text if title_needed else _cached_title(article)
        ext = reader_ai._load_extensions(article)
        if plan.chunks:
            ext.update({
                reader_ai.TRANSLATION_KEY: translated,
                reader_ai.TRANSLATION_FP_KEY: reader_ai._body_fingerprint((article.content or "").strip()),
                reader_ai.TRANSLATION_VERSION_KEY: reader_ai.TRANSLATION_VERSION,
            })
        if title is not None:
            ext.update({
                reader_ai.TRANSLATION_TITLE_KEY: title,
                reader_ai.TRANSLATION_TITLE_FP_KEY: reader_ai._body_fingerprint((article.title or "").strip()),
            })
        ext.pop("translation_zh_config", None)
        # Article merge + job completion + checkpoint removal share one write
        # transaction, serialized against cancellation and source replacement.
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.exec(delete(Chunk).where(Chunk.job_id == job_id))
        article_id = job.article_id
        session.delete(job)
    translation_updates.notify(engine, article_id)


async def run_job(engine, job_id, token):
    async def renew():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await asyncio.to_thread(heartbeat, engine, job_id, token)

    def load_work():
        with Session(engine) as session:
            job = _fenced(session, job_id, token)
            article, config = _current(session, job)
            plan, _ = _plan(article, job.input_key, total=job.total)
            title = (article.title or "").strip()
            return config, plan, title, article.id

    def missing_chunks():
        with Session(engine) as session:
            return session.exec(select(Chunk).where(Chunk.job_id == job_id, Chunk.text == "")).all()

    async def work():
        config, plan, title, article_id = await asyncio.to_thread(load_work)
        semaphore = asyncio.Semaphore(max(1, min(8, config.map_concurrency)))
        stopping_error = None
        async with client_session(config) as client:
            async def translate(ordinal):
                nonlocal stopping_error
                async with semaphore:
                    if stopping_error is not None:
                        raise stopping_error
                    chunk = plan.chunks[ordinal] if ordinal < len(plan.chunks) else None
                    if chunk is None:
                        cached_title = await asyncio.to_thread(_available_title, engine, job_id, token)
                        if cached_title is not None:
                            await asyncio.to_thread(_checkpoint, engine, job_id, token, ordinal, cached_title)
                            return
                    if chunk is not None and not translation_units(chunk.text):
                        await asyncio.to_thread(_checkpoint, engine, job_id, token, ordinal, chunk.text)
                        return
                    async def call_provider():
                        nonlocal stopping_error
                        prompt = (prompts.TRANSLATE_SYSTEM_PROMPT + prompts.build_translate_user_prompt(
                            title, json.dumps({"units": translation_units(chunk.text)}, ensure_ascii=False))
                            if chunk is not None else prompts.TRANSLATE_TITLE_SYSTEM_PROMPT
                            + prompts.build_translate_title_user_prompt(title))
                        # UTF-8 bytes conservatively bound input tokens without a
                        # provider-specific tokenizer; max_tokens bounds output.
                        hold = len(prompt.encode()) + max(1, config.max_tokens) + 256
                        call_id, username = await asyncio.to_thread(_reserve, engine, job_id, token, ordinal, hold)
                        receipts = []
                        meta = UsageMeta("translate", username,
                                         recorder=lambda _meta, usage, model: receipts.append((usage, model)))
                        rejected = False
                        try:
                            if chunk is not None:
                                value = await reader_ai._translate_segment(title, chunk.text, config, meta, client, max_retries=1)
                                value = chunk.validate(value)
                            else:
                                value = await reader_ai._translate_title(title, config, meta, client, max_retries=1)
                            if not value.strip():
                                raise TranslationStructureError("翻译片段为空")
                            for usage, model in receipts:
                                await asyncio.to_thread(_settle, engine, call_id, usage, model)
                            return value
                        except LLMHTTPError as exc:
                            rejected = 400 <= exc.status_code < 500 and exc.status_code != 408
                            if not exc.retryable:
                                stopping_error = exc
                            raise
                        finally:
                            for usage, model in receipts:
                                await asyncio.to_thread(_settle, engine, call_id, usage, model)
                            if rejected:
                                await asyncio.to_thread(_release, engine, call_id)
                            await asyncio.to_thread(_unknown, engine, call_id)

                    if chunk is None:
                        from services.title_translation import translate_shared
                        value = await translate_shared(engine, article_id, title, call_provider)
                    else:
                        value = await call_provider()
                    await asyncio.to_thread(_checkpoint, engine, job_id, token, ordinal, value)

            for cycle in range(MAX_ATTEMPTS):
                missing = await asyncio.to_thread(missing_chunks)
                if not missing:
                    await asyncio.to_thread(_finish, engine, job_id, token, plan)
                    return
                eligible = [row.ordinal for row in missing if row.attempts < MAX_ATTEMPTS]
                if not eligible:
                    raise Error("部分片段翻译失败，已完成片段已保留，可继续翻译", 502)
                tasks = [asyncio.create_task(translate(index)) for index in eligible]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                errors = [result for result in results if isinstance(result, BaseException)]
                if errors:
                    if stopping_error is not None:
                        raise stopping_error
                    # Budget/permission/input/lease errors need user/config change.
                    if any(isinstance(e, Error) and e.status_code != 502 for e in errors):
                        raise next(e for e in errors if isinstance(e, Error) and e.status_code != 502)
                    if cycle == MAX_ATTEMPTS - 1:
                        raise errors[0]
                    await asyncio.sleep(2 ** cycle)
            await asyncio.to_thread(_finish, engine, job_id, token, plan)

    worker, keeper = asyncio.create_task(work()), asyncio.create_task(renew())
    error = None
    try:
        done, _ = await asyncio.wait((worker, keeper), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        # Shutdown leaves the job resumable after its lease; successful chunks
        # remain. Never equate losing the HTTP/browser connection with cancelling.
        raise
    except Exception as exc:
        error = exc
    finally:
        worker.cancel()
        keeper.cancel()
        await asyncio.gather(worker, keeper, return_exceptions=True)
        with _write(engine) as session:
            job = session.get(Job, job_id)
            if job and job.lease_token == token:
                for call in session.exec(select(Call).where(Call.job_id == job_id, Call.status == "reserved")).all():
                    call.status = "unknown"
                if error and job.status == "running":
                    job.status = ("queued" if isinstance(error, LeaseLost) and job.lease_until <= time.time()
                                  else "stale" if isinstance(error, InputChanged) else "failed")
                    job.error = sanitize_error(error)
                    job.updated_at = time.time()
                job.lease_token, job.lease_until = "", 0
            article_id = job.article_id if job else None
        if article_id:
            translation_updates.notify(engine, article_id)


async def run_once(engine):
    claimed = await asyncio.to_thread(claim, engine)
    if claimed:
        await run_job(engine, *claimed)
    return bool(claimed)


async def worker_loop(engine):
    while True:
        try:
            if await run_once(engine):
                continue
        except Exception:
            logger.exception("后台翻译 worker 异常；未完成任务将在租约到期后恢复")
        await asyncio.sleep(POLL_SECONDS)
