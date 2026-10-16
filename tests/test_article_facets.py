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
from models.db import ArticleRecord, SourceConfigRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _sink(tmp_path):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'facets.db'}")


def _add(sink, rid, ctype, src):
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id=rid, title=f"t-{rid}", content_type=ctype, source_id=src,
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content="body",
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
            {"value": "web_article", "count": 2, "label": "网页条目"},
            {"value": "rss_article", "count": 1, "label": "RSS 条目"},
        ]
        assert {r["value"] for r in data["source_ids"]} == {"src_a", "src_b"}
        # 降序;不在注册表 / 兜底表 / 配置表里的来源,展示名回落原 id
        assert data["source_ids"][0] == {"value": "src_a", "count": 2, "label": "src_a"}

        # 不排除时日报计入
        assert client.get("/api/articles/facets").json()["total"] == 4

        # content_types CSV 透传:多类型命中
        rows = client.get("/api/articles?content_types=web_article,rss_article&limit=50").json()
        assert {r["id"] for r in rows} == {"a1", "a2", "a3"}

        # 兜底路由未吞掉 facets(单条详情仍可用)
        assert client.get("/api/articles/a1").json()["id"] == "a1"


def test_facets_source_labels_use_friendly_names(monkeypatch, tmp_path):
    """台账来源分面带展示名(issue #184):读者自定源取配置行的名称,注册源取抓取器名,未知 id 回落原值。"""
    import api.app as app_module
    from api import deps
    sink = _sink(tmp_path)
    _seed_users(sink.engine)
    _add(sink, "u1", "rss_article", "user_rss_2fa81c48")
    _add(sink, "u2", "rss_article", "user_rss_2fa81c48")
    _add(sink, "r1", "web_article", "web_qbitai")
    _add(sink, "x1", "web_article", "src_unknown")
    with Session(sink.engine) as session:
        now = datetime.datetime.now().isoformat()
        session.add(SourceConfigRecord(source_id="user_rss_2fa81c48", name="AIHOT — 全部 AI 动态", url="http://feed",
                                       created_at=now, updated_at=now))
        session.commit()
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(deps, "get_db_sink", lambda: sink)
    # 分面取名走批量路径:不许再逐个 id 各开一次 session(codex R1)
    from services import source_naming

    def _no_per_id_lookup(_source_id):
        raise AssertionError("facets 不应逐个 id 查配置源名称")

    monkeypatch.setattr(source_naming, "_config_source_name", _no_per_id_lookup)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        labels = {row["value"]: row["label"] for row in client.get("/api/articles/facets").json()["source_ids"]}
    assert labels["user_rss_2fa81c48"] == "AIHOT — 全部 AI 动态"
    assert labels["web_qbitai"] == "量子位"
    assert labels["src_unknown"] == "src_unknown"
