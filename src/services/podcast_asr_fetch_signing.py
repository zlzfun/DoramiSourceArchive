"""Short-lived, provider-fetchable Podcast source-audio URL signatures.

This module deliberately owns no HTTP route.  It only produces and verifies a
small signed capability whose public origin comes from deployment config, never
from an inbound Host header. Callers must still enforce deployment privacy and
CAS integrity gates before returning audio bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlmodel import Session

import config
from services import credentials


_VERSION = 1
_PURPOSE = "podcast-asr-fetch"
ASR_FETCH_PATH = "/api/public/podcast-asr/source-audio"
_ALLOWED_METHODS = ("GET", "HEAD")
_PAYLOAD_KEYS = frozenset(
    {
        "artifact_id",
        "authority_id",
        "exp",
        "iat",
        "methods",
        "path",
        "processing_id",
        "purpose",
        "sha256",
        "v",
    }
)
_QUERY_KEYS = frozenset({"payload", "signature"})
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PodcastAsrFetchSignatureError(ValueError):
    """Uniform, non-sensitive failure for every capability verification error."""


@dataclass(frozen=True)
class PodcastAsrFetchClaims:
    processing_id: str
    artifact_id: str
    content_sha256: str
    authority_id: str
    canonical_path: str
    issued_at: int
    expires_at: int
    allowed_methods: tuple[str, ...] = _ALLOWED_METHODS


@dataclass(frozen=True)
class SignedPodcastAsrFetchUrl:
    """Generated URL container whose repr cannot leak the signed capability."""

    url: str = field(repr=False)
    expires_at: int
    min_remaining_seconds: int


def resolve_config(session: Session) -> config.PodcastAsrFetchConfig:
    """Resolve runtime KV overrides over the env/INI startup baseline."""

    values = credentials.resolve_values(
        session,
        credentials.PODCAST_ASR_FETCH_NAMESPACE,
        config.settings.podcast_asr_fetch,
    )
    return config.PodcastAsrFetchConfig(**values)


def field_sources(session: Session) -> dict[str, str]:
    """Expose provenance only; never expose the resolved signing secret."""

    return credentials.field_sources(
        session, credentials.PODCAST_ASR_FETCH_NAMESPACE
    )


def clear_previous_signing_secret(session: Session) -> None:
    """Remove only the temporary grace verification key from runtime KV."""

    credentials.clear_secret_fields(
        session,
        credentials.PODCAST_ASR_FETCH_NAMESPACE,
        ("previous_signing_secret",),
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or not _B64URL_RE.fullmatch(value):
        raise ValueError("invalid base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        raise ValueError("non-canonical JSON payload")
    return value


def _bounded_identifier(value: str, label: str) -> str:
    text = str(value or "")
    if (
        not text
        or len(text) > 256
        or not text.isascii()
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in text)
    ):
        raise ValueError(f"invalid {label}")
    return text


class PodcastAsrFetchUrlSigner:
    """Issue with the current key and verify with current plus one grace key."""

    def __init__(
        self,
        signing_config: config.PodcastAsrFetchConfig,
        *,
        authority_id: str,
    ) -> None:
        if not signing_config.configured:
            raise ValueError("Podcast ASR fetch signing is not configured")
        self._config = signing_config
        self._secret = signing_config.signing_secret.encode("utf-8")
        self._verification_secrets = tuple(
            secret.encode("utf-8")
            for secret in (
                signing_config.signing_secret,
                signing_config.previous_signing_secret,
            )
            if secret
        )
        self._authority_id = _bounded_identifier(authority_id, "authority_id")
        self._base_url = signing_config.public_base_url
        self._canonical_path = urlsplit(self._base_url).path or "/"
        if self._canonical_path != ASR_FETCH_PATH:
            raise ValueError(
                f"Podcast ASR fetch public_base_url path must be {ASR_FETCH_PATH}"
            )

    @property
    def min_remaining_seconds(self) -> int:
        return self._config.min_remaining_seconds

    def issue(
        self,
        *,
        processing_id: str,
        artifact_id: str,
        content_sha256: str,
        now: int | None = None,
        expires_at_cap: int | None = None,
    ) -> SignedPodcastAsrFetchUrl:
        """Create a URL capped by config and an optional artifact expiry."""

        if now is not None and (isinstance(now, bool) or not isinstance(now, int)):
            raise ValueError("now must be an integer Unix timestamp")
        if expires_at_cap is not None and (
            isinstance(expires_at_cap, bool)
            or not isinstance(expires_at_cap, int)
        ):
            raise ValueError("expires_at_cap must be an integer Unix timestamp")
        issued_at = int(time.time()) if now is None else now
        processing = _bounded_identifier(processing_id, "processing_id")
        artifact = _bounded_identifier(artifact_id, "artifact_id")
        digest = str(content_sha256 or "")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("content_sha256 must be lowercase SHA-256 hex")
        configured_expiry = issued_at + self._config.url_ttl_seconds
        expires_at = (
            configured_expiry
            if expires_at_cap is None
            else min(configured_expiry, expires_at_cap)
        )
        if expires_at - issued_at < self._config.min_remaining_seconds:
            raise ValueError("bounded capability lifetime is too short")
        payload = {
            "artifact_id": artifact,
            "authority_id": self._authority_id,
            "exp": expires_at,
            "iat": issued_at,
            "methods": list(_ALLOWED_METHODS),
            "path": self._canonical_path,
            "processing_id": processing,
            "purpose": _PURPOSE,
            "sha256": digest,
            "v": _VERSION,
        }
        payload_raw = _canonical_json(payload)
        payload_text = _b64url_encode(payload_raw)
        signature = _b64url_encode(
            hmac.new(
                self._secret,
                payload_raw,
                hashlib.sha256,
            ).digest()
        )
        query = urlencode(
            (("payload", payload_text), ("signature", signature)),
            doseq=False,
        )
        return SignedPodcastAsrFetchUrl(
            url=f"{self._base_url}?{query}",
            expires_at=expires_at,
            min_remaining_seconds=self._config.min_remaining_seconds,
        )

    def verify(
        self,
        *,
        method: str,
        canonical_path: str,
        raw_query: str | bytes,
        now: int | None = None,
    ) -> PodcastAsrFetchClaims:
        """Verify a request capability, collapsing every failure to one error."""

        try:
            return self._verify(
                method=method,
                canonical_path=canonical_path,
                raw_query=raw_query,
                now=now,
            )
        except (TypeError, ValueError):
            raise PodcastAsrFetchSignatureError(
                "invalid Podcast ASR fetch authorization"
            ) from None

    def _verify(
        self,
        *,
        method: str,
        canonical_path: str,
        raw_query: str | bytes,
        now: int | None,
    ) -> PodcastAsrFetchClaims:
        if now is not None and (isinstance(now, bool) or not isinstance(now, int)):
            raise TypeError("now must be an integer Unix timestamp")
        current_time = int(time.time()) if now is None else now
        request_method = str(method or "").upper()
        request_path = str(canonical_path or "")
        if isinstance(raw_query, bytes):
            query_text = raw_query.decode("ascii")
        elif isinstance(raw_query, str):
            query_text = raw_query
        else:
            raise TypeError("raw_query must be text or bytes")
        if len(query_text) > 4096:
            raise ValueError("query is too large")
        pairs = parse_qsl(
            query_text,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=2,
        )
        if (
            len(pairs) != 2
            or frozenset(key for key, _ in pairs) != _QUERY_KEYS
            or len({key for key, _ in pairs}) != 2
        ):
            raise ValueError("invalid query shape")
        query = dict(pairs)
        payload_text = query["payload"]
        canonical_query = urlencode(
            (("payload", payload_text), ("signature", query["signature"])),
            doseq=False,
        )
        if query_text != canonical_query:
            raise ValueError("non-canonical query")
        payload_raw = _b64url_decode(payload_text)
        supplied_signature = _b64url_decode(query["signature"])
        if len(supplied_signature) != hashlib.sha256().digest_size:
            raise ValueError("invalid signature length")
        signature_matches = False
        for secret in self._verification_secrets:
            expected_signature = hmac.new(
                secret,
                payload_raw,
                hashlib.sha256,
            ).digest()
            # Never short-circuit: when a grace secret is configured, both
            # comparisons are always performed regardless of which key signed.
            signature_matches |= hmac.compare_digest(
                supplied_signature, expected_signature
            )
        if not signature_matches:
            raise ValueError("signature mismatch")
        payload = _strict_json_object(payload_raw)
        if frozenset(payload) != _PAYLOAD_KEYS:
            raise ValueError("invalid payload shape")
        if (
            payload["v"] != _VERSION
            or isinstance(payload["v"], bool)
            or payload["purpose"] != _PURPOSE
            or payload["methods"] != list(_ALLOWED_METHODS)
            or payload["path"] != self._canonical_path
            or request_path != self._canonical_path
            or request_method not in _ALLOWED_METHODS
            or payload["authority_id"] != self._authority_id
        ):
            raise ValueError("capability binding mismatch")
        processing = _bounded_identifier(payload["processing_id"], "processing_id")
        artifact = _bounded_identifier(payload["artifact_id"], "artifact_id")
        authority = _bounded_identifier(payload["authority_id"], "authority_id")
        digest = payload["sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError("invalid digest")
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if (
            not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or issued_at < 0
        ):
            raise ValueError("invalid timestamps")
        lifetime = expires_at - issued_at
        if (
            lifetime < self._config.min_remaining_seconds
            or lifetime > self._config.url_ttl_seconds
            or issued_at > current_time + self._config.clock_skew_seconds
            or expires_at < current_time - self._config.clock_skew_seconds
            or expires_at
            > current_time
            + self._config.url_ttl_seconds
            + self._config.clock_skew_seconds
        ):
            raise ValueError("invalid capability lifetime")
        return PodcastAsrFetchClaims(
            processing_id=processing,
            artifact_id=artifact,
            content_sha256=digest,
            authority_id=authority,
            canonical_path=self._canonical_path,
            issued_at=issued_at,
            expires_at=expires_at,
        )


def resolve_signer(
    session: Session,
    *,
    podcast_config: config.PodcastConfig | None = None,
) -> PodcastAsrFetchUrlSigner:
    """Build the signer only for the enabled external ASR authority.

    This check intentionally happens after runtime KV resolution: deployments
    may keep the secret in the credential cabinet, while every actual ASR
    submit/sign path still fails closed if either authority or signing config is
    incomplete.
    """

    podcast = podcast_config or config.settings.podcast
    if not (
        podcast.processing_enabled
        and podcast.installation == "external"
        and "asr" in podcast.allowed_stages
    ):
        raise ValueError("external Podcast ASR authority is not enabled")
    return PodcastAsrFetchUrlSigner(
        resolve_config(session),
        authority_id=podcast.authority_id,
    )
