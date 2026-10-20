"""Durable translation: real DB, real LLM client protocol, fake provider only."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlmodel import Session, select

from models.db import ArticleRecord, AiUsageRecord
from services import translation_jobs as tr, reader_ai
from tests.test_reader_ai_limits import (
    _make_sink, _seed_users, _configure_llm, _enable_ai_beta, _seed_article,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    sink = _make_sink(tmp_path, "translation.db")
    _seed_users(sink.engine)
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _enable_ai_beta(sink.engine, "admin")
    _seed_article(sink.engine, "a", "rss_a", "English title",
                  "First paragraph with $x$.\n\n" + "Second paragraph. " * 250)
    calls = []
    mode = {"fail": False, "block": None, "started": None, "usage": True}

    async def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if mode["started"]:
            mode["started"].set()
        if mode["block"]:
            await mode["block"].wait()
        if mode.get("http_status"):
            data = {"error": "provider rejected"}
            if mode.get("error_usage"):
                data["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            return httpx.Response(mode["http_status"], json=data)
        if mode["fail"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
                                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        prompt = payload["messages"][-1]["content"]
        content = (prompt.split("【待翻译正文】\n", 1)[1] if "【待翻译正文】\n" in prompt
                   else "测试标题")
        result = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        if mode["usage"]:
            result["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return httpx.Response(200, json=result)

    @asynccontextmanager
    async def client(_config):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            yield transport

    monkeypatch.setattr(tr, "client_session", client)
    return sink.engine, calls, mode


def start(engine):
    return tr.request(engine, "user", "a")["job"]


def row(engine, model, key):
    with Session(engine) as session:
        return session.get(model, key)


def test_full_job_checkpoint_publish_and_zero_cost_cache(env):
    engine, calls, _ = env
    job = start(engine)
    assert job["total"] >= 3
    assert tr.status(engine, "user", "a")["status"] == "queued"
    asyncio.run(tr.run_once(engine))
    state = tr.status(engine, "user", "a")
    assert state["status"] == "succeeded", state
    assert "$x$" in state["result"]["translation"]
    assert state["job"] is None  # Successful receipt must not occupy queue capacity.
    assert row(engine, tr.Job, job["job_id"]) is None
    with Session(engine) as session:
        assert not session.exec(select(tr.Chunk)).all()
        usage = session.exec(select(AiUsageRecord)).one()
        assert usage.calls == len(calls) == job["total"]
        assert usage.total_tokens == len(calls) * 15
    assert tr.request(engine, "admin", "a")["result"]["cached"]
    assert not asyncio.run(tr.run_once(engine))
    assert len(calls) == job["total"]


def test_dedup_and_global_claim_across_connections(env):
    engine, _, _ = env
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: start(engine), range(8)))
    assert len({item["job_id"] for item in results}) == 1
    assert tr.request(engine, "admin", "a")["job"]["job_id"] == results[0]["job_id"]
    with ThreadPoolExecutor(8) as pool:
        claims = list(pool.map(lambda _: tr.claim(engine), range(8)))
    assert sum(value is not None for value in claims) == 1


def test_expired_lease_reuses_verified_checkpoint(env):
    engine, calls, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    tr._checkpoint(engine, job_id, token, 0, plan.chunks[0].text)
    with Session(engine) as session:
        item = session.get(tr.Job, job_id)
        item.lease_until = time.time() - 1
        session.commit()
    with pytest.raises(tr.Error):
        tr._checkpoint(engine, job_id, token, 1, "late")
    asyncio.run(tr.run_once(engine))
    assert tr.status(engine, "user", "a")["status"] == "succeeded"
    assert len(calls) == job["total"] - 1


def test_failures_are_bounded_and_resume_only_missing(env):
    engine, calls, mode = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    tr._checkpoint(engine, job_id, token, 0, plan.chunks[0].text)
    mode["fail"] = True
    asyncio.run(tr.run_job(engine, job_id, token))
    assert len(calls) == (job["total"] - 1) * 3
    state = tr.status(engine, "user", "a")
    assert state["status"] == "failed"
    assert state["job"]["completed"] == 1
    assert "translation_zh" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)
    mode["fail"] = False
    before = len(calls)
    assert tr.resume(engine, "user", job_id)["status"] == "queued"
    asyncio.run(tr.run_once(engine))
    assert len(calls) - before == job["total"] - 1
    assert tr.status(engine, "user", "a")["status"] == "succeeded"


@pytest.mark.parametrize("change", ["body", "title", "model", "version"])
def test_changed_input_never_publishes_or_reuses_checkpoints(env, monkeypatch, change):
    engine, calls, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    tr._checkpoint(engine, job_id, token, 0, plan.chunks[0].text)
    if change == "version":
        monkeypatch.setattr(tr, "PIPELINE_VERSION", 999)
    else:
        with Session(engine) as session:
            article = session.get(ArticleRecord, "a")
            if change == "body":
                article.content += "\nChanged source"
            elif change == "title":
                article.title += " revised"
            else:
                tr.daily_brief.set_setting(session, tr.daily_brief.KEY_LLM_MODEL, "another-model")
            session.commit()
    asyncio.run(tr.run_job(engine, job_id, token))
    assert not calls
    assert row(engine, tr.Job, job_id).status == "stale"
    with pytest.raises(tr.Error, match="更新"):
        tr.resume(engine, "user", job_id)
    assert start(engine)["job_id"] != job["job_id"]


def test_cancellation_fences_inflight_publish_and_can_resume(env):
    engine, calls, mode = env

    async def scenario():
        job = start(engine)
        mode["block"], mode["started"] = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(tr.run_once(engine))
        await asyncio.wait_for(mode["started"].wait(), timeout=5)
        with pytest.raises(tr.Error):
            tr.cancel(engine, "admin", job["job_id"])
        tr.cancel(engine, "user", job["job_id"])
        mode["block"].set()
        await task
        assert tr.status(engine, "user", "a")["status"] == "cancelled"
        assert "translation_zh" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)
        tr.resume(engine, "user", job["job_id"])
        await tr.run_once(engine)
        assert tr.status(engine, "user", "a")["status"] == "succeeded"
    asyncio.run(scenario())


def test_shutdown_retains_checkpoint_and_releases_worker(env):
    engine, calls, mode = env

    async def scenario():
        job = start(engine)
        mode["block"], mode["started"] = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(tr.run_once(engine))
        await asyncio.wait_for(mode["started"].wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert row(engine, tr.Job, job["job_id"]).lease_until == 0
        with Session(engine) as session:
            assert all(c.status == "unknown" for c in session.exec(select(tr.Call)).all())
        mode["block"] = None
        await tr.run_once(engine)
        assert tr.status(engine, "user", "a")["status"] == "succeeded"
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["account", "global", "hidden", "private"])
def test_worker_rechecks_authority(env, change):
    engine, calls, _ = env
    job = start(engine)
    with Session(engine) as session:
        if change == "account":
            tr.accounts.set_ai_beta_enabled(session, "user", False)
        elif change == "global":
            tr.accounts.set_ai_beta_global_enabled(session, False)
        elif change == "hidden":
            from models.db import AppSettingRecord
            session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["rss_a"]'))
            session.commit()
        else:
            article = session.get(ArticleRecord, "a")
            article.source_id = "user_rss_private"
            session.commit()
    assert not asyncio.run(tr.run_once(engine))
    assert not calls
    assert row(engine, tr.Job, job["job_id"]).status in {"failed", "stale"}


def test_atomic_daily_quota_reservations(env, monkeypatch):
    engine, calls, _ = env
    monkeypatch.setattr(tr, "DAILY_CALL_LIMIT", 1)
    job = start(engine)
    asyncio.run(tr.run_once(engine))
    assert len(calls) == 1
    assert tr.status(engine, "user", "a")["status"] == "failed"
    assert row(engine, tr.Job, job["job_id"]).completed == 1
    assert tr.status(engine, "admin", "a")["job"]["completed"] == 1


def test_budget_hold_and_unknown_usage_not_zero(env):
    engine, calls, mode = env
    mode["usage"] = False
    job = start(engine)
    with Session(engine) as session:
        tr.accounts.set_ai_daily_token_budget(session, 30000)
    asyncio.run(tr.run_once(engine))
    with Session(engine) as session:
        holds = session.exec(select(tr.Call)).all()
        assert all(c.status == "unknown" and c.recorded for c in holds)
        assert sum(c.reserved_tokens for c in holds) <= 30000
        assert session.exec(select(AiUsageRecord)).one().calls == len(calls)
    assert len(calls) > 0
    saved = row(engine, tr.Job, job["job_id"])
    assert (saved.completed if saved else job["total"]) == len(calls)


def test_no_partial_publish_when_checkpoint_tampered(env):
    engine, _, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    with Session(engine) as session:
        for chunk in session.exec(select(tr.Chunk)).all():
            chunk.text = "broken"
        session.commit()
    with pytest.raises(tr.TranslationStructureError):
        tr._finish(engine, job_id, token, plan)
    assert "translation_zh" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)


def test_capacity_ttl_and_cascade(env, monkeypatch):
    engine, _, _ = env
    monkeypatch.setattr(tr, "MAX_JOBS", 1)
    job = start(engine)
    _seed_article(engine, "b", "rss_a", "Other", "Body")
    with pytest.raises(tr.Error, match="队列已满"):
        tr.request(engine, "user", "b")
    tr.cancel(engine, "user", job["job_id"])
    with Session(engine) as session:
        item = session.get(tr.Job, job["job_id"])
        item.updated_at = time.time() - tr.RETENTION_SECONDS - 1
        session.commit()
    assert tr.request(engine, "user", "b")["status"] == "queued"
    with Session(engine) as session:
        assert session.get(tr.Job, job["job_id"]) is None
        assert not session.exec(select(tr.Chunk).where(tr.Chunk.job_id == job["job_id"])).all()
        session.delete(session.get(ArticleRecord, "b"))
        session.commit()
        assert not session.exec(select(tr.Chunk)).all()


def test_http_submission_returns_before_provider_and_refresh_restores(env, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import api.app as app
    from tests.test_reader_ai_limits import _login
    engine, calls, _ = env
    monkeypatch.setattr(app, "db_sink", SimpleNamespace(engine=engine))
    # Deliberately do not enter lifespan: submit must not depend on a worker
    # running in the HTTP process, and a separate worker can pick up the record.
    client = TestClient(app.app)
    _login(client)
    response = client.post("/api/reader/ai/translation-jobs", json={"article_id": "a"})
    assert response.status_code == 202
    assert not calls
    job_id = response.json()["job"]["job_id"]
    assert client.get("/api/reader/ai/translation-jobs?article_id=a").json()["job"]["job_id"] == job_id
    # Old long-body endpoint also refuses to occupy the HTTP request.
    assert client.post("/api/reader/ai/translate", json={"article_id": "a"}).status_code == 202
    asyncio.run(tr.run_once(engine))
    refreshed = TestClient(app.app)
    _login(refreshed)
    data = refreshed.get("/api/reader/ai/translation-jobs?article_id=a").json()
    assert data["status"] == "succeeded"
    assert data["result"]["cached"]
    assert client.post("/api/reader/ai/translation-jobs", json={"article_id": "a"}).status_code == 200
    _enable_ai_beta(engine, "user")  # Permission is read again, never frozen in a task.
    with Session(engine) as session:
        tr.accounts.set_ai_beta_enabled(session, "user", False)
    assert refreshed.get("/api/reader/ai/translation-jobs?article_id=a").status_code == 403


def test_expired_reservation_and_old_owner_cannot_write(env):
    engine, _, _ = env
    job = start(engine)
    job_id, old_token = tr.claim(engine)
    call_id, _ = tr._reserve(engine, job_id, old_token, 0, 500)
    with Session(engine) as session:
        item = session.get(tr.Job, job_id)
        item.lease_until = time.time() - 1
        session.commit()
    new_id, new_token = tr.claim(engine)
    assert new_id == job_id and new_token != old_token
    assert row(engine, tr.Call, call_id).status == "unknown"
    with pytest.raises(tr.Error):
        tr.heartbeat(engine, job_id, old_token)
    with pytest.raises(tr.Error):
        tr._checkpoint(engine, job_id, old_token, 0, "late")
    # A late receipt can still settle the old cost once, even though its text
    # can no longer be published by the stale owner.
    tr._settle(engine, call_id, {"total_tokens": 17}, "test-model")
    tr._settle(engine, call_id, {"total_tokens": 17}, "test-model")
    with Session(engine) as session:
        usage = session.exec(select(AiUsageRecord)).one()
        assert usage.calls == 1 and usage.total_tokens == 17


def test_expired_owner_without_successor_returns_to_queue(env):
    engine, calls, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        item = session.get(tr.Job, job_id)
        item.lease_until = time.time() - 1
        session.commit()
    asyncio.run(tr.run_job(engine, job_id, token))
    assert tr.status(engine, "user", "a")["status"] == "queued"
    assert not calls
    asyncio.run(tr.run_once(engine))
    assert tr.status(engine, "user", "a")["status"] == "succeeded"
    assert len(calls) == job["total"]


@pytest.mark.parametrize("bad", ["truncated", "empty", "missing_unit", "damaged_token", "table_numbers"])
def test_invalid_model_output_never_published(env, monkeypatch, bad):
    from llm.client import LLMIncompleteOutput, LLMError
    engine, _, _ = env
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.content = "Formula $x$.\n\n| Value | Score |\n| --- | --- |\n| A | 12.5% |"
        article.title = "中文"
        session.commit()
    monkeypatch.setattr(tr, "MAX_ATTEMPTS", 1)

    async def broken(_title, segment, *_args, **_kwargs):
        if bad == "truncated":
            raise LLMIncompleteOutput("truncated")
        if bad == "empty":
            raise LLMError("empty content")
        units = tr.translation_units(segment)
        if bad == "missing_unit":
            units.pop()
        elif bad == "damaged_token":
            units[0]["text"] = "missing formula"
        else:
            units[-1]["text"] = units[-1]["text"].replace("12.5%", "12.5")
        from services.translation_markdown import restore_translation_units
        return restore_translation_units(segment, json.dumps({"units": units}))
    monkeypatch.setattr(reader_ai, "_translate_segment", broken)
    start(engine)
    asyncio.run(tr.run_once(engine))
    assert tr.status(engine, "user", "a")["status"] == "failed"
    assert "translation_zh" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)


def test_cancel_and_permission_change_are_rechecked_at_publish(env):
    engine, _, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    for ordinal, chunk in enumerate(plan.chunks):
        tr._checkpoint(engine, job_id, token, ordinal, chunk.text)
    tr._checkpoint(engine, job_id, token, len(plan.chunks), "译名")
    with Session(engine) as session:
        tr.accounts.set_ai_beta_enabled(session, "user", False)
    with pytest.raises(tr.Error):
        tr._finish(engine, job_id, token, plan)
    tr.cancel(engine, "user", job_id)  # Cancellation remains possible with AI off.
    with pytest.raises(tr.Error):
        tr._finish(engine, job_id, token, plan)
    assert "translation_zh" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)


def test_literal_only_work_does_not_need_budget(env, monkeypatch):
    engine, calls, _ = env
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.content, article.title = "```python\nx = 1\n```", "中文"
        session.commit()
    monkeypatch.setattr(tr, "DAILY_CALL_LIMIT", 0)
    start(engine)
    asyncio.run(tr.run_once(engine))
    assert tr.status(engine, "user", "a")["status"] == "succeeded"
    assert not calls


def test_success_releases_global_queue_capacity(env, monkeypatch):
    engine, _, _ = env
    monkeypatch.setattr(tr, "MAX_JOBS", 1)
    start(engine)
    asyncio.run(tr.run_once(engine))
    with Session(engine) as session:
        assert not session.exec(select(tr.Job)).all()
    _seed_article(engine, "b", "rss_a", "Title", "Body")
    assert tr.request(engine, "user", "b")["status"] == "queued"


def test_per_user_slots_and_terminal_lru_without_affecting_other_users(env):
    engine, _, _ = env
    first = start(engine)
    for name in ("b", "c", "d"):
        _seed_article(engine, name, "rss_a", "Title", "Body")
    tr.request(engine, "user", "b")
    tr.request(engine, "user", "c")
    with pytest.raises(tr.Error, match="3 篇"):
        tr.request(engine, "user", "d")
    # Joining a shared active job has no slot cost or ownership takeover.
    assert tr.request(engine, "admin", "a")["job"]["job_id"] == first["job_id"]
    assert tr.request(engine, "admin", "d")["status"] == "queued"
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    tr._checkpoint(engine, job_id, token, 0, plan.chunks[0].text)
    tr.cancel(engine, "user", job_id)
    with Session(engine) as session:
        job = session.get(tr.Job, job_id)
        job.lease_until = 0
        session.commit()
    _seed_article(engine, "e", "rss_a", "Title", "Body")
    response = tr.request(engine, "user", "e")
    assert response["evicted_progress"]
    assert row(engine, tr.Job, first["job_id"]) is None
    with Session(engine) as session:
        assert session.exec(select(tr.Job).where(tr.Job.created_by == "admin")).one().article_id == "d"


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 422, 429])
def test_definitive_provider_rejection_releases_quota(env, status):
    engine, calls, mode = env
    mode["http_status"] = status
    job = start(engine)
    asyncio.run(tr.run_once(engine))
    with Session(engine) as session:
        assert all(c.status == "released" for c in session.exec(select(tr.Call)).all())
        assert not session.exec(select(AiUsageRecord)).all()
        tr._cost_gate(session, "user", 1)
    assert row(engine, tr.Job, job["job_id"]).status == "failed"
    if status != 429:
        # At most one concurrent wave; an auth/config failure stops new chunks.
        assert len(calls) <= 8  # HTTP400 can do one compatibility fallback per call.


@pytest.mark.parametrize("status", [408, 500, 502, 504])
def test_ambiguous_provider_response_keeps_cost_hold(env, status):
    engine, calls, mode = env
    mode["http_status"] = status
    start(engine)
    asyncio.run(tr.run_once(engine))
    with Session(engine) as session:
        rows = session.exec(select(tr.Call)).all()
        assert len(rows) == len(calls)
        assert all(c.status == "unknown" for c in rows)
        assert not session.exec(select(AiUsageRecord)).all()


def test_error_usage_is_settled_once_before_retry(env):
    engine, calls, mode = env
    mode.update(http_status=500, error_usage=True)
    start(engine)
    asyncio.run(tr.run_once(engine))
    with Session(engine) as session:
        rows = session.exec(select(tr.Call)).all()
        assert all(c.status == "settled" for c in rows)
        usage = session.exec(select(AiUsageRecord)).one()
        assert usage.calls == len(calls) == len(rows)
        assert usage.total_tokens == 15 * len(rows)


def test_published_cache_survives_model_and_credential_rotation(env):
    engine, calls, _ = env
    start(engine)
    asyncio.run(tr.run_once(engine))
    before = len(calls)
    with Session(engine) as session:
        tr.daily_brief.set_setting(session, tr.daily_brief.KEY_LLM_MODEL, "new-model")
        tr.daily_brief.set_setting(session, tr.daily_brief.KEY_LLM_API_KEY, "rotated-key")
    assert tr.request(engine, "user", "a")["result"]["cached"]
    assert not asyncio.run(tr.run_once(engine))
    assert len(calls) == before
    assert "translation_zh_config" not in json.loads(row(engine, ArticleRecord, "a").extensions_json)


def test_legacy_body_cache_is_visible_and_only_missing_title_is_translated(env):
    engine, calls, _ = env
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.extensions_json = json.dumps({"translation_zh": "已发布的完整正文", "translation_zh_config": "legacy"})
        session.commit()
    assert tr.status(engine, "user", "a")["result"]["translation"] == "已发布的完整正文"
    job = start(engine)
    assert job["total"] == 1
    asyncio.run(tr.run_once(engine))
    assert len(calls) == 1
    assert "【待翻译正文】" not in calls[0]["messages"][-1]["content"]
    state = tr.status(engine, "user", "a")
    assert state["result"]["translation"] == "已发布的完整正文"
    assert state["result"]["title"] == "测试标题"
    ext = json.loads(row(engine, ArticleRecord, "a").extensions_json)
    assert "translation_zh_fp" not in ext  # Title-only work does not relabel old body provenance.
    assert "translation_zh_config" not in ext


def test_title_only_work_rejects_replaced_body_cache(env):
    engine, calls, _ = env
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.extensions_json = json.dumps({"translation_zh": "已发布正文"})
        session.commit()
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.extensions_json = json.dumps({"translation_zh": "被更新的正文"})
        session.commit()
    asyncio.run(tr.run_job(engine, job_id, token))
    assert not calls
    assert row(engine, tr.Job, job["job_id"]).status == "stale"


def test_layout_metadata_without_body_is_not_a_successful_empty_translation(env):
    engine, calls, _ = env
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.content = "[zqpl]: center\n"
        session.commit()
    with pytest.raises(tr.Error, match="暂无可翻译"):
        start(engine)
    assert not calls
    with Session(engine) as session:
        assert not session.exec(select(tr.Job)).all()


def _write_digest_title(engine):
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        ext = reader_ai._load_extensions(article)
        ext[reader_ai.TRANSLATION_TITLE_KEY] = "早报已翻译的标题"
        ext[reader_ai.TRANSLATION_TITLE_FP_KEY] = reader_ai._body_fingerprint(article.title.strip())
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.commit()


def test_digest_title_does_not_invalidate_saved_body_checkpoints(env):
    engine, calls, _ = env
    job = start(engine)
    job_id, token = tr.claim(engine)
    with Session(engine) as session:
        article, config = tr._current(session, session.get(tr.Job, job_id))
        plan, _ = tr._plan(article, tr.input_key(article, config))
    tr._checkpoint(engine, job_id, token, 0, plan.chunks[0].text)
    _write_digest_title(engine)
    tr.heartbeat(engine, job_id, token)
    assert tr.status(engine, "user", "a")["job"]["job_id"] == job_id
    asyncio.run(tr.run_job(engine, job_id, token))
    state = tr.status(engine, "user", "a")
    assert state["status"] == "succeeded"
    assert state["result"]["title"] == "早报已翻译的标题"
    assert len(calls) == job["total"] - 2  # Saved body + externally cached title.
    with Session(engine) as session:
        assert session.exec(select(AiUsageRecord)).one().calls == len(calls)


def test_digest_title_written_during_body_request_is_reused(env, monkeypatch):
    engine, calls, mode = env
    resolve = tr.daily_brief.resolve_llm_config
    monkeypatch.setattr(tr.daily_brief, "resolve_llm_config",
                        lambda session: replace(resolve(session), map_concurrency=1))
    job = start(engine)

    async def scenario():
        mode["started"], mode["block"] = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(tr.run_once(engine))
        await asyncio.wait_for(mode["started"].wait(), timeout=5)
        assert len(calls) == 1
        _write_digest_title(engine)
        assert tr.status(engine, "user", "a")["job"]["job_id"] == job["job_id"]
        mode["block"].set()
        await task

    asyncio.run(scenario())
    state = tr.status(engine, "user", "a")
    assert state["status"] == "succeeded"
    assert state["result"]["title"] == "早报已翻译的标题"
    assert len(calls) == job["total"] - 1
    assert all("【待翻译正文】" in call["messages"][-1]["content"] for call in calls)


def test_removed_title_cache_does_not_change_existing_body_plan(env):
    engine, calls, _ = env
    _write_digest_title(engine)
    job = start(engine)
    with Session(engine) as session:
        article = session.get(ArticleRecord, "a")
        article.extensions_json = "{}"
        session.commit()
    asyncio.run(tr.run_once(engine))
    assert len(calls) == job["total"]  # Existing plan has only body checkpoints.
    ext = json.loads(row(engine, ArticleRecord, "a").extensions_json)
    assert ext[reader_ai.TRANSLATION_KEY]
    assert reader_ai.TRANSLATION_TITLE_KEY not in ext
    assert reader_ai.TRANSLATION_TITLE_FP_KEY not in ext
    assert row(engine, tr.Job, job["job_id"]) is None
    assert start(engine)["total"] == 1  # Missing title can be filled separately.


@pytest.mark.parametrize("extra", [-2, 1])
def test_invalid_persisted_title_layout_is_stale(env, extra):
    engine, calls, _ = env
    job = start(engine)
    with Session(engine) as session:
        session.get(tr.Job, job["job_id"]).total += extra
        session.commit()
    asyncio.run(tr.run_once(engine))
    assert not calls
    assert row(engine, tr.Job, job["job_id"]).status == "stale"
