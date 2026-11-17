"""手工录入并入收录后的扩展字段契约 + 列表轻取数契约(issue #262 推送入口统一波重写)。

旧契约(调用方自定 id / content_type、任意 ``extensions_json``、坏 JSON 整体忽略)已被取代:
运维「单条收录」与外部调用方一律走 ``POST /api/public/ingest/v1/items``——条目 id 与
``content_type`` 由服务端决定,``extensions`` 按 kind 走键白名单(系统键、基础字段键丢弃),
值类型不对只拒收这一条。手工日报不再从通用录入进入(公共日报源是保留 id)。

列表轻取数契约不变:``GET /api/articles`` 的 include_content=false 不带正文与扩展、
include_extensions=true 在不带正文时仍返回 extensions_json(日报列表行 meta 的轻取数路径)。
"""

import asyncio
import datetime
import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _sink(tmp_path):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'manual_ext.db'}")


def _seed_admin(engine):
    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        session.add(UserRecord(username="admin", password_hash=accounts_service.hash_password("admin"),
                               role="admin", is_active=True, created_at=now, updated_at=now))
        session.commit()


def _ingest(client, items):
    response = client.post("/api/public/ingest/v1/items", json={"items": items})
    assert response.status_code == 200, response.text
    return response.json()["results"]


def test_single_ingest_lets_the_server_decide_id_type_and_extensions(monkeypatch, tmp_path):
    import api.app as app_module
    sink = _sink(tmp_path)
    _seed_admin(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda ids: None)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        # 旧入口退役:回 410 并指向收录端点。
        retired = client.post("/api/articles", json={"id": "m1", "title": "t", "content": "x"})
        assert retired.status_code == 410
        assert "/api/public/ingest/v1/items" in retired.json()["detail"]

        results = _ingest(client, [
            {
                "kind": "article", "url": "https://blog.example.com/p/ext", "title": "手工一篇",
                "content": "正文",
                # 调用方写不进 id / content_type(未知字段忽略)。
                "id": "caller_id", "content_type": "daily_brief",
                # author 在白名单;listing_source 是系统键、title 是基础字段键,都丢弃。
                "extensions": {"author": "A", "listing_source": "hn", "title": "不该覆写"},
            },
            # 值类型不对:只拒收这一条。
            {"kind": "article", "url": "https://blog.example.com/p/bad", "title": "坏扩展",
             "content": "x", "extensions": {"tags": {"not": "a list"}}},
            # 手工日报不再从通用录入进入:公共日报源是保留 id。
            {"kind": "article", "url": "https://dorami.example/brief", "title": "手工日报",
             "content": "x", "source": {"source_id": "dorami_daily_brief"}},
        ])
        assert results[0]["status"] == "created"
        article_id = results[0]["id"]
        assert article_id.startswith("web_blog_example_com_") and article_id != "caller_id"
        assert results[1]["status"] == "rejected" and "tags" in results[1]["reason"]
        assert results[2]["status"] == "rejected"

        detail = client.get(f"/api/articles/{article_id}").json()
        assert detail["title"] == "手工一篇"
        assert detail["content_type"] == "web_article"
        ext = json.loads(detail["extensions_json"])
        assert ext["author"] == "A"
        assert "listing_source" not in ext and "title" not in ext


def test_light_listing_contract(monkeypatch, tmp_path):
    from api.articles_view import GenericContent

    import api.app as app_module
    sink = _sink(tmp_path)
    _seed_admin(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)
    brief = GenericContent(id="m1", title="日报", source_url="", publish_date="2026-07-25", content="正文",
                           has_content=True)
    brief.source_id = "dorami_daily_brief"
    brief.content_type = "daily_brief"
    brief.extra_extensions = {"articles_count": 18, "llm_model": "test-model"}
    assert asyncio.run(sink.save(brief)) is True

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        # 轻取数:不带正文时默认也不带扩展;include_extensions=true 单独放行扩展
        light = client.get("/api/articles?source_id=dorami_daily_brief&include_content=false").json()
        assert light and "content" not in light[0] and "extensions_json" not in light[0]
        light_ext = client.get(
            "/api/articles?source_id=dorami_daily_brief&include_content=false&include_extensions=true"
        ).json()
        assert "content" not in light_ext[0]
        assert json.loads(light_ext[0]["extensions_json"])["articles_count"] == 18
