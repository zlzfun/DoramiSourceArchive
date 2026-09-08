"""Curated podcast catalog validation and import contract."""

from dataclasses import replace
from types import SimpleNamespace
import asyncio
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api.collection_planning import build_collection_job_items
from models.db import CollectionJobRecord, FetchRunRecord, SourceConfigRecord, UserRecord
from services import accounts as accounts_service
from services.podcast_catalog import (
    PODCAST_CATALOG,
    catalog_by_id,
    ensure_default_podcast_sources,
    import_podcast_catalog,
    list_podcast_catalog,
)
from storage.impl.db_storage import DatabaseStorage


def test_catalog_has_36_stable_unique_entries_and_one_explicit_blocker():
    assert len(PODCAST_CATALOG) == 36
    assert len(catalog_by_id()) == 36
    assert all(item.source_id.startswith("podcast_") for item in PODCAST_CATALOG)
    assert len({item.feed_url for item in PODCAST_CATALOG}) == 36
    assert all(item.feed_url.startswith("https://") for item in PODCAST_CATALOG)
    assert {item.ingest_status for item in PODCAST_CATALOG} == {"ready", "blocked"}
    blocked = [item for item in PODCAST_CATALOG if item.ingest_status == "blocked"]
    assert [item.source_id for item in blocked] == ["podcast_voices_from_darpa"]
    assert "TLS" in blocked[0].status_note
    by_id = catalog_by_id()
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
        assert len(result["created"]) == 36
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

        again = import_podcast_catalog(session)
        assert again["created"] == []
        assert len(again["skipped_existing"]) == 36

        catalog = list_podcast_catalog(session)
        assert catalog["total"] == 36
        assert catalog["ready"] == 35
        assert catalog["installed"] == 36
        assert "active" not in catalog
        assert all("active" not in item for item in catalog["items"])


def test_application_bootstrap_preserves_metadata_and_normalizes_legacy_inactive_rows(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'bootstrap.db'}")

    first = ensure_default_podcast_sources(db.engine)
    assert len(first["created"]) == 36

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent is not None
        assert latent.is_active is True
        latent.name = "本地维护的名称"
        latent.is_active = False
        session.add(latent)
        session.commit()

    second = ensure_default_podcast_sources(db.engine)
    assert second["created"] == []
    assert len(second["skipped_existing"]) == 36

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent.name == "本地维护的名称"
        assert latent.is_active is True
        catalog = list_podcast_catalog(session)
        assert catalog["installed"] == 36
        assert "active" not in catalog


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
    monkeypatch.setattr(app_module, "schedule_podcast_premium_after_landing", lambda _ids: 0)

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
        assert catalog.json()["total"] == 36
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
