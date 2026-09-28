"""Issue #141: actual admin HTTP route before ASR submission."""
from dataclasses import replace
from types import SimpleNamespace
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select
from tests.test_podcast_processing_api import api_env, _login
from models.db import PodcastProcessingRecord, PodcastSourceMediaSnapshotRecord, PodcastBudgetReservationRecord
from services import http_safety

@pytest.mark.parametrize(
    "scenario,status,code",
    [
        ("gate", 503, "podcast_landing_gated"),
        ("timeout", 504, "podcast_source_media_timeout"),
        ("fetch403", 502, "podcast_source_media_fetch_failed"),
        ("too_large", 413, "podcast_source_media_too_large"),
    ],
)
def test_full_analysis_input_preparation_errors_are_safe_domain_responses(
    api_env, monkeypatch, scenario, status, code
):
    app, sink, _store, snapshot, config = api_env
    with Session(sink.engine) as session:
        session.delete(session.get(PodcastSourceMediaSnapshotRecord, snapshot.id))
        session.commit()
    monkeypatch.setattr(app, "settings", replace(
        app.settings, podcast=replace(config, provider_ready_targets=("full_analysis",))
    ))
    app.podcast_processing_providers.register_target(
        "full_analysis",
        stage_executors={"asr": lambda _c: None, "analyze": lambda _c: None},
        estimator=lambda _m: 25,
    )
    monkeypatch.setattr(app.podcast_processing_admin_service, "require_full_analysis_llm", lambda *_a: None)
    monkeypatch.setattr(app, "_podcast_asr_admission_ready", lambda: scenario != "gate")
    monkeypatch.setattr(app.podcast_speech_config_service, "resolve_config", lambda _s: SimpleNamespace(asr_max_audio_seconds_per_file=7200))
    calls = []

    async def fail_download(*_args, **_kwargs):
        calls.append(scenario)
        if scenario == "timeout":
            raise http_safety.PublicDownloadTimeout("signed-url-secret")
        if scenario == "too_large":
            raise http_safety.PublicDownloadError("下载响应超过大小上限 signed-url-secret")
        raise http_safety.PublicDownloadError("下载服务器返回异常状态码 403 signed-url-secret")

    monkeypatch.setattr(http_safety, "stream_public_url_to_file", fail_download)
    client = TestClient(app.app, raise_server_exceptions=False)
    _login(client)
    response = client.post("/api/admin/podcast-episodes/episode-ok/process", json={
        "target": "full_analysis", "selection_override": True,
        "reason": "input failure contract", "idempotency_key": "input-failure-" + scenario,
    })
    assert response.status_code == status
    assert response.json()["code"] == code
    assert "signed-url-secret" not in response.text
    assert calls == ([] if scenario == "gate" else [scenario])
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []
        assert session.exec(select(PodcastSourceMediaSnapshotRecord)).all() == []
        assert session.exec(select(PodcastBudgetReservationRecord)).all() == []


