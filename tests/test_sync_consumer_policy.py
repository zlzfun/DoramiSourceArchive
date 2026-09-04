"""Receiver quiesce fence for the first Archive Sync v2 authority snapshot."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from models.content import RssArticleContent  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArchiveSyncEntityStateRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    FetchRunRecord,
    MediaAssetRecord,
    SourceConfigRecord,
    SourceStateRecord,
)
from services import remote_sync, sync_consumer_policy  # noqa: E402
from services.article_analysis import (  # noqa: E402
    claim_analysis_tasks,
    process_claimed_analysis,
    queue_article_analysis,
    scan_analysis_backfill,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc)
NOW_ISO = NOW.isoformat()


def _v2_probe(authority_id="producer-a"):
    return {
        "schema_version": remote_sync.archive_sync_v2.SCHEMA_VERSION,
        "capabilities": list(remote_sync.archive_sync_v2.CAPABILITIES),
        "authority_id": authority_id,
        "taxonomy_ready": True,
    }


def _sink(tmp_path, name="consumer.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _source(source_id: str, *, owner: str = "", credentialed: bool = False):
    return SourceConfigRecord(
        source_id=source_id,
        name=source_id,
        source_type="rss",
        url=(
            "https://example.test/private/feed?token=secret"
            if credentialed
            else "https://example.test/feed"
        ),
        fetcher_id="generic_rss",
        owner_username=owner,
        ai_analysis_enabled=True,
        is_active=True,
        params_json=json.dumps({"credentialed_private": credentialed}),
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _article(article_id: str, source_id: str):
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type="rss_article",
        source_id=source_id,
        source_url=f"https://example.test/{article_id}",
        publish_date=NOW_ISO,
        fetched_date=NOW_ISO,
        has_content=True,
        content="body",
        extensions_json="{}",
    )


def _analysis_payload():
    return {
        "quality_score": 8.0,
        "dimension_scores": {},
        "score_reason": "useful",
        "summary": "long summary",
        "content_genre": "analysis",
        "content_features": [],
        "entities": [],
        "tag_assignments": [],
        "tag_candidates": [],
    }


def test_consumer_mode_allows_owned_legacy_custom_and_signed_collection_only(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([
            _source("platform"),
            _source("legacy_custom", owner="alice"),
            _source("user_rss_signed", owner="alice", credentialed=True),
        ])
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")

        assert not sync_consumer_policy.local_source_operation_allowed(
            session, "platform", operation="collection"
        )
        assert sync_consumer_policy.local_source_operation_allowed(
            session, "legacy_custom", operation="collection"
        )
        assert sync_consumer_policy.local_source_operation_allowed(
            session, "legacy_custom", operation="analysis"
        )
        assert sync_consumer_policy.local_source_operation_allowed(
            session, "user_rss_signed", operation="collection"
        )
        assert not sync_consumer_policy.local_source_operation_allowed(
            session, "user_rss_signed", operation="analysis"
        )


def test_manual_v2_launch_activates_fence_before_job_is_queued(tmp_path, monkeypatch):
    from api.routers import remote_sync as router

    sink = _sink(tmp_path)
    seen = {}

    def fake_launch(_engine, _job_type, _work, **_kwargs):
        with Session(sink.engine) as session:
            seen["active_at_launch"] = sync_consumer_policy.v2_consumer_mode_active(session)
        return object()

    monkeypatch.setattr(router.jobs, "launch", fake_launch)
    with pytest.raises(remote_sync.RemoteSyncError, match="必须先验证"):
        router.launch_remote_sync_job(
            sink.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v2",
        )
    with Session(sink.engine) as session:
        assert not sync_consumer_policy.v2_consumer_mode_active(session)

    router.launch_remote_sync_job(
        sink.engine,
        base_url="http://producer.test",
        username="admin",
        password="secret",
        protocol="v2",
        v2_probe=_v2_probe(),
    )
    assert seen == {"active_at_launch": True}


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        ({"schema_version": remote_sync.archive_sync_v2.SCHEMA_VERSION,
          "capabilities": [], "authority_id": "producer-a"}, "capability"),
        ({"schema_version": "archive-sync-v2",
          "capabilities": list(remote_sync.archive_sync_v2.CAPABILITIES),
          "authority_id": "producer-a"}, "schema_version"),
        ({"schema_version": remote_sync.archive_sync_v2.SCHEMA_VERSION,
          "capabilities": list(remote_sync.archive_sync_v2.CAPABILITIES),
          "authority_id": ""}, "authority_id"),
        ({"schema_version": remote_sync.archive_sync_v2.SCHEMA_VERSION,
          "capabilities": list(remote_sync.archive_sync_v2.CAPABILITIES),
          "authority_id": "producer-a", "taxonomy_ready": False}, "Taxonomy"),
    ],
)
def test_manual_v2_launch_requires_complete_transaction_revision_probe(
    tmp_path, probe, message
):
    from api.routers import remote_sync as router

    sink = _sink(tmp_path, f"invalid-probe-{message}.db")
    with pytest.raises(remote_sync.RemoteSyncError, match=message):
        router.launch_remote_sync_job(
            sink.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v2",
            v2_probe=probe,
        )
    with Session(sink.engine) as session:
        assert not sync_consumer_policy.v2_consumer_mode_active(session)


def test_v2_launch_requires_local_media_before_rebase(tmp_path, monkeypatch):
    import api.app as app_module
    from api.routers import remote_sync as router

    sink = _sink(tmp_path, "media-disabled-preflight.db")
    with Session(sink.engine) as session:
        session.add(_source("remote-public"))
        session.add(_article("remote-article", "remote-public"))
        session.add(AppSettingRecord(
            key=remote_sync.REMOTE_SYNC_STATE_KEY,
            value=json.dumps({"targets": {"http://producer.test": {
                "v2_streams": {"articles": {"snapshot": "2026-01-01T00:00:00"}}
            }}}),
        ))
        session.commit()
    monkeypatch.setattr(app_module, "media_store", None)

    with pytest.raises(remote_sync.RemoteSyncError, match="media store"):
        router.launch_remote_sync_job(
            sink.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v2",
            v2_probe=_v2_probe(),
        )
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "remote-article") is not None
        assert session.get(AppSettingRecord, "remote_sync:v2_consumer_mode") is None
        assert session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY) is not None


def test_transaction_revision_prepare_rebases_legacy_timestamp_checkpoint(tmp_path):
    sink = _sink(tmp_path, "transaction-revision-rebase.db")
    base_url = "http://producer.test"
    authority_id = "producer-a"
    media_hash = "a" * 64
    with Session(sink.engine) as session:
        remote_source = _source("remote-public")
        remote_source.collection_authority_id = authority_id
        legacy_public_source = _source("legacy-public")
        local_source = _source("local-custom", owner="alice")
        session.add_all([
            remote_source,
            legacy_public_source,
            local_source,
            _article("remote-article", "remote-public"),
            _article("local-article", "local-custom"),
        ])
        session.flush()
        remote_article = session.get(ArticleRecord, "remote-article")
        remote_article.analysis_authority_id = authority_id
        remote_article.read_count = 7
        session.add(remote_article)
        manual_tag = CmsTagRecord(
            code="manual-preserved", kind="topic", name_en="Manual Preserved",
            normalized_name="manual preserved", status="active",
            created_at=NOW_ISO, updated_at=NOW_ISO,
        )
        session.add(manual_tag)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id="remote-article", tag_id=int(manual_tag.id), tag_kind="topic",
            is_primary=True, relevance=1, assignment_source="manual",
            prompt_version="", taxonomy_version=0,
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.add_all([
            ArticleAnalysisRecord(
                article_id="remote-article",
                status="succeeded",
                tagging_status="succeeded",
                content_hash="remote-hash",
                authority_id=authority_id,
                authority_revision="2026-09-03T00:00:00+00:00",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            ArticleAnalysisRecord(
                article_id="local-article",
                status="succeeded",
                tagging_status="succeeded",
                content_hash="local-hash",
                authority_id="",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            SourceStateRecord(
                source_id="remote-public",
                fetcher_id="generic_rss",
                status="healthy",
                authority_id=authority_id,
                authority_revision="2026-09-03T00:00:00+00:00",
                updated_at=NOW_ISO,
            ),
            SourceStateRecord(
                source_id="legacy-public",
                fetcher_id="generic_rss",
                status="healthy",
                authority_id="",
                updated_at=NOW_ISO,
            ),
            SourceStateRecord(
                source_id="local-custom",
                fetcher_id="generic_rss",
                status="healthy",
                authority_id="",
                updated_at=NOW_ISO,
            ),
            MediaAssetRecord(
                url_hash=media_hash,
                url="https://example.test/remote.png",
                status="cached",
                content_hash="b" * 64,
                mime="image/png",
                ext=".png",
                size_bytes=4,
                sync_authority_id=authority_id,
                sync_authority_revision="2026-09-03T00:00:00+00:00",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ),
            ArchiveSyncEntityStateRecord(
                stream="articles",
                identity="remote-article",
                authority_id=authority_id,
                revision=7,
                operation="upsert",
                updated_at=NOW_ISO,
            ),
            AppSettingRecord(
                key=remote_sync.REMOTE_SYNC_STATE_KEY,
                value=json.dumps({
                    "targets": {
                        base_url: {
                            "username": "legacy-admin",
                            "v2_streams": {
                                "articles": {
                                    "authority_id": authority_id,
                                    "snapshot": "2026-09-03T00:00:00+00:00",
                                    "cursor": "legacy-cursor",
                                }
                            },
                        },
                        "http://other.test": {"v2_streams": {"sources": {"snapshot": "9"}}},
                    }
                }),
            ),
        ])
        session.commit()

        # A producer identity mismatch in the legacy checkpoint must fail
        # before deactivating or deleting any authority-owned rows.
        with pytest.raises(remote_sync.RemoteSyncError, match="旧 checkpoint"):
            remote_sync.prepare_transaction_revision_consumer(
                session,
                base_url=base_url,
                username="admin",
                authority_id="producer-b",
                schema_version=remote_sync.archive_sync_v2.SCHEMA_VERSION,
                prepared_at="2026-09-04T00:30:00+00:00",
            )
        session.rollback()
        assert session.get(SourceConfigRecord, "remote-public").is_active is True
        assert session.get(ArticleRecord, "remote-article") is not None

        assert remote_sync.prepare_transaction_revision_consumer(
            session,
            base_url=base_url,
            username="admin",
            authority_id=authority_id,
            schema_version=remote_sync.archive_sync_v2.SCHEMA_VERSION,
            prepared_at=NOW_ISO,
        ) is True
        session.commit()

        assert session.get(SourceConfigRecord, "remote-public").is_active is False
        assert session.get(SourceConfigRecord, "legacy-public").is_active is False
        assert session.get(SourceConfigRecord, "local-custom").is_active is True
        assert session.get(ArticleRecord, "remote-article").read_count == 7
        assert session.get(ArticleAnalysisRecord, "remote-article") is not None
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "remote-article",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None
        assert session.get(SourceStateRecord, "remote-public") is None
        assert session.get(SourceStateRecord, "legacy-public") is None
        assert session.get(ArchiveSyncEntityStateRecord, ("articles", "remote-article")) is None
        assert session.get(MediaAssetRecord, media_hash) is not None
        assert session.get(ArticleRecord, "local-article") is not None
        assert session.get(ArticleAnalysisRecord, "local-article") is not None
        assert session.get(SourceStateRecord, "local-custom") is not None

        state = json.loads(session.get(
            AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY
        ).value)
        target = state["targets"][base_url]
        assert target["username"] == "admin"
        assert target["v2_schema_version"] == remote_sync.archive_sync_v2.SCHEMA_VERSION
        assert target["v2_authority_id"] == authority_id
        assert target["v2_rebased_at"] == NOW_ISO
        assert target["v2_streams"] == {}
        assert "http://other.test" in state["targets"]

        # Preparing the same producer epoch is idempotent and keeps the empty
        # transaction-revision checkpoint plus retained media untouched.
        assert remote_sync.prepare_transaction_revision_consumer(
            session,
            base_url=base_url,
            username="admin",
            authority_id=authority_id,
            schema_version=remote_sync.archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-09-04T01:00:00+00:00",
        ) is False
        session.commit()
        target = json.loads(session.get(
            AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY
        ).value)["targets"][base_url]
        assert target["v2_rebased_at"] == NOW_ISO
        assert target["v2_streams"] == {}
        assert session.get(MediaAssetRecord, media_hash) is not None

        with pytest.raises(remote_sync.RemoteSyncError, match="authority_id 已变化"):
            remote_sync.prepare_transaction_revision_consumer(
                session,
                base_url=base_url,
                username="admin",
                authority_id="producer-b",
                schema_version=remote_sync.archive_sync_v2.SCHEMA_VERSION,
                prepared_at="2026-09-04T02:00:00+00:00",
            )
        session.rollback()
        target = json.loads(session.get(
            AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY
        ).value)["targets"][base_url]
        assert target["v2_authority_id"] == authority_id


def test_legacy_filtered_schedule_is_disabled_without_mutating_raw_payload(tmp_path):
    sink = _sink(tmp_path)
    raw = {
        "enabled": True,
        "base_url": "http://producer.test",
        "username": "admin",
        "password": "secret",
        "source_ids": ["platform"],
    }
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(
            key=remote_sync.REMOTE_SYNC_SCHEDULE_KEY,
            value=json.dumps(raw),
        ))
        session.commit()

    view = remote_sync.load_schedule(sink.engine)
    assert view["migration_required"] is True
    assert view["enabled"] is False
    assert view["protocol"] == ""
    with Session(sink.engine) as session:
        stored = session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_SCHEDULE_KEY)
        assert json.loads(stored.value) == raw

    with pytest.raises(remote_sync.RemoteSyncError, match="显式选择"):
        remote_sync.save_schedule(
            sink.engine,
            {"enabled": False},
            updated_at=NOW_ISO,
        )

    saved = remote_sync.save_schedule(
        sink.engine,
        {"enabled": True, "protocol": "v1"},
        updated_at=NOW_ISO,
    )
    assert saved["enabled"] is True
    assert saved["protocol"] == "v1"
    assert saved["migration_required"] is False
    with Session(sink.engine) as session:
        assert not sync_consumer_policy.v2_consumer_mode_active(session)


def test_scheduler_startup_helper_activates_only_explicit_enabled_v2(tmp_path):
    legacy = _sink(tmp_path, "legacy.db")
    with Session(legacy.engine) as session:
        session.add(AppSettingRecord(
            key=remote_sync.REMOTE_SYNC_SCHEDULE_KEY,
            value=json.dumps({"enabled": True, "source_ids": ["platform"]}),
        ))
        session.commit()
    assert not remote_sync.activate_consumer_for_enabled_v2_schedule(
        legacy.engine, reason="startup"
    )

    legacy_full = _sink(tmp_path, "legacy-full.db")
    with Session(legacy_full.engine) as session:
        session.add(AppSettingRecord(
            key=remote_sync.REMOTE_SYNC_SCHEDULE_KEY,
            value=json.dumps({"enabled": True, "source_ids": []}),
        ))
        session.commit()
    legacy_full_view = remote_sync.load_schedule(legacy_full.engine)
    assert legacy_full_view["enabled"] is True
    assert legacy_full_view["protocol"] == "v2"
    assert legacy_full_view["migration_required"] is False
    assert remote_sync.activate_consumer_for_enabled_v2_schedule(
        legacy_full.engine, reason="startup"
    )

    explicit_v1 = _sink(tmp_path, "v1.db")
    remote_sync.save_schedule(
        explicit_v1.engine,
        {"enabled": True, "protocol": "v1"},
        updated_at=NOW_ISO,
    )
    assert not remote_sync.activate_consumer_for_enabled_v2_schedule(
        explicit_v1.engine, reason="startup"
    )

    explicit_v2 = _sink(tmp_path, "v2.db")
    remote_sync.save_schedule(
        explicit_v2.engine,
        {"enabled": True, "protocol": "v2"},
        updated_at=NOW_ISO,
    )
    with Session(explicit_v2.engine) as session:
        # save_schedule commits the marker atomically with the enabled v2 intent.
        assert sync_consumer_policy.v2_consumer_mode_active(session)
    assert not remote_sync.activate_consumer_for_enabled_v2_schedule(
        explicit_v2.engine, reason="startup"
    )


def test_consumer_or_authority_cannot_be_downgraded_to_enabled_v1(tmp_path):
    from api.routers import remote_sync as router

    sink = _sink(tmp_path)
    remote_sync.save_schedule(
        sink.engine,
        {"enabled": True, "protocol": "v2"},
        updated_at=NOW_ISO,
    )
    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级为 v1"):
        remote_sync.save_schedule(
            sink.engine,
            {"enabled": True, "protocol": "v1"},
            updated_at=NOW_ISO,
        )
    assert remote_sync.load_schedule(sink.engine)["protocol"] == "v2"

    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级启动 v1"):
        router.launch_remote_sync_job(
            sink.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v1",
        )

    # Even an old raw v1 intent encountered after manual v2 activation is
    # exposed as effectively disabled rather than registered by the scheduler.
    with Session(sink.engine) as session:
        schedule = session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_SCHEDULE_KEY)
        schedule.value = json.dumps({"enabled": True, "protocol": "v1"})
        session.add(schedule)
        session.commit()
    view = remote_sync.load_schedule(sink.engine)
    assert view["enabled"] is False
    assert view["protocol_downgrade_blocked"] is True

    # Per-row authority is independently sufficient: losing the rollout marker
    # must not silently reopen the legacy v1 writer.
    authority_only = _sink(tmp_path, "authority-only.db")
    with Session(authority_only.engine) as session:
        session.add(_source("remote"))
        session.commit()
        remote_source = session.get(SourceConfigRecord, "remote")
        remote_source.collection_authority_id = "producer-a"
        session.add(remote_source)
        session.commit()
        assert not sync_consumer_policy.v2_consumer_mode_active(session)
        assert sync_consumer_policy.v2_receiver_state_present(session)
    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级为 v1"):
        remote_sync.save_schedule(
            authority_only.engine,
            {"enabled": True, "protocol": "v1"},
            updated_at=NOW_ISO,
        )
    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级启动 v1"):
        router.launch_remote_sync_job(
            authority_only.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v1",
        )

    # A stored v1 intent is likewise safe-disabled when authority exists.
    with Session(authority_only.engine) as session:
        session.add(
            AppSettingRecord(
                key=remote_sync.REMOTE_SYNC_SCHEDULE_KEY,
                value=json.dumps({"enabled": True, "protocol": "v1"}),
            )
        )
        session.commit()
    authority_view = remote_sync.load_schedule(authority_only.engine)
    assert authority_view["enabled"] is False
    assert authority_view["protocol_downgrade_blocked"] is True


@pytest.mark.parametrize("authority_kind", ["analysis", "taxonomy"])
def test_analysis_or_taxonomy_authority_alone_blocks_v1(tmp_path, authority_kind):
    sink = _sink(tmp_path, f"{authority_kind}-authority-only.db")
    with Session(sink.engine) as session:
        if authority_kind == "analysis":
            session.add(_article("remote-analysis", "platform"))
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id="remote-analysis",
                status="succeeded",
                tagging_status="succeeded",
                content_hash="hash",
                authority_id="producer-a",
                authority_revision="rev-1",
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ))
        else:
            session.add(AppSettingRecord(key="taxonomy:authority_id", value="producer-a"))
        session.commit()
        assert sync_consumer_policy.v2_receiver_state_present(session)

    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级为 v1"):
        remote_sync.save_schedule(
            sink.engine,
            {"enabled": True, "protocol": "v1"},
            updated_at=NOW_ISO,
        )


def test_queue_scan_and_claim_quiesce_public_but_keep_custom_rss(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([
            _source("platform"),
            _source("legacy_custom", owner="alice"),
            _article("public", "platform"),
            _article("custom", "legacy_custom"),
        ])
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")
        stats = scan_analysis_backfill(session, now=NOW)
        assert stats.created == 1
        assert session.get(ArticleAnalysisRecord, "public") is None
        assert session.get(ArticleAnalysisRecord, "custom").status == "pending"
        tasks = claim_analysis_tasks(session, worker_id="worker", now=NOW)
        assert [task.article_id for task in tasks] == ["custom"]


def test_consumer_mode_disables_local_taxonomy_auto_governance(tmp_path, monkeypatch):
    from services import taxonomy

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")
        monkeypatch.setattr(
            taxonomy,
            "auto_activation_enabled",
            lambda _session: (_ for _ in ()).throw(AssertionError("must not evaluate")),
        )
        assert taxonomy.run_auto_activation_cycle(session) == []


def test_replica_deployment_disables_local_taxonomy_auto_governance(tmp_path, monkeypatch):
    from services import taxonomy

    sink = _sink(tmp_path, "taxonomy-replica.db")
    monkeypatch.setattr(
        taxonomy,
        "settings",
        SimpleNamespace(taxonomy=SimpleNamespace(mode="replica")),
    )
    with Session(sink.engine) as session:
        monkeypatch.setattr(
            taxonomy,
            "auto_activation_enabled",
            lambda _session: (_ for _ in ()).throw(AssertionError("must not evaluate")),
        )
        assert taxonomy.run_auto_activation_cycle(session) == []


def test_claim_revokes_public_task_queued_before_consumer_activation(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([
            _source("platform"),
            _source("legacy_custom", owner="alice"),
            _article("public", "platform"),
            _article("custom", "legacy_custom"),
        ])
        session.commit()
        assert queue_article_analysis(session, "public", now=NOW) == "created"
        assert queue_article_analysis(session, "custom", now=NOW) == "created"
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")

        tasks = claim_analysis_tasks(session, worker_id="worker", now=NOW)
        assert [task.article_id for task in tasks] == ["custom"]
        public = session.get(ArticleAnalysisRecord, "public")
        assert public.status == "skipped"
        assert public.last_error == "v2_consumer_quiesced"


def test_claim_keyset_reaches_custom_task_past_failed_public_page(tmp_path):
    sink = _sink(tmp_path, "claim-keyset.db")
    with Session(sink.engine) as session:
        session.add_all([_source("platform"), _source("custom", owner="alice")])
        articles = []
        for index in range(65):
            article = _article(f"zz-public-{index:03d}", "platform")
            article.fetched_date = ""
            articles.append(article)
        custom = _article("aa-custom", "custom")
        custom.fetched_date = ""
        articles.append(custom)
        session.add_all(articles)
        session.flush()
        for index in range(65):
            session.add(ArticleAnalysisRecord(
                article_id=f"zz-public-{index:03d}",
                status="failed",
                tagging_status="pending",
                content_hash="stale",
                next_attempt_at=NOW_ISO,
                created_at=NOW_ISO,
                updated_at=NOW_ISO,
            ))
        session.add(ArticleAnalysisRecord(
            article_id="aa-custom",
            status="pending",
            tagging_status="pending",
            content_hash="current",
            created_at=NOW_ISO,
            updated_at=NOW_ISO,
        ))
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")

        tasks = claim_analysis_tasks(
            session,
            worker_id="worker",
            limit=1,
            now=NOW,
        )

        assert [task.article_id for task in tasks] == ["aa-custom"]
        assert session.get(ArticleAnalysisRecord, "zz-public-000").status == "failed"


def test_activation_during_llm_discards_public_result_and_closes_lease(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([_source("platform"), _article("public", "platform")])
        session.commit()
        assert queue_article_analysis(session, "public", now=NOW) == "created"
        session.commit()
        [task] = claim_analysis_tasks(session, worker_id="worker", now=NOW)

    async def activate_while_running(*_args):
        with Session(sink.engine) as session:
            sync_consumer_policy.activate_v2_consumer_mode(session, reason="manual_v2")
        return _analysis_payload()

    result = asyncio.run(process_claimed_analysis(
        sink.engine,
        task,
        llm_config=LLMConfig(
            base_url="https://maas.invalid/v1",
            api_key="test",
            model="test",
        ),
        analyzer=activate_while_running,
        now_fn=lambda: NOW,
    ))
    assert result.status == "superseded"
    with Session(sink.engine) as session:
        record = session.get(ArticleAnalysisRecord, "public")
        assert record.status == "skipped"
        assert record.quality_score is None
        assert record.lease_owner is None


def test_storage_commit_fence_blocks_public_but_not_owned_custom(tmp_path):
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([_source("platform"), _source("legacy_custom", owner="alice")])
        session.commit()
        sync_consumer_policy.activate_v2_consumer_mode(session, reason="test")

    def item(article_id: str, source_id: str):
        return RssArticleContent(
            id=article_id,
            title=article_id,
            source_url=f"https://example.test/{article_id}",
            publish_date=NOW_ISO,
            fetched_date=NOW_ISO,
            source_id=source_id,
            content="body",
        )

    assert asyncio.run(sink.save(item("public", "platform"))) is False
    assert asyncio.run(sink.save(item("custom", "legacy_custom"))) is True


def test_collection_rechecks_consumer_fence_after_inflight_network_work(tmp_path, monkeypatch):
    import api.app as app_module

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add(_source("platform"))
        session.commit()

    class FakePipeline:
        async def run_task(self, _fetcher, *, lineage, **_params):
            with Session(sink.engine) as session:
                session.add(_article("late-public", "platform"))
                article = session.get(ArticleRecord, "late-public")
                article.fetch_run_id = lineage["fetch_run_id"]
                session.add(article)
                session.commit()
                sync_consumer_policy.activate_v2_consumer_mode(
                    session,
                    reason="manual_v2",
                )
            return SimpleNamespace(
                fetched_count=1,
                saved_count=1,
                skipped_count=0,
                saved_content_ids=["late-public"],
            )

    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "pipeline", FakePipeline())
    monkeypatch.setattr(app_module, "media_store", None)
    monkeypatch.setattr(app_module.fetcher_registry, "get_class", lambda _id: object)

    with pytest.raises(RuntimeError, match="本次本地采集作废"):
        asyncio.run(app_module.run_fetcher_with_tracking(
            "generic_rss",
            {"source_id": "platform"},
        ))
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "late-public") is None
        run = session.exec(select(FetchRunRecord)).one()
        assert run.status == "failed"
