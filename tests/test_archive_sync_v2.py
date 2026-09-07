import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest
import httpx
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    ArchiveSyncClockRecord,
    ArchiveSyncEntityStateRecord,
    CmsTagCandidateRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagAliasRecord,
    CmsTagRecord,
    CollectionJobRunRecord,
    FetchRunRecord,
    RemoteCandidateEvidenceRecord,
    MediaAssetRecord,
    SourceConfigRecord,
    SourceStateRecord,
    TaxonomyVersionRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import remote_sync as remote_sync_service  # noqa: E402
from services.article_analysis import compute_content_hash, queue_article_analysis  # noqa: E402
from services.media_store import extract_image_urls  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _sink(tmp_path, name):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _source(source_id="rss_platform", **overrides):
    data = dict(
        source_id=source_id,
        name="Platform",
        source_type="rss",
        url="https://example.test/feed.xml",
        category="official",
        fetcher_id="generic_rss",
        owner_username="",
        ai_analysis_enabled=True,
        is_active=True,
        fetch_interval_minutes=60,
        cron_expr="0 * * * *",
        params_json='{"feed_url":"https://example.test/feed.xml"}',
        created_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    data.update(overrides)
    return SourceConfigRecord(**data)


def _article(article_id="article-1", **overrides):
    data = dict(
        id=article_id,
        title="Article",
        content_type="rss_article",
        source_id="rss_platform",
        source_url="https://example.test/a",
        publish_date="2026-09-01T00:00:00+00:00",
        fetched_date="2026-09-01T01:00:00+00:00",
        archive_updated_at="2026-09-02T01:00:00+00:00",
        has_content=True,
        content="Full body",
        extensions_json="{}",
    )
    data.update(overrides)
    return ArticleRecord(**data)


def _copy_stream(producer, consumer, stream, *, limit=1000):
    raw = archive_sync_v2.export_page(
        producer.engine,
        stream,
        limit=limit,
    )
    result = archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)
    return raw, result


def test_sources_remain_reader_visible_but_become_remote_managed(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "producer-source.db")
    consumer = _sink(tmp_path, "consumer-source.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source(
            updated_at="2026-09-01T00:00:00+00:00",
            ai_analysis_enabled=False,
        ))
        session.commit()

    _copy_stream(producer, consumer, "sources")
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row.is_active is True  # reader-visible metadata is not disabled
        assert row.collection_authority_id == authority
        assert row.ai_analysis_enabled is True

    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        row.is_active = False
        row.ai_analysis_enabled = False
        row.fetch_interval_minutes = 99
        row.updated_at = "2026-09-03T00:00:00+00:00"
        session.add(row)
        session.commit()
    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_platform")
        assert row.is_active is False
        assert row.ai_analysis_enabled is False
        assert row.fetch_interval_minutes == 99

    import api.app as app_module
    monkeypatch.setattr(app_module, "db_sink", consumer)
    with pytest.raises(ValueError, match="(远端权威节点|v2 接收端采集围栏)"):
        asyncio.run(app_module.run_fetcher_with_tracking(
            "generic_rss", {"source_id": "rss_platform"}
        ))


def test_source_physical_delete_tombstone_removes_receiver_config(tmp_path):
    producer = _sink(tmp_path, "producer-source-delete.db")
    consumer = _sink(tmp_path, "consumer-source-delete.db")
    with Session(producer.engine) as session:
        session.add_all([
            _source(),
            _article(),
            SourceStateRecord(
                source_id="rss_platform",
                fetcher_id="rss_platform",
                status="healthy",
                updated_at="2026-09-01T00:00:00+00:00",
            ),
        ])
        session.commit()

    checkpoints = {}
    for stream in ("sources", "articles", "source_states"):
        first_page = archive_sync_v2.export_page(producer.engine, stream)
        first_manifest, _ = archive_sync_v2.parse_page(
            first_page, expected_stream=stream
        )
        checkpoints[stream] = first_manifest["snapshot"]
        archive_sync_v2.import_page(consumer.engine, first_page, expected_stream=stream)
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "rss_platform") is not None
        assert session.get(SourceStateRecord, "rss_platform") is not None

    with Session(producer.engine) as session:
        session.delete(session.get(SourceConfigRecord, "rss_platform"))
        session.commit()
        article = session.get(ArticleRecord, "article-1")
        article.content = "updated after source delete"
        session.add(article)
        session.commit()

    for stream in ("sources", "articles", "source_states"):
        delta = archive_sync_v2.export_page(
            producer.engine, stream, since=checkpoints[stream]
        )
        _manifest, rows = archive_sync_v2.parse_page(delta, expected_stream=stream)
        if stream in {"sources", "source_states"}:
            assert len(rows) == 1 and rows[0]["operation"] == "tombstone"
        result = archive_sync_v2.import_page(
            consumer.engine, delta, expected_stream=stream
        )
        if stream in {"sources", "source_states"}:
            assert result["deleted"] in {0, 1}
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "rss_platform") is None
        assert session.get(SourceStateRecord, "rss_platform") is None
        article = session.get(ArticleRecord, "article-1")
        assert article is not None and article.content == "updated after source delete"


def test_midflight_authority_takeover_fails_runs_and_removes_only_new_local_rows(tmp_path, monkeypatch):
    consumer = _sink(tmp_path, "midflight-takeover.db")
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article("preexisting"))
        session.commit()

    import api.app as app_module
    monkeypatch.setattr(app_module, "db_sink", consumer)

    async def fake_run_task(_fetcher, *, lineage, **_params):
        with Session(consumer.engine) as session:
            session.add(_article("created-midflight", fetch_run_id=lineage["fetch_run_id"]))
            source = session.get(SourceConfigRecord, "rss_platform")
            source.collection_authority_id = "external"
            session.add(source)
            session.commit()
        return SimpleNamespace(
            fetched_count=1, saved_count=1, skipped_count=0,
            saved_content_ids=["created-midflight"],
        )

    monkeypatch.setattr(app_module.pipeline, "run_task", fake_run_task)
    with pytest.raises(RuntimeError, match="远端权威接管"):
        asyncio.run(app_module.run_single_fetch_as_collection(
            "generic_rss", {"source_id": "rss_platform"}, "race"
        ))
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "created-midflight") is None
        assert session.get(ArticleRecord, "preexisting") is not None
        assert session.exec(select(FetchRunRecord)).one().status == "failed"
        assert session.exec(select(CollectionJobRunRecord)).one().status == "failed"


def test_remote_managed_source_and_article_admin_mutations_are_fenced(tmp_path, monkeypatch):
    sink = _sink(tmp_path, "admin-authority-fence.db")
    with Session(sink.engine) as session:
        session.add(_source(collection_authority_id="external"))
        session.add(_article(analysis_authority_id="external"))
        session.commit()

    from fastapi import HTTPException
    from api.routers import articles as articles_router
    from api.routers import source_configs as sources_router
    from api.schemas import BatchOpParams

    with Session(sink.engine) as session:
        with pytest.raises(HTTPException) as update_source:
            sources_router.update_source_config(
                "rss_platform", sources_router.SourceConfigUpdate(name="changed"), session
            )
        assert update_source.value.status_code == 409
        with pytest.raises(HTTPException) as toggle_source:
            sources_router.toggle_source_config("rss_platform", False, session)
        assert toggle_source.value.status_code == 409
        with pytest.raises(HTTPException) as delete_source:
            sources_router.delete_source_config("rss_platform", session)
        assert delete_source.value.status_code == 409

    monkeypatch.setattr(articles_router.deps, "get_db_sink", lambda: sink)
    monkeypatch.setattr(
        articles_router, "_app", lambda: SimpleNamespace(queue_article_analysis_after_commit=lambda _ids: None)
    )
    with pytest.raises(HTTPException) as update_article:
        asyncio.run(articles_router.update_article(
            "article-1", articles_router.ArticleUpdateParams(title="changed")
        ))
    assert update_article.value.status_code == 409
    with pytest.raises(HTTPException) as delete_article:
        asyncio.run(articles_router.delete_article("article-1"))
    assert delete_article.value.status_code == 409
    with pytest.raises(HTTPException) as batch_delete:
        asyncio.run(articles_router.batch_delete_articles(BatchOpParams(ids=["article-1"])))
    assert batch_delete.value.status_code == 409
    with pytest.raises(HTTPException) as update_extensions:
        asyncio.run(articles_router.update_article(
            "article-1",
            articles_router.ArticleUpdateParams(extensions_json='{"reader_cache":true}'),
        ))
    assert update_extensions.value.status_code == 409


def test_article_authority_suppresses_queue_and_fences_old_lease(tmp_path):
    producer = _sink(tmp_path, "producer-article.db")
    consumer = _sink(tmp_path, "consumer-article.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()
    with Session(consumer.engine) as session:
        old = _article(archive_updated_at="2026-09-01T01:00:00+00:00", content="Old")
        session.add(old)
        session.commit()
        assert queue_article_analysis(session, old.id) == "created"
        session.commit()

    _copy_stream(producer, consumer, "articles")
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "Full body"
        assert article.analysis_authority_id == authority
        assert analysis.status == "pending"
        assert analysis.lease_owner is None
        assert queue_article_analysis(session, article.id, force=True) == "remote_authority"


def test_article_handoff_keeps_same_content_result_until_remote_revision_arrives(tmp_path):
    producer = _sink(tmp_path, "producer-handoff.db")
    consumer = _sink(tmp_path, "consumer-handoff.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()
    with Session(consumer.engine) as session:
        article = _article(archive_updated_at="2026-09-01T01:00:00+00:00")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            quality_score=7.5, score_reason="old",
            summary="old summary", content_genre="industry_news",
            content_hash=compute_content_hash(article), analyzed_at=now,
            tagged_at=now, created_at=now, updated_at=now,
        ))
        session.commit()
    _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        record = session.get(ArticleAnalysisRecord, "article-1")
        assert record.status == "pending"
        assert record.quality_score == 7.5
        assert record.summary == "old summary"
        assert record.analyzed_at == now


def test_external_authority_wins_first_handoff_even_when_local_clock_is_newer(tmp_path):
    producer = _sink(tmp_path, "producer-authority-wins.db")
    consumer = _sink(tmp_path, "consumer-authority-wins.db")
    with Session(producer.engine) as session:
        session.add(_source(updated_at="2026-09-01T00:00:00+00:00", name="External"))
        session.add(_article(
            archive_updated_at="2026-09-01T01:00:00+00:00",
            content="External body A",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source(updated_at="2026-09-03T00:00:00+00:00", name="Local edit"))
        local_article = _article(
            archive_updated_at="2026-09-03T01:00:00+00:00",
            content="Local body B",
        )
        session.add(local_article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=local_article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=9.0,
            content_hash=compute_content_hash(local_article),
            created_at="2026-09-03T01:00:00+00:00",
            updated_at="2026-09-03T01:00:00+00:00",
        ))
        session.add(SourceStateRecord(
            source_id="rss_platform",
            fetcher_id="rss_platform",
            status="healthy",
            last_completed_at="2026-09-03T01:00:00+00:00",
            updated_at="2026-09-03T01:00:00+00:00",
        ))
        session.commit()

    _copy_stream(producer, consumer, "sources")
    with Session(consumer.engine) as session:
        source = session.get(SourceConfigRecord, "rss_platform")
        assert source.name == "External"
        assert session.get(SourceStateRecord, "rss_platform") is None
    _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "External body A"
        assert article.archive_updated_at == "2026-09-01T01:00:00+00:00"
        assert analysis.status == "pending"
        assert analysis.quality_score is None


def test_analysis_maps_tags_by_code_and_preserves_manual_overlay(tmp_path):
    producer = _sink(tmp_path, "producer-analysis.db")
    consumer = _sink(tmp_path, "consumer-analysis.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        article = _article()
        session.add(article)
        tag = CmsTagRecord(
            code="ai-agents", kind="topic", name_zh="智能体", name_en="AI Agents",
            normalized_name="ai agents", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id, status="succeeded", tagging_status="succeeded",
            quality_score=8.5, score_reason="good",
            summary="summary", content_genre="industry_news",
            content_hash=compute_content_hash(article), model_name="model",
            prompt_version="p1", scoring_version="s1", taxonomy_version=1,
            analyzed_at=now, tagged_at=now, created_at=now, updated_at=now,
        ))
        session.add(ArticleTagAssignmentRecord(
            article_id=article.id, tag_id=int(tag.id), tag_kind="topic", is_primary=True,
            relevance=0.9, assignment_source="llm", prompt_version="p1",
            taxonomy_version=1, created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    _copy_stream(producer, consumer, "articles")
    with Session(consumer.engine) as session:
        tag = CmsTagRecord(
            code="manual-industry", kind="industry", name_zh="人工行业", name_en="Manual Industry",
            normalized_name="manual industry", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id="article-1", tag_id=int(tag.id), tag_kind="industry", is_primary=True,
            relevance=1, assignment_source="manual", prompt_version="",
            taxonomy_version=1, created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "analyses")
    with Session(producer.engine) as session:
        producer_revision = session.get(
            ArchiveSyncEntityStateRecord, ("analyses", "article-1")
        ).revision
    with Session(consumer.engine) as session:
        record = session.get(ArticleAnalysisRecord, "article-1")
        assignments = session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "article-1"
        )).all()
        assert record.quality_score == 8.5
        assert record.authority_id == archive_sync_v2.producer_authority_id(producer.engine)
        assert record.authority_revision == str(producer_revision)
        assert sorted((row.assignment_source, row.tag_kind, row.is_primary) for row in assignments) == [
            ("llm", "topic", False), ("manual", "industry", True),
        ]
        assert record.primary_tag_id == next(row.tag_id for row in assignments if row.assignment_source == "manual")

    # Replay cannot regress or duplicate the authority result.
    _, replay = _copy_stream(producer, consumer, "analyses")
    assert replay["inserted"] == 0
    assert replay["updated"] == 0


def test_new_article_body_hides_old_authority_analysis_until_matching_result_arrives(tmp_path):
    producer = _sink(tmp_path, "producer-body-revision.db")
    consumer = _sink(tmp_path, "consumer-body-revision.db")
    first_revision = "2026-09-02T01:00:00+00:00"
    second_revision = "2026-09-03T01:00:00+00:00"
    with Session(producer.engine) as session:
        article = _article(content="Body A", archive_updated_at=first_revision)
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=8.0,
            content_hash=compute_content_hash(article),
            created_at=first_revision,
            updated_at=first_revision,
        ))
        session.commit()
    article_raw = archive_sync_v2.export_page(producer.engine, "articles")
    article_manifest, _ = archive_sync_v2.parse_page(
        article_raw, expected_stream="articles"
    )
    archive_sync_v2.import_page(consumer.engine, article_raw, expected_stream="articles")
    analysis_raw = archive_sync_v2.export_page(producer.engine, "analyses")
    analysis_manifest, _ = archive_sync_v2.parse_page(
        analysis_raw, expected_stream="analyses"
    )
    archive_sync_v2.import_page(consumer.engine, analysis_raw, expected_stream="analyses")

    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Body B"
        article.archive_updated_at = second_revision
        session.add(article)
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        analysis.content_hash = compute_content_hash(article)
        analysis.quality_score = 9.0
        analysis.updated_at = second_revision
        session.add(analysis)
        session.commit()

    raw = archive_sync_v2.export_page(
        producer.engine,
        "articles",
        since=article_manifest["snapshot"],
    )
    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="articles")
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert article.content == "Body B"
        assert analysis.status == "pending"
        assert analysis.quality_score is None

    raw = archive_sync_v2.export_page(
        producer.engine,
        "analyses",
        since=analysis_manifest["snapshot"],
    )
    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="analyses")
    with Session(consumer.engine) as session:
        analysis = session.get(ArticleAnalysisRecord, "article-1")
        assert analysis.status == "succeeded"
        assert analysis.quality_score == 9.0


def test_taxonomy_full_snapshot_retires_receiver_only_active_tag(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-retire.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-retire.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="external-tag", kind="topic", name_zh="外部标签",
            normalized_name="外部标签", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(CmsTagRecord(
            code="local-only", kind="topic", name_zh="本地标签",
            normalized_name="本地标签", status="active", taxonomy_version=1,
            user_selectable=True, filterable=True, recommendable=True,
            created_at=now, updated_at=now,
        ))
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    with Session(consumer.engine) as session:
        stale = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "local-only")).one()
        assert stale.status == "deprecated"
        assert stale.user_selectable is False
        assert stale.filterable is False
        assert stale.recommendable is False
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        assert external.status == "active"

    no_op = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    manifest, rows = archive_sync_v2.parse_page(no_op, expected_stream="taxonomy")
    assert rows == []
    assert manifest["full_snapshot"] is False
    archive_sync_v2.import_page(consumer.engine, no_op, expected_stream="taxonomy")
    with Session(consumer.engine) as session:
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        assert external.status == "active"

    from services.taxonomy import rename_tag
    with Session(producer.engine) as session:
        external = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "external-tag")).one()
        rename_tag(
            session, int(external.id), actor_id="admin", name_zh="外部新标签",
            reason="sync watermark regression",
        )
    renamed = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    renamed_manifest, renamed_rows = archive_sync_v2.parse_page(
        renamed, expected_stream="taxonomy", requested_since="1"
    )
    assert int(renamed_manifest["snapshot"]) > 1
    assert renamed_manifest["taxonomy_version"] == 1
    assert renamed_manifest["full_snapshot"] is True
    assert any(row["payload"]["name_zh"] == "外部新标签" for row in renamed_rows)


def test_bad_page_is_atomic_and_checkpoint_cannot_be_claimed(tmp_path):
    producer = _sink(tmp_path, "producer-bad.db")
    consumer = _sink(tmp_path, "consumer-bad.db")
    with Session(producer.engine) as session:
        session.add(_article("a1"))
        session.add(_article("a2"))
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "articles")
    lines = raw.splitlines()
    damaged = json.loads(lines[2])
    damaged["payload"]["title"] = "tampered"
    lines[2] = json.dumps(damaged)
    with pytest.raises(archive_sync_v2.SyncV2Error, match="checksum"):
        archive_sync_v2.import_page(consumer.engine, "\n".join(lines) + "\n")
    with Session(consumer.engine) as session:
        assert session.exec(select(ArticleRecord)).all() == []


def test_taxonomy_full_replay_retries_candidate_reconciliation(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "producer-taxonomy-reconcile-retry.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-reconcile-retry.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="retry-tag", kind="topic", name_en="Retry Tag",
            normalized_name="retry tag", status="active", taxonomy_version=1,
            created_at=now, updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "taxonomy")
    calls = []

    def flaky_reconcile(_session, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("simulated reconcile failure")

    monkeypatch.setattr(
        archive_sync_v2, "reconcile_synced_taxonomy_candidates", flaky_reconcile
    )
    with pytest.raises(RuntimeError, match="simulated reconcile failure"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    with Session(consumer.engine) as session:
        assert session.exec(select(CmsTagRecord).where(
            CmsTagRecord.code == "retry-tag"
        )).one() is not None

    replay = archive_sync_v2.import_page(
        consumer.engine, raw, expected_stream="taxonomy"
    )
    assert replay["inserted"] == 0 and replay["updated"] == 0
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_taxonomy_snapshot_allows_canonical_and_alias_owner_swaps(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-swap.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-swap.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        tag_a = CmsTagRecord(
            code="a", kind="topic", name_en="Alpha", normalized_name="alpha",
            status="active", taxonomy_version=1, created_at=now, updated_at=now,
        )
        tag_b = CmsTagRecord(
            code="b", kind="topic", name_en="Beta", normalized_name="beta",
            status="active", taxonomy_version=1, created_at=now, updated_at=now,
        )
        session.add_all([tag_a, tag_b])
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.flush()
        session.add_all([
            CmsTagAliasRecord(
                tag_id=int(tag_a.id), kind="topic", alias="Alias X",
                normalized_alias="alias x", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
            CmsTagAliasRecord(
                tag_id=int(tag_b.id), kind="topic", alias="Alias Y",
                normalized_alias="alias y", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
        ])
        session.commit()
    with Session(consumer.engine) as session:
        old_a = CmsTagRecord(
            code="a", kind="topic", name_en="Beta", normalized_name="beta",
            status="active", created_at=now, updated_at=now,
        )
        old_b = CmsTagRecord(
            code="b", kind="topic", name_en="Alpha", normalized_name="alpha",
            status="active", created_at=now, updated_at=now,
        )
        session.add_all([old_a, old_b])
        session.flush()
        session.add_all([
            CmsTagAliasRecord(
                tag_id=int(old_a.id), kind="topic", alias="Alias Y",
                normalized_alias="alias y", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
            CmsTagAliasRecord(
                tag_id=int(old_b.id), kind="topic", alias="Alias X",
                normalized_alias="alias x", alias_type="synonym", locale="en",
                created_at=now, updated_at=now,
            ),
        ])
        session.commit()

    _copy_stream(producer, consumer, "taxonomy")
    with Session(consumer.engine) as session:
        tags = {row.code: row for row in session.exec(select(CmsTagRecord)).all()}
        aliases = {row.normalized_alias: row.tag_id for row in session.exec(
            select(CmsTagAliasRecord)
        ).all()}
        assert tags["a"].normalized_name == "alpha"
        assert tags["b"].normalized_name == "beta"
        assert aliases == {"alias x": tags["a"].id, "alias y": tags["b"].id}


@pytest.mark.parametrize("stream", ["sources", "articles", "source_states"])
def test_legacy_custom_source_id_collision_is_rejected_atomically(tmp_path, stream):
    producer = _sink(tmp_path, f"producer-private-collision-{stream}.db")
    consumer = _sink(tmp_path, f"consumer-private-collision-{stream}.db")
    with Session(producer.engine) as session:
        if stream == "sources":
            session.add_all([_source("a-public-new"), _source("legacy-custom")])
        elif stream == "articles":
            session.add_all([
                _article("a-public-new", source_id="public-new"),
                _article("legacy-collision", source_id="legacy-custom"),
            ])
        else:
            session.add_all([
                SourceStateRecord(
                    source_id="a-public-new", fetcher_id="rss", status="healthy",
                    updated_at="2026-09-02T00:00:00+00:00",
                ),
                SourceStateRecord(
                    source_id="legacy-custom", fetcher_id="rss", status="healthy",
                    updated_at="2026-09-02T00:00:00+00:00",
                ),
            ])
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source("legacy-custom", owner_username="alice"))
        session.add(_article("local-private", source_id="legacy-custom"))
        session.add(SourceStateRecord(
            source_id="legacy-custom", fetcher_id="private-rss", status="healthy",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, stream)
    with pytest.raises(archive_sync_v2.SyncV2Error, match="local custom source"):
        archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)

    with Session(consumer.engine) as session:
        private = session.get(SourceConfigRecord, "legacy-custom")
        assert private.owner_username == "alice"
        assert private.collection_authority_id == ""
        assert session.get(ArticleRecord, "local-private").analysis_authority_id == ""
        assert session.get(SourceStateRecord, "legacy-custom").fetcher_id == "private-rss"
        assert session.get(SourceConfigRecord, "a-public-new") is None
        assert session.get(ArticleRecord, "a-public-new") is None
        assert session.get(SourceStateRecord, "a-public-new") is None


def test_keyset_snapshot_and_source_state_readiness_fence(tmp_path):
    producer = _sink(tmp_path, "producer-pages.db")
    consumer = _sink(tmp_path, "consumer-pages.db")
    with Session(producer.engine) as session:
        session.add(_article("a1", archive_updated_at="2026-09-01T01:00:00+00:00"))
        session.add(_article("a2", archive_updated_at="2026-09-01T01:00:00+00:00"))
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", last_success_at="2026-09-02T03:00:00+00:00",
            total_runs=1, success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    first_raw = archive_sync_v2.export_page(producer.engine, "articles", limit=1)
    first_manifest, _ = archive_sync_v2.parse_page(first_raw)
    second_raw = archive_sync_v2.export_page(
        producer.engine, "articles", limit=1,
        snapshot=first_manifest["snapshot"],
        after=first_manifest["next_cursor"],
    )
    archive_sync_v2.import_page(consumer.engine, first_raw)
    archive_sync_v2.import_page(consumer.engine, second_raw)
    with Session(consumer.engine) as session:
        assert [r.id for r in session.exec(select(ArticleRecord).order_by(ArticleRecord.id)).all()] == ["a1", "a2"]
        # Source terminal state is a final stream, not published by article pages.
        assert session.get(SourceStateRecord, "rss_platform") is None
    _copy_stream(producer, consumer, "source_states")
    with Session(producer.engine) as session:
        producer_revision = session.get(
            ArchiveSyncEntityStateRecord, ("source_states", "rss_platform")
        ).revision
    with Session(consumer.engine) as session:
        state = session.get(SourceStateRecord, "rss_platform")
        assert state.status == "healthy"
        assert state.last_run_id is None
        assert state.authority_id == archive_sync_v2.producer_authority_id(producer.engine)
        assert state.authority_revision == str(producer_revision)


def test_parse_page_rejects_manifest_type_and_request_echo_tampering(tmp_path):
    producer = _sink(tmp_path, "strict-page.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()
    raw = archive_sync_v2.export_page(producer.engine, "sources")
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="sources", requested_since="")
    bad = dict(manifest)
    bad["complete"] = "true"
    with pytest.raises(archive_sync_v2.SyncV2Error, match="boolean"):
        archive_sync_v2.parse_page(archive_sync_v2.encode_page(bad, rows), expected_stream="sources")
    with pytest.raises(archive_sync_v2.SyncV2Error, match="since"):
        archive_sync_v2.parse_page(raw, expected_stream="sources", requested_since="unexpected")


def test_export_does_not_launder_remote_or_legacy_user_source_rows(tmp_path, monkeypatch):
    producer = _sink(tmp_path, "no-launder.db")
    private_url = "https://img.test/legacy-private.png"
    with Session(producer.engine) as session:
        session.add(_source("remote-source", collection_authority_id="other-authority"))
        session.add(_article("orphan-local", source_id="remote-source"))
        session.add(SourceStateRecord(
            source_id="remote-source", fetcher_id="rss", status="healthy",
            authority_id="", updated_at="2026-09-02T00:00:00",
        ))
        session.add(_source("local-source"))
        session.add(_article("owned-local", source_id="local-source"))
        session.add(_source("legacy-custom", owner_username="alice"))
        session.add(_article(
            "legacy-private", source_id="legacy-custom",
            content=f"![private]({private_url})",
        ))
        session.flush()
        session.add(SourceStateRecord(
            source_id="legacy-custom", fetcher_id="rss", status="healthy",
            authority_id="", updated_at="2026-09-02T00:00:00",
        ))
        session.add(ArticleAnalysisRecord(
            article_id="legacy-private", status="succeeded", tagging_status="succeeded",
            authority_id="", created_at="2026-09-02T00:00:00+00:00",
            updated_at="2026-09-02T00:00:00+00:00",
        ))
        session.add(MediaAssetRecord(
            url_hash=hashlib.sha256(private_url.encode()).hexdigest(), url=private_url,
            status="cached", content_hash="b" * 64, mime="image/png", ext=".png",
            size_bytes=10, created_at="2026-09-02T00:00:00", updated_at="2026-09-02T00:00:00",
        ))
        session.commit()
    _, article_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "articles"), expected_stream="articles"
    )
    _, state_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "source_states"), expected_stream="source_states"
    )
    assert [row["identity"] for row in article_rows] == ["owned-local"]
    assert state_rows == []
    _, analysis_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "analyses"),
        expected_stream="analyses",
    )
    _, media_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(producer.engine, "media"), expected_stream="media"
    )
    assert analysis_rows == []
    assert media_rows == []

    from api.routers import archive_sync as archive_router
    monkeypatch.setattr(archive_router.deps, "get_db_sink", lambda: producer)
    response = archive_router.export_archive_articles_jsonl(limit=1000)
    v1_rows = [json.loads(line) for line in response.body.decode().splitlines()][1:]
    assert [row["article"]["id"] for row in v1_rows] == ["orphan-local", "owned-local"]


def test_media_install_rejects_declared_image_with_non_image_bytes(tmp_path):
    sink = _sink(tmp_path, "media-mime.db")
    body = b"<html>not an image</html>"
    key = hashlib.sha256(b"https://img.test/not-image.jpg").hexdigest()
    with Session(sink.engine) as session:
        session.add(MediaAssetRecord(
            url_hash=key, url="https://img.test/not-image.jpg", status="pending_sync",
            content_hash=hashlib.sha256(body).hexdigest(), mime="image/jpeg", ext=".jpg",
            size_bytes=len(body), created_at="2026-09-01", updated_at="2026-09-01",
        ))
        session.commit()
    with pytest.raises(archive_sync_v2.SyncV2Error, match="图片"):
        archive_sync_v2.install_media_bytes(sink.engine, tmp_path / "media-mime", key, body)


def test_media_scope_includes_cover_but_not_audio_and_verifies_binary(tmp_path):
    urls = extract_image_urls(
        "![body](https://img.test/body.png)",
        {"image_url": "https://img.test/podcast.jpg", "audio_url": "https://audio.test/e.mp3"},
    )
    assert "https://img.test/podcast.jpg" in urls
    assert not any("audio.test" in url for url in urls)

    sink = _sink(tmp_path, "media.db")
    body = b"\xff\xd8\xffimage bytes"
    content_hash = hashlib.sha256(body).hexdigest()
    url = "https://img.test/podcast.jpg"
    key = hashlib.sha256(url.encode()).hexdigest()
    with Session(sink.engine) as session:
        session.add(MediaAssetRecord(
            url_hash=key, url=url, status="pending_sync", content_hash=content_hash,
            mime="image/jpeg", ext=".jpg", size_bytes=len(body),
            created_at="2026-09-01", updated_at="2026-09-01",
        ))
        session.commit()
    record = archive_sync_v2.install_media_bytes(sink.engine, tmp_path / "media", key, body)
    assert record.status == "cached"
    assert record.updated_at == "2026-09-01"
    assert (tmp_path / "media" / content_hash[:2] / f"{content_hash}.jpg").read_bytes() == body


def test_media_refresh_replaces_cached_manifest_and_public_scope_is_enforced(tmp_path):
    sink = _sink(tmp_path, "media-refresh.db")
    public_url = "https://img.test/public.jpg"
    private_url = "https://img.test/private.jpg"
    downstream_url = "https://img.test/remote-owned.jpg"
    public_key = hashlib.sha256(public_url.encode()).hexdigest()
    old_body = b"\xff\xd8\xffold image"
    new_body = b"\xff\xd8\xffnew image"
    with Session(sink.engine) as session:
        session.add(_article(content=f"![cover]({public_url})"))
        session.add(_source("user_rss_private", owner_username="alice"))
        session.add(_article(
            "private-article",
            source_id="user_rss_private",
            content=f"![private]({private_url})",
        ))
        session.add(_article(
            "remote-owned-article", content=f"![remote]({downstream_url})",
            analysis_authority_id="upstream",
        ))
        session.add(MediaAssetRecord(
            url_hash=public_key,
            url=public_url,
            status="cached",
            content_hash=hashlib.sha256(old_body).hexdigest(),
            mime="image/jpeg",
            ext=".jpg",
            size_bytes=len(old_body),
            fetched_at="2026-09-01T00:00:00+00:00",
            created_at="2026-09-01T00:00:00+00:00",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
        assert archive_sync_v2.is_public_media_reference(session, public_url) is True
        assert archive_sync_v2.is_public_media_reference(session, private_url) is False
        assert archive_sync_v2.is_public_media_reference(session, downstream_url) is False

    payload = {
        "url_hash": public_key,
        "url": public_url,
        "content_hash": hashlib.sha256(new_body).hexdigest(),
        "mime": "image/jpeg",
        "ext": ".jpg",
        "size_bytes": len(new_body),
        "fetched_at": "2026-09-02T00:00:00+00:00",
        "updated_at": "2026-09-02T00:00:00+00:00",
    }
    row = archive_sync_v2._line(  # noqa: SLF001 - precise wire-contract fixture
        "media", payload, revision="1", identity=public_key
    )
    manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "media", "external", "1", "", [row], complete=True
    )
    archive_sync_v2.import_page(
        sink.engine,
        archive_sync_v2.encode_page(manifest, [row]),
        expected_stream="media",
    )
    with Session(sink.engine) as session:
        refreshed = session.get(MediaAssetRecord, public_key)
        assert refreshed.status == "pending_sync"
        assert refreshed.content_hash == hashlib.sha256(new_body).hexdigest()
        assert refreshed.fetched_at is None

    installed = archive_sync_v2.install_media_bytes(
        sink.engine, tmp_path / "media-refresh", public_key, new_body
    )
    assert installed.status == "cached"
    assert installed.updated_at == payload["updated_at"]


def test_public_media_reference_scans_past_substring_false_positives(tmp_path):
    sink = _sink(tmp_path, "media-reference-pagination.db")
    url = "https://img.test/late-real-image.png"
    with Session(sink.engine) as session:
        session.add_all([
            _article(
                f"a-false-positive-{index:03d}",
                content=f"ordinary link, not an image: {url}",
            )
            for index in range(205)
        ])
        session.add(_article("z-real-image", content=f"![image]({url})"))
        session.commit()
        assert archive_sync_v2.is_public_media_reference(session, url) is True


def test_media_manifest_rejects_path_traversal_extension(tmp_path):
    sink = _sink(tmp_path, "media-traversal.db")
    body = b"image bytes"
    url = "https://img.test/safe.jpg"
    payload = {
        "url_hash": hashlib.sha256(url.encode()).hexdigest(),
        "url": url,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "mime": "image/jpeg",
        "ext": "/../../../../escaped.py",
        "size_bytes": len(body),
        "updated_at": "2026-09-02T00:00:00+00:00",
    }
    row = archive_sync_v2._line(  # noqa: SLF001
        "media", payload, revision="1", identity=payload["url_hash"]
    )
    manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "media", "external", "1", "", [row], complete=True
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="extension"):
        archive_sync_v2.import_page(
            sink.engine,
            archive_sync_v2.encode_page(manifest, [row]),
            expected_stream="media",
        )
    assert not (tmp_path / "escaped.py").exists()


def test_candidate_evidence_is_minimized_and_review_only(tmp_path):
    sink = _sink(tmp_path, "candidate-inbound.db")
    payload = {
        "label": "Agent Memory",
        "kind": "topic",
        "confidence": 0.8,
        "article_fingerprint": "a" * 64,
        "source_provenance": "user_rss_example",
        "prompt_version": "p1",
    }
    manifest = {
        "kind": "manifest", "schema_version": archive_sync_v2.SCHEMA_VERSION,
        "stream": "candidate_evidence", "authority_id": "inner-stable",
        "snapshot": "2026-09-02T00:00:00+00:00", "after": "",
        "next_cursor": archive_sync_v2._encode_cursor("2026-09-01T00:00:00+00:00", "00000000000000000001"),
        "complete": True, "count": 1,
    }
    row = {"kind": "candidate_evidence", "schema_version": archive_sync_v2.SCHEMA_VERSION,
               "revision": "2026-09-01T00:00:00+00:00", "identity": "00000000000000000001",
               "checksum": archive_sync_v2.checksum(payload), "payload": payload}
    raw = archive_sync_v2.encode_page(manifest, [row])
    result = archive_sync_v2.import_candidate_evidence_page(sink.engine, raw)
    assert result["inserted"] == 1
    with Session(sink.engine) as session:
        evidence = session.exec(select(RemoteCandidateEvidenceRecord)).one()
        assert evidence.article_fingerprint == "a" * 64
        assert session.exec(select(CmsTagRecord)).first() is None  # never auto-activates a canonical taxonomy tag
        from api.routers.taxonomy import _candidate_payload
        candidate = session.get(CmsTagCandidateRecord, evidence.candidate_id)
        review = _candidate_payload(session, candidate)
        assert review["support_article_count_7d"] == 0
        assert review["remote_evidence"] == [{
            "authority_id": "inner-stable",
            "source_provenance": "user_rss_example",
            "confidence": 0.8,
            "label": "Agent Memory",
            "prompt_version": "p1",
            "created_at": evidence.created_at,
        }]
        assert "article_fingerprint" not in review["remote_evidence"][0]

    withdrawn_manifest = {
        **manifest,
        "snapshot": "2026-09-03T00:00:00+00:00",
        "next_cursor": "",
        "count": 0,
    }
    archive_sync_v2.import_candidate_evidence_page(
        sink.engine, archive_sync_v2.encode_page(withdrawn_manifest, [])
    )
    with Session(sink.engine) as session:
        assert session.exec(select(RemoteCandidateEvidenceRecord)).all() == []
        assert session.exec(select(CmsTagCandidateRecord)).all() == []

    leaking = json.loads(json.dumps(row))
    leaking["payload"]["content"] = "private body"
    leaking["checksum"] = archive_sync_v2.checksum(leaking["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match="unknown content fields"):
        archive_sync_v2.import_candidate_evidence_page(
            sink.engine, archive_sync_v2.encode_page(manifest, [leaking])
        )


def test_custom_candidate_evidence_uses_fixed_snapshot_keyset_pages(tmp_path):
    sink = _sink(tmp_path, "candidate-export.db")
    with Session(sink.engine) as session:
        session.add(_source("user_rss_local", owner_username="alice"))
        session.add(_source("legacy_custom", owner_username="alice"))
        session.add(_source(
            "user_rss_secret", owner_username="alice",
            params_json='{"credentialed_private":true}',
        ))
        session.add_all([
            _article("custom-a", source_id="user_rss_local"),
            _article("custom-b", source_id="legacy_custom"),
            _article("custom-secret", source_id="user_rss_secret"),
            _article("custom-orphan", source_id="user_rss_orphan"),
        ])
        candidate = CmsTagCandidateRecord(
            label="Memory", normalized_label="memory", proposed_kind="topic",
            first_seen_at="2026-09-01", last_seen_at="2026-09-01",
            created_at="2026-09-01", updated_at="2026-09-01",
        )
        session.add(candidate)
        session.flush()
        session.add_all([
            CmsTagCandidateEvidenceRecord(
                candidate_id=int(candidate.id), article_id=article_id,
                source_id=source_id, confidence=0.8, raw_label="Memory",
                created_at="2026-09-01T00:00:00+00:00",
            )
            for article_id, source_id in (
                ("custom-a", "user_rss_local"),
                ("custom-b", "legacy_custom"),
                ("custom-secret", "user_rss_secret"),
                ("custom-orphan", "user_rss_orphan"),
            )
        ])
        session.commit()
    first_raw = archive_sync_v2.export_custom_candidate_evidence_page(sink.engine, limit=1)
    first, first_rows = archive_sync_v2.parse_candidate_evidence_page(first_raw)
    assert first["complete"] is False and len(first_rows) == 1
    second_raw = archive_sync_v2.export_custom_candidate_evidence_page(
        sink.engine, snapshot=first["snapshot"], after=first["next_cursor"], limit=1,
    )
    second, second_rows = archive_sync_v2.parse_candidate_evidence_page(second_raw)
    assert second["snapshot"] == first["snapshot"]
    assert second["complete"] is True and len(second_rows) == 1
    assert second_rows[0]["identity"] > first_rows[0]["identity"]
    assert {
        row["payload"]["source_provenance"] for row in first_rows + second_rows
    } == {"user_rss_local", "legacy_custom"}

    receiver = _sink(tmp_path, "candidate-export-receiver.db")
    archive_sync_v2.import_candidate_evidence_page(receiver.engine, first_raw)
    tampered_manifest = {**second, "snapshot": "2099-01-01T00:00:00+00:00"}
    with pytest.raises(archive_sync_v2.SyncV2Error, match="staging"):
        archive_sync_v2.import_candidate_evidence_page(
            receiver.engine, archive_sync_v2.encode_page(tampered_manifest, second_rows)
        )


class _V2Remote:
    def __init__(self, producer, *, fail_stream=""):
        self.producer = producer
        self.fail_stream = fail_stream
        self.requested_streams = []
        self.requested_params = []

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
            if stream == self.fail_stream:
                return httpx.Response(400, json={"detail": "boom"})
            params = dict(request.url.params)
            self.requested_params.append((stream, params))
            return httpx.Response(
                200,
                text=archive_sync_v2.export_page(
                    self.producer.engine,
                    stream,
                    snapshot=params.get("snapshot", ""),
                    since=params.get("since", ""),
                    after=params.get("after", ""),
                    limit=int(params.get("limit", 1000)),
                ),
            )
        if path == "/api/archive/v2/presence":
            payload = json.loads(request.content)
            identities = payload["identities"]
            return httpx.Response(200, json={
                "schema_version": archive_sync_v2.SCHEMA_VERSION,
                "capability": archive_sync_v2.AUTHORITATIVE_PRESENCE_CAPABILITY,
                "authority_id": archive_sync_v2.producer_authority_id(
                    self.producer.engine
                ),
                "stream": payload["stream"],
                "requested": identities,
                "present": archive_sync_v2.authority_present_identities(
                    self.producer.engine,
                    payload["stream"],
                    identities,
                ),
            })
        if path == "/api/archive/v2/candidate-evidence.jsonl":
            return httpx.Response(200, json={"status": "success", "inserted": 0, "skipped": 0})
        return httpx.Response(404)


def test_probe_v2_returns_remote_authority(tmp_path):
    producer = _sink(tmp_path, "producer-probe-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(TaxonomyVersionRecord(
            version=1,
            status="active",
            created_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer)
    result = asyncio.run(remote_sync_service.probe(
        "https://remote.test", "admin", "secret",
        protocol="v2", transport=httpx.MockTransport(remote.handler),
    ))
    assert result["protocol"] == "v2"
    assert result["authority_id"] == archive_sync_v2.producer_authority_id(producer.engine)
    assert archive_sync_v2.TRANSACTION_REVISION_CAPABILITY in result["capabilities"]
    assert remote.requested_streams == ["sources", "taxonomy"]
    assert result["taxonomy_ready"] is True


def test_probe_rejects_unpublished_taxonomy_before_consumer_transition(tmp_path):
    producer = _sink(tmp_path, "producer-unpublished-taxonomy.db")
    consumer = _sink(tmp_path, "consumer-before-unpublished-probe.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(CmsTagRecord(
            code="draft-catalog", kind="topic", name_en="Draft Catalog",
            normalized_name="draft catalog", status="active", taxonomy_version=0,
            created_at="2026-09-01T00:00:00+00:00",
            updated_at="2026-09-01T00:00:00+00:00",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_article("must-survive-preflight"))
        session.commit()

    remote = _V2Remote(producer)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="尚未人工发布"):
        asyncio.run(remote_sync_service.probe(
            "https://remote.test", "admin", "secret",
            protocol="v2", transport=httpx.MockTransport(remote.handler),
        ))
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "must-survive-preflight") is not None
        assert session.get(AppSettingRecord, "remote_sync:v2_consumer_mode") is None


def test_new_consumer_rejects_old_v2_peer_during_probe_and_pull(tmp_path):
    producer = _sink(tmp_path, "producer-old-v2.db")
    consumer = _sink(tmp_path, "consumer-new-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.commit()

    class OldV2Remote(_V2Remote):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                response.status_code == 200
                and request.url.path.endswith("/sources.jsonl")
            ):
                lines = response.text.splitlines()
                manifest = json.loads(lines[0])
                manifest.pop("capabilities", None)
                return httpx.Response(
                    200,
                    text="\n".join([json.dumps(manifest), *lines[1:]]) + "\n",
                )
            return response

    remote = OldV2Remote(producer)
    transport = httpx.MockTransport(remote.handler)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="capability"):
        asyncio.run(remote_sync_service.probe(
            "https://remote.test",
            "admin",
            "secret",
            protocol="v2",
            transport=transport,
        ))
    with pytest.raises(remote_sync_service.RemoteSyncError, match="capability"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "old-peer-media",
            transport=transport,
        ))
    with Session(consumer.engine) as session:
        assert session.exec(select(SourceConfigRecord)).all() == []


def test_pull_rejects_authority_changed_after_probe_before_first_write(tmp_path):
    producer = _sink(tmp_path, "producer-authority-b.db")
    consumer = _sink(tmp_path, "consumer-prepared-authority-a.db")
    with Session(producer.engine) as session:
        session.add(_source("authority-b-source"))
        session.commit()
    with Session(consumer.engine) as session:
        remote_sync_service.prepare_transaction_revision_consumer(
            session,
            base_url="https://remote.test",
            username="admin",
            authority_id="prepared-authority-a",
            schema_version=archive_sync_v2.SCHEMA_VERSION,
            prepared_at="2026-09-04T00:00:00+00:00",
        )
        session.commit()

    remote = _V2Remote(producer)
    with pytest.raises(remote_sync_service.RemoteSyncError, match="连接预检不一致"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "authority-mismatch-media",
            expected_authority_id="prepared-authority-a",
            transport=httpx.MockTransport(remote.handler),
        ))
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "authority-b-source") is None
        producer_authority = archive_sync_v2.producer_authority_id(producer.engine)
        assert session.exec(select(ArchiveSyncEntityStateRecord).where(
            ArchiveSyncEntityStateRecord.authority_id == producer_authority
        )).all() == []
        state = json.loads(session.get(
            AppSettingRecord, remote_sync_service.REMOTE_SYNC_STATE_KEY
        ).value)
        assert state["targets"]["https://remote.test"]["v2_authority_id"] == "prepared-authority-a"


def test_new_producer_rejects_wall_clock_snapshot_and_invalid_continuation(tmp_path):
    producer = _sink(tmp_path, "producer-fail-closed-v2.db")
    with Session(producer.engine) as session:
        session.add_all([_source("rss_a"), _source("rss_b")])
        session.commit()

    with pytest.raises(archive_sync_v2.SyncV2Error, match="canonical"):
        archive_sync_v2.export_page(
            producer.engine,
            "analyses",
            snapshot="2026-09-04T00:00:00+00:00",
        )

    first_raw = archive_sync_v2.export_page(
        producer.engine,
        "sources",
        limit=1,
    )
    first, _rows = archive_sync_v2.parse_page(
        first_raw,
        expected_stream="sources",
    )
    assert first["complete"] is False
    with pytest.raises(archive_sync_v2.SyncV2Error, match="snapshot is required"):
        archive_sync_v2.export_page(
            producer.engine,
            "sources",
            after=first["next_cursor"],
            limit=1,
        )

    with pytest.raises(archive_sync_v2.SyncV2Error, match="canonical"):
        archive_sync_v2.export_page(
            producer.engine,
            "articles",
            snapshot=first["snapshot"],
            after=archive_sync_v2._encode_cursor("01", "article"),
            limit=1,
        )


def test_remote_pull_v2_end_to_end_and_terminal_state_is_last(tmp_path):
    producer = _sink(tmp_path, "producer-remote-v2.db")
    consumer = _sink(tmp_path, "consumer-remote-v2.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", last_success_at="2026-09-02T03:00:00+00:00",
            total_runs=1, success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source())
        matching = _article(content="stale receiver body")
        receiver_only = _article("receiver-only", content="not present on authority")
        legacy_orphan = _article("legacy-orphan", content="v1 row without source metadata")
        legacy_orphan.source_id = "legacy-missing-source"
        session.add_all([matching, receiver_only, legacy_orphan])
        session.flush()
        session.add_all([
            ArticleAnalysisRecord(
                article_id=matching.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=3.0,
                content_hash=compute_content_hash(matching),
                created_at="2026-09-01T00:00:00+00:00",
                updated_at="2026-09-01T00:00:00+00:00",
            ),
            ArticleAnalysisRecord(
                article_id=legacy_orphan.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=2.0,
                content_hash=compute_content_hash(legacy_orphan),
                created_at="2026-09-01T00:00:00+00:00",
                updated_at="2026-09-01T00:00:00+00:00",
            ),
        ])
        session.commit()
    remote = _V2Remote(producer)
    completed = []
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media",
        on_stream_complete=lambda stream, checkpoint: completed.append(stream),
        transport=httpx.MockTransport(remote.handler),
    ))
    assert completed == list(remote_sync_service.V2_STREAM_ORDER)
    assert completed[-1] == "source_states"
    first_params = {stream: params for stream, params in remote.requested_params}
    assert "snapshot" not in first_params["sources"]
    generation = result["streams"]["sources"]["snapshot"]
    assert all(
        first_params[stream]["snapshot"] == generation
        for stream in ("articles", "analyses", "media", "source_states")
    )
    assert result["candidate_evidence"]["status"] == "success"
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-1").analysis_authority_id == result["authority_id"]
        assert session.get(ArticleAnalysisRecord, "article-1") is None
        assert session.get(ArticleRecord, "receiver-only") is None
        assert session.get(ArticleRecord, "legacy-orphan") is None
        assert session.get(ArticleAnalysisRecord, "legacy-orphan") is None
        assert session.get(SourceStateRecord, "rss_platform").status == "healthy"
    assert result["streams"]["articles"]["pruned"] == 2
    assert result["streams"]["analyses"]["pruned"] == 1

    # Independent completed snapshots become exclusive lower watermarks; an
    # unchanged second run is genuinely incremental, not a hidden full replay.
    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media",
        checkpoints=result["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert all(item["count"] == 0 for item in second["streams"].values())


def test_full_prune_presence_protects_row_updated_between_pages(tmp_path):
    producer = _sink(tmp_path, "producer-presence-race.db")
    consumer = _sink(tmp_path, "consumer-presence-race.db")
    now = "2026-09-02T00:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(_source())
        session.add_all([
            _article("a-first", content="first"),
            _article("z-race", content="old authority body"),
        ])
        session.commit()
    with Session(consumer.engine) as session:
        session.add(_source())
        session.add(_article("z-race", content="old receiver body"))
        tag = CmsTagRecord(
            code="manual-local", kind="topic", name_en="Manual Local",
            normalized_name="manual local", status="active", taxonomy_version=0,
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id="z-race", tag_id=int(tag.id), tag_kind="topic",
            is_primary=True, relevance=1, assignment_source="manual",
            prompt_version="", taxonomy_version=0, created_at=now, updated_at=now,
        ))
        session.commit()

    class UpdatingBetweenPagesRemote(_V2Remote):
        updated = False

        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                not self.updated
                and request.url.path.endswith("/articles.jsonl")
                and "after" not in request.url.params
            ):
                with Session(self.producer.engine) as session:
                    raced = session.get(ArticleRecord, "z-race")
                    raced.content = "new authority body"
                    raced.fetched_date = "2026-09-03T01:00:00+00:00"
                    raced.archive_updated_at = "2026-09-03T00:00:00+00:00"
                    session.add(raced)
                    session.commit()
                self.updated = True
            return response

    remote = UpdatingBetweenPagesRemote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "presence-media",
        page_size=1,
        transport=httpx.MockTransport(remote.handler),
    ))
    assert first["streams"]["articles"]["pruned"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "z-race").content == "old receiver body"
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "z-race",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "presence-media",
        page_size=1,
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "z-race").content == "new authority body"
        assert session.get(ArticleRecord, "z-race").fetched_date == "2026-09-03T01:00:00+00:00"
        assert session.exec(select(ArticleTagAssignmentRecord).where(
            ArticleTagAssignmentRecord.article_id == "z-race",
            ArticleTagAssignmentRecord.assignment_source == "manual",
        )).one() is not None


def test_completed_checkpoints_do_not_hide_later_article_and_matching_analysis(tmp_path):
    producer = _sink(tmp_path, "producer-after-checkpoint.db")
    consumer = _sink(tmp_path, "consumer-after-checkpoint.db")
    with Session(producer.engine) as session:
        session.add(_source())
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            created_at="2026-09-02T02:00:00+00:00",
            updated_at="2026-09-02T02:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-after-checkpoint-media",
        transport=httpx.MockTransport(remote.handler),
    ))

    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        old_revision = article.archive_updated_at
        article.content = "Body B"
        session.add(article)
        assert queue_article_analysis(
            session,
            article.id,
            force=True,
            now=dt.datetime.now(dt.timezone.utc),
        ) == "invalidated"
        session.commit()
        session.refresh(article)
        assert article.archive_updated_at > old_revision

    # Complete the newly queued result only after the content mutation has
    # committed, as the worker would. It must match the article hash that the
    # consumer receives earlier in the same v2 run.
    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, article.id)
        analysis.status = "succeeded"
        analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.0
        analysis.content_hash = compute_content_hash(article)
        analysis.updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        session.add(analysis)
        session.commit()

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-after-checkpoint-media",
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    assert second["streams"]["analyses"]["count"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        analysis = session.get(ArticleAnalysisRecord, article.id)
        assert article.content == "Body B"
        assert analysis.status == "succeeded"
        assert analysis.quality_score == 9.0
        assert analysis.content_hash == compute_content_hash(article)


def test_analysis_update_in_same_wall_clock_second_gets_new_transaction_revision(tmp_path):
    producer = _sink(tmp_path, "producer-subsecond-analysis.db")
    with Session(producer.engine) as session:
        session.add(_source())
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            analyzed_at="2026-09-04T12:00:00.400000+00:00",
            created_at="2026-09-04T12:00:00.400000+00:00",
            updated_at="2026-09-04T12:00:00.400000+00:00",
        ))
        session.commit()

    first_raw = archive_sync_v2.export_page(producer.engine, "analyses")
    first_manifest, first_rows = archive_sync_v2.parse_page(
        first_raw, expected_stream="analyses"
    )
    assert len(first_rows) == 1

    with Session(producer.engine) as session:
        outcome = queue_article_analysis(
            session,
            "article-1",
            force=True,
            now=dt.datetime.fromisoformat("2026-09-04T12:00:00.700000+00:00"),
        )
        assert outcome == "invalidated"
        session.commit()
        assert session.get(ArticleAnalysisRecord, "article-1").updated_at == (
            "2026-09-04T12:00:00.700000+00:00"
        )

    second_raw = archive_sync_v2.export_page(
        producer.engine,
        "analyses",
        since=first_manifest["snapshot"],
    )
    second_manifest, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="analyses"
    )
    assert int(second_manifest["snapshot"]) > int(first_manifest["snapshot"])
    assert len(second_rows) == 1
    assert int(second_rows[0]["revision"]) > int(first_manifest["snapshot"])
    assert second_rows[0]["payload"]["status"] == "pending"


def test_preflush_commit_is_replayed_and_rolled_back_revision_never_escapes(tmp_path):
    sink = _sink(tmp_path, "preflush-rollback.db")
    with Session(sink.engine) as session:
        session.add(_article())
        session.commit()

    baseline_raw = archive_sync_v2.export_page(sink.engine, "articles")
    baseline, _ = archive_sync_v2.parse_page(
        baseline_raw, expected_stream="articles"
    )

    with Session(sink.engine) as writer:
        article = writer.get(ArticleRecord, "article-1")
        article.content = "assigned before export, not flushed"
        writer.add(article)

        # Assigning ORM state no longer assigns a wall-clock watermark. Until
        # flush/commit, another connection sees the old transaction revision.
        interim_raw = archive_sync_v2.export_page(
            sink.engine, "articles", since=baseline["snapshot"]
        )
        interim, interim_rows = archive_sync_v2.parse_page(
            interim_raw, expected_stream="articles"
        )
        assert interim_rows == []
        assert interim["snapshot"] == baseline["snapshot"]
        writer.commit()

    committed_raw = archive_sync_v2.export_page(
        sink.engine, "articles", since=interim["snapshot"]
    )
    committed, committed_rows = archive_sync_v2.parse_page(
        committed_raw, expected_stream="articles"
    )
    assert [row["identity"] for row in committed_rows] == ["article-1"]
    assert int(committed_rows[0]["revision"]) > int(interim["snapshot"])

    with Session(sink.engine) as writer:
        article = writer.get(ArticleRecord, "article-1")
        article.content = "this mutation rolls back"
        writer.add(article)
        writer.flush()
        transient_revision = writer.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        ).revision
        assert transient_revision > int(committed["snapshot"])
        writer.rollback()

    after_rollback_raw = archive_sync_v2.export_page(
        sink.engine, "articles", since=committed["snapshot"]
    )
    after_rollback, after_rollback_rows = archive_sync_v2.parse_page(
        after_rollback_raw, expected_stream="articles"
    )
    assert after_rollback_rows == []
    assert after_rollback["snapshot"] == committed["snapshot"]
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "article-1").content == (
            "assigned before export, not flushed"
        )


def test_article_parent_revision_precedes_matching_analysis_child_revision(tmp_path):
    sink = _sink(tmp_path, "parent-child-revision.db")
    with Session(sink.engine) as session:
        article = _article(content="Body A")
        session.add(article)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=7.0,
                content_hash=compute_content_hash(article),
                created_at="2026-09-04T12:00:00+00:00",
                updated_at="2026-09-04T12:00:00+00:00",
            )
        )
        session.commit()

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Body B"
        session.add(article)
        assert queue_article_analysis(session, article.id, force=True) == "invalidated"
        analysis = session.get(ArticleAnalysisRecord, article.id)
        analysis.status = "succeeded"
        analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.0
        analysis.content_hash = compute_content_hash(article)
        session.add(analysis)
        session.commit()

    with Session(sink.engine) as session:
        article_state = session.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        )
        analysis_state = session.get(
            ArchiveSyncEntityStateRecord, ("analyses", "article-1")
        )
        clock = session.get(ArchiveSyncClockRecord, 1)
        assert article_state.operation == analysis_state.operation == "upsert"
        assert article_state.revision < analysis_state.revision <= clock.revision
        snapshot = str(clock.revision)

    article_manifest, article_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(sink.engine, "articles", snapshot=snapshot),
        expected_stream="articles",
    )
    analysis_manifest, analysis_rows = archive_sync_v2.parse_page(
        archive_sync_v2.export_page(sink.engine, "analyses", snapshot=snapshot),
        expected_stream="analyses",
    )
    assert article_manifest["snapshot"] == analysis_manifest["snapshot"] == snapshot
    assert int(article_rows[0]["revision"]) < int(analysis_rows[0]["revision"])
    assert analysis_rows[0]["payload"]["content_hash"] == compute_content_hash(
        _article(content="Body B")
    )


def test_integer_revision_keyset_orders_nine_before_ten(tmp_path):
    sink = _sink(tmp_path, "numeric-keyset.db")
    with Session(sink.engine) as session:
        for number in range(1, 13):
            session.add(_source(f"rss_{number:02d}"))
            session.flush()
        session.commit()

    first_raw = archive_sync_v2.export_page(sink.engine, "sources", limit=9)
    first, first_rows = archive_sync_v2.parse_page(
        first_raw, expected_stream="sources"
    )
    assert [int(row["revision"]) for row in first_rows] == list(range(1, 10))
    assert first["complete"] is False

    second_raw = archive_sync_v2.export_page(
        sink.engine,
        "sources",
        snapshot=first["snapshot"],
        after=first["next_cursor"],
        limit=9,
    )
    second, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="sources"
    )
    assert second["snapshot"] == first["snapshot"]
    assert [int(row["revision"]) for row in second_rows] == [10, 11, 12]


def test_tombstone_wins_when_an_older_upsert_page_is_replayed(tmp_path):
    producer = _sink(tmp_path, "producer-tombstone-replay.db")
    consumer = _sink(tmp_path, "consumer-tombstone-replay.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.commit()

    old_page = archive_sync_v2.export_page(producer.engine, "articles")
    old_manifest, old_rows = archive_sync_v2.parse_page(
        old_page, expected_stream="articles"
    )
    assert old_rows[0]["operation"] == "upsert"
    archive_sync_v2.import_page(consumer.engine, old_page, expected_stream="articles")

    with Session(producer.engine) as session:
        session.delete(session.get(ArticleRecord, "article-1"))
        session.commit()
    tombstone_page = archive_sync_v2.export_page(
        producer.engine, "articles", since=old_manifest["snapshot"]
    )
    _tombstone_manifest, tombstone_rows = archive_sync_v2.parse_page(
        tombstone_page, expected_stream="articles"
    )
    assert tombstone_rows[0]["operation"] == "tombstone"
    assert int(tombstone_rows[0]["revision"]) > int(old_rows[0]["revision"])
    archive_sync_v2.import_page(
        consumer.engine, tombstone_page, expected_stream="articles"
    )
    replay = archive_sync_v2.import_page(
        consumer.engine, old_page, expected_stream="articles"
    )
    assert replay["inserted"] == replay["updated"] == replay["deleted"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-1") is None
        state = session.get(
            ArchiveSyncEntityStateRecord, ("articles", "article-1")
        )
        assert state.operation == "tombstone"
        assert state.revision == int(tombstone_rows[0]["revision"])


def test_media_delta_is_driven_by_current_article_reference_revision(tmp_path):
    sink = _sink(tmp_path, "media-reference-revision.db")
    url = "https://img.test/reference-driven.jpg"
    key = hashlib.sha256(url.encode()).hexdigest()
    with Session(sink.engine) as session:
        session.add(
            MediaAssetRecord(
                url_hash=key,
                url=url,
                status="cached",
                content_hash="a" * 64,
                mime="image/jpeg",
                ext=".jpg",
                size_bytes=10,
                created_at="2026-09-01T00:00:00",
                updated_at="2026-09-01T00:00:00",
            )
        )
        session.commit()
    with Session(sink.engine) as session:
        session.add(_article(content="Body without an image"))
        session.commit()

    baseline_raw = archive_sync_v2.export_page(sink.engine, "media")
    baseline, baseline_rows = archive_sync_v2.parse_page(
        baseline_raw, expected_stream="media"
    )
    assert baseline_rows == []

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = f"![new reference]({url})"
        session.add(article)
        session.commit()
        article_revision = session.get(
            ArchiveSyncEntityStateRecord, ("articles", article.id)
        ).revision
        media_revision = session.get(
            ArchiveSyncEntityStateRecord, ("media", key)
        ).revision
        assert article_revision > media_revision

    referenced_raw = archive_sync_v2.export_page(
        sink.engine, "media", since=baseline["snapshot"]
    )
    referenced, referenced_rows = archive_sync_v2.parse_page(
        referenced_raw, expected_stream="media"
    )
    assert [row["identity"] for row in referenced_rows] == [key]
    assert int(referenced_rows[0]["revision"]) == article_revision

    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "article-1")
        article.content = "Reference removed"
        media = session.get(MediaAssetRecord, key)
        media.content_hash = "b" * 64
        session.add(article)
        session.add(media)
        session.commit()
    unreferenced_raw = archive_sync_v2.export_page(
        sink.engine, "media", since=referenced["snapshot"]
    )
    _unreferenced, unreferenced_rows = archive_sync_v2.parse_page(
        unreferenced_raw, expected_stream="media"
    )
    assert unreferenced_rows == []


@pytest.mark.parametrize(
    ("stream", "identity"),
    [
        ("sources", "rss_platform"),
        ("articles", "article-1"),
        ("analyses", "article-1"),
        ("media", hashlib.sha256(b"https://img.test/commit-race.jpg").hexdigest()),
        ("source_states", "rss_platform"),
    ],
)
def test_uncommitted_writer_is_replayed_after_its_commit_revision(
    tmp_path, stream, identity
):
    """A concurrent uncommitted write lands strictly after the visible snapshot."""

    sink = _sink(tmp_path, f"commit-barrier-{stream}.db")
    media_url = "https://img.test/commit-race.jpg"
    with Session(sink.engine) as session:
        session.add(_source(updated_at="2020-01-01T00:00:00"))
        article = _article(
            content=f"![image]({media_url})",
            archive_updated_at="2020-01-01T00:00:00",
        )
        session.add(article)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=7.0,
            content_hash=compute_content_hash(article),
            created_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        ))
        session.add(MediaAssetRecord(
            url_hash=hashlib.sha256(media_url.encode()).hexdigest(),
            url=media_url,
            status="cached",
            content_hash="a" * 64,
            mime="image/jpeg",
            ext=".jpg",
            size_bytes=10,
            created_at="2020-01-01T00:00:00",
            updated_at="2020-01-01T00:00:00",
        ))
        session.add(SourceStateRecord(
            source_id="rss_platform",
            fetcher_id="rss_platform",
            status="healthy",
            total_runs=1,
            success_runs=1,
            updated_at="2020-01-01T00:00:00",
        ))
        session.commit()

    first_raw = archive_sync_v2.export_page(sink.engine, stream)
    first_manifest, _ = archive_sync_v2.parse_page(first_raw, expected_stream=stream)
    writer_ready = threading.Event()
    allow_commit = threading.Event()
    writer_errors = []

    def write_before_snapshot_but_commit_after_export_starts():
        connection = sink.engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(bind=connection) as session:
                if stream == "sources":
                    record = session.get(SourceConfigRecord, identity)
                    record.name = "committed source"
                elif stream == "articles":
                    record = session.get(ArticleRecord, identity)
                    record.content = "committed article body"
                elif stream == "analyses":
                    record = session.get(ArticleAnalysisRecord, identity)
                    record.quality_score = 8.0
                elif stream == "media":
                    record = session.get(MediaAssetRecord, identity)
                    record.content_hash = "b" * 64
                else:
                    record = session.get(SourceStateRecord, identity)
                    record.total_runs = 2
                session.add(record)
                session.flush()
                writer_ready.set()
                if not allow_commit.wait(timeout=5):
                    raise AssertionError("test did not release the writer")
            connection.commit()
        except Exception as exc:  # pragma: no cover - surfaced in the main thread
            writer_errors.append(exc)
            connection.rollback()
        finally:
            connection.close()

    writer = threading.Thread(target=write_before_snapshot_but_commit_after_export_starts)
    writer.start()
    assert writer_ready.wait(timeout=5)
    # The exporter sees neither the uncommitted entity state nor its clock tick.
    interim_raw = archive_sync_v2.export_page(
        sink.engine,
        stream,
        since=first_manifest["snapshot"],
    )
    interim_manifest, interim_rows = archive_sync_v2.parse_page(
        interim_raw,
        expected_stream=stream,
        requested_since=first_manifest["snapshot"],
    )
    assert interim_rows == []
    assert interim_manifest["snapshot"] == first_manifest["snapshot"]

    allow_commit.set()
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert writer_errors == []

    committed_raw = archive_sync_v2.export_page(
        sink.engine,
        stream,
        since=interim_manifest["snapshot"],
    )
    committed_manifest, committed_rows = archive_sync_v2.parse_page(
        committed_raw,
        expected_stream=stream,
        requested_since=interim_manifest["snapshot"],
    )
    assert int(committed_manifest["snapshot"]) > int(interim_manifest["snapshot"])
    assert [row["identity"] for row in committed_rows] == [identity]


def test_taxonomy_import_rejects_out_of_order_and_same_revision_rewrite(tmp_path):
    producer = _sink(tmp_path, "producer-taxonomy-order.db")
    consumer = _sink(tmp_path, "consumer-taxonomy-order.db")
    now = "2026-09-02T02:00:00+00:00"
    with Session(producer.engine) as session:
        session.add(CmsTagRecord(
            code="stable-tag",
            kind="topic",
            name_zh="稳定标签",
            normalized_name="稳定标签",
            status="active",
            taxonomy_version=1,
            created_at=now,
            updated_at=now,
        ))
        session.add(TaxonomyVersionRecord(version=1, status="active", created_at=now))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "taxonomy")
    first = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    replay = archive_sync_v2.import_page(consumer.engine, raw, expected_stream="taxonomy")
    assert first["inserted"] == 1
    assert replay["inserted"] == replay["updated"] == 0

    no_op = archive_sync_v2.export_page(producer.engine, "taxonomy", since="1")
    no_op_result = archive_sync_v2.import_page(
        consumer.engine, no_op, expected_stream="taxonomy"
    )
    assert no_op_result["inserted"] == no_op_result["updated"] == 0

    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="taxonomy")
    rewritten_rows = json.loads(json.dumps(rows))
    rewritten_rows[0]["payload"]["name_zh"] = "同版本偷改"
    rewritten_rows[0]["checksum"] = archive_sync_v2.checksum(
        rewritten_rows[0]["payload"]
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="does not match"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(manifest, rewritten_rows),
            expected_stream="taxonomy",
        )

    lower_manifest = {**manifest, "snapshot": "0"}
    lower_rows = json.loads(json.dumps(rows))
    for row in lower_rows:
        row["revision"] = "0"
    lower_manifest["next_cursor"] = archive_sync_v2._encode_cursor(
        "0", lower_rows[-1]["identity"]
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="backwards"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(lower_manifest, lower_rows),
            expected_stream="taxonomy",
        )

    higher_noop = {
        **manifest,
        "snapshot": "2",
        "since": "1",
        "next_cursor": "",
        "full_snapshot": False,
        "count": 0,
    }
    with pytest.raises(archive_sync_v2.SyncV2Error, match="requires a full snapshot"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(higher_noop, []),
            expected_stream="taxonomy",
        )

    with Session(consumer.engine) as session:
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "stable-tag")).one()
        assert tag.name_zh == "稳定标签"


def test_remote_pull_failure_does_not_publish_source_readiness(tmp_path):
    producer = _sink(tmp_path, "producer-failed-v2.db")
    consumer = _sink(tmp_path, "consumer-failed-v2.db")
    with Session(producer.engine) as session:
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00+00:00", total_runs=1,
            success_runs=1, updated_at="2026-09-02T03:00:00+00:00",
        ))
        session.commit()
    remote = _V2Remote(producer, fail_stream="analyses")
    with pytest.raises(remote_sync_service.RemoteSyncError, match="analyses"):
        asyncio.run(remote_sync_service.run_pull_v2(
            engine=consumer.engine,
            base_url="https://remote.test",
            username="admin",
            password="secret",
            media_root=tmp_path / "consumer-media-failed",
            transport=httpx.MockTransport(remote.handler),
        ))
    assert "source_states" not in remote.requested_streams
    assert remote.requested_streams.count("analyses") == 1
    with Session(consumer.engine) as session:
        assert session.get(SourceStateRecord, "rss_platform") is None


def test_source_readiness_cannot_advance_past_shared_generation_revision(tmp_path):
    producer = _sink(tmp_path, "producer-readiness-cutoff.db")
    consumer = _sink(tmp_path, "consumer-readiness-cutoff.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.add(SourceStateRecord(
            source_id="rss_platform", fetcher_id="rss_platform", status="healthy",
            last_completed_at="2026-09-02T03:00:00", total_runs=1,
            success_runs=1, updated_at="2026-09-02T03:00:00",
        ))
        session.commit()

    class MidRunFetchRemote(_V2Remote):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if request.url.path.endswith("/articles.jsonl") and response.status_code == 200:
                manifest = json.loads(response.text.splitlines()[0])
                with Session(self.producer.engine) as session:
                    session.add(_article(
                        "arrived-after-cutoff",
                    ))
                    state = session.get(SourceStateRecord, "rss_platform")
                    state.last_completed_at = "2026-09-03T00:00:00"
                    state.total_runs = 2
                    session.add(state)
                    session.commit()
            return response

    remote = MidRunFetchRemote(producer)
    result = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-media-cutoff",
        transport=httpx.MockTransport(remote.handler),
    ))

    assert result["streams"]["source_states"]["snapshot"] == result["streams"]["articles"]["snapshot"]
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "article-1") is not None
        assert session.get(ArticleRecord, "arrived-after-cutoff") is None
        assert session.get(SourceStateRecord, "rss_platform") is None


def test_article_and_analysis_committed_after_generation_wait_for_next_pull(tmp_path):
    producer = _sink(tmp_path, "producer-analysis-generation-race.db")
    consumer = _sink(tmp_path, "consumer-analysis-generation-race.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_article())
        session.commit()

    class AnalysisRaceRemote(_V2Remote):
        created = False

        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            if (
                not self.created
                and request.url.path.endswith("/articles.jsonl")
                and response.status_code == 200
            ):
                with Session(self.producer.engine) as session:
                    article = _article("arrived-with-analysis-after-cutoff")
                    session.add(article)
                    session.flush()
                    session.add(ArticleAnalysisRecord(
                        article_id=article.id,
                        status="succeeded",
                        tagging_status="succeeded",
                        quality_score=9.1,
                        content_hash=compute_content_hash(article),
                        analyzed_at="2026-09-04T12:00:00+00:00",
                        created_at="2026-09-04T12:00:00+00:00",
                        updated_at="2026-09-04T12:00:00+00:00",
                    ))
                    session.commit()
                self.created = True
            return response

    remote = AnalysisRaceRemote(producer)
    first = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-analysis-generation-media",
        transport=httpx.MockTransport(remote.handler),
    ))
    first_params = {stream: params for stream, params in remote.requested_params}
    generation = first["streams"]["sources"]["snapshot"]
    assert first_params["articles"]["snapshot"] == generation
    assert first_params["analyses"]["snapshot"] == generation
    assert first["streams"]["analyses"]["count"] == 0
    with Session(consumer.engine) as session:
        assert session.get(ArticleRecord, "arrived-with-analysis-after-cutoff") is None
        assert session.get(
            ArticleAnalysisRecord, "arrived-with-analysis-after-cutoff"
        ) is None

    second = asyncio.run(remote_sync_service.run_pull_v2(
        engine=consumer.engine,
        base_url="https://remote.test",
        username="admin",
        password="secret",
        media_root=tmp_path / "consumer-analysis-generation-media",
        checkpoints=first["streams"],
        transport=httpx.MockTransport(remote.handler),
    ))
    assert second["streams"]["articles"]["count"] == 1
    assert second["streams"]["analyses"]["count"] == 1
    with Session(consumer.engine) as session:
        article = session.get(ArticleRecord, "arrived-with-analysis-after-cutoff")
        analysis = session.get(
            ArticleAnalysisRecord, "arrived-with-analysis-after-cutoff"
        )
        assert article is not None
        assert analysis is not None and analysis.quality_score == 9.1
