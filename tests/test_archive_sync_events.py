"""Archive Sync v3 contract tests for the events stream (issue #121)."""

import asyncio
import datetime as dt
import json
import os
import sys

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArchiveSyncEntityStateRecord,
    ArticleRecord,
    EventMemberRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import make_alembic_config  # noqa: E402


def _iso(hours_ago: float = 0.0) -> str:
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return moment.isoformat(timespec="seconds")


def _sink(tmp_path, name):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _source(source_id="rss_platform", **overrides):
    data = dict(
        source_id=source_id,
        name="Platform",
        source_type="rss",
        url=f"https://example.test/{source_id}.xml",
        category="official",
        fetcher_id="generic_rss",
        owner_username="",
        ai_analysis_enabled=True,
        is_active=True,
        fetch_interval_minutes=60,
        params_json='{"feed_url":"https://example.test/feed.xml"}',
        created_at=_iso(48),
        updated_at=_iso(47),
    )
    data.update(overrides)
    return SourceConfigRecord(**data)


def _article(article_id, *, source_id="rss_platform", hours_ago=10.0, **overrides):
    data = dict(
        id=article_id,
        title=f"Article {article_id}",
        content_type="rss_article",
        source_id=source_id,
        source_url=f"https://example.test/{article_id}",
        publish_date=_iso(hours_ago),
        fetched_date=_iso(hours_ago - 0.5),
        archive_updated_at=_iso(hours_ago - 0.5),
        has_content=True,
        content=f"Body of {article_id}",
        extensions_json="{}",
    )
    data.update(overrides)
    return ArticleRecord(**data)


def _event(*, hours_ago=8.0, **overrides):
    data = dict(
        status="open",
        title="Event",
        title_source="representative",
        member_count=0,
        publisher_count=0,
        first_seen_at=_iso(hours_ago),
        last_member_at=_iso(hours_ago),
        assignment_version="v1",
        created_at=_iso(hours_ago),
        updated_at=_iso(hours_ago),
    )
    data.update(overrides)
    return EventRecord(**data)


def _member(event_id, article_id, *, hours_ago=8.0, **overrides):
    data = dict(
        event_id=event_id,
        article_id=article_id,
        publisher_key="openai",
        report_at=_iso(hours_ago),
        first_seen_at=_iso(hours_ago),
        assigned_by="llm",
        confidence=0.9,
        prompt_version="p1",
        is_representative=False,
        created_at=_iso(hours_ago),
    )
    data.update(overrides)
    return EventMemberRecord(**data)


def _seed_producer(producer, *, article_ids=("a-1", "a-2", "a-3")):
    """源 + 三篇公共文章 + 两个事件(一个双成员、一个单例);返回两个事件 ID。"""
    with Session(producer.engine) as session:
        session.add(_source())
        for article_id in article_ids:
            session.add(_article(article_id))
        session.flush()
        launch = _event(
            title="Launch",
            member_count=2,
            publisher_count=2,
            representative_article_id=article_ids[0],
        )
        singleton = _event(title="Singleton", member_count=1, publisher_count=1)
        session.add_all([launch, singleton])
        session.flush()
        session.add_all([
            _member(launch.id, article_ids[0], is_representative=True, assigned_by="url"),
            _member(launch.id, article_ids[1], publisher_key="google"),
            _member(singleton.id, article_ids[2], assigned_by="singleton", confidence=None),
        ])
        session.commit()
        return launch.id, singleton.id


def _copy(producer, consumer, stream, *, limit=1000, since=""):
    raw = archive_sync_v2.export_page(producer.engine, stream, limit=limit, since=since)
    return raw, archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)


def _state(engine, identity):
    with Session(engine) as session:
        return session.get(ArchiveSyncEntityStateRecord, ("events", str(identity)))


def _member_ids(engine, event_id):
    with Session(engine) as session:
        return sorted(
            row.article_id
            for row in session.exec(
                select(EventMemberRecord).where(EventMemberRecord.event_id == event_id)
            ).all()
        )


def _trigger_names(engine):
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }


class _Remote:
    """v3 生产者假远端;``advertise_events=False`` 模拟还没有事件流的旧版本。"""

    def __init__(self, producer, *, advertise_events=True):
        self.producer = producer
        self.advertise_events = advertise_events
        self.requested_streams = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"user": {"role": "admin"}},
                headers={"set-cookie": "session=ok; Secure"},
            )
        if "session=ok" not in (request.headers.get("cookie") or ""):
            return httpx.Response(401)
        prefix = "/api/archive/v2/export/"
        if path.startswith(prefix):
            stream = path[len(prefix):].removesuffix(".jsonl")
            self.requested_streams.append(stream)
            params = dict(request.url.params)
            body = archive_sync_v2.export_page(
                self.producer.engine,
                stream,
                snapshot=params.get("snapshot", ""),
                since=params.get("since", ""),
                after=params.get("after", ""),
                limit=int(params.get("limit", 1000)),
            )
            if not self.advertise_events:
                lines = body.splitlines()
                manifest = json.loads(lines[0])
                manifest["capabilities"] = [
                    item
                    for item in manifest["capabilities"]
                    if item != archive_sync_v2.EVENTS_CAPABILITY
                ]
                body = "\n".join([json.dumps(manifest), *lines[1:]]) + "\n"
            return httpx.Response(200, text=body)
        if path == "/api/archive/v2/presence":
            payload = json.loads(request.content)
            return httpx.Response(200, json={
                "schema_version": archive_sync_v2.SCHEMA_VERSION,
                "capability": archive_sync_v2.AUTHORITATIVE_PRESENCE_CAPABILITY,
                "authority_id": archive_sync_v2.producer_authority_id(self.producer.engine),
                "stream": payload["stream"],
                "requested": payload["identities"],
                "present": archive_sync_v2.authority_present_identities(
                    self.producer.engine, payload["stream"], payload["identities"]
                ),
            })
        if path == "/api/archive/v2/candidate-evidence.jsonl":
            return httpx.Response(200, json={"status": "success", "inserted": 0, "skipped": 0})
        return httpx.Response(404)


def _pull(consumer, remote, tmp_path, **kwargs):
    return asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "media",
        transport=httpx.MockTransport(remote.handler),
        **kwargs,
    ))


# ── 能力协商与顺序 ──────────────────────────────────────────────────────────────


def test_events_capability_is_optional_and_ordered_after_analyses():
    assert archive_sync_v2.EVENTS_CAPABILITY == "events_v1"
    assert archive_sync_v2.EVENTS_CAPABILITY in archive_sync_v2.ADVERTISED_CAPABILITIES
    assert archive_sync_v2.EVENTS_CAPABILITY not in archive_sync_v2.REQUIRED_CAPABILITIES
    assert "events" in archive_sync_v2.STREAMS

    full = remote_sync_service.negotiated_v2_stream_order(
        archive_sync_v2.ADVERTISED_CAPABILITIES
    )
    assert full == remote_sync_service.V2_STREAM_ORDER
    assert full.index("events") == full.index("analyses") + 1
    assert full.index("events") < full.index("media")
    assert full[-1] == "source_states"
    legacy = remote_sync_service.negotiated_v2_stream_order(
        archive_sync_v2.REQUIRED_CAPABILITIES
    )
    assert "events" not in legacy
    assert legacy == remote_sync_service.V2_LEGACY_STREAM_ORDER


def test_pull_skips_events_when_peer_lacks_capability(tmp_path):
    producer = _sink(tmp_path, "producer-no-capability.db")
    consumer = _sink(tmp_path, "consumer-no-capability.db")
    _seed_producer(producer)
    remote = _Remote(producer, advertise_events=False)

    result = _pull(consumer, remote, tmp_path)

    assert "events" not in remote.requested_streams
    assert "events" not in result["streams"]
    assert remote.requested_streams == list(remote_sync_service.negotiated_v2_stream_order(
        result["capabilities"]
    ))
    with Session(consumer.engine) as session:
        assert session.exec(select(EventRecord)).all() == []
        assert session.get(ArticleRecord, "a-1") is not None


def test_pull_negotiates_events_between_analyses_and_media_and_is_incremental(tmp_path):
    producer = _sink(tmp_path, "producer-pull.db")
    consumer = _sink(tmp_path, "consumer-pull.db")
    launch_id, singleton_id = _seed_producer(producer)
    remote = _Remote(producer)
    completed = []

    result = _pull(
        consumer, remote, tmp_path,
        on_stream_complete=lambda stream, _checkpoint: completed.append(stream),
    )

    assert remote.requested_streams == list(remote_sync_service.V2_STREAM_ORDER)
    assert completed == list(remote_sync_service.V2_STREAM_ORDER)
    events_checkpoint = result["streams"]["events"]
    assert events_checkpoint["inserted"] == 2
    assert events_checkpoint["skipped"] == 0
    assert events_checkpoint["snapshot"] == result["streams"]["source_states"]["snapshot"]
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id).authority_id == result["authority_id"]
        assert session.get(EventRecord, singleton_id).title == "Singleton"
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]

    second = _pull(consumer, remote, tmp_path, checkpoints=result["streams"])
    assert second["streams"]["events"]["count"] == 0


# ── 导出分页 / checksum / 幂等 ────────────────────────────────────────────────


def test_event_pages_paginate_by_keyset_verify_checksums_and_apply_idempotently(tmp_path):
    producer = _sink(tmp_path, "producer-pages.db")
    consumer = _sink(tmp_path, "consumer-pages.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")

    first_raw = archive_sync_v2.export_page(producer.engine, "events", limit=1)
    manifest, rows = archive_sync_v2.parse_page(first_raw, expected_stream="events")
    assert manifest["complete"] is False and len(rows) == 1
    assert archive_sync_v2.EVENTS_CAPABILITY in manifest["capabilities"]
    row = rows[0]
    payload = row["payload"]
    assert row["kind"] == "event" and row["operation"] == "upsert"
    assert row["identity"] == str(payload["id"]) == str(payload["event"]["id"])
    assert row["checksum"] == archive_sync_v2.checksum(payload)
    assert set(payload) == {"id", "event", "members", "redirects_from", "merged_into"}
    assert "authority_id" not in payload["event"]

    tampered = json.loads(first_raw.splitlines()[1])
    tampered["payload"]["event"]["title"] = "forged"
    forged_page = "\n".join([first_raw.splitlines()[0], json.dumps(tampered)]) + "\n"
    with pytest.raises(archive_sync_v2.SyncV2Error, match="checksum"):
        archive_sync_v2.parse_page(forged_page, expected_stream="events")

    applied_first = archive_sync_v2.import_page(
        consumer.engine, first_raw, expected_stream="events"
    )
    second_raw = archive_sync_v2.export_page(
        producer.engine, "events",
        snapshot=manifest["snapshot"], after=manifest["next_cursor"], limit=1,
    )
    second_manifest, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="events",
        requested_snapshot=manifest["snapshot"], requested_after=manifest["next_cursor"],
    )
    assert second_manifest["complete"] is True and len(second_rows) == 1
    applied_second = archive_sync_v2.import_page(
        consumer.engine, second_raw, expected_stream="events"
    )
    assert applied_first["inserted"] + applied_second["inserted"] == 2
    assert applied_first["skipped"] == applied_second["skipped"] == 0

    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        launch = session.get(EventRecord, launch_id)
        assert launch.authority_id == authority
        assert launch.title == "Launch"
        assert launch.representative_article_id == "a-1"
        assert launch.member_count == 2 and launch.publisher_count == 2
        members = session.exec(
            select(EventMemberRecord)
            .where(EventMemberRecord.event_id == launch_id)
            .order_by(EventMemberRecord.article_id)
        ).all()
        assert [(m.article_id, m.assigned_by, m.is_representative, m.publisher_key) for m in members] == [
            ("a-1", "url", True, "openai"),
            ("a-2", "llm", False, "google"),
        ]
        singleton_member = session.get(EventMemberRecord, (singleton_id, "a-3"))
        assert singleton_member.assigned_by == "singleton"
        assert singleton_member.confidence is None
    # 接收端记的是远端状态,不会把同步来的事件再当成本机待导出
    assert _state(consumer.engine, launch_id).authority_id == authority
    assert archive_sync_v2.parse_page(
        archive_sync_v2.export_page(consumer.engine, "events"), expected_stream="events"
    )[1] == []

    replay = archive_sync_v2.import_page(consumer.engine, first_raw, expected_stream="events")
    assert replay["inserted"] == replay["updated"] == replay["deleted"] == 0
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]

    incremental = archive_sync_v2.export_page(
        producer.engine, "events", since=second_manifest["snapshot"]
    )
    inc_manifest, inc_rows = archive_sync_v2.parse_page(incremental, expected_stream="events")
    assert inc_rows == [] and inc_manifest["complete"] is True


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["event"].update(status="hot"), "status"),
        (lambda p: p["members"].append(dict(p["members"][0])), "duplicated"),
        (lambda p: p["members"][0].update(assigned_by="guess"), "assigned_by"),
        (lambda p: p["members"][0].update(confidence=1.5), "confidence"),
        (lambda p: p["redirects_from"].append(
            {"old_event_id": p["id"], "reason": "", "created_at": _iso()}
        ), "old_event_id"),
        (lambda p: p["event"].update(id=p["id"] + 1), "identity"),
    ],
    ids=["status", "duplicate-member", "assigned_by", "confidence", "self-redirect", "id-mismatch"],
)
def test_event_payload_validation_rejects_non_canonical_values(tmp_path, mutate, message):
    producer = _sink(tmp_path, "producer-validation.db")
    _seed_producer(producer)
    raw = archive_sync_v2.export_page(producer.engine, "events", limit=1)
    manifest_line, row_line = raw.splitlines()[:2]
    row = json.loads(row_line)
    mutate(row["payload"])
    row["checksum"] = archive_sync_v2.checksum(row["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match=message):
        archive_sync_v2.parse_page(
            "\n".join([manifest_line, json.dumps(row, ensure_ascii=False)]) + "\n",
            expected_stream="events",
        )


# ── 成员缺文章 / 私有文章 ──────────────────────────────────────────────────────


def test_members_without_local_article_are_skipped_and_local_articles_never_overwritten(tmp_path):
    producer = _sink(tmp_path, "producer-missing-member.db")
    consumer = _sink(tmp_path, "consumer-missing-member.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    # 文章流拉完之后生产端又多了一篇成员文章(下一轮文章流才会到)……
    with Session(producer.engine) as session:
        session.add(_article("a-late"))
        session.add(_article("a-local"))
        session.flush()
        session.add(_member(launch_id, "a-late", publisher_key="meta"))
        session.add(_member(singleton_id, "a-local", publisher_key="x"))
        session.commit()
    # ……而接收端自己有一篇同 ID 的私有订阅文章
    with Session(consumer.engine) as session:
        session.add(_source("user_rss_me", owner_username="me"))
        session.add(_article("a-local", source_id="user_rss_me", title="Private local article"))
        session.commit()

    _raw, applied = _copy(producer, consumer, "events")

    assert applied["inserted"] == 2
    assert applied["skipped"] == 2
    with Session(consumer.engine) as session:
        assert session.get(EventMemberRecord, (launch_id, "a-late")) is None
        assert session.get(EventMemberRecord, (singleton_id, "a-local")) is None
        local = session.get(ArticleRecord, "a-local")
        assert local.title == "Private local article"
        assert local.analysis_authority_id == ""
        assert local.source_id == "user_rss_me"
        # 计数列照抄 authority 的缓存值,读取侧按本地成员另算
        assert session.get(EventRecord, launch_id).member_count == 2
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]
    assert _member_ids(consumer.engine, singleton_id) == ["a-3"]


def test_private_or_owner_bound_articles_never_leave_the_producer_as_members(tmp_path):
    producer = _sink(tmp_path, "producer-private-member.db")
    launch_id, _singleton_id = _seed_producer(producer)
    with Session(producer.engine) as session:
        session.add(_source("user_rss_owner", owner_username="owner"))
        session.add(_source("rss_team", owner_username="team"))
        session.add(_article("private-1", source_id="user_rss_owner"))
        session.add(_article("team-1", source_id="rss_team"))
        session.flush()
        session.add(_member(launch_id, "private-1"))
        session.add(_member(launch_id, "team-1"))
        session.commit()

    _manifest, rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "events"), expected_stream="events"
    )
    launch = next(row for row in rows if row["identity"] == str(launch_id))
    assert [m["article_id"] for m in launch["payload"]["members"]] == ["a-1", "a-2"]


# ── tombstone / redirect ──────────────────────────────────────────────────────


def test_tombstone_removes_receiver_event_and_old_upsert_cannot_resurrect_it(tmp_path):
    producer = _sink(tmp_path, "producer-tombstone.db")
    consumer = _sink(tmp_path, "consumer-tombstone.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    full_raw, _applied = _copy(producer, consumer, "events")
    full_snapshot = archive_sync_v2.parse_page(full_raw, expected_stream="events")[0]["snapshot"]

    with Session(producer.engine) as session:
        session.delete(session.get(EventRecord, launch_id))
        session.commit()
    assert _state(producer.engine, launch_id).operation == "tombstone"

    raw = archive_sync_v2.export_page(producer.engine, "events", since=full_snapshot)
    _manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="events")
    assert [(row["identity"], row["operation"]) for row in rows] == [(str(launch_id), "tombstone")]
    assert rows[0]["payload"] == {"id": launch_id, "tombstone": True}

    applied = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="events")
    assert applied["deleted"] == 1
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id) is None
        assert session.get(EventRecord, singleton_id) is not None
        assert session.get(ArticleRecord, "a-1") is not None
    assert _member_ids(consumer.engine, launch_id) == []
    assert _member_ids(consumer.engine, singleton_id) == ["a-3"]

    replay = archive_sync_v2.import_page(consumer.engine, full_raw, expected_stream="events")
    assert replay["inserted"] == 0
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id) is None


@pytest.mark.parametrize("merge_into_lower_id", [True, False], ids=["old-id-higher", "old-id-lower"])
def test_merge_redirect_resolves_old_id_after_sync_across_page_boundary(tmp_path, merge_into_lower_id):
    producer = _sink(tmp_path, "producer-redirect.db")
    consumer = _sink(tmp_path, "consumer-redirect.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    old_id, new_id = (singleton_id, launch_id) if merge_into_lower_id else (launch_id, singleton_id)

    with Session(producer.engine) as session:
        session.exec(
            update(EventMemberRecord)
            .where(EventMemberRecord.event_id == old_id)
            .values(event_id=new_id)
        )
        old = session.get(EventRecord, old_id)
        old.status = "merged"
        old.member_count = 0
        session.add(old)
        session.add(EventRedirectRecord(
            old_event_id=old_id, new_event_id=new_id, reason="llm", created_at=_iso(1),
        ))
        session.commit()

    # 两个事件各占一页:无论旧事件那侧(merged_into)还是新事件那侧(redirects_from)后到,重定向都在。
    first_raw = archive_sync_v2.export_page(producer.engine, "events", limit=1)
    manifest, first_rows = archive_sync_v2.parse_page(first_raw, expected_stream="events")
    archive_sync_v2.import_page(consumer.engine, first_raw, expected_stream="events")
    with Session(consumer.engine) as session:
        assert session.get(EventRedirectRecord, old_id) is None
    second_raw = archive_sync_v2.export_page(
        producer.engine, "events", snapshot=manifest["snapshot"], after=manifest["next_cursor"], limit=1,
    )
    _second_manifest, second_rows = archive_sync_v2.parse_page(second_raw, expected_stream="events")
    archive_sync_v2.import_page(consumer.engine, second_raw, expected_stream="events")

    payloads = {row["identity"]: row["payload"] for row in (*first_rows, *second_rows)}
    assert payloads[str(new_id)]["redirects_from"] == [
        {"old_event_id": old_id, "reason": "llm", "created_at": payloads[str(old_id)]["merged_into"]["created_at"]}
    ]
    assert payloads[str(old_id)]["merged_into"]["new_event_id"] == new_id
    assert payloads[str(old_id)]["members"] == []
    with Session(consumer.engine) as session:
        redirect = session.get(EventRedirectRecord, old_id)
        assert redirect is not None
        assert redirect.new_event_id == new_id and redirect.reason == "llm"
        assert session.get(EventRecord, old_id).status == "merged"
    assert _member_ids(consumer.engine, new_id) == ["a-1", "a-2", "a-3"]
    assert _member_ids(consumer.engine, old_id) == []


# ── 围栏 ───────────────────────────────────────────────────────────────────────


def test_receiver_guard_blocks_local_members_in_authority_events(tmp_path):
    producer = _sink(tmp_path, "producer-guard.db")
    consumer = _sink(tmp_path, "consumer-guard.db")
    launch_id, _singleton_id = _seed_producer(producer)
    for stream in ("sources", "articles", "events"):
        _copy(producer, consumer, stream)
    with Session(consumer.engine) as session:
        session.add(_source("user_rss_me", owner_username="me"))
        session.add(_article("local-1", source_id="user_rss_me"))
        session.commit()

    # 本机分配(任何 assigned_by)都进不了同步来的事件
    with Session(consumer.engine) as session:
        session.add(_member(launch_id, "local-1", assigned_by="llm"))
        with pytest.raises(IntegrityError, match="same authority"):
            session.commit()
        session.rollback()
    with Session(consumer.engine) as session:
        with pytest.raises(IntegrityError, match="same authority"):
            session.exec(
                update(EventMemberRecord)
                .where(EventMemberRecord.event_id == launch_id, EventMemberRecord.article_id == "a-2")
                .values(article_id="local-1")
            )
        session.rollback()
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]

    # 本机自己的事件照常接受本机文章,且作为本机事件进入 events 流待导出
    with Session(consumer.engine) as session:
        local_event = _event(title="Local")
        session.add(local_event)
        session.flush()
        session.add(_member(local_event.id, "local-1"))
        session.commit()
        local_id = local_event.id
    assert _member_ids(consumer.engine, local_id) == ["local-1"]
    assert _state(consumer.engine, local_id).authority_id == ""


def test_local_event_under_a_synced_id_fails_the_page_closed(tmp_path):
    producer = _sink(tmp_path, "producer-collision.db")
    consumer = _sink(tmp_path, "consumer-collision.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        session.add_all([_event(title="Local A"), _event(title="Local B")])
        session.commit()
    with Session(consumer.engine) as session:
        session.delete(session.get(EventRecord, launch_id))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "events")
    with pytest.raises(archive_sync_v2.SyncV2Error, match="belongs to another authority"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream="events")

    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id) is None  # 整页回滚,第一条也没留下
        local = session.get(EventRecord, singleton_id)
        assert local.title == "Local B" and local.authority_id == ""
        authority = archive_sync_v2.producer_authority_id(producer.engine)
        assert session.exec(
            select(ArchiveSyncEntityStateRecord).where(
                ArchiveSyncEntityStateRecord.stream == "events",
                ArchiveSyncEntityStateRecord.authority_id == authority,
            )
        ).all() == []


def test_first_full_pull_prunes_authority_events_absent_from_producer(tmp_path):
    producer = _sink(tmp_path, "producer-prune.db")
    consumer = _sink(tmp_path, "consumer-prune.db")
    launch_id, singleton_id = _seed_producer(producer)
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        session.add(_event(id=998, title="Local survives"))
        session.add(_event(id=999, title="Stale authority copy", authority_id=authority))
        session.commit()

    result = _pull(consumer, _Remote(producer), tmp_path)

    assert result["streams"]["events"]["pruned"] == 1
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, 999) is None
        assert session.get(EventRecord, 998).title == "Local survives"
        assert session.get(EventRecord, launch_id).authority_id == authority
        assert session.get(EventRecord, singleton_id).authority_id == authority


# ── 生产端触发器 ──────────────────────────────────────────────────────────────


def test_member_and_redirect_changes_mark_the_owning_event_and_delete_is_tombstone(tmp_path):
    producer = _sink(tmp_path, "producer-triggers.db")
    launch_id, singleton_id = _seed_producer(producer)

    def revision(event_id):
        state = _state(producer.engine, event_id)
        return int(state.revision), state.operation

    base_launch = revision(launch_id)
    base_singleton = revision(singleton_id)
    assert base_launch[1] == base_singleton[1] == "upsert"

    with Session(producer.engine) as session:
        session.add(_article("a-4"))
        session.flush()
        session.add(_member(launch_id, "a-4", publisher_key="meta"))
        session.commit()
    after_insert = revision(launch_id)
    assert after_insert[0] > base_launch[0] and after_insert[1] == "upsert"
    assert revision(singleton_id) == base_singleton

    with Session(producer.engine) as session:
        member = session.get(EventMemberRecord, (launch_id, "a-4"))
        member.is_representative = True
        session.add(member)
        session.commit()
    after_update = revision(launch_id)
    assert after_update[0] > after_insert[0]

    with Session(producer.engine) as session:
        session.exec(
            update(EventMemberRecord)
            .where(EventMemberRecord.article_id == "a-4")
            .values(event_id=singleton_id)
        )
        session.commit()
    after_move_launch = revision(launch_id)
    after_move_singleton = revision(singleton_id)
    assert after_move_launch[0] > after_update[0]
    assert after_move_singleton[0] > base_singleton[0]

    with Session(producer.engine) as session:
        session.exec(delete(EventMemberRecord).where(EventMemberRecord.article_id == "a-4"))
        session.commit()
    assert revision(singleton_id)[0] > after_move_singleton[0]

    before_redirect = (revision(launch_id)[0], revision(singleton_id)[0])
    with Session(producer.engine) as session:
        session.add(EventRedirectRecord(
            old_event_id=singleton_id, new_event_id=launch_id, reason="manual", created_at=_iso(),
        ))
        session.commit()
    assert revision(launch_id)[0] > before_redirect[0]
    assert revision(singleton_id)[0] > before_redirect[1]

    # 事件物理删除:成员 / 重定向级联删除的触发器不会把 tombstone 改回 upsert
    with Session(producer.engine) as session:
        session.delete(session.get(EventRecord, launch_id))
        session.commit()
    assert revision(launch_id)[1] == "tombstone"
    with Session(producer.engine) as session:
        assert session.get(EventRedirectRecord, singleton_id) is None
        assert session.exec(
            select(EventMemberRecord).where(EventMemberRecord.event_id == launch_id)
        ).all() == []
    _manifest, rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "events"), expected_stream="events"
    )
    assert {(row["identity"], row["operation"]) for row in rows} == {
        (str(launch_id), "tombstone"),
        (str(singleton_id), "upsert"),
    }


def test_synced_events_never_enter_the_receiver_export_stream(tmp_path):
    producer = _sink(tmp_path, "producer-no-reexport.db")
    consumer = _sink(tmp_path, "consumer-no-reexport.db")
    launch_id, _singleton_id = _seed_producer(producer)
    for stream in ("sources", "articles", "events"):
        _copy(producer, consumer, stream)
    with Session(consumer.engine) as session:
        state = session.get(ArchiveSyncEntityStateRecord, ("events", str(launch_id)))
        assert state.authority_id == archive_sync_v2.producer_authority_id(producer.engine)
    _manifest, rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(consumer.engine, "events"), expected_stream="events"
    )
    assert rows == []


# ── 迁移 ───────────────────────────────────────────────────────────────────────


def test_events_stream_migration_widens_the_check_and_installs_triggers(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'events-stream-migration.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "ed530f8224f9")
    engine = create_engine(db_url)
    try:
        assert "archive_sync_event_insert" not in _trigger_names(engine)
        with engine.connect() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(text(
                    "INSERT INTO archive_sync_entity_states"
                    "(stream, identity, authority_id, revision, operation, updated_at) "
                    "VALUES ('events', '1', '', 1, 'upsert', '')"
                ))
            conn.rollback()

        command.upgrade(cfg, "heads")
        assert {
            "archive_sync_event_insert",
            "archive_sync_event_delete",
            "archive_sync_event_member_insert",
            "archive_sync_event_member_update",
            "archive_sync_event_redirect_insert",
            "event_member_authority_guard_insert",
            "event_member_authority_guard_update",
        } <= _trigger_names(engine)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO events(id, status, title, title_source, member_count, publisher_count, "
                "first_seen_at, last_member_at, assignment_version, authority_id, created_at, updated_at) "
                "VALUES (7, 'open', 'migrated', '', 0, 0, 'x', 'x', '', '', 'x', 'x')"
            ))
            operation = conn.execute(text(
                "SELECT operation FROM archive_sync_entity_states "
                "WHERE stream = 'events' AND identity = '7'"
            )).scalar()
            assert operation == "upsert"

        command.downgrade(cfg, "ed530f8224f9")
        assert "archive_sync_event_insert" not in _trigger_names(engine)
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT COUNT(*) FROM archive_sync_entity_states WHERE stream = 'events'"
            )).scalar() == 0
    finally:
        engine.dispose()
