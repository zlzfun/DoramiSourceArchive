"""Recommendations require the evidence advertised to the reader; no paid work."""

import asyncio
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm.article_analysis_prompt import CONTENT_VALUE_SCORING_VERSION, analysis_contract_versions
from models.db import ArticleRecord, ArticleAnalysisRecord, AppSettingRecord, SourceConfigRecord
from models.analysis_contracts import SelectionLane
from services import daily_brief as daily
from services import public_content_recommendations as public
from services.content_recommendations import recommendation_value
from services.digest_selection import ValueLanePolicy
from services.personal_digest import load_value_candidates
from storage.impl.db_storage import DatabaseStorage
from tests.test_daily_brief import CONFIGURED, _patch_llm, _fake_chat_completion


@pytest.fixture
def sink(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'recommendations.db'}")
    yield value
    value.engine.dispose()


def seed(session, article_id, *, kind="opinion", score=4.0, value=8.0, basis="article_body", source=None, days=1):
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    when = (now - dt.timedelta(days=days)).isoformat()
    content_type = "podcast_episode" if basis in {"podcast_show_notes", "publisher_transcript", "asr_transcript"} else "rss_article"
    prompt, ruler = analysis_contract_versions(content_type)
    article = ArticleRecord(
        id=article_id, title=f"内容 {article_id}", content_type=content_type,
        source_id=source or f"source_{article_id}", source_url=f"https://example.com/{article_id}",
        publish_date=when, fetched_date=when, has_content=True, content="原始内容",
        extensions_json=json.dumps({"duration_seconds": 5400}),
    )
    session.add(article)
    session.flush()
    analysis = ArticleAnalysisRecord(
        article_id=article_id, status="succeeded", quality_score=score,
        podcast_final_score=value if basis in {"publisher_transcript", "asr_transcript"} else None,
        analysis_basis=basis, content_kind=kind, content_value=value,
        content_value_version=CONTENT_VALUE_SCORING_VERSION, content_value_reason="具体方法与边界",
        score_reason="一手经验与充分证据", summary="可复用的方法、失败案例和取舍。",
        prompt_version=prompt, scoring_version=ruler,
        created_at=now.isoformat(), updated_at=now.isoformat(),
    )
    session.add(analysis)
    return article, analysis


def today():
    return dt.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def test_personal_recommendation_uses_full_score_not_show_notes_or_legacy_score(sink):
    with Session(sink.engine) as session:
        seed(session, "notes", basis="podcast_show_notes", score=9.9, value=9.9)
        seed(session, "final", basis="publisher_transcript", score=4.0, value=8.2)
        seed(session, "low-final", basis="asr_transcript", score=9.9, value=7.0)
        session.commit()
        pool = load_value_candidates(
            session, lane=SelectionLane.LISTEN,
            source_ids=["source_notes", "source_final", "source_low-final"], followed_codes=[],
            cutoff_at=dt.datetime.now(ZoneInfo("Asia/Shanghai")),
            policy=ValueLanePolicy(min_value=7.5, max_items=2),
        )
        assert [(c.article_id, c.content_value) for c in pool] == [("final", 8.2)]


def test_legacy_full_score_is_supported_but_invalid_values_are_not_recommended():
    full = SimpleNamespace(analysis_basis="publisher_transcript", podcast_final_score=None, quality_score=8.1)
    assert recommendation_value("podcast_episode", full) == 8.1
    full.analysis_basis = "podcast_show_notes"
    assert recommendation_value("podcast_episode", full) is None
    full.analysis_basis = "asr_transcript"
    full.podcast_final_score = float("nan")
    assert recommendation_value("podcast_episode", full) is None


def test_public_pools_enforce_visibility_scope_evidence_and_existing_paper_quota(sink):
    with Session(sink.engine) as session:
        seed(session, "read")
        seed(session, "listen", basis="asr_transcript", score=8.1, value=8.1)
        seed(session, "notes", basis="podcast_show_notes", score=9.9)
        seed(session, "paper", kind="paper", value=9.0)
        seed(session, "private", source="user_rss_private")
        seed(session, "owned", source="owned")
        seed(session, "hidden", source="hidden")
        seed(session, "old", days=9)
        _, stale = seed(session, "stale")
        stale.content_value_version = "old"
        session.add(stale)
        session.add(SourceConfigRecord(source_id="owned", name="Private", source_type="rss", owner_username="bob", created_at=today(), updated_at=today()))
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden"]'))
        session.commit()
        pool = public.load_pool(session, report_date=today(), source_ids=None)
        assert {e["id"] for e in pool} == {"read", "listen"}
        assert {e["id"] for e in public.load_pool(session, report_date=today(), source_ids=["source_listen", "owned", "hidden", "user_rss_private"])} == {"listen"}
        assert next(e for e in pool if e["id"] == "listen")["duration_seconds"] == 5400


def test_public_generation_keeps_episodes_out_of_news_and_never_scores_or_generates_them(sink, monkeypatch):
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs["messages"])
        assert "内容 notes" not in str(kwargs["messages"])
        assert "内容 listen" not in str(kwargs["messages"])
        return await _fake_chat_completion(**kwargs)

    _patch_llm(monkeypatch, fake)
    with Session(sink.engine) as session:
        seed(session, "news", kind="news", score=8.5)
        seed(session, "read", kind="tutorial", score=4.0)
        seed(session, "listen", basis="publisher_transcript", score=4.0, value=8.3)
        seed(session, "notes", basis="podcast_show_notes", score=9.9)
        session.commit()
    result = asyncio.run(daily.generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date=today()))
    assert result["status"] == "success" and result["content_recommendations_count"] == 2
    with Session(sink.engine) as session:
        report = session.get(ArticleRecord, result["article_id"])
        ext = json.loads(report.extensions_json)
        assert [item["id"] for item in ext["items"]] == ["news"]
        assert ext["included_article_ids"] == ["news"]
        assert {item["id"] for item in ext["content_recommendations"]} == {"read", "listen"}
        assert "深度阅读（1 篇）" in report.content and "播客精选（1 集）" in report.content
        assert "全文内容价值 8.3" in report.content and "原节目约 90 分钟" in report.content
        assert "内容 notes" not in report.content


def test_content_only_generation_and_late_full_analysis_do_not_depend_on_news_cursor(sink, monkeypatch):
    async def no_llm(**kwargs):
        raise AssertionError("content-only brief must reuse existing analysis without a model call")

    _patch_llm(monkeypatch, no_llm)
    with Session(sink.engine) as session:
        seed(session, "listen", basis="asr_transcript", score=4.0, value=8.1)
        daily.set_setting(session, daily.KEY_CURSOR, dt.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
        session.commit()
        before = daily.read_cursor(session)
    result = asyncio.run(daily.generate_daily_brief(storage=sink, llm_config=CONFIGURED, report_date=today()))
    assert result["content_recommendations_count"] == 1
    with Session(sink.engine) as session:
        assert daily.read_cursor(session) == before
        report = session.get(ArticleRecord, result["article_id"])
        assert "本期推荐 1 集播客" in report.content and "0 条资讯" not in report.content
        assert json.loads(report.extensions_json)["items"] == []
        # A content-only edition must not feed its podcast titles into tomorrow's
        # news deduplication through the pre-items Markdown compatibility path.
        assert daily.fetch_recent_brief_items(session) == []


def test_same_day_keeps_prior_snapshot_and_limits_sources_and_recent_repeats(sink):
    with Session(sink.engine) as session:
        for name, score, source in (("a", 8.0, "a"), ("b", 8.1, "b"), ("c", 9.0, "b"), ("d", 8.8, "d")):
            seed(session, name, basis="asr_transcript", score=score, value=score, source=source)
        session.commit()
        pool = public.load_pool(session, report_date=today(), source_ids=None)
        a = next(e for e in pool if e["id"] == "a")
        a = {**a, "summary": "早间冻结摘要"}
        session.add(ArticleRecord(id=f"daily_brief_{today()}", title="今日", source_url="https://example.com/today", fetched_date=today(), source_id="dorami_daily_brief", content_type="daily_brief", publish_date=today(), extensions_json=json.dumps({"content_recommendations": [a]})))
        yesterday = (dt.date.fromisoformat(today()) - dt.timedelta(days=1)).isoformat()
        session.add(ArticleRecord(id=f"daily_brief_{yesterday}", title="昨日", source_url="https://example.com/yesterday", fetched_date=yesterday, source_id="dorami_daily_brief", content_type="daily_brief", publish_date=yesterday, extensions_json=json.dumps({"content_recommendations": [{"id": "d"}]})))
        session.commit()
        picked = public.select_recommendations(session, pool, report_date=today())
        assert [entry["id"] for entry in picked] == ["a", "c"]
        assert picked[0]["summary"] == "早间冻结摘要"
        picked = public.select_recommendations(session, pool, report_date=today(), excluded_ids={"a"})
        assert [entry["id"] for entry in picked] == ["c"]


def test_render_does_not_invent_durations_or_executable_links():
    entry = {"selection_lane": "listen", "title": "[假链接](javascript:alert(1))", "source_url": "javascript:alert(1)", "content_value": 8.0, "source": "来源", "duration_seconds": float("nan"), "guide_duration_seconds": -2}
    body = "\n".join(public.render_sections([entry]))
    assert "分钟" not in body and "### [" not in body
    assert "全文内容价值 8.0" in body


def test_public_recommendations_skip_news_event_and_reuse_verified_title(sink):
    from models.db import EventRecord, EventMemberRecord
    from services.title_cache import fingerprint

    with Session(sink.engine) as session:
        news, _ = seed(session, "event-news", kind="news", score=8.5)
        read, _ = seed(session, "event-read", kind="tutorial", value=9.0)
        episode, _ = seed(session, "translated", basis="publisher_transcript", value=8.2)
        episode.title = "How to evaluate AI agents"
        episode.extensions_json = json.dumps({"translation_zh_title": "如何评估 AI 智能体", "translation_zh_title_fp": fingerprint(episode.title)})
        session.add(episode)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        event = EventRecord(status="open", first_seen_at=stamp, last_member_at=stamp, created_at=stamp, updated_at=stamp)
        session.add(event)
        session.flush()
        for article in (news, read):
            session.add(EventMemberRecord(event_id=event.id, article_id=article.id, publisher_key=article.source_id, report_at=stamp, first_seen_at=stamp, assigned_by="llm", created_at=stamp))
        session.commit()
        pool = public.load_pool(session, report_date=today(), source_ids=None)
        selected = public.select_recommendations(session, pool, report_date=today(), excluded_ids={news.id})
        assert [item["id"] for item in selected] == [episode.id]
        assert selected[0]["title"] == "如何评估 AI 智能体"


def test_ranking_picks_use_explicit_full_score_even_when_legacy_score_is_low(sink):
    from services.ranking_picks import read_picks

    with Session(sink.engine) as session:
        seed(session, "final-rank", basis="asr_transcript", score=4.0, value=8.4)
        seed(session, "notes-rank", basis="podcast_show_notes", score=9.9)
        session.commit()
        picked = read_picks(session, shape="podcast")
        assert [(item["id"], item["content_value"]) for item in picked["items"]] == [("final-rank", 8.4)]
