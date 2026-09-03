#!/usr/bin/env python3
"""Generate review-only JSONL proposals from a frozen taxonomy manifest."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from config import settings  # noqa: E402
from services.daily_brief import resolve_llm_config  # noqa: E402
from services.taxonomy_bootstrap import BootstrapManifest, validate_manifest  # noqa: E402
from services.taxonomy_bootstrap_extraction import (  # noqa: E402
    extract_bootstrap_proposals,
    load_manifest_articles,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract review-only taxonomy bootstrap proposals.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--proposals-out", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_manifest(path: Path) -> BootstrapManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["source_ids"] = tuple(raw.get("source_ids") or ())
    raw["article_ids"] = tuple(raw.get("article_ids") or ())
    raw["structural_labels"] = tuple(raw.get("structural_labels") or ())
    manifest = BootstrapManifest(**raw)
    validate_manifest(manifest)
    return manifest


def main() -> int:
    args = parse_args()
    if args.proposals_out.exists() and not args.overwrite:
        raise SystemExit(f"refusing to overwrite {args.proposals_out}; pass --overwrite")
    engine = create_engine(args.database_url)
    try:
        manifest = load_manifest(args.manifest)
        articles = load_manifest_articles(engine, manifest, limit=args.limit)
        with Session(engine) as session:
            llm_config = resolve_llm_config(session)
        if not llm_config.configured:
            raise SystemExit("LLM is not configured")

        def progress(done: int, total: int) -> None:
            print(f"taxonomy bootstrap extraction: {done}/{total}", file=sys.stderr, flush=True)

        proposals = asyncio.run(
            extract_bootstrap_proposals(
                articles,
                structural_labels=manifest.structural_labels,
                llm_config=llm_config,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                progress=progress,
            )
        )
        args.proposals_out.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(
            json.dumps(proposal.__dict__, ensure_ascii=False, sort_keys=True) + "\n"
            for proposal in proposals
        )
        args.proposals_out.write_text(payload, encoding="utf-8")
        print(
            json.dumps(
                {
                    "articles": len(articles),
                    "batches": (len(articles) + args.batch_size - 1) // args.batch_size,
                    "model": llm_config.for_aux().model,
                    "proposals": len(proposals),
                    "proposals_out": str(args.proposals_out),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
