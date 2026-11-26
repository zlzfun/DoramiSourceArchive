"""推文入库即译(issue #349):同事务入队、各入库方式、预算与门控隔离、列表带回译文。"""
import asyncio
import datetime as dt
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from config import LLMConfig
from models.db import (
    AiUsageRecord, AppSettingRecord, ArchiveSyncEntityStateRecord, ArticleRecord, SourceConfigRecord,
    TranslationJobRecord,
    SocialTranslationCallRecord as Call, SocialTranslationJobRecord as Job,
)
from services import archive_sync_v2, reader_ai
from services import social_translation as service
from services.title_translation import SUPPRESS_ENQUEUE_KEY
from storage.impl.db_storage import DatabaseStorage

from tests.test_ingest_push import _issue_token, _login, _push, _setup
from tests.test_ingest_social_fidelity import _fetched, _run_pipeline, _structured, _x_payload

CONFIG = LLMConfig(base_url="http://llm.test", api_key="test", model="main", aux_model="aux", thinking_mode="max")
TEXT = "We just shipped a new model for long-context coding."
QUOTED = {"author_handle": "other", "text": "the original post, in full https://t.co/m", "lang": "en"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'social.db'}")
    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    yield value
    value.engine.dispose()


def post(pid="p", text=TEXT, ext=None, **kwargs):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return ArticleRecord(
        id=pid, title=text[:40] or pid, content_type="social_post", source_id="x_lab",
        source_url=f"https://x.com/lab/status/{pid}", publish_date=now, fetched_date=now,
        has_content=bool(text), content=text, content_format="txt",
        extensions_json=json.dumps({"platform": "x", "lang": "en", **(ext or {})}, ensure_ascii=False),
        **kwargs,
    )


def seed(store, *records):
    with Session(store.engine) as session:
        session.add_all(records)
        session.commit()


def job_of(store, pid="p"):
    with Session(store.engine) as session:
        return session.get(Job, pid)


def zh_of(store, pid="p"):
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, pid)
        return reader_ai.cached_social_translation(row.content, json.loads(row.extensions_json))


def set_ext(store, pid, **changes):
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, pid)
        ext = json.loads(row.extensions_json)
        ext.update(changes)
        row.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.commit()


def fake_provider(monkeypatch, calls, *, usage=True, fails=False, on_call=None, fails_when=None):
    """JSON 进、JSON 出的桩:逐单元回「译：原文」,并像真客户端一样把用量交给本次调用自带的回调。
    fails_when(texts) 为真的那一次请求在别的请求回来之后才超时(没有回执)。"""
    async def chat(*, messages, config, usage_meta=None, **kwargs):
        units = json.loads(messages[-1].content)["units"]
        calls.append({"texts": [u["text"] for u in units], "config": config, "meta": usage_meta, "kwargs": kwargs})
        if on_call is not None:
            on_call(len(calls))
        if fails_when is not None and fails_when([u["text"] for u in units]):
            await asyncio.sleep(0.05)
            raise RuntimeError("provider timeout")
        if fails:
            raise RuntimeError("provider timeout")
        usage_meta.recorder(
            usage_meta, {"total_tokens": 120, "prompt_tokens": 90, "completion_tokens": 30} if usage else {},
            config.model,
        )
        return json.dumps({"units": [{"id": u["id"], "text": f"译：{u['text']}"} for u in units]}, ensure_ascii=False)
    monkeypatch.setattr(reader_ai, "chat_completion", chat)


def run(store):
    return asyncio.run(service.run_once(store))


# ── 入队:同事务、只认原文变化、不回填 ─────────────────────────

def test_outbox_rides_the_posts_transaction_and_follows_source_changes_only(store):
    with Session(store.engine) as session:
        session.add(post())
        session.flush()
        assert session.get(Job, "p").status == "pending"
        session.rollback()
    with Session(store.engine) as session:
        assert session.get(Job, "p") is None and session.get(ArticleRecord, "p") is None
    seed(store, post())
    first_fp = job_of(store).source_fp
    with Session(store.engine) as session:
        job = session.get(Job, "p")
        job.status, job.attempts = "failed", 3
        session.commit()
    # 配图、标题回执、阅读数这类与原文无关的写入不重排
    set_ext(store, "p", media_urls=["https://pbs.twimg.com/media/A.jpg"], translation_zh_title="标题")
    with Session(store.engine) as session:
        session.get(ArticleRecord, "p").read_count = 7
        session.commit()
    assert (job_of(store).status, job_of(store).attempts, job_of(store).source_fp) == ("failed", 3, first_fp)
    # 原文变了(补上引用帖)才换指纹重排
    set_ext(store, "p", quoted=QUOTED)
    job = job_of(store)
    assert (job.status, job.attempts) == ("pending", 0) and job.source_fp != first_fp


def test_non_social_records_never_get_a_job(store):
    row = post("article")
    row.content_type = "rss_article"
    seed(store, row)
    assert job_of(store, "article") is None


def test_initialize_never_backfills_stock_posts(store):
    with Session(store.engine) as session:
        session.exec(delete(AppSettingRecord).where(AppSettingRecord.key == service.WATERMARK_KEY))
        session.commit()
    seed(store, post("old"))
    assert job_of(store, "old") is None
    service.initialize(store.engine)
    with Session(store.engine) as session:
        marker = session.get(AppSettingRecord, service.WATERMARK_KEY).value
    service.initialize(store.engine)
    with Session(store.engine) as session:
        assert session.get(AppSettingRecord, service.WATERMARK_KEY).value == marker
    assert job_of(store, "old") is None
    # 存量推文上的无关写入(缓存、配图)不是回填信号
    set_ext(store, "old", media_urls=["https://pbs.twimg.com/media/A.jpg"], summary_zh="摘要")
    assert job_of(store, "old") is None
    # 原文确实变了才入队
    set_ext(store, "old", quoted=QUOTED)
    assert job_of(store, "old").status == "pending"


@pytest.mark.parametrize("text,ext", [
    ("这条推文本来就是中文，不需要翻译。", {"lang": "zh"}),
    ("https://t.co/abc123", {"lang": "zxx"}),
    ("🚀🚀🚀", {"lang": "art"}),
    ("", {}),
    ("word " * 1300, {"lang": "en"}),  # 超长 note tweet 留给读者手动翻译
])
def test_posts_with_nothing_to_translate_are_skipped(store, text, ext):
    seed(store, post(text=text, ext=ext))
    assert job_of(store).status == "skipped"


def test_a_valid_cache_counts_as_done_and_a_stale_one_does_not(store):
    sources = reader_ai.social_translation_sources(TEXT, {"lang": "en"})
    entry = {"text": "已有译文", "quoted": None, "fp": reader_ai._social_fingerprint(sources),
             "v": reader_ai.SOCIAL_TRANSLATION_VERSION}
    seed(store, post("fresh", ext={"translation_zh_social": entry}),
         post("stale", ext={"translation_zh_social": {**entry, "fp": "another-original"}}))
    assert job_of(store, "fresh").status == "succeeded"
    assert job_of(store, "stale").status == "pending"


# ── 派发:模型档位、系统预算、门控 ───────────────────────────

def test_automatic_call_uses_aux_without_thinking_and_only_the_system_budget(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post(ext={"quoted": QUOTED}))
    assert run(store)
    assert len(calls) == 1, "主推与引用推一次调用"
    assert calls[0]["texts"][0] == TEXT and calls[0]["texts"][1].startswith("the original post")
    assert calls[0]["config"].model == "aux" and calls[0]["config"].thinking_mode == "disabled"
    assert calls[0]["kwargs"].get("max_retries") == 1, "自动路径不让客户端内部重试(否则前一次的不明费用会漏记)"
    translated = zh_of(store)
    assert translated["text"] == f"译：{TEXT}" and translated["quoted"].startswith("译：the original post")
    assert job_of(store).status == "succeeded"
    with Session(store.engine) as session:
        usage = session.exec(select(AiUsageRecord)).all()
        assert [(row.purpose, row.username, row.total_tokens) for row in usage] == [
            (service.USAGE_PURPOSE, "system", 120),
        ]
        assert session.exec(select(Call)).one().status == "settled"
    assert not run(store)


def test_budget_switch_global_gate_and_reader_priority_hold_the_queue(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post())

    def setting(key, value):
        with Session(store.engine) as session:
            row = session.get(AppSettingRecord, key)
            if row is None:
                session.add(AppSettingRecord(key=key, value=value))
            else:
                row.value = value
            session.commit()

    setting(service.DAILY_TOKENS_KEY, "1")
    assert not run(store) and not calls
    setting(service.DAILY_TOKENS_KEY, "300000")
    setting(service.ENABLED_KEY, "false")
    assert not run(store) and not calls
    setting(service.ENABLED_KEY, "true")
    setting(service.accounts.AI_BETA_GLOBAL_KEY, "false")
    assert not run(store) and not calls
    setting(service.accounts.AI_BETA_GLOBAL_KEY, "true")
    with Session(store.engine) as session:
        session.add(TranslationJobRecord(id="body", input_key="key", article_id="p", created_by="reader",
                                         created_at=time.time(), updated_at=time.time()))
        session.commit()
    assert not run(store) and not calls
    assert (job_of(store).status, job_of(store).attempts) == ("pending", 0)
    with Session(store.engine) as session:
        session.exec(delete(TranslationJobRecord))
        session.commit()
    assert run(store) and len(calls) == 1


def test_budget_counts_only_its_own_purpose(store, monkeypatch):
    """标题自动翻译与读者翻译的用量不占推文预译的预算。"""
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post())
    today = dt.date.today().isoformat()
    with Session(store.engine) as session:
        for purpose in ("auto_title_translate", "translate"):
            service.ai_usage.record_usage(session, username="reader", purpose=purpose, model="aux",
                                          usage={"total_tokens": 10_000_000}, day=today)
    assert run(store) and len(calls) == 1


def test_synced_credentialed_and_imported_posts_are_fenced(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with Session(store.engine) as session:
        session.add(SourceConfigRecord(
            source_id="user_rss_secret", name="private", source_type="rss",
            url="https://example.com/feed?token=secret", owner_username="alice", created_at=now, updated_at=now,
        ))
        session.commit()
        secret = post("private")
        secret.source_id = "user_rss_secret"
        session.add_all([secret, post("replica", analysis_authority_id="outer")])
        session.commit()
        assert session.get(Job, "private") is None and session.get(Job, "replica") is None
    # 归档导入的事务:新条目不建待办
    with Session(store.engine) as session:
        session.info[SUPPRESS_ENQUEUE_KEY] = True
        session.add(post("imported"))
        session.commit()
    assert job_of(store, "imported") is None
    # 入队之后、派发之前被上游接管:派发时作罢,不调用模型
    seed(store, post())
    with Session(store.engine) as session:
        session.get(ArticleRecord, "p").analysis_authority_id = "outer"
        session.commit()
    assert run(store) and not calls
    assert job_of(store).status == "skipped"
    assert not run(store)


def test_translation_made_upstream_reaches_the_downstream_node_through_archive_sync(tmp_path, monkeypatch):
    """节点间同步:译文只在收进这条推文的节点上产出,随 articles 流到下游;下游不替上游的条目翻译。
    第一轮同步时还没译好的,译文写入让这条推文的修订前进,下一轮带到。"""
    from api.articles_view import serialize_article_list_item

    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    upstream = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'upstream.db'}")
    downstream = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'downstream.db'}")
    calls = []
    fake_provider(monkeypatch, calls)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with Session(upstream.engine) as session:
        session.add(SourceConfigRecord(
            source_id="x_lab", name="Lab", source_type="rss", url="https://x.com/lab", fetcher_id="generic_rss",
            created_at=now, updated_at=now,
        ))
        session.add(post(ext={"quoted": QUOTED}))
        session.commit()

    def sync(*streams):
        return {
            stream: archive_sync_v2.import_page(
                downstream.engine, archive_sync_v2.export_page(upstream.engine, stream), expected_stream=stream,
            ) for stream in streams
        }

    def revision():
        with Session(upstream.engine) as session:
            return session.exec(select(ArchiveSyncEntityStateRecord.revision).where(
                ArchiveSyncEntityStateRecord.stream == "articles", ArchiveSyncEntityStateRecord.identity == "p",
            )).one()

    try:
        # 第一轮:上游还没译。下游拿到原文,不建待办,它的 worker 也不替上游翻译。
        assert sync("sources", "articles")["articles"]["inserted"] == 1
        assert zh_of(downstream) is None and job_of(downstream) is None
        assert not run(downstream) and not calls

        # 这期间下游的读者自己点了「译为中文」:缓存只留在下游,等上游的版本到了由上游的覆盖,不会并存两份。
        with Session(downstream.engine) as session:
            row = session.get(ArticleRecord, "p")
            ext = json.loads(row.extensions_json)
            ext[reader_ai.SOCIAL_TRANSLATION_KEY] = {
                "text": "下游读者的译文", "quoted": "下游读者的引用译文", "v": reader_ai.SOCIAL_TRANSLATION_VERSION,
                "fp": reader_ai._social_fingerprint(reader_ai.social_translation_sources(row.content, ext)),
            }
            row.extensions_json = json.dumps(ext, ensure_ascii=False)
            session.commit()
        assert zh_of(downstream)["text"] == "下游读者的译文" and job_of(downstream) is None

        # 上游译好:只付一次,这条推文的修订前进。
        before = revision()
        assert run(upstream) and len(calls) == 1
        assert revision() > before
        expected = {"text": f"译：{TEXT}", "quoted": f"译：{QUOTED['text']}"}
        assert zh_of(upstream) == expected

        # 第二轮:译文随同一条流到下游,列表载荷直接带上;下游仍然没有待办、没有调用。
        assert sync("articles")["articles"]["updated"] == 1
        assert zh_of(downstream) == expected
        with Session(downstream.engine) as session:
            assert serialize_article_list_item(session.get(ArticleRecord, "p"))["social_zh"] == expected
        assert job_of(downstream) is None
        assert not run(downstream) and len(calls) == 1
    finally:
        upstream.engine.dispose()
        downstream.engine.dispose()


def test_failure_cap_holds_unknown_cost_and_stops_after_three_attempts(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls, fails=True)
    seed(store, post())
    for attempt in range(3):
        assert run(store)
        with Session(store.engine) as session:
            row = session.get(Job, "p")
            assert row.attempts == attempt + 1 and row.lease_until == 0 and row.lease_token == ""
            row.retry_at = 0
            session.commit()
    assert not run(store)
    assert job_of(store).status == "failed" and len(calls) == 3
    with Session(store.engine) as session:
        assert [row.status for row in session.exec(select(Call)).all()] == ["unknown"] * 3
    assert zh_of(store) is None


def test_source_changed_during_the_request_is_requeued_not_published(store, monkeypatch):
    calls = []

    def change_source(count):
        if count == 1:
            set_ext(store, "p", quoted=QUOTED)

    fake_provider(monkeypatch, calls, on_call=change_source)
    seed(store, post())
    old_fp = job_of(store).source_fp
    assert run(store)
    job = job_of(store)
    assert (job.status, job.attempts) == ("pending", 0) and job.source_fp != old_fp
    assert zh_of(store) is None, "旧原文的译文不落缓存"
    assert run(store)
    assert job_of(store).status == "succeeded"
    assert zh_of(store)["quoted"].startswith("译：the original post")


def test_reader_translated_first_costs_the_system_nothing(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post())
    reader_meta = reader_ai.UsageMeta(purpose="translate", username="reader", recorder=lambda *_: None)
    asyncio.run(reader_ai.translate_social_post(store, "p", CONFIG, reader_meta))
    assert len(calls) == 1 and job_of(store).status == "pending"
    assert run(store)
    assert len(calls) == 1 and job_of(store).status == "succeeded"
    with Session(store.engine) as session:
        assert session.exec(select(Call)).all() == []


def test_one_automatic_request_at_a_time_and_expired_lease_is_reclaimed(store):
    seed(store, post("a"), post("b"))
    fp = job_of(store, "a").source_fp
    call_id, token = service._reserve(store.engine, "a", fp, 1000)
    assert job_of(store, "a").status == "running" and job_of(store, "a").lease_token == token
    assert service._pick(store.engine) is None, "已有请求在途:这一拍不领,也不去推迟别的待办"
    with pytest.raises(service.SocialPending, match="another automatic"):
        service._reserve(store.engine, "b", fp, 1000)
    with pytest.raises(service.SocialPending, match="already dispatched"):
        service._reserve(store.engine, "a", fp, 1000)
    # 没占到的一方收尾时不动占用者的状态
    service._complete(store.engine, "a", fp, None, error=service.SocialPending("lost the race"))
    assert job_of(store, "a").status == "running"
    # 续租认 token
    with pytest.raises(service.SocialPending, match="lease lost"):
        service._renew(store.engine, "a", "someone-else")
    before = job_of(store, "a").lease_until
    time.sleep(0.01)
    service._renew(store.engine, "a", token)
    assert job_of(store, "a").lease_until > before
    # 进程中途退出:占用到期后同一条可以重新领取,旧预留转为费用不明
    with Session(store.engine) as session:
        session.get(Job, "a").lease_until = time.time() - 1
        session.get(Call, call_id).created_at = time.time() - 601
        session.commit()
    service._maintenance(store.engine)
    with Session(store.engine) as session:
        assert session.get(Call, call_id).status == "unknown"
    _, new_token = service._reserve(store.engine, "a", fp, 1000)
    assert new_token != token and job_of(store, "a").attempts == 2
    # 占用过期后才回来的旧持有者:续租失败、不能发布、收尾不动新持有者的状态
    with pytest.raises(service.SocialPending):
        service._renew(store.engine, "a", token)
    with pytest.raises(service.SocialPending):
        service._guard_publication(store.engine, "a", token)
    service._complete(store.engine, "a", fp, token, error=RuntimeError("late"))
    job = job_of(store, "a")
    assert (job.status, job.lease_token, job.attempts) == ("running", new_token, 2)


def test_a_claim_only_pays_for_the_original_it_claimed(store, monkeypatch):
    """领取之后、读取之前原文变了:这次领取不为新原文调模型(否则另一个进程领新待办时会为同一份原文再付一次)。"""
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post(), post("other"))
    old_fp = job_of(store).source_fp
    call_id, token = service._reserve(store.engine, "p", old_fp, 1000)
    set_ext(store, "p", quoted=QUOTED)  # 钩子按新指纹重排
    job = job_of(store)
    assert (job.status, job.attempts) == ("pending", 0) and job.source_fp != old_fp
    assert job.lease_token == token and job.lease_until > time.time(), "重排不清在途请求的占用"
    assert service._pick(store.engine) is None
    with pytest.raises(service.SocialPending, match="another automatic"):
        service._reserve(store.engine, "other", job_of(store, "other").source_fp, 1000)
    with pytest.raises(reader_ai.ReaderAIError):
        asyncio.run(service._dispatch(store, "p", old_fp, CONFIG, call_id, token))
    assert calls == [], "没有为新原文发请求"
    with Session(store.engine) as session:
        assert session.get(Call, call_id).status == "released"
    service._complete(store.engine, "p", old_fp, token, error=reader_ai.ReaderAIError("原文已更新", 409))
    job = job_of(store)
    assert (job.status, job.lease_until, job.lease_token) == ("pending", 0, ""), "占用交还,新指纹的待办立刻可领"
    # 两条待办(另一条排在前面)各派发一次:新原文只付了一次
    assert run(store) and run(store) and not run(store)
    assert sorted(len(call["texts"]) for call in calls) == [1, 2]
    assert zh_of(store)["quoted"].startswith("译：the original post")


def test_partial_receipts_keep_the_hold_for_the_request_without_one(store, monkeypatch):
    """两批请求,一批有回执、另一批发出去后超时:到的回执照记,整笔预留留到当天结束。"""
    calls = []
    long_quote = {**QUOTED, "text": "quoted " * 400}
    fake_provider(monkeypatch, calls, fails_when=lambda texts: any(text.startswith("quoted") for text in texts))
    seed(store, post(text="main " * 400, ext={"quoted": long_quote}))
    assert run(store)
    assert len(calls) == 2, "超过一批的长推文"
    with Session(store.engine) as session:
        call = session.exec(select(Call)).one()
        assert (call.status, call.recorded) == ("unknown", True)
        assert [row.total_tokens for row in session.exec(select(AiUsageRecord)).all()] == [120]
    assert job_of(store).status == "pending" and zh_of(store) is None


def test_result_is_not_published_once_the_gate_closes_mid_flight(store, monkeypatch):
    calls = []

    def close_gate(_count):
        with Session(store.engine) as session:
            session.add(AppSettingRecord(key=service.ENABLED_KEY, value="false"))
            session.commit()

    fake_provider(monkeypatch, calls, on_call=close_gate)
    seed(store, post())
    assert run(store) and len(calls) == 1
    assert zh_of(store) is None, "开关在途中被关掉:结果不落缓存"
    job = job_of(store)
    assert (job.status, job.lease_until, job.attempts) == ("pending", 0, 1)
    with Session(store.engine) as session:
        assert session.exec(select(Call)).one().status == "settled", "已发生的用量照记"


def test_released_hold_when_nothing_was_sent(store):
    seed(store, post())
    fp = job_of(store).source_fp
    call_id, _token = service._reserve(store.engine, "p", fp, 1000)
    service._settle(store.engine, call_id, [], sent=0, rejected=False)
    with Session(store.engine) as session:
        assert session.get(Call, call_id).status == "released"


@pytest.mark.parametrize("sent,rejected,expected", [
    (1, True, "released"),   # 唯一的请求被服务商明确拒绝
    (1, False, "unknown"),   # 发出去了、没有回执
    (2, True, "unknown"),    # 一个被拒、另一个没有回执:后者的费用不明(检视复检)
])
def test_hold_without_any_receipt_follows_what_was_actually_sent(store, sent, rejected, expected):
    seed(store, post())
    call_id, _token = service._reserve(store.engine, "p", job_of(store).source_fp, 1000)
    service._settle(store.engine, call_id, [], sent=sent, rejected=rejected)
    with Session(store.engine) as session:
        assert session.get(Call, call_id).status == expected


def test_a_done_job_is_requeued_when_its_translation_disappears(store, monkeypatch):
    calls = []
    fake_provider(monkeypatch, calls)
    seed(store, post())
    assert run(store) and job_of(store).status == "succeeded"
    with Session(store.engine) as session:
        row = session.get(ArticleRecord, "p")
        ext = json.loads(row.extensions_json)
        ext.pop("translation_zh_social")
        row.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.commit()
    assert (job_of(store).status, job_of(store).attempts) == ("pending", 0)
    assert run(store) and zh_of(store) is not None and len(calls) == 2


@pytest.mark.parametrize("entry", [
    {"text": [], "quoted": "引用译文"},
    {"text": "正文译文", "quoted": 3},
    {"text": "", "quoted": "  "},
    {"text": "正文译文", "quoted": "引用译文", "v": "1"},
])
def test_malformed_cache_groups_are_rejected_by_reader_and_sync_alike(entry):
    ext = {"lang": "en", "quoted": QUOTED}
    sources = reader_ai.social_translation_sources(TEXT, ext)
    fp = reader_ai._social_fingerprint(sources)
    group = {"fp": fp, "v": reader_ai.SOCIAL_TRANSLATION_VERSION, **entry}
    assert reader_ai.social_translation_entry_valid(group, fp) is False
    assert reader_ai.cached_social_translation(TEXT, {**ext, "translation_zh_social": group}) is None


# ── 列表带回译文 ─────────────────────────────────────────────

def test_list_item_carries_only_a_translation_that_matches_the_current_original(store, monkeypatch):
    from api.articles_view import serialize_article_list_item

    calls = []
    fake_provider(monkeypatch, calls)
    # 主推本来就是中文,只有引用推要译
    seed(store, post(), post("half", text="这条主推是中文。", ext={"lang": "zh", "quoted": QUOTED}))
    assert run(store) and run(store)

    def item(pid, **kwargs):
        with Session(store.engine) as session:
            return serialize_article_list_item(session.get(ArticleRecord, pid), **kwargs)

    assert item("p")["social_zh"] == {"text": f"译：{TEXT}", "quoted": None}
    half = item("half")["social_zh"]
    assert half["text"] is None and half["quoted"].startswith("译：the original post")
    assert "social_zh" not in item("p", include_content=False), "不带正文的列表不带译文"
    # 原文变了,旧译文不再给
    set_ext(store, "p", quoted=QUOTED)
    assert item("p")["social_zh"] is None
    row = post("article")
    row.content_type = "rss_article"
    seed(store, row)
    assert "social_zh" not in item("article")


# ── 各入库方式:推送、补全、抓取 ──────────────────────────────

def _fp_of(sink, article_id):
    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, article_id)
        sources = reader_ai.social_translation_sources((row.content or "").strip(), json.loads(row.extensions_json))
        return sources, reader_ai._social_fingerprint(sources)


def test_pushed_posts_are_queued_and_a_later_completion_requeues_them(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    calls = []
    fake_provider(monkeypatch, calls)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        # 第一次推送缺引用帖
        first = _push(client, token, [_structured("quoted", quoted=None)]).json()["results"]
        assert first[0]["status"] == "created", first
        sources, fp = _fp_of(sink, "x_new_lab_2001")
        assert set(sources) == {"text"}
        job = job_of(sink, "x_new_lab_2001")
        assert (job.status, job.source_fp) == ("pending", fp)
        assert run(sink) and job_of(sink, "x_new_lab_2001").status == "succeeded"
        assert zh_of(sink, "x_new_lab_2001")["quoted"] is None
        # 同一条再推一次、这次带上引用帖:原文多了一段,重新排队;旧译文对不上新原文,不再给
        again = _push(client, token, [_structured("quoted")]).json()["results"]
        assert again[0]["status"] != "created", again
    sources, fp = _fp_of(sink, "x_new_lab_2001")
    assert set(sources) == {"text", "quoted"}
    job = job_of(sink, "x_new_lab_2001")
    assert (job.status, job.attempts, job.source_fp) == ("pending", 0, fp)
    assert zh_of(sink, "x_new_lab_2001") is None
    assert run(sink)
    assert zh_of(sink, "x_new_lab_2001")["quoted"].startswith("译：the original post")
    assert len(calls) == 2


def test_fetched_posts_are_queued_and_adopting_a_pushed_post_follows_the_fetched_text(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    calls = []
    fake_provider(monkeypatch, calls)
    # 抓取器直接抓到的新推文
    fresh = _x_payload("quoted", post_id="3001")
    _run_pipeline(sink, "x_new_lab", [_fetched(sink, fresh, "x_new_lab")])
    sources, fp = _fp_of(sink, "x_new_lab_3001")
    assert set(sources) == {"text", "quoted"}
    assert (job_of(sink, "x_new_lab_3001").status, job_of(sink, "x_new_lab_3001").source_fp) == ("pending", fp)
    # 先被推送(只有正文、措辞与平台原样不同),随后抓取器抓到同一条:整体换成抓取版,按抓取版的原文重排
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        pushed = _push(client, token, [_structured("quoted", quoted=None, content="Big news (as pasted by the agent)")])
        assert pushed.json()["results"][0]["status"] == "created", pushed.text
    _, pushed_fp = _fp_of(sink, "x_new_lab_2001")
    assert job_of(sink, "x_new_lab_2001").source_fp == pushed_fp
    _run_pipeline(sink, "x_new_lab", [_fetched(sink, _x_payload("quoted"), "x_new_lab")])
    sources, fetched_fp = _fp_of(sink, "x_new_lab_2001")
    assert fetched_fp != pushed_fp and sources["text"].startswith("Big news 👇")
    job = job_of(sink, "x_new_lab_2001")
    assert (job.status, job.source_fp) == ("pending", fetched_fp)
    assert run(sink) and run(sink)
    assert zh_of(sink, "x_new_lab_3001") and zh_of(sink, "x_new_lab_2001")


def test_adopting_the_fetched_copy_keeps_a_translation_that_still_matches(monkeypatch, tmp_path):
    """推送的转推已有译文;抓取版只是顶层「RT @…」包装文字不同,原帖全文没变:译文留着,不重译也不丢。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(service.daily_brief, "resolve_llm_config", lambda _session: CONFIG)
    calls = []
    fake_provider(monkeypatch, calls)
    with TestClient(app_module.app) as client:
        _login(client)
        token = _issue_token(client)
        pushed = _push(client, token, [_structured("reposted", post_id="2002")]).json()["results"]
        assert pushed[0]["status"] == "created", pushed
    assert run(sink) and job_of(sink, "x_new_lab_2002").status == "succeeded"
    before = zh_of(sink, "x_new_lab_2002")
    with Session(sink.engine) as session:
        pushed_content = session.get(ArticleRecord, "x_new_lab_2002").content
    _, pushed_fp = _fp_of(sink, "x_new_lab_2002")
    _run_pipeline(sink, "x_new_lab", [_fetched(sink, _x_payload("retweeted", post_id="2002"), "x_new_lab")])
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "x_new_lab_2002").content != pushed_content, "顶层正文确实换成了抓取版"
    _, fetched_fp = _fp_of(sink, "x_new_lab_2002")
    assert fetched_fp == pushed_fp
    assert zh_of(sink, "x_new_lab_2002") == before
    assert job_of(sink, "x_new_lab_2002").status == "succeeded"
    assert not run(sink) and len(calls) == 1
