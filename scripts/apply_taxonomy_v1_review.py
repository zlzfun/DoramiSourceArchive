#!/usr/bin/env python3
"""Apply human-approved taxonomy-v1 review entries without publishing a version.

The review file remains the product approval artifact.  This command imports
only ``accept``/``edit`` rows, creates active canonical tags and Aliases, and
resolves matching Candidate rows.  Publishing taxonomy v1 is a separate guarded
action in the admin management surface.
"""

from __future__ import annotations

import argparse
import json
import re
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
from models.db import AppSettingRecord, CmsTagAliasRecord, CmsTagCandidateRecord, CmsTagRecord  # noqa: E402
from services import taxonomy  # noqa: E402
from taxonomy_catalog import validate_manifest  # noqa: E402


DEFAULT_CATALOG = PROJECT_ROOT / "config" / "taxonomy-v1-approved-catalog.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply an approved taxonomy-v1 review artifact.")
    parser.add_argument("--database-url", default=settings.storage.database_url)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true", help="Persist validated decisions")
    return parser.parse_args(argv)


def validate_review_catalog_binding(
    report: dict[str, Any],
    catalog: dict[str, Any],
) -> None:
    """Prove that the DB-bound review still represents the approved catalog."""

    validate_manifest(catalog)
    if catalog.get("status") != "product_approved":
        raise ValueError("catalog must have status=product_approved")
    if report.get("manifest_sha256") != catalog.get("manifest_sha256"):
        raise ValueError("review manifest_sha256 does not match the approved catalog")

    ignored_catalog_keys = {"candidate_matches", "merge_only_candidates"}
    bound_review_keys = {"source_candidates", "source_candidate_ids", "source_labels"}
    expected = {
        str(entry.get("code") or ""): {
            key: value
            for key, value in entry.items()
            if key not in ignored_catalog_keys
        }
        for entry in catalog.get("entries") or []
        if isinstance(entry, dict)
    }
    actual = {
        str(entry.get("code") or ""): {
            key: value
            for key, value in entry.items()
            if key not in bound_review_keys
        }
        for entry in report.get("entries") or []
        if isinstance(entry, dict)
    }
    if not expected or actual != expected:
        raise ValueError("review entries do not match the approved catalog content")

    catalog_by_code = {
        str(entry.get("code") or ""): entry
        for entry in catalog.get("entries") or []
        if isinstance(entry, dict)
    }
    for entry in report.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        approved = catalog_by_code.get(code) or {}
        allowed_candidates = {
            (str(group.get("kind") or ""), taxonomy.normalize_label(label))
            for group in approved.get("candidate_matches") or []
            if isinstance(group, dict)
            for label in group.get("labels") or []
        }
        source_candidates = entry.get("source_candidates") or []
        if not isinstance(source_candidates, list):
            raise ValueError(f"review source_candidates are invalid for {code}")
        normalized_candidates: list[tuple[int, str, str, str]] = []
        for candidate in source_candidates:
            if not isinstance(candidate, dict):
                raise ValueError(f"review source_candidates are invalid for {code}")
            label = str(candidate.get("label") or "")
            kind = str(candidate.get("kind") or "")
            normalized = str(candidate.get("normalized_label") or "")
            try:
                candidate_id = int(candidate.get("candidate_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"review source candidate id is invalid for {code}") from exc
            if candidate_id < 1 or normalized != taxonomy.normalize_label(label):
                raise ValueError(f"review source candidate content is invalid for {code}")
            if (kind, normalized) not in allowed_candidates:
                raise ValueError(f"review source candidate is not approved for {code}")
            normalized_candidates.append((candidate_id, kind, label, normalized))

        expected_ids = [item[0] for item in normalized_candidates]
        expected_labels = [item[2] for item in normalized_candidates]
        if entry.get("source_candidate_ids") != expected_ids:
            raise ValueError(f"review source_candidate_ids do not match source_candidates for {code}")
        if entry.get("source_labels") != expected_labels:
            raise ValueError(f"review source_labels do not match source_candidates for {code}")


def approved_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    if report.get("status") != "human_review_required":
        raise ValueError("review artifact has an unsupported status")
    entries = report.get("entries")
    if not isinstance(entries, list):
        raise ValueError("review artifact is missing entries")
    approved: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict) or raw.get("decision") not in {"accept", "edit"}:
            continue
        code = str(raw.get("code") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        name_zh = str(raw.get("name_zh") or "").strip()
        name_en = str(raw.get("name_en") or "").strip()
        if not code or kind not in {"topic", "industry", "entity"} or not (name_zh or name_en):
            raise ValueError(f"approved entry is incomplete: {code or '<missing-code>'}")
        if kind == "entity":
            try:
                taxonomy.validate_entity_type(str(raw.get("entity_type") or ""), required=True)
            except taxonomy.TaxonomyError as exc:
                raise ValueError(f"approved Entity has invalid entity_type: {code}") from exc
        parent_code = str(raw.get("parent_code") or "").strip()
        if parent_code and (not parent_code.startswith(f"{kind}.") or parent_code == code):
            raise ValueError(f"approved entry has invalid parent_code: {code}")
        approved.append({**raw, "code": code, "kind": kind, "name_zh": name_zh, "name_en": name_en})
    return approved


def validate_complete_review(report: dict[str, Any]) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(report.get("manifest_sha256") or "")):
        raise ValueError("review artifact has an invalid manifest_sha256")
    entries = report.get("entries") or []
    unmapped = report.get("unmapped_candidates") or []
    pending = [
        str(item.get("code") or item.get("label") or "<unknown>")
        for item in [*entries, *unmapped]
        if not isinstance(item, dict) or item.get("decision") not in {"accept", "edit", "reject", "merge"}
    ]
    if pending:
        raise ValueError(f"review still has pending decisions: {pending[:10]}")
    duplicate_codes = [
        code
        for code in {str(item.get("code") or "") for item in entries if isinstance(item, dict)}
        if code and sum(str(item.get("code") or "") == code for item in entries) > 1
    ]
    if duplicate_codes:
        raise ValueError(f"review has duplicate canonical codes: {sorted(duplicate_codes)[:10]}")
    owners: dict[tuple[str, str], str] = {}
    for entry in entries:
        if entry.get("decision") == "merge":
            raise ValueError(f"canonical entry cannot use merge; reject it and merge its Candidates: {entry.get('code')}")
        for key in entry.get("source_candidates") or []:
            candidate_key = (
                str(key.get("kind") or ""),
                str(key.get("normalized_label") or ""),
            )
            previous = owners.setdefault(candidate_key, str(entry.get("code") or ""))
            if all(candidate_key) and previous != str(entry.get("code") or ""):
                raise ValueError(
                    f"Candidate {candidate_key} is mapped to both {previous} and {entry.get('code')}"
                )
    for item in unmapped:
        if item.get("decision") == "merge" and not str(item.get("resolution_code") or "").strip():
            raise ValueError(f"unmapped Candidate merge is missing resolution_code: {item.get('label')}")
        if item.get("decision") in {"accept", "edit"}:
            raise ValueError(f"promote accepted unmapped Candidate into entries: {item.get('label')}")
        candidate_key = (
            str(item.get("proposed_kind") or ""),
            str(item.get("normalized_label") or taxonomy.normalize_label(item.get("label") or "")),
        )
        if all(candidate_key) and candidate_key in owners:
            raise ValueError(f"Candidate {candidate_key} appears in both an entry and the unmapped list")
        owners[candidate_key] = str(item.get("label") or "")
    coverage = report.get("coverage") or {}
    if report.get("review_basis") == "label_set_only":
        if report.get("coverage_decision") != "not_applicable":
            raise ValueError("label-set-only review must use coverage_decision=not_applicable")
        if any(int(coverage.get(key) or 0) != 0 for key in (
            "sampled_source_count", "manifest_source_count", "article_count", "candidate_count",
        )):
            raise ValueError("label-set-only review must not carry article/source coverage")
    else:
        sampled = int(coverage.get("sampled_source_count") or 0)
        manifest = int(coverage.get("manifest_source_count") or 0)
        if sampled < 0 or manifest < 1 or sampled > manifest:
            raise ValueError("review artifact has invalid source coverage counts")
        complete = sampled == manifest
        if not complete and report.get("coverage_decision") != "accept_bias":
            raise ValueError("source coverage is incomplete; set coverage_decision=accept_bias after product review")


def validate_target_candidate_coverage(session: Session, report: dict[str, Any]) -> None:
    """Fail before writes if the target database has Candidate drift."""

    referenced: set[tuple[str, str]] = set()
    for entry in report.get("entries") or []:
        keys = entry.get("source_candidates") or []
        if keys:
            for key in keys:
                referenced.add(
                    (str(key.get("kind") or ""), str(key.get("normalized_label") or ""))
                )
        else:
            referenced.update(
                (str(entry.get("kind") or ""), taxonomy.normalize_label(label))
                for label in entry.get("source_labels") or []
            )
    for item in report.get("unmapped_candidates") or []:
        referenced.add(
            (
                str(item.get("proposed_kind") or ""),
                str(item.get("normalized_label") or taxonomy.normalize_label(item.get("label") or "")),
            )
        )
    unresolved = list(
        session.exec(
            select(CmsTagCandidateRecord).where(
                CmsTagCandidateRecord.status.in_(("candidate", "reviewing"))
            )
        ).all()
    )
    missing = [
        (item.proposed_kind, item.normalized_label)
        for item in unresolved
        if (item.proposed_kind, item.normalized_label) not in referenced
    ]
    if missing:
        raise ValueError(
            f"target database has {len(missing)} unresolved Candidates outside this review: {missing[:10]}"
        )


def apply_review(
    session: Session,
    entries: list[dict[str, Any]],
    *,
    actor_id: str,
    resolve_candidates: bool = True,
) -> dict[str, int]:
    current_version = taxonomy.current_taxonomy_version(session)
    if current_version > 0:
        raise ValueError("taxonomy v1 is already published; use normal governance operations")
    counts = {
        "created": 0,
        "updated": 0,
        "aliases": 0,
        "candidates_resolved": 0,
        "parents": 0,
    }
    for entry in entries:
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == entry["code"])).first()
        if tag is None:
            tag = taxonomy.create_tag(
                session,
                code=entry["code"],
                kind=entry["kind"],
                name_zh=entry["name_zh"],
                name_en=entry["name_en"],
                description=str(entry.get("description") or ""),
                prompt_description=str(entry.get("prompt_description") or ""),
                status="active",
                user_selectable=bool(entry.get("user_selectable", True)),
                filterable=bool(entry.get("filterable", True)),
                recommendable=bool(entry.get("recommendable", True)),
                entity_type=str(entry.get("entity_type") or ""),
                external_key=entry.get("external_key"),
            )
            taxonomy._event(  # noqa: SLF001 - this script is a governance boundary
                session,
                "activate",
                target_tag_id=tag.id,
                actor_id=actor_id,
                reason="approved taxonomy-v1 review import",
                payload={"review_code": entry["code"]},
            )
            session.commit()
            counts["created"] += 1
        else:
            if tag.kind != entry["kind"]:
                raise ValueError(f"existing code has a different facet: {entry['code']}")
            if tag.status != "active":
                tag.status = "active"
                tag.replacement_id = None
                session.add(tag)
                taxonomy._event(  # noqa: SLF001 - this script is a governance boundary
                    session,
                    "activate",
                    target_tag_id=tag.id,
                    actor_id=actor_id,
                    reason="approved taxonomy-v1 review reactivation",
                    payload={"review_code": entry["code"]},
                )
                session.commit()
            if (tag.name_zh, tag.name_en) != (entry["name_zh"], entry["name_en"]):
                tag = taxonomy.rename_tag(
                    session,
                    int(tag.id),
                    actor_id=actor_id,
                    name_zh=entry["name_zh"],
                    name_en=entry["name_en"],
                    reason="approved taxonomy-v1 review edit",
                )
            taxonomy.change_tag_flags(
                session,
                int(tag.id),
                actor_id=actor_id,
                user_selectable=bool(entry.get("user_selectable", True)),
                filterable=bool(entry.get("filterable", True)),
                recommendable=bool(entry.get("recommendable", True)),
                reason="approved taxonomy-v1 review flags",
            )
            taxonomy.change_tag_descriptions(
                session,
                int(tag.id),
                actor_id=actor_id,
                description=str(entry.get("description") or ""),
                prompt_description=str(entry.get("prompt_description") or ""),
                reason="approved taxonomy-v1 review guidance",
            )
            if tag.kind == "entity":
                taxonomy.change_entity_metadata(
                    session,
                    int(tag.id),
                    actor_id=actor_id,
                    entity_type=str(entry.get("entity_type") or ""),
                    external_key=entry.get("external_key"),
                    reason="approved taxonomy-v1 review entity metadata",
                )
            counts["updated"] += 1

        canonical = {
            taxonomy.normalize_label(value)
            for value in (tag.name_zh, tag.name_en)
            if value
        }
        labels = list(dict.fromkeys([*(entry.get("aliases") or []), *(entry.get("source_labels") or [])]))
        for label in labels:
            if not str(label).strip() or taxonomy.normalize_label(label) in canonical:
                continue
            normalized = taxonomy.normalize_label(label)
            existing_alias = session.exec(
                select(CmsTagAliasRecord).where(
                    CmsTagAliasRecord.kind == tag.kind,
                    CmsTagAliasRecord.normalized_alias == normalized,
                )
            ).first()
            taxonomy.add_alias(session, tag_id=int(tag.id), alias=str(label), alias_type="synonym")
            session.commit()
            counts["aliases"] += int(existing_alias is None)

        if not resolve_candidates:
            continue
        candidate_keys = entry.get("source_candidates") or []
        candidate_ids = {
            int(value) for value in entry.get("source_candidate_ids") or [] if str(value).isdigit()
        }
        if candidate_keys:
            candidates = []
            for key in candidate_keys:
                candidate = session.exec(
                    select(CmsTagCandidateRecord).where(
                        CmsTagCandidateRecord.proposed_kind == str(key.get("kind") or ""),
                        CmsTagCandidateRecord.normalized_label
                        == str(key.get("normalized_label") or ""),
                        CmsTagCandidateRecord.status.in_(("candidate", "reviewing")),
                    )
                ).first()
                if candidate is not None:
                    candidates.append(candidate)
        elif candidate_ids:
            candidates = list(
                session.exec(
                    select(CmsTagCandidateRecord).where(
                        CmsTagCandidateRecord.id.in_(candidate_ids),
                        CmsTagCandidateRecord.status.in_(("candidate", "reviewing")),
                    )
                ).all()
            )
        else:
            # Backward compatibility for the initial hand-authored review.
            # Newly generated artifacts carry portable facet+normalized keys.
            normalized_labels = {
                taxonomy.normalize_label(value) for value in labels if str(value).strip()
            }
            candidates = list(
                session.exec(
                    select(CmsTagCandidateRecord).where(
                        CmsTagCandidateRecord.normalized_label.in_(normalized_labels),
                        CmsTagCandidateRecord.status.in_(("candidate", "reviewing")),
                    )
                ).all()
            ) if normalized_labels else []
        for candidate in candidates:
            taxonomy.resolve_candidate_to_tag(
                session,
                int(candidate.id),
                target_tag_id=int(tag.id),
                actor_id=actor_id,
                reason="approved taxonomy-v1 review mapping",
            )
            counts["candidates_resolved"] += 1
    # Resolve hierarchy only after every canonical row exists; review entry
    # order must not affect whether a child can reference its parent.
    for entry in entries:
        parent_code = str(entry.get("parent_code") or "").strip()
        if not parent_code:
            continue
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == entry["code"])).one()
        parent = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == parent_code)).first()
        if parent is None or parent.kind != tag.kind:
            raise ValueError(f"parent_code does not resolve in the same facet: {entry['code']}")
        if tag.parent_id != parent.id:
            taxonomy.change_tag_parent(
                session,
                int(tag.id),
                parent_id=int(parent.id),
                actor_id=actor_id,
                reason="approved taxonomy-v1 review hierarchy",
            )
            counts["parents"] += 1
    return counts


def apply_remaining_decisions(
    session: Session,
    report: dict[str, Any],
    *,
    actor_id: str,
) -> dict[str, int]:
    counts = {"rejected": 0, "merged": 0}
    decisions: list[tuple[int | None, str, str, str, str]] = []
    for entry in report.get("entries") or []:
        if entry.get("decision") == "reject":
            keys = entry.get("source_candidates") or []
            ids = [int(value) for value in entry.get("source_candidate_ids") or []]
            if keys:
                decisions.extend(
                    (
                        None,
                        str(key.get("normalized_label") or ""),
                        str(key.get("kind") or ""),
                        "reject",
                        "",
                    )
                    for key in keys
                )
            elif ids:
                decisions.extend((candidate_id, "", "", "reject", "") for candidate_id in ids)
            else:
                decisions.extend(
                    (None, taxonomy.normalize_label(str(label)), "", "reject", "")
                    for label in entry.get("source_labels") or []
                )
    for item in report.get("unmapped_candidates") or []:
        decisions.append(
            (
                int(item["candidate_id"]) if item.get("candidate_id") is not None else None,
                str(item.get("normalized_label") or taxonomy.normalize_label(item.get("label") or "")),
                str(item.get("proposed_kind") or ""),
                str(item.get("decision") or ""),
                str(item.get("resolution_code") or ""),
            )
        )
    for candidate_id, normalized, kind, decision, resolution_code in decisions:
        if normalized and kind:
            candidate = session.exec(
                select(CmsTagCandidateRecord).where(
                    CmsTagCandidateRecord.proposed_kind == kind,
                    CmsTagCandidateRecord.normalized_label == normalized,
                    CmsTagCandidateRecord.status.in_(("candidate", "reviewing")),
                )
            ).first()
            candidates = [candidate] if candidate is not None else []
        elif candidate_id is not None:
            candidate = session.get(CmsTagCandidateRecord, candidate_id)
            candidates = [candidate] if candidate and candidate.status in {"candidate", "reviewing"} else []
        else:
            if not normalized:
                continue
            candidates = list(
                session.exec(
                    select(CmsTagCandidateRecord).where(
                        CmsTagCandidateRecord.normalized_label == normalized,
                        CmsTagCandidateRecord.status.in_(("candidate", "reviewing")),
                    )
                ).all()
            )
        for candidate in candidates:
            if decision == "reject":
                taxonomy.reject_candidate(
                    session,
                    int(candidate.id),
                    actor_id=actor_id,
                    reason="rejected in taxonomy-v1 product review",
                )
                counts["rejected"] += 1
            elif decision == "merge":
                target = session.exec(
                    select(CmsTagRecord).where(CmsTagRecord.code == resolution_code)
                ).first()
                if target is None:
                    raise ValueError(f"resolution_code does not exist: {resolution_code}")
                taxonomy.resolve_candidate_to_tag(
                    session,
                    int(candidate.id),
                    target_tag_id=int(target.id),
                    actor_id=actor_id,
                    reason="merged in taxonomy-v1 product review",
                )
                counts["merged"] += 1
    return counts


def save_review_receipt(
    session: Session,
    report: dict[str, Any],
    *,
    actor_id: str,
) -> None:
    coverage = report.get("coverage") or {}
    receipt = {
        "manifest_sha256": report.get("manifest_sha256"),
        "actor_id": actor_id,
        "reviewed_at": taxonomy.now_iso(),
        "review_basis": report.get("review_basis") or "evidence_bootstrap",
        "coverage_decision": report.get("coverage_decision") or "complete",
        "sampled_source_count": coverage.get("sampled_source_count", 0),
        "manifest_source_count": coverage.get("manifest_source_count", 0),
        "article_count": coverage.get("article_count", 0),
        "candidate_count": coverage.get("candidate_count", 0),
    }
    record = session.get(AppSettingRecord, taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY)
    if record is None:
        record = AppSettingRecord(key=taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY, value="")
    record.value = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    session.add(record)
    session.commit()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = json.loads(args.review.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    validate_review_catalog_binding(report, catalog)
    entries = approved_entries(report)
    validate_complete_review(report)
    if not args.apply:
        print(json.dumps({"mode": "validation-only", "approved_entries": len(entries)}, ensure_ascii=False))
        return 0
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            validate_target_candidate_coverage(session, report)
            counts = apply_review(session, entries, actor_id=args.actor)
            counts.update(apply_remaining_decisions(session, report, actor_id=args.actor))
            unresolved = session.exec(
                select(CmsTagCandidateRecord).where(
                    CmsTagCandidateRecord.status.in_(("candidate", "reviewing"))
                )
            ).first()
            if unresolved is not None:
                raise ValueError("review decisions did not resolve every Candidate in the target database")
            save_review_receipt(session, report, actor_id=args.actor)
    finally:
        engine.dispose()
    print(json.dumps({"mode": "applied", **counts}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
