"""Repository-approved Taxonomy v1 installs reproducibly on fresh and dev DBs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, select


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_taxonomy_v1_review as review_apply  # noqa: E402
import install_taxonomy_v1 as installer  # noqa: E402
import prepare_taxonomy_v1_review as review_prepare  # noqa: E402
from config import TaxonomyDeploymentConfig  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    CmsTagAliasRecord,
    CmsTagEventRecord,
    CmsTagRecord,
)
from services import taxonomy  # noqa: E402
from services.taxonomy_deployment import (  # noqa: E402
    TaxonomyDeploymentError,
    reconcile_approved_taxonomy_v1,
    run_taxonomy_deployment,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def test_repository_approved_catalog_is_complete_and_product_confirmed():
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    assert len(catalog["entries"]) == 96
    assert sum(bool(row["user_selectable"]) for row in catalog["entries"]) == 94
    by_code = {row["code"]: row for row in catalog["entries"]}
    for code in ("topic.pretraining", "topic.post-training"):
        assert by_code[code]["parent_code"] == "topic.model-training"
        assert by_code[code]["user_selectable"] is False
        assert by_code[code]["filterable"] is True
        assert by_code[code]["recommendable"] is True
    assert by_code["industry.cybersecurity"]["name_zh"] == "网络安全产业"
    assert by_code["industry.cybersecurity"]["user_selectable"] is True


def test_authority_reconcile_installs_once_without_publishing():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        first = reconcile_approved_taxonomy_v1(storage.engine)
        with Session(storage.engine) as session:
            state = taxonomy.taxonomy_governance_state(session)
            tags = list(session.exec(select(CmsTagRecord)).all())
            aliases_before = len(list(session.exec(select(CmsTagAliasRecord)).all()))
            events_before = len(list(session.exec(select(CmsTagEventRecord)).all()))
            assert first["status"] == "installed_awaiting_publish"
            assert first["created"] == 96
            assert len(tags) == 96
            assert sum(tag.user_selectable for tag in tags) == 94
            assert state["publish_ready"] is True
            assert state["active_version"] == 0
            assert state["review_receipt"]["review_basis"] == "label_set_only"
            assert state["review_receipt"]["coverage_decision"] == "not_applicable"

        second = reconcile_approved_taxonomy_v1(storage.engine)
        with Session(storage.engine) as session:
            assert second["status"] == "unchanged"
            assert second["created"] == 0
            assert len(list(session.exec(select(CmsTagRecord)).all())) == 96
            assert len(list(session.exec(select(CmsTagAliasRecord)).all())) == aliases_before
            assert len(list(session.exec(select(CmsTagEventRecord)).all())) == events_before
    finally:
        storage.engine.dispose()


def test_matching_receipt_remains_noop_after_human_publish():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        reconcile_approved_taxonomy_v1(storage.engine)
        with Session(storage.engine) as session:
            version = taxonomy.create_taxonomy_version(
                session,
                change_summary="published v1",
            )
            taxonomy.activate_taxonomy_version(session, version.version, actor_id="human")
        assert reconcile_approved_taxonomy_v1(storage.engine)["status"] == "unchanged"
    finally:
        storage.engine.dispose()


def test_authority_reconcile_recovers_a_compatible_partial_import():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    first_entry = catalog["entries"][0]
    try:
        with Session(storage.engine) as session:
            taxonomy.create_tag(
                session,
                code=first_entry["code"],
                kind=first_entry["kind"],
                name_zh=first_entry["name_zh"],
                name_en=first_entry["name_en"],
                description=first_entry["description"],
                prompt_description=first_entry["prompt_description"],
                status="active",
                user_selectable=first_entry["user_selectable"],
                filterable=first_entry["filterable"],
                recommendable=first_entry["recommendable"],
                activation_mode="manual",
                entity_type=first_entry["entity_type"],
                external_key=first_entry.get("external_key"),
            )
            session.commit()

        result = reconcile_approved_taxonomy_v1(storage.engine)
        assert result["created"] == 95
        with Session(storage.engine) as session:
            assert len(list(session.exec(select(CmsTagRecord)).all())) == 96
            assert session.get(
                AppSettingRecord, taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY
            ) is not None
    finally:
        storage.engine.dispose()


def test_authority_reconcile_fails_closed_on_catalog_conflict():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    first_entry = catalog["entries"][0]
    try:
        with Session(storage.engine) as session:
            taxonomy.create_tag(
                session,
                code=first_entry["code"],
                kind=first_entry["kind"],
                name_zh=first_entry["name_zh"],
                name_en=first_entry["name_en"],
                description="conflicting local edit",
                prompt_description=first_entry["prompt_description"],
                status="active",
                user_selectable=first_entry["user_selectable"],
                filterable=first_entry["filterable"],
                recommendable=first_entry["recommendable"],
                activation_mode="manual",
                entity_type=first_entry["entity_type"],
                external_key=first_entry.get("external_key"),
            )
            session.commit()

        with pytest.raises(TaxonomyDeploymentError, match="conflicts"):
            reconcile_approved_taxonomy_v1(storage.engine)
        with Session(storage.engine) as session:
            assert len(list(session.exec(select(CmsTagRecord)).all())) == 1
            assert session.get(
                AppSettingRecord, taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY
            ) is None
    finally:
        storage.engine.dispose()


def test_authority_reconcile_fails_closed_on_different_receipt():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            session.add(
                AppSettingRecord(
                    key=taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY,
                    value=json.dumps({"manifest_sha256": "0" * 64}),
                )
            )
            session.commit()
        with pytest.raises(TaxonomyDeploymentError, match="receipt conflicts"):
            reconcile_approved_taxonomy_v1(storage.engine)
        with Session(storage.engine) as session:
            assert len(list(session.exec(select(CmsTagRecord)).all())) == 0
    finally:
        storage.engine.dispose()


def test_authority_reconcile_rejects_incomplete_matching_receipt():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    try:
        with Session(storage.engine) as session:
            session.add(
                AppSettingRecord(
                    key=taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY,
                    value=json.dumps(
                        {"manifest_sha256": catalog["manifest_sha256"]}
                    ),
                )
            )
            session.commit()
        with pytest.raises(TaxonomyDeploymentError, match="receipt is malformed"):
            reconcile_approved_taxonomy_v1(storage.engine)
    finally:
        storage.engine.dispose()


@pytest.mark.parametrize("mode", ["manual", "replica"])
def test_non_authority_mode_is_explicit_noop_without_loading_catalog(mode):
    result = run_taxonomy_deployment(
        "sqlite:///:memory:",
        TaxonomyDeploymentConfig(mode=mode, catalog_path="/does/not/exist.json"),
    )
    assert result == {"status": mode, "action": "none"}


def test_role_all_does_not_infer_taxonomy_authority(monkeypatch, tmp_path):
    import config

    ini = tmp_path / "backend.ini"
    ini.write_text("[runtime]\nrole = all\n", encoding="utf-8")
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.delenv("DORAMI_TAXONOMY_DEPLOYMENT", raising=False)
    assert config.load_config().taxonomy.mode == "manual"

    monkeypatch.setenv("DORAMI_TAXONOMY_DEPLOYMENT", "replica")
    assert config.load_config().taxonomy.mode == "replica"
    monkeypatch.setenv("DORAMI_TAXONOMY_DEPLOYMENT", "authority")
    assert config.load_config().taxonomy.mode == "authority"


def test_invalid_taxonomy_deployment_mode_is_rejected(monkeypatch, tmp_path):
    import config

    ini = tmp_path / "backend.ini"
    ini.write_text("[taxonomy]\ndeployment = automatic\n", encoding="utf-8")
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.delenv("DORAMI_TAXONOMY_DEPLOYMENT", raising=False)
    with pytest.raises(ValueError, match="Invalid taxonomy deployment mode"):
        config.load_config()


def test_official_startup_paths_reconcile_after_migration_before_api():
    main_source = (ROOT / "src/main.py").read_text(encoding="utf-8")
    container_source = (ROOT / "docker/entrypoint.py").read_text(encoding="utf-8")
    deploy_source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    for source, final_marker in (
        (main_source, "uvicorn.run("),
        (container_source, "import uvicorn"),
        (deploy_source, "Building frontend"),
    ):
        assert source.index("ensure_migrated") < source.index("run_taxonomy_deployment")
        assert source.index("run_taxonomy_deployment") < source.index(final_marker)
    assert "COPY config/taxonomy-v1-approved-catalog.json" in (
        ROOT / "docker/backend.Dockerfile"
    ).read_text(encoding="utf-8")


def test_catalog_manifest_detects_content_tampering(tmp_path):
    catalog = json.loads(installer.DEFAULT_CATALOG.read_text(encoding="utf-8"))
    catalog["entries"][0]["name_zh"] = "被篡改"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest_sha256"):
        installer.load_catalog(path)


def test_apply_review_rejects_content_tampering_even_with_original_manifest():
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            report = review_prepare.prepare_review(session, catalog)
        review_apply.validate_review_catalog_binding(report, catalog)
        report["entries"][0]["name_zh"] = "被篡改"
        with pytest.raises(ValueError, match="do not match"):
            review_apply.validate_review_catalog_binding(report, catalog)
    finally:
        storage.engine.dispose()


def test_apply_review_rejects_injected_target_derived_aliases():
    catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            report = review_prepare.prepare_review(session, catalog)
        report["entries"][0]["source_labels"] = ["Injected Alias"]
        with pytest.raises(ValueError, match="source_labels"):
            review_apply.validate_review_catalog_binding(report, catalog)
    finally:
        storage.engine.dispose()
