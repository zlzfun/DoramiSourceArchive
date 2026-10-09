"""SQLite FTS5 全文搜索（标题 + 正文）单测。

覆盖：
- ensure_fts 幂等 + fts_available 探测；
- insert/update/delete 经 trigger 与 articles 表实时同步；
- 标题命中、**正文命中**（LIKE 时代搜不到的核心增量）、中文子串、英文大小写不敏感；
- 短 query（< 3 字符）fts_search_ids 返回 None → apply_article_query_filters 回退标题 LIKE；
- build_match_query 转义 / 短词丢弃；
- 端点级 GET /api/articles?search= 正文关键词能搜到（TestClient）；
- Unicode 归一化（issue #94）：Python/SQL 两端映射一致、排版变体互搜、
  update/delete 后索引干净、旧库重建与迁移升降级；
- 短词（< 3 字）在 FTS 生效时补标题 LIKE 约束（不再被静默丢弃）。
"""

import datetime
import importlib.util
import os
import sqlite3
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from models.db import ArticleRecord, UserRecord  # noqa: E402
from services import accounts as accounts_service  # noqa: E402
from storage.fts import (  # noqa: E402
    _NORMALIZE_MAP,
    _sql_normalize_expr,
    build_match_query,
    build_search_components,
    drop_fts,
    ensure_fts,
    fts_available,
    fts_search_ids,
    normalize_for_search,
    rebuild_fts_normalized,
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
            has_content=True, content=content,
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


# ------------------------------------------------------------ Unicode 归一化（issue #94）

_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "alembic", "versions",
    "7ec8c19b1356_normalize_articles_fts_unicode.py",
)


def _legacy_migration_module():
    """加载归一化迁移模块，复用其中的「归一化之前」原文 DDL 模拟存量旧库。"""
    spec = importlib.util.spec_from_file_location("_fts_norm_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_legacy_fts(conn):
    """把库里的 FTS 换成归一化之前的形态：原文 trigger + 'rebuild' 原文索引。"""
    legacy = _legacy_migration_module()
    drop_fts(conn)
    conn.exec_driver_sql(legacy._LEGACY_CREATE)
    for ddl in legacy._LEGACY_TRIGGERS:
        conn.exec_driver_sql(ddl)
    conn.exec_driver_sql("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")


def _raw_match_count(engine, phrase):
    """绕过查询端归一化，直接对索引发 MATCH，用来观察索引里实际存的形式。"""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM articles_fts WHERE articles_fts MATCH :q"),
            {"q": '"' + phrase + '"'},
        ).scalar_one()


def test_python_and_sql_normalization_use_identical_mapping():
    """两端同源：任一码点经 Python translate 与 SQLite REPLACE 链结果逐字一致。"""
    sample = "x" + "".join(chr(cp) for cp in _NORMALIZE_MAP) + "y"
    conn = sqlite3.connect(":memory:")
    try:
        (sql_result,) = conn.execute(f"SELECT {_sql_normalize_expr('?')}", (sample,)).fetchone()
    finally:
        conn.close()
    assert sql_result == normalize_for_search(sample)
    assert all(ord(ch) < 128 for ch in sql_result)
    # 每个替换都保持字符数不变（trigram 依赖长度）
    assert all(len(repl) == 1 for repl in _NORMALIZE_MAP.values())


def test_build_search_components_normalizes_and_splits_short_words():
    assert build_search_components("AI agent 2026") == ('"agent" AND "2026"', ["AI"])
    assert build_search_components("AI as") == (None, ["AI", "as"])
    assert build_search_components("AI\u2011Native agent") == ('"AI-Native" AND "agent"', [])
    # 全角空格也是分隔符
    assert build_search_components("AI\u3000Agent") == ('"Agent"', ["AI"])
    assert build_search_components("") == (None, [])
    assert build_search_components(None) == (None, [])
    # build_match_query 与之同源
    assert build_match_query("AI\u2011Native") == '"AI-Native"'


def test_unicode_variants_match_ascii_queries_both_ways(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "u1", "AI\u2011Native \u2018DeepSeek\u2019 Architecture", "body mentions Claude\uff1a3.5")
    _add(sink, "u2", "Plain ASCII-Hyphen title", "nothing special")
    # 索引里的排版变体 ← 键盘 ASCII 输入
    assert _title_ids(sink, "AI-Native") == {"u1"}
    assert _title_ids(sink, "'DeepSeek'") == {"u1"}
    assert _title_ids(sink, "Claude:3.5") == {"u1"}
    # 粘贴进来的排版变体 → 也能搜到（查询端同样归一化）
    assert _title_ids(sink, "AI\u2011Native") == {"u1"}
    assert _title_ids(sink, "ASCII\u2013Hyphen") == {"u2"}


def test_unicode_update_and_delete_leave_no_stale_index_rows(tmp_path):
    """'delete' 指令必须带入索引时的（归一化）旧值，否则旧 token 残留在索引里。"""
    sink = _sink(tmp_path)
    _add(sink, "d1", "AI\u2011Native first", "body\u2014text")
    assert _raw_match_count(sink.engine, "AI-Native") == 1

    with Session(sink.engine) as session:
        rec = session.get(ArticleRecord, "d1")
        rec.title = "Renamed\u2011Title"
        session.add(rec)
        session.commit()
    assert _raw_match_count(sink.engine, "AI-Native") == 0
    assert _title_ids(sink, "Renamed-Title") == {"d1"}

    with Session(sink.engine) as session:
        session.delete(session.get(ArticleRecord, "d1"))
        session.commit()
    assert _raw_match_count(sink.engine, "Renamed-Title") == 0
    assert _raw_match_count(sink.engine, "body-text") == 0


def test_ensure_fts_does_not_swap_triggers_on_legacy_index(tmp_path):
    """存量旧索引上 ensure_fts 不得就地换 trigger（换了会让 'delete' 撤不干净）。"""
    sink = _sink(tmp_path)
    _add(sink, "l1", "AI\u2011Native legacy", "body")
    with sink.engine.begin() as conn:
        _install_legacy_fts(conn)
    assert ensure_fts(sink.engine) is True
    with sink.engine.connect() as conn:
        trigger_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='articles_fts_ai'")
        ).scalar_one()
    assert "REPLACE" not in trigger_sql


def test_rebuild_fts_normalized_upgrades_legacy_index(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "r1", "AI\u2011Native Systems", "body content")
    with sink.engine.begin() as conn:
        _install_legacy_fts(conn)
    # 旧索引存的是原文：ASCII 查询搜不中
    assert _title_ids(sink, "AI-Native") == set()

    assert rebuild_fts_normalized(sink.engine) is True
    assert _title_ids(sink, "AI-Native") == {"r1"}
    assert _raw_match_count(sink.engine, "AI\u2011Native") == 0  # 索引里已无原始变体


def test_normalization_migration_upgrade_and_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine

    from storage.migrations import make_alembic_config

    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    config = make_alembic_config(url)
    command.upgrade(config, "d4e8a1b7c603")
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            session.add(ArticleRecord(
                id="m1", title="AI\u2011Native Systems", content_type="web_article",
                source_id="src_a", source_url="http://x", publish_date="2026-06-01",
                fetched_date="2026-06-01", has_content=True, content="body",
            ))
            session.commit()
        with engine.begin() as conn:
            _install_legacy_fts(conn)  # 模拟生产存量库：原文 trigger + 原文索引
        assert _raw_match_count(engine, "AI-Native") == 0

        command.upgrade(config, "head")
        assert _raw_match_count(engine, "AI-Native") == 1
        with Session(engine) as session:
            assert len(fts_search_ids(session, "AI-Native")) == 1

        command.downgrade(config, "d4e8a1b7c603")
        with engine.connect() as conn:
            trigger_sql = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='articles_fts_au'")
            ).scalar_one()
        assert "REPLACE" not in trigger_sql
        assert _raw_match_count(engine, "AI\u2011Native") == 1  # 恢复为原文索引
    finally:
        engine.dispose()


# ------------------------------------------------------------ 短词补标题 LIKE

def test_short_words_constrain_fts_results_by_title(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "m1", "AI Agent Frameworks", "orchestrating autonomous entities")
    _add(sink, "m2", "Only Agent Frameworks", "orchestrating autonomous entities")
    _add(sink, "m3", "Weekly roundup", "agent tooling notes")
    # 修复前：「AI」被 build_match_query 丢弃，三篇都命中
    assert _title_ids(sink, "AI agent") == {"m1"}
    # 只有长词时行为不变（正文命中仍算）
    assert _title_ids(sink, "agent") == {"m1", "m2", "m3"}
    # 全是短词：FTS 不可用 → 原整串标题 LIKE，行为不变
    assert _title_ids(sink, "AI") == {"m1"}


def test_short_word_like_treats_wildcards_literally(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "w1", "Agent growth 50% faster", "body")
    _add(sink, "w2", "Agent growth report", "body")
    # 「%」作为短词必须按字面匹配，而不是 LIKE 通配符
    assert _title_ids(sink, "% agent") == {"w1"}
    assert _title_ids(sink, "_ agent") == set()
