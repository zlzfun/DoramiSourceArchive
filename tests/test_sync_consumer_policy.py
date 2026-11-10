"""拉取别人不改变本机能做什么(issue #263):没有整机 consumer 模式,也没有按源围栏。

这里守的是拆掉围栏之后仍然成立的几条:读者自定 RSS 的规则对所有节点生效;v1 兼容通道在本机
持有 v2 同步写入的数据后被锁;首次同步不动本机的源;同步来的词表只读。
"""

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


def test_policy_never_blocks_public_sources_and_keeps_custom_rss_rules(tmp_path):
    """公共源在任何节点上都恒为可采、可析、可管——即使它带着「上游也有」的簿记。读者自定 RSS 的
    规则(须启用、凭据源不经 MaaS)在已接入上游的节点上原样保留;没有上游的节点上这里不拦
    (由各自的业务路径把关),两类节点都与改动前一致。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        upstream_also_has = _source("platform_synced")
        upstream_also_has.collection_authority_id = "producer-a"
        paused_custom = _source("legacy_paused", owner="alice")
        paused_custom.is_active = False
        session.add_all([
            _source("platform"),
            upstream_also_has,
            _source("legacy_custom", owner="alice"),
            _source("user_rss_signed", owner="alice", credentialed=True),
            paused_custom,
        ])
        session.commit()

        # 没有上游:这里对任何源都不拦
        for source_id in ("platform", "platform_synced", "never-registered",
                          "legacy_custom", "user_rss_signed", "legacy_paused"):
            for operation in ("collection", "analysis", "governance"):
                assert sync_consumer_policy.local_source_operation_allowed(
                    session, source_id, operation=operation
                ), (source_id, operation)

        from services import user_sources

        user_sources.set_feature_enabled(session, True)
        session.add(AppSettingRecord(
            key=remote_sync.REMOTE_SYNC_STATE_KEY,
            value=json.dumps({"targets": {"http://producer.test": {"v2_authority_id": "producer-a"}}}),
        ))
        session.commit()
        for source_id in ("platform", "platform_synced", "never-registered"):
            for operation in ("collection", "analysis", "governance"):
                assert sync_consumer_policy.local_source_operation_allowed(
                    session, source_id, operation=operation
                ), (source_id, operation)
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
        assert not sync_consumer_policy.local_source_operation_allowed(
            session, "legacy_paused", operation="collection"
        )
        assert sync_consumer_policy.local_source_operation_allowed(
            session, "legacy_paused", operation="governance"
        )
        with pytest.raises(ValueError):
            sync_consumer_policy.local_source_operation_allowed(session, "platform", operation="x")


def test_manual_v2_launch_prepares_the_target_without_touching_local_work(tmp_path, monkeypatch):
    """启动首次 v2 同步只登记目标,不写任何围栏标记,也不停用本机的源或删本机的源状态。"""
    from api.routers import remote_sync as router

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add(_source("platform"))
        session.add(SourceStateRecord(
            source_id="platform", fetcher_id="generic_rss", status="healthy", updated_at=NOW_ISO,
        ))
        session.commit()
    launched = []
    monkeypatch.setattr(
        router.jobs, "launch", lambda *_args, **_kwargs: launched.append(True) or object()
    )
    with pytest.raises(remote_sync.RemoteSyncError, match="必须先验证"):
        router.launch_remote_sync_job(
            sink.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v2",
        )
    assert launched == []

    router.launch_remote_sync_job(
        sink.engine,
        base_url="http://producer.test",
        username="admin",
        password="secret",
        protocol="v2",
        v2_probe=_v2_probe(),
    )
    assert launched == [True]
    with Session(sink.engine) as session:
        assert session.get(AppSettingRecord, sync_consumer_policy.V2_CONSUMER_MODE_KEY) is None
        assert session.get(SourceConfigRecord, "platform").is_active is True
        assert session.get(SourceStateRecord, "platform") is not None
        assert sync_consumer_policy.upstream_established(session) is True
        assert sync_consumer_policy.upstream_provides_events(session) is True
        for operation in ("collection", "analysis", "governance"):
            assert sync_consumer_policy.local_source_operation_allowed(
                session, "platform", operation=operation
            )


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
        assert not sync_consumer_policy.upstream_established(session)


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
        # before anything is written.
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

        # issue #263:重基线不停用任何源、不删任何源状态——拉取不改变本机能做什么。
        assert session.get(SourceConfigRecord, "remote-public").is_active is True
        assert session.get(SourceConfigRecord, "legacy-public").is_active is True
        assert session.get(SourceConfigRecord, "local-custom").is_active is True
        assert session.get(ArticleRecord, "remote-article").read_count == 7
        assert session.get(ArticleAnalysisRecord, "remote-article") is not None
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "remote-article",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None
        assert session.get(SourceStateRecord, "remote-public") is not None
        assert session.get(SourceStateRecord, "legacy-public") is not None
        # 该上游写入的行只失效接收修订(置 0),状态行保留:它同时是本机向下游导出的索引。
        entity = session.get(ArchiveSyncEntityStateRecord, ("articles", "remote-article"))
        assert (entity.authority_id, entity.revision, entity.operation) == (authority_id, 0, "upsert")
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


def test_enabling_a_v2_schedule_writes_no_node_level_marker(tmp_path):
    """启用 v2 定时只保存定时配置:不写围栏标记,本机照常可以采集与分析。"""
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

    explicit_v2 = _sink(tmp_path, "v2.db")
    with Session(explicit_v2.engine) as session:
        session.add(_source("platform"))
        session.commit()
    saved = remote_sync.save_schedule(
        explicit_v2.engine,
        {"enabled": True, "protocol": "v2"},
        updated_at=NOW_ISO,
    )
    assert saved["enabled"] is True and saved["protocol"] == "v2"
    with Session(explicit_v2.engine) as session:
        assert session.get(AppSettingRecord, sync_consumer_policy.V2_CONSUMER_MODE_KEY) is None
        assert not sync_consumer_policy.v2_receiver_state_present(session)
        assert sync_consumer_policy.local_source_operation_allowed(
            session, "platform", operation="collection"
        )
    assert not hasattr(remote_sync, "activate_consumer_for_enabled_v2_schedule")


def test_node_holding_synced_rows_cannot_be_downgraded_to_enabled_v1(tmp_path):
    """v1 归档通道按「没有印记」写文章,会绕过 v2 的合并规则:库里一旦有 v2 同步写入的行就锁住。
    只是保存过 v2 定时、还没同步过任何数据的节点不受限。"""
    from api.routers import remote_sync as router

    fresh = _sink(tmp_path, "fresh.db")
    remote_sync.save_schedule(
        fresh.engine, {"enabled": True, "protocol": "v2"}, updated_at=NOW_ISO,
    )
    downgraded = remote_sync.save_schedule(
        fresh.engine, {"enabled": True, "protocol": "v1"}, updated_at=NOW_ISO,
    )
    assert downgraded["protocol"] == "v1" and downgraded["enabled"] is True

    synced = _sink(tmp_path, "synced.db")
    with Session(synced.engine) as session:
        session.add(_source("remote"))
        session.commit()
        remote_source = session.get(SourceConfigRecord, "remote")
        remote_source.collection_authority_id = "producer-a"
        session.add(remote_source)
        session.commit()
        assert sync_consumer_policy.v2_receiver_state_present(session)
    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级为 v1"):
        remote_sync.save_schedule(
            synced.engine,
            {"enabled": True, "protocol": "v1"},
            updated_at=NOW_ISO,
        )
    with pytest.raises(remote_sync.RemoteSyncError, match="不能降级启动 v1"):
        router.launch_remote_sync_job(
            synced.engine,
            base_url="http://producer.test",
            username="admin",
            password="secret",
            protocol="v1",
        )

    # A stored v1 intent is likewise safe-disabled when synced rows exist.
    with Session(synced.engine) as session:
        session.add(
            AppSettingRecord(
                key=remote_sync.REMOTE_SYNC_SCHEDULE_KEY,
                value=json.dumps({"enabled": True, "protocol": "v1"}),
            )
        )
        session.commit()
    view = remote_sync.load_schedule(synced.engine)
    assert view["enabled"] is False
    assert view["protocol_downgrade_blocked"] is True


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


def _establish_upstream(session, *, capabilities=None):
    session.add(AppSettingRecord(
        key=remote_sync.REMOTE_SYNC_STATE_KEY,
        value=json.dumps({"targets": {"http://producer.test": {
            "v2_authority_id": "producer-a",
            "v2_capabilities": list(
                remote_sync.archive_sync_v2.CAPABILITIES if capabilities is None else capabilities
            ),
        }}}),
    ))
    session.commit()


def test_node_with_an_upstream_still_queues_and_claims_its_own_public_articles(tmp_path):
    """有上游的节点上,本机自己写入的公共文章照常入队、认领;同步写入的那一篇不在本机分析。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        synced = _article("synced", "platform")
        synced.analysis_authority_id = "producer-a"
        session.add_all([
            _source("platform"),
            _source("legacy_custom", owner="alice"),
            _article("public", "platform"),
            synced,
            _article("custom", "legacy_custom"),
        ])
        session.commit()
        from services import user_sources

        user_sources.set_feature_enabled(session, True)
        _establish_upstream(session)
        stats = scan_analysis_backfill(session, now=NOW)
        assert stats.created == 2
        assert session.get(ArticleAnalysisRecord, "synced") is None
        assert session.get(ArticleAnalysisRecord, "public").status == "pending"
        assert session.get(ArticleAnalysisRecord, "custom").status == "pending"
        tasks = claim_analysis_tasks(session, worker_id="worker", now=NOW)
        assert sorted(task.article_id for task in tasks) == ["custom", "public"]
        assert queue_article_analysis(session, "synced", now=NOW) == "remote_authority"


def test_source_analysis_switch_off_keeps_local_articles_out_of_the_queue(tmp_path):
    """同步建出来的源分析默认关:本机在这个源下抓到的文章不分析,直到管理员打开开关。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        source = _source("platform")
        source.ai_analysis_enabled = False
        source.collection_authority_id = "producer-a"
        session.add_all([source, _article("public", "platform")])
        session.commit()
        assert queue_article_analysis(session, "public", now=NOW) == "skipped"
        session.commit()
        assert claim_analysis_tasks(session, worker_id="worker", now=NOW) == []

        source = session.get(SourceConfigRecord, "platform")
        source.ai_analysis_enabled = True
        session.add(source)
        session.commit()
        assert queue_article_analysis(session, "public", now=NOW) in {"created", "invalidated"}
        session.commit()
        [task] = claim_analysis_tasks(session, worker_id="worker", now=NOW)
        assert task.article_id == "public"


def test_synced_taxonomy_disables_local_taxonomy_auto_governance(tmp_path, monkeypatch):
    from services import taxonomy

    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="taxonomy:authority_id", value="producer-a"))
        session.commit()
        assert sync_consumer_policy.taxonomy_governed_remotely(session)
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


def test_claim_revokes_tasks_of_a_custom_source_paused_after_queueing(tmp_path):
    """已接入上游的节点上(与改动前一致):读者自定源停用后,排队中的分析任务在认领时被撤回;
    公共源的任务不受影响。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add_all([
            _source("platform"),
            _source("legacy_custom", owner="alice"),
            _article("public", "platform"),
            _article("custom", "legacy_custom"),
        ])
        session.commit()
        from services import user_sources

        user_sources.set_feature_enabled(session, True)
        _establish_upstream(session)
        assert queue_article_analysis(session, "public", now=NOW) == "created"
        assert queue_article_analysis(session, "custom", now=NOW) == "created"
        session.commit()
        custom_source = session.get(SourceConfigRecord, "legacy_custom")
        custom_source.is_active = False
        session.add(custom_source)
        session.commit()

        tasks = claim_analysis_tasks(session, worker_id="worker", now=NOW)
        assert [task.article_id for task in tasks] == ["public"]
        custom = session.get(ArticleAnalysisRecord, "custom")
        assert custom.status == "skipped"


def test_article_taken_over_during_llm_discards_the_local_result(tmp_path):
    """本机分析在途时,上游发来这篇文章更新的版本并接管:本机这次的结果作废、租约关闭,
    之后等上游的分析;依赖流被登记为待重放。"""
    sink = _sink(tmp_path)
    producer = _sink(tmp_path, "producer.db")
    with Session(producer.engine) as session:
        newer = _article("public", "platform")
        newer.content = "newer upstream body"
        newer.archive_updated_at = (NOW + dt.timedelta(hours=1)).isoformat()
        session.add(newer)
        session.commit()
    with Session(sink.engine) as session:
        local = _article("public", "platform")
        local.archive_updated_at = NOW_ISO
        session.add_all([_source("platform"), local])
        session.commit()
        assert queue_article_analysis(session, "public", now=NOW) == "created"
        session.commit()
        [task] = claim_analysis_tasks(session, worker_id="worker", now=NOW)

    async def taken_over_while_running(*_args):
        page = remote_sync.archive_sync_v2.export_page(producer.engine, "articles")
        applied = remote_sync.archive_sync_v2.import_page(
            sink.engine, page, expected_stream="articles"
        )
        assert applied["taken_over"] == 1
        return _analysis_payload()

    result = asyncio.run(process_claimed_analysis(
        sink.engine,
        task,
        llm_config=LLMConfig(
            base_url="https://maas.invalid/v1",
            api_key="test",
            model="test",
        ),
        analyzer=taken_over_while_running,
        now_fn=lambda: NOW,
    ))
    assert result.status == "superseded"
    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "public")
        assert article.content == "newer upstream body" and article.analysis_authority_id
        record = session.get(ArticleAnalysisRecord, "public")
        assert record.status == "pending"
        assert record.quality_score is None
        assert record.lease_owner is None
        assert queue_article_analysis(session, "public", now=NOW) == "remote_authority"
    assert remote_sync.archive_sync_v2.pending_dependency_replay(sink.engine) == set(
        remote_sync.archive_sync_v2.DEPENDENCY_REPLAY_STREAMS
    )


def test_storage_accepts_public_items_on_a_node_with_an_upstream(tmp_path):
    """有上游的节点照常可以把自己抓到的公共条目入库(含上游也有的源);停用的读者自定源仍被挡住。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        synced = _source("platform_synced")
        synced.collection_authority_id = "producer-a"
        paused = _source("legacy_paused", owner="alice")
        paused.is_active = False
        session.add_all([
            _source("platform"), synced, _source("legacy_custom", owner="alice"), paused,
        ])
        session.commit()
        from services import user_sources

        user_sources.set_feature_enabled(session, True)
        _establish_upstream(session)

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

    assert asyncio.run(sink.save(item("public", "platform"))) is True
    assert asyncio.run(sink.save(item("public-synced-source", "platform_synced"))) is True
    assert asyncio.run(sink.save(item("custom", "legacy_custom"))) is True
    assert asyncio.run(sink.save(item("paused", "legacy_paused"))) is False
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "public-synced-source").analysis_authority_id == ""


def test_local_fetch_that_changes_a_synced_article_claims_it_as_the_local_version(tmp_path):
    """本机抓取补全了一篇同步写入、还没有正文的文章:它从此是本机版本——文章与其分析行的同步
    印记清掉,修订号前进。"""
    sink = _sink(tmp_path)
    with Session(sink.engine) as session:
        session.add(_source("platform"))
        stub = _article("stub", "platform")
        stub.has_content = False
        stub.content = ""
        stub.analysis_authority_id = "producer-a"
        stub.archive_updated_at = "2026-09-01T00:00:00+00:00"
        session.add(stub)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id="stub", status="skipped", tagging_status="pending", content_hash="old",
            authority_id="producer-a", authority_revision="7",
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.commit()

    outcome = asyncio.run(sink.save_with_outcome(RssArticleContent(
        id="stub", title="stub", source_url="https://example.test/stub",
        publish_date=NOW_ISO, fetched_date=NOW_ISO, source_id="platform", content="full body",
    )))
    assert outcome == "content_filled"
    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "stub")
        analysis = session.get(ArticleAnalysisRecord, "stub")
        assert article.analysis_authority_id == "" and article.content == "full body"
        assert article.archive_updated_at > "2026-09-01T00:00:00+00:00"
        assert (analysis.authority_id, analysis.authority_revision) == ("", "")
        state = session.get(ArchiveSyncEntityStateRecord, ("articles", "stub"))
        assert state.authority_id == "" and state.local_revision == state.revision
