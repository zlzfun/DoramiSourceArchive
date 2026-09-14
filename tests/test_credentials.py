"""外部凭据统一保管层(services/credentials)契约测试。

覆盖:解析序(KV > 基线)与数值坏值回落、field_sources 四来源标注、
save_updates 的只写不回显写入半边(secret 空=保留/普通空=清覆盖)、
mask_tail 脱敏、sanitize_values 响应形状、blob 形态 apply_secret_update、
注册表 KV key 与历史存量一致(零迁移守卫)。
"""

import os
import sys

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import AppSettingRecord  # noqa: E402
from services import credentials  # noqa: E402


@pytest.fixture()
def session(tmp_path):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'credentials.db'}")
    with Session(sink.engine) as s:
        yield s


class _Baseline:
    base_url = "https://ini.example.com"
    api_key = "ini-key"
    model = "ini-model"
    temperature = 0.3
    max_tokens = 4096
    thinking_mode = ""
    aux_model = ""


NS = credentials.LLM_NAMESPACE


def test_resolve_prefers_kv_and_falls_back_to_baseline(session):
    credentials.set_setting(session, "llm_model", "kv-model")
    credentials.set_setting(session, "llm_max_tokens", "8192")
    values = credentials.resolve_values(session, NS, _Baseline())
    assert values["model"] == "kv-model"
    assert values["max_tokens"] == 8192
    assert values["base_url"] == "https://ini.example.com"  # 无 KV 覆盖回落基线
    assert values["api_key"] == "ini-key"


def test_resolve_bad_numeric_kv_falls_back(session):
    credentials.set_setting(session, "llm_temperature", "not-a-number")
    values = credentials.resolve_values(session, NS, _Baseline())
    assert values["temperature"] == pytest.approx(0.3)


def test_field_sources_marks_runtime_kv_and_env(session, monkeypatch):
    credentials.set_setting(session, "llm_model", "kv-model")
    monkeypatch.setenv("DORAMI_LLM_API_KEY", "env-key")
    sources = credentials.field_sources(session, NS)
    assert sources["model"] == "runtime_kv"
    assert sources["api_key"] == "env"
    # 未覆盖字段只可能是 ini 或 default(取决于本机 ini 是否写了该项)
    assert sources["base_url"] in ("ini", "default")
    assert credentials.overall_source(sources) == "runtime_kv"


def test_save_updates_secret_empty_keeps_existing(session):
    credentials.save_updates(session, NS, {"api_key": "sk-first", "model": "m1"})
    assert credentials.get_setting(session, "llm_api_key") == "sk-first"
    # secret 空串/None = 保留;普通字段空串 = 清除 KV 覆盖
    credentials.save_updates(session, NS, {"api_key": "", "model": ""})
    assert credentials.get_setting(session, "llm_api_key") == "sk-first"
    assert credentials.get_setting(session, "llm_model") == ""
    credentials.save_updates(session, NS, {"api_key": None})
    assert credentials.get_setting(session, "llm_api_key") == "sk-first"
    # 未提及的字段不动
    credentials.save_updates(session, NS, {"model": "m2"})
    assert credentials.get_setting(session, "llm_api_key") == "sk-first"


def test_save_updates_all_noop_values_do_not_commit_caller_state(session):
    pending = AppSettingRecord(key="caller-pending", value="not-committed")
    session.add(pending)
    credentials.save_updates(
        session,
        NS,
        {"api_key": "", "model": None},
    )
    assert pending in session.new


def test_clear_secret_fields_is_explicitly_allowlisted(session):
    with pytest.raises(ValueError, match="not clearable"):
        credentials.clear_secret_fields(
            session, credentials.LLM_NAMESPACE, ("api_key",)
        )


@pytest.mark.parametrize(
    "value",
    [
        ("aliyuncs.com", "example.test"),
        ["aliyuncs.com", "example.test"],
    ],
)
def test_save_updates_csv_round_trips_as_canonical_text(session, value):
    ns = credentials.ALIYUN_ISI_NAMESPACE
    credentials.save_updates(
        session,
        ns,
        {"tts_result_allowed_host_suffixes": value},
    )

    assert credentials.get_setting(
        session, "aliyun_isi_tts_result_allowed_host_suffixes"
    ) == "aliyuncs.com,example.test"
    resolved = credentials.resolve_values(
        session,
        ns,
        credentials.config.AliyunIsiConfig(),
    )
    assert resolved["tts_result_allowed_host_suffixes"] == (
        "aliyuncs.com",
        "example.test",
    )


def test_mask_tail_shapes():
    assert credentials.mask_tail("") == ""
    assert credentials.mask_tail("abcd") == "****"
    assert credentials.mask_tail("secret-9876") == "••••9876"


def test_sanitize_values_never_returns_secret():
    values = {"base_url": "https://x", "api_key": "sk-secret-9876", "model": "m",
              "temperature": 0.3, "max_tokens": 4096}
    out = credentials.sanitize_values(NS, values)
    assert out["api_key_set"] is True
    assert out["api_key_preview"] == "••••9876"
    assert "api_key" not in out
    assert out["base_url"] == "https://x"
    flat = str(out)
    assert "sk-secret-9876" not in flat


def test_apply_secret_update_blob_semantics():
    target = {"password": "old"}
    credentials.apply_secret_update(target, {"password": ""}, "password")
    assert target["password"] == "old"
    credentials.apply_secret_update(target, {}, "password")
    assert target["password"] == "old"
    credentials.apply_secret_update(target, {"password": "new"}, "password")
    assert target["password"] == "new"


def test_registry_kv_keys_match_legacy_storage():
    """零迁移守卫:注册表 KV key 必须与历史存量 key 逐字一致。"""
    llm = {f.name: f.kv_key for f in credentials.LLM_NAMESPACE.fields}
    assert llm == {
        "base_url": "llm_base_url",
        "api_key": "llm_api_key",
        "model": "llm_model",
        "temperature": "llm_temperature",
        "max_tokens": "llm_max_tokens",
        "thinking_mode": "llm_thinking_mode",
        "aux_model": "llm_aux_model",
    }
    x = {f.name: f.kv_key for f in credentials.X_API_NAMESPACE.fields}
    assert x == {
        "bearer_token": "x_api_bearer_token",
        "base_url": "x_api_base_url",
        "timeout_seconds": "x_api_timeout_seconds",
        "max_results": "x_api_max_results",
        "monthly_budget_usd": "x_api_monthly_budget_usd",
    }
    aliyun = {f.name: f.kv_key for f in credentials.ALIYUN_ISI_NAMESPACE.fields}
    assert aliyun == {
        "access_key_id": "aliyun_isi_access_key_id",
        "access_key_secret": "aliyun_isi_access_key_secret",
        "security_token": "aliyun_isi_security_token",
        "app_key": "aliyun_isi_app_key",
        "access_token": "aliyun_isi_access_token",
        "token_expires_at": "aliyun_isi_token_expires_at",
        "region_id": "aliyun_isi_region_id",
        "asr_domain": "aliyun_isi_asr_domain",
        "asr_product": "aliyun_isi_asr_product",
        "asr_api_version": "aliyun_isi_asr_api_version",
        "asr_task_version": "aliyun_isi_asr_task_version",
        "asr_enable_words": "aliyun_isi_asr_enable_words",
        "asr_auto_split": "aliyun_isi_asr_auto_split",
        "asr_enable_sample_rate_adaptive": "aliyun_isi_asr_enable_sample_rate_adaptive",
        "token_url": "aliyun_isi_token_url",
        "tts_url": "aliyun_isi_tts_url",
        "tts_product": "aliyun_isi_tts_product",
        "tts_api_version": "aliyun_isi_tts_api_version",
        "tts_device_id": "aliyun_isi_tts_device_id",
        "tts_voice_profiles_json": "aliyun_isi_tts_voice_profiles_json",
        "tts_result_allowed_host_suffixes": "aliyun_isi_tts_result_allowed_host_suffixes",
        "tts_max_chars": "aliyun_isi_tts_max_chars",
        "request_timeout_seconds": "aliyun_isi_request_timeout_seconds",
        "asr_poll_interval_seconds": "aliyun_isi_asr_poll_interval_seconds",
        "tts_poll_interval_seconds": "aliyun_isi_tts_poll_interval_seconds",
        "token_refresh_skew_seconds": "aliyun_isi_token_refresh_skew_seconds",
        "asr_quota_scope": "aliyun_isi_asr_quota_scope",
        "asr_quota_timezone": "aliyun_isi_asr_quota_timezone",
        "asr_daily_audio_seconds_limit": "aliyun_isi_asr_daily_audio_seconds_limit",
        "asr_max_audio_seconds_per_file": "aliyun_isi_asr_max_audio_seconds_per_file",
        "asr_entitlement_ends_at": "aliyun_isi_asr_entitlement_ends_at",
        "asr_provider_deadline_seconds": "aliyun_isi_asr_provider_deadline_seconds",
        "asr_price_cny_minor_per_hour": "aliyun_isi_asr_price_cny_minor_per_hour",
        "asr_pricing_revision": "aliyun_isi_asr_pricing_revision",
        "tts_quota_scope": "aliyun_isi_tts_quota_scope",
        "tts_campaign_id": "aliyun_isi_tts_campaign_id",
        "tts_campaign_starts_at": "aliyun_isi_tts_campaign_starts_at",
        "tts_campaign_ends_at": "aliyun_isi_tts_campaign_ends_at",
        "tts_campaign_character_limit": "aliyun_isi_tts_campaign_character_limit",
        "tts_provider_deadline_seconds": "aliyun_isi_tts_provider_deadline_seconds",
        "tts_price_cny_minor_per_10000_chars": "aliyun_isi_tts_price_cny_minor_per_10000_chars",
        "tts_pricing_revision": "aliyun_isi_tts_pricing_revision",
        "tts_usage_settlement_mode": "aliyun_isi_tts_usage_settlement_mode",
    }
    assert set(credentials.REGISTRY) == {
        "llm",
        "x_api",
        "aliyun_isi",
    }


def test_aliyun_isi_secrets_are_sanitized():
    ns = credentials.ALIYUN_ISI_NAMESPACE
    values = {
        "access_key_id": "test-ak-id",
        "access_key_secret": "test-ak-secret",
        "security_token": "test-sts-token",
        "app_key": "test-app-key",
        "access_token": "test-nls-token",
        "token_expires_at": 123,
        "region_id": "cn-shanghai",
    }
    out = credentials.sanitize_values(ns, values)
    for key in (
        "access_key_id",
        "access_key_secret",
        "security_token",
        "app_key",
        "access_token",
    ):
        assert key not in out
        assert out[f"{key}_set"] is True
    assert out["token_expires_at"] == 123
    assert not any(
        values[key] in str(out)
        for key in (
            "access_key_id",
            "access_key_secret",
            "security_token",
            "app_key",
            "access_token",
        )
    )
