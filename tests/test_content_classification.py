"""内容类型 / 事件类型推导与词表(issue #178 第一波)。

覆盖三件事:
1. 词表与分析体裁闭集逐项对齐(新增体裁必须在这里显式归类);
2. ``classify_item`` 的判定序:来源先验 → 数据结构 → 链接形状 → 体裁 → 未知;
3. SQL 镜像 ``kind_expr`` / ``kind_condition`` 与 Python 推导逐条对拍(含穷举组合),以及 /api/articles 的 kind 筛选与分面。
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
    assert cc.parse_kind_filter("tutorial, paper ,PAPER") == ["paper", "tutorial"]
    assert cc.parse_kind_filter("") == []
    assert cc.parse_kind_filter(None) == []
    # 任一非法值即拒绝,不静默丢弃(codex R1 #10)
    with pytest.raises(ValueError):
        cc.parse_kind_filter("paper,bogus")


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


def test_paper_url_rule_is_anchored_to_scheme_host_and_path():
    """只认 URL 开头的论文地址;查询串里引用的、相似域名的都不算(codex R1 #5)。"""
    for url in (
        "https://arxiv.org/abs/2609.01234?context=cs",
        "HTTP://Export.ArXiv.org/pdf/2609.1v2#page=3",
        "https://hf.co/papers/2609.1",
        "https://openreview.net/forum?id=abc",
    ):
        assert cc.is_paper_url(url), url
    for url in (
        "https://news.test/story?ref=https://arxiv.org/abs/1",
        "https://news.test/#https://huggingface.co/papers/1",
        "https://notarxiv.org/abs/1",
        "https://arxiv.org.evil.test/abs/1",
        "https://arxiv.org/list/cs.AI/recent",
        "ftp://arxiv.org/abs/1",
        "",
        None,
    ):
        assert not cc.is_paper_url(url), url
    # 前缀里不得出现 LIKE 通配符,SQL 侧才与 startswith 逐字等价
    assert not any(ch in prefix for prefix in cc.PAPER_URL_PREFIXES for ch in "%_")


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
    ("p4", "blog", "rss_article", "HTTPS://ArXiv.org/abs/2609.4?v=2", "industry_news"),
    ("n3", "blog", "rss_article", "https://news.test/s?ref=https://arxiv.org/abs/1", "industry_news"),
    ("n4", "blog", "rss_article", "https://notarxiv.org/abs/1", "industry_news"),
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


def test_sql_expression_matches_python_on_exhaustive_combinations(tmp_path):
    """由规则表生成的穷举组合:源 × 数据结构 × URL × (体裁, 模型类型)(含无分析行),逐条比较
    ``kind_expr`` 与 ``classify_item``;规则表新增取值会自动进入组合(codex R1 #8)。第二波起
    模型给的类型排在规则之后、体裁之前,组合里含每个合法类型与一个非法值。"""
    from sqlalchemy import func, insert

    from models.db import ArticleAnalysisRecord, ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sources = sorted(RULES) + ["plain_blog"]
    content_types = sorted(
        set(cc.CONTENT_TYPE_DEFAULT_KINDS) | set(cc.URL_RULE_EXCLUDED_CONTENT_TYPES) | {"rss_article", "web_article"}
    )
    first_prefix_per_host = {}
    for prefix in cc.PAPER_URL_PREFIXES:
        first_prefix_per_host.setdefault(prefix.split("://", 1)[1].split("/", 1)[0], prefix)
    urls = [f"{prefix}2609.1" for prefix in first_prefix_per_host.values()] + [
        "HTTPS://ArXiv.ORG/ABS/2609.1?v=2",
        "https://news.test/s?ref=https://arxiv.org/abs/1",
        "https://notarxiv.org/abs/1",
        "https://arxiv.org/list/cs.AI",
        "https://blog.test/1",
        "",
    ]
    genres = sorted(cc.GENRE_KIND_EVENT) + [None]
    llm_kinds = list(cc.ITEM_KINDS) + ["bogus"]
    analysis_variants = [(genre, None) for genre in genres] + [
        (genre, llm_kind) for genre in (None, "research_paper", "opinion") for llm_kind in llm_kinds
    ]

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'exhaustive.db'}")
    articles, analyses, expected = [], [], {}
    for source_id in sources:
        for content_type in content_types:
            for url_index, url in enumerate(urls):
                for genre, llm_kind in analysis_variants:
                    article_id = f"{source_id}|{content_type}|{url_index}|{genre}|{llm_kind}"
                    articles.append({
                        "id": article_id, "title": "t", "content_type": content_type, "source_id": source_id,
                        "source_url": url, "publish_date": "2026-09-01T00:00:00",
                        "fetched_date": "2026-09-01T00:00:00", "has_content": True, "content": "",
                        "extensions_json": "{}",
                    })
                    if genre is not None or llm_kind is not None:
                        analyses.append({
                            "article_id": article_id, "status": "succeeded", "content_genre": genre,
                            "content_kind": llm_kind,
                            "created_at": "2026-09-01T00:00:00", "updated_at": "2026-09-01T00:00:00",
                        })
                    expected[article_id] = cc.classify_item(
                        source_id=source_id, source_url=url, content_type=content_type,
                        content_genre=genre, source_kinds=RULES, content_kind=llm_kind,
                    ).kind
    with Session(sink.engine) as session:
        session.execute(insert(ArticleRecord.__table__), articles)
        session.execute(insert(ArticleAnalysisRecord.__table__), analyses)
        session.commit()
        kind_value = cc.kind_expr(RULES)
        actual = dict(session.execute(select(ArticleRecord.id, kind_value)).all())
        grouped = dict(session.execute(
            select(kind_value, func.count(ArticleRecord.id)).group_by(kind_value)
        ).all())

    mismatches = [(aid, expected[aid], actual.get(aid)) for aid in expected if expected[aid] != actual.get(aid)]
    assert not mismatches, mismatches[:10]
    assert set(filter(None, expected.values())) == set(cc.ITEM_KINDS)
    for kind in cc.ITEM_KINDS:
        assert grouped.get(kind, 0) == sum(1 for value in expected.values() if value == kind), kind


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
        assert client.get("/api/articles", params={**base, "kind": "paper,bogus"}).status_code == 400
        # 不传 kind_facets 时不附分面(默认响应形状不变)
        assert "kind_facets" not in client.get("/api/articles", params=base).json()
        # 单独请求分面(不带 include_total)也拿到分页对象与分面;两者都不传时仍是数组(codex R1 #7)
        bare = {"include_content": "false", "shape": "article"}
        alone = client.get("/api/articles", params={**bare, "kind_facets": "true"}).json()
        assert alone["total"] == 4 and len(alone["kind_facets"]) == 6
        assert isinstance(client.get("/api/articles", params=bare).json(), list)


def test_article_facets_carry_content_type_labels(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "facet_labels.db")
    _seed(sink.engine, [("a", "web_anthropic_news", "web_article", "https://anthropic.com/x", None)])
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        facets = client.get("/api/articles/facets").json()
        assert {"value": "web_article", "count": 1, "label": "网页条目"} in facets["content_types"]


def test_article_analysis_endpoint_carries_kind_and_dual_scores(monkeypatch, tmp_path):
    """issue #178 第二波:单篇分析端点与列表同一推导与投影(含模型类型与内容价值)。"""
    from models.db import ArticleAnalysisRecord

    app_module, sink = _make_app(monkeypatch, tmp_path, "analysis_endpoint.db")
    _seed(sink.engine, [("tu", "plain_blog", "rss_article", "https://blog.test/x", "product_update")])
    with Session(sink.engine) as session:
        record = session.get(ArticleAnalysisRecord, "tu")
        record.content_kind, record.content_value = "tutorial", 7.4
        record.content_value_reason, record.content_value_version = "步骤完整", "content-value-v2"
        session.add(record)
        session.commit()
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        data = client.get("/api/articles/tu/analysis").json()
        listed = client.get("/api/articles", params={"include_content": "false"}).json()
    assert (data["kind"], data["kind_basis"]) == ("tutorial", "llm")
    assert (data["news_value"], data["content_value"], data["content_value_reason"]) == (6.0, 7.4, "步骤完整")
    assert (data["primary_dimension"], data["primary_score"]) == ("content", 7.4)
    item = next(row for row in listed if row["id"] == "tu")
    for key in ("kind", "news_value", "content_value", "primary_dimension", "primary_score"):
        assert item[key] == data[key], key
