#!/usr/bin/env python3
"""Review or import the curated podcast catalog.

Examples (run from repository root)::

    PYTHONPATH=src uv run python scripts/import_podcast_catalog.py
    PYTHONPATH=src uv run python scripts/import_podcast_catalog.py --apply --activate
    PYTHONPATH=src uv run python scripts/import_podcast_catalog.py --apply \
        --source podcast_latent_space --source podcast_semianalysis_weekly

The default is a read-only dry run. ``--apply`` writes source-config rows, and
new rows remain inactive unless ``--activate`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlmodel import Session  # noqa: E402

from config import settings  # noqa: E402
from services.podcast_catalog import (  # noqa: E402
    PODCAST_CATALOG,
    import_podcast_catalog,
    list_podcast_catalog,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审核或幂等导入精选播客目录。")
    parser.add_argument("--apply", action="store_true", help="写入数据库；默认只做 dry-run。")
    parser.add_argument("--activate", action="store_true", help="新建后立即启用；默认保持停用。")
    parser.add_argument("--update-existing", action="store_true", help="用目录元数据更新已有同 ID 配置。")
    parser.add_argument("--include-blocked", action="store_true", help="连同当前验证失败的源一起导入。")
    parser.add_argument("--source", action="append", default=[], help="仅处理指定 source_id，可重复。")
    parser.add_argument("--database-url", default=settings.storage.database_url, help="目标数据库 URL。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    known_ids = {item.source_id for item in PODCAST_CATALOG}
    unknown = sorted(set(args.source) - known_ids)
    if unknown:
        raise SystemExit(f"未知播客目录 source_id: {', '.join(unknown)}")

    if not args.apply:
        catalog = list_podcast_catalog()
        selected = set(args.source) if args.source else known_ids
        items = [
            item for item in catalog["items"]
            if item["source_id"] in selected
            and (args.include_blocked or item["ingest_status"] == "ready")
        ]
        print(json.dumps({
            "action": "dry_run",
            "database_url": args.database_url,
            "activate": args.activate,
            "update_existing": args.update_existing,
            "selected": len(items),
            "items": items,
        }, ensure_ascii=False, indent=2))
        return 0

    db = DatabaseStorage(args.database_url)
    with Session(db.engine) as session:
        result = import_podcast_catalog(
            session,
            source_ids=args.source,
            activate=args.activate,
            update_existing=args.update_existing,
            include_blocked=args.include_blocked,
        )
    print(json.dumps({"action": "applied", "database_url": args.database_url, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
