#!/usr/bin/env python3
"""Inventory, migrate, verify, restore or evict media. Dry-run by default.

Run against an already-migrated DB with API/workers stopped before --apply.
Does not import api.app, initialize schema or start background jobs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["upload", "verify", "restore", "evict", "gc"])
    parser.add_argument("--namespace", choices=["all", "media", "podcast"], default="all")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--offline", action="store_true", help="confirm ALL API/workers are stopped")
    parser.add_argument("--cache-target-mb", type=int, default=2048, help="per selected namespace, evict only verified OSS copies")
    parser.add_argument("--prune-before", type=dt.date.fromisoformat,
                        help="GC cutoff date; ensure no retained backup needs these unreferenced objects")
    args = parser.parse_args(argv)
    if args.apply and not args.offline:
        parser.error("--apply requires --offline: stop all API/workers first")
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from config import load_config
    from services.object_storage import ObjectStorage
    from services.object_storage_maintenance import execute
    config = load_config()
    url = make_url(config.storage.database_url)
    if url.get_backend_name() == "sqlite":
        database = Path(url.database or "").resolve()
        if not database.is_file():
            parser.error("existing file database required; apply Alembic migrations separately")
        if not args.apply:
            import sqlite3
            engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True))
        else:
            engine = create_engine(url)
    else:
        engine = create_engine(url)
    # Refuse accidental initialization of a new/old schema by performing only reads.
    stores = {ns: ObjectStorage(engine, Path(root), ns, config.oss) for ns, root in
              (("media", config.media.media_dir), ("podcast", config.podcast_artifacts.root_dir))
              if args.namespace in {"all", ns}}
    cutoff = dt.datetime.combine(args.prune_before, dt.time(), tzinfo=dt.timezone.utc) if args.prune_before else None
    try:
        result = execute(engine, stores, action=args.action, apply=args.apply, offline=args.offline,
                         cache_target_bytes=args.cache_target_mb * 1024 * 1024, prune_before=cutoff,
                         emit=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
        print(json.dumps({"summary": result}, ensure_ascii=False))
        return 1 if result["errors"] else 0
    except Exception as exc:
        # SQL/SDK errors can embed credentials or source URLs; do not dump them.
        print(json.dumps({"fatal": type(exc).__name__}), file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
