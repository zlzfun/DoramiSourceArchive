"""The workflow keeps operating when article analysis is disabled."""

from dataclasses import replace

import pytest

from apscheduler.schedulers.background import BackgroundScheduler

import api.app as app_module


@pytest.mark.parametrize("disabled_gate", ["review_enabled", "feishu_config_path"])
def test_reload_is_independent_and_preserves_unrelated_jobs(monkeypatch, disabled_gate):
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.start(paused=True)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings,
        taxonomy=replace(app_module.settings.taxonomy, governance_enabled=True,
                         review_enabled=True, feishu_config_path="/private/not-read.ini")))
    scheduler.add_job(lambda: None, "interval", id="unrelated", hours=1)
    try:
        app_module.reload_interest_governance_schedule()
        first = scheduler.get_job("custom_interest_matches").next_run_time
        app_module.reload_interest_governance_schedule()
        ids = [job.id for job in scheduler.get_jobs()]
        assert len(ids) == len(set(ids))
        assert {"unrelated", "custom_interest_matches", "candidate_governance",
                "taxonomy_review_proposals", "taxonomy_review_digest", "taxonomy_review_digest_retry"} <= set(ids)
        assert scheduler.get_job("custom_interest_matches").next_run_time == first
        digest = scheduler.get_job("taxonomy_review_digest")
        assert digest.coalesce and digest.misfire_grace_time == 300
        assert str(digest.trigger.timezone) == "Asia/Shanghai"
        retry = scheduler.get_job("taxonomy_review_digest_retry")
        assert retry.id != digest.id and retry.func is digest.func
        assert retry.trigger.interval.total_seconds() == 300
        assert retry.coalesce and retry.misfire_grace_time == 300
        assert retry.max_instances == 1
        retry_due = retry.next_run_time
        app_module.reload_interest_governance_schedule()
        assert scheduler.get_job(retry.id).next_run_time == retry_due
        monkeypatch.setattr(app_module, "settings", replace(app_module.settings,
            taxonomy=replace(app_module.settings.taxonomy, governance_enabled=False,
                **{disabled_gate: False if disabled_gate == "review_enabled" else ""})))
        app_module.reload_interest_governance_schedule()
        assert scheduler.get_job("unrelated")
        assert scheduler.get_job("custom_interest_matches")
        assert scheduler.get_job("candidate_governance") is None
        assert scheduler.get_job("taxonomy_review_digest") is None
        assert scheduler.get_job("taxonomy_review_digest_retry") is None
    finally:
        scheduler.shutdown(wait=False)


def test_semantic_helpers_are_inert_when_llm_unconfigured(monkeypatch):
    from services import interest_governance_llm as helper
    monkeypatch.setattr(helper, "_config", lambda _engine: None)
    monkeypatch.setattr(helper, "_interest_config", lambda _engine: None)
    assert helper.make_interest_verifier(object()) is None
    assert helper.make_taxonomy_adviser(object()) is None


def test_semantic_calls_keep_paid_deadline_and_user_metering(monkeypatch):
    from config import LLMConfig
    from services import interest_governance_llm as helper
    config = LLMConfig(base_url="https://example.com", api_key="fixture", model="aux",
                       paid_authorized_until="2030-01-01T00:00:00Z")
    monkeypatch.setattr(helper, "_interest_config", lambda _engine: config)
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        return '{"outcome":"mention","scope":"aside","reason":"仅提及","evidence":[],"missing_group_indexes":[0]}'

    monkeypatch.setattr(helper, "chat_completion", completion)
    verifier = helper.make_interest_verifier(object())
    from services.interest_evidence_windows import build_evidence_page
    ast = {"all_of": [{"any_of": ["fixture"]}], "exclude": []}
    payload = {"intent": {"condition": ast}, "source": {}, **build_evidence_page("fixture body", ast)}
    result = verifier(username="fixture-user", payload=payload, reserved_output_token_limit=verifier.max_output_tokens, expected_verifier_policy=verifier.verifier_policy)
    assert result["outcome"] == "mention"
    assert calls[0]["config"].paid_authorized_until == config.paid_authorized_until
    assert calls[0]["usage_meta"].username == "fixture-user"
    assert calls[0]["max_retries"] == 1
    assert calls[0]["allow_protocol_fallback"] is False
    assert calls[0]["require_complete"]


def test_registered_digest_retry_tick_drives_bounded_failed_delivery(monkeypatch, tmp_path):
    import asyncio
    import datetime as dt
    from types import SimpleNamespace

    import httpx
    from sqlalchemy import create_engine
    from sqlmodel import Session, SQLModel, select
    from models.db import ArticleRecord, CmsTagCandidateRecord, CmsTagCandidateEvidenceRecord
    from models.taxonomy_review import ReviewNotificationBatchRecord
    from services import feishu_notifications, taxonomy_review

    engine = create_engine(f"sqlite:///{tmp_path / 'retry-wiring.db'}")
    SQLModel.metadata.create_all(engine)
    now = dt.datetime.now(dt.timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        candidate = CmsTagCandidateRecord(label="retry concept", normalized_label="retry concept", proposed_kind="topic",
            first_seen_at=now.isoformat(), last_seen_at=now.isoformat(), created_at=now.isoformat(), updated_at=now.isoformat())
        session.add(candidate)
        session.flush()
        session.add(ArticleRecord(id="retry-article", title="Retry concept", content_type="article", source_id="public-retry",
            source_url="https://example.com/retry", content="Public retry evidence", publish_date=now.isoformat(), fetched_date=now.isoformat()))
        session.flush()
        session.add(CmsTagCandidateEvidenceRecord(candidate_id=candidate.id, article_id="retry-article", source_id="public-retry",
            published_date=now.isoformat(), confidence=.9, raw_label="retry concept", created_at=now.isoformat()))
        session.commit()
        assert taxonomy_review.refresh_review_proposals(session, now=now)["created"] == 1
    config = feishu_notifications.FeishuNotificationConfig(enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook-scheduler-retry",
        signing_secret="fixture-signature", review_base_url="https://dorami.example.com")
    monkeypatch.setattr(app_module, "db_sink", SimpleNamespace(engine=engine))
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings,
        taxonomy=replace(app_module.settings.taxonomy, review_enabled=True, feishu_config_path="/not-read.ini")))
    monkeypatch.setattr(feishu_notifications, "load_feishu_config", lambda _path: config)
    current = [now]
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(429, json={"code": 1})))
    send = taxonomy_review.send_daily_review_digest

    async def controlled_send(actual_engine, actual_config):
        assert actual_engine is engine and actual_config is config
        return await send(actual_engine, actual_config, now=current[0], transport=transport)

    monkeypatch.setattr(taxonomy_review, "send_daily_review_digest", controlled_send)
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.start(paused=True)
    monkeypatch.setattr(app_module, "scheduler", scheduler)
    try:
        app_module.reload_interest_governance_schedule()
        retry = scheduler.get_job("taxonomy_review_digest_retry")
        assert retry is not None
        # Execute only the registered interval callback; no direct service calls
        # drive delivery. Clock advancement avoids wall time and external IO.
        for minutes, expected_attempts in ((0, 1), (5, 2), (15, 3), (40, 3)):
            current[0] = now + dt.timedelta(minutes=minutes)
            asyncio.run(retry.func(*retry.args, **retry.kwargs))
            with Session(engine) as session:
                batch = session.exec(select(ReviewNotificationBatchRecord)).one()
                assert batch.status == "failed" and batch.attempt_count == expected_attempts
        assert len(calls) == 3
    finally:
        scheduler.shutdown(wait=False)
        engine.dispose()
