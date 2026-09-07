"""Podcast stage authority policy and capability endpoint tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from config import PodcastConfig, RuntimeConfig, load_config
from services.podcast_artifacts import PodcastArtifactStore
from services.podcast_stage_policy import (
    EXECUTION_BOUNDARIES,
    PodcastStageDenied,
    PodcastStagePolicy,
)
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


def _config(
    *,
    installation: str = "external",
    stages: tuple[str, ...] = ("fetch", "asr", "translate"),
    authority_id: str = "podcast-external-a",
) -> PodcastConfig:
    return PodcastConfig(
        installation=installation,
        authority_id=authority_id,
        allowed_stages=stages,
    )


def test_config_parses_allowlist_and_environment_overrides(monkeypatch, tmp_path):
    ini = tmp_path / "backend.ini"
    ini.write_text(
        "[podcast]\ninstallation = external\nauthority_id = ini-external\n"
        "allowed_stages = fetch,asr,translate,analyze,digest,script,local_publish\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_PODCAST_INSTALLATION", "internal")
    monkeypatch.setenv("DORAMI_PODCAST_AUTHORITY_ID", "internal-a")
    monkeypatch.setenv("DORAMI_PODCAST_ALLOWED_STAGES", "")

    configured = load_config().podcast

    assert configured.installation == "internal"
    assert configured.authority_id == "internal-a"
    assert configured.allowed_stages == ()


@pytest.mark.parametrize(
    ("stages", "match"),
    [
        (("fetch", "unknown"), "unknown"),
        (("fetch", "fetch"), "duplicate"),
    ],
)
def test_config_rejects_unknown_duplicate_and_conflicting_stages(stages, match):
    with pytest.raises(ValueError, match=match):
        _config(installation="development", stages=stages, authority_id="dev-a")


def test_config_rejects_installation_stage_conflicts_and_missing_authority():
    external = _config(
        installation="external",
        stages=("asr", "tts", "audio_qa", "local_publish"),
    )
    assert external.allowed_stages == ("asr", "tts", "audio_qa", "local_publish")
    with pytest.raises(ValueError, match="internal.*sync-only"):
        _config(installation="internal", stages=("asr",), authority_id="internal-a")
    with pytest.raises(ValueError, match="authority_id"):
        _config(installation="external", stages=("fetch",), authority_id="")


def test_processing_config_is_fail_closed_and_installation_scoped(monkeypatch, tmp_path):
    ini = tmp_path / "processing.ini"
    ini.write_text(
        "[podcast]\n"
        "installation = external\n"
        "authority_id = external-a\n"
        "allowed_stages = fetch,asr,translate,analyze,digest,script\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_PODCAST_PROCESSING_ENABLED", "true")
    monkeypatch.setenv(
        "DORAMI_PODCAST_PROVIDER_READY_TARGETS", "transcript,digest_blog"
    )
    monkeypatch.setenv("DORAMI_PODCAST_MONTHLY_BUDGET_CNY_MINOR", "12000")
    monkeypatch.setenv("DORAMI_PODCAST_PER_RUN_BUDGET_CNY_MINOR", "900")
    configured = load_config().podcast
    assert configured.processing_enabled is True
    assert configured.provider_ready_targets == ("transcript", "digest_blog")
    assert configured.monthly_budget_cny_minor == 12_000
    assert configured.per_run_budget_cny_minor == 900

    with pytest.raises(ValueError, match="missing stage"):
        PodcastConfig(
            installation="external",
            authority_id="external-a",
            allowed_stages=("fetch", "asr"),
            processing_enabled=True,
            provider_ready_targets=("digest_audio",),
            monthly_budget_cny_minor=100,
            per_run_budget_cny_minor=10,
            voice_profiles=("narrator",),
            default_voice_profile="narrator",
        )
    with pytest.raises(ValueError, match="requires targets and positive"):
        PodcastConfig(
            installation="internal",
            authority_id="internal-a",
            allowed_stages=(),
            processing_enabled=True,
        )


@pytest.mark.parametrize("boundary", sorted(EXECUTION_BOUNDARIES))
def test_require_stage_rejects_disallowed_stage_at_every_boundary(boundary):
    policy = PodcastStagePolicy(_config(stages=("fetch",)))
    with pytest.raises(PodcastStageDenied, match=f"analyze.*{boundary}"):
        policy.require_stage("analyze", boundary=boundary)


def test_require_stage_rejects_unknown_stage_and_boundary():
    policy = PodcastStagePolicy(_config(stages=("fetch",)))
    with pytest.raises(ValueError, match="unknown Podcast stage"):
        policy.require_stage("made_up", boundary="enqueue")
    with pytest.raises(ValueError, match="unknown Podcast execution boundary"):
        policy.require_stage("fetch", boundary="after_commit")


def test_external_owns_provider_calls_and_internal_denies_all_before_spy():
    called: list[str] = []

    def provider(name: str) -> None:
        called.append(name)

    external = PodcastStagePolicy(_config(
        installation="external", stages=("fetch", "asr", "tts")
    ))
    external.require_stage("tts", boundary="provider_submit")
    provider("tts")

    internal = PodcastStagePolicy(
        _config(
            installation="internal",
            stages=(),
            authority_id="podcast-internal-a",
        )
    )
    with pytest.raises(PodcastStageDenied):
        internal.require_stage("asr", boundary="provider_submit")
        provider("asr")
    assert called == ["tts"]


def test_artifact_writer_is_single_site_and_requires_producing_stage():
    external = PodcastStagePolicy(
        _config(stages=("fetch", "script"))
    )
    assert external.artifact_kind_writer_allowed("narration_script_zh") is True
    assert external.artifact_kind_writer_allowed("digest_audio_zh") is True
    external.require_artifact_writer("narration_script_zh", boundary="commit")
    with pytest.raises(PodcastStageDenied, match="local_publish"):
        external.require_artifact_writer("digest_audio_zh", boundary="commit")

    internal = PodcastStagePolicy(
        _config(
            installation="internal",
            stages=(),
            authority_id="podcast-internal-a",
        )
    )
    assert internal.artifact_kind_writer_allowed("digest_audio_zh") is False
    assert internal.artifact_kind_writer_allowed("narration_script_zh") is False

    no_script = PodcastStagePolicy(_config(stages=("fetch",)))
    with pytest.raises(PodcastStageDenied, match="script"):
        no_script.require_artifact_writer("narration_script_zh", boundary="commit")

    with pytest.raises(ValueError, match="unknown Podcast artifact kind"):
        external.require_artifact_writer("narration_script", boundary="commit")


def test_default_development_policy_has_no_provider_stages(monkeypatch, tmp_path):
    ini = tmp_path / "empty.ini"
    ini.write_text("[storage]\ndatabase_url = sqlite:///:memory:\n", encoding="utf-8")
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    for key in (
        "DORAMI_PODCAST_INSTALLATION",
        "DORAMI_PODCAST_AUTHORITY_ID",
        "DORAMI_PODCAST_ALLOWED_STAGES",
    ):
        monkeypatch.delenv(key, raising=False)
    config = load_config().podcast
    assert config.installation == "development"
    assert config.allowed_stages == ("fetch", "local_publish")
    assert not ({"asr", "translate", "analyze", "digest", "script", "tts"} & set(config.allowed_stages))


def test_admin_capabilities_endpoint_is_read_only_and_redacted(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'capabilities.db'}")
    seed_default_accounts(sink.engine)
    artifact_store = PodcastArtifactStore(
        sink.engine,
        tmp_path / "capabilities-cas",
        max_bytes=1024 * 1024,
        total_quota_bytes=2 * 1024 * 1024,
        source_audio_quota_bytes=1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
    )
    podcast = _config(stages=("fetch", "asr", "script"))
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "podcast_artifact_store", artifact_store)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=podcast,
        ),
    )
    with TestClient(app_module.app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "user", "password": "user"}
        ).status_code == 200
        assert client.get("/api/admin/podcast-stages/capabilities").status_code == 403

    with TestClient(app_module.app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).status_code == 200
        response = client.get("/api/admin/podcast-stages/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert body["installation"] == "external"
        assert body["authority_id"] == "podcast-external-a"
        assert body["allowed_stages"] == ["fetch", "asr", "script"]
        assert set(body["execution_boundaries"]) == EXECUTION_BOUNDARIES
        assert "narration_script_zh" in body["writable_artifact_kinds"]
        assert "digest_audio_zh" in body["writable_artifact_kinds"]
        assert all("key" not in key.lower() and "secret" not in key.lower() for key in body)
        assert client.post("/api/admin/podcast-stages/capabilities").status_code == 405
