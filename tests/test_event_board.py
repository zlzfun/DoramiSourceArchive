"""事件热点榜读取侧(issue #121 wave 1)服务层测试:可见性过滤、< 2 主体剔除、排序、is_new、
previous_rank、标题优先级、redirect、日快照事务替换、管理员合并 / 摘出语义。造数据一律相对时间。"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    EventAssignmentLedgerRecord,
    EventMemberBacklogRecord,
    EventMemberRecord,
    EventRankSnapshotRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import event_board  # noqa: E402
from services.reader_ai import (  # noqa: E402
    TRANSLATION_TITLE_FP_KEY,
    TRANSLATION_TITLE_KEY,
    _body_fingerprint,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def _sink(tmp_path, name: str) -> DatabaseStorage:
    return DatabaseStorage(f"sqlite:///{tmp_path / name}")


def _article(
    session,
    article_id,
    source_id,
    *,
    title=None,
    hours_ago=3.0,
    score=7.0,
    summary=None,
    content_type="rss_article",
    extensions=None,
    analysis=True,
):
    stamp = _iso(hours_ago)
    session.add(ArticleRecord(
        id=article_id,
        title=title if title is not None else f"Title {article_id}",
        content_type=content_type,
        source_id=source_id,
        source_url=f"https://example.test/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        content="body",
        extensions_json=json.dumps(extensions or {}),
    ))
    session.flush()  # 先落文章行,再挂分析行(FK)
    if analysis:
        session.add(ArticleAnalysisRecord(
            article_id=article_id,
            status="succeeded",
            quality_score=score,
            summary=summary if summary is not None else f"Summary {article_id}",
            created_at=stamp,
            updated_at=stamp,
        ))
    session.flush()


def _event(session, *, hours_ago=3.0, status="open", title="", title_source="", follows=None, authority_id=""):
    stamp = _iso(hours_ago)
    event = EventRecord(
        status=status,
        title=title,
        title_source=title_source,
        first_seen_at=stamp,
        last_member_at=stamp,
        follows_event_id=follows,
        authority_id=authority_id,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(event)
    session.flush()
    return event


def _member(session, event, article_id, *, publisher_key="", hours_ago=3.0, representative=False):
    stamp = _iso(hours_ago)
    session.add(EventMemberRecord(
        event_id=event.id,
        article_id=article_id,
        publisher_key=publisher_key,
        report_at=stamp,
        first_seen_at=stamp,
        assigned_by="llm",
        is_representative=representative,
        created_at=stamp,
    ))
    session.flush()


def _official_source(session, source_id, name="Official"):
    stamp = _iso(100)
    session.add(SourceConfigRecord(
        source_id=source_id, name=name, source_type="rss", url="https://official.test/feed",
        source_scope="company", provenance_tier="tier0_primary", created_at=stamp, updated_at=stamp,
    ))
    session.flush()


def _two_publisher_event(session, tag, *, hours_ago=3.0, status="open", extra=0):
    """一个有两个可见发布主体的事件(成员 {tag}-1 / {tag}-2 各一源),可追加 extra 个额外主体。"""

    event = _event(session, hours_ago=hours_ago, status=status, title=f"stored {tag}")
    for index in range(1, 3 + extra):
        article_id = f"{tag}-{index}"
        _article(session, article_id, f"src-{tag}-{index}", hours_ago=hours_ago)
        _member(session, event, article_id, hours_ago=hours_ago)
    return event


def test_board_filters_hidden_private_sources_and_requires_two_visible_publishers(tmp_path):
    sink = _sink(tmp_path, "visibility.db")
    with Session(sink.engine) as session:
        stamp = _iso(100)
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden-src"]'))
        session.add(SourceConfigRecord(
            source_id="legacy-private", name="private", source_type="rss", url="https://p.test/feed",
            owner_username="alice", created_at=stamp, updated_at=stamp,
        ))
        session.add(SourceConfigRecord(
            source_id="signed-feed", name="signed", source_type="rss", url="https://s.test/feed?token=abc",
            params_json='{"credentialed_private": true}', created_at=stamp, updated_at=stamp,
        ))
        # A:两个可见主体 + 隐藏源 + user_rss_ + owner 私有源 + 凭据源(后四者全部不可见)。
        visible = _event(session, hours_ago=3)
        _article(session, "a1", "pub-a", score=6.0, summary="visible summary")
        _article(session, "a2", "pub-b", score=5.0)
        _article(session, "a3", "hidden-src", score=9.9, summary="hidden summary", title="HIDDEN TITLE")
        _article(session, "a4", "user_rss_x", score=9.8)
        _article(session, "a5", "legacy-private", score=9.7)
        _article(session, "a6", "signed-feed", score=9.6)
        for article_id in ("a1", "a2", "a3", "a4", "a5", "a6"):
            _member(session, visible, article_id)
        # B:只剩一个可见主体 → 不上榜。
        one_visible = _event(session, hours_ago=2)
        _article(session, "b1", "pub-a")
        _article(session, "b2", "hidden-src")
        _member(session, one_visible, "b1")
        _member(session, one_visible, "b2")
        # C:两源同一发布主体(publisher_key 合并)→ 只算一个主体 → 不上榜。
        same_owner = _event(session, hours_ago=2)
        _article(session, "c1", "x-openai")
        _article(session, "c2", "rss-openai")
        _member(session, same_owner, "c1", publisher_key="openai")
        _member(session, same_owner, "c2", publisher_key="openai")
        session.commit()

        board = event_board.read_board(session, now=NOW)

    assert board["empty_reason"] is None
    assert [item["event_id"] for item in board["items"]] == [visible.id]
    item = board["items"][0]
    assert item["publisher_count"] == 2 and item["member_count"] == 2
    assert {publisher["key"] for publisher in item["publishers"]} == {"pub-a", "pub-b"}
    # 代表稿与摘要只从可见成员算:隐藏源那篇分数最高也不得成为代表。
    assert item["representative"]["article_id"] == "a1"
    assert item["summary"] == "visible summary"
    assert "HIDDEN" not in item["title"]
    assert item["rank"] == 1 and item["previous_rank"] is None and board["snapshot_date"] is None


def test_board_orders_by_publisher_count_then_latest_and_honours_window_status_limit(tmp_path):
    sink = _sink(tmp_path, "ordering.db")
    with Session(sink.engine) as session:
        three = _two_publisher_event(session, "three", hours_ago=5, extra=1)
        fresh = _two_publisher_event(session, "fresh", hours_ago=1)
        stale = _two_publisher_event(session, "stale", hours_ago=4)
        closed = _two_publisher_event(session, "closed", hours_ago=40, status="closed")
        _two_publisher_event(session, "merged", hours_ago=2, status="merged", extra=2)
        _two_publisher_event(session, "old", hours_ago=50)
        session.commit()

        board = event_board.read_board(session, now=NOW)
        assert [item["event_id"] for item in board["items"]] == [three.id, fresh.id, stale.id, closed.id]
        assert [item["rank"] for item in board["items"]] == [1, 2, 3, 4]
        assert board["window_hours"] == 48

        capped = event_board.read_board(session, now=NOW, limit=2)
        assert [item["event_id"] for item in capped["items"]] == [three.id, fresh.id]

        wide = event_board.read_board(session, now=NOW, window_hours=72)
        assert any(item["event_id"] != closed.id and item["first_seen_at"] == _iso(50) for item in wide["items"])

        empty = event_board.read_board(session, now=NOW + dt.timedelta(days=30))
    assert empty["items"] == [] and empty["empty_reason"] == "no_events"


def test_is_new_and_previous_rank_come_from_prior_day_snapshot(tmp_path):
    sink = _sink(tmp_path, "previous.db")
    with Session(sink.engine) as session:
        new_event = _two_publisher_event(session, "new", hours_ago=2)
        older = _two_publisher_event(session, "older", hours_ago=10)
        today = event_board.shanghai_date(NOW)
        yesterday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
        for date, event_id, rank in ((yesterday, older.id, 3), (today, older.id, 1)):
            session.add(EventRankSnapshotRecord(
                snapshot_date=date, event_id=event_id, rank=rank, publisher_count=2,
                member_count=2, heat=1.0, generated_at=_iso(20),
            ))
        session.commit()

        board = event_board.read_board(session, now=NOW)

    by_id = {item["event_id"]: item for item in board["items"]}
    assert by_id[new_event.id]["is_new"] is True
    assert by_id[older.id]["is_new"] is False
    # 只认「前一日」快照:今天 07:00 那份(rank 1)不作 previous_rank。
    assert by_id[older.id]["previous_rank"] == 3
    assert by_id[new_event.id]["previous_rank"] is None
    assert board["snapshot_date"] == yesterday


def test_title_precedence_zh_member_translation_cache_llm_then_representative(tmp_path):
    sink = _sink(tmp_path, "titles.db")
    with Session(sink.engine) as session:
        _official_source(session, "official-src")
        # 1) 中文成员标题优先(即便代表稿是英文官方稿)。
        zh_member = _event(session, hours_ago=3)
        _article(session, "z1", "official-src", title="OpenAI ships GPT-6", score=9.0)
        _article(session, "z2", "media-src", title="OpenAI 发布新模型", score=5.0)
        _member(session, zh_member, "z1")
        _member(session, zh_member, "z2")
        # 2) 无中文成员 → 代表稿译题缓存(指纹一致)。
        cached = _event(session, hours_ago=3)
        _article(session, "c1", "official-src", title="Anthropic ships Claude", score=9.0, extensions={
            TRANSLATION_TITLE_KEY: "Anthropic 发布 Claude",
            TRANSLATION_TITLE_FP_KEY: _body_fingerprint("Anthropic ships Claude"),
        })
        _article(session, "c2", "media-src", title="Claude release covered", score=5.0)
        _member(session, cached, "c1")
        _member(session, cached, "c2")
        # 3) 缓存指纹失配 → 模型拟题(title_source=llm)。
        llm = _event(session, hours_ago=3, title="模型拟题", title_source="llm")
        _article(session, "l1", "official-src", title="Google ships Gemini", score=9.0, extensions={
            TRANSLATION_TITLE_KEY: "过期译题", TRANSLATION_TITLE_FP_KEY: "stale",
        })
        _article(session, "l2", "media-src", title="Gemini release covered", score=5.0)
        _member(session, llm, "l1")
        _member(session, llm, "l2")
        # 4) 都没有 → 代表稿原题(英文)。
        plain = _event(session, hours_ago=3, title="stored but not llm", title_source="representative")
        _article(session, "p1", "official-src", title="Meta ships Llama", score=9.0)
        _article(session, "p2", "media-src", title="Llama release covered", score=5.0)
        _member(session, plain, "p1")
        _member(session, plain, "p2")
        session.commit()

        board = event_board.read_board(session, now=NOW)

    by_id = {item["event_id"]: item for item in board["items"]}
    assert (by_id[zh_member.id]["title"], by_id[zh_member.id]["title_lang"]) == ("OpenAI 发布新模型", "zh")
    assert by_id[zh_member.id]["representative"]["article_id"] == "z1"  # 官方稿仍是代表
    assert by_id[zh_member.id]["representative"]["source_role"] == "official"
    assert (by_id[cached.id]["title"], by_id[cached.id]["title_lang"]) == ("Anthropic 发布 Claude", "zh")
    assert (by_id[llm.id]["title"], by_id[llm.id]["title_lang"]) == ("模型拟题", "zh")
    assert (by_id[plain.id]["title"], by_id[plain.id]["title_lang"]) == ("Meta ships Llama", "en")


def test_read_event_follows_redirects_orders_members_and_lists_followups(tmp_path):
    sink = _sink(tmp_path, "detail.db")
    with Session(sink.engine) as session:
        _official_source(session, "official-src")
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden-src"]'))
        target = _event(session, hours_ago=3, title="target stored")
        _article(session, "t1", "media-src", hours_ago=1, score=8.0, extensions={
            TRANSLATION_TITLE_KEY: "媒体译题", TRANSLATION_TITLE_FP_KEY: _body_fingerprint("Title t1"),
        })
        _article(session, "t2", "official-src", hours_ago=5, score=6.0)
        _article(session, "t3", "x-src", hours_ago=2, content_type="social_post")
        _article(session, "t4", "hidden-src", hours_ago=0.5)
        for article_id, hours in (("t1", 1), ("t2", 5), ("t3", 2), ("t4", 0.5)):
            _member(session, target, article_id, hours_ago=hours)
        old = _event(session, hours_ago=4, status="merged")
        session.add(EventRedirectRecord(old_event_id=old.id, new_event_id=target.id, reason="manual", created_at=_iso(1)))
        older = _event(session, hours_ago=4, status="merged")
        session.add(EventRedirectRecord(old_event_id=older.id, new_event_id=old.id, reason="manual", created_at=_iso(1)))
        followup = _event(session, hours_ago=1, title="followup stored", follows=target.id)
        _article(session, "f1", "media-src", hours_ago=1)
        _member(session, followup, "f1", hours_ago=1)
        out_of_window = _two_publisher_event(session, "oow", hours_ago=80, status="closed")
        all_hidden = _event(session, hours_ago=1)
        _article(session, "h1", "hidden-src")
        _member(session, all_hidden, "h1")
        session.commit()

        direct = event_board.read_event(session, target.id, now=NOW)
        via_old = event_board.read_event(session, old.id, now=NOW)
        via_older = event_board.read_event(session, older.id, now=NOW)
        follow = event_board.read_event(session, followup.id, now=NOW)
        closed = event_board.read_event(session, out_of_window.id, now=NOW)
        assert event_board.read_event(session, 99999, now=NOW) is None
        assert event_board.read_event(session, all_hidden.id, now=NOW) is None

    assert direct["event_id"] == target.id and direct["merged_into"] is None
    assert direct["rank"] == 1
    assert via_old["event_id"] == target.id and via_old["merged_into"] == target.id
    assert via_older["merged_into"] == target.id  # 多跳 redirect 解析到终点
    assert [member["article_id"] for member in direct["members"]] == ["t1", "t3", "t2"]  # report_at 倒序,隐藏源缺席
    assert direct["representative"]["article_id"] == "t2"  # 官方 > 非社交 > 分数
    members = {member["article_id"]: member for member in direct["members"]}
    assert members["t2"]["is_representative"] is True and members["t1"]["is_representative"] is False
    assert members["t1"]["title_zh"] == "媒体译题" and members["t2"]["title_zh"] is None
    assert members["t3"]["content_shape"] == "social"
    assert members["t2"]["source_role"] == "official" and members["t2"]["source_name"] == "Official"
    # 关联事件的标题同样只从可见成员算(F11):不是库里冻结的 stored 题。
    assert direct["followups"] == [
        {"event_id": followup.id, "title": "Title f1", "first_seen_at": _iso(1)}
    ]
    assert follow["story"] == {"follows_event_id": target.id, "follows_title": "Title t2"}
    assert closed["rank"] is None and closed["member_count"] == 2


def test_llm_title_is_dropped_once_any_member_is_hidden_after_formation(tmp_path):
    """codex R1 F1:模型拟题可能复述隐藏成员独有的信息;事件形成后隐藏来源,标题不得含隐藏成员信息。"""

    sink = _sink(tmp_path, "llm-title-hidden.db")
    with Session(sink.engine) as session:
        event = _event(session, hours_ago=3, title="Project Cedar 曝光:三方报道", title_source="llm")
        _article(session, "e1", "pub-a", title="Acme ships a new model", score=8.0)
        _article(session, "e2", "pub-b", title="Acme model covered by press", score=6.0)
        _article(session, "e3", "cedar-src", title="Project Cedar revealed", score=9.5)
        for article_id in ("e1", "e2", "e3"):
            _member(session, event, article_id)
        session.commit()
        event_id = event.id

        before = event_board.read_board(session, now=NOW)["items"][0]
        assert before["title"] == "Project Cedar 曝光:三方报道" and before["member_count"] == 3

        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["cedar-src"]'))
        session.commit()
        board = event_board.read_board(session, now=NOW)["items"][0]
        detail = event_board.read_event(session, event_id, now=NOW)
        # 管理员视图不受影响:库内 authority 标题照旧。
        stored = event_board.list_events(session)[0]

        # 可见中文成员出现后优先用它;译题缓存次之。
        _article(session, "e4", "pub-c", title="Acme 发布新模型", score=3.0)
        _member(session, event, "e4")
        session.commit()
        with_zh = event_board.read_board(session, now=NOW)["items"][0]

    assert board["member_count"] == 2 and {p["key"] for p in board["publishers"]} == {"pub-a", "pub-b"}
    assert board["title"] == "Acme ships a new model" and board["title_lang"] == "en"
    assert "Cedar" not in board["title"] and "Cedar" not in detail["title"]
    assert detail["title"] == board["title"]
    assert stored["title"] == "Project Cedar 曝光:三方报道"
    assert with_zh["title"] == "Acme 发布新模型" and "Cedar" not in with_zh["title"]


def test_llm_title_is_dropped_while_a_member_waits_in_backlog(tmp_path):
    """codex R2 G1 / V3:接收端第三篇晚到进入待补账,已到的两篇都公开也不能证明模型题只依赖可见成员。"""

    sink = _sink(tmp_path, "llm-title-backlog.db")
    with Session(sink.engine) as session:
        event = _event(session, hours_ago=3, title="Acme launches confidential Project Cedar", title_source="llm")
        event.member_count = 3  # authority 写入值:三篇
        session.add(event)
        _article(session, "a", "pub-a", title="Acme launches a new model", score=8.0)
        _article(session, "b", "pub-b", title="Acme model covered by press", score=6.0)
        _member(session, event, "a")
        _member(session, event, "b")
        session.add(EventMemberBacklogRecord(
            authority_id=event.authority_id, article_id="c", event_id=event.id, revision=0,
            payload_json="{}", first_seen_at=_iso(1), updated_at=_iso(1),
        ))
        follower = _two_publisher_event(session, "fl", hours_ago=1)
        follower.follows_event_id = event.id
        session.add(follower)
        session.commit()
        event_id, follower_id = event.id, follower.id

        board = {item["event_id"]: item for item in event_board.read_board(session, now=NOW)["items"]}
        detail = event_board.read_event(session, event_id, now=NOW)
        assert board[event_id]["member_count"] == 2
        assert board[event_id]["title"] == "Acme launches a new model"
        assert detail["title"] == "Acme launches a new model"
        assert board[follower_id]["story"] == {"follows_event_id": event_id, "follows_title": "Acme launches a new model"}

        # 第三篇(公开源)到达并重放:待补行删除、成员落库 → 事件完整,模型题恢复。
        session.delete(session.get(EventMemberBacklogRecord, (event.authority_id, "c")))
        _article(session, "c", "pub-c", title="Project Cedar revealed", score=9.0)
        _member(session, event, "c")
        session.commit()
        board = {item["event_id"]: item for item in event_board.read_board(session, now=NOW)["items"]}
        assert board[event_id]["member_count"] == 3
        assert board[event_id]["title"] == "Acme launches confidential Project Cedar"
        assert event_board.read_event(session, event_id, now=NOW)["title"] == "Acme launches confidential Project Cedar"
        assert board[follower_id]["story"]["follows_title"] == "Acme launches confidential Project Cedar"


def test_llm_title_is_dropped_when_local_members_are_fewer_than_authority_count(tmp_path):
    """codex R2 G1:没有隐藏源、没有待补行,但本地成员数 < authority 缓存 member_count 也算不完整。"""

    sink = _sink(tmp_path, "llm-title-count.db")
    with Session(sink.engine) as session:
        event = _event(session, hours_ago=3, title="模型题:Project Cedar", title_source="llm")
        event.member_count = 3
        session.add(event)
        _article(session, "a", "pub-a", title="Acme launches a new model", score=8.0)
        _article(session, "b", "pub-b", title="Acme model covered by press", score=6.0)
        _member(session, event, "a")
        _member(session, event, "b")
        session.commit()
        event_id = event.id

        short = event_board.read_board(session, now=NOW)["items"][0]
        assert short["title"] == "Acme launches a new model"
        assert event_board.read_event(session, event_id, now=NOW)["title"] == "Acme launches a new model"

        event = session.get(EventRecord, event_id)
        event.member_count = 2  # 缓存与本地一致 → 完整
        session.add(event)
        session.commit()
        full = event_board.read_board(session, now=NOW)["items"][0]
        assert full["title"] == "模型题:Project Cedar" and full["title_lang"] == "zh"


def test_related_events_follow_reader_visibility(tmp_path):
    """codex R1 F11:story.follows_title 与 followups 走可见成员视图;无可见成员的关联事件不返回。"""

    sink = _sink(tmp_path, "related-visibility.db")
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value='["hidden-src"]'))
        public = _two_publisher_event(session, "pub", hours_ago=6)
        hidden_only = _event(session, hours_ago=5, title="hidden stored", follows=public.id)
        _article(session, "hid1", "hidden-src", hours_ago=5)
        _member(session, hidden_only, "hid1", hours_ago=5)
        # 后续事件带模型题且含一名隐藏成员:列在 followups 里时标题也要退回可见报道题。
        tainted = _event(session, hours_ago=4, title="模型题含隐藏信息", title_source="llm", follows=public.id)
        _article(session, "ta1", "media-a", hours_ago=4, title="Public follow-up report")
        _article(session, "ta2", "hidden-src", hours_ago=4, title="Secret follow-up")
        _member(session, tainted, "ta1", hours_ago=4)
        _member(session, tainted, "ta2", hours_ago=4)
        follows_hidden = _two_publisher_event(session, "fh", hours_ago=2)
        follows_hidden.follows_event_id = hidden_only.id
        session.add(follows_hidden)
        follows_public = _two_publisher_event(session, "fp", hours_ago=1)
        follows_public.follows_event_id = public.id
        session.add(follows_public)
        session.commit()

        board = {item["event_id"]: item for item in event_board.read_board(session, now=NOW)["items"]}
        detail = event_board.read_event(session, public.id, now=NOW)
        assert event_board.read_event(session, hidden_only.id, now=NOW) is None

    # 隐藏源独占的后续事件不出现;带隐藏成员的后续事件标题退回可见报道题;按首见升序。
    assert detail["followups"] == [
        {"event_id": tainted.id, "title": "Public follow-up report", "first_seen_at": _iso(4)},
        {"event_id": follows_public.id, "title": "Title fp-1", "first_seen_at": _iso(1)},
    ]
    assert board[follows_hidden.id]["story"] == {"follows_event_id": None, "follows_title": None}
    assert board[follows_public.id]["story"] == {"follows_event_id": public.id, "follows_title": "Title pub-1"}


def test_build_rank_snapshot_replaces_same_day_rows_transactionally(tmp_path, monkeypatch):
    sink = _sink(tmp_path, "snapshot.db")
    with Session(sink.engine) as session:
        first = _two_publisher_event(session, "first", hours_ago=2, extra=1)
        second = _two_publisher_event(session, "second", hours_ago=30)
        first_id, second_id = first.id, second.id
        session.commit()

    result = event_board.build_rank_snapshot(sink.engine, now=NOW)
    today = event_board.shanghai_date(NOW)
    assert result["snapshot_date"] == today and result["count"] == 2
    with Session(sink.engine) as session:
        rows = session.exec(
            select(EventRankSnapshotRecord).order_by(EventRankSnapshotRecord.rank)
        ).all()
        assert [(row.event_id, row.rank, row.publisher_count) for row in rows] == [
            (first_id, 1, 3), (second_id, 2, 2),
        ]
        # 热度 = Σ_publisher 0.5^(age/24h):三主体 2h 前 ≈ 3 × 0.944;两主体 30h 前 ≈ 2 × 0.42。
        assert 2.7 < rows[0].heat < 3.0 and 0.7 < rows[1].heat < 1.0
        # 同日重建:新增事件后整日行替换,不堆积旧行。
        _two_publisher_event(session, "third", hours_ago=1, extra=2)
        session.commit()
    rebuilt = event_board.build_rank_snapshot(sink.engine, now=NOW)
    assert rebuilt["count"] == 3
    with Session(sink.engine) as session:
        rows = session.exec(select(EventRankSnapshotRecord)).all()
        assert len(rows) == 3 and {row.snapshot_date for row in rows} == {today}
        assert [row.rank for row in sorted(rows, key=lambda row: row.rank)] == [1, 2, 3]
        # 次日读榜:previous_rank 来自今天这份快照。
        tomorrow_board = event_board.read_board(session, now=NOW + dt.timedelta(days=1))
    assert tomorrow_board["snapshot_date"] == today
    assert {item["event_id"]: item["previous_rank"] for item in tomorrow_board["items"]}[first_id] == 2

    def _fail_commit(_session):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(Session, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        event_board.build_rank_snapshot(sink.engine, now=NOW, date="2000-01-01")
    monkeypatch.undo()
    with Session(sink.engine) as session:
        assert len(session.exec(select(EventRankSnapshotRecord)).all()) == 3


def test_merge_events_moves_members_writes_redirect_and_recomputes_target(tmp_path):
    sink = _sink(tmp_path, "merge.db")
    with Session(sink.engine) as session:
        _official_source(session, "official-src")
        source = _event(session, hours_ago=6)
        _article(session, "s1", "official-src", hours_ago=6, score=9.0)
        _article(session, "s2", "media-a", hours_ago=5, score=4.0)
        _member(session, source, "s1", hours_ago=6, representative=True)
        _member(session, source, "s2", hours_ago=5)
        target = _event(session, hours_ago=3)
        _article(session, "t1", "media-b", hours_ago=3, score=7.0)
        _member(session, target, "t1", hours_ago=3, representative=True)
        follower = _event(session, hours_ago=1, follows=source.id)
        already_merged = _event(session, hours_ago=8, status="merged")
        session.add(EventRedirectRecord(old_event_id=already_merged.id, new_event_id=source.id, reason="manual", created_at=_iso(2)))
        synced = _event(session, hours_ago=2, authority_id="prod-authority")
        session.commit()
        ids = (source.id, target.id, follower.id, already_merged.id, synced.id)

    with Session(sink.engine) as session:
        with pytest.raises(event_board.EventBoardError) as same:
            event_board.merge_events(session, ids[1], ids[1])
        assert same.value.status_code == 409
        with pytest.raises(event_board.EventBoardError) as missing:
            event_board.merge_events(session, 99999, ids[1])
        assert missing.value.status_code == 404
        with pytest.raises(event_board.EventBoardError) as fence:
            event_board.merge_events(session, ids[0], ids[4])
        assert fence.value.code == "synced_event"

        result = event_board.merge_events(session, ids[0], ids[1], now=NOW)
        assert result["merged_from"] == ids[0]
        assert result["event"]["id"] == ids[1]
        assert result["event"]["member_count"] == 3 and result["event"]["publisher_count"] == 3
        assert result["event"]["representative_article_id"] == "s1"  # 官方稿接管代表
        assert result["event"]["representative_title"] == "Title s1"
        assert result["event"]["first_seen_at"] == _iso(6)

    with Session(sink.engine) as session:
        src = session.get(EventRecord, ids[0])
        assert src.status == "merged" and src.member_count == 0 and src.representative_article_id is None
        members = session.exec(select(EventMemberRecord).where(EventMemberRecord.event_id == ids[1])).all()
        assert {m.article_id for m in members} == {"s1", "s2", "t1"}
        assert {m.article_id for m in members if m.is_representative} == {"s1"}
        assert {m.assigned_by for m in members if m.article_id != "t1"} == {"manual"}
        assert session.exec(select(EventMemberRecord).where(EventMemberRecord.event_id == ids[0])).all() == []
        assert event_board.resolve_redirect(session, ids[0]) == (ids[1], ids[1])
        assert event_board.resolve_redirect(session, ids[3]) == (ids[1], ids[1])  # 旧链改指目标
        assert session.get(EventRecord, ids[2]).follows_event_id == ids[1]
        # 并入已合并事件:沿 redirect 落到终点。
        late = _event(session, hours_ago=2)
        _article(session, "x1", "media-c", hours_ago=2)
        _member(session, late, "x1", hours_ago=2)
        session.commit()
        late_id = late.id
        result = event_board.merge_events(session, late_id, ids[0], now=NOW)
        assert result["event"]["id"] == ids[1] and result["event"]["member_count"] == 4
        with pytest.raises(event_board.EventBoardError) as again:
            event_board.merge_events(session, ids[0], ids[1])
        assert again.value.code == "source_merged"


def test_detach_member_creates_singleton_and_blocks_automatic_reflow(tmp_path):
    sink = _sink(tmp_path, "detach.db")
    with Session(sink.engine) as session:
        event = _event(session, hours_ago=3)
        for article_id, hours in (("d1", 3), ("d2", 2), ("d3", 60)):
            _article(session, article_id, f"src-{article_id}", hours_ago=hours, score=5.0)
            _member(session, event, article_id, hours_ago=hours, representative=article_id == "d1")
        session.add(EventAssignmentLedgerRecord(
            article_id="d2", status="assigned", blocked_event_ids_json="[7]",
            created_at=_iso(2), updated_at=_iso(2),
        ))
        session.commit()
        event_id = event.id

    with Session(sink.engine) as session:
        with pytest.raises(event_board.EventBoardError) as missing:
            event_board.detach_member(session, event_id, "nope")
        assert missing.value.status_code == 404

        result = event_board.detach_member(session, event_id, "d2", now=NOW)
        new_id = result["event"]["id"]
        assert result["detached_from"] == event_id and new_id != event_id
        assert result["event"]["status"] == "open"
        assert result["event"]["member_count"] == 1 and result["event"]["publisher_count"] == 1
        assert result["event"]["representative_article_id"] == "d2"
        assert result["event"]["title"] == "Title d2"

        member = session.get(EventMemberRecord, (new_id, "d2"))
        assert member is not None and member.is_representative is True and member.assigned_by == "manual"
        ledger = session.get(EventAssignmentLedgerRecord, "d2")
        assert json.loads(ledger.blocked_event_ids_json) == [7, event_id]
        old = session.get(EventRecord, event_id)
        assert old.member_count == 2 and old.publisher_count == 2
        assert {m.article_id for m in session.exec(select(EventMemberRecord).where(EventMemberRecord.event_id == event_id)).all()} == {"d1", "d3"}

        # 首见超过 48h 的成员摘出后新事件即 closed;ledger 缺行时新建。
        aged = event_board.detach_member(session, event_id, "d3", now=NOW)
        assert aged["event"]["status"] == "closed"
        assert json.loads(session.get(EventAssignmentLedgerRecord, "d3").blocked_event_ids_json) == [event_id]

        with pytest.raises(event_board.EventBoardError) as sole:
            event_board.detach_member(session, event_id, "d1")
        assert sole.value.code == "sole_member"
        with pytest.raises(event_board.EventBoardError) as sole_new:
            event_board.detach_member(session, new_id, "d2")
        assert sole_new.value.code == "sole_member"


def test_list_events_filters_status_and_title_substring(tmp_path):
    sink = _sink(tmp_path, "list.db")
    with Session(sink.engine) as session:
        open_event = _event(session, hours_ago=2, title="GPT-6 发布")
        _article(session, "o1", "media-a")
        _member(session, open_event, "o1", representative=True)
        open_event.representative_article_id = "o1"
        open_event.member_count = 1
        session.add(open_event)
        _event(session, hours_ago=5, title="Gemini 发布", status="closed")
        _event(session, hours_ago=9, title="旧事件", status="merged")
        session.commit()

        everything = event_board.list_events(session)
        assert [item["title"] for item in everything] == ["GPT-6 发布", "Gemini 发布", "旧事件"]
        assert everything[0]["representative_title"] == "Title o1"
        assert set(everything[0]) == {
            "id", "title", "status", "publisher_count", "member_count", "first_seen_at",
            "last_member_at", "representative_article_id", "representative_title",
        }
        assert [item["status"] for item in event_board.list_events(session, status="closed")] == ["closed"]
        assert [item["title"] for item in event_board.list_events(session, q="发布")] == ["GPT-6 发布", "Gemini 发布"]
        assert [item["title"] for item in event_board.list_events(session, q="%")] == []  # LIKE 通配符被转义
        assert len(event_board.list_events(session, limit=1)) == 1
