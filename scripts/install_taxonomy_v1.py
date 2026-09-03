#!/usr/bin/env python3
"""Install the repository-approved Taxonomy v1 catalog into one target DB.

Fresh production mode performs the exact guarded sequence:
  migrate -> bind catalog to target -> validation-only -> import + receipt.
Publishing remains an explicit admin UI action. Article analysis must stay off
until that publish succeeds. The installer deliberately refuses to mutate an
already-active taxonomy version; governed changes go through a new version.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlmodel import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import apply_taxonomy_v1_review as review_apply  # noqa: E402
import prepare_taxonomy_v1_review as review_prepare  # noqa: E402
from taxonomy_catalog import validate_manifest  # noqa: E402
from config import settings  # noqa: E402
from models.db import AppSettingRecord  # noqa: E402
from services import taxonomy  # noqa: E402
from services.article_analysis import ARTICLE_ANALYSIS_ENABLED_KEY  # noqa: E402
from storage.migrations import ensure_migrated  # noqa: E402


DEFAULT_CATALOG = PROJECT_ROOT / "config" / "taxonomy-v1-approved-catalog.json"
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the approved Taxonomy v1 catalog.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--review-out", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true", help="Migrate and persist the validated catalog")
    parser.add_argument("--backup", type=Path, help="Optional one-time SQLite backup before writes")
    parser.add_argument("--overwrite-review", action="store_true")
    return parser.parse_args(argv)


def load_catalog(path: Path) -> dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(catalog)
    if catalog.get("status") != "product_approved":
        raise ValueError("catalog must have status=product_approved")
    if catalog.get("review_basis") != "label_set_only":
        raise ValueError("installer only accepts the repository-approved label-set-only catalog")
    if catalog.get("unmatched_candidate_policy") != "fail":
        raise ValueError("approved catalog must fail on unmatched Candidates")
    if not isinstance(catalog.get("entries"), list) or not catalog["entries"]:
        raise ValueError("approved catalog has no entries")
    return catalog


def write_review(path: Path, report: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {path}; pass --overwrite-review")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sqlite_path(database_url: str) -> Path | None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return None
    return Path(parsed.database).expanduser().resolve()


def backup_sqlite(database_url: str, destination: Path) -> None:
    source = sqlite_path(database_url)
    if source is None:
        raise ValueError("--backup currently supports file-backed SQLite only")
    if not source.exists():
        return
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite backup {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        integrity = backup_db.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise ValueError(f"backup integrity check failed: {destination}")


def _feature_flag(session: Session, key: str, *, default: bool = False) -> bool:
    record = session.get(AppSettingRecord, key)
    if record is None:
        return default
    return str(record.value or "").strip().lower() in {"1", "true", "yes", "on"}


def apply_fresh(session: Session, report: dict[str, Any], *, actor_id: str) -> dict[str, int]:
    if taxonomy.current_taxonomy_version(session) != 0:
        raise ValueError("fresh Taxonomy v1 install requires active taxonomy version 0")
    if _feature_flag(session, ARTICLE_ANALYSIS_ENABLED_KEY, default=False):
        raise ValueError("disable article analysis before importing and publishing Taxonomy v1")
    review_apply.validate_target_candidate_coverage(session, report)
    counts = review_apply.apply_review(
        session,
        review_apply.approved_entries(report),
        actor_id=actor_id,
    )
    counts.update(review_apply.apply_remaining_decisions(session, report, actor_id=actor_id))
    review_apply.save_review_receipt(session, report, actor_id=actor_id)
    state = taxonomy.taxonomy_governance_state(session)
    if not state["publish_ready"]:
        raise ValueError(f"import finished but publish gate is blocked: {state['publish_blockers']}")
    return counts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalog = load_catalog(args.catalog)
    if args.apply:
        if args.backup:
            backup_sqlite(args.database_url, args.backup)
        ensure_migrated(args.database_url)

    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = review_prepare.prepare_review(session, catalog)
        review_apply.validate_complete_review(report)
        write_review(args.review_out, report, overwrite=args.overwrite_review)
        if not args.apply:
            print(json.dumps({
                "mode": "validation-only",
                "approved_entries": len(review_apply.approved_entries(report)),
                "review_out": str(args.review_out),
            }, ensure_ascii=False, sort_keys=True))
            return 0
        with Session(engine) as session:
            counts = apply_fresh(session, report, actor_id=args.actor)
    finally:
        engine.dispose()
    print(json.dumps({
        "mode": "imported-awaiting-publish",
        "review_out": str(args.review_out),
        **counts,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
