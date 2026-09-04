"""Deployment-time reconciliation for the repository-approved Taxonomy v1.

This module is deliberately independent from ``[runtime] role``.  Both sides of
the production split run with ``role=all``; operators must explicitly select
``authority`` (install the approved catalog), ``replica`` (Archive Sync only),
or ``manual`` (no deployment-time taxonomy action).

The authority reconciler validates the complete target before it writes, then
installs missing catalog rows and the review receipt in one transaction.  A
matching receipt is a no-op, while a different/malformed receipt or conflicting
pre-existing taxonomy fails startup closed.  Publishing version 1 remains an
explicit human governance action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import PROJECT_ROOT, TaxonomyDeploymentConfig
from models.db import (
    AppSettingRecord,
    CmsTagAliasRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    TaxonomyVersionRecord,
)
from services import taxonomy


DEFAULT_CATALOG = PROJECT_ROOT / "config" / "taxonomy-v1-approved-catalog.json"
DEPLOYMENT_MODES = frozenset({"manual", "authority", "replica"})
DEPLOYMENT_ACTOR = "taxonomy-v1-deployment-reconciler"


class TaxonomyDeploymentError(ValueError):
    """A deployment configuration or catalog/database conflict."""


def manifest_core(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in catalog.items()
        if key not in {"manifest_sha256", "coverage"}
    }


def compute_manifest_sha256(catalog: Mapping[str, Any]) -> str:
    payload = json.dumps(
        manifest_core(catalog),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    expected_digest = str(catalog.get("manifest_sha256") or "")
    actual_digest = compute_manifest_sha256(catalog)
    if not hmac.compare_digest(expected_digest, actual_digest):
        raise TaxonomyDeploymentError(
            "taxonomy catalog manifest_sha256 does not match its approved content"
        )
    if catalog.get("schema_version") != "taxonomy-v1-approved-catalog-v2":
        raise TaxonomyDeploymentError("unsupported taxonomy v1 catalog schema")
    if catalog.get("status") != "product_approved":
        raise TaxonomyDeploymentError("taxonomy catalog must be product_approved")
    if catalog.get("review_basis") != "label_set_only":
        raise TaxonomyDeploymentError("deployment catalog must use label_set_only review")
    if catalog.get("coverage_decision") != "not_applicable":
        raise TaxonomyDeploymentError("label-set-only catalog must use not_applicable coverage")
    if catalog.get("unmatched_candidate_policy") != "fail":
        raise TaxonomyDeploymentError("deployment catalog must fail on unmatched Candidates")
    if not isinstance(catalog.get("coverage") or {}, Mapping):
        raise TaxonomyDeploymentError("taxonomy catalog coverage must be an object")

    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise TaxonomyDeploymentError("taxonomy catalog has no approved entries")
    codes: set[str] = set()
    canonical_names: dict[tuple[str, str], str] = {}
    alias_owners: dict[tuple[str, str], str] = {}
    by_code: dict[str, Mapping[str, Any]] = {}
    for raw in entries:
        if not isinstance(raw, Mapping) or raw.get("decision") not in {"accept", "edit"}:
            raise TaxonomyDeploymentError("every catalog entry must be an approved mapping")
        code = str(raw.get("code") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        name_zh = str(raw.get("name_zh") or "").strip()
        name_en = str(raw.get("name_en") or "").strip()
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", code)
            or code in codes
            or kind not in {"topic", "industry", "entity"}
            or not (name_zh or name_en)
        ):
            raise TaxonomyDeploymentError(f"invalid or duplicate catalog entry: {code!r}")
        if kind == "entity":
            try:
                taxonomy.validate_entity_type(
                    str(raw.get("entity_type") or ""), required=True
                )
            except taxonomy.TaxonomyError as exc:
                raise TaxonomyDeploymentError(
                    f"invalid Entity metadata in catalog: {code}"
                ) from exc
        elif raw.get("entity_type") or raw.get("external_key"):
            raise TaxonomyDeploymentError(
                f"non-Entity catalog entry carries Entity metadata: {code}"
            )
        codes.add(code)
        by_code[code] = raw
        normalized = taxonomy.normalize_label(name_zh or name_en or code)
        key = (kind, normalized)
        if key in canonical_names:
            raise TaxonomyDeploymentError(
                f"duplicate normalized catalog name: {code} and {canonical_names[key]}"
            )
        canonical_names[key] = code

    for code, raw in by_code.items():
        kind = str(raw["kind"])
        parent_code = str(raw.get("parent_code") or "").strip()
        if parent_code:
            parent = by_code.get(parent_code)
            if parent is None or parent.get("kind") != kind or parent_code == code:
                raise TaxonomyDeploymentError(f"invalid parent_code for {code}")
        for normalized, data in _expected_aliases(raw).items():
            key = (kind, normalized)
            canonical_owner = canonical_names.get(key)
            previous = alias_owners.setdefault(key, code)
            if (canonical_owner and canonical_owner != code) or previous != code:
                raise TaxonomyDeploymentError(
                    f"conflicting catalog alias: {data['alias']!r}"
                )


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaxonomyDeploymentError(f"cannot load taxonomy catalog: {path}") from exc
    if not isinstance(catalog, dict):
        raise TaxonomyDeploymentError("taxonomy catalog must be a JSON object")
    validate_catalog(catalog)
    return catalog


def _expected_tag(entry: Mapping[str, Any]) -> dict[str, Any]:
    name_zh = str(entry.get("name_zh") or "").strip()
    name_en = str(entry.get("name_en") or "").strip()
    return {
        "kind": str(entry["kind"]),
        "name_zh": name_zh,
        "name_en": name_en,
        "normalized_name": taxonomy.normalize_label(name_zh or name_en or entry["code"]),
        "description": str(entry.get("description") or "").strip(),
        "prompt_description": str(entry.get("prompt_description") or "").strip(),
        "status": "active",
        "replacement_id": None,
        "entity_type": str(entry.get("entity_type") or "").strip(),
        "external_key": str(entry.get("external_key") or "").strip() or None,
        "user_selectable": bool(entry.get("user_selectable", True)),
        "filterable": bool(entry.get("filterable", True)),
        "recommendable": bool(entry.get("recommendable", True)),
        "activation_mode": "manual",
        "taxonomy_version": 0,
    }


def _expected_aliases(entry: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    name_zh = str(entry.get("name_zh") or "").strip()
    name_en = str(entry.get("name_en") or "").strip()
    primary = taxonomy.normalize_label(name_zh or name_en or entry["code"])
    canonical = {
        taxonomy.normalize_label(value)
        for value in (name_zh, name_en)
        if value
    }
    result: dict[str, dict[str, str]] = {}
    for locale, value in (("zh", name_zh), ("en", name_en)):
        normalized = taxonomy.normalize_label(value)
        if value and normalized != primary:
            result[normalized] = {
                "alias": value,
                "alias_type": "translation",
                "locale": locale,
            }
    for value in entry.get("aliases") or []:
        alias = str(value or "").strip()
        normalized = taxonomy.normalize_label(alias)
        if alias and normalized not in canonical and normalized not in result:
            result[normalized] = {
                "alias": alias,
                "alias_type": "synonym",
                "locale": "",
            }
    return result


def _receipt(session: Session) -> dict[str, Any] | None:
    row = session.get(AppSettingRecord, taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY)
    if row is None:
        return None
    try:
        value = json.loads(row.value or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise TaxonomyDeploymentError("taxonomy v1 receipt is malformed") from exc
    if not isinstance(value, dict):
        raise TaxonomyDeploymentError("taxonomy v1 receipt is malformed")
    return value


def _validate_existing_subset(
    session: Session,
    entries: list[Mapping[str, Any]],
) -> dict[str, CmsTagRecord]:
    expected_by_code = {str(entry["code"]): entry for entry in entries}
    existing = list(session.exec(select(CmsTagRecord)).all())
    unexpected = sorted(row.code for row in existing if row.code not in expected_by_code)
    if unexpected:
        raise TaxonomyDeploymentError(
            f"database contains taxonomy tags outside approved v1: {unexpected[:10]}"
        )
    by_code = {row.code: row for row in existing}
    conflicts: list[str] = []
    for code, row in by_code.items():
        expected = _expected_tag(expected_by_code[code])
        if any(getattr(row, field) != value for field, value in expected.items()):
            conflicts.append(code)
    if conflicts:
        raise TaxonomyDeploymentError(
            f"database taxonomy conflicts with approved v1: {sorted(conflicts)[:10]}"
        )

    expected_aliases = {
        (str(entry["kind"]), normalized): (str(entry["code"]), data)
        for entry in entries
        for normalized, data in _expected_aliases(entry).items()
    }
    for alias in session.exec(select(CmsTagAliasRecord)).all():
        owner = next((code for code, tag in by_code.items() if tag.id == alias.tag_id), None)
        expected = expected_aliases.get((alias.kind, alias.normalized_alias))
        if (
            owner is None
            or expected is None
            or owner != expected[0]
            or alias.alias != expected[1]["alias"]
            or alias.alias_type != expected[1]["alias_type"]
            or alias.locale != expected[1]["locale"]
        ):
            raise TaxonomyDeploymentError(
                f"database alias conflicts with approved v1: {alias.alias!r}"
            )
    return by_code


def reconcile_catalog_session(
    session: Session,
    catalog: Mapping[str, Any],
    *,
    actor_id: str = DEPLOYMENT_ACTOR,
) -> dict[str, Any]:
    """Reconcile one authority database; caller owns the transaction."""

    validate_catalog(catalog)
    digest = str(catalog["manifest_sha256"])
    receipt = _receipt(session)
    if receipt is not None:
        receipt_digest = str(receipt.get("manifest_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", receipt_digest):
            raise TaxonomyDeploymentError("taxonomy v1 receipt is malformed")
        if not hmac.compare_digest(receipt_digest, digest):
            raise TaxonomyDeploymentError(
                "installed taxonomy v1 receipt conflicts with packaged catalog"
            )
        if (
            not str(receipt.get("actor_id") or "").strip()
            or not str(receipt.get("reviewed_at") or "").strip()
            or receipt.get("review_basis") != catalog.get("review_basis")
            or receipt.get("coverage_decision") != catalog.get("coverage_decision")
        ):
            raise TaxonomyDeploymentError("taxonomy v1 receipt is malformed")
        return {"status": "unchanged", "manifest_sha256": digest, "created": 0}

    actor_id = str(actor_id or "").strip()
    if not actor_id:
        raise TaxonomyDeploymentError("taxonomy deployment actor_id must not be empty")

    versions = list(session.exec(select(TaxonomyVersionRecord)).all())
    if versions:
        raise TaxonomyDeploymentError(
            "taxonomy version exists without the approved v1 receipt; recover manually"
        )
    unresolved = session.exec(
        select(CmsTagCandidateRecord).where(
            CmsTagCandidateRecord.status.in_(("candidate", "reviewing"))
        )
    ).first()
    if unresolved is not None:
        raise TaxonomyDeploymentError(
            "authority database has unresolved Candidates; use the governance migration"
        )

    entries = [entry for entry in catalog["entries"] if isinstance(entry, Mapping)]
    by_code = _validate_existing_subset(session, entries)
    created = 0
    stamp = taxonomy.now_iso()
    for entry in entries:
        code = str(entry["code"])
        if code in by_code:
            continue
        expected = _expected_tag(entry)
        tag = taxonomy.create_tag(
            session,
            code=code,
            kind=expected["kind"],
            name_zh=expected["name_zh"],
            name_en=expected["name_en"],
            description=expected["description"],
            prompt_description=expected["prompt_description"],
            status="active",
            user_selectable=expected["user_selectable"],
            filterable=expected["filterable"],
            recommendable=expected["recommendable"],
            activation_mode="manual",
            entity_type=expected["entity_type"],
            external_key=expected["external_key"],
        )
        taxonomy._event(  # noqa: SLF001 - deployment import is a governance boundary
            session,
            "activate",
            target_tag_id=tag.id,
            actor_type="system",
            actor_id=actor_id,
            reason="approved taxonomy-v1 deployment import",
            payload={"review_code": code, "manifest_sha256": digest},
        )
        by_code[code] = tag
        created += 1

    for entry in entries:
        tag = by_code[str(entry["code"])]
        for data in _expected_aliases(entry).values():
            taxonomy.add_alias(
                session,
                tag_id=int(tag.id),
                alias=data["alias"],
                alias_type=data["alias_type"],
                locale=data["locale"],
            )
    for entry in entries:
        parent_code = str(entry.get("parent_code") or "").strip()
        tag = by_code[str(entry["code"])]
        expected_parent_id = int(by_code[parent_code].id) if parent_code else None
        if tag.parent_id != expected_parent_id:
            tag.parent_id = expected_parent_id
            tag.updated_at = stamp
            session.add(tag)
    taxonomy.touch_taxonomy_sync_revision(session)

    coverage = catalog.get("coverage") or {}
    session.add(AppSettingRecord(
        key=taxonomy.TAXONOMY_V1_REVIEW_RECEIPT_KEY,
        value=json.dumps(
            {
                "manifest_sha256": digest,
                "catalog_schema_version": catalog.get("schema_version"),
                "actor_id": actor_id,
                "reviewed_at": stamp,
                "review_basis": catalog.get("review_basis"),
                "coverage_decision": catalog.get("coverage_decision"),
                "sampled_source_count": coverage.get("sampled_source_count", 0),
                "manifest_source_count": coverage.get("manifest_source_count", 0),
                "article_count": coverage.get("article_count", 0),
                "candidate_count": coverage.get("candidate_count", 0),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    ))
    session.flush()
    return {
        "status": "installed_awaiting_publish",
        "manifest_sha256": digest,
        "created": created,
        "total": len(entries),
    }


def reconcile_approved_taxonomy_v1(
    engine: Engine,
    catalog_path: Path = DEFAULT_CATALOG,
    *,
    actor_id: str = DEPLOYMENT_ACTOR,
) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    with Session(engine) as session:
        try:
            result = reconcile_catalog_session(session, catalog, actor_id=actor_id)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def run_taxonomy_deployment(
    database_url: str,
    config: TaxonomyDeploymentConfig,
    *,
    actor_id: str = DEPLOYMENT_ACTOR,
) -> dict[str, Any]:
    mode = str(config.mode or "").strip().lower()
    if mode not in DEPLOYMENT_MODES:
        raise TaxonomyDeploymentError(
            f"invalid taxonomy deployment mode {mode!r}; expected authority, replica, or manual"
        )
    if mode != "authority":
        return {"status": mode, "action": "none"}
    engine = create_engine(database_url)
    try:
        return reconcile_approved_taxonomy_v1(
            engine,
            Path(config.catalog_path),
            actor_id=actor_id,
        )
    finally:
        engine.dispose()


__all__ = [
    "DEFAULT_CATALOG",
    "DEPLOYMENT_MODES",
    "TaxonomyDeploymentError",
    "compute_manifest_sha256",
    "load_catalog",
    "manifest_core",
    "reconcile_approved_taxonomy_v1",
    "reconcile_catalog_session",
    "run_taxonomy_deployment",
    "validate_catalog",
]
