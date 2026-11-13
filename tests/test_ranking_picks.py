"""榜单页「深度阅读」「播客精选」(issue #221)服务层与端点测试:与早报同名分区同口径(内容类型 + 内容价值门槛)、
近 7 天滚动窗、可见性、同事件留一篇、热点事件 7 天榜上的事件不出现、游客可读。造数据一律相对时间。"""

from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

from llm.article_analysis_prompt import CONTENT_VALUE_SCORING_VERSION  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    EventMemberRecord,
    EventRecord,
)
from services import ranking_picks  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def _sink(tmp_path, name: str) -> DatabaseStorage:
    return DatabaseStorage(f"sqlite:///{tmp_path / name}")


def _item(
    session,
    article_id,
    source_id,
    *,
    kind=None,
    value=None,
    news=6.0,
    hours_ago=24.0,
    content_type="rss_article",
    version=CONTENT_VALUE_SCORING_VERSION,
    status="succeeded",
    analyzed=True,
    basis=None,
):
    stamp = _iso(hours_ago)
    session.add(ArticleRecord(
        id=article_id, title=f"Title {article_id}", content_type=content_type, source_id=source_id,
        source_url=f"https://example.test/{article_id}", publish_date=stamp, fetched_date=stamp,
        content="body", extensions_json="{}",
    ))
    session.flush()
    session.add(ArticleAnalysisRecord(
        article_id=article_id, status=status, quality_score=news, summary=f"Summary {article_id}",
        analyzed_at=stamp if analyzed else None, analysis_basis=basis if basis is not None else ("publisher_transcript" if content_type == "podcast_episode" else "article_body"),
        content_genre="industry_news", content_kind=kind, content_value=value,
        content_value_reason="reason" if value is not None else "",
        content_value_version=version if value is not None else "",
        created_at=stamp, updated_at=stamp,
    ))
    session.flush()


def _event(session, article_ids, *, hours_ago=24.0, publishers=None):
    stamp = _iso(hours_ago)
    event = EventRecord(status="open", first_seen_at=stamp, last_member_at=stamp, created_at=stamp, updated_at=stamp)
    session.add(event)
    session.flush()
    for index, article_id in enumerate(article_ids):
        session.add(EventMemberRecord(
            event_id=event.id, article_id=article_id,
            publisher_key=(publishers[index] if publishers else ""),
            report_at=stamp, first_seen_at=stamp, assigned_by="llm", created_at=stamp,
        ))
    session.flush()
    return event


def _ids(payload):
    return [item["id"] for item in payload["items"]]


def test_reading_picks_keep_read_kinds_over_threshold_by_content_value(tmp_path):
    sink = _sink(tmp_path, "read.db")
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden-src"]'))
        _item(session, "paper", "media-a", kind="paper", value=8.2)
        _item(session, "opinion", "media-b", kind="opinion", value=7.8, hours_ago=2)
        _item(session, "tutorial-low", "media-a", kind="tutorial", value=6.9)
        _item(session, "news", "media-a", kind="news", value=9.0, news=9.5)   # 资讯归热点事件,不进深度阅读
        _item(session, "stale-ruler", "media-a", kind="paper", value=9.1, version="content-value-v1")
        _item(session, "too-old", "media-a", kind="paper", value=9.2, hours_ago=24 * 8)
        _item(session, "hidden", "hidden-src", kind="paper", value=9.3)
        _item(session, "private", "user_rss_alice", kind="paper", value=9.4)
        _item(session, "brief", "dorami_daily_brief", kind="digest", value=9.5, content_type="daily_brief")
        _item(session, "episode", "pod-a", kind="opinion", value=9.6, news=9.6, content_type="podcast_episode")
        session.commit()

        payload = ranking_picks.read_picks(session, shape="article", now=NOW)

    assert _ids(payload) == ["paper", "opinion"]
    assert payload["shape"] == "article" and payload["window_days"] == 7 and payload["min_value"] == 7.0
    assert payload["empty_reason"] is None
    first = payload["items"][0]
    assert first["content_value"] == 8.2 and first["kind"] == "paper" and first["kind_label"] == "论文"
    assert first["source_name"] and first["summary"] == "Summary paper" and first["event_id"] is None


def test_reading_picks_keep_one_per_event_and_skip_events_on_the_hot_board(tmp_path):
    sink = _sink(tmp_path, "events.db")
    with Session(sink.engine) as session:
        # 热点事件 7 天榜上的事件(两家发布主体):整件不出现在深度阅读。
        _item(session, "board-opinion", "media-x", kind="opinion", value=8.6)
        _item(session, "board-news", "media-y", kind="news", value=6.0)
        _event(session, ["board-opinion", "board-news"])
        # 单一主体的事件不上热点榜:同事件只留内容价值最高的一篇。
        _item(session, "same-1", "media-z", kind="paper", value=7.9)
        _item(session, "same-2", "media-z", kind="paper", value=7.6)
        single = _event(session, ["same-1", "same-2"], publishers=["media-z", "media-z"])
        _item(session, "loose", "media-a", kind="tutorial", value=7.2)
        session.commit()
        single_id = single.id

        payload = ranking_picks.read_picks(session, shape="article", now=NOW)

    assert _ids(payload) == ["same-1", "loose"]
    assert payload["items"][0]["event_id"] == single_id and payload["items"][1]["event_id"] is None


def test_podcast_picks_rank_episodes_by_podcast_score(tmp_path):
    sink = _sink(tmp_path, "listen.db")
    with Session(sink.engine) as session:
        _item(session, "ep-high", "pod-a", news=8.4, content_type="podcast_episode")
        _item(session, "ep-mid", "pod-b", news=7.6, content_type="podcast_episode", hours_ago=3)
        _item(session, "ep-low", "pod-c", news=7.4, content_type="podcast_episode")
        _item(session, "ep-old", "pod-a", news=9.0, content_type="podcast_episode", hours_ago=24 * 7.5)
        _item(session, "article", "media-a", kind="paper", value=9.0)
        session.commit()

        listen = ranking_picks.read_picks(session, shape="podcast", now=NOW)
        empty = ranking_picks.read_picks(session, shape="podcast", now=NOW + dt.timedelta(days=30))

    assert _ids(listen) == ["ep-high", "ep-mid"]
    assert listen["min_value"] == 7.5 and listen["items"][0]["content_value"] == 8.4
    assert listen["items"][0]["event_id"] is None
    assert empty["items"] == [] and empty["empty_reason"] == "no_items"


def test_picks_keep_prior_authoritative_result_while_reanalysis_runs_or_fails(tmp_path):
    """检视 R1 #1:与早报候选池同一条件——强制重分析期间(pending / running / failed)上一次的权威结果仍有效。"""

    sink = _sink(tmp_path, "reanalysis.db")
    with Session(sink.engine) as session:
        for status in ("pending", "running", "failed"):
            _item(session, f"read-{status}", "media-a", kind="paper", value=8.0, status=status)
            _item(session, f"ep-{status}", "pod-a", news=8.0, content_type="podcast_episode", status=status)
        # 从没分析成功过的 pending 行没有权威结果:不进
        _item(session, "never", "media-a", kind="paper", value=9.0, status="pending", analyzed=False)
        session.commit()
        reads = ranking_picks.read_picks(session, shape="article", now=NOW)
        listens = ranking_picks.read_picks(session, shape="podcast", now=NOW)

    assert sorted(_ids(reads)) == ["read-failed", "read-pending", "read-running"]
    assert sorted(_ids(listens)) == ["ep-failed", "ep-pending", "ep-running"]


def test_podcast_picks_carry_public_full_analysis_fact(tmp_path):
    """#178:简介不足以推荐整期;精选只保留全文终评,文章条目不带播客依据。"""

    sink = _sink(tmp_path, "basis.db")
    with Session(sink.engine) as session:
        _item(session, "ep-full", "pod-a", news=8.4, content_type="podcast_episode", basis="asr_transcript")
        _item(session, "ep-notes", "pod-b", news=8.0, content_type="podcast_episode", basis="podcast_show_notes")
        _item(session, "paper", "media-a", kind="paper", value=8.0)
        session.commit()
        listens = ranking_picks.read_picks(session, shape="podcast", now=NOW)
        reads = ranking_picks.read_picks(session, shape="article", now=NOW)

    assert {item["id"]: item["full_analysis_ready"] for item in listens["items"]} == {"ep-full": True}
    assert "full_analysis_ready" not in reads["items"][0]


def test_guest_reads_picks_endpoint(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'picks_api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all")))
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        _item(session, "paper", "media-a", kind="paper", value=8.0)
        session.commit()

    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/guest", json={"entry": "deep_link"}).status_code == 200
        response = client.get("/api/reader/rankings/picks?shape=article")
        assert response.status_code == 200
        assert _ids(response.json()) == ["paper"]
        assert client.get("/api/reader/rankings/picks?shape=podcast").json()["empty_reason"] == "no_items"
        assert client.get("/api/reader/rankings/picks?shape=social").status_code == 422
