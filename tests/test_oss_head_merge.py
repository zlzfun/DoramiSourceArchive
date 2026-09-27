"""Both deployed migration branches converge without skipping either schema change."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session

from models.db import SourceConfigRecord, SQLModel
from storage.fts import fts_include_object
from storage.migrations import ensure_migrated, make_alembic_config


OSS_REVISION = "c92a8f01d6b3"
RETIREMENT_REVISION = "c8a1e4f7d2b6"
COMMON_PARENT = "b715a91c4e02"
MERGE_REVISION = "d17e9a4c2b61"


@pytest.mark.parametrize("starting_revisions", [
    (OSS_REVISION,),
    (RETIREMENT_REVISION,),
    (OSS_REVISION, RETIREMENT_REVISION),
], ids=["oss-deployment", "main-deployment", "both-branches-applied"])
def test_existing_branches_upgrade_to_one_head_without_drift(tmp_path, starting_revisions):
    url = f"sqlite:///{tmp_path / 'existing-deployment.db'}"
    cfg = make_alembic_config(url)
    scripts = ScriptDirectory.from_config(cfg)
    # Reparenting an already deployed revision would skip the other branch's DDL.
    assert scripts.get_revision(OSS_REVISION).down_revision == COMMON_PARENT
    assert scripts.get_revision(RETIREMENT_REVISION).down_revision == COMMON_PARENT
    assert set(scripts.get_revision(MERGE_REVISION).down_revision) == {OSS_REVISION, RETIREMENT_REVISION}
    target_head = scripts.get_current_head()
    assert target_head not in {OSS_REVISION, RETIREMENT_REVISION}

    for revision in starting_revisions:
        command.upgrade(cfg, revision)

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            assert set(MigrationContext.configure(connection).get_current_heads()) == set(starting_revisions)
            connection.execute(text("INSERT INTO app_settings (key, value) VALUES ('merge-proof', 'retained')"))
            if OSS_REVISION in starting_revisions:
                connection.execute(text(
                    "INSERT INTO object_blobs "
                    "(id, namespace, content_hash, ext, size_bytes, mime, bucket, region, object_key, created_at) "
                    "VALUES ('media:existing', 'media', :hash, '.png', 3, 'image/png', "
                    "'existing-bucket', 'ap-southeast-1', 'prod/media/existing.png', '2026-09-16')"
                ), {"hash": "a" * 64})
            else:
                assert "object_blobs" not in inspect(connection).get_table_names()
            if RETIREMENT_REVISION not in starting_revisions:
                assert "retired_at" not in {row["name"] for row in inspect(connection).get_columns("source_configs")}
        if RETIREMENT_REVISION in starting_revisions:
            with Session(engine) as session:
                session.add(SourceConfigRecord(
                    source_id="retained-retirement", name="Retired source", retired_at="2026-09-16T01:00:00Z",
                    created_at="2026-09-15T01:00:00Z", updated_at="2026-09-16T01:00:00Z",
                ))
                session.commit()

        ensure_migrated(url)
        # Repeated deployment startup must remain idempotent.
        ensure_migrated(url)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={
                "compare_type": True, "render_as_batch": True, "include_object": fts_include_object,
            })
            assert context.get_current_heads() == (target_head,)
            assert compare_metadata(context, SQLModel.metadata) == []
            assert connection.execute(text("SELECT value FROM app_settings WHERE key='merge-proof'")).scalar_one() == "retained"
            assert "ix_object_blobs_namespace" in {row["name"] for row in inspect(connection).get_indexes("object_blobs")}
            assert "ix_source_configs_retired_at" in {row["name"] for row in inspect(connection).get_indexes("source_configs")}
            if OSS_REVISION in starting_revisions:
                assert connection.execute(text("SELECT object_key FROM object_blobs WHERE id='media:existing'")).scalar_one() == "prod/media/existing.png"
            if RETIREMENT_REVISION in starting_revisions:
                assert connection.execute(text("SELECT retired_at FROM source_configs WHERE source_id='retained-retirement'")).scalar_one() == "2026-09-16T01:00:00Z"
    finally:
        engine.dispose()
