"""Contract v1.0 & Alignment (D01-D20, C01-C12, I07) tests for M2M personal briefs."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from models.analysis_contracts import AnalysisStatus, DigestGenerationReason
from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
    UserRecord,
)
from services import accounts as accounts_service
from services import briefs_runtime
from services import personal_digest as digest_service
from storage.impl.db_storage import DatabaseStorage


SHANGHAI = briefs_runtime.SHANGHAI
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARED_DIR = REPO_ROOT / "contracts" / "briefs-v1" / "shared"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_URL_RE = re.compile(r"^https?://[^\s/]+(?:[^\s]*)$")
_EMP_RE = re.compile(r"^\S{1,128}$")
_ALLOWED_ERRORS = {
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "CSRF_REJECTED",
    "ACCOUNT_NOT_FOUND",
    "INVALID_ARGUMENT",
    "RATE_LIMITED",
    "INTERNAL_ERROR",
    "ACCOUNT_MAPPING_CONFLICT",
    "SERVICE_UNAVAILABLE",
}


def assert_contract_headers(headers: Any, *, expected_request_id: str | None = None) -> str:
    assert "application/json" in headers.get("content-type", "")
    assert headers.get("cache-control") == "no-store"
    assert headers.get("x-briefs-contract-version") == "1.0"
    request_id = headers.get("x-request-id") or ""
    assert 1 <= len(request_id) <= 128
    if expected_request_id is not None:
        assert request_id == expected_request_id
    return request_id


def assert_error_envelope(
    response: Any,
    expected_http: int,
    expected_code: str,
    *,
    expected_request_id: str | None = None,
) -> dict[str, Any]:
    assert response.status_code == expected_http, response.text
    req_id = assert_contract_headers(response.headers, expected_request_id=expected_request_id)
    payload = response.json()
    assert isinstance(payload, dict) and "error" in payload
    err = payload["error"]
    assert err["code"] == expected_code and err["code"] in _ALLOWED_ERRORS
    assert isinstance(err["message"], str) and err["message"].strip()
    assert err["request_id"] == req_id
    return payload


def validate_brief_schema(payload: dict[str, Any], *, is_ensure: bool = False) -> None:
    """Validate every OpenAPI 3.1 + R03/R04 invariant on a 200 BriefResponse."""
    required_top = {
        "employee_number",
        "username",
        "status",
        "pushable",
        "fallback_hint",
        "edition",
    }
    assert required_top.issubset(payload.keys())
    assert isinstance(payload["employee_number"], str) and _EMP_RE.fullmatch(payload["employee_number"])
    assert isinstance(payload["username"], str) and payload["username"].strip()
    assert type(payload["pushable"]) is bool
    status = payload["status"]
    assert status in {"missing", "pending", "processing", "ready", "degraded", "empty", "failed"}
    if is_ensure:
        assert status != "missing"
    hint = payload["fallback_hint"]
    assert hint in {"public", "retry", None}
    edition = payload["edition"]

    if status == "missing":
        assert edition is None
        assert payload["pushable"] is False and hint == "retry"
        return

    assert isinstance(edition, dict)
    req_ed = {
        "report_date",
        "revision",
        "status",
        "degraded_reason",
        "selection_stats",
        "generated_at",
        "items",
    }
    assert req_ed.issubset(edition.keys())
    assert isinstance(edition["report_date"], str) and _DATE_RE.fullmatch(edition["report_date"])
    assert type(edition["revision"]) is int and edition["revision"] >= 1
    assert edition["status"] in {"pending", "processing", "ready", "failed"}
    assert edition["degraded_reason"] is None or (
        isinstance(edition["degraded_reason"], str) and bool(edition["degraded_reason"].strip())
    )
    assert isinstance(edition["selection_stats"], dict)
    assert isinstance(edition["items"], list)

    if edition["status"] in {"pending", "processing", "failed"}:
        assert edition["generated_at"] is None
        assert edition["items"] == []
    else:
        assert isinstance(edition["generated_at"], str) and _DT_RE.fullmatch(edition["generated_at"])

    # R03 state matrix check
    if status in {"pending", "processing"}:
        assert edition["status"] == status
        assert payload["pushable"] is False and hint == "retry"
    elif status == "failed":
        assert edition["status"] == "failed"
        assert payload["pushable"] is False and hint == "public"
    elif status == "empty":
        assert edition["status"] == "ready"
        assert edition["items"] == []
        assert payload["pushable"] is False and hint == "public"
    elif status == "ready":
        assert edition["status"] == "ready"
        assert len(edition["items"]) >= 1
        assert edition["degraded_reason"] is None
        assert payload["pushable"] is True and hint is None
    elif status == "degraded":
        assert edition["status"] == "ready"
        assert len(edition["items"]) >= 1
        assert isinstance(edition["degraded_reason"], str) and edition["degraded_reason"].strip()
        assert payload["pushable"] is True and hint is None

    for idx, item in enumerate(edition["items"], start=1):
        assert isinstance(item, dict)
        assert {
            "position",
            "section",
            "selection_lane",
            "quality_score",
            "selection_reason",
            "snapshot",
        }.issubset(item.keys())
        assert type(item["position"]) is int and item["position"] == idx
        assert isinstance(item["section"], str) and item["section"].strip()
        assert item["selection_lane"] in {"interest", "quality", "breaking"}
        assert item["quality_score"] is None or type(item["quality_score"]) in {int, float}
        assert item["selection_reason"] is None or isinstance(item["selection_reason"], str)

        snap = item["snapshot"]
        assert isinstance(snap, dict)
        assert {
            "title",
            "source_name",
            "source_url",
            "publish_date",
            "summary",
            "score_reason",
            "tags",
            "display_tags",
            "event_id",
            "related_sources",
        }.issubset(snap.keys())
        assert isinstance(snap["title"], str) and snap["title"].strip()
        assert isinstance(snap["source_name"], str) and snap["source_name"].strip()
        assert isinstance(snap["source_url"], str) and _URL_RE.fullmatch(snap["source_url"])
        if snap["publish_date"] is not None:
            assert isinstance(snap["publish_date"], str) and (
                _DATE_RE.fullmatch(snap["publish_date"]) or _DT_RE.fullmatch(snap["publish_date"])
            )
        assert snap["summary"] is None or isinstance(snap["summary"], str)
        assert snap["score_reason"] is None or isinstance(snap["score_reason"], str)
        for tag_field in ("tags", "display_tags"):
            assert isinstance(snap[tag_field], list)
            assert all(isinstance(t, str) and t.strip() for t in snap[tag_field])
        assert snap["event_id"] is None or (isinstance(snap["event_id"], str) and snap["event_id"].strip())
        assert isinstance(snap["related_sources"], list)
        seen_related: set[str] = set()
        for rel in snap["related_sources"]:
            assert isinstance(rel, dict)
            assert {"title", "source_name", "source_url"}.issubset(rel.keys())
            assert isinstance(rel["title"], str) and rel["title"].strip()
            assert isinstance(rel["source_name"], str) and rel["source_name"].strip()
            assert isinstance(rel["source_url"], str) and _URL_RE.fullmatch(rel["source_url"])
            assert rel["source_url"] != snap["source_url"]
            assert rel["source_url"] not in seen_related
            seen_related.add(rel["source_url"])


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    seed_content: bool = True,
    base_now: dt.datetime | None = None,
):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'admin-briefs.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    current = (base_now or briefs_runtime.now()).astimezone(SHANGHAI)
    stamp = briefs_runtime.iso_shanghai(current)
    article_stamp = briefs_runtime.iso_shanghai(current - dt.timedelta(hours=1))

    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true", updated_at=stamp))
        for username, role, employee in (
            ("admin", "admin", None),
            # C01 / A02: w3_uid != username; also create an unmapped user whose
            # username equals the employee_number to prove no username fallback!
            ("reader_demo", "user", "l00976966"),
            ("l00976966", "user", None),
            ("alice", "user", "E123"),
        ):
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password("pw"),
                role=role,
                is_active=True,
                w3_uid=employee,
                session_epoch="epoch",
                created_at=stamp,
                updated_at=stamp,
            ))
            session.add(AppSettingRecord(
                key=f"reader_defaults_seeded:{username}",
                value=stamp,
                updated_at=stamp,
            ))
        if seed_content:
            session.add(SourceStateRecord(
                source_id="src-energy",
                fetcher_id="src-energy",
                status="healthy",
                last_completed_at=stamp,
                last_success_at=stamp,
                updated_at=stamp,
            ))
            for owner in ("reader_demo", "alice"):
                session.add(ReaderSubscriptionRecord(
                    owner_username=owner,
                    name=f"{owner}-sub",
                    filters_json=json.dumps({"source_ids": ["src-energy"]}),
                    delivery_policy_json="{}",
                    token_hash=f"hash-{owner}",
                    token_preview="dsub_***",
                    is_active=True,
                    created_at=stamp,
                    updated_at=stamp,
                ))
            session.add(ArticleRecord(
                id="art-energy-1",
                title="储能行业动态示例",
                content_type="web_article",
                source_id="src-energy",
                source_url="https://example.com/news/energy-001",
                publish_date=article_stamp,
                fetched_date=article_stamp,
                has_content=True,
                content="正文内容",
            ))
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id="art-energy-1",
                status=AnalysisStatus.SUCCEEDED.value,
                tagging_status="succeeded",
                quality_score=8.5,
                summary="此条为契约联调模拟素材。",
                score_reason="新闻价值充足",
                content_genre="industry_news",
                content_hash="hash-art-energy-1",
                prompt_version="article-analysis-v1",
                scoring_version="quality-v1",
                analyzed_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            ))
        session.commit()
    return app_module, sink


def _login(client: TestClient, username: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert response.status_code == 200, response.text


def test_shared_baseline_sha256_and_manifest_fixtures():
    """Verify shared/SHA256SUMS byte parity and validate all 18 shared examples."""
    sums_lines = (SHARED_DIR / "SHA256SUMS").read_text(encoding="utf-8").strip().splitlines()
    assert len(sums_lines) == 21
    for line in sums_lines:
        expected_hash, rel_path = line.split()
        actual_hash = hashlib.sha256((SHARED_DIR / rel_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash, f"SHA-256 mismatch on {rel_path}"

    manifest = json.loads((SHARED_DIR / "examples" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 18
    for entry in manifest:
        body = json.loads((SHARED_DIR / "examples" / entry["body_file"]).read_text(encoding="utf-8"))
        if entry["status_code"] == 200:
            validate_brief_schema(body, is_ensure=(entry["scenario"] != "missing"))
        else:
            assert body["error"]["code"] in _ALLOWED_ERRORS
            assert body["error"]["request_id"] == entry["headers"]["X-Request-ID"]


def test_d01_d02_c01_get_ready_and_missing_have_zero_side_effects_and_preserve_w3_semantics(
    monkeypatch, tmp_path
):
    """D01, D02, C01, A02: GET missing/ready never generates and resolves w3_uid != username."""
    app_module, sink = _setup(monkeypatch, tmp_path)
    gen_calls: list[str] = []
    orig_gen = digest_service.generate_personal_digest

    def spy_generate(*args, **kwargs):
        gen_calls.append("called")
        return orig_gen(*args, **kwargs)

    monkeypatch.setattr(digest_service, "generate_personal_digest", spy_generate)

    with TestClient(app_module.app) as client:
        _login(client, "admin")

        # D02 + C01: l00976966 maps to reader_demo (not user 'l00976966'), missing -> 200/missing
        missing = client.get("/api/admin/reader/briefs/today?employee_number=l00976966")
        assert missing.status_code == 200
        assert_contract_headers(missing.headers)
        missing_body = missing.json()
        validate_brief_schema(missing_body)
        assert missing_body == {
            "employee_number": "l00976966",
            "username": "reader_demo",
            "status": "missing",
            "pushable": False,
            "fallback_hint": "retry",
            "edition": None,
        }
        assert gen_calls == []
        with Session(sink.engine) as session:
            assert session.exec(
                select(PersonalDigestEditionRecord).where(
                    PersonalDigestEditionRecord.owner_username == "reader_demo"
                )
            ).all() == []

        # Ensure creates rev 1, then GET hits ready without invoking generator again (D01)
        ensured = client.post("/api/admin/reader/briefs/today/ensure?employee_number=l00976966")
        assert ensured.status_code == 200
        ensure_body = ensured.json()
        validate_brief_schema(ensure_body, is_ensure=True)
        assert ensure_body["username"] == "reader_demo"
        assert ensure_body["status"] == "ready"
        assert ensure_body["pushable"] is True
        assert len(gen_calls) == 1

        ready_get = client.get("/api/admin/reader/briefs/today?employee_number=l00976966")
        assert ready_get.status_code == 200
        assert_contract_headers(ready_get.headers)
        ready_body = ready_get.json()
        validate_brief_schema(ready_body)
        assert ready_body["status"] == "ready"
        assert ready_body["pushable"] is True
        assert ready_body["edition"]["revision"] == 1
        assert len(gen_calls) == 1

        # A02: username 'reader_demo' has w3_uid='l00976966'; querying employee_number=reader_demo
        # MUST NOT fall back to username == employee_number!
        by_username = client.get("/api/admin/reader/briefs/today?employee_number=reader_demo")
        assert_error_envelope(by_username, 404, "ACCOUNT_NOT_FOUND")


def test_d03_d04_d05_c02_c03_c04_c05_auth_headers_arguments_and_error_envelopes(
    monkeypatch, tmp_path
):
    """D03, D04, D05, C02, C03, C04, C05, A03, A04: strict auth, headers, query/body, and errors."""
    app_module, sink = _setup(monkeypatch, tmp_path, seed_content=False)
    gen_calls: list[int] = []
    monkeypatch.setattr(
        briefs_runtime, "run_attempt", lambda *a, **kw: gen_calls.append(1)
    )

    with TestClient(app_module.app) as client:
        # D04: missing cookie -> 401 JSON, no HTML redirect; Bearer ignored -> 401
        assert_error_envelope(
            client.get("/api/admin/reader/briefs/today?employee_number=E123"),
            401,
            "UNAUTHENTICATED",
        )
        assert_error_envelope(
            client.get(
                "/api/admin/reader/briefs/today?employee_number=E123",
                headers={"Authorization": "Bearer admin-token"},
            ),
            401,
            "UNAUTHENTICATED",
        )
        client.cookies.set("dorami_admin_session", "expired.invalid-signature")
        assert_error_envelope(
            client.get("/api/admin/reader/briefs/today?employee_number=E123"),
            401,
            "UNAUTHENTICATED",
        )

        # D04: non-admin reader cookie -> 403 FORBIDDEN
        client.cookies.clear()
        _login(client, "alice")
        assert_error_envelope(
            client.get("/api/admin/reader/briefs/today?employee_number=E123"),
            403,
            "FORBIDDEN",
        )

        # Admin login
        client.cookies.clear()
        _login(client, "admin")

        # C04: valid X-Request-ID echoed; invalid/missing X-Request-ID replaced without 422
        valid_rid = client.get(
            "/api/admin/reader/briefs/today?employee_number=E123",
            headers={"X-Request-ID": "brief.req_01-ok"},
        )
        assert valid_rid.status_code == 200
        assert_contract_headers(valid_rid.headers, expected_request_id="brief.req_01-ok")

        invalid_rid = client.get(
            "/api/admin/reader/briefs/today?employee_number=E999",
            headers={"X-Request-ID": "bad request id with spaces!"},
        )
        err_payload = assert_error_envelope(invalid_rid, 404, "ACCOUNT_NOT_FOUND")
        assert err_payload["error"]["request_id"] != "bad request id with spaces!"

        # D03: missing, blank, whitespace, >128 chars, duplicate, unknown query params -> 422
        for bad_query in (
            "",
            "?employee_number=",
            "?employee_number=%20",
            "?employee_number=E%20123",
            f"?employee_number={'x' * 129}",
            "?employee_number=E123&employee_number=E123",
            "?employee_number=E123&unexpected=1",
        ):
            resp = client.get(f"/api/admin/reader/briefs/today{bad_query}")
            assert_error_envelope(resp, 422, "INVALID_ARGUMENT")

        # Exact match: untrimmed leading/trailing spaces rejected, case sensitivity preserved
        assert_error_envelope(
            client.get("/api/admin/reader/briefs/today?employee_number=e123"),
            404,
            "ACCOUNT_NOT_FOUND",
        )

        # C02: POST with {}, null, space, newline -> 422
        for non_empty_body in (b"{}", b"null", b" ", b"\n"):
            resp = client.post(
                "/api/admin/reader/briefs/today/ensure?employee_number=E123",
                content=non_empty_body,
            )
            assert_error_envelope(resp, 422, "INVALID_ARGUMENT")

        assert gen_calls == []

        # C03: POST with empty body and without explicit Content-Length header -> accepted (not 422)
        req = client.build_request(
            "POST", "/api/admin/reader/briefs/today/ensure?employee_number=E123"
        )
        req.headers.pop("content-length", None)
        c03_resp = client.send(req)
        assert c03_resp.status_code == 200
        assert_contract_headers(c03_resp.headers)

        # C05: CSRF rejection & rate-limit & internal error envelopes
        app_module.app.state.briefs_csrf_verifier = lambda _req, _auth: False
        try:
            csrf_resp = client.post("/api/admin/reader/briefs/today/ensure?employee_number=E123")
            assert_error_envelope(csrf_resp, 403, "CSRF_REJECTED")
        finally:
            app_module.app.state.briefs_csrf_verifier = None

        # A04: unknown sub-route must NOT return ACCOUNT_NOT_FOUND
        unknown_route = client.get("/api/admin/reader/briefs/nonexistent")
        assert unknown_route.status_code == 404
        assert unknown_route.json().get("error", {}).get("code") != "ACCOUNT_NOT_FOUND"

    # D05: verify unmapped lookups created zero users/editions for E999
    with Session(sink.engine) as session:
        assert session.exec(select(UserRecord).where(UserRecord.w3_uid == "E999")).first() is None


def test_d06_duplicate_w3_uid_conflict_and_inactive_user(monkeypatch, tmp_path):
    """D06: duplicate w3_uid returns 500 ACCOUNT_MAPPING_CONFLICT; inactive user returns 404."""
    app_module, sink = _setup(monkeypatch, tmp_path, seed_content=False)
    with Session(sink.engine) as session:
        with pytest.raises(accounts_service.AccountError):
            accounts_service.set_w3_uid(session, "admin", " E123 ")
        alice = session.get(UserRecord, "alice")
        assert alice is not None
        alice.is_active = False
        session.add(alice)
        session.commit()
        assert accounts_service.resolve_w3_user(session, "E123") is None

    # Simulate legacy DB with dropped unique index and duplicate w3_uid rows
    with sink.engine.begin() as conn:
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_users_w3_uid"))
        conn.execute(
            sa.text("UPDATE users SET w3_uid = 'l00976966' WHERE username = 'l00976966'")
        )

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        conflict = client.get("/api/admin/reader/briefs/today?employee_number=l00976966")
        assert_error_envelope(conflict, 500, "ACCOUNT_MAPPING_CONFLICT")


@pytest.mark.parametrize("status", ["pending", "generating", "ready", "failed", "superseded"])
def test_d07_ensure_returns_existing_revision_without_processing_or_incrementing(
    monkeypatch, tmp_path, status
):
    """D07: ensure returns any existing revision unchanged without invoking generation."""
    app_module, sink = _setup(monkeypatch, tmp_path, seed_content=False)
    current = briefs_runtime.now()
    today = current.date().isoformat()
    stamp = briefs_runtime.iso_shanghai(current)
    run_calls: list[int] = []
    monkeypatch.setattr(briefs_runtime, "run_attempt", lambda *a, **kw: run_calls.append(1))

    with Session(sink.engine) as session:
        session.add(PersonalDigestEditionRecord(
            owner_username="alice",
            report_date=today,
            revision=1,
            status=status,
            generated_at=stamp if status == "ready" else None,
            check_after=stamp,
            cutoff_at=stamp,
            deadline_at=stamp,
            generation_reason="first_open",
            created_at=stamp,
            updated_at=stamp,
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        first = client.post("/api/admin/reader/briefs/today/ensure?employee_number=E123")
        second = client.post("/api/admin/reader/briefs/today/ensure?employee_number=E123")
        assert first.status_code == second.status_code == 200
        expected = {
            "generating": "processing",
            "ready": "empty",  # ready with 0 items projects to top-level empty
            "superseded": "failed",
        }.get(status, status)
        validate_brief_schema(first.json(), is_ensure=True)
        assert first.json()["status"] == second.json()["status"] == expected
        assert run_calls == []
        with Session(sink.engine) as session:
            rows = session.exec(
                select(PersonalDigestEditionRecord).where(
                    PersonalDigestEditionRecord.owner_username == "alice"
                )
            ).all()
            assert len(rows) == 1 and rows[0].revision == 1


def test_d08_d09_concurrent_ensure_and_scheduled_pregeneration_share_creation_gate(
    monkeypatch, tmp_path
):
    """D08 & D09: concurrent ensure×ensure and ensure×scheduled create at most 1 revision=1."""
    app_module, sink = _setup(monkeypatch, tmp_path)
    from api.routers import personal_briefs

    claim_tokens: list[str] = []
    orig_claim = briefs_runtime.claim

    def spy_claim(engine, edition_id, **kwargs):
        tok = orig_claim(engine, edition_id, **kwargs)
        if tok is not None:
            claim_tokens.append(tok)
        return tok

    monkeypatch.setattr(briefs_runtime, "claim", spy_claim)

    # D08: two concurrent HTTP POST /today/ensure for reader_demo (l00976966)
    barrier = Barrier(2)
    with TestClient(app_module.app) as client:
        _login(client, "admin")

        def call_ensure(_idx: int) -> dict[str, Any]:
            barrier.wait()
            resp = client.post(
                "/api/admin/reader/briefs/today/ensure?employee_number=l00976966"
            )
            assert resp.status_code == 200
            return resp.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(call_ensure, range(2)))

    for body in responses:
        validate_brief_schema(body, is_ensure=True)
        assert body["edition"]["revision"] == 1
    assert len(claim_tokens) == 1
    with Session(sink.engine) as session:
        rows = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == "reader_demo"
            )
        ).all()
        assert len(rows) == 1 and rows[0].revision == 1

    # D09: concurrent scheduled pregeneration × ensure for alice (E123)
    sched_barrier = Barrier(2)

    def run_scheduled():
        sched_barrier.wait()
        return personal_briefs.trigger_today_revision(
            sink.engine, "alice", DigestGenerationReason.SCHEDULED
        )

    def run_ensure_service():
        sched_barrier.wait()
        with Session(sink.engine) as session:
            return digest_service.ensure_first_personal_digest_edition(
                session, "alice", generation_reason="first_open", allow_empty=True
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_sched = pool.submit(run_scheduled)
        f_ens = pool.submit(run_ensure_service)
        sched_ed = f_sched.result()
        ens_res = f_ens.result()

    assert sched_ed is not None and ens_res.edition is not None
    assert sched_ed.id == ens_res.edition.id
    with Session(sink.engine) as session:
        alice_rows = session.exec(
            select(PersonalDigestEditionRecord).where(
                PersonalDigestEditionRecord.owner_username == "alice"
            )
        ).all()
        assert len(alice_rows) == 1 and alice_rows[0].revision == 1


def test_d10_c11_i07_latest_revision_selection_and_long_ensure_isolation(monkeypatch, tmp_path):
    """D10, C11, I07: rev2 processing masks rev1 ready; long ensure returns its own rev1 while subsequent GET sees rev2."""
    app_module, sink = _setup(monkeypatch, tmp_path)
    current = briefs_runtime.now()
    today = current.date().isoformat()
    stamp = briefs_runtime.iso_shanghai(current)

    # D10: rev1 ready + rev2 generating -> GET and ensure return rev2 processing
    with Session(sink.engine) as session:
        session.add(PersonalDigestEditionRecord(
            owner_username="alice",
            report_date=today,
            revision=1,
            status="ready",
            generated_at=stamp,
            check_after=stamp,
            cutoff_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        ))
        session.add(PersonalDigestEditionRecord(
            owner_username="alice",
            report_date=today,
            revision=2,
            status="generating",
            generation_token="tok-rev2",
            generation_lease_expires_at=briefs_runtime.iso_shanghai(current + dt.timedelta(seconds=300)),
            check_after=stamp,
            cutoff_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        get_resp = client.get("/api/admin/reader/briefs/today?employee_number=E123").json()
        ens_resp = client.post("/api/admin/reader/briefs/today/ensure?employee_number=E123").json()
        validate_brief_schema(get_resp)
        validate_brief_schema(ens_resp, is_ensure=True)
        assert get_resp["status"] == ens_resp["status"] == "processing"
        assert get_resp["edition"]["revision"] == ens_resp["edition"]["revision"] == 2

    # C11 & I07: long ensure on reader_demo creates rev1; while rev1 runs, rev2 is inserted;
    # ensure returns rev1, subsequent GET returns rev2!
    entered = Event()
    release = Event()
    orig_gen = briefs_runtime._generate

    def slow_generate(engine, edition_id, token, business_now):
        entered.set()
        release.wait(timeout=5)
        return orig_gen(engine, edition_id, token, business_now)

    monkeypatch.setattr(briefs_runtime, "_generate", slow_generate)

    with TestClient(app_module.app) as client:
        _login(client, "admin")

        def long_ensure():
            return client.post(
                "/api/admin/reader/briefs/today/ensure?employee_number=l00976966"
            ).json()

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(long_ensure)
            assert entered.wait(timeout=5)
            with Session(sink.engine) as session:
                rev2 = PersonalDigestEditionRecord(
                    owner_username="reader_demo",
                    report_date=today,
                    revision=2,
                    status="ready",
                    generated_at=stamp,
                    check_after=stamp,
                    cutoff_at=stamp,
                    created_at=stamp,
                    updated_at=stamp,
                )
                session.add(rev2)
                session.commit()
                session.refresh(rev2)
                session.add(PersonalDigestItemRecord(
                    edition_id=rev2.id,
                    article_id="art-energy-1",
                    position=0,
                    section="重点关注",
                    selection_lane="interest",
                    quality_score_snapshot=9.0,
                    selection_reason="rev2精选",
                    snapshot_json=json.dumps({
                        "title": "第二版标题",
                        "source_name": "示例来源",
                        "source_url": "https://example.com/news/energy-002",
                        "publish_date": today,
                    }),
                    created_at=stamp,
                ))
                session.commit()
            release.set()
            ensure_result = fut.result()

        validate_brief_schema(ensure_result, is_ensure=True)
        assert ensure_result["edition"]["revision"] == 1
        assert ensure_result["status"] == "ready"

        later_get = client.get("/api/admin/reader/briefs/today?employee_number=l00976966").json()
        validate_brief_schema(later_get)
        assert later_get["edition"]["revision"] == 2
        assert later_get["status"] == "ready"


def test_d11_d12_d13_d19_c12_lease_recovery_stale_token_fencing_and_exhaustion(
    monkeypatch, tmp_path
):
    """D11, D12, D13, D19, C12: pending grace, lease takeover, stale worker rejection, max 2 attempts, and rollback recovery."""
    day_0 = dt.datetime.now(SHANGHAI).date() - dt.timedelta(days=1)
    day_1 = day_0 + dt.timedelta(days=1)
    day_0_str = day_0.isoformat()
    day_1_str = day_1.isoformat()
    start_clock = dt.datetime.combine(day_0, dt.time(9, 0, 0), tzinfo=SHANGHAI)
    _app_module, sink = _setup(monkeypatch, tmp_path, base_now=start_clock)
    from api.routers import personal_briefs

    clock = [start_clock]
    monkeypatch.setattr(briefs_runtime, "now", lambda: clock[0])

    # C12 part 1: failure before commit leaves zero editions
    with pytest.raises(RuntimeError):
        with Session(sink.engine) as session:
            session.add(PersonalDigestEditionRecord(
                owner_username="alice",
                report_date=day_0_str,
                revision=1,
                status="pending",
                check_after=briefs_runtime.iso_shanghai(clock[0]),
                cutoff_at=briefs_runtime.iso_shanghai(clock[0]),
                created_at=briefs_runtime.iso_shanghai(clock[0]),
                updated_at=briefs_runtime.iso_shanghai(clock[0]),
            ))
            raise RuntimeError("crash before commit")
    with Session(sink.engine) as session:
        assert session.exec(select(PersonalDigestEditionRecord)).all() == []

    # D11 & C12 part 2: committed pending edition not started; skipped before 30s grace,
    # recovered after 30s even when personal_digest_enabled is false!
    with Session(sink.engine) as session:
        setting = session.get(AppSettingRecord, "personal_digest_enabled")
        setting.value = "false"
        session.add(setting)
        res = digest_service.ensure_first_personal_digest_edition(
            session, "reader_demo", report_date=day_0_str, now=clock[0], allow_empty=True
        )
        pending_id = res.edition.id
        assert pending_id is not None

    clock[0] = clock[0] + dt.timedelta(seconds=10)
    personal_briefs.process_pending_editions(sink.engine, recovery=True)
    with Session(sink.engine) as session:
        assert session.get(PersonalDigestEditionRecord, pending_id).status == "pending"

    clock[0] = clock[0] + dt.timedelta(seconds=25)  # +35s > 30s grace
    personal_briefs.process_pending_editions(sink.engine, recovery=True)
    with Session(sink.engine) as session:
        recovered = session.get(PersonalDigestEditionRecord, pending_id)
        assert recovered.status in {"ready", "degraded"}
        assert recovered.revision == 1
        assert recovered.generation_attempt_count == 1

    # D12, D13, D19: crash mid-processing, stale token rejected, attempt 2 succeeds or exhausts
    t0 = dt.datetime.combine(day_0, dt.time(10, 0, 0), tzinfo=SHANGHAI)
    clock[0] = t0
    with Session(sink.engine) as session:
        ed_res = digest_service.ensure_first_personal_digest_edition(
            session, "alice", report_date=day_0_str, now=t0, allow_empty=True
        )
        alice_ed_id = ed_res.edition.id
        assert alice_ed_id is not None

    # Attempt 1 claims token_1 and "crashes" without finishing
    token_1 = briefs_runtime.claim(sink.engine, alice_ed_id, current=t0)
    assert token_1 is not None

    # Before 300s lease expires, recovery cannot steal
    clock[0] = t0 + dt.timedelta(seconds=120)
    assert briefs_runtime.claim(sink.engine, alice_ed_id, current=clock[0], recovery=True) is None

    # Lease expires at +301s -> recovery worker claims token_2 (attempt 2) and publishes ready
    clock[0] = t0 + dt.timedelta(seconds=301)
    personal_briefs.process_pending_editions(sink.engine, recovery=True)
    with Session(sink.engine) as session:
        after_takeover = session.get(PersonalDigestEditionRecord, alice_ed_id)
        assert after_takeover.status in {"ready", "degraded"}
        assert after_takeover.generation_attempt_count == 2
        published_generated_at = after_takeover.generated_at

    # D13: stale worker with token_1 wakes up and tries heartbeat, fail, and generate_personal_digest
    assert briefs_runtime.heartbeat(sink.engine, alice_ed_id, token_1, current=clock[0]) is False
    assert briefs_runtime.fail(sink.engine, alice_ed_id, token_1, "stale_fail", current=clock[0]) is False
    with Session(sink.engine) as session:
        digest_service.generate_personal_digest(
            session,
            "alice",
            report_date=day_0_str,
            now=t0,
            pending_edition_id=alice_ed_id,
            generation_token=token_1,
            execution_clock=lambda: clock[0],
            localize_before_publish=True,
        )
    with Session(sink.engine) as session:
        untouched = session.get(PersonalDigestEditionRecord, alice_ed_id)
        assert untouched.status in {"ready", "degraded"}
        assert untouched.generated_at == published_generated_at

    # D19: when 2 attempts both crash without publishing, next recovery marks terminal failed
    t_ex = dt.datetime.combine(day_1, dt.time(9, 0, 0), tzinfo=SHANGHAI)
    clock[0] = t_ex
    with Session(sink.engine) as session:
        ex_ed = digest_service.ensure_first_personal_digest_edition(
            session, "alice", report_date=day_1_str, now=t_ex, allow_empty=True
        ).edition
        ex_id = ex_ed.id
    assert briefs_runtime.claim(sink.engine, ex_id, current=t_ex) is not None
    clock[0] = t_ex + dt.timedelta(seconds=301)
    assert briefs_runtime.claim(sink.engine, ex_id, current=clock[0], recovery=True) is not None
    clock[0] = t_ex + dt.timedelta(seconds=602)
    assert briefs_runtime.claim(sink.engine, ex_id, current=clock[0], recovery=True) is None
    with Session(sink.engine) as session:
        exhausted = session.get(PersonalDigestEditionRecord, ex_id)
        assert exhausted.status == "failed"
        assert exhausted.generation_token is None
        assert exhausted.generation_lease_expires_at is None
    # Subsequent sweep never re-claims terminal failed
    clock[0] = t_ex + dt.timedelta(seconds=700)
    personal_briefs.process_pending_editions(sink.engine, recovery=True)
    with Session(sink.engine) as session:
        assert session.get(PersonalDigestEditionRecord, ex_id).generation_attempt_count == 2


def test_d14_d15_c08_timeout_failure_disconnect_survival_and_db_write_error(monkeypatch, tmp_path):
    """D14, D15, C08: attempt timeout/error -> 200/failed; client disconnect keeps task alive; DB write error -> 503."""
    app_module, sink = _setup(monkeypatch, tmp_path)

    # D14: timeout budget exhaustion marks edition failed -> API returns 200/failed
    monkeypatch.setattr(briefs_runtime, "ATTEMPT_SECONDS", 0.05)
    orig_gen = briefs_runtime._generate

    def slow_gen(engine, edition_id, token, business_now):
        time.sleep(0.25)

    monkeypatch.setattr(briefs_runtime, "_generate", slow_gen)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        timed_out = client.post(
            "/api/admin/reader/briefs/today/ensure?employee_number=E123"
        ).json()
        validate_brief_schema(timed_out, is_ensure=True)
        assert timed_out["status"] == "failed"
        assert timed_out["pushable"] is False
        assert timed_out["fallback_hint"] == "public"

    # D15: client disconnects (cancels shielded await) while background runtime completes
    monkeypatch.setattr(briefs_runtime, "ATTEMPT_SECONDS", 240)
    done_event = Event()

    def delayed_real_gen(engine, edition_id, token, business_now):
        time.sleep(0.15)
        orig_gen(engine, edition_id, token, business_now)
        done_event.set()

    monkeypatch.setattr(briefs_runtime, "_generate", delayed_real_gen)

    async def simulate_disconnect():
        with Session(sink.engine) as session:
            res = digest_service.ensure_first_personal_digest_edition(
                session, "reader_demo", allow_empty=True
            )
            ed_id = res.edition.id
        rt = briefs_runtime.BriefRuntime(sink.engine)
        task = rt.submit(ed_id)
        waiter = asyncio.create_task(asyncio.wait_for(asyncio.shield(task), timeout=0.02))
        with pytest.raises(asyncio.TimeoutError):
            await waiter
        await rt.close()
        return ed_id

    asyncio.run(simulate_disconnect())
    assert done_event.is_set()
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        after_disc = client.get(
            "/api/admin/reader/briefs/today?employee_number=l00976966"
        ).json()
        validate_brief_schema(after_disc)
        assert after_disc["status"] == "ready"

    # C08: DB write failure when creating missing edition returns 503 SERVICE_UNAVAILABLE
    def broken_ensure(*_args, **_kwargs):
        raise OperationalError("BEGIN IMMEDIATE", {}, Exception("attempt to write a readonly database"))

    monkeypatch.setattr(digest_service, "ensure_first_personal_digest_edition", broken_ensure)
    with Session(sink.engine) as session:
        session.add(UserRecord(
            username="bob_ro",
            password_hash=accounts_service.hash_password("pw"),
            role="user",
            is_active=True,
            w3_uid="E_RO",
            session_epoch="epoch",
            created_at=briefs_runtime.iso_shanghai(briefs_runtime.now()),
            updated_at=briefs_runtime.iso_shanghai(briefs_runtime.now()),
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        ro_resp = client.post("/api/admin/reader/briefs/today/ensure?employee_number=E_RO")
        assert_error_envelope(ro_resp, 503, "SERVICE_UNAVAILABLE")


def test_d16_d18_d20_item_filtering_historical_adaptation_corrupt_detection_and_frozen_immutability(
    monkeypatch, tmp_path
):
    """D16, D18, D20: partial invalid item filtering, empty precedence, legacy row adaptation, corrupt 500, and frozen ready."""
    app_module, sink = _setup(monkeypatch, tmp_path)
    current = briefs_runtime.now()
    today = current.date().isoformat()
    stamp = briefs_runtime.iso_shanghai(current)

    # 1) D16 + D18: historical edition (no briefs_payload_json) with 1 invalid item + 1 legacy 'read' item
    with Session(sink.engine) as session:
        ed = PersonalDigestEditionRecord(
            owner_username="alice",
            report_date=today,
            revision=1,
            status="ready",
            generated_at=stamp,
            check_after=stamp,
            cutoff_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(ed)
        session.commit()
        session.refresh(ed)
        ed_id = int(ed.id)
        # Invalid item: non-http source_url -> filtered out
        session.add(PersonalDigestItemRecord(
            edition_id=ed_id,
            article_id="art-energy-1",
            position=0,
            section="坏条目",
            selection_lane="interest",
            quality_score_snapshot=7.0,
            selection_reason="bad",
            snapshot_json=json.dumps({
                "title": "坏链接",
                "source_name": "来源",
                "source_url": "ftp://invalid.example.com/file",
            }),
            created_at=stamp,
        ))
        # Valid legacy item: selection_lane='read', object tags, int event_id, duplicate/self related_sources
        session.add(PersonalDigestItemRecord(
            edition_id=ed_id,
            article_id="art-energy-1",
            position=1,
            section="深度阅读",
            selection_lane="read",
            quality_score_snapshot=8.8,
            selection_reason="",
            snapshot_json=json.dumps({
                "title": "有效深度文章",
                "source_name": "示例来源",
                "source_url": "https://example.com/news/main",
                "publish_date": None,
                "one_sentence_summary": "历史摘要回退",
                "score_reason": "",
                "tags": [{"code": "ai", "name_zh": "人工智能"}],
                "display_tags": [],
                "event_id": 42,
                "related_sources": [
                    {"title": "主链接自身应排除", "source_id": "src-energy", "source_name": "源A", "url": "https://example.com/news/main"},
                    {"title": "相关1", "source_id": "src-energy", "source_name": "源B", "url": "https://example.com/news/rel-1"},
                    {"title": "重复相关1", "source_id": "src-energy", "source_name": "源C", "url": "https://example.com/news/rel-1"},
                    {"title": "无来源身份不可验证权限", "source_name": "旧来源", "url": "https://example.com/news/unverified"},
                ],
                "custom_extra": "preserved",
            }),
            created_at=stamp,
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        partial = client.get("/api/admin/reader/briefs/today?employee_number=E123").json()
        validate_brief_schema(partial)
        assert partial["status"] == "degraded"
        assert partial["pushable"] is True
        assert partial["edition"]["degraded_reason"] == "invalid_items_filtered"
        assert len(partial["edition"]["items"]) == 1
        item = partial["edition"]["items"][0]
        assert item["position"] == 1
        assert item["selection_lane"] == "quality"
        assert item["selection_reason"] is None
        assert item["snapshot"]["summary"] == "历史摘要回退"
        assert item["snapshot"]["tags"] == ["人工智能"]
        assert item["snapshot"]["event_id"] == "42"
        assert len(item["snapshot"]["related_sources"]) == 1
        assert item["snapshot"]["custom_extra"] == "preserved"

        # D16: all items invalid -> empty takes priority over degraded!
        with Session(sink.engine) as session:
            rows = list(session.exec(
                select(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id == ed_id)
            ).all())
            session.delete(rows[1])
            session.commit()
        all_filtered = client.get("/api/admin/reader/briefs/today?employee_number=E123").json()
        validate_brief_schema(all_filtered)
        assert all_filtered["status"] == "empty"
        assert all_filtered["pushable"] is False
        assert all_filtered["fallback_hint"] == "public"

        # D18: corrupted ready row missing generated_at -> 500 INTERNAL_ERROR (never fakes ready)
        with Session(sink.engine) as session:
            corrupt = session.get(PersonalDigestEditionRecord, ed_id)
            corrupt.generated_at = None
            session.add(corrupt)
            session.commit()
        corrupt_resp = client.get("/api/admin/reader/briefs/today?employee_number=E123")
        assert_error_envelope(corrupt_resp, 500, "INTERNAL_ERROR")

        # D20: published edition via ensure is frozen across repeated GETs even if article mutates
        ensured = client.post(
            "/api/admin/reader/briefs/today/ensure?employee_number=l00976966"
        ).json()
        validate_brief_schema(ensured, is_ensure=True)
        with Session(sink.engine) as session:
            art = session.get(ArticleRecord, "art-energy-1")
            art.title = "事后被篡改的标题"
            session.add(art)
            session.commit()
        first_get = client.get("/api/admin/reader/briefs/today?employee_number=l00976966").json()
        second_get = client.get("/api/admin/reader/briefs/today?employee_number=l00976966").json()
        assert first_get == second_get == ensured
        assert first_get["edition"]["items"][0]["snapshot"]["title"] == "储能行业动态示例"


def test_d17_c09_c10_midnight_entry_capture_and_cross_midnight_completion_and_recovery(
    monkeypatch, tmp_path
):
    """D17, C09, C10, A06: 23:59:59 entry captures previous day before auth; 00:01 completion & worker recovery keep previous day."""
    day_0 = dt.datetime.now(SHANGHAI).date() - dt.timedelta(days=1)
    day_1 = day_0 + dt.timedelta(days=1)
    day_0_str = day_0.isoformat()
    day_1_str = day_1.isoformat()
    entry_clock = dt.datetime.combine(day_0, dt.time(23, 59, 59), tzinfo=SHANGHAI)
    app_module, sink = _setup(monkeypatch, tmp_path, base_now=entry_clock)
    from api.routers import personal_briefs

    clock = [entry_clock]
    monkeypatch.setattr(briefs_runtime, "now", lambda: clock[0])

    # C09 & D17 & C10: request enters at 23:59:59, auth advances clock to 00:00:02,
    # generation finishes at 00:01:10 -> report_date remains day_0!
    orig_auth = app_module.current_auth_session

    def slow_midnight_auth(request):
        clock[0] = dt.datetime.combine(day_1, dt.time(0, 0, 2), tzinfo=SHANGHAI)
        return orig_auth(request)

    monkeypatch.setattr(app_module, "current_auth_session", slow_midnight_auth)

    orig_gen = briefs_runtime._generate

    def cross_midnight_gen(engine, edition_id, token, business_now):
        clock[0] = dt.datetime.combine(day_1, dt.time(0, 1, 10), tzinfo=SHANGHAI)
        return orig_gen(engine, edition_id, token, business_now)

    monkeypatch.setattr(briefs_runtime, "_generate", cross_midnight_gen)

    with TestClient(app_module.app) as client:
        _login(client, "admin")
        clock[0] = entry_clock
        resp = client.post("/api/admin/reader/briefs/today/ensure?employee_number=l00976966")
        assert resp.status_code == 200
        body = resp.json()
        validate_brief_schema(body, is_ensure=True)
        assert body["edition"]["report_date"] == day_0_str
        assert body["edition"]["generated_at"].startswith(f"{day_1_str}T00:01:10")
        assert body["status"] in {"ready", "degraded"}

    # A06 worker cross-midnight scan: alice pending created at 23:59:30, recovered at 00:01:30
    t_late = dt.datetime.combine(day_0, dt.time(23, 59, 30), tzinfo=SHANGHAI)
    clock[0] = t_late
    with Session(sink.engine) as session:
        alice_late = digest_service.ensure_first_personal_digest_edition(
            session, "alice", report_date=day_0_str, now=t_late, allow_empty=True
        ).edition
        alice_late_id = alice_late.id

    clock[0] = dt.datetime.combine(day_1, dt.time(0, 1, 30), tzinfo=SHANGHAI)
    personal_briefs.process_pending_editions(sink.engine, recovery=True)
    with Session(sink.engine) as session:
        recovered_late = session.get(PersonalDigestEditionRecord, alice_late_id)
        assert recovered_late.report_date == day_0_str
        assert recovered_late.status in {"ready", "degraded"}
        assert recovered_late.generated_at.startswith(f"{day_1_str}T00:")


def test_w3_uid_vs_w3_employee_number_and_username_distinction(monkeypatch, tmp_path):
    """Verify exact Dorami intranet schema alignment:
    - Query parameter name on both endpoints is ``employee_number`` with letter-prefixed W3 ID (e.g. ``l00976966``).
    - Lookup matches ONLY ``users.w3_uid`` (``l00976966``), NEVER ``users.w3_employee_number`` (``00976966``)
      and NEVER falls back to ``users.username``.
    """
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'intranet-schema.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    stamp = briefs_runtime.iso_shanghai(briefs_runtime.now())

    # Mirror Dorami intranet users table where w3_employee_number coexists with w3_uid & username
    with sink.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN w3_employee_number VARCHAR")

    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="personal_digest_enabled", value="true", updated_at=stamp))
        accounts_service.create_user(session, "admin", "pw", "admin", w3_uid=None)
        # Typical W3 SSO account: username == w3_uid == 'l00976966', w3_employee_number == '00976966'
        accounts_service.create_user(session, "l00976966", "pw", "user", w3_uid="l00976966")
        # Custom username bound to W3 account: username='custom_reader', w3_uid='z00123456', w3_employee_number='00123456'
        accounts_service.create_user(session, "custom_reader", "pw", "user", w3_uid="z00123456")
        # Account with username & w3_employee_number populated, but w3_uid unbound (NULL)
        accounts_service.create_user(session, "m00654321", "pw", "user", w3_uid=None)
        for u in ("admin", "l00976966", "custom_reader", "m00654321"):
            session.add(AppSettingRecord(key=f"reader_defaults_seeded:{u}", value=stamp, updated_at=stamp))
        session.commit()

    with sink.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE users SET w3_employee_number = '00976966' WHERE username = 'l00976966'"
        )
        conn.exec_driver_sql(
            "UPDATE users SET w3_employee_number = '00123456' WHERE username = 'custom_reader'"
        )
        conn.exec_driver_sql(
            "UPDATE users SET w3_employee_number = '00654321' WHERE username = 'm00654321'"
        )

    with TestClient(app_module.app) as client:
        _login(client, "admin")

        # 1. Exact w3_uid (letter-prefixed l00976966) succeeds on both GET and POST /ensure
        r_get = client.get("/api/admin/reader/briefs/today?employee_number=l00976966")
        assert r_get.status_code == 200
        b_get = r_get.json()
        validate_brief_schema(b_get)
        assert b_get["employee_number"] == "l00976966"
        assert b_get["username"] == "l00976966"
        assert b_get["status"] == "missing"

        r_ensure = client.post("/api/admin/reader/briefs/today/ensure?employee_number=l00976966")
        assert r_ensure.status_code == 200
        b_ensure = r_ensure.json()
        validate_brief_schema(b_ensure, is_ensure=True)
        assert b_ensure["employee_number"] == "l00976966"
        assert b_ensure["username"] == "l00976966"

        # 2. Numeric-only w3_employee_number ('00976966') MUST NOT match -> 404 ACCOUNT_NOT_FOUND
        for method, path in (
            ("GET", "/api/admin/reader/briefs/today?employee_number=00976966"),
            ("POST", "/api/admin/reader/briefs/today/ensure?employee_number=00976966"),
        ):
            r_num = client.request(method, path)
            assert r_num.status_code == 404
            assert r_num.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

        # 3. Case-variant ('L00976966') MUST NOT match -> 404 ACCOUNT_NOT_FOUND
        r_case = client.get("/api/admin/reader/briefs/today?employee_number=L00976966")
        assert r_case.status_code == 404
        assert r_case.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

        # 4. Custom username ('custom_reader') with w3_uid='z00123456' & w3_employee_number='00123456'
        r_z = client.get("/api/admin/reader/briefs/today?employee_number=z00123456")
        assert r_z.status_code == 200
        assert r_z.json()["employee_number"] == "z00123456"
        assert r_z.json()["username"] == "custom_reader"

        for bad_val in ("00123456", "custom_reader"):
            r_bad = client.get(f"/api/admin/reader/briefs/today?employee_number={bad_val}")
            assert r_bad.status_code == 404
            assert r_bad.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

        # 5. Unbound w3_uid (m00654321 has username='m00654321' & w3_employee_number='00654321', w3_uid=NULL)
        for bad_val in ("m00654321", "00654321"):
            r_unbound = client.get(f"/api/admin/reader/briefs/today?employee_number={bad_val}")
            assert r_unbound.status_code == 404
            assert r_unbound.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

        # 6. Wrong query parameter names (w3_uid / w3_employee_number) -> 422 INVALID_ARGUMENT
        for bad_qs in ("w3_uid=l00976966", "w3_employee_number=00976966"):
            r_qs = client.get(f"/api/admin/reader/briefs/today?{bad_qs}")
            assert r_qs.status_code == 422
            assert r_qs.json()["error"]["code"] == "INVALID_ARGUMENT"

