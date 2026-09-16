"""源合集(策展合集)测试:注册表校验 + 目录端点 + 批量订阅/退订端到端。

定调见 docs/source-collections-wave-plan.md:合集是目录呈现层的批量动作,
不是订阅实体——这里验证的正是「批量 = 逐成员复刻单源订阅语义」。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in accounts:
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()


def _mark_defaults_seeded(engine, usernames=("user",)):
    """预标记「默认订阅已播种」,让测试账号保持零订阅初态。"""
    import api.app as app_module
    from services import daily_brief as daily_brief_service

    with Session(engine) as session:
        for username in usernames:
            daily_brief_service.set_setting(
                session, f"{app_module.DEFAULTS_SEEDED_KEY_PREFIX}:{username}", "test-preseeded"
            )


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _bootstrap(monkeypatch, tmp_path, name: str):
    import api.app as app_module

    sink = _make_sink(tmp_path, name)
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    _mark_defaults_seeded(sink.engine)
    return app_module, sink


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


# ==================== 注册表校验(CI 级护栏) ====================

def test_registry_collections_are_well_formed():
    """collection_id 唯一、成员非空无重复、成员必须是现役注册源(防注册表漂移腐烂)。"""
    from api.sources import _registry_source_meta
    from services.source_collections import SOURCE_COLLECTIONS

    registry_ids = set(_registry_source_meta().keys())
    seen_collection_ids = set()
    for collection in SOURCE_COLLECTIONS:
        assert collection.collection_id and collection.collection_id not in seen_collection_ids
        seen_collection_ids.add(collection.collection_id)
        assert collection.name and collection.description and collection.provenance_note
        assert len(collection.source_ids) > 0
        assert len(set(collection.source_ids)) == len(collection.source_ids), (
            f"合集 {collection.collection_id} 成员重复"
        )
        missing = [sid for sid in collection.source_ids if sid not in registry_ids]
        assert not missing, f"合集 {collection.collection_id} 含注册表外成员: {missing}"


# ==================== 目录端点 ====================

def test_collections_catalog_shape_and_gating(monkeypatch, tmp_path):
    app_module, _sink = _bootstrap(monkeypatch, tmp_path, "coll_catalog.db")

    with TestClient(app_module.app) as client:
        # 未登录:reader 前缀门控拒绝
        assert client.get("/api/reader/collections").status_code in (401, 403)

        _login(client)
        response = client.get("/api/reader/collections")
        assert response.status_code == 200
        collections = response.json()["collections"]
        assert len(collections) >= 1
        first = next(c for c in collections if c["collection_id"] == "hn-popular-blogs-2025")
        assert set(first.keys()) == {
            "collection_id", "name", "description", "provenance_note", "source_ids"
        }
        assert "rss_sean_goedecke" in first["source_ids"]


# ==================== 批量订阅 ====================

def test_subscribe_collection_end_to_end(monkeypatch, tmp_path):
    from models.db import ReaderSubscriptionRecord
    from services.source_collections import get_collection

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "coll_sub.db")
    collection = get_collection("hn-popular-blogs-2025")

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post("/api/reader/collections/hn-popular-blogs-2025/subscribe")
        assert response.status_code == 200
        data = response.json()
        assert data["added"] == list(collection.source_ids)
        assert data["already_subscribed"] == []
        assert data["unavailable"] == []
        assert set(collection.source_ids) <= set(data["subscribed_source_ids"])

        # 幂等重放:全部落入 already_subscribed,不新建记录
        replay = client.post("/api/reader/collections/hn-popular-blogs-2025/subscribe").json()
        assert replay["added"] == []
        assert replay["already_subscribed"] == list(collection.source_ids)

    with Session(sink.engine) as session:
        records = session.exec(
            select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == "user")
        ).all()
        assert len(records) == len(collection.source_ids)
        # 与单源一键订阅同语义:每成员建立未读水位
        from services import reader_state as reader_state_service
        cursors = reader_state_service.load_cursors(session, username="user")
        for source_id in collection.source_ids:
            assert source_id in cursors


def test_subscribe_collection_skips_hidden_members(monkeypatch, tmp_path):
    from services import source_visibility as source_visibility_service
    from services.source_collections import get_collection

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "coll_hidden.db")
    collection = get_collection("hn-popular-blogs-2025")
    hidden_member = collection.source_ids[0]
    with Session(sink.engine) as session:
        source_visibility_service.set_source_hidden(session, hidden_member, True)

    with TestClient(app_module.app) as client:
        _login(client)
        data = client.post("/api/reader/collections/hn-popular-blogs-2025/subscribe").json()
        assert hidden_member in data["unavailable"]
        assert hidden_member not in data["added"]
        assert hidden_member not in data["subscribed_source_ids"]
        assert set(collection.source_ids) - {hidden_member} == set(data["added"])


def test_subscribe_unknown_collection_404(monkeypatch, tmp_path):
    app_module, _sink = _bootstrap(monkeypatch, tmp_path, "coll_404.db")

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/collections/no-such-collection/subscribe").status_code == 404
        assert client.delete("/api/reader/collections/no-such-collection/subscribe").status_code == 404


# ==================== 批量退订 ====================

def test_unsubscribe_collection_removes_members_and_spares_others(monkeypatch, tmp_path):
    from models.db import ReaderSubscriptionRecord
    from services.source_collections import get_collection

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "coll_unsub.db")
    collection = get_collection("hn-popular-blogs-2025")

    with TestClient(app_module.app) as client:
        _login(client)
        # 先订合集 + 一个合集外的源(退订合集不得殃及)
        client.post("/api/reader/collections/hn-popular-blogs-2025/subscribe")
        outsider = client.post("/api/reader/sources/web_qbitai/subscribe")
        assert outsider.status_code == 200

        response = client.delete("/api/reader/collections/hn-popular-blogs-2025/subscribe")
        assert response.status_code == 200
        data = response.json()
        assert set(data["removed"]) == set(collection.source_ids)
        assert data["subscribed_source_ids"] == ["web_qbitai"]

    with Session(sink.engine) as session:
        records = session.exec(
            select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.owner_username == "user")
        ).all()
        assert len(records) == 1  # 只剩合集外那份
        from services import reader_state as reader_state_service
        cursors = reader_state_service.load_cursors(session, username="user")
        for source_id in collection.source_ids:
            assert source_id not in cursors  # 水位清空
        assert "web_qbitai" in cursors


# ==================== 按形态批量订阅 ====================

def test_subscribe_all_article_sources_is_partial_and_idempotent(monkeypatch, tmp_path):
    app_module, sink = _bootstrap(monkeypatch, tmp_path, "shape_article.db")

    with TestClient(app_module.app) as client:
        _login(client)
        catalog = client.get("/api/reader/sources").json()["sources"]
        article_ids = [
            row["source_id"] for row in catalog
            if row["shape"] == "article" and not row["hidden"]
        ]
        assert len(article_ids) > 1

        first = article_ids[0]
        assert client.post(f"/api/reader/sources/{first}/subscribe").status_code == 200
        response = client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "article"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["shape"] == "article"
        assert data["already_subscribed"] == [first]
        assert set(data["added"]) == set(article_ids) - {first}
        assert data["unavailable"] == []
        assert set(article_ids) <= set(data["subscribed_source_ids"])

        replay = client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "article"}
        ).json()
        assert replay["added"] == []
        assert set(replay["already_subscribed"]) == set(article_ids)

    with Session(sink.engine) as session:
        from services import reader_state as reader_state_service

        cursors = reader_state_service.load_cursors(session, username="user")
        assert set(article_ids) <= set(cursors)


def test_subscribe_all_podcast_sources_and_reject_other_shapes(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "shape_podcast.db")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast_batch_test",
            name="Batch Test Podcast",
            source_type="podcast",
            url="https://example.test/podcast.xml",
            created_at="2026-09-16T00:00:00",
            updated_at="2026-09-16T00:00:00",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        catalog = client.get("/api/reader/sources").json()["sources"]
        podcast_ids = [
            row["source_id"] for row in catalog
            if row["shape"] == "podcast" and not row["hidden"]
        ]
        assert podcast_ids

        response = client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "podcast"}
        )
        assert response.status_code == 200
        assert set(response.json()["added"]) == set(podcast_ids)
        assert client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "social"}
        ).status_code == 422


def test_subscribe_by_shape_does_not_enumerate_unknown_hidden_sources(monkeypatch, tmp_path):
    from api.sources import _registry_source_meta
    from services import source_visibility as source_visibility_service

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "shape_hidden.db")
    article_ids = [
        source_id for source_id, meta in _registry_source_meta().items()
        if not meta.get("is_template") and (meta.get("shape") or "article") == "article"
    ]
    assert len(article_ids) >= 2
    known_hidden_id, unknown_hidden_id = article_ids[:2]

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post(f"/api/reader/sources/{known_hidden_id}/subscribe").status_code == 200
        with Session(sink.engine) as session:
            source_visibility_service.set_source_hidden(session, known_hidden_id, True)
            source_visibility_service.set_source_hidden(session, unknown_hidden_id, True)

        catalog_by_id = {
            row["source_id"]: row
            for row in client.get("/api/reader/sources").json()["sources"]
        }
        assert catalog_by_id[known_hidden_id]["hidden"] is True
        assert unknown_hidden_id not in catalog_by_id
        data = client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "article"}
        ).json()
        assert known_hidden_id in data["unavailable"]
        assert unknown_hidden_id not in data["unavailable"]
        assert unknown_hidden_id not in data["added"]
        assert known_hidden_id in data["subscribed_source_ids"]


def test_subscribe_by_shape_rolls_back_the_whole_batch_on_failure(monkeypatch, tmp_path):
    from api.routers import reader as reader_router
    from models.db import ReaderSubscriptionRecord
    from services import reader_state as reader_state_service

    app_module, sink = _bootstrap(monkeypatch, tmp_path, "shape_atomic.db")
    monkeypatch.setattr(reader_router, "_reader_sources_catalog", lambda *_args, **_kwargs: {
        "sources": [
            {"source_id": "batch_article_a", "name": "A", "shape": "article", "hidden": False},
            {"source_id": "batch_article_b", "name": "B", "shape": "article", "hidden": False},
        ],
        "subscribed_source_ids": [],
        "total_sources": 2,
    })
    original_init = reader_state_service.init_cursor_with_backlog

    def fail_second(session, *, username, source_id):
        if source_id == "batch_article_b":
            raise RuntimeError("simulated cursor failure")
        return original_init(session, username=username, source_id=source_id)

    monkeypatch.setattr(reader_state_service, "init_cursor_with_backlog", fail_second)
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        _login(client)
        response = client.post(
            "/api/reader/sources/subscribe-batch", json={"shape": "article"}
        )
        assert response.status_code == 500

    with Session(sink.engine) as session:
        records = session.exec(
            select(ReaderSubscriptionRecord).where(
                ReaderSubscriptionRecord.owner_username == "user"
            )
        ).all()
        assert records == []
        assert reader_state_service.load_cursors(session, username="user") == {}
