"""远程内容同步(v3.18 互通波)测试:假远端全程 httpx.MockTransport,不打真网。

覆盖:探针(版本/契约/总量、错误凭据、非 admin 拒绝)、分页拉取与真实导入的
端到端(翻页、幂等、增量游标、checksum 坏行计数)、Secure cookie 显式头回传
(假远端 Set-Cookie 带 Secure 而 base_url 为 http,cookiejar 必不发,只有显式
Cookie 头能通过——机制性验证)、端点门控与 job payload 不含密码。
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services import remote_sync as remote_sync_service  # noqa: E402
from services.remote_sync import RemoteSyncError  # noqa: E402


# ==================== 假远端 ====================

_SESSION_COOKIE = "dorami_session=remote-secret-token"


def _article(i, fetched_date, source_id="rss_demo"):
    return {
        "id": f"art_{i}",
        "title": f"文章 {i}",
        "content_type": "rss_article",
        "source_id": source_id,
        "source_url": f"https://example.test/{i}",
        "publish_date": fetched_date,
        "fetched_date": fetched_date,
        "fetch_run_id": None,
        "job_id": None,
        "job_run_id": None,
        "source_group_id": None,
        "run_scope": "ad_hoc",
        "has_content": True,
        "content": f"正文 {i}",
        "extensions": {},
    }


class FakeRemote:
    """模拟发送方后端:登录 + runtime + 文章总量 + 归档导出(遵守契约与分页)。

    Set-Cookie 刻意带 Secure:base_url 走 http 时 httpx cookiejar 绝不会回发,
    只有服务代码手工抽 Set-Cookie 拼显式 Cookie 头才能通过后续请求——以此验证
    对「远端 cookie_secure=true」场景的兼容。
    """

    def __init__(self, articles, *, username="admin", password="secret", role="admin"):
        self.articles = list(articles)
        self.username = username
        self.password = password
        self.role = role
        self.export_requests = []

    def transport(self):
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if path == "/api/auth/login":
            body = json.loads(request.content.decode("utf-8"))
            if body.get("username") != self.username or body.get("password") != self.password:
                return httpx.Response(401, json={"detail": "账号或密码错误"})
            return httpx.Response(
                200,
                json={"authenticated": True, "user": {"username": self.username, "role": self.role}},
                headers={"set-cookie": f"{_SESSION_COOKIE}; Path=/; HttpOnly; Secure; SameSite=Lax"},
            )
        # 其余端点一律要求显式 Cookie 头带回会话。
        if _SESSION_COOKIE not in (request.headers.get("cookie") or ""):
            return httpx.Response(401, json={"detail": "未登录"})
        if path == "/api/runtime":
            return httpx.Response(200, json={"version": "9.9.9-test"})
        if path == "/api/articles":
            return httpx.Response(200, json={"items": [], "total": len(self.articles)})
        if path == "/api/archive/export/articles.jsonl":
            return self._export(request)
        return httpx.Response(404, json={"detail": "not found"})

    def _export(self, request: httpx.Request) -> httpx.Response:
        from api.routers.archive_sync import (
            ARCHIVE_SYNC_SCHEMA_VERSION,
            archive_article_checksum,
        )

        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        self.export_requests.append(params)
        skip = int(params.get("skip", 0))
        limit = int(params.get("limit", 1000))
        start = params.get("fetched_date_start")
        source_ids = set((params.get("source_ids") or "").split(",")) - {""}

        selected = [a for a in self.articles if not start or a["fetched_date"] >= start]
        if source_ids:
            selected = [a for a in selected if a["source_id"] in source_ids]
        selected.sort(key=lambda a: (a["fetched_date"], a["id"]))
        page = selected[skip:skip + limit]

        lines = [json.dumps({
            "kind": "manifest", "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
            "generated_at": "2026-07-23T00:00:00", "content": "articles",
            "count": len(page), "filters": params,
        }, ensure_ascii=False)]
        for article in page:
            checksum = article.pop("_bad_checksum", None) or archive_article_checksum(article)
            lines.append(json.dumps({
                "kind": "article", "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
                "checksum": checksum, "article": article,
            }, ensure_ascii=False))
        return httpx.Response(
            200, content="\n".join(lines) + "\n",
            headers={"content-type": "application/x-ndjson; charset=utf-8"},
        )


# ==================== 探针 ====================

def test_probe_success():
    remote = FakeRemote([_article(1, "2026-07-01T10:00:00")])
    result = asyncio.run(remote_sync_service.probe(
        "http://remote.test/", "admin", "secret", transport=remote.transport(),
    ))
    assert result["ok"] is True
    assert result["base_url"] == "http://remote.test"
    assert result["version"] == "9.9.9-test"
    assert result["schema_version"] == "articles-jsonl-v1"
    assert result["article_total"] == 1


def test_probe_rejects_bad_credentials_and_non_admin():
    remote = FakeRemote([])
    with pytest.raises(RemoteSyncError, match="账号或密码错误"):
        asyncio.run(remote_sync_service.probe(
            "http://remote.test", "admin", "wrong", transport=remote.transport(),
        ))
    reader_remote = FakeRemote([], role="user")
    with pytest.raises(RemoteSyncError, match="管理员"):
        asyncio.run(remote_sync_service.probe(
            "http://remote.test", "admin", "secret", transport=reader_remote.transport(),
        ))


def test_normalize_base_url_rejects_garbage():
    with pytest.raises(RemoteSyncError):
        remote_sync_service.normalize_base_url("ftp://x")
    with pytest.raises(RemoteSyncError):
        remote_sync_service.normalize_base_url("not a url")
    assert remote_sync_service.normalize_base_url(" http://a.b:8088/ ") == "http://a.b:8088"


# ==================== 拉取 + 真实导入端到端 ====================

def _make_local_sink(tmp_path, monkeypatch, name):
    import api.app as app_module
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    return sink


def test_run_pull_paginates_imports_and_increments(monkeypatch, tmp_path):
    from api.routers.archive_sync import import_archive_sync_jsonl
    from models.db import ArticleRecord
    from sqlmodel import Session, select

    sink = _make_local_sink(tmp_path, monkeypatch, "pull.db")
    articles = [_article(i, f"2026-07-0{i}T10:00:00") for i in range(1, 6)]
    remote = FakeRemote(articles)

    advanced = []
    result = asyncio.run(remote_sync_service.run_pull(
        base_url="http://remote.test",
        username="admin", password="secret",
        page_size=2,
        import_fn=import_archive_sync_jsonl,
        on_advance=advanced.append,
        transport=remote.transport(),
    ))
    assert result["pages"] == 3
    assert result["pulled"] == 5
    assert result["imported"] == 5
    assert result["errors"] == 0
    assert result["max_fetched_date"] == "2026-07-05T10:00:00"
    assert sum(advanced) == 5
    with Session(sink.engine) as session:
        assert len(session.exec(select(ArticleRecord)).all()) == 5

    remote_sync_service.record_sync_success(sink.engine, result, synced_at="2026-07-23T12:00:00")
    state = remote_sync_service.load_sync_state(sink.engine)
    target = state["targets"]["http://remote.test"]
    assert target["last_fetched_date"] == "2026-07-05T10:00:00"
    assert "password" not in json.dumps(state)

    # 增量:远端新增一条,以游标为起点再拉——只新进 1 条,边界重复被幂等跳过。
    remote.articles.append(_article(6, "2026-07-06T10:00:00"))
    result2 = asyncio.run(remote_sync_service.run_pull(
        base_url="http://remote.test",
        username="admin", password="secret",
        fetched_date_start=target["last_fetched_date"],
        page_size=2,
        import_fn=import_archive_sync_jsonl,
        transport=remote.transport(),
    ))
    assert result2["imported"] == 1
    assert result2["skipped"] == 1  # 游标边界(>=)那条重复,被导入端按 id 跳过
    with Session(sink.engine) as session:
        assert len(session.exec(select(ArticleRecord)).all()) == 6

    # 空跑增量不清游标。
    remote_sync_service.record_sync_success(
        sink.engine,
        {**result2, "max_fetched_date": ""},
        synced_at="2026-07-23T13:00:00",
    )
    state = remote_sync_service.load_sync_state(sink.engine)
    assert state["targets"]["http://remote.test"]["last_fetched_date"] == "2026-07-05T10:00:00"


def test_run_pull_counts_checksum_errors(monkeypatch, tmp_path):
    from api.routers.archive_sync import import_archive_sync_jsonl

    _make_local_sink(tmp_path, monkeypatch, "bad.db")
    articles = [_article(1, "2026-07-01T10:00:00"), _article(2, "2026-07-02T10:00:00")]
    articles[1]["_bad_checksum"] = "deadbeef" * 8
    remote = FakeRemote(articles)

    result = asyncio.run(remote_sync_service.run_pull(
        base_url="http://remote.test",
        username="admin", password="secret",
        import_fn=import_archive_sync_jsonl,
        transport=remote.transport(),
    ))
    assert result["imported"] == 1
    assert result["errors"] == 1
    assert result["error_samples"] and "checksum" in result["error_samples"][0]["error"]


def test_source_ids_filter_passthrough(monkeypatch, tmp_path):
    from api.routers.archive_sync import import_archive_sync_jsonl

    _make_local_sink(tmp_path, monkeypatch, "filter.db")
    articles = [
        _article(1, "2026-07-01T10:00:00", source_id="rss_a"),
        _article(2, "2026-07-02T10:00:00", source_id="rss_b"),
    ]
    remote = FakeRemote(articles)
    result = asyncio.run(remote_sync_service.run_pull(
        base_url="http://remote.test",
        username="admin", password="secret",
        source_ids=["rss_b"],
        import_fn=import_archive_sync_jsonl,
        transport=remote.transport(),
    ))
    assert result["pulled"] == 1
    assert result["imported"] == 1
    assert remote.export_requests[0].get("source_ids") == "rss_b"


# ==================== 端点门控与 job 提交 ====================

def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage
    from tests.conftest import seed_default_accounts

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'app_remote_sync.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    return app_module


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_endpoints_admin_gated(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        assert client.get("/api/admin/remote-sync/status").status_code == 401
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.get("/api/admin/remote-sync/status").status_code == 403
        assert client.post("/api/admin/remote-sync/test", json={}).status_code == 403
        assert client.post("/api/admin/remote-sync/start", json={}).status_code == 403
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.get("/api/admin/remote-sync/status")
        assert res.status_code == 200
        assert res.json() == {"state": {"targets": {}}, "jobs": []}
        # 参数校验:坏地址/空凭据 400。
        assert client.post(
            "/api/admin/remote-sync/test",
            json={"base_url": "nope", "username": "a", "password": "b"},
        ).status_code == 400
        assert client.post(
            "/api/admin/remote-sync/start",
            json={"base_url": "http://r.test", "username": "", "password": ""},
        ).status_code == 400


def test_start_submits_job_without_password(monkeypatch, tmp_path):
    app_module = _setup_app(monkeypatch, tmp_path)

    async def _fake_run_pull(**kwargs):
        assert kwargs["password"] == "secret"  # 凭据确实进入任务内存
        if kwargs.get("on_total"):
            kwargs["on_total"](2)
        if kwargs.get("on_advance"):
            kwargs["on_advance"](2)
        return {
            "base_url": kwargs["base_url"], "username": kwargs["username"],
            "fetched_date_start": "", "source_ids": [],
            "max_fetched_date": "2026-07-05T10:00:00", "error_samples": [],
            "pages": 1, "pulled": 2, "imported": 2, "updated": 0, "skipped": 0, "errors": 0,
        }

    monkeypatch.setattr(remote_sync_service, "run_pull", _fake_run_pull)

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        res = client.post("/api/admin/remote-sync/start", json={
            "base_url": "http://remote.test", "username": "admin", "password": "secret",
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        deadline = time.time() + 10
        job = None
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        assert job and job["status"] == "succeeded"
        assert job["result"]["imported"] == 2

        # job payload 快照绝不含密码。
        from models.db import JobRecord
        from sqlmodel import Session
        with Session(app_module.db_sink.engine) as session:
            record = session.get(JobRecord, job_id)
            assert "secret" not in record.payload_json
            assert "password" not in record.payload_json

        # 成功后 KV 游标就位,status 端点可见。
        status = client.get("/api/admin/remote-sync/status").json()
        target = status["state"]["targets"]["http://remote.test"]
        assert target["last_fetched_date"] == "2026-07-05T10:00:00"
        assert status["jobs"][0]["job_id"] == job_id
