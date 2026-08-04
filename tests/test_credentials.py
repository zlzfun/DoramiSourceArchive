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
    }
    x = {f.name: f.kv_key for f in credentials.X_API_NAMESPACE.fields}
    assert x == {
        "bearer_token": "x_api_bearer_token",
        "base_url": "x_api_base_url",
        "timeout_seconds": "x_api_timeout_seconds",
        "max_results": "x_api_max_results",
        "monthly_budget_usd": "x_api_monthly_budget_usd",
    }
    assert set(credentials.REGISTRY) == {"llm", "x_api"}
