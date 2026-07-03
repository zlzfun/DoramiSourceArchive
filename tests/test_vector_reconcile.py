"""SQLite↔Chroma 向量索引对账（阶段 2 · 跨存储一致性）。

覆盖 services.vector_reconcile.reconcile 的三类漂移分类与修复动作：
- flagged_but_absent：DB is_vectorized=True 但 Chroma 无 chunk → 复位 False；
- present_but_unflagged：Chroma 有 chunk 但文章 is_vectorized=False → 采纳置 True；
- orphan_chunks：Chroma 有 chunk 但 SQLite 已无该文章 → 清除孤儿 chunk。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session  # noqa: E402

from models.db import ArticleRecord  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from services import vector_reconcile  # noqa: E402


class FakeVectorSink:
    """只实现对账用到的 list_parent_ids / delete；parents 是 Chroma 现存 parent_id 集。"""

    def __init__(self, parents):
        self.parents = set(parents)
        self.deleted = []

    async def list_parent_ids(self):
        return set(self.parents)

    async def delete(self, article_id):
        self.deleted.append(article_id)
        self.parents.discard(article_id)
        return True


def _seed(db_sink, rows):
    with Session(db_sink.engine) as session:
        for rid, is_vec in rows:
            session.add(ArticleRecord(
                id=rid, title="t", content_type="web_article", source_id="s",
                source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
                is_vectorized=is_vec,
            ))
        session.commit()


def _vec_flag(db_sink, rid):
    with Session(db_sink.engine) as session:
        return session.get(ArticleRecord, rid).is_vectorized


def test_reconcile_report_classifies_drift(tmp_path):
    db = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'rec.db'}")
    # in_sync：a1 已索引且在 Chroma
    # flagged_but_absent：a2 DB 说已索引，Chroma 无
    # present_but_unflagged：a3 未标记，Chroma 却有 chunk
    _seed(db, [("a1", True), ("a2", True), ("a3", False)])
    # 另有孤儿 a9（Chroma 有 chunk，DB 无此文章）
    vs = FakeVectorSink(parents={"a1", "a3", "a9"})

    report = asyncio.run(vector_reconcile.reconcile(db, vs, repair=False))

    assert report["db_total"] == 3
    assert report["db_vectorized"] == 2
    assert report["chroma_parents"] == 3
    assert report["flagged_but_absent"]["count"] == 1
    assert report["flagged_but_absent"]["sample"] == ["a2"]
    assert report["present_but_unflagged"]["sample"] == ["a3"]
    assert report["orphan_chunks"]["sample"] == ["a9"]
    assert report["in_sync"] is False
    assert report["repaired"] is None
    # 只读报告不应改动任何状态
    assert vs.deleted == []
    assert _vec_flag(db, "a2") is True
    assert _vec_flag(db, "a3") is False


def test_reconcile_repair_fixes_all_three(tmp_path):
    db = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'rec2.db'}")
    _seed(db, [("a1", True), ("a2", True), ("a3", False)])
    vs = FakeVectorSink(parents={"a1", "a3", "a9"})

    report = asyncio.run(vector_reconcile.reconcile(db, vs, repair=True))

    assert report["repaired"] == {"reset_flag": 1, "adopted_flag": 1, "purged_chunks": 1}
    assert _vec_flag(db, "a2") is False   # 复位（丢索引）
    assert _vec_flag(db, "a3") is True    # 采纳
    assert vs.deleted == ["a9"]           # 孤儿清除
    # 丢索引项标为 stale（仍会被 all-pending 重拾），采纳项标 indexed
    with Session(db.engine) as session:
        assert session.get(ArticleRecord, "a2").index_status == "stale"
        assert session.get(ArticleRecord, "a3").index_status == "indexed"


def test_reconcile_in_sync(tmp_path):
    db = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'rec3.db'}")
    _seed(db, [("a1", True), ("a2", False)])
    vs = FakeVectorSink(parents={"a1"})  # a2 未索引也不在 Chroma → 一致

    report = asyncio.run(vector_reconcile.reconcile(db, vs, repair=False))
    assert report["in_sync"] is True
    for key in ("flagged_but_absent", "present_but_unflagged", "orphan_chunks"):
        assert report[key]["count"] == 0


def _seed_users(engine):
    import datetime
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(
                username=username, password_hash=accounts_service.hash_password(password),
                role=role, is_active=True, created_at=now, updated_at=now,
            ))
        session.commit()


def test_reconcile_endpoints_admin_gated_and_wired(monkeypatch, tmp_path):
    """端点级冒烟：collector(admin) 可读报告/触发修复，reader 被拦（403），二者经 deps 正确取 sink。"""
    from fastapi.testclient import TestClient
    import api.app as app_module

    db = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'ep.db'}")
    _seed_users(db.engine)
    _seed(db, [("a1", True), ("a2", True)])  # a2 flagged_but_absent
    monkeypatch.setattr(app_module, "db_sink", db)
    monkeypatch.setattr(app_module, "vector_sink", FakeVectorSink(parents={"a1", "orphan"}))

    with TestClient(app_module.app) as client:
        # reader（user）不得访问 collector 端点
        client.post("/api/auth/login", json={"username": "user", "password": "user"})
        assert client.get("/api/vector/reconcile").status_code == 403
        client.post("/api/auth/logout")

        # admin 只读报告
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        report = client.get("/api/vector/reconcile")
        assert report.status_code == 200
        body = report.json()
        assert body["flagged_but_absent"]["count"] == 1  # a2
        assert body["orphan_chunks"]["count"] == 1        # orphan
        assert body["repaired"] is None

        # admin 触发修复
        fixed = client.post("/api/vector/reconcile").json()
        assert fixed["repaired"]["reset_flag"] == 1
        assert fixed["repaired"]["purged_chunks"] == 1
