import base64
import hashlib
import hmac
import json
import os
import sys
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config  # noqa: E402
from services import credentials, podcast_asr_fetch_signing  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


SECRET = "s" * 32
BASE_URL = "https://audio.example.test/api/public/podcast-asr/source-audio"
DIGEST = "a" * 64
NOW = 2_000_000_000


@pytest.fixture(autouse=True)
def _clear_signing_environment(monkeypatch):
    for name in (
        "DORAMI_PODCAST_ASR_FETCH_PUBLIC_BASE_URL",
        "DORAMI_PODCAST_ASR_FETCH_SIGNING_SECRET",
        "DORAMI_PODCAST_ASR_FETCH_PREVIOUS_SIGNING_SECRET",
        "DORAMI_PODCAST_ASR_FETCH_URL_TTL_SECONDS",
        "DORAMI_PODCAST_ASR_FETCH_CLOCK_SKEW_SECONDS",
        "DORAMI_PODCAST_ASR_FETCH_MIN_REMAINING_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def _config(**overrides):
    values = {
        "public_base_url": BASE_URL,
        "signing_secret": SECRET,
        "url_ttl_seconds": 900,
        "clock_skew_seconds": 30,
        "min_remaining_seconds": 300,
    }
    values.update(overrides)
    return config.PodcastAsrFetchConfig(**values)


def _signer(**overrides):
    return podcast_asr_fetch_signing.PodcastAsrFetchUrlSigner(
        _config(**overrides), authority_id="external-authority"
    )


def _issue(signer=None):
    active = signer or _signer()
    signed = active.issue(
        processing_id="processing-1",
        artifact_id="artifact-1",
        content_sha256=DIGEST,
        now=NOW,
    )
    split = urlsplit(signed.url)
    return active, signed, split


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _resign_payload(split, payload, *, secret=SECRET, canonical=True):
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":") if canonical else None,
        sort_keys=canonical,
    ).encode("utf-8")
    encoded = _encode(raw)
    signature = _encode(hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest())
    return urlencode({"payload": encoded, "signature": signature})


def _payload(split):
    encoded = parse_qs(split.query)["payload"][0]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    return json.loads(raw)


def test_issue_and_verify_get_and_head_bind_all_claims_without_host_input():
    signer, signed, split = _issue()

    assert split.scheme == "https"
    assert split.netloc == "audio.example.test"
    assert split.path == "/api/public/podcast-asr/source-audio"
    assert signed.expires_at == NOW + 900
    assert signed.min_remaining_seconds == 300
    assert signed.url not in repr(signed)

    for method in ("GET", "HEAD", "get"):
        claims = signer.verify(
            method=method,
            canonical_path=split.path,
            raw_query=split.query,
            now=NOW + 1,
        )
        assert claims.processing_id == "processing-1"
        assert claims.artifact_id == "artifact-1"
        assert claims.content_sha256 == DIGEST
        assert claims.authority_id == "external-authority"
        assert claims.canonical_path == split.path
        assert claims.allowed_methods == ("GET", "HEAD")


def test_issue_caps_lifetime_without_exceeding_config_or_minimum():
    signer = _signer()
    bounded = signer.issue(
        processing_id="processing-1",
        artifact_id="artifact-1",
        content_sha256=DIGEST,
        now=NOW,
        expires_at_cap=NOW + 400,
    )
    split = urlsplit(bounded.url)
    assert bounded.expires_at == NOW + 400
    claims = signer.verify(
        method="GET",
        canonical_path=split.path,
        raw_query=split.query,
        now=NOW,
    )
    assert claims.expires_at == NOW + 400

    config_capped = signer.issue(
        processing_id="processing-1",
        artifact_id="artifact-1",
        content_sha256=DIGEST,
        now=NOW,
        expires_at_cap=NOW + 1_200,
    )
    assert config_capped.expires_at == NOW + 900
    with pytest.raises(ValueError, match="too short"):
        signer.issue(
            processing_id="processing-1",
            artifact_id="artifact-1",
            content_sha256=DIGEST,
            now=NOW,
            expires_at_cap=NOW + 299,
        )


def test_signing_uses_compare_digest(monkeypatch):
    signer, _, split = _issue()
    calls = []
    original = hmac.compare_digest

    def recorded(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(podcast_asr_fetch_signing.hmac, "compare_digest", recorded)
    signer.verify(
        method="GET",
        canonical_path=split.path,
        raw_query=split.query,
        now=NOW,
    )
    assert len(calls) == 1


def test_secret_rotation_verifies_current_and_previous_without_short_circuit(
    monkeypatch,
):
    previous_secret = "p" * 32
    current_secret = "n" * 32
    old_signer = _signer(signing_secret=previous_secret)
    _, old_url, old_split = _issue(old_signer)
    rotated = _signer(
        signing_secret=current_secret,
        previous_signing_secret=previous_secret,
    )
    _, new_url, new_split = _issue(rotated)
    comparisons = []
    original_compare = hmac.compare_digest

    def recorded(left, right):
        comparisons.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(podcast_asr_fetch_signing.hmac, "compare_digest", recorded)
    for split in (old_split, new_split):
        claims = rotated.verify(
            method="GET",
            canonical_path=split.path,
            raw_query=split.query,
            now=NOW,
        )
        assert claims.processing_id == "processing-1"
    assert len(comparisons) == 4
    assert old_url.url != new_url.url

    without_previous = _signer(signing_secret=current_secret)
    with pytest.raises(podcast_asr_fetch_signing.PodcastAsrFetchSignatureError):
        without_previous.verify(
            method="GET",
            canonical_path=old_split.path,
            raw_query=old_split.query,
            now=NOW,
        )
    without_previous.verify(
        method="GET",
        canonical_path=new_split.path,
        raw_query=new_split.query,
        now=NOW,
    )


@pytest.mark.parametrize(
    "query_mutator",
    [
        lambda query: f"{query}&extra=1",
        lambda query: f"{query}&payload=duplicate",
        lambda query: query.replace("payload=", "p%61yload=", 1),
        lambda query: "&".join(reversed(query.split("&"))),
        lambda query: query.replace("signature=", "signature=!", 1),
        lambda query: query.replace("payload=", "payload=x", 1),
        lambda query: "payload=&signature=",
    ],
)
def test_verify_rejects_extra_duplicate_malformed_and_tampered_query(query_mutator):
    signer, _, split = _issue()
    with pytest.raises(
        podcast_asr_fetch_signing.PodcastAsrFetchSignatureError,
        match="invalid Podcast ASR fetch authorization",
    ):
        signer.verify(
            method="GET",
            canonical_path=split.path,
            raw_query=query_mutator(split.query),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("mutate", "now"),
    [
        (lambda payload: payload.update(purpose="other"), NOW),
        (lambda payload: payload.update(v=2), NOW),
        (lambda payload: payload.update(methods=["GET"]), NOW),
        (lambda payload: payload.update(path="/other"), NOW),
        (lambda payload: payload.update(authority_id="other"), NOW),
        (lambda payload: payload.update(sha256="A" * 64), NOW),
        (lambda payload: payload.update(extra="not-allowed"), NOW),
        (lambda payload: payload.update(exp=payload["iat"] + 901), NOW),
        (lambda payload: payload.update(exp=payload["iat"] + 299), NOW),
        (lambda payload: payload.update(iat=NOW + 31, exp=NOW + 331), NOW),
        (lambda payload: None, NOW + 931),
    ],
)
def test_verify_rejects_binding_time_and_payload_shape_changes(mutate, now):
    signer, _, split = _issue()
    payload = _payload(split)
    mutate(payload)
    query = _resign_payload(split, payload)

    with pytest.raises(podcast_asr_fetch_signing.PodcastAsrFetchSignatureError):
        signer.verify(
            method="GET",
            canonical_path=split.path,
            raw_query=query,
            now=now,
        )


def test_verify_rejects_noncanonical_signed_json_and_wrong_request_binding():
    signer, _, split = _issue()
    payload = _payload(split)
    noncanonical_query = _resign_payload(split, payload, canonical=False)

    for method, path, query in (
        ("POST", split.path, split.query),
        ("GET", "/api/podcast/other", split.query),
        ("GET", split.path, noncanonical_query),
    ):
        with pytest.raises(podcast_asr_fetch_signing.PodcastAsrFetchSignatureError):
            signer.verify(
                method=method,
                canonical_path=path,
                raw_query=query,
                now=NOW,
            )


def test_config_validates_https_secret_bytes_and_lifetimes_without_repr_leak():
    configured = _config(
        signing_secret="密" * 11,
        previous_signing_secret="旧" * 11,
    )
    assert configured.configured is True
    assert configured.signing_secret not in repr(configured)
    assert configured.previous_signing_secret not in repr(configured)

    for bad_url in (
        "http://audio.example.test/path",
        "https://user@audio.example.test/path",
        "https://audio.example.test/path?x=1",
        "https://audio.example.test/path?",
        "https://audio.example.test/path#fragment",
        "https://audio.example.test/path#",
        "https://audio.example.test/a/../path",
        "https://audio.example.test/a//path",
        "https://audio.example.test/%70ath",
        "https://audio.example.test/bad path",
    ):
        with pytest.raises(ValueError, match="HTTPS URL"):
            _config(public_base_url=bad_url)
    with pytest.raises(ValueError, match="32 bytes"):
        _config(signing_secret="short")
    with pytest.raises(ValueError, match="previous_signing_secret"):
        _config(previous_signing_secret="short")
    with pytest.raises(ValueError, match="public_base_url path"):
        podcast_asr_fetch_signing.PodcastAsrFetchUrlSigner(
            _config(public_base_url="https://audio.example.test/wrong"),
            authority_id="external-authority",
        )
    with pytest.raises(ValueError, match="TTL must be positive"):
        _config(url_ttl_seconds=0, min_remaining_seconds=1)
    with pytest.raises(ValueError, match="clock skew"):
        _config(clock_skew_seconds=-1)
    with pytest.raises(ValueError, match="cannot exceed"):
        _config(url_ttl_seconds=60, min_remaining_seconds=61)


def test_disabled_default_and_enabled_external_asr_can_load_without_baseline(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "missing.ini"))
    assert config.load_config().podcast_asr_fetch.configured is False

    monkeypatch.setenv("DORAMI_PODCAST_INSTALLATION", "external")
    monkeypatch.setenv("DORAMI_PODCAST_ALLOWED_STAGES", "fetch,asr")
    monkeypatch.setenv("DORAMI_PODCAST_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("DORAMI_PODCAST_PROVIDER_READY_TARGETS", "transcript")
    monkeypatch.setenv("DORAMI_PODCAST_MONTHLY_BUDGET_CNY_MINOR", "100")
    monkeypatch.setenv("DORAMI_PODCAST_PER_RUN_BUDGET_CNY_MINOR", "10")
    loaded_without_baseline = config.load_config()
    assert loaded_without_baseline.podcast_asr_fetch.configured is False
    with pytest.raises(ValueError, match="not configured"):
        podcast_asr_fetch_signing.PodcastAsrFetchUrlSigner(
            loaded_without_baseline.podcast_asr_fetch,
            authority_id=loaded_without_baseline.podcast.authority_id,
        )

    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_PUBLIC_BASE_URL", BASE_URL)
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_PREVIOUS_SIGNING_SECRET", "p" * 32)
    loaded = config.load_config()
    assert loaded.podcast_asr_fetch.configured is True


def test_internal_sync_only_does_not_require_external_asr_fetch_config(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "missing.ini"))
    monkeypatch.setenv("DORAMI_PODCAST_INSTALLATION", "internal")
    monkeypatch.setenv("DORAMI_PODCAST_ALLOWED_STAGES", "")

    assert config.load_config().podcast_asr_fetch.configured is False


def test_env_and_runtime_kv_round_trip_without_secret_exposure(tmp_path, monkeypatch):
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "missing.ini"))
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_PUBLIC_BASE_URL", BASE_URL)
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_PREVIOUS_SIGNING_SECRET", "e" * 32)
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_URL_TTL_SECONDS", "800")
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_CLOCK_SKEW_SECONDS", "20")
    monkeypatch.setenv("DORAMI_PODCAST_ASR_FETCH_MIN_REMAINING_SECONDS", "250")
    baseline = config.load_config().podcast_asr_fetch
    assert baseline.url_ttl_seconds == 800
    assert baseline.previous_signing_secret == "e" * 32
    monkeypatch.setattr(
        config,
        "settings",
        replace(
            config.settings,
            podcast=config.PodcastConfig(
                installation="external",
                authority_id="external-authority",
                allowed_stages=("fetch", "asr"),
                processing_enabled=True,
                provider_ready_targets=("transcript",),
                monthly_budget_cny_minor=100,
                per_run_budget_cny_minor=10,
            ),
            # Keep the startup baseline empty to prove KV-only resolution.
            podcast_asr_fetch=config.PodcastAsrFetchConfig(),
        ),
    )

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'signing.db'}")
    with Session(sink.engine) as session:
        credentials.save_updates(
            session,
            credentials.PODCAST_ASR_FETCH_NAMESPACE,
            {
                "public_base_url": "https://runtime.example.test/api/public/podcast-asr/source-audio",
                "signing_secret": "k" * 32,
                "previous_signing_secret": "p" * 32,
                "url_ttl_seconds": 700,
                "clock_skew_seconds": 10,
                "min_remaining_seconds": 200,
            },
        )
        resolved = podcast_asr_fetch_signing.resolve_config(session)
        sources = podcast_asr_fetch_signing.field_sources(session)
        sanitized = credentials.sanitize_values(
            credentials.PODCAST_ASR_FETCH_NAMESPACE,
            resolved.__dict__,
        )
        runtime_signer = podcast_asr_fetch_signing.resolve_signer(session)

    assert resolved.public_base_url == (
        "https://runtime.example.test/api/public/podcast-asr/source-audio"
    )
    assert resolved.signing_secret == "k" * 32
    assert resolved.previous_signing_secret == "p" * 32
    assert resolved.url_ttl_seconds == 700
    assert set(sources.values()) == {"runtime_kv"}
    assert sanitized["signing_secret_set"] is True
    assert sanitized["previous_signing_secret_set"] is True
    assert "k" * 32 not in str(sanitized)
    assert "p" * 32 not in str(sanitized)
    assert "signing_secret" not in sanitized
    assert "previous_signing_secret" not in sanitized
    assert (
        runtime_signer.issue(
            processing_id="processing-kv",
            artifact_id="artifact-kv",
            content_sha256=DIGEST,
            now=NOW,
        ).expires_at
        == NOW + 700
    )


def test_ini_round_trip_uses_the_dedicated_section(tmp_path, monkeypatch):
    ini = tmp_path / "backend.ini"
    ini.write_text(
        "\n".join(
            (
                "[podcast_asr_fetch]",
                "public_base_url = https://ini.example.test/api/public/podcast-asr/source-audio",
                f"signing_secret = {SECRET}",
                f"previous_signing_secret = {'p' * 32}",
                "url_ttl_seconds = 720",
                "clock_skew_seconds = 12",
                "min_remaining_seconds = 180",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))

    loaded = config.load_config().podcast_asr_fetch

    assert loaded.public_base_url == (
        "https://ini.example.test/api/public/podcast-asr/source-audio"
    )
    assert loaded.signing_secret == SECRET
    assert loaded.previous_signing_secret == "p" * 32
    assert loaded.url_ttl_seconds == 720
    assert loaded.clock_skew_seconds == 12
    assert loaded.min_remaining_seconds == 180


def test_invalid_issue_inputs_never_generate_a_capability():
    signer = _signer()
    for kwargs in (
        {"processing_id": "", "artifact_id": "a", "content_sha256": DIGEST},
        {"processing_id": "p", "artifact_id": "", "content_sha256": DIGEST},
        {"processing_id": "p", "artifact_id": "a", "content_sha256": "A" * 64},
    ):
        with pytest.raises(ValueError):
            signer.issue(**kwargs, now=NOW)
    with pytest.raises(ValueError, match="Unix timestamp"):
        signer.issue(
            processing_id="p",
            artifact_id="a",
            content_sha256=DIGEST,
            now=True,
        )


def test_resolve_signer_rejects_disabled_or_non_external_authority(
    tmp_path, monkeypatch
):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'disabled-signing.db'}")
    for podcast in (
        config.PodcastConfig(),
        config.PodcastConfig(
            installation="internal",
            authority_id="internal-authority",
            allowed_stages=(),
        ),
    ):
        monkeypatch.setattr(
            config,
            "settings",
            replace(config.settings, podcast=podcast),
        )
        with Session(sink.engine) as session:
            with pytest.raises(ValueError, match="authority is not enabled"):
                podcast_asr_fetch_signing.resolve_signer(session)
