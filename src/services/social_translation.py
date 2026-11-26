"""推文预译:新入库 / 原文变化的推文在后台译成中文(issue #349)。

做法对齐标题自动翻译(``title_translation``):

- 待办与推文同事务落库(``before_flush``),提交后才可领取。抓取、推送、手工收录,以及同一条再次到达时
  的补全与换成抓取版(``DatabaseStorage._complete_social_post``),都是经 ORM 写 ``ArticleRecord``——
  钩子挂在写入层,不分入库方式;
- 不扫描存量:首次启用记一个水位,之后只处理新记录与原文的后续变化;
- 只在内容的本机权威节点派发(同步来的条目不在本机重复翻译),遵守 AI 总闸与来源外送许可;
- 独立用途 ``auto_social_translate`` 与每日 token 预留 / 核销:不扣读者额度,也不占标题自动翻译的预算;
- 译文写进既有缓存 ``extensions_json.translation_zh_social``(与读者手动翻译同一份、同一格式),随
  ``articles`` 流同步;实际调用复用 ``reader_ai.translate_social_post``,提示词与结构校验只有一套。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import datetime as dt
import json
import logging
import time
import uuid

from sqlalchemy import delete, event, inspect, text
from sqlalchemy.orm import Session as SASession
from sqlmodel import Session, func, select

from llm.client import LLMHTTPError, UsageMeta
from llm import prompts
from models.db import (
    AiUsageRecord, AppSettingRecord, ArticleRecord, TranslationJobRecord,
    SocialTranslationCallRecord as Call, SocialTranslationJobRecord as Job,
)
from services import accounts, ai_usage, daily_brief, sync_consumer_policy, user_sources
from services.title_cache import extensions
from services.title_translation import SUPPRESS_ENQUEUE_KEY

logger = logging.getLogger(__name__)
ENABLED_KEY = "auto_social_translation_enabled"
DAILY_TOKENS_KEY = "auto_social_translation_daily_tokens"
WATERMARK_KEY = "auto_social_translation_started_at"
DEFAULT_DAILY_TOKENS = 300_000
# 自动翻译只管常规长度的推文;更长的(个别超长 note tweet)留给读者手动翻译,不让一条吃掉当天预算。
MAX_SOURCE_CHARS = 6000
MAX_ATTEMPTS = 3
POLL_SECONDS = 10
# 一次派发的占用期与续租间隔:在途期间持有者定时续租,进程中途退出时到期自动回到可领取。
LEASE_SECONDS = 180
RENEW_SECONDS = 30
USAGE_PURPOSE = "auto_social_translate"
CONTENT_TYPE = "social_post"
_hooks_installed = False


class SocialPending(Exception):
    """闸门关着、预算不够或另有派发在途:这次不算一次失败的尝试。"""


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


def _sources_of(content, extension_values: dict) -> dict:
    from services.reader_ai import social_translation_sources
    return social_translation_sources((content or "").strip(), extension_values)


def _sources(article) -> dict:
    """需要翻译的原文 {"text"?, "quoted"?},与手动翻译、译文缓存指纹同一口径。"""
    return _sources_of(article.content, extensions(article))


def _fingerprint(sources: dict) -> str:
    from services.reader_ai import _social_fingerprint
    return _social_fingerprint(sources)


def _translated(article) -> bool:
    from services.reader_ai import cached_social_translation
    return cached_social_translation(article.content or "", extensions(article)) is not None


def _too_long(sources: dict) -> bool:
    return sum(len(value) for value in sources.values()) > MAX_SOURCE_CHARS


def _local_article(session, article) -> bool:
    return not (article.analysis_authority_id or "").strip() and sync_consumer_policy.local_source_operation_allowed(
        session, article.source_id, operation="collection",
    )


def _source_allowed(session, article) -> bool:
    return user_sources.source_content_may_leave_deployment(session, article.source_id)


def _initial_status(article, sources: dict) -> str:
    if not sources:
        return "skipped"  # 中文 / 纯链接 / 纯表情:没有要译的
    if _translated(article):
        return "succeeded"  # 读者已手动译过,或同一份原文的译文还在
    return "skipped" if _too_long(sources) else "pending"


def initialize(engine) -> None:
    """记下首次启用的水位;永不枚举或回填存量推文。"""
    with _write(engine) as session:
        if session.get(AppSettingRecord, WATERMARK_KEY) is None:
            session.add(AppSettingRecord(key=WATERMARK_KEY, value=dt.datetime.now(dt.timezone.utc).isoformat()))
    install_hooks()


def _sources_before(article) -> dict | None:
    """本次写入之前的原文;旧值读不到(属性未加载就被赋值)时返回 None。"""
    state = inspect(article)
    values = {}
    for name in ("content", "extensions_json"):
        history = state.attrs[name].history
        if not history.has_changes():
            values[name] = getattr(article, name)
        elif history.deleted:
            values[name] = history.deleted[0]
        else:
            return None
    return _sources_of(values["content"], extensions({"extensions_json": values["extensions_json"]}))


def enqueue(session, article, *, is_new: bool) -> None:
    """新推文,或正文 / 扩展字段有改动的推文;在它自己的事务里调用。"""
    if session.get(AppSettingRecord, WATERMARK_KEY) is None:
        return
    row = session.get(Job, article.id)
    if row is None:
        row = next((value for value in session.new if isinstance(value, Job) and value.article_id == article.id), None)
    sources = _sources(article)
    fp = _fingerprint(sources)
    if row is not None and row.source_fp == fp:
        # 原文没变:译文缓存、标题回执、配图等无关写入都走到这里。唯一的例外是「记着已完成、译文却没了」
        # (别的写入方把缓存冲掉了)——不重排的话这一条永远没有中文。
        if not (row.status == "succeeded" and sources and not _translated(article)):
            return
    if row is None and not is_new:
        # 启用前入库的存量推文没有待办。只有需要翻译的原文确实变了(补上引用帖、换成抓取版)才入队;
        # 旧值读不到时按没变处理——一次无关写入不能变成对存量的隐性回填。
        before = _sources_before(article)
        if before is None or before == sources:
            return
    now = time.time()
    # 重排一条正在派发的待办时不动它的占用(lease_until / lease_token):在途请求结束前别人领不到这一条,
    # 在途方收尾时发现指纹已变、占用还是自己的,再把占用交还(见 _complete)。
    if session.info.get(SUPPRESS_ENQUEUE_KEY):
        # 归档导入:可以让本机一条过期的待办作废,但决不新建本机的自动派发。
        if row is not None:
            row.source_fp, row.status = fp, "succeeded" if _translated(article) else "skipped"
            row.attempts, row.retry_at, row.error, row.updated_at = 0, 0, "", now
        return
    # 同步来的公共条目由它的权威节点翻译;不可外送的来源不入队——来源政策日后放开也不该把存量变成回填。
    if not _local_article(session, article) or not _source_allowed(session, article):
        if row is not None:
            row.source_fp, row.status, row.updated_at = fp, "skipped", now
        return
    status = _initial_status(article, sources)
    if row is None:
        session.add(Job(article_id=article.id, source_fp=fp, status=status, updated_at=now))
    else:
        row.source_fp, row.status, row.attempts, row.retry_at = fp, status, 0, 0
        row.error, row.updated_at = "", now


def install_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    event.listen(SASession, "before_flush", _queue_changed_posts)
    _hooks_installed = True


def _queue_changed_posts(session, _context, _instances):
    # 先快照再加待办行。直接走 ORM 的写入方、存储层的保存 / 补全 / 更新都落在同一个事务里。
    for article in list(session.new) + list(session.dirty):
        if not isinstance(article, ArticleRecord) or article in session.deleted:
            continue
        if article.content_type != CONTENT_TYPE:
            continue
        is_new = article in session.new
        if not is_new:
            attrs = inspect(article).attrs
            if not (attrs.content.history.has_changes() or attrs.extensions_json.history.has_changes()):
                continue
        enqueue(session, article, is_new=is_new)


def _reader_jobs_active(session) -> bool:
    return session.exec(select(TranslationJobRecord.id).where(
        TranslationJobRecord.status.in_(("queued", "running")),
    ).limit(1)).first() is not None


def _required_hold(sources: dict) -> int:
    """一次派发的预留量:提示词 + 原文(请求里一份、译文里约一份)的字节数,按字节算只会偏多。"""
    source_bytes = sum(len(value.encode()) for value in sources.values())
    return len(prompts.SOCIAL_TRANSLATE_SYSTEM_PROMPT.encode()) + 3 * source_bytes + 512


def _budget_fits(session, hold: int, day: str) -> bool:
    used = session.exec(select(func.coalesce(func.sum(AiUsageRecord.total_tokens), 0)).where(
        AiUsageRecord.day == day, AiUsageRecord.purpose == USAGE_PURPOSE,
    )).one()
    reserved = session.exec(select(func.coalesce(func.sum(Call.reserved_tokens), 0)).where(
        Call.day == day, Call.status.in_(("reserved", "unknown")),
    )).one()
    return used + reserved + hold <= _daily_limit(session)


def _pick(engine):
    with Session(engine) as session:
        if not _enabled(session) or not accounts.ai_beta_global_enabled(session):
            return None
        config = daily_brief.resolve_llm_config(session)
        if not config.configured:
            return None
        # 读者自己发起的翻译优先;下面的预留还会在多个应用进程之间只放行一个自动请求。
        if _reader_jobs_active(session):
            return None
        now = time.time()
        # 已有一个自动请求在途(可能在另一个应用进程):这一拍不领,也不去把别的待办一条条推迟。
        if session.exec(select(Job.article_id).where(Job.lease_until > now).limit(1)).first():
            return None
        job = session.exec(select(Job).where(
            Job.status.in_(("pending", "running")), Job.retry_at <= now, Job.attempts < MAX_ATTEMPTS,
        ).order_by(Job.updated_at).limit(1)).first()
        if job is None:
            return None
        article = session.get(ArticleRecord, job.article_id)
        if article is None:
            return None
        sources = _sources(article)
        hold = _required_hold(sources)
        if not _translated(article) and not _budget_fits(session, hold, dt.date.today().isoformat()):
            return None  # 当天预算用完:待办留着,次日继续
        # 与标题自动翻译同一档:辅助模型、明确关闭思考(短文本直译,思考只会把输出配额吃掉)。
        return article.id, job.source_fp, hold, replace(config.for_aux(), thinking_mode="disabled")


def _gates_open(session, article) -> bool:
    return (
        _enabled(session) and accounts.ai_beta_global_enabled(session)
        and _local_article(session, article) and _source_allowed(session, article)
    )


def _reserve(engine, article_id: str, source_fp: str, hold: int):
    """占用这条待办并预留预算,返回 (预留 id, 占用 token)。译文已经有了(读者刚手动译过)返回 None,不必调用。"""
    with _write(engine) as session:
        job = session.get(Job, article_id)
        article = session.get(ArticleRecord, article_id)
        if job is None or article is None or job.source_fp != source_fp or job.attempts >= MAX_ATTEMPTS:
            raise SocialPending("automatic social job changed")
        if _fingerprint(_sources(article)) != source_fp:
            raise SocialPending("social post changed")
        if not _gates_open(session, article):
            raise SocialPending("automatic social translation is not allowed")
        now = time.time()
        if job.lease_until > now:
            raise SocialPending("this social job is already dispatched")
        if _translated(article):
            return None
        if _reader_jobs_active(session):
            raise SocialPending("reader translations have priority")
        # 看占用期而不是状态:被钩子重排成 pending 的在途条目同样算「有一个自动请求在途」。
        if session.exec(select(Job.article_id).where(
            Job.lease_until > now, Job.article_id != article_id,
        ).limit(1)).first():
            raise SocialPending("another automatic social request is active")
        day = dt.date.today().isoformat()
        if not _budget_fits(session, hold, day):
            raise SocialPending("automatic social daily budget reached")
        call = Call(id=uuid.uuid4().hex, article_id=article_id, day=day,
                    reserved_tokens=hold, created_at=now)
        session.add(call)
        token = uuid.uuid4().hex
        job.attempts += 1
        job.status, job.lease_until, job.lease_token, job.updated_at = "running", now + LEASE_SECONDS, token, now
        return call.id, token


def _renew(engine, article_id: str, token: str) -> None:
    with _write(engine) as session:
        job = session.get(Job, article_id)
        if job is None or job.lease_token != token or job.lease_until <= time.time():
            raise SocialPending("social lease lost")
        job.lease_until = time.time() + LEASE_SECONDS


def _guard_publication(engine, article_id: str, token: str) -> None:
    """落缓存前的复核:占用还是自己的,开关 / 总闸还开着,条目仍属本机、来源仍可外送;否则这次结果不发布。"""
    with Session(engine) as session:
        job = session.get(Job, article_id)
        article = session.get(ArticleRecord, article_id)
        if job is None or article is None or job.lease_token != token or job.lease_until <= time.time():
            raise SocialPending("social lease lost")
        if not _gates_open(session, article):
            raise SocialPending("automatic social translation is not allowed")


def _settle(engine, call_id: str, receipts: list, *, sent: int, rejected: bool) -> None:
    """按实际发出的请求核销:sent = 发出的请求数,receipts = 收到的用量回执。"""
    with _write(engine) as session:
        call = session.get(Call, call_id)
        if call is None or call.recorded:
            return
        if receipts:
            known = len(receipts) >= sent
            for usage, model in receipts:
                ai_usage.record_usage(session, username=None, purpose=USAGE_PURPOSE, model=model,
                                      usage=usage, day=call.day, commit=False)
                known = known and bool(usage) and any(key in usage for key in ("total_tokens", "prompt_tokens"))
            # 有请求发出去却没有回执(另一批超时),或回执里没有 token 数:那部分费用不明,整笔预留留到当天结束。
            call.recorded, call.status = True, "settled" if known else "unknown"
        else:
            # 一个请求都没发(缓存命中、原文已变、结构不可译),或服务商明确拒绝:释放预留。
            call.status = "released" if (sent == 0 or rejected) else "unknown"


def _complete(engine, article_id: str, source_fp: str, token: str | None, *, error=None) -> None:
    from services.reader_ai import ReaderAIError
    with _write(engine) as session:
        job = session.get(Job, article_id)
        if job is None:
            return
        now = time.time()
        owns = bool(token) and job.lease_token == token
        if job.source_fp != source_fp:
            # 原文中途变了:钩子已按新指纹重排、占用没动。交还占用,新指纹的待办立刻可领。
            if owns:
                job.lease_until, job.lease_token = 0, ""
            return
        if not owns and job.lease_until > now:
            return  # 别人正占着这条(没占到的一方 / 占用过期后才回来的旧持有者):不动它的状态
        article = session.get(ArticleRecord, article_id)
        job.lease_until, job.lease_token, job.error = 0, "", ""
        if article is not None and _translated(article):
            job.status = "succeeded"
        elif article is None or not _sources(article) or not _local_article(session, article) \
                or not _source_allowed(session, article):
            # 被上游接管、来源改成不可外送、原文已无可译文字:不再属于本机的自动翻译
            job.status = "skipped"
        elif isinstance(error, SocialPending):
            job.status, job.retry_at = "pending", now + 60  # 闸门 / 预算 / 让路:稍后再看,不计尝试次数
        elif isinstance(error, ReaderAIError) and error.status_code in (400, 403):
            job.status = "skipped"  # 没有可交给模型的文字 / 来源不可外送:重试也不会变
        else:
            job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "pending"
            job.error = "推文翻译暂未完成"
            job.retry_at = now + min(3600, 30 * 2 ** job.attempts)
        job.updated_at = now


async def _dispatch(db_sink, article_id: str, source_fp: str, config, call_id: str, token: str) -> None:
    """发请求、续租、核销。原文对不上领取时的指纹就不调模型;失去占用即取消在途调用。"""
    from services.reader_ai import translate_social_post
    engine = db_sink.engine
    receipts: list = []
    state = {"sent": 0, "rejected": False}
    meta = UsageMeta(USAGE_PURPOSE, None, recorder=lambda _meta, usage, model: receipts.append((usage, model)))

    async def renew():
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            await asyncio.to_thread(_renew, engine, article_id, token)

    async def execute():
        await translate_social_post(
            db_sink, article_id, config, meta,
            expected_fingerprint=source_fp,
            on_llm_request=lambda: state.__setitem__("sent", state["sent"] + 1),
            max_retries=1,  # 客户端内部重试会让「首次超时、后次成功」漏掉前一次的不明费用
            pre_save_check=lambda: _guard_publication(engine, article_id, token),
        )

    work, keeper = asyncio.create_task(execute()), asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait((work, keeper), return_when=asyncio.FIRST_COMPLETED)
        if keeper in done:
            await keeper  # 续租失败:抛出,下面取消在途调用
        await work
    except LLMHTTPError as exc:
        state["rejected"] = 400 <= exc.status_code < 500 and exc.status_code != 408
        raise
    finally:
        for task in (work, keeper):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, keeper, return_exceptions=True)
        await asyncio.to_thread(_settle, engine, call_id, receipts, **state)


async def run_once(db_sink) -> bool:
    engine = db_sink.engine
    picked = await asyncio.to_thread(_pick, engine)
    if picked is None:
        return False
    article_id, source_fp, hold, config = picked
    error, token = None, None
    try:
        claim = await asyncio.to_thread(_reserve, engine, article_id, source_fp, hold)
        if claim is not None:
            call_id, token = claim
            await _dispatch(db_sink, article_id, source_fp, config, call_id, token)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 服务商故障不该让 worker 停下
        error = exc
        logger.info("Automatic social translation deferred article=%s reason=%s", article_id, type(exc).__name__)
    await asyncio.to_thread(_complete, engine, article_id, source_fp, token, error=error)
    return True


def _maintenance(engine) -> None:
    now = time.time()
    # 空闲 / 预算卡住时不为几条空删除去抢 SQLite 的写锁。
    with Session(engine) as session:
        old_call = session.exec(select(Call.id).where(
            (Call.created_at < now - 30 * 86400)
            | ((Call.status == "reserved") & (Call.created_at < now - 600)),
        ).limit(1)).first()
        exhausted_job = session.exec(select(Job.article_id).where(
            Job.status.in_(("pending", "running")), Job.attempts >= MAX_ATTEMPTS, Job.lease_until <= now,
        ).limit(1)).first()
        if not (old_call or exhausted_job):
            return
    with _write(engine) as session:
        now = time.time()
        session.exec(delete(Call).where(Call.created_at < now - 30 * 86400))
        for job in session.exec(select(Job).where(
            Job.status.in_(("pending", "running")), Job.attempts >= MAX_ATTEMPTS, Job.lease_until <= now,
        )).all():
            article = session.get(ArticleRecord, job.article_id)
            job.status = "succeeded" if article is not None and _translated(article) else "failed"
            job.error = "推文翻译暂未完成" if job.status == "failed" else ""
            job.lease_until, job.lease_token, job.updated_at = 0, "", now
        # 中途终止的请求费用不明:预留留在它发出的那一天。
        for call in session.exec(select(Call).where(Call.status == "reserved", Call.created_at < now - 600)).all():
            call.status = "unknown"


async def worker_loop(db_sink):
    while True:
        try:
            await asyncio.to_thread(_maintenance, db_sink.engine)
            worked = await run_once(db_sink)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic social translation worker failed")
            worked = False
        await asyncio.sleep(1 if worked else POLL_SECONDS)
