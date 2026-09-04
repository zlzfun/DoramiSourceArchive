#!/usr/bin/env python3
"""Thin manual wrapper around the deployment Taxonomy v1 reconciler.

Normal authority deployments invoke the same reconciler automatically after
Alembic migration and before API/worker startup. This command remains useful
for validation, an optional SQLite backup, and explicit disaster recovery.
Publishing is intentionally not part of either path.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import TaxonomyDeploymentConfig, settings  # noqa: E402
from services.taxonomy_deployment import (  # noqa: E402
    DEFAULT_CATALOG,
    load_catalog,
    run_taxonomy_deployment,
)
from storage.migrations import ensure_migrated  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or reconcile the approved Taxonomy v1 catalog."
    )
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--actor", default="taxonomy-v1-manual-recovery")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    return parser.parse_args(argv)


def backup_sqlite(database_url: str, destination: Path) -> None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        raise ValueError("--backup supports file-backed SQLite only")
    source = Path(parsed.database).expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite backup {destination}")
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        integrity = backup_db.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise ValueError(f"backup integrity check failed: {destination}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalog = load_catalog(args.catalog)
    if not args.apply:
        print(json.dumps({
            "mode": "validation-only",
            "approved_entries": len(catalog["entries"]),
            "manifest_sha256": catalog["manifest_sha256"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.backup:
        backup_sqlite(args.database_url, args.backup)
    ensure_migrated(args.database_url)
    result = run_taxonomy_deployment(
        args.database_url,
        TaxonomyDeploymentConfig(
            mode="authority",
            catalog_path=str(args.catalog.resolve()),
        ),
        actor_id=args.actor,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
