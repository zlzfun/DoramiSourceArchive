import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import AliyunIsiConfig  # noqa: E402
from models.db import AppSettingRecord  # noqa: E402
from services.aliyun_isi_auth import (  # noqa: E402
    NlsToken,
    NlsTokenManager,
)
from services.aliyun_isi_token_store import (  # noqa: E402
    NLS_TOKEN_CONTROL_KEY,
    NlsTokenControlConflict,
    NlsTokenControlError,
    NlsTokenControlStore,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _config(**updates):
    values = {
        "access_key_id": "ak-id",
        "access_key_secret": "ak-secret",
        "app_key": "app-key",
        "access_token": "configured-token",
        "token_expires_at": 2_000,
        "token_refresh_skew_seconds": 30,
        "request_timeout_seconds": 1,
    }
    values.update(updates)
    return AliyunIsiConfig(**values)


def _storage(tmp_path, name="token-control.db"):
    return DatabaseStorage(f"sqlite:///{tmp_path / name}")


def _raw_control(storage):
    with Session(storage.engine) as session:
        row = session.get(AppSettingRecord, NLS_TOKEN_CONTROL_KEY)
        return str(row.value) if row is not None else ""


def _assert_fixed_storage_error(error, *sensitive_values):
    assert str(error) == "Aliyun NLS token control storage failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = repr(error) + str(error)
    for value in sensitive_values:
        assert value not in rendered


def test_rejected_fallback_cannot_revive_in_a_new_session(tmp_path):
    storage = _storage(tmp_path)
    config = _config()
    first = NlsTokenManager(
        config,
        token_store=NlsTokenControlStore(storage.engine),
        clock=lambda: 1_000,
    )

    assert first.get().value == "configured-token"
    assert first.invalidate("configured-token") is True

    calls = []

    class FakePop:
        def call(self, **kwargs):
            calls.append(kwargs)
            return {"Token": {"Id": "fresh-token", "ExpireTime": 5_000}}

    reopened = NlsTokenManager(
        config,
        token_store=NlsTokenControlStore(storage.engine),
        pop_client=FakePop(),
        clock=lambda: 1_000,
    )
    assert reopened.get().value == "fresh-token"
    assert len(calls) == 1

    raw = _raw_control(storage)
    assert "configured-token" not in raw
    assert hashlib.sha256(b"configured-token").hexdigest() in raw
    assert "fresh-token" in raw


def test_concurrent_managers_allow_only_refresh_owner_to_create_token(tmp_path):
    storage = _storage(tmp_path)
    store_a = NlsTokenControlStore(storage.engine)
    store_b = NlsTokenControlStore(storage.engine)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingPop:
        def call(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            assert release.wait(timeout=3)
            return {"Token": {"Id": "one-fresh-token", "ExpireTime": 5_000}}

    config = _config(access_token="", token_expires_at=0)
    managers = (
        NlsTokenManager(
            config,
            token_store=store_a,
            pop_client=BlockingPop(),
            clock=lambda: 1_000,
            refresh_wait_seconds=0.005,
            refresh_wait_timeout_seconds=2,
        ),
        NlsTokenManager(
            config,
            token_store=store_b,
            pop_client=BlockingPop(),
            clock=lambda: 1_000,
            refresh_wait_seconds=0.005,
            refresh_wait_timeout_seconds=2,
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(managers[0].get)
        assert entered.wait(timeout=2)
        second = pool.submit(managers[1].get)
        release.set()
        results = (first.result(timeout=3), second.result(timeout=3))

    assert [item.value for item in results] == ["one-fresh-token"] * 2
    assert len(calls) == 1


def test_expired_refresh_lease_is_recoverable_and_old_owner_cannot_commit(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    empty = NlsToken()

    first = store.claim_refresh(
        "owner-a",
        empty,
        now=1_000,
        skew_seconds=30,
        lease_seconds=10,
    )
    blocked = store.claim_refresh(
        "owner-b",
        empty,
        now=1_005,
        skew_seconds=30,
        lease_seconds=10,
    )
    recovered = store.claim_refresh(
        "owner-b",
        empty,
        now=1_010,
        skew_seconds=30,
        lease_seconds=10,
    )

    assert first.acquired is True
    assert blocked.acquired is False
    assert blocked.retry_at == 1_010
    assert recovered.acquired is True
    with pytest.raises(NlsTokenControlConflict, match="ownership was lost"):
        store.persist_refreshed(
            "owner-a",
            NlsToken("stale-owner-token", 5_000),
            now=1_010,
            minimum_validity_seconds=30,
        )
    stored = store.persist_refreshed(
        "owner-b",
        NlsToken("recovered-token", 5_000),
        now=1_010,
        minimum_validity_seconds=30,
    )
    assert stored.value == "recovered-token"


def test_late_old_token_rejection_preserves_newer_durable_token(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    empty = NlsToken()
    assert store.claim_refresh(
        "owner",
        empty,
        now=1_000,
        skew_seconds=30,
        lease_seconds=60,
    ).acquired
    store.persist_refreshed(
        "owner",
        NlsToken("new-token", 5_000),
        now=1_000,
        minimum_validity_seconds=30,
    )

    old_manager = NlsTokenManager(
        _config(access_token="old-token", token_expires_at=2_000),
        token_store=store,
        clock=lambda: 1_000,
    )
    # Simulate a response that was sent with the old process-local token before
    # another process completed its refresh.
    old_manager._token = NlsToken("old-token", 2_000)
    assert old_manager.invalidate("old-token") is True

    assert store.load(
        NlsToken(),
        now=1_000,
        skew_seconds=30,
    ).value == "new-token"
    raw = _raw_control(storage)
    assert "old-token" not in raw
    assert "new-token" in raw


def test_concurrent_invalidations_merge_whole_blob_tombstones(tmp_path):
    storage = _storage(tmp_path)
    stores = [NlsTokenControlStore(storage.engine) for _ in range(2)]
    tokens = [NlsToken("rejected-a", 2_000), NlsToken("rejected-b", 3_000)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda pair: pair[0].invalidate(pair[1], now=1_000),
                zip(stores, tokens),
            )
        )

    raw = _raw_control(storage)
    assert "rejected-a" not in raw
    assert "rejected-b" not in raw
    assert hashlib.sha256(b"rejected-a").hexdigest() in raw
    assert hashlib.sha256(b"rejected-b").hexdigest() in raw


def test_tombstone_overflow_blocks_dropped_fallback_until_its_expiry(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    rejected = [NlsToken(f"rejected-{index}", 2_000 + index) for index in range(65)]
    for token in rejected:
        store.invalidate(token, now=1_000)

    # The bounded blob may compact the oldest digest, but the fail-closed
    # cutoff prevents any dropped env/INI/KV fallback from being revived.
    assert store.load(
        rejected[0],
        now=1_000,
        skew_seconds=30,
    ).value == ""
    payload = json.loads(_raw_control(storage))
    assert len(payload["tombstones"]) == 64
    assert payload["fallback_blocked_until"] >= rejected[0].expires_at


def test_fallback_token_with_excessive_lifetime_or_size_is_never_loaded(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(
        storage.engine,
        max_token_lifetime_seconds=1_000,
    )
    assert store.load(
        NlsToken("too-long-lived", 2_001),
        now=1_000,
        skew_seconds=30,
    ).value == ""
    assert store.load(
        NlsToken("x" * 16_385, 1_500),
        now=1_000,
        skew_seconds=30,
    ).value == ""


def test_refresh_commit_failure_never_promotes_uncommitted_token(tmp_path):
    storage = _storage(tmp_path)

    class FailingSecondCommitStore(NlsTokenControlStore):
        def __init__(self, engine):
            super().__init__(engine)
            self.commits = 0

        def _commit(self, session):
            self.commits += 1
            if self.commits == 2:
                raise RuntimeError("simulated commit failure")
            super()._commit(session)

    class FakePop:
        def call(self, **_kwargs):
            return {"Token": {"Id": "must-not-be-used", "ExpireTime": 5_000}}

    manager = NlsTokenManager(
        _config(access_token="", token_expires_at=0),
        token_store=FailingSecondCommitStore(storage.engine),
        pop_client=FakePop(),
        clock=lambda: 1_000,
    )
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        manager.get()

    assert manager._token.value == ""
    raw = _raw_control(storage)
    assert "must-not-be-used" not in raw
    payload = json.loads(raw)
    assert payload["lease"] is not None
    assert payload["token"] is None


@pytest.mark.parametrize(
    "token",
    [
        NlsToken("token", "bad"),
        NlsToken("token", True),
        NlsToken("token", 1_030),
        NlsToken("token", 1_000 + 8 * 24 * 60 * 60),
    ],
)
def test_persist_rejects_malformed_or_out_of_horizon_expiry(tmp_path, token):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    assert store.claim_refresh(
        "owner",
        NlsToken(),
        now=1_000,
        skew_seconds=30,
        lease_seconds=60,
    ).acquired

    with pytest.raises(NlsTokenControlError, match="lifetime is invalid"):
        store.persist_refreshed(
            "owner",
            token,
            now=1_000,
            minimum_validity_seconds=30,
        )


def test_repr_and_errors_do_not_expose_token_digest_owner_or_control_json(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    token = NlsToken("sensitive-token", 2_000)
    claim = store.claim_refresh(
        "sensitive-owner",
        NlsToken(),
        now=1_000,
        skew_seconds=30,
        lease_seconds=60,
    )
    digest = hashlib.sha256(token.value.encode()).hexdigest()

    rendered = f"{token!r} {claim!r} {store!r}"
    assert token.value not in rendered
    assert digest not in rendered
    assert "sensitive-owner" not in rendered
    with pytest.raises(NlsTokenControlError) as caught:
        NlsTokenControlStore(storage.engine, max_token_lifetime_seconds=1).persist_refreshed(
            "sensitive-owner",
            token,
            now=1_000,
            minimum_validity_seconds=30,
        )
    rendered_error = f"{caught.value!r} {caught.value}"
    assert token.value not in rendered_error
    assert digest not in rendered_error
    assert "sensitive-owner" not in rendered_error


def test_insert_failure_does_not_expose_bound_control_blob(tmp_path):
    storage = _storage(tmp_path)
    secret = "VERY-SECRET-INSERT-TOKEN"
    digest = hashlib.sha256(secret.encode()).hexdigest()
    with storage.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_nls_control_insert
            BEFORE INSERT ON app_settings
            WHEN NEW.key = 'aliyun_isi:nls_token_control:v1'
            BEGIN
              SELECT RAISE(FAIL, 'forced insert failure');
            END
            """
        )

    with pytest.raises(NlsTokenControlError) as caught:
        NlsTokenControlStore(storage.engine).invalidate(
            NlsToken(secret, 2_000),
            now=1_000,
        )

    _assert_fixed_storage_error(caught.value, secret, digest, "forced insert failure")


def test_update_failure_does_not_expose_old_or_new_control_blob(tmp_path):
    storage = _storage(tmp_path)
    store = NlsTokenControlStore(storage.engine)
    old_secret = "VERY-SECRET-OLD-TOKEN"
    new_secret = "VERY-SECRET-UPDATE-TOKEN"
    store.invalidate(NlsToken(old_secret, 2_000), now=1_000)
    old_raw = _raw_control(storage)
    new_digest = hashlib.sha256(new_secret.encode()).hexdigest()
    with storage.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_nls_control_update
            BEFORE UPDATE ON app_settings
            WHEN NEW.key = 'aliyun_isi:nls_token_control:v1'
            BEGIN
              SELECT RAISE(FAIL, 'forced update failure');
            END
            """
        )

    with pytest.raises(NlsTokenControlError) as caught:
        store.invalidate(NlsToken(new_secret, 3_000), now=1_000)

    _assert_fixed_storage_error(
        caught.value,
        old_secret,
        new_secret,
        old_raw,
        new_digest,
        "forced update failure",
    )


def test_commit_failure_does_not_expose_token_or_exception_chain(tmp_path):
    storage = _storage(tmp_path)
    secret = "VERY-SECRET-COMMIT-TOKEN"
    digest = hashlib.sha256(secret.encode()).hexdigest()

    class FailingCommitStore(NlsTokenControlStore):
        def _commit(self, session):
            raise SQLAlchemyError(
                f"forced commit failure with {secret} {digest} sensitive-owner"
            )

    with pytest.raises(NlsTokenControlError) as caught:
        FailingCommitStore(storage.engine).invalidate(
            NlsToken(secret, 2_000),
            now=1_000,
        )

    _assert_fixed_storage_error(
        caught.value,
        secret,
        digest,
        "sensitive-owner",
        "forced commit failure",
    )
