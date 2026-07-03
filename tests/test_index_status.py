"""向量索引状态枚举 index_status（阶段2/3 跨存储一致性）。

覆盖：存储层态转移（mark_as_vectorized/unvectorized/set_index_status 与派生位
is_vectorized 同步）、save() 语义（新记录 pending、正文回填 stale）、
GET /api/articles 的 index_status 过滤与载荷字段。
"""

import asyncio
import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import ArticleRecord, UserRecord  # noqa: E402
from api.articles_view import GenericContent  # noqa: E402
from services import accounts as accounts_service  # noqa: E402


def _sink(tmp_path, name="idx.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _add(sink, rid, is_vec=False):
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id=rid, title="t", content_type="web_article", source_id="s",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content="body", is_vectorized=is_vec,
        ))
        session.commit()


def _get(sink, rid):
    with Session(sink.engine) as session:
        r = session.get(ArticleRecord, rid)
        return r.index_status, r.is_vectorized


def test_new_record_defaults_pending(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "a1")
    assert _get(sink, "a1") == ("pending", False)


def test_mark_vectorized_and_unvectorized(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "a1")
    assert asyncio.run(sink.mark_as_vectorized("a1"))
    assert _get(sink, "a1") == ("indexed", True)
    assert asyncio.run(sink.mark_as_unvectorized("a1"))
    assert _get(sink, "a1") == ("pending", False)


def test_set_index_status_syncs_derived_flag(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "a1")
    assert asyncio.run(sink.set_index_status("a1", "indexing"))
    assert _get(sink, "a1") == ("indexing", False)
    assert asyncio.run(sink.set_index_status("a1", "failed"))
    assert _get(sink, "a1") == ("failed", False)
    assert asyncio.run(sink.set_index_status("a1", "indexed"))
    assert _get(sink, "a1") == ("indexed", True)  # 派生位随 indexed 置 True
    # 非法状态被拒
    assert asyncio.run(sink.set_index_status("a1", "bogus")) is False
    assert _get(sink, "a1") == ("indexed", True)


def test_save_content_backfill_marks_stale(tmp_path):
    sink = _sink(tmp_path)
    # 先落一个无正文的记录
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="a1", title="t", content_type="web_article", source_id="s",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=False, content=None, is_vectorized=False,
        ))
        session.commit()
    # 用带正文的同 id 内容回填
    item = GenericContent(
        id="a1", title="t", source_url="http://x", publish_date="2026-06-01",
        fetched_date="2026-06-01", has_content=True, content="now has a real body",
    )
    item.source_id = "s"
    assert asyncio.run(sink.save(item)) is True
    assert _get(sink, "a1")[0] == "stale"


def _seed_users(engine):
    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for u, p, r in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(username=u, password_hash=accounts_service.hash_password(p),
                                   role=r, is_active=True, created_at=now, updated_at=now))
        session.commit()


def test_articles_endpoint_filters_and_exposes_index_status(monkeypatch, tmp_path):
    import api.app as app_module
    sink = _sink(tmp_path, "ep.db")
    _seed_users(sink.engine)
    _add(sink, "a_pending")
    _add(sink, "a_indexed")
    asyncio.run(sink.set_index_status("a_indexed", "indexed"))
    _add(sink, "a_failed")
    asyncio.run(sink.set_index_status("a_failed", "failed"))
    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

        # 载荷含 index_status
        allrows = client.get("/api/articles?limit=100").json()
        by_id = {r["id"]: r for r in allrows}
        assert by_id["a_indexed"]["index_status"] == "indexed"
        assert by_id["a_pending"]["index_status"] == "pending"

        # 过滤 failed
        failed = client.get("/api/articles?index_status=failed").json()
        assert {r["id"] for r in failed} == {"a_failed"}

        # is_vectorized 契约仍在
        vec = client.get("/api/articles?is_vectorized=true").json()
        assert {r["id"] for r in vec} == {"a_indexed"}
