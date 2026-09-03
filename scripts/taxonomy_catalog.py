"""Canonical hashing helpers for repository-approved taxonomy catalogs."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def manifest_core(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in catalog.items()
        if key not in {"manifest_sha256", "coverage"}
    }


def compute_manifest_sha256(catalog: dict[str, Any]) -> str:
    payload = json.dumps(
        manifest_core(catalog),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_manifest(catalog: dict[str, Any]) -> None:
    expected = str(catalog.get("manifest_sha256") or "")
    actual = compute_manifest_sha256(catalog)
    if not hmac.compare_digest(expected, actual):
        raise ValueError(
            "taxonomy catalog manifest_sha256 does not match its approved content"
        )


__all__ = ["compute_manifest_sha256", "manifest_core", "validate_manifest"]
