"""daily-brief / llm 配置端点冒烟测试（阶段1 迁出至 routers/daily_brief.py 后补的接线保护）。

这些端点此前无 TestClient 覆盖；迁出为 Router 后用最小冒烟锁住：admin 放行 + 响应
结构、user 被 collector 网关拒绝、Router 经 _app() 延迟调用 api.app 编排 helper 可用。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _seed_users(engine):
    from sqlmodel import Session
    from models.db import UserRecord
    from services import accounts as accounts_service

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in (("admin", "admin", "admin"), ("user", "user", "user")):
            if session.get(UserRecord, username) is None:
                session.add(UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(password),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                ))
        session.commit()


def _setup(monkeypatch, tmp_path):
    import api.app as app_module
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'db_endpoints.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    return app_module


def _login(client, username="admin", password="admin"):
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200


def test_daily_brief_and_llm_config_readable_by_admin(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)

        cfg = client.get("/api/daily-brief/config")
        assert cfg.status_code == 200
        assert set(cfg.json()) >= {"enabled", "cron", "cursor", "top_n", "last_run"}

        llm = client.get("/api/llm/config")
        assert llm.status_code == 200
        body = llm.json()
        assert "api_key" not in body  # 绝不返回明文
        assert {"base_url", "model", "configured", "api_key_set"} <= set(body)

        assert client.get("/api/daily-brief/progress").status_code == 200


def test_daily_brief_config_rejects_bad_cron(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/daily-brief/config", json={"cron": "bad cron"})
        assert resp.status_code == 400


def test_llm_config_denied_for_reader(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/llm/config").status_code == 403
        assert client.post("/api/daily-brief/config", json={"enabled": True}).status_code == 403
