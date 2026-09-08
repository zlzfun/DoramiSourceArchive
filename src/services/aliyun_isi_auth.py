"""Aliyun POP signing and short-lived NLS token management.

The recording-file ASR API and CreateToken use POP HMAC-SHA1 requests.  Long
text TTS does not: it consumes the NLS token returned here.  Keeping these
mechanisms in one small module prevents accidental credential mixing while
letting tests verify the wire contract without a live provider call.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote
from urllib.parse import urlsplit

import httpx

from config import AliyunIsiConfig
from services.http_safety import install_http_query_redaction_filters


install_http_query_redaction_filters()


def _percent_encode(value: Any) -> str:
    return quote(str(value), safe="~-._")


def canonicalized_query(parameters: Mapping[str, Any]) -> str:
    return "&".join(
        f"{_percent_encode(key)}={_percent_encode(parameters[key])}"
        for key in sorted(parameters)
    )


def pop_signature(
    method: str,
    parameters: Mapping[str, Any],
    access_key_secret: str,
) -> str:
    """Return the base64 POP signature (before form/query percent encoding)."""

    canonical = canonicalized_query(parameters)
    string_to_sign = (
        f"{str(method).upper()}&{_percent_encode('/')}&{_percent_encode(canonical)}"
    )
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class AliyunIsiError(RuntimeError):
    """Base error whose message never contains provider payloads or secrets."""


class AliyunIsiConfigurationError(AliyunIsiError):
    pass


class AliyunIsiTransportError(AliyunIsiError):
    pass


class AliyunIsiProtocolError(AliyunIsiError):
    def __init__(
        self,
        *,
        action: str,
        status_code: int | None = None,
        provider_code: str = "",
        request_id: str = "",
    ) -> None:
        self.action = action
        self.status_code = status_code
        self.provider_error_kind = _provider_error_kind(provider_code)
        safe_code = _identifier_fingerprint(provider_code)
        safe_request_id = _identifier_fingerprint(request_id)
        self.provider_code_ref = safe_code
        self.request_id_ref = safe_request_id
        details = [f"action={action}"]
        if status_code is not None:
            details.append(f"status={status_code}")
        if safe_code:
            details.append(f"code_ref={safe_code}")
        if safe_request_id:
            details.append(f"request_ref={safe_request_id}")
        super().__init__(f"Aliyun ISI protocol error ({', '.join(details)})")


def _identifier_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _provider_error_kind(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized.startswith("throttling"):
        return "throttled"
    return ""


@dataclass(frozen=True)
class NlsToken:
    value: str = field(default="", repr=False)
    expires_at: int = 0

    def is_valid_at(self, unix_seconds: int, *, skew_seconds: int = 0) -> bool:
        return bool(self.value and self.expires_at > int(unix_seconds) + skew_seconds)


@dataclass(frozen=True)
class NlsRefreshClaim:
    acquired: bool
    retry_at: int = 0
    token: NlsToken = field(default_factory=NlsToken, repr=False)


class AliyunPopClient:
    """Small HTTPS POP client implementing the documented RPC signing scheme."""

    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        http_client: httpx.Client | None = None,
        nonce_factory: Callable[[], str] | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        if not config.ak_configured:
            raise AliyunIsiConfigurationError("Aliyun ISI AK/SK are not configured")
        self._config = config
        self._http_client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = http_client is None
        self._nonce_factory = nonce_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    def close(self) -> None:
        if self._owns_client:
            self._http_client.close()

    def __enter__(self) -> "AliyunPopClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def call(
        self,
        *,
        action: str,
        version: str,
        url: str,
        method: str = "POST",
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_method = str(method or "").upper()
        if request_method not in {"GET", "POST"}:
            raise ValueError("Aliyun POP method must be GET or POST")
        parsed_url = urlsplit(str(url or ""))
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Aliyun POP endpoint must be a clean HTTPS URL")
        reserved = {
            "AccessKeyId",
            "Action",
            "Format",
            "Signature",
            "SignatureMethod",
            "SignatureNonce",
            "SignatureVersion",
            "SecurityToken",
            "Timestamp",
            "Version",
        }
        supplied = dict(parameters or {})
        conflict = sorted(reserved & set(supplied))
        if conflict:
            raise ValueError(
                f"Aliyun POP parameters cannot override: {', '.join(conflict)}"
            )
        timestamp = self._clock().astimezone(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        signed: dict[str, Any] = {
            "AccessKeyId": self._config.access_key_id,
            "Action": action,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": self._nonce_factory(),
            "SignatureVersion": "1.0",
            "Timestamp": timestamp,
            "Version": version,
        }
        if self._config.security_token:
            signed["SecurityToken"] = self._config.security_token
        signed.update(supplied)
        signed["Signature"] = pop_signature(
            request_method,
            signed,
            self._config.access_key_secret,
        )
        try:
            response = self._http_client.request(
                request_method,
                url,
                params=signed if request_method == "GET" else None,
                data=signed if request_method == "POST" else None,
                headers={"Accept": "application/json"},
                # An injected client may have follow_redirects=True.  POP
                # signatures and credentials are scoped to the configured
                # endpoint and must never cross a redirect boundary.
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise AliyunIsiTransportError(
                f"Aliyun ISI transport failed (action={action})"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            raise AliyunIsiProtocolError(
                action=action, status_code=response.status_code
            ) from None
        if not isinstance(payload, dict):
            raise AliyunIsiProtocolError(
                action=action, status_code=response.status_code
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise AliyunIsiProtocolError(
                action=action,
                status_code=response.status_code,
                provider_code=str(payload.get("Code") or payload.get("code") or ""),
                request_id=str(payload.get("RequestId") or payload.get("request_id") or ""),
            )
        return payload


class NlsTokenStore(Protocol):
    """Private durable control surface used by the future TTS worker."""

    def load(
        self,
        fallback: NlsToken,
        *,
        now: int,
        skew_seconds: int,
    ) -> NlsToken: ...

    def claim_refresh(
        self,
        owner: str,
        fallback: NlsToken,
        *,
        now: int,
        skew_seconds: int,
        lease_seconds: int,
    ) -> NlsRefreshClaim: ...

    def persist_refreshed(
        self,
        owner: str,
        token: NlsToken,
        *,
        now: int,
        minimum_validity_seconds: int,
    ) -> NlsToken: ...

    def abandon_refresh(self, owner: str) -> bool: ...

    def invalidate(self, token: NlsToken, *, now: int) -> bool: ...


class NlsTokenManager:
    """Token cache with optional cross-process refresh and rejection control.

    ``token_store`` is required when wiring a durable TTS worker.  Keeping it
    optional preserves the isolated wire adapter and test use cases while the
    ``digest_audio`` target remains closed.
    """

    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        pop_client: AliyunPopClient | None = None,
        clock: Callable[[], int] | None = None,
        on_refresh: Callable[[NlsToken], None] | None = None,
        token_store: NlsTokenStore | None = None,
        refresh_owner: str | None = None,
        refresh_lease_seconds: int | None = None,
        refresh_wait_seconds: float = 0.05,
        refresh_wait_timeout_seconds: float | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: int(time.time()))
        self._on_refresh = on_refresh
        self._lock = threading.Lock()
        self._token = NlsToken(config.access_token, config.token_expires_at)
        self._pop_client = pop_client
        self._owns_pop_client = pop_client is None
        self._token_store = token_store
        self._refresh_owner = refresh_owner or uuid.uuid4().hex
        request_timeout = max(1, int(config.request_timeout_seconds))
        self._refresh_lease_seconds = int(
            refresh_lease_seconds or max(30, request_timeout + 5)
        )
        self._refresh_wait_seconds = float(refresh_wait_seconds)
        self._refresh_wait_timeout_seconds = float(
            refresh_wait_timeout_seconds
            if refresh_wait_timeout_seconds is not None
            else request_timeout
        )
        self._sleeper = sleeper or time.sleep
        self._monotonic_clock = monotonic_clock or time.monotonic
        if (
            self._refresh_lease_seconds <= 0
            or self._refresh_wait_seconds <= 0
            or self._refresh_wait_timeout_seconds <= 0
        ):
            raise ValueError("Aliyun NLS token refresh timing must be positive")

    def close(self) -> None:
        if self._owns_pop_client and self._pop_client is not None:
            self._pop_client.close()

    def __enter__(self) -> "NlsTokenManager":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self) -> NlsToken:
        if self._token_store is not None:
            return self._get_durable()
        if self._token.is_valid_at(
            self._clock(), skew_seconds=self._config.token_refresh_skew_seconds
        ):
            return self._token
        with self._lock:
            if self._token.is_valid_at(
                self._clock(), skew_seconds=self._config.token_refresh_skew_seconds
            ):
                return self._token
            refreshed = self._refresh_from_provider()
            if self._on_refresh is not None:
                self._on_refresh(refreshed)
            self._token = refreshed
            return refreshed

    def _refresh_from_provider(self) -> NlsToken:
        if not self._config.ak_configured:
            raise AliyunIsiConfigurationError(
                "Aliyun ISI token is expired and AK/SK refresh is unavailable"
            )
        client = self._pop_client
        if client is None:
            client = AliyunPopClient(self._config)
            self._pop_client = client
        payload = client.call(
            action="CreateToken",
            version="2019-02-28",
            url=self._config.token_url,
            method="POST",
            parameters={"RegionId": self._config.region_id},
        )
        token_payload = payload.get("Token")
        if not isinstance(token_payload, dict):
            raise AliyunIsiProtocolError(
                action="CreateToken",
                provider_code=str(payload.get("Code") or "missing_token"),
                request_id=str(payload.get("RequestId") or ""),
            )
        value = str(token_payload.get("Id") or "").strip()
        try:
            expires_at = int(token_payload.get("ExpireTime"))
        except (TypeError, ValueError):
            raise AliyunIsiProtocolError(
                action="CreateToken",
                provider_code="invalid_expiry",
                request_id=str(payload.get("RequestId") or ""),
            ) from None
        refreshed = NlsToken(value, expires_at)
        if not refreshed.is_valid_at(
            self._clock(), skew_seconds=self._config.token_refresh_skew_seconds
        ):
            raise AliyunIsiProtocolError(
                action="CreateToken",
                provider_code="invalid_token_lifetime",
                request_id=str(payload.get("RequestId") or ""),
            )
        return refreshed

    def _get_durable(self) -> NlsToken:
        assert self._token_store is not None
        with self._lock:
            deadline = self._monotonic_clock() + self._refresh_wait_timeout_seconds
            fallback = NlsToken(
                self._config.access_token,
                self._config.token_expires_at,
            )
            while True:
                now = int(self._clock())
                loaded = self._token_store.load(
                    fallback,
                    now=now,
                    skew_seconds=self._config.token_refresh_skew_seconds,
                )
                if loaded.value:
                    self._token = loaded
                    return loaded
                claim = self._token_store.claim_refresh(
                    self._refresh_owner,
                    fallback,
                    now=now,
                    skew_seconds=self._config.token_refresh_skew_seconds,
                    lease_seconds=self._refresh_lease_seconds,
                )
                if claim.token.value:
                    self._token = claim.token
                    return claim.token
                if claim.acquired:
                    break
                if self._monotonic_clock() >= deadline:
                    raise AliyunIsiConfigurationError(
                        "Aliyun ISI token refresh is already in progress"
                    )
                self._sleeper(self._refresh_wait_seconds)

            try:
                refreshed = self._refresh_from_provider()
            except Exception:
                # No valid token was returned, so another owner may safely retry.
                try:
                    self._token_store.abandon_refresh(self._refresh_owner)
                except Exception:
                    # A failed cleanup is safe: the lease expires by design.
                    # Preserve the useful provider failure without attaching
                    # either control state or storage exception details.
                    pass
                raise
            # Once CreateToken succeeds, never release the lease on a failed
            # database commit: the uncommitted token must not be used and the
            # lease's TTL is the crash-recovery boundary.
            persisted = self._token_store.persist_refreshed(
                self._refresh_owner,
                refreshed,
                now=int(self._clock()),
                minimum_validity_seconds=self._config.token_refresh_skew_seconds,
            )
            if self._on_refresh is not None:
                self._on_refresh(persisted)
            self._token = persisted
            return persisted

    def invalidate(self, expected_value: str | None = None) -> bool:
        """Compare-and-discard a rejected token without racing a newer refresh."""

        with self._lock:
            if expected_value is not None and not hmac.compare_digest(
                self._token.value,
                str(expected_value),
            ):
                return False
            rejected = self._token
            self._token = NlsToken()
            if self._token_store is not None:
                self._token_store.invalidate(rejected, now=int(self._clock()))
            return True
