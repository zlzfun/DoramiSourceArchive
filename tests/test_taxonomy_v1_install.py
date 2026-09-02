"""Repository-approved Taxonomy v1 installs reproducibly on fresh and dev DBs."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

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


def test_active_v1_dev_sync_preserves_candidates_and_cleans_known_bad_rows():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        catalog = installer.load_catalog(installer.DEFAULT_CATALOG)
        report = installer.portable_review(catalog)
        review_apply.validate_complete_review(report)
        with Session(storage.engine) as session:
            taxonomy.create_tag(
                session,
                code="topic.ai",
                kind="industry",
                name_zh="人工智能",
                name_en="artificial intelligence",
                status="active",
                user_selectable=True,
            )
            taxonomy.create_tag(
                session,
                code="topic.ai.vendor",
                kind="industry",
                name_zh="人工智能应用服务商",
                name_en="AI vendor",
                status="active",
                user_selectable=True,
            )
            session.commit()
            version = taxonomy.create_taxonomy_version(
                session,
                change_summary="old development v1",
                now=NOW,
            )
            taxonomy.activate_taxonomy_version(session, version.version, actor_id="test", now=NOW)
            session.add(CmsTagCandidateRecord(
                label="保留的灵活标签",
                normalized_label="保留的灵活标签",
                proposed_kind="topic",
                status="candidate",
                created_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
                first_seen_at=NOW.isoformat(),
                last_seen_at=NOW.isoformat(),
            ))
            session.add(CmsTagCandidateRecord(
                label="具身智能",
                normalized_label="具身智能",
                proposed_kind="topic",
                status="candidate",
                created_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
                first_seen_at=NOW.isoformat(),
                last_seen_at=NOW.isoformat(),
            ))
            session.commit()

            counts = installer.sync_active_v1(
                session,
                catalog,
                report,
                actor_id="release-test",
            )
            active = list(
                session.exec(select(CmsTagRecord).where(CmsTagRecord.status == "active")).all()
            )
            assert counts["deprecated"] == 2
            assert counts["candidates_resolved"] == 0
            assert len(active) == 96
            assert {
                row.status for row in session.exec(select(CmsTagCandidateRecord)).all()
            } == {"candidate"}
            assert taxonomy.current_taxonomy_version(session) == 1
    finally:
        storage.engine.dispose()
