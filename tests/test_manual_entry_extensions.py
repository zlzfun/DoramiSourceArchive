"""手工录入扩展字段与列表轻取数契约。

覆盖:POST /api/articles 携带 extensions_json 真实落库(旧 setattr 写法不进
dataclass fields(),曾静默丢失);extensions_json 中的基础字段键不得覆写/泄入扩展;
GET /api/articles 的 include_content=false 不带正文与扩展、include_extensions=true
在不带正文时仍返回 extensions_json(日报列表行 meta 的轻取数路径)。
"""

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


def test_manual_entry_persists_extensions_and_light_listing(monkeypatch, tmp_path):
    import api.app as app_module
    sink = _sink(tmp_path)
    _seed_admin(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

        resp = client.post("/api/articles", json={
            "id": "m1", "title": "手工日报", "content": "正文",
            "publish_date": "2026-07-25", "content_type": "daily_brief",
            "source_id": "dorami_daily_brief",
            # title 是基础字段:不得覆写记录标题,也不得泄入扩展
            "extensions_json": json.dumps(
                {"articles_count": 18, "llm_model": "test-model", "title": "不该覆写"}
            ),
        })
        assert resp.status_code == 200

        detail = client.get("/api/articles/m1").json()
        assert detail["title"] == "手工日报"
        ext = json.loads(detail["extensions_json"])
        assert ext["articles_count"] == 18
        assert ext["llm_model"] == "test-model"
        assert "title" not in ext

        # 非法 extensions_json:整体忽略,录入本身不受影响
        assert client.post("/api/articles", json={
            "id": "m2", "title": "坏扩展", "content": "x",
            "publish_date": "2026-07-25", "content_type": "manual_entry",
            "extensions_json": "not-json",
        }).status_code == 200
        assert json.loads(client.get("/api/articles/m2").json()["extensions_json"]) == {}

        # 轻取数:不带正文时默认也不带扩展;include_extensions=true 单独放行扩展
        light = client.get("/api/articles?source_id=dorami_daily_brief&include_content=false").json()
        assert light and "content" not in light[0] and "extensions_json" not in light[0]
        light_ext = client.get(
            "/api/articles?source_id=dorami_daily_brief&include_content=false&include_extensions=true"
        ).json()
        assert "content" not in light_ext[0]
        assert json.loads(light_ext[0]["extensions_json"])["articles_count"] == 18
