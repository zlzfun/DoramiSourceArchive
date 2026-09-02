#!/usr/bin/env python3
"""Run the auditable taxonomy-bootstrap-v1 database pass.

Normal collection is intentionally executed with the existing collection job
before this script; this command never resets a source cursor.  It freezes the
bounded article sample, optionally ingests JSONL proposals produced by an open
``topic/industry/entity`` extraction pass, and leaves automatic activation off.

Examples::

    .venv/bin/python scripts/bootstrap_taxonomy.py --manifest-out data/taxonomy-bootstrap-v1.json
    .venv/bin/python scripts/bootstrap_taxonomy.py \
      --manifest-in data/taxonomy-bootstrap-v1.json \
      --proposals-jsonl data/taxonomy-bootstrap-v1-proposals.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlmodel import Session  # noqa: E402

from config import settings  # noqa: E402
from services.taxonomy_bootstrap import (  # noqa: E402
    BOOTSTRAP_LOOKBACK_DAYS,
    BOOTSTRAP_PER_SOURCE_LIMIT,
    BootstrapManifest,
    BootstrapProposal,
    build_bootstrap_manifest,
    ingest_bootstrap_proposals,
    validate_manifest,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _as_of(value: str) -> dt.datetime:
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be an ISO-8601 datetime") from exc
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of must include an explicit timezone")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze and ingest taxonomy-bootstrap-v1.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--as-of", type=_as_of, default=dt.datetime.now(dt.timezone.utc))
    parser.add_argument("--lookback-days", type=int, default=BOOTSTRAP_LOOKBACK_DAYS)
    parser.add_argument("--per-source-limit", type=int, default=BOOTSTRAP_PER_SOURCE_LIMIT)
    parser.add_argument(
        "--manifest-in",
        type=Path,
        help="Reuse an exact frozen manifest instead of rebuilding it with the current database state.",
    )
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument(
        "--proposals-jsonl",
        type=Path,
        help="One JSON object per line: article_id,label,proposed_kind,confidence[,context_excerpt].",
    )
    return parser.parse_args()


def load_proposals(path: Path) -> list[BootstrapProposal]:
    proposals: list[BootstrapProposal] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                proposals.append(BootstrapProposal(**json.loads(line)))
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"invalid proposal at {path}:{line_no}: {exc}") from exc
    return proposals


def load_manifest(path: Path) -> BootstrapManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key in ("source_ids", "article_ids", "structural_labels"):
            raw[key] = tuple(raw.get(key) or ())
        manifest = BootstrapManifest(**raw)
        validate_manifest(manifest)
        return manifest
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid bootstrap manifest {path}: {exc}") from exc


def main() -> int:
    args = parse_args()
    storage = DatabaseStorage(args.database_url)
    try:
        with Session(storage.engine) as session:
            manifest = (
                load_manifest(args.manifest_in)
                if args.manifest_in
                else build_bootstrap_manifest(
                    session,
                    as_of=args.as_of,
                    lookback_days=args.lookback_days,
                    per_source_limit=args.per_source_limit,
                )
            )
            if args.manifest_out:
                args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
                args.manifest_out.write_text(manifest.to_json() + "\n", encoding="utf-8")
            result = None
            if args.proposals_jsonl:
                result = ingest_bootstrap_proposals(
                    session,
                    manifest=manifest,
                    proposals=load_proposals(args.proposals_jsonl),
                    now=dt.datetime.fromisoformat(manifest.as_of),
                )
            print(
                json.dumps(
                    {"manifest": manifest.to_dict(), "ingestion": result},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
    finally:
        storage.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
