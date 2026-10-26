"""事件分配服务单测(issue #121 事件层 wave 1,services/event_assignment)。

覆盖:canonical_url;标题直连与数字串守卫 / 同源连载不直连;URL 直连对 docs_ / github_ 源关闭;三通道召回
(tmp 库 + FTS5)与结构规则剔除;提示词构造与解析(非法 eid / 回显失配 / 低置信跨日 / 空共同事实);
apply_assignments 事务、代表稿、事件题优先级、publisher_key 别名;ledger 退避与 72h 兜底;LLM 未配置退化;
入池排除;端到端模型归并;run_analysis_cycle 挂钩。全程不触网:``chat_completion`` 以 monkeypatch 打桩;
造数据一律相对时间(滚动窗 + 固定日期 = 时间炸弹)。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session, select  # noqa: E402

from config import LLMConfig  # noqa: E402
from llm.client import LLMError  # noqa: E402
from llm.prompts import build_event_assign_user_prompt  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    EventAssignmentLedgerRecord,
    EventMemberRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import reader_ai  # noqa: E402
from services import article_analysis, event_assignment as ea  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
LLM = LLMConfig(base_url="https://llm.invalid/v1", api_key="test", model="fake")
UNCONFIGURED = LLMConfig()


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _sink(tmp_path):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'events.db'}")


def _article(engine, article_id, title, *, source_id="rss_a", url=None, publish=None, fetched=None,
             content_type="rss_article", content="", extensions=None, authority=""):
    publish = publish or NOW
    fetched = fetched or publish
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=title, content_type=content_type, source_id=source_id,
            source_url=url or f"https://{source_id}.example/{article_id}",
            publish_date=_iso(publish), fetched_date=_iso(fetched), has_content=bool(content),
            content=content or title, extensions_json=json.dumps(extensions or {}, ensure_ascii=False),
            analysis_authority_id=authority,
        ))
        session.commit()


def _analysis(engine, article_id, *, summary="", score=7.0, genre="industry_news", content_kind=None,
              status="succeeded"):
    stamp = _iso(NOW)
    with Session(engine) as session:
        session.add(ArticleAnalysisRecord(
            article_id=article_id, status=status, tagging_status="succeeded", quality_score=score,
            summary=summary, content_genre=genre, content_kind=content_kind, analyzed_at=stamp,
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()


def _entity(engine, article_id, code):
    stamp = _iso(NOW)
    with Session(engine) as session:
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == code)).first()
        if tag is None:
            tag = CmsTagRecord(
                code=code, kind="entity", name_zh=code, name_en=code, normalized_name=code,
                status="active", user_selectable=True, created_at=stamp, updated_at=stamp,
            )
            session.add(tag)
            session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id=article_id, tag_id=int(tag.id), tag_kind="entity", is_primary=False,
            relevance=0.9, assignment_source="llm", prompt_version="t", taxonomy_version=1,
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()


def _source(engine, source_id, *, name=None, source_type="rss", scope="", tier="", owner="",
            owner_username="", url=""):
    stamp = _iso(NOW)
    with Session(engine) as session:
        session.add(SourceConfigRecord(
            source_id=source_id, name=name or source_id, source_type=source_type, url=url,
            source_scope=scope, provenance_tier=tier, source_owner=owner, owner_username=owner_username,
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()


def _seed(engine, article_id, title, *, summary="", entities=(), **kwargs):
    _article(engine, article_id, title, **kwargs)
    _analysis(engine, article_id, summary=summary)
    for code in entities:
        _entity(engine, article_id, code)


def _member(engine, article_id) -> EventMemberRecord | None:
    with Session(engine) as session:
        return session.exec(select(EventMemberRecord).where(EventMemberRecord.article_id == article_id)).first()


def _event(engine, event_id) -> EventRecord:
    with Session(engine) as session:
        return session.get(EventRecord, event_id)


def _ledger(engine, article_id) -> EventAssignmentLedgerRecord | None:
    with Session(engine) as session:
        return session.get(EventAssignmentLedgerRecord, article_id)


def _patch_llm(monkeypatch, replies):
    """把 event_assignment.chat_completion 换成桩:replies 逐次弹出(str 返回 / Exception 抛出);返回 calls。"""
    calls: list[dict] = []
    queue = list(replies)

    async def fake(**kwargs):
        calls.append(kwargs)
        reply = queue.pop(0) if queue else replies[-1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(ea, "chat_completion", fake)
    return calls


def _reply(assignments, new_events=()):
    return json.dumps({"assignments": assignments, "new_events": list(new_events)}, ensure_ascii=False)


def _echo(title):
    return title[:12]


# ──────────────── canonical_url / token ────────────────

def test_canonical_url_normalizes():
    assert ea.canonical_url("HTTPS://Example.COM/Path//to/?utm_source=x&b=2&a=1&fbclid=z#frag") == (
        "https://example.com/Path/to?a=1&b=2"
    )
    assert ea.canonical_url("https://example.com/post/") == "https://example.com/post"
    assert ea.canonical_url("https://example.com/post") == ea.canonical_url("https://EXAMPLE.com/post/?utm_medium=rss")
    assert ea.canonical_url("") == ""
    assert ea.canonical_url("https://example.com/") == "https://example.com/"


def test_recall_tokens_keeps_versions_and_splits_cjk():
    tokens = ea.recall_tokens("OpenAI 发布 GPT-5.5 模型,支持百万上下文 AI")
    assert "OpenAI" in tokens and "GPT-5.5" in tokens
    assert "发布" not in tokens and "AI" not in tokens
    assert "支持百" in tokens and "持百万" in tokens
    assert len(ea.recall_tokens("一" * 400)) <= ea.MAX_RECALL_TOKENS


# ──────────────── 机械直连 ────────────────

def test_title_direct_connect_with_digit_guard_and_short_title(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "a1", "OpenAI releases GPT-5.5 for everyone", source_id="rss_a")
    ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    first = _member(engine, "a1")
    assert first is not None and first.assigned_by == "singleton"

    _seed(engine, "b1", "OpenAI Releases GPT-5.5 For Everyone!", source_id="rss_b")
    _seed(engine, "c1", "OpenAI releases GPT-5.6 for everyone", source_id="rss_c")
    _seed(engine, "s1", "Qwen 更新", source_id="rss_a")
    _seed(engine, "s2", "Qwen 更新", source_id="rss_b")
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW + dt.timedelta(minutes=5))
    assert stats["direct"] == 1
    assert _member(engine, "b1").event_id == first.event_id
    assert _member(engine, "b1").assigned_by == "title"
    assert _member(engine, "c1").event_id != first.event_id
    assert _member(engine, "c1").assigned_by == "singleton"
    assert _member(engine, "s1").event_id != _member(engine, "s2").event_id
    event = _event(engine, first.event_id)
    assert event.member_count == 2 and event.publisher_count == 2


def test_title_direct_skips_same_source_serial(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "d1", "每日 AI 简报:今天值得关注的新闻", source_id="rss_daily", publish=NOW - dt.timedelta(days=1))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(days=1))
    _seed(engine, "d2", "每日 AI 简报:今天值得关注的新闻", source_id="rss_daily", publish=NOW)
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert stats["direct"] == 0
    assert _member(engine, "d1").event_id != _member(engine, "d2").event_id


def test_url_direct_connect_closed_for_shared_page_sources(tmp_path):
    engine = _sink(tmp_path).engine
    shared = "https://platform.openai.com/docs/changelog"
    _seed(engine, "doc1", "Changelog: new realtime voice", source_id="docs_openai_changelog", url=shared)
    _seed(engine, "gh1", "v1.2.0", source_id="rss_releases", url="https://github.com/x/y/releases",
          content_type="github_release")
    _seed(engine, "u1", "Astra limits capability rollout", source_id="rss_a",
          url="https://news.example/astra?utm_source=rss")
    ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    _seed(engine, "doc2", "Changelog: pricing update", source_id="docs_openai_other", url=shared)
    _seed(engine, "gh2", "v1.3.0", source_id="rss_other_releases", url="https://github.com/x/y/releases",
          content_type="github_release")
    _seed(engine, "u2", "Astra 限制能力开放:安全门槛达到", source_id="rss_b",
          url="https://NEWS.example/astra/?fbclid=abc")
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW + dt.timedelta(minutes=1))
    assert stats["direct"] == 1
    assert _member(engine, "u2").event_id == _member(engine, "u1").event_id
    assert _member(engine, "u2").assigned_by == "url"
    assert _member(engine, "doc2").event_id != _member(engine, "doc1").event_id
    assert _member(engine, "gh2").event_id != _member(engine, "gh1").event_id


# ──────────────── 召回 ────────────────

def _seed_open_events(engine):
    """两个开放事件(各一成员)供召回:E1 Anthropic / E2 Mistral。"""
    _seed(engine, "m1", "Anthropic ships Claude Opus 5 with longer context",
          content="Anthropic 发布 Claude Opus 5,上下文翻倍", summary="Anthropic 发布 Claude Opus 5",
          entities=("entity.anthropic",), source_id="rss_official", publish=NOW - dt.timedelta(hours=2))
    _seed(engine, "m2", "Mistral opens weights for Magistral 2",
          content="Magistral 2 权重开放下载", summary="Mistral 开源 Magistral 2 权重",
          entities=("entity.mistral",), source_id="rss_media", publish=NOW - dt.timedelta(hours=2))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(hours=1))
    return _member(engine, "m1").event_id, _member(engine, "m2").event_id


def test_recall_three_channels(tmp_path):
    engine = _sink(tmp_path).engine
    e1, e2 = _seed_open_events(engine)
    _seed(engine, "n1", "Claude Opus 5 longer context review", summary="Anthropic 发布 Claude Opus 5",
          entities=("entity.mistral",), source_id="rss_other",
          extensions={"translation_zh_title": "Magistral 2 权重开放"})
    with Session(engine) as session:
        article = session.get(ArticleRecord, "n1")
        candidates = ea.recall_candidates(session, article, now=NOW)
    by_id = {c.event_id: c for c in candidates}
    assert set(by_id) == {e1, e2}
    assert {"fts_title", "fts_summary"} <= by_id[e1].channels
    assert {"entity", "fts_zh"} <= by_id[e2].channels
    assert by_id[e1].title == "Anthropic ships Claude Opus 5 with longer context"
    assert by_id[e1].summary == "Anthropic 发布 Claude Opus 5"
    assert by_id[e1].member_count == 1


def test_recall_structural_rule_drops_same_source_serial(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "col1", "Weekly roundup: what happened in AI this week", source_id="rss_col",
          publish=NOW - dt.timedelta(days=1))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(days=1))
    _seed(engine, "col2", "Weekly roundup: what happened in AI this week", source_id="rss_col", publish=NOW)
    with Session(engine) as session:
        assert ea.recall_candidates(session, session.get(ArticleRecord, "col2"), now=NOW) == []
    _seed(engine, "col3", "Weekly roundup: what happened in AI this week", source_id="rss_other", publish=NOW)
    with Session(engine) as session:
        hits = ea.recall_candidates(session, session.get(ArticleRecord, "col3"), now=NOW)
    assert [c.event_id for c in hits] == [_member(engine, "col1").event_id]


def test_recall_only_open_events(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "old1", "Gemini 3 launches with native audio", entities=("entity.google",),
          publish=NOW - dt.timedelta(days=4))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(days=4))
    _seed(engine, "new1", "Gemini 3 launches with native audio", entities=("entity.google",), source_id="rss_b")
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert stats["closed"] == 1 and stats["direct"] == 0
    assert _event(engine, _member(engine, "old1").event_id).status == "closed"
    assert _member(engine, "new1").event_id != _member(engine, "old1").event_id


# ──────────────── 提示词与解析 ────────────────

def test_prompt_build_mentions_candidates_and_items():
    prompt = build_event_assign_user_prompt(
        [{"idx": 0, "title": "T0", "role": "official", "day": "2026-01-02", "candidates": [5]},
         {"idx": 1, "title": "T1", "role": "media", "day": "2026-01-02", "candidates": []}],
        [{"eid": 5, "title": "E5", "summary": "s" * 200, "member_count": 2, "first_day": "2026-01-01"}],
    )
    assert "e5 | 首篇 2026-01-01 | 成员 2 | E5" in prompt
    assert "摘要:" + "s" * 120 + "\n" in prompt
    assert "idx=0 | 官方 | 2026-01-02 | 候选:e5 | T0" in prompt
    assert "idx=1 | 媒体 | 2026-01-02 | 候选:无 | T1" in prompt


def test_parse_rules():
    titles = ["Anthropic ships Claude Opus 5", "Claude Opus 5 发布", "Mistral opens Magistral 2",
              "Magistral 2 权重开放", "Something else entirely", "Late article about Opus"]
    candidates = [{5: _iso(NOW - dt.timedelta(days=3)), 7: _iso(NOW - dt.timedelta(hours=3))}] * len(titles)
    report_ats = [NOW] * len(titles)
    raw = _reply([
        {"idx": 0, "echo": _echo(titles[0]), "event": "e999", "confidence": 0.9},
        {"idx": 1, "echo": "完全不对的回显", "event": "e7", "confidence": 0.9},
        {"idx": 2, "echo": _echo(titles[2]), "event": "new:1", "fact": "", "confidence": 0.8},
        {"idx": 3, "echo": _echo(titles[3]), "event": "new:1", "fact": "", "confidence": 0.8},
        {"idx": 5, "echo": _echo(titles[5]), "event": "e5", "confidence": 0.5},
    ], new_events=[{"key": "new:1", "title": "Mistral 发布 Magistral 2"}])
    parsed = ea.parse_event_assign_response(raw, titles=titles, candidates=candidates, report_ats=report_ats)
    assert parsed.illegal_target == 1 and parsed.assignments[0].target == "single"
    assert parsed.echo_mismatch == 1 and parsed.assignments[1].target == "single"
    assert parsed.echo_samples == [{"title": titles[1][:12], "echo": "完全不对的回显"}]
    assert parsed.empty_fact == 2 and parsed.assignments[2].target == parsed.assignments[3].target == "single"
    assert parsed.assignments[4].target == "single"  # 漏掉的 idx
    assert parsed.low_confidence == 1 and parsed.assignments[5].target == "single"

    raw_ok = _reply([
        {"idx": 1, "echo": _echo(titles[1]), "event": "e7", "confidence": 0.9},
        {"idx": 2, "echo": _echo(titles[2]), "event": "new:1", "fact": "Mistral 发布 Magistral 2", "confidence": 0.8},
        {"idx": 3, "echo": _echo(titles[3]), "event": "new:1", "fact": "", "confidence": 0.8},
        {"idx": 5, "echo": _echo(titles[5]), "event": "e5", "confidence": 0.95},
    ], new_events=[{"key": "new:1", "title": "Mistral 发布 Magistral 2"}])
    parsed = ea.parse_event_assign_response(raw_ok, titles=titles, candidates=candidates, report_ats=report_ats)
    assert parsed.assignments[1].target == "event" and parsed.assignments[1].event_id == 7
    assert parsed.assignments[2].target == "new" and parsed.assignments[3].new_key == "new:1"
    assert parsed.assignments[5].target == "event" and parsed.assignments[5].event_id == 5
    assert parsed.new_titles["new:1"] == "Mistral 发布 Magistral 2"

    undeclared = _reply([{"idx": 2, "echo": _echo(titles[2]), "event": "new:9", "fact": "x" * 10}])
    parsed = ea.parse_event_assign_response(undeclared, titles=titles, candidates=candidates, report_ats=report_ats)
    assert parsed.illegal_target == 1 and parsed.assignments[2].target == "single"
    with pytest.raises(ValueError):
        ea.parse_event_assign_response("not json at all", titles=titles, candidates=candidates, report_ats=report_ats)
    fenced = "```json\n" + raw_ok + "\n```"
    assert ea.parse_event_assign_response(fenced, titles=titles, candidates=candidates,
                                          report_ats=report_ats).assignments[1].target == "event"


# ──────────────── 落库 ────────────────

def test_apply_assignments_representative_title_and_publisher(tmp_path):
    engine = _sink(tmp_path).engine
    _source(engine, "x_sam", source_type="x", scope="company", tier="tier0_primary", owner="sam_altman")
    _source(engine, "media_cn", scope="ai_media")
    _source(engine, "openai_blog", scope="company", tier="tier0_primary", owner="OpenAI")
    _seed(engine, "a", "we just shipped GPT-5.5, enjoy", source_id="x_sam", content_type="social_post",
          publish=NOW - dt.timedelta(hours=1))
    _seed(engine, "b", "OpenAI 发布 GPT-5.5,上下文翻倍", source_id="media_cn", publish=NOW - dt.timedelta(hours=3))
    _seed(engine, "c", "Introducing GPT-5.5", source_id="openai_blog", publish=NOW - dt.timedelta(days=5),
          extensions={"translation_zh_title": "GPT-5.5 正式发布"})
    with Session(engine) as session:
        session.get(ArticleAnalysisRecord, "b").quality_score = 9.0
        session.commit()
    decisions = [
        ea.AssignmentDecision(article_id="a", assigned_by="llm", new_key="new:1", confidence=0.9),
        ea.AssignmentDecision(article_id="b", assigned_by="llm", new_key="new:1", confidence=0.9),
        ea.AssignmentDecision(article_id="c", assigned_by="llm", new_key="new:1", confidence=0.9),
    ]
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, decisions, now=NOW, llm_titles={"new:1": "模型题"})
    assert outcome["assigned"] == 3 and outcome["new_events"] == 1
    event = _event(engine, outcome["event_ids"][0])
    assert event.representative_article_id == "c"           # 官方且非社交,胜过高分媒体与官方推文
    assert event.title == "OpenAI 发布 GPT-5.5,上下文翻倍" and event.title_source == "zh_member"
    assert event.member_count == 3 and event.publisher_count == 2   # sam_altman → openai 与 openai_blog 合并
    assert event.assignment_version == ea.ASSIGNMENT_VERSION and event.status == "open"
    assert _member(engine, "a").publisher_key == "openai"
    assert _member(engine, "c").publisher_key == "openai"
    assert _member(engine, "b").publisher_key == "media_cn"
    assert _member(engine, "c").is_representative and not _member(engine, "a").is_representative
    assert _member(engine, "a").confidence == 0.9 and _member(engine, "a").prompt_version
    # report_at 钳制:c 发布于 5 天前 → 钳到首见 −72h;a 一小时前 → 原值
    assert _member(engine, "c").report_at == _iso(NOW - dt.timedelta(hours=72))
    assert _member(engine, "a").report_at == _iso(NOW - dt.timedelta(hours=1))
    assert _ledger(engine, "a").status == "assigned"


def test_title_priority_translation_then_llm_then_representative(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "t1", "Introducing GPT-5.5", extensions={"translation_zh_title": "GPT-5.5 正式发布"})
    _seed(engine, "t2", "Magistral 2 weights are out", source_id="rss_b")
    _seed(engine, "t3", "Plain english title here", source_id="rss_c")
    with Session(engine) as session:
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t1", assigned_by="singleton")], now=NOW)
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t2", assigned_by="llm", new_key="new:1")],
                             now=NOW, llm_titles={"new:1": "Mistral 发布 Magistral 2"})
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t3", assigned_by="singleton")], now=NOW)
    e1, e2, e3 = (_event(engine, _member(engine, a).event_id) for a in ("t1", "t2", "t3"))
    assert (e1.title, e1.title_source) == ("GPT-5.5 正式发布", "translation")
    assert (e2.title, e2.title_source) == ("Mistral 发布 Magistral 2", "llm")
    assert (e3.title, e3.title_source) == ("Plain english title here", "representative")
    # 再有成员加入时模型题仍保留(没有更优来源)
    _seed(engine, "t4", "Another english take on Magistral", source_id="rss_d")
    with Session(engine) as session:
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t4", assigned_by="llm", event_id=e2.id)], now=NOW)
    assert _event(engine, e2.id).title_source == "llm" and _event(engine, e2.id).member_count == 2


def test_apply_assignments_rolls_back_failed_article_and_retries(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    _seed(engine, "x1", "Transactional article one")

    def boom(*_args, **_kwargs):
        raise RuntimeError("recompute failed")

    monkeypatch.setattr(ea, "_recompute_events", boom)
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [ea.AssignmentDecision(article_id="x1", assigned_by="singleton")], now=NOW)
    assert outcome["isolated_failures"] == 1 and outcome["assigned"] == 0
    with Session(engine) as session:
        assert session.exec(select(EventRecord)).all() == []
        assert session.exec(select(EventMemberRecord)).all() == []
    ledger = _ledger(engine, "x1")
    assert ledger.status == "pending" and ledger.attempts == 1 and "recompute failed" in ledger.last_error


def test_apply_assignments_isolates_single_failure(tmp_path, monkeypatch):
    """R1 #7:整批里一篇落库失败,其它照常提交,失败篇 ledger 退避。"""
    engine = _sink(tmp_path).engine
    _seed(engine, "g1", "Anthropic launches Claude Opus 5", source_id="rss_a")
    _seed(engine, "g2", "Broken source article", source_id="rss_bad")
    _seed(engine, "g3", "Claude Opus 5 发布", source_id="rss_c")
    real = ea.publisher_key

    def flaky(source_id, meta):
        if source_id == "rss_bad":
            raise RuntimeError("publisher exploded")
        return real(source_id, meta)

    monkeypatch.setattr(ea, "publisher_key", flaky)
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="g1", assigned_by="llm", new_key="new:1", confidence=0.9),
            ea.AssignmentDecision(article_id="g2", assigned_by="singleton"),
            ea.AssignmentDecision(article_id="g3", assigned_by="llm", new_key="new:1", confidence=0.9),
        ], now=NOW)
    assert outcome["isolated_failures"] == 1 and outcome["assigned"] == 2 and outcome["new_events"] == 1
    assert _member(engine, "g1").event_id == _member(engine, "g3").event_id   # 逐篇重放仍落同一新事件
    assert _member(engine, "g2") is None
    assert _ledger(engine, "g2").status == "pending" and _ledger(engine, "g2").attempts == 1
    assert "publisher exploded" in _ledger(engine, "g2").last_error
    assert _event(engine, _member(engine, "g1").event_id).member_count == 2


# ──────────────── ledger 退避 / 72h 兜底 / 未配置 ────────────────

def test_llm_failure_backoff_then_timeout_singleton(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    _seed(engine, "f1", "Anthropic ships Claude Opus 5", entities=("entity.anthropic",), source_id="rss_a")
    _seed(engine, "f2", "Claude Opus 5 发布:上下文翻倍", entities=("entity.anthropic",), source_id="rss_b")
    calls = _patch_llm(monkeypatch, [LLMError("boom")])
    stats = ea.assign_pending(engine, LLM, now=NOW)
    assert stats["failed"] == 2 and stats["llm_calls"] == 1 and len(calls) == 1
    assert calls[0]["usage_meta"].purpose == "event_assignment" and calls[0]["usage_meta"].username == "system"
    ledger = _ledger(engine, "f1")
    assert ledger.status == "pending" and ledger.attempts == 1 and "boom" in ledger.last_error
    assert ledger.next_attempt_at == _iso(NOW + dt.timedelta(minutes=5))
    assert _member(engine, "f1") is None

    # 退避未到:不取批、不调模型
    stats = ea.assign_pending(engine, LLM, now=NOW + dt.timedelta(minutes=2))
    assert stats["llm_calls"] == 0 and len(calls) == 1

    second = NOW + dt.timedelta(minutes=6)
    ea.assign_pending(engine, LLM, now=second)
    assert _ledger(engine, "f1").attempts == 2
    assert _ledger(engine, "f1").next_attempt_at == _iso(second + dt.timedelta(minutes=15))
    third = second + dt.timedelta(minutes=16)
    ea.assign_pending(engine, LLM, now=third)
    assert _ledger(engine, "f1").next_attempt_at == _iso(third + dt.timedelta(minutes=45))

    # 非法 JSON 同样按失败退避
    _patch_llm(monkeypatch, ["{oops"])
    fourth = third + dt.timedelta(minutes=46)
    stats = ea.assign_pending(engine, LLM, now=fourth)
    assert stats["failed"] == 2 and _ledger(engine, "f1").attempts == 4

    late = NOW + dt.timedelta(hours=73)
    stats = ea.assign_pending(engine, LLM, now=late)
    assert stats["skipped"] == 2 and stats["llm_calls"] == 0
    assert _member(engine, "f1").assigned_by == "singleton"
    assert _member(engine, "f1").event_id != _member(engine, "f2").event_id
    assert _ledger(engine, "f1").status == "skipped" and _ledger(engine, "f1").last_error == "pending_timeout"


def test_llm_unconfigured_all_singleton(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    _seed(engine, "u1", "Anthropic ships Claude Opus 5", entities=("entity.anthropic",), source_id="rss_a")
    _seed(engine, "u2", "Claude Opus 5 发布:上下文翻倍", entities=("entity.anthropic",), source_id="rss_b")
    calls = _patch_llm(monkeypatch, ["should not be called"])
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert stats["llm_unconfigured"] is True and stats["singleton"] == 2 and calls == []
    assert stats["recall"]["batch_similar"] == 2
    assert _member(engine, "u1").event_id != _member(engine, "u2").event_id
    assert _ledger(engine, "u1").status == "assigned"


def test_assign_pending_busy_when_locked(tmp_path):
    engine = _sink(tmp_path).engine
    assert ea._ASSIGN_LOCK.acquire(blocking=False)
    try:
        assert ea.assign_pending(engine, UNCONFIGURED, now=NOW)["busy"] is True
    finally:
        ea._ASSIGN_LOCK.release()


# ──────────────── 入池 ────────────────

def test_scan_pending_exclusions(tmp_path):
    engine = _sink(tmp_path).engine
    _source(engine, "owned_feed", owner_username="bob")
    _source(engine, "cred_feed", url="https://user:secret@feeds.example/private.xml")
    _seed(engine, "ok", "A normal public article about agents")
    _seed(engine, "paper", "Attention is still all you need", content_type="arxiv",
          url="https://arxiv.org/abs/2601.00001")
    _seed(engine, "digest", "哆啦美 AI 日报", source_id="dorami_daily_brief", content_type="daily_brief")
    _seed(engine, "pod", "Episode 12", content_type="podcast_episode")
    _seed(engine, "private", "Private feed item", source_id="user_rss_abc")
    _seed(engine, "owned", "Owned feed item", source_id="owned_feed")
    _seed(engine, "cred", "Credentialed feed item", source_id="cred_feed")
    _seed(engine, "remote", "Synced from authority", authority="authority-1")
    _seed(engine, "old", "Nine days old", fetched=NOW - dt.timedelta(days=9), publish=NOW - dt.timedelta(days=9))
    _article(engine, "unanalyzed", "Not analysed yet")
    _article(engine, "kind_paper", "Model says this is a paper")
    _analysis(engine, "kind_paper", content_kind="paper", genre="research_paper")
    # 汇编体裁(GitHub Trending 日榜一类):内容类型是 project,靠体裁 aggregation 拦(生产回放发现,方案 §13)
    _article(engine, "trending", "GitHub Trending 日榜 · 2026-10-11", source_id="github_trending_daily")
    _analysis(engine, "trending", content_kind="project", genre="aggregation")
    with Session(engine) as session:
        assert ea.scan_pending(session, now=NOW) == 1
    assert _ledger(engine, "ok").status == "pending"
    assert _ledger(engine, "paper").last_error == "kind:paper"
    assert _ledger(engine, "kind_paper").last_error == "kind:paper"
    assert _ledger(engine, "digest").last_error == "kind:digest"
    assert _ledger(engine, "trending").last_error == "genre:aggregation"
    assert _ledger(engine, "pod").last_error == "podcast"
    for article_id in ("private", "owned", "cred"):
        assert _ledger(engine, article_id).status == "skipped"
        assert _ledger(engine, article_id).last_error == "private_source"
    assert _ledger(engine, "remote") is None and _ledger(engine, "old") is None
    assert _ledger(engine, "unanalyzed") is None
    with Session(engine) as session:
        assert ea.scan_pending(session, now=NOW) == 0   # 幂等:已记账的不重扫


# ──────────────── 端到端:模型归并 ────────────────

def test_assign_pending_llm_merge_flow(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    _source(engine, "anthropic_blog", scope="company", tier="tier0_primary", owner="anthropic")
    _seed(engine, "a0", "Anthropic launches Claude Opus 5", summary="Anthropic 发布 Claude Opus 5",
          entities=("entity.anthropic",), source_id="anthropic_blog", publish=NOW - dt.timedelta(hours=3))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(hours=2))
    e_anthropic = _member(engine, "a0").event_id

    _seed(engine, "b1", "Claude Opus 5 发布:上下文翻倍", entities=("entity.anthropic",), source_id="media_cn")
    _seed(engine, "c1", "Mistral releases Magistral 2", entities=("entity.mistral",), source_id="rss_c")
    _seed(engine, "d1", "Magistral 2 weights are out", entities=("entity.mistral",), source_id="rss_d")
    _seed(engine, "z1", "Completely unrelated gardening tips", source_id="rss_z")
    calls = _patch_llm(monkeypatch, [_reply([
        {"idx": 0, "echo": _echo("Claude Opus 5 发布:上下文翻倍"), "event": f"e{e_anthropic}", "confidence": 0.92},
        {"idx": 1, "echo": _echo("Mistral releases Magistral 2"), "event": "new:1",
         "fact": "Mistral 发布 Magistral 2", "confidence": 0.85},
        {"idx": 2, "echo": _echo("Magistral 2 weights are out"), "event": "new:1",
         "fact": "Mistral 发布 Magistral 2", "confidence": 0.85},
    ], new_events=[{"key": "new:1", "title": "Mistral 发布 Magistral 2"}])])
    stats = ea.assign_pending(engine, LLM, now=NOW)
    assert stats["llm_calls"] == 1 and stats["llm_items"] == 3 and stats["llm_assigned"] == 3
    assert stats["singleton"] == 1 and stats["new_events"] == 2 and stats["failed"] == 0
    prompt = calls[0]["messages"][1].content
    assert f"e{e_anthropic} | 首篇" in prompt and "Anthropic launches Claude Opus 5" in prompt
    assert "idx=0 | 媒体" in prompt and "候选:e" in prompt and "gardening" not in prompt
    assert calls[0]["config"].model == "fake" and calls[0]["response_json"] is True
    assert _member(engine, "b1").event_id == e_anthropic
    assert _member(engine, "b1").assigned_by == "llm" and _member(engine, "b1").confidence == 0.92
    event = _event(engine, e_anthropic)
    assert event.member_count == 2 and event.publisher_count == 2
    assert event.title == "Claude Opus 5 发布:上下文翻倍" and event.title_source == "zh_member"
    assert event.representative_article_id == "a0"
    assert _member(engine, "c1").event_id == _member(engine, "d1").event_id
    mistral = _event(engine, _member(engine, "c1").event_id)
    assert (mistral.title, mistral.title_source) == ("Mistral 发布 Magistral 2", "llm")
    assert _member(engine, "z1").assigned_by == "singleton"
    with Session(engine) as session:
        assert {row.status for row in session.exec(select(EventAssignmentLedgerRecord)).all()} == {"assigned"}


def test_blocked_event_ids_keep_article_out(tmp_path):
    engine = _sink(tmp_path).engine
    _seed(engine, "k1", "Anthropic launches Claude Opus 5 today", source_id="rss_a")
    ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    blocked = _member(engine, "k1").event_id
    _seed(engine, "k2", "Anthropic launches Claude Opus 5 today", source_id="rss_b")
    stamp = _iso(NOW)
    with Session(engine) as session:
        session.add(EventAssignmentLedgerRecord(
            article_id="k2", status="pending", blocked_event_ids_json=json.dumps([blocked]),
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()
    ea.assign_pending(engine, UNCONFIGURED, now=NOW + dt.timedelta(minutes=1))
    assert _member(engine, "k2").event_id != blocked and _member(engine, "k2").assigned_by == "singleton"


# ──────────────── 分析周期挂钩 ────────────────

def test_run_analysis_cycle_hook_only_with_real_analyzer(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    seen: list = []
    monkeypatch.setattr(ea, "assign_pending", lambda eng, cfg, **kw: seen.append((eng, cfg)) or {})
    asyncio.run(article_analysis.run_analysis_cycle(engine, worker_id="t", llm_config=LLM, enabled=True))
    assert seen and seen[0][0] is engine and seen[0][1] is LLM

    async def fake_analyzer(*_args, **_kwargs):
        return {}

    asyncio.run(article_analysis.run_analysis_cycle(
        engine, worker_id="t", llm_config=LLM, enabled=True, analyzer=fake_analyzer,
    ))
    assert len(seen) == 1

    # 显式 assign_events=True:即使注入替身 analyzer 也跑;显式 False:真实分析器也不跑
    asyncio.run(article_analysis.run_analysis_cycle(
        engine, worker_id="t", llm_config=LLM, enabled=True, analyzer=fake_analyzer, assign_events=True,
    ))
    assert len(seen) == 2
    asyncio.run(article_analysis.run_analysis_cycle(
        engine, worker_id="t", llm_config=LLM, enabled=True, assign_events=False,
    ))
    assert len(seen) == 2

    def boom(*_args, **_kwargs):
        raise RuntimeError("assignment exploded")

    monkeypatch.setattr(ea, "assign_pending", boom)
    assert asyncio.run(article_analysis.run_analysis_cycle(engine, worker_id="t", llm_config=LLM, enabled=True)) == []


# ──────────────── 批内机械同组 ────────────────

def test_batch_mechanical_group_follows_head(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    shared = "https://news.example/opus-5"
    _seed(engine, "h1", "Anthropic launches Claude Opus 5", source_id="rss_a", url=shared)
    _seed(engine, "h2", "Anthropic launches Claude Opus 5", source_id="rss_b")
    _seed(engine, "h3", "Mirror of the same story", source_id="rss_c", url=shared + "?utm_source=x")
    _seed(engine, "h4", "Anthropic launches Claude Opus 5", source_id="rss_a",
          publish=NOW - dt.timedelta(days=1))   # 同源非同日:不跟随
    calls = _patch_llm(monkeypatch, ["should not be called"])
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert calls == [] and stats["direct"] == 2
    head = _member(engine, "h1")
    assert head.assigned_by == "singleton"
    assert _member(engine, "h2").event_id == head.event_id and _member(engine, "h2").assigned_by == "title"
    assert _member(engine, "h3").event_id == head.event_id and _member(engine, "h3").assigned_by == "url"
    assert _member(engine, "h4").event_id != head.event_id
    assert _event(engine, head.event_id).member_count == 3 and _event(engine, head.event_id).publisher_count == 3


def test_batch_followers_wait_when_head_is_retried(tmp_path, monkeypatch):
    engine = _sink(tmp_path).engine
    _seed(engine, "p0", "Anthropic launches Claude Opus 5", entities=("entity.anthropic",), source_id="rss_a")
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(hours=1))
    _seed(engine, "p1", "Claude Opus 5 发布:上下文翻倍", entities=("entity.anthropic",), source_id="rss_b")
    _seed(engine, "p2", "Claude Opus 5 发布:上下文翻倍", source_id="rss_c")
    _patch_llm(monkeypatch, [LLMError("boom")])
    stats = ea.assign_pending(engine, LLM, now=NOW)
    assert stats["failed"] == 2 and _member(engine, "p1") is None and _member(engine, "p2") is None
    assert _ledger(engine, "p2").status == "pending" and _ledger(engine, "p2").attempts == 1
    assert _ledger(engine, "p2").last_error == "head_retry"
    assert _ledger(engine, "p2").next_attempt_at == _ledger(engine, "p1").next_attempt_at
    e0 = _member(engine, "p0").event_id
    _patch_llm(monkeypatch, [_reply([
        {"idx": 0, "echo": _echo("Claude Opus 5 发布:上下文翻倍"), "event": f"e{e0}", "confidence": 0.9},
    ])])
    stats = ea.assign_pending(engine, LLM, now=NOW + dt.timedelta(minutes=6))
    assert stats["llm_assigned"] == 1 and stats["direct"] == 1
    assert _member(engine, "p1").event_id == e0 and _member(engine, "p2").event_id == e0
    assert _member(engine, "p2").assigned_by == "title"


# ──────────────── 回放脚本:LLM 配置从指定库只读解析 ────────────────

def test_replay_script_resolves_llm_config_from_database(tmp_path):
    import importlib.util

    from models.db import AppSettingRecord

    spec = importlib.util.spec_from_file_location(
        "replay_event_assignment",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "replay_event_assignment.py"),
    )
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    baseline = replay.settings.llm   # settings 是 frozen dataclass:基线原样取,KV 覆盖在其上
    db = tmp_path / "real.db"
    engine = DatabaseStorage(db_url=f"sqlite:///{db}").engine
    with Session(engine) as session:
        session.add(AppSettingRecord(key="llm_base_url", value="https://kv.example/v1"))
        session.add(AppSettingRecord(key="llm_model", value="kv-model"))
        session.add(AppSettingRecord(key="llm_api_key", value="kv-key"))
        session.add(AppSettingRecord(key="llm_aux_model", value="kv-aux"))
        session.commit()
    engine.dispose()
    before = db.stat().st_mtime_ns
    cfg = replay.resolve_config(f"sqlite:///{db}")
    assert (cfg.base_url, cfg.model, cfg.api_key, cfg.aux_model) == ("https://kv.example/v1", "kv-model", "kv-key", "kv-aux")
    assert cfg.configured and cfg.for_aux().model == "kv-aux"
    assert db.stat().st_mtime_ns == before      # 只读:库文件未被改写
    overridden = replay.resolve_config(f"sqlite:///{db}", "forced")
    assert (overridden.model, overridden.aux_model, overridden.thinking_mode) == ("forced", "", "disabled")
    assert replay.resolve_config("") == baseline   # 未指定库:退回 ini / env 基线
    assert replay.resolve_config(f"sqlite:///{tmp_path / 'missing' / 'x.db'}") == baseline  # 库不可用:退回基线


def test_echo_check_is_normalized_prefix():
    # 实测失配样本:模型把「12 个字符」数成 7 个汉字 / 在标点处截断 / 全角半角不一
    assert ea._echo_matches("阶跃发布 Ste", "阶跃发布 StepAudio 2:端到端语音")
    assert ea._echo_matches("比尔 · 盖茨：各", "比尔 · 盖茨：各国政府应当…")
    assert ea._echo_matches("字节跳动 CEO", "字节跳动 CEO 梁汝波内部信")
    assert ea._echo_matches("网友测试阿里 Q", "网友测试阿里 Qwen3 发现…")
    assert ea._echo_matches("ＯｐｅｎＡＩ发布ｇｐｔ", "OpenAI 发布 GPT-5.5:上下文翻倍")
    assert ea._echo_matches("openai发布gpt55", "OpenAI 发布 GPT-5.5:上下文翻倍")
    assert ea._echo_matches("Qwen 更新", "Qwen 更新")                      # 短题:抄全即可
    assert not ea._echo_matches("比尔盖茨", "比尔 · 盖茨：各国政府应当…")    # 只抄了 4 个字
    assert not ea._echo_matches("OpenAI 发布 Claude", "OpenAI 发布 GPT-5.5")  # 不是前缀
    assert not ea._echo_matches(None, "OpenAI 发布 GPT-5.5") and not ea._echo_matches("", "x")


# ──────────────── R1 回归:F3 / F5 / F6 / F7 / F9 / F10 ────────────────

def _redirect(engine, old_id, new_id):
    """模拟管理员合并:成员迁到目标、源事件置 merged、写 redirect。"""
    with Session(engine) as session:
        for member in session.exec(select(EventMemberRecord).where(EventMemberRecord.event_id == old_id)).all():
            member.event_id = new_id
            session.add(member)
        old = session.get(EventRecord, old_id)
        old.status = "merged"
        old.member_count = 0
        old.publisher_count = 0
        session.add(old)
        session.add(EventRedirectRecord(old_event_id=old_id, new_event_id=new_id, reason="manual", created_at=_iso(NOW)))
        session.commit()


def test_late_assignment_after_merge_follows_redirect_or_singles(tmp_path):
    """R1 #3:模型等待期间管理员把 A 并入 B,迟到的 A 目标沿 redirect 落到 B;终点 merged / authority / blocked / 缺失 → 单例。"""
    engine = _sink(tmp_path).engine
    _seed(engine, "a1", "Anthropic launches Claude Opus 5", source_id="rss_a")
    _seed(engine, "b1", "Claude Opus 5 发布", source_id="rss_b")
    with Session(engine) as session:
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="a1", assigned_by="singleton")], now=NOW)
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="b1", assigned_by="singleton")], now=NOW)
    event_a, event_b = _member(engine, "a1").event_id, _member(engine, "b1").event_id
    _redirect(engine, event_a, event_b)

    _seed(engine, "c1", "Late report on Claude Opus 5", source_id="rss_c")
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="c1", assigned_by="llm", event_id=event_a, confidence=0.9),
        ], now=NOW)
    assert outcome["retargeted"] == 1 and _member(engine, "c1").event_id == event_b
    assert _event(engine, event_b).member_count == 3 and _event(engine, event_a).member_count == 0

    # blocked 终点 → 单例
    _seed(engine, "d1", "Another late report", source_id="rss_d")
    with Session(engine) as session:
        session.add(EventAssignmentLedgerRecord(
            article_id="d1", status="pending", blocked_event_ids_json=json.dumps([event_b]),
            created_at=_iso(NOW), updated_at=_iso(NOW),
        ))
        session.commit()
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="d1", assigned_by="llm", event_id=event_a, confidence=0.9),
        ], now=NOW)
    assert outcome["invalid_target"] == 1
    assert _member(engine, "d1").event_id not in {event_a, event_b} and _member(engine, "d1").assigned_by == "singleton"

    # 缺失终点 → 单例;authority 事件 → 单例且不被污染
    _seed(engine, "e1", "Missing target report", source_id="rss_e")
    _seed(engine, "f1", "Authority target report", source_id="rss_f")
    with Session(engine) as session:
        target = session.get(EventRecord, event_b)
        target.authority_id = "remote-1"
        session.add(target)
        session.commit()
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="e1", assigned_by="llm", event_id=999, confidence=0.9),
            ea.AssignmentDecision(article_id="f1", assigned_by="url", event_id=event_b),
        ], now=NOW)
    assert outcome["invalid_target"] == 2
    assert _member(engine, "e1").assigned_by == "singleton" and _member(engine, "f1").assigned_by == "singleton"
    assert _event(engine, event_b).member_count == 3


def test_parse_validates_per_item_candidates():
    """R1 #6 ①:A 已排除事件 1(候选为空),同批 B 召回了 1;模型把 A 也分给 e1 → A 单例、B 照常。"""
    titles = ["A title about Opus launch", "B title about Opus launch"]
    raw = _reply([
        {"idx": 0, "echo": _echo(titles[0]), "event": "e1", "confidence": 0.9},
        {"idx": 1, "echo": _echo(titles[1]), "event": "e1", "confidence": 0.9},
    ])
    parsed = ea.parse_event_assign_response(
        raw, titles=titles, candidates=[{}, {1: _iso(NOW)}], report_ats=[NOW, NOW],
    )
    assert parsed.illegal_target == 1
    assert parsed.assignments[0].target == "single" and parsed.assignments[1].target == "event"


def test_title_direct_respects_event_level_serial_rule(tmp_path):
    """R1 #6 ②:昨日事件含来源 A / B 的同题稿,今日 A 的同题稿不得借 B 的标题直连进去。"""
    engine = _sink(tmp_path).engine
    yesterday = NOW - dt.timedelta(days=1)
    title = "Weekly AI roundup: what mattered this week"
    _seed(engine, "s1", title, source_id="rss_a", publish=yesterday)
    _seed(engine, "s2", title, source_id="rss_b", publish=yesterday)
    ea.assign_pending(engine, UNCONFIGURED, now=yesterday)
    event = _member(engine, "s1").event_id
    assert _member(engine, "s2").event_id == event
    _seed(engine, "s3", title, source_id="rss_a", publish=NOW)
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert stats["direct"] == 0 and _member(engine, "s3").event_id != event
    assert _member(engine, "s3").assigned_by == "singleton"
    with Session(engine) as session:   # 事件级结构规则同样把它挡在召回之外(只剩 s3 自己的单例事件)
        hits = {c.event_id for c in ea.recall_candidates(session, session.get(ArticleRecord, "s3"), now=NOW)}
    assert event not in hits


def test_new_group_serial_members_are_split(tmp_path, monkeypatch):
    """R1 #6 ③:同批同源同题跨日稿,机械分组拒并后模型仍给同一 new:1 → 落库前结构校验拆开。"""
    engine = _sink(tmp_path).engine
    title = "Daily AI digest: the stories you need"
    _seed(engine, "x1", title, source_id="rss_a", publish=NOW - dt.timedelta(days=1), entities=("entity.acme",))
    _seed(engine, "x2", title, source_id="rss_a", publish=NOW, entities=("entity.acme",))
    _patch_llm(monkeypatch, [_reply([
        {"idx": 0, "echo": _echo(title), "event": "new:1", "fact": "Acme 日报同一期", "confidence": 0.9},
        {"idx": 1, "echo": _echo(title), "event": "new:1", "fact": "Acme 日报同一期", "confidence": 0.9},
    ], new_events=[{"key": "new:1", "title": "Acme 日报"}])])
    stats = ea.assign_pending(engine, LLM, now=NOW)
    assert stats["llm_calls"] == 1 and stats["serial_split"] == 1
    assert _member(engine, "x1").event_id != _member(engine, "x2").event_id
    assert _member(engine, "x2").assigned_by == "singleton"


def test_authority_events_are_not_candidates(tmp_path):
    """R1 #7:同步来的 authority 事件不进候选池;同题本地文章另立事件,ledger 正常收口,远端事件不被碰。"""
    engine = _sink(tmp_path).engine
    title = "Anthropic launches Claude Opus 5"
    _seed(engine, "r1", title, source_id="rss_remote", authority="remote-1", publish=NOW - dt.timedelta(hours=1))
    stamp = _iso(NOW - dt.timedelta(hours=1))
    with Session(engine) as session:
        remote = EventRecord(
            status="open", title=title, title_source="representative", representative_article_id="r1",
            member_count=1, publisher_count=1, first_seen_at=stamp, last_member_at=stamp,
            assignment_version="event-assign-v1", authority_id="remote-1", created_at=stamp, updated_at=stamp,
        )
        session.add(remote)
        session.flush()
        session.add(EventMemberRecord(
            event_id=int(remote.id), article_id="r1", publisher_key="rss_remote", report_at=stamp,
            first_seen_at=stamp, assigned_by="sync", created_at=stamp,
        ))
        session.commit()
        remote_id = int(remote.id)
    _seed(engine, "l1", title, source_id="rss_local", entities=("entity.anthropic",))
    stats = ea.assign_pending(engine, UNCONFIGURED, now=NOW)
    assert stats["direct"] == 0 and stats["singleton"] == 1 and stats["isolated_failures"] == 0
    assert _member(engine, "l1").event_id != remote_id and _ledger(engine, "l1").status == "assigned"
    assert _event(engine, remote_id).member_count == 1
    with Session(engine) as session:
        hits = {c.event_id for c in ea.recall_candidates(session, session.get(ArticleRecord, "l1"), now=NOW)}
    assert remote_id not in hits


def test_scan_pending_pages_past_old_backlog(tmp_path):
    """R1 #9:日期前缀宽松范围里 500 篇窗口外旧文不再占满扫描额度;旧文记 skipped 不重扫。"""
    engine = _sink(tmp_path).engine
    old = NOW - dt.timedelta(days=7, hours=1)
    stamp = _iso(NOW)
    with Session(engine) as session:
        for i in range(500):
            session.add(ArticleRecord(
                id=f"old-{i:03d}", title=f"Old article {i}", content_type="rss_article", source_id="rss_a",
                source_url=f"https://rss_a.example/old-{i}", publish_date=_iso(old), fetched_date=_iso(old),
                has_content=False, content="", extensions_json="{}",
            ))
        session.flush()
        for i in range(500):
            session.add(ArticleAnalysisRecord(
                article_id=f"old-{i:03d}", status="succeeded", tagging_status="succeeded", quality_score=6.0,
                analyzed_at=stamp, created_at=stamp, updated_at=stamp,
            ))
        session.commit()
    _seed(engine, "fresh", "A fresh article inside the window", source_id="rss_b")
    with Session(engine) as session:
        assert ea.scan_pending(session, now=NOW) == 1
    assert _ledger(engine, "fresh").status == "pending"
    with Session(engine) as session:
        skipped = session.exec(select(EventAssignmentLedgerRecord).where(
            EventAssignmentLedgerRecord.last_error == "outside_intake_window",
        )).all()
        assert len(skipped) == 500
        assert ea.scan_pending(session, now=NOW) == 0


def test_stale_translation_title_is_ignored(tmp_path):
    """R1 #10:标题刷新后旧译题(指纹不符)不作事件题、不进译中召回;指纹一致或无指纹存量照常采纳。"""
    engine = _sink(tmp_path).engine
    stale = {"translation_zh_title": "Acme 旧产品停止服务",
             "translation_zh_title_fp": reader_ai._body_fingerprint("Acme sunsets Old Product")}
    fresh_title = "Acme launches New Product v2"
    fresh = {"translation_zh_title": "Acme 发布新品 v2", "translation_zh_title_fp": reader_ai._body_fingerprint(fresh_title)}
    _seed(engine, "t1", "Acme launches New Product", source_id="rss_a", extensions=stale)
    _seed(engine, "t2", fresh_title, source_id="rss_b", extensions=fresh)
    _seed(engine, "t3", "Legacy cached title article", source_id="rss_c", extensions={"translation_zh_title": "无指纹存量"})
    with Session(engine) as session:
        assert ea.translated_title(session.get(ArticleRecord, "t1")) == ""
        assert ea.translated_title(session.get(ArticleRecord, "t2")) == "Acme 发布新品 v2"
        assert ea.translated_title(session.get(ArticleRecord, "t3")) == "无指纹存量"
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t1", assigned_by="llm", new_key="new:1")],
                             now=NOW, llm_titles={"new:1": "Acme 发布新品"})
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="t2", assigned_by="singleton")], now=NOW)
    e1, e2 = _event(engine, _member(engine, "t1").event_id), _event(engine, _member(engine, "t2").event_id)
    assert (e1.title, e1.title_source) == ("Acme 发布新品", "llm")
    assert (e2.title, e2.title_source) == ("Acme 发布新品 v2", "translation")
    # 译中召回:失效译题不进 fts_zh 通道
    _seed(engine, "old-zh", "Acme sunsets Old Product", source_id="rss_d", content="Acme 旧产品停止服务 公告",
          publish=NOW - dt.timedelta(hours=2))
    ea.assign_pending(engine, UNCONFIGURED, now=NOW - dt.timedelta(hours=1))
    with Session(engine) as session:
        channels = {c for cand in ea.recall_candidates(session, session.get(ArticleRecord, "t1"), now=NOW) for c in cand.channels}
    assert "fts_zh" not in channels


def test_replay_export_keeps_source_database_bytes(tmp_path, monkeypatch, capsys):
    """R1 #5:--export 走只读引擎,源库主文件字节与 archive_updated_at 都不变。"""
    import hashlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "replay_event_assignment_ro",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "replay_event_assignment.py"),
    )
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    db = tmp_path / "source.db"
    engine = DatabaseStorage(db_url=f"sqlite:///{db}").engine
    _seed(engine, "pub1", "Public article for export", source_id="rss_a")
    _seed(engine, "priv1", "Private article", source_id="user_rss_x")
    engine.dispose()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    today = NOW.astimezone(ea.SHANGHAI).date().isoformat()
    items = replay.load_from_database(f"sqlite:///{db}", today, today)
    assert [item["id"] for item in items] == ["pub1"]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    # 摘要不一致(活库并发写入)只告警不中断;只读连接拒写仍是硬保证
    digests = iter(["before", "after"])
    monkeypatch.setattr(replay, "sqlite_digest", lambda _url: next(digests))
    assert [item["id"] for item in replay.load_from_database(f"sqlite:///{db}", today, today)] == ["pub1"]
    assert "摘要不一致" in capsys.readouterr().err
    ro = replay.readonly_engine(f"sqlite:///{db}")
    with Session(ro) as session:
        assert session.get(ArticleRecord, "pub1").archive_updated_at == ""
        with pytest.raises(Exception):
            session.add(ArticleRecord(
                id="w", title="w", content_type="rss_article", source_id="s", source_url="u",
                publish_date=_iso(NOW), fetched_date=_iso(NOW), has_content=False, content="", extensions_json="{}",
            ))
            session.commit()
    ro.dispose()


def test_isolated_replay_revalidates_targets_between_commits(tmp_path, monkeypatch):
    """R2 N1:隔离重放两次提交之间管理员合并了目标,后续篇沿 redirect 落到终点而不是写进 merged 事件。"""
    from services import event_board

    engine = _sink(tmp_path).engine
    _seed(engine, "b0", "Claude Opus 5 发布:上下文翻倍", source_id="rss_b", publish=NOW - dt.timedelta(hours=1))
    with Session(engine) as session:
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="b0", assigned_by="singleton")], now=NOW)
    target_b = _member(engine, "b0").event_id
    _seed(engine, "g1", "Anthropic launches Claude Opus 5", source_id="rss_a")
    _seed(engine, "g2", "Broken source article", source_id="rss_bad")
    _seed(engine, "g3", "Opus 5 arrives with longer context", source_id="rss_c")
    _seed(engine, "g4", "Opus 5 developer notes", source_id="rss_d")
    real_publisher = ea.publisher_key

    def flaky(source_id, meta):
        if source_id == "rss_bad":
            raise RuntimeError("publisher exploded")
        return real_publisher(source_id, meta)

    monkeypatch.setattr(ea, "publisher_key", flaky)
    real_isolated = ea._apply_isolated
    merged_from: list[int] = []

    def hooked(session, decision, **kwargs):
        if decision.article_id == "g3" and not merged_from:
            # g1 已单独提交进 new:1 事件 A;管理员此刻把 A 并入 B(真实 merge_events,独立会话)
            event_a = kwargs["key_events"]["new:1"]
            with Session(engine) as other:
                event_board.merge_events(other, event_a, target_b, now=NOW)
            merged_from.append(event_a)
        return real_isolated(session, decision, **kwargs)

    monkeypatch.setattr(ea, "_apply_isolated", hooked)
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="g1", assigned_by="llm", new_key="new:1", confidence=0.9),
            ea.AssignmentDecision(article_id="g2", assigned_by="singleton"),
            ea.AssignmentDecision(article_id="g3", assigned_by="llm", new_key="new:1", confidence=0.9),
            ea.AssignmentDecision(article_id="g4", assigned_by="llm", event_id=None, confidence=0.9),
        ], now=NOW)
    assert outcome["isolated_failures"] == 1 and merged_from
    event_a = merged_from[0]
    assert _event(engine, event_a).status == "merged"
    assert _member(engine, "g1").event_id == target_b          # 随合并迁走
    assert _member(engine, "g3").event_id == target_b          # 复用组键时验到当前终点 B,不写进 merged A
    assert _event(engine, target_b).member_count == 3 and _event(engine, event_a).member_count == 0
    assert _member(engine, "g4").event_id not in {event_a, target_b}

    # 显式 event_id 目标同样在隔离事务里重验:提交间合并 → retargeted 到终点
    _seed(engine, "h1", "Yet another Opus 5 report", source_id="rss_e")
    _seed(engine, "h2", "Broken again", source_id="rss_bad")
    _seed(engine, "h3", "Final Opus 5 report", source_id="rss_f")
    _seed(engine, "c0", "Opus 5 standalone", source_id="rss_g")
    with Session(engine) as session:
        ea.apply_assignments(session, [ea.AssignmentDecision(article_id="c0", assigned_by="singleton")], now=NOW)
    target_c = _member(engine, "c0").event_id
    merged_from.clear()

    def hooked_explicit(session, decision, **kwargs):
        if decision.article_id == "h3" and not merged_from:
            with Session(engine) as other:
                event_board.merge_events(other, target_b, target_c, now=NOW)
            merged_from.append(target_b)
        return real_isolated(session, decision, **kwargs)

    monkeypatch.setattr(ea, "_apply_isolated", hooked_explicit)
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="h1", assigned_by="llm", event_id=target_b, confidence=0.9),
            ea.AssignmentDecision(article_id="h2", assigned_by="singleton"),
            ea.AssignmentDecision(article_id="h3", assigned_by="llm", event_id=target_b, confidence=0.9),
        ], now=NOW)
    assert outcome["isolated_failures"] == 1 and outcome["retargeted"] == 1
    assert _member(engine, "h1").event_id == target_c and _member(engine, "h3").event_id == target_c
    assert _event(engine, target_b).status == "merged" and _event(engine, target_b).member_count == 0


def test_isolated_replay_applies_serial_rule_against_committed_group_members(tmp_path, monkeypatch):
    """R3 组合路径:批内 g1 / g3 同源同题跨日被模型给了同一 new:1,中间 g2 落库失败触发逐篇隔离重放;
    g3 复用 g1 已建的组事件时须按「并入已有事件」路径对现有成员做结构校验 → 拆成单例。"""
    engine = _sink(tmp_path).engine
    title = "Daily AI digest: the stories you need"
    _seed(engine, "g1", title, source_id="rss_a", publish=NOW - dt.timedelta(days=1))
    _seed(engine, "g2", "Broken source article", source_id="rss_bad")
    _seed(engine, "g3", title, source_id="rss_a", publish=NOW)
    _seed(engine, "g4", "Daily digest companion piece", source_id="rss_c")
    real_publisher = ea.publisher_key

    def flaky(source_id, meta):
        if source_id == "rss_bad":
            raise RuntimeError("publisher exploded")
        return real_publisher(source_id, meta)

    monkeypatch.setattr(ea, "publisher_key", flaky)
    with Session(engine) as session:
        outcome = ea.apply_assignments(session, [
            ea.AssignmentDecision(article_id="g1", assigned_by="llm", new_key="new:1", confidence=0.9),
            ea.AssignmentDecision(article_id="g2", assigned_by="singleton"),
            ea.AssignmentDecision(article_id="g3", assigned_by="llm", new_key="new:1", confidence=0.9),
            ea.AssignmentDecision(article_id="g4", assigned_by="llm", new_key="new:1", confidence=0.9),
        ], now=NOW)
    assert outcome["isolated_failures"] == 1 and outcome["serial_split"] == 1 and outcome["assigned"] == 3
    group_event = _member(engine, "g1").event_id
    assert _member(engine, "g3").event_id != group_event and _member(engine, "g3").assigned_by == "singleton"
    assert _member(engine, "g4").event_id == group_event          # 组键映射保留给不冲突的后续篇
    assert _event(engine, group_event).member_count == 2
    assert _member(engine, "g2") is None and _ledger(engine, "g2").attempts == 1
