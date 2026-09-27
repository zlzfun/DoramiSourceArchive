"""Curated podcast catalog validation and import contract."""

from dataclasses import replace
from types import SimpleNamespace
import asyncio
import hashlib
import json

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

from api.collection_planning import build_collection_job_items
from fetchers.impl.podcast_rss_fetcher import GenericPodcastRssFetcher
from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleShareRecord,
    CollectionJobRecord,
    FetchRunRecord,
    PodcastProcessingRecord,
    PodcastSourceMediaSnapshotRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    ReaderArticleReadStateRecord,
    ReaderFavoriteRecord,
    ReaderReadCursorRecord,
    ReaderReadRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserRecord,
)
from services import accounts as accounts_service
from services import archive_sync_v2
from services import podcast_premium_guides as podcast_premium_guide_service
from services import user_sources as user_sources_service
from services.collection_nodes import build_source_fetch_params
from services.podcast_catalog import (
    AI_NATIVE_DEV_COLLECTION_JOB_NAME,
    AI_NATIVE_DEV_SOURCE_ID,
    PODCAST_CATALOG,
    catalog_by_id,
    ensure_default_podcast_sources,
    import_podcast_catalog,
    list_podcast_catalog,
)
from storage.impl.db_storage import DatabaseStorage


STAMP = "2026-09-24T00:00:00+00:00"


def test_catalog_has_37_stable_unique_entries_and_one_explicit_blocker():
    assert len(PODCAST_CATALOG) == 37
    assert len(catalog_by_id()) == 37
    assert all(item.source_id.startswith("podcast_") for item in PODCAST_CATALOG)
    assert len({item.feed_url for item in PODCAST_CATALOG}) == 37
    assert all(item.feed_url.startswith("https://") for item in PODCAST_CATALOG)
    assert {item.ingest_status for item in PODCAST_CATALOG} == {"ready", "blocked"}
    blocked = [item for item in PODCAST_CATALOG if item.ingest_status == "blocked"]
    assert [item.source_id for item in blocked] == ["podcast_voices_from_darpa"]
    assert "TLS" in blocked[0].status_note
    by_id = catalog_by_id()
    tessl = by_id[AI_NATIVE_DEV_SOURCE_ID]
    assert tessl.publisher == "Tessl"
    assert tessl.source_scope == "company"
    assert tessl.launch_tier == "core"
    assert tessl.verified_at == "2026-09-24"
    assert tessl.adopt_existing_custom is True
    assert all(item.verified_at for item in PODCAST_CATALOG)
    assert (by_id["podcast_nvidia_ai"].source_scope, by_id["podcast_nvidia_ai"].provenance_tier) == (
        "company", "tier0_primary",
    )
    assert (by_id["podcast_voices_from_darpa"].source_scope, by_id["podcast_voices_from_darpa"].provenance_tier) == (
        "research_lab", "tier0_primary",
    )
    assert (by_id["podcast_dwarkesh"].source_scope, by_id["podcast_dwarkesh"].provenance_tier) == (
        "expert_commentary", "tier2_commentary",
    )
    assert (by_id["podcast_interconnects"].source_scope, by_id["podcast_interconnects"].provenance_tier) == (
        "expert_newsletter", "tier2_commentary",
    )
    assert (by_id["podcast_latent_space"].source_scope, by_id["podcast_latent_space"].provenance_tier) == (
        "ai_media", "tier1_curated",
    )


def test_default_import_creates_collectable_public_nodes_and_is_idempotent(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'catalog.db'}")
    with Session(db.engine) as session:
        result = import_podcast_catalog(session, feed_max_bytes=12_345)
        assert len(result["created"]) == 37
        assert result["updated"] == []

        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent is not None
        assert latent.source_type == "podcast"
        assert latent.fetcher_id == "generic_podcast_rss"
        assert latent.category == "incubating"
        assert latent.is_active is True
        assert latent.owner_username == ""
        assert latent.source_scope == "ai_media"
        assert latent.provenance_tier == "tier1_curated"
        assert latent.signal_strength == "high_signal"
        assert latent.noise_risk == "low_noise"
        assert latent.fetch_reliability == "stable_public"
        nvidia = session.get(SourceConfigRecord, "podcast_nvidia_ai")
        assert nvidia.source_scope == "company"
        assert nvidia.provenance_tier == "tier0_primary"
        dwarkesh = session.get(SourceConfigRecord, "podcast_dwarkesh")
        assert dwarkesh.source_scope == "expert_commentary"
        assert dwarkesh.provenance_tier == "tier2_commentary"
        extended = session.get(SourceConfigRecord, "podcast_20vc")
        assert extended.signal_strength == "medium_signal"
        assert extended.noise_risk == "medium_noise"
        assert extended.fetch_reliability == "stable_public"
        blocked = session.get(SourceConfigRecord, "podcast_voices_from_darpa")
        assert blocked.fetch_reliability == "blocked_or_fragile"
        params = json.loads(latent.params_json)
        assert params["limit"] == 20
        assert params["max_response_bytes"] == 12_345
        assert params["credentialed_private"] is False

        again = import_podcast_catalog(session)
        assert again["created"] == []
        assert len(again["skipped_existing"]) == 37

        catalog = list_podcast_catalog(session)
        assert catalog["total"] == 37
        assert catalog["ready"] == 36
        assert catalog["installed"] == 37
        assert catalog["verification_mode"] == "per_source"
        assert "verified_at" not in catalog
        assert catalog["latest_verified_at"] == "2026-09-24"
        assert "active" not in catalog
        assert all("active" not in item for item in catalog["items"])


def test_ai_native_dev_fresh_install_is_public_scheduled_and_not_subscribed(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'tessl-fresh.db'}")
    result = ensure_default_podcast_sources(db.engine)
    assert AI_NATIVE_DEV_SOURCE_ID in result["created"]
    with Session(db.engine) as session:
        source = session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID)
        assert source is not None
        assert source.owner_username == ""
        assert source.category == "incubating"
        assert json.loads(source.params_json)["catalog_verified_at"] == "2026-09-24"
        assert session.exec(select(ReaderSubscriptionRecord)).all() == []
        assert session.exec(
            select(ArticleRecord).where(ArticleRecord.source_id == AI_NATIVE_DEV_SOURCE_ID)
        ).all() == []
        job = session.exec(
            select(CollectionJobRecord).where(
                CollectionJobRecord.name == AI_NATIVE_DEV_COLLECTION_JOB_NAME
            )
        ).one()
        assert job.is_active is True
        assert json.loads(job.fetcher_ids_json) == [AI_NATIVE_DEV_SOURCE_ID]
        assert job.cron_expr

        job.is_active = False
        session.add(job)
        session.commit()
        result = import_podcast_catalog(
            session, source_ids=[AI_NATIVE_DEV_SOURCE_ID]
        )
        assert result["skipped_existing"] == [AI_NATIVE_DEV_SOURCE_ID]
        assert session.get(CollectionJobRecord, job.id).is_active is False


def test_ai_native_dev_adopts_custom_source_without_changing_episode_identity(
    tmp_path,
):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'tessl-adopt.db'}")
    old_source_id = "user_rss_3dddb2046f7f"
    episode_id = f"{old_source_id}_{hashlib.sha1(b'Buzzsprout-1').hexdigest()[:16]}"
    with Session(db.engine) as session:
        session.add(SourceConfigRecord(
            source_id=old_source_id,
            name="The AI Native Dev",
            source_type="podcast",
            url="https://rss.buzzsprout.com/2375985.rss/",
            category="user",
            fetcher_id="generic_podcast_rss",
            owner_username="alice",
            params_json='{"limit":20,"credentialed_private":false}',
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(ArticleRecord(
            id=episode_id,
            title="Existing episode",
            content_type="podcast_episode",
            source_id=old_source_id,
            source_url="https://www.buzzsprout.com/2375985/episodes/1",
            publish_date=STAMP,
            fetched_date=STAMP,
            content="show notes",
            extensions_json='{"guid":"Buzzsprout-1","duration_seconds":1800}',
        ))
        session.add(SourceStateRecord(
            source_id=old_source_id,
            fetcher_id="generic_podcast_rss",
            content_type="podcast_episode",
            status="healthy",
            last_content_id=episode_id,
            updated_at=STAMP,
        ))
        session.commit()
        session.add(ArticleAnalysisRecord(
            article_id=episode_id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=8.2,
            podcast_final_score=8.2,
            content_hash="analysis-hash",
            analysis_basis="publisher_transcript",
            transcript_artifact_id="publisher-transcript-1",
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(PodcastSourceMediaSnapshotRecord(
            id="source-media-1",
            episode_id=episode_id,
            locator_hash="a" * 64,
            content_hash="b" * 64,
            mime="audio/mpeg",
            size_bytes=1024,
            duration_seconds=60,
            created_at=STAMP,
        ))
        session.add(PodcastProcessingRecord(
            id="processing-tessl-1",
            episode_id=episode_id,
            input_fingerprint="c" * 64,
            pipeline_version="test-v1",
            policy_version="test-v1",
            requested_target="full_analysis",
            idempotency_key="processing-tessl-key",
            input_artifact_id="source-media-1",
            input_artifact_kind="source_media_snapshot",
            input_content_hash="b" * 64,
            input_language="und",
            budget_scope="test",
            budget_period="2026-09",
            budget_limit_minor=1000,
            per_run_budget_minor=100,
            eligibility_status="eligible",
            processing_status="ready",
            stage="analyze",
            queued_at=STAMP,
            updated_at=STAMP,
            finished_at=STAMP,
            created_at=STAMP,
        ))
        transcript_text = "Publisher transcript"
        transcript_hash = hashlib.sha256(transcript_text.encode()).hexdigest()
        session.add(PodcastTextArtifactRecord(
            id="publisher-transcript-1",
            episode_id=episode_id,
            kind="publisher_transcript",
            version=1,
            content_hash=transcript_hash,
            inline_text=transcript_text,
            language="en",
            provenance_json="{}",
            created_at=STAMP,
        ))
        session.flush()
        session.add(PodcastTextPublicationRecord(
            identity=f"{episode_id}:publisher_transcript",
            episode_id=episode_id,
            kind="publisher_transcript",
            artifact_id="publisher-transcript-1",
            status="published",
            published_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(ReaderSubscriptionRecord(
            owner_username="alice",
            name="Tessl",
            filters_json=json.dumps({"source_ids": old_source_id, "has_content": True}),
            token_hash="token-hash",
            token_preview="dsub_...",
            is_active=True,
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(ReaderReadCursorRecord(
            owner_username="alice",
            source_id=old_source_id,
            mark_read_before=STAMP,
            updated_at=STAMP,
        ))
        session.add(ReaderReadRecord(
            day="2026-09-24",
            username="alice",
            source_id=old_source_id,
            reads=7,
            updated_at=STAMP,
        ))
        session.add(ReaderFavoriteRecord(
            owner_username="alice", article_id=episode_id, created_at=STAMP
        ))
        session.add(ReaderArticleReadStateRecord(
            owner_username="alice",
            article_id=episode_id,
            is_read=True,
            read_at=STAMP,
        ))
        session.add(ArticleShareRecord(
            token="dshr_tessl",
            article_id=episode_id,
            owner_username="alice",
            created_at=STAMP,
        ))
        session.add(AppSettingRecord(
            key="reader_hidden_source_ids",
            value=json.dumps([old_source_id, "podcast_other"]),
        ))
        session.add(CollectionJobRecord(
            name="Legacy custom job",
            fetcher_ids_json=json.dumps([old_source_id]),
            per_fetcher_params_json=json.dumps({old_source_id: {"limit": 9}}),
            is_active=True,
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(FetchRunRecord(
            fetcher_id=old_source_id,
            status="success",
            params_json=json.dumps({"source_id": old_source_id}),
            started_at=STAMP,
            ended_at=STAMP,
        ))
        session.commit()

        result = import_podcast_catalog(
            session,
            source_ids=[AI_NATIVE_DEV_SOURCE_ID],
            feed_max_bytes=12_345,
        )
        assert result["adopted"] == [{
            "old_source_id": old_source_id,
            "new_source_id": AI_NATIVE_DEV_SOURCE_ID,
            "article_count": 1,
            "subscription_count": 1,
        }]
        assert session.get(SourceConfigRecord, old_source_id) is None
        source = session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID)
        assert source.owner_username == ""
        assert json.loads(source.params_json)["entry_id_namespace"] == old_source_id
        fetch_params = build_source_fetch_params(source)
        assert fetch_params["entry_id_namespace"] == old_source_id
        assert GenericPodcastRssFetcher()._entry_id(
            fetch_params["entry_id_namespace"], {"guid": "Buzzsprout-1"}
        ) == episode_id
        episode = session.get(ArticleRecord, episode_id)
        assert episode.source_id == AI_NATIVE_DEV_SOURCE_ID
        assert json.loads(episode.extensions_json)["premium_guide_auto_suppressed"] is True
        assert session.get(ArticleAnalysisRecord, episode_id).quality_score == 8.2
        assert session.get(PodcastProcessingRecord, "processing-tessl-1").episode_id == episode_id
        assert session.get(PodcastSourceMediaSnapshotRecord, "source-media-1").episode_id == episode_id
        assert session.get(PodcastTextArtifactRecord, "publisher-transcript-1").episode_id == episode_id
        assert session.get(
            PodcastTextPublicationRecord, f"{episode_id}:publisher_transcript"
        ).artifact_id == "publisher-transcript-1"
        assert session.get(ReaderFavoriteRecord, ("alice", episode_id)) is not None
        assert session.get(ReaderArticleReadStateRecord, ("alice", episode_id)) is not None
        assert session.exec(
            select(ArticleShareRecord).where(ArticleShareRecord.article_id == episode_id)
        ).one().token == "dshr_tessl"
        subscription = session.exec(select(ReaderSubscriptionRecord)).one()
        assert json.loads(subscription.filters_json)["source_ids"] == AI_NATIVE_DEV_SOURCE_ID
        assert session.get(ReaderReadCursorRecord, ("alice", AI_NATIVE_DEV_SOURCE_ID)) is not None
        assert session.get(ReaderReadCursorRecord, ("alice", old_source_id)) is None
        assert session.exec(
            select(ReaderReadRecord).where(ReaderReadRecord.source_id == AI_NATIVE_DEV_SOURCE_ID)
        ).one().reads == 7
        assert session.get(SourceStateRecord, AI_NATIVE_DEV_SOURCE_ID).last_content_id == episode_id
        assert json.loads(session.get(AppSettingRecord, "reader_hidden_source_ids").value) == [
            AI_NATIVE_DEV_SOURCE_ID,
            "podcast_other",
        ]
        legacy_job = session.exec(
            select(CollectionJobRecord).where(CollectionJobRecord.name == "Legacy custom job")
        ).one()
        assert json.loads(legacy_job.fetcher_ids_json) == [AI_NATIVE_DEV_SOURCE_ID]
        assert json.loads(legacy_job.per_fetcher_params_json) == {
            AI_NATIVE_DEV_SOURCE_ID: {"limit": 9}
        }
        historical_run = session.exec(select(FetchRunRecord)).one()
        assert historical_run.fetcher_id == AI_NATIVE_DEV_SOURCE_ID
        assert json.loads(historical_run.params_json)["source_id"] == old_source_id
        assert user_sources_service.admin_overview(session)["kpi"]["source_count"] == 0
        assert user_sources_service.system_feed_urls(session)[
            "https://rss.buzzsprout.com/2375985.rss"
        ]["source_id"] == AI_NATIVE_DEV_SOURCE_ID

        again = import_podcast_catalog(
            session,
            source_ids=[AI_NATIVE_DEV_SOURCE_ID],
            update_existing=True,
        )
        assert again["adopted"] == []
        assert json.loads(
            session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID).params_json
        )["entry_id_namespace"] == old_source_id
        assert session.get(ArticleRecord, episode_id).source_id == AI_NATIVE_DEV_SOURCE_ID

    assert podcast_premium_guide_service.pending_premium_guide_candidates(
        db.engine, minimum_duration_seconds=1, score_threshold=7.5
    ) == []

    sources_wire = archive_sync_v2.export_page(db.engine, "sources")
    articles_wire = archive_sync_v2.export_page(db.engine, "articles")
    podcast_texts_wire = archive_sync_v2.export_page(db.engine, "podcast_texts")
    assert old_source_id not in sources_wire
    assert "owner_username" not in sources_wire
    article_rows = [json.loads(line) for line in articles_wire.splitlines()[1:]]
    assert [row["identity"] for row in article_rows].count(episode_id) == 1
    assert article_rows[0]["payload"]["source_id"] == AI_NATIVE_DEV_SOURCE_ID
    text_rows = [json.loads(line) for line in podcast_texts_wire.splitlines()[1:]]
    assert [row["identity"] for row in text_rows] == [
        f"{episode_id}:publisher_transcript"
    ]


def test_ai_native_dev_adoption_rolls_back_every_write_on_midway_failure(
    monkeypatch, tmp_path
):
    import services.podcast_catalog as catalog_service

    db = DatabaseStorage(f"sqlite:///{tmp_path / 'tessl-rollback.db'}")
    old_source_id = "user_rss_3dddb2046f7f"
    episode_id = "legacy-episode"
    with Session(db.engine) as session:
        session.add(SourceConfigRecord(
            source_id=old_source_id,
            name="The AI Native Dev",
            source_type="podcast",
            url="https://rss.buzzsprout.com/2375985.rss",
            category="user",
            fetcher_id="generic_podcast_rss",
            owner_username="alice",
            created_at=STAMP,
            updated_at=STAMP,
        ))
        session.add(ArticleRecord(
            id=episode_id,
            title="Episode",
            content_type="podcast_episode",
            source_id=old_source_id,
            source_url="https://example.test/episode",
            publish_date=STAMP,
            fetched_date=STAMP,
        ))
        session.commit()

        def fail_after_article_write(active_session, **_kwargs):
            article = active_session.get(ArticleRecord, episode_id)
            article.source_id = AI_NATIVE_DEV_SOURCE_ID
            active_session.add(article)
            active_session.flush()
            raise RuntimeError("injected failure")

        monkeypatch.setattr(
            catalog_service, "adopt_custom_podcast_source", fail_after_article_write
        )
        with pytest.raises(RuntimeError, match="injected failure"):
            import_podcast_catalog(session, source_ids=[AI_NATIVE_DEV_SOURCE_ID])

        assert session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID) is None
        assert session.get(SourceConfigRecord, old_source_id) is not None
        assert session.get(ArticleRecord, episode_id).source_id == old_source_id
        assert session.exec(
            select(CollectionJobRecord).where(
                CollectionJobRecord.name == AI_NATIVE_DEV_COLLECTION_JOB_NAME
            )
        ).first() is None


def test_ai_native_dev_adoption_fails_closed_on_duplicate_canonical_custom_sources(
    tmp_path,
):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'tessl-ambiguous.db'}")
    with Session(db.engine) as session:
        for source_id, url in (
            ("user_rss_short", "https://rss.buzzsprout.com/2375985.rss"),
            ("user_rss_full", "HTTPS://RSS.BUZZSPROUT.COM:443/2375985.rss/"),
        ):
            session.add(SourceConfigRecord(
                source_id=source_id,
                name=source_id,
                source_type="podcast",
                url=url,
                category="user",
                fetcher_id="generic_podcast_rss",
                owner_username="alice",
                created_at=STAMP,
                updated_at=STAMP,
            ))
        session.commit()
        with pytest.raises(ValueError, match="多个自定源"):
            import_podcast_catalog(session, source_ids=[AI_NATIVE_DEV_SOURCE_ID])
        assert session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID) is None
        assert session.get(SourceConfigRecord, "user_rss_short") is not None
        assert session.get(SourceConfigRecord, "user_rss_full") is not None


@pytest.mark.parametrize("conflict", ["inactive", "retired", "remote_state"])
def test_ai_native_dev_adoption_fails_closed_on_custom_source_state_conflict(
    tmp_path, conflict
):
    db = DatabaseStorage(f"sqlite:///{tmp_path / f'tessl-{conflict}.db'}")
    source_id = "user_rss_conflicted"
    with Session(db.engine) as session:
        session.add(SourceConfigRecord(
            source_id=source_id,
            name="The AI Native Dev",
            source_type="podcast",
            url="https://rss.buzzsprout.com/2375985.rss",
            fetcher_id="generic_podcast_rss",
            owner_username="alice",
            is_active=conflict != "inactive",
            retired_at=STAMP if conflict == "retired" else None,
            created_at=STAMP,
            updated_at=STAMP,
        ))
        if conflict == "remote_state":
            session.add(SourceStateRecord(
                source_id=source_id,
                fetcher_id="generic_podcast_rss",
                authority_id="remote-node",
                updated_at=STAMP,
            ))
        session.commit()

        with pytest.raises(ValueError, match="停止自动收养"):
            import_podcast_catalog(session, source_ids=[AI_NATIVE_DEV_SOURCE_ID])
        assert session.get(SourceConfigRecord, source_id) is not None
        assert session.get(SourceConfigRecord, AI_NATIVE_DEV_SOURCE_ID) is None


def test_application_bootstrap_preserves_metadata_and_normalizes_legacy_inactive_rows(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'bootstrap.db'}")

    first = ensure_default_podcast_sources(db.engine)
    assert len(first["created"]) == 37

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent is not None
        assert latent.is_active is True
        latent.name = "本地维护的名称"
        latent.is_active = False
        params = json.loads(latent.params_json)
        params.pop("credentialed_private")
        params["local_setting"] = "preserve-me"
        latent.params_json = json.dumps(params)
        session.add(latent)
        session.commit()

    second = ensure_default_podcast_sources(db.engine)
    assert second["created"] == []
    assert len(second["skipped_existing"]) == 37

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent.name == "本地维护的名称"
        assert latent.is_active is True
        params = json.loads(latent.params_json)
        assert params["credentialed_private"] is False
        assert params["local_setting"] == "preserve-me"
        catalog = list_podcast_catalog(session)
        assert catalog["installed"] == 37
        assert "active" not in catalog


def test_catalog_public_identity_overrides_opaque_url_only_while_exactly_governed(
    tmp_path,
):
    from services.user_sources import (
        feed_url_has_credentials,
        source_is_credentialed,
    )
    from services.article_analysis import source_allows_analysis

    db = DatabaseStorage(f"sqlite:///{tmp_path / 'catalog-credential-policy.db'}")
    ensure_default_podcast_sources(db.engine)
    with Session(db.engine) as session:
        practical = session.get(SourceConfigRecord, "podcast_practical_ai")
        ted = session.get(SourceConfigRecord, "podcast_ted_ai_show")
        assert feed_url_has_credentials(practical.url) is True
        assert feed_url_has_credentials(ted.url) is True
        assert source_is_credentialed(practical) is False
        assert source_is_credentialed(ted) is False
        assert source_allows_analysis(session, practical.source_id) is True
        assert source_allows_analysis(session, ted.source_id) is True

        practical.url = f"{practical.url}?token=later-private-token"
        session.add(practical)
        session.commit()
        assert source_is_credentialed(practical) is True

        custom = SourceConfigRecord(
            source_id="user_rss_explicit_false",
            name="User feed",
            owner_username="alice",
            source_type="rss",
            url="https://feeds.example.test/abcdefghijklmnopqrstuvwxyzabcdef",
            params_json=json.dumps({"credentialed_private": False}),
            created_at="now",
            updated_at="now",
        )
        assert source_is_credentialed(custom) is True


def test_collection_job_resolves_podcast_logical_id_and_limit_override(monkeypatch, tmp_path):
    import api.app as app_module

    db = DatabaseStorage(f"sqlite:///{tmp_path / 'collection-job.db'}")
    ensure_default_podcast_sources(db.engine)
    with Session(db.engine) as session:
        source = session.get(SourceConfigRecord, "podcast_latent_space")
        session.add(source)
        session.add(CollectionJobRecord(
            id=34,
            name="Podcast collection",
            fetcher_ids_json='["podcast_latent_space"]',
            params_json='{"limit": 10}',
            per_fetcher_params_json='{"podcast_latent_space": {"limit": 3}}',
            is_active=True,
            created_at="2026-09-08T00:00:00+00:00",
            updated_at="2026-09-08T00:00:00+00:00",
        ))
        session.commit()
        job = session.get(CollectionJobRecord, 34)
        items = build_collection_job_items(job)

    assert items == [{"fetcher_id": "podcast_latent_space", "params": {"limit": 3}}]

    captured = {}

    class FakePipeline:
        async def run_task(self, fetcher, *, lineage, **params):
            captured["fetcher_type"] = type(fetcher).__name__
            captured["lineage"] = lineage
            captured["params"] = params
            return SimpleNamespace(
                fetched_count=3,
                saved_count=3,
                skipped_count=0,
                saved_content_ids=[],
                latest_content_id="",
                latest_cursor_value="",
                latest_content_publish_date="",
                latest_content_source_id="podcast_latent_space",
                latest_content_type="podcast_episode",
            )

    monkeypatch.setattr(app_module, "db_sink", db)
    monkeypatch.setattr(app_module, "pipeline", FakePipeline())
    monkeypatch.setattr(app_module, "require_podcast_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "queue_article_analysis_after_commit", lambda _ids: 0)
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda _ids: None)

    result = asyncio.run(app_module.run_collection_items(
        items,
        name="Podcast collection",
        trigger_type="scheduled",
        job_id=34,
        run_scope="saved_job",
    ))

    assert result["status"] == "success"
    assert result["results"][0]["fetcher_id"] == "podcast_latent_space"
    assert result["results"][0]["execution_fetcher_id"] == "generic_podcast_rss"
    assert captured["fetcher_type"] == "GenericPodcastRssFetcher"
    assert captured["lineage"]["job_id"] == 34
    assert captured["params"]["limit"] == 3
    assert captured["params"]["source_id"] == "podcast_latent_space"
    assert captured["params"]["feed_url"].startswith("https://")

    with Session(db.engine) as session:
        run = session.exec(select(FetchRunRecord)).one()
        assert run.fetcher_id == "podcast_latent_space"
        assert json.loads(run.params_json)["limit"] == 3

    with Session(db.engine) as session:
        source = session.get(SourceConfigRecord, "podcast_latent_space")
        source.is_active = False
        session.add(source)
        session.commit()

    rerun = asyncio.run(app_module.run_collection_items(
        items,
        name="Podcast collection",
        trigger_type="scheduled",
        job_id=34,
        run_scope="saved_job",
    ))
    assert rerun["status"] == "success"
    assert rerun["results"][0]["fetcher_id"] == "podcast_latent_space"
    with Session(db.engine) as session:
        # A stale compatibility value cannot create a second public-podcast gate.
        assert len(session.exec(select(FetchRunRecord)).all()) == 2


def test_selective_update_normalizes_public_node_and_allows_unhealthy_feed(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'selective.db'}")
    with Session(db.engine) as session:
        first = import_podcast_catalog(
            session,
            source_ids=["podcast_latent_space"],
        )
        assert first["created"] == ["podcast_latent_space"]

        row = session.get(SourceConfigRecord, "podcast_latent_space")
        assert row.is_active is True
        row.name = "Local name"
        row.is_active = False
        session.add(row)
        session.commit()

        updated = import_podcast_catalog(
            session,
            source_ids=["podcast_latent_space"],
            update_existing=True,
        )
        session.refresh(row)
        assert updated["updated"] == ["podcast_latent_space"]
        assert row.name == "Latent Space"
        assert row.is_active is True

        unhealthy = import_podcast_catalog(
            session,
            source_ids=["podcast_voices_from_darpa"],
        )
        assert unhealthy["created"] == ["podcast_voices_from_darpa"]


def test_catalog_api_is_not_shadowed_by_dynamic_source_route(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    with Session(sink.engine) as session:
        session.add(UserRecord(
            username="admin",
            password_hash=accounts_service.hash_password("admin"),
            role="admin",
            is_active=True,
            created_at="2026-09-03T00:00:00+00:00",
            updated_at="2026-09-03T00:00:00+00:00",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        catalog = client.get("/api/source-configs/podcast-catalog")
        assert catalog.status_code == 200
        assert catalog.json()["total"] == 37
        imported = client.post(
            "/api/source-configs/podcast-catalog/import",
            json={"source_ids": ["podcast_semianalysis_weekly"]},
        )
        assert imported.status_code == 200
        assert imported.json()["created"] == ["podcast_semianalysis_weekly"]

        source = client.get("/api/source-configs/podcast_semianalysis_weekly")
        assert source.status_code == 200
        assert source.json()["shape"] == "podcast"
        assert source.json()["is_active"] is True
        assert source.json()["fetch_interval_minutes"] is None

        toggle = client.post(
            "/api/source-configs/podcast_semianalysis_weekly/toggle",
            json={"is_active": False},
        )
        assert toggle.status_code == 400
        update = client.put(
            "/api/source-configs/podcast_semianalysis_weekly",
            json={"is_active": False},
        )
        assert update.status_code == 400
        interval_update = client.put(
            "/api/source-configs/podcast_semianalysis_weekly",
            json={"fetch_interval_minutes": 60},
        )
        assert interval_update.status_code == 400

        created = client.post(
            "/api/source-configs",
            json={
                "source_id": "podcast_manual_public",
                "name": "Manual public podcast",
                "source_type": "podcast",
                "url": "https://example.test/manual.xml",
                "is_active": False,
                "fetch_interval_minutes": 60,
            },
        )
        assert created.status_code == 200
        assert created.json()["is_active"] is True
        assert created.json()["fetch_interval_minutes"] is None

        fetchers = client.get("/api/fetchers")
        assert fetchers.status_code == 200
        logical_node = next(
            item for item in fetchers.json()
            if item["id"] == "podcast_semianalysis_weekly"
        )
        assert logical_node["source_config_node"] is True
        assert logical_node["execution_fetcher_id"] == "generic_podcast_rss"
        assert logical_node["parameters"] == [{
            "field": "limit",
            "label": "单次获取上限",
            "type": "number",
            "default": 20,
        }]
        assert logical_node["content_tags"]
        assert logical_node["signal_strength"] == "high_signal"
        assert logical_node["noise_risk"] == "low_noise"
        assert logical_node["fetch_reliability"] == "stable_public"

        health = client.get("/api/source-health")
        assert health.status_code == 200
        node = next(
            item for item in health.json()
            if item["source_id"] == "podcast_semianalysis_weekly"
        )
        assert node["source_config_node"] is True
        assert node["source_type"] == "podcast"
        assert node["content_type"] == "podcast_episode"
        assert node["shape"] == "podcast"
        assert "is_active" not in node
        assert node["feed_url"].startswith("https://")
        assert node["source_scope"] == "ai_media"
        assert node["content_tags"]
        assert node["signal_strength"] == "high_signal"
        assert node["noise_risk"] == "low_noise"
        assert node["fetch_reliability"] == "stable_public"
