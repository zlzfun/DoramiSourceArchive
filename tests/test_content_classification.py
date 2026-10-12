"""内容类型 / 事件类型推导与词表(issue #178 第一波)。

覆盖三件事:
1. 词表与分析体裁闭集逐项对齐(新增体裁必须在这里显式归类);
2. ``classify_item`` 的判定序:来源先验 → 数据结构 → 链接形状 → 体裁 → 未知;
3. SQL 镜像 ``kind_condition`` 与 Python 推导逐条对拍,以及 /api/articles 的 kind 筛选与分面。
"""
import os
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import ContentGenre  # noqa: E402
from services import content_classification as cc  # noqa: E402


# ==================== 词表 ====================

def test_vocabulary_covers_every_controlled_genre():
    genres = {genre.value for genre in ContentGenre}
    assert set(cc.GENRE_KIND_EVENT) == genres
    assert set(cc.GENRE_LABELS) == genres
    assert set(cc.PERSONAL_DIGEST_GENRE_SECTIONS) == genres
    assert set(cc.DAILY_BRIEF_GENRE_CLASSIFICATIONS) == genres
    for kind, event in cc.GENRE_KIND_EVENT.values():
        assert kind in cc.ITEM_KIND_LABELS
        assert event is None or event in cc.EVENT_TYPE_LABELS


def test_daily_brief_classification_contract_unchanged():
    """公共日报分类是神灯流水线的对外契约:收拢定义后取值必须与收拢前逐字一致。"""
    from services import daily_brief as db

    assert db.CLASSIFICATION_FROM_GENRE is cc.DAILY_BRIEF_GENRE_CLASSIFICATIONS
    assert db.classification_from_genre("tutorial") == "行业资讯"
    assert db.classification_from_genre("opinion") == "行业资讯"
    assert db.classification_from_genre("research_paper") == "学术论文"
    assert db.classification_from_genre("other") == "资讯聚合"


def test_personal_digest_sections_unchanged():
    from services import digest_selection

    assert digest_selection.GENRE_SECTIONS is cc.PERSONAL_DIGEST_GENRE_SECTIONS
    assert digest_selection.section_for_genre("tutorial") == "工程实践"
    assert digest_selection.section_for_genre("opinion") == "观点洞察"
    assert digest_selection.section_for_genre(None) == "其它"


def test_content_type_labels_are_data_structure_names():
    """rss / web 只说明怎么抓进来,不再叫「资讯文章」。"""
    assert cc.content_type_label("rss_article") == "RSS 条目"
    assert cc.content_type_label("web_article") == "网页条目"
    assert cc.content_type_label("future_type") == "future_type"
    assert cc.content_type_label(None) == ""


def test_parse_kind_filter():
    assert cc.parse_kind_filter("tutorial, paper ,PAPER,bogus") == ["paper", "tutorial"]
    assert cc.parse_kind_filter("") == []
    assert cc.parse_kind_filter(None) == []


# ==================== Python 推导 ====================

RULES = {"hf_papers": "paper", "cc_changelog": "release", "hf_models": "project", "dorami_daily_brief": "digest"}


def _classify(source_id, content_type="web_article", genre=None, url="https://example.test/x"):
    return cc.classify_item(
        source_id=source_id,
        source_url=url,
        content_type=content_type,
        content_genre=genre,
        source_kinds=RULES,
    )


def test_source_prior_wins_over_genre():
    result = _classify("hf_papers", genre="research_paper")
    assert (result.kind, result.kind_basis, result.event_type) == ("paper", "source", None)
    assert result.kind_label == "论文"


def test_release_keeps_event_from_genre():
    result = _classify("cc_changelog", genre="product_update")
    assert (result.kind, result.kind_basis, result.event_type) == ("release", "source", "product_update")
    assert result.as_projection() == {
        "kind": "release",
        "kind_label": "版本更新",
        "kind_basis": "source",
        "event_type": "product_update",
        "event_label": "产品更新",
    }


def test_media_report_on_research_is_news_not_paper():
    result = _classify("qbitai", genre="research_paper")
    assert (result.kind, result.kind_basis, result.event_type) == ("news", "genre", "research")


def test_paper_url_rule_but_not_for_social_posts():
    url = "https://arxiv.org/abs/2609.01234"
    assert _classify("rss_hn_ai", "rss_article", "social_discussion", url).kind == "paper"
    assert _classify("rss_hn_ai", "rss_article", "social_discussion", url).kind_basis == "url"
    social = _classify("x_someone", "social_post", "social_discussion", url)
    assert (social.kind, social.kind_basis) == ("news", "genre")


def test_content_type_fallback_for_unregistered_sources():
    result = _classify("legacy_releases", "github_release", "product_update")
    assert (result.kind, result.kind_basis, result.event_type) == ("release", "content_type", "product_update")
    assert _classify("legacy_models", "hf_model").kind == "project"


def test_non_event_kinds_drop_event_type():
    assert _classify("blog", genre="tutorial").event_type is None
    assert _classify("blog", genre="opinion").kind == "opinion"
    assert _classify("weekly", genre="aggregation").kind == "digest"


def test_unknown_without_analysis_or_rule():
    result = _classify("blog", genre=None)
    assert (result.kind, result.kind_basis, result.event_type) == (None, "unknown", None)
    assert result.as_projection()["kind_label"] is None


def test_daily_brief_source_is_digest():
    assert _classify("dorami_daily_brief", "daily_brief").kind == "digest"
    assert cc.source_default_kinds()["dorami_daily_brief"] == "digest"


def test_registry_priors_cover_former_bulletin_sources():
    kinds = cc.source_default_kinds()
    assert kinds["web_huggingface_daily_papers"] == "paper"
    assert kinds["docs_claude_code_changelog"] == "release"
    assert kinds["github_opencode_releases"] == "release"
    assert kinds["hf_qwen_models"] == "project"
    assert kinds["github_trending_daily"] == "project"
    assert "web_anthropic_news" not in kinds


# ==================== SQL 镜像对拍 ====================

FIXTURE_ROWS = [
    # (id, source_id, content_type, url, genre)
    ("p1", "hf_papers", "web_article", "https://huggingface.co/papers/1", "research_paper"),
    ("p2", "rss_hn_ai", "rss_article", "https://arxiv.org/pdf/2609.1", "social_discussion"),
    ("p3", "rss_hn_ai", "rss_article", "https://openreview.net/forum?id=x", None),
    ("n1", "qbitai", "web_article", "https://qbitai.com/1", "research_paper"),
    ("n2", "anthropic", "web_article", "https://anthropic.com/1", "model_release"),
    ("s1", "x_someone", "social_post", "https://arxiv.org/abs/2609.2", "social_discussion"),
    ("r1", "cc_changelog", "web_article", "https://code.claude.com/1", "product_update"),
    ("r2", "cc_changelog", "web_article", "https://arxiv.org/abs/2609.3", None),
    ("r3", "legacy_releases", "github_release", "https://github.com/a/b/releases/1", None),
    ("j1", "hf_models", "hf_model", "https://huggingface.co/m", "model_release"),
    ("j2", "legacy_models", "huggingface_model", "", None),
    ("t1", "blog", "rss_article", "https://blog.test/1", "tutorial"),
    ("o1", "blog", "rss_article", "https://blog.test/2", "opinion"),
    ("d1", "weekly", "rss_article", "https://weekly.test/1", "aggregation"),
    ("d2", "dorami_daily_brief", "daily_brief", "", None),
    ("u1", "blog", "rss_article", "https://blog.test/3", None),
    ("u2", "blog", "rss_article", "", None),
]


@pytest.fixture()
def fixture_engine(tmp_path):
    from models.db import ArticleAnalysisRecord, ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'kinds.db'}")
    with Session(sink.engine) as session:
        for article_id, source_id, content_type, url, genre in FIXTURE_ROWS:
            session.add(ArticleRecord(
                id=article_id,
                title=f"Title {article_id}",
                content_type=content_type,
                source_id=source_id,
                source_url=url,
                publish_date="2026-09-01T00:00:00",
                fetched_date="2026-09-01T00:00:00",
                has_content=True,
                content="body",
                extensions_json="{}",
            ))
        session.commit()
        for article_id, _source_id, _content_type, _url, genre in FIXTURE_ROWS:
            if genre:
                session.add(ArticleAnalysisRecord(
                    article_id=article_id,
                    status="succeeded",
                    quality_score=5.0,
                    content_genre=genre,
                    created_at="2026-09-01T00:00:00",
                    updated_at="2026-09-01T00:00:00",
                ))
        session.commit()
    return sink.engine


def test_sql_condition_matches_python_classification(fixture_engine):
    from models.db import ArticleRecord

    expected: dict[str, set[str]] = {}
    for article_id, source_id, content_type, url, genre in FIXTURE_ROWS:
        result = cc.classify_item(
            source_id=source_id, source_url=url, content_type=content_type,
            content_genre=genre, source_kinds=RULES,
        )
        if result.kind:
            expected.setdefault(result.kind, set()).add(article_id)
    # 夹具覆盖每一种内容类型,对拍才有意义
    assert set(expected) == set(cc.ITEM_KINDS)

    with Session(fixture_engine) as session:
        for kind in cc.ITEM_KINDS:
            actual = set(session.exec(
                select(ArticleRecord.id).where(cc.kind_condition([kind], RULES))
            ).all())
            assert actual == expected[kind], kind
        both = set(session.exec(
            select(ArticleRecord.id).where(cc.kind_condition(["paper", "tutorial"], RULES))
        ).all())
        assert both == expected["paper"] | expected["tutorial"]
        assert session.exec(
            select(ArticleRecord.id).where(cc.kind_condition([], RULES))
        ).all() == []


# ==================== /api/articles ====================

def _make_app(monkeypatch, tmp_path, name):
    import datetime

    import api.app as app_module
    from config import RuntimeConfig
    from models.db import UserRecord
    from services import accounts as accounts_service
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        session.add(UserRecord(
            username="admin",
            password_hash=accounts_service.hash_password("admin"),
            role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return app_module, sink


def _seed(engine, rows):
    from models.db import ArticleAnalysisRecord, ArticleRecord

    with Session(engine) as session:
        for article_id, source_id, content_type, url, genre in rows:
            session.add(ArticleRecord(
                id=article_id,
                title=f"Title {article_id}",
                content_type=content_type,
                source_id=source_id,
                source_url=url,
                publish_date="2026-09-01T00:00:00",
                fetched_date="2026-09-01T00:00:00",
                has_content=True,
                content="body",
                extensions_json="{}",
            ))
        session.commit()
        for article_id, *_rest, genre in rows:
            if genre:
                session.add(ArticleAnalysisRecord(
                    article_id=article_id,
                    status="succeeded",
                    quality_score=6.0,
                    content_genre=genre,
                    created_at="2026-09-01T00:00:00",
                    updated_at="2026-09-01T00:00:00",
                ))
        session.commit()


def test_articles_kind_filter_facets_and_projection(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "kind_api.db")
    _seed(sink.engine, [
        ("hp", "web_huggingface_daily_papers", "web_article", "https://huggingface.co/papers/1", "research_paper"),
        ("cl", "docs_claude_code_changelog", "web_article", "https://code.claude.com/x", "product_update"),
        ("tu", "web_claude_blog", "web_article", "https://claude.com/blog/x", "tutorial"),
        ("nw", "web_anthropic_news", "web_article", "https://anthropic.com/news/x", "model_release"),
        ("sx", "x_openai", "social_post", "https://x.com/openai/status/1", "social_discussion"),
    ])

    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        base = {"include_total": "true", "include_content": "false", "shape": "article"}

        data = client.get("/api/articles", params={**base, "kind_facets": "true"}).json()
        assert {item["id"] for item in data["items"]} == {"hp", "cl", "tu", "nw"}
        assert data["kind_facets"] == [
            {"kind": "paper", "label": "论文", "count": 1},
            {"kind": "tutorial", "label": "教程", "count": 1},
            {"kind": "opinion", "label": "观点", "count": 0},
            {"kind": "release", "label": "版本更新", "count": 1},
            {"kind": "project", "label": "项目", "count": 0},
            {"kind": "digest", "label": "汇编", "count": 0},
        ]
        by_id = {item["id"]: item for item in data["items"]}
        assert by_id["hp"]["kind"] == "paper" and by_id["hp"]["kind_label"] == "论文"
        assert by_id["cl"]["kind"] == "release" and by_id["cl"]["event_label"] == "产品更新"
        assert by_id["nw"]["kind"] == "news" and by_id["nw"]["event_type"] == "model_release"
        assert by_id["nw"]["content_genre_label"] == "模型发布"
        assert by_id["hp"]["content_type_label"] == "网页条目"

        # 选中「论文」后,分面仍按「除 kind 外」的范围计数——其它选项不消失
        papers = client.get("/api/articles", params={**base, "kind": "paper", "kind_facets": "true"}).json()
        assert [item["id"] for item in papers["items"]] == ["hp"]
        assert papers["total"] == 1
        assert {f["kind"]: f["count"] for f in papers["kind_facets"]}["release"] == 1

        mixed = client.get("/api/articles", params={**base, "kind": "release,tutorial"}).json()
        assert {item["id"] for item in mixed["items"]} == {"cl", "tu"}

        assert client.get("/api/articles", params={**base, "kind": "bogus"}).status_code == 400
        # 不传 kind_facets 时不附分面(默认响应形状不变)
        assert "kind_facets" not in client.get("/api/articles", params=base).json()


def test_article_facets_carry_content_type_labels(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "facet_labels.db")
    _seed(sink.engine, [("a", "web_anthropic_news", "web_article", "https://anthropic.com/x", None)])
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        facets = client.get("/api/articles/facets").json()
        assert {"value": "web_article", "count": 1, "label": "网页条目"} in facets["content_types"]
