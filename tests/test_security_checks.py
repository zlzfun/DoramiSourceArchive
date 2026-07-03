"""启动期安全配置校验（阶段4 D10）。

验证 evaluate_security_config 按部署姿态分级：开发姿态（cookie_secure=False）仅告警、
不阻断；生产姿态（cookie_secure=True）关键漏配升级为 error；enforce 在有 error 时抛。
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.security_checks import evaluate_security_config, enforce_security_config  # noqa: E402


def _settings(*, secret="a-long-random-proper-secret", cookie_secure=False,
              origins=("https://app.example.com",), creds=True, disable_ca=False):
    return SimpleNamespace(
        auth=SimpleNamespace(secret=secret, cookie_secure=cookie_secure),
        cors=SimpleNamespace(allow_origins=list(origins), allow_credentials=creds),
        network=SimpleNamespace(disable_ca_bundle=disable_ca),
    )


def test_clean_prod_config_has_no_errors():
    errors, warnings = evaluate_security_config(_settings(cookie_secure=True))
    assert errors == []
    assert warnings == []


def test_dev_posture_only_warns_on_placeholder_and_star_cors():
    s = _settings(secret="change-me-to-a-long-random-string", cookie_secure=False,
                  origins=("*",), creds=True)
    errors, warnings = evaluate_security_config(s)
    assert errors == []            # 开发姿态不阻断
    assert len(warnings) == 2      # secret 占位 + CORS 不安全组合


def test_prod_posture_promotes_placeholder_secret_to_error():
    s = _settings(secret="change-me", cookie_secure=True)
    errors, _ = evaluate_security_config(s)
    assert any("secret" in e for e in errors)


def test_prod_posture_promotes_star_cors_with_credentials_to_error():
    s = _settings(cookie_secure=True, origins=("*",), creds=True)
    errors, _ = evaluate_security_config(s)
    assert any("cors" in e.lower() or "allow_origins" in e for e in errors)


def test_star_cors_without_credentials_is_fine():
    s = _settings(cookie_secure=True, origins=("*",), creds=False)
    errors, _ = evaluate_security_config(s)
    assert errors == []


def test_empty_secret_warns_in_dev_errors_in_prod():
    dev_err, dev_warn = evaluate_security_config(_settings(secret="", cookie_secure=False))
    assert dev_err == [] and any("secret" in w for w in dev_warn)
    prod_err, _ = evaluate_security_config(_settings(secret="", cookie_secure=True))
    assert any("secret" in e for e in prod_err)


def test_disable_ca_bundle_warns_in_prod():
    _, warnings = evaluate_security_config(_settings(cookie_secure=True, disable_ca=True))
    assert any("disable_ca_bundle" in w for w in warnings)


def test_enforce_raises_on_errors_but_passes_on_warnings():
    # 生产姿态占位 secret → error → 抛。
    with pytest.raises(RuntimeError):
        enforce_security_config(_settings(secret="change-me", cookie_secure=True))
    # 开发姿态仅告警 → 不抛。
    enforce_security_config(_settings(secret="change-me", cookie_secure=False, origins=("*",)))
