"""X API 运维配置、低成本探针与配额目录契约。"""

import datetime
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _seed_users(engine):
    from models.db import UserRecord
    from services import accounts as accounts_service

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in (
            ("admin", "admin", "admin"),
            ("user", "user", "user"),
        ):
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(password),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()


def _setup(monkeypatch, tmp_path, name="x-api.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    _seed_users(sink.engine)
    return app_module, sink


def _login(client, username="admin", password="admin"):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def test_x_api_config_roundtrip_masks_secret_and_validates_before_write(
    monkeypatch, tmp_path
):
    from models.db import AppSettingRecord
    from services.x_api_config import KEY_BEARER_TOKEN

    app_module, sink = _setup(monkeypatch, tmp_path)
    secret = "runtime-bearer-secret-9876"
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/x-api/config",
            json={
                "bearer_token": secret,
                "base_url": "https://api.example.test/2/",
                "timeout_seconds": 17,
                "max_results": 25,
                "monthly_budget_usd": 3.5,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "bearer_token" not in body
        assert secret not in response.text
        assert body["configured"] is True
        assert body["bearer_token_set"] is True
        assert body["bearer_token_preview"] == "••••9876"
        assert body["base_url"] == "https://api.example.test/2"
        assert body["timeout_seconds"] == 17
        assert body["max_results"] == 25
        assert body["monthly_budget_usd"] == 3.5
        assert body["source"] == "runtime_kv"
        assert set(body["field_sources"].values()) == {"runtime_kv"}

        fetched = client.get("/api/x-api/config")
        assert fetched.status_code == 200
        assert secret not in fetched.text
        assert fetched.json()["bearer_token_preview"] == "••••9876"

        # 整份 payload 先校验：后置非法字段不能造成 token 部分写入。
        invalid = client.post(
            "/api/x-api/config",
            json={"bearer_token": "must-not-be-written", "max_results": 101},
        )
        assert invalid.status_code == 400

    with Session(sink.engine) as session:
        assert session.get(AppSettingRecord, KEY_BEARER_TOKEN).value == secret

    # DataPipeline 注入 engine 后，通用/preset fetcher 都读取同一套运行时覆盖。
    from fetchers.impl.x_timeline_fetcher import XTimelineFetcher

    fetcher = XTimelineFetcher()
    fetcher.bind_runtime_engine(sink.engine)
    assert fetcher.x_config.bearer_token == secret
    assert fetcher.x_config.base_url == "https://api.example.test/2"
    assert fetcher.x_config.timeout_seconds == 17
    assert fetcher.x_config.max_results == 25
    assert fetcher.x_config.monthly_budget_usd == 3.5


def test_x_api_endpoints_are_admin_only(monkeypatch, tmp_path):
    app_module, _ = _setup(monkeypatch, tmp_path, "auth.db")
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/x-api/config").status_code == 403
        assert client.post("/api/x-api/config", json={}).status_code == 403
        assert client.post("/api/x-api/config/test").status_code == 403
        assert client.get("/api/x-api/quota").status_code == 403


def test_x_api_probe_reports_first_user_cost_then_daily_dedup(monkeypatch, tmp_path):
    import api.routers.x_api as x_api_router

    app_module, _ = _setup(monkeypatch, tmp_path, "probe.db")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer probe-secret-1234"
        assert request.url.path.startswith("/2/users/")
        user_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": user_id,
                    "name": "Probe User",
                    "username": "probe_user",
                    "profile_image_url": "https://img.example/probe_normal.jpg",
                }
            },
        )

    transport = httpx.MockTransport(handler)

    def async_client_factory(*args, **kwargs):
        return httpx.AsyncClient(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        x_api_router,
        "httpx",
        SimpleNamespace(AsyncClient=async_client_factory, HTTPError=httpx.HTTPError),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        configured = client.post(
            "/api/x-api/config",
            json={
                "bearer_token": "probe-secret-1234",
                "base_url": "https://api.example.test/2",
                "monthly_budget_usd": 1,
            },
        )
        assert configured.status_code == 200

        first = client.post("/api/x-api/config/test")
        assert first.status_code == 200, first.text
        assert first.json()["estimated_cost_usd"] == 0.01
        assert first.json()["deduplicated_today"] is False

        second = client.post("/api/x-api/config/test")
        assert second.status_code == 200, second.text
        assert second.json()["estimated_cost_usd"] == 0
        assert second.json()["deduplicated_today"] is True

        quota = client.get("/api/x-api/quota")
        assert quota.status_code == 200
        usage = quota.json()
        assert usage["user_reads"] == 1
        assert usage["estimated_cost_usd"] == 0.01
        source_id = first.json()["source_id"]
        assert usage["by_source"][source_id]["users"] == 1
        assert usage["by_source"][source_id]["estimated_cost_usd"] == 0.01

        sources = {
            item["source_id"]: item
            for item in client.get("/api/reader/sources").json()["sources"]
        }
        assert sources[source_id]["avatar_url"] == (
            "https://img.example/probe_400x400.jpg"
        )
        assert sources[source_id]["avatar_url_original"] == (
            "https://img.example/probe_normal.jpg"
        )

    assert len(requests) == 2


def test_x_api_probe_prefers_already_seen_post_for_zero_increment(monkeypatch, tmp_path):
    import api.routers.x_api as x_api_router
    from models.db import SourceStateRecord
    from services.x_api_quota import XApiQuotaGuard

    app_module, sink = _setup(monkeypatch, tmp_path, "post-probe.db")
    post_id = "2012345678901234567"
    with Session(sink.engine) as session:
        session.add(
            SourceStateRecord(
                source_id="x_openai",
                fetcher_id="x_openai",
                last_cursor_value=post_id,
                created_at="2026-07-20T00:00:00",
                updated_at="2026-07-20T00:00:00",
            )
        )
        session.commit()
    # 模拟同一 UTC 日刚完成过采集：重复探针仍请求 X 验证 token，但本地费用增量为 0。
    XApiQuotaGuard(sink.engine, monthly_budget_usd=1).record_response(
        {"data": {"id": post_id}},
        primary_resource="post",
        source_id="x_openai",
    )

    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer post-probe-secret"
        return httpx.Response(200, json={"data": {"id": post_id, "text": "probe"}})

    transport = httpx.MockTransport(handler)

    def async_client_factory(*args, **kwargs):
        return httpx.AsyncClient(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        x_api_router,
        "httpx",
        SimpleNamespace(AsyncClient=async_client_factory, HTTPError=httpx.HTTPError),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post(
            "/api/x-api/config", json={"bearer_token": "post-probe-secret"}
        ).status_code == 200
        response = client.post("/api/x-api/config/test")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["probe"] == "cached_post_lookup"
        assert body["resource_type"] == "post"
        assert body["resource_id"] == post_id
        assert body["estimated_cost_usd"] == 0
        assert body["deduplicated_today"] is True

    assert paths == [f"/2/tweets/{post_id}"]
