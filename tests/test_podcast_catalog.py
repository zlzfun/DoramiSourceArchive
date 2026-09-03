"""Curated podcast catalog validation and import contract."""

from dataclasses import replace
import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from models.db import SourceConfigRecord, UserRecord
from services import accounts as accounts_service
from services.podcast_catalog import (
    DEFAULT_MAX_RESPONSE_BYTES,
    PODCAST_CATALOG,
    catalog_by_id,
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
