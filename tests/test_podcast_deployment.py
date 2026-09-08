"""Deployment guards for the local Podcast audio runtime."""

import configparser
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_backend_image_installs_ffmpeg_and_cleans_apt_metadata():
    dockerfile = _read("docker/backend.Dockerfile")
    install = re.search(
        r"apt-get install -y --no-install-recommends(?P<packages>.*?)\\\n\s*&& rm -rf /var/lib/apt/lists/\*",
        dockerfile,
        re.DOTALL,
    )
    assert install is not None
    assert "ffmpeg" in install.group("packages").split()


def test_compose_keeps_all_role_and_wires_persistent_podcast_runtime():
    compose = _read("docker-compose.yml")
    assert 'DORAMI_RUNTIME_ROLE: "all"' in compose
    assert "DORAMI_PODCAST_INSTALLATION: ${DORAMI_PODCAST_INSTALLATION:?" in compose
    assert "DORAMI_PODCAST_ALLOWED_STAGES: ${DORAMI_PODCAST_ALLOWED_STAGES-}" in compose
    assert "DORAMI_PODCAST_AUTHORITY_ID: ${DORAMI_PODCAST_AUTHORITY_ID:?" in compose
    assert "DORAMI_PODCAST_ARTIFACT_ROOT_DIR: /app/data/podcast-artifacts" in compose
    assert "DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_MB: ${" in compose
    assert "DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_MB: ${" in compose
    assert "DORAMI_PODCAST_ARTIFACT_STAGING_TTL_SECONDS: ${" in compose
    assert "DORAMI_PODCAST_PROCESSING_ENABLED: ${" in compose
    assert "DORAMI_PODCAST_PROVIDER_READY_TARGETS: ${" in compose
    assert "DORAMI_PODCAST_MONTHLY_BUDGET_CNY_MINOR: ${" in compose
    assert "DORAMI_PODCAST_PER_RUN_BUDGET_CNY_MINOR: ${" in compose
    assert "DORAMI_PODCAST_VOICE_PROFILES: ${" in compose
    for name in (
        "DORAMI_PODCAST_WORKER_TICK_SECONDS",
        "DORAMI_PODCAST_WORKER_LEASE_SECONDS",
        "DORAMI_PODCAST_WORKER_HEARTBEAT_SECONDS",
        "DORAMI_PODCAST_WORKER_FALLBACK_RETRY_SECONDS",
        "DORAMI_PODCAST_WORKER_MAX_STEPS_PER_TICK",
    ):
        assert f"{name}: ${{{name}:-}}" in compose
    for name in (
        "DORAMI_PODCAST_ASR_FETCH_PUBLIC_BASE_URL",
        "DORAMI_PODCAST_ASR_FETCH_SIGNING_SECRET",
        "DORAMI_PODCAST_ASR_FETCH_PREVIOUS_SIGNING_SECRET",
        "DORAMI_PODCAST_ASR_FETCH_URL_TTL_SECONDS",
        "DORAMI_PODCAST_ASR_FETCH_CLOCK_SKEW_SECONDS",
        "DORAMI_PODCAST_ASR_FETCH_MIN_REMAINING_SECONDS",
    ):
        assert f"{name}: ${{{name}:-}}" in compose
    assert "- ./data:/app/data" in compose
    for name in (
        "ALIYUN_AK_ID",
        "ALIYUN_AK_SECRET",
        "ALIYUN_SECURITY_TOKEN",
        "NLS_APP_KEY",
        "NLS_ACCESS_TOKEN",
        "NLS_TOKEN_EXPIRES_AT",
    ):
        assert f"{name}: ${{{name}:-}}" in compose
    for name in (
        "DORAMI_ALIYUN_ISI_REGION_ID",
        "DORAMI_ALIYUN_ISI_ASR_DOMAIN",
        "DORAMI_ALIYUN_ISI_ASR_PRODUCT",
        "DORAMI_ALIYUN_ISI_ASR_API_VERSION",
        "DORAMI_ALIYUN_ISI_ASR_TASK_VERSION",
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS",
        "DORAMI_ALIYUN_ISI_ASR_AUTO_SPLIT",
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_SAMPLE_RATE_ADAPTIVE",
        "DORAMI_ALIYUN_ISI_ASR_POLL_INTERVAL_SECONDS",
        "DORAMI_ALIYUN_ISI_TOKEN_URL",
        "DORAMI_ALIYUN_ISI_TTS_URL",
        "DORAMI_ALIYUN_ISI_TTS_DEVICE_ID",
        "DORAMI_ALIYUN_ISI_TTS_VOICE_PROFILES_JSON",
        "DORAMI_ALIYUN_ISI_TTS_RESULT_ALLOWED_HOST_SUFFIXES",
        "DORAMI_ALIYUN_ISI_TTS_MAX_CHARS",
        "DORAMI_ALIYUN_ISI_TTS_POLL_INTERVAL_SECONDS",
        "DORAMI_ALIYUN_ISI_REQUEST_TIMEOUT_SECONDS",
        "DORAMI_ALIYUN_ISI_TOKEN_REFRESH_SKEW_SECONDS",
        "DORAMI_ALIYUN_ISI_ASR_QUOTA_SCOPE",
        "DORAMI_ALIYUN_ISI_ASR_QUOTA_TIMEZONE",
        "DORAMI_ALIYUN_ISI_ASR_DAILY_AUDIO_SECONDS_LIMIT",
        "DORAMI_ALIYUN_ISI_ASR_ENTITLEMENT_ENDS_AT",
        "DORAMI_ALIYUN_ISI_ASR_PROVIDER_DEADLINE_SECONDS",
        "DORAMI_ALIYUN_ISI_ASR_PRICE_CNY_MINOR_PER_HOUR",
        "DORAMI_ALIYUN_ISI_ASR_PRICING_REVISION",
        "DORAMI_ALIYUN_ISI_TTS_QUOTA_SCOPE",
        "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ID",
        "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_STARTS_AT",
        "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ENDS_AT",
        "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_CHARACTER_LIMIT",
        "DORAMI_ALIYUN_ISI_TTS_PROVIDER_DEADLINE_SECONDS",
        "DORAMI_ALIYUN_ISI_TTS_PRICE_CNY_MINOR_PER_10000_CHARS",
        "DORAMI_ALIYUN_ISI_TTS_PRICING_REVISION",
        "DORAMI_ALIYUN_ISI_TTS_USAGE_SETTLEMENT_MODE",
    ):
        assert f"{name}: ${{{name}:-}}" in compose


def test_production_template_defaults_external_processing_on():
    parser = configparser.ConfigParser()
    parser.read(ROOT / "config/production.example.ini", encoding="utf-8")

    assert parser.get("podcast", "installation") == "external"
    assert not parser.has_option("podcast", "allowed_stages")
    assert not parser.has_option("podcast", "processing_enabled")
    assert not parser.has_option("podcast", "provider_ready_targets")
    assert not parser.has_option("podcast", "monthly_budget_cny_minor")
    assert not parser.has_option("podcast", "per_run_budget_cny_minor")
    assert not parser.has_option("podcast", "voice_profiles")
    assert not parser.has_option("podcast", "default_voice_profile")


def test_baremetal_deploy_requires_audio_tools_and_creates_configured_root():
    deploy = _read("deploy.sh")
    assert "command -v ffmpeg" in deploy
    assert "command -v ffprobe" in deploy
    assert "missing+=(ffmpeg)" in deploy
    assert "ini_get podcast_artifacts root_dir data/podcast-artifacts" in deploy
    assert 'mkdir -p logs data "$PODCAST_ARTIFACT_ROOT"' in deploy
