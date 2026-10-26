"""Automatic title outbox, shared dispatch fences and isolated cost accounting."""
import asyncio
import datetime as dt
import json
import time

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from config import LLMConfig
from models.db import (
    ArticleRecord, AppSettingRecord, SourceConfigRecord, AiUsageRecord,
    TitleTranslationJobRecord as Job, TitleTranslationLeaseRecord as Lease,
    TitleTranslationCallRecord as Call, TranslationJobRecord,
)
from services import title_translation as service
from services import reader_ai
from services.title_cache import cached_title, fingerprint
from storage.impl.db_storage import DatabaseStorage

REAL_WORKER_LOOP = service.worker_loop

CONFIG = LLMConfig(base_url="http://llm.test", api_key="test", model="main", aux_model="aux", thinking_mode="max")


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'title.db'}")
    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    yield value
    value.engine.dispose()


def article(aid="a", title="A useful new research result", **kwargs):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return ArticleRecord(id=aid, title=title, content_type="rss_article", source_id="public_news",
                         source_url="https://example.com/article", publish_date=now, fetched_date=now,
                         content="Original body", **kwargs)


def seed(store, *records):
    with Session(store.engine) as session:
        session.add_all(records)
        session.commit()


def fake_provider(monkeypatch, calls, *, usage=True, fails=False):
    async def translate(title, config, meta=None, http_client=None, **kwargs):
        calls.append((title, config, meta))
        if fails:
            raise RuntimeError("provider timeout")
        meta.recorder(meta, {"total_tokens": 83, "prompt_tokens": 65, "completion_tokens": 18} if usage else {}, config.model)
        return "一项有用的新研究成果"
    monkeypatch.setattr(reader_ai, "_translate_title", translate)


def test_outbox_same_transaction_and_title_changes_only(store):
    with Session(store.engine) as session:
        session.add(article())
        session.flush()
        assert session.get(Job, "a").status == "pending"
        session.rollback()
    with Session(store.engine) as session:
        assert session.get(Job, "a") is None and session.get(ArticleRecord, "a") is None
    seed(store, article())
    with Session(store.engine) as session:
        job = session.get(Job, "a")
        job.status, job.attempts = "failed", 3
        session.commit()
        record = session.get(ArticleRecord, "a")
        record.content = "A replaced body"
        session.commit()
        assert job.status == "failed" and job.attempts == 3
        record.title = "A newer original title"
        session.commit()
        assert job.status == "pending" and job.attempts == 0
        assert job.title_fp == fingerprint(record.title)


def test_initialize_never_backfills_and_keeps_watermark(store):
    with Session(store.engine) as session:
        session.exec(delete(AppSettingRecord).where(AppSettingRecord.key == service.WATERMARK_KEY))
        session.commit()
        session.add(article("old"))
        session.commit()
        assert session.get(Job, "old") is None
    service.initialize(store.engine)
    with Session(store.engine) as session:
        marker = session.get(AppSettingRecord, service.WATERMARK_KEY).value
        assert session.get(Job, "old") is None
    service.initialize(store.engine)
    with Session(store.engine) as session:
        assert session.get(AppSettingRecord, service.WATERMARK_KEY).value == marker
        row = session.get(ArticleRecord, "old")
        row.content = "Body-only refresh is not a backfill"
        session.commit()
        assert session.get(Job, "old") is None
        row.title = "An explicitly updated title"
        session.commit()
        assert session.get(Job, "old").status == "pending"


@pytest.mark.parametrize("title", ["中文新闻标题", "", "https://example.com/a", "tinyserve v0.9.0", "GPT-5.5", "OpenAI", "42", "x" * 1201])
def test_titles_without_translation_are_skipped(store, title):
    seed(store, article(title=title))
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "skipped"


def test_cache_reuse_and_strict_fingerprint(store):
    title = "A useful new research result"
    seed(store, article(extensions_json=json.dumps({"translation_zh_title": "已有译名", "translation_zh_title_fp": fingerprint(title)})))
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "a")
        assert cached_title(row) == "已有译名"
        assert session.get(Job, "a").status == "succeeded"
        row.title = "Changed title"
        session.commit()
        assert cached_title(row) is None
        assert "translation_zh_title" not in json.loads(row.extensions_json)
    assert cached_title(article(extensions_json='{"translation_zh_title":"无指纹译名"}')) is None


def test_automatic_call_config_and_system_budget_only(store, monkeypatch):
    calls=[];fake_provider(monkeypatch,calls)
    seed(store,article())
    assert asyncio.run(service.run_once(store.engine))
    assert len(calls)==1 and calls[0][1].model=="aux"
    assert calls[0][1].thinking_mode=="disabled" and calls[0][1].max_tokens==512
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord,"a"))=="一项有用的新研究成果"
        assert session.get(Job,"a").status=="succeeded"
        usage=session.exec(select(AiUsageRecord)).all()
        assert len(usage)==1 and usage[0].purpose==service.USAGE_PURPOSE and usage[0].username=="system"
        assert usage[0].total_tokens==83
        assert session.exec(select(Call)).one().status=="settled"
    assert not asyncio.run(service.run_once(store.engine))


def test_budget_global_gate_and_reader_priority(store, monkeypatch):
    calls=[];fake_provider(monkeypatch,calls)
    seed(store,article())
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key=service.DAILY_TOKENS_KEY,value="1"))
        session.commit()
    assert not asyncio.run(service.run_once(store.engine)) and not calls
    with Session(store.engine) as session:
        job=session.get(Job,"a");assert job.attempts==0;job.retry_at=0
        session.get(AppSettingRecord,service.DAILY_TOKENS_KEY).value="200000"
        session.add(AppSettingRecord(key=service.accounts.AI_BETA_GLOBAL_KEY,value="false"));session.commit()
    assert not asyncio.run(service.run_once(store.engine)) and not calls
    with Session(store.engine) as session:
        session.get(AppSettingRecord,service.accounts.AI_BETA_GLOBAL_KEY).value="true"
        session.add(TranslationJobRecord(id="body",input_key="key",article_id="a",created_by="reader",created_at=time.time(),updated_at=time.time()))
        session.commit()
    assert not asyncio.run(service.run_once(store.engine)) and not calls


def test_source_authority_and_credentials_are_fenced(store):
    with Session(store.engine) as session:
        session.add(SourceConfigRecord(source_id="user_rss_secret",name="private",source_type="rss",url="https://example.com/feed?token=secret",owner_username="alice",created_at=dt.datetime.now(dt.timezone.utc).isoformat(),updated_at=dt.datetime.now(dt.timezone.utc).isoformat()))
        session.commit()
        secret=article("private");secret.source_id="user_rss_secret"
        session.add_all([secret,article("replica",analysis_authority_id="outer")]);session.commit()
        assert session.get(Job,"private") is None and session.get(Job,"replica") is None
    seed(store,article())
    with Session(store.engine) as session:
        service.sync_consumer_policy.activate_v2_consumer_mode(session,reason="test")
    assert asyncio.run(service.run_once(store.engine))
    with Session(store.engine) as session:
        assert session.get(Job,"a").status=="skipped"


def test_failure_cap_unknown_cost_and_restart_recovery(store, monkeypatch):
    calls=[];fake_provider(monkeypatch,calls,fails=True);seed(store,article())
    for i in range(3):
        assert asyncio.run(service.run_once(store.engine))
        with Session(store.engine) as session:
            row=session.get(Job,"a");assert row.attempts==i+1;row.retry_at=0;session.commit()
    assert not asyncio.run(service.run_once(store.engine))
    with Session(store.engine) as session:
        assert session.get(Job,"a").status=="failed"
        assert [row.status for row in session.exec(select(Call)).all()]==["unknown"]*3
    assert len(calls)==3


def test_concurrent_callers_share_one_provider_and_merge_latest_extensions(store):
    seed(store,article())
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def call():
            calls.append(True);entered.set();await release.wait();return "共享译名"
        first=asyncio.create_task(service.translate_shared(store.engine,"a","A useful new research result",call))
        await entered.wait()
        second=asyncio.create_task(service.translate_shared(store.engine,"a","A useful new research result",call))
        with Session(store.engine) as session:
            row=session.get(ArticleRecord,"a");row.content="New body while title is unchanged"
            row.extensions_json=json.dumps({"translation_zh":"完整正文", "new_metadata":"keep"});session.commit()
        release.set()
        values=await asyncio.gather(first,second)
        assert values==["共享译名","共享译名"] and len(calls)==1
    asyncio.run(scenario())
    with Session(store.engine) as session:
        ext=json.loads(session.get(ArticleRecord,"a").extensions_json)
        assert ext["translation_zh"]=="完整正文" and ext["new_metadata"]=="keep"


def test_stale_title_and_policy_change_cannot_publish(store):
    seed(store,article())
    async def change_title():
        with Session(store.engine) as session:
            row=session.get(ArticleRecord,"a");row.title="Changed during provider request";session.commit()
        return "旧标题的译文"
    with pytest.raises(reader_ai.ReaderAIError):
        asyncio.run(service.translate_shared(store.engine,"a","A useful new research result",change_title))
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord,"a")) is None
        assert session.get(Lease,"a") is None
    async def close_gate():
        with Session(store.engine) as session:
            session.add(AppSettingRecord(key=service.accounts.AI_BETA_GLOBAL_KEY,value="false"));session.commit()
        return "不应写入"
    with pytest.raises(reader_ai.ReaderAIError):
        asyncio.run(service.translate_shared(store.engine,"a","Changed during provider request",close_gate))
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord,"a")) is None


def test_expired_lease_reclaim_and_old_owner_fenced(store):
    seed(store,article());title="A useful new research result"
    old,_=service._acquire(store.engine,"a",title,False,180)
    with Session(store.engine) as session:
        session.get(Lease,"a").until=time.time()-1;session.commit()
    new,_=service._acquire(store.engine,"a",title,False,180)
    assert old!=new
    with pytest.raises(service.TitlePending):
        service._publish(store.engine,"a",title,"陈旧调用",old,False)
    service._publish(store.engine,"a",title,"新调用",new,False)


def test_auto_reservations_are_serialized_across_distinct_titles(store):
    seed(store, article("a"), article("b"))
    title = "A useful new research result"
    first, _ = service._acquire(store.engine, "a", title, True, 180)
    second, _ = service._acquire(store.engine, "b", title, True, 180)
    call_id = service._reserve(store.engine, "a", title, fingerprint(title), 1000)
    with pytest.raises(service.TitlePending, match="another automatic"):
        service._reserve(store.engine, "b", title, fingerprint(title), 1000)
    service._settle(store.engine, call_id, [({"total_tokens": 100}, "aux")], False)
    assert service._reserve(store.engine, "b", title, fingerprint(title), 1000)
    service._release(store.engine, "a", first)
    service._release(store.engine, "b", second)


def test_final_crashed_attempt_becomes_terminal_after_lease_expiry(store):
    seed(store, article())
    title = "A useful new research result"
    service._acquire(store.engine, "a", title, True, 180)
    service._reserve(store.engine, "a", title, fingerprint(title), 1000)
    with Session(store.engine) as session:
        session.get(Job, "a").attempts = 3
        session.get(Lease, "a").until = time.time() - 1
        session.exec(select(Call)).one().created_at = time.time() - 601
        session.commit()
    service._maintenance(store.engine)
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "failed"
        assert session.get(Lease, "a") is None
        assert session.exec(select(Call)).one().status == "unknown"
    assert not asyncio.run(service.run_once(store.engine))


def test_provider_kept_proper_name_is_terminal(store, monkeypatch):
    seed(store, article(title="Deepseek"))
    async def unchanged(title, config, meta=None, http_client=None, **kwargs):
        meta.recorder(meta, {"total_tokens": 10}, config.model)
        return title
    monkeypatch.setattr(reader_ai, "_translate_title", unchanged)
    assert asyncio.run(service.run_once(store.engine))
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "skipped"
        assert session.get(Job, "a").attempts == 1
    assert not asyncio.run(service.run_once(store.engine))


def test_lifespan_starts_and_cancels_title_worker(store, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    import api.app as app_module

    started, stopped = threading.Event(), threading.Event()
    async def watched(engine):
        assert engine is store.engine
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(app_module, "db_sink", store)
    monkeypatch.setattr(service, "worker_loop", watched)
    with TestClient(app_module.app):
        assert started.wait(2)
    assert stopped.is_set()


def test_competing_worker_cannot_reset_active_final_attempt(store):
    seed(store, article())
    title = "A useful new research result"
    service._acquire(store.engine, "a", title, True, 180)
    with Session(store.engine) as session:
        job = session.get(Job, "a")
        job.attempts, job.status = 3, "running"
        session.commit()
    service._complete(store.engine, "a", fingerprint(title), error=service.TitlePending(), deferred=True)
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "running"


def test_frozen_title_cache_checks_snapshot_original(store):
    row = article(title="Current title", extensions_json=json.dumps({
        "translation_zh_title": "旧版标题的译文",
        "translation_zh_title_fp": fingerprint("Snapshot title"),
    }))
    assert cached_title(row) is None
    assert cached_title(row, title="Snapshot title") == "旧版标题的译文"
    row.extensions_json = '{"translation_zh_title": "无法证明归属的旧缓存"}'
    assert cached_title(row, title="Snapshot title") is None


@pytest.mark.parametrize("change", ["source", "authority"])
def test_inflight_policy_changes_fence_publication(store, change):
    seed(store, article())
    async def moved():
        with Session(store.engine) as session:
            if change == "authority":
                session.get(ArticleRecord, "a").analysis_authority_id = "new-authority"
            else:
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                session.add(SourceConfigRecord(source_id="public_news", name="now private",
                    source_type="rss", url="https://example.com/feed?token=secret",
                    created_at=now, updated_at=now))
            session.commit()
        return "已过期的结果"
    with pytest.raises((reader_ai.ReaderAIError, service.TitlePending)):
        asyncio.run(service.translate_shared(store.engine, "a", "A useful new research result", moved, automatic=True))
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord, "a")) is None
        assert session.get(Lease, "a") is None


def test_response_without_usage_keeps_conservative_daily_hold(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls, usage=False)
    seed(store, article())
    assert asyncio.run(service.run_once(store.engine))
    with Session(store.engine) as session:
        call = session.exec(select(Call)).one()
        assert call.status == "unknown" and call.recorded and call.reserved_tokens > 512
        usage = session.exec(select(AiUsageRecord)).one()
        assert usage.calls == 1 and usage.total_tokens == 0
        assert session.get(Job, "a").status == "succeeded"


@pytest.mark.parametrize("title", [
    "Новая модель для перевода", "새로운 번역 모델", "Νέο μοντέλο μετάφρασης",
    "نموذج جديد للترجمة", "модель5", "모델5출시", "かなタイトル",
])
def test_unicode_natural_language_titles_are_eligible(store, title):
    seed(store, article(title=title))
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "pending"


@pytest.mark.parametrize("title", ["🤖✨", "123-456", "GPT-5.5", "@사용자", "#новости"])
def test_nonprose_unicode_and_ascii_identifiers_remain_skipped(store, title):
    seed(store, article(title=title))
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "skipped"


def test_unchanged_receipt_deduplicates_concurrent_and_later_callers(store):
    from services.title_cache import title_receipt
    seed(store, article(title="Deepseek"))
    calls = []
    async def provider():
        calls.append(True)
        await asyncio.sleep(.01)
        return "Deepseek"
    async def scenario():
        first, second = await asyncio.gather(
            service.translate_shared(store.engine, "a", "Deepseek", provider),
            service.translate_shared(store.engine, "a", "Deepseek", provider),
        )
        third = await service.translate_shared(store.engine, "a", "Deepseek", provider)
        assert first == second == third == "Deepseek"
    asyncio.run(scenario())
    assert len(calls) == 1
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "a")
        assert title_receipt(row) == "Deepseek"
        assert cached_title(row) is None


@pytest.mark.parametrize("fp", [None, "stale", fingerprint("Deepseek")])
def test_receipt_requires_strict_fingerprint_with_dict_support(fp):
    from services.title_cache import title_receipt
    ext = {"translation_zh_title": "Deepseek"}
    if fp is not None:
        ext["translation_zh_title_fp"] = fp
    value = {"title": "Deepseek", "extensions": ext, "title_zh": "不要信任顶层投影"}
    assert title_receipt(value) == ("Deepseek" if fp == fingerprint("Deepseek") else None)
    assert cached_title(value) is None


def test_auto_retained_name_reused_by_digest_and_legacy_reader_without_cost(store, monkeypatch):
    from services import personal_digest_titles
    seed(store, article(title="Deepseek"))
    calls = []
    async def provider(title, config, meta=None, http_client=None, **kwargs):
        calls.append(title)
        meta.recorder(meta, {"total_tokens": 10}, config.model)
        return title
    monkeypatch.setattr(reader_ai, "_translate_title", provider)
    assert asyncio.run(service.run_once(store.engine))
    async def forbidden(*args, **kwargs):
        pytest.fail("a valid retained-name receipt must not call the provider again")
    monkeypatch.setattr(reader_ai, "_translate_title", forbidden)
    monkeypatch.setattr(personal_digest_titles, "_translate_title", forbidden)
    result = asyncio.run(personal_digest_titles._translate_batch(
        {"a": "Deepseek"}, CONFIG, username=None, budget_seconds=1, engine=store.engine,
    ))
    assert result == {}  # No duplicate original-as-Chinese subtitle.
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "a")
        ext = json.loads(row.extensions_json)
        ext.update(translation_zh="已有正文译文", translation_zh_fp=reader_ai._body_fingerprint(row.content))
        row.extensions_json = json.dumps(ext)
        session.commit()
    def forbidden_budget():
        pytest.fail("zero-cost full cache reuse must not reserve reader quota")
    result = asyncio.run(reader_ai.translate_article(store, "a", CONFIG, pre_llm_check=forbidden_budget))
    assert result == {"translation": "已有正文译文", "title": "Deepseek", "cached": True}
    with Session(store.engine) as session:
        usage = session.exec(select(AiUsageRecord)).one()
        assert usage.purpose == service.USAGE_PURPOSE and usage.calls == 1 and usage.total_tokens == 10
        assert session.get(Job, "a").status == "skipped"
    assert calls == ["Deepseek"]


def test_existing_retained_receipt_is_terminal_on_enqueue_and_recovery(store):
    seed(store, article(title="Deepseek", extensions_json=json.dumps({
        "translation_zh_title": "Deepseek", "translation_zh_title_fp": fingerprint("Deepseek"),
    })))
    with Session(store.engine) as session:
        job = session.get(Job, "a")
        assert job.status == "skipped"
        job.status, job.attempts = "running", 3
        session.commit()
    service._maintenance(store.engine)
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "skipped"


@pytest.mark.parametrize("replace_owner", [False, True])
def test_cancel_during_claim_thread_releases_only_its_own_token(store, monkeypatch, replace_owner):
    import threading
    seed(store, article())
    entered, release = threading.Event(), threading.Event()
    original = service._acquire
    captured = []
    def blocked_claim(*args):
        value = original(*args)
        captured.append(value)
        entered.set()
        assert release.wait(5)
        return value
    monkeypatch.setattr(service, "_acquire", blocked_claim)
    calls = []
    async def provider():
        calls.append(True)
        return "不应调用"
    async def scenario():
        task = asyncio.create_task(service.translate_shared(
            store.engine, "a", "A useful new research result", provider,
        ))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Outer shutdown paths may cancel a child more than once.
        with Session(store.engine) as session:
            lease = session.get(Lease, "a")
            assert lease.token == captured[0][0]
            if replace_owner:
                lease.token = "a-new-owner"
                session.commit()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    asyncio.run(scenario())
    assert not calls
    with Session(store.engine) as session:
        lease = session.get(Lease, "a")
        if replace_owner:
            assert lease.token == "a-new-owner"
        else:
            assert lease is None


def _rss_item(aid, title, content="", *, extra=None):
    from models.content import RssArticleContent
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    item = RssArticleContent(id=aid, title=title, source_url="https://example.test/" + aid,
        publish_date=now, fetched_date=now, source_id="public_news", content=content,
        has_content=bool(content))
    if extra is not None:
        item.extra_extensions = extra
    return item


@pytest.mark.parametrize("incoming", [None, "valid", "stale"])
def test_real_storage_backfill_preserves_title_receipt_without_repayment(store, monkeypatch, incoming):
    from services.title_cache import title_receipt
    title = "A useful new research result"
    assert asyncio.run(store.save(_rss_item("fill", title, extra={"old_metadata": "replace"})))
    calls = []
    fake_provider(monkeypatch, calls)
    assert asyncio.run(service.run_once(store.engine))
    ext = {"new_metadata": "keep"}
    if incoming:
        ext.update(translation_zh_title="输入提供的新译名", translation_zh_title_fp=fingerprint(title) if incoming == "valid" else "stale")
    assert asyncio.run(store.save(_rss_item("fill", title, "Full body arrives later.", extra=ext)))
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "fill")
        assert title_receipt(row) == ("输入提供的新译名" if incoming == "valid" else "一项有用的新研究成果")
        assert row.content == "Full body arrives later."
        assert json.loads(row.extensions_json)["new_metadata"] == "keep"
        assert "old_metadata" not in json.loads(row.extensions_json)
        assert session.get(Job, "fill").status == "succeeded"
        assert session.exec(select(AiUsageRecord)).one().calls == 1
    assert not asyncio.run(service.run_once(store.engine)) and len(calls) == 1


def test_real_storage_backfill_with_changed_title_drops_old_receipt(store, monkeypatch):
    assert asyncio.run(store.save(_rss_item("fill", "An old original")))
    fake_provider(monkeypatch, [])
    assert asyncio.run(service.run_once(store.engine))
    assert asyncio.run(store.save(_rss_item("fill", "A different original", "Full body")))
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "fill")
        assert cached_title(row) is None
        assert "translation_zh_title_fp" not in json.loads(row.extensions_json)
        assert session.get(Job, "fill").status == "pending"
        assert session.get(Job, "fill").attempts == 0


def test_storage_update_replacement_keeps_only_matching_title_receipt(store):
    title = "A useful new research result"
    seed(store, article(extensions_json=json.dumps({
        "translation_zh_title": "匹配译名", "translation_zh_title_fp": fingerprint(title), "old": True,
    })))
    assert asyncio.run(store.update("a", {"extensions_json": '{"fresh": true}'}))
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "a")
        assert cached_title(row) == "匹配译名" and json.loads(row.extensions_json)["fresh"]
        assert "old" not in json.loads(row.extensions_json)
    assert asyncio.run(store.update("a", {"title": "Different title", "extensions_json": '{}'}))
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord, "a")) is None


def test_v1_import_never_queues_titles_and_does_not_suppress_later_collection(store, monkeypatch):
    import api.app as app_module
    from api.routers import archive_sync
    from services.title_cache import title_receipt
    monkeypatch.setattr(app_module, "db_sink", store)
    lines = []
    for index, age in enumerate([400, 0]):
        title = f"Imported English headline number {index}"
        row = article(f"import-{index}", title=title, extensions_json=json.dumps({
            "translation_zh_title": "来自归档的译名", "translation_zh_title_fp": fingerprint(title),
        }))
        row.fetched_date = row.publish_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age)).isoformat()
        lines.append(json.dumps(archive_sync.archive_sync_line(row)))
    result = archive_sync.import_archive_sync_jsonl("\n".join(lines))
    assert result["imported_count"] == 2 and not result["error_count"]
    with Session(store.engine) as session:
        assert not session.exec(select(Job)).all()
        assert all(title_receipt(row) == "来自归档的译名" for row in session.exec(select(ArticleRecord)).all())
    assert asyncio.run(store.save(_rss_item("collected", "A newly collected title", "New body")))
    with Session(store.engine) as session:
        assert session.get(Job, "collected").status == "pending"


def test_v1_import_changed_title_invalidates_old_local_pending_job(store, monkeypatch):
    import api.app as app_module
    from api.routers import archive_sync
    monkeypatch.setattr(app_module, "db_sink", store)
    assert asyncio.run(store.save(_rss_item("import-fill", "Original local title")))
    incoming = article("import-fill", title="Replaced by imported original")
    result = archive_sync.import_archive_sync_jsonl(json.dumps(archive_sync.archive_sync_line(incoming)))
    assert result["updated_count"] == 1
    with Session(store.engine) as session:
        job = session.get(Job, "import-fill")
        assert job.status == "skipped" and job.title_fp == fingerprint(incoming.title)
    assert not asyncio.run(service.run_once(store.engine))


def test_exhausted_budget_worker_ticks_do_not_claim_or_write_jobs(store, monkeypatch):
    from sqlalchemy import event
    seed(store, *(article(f"queued-{i}", title=f"A fresh headline number {i}") for i in range(4)))
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key=service.DAILY_TOKENS_KEY, value="1"))
        before = [(row.article_id, row.status, row.attempts, row.retry_at, row.updated_at)
                  for row in session.exec(select(Job).order_by(Job.article_id)).all()]
        session.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("exhausted budget must be rejected before acquiring a title lease")
    monkeypatch.setattr(service, "_acquire", forbidden)
    writes = []
    def capture(_connection, _cursor, statement, *_args):
        sql = statement.upper().lstrip()
        if sql.startswith(("INSERT", "DELETE", "UPDATE")) and any(name in sql for name in ("TITLE_TRANSLATION_LEASES", "TITLE_TRANSLATION_JOBS")):
            writes.append(statement)
    event.listen(store.engine, "before_cursor_execute", capture)
    async def scenario():
        seen = 0
        enough = asyncio.Event()
        run_once = service.run_once
        async def counted(engine):
            nonlocal seen
            assert not await run_once(engine)
            seen += 1
            if seen == 3:
                enough.set()
            return False
        monkeypatch.setattr(service, "run_once", counted)
        monkeypatch.setattr(service, "POLL_SECONDS", .01)
        worker = asyncio.create_task(REAL_WORKER_LOOP(store.engine))
        try:
            await asyncio.wait_for(enough.wait(), 2)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())
    event.remove(store.engine, "before_cursor_execute", capture)
    assert writes == []
    with Session(store.engine) as session:
        after = [(row.article_id, row.status, row.attempts, row.retry_at, row.updated_at)
                 for row in session.exec(select(Job).order_by(Job.article_id)).all()]
        assert after == before and not session.exec(select(Lease)).all()


def test_budget_raise_rollover_and_atomic_recheck(store):
    title = "A useful new research result"
    seed(store, article())
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key=service.DAILY_TOKENS_KEY, value="1"))
        session.commit()
    assert service._pick(store.engine) is None
    with Session(store.engine) as session:
        session.get(AppSettingRecord, service.DAILY_TOKENS_KEY).value = "200000"
        session.commit()
    assert service._pick(store.engine) is not None
    with Session(store.engine) as session:
        session.add(Call(id="held", article_id="previous", day=dt.date.today().isoformat(), reserved_tokens=200000, status="unknown", created_at=time.time()))
        session.commit()
    assert service._pick(store.engine) is None
    with Session(store.engine) as session:
        session.get(Call, "held").day = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        session.commit()
    assert service._pick(store.engine) is not None
    with Session(store.engine) as session:
        session.get(AppSettingRecord, service.DAILY_TOKENS_KEY).value = "1"
        session.commit()
    with pytest.raises(service.TitlePending, match="budget"):
        service._reserve(store.engine, "a", title, fingerprint(title), service._required_hold(title))
    with Session(store.engine) as session:
        assert session.get(Job, "a").attempts == 0
        assert len(session.exec(select(Call)).all()) == 1


def test_cached_receipt_completes_even_when_automatic_budget_is_zero(store):
    title = "A useful new research result"
    seed(store, article(extensions_json=json.dumps({"translation_zh_title":"现成译名","translation_zh_title_fp":fingerprint(title)})))
    with Session(store.engine) as session:
        session.get(Job, "a").status = "pending"
        session.add(AppSettingRecord(key=service.DAILY_TOKENS_KEY, value="0"))
        session.commit()
    assert asyncio.run(service.run_once(store.engine))
    with Session(store.engine) as session:
        assert session.get(Job, "a").status == "succeeded"
        assert not session.exec(select(Call)).all()


@pytest.mark.parametrize("automatic, require_ai_gate", [(False, True), (True, False)])
def test_reader_and_automatic_generation_keep_global_gate(store, automatic, require_ai_gate):
    seed(store, article())
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key=service.accounts.AI_BETA_GLOBAL_KEY, value="false"))
        session.commit()
    async def forbidden():
        pytest.fail("reader/automatic title provider must remain globally gated")
    with pytest.raises(reader_ai.ReaderAIError, match="关闭"):
        asyncio.run(service.translate_shared(store.engine, "a", "A useful new research result",
            forbidden, automatic=automatic, require_ai_gate=require_ai_gate))
    with Session(store.engine) as session:
        assert session.get(Lease, "a") is None
    assert service._pick(store.engine) is None


def test_digest_policy_is_preserved_on_claim_renew_and_publish(store):
    seed(store, article())
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key=service.accounts.AI_BETA_GLOBAL_KEY, value="false"))
        session.commit()
    title = "A useful new research result"
    token, _ = service._acquire(store.engine, "a", title, False, 180, False)
    service._renew(store.engine, "a", title, token, False, 180, False)
    service._publish(store.engine, "a", title, "早报系统译名", token, False, False)
    with Session(store.engine) as session:
        assert cached_title(session.get(ArticleRecord, "a")) == "早报系统译名"


def test_v1_body_backfill_preserves_existing_matching_title_receipt(store, monkeypatch):
    import api.app as app_module
    from api.routers import archive_sync
    monkeypatch.setattr(app_module, "db_sink", store)
    title = "A useful new research result"
    assert asyncio.run(store.save(_rss_item("import-fill-cache", title)))
    fake_provider(monkeypatch, [])
    assert asyncio.run(service.run_once(store.engine))
    incoming = article("import-fill-cache", title=title, extensions_json='{"imported_metadata":true}')
    result = archive_sync.import_archive_sync_jsonl(json.dumps(archive_sync.archive_sync_line(incoming)))
    assert result["updated_count"] == 1
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "import-fill-cache")
        assert cached_title(row) == "一项有用的新研究成果"
        assert json.loads(row.extensions_json)["imported_metadata"] is True
        assert session.get(Job, "import-fill-cache").status == "succeeded"
    assert not asyncio.run(service.run_once(store.engine))
