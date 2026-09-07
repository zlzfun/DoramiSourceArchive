"""Compatibility exports for generic taxonomy review/recovery scripts."""

import hmac

from services.taxonomy_deployment import (
    compute_manifest_sha256,
    manifest_core,
)


def validate_manifest(catalog):
    """Keep generic review tools digest-only while sharing canonical hashing."""

    expected = str(catalog.get("manifest_sha256") or "")
    actual = compute_manifest_sha256(catalog)
    if not hmac.compare_digest(expected, actual):
        raise ValueError(
            "taxonomy catalog manifest_sha256 does not match its approved content"
        )


__all__ = ["compute_manifest_sha256", "manifest_core", "validate_manifest"]
