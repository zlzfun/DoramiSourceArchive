"""Source-audio TTL schema, migration, and configuration contracts."""

from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alembic import command
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from config import PodcastArtifactStorageConfig, load_config
from models.db import PodcastProcessingCommandRecord, PodcastProcessingRecord
from storage.impl.db_storage import DatabaseStorage
from storage.migrations import make_alembic_config


PARENT_REVISION = "4a6f9c2d8e31"
HEAD_REVISION = "7c2e1a9b4d60"
CURRENT_HEAD_REVISION = "6b8d2f4a9c70"
STAMP = "2026-09-06T08:00:00.000000+00:00"
AUDIO_TRIGGER_NAMES = {
    "podcast_audio_dependency_insert",
    "podcast_audio_dependency_update",
    "podcast_audio_processing_insert",
    "podcast_audio_processing_update",
    "podcast_audio_binding_immutable",
    "podcast_script_audio_invalidate_update",
    "podcast_script_audio_invalidate_delete",
}
NEW_ARTIFACT_INDEXES = {
    "ix_podcast_artifacts_source_locator_hash",
    "ix_podcast_artifacts_expires_at",
    "ix_podcast_artifacts_expired_at",
    "ix_podcast_artifacts_kind_status_expires",
}


def _sqlite_trigger_names(connection) -> set[str]:
    return set(connection.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    )).scalars())


def _insert_episode(connection, episode_id: str = "source-cache-episode") -> None:
    connection.execute(
        text(
            "INSERT INTO articles "
            "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
            "archive_updated_at,run_scope,has_content,extensions_json,"
            "analysis_authority_id,read_count) VALUES "
            "(:id,'Episode','podcast_episode','podcast-source','',:stamp,:stamp,"
            "'','ad_hoc',1,'{}','',0)"
        ),
        {"id": episode_id, "stamp": STAMP},
    )


def _insert_artifact(
    connection,
    *,
    artifact_id: str,
    kind: str = "source_audio",
    status: str = "ready",
    expires_at: str | None = STAMP,
    expired_at: str | None = None,
    source_locator_hash: str | None = None,
    current_schema: bool = True,
) -> None:
    columns = (
        "id,episode_id,kind,content_hash,mime,ext,size_bytes,status,provenance,"
        "authority_id,created_at,updated_at"
    )
    values = (
        ":id,'source-cache-episode',:kind,:content_hash,'audio/mpeg','.mp3',1,"
        ":status,'publisher_enclosure','',:stamp,:stamp"
    )
    params = {
        "id": artifact_id,
        "kind": kind,
        "content_hash": "a" * 64,
        "status": status,
        "stamp": STAMP,
    }
    if current_schema:
        columns += ",source_locator_hash,expires_at,expired_at"
        values += ",:source_locator_hash,:expires_at,:expired_at"
        params.update(
            source_locator_hash=source_locator_hash,
            expires_at=expires_at,
            expired_at=expired_at,
        )
    connection.execute(
        text(f"INSERT INTO podcast_artifacts ({columns}) VALUES ({values})"),
        params,
    )


def test_source_cache_config_supports_ini_and_environment(monkeypatch, tmp_path):
    ini = tmp_path / "backend.ini"
    ini.write_text(
        "[podcast_artifacts]\n"
        "total_quota_mb = 512\n"
        "source_audio_quota_mb = 64\n"
        "source_audio_ttl_seconds = 7200\n"
        "download_timeout_seconds = 45\n"
        "download_max_redirects = 4\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))

    configured = load_config().podcast_artifacts
    assert configured.source_audio_quota_bytes == 64 * 1024 * 1024
    assert configured.source_audio_ttl_seconds == 7200
    assert configured.download_timeout_seconds == 45
    assert configured.download_max_redirects == 4

    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_QUOTA_BYTES", "123456")
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_TTL_SECONDS", "90")
    monkeypatch.setenv("DORAMI_PODCAST_ARTIFACT_DOWNLOAD_MAX_REDIRECTS", "2")
    overridden = load_config().podcast_artifacts
    assert overridden.source_audio_quota_bytes == 123456
    assert overridden.source_audio_ttl_seconds == 90
    assert overridden.download_max_redirects == 2


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"source_audio_ttl_seconds": 0}, "ttl_seconds must be positive"),
        ({"source_audio_quota_bytes": 0}, "quota must be positive"),
        ({"download_max_redirects": -1}, "redirects cannot be negative"),
    ],
)
def test_source_cache_config_rejects_unsafe_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        PodcastArtifactStorageConfig(**overrides)


def test_source_cache_config_is_exposed_in_examples_and_compose():
    root = Path(__file__).parent.parent
    examples = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("config/backend.example.ini", "config/production.example.ini")
    )
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    for option in (
        "source_audio_ttl_seconds",
        "source_audio_quota_mb",
        "download_max_redirects",
    ):
        assert examples.count(option) == 2
    for variable in (
        "DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_TTL_SECONDS",
        "DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_QUOTA_MB",
        "DORAMI_PODCAST_ARTIFACT_DOWNLOAD_MAX_REDIRECTS",
    ):
        assert variable in compose


def test_storage_openapi_paths_match_the_admin_client_contract():
    contract = (
        Path(__file__).parent.parent
        / "specs/007-podcast-intelligence/contracts/podcast-api.yaml"
    ).read_text(encoding="utf-8")
    for path in (
        "/api/admin/podcast-artifacts/stats:",
        "/api/admin/podcast-artifacts:",
        "/api/admin/podcast-artifacts/reconcile:",
        "/api/admin/podcast-episodes/{episode_id}/cache-source-audio:",
    ):
        assert path in contract
    assert "/api/admin/podcast-storage/" not in contract


def test_runtime_openapi_declares_source_cache_success_and_stable_errors():
    from api import app as app_module

    operation = app_module.app.openapi()["paths"][
        "/api/admin/podcast-episodes/{episode_id}/cache-source-audio"
    ]["post"]
    success_ref = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert success_ref.endswith("/PodcastAudioArtifactResponse")
    for status in (
        "400", "401", "403", "404", "409", "413", "415",
        "422", "502", "503", "504", "507",
    ):
        error_ref = operation["responses"][status]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert error_ref.endswith("/PodcastDomainErrorResponse")


@pytest.mark.parametrize("schema_origin", ["create_all", "alembic"])
def test_source_cache_constraints_and_indexes(schema_origin, tmp_path):
    db_url = f"sqlite:///{tmp_path / f'{schema_origin}.db'}"
    if schema_origin == "create_all":
        engine = DatabaseStorage(db_url).engine
    else:
        command.upgrade(make_alembic_config(db_url), "head")
        engine = create_engine(db_url)

    artifact_indexes = {
        item["name"] for item in inspect(engine).get_indexes("podcast_artifacts")
    }
    processing_indexes = {
        item["name"] for item in inspect(engine).get_indexes("podcast_processings")
    }
    assert NEW_ARTIFACT_INDEXES.issubset(artifact_indexes)
    assert "ix_podcast_processings_input_status" in processing_indexes

    with engine.begin() as connection:
        _insert_episode(connection)
        _insert_artifact(
            connection,
            artifact_id="valid-expired",
            status="expired",
            expires_at=STAMP,
            expired_at=STAMP,
            source_locator_hash="b" * 64,
        )
        _insert_artifact(
            connection,
            artifact_id="immutable-cache-binding",
            status="ready",
            expires_at="2099-01-01T00:00:00.000000+00:00",
            source_locator_hash="c" * 64,
        )

    for assignment in (
        "expires_at = '2100-01-01T00:00:00.000000+00:00'",
        f"source_locator_hash = '{'d' * 64}'",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(
                "UPDATE podcast_artifacts SET "
                f"{assignment} WHERE id = 'immutable-cache-binding'"
            ))

    invalid_rows = (
        {"artifact_id": "source-no-expiry", "expires_at": None},
        {
            "artifact_id": "digest-with-expiry",
            "kind": "digest_audio_zh",
            "status": "withdrawn",
            "expires_at": STAMP,
        },
        {
            "artifact_id": "expired-without-time",
            "status": "expired",
            "expires_at": STAMP,
            "expired_at": None,
        },
        {
            "artifact_id": "bad-locator-hash",
            "source_locator_hash": "not-a-sha256",
        },
        {
            "artifact_id": "published-source",
            "status": "published",
        },
    )
    for row in invalid_rows:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_artifact(connection, **row)
    engine.dispose()


def test_migration_backfills_historical_source_expiry(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, PARENT_REVISION)
    engine = create_engine(db_url)
    with engine.begin() as connection:
        _insert_episode(connection)
        _insert_artifact(
            connection,
            artifact_id="historical-source",
            current_schema=False,
        )
        _insert_artifact(
            connection,
            artifact_id="historical-published-source",
            status="published",
            current_schema=False,
        )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT status, expires_at, expired_at, source_locator_hash "
                "FROM podcast_artifacts WHERE id = 'historical-source'"
            )).mappings().one()
        assert row == {
            "status": "ready",
            "expires_at": STAMP,
            "expired_at": None,
            "source_locator_hash": None,
        }
        with engine.connect() as connection:
            published = connection.execute(text(
                "SELECT status, expires_at, published_at "
                "FROM podcast_artifacts "
                "WHERE id = 'historical-published-source'"
            )).mappings().one()
        assert published == {
            "status": "ready",
            "expires_at": STAMP,
            "published_at": None,
        }
        with engine.connect() as connection:
            assert AUDIO_TRIGGER_NAMES.issubset(_sqlite_trigger_names(connection))
        artifact_fks = inspect(engine).get_foreign_keys("podcast_artifacts")
        assert {
            tuple(item["constrained_columns"]) for item in artifact_fks
        } == {
            ("episode_id",),
            ("narration_artifact_id",),
            ("processing_id",),
            ("producing_attempt_id", "processing_id"),
        }
    finally:
        engine.dispose()


def test_migration_refuses_offline_sql_generation(tmp_path):
    cfg = make_alembic_config(f"sqlite:///{tmp_path / 'offline.db'}")
    cfg.output_buffer = StringIO()
    with pytest.raises(RuntimeError, match="requires an online database connection"):
        command.upgrade(cfg, f"{PARENT_REVISION}:{HEAD_REVISION}", sql=True)


def test_downgrade_refuses_any_source_audio_without_schema_drift(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'downgrade-refusal.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        _insert_episode(connection)
        _insert_artifact(connection, artifact_id="retained-source")
    engine.dispose()

    with pytest.raises(RuntimeError, match="仍[含有] source_audio"):
        command.downgrade(cfg, PARENT_REVISION)

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == CURRENT_HEAD_REVISION
            )
            assert connection.execute(text(
                "SELECT count(*) FROM podcast_artifacts WHERE id = 'retained-source'"
            )).scalar_one() == 1
        columns = {
            item["name"] for item in inspect(engine).get_columns("podcast_artifacts")
        }
        assert set(("source_locator_hash", "expires_at", "expired_at")).issubset(columns)
    finally:
        engine.dispose()


def test_empty_database_can_downgrade_to_parent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'empty-downgrade.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    head_engine = create_engine(db_url)
    stable_indexes = {
        item["name"]
        for item in inspect(head_engine).get_indexes("podcast_artifacts")
    } - NEW_ARTIFACT_INDEXES - {"ix_podcast_artifacts_producing_attempt_id"}
    stable_fks = {
        (tuple(item["constrained_columns"]), tuple(item["referred_columns"]))
        for item in inspect(head_engine).get_foreign_keys("podcast_artifacts")
        if item["constrained_columns"]
        != ["producing_attempt_id", "processing_id"]
    }
    head_engine.dispose()
    command.downgrade(cfg, PARENT_REVISION)

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == PARENT_REVISION
        columns = {
            item["name"] for item in inspect(engine).get_columns("podcast_artifacts")
        }
        assert not set(("source_locator_hash", "expires_at", "expired_at")) & columns
        assert {
            item["name"]
            for item in inspect(engine).get_indexes("podcast_artifacts")
        } == stable_indexes
        assert {
            (tuple(item["constrained_columns"]), tuple(item["referred_columns"]))
            for item in inspect(engine).get_foreign_keys("podcast_artifacts")
        } == stable_fks
        with engine.connect() as connection:
            assert AUDIO_TRIGGER_NAMES.issubset(_sqlite_trigger_names(connection))
    finally:
        engine.dispose()


def test_multiversion_downgrade_refusal_stays_at_head_for_command_only_data(
    tmp_path,
):
    db_url = f"sqlite:///{tmp_path / 'command-only-downgrade.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        _insert_episode(connection, episode_id="command-episode")
    with Session(engine) as session:
        session.add(
            PodcastProcessingRecord(
                id="command-processing",
                episode_id="command-episode",
                input_fingerprint="e" * 64,
                pipeline_version="pipeline-v1",
                policy_version="policy-v1",
                requested_target="transcript",
                selection_source="editor",
                request_reason="audited request",
                idempotency_key="command-processing-key",
                eligibility_status="blocked_source",
                processing_status="not_required",
                stage="fetch",
                queued_at=STAMP,
                updated_at=STAMP,
                created_at=STAMP,
            )
        )
        session.commit()
        session.add(
            PodcastProcessingCommandRecord(
                id="command-record",
                processing_id="command-processing",
                idempotency_key="command-retry-key",
                expected_attempt_count=0,
                requested_by="admin",
                reason="audited retry",
                outcome="rejected",
                error_code="not_retryable",
                created_at=STAMP,
            )
        )
        session.commit()
    engine.dispose()

    with pytest.raises(RuntimeError, match="processing 管理 schema"):
        command.downgrade(cfg, "8f3b2d1c7a90")

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == CURRENT_HEAD_REVISION
            )
            assert connection.execute(text(
                "SELECT count(*) FROM podcast_processing_commands"
            )).scalar_one() == 1
    finally:
        engine.dispose()
