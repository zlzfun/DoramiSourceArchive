"""个人早报的系统换版(issue #343):新内容落地补编、偏好改动跟进、失败重试、旧版让位。

时间一律相对 ``NOW`` 并显式传入:巡检的「今天」「上午截止」都按传入的时刻算,不读墙上时钟。
"""

import datetime as dt
import json
import os
import sys

import pytest
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
from services import personal_digest as digest  # noqa: E402
from services import personal_digest_refresh as refresh  # noqa: E402
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
) -> str:
    """文章与分析行的时间都落在 ``at``——默认早于首版的编排时刻,模拟同步进来的行带上游时间。"""

    stamp = (at or NOW - dt.timedelta(hours=1, minutes=number)).isoformat()
    article_id = f"article-{number:02d}"
    session.add(ArticleRecord(
        id=article_id,
        title=f"Article {number}",
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


def test_preference_change_applies_after_noon_and_empties_on_last_unsubscribe(storage):
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

    # 上午截止只管「因新内容换版」;读者自己改的任何时候都跟上
    result = refresh.apply_preference_intents(storage.engine, now=afternoon + dt.timedelta(minutes=1))
    assert result == {"empty": 1}
    # 退订到一个源都不剩且没有兴趣:当日版本清成空订阅状态(沿用既有重编口径)
    assert _editions(storage) == [(1, "superseded", "scheduled")]
    with Session(storage.engine) as session:
        assert digest._latest_edition(session, "alice", TODAY) is None  # noqa: SLF001


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
