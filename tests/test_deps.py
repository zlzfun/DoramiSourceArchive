"""阶段1 DI/权限脚手架（api/deps.py）单测。

重点验证：依赖提供者动态读取 api.app 单例（兼容 monkeypatch），以及
require_admin/reader/collector 的放行/拒绝语义与 HTTP 状态码。
"""

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _req():
    # require_* 只把 request 透传给 current_auth_session（已被 monkeypatch），不读取其属性。
    return object()


def test_get_db_sink_reads_dynamically(monkeypatch):
    import api.app as app_module
    from api import deps

    sentinel = object()
    monkeypatch.setattr(app_module, "db_sink", sentinel)
    assert deps.get_db_sink() is sentinel


def test_get_vector_sink_raises_503_when_disabled(monkeypatch):
    import api.app as app_module
    from api import deps

    monkeypatch.setattr(app_module, "vector_sink", None)
    with pytest.raises(HTTPException) as exc:
        deps.get_vector_sink()
    assert exc.value.status_code == 503


def test_get_vector_sink_returns_sink_when_enabled(monkeypatch):
    import api.app as app_module
    from api import deps

    sentinel = object()
    monkeypatch.setattr(app_module, "vector_sink", sentinel)
    assert deps.get_vector_sink() is sentinel


def test_require_login_rejects_anonymous(monkeypatch):
    import api.app as app_module
    from api import deps

    monkeypatch.setattr(app_module, "current_auth_session", lambda request: None)
    with pytest.raises(HTTPException) as exc:
        deps.require_login(_req())
    assert exc.value.status_code == 401


def test_require_admin_allows_admin_rejects_user(monkeypatch):
    import api.app as app_module
    from api import deps

    monkeypatch.setattr(app_module, "current_auth_session", lambda request: {"sub": "a", "role": "admin"})
    assert deps.require_admin(_req())["role"] == "admin"

    monkeypatch.setattr(app_module, "current_auth_session", lambda request: {"sub": "u", "role": "user"})
    with pytest.raises(HTTPException) as exc:
        deps.require_admin(_req())
    assert exc.value.status_code == 403


def test_require_collector_and_reader_follow_role_predicates(monkeypatch):
    import api.app as app_module
    from api import deps

    # admin：采集+读者通吃
    monkeypatch.setattr(app_module, "current_auth_session", lambda request: {"sub": "a", "role": "admin"})
    assert deps.require_collector(_req())["role"] == "admin"
    assert deps.require_reader(_req())["role"] == "admin"

    # user：仅读者面；采集面应 403
    monkeypatch.setattr(app_module, "current_auth_session", lambda request: {"sub": "u", "role": "user"})
    assert deps.require_reader(_req())["role"] == "user"
    with pytest.raises(HTTPException) as exc:
        deps.require_collector(_req())
    assert exc.value.status_code == 403


def test_access_policy_from_session(monkeypatch):
    import api.app as app_module
    from api import deps

    policy = deps.AccessPolicy.from_session({"sub": "a", "role": "admin"})
    assert policy.account_role == "admin"
    assert policy.collector_enabled is True
    assert policy.reader_enabled is True

    user_policy = deps.AccessPolicy.from_session({"sub": "u", "role": "user"})
    assert user_policy.collector_enabled is False
    assert user_policy.reader_enabled is True
