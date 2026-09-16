"""`GET /api/health` 部署探针(issue #102 自动部署,PR-1)。

流水线没有账号,`/api/runtime` 匿名 401;本端点是鉴权中间件里与 `/api/auth/session` 同级的
**exact-path** 白名单——匿名 200、三种 runtime role 都 200、坏 cookie 仍 200、只回三个字段、
相邻伪路径照旧 401、`Cache-Control: no-store`。
"""

import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from version import __version__  # noqa: E402


def _client(monkeypatch, tmp_path, role: str = "all") -> TestClient:
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role=role))
    )
    monkeypatch.setattr(app_module, "db_sink", DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'health.db'}"))
    return TestClient(app_module.app)


@pytest.mark.parametrize("role", ["all", "collector", "reader"])
def test_health_is_anonymous_and_independent_of_runtime_role(monkeypatch, tmp_path, role):
    client = _client(monkeypatch, tmp_path, role)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "build"}, "只透出三项,不带能力位/账号/配置"
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert set(body["build"]) == {"ref", "sha", "source"}
    assert response.headers["cache-control"] == "no-store"


def test_health_ignores_invalid_session_cookie(monkeypatch, tmp_path):
    import api.app as app_module

    client = _client(monkeypatch, tmp_path)
    client.cookies.set(app_module.AUTH_COOKIE_NAME, "not-a-valid-session")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/api/healthz", "/api/health/x", "/api/health/"])
def test_health_whitelist_is_exact_not_prefix(monkeypatch, tmp_path, path):
    client = _client(monkeypatch, tmp_path)
    response = client.get(path)
    assert response.status_code == 401, f"{path} 不该被白名单放行"


def test_runtime_still_requires_login(monkeypatch, tmp_path):
    """对照:能力位端点仍然要登录——健康探针没有顺手把它放开。"""
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/runtime").status_code == 401
