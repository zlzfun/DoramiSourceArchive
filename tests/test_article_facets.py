"""分面聚合端点 GET /api/articles/facets(台账分面栏单一数据源)。

覆盖:全量 group-by 计数、exclude_source_ids 排除、计数按降序、
content_types CSV 透传对列表端点生效、兜底路由不被误吞(facets 先注册)。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import ArticleRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _sink(tmp_path):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'facets.db'}")


def _add(sink, rid, ctype, src):
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id=rid, title=f"t-{rid}", content_type=ctype, source_id=src,
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content="body", is_vectorized=False,
        ))
        session.commit()


def _seed_users(engine):
    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        session.add(UserRecord(username="admin", password_hash=accounts_service.hash_password("admin"),
                               role="admin", is_active=True, created_at=now, updated_at=now))
        session.commit()


def test_facets_counts_exclude_and_csv_filter(monkeypatch, tmp_path):
    import api.app as app_module
    sink = _sink(tmp_path)
    _seed_users(sink.engine)
    _add(sink, "a1", "web_article", "src_a")
    _add(sink, "a2", "web_article", "src_a")
    _add(sink, "a3", "rss_article", "src_b")
    _add(sink, "a4", "daily_brief", "dorami_daily_brief")
    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

        data = client.get("/api/articles/facets?exclude_source_ids=dorami_daily_brief").json()
        assert data["total"] == 3
        assert data["content_types"] == [
            {"value": "web_article", "count": 2},
            {"value": "rss_article", "count": 1},
        ]
        assert {r["value"] for r in data["source_ids"]} == {"src_a", "src_b"}
        # 降序
        assert data["source_ids"][0] == {"value": "src_a", "count": 2}

        # 不排除时日报计入
        assert client.get("/api/articles/facets").json()["total"] == 4

        # content_types CSV 透传:多类型命中
        rows = client.get("/api/articles?content_types=web_article,rss_article&limit=50").json()
        assert {r["id"] for r in rows} == {"a1", "a2", "a3"}

        # 兜底路由未吞掉 facets(单条详情仍可用)
        assert client.get("/api/articles/a1").json()["id"] == "a1"
