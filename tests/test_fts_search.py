"""SQLite FTS5 全文搜索（标题 + 正文）单测。

覆盖：
- ensure_fts 幂等 + fts_available 探测；
- insert/update/delete 经 trigger 与 articles 表实时同步；
- 标题命中、**正文命中**（LIKE 时代搜不到的核心增量）、中文子串、英文大小写不敏感；
- 短 query（< 3 字符）fts_search_ids 返回 None → apply_article_query_filters 回退标题 LIKE；
- build_match_query 转义 / 短词丢弃；
- 端点级 GET /api/articles?search= 正文关键词能搜到（TestClient）。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session, select  # noqa: E402

from models.db import ArticleRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402
from storage.fts import (  # noqa: E402
    build_match_query,
    ensure_fts,
    fts_available,
    fts_search_ids,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from api.articles_view import apply_article_query_filters  # noqa: E402


def _sink(tmp_path, name="fts.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _add(sink, rid, title, content):
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id=rid, title=title, content_type="web_article", source_id="src_a",
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content=content, is_vectorized=False,
        ))
        session.commit()


def _search_ids(sink, term):
    with Session(sink.engine) as session:
        return fts_search_ids(session, term)


def _title_ids(sink, term):
    """经 apply_article_query_filters 的 search 分支取命中 id（走 FTS 或回退 LIKE）。"""
    with Session(sink.engine) as session:
        query = apply_article_query_filters(
            select(ArticleRecord), search=term, session=session
        )
        return {r.id for r in session.exec(query).all()}


# ---------------------------------------------------------------- DDL / 探测

def test_ensure_fts_idempotent_and_available(tmp_path):
    sink = _sink(tmp_path)  # __init__ 已调 ensure_fts
    assert fts_available(sink.engine) is True
    # 二次、三次调用不应报错，且不重复回填破坏状态
    assert ensure_fts(sink.engine) is True
    assert ensure_fts(sink.engine) is True
    assert fts_available(sink.engine) is True


def test_rebuild_backfills_preexisting_rows(tmp_path):
    """首次建 FTS 前已有的行也应被 rebuild 回填（模拟老库先有数据后建索引）。"""
    sink = _sink(tmp_path)
    _add(sink, "pre1", "Preexisting Title", "body mentions penguins here")
    # 手工删表再重建，验证 rebuild 回填存量
    from storage.fts import drop_fts
    drop_fts(sink.engine)
    assert fts_available(sink.engine) is False
    ensure_fts(sink.engine)
    ids = _search_ids(sink, "penguins")
    assert ids is not None and len(ids) == 1  # rebuild 回填了存量行
    # rebuild 回填后正文能命中
    assert _title_ids(sink, "penguins") == {"pre1"}


# ------------------------------------------------------------ trigger 同步

def test_triggers_sync_insert_update_delete(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "a1", "Hello World", "body about kittens")
    assert _title_ids(sink, "kittens") == {"a1"}

    # 更新正文：旧词消失、新词命中
    with Session(sink.engine) as session:
        rec = session.get(ArticleRecord, "a1")
        rec.content = "body about puppies"
        session.add(rec)
        session.commit()
    assert _title_ids(sink, "kittens") == set()
    assert _title_ids(sink, "puppies") == {"a1"}

    # 删除：不再命中
    with Session(sink.engine) as session:
        session.delete(session.get(ArticleRecord, "a1"))
        session.commit()
    assert _title_ids(sink, "puppies") == set()


# ------------------------------------------------------------ 命中语义

def test_title_hit(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "t1", "Transformer architecture explained", "unrelated body text")
    assert _title_ids(sink, "Transformer") == {"t1"}


def test_content_hit_is_the_increment_over_title_like(tmp_path):
    """核心增量：关键词只在正文、标题无——LIKE-on-title 搜不到，FTS 能搜到。"""
    sink = _sink(tmp_path)
    _add(sink, "c1", "Weekly digest", "deep dive into retrieval augmented generation")
    # 标题不含 retrieval
    assert "retrieval" not in "Weekly digest".lower()
    ids = _search_ids(sink, "retrieval")
    assert ids is not None and len(ids) == 1
    assert _title_ids(sink, "retrieval") == {"c1"}


def test_chinese_substring_hit(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "z1", "本周资讯", "本文介绍深度学习模型的最新发布")
    # 正文子串命中（trigram）
    assert _title_ids(sink, "深度学习") == {"z1"}
    assert _title_ids(sink, "模型") == set()  # 2 字 < trigram 下限 → None → 回退标题 LIKE 也不含
    assert _title_ids(sink, "学习模型") == {"z1"}


def test_case_insensitive_english(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "e1", "Some news", "OpenAI released a new Model today")
    assert _title_ids(sink, "openai") == {"e1"}
    assert _title_ids(sink, "OPENAI") == {"e1"}


# ------------------------------------------------------------ 短 query 回退

def test_short_query_returns_none_and_falls_back_to_title_like(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "s1", "AI weekly", "body has no such short token standalone")
    # < 3 字符 → fts_search_ids 返回 None
    assert _search_ids(sink, "AI") is None
    assert _search_ids(sink, "a") is None
    # 回退标题 LIKE：标题含 "AI" 应命中
    assert _title_ids(sink, "AI") == {"s1"}
    # 回退标题 LIKE：短词只在正文、标题无 → LIKE-on-title 搜不到
    assert _title_ids(sink, "no") == set()


def test_fts_search_ids_no_match_returns_empty_not_none(tmp_path):
    """FTS 可用但零命中返回 []（区别于 None 的不可用），调用方按空结果处理。"""
    sink = _sink(tmp_path)
    _add(sink, "n1", "Alpha", "beta gamma")
    assert _search_ids(sink, "zzzznotpresent") == []


def test_build_match_query_escaping_and_short_token_drop():
    assert build_match_query("machine learning") == '"machine" AND "learning"'
    # 内部双引号翻倍转义
    assert build_match_query('say "hi" there') == '"say" AND """hi""" AND "there"'
    # 全部短词 → None
    assert build_match_query("a b c") is None
    # 混合：短词丢弃，长词保留
    assert build_match_query("ab retrieval") == '"retrieval"'
    assert build_match_query("") is None


# ------------------------------------------------------------ 端点级

def _seed_admin(engine):
    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        session.add(UserRecord(
            username="admin", password_hash=accounts_service.hash_password("admin"),
            role="admin", is_active=True, created_at=now, updated_at=now,
        ))
        session.commit()


def test_articles_endpoint_searches_content(monkeypatch, tmp_path):
    import api.app as app_module
    sink = _sink(tmp_path, "endpoint.db")
    _seed_admin(sink.engine)
    _add(sink, "p1", "Generic headline one", "quantized inference speedups on edge devices")
    _add(sink, "p2", "Generic headline two", "totally different subject matter")
    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        # 正文关键词（标题不含）能搜到
        rows = client.get("/api/articles?search=quantized&limit=50").json()
        assert {r["id"] for r in rows} == {"p1"}
        # 无关词零命中
        assert client.get("/api/articles?search=nonexistentkeyword&limit=50").json() == []
        # 标题词仍命中
        rows2 = client.get("/api/articles?search=headline&limit=50").json()
        assert {r["id"] for r in rows2} == {"p1", "p2"}
