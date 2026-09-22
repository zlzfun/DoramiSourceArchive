"""读者点播总闸（issue #137）。

总闸缺省开，只决定读者能不能发起新的点播；播客阶段授权、LLM、TTS、默认音色
仍由部署决定。关闭后两条端点直接 403，不评估、不调度、不扣额度。
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import PodcastConfig  # noqa: E402
from models.db import AiUsageRecord, AppSettingRecord  # noqa: E402
from services.reader_ondemand import (  # noqa: E402
    ENABLED_KEY,
    evaluate,
    feature_enabled,
    missing_podcast_stages,
    set_feature_enabled,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from tests.conftest import seed_default_accounts  # noqa: E402


STAMP = "2026-09-07T00:00:00+00:00"
READY_STAGES = (
    "fetch",
    "asr",
    "translate",
    "analyze",
    "digest",
    "script",
    "tts",
    "audio_qa",
    "local_publish",
)


def _podcast_config(**updates) -> PodcastConfig:
    values = {
        "installation": "external",
        "authority_id": "ondemand-switch-test",
        "allowed_stages": READY_STAGES,
        "voice_profiles": ("narrator",),
        "default_voice_profile": "narrator",
        "premium_score_threshold": 8.0,
    }
    values.update(updates)
    return PodcastConfig(**values)


def _setup(monkeypatch, tmp_path, *, podcast: PodcastConfig | None = None):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'ondemand-switch.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, podcast=podcast or _podcast_config()),
    )
    seed_default_accounts(sink.engine)
    from services import accounts as accounts_service
    from services import daily_brief as daily_brief_service

    with Session(sink.engine) as session:
        accounts_service.set_ai_beta_enabled(session, "user", True)
        daily_brief_service.set_setting(
            session, daily_brief_service.KEY_LLM_BASE_URL, "https://llm.test/v1"
        )
        daily_brief_service.set_setting(
            session, daily_brief_service.KEY_LLM_API_KEY, "sk-test"
        )
        daily_brief_service.set_setting(
            session, daily_brief_service.KEY_LLM_MODEL, "test-model"
        )
    return app_module, sink


def _speech_ready(monkeypatch, ready: bool = True) -> None:
    monkeypatch.setattr(
        "services.bailian_speech_config.resolve_config",
        lambda session: SimpleNamespace(tts_configured=ready),
    )


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def test_missing_flag_defaults_open_and_explicit_values_roundtrip(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'flag.db'}")
    with Session(sink.engine) as session:
        assert feature_enabled(session) is True
        set_feature_enabled(session, False)
        assert feature_enabled(session) is False
        stored = session.get(AppSettingRecord, ENABLED_KEY)
        assert stored is not None and stored.value == "false"
        set_feature_enabled(session, True)
        assert feature_enabled(session) is True

    with Session(sink.engine) as session:
        for raw, expected in ((" YES ", True), ("0", False), ("", False)):
            record = session.get(AppSettingRecord, ENABLED_KEY)
            record.value = raw
            session.add(record)
            session.commit()
            assert feature_enabled(session) is expected


def test_evaluate_splits_switch_from_deployment_facts():
    closed = evaluate(
        switch_on=False,
        llm_configured=False,
        tts_configured=False,
        voice_configured=False,
        missing_stages=("tts",),
    )
    assert closed.enabled is False
    assert closed.as_runtime() == {"podcast": False, "article": False}
    assert closed.blockers == ()

    ready = evaluate(
        switch_on=True,
        llm_configured=True,
        tts_configured=True,
        voice_configured=True,
        missing_stages=(),
    )
    assert ready.as_runtime() == {"podcast": True, "article": True}
    assert ready.blockers == ()

    article_only = evaluate(
        switch_on=True,
        llm_configured=True,
        tts_configured=True,
        voice_configured=True,
        missing_stages=("script", "tts"),
    )
    assert article_only.as_runtime() == {"podcast": False, "article": True}
    assert article_only.blockers == ("播客处理阶段未授权：script、tts",)

    blocked = evaluate(
        switch_on=True,
        llm_configured=False,
        tts_configured=False,
        voice_configured=False,
        missing_stages=("audio_qa",),
    )
    assert blocked.as_runtime() == {"podcast": False, "article": False}
    assert blocked.blockers == (
        "LLM 未配置",
        "TTS 未配置",
        "未设默认音色",
        "播客处理阶段未授权：audio_qa",
    )


def test_missing_podcast_stages_follow_ondemand_order():
    config = _podcast_config(
        allowed_stages=("local_publish", "translate", "analyze", "digest")
    )
    assert missing_podcast_stages(config) == ("script", "tts", "audio_qa")


def test_closed_switch_does_not_read_provider_config(monkeypatch, tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'closed.db'}")

    def _explode(session):
        raise AssertionError("总闸关闭时不应再解析凭据")

    monkeypatch.setattr("services.bailian_speech_config.resolve_config", _explode)
    monkeypatch.setattr("services.daily_brief.resolve_llm_config", _explode)
    with Session(sink.engine) as session:
        set_feature_enabled(session, False)
        from services.reader_ondemand import availability

        result = availability(session, podcast_config=_podcast_config())
    assert result.enabled is False
    assert result.blockers == ()


def test_admin_switch_and_runtime_follow_deployment(monkeypatch, tmp_path):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    _speech_ready(monkeypatch, True)
    monkeypatch.setattr(
        app_module, "schedule_forced_podcast_premium_guide", _forbid_schedule
    )
    monkeypatch.setattr(app_module, "schedule_article_listen_guide", _forbid_schedule)

    with TestClient(app_module.app) as client:
        assert client.get("/api/admin/reader-ondemand").status_code == 401
        _login(client, "user", "user")
        assert client.get("/api/admin/reader-ondemand").status_code == 403
        runtime = client.get("/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["ondemand"] == {"podcast": True, "article": True}

        _login(client, "admin", "admin")
        opened = client.get("/api/admin/reader-ondemand")
        assert opened.status_code == 200
        assert opened.json() == {
            "enabled": True,
            "podcast_available": True,
            "article_available": True,
            "blockers": [],
        }
        closed = client.post("/api/admin/reader-ondemand", json={"enabled": False})
        assert closed.status_code == 200
        assert closed.json()["enabled"] is False
        assert closed.json()["podcast_available"] is False
        assert closed.json()["article_available"] is False
        assert closed.json()["blockers"] == []

        _login(client, "user", "user")
        assert client.get("/api/runtime").json()["ondemand"] == {
            "podcast": False,
            "article": False,
        }
        for path in (
            "/api/reader/ai/podcasts/missing-episode/ondemand",
            "/api/reader/ai/articles/missing-article/ondemand",
        ):
            denied = client.post(path)
            assert denied.status_code == 403
            assert denied.json()["detail"] == "点播功能已关闭"

    with Session(app_module.db_sink.engine) as session:
        assert session.exec(select(AiUsageRecord)).all() == []


def test_open_switch_reports_article_only_when_podcast_stages_missing(
    monkeypatch, tmp_path
):
    config = _podcast_config(
        allowed_stages=tuple(stage for stage in READY_STAGES if stage != "tts")
    )
    app_module, _sink = _setup(monkeypatch, tmp_path, podcast=config)
    _speech_ready(monkeypatch, True)

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        payload = client.get("/api/admin/reader-ondemand").json()
        assert payload["enabled"] is True
        assert payload["article_available"] is True
        assert payload["podcast_available"] is False
        assert payload["blockers"] == ["播客处理阶段未授权：tts"]
        _login(client, "user", "user")
        assert client.get("/api/runtime").json()["ondemand"] == {
            "podcast": False,
            "article": True,
        }


def _forbid_schedule(*_args, **_kwargs):
    raise AssertionError("总闸关闭后不应调度点播")


@pytest.mark.parametrize(
    "switch_on, llm, tts, voice, expected_article",
    [
        (True, True, True, False, False),
        (True, True, False, True, False),
        (True, False, True, True, False),
    ],
)
def test_article_ondemand_needs_every_provider(
    switch_on, llm, tts, voice, expected_article
):
    result = evaluate(
        switch_on=switch_on,
        llm_configured=llm,
        tts_configured=tts,
        voice_configured=voice,
        missing_stages=(),
    )
    assert result.article is expected_article
    assert result.podcast is expected_article
