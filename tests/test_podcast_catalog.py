"""Curated podcast catalog validation and import contract."""

from dataclasses import replace
from types import SimpleNamespace
import asyncio
import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from models.db import SourceConfigRecord, UserRecord
from services import accounts as accounts_service
from services.podcast_catalog import (
    DEFAULT_MAX_RESPONSE_BYTES,
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


def test_default_import_creates_only_ready_sources_inactive_and_is_idempotent(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'catalog.db'}")
    with Session(db.engine) as session:
        result = import_podcast_catalog(session)
        assert len(result["created"]) == 35
        assert result["updated"] == []
        assert result["skipped_blocked"][0]["source_id"] == "podcast_voices_from_darpa"

        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent is not None
        assert latent.source_type == "podcast"
        assert latent.fetcher_id == "generic_podcast_rss"
        assert latent.category == "incubating"
        assert latent.is_active is False
        assert latent.owner_username == ""
        assert latent.source_scope == "ai_media"
        assert latent.provenance_tier == "tier1_curated"
        nvidia = session.get(SourceConfigRecord, "podcast_nvidia_ai")
        assert nvidia.source_scope == "company"
        assert nvidia.provenance_tier == "tier0_primary"
        dwarkesh = session.get(SourceConfigRecord, "podcast_dwarkesh")
        assert dwarkesh.source_scope == "expert_commentary"
        assert dwarkesh.provenance_tier == "tier2_commentary"
        params = json.loads(latent.params_json)
        assert params["limit"] == 20
        assert params["max_response_bytes"] == DEFAULT_MAX_RESPONSE_BYTES

        again = import_podcast_catalog(session)
        assert again["created"] == []
        assert len(again["skipped_existing"]) == 35

        catalog = list_podcast_catalog(session)
        assert catalog["total"] == 36
        assert catalog["ready"] == 35
        assert catalog["installed"] == 35
        assert catalog["active"] == 0


def test_application_bootstrap_installs_ready_sources_without_overwriting_local_state(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'bootstrap.db'}")

    first = ensure_default_podcast_sources(db.engine)
    assert len(first["created"]) == 35
    assert first["skipped_blocked"][0]["source_id"] == "podcast_voices_from_darpa"

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent is not None
        assert latent.is_active is False
        latent.name = "本地维护的名称"
        latent.is_active = True
        session.add(latent)
        session.commit()

    second = ensure_default_podcast_sources(db.engine)
    assert second["created"] == []
    assert len(second["skipped_existing"]) == 35

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        assert latent.name == "本地维护的名称"
        assert latent.is_active is True
        catalog = list_podcast_catalog(session)
        assert catalog["installed"] == 35
        assert catalog["active"] == 1


class _PodcastScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, callback, trigger, **kwargs):
        self.jobs[kwargs["id"]] = SimpleNamespace(
            id=kwargs["id"], callback=callback, trigger=trigger, kwargs=kwargs,
        )

    def get_jobs(self):
        return list(self.jobs.values())

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def test_active_shared_podcasts_get_independent_interval_schedules(monkeypatch, tmp_path):
    import api.app as app_module

    db = DatabaseStorage(f"sqlite:///{tmp_path / 'schedules.db'}")
    ensure_default_podcast_sources(db.engine)
    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        latent.is_active = True
        latent.fetch_interval_minutes = 90
        session.add(latent)
        second = session.get(SourceConfigRecord, "podcast_20vc")
        second.is_active = True
        second.fetch_interval_minutes = 90
        session.add(second)
        session.commit()

    fake = _PodcastScheduler()
    monkeypatch.setattr(app_module, "db_sink", db)
    monkeypatch.setattr(app_module, "scheduler", fake)
    app_module.reload_podcast_source_schedules()

    job_id = f"{app_module.PODCAST_SCHEDULE_JOB_PREFIX}podcast_latent_space"
    second_job_id = f"{app_module.PODCAST_SCHEDULE_JOB_PREFIX}podcast_20vc"
    assert set(fake.jobs) == {job_id, second_job_id}
    assert fake.jobs[job_id].kwargs["minutes"] == 90
    assert fake.jobs[job_id].kwargs["args"] == ["podcast_latent_space"]
    assert fake.jobs[job_id].kwargs["next_run_time"] != fake.jobs[second_job_id].kwargs["next_run_time"]

    with Session(db.engine) as session:
        latent = session.get(SourceConfigRecord, "podcast_latent_space")
        latent.fetch_interval_minutes = -1
        session.add(latent)
        second = session.get(SourceConfigRecord, "podcast_20vc")
        second.is_active = False
        session.add(second)
        session.commit()
    app_module.reload_podcast_source_schedules()
    assert fake.jobs == {}


def test_scheduled_podcast_job_fetches_active_source_and_rechecks_toggle(monkeypatch, tmp_path):
    import api.app as app_module

    db = DatabaseStorage(f"sqlite:///{tmp_path / 'scheduled-fetch.db'}")
    ensure_default_podcast_sources(db.engine)
    with Session(db.engine) as session:
        source = session.get(SourceConfigRecord, "podcast_latent_space")
        source.is_active = True
        session.add(source)
        session.commit()

    calls = []

    async def _fake_run(items, **kwargs):
        calls.append((items, kwargs))
        return {"status": "success", "results": []}

    monkeypatch.setattr(app_module, "db_sink", db)
    monkeypatch.setattr(app_module, "run_collection_items", _fake_run)
    asyncio.run(app_module.execute_podcast_source_refresh_job("podcast_latent_space"))

    assert len(calls) == 1
    items, kwargs = calls[0]
    assert items[0]["fetcher_id"] == "generic_podcast_rss"
    assert items[0]["params"]["feed_url"].startswith("https://")
    assert kwargs["trigger_type"] == "scheduled"

    with Session(db.engine) as session:
        source = session.get(SourceConfigRecord, "podcast_latent_space")
        source.is_active = False
        session.add(source)
        session.commit()
    asyncio.run(app_module.execute_podcast_source_refresh_job("podcast_latent_space"))
    assert len(calls) == 1


def test_selective_update_preserves_active_and_blocked_requires_opt_in(tmp_path):
    db = DatabaseStorage(f"sqlite:///{tmp_path / 'selective.db'}")
    with Session(db.engine) as session:
        first = import_podcast_catalog(
            session,
            source_ids=["podcast_latent_space"],
            activate=True,
        )
        assert first["created"] == ["podcast_latent_space"]

        row = session.get(SourceConfigRecord, "podcast_latent_space")
        row.name = "Local name"
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

        blocked = import_podcast_catalog(
            session,
            source_ids=["podcast_voices_from_darpa"],
        )
        assert blocked["created"] == []
        assert blocked["skipped_blocked"]
        allowed = import_podcast_catalog(
            session,
            source_ids=["podcast_voices_from_darpa"],
            include_blocked=True,
        )
        assert allowed["created"] == ["podcast_voices_from_darpa"]


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
            json={"source_ids": ["podcast_semianalysis_weekly"], "activate": True},
        )
        assert imported.status_code == 200
        assert imported.json()["created"] == ["podcast_semianalysis_weekly"]

        source = client.get("/api/source-configs/podcast_semianalysis_weekly")
        assert source.status_code == 200
        assert source.json()["shape"] == "podcast"
        assert source.json()["is_active"] is True

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
        assert node["is_active"] is True
        assert node["feed_url"].startswith("https://")
        assert node["source_scope"] == "ai_media"
