#!/usr/bin/env python3
"""Bind an approved v1 catalog to the unresolved Candidates in one database.

This command never changes taxonomy state.  It turns a product-approved stable
catalog into the complete portable review artifact required by
``apply_taxonomy_v1_review.py``.  Exact approved mappings are resolved to their
canonical entries; explicitly merge-only mappings do not become Aliases; every
remaining Candidate is rejected for v1 only when the catalog says so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from config import settings  # noqa: E402
from models.db import CmsTagCandidateRecord  # noqa: E402
from services.taxonomy import normalize_label  # noqa: E402
from taxonomy_catalog import validate_manifest  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a complete taxonomy-v1 review artifact.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def _candidate_key(kind: object, label: object) -> tuple[str, str]:
    return str(kind or "").strip(), normalize_label(str(label or ""))


def prepare_review(session: Session, catalog: dict[str, Any]) -> dict[str, Any]:
    if catalog.get("status") != "product_approved":
        raise ValueError("catalog must have status=product_approved")
    review_basis = str(catalog.get("review_basis") or "evidence_bootstrap")
    unmatched_policy = str(catalog.get("unmatched_candidate_policy") or "")
    reject_unmatched = bool(catalog.get("reject_unmatched_candidates_for_v1"))
    if review_basis == "label_set_only":
        if unmatched_policy != "fail":
            raise ValueError("label-set-only catalog must use unmatched_candidate_policy=fail")
    elif not reject_unmatched:
        raise ValueError("catalog must explicitly decide how unmatched Candidates are handled")
    raw_entries = catalog.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("catalog is missing approved entries")

    unresolved = list(
        session.exec(
            select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.status.in_(("candidate", "reviewing"))
            )
        ).all()
    )
    if review_basis == "label_set_only" and unresolved:
        raise ValueError(
            "label-set-only taxonomy v1 can only bootstrap a database with no unresolved Candidates; "
            "run the existing-database governance migration instead"
        )
    remaining = {
        (row.proposed_kind, row.normalized_label): row
        for row in unresolved
    }
    entries: list[dict[str, Any]] = []
    merge_only: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict) or raw.get("decision") not in {"accept", "edit"}:
            raise ValueError("every approved catalog entry must use accept/edit")
        entry = {
            key: value
            for key, value in raw.items()
            if key not in {"candidate_matches", "merge_only_candidates"}
        }
        source_candidates: list[dict[str, Any]] = []
        for group in raw.get("candidate_matches") or []:
            for label in group.get("labels") or []:
                key = _candidate_key(group.get("kind"), label)
                candidate = remaining.pop(key, None)
                if candidate is None:
                    continue
                source_candidates.append(
                    {
                        "candidate_id": int(candidate.id),
                        "kind": candidate.proposed_kind,
                        "label": candidate.label,
                        "normalized_label": candidate.normalized_label,
                    }
                )
        entry["source_candidates"] = source_candidates
        entry["source_candidate_ids"] = [item["candidate_id"] for item in source_candidates]
        entry["source_labels"] = [item["label"] for item in source_candidates]
        entries.append(entry)

        for group in raw.get("merge_only_candidates") or []:
            for label in group.get("labels") or []:
                key = _candidate_key(group.get("kind"), label)
                candidate = remaining.pop(key, None)
                if candidate is None:
                    continue
                merge_only.append(
                    {
                        "candidate_id": int(candidate.id),
                        "label": candidate.label,
                        "normalized_label": candidate.normalized_label,
                        "proposed_kind": candidate.proposed_kind,
                        "decision": "merge",
                        "resolution_code": str(raw["code"]),
                        "reason": "approved v1 concept mapping without creating an unconditional Alias",
                    }
                )

    rejected = [
        {
            "candidate_id": int(candidate.id),
            "label": candidate.label,
            "normalized_label": candidate.normalized_label,
            "proposed_kind": candidate.proposed_kind,
            "decision": "reject",
            "reason": "not accepted into the conservative taxonomy-v1 stable catalog",
        }
        for candidate in sorted(
            remaining.values(),
            key=lambda row: (row.proposed_kind, row.normalized_label, int(row.id)),
        )
    ] if reject_unmatched else []
    return {
        "status": "human_review_required",
        "review_basis": review_basis,
        "manifest_sha256": catalog.get("manifest_sha256"),
        "coverage": catalog.get("coverage") or {},
        "coverage_decision": catalog.get("coverage_decision"),
        "entries": entries,
        "unmapped_candidates": [*merge_only, *rejected],
        "decision_summary": {
            "accepted_entries": len(entries),
            "candidate_merges": sum(len(entry["source_candidates"]) for entry in entries)
            + len(merge_only),
            "candidate_rejections": len(rejected),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.json_out.exists() and not args.overwrite:
        raise FileExistsError(f"refusing to overwrite {args.json_out}")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    validate_manifest(catalog)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            report = prepare_review(session, catalog)
    finally:
        engine.dispose()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["decision_summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
