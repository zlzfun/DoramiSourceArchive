"""Migration/create_all parity and constraints for Podcast processing state."""

from __future__ import annotations

import os
import sys
from io import StringIO

import pytest
from alembic import command
from sqlalchemy import DDL, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import make_alembic_config  # noqa: E402
from models.db import (  # noqa: E402
    _podcast_audio_dependency_postgresql_sql,
    _podcast_processing_postgresql_audit_sql,
    _podcast_processing_command_postgresql_sql,
)


EXPECTED_TABLES = {
    "podcast_processings",
    "podcast_stage_attempts",
    "podcast_budget_reservations",
    "podcast_cost_ledger",
}


def test_processing_migration_upgrade_downgrade_round_trip(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "7e4a1c9b2d63")
    engine = create_engine(db_url)
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, "1d7c9a4e2b60")
    engine = create_engine(db_url)
    assert EXPECTED_TABLES.issubset(inspect(engine).get_table_names())
    processing_fks = inspect(engine).get_foreign_keys("podcast_processings")
    assert next(
        fk for fk in processing_fks if fk["constrained_columns"] == ["episode_id"]
    )["options"].get("ondelete") == "RESTRICT"
    reservation_fks = inspect(engine).get_foreign_keys(
        "podcast_budget_reservations"
    )
    assert any(
        fk["constrained_columns"] == ["attempt_id", "processing_id"]
        and fk["referred_columns"] == ["id", "processing_id"]
        for fk in reservation_fks
    )
    ledger_fks = inspect(engine).get_foreign_keys("podcast_cost_ledger")
    assert any(
        fk["constrained_columns"]
        == ["reservation_id", "attempt_id", "processing_id"]
        and fk["referred_columns"] == ["id", "attempt_id", "processing_id"]
        for fk in ledger_fks
    )
    engine.dispose()

    command.downgrade(cfg, "7e4a1c9b2d63")
    engine = create_engine(db_url)
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()
    command.upgrade(cfg, "head")


def test_tts_artifact_binding_migration_round_trip(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tts-artifact-binding.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "8c4e1a7b9d20")
    engine = create_engine(db_url)
    assert "producing_attempt_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_artifacts")
    }
    assert "output_authority_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_stage_attempts")
    }
    engine.dispose()

    command.upgrade(cfg, "5e9a1c7d3b42")
    engine = create_engine(db_url)
    assert "producing_attempt_id" in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_artifacts")
    }
    assert "output_authority_id" in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_stage_attempts")
    }
    assert any(
        fk["constrained_columns"] == ["producing_attempt_id", "processing_id"]
        and fk["referred_columns"] == ["id", "processing_id"]
        for fk in inspect(engine).get_foreign_keys("podcast_artifacts")
    )
    engine.dispose()

    command.downgrade(cfg, "8c4e1a7b9d20")
    engine = create_engine(db_url)
    assert "producing_attempt_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_artifacts")
    }
    assert "output_authority_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("podcast_stage_attempts")
    }
    engine.dispose()
    command.upgrade(cfg, "head")


def test_processing_admin_migration_supersedes_unbound_historical_runs(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'historical-processing.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "8f3b2d1c7a90")
    engine = create_engine(db_url)
    stamp = "2026-09-05T08:00:00.000000+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO articles "
                "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
                "archive_updated_at,run_scope,has_content,extensions_json,"
                "analysis_authority_id,read_count) VALUES "
                "('historical-episode','Episode','podcast_episode','source','',"
                ":stamp,:stamp,'','ad_hoc',1,'{}','',0)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_processings "
                "(id,episode_id,input_fingerprint,pipeline_version,policy_version,"
                "requested_target,selection_source,request_reason,idempotency_key,"
                "eligibility_status,eligibility_reasons_json,processing_status,stage,"
                "attempt_count,fencing_token,asr_provider,asr_model,asr_revision,"
                "llm_provider,llm_model,llm_revision,tts_provider,tts_model,tts_revision,"
                "audio_minutes,input_tokens,output_tokens,tts_characters,tts_audio_tokens,"
                "cost_currency,estimated_cost_minor,actual_cost_minor,budget_breached,"
                "stage_cost_json,error_code,error_message,queued_at,updated_at,created_at) "
                "VALUES ('historical-run','historical-episode',:fingerprint,'v1','v1',"
                "'transcript','policy','','historical-key','eligible','[]','queued','asr',"
                "0,0,'','','','','','','','','',0,0,0,0,0,'CNY',1,0,0,'{}','','',"
                ":stamp,:stamp,:stamp)"
            ),
            {"fingerprint": "a" * 64, "stamp": stamp},
        )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT processing_status, eligibility_status, input_artifact_id, "
                    "budget_scope FROM podcast_processings WHERE id='historical-run'"
                )
            ).one()
            assert tuple(row) == ("superseded", "invalid_input", None, None)
        assert "podcast_processing_commands" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.parametrize("target_revision", ["4a6f9c2d8e31", "head"])
def test_processing_admin_migration_withdraws_audio_bound_to_historical_run(
    tmp_path, target_revision
):
    db_url = f"sqlite:///{tmp_path / f'historical-bound-audio-{target_revision}.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "8f3b2d1c7a90")
    engine = create_engine(db_url)
    stamp = "2026-09-05T08:00:00.000000+00:00"
    narration_hash = "c" * 64
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO articles "
                "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
                "archive_updated_at,run_scope,has_content,extensions_json,"
                "analysis_authority_id,read_count) VALUES "
                "('historical-audio-episode','Episode','podcast_episode','source','',"
                ":stamp,:stamp,'','ad_hoc',1,'{}','',0)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_text_artifacts "
                "(id,episode_id,kind,version,content_hash,inline_text,language,"
                "authority_id,source_artifact_id,source_content_hash,rights_version,"
                "provenance_json,created_at) VALUES "
                "('historical-script','historical-audio-episode',"
                "'narration_script_zh',1,:narration_hash,'script','zh-CN','',"
                "'historical-source',:source_hash,'rights-v1','{}',:stamp)"
            ),
            {
                "narration_hash": narration_hash,
                "source_hash": "d" * 64,
                "stamp": stamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO podcast_text_publications "
                "(identity,episode_id,kind,artifact_id,status,authority_id,"
                "published_at,updated_at) VALUES "
                "('historical-audio-episode:narration_script_zh',"
                "'historical-audio-episode','narration_script_zh',"
                "'historical-script','published','',:stamp,:stamp)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_processings "
                "(id,episode_id,input_fingerprint,pipeline_version,policy_version,"
                "requested_target,selection_source,request_reason,idempotency_key,"
                "eligibility_status,eligibility_reasons_json,processing_status,stage,"
                "attempt_count,fencing_token,asr_provider,asr_model,asr_revision,"
                "llm_provider,llm_model,llm_revision,tts_provider,tts_model,tts_revision,"
                "audio_minutes,input_tokens,output_tokens,tts_characters,tts_audio_tokens,"
                "cost_currency,estimated_cost_minor,actual_cost_minor,budget_breached,"
                "stage_cost_json,error_code,error_message,queued_at,updated_at,created_at) "
                "VALUES ('historical-audio-run','historical-audio-episode',:fingerprint,"
                "'v1','v1','digest_audio','policy','','historical-audio-key','eligible',"
                "'[]','ready','local_publish',0,0,'','','','','','','','','',0,0,0,0,0,"
                "'CNY',1,1,0,'{}','','',:stamp,:stamp,:stamp)"
            ),
            {"fingerprint": "e" * 64, "stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_artifacts "
                "(id,episode_id,kind,content_hash,mime,ext,size_bytes,status,provenance,"
                "authority_id,narration_artifact_id,narration_content_hash,processing_id,"
                "created_at,updated_at,published_at) VALUES "
                "('historical-bound-audio','historical-audio-episode',"
                "'digest_audio_zh',:content_hash,'audio/mpeg','.mp3',1,'published','tts',"
                "'','historical-script',:narration_hash,'historical-audio-run',"
                ":stamp,:stamp,:stamp)"
            ),
            {
                "content_hash": "f" * 64,
                "narration_hash": narration_hash,
                "stamp": stamp,
            },
        )
    engine.dispose()

    command.upgrade(cfg, target_revision)
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            artifact = connection.execute(
                text(
                    "SELECT status, processing_id, narration_artifact_id, "
                    "narration_content_hash, withdrawn_at, provenance "
                    "FROM podcast_artifacts "
                    "WHERE id='historical-bound-audio'"
                )
            ).one()
            processing = connection.execute(
                text(
                    "SELECT processing_status, narration_artifact_id "
                    "FROM podcast_processings WHERE id='historical-audio-run'"
                )
            ).one()
        assert tuple(artifact[:4]) == (
            "withdrawn",
            "historical-audio-run",
            "historical-script",
            narration_hash,
        )
        assert artifact.withdrawn_at is not None
        assert artifact.provenance == (
            "legacy_tts_unbound" if target_revision == "head" else "tts"
        )
        assert tuple(processing) == ("superseded", None)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE podcast_artifacts SET status='ready' "
                    "WHERE id='historical-bound-audio'"
                )
            )
    finally:
        engine.dispose()


def test_processing_admin_empty_downgrade_restores_parent_audio_trigger(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'processing-admin-downgrade.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "8f3b2d1c7a90")
    engine = create_engine(db_url)
    try:
        processing_columns = {
            column["name"]
            for column in inspect(engine).get_columns("podcast_processings")
        }
        assert "narration_artifact_id" not in processing_columns
        with engine.connect() as connection:
            trigger_sql = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='podcast_audio_processing_insert'"
                )
            ).scalar_one()
        assert "processing.narration_artifact_id" not in trigger_sql
        assert "processing.requested_target = 'digest_audio'" in trigger_sql
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")


def test_processing_admin_migration_refuses_offline_sql_generation(tmp_path):
    cfg = make_alembic_config(f"sqlite:///{tmp_path / 'offline.db'}")
    cfg.output_buffer = StringIO()
    with pytest.raises(RuntimeError, match="requires an online database connection"):
        command.upgrade(cfg, "8f3b2d1c7a90:4a6f9c2d8e31", sql=True)


@pytest.mark.parametrize(
    ("submission_state", "request_unknown", "retry_state", "with_reservation"),
    [
        ("submitted", 0, "none", False),
        ("request_unknown", 1, "reconcile_required", False),
        ("failed_terminal", 0, "exhausted", True),
    ],
)
def test_processing_admin_migration_refuses_provider_facing_work_without_drift(
    tmp_path,
    submission_state,
    request_unknown,
    retry_state,
    with_reservation,
):
    db_url = f"sqlite:///{tmp_path / f'unsafe-{submission_state}.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "8f3b2d1c7a90")
    engine = create_engine(db_url)
    stamp = "2026-09-05T08:00:00.000000+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO articles "
                "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
                "archive_updated_at,run_scope,has_content,extensions_json,"
                "analysis_authority_id,read_count) VALUES "
                "('unsafe-episode','Episode','podcast_episode','source','',"
                ":stamp,:stamp,'','ad_hoc',1,'{}','',0)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_processings "
                "(id,episode_id,input_fingerprint,pipeline_version,policy_version,"
                "requested_target,selection_source,request_reason,idempotency_key,"
                "eligibility_status,eligibility_reasons_json,processing_status,stage,"
                "attempt_count,fencing_token,asr_provider,asr_model,asr_revision,"
                "llm_provider,llm_model,llm_revision,tts_provider,tts_model,tts_revision,"
                "audio_minutes,input_tokens,output_tokens,tts_characters,tts_audio_tokens,"
                "cost_currency,estimated_cost_minor,actual_cost_minor,budget_breached,"
                "stage_cost_json,error_code,error_message,queued_at,updated_at,created_at) "
                "VALUES ('unsafe-run','unsafe-episode',:fingerprint,'v1','v1',"
                "'transcript','policy','','unsafe-key','eligible','[]','queued','asr',"
                "1,1,'','','','','','','','','',0,0,0,0,0,'CNY',10,0,0,'{}','','',"
                ":stamp,:stamp,:stamp)"
            ),
            {"fingerprint": "b" * 64, "stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO podcast_stage_attempts "
                "(id,processing_id,stage,attempt_no,fencing_token,lease_token,input_hash,"
                "output_hash,provider_name,model_name,provider_revision,provider_request_key,"
                "provider_task_id,submission_state,request_unknown,retry_state,usage_json,"
                "cost_currency,estimated_cost_minor,actual_cost_minor,error_code,error_message,"
                "started_at,created_at,updated_at) VALUES "
                "('unsafe-attempt','unsafe-run','asr',1,1,'lease','input','','provider',"
                "'model','revision','provider-key','provider-task',:state,:unknown,:retry,"
                "'{}','CNY',10,0,'','',:stamp,:stamp,:stamp)"
            ),
            {
                "state": submission_state,
                "unknown": request_unknown,
                "retry": retry_state,
                "stamp": stamp,
            },
        )
        if with_reservation:
            connection.execute(
                text(
                    "INSERT INTO podcast_budget_reservations "
                    "(id,processing_id,attempt_id,budget_scope,budget_period,currency,"
                    "reserved_minor,actual_cost_minor,budget_breached,status,idempotency_key,"
                    "created_at,updated_at) VALUES "
                    "('unsafe-reservation','unsafe-run','unsafe-attempt','scope','2026-09',"
                    "'CNY',10,0,0,'reserved','reservation-key',:stamp,:stamp)"
                ),
                {"stamp": stamp},
            )
    engine.dispose()

    with pytest.raises(RuntimeError, match="reconciled and budget reservations"):
        command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        assert "input_artifact_id" not in {
            column["name"]
            for column in inspect(engine).get_columns("podcast_processings")
        }
        assert "podcast_processing_commands" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT submission_state FROM podcast_stage_attempts "
                    "WHERE id='unsafe-attempt'"
                )
            ).scalar_one()
            assert state == submission_state
    finally:
        engine.dispose()


def test_postgresql_audit_trigger_ddl_compiles():
    compiled = [
        str(DDL(statement).compile(dialect=postgresql.dialect()))
        for statement in _podcast_processing_postgresql_audit_sql()
    ]
    sql = "\n".join(compiled)
    assert "podcast_cost_ledger_binding_insert" in sql
    assert "podcast_cost_ledger_immutable_delete" in sql
    assert "podcast_budget_reservation_immutable_delete" in sql
    assert "podcast_budget_reservation_transition" in sql
    assert "podcast_stage_attempt_identity_immutable" in sql
    assert "IS DISTINCT FROM" in sql
    assert sql.count("END; $$") == 5

    current_sql = "\n".join(
        str(DDL(statement).compile(dialect=postgresql.dialect()))
        for statement in _podcast_processing_postgresql_audit_sql(
            include_execution_kind=True,
            include_poll_state=True,
            include_output_binding=True,
            include_provider_usage=True,
            include_output_authority=True,
        )
    )
    assert "NEW.settings_fingerprint IS DISTINCT FROM OLD.settings_fingerprint" in current_sql
    assert "OLD.output_hash" in current_sql
    assert "NEW.output_artifact_id IS DISTINCT FROM OLD.output_artifact_id" in current_sql
    assert "NEW.output_authority_id IS DISTINCT FROM OLD.output_authority_id" in current_sql
    assert "NEW.provider_quota_scope IS NOT DISTINCT FROM r.provider_quota_scope" in current_sql
    assert "NEW.actual_usage_units = r.actual_usage_units" in current_sql


def test_postgresql_audio_dependency_ddl_uses_one_episode_advisory_lock():
    compiled = [
        str(DDL(statement).compile(dialect=postgresql.dialect()))
        for statement in _podcast_audio_dependency_postgresql_sql(
            include_attempt_binding=True
        )
    ]
    sql = "\n".join(compiled)
    assert sql.count("pg_advisory_xact_lock(") == 2
    assert sql.count("hashtextextended('dorami:podcast-audio:' ||") == 2
    assert "NEW.processing_id IS DISTINCT FROM OLD.processing_id" in sql
    assert "processing.requested_target = 'digest_audio'" in sql
    assert "processing.narration_artifact_id = NEW.narration_artifact_id" in sql
    assert "processing.narration_content_hash = NEW.narration_content_hash" in sql
    assert "attempt.output_artifact_id = NEW.id" in sql
    assert "attempt.output_authority_id = NEW.authority_id" in sql
    assert "reservation.status = 'settled'" in sql
    assert sql.count("END; $$") == 2
    parent_sql = "\n".join(
        str(DDL(statement).compile(dialect=postgresql.dialect()))
        for statement in _podcast_audio_dependency_postgresql_sql(
            require_processing_narration=False
        )
    )
    assert "processing.requested_target = 'digest_audio'" in parent_sql
    assert "processing.narration_artifact_id" not in parent_sql


def test_postgresql_processing_admin_audit_ddl_compiles():
    compiled = [
        str(DDL(statement).compile(dialect=postgresql.dialect()))
        for statement in _podcast_processing_command_postgresql_sql()
    ]
    sql = "\n".join(compiled)
    assert "podcast_processing_input_immutable" in sql
    assert "podcast_processing_command_immutable_update" in sql
    assert "podcast_processing_command_immutable_delete" in sql
    assert "budget_limit_minor IS DISTINCT FROM OLD.budget_limit_minor" in sql
    assert sql.count("END; $$") == 2

@pytest.mark.parametrize("schema_origin", ["create_all", "alembic"])
def test_processing_schema_enforces_cny_integer_state(tmp_path, schema_origin):
    db_url = f"sqlite:///{tmp_path / f'{schema_origin}.db'}"
    if schema_origin == "create_all":
        engine = DatabaseStorage(db_url).engine
    else:
        command.upgrade(make_alembic_config(db_url), "head")
        engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)
    constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints("podcast_processings")
    }
    assert {
        "ck_podcast_processings_currency",
        "ck_podcast_processings_running_lease",
        "ck_podcast_processings_editor_reason",
        "ck_podcast_processings_requested_target",
    }.issubset(constraints)
    attempt_constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints("podcast_stage_attempts")
    }
    assert {
        "ck_podcast_stage_attempts_settings_fingerprint",
        "ck_podcast_stage_attempts_output_binding",
        "ck_podcast_stage_attempts_output_authority",
    }.issubset(attempt_constraints)
    text_constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints("podcast_text_artifacts")
    }
    assert {
        "ck_podcast_text_artifacts_processing_attempt_pair",
        "ck_podcast_text_artifacts_normalized_bound",
    }.issubset(text_constraints)
    artifact_constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints("podcast_artifacts")
    }
    assert "ck_podcast_artifacts_attempt_bound_digest" in artifact_constraints
    assert any(
        item["constrained_columns"] == ["producing_attempt_id", "processing_id"]
        and item["referred_columns"] == ["id", "processing_id"]
        for item in inspect(engine).get_foreign_keys("podcast_text_artifacts")
    )
    assert any(
        item["constrained_columns"] == ["producing_attempt_id", "processing_id"]
        and item["referred_columns"] == ["id", "processing_id"]
        for item in inspect(engine).get_foreign_keys("podcast_artifacts")
    )
    text_uniques = {
        item["name"]
        for item in inspect(engine).get_unique_constraints("podcast_text_artifacts")
    }
    assert {
        "uq_podcast_text_artifacts_producing_attempt",
        "uq_podcast_text_artifacts_processing_kind",
    }.issubset(text_uniques)

    stamp = "2026-09-05T08:00:00.000000+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO articles "
                "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
                "archive_updated_at,run_scope,has_content,extensions_json,"
                "analysis_authority_id,read_count) VALUES "
                "('episode','Episode','podcast_episode','source','https://example.test/e',"
                ":stamp,:stamp,'','ad_hoc',1,'{}','',0)"
            ),
            {"stamp": stamp},
        )
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO podcast_processings "
                "(id,episode_id,input_fingerprint,pipeline_version,policy_version,"
                "requested_target,selection_source,request_reason,idempotency_key,"
                "eligibility_status,eligibility_reasons_json,processing_status,stage,"
                "attempt_count,fencing_token,asr_provider,asr_model,asr_revision,llm_provider,"
                "llm_model,llm_revision,tts_provider,tts_model,tts_revision,audio_minutes,"
                "input_tokens,output_tokens,tts_characters,tts_audio_tokens,cost_currency,"
                "estimated_cost_minor,actual_cost_minor,stage_cost_json,error_code,error_message,"
                "queued_at,updated_at,created_at) VALUES "
                "('bad','episode',:fingerprint,'v1','','premium_ready','editor','reason',"
                "'bad-key','eligible','[]','queued','asr',0,0,'','','','','','','','','',"
                "0,0,0,0,0,'USD',0,0,'{}','','',:stamp,:stamp,:stamp)"
            ),
            {"fingerprint": "a" * 64, "stamp": stamp},
        )
    engine.dispose()
