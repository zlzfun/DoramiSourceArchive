"""个人早报的系统换版(issue #343):新内容落地补编、偏好改动跟进、失败重试、旧版让位。

时间一律相对 ``NOW`` 并显式传入:巡检的「今天」「上午截止」都按传入的时刻算,不读墙上时钟。
"""

import datetime as dt
import json
import os
import sys
import threading
import time

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import DigestGenerationReason  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    CmsTagRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserInterestTagRecord,
    UserRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import personal_digest as digest  # noqa: E402
from services import personal_digest_refresh as refresh  # noqa: E402
from services import personal_digest_titles as digest_titles  # noqa: E402
from services import remote_sync  # noqa: E402
from services import title_cache  # noqa: E402
from services.briefs_contract import adapt_edition, envelope  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


SHANGHAI = dt.timezone(dt.timedelta(hours=8))
# 「今天」08:30;落地、静置、截止都从它往后推。
NOW = dt.datetime.combine(
    dt.datetime.now(SHANGHAI).date(), dt.time(8, 30), tzinfo=SHANGHAI
)
NOW_ISO = NOW.isoformat()
TODAY = NOW.date().isoformat()
UPSTREAM = "https://upstream.example"


@pytest.fixture
def storage(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'digest-refresh.db'}")
    with Session(value.engine) as session:
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true"))
        session.commit()
    yield value
    value.engine.dispose()


def _user(username: str = "alice", *, active: bool = True) -> UserRecord:
    return UserRecord(
        username=username,
        password_hash="hash",
        role="user",
        is_active=active,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _subscribe(username: str, source_ids: str, *, token: str = "token") -> ReaderSubscriptionRecord:
    return ReaderSubscriptionRecord(
        owner_username=username,
        name=f"sub-{token}",
        filters_json=json.dumps({"source_ids": source_ids}),
        token_hash=f"{username}-{token}",
        is_active=True,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _seed_article(
    session: Session,
    number: int,
    *,
    source: str = "rss_a",
    score: float = 8.0,
    at: dt.datetime | None = None,
    cached_title: str | None = None,
) -> str:
    """文章与分析行的时间都落在 ``at``——默认早于首版的编排时刻,模拟同步进来的行带上游时间。"""

    stamp = (at or NOW - dt.timedelta(hours=1, minutes=number)).isoformat()
    article_id = f"article-{number:02d}"
    title = f"Article {number}"
    session.add(ArticleRecord(
        id=article_id,
        title=title,
        extensions_json=json.dumps({
            title_cache.TITLE_KEY: cached_title,
            title_cache.TITLE_FP_KEY: title_cache.fingerprint(title),
        }) if cached_title else "{}",
        content_type="rss_article",
        source_id=source,
        source_url=f"https://example.com/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        content=f"full body {number}",
    ))
    session.flush()
    session.add(ArticleAnalysisRecord(
        article_id=article_id,
        status="succeeded",
        tagging_status="succeeded",
        quality_score=score,
        score_reason=f"reason {number}",
        summary=f"summary {number}",
        content_genre="industry_news",
        analyzed_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    ))
    return article_id


def _upstream_source(session: Session, source_id: str = "rss_a", *, completed_today: bool = False) -> None:
    """上游采集的公共源:本机没有采集任务,就绪看同步来的源状态。"""

    completed = NOW - dt.timedelta(minutes=40) if completed_today else NOW - dt.timedelta(days=1)
    session.add(SourceConfigRecord(
        source_id=source_id, name=source_id, collection_authority_id="producer-a",
        created_at=NOW_ISO, updated_at=NOW_ISO,
    ))
    session.add(SourceStateRecord(
        source_id=source_id, fetcher_id="generic_rss", status="healthy",
        authority_id="producer-a", last_completed_at=completed.isoformat(),
        updated_at=completed.isoformat(),
    ))


def _first_edition(session: Session, username: str = "alice", *, stale: bool = True):
    result = digest.generate_personal_digest(
        session, username, now=NOW, sync_stale=stale,
    )
    assert result.edition is not None
    return result.edition


def _sync_landed(
    storage, at: dt.datetime, *, inserted: int = 1, updated: int = 0, deleted: int = 0
) -> None:
    state = {"targets": {UPSTREAM: {"v2_streams": {"analyses": {
        "inserted": inserted, "updated": updated, "deleted": deleted,
        "completed_at": at.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    }}}}}
    with Session(storage.engine) as session:
        row = session.get(AppSettingRecord, "remote_sync:state")
        if row is None:
            row = AppSettingRecord(key="remote_sync:state", value="")
        row.value = json.dumps(state)
        session.add(row)
        session.commit()


def _editions(storage, username: str = "alice") -> list[tuple[int, str, str]]:
    with Session(storage.engine) as session:
        rows = session.exec(
            select(PersonalDigestEditionRecord)
            .where(PersonalDigestEditionRecord.owner_username == username)
            .order_by(PersonalDigestEditionRecord.revision)
        ).all()
        return [(row.revision, row.status, row.generation_reason) for row in rows]


def _article_ids(storage, revision: int, username: str = "alice") -> list[str]:
    with Session(storage.engine) as session:
        edition = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == username,
                PersonalDigestEditionRecord.revision == revision,
            )
        ).one()
        rows = session.exec(
            select(PersonalDigestItemRecord)
            .where(PersonalDigestItemRecord.edition_id == edition.id)
            .order_by(PersonalDigestItemRecord.position)
        ).all()
        return [row.article_id for row in rows]


def _alice_with_stale_edition(storage) -> None:
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _upstream_source(session)
        _seed_article(session, 1, score=7.0)
        session.commit()
        _first_edition(session)


# --------------------------------------------------------------------------- 新内容落地


def test_content_landing_waits_for_quiet_then_swaps_today_edition(storage):
    _alice_with_stale_edition(storage)
    landing = NOW + dt.timedelta(minutes=35)
    with Session(storage.engine) as session:
        # 同步进来的行带的是上游时间,早于本机 08:30 的编排时刻——只能靠本机的落地时刻判断
        new_id = _seed_article(session, 2, score=9.0)
        session.commit()
    _sync_landed(storage, landing)

    assert refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=1)) == {}
    assert _editions(storage) == [(1, "degraded", "scheduled")]

    counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    assert counts["published"] == 1 and counts["failed"] == 0
    assert _editions(storage) == [
        (1, "superseded", "scheduled"),
        (2, "degraded", "content_arrived"),
    ]
    assert new_id in _article_ids(storage, 2)
    with Session(storage.engine) as session:
        current = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert current.revision == 2
        # 与带租约通道发布的版本同口径:机器人接口的投影已固化
        frozen = json.loads(current.briefs_payload_json)
        assert frozen["revision"] == 2 and frozen["status"] == "ready"
        # 源状态还没落地(仍是昨天的):这一版仍带标记,但生成时刻已晚于落地时刻
        assert current.sync_stale is True

    # 水位已推进:同一批内容不会再触发
    assert refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=9)) == {}
    assert len(_editions(storage)) == 2


def test_content_landing_with_same_layout_writes_nothing(storage):
    _alice_with_stale_edition(storage)
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)  # 落地了,但没有哪篇能改变这位读者的版面

    counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    assert counts["unchanged"] == 1 and counts["published"] == 0
    assert _editions(storage) == [(1, "degraded", "scheduled")]
    assert refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=9)) == {}


def test_content_refresh_stops_at_noon_and_ignores_empty_sync_rounds(storage):
    _alice_with_stale_edition(storage)
    with Session(storage.engine) as session:
        _seed_article(session, 2, score=9.0)
        session.commit()

    # 一轮同步里分析流没有任何变化:完成时刻刷新了,但不算落地
    _sync_landed(storage, NOW + dt.timedelta(minutes=35), inserted=0)
    assert refresh.refresh_for_new_content(storage.engine, now=NOW + dt.timedelta(minutes=45)) == {}

    # 过了上午截止才落地:今天不再因新内容换版
    noon = dt.datetime.combine(NOW.date(), refresh.CONTENT_CUTOFF, tzinfo=SHANGHAI)
    _sync_landed(storage, noon - dt.timedelta(minutes=2))
    assert refresh.refresh_for_new_content(storage.engine, now=noon + dt.timedelta(minutes=2)) == {}
    assert _editions(storage) == [(1, "degraded", "scheduled")]


def test_complete_editions_and_inactive_accounts_are_left_alone(storage):
    with Session(storage.engine) as session:
        session.add_all([_user("alice"), _user("bob"), _user("carol", active=False)])
        for name in ("alice", "bob", "carol"):
            session.add(_subscribe(name, "rss_a"))
        _upstream_source(session, completed_today=True)
        _seed_article(session, 1, score=7.0)
        session.commit()
        _first_edition(session, "alice", stale=False)  # 内容齐备时编的:当天不再动
        _first_edition(session, "bob", stale=True)
        _first_edition(session, "carol", stale=True)  # 停用账号
        _seed_article(session, 2, score=9.0)
        session.commit()
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)

    counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    assert counts["published"] == 1
    assert _editions(storage, "alice") == [(1, "ready", "scheduled")]
    assert _editions(storage, "carol") == [(1, "degraded", "scheduled")]
    bob = _editions(storage, "bob")
    assert bob[0][:2] == (1, "superseded")
    # 上游的源状态今天已完成:补编出来的这一版不再带标记,当天定稿
    assert bob[1] == (2, "ready", "content_arrived")


def test_local_analysis_of_public_sources_counts_as_landing(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a,user_rss_mine"))
        session.add(SourceConfigRecord(
            source_id="user_rss_mine", name="mine", owner_username="alice",
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        _seed_article(session, 1, score=7.0)
        session.commit()
        _first_edition(session)
        private_id = _seed_article(session, 2, source="user_rss_mine", score=9.5)
        public_id = _seed_article(session, 3, score=9.0)
        session.commit()
    landed = NOW + dt.timedelta(minutes=20)

    # 读者自定义源的分析不算落地:否则全员的早报会跟着某个人的自定义源重算
    assert refresh.note_local_analysis(storage.engine, [private_id], now=landed) is False
    assert refresh.refresh_for_new_content(storage.engine, now=landed + dt.timedelta(minutes=4)) == {}

    assert refresh.note_local_analysis(storage.engine, [private_id, public_id], now=landed) is True
    counts = refresh.refresh_for_new_content(storage.engine, now=landed + dt.timedelta(minutes=4))
    assert counts["published"] == 1
    assert {public_id, private_id} <= set(_article_ids(storage, 2))


def test_content_that_keeps_trickling_in_is_not_waited_on_forever(storage):
    _alice_with_stale_edition(storage)
    with Session(storage.engine) as session:
        _seed_article(session, 2, score=9.0)
        session.commit()
    start = NOW + dt.timedelta(minutes=30)
    step = dt.timedelta(seconds=refresh.CONTENT_QUIET_SECONDS - 60)
    tick = start
    # 每隔两分钟又来一点:距最近一次落地总是不满静置时间
    while (tick - start).total_seconds() < refresh.CONTENT_MAX_WAIT_SECONDS:
        _sync_landed(storage, tick - dt.timedelta(seconds=30))
        assert refresh.refresh_for_new_content(storage.engine, now=tick) == {}
        tick += step
    _sync_landed(storage, tick - dt.timedelta(seconds=30))
    counts = refresh.refresh_for_new_content(storage.engine, now=tick)
    assert counts["published"] == 1
    # 这一批处理完,下一批重新计等待
    later = tick + dt.timedelta(minutes=10)
    _sync_landed(storage, later - dt.timedelta(seconds=30))
    assert refresh.refresh_for_new_content(storage.engine, now=later) == {}


def test_content_pass_retries_a_failed_batch_then_gives_up(storage, monkeypatch):
    _alice_with_stale_edition(storage)
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(refresh, "publish_refresh", boom)
    when = landing + dt.timedelta(minutes=4)
    for attempt in range(1, refresh.CONTENT_PASS_MAX_TRIES):
        assert refresh.refresh_for_new_content(storage.engine, now=when)["failed"] == 1
        with Session(storage.engine) as session:
            state = json.loads(session.get(AppSettingRecord, refresh.CONTENT_REFRESH_STATE_KEY).value)
        assert state["tries"] == attempt and "mark" not in state
    assert refresh.refresh_for_new_content(storage.engine, now=when)["failed"] == 1
    with Session(storage.engine) as session:
        state = json.loads(session.get(AppSettingRecord, refresh.CONTENT_REFRESH_STATE_KEY).value)
    assert state == {"mark": landing.isoformat()}  # 放弃这一批,等下一批内容
    assert refresh.refresh_for_new_content(storage.engine, now=when) == {}


# --------------------------------------------------------------------------- 偏好改动


def _tag(session: Session, code: str = "topic-agents") -> int:
    tag = CmsTagRecord(
        code=code, kind="topic", name_zh=code, normalized_name=code, status="active",
        user_selectable=True, created_at=NOW_ISO, updated_at=NOW_ISO,
    )
    session.add(tag)
    session.flush()
    return tag.id


def _follow(session: Session, username: str, tag_id: int, *, at: dt.datetime) -> None:
    session.add(UserInterestTagRecord(
        owner_username=username, tag_id=tag_id, stance="follow", priority="normal",
        source="explicit", created_at=at.isoformat(), updated_at=at.isoformat(),
    ))


def _marker(storage, revision: int = 1, username: str = "alice"):
    with Session(storage.engine) as session:
        row = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == username,
                PersonalDigestEditionRecord.revision == revision,
            )
        ).one()
        return row.desired_generation_reason, row.desired_requested_at


def test_preference_change_is_debounced_then_published_once(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        other_id = _tag(session, "topic-chips")
        session.commit()

        # 今天还没有版本:什么都不记,首版本来就按最新的偏好编
        assert digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=NOW
        ) is None
        _first_edition(session, stale=False)

        # 偏好其实没变:不记
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=NOW
        )
        assert _marker(storage) == (None, None)

        first_edit = NOW + dt.timedelta(minutes=5)
        _follow(session, "alice", tag_id, at=first_edit)
        session.commit()
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=first_edit
        )
        second_edit = first_edit + dt.timedelta(seconds=20)
        _follow(session, "alice", other_id, at=second_edit)
        session.commit()
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=second_edit
        )
    assert _marker(storage) == ("interest_changed", second_edit.isoformat())

    # 距最后一次改动不足静置时间:先不动(第一次改动已经过去 40 秒也不算)
    waiting = refresh.apply_preference_intents(storage.engine, now=second_edit + dt.timedelta(seconds=20))
    assert waiting == {"waiting": 1}
    assert _editions(storage) == [(1, "ready", "scheduled")]

    done = refresh.apply_preference_intents(storage.engine, now=second_edit + dt.timedelta(seconds=31))
    assert done == {"published": 1}
    assert _editions(storage) == [(1, "superseded", "scheduled"), (2, "ready", "interest_changed")]
    assert _marker(storage, 1) == (None, None)
    with Session(storage.engine) as session:
        current = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert digest.edition_freshness(session, current) == {
            "interest_stale": False, "scope_stale": False,
        }
    assert refresh.apply_preference_intents(storage.engine, now=second_edit + dt.timedelta(minutes=5)) == {}


def test_preference_change_reverted_before_the_quiet_period_publishes_nothing(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        session.add(_subscribe("alice", "rss_b", token="b"))
        _seed_article(session, 1)
        session.commit()
        _first_edition(session, stale=False)
        extra = session.exec(
            select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.name == "sub-b")
        ).one()
        extra.is_active = False
        session.add(extra)
        session.commit()
        edited = NOW + dt.timedelta(minutes=5)
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=edited
        )
        assert _marker(storage)[0] == "subscription_changed"
        extra.is_active = True  # 又订回来了
        session.add(extra)
        session.commit()

    result = refresh.apply_preference_intents(storage.engine, now=edited + dt.timedelta(minutes=1))
    assert result == {"cleared": 1}
    assert _editions(storage) == [(1, "ready", "scheduled")]
    assert _marker(storage) == (None, None)


def _bot_state(storage, username: str = "alice") -> str:
    """机器人接口眼里的今天:只取最大 revision、不看状态(docs/contracts/admin_reader_briefs.md)。"""

    from api.routers.personal_briefs import serialize_edition

    with Session(storage.engine) as session:
        edition = session.exec(
            select(PersonalDigestEditionRecord)
            .where(PersonalDigestEditionRecord.owner_username == username)
            .order_by(PersonalDigestEditionRecord.revision.desc())
        ).first()
        if edition.briefs_payload_json and edition.status in {"ready", "degraded"}:
            projected = adapt_edition(json.loads(edition.briefs_payload_json))
        else:
            projected = adapt_edition(serialize_edition(session, edition))
        return envelope("l00000001", username, projected)["status"]


def test_emptied_preferences_publish_an_empty_edition_and_resubscribing_recovers(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
        _first_edition(session, stale=False)
        row = session.exec(select(ReaderSubscriptionRecord)).one()
        row.is_active = False
        session.add(row)
        session.commit()
        afternoon = dt.datetime.combine(NOW.date(), dt.time(15, 0), tzinfo=SHANGHAI)
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=afternoon
        )
    assert _bot_state(storage) == "ready"

    # 上午截止只管「因新内容换版」;读者自己改的任何时候都跟上
    result = refresh.apply_preference_intents(storage.engine, now=afternoon + dt.timedelta(minutes=1))
    assert result == {"empty": 1}
    # 订阅与兴趣都空:落成一版零条目的终态版,而不是把今天的版本全标成被换下——
    # 机器人只取最大 revision,那样它会看到同一个 revision 从成功变成失败
    rows = _editions(storage)
    assert rows[0] == (1, "superseded", "scheduled")
    assert rows[1][0] == 2 and rows[1][1] in {"ready", "degraded"} and rows[1][2] == "subscription_changed"
    assert _article_ids(storage, 2) == []
    assert _bot_state(storage) == "empty"
    with Session(storage.engine) as session:
        empty = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert refresh.is_empty_preferences_edition(session, empty)

        # 重新订阅:空版继续承载意向,巡检自动恢复
        row = session.exec(select(ReaderSubscriptionRecord)).one()
        row.is_active = True
        session.add(row)
        session.commit()
        back = afternoon + dt.timedelta(minutes=10)
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=back
        )
    assert _marker(storage, 2)[0] == "subscription_changed"
    assert refresh.apply_preference_intents(storage.engine, now=back + dt.timedelta(minutes=1)) == {
        "published": 1
    }
    assert [row[:2] for row in _editions(storage)] == [(1, "superseded"), (2, "superseded"), (3, "ready")]
    assert _article_ids(storage, 3) == ["article-01"]
    assert _bot_state(storage) == "ready"


def test_preferences_emptied_while_an_edition_is_generating_still_end_in_an_empty_edition(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
        pending = digest.start_personal_digest_edition(session, "alice", now=NOW).edition
        carrier, token = digest.claim_personal_digest_generation(session, pending.id, now=NOW)
        assert carrier.status == "generating"
        row = session.exec(select(ReaderSubscriptionRecord)).one()
        row.is_active = False
        session.add(row)
        session.commit()
        edited = NOW + dt.timedelta(seconds=5)
        # 在途时清空偏好:只记意向,不把今天的版本全标成被换下
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=edited
        )
        assert _editions(storage) == [(1, "generating", "scheduled")]
        assert _marker(storage) == ("subscription_changed", edited.isoformat())
        # 在途期间巡检不处理这条意向
        assert refresh.apply_preference_intents(storage.engine, now=edited + dt.timedelta(minutes=1)) == {}
        digest.generate_personal_digest(
            session, "alice", now=NOW, pending_edition_id=pending.id, generation_token=token,
        )
    # 它按冻结的旧范围落成之后,意向才被处理:换成空版
    assert _editions(storage)[0][:2] == (1, "ready")
    assert refresh.apply_preference_intents(storage.engine, now=edited + dt.timedelta(minutes=2)) == {
        "empty": 1
    }
    assert _article_ids(storage, 2) == []
    assert _bot_state(storage) == "empty"


def test_empty_edition_never_carries_items_even_when_a_breaking_event_qualifies(storage):
    """「无订阅不出报」:空版连不看订阅的「重大事件」也不放——否则读者端按「范围空、兴趣空」把它
    映射成空订阅引导时,会把一版实际有条目的早报藏起来。"""

    with Session(storage.engine) as session:
        session.add(_user())
        entity = CmsTagRecord(
            code="entity.openai", kind="entity", name_zh="OpenAI", normalized_name="openai",
            status="active", user_selectable=True, entity_type="organization",
            created_at=NOW_ISO, updated_at=NOW_ISO,
        )
        session.add(entity)
        session.flush()
        headline = _seed_article(session, 2, source="rss_openai_news", score=9.6)
        second = _seed_article(session, 3, source="x_openai", score=9.8)
        from models.db import ArticleTagAssignmentRecord

        for article_id in (headline, second):
            session.add(ArticleTagAssignmentRecord(
                article_id=article_id, tag_id=entity.id, tag_kind="entity", relevance=0.9,
                created_at=NOW_ISO, updated_at=NOW_ISO,
            ))
        session.commit()
        # 有订阅时这条官方一手确实够格上头条(证明造的数据有效)
        session.add(_subscribe("alice", "rss_a"))
        session.commit()
        with_scope = digest.generate_personal_digest(session, "alice", now=NOW, dry_run=True)
        assert any(item.selection_lane == "breaking" for item in with_scope.items)
        row = session.exec(select(ReaderSubscriptionRecord)).one()
        row.is_active = False
        session.add(row)
        session.commit()

        assert digest.generate_personal_digest(session, "alice", now=NOW).status == "empty_subscriptions"
        empty = digest.generate_personal_digest(session, "alice", now=NOW, allow_empty=True)
        assert empty.edition is not None and empty.items == ()
        assert refresh.is_empty_preferences_edition(session, empty.edition)


def test_intent_left_on_a_replaced_edition_follows_the_current_one(storage):
    _alice_with_stale_edition(storage)
    edited = NOW + dt.timedelta(minutes=30)
    landing = NOW + dt.timedelta(minutes=35)
    with Session(storage.engine) as session:
        # 换下第 1 版的那一版先冻结了旧偏好,读者随后才改——意向却记在了第 1 版上
        _seed_article(session, 2, score=9.0)
        session.commit()
        second = digest.generate_personal_digest(
            session, "alice", now=landing,
            generation_reason=DigestGenerationReason.CONTENT_ARRIVED,
        ).edition
        assert second.revision == 2
        _follow(session, "alice", _tag(session), at=landing + dt.timedelta(seconds=5))
        first = session.exec(
            select(PersonalDigestEditionRecord).where(PersonalDigestEditionRecord.revision == 1)
        ).one()
        first.desired_generation_reason = "interest_changed"
        first.desired_requested_at = edited.isoformat()
        session.add(first)
        session.commit()

    moved = refresh.apply_preference_intents(storage.engine, now=landing + dt.timedelta(seconds=10))
    assert moved == {"moved": 1}
    assert _marker(storage, 1) == (None, None)
    assert _marker(storage, 2) == ("interest_changed", edited.isoformat())

    done = refresh.apply_preference_intents(storage.engine, now=landing + dt.timedelta(minutes=1))
    assert done == {"published": 1}
    assert [row[:2] for row in _editions(storage)] == [
        (1, "superseded"), (2, "superseded"), (3, "degraded"),
    ]


def test_failed_preference_publish_pushes_the_intent_back(storage, monkeypatch):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        _first_edition(session, stale=False)
        edited = NOW + dt.timedelta(minutes=5)
        _follow(session, "alice", tag_id, at=edited)
        session.commit()
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=edited
        )

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(refresh, "publish_refresh", boom)
    tick = edited + dt.timedelta(minutes=1)
    assert refresh.apply_preference_intents(storage.engine, now=tick) == {"failed": 1}
    retry_at = tick + dt.timedelta(seconds=refresh.PREFERENCE_RETRY_SECONDS)
    assert _marker(storage) == ("interest_changed", retry_at.isoformat())
    assert refresh.apply_preference_intents(
        storage.engine, now=tick + dt.timedelta(seconds=30)
    ) == {"waiting": 1}


def test_lifecycle_scan_leaves_preference_intents_to_the_refresh_pass(storage):
    from api.routers import personal_briefs

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        # 这条巡检按墙上时钟认「今天」,版本也建在真实的今天
        real_now = dt.datetime.now(SHANGHAI)
        digest.generate_personal_digest(session, "alice", now=real_now)
        _follow(session, "alice", tag_id, at=real_now)
        session.commit()
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=real_now
        )

    personal_briefs.process_pending_editions(storage.engine)
    # 没有被落成在途版本,也没有被清掉:留给静置合并那条路
    rows = _editions(storage)
    assert len(rows) == 1 and rows[0][1] in {"ready", "degraded"}
    assert _marker(storage)[0] == "interest_changed"


# --------------------------------------------------------------------------- 失败重试


def _failed_edition(storage, *, failed_at: dt.datetime, username: str = "alice") -> None:
    with Session(storage.engine) as session:
        revision = digest._latest_revision(session, username, TODAY) + 1  # noqa: SLF001
        session.add(PersonalDigestEditionRecord(
            owner_username=username, report_date=TODAY, revision=revision, status="failed",
            check_after=NOW_ISO, cutoff_at=NOW_ISO, generation_reason="scheduled",
            error="generation_failed", created_at=failed_at.isoformat(),
            updated_at=failed_at.isoformat(),
        ))
        session.commit()


def _fail_latest(storage, at: dt.datetime) -> None:
    with Session(storage.engine) as session:
        latest = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        latest.status = "failed"
        latest.error = "generation_failed"
        latest.updated_at = at.isoformat()
        session.add(latest)
        session.commit()


def test_failed_edition_is_retried_on_a_growing_schedule_then_left_alone(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
    _failed_edition(storage, failed_at=NOW)
    started: list[int] = []

    def keep_pending(engine, edition_id):
        started.append(edition_id)

    when = NOW
    for delay in refresh.FAILURE_RETRY_DELAYS:
        assert refresh.retry_failed_editions(
            storage.engine, now=when + dt.timedelta(seconds=delay - 1), run=keep_pending
        ) == 0
        when = when + dt.timedelta(seconds=delay)
        assert refresh.retry_failed_editions(storage.engine, now=when, run=keep_pending) == 1
        # 在途时不重复发起
        assert refresh.retry_failed_editions(storage.engine, now=when, run=keep_pending) == 0
        _fail_latest(storage, when)

    assert len(started) == len(refresh.FAILURE_RETRY_DELAYS)
    assert [row[2] for row in _editions(storage)] == ["scheduled", "recovery", "recovery", "recovery"]
    # 重试机会用尽:当天不再试,页面据此不再等
    assert refresh.retry_failed_editions(
        storage.engine, now=when + dt.timedelta(hours=3), run=keep_pending
    ) == 0
    with Session(storage.engine) as session:
        latest = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert refresh.failure_retry_due_at(session, latest) is None
        assert refresh.refresh_pending(session, latest, now=when) is None


def test_successful_retry_hides_the_failed_edition(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
    _failed_edition(storage, failed_at=NOW)
    when = NOW + dt.timedelta(seconds=refresh.FAILURE_RETRY_DELAYS[0])

    def generate(engine, edition_id):
        with Session(engine) as session:
            digest.generate_personal_digest(
                session, "alice", now=when, pending_edition_id=edition_id,
            )

    assert refresh.retry_failed_editions(storage.engine, now=when, run=generate) == 1
    assert _editions(storage) == [(1, "superseded", "scheduled"), (2, "ready", "recovery")]


# --------------------------------------------------------------------------- 旧版让位 / 页面状态


def test_any_new_edition_replaces_the_earlier_ones_but_keeps_them_readable(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
        first = _first_edition(session, stale=False)
        first_id = first.id
        rebuilt = digest.generate_personal_digest(
            session, "alice", now=NOW + dt.timedelta(minutes=10),
            generation_reason=DigestGenerationReason.MANUAL_REBUILD,
        )
        assert rebuilt.edition.revision == 2
    assert _editions(storage) == [(1, "superseded", "scheduled"), (2, "ready", "manual_rebuild")]
    with Session(storage.engine) as session:
        # 行与条目快照都还在:带着旧版本号回来仍能读到
        old = session.get(PersonalDigestEditionRecord, first_id)
        assert old.generated_at is not None
        assert session.exec(
            select(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id == first_id)
        ).all()


def test_dry_run_plans_without_writing(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        article_id = _seed_article(session, 1)
        session.commit()
        planned = digest.generate_personal_digest(session, "alice", now=NOW, dry_run=True)
        assert planned.edition is None
        assert [item.article_id for item in planned.items] == [article_id]
        assert session.exec(select(PersonalDigestEditionRecord)).all() == []
        assert session.exec(select(PersonalDigestItemRecord)).all() == []
        # 试算不占版本号
        assert digest.generate_personal_digest(session, "alice", now=NOW).edition.revision == 1


def test_refresh_pending_tells_the_page_whether_to_come_back(storage):
    _alice_with_stale_edition(storage)
    noon = dt.datetime.combine(NOW.date(), refresh.CONTENT_CUTOFF, tzinfo=SHANGHAI)
    with Session(storage.engine) as session:
        edition = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert refresh.refresh_pending(session, edition, now=NOW) == "content"
        assert refresh.refresh_pending(session, edition, now=noon) is None
        # 往期的版本不会再动
        assert refresh.refresh_pending(session, edition, now=NOW + dt.timedelta(days=1)) is None

        edition.desired_generation_reason = "interest_changed"
        edition.desired_requested_at = noon.isoformat()
        session.add(edition)
        session.commit()
        assert refresh.refresh_pending(session, edition, now=noon) == "soon"

        edition.desired_generation_reason = None
        edition.desired_requested_at = None
        edition.sync_stale = False
        session.add(edition)
        session.commit()
        assert refresh.refresh_pending(session, edition, now=NOW) is None

    _failed_edition(storage, failed_at=NOW)
    with Session(storage.engine) as session:
        failed = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert failed.status == "failed"
        assert refresh.refresh_pending(session, failed, now=NOW) == "soon"


# --------------------------------------------------------------------------- 巡检接线


def test_pending_job_runs_the_refresh_cycle_and_one_broken_step_does_not_stop_the_rest(
    storage, monkeypatch
):
    import api.app as app_module

    monkeypatch.setattr(app_module, "db_sink", storage)
    ran: list[str] = []

    def boom(engine, **kwargs):
        ran.append("preferences")
        raise RuntimeError("database is locked")

    monkeypatch.setattr(refresh, "apply_preference_intents", boom)
    monkeypatch.setattr(
        refresh, "retry_failed_editions", lambda engine, **kwargs: ran.append("failures")
    )
    monkeypatch.setattr(
        refresh, "refresh_for_new_content", lambda engine, **kwargs: ran.append("content")
    )
    app_module.execute_personal_digest_pending_job()
    assert ran == ["preferences", "failures", "content"]


# --------------------------------------------------------------------------- 检视 R1 的修复


def _editions_detail(storage, username: str = "alice"):
    with Session(storage.engine) as session:
        rows = session.exec(
            select(PersonalDigestEditionRecord)
            .where(PersonalDigestEditionRecord.owner_username == username)
            .order_by(PersonalDigestEditionRecord.revision)
        ).all()
        return [
            (row.revision, row.status, json.loads(row.expected_source_ids_json)) for row in rows
        ]


def test_publish_rechecks_the_current_edition_and_scope_inside_the_write_lock(storage, monkeypatch):
    """R1 #1:试算到拿写锁之间(备标题可能调模型)别的触发换了版 / 改了范围,旧输入不能盖回去。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        session.add(_subscribe("alice", "rss_b", token="b"))
        _upstream_source(session, "rss_a")
        _upstream_source(session, "rss_b")
        _seed_article(session, 1, source="rss_a", score=7.0)
        session.commit()
        _first_edition(session)
        _seed_article(session, 2, source="rss_a", score=9.0)
        _seed_article(session, 3, source="rss_b", score=8.5)
        session.commit()
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)
    real_warm = digest_titles.localize_edition_titles
    interleaved: list[str] = []

    def takedown_during_warmup(session, edition, **kwargs):
        # 管理员下架 rss_a,全员重编先一步落成了第 2 版(只含 rss_b)
        if not interleaved and kwargs.get("persist") is False:
            interleaved.append("takedown")
            with Session(storage.engine) as other:
                row = other.exec(
                    select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.name == "sub-token")
                ).one()
                row.is_active = False
                other.add(row)
                other.commit()
                digest.generate_personal_digest(
                    other, "alice", now=landing + dt.timedelta(minutes=4),
                    generation_reason=DigestGenerationReason.SUBSCRIPTION_CHANGED,
                )
        return real_warm(session, edition, **kwargs)

    monkeypatch.setattr(digest_titles, "localize_edition_titles", takedown_during_warmup)
    counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    assert interleaved == ["takedown"]
    assert counts["published"] == 0 and counts["skipped"] == 1 and counts["failed"] == 0
    # 没有第 3 版把 rss_a 放回去;当前版本就是下架后的那一版
    assert _editions_detail(storage) == [
        (1, "superseded", ["rss_a", "rss_b"]),
        (2, "ready", ["rss_b"]),
    ]


def test_publish_uses_the_scope_as_of_the_write_lock(storage, monkeypatch):
    """R1 #1 的另一半:期间读者退订了一个源但没有新版本出现——发布按锁内的范围,不按试算时的。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        session.add(_subscribe("alice", "rss_b", token="b"))
        _upstream_source(session, "rss_a")
        _upstream_source(session, "rss_b")
        _seed_article(session, 1, source="rss_b", score=7.0)
        session.commit()
        _first_edition(session)
        _seed_article(session, 2, source="rss_a", score=9.0)
        _seed_article(session, 3, source="rss_b", score=8.5)
        session.commit()
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)
    real_warm = digest_titles.localize_edition_titles
    done: list[int] = []

    def unsubscribe_during_warmup(session, edition, **kwargs):
        if not done and kwargs.get("persist") is False:
            done.append(1)
            with Session(storage.engine) as other:
                row = other.exec(
                    select(ReaderSubscriptionRecord).where(ReaderSubscriptionRecord.name == "sub-token")
                ).one()
                row.is_active = False
                other.add(row)
                other.commit()
        return real_warm(session, edition, **kwargs)

    monkeypatch.setattr(digest_titles, "localize_edition_titles", unsubscribe_during_warmup)
    counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    assert counts["published"] == 1
    detail = _editions_detail(storage)
    assert detail[-1][0] == 2 and detail[-1][2] == ["rss_b"]
    assert "article-02" not in _article_ids(storage, 2)


def test_published_edition_is_final_the_moment_it_becomes_visible(storage):
    """R1 #2:终态、带标题的条目、机器人投影在同一次提交里出现——同一 revision 的输出不会先后不一。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _upstream_source(session)
        _seed_article(session, 1, score=7.0)
        session.commit()
        _first_edition(session)
        _seed_article(session, 2, score=9.0, cached_title="缓存中文标题")
        session.commit()
    landing = NOW + dt.timedelta(minutes=35)
    _sync_landed(storage, landing)
    seen: list[tuple[bool, list[str | None]]] = []

    def at_commit(_session):
        # 每次提交之后用另一条连接看:第 2 版一旦可见,就必须已经是最终的样子
        with Session(storage.engine) as probe:
            edition = probe.exec(
                select(PersonalDigestEditionRecord).where(PersonalDigestEditionRecord.revision == 2)
            ).first()
            if edition is None or seen:
                return
            titles = [
                json.loads(item.snapshot_json).get("title_zh")
                for item in probe.exec(
                    select(PersonalDigestItemRecord)
                    .where(PersonalDigestItemRecord.edition_id == edition.id)
                    .order_by(PersonalDigestItemRecord.position)
                ).all()
            ]
            seen.append((edition.briefs_payload_json is not None, titles))

    event.listen(Session, "after_commit", at_commit)
    try:
        counts = refresh.refresh_for_new_content(storage.engine, now=landing + dt.timedelta(minutes=4))
    finally:
        event.remove(Session, "after_commit", at_commit)
    assert counts["published"] == 1
    assert seen, "第 2 版没有出现"
    frozen_at_first_sight, titles = seen[0]
    assert frozen_at_first_sight is True
    assert "缓存中文标题" in titles
    with Session(storage.engine) as session:
        edition = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        frozen = json.loads(edition.briefs_payload_json)
        assert [item["snapshot"].get("title_zh") for item in frozen["items"]] == titles


def _clone_edition_sql(revision: int, status: str) -> str:
    """裸连接上「另一个请求刚发布了一版」:把第 1 版复制成指定版次与状态。"""

    return (
        "INSERT INTO personal_digest_editions "
        "(owner_username, report_date, revision, status, check_after, cutoff_at, generated_at,"
        " expected_source_ids_json, due_source_ids_json, source_state_snapshot_json, policy_version,"
        " taxonomy_version, interest_version, interest_snapshot_json, generation_attempt_count,"
        " generation_reason, sync_stale, analysis_incomplete, created_at, updated_at) "
        "SELECT owner_username, report_date, ?, ?, check_after, cutoff_at, updated_at,"
        " expected_source_ids_json, due_source_ids_json, source_state_snapshot_json, policy_version,"
        " taxonomy_version, interest_version, interest_snapshot_json, 0, 'manual_rebuild', 0, 0,"
        " updated_at, updated_at FROM personal_digest_editions WHERE revision = 1"
    ), (revision, status)


def _run_while_holding_write_lock(storage, work, holder_sql: list[tuple[str, tuple]]):
    """确定性交错:另一条连接持写锁期间 ``work`` 必须卡在拿锁处;对方提交后它才继续。"""

    holder = storage.engine.raw_connection()
    outcome: list[object] = []
    try:
        holder.isolation_level = None  # 显式 BEGIN / COMMIT
        cursor = holder.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(target=lambda: outcome.append(work()))
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "没有等写锁就读了"
        for sql, params in holder_sql:
            cursor.execute(sql, params)
        cursor.execute("COMMIT")
    finally:
        holder.close()
    worker.join(timeout=30)
    assert not worker.is_alive()
    return outcome[0] if outcome else None


def test_intent_is_recorded_on_the_edition_that_is_current_after_taking_the_lock(storage):
    """R1 #4:读当前版、判落后、记意向在同一个写事务里——不会记到刚被换下的版本上而丢掉。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        _first_edition(session, stale=False)
        edited = NOW + dt.timedelta(minutes=5)
        _follow(session, "alice", tag_id, at=edited)
        session.commit()

    def record():
        with Session(storage.engine) as session:
            digest.request_preference_rebuild(
                session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=edited
            )

    clone_sql, clone_params = _clone_edition_sql(2, "ready")
    _run_while_holding_write_lock(storage, record, [
        ("UPDATE personal_digest_editions SET status = 'superseded' WHERE revision = 1", ()),
        (clone_sql, clone_params),
    ])
    # 第 2 版冻结的仍是旧兴趣:意向记在它上面,而不是落空
    assert _marker(storage, 1) == (None, None)
    assert _marker(storage, 2) == ("interest_changed", edited.isoformat())


def test_intent_waits_while_the_replacing_edition_is_still_generating(storage):
    """R1 #5:换下它的那一版还在编(冻结的是旧偏好)时,意向不清不挪;它落成后再挪过去。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        _first_edition(session, stale=False)
        # 重编先冻结了旧偏好并进入生成
        rebuild_at = NOW + dt.timedelta(minutes=5)
        pending = digest.start_personal_digest_edition(
            session, "alice", now=rebuild_at,
            generation_reason=DigestGenerationReason.MANUAL_REBUILD,
        ).edition
        _carrier, token = digest.claim_personal_digest_generation(session, pending.id, now=rebuild_at)
        # 读者随后才改兴趣,意向却挂在第 1 版上
        edited = rebuild_at + dt.timedelta(seconds=5)
        _follow(session, "alice", tag_id, at=edited)
        first = session.exec(
            select(PersonalDigestEditionRecord).where(PersonalDigestEditionRecord.revision == 1)
        ).one()
        first.desired_generation_reason = "interest_changed"
        first.desired_requested_at = edited.isoformat()
        session.add(first)
        session.commit()

        tick = edited + dt.timedelta(minutes=1)
        assert refresh.apply_preference_intents(storage.engine, now=tick) == {"waiting": 1}
        assert _marker(storage, 1) == ("interest_changed", edited.isoformat())

        digest.generate_personal_digest(
            session, "alice", now=rebuild_at, pending_edition_id=pending.id, generation_token=token,
        )
    assert [row[:2] for row in _editions(storage)] == [(1, "superseded"), (2, "ready")]
    assert refresh.apply_preference_intents(storage.engine, now=tick) == {"moved": 1}
    assert _marker(storage, 2) == ("interest_changed", edited.isoformat())
    assert refresh.apply_preference_intents(storage.engine, now=tick) == {"published": 1}
    assert [row[:2] for row in _editions(storage)][-1] == (3, "ready")


def _prepared_consumer(storage) -> None:
    with Session(storage.engine) as session:
        remote_sync.prepare_transaction_revision_consumer(
            session,
            base_url=UPSTREAM,
            username="admin",
            authority_id="producer-a",
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at=(NOW - dt.timedelta(days=1)).astimezone(dt.timezone.utc).isoformat(),
            capabilities=list(archive_sync_v2.ADVERTISED_CAPABILITIES),
        )
        session.commit()


def _record_stream(storage, stream: str, at: dt.datetime, **changes: int) -> None:
    remote_sync.record_v2_stream_success(
        storage.engine,
        base_url=UPSTREAM,
        username="admin",
        stream=stream,
        checkpoint={
            "authority_id": "producer-a", "snapshot": "1", "cursor": "",
            "inserted": 0, "updated": 0, "deleted": 0, **changes,
        },
        synced_at=at.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    )


def test_landing_survives_a_later_round_without_changes_and_counts_events(storage):
    """R1 #6 / #7:落地时刻随同步进度同一次提交记下、只增不减;事件流的变化同样算落地。"""

    _alice_with_stale_edition(storage)
    with Session(storage.engine) as session:
        _seed_article(session, 2, score=9.0)
        session.commit()
    _prepared_consumer(storage)
    first = NOW + dt.timedelta(minutes=35)
    _record_stream(storage, "analyses", first, inserted=3)
    # 还在静置,下一轮同步(没有任何变化)把 checkpoint 盖掉了
    assert refresh.refresh_for_new_content(storage.engine, now=first + dt.timedelta(seconds=30)) == {}
    _record_stream(storage, "analyses", first + dt.timedelta(minutes=1))
    with Session(storage.engine) as session:
        assert refresh.sync_landing_at(session) == first
    counts = refresh.refresh_for_new_content(storage.engine, now=first + dt.timedelta(minutes=4))
    assert counts["published"] == 1

    # 事件流稍后把两篇归成同一事件:也是一次落地,已发布的那版要按新的归属再判一遍
    later = first + dt.timedelta(minutes=20)
    _record_stream(storage, "events", later, updated=2)
    with Session(storage.engine) as session:
        assert refresh.sync_landing_at(session) == later
    again = refresh.refresh_for_new_content(storage.engine, now=later + dt.timedelta(minutes=4))
    assert again["unchanged"] == 1  # 这里的归属没有改变版面;关键是这一批被处理了
    # 没有变化的事件流不算
    _record_stream(storage, "events", later + dt.timedelta(minutes=30))
    with Session(storage.engine) as session:
        assert refresh.sync_landing_at(session) == later


def test_failed_edition_is_not_retried_over_one_that_was_just_published(storage):
    """R1 #8:写锁内复核——读到失败版之后别的请求先发布了一版好的,就不再往上压一条重试版。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
    _failed_edition(storage, failed_at=NOW)
    when = NOW + dt.timedelta(seconds=refresh.FAILURE_RETRY_DELAYS[0])
    started: list[int] = []
    clone_sql, clone_params = _clone_edition_sql(2, "ready")
    result = _run_while_holding_write_lock(
        storage,
        lambda: refresh.retry_failed_editions(
            storage.engine, now=when, run=lambda _engine, edition_id: started.append(edition_id)
        ),
        [(clone_sql, clone_params)],
    )
    assert result == 0 and started == []
    assert [row[:2] for row in _editions(storage)] == [(1, "failed"), (2, "ready")]


def test_content_refresh_does_not_publish_after_crossing_noon(storage):
    """R1 #9:11:59 开始的一轮,备完标题已过 12:00——写锁里再看一次时钟,不发布。"""

    _alice_with_stale_edition(storage)
    with Session(storage.engine) as session:
        _seed_article(session, 2, score=9.0)
        session.commit()
    noon = dt.datetime.combine(NOW.date(), refresh.CONTENT_CUTOFF, tzinfo=SHANGHAI)
    _sync_landed(storage, noon - dt.timedelta(minutes=10))
    ticks = iter([noon - dt.timedelta(minutes=1), noon - dt.timedelta(minutes=1)])

    def clock():
        return next(ticks, noon + dt.timedelta(minutes=1))

    counts = refresh.refresh_for_new_content(
        storage.engine, now=noon - dt.timedelta(minutes=1), clock=clock
    )
    assert counts["published"] == 0 and counts["skipped"] == 1
    assert _editions(storage) == [(1, "degraded", "scheduled")]
    # 逐账号处理前也看时钟:已过截止的直接跳过,不再试算
    _sync_landed(storage, noon - dt.timedelta(minutes=5))
    late = refresh.refresh_for_new_content(
        storage.engine, now=noon - dt.timedelta(seconds=30), clock=lambda: noon + dt.timedelta(minutes=2)
    )
    assert late["skipped"] == 1 and late["published"] == 0


def test_sweep_recovers_preference_changes_whose_intent_was_never_recorded(storage):
    """R1 #10:偏好已提交、意向没记上(进程停在两步之间、写锁超时、管理员侧改了范围)——补扫兜底。"""

    with Session(storage.engine) as session:
        session.add_all([_user("alice"), _user("bob"), _user("carol", active=False)])
        for name in ("alice", "bob", "carol"):
            session.add(_subscribe(name, "rss_a", token=name))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        for name in ("alice", "bob", "carol"):
            _first_edition(session, name, stale=False)
        edited = NOW + dt.timedelta(minutes=5)
        _follow(session, "alice", tag_id, at=edited)   # 改了兴趣,意向没记上
        _follow(session, "carol", tag_id, at=edited)   # 停用账号不管
        session.commit()

    swept = NOW + dt.timedelta(minutes=12)
    assert refresh.sweep_stale_preferences(storage.engine, now=swept) == 1
    assert _marker(storage, 1, "alice") == ("interest_changed", swept.isoformat())
    assert _marker(storage, 1, "bob") == (None, None)      # 偏好没变
    assert _marker(storage, 1, "carol") == (None, None)
    # 已有意向的不动——不提前、不后推
    assert refresh.sweep_stale_preferences(storage.engine, now=swept + dt.timedelta(minutes=10)) == 0
    assert _marker(storage, 1, "alice")[1] == swept.isoformat()
    assert refresh.apply_preference_intents(storage.engine, now=swept + dt.timedelta(minutes=1)) == {
        "published": 1
    }

    # 失败后退避到未来的意向不能被补扫提前
    with Session(storage.engine) as session:
        row = session.exec(select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == "bob"
        )).one()
        row.is_active = False
        session.add(row)
        session.add(_subscribe("bob", "rss_b", token="bob-b"))
        future = (swept + dt.timedelta(minutes=30)).isoformat()
        edition = digest._latest_edition(session, "bob", TODAY)  # noqa: SLF001
        edition.desired_generation_reason = "subscription_changed"
        edition.desired_requested_at = future
        session.add(edition)
        session.commit()
    assert refresh.sweep_stale_preferences(storage.engine, now=swept + dt.timedelta(minutes=20)) == 0
    assert _marker(storage, 1, "bob") == ("subscription_changed", future)


def test_sweep_also_covers_a_failed_edition_whose_retries_are_used_up(storage):
    """R1 #10:重试次数用尽之后读者再改偏好、意向又漏记——失败版同样被补扫;偏好没变的失败版不动。"""

    with Session(storage.engine) as session:
        session.add_all([_user("alice"), _user("bob")])
        session.add(_subscribe("alice", "rss_a", token="alice"))
        session.add(_subscribe("bob", "rss_a", token="bob"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        for name in ("alice", "bob"):
            for index in range(len(refresh.FAILURE_RETRY_DELAYS) + 1):
                session.add(PersonalDigestEditionRecord(
                    owner_username=name, report_date=TODAY, revision=index + 1, status="failed",
                    check_after=NOW_ISO, cutoff_at=NOW_ISO,
                    generation_reason="scheduled" if index == 0 else "recovery",
                    expected_source_ids_json=json.dumps(["rss_a"]),
                    error="generation_failed", created_at=NOW_ISO, updated_at=NOW_ISO,
                ))
        session.commit()
        latest = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        assert refresh.failure_retry_due_at(session, latest) is None  # 重试已用尽
        _follow(session, "alice", tag_id, at=NOW + dt.timedelta(hours=2))
        session.commit()

    swept = NOW + dt.timedelta(hours=3)
    assert refresh.sweep_stale_preferences(storage.engine, now=swept) == 1
    last = len(refresh.FAILURE_RETRY_DELAYS) + 1
    assert _marker(storage, last, "alice")[0] == "interest_changed"
    assert _marker(storage, last, "bob") == (None, None)
    done = refresh.apply_preference_intents(storage.engine, now=swept + dt.timedelta(minutes=1))
    assert done == {"published": 1}
    assert _editions(storage, "alice")[-1][1] == "ready"
    assert all(row[1] == "superseded" for row in _editions(storage, "alice")[:-1])
    assert all(row[1] == "failed" for row in _editions(storage, "bob"))


def test_refresh_pending_is_soon_for_an_edition_that_lags_behind_the_preferences(storage):
    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        session.commit()
        edition = _first_edition(session, stale=False)
        assert refresh.refresh_pending(session, edition, now=NOW) is None
        # 偏好已变、意向还没记上(写入点稍后记,或等补扫):页面也该回来取
        assert refresh.refresh_pending(session, edition, now=NOW, stale=True) == "soon"


def test_sync_checkpoint_helpers_ignore_malformed_values():
    assert remote_sync.checkpoint_changed({"inserted": "2"}) is True
    assert remote_sync.checkpoint_changed({"inserted": 0, "updated": None, "deleted": "x"}) is False
    assert remote_sync.checkpoint_changed(None) is False
    assert remote_sync._later_stamp("2026-11-25T01:05:00+00:00", None) is True  # noqa: SLF001
    assert remote_sync._later_stamp("2026-11-25T01:05:00+00:00", "not a time") is True  # noqa: SLF001
    assert remote_sync._later_stamp("2026-11-25T01:05:00+00:00", "2026-11-25T01:05:00Z") is False  # noqa: SLF001
    assert remote_sync._later_stamp("garbage", "2026-11-25T01:05:00Z") is False  # noqa: SLF001


def test_sweep_style_request_never_touches_an_existing_intent(storage):
    """补扫的写锁内复核(``only_if_absent``):已有意向——无论更早还是退避到未来——都不动;
    读者自己的新改动则把更早的意向往后推、不把未来的提前。"""

    with Session(storage.engine) as session:
        session.add(_user())
        session.add(_subscribe("alice", "rss_a"))
        _seed_article(session, 1)
        tag_id = _tag(session)
        session.commit()
        _first_edition(session, stale=False)
        edited = NOW + dt.timedelta(minutes=5)
        _follow(session, "alice", tag_id, at=edited)
        session.commit()
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED, now=edited
        )
        later = edited + dt.timedelta(minutes=3)
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=later,
            only_if_absent=True,
        )
        assert _marker(storage) == ("interest_changed", edited.isoformat())
        digest.request_preference_rebuild(
            session, "alice", reason=DigestGenerationReason.SUBSCRIPTION_CHANGED, now=later
        )
        assert _marker(storage) == ("subscription_changed", later.isoformat())

        future = (later + dt.timedelta(minutes=30)).isoformat()
        edition = digest._latest_edition(session, "alice", TODAY)  # noqa: SLF001
        edition.desired_requested_at = future
        session.add(edition)
        session.commit()
        for flag in (True, False):
            digest.request_preference_rebuild(
                session, "alice", reason=DigestGenerationReason.INTEREST_CHANGED,
                now=later + dt.timedelta(minutes=1), only_if_absent=flag,
            )
            assert _marker(storage) == ("subscription_changed", future)
