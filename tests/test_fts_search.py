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
import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import literal_column  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    ReaderSubscriptionRecord,
    UserRecord,
)
from services import accounts as accounts_service  # noqa: E402
from services import source_visibility as source_visibility_service  # noqa: E402
from services.article_display_tags import sync_article_tags_text  # noqa: E402
from storage.fts import (  # noqa: E402
    build_match_query,
    build_search_components,
    ensure_fts,
    fts_available,
    fts_search_ids,
    fts_search_ranked,
    normalize_for_search,
    rebuild_fts_normalized,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from api.articles_view import apply_article_query_filters  # noqa: E402


def _sink(tmp_path, name="fts.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _add(sink, rid, title, content, tags="", content_type="web_article", source_id="src_a"):
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id=rid, title=title, content_type=content_type, source_id=source_id,
            source_url="http://x", publish_date="2026-06-01", fetched_date="2026-06-01",
            has_content=True, content=content, tags=tags,
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


# ------------------------------------------------------------ Unicode 归一化 & 短词组合测试

def test_unicode_normalization_search_hit(tmp_path):
    sink = _sink(tmp_path)
    # 标题含排版连字符 U+2011（‑）与弯引号 U+2018 / U+2019
    _add(sink, "u1", "AI\u2011Native \u2018DeepSeek\u2019 Architecture", "body content mentions Claude\uff1a3.5")
    # 普通 ASCII 减号 "AI-Native" 能命中
    assert _title_ids(sink, "AI-Native") == {"u1"}
    # 特殊符号也能命中
    assert _title_ids(sink, "AI\u2011Native") == {"u1"}
    # 弯引号转直引号能命中
    assert _title_ids(sink, "'DeepSeek'") == {"u1"}
    # 正文全角冒号 U+FF1A 能通过半角冒号搜到
    assert _title_ids(sink, "Claude:3.5") == {"u1"}


def test_build_search_components_and_normalize():
    match, short = build_search_components("AI agent 2026")
    assert match == '"agent" AND "2026"'
    assert short == ["AI"]

    # 全短词
    match, short = build_search_components("AI as")
    assert match is None
    assert short == ["AI", "as"]

    # 带 Unicode 特殊字符
    match, short = build_search_components("AI\u2011Native agent")
    assert match == '"AI-Native" AND "agent"'
    assert short == []

    # 空串
    match, short = build_search_components("")
    assert match is None
    assert short == []


def test_search_mixed_long_and_short_words(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "m1", "AI Agent Frameworks", "orchestrating multiple autonomous entities")
    _add(sink, "m2", "Only Agent Frameworks", "orchestrating multiple autonomous entities")
    # "AI agent"：长词 "agent" 走 FTS，短词 "AI" 走标题 LIKE
    assert _title_ids(sink, "AI agent") == {"m1"}
    # 单独搜短词 "AI"
    assert _title_ids(sink, "AI") == {"m1"}


def test_rebuild_fts_normalized(tmp_path):
    sink = _sink(tmp_path)
    _add(sink, "r1", "AI\u2011Native Systems", "body content")
    assert rebuild_fts_normalized(sink.engine) is True
    assert _title_ids(sink, "AI-Native") == {"r1"}


def _seed_user(engine, username="reader", role="user"):
    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        session.add(UserRecord(
            username=username, password_hash=accounts_service.hash_password("password"),
            role=role, is_active=True, created_at=now, updated_at=now,
        ))
        session.commit()


# ------------------------------------------------------------ 标签搜索 & 权重 & 隔离

def test_tag_hit(tmp_path):
    """关键词仅在 tags 中出现，标题与正文均不包含，FTS 仍能命中。"""
    sink = _sink(tmp_path)
    _add(sink, "tag1", "Generic headline", "general content", tags="kubernetes cloud-native")
    _add(sink, "tag2", "Another headline", "other content", tags="machine-learning")
    assert _title_ids(sink, "kubernetes") == {"tag1"}
    assert _title_ids(sink, "cloud-native") == {"tag1"}
    assert _title_ids(sink, "machine-learning") == {"tag2"}


def test_bm25_weights_title_100_tag_10_content_1(tmp_path):
    """权重检验：标题 100 > 标签 10 > 正文 1。

    SQLite FTS5 bm25 的分数值越小表示越相关（即负数或小数值），
    因此 rank(title) < rank(tag) < rank(content)。
    同时 fts_search_ids 按照 rank 升序排列，排序结果严格为 [title, tag, content]。
    """
    sink = _sink(tmp_path)
    # 三篇文章分别只在 标题、标签、正文 中包含唯一匹配词 "Dorami"
    _add(sink, "art_title", "Dorami Systems", "General description", tags="software tooling")
    _add(sink, "art_tag", "Weekly Updates", "General description", tags="Dorami tooling")
    _add(sink, "art_content", "Weekly Updates", "Mentions Dorami in content body", tags="software tooling")

    with Session(sink.engine) as session:
        ranked = fts_search_ranked(session, "Dorami")
        assert ranked is not None and len(ranked) == 3

        r_title = session.exec(select(literal_column("articles.rowid")).where(ArticleRecord.id == "art_title")).one()
        r_tag = session.exec(select(literal_column("articles.rowid")).where(ArticleRecord.id == "art_tag")).one()
        r_content = session.exec(select(literal_column("articles.rowid")).where(ArticleRecord.id == "art_content")).one()

        # SQLite bm25: 越小越相关
        assert ranked[r_title] < ranked[r_tag] < ranked[r_content]
        # fts_search_ids 顺序保持相关性排序（最相关在前）
        ids = fts_search_ids(session, "Dorami")
        assert ids == [r_title, r_tag, r_content]


def test_short_query_matches_tags(tmp_path):
    """短词（< 3 字符，如 'AI'）回退匹配 title 或 tags，但不匹配 content。"""
    sink = _sink(tmp_path)
    _add(sink, "s_title", "AI Architecture", "body text", tags="tech")
    _add(sink, "s_tag", "Modern Infrastructure", "body text", tags="AI devops")
    _add(sink, "s_content", "Modern Infrastructure", "mentions AI in text", tags="cloud")

    # 'AI' 长度 2 < 3，走短词 title/tags LIKE
    assert _title_ids(sink, "AI") == {"s_title", "s_tag"}


def test_tag_triggers_sync(tmp_path):
    """标签更新或删除时，SQLite triggers 实时同步 FTS 表。"""
    sink = _sink(tmp_path)
    _add(sink, "t_dyn", "Language Guide", "content", tags="rust systems")
    assert _title_ids(sink, "rust") == {"t_dyn"}

    # 更新 tags
    with Session(sink.engine) as session:
        rec = session.get(ArticleRecord, "t_dyn")
        rec.tags = "golang systems"
        session.add(rec)
        session.commit()

    assert _title_ids(sink, "rust") == set()
    assert _title_ids(sink, "golang") == {"t_dyn"}

    # 删除记录
    with Session(sink.engine) as session:
        rec = session.get(ArticleRecord, "t_dyn")
        session.delete(rec)
        session.commit()

    assert _title_ids(sink, "golang") == set()


def test_shape_isolation_search_articles_and_podcasts(monkeypatch, tmp_path):
    """形态隔离：搜索文章时不可与播客匹配，搜索播客时不可与文章匹配。"""
    import api.app as app_module
    sink = _sink(tmp_path, "shape.db")
    _seed_admin(sink.engine)
    _add(sink, "art_ml", "Machine Learning in Production", "article body", tags="mlops", content_type="web_article")
    _add(sink, "pod_ml", "Machine Learning Talk Show", "podcast show notes", tags="mlops", content_type="podcast_episode")
    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

        # 1. 搜索标签 mlops, shape=article -> 只返回文章
        res_art = client.get("/api/articles?search=mlops&shape=article").json()
        assert {r["id"] for r in res_art} == {"art_ml"}

        # 2. 搜索标签 mlops, shape=podcast -> 只返回播客
        res_pod = client.get("/api/articles?search=mlops&shape=podcast").json()
        assert {r["id"] for r in res_pod} == {"pod_ml"}

        # 3. 搜索标题 Machine Learning, shape 隔离同样生效
        res_art_title = client.get("/api/articles?search=Machine%20Learning&shape=article").json()
        assert {r["id"] for r in res_art_title} == {"art_ml"}
        res_pod_title = client.get("/api/articles?search=Machine%20Learning&shape=podcast").json()
        assert {r["id"] for r in res_pod_title} == {"pod_ml"}


def test_subscribed_scope_and_data_security_isolation_with_search(monkeypatch, tmp_path):
    """订阅范围限制与数据安全性隔离在搜索中依然生效。"""
    import api.app as app_module
    sink = _sink(tmp_path, "security.db")
    _seed_admin(sink.engine)
    _seed_user(sink.engine, username="reader1", role="user")

    # 订阅源 src_sub，未订阅源 src_unsub，隐藏源 src_hidden
    _add(sink, "sub_1", "Post One", "body", tags="blockchain", source_id="src_sub")
    _add(sink, "unsub_1", "Post Two", "body", tags="blockchain", source_id="src_unsub")
    _add(sink, "hidden_1", "Post Three", "body", tags="blockchain", source_id="src_hidden")

    now = datetime.datetime.now().isoformat()
    # 设置订阅与隐藏源
    with Session(sink.engine) as session:
        session.add(ReaderSubscriptionRecord(
            owner_username="reader1",
            name="Sub 1",
            filters_json=json.dumps({"source_ids": "src_sub"}),
            token_hash="fake_hash",
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
        source_visibility_service.set_source_hidden(session, "src_hidden", True)

    monkeypatch.setattr(app_module, "db_sink", sink)

    with TestClient(app_module.app) as client:
        # 普通读者登录
        client.post("/api/auth/login", json={"username": "reader1", "password": "password"})

        # subscribed_scope=only: 仅限订阅源
        res_sub_only = client.get("/api/articles?search=blockchain&subscribed_scope=only").json()
        assert {r["id"] for r in res_sub_only} == {"sub_1"}

        # subscribed_scope=off: 全站范围，隐藏源不可见
        res_all = client.get("/api/articles?search=blockchain&subscribed_scope=off").json()
        assert {r["id"] for r in res_all} == {"sub_1", "unsub_1"}
        assert "hidden_1" not in {r["id"] for r in res_all}

        # 管理员登录可以看到隐藏源
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        res_admin = client.get("/api/articles?search=blockchain&subscribed_scope=off").json()
        assert {r["id"] for r in res_admin} == {"sub_1", "unsub_1", "hidden_1"}


def test_sync_article_tags_text_updates_fts(tmp_path):
    """sync_article_tags_text 聚合规范标签与展示标签，写回 article.tags 并同步到 FTS。"""
    sink = _sink(tmp_path)
    _add(sink, "sync_art", "Title Without Tag", "Content body", tags="")
    assert _title_ids(sink, "Architecture") == set()

    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        # 添加 CMS 规范标签
        tag = CmsTagRecord(
            id=101,
            code="architecture_concept",
            kind="topic",
            name_zh="架构设计",
            name_en="Architecture",
            normalized_name="architecture",
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(tag)
        session.commit()
        session.add(ArticleTagAssignmentRecord(
            article_id="sync_art",
            tag_id=101,
            tag_kind="topic",
            is_primary=True,
            created_at=now,
            updated_at=now,
        ))
        # 添加展示标签
        session.add(ArticleAnalysisRecord(
            article_id="sync_art",
            display_tags_json='[{"name": "Microservices", "type": "extracted"}]',
            created_at=now,
            updated_at=now,
        ))
        session.commit()

        # 执行同步并提交
        sync_article_tags_text(session, "sync_art")
        session.commit()

    # 验证 tags 字段已同步且 FTS 可检索
    assert _title_ids(sink, "Architecture") == {"sync_art"}
    assert _title_ids(sink, "Microservices") == {"sync_art"}


