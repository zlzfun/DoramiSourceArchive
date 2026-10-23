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
    EventMemberBacklogRecord,
    EventMemberRecord,
    EventRecord,
    EventRedirectRecord,
    SourceConfigRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from services.reader_ai import _body_fingerprint  # noqa: E402
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


def _backlog(engine, authority, article_id):
    with Session(engine) as session:
        return session.get(EventMemberBacklogRecord, (authority, article_id))


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
        # 某流的页刚导出之后要在生产端做的事(快照此时已固定),一次性。
        self.hooks = {}

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
            hook = self.hooks.pop(stream, None)
            if hook is not None:
                hook()
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
    assert events_checkpoint["deferred"] == 0
    assert result["streams"]["articles"]["backlog_replay"] == {
        "installed": 0, "pending": 0, "expired": 0, "dropped": 0, "superseded": 0,
    }
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


def test_members_without_local_article_are_deferred_and_local_articles_never_overwritten(tmp_path):
    producer = _sink(tmp_path, "producer-missing-member.db")
    consumer = _sink(tmp_path, "consumer-missing-member.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    # 文章流拉完之后生产端又多了一篇成员文章(下一轮文章流才会到)→ 进待补账……
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

    raw, applied = _copy(producer, consumer, "events")

    assert applied["inserted"] == 2
    assert applied["skipped"] == 1
    assert applied["deferred"] == 1
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    launch_revision = next(
        int(row["revision"])
        for row in archive_sync_v2.parse_page(raw, expected_stream="events")[1]
        if row["identity"] == str(launch_id)
    )
    with Session(consumer.engine) as session:
        assert session.get(EventMemberRecord, (launch_id, "a-late")) is None
        assert session.get(EventMemberRecord, (singleton_id, "a-local")) is None
        local = session.get(ArticleRecord, "a-local")
        assert local.title == "Private local article"
        assert local.analysis_authority_id == ""
        assert local.source_id == "user_rss_me"
        # 计数列照抄 authority 的缓存值,读取侧按本地成员另算
        assert session.get(EventRecord, launch_id).member_count == 2
    backlog = _backlog(consumer.engine, authority, "a-late")
    assert backlog is not None and backlog.attempts == 0
    assert backlog.event_id == launch_id and backlog.revision == launch_revision
    assert json.loads(backlog.payload_json)["publisher_key"] == "meta"
    assert _backlog(consumer.engine, authority, "a-local") is None
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]
    assert _member_ids(consumer.engine, singleton_id) == ["a-3"]


@pytest.mark.parametrize(
    "source_id, overrides",
    [
        ("user_rss_owner", dict(owner_username="owner")),
        ("rss_team", dict(owner_username="team")),
        ("rss_signed", dict(
            params_json='{"feed_url":"https://example.test/feed.xml","credentialed_private":true}',
        )),
    ],
    ids=["user-rss", "owner-bound", "credentialed"],
)
def test_private_members_never_leave_the_producer(tmp_path, source_id, overrides):
    producer = _sink(tmp_path, "producer-private-member.db")
    launch_id, _singleton_id = _seed_producer(producer)
    with Session(producer.engine) as session:
        session.add(_source(source_id, **overrides))
        session.add(_article("hidden-1", source_id=source_id))
        session.flush()
        session.add(_member(launch_id, "hidden-1", publisher_key="hidden"))
        session.commit()

    _manifest, rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "events"), expected_stream="events"
    )
    launch = next(row for row in rows if row["identity"] == str(launch_id))
    assert [m["article_id"] for m in launch["payload"]["members"]] == ["a-1", "a-2"]
    assert launch["payload"]["event"]["member_count"] == 2
    assert launch["payload"]["event"]["publisher_count"] == 2
    assert "hidden" not in json.dumps(launch["payload"], ensure_ascii=False)


def test_event_metadata_is_rebuilt_from_exportable_members_only(tmp_path):
    producer = _sink(tmp_path, "producer-metadata.db")
    consumer = _sink(tmp_path, "consumer-metadata.db")
    launch_id, _singleton_id = _seed_producer(producer)
    with Session(producer.engine) as session:
        # 私有源成员是代表稿,事件题是带着它信息的模型拟题
        session.add(_source("rss_vendor", owner_username="vendor"))
        session.add(_article("secret-1", source_id="rss_vendor", title="Project Cedar roadmap"))
        session.flush()
        session.add(_member(launch_id, "secret-1", publisher_key="vendor", is_representative=True))
        for member in session.exec(
            select(EventMemberRecord).where(EventMemberRecord.event_id == launch_id)
        ).all():
            if member.article_id != "secret-1":
                member.is_representative = False
                session.add(member)
        launch = session.get(EventRecord, launch_id)
        launch.title = "Project Cedar leaks ahead of launch"
        launch.title_source = "llm"
        launch.representative_article_id = "secret-1"
        launch.member_count = 3
        launch.publisher_count = 3
        session.add(launch)
        # 公共成员 a-2 有指纹有效的译题缓存
        a2 = session.get(ArticleRecord, "a-2")
        a2.extensions_json = json.dumps({
            "translation_zh_title": "甲方发布新品",
            "translation_zh_title_fp": _body_fingerprint(a2.title),
        })
        session.add(a2)
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")

    raw, applied = _copy(producer, consumer, "events")
    _manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="events")
    payload = next(row for row in rows if row["identity"] == str(launch_id))["payload"]
    assert "Cedar" not in json.dumps(payload, ensure_ascii=False)
    assert payload["event"]["title"] == "甲方发布新品"
    assert payload["event"]["title_source"] == "translation"
    assert payload["event"]["representative_article_id"] == "a-1"
    assert payload["event"]["member_count"] == 2 and payload["event"]["publisher_count"] == 2
    assert [(m["article_id"], m["is_representative"]) for m in payload["members"]] == [
        ("a-1", True), ("a-2", False),
    ]
    assert applied["inserted"] == 2
    with Session(consumer.engine) as session:
        synced = session.get(EventRecord, launch_id)
        assert synced.title == "甲方发布新品" and synced.title_source == "translation"
        assert synced.representative_article_id == "a-1"
        assert session.get(EventMemberRecord, (launch_id, "a-1")).is_representative is True


def test_event_without_exportable_members_is_withdrawn_as_tombstone(tmp_path):
    producer = _sink(tmp_path, "producer-all-private.db")
    consumer = _sink(tmp_path, "consumer-all-private.db")
    launch_id, singleton_id = _seed_producer(producer)
    for stream in ("sources", "articles", "events"):
        _copy(producer, consumer, stream)
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]

    # 事件成员的来源整个转为 owner 源 → 一个可导出成员都不剩
    with Session(producer.engine) as session:
        source = session.get(SourceConfigRecord, "rss_platform")
        source.owner_username = "vendor"
        session.add(source)
        launch = session.get(EventRecord, launch_id)
        launch.title = "Confidential roadmap"
        launch.title_source = "llm"
        session.add(launch)
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "events")
    _manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="events")
    by_identity = {row["identity"]: row for row in rows}
    assert by_identity[str(launch_id)]["operation"] == "tombstone"
    assert by_identity[str(singleton_id)]["operation"] == "tombstone"
    assert "Confidential" not in raw

    applied = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="events")
    assert applied["deleted"] == 2
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id) is None
        assert session.get(EventRecord, singleton_id) is None
    assert _member_ids(consumer.engine, launch_id) == []


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


def test_merged_event_is_exported_stripped_and_never_leaks_its_old_title(tmp_path):
    """检视 R2 V4:合并后旧 ID 只剩重定向;成员来源日后转私有,旧 ID 也不会带出私有标题。"""
    producer = _sink(tmp_path, "producer-merged-strip.db")
    consumer = _sink(tmp_path, "consumer-merged-strip.db")
    launch_id, singleton_id = _seed_producer(producer)
    with Session(producer.engine) as session:
        old = session.get(EventRecord, singleton_id)
        old.title = "Project Cedar confidential roadmap"
        old.title_source = "llm"
        session.add(old)
        session.commit()
    for stream in ("sources", "articles", "events"):
        full_raw, _applied = _copy(producer, consumer, stream)
    full_snapshot = archive_sync_v2.parse_page(full_raw, expected_stream="events")[0]["snapshot"]
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, singleton_id).title == "Project Cedar confidential roadmap"

    # 合并:singleton → launch
    with Session(producer.engine) as session:
        session.exec(
            update(EventMemberRecord).where(EventMemberRecord.event_id == singleton_id).values(event_id=launch_id)
        )
        old = session.get(EventRecord, singleton_id)
        old.status = "merged"
        session.add(old)
        session.add(EventRedirectRecord(
            old_event_id=singleton_id, new_event_id=launch_id, reason="manual", created_at=_iso(1),
        ))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "events", since=full_snapshot)
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="events")
    assert "Cedar" not in raw
    stripped = next(row for row in rows if row["identity"] == str(singleton_id))
    assert stripped["operation"] == "upsert"
    assert stripped["payload"]["members"] == []
    assert stripped["payload"]["merged_into"]["new_event_id"] == launch_id
    assert {
        key: stripped["payload"]["event"][key]
        for key in ("status", "title", "title_source", "representative_article_id", "member_count", "publisher_count")
    } == {
        "status": "merged", "title": "", "title_source": "", "representative_article_id": None,
        "member_count": 0, "publisher_count": 0,
    }
    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="events")
    with Session(consumer.engine) as session:
        old = session.get(EventRecord, singleton_id)
        assert old.status == "merged" and old.title == "" and old.representative_article_id is None
        assert session.get(EventRedirectRecord, singleton_id).new_event_id == launch_id
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2", "a-3"]
    assert _member_ids(consumer.engine, singleton_id) == []

    # 原成员来源转私有:新事件撤回;旧 ID 无论全量还是增量导出都不含私有标题
    with Session(producer.engine) as session:
        source = session.get(SourceConfigRecord, "rss_platform")
        source.owner_username = "vendor"
        session.add(source)
        session.commit()
    full_again = archive_sync_v2.export_page(producer.engine, "events")
    assert "Cedar" not in full_again
    incremental = archive_sync_v2.export_page(producer.engine, "events", since=manifest["snapshot"])
    assert "Cedar" not in incremental
    _inc_manifest, inc_rows = archive_sync_v2.parse_page(incremental, expected_stream="events")
    assert {(row["identity"], row["operation"]) for row in inc_rows} == {(str(launch_id), "tombstone")}
    applied = archive_sync_v2.import_page(consumer.engine, incremental, expected_stream="events")
    assert applied["deleted"] == 1
    with Session(consumer.engine) as session:
        assert session.get(EventRecord, launch_id) is None
        assert session.get(EventRecord, singleton_id).title == ""


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


@pytest.mark.parametrize("existing_authority", ["", "third-authority"], ids=["local-event", "third-authority"])
def test_tombstone_colliding_with_a_foreign_event_fails_the_page_closed(tmp_path, existing_authority):
    producer = _sink(tmp_path, "producer-tombstone-collision.db")
    consumer = _sink(tmp_path, "consumer-tombstone-collision.db")
    launch_id, singleton_id = _seed_producer(producer)
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    full_raw, _applied = _copy(producer, consumer, "events")
    full_snapshot = archive_sync_v2.parse_page(full_raw, expected_stream="events")[0]["snapshot"]
    # 接收端把同步来的 launch 换成了一个不归该 authority 的同 ID 事件(本地事件 / 第三 authority)
    with Session(consumer.engine) as session:
        session.delete(session.get(EventRecord, launch_id))
        session.exec(delete(ArchiveSyncEntityStateRecord).where(
            ArchiveSyncEntityStateRecord.stream == "events",
            ArchiveSyncEntityStateRecord.identity == str(launch_id),
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_event(id=launch_id, title="Foreign", authority_id=existing_authority))
        session.commit()

    with Session(producer.engine) as session:
        session.delete(session.get(EventRecord, launch_id))
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "events", since=full_snapshot)
    assert [row["operation"] for row in archive_sync_v2.parse_page(raw, expected_stream="events")[1]] == ["tombstone"]

    with pytest.raises(archive_sync_v2.SyncV2Error, match="belongs to another authority"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream="events")

    producer_authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        foreign = session.get(EventRecord, launch_id)
        assert foreign.title == "Foreign" and foreign.authority_id == existing_authority
        state = session.get(ArchiveSyncEntityStateRecord, ("events", str(launch_id)))
        assert state is None or state.authority_id != producer_authority
        assert session.get(EventRecord, singleton_id).authority_id == producer_authority
    if not existing_authority:
        # 本地事件仍在本节点自己的导出流里
        _manifest, rows = archive_sync_v2.parse_page(
            archive_sync_v2.export_page(consumer.engine, "events"), expected_stream="events"
        )
        assert [row["identity"] for row in rows] == [str(launch_id)]


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


# ── 待补账(成员文章晚到) ──────────────────────────────────────────────────────


def test_member_whose_article_arrives_in_the_next_pull_is_recovered_from_the_backlog(tmp_path):
    producer = _sink(tmp_path, "producer-backlog.db")
    consumer = _sink(tmp_path, "consumer-backlog.db")
    launch_id, _singleton_id = _seed_producer(producer)
    remote = _Remote(producer)

    def refresh_a2():
        # 快照已在 sources 页固定:a-2 此刻更新 → 其 revision 越过快照,本轮 articles 流不含它,
        # 而事件 revision 仍在快照内、导出时成员里带着 a-2。
        with Session(producer.engine) as session:
            article = session.get(ArticleRecord, "a-2")
            article.content = "refreshed body"
            article.archive_updated_at = _iso(0)
            session.add(article)
            session.commit()

    remote.hooks["sources"] = refresh_a2
    first = _pull(consumer, remote, tmp_path)

    assert first["streams"]["events"]["deferred"] == 1
    assert first["streams"]["events"]["skipped"] == 0
    authority = first["authority_id"]
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "a-2") is None
    backlog = _backlog(consumer.engine, authority, "a-2")
    assert backlog is not None and backlog.attempts == 0 and backlog.event_id == launch_id
    assert _member_ids(consumer.engine, launch_id) == ["a-1"]

    second = _pull(consumer, remote, tmp_path, checkpoints=first["streams"])

    assert second["streams"]["articles"]["inserted"] == 1
    assert second["streams"]["events"]["count"] == 0  # 事件本身没变,不会重发
    assert second["streams"]["articles"]["backlog_replay"]["installed"] == 1
    assert _member_ids(consumer.engine, launch_id) == ["a-1", "a-2"]
    assert _backlog(consumer.engine, authority, "a-2") is None
    with Session(consumer.engine) as session:
        member = session.get(EventMemberRecord, (launch_id, "a-2"))
        assert member.publisher_key == "google" and member.assigned_by == "llm"


def test_backlog_replay_expires_stale_rows_and_never_overrides_newer_attributions(tmp_path):
    consumer = _sink(tmp_path, "consumer-backlog-replay.db")
    authority = "producer-x"
    member_payload = dict(
        article_id="", publisher_key="openai", report_at=_iso(2), first_seen_at=_iso(2),
        assigned_by="llm", confidence=0.7, prompt_version="p1", is_representative=False,
        created_at=_iso(2),
    )

    def backlog_row(article_id, event_id, *, revision=5, first_seen=None):
        stamp = first_seen or _iso(1)
        return EventMemberBacklogRecord(
            authority_id=authority, article_id=article_id, event_id=event_id, revision=revision,
            payload_json=json.dumps({**member_payload, "article_id": article_id}),
            first_seen_at=stamp, attempts=0, updated_at=stamp,
        )

    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_source("user_rss_me", owner_username="me"))
        for article_id in ("arrived", "settled", "orphan"):
            session.add(_article(article_id, analysis_authority_id=authority))
        session.add(_article("local-1", source_id="user_rss_me"))
        session.add_all([
            _event(id=41, title="Synced", authority_id=authority),
            _event(id=42, title="Local"),
            _event(id=43, title="Newer", authority_id=authority),
            _event(id=44, title="Merged away", status="merged", authority_id=authority),
        ])
        session.flush()
        # 事件 43 的页行 revision 更新,且已把 settled 落库
        session.add(ArchiveSyncEntityStateRecord(
            stream="events", identity="43", authority_id=authority, revision=50, operation="upsert",
        ))
        session.add(_member(43, "settled"))
        session.add_all([
            backlog_row("arrived", 41),                              # 文章已到 → 落成员
            backlog_row("local-1", 41),                              # 文章在本地却是私有订阅 → 丢弃
            backlog_row("never", 41, first_seen=_iso(24 * 31)),      # 入账超 30 天 → 清理
            backlog_row("waiting", 41, first_seen=_iso(24)),         # 仍缺 → attempts +1
            backlog_row("settled", 41, revision=7),                  # 本地已有更新版本的归属 → 不覆盖
            backlog_row("arrived-local", 42),                        # 指向本地事件 → 丢弃
            backlog_row("orphan", 44),                               # 目标事件已合并 → 丢弃
        ])
        session.commit()

    outcome = archive_sync_v2.replay_event_member_backlog(consumer.engine)

    assert outcome == {"installed": 1, "pending": 1, "expired": 1, "dropped": 3, "superseded": 1}
    assert _member_ids(consumer.engine, 41) == ["arrived"]
    assert _member_ids(consumer.engine, 43) == ["settled"]
    with Session(consumer.engine) as session:
        remaining = session.exec(select(EventMemberBacklogRecord)).all()
        assert [(row.event_id, row.article_id, row.attempts) for row in remaining] == [(41, "waiting", 1)]
        # 事件撤回 → 待补行随 FK 级联删除
        session.delete(session.get(EventRecord, 41))
        session.commit()
        assert session.exec(select(EventMemberBacklogRecord)).all() == []


def test_interleaved_pulls_keep_the_newest_attribution_for_a_backlogged_article(tmp_path):
    """检视 R2 V6:摘出 / 合并 / 文章刷新 / 源配置修改与四轮拉取交错,归属不丢、不倒退。"""
    producer = _sink(tmp_path, "producer-interleaved.db")
    consumer = _sink(tmp_path, "consumer-interleaved.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_source("rss_other"))
        for article_id in ("a-1", "a-2", "a-3"):
            session.add(_article(article_id))
        session.add(_article("a-4", source_id="rss_other"))
        session.flush()
        e1 = _event(title="E1", member_count=2, publisher_count=2)
        e2 = _event(title="E2", member_count=2, publisher_count=2)
        session.add_all([e1, e2])
        session.flush()
        session.add_all([
            _member(e1.id, "a-1"), _member(e1.id, "a-2", publisher_key="google"),
            _member(e2.id, "a-3"), _member(e2.id, "a-4", publisher_key="meta"),
        ])
        session.commit()
        e1_id, e2_id = e1.id, e2.id
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    remote = _Remote(producer)

    def refresh_a3():
        with Session(producer.engine) as session:
            article = session.get(ArticleRecord, "a-3")
            article.content += " +"
            article.archive_updated_at = _iso(0)
            session.add(article)
            session.commit()

    # 第一轮:快照固定后 a-3 刷新 → (E2, a-3) 待补
    remote.hooks["sources"] = refresh_a3
    first = _pull(consumer, remote, tmp_path)
    assert first["streams"]["events"]["deferred"] == 1
    assert _backlog(consumer.engine, authority, "a-3").event_id == e2_id

    # 两轮之间管理员把 a-3 从 E2 摘出并入 E1(E1、E2 都被回标,仍在下一轮快照内)
    with Session(producer.engine) as session:
        session.exec(
            update(EventMemberRecord).where(EventMemberRecord.article_id == "a-3").values(event_id=e1_id)
        )
        session.commit()

    # 第二轮:快照固定后 a-3 再次刷新、E2 剩余成员的源配置被改 → a-3 仍缺、E2 的新状态越过快照;只有 E1 到达
    def refresh_a3_and_touch_e2_source():
        refresh_a3()
        with Session(producer.engine) as session:
            source = session.get(SourceConfigRecord, "rss_other")
            source.params_json = '{"feed_url":"https://example.test/rss_other.xml","note":"touched"}'
            session.add(source)
            session.commit()

    remote.hooks["sources"] = refresh_a3_and_touch_e2_source
    second = _pull(consumer, remote, tmp_path, checkpoints=first["streams"])
    assert second["streams"]["events"]["count"] == 1
    assert second["streams"]["events"]["deferred"] == 1
    with Session(consumer.engine) as session:
        rows = session.exec(select(EventMemberBacklogRecord)).all()
        assert [(row.event_id, row.article_id) for row in rows] == [(e1_id, "a-3")]
    assert _member_ids(consumer.engine, e2_id) == ["a-4"]  # a-3 从未落库到 E2(第一轮就进了待补账)

    # 第三轮:a-3 到达 → 重放只落 E1;随后 E2 的新成员集(无 a-3)到达,不动 a-3 的归属
    third = _pull(consumer, remote, tmp_path, checkpoints=second["streams"])
    assert third["streams"]["articles"]["backlog_replay"]["installed"] == 1
    assert third["streams"]["events"]["count"] == 1
    assert _member_ids(consumer.engine, e1_id) == ["a-1", "a-2", "a-3"]
    assert _member_ids(consumer.engine, e2_id) == ["a-4"]

    # 第四轮:无变化,归属稳定、待补账为空
    fourth = _pull(consumer, remote, tmp_path, checkpoints=third["streams"])
    assert fourth["streams"]["events"]["count"] == 0
    assert _member_ids(consumer.engine, e1_id) == ["a-1", "a-2", "a-3"]
    with Session(consumer.engine) as session:
        assert session.exec(select(EventMemberBacklogRecord)).all() == []


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


def test_backlog_versioning_migration_keeps_the_newest_whole_row_per_article(tmp_path, capfd):
    """检视 R3:旧表里同一文章两条冲突待补(旧归属 E1 / revision 10,新归属 E2 / revision 20,E1 的 ID
    更小)→ 迁移保留 updated_at 最新的整行并取该事件已接收的 revision;文章到达重放进 E2,随后 E1 的
    移除更新到达,文章仍在 E2;无法归属的行记日志而不静默丢弃。"""
    db_url = f"sqlite:///{tmp_path / 'backlog-versioning.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "7c3d9e1f5a24")
    authority = "producer-legacy"
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for event_id, authority_id in ((1, authority), (2, authority), (3, "")):
                conn.execute(text(
                    "INSERT INTO events(id, status, title, title_source, member_count, publisher_count, "
                    "first_seen_at, last_member_at, assignment_version, authority_id, created_at, updated_at) "
                    "VALUES (:id, 'open', :title, '', 0, 0, :t, :t, '', :auth, :t, :t)"
                ), {"id": event_id, "title": f"E{event_id}", "t": _iso(5), "auth": authority_id})
            for identity, revision in (("1", 10), ("2", 20)):
                conn.execute(text(
                    "INSERT INTO archive_sync_entity_states(stream, identity, authority_id, revision, operation, updated_at) "
                    "VALUES ('events', :identity, :auth, :revision, 'upsert', :t)"
                ), {"identity": identity, "auth": authority, "revision": revision, "t": _iso(5)})
            member = dict(
                article_id="x", publisher_key="", report_at=_iso(3), first_seen_at=_iso(3),
                assigned_by="llm", confidence=0.6, prompt_version="p1", is_representative=False,
                created_at=_iso(3),
            )
            for event_id, publisher, first_seen, attempts, updated in (
                (1, "old-owner", _iso(4), 3, _iso(4)),   # 旧归属:ID 小、updated_at 早
                (2, "new-owner", _iso(2), 1, _iso(2)),   # 新归属:updated_at 晚 → 整行保留
            ):
                conn.execute(text(
                    "INSERT INTO event_member_backlog(event_id, article_id, payload_json, first_seen_at, attempts, updated_at) "
                    "VALUES (:event_id, 'x', :payload, :first_seen, :attempts, :updated)"
                ), {
                    "event_id": event_id, "payload": json.dumps({**member, "publisher_key": publisher}),
                    "first_seen": first_seen, "attempts": attempts, "updated": updated,
                })
            conn.execute(text(
                "INSERT INTO event_member_backlog(event_id, article_id, payload_json, first_seen_at, attempts, updated_at) "
                "VALUES (3, 'y', '{}', :t, 0, :t)"
            ), {"t": _iso(1)})  # 指向本地事件,无法归属 → 记日志

        capfd.readouterr()
        command.upgrade(cfg, "heads")
        # alembic 的日志经 fileConfig 直写 stderr(不走 caplog),按文件描述符截获
        logged = capfd.readouterr().err
        assert "event=1 article=x" in logged and "superseded by event 2" in logged
        assert "event=3 article=y" in logged and "node-local" in logged

        with Session(engine) as session:
            rows = session.exec(select(EventMemberBacklogRecord)).all()
            assert [(r.authority_id, r.article_id, r.event_id, r.revision, r.attempts) for r in rows] == [
                (authority, "x", 2, 20, 1)
            ]
            kept = rows[0]
            assert json.loads(kept.payload_json)["publisher_key"] == "new-owner"
            assert kept.first_seen_at == kept.updated_at  # 同一旧行的 first_seen / updated

        # 文章到达 → 重放落入新归属 E2
        with Session(engine) as session:
            session.add(_source())
            session.add(_article("x", analysis_authority_id=authority))
            session.commit()
        outcome = archive_sync_v2.replay_event_member_backlog(engine)
        assert outcome["installed"] == 1
        assert _member_ids(engine, 2) == ["x"]

        # 旧事件 E1 的「移除更新」到达(revision 30,成员集为空)→ x 仍在 E2
        payload = {
            "id": 1,
            "event": {
                "id": 1, "status": "open", "title": "E1", "title_source": "", "representative_article_id": None,
                "member_count": 0, "publisher_count": 0, "first_seen_at": _iso(5), "last_member_at": _iso(5),
                "story_id": None, "follows_event_id": None, "assignment_version": "", "created_at": _iso(5),
                "updated_at": _iso(0),
            },
            "members": [], "redirects_from": [], "merged_into": None,
        }
        row = archive_sync_v2._line("events", payload, revision="30", identity="1")
        manifest = archive_sync_v2._manifest("events", authority, "30", "", [row], complete=True)
        applied = archive_sync_v2.import_page(
            engine, archive_sync_v2.encode_page(manifest, [row]), expected_stream="events"
        )
        assert applied["updated"] == 1
        assert _member_ids(engine, 2) == ["x"]
        assert _member_ids(engine, 1) == []
        with Session(engine) as session:
            assert session.exec(select(EventMemberBacklogRecord)).all() == []
    finally:
        engine.dispose()


def test_backlog_versioning_migration_prefers_applied_revision_over_flattened_timestamps(tmp_path, capfd):
    """检视 R4:最新归属 E1 的 ID 较小,且迁移前旧版重放已把两行 updated_at 抹成同一轮时间 →
    迁移按已接收 revision 选行(E1 / 20 胜 E2 / 10);文章到达进 E1,E2 的移除更新后仍在 E1。
    两行都查不到 revision 的冲突按时间 / ID 兜底并 WARNING「依据不足」。"""
    db_url = f"sqlite:///{tmp_path / 'backlog-versioning-flat.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "7c3d9e1f5a24")
    authority = "producer-legacy"
    flattened = _iso(1)  # 旧版重放在同一轮把所有待补行 updated_at 写成同一时间
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for event_id in (1, 2, 4, 5):
                conn.execute(text(
                    "INSERT INTO events(id, status, title, title_source, member_count, publisher_count, "
                    "first_seen_at, last_member_at, assignment_version, authority_id, created_at, updated_at) "
                    "VALUES (:id, 'open', :title, '', 0, 0, :t, :t, '', :auth, :t, :t)"
                ), {"id": event_id, "title": f"E{event_id}", "t": _iso(5), "auth": authority})
            for identity, revision in (("1", 20), ("2", 10)):  # E1 更新,但 ID 更小
                conn.execute(text(
                    "INSERT INTO archive_sync_entity_states(stream, identity, authority_id, revision, operation, updated_at) "
                    "VALUES ('events', :identity, :auth, :revision, 'upsert', :t)"
                ), {"identity": identity, "auth": authority, "revision": revision, "t": _iso(5)})
            member = dict(
                article_id="x", publisher_key="", report_at=_iso(3), first_seen_at=_iso(3),
                assigned_by="llm", confidence=0.6, prompt_version="p1", is_representative=False,
                created_at=_iso(3),
            )
            for event_id, article_id, publisher in ((1, "x", "new-owner"), (2, "x", "old-owner"), (4, "z", "four"), (5, "z", "five")):
                conn.execute(text(
                    "INSERT INTO event_member_backlog(event_id, article_id, payload_json, first_seen_at, attempts, updated_at) "
                    "VALUES (:event_id, :article_id, :payload, :first_seen, 2, :updated)"
                ), {
                    "event_id": event_id, "article_id": article_id,
                    "payload": json.dumps({**member, "article_id": article_id, "publisher_key": publisher}),
                    "first_seen": _iso(4), "updated": flattened,
                })

        capfd.readouterr()
        command.upgrade(cfg, "heads")
        logged = capfd.readouterr().err
        assert "event=2 article=x" in logged and "superseded by event 1" in logged
        assert "insufficient basis for article=z" in logged and "kept event=5" in logged

        with Session(engine) as session:
            rows = session.exec(select(EventMemberBacklogRecord).order_by(EventMemberBacklogRecord.article_id)).all()
            assert [(r.article_id, r.event_id, r.revision) for r in rows] == [("x", 1, 20), ("z", 5, 0)]
            assert json.loads(rows[0].payload_json)["publisher_key"] == "new-owner"

        with Session(engine) as session:
            session.add(_source())
            session.add(_article("x", analysis_authority_id=authority))
            session.commit()
        assert archive_sync_v2.replay_event_member_backlog(engine)["installed"] == 1
        assert _member_ids(engine, 1) == ["x"]

        # E2 的移除更新(revision 30、空成员集)到达 → x 仍在 E1
        payload = {
            "id": 2,
            "event": {
                "id": 2, "status": "open", "title": "E2", "title_source": "", "representative_article_id": None,
                "member_count": 0, "publisher_count": 0, "first_seen_at": _iso(5), "last_member_at": _iso(5),
                "story_id": None, "follows_event_id": None, "assignment_version": "", "created_at": _iso(5),
                "updated_at": _iso(0),
            },
            "members": [], "redirects_from": [], "merged_into": None,
        }
        row = archive_sync_v2._line("events", payload, revision="30", identity="2")
        manifest = archive_sync_v2._manifest("events", authority, "30", "", [row], complete=True)
        archive_sync_v2.import_page(engine, archive_sync_v2.encode_page(manifest, [row]), expected_stream="events")
        assert _member_ids(engine, 1) == ["x"]
        assert _member_ids(engine, 2) == []
    finally:
        engine.dispose()


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
