"""关键词检索命中标签（issue #94）全链路单测。

覆盖：
- /api/articles?search= 合并命中标题、正文（FTS5）与标签（规范标签中英文名/别名、
  读时合并/激活候选投影、灵活标签、旧版 Candidate evidence），逐条回填 search_tag_hits；
- 治理边界：已拒绝/已删除候选、已停用/退役规范标签绝不被搜出；
- 跨字段多词 AND（词 1 命中标签 + 词 2 命中正文）、短词（< 3 字如 AI）命中标签、
  Unicode 排版变体（U+2011）与 \\uXXXX 转义 JSON 兼容、SQL LIKE 通配符字面转义；
- 全检索面一致：收藏列表 /api/reader/favorites、Feed 交付、MCP search_articles、
  AI 订阅问答召回 reader_search.fetch_candidates，以及 FTS 不可用降级路径。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api.articles_view import apply_article_query_filters  # noqa: E402
from api.feed_service import feed_articles_for_owner, query_subscription_articles  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    ReaderFavoriteRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
)
from services import accounts as accounts_service  # noqa: E402
from services import reader_search  # noqa: E402
from services.taxonomy import create_tag, delete_candidate, normalize_label  # noqa: E402
from storage.fts import drop_fts  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from tests.conftest import seed_default_accounts  # noqa: E402


def _now_iso(days_ago: int = 0) -> str:
    return (
        dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        - dt.timedelta(days=days_ago)
    ).isoformat()


def _day_str(days_ago: int = 0) -> str:
    return (
        dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        - dt.timedelta(days=days_ago)
    ).strftime("%Y-%m-%d")


def _sink(tmp_path, name: str = "tag_search.db") -> DatabaseStorage:
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _add_article(
    session: Session,
    article_id: str,
    title: str,
    content: str,
    *,
    source_id: str = "src_tech",
    days_ago: int = 0,
) -> ArticleRecord:
    stamp = _now_iso(days_ago)
    rec = ArticleRecord(
        id=article_id,
        title=title,
        content_type="web_article",
        source_id=source_id,
        source_url=f"https://example.test/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        has_content=True,
        content=content,
        extensions_json="{}",
    )
    session.add(rec)
    session.flush()
    return rec


def _assign_tag(session: Session, article_id: str, tag, *, is_primary: bool = True) -> None:
    stamp = _now_iso()
    session.add(ArticleTagAssignmentRecord(
        article_id=article_id,
        tag_id=int(tag.id),
        tag_kind=tag.kind,
        is_primary=is_primary,
        relevance=0.95,
        assignment_source="llm",
        created_at=stamp,
        updated_at=stamp,
    ))


def _add_analysis(
    session: Session,
    article_id: str,
    display_tags: list[dict],
    *,
    ensure_ascii: bool = False,
) -> None:
    stamp = _now_iso()
    session.add(ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        tagging_status="succeeded",
        quality_score=8.0,
        display_tags_json=json.dumps(display_tags, ensure_ascii=ensure_ascii),
        analyzed_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    ))


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------ /api/articles 端点级


def test_articles_search_unions_title_content_canonical_and_alias_tags(monkeypatch, tmp_path):
    """搜 Claude：同时命中标题、正文、规范标签（英文名/别名），且标注 search_tag_hits。"""
    import api.app as app_module

    sink = _sink(tmp_path, "articles_union.db")
    seed_default_accounts(sink.engine)
    stamp = _now_iso()

    with Session(sink.engine) as session:
        tag_claude = create_tag(
            session, code="entity.claude", kind="entity", entity_type="model",
            name_zh="克劳德", name_en="Claude", status="active",
        )
        tag_coding = create_tag(
            session, code="topic.agentic_coding", kind="topic",
            name_zh="智能编程", name_en="Agentic Coding", status="active",
        )
        tag_db = create_tag(
            session, code="topic.database", kind="topic",
            name_zh="数据库", name_en="Database", status="active",
        )
        session.add(CmsTagAliasRecord(
            tag_id=tag_coding.id,
            kind=tag_coding.kind,
            alias="Claude Code",
            normalized_alias=normalize_label("Claude Code"),
            created_at=stamp,
            updated_at=stamp,
        ))
        _add_article(session, "a-title", "Claude 4 发布解读", "正文未提其他词")
        _add_article(session, "a-content", "大模型周报", "本周重点评测 Claude 推理能力")
        _add_article(session, "a-tag-en", "Anthropic 新一代助手评测", "响应速度与长上下文表现亮眼")
        _assign_tag(session, "a-tag-en", tag_claude)
        _add_article(session, "a-tag-alias", "终端开发工作流实战", "自动重构与测试闭环体验")
        _assign_tag(session, "a-tag-alias", tag_coding)
        _add_article(session, "a-none", "关系型索引调优", "B-Tree 页面分裂机制")
        _assign_tag(session, "a-none", tag_db)
        session.commit()

    monkeypatch.setattr(app_module, "db_sink", sink)
    with TestClient(app_module.app) as client:
        _login(client)

        for query_term in ("Claude", "claude", "CLAUDE"):
            resp = client.get(f"/api/articles?search={query_term}&include_total=true&limit=50")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["total"] == 4
            by_id = {item["id"]: item for item in payload["items"]}
            assert set(by_id) == {"a-title", "a-content", "a-tag-en", "a-tag-alias"}
            assert by_id["a-title"]["search_tag_hits"] == []
            assert by_id["a-content"]["search_tag_hits"] == []
            assert by_id["a-tag-en"]["search_tag_hits"] == ["克劳德"]
            assert by_id["a-tag-alias"]["search_tag_hits"] == ["智能编程"]

        # 非搜索态不附带 search_tag_hits
        plain = client.get("/api/articles?include_total=true&limit=50").json()
        assert plain["total"] == 5
        assert all("search_tag_hits" not in item for item in plain["items"])


def test_tag_search_supports_flexible_merged_evidence_and_respects_governance(tmp_path):
    """覆盖灵活标签、候选合并读时投影、历史 evidence、Unicode 变体/转义，并排除已拒绝/删除/停用标签。"""
    sink = _sink(tmp_path, "governance.db")
    stamp = _now_iso()

    with Session(sink.engine) as session:
        tag_claude = create_tag(
            session, code="entity.claude", kind="entity", entity_type="model",
            name_zh="克劳德", name_en="Claude", status="active",
        )
        tag_deprecated = create_tag(
            session, code="entity.claude_old", kind="entity", entity_type="model",
            name_zh="Claude 旧版", name_en="Claude Legacy", status="deprecated",
        )
        cand_merged = CmsTagCandidateRecord(
            label="Anthropic模型",
            normalized_label=normalize_label("Anthropic模型"),
            proposed_kind="entity",
            status="merged",
            resolution_tag_id=tag_claude.id,
            first_seen_at=stamp, last_seen_at=stamp, created_at=stamp, updated_at=stamp,
        )
        cand_rejected = CmsTagCandidateRecord(
            label="Claude 谣言",
            normalized_label=normalize_label("Claude 谣言"),
            proposed_kind="topic",
            status="rejected",
            first_seen_at=stamp, last_seen_at=stamp, created_at=stamp, updated_at=stamp,
        )
        cand_deleted = CmsTagCandidateRecord(
            label="Claude 噪声",
            normalized_label=normalize_label("Claude 噪声"),
            proposed_kind="topic",
            status="candidate",
            first_seen_at=stamp, last_seen_at=stamp, created_at=stamp, updated_at=stamp,
        )
        cand_legacy = CmsTagCandidateRecord(
            label="Claude Sonnet",
            normalized_label=normalize_label("Claude Sonnet"),
            proposed_kind="entity",
            status="candidate",
            first_seen_at=stamp, last_seen_at=stamp, created_at=stamp, updated_at=stamp,
        )
        session.add_all([cand_merged, cand_rejected, cand_deleted, cand_legacy])
        session.flush()

        # 1) 灵活标签命中
        _add_article(session, "a-flex", "代码助手趋势", "正文无关键字")
        _add_analysis(session, "a-flex", [
            {"candidate_id": None, "label": "Claude Code", "kind": "entity", "confidence": 0.92},
        ])
        # 2) 读时投影：候选合并至 Claude（无 durable assignment）
        _add_article(session, "a-merged", "模型能力对比", "正文未提目标词")
        _add_analysis(session, "a-merged", [
            {"candidate_id": cand_merged.id, "label": "Anthropic模型", "kind": "entity", "confidence": 0.88},
        ])
        # 3) 历史分析无 display_tags_json，但有 Candidate evidence
        _add_article(session, "a-evidence", "旧版评测记录", "正文未提目标词")
        _add_analysis(session, "a-evidence", [])
        session.add(CmsTagCandidateEvidenceRecord(
            candidate_id=cand_legacy.id,
            article_id="a-evidence",
            source_id="src_tech",
            raw_label="Claude Sonnet",
            confidence=0.85,
            created_at=stamp,
        ))
        # 4) 已拒绝候选 -> 不得命中
        _add_article(session, "a-rejected", "辟谣文章", "正文未提目标词")
        _add_analysis(session, "a-rejected", [
            {"candidate_id": cand_rejected.id, "label": "Claude 谣言", "kind": "topic", "confidence": 0.90},
        ])
        # 5) 已删除候选 -> 不得命中
        _add_article(session, "a-deleted", "待清理文章", "正文未提目标词")
        _add_analysis(session, "a-deleted", [
            {"candidate_id": cand_deleted.id, "label": "Claude 噪声", "kind": "topic", "confidence": 0.90},
        ])
        # 6) 已退役规范标签 -> 不得命中
        _add_article(session, "a-deprecated", "过时专题", "正文未提目标词")
        _assign_tag(session, "a-deprecated", tag_deprecated)
        # 7) \uXXXX 转义 JSON 中文标签 + 非破坏性连字符 U+2011 变体标签
        _add_article(session, "a-escaped", "架构简报", "正文不含检索词")
        _add_analysis(
            session,
            "a-escaped",
            [
                {"candidate_id": None, "label": "大模型推理", "kind": "topic", "confidence": 0.91},
                {"candidate_id": None, "label": "AI\u2011Native", "kind": "topic", "confidence": 0.90},
            ],
            ensure_ascii=True,
        )
        session.commit()

        delete_candidate(session, int(cand_deleted.id), actor_id="admin", reason="测试删除")
        session.commit()

        def _search(term: str) -> set[str]:
            q = apply_article_query_filters(select(ArticleRecord.id), search=term, session=session)
            return set(session.exec(q).all())

        assert _search("Claude") == {"a-flex", "a-merged", "a-evidence"}
        assert _search("大模型") == {"a-escaped"}
        assert _search("AI-Native") == {"a-escaped"}
        # LIKE 通配符必须按字面转义，不得扫出全部文章
        assert _search("%") == set()
        assert _search("_") == set()


def test_cross_field_multi_word_short_word_and_fts_fallback(tmp_path):
    """跨字段多词 AND（标签 + 正文）、短词（< 3 字如 AI）搜标签、FTS 缺失降级仍保留标签命中。"""
    sink = _sink(tmp_path, "cross_field.db")

    with Session(sink.engine) as session:
        tag_claude = create_tag(
            session, code="entity.claude", kind="entity", entity_type="model",
            name_zh="克劳德", name_en="Claude", status="active",
        )
        tag_ai = create_tag(
            session, code="industry.ai", kind="industry",
            name_zh="人工智能", name_en="AI", status="active",
        )
        _add_article(session, "a-cross", "工程实践笔记", "深入剖析分布式推理架构设计")
        _assign_tag(session, "a-cross", tag_claude)
        _assign_tag(session, "a-cross", tag_ai, is_primary=False)

        _add_article(session, "a-title-only", "Claude 入门教程", "基础使用说明")
        _add_article(session, "a-content-only", "技术月报", "分布式推理架构设计总览")
        session.commit()

        def _search(term: str) -> set[str]:
            q = apply_article_query_filters(select(ArticleRecord.id), search=term, session=session)
            return set(session.exec(q).all())

        # 词 1 命中标签(Claude) + 词 2 命中正文(架构设计) -> 跨字段 AND 命中
        assert _search("Claude 架构设计") == {"a-cross"}
        assert _search("Claude 不存在的词") == set()
        # 短词 AI（2 字符 < trigram 下限）在标题不含 AI 时仍通过标签命中
        assert _search("AI") == {"a-cross"}
        assert _search("AI 架构设计") == {"a-cross"}

    # FTS 虚拟表不可用时降级：标题 LIKE ∪ 标签命中均有效
    drop_fts(sink.engine)
    with Session(sink.engine) as session:
        q = apply_article_query_filters(select(ArticleRecord.id), search="Claude", session=session)
        assert set(session.exec(q).all()) == {"a-cross", "a-title-only"}


# ------------------------------------------------------------ 收藏 / Feed / MCP / AI 问答


def test_favorites_feed_mcp_and_qa_reader_search_include_tag_hits(monkeypatch, tmp_path):
    """验证收藏列表、订阅/个人 Feed、MCP search_articles 与 AI 问答召回均支持按标签搜到文章。"""
    import api.app as app_module
    import mcp_server

    sink = _sink(tmp_path, "all_surfaces.db")
    seed_default_accounts(sink.engine)
    stamp = _now_iso()

    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="src_pub", name="公开源", url="https://example.test/rss",
            created_at=stamp, updated_at=stamp,
        ))
        tag_claude = create_tag(
            session, code="entity.claude", kind="entity", entity_type="model",
            name_zh="Claude", name_en="Claude", status="active",
        )
        tag_ai = create_tag(
            session, code="topic.ai", kind="topic",
            name_zh="人工智能", name_en="AI", status="active",
        )
        # 仅标签含 Claude（标题与正文都不含 Claude）
        _add_article(
            session, "a-tag-only", "Anthropic 工程博客精选",
            "长上下文工具调用与评测方法论", source_id="src_pub", days_ago=0,
        )
        _assign_tag(session, "a-tag-only", tag_claude)
        _assign_tag(session, "a-tag-only", tag_ai, is_primary=False)

        # 标题/正文含 Claude（无标签）
        _add_article(
            session, "a-text-hit", "Claude 提示词技巧",
            "如何在工程中写出更稳定的 Claude 提示词", source_id="src_pub", days_ago=1,
        )

        # 无关文章
        _add_article(
            session, "a-other", "前端打包优化", "Vite 分包策略实战",
            source_id="src_pub", days_ago=0,
        )

        # admin 收藏 a-tag-only 与 a-other
        session.add(ReaderFavoriteRecord(owner_username="admin", article_id="a-tag-only", created_at=stamp))
        session.add(ReaderFavoriteRecord(owner_username="admin", article_id="a-other", created_at=stamp))

        # 订阅（带 search="Claude"）给 user 账号，用于验证订阅 Feed 与个人聚合 Feed
        sub = ReaderSubscriptionRecord(
            owner_username="user",
            name="Claude 订阅",
            filters_json=json.dumps({"source_ids": "src_pub", "search": "Claude"}),
            delivery_policy_json="{}",
            token_hash="hash-sub-claude",
            token_preview="dsub_***",
            is_active=True,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # 1) Feed 交付：单订阅查询与个人聚合查询均命中 a-tag-only 与 a-text-hit
        sub_rows, _ = query_subscription_articles(session, sub, limit=20)
        assert {r.id for r in sub_rows} == {"a-tag-only", "a-text-hit"}
        personal_rows = feed_articles_for_owner(session, "user", search="Claude", limit=20)
        assert {r.id for r in personal_rows} == {"a-tag-only", "a-text-hit"}

    # 2) 收藏接口 /api/reader/favorites?search=Claude
    monkeypatch.setattr(app_module, "db_sink", sink)
    with TestClient(app_module.app) as client:
        _login(client)
        fav_resp = client.get("/api/reader/favorites?search=Claude&limit=20")
        assert fav_resp.status_code == 200
        fav_data = fav_resp.json()
        assert fav_data["total"] == 1
        assert [item["id"] for item in fav_data["items"]] == ["a-tag-only"]
        assert fav_data["items"][0]["search_tag_hits"] == ["Claude"]

    # 3) MCP search_articles 底层检索
    mcp_rows = mcp_server._fts_hit_records(sink, "Claude", top_k=10, source_ids=["src_pub"])
    assert {r.id for r in mcp_rows} == {"a-tag-only", "a-text-hit"}

    # 4) AI 订阅问答召回 reader_search.fetch_candidates
    #    FTS 命中排在前（有 BM25 rank），纯标签命中补入其后，且不重不漏
    qa_hits = reader_search.fetch_candidates(
        sink.engine, keywords=["Claude"], source_ids=["src_pub"],
    )
    assert [r.id for r in qa_hits] == ["a-text-hit", "a-tag-only"]

    #    当仅存标签命中、FTS 零命中时（如日期窗只覆盖 today 的 a-tag-only），仍可召回
    qa_tag_only = reader_search.fetch_candidates(
        sink.engine,
        keywords=["Claude"],
        source_ids=["src_pub"],
        date_gte=_day_str(0),
    )
    assert [r.id for r in qa_tag_only] == ["a-tag-only"]

    #    短词 AI（< 3 字符）在问答召回中也通过标签命中
    qa_short = reader_search.fetch_candidates(
        sink.engine, keywords=["AI"], source_ids=["src_pub"],
    )
    assert [r.id for r in qa_short] == ["a-tag-only"]

    #    未授权 source_ids 绝不泄露标签命中的文章
    assert reader_search.fetch_candidates(
        sink.engine, keywords=["Claude"], source_ids=["other_src"],
    ) == []
