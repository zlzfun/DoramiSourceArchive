import os
import sys
from dataclasses import replace

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config  # noqa: E402
from services import aliyun_isi_config, credentials  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


ACCOUNTING_ENV = {
    "DORAMI_ALIYUN_ISI_ASR_QUOTA_SCOPE": "asr-trial",
    "DORAMI_ALIYUN_ISI_ASR_QUOTA_TIMEZONE": "Asia/Shanghai",
    "DORAMI_ALIYUN_ISI_ASR_DAILY_AUDIO_SECONDS_LIMIT": "7200",
    "DORAMI_ALIYUN_ISI_ASR_MAX_AUDIO_SECONDS_PER_FILE": "36000",
    "DORAMI_ALIYUN_ISI_ASR_ENTITLEMENT_ENDS_AT": "2026-12-06T00:00:00+08:00",
    "DORAMI_ALIYUN_ISI_ASR_PROVIDER_DEADLINE_SECONDS": "7100",
    "DORAMI_ALIYUN_ISI_ASR_PRICE_CNY_MINOR_PER_HOUR": "125",
    "DORAMI_ALIYUN_ISI_ASR_PRICING_REVISION": "trial-2026-09",
    "DORAMI_ALIYUN_ISI_TTS_QUOTA_SCOPE": "tts-smoke",
    "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ID": "initial-10k",
    "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_STARTS_AT": "2026-09-01T00:00:00+08:00",
    "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ENDS_AT": "2026-10-01T00:00:00+08:00",
    "DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_CHARACTER_LIMIT": "10000",
    "DORAMI_ALIYUN_ISI_TTS_PROVIDER_DEADLINE_SECONDS": "3500",
    "DORAMI_ALIYUN_ISI_TTS_PRICE_CNY_MINOR_PER_10000_CHARS": "300",
    "DORAMI_ALIYUN_ISI_TTS_PRICING_REVISION": "public-2026-09",
    "DORAMI_ALIYUN_ISI_TTS_USAGE_SETTLEMENT_MODE": "submitted_characters",
}


def test_loads_private_credential_environment_and_overridable_protocol(monkeypatch):
    monkeypatch.setenv("ALIYUN_AK_ID", "ak-id")
    monkeypatch.setenv("ALIYUN_AK_SECRET", "ak-secret")
    monkeypatch.setenv("NLS_APP_KEY", "app-key")
    monkeypatch.setenv("NLS_ACCESS_TOKEN", "nls-token")
    monkeypatch.setenv("NLS_TOKEN_EXPIRES_AT", "4102444800")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_ASR_DOMAIN", "asr.example.test")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS", "false")
    monkeypatch.setenv(
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_SAMPLE_RATE_ADAPTIVE", "false"
    )
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_REQUEST_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_TTS_DEVICE_ID", "issue7-test-node")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_TTS_PRODUCT", "long-tts-test")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_TTS_API_VERSION", "rest-v1-test")
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_TTS_MAX_CHARS", "99999")
    monkeypatch.setenv(
        "DORAMI_ALIYUN_ISI_TTS_RESULT_ALLOWED_HOST_SUFFIXES",
        "aliyuncs.com,example.test",
    )

    loaded = config.load_config().aliyun_isi

    assert loaded.access_key_id == "ak-id"
    assert loaded.access_key_secret == "ak-secret"
    assert loaded.app_key == "app-key"
    assert loaded.access_token == "nls-token"
    assert loaded.token_expires_at == 4102444800
    assert loaded.asr_domain == "asr.example.test"
    assert loaded.asr_enable_words is False
    assert loaded.asr_enable_sample_rate_adaptive is False
    assert loaded.request_timeout_seconds == 17
    assert loaded.tts_device_id == "issue7-test-node"
    assert loaded.tts_product == "long-tts-test"
    assert loaded.tts_api_version == "rest-v1-test"
    assert loaded.tts_max_chars == 99999
    assert loaded.tts_result_allowed_host_suffixes == (
        "aliyuncs.com",
        "example.test",
    )
    assert loaded.asr_configured is True
    assert loaded.tts_configured is True


def test_loads_all_provider_accounting_environment_overrides(monkeypatch):
    for name, value in ACCOUNTING_ENV.items():
        monkeypatch.setenv(name, value)

    loaded = config.load_config().aliyun_isi

    assert loaded.asr_quota_scope == "asr-trial"
    assert loaded.asr_quota_timezone == "Asia/Shanghai"
    assert loaded.asr_daily_audio_seconds_limit == 7_200
    assert loaded.asr_max_audio_seconds_per_file == 36_000
    assert loaded.asr_entitlement_ends_at == "2026-12-06T00:00:00+08:00"
    assert loaded.asr_provider_deadline_seconds == 7_100
    assert loaded.asr_price_cny_minor_per_hour == 125
    assert loaded.asr_pricing_revision == "trial-2026-09"
    assert loaded.tts_quota_scope == "tts-smoke"
    assert loaded.tts_campaign_id == "initial-10k"
    assert loaded.tts_campaign_starts_at == "2026-09-01T00:00:00+08:00"
    assert loaded.tts_campaign_ends_at == "2026-10-01T00:00:00+08:00"
    assert loaded.tts_campaign_character_limit == 10_000
    assert loaded.tts_provider_deadline_seconds == 3_500
    assert loaded.tts_price_cny_minor_per_10000_chars == 300
    assert loaded.tts_pricing_revision == "public-2026-09"
    assert loaded.tts_usage_settlement_mode == "submitted_characters"
    assert loaded.asr_accounting_ready is True
    assert loaded.tts_accounting_ready is True


def test_loads_all_provider_accounting_ini_values(tmp_path, monkeypatch):
    for name in ACCOUNTING_ENV:
        monkeypatch.delenv(name, raising=False)
    ini = tmp_path / "accounting.ini"
    ini.write_text(
        """\
[aliyun_isi]
asr_quota_scope = ini-asr-trial
asr_quota_timezone = Asia/Shanghai
asr_daily_audio_seconds_limit = 7199
asr_max_audio_seconds_per_file = 35999
asr_entitlement_ends_at = 2026-12-05T23:59:59+08:00
asr_provider_deadline_seconds = 7000
asr_price_cny_minor_per_hour = 124
asr_pricing_revision = ini-trial
tts_quota_scope = ini-tts-smoke
tts_campaign_id = ini-initial-10k
tts_campaign_starts_at = 2026-09-02T00:00:00+08:00
tts_campaign_ends_at = 2026-09-30T00:00:00+08:00
tts_campaign_character_limit = 9999
tts_provider_deadline_seconds = 3400
tts_price_cny_minor_per_10000_chars = 299
tts_pricing_revision = ini-public
tts_usage_settlement_mode = submitted_characters
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))

    loaded = config.load_config().aliyun_isi

    assert loaded.asr_quota_scope == "ini-asr-trial"
    assert loaded.asr_quota_timezone == "Asia/Shanghai"
    assert loaded.asr_daily_audio_seconds_limit == 7_199
    assert loaded.asr_max_audio_seconds_per_file == 35_999
    assert loaded.asr_entitlement_ends_at == "2026-12-05T23:59:59+08:00"
    assert loaded.asr_provider_deadline_seconds == 7_000
    assert loaded.asr_price_cny_minor_per_hour == 124
    assert loaded.asr_pricing_revision == "ini-trial"
    assert loaded.tts_quota_scope == "ini-tts-smoke"
    assert loaded.tts_campaign_id == "ini-initial-10k"
    assert loaded.tts_campaign_starts_at == "2026-09-02T00:00:00+08:00"
    assert loaded.tts_campaign_ends_at == "2026-09-30T00:00:00+08:00"
    assert loaded.tts_campaign_character_limit == 9_999
    assert loaded.tts_provider_deadline_seconds == 3_400
    assert loaded.tts_price_cny_minor_per_10000_chars == 299
    assert loaded.tts_pricing_revision == "ini-public"
    assert loaded.tts_usage_settlement_mode == "submitted_characters"
    assert loaded.asr_accounting_ready is True
    assert loaded.tts_accounting_ready is True


def test_rejects_insecure_endpoint_and_invalid_intervals():
    with pytest.raises(ValueError, match="HTTPS"):
        config.AliyunIsiConfig(token_url="http://token.example.test/")
    with pytest.raises(ValueError, match="HTTPS"):
        config.AliyunIsiConfig(token_url="https://token.example.test/not-root")
    with pytest.raises(ValueError, match="hostname"):
        config.AliyunIsiConfig(asr_domain="https://asr.example.test")
    with pytest.raises(ValueError, match="positive"):
        config.AliyunIsiConfig(asr_poll_interval_seconds=0)
    with pytest.raises(ValueError, match="boolean"):
        config.AliyunIsiConfig(asr_enable_words="true")
    with pytest.raises(ValueError, match="boolean"):
        config.AliyunIsiConfig(asr_enable_sample_rate_adaptive="true")
    with pytest.raises(ValueError, match="version 4.0"):
        config.AliyunIsiConfig(asr_task_version="2.0")
    with pytest.raises(ValueError, match="tts_device_id"):
        config.AliyunIsiConfig(tts_device_id="contains spaces")
    with pytest.raises(ValueError, match="tts_max_chars"):
        config.AliyunIsiConfig(tts_max_chars=100001)
    with pytest.raises(ValueError, match="asr_max_audio_seconds_per_file"):
        config.AliyunIsiConfig(asr_max_audio_seconds_per_file=0)
    with pytest.raises(ValueError, match="asr_max_audio_seconds_per_file"):
        config.AliyunIsiConfig(asr_max_audio_seconds_per_file=43_201)
    with pytest.raises(ValueError, match="host suffixes"):
        config.AliyunIsiConfig(tts_result_allowed_host_suffixes=("bad_suffix",))
    with pytest.raises(ValueError, match="host suffixes"):
        config.AliyunIsiConfig(tts_result_allowed_host_suffixes=("com",))
    with pytest.raises(ValueError, match="host suffixes"):
        config.AliyunIsiConfig(tts_result_allowed_host_suffixes="aliyuncs.com")
    with pytest.raises(ValueError, match="tts_usage_settlement_mode"):
        config.AliyunIsiConfig(tts_usage_settlement_mode="provider_actual")


def test_tts_usage_settlement_defaults_to_manual():
    assert config.AliyunIsiConfig().tts_usage_settlement_mode == "manual"


def test_asr_single_audio_limit_defaults_to_twelve_hours():
    assert config.AliyunIsiConfig().asr_max_audio_seconds_per_file == 12 * 60 * 60


def test_compose_empty_asr_switches_keep_enabled_defaults(monkeypatch):
    for name in (
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS",
        "DORAMI_ALIYUN_ISI_ASR_AUTO_SPLIT",
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_SAMPLE_RATE_ADAPTIVE",
    ):
        monkeypatch.setenv(name, "")

    loaded = config.load_config().aliyun_isi

    assert loaded.asr_enable_words is True
    assert loaded.asr_auto_split is True
    assert loaded.asr_enable_sample_rate_adaptive is True


def test_invalid_nonempty_asr_switch_fails_closed(monkeypatch):
    monkeypatch.setenv("DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS", "treu")
    with pytest.raises(ValueError, match="DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS"):
        config.load_config()


def test_tts_readiness_rejects_expired_token_without_refresh_credentials():
    expired = config.AliyunIsiConfig(
        app_key="app-key",
        access_token="expired-token",
        token_expires_at=100,
    )
    refreshable = config.AliyunIsiConfig(
        access_key_id="ak-id",
        access_key_secret="ak-secret",
        app_key="app-key",
        access_token="expired-token",
        token_expires_at=100,
    )

    assert expired.tts_configured is False
    assert refreshable.tts_configured is True


def test_aliyun_config_repr_never_exposes_credentials():
    secrets = {
        "access_key_id": "repr-ak-id",
        "access_key_secret": "repr-ak-secret",
        "security_token": "repr-sts-token",
        "app_key": "repr-app-key",
        "access_token": "repr-access-token",
    }
    rendered = repr(config.AliyunIsiConfig(**secrets))
    assert all(secret not in rendered for secret in secrets.values())


def test_registry_runtime_override_resolves_without_exposing_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config,
        "settings",
        replace(
            config.settings,
            aliyun_isi=config.AliyunIsiConfig(
                access_key_id="baseline-id",
                access_key_secret="baseline-secret",
                app_key="baseline-app",
            ),
        ),
    )
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'aliyun-config.db'}")
    with Session(sink.engine) as session:
        credentials.save_updates(
            session,
            credentials.ALIYUN_ISI_NAMESPACE,
            {
                "app_key": "runtime-app",
                "request_timeout_seconds": 19,
                "asr_auto_split": "false",
                "tts_product": "runtime-long-tts",
                "tts_api_version": "runtime-rest-v1",
                "tts_result_allowed_host_suffixes": "aliyuncs.com,example.test",
            },
        )
        resolved = aliyun_isi_config.resolve_config(session)
        sanitized = credentials.sanitize_values(
            credentials.ALIYUN_ISI_NAMESPACE,
            resolved.__dict__,
        )

    assert resolved.app_key == "runtime-app"
    assert resolved.access_key_secret == "baseline-secret"
    assert resolved.request_timeout_seconds == 19
    assert resolved.asr_auto_split is False
    assert resolved.tts_product == "runtime-long-tts"
    assert resolved.tts_api_version == "runtime-rest-v1"
    assert resolved.tts_result_allowed_host_suffixes == (
        "aliyuncs.com",
        "example.test",
    )
    assert "runtime-app" not in str(sanitized)
    assert "baseline-secret" not in str(sanitized)


def test_refreshed_token_and_expiry_persist_as_one_runtime_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config,
        "settings",
        replace(config.settings, aliyun_isi=config.AliyunIsiConfig()),
    )
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'aliyun-token.db'}")
    with Session(sink.engine) as session:
        aliyun_isi_config.persist_refreshed_token(
            session,
            access_token="fresh-token",
            expires_at=5000,
        )
        resolved = aliyun_isi_config.resolve_config(session)

    assert resolved.access_token == "fresh-token"
    assert resolved.token_expires_at == 5000


def test_refreshed_token_pair_rejects_partial_values(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'aliyun-token-invalid.db'}")
    with Session(sink.engine) as session:
        with pytest.raises(ValueError, match="token and expiry"):
            aliyun_isi_config.persist_refreshed_token(
                session,
                access_token="",
                expires_at=5000,
            )
