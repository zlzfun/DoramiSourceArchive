"""Repository-approved Taxonomy v1 installs reproducibly on fresh and dev DBs."""

from __future__ import annotations

import datetime as dt
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
from models.db import CmsTagCandidateRecord, CmsTagRecord  # noqa: E402
from services import taxonomy  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc)


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


def test_fresh_install_reaches_publish_gate_without_publishing():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
        with Session(storage.engine) as session:
            report = review_prepare.prepare_review(session, catalog)
            review_apply.validate_complete_review(report)
            counts = installer.apply_fresh(session, report, actor_id="release-test")
            state = taxonomy.taxonomy_governance_state(session, now=NOW)
            tags = list(session.exec(select(CmsTagRecord)).all())
            assert counts["created"] == 96
            assert len(tags) == 96
            assert sum(tag.user_selectable for tag in tags) == 94
            assert state["publish_ready"] is True
            assert state["active_version"] == 0
            assert state["review_receipt"]["review_basis"] == "label_set_only"
            assert state["review_receipt"]["coverage_decision"] == "not_applicable"
    finally:
        storage.engine.dispose()


def test_installer_refuses_to_mutate_an_active_v1():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
        with Session(storage.engine) as session:
            report = review_prepare.prepare_review(session, catalog)
            installer.apply_fresh(session, report, actor_id="release-test")
            version = taxonomy.create_taxonomy_version(
                session,
                change_summary="published v1",
                now=NOW,
            )
            taxonomy.activate_taxonomy_version(session, version.version, actor_id="test", now=NOW)
            with pytest.raises(ValueError, match="active taxonomy version 0"):
                installer.apply_fresh(session, report, actor_id="release-test")
    finally:
        storage.engine.dispose()


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
