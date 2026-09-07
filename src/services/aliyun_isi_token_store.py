"""Durable coordination for Aliyun NLS tokens.

The control state deliberately lives outside the ordinary credential namespace:
it contains an ephemeral provider token, rejection tombstones and a refresh
lease.  Callers must never return the blob through configuration APIs or log it.

The whole JSON value is updated with compare-and-swap.  That matters for two
independent invalidations: a last-writer-wins setting update could otherwise
silently discard one tombstone and revive a rejected env/INI fallback token.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from sqlalchemy import insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session

from models.db import AppSettingRecord
from services.aliyun_isi_auth import NlsRefreshClaim, NlsToken


NLS_TOKEN_CONTROL_KEY = "aliyun_isi:nls_token_control:v1"
CONTROL_VERSION = 1
MAX_TOMBSTONES = 64
DEFAULT_MAX_TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class NlsTokenControlError(RuntimeError):
    """Fixed-message failure that never exposes control state or token data."""


class NlsTokenControlConflict(NlsTokenControlError):
    pass


@dataclass(frozen=True)
class _Tombstone:
    digest: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class _Lease:
    owner: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class _ControlState:
    revision: int = 0
    token: NlsToken = field(default_factory=NlsToken, repr=False)
    tombstones: tuple[_Tombstone, ...] = field(default=(), repr=False)
    lease: _Lease | None = field(default=None, repr=False)
    fallback_blocked_until: int = 0


def _token_digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _plain_int(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NlsTokenControlError("Aliyun NLS token control state is invalid")
    return value


def _parse_state(raw: str) -> _ControlState:
    if not raw:
        return _ControlState()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise NlsTokenControlError("Aliyun NLS token control state is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {
        "fallback_blocked_until",
        "lease",
        "revision",
        "token",
        "tombstones",
        "version",
    }:
        raise NlsTokenControlError("Aliyun NLS token control state is invalid")
    if payload["version"] != CONTROL_VERSION:
        raise NlsTokenControlError("Aliyun NLS token control version is unsupported")

    token_payload = payload["token"]
    if token_payload is None:
        token = NlsToken()
    elif (
        isinstance(token_payload, dict)
        and set(token_payload) == {"expires_at", "value"}
        and isinstance(token_payload["value"], str)
        and 1 <= len(token_payload["value"]) <= 16_384
    ):
        token = NlsToken(
            token_payload["value"],
            _plain_int(token_payload["expires_at"], minimum=1),
        )
    else:
        raise NlsTokenControlError("Aliyun NLS token control state is invalid")

    tombstone_payload = payload["tombstones"]
    if not isinstance(tombstone_payload, list) or len(tombstone_payload) > MAX_TOMBSTONES:
        raise NlsTokenControlError("Aliyun NLS token control state is invalid")
    tombstones: list[_Tombstone] = []
    seen: set[str] = set()
    for item in tombstone_payload:
        if (
            not isinstance(item, dict)
            or set(item) != {"digest", "expires_at"}
            or not isinstance(item["digest"], str)
            or not _DIGEST_RE.fullmatch(item["digest"])
            or item["digest"] in seen
        ):
            raise NlsTokenControlError("Aliyun NLS token control state is invalid")
        seen.add(item["digest"])
        tombstones.append(
            _Tombstone(
                item["digest"],
                _plain_int(item["expires_at"], minimum=1),
            )
        )

    lease_payload = payload["lease"]
    if lease_payload is None:
        lease = None
    elif (
        isinstance(lease_payload, dict)
        and set(lease_payload) == {"expires_at", "owner"}
        and isinstance(lease_payload["owner"], str)
        and 1 <= len(lease_payload["owner"]) <= 128
    ):
        lease = _Lease(
            lease_payload["owner"],
            _plain_int(lease_payload["expires_at"], minimum=1),
        )
    else:
        raise NlsTokenControlError("Aliyun NLS token control state is invalid")

    return _ControlState(
        revision=_plain_int(payload["revision"]),
        token=token,
        tombstones=tuple(tombstones),
        lease=lease,
        fallback_blocked_until=_plain_int(payload["fallback_blocked_until"]),
    )


def _serialize_state(state: _ControlState) -> str:
    payload = {
        "fallback_blocked_until": state.fallback_blocked_until,
        "lease": (
            {"expires_at": state.lease.expires_at, "owner": state.lease.owner}
            if state.lease is not None
            else None
        ),
        "revision": state.revision,
        "token": (
            {"expires_at": state.token.expires_at, "value": state.token.value}
            if state.token.value
            else None
        ),
        "tombstones": [
            {"digest": item.digest, "expires_at": item.expires_at}
            for item in state.tombstones
        ],
        "version": CONTROL_VERSION,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _active_tombstones(
    state: _ControlState,
    *,
    now: int,
) -> tuple[_Tombstone, ...]:
    return tuple(item for item in state.tombstones if item.expires_at > now)


def _is_tombstoned(
    token: NlsToken,
    tombstones: tuple[_Tombstone, ...],
) -> bool:
    if not token.value:
        return False
    digest = _token_digest(token.value)
    return any(item.digest == digest for item in tombstones)


def _usable_token(
    state: _ControlState,
    fallback: NlsToken,
    *,
    now: int,
    skew_seconds: int,
    max_token_lifetime_seconds: int,
) -> NlsToken:
    tombstones = _active_tombstones(state, now=now)
    candidates = ((state.token, False), (fallback, True))
    for candidate, is_fallback in candidates:
        if (
            not candidate.value
            or len(candidate.value) > 16_384
            or isinstance(candidate.expires_at, bool)
            or not isinstance(candidate.expires_at, int)
            or candidate.expires_at > now + max_token_lifetime_seconds
            or not candidate.is_valid_at(now, skew_seconds=skew_seconds)
            or _is_tombstoned(candidate, tombstones)
            or (is_fallback and state.fallback_blocked_until > now)
        ):
            continue
        return candidate
    return NlsToken()


_T = TypeVar("_T")


class NlsTokenControlStore:
    """CAS-backed NLS token state stored in one private ``app_settings`` row."""

    def __init__(
        self,
        engine: Engine,
        *,
        key: str = NLS_TOKEN_CONTROL_KEY,
        max_token_lifetime_seconds: int = DEFAULT_MAX_TOKEN_LIFETIME_SECONDS,
        max_cas_attempts: int = 32,
    ) -> None:
        if not key or len(key) > 255:
            raise ValueError("Aliyun NLS token control key is invalid")
        if max_token_lifetime_seconds <= 0 or max_cas_attempts <= 0:
            raise ValueError("Aliyun NLS token control bounds must be positive")
        self._engine = engine
        self._key = key
        self._max_token_lifetime_seconds = int(max_token_lifetime_seconds)
        self._max_cas_attempts = int(max_cas_attempts)

    def _commit(self, session: Session) -> None:
        session.commit()

    def _read_raw(self) -> str:
        storage_failed = False
        try:
            with Session(self._engine) as session:
                row = session.get(AppSettingRecord, self._key)
                return str(row.value or "") if row is not None else ""
        except SQLAlchemyError:
            # Raise after leaving the handler so the provider token and the
            # whole control blob cannot survive in ``__context__``.
            storage_failed = True
        if storage_failed:
            raise NlsTokenControlError(
                "Aliyun NLS token control storage failed"
            ) from None
        raise AssertionError("unreachable")

    def _row_exists(self) -> bool:
        storage_failed = False
        try:
            with Session(self._engine) as session:
                return session.get(AppSettingRecord, self._key) is not None
        except SQLAlchemyError:
            storage_failed = True
        if storage_failed:
            raise NlsTokenControlError(
                "Aliyun NLS token control storage failed"
            ) from None
        raise AssertionError("unreachable")

    def _mutate(
        self,
        mutation: Callable[[_ControlState], tuple[_ControlState, _T]],
    ) -> _T:
        for _attempt in range(self._max_cas_attempts):
            insert_conflict = False
            storage_failed = False
            try:
                with Session(self._engine) as session:
                    row = session.get(AppSettingRecord, self._key)
                    old_raw = str(row.value or "") if row is not None else ""
                    state = _parse_state(old_raw)
                    next_state, result = mutation(state)
                    next_raw = _serialize_state(next_state)
                    if next_raw == old_raw:
                        return result
                    if row is None:
                        try:
                            session.exec(
                                insert(AppSettingRecord).values(
                                    key=self._key,
                                    value=next_raw,
                                )
                            )
                            self._commit(session)
                        except IntegrityError:
                            insert_conflict = True
                            try:
                                session.rollback()
                            except SQLAlchemyError:
                                storage_failed = True
                        if not insert_conflict:
                            return result
                    else:
                        written = session.exec(
                            update(AppSettingRecord)
                            .where(
                                AppSettingRecord.key == self._key,
                                AppSettingRecord.value == old_raw,
                            )
                            .values(value=next_raw)
                        )
                        if written.rowcount != 1:
                            session.rollback()
                            continue
                        self._commit(session)
                        return result
            except SQLAlchemyError:
                storage_failed = True

            # Never raise while handling a SQLAlchemy exception.  Its string
            # representation and bound parameters can contain the plaintext
            # token, lease owner, and tombstone digests.
            if storage_failed:
                raise NlsTokenControlError(
                    "Aliyun NLS token control storage failed"
                ) from None
            if insert_conflict:
                # An INSERT IntegrityError is retriable only when another
                # writer demonstrably created this exact control row.
                if self._row_exists():
                    continue
                raise NlsTokenControlError(
                    "Aliyun NLS token control storage failed"
                ) from None
        raise NlsTokenControlConflict("Aliyun NLS token control update conflicted")

    def load(
        self,
        fallback: NlsToken,
        *,
        now: int,
        skew_seconds: int,
    ) -> NlsToken:
        state = _parse_state(self._read_raw())
        return _usable_token(
            state,
            fallback,
            now=int(now),
            skew_seconds=int(skew_seconds),
            max_token_lifetime_seconds=self._max_token_lifetime_seconds,
        )

    def claim_refresh(
        self,
        owner: str,
        fallback: NlsToken,
        *,
        now: int,
        skew_seconds: int,
        lease_seconds: int,
    ) -> NlsRefreshClaim:
        normalized_owner = str(owner or "")
        if not normalized_owner or len(normalized_owner) > 128:
            raise ValueError("Aliyun NLS token refresh owner is invalid")
        if lease_seconds <= 0:
            raise ValueError("Aliyun NLS token refresh lease must be positive")
        current = int(now)

        def mutation(state: _ControlState) -> tuple[_ControlState, NlsRefreshClaim]:
            token = _usable_token(
                state,
                fallback,
                now=current,
                skew_seconds=int(skew_seconds),
                max_token_lifetime_seconds=self._max_token_lifetime_seconds,
            )
            if token.value:
                return state, NlsRefreshClaim(False, token=token)
            if (
                state.lease is not None
                and state.lease.owner != normalized_owner
                and state.lease.expires_at > current
            ):
                return state, NlsRefreshClaim(False, retry_at=state.lease.expires_at)
            next_state = _ControlState(
                revision=state.revision + 1,
                token=state.token,
                tombstones=_active_tombstones(state, now=current),
                lease=_Lease(normalized_owner, current + int(lease_seconds)),
                fallback_blocked_until=(
                    state.fallback_blocked_until
                    if state.fallback_blocked_until > current
                    else 0
                ),
            )
            return next_state, NlsRefreshClaim(True, retry_at=next_state.lease.expires_at)

        return self._mutate(mutation)

    def persist_refreshed(
        self,
        owner: str,
        token: NlsToken,
        *,
        now: int,
        minimum_validity_seconds: int,
    ) -> NlsToken:
        current = int(now)
        try:
            minimum = int(minimum_validity_seconds)
        except (TypeError, ValueError):
            raise NlsTokenControlError(
                "Aliyun NLS refreshed token lifetime is invalid"
            ) from None
        if (
            not token.value
            or len(token.value) > 16_384
            or isinstance(minimum_validity_seconds, bool)
            or isinstance(token.expires_at, bool)
            or not isinstance(token.expires_at, int)
            or minimum < 0
            or token.expires_at <= current + minimum
            or token.expires_at > current + self._max_token_lifetime_seconds
        ):
            raise NlsTokenControlError("Aliyun NLS refreshed token lifetime is invalid")
        normalized_owner = str(owner or "")

        def mutation(state: _ControlState) -> tuple[_ControlState, NlsToken]:
            if (
                state.lease is None
                or state.lease.owner != normalized_owner
                or state.lease.expires_at <= current
            ):
                raise NlsTokenControlConflict(
                    "Aliyun NLS token refresh ownership was lost"
                )
            tombstones = _active_tombstones(state, now=current)
            if _is_tombstoned(token, tombstones):
                raise NlsTokenControlError("Aliyun NLS refreshed token was rejected")
            return (
                _ControlState(
                    revision=state.revision + 1,
                    token=token,
                    tombstones=tombstones,
                    lease=None,
                    fallback_blocked_until=(
                        state.fallback_blocked_until
                        if state.fallback_blocked_until > current
                        else 0
                    ),
                ),
                token,
            )

        return self._mutate(mutation)

    def abandon_refresh(self, owner: str) -> bool:
        normalized_owner = str(owner or "")

        def mutation(state: _ControlState) -> tuple[_ControlState, bool]:
            if state.lease is None or state.lease.owner != normalized_owner:
                return state, False
            return (
                _ControlState(
                    revision=state.revision + 1,
                    token=state.token,
                    tombstones=state.tombstones,
                    lease=None,
                    fallback_blocked_until=state.fallback_blocked_until,
                ),
                True,
            )

        return self._mutate(mutation)

    def invalidate(
        self,
        token: NlsToken,
        *,
        now: int,
    ) -> bool:
        if not token.value:
            return False
        current = int(now)
        if (
            len(token.value) > 16_384
            or isinstance(token.expires_at, bool)
            or not isinstance(token.expires_at, int)
        ):
            raise NlsTokenControlError("Aliyun NLS rejected token is invalid")
        digest = _token_digest(token.value)
        tombstone_expiry = min(
            max(token.expires_at, current + 1),
            current + self._max_token_lifetime_seconds,
        )

        def mutation(state: _ControlState) -> tuple[_ControlState, bool]:
            tombstones = {
                item.digest: item
                for item in _active_tombstones(state, now=current)
            }
            previous = tombstones.get(digest)
            tombstones[digest] = _Tombstone(
                digest,
                max(tombstone_expiry, previous.expires_at if previous else 0),
            )
            ordered = sorted(
                tombstones.values(),
                key=lambda item: (item.expires_at, item.digest),
            )
            dropped = ordered[:-MAX_TOMBSTONES]
            retained = ordered[-MAX_TOMBSTONES:]
            fallback_blocked_until = max(
                [
                    state.fallback_blocked_until,
                    *(item.expires_at for item in dropped),
                ]
            )
            stored_matches = bool(
                state.token.value
                and _token_digest(state.token.value) == digest
            )
            next_state = _ControlState(
                revision=state.revision + 1,
                token=NlsToken() if stored_matches else state.token,
                tombstones=tuple(retained),
                lease=state.lease,
                fallback_blocked_until=fallback_blocked_until,
            )
            return next_state, stored_matches

        return self._mutate(mutation)
